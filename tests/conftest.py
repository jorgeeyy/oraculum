from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete as sa_delete, text

from app.api.documents import get_embedder, get_storage, get_vector_store
from app.config import get_settings
from app.db.session import normalize_database_url
from app.main import create_app
from app.schemas.documents import Chunk
from app.services.errors import DatabaseNotConfiguredError
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
    """Stands in for ChromaDB; records upserts/deletes and can be told to fail."""

    def __init__(self) -> None:
        self.upserts: list[tuple[list[Chunk], list[list[float]]]] = []
        self.deletes: list[str] = []
        self.fail_upsert: Exception | None = None
        self.fail_delete: Exception | None = None

    def upsert_chunks(self, chunks, embeddings) -> None:
        if self.fail_upsert is not None:
            raise self.fail_upsert
        self.upserts.append((list(chunks), list(embeddings)))

    def delete_document_vectors(self, document_id: str) -> None:
        if self.fail_delete is not None:
            raise self.fail_delete
        self.deletes.append(document_id)


@pytest.fixture()
def fake_embedder() -> RecordingEmbedder:
    return RecordingEmbedder()


@pytest.fixture()
def fake_vector_store() -> RecordingVectorStore:
    return RecordingVectorStore()


@pytest.fixture()
def client(
    tmp_path, fake_embedder: RecordingEmbedder, fake_vector_store: RecordingVectorStore
) -> Iterator[TestClient]:
    app = create_app()
    storage = DocumentStorage(tmp_path / "uploads")
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
    app.dependency_overrides[get_vector_store] = lambda: fake_vector_store
    with TestClient(app) as test_client:
        test_client.storage = storage  # type: ignore[attr-defined]
        yield test_client


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
    # Tests only need an empty documents table, so wipe rows - never
    # drop the table, which would desync Alembic's version tracking.
    from app.db.models import Document

    with engine.begin() as conn:
        conn.execute(sa_delete(Document))
    yield engine
    engine.dispose()
