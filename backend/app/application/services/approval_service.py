"""Queue high-impact requests for a later Owner decision; execute nothing."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256


def queue_owner_approval(db, *, owner_id: str, conversation_id: str, action: str, message: str) -> str:
    # Repeating the same message in one conversation should not queue twice.
    request_key = sha256(f"{owner_id}\0{conversation_id}\0{action}\0{message}".encode("utf-8")).hexdigest()
    db.approval_requests.update_one(
        {"request_key": request_key},
        {"$setOnInsert": {
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "request_key": request_key,
            "agent_code": "orchestrator",
            "action_type": action,
            "target": None,
            "proposed_changes": {"owner_request": message},
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return str(db.approval_requests.find_one({"request_key": request_key})["_id"])
