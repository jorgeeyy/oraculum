import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.schemas.retrieval import RetrievalResult
from app.services.embeddings import Embedder
from app.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalOutcome:
    results: list[RetrievalResult]
    elapsed_ms: float


class EmptyQuestionError(ValueError):
    """The question is empty after trimming."""


def retrieve_documents(
    question: str,
    *,
    embedder: Embedder,
    store: VectorStore,
    top_k: int = 5,
    document_id: str | None = None,
) -> RetrievalOutcome:
    """Find the chunks most similar to a natural-language question.

    The query is embedded with the same model used for indexing, so query and
    index vectors live in one shared space. Scores are cosine similarity and
    are useful for ranking, but they are not calibrated answer confidence.
    """
    trimmed = question.strip()
    if not trimmed:
        raise EmptyQuestionError("question must not be blank")

    started = time.perf_counter()
    query_vector = embedder.embed([trimmed])[0]
    hits = store.query(query_vector, top_k=top_k, document_id=document_id)
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
        "retrieval top_k=%d hits=%d elapsed_ms=%.0f document_id=%s question=%r",
        top_k,
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
