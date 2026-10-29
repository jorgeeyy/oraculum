import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_size_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_overlap_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Normalized (casefolded) email; unique among users.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Argon2id hash. NULL only for the API-key service account, which can
    # never log in with a password.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # SHA-256 of the opaque cookie token; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    api_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Owning user for session-mode chats; NULL for legacy/local rows and for
    # key-mode rows whose service account has not been resolved yet.
    # ON DELETE SET NULL: deleting a user releases their conversations back
    # to scope-by-fingerprint rather than destroying chat history.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Thumbs feedback on the assistant answer: "up" / "down" / NULL (none).
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sources_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieval_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
