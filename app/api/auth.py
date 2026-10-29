"""Identity: API-key admin credential, session cookies, and account endpoints.

Two authentication modes share one resolver (`get_actor`):

- **API key** (the configured `API_KEY`) — the ADMIN credential. A request
  bearing it acts as the admin service account; the scope stays the key's
  fingerprint exactly as before identity existed, so curl/evals/scripts are
  byte-for-byte compatible. This path is deliberately DB-free.
- **Session cookie** — real user accounts (email + Argon2id password).
  Scoped by owning user id. When `API_KEY` is unset (local development),
  requests without a session behave exactly as before: the open, single
  local scope, no login required.

Sessions are opaque random tokens; only their SHA-256 is stored, cookies
are HttpOnly + SameSite=Lax, and login is throttled per IP and per email.
"""

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, open_db_session
from app.config import Settings, get_settings
from app.db import repositories
from app.schemas.auth import LoginRequest, RegisterRequest, UserOut
from app.services.passwords import hash_password, verify_password
from app.services.rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED = "Invalid or missing API key."


def api_key_fingerprint(settings: Settings) -> str | None:
    """Stable non-reversible scope tag for the configured API key.

    Used to partition chat history per key without storing the key itself.
    When no key is configured, None marks the single local user.
    """
    if not settings.api_key:
        return None
    return hashlib.sha256(settings.api_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthActor:
    """Who is making this request, and how to scope its data access."""

    mode: Literal["api_key", "session", "local"]
    role: Literal["admin", "user", "local"]
    api_key_hash: str | None
    user_id: uuid.UUID | None


def scope_kwargs(actor: AuthActor) -> dict:
    """Repository scope arguments for this actor.

    Session users scope by owning user id; API-key and local requests keep
    the pre-identity fingerprint scope (None = the single local scope).
    """
    if actor.mode == "session":
        return {"api_key_hash": None, "owner_user_id": actor.user_id}
    return {"api_key_hash": actor.api_key_hash, "owner_user_id": None}


def _provided_key(request: Request) -> str | None:
    provided = request.headers.get("X-API-Key")
    if not provided:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            provided = authorization[len("Bearer ") :]
    return provided


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_actor(request: Request, settings: Settings) -> AuthActor | None:
    """Resolve a valid session cookie to its user; None when absent/expired.

    Sliding renewal: past half the TTL, the expiry is extended in place.
    """
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    token_hash = _hash_token(token)
    session = open_db_session()
    try:
        row = repositories.get_session_by_token(session, token_hash)
        now = datetime.now(timezone.utc)
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            return None
        user = repositories.get_user(session, row.user_id)
        if user is None or not user.is_active:
            return None
        ttl = timedelta(days=settings.auth_session_ttl_days)
        if row.expires_at - now < ttl / 2:
            repositories.touch_session_expiry(session, row.id, now + ttl)
        return AuthActor(
            mode="session", role=user.role, api_key_hash=None, user_id=user.id
        )
    finally:
        session.close()


def get_actor(
    request: Request, settings: Settings = Depends(get_settings)
) -> AuthActor:
    """Resolve the request's identity; 401 when auth is required and absent.

    Precedence: a valid API key always wins (deterministic for scripts),
    then a session cookie, then the open local scope when no API_KEY is
    configured. With API_KEY set, neither a key nor a valid session = 401,
    so every human client must log in.
    """
    provided = _provided_key(request)
    if settings.api_key:
        if provided and secrets.compare_digest(provided, settings.api_key):
            actor = AuthActor(
                mode="api_key",
                role="admin",
                api_key_hash=api_key_fingerprint(settings),
                user_id=None,
            )
            request.state.actor = actor  # for per-user rate limiting downstream
            return actor
        if (actor := _session_actor(request, settings)) is not None:
            request.state.actor = actor
            return actor
        raise HTTPException(
            status_code=401, detail=_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
        )
    if (actor := _session_actor(request, settings)) is not None:
        request.state.actor = actor
        return actor
    actor = AuthActor(mode="local", role="local", api_key_hash=None, user_id=None)
    request.state.actor = actor
    return actor


# ----------------------------------------------------------- account API


def _user_out(user: repositories.User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _issue_session(
    session: Session, user_id: uuid.UUID, request: Request, settings: Settings
) -> str:
    """Create a session row and return the raw token for the cookie."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.auth_session_ttl_days
    )
    repositories.create_session(
        session,
        user_id=user_id,
        token_hash=_hash_token(token),
        expires_at=expires_at,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return token


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=int(timedelta(days=settings.auth_session_ttl_days).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    """Create an account; the very first one becomes the admin.

    Automatically signed in (session cookie set). When registration is
    disabled by configuration, every attempt is 403.
    """
    if not settings.auth_allow_registration:
        raise HTTPException(status_code=403, detail="Registration is disabled.")
    if repositories.get_user_by_email(session, payload.email) is not None:
        raise HTTPException(status_code=409, detail="Email is already registered.")
    first_user = repositories.count_users(session) == 0
    user_id = uuid.UUID(
        repositories.create_user(
            session,
            email=payload.email,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            role="admin" if first_user else "user",
        )
    )
    if first_user:
        # Adopt pre-identity local-scope chats so the admin sees them.
        repositories.adopt_conversations(
            session, api_key_hash=None, user_id=user_id
        )
    token = _issue_session(session, user_id, request, settings)
    _set_session_cookie(response, token, settings)
    logger.info("User registered: %s (role=%s)", payload.email, "admin" if first_user else "user")
    return _user_out(repositories.get_user(session, user_id))


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    limiter=Depends(get_rate_limiter),
) -> UserOut:
    """Verify credentials and start a session; throttled per IP and email."""
    ip = _client_ip(request)
    limit = settings.auth_login_rate_limit_per_minute
    if limit > 0:
        for throttle_key in (f"auth:ip:{ip}", f"auth:email:{payload.email}"):
            if not limiter.allow(throttle_key, limit):
                raise HTTPException(
                    status_code=429,
                    detail="Too many login attempts. Try again later.",
                    headers={"Retry-After": "60"},
                )
    user = repositories.get_user_by_email(session, payload.email)
    if (
        user is None
        or user.password_hash is None  # service accounts never log in
        or not verify_password(user.password_hash, payload.password)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled.")
    token = _issue_session(session, user.id, request, settings)
    _set_session_cookie(response, token, settings)
    repositories.touch_last_login(session, user.id)
    logger.info("User logged in: %s", payload.email)
    return _user_out(user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    """Revoke the current session; idempotent (no session = still 200)."""
    token = request.cookies.get(settings.auth_cookie_name)
    if token:
        session = open_db_session()
        try:
            repositories.revoke_session(session, _hash_token(token))
        finally:
            session.close()
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return {"logged_out": True}


@router.get("/me", response_model=UserOut)
def me(
    actor: AuthActor = Depends(get_actor),
    session: Session = Depends(get_db_session),
) -> UserOut:
    """The signed-in account; 401 for key/local/anonymous requests."""
    if actor.mode != "session" or actor.user_id is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    user = repositories.get_user(session, actor.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return _user_out(user)


def require_admin(actor: AuthActor = Depends(get_actor)) -> AuthActor:
    """Role gate for admin-only endpoints (WS-6 exposes these as /admin/*)."""
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return actor
