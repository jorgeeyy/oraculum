import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.services.errors import EmbeddingServiceError, VectorStoreError
from app.services.retrieval import (
    EmptyQuestionError,
    recall_at_k,
    retrieve_documents,
    reciprocal_rank,
)

DOC_ID = "11111111-2222-3333-4444-555555555555"


def _seed_document(document_id: str = DOC_ID) -> None:
    """Insert a real document row so document_id filtering validates.

    Unknown document ids now 404 (indistinguishable from foreign ones), so
    the filter-forwarding tests need an actual visible document. db_conn
    removes the row at teardown.
    """
    from sqlalchemy.orm import sessionmaker

    from app.db.repositories import create_document
    from app.db.session import get_engine

    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        create_document(
            session,
            document_id=document_id,
            original_filename="handbook.md",
            file_type="md",
            size_bytes=10,
        )
    finally:
        session.close()

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

    def test_empty_allowed_set_short_circuits_without_work(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        # A caller with zero visible documents must not embed, query the
        # vector store, or touch the term index at all.
        outcome = retrieve_documents(
            "annual leave?",
            embedder=fake_embedder,
            store=fake_vector_store,
            bm25=fake_bm25,
            hybrid=True,
            allowed_document_ids=set(),
        )
        assert outcome.results == []
        assert fake_embedder.batches == []
        assert fake_vector_store.queries == []
        assert fake_bm25.searches == []

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
        assert body["top_k"] == 8
        assert body["elapsed_ms"] >= 0
        assert len(body["results"]) == 1
        result = body["results"][0]
        assert result["document_id"] == DOC_ID
        assert result["filename"] == "handbook.md"
        # Hybrid mode reports the fused RRF score (vector rank 1, no BM25
        # match: 1/(60+1)), not raw cosine similarity.
        assert result["score"] == pytest.approx(1 / 61)

    def test_filter_and_top_k_forwarded(
        self, client: TestClient, fake_vector_store, db_conn
    ) -> None:
        _seed_document()
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

    def test_unknown_document_id_returns_404(
        self, client: TestClient, fake_vector_store, db_conn
    ) -> None:
        # Unknown and foreign document ids are indistinguishable (no
        # existence leak) and are rejected before any embedding/index work.
        response = client.post(
            "/retrieve",
            json={"question": "vpn", "document_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404
        assert fake_vector_store.queries == []


class TestTopKResolution:
    def test_omitted_top_k_uses_config_default(
        self, client: TestClient, fake_vector_store
    ) -> None:
        response = client.post("/retrieve", json={"question": "vpn"})
        assert response.status_code == 200
        assert fake_vector_store.queries[0]["top_k"] == 8
        assert response.json()["top_k"] == 8

    def test_config_value_changes_server_default(
        self, client_with_settings, fake_vector_store
    ) -> None:
        with client_with_settings(retrieval_top_k=10) as test_client:
            response = test_client.post("/retrieve", json={"question": "vpn"})
        assert response.status_code == 200
        assert fake_vector_store.queries[0]["top_k"] == 10
        # Response reports the effective value actually used.
        assert response.json()["top_k"] == 10

    def test_request_value_beats_config(
        self, client_with_settings, fake_vector_store
    ) -> None:
        with client_with_settings(retrieval_top_k=10) as test_client:
            response = test_client.post(
                "/retrieve", json={"question": "vpn", "top_k": 3}
            )
        assert response.status_code == 200
        assert fake_vector_store.queries[0]["top_k"] == 3
        assert response.json()["top_k"] == 3

    def test_settings_reject_out_of_range_default(self) -> None:
        with pytest.raises(ValidationError):
            Settings(retrieval_top_k=999)
        with pytest.raises(ValidationError):
            Settings(retrieval_top_k=0)
