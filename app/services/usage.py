"""Cost governance (WS-5): usage ledger writes and budget enforcement.

Two responsibilities, both fail-open by design:

- `record_usage` appends one row per token-spending request from counters
  the pipeline already keeps (generator tokens, embedder token delta).
  A ledger failure must never break the request it describes.
- `enforce_budget` runs BEFORE any LLM spend on /chat*: it sums today's
  and this month's estimated spend (per-user for session actors, global
  for unscoped ones) and refuses with a structured 403 when a budget is
  exhausted. A failed budget check allows the request - governance is a
  brake, not an availability risk.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.auth import AuthActor
from app.api.deps import open_db_session
from app.config import Settings
from app.db.models import UsageEvent, User
from app.db.session import get_engine
from app.observability import request_id_var
from app.services.pricing import cost_usd

logger = logging.getLogger(__name__)


# Sentinel: "compute est_usd from the model's price"; passing None
# explicitly records an unknown price (never a fabricated zero).
_AUTO_COST = object()


def record_usage(
    *,
    actor: AuthActor,
    endpoint: str,
    model: str | None,
    tokens_in: int,
    tokens_out: int,
    latency_ms: float | None,
    status: str | None,
    est_usd: float | None | object = _AUTO_COST,
    settings: Settings | None = None,
) -> None:
    """Append one usage event; never raises into the caller's request.

    user_id is only stamped for real accounts (session mode): API-key and
    local spend stay unattributed (NULL), matching the shared corpus they
    search. est_usd defaults to the model's price; callers mixing models
    (embed + generation) pass the combined estimate, or None when neither
    model has a known price. With `settings.usage_ledger_enabled` false
    the write is skipped entirely (tests pin it off).
    """
    if settings is not None and not settings.usage_ledger_enabled:
        return
    try:
        session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    except Exception:
        logger.warning("usage ledger unavailable (no engine)", exc_info=True)
        return
    try:
        if est_usd is _AUTO_COST:
            est_usd = cost_usd(model or "", tokens_in, tokens_out)
        session.add(
            UsageEvent(
                request_id=request_id_var.get() or None,
                user_id=actor.user_id if actor.mode == "session" else None,
                endpoint=endpoint,
                model=model,
                tokens_in=max(0, tokens_in),
                tokens_out=max(0, tokens_out),
                est_usd=est_usd,
                latency_ms=latency_ms,
                status=status,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - telemetry is best-effort by design
        session.rollback()
        logger.warning("usage write failed (endpoint=%s)", endpoint, exc_info=True)
    finally:
        session.close()


@dataclass(frozen=True)
class BudgetCheck:
    """Outcome of enforce_budget: nothing, a warning header, or a refusal."""

    warn_header: str | None = None


def _utc_day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _utc_month_start(now: datetime) -> datetime:
    return _utc_day_start(now).replace(day=1)


def _spend_since(session: Session, since: datetime, *, user_id: uuid.UUID | None) -> float:
    """Estimated USD spent since `since`; unknown prices count as 0."""
    stmt = select(func.coalesce(func.sum(UsageEvent.est_usd), 0.0)).where(
        UsageEvent.created_at >= since
    )
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    return float(session.scalar(stmt) or 0.0)


def _limits(actor: AuthActor, settings: Settings, session: Session) -> tuple[float | None, float | None]:
    """(daily, monthly) budget in USD for this actor.

    Session users may carry per-user overrides (admin-managed); everyone
    else - and users without overrides - falls back to the global
    settings. Unscoped spend (API-key/local) is governed by the global
    budget only, against the whole ledger.
    """
    daily = settings.usage_daily_budget_usd
    monthly = settings.usage_monthly_budget_usd
    if actor.mode == "session" and actor.user_id is not None:
        row = session.execute(
            select(User.daily_budget_usd, User.monthly_budget_usd).where(
                User.id == actor.user_id
            )
        ).one_or_none()
        if row is not None:
            daily = row[0] if row[0] is not None else daily
            monthly = row[1] if row[1] is not None else monthly
    return (
        daily if daily and daily > 0 else None,
        monthly if monthly and monthly > 0 else None,
    )


def enforce_budget(actor: AuthActor, settings: Settings) -> BudgetCheck:
    """Refuse an over-budget /chat* request before any LLM spend.

    Applies only when at least one budget window is configured. Session
    spend is checked against the user's limits (override or global);
    unscoped spend against the global limits over the whole ledger. Any
    failure in the check itself allows the request (fail-open).
    """
    # Fast path: no global budgets and no per-user override possible ->
    # never touch the database (key-auth stays DB-free).
    if (
        not settings.usage_daily_budget_usd
        and not settings.usage_monthly_budget_usd
        and not (actor.mode == "session" and actor.user_id is not None)
    ):
        return BudgetCheck()
    try:
        session = open_db_session()
    except Exception:
        logger.warning("budget check skipped (database unavailable)", exc_info=True)
        return BudgetCheck()
    try:
        daily_limit, monthly_limit = _limits(actor, settings, session)
        if daily_limit is None and monthly_limit is None:
            return BudgetCheck()

        now = datetime.now(timezone.utc)
        scoped_user = actor.user_id if actor.mode == "session" else None
        daily_used = (
            _spend_since(session, _utc_day_start(now), user_id=scoped_user)
            if daily_limit is not None
            else 0.0
        )
        monthly_used = (
            _spend_since(session, _utc_month_start(now), user_id=scoped_user)
            if monthly_limit is not None
            else 0.0
        )

        for used, limit, window in (
            (daily_used, daily_limit, "daily"),
            (monthly_used, monthly_limit, "monthly"),
        ):
            if limit is not None and used >= limit:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "usage_limit_exceeded",
                        "window": window,
                        "limit_usd": limit,
                        "spent_usd": round(used, 6),
                        "message": (
                            f"The {window} usage budget "
                            f"(${limit:.2f}) is exhausted. "
                            "Contact an administrator."
                        ),
                    },
                )

        # >= warn_ratio of either budget: surface a warning header.
        ratio = settings.usage_warn_ratio
        for used, limit in ((daily_used, daily_limit), (monthly_used, monthly_limit)):
            if limit is not None and used >= ratio * limit:
                percent = int(round(100 * used / limit))
                return BudgetCheck(warn_header=f"{percent}% of budget used")
        return BudgetCheck()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - governance must not break availability
        logger.warning("budget check failed; allowing request", exc_info=True)
        return BudgetCheck()
    finally:
        session.close()


def usage_warn_headers(check: BudgetCheck) -> dict[str, str]:
    """Response headers for a budget warning (empty when none)."""
    return {"X-Usage-Warn": check.warn_header} if check.warn_header else {}
