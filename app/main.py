import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth import require_api_key
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.retrieve import router as retrieve_router
from app.config import PROJECT_ROOT, get_settings
from app.observability import (
    RequestIdMiddleware,
    configure_file_logging,
    configure_request_logging,
)
from app.services.ingestion import requeue_interrupted_ingestions

_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Finish ingestions interrupted by a restart. Runs in a daemon thread so
    # startup never blocks on embedding (and TestClient, which does not enter
    # lifespan by default, never triggers it during tests).
    threading.Thread(
        target=requeue_interrupted_ingestions, name="ingest-recovery", daemon=True
    ).start()
    yield


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
    )
    configure_request_logging()
    settings = get_settings()
    if settings.log_file:
        configure_file_logging(settings.log_file_path)
    app = FastAPI(
        title=settings.app_name,
        description="Retrieval-augmented generation knowledge assistant.",
        version="0.8.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # No cookies/sessions: credentials are a static API key header.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    # Auth applies to everything that touches documents or spends money;
    # health checks stay open for load balancers and monitoring.
    app.include_router(health_router)
    app.include_router(documents_router, dependencies=[Depends(require_api_key)])
    app.include_router(retrieve_router, dependencies=[Depends(require_api_key)])
    app.include_router(chat_router, dependencies=[Depends(require_api_key)])
    app.include_router(conversations_router, dependencies=[Depends(require_api_key)])
    # Serve the built frontend when it exists (npm run build); routes added
    # above win, so the mount only catches static asset paths and "/".
    if (_FRONTEND_DIST / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    
    @app.middleware("http")
    async def log_probe_source(request: Request, call_next):
        if request.url.path == "/v1/models":
            client_ip = (
                request.client.host
                if request.client
                else "unknown"
            )
            user_agent = request.headers.get(
                "user-agent", "unknown"
            )
            forwarded_for = request.headers.get(
                "x-forwarded-for"
            )

            logging.getLogger("app.probe").warning(
                "Probe source: client_ip=%s forwarded_for=%s "
                "method=%s path=%s user_agent=%r",
                client_ip,
                forwarded_for,
                request.method,
                request.url.path,
                user_agent,
            )

        return await call_next(request)

    return app


app = create_app()
