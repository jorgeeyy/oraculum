"""Admin dashboard tests (WS-6): role gate, audited mutations, aggregates.

The 403 matrix is the core contract: anonymous (even in open-local mode),
regular users and the local scope never reach admin data; admin sessions
and the API-key service credential do. Every mutation lands in the audit
trail, and the lockout guards (self-demote, last-admin, service account)
return 409 without writing anything.
"""

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.api.auth import scope_kwargs  # noqa: F401  (kept: shared helpers)
from app.config import get_settings
from app.db import repositories
from app.db.models import AuditEvent, ChatMessage, Conversation

PASSWORD = "correct-horse-battery"

ADMIN_ENDPOINTS = [
    "/admin/overview",
    "/admin/users",
    "/admin/usage",
    "/admin/feedback",
    "/admin/audit",
]


def _db():
    session = sessionmaker(bind=get_settings_engine(), expire_on_commit=False)()
    return session


def get_settings_engine():
    from app.db.session import get_engine

    return get_engine()


def _register(client, email: str):
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201
    return response.json()


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


def _audit_actions() -> list[str]:
    session = _db()
    try:
        rows = repositories.list_audit_events(session, limit=200)
        return [row["action"] for row in rows]
    finally:
        session.close()


class TestAdminGate:
    @pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
    def test_admin_session_passes(
        self, endpoint: str, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "gate-admin@local.dev")
        assert admin.get(endpoint).status_code == 200

    @pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
    def test_anonymous_rejected_even_in_open_local_mode(
        self, endpoint: str, client_with_settings, db_conn
    ) -> None:
        # No API_KEY configured: the local scope may chat, but the admin
        # surface always requires a real admin identity.
        client = client_with_settings()
        assert client.get(endpoint).status_code == 403

    @pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
    def test_regular_user_rejected(
        self, endpoint: str, client_with_settings, db_conn
    ) -> None:
        user = client_with_settings()
        _register(user, "gate-user@local.dev")
        assert user.get(endpoint).status_code == 403

    def test_api_key_service_credential_passes(
        self, client_with_settings, db_conn
    ) -> None:
        # The configured API_KEY is the admin credential (WS-3 contract).
        client = client_with_settings(api_key="secret-a")
        response = client.get(
            "/admin/overview", headers={"X-API-Key": "secret-a"}
        )
        assert response.status_code == 200


class TestOverview:
    def test_overview_reports_version_and_counts(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "ov-admin@local.dev")
        body = admin.get("/admin/overview").json()
        from app.main import APP_VERSION

        assert body["version"] == APP_VERSION
        assert isinstance(body["documents"], int)
        assert isinstance(body["conversations"], int)
        assert isinstance(body["users"], int)
        assert body["users"] >= 1  # at least the admin account itself
        assert body["active_admins"] >= 1
        assert body["chat_cache_enabled"] is False  # conftest pins it off
        assert "git_commit" in body


class TestUsers:
    def test_list_users_shows_all_accounts_without_secrets(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "lu-admin@local.dev")
        user = client_with_settings()
        _register(user, "lu-user@local.dev")

        rows = admin.get("/admin/users").json()
        emails = {row["email"]: row for row in rows}
        assert "lu-admin@local.dev" in emails
        assert "lu-user@local.dev" in emails
        assert emails["lu-admin@local.dev"]["role"] == "admin"
        assert emails["lu-user@local.dev"]["role"] == "user"
        assert emails["lu-user@local.dev"]["is_active"] is True
        # Password material never leaves the server.
        assert "password_hash" not in rows[0]

    def test_promote_and_deactivate_are_audited(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "pm-admin@local.dev")
        user = client_with_settings()
        target = _register(user, "pm-user@local.dev")

        promoted = admin.patch(
            f"/admin/users/{target['id']}", json={"role": "admin"}
        )
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "admin"

        deactivated = admin.patch(
            f"/admin/users/{target['id']}", json={"is_active": False}
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False

        actions = _audit_actions()
        assert "user.update" in actions

    def test_unknown_user_is_404(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "nf-admin@local.dev")
        ghost = uuid.uuid4()
        assert admin.patch(f"/admin/users/{ghost}", json={"role": "user"}).status_code == 404
        assert admin.post(f"/admin/users/{ghost}/password-reset").status_code == 404
        assert admin.patch("/admin/users/not-a-uuid", json={"role": "user"}).status_code == 404

    def test_cannot_demote_or_deactivate_self(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "self-admin@local.dev")
        me = admin.get("/auth/me").json()

        demote = admin.patch(f"/admin/users/{me['id']}", json={"role": "user"})
        assert demote.status_code == 409
        deactivate = admin.patch(f"/admin/users/{me['id']}", json={"is_active": False})
        assert deactivate.status_code == 409
        # Self-lockout guard leaves the account untouched.
        assert admin.get("/auth/me").status_code == 200

    def test_last_active_admin_cannot_be_demoted(
        self, client_with_settings, db_conn
    ) -> None:
        # Deterministic against a shared dev DB: temporarily deactivate
        # every OTHER active admin so the target is provably the last one,
        # then restore their state afterwards.
        admin = _admin_client(client_with_settings, "last1-admin@local.dev")
        helper = client_with_settings()
        second = _register(helper, "last2-admin@local.dev")
        _promote_to_admin("last2-admin@local.dev")

        # Demoting the second admin is allowed (one admin remains).
        assert (
            admin.patch(f"/admin/users/{second['id']}", json={"role": "user"}).status_code
            == 200
        )

        session = _db()
        restored: list[tuple[uuid.UUID, bool]] = []
        try:
            me = repositories.get_user_by_email(session, "last1-admin@local.dev")
            assert me is not None
            others = [
                user
                for user in repositories.list_users(session)
                if user.role == "admin" and user.is_active and user.id != me.id
            ]
            for user in others:
                restored.append((user.id, user.is_active))
                user.is_active = False
            session.commit()

            # admin is now the only active admin; the API-key credential
            # (not self) attempts the demotion → last-admin guard fires.
            key_client = client_with_settings(api_key="secret-a")
            blocked = key_client.patch(
                f"/admin/users/{me.id}",
                json={"role": "user"},
                headers={"X-API-Key": "secret-a"},
            )
            assert blocked.status_code == 409
            assert "active admin" in blocked.json()["detail"]
        finally:
            for user_id, was_active in restored:
                user = session.get(type(me), user_id)
                if user is not None:
                    user.is_active = was_active
            session.commit()
            session.close()

    def test_service_account_is_immutable(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "svc-admin@local.dev")
        session = _db()
        try:
            service = repositories.ensure_service_admin(
                session, api_key_hash="f" * 64
            )
            assert service is not None
            service_id = service.id
        finally:
            session.close()

        response = admin.patch(
            f"/admin/users/{service_id}", json={"is_active": False}
        )
        assert response.status_code == 409
        assert "immutable" in response.json()["detail"]

    def test_invalid_role_rejected_by_schema(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "schema-admin@local.dev")
        user = client_with_settings()
        target = _register(user, "schema-user@local.dev")
        response = admin.patch(
            f"/admin/users/{target['id']}", json={"role": "superuser"}
        )
        assert response.status_code == 422


class TestPasswordReset:
    def test_reset_revokes_all_sessions_and_is_audited(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "pr-admin@local.dev")
        user = client_with_settings()
        target = _register(user, "pr-user@local.dev")
        assert user.get("/auth/me").status_code == 200  # live session first

        response = admin.post(f"/admin/users/{target['id']}/password-reset")
        assert response.status_code == 200
        assert response.json()["sessions_revoked"] >= 1

        # The victim's cookie no longer resolves.
        assert user.get("/auth/me").status_code == 401
        assert "user.password_reset" in _audit_actions()


class TestUsageFeedbackAudit:
    def test_usage_reports_daily_answers_and_totals(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "usage-admin@local.dev")
        session = _db()
        try:
            conversation_id = repositories.create_conversation(
                session, title="usage", api_key_hash=None
            )
            session.add_all(
                [
                    ChatMessage(
                        conversation_id=uuid.UUID(conversation_id),
                        role="user",
                        content="question?",
                    ),
                    ChatMessage(
                        conversation_id=uuid.UUID(conversation_id),
                        role="assistant",
                        content="answer.",
                        feedback="up",
                    ),
                    ChatMessage(
                        conversation_id=uuid.UUID(conversation_id),
                        role="assistant",
                        content="another answer.",
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()

        body = admin.get("/admin/usage?days=7").json()
        assert body["days"] == 7
        assert len(body["daily"]) >= 1
        assert body["daily"][-1]["answers"] >= 2  # assistant rows only
        assert body["totals"]["answers"] >= 2
        assert body["totals"]["feedback_up"] >= 1

    def test_usage_rejects_absurd_windows(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "usage2-admin@local.dev")
        assert admin.get("/admin/usage?days=0").status_code == 422
        assert admin.get("/admin/usage?days=400").status_code == 422

    def test_feedback_inbox_lists_rated_answers(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "fb-admin@local.dev")
        session = _db()
        try:
            conversation_id = repositories.create_conversation(
                session, title="feedback", api_key_hash=None
            )
            answer = ChatMessage(
                conversation_id=uuid.UUID(conversation_id),
                role="assistant",
                content="the rated answer",
                feedback="down",
            )
            session.add(answer)
            session.commit()
            answer_id = str(answer.id)
        finally:
            session.close()

        rows = admin.get("/admin/feedback").json()
        assert any(row["message_id"] == answer_id for row in rows)
        only_down = admin.get("/admin/feedback?rating=down").json()
        assert all(row["feedback"] == "down" for row in only_down)
        assert admin.get("/admin/feedback?rating=maybe").status_code == 422

    def test_audit_tail_lists_recent_events(
        self, client_with_settings, db_conn
    ) -> None:
        admin = _admin_client(client_with_settings, "audit-admin@local.dev")
        user = client_with_settings()
        target = _register(user, "audit-user@local.dev")
        admin.patch(f"/admin/users/{target['id']}", json={"role": "user"})

        rows = admin.get("/admin/audit?limit=50").json()
        actions = [row["action"] for row in rows]
        assert "user.update" in actions
        update_row = next(row for row in rows if row["action"] == "user.update")
        assert update_row["target_type"] == "user"
        assert update_row["target_id"] == str(target["id"])
        assert update_row["actor_label"].startswith("user:")
        assert "created_at" in update_row

    def test_audit_requires_admin(
        self, client_with_settings, db_conn
    ) -> None:
        user = client_with_settings()
        _register(user, "no-audit@local.dev")
        assert user.get("/admin/audit").status_code == 403
