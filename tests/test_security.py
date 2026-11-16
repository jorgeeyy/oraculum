"""WS-4 security hardening: CSRF matrix, headers, cookie flags, audit rows.

The CSRF rule (app/services/security.py): a mutating request that carries
the session cookie must also carry X-Requested-With; a foreign Origin is
always rejected; API-key callers are exempt (the key is not an ambient
credential). The suite's standard clients send the header by default
(mirroring the SPA), so these tests build their own header-less clients.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_generator
from app.api.documents import get_embedder, get_storage, get_vector_store
from app.api.retrieve import get_bm25_index
from app.config import Settings, get_settings
from app.main import create_app
from app.services.rate_limit import FixedWindowRateLimiter, get_rate_limiter
from app.services.storage import DocumentStorage

_CSRF = {"X-Requested-With": "XMLHttpRequest"}
_QUESTION = {"question": "How many vacation days?"}


def _raw_client_factory(
    tmp_path, fake_embedder, fake_vector_store, fake_generator, fake_bm25
):
    """Like conftest's client_with_settings, but WITHOUT the default CSRF
    header - the matrix must control the header per request."""

    def factory(**settings_overrides) -> TestClient:
        app = create_app()
        storage = DocumentStorage(tmp_path / "uploads")
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_embedder] = lambda: fake_embedder
        app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
        app.dependency_overrides[get_generator] = lambda: fake_generator
        app.dependency_overrides[get_bm25_index] = lambda: fake_bm25
        app.dependency_overrides[get_rate_limiter] = lambda: FixedWindowRateLimiter()
        merged = (
            get_settings().model_dump()
            | {
                "chat_cache_enabled": False,
                "auth_allow_anonymous": True,
                "usage_ledger_enabled": False,
                "usage_daily_budget_usd": None,
                "usage_monthly_budget_usd": None,
            }
            | settings_overrides
        )
        app.dependency_overrides[get_settings] = lambda: Settings(**merged)
        return TestClient(app)

    return factory


@pytest.fixture()
def raw_client(tmp_path, fake_embedder, fake_vector_store, fake_generator, fake_bm25):
    return _raw_client_factory(
        tmp_path, fake_embedder, fake_vector_store, fake_generator, fake_bm25
    )


class TestCsrfMatrix:
    def test_cookie_mutation_without_header_is_rejected(self, raw_client) -> None:
        client = raw_client()
        client.cookies.set("session", "whatever")  # presence is what matters
        response = client.post("/chat", json=_QUESTION)
        assert response.status_code == 403
        assert "X-Requested-With" in response.json()["detail"]

    def test_cookie_mutation_with_header_passes_csrf(self, raw_client) -> None:
        client = raw_client()
        client.cookies.set("session", "whatever")
        # Not 403: the gate let it through (the bogus cookie resolves to
        # the anonymous local actor in this fixture's settings).
        response = client.post("/chat", json=_QUESTION, headers=_CSRF)
        assert response.status_code == 200

    def test_foreign_origin_mutation_is_rejected_even_without_cookie(
        self, raw_client
    ) -> None:
        client = raw_client()
        response = client.post(
            "/chat", json=_QUESTION, headers={"Origin": "https://evil.example"}
        )
        assert response.status_code == 403
        assert "Cross-origin" in response.json()["detail"]

    def test_sandboxed_null_origin_is_rejected(self, raw_client) -> None:
        response = raw_client().post(
            "/chat", json=_QUESTION, headers={"Origin": "null"}
        )
        assert response.status_code == 403

    def test_configured_frontend_origin_is_allowed(self, raw_client) -> None:
        response = raw_client().post(
            "/chat",
            json=_QUESTION,
            headers={"Origin": "http://localhost:5173", **_CSRF},
        )
        assert response.status_code != 403

    def test_same_origin_is_allowed(self, raw_client) -> None:
        # Swagger served by this app posts to itself (Origin == Host).
        response = raw_client().post(
            "/chat",
            json=_QUESTION,
            headers={"Origin": "http://testserver", **_CSRF},
        )
        assert response.status_code != 403

    def test_api_key_mutation_needs_no_header(self, raw_client) -> None:
        # Scripts/curl: the key is not ambient, so no CSRF rules apply.
        client = raw_client(api_key="sekret-key")
        response = client.post(
            "/chat", json=_QUESTION, headers={"X-API-Key": "sekret-key"}
        )
        assert response.status_code == 200

    def test_get_with_cookie_needs_no_header(self, raw_client) -> None:
        client = raw_client()
        client.cookies.set("session", "whatever")
        response = client.get("/documents")
        assert response.status_code != 403  # GETs are CSRF-exempt

    def test_anonymous_mutation_without_cookie_passes(self, raw_client) -> None:
        # Local/anonymous mode has no ambient credential to ride.
        response = raw_client().post("/chat", json=_QUESTION)
        assert response.status_code == 200

    def test_logout_without_header_is_rejected(self, raw_client) -> None:
        client = raw_client()
        client.cookies.set("session", "whatever")
        assert client.post("/auth/logout").status_code == 403
        assert client.post("/auth/logout", headers=_CSRF).status_code == 200


class TestSecurityHeaders:
    def test_baseline_headers_on_success(self, client) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert (
            response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        )
        csp = response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'self'" in csp
        assert "default-src 'self'" in csp

    def test_headers_on_error_responses_too(self, client) -> None:
        response = client.get("/does-not-exist")
        assert response.status_code == 404
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Content-Security-Policy"]


class TestCookieFlags:
    def test_session_cookie_carries_all_flags(self, client, db_conn) -> None:
        email = f"flags-{uuid.uuid4().hex[:10]}@local.dev"
        response = client.post(
            "/auth/register", json={"email": email, "password": "correct-horse"}
        )
        assert response.status_code == 201
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "path=/" in cookie
        assert "secure" not in cookie  # dev (http://localhost) default

    def test_secure_flag_when_enabled(self, client_with_settings, db_conn) -> None:
        client = client_with_settings(auth_cookie_secure=True)
        email = f"flags-sec-{uuid.uuid4().hex[:10]}@local.dev"
        response = client.post(
            "/auth/register", json={"email": email, "password": "correct-horse"}
        )
        assert response.status_code == 201
        assert "secure" in response.headers["set-cookie"].lower()


class TestBodySizeCap:
    def test_oversized_declared_body_is_413(self, client_with_settings) -> None:
        client = client_with_settings(max_request_body_bytes=32)
        response = client.post("/chat", json={"question": "x" * 200})
        assert response.status_code == 413
        assert response.json()["detail"] == "Request body too large."

    def test_body_under_cap_passes(self, client_with_settings) -> None:
        client = client_with_settings(max_request_body_bytes=10_000)
        response = client.post("/chat", json=_QUESTION)
        assert response.status_code == 200


class TestDocsExposure:
    def test_docs_disabled_hides_the_api_surface(self) -> None:
        app = create_app(Settings(docs_enabled=False))
        client = TestClient(app)
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        # The API itself is unaffected.
        assert client.get("/health").status_code == 200

    def test_docs_enabled_by_default(self, client) -> None:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


class TestAuditRows:
    """Every security-relevant auth action leaves one audit event."""

    @staticmethod
    def _actions(db_conn, target_id: str) -> list[str]:
        from sqlalchemy import select

        from app.db.models import AuditEvent

        with db_conn.connect() as conn:
            rows = conn.execute(
                select(AuditEvent.action)
                .where(AuditEvent.target_id == target_id)
                .order_by(AuditEvent.id)
            ).scalars()
            return list(rows)

    def test_login_failure_and_success_are_audited(
        self, client_with_settings, db_conn
    ) -> None:
        email = f"audit-login-{uuid.uuid4().hex[:10]}@local.dev"
        client = client_with_settings()
        me = client.post(
            "/auth/register", json={"email": email, "password": "correct-horse"}
        ).json()
        client.post("/auth/logout")

        # Failures are audited by email (no user resolved yet)...
        assert (
            client.post(
                "/auth/login", json={"email": email, "password": "wrong-thing"}
            ).status_code
            == 401
        )
        assert "auth.login_failed" in self._actions(db_conn, email)

        # ...success by the resolved user id.
        assert (
            client.post(
                "/auth/login", json={"email": email, "password": "correct-horse"}
            ).status_code
            == 200
        )
        actions = self._actions(db_conn, me["id"])
        assert "auth.login" in actions

    def test_logout_is_audited_with_the_user_id(
        self, client_with_settings, db_conn
    ) -> None:
        email = f"audit-logout-{uuid.uuid4().hex[:10]}@local.dev"
        client = client_with_settings()
        me = client.post(
            "/auth/register", json={"email": email, "password": "correct-horse"}
        ).json()
        client.post("/auth/logout")

        actions = self._actions(db_conn, me["id"])
        assert "auth.register" in actions
        assert "auth.logout" in actions

    def test_document_delete_is_audited(self, client, db_conn) -> None:
        from sqlalchemy.orm import sessionmaker

        from app.db import repositories
        from app.db.session import get_engine

        # Canonical (dashed) UUID: the route parses ids into this form and
        # the audit row stores exactly what it deleted.
        document_id = str(uuid.uuid4())
        session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
        try:
            repositories.create_document(
                session,
                document_id=document_id,
                original_filename="audit-me.txt",
                file_type="txt",
                size_bytes=10,
            )
        finally:
            session.close()

        response = client.delete(f"/documents/{document_id}")
        assert response.status_code == 200

        actions = self._actions(db_conn, document_id)
        assert "document.delete" in actions


class TestNoCredentialsInLogs:
    def test_password_never_reaches_the_logs(self, client, caplog) -> None:
        import logging

        secret = f"pw-{uuid.uuid4().hex}"
        with caplog.at_level(logging.DEBUG):
            client.post(
                "/auth/login",
                json={"email": "nobody-at-all@local.dev", "password": secret},
            )
        assert secret not in caplog.text

    def test_api_key_never_reaches_the_logs(
        self, client_with_settings, caplog
    ) -> None:
        import logging

        secret_key = f"key-{uuid.uuid4().hex}"
        client = client_with_settings(api_key=secret_key)
        with caplog.at_level(logging.DEBUG):
            client.post(
                "/chat",
                json=_QUESTION,
                headers={"X-API-Key": secret_key, **_CSRF},
            )
        assert secret_key not in caplog.text
