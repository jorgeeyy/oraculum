import hashlib

from fastapi.testclient import TestClient

from app.api.chat import get_generator
from app.api.documents import get_embedder, get_storage, get_vector_store
from app.config import Settings, get_settings
from app.main import create_app
from app.services.errors import VectorStoreError
from app.services.openai_client import clear_openai_client_cache, get_openai_client
from app.services.rate_limit import FixedWindowRateLimiter, get_rate_limiter
from app.services.storage import DocumentStorage


def _make_client(
    tmp_path,
    fake_embedder,
    fake_vector_store,
    fake_generator,
    settings_overrides: dict | None = None,
) -> TestClient:
    app = create_app()
    storage = DocumentStorage(tmp_path / "uploads")
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
    app.dependency_overrides[get_generator] = lambda: fake_generator
    if settings_overrides:
        base = get_settings()
        merged = base.model_dump() | settings_overrides
        app.dependency_overrides[get_settings] = lambda: Settings(**merged)
    # One limiter instance shared by all requests; fresh per test app so
    # window state never leaks between tests.
    limiter = FixedWindowRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    return TestClient(app)


class TestRequestId:
    def test_response_includes_generated_request_id(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID")

    def test_client_supplied_request_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers["X-Request-ID"] == "trace-abc-123"


class TestRateLimiting:
    def test_returns_429_when_limit_exhausted(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"rate_limit_per_minute": 2},
        )
        with test_client:
            for _ in range(2):
                assert client_status_ok(test_client)
            response = test_client.post("/chat", json={"question": "x"})
        assert response.status_code == 429
        assert response.headers.get("Retry-After")

    def test_zero_disables_rate_limiting(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"rate_limit_per_minute": 0},
        )
        with test_client:
            for _ in range(5):
                assert client_status_ok(test_client)

    def test_documents_endpoints_are_not_rate_limited(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"rate_limit_per_minute": 1},
        )
        with test_client:
            assert test_client.get("/documents").status_code == 200
            assert test_client.get("/documents").status_code == 200


def client_status_ok(test_client: TestClient) -> bool:
    """Drive /retrieve (cheap with fakes); True when not rate limited."""
    return test_client.post("/retrieve", json={"question": "q"}).status_code == 200


class TestApiKeyAuth:
    def test_missing_key_returns_401(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"api_key": "secret-key"},
        )
        with test_client:
            assert test_client.get("/documents").status_code == 401
            assert test_client.post("/chat", json={"question": "q"}).status_code == 401
            assert test_client.post("/retrieve", json={"question": "q"}).status_code == 401

    def test_bearer_token_is_accepted(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"api_key": "secret-key"},
        )
        with test_client:
            response = test_client.get(
                "/documents", headers={"Authorization": "Bearer secret-key"}
            )
        assert response.status_code == 200

    def test_x_api_key_header_is_accepted(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"api_key": "secret-key"},
        )
        with test_client:
            response = test_client.get(
                "/documents", headers={"X-API-Key": "secret-key"}
            )
        assert response.status_code == 200

    def test_wrong_key_returns_401(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"api_key": "secret-key"},
        )
        with test_client:
            response = test_client.get(
                "/documents", headers={"Authorization": "Bearer wrong"}
            )
        assert response.status_code == 401

    def test_health_stays_open_without_key(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        test_client = _make_client(
            tmp_path,
            fake_embedder,
            fake_vector_store,
            fake_generator,
            settings_overrides={"api_key": "secret-key"},
        )
        with test_client:
            assert test_client.get("/health").status_code == 200
            assert test_client.get("/health/ready").status_code in (200, 503)


class TestDuplicateUpload:
    def test_identical_bytes_return_existing_document(
        self,
        client: TestClient,
        fake_embedder,
        fake_vector_store,
        db_conn,
    ) -> None:
        payload = b"# Policies\n\nContractors must badge in at reception."
        first = client.post(
            "/documents",
            files={"file": ("contractor_policy.md", payload, "text/markdown")},
        )
        assert first.status_code == 201, first.text
        first_id = first.json()["id"]

        second = client.post(
            "/documents",
            files={"file": ("renamed_contractor.md", payload, "text/markdown")},
        )
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first_id
        assert second.json()["content_hash"] == hashlib.sha256(payload).hexdigest()
        # Only one ingest worth of vectors was written.
        assert len(fake_vector_store.upserts) == 1

    def test_different_bytes_create_new_document(
        self,
        client: TestClient,
        fake_embedder,
        fake_vector_store,
        db_conn,
    ) -> None:
        first = client.post(
            "/documents",
            files={"file": ("a.md", b"# A\n\nalpha", "text/markdown")},
        )
        second = client.post(
            "/documents",
            files={"file": ("b.md", b"# B\n\nbravo", "text/markdown")},
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] != second.json()["id"]


class TestHealthReady:
    def test_ready_reports_dependency_structure(self, client: TestClient) -> None:
        response = client.get("/health/ready")
        assert response.status_code in (200, 503)
        body = response.json()
        assert body["status"] in ("ok", "unavailable")
        assert set(body["dependencies"]) == {
            "postgres",
            "vector_store",
            "openai_api_key",
        }

    def test_ready_is_503_when_vector_store_down(
        self,
        tmp_path,
        fake_embedder,
        fake_vector_store,
        fake_generator,
    ) -> None:
        class _BrokenStore:
            def count(self):
                raise VectorStoreError("down")

            def query(self, *a, **k):
                raise VectorStoreError("down")

            def upsert_chunks(self, *a, **k):
                pass

            def delete_document_vectors(self, *a, **k):
                pass

        app = create_app()
        storage = DocumentStorage(tmp_path / "uploads")
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_embedder] = lambda: fake_embedder
        app.dependency_overrides[get_vector_store] = lambda: _BrokenStore()
        app.dependency_overrides[get_generator] = lambda: fake_generator
        limiter = FixedWindowRateLimiter()
        app.dependency_overrides[get_rate_limiter] = lambda: limiter
        with TestClient(app) as test_client:
            response = test_client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["dependencies"]["vector_store"] == "down"


class TestCORS:
    def test_dev_origin_is_allowed(self, client: TestClient) -> None:
        response = client.post(
            "/retrieve",
            json={"question": "q"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == (
            "http://localhost:5173"
        )
        # The request-id header is exposed so the client can trace responses.
        assert "x-request-id" in response.headers.get(
            "access-control-expose-headers", ""
        ).lower()

    def test_unknown_origin_is_not_allowed(self, client: TestClient) -> None:
        response = client.post(
            "/retrieve",
            json={"question": "q"},
            headers={"Origin": "http://evil.example.com"},
        )
        assert not response.headers.get("access-control-allow-origin")

    def test_preflight_allows_api_key_header(self, client: TestClient) -> None:
        response = client.options(
            "/chat",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-api-key",
            },
        )
        assert response.status_code == 200
        allowed = response.headers.get("access-control-allow-headers", "").lower()
        assert "x-api-key" in allowed


class TestOpenAIClientCache:
    def setup_method(self) -> None:
        clear_openai_client_cache()

    def teardown_method(self) -> None:
        clear_openai_client_cache()

    def test_same_key_and_timeout_reuse_one_client(self) -> None:
        first = get_openai_client("key-a", 30.0)
        second = get_openai_client("key-a", 30.0)
        assert first is second

    def test_different_key_gets_different_client(self) -> None:
        first = get_openai_client("key-a", 30.0)
        other = get_openai_client("key-b", 30.0)
        assert first is not other
