from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.services.errors import DatabaseNotConfiguredError


def normalize_database_url(url: str) -> str:
    """Map plain postgres:// or postgresql:// URLs to the psycopg3 dialect.

    SQLAlchemy's default for postgresql:// is psycopg2; this project uses
    psycopg 3, so the dialect must be stated explicitly. Accepting the plain
    forms keeps the local .env simple.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


@lru_cache
def _engine_for(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine:
    """Return the application engine, or raise a clear config error."""
    if url := get_settings().database_url:
        return _engine_for(normalize_database_url(url))
    else:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not configured. Set it in .env (see .env.example)."
        )


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to the application engine."""
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
