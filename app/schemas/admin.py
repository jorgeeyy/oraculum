"""Admin dashboard schemas (WS-6)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserOut(BaseModel):
    """Account row for the admin users table; is_active exposed for toggling."""

    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class UserPatch(BaseModel):
    """Partial update; role and/or is_active."""

    role: str | None = Field(default=None, pattern="^(admin|user)$")
    is_active: bool | None = None


class PasswordResetOut(BaseModel):
    """Result of an admin-forced password reset."""

    user_id: uuid.UUID
    sessions_revoked: int


class OverviewResponse(BaseModel):
    """One-shot facts for the overview cards."""

    version: str
    git_commit: str | None
    documents: int
    chunks: int | None
    conversations: int
    messages: int
    users: int
    active_admins: int
    chat_cache_enabled: bool


class UsageDay(BaseModel):
    day: str | None
    answers: int


class UsageResponse(BaseModel):
    """Chat usage proxy over chat_messages (token spend lands in WS-5)."""

    days: int
    daily: list[UsageDay]
    totals: dict


class FeedbackItem(BaseModel):
    message_id: str
    conversation_id: str
    feedback: str
    status: str | None
    model: str | None
    created_at: str
    answer: str


class AuditEventOut(BaseModel):
    id: str
    actor_label: str
    actor_user_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    request_id: str | None
    ip: str | None
    detail: dict | None
    created_at: str
