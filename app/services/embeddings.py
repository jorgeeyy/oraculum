import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)

from app.services.errors import EmbeddingNotConfiguredError, EmbeddingServiceError

logger = logging.getLogger(__name__)

# Error types worth retrying: rate limits, network blips, timeouts, and 5xx.
_TRANSIENT_ERRORS = (RateLimitError, APIConnectionError, APITimeoutError)


class Embedder:
    """Turns text into vectors with the OpenAI embeddings API.

    Retries are bounded and exponential: only transient failures (rate limits,
    timeouts, connection errors, 5xx) are retried; auth or bad-request errors
    fail immediately. The OpenAI SDK's own retries are disabled so this class
    owns the retry policy and it stays observable and testable.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        batch_size: int = 100,
        max_retries: int = 3,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if client is None:
            if not api_key:
                raise EmbeddingNotConfiguredError(
                    "OPENAI_API_KEY is not configured; cannot embed documents."
                )
            client = OpenAI(api_key=api_key, max_retries=0)
        self._client = client
        self._model = model
        self._batch_size = max(1, batch_size)
        self._max_retries = max(1, max_retries)
        self._sleep = sleep
        self._tokens_in = 0
        self._retries = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def tokens_in(self) -> int:
        """Prompt tokens consumed since this embedder was created."""
        return self._tokens_in

    @property
    def retry_count(self) -> int:
        """Transient retries since this embedder was created (observability)."""
        return self._retries

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in bounded batches, preserving input order."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        attempts = 0
        while True:
            try:
                response = self._client.embeddings.create(
                    model=self._model, input=batch
                )
                self._record_usage(response)
                return self._ordered_vectors(response, expected=len(batch))
            except _TRANSIENT_ERRORS as exc:
                error: Exception = exc
            except APIStatusError as exc:
                if exc.status_code is None or exc.status_code < 500:
                    raise EmbeddingServiceError(
                        f"Embedding request failed with status {exc.status_code}."
                    ) from exc
                error = exc
            except OpenAIError as exc:
                raise EmbeddingServiceError(
                    f"Embedding request failed: {type(exc).__name__}."
                ) from exc

            attempts += 1
            if attempts >= self._max_retries:
                raise EmbeddingServiceError(
                    f"Embedding request failed after {attempts} attempts: "
                    f"{type(error).__name__}."
                ) from error

            self._retries += 1
            delay = 2 ** (attempts - 1)
            logger.warning(
                "Transient embedding failure (attempt %d/%d): %s; retrying in %ss",
                attempts,
                self._max_retries,
                type(error).__name__,
                delay,
            )
            self._sleep(delay)

    def _record_usage(self, response: Any) -> None:
        """Accumulate prompt tokens (defensive: usage may be absent in tests)."""
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        if isinstance(prompt_tokens, int):
            self._tokens_in += prompt_tokens

    @staticmethod
    def _ordered_vectors(response: Any, *, expected: int) -> list[list[float]]:
        """Collect vectors sorted by index and verify the API returned them all."""
        items = sorted(response.data, key=lambda item: item.index)
        vectors = [item.embedding for item in items]
        if len(vectors) != expected:
            raise EmbeddingServiceError(
                f"Embedding API returned {len(vectors)} vectors for {expected} inputs."
            )
        if any(not vector for vector in vectors):
            raise EmbeddingServiceError("Embedding API returned an empty vector.")
        return vectors
