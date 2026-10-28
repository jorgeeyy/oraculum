"""Thumbs feedback: PUT /chat/messages/{id}/feedback.

Covers the full loop - the message_id exposed by /chat (and the stream's
`done` event) is the assistant row that feedback attaches to - plus the
ownership boundary (feedback can never probe another scope's message ids)
and the clear/toggle semantics.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from test_streaming import parse_sse

from app.services.errors import GenerationServiceError

DOC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

STORE_HIT = {
    "id": f"{DOC_ID}#0",
    "score": 0.81,
    "text": "Employees receive 20 days of paid annual leave.",
    "metadata": {
        "document_id": DOC_ID,
        "filename": "handbook.md",
        "page": 1,
        "chunk_index": 0,
    },
}


def _persisted_exchange(client: TestClient, fake_vector_store, fake_generator):
    """Run one persisted chat; return (conversation_id, message_id)."""
    fake_vector_store.query_result = [STORE_HIT]
    response = client.post(
        "/chat",
        json={"question": "How many days of annual leave?", "conversation_id": None},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conversation_id"]
    assert body["message_id"]
    return body["conversation_id"], body["message_id"]


def test_message_id_is_the_assistant_row(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    conversation_id, message_id = _persisted_exchange(
        client, fake_vector_store, fake_generator
    )
    try:
        detail = client.get(f"/conversations/{conversation_id}").json()
        by_role = {m["role"]: m["id"] for m in detail["messages"]}
        assert message_id == by_role["assistant"]
        assert message_id != by_role["user"]  # feedback targets the answer
        assert detail["messages"][1]["feedback"] is None  # starts unrated
    finally:
        client.delete(f"/conversations/{conversation_id}")


def test_set_toggle_and_clear_feedback(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    conversation_id, message_id = _persisted_exchange(
        client, fake_vector_store, fake_generator
    )
    try:
        # Thumbs up.
        up = client.put(
            f"/chat/messages/{message_id}/feedback", json={"rating": "up"}
        )
        assert up.status_code == 200, up.text
        assert up.json() == {"message_id": message_id, "feedback": "up"}

        # Visible when reading the conversation back.
        detail = client.get(f"/conversations/{conversation_id}").json()
        assert detail["messages"][1]["feedback"] == "up"

        # Toggle to thumbs down.
        down = client.put(
            f"/chat/messages/{message_id}/feedback", json={"rating": "down"}
        )
        assert down.json()["feedback"] == "down"

        # Clear (null) - an explicit "no opinion" resets the toggle.
        cleared = client.put(
            f"/chat/messages/{message_id}/feedback", json={"rating": None}
        )
        assert cleared.json()["feedback"] is None
        detail = client.get(f"/conversations/{conversation_id}").json()
        assert detail["messages"][1]["feedback"] is None
    finally:
        client.delete(f"/conversations/{conversation_id}")


def test_stateless_response_exposes_no_message_id(
    client: TestClient, fake_vector_store, fake_generator
) -> None:
    fake_vector_store.query_result = [STORE_HIT]
    body = client.post("/chat", json={"question": "How many days?"}).json()
    assert body["conversation_id"] is None
    assert body["message_id"] is None


def test_stream_done_carries_message_id(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    fake_vector_store.query_result = [STORE_HIT]
    response = client.post(
        "/chat/stream",
        json={"question": "How many days?", "conversation_id": None},
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "done"
    payload = events[-1][1]
    conversation_id = payload["conversation_id"]
    try:
        assert payload["message_id"]
        rated = client.put(
            f"/chat/messages/{payload['message_id']}/feedback",
            json={"rating": "up"},
        )
        assert rated.status_code == 200
    finally:
        client.delete(f"/conversations/{conversation_id}")


def test_unknown_message_returns_404(client: TestClient, db_conn) -> None:
    missing = str(uuid.uuid4())
    response = client.put(
        f"/chat/messages/{missing}/feedback", json={"rating": "up"}
    )
    assert response.status_code == 404


def test_invalid_message_id_returns_422(client: TestClient, db_conn) -> None:
    response = client.put(
        "/chat/messages/not-a-uuid/feedback", json={"rating": "up"}
    )
    assert response.status_code == 422


def test_invalid_rating_returns_422(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    conversation_id, message_id = _persisted_exchange(
        client, fake_vector_store, fake_generator
    )
    try:
        response = client.put(
            f"/chat/messages/{message_id}/feedback", json={"rating": "sideways"}
        )
        assert response.status_code == 422
    finally:
        client.delete(f"/conversations/{conversation_id}")


def test_user_message_cannot_carry_feedback(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    conversation_id, _ = _persisted_exchange(
        client, fake_vector_store, fake_generator
    )
    try:
        detail = client.get(f"/conversations/{conversation_id}").json()
        user_message_id = detail["messages"][0]["id"]
        assert detail["messages"][0]["role"] == "user"
        response = client.put(
            f"/chat/messages/{user_message_id}/feedback", json={"rating": "up"}
        )
        assert response.status_code == 404
    finally:
        client.delete(f"/conversations/{conversation_id}")


def test_feedback_is_scoped_to_the_owning_api_key(
    client_with_settings, db_conn, fake_vector_store, fake_generator
) -> None:
    fake_vector_store.query_result = [STORE_HIT]
    client_a = client_with_settings(api_key="secret-a")
    created = client_a.post(
        "/chat",
        json={"question": "How many days?", "conversation_id": None},
        headers={"X-API-Key": "secret-a"},
    ).json()
    message_id = created["message_id"]

    client_b = client_with_settings(api_key="secret-b")
    foreign = client_b.put(
        f"/chat/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers={"X-API-Key": "secret-b"},
    )
    assert foreign.status_code == 404  # no probing of other scopes' ids

    # The owner can still rate it.
    own = client_a.put(
        f"/chat/messages/{message_id}/feedback",
        json={"rating": "up"},
        headers={"X-API-Key": "secret-a"},
    )
    assert own.status_code == 200

    client_a.delete(
        f"/conversations/{created['conversation_id']}",
        headers={"X-API-Key": "secret-a"},
    )


def test_failed_stream_stores_nothing_to_rate(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    fake_vector_store.query_result = [STORE_HIT]
    before = len(client.get("/conversations").json())
    fake_generator.fail = GenerationServiceError("model down")
    response = client.post(
        "/chat/stream", json={"question": "hi", "conversation_id": None}
    )
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "event: done" not in response.text
    assert len(client.get("/conversations").json()) == before  # nothing stored


def test_list_feedback_returns_only_rated_answers(
    client: TestClient, db_conn, fake_vector_store, fake_generator
) -> None:
    """The export query (scripts/export_feedback.py) sees votes, not noise."""
    from app.db import repositories

    conversation_id, message_id = _persisted_exchange(
        client, fake_vector_store, fake_generator
    )
    try:
        session = sessionmaker(bind=db_conn, expire_on_commit=False)()
        try:
            # Unrated rows (both messages of the exchange) are excluded.
            assert repositories.list_feedback(session) == []

            client.put(
                f"/chat/messages/{message_id}/feedback", json={"rating": "up"}
            )
            rows = repositories.list_feedback(session)
            assert len(rows) == 1
            assert rows[0]["message_id"] == message_id
            assert rows[0]["conversation_id"] == conversation_id
            assert rows[0]["feedback"] == "up"
            assert rows[0]["answer"] == fake_generator.result.answer

            # Bucket filter for the --rating flag.
            assert repositories.list_feedback(session, rating="down") == []
            assert len(repositories.list_feedback(session, rating="up")) == 1
        finally:
            session.close()
    finally:
        client.delete(f"/conversations/{conversation_id}")
