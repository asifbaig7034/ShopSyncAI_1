"""Durable, idempotent event recording (MongoDB in live mode)."""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError


def _verify_identity(existing: dict, *, owner_id: str, event_type: str, source: str, details: dict) -> None:
    """An event ID must never be recycled for a different owner or occurrence."""
    if any((existing.get(field) != value) for field, value in (
        ("owner_id", owner_id), ("event_type", event_type),
        ("source", source), ("details", details),
    )):
        raise ValueError("Event ID already belongs to a different event")


def save_event(db, *, event_id: str, event_type: str, owner_id: str, source: str, targets: list[str], details: dict) -> bool:
    if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
        raise ValueError("A short, stable event ID is required")
    existing = db.system_events.find_one({"event_id": event_id})
    if existing is not None:
        _verify_identity(existing, owner_id=owner_id, event_type=event_type, source=source, details=details)
        return False
    try:
        result = db.system_events.update_one(
            {"event_id": event_id},
            {"$setOnInsert": {
                "event_id": event_id,
                "event_type": event_type,
                "owner_id": owner_id,
                "source": source,
                "target_agents": targets,
                "details": details,
                "status": "recorded",
                "agent_responses": [],
                "created_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        # A concurrent publisher may have inserted the same ID since our read.
        result = None
    if result is None or result.upserted_id is None:
        existing = db.system_events.find_one({"event_id": event_id})
        if existing is None:
            raise RuntimeError("Event write could not be confirmed")
        _verify_identity(existing, owner_id=owner_id, event_type=event_type, source=source, details=details)
        return False
    return True
