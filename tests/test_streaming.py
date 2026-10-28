"""Streaming SSE (/chat/stream) - extractor, generator, and endpoint layers.

Three layers are covered:

1. AnswerStreamExtractor - incremental decoding of the JSON "answer" field
   from raw stream text (escapes, split escapes, key order, missing key).
2. Generator.generate_stream - deltas then an authoritative result, usage
   from the final chunk, retry-only-before-first-delta, repair of malformed
   output via one non-streamed call.
3. POST /chat/stream - event protocol (meta -> delta* -> done | error),
   HTTP status codes for pre-stream failures, persistence before `done`.
"""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError

from app.services.errors import GenerationServiceError
from app.services.generation import Generator, INSUFFICIENT_ANSWER
from app.services.stream_parse import AnswerStreamExtractor

from test_generation import make_hit

HISTORY = [
    {"role": "user", "content": "How many days of annual leave do I get?"},
    {"role": "assistant", "content": "Full-time employees receive 20 days."},
]

DOC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

STORE_HIT = {
    "id": f"{DOC_ID}#0",
    "score": 0.81,
    "text": "Employees receive 20 days of paid annual leave.",
    "metadata": {
        "document_id": DOC_ID,
        "filename": "handbook.md",
        "page": 1,
        "chunk_index": 0,
    },
}


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Decode an SSE body into [(event, data), ...] frames."""
    events: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        if not frame.strip():
            continue
        event, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        assert event is not None and data is not None, f"bad frame: {frame!r}"
        events.append((event, data))
    return events


# ---------------------------------------------------------------- extractor


class TestAnswerStreamExtractor:
    def test_nothing_emitted_before_answer_key(self) -> None:
        extractor = AnswerStreamExtractor()
        assert extractor.feed('{"status": "answered", ') == ""
        assert extractor.feed('"cited_aliases": ["S1"]') == ""
        assert not extractor.finished

    def test_value_decoded_incrementally(self) -> None:
        extractor = AnswerStreamExtractor()
        assert extractor.feed('{"answer": "Emp') == "Emp"
        assert extractor.feed("loyees ") == "loyees "
        assert extractor.feed('receive 20 days."') == "receive 20 days."
        assert extractor.finished
        assert extractor.feed('" ignored') == ""  # closed: later feeds ignored

    def test_key_whitespace_variants(self) -> None:
        extractor = AnswerStreamExtractor()
        assert extractor.feed('{ "answer"   :   "Hi there" }') == "Hi there"
        assert extractor.finished

    def test_key_order_does_not_break_decoding(self) -> None:
        extractor = AnswerStreamExtractor()
        payload = json.dumps(
            {"status": "answered", "cited_aliases": ["S1"], "answer": "Hello"}
        )
        assert extractor.feed(payload) == "Hello"

    def test_missing_key_emits_nothing(self) -> None:
        extractor = AnswerStreamExtractor()
        assert extractor.feed('{"status": "insufficient_evidence"}') == ""
        assert not extractor.finished  # caller just never gets deltas

    def test_char_by_char_with_escapes_and_surrogates(self) -> None:
        answer = 'He said "hi"\nRésumé 😀 \tend\\'
        payload = json.dumps({"answer": answer, "status": "answered"})
        extractor = AnswerStreamExtractor()
        got = "".join(extractor.feed(char) for char in payload)
        assert got == answer  # escapes/emoji decoded exactly once each
        assert extractor.finished

    def test_split_escape_is_not_emitted_until_decodable(self) -> None:
        extractor = AnswerStreamExtractor()
        assert extractor.feed('{"answer": "caf\\u00') == ""
        assert extractor.feed('e9 days"}') == "café days"

    def test_trailing_backslash_waits_for_next_feed(self) -> None:
        extractor = AnswerStreamExtractor()
        assert extractor.feed('{"answer": "line1\\') == ""
        assert extractor.feed('nline2"}') == "line1\nline2"

    def test_quoted_text_survives(self) -> None:
        extractor = AnswerStreamExtractor()
        payload = '{"answer": "say \\"hello\\" now"}'
        assert extractor.feed(payload) == 'say "hello" now'


# -------------------------------------------------- streaming fake + helpers


def _chunk(text: str | None = None) -> SimpleNamespace:
    delta = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def _usage_chunk() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


def answer_payload(
    answer: str = "Employees receive 20 days.",
    status: str = "answered",
    aliases: tuple = ("S1",),
) -> str:
    return json.dumps(
        {"answer": answer, "status": status, "cited_aliases": list(aliases)}
    )


def pieces(payload: str, size: int = 7) -> list[str]:
    return [payload[i : i + size] for i in range(0, len(payload), size)]


class StreamingChatClient:
    """Fake OpenAI client for streaming: script entries are lists of stream
    pieces (strings yield chunks, exceptions raise mid-stream) or a bare
    Exception (raised at create time). Non-streamed calls pop `repair`."""

    def __init__(self, script=None, repair=None) -> None:
        self.script = list(script or [])
        self.repair = list(repair or [answer_payload("repaired answer")])
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    def create(
        self,
        *,
        model,
        messages,
        response_format,
        temperature,
        max_tokens,
        stream=False,
        stream_options=None,
    ):
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "stream": stream,
                "stream_options": stream_options,
            }
        )
        if stream:
            action = self.script.pop(0) if self.script else pieces(answer_payload())
            if isinstance(action, Exception):
                raise action
            return self._chunks(action)
        action = self.repair.pop(0) if self.repair else answer_payload()
        if isinstance(action, Exception):
            raise action
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=action))]
        )

    @staticmethod
    def _chunks(stream_pieces):
        for piece in stream_pieces:
            if isinstance(piece, Exception):
                raise piece
            yield _chunk(piece)
        yield _usage_chunk()


def make_stream_generator(client, **overrides) -> Generator:
    defaults = dict(
        api_key="test-key",
        model="test-chat-model",
        max_tokens=500,
        temperature=0.0,
        timeout=5.0,
        max_retries=3,
        client=client,
        sleep=lambda _delay: None,
    )
    defaults.update(overrides)
    return Generator(**defaults)


# -------------------------------------------------- Generator.generate_stream


class TestGenerateStream:
    def test_deltas_then_authoritative_result_with_usage(self) -> None:
        client = StreamingChatClient(script=[pieces(answer_payload())])
        generator = make_stream_generator(client)

        events = list(generator.generate_stream("How many days?", [make_hit()]))

        kinds = [kind for kind, _ in events]
        assert kinds[-1] == "result"
        assert set(kinds[:-1]) == {"delta"}  # deltas first, exactly one result
        deltas = "".join(payload for kind, payload in events if kind == "delta")
        assert deltas == "Employees receive 20 days."

        result = events[-1][1]
        assert result.answer == deltas
        assert result.status == "answered"
        assert result.tokens_in == 11 and result.tokens_out == 7  # final chunk

        call = client.calls[0]
        assert call["stream"] is True
        assert call["stream_options"] == {"include_usage": True}

    def test_no_evidence_short_circuits_without_any_llm_call(self) -> None:
        client = StreamingChatClient()
        generator = make_stream_generator(client)

        events = list(generator.generate_stream("q?", []))

        assert [kind for kind, _ in events] == ["result"]
        assert events[0][1].status == "insufficient_evidence"
        assert events[0][1].answer == INSUFFICIENT_ANSWER
        assert client.calls == []

    def test_history_reaches_the_prompt(self) -> None:
        client = StreamingChatClient(script=[pieces(answer_payload())])
        generator = make_stream_generator(client)

        list(generator.generate_stream("What about part-timers?", [make_hit()], HISTORY))

        prompt = client.calls[0]["messages"][1]["content"]
        assert "Conversation so far" in prompt
        assert "What about part-timers?" in prompt

    def test_transient_error_before_first_delta_retries_once(self) -> None:
        client = StreamingChatClient(
            script=[_rate_limit_error(), pieces(answer_payload())]
        )
        generator = make_stream_generator(client)

        events = list(generator.generate_stream("q?", [make_hit()]))

        assert events[-1][0] == "result"
        deltas = "".join(p for k, p in events if k == "delta")
        assert deltas == "Employees receive 20 days."  # emitted exactly once
        assert sum(1 for c in client.calls if c["stream"]) == 2  # retried
        assert generator.retry_count == 1

    def test_transient_error_after_first_delta_fails_without_retry(self) -> None:
        # Text already on the wire: a retry would duplicate it, so the
        # generator must fail fast instead. The first piece must be real
        # answer-field content, otherwise no delta has actually been emitted.
        payload = answer_payload()
        client = StreamingChatClient(
            script=[[payload[:20], _rate_limit_error()]]
        )
        generator = make_stream_generator(client)

        with pytest.raises(GenerationServiceError):
            list(generator.generate_stream("q?", [make_hit()]))

        assert len(client.calls) == 1  # no retry, no repair attempt

    def test_malformed_stream_output_repaired_by_nonstreamed_call(self) -> None:
        truncated = '{"answer": "20 days'  # never closes the JSON object
        client = StreamingChatClient(
            script=[pieces(truncated)],
            repair=[answer_payload("Employees receive 20 days.")],
        )
        generator = make_stream_generator(client)

        events = list(generator.generate_stream("q?", [make_hit()]))

        result = events[-1][1]
        assert result.answer == "Employees receive 20 days."  # authoritative
        assert any(not c["stream"] for c in client.calls)  # repair happened


# ------------------------------------------------------------- /chat/stream


class TestChatStreamEndpoint:
    def test_event_order_and_done_is_authoritative(
        self, client, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post("/chat/stream", json={"question": "How many days?"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

        events = parse_sse(response.text)
        kinds = [kind for kind, _ in events]
        assert kinds[0] == "meta"
        assert kinds[-1] == "done"
        assert set(kinds) <= {"meta", "delta", "done", "error"}
        assert "error" not in kinds

        # Deltas concatenate to the answer; done carries the validated result.
        expected = fake_generator.result.answer
        assert "".join(d["text"] for k, d in events if k == "delta") == expected
        done = events[-1][1]
        assert done["answer"] == expected
        assert done["status"] == "answered"
        assert done["conversation_id"] is None  # stateless request

        # It used the streaming path, not the plain one.
        assert fake_generator.stream_calls
        assert fake_generator.calls == []

    def test_no_evidence_emits_meta_and_done_only(
        self, client, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = []
        response = client.post("/chat/stream", json={"question": "nothing here"})

        events = parse_sse(response.text)
        assert [kind for kind, _ in events] == ["meta", "done"]
        done = events[-1][1]
        assert done["status"] == "insufficient_evidence"
        assert done["answer"] == INSUFFICIENT_ANSWER
        assert fake_generator.stream_calls == []
        assert fake_generator.calls == []

    def test_generation_failure_emits_error_event(
        self, client, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        fake_generator.fail = GenerationServiceError("model unavailable")

        response = client.post("/chat/stream", json={"question": "How many days?"})

        assert response.status_code == 200  # headers already sent
        events = parse_sse(response.text)
        assert [kind for kind, _ in events] == ["meta", "error"]
        error = events[-1][1]
        assert error["message"]  # generic; internals stay in the logs

    def test_pre_stream_failures_keep_normal_http_codes(
        self, client, fake_vector_store, fake_generator
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        unknown = client.post(
            "/chat/stream",
            json={"question": "hi", "conversation_id": "11111111-2222-3333-4444-555555555555"},
        )
        assert unknown.status_code == 404
        assert fake_generator.stream_calls == []

        invalid = client.post("/chat/stream", json={"question": "   "})
        assert invalid.status_code == 422

        empty = client.post("/chat/stream", json={})
        assert empty.status_code == 422

    def test_stream_persists_the_exchange_before_done(
        self, client, db_conn, fake_vector_store, fake_generator, fake_embedder
    ) -> None:
        fake_vector_store.query_result = [STORE_HIT]
        response = client.post(
            "/chat/stream",
            json={"question": "How many days?", "conversation_id": None},
        )

        events = parse_sse(response.text)
        done = events[-1][1]
        conversation_id = done["conversation_id"]
        assert conversation_id

        # Persisted before `done`: the conversation is immediately readable.
        conversation = client.get(f"/conversations/{conversation_id}")
        assert conversation.status_code == 200
        messages = conversation.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == fake_generator.result.answer

        # Follow-up on the streamed conversation sees the stored history.
        fake_generator.rewrite_result = "part-time employees annual leave"
        follow_up = client.post(
            "/chat/stream",
            json={"question": "What about part-timers?", "conversation_id": conversation_id},
        )
        follow_events = parse_sse(follow_up.text)
        assert follow_events[0] == (
            "meta",
            {
                "model": fake_generator.model,
                "retrieval_ms": follow_events[0][1]["retrieval_ms"],
                "rewritten": True,
                "has_evidence": True,
            },
        )
        assert fake_generator.rewrite_calls[-1]["question"] == "What about part-timers?"

        client.delete(f"/conversations/{conversation_id}")
