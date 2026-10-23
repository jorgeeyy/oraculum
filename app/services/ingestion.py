"""Background ingestion: extract -> chunk -> embed -> index, off the request.

Uploads only create the row and return; the heavy pipeline runs afterwards as
a FastAPI BackgroundTask (or from the startup recovery thread). A process-wide
semaphore serializes ingestion so concurrent uploads never stack OpenAI
traffic or in-memory chunk buffers — batch uploads will queue for free.
"""

import logging
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import repositories
from app.db.models import Document
from app.db.session import get_engine
from app.services.chunking import chunk_pages
from app.services.embeddings import Embedder
from app.services.errors import (
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    EmptyExtractionError,
    StoredFileMissingError,
    UnsupportedFileTypeError,
    VectorStoreError,
)
from app.services.openai_client import get_openai_client
from app.services.storage import DocumentStorage
from app.services.text_extraction import extract_pages
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

# One document at a time: embedding a second file while the first is still in
# flight doubles OpenAI traffic and memory without finishing anything sooner.
_INGEST_LOCK = threading.Lock()

_ACTIVE_STATUSES = ("pending", "processing")


def build_embedder(settings: Settings) -> Embedder:
    """Construct the embedder outside FastAPI DI (used by the request
    dependency and by startup recovery alike)."""
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


def ingest_document(
    document_id: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    storage: DocumentStorage,
) -> None:
    """Run the full pipeline for one document. Never raises.

    Every failure is stamped on the document row (`failed` + error_message)
    so the UI can surface it; a document deleted mid-flight is skipped and
    its vectors cleaned up.
    """
    try:
        with _INGEST_LOCK:
            _ingest(document_id, embedder=embedder, store=store, storage=storage)
    except Exception:
        # Session could not even be opened (DB down): the row stays pending
        # and startup recovery retries it on the next boot.
        logger.exception("Ingestion aborted for document %s", document_id)


def _open_session() -> Session:
    # Eager connection check so a dead database surfaces here, not mid-write.
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    session.connection()
    return session


def _document_exists(session: Session, document_id: str) -> bool:
    # Query instead of session.get(): the session's identity map holds a stale
    # copy of the row even after another connection deleted it.
    return (
        session.execute(
            select(Document.id).where(Document.id == uuid.UUID(document_id))
        ).first()
        is not None
    )


def _cleanup_orphan_vectors(document_id: str, store: VectorStore) -> None:
    try:
        store.delete_document_vectors(document_id)
    except VectorStoreError:
        logger.warning("Vector cleanup after mid-flight delete failed for %s", document_id)


def _ingest(
    document_id: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    storage: DocumentStorage,
) -> None:
    session = _open_session()
    try:
        record = repositories.get_document(session, document_id)
        if record is None:
            logger.warning("Ingestion skipped: document %s no longer exists", document_id)
            return
        if record.status not in _ACTIVE_STATUSES:
            logger.info(
                "Ingestion skipped: document %s is already %s", document_id, record.status
            )
            return

        repositories.mark_processing(session, document_id)
        filename = record.original_filename

        try:
            data = storage.load(document_id, f".{record.file_type}")
        except StoredFileMissingError:
            repositories.fail_document(
                session,
                document_id,
                "Stored file is missing; re-upload the document.",
            )
            logger.warning("Stored file missing for document %s", document_id)
            return

        try:
            pages = extract_pages(filename, data)
            chunks = chunk_pages(pages, document_id=document_id, filename=filename)
        except (EmptyExtractionError, UnsupportedFileTypeError) as exc:
            repositories.fail_document(session, document_id, str(exc))
            logger.info("Ingestion failed for %s: %s", document_id, exc)
            return
        except Exception as exc:
            repositories.fail_document(session, document_id, f"Unexpected error: {exc}")
            logger.exception("Unexpected extraction failure for %s", document_id)
            return

        try:
            vectors = embedder.embed([chunk.text for chunk in chunks])
            # The document may have been deleted while embeddings were in
            # flight; don't resurrect it with fresh vectors.
            if not _document_exists(session, document_id):
                logger.info("Ingestion aborted for %s: deleted mid-flight", document_id)
                _cleanup_orphan_vectors(document_id, store)
                return
            store.upsert_chunks(chunks, vectors)
        except (EmbeddingNotConfiguredError, EmbeddingServiceError, VectorStoreError) as exc:
            repositories.fail_document(session, document_id, str(exc))
            logger.warning("Indexing failed for document %s: %s", document_id, exc)
            return
        except Exception as exc:
            repositories.fail_document(session, document_id, f"Unexpected error: {exc}")
            logger.exception("Unexpected indexing failure for %s", document_id)
            return

        if not _document_exists(session, document_id):
            logger.info("Ingestion aborted for %s: deleted during upsert", document_id)
            _cleanup_orphan_vectors(document_id, store)
            return

        settings = get_settings()
        repositories.complete_document(
            session,
            document_id,
            len(chunks),
            embedding_model=embedder.model,
            chunk_size_tokens=settings.chunk_size_tokens,
            chunk_overlap_tokens=settings.chunk_overlap_tokens,
        )
        logger.info(
            "Ingested and indexed document %s (%s, %d bytes, %d chunks)",
            document_id,
            filename,
            len(data),
            len(chunks),
        )
    finally:
        session.close()


def requeue_interrupted_ingestions() -> None:
    """Finish ingestions interrupted by a restart. Called at startup in a
    background thread. Rows stuck in pending/processing keep their stored
    file, so the pipeline simply runs again."""
    try:
        session = _open_session()
    except Exception:
        logger.warning("Startup recovery skipped: database unavailable", exc_info=True)
        return
    try:
        ids = [
            str(row[0])
            for row in session.execute(
                select(Document.id).where(Document.status.in_(_ACTIVE_STATUSES))
            ).all()
        ]
    finally:
        session.close()

    if not ids:
        return

    settings = get_settings()
    try:
        embedder = build_embedder(settings)
    except EmbeddingNotConfiguredError:
        logger.warning(
            "Startup recovery skipped: embedding is not configured "
            "(%d interrupted document(s) remain pending)",
            len(ids),
        )
        return

    logger.info("Startup recovery: re-queueing %d interrupted ingestion(s)", len(ids))
    store = get_vector_store()
    storage = DocumentStorage(settings.uploads_path)
    for document_id in ids:
        ingest_document(document_id, embedder=embedder, store=store, storage=storage)
