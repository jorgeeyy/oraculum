from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete as sa_delete, text

from app.api.documents import get_storage
from app.config import get_settings
from app.db.session import normalize_database_url
from app.main import create_app
from app.services.errors import DatabaseNotConfiguredError
from app.services.storage import DocumentStorage


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    app = create_app()
    storage = DocumentStorage(tmp_path / "uploads")
    app.dependency_overrides[get_storage] = lambda: storage
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
