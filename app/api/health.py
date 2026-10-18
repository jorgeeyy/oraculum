from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_engine
from app.services.errors import VectorStoreError
from app.services.vector_store import VectorStore, get_vector_store

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Process liveness only: the app is up, dependencies are not checked."""
    return {"status": "ok", "service": get_settings().app_name}


@router.get("/health/ready")
def ready(
    response: Response,
    store: VectorStore = Depends(get_vector_store),
) -> dict:
    """Dependency readiness: Postgres and the vector store must respond.

    OpenAI key presence is reported informationally - the app can still list
    and manage documents without it, so a missing key degrades but does not
    fail readiness.
    """
    settings = get_settings()
    dependencies: dict[str, str] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        dependencies["postgres"] = "ok"
    except Exception:
        dependencies["postgres"] = "down"

    try:
        store.count()
        dependencies["vector_store"] = "ok"
    except VectorStoreError:
        dependencies["vector_store"] = "down"

    dependencies["openai_api_key"] = "configured" if settings.openai_api_key else "missing"

    critical_ok = (
        dependencies["postgres"] == "ok" and dependencies["vector_store"] == "ok"
    )
    if not critical_ok:
        response.status_code = 503
    return {
        "status": "ok" if critical_ok else "unavailable",
        "service": settings.app_name,
        "dependencies": dependencies,
    }
