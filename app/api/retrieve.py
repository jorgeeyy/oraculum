import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import (
    AuthActor,
    document_owner_filter,
    get_actor,
)
from app.api.deps import open_db_session
from app.api.documents import get_embedder
from app.config import Settings, get_settings
from app.db import repositories
from app.schemas.retrieval import RetrievalRequest, RetrievalResponse
from app.services.bm25_index import Bm25Index, get_bm25_index
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
    bm25: Bm25Index = Depends(get_bm25_index),
    settings: Settings = Depends(get_settings),
    actor: AuthActor = Depends(get_actor),
) -> RetrievalResponse:
    """Search the index for chunks relevant to the question.

    Hybrid by default: vector similarity and BM25 term matching fused with
    Reciprocal Rank Fusion. No LLM - retrieval can be tested and debugged
    in isolation. Scoped to the caller's visible documents (shared corpus +
    own uploads); Postgres is only touched for session actors, so the
    API-key path stays as lean as before.
    """
    top_k = resolve_top_k(request.top_k, settings)

    # Per-user scoping: only this actor's visible documents are searchable.
    owner = document_owner_filter(actor)
    visible: set[str] | None = None
    if owner is not repositories.UNSCOPED or request.document_id is not None:
        session = open_db_session()
        try:
            visible = repositories.visible_document_ids(session, owner=owner)
            if request.document_id is not None:
                record = repositories.get_document(
                    session, request.document_id, owner=owner
                )
                if record is None:
                    raise HTTPException(
                        status_code=404, detail="Document not found."
                    )
        finally:
            session.close()

    try:
        outcome = retrieve_documents(
            request.question,
            embedder=embedder,
            store=store,
            top_k=top_k,
            document_id=request.document_id,
            allowed_document_ids=visible,
            bm25=bm25,
            hybrid=settings.retrieval_hybrid,
            rrf_k=settings.retrieval_rrf_k,
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
