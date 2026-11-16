"""Own-account usage view (WS-5): what THIS user has spent.

Session actors see their own ledger rows (chat/retrieve/ingest spend,
attributed at write time); API-key/local callers have no account to
attribute spend to, so they get 404 - the ledger's NULL-user rows are
theirs collectively, not individually.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import AuthActor, get_actor
from app.api.deps import get_db_session
from app.config import Settings, get_settings
from app.db.models import UsageEvent, User
from app.db.repositories import usage_totals
from app.schemas.admin import MyUsageResponse, UsageDay

router = APIRouter(prefix="/usage", tags=["usage"])


def _window_spend(session: Session, user_id: uuid.UUID, since: datetime) -> float:
    return float(
        session.scalar(
            select(func.coalesce(func.sum(UsageEvent.est_usd), 0.0)).where(
                UsageEvent.user_id == user_id, UsageEvent.created_at >= since
            )
        )
        or 0.0
    )


@router.get("/me", response_model=MyUsageResponse)
def my_usage(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
    settings: Settings = Depends(get_settings),
) -> MyUsageResponse:
    """This account's spend: window rollups + budget state.

    Budget fields are the effective limits (per-user override when set,
    otherwise the global settings) plus the spend already accrued in each
    window, so the UI can render a progress bar without a second call.
    """
    if actor.mode != "session" or actor.user_id is None:
        raise HTTPException(
            status_code=404,
            detail="No usage is attributed to this credential.",
        )

    user = db.get(User, actor.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    # The user's own ledger rows, per UTC day, newest bucket fetched then
    # reversed into chart order (oldest first).
    day = func.date_trunc("day", UsageEvent.created_at)
    stmt = (
        select(
            day,
            func.count(UsageEvent.id),
            func.coalesce(func.sum(UsageEvent.est_usd), 0.0),
            func.coalesce(func.sum(UsageEvent.tokens_in), 0),
            func.coalesce(func.sum(UsageEvent.tokens_out), 0),
        )
        .where(UsageEvent.user_id == actor.user_id)
        .group_by(day)
        .order_by(day.desc())
        .limit(days)
    )
    rows = list(db.execute(stmt).all())
    rows.reverse()
    daily = [
        UsageDay(
            day=bucket.strftime("%Y-%m-%d") if bucket else None,
            answers=0,  # per-account view: answers live on the admin chart
            requests=requests,
            spend_usd=round(float(spend or 0.0), 6),
            tokens_in=int(tokens_in or 0),
            tokens_out=int(tokens_out or 0),
        )
        for bucket, requests, spend, tokens_in, tokens_out in rows
    ]

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)

    return MyUsageResponse(
        days=days,
        daily=daily,
        totals=usage_totals(db, user_id=actor.user_id),
        spend_usd=round(sum(row.spend_usd for row in daily), 6),
        daily_budget_usd=user.daily_budget_usd or settings.usage_daily_budget_usd,
        monthly_budget_usd=user.monthly_budget_usd
        or settings.usage_monthly_budget_usd,
        daily_spend_usd=round(_window_spend(db, actor.user_id, day_start), 6),
        monthly_spend_usd=round(_window_spend(db, actor.user_id, month_start), 6),
    )
