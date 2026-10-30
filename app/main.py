import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import get_actor, router as auth_router
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

# Frontend routes whose paths collide with API routes that return JSON
# (GET /documents, GET /admin/users, ...). A browser navigation to these
# paths must receive the SPA shell; fetch/curl (Accept: */*) keeps JSON.
_SPA_ROUTE_PREFIXES = ("/chat", "/documents", "/admin", "/login", "/register")


def _install_spa_fallback(app: FastAPI) -> None:
    """Serve index.html for frontend routes and unknown deep links.

    StaticFiles has no SPA fallback: refreshing /chat or /admin on the
    built frontend (served by uvicorn)404s. Two cases are handled:
    (1) a GET whose Accept header asks for HTML on a path that is also a
    frontend route - the API layer would answer JSON, browsers need the
    shell; (2) any other GET that ends in 404 - an unknown deep link
    renders the SPA's own not-found page. Non-GET requests and clients
    that do not ask for HTML (fetch defaults to Accept: */*) are never
    intercepted.
    """

    def _wants_html(request: Request) -> bool:
        return "text/html" in request.headers.get("accept", "")

    def _looks_like_asset(path: str) -> bool:
        # Real SPA routes never contain a dot in their last segment; a
        # missing /assets/*.js must stay a 404, not become the HTML shell.
        return "." in path.rsplit("/", 1)[-1]

    @app.middleware("http")
    async def spa_fallback(request: Request, call_next):
        path = request.url.path
        if request.method == "GET" and _wants_html(request):
            # Exact SPA routes + the nested admin pages (/admin/users...).
            if (
                path.rstrip("/") in _SPA_ROUTE_PREFIXES
                or path.startswith("/admin/")
            ):
                return FileResponse(_FRONTEND_DIST / "index.html")
            response = await call_next(request)
            if response.status_code == 404 and not _looks_like_asset(path):
                # Drain the downstream body so its task finishes cleanly
                # before the response is swapped for the SPA shell.
                async for _chunk in response.body_iterator:
                    pass
                return FileResponse(_FRONTEND_DIST / "index.html")
            return response
        return await call_next(request)

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


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
    )
    configure_request_logging()
    # uvicorn's built-in access logger emits a SECOND line per request
    # ("INFO: 127.0.0.1:... \"GET ...\""), including for paths we deliberately
    # exclude from logging (/health probes, external /v1/* probes) - which is
    # how /v1/models kept showing up. Our app.access lines already cover
    # every request, so mute uvicorn's duplicate at INFO (errors still pass).
    # Safe here: uvicorn's Config.configure_logging() runs before the app is
    # imported, so this level is never overwritten afterwards.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    settings = get_settings()
    if settings.log_file:
        configure_file_logging(settings.log_file_path)
    app = FastAPI(
        title=settings.app_name,
        description="Retrieval-augmented generation knowledge assistant.",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # No cookies/sessions: credentials are a static API key header.
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
    # Auth applies to everything that touches documents or spends money;
    # health checks stay open for load balancers and monitoring. The auth
    # router itself carries no gate (register/login are public; /auth/me
    # resolves its own identity).
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(documents_router, dependencies=[Depends(get_actor)])
    app.include_router(retrieve_router, dependencies=[Depends(get_actor)])
    app.include_router(chat_router, dependencies=[Depends(get_actor)])
    app.include_router(conversations_router, dependencies=[Depends(get_actor)])
    # Admin routes resolve the actor the same way, then require_admin
    # applies the role gate per route (403 for non-admin sessions).
    app.include_router(admin_router, dependencies=[Depends(get_actor)])
    # Serve the built frontend when it exists (npm run build); routes added
    # above win, so the mount only catches static asset paths and "/". The
    # fallback middleware must be installed BEFORE the mount so deep-link
    # refreshes (/chat, /admin) and unknown routes get index.html instead
    # of StaticFiles' 404.
    if (_FRONTEND_DIST / "index.html").is_file():
        _install_spa_fallback(app)
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

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
