import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import Document
from app.schemas.documents import DocumentRecord


def _to_record(doc: Document) -> DocumentRecord:
    return DocumentRecord(
        id=str(doc.id),
        original_filename=doc.original_filename,
        file_type=doc.file_type,
        size_bytes=doc.size_bytes,
        chunk_count=doc.chunk_count,
        status=doc.status,
        error_message=doc.error_message,
        embedding_model=doc.embedding_model,
        chunk_size_tokens=doc.chunk_size_tokens,
        chunk_overlap_tokens=doc.chunk_overlap_tokens,
        content_hash=doc.content_hash,
        created_at=doc.created_at,
    )


def create_document(
    session: Session,
    *,
    document_id: str,
    original_filename: str,
    file_type: str,
    size_bytes: int,
    content_hash: str | None = None,
) -> None:
    session.add(
        Document(
            id=uuid.UUID(document_id),
            original_filename=original_filename,
            file_type=file_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
        )
    )
    session.commit()


def find_by_content_hash(session: Session, content_hash: str) -> DocumentRecord | None:
    """Return the newest completed document with this content hash, if any.

    Only completed documents count: a failed ingest with the same bytes must
    stay retryable as a fresh upload rather than short-circuiting as a
    "duplicate".
    """
    doc = session.scalars(
        select(Document)
        .where(Document.content_hash == content_hash, Document.status == "completed")
        .order_by(Document.created_at.desc())
        .limit(1)
    ).first()
    return _to_record(doc) if doc else None


def list_documents(session: Session) -> list[DocumentRecord]:
    docs = session.scalars(select(Document).order_by(Document.created_at.desc())).all()
    return [_to_record(doc) for doc in docs]


def get_document(session: Session, document_id: str) -> DocumentRecord | None:
    doc = session.get(Document, uuid.UUID(document_id))
    return _to_record(doc) if doc else None


def complete_document(
    session: Session,
    document_id: str,
    chunk_count: int,
    *,
    embedding_model: str,
    chunk_size_tokens: int,
    chunk_overlap_tokens: int,
) -> None:
    """Mark a document completed and record the indexing config it was built with."""
    session.execute(
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(
            status="completed",
            chunk_count=chunk_count,
            error_message=None,
            embedding_model=embedding_model,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
    )
    session.commit()


def fail_document(session: Session, document_id: str, message: str) -> None:
    session.execute(
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(status="failed", error_message=message)
    )
    session.commit()


def delete_document(session: Session, document_id: str) -> None:
    session.execute(delete(Document).where(Document.id == uuid.UUID(document_id)))
    session.commit()
