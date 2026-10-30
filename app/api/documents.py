import hashlib
import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthActor,
    document_owner_filter,
    document_owner_id,
    get_actor,
)
from app.api.deps import get_db_session
from app.config import Settings, get_settings
from app.db import repositories
from app.schemas.documents import (
    ChunksResponse,
    DeleteResponse,
    DocumentContentResponse,
    DocumentRecord,
    UploadDocumentResponse,
)
from app.services.answer_cache import invalidate_answer_cache
from app.services.bm25_index import Bm25Error, Bm25Index, get_bm25_index
from app.services.chunking import chunk_pages
from app.services.embeddings import Embedder
from app.services.errors import (
    EmbeddingNotConfiguredError,
    FileTooLargeError,
    StoredFileMissingError,
    UnsupportedFileTypeError,
    VectorStoreError,
)
from app.services.ingestion import build_embedder, ingest_document
from app.services.storage import DocumentStorage
from app.services.text_extraction import extract_pages, validate_upload
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_READ_CHUNK_BYTES = 1024 * 1024

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


def get_storage(settings: Settings = Depends(get_settings)) -> DocumentStorage:
    return DocumentStorage(settings.uploads_path)


def get_embedder(settings: Settings = Depends(get_settings)) -> Embedder:
    """Build the embedder eagerly so a missing API key surfaces as 503, not 500."""
    try:
        return build_embedder(settings)
    except EmbeddingNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _parse_document_id(document_id: str) -> str:
    try:
        return str(uuid.UUID(document_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Document not found.") from exc


async def _read_bounded(file: UploadFile, max_size: int) -> bytes:
    """Read the upload with a hard upper bound to avoid unbounded memory use."""
    data = bytearray()
    while True:
        block = await file.read(_READ_CHUNK_BYTES)
        if not block:
            break
        data.extend(block)
        if len(data) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {max_size}-byte size limit.",
            )
    return bytes(data)


@router.post("", response_model=UploadDocumentResponse, status_code=202)
async def upload_document(
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    storage: DocumentStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> UploadDocumentResponse:
    """Accept a file and queue background ingestion.

    Validation (type/size) is synchronous; extraction, embedding, and vector
    indexing run after the response so a large upload never blocks the app.
    The returned record is `pending`; poll GET /documents for progress.

    Uploads are private to the account that made them (owner stamped at
    creation); API-key/local callers land in the shared corpus. Dedup is
    owner-scoped, so identical bytes uploaded by two users produce one
    document row each - never a reference to the other user's document.
    """
    data = await _read_bounded(file, settings.max_file_size_bytes)
    filename = file.filename or ""

    try:
        extension = validate_upload(
            filename=filename,
            content_type=file.content_type,
            data=data,
            max_size=settings.max_file_size_bytes,
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Idempotent ingestion: identical bytes already completed -> duplicate;
    # identical bytes still cooking -> "already being indexed". Either way
    # no second record or job is created. Both lookups are owner-scoped:
    # bytes that only ANOTHER user ingested are a fresh upload for us.
    content_hash = hashlib.sha256(data).hexdigest()
    owner = document_owner_filter(actor)
    existing = repositories.find_by_content_hash(session, content_hash, owner=owner)
    if existing is not None:
        logger.info(
            "Duplicate upload of %r (hash=%s...) -> existing document %s (%s)",
            filename,
            content_hash[:12],
            existing.id,
            existing.status,
        )
        response.status_code = 200 if existing.status == "completed" else 202
        return UploadDocumentResponse(**existing.model_dump(), chunks=[])

    # Same bytes as a previously failed document -> retry in place.
    failed = repositories.find_failed_by_content_hash(session, content_hash, owner=owner)
    if failed is not None:
        repositories.reopen_document(
            session,
            failed.id,
            original_filename=filename,
            size_bytes=len(data),
        )
        logger.info("Retrying failed document %s for %r", failed.id, filename)
        background_tasks.add_task(
            ingest_document,
            failed.id,
            embedder=embedder,
            store=store,
            storage=storage,
        )
        record = repositories.get_document(session, failed.id, owner=owner)
        return UploadDocumentResponse(**record.model_dump(), chunks=[])

    document_id = storage.save(data, extension)
    try:
        repositories.create_document(
            session,
            document_id=document_id,
            original_filename=filename,
            file_type=extension.lstrip("."),
            size_bytes=len(data),
            content_hash=content_hash,
            owner_user_id=document_owner_id(actor),
        )
    except SQLAlchemyError:
        storage.delete(document_id, extension)
        logger.exception("Failed to record document %s", document_id)
        raise HTTPException(
            status_code=503, detail="Could not record the document in the database."
        ) from None

    background_tasks.add_task(
        ingest_document,
        document_id,
        embedder=embedder,
        store=store,
        storage=storage,
    )
    record = repositories.get_document(session, document_id)
    logger.info(
        "Queued document %s (%r, %d bytes) for background ingestion",
        document_id,
        filename,
        len(data),
    )
    return UploadDocumentResponse(**record.model_dump(), chunks=[])


@router.get("", response_model=list[DocumentRecord])
def list_documents(
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> list[DocumentRecord]:
    """List visible documents: shared corpus + the caller's own uploads."""
    return repositories.list_documents(
        session, owner=document_owner_filter(actor)
    )


@router.get("/{document_id}", response_model=DocumentRecord)
def get_document(
    document_id: str,
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> DocumentRecord:
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(
        session, document_id, owner=document_owner_filter(actor)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return record


@router.get("/{document_id}/chunks", response_model=ChunksResponse)
def get_document_chunks(
    document_id: str,
    storage: DocumentStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> ChunksResponse:
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(
        session, document_id, owner=document_owner_filter(actor)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        data = storage.load(document_id, f".{record.file_type}")
    except StoredFileMissingError as exc:
        raise HTTPException(
            status_code=410,
            detail=(
                f"Stored file is missing for document {document_id}. "
                "Re-upload the document or delete this record."
            ),
        ) from exc

    try:
        pages = extract_pages(record.original_filename, data)
        chunks = chunk_pages(pages, document_id=document_id, filename=record.original_filename)
    except Exception as exc:
        logger.exception("Re-chunking failed for %s", document_id)
        raise HTTPException(status_code=422, detail=f"Could not re-chunk document: {exc}") from exc

    return ChunksResponse(
        document_id=document_id,
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        chunk_count=len(chunks),
        chunks=chunks,
    )


@router.get("/{document_id}/content", response_model=DocumentContentResponse)
def get_document_content(
    document_id: str,
    storage: DocumentStorage = Depends(get_storage),
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> DocumentContentResponse:
    """Return the document's full extracted text (no chunking)."""
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(
        session, document_id, owner=document_owner_filter(actor)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        data = storage.load(document_id, f".{record.file_type}")
    except StoredFileMissingError as exc:
        raise HTTPException(
            status_code=410,
            detail=(
                f"Stored file is missing for document {document_id}. "
                "Re-upload the document or delete this record."
            ),
        ) from exc

    try:
        pages = extract_pages(record.original_filename, data)
    except Exception as exc:
        logger.exception("Reading document text failed for %s", document_id)
        raise HTTPException(status_code=422, detail=f"Could not read document: {exc}") from exc

    if len(pages) == 1:
        text = pages[0].text
    else:
        text = "\n\n".join(
            f"--- Page {page.page_number} ---\n{page.text}" for page in pages
        )

    return DocumentContentResponse(
        document_id=document_id,
        filename=record.original_filename,
        file_type=record.file_type,
        page_count=len(pages),
        text=text,
    )


@router.get("/{document_id}/file")
def get_document_file(
    document_id: str,
    storage: DocumentStorage = Depends(get_storage),
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> Response:
    """Serve the stored file's raw bytes (e.g. for an in-browser PDF view)."""
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(
        session, document_id, owner=document_owner_filter(actor)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        data = storage.load(document_id, f".{record.file_type}")
    except StoredFileMissingError as exc:
        raise HTTPException(
            status_code=410,
            detail=(
                f"Stored file is missing for document {document_id}. "
                "Re-upload the document or delete this record."
            ),
        ) from exc

    media_type = _MEDIA_TYPES.get(f".{record.file_type}", "application/octet-stream")
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{record.original_filename}"'
        },
    )


@router.delete("/{document_id}", response_model=DeleteResponse)
def delete_document(
    document_id: str,
    storage: DocumentStorage = Depends(get_storage),
    store: VectorStore = Depends(get_vector_store),
    bm25: Bm25Index = Depends(get_bm25_index),
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> DeleteResponse:
    document_id = _parse_document_id(document_id)
    owner = document_owner_filter(actor)
    record = repositories.get_document(session, document_id, owner=owner)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Search index first: deleting rows that may not exist is a no-op, so a
    # retry after any later failure stays safe.
    try:
        bm25.delete_document(document_id)
    except Bm25Error as exc:
        logger.exception("Search-index cleanup failed for %s", document_id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not remove the document from the term index. "
                "Nothing was deleted; try again."
            ),
        ) from exc

    # Then vectors: if this fails the record and file survive, so the
    # deletion can simply be retried (the term-index rows are already gone
    # and re-deleting them is harmless).
    try:
        store.delete_document_vectors(document_id)
    except VectorStoreError as exc:
        logger.exception("Vector cleanup failed for %s", document_id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not remove the indexed vectors for this document. "
                "The document record and file are still present; try again."
            ),
        ) from exc

    repositories.delete_document(session, document_id, owner=owner)
    # Corpus changed: wipe the semantic answer cache (hygiene; the
    # fingerprint check is what actually invalidates stale entries).
    invalidate_answer_cache()

    try:
        storage.delete(document_id, f".{record.file_type}")
    except OSError:
        logger.warning("DB row deleted but stored file cleanup failed for %s", document_id)

    logger.info("Deleted document %s", document_id)
    return DeleteResponse(deleted=True, document_id=document_id)
