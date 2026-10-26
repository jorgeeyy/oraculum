"""Unit tests for the FTS5-backed BM25 index against a real SQLite file.

These use a temporary database (not the fake) so the actual FTS5 behavior -
porter stemming, MATCH sanitization, atomic replace - is exercised.
"""

import sqlite3

import pytest

from app.schemas.documents import Chunk
from app.services.bm25_index import Bm25Error, Bm25Index


def _chunk(index: int, text: str, *, document_id: str = "doc-1") -> Chunk:
    return Chunk(
        document_id=document_id,
        filename="handbook.md",
        page=1,
        chunk_index=index,
        token_count=len(text.split()),
        text=text,
    )


@pytest.fixture()
def index(tmp_path) -> Bm25Index:
    return Bm25Index(tmp_path / "bm25.db")


class TestSearch:
    def test_finds_term_and_orders_best_first(self, index: Bm25Index) -> None:
        index.index_chunks(
            [
                _chunk(0, "Employees receive 20 days of annual leave"),
                _chunk(1, "The data protection principles apply to everyone"),
            ]
        )
        hits = index.search("principles")
        assert len(hits) == 1
        assert hits[0]["id"] == "doc-1#1"
        assert hits[0]["document_id"] == "doc-1"
        assert hits[0]["filename"] == "handbook.md"
        assert hits[0]["page"] == 1
        assert hits[0]["chunk_index"] == 1
        assert "principles" in hits[0]["text"]

    def test_porter_stemming_matches_inflections(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(0, "the company stores personal data")])
        assert index.search("storing")  # porter: store/storing
        assert index.search("stored")

    def test_or_semantics_across_query_terms(self, index: Bm25Index) -> None:
        index.index_chunks(
            [
                _chunk(0, "vpn setup guide"),
                _chunk(1, "annual leave policy"),
            ]
        )
        hits = index.search("vpn leave")
        assert len(hits) == 2

    def test_unsafe_query_tokens_do_not_raise(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(0, "firewall rules - advanced (v2.0: yes)")])
        for query in ("a-b", "site:example.com", '(broken "quote', "!!!"):
            assert isinstance(index.search(query), list)  # never raises

    def test_termless_query_returns_empty(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(0, "some text")])
        assert index.search("   ") == []
        assert index.search("!!!") == []

    def test_document_filter_restricts_results(self, index: Bm25Index) -> None:
        index.index_chunks(
            [
                _chunk(0, "principles for doc one", document_id="doc-1"),
                _chunk(0, "principles for doc two", document_id="doc-2"),
            ]
        )
        hits = index.search("principles", document_id="doc-2")
        assert [hit["document_id"] for hit in hits] == ["doc-2"]

    def test_top_k_limits_results(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(i, f"principles number {i}") for i in range(5)])
        assert len(index.search("principles", top_k=3)) == 3


class TestIndexing:
    def test_reindex_same_document_replaces_rows(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(0, "old text about holidays")])
        index.index_chunks([_chunk(0, "new text about parental leave")])
        assert index.count() == 1
        assert index.search("holidays") == []
        assert len(index.search("parental leave")) == 1

    def test_index_is_persistent_across_instances(self, tmp_path) -> None:
        path = tmp_path / "bm25.db"
        Bm25Index(path).index_chunks([_chunk(0, "persistent principles")])
        assert len(Bm25Index(path).search("principles")) == 1

    def test_indexing_empty_list_is_noop(self, index: Bm25Index) -> None:
        index.index_chunks([])
        assert index.count() == 0

    def test_count_reflects_rows(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(i, f"chunk {i}") for i in range(3)])
        index.index_chunks([_chunk(i, f"other {i}", document_id="doc-2") for i in range(2)])
        assert index.count() == 5

    def test_failed_write_leaves_index_unchanged(
        self, index: Bm25Index
    ) -> None:
        index.index_chunks([_chunk(0, "surviving principles text")])
        # Deny INSERTs on a guarded connection: the DELETE inside
        # index_chunks' transaction succeeds first, then the INSERT fails,
        # so the whole batch must roll back - no partial rows, old row kept.
        real_connect = index._connect

        def guarded_connect():
            conn = real_connect()

            def _authorize(action, _a, _b, _db, _src):
                if action == sqlite3.SQLITE_INSERT:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(_authorize)
            return conn

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(index, "_connect", guarded_connect)
            with pytest.raises(Bm25Error, match="index"):
                index.index_chunks([_chunk(0, "blocked text")])

        assert index.count() == 1
        assert len(index.search("principles")) == 1


class TestDeleteAndClear:
    def test_delete_removes_only_that_document(self, index: Bm25Index) -> None:
        index.index_chunks(
            [
                _chunk(0, "principles alpha", document_id="doc-1"),
                _chunk(0, "principles beta", document_id="doc-2"),
            ]
        )
        index.delete_document("doc-1")
        hits = index.search("principles")
        assert [hit["document_id"] for hit in hits] == ["doc-2"]

    def test_delete_unknown_document_is_safe(self, index: Bm25Index) -> None:
        index.delete_document("never-indexed")
        assert index.count() == 0

    def test_clear_empties_index(self, index: Bm25Index) -> None:
        index.index_chunks([_chunk(0, "principles everywhere")])
        index.clear()
        assert index.count() == 0
        assert index.search("principles") == []
