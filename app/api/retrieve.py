import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.documents import get_embedder
from app.schemas.retrieval import RetrievalRequest, RetrievalResponse
from app.services.embeddings import Embedder
from app.services.errors import (
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    VectorStoreError,
)
from app.services.retrieval import EmptyQuestionError, retrieve_documents
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieve", tags=["retrieval"])


@router.post("", response_model=RetrievalResponse)
def retrieve(
    request: RetrievalRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
) -> RetrievalResponse:
    """Search the vector index for chunks similar to the question.

    Runs entirely against the embedding model and vector store - no LLM,
    no Postgres - so retrieval can be tested and debugged in isolation.
    """
    try:
        outcome = retrieve_documents(
            request.question,
            embedder=embedder,
            store=store,
            top_k=request.top_k,
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
        top_k=request.top_k,
        document_id=request.document_id,
        elapsed_ms=round(outcome.elapsed_ms, 1),
        results=outcome.results,
    )
