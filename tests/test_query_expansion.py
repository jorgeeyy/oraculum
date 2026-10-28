"""Multi-query expansion tests (flag-gated, off by default).

Covers the generator's expand_query helper (never-break contract, malformed
output, dedup/cap) and the /chat + /chat/stream wiring (one batched embed of
original+variants, per-variant vector/BM25 queries fused via RRF, meta
reporting, rewrite-then-expand ordering).
"""

import json

import pytest

from app.services.errors import GenerationServiceError
from app.services.retrieval import (
    fuse_rankings,
    reciprocal_rank_fuse,
    retrieve_documents,
)

from test_generation import make_generator
from test_streaming import parse_sse

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


def _vector_hit(chunk_index: int, score: float = 0.9) -> dict:
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
        "text": f"bm25 chunk {chunk_index}",
    }


class TestExpandQuery:
    def test_malformed_output_returns_empty(self) -> None:
        generator, _ = make_generator(script=["not json at all"])
        assert generator.expand_query("How many days?", 2) == []

    def test_client_failure_returns_empty(self) -> None:
        generator, _ = make_generator(script=[RuntimeError("openai down")])
        assert generator.expand_query("How many days?", 2) == []

    def test_service_error_returns_empty(self) -> None:
        generator, _ = make_generator(
            script=[GenerationServiceError("model unavailable")]
        )
        assert generator.expand_query("How many days?", 2) == []

    def test_zero_count_short_circuits(self) -> None:
        generator, client = make_generator()
        assert generator.expand_query("How many days?", 0) == []
        assert client.calls == []  # no API call was made

    def test_dedup_and_cap(self) -> None:
        payload = json.dumps(
            {"variants": ["annual leave", "annual leave", "  ", "vacation", "time off"]}
        )
        generator, _ = make_generator(script=[payload])
        variants = generator.expand_query("How many days?", 2)
        assert variants == ["annual leave", "vacation"]  # dedup + cap at 2

    def test_original_query_excluded(self) -> None:
        payload = json.dumps(
            {"variants": ["How many days?", "annual leave days"]}
        )
        generator, _ = make_generator(script=[payload])
        variants = generator.expand_query("How many days?", 2)
        assert variants == ["annual leave days"]  # the original is dropped

    def test_expansion_tokens_tracked(self) -> None:
        payload = json.dumps({"variants": ["annual leave days"]})
        generator, _ = make_generator(script=[payload])
        generator.expand_query("How many days?", 1)
        assert generator.expansion_tokens[0] >= 0


class TestFuseRankings:
    def test_three_rankings_are_merged(self) -> None:
        a = [_vector_hit(0), _vector_hit(1)]
        b = [_vector_hit(1), _vector_hit(2)]
        c = [_vector_hit(2), _vector_hit(0)]
        fused = fuse_rankings(a, b, c, top_k=10)
        assert {h["id"] for h in fused} == {
            f"{DOC_ID}#0",
            f"{DOC_ID}#1",
            f"{DOC_ID}#2",
        }
        # Every chunk appears in exactly two of the three rankings, so scores tie.
        scores = [h["score"] for h in fused]
        assert scores == pytest.approx([scores[0]] * 3)

    def test_found_in_more_rankings_outranks_single(self) -> None:
        a = [_vector_hit(0), _vector_hit(1)]
        b = [_vector_hit(1)]
        c = [_vector_hit(2)]
        fused = fuse_rankings(a, b, c, top_k=10)
        # Chunk #1 is in two rankings; #0 and #2 are in one each.
        assert fused[0]["id"] == f"{DOC_ID}#1"

    def test_wrapper_matches_two_ranking_fuse(self) -> None:
        vector = [_vector_hit(0), _vector_hit(1)]
        bm25 = [_bm25_hit(1)]
        assert reciprocal_rank_fuse(vector, bm25) == fuse_rankings(vector, bm25)

    def test_mixed_shapes_are_normalized(self) -> None:
        fused = fuse_rankings([_vector_hit(0)], [_bm25_hit(0)], top_k=10)
        assert fused[0]["id"] == f"{DOC_ID}#0"
        assert fused[0]["metadata"]["chunk_index"] == 0


class TestRetrieveWithVariants:
    def test_variants_embed_in_one_batch_and_query_each_side(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        retrieve_documents(
            "How many days?",
            embedder=fake_embedder,
            store=fake_vector_store,
            bm25=fake_bm25,
            hybrid=True,
            variants=["annual leave days", "vacation entitlement"],
        )
        # One batched embed: original + both variants.
        assert fake_embedder.batches == [
            ["How many days?", "annual leave days", "vacation entitlement"]
        ]
        # The vector store was queried once per query.
        assert len(fake_vector_store.queries) == 3
        # BM25 searched each query too (hybrid on).
        assert [s["query"] for s in fake_bm25.searches] == [
            "How many days?",
            "annual leave days",
            "vacation entitlement",
        ]

    def test_variants_matching_the_question_are_deduped(
        self, fake_embedder, fake_vector_store
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        retrieve_documents(
            "How many days?",
            embedder=fake_embedder,
            store=fake_vector_store,
            variants=["How many days?", "   ", "annual leave"],
        )
        # The duplicate and blank were dropped: only two texts embedded.
        assert fake_embedder.batches == [["How many days?", "annual leave"]]
        assert len(fake_vector_store.queries) == 2

    def test_precomputed_vector_skips_the_original_embed(
        self, fake_embedder, fake_vector_store
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        retrieve_documents(
            "How many days?",
            embedder=fake_embedder,
            store=fake_vector_store,
            query_vector=[0.25, 0.5, 0.75],
            variants=["annual leave"],
        )
        # Only the variant was embedded; the original reused the given vector.
        assert fake_embedder.batches == [["annual leave"]]

    def test_non_hybrid_variants_bypass_bm25(
        self, fake_embedder, fake_vector_store, fake_bm25
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        outcome = retrieve_documents(
            "How many days?",
            embedder=fake_embedder,
            store=fake_vector_store,
            bm25=fake_bm25,
            hybrid=False,
            variants=["annual leave"],
        )
        assert len(fake_vector_store.queries) == 2
        assert fake_bm25.searches == []
        assert outcome.results  # fused vector rankings still produce results


class TestExpansionEndpoint:
    def test_expansion_off_by_default(
        self, client, fake_embedder, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200
        # No expansion call; a single-query embed happened.
        assert fake_generator.expansion_calls == []
        assert fake_embedder.batches == [["How many days?"]]

    def test_expansion_embeds_and_searches_every_query(
        self, client_with_settings, fake_embedder, fake_vector_store, fake_bm25, fake_generator
    ) -> None:
        client = client_with_settings(
            chat_query_expansion=True, chat_query_expansion_variants=2
        )
        fake_generator.expansion_result = ["annual leave days", "vacation entitlement"]
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200
        assert fake_embedder.batches == [
            ["How many days?", "annual leave days", "vacation entitlement"]
        ]
        assert len(fake_vector_store.queries) == 3
        assert [s["query"] for s in fake_bm25.searches] == [
            "How many days?",
            "annual leave days",
            "vacation entitlement",
        ]
        # Expansion ran on the standalone (stateless) question.
        assert fake_generator.expansion_calls[0]["question"] == "How many days?"

    def test_expansion_failure_falls_back_to_single_query(
        self, client_with_settings, fake_embedder, fake_vector_store, fake_generator
    ) -> None:
        client = client_with_settings(chat_query_expansion=True)
        fake_generator.expansion_result = []  # expand_query degraded to []
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200
        assert fake_embedder.batches == [["How many days?"]]
        assert len(fake_vector_store.queries) == 1

    def test_non_hybrid_expansion_fuses_vector_rankings_only(
        self, client_with_settings, fake_embedder, fake_vector_store, fake_bm25, fake_generator
    ) -> None:
        client = client_with_settings(
            chat_query_expansion=True,
            chat_query_expansion_variants=2,
            retrieval_hybrid=False,
        )
        fake_generator.expansion_result = ["a", "b"]
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200
        assert len(fake_vector_store.queries) == 3
        assert fake_bm25.searches == []  # hybrid off -> no BM25

    def test_stream_meta_reports_expanded_count(
        self, client_with_settings, fake_vector_store, fake_generator
    ) -> None:
        client = client_with_settings(
            chat_query_expansion=True, chat_query_expansion_variants=2
        )
        fake_generator.expansion_result = ["a", "b"]
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat/stream", json={"question": "How many days?"})
        meta = parse_sse(response.text)[0][1]
        assert meta["expanded"] == 2
        assert meta["cached"] is False

    def test_expansion_runs_after_rewrite(
        self, client_with_settings, db_conn, fake_vector_store, fake_generator
    ) -> None:
        client = client_with_settings(
            chat_query_expansion=True, chat_query_expansion_variants=2
        )
        fake_vector_store.query_result = [STORE_HIT]
        first = client.post(
            "/chat",
            json={"question": "How many days of annual leave?", "conversation_id": None},
        )
        conversation_id = first.json()["conversation_id"]

        fake_generator.rewrite_result = "part-time employees annual leave"
        fake_generator.expansion_result = ["vacation days", "time off"]
        follow_up = client.post(
            "/chat",
            json={"question": "What about part-timers?", "conversation_id": conversation_id},
        )
        assert follow_up.status_code == 200
        # Rewrite ran on the raw follow-up; expansion ran on the REWRITTEN query.
        assert fake_generator.rewrite_calls[-1]["question"] == "What about part-timers?"
        assert fake_generator.expansion_calls[-1]["question"] == "part-time employees annual leave"

        client.delete(f"/conversations/{conversation_id}")
