import uuid as uuid_module
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # None = use the server default (RETRIEVAL_TOP_K); a value must be 1-20.
    top_k: int | None = Field(default=None, ge=1, le=20)
    document_id: str | None = None
    # Chat history: omit the field entirely for stateless Q&A (scripts,
    # curl). Send it explicitly to persist the exchange: null starts a new
    # conversation, a UUID appends to an existing one.
    conversation_id: str | None = None

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        if stripped := value.strip():
            return stripped
        else:
            raise ValueError("question must not be blank")

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

    @field_validator("conversation_id")
    @classmethod
    def validate_conversation_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            uuid_module.UUID(value)
        except ValueError as exc:
            raise ValueError("conversation_id must be a valid UUID") from exc
        return value


class Source(BaseModel):
    source_id: str
    document_id: str
    filename: str
    page: int
    chunk_index: int
    excerpt: str


class ChatResponse(BaseModel):
    question: str
    answer: str
    status: Literal["answered", "insufficient_evidence"]
    sources: list[Source] = Field(default_factory=list)
    model: str
    retrieval_ms: float
    generation_ms: float
    # Set whenever the request opted into persistence (conversation_id key
    # present); identifies the conversation the exchange was stored in.
    conversation_id: str | None = None
