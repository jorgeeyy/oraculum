import logging
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine
from app.services.errors import DatabaseNotConfiguredError

logger = logging.getLogger(__name__)


def open_db_session() -> Session:
    """Open a session now, mapping config/driver failures to HTTP 503.

    Connection is attempted eagerly so an unreachable database produces a
    clean 503 instead of a 500 mid-request. Driver details are logged,
    never returned to the client.
    """
    try:
        session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
        session.connection()
        return session
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.exception("Database connection failed")
        raise HTTPException(
            status_code=503,
            detail="Database is unavailable. Check DATABASE_URL and the server.",
        ) from exc


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency wrapper around open_db_session."""
    session = open_db_session()
    try:
        yield session
    finally:
        session.close()
