"""Shared, cached OpenAI client.

The OpenAI SDK's client owns an HTTP connection pool. Building a new client
per request pays a full TLS handshake on every call, which on Windows can
add seconds to first-byte latency. One cached client per (api_key, timeout)
lets connections be reused across requests.
"""

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=4)
def _build_client(api_key: str, timeout: float) -> Any:
    from openai import OpenAI

    # max_retries=0: Embedder/Generator own the retry policy explicitly.
    return OpenAI(api_key=api_key, max_retries=0, timeout=timeout)


def get_openai_client(api_key: str, timeout: float) -> Any:
    return _build_client(api_key, timeout)


def clear_openai_client_cache() -> None:
    """Test hook: drop cached clients so key/timeout changes take effect."""
    _build_client.cache_clear()
