from collections.abc import Iterator

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text

# Keep test runs out of the real application log file (Settings reads env
# before .env, so this wins).
os.environ["LOG_FILE"] = ""

from app.api.chat import get_generator
from app.api.documents import get_embedder, get_storage, get_vector_store
from app.api.retrieve import get_bm25_index
from app.config import Settings, get_settings
from app.db.session import normalize_database_url
from app.main import create_app
from app.schemas.documents import Chunk
from app.services.errors import DatabaseNotConfiguredError
from app.services.generation import INSUFFICIENT_ANSWER, GenerationResult
from app.services.rate_limit import FixedWindowRateLimiter, get_rate_limiter
from app.services.storage import DocumentStorage


class RecordingEmbedder:
    """Stands in for the OpenAI-backed embedder; records every embed call."""

    model = "test-embedding-model"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed(self, texts) -> list[list[float]]:
        texts = list(texts)
        self.batches.append(texts)
        return [[0.25, 0.5, 0.75] for _ in texts]


class RecordingVectorStore:
    """Stands in for ChromaDB; records queries/upserts/deletes and can fail on demand."""

    def __init__(self) -> None:
        self.upserts: list[tuple[list[Chunk], list[list[float]]]] = []
        self.deletes: list[str] = []
        self.queries: list[dict] = []
        self.query_result: list[dict] = []
        self.fail_upsert: Exception | None = None
        self.fail_delete: Exception | None = None
        self.fail_query: Exception | None = None
        self.vector_count: int = 0

    def upsert_chunks(self, chunks, embeddings) -> None:
        if self.fail_upsert is not None:
            raise self.fail_upsert
        self.upserts.append((list(chunks), list(embeddings)))

    def delete_document_vectors(self, document_id: str) -> None:
        if self.fail_delete is not None:
            raise self.fail_delete
        self.deletes.append(document_id)

    def query(
        self,
        vector,
        *,
        top_k: int = 5,
        document_id: str | None = None,
        document_ids: set[str] | None = None,
    ):
        if self.fail_query is not None:
            raise self.fail_query
        self.queries.append(
            {
                "vector": list(vector),
                "top_k": top_k,
                "document_id": document_id,
                "document_ids": document_ids,
            }
        )
        return self.query_result

    def count(self) -> int:
        return self.vector_count


class RecordingBm25:
    """Stands in for the FTS5 index; records index/delete/search calls."""

    def __init__(self) -> None:
        self.indexed: list[Chunk] = []
        self.deletes: list[str] = []
        self.searches: list[dict] = []
        self.search_result: list[dict] = []
        self.fail_index: Exception | None = None
        self.fail_search: Exception | None = None
        self.fail_delete: Exception | None = None

    def index_chunks(self, chunks) -> None:
        if self.fail_index is not None:
            raise self.fail_index
        self.indexed.extend(list(chunks))

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        document_id: str | None = None,
        document_ids: set[str] | None = None,
    ):
        if self.fail_search is not None:
            raise self.fail_search
        self.searches.append(
            {
                "query": query,
                "top_k": top_k,
                "document_id": document_id,
                "document_ids": document_ids,
            }
        )
        return list(self.search_result)

    def delete_document(self, document_id: str) -> None:
        if self.fail_delete is not None:
            raise self.fail_delete
        self.deletes.append(document_id)

    def count(self) -> int:
        return len(self.indexed)

    def clear(self) -> None:
        self.indexed.clear()


class RecordingGenerator:
    """Stands in for the OpenAI chat generator; records calls, canned results."""

    model = "test-chat-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.rewrite_calls: list[dict] = []
        self.rewrite_result: str | None = None  # None = echo the raw question
        self.expansion_calls: list[dict] = []
        self.expansion_result: list[str] = []  # returned as-is (empty = no variants)
        self.result: GenerationResult = GenerationResult(
            answer="Employees receive 20 days of annual leave.",
            status="answered",
            sources=[],
            model=self.model,
            elapsed_ms=5.0,
        )
        self.fail: Exception | None = None
        # Streaming behaviour: how to split the answer into deltas, and an
        # optional exception raised after that many deltas (mid-stream failure).
        self.stream_chunks: list[str] | None = None  # None = split answer in half
        self.stream_fail_after: int | None = None
        self.stream_calls: list[dict] = []
        self.stream_events: list[tuple[str, object]] = []

    def generate(self, question, evidence, history=None):
        if self.fail is not None:
            raise self.fail
        self.calls.append(
            {"question": question, "evidence": list(evidence), "history": history}
        )
        return self.result

    def generate_stream(self, question, evidence, history=None):
        """Same contract as Generator.generate_stream: deltas, then a result."""
        if self.fail is not None:
            raise self.fail
        self.stream_calls.append(
            {"question": question, "evidence": list(evidence), "history": history}
        )
        if not evidence:  # matches the real short-circuit: no LLM involvement
            yield ("result", self.result)
            return
        answer = self.result.answer
        chunks = self.stream_chunks
        if chunks is None:
            half = max(1, len(answer) // 2)
            chunks = [answer[:half], answer[half:]]
        for index, chunk in enumerate(chunks):
            if self.stream_fail_after is not None and index >= self.stream_fail_after:
                raise self.fail or GenerationServiceError("stream broke")
            event = ("delta", chunk)
            self.stream_events.append(event)
            yield event
        event = ("result", self.result)
        self.stream_events.append(event)
        yield event

    def rewrite_query(self, question, history):
        self.rewrite_calls.append({"question": question, "history": history})
        return self.rewrite_result if self.rewrite_result is not None else question

    def expand_query(self, question, count=2):
        self.expansion_calls.append({"question": question, "count": count})
        return list(self.expansion_result)


class _PermissiveRateLimiter:
    """Lets the whole suite through; dedicated tests build real limiters."""

    def allow(self, key: str, limit: int) -> bool:
        return True


@pytest.fixture()
def fake_embedder() -> RecordingEmbedder:
    return RecordingEmbedder()


@pytest.fixture()
def fake_vector_store() -> RecordingVectorStore:
    return RecordingVectorStore()


@pytest.fixture()
def fake_generator() -> RecordingGenerator:
    return RecordingGenerator()


@pytest.fixture()
def fake_bm25() -> RecordingBm25:
    return RecordingBm25()


@pytest.fixture(autouse=True)
def _patch_ingestion_bm25(fake_bm25: RecordingBm25, monkeypatch) -> None:
    """Ingestion resolves the index itself (outside the DI graph); pin it too."""
    import app.services.ingestion as ingestion_module

    monkeypatch.setattr(ingestion_module, "get_bm25_index", lambda: fake_bm25)


@pytest.fixture()
def client(
    tmp_path,
    fake_embedder: RecordingEmbedder,
    fake_vector_store: RecordingVectorStore,
    fake_generator: RecordingGenerator,
    fake_bm25: RecordingBm25,
) -> Iterator[TestClient]:
    app = create_app()
    storage = DocumentStorage(tmp_path / "uploads")
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
    app.dependency_overrides[get_generator] = lambda: fake_generator
    app.dependency_overrides[get_bm25_index] = lambda: fake_bm25
    app.dependency_overrides[get_rate_limiter] = lambda: _PermissiveRateLimiter()
    # Same rule as the settings factory below: never inherit the semantic
    # cache flag from the developer's .env; cache tests opt in explicitly.
    # auth_allow_anonymous=True keeps the suite's pre-login request style
    # (the required-login default is covered by dedicated identity tests).
    app.dependency_overrides[get_settings] = lambda: Settings(
        chat_cache_enabled=False, auth_allow_anonymous=True
    )
    with TestClient(app) as test_client:
        test_client.storage = storage  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture()
def client_with_settings(
    tmp_path,
    fake_embedder: RecordingEmbedder,
    fake_vector_store: RecordingVectorStore,
    fake_generator: RecordingGenerator,
    fake_bm25: RecordingBm25,
):
    """Factory: TestClient app with fake deps AND overridden Settings values."""

    def factory(**settings_overrides) -> TestClient:
        app = create_app()
        storage = DocumentStorage(tmp_path / "uploads")
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_embedder] = lambda: fake_embedder
        app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
        app.dependency_overrides[get_generator] = lambda: fake_generator
        app.dependency_overrides[get_bm25_index] = lambda: fake_bm25
        app.dependency_overrides[get_rate_limiter] = lambda: _PermissiveRateLimiter()
        # The semantic cache is ALWAYS off unless a test opts in explicitly:
        # it must never be inherited from the developer's .env, or a flag
        # flipped for live use would silently change embed-batch shapes and
        # short-circuit retrieval underneath unrelated tests.
        # auth_allow_anonymous defaults to False product-wide (login
        # required); the suite pins it True here so existing tests keep
        # their pre-login request style, exactly like the cache flag —
        # dedicated identity tests cover the required-login default.
        merged = (
            get_settings().model_dump()
            | {"chat_cache_enabled": False, "auth_allow_anonymous": True}
            | settings_overrides
        )
        app.dependency_overrides[get_settings] = lambda: Settings(**merged)
        return TestClient(app)

    return factory


@pytest.fixture()
def db_conn() -> Iterator:
    url = get_settings().database_url
    if not url:
        pytest.skip("DATABASE_URL is not configured; skipping database-backed tests")
    try:
        engine = create_engine(normalize_database_url(url), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL is unreachable: {exc}")

    # The schema is managed by Alembic (uv run alembic upgrade head).
    # The development database also holds manually uploaded documents (and
    # their Chroma vectors and files) that belong to the user, so tests must
    # never wipe the table. Instead: snapshot the IDs that already exist,
    # and after the session remove only the rows the tests created.
    import uuid as uuid_module

    from sqlalchemy import select

    from app.db.models import (
        AuditEvent,
        ChatMessage,
        Conversation,
        Document,
        User,
        UserSession,
    )

    with engine.connect() as conn:
        existing_ids = set(
            conn.execute(select(Document.id)).scalars()
        )
        existing_conversation_ids = set(
            conn.execute(select(Conversation.id)).scalars()
        )
        existing_user_ids = set(conn.execute(select(User.id)).scalars())
        existing_session_ids = set(conn.execute(select(UserSession.id)).scalars())
        existing_audit_ids = set(conn.execute(select(AuditEvent.id)).scalars())

    yield engine

    with engine.begin() as conn:
        if existing_ids:
            conn.execute(
                delete(Document).where(Document.id.not_in(existing_ids))
            )
        else:
            conn.execute(delete(Document))
        if existing_conversation_ids:
            conn.execute(
                delete(ChatMessage).where(
                    ChatMessage.conversation_id.not_in(existing_conversation_ids)
                )
            )
            conn.execute(
                delete(Conversation).where(
                    Conversation.id.not_in(existing_conversation_ids)
                )
            )
        else:
            conn.execute(delete(ChatMessage))
            conn.execute(delete(Conversation))
        # Identity rows created by tests (register/login flows). Deleting a
        # user releases any conversation it adopted via ON DELETE SET NULL.
        if existing_session_ids:
            conn.execute(delete(UserSession).where(UserSession.id.not_in(existing_session_ids)))
        else:
            conn.execute(delete(UserSession))
        if existing_user_ids:
            conn.execute(delete(User).where(User.id.not_in(existing_user_ids)))
        else:
            conn.execute(delete(User))
        # Audit rows reference users with SET NULL; remove test-created ones.
        if existing_audit_ids:
            conn.execute(delete(AuditEvent).where(AuditEvent.id.not_in(existing_audit_ids)))
        else:
            conn.execute(delete(AuditEvent))
    engine.dispose()
