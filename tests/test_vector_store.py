from pathlib import Path

import pytest

from app.schemas.documents import Chunk
from app.services import vector_store as vector_store_module
from app.services.errors import VectorStoreError
from app.services.vector_store import VectorStore

DOC_ID = "11111111-2222-3333-4444-555555555555"


def make_chunk(chunk_index: int = 0, text: str = "Employees receive 20 days.") -> Chunk:
    return Chunk(
        document_id=DOC_ID,
        filename="handbook.md",
        page=3,
        chunk_index=chunk_index,
        token_count=6,
        text=text,
    )


class FakeCollection:
    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.deletes: list[dict] = []
        self.queries: list[dict] = []
        self._count = 0
        self.query_result: dict = {}
        self.delete_error: Exception | None = None

    def upsert(self, *, ids, embeddings, metadatas, documents) -> None:
        self.upserts.append(
            {
                "ids": ids,
                "embeddings": embeddings,
                "metadatas": metadatas,
                "documents": documents,
            }
        )
        self._count += len(ids)

    def delete(self, *, where) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deletes.append(where)

    def count(self) -> int:
        return self._count

    def query(self, *, query_embeddings, n_results, where, include) -> dict:
        self.queries.append(
            {
                "query_embeddings": query_embeddings,
                "n_results": n_results,
                "where": where,
                "include": include,
            }
        )
        return self.query_result


class FakeChromaClient:
    def __init__(self) -> None:
        self.collection = FakeCollection()
        self.get_or_create_calls: list[dict] = []

    def get_or_create_collection(self, name: str, metadata=None):
        self.get_or_create_calls.append({"name": name, "metadata": metadata})
        return self.collection


def make_store() -> tuple[VectorStore, FakeChromaClient]:
    client = FakeChromaClient()
    store = VectorStore(Path("data/chroma"), "documents", client=client)
    return store, client


class TestUpsert:
    def test_upsert_payload_shape(self) -> None:
        store, client = make_store()
        chunks = [make_chunk(0, "first"), make_chunk(1, "second")]
        embeddings = [[0.1, 0.2], [0.3, 0.4]]

        store.upsert_chunks(chunks, embeddings)

        assert client.get_or_create_calls == [
            {"name": "documents", "metadata": {"hnsw:space": "cosine"}}
        ]
        call = client.collection.upserts[0]
        assert call["ids"] == [f"{DOC_ID}#0", f"{DOC_ID}#1"]
        assert call["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
        assert call["documents"] == ["first", "second"]
        assert call["metadatas"][0] == {
            "document_id": DOC_ID,
            "filename": "handbook.md",
            "page": 3,
            "chunk_index": 0,
        }

    def test_upsert_count_mismatch_raises(self) -> None:
        store, client = make_store()
        with pytest.raises(VectorStoreError, match="1 chunks but 0 embeddings"):
            store.upsert_chunks([make_chunk()], [])
        assert client.collection.upserts == []

    def test_upsert_empty_is_noop(self) -> None:
        store, client = make_store()
        store.upsert_chunks([], [])
        assert client.collection.upserts == []

    def test_upsert_error_is_wrapped(self) -> None:
        store, client = make_store()

        def explode(**_kwargs) -> None:
            raise RuntimeError("disk full")

        client.collection.upsert = explode  # type: ignore[method-assign]
        with pytest.raises(VectorStoreError, match="RuntimeError"):
            store.upsert_chunks([make_chunk()], [[0.1]])


class TestDelete:
    def test_delete_uses_document_metadata_filter(self) -> None:
        store, client = make_store()
        store.delete_document_vectors(DOC_ID)
        assert client.collection.deletes == [{"document_id": {"$eq": DOC_ID}}]

    def test_delete_error_is_wrapped(self) -> None:
        store, client = make_store()
        client.collection.delete_error = RuntimeError("locked")
        with pytest.raises(VectorStoreError, match="RuntimeError"):
            store.delete_document_vectors(DOC_ID)


class TestQuery:
    def test_query_maps_distance_to_score(self) -> None:
        store, client = make_store()
        client.collection._count = 1
        client.collection.query_result = {
            "ids": [[f"{DOC_ID}#0"]],
            "distances": [[0.1]],
            "documents": [["Annual leave is 20 days."]],
            "metadatas": [[{"document_id": DOC_ID, "page": 3}]],
        }

        hits = store.query([0.1, 0.2], top_k=3)

        assert len(hits) == 1
        assert hits[0]["id"] == f"{DOC_ID}#0"
        assert hits[0]["score"] == pytest.approx(0.9)
        assert hits[0]["text"] == "Annual leave is 20 days."
        assert hits[0]["metadata"]["page"] == 3
        query_call = client.collection.queries[0]
        assert query_call["n_results"] == 3
        assert query_call["where"] is None

    def test_query_scopes_to_document(self) -> None:
        store, client = make_store()
        client.collection._count = 1
        client.collection.query_result = {"ids": [[]], "distances": [[]]}

        store.query([0.1], document_id=DOC_ID)

        assert client.collection.queries[0]["where"] == {
            "document_id": {"$eq": DOC_ID}
        }

    def test_query_empty_index_returns_without_calling_collection(self) -> None:
        store, client = make_store()
        assert store.query([0.1]) == []
        assert client.collection.queries == []


def test_persistent_client_disables_telemetry(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    class FakeSettings:
        def __init__(self, *, anonymized_telemetry: bool) -> None:
            self.anonymized_telemetry = anonymized_telemetry

    class FakePersistentClient:
        def __init__(self, path: str, settings) -> None:
            captured["path"] = path
            captured["settings"] = settings

        def get_or_create_collection(self, name: str, metadata=None):
            return FakeCollection()

    monkeypatch.setattr(
        vector_store_module.chromadb, "PersistentClient", FakePersistentClient
    )
    monkeypatch.setattr(vector_store_module, "ChromaSettings", FakeSettings)

    VectorStore(tmp_path / "chroma", "documents")

    assert captured["path"] == str(tmp_path / "chroma")
    assert isinstance(captured["settings"], FakeSettings)
    assert captured["settings"].anonymized_telemetry is False
