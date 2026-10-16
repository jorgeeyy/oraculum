from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Retrieval-augmented generation knowledge assistant.",
        version="0.1.0",
    )
    app.include_router(health_router)
    return app


app = create_app()
