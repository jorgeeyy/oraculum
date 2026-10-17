import uuid as uuid_module

from pydantic import BaseModel, Field, field_validator


class RetrievalRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    document_id: str | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            uuid_module.UUID(value)
        except ValueError as exc:
            raise ValueError("document_id must be a valid UUID") from exc
        return value


class RetrievalResult(BaseModel):
    source_id: str
    document_id: str
    filename: str
    page: int
    chunk_index: int
    text: str
    score: float = Field(
        description="Cosine similarity; a ranking signal, not calibrated confidence."
    )


class RetrievalResponse(BaseModel):
    question: str
    top_k: int
    document_id: str | None = None
    elapsed_ms: float
    results: list[RetrievalResult] = Field(default_factory=list)
