"""Replay-safe bridge from UC-4's SQLite event outbox to UC-2's Mongo events.

Order writes and their event records commit in the same SQLite transaction.
Publishing happens afterwards: the worker claims a lease, lets the existing
dispatcher record and coordinate the event in Mongo, and acknowledges SQLite
only once Mongo reports that all enabled subscribers have been handled.
Repeating delivery after a crash uses the same event ID and is harmless.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..services.order_engine.db import connect, transaction
from ..services.order_service import ensure_order_engine
from .dispatcher import publish_event
from .event_types import (
    LOW_STOCK, ORDER_CANCELLED, ORDER_CONFIRMED, RISK_HELD, STOCK_REPLENISHED,
)


_SOURCES = {
    ORDER_CONFIRMED: "customer_commerce",
    ORDER_CANCELLED: "customer_commerce",
    LOW_STOCK: "inventory_logistics",
    STOCK_REPLENISHED: "inventory_logistics",
    RISK_HELD: "order_engine",
}
_LEASE_SECONDS = 120


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _sole_real_owner(database: Any, owner_id: str | None) -> str | None:
    """Only the one registered brand owner may receive this single-brand ledger."""
    rows = list(database.owners.find(
        {"singleton_key": "primary_owner", "role": "owner", "demo": False},
        {"_id": 1},
    ).limit(2))
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError("An ambiguous owner cannot receive order events")
    verified_id = str(rows[0]["_id"])
    if owner_id is not None and owner_id != verified_id:
        raise ValueError("Order events belong to a different owner")
    return verified_id


def _claim_due(batch_size: int) -> list[dict]:
    now = _utc_now()
    due = _timestamp(now)
    lease_until = _timestamp(now + timedelta(seconds=_LEASE_SECONDS))
    claimed: list[dict] = []
    # A short SQLite transaction atomically claims work before network I/O;
    # concurrent processes cannot deliver the same row during its lease.
    with transaction() as sqlite:
        rows = sqlite.execute(
            "SELECT * FROM agent_event_outbox "
            "WHERE status IN ('PENDING','RETRY','PROCESSING') AND next_attempt_at<=? "
            "ORDER BY created_at, rowid LIMIT ?",
            (due, batch_size),
        ).fetchall()
        for row in rows:
            updated = sqlite.execute(
                "UPDATE agent_event_outbox "
                "SET status='PROCESSING',attempts=attempts+1,next_attempt_at=? "
                "WHERE event_id=? AND status IN ('PENDING','RETRY','PROCESSING') "
                "AND next_attempt_at<=?",
                (lease_until, row["event_id"], due),
            )
            if updated.rowcount:
                claimed.append({**dict(row), "attempts": int(row["attempts"]) + 1})
    return claimed


def _mark_delivered(event_id: str, attempt: int) -> bool:
    with transaction() as sqlite:
        updated = sqlite.execute(
            "UPDATE agent_event_outbox SET status='DELIVERED', delivered_at=?, "
            "last_error=NULL WHERE event_id=? AND status='PROCESSING' AND attempts=?",
            (_timestamp(_utc_now()), event_id, attempt),
        )
        return bool(updated.rowcount)


def _schedule_retry(event_id: str, attempt: int) -> None:
    with transaction() as sqlite:
        row = sqlite.execute(
            "SELECT attempts FROM agent_event_outbox "
            "WHERE event_id=? AND status='PROCESSING' AND attempts=?",
            (event_id, attempt),
        ).fetchone()
        if row is None:
            return
        seconds = min(3600, 2 ** min(int(row["attempts"]), 12))
        sqlite.execute(
            "UPDATE agent_event_outbox SET status='RETRY', next_attempt_at=?, "
            "last_error='Event delivery postponed; the orchestrator will retry.' "
            "WHERE event_id=? AND status='PROCESSING' AND attempts=?",
            (_timestamp(_utc_now() + timedelta(seconds=seconds)), event_id, attempt),
        )


def _pending_count() -> int:
    from contextlib import closing

    with closing(connect()) as sqlite:
        return int(sqlite.execute(
            "SELECT COUNT(*) FROM agent_event_outbox WHERE status!='DELIVERED'"
        ).fetchone()[0])


def drain_order_events(database: Any, owner_id: str | None = None, *, batch_size: int = 50) -> dict[str, int]:
    """Deliver due UC-4 events for the registered owner; called by worker/API.

    An unhandled risk hold is still durably recorded in Mongo for the owner.
    Failed agent subscribers are retried, but no real business action runs in
    a UC-2 subscriber. No event is attributed to a synthetic demo owner.
    """
    if not 1 <= batch_size <= 100:
        raise ValueError("Batch size must be between 1 and 100")
    verified_owner = _sole_real_owner(database, owner_id)
    if verified_owner is None:
        return {"delivered": 0, "retry": 0, "pending": 0}
    ensure_order_engine()
    delivered = failed = 0
    for event in _claim_due(batch_size):
        event_id = event["event_id"]
        attempt = event["attempts"]
        try:
            event_type = event["event_type"]
            if event_type not in _SOURCES or not event_id.startswith("uc4:"):
                raise ValueError("Unsupported outbox event")
            details = json.loads(event["payload_json"])
            if not isinstance(details, dict):
                raise ValueError("Invalid event payload")
            publish_event(
                database, owner_id=verified_owner, event_id=event_id,
                event_type=event_type, source=_SOURCES[event_type], details=details,
            )
            persisted = database.system_events.find_one({
                "event_id": event_id, "owner_id": verified_owner,
            }, {"status": 1})
            if not persisted or persisted.get("status") not in {
                "coordinated", "unhandled", "skipped_disabled", "partially_coordinated",
            }:
                raise RuntimeError("Event has pending agent subscribers")
            if not _mark_delivered(event_id, attempt):
                raise RuntimeError("Outbox lease changed during delivery")
            delivered += 1
        except Exception:
            # Never leak private Mongo/provider exceptions into an owner event.
            # A failed publish or subscriber leaves its durable SQLite record.
            _schedule_retry(event_id, attempt)
            failed += 1
    return {"delivered": delivered, "retry": failed, "pending": _pending_count()}
