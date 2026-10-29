import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().casefold()
        if not _EMAIL_RE.match(email):
            raise ValueError("email must be a valid address")
        return email

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str | None) -> str | None:
        return value.strip() if value else None


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().casefold()


class UserOut(BaseModel):
    """Public account view; never exposes password material."""

    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    created_at: datetime
    last_login_at: datetime | None
