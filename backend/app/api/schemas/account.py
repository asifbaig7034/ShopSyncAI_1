"""UC-1 owner account requests and public responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator, model_validator


class OwnerSignup(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    business_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("name", "business_name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Enter at least two visible characters.")
        return cleaned


class OwnerLogin(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    code: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=12, max_length=128)


class OwnerInfo(BaseModel):
    id: str
    name: str
    email: str
    business_name: str
    role: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    owner: OwnerInfo


class BusinessProfileSettings(BaseModel):
    name: str
    contact: str = ""
    currency: str = "PKR"
    language: Literal["English", "Urdu", "Roman Urdu"] = "English"


class AgentSettings(BaseModel):
    customer_commerce: StrictBool = False
    inventory_logistics: StrictBool = False


class OwnerSettingsResponse(BaseModel):
    business_profile: BusinessProfileSettings
    agent_settings: AgentSettings


class BusinessProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=120)
    contact: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    language: Literal["English", "Urdu", "Roman Urdu"] | None = None

    @field_validator("name", "contact", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def nonempty_partial_update(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Provide a non-null business profile field to update.")
        return self


class AgentSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_commerce: StrictBool | None = None
    inventory_logistics: StrictBool | None = None

    @model_validator(mode="after")
    def nonempty_partial_update(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Provide a non-null agent switch to update.")
        return self


class OwnerSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_profile: BusinessProfileUpdate | None = None
    agent_settings: AgentSettingsUpdate | None = None

    @model_validator(mode="after")
    def nonempty_partial_update(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Provide profile fields or agent switches to update.")
        return self
