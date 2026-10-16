from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or a local .env file.

    All fields are optional at this stage so the API can start without real
    credentials. Required values will be enforced per-phase as integrations
    are added (e.g. OpenAI keys before embeddings, DATABASE_URL before DB use).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "rag-knowledge-assistant"
    openai_api_key: str | None = None
    pinecone_api_key: str | None = None
    pinecone_index_name: str | None = None
    database_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
