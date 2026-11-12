"""UC-4 requests, compatible with the tested standalone order prototype."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    price_cents: int = Field(ge=0, le=100_000_000)
    stock_qty: int = Field(ge=0, le=1_000_000)


class RestockIn(BaseModel):
    quantity: int = Field(gt=0, le=1_000_000)


class ItemIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=100)


class DraftIn(BaseModel):
    phone: str = Field(min_length=8, max_length=32)
    name: str = Field(default="", max_length=100)
    items: list[ItemIn] = Field(min_length=1, max_length=25)
    delivery_address: str = Field(min_length=1, max_length=500)
    payment_method: Literal["COD", "BANK_TRANSFER"] = "COD"
    source_key: str = Field(min_length=8, max_length=100)


class EditDraftIn(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=25)
    delivery_address: str = Field(min_length=1, max_length=500)
    payment_method: Literal["COD", "BANK_TRANSFER"] | None = None


class DecisionIn(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=500)


class ReturnIn(BaseModel):
    kind: Literal["RETURN", "REFUND"]
    reason: str = Field(min_length=1, max_length=500)


class InspectionIn(BaseModel):
    accept: bool
    reason: str = Field(default="", max_length=500)


class ChatIn(BaseModel):
    message_id: str = Field(min_length=8, max_length=100)
    phone: str = Field(min_length=8, max_length=32)
    name: str = Field(default="", max_length=100)
    text: str = Field(min_length=1, max_length=2000)
