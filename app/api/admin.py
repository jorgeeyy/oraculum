"""Admin dashboard API (WS-6): overview, users, usage, feedback, audit.

Every route requires an admin: a session with role=admin, or the API_KEY
service credential (resolved by get_actor → require_admin). All mutations
write to the audit trail via `record_audit` (fail-open).

User-management protections:
- admins cannot demote/deactivate themselves (no self-lockout),
- the last active admin cannot be demoted or deactivated,
- the API-key service account (admin@service.local) is immutable.
"""

import subprocess
import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.api.deps import get_db_session
from app.config import Settings, get_settings
from app.db import repositories
from app.db.models import ChatMessage, Conversation, Document
from app.schemas.admin import (
    AdminUserOut,
    AuditEventOut,
    FeedbackItem,
    OverviewResponse,
    PasswordResetOut,
    UsageDay,
    UsageResponse,
    UserPatch,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/admin", tags=["admin"])

# The API-key service account exists to back the key; role/state are fixed.
SERVICE_EMAIL = repositories.SERVICE_ADMIN_EMAIL


@lru_cache(maxsize=1)
def _git_commit() -> str | None:
    """Short HEAD sha for the overview card; None outside a git checkout."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - informational only
        return None


def _user_out(user: repositories.User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


# ---------------------------------------------------------------- overview


@router.get("/overview", response_model=OverviewResponse)
def overview(
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    _admin: object = Depends(require_admin),
) -> OverviewResponse:
    """One-shot facts for the overview cards: version, corpus, accounts."""
    documents = db.scalar(select(func.count(Document.id))) or 0
    chunks = db.scalar(select(func.sum(Document.chunk_count))) or 0
    conversations = db.scalar(select(func.count(Conversation.id))) or 0
    messages = db.scalar(select(func.count(ChatMessage.id))) or 0
    return OverviewResponse(
        version=_app_version(),
        git_commit=_git_commit(),
        documents=documents,
        chunks=int(chunks),
        conversations=conversations,
        messages=messages,
        users=repositories.count_users(db),
        active_admins=repositories.count_active_admins(db),
        chat_cache_enabled=settings.chat_cache_enabled,
    )


def _app_version() -> str:
    from app.main import APP_VERSION

    return APP_VERSION


# ------------------------------------------------------------------- users


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    db: Session = Depends(get_db_session),
    _admin: object = Depends(require_admin),
) -> list[AdminUserOut]:
    """All accounts with role and active state."""
    return [_user_out(user) for user in repositories.list_users(db)]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def patch_user(
    user_id: str,
    patch: UserPatch,
    request: Request,
    db: Session = Depends(get_db_session),
    admin: object = Depends(require_admin),
) -> AdminUserOut:
    """Change a user's role and/or active flag, with lockout guards."""
    try:
        parsed = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found.") from None

    target = repositories.get_user(db, parsed)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.email == SERVICE_EMAIL:
        raise HTTPException(
            status_code=409, detail="The API-key service account is immutable."
        )

    actor_user_id = getattr(getattr(request.state, "actor", None), "user_id", None)
    self_modify = actor_user_id == target.id

    demoting = patch.role is not None and patch.role != "admin" and target.role == "admin"
    deactivating = patch.is_active is False and target.is_active
    if self_modify and (demoting or deactivating):
        raise HTTPException(
            status_code=409, detail="You cannot demote or deactivate your own account."
        )
    if (demoting or deactivating) and repositories.count_active_admins(
        db, exclude_user_id=target.id
    ) == 0:
        raise HTTPException(
            status_code=409, detail="At least one active admin must remain."
        )

    updated = repositories.update_user(
        db, parsed, role=patch.role, is_active=patch.is_active
    )
    assert updated is not None  # fetched above

    changed: dict[str, object] = {}
    if patch.role is not None:
        changed["role"] = patch.role
    if patch.is_active is not None:
        changed["is_active"] = patch.is_active
    record_audit(
        db,
        request,
        action="user.update",
        target_type="user",
        target_id=str(target.id),
        detail={"email": target.email, "changes": changed},
    )
    return _user_out(updated)


@router.post("/users/{user_id}/password-reset", response_model=PasswordResetOut)
def reset_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
    admin: object = Depends(require_admin),
) -> PasswordResetOut:
    """Revoke every live session for a user (signs them out everywhere)."""
    try:
        parsed = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found.") from None

    target = repositories.get_user(db, parsed)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    revoked = repositories.revoke_user_sessions(db, parsed)
    record_audit(
        db,
        request,
        action="user.password_reset",
        target_type="user",
        target_id=str(target.id),
        detail={"email": target.email, "sessions_revoked": revoked},
    )
    return PasswordResetOut(user_id=target.id, sessions_revoked=revoked)


# ------------------------------------------------------------------- usage


@router.get("/usage", response_model=UsageResponse)
def usage(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db_session),
    _admin: object = Depends(require_admin),
) -> UsageResponse:
    """Chat usage proxy: answers/day + lifetime feedback totals.

    Token-spend columns arrive with usage_events in WS-5; this endpoint's
    shape (daily buckets + totals) extends in place when they do.
    """
    daily = repositories.usage_daily(db, days=days)
    totals = repositories.usage_totals(db)
    return UsageResponse(
        days=days,
        daily=[UsageDay(**row) for row in daily],
        totals=totals,
    )


# --------------------------------------------------------------- feedback


@router.get("/feedback", response_model=list[FeedbackItem])
def feedback(
    rating: str | None = Query(default=None, pattern="^(up|down)$"),
    db: Session = Depends(get_db_session),
    _admin: object = Depends(require_admin),
) -> list[FeedbackItem]:
    """Feedback inbox: rated answers, newest first (moderation view)."""
    rows = repositories.list_feedback(db, rating=rating)
    return [FeedbackItem(**row) for row in rows]


# ------------------------------------------------------------------ audit


@router.get("/audit", response_model=list[AuditEventOut])
def audit_tail(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db_session),
    _admin: object = Depends(require_admin),
) -> list[AuditEventOut]:
    """Most recent audit events, newest first."""
    rows = repositories.list_audit_events(db, limit=limit)
    return [AuditEventOut(**row) for row in rows]
