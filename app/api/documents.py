import hashlib
import logging
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import repositories
from app.db.session import get_engine
from app.schemas.documents import (
    ChunksResponse,
    DeleteResponse,
    DocumentRecord,
    UploadDocumentResponse,
)
from app.services.chunking import chunk_pages
from app.services.embeddings import Embedder
from app.services.errors import (
    DatabaseNotConfiguredError,
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    EmptyExtractionError,
    FileTooLargeError,
    StoredFileMissingError,
    UnsupportedFileTypeError,
    VectorStoreError,
)
from app.services.openai_client import get_openai_client
from app.services.storage import DocumentStorage
from app.services.text_extraction import extract_pages, validate_upload
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_READ_CHUNK_BYTES = 1024 * 1024


def get_storage(settings: Settings = Depends(get_settings)) -> DocumentStorage:
    return DocumentStorage(settings.uploads_path)


def get_embedder(settings: Settings = Depends(get_settings)) -> Embedder:
    """Build the embedder eagerly so a missing API key surfaces as 503, not 500."""
    try:
        return Embedder(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            max_retries=settings.embedding_max_retries,
            client=(
                get_openai_client(settings.openai_api_key, settings.embedding_timeout_seconds)
                if settings.openai_api_key
                else None
            ),
        )
    except EmbeddingNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _session_dep() -> Iterator[Session]:
    """Yield a session, mapping config/driver failures to HTTP 503.

    Connection is attempted eagerly so an unreachable database produces a
    clean 503 instead of a 500 mid-request. Driver details are logged,
    never returned to the client.
    """
    try:
        session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
        session.connection()
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Database connection failed")
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable. Check DATABASE_URL and the server.",
        ) from exc
    try:
        yield session
    finally:
        session.close()


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


@router.post("", response_model=UploadDocumentResponse, status_code=201)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    storage: DocumentStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    session: Session = Depends(_session_dep),
) -> UploadDocumentResponse:
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

    # Idempotent ingestion: identical bytes already indexed -> return the
    # existing document instead of creating a duplicate record and vectors.
    content_hash = hashlib.sha256(data).hexdigest()
    existing = repositories.find_by_content_hash(session, content_hash)
    if existing is not None:
        logger.info(
            "Duplicate upload of %r (hash=%s...) -> existing document %s",
            filename,
            content_hash[:12],
            existing.id,
        )
        response.status_code = 200
        return UploadDocumentResponse(**existing.model_dump(), chunks=[])

    document_id = storage.save(data, extension)
    try:
        repositories.create_document(
            session,
            document_id=document_id,
            original_filename=filename,
            file_type=extension.lstrip("."),
            size_bytes=len(data),
            content_hash=content_hash,
        )
    except SQLAlchemyError:
        storage.delete(document_id, extension)
        logger.exception("Failed to record document %s", document_id)
        raise HTTPException(
            status_code=503, detail="Could not record the document in the database."
        ) from None

    try:
        pages = extract_pages(filename, data)
        chunks = chunk_pages(pages, document_id=document_id, filename=filename)
    except (EmptyExtractionError, UnsupportedFileTypeError) as exc:
        repositories.fail_document(session, document_id, str(exc))
        logger.info("Ingestion failed for %s: %s", document_id, exc)
        raise HTTPException(
            status_code=422,
            detail={"document_id": document_id, "message": str(exc)},
        ) from exc
    except Exception as exc:  # unexpected extraction/chunking failure
        repositories.fail_document(session, document_id, f"Unexpected error: {exc}")
        logger.exception("Unexpected ingestion failure for %s", document_id)
        raise HTTPException(
            status_code=500, detail="Document ingestion failed unexpectedly."
        ) from exc

    # Indexing: embed chunks and store vectors. The document is only marked
    # completed after the vectors are safely in the store.
    try:
        vectors = embedder.embed([chunk.text for chunk in chunks])
        store.upsert_chunks(chunks, vectors)
    except (EmbeddingNotConfiguredError, EmbeddingServiceError, VectorStoreError) as exc:
        repositories.fail_document(session, document_id, str(exc))
        logger.warning("Indexing failed for document %s: %s", document_id, exc)
        raise HTTPException(
            status_code=503,
            detail={"document_id": document_id, "message": str(exc)},
        ) from exc
    except Exception as exc:  # unexpected embedding/indexing failure
        repositories.fail_document(session, document_id, f"Unexpected error: {exc}")
        logger.exception("Unexpected indexing failure for %s", document_id)
        raise HTTPException(
            status_code=500, detail="Document indexing failed unexpectedly."
        ) from exc

    repositories.complete_document(
        session,
        document_id,
        len(chunks),
        embedding_model=embedder.model,
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
    )
    record = repositories.get_document(session, document_id)

    logger.info(
        "Ingested and indexed document %s (%s, %d bytes, %d chunks)",
        document_id,
        filename,
        len(data),
        len(chunks),
    )
    return UploadDocumentResponse(**record.model_dump(), chunks=chunks)


@router.get("", response_model=list[DocumentRecord])
def list_documents(session: Session = Depends(_session_dep)) -> list[DocumentRecord]:
    return repositories.list_documents(session)


@router.get("/{document_id}", response_model=DocumentRecord)
def get_document(
    document_id: str, session: Session = Depends(_session_dep)
) -> DocumentRecord:
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(session, document_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return record


@router.get("/{document_id}/chunks", response_model=ChunksResponse)
def get_document_chunks(
    document_id: str,
    storage: DocumentStorage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(_session_dep),
) -> ChunksResponse:
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(session, document_id)
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


@router.delete("/{document_id}", response_model=DeleteResponse)
def delete_document(
    document_id: str,
    storage: DocumentStorage = Depends(get_storage),
    store: VectorStore = Depends(get_vector_store),
    session: Session = Depends(_session_dep),
) -> DeleteResponse:
    document_id = _parse_document_id(document_id)
    record = repositories.get_document(session, document_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Vectors first: if this fails nothing is removed, so the record stays
    # consistent and the deletion can simply be retried.
    try:
        store.delete_document_vectors(document_id)
    except VectorStoreError as exc:
        logger.exception("Vector cleanup failed for %s", document_id)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not remove the indexed vectors for this document. "
                "Nothing was deleted; try again."
            ),
        ) from exc

    repositories.delete_document(session, document_id)

    try:
        storage.delete(document_id, f".{record.file_type}")
    except OSError:
        logger.warning("DB row deleted but stored file cleanup failed for %s", document_id)

    logger.info("Deleted document %s", document_id)
    return DeleteResponse(deleted=True, document_id=document_id)
