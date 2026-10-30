import pytest
from fastapi.testclient import TestClient

from app.main import _FRONTEND_DIST, create_app

_INDEX = _FRONTEND_DIST / "index.html"

pytestmark = pytest.mark.skipif(
    not _INDEX.is_file(),
    reason="built frontend (frontend/dist) is missing; run npm run build",
)

_HTML = {"Accept": "text/html"}


def _client() -> TestClient:
    return TestClient(create_app())


def test_browser_navigation_to_colliding_route_serves_the_spa() -> None:
    # GET /documents is an API route returning JSON - but a browser
    # navigating there (refresh/deep link) must get the app shell.
    response = _client().get("/documents", headers=_HTML)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == _INDEX.read_bytes()


def test_admin_deep_link_refresh_serves_the_spa() -> None:
    # The reported bug: clearing a session then refreshing /admin (or a
    # nested page like /admin/users)404'd on the built frontend.
    for path in ("/admin", "/admin/users", "/chat", "/login", "/register"):
        response = _client().get(path, headers=_HTML)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html"), path
        assert response.content == _INDEX.read_bytes(), path


def test_unknown_deep_link_renders_the_spa_not_found_page() -> None:
    # Unknown paths hand off to the SPA's own router (its NotFound page).
    response = _client().get("/some/unknown/route", headers=_HTML)
    assert response.status_code == 200
    assert response.content == _INDEX.read_bytes()


def test_api_clients_are_not_intercepted() -> None:
    # fetch/curl send Accept: */* - they keep API behavior (JSON, and the
    # auth gate's 401/200), never the HTML shell.
    response = _client().get("/documents")
    assert response.headers["content-type"].startswith("application/json")
    assert response.status_code in (200, 401)


def test_missing_asset_stays_404() -> None:
    # A stale tab after a rebuild must not receive HTML for a .js request.
    response = _client().get("/assets/gone.js", headers=_HTML)
    assert response.status_code == 404


def test_health_is_never_replaced_by_the_shell() -> None:
    response = _client().get("/health", headers=_HTML)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
