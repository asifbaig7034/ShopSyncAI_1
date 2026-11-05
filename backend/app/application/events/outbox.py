"""Durable, idempotent event recording (MongoDB in live mode)."""

from __future__ import annotations

from datetime import datetime, timezone


def save_event(db, *, event_id: str, event_type: str, owner_id: str, source: str, targets: list[str], details: dict) -> bool:
    if not event_id or len(event_id) > 128:
        raise ValueError("A short, stable event ID is required")
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
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return result.upserted_id is not None
