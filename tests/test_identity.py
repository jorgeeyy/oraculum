"""Identity tests (WS-3): accounts, sessions, scoping, API-key admin.

Covers the auth endpoints (register/login/logout/me), the session lifecycle
(cookie issue, revoke, expiry, sliding renewal), login throttling, and the
two scoping modes: session users partition by owning user id while the
API-key path keeps the pre-identity fingerprint scope and attributes to the
auto-provisioned admin service account.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.auth import AuthActor, api_key_fingerprint, scope_kwargs
from app.config import Settings, get_settings
from app.db import repositories
from app.db.models import Conversation, User, UserSession
from app.services.rate_limit import FixedWindowRateLimiter, get_rate_limiter

from test_streaming import parse_sse  # noqa: F401  (kept: cross-module helpers)

DOC_ID = "11111111-2222-3333-4444-555555555555"

STORE_HIT = {
    "id": f"{DOC_ID}#0",
    "score": 0.71,
    "text": "Employees receive 20 days of annual leave.",
    "metadata": {
        "document_id": DOC_ID,
        "filename": "handbook.md",
        "page": 1,
        "chunk_index": 0,
    },
}

PASSWORD = "correct-horse-battery"


def _db():
    session = sessionmaker(bind=get_settings_engine(), expire_on_commit=False)()
    return session


def get_settings_engine():
    from app.db.session import get_engine, normalize_database_url

    return get_engine()


def register(client: TestClient, email: str, password: str = PASSWORD):
    return client.post(
        "/auth/register", json={"email": email, "password": password}
    )


def _users_before_register() -> int:
    """User count before a register call; decides first-user assertions."""
    session = _db()
    try:
        return repositories.count_users(session)
    finally:
        session.close()


class TestRegister:
    def test_first_user_is_admin_and_signed_in(self, client, db_conn) -> None:
        was_empty = _users_before_register() == 0
        response = register(client, "first-admin@local.dev")
        assert response.status_code == 201
        # Only the very first account of a fresh install becomes admin; on a
        # database that already has accounts, later registrations are "user".
        assert response.json()["role"] == ("admin" if was_empty else "user")
        assert response.json()["email"] == "first-admin@local.dev"
        # Session cookie set: /auth/me works immediately.
        me = client.get("/auth/me")
        assert me.status_code == 200
        # Password material never leaks.
        assert "password" not in response.text

    def test_second_user_is_regular_user(self, client_with_settings, db_conn) -> None:
        was_empty = _users_before_register() == 0
        client = client_with_settings()
        first_role = register(client, "boss@local.dev").json()["role"]
        assert first_role == ("admin" if was_empty else "user")
        # A second account is always a regular user, never admin.
        client2 = client_with_settings()
        assert register(client2, "worker@local.dev").json()["role"] == "user"

    def test_duplicate_email_conflicts(self, client, db_conn) -> None:
        assert register(client, "dup@local.dev").status_code == 201
        again = register(client, "DUP@local.dev ")  # case/whitespace insensitive
        assert again.status_code == 409

    def test_invalid_email_and_short_password_rejected(self, client, db_conn) -> None:
        assert (
            client.post(
                "/auth/register", json={"email": "not-an-email", "password": PASSWORD}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/auth/register", json={"email": "short@local.dev", "password": "abc"}
            ).status_code
            == 422
        )

    def test_registration_can_be_disabled(
        self, client_with_settings, db_conn
    ) -> None:
        client = client_with_settings(auth_allow_registration=False)
        response = register(client, "closed@local.dev")
        assert response.status_code == 403
        assert client.get("/auth/me").status_code == 401

    def test_register_adopts_local_scope_chats(self, client, db_conn) -> None:
        was_empty = _users_before_register() == 0
        session = _db()
        try:
            # A pre-identity local-scope conversation (the dev flow).
            conversation_id = repositories.create_conversation(
                session, title="old chat", api_key_hash=None
            )
        finally:
            session.close()
        assert register(client, "adopter@local.dev").status_code == 201
        listed = client.get("/conversations")
        assert listed.status_code == 200
        listed_ids = [c["id"] for c in listed.json()]
        if was_empty:
            # The first (admin) account adopts pre-identity chats so the
            # admin sees them; any other local-scope chats come along too.
            assert conversation_id in listed_ids
        else:
            # Only the first account adopts history; later accounts start
            # with an empty, user-scoped inbox.
            assert conversation_id not in listed_ids
        client.delete(f"/conversations/{conversation_id}")


class TestLoginLogout:
    def test_login_and_logout_lifecycle(self, client_with_settings, db_conn) -> None:
        owner = client_with_settings()
        assert register(owner, "life@local.dev").status_code == 201
        owner.post("/auth/logout")

        fresh = client_with_settings()  # separate cookie jar
        ok = fresh.post(
            "/auth/login", json={"email": "life@local.dev", "password": PASSWORD}
        )
        assert ok.status_code == 200
        assert fresh.get("/auth/me").status_code == 200

        assert fresh.post("/auth/logout").status_code == 200
        assert fresh.get("/auth/me").status_code == 401

    def test_wrong_password_and_unknown_email_401(
        self, client_with_settings, db_conn
    ) -> None:
        owner = client_with_settings()
        register(owner, "guard@local.dev")
        owner.post("/auth/logout")

        fresh = client_with_settings()
        assert (
            fresh.post(
                "/auth/login", json={"email": "guard@local.dev", "password": "wrong-thing"}
            ).status_code
            == 401
        )
        assert (
            fresh.post(
                "/auth/login", json={"email": "ghost@local.dev", "password": PASSWORD}
            ).status_code
            == 401
        )

    def test_disabled_account_cannot_login(self, client_with_settings, db_conn) -> None:
        owner = client_with_settings()
        register(owner, "fired@local.dev")
        session = _db()
        try:
            user = repositories.get_user_by_email(session, "fired@local.dev")
            user.is_active = False
            session.commit()
        finally:
            session.close()
        fresh = client_with_settings()
        assert (
            fresh.post(
                "/auth/login", json={"email": "fired@local.dev", "password": PASSWORD}
            ).status_code
            == 403
        )

    def test_service_account_can_never_login(self, db_conn) -> None:
        session = _db()
        try:
            repositories.create_user(
                session, email="svc@service.local", password_hash=None, role="admin"
            )
        finally:
            session.close()
        from app.main import create_app

        app_client = TestClient(create_app())
        assert (
            app_client.post(
                "/auth/login", json={"email": "svc@service.local", "password": PASSWORD}
            ).status_code
            == 401
        )

    def test_login_is_throttled_per_email(self, client_with_settings, db_conn) -> None:
        owner = client_with_settings()
        register(owner, "throttle@local.dev")
        owner.post("/auth/logout")

        client = client_with_settings()
        limiter = FixedWindowRateLimiter()
        client.app.dependency_overrides[get_rate_limiter] = lambda: limiter
        for _ in range(5):
            assert (
                client.post(
                    "/auth/login",
                    json={"email": "throttle@local.dev", "password": "wrong-thing"},
                ).status_code
                == 401
            )
        blocked = client.post(
            "/auth/login",
            json={"email": "throttle@local.dev", "password": "wrong-thing"},
        )
        assert blocked.status_code == 429
        assert blocked.headers.get("retry-after")

    def test_logout_is_idempotent(self, client, db_conn) -> None:
        assert client.post("/auth/logout").status_code == 200
        assert client.post("/auth/logout").status_code == 200


class TestSessionLifecycle:
    def test_expired_session_is_rejected(self, client_with_settings, db_conn) -> None:
        owner = client_with_settings()
        register(owner, "expired@local.dev")

        session = _db()
        try:
            row = repositories.get_session_by_token(
                session, _only_token_hash(session, "expired@local.dev")
            )
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        finally:
            session.close()
        assert owner.get("/auth/me").status_code == 401

    def test_revoked_session_is_rejected(self, client_with_settings, db_conn) -> None:
        owner = client_with_settings()
        register(owner, "revoked@local.dev")
        session = _db()
        try:
            token_hash = _only_token_hash(session, "revoked@local.dev")
            assert repositories.revoke_session(session, token_hash) is True
            # Second revoke is a no-op.
            assert repositories.revoke_session(session, token_hash) is False
        finally:
            session.close()
        assert owner.get("/auth/me").status_code == 401

    def test_sliding_renewal_extends_expiry(self, client_with_settings, db_conn) -> None:
        owner = client_with_settings()
        register(owner, "slide@local.dev")
        session = _db()
        try:
            row = repositories.get_session_by_token(
                session, _only_token_hash(session, "slide@local.dev")
            )
            # Push expiry to just past half the TTL so the next request renews.
            shortened = datetime.now(timezone.utc) + timedelta(days=1)
            row.expires_at = shortened
            session.commit()
            before = row.expires_at
        finally:
            session.close()
        assert owner.get("/auth/me").status_code == 200
        session = _db()
        try:
            row = repositories.get_session_by_token(
                session, _only_token_hash(session, "slide@local.dev")
            )
            assert row.expires_at > before  # extended in place
        finally:
            session.close()


def _only_token_hash(session, email: str) -> str:
    """The single live session row belonging to this test's user."""
    rows = session.scalars(
        select(UserSession.token_hash)
        .join(User, User.id == UserSession.user_id)
        .where(User.email == email)
    ).all()
    assert len(rows) == 1, f"expected one session for {email}, got {len(rows)}"
    return rows[0]


class TestSessionScoping:
    def test_users_cannot_see_each_others_chats(
        self, client_with_settings, fake_vector_store, fake_generator, db_conn
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        alice = client_with_settings()
        assert register(alice, "alice@local.dev").status_code == 201
        created = alice.post(
            "/chat",
            json={"question": "How many days?", "conversation_id": None},
        ).json()
        conversation_id = created["conversation_id"]
        message_id = created["message_id"]

        bob = client_with_settings()
        assert register(bob, "bob@local.dev").status_code == 201
        assert all(
            c["id"] != conversation_id for c in bob.get("/conversations").json()
        )
        assert bob.get(f"/conversations/{conversation_id}").status_code == 404
        assert bob.delete(f"/conversations/{conversation_id}").status_code == 404
        assert (
            bob.put(
                f"/chat/messages/{message_id}/feedback", json={"rating": "up"}
            ).status_code
            == 404
        )

        # The owner still sees and can delete it.
        assert alice.get(f"/conversations/{conversation_id}").status_code == 200
        assert alice.delete(f"/conversations/{conversation_id}").status_code == 200

    def test_chats_are_stamped_with_the_owning_user(
        self, client_with_settings, fake_vector_store, fake_generator, db_conn
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        client = client_with_settings()
        register(client, "stamp@local.dev")
        created = client.post(
            "/chat",
            json={"question": "How many days?", "conversation_id": None},
        ).json()
        session = _db()
        try:
            conversation = session.get(
                Conversation, uuid.UUID(created["conversation_id"])
            )
            assert conversation.user_id is not None
            assert conversation.api_key_hash is None  # session-mode attribution
        finally:
            session.close()
        client.delete(f"/conversations/{created['conversation_id']}")


class TestApiKeyAsAdmin:
    def test_key_requests_stay_fingerprint_scoped_and_db_free_auth(
        self, client_with_settings, fake_vector_store, fake_generator, db_conn
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        client = client_with_settings(api_key="secret-a")
        created = client.post(
            "/chat",
            json={"question": "How many days?", "conversation_id": None},
            headers={"X-API-Key": "secret-a"},
        ).json()
        session = _db()
        try:
            conversation = session.get(
                Conversation, uuid.UUID(created["conversation_id"])
            )
            fingerprint = api_key_fingerprint(Settings(api_key="secret-a"))
            assert conversation.api_key_hash == fingerprint
            # Persist-time attribution: owned by the admin service account.
            assert conversation.user_id is not None
            owner = session.get(User, conversation.user_id)
            assert owner.role == "admin"
            assert owner.email == repositories.SERVICE_ADMIN_EMAIL
        finally:
            session.close()
        client.delete(
            f"/conversations/{created['conversation_id']}",
            headers={"X-API-Key": "secret-a"},
        )

    def test_api_key_set_requires_key_or_session(
        self, client_with_settings, db_conn
    ) -> None:
        client = client_with_settings(api_key="secret-a")
        assert client.get("/conversations").status_code == 401
        assert (
            client.get("/conversations", headers={"X-API-Key": "secret-a"}).status_code
            == 200
        )

    def test_logged_in_user_works_alongside_api_key(
        self, client_with_settings, db_conn
    ) -> None:
        client = client_with_settings(api_key="secret-a")
        assert register(client, "browser@local.dev").status_code == 201
        # No key header: the session cookie authenticates.
        assert client.get("/auth/me").status_code == 200
        assert client.get("/conversations").status_code == 200

    def test_me_is_401_for_key_requests(self, client_with_settings, db_conn) -> None:
        client = client_with_settings(api_key="secret-a")
        assert (
            client.get("/auth/me", headers={"X-API-Key": "secret-a"}).status_code
            == 401
        )


class TestScopeKwargs:
    def test_session_actor_scopes_by_user(self) -> None:
        actor = AuthActor(
            mode="session", role="user", api_key_hash=None, user_id=uuid.uuid4()
        )
        kwargs = scope_kwargs(actor)
        assert kwargs["owner_user_id"] == actor.user_id
        assert kwargs["api_key_hash"] is None

    def test_key_and_local_actors_scope_by_fingerprint(self) -> None:
        key_actor = AuthActor(
            mode="api_key", role="admin", api_key_hash="f" * 64, user_id=None
        )
        assert scope_kwargs(key_actor) == {
            "api_key_hash": "f" * 64,
            "owner_user_id": None,
        }
        local_actor = AuthActor(mode="local", role="local", api_key_hash=None, user_id=None)
        assert scope_kwargs(local_actor) == {
            "api_key_hash": None,
            "owner_user_id": None,
        }


class TestCreateAdminScript:
    def test_script_creates_and_promotes_admins(self, db_conn) -> None:
        from scripts.create_admin import main as create_admin_main
        import getpass as getpass_module

        from unittest.mock import patch

        email = "scripted@local.dev"
        with patch.object(getpass_module, "getpass", return_value=PASSWORD):
            with patch("sys.argv", ["create_admin.py", email]):
                assert create_admin_main() == 0
        session = _db()
        try:
            user = repositories.get_user_by_email(session, email)
            assert user is not None
            assert user.role == "admin"
            assert user.password_hash
        finally:
            session.close()
        # Second run promotes instead of failing.
        with patch("sys.argv", ["create_admin.py", email]):
            assert create_admin_main() == 0
        session = _db()
        try:
            assert (
                repositories.get_user_by_email(session, email).role == "admin"
            )
        finally:
            session.close()
