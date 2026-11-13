"""Transactional SQLite queue for UC-4 events consumed by the UC-2 orchestrator."""

import json
import uuid
from datetime import datetime, timezone


def queue_agent_event(db, event_type: str, aggregate_id: str, details: dict) -> str:
    """Write inside the caller's order transaction; never publish to Mongo here.

    The event ID is durable and reused when the worker retries, allowing the
    Mongo dispatcher to deduplicate delivery after an interrupted attempt.
    Customer names, phone numbers, and addresses must not enter ``details``.
    """
    event_id = "uc4:" + uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO agent_event_outbox "
        "(event_id,event_type,aggregate_id,payload_json,next_attempt_at,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            event_id,
            event_type,
            aggregate_id,
            json.dumps(details, sort_keys=True, separators=(",", ":")),
            created_at,
            created_at,
        ),
    )
    return event_id
