import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, Conversation, Document, User, UserSession
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


def corpus_fingerprint(session: Session) -> str:
    """Hash over every document's identity, content, and lifecycle state.

    Any change to any document row - ingest, delete, re-ingest, or a
    pending -> processing -> completed / failed transition - alters the
    fingerprint, which makes every semantic-cache answer recorded against
    the previous corpus state unreachable. The ingest/delete cache wipe is
    hygiene; this check is what guarantees a stale answer is never served.
    """
    rows = session.execute(
        select(Document.id, Document.content_hash, Document.status)
    ).all()
    digest = hashlib.sha256()
    for document_id, content_hash, status in sorted(
        rows, key=lambda row: str(row[0])
    ):
        digest.update(f"{document_id}:{content_hash}:{status}".encode())
    return digest.hexdigest()


def _scope_clause(api_key_hash: str | None, owner_user_id: uuid.UUID | None = None):
    """Ownership filter for conversation queries.

    Session-mode callers pass owner_user_id and match by owning user;
    key/local-mode callers pass the api_key_hash fingerprint (None = the
    single local scope) exactly as before identity existed.
    """
    if owner_user_id is not None:
        return Conversation.user_id == owner_user_id
    if api_key_hash is None:
        return Conversation.api_key_hash.is_(None)
    return Conversation.api_key_hash == api_key_hash


def _owns(
    conversation: Conversation,
    *,
    api_key_hash: str | None,
    owner_user_id: uuid.UUID | None,
) -> bool:
    """Per-row ownership check mirroring _scope_clause."""
    if owner_user_id is not None:
        return conversation.user_id == owner_user_id
    return conversation.api_key_hash == api_key_hash


def create_conversation(
    session: Session,
    *,
    title: str,
    api_key_hash: str | None,
    user_id: uuid.UUID | None = None,
) -> str:
    conversation = Conversation(title=title, api_key_hash=api_key_hash, user_id=user_id)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return str(conversation.id)


def list_conversations(
    session: Session,
    *,
    api_key_hash: str | None,
    owner_user_id: uuid.UUID | None = None,
) -> list[ConversationSummary]:
    rows = session.execute(
        select(Conversation, func.count(ChatMessage.id))
        .outerjoin(ChatMessage, ChatMessage.conversation_id == Conversation.id)
        .where(_scope_clause(api_key_hash, owner_user_id))
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


def load_chat_history(
    session: Session,
    conversation_id: str,
    *,
    api_key_hash: str | None,
    limit: int,
    owner_user_id: uuid.UUID | None = None,
) -> list[dict] | None:
    """Recent chat turns for prompt context, oldest first.

    Returns None when the conversation is absent or owned by another
    key/user (caller decides on 404); an empty list means "exists but no
    usable history". Only role/content are loaded - sources and timings
    are not needed to build context.
    """
    conversation = session.get(Conversation, uuid.UUID(conversation_id))
    if conversation is None or not _owns(
        conversation, api_key_hash=api_key_hash, owner_user_id=owner_user_id
    ):
        return None
    if limit <= 0:
        return []
    rows = session.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.role.in_(("user", "assistant")),
        )
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id)
    ).all()
    recent = list(reversed(rows[:limit]))  # oldest first within the window
    return [
        {"role": message.role, "content": message.content} for message in recent
    ]


def get_conversation(
    session: Session,
    conversation_id: str,
    *,
    api_key_hash: str | None,
    owner_user_id: uuid.UUID | None = None,
) -> ConversationDetail | None:
    """Return the conversation with its messages, or None if absent/out of scope."""
    conversation = session.get(Conversation, uuid.UUID(conversation_id))
    if conversation is None or not _owns(
        conversation, api_key_hash=api_key_hash, owner_user_id=owner_user_id
    ):
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
    session: Session,
    conversation_id: str,
    title: str,
    *,
    api_key_hash: str | None,
    owner_user_id: uuid.UUID | None = None,
) -> bool:
    result = session.execute(
        update(Conversation)
        .where(
            Conversation.id == uuid.UUID(conversation_id),
            _scope_clause(api_key_hash, owner_user_id),
        )
        .values(title=title)
    )
    session.commit()
    return result.rowcount > 0


def get_conversation_summary(
    session: Session,
    conversation_id: str,
    *,
    api_key_hash: str | None,
    owner_user_id: uuid.UUID | None = None,
) -> ConversationSummary:
    """Summary row for one in-scope conversation (assumes it exists)."""
    conversation = session.get(Conversation, uuid.UUID(conversation_id))
    assert conversation is not None and _owns(
        conversation, api_key_hash=api_key_hash, owner_user_id=owner_user_id
    )
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
    session: Session,
    conversation_id: str,
    *,
    api_key_hash: str | None,
    owner_user_id: uuid.UUID | None = None,
) -> bool:
    """Delete a conversation; its messages go with it (FK CASCADE)."""
    exists = session.scalars(
        select(Conversation).where(
            Conversation.id == uuid.UUID(conversation_id),
            _scope_clause(api_key_hash, owner_user_id),
        )
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
        feedback=message.feedback,
        sources=sources,
        model=message.model,
        retrieval_ms=message.retrieval_ms,
        generation_ms=message.generation_ms,
        created_at=message.created_at,
    )


def list_feedback(session: Session, *, rating: str | None = None) -> list[dict]:
    """Rated assistant messages, newest first (export/analytics helper).

    Joins the owning conversation so rows carry both ids; `rating` narrows to
    one bucket ("up"/"down"). Answers are included verbatim so an analyst can
    see what was voted on without a second query.
    """
    stmt = (
        select(ChatMessage, Conversation)
        .join(Conversation, ChatMessage.conversation_id == Conversation.id)
        .where(
            ChatMessage.role == "assistant",
            ChatMessage.feedback.is_not(None),
        )
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id)
    )
    if rating is not None:
        stmt = stmt.where(ChatMessage.feedback == rating)
    rows = session.execute(stmt).all()
    return [
        {
            "message_id": str(message.id),
            "conversation_id": str(conversation.id),
            "feedback": message.feedback,
            "status": message.status,
            "model": message.model,
            "created_at": message.created_at.isoformat(),
            "answer": message.content,
        }
        for message, conversation in rows
    ]


def set_message_feedback(
    session: Session,
    message_id: str,
    *,
    api_key_hash: str | None,
    rating: str | None,
    owner_user_id: uuid.UUID | None = None,
) -> tuple[bool, str | None]:
    """Set (or clear, rating=None) the 👍/👎 feedback on one assistant message.

    Ownership follows the conversation's owner (user id in session mode, the
    api_key_hash fingerprint otherwise); only assistant messages can carry
    feedback. Returns (found, new_rating): found is False when the message
    is absent, not an answer, or out of scope (caller raises 404);
    new_rating is the stored value after the operation.
    """
    try:
        parsed_id = uuid.UUID(message_id)
    except ValueError:
        return False, None
    message = session.get(ChatMessage, parsed_id)
    if message is None or message.role != "assistant":
        return False, None
    conversation = session.get(Conversation, message.conversation_id)
    if conversation is None or not _owns(
        conversation, api_key_hash=api_key_hash, owner_user_id=owner_user_id
    ):
        return False, None
    message.feedback = rating
    session.commit()
    return True, rating


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
    user_id: uuid.UUID | None = None,
) -> tuple[str, str]:
    """Persist a user question + assistant answer as one transaction.

    Creates the conversation when conversation_id is None. Touching
    updated_at keeps the sidebar ordered by most recent activity. Returns
    (conversation_id, assistant_message_id) so callers can expose the
    message id for per-answer feedback.
    """
    if conversation_id is None:
        conversation = Conversation(
            title=title, api_key_hash=api_key_hash, user_id=user_id
        )
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
    answer_message = ChatMessage(
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
    session.add(answer_message)
    conversation.updated_at = datetime.now()
    session.flush()  # assign the assistant message id before commit
    result = (str(conversation.id), str(answer_message.id))
    session.commit()
    return result


# ----------------------------------------------------------------- users

# Fixed identity of the API-key service account (the admin credential).
# It has no password and can never log in; requests carrying the configured
# API_KEY act as this account for attribution and dashboard purposes.
SERVICE_ADMIN_EMAIL = "admin@service.local"


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def create_user(
    session: Session,
    *,
    email: str,
    password_hash: str | None,
    display_name: str | None = None,
    role: str = "user",
) -> str:
    user = User(
        email=normalize_email(email),
        password_hash=password_hash,
        display_name=display_name,
        role=role,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return str(user.id)


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def get_user(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def count_users(session: Session) -> int:
    return session.scalar(select(func.count(User.id))) or 0


def touch_last_login(session: Session, user_id: uuid.UUID) -> None:
    user = session.get(User, user_id)
    if user is not None:
        user.last_login_at = datetime.now(timezone.utc)
        session.commit()


def adopt_conversations(
    session: Session, *, api_key_hash: str | None, user_id: uuid.UUID
) -> int:
    """Stamp unowned conversations in one scope as owned by user_id.

    Used when an admin account is created so pre-identity chat history
    (scoped by fingerprint, or the single local scope) becomes visible to
    the admin's logged-in session. Rows already owned are never touched.
    """
    scope = (
        Conversation.api_key_hash.is_(None)
        if api_key_hash is None
        else Conversation.api_key_hash == api_key_hash
    )
    result = session.execute(
        update(Conversation)
        .where(scope, Conversation.user_id.is_(None))
        .values(user_id=user_id)
    )
    session.commit()
    return result.rowcount or 0


def ensure_service_admin(
    session: Session, *, api_key_hash: str | None = None
) -> User | None:
    """Get-or-create the admin service account backing the API_KEY.

    On first creation it adopts the conversations already scoped to the
    given fingerprint so the admin's session shows pre-identity history.
    Returns None when api_key_hash is None (local scope has no service
    account to attribute to).
    """
    if api_key_hash is None:
        return None
    user = get_user_by_email(session, SERVICE_ADMIN_EMAIL)
    if user is not None:
        return user
    user_id = create_user(
        session, email=SERVICE_ADMIN_EMAIL, password_hash=None, role="admin"
    )
    adopt_conversations(session, api_key_hash=api_key_hash, user_id=uuid.UUID(user_id))
    return get_user(session, uuid.UUID(user_id))


# -------------------------------------------------------------- sessions

def create_session(
    session: Session,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    row = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return str(row.id)


def get_session_by_token(session: Session, token_hash: str) -> UserSession | None:
    return session.scalar(
        select(UserSession).where(UserSession.token_hash == token_hash)
    )


def revoke_session(session: Session, token_hash: str) -> bool:
    """Revoke one session; False when absent or already revoked."""
    row = get_session_by_token(session, token_hash)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    session.commit()
    return True


def touch_session_expiry(session: Session, session_id: uuid.UUID, expires_at: datetime) -> None:
    """Sliding renewal: extend a live session's expiry."""
    row = session.get(UserSession, session_id)
    if row is not None:
        row.expires_at = expires_at
        session.commit()
