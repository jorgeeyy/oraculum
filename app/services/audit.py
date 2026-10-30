"""Audit trail: record who did what, fail-open.

Every admin/security-relevant mutation calls `record_audit`. It must never
break the mutation it describes: a full rollback + warning log is the worst
case, the user-facing action still succeeds. Request id comes from the
observability contextvar so audit rows correlate with request logs.
"""

import logging
import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.observability import request_id_var

logger = logging.getLogger(__name__)


def actor_label(request: Request) -> tuple[uuid.UUID | None, str]:
    """(user_id, label) for the current request's actor, set by get_actor."""
    actor = getattr(request.state, "actor", None)
    if actor is None:
        return None, "anonymous"
    if actor.mode == "session" and actor.user_id is not None:
        return actor.user_id, f"user:{actor.user_id}"
    if actor.mode == "api_key":
        return None, "api-key"
    return None, "local"


def record_audit(
    session: Session,
    request: Request,
    *,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append one audit event; never raises into the caller's request."""
    user_id, label = actor_label(request)
    event = AuditEvent(
        actor_user_id=user_id,
        actor_label=label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id_var.get() or None,
        ip=_client_ip(request),
        detail=detail,
    )
    try:
        session.add(event)
        session.commit()
    except Exception:  # noqa: BLE001 - audit is best-effort by design
        session.rollback()
        logger.warning("audit write failed (action=%s)", action, exc_info=True)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client:
        return request.client.host[:64]
    return None
