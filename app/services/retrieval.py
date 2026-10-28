import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.schemas.retrieval import RetrievalResult
from app.services.bm25_index import Bm25Index, get_bm25_index
from app.services.embeddings import Embedder
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalOutcome:
    results: list[RetrievalResult]
    elapsed_ms: float


class EmptyQuestionError(ValueError):
    """The question is empty after trimming."""


def _as_hit(hit: dict) -> dict:
    """Normalize a BM25 row to the vector-hit shape {id, score, text, metadata}."""
    return {
        "id": hit["id"],
        "score": 0.0,
        "text": hit.get("text", ""),
        "metadata": {
            "document_id": hit.get("document_id", ""),
            "filename": hit.get("filename", ""),
            "page": hit.get("page", 0),
            "chunk_index": hit.get("chunk_index", -1),
        },
    }


def fuse_rankings(
    *rankings: Sequence[dict],
    rrf_k: int = 60,
    top_k: int = 5,
) -> list[dict]:
    """Merge any number of rankings with Reciprocal Rank Fusion.

    Each ranking votes for a chunk with 1/(rrf_k + rank); chunks found by
    more rankings win. Ranking entries may be vector hits ({id, score,
    text, metadata}) or BM25 rows ({id, text, document_id, ...}): the    latter are normalized to the vector-hit shape on arrival. Input dicts
    are never mutated (the vector store may hand back live objects); ties
    break on id so ordering is deterministic.
    """
    payloads: dict[str, dict] = {}
    scores: dict[str, float] = {}

    def vote(payload: dict, rank: int) -> None:
        key = str(payload["id"])
        if key not in payloads:
            payloads[key] = payload
        scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)

    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            payload = dict(hit) if "metadata" in hit else _as_hit(hit)
            vote(payload, rank)

    ranked = sorted(
        payloads.values(),
        key=lambda payload: (-scores[str(payload["id"])], str(payload["id"])),
    )[:top_k]
    return [{**payload, "score": scores[str(payload["id"])]} for payload in ranked]


def reciprocal_rank_fuse(
    vector_hits: Sequence[dict],
    bm25_hits: Sequence[dict],
    *,
    rrf_k: int = 60,
    top_k: int = 5,
) -> list[dict]:
    """Fuse two rankings (vector + BM25); thin wrapper over fuse_rankings."""
    return fuse_rankings(vector_hits, bm25_hits, rrf_k=rrf_k, top_k=top_k)


def _clean_variants(question: str, variants: Sequence[str]) -> list[str]:
    """Drop blank variants, exact duplicates, and copies of the question.

    Keeps the first occurrence of each so embedding batches never repeat a
    query the original already covers.
    """
    seen = {question}
    cleaned: list[str] = []
    for variant in variants:
        text = variant.strip()
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def retrieve_documents(
    question: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    top_k: int = 5,
    document_id: str | None = None,
    bm25: Bm25Index | None = None,
    hybrid: bool = False,
    rrf_k: int = 60,
    variants: Sequence[str] = (),
    query_vector: Sequence[float] | None = None,
) -> RetrievalOutcome:
    """Find the chunks most relevant to a natural-language question.

    The vector side ranks by cosine similarity; with hybrid=True a BM25 term
    search runs alongside it and both rankings are merged with Reciprocal
    Rank Fusion (scores then mean fused rank support, not similarity). A
    BM25 failure degrades to vector-only rather than failing the request -
    the write path is where consistency is enforced. Scores are for ranking
    order, never calibrated answer confidence.

    Multi-query expansion: each variant is embedded alongside the question
    in one batch, searched on both sides, and every ranking is fused - so a
    chunk matched by the question AND a variant outranks a single-side hit.
    `query_vector` reuses a precomputed embedding of the question (the
    cache probe already embeds it), skipping only that one embed call.
    """
    trimmed = question.strip()
    if not trimmed:
        raise EmptyQuestionError("question must not be blank")

    extra_queries = _clean_variants(trimmed, variants)

    started = time.perf_counter()
    if query_vector is None:
        vectors = embedder.embed([trimmed, *extra_queries])
    else:
        vectors = [list(query_vector)]
        if extra_queries:
            vectors.extend(embedder.embed(extra_queries))

    vector_rankings = [
        store.query(vector, top_k=top_k, document_id=document_id)
        for vector in vectors
    ]
    vector_hits = vector_rankings[0]

    bm25_rankings: list[list[dict]] = []
    bm25_count = 0
    if hybrid:
        if bm25 is None:
            bm25 = get_bm25_index()
        for query in [trimmed, *extra_queries]:
            try:
                hits = bm25.search(query, top_k=top_k, document_id=document_id)
            except Exception:
                logger.warning(
                    "BM25 search failed; falling back to vector-only", exc_info=True
                )
                hits = []
            bm25_rankings.append(hits)
            bm25_count += len(hits)

    if not extra_queries:
        # Legacy shape: one ranking per side - identical ordering to the
        # pre-expansion path (single non-hybrid ranking bypasses fusion).
        if hybrid:
            hits = reciprocal_rank_fuse(
                vector_hits, bm25_rankings[0], rrf_k=rrf_k, top_k=top_k
            )
        else:
            hits = list(vector_hits)
    else:
        rankings = (
            [*vector_rankings, *bm25_rankings] if hybrid else vector_rankings
        )
        hits = fuse_rankings(*rankings, rrf_k=rrf_k, top_k=top_k)
    elapsed_ms = (time.perf_counter() - started) * 1000

    results = [
        RetrievalResult(
            source_id=hit["id"],
            document_id=str(hit["metadata"].get("document_id", "")),
            filename=str(hit["metadata"].get("filename", "")),
            page=int(hit["metadata"].get("page", 0)),
            chunk_index=int(hit["metadata"].get("chunk_index", -1)),
            text=hit.get("text", ""),
            score=float(hit.get("score", 0.0)),
        )
        for hit in hits
    ]

    logger.info(
        "retrieval hybrid=%s top_k=%d variants=%d vector_hits=%d bm25_hits=%d "
        "results=%d elapsed_ms=%.0f document_id=%s question=%r",
        hybrid,
        top_k,
        len(extra_queries),
        len(vector_hits),
        bm25_count,
        len(results),
        elapsed_ms,
        document_id or "-",
        trimmed[:60],
    )
    return RetrievalOutcome(results=results, elapsed_ms=elapsed_ms)


def recall_at_k(
    relevant: Iterable[tuple], retrieved: Sequence[tuple], k: int
) -> float:
    """Fraction of relevant (filename, page) keys found within the top k hits.

    Returns 0.0 for questions with no relevant evidence (no-answer questions
    are scored separately by the evaluation script).
    """
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    found = relevant_set & set(retrieved[:k])
    return len(found) / len(relevant_set)


def reciprocal_rank(relevant: Iterable[tuple], retrieved: Sequence[tuple]) -> float:
    """1 / position of the first relevant hit; 0.0 if none is found."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    for position, key in enumerate(retrieved, start=1):
        if key in relevant_set:
            return 1.0 / position
    return 0.0
