"""Auditable UC-2 events in the SRS's system_events collection."""

from __future__ import annotations

from datetime import datetime, timezone


def record_uc2_audit(db, *, owner_id: str, conversation_id: str, run_id: str, status: str, routed_agents: list[str]) -> None:
    db.system_events.insert_one({
        "event_type": "audit.uc2.request",
        "actor": "Business Owner",
        "owner_id": owner_id,
        "target": conversation_id,
        "details": {"run_id": run_id, "status": status, "routed_agents": routed_agents},
        "created_at": datetime.now(timezone.utc),
    })
