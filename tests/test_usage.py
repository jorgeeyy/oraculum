"""WS-5 cost governance: pricing, usage ledger, budget gates, aggregates.

The contract under test:

- `app/services/pricing.py` is the single price source: the eval scripts
  import it, so a dashboard number and an eval number can never diverge.
- `record_usage` appends one ledger row per spending request and is
  fail-open by design: a broken write logs a warning and the request
  still succeeds; with `usage_ledger_enabled=False` it writes nothing.
- `enforce_budget` runs on /chat* BEFORE any LLM spend: `used >= limit`
  is a structured 403 `usage_limit_exceeded`, >= 80% of a window surfaces
  `X-Usage-Warn`, per-user admin overrides win over the global settings
  (0 disables a window), and any failure in the check itself lets the
  request through (governance is a brake, not an availability risk).
- Aggregates: `GET /admin/usage` ranks spend by account and model over
  the window; `GET /usage/me` shows ONLY the caller's ledger rows (404
  for API-key/local credentials); `PATCH /admin/users/{id}` sets budget
  overrides and audits them as `user.budget_override`.

Database-backed tests take `db_conn` and are self-contained: they
register their own users and seed their own ledger rows (the fixture
removes them afterwards), never assuming development-database state.
"""

import importlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.auth import AuthActor
from app.config import Settings, get_settings
from app.db import repositories
from app.db.models import UsageEvent
from app.services.pricing import cost_usd
from app.services.usage import record_usage

PASSWORD = "correct-horse-battery"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUESTION = {"question": "How many days of vacation do employees get?"}


# --------------------------------------------------------------- helpers


def _db():
    from app.db.session import get_engine

    return sessionmaker(bind=get_engine(), expire_on_commit=False)()


def _email(tag: str) -> str:
    """Fresh address per test: never collides with development-database users."""
    return f"{tag}-{uuid.uuid4().hex[:8]}@local.dev"


def _register(client, email: str) -> None:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


def _promote_to_admin(email: str) -> None:
    session = _db()
    try:
        user = repositories.get_user_by_email(session, email)
        assert user is not None
        user.role = "admin"
        session.commit()
    finally:
        session.close()


def _admin_client(client_with_settings, email: str):
    """Registered + promoted admin, logged in (cookie held by the client)."""
    client = client_with_settings()
    _register(client, email)
    _promote_to_admin(email)
    return client


def _user_id(email: str) -> uuid.UUID:
    session = _db()
    try:
        user = repositories.get_user_by_email(session, email)
        assert user is not None
        return user.id
    finally:
        session.close()


def _seed_spend(
    user_id: uuid.UUID | None,
    usd: float,
    *,
    model: str = "unit-test-model",
    days_ago: float = 0.0,
) -> None:
    """Insert ledger rows directly (the write path itself is tested below)."""
    session = _db()
    try:
        session.add(
            UsageEvent(
                user_id=user_id,
                endpoint="chat",
                model=model,
                tokens_in=1000,
                tokens_out=500,
                est_usd=usd,
                status="answered",
                created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            )
        )
        session.commit()
    finally:
        session.close()


def _usage_rows(
    *,
    user_id: uuid.UUID | None = None,
    endpoint: str | None = None,
    model: str | None = None,
) -> list[UsageEvent]:
    session = _db()
    try:
        stmt = select(UsageEvent)
        if user_id is not None:
            stmt = stmt.where(UsageEvent.user_id == user_id)
        if endpoint is not None:
            stmt = stmt.where(UsageEvent.endpoint == endpoint)
        if model is not None:
            stmt = stmt.where(UsageEvent.model == model)
        return list(session.scalars(stmt).all())
    finally:
        session.close()


def _audit_rows(*, target_id: str | None = None) -> list[dict]:
    session = _db()
    try:
        rows = repositories.list_audit_events(session, limit=200)
        if target_id is not None:
            rows = [row for row in rows if row["target_id"] == target_id]
        return rows
    finally:
        session.close()


# --------------------------------------------------------------- pricing


class TestPricing:
    def test_embedding_price(self) -> None:
        assert cost_usd("text-embedding-3-small", 1_000_000, 0) == 0.02

    def test_chat_price_blends_input_and_output(self) -> None:
        assert cost_usd("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75
        assert cost_usd("gpt-4o-mini", 1234, 5678) == round(
            1234 / 1e6 * 0.15 + 5678 / 1e6 * 0.6, 6
        )

    def test_unknown_model_returns_none_not_zero(self) -> None:
        # Never a fabricated zero: callers decide what None means.
        assert cost_usd("test-chat-model", 1000, 1000) is None

    def test_eval_scripts_import_the_shared_price_table(self) -> None:
        """Single source: evals and the ledger can never disagree on price."""
        eval_chat = importlib.import_module("scripts.evaluate_chat")
        assert eval_chat.cost_usd is cost_usd

        chat_source = (PROJECT_ROOT / "scripts" / "evaluate_chat.py").read_text(
            encoding="utf-8"
        )
        assert "from app.services.pricing import cost_usd" in chat_source
        assert "PRICES_PER_1M" not in chat_source  # no private copy of the table

        # scripts/evaluate.py drives evaluate_chat -> same table transitively.
        runner_source = (PROJECT_ROOT / "scripts" / "evaluate.py").read_text(
            encoding="utf-8"
        )
        assert "from scripts import evaluate_chat" in runner_source
        assert "PRICES_PER_1M" not in runner_source


# ------------------------------------------------------------ budget gate


@pytest.mark.usefixtures("db_conn")
class TestBudgetGate:
    """enforce_budget on /chat*: under / edge / over / warn / fail-open."""

    def test_under_budget_passes_without_warning(
        self, client_with_settings
    ) -> None:
        email = _email("under")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 0.10)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200
        assert "X-Usage-Warn" not in response.headers

    def test_at_exact_limit_is_refused(self, client_with_settings) -> None:
        email = _email("edge")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 1.0)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["code"] == "usage_limit_exceeded"
        assert detail["window"] == "daily"
        assert detail["limit_usd"] == pytest.approx(1.0)
        assert detail["spent_usd"] == pytest.approx(1.0)

    def test_over_budget_refused_before_any_spend(
        self, client_with_settings, fake_embedder, fake_generator
    ) -> None:
        """No embedding, no generation, no ledger row: the refusal is free."""
        email = _email("over")
        client = client_with_settings(
            usage_ledger_enabled=True, usage_daily_budget_usd=1.0
        )
        _register(client, email)
        uid = _user_id(email)
        _seed_spend(uid, 5.0)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "usage_limit_exceeded"
        assert fake_embedder.batches == []
        assert fake_generator.calls == []
        # The refused request itself wrote nothing; only the seeded row exists.
        assert len(_usage_rows(user_id=uid)) == 1

    def test_warn_header_at_80_percent(self, client_with_settings) -> None:
        email = _email("warn")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 0.85)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200
        assert response.headers.get("X-Usage-Warn") == "85% of budget used"

    def test_no_warn_below_threshold(self, client_with_settings) -> None:
        email = _email("quiet")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 0.79)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200
        assert "X-Usage-Warn" not in response.headers

    def test_stream_refused_over_budget(self, client_with_settings) -> None:
        email = _email("stream-over")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 5.0)

        response = client.post("/chat/stream", json=QUESTION)
        assert response.status_code == 403  # refused before the stream starts
        assert response.json()["detail"]["code"] == "usage_limit_exceeded"

    def test_stream_carries_warn_header(self, client_with_settings) -> None:
        email = _email("stream-warn")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 0.85)

        response = client.post("/chat/stream", json=QUESTION)
        assert response.status_code == 200
        assert response.headers.get("X-Usage-Warn") == "85% of budget used"

    def test_per_user_override_beats_global(self, client_with_settings) -> None:
        email = _email("override")
        client = client_with_settings(usage_daily_budget_usd=100.0)
        _register(client, email)
        uid = _user_id(email)
        _seed_spend(uid, 0.50)
        session = _db()
        try:
            repositories.update_user(session, uid, daily_budget_usd=0.20)
            session.commit()
        finally:
            session.close()

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["code"] == "usage_limit_exceeded"
        assert detail["limit_usd"] == pytest.approx(0.20)

    def test_zero_override_disables_window(self, client_with_settings) -> None:
        email = _email("zero")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        uid = _user_id(email)
        _seed_spend(uid, 5.0)
        session = _db()
        try:
            repositories.update_user(session, uid, daily_budget_usd=0)
            session.commit()
        finally:
            session.close()

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200
        assert "X-Usage-Warn" not in response.headers

    def test_monthly_window_blocks(self, client_with_settings) -> None:
        email = _email("monthly")
        client = client_with_settings(
            usage_daily_budget_usd=None, usage_monthly_budget_usd=0.5
        )
        _register(client, email)
        _seed_spend(_user_id(email), 0.60)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail["window"] == "monthly"
        assert detail["limit_usd"] == pytest.approx(0.5)

    def test_failed_budget_check_fails_open(
        self, client_with_settings, monkeypatch
    ) -> None:
        """A broken budget check allows the request (availability first)."""
        email = _email("failopen")
        client = client_with_settings(usage_daily_budget_usd=1.0)
        _register(client, email)
        _seed_spend(_user_id(email), 5.0)  # would normally refuse

        def _boom():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr("app.services.usage.open_db_session", _boom)
        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200

    def test_api_key_chat_touches_no_database(
        self, client_with_settings, monkeypatch
    ) -> None:
        """Key-auth stays DB-free: no budget check, no scoping, no persist."""
        opened = {"budget": 0, "chat": 0}

        def _budget_db():
            opened["budget"] += 1
            raise AssertionError("budget gate opened the database")

        def _chat_db():
            opened["chat"] += 1
            raise AssertionError("chat opened the database")

        monkeypatch.setattr("app.services.usage.open_db_session", _budget_db)
        monkeypatch.setattr("app.api.chat.open_db_session", _chat_db)

        client = client_with_settings(api_key="secret-dbfree")
        response = client.post(
            "/chat", json=QUESTION, headers={"X-API-Key": "secret-dbfree"}
        )
        assert response.status_code == 200, response.text
        assert opened == {"budget": 0, "chat": 0}


# ----------------------------------------------------------- ledger writes


@pytest.mark.usefixtures("db_conn")
class TestLedgerWrites:
    def test_chat_writes_one_row(self, client_with_settings) -> None:
        email = _email("ledger-chat")
        client = client_with_settings(usage_ledger_enabled=True)
        _register(client, email)
        uid = _user_id(email)

        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200

        rows = _usage_rows(user_id=uid)
        assert len(rows) == 1
        row = rows[0]
        assert row.endpoint == "chat"
        assert row.status == "insufficient_evidence"
        assert row.model == "test-chat-model"
        assert row.tokens_in == 0 and row.tokens_out == 0
        # Unknown test model -> NULL estimate, never a fabricated zero.
        assert row.est_usd is None
        assert row.latency_ms is not None

    def test_retrieve_writes_row(self, client_with_settings) -> None:
        email = _email("ledger-retrieve")
        client = client_with_settings(usage_ledger_enabled=True)
        _register(client, email)
        uid = _user_id(email)

        response = client.post("/retrieve", json={"question": "vacation days?"})
        assert response.status_code == 200, response.text

        rows = _usage_rows(user_id=uid, endpoint="retrieve")
        assert len(rows) == 1
        assert rows[0].status == "ok"
        assert rows[0].tokens_out == 0

    def test_ingest_writes_row_attributed_to_owner(
        self, client_with_settings
    ) -> None:
        email = _email("ledger-ingest")
        client = client_with_settings(usage_ledger_enabled=True)
        _register(client, email)
        uid = _user_id(email)
        tag = uuid.uuid4().hex[:8]

        response = client.post(
            "/documents",
            files={
                "file": (
                    f"handbook-{tag}.md",
                    f"# Handbook {tag}\n\nEmployees receive 20 days of leave.",
                    "text/markdown",
                )
            },
        )
        assert response.status_code == 202, response.text

        # TestClient runs BackgroundTasks inline; ingestion already recorded.
        rows = _usage_rows(user_id=uid, endpoint="ingest")
        assert len(rows) == 1
        assert rows[0].status == "ok"
        assert rows[0].model == "test-embedding-model"

    def test_ledger_off_writes_nothing(self, client_with_settings) -> None:
        email = _email("ledger-off")
        client = client_with_settings()  # usage_ledger_enabled pinned False
        _register(client, email)
        uid = _user_id(email)

        assert client.post("/chat", json=QUESTION).status_code == 200
        assert (
            client.post("/retrieve", json={"question": "vacation?"}).status_code
            == 200
        )
        assert _usage_rows(user_id=uid) == []

    def test_write_failure_never_breaks_the_request(
        self, client_with_settings, monkeypatch
    ) -> None:
        """Telemetry is best-effort: a broken ledger write only logs."""
        email = _email("ledger-broken")
        client = client_with_settings(usage_ledger_enabled=True)
        _register(client, email)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("ledger down")

        monkeypatch.setattr("app.services.usage.sessionmaker", _boom)
        response = client.post("/chat", json=QUESTION)
        assert response.status_code == 200, response.text

    def test_record_usage_auto_prices_known_model(self) -> None:
        """Direct unit: _AUTO_COST prices from the shared table, None if unknown."""
        settings = Settings(
            **{**get_settings().model_dump(), "usage_ledger_enabled": True}
        )
        local = AuthActor(
            mode="local", role="local", api_key_hash=None, user_id=None
        )
        record_usage(
            actor=local,
            endpoint="retrieve",
            model="gpt-4o-mini",
            tokens_in=1_000_000,
            tokens_out=0,
            latency_ms=5.0,
            status="ok",
            settings=settings,
        )
        priced = [
            row
            for row in _usage_rows(endpoint="retrieve", model="gpt-4o-mini")
            if row.tokens_in == 1_000_000
        ]
        assert priced, "auto-priced row was not written"
        assert priced[-1].est_usd == pytest.approx(0.15)
        assert priced[-1].user_id is None  # local mode = unattributed

        record_usage(
            actor=local,
            endpoint="retrieve",
            model="no-such-model",
            tokens_in=100,
            tokens_out=100,
            latency_ms=1.0,
            status="ok",
            settings=settings,
        )
        unpriced = [
            row
            for row in _usage_rows(endpoint="retrieve", model="no-such-model")
            if row.tokens_in == 100
        ]
        assert unpriced and unpriced[-1].est_usd is None


# -------------------------------------------------------------- aggregates


@pytest.mark.usefixtures("db_conn")
class TestAggregates:
    def test_admin_usage_ranks_accounts_and_models(
        self, client_with_settings
    ) -> None:
        tag = uuid.uuid4().hex[:8]
        admin = _admin_client(client_with_settings, _email("agg-admin"))
        victim = client_with_settings()
        email = _email("agg-user")
        _register(victim, email)
        uid = _user_id(email)

        model_a = f"unit-model-a-{tag}"
        model_b = f"unit-model-b-{tag}"
        _seed_spend(uid, 0.25, model=model_a)
        _seed_spend(uid, 0.15, model=model_b)
        _seed_spend(None, 0.40, model=model_a)  # unattributed (API-key/local)

        response = admin.get("/admin/usage")
        assert response.status_code == 200
        data = response.json()

        by_user = {row["key"]: row for row in data["by_user"]}
        assert by_user[email]["requests"] == 2
        assert by_user[email]["spend_usd"] == pytest.approx(0.40)
        assert by_user[email]["tokens_in"] == 2000
        assert by_user["unattributed"]["requests"] == 1
        assert by_user["unattributed"]["spend_usd"] == pytest.approx(0.40)

        by_model = {row["key"]: row for row in data["by_model"]}
        assert by_model[model_a]["requests"] == 2
        assert by_model[model_a]["spend_usd"] == pytest.approx(0.65)
        assert by_model[model_b]["requests"] == 1
        assert by_model[model_b]["spend_usd"] == pytest.approx(0.15)

        # Window spend reconciles with the daily buckets; totals at least
        # include this test's rows (development rows may add more).
        assert data["spend_usd"] == pytest.approx(
            sum(day["spend_usd"] for day in data["daily"])
        )
        assert data["spend_usd"] >= 0.80
        assert data["totals"]["requests"] >= 3
        # Merged shape keeps the transcript columns the admin chart reads.
        assert "answers" in data["totals"]
        assert "feedback_up" in data["totals"]
        assert all(
            {"day", "requests", "spend_usd"} <= set(day) for day in data["daily"]
        )

    def test_usage_me_scopes_to_own_rows(self, client_with_settings) -> None:
        client_a, client_b = client_with_settings(), client_with_settings()
        email_a, email_b = _email("me-a"), _email("me-b")
        _register(client_a, email_a)
        _register(client_b, email_b)
        _seed_spend(_user_id(email_a), 0.30)
        _seed_spend(_user_id(email_b), 0.90)

        mine = client_a.get("/usage/me")
        assert mine.status_code == 200
        body = mine.json()
        assert body["spend_usd"] == pytest.approx(0.30)
        assert body["daily_spend_usd"] == pytest.approx(0.30)
        assert body["totals"]["spend_usd"] == pytest.approx(0.30)
        assert body["totals"]["requests"] == 1
        assert body["daily"] and body["daily"][0]["requests"] == 1
        assert body["daily"][0]["spend_usd"] == pytest.approx(0.30)

        theirs = client_b.get("/usage/me").json()
        assert theirs["spend_usd"] == pytest.approx(0.90)

    def test_usage_me_daily_buckets_oldest_first(
        self, client_with_settings
    ) -> None:
        email = _email("me-days")
        client = client_with_settings()
        _register(client, email)
        uid = _user_id(email)
        _seed_spend(uid, 0.20, days_ago=1.0)
        _seed_spend(uid, 0.10, days_ago=0.0)

        body = client.get("/usage/me").json()
        assert len(body["daily"]) == 2
        assert body["daily"][0]["day"] < body["daily"][1]["day"]
        assert body["spend_usd"] == pytest.approx(0.30)
        # daily_spend_usd is the today window only.
        assert body["daily_spend_usd"] == pytest.approx(0.10)
        assert body["monthly_spend_usd"] == pytest.approx(0.30)

    def test_usage_me_requires_session_credential(
        self, client_with_settings
    ) -> None:
        key_client = client_with_settings(api_key="secret-me")
        response = key_client.get("/usage/me", headers={"X-API-Key": "secret-me"})
        assert response.status_code == 404

        anonymous = client_with_settings()
        assert anonymous.get("/usage/me").status_code == 404

    def test_usage_me_reports_effective_budgets(
        self, client_with_settings
    ) -> None:
        email = _email("me-budget")
        client = client_with_settings(usage_daily_budget_usd=7.5)
        _register(client, email)
        uid = _user_id(email)

        body = client.get("/usage/me").json()
        assert body["daily_budget_usd"] == pytest.approx(7.5)
        assert body["monthly_budget_usd"] is None

        session = _db()
        try:
            repositories.update_user(session, uid, daily_budget_usd=2.5)
            session.commit()
        finally:
            session.close()

        body = client.get("/usage/me").json()
        assert body["daily_budget_usd"] == pytest.approx(2.5)  # override wins


# --------------------------------------------------------- budget overrides


@pytest.mark.usefixtures("db_conn")
class TestBudgetOverride:
    def test_patch_sets_clears_and_audits(self, client_with_settings) -> None:
        admin = _admin_client(client_with_settings, _email("bo-admin"))
        victim = client_with_settings()
        email = _email("bo-user")
        _register(victim, email)
        uid = _user_id(email)

        response = admin.patch(
            f"/admin/users/{uid}", json={"daily_budget_usd": 2.5}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["daily_budget_usd"] == pytest.approx(2.5)
        assert body["monthly_budget_usd"] is None
        assert body["role"] == "user"  # budgets-only patch leaves role alone

        rows = _audit_rows(target_id=str(uid))
        budget_events = [
            row for row in rows if row["action"] == "user.budget_override"
        ]
        assert budget_events, "budget override was not audited"
        changes = budget_events[-1]["detail"]["changes"]
        assert changes["daily_budget_usd"] == pytest.approx(2.5)

        # Monthly set independently; explicit null clears the daily override.
        response = admin.patch(
            f"/admin/users/{uid}", json={"monthly_budget_usd": 10}
        )
        assert response.json()["monthly_budget_usd"] == pytest.approx(10)
        response = admin.patch(
            f"/admin/users/{uid}", json={"daily_budget_usd": None}
        )
        body = response.json()
        assert body["daily_budget_usd"] is None
        assert body["monthly_budget_usd"] == pytest.approx(10)  # untouched

    def test_negative_budget_rejected(self, client_with_settings) -> None:
        admin = _admin_client(client_with_settings, _email("bo-neg"))
        victim = client_with_settings()
        email = _email("bo-neg-user")
        _register(victim, email)
        uid = _user_id(email)

        response = admin.patch(
            f"/admin/users/{uid}", json={"daily_budget_usd": -1}
        )
        assert response.status_code == 422

    def test_non_admin_cannot_set_budgets(self, client_with_settings) -> None:
        victim = client_with_settings()
        email = _email("bo-forbidden")
        _register(victim, email)
        uid = _user_id(email)

        response = victim.patch(
            f"/admin/users/{uid}", json={"daily_budget_usd": 1}
        )
        assert response.status_code == 403
