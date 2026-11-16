import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.auth import get_actor, router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.retrieve import router as retrieve_router
from app.api.usage import router as usage_router
from app.config import Settings, get_settings
from app.observability import (
    RequestIdMiddleware,
    configure_file_logging,
    configure_request_logging,
)
from app.services.ingestion import requeue_interrupted_ingestions
from app.services.security import SecurityHeadersMiddleware, harden_request

# Single source of truth for the API/eval-reported version.
APP_VERSION = "0.8.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Finish ingestions interrupted by a restart. Runs in a daemon thread so
    # startup never blocks on embedding (and TestClient, which does not enter
    # lifespan by default, never triggers it during tests).
    threading.Thread(
        target=requeue_interrupted_ingestions, name="ingest-recovery", daemon=True
    ).start()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
    )
    configure_request_logging()
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    settings = settings or get_settings()
    if settings.log_file:
        configure_file_logging(settings.log_file_path)
    app = FastAPI(
        title=settings.app_name,
        description="Retrieval-augmented generation knowledge assistant.",
        version=APP_VERSION,
        lifespan=lifespan,
        # API surface not advertised in production (DOCS_ENABLED=false).
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Stays false on purpose: the SPA reaches the API same-origin
        # through the Vite proxy, so no cross-origin request ever needs to
        # ride the session cookie (and none may - that is the CSRF model).
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-API-Key",
            "Authorization",
            "X-Request-ID",
            "X-Requested-With",
        ],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(health_router)
    # harden_request runs before get_actor: CSRF/body-cap rejections are
    # deterministic and never depend on resolving an identity first.
    app.include_router(auth_router, dependencies=[Depends(harden_request)])
    app.include_router(
        documents_router, dependencies=[Depends(harden_request), Depends(get_actor)]
    )
    app.include_router(
        retrieve_router, dependencies=[Depends(harden_request), Depends(get_actor)]
    )
    app.include_router(
        chat_router, dependencies=[Depends(harden_request), Depends(get_actor)]
    )
    app.include_router(
        usage_router, dependencies=[Depends(harden_request), Depends(get_actor)]
    )
    app.include_router(
        conversations_router,
        dependencies=[Depends(harden_request), Depends(get_actor)],
    )
    app.include_router(
        admin_router, dependencies=[Depends(harden_request), Depends(get_actor)]
    )

    # Diagnostic middleware for who's probing /v1/models (answer: the
    # OpenCode CLI, user_agent='opencode/latest/2.1.4/cli'). The probes are
    # external and expected; logging them forever was pure noise. The 404
    # they trigger is excluded from access logging too (see observability).
    # Re-enable by uncommenting the block below (the `Request` import above
    # is kept solely so this works as-is).
    #
    # @app.middleware("http")
    # async def log_probe_source(request: Request, call_next):
    #     if request.url.path == "/v1/models":
    #         client_ip = (
    #             request.client.host
    #             if request.client
    #             else "unknown"
    #         )
    #         user_agent = request.headers.get(
    #             "user-agent", "unknown"
    #         )
    #         forwarded_for = request.headers.get(
    #             "x-forwarded-for"
    #         )
    #
    #         logging.getLogger("app.probe").warning(
    #             "Probe source: client_ip=%s forwarded_for=%s "
    #             "method=%s path=%s user_agent=%r",
    #             client_ip,
    #             forwarded_for,
    #             request.method,
    #             request.url.path,
    #             user_agent,
    #         )
    #
    #     return await call_next(request)

    return app


app = create_app()
