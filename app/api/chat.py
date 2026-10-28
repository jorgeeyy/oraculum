import json
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth import api_key_fingerprint
from app.api.deps import get_db_session, open_db_session
from app.api.documents import get_embedder
from app.api.retrieve import resolve_top_k
from app.config import Settings, get_settings
from app.db import repositories
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    FeedbackResponse,
    Source,
)
from app.services.answer_cache import AnswerCache, CacheHit, get_answer_cache
from app.services.bm25_index import Bm25Index, get_bm25_index
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
from app.services.retrieval import EmptyQuestionError, RetrievalOutcome, retrieve_documents
from app.services.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_TITLE_CHARS = 60


def _sse(event: str, data: dict) -> str:
    """One Server-Sent Events frame (single-line JSON payload)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def get_answer_cache_dep(settings: Settings = Depends(get_settings)) -> AnswerCache | None:
    """Answer cache when the flag is on, else None (the feature is opt-in).

    Fails open: a cache that cannot be opened disables caching for the
    request rather than failing it.
    """
    if not settings.chat_cache_enabled:
        return None
    try:
        return get_answer_cache()
    except Exception:
        logger.warning("Answer cache unavailable; proceeding without it", exc_info=True)
        return None


def _corpus_fingerprint() -> str:
    """Hash of the current corpus; ties cached answers to the evidence they used."""
    session = open_db_session()
    try:
        return repositories.corpus_fingerprint(session)
    finally:
        session.close()


@dataclass(frozen=True)
class AnswerCacheContext:
    """Cache handle plus the request-scoped facts a store call needs."""

    cache: AnswerCache
    fingerprint: str
    query_vector: list[float]


@dataclass(frozen=True)
class PreparedChat:
    """Shared pre-generation state for /chat and /chat/stream.

    cache_hit short-circuits both endpoints: when set, retrieval never ran
    and the answer comes from the cache entry instead of the LLM.
    """

    retrieval: RetrievalOutcome
    history: list[dict] | None
    rewritten: bool
    expanded: int = 0
    cache_hit: CacheHit | None = None
    cache_context: AnswerCacheContext | None = None


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
) -> tuple[str, str] | None:
    """Save the exchange; ownership of an existing conversation is re-checked.

    The session opens only here so stateless /chat requests never touch the
    database. Returns (conversation_id, message_id) when the request opted
    into persistence, otherwise None (still stateless).
    """
    if "conversation_id" not in request.model_fields_set:
        return None
    owner = api_key_fingerprint(settings)
    conversation_id = request.conversation_id
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


def _prepare_chat(
    request: ChatRequest,
    *,
    embedder: Embedder,
    store: VectorStore,
    bm25: Bm25Index,
    generator: Generator,
    settings: Settings,
    cache: AnswerCache | None = None,
) -> PreparedChat:
    """Shared pre-generation phase for /chat and /chat/stream.

    Resolves top_k, loads conversation history (early 404), probes the
    semantic answer cache on the raw question (a hit short-circuits
    everything downstream), rewrites the follow-up into a standalone query
    when history exists, expands it into variants when enabled, and
    retrieves. Raises HTTPException on the same conditions as the plain
    endpoint. Every cache step fails open.
    """
    top_k = resolve_top_k(request.top_k, settings)

    # Multi-turn: load prior turns when the request targets an existing
    # conversation. Ownership is checked HERE (not only at persist time), so
    # a missing/foreign conversation 404s before any LLM spend.
    history: list[dict] | None = None
    if (
        "conversation_id" in request.model_fields_set
        and request.conversation_id is not None
    ):
        session = open_db_session()
        try:
            history = repositories.load_chat_history(
                session,
                request.conversation_id,
                api_key_hash=api_key_fingerprint(settings),
                limit=settings.chat_history_turns * 2,
            )
        finally:
            session.close()
        if history is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")

    # Semantic cache probe: embed the RAW question once (reused for retrieval
    # when the query is not rewritten) and look for a fresh, matching answered
    # entry against the current corpus fingerprint.
    cache_hit: CacheHit | None = None
    cache_context: AnswerCacheContext | None = None
    query_vector: list[float] | None = None
    probe_ms = 0.0
    if cache is not None:
        probe_started = time.perf_counter()
        try:
            query_vector = embedder.embed([request.question])[0]
            fingerprint = _corpus_fingerprint()
            cache_hit = cache.lookup(
                query_vector,
                fingerprint,
                document_id=request.document_id,
                model=generator.model,
                min_similarity=settings.chat_cache_min_similarity,
                max_age_hours=settings.chat_cache_max_age_hours,
            )
            if cache_hit is None:
                cache_context = AnswerCacheContext(
                    cache=cache, fingerprint=fingerprint, query_vector=query_vector
                )
        except Exception:
            logger.warning(
                "Answer cache probe failed; continuing uncached", exc_info=True
            )
            cache_hit = None
            cache_context = None
        finally:
            probe_ms = (time.perf_counter() - probe_started) * 1000

    if cache_hit is not None:
        logger.info(
            "chat cache hit similarity=%.3f question=%r",
            cache_hit.similarity,
            request.question[:60],
        )
        return PreparedChat(
            retrieval=RetrievalOutcome(results=[], elapsed_ms=probe_ms),
            history=history,
            rewritten=False,
            cache_hit=cache_hit,
        )

    # Follow-ups ("what about part-timers?") are rewritten into a standalone
    # query for retrieval only; the stored/displayed question stays raw.
    # Stateless requests have no history and are never rewritten.
    retrieval_query = request.question
    rewritten = False
    if history and settings.chat_query_rewrite:
        retrieval_query = generator.rewrite_query(request.question, history)
        rewritten = retrieval_query != request.question

    # Multi-query expansion runs on the standalone query (after the rewrite)
    # and applies to stateless questions too; [] on any failure.
    expanded_variants: tuple[str, ...] = ()
    if settings.chat_query_expansion:
        expanded_variants = tuple(
            generator.expand_query(
                retrieval_query, settings.chat_query_expansion_variants
            )
        )

    # Reuse the probe's embedding only when retrieval still targets the raw
    # question; a rewritten query embeds itself inside retrieve_documents.
    reusable_vector = (
        query_vector if (query_vector is not None and not rewritten) else None
    )

    try:
        retrieval = retrieve_documents(
            retrieval_query,
            embedder=embedder,
            store=store,
            top_k=top_k,
            document_id=request.document_id,
            bm25=bm25,
            hybrid=settings.retrieval_hybrid,
            rrf_k=settings.retrieval_rrf_k,
            variants=expanded_variants,
            query_vector=reusable_vector,
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
    return PreparedChat(
        retrieval=retrieval,
        history=history,
        rewritten=rewritten,
        expanded=len(expanded_variants),
        cache_context=cache_context,
    )


def _cached_response(
    request: ChatRequest,
    prepared: PreparedChat,
    *,
    settings: Settings,
    generator: Generator,
) -> ChatResponse:
    """Serve a cache hit as a ChatResponse; the exchange is still persisted."""
    hit = prepared.cache_hit
    assert hit is not None  # caller-checked
    saved = _persist_exchange(
        settings,
        request,
        answer=hit.answer,
        status=hit.status,
        sources=hit.sources,
        model=hit.model,
        retrieval_ms=round(prepared.retrieval.elapsed_ms, 1),
        generation_ms=0.0,
    )
    logger.info(
        "chat status=%s cached=true similarity=%.3f sources=%d "
        "retrieval_ms=%.0f question=%r",
        hit.status,
        hit.similarity,
        len(hit.sources),
        prepared.retrieval.elapsed_ms,
        request.question[:60],
    )
    return ChatResponse(
        question=request.question,
        answer=hit.answer,
        status=hit.status,
        sources=hit.sources,
        model=hit.model,
        retrieval_ms=round(prepared.retrieval.elapsed_ms, 1),
        generation_ms=0.0,
        conversation_id=saved[0] if saved else None,
        message_id=saved[1] if saved else None,
        cached=True,
        cache_similarity=round(hit.similarity, 4),
    )


def _store_in_cache(
    prepared: PreparedChat,
    request: ChatRequest,
    *,
    answer: str,
    status: str,
    sources: list[Source],
    model: str,
    settings: Settings,
) -> None:
    """Remember an answered response (never a refusal); fail-open."""
    context = prepared.cache_context
    if context is None or status != "answered":
        return
    try:
        context.cache.store(
            context.query_vector,
            question=request.question,
            answer=answer,
            status=status,
            sources=sources,
            model=model,
            fingerprint=context.fingerprint,
            document_id=request.document_id,
            max_age_hours=settings.chat_cache_max_age_hours,
        )
    except Exception:
        logger.warning("Answer cache store failed", exc_info=True)


@router.post("", response_model=ChatResponse, dependencies=[Depends(enforce_rate_limit)])
def chat(
    request: ChatRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    bm25: Bm25Index = Depends(get_bm25_index),
    generator: Generator = Depends(get_generator),
    cache: AnswerCache | None = Depends(get_answer_cache_dep),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    """Answer a question from retrieved evidence with resolved citations.

    A semantic-cache hit short-circuits retrieval and generation entirely.
    Otherwise, retrieval runs first; if nothing is found the answer is a
    refusal and the LLM is never called. When evidence exists, the model
    must cite it, and every citation is resolved against the real retrieved
    chunks - unknown citations are dropped and uncited answers are
    downgraded to insufficient_evidence.
    """
    prepared = _prepare_chat(
        request,
        embedder=embedder,
        store=store,
        bm25=bm25,
        generator=generator,
        settings=settings,
        cache=cache,
    )

    if prepared.cache_hit is not None:
        return _cached_response(request, prepared, settings=settings, generator=generator)

    retrieval = prepared.retrieval

    # No evidence: refuse deterministically without spending an LLM call.
    if not retrieval.results:
        saved = _persist_exchange(
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
            conversation_id=saved[0] if saved else None,
            message_id=saved[1] if saved else None,
        )

    try:
        outcome = generator.generate(request.question, retrieval.results, prepared.history)
    except GenerationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationServiceError as exc:
        logger.warning("Answer generation failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "The answer service is unavailable. Try again."},
        ) from exc

    logger.info(
        "chat status=%s sources=%d retrieval_ms=%.0f generation_ms=%.0f "
        "tokens_in=%d tokens_out=%d embed_retries=%d gen_retries=%d "
        "rewritten=%s expanded=%d question=%r",
        outcome.status,
        len(outcome.sources),
        retrieval.elapsed_ms,
        outcome.elapsed_ms,
        outcome.tokens_in,
        outcome.tokens_out,
        getattr(embedder, "retry_count", 0),
        getattr(generator, "retry_count", 0),
        prepared.rewritten,
        prepared.expanded,
        request.question[:60],
    )
    _store_in_cache(
        prepared,
        request,
        answer=outcome.answer,
        status=outcome.status,
        sources=outcome.sources,
        model=outcome.model,
        settings=settings,
    )
    saved = _persist_exchange(
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
        conversation_id=saved[0] if saved else None,
        message_id=saved[1] if saved else None,
    )


@router.post("/stream", dependencies=[Depends(enforce_rate_limit)])
def chat_stream(
    request: ChatRequest,
    embedder: Embedder = Depends(get_embedder),
    store: VectorStore = Depends(get_vector_store),
    bm25: Bm25Index = Depends(get_bm25_index),
    generator: Generator = Depends(get_generator),
    cache: AnswerCache | None = Depends(get_answer_cache_dep),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream an answer as Server-Sent Events.

    Event protocol (each frame: `event: <type>` + single-line JSON `data`):

    - `meta`  - retrieval finished; carries model + retrieval_ms + the
      cached/expanded flags so the UI can react before any answer text
      exists.
    - `delta` - incremental answer text (extracted from the model's JSON
      answer field; cosmetic only). A cache hit emits exactly one delta
      carrying the whole answer.
    - `done`  - the authoritative ChatResponse payload (validated citations,
      status, timings, conversation_id, cached). Clients MUST replace any
      streamed text with `done.answer`.
    - `error` - generation failed mid-stream; mirrors the plain endpoint's
      503 detail.

    Pre-stream failures (bad request, unknown conversation, retrieval
    outage) still return normal HTTP status codes because they happen
    before the response starts.
    """
    prepared = _prepare_chat(
        request,
        embedder=embedder,
        store=store,
        bm25=bm25,
        generator=generator,
        settings=settings,
        cache=cache,
    )
    retrieval = prepared.retrieval

    def event_source() -> Iterator[str]:
        yield _sse(
            "meta",
            {
                "model": generator.model,
                "retrieval_ms": round(retrieval.elapsed_ms, 1),
                "rewritten": prepared.rewritten,
                "expanded": prepared.expanded,
                "has_evidence": (
                    bool(prepared.cache_hit.sources)
                    if prepared.cache_hit is not None
                    else bool(retrieval.results)
                ),
                "cached": prepared.cache_hit is not None,
            },
        )

        # Cache hit: replay the stored answer as one delta, then done.
        if prepared.cache_hit is not None:
            hit = prepared.cache_hit
            saved = _persist_exchange(
                settings,
                request,
                answer=hit.answer,
                status=hit.status,
                sources=hit.sources,
                model=hit.model,
                retrieval_ms=round(retrieval.elapsed_ms, 1),
                generation_ms=0.0,
            )
            logger.info(
                "chat(stream) status=%s cached=true similarity=%.3f sources=%d "
                "retrieval_ms=%.0f question=%r",
                hit.status,
                hit.similarity,
                len(hit.sources),
                retrieval.elapsed_ms,
                request.question[:60],
            )
            yield _sse("delta", {"text": hit.answer})
            yield _sse(
                "done",
                ChatResponse(
                    question=request.question,
                    answer=hit.answer,
                    status=hit.status,
                    sources=hit.sources,
                    model=hit.model,
                    retrieval_ms=round(retrieval.elapsed_ms, 1),
                    generation_ms=0.0,
                    conversation_id=saved[0] if saved else None,
                    message_id=saved[1] if saved else None,
                    cached=True,
                    cache_similarity=round(hit.similarity, 4),
                ).model_dump(),
            )
            return

        # No evidence: refuse deterministically without spending an LLM call.
        if not retrieval.results:
            saved = _persist_exchange(
                settings,
                request,
                answer=INSUFFICIENT_ANSWER,
                status="insufficient_evidence",
                sources=[],
                model=generator.model,
                retrieval_ms=round(retrieval.elapsed_ms, 1),
                generation_ms=0.0,
            )
            yield _sse(
                "done",
                ChatResponse(
                    question=request.question,
                    answer=INSUFFICIENT_ANSWER,
                    status="insufficient_evidence",
                    sources=[],
                    model=generator.model,
                    retrieval_ms=round(retrieval.elapsed_ms, 1),
                    generation_ms=0.0,
                    conversation_id=saved[0] if saved else None,
                    message_id=saved[1] if saved else None,
                ).model_dump(),
            )
            return

        try:
            outcome = None
            for kind, payload in generator.generate_stream(
                request.question, retrieval.results, prepared.history
            ):
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                else:
                    outcome = payload
        except GenerationNotConfiguredError as exc:
            yield _sse("error", {"message": str(exc)})
            return
        except GenerationServiceError as exc:
            logger.warning("Streamed generation failed: %s", exc)
            yield _sse(
                "error",
                {"message": "The answer service is unavailable. Try again."},
            )
            return

        if outcome is None:  
            yield _sse(
                "error",
                {"message": "The answer service is unavailable. Try again."},
            )
            return

        logger.info(
            "chat(stream) status=%s sources=%d retrieval_ms=%.0f generation_ms=%.0f "
            "tokens_in=%d tokens_out=%d embed_retries=%d gen_retries=%d "
            "rewritten=%s expanded=%d question=%r",
            outcome.status,
            len(outcome.sources),
            retrieval.elapsed_ms,
            outcome.elapsed_ms,
            outcome.tokens_in,
            outcome.tokens_out,
            getattr(embedder, "retry_count", 0),
            getattr(generator, "retry_count", 0),
            prepared.rewritten,
            prepared.expanded,
            request.question[:60],
        )
        _store_in_cache(
            prepared,
            request,
            answer=outcome.answer,
            status=outcome.status,
            sources=outcome.sources,
            model=outcome.model,
            settings=settings,
        )
        saved = _persist_exchange(
            settings,
            request,
            answer=outcome.answer,
            status=outcome.status,
            sources=outcome.sources,
            model=outcome.model,
            retrieval_ms=round(retrieval.elapsed_ms, 1),
            generation_ms=round(outcome.elapsed_ms, 1),
        )
        yield _sse(
            "done",
            ChatResponse(
                question=request.question,
                answer=outcome.answer,
                status=outcome.status,
                sources=outcome.sources,
                model=outcome.model,
                retrieval_ms=round(retrieval.elapsed_ms, 1),
                generation_ms=round(outcome.elapsed_ms, 1),
                conversation_id=saved[0] if saved else None,
                message_id=saved[1] if saved else None,
            ).model_dump(),
        )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering so frames flush immediately.
            "X-Accel-Buffering": "no",
        },
    )


@router.put(
    "/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
    dependencies=[Depends(enforce_rate_limit)],
)
def set_message_feedback(
    message_id: uuid.UUID,
    payload: FeedbackRequest,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
) -> FeedbackResponse:
    """Set or clear 👍/👎 feedback on one assistant answer.

    Rating "up"/"down" records the vote, null clears it (toggle off). The
    message must belong to a conversation in this API-key scope, otherwise
    404 - feedback can never probe whether a message id exists elsewhere.
    """
    rating = payload.rating
    found, stored = repositories.set_message_feedback(
        session,
        str(message_id),
        api_key_hash=api_key_fingerprint(settings),
        rating=rating,
    )
    if not found:
        raise HTTPException(status_code=404, detail="Message not found.")
    return FeedbackResponse(message_id=str(message_id), feedback=stored)
