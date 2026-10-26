import logging
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.schemas.documents import Chunk
from app.services.errors import VectorStoreError

logger = logging.getLogger(__name__)

_UPSERT_BATCH_SIZE = 500


class VectorStore:
    """Local vector index backed by a persistent ChromaDB collection.

    Vectors are stored with the chunk text and citation metadata so retrieval
    can return sources without re-chunking the original file. The collection
    uses cosine distance; query results expose a similarity score where
    higher is better.
    """

    def __init__(
        self,
        path: Path,
        collection_name: str,
        client: Any | None = None,
    ) -> None:
        if client is None:
            path.mkdir(parents=True, exist_ok=True)
            # Telemetry is a local-app decision, not user config: off.
            client = chromadb.PersistentClient(
                path=str(path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        self._client = client
        try:
            self._collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise VectorStoreError(
                f"Could not open vector collection '{collection_name}': "
                f"{type(exc).__name__}."
            ) from exc

    def upsert_chunks(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Index chunks with their embeddings; same IDs replace, never duplicate."""
        if len(chunks) != len(embeddings):
            raise VectorStoreError(
                f"Cannot index: {len(chunks)} chunks but {len(embeddings)} embeddings."
            )
        if not chunks:
            return

        ids = [f"{chunk.document_id}#{chunk.chunk_index}" for chunk in chunks]
        metadatas = [
            {
                "document_id": chunk.document_id,
                "filename": chunk.filename,
                "page": chunk.page,
                "chunk_index": chunk.chunk_index,
            }
            for chunk in chunks
        ]
        documents = [chunk.text for chunk in chunks]

        for start in range(0, len(ids), _UPSERT_BATCH_SIZE):
            end = start + _UPSERT_BATCH_SIZE
            try:
                self._collection.upsert(
                    ids=ids[start:end],
                    embeddings=[list(vector) for vector in embeddings[start:end]],
                    metadatas=metadatas[start:end],
                    documents=documents[start:end],
                )
            except Exception as exc:
                raise VectorStoreError(
                    f"Failed to index {len(ids)} chunks: {type(exc).__name__}."
                ) from exc

    def delete_document_vectors(self, document_id: str) -> None:
        """Remove every vector belonging to a document; safe if none exist."""
        try:
            self._collection.delete(where={"document_id": {"$eq": document_id}})
        except Exception as exc:
            raise VectorStoreError(
                f"Failed to remove vectors for document {document_id}: "
                f"{type(exc).__name__}."
            ) from exc

    def count(self) -> int:
        """Total vectors in the collection; used by readiness checks."""
        try:
            return int(self._collection.count())
        except Exception as exc:
            raise VectorStoreError(
                f"Could not count vectors: {type(exc).__name__}."
            ) from exc

    def all_chunks(self) -> list[dict[str, Any]]:
        """Every stored chunk as {id, text, metadata}; used by the BM25 backfill.

        Reads stored texts only - no embeddings are returned or recomputed.
        """
        try:
            result = self._collection.get(include=["documents", "metadatas"])
        except Exception as exc:
            raise VectorStoreError(
                f"Could not list stored chunks: {type(exc).__name__}."
            ) from exc
        ids = result.get("ids") or []
        texts = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        return [
            {
                "id": vector_id,
                "text": text or "",
                "metadata": metadata or {},
            }
            for vector_id, text, metadata in zip(ids, texts, metadatas, strict=False)
        ]

    def query(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 5,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return nearest chunks as {id, score, text, metadata}; higher score is better."""
        where = {"document_id": {"$eq": document_id}} if document_id else None
        try:
            if self._collection.count() == 0:
                return []
            result = self._collection.query(
                query_embeddings=[list(vector)],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreError(
                f"Vector query failed: {type(exc).__name__}."
            ) from exc

        hits: list[dict[str, Any]] = []
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        texts = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        for vector_id, distance, text, metadata in zip(
            ids, distances, texts, metadatas, strict=False
        ):
            hits.append(
                {
                    "id": vector_id,
                    # Cosine distance: 0 = identical; convert to similarity.
                    "score": 1.0 - distance,
                    "text": text or "",
                    "metadata": metadata or {},
                }
            )
        return hits


@lru_cache(maxsize=4)
def _build_store(path: str, collection_name: str) -> VectorStore:
    return VectorStore(Path(path), collection_name)


def get_vector_store() -> VectorStore:
    """Dependency entry point; one cached store per (path, collection)."""
    settings = get_settings()
    return _build_store(str(settings.chroma_path), settings.vector_collection)
