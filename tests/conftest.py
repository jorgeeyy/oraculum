from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text

from app.api.chat import get_generator
from app.api.documents import get_embedder, get_storage, get_vector_store
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

    def query(self, vector, *, top_k: int = 5, document_id: str | None = None):
        if self.fail_query is not None:
            raise self.fail_query
        self.queries.append(
            {"vector": list(vector), "top_k": top_k, "document_id": document_id}
        )
        return self.query_result

    def count(self) -> int:
        return self.vector_count


class RecordingGenerator:
    """Stands in for the OpenAI chat generator; records calls, canned results."""

    model = "test-chat-model"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result: GenerationResult = GenerationResult(
            answer="Employees receive 20 days of annual leave.",
            status="answered",
            sources=[],
            model=self.model,
            elapsed_ms=5.0,
        )
        self.fail: Exception | None = None

    def generate(self, question, evidence):
        if self.fail is not None:
            raise self.fail
        self.calls.append({"question": question, "evidence": list(evidence)})
        return self.result


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
def client(
    tmp_path,
    fake_embedder: RecordingEmbedder,
    fake_vector_store: RecordingVectorStore,
    fake_generator: RecordingGenerator,
) -> Iterator[TestClient]:
    app = create_app()
    storage = DocumentStorage(tmp_path / "uploads")
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
    app.dependency_overrides[get_generator] = lambda: fake_generator
    app.dependency_overrides[get_rate_limiter] = lambda: _PermissiveRateLimiter()
    with TestClient(app) as test_client:
        test_client.storage = storage  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture()
def client_with_settings(
    tmp_path,
    fake_embedder: RecordingEmbedder,
    fake_vector_store: RecordingVectorStore,
    fake_generator: RecordingGenerator,
):
    """Factory: TestClient app with fake deps AND overridden Settings values."""

    def factory(**settings_overrides) -> TestClient:
        app = create_app()
        storage = DocumentStorage(tmp_path / "uploads")
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_embedder] = lambda: fake_embedder
        app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
        app.dependency_overrides[get_generator] = lambda: fake_generator
        app.dependency_overrides[get_rate_limiter] = lambda: _PermissiveRateLimiter()
        merged = get_settings().model_dump() | settings_overrides
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

    from app.db.models import ChatMessage, Conversation, Document

    with engine.connect() as conn:
        existing_ids = set(
            conn.execute(select(Document.id)).scalars()
        )
        existing_conversation_ids = set(
            conn.execute(select(Conversation.id)).scalars()
        )

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
    engine.dispose()
