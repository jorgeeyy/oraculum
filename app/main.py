import logging

from fastapi import Depends, FastAPI

from app.api.auth import require_api_key
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.retrieve import router as retrieve_router
from app.config import get_settings
from app.observability import RequestIdMiddleware, configure_request_logging


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
    )
    configure_request_logging()
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Retrieval-augmented generation knowledge assistant.",
        version="0.5.0",
    )
    app.add_middleware(RequestIdMiddleware)
    # Auth applies to everything that touches documents or spends money;
    # health checks stay open for load balancers and monitoring.
    app.include_router(health_router)
    app.include_router(documents_router, dependencies=[Depends(require_api_key)])
    app.include_router(retrieve_router, dependencies=[Depends(require_api_key)])
    app.include_router(chat_router, dependencies=[Depends(require_api_key)])
    return app


app = create_app()
