import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, Conversation, Document
from app.schemas.chat import Source
from app.schemas.conversations import (
    ConversationDetail,
    ConversationSummary,
    MessageRecord,
)
from app.schemas.documents import DocumentRecord


def _to_record(doc: Document) -> DocumentRecord:
    """Convert a document into its record representation. The returned record
    contains the document's identifying information and current processing
    state.
    """
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
    """Return the newest active document with this content hash, if any.

    Completed documents short-circuit as duplicates; pending/processing ones
    short-circuit as "still being indexed" so a re-upload never starts a
    second ingestion of the same bytes. Failed documents are excluded so the
    same bytes stay retryable.
    """
    doc = session.scalars(
        select(Document)
        .where(
            Document.content_hash == content_hash,
            Document.status.in_(["completed", "pending", "processing"]),
        )
        .order_by(Document.created_at.desc())
        .limit(1)
    ).first()
    return _to_record(doc) if doc else None


def find_failed_by_content_hash(session: Session, content_hash: str) -> DocumentRecord | None:
    """Return the newest failed document with this content hash, if any."""
    doc = session.scalars(
        select(Document)
        .where(Document.content_hash == content_hash, Document.status == "failed")
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


def mark_processing(session: Session, document_id: str) -> None:
    """Flip a queued document to processing (clears any stale error text)."""
    session.execute(
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(status="processing", error_message=None)
    )
    session.commit()


def reopen_document(
    session: Session,
    document_id: str,
    *,
    original_filename: str,
    size_bytes: int,
) -> None:
    """Reset a failed document back to pending so identical bytes can retry."""
    session.execute(
        update(Document)
        .where(Document.id == uuid.UUID(document_id))
        .values(
            status="pending",
            error_message=None,
            original_filename=original_filename,
            size_bytes=size_bytes,
            chunk_count=None,
        )
    )
    session.commit()


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


def _scope_clause(api_key_hash: str | None):
    if api_key_hash is None:
        return Conversation.api_key_hash.is_(None)
    return Conversation.api_key_hash == api_key_hash


def create_conversation(
    session: Session, *, title: str, api_key_hash: str | None
) -> str:
    conversation = Conversation(title=title, api_key_hash=api_key_hash)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return str(conversation.id)


def list_conversations(
    session: Session, *, api_key_hash: str | None
) -> list[ConversationSummary]:
    rows = session.execute(
        select(Conversation, func.count(ChatMessage.id))
        .outerjoin(ChatMessage, ChatMessage.conversation_id == Conversation.id)
        .where(_scope_clause(api_key_hash))
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    ).all()
    return [
        ConversationSummary(
            id=str(conversation.id),
            title=conversation.title,
            message_count=count,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for conversation, count in rows
    ]


def get_conversation(
    session: Session, conversation_id: str, *, api_key_hash: str | None
) -> ConversationDetail | None:
    """Return the conversation with its messages, or None if absent/out of scope."""
    conversation = session.get(Conversation, uuid.UUID(conversation_id))
    if conversation is None or conversation.api_key_hash != api_key_hash:
        return None
    messages = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    return ConversationDetail(
        id=str(conversation.id),
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_to_message_record(message) for message in messages],
    )


def rename_conversation(
    session: Session, conversation_id: str, title: str, *, api_key_hash: str | None
) -> bool:
    result = session.execute(
        update(Conversation)
        .where(Conversation.id == uuid.UUID(conversation_id), _scope_clause(api_key_hash))
        .values(title=title)
    )
    session.commit()
    return result.rowcount > 0


def get_conversation_summary(
    session: Session, conversation_id: str, *, api_key_hash: str | None
) -> ConversationSummary:
    """Summary row for one in-scope conversation (assumes it exists)."""
    conversation = session.get(Conversation, uuid.UUID(conversation_id))
    assert conversation is not None
    count = session.scalar(
        select(func.count(ChatMessage.id)).where(
            ChatMessage.conversation_id == conversation.id
        )
    )
    return ConversationSummary(
        id=str(conversation.id),
        title=conversation.title,
        message_count=count or 0,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def delete_conversation(
    session: Session, conversation_id: str, *, api_key_hash: str | None
) -> bool:
    """Delete a conversation; its messages go with it (FK CASCADE)."""
    exists = session.scalars(
        select(Conversation)
        .where(Conversation.id == uuid.UUID(conversation_id), _scope_clause(api_key_hash))
    ).first()
    if exists is None:
        return False
    session.execute(
        delete(ChatMessage).where(ChatMessage.conversation_id == exists.id)
    )
    session.execute(delete(Conversation).where(Conversation.id == exists.id))
    session.commit()
    return True


def _to_message_record(message: ChatMessage) -> MessageRecord:
    sources = (
        [Source.model_validate(item) for item in message.sources_json]
        if message.sources_json
        else []
    )
    return MessageRecord(
        id=str(message.id),
        role=message.role,
        content=message.content,
        status=message.status,
        sources=sources,
        model=message.model,
        retrieval_ms=message.retrieval_ms,
        generation_ms=message.generation_ms,
        created_at=message.created_at,
    )


def save_chat_exchange(
    session: Session,
    *,
    conversation_id: str | None,
    title: str,
    api_key_hash: str | None,
    question: str,
    answer: str,
    status: str,
    sources: list[Source],
    model: str,
    retrieval_ms: float,
    generation_ms: float,
) -> str:
    """Persist a user question + assistant answer as one transaction.

    Creates the conversation when conversation_id is None. Touching
    updated_at keeps the sidebar ordered by most recent activity.
    """
    if conversation_id is None:
        conversation = Conversation(title=title, api_key_hash=api_key_hash)
        session.add(conversation)
        session.flush()
    else:
        conversation = session.get(Conversation, uuid.UUID(conversation_id))
        if conversation is None:
            session.rollback()
            raise LookupError(f"conversation {conversation_id} not found")

    # Windows clocks can return the same microsecond for two rows flushed in
    # one transaction, and the UUID tiebreak is random — so pin an explicit,
    # strictly increasing timestamp to keep messages in exchange order.
    now = datetime.now()
    session.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="user",
            content=question,
            created_at=now,
        )
    )
    session.add(
        ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            status=status,
            sources_json=[source.model_dump() for source in sources],
            model=model,
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            created_at=now + timedelta(microseconds=1),
        )
    )
    conversation.updated_at = datetime.now()
    session.commit()
    return str(conversation.id)
