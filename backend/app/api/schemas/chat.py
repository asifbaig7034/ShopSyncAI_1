"""Validated JSON contract for the Business Owner's UC-2 chat."""

from pydantic import BaseModel, Field, field_validator


class OwnerChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Write a request before sending it.")
        return cleaned


class OwnerChatResponse(BaseModel):
    conversation_id: str
    reply: str
    routed_agents: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    mode: str
    warnings: list[str] = Field(default_factory=list)
    approval_request_id: str | None = None


class OwnerChatHistoryItem(BaseModel):
    request: str
    result: str
    routed_agents: list[str] = Field(default_factory=list)
    mode: str
    warnings: list[str] = Field(default_factory=list)
    approval_request_id: str | None = None
    created_at: str


class OwnerChatHistoryResponse(BaseModel):
    conversation_id: str
    messages: list[OwnerChatHistoryItem]
