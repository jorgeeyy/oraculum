"""Optional API-key authentication.

Disabled entirely when API_KEY is unset (the local-development default).
When set, every non-health endpoint requires the key via either
`Authorization: Bearer <key>` or `X-API-Key: <key>`.
"""

import secrets

from fastapi import Depends, HTTPException, Request

from app.config import Settings, get_settings


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
