"""Owner-facing history of real, committed UC-4 events coordinated by UC-2."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request

from ...application.events.order_bridge import drain_order_events
from ...core.security import require_real_owner
from ...infrastructure.db.mongo import database_for_request


router = APIRouter(prefix="/api/owner/events", tags=["UC-2: Agent Events"])


@router.get("")
def list_agent_events(request: Request, owner: dict = Depends(require_real_owner)) -> dict:
    """Drain due events first so a newly confirmed order appears immediately.

    The SQLite outbox retains failed deliveries for retry; no customer data
    or synthetic events are returned to this real-owner workspace.
    """
    database = database_for_request(request)
    result = drain_order_events(database, str(owner["_id"]))
    rows = database.system_events.find({
        "owner_id": str(owner["_id"]), "event_id": {"$regex": "^uc4:"},
    }).sort("created_at", -1).limit(30)
    events = []
    for row in rows:
        created_at = row.get("created_at")
        events.append({
            "event_id": row.get("event_id", ""),
            "event_type": row.get("event_type", ""),
            "status": row.get("status", "recorded"),
            "target_agents": row.get("target_agents", []),
            "agent_responses": row.get("agent_responses", []),
            "failed_agents": row.get("failed_agents", []),
            "skipped_agents": row.get("skipped_agents", []),
            "details": row.get("details", {}),
            "created_at": created_at.isoformat() if isinstance(created_at, datetime) else "",
        })
    return {"events": events, "pending": result.get("pending", 0)}
