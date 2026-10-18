import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.documents import get_embedder
from app.config import Settings, get_settings
from app.schemas.retrieval import RetrievalRequest, RetrievalResponse
from app.services.embeddings import Embedder
from app.services.errors import (
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    VectorStoreError,
)
from app.services.rate_limit import enforce_rate_limit
from app.services.retrieval import EmptyQuestionError, retrieve_documents
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieval"])


def resolve_top_k(requested: int | None, settings: Settings) -> int:
    """Per-request override wins; otherwise the configured server default."""
    return requested if requested is not None else settings.retrieval_top_k


@router.post("", response_model=RetrievalResponse, dependencies=[Depends(enforce_rate_limit)])
def retrieve(
    request: RetrievalRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    settings: Settings = Depends(get_settings),
) -> RetrievalResponse:
    """Search the vector index for chunks similar to the question.

    Runs entirely against the embedding model and vector store - no LLM,
    no Postgres - so retrieval can be tested and debugged in isolation.
    """
    top_k = resolve_top_k(request.top_k, settings)
    try:
        outcome = retrieve_documents(
            request.question,
            embedder=embedder,
            store=store,
            top_k=top_k,
            document_id=request.document_id,
        )
    except EmptyQuestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (EmbeddingNotConfiguredError, EmbeddingServiceError) as exc:
        logger.warning("Retrieval embedding failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "Could not embed the question. Try again."},
        ) from exc
    except VectorStoreError as exc:
        logger.warning("Retrieval query failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "Vector search is unavailable. Try again."},
        ) from exc

    return RetrievalResponse(
        question=request.question,
        top_k=top_k,
        document_id=request.document_id,
        elapsed_ms=round(outcome.elapsed_ms, 1),
        results=outcome.results,
    )
