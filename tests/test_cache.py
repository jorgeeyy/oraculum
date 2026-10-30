"""Semantic answer cache tests (flag-gated, off by default).

Covers the cache's own matching rules (cosine threshold, staleness,
fingerprint/owner-bucket/document/model scoping, refusal skip) against real
Chroma in a temp dir, plus the /chat and /chat/stream wiring (miss -> hit
skips the LLM, stream hit replays one delta, cached answers still persist).
The owner bucket is the privacy boundary: one user's cached answer is never
served to another (per-user buckets; unscoped callers share one). The cache
probe must never break a request: every failure degrades to the normal path.
"""

import time
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.api.chat import get_answer_cache_dep
from app.db import repositories
from app.schemas.chat import Source
from app.services.answer_cache import AnswerCache
from app.services.generation import INSUFFICIENT_ANSWER

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


def _source() -> Source:
    return Source(
        source_id=f"{DOC_ID}#0",
        document_id=DOC_ID,
        filename="handbook.md",
        page=1,
        chunk_index=0,
        excerpt="Employees receive 20 days of annual leave.",
    )


def _store(
    cache: AnswerCache,
    vector,
    *,
    fingerprint="fp",
    owner_key="",
    document_id=None,
    model="m",
    answer="20 days.",
    max_age_hours=24.0,
) -> None:
    cache.store(
        vector,
        question="How many days?",
        answer=answer,
        status="answered",
        sources=[_source()],
        model=model,
        fingerprint=fingerprint,
        owner_key=owner_key,
        document_id=document_id,
        max_age_hours=max_age_hours,
    )


def _lookup(
    cache: AnswerCache,
    vector,
    *,
    fingerprint="fp",
    owner_key="",
    document_id=None,
    model="m",
    min_similarity=0.97,
    max_age_hours=24.0,
):
    return cache.lookup(
        vector,
        fingerprint,
        owner_key=owner_key,
        document_id=document_id,
        model=model,
        min_similarity=min_similarity,
        max_age_hours=max_age_hours,
    )


def _register(client, email: str) -> None:
    """Create an account and leave the client signed in as that user."""
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text


def _create_shared_document(db_conn) -> str:
    """Seed one NULL-owner row so scoped callers have a non-empty visible set.

    Tests must never assume a pre-existing shared corpus: the dev database
    may be freshly flushed. db_conn removes the row at teardown.
    """
    session = sessionmaker(bind=db_conn, expire_on_commit=False)()
    document_id = str(uuid.uuid4())
    try:
        repositories.create_document(
            session,
            document_id=document_id,
            original_filename="shared.md",
            file_type="md",
            size_bytes=10,
        )
    finally:
        session.close()
    return document_id


@pytest.fixture()
def cache(tmp_path) -> AnswerCache:
    """A real AnswerCache backed by Chroma in a throw-away directory."""
    return AnswerCache(tmp_path, "chat_cache")


@pytest.fixture()
def cache_client_factory(tmp_path, monkeypatch, client_with_settings):
    """TestClient factory with the cache enabled and a real tmp Chroma cache.

    The corpus fingerprint is stubbed (no DB needed) and mutable via the
    returned object's `fingerprint` dict, so tests can simulate the corpus
    changing between requests.
    """
    cache = AnswerCache(tmp_path, "chat_cache")
    fingerprint = {"value": "fp-test"}
    monkeypatch.setattr(
        "app.api.chat._corpus_fingerprint", lambda: fingerprint["value"]
    )

    def factory(**settings_overrides):
        client = client_with_settings(
            chat_cache_enabled=True, **settings_overrides
        )
        client.app.dependency_overrides[get_answer_cache_dep] = lambda: cache
        return client

    factory.cache = cache
    factory.fingerprint = fingerprint
    return factory


class TestAnswerCacheUnit:
    def test_store_then_lookup_roundtrip(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0])
        hit = _lookup(cache, [1.0, 0.0, 0.0])
        assert hit is not None
        assert hit.answer == "20 days."
        assert hit.status == "answered"
        assert hit.model == "m"
        assert hit.similarity == pytest.approx(1.0)
        assert hit.sources[0].filename == "handbook.md"

    def test_below_threshold_is_a_miss(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0])
        # Orthogonal vector: cosine similarity 0 < 0.97.
        assert _lookup(cache, [0.0, 1.0, 0.0]) is None

    def test_stale_entry_is_a_miss(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0], max_age_hours=24.0)
        # A zero max age makes the just-stored entry already too old.
        time.sleep(0.01)
        assert _lookup(cache, [1.0, 0.0, 0.0], max_age_hours=0.0) is None

    def test_fingerprint_change_is_a_miss(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0], fingerprint="v1")
        assert _lookup(cache, [1.0, 0.0, 0.0], fingerprint="v2") is None

    def test_document_filter_mismatch_is_a_miss(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0], document_id="doc-A")
        # An unfiltered lookup must not see the document-scoped entry.
        assert _lookup(cache, [1.0, 0.0, 0.0], document_id=None) is None

    def test_model_mismatch_is_a_miss(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0], model="gpt-4o-mini")
        assert _lookup(cache, [1.0, 0.0, 0.0], model="gpt-4o") is None

    def test_owner_bucket_mismatch_is_a_miss(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0], owner_key="user-a")
        # Another user's (or anonymous) identical question must not see it.
        assert _lookup(cache, [1.0, 0.0, 0.0], owner_key="user-b") is None
        assert _lookup(cache, [1.0, 0.0, 0.0], owner_key="") is None
        # The owning bucket still hits.
        hit = _lookup(cache, [1.0, 0.0, 0.0], owner_key="user-a")
        assert hit is not None
        assert hit.answer == "20 days."

    def test_same_question_in_two_buckets_stays_independent(self, cache) -> None:
        # Entry ids hash the owner bucket: two users storing the identical
        # question create separate entries and never overwrite each other.
        _store(cache, [1.0, 0.0, 0.0], owner_key="user-a", answer="A's answer")
        _store(cache, [1.0, 0.0, 0.0], owner_key="user-b", answer="B's answer")
        assert cache._collection.count() == 2
        assert (
            _lookup(cache, [1.0, 0.0, 0.0], owner_key="user-a").answer
            == "A's answer"
        )
        assert (
            _lookup(cache, [1.0, 0.0, 0.0], owner_key="user-b").answer
            == "B's answer"
        )

    def test_refusal_is_never_stored(self, cache) -> None:
        cache.store(
            [1.0, 0.0, 0.0],
            question="How many days?",
            answer=INSUFFICIENT_ANSWER,
            status="insufficient_evidence",
            sources=[],
            model="m",
            fingerprint="fp",
            owner_key="",
            document_id=None,
            max_age_hours=24.0,
        )
        assert _lookup(cache, [1.0, 0.0, 0.0]) is None

    def test_clear_empties_the_cache(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0])
        cache.clear()
        assert _lookup(cache, [1.0, 0.0, 0.0]) is None

    def test_store_prunes_expired_entries(self, cache) -> None:
        _store(cache, [1.0, 0.0, 0.0], max_age_hours=24.0)
        # Backdate the stored entry so the next store's prune reclaims it.
        existing = cache._collection.get(include=["metadatas"])
        vector_id = existing["ids"][0]
        metadata = dict(existing["metadatas"][0])
        metadata["created_at"] = time.time() - 48 * 3600
        cache._collection.upsert(
            ids=[vector_id],
            embeddings=[[1.0, 0.0, 0.0]],
            documents=["20 days."],
            metadatas=[metadata],
        )
        assert cache._collection.count() == 1
        # A fresh store triggers a prune that reclaims the backdated entry.
        _store(cache, [0.0, 1.0, 0.0], max_age_hours=24.0)
        assert cache._collection.count() == 1


class TestCacheEndpoint:
    def test_cache_off_by_default_serves_from_generation(
        self, client, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        first = client.post("/chat", json={"question": "How many days?"})
        second = client.post("/chat", json={"question": "How many days?"})
        assert first.status_code == second.status_code == 200
        assert first.json()["cached"] is False
        assert second.json()["cached"] is False
        # Both hit the LLM; the cache was never consulted.
        assert len(fake_generator.calls) == 2

    def test_stateless_request_stays_db_free_when_cache_off(
        self, client, fake_vector_store, fake_generator, monkeypatch
    ) -> None:
        def _boom():
            raise AssertionError("stateless path must not touch the database")

        monkeypatch.setattr("app.api.chat.open_db_session", _boom)
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200

    def test_miss_then_hit_skips_the_llm(
        self, cache_client_factory, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = [STORE_HIT]
        first = client.post("/chat", json={"question": "How many days?"})
        assert first.json()["cached"] is False
        assert len(fake_generator.calls) == 1

        second = client.post("/chat", json={"question": "How many days?"})
        body = second.json()
        assert body["cached"] is True
        assert body["cache_similarity"] == pytest.approx(1.0)
        assert body["answer"] == first.json()["answer"]
        # The LLM was not called a second time.
        assert len(fake_generator.calls) == 1

    def test_probe_embedding_is_reused_for_retrieval(
        self, cache_client_factory, fake_embedder, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = [STORE_HIT]
        client.post("/chat", json={"question": "How many days?"})
        # One embed total: the probe vector doubles as the retrieval vector.
        assert fake_embedder.batches == [["How many days?"]]

    def test_staleness_via_tiny_max_age_regenerates(
        self, cache_client_factory, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory(chat_cache_max_age_hours=1e-6)
        fake_vector_store.query_result = [STORE_HIT]
        client.post("/chat", json={"question": "How many days?"})
        time.sleep(0.02)  # age the entry past the tiny max age
        second = client.post("/chat", json={"question": "How many days?"})
        assert second.json()["cached"] is False
        assert len(fake_generator.calls) == 2

    def test_fingerprint_change_invalidates_the_entry(
        self, cache_client_factory, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = [STORE_HIT]
        client.post("/chat", json={"question": "How many days?"})
        # Simulate an ingest changing the corpus fingerprint.
        cache_client_factory.fingerprint["value"] = "fp-changed"
        second = client.post("/chat", json={"question": "How many days?"})
        assert second.json()["cached"] is False
        assert len(fake_generator.calls) == 2

    def test_refusal_is_never_cached(
        self, cache_client_factory, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = []  # no evidence -> deterministic refusal
        first = client.post("/chat", json={"question": "nothing here"})
        assert first.json()["status"] == "insufficient_evidence"
        second = client.post("/chat", json={"question": "nothing here"})
        assert second.json()["cached"] is False
        assert second.json()["status"] == "insufficient_evidence"
        # Refusals cost no LLM call, so nothing was generated to cache.
        assert fake_generator.calls == []

    def test_cache_failure_degrades_to_normal_answer(
        self, cache_client_factory, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = [STORE_HIT]

        class _BrokenCache:
            def lookup(self, *args, **kwargs):
                raise RuntimeError("chroma exploded")

            def store(self, *args, **kwargs):
                raise RuntimeError("chroma exploded")

        client.app.dependency_overrides[get_answer_cache_dep] = lambda: _BrokenCache()
        response = client.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200
        assert response.json()["cached"] is False
        assert response.json()["answer"] == fake_generator.result.answer


    def test_cached_answer_is_never_served_across_users(
        self, cache_client_factory, fake_vector_store, fake_generator, db_conn
    ) -> None:
        # Seed the shared corpus: session users need a non-empty visible
        # set to reach generation (a fresh flush may leave none).
        _create_shared_document(db_conn)
        fake_vector_store.query_result = [STORE_HIT]

        alice = cache_client_factory()
        _register(alice, "cache-alice@local.dev")
        first = alice.post("/chat", json={"question": "How many days?"})
        assert first.json()["cached"] is False
        assert len(fake_generator.calls) == 1

        # Bob asks the IDENTICAL question: alice's cached answer (with her
        # citations) must NOT be served - it is a fresh miss for him.
        bob = cache_client_factory()
        _register(bob, "cache-bob@local.dev")
        bob_first = bob.post("/chat", json={"question": "How many days?"})
        assert bob_first.json()["cached"] is False
        assert len(fake_generator.calls) == 2

        # Each user's own entry then hits (per-bucket entry ids: neither
        # store overwrote the other).
        bob_second = bob.post("/chat", json={"question": "How many days?"})
        alice_second = alice.post("/chat", json={"question": "How many days?"})
        assert bob_second.json()["cached"] is True
        assert alice_second.json()["cached"] is True
        assert len(fake_generator.calls) == 2

    def test_unscoped_callers_share_one_bucket(
        self, cache_client_factory, fake_vector_store, fake_generator, db_conn
    ) -> None:
        # The opt-in local scope answers and stores (bucket "")...
        fake_vector_store.query_result = [STORE_HIT]
        anonymous = cache_client_factory()
        first = anonymous.post("/chat", json={"question": "How many days?"})
        assert first.json()["cached"] is False
        assert len(fake_generator.calls) == 1

        # ...and an admin session - also unscoped (whole corpus) - reuses
        # it: sharing among full-corpus callers leaks nothing.
        admin = cache_client_factory()
        _register(admin, "cache-boss@local.dev")
        session = sessionmaker(bind=db_conn, expire_on_commit=False)()
        try:
            user = repositories.get_user_by_email(session, "cache-boss@local.dev")
            assert user is not None
            user.role = "admin"
            session.commit()
        finally:
            session.close()
        hit = admin.post("/chat", json={"question": "How many days?"})
        assert hit.json()["cached"] is True
        assert len(fake_generator.calls) == 1

    def test_unscoped_entry_is_not_served_to_a_regular_user(
        self, cache_client_factory, fake_vector_store, fake_generator, db_conn
    ) -> None:
        # The mirror of the test above: an entry stored by a full-corpus
        # caller may cite anything, so a regular user never receives it.
        # The shared corpus is seeded so the user's own request reaches
        # generation (fresh DBs may otherwise short-circuit to a refusal,
        # which is never cached and would prove nothing).
        _create_shared_document(db_conn)
        fake_vector_store.query_result = [STORE_HIT]
        anonymous = cache_client_factory()
        anonymous.post("/chat", json={"question": "How many days?"})
        assert len(fake_generator.calls) == 1

        user = cache_client_factory()
        _register(user, "cache-regular@local.dev")
        fresh = user.post("/chat", json={"question": "How many days?"})
        assert fresh.json()["cached"] is False
        assert len(fake_generator.calls) == 2


class TestCacheStream:
    def test_stream_cache_hit_emits_meta_delta_done(
        self, cache_client_factory, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = [STORE_HIT]
        # Warm the cache via the plain endpoint.
        client.post("/chat", json={"question": "How many days?"})

        response = client.post("/chat/stream", json={"question": "How many days?"})
        events = parse_sse(response.text)
        assert [kind for kind, _ in events] == ["meta", "delta", "done"]
        meta = events[0][1]
        assert meta["cached"] is True
        assert events[1][1]["text"] == fake_generator.result.answer
        done = events[2][1]
        assert done["cached"] is True
        assert done["cache_similarity"] == pytest.approx(1.0)
        # The streaming generator was never invoked for the cached answer.
        assert fake_generator.stream_calls == []

    def test_cached_answer_is_persisted(
        self, cache_client_factory, db_conn, fake_vector_store, fake_generator
    ) -> None:
        client = cache_client_factory()
        fake_vector_store.query_result = [STORE_HIT]
        first = client.post(
            "/chat", json={"question": "How many days?", "conversation_id": None}
        )
        conversation_id = first.json()["conversation_id"]
        assert conversation_id

        second = client.post(
            "/chat",
            json={"question": "How many days?", "conversation_id": conversation_id},
        )
        assert second.json()["cached"] is True
        # The cached exchange was appended to the same conversation.
        detail = client.get(f"/conversations/{conversation_id}").json()
        contents = [m["content"] for m in detail["messages"]]
        assert contents.count(fake_generator.result.answer) == 2


class TestCorpusFingerprint:
    def test_fingerprint_changes_when_a_document_row_changes(self, db_conn) -> None:
        session = sessionmaker(bind=db_conn, expire_on_commit=False)()
        document_id = str(uuid.uuid4())
        repositories.create_document(
            session,
            document_id=document_id,
            original_filename="a.md",
            file_type="md",
            size_bytes=10,
            content_hash="hash-1",
        )
        try:
            before = repositories.corpus_fingerprint(session)
            repositories.mark_processing(session, document_id)
            after_status = repositories.corpus_fingerprint(session)
            assert after_status != before
            repositories.fail_document(session, document_id, "boom")
            assert repositories.corpus_fingerprint(session) != after_status
        finally:
            session.close()

    def test_fingerprint_is_stable_for_an_unchanged_corpus(self, db_conn) -> None:
        session = sessionmaker(bind=db_conn, expire_on_commit=False)()
        assert repositories.corpus_fingerprint(
            session
        ) == repositories.corpus_fingerprint(session)
        session.close()
