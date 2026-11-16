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
    daily_budget_usd: float | None = None
    monthly_budget_usd: float | None = None


class UserPatch(BaseModel):
    """Partial update; role, is_active, and/or budget overrides.

    Budget overrides are per-user caps in USD; null clears the override
    (the global USAGE_*_BUDGET_USD settings apply again). A value of 0
    disables that window for this user entirely.
    """

    role: str | None = Field(default=None, pattern="^(admin|user)$")
    is_active: bool | None = None
    daily_budget_usd: float | None = Field(default=None, ge=0)
    monthly_budget_usd: float | None = Field(default=None, ge=0)


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
    # Ledger columns (usage_events); zero when the day had no spend rows.
    requests: int = 0
    spend_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class UsageRollup(BaseModel):
    """Spend grouped by account or model over the window."""

    key: str  # email or model name; "unattributed" for NULL ledger rows
    requests: int
    spend_usd: float
    tokens_in: int
    tokens_out: int


class UsageResponse(BaseModel):
    """Usage proxy: answers/feedback (transcript) + spend (usage_events)."""

    days: int
    daily: list[UsageDay]
    totals: dict
    by_user: list[UsageRollup] = []
    by_model: list[UsageRollup] = []
    spend_usd: float = 0.0  # window spend (sum of daily)


class MyUsageResponse(BaseModel):
    """One account's own spend: window rollups + budget state (GET /usage/me)."""

    days: int
    daily: list[UsageDay]
    totals: dict
    spend_usd: float
    daily_budget_usd: float | None
    monthly_budget_usd: float | None
    daily_spend_usd: float
    monthly_spend_usd: float


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
