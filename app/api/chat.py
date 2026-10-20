import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import api_key_fingerprint
from app.api.deps import open_db_session
from app.api.documents import get_embedder
from app.api.retrieve import resolve_top_k
from app.config import Settings, get_settings
from app.db import repositories
from app.schemas.chat import ChatRequest, ChatResponse, Source
from app.services.embeddings import Embedder
from app.services.errors import (
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    GenerationNotConfiguredError,
    GenerationServiceError,
    VectorStoreError,
)
from app.services.generation import INSUFFICIENT_ANSWER, Generator
from app.services.openai_client import get_openai_client
from app.services.rate_limit import enforce_rate_limit
from app.services.retrieval import EmptyQuestionError, retrieve_documents
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_TITLE_CHARS = 60


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
            client=(
                get_openai_client(settings.openai_api_key, settings.chat_timeout_seconds)
                if settings.openai_api_key
                else None
            ),
        )
    except GenerationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _conversation_title(question: str) -> str:
    """Truncate the first question on a word boundary for the sidebar."""
    if len(question) <= _TITLE_CHARS:
        return question
    cut = question[:_TITLE_CHARS].rsplit(" ", 1)[0]
    return f"{cut} ..."


def _persist_exchange(
    settings: Settings,
    request: ChatRequest,
    *,
    answer: str,
    status: str,
    sources: list[Source],
    model: str,
    retrieval_ms: float,
    generation_ms: float,
) -> str:
    """Save the exchange; ownership of an existing conversation is re-checked.

    The session opens only here so stateless /chat requests never touch the
    database.
    """
    owner = api_key_fingerprint(settings)
    conversation_id = (
        request.conversation_id if "conversation_id" in request.model_fields_set else None
    )
    session = open_db_session()
    try:
        if conversation_id is not None:
            existing = repositories.get_conversation(
                session, conversation_id, api_key_hash=owner
            )
            if existing is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
        try:
            return repositories.save_chat_exchange(
                session,
                conversation_id=conversation_id,
                title=_conversation_title(request.question),
                api_key_hash=owner,
                question=request.question,
                answer=answer,
                status=status,
                sources=sources,
                model=model,
                retrieval_ms=retrieval_ms,
                generation_ms=generation_ms,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc
    finally:
        session.close()


@router.post("", response_model=ChatResponse, dependencies=[Depends(enforce_rate_limit)])
def chat(
    request: ChatRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    generator: Generator = Depends(get_generator),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    """Answer a question from retrieved evidence with resolved citations.

    Retrieval runs first; if nothing is found the answer is a refusal and the
    LLM is never called. When evidence exists, the model must cite it, and
    every citation is resolved against the real retrieved chunks - unknown
    citations are dropped and uncited answers are downgraded to
    insufficient_evidence.
    """
    top_k = resolve_top_k(request.top_k, settings)
    try:
        retrieval = retrieve_documents(
            request.question,
            embedder=embedder,
            store=store,
            top_k=top_k,
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
        conversation_id = None
        if "conversation_id" in request.model_fields_set:
            conversation_id = _persist_exchange(
                settings,
                request,
                answer=INSUFFICIENT_ANSWER,
                status="insufficient_evidence",
                sources=[],
                model=generator.model,
                retrieval_ms=round(retrieval.elapsed_ms, 1),
                generation_ms=0.0,
            )
        return ChatResponse(
            question=request.question,
            answer=INSUFFICIENT_ANSWER,
            status="insufficient_evidence",
            sources=[],
            model=generator.model,
            retrieval_ms=round(retrieval.elapsed_ms, 1),
            generation_ms=0.0,
            conversation_id=conversation_id,
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
    conversation_id = None
    if "conversation_id" in request.model_fields_set:
        conversation_id = _persist_exchange(
            settings,
            request,
            answer=outcome.answer,
            status=outcome.status,
            sources=outcome.sources,
            model=outcome.model,
            retrieval_ms=round(retrieval.elapsed_ms, 1),
            generation_ms=round(outcome.elapsed_ms, 1),
        )
    return ChatResponse(
        question=request.question,
        answer=outcome.answer,
        status=outcome.status,
        sources=outcome.sources,
        model=outcome.model,
        retrieval_ms=round(retrieval.elapsed_ms, 1),
        generation_ms=round(outcome.elapsed_ms, 1),
        conversation_id=conversation_id,
    )
