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
    retrieval_top_k: int = Field(default=5, ge=1, le=20)

    # Answer generation (Phase 4)
    chat_model: str = "gpt-4o-mini"
    chat_max_tokens: int = 500
    chat_temperature: float = 0.0
    chat_timeout_seconds: float = 30.0
    chat_max_retries: int = 3

    # Reliability and security (Phase 5)
    rate_limit_per_minute: int = 60  # 0 disables rate limiting
    api_key: str | None = None  # when set, all non-health endpoints require it

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

    @staticmethod
    def _project_path(value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
