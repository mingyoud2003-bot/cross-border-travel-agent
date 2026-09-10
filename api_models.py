from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None,
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class ChatResponse(BaseModel):
    request_id: str
    session_id: str
    answer_type: Literal["text", "decision"]
    message: str
    decision: dict[str, Any] | None
    state: dict[str, Any]
    trace: dict[str, Any]


class SessionResponse(BaseModel):
    session_id: str
    created_at: str
    state: dict[str, Any]
    traces: list[dict[str, Any]]


class DeleteSessionResponse(BaseModel):
    deleted: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    model: str
    api_key_configured: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    database: Literal["ready"]
    api_key: Literal["configured"]
