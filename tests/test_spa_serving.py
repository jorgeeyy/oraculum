"""The API origin must never serve the frontend (API/UI origin split).

The backend (uvicorn, :8000) answers JSON only - the SPA lives on the dev
server (:5173) or, in production, its own origin/CDN. These tests pin the
security boundary the user asked for: reaching the API port must not hand
an attacker the UI, and no Accept-header trickery can swap a JSON answer
for an HTML shell (the old SPA-fallback middleware is gone).
"""

from fastapi.testclient import TestClient

from app.main import create_app

_HTML = {"Accept": "text/html"}


def _client() -> TestClient:
    return TestClient(create_app())


def test_root_is_not_a_serving_path() -> None:
    # No StaticFiles mount: "/" is simply an unknown API path.
    response = _client().get("/")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_browser_navigation_to_colliding_route_stays_json() -> None:
    # GET /documents is the API list endpoint. A browser-style Accept header
    # must NOT swap it for an SPA shell - the answer stays JSON (401 behind
    # auth), whatever the client claims to accept.
    response = _client().get("/documents", headers=_HTML)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")


def test_spa_deep_link_paths_are_never_html() -> None:
    # Paths that used to receive index.html (/chat, /admin, /login...) now
    # answer API semantics only: 405 on POST-only routes, 401/404 elsewhere.
    expectations = {
        "/chat": 405,  # POST /chat exists; GET is method-not-allowed
        "/admin": 404,
        "/admin/users": 401,  # real API route, auth-gated
        "/login": 404,  # the API route is POST /auth/login; /login is unknown
        "/register": 404,
    }
    for path, status in expectations.items():
        response = _client().get(path, headers=_HTML)
        assert response.status_code == status, path
        assert response.headers["content-type"].startswith(
            "application/json"
        ), path


def test_unknown_deep_link_is_a_json_404() -> None:
    # No shell fallback: unknown paths are plain API 404s.
    response = _client().get("/some/unknown/route", headers=_HTML)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_missing_asset_stays_404() -> None:
    response = _client().get("/assets/gone.js", headers=_HTML)
    assert response.status_code == 404


def test_health_is_never_replaced_by_any_shell() -> None:
    response = _client().get("/health", headers=_HTML)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
