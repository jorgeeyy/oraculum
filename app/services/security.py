"""Request-level hardening (WS-4): CSRF rules, body-size cap, security headers.

CSRF model for this app: the only ambient credential a browser attaches on
its own is the session cookie (SameSite=Lax). The X-API-Key header is never
ambient - a cross-origin attacker cannot set a custom header without a CORS
preflight this server would refuse - so key callers (scripts, Swagger with a
key, evals) are exempt from CSRF rules entirely and stay byte-compatible.

Two independent defenses apply to every state-changing request:

1. A foreign ``Origin`` is rejected outright. Browsers attach Origin to
   every cross-site write (forms, fetch, images with bodies), so this alone
   kills form-based CSRF - including login CSRF - even when no cookie is
   involved. Same-origin requests (Swagger served by this app on its own
   port) and the configured frontend origins pass.
2. A mutating request that carries our session cookie must also carry the
   custom ``X-Requested-With`` header. Cross-site requests cannot forge it
   without a refused preflight, so its presence proves the request came
   from our own JavaScript.

The SPA (dev server :5173) reaches the API same-origin through the Vite
proxy, so browser-side nothing changes: the fetch wrapper sends
X-Requested-With on every call and the Origin header matches the configured
frontend origins.
"""

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import Settings, get_settings

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_CSRF_HEADER = "X-Requested-With"

# Baseline CSP for this origin. It must keep Swagger UI working (FastAPI's
# /docs loads swagger-ui-dist from cdn.jsdelivr.net plus one inline script)
# and must not break the SPA's PDF previews, which iframe
# /documents/{id}/file through the proxy - hence frame-ancestors 'self'
# (and X-Frame-Options: SAMEORIGIN) instead of DENY: framing is only ever
# done by our own frontend origin. JSON and SSE responses ignore CSP.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)


def harden_request(
    request: Request, settings: Settings = Depends(get_settings)
) -> None:
    """CSRF + payload-size gate for state-changing requests.

    Applied as a router-level dependency (before authentication), so the
    rejections are deterministic and cheap. Order of the checks:

    1. Declared bodies above the configured cap are refused with 413.
       Streaming uploads without a declared length are bounded separately
       while being read (``_read_bounded`` in the documents API).
    2. A foreign Origin on a mutating method is always rejected (see
       module docstring).
    3. API-key callers pass: the key is not an ambient credential.
    4. Cookie-carrying mutations must include X-Requested-With.

    Reads (GET/HEAD/OPTIONS) and the health endpoints pass untouched.
    """
    if request.method not in _MUTATING_METHODS:
        return

    length = request.headers.get("content-length")
    if length is not None:
        try:
            too_large = int(length) > settings.max_request_body_bytes
        except ValueError:
            too_large = True  # malformed header: refuse rather than guess
        if too_large:
            raise HTTPException(status_code=413, detail="Request body too large.")

    origin = request.headers.get("Origin")
    if origin is not None and not _origin_allowed(origin, request, settings):
        raise HTTPException(
            status_code=403, detail="Cross-origin request rejected."
        )

    if request.headers.get("X-API-Key"):
        return  # script caller: no ambient credential, nothing to forge

    if settings.auth_cookie_name not in request.cookies:
        return  # anonymous/local request: no session cookie to ride

    if not request.headers.get(_CSRF_HEADER):
        raise HTTPException(
            status_code=403,
            detail=f"Missing {_CSRF_HEADER} header.",
        )


def _origin_allowed(origin: str, request: Request, settings: Settings) -> bool:
    if origin == "null":  # sandboxed iframe / file:// - never trusted
        return False
    if origin in settings.cors_origins:
        return True
    # Same-origin from another host spelling (e.g. Swagger served by this
    # app itself: Origin http://localhost:8000 against Host localhost:8000).
    netloc = urlsplit(origin).netloc
    return bool(netloc) and netloc == request.headers.get("host")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security headers on every response, SSE streams included."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        # SAMEORIGIN (not DENY): the SPA iframes /documents/{id}/file for
        # PDF previews - through the proxy the browser sees that as
        # same-origin. Cross-site framing stays blocked.
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        return response
