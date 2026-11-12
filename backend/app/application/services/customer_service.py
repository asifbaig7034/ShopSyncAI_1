"""UC-3 local customer conversations, scoped to one signed-in brand owner.

The owner-only simulator shares UC-4's order ledger for product and order
answers. It neither accepts real WhatsApp webhooks nor sends external messages.
Per-conversation locks serialize local demo requests and owner takeovers in a
single API process. They do not synchronize across processes or make MongoDB
and SQLite one transactional database; multi-worker/live use needs a durable
coordination mechanism before it can be enabled.
"""

from __future__ import annotations

import hashlib
import re
from threading import RLock
from datetime import datetime, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from . import order_service


MAX_MESSAGES = 500
# Keep a bounded number of locks regardless of conversation count. Unrelated
# conversations in the same stripe can briefly queue behind an order command.
_STRIPES = tuple(RLock() for _ in range(64))


def _lock_for(cid: str) -> RLock:
    # The owner inbox also accepts arbitrary URL identifiers; invalid IDs must
    # still produce a scoped 404, not fail here before the Mongo lookup.
    digest = hashlib.sha256(cid.encode()).digest()
    return _STRIPES[int.from_bytes(digest[:4], "big") % len(_STRIPES)]


ORDER_COMMAND = re.compile(r"^(?:ORDER|EDIT|CONFIRM|STATUS|CANCEL|RETURN|REFUND)\b", re.I)
ORDER_INTENT = re.compile(r"\b(?:order|buy|purchase|refund|return|cancel|kharid|khareed|wapas|wapis|mangwa|mera order)\b|آرڈر|خرید|واپس|منسوخ", re.I)
HUMAN_INTENT = re.compile(r"\b(?:human|owner|manager|insaan|insan|malik|agent se)\b|انسان|مالک|نمائند", re.I)
COMPLAINT_INTENT = re.compile(r"\b(?:complaint|broken|damaged|wrong item|shikayat|kharab|naraz)\b|شکایت|خراب", re.I)
PRODUCT_INTENT = re.compile(r"\b(?:product|products|catalogue|catalog|option|options|recommend|suggest|dikhao|price|cost|stock|available|availability|qeemat|kimat|rate)\b|مصنوعات|تجویز|دکھائیں|قیمت|دستیاب|اسٹاک", re.I)

TEXT = {
    "greeting": {
        "en": "Hello! Ask about our products, prices and availability, or ask to speak to the owner.",
        "roman_ur": "Assalam o alaikum! Products, qeemat aur stock ke bare mein pooch sakte hain, ya owner se baat kar sakte hain.",
        "ur": "السلام علیکم! مصنوعات، قیمت اور دستیابی کے بارے میں پوچھیں، یا مالک سے بات کریں۔",
    },
    "missing": {
        "en": "Please share the product name or code. Available products: ",
        "roman_ur": "Product ka naam ya code bata dein. Available products: ",
        "ur": "براہ کرم پراڈکٹ کا نام یا کوڈ بتائیں۔ دستیاب مصنوعات: ",
    },
    "human": {
        "en": "I have flagged this conversation for the owner. Automatic replies are paused.",
        "roman_ur": "Conversation owner ko bhej di hai. Automatic replies band hain.",
        "ur": "یہ گفتگو مالک کو بھیج دی گئی ہے۔ خودکار جوابات روک دیے گئے ہیں۔",
    },
    "uncertain": {
        "en": "I cannot answer this accurately. I have flagged your message for the owner.",
        "roman_ur": "Is sawal ka yaqini jawab nahi mil raha. Message owner ko bhej diya hai.",
        "ur": "اس سوال کا یقینی جواب نہیں مل رہا۔ پیغام مالک کو بھیج دیا گیا ہے۔",
    },
}


class ConversationError(Exception):
    """A customer conversation could not be processed as requested."""


class ConversationNotFound(ConversationError):
    """The conversation does not exist under this owner."""


class ConversationConflict(ConversationError):
    """The external message ID was reused or the operation is not allowed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(owner_id: str, customer_id: str, channel: str = "demo") -> str:
    return hashlib.sha256(f"{owner_id}:{channel}:{customer_id}".encode()).hexdigest()[:32]


def _message_id(conversation_id: str, external_id: str, role: str) -> str:
    return role + "-" + hashlib.sha256(f"{conversation_id}:{role}:{external_id}".encode()).hexdigest()


def _message(message_id: str, role: str, text: str) -> dict:
    return {
        "id": message_id, "role": role, "text": text, "created_at": _now(),
        # The only configured channel is the local simulator: nothing has been
        # delivered to a customer on WhatsApp.
        "delivery_status": "simulated",
    }


def _stamp(value: datetime) -> str:
    # PyMongo's default datetime decoding returns naive UTC; label it as UTC
    # rather than accidentally displaying it in the browser's local timezone.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def language_of(text: str) -> str:
    if re.search(r"[\u0600-\u06ff]", text):
        return "ur"
    if re.search(r"\b(?:hai|hain|kya|kia|mujhe|chahiye|batao|kitne|kitni|kitna|ka|ki|ke|aap|mera|meri|se|yeh|ye|nahi|karna|karo|salam|assalam|qeemat)\b", text, re.I):
        return "roman_ur"
    return "en"


def _public(doc: dict, *, full: bool = True) -> dict:
    last = doc.get("last_message", "")
    result = {
        "id": doc["id"], "business_id": doc["owner_id"],
        "customer_id": doc["customer_id"], "customer_name": doc.get("customer_name") or "Customer",
        "mode": doc.get("mode", "bot"), "language": doc.get("language", "en"),
        "updated_at": _stamp(doc["updated_at"]), "last_message": {"text": last},
        "handover_reason": doc.get("handover_reason"),
    }
    if full:
        result["messages"] = [
            {**message, "created_at": _stamp(message["created_at"])}
            for message in doc.get("messages", [])
        ]
    return result


def list_conversations(db, owner: dict) -> list[dict]:
    owner_id = str(owner["_id"])
    return [_public(doc, full=False) for doc in db.conversations.find({"owner_id": owner_id})
            .sort("updated_at", -1).limit(200)]


def get_conversation(db, owner: dict, conversation_id: str) -> dict:
    doc = db.conversations.find_one({"_id": conversation_id, "owner_id": str(owner["_id"])})
    if doc is None:
        raise ConversationNotFound("Conversation not found")
    return _public(doc)


def _append(db, owner_id: str, cid: str, message: dict, *, expected_mode: str | None = None) -> bool:
    query = {"_id": cid, "owner_id": owner_id,
             "messages.id": {"$ne": message["id"]}, "messages.499": {"$exists": False}}
    if expected_mode is not None:
        query["mode"] = expected_mode
    result = db.conversations.update_one(query, {"$push": {"messages": message},
                                                 "$set": {"last_message": message["text"], "updated_at": _now()}})
    if result.modified_count:
        return True
    doc = db.conversations.find_one({"_id": cid, "owner_id": owner_id})
    if doc is None:
        raise ConversationNotFound("Conversation not found")
    existing = next((item for item in doc["messages"] if item["id"] == message["id"]), None)
    if existing is not None:
        if existing["text"] != message["text"] or existing["role"] != message["role"]:
            raise ConversationConflict("external_id was already used for different message text")
        return False
    if len(doc["messages"]) >= MAX_MESSAGES:
        raise ConversationConflict("Conversation message limit reached; owner review required")
    if expected_mode is not None and doc["mode"] != expected_mode:
        return False
    raise ConversationConflict("Conversation could not be updated")


def _ensure_conversation(db, owner: dict, customer_id: str) -> str:
    owner_id = str(owner["_id"])
    cid = _id(owner_id, customer_id)
    doc = {
        "id": cid, "owner_id": owner_id, "channel": "demo",
        "customer_id": customer_id, "customer_name": "Customer", "mode": "bot",
        "language": "en", "updated_at": _now(), "last_message": "",
        "handover_reason": None, "messages": [],
    }
    try:
        db.conversations.update_one({"_id": cid}, {"$setOnInsert": doc}, upsert=True)
    except DuplicateKeyError:
        pass  # Another copy of this same customer request initialized the row.
    return cid


def _products(owner: dict) -> list[dict]:
    # The UC-4 owner-scoped facade shares its one SQLite catalogue with orders.
    # Never quote prices or stock from UC-2's separate, synthetic Mongo fixture.
    return order_service.list_products(owner)


def _matching_products(text: str, products: list[dict]) -> list[dict]:
    t = text.casefold()
    return [p for p in products if any(
        re.search(r"(?<!\w)" + re.escape(str(value).casefold()) + r"(?!\w)", t)
        for value in (p.get("name"), p.get("sku"), p.get("id")) if value
    )]


def _product_reply(text: str, language: str, products: list[dict], history: list[dict]) -> str:
    selected = _matching_products(text, products)
    t = text.casefold()
    if not selected and re.fullmatch(r"\s*(?:what is (?:its |the )?|iski |is ki |اس کی )?(?:price|stock|available|availability|قیمت|دستیاب)(?: hai| kya hai| hai kya)?\s*[?.!]*\s*", t):
        for message in reversed(history[-6:]):
            selected = _matching_products(message["text"], products)
            if selected:
                break
    if not selected and re.search(r"\b(?:recommend|suggest|products|catalogue|catalog|options|dikhao)\b|تجویز|مصنوعات|دکھائیں", text, re.I):
        selected = [p for p in products if p.get("stock_qty", 0) > 0][:5]
    if not selected:
        names = ", ".join(p["name"] for p in products[:5]) or "(empty catalogue)"
        return TEXT["missing"][language] + names
    lines = []
    for p in selected[:5]:
        price = f"PKR {p['price_cents'] / 100:,.2f}"
        stock = p.get("stock_qty", 0)
        if language == "ur":
            line = f"{p['name']} — قیمت: {price}۔ دستیاب اسٹاک: {stock}۔"
        elif language == "roman_ur":
            line = f"{p['name']} — Qeemat: {price}. Available stock: {stock}."
        else:
            line = f"{p['name']} — Price: {price}. Available stock: {stock}."
        if not stock:
            line += {"ur": " فی الحال اسٹاک ختم ہے۔", "roman_ur": " Filhal stock khatam hai.", "en": " Currently out of stock."}[language]
        lines.append(line)
    return "\n".join(lines)


def _handover(db, owner_id: str, cid: str, reason: str, reply_id: str, lang: str, phrase: str) -> None:
    # One document update keeps the human takeover and acknowledgement together.
    doc = db.conversations.find_one({"_id": cid, "owner_id": owner_id})
    if doc is None or doc["mode"] != "bot":
        return
    if any(m["id"] == reply_id for m in doc["messages"]):
        return
    if len(doc["messages"]) >= MAX_MESSAGES:
        raise ConversationConflict("Conversation message limit reached; owner review required")
    msg = _message(reply_id, "system", phrase)
    db.conversations.update_one({"_id": cid, "owner_id": owner_id, "mode": "bot",
                                 "messages.id": {"$ne": reply_id}, "messages.499": {"$exists": False}},
                                {"$set": {"mode": "human", "language": lang, "handover_reason": reason,
                                          "last_message": msg["text"], "updated_at": _now()},
                                 "$push": {"messages": msg}})


def receive_demo_message(db, owner: dict, customer_id: str, text: str, external_id: str | None) -> dict:
    cid = _id(str(owner["_id"]), customer_id)
    with _lock_for(cid):
        return _receive_unlocked(db, owner, customer_id, text, external_id)


def _receive_unlocked(db, owner: dict, customer_id: str, text: str, external_id: str | None) -> dict:
    owner_id = str(owner["_id"])
    cid = _ensure_conversation(db, owner, customer_id)
    inbound_id = _message_id(cid, external_id or uuid4().hex, "customer")
    reply_id = "out-" + inbound_id.removeprefix("customer-")
    _append(db, owner_id, cid, _message(inbound_id, "customer", text))
    doc = db.conversations.find_one({"_id": cid, "owner_id": owner_id})
    if doc["mode"] == "human" or any(item["id"] == reply_id for item in doc["messages"]):
        return _public(doc)
    language = language_of(text)
    db.conversations.update_one({"_id": cid, "owner_id": owner_id, "mode": "bot"},
                                {"$set": {"language": language}})
    if not owner.get("agent_settings", {}).get("customer_commerce", False):
        db.conversations.update_one({"_id": cid, "owner_id": owner_id, "mode": "bot"},
                                    {"$set": {"mode": "human", "handover_reason": "Customer Agent is disabled in owner settings"}})
    elif HUMAN_INTENT.search(text):
        _handover(db, owner_id, cid, "Customer requested the owner", reply_id, language, TEXT["human"][language])
    elif COMPLAINT_INTENT.search(text):
        _handover(db, owner_id, cid, "Customer complaint requires owner attention", reply_id, language, TEXT["human"][language])
    elif ORDER_COMMAND.match(text):
        # The deterministic inbound ID is the idempotency key for UC-4.
        try:
            reply = order_service.process_customer_command(owner, inbound_id, customer_id, text)
        except order_service.OrderError as exc:
            reply = str(exc)
        except Exception:
            # An unknown outcome must not be presented as a confirmed order.
            _handover(db, owner_id, cid, "Order processing unavailable; check the order ledger",
                      reply_id, language, TEXT["human"][language])
            return get_conversation(db, owner, cid)
        _append(db, owner_id, cid, _message(reply_id, "assistant", reply), expected_mode="bot")
    elif ORDER_INTENT.search(text):
        _handover(db, owner_id, cid, "Order request needs an order ID or owner review",
                  reply_id, language, TEXT["human"][language])
    elif re.fullmatch(r"\s*(?:hello|hi|hey|salam|assalam[^.!]*|السلام[^.!]*|سلام)\s*[!.]*\s*", text, re.I):
        _append(db, owner_id, cid, _message(reply_id, "assistant", TEXT["greeting"][language]), expected_mode="bot")
    else:
        try:
            products = _products(owner)
        except Exception:
            _handover(db, owner_id, cid, "Product catalogue unavailable", reply_id, language,
                      TEXT["uncertain"][language])
            return get_conversation(db, owner, cid)
        if PRODUCT_INTENT.search(text) or _matching_products(text, products):
            reply = _product_reply(text, language, products, doc["messages"][:-1])
            _append(db, owner_id, cid, _message(reply_id, "assistant", reply), expected_mode="bot")
        else:
            _handover(db, owner_id, cid, "Assistant cannot answer accurately", reply_id, language,
                      TEXT["uncertain"][language])
    return get_conversation(db, owner, cid)


def change_mode(db, owner: dict, cid: str, mode: str) -> dict:
    with _lock_for(cid):
        return _change_mode_unlocked(db, owner, cid, mode)


def _change_mode_unlocked(db, owner: dict, cid: str, mode: str) -> dict:
    owner_id = str(owner["_id"])
    result = db.conversations.update_one({"_id": cid, "owner_id": owner_id}, {"$set": {
        "mode": mode, "handover_reason": "Owner took over this conversation" if mode == "human" else None,
        "updated_at": _now(),
    }})
    if not result.matched_count:
        raise ConversationNotFound("Conversation not found")
    return get_conversation(db, owner, cid)


def owner_reply(db, owner: dict, cid: str, text: str, external_id: str | None) -> dict:
    with _lock_for(cid):
        return _owner_reply_unlocked(db, owner, cid, text, external_id)


def _owner_reply_unlocked(db, owner: dict, cid: str, text: str, external_id: str | None) -> dict:
    owner_id = str(owner["_id"])
    inbound_id = _message_id(cid, external_id or uuid4().hex, "owner")
    doc = db.conversations.find_one({"_id": cid, "owner_id": owner_id})
    if doc is None:
        raise ConversationNotFound("Conversation not found")
    if doc["mode"] != "human":
        raise ConversationConflict("Take over the conversation before replying")
    _append(db, owner_id, cid, _message(inbound_id, "owner", text), expected_mode="human")
    return get_conversation(db, owner, cid)
