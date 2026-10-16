import logging

from fastapi import FastAPI

from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Retrieval-augmented generation knowledge assistant.",
        version="0.2.0",
    )
    app.include_router(health_router)
    app.include_router(documents_router)
    return app


app = create_app()
