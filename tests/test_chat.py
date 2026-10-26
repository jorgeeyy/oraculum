import uuid

from fastapi.testclient import TestClient

from app.schemas.chat import Source
from app.services.errors import EmbeddingServiceError, GenerationServiceError
from app.services.generation import GenerationResult

DOC_ID = "11111111-2222-3333-4444-555555555555"

STORE_HIT = {
    "id": f"{DOC_ID}#0",
    "score": 0.71,
    "text": "Employees receive 20 days of annual leave.",
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


class TestChatEndpoint:
    def test_answered_with_resolved_sources(
        self, client: TestClient, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        fake_generator.result = ANSWER_RESULT

        response = client.post(
            "/chat", json={"question": "How many days of annual leave?"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "answered"
        assert "20 days" in body["answer"]
        assert body["model"] == "test-chat-model"
        assert body["retrieval_ms"] >= 0
        assert body["generation_ms"] >= 0
        assert len(body["sources"]) == 1
        source = body["sources"][0]
        assert source["source_id"] == f"{DOC_ID}#0"
        assert source["filename"] == "handbook.md"
        assert source["page"] == 1
        assert source["excerpt"]

    def test_generator_receives_retrieved_evidence(
        self, client: TestClient, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]

        client.post("/chat", json={"question": "How many days?"})

        assert len(fake_generator.calls) == 1
        evidence = fake_generator.calls[0]["evidence"]
        assert len(evidence) == 1
        assert evidence[0].source_id == f"{DOC_ID}#0"

    def test_empty_retrieval_short_circuits_without_llm(
        self, client: TestClient, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = []

        response = client.post("/chat", json={"question": "parental leave?"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "insufficient_evidence"
        assert body["sources"] == []
        assert body["generation_ms"] == 0.0
        assert "could not find enough information" in body["answer"].lower()
        assert fake_generator.calls == []

    def test_filter_and_top_k_forwarded(
        self, client: TestClient, fake_vector_store
    ) -> None:
        client.post(
            "/chat",
            json={"question": "vpn", "top_k": 3, "document_id": DOC_ID},
        )
        assert fake_vector_store.queries[0]["top_k"] == 3
        assert fake_vector_store.queries[0]["document_id"] == DOC_ID

    def test_omitted_top_k_uses_config_default(
        self, client: TestClient, fake_vector_store
    ) -> None:
        client.post("/chat", json={"question": "vpn"})
        assert fake_vector_store.queries[0]["top_k"] == 8

    def test_config_value_changes_chat_default(
        self, client_with_settings, fake_vector_store
    ) -> None:
        with client_with_settings(retrieval_top_k=8) as test_client:
            test_client.post("/chat", json={"question": "vpn"})
        assert fake_vector_store.queries[0]["top_k"] == 8

    def test_request_top_k_beats_config_in_chat(
        self, client_with_settings, fake_vector_store
    ) -> None:
        with client_with_settings(retrieval_top_k=8) as test_client:
            test_client.post("/chat", json={"question": "vpn", "top_k": 2})
        assert fake_vector_store.queries[0]["top_k"] == 2

    def test_generator_failure_returns_503(
        self, client: TestClient, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        fake_generator.fail = GenerationServiceError("model unavailable")

        response = client.post("/chat", json={"question": "leave?"})
        assert response.status_code == 503

    def test_embedding_failure_returns_503(
        self, client: TestClient, fake_embedder
    ) -> None:
        def boom(texts):
            raise EmbeddingServiceError("down")

        fake_embedder.embed = boom  # type: ignore[method-assign]
        response = client.post("/chat", json={"question": "leave?"})
        assert response.status_code == 503

    def test_blank_question_returns_422(self, client: TestClient) -> None:
        response = client.post("/chat", json={"question": "  "})
        assert response.status_code == 422

    def test_invalid_document_id_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/chat", json={"question": "leave?", "document_id": "not-a-uuid"}
        )
        assert response.status_code == 422

    def test_random_uuid_filter_returns_insufficient(
        self, client: TestClient, fake_vector_store, fake_generator
    ) -> None:
        response = client.post(
            "/chat",
            json={"question": "leave?", "document_id": str(uuid.uuid4())},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "insufficient_evidence"
        assert fake_generator.calls == []
