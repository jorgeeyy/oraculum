"""Request-ID tracking and access logging.

Every request gets a correlation ID (from the X-Request-ID header if the
client supplied one, otherwise generated). The ID is stored in a contextvar
so any log line emitted while handling the request can include it, and it is
echoed back in the response header for end-to-end tracing.
"""

import contextvars
import logging
import time
import uuid

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


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
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
