from functools import lru_cache
from pathlib import Path

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
    pinecone_api_key: str | None = None
    pinecone_index_name: str | None = None
    database_url: str | None = None

    # Ingestion (Phase 1)
    chunk_size_tokens: int = 600
    chunk_overlap_tokens: int = 80
    max_file_size_bytes: int = 10 * 1024 * 1024
    uploads_dir: str = "data/uploads"

    @property
    def uploads_path(self) -> Path:
        """Absolute uploads directory; relative values resolve to the project root."""
        path = Path(self.uploads_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
