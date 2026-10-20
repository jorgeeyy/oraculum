import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.chat import _conversation_title
from app.db import repositories
from app.schemas.chat import Source
from app.services.errors import GenerationServiceError
from app.services.generation import GenerationResult

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


ANSWER_RESULT = GenerationResult(
    answer="Employees receive 20 days of paid annual leave per year.",
    status="answered",
    sources=[
        Source(
            source_id=f"{DOC_ID}#0",
            document_id=DOC_ID,
            filename="handbook.md",
            page=1,
            chunk_index=0,
            excerpt="Employees receive 20 days of annual leave.",
        )
    ],
    model="test-chat-model",
    elapsed_ms=12.0,
)


def chat(client: TestClient, payload: dict, **kwargs):
    return client.post("/chat", json=payload, **kwargs)


class TestChatPersistence:
    def test_first_message_creates_conversation(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        fake_generator.result = ANSWER_RESULT

        response = chat(
            client,
            {
                "question": "How many days of annual leave do employees get?",
                "conversation_id": None,
            },
        )
        assert response.status_code == 200, response.text
        conversation_id = response.json()["conversation_id"]
        assert conversation_id

        listed = client.get("/conversations").json()
        match = [c for c in listed if c["id"] == conversation_id]
        assert len(match) == 1
        assert match[0]["title"].startswith("How many days of annual leave")
        assert match[0]["message_count"] == 2

        detail = client.get(f"/conversations/{conversation_id}").json()
        assert len(detail["messages"]) == 2
        user_msg, assistant_msg = detail["messages"]
        assert user_msg["role"] == "user"
        assert user_msg["content"].startswith("How many days")
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["status"] == "answered"
        assert assistant_msg["model"] == "test-chat-model"
        assert assistant_msg["retrieval_ms"] >= 0
        assert len(assistant_msg["sources"]) == 1
        assert assistant_msg["sources"][0]["filename"] == "handbook.md"

        deleted = client.delete(f"/conversations/{conversation_id}")
        assert deleted.status_code == 200

    def test_follow_up_appends_to_existing_conversation(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        first = chat(client, {"question": "Annual leave policy?", "conversation_id": None})
        conversation_id = first.json()["conversation_id"]

        follow_up = chat(
            client,
            {"question": "And the carry-over limit?", "conversation_id": conversation_id},
        )
        assert follow_up.status_code == 200
        assert follow_up.json()["conversation_id"] == conversation_id

        detail = client.get(f"/conversations/{conversation_id}").json()
        assert len(detail["messages"]) == 4
        assert detail["title"] == "Annual leave policy?"
        client.delete(f"/conversations/{conversation_id}")

    def test_omitted_conversation_id_stays_stateless(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        before = len(client.get("/conversations").json())

        response = chat(client, {"question": "Annual leave?"})
        assert response.status_code == 200
        assert response.json()["conversation_id"] is None

        assert len(client.get("/conversations").json()) == before

    def test_unknown_conversation_id_returns_404(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        response = chat(
            client,
            {"question": "hello", "conversation_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404

    def test_invalid_conversation_id_returns_422(
        self, client: TestClient, db_conn
    ) -> None:
        response = chat(client, {"question": "hello", "conversation_id": "not-a-uuid"})
        assert response.status_code == 422

    def test_failed_generation_persists_nothing(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        fake_generator.fail = GenerationServiceError("model unavailable")
        before = len(client.get("/conversations").json())

        response = chat(client, {"question": "Annual leave?", "conversation_id": None})
        assert response.status_code == 503
        assert len(client.get("/conversations").json()) == before

    def test_insufficient_evidence_exchange_is_persisted(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = []
        response = chat(client, {"question": "Unknown topic?", "conversation_id": None})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "insufficient_evidence"
        conversation_id = body["conversation_id"]
        assert conversation_id

        detail = client.get(f"/conversations/{conversation_id}").json()
        assert len(detail["messages"]) == 2
        assert detail["messages"][1]["status"] == "insufficient_evidence"
        client.delete(f"/conversations/{conversation_id}")


class TestConversationEndpoints:
    def test_rename_and_list(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        conversation_id = chat(
            client, {"question": "First question?", "conversation_id": None}
        ).json()["conversation_id"]

        renamed = client.patch(
            f"/conversations/{conversation_id}", json={"title": "  Leave rules  "}
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Leave rules"

        listed = client.get("/conversations").json()
        assert any(
            c["id"] == conversation_id and c["title"] == "Leave rules" for c in listed
        )

        missing = client.patch(f"/conversations/{uuid.uuid4()}", json={"title": "nope"})
        assert missing.status_code == 404

        blank = client.patch(f"/conversations/{conversation_id}", json={"title": "   "})
        assert blank.status_code == 422

        client.delete(f"/conversations/{conversation_id}")

    def test_delete_removes_messages(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        conversation_id = chat(
            client, {"question": "Delete me?", "conversation_id": None}
        ).json()["conversation_id"]

        assert client.delete(f"/conversations/{conversation_id}").status_code == 200
        assert client.get(f"/conversations/{conversation_id}").status_code == 404
        assert client.delete(f"/conversations/{conversation_id}").status_code == 404

        with sessionmaker(bind=db_conn)() as session:
            remaining = repositories.get_conversation(
                session, conversation_id, api_key_hash=None
            )
            assert remaining is None

    def test_list_orders_by_recent_activity(
        self, client: TestClient, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        first_id = chat(
            client, {"question": "First question?", "conversation_id": None}
        ).json()["conversation_id"]
        second_id = chat(
            client, {"question": "Second question?", "conversation_id": None}
        ).json()["conversation_id"]

        listed = client.get("/conversations").json()
        ids = [c["id"] for c in listed]
        assert ids.index(second_id) < ids.index(first_id)

        client.delete(f"/conversations/{first_id}")
        client.delete(f"/conversations/{second_id}")


class TestScopeIsolation:
    def test_history_is_partitioned_per_api_key(
        self, client_with_settings, db_conn, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]

        client_a = client_with_settings(api_key="secret-a")
        response = chat(
            client_a,
            {"question": "Private question A?", "conversation_id": None},
            headers={"X-API-Key": "secret-a"},
        )
        assert response.status_code == 200, response.text
        conversation_id = response.json()["conversation_id"]

        client_b = client_with_settings(api_key="secret-b")
        listed = client_b.get("/conversations", headers={"X-API-Key": "secret-b"})
        assert listed.status_code == 200
        assert all(c["id"] != conversation_id for c in listed.json())

        assert (
            client_b.get(
                f"/conversations/{conversation_id}", headers={"X-API-Key": "secret-b"}
            ).status_code
            == 404
        )
        assert (
            client_b.delete(
                f"/conversations/{conversation_id}", headers={"X-API-Key": "secret-b"}
            ).status_code
            == 404
        )

        # The owning key still sees and can delete its own history.
        assert (
            client_a.get(
                f"/conversations/{conversation_id}", headers={"X-API-Key": "secret-a"}
            ).status_code
            == 200
        )
        assert (
            client_a.delete(
                f"/conversations/{conversation_id}", headers={"X-API-Key": "secret-a"}
            ).status_code
            == 200
        )


class TestConversationTitle:
    def test_short_question_is_used_verbatim(self) -> None:
        assert _conversation_title("Annual leave policy?") == "Annual leave policy?"

    def test_long_question_truncates_on_word_boundary(self) -> None:
        question = "What exactly happens to unused annual leave days " * 5
        title = _conversation_title(question)
        assert title.endswith(" ...")
        assert len(title) <= 64
        assert not title[:-4].endswith(" ")
