import uuid

import pytest
from fastapi.testclient import TestClient

from app.services.errors import EmbeddingServiceError, VectorStoreError
from app.services.retrieval import (
    EmptyQuestionError,
    recall_at_k,
    retrieve_documents,
    reciprocal_rank,
)

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


class TestRetrieveDocuments:
    def test_blank_question_rejected(self, fake_embedder, fake_vector_store) -> None:
        with pytest.raises(EmptyQuestionError):
            retrieve_documents(
                "   ", embedder=fake_embedder, store=fake_vector_store
            )

    def test_maps_store_hits_to_results(
        self, fake_embedder, fake_vector_store
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]

        outcome = retrieve_documents(
            "annual leave?", embedder=fake_embedder, store=fake_vector_store
        )

        assert len(outcome.results) == 1
        result = outcome.results[0]
        assert result.source_id == f"{DOC_ID}#0"
        assert result.document_id == DOC_ID
        assert result.filename == "handbook.md"
        assert result.page == 1
        assert result.chunk_index == 0
        assert result.score == pytest.approx(0.71)
        assert outcome.elapsed_ms >= 0

    def test_passes_top_k_and_document_filter(
        self, fake_embedder, fake_vector_store
    ) -> None:
        retrieve_documents(
            "vpn setup",
            embedder=fake_embedder,
            store=fake_vector_store,
            top_k=3,
            document_id=DOC_ID,
        )
        assert fake_vector_store.queries[0]["top_k"] == 3
        assert fake_vector_store.queries[0]["document_id"] == DOC_ID

    def test_empty_index_returns_empty_results(
        self, fake_embedder, fake_vector_store
    ) -> None:
        outcome = retrieve_documents(
            "anything", embedder=fake_embedder, store=fake_vector_store
        )
        assert outcome.results == []

    def test_embedding_failure_propagates(
        self, fake_embedder, fake_vector_store
    ) -> None:
        def boom(texts):
            raise EmbeddingServiceError("down")

        fake_embedder.embed = boom  # type: ignore[method-assign]
        with pytest.raises(EmbeddingServiceError):
            retrieve_documents(
                "question", embedder=fake_embedder, store=fake_vector_store
            )

    def test_query_failure_propagates(
        self, fake_embedder, fake_vector_store
    ) -> None:
        fake_vector_store.fail_query = VectorStoreError("locked")
        with pytest.raises(VectorStoreError):
            retrieve_documents(
                "question", embedder=fake_embedder, store=fake_vector_store
            )


class TestMetrics:
    def test_recall_full_partial_and_miss(self) -> None:
        relevant = [("a.md", 1), ("b.md", 1)]
        assert recall_at_k(relevant, [("a.md", 1), ("b.md", 1), ("c.md", 1)], 3) == 1.0
        assert recall_at_k(relevant, [("a.md", 1), ("c.md", 1)], 3) == 0.5
        assert recall_at_k(relevant, [("c.md", 1)], 3) == 0.0
        assert recall_at_k(relevant, [], 3) == 0.0

    def test_recall_respects_k(self) -> None:
        relevant = [("b.md", 1)]
        retrieved = [("a.md", 1), ("b.md", 1)]
        assert recall_at_k(relevant, retrieved, 1) == 0.0
        assert recall_at_k(relevant, retrieved, 2) == 1.0

    def test_recall_no_relevant_is_zero(self) -> None:
        assert recall_at_k([], [("a.md", 1)], 5) == 0.0

    def test_reciprocal_rank_positions(self) -> None:
        relevant = [("b.md", 1)]
        assert reciprocal_rank(relevant, [("b.md", 1), ("a.md", 1)]) == 1.0
        assert reciprocal_rank(relevant, [("a.md", 1), ("b.md", 1)]) == 0.5
        assert reciprocal_rank(relevant, [("a.md", 1)]) == 0.0
        assert reciprocal_rank([], [("a.md", 1)]) == 0.0


class TestRetrieveEndpoint:
    def test_retrieve_returns_results(
        self, client: TestClient, fake_vector_store
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]

        response = client.post(
            "/retrieve", json={"question": "How many days of annual leave?"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["question"] == "How many days of annual leave?"
        assert body["top_k"] == 5
        assert body["elapsed_ms"] >= 0
        assert len(body["results"]) == 1
        result = body["results"][0]
        assert result["document_id"] == DOC_ID
        assert result["filename"] == "handbook.md"
        assert result["score"] == pytest.approx(0.71)

    def test_filter_and_top_k_forwarded(
        self, client: TestClient, fake_vector_store
    ) -> None:
        response = client.post(
            "/retrieve",
            json={"question": "vpn", "top_k": 2, "document_id": DOC_ID},
        )
        assert response.status_code == 200
        assert fake_vector_store.queries[0]["top_k"] == 2
        assert fake_vector_store.queries[0]["document_id"] == DOC_ID

    def test_empty_index_returns_empty_list(
        self, client: TestClient, fake_vector_store
    ) -> None:
        response = client.post("/retrieve", json={"question": "anything"})
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_blank_question_returns_422(self, client: TestClient) -> None:
        response = client.post("/retrieve", json={"question": "   "})
        assert response.status_code == 422

    def test_out_of_range_top_k_returns_422(self, client: TestClient) -> None:
        response = client.post("/retrieve", json={"question": "vpn", "top_k": 0})
        assert response.status_code == 422
        response = client.post("/retrieve", json={"question": "vpn", "top_k": 21})
        assert response.status_code == 422

    def test_invalid_document_id_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/retrieve", json={"question": "vpn", "document_id": "not-a-uuid"}
        )
        assert response.status_code == 422

    def test_embedding_failure_returns_503(
        self, client: TestClient, fake_embedder
    ) -> None:
        def boom(texts):
            raise EmbeddingServiceError("rate limited")

        fake_embedder.embed = boom  # type: ignore[method-assign]
        response = client.post("/retrieve", json={"question": "vpn"})
        assert response.status_code == 503

    def test_query_failure_returns_503(
        self, client: TestClient, fake_vector_store
    ) -> None:
        fake_vector_store.fail_query = VectorStoreError("disk error")
        response = client.post("/retrieve", json={"question": "vpn"})
        assert response.status_code == 503

    def test_random_uuid_filter_returns_200_empty(
        self, client: TestClient, fake_vector_store
    ) -> None:
        response = client.post(
            "/retrieve",
            json={"question": "vpn", "document_id": str(uuid.uuid4())},
        )
        assert response.status_code == 200
        assert response.json()["results"] == []
