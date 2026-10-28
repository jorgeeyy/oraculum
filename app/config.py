from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor all relative paths to the repository root, not the process working
# directory. A server started from any directory must see the same .env and
# the same uploads folder, otherwise records in the database and files on
# disk silently diverge.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or a local .env file.

    All fields are optional at this stage so the API can start without real
    credentials. Required values will be enforced per-phase as integrations
    are added (e.g. OpenAI keys before embeddings, DATABASE_URL before DB use).
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "rag-knowledge-assistant"
    openai_api_key: str | None = None
    database_url: str | None = None

    # Ingestion (Phase 1)
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 80
    max_file_size_bytes: int = 10 * 1024 * 1024
    uploads_dir: str = "data/uploads"

    # Embeddings and vector indexing (Phase 2)
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 100
    embedding_max_retries: int = 3
    embedding_timeout_seconds: float = 30.0
    chroma_dir: str = "data/chroma"
    vector_collection: str = "documents"

    # Retrieval (Phase 3)
    # Server-wide default for /retrieve and /chat; a request may override
    # per call via its optional top_k field. Bounded so a bad deploy value
    # fails at startup instead of at request time.
    # 8: with 5 the answer to a term-specific question could sit at rank 6
    # outside the cutoff (observed on the Acme policy corpus).
    retrieval_top_k: int = Field(default=8, ge=1, le=20)
    # Hybrid term+vector search: BM25 (SQLite FTS5) ranks are fused with the
    # vector ranks via Reciprocal Rank Fusion. False = legacy pure-vector path.
    retrieval_hybrid: bool = True
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    bm25_db_path: str = "data/bm25.db"

    # Answer generation (Phase 4)
    chat_model: str = "gpt-4o-mini"
    chat_max_tokens: int = 500
    chat_temperature: float = 0.0
    chat_timeout_seconds: float = 30.0
    chat_max_retries: int = 3

    # Multi-turn chat (pre-Phase 7)
    # Prior user/assistant turns replayed into the prompt when a request
    # carries a conversation_id. 0 disables history (stateless behaviour).
    # Bounded so an old thread cannot blow the context window.
    chat_history_turns: int = Field(default=4, ge=0, le=20)
    # Rewrite follow-up questions ("what about part-timers?") into a
    # standalone retrieval query - only when history is present; stateless
    # questions are already standalone and are never rewritten. On failure
    # the raw question is used, so this can never break a request.
    chat_query_rewrite: bool = True

    # Semantic answer cache (off until eval-validated).
    # A question whose embedding is at least min_similarity cosine-similar
    # to a cached one - within max_age_hours, against an unchanged corpus
    # fingerprint, same document filter and same model - is answered from
    # cache: no retrieval, no LLM call. Only "answered" responses are ever
    # stored (refusals cost nothing to regenerate and caching them could
    # hide newly indexed evidence). Any cache failure skips the cache and
    # the request continues down the normal path.
    chat_cache_enabled: bool = False
    chat_cache_min_similarity: float = Field(default=0.97, ge=0.0, le=1.0)
    chat_cache_max_age_hours: float = Field(default=24.0, gt=0.0)
    chat_cache_collection: str = "chat_cache"

    # Multi-query expansion (off until eval-validated).
    # The retrieval query is rephrased into N variants; original + variants
    # are embedded in one batch, searched on both the vector store and BM25,
    # and fused with RRF. Runs after the follow-up rewrite and applies to
    # stateless questions too. On any failure it degrades to the original
    # query alone, so it can never break a request.
    chat_query_expansion: bool = False
    chat_query_expansion_variants: int = Field(default=2, ge=1, le=5)

    # Reliability and security (Phase 5)
    rate_limit_per_minute: int = 60  # 0 disables rate limiting
    api_key: str | None = None  # when set, all non-health endpoints require it

    # Logging: rotating file in addition to stderr. Set LOG_FILE= (empty) to
    # disable file logging entirely.
    log_file: str = "data/logs/oraculum.log"

    # Frontend (dev server origins; production build is same-origin)
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    @property
    def uploads_path(self) -> Path:
        """Absolute uploads directory; relative values resolve to the project root."""
        return self._project_path(self.uploads_dir)

    @property
    def chroma_path(self) -> Path:
        """Absolute ChromaDB persistence directory anchored to the project root."""
        return self._project_path(self.chroma_dir)

    @property
    def bm25_path(self) -> Path:
        """Absolute SQLite FTS5 (BM25) index file anchored to the project root."""
        return self._project_path(self.bm25_db_path)

    @property
    def log_file_path(self) -> Path:
        """Absolute log-file path anchored to the project root."""
        return self._project_path(self.log_file)

    @staticmethod
    def _project_path(value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
