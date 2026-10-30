"""Semantic answer cache over a second persistent Chroma collection.

A question whose embedding is at least `min_similarity` cosine-similar to a
cached one - within `max_age_hours`, recorded against the same corpus
fingerprint, owner bucket, document filter, and model - is answered straight
from the cache: no retrieval, no LLM call. Only "answered" responses are
ever stored; refusals cost nothing to regenerate and caching them could
hide newly indexed evidence.

The owner bucket (`owner_key`) is the privacy boundary: a regular user's
cached answer - which embeds evidence from their private documents - is
only ever served back to that same user. Unscoped callers (admin session,
API key, local mode) share one bucket; entries written before the field
existed never match (treated as misses).

Correctness rests on the corpus fingerprint (sha256 over every document's
id, content hash, and status): any ingest, delete, or lifecycle change makes
every cached entry unreachable, and the wipe on those paths is hygiene.
Every operation here is fail-open by design - a cache failure must degrade
to the normal answer path, never fail the request.
"""

import hashlib
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.schemas.chat import Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheHit:
    """A cached answer that matched the incoming question."""

    answer: str
    status: str
    sources: list[Source]
    model: str
    similarity: float


class AnswerCache:
    """Cosine-similarity answer cache backed by a Chroma collection.

    Mirrors VectorStore's construction (persistent client, cosine space) so
    it reuses the same on-disk Chroma instance under a second collection
    name - no new dependency, no new service.
    """

    def __init__(
        self,
        path: Path,
        collection_name: str,
        client: Any | None = None,
    ) -> None:
        if client is None:
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        self._client = client
        self._collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def lookup(
        self,
        vector: Sequence[float],
        fingerprint: str,
        *,
        owner_key: str,
        document_id: str | None = None,
        model: str,
        min_similarity: float,
        max_age_hours: float,
    ) -> CacheHit | None:
        """Return the nearest matching cached answer, or None.

        The where-filter pins the entry to the same corpus fingerprint,
        owner bucket, document filter, and model; similarity and age are
        checked in Python. `owner_key` is what keeps one user's cached
        answer (with its citations) from ever being served to another -
        entries predating the field simply never match. Any failure logs
        and returns None (fail-open).
        """
        try:
            if self._collection.count() == 0:
                return None
            result = self._collection.query(
                query_embeddings=[list(vector)],
                n_results=1,
                where={
                    "$and": [
                        {"fingerprint": {"$eq": fingerprint}},
                        {"owner_key": {"$eq": owner_key}},
                        {"document_id": {"$eq": document_id or ""}},
                        {"model": {"$eq": model}},
                    ]
                },
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            logger.warning("Answer cache lookup failed", exc_info=True)
            return None

        ids = (result.get("ids") or [[]])[0]
        if not ids:
            return None
        distances = (result.get("distances") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]

        similarity = 1.0 - float(distances[0])  # cosine distance -> similarity
        if similarity < min_similarity:
            return None
        metadata = metadatas[0] or {}
        created_at = float(metadata.get("created_at", 0.0))
        if time.time() - created_at > max_age_hours * 3600:
            return None
        try:
            sources = [
                Source.model_validate(item)
                for item in json.loads(metadata.get("sources_json", "[]"))
            ]
        except Exception:
            logger.warning("Cached answer has unreadable sources; treating as a miss")
            return None
        return CacheHit(
            answer=documents[0] or "",
            status=str(metadata.get("status", "answered")),
            sources=sources,
            model=str(metadata.get("model", model)),
            similarity=similarity,
        )

    def store(
        self,
        vector: Sequence[float],
        *,
        question: str,
        answer: str,
        status: str,
        sources: Sequence[Source],
        model: str,
        fingerprint: str,
        owner_key: str,
        document_id: str | None = None,
        max_age_hours: float,
    ) -> None:
        """Cache an answered response keyed by the question embedding.

        Refusals are never stored. The entry id hashes the question within
        its (owner, document, model) scope, so re-asking refreshes the
        entry in place (a corpus change lands on the same id and overwrites
        the stale answer) while two users asking the same question keep
        independent entries. Expired entries are pruned opportunistically.
        Fail-open.
        """
        if status != "answered":
            return
        try:
            entry_id = hashlib.sha256(
                f"{owner_key}|{document_id or ''}|{model}|"
                f"{question.strip().casefold()}".encode()
            ).hexdigest()
            self._collection.upsert(
                ids=[entry_id],
                embeddings=[list(vector)],
                documents=[answer],
                metadatas=[
                    {
                        "created_at": time.time(),
                        "fingerprint": fingerprint,
                        "owner_key": owner_key,
                        "document_id": document_id or "",
                        "model": model,
                        "status": status,
                        "sources_json": json.dumps(
                            [source.model_dump() for source in sources]
                        ),
                    }
                ],
            )
            self._prune(max_age_hours)
        except Exception:
            logger.warning("Answer cache store failed", exc_info=True)

    def clear(self) -> None:
        """Remove every cached answer (corpus changed). Fail-open."""
        try:
            existing = self._collection.get()
            ids = existing.get("ids") or []
            if ids:
                self._collection.delete(ids=ids)
        except Exception:
            logger.warning("Answer cache clear failed", exc_info=True)

    def _prune(self, max_age_hours: float) -> None:
        """Drop entries older than max_age_hours (called after each store)."""
        cutoff = time.time() - max_age_hours * 3600
        try:
            existing = self._collection.get(include=["metadatas"])
            ids = existing.get("ids") or []
            metadatas = existing.get("metadatas") or []
            expired = [
                vector_id
                for vector_id, metadata in zip(ids, metadatas, strict=False)
                if float((metadata or {}).get("created_at", 0.0)) < cutoff
            ]
            if expired:
                self._collection.delete(ids=expired)
        except Exception:
            logger.warning("Answer cache prune failed", exc_info=True)


@lru_cache(maxsize=2)
def _build_answer_cache(path: str, collection_name: str) -> AnswerCache:
    return AnswerCache(Path(path), collection_name)


def get_answer_cache() -> AnswerCache:
    """Dependency entry point; one cached instance per (path, collection)."""
    settings = get_settings()
    return _build_answer_cache(str(settings.chroma_path), settings.chat_cache_collection)


def invalidate_answer_cache() -> None:
    """Wipe the cache after a corpus change; never raises.

    Called on ingest completion and document deletion. A no-op when the
    feature is disabled, so the default path never touches Chroma. Failure-
    path ingests are covered by the fingerprint change alone (entries become
    unreachable), so this is hygiene, not correctness.
    """
    settings = get_settings()
    if not settings.chat_cache_enabled:
        return
    try:
        get_answer_cache().clear()
    except Exception:
        logger.warning("Answer cache invalidation failed", exc_info=True)
