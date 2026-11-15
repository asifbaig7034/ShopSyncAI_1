"""Idempotent WhatsApp status receipts. No network calls or order mutations."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .db import get_store, run_transaction
from .service import now

RETRY_CODES = {130429, 131000, 131016, 131056}
CONFIG_CODES = {10, 190, 200, 131005, 131031, 131042, 133000}
RANKS = {"sent": 1, "failed": 2, "delivered": 3, "read": 4}


def retry_at(attempts):
    return (datetime.now(timezone.utc) + timedelta(
        seconds=min(300, 10 * 2 ** min(max(attempts, 1), 5)))).isoformat(timespec="microseconds")


def failure_state(codes, attempts, http_status=None):
    """Only known temporary failures retry. Unknown/permanent errors need review."""
    codes = set(codes)
    if 131047 in codes:
        return "NEEDS_TEMPLATE"
    if codes & CONFIG_CODES:
        return "NEEDS_CONFIGURATION"
    transient = bool(codes) and codes.issubset(RETRY_CODES)
    if not codes and http_status is not None:
        transient = http_status == 429 or http_status >= 500
    return "FAILED" if transient and attempts < 5 else "MANUAL_REVIEW"


def error_codes(errors):
    result = set()
    for error in errors[:10] if isinstance(errors, list) else []:
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, int) and not isinstance(code, bool) and code >= 0:
            result.add(code)
    return sorted(result)


def normalize_status(value):
    if not isinstance(value, dict):
        return None
    provider_id, status = value.get("id"), value.get("status")
    timestamp = value.get("timestamp")
    if (not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 512 or
            not isinstance(status, str) or status not in RANKS or isinstance(timestamp, bool)):
        return None
    text = str(timestamp)
    if not text.isascii() or not text.isdigit() or len(text) > 12:
        return None
    recipient = value.get("recipient_id")
    if recipient is not None and (not isinstance(recipient, str) or
                                  not recipient.isascii() or not recipient.isdigit() or len(recipient) > 20):
        return None
    codes = error_codes(value.get("errors", []))
    key = json.dumps([provider_id, status, int(text), codes, recipient], separators=(",", ":"))
    return dict(id=hashlib.sha256(key.encode()).hexdigest(), provider_id=provider_id,
                provider_status=status, provider_timestamp=int(text), error_codes=codes,
                recipient=recipient, rank=RANKS[status], applied=False, received_at=now(),
                next_check_at=now(), outcome="WAITING_FOR_MESSAGE", processed_at=None)


def _complete(db, event, outcome):
    db.update("delivery_events", {"_id": event["id"]}, {"$set": {
        "applied": True, "outcome": outcome, "processed_at": now()}})
    return outcome


def _wait(db, event):
    db.update("delivery_events", {"_id": event["id"]}, {"$set": {
        "outcome": "WAITING_FOR_MESSAGE",
        "next_check_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(timespec="microseconds")}})
    return "WAITING_FOR_MESSAGE"


def apply_event(db, event):
    if event["applied"]:
        return "DUPLICATE"
    provider = event["provider_id"]
    message = db.get("outbox", {"provider_id": provider})
    if message is None:
        message = db.get("outbox", {"provider_ids": provider})
    if message is None:
        return _wait(db, event)
    if event.get("recipient") and event["recipient"] != message["phone"]:
        return _complete(db, event, "RECIPIENT_MISMATCH")
    status = event["provider_status"]
    current = message["status"]
    if current in ("DELIVERED", "READ"):
        if status != "read" or current == "READ":
            return _complete(db, event, "ALREADY_DELIVERED")
    if status in ("delivered", "read"):
        # Delivery from any attempt is proof of delivery and stops further retries.
        db.update("outbox", {"_id": message["id"]}, {"$set": {
            "status": status.upper(), "provider_status": status,
            "delivery_timestamp": max(event["provider_timestamp"], message.get("delivery_timestamp", -1)),
            "delivery_rank": RANKS[status], "last_error": None, "claim_id": None, "lease_until": None}})
        return _complete(db, event, status.upper())
    if current == "SENDING":
        # Receipt may beat persistence of the HTTP response. finish() replays it.
        return _wait(db, event)
    if (provider != message.get("provider_id") or
            message.get("provider_attempt_number", message["attempts"]) != message["attempts"]):
        return _complete(db, event, "STALE_ATTEMPT")
    if current in ("PENDING", "FAILED", "MANUAL_REVIEW", "NEEDS_TEMPLATE", "NEEDS_CONFIGURATION"):
        return _complete(db, event, "RETRY_OR_REVIEW_ALREADY_SCHEDULED")
    old_time, old_rank = message.get("delivery_timestamp", -1), message.get("delivery_rank", 0)
    if (event["provider_timestamp"], event["rank"]) <= (old_time, old_rank):
        return _complete(db, event, "OUT_OF_ORDER")
    fields = dict(provider_status=status, delivery_timestamp=event["provider_timestamp"],
                  delivery_rank=event["rank"], claim_id=None, lease_until=None)
    if status == "sent":
        fields.update(status="SENT", last_error=None)
    else:
        target = failure_state(event["error_codes"], message["attempts"])
        codes = ",".join(str(code) for code in event["error_codes"]) or "unspecified"
        fields.update(status=target, last_error="Provider delivery failed (codes: " + codes + ")")
        if target == "FAILED":
            fields["next_attempt_at"] = retry_at(message["attempts"])
    db.update("outbox", {"_id": message["id"]}, {"$set": fields})
    return _complete(db, event, fields["status"])


def handle_delivery_status(value):
    normalized = normalize_status(value)
    if normalized is None:
        return {"handled": False, "outcome": "INVALID_STATUS"}

    def process(db):
        event = db.get("delivery_events", {"_id": normalized["id"]})
        if event is None:
            db.insert("delivery_events", normalized)
            event = normalized
        outcome = apply_event(db, event)
        return {"handled": True, "outcome": outcome}
    return run_transaction(process)


def apply_pending_for(db, provider_ids):
    for provider_id in dict.fromkeys(value for value in provider_ids if value):
        events = db.all("delivery_events", {"provider_id": provider_id, "applied": False},
                        sort=[("provider_timestamp", 1), ("rank", 1)], limit=100)
        for event in events:
            apply_event(db, event)


def reconcile_waiting(limit=20):
    # Also covers the small race where a callback commits after finish()'s snapshot.
    events = get_store().all("delivery_events", {"applied": False, "next_check_at": {"$lte": now()}},
                             sort=[("next_check_at", 1), ("received_at", 1)], limit=limit)
    for event in events:
        def process(db):
            latest = db.get("delivery_events", {"_id": event["id"]})
            if latest is not None:
                apply_event(db, latest)
        run_transaction(process)
