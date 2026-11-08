"""Owner-only UC-1 connection requests; never serialize access tokens."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class WhatsAppConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    waba_id: str = Field(pattern=r"^[0-9]{5,25}$")
    phone_number_id: str = Field(pattern=r"^[0-9]{5,25}$")
    access_token: SecretStr

    @field_validator("waba_id", "phone_number_id", mode="before")
    @classmethod
    def trim_ids(cls, value):
        return value.strip() if isinstance(value, str) else value


class WhatsAppStatus(BaseModel):
    status: Literal["not_connected", "meta_read_verified", "needs_attention"]
    waba_id: str | None = None
    phone_number_id: str | None = None
    display_phone_number: str | None = None
    verified_name: str | None = None
    verified_at: str | None = None
