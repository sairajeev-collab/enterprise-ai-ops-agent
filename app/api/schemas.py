"""API request/response models.

Pydantic validates every field crossing the trust boundary: enums are
constrained, free text is length-capped, and control characters are stripped
before anything reaches the LLM or the database. Response models are explicit so
we never accidentally serialize internal fields.
"""

from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import Channel, Priority, RequestType, RunStatus

# Control characters except tab/newline/carriage-return. Stripped from user text.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_SUBJECT = 255
MAX_BODY = 50_000


def _sanitize(value: str) -> str:
    return _CONTROL_CHARS.sub("", value).strip()


class TokenRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CreateRequest(BaseModel):
    channel: Channel
    subject: str = Field(default="", max_length=MAX_SUBJECT)
    body: str = Field(min_length=1, max_length=MAX_BODY)

    @field_validator("subject", "body")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _sanitize(value)

    @field_validator("body")
    @classmethod
    def _non_empty_after_clean(cls, value: str) -> str:
        if not value:
            raise ValueError("body must contain printable content")
        return value


class RequestAccepted(BaseModel):
    id: str
    status: RunStatus
    status_url: str


class ArtifactOut(BaseModel):
    kind: str
    ref: str
    payload: dict[str, object]
    created_at: dt.datetime


class RequestStatusResponse(BaseModel):
    id: str
    channel: Channel
    status: RunStatus
    request_type: RequestType | None = None
    priority: Priority | None = None
    confidence: float | None = None
    attempts: int
    error: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    artifacts: list[ArtifactOut] = Field(default_factory=list)
