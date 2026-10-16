from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "rag-knowledge-assistant"


def test_docs_available() -> None:
    client = TestClient(create_app())
    response = client.get("/docs")

    assert response.status_code == 200
