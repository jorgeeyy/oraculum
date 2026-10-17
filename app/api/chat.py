import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.documents import get_embedder
from app.config import Settings, get_settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.embeddings import Embedder
from app.services.errors import (
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    GenerationNotConfiguredError,
    GenerationServiceError,
    VectorStoreError,
)
from app.services.generation import INSUFFICIENT_ANSWER, Generator
from app.services.retrieval import EmptyQuestionError, retrieve_documents
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def get_generator(settings: Settings = Depends(get_settings)) -> Generator:
    """Build the generator eagerly so a missing API key surfaces as 503, not 500."""
    try:
        return Generator(
            api_key=settings.openai_api_key,
            model=settings.chat_model,
            max_tokens=settings.chat_max_tokens,
            temperature=settings.chat_temperature,
            timeout=settings.chat_timeout_seconds,
            max_retries=settings.chat_max_retries,
        )
    except GenerationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    generator: Generator = Depends(get_generator),
) -> ChatResponse:
    """Answer a question from retrieved evidence with resolved citations.

    Retrieval runs first; if nothing is found the answer is a refusal and the
    LLM is never called. When evidence exists, the model must cite it, and
    every citation is resolved against the real retrieved chunks - unknown
    citations are dropped and uncited answers are downgraded to
    insufficient_evidence.
    """
    try:
        retrieval = retrieve_documents(
            request.question,
            embedder=embedder,
            store=store,
            top_k=request.top_k,
            document_id=request.document_id,
        )
    except EmptyQuestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (EmbeddingNotConfiguredError, EmbeddingServiceError) as exc:
        logger.warning("Chat retrieval embedding failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "Could not embed the question. Try again."},
        ) from exc
    except VectorStoreError as exc:
        logger.warning("Chat retrieval query failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "Vector search is unavailable. Try again."},
        ) from exc

    # No evidence: refuse deterministically without spending an LLM call.
    if not retrieval.results:
        return ChatResponse(
            question=request.question,
            answer=INSUFFICIENT_ANSWER,
            status="insufficient_evidence",
            sources=[],
            model=generator.model,
            retrieval_ms=round(retrieval.elapsed_ms, 1),
            generation_ms=0.0,
        )

    try:
        outcome = generator.generate(request.question, retrieval.results)
    except GenerationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationServiceError as exc:
        logger.warning("Answer generation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "The answer service is unavailable. Try again."},
        ) from exc

    logger.info(
        "chat status=%s sources=%d retrieval_ms=%.0f generation_ms=%.0f question=%r",
        outcome.status,
        len(outcome.sources),
        retrieval.elapsed_ms,
        outcome.elapsed_ms,
        request.question[:60],
    )
    return ChatResponse(
        question=request.question,
        answer=outcome.answer,
        status=outcome.status,
        sources=outcome.sources,
        model=outcome.model,
        retrieval_ms=round(retrieval.elapsed_ms, 1),
        generation_ms=round(outcome.elapsed_ms, 1),
    )
