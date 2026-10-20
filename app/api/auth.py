"""Optional API-key authentication.

Disabled entirely when API_KEY is unset (the local-development default).
When set, every non-health endpoint requires the key via either
`Authorization: Bearer <key>` or `X-API-Key: <key>`.
"""

import hashlib
import secrets

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings


def api_key_fingerprint(settings: Settings) -> str | None:
    """Stable non-reversible scope tag for the configured API key.

    Used to partition chat history per key without storing the key itself.
    When no key is configured, None marks the single local user.
    """
    if not settings.api_key:
        return None
    return hashlib.sha256(settings.api_key.encode("utf-8")).hexdigest()


def require_api_key(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if not settings.api_key:
        return
    provided = request.headers.get("X-API-Key")
    if not provided:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            provided = authorization[len("Bearer ") :]
    if not provided or not secrets.compare_digest(provided, settings.api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
