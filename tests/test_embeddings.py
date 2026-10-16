from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError, RateLimitError

from app.api.documents import get_embedder
from app.config import Settings
from app.services.embeddings import Embedder
from app.services.errors import EmbeddingNotConfiguredError, EmbeddingServiceError
from fastapi import HTTPException


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


def _auth_error() -> AuthenticationError:
    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx.Response(401, request=request)
    return AuthenticationError("bad key", response=response, body=None)


class ScriptedClient:
    """Fake OpenAI client: pops one scripted action per call; defaults to echo.

    Echo responses encode each input's length as its vector and return items in
    reverse index order, so tests can prove output is re-sorted to input order.
    """

    def __init__(self, script: list | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[dict] = []
        self.embeddings = self

    def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
        self.calls.append({"model": model, "input": list(input)})
        action = self.script.pop(0) if self.script else "ok"
        if isinstance(action, Exception):
            raise action
        items = [
            SimpleNamespace(index=i, embedding=[float(len(text))])
            for i, text in enumerate(input)
        ]
        items.reverse()
        return SimpleNamespace(data=items)


def make_embedder(client=None, **overrides) -> tuple[Embedder, ScriptedClient]:
    script = overrides.pop("script", None)
    if client is None:
        client = ScriptedClient(script)
    defaults = dict(
        api_key="test-key",
        model="test-model",
        batch_size=100,
        max_retries=3,
        client=client,
        sleep=lambda _delay: None,
    )
    defaults.update(overrides)
    return Embedder(**defaults), client


class TestEmbedder:
    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(EmbeddingNotConfiguredError):
            Embedder(api_key=None, model="test-model")

    def test_empty_input_makes_no_api_calls(self) -> None:
        embedder, client = make_embedder()
        assert embedder.embed([]) == []
        assert client.calls == []

    def test_batching_preserves_input_order(self) -> None:
        texts = ["a", "bb", "ccc", "dddd", "eeeee"]
        embedder, client = make_embedder(batch_size=2)

        vectors = embedder.embed(texts)

        assert [call["input"] for call in client.calls] == [
            ["a", "bb"],
            ["ccc", "dddd"],
            ["eeeee"],
        ]
        assert all(call["model"] == "test-model" for call in client.calls)
        # reverse-ordered responses must be re-sorted to input order
        assert vectors == [[1.0], [2.0], [3.0], [4.0], [5.0]]

    def test_transient_error_retries_then_succeeds(self) -> None:
        sleeps: list[float] = []
        embedder, client = make_embedder(
            script=[_rate_limit_error()], sleep=sleeps.append
        )

        vectors = embedder.embed(["hello"])

        assert vectors == [[5.0]]
        assert len(client.calls) == 2
        assert sleeps == [1.0]

    def test_retry_exhaustion_raises_after_bounded_attempts(self) -> None:
        sleeps: list[float] = []
        embedder, client = make_embedder(
            script=[_rate_limit_error(), _rate_limit_error(), _rate_limit_error()],
            max_retries=3,
            sleep=sleeps.append,
        )

        with pytest.raises(EmbeddingServiceError, match="after 3 attempts"):
            embedder.embed(["hello"])

        assert len(client.calls) == 3
        assert sleeps == [1.0, 2.0]

    def test_auth_error_fails_immediately_without_retry(self) -> None:
        embedder, client = make_embedder(script=[_auth_error()])

        with pytest.raises(EmbeddingServiceError, match="status 401"):
            embedder.embed(["hello"])

        assert len(client.calls) == 1
        assert embedder.model == "test-model"

    def test_mismatched_response_count_raises(self) -> None:
        class ShortClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.embeddings = self

            def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
                self.calls.append({"model": model, "input": list(input)})
                return SimpleNamespace(
                    data=[SimpleNamespace(index=0, embedding=[1.0])]
                )

        embedder, _client = make_embedder(client=ShortClient())

        with pytest.raises(EmbeddingServiceError, match="returned 1 vectors for 2"):
            embedder.embed(["aa", "bb"])


class TestEmbedderDependency:
    def test_missing_key_maps_to_503(self) -> None:
        settings = Settings(openai_api_key=None)
        with pytest.raises(HTTPException) as excinfo:
            get_embedder(settings)
        assert excinfo.value.status_code == 503
        assert "OPENAI_API_KEY" in str(excinfo.value.detail)
