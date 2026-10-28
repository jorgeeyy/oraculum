from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.chat import Source


class ConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class MessageRecord(BaseModel):
    id: str
    role: str
    content: str
    status: str | None = None
    # Thumbs feedback on assistant answers: "up" / "down" / None.
    feedback: str | None = None
    sources: list[Source] = Field(default_factory=list)
    model: str | None = None
    retrieval_ms: float | None = None
    generation_ms: float | None = None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRecord]


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        if stripped := value.strip():
            return stripped
        else:
            raise ValueError("title must not be blank")
