"""Hybrid retrieval tests: RRF fusion, BM25 rescue scenario, and index sync.

The scenario mirrors the observed production failure: a question literally
containing a distinctive term ("principles") whose chunk sat at vector rank 6,
outside the top-k cutoff. BM25 must pull it back into the returned set.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.services.bm25_index import Bm25Error
from app.services.retrieval import retrieve_documents, reciprocal_rank_fuse

from tests.test_ingestion import _get_status, _seed_document

DOC_ID = "11111111-2222-3333-4444-555555555555"


def _vector_hit(chunk_index: int, score: float) -> dict:
    return {
        "id": f"{DOC_ID}#{chunk_index}",
        "score": score,
        "text": f"vector chunk {chunk_index}",
        "metadata": {
            "document_id": DOC_ID,
            "filename": "policy.pdf",
            "page": chunk_index + 1,
            "chunk_index": chunk_index,
        },
    }


def _bm25_hit(chunk_index: int) -> dict:
    return {
        "id": f"{DOC_ID}#{chunk_index}",
        "document_id": DOC_ID,
        "filename": "policy.pdf",
        "page": chunk_index + 1,
        "chunk_index": chunk_index,
        "text": f"the data protection principles say so ({chunk_index})",
    }


class TestReciprocalRankFusion:
    def test_found_by_both_sides_beats_single_side(self) -> None:
        vector = [_vector_hit(0, 0.9), _vector_hit(1, 0.8)]
        bm25 = [_bm25_hit(1), _bm25_hit(5)]
        fused = reciprocal_rank_fuse(vector, bm25, top_k=10)
        # Chunk #1 appears in both lists: 1/(60+2) vector + 1/(60+1) BM25
        # beats #0's lone 1/61.
        assert fused[0]["id"] == f"{DOC_ID}#1"
        assert fused[0]["score"] == pytest.approx(1 / 61 + 1 / 62)

    def test_bm25_only_hit_is_self_sufficient(self) -> None:
        """A BM25 row must yield a full payload without a vector lookup."""
        vector = [_vector_hit(0, 0.9)]
        bm25 = [_bm25_hit(5)]
        fused = reciprocal_rank_fuse(vector, bm25, top_k=10)
        rescued = next(h for h in fused if h["id"] == f"{DOC_ID}#5")
        assert rescued["text"].startswith("the data protection principles")
        assert rescued["metadata"]["document_id"] == DOC_ID
        assert rescued["metadata"]["chunk_index"] == 5
        assert rescued["metadata"]["page"] == 6

    def test_fusion_can_rescue_rank_beyond_cutoff(self) -> None:
        # Vector side only ever returns its top 5; the term hit is not there.
        vector = [_vector_hit(i, 0.9 - i * 0.001) for i in range(5)]
        bm25 = [_bm25_hit(5), _bm25_hit(2)]
        fused = reciprocal_rank_fuse(vector, bm25, top_k=5)
        ids = [h["id"] for h in fused]
        assert f"{DOC_ID}#5" in ids

    def test_inputs_are_never_mutated(self) -> None:
        vector = [_vector_hit(0, 0.9)]
        bm25 = [_bm25_hit(1)]
        reciprocal_rank_fuse(vector, bm25, top_k=10)
        assert vector[0]["score"] == 0.9  # store may hand back live objects
        assert "score" not in bm25[0]
        assert "metadata" not in bm25[0]

    def test_ties_break_deterministically(self) -> None:
        vector = [_vector_hit(3, 0.9)]
        bm25 = [_bm25_hit(7)]
        first = reciprocal_rank_fuse(vector, bm25, top_k=10)
        second = reciprocal_rank_fuse(list(vector), list(bm25), top_k=10)
        assert [h["id"] for h in first] == [h["id"] for h in second]

    def test_empty_bm25_still_fuses_vector_ranks(self) -> None:
        vector = [_vector_hit(0, 0.9), _vector_hit(1, 0.8)]
        fused = reciprocal_rank_fuse(vector, [], top_k=10)
        assert [h["score"] for h in fused] == pytest.approx([1 / 61, 1 / 62])

    def test_top_k_limits_output(self) -> None:
        vector = [_vector_hit(i, 0.9) for i in range(8)]
        assert len(reciprocal_rank_fuse(vector, [], top_k=5)) == 5


class TestHybridRetrieveDocuments:
    def test_bm25_rescues_term_hit_otherwise_cut_off(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [
            _vector_hit(i, 0.9 - i * 0.001) for i in range(5)
        ]
        fake_bm25.search_result = [_bm25_hit(5)]

        outcome = retrieve_documents(
            "Are there any data protection principles?",
            embedder=fake_embedder,
            store=fake_vector_store,
            top_k=5,
            bm25=fake_bm25,
            hybrid=True,
        )

        ids = [result.source_id for result in outcome.results]
        assert f"{DOC_ID}#5" in ids
        rescued = next(r for r in outcome.results if r.source_id == f"{DOC_ID}#5")
        assert "principles" in rescued.text

    def test_toggle_off_is_pure_vector_path(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [_vector_hit(0, 0.71)]

        outcome = retrieve_documents(
            "anything",
            embedder=fake_embedder,
            store=fake_vector_store,
            hybrid=False,
            bm25=fake_bm25,
        )

        assert fake_bm25.searches == []  # BM25 never consulted
        assert outcome.results[0].score == pytest.approx(0.71)  # raw cosine
        assert outcome.results[0].source_id == f"{DOC_ID}#0"

    def test_bm25_failure_degrades_to_vector_only(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [_vector_hit(0, 0.71), _vector_hit(1, 0.6)]
        fake_bm25.fail_search = Bm25Error("database is locked")

        outcome = retrieve_documents(
            "anything",
            embedder=fake_embedder,
            store=fake_vector_store,
            bm25=fake_bm25,
            hybrid=True,
        )

        assert [r.source_id for r in outcome.results] == [
            f"{DOC_ID}#0",
            f"{DOC_ID}#1",
        ]

    def test_query_filter_reaches_both_sides(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        retrieve_documents(
            "vpn",
            embedder=fake_embedder,
            store=fake_vector_store,
            document_id=DOC_ID,
            bm25=fake_bm25,
            hybrid=True,
        )
        assert fake_vector_store.queries[0]["document_id"] == DOC_ID
        assert fake_bm25.searches[0]["document_id"] == DOC_ID


class TestEndpointHybridBehavior:
    def test_retrieve_fuses_and_reports_fused_scores(
        self, client: TestClient, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [
            _vector_hit(i, 0.9 - i * 0.001) for i in range(5)
        ]
        fake_bm25.search_result = [_bm25_hit(5)]

        response = client.post(
            "/retrieve", json={"question": "data protection principles"}
        )

        assert response.status_code == 200, response.text
        ids = [r["source_id"] for r in response.json()["results"]]
        assert f"{DOC_ID}#5" in ids
        assert len(fake_bm25.searches) == 1

    def test_bm25_outage_returns_vector_results_not_503(
        self, client: TestClient, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [_vector_hit(0, 0.71)]
        fake_bm25.fail_search = Bm25Error("database is locked")

        response = client.post("/retrieve", json={"question": "vpn"})

        assert response.status_code == 200, response.text
        assert len(response.json()["results"]) == 1

    def test_hybrid_disabled_config_skips_bm25(
        self, client_with_settings, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [_vector_hit(0, 0.71)]
        with client_with_settings(retrieval_hybrid=False) as test_client:
            response = test_client.post("/retrieve", json={"question": "vpn"})
        assert response.status_code == 200
        assert fake_bm25.searches == []
        assert response.json()["results"][0]["score"] == pytest.approx(0.71)


class TestIndexSync:
    def test_upload_indexes_chunks_in_fts(
        self, client: TestClient, db_conn, fake_bm25
    ) -> None:
        # Unique bytes: content-hash dedup would otherwise reuse a row from
        # another test and skip ingestion entirely.
        payload = (
            f"# Handbook {uuid.uuid4().hex}\n\n"
            "Employees receive 20 days of annual leave."
        ).encode()
        response = client.post(
            "/documents",
            files={
                "file": (
                    "handbook.md",
                    payload,
                    "text/markdown",
                )
            },
        )
        assert response.status_code == 202, response.text
        document_id = response.json()["id"]

        assert _get_status(db_conn, document_id)[0] == "completed"
        assert fake_bm25.indexed, "chunks were never handed to the term index"
        assert {chunk.document_id for chunk in fake_bm25.indexed} == {document_id}

    def test_delete_removes_rows_from_both_indexes(
        self, client: TestClient, db_conn, fake_bm25, fake_vector_store
    ) -> None:
        document_id = _seed_document(db_conn, client.storage)

        response = client.delete(f"/documents/{document_id}")

        assert response.status_code == 200, response.text
        assert fake_bm25.deletes == [document_id]
        assert fake_vector_store.deletes == [document_id]

    def test_fts_delete_failure_keeps_document(
        self, client: TestClient, db_conn, fake_bm25, fake_vector_store
    ) -> None:
        document_id = _seed_document(db_conn, client.storage)
        fake_bm25.fail_delete = Bm25Error("database is locked")

        failed = client.delete(f"/documents/{document_id}")

        assert failed.status_code == 503
        # Nothing was deleted on either side; a retry is safe.
        assert fake_vector_store.deletes == []
        assert client.get(f"/documents/{document_id}").status_code == 200

        fake_bm25.fail_delete = None
        retried = client.delete(f"/documents/{document_id}")
        assert retried.status_code == 200
        assert fake_bm25.deletes == [document_id]
        assert fake_vector_store.deletes == [document_id]


class TestIngestionIndexFailure:
    def test_fts_write_failure_fails_document_and_rolls_back_vectors(
        self, db_conn, fake_embedder, fake_vector_store, fake_bm25, tmp_path
    ) -> None:
        from app.services import ingestion
        from app.services.storage import DocumentStorage

        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage)
        fake_bm25.fail_index = Bm25Error("database is locked")

        ingestion.ingest_document(
            document_id,
            embedder=fake_embedder,
            store=fake_vector_store,
            storage=storage,
        )

        status, record = _get_status(db_conn, document_id)
        assert status == "failed"
        assert "Search index write failed" in record.error_message
        # Vectors were written before the FTS step; they must be rolled back
        # so a `failed` document is not retrievable through either path.
        assert len(fake_vector_store.upserts) == 1
        assert fake_vector_store.deletes == [document_id]
