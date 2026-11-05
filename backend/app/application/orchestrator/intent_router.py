"""Conservative, read-only routing for the UC-2 demo.

The configured LLM normally classifies an owner request. These local rules
keep the *demo* usable without a key and never authorize an action.
"""

from __future__ import annotations

import re

SUPPORTED_INTENTS = frozenset({"products", "stock", "orders"})
KNOWN_LABELS = SUPPORTED_INTENTS | {"risk", "unsupported"}

_WORDS = {
    "products": ("product", "products", "catalog", "catalogue", "item", "items", "price", "prices", "پروڈکٹ", "مصنوعات"),
    "stock": ("stock", "stocks", "inventory", "quantity", "available", "availability", "low-stock", "اسٹاک", "دستیاب"),
    "orders": ("order", "orders", "sales", "booking", "refund", "return", "آرڈر", "آرڈرز"),
    "risk": ("risk", "fraud", "suspicious", "خطرہ", "فراڈ"),
    "unsupported": ("competitor", "courier", "shipment", "instagram", "facebook", "marketing", "profit", "invoice", "social media", "publish", "post", "schedule"),
}


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) for word in words)


def detect_intents(message: str) -> list[str]:
    """Infer simple domains locally; this is labelled a demo, never an LLM."""
    text = message.casefold()
    return [name for name, words in _WORDS.items() if _contains(text, words)]


def validated_intents(labels: list[str] | None) -> list[str]:
    if not isinstance(labels, list):
        return []
    return list(dict.fromkeys(label for label in labels if isinstance(label, str) and label in KNOWN_LABELS))


def sensitive_action(message: str) -> str | None:
    """Recognize requests needing approval without performing them."""
    text = message.casefold()
    # Consider each clause independently: "refund order X and show status"
    # contains both a sensitive request and an ordinary read-only request.
    for clause in re.split(r"\s+(?:and|then|also)\s+|[,;]", text):
        if _contains(clause, ("show", "list", "view", "report", "status", "history", "check")):
            continue
        if re.search(r"\b(refund|reimburse)\b", clause):
            return "refund"
        if _contains(text, ("price", "prices", "قیمت")) and _contains(clause, ("change", "set", "update", "increase", "decrease", "raise", "lower", "بدل")):
            return "price_change"
        if _contains(clause, ("publish", "schedule", "شائع")) and _contains(text, ("post", "ad", "campaign", "instagram", "facebook", "شائع")):
            return "publish_post"
    return None
