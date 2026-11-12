"""Customer conversation requests. The customer simulator is owner-only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DemoCustomerMessage(BaseModel):
    customer_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    external_id: str | None = Field(default=None, max_length=180)

    @field_validator("customer_id", "text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip()

    @field_validator("external_id")
    @classmethod
    def external_id_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("external_id cannot be blank")
        return value.strip() if value is not None else None


class OwnerConversationMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    external_id: str | None = Field(default=None, max_length=180)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value.strip()

    @field_validator("external_id")
    @classmethod
    def external_id_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("external_id cannot be blank")
        return value.strip() if value is not None else None


class HandoverRequest(BaseModel):
    mode: Literal["human", "bot"]
