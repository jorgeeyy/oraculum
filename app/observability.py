"""Request-ID tracking and access logging.

Every request gets a correlation ID (from the X-Request-ID header if the
client supplied one, otherwise generated). The ID is stored in a contextvar
so any log line emitted while handling the request can include it, and it is
echoed back in the response header for end-to-end tracing.
"""

import contextvars
import logging
import logging.handlers
import time
import uuid
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

_ACCESS_LOGGER = logging.getLogger("app.access")

_EXCLUDED_PATHS = {"/health", "/health/ready", "/docs", "/openapi.json", "/redoc"}


class RequestIdFilter(logging.Filter):
    """Attach the current request ID to every record that passes through."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_request_logging() -> None:
    """Install the request-id filter and formatter on the root handler once."""
    root = logging.getLogger()
    if not root.handlers:
        return
    handler = root.handlers[0]
    if any(isinstance(f, RequestIdFilter) for f in handler.filters):
        return
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
        )
    )


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_LOG_BACKUP_COUNT = 3  # keep oraculum.log plus oraculum.log.1..3


def configure_file_logging(log_path: Path) -> None:
    """Send application logs to a rotating file in addition to stderr.

    Idempotent: creating the app again (tests) will not attach a second
    handler for the same file.
    """
    root = logging.getLogger()
    target = str(log_path)
    for handler in root.handlers:
        if (
            isinstance(handler, logging.FileHandler)
            and getattr(handler, "baseFilename", None) == target
        ):
            return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    file_handler.addFilter(RequestIdFilter())
    root.addHandler(file_handler)


class RequestIdMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process a request while tracking and returning its correlation ID. It also logs the request outcome and duration unless the path is excluded from access logging."""
        
        incoming = request.headers.get("X-Request-ID", "")
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming[:64] if incoming else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            if request.url.path not in _EXCLUDED_PATHS:
                elapsed_ms = (time.perf_counter() - started) * 1000
                _ACCESS_LOGGER.info(
                    "%s %s -> %d in %.0fms",
                    request.method,
                    request.url.path,
                    response.status_code,
                    elapsed_ms,
                )
            return response
        finally:
            request_id_var.reset(token)
