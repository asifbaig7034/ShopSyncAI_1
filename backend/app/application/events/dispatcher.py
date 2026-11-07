"""UC-2 event coordinator: durable fan-out to enabled, registered agents.

Future agent implementations can subscribe by adding an event type and a
read-only handler to SUBSCRIPTIONS. Effectful handlers must use event_id to
deduplicate their own actions: delivery is at least once after a process crash.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import time

from bson import ObjectId

from ..agents import customer_commerce, inventory_logistics
from .event_types import DEMAND_SPIKE, FRAUD_FLAG, LOW_STOCK
from .outbox import save_event


EventHandler = Callable[[dict], dict]

# No risk action is registered until the Risk & Trust agent is implemented.
SUBSCRIPTIONS: dict[str, dict[str, EventHandler]] = {
    LOW_STOCK: {customer_commerce.CODE: customer_commerce.on_low_stock},
    DEMAND_SPIKE: {inventory_logistics.CODE: inventory_logistics.on_demand_spike},
}
EVENT_TYPES = frozenset({LOW_STOCK, DEMAND_SPIKE, FRAUD_FLAG})


def publish_event(
    db, *, owner_id: str, event_id: str, event_type: str,
    source: str, details: dict, handlers: Mapping[str, EventHandler] | None = None,
) -> bool:
    """Record once, deliver to enabled subscribers, retry failed ones only.

    The persisted list of target agents is a snapshot from first publication.
    Reusing an event ID with changed event identity is rejected. A handler
    cannot mutate business data in this UC-2 prototype.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported event type")
    if not isinstance(source, str) or not source or not isinstance(details, dict):
        raise ValueError("Event source and details are required")
    owner_key = ObjectId(owner_id) if ObjectId.is_valid(owner_id) else owner_id
    owner = db.owners.find_one({"_id": owner_key}, {"agent_settings": 1})
    if not owner:
        raise ValueError("Event owner does not exist")
    enabled = owner.get("agent_settings") or {}
    subscribers = SUBSCRIPTIONS.get(event_type, {}) if handlers is None else handlers
    targets = [code for code in subscribers if code != source and enabled.get(code, False)]
    created = save_event(
        db, event_id=event_id, event_type=event_type, owner_id=owner_id,
        source=source, targets=targets, details=details,
    )
    now = time()
    # Only one publisher may claim an event. Replays reattempt failed targets
    # but never call an already acknowledged handler in ordinary retries. A
    # stale processing claim can be recovered after a process interruption.
    claimed = db.system_events.update_one(
        {"event_id": event_id, "owner_id": owner_id,
         "$or": [
             {"status": {"$in": ["recorded", "subscriber_failed"]}},
             {"status": "processing", "processing_started_at": {"$lt": now - 120}},
             {"status": "processing", "processing_started_at": {"$exists": False}},
         ]},
        {"$set": {"status": "processing", "processing_started_at": now, "failed_agents": []}},
    )
    if not claimed.matched_count:
        return created
    event = db.system_events.find_one({"event_id": event_id, "owner_id": owner_id})
    acknowledged = {row.get("agent") for row in event.get("agent_responses", []) if isinstance(row, dict)}
    failed: list[str] = []
    for code in event.get("target_agents", []):
        if code in acknowledged:
            continue
        handler = subscribers.get(code)
        if not enabled.get(code, False) or handler is None:
            failed.append(code)
            continue
        try:
            response = handler(event)
            if (not isinstance(response, dict) or response.get("agent") != code
                    or not isinstance(response.get("status"), str) or not response["status"]):
                raise ValueError("Invalid event acknowledgement")
            # Save each success immediately so another target's failure does
            # not cause successful targets to run again on the next attempt.
            db.system_events.update_one(
                {"event_id": event_id, "owner_id": owner_id},
                {"$push": {"agent_responses": response}},
            )
            acknowledged.add(code)
        except Exception:
            # Keep internal errors out of the event record and Owner's view.
            failed.append(code)
    status = "subscriber_failed" if failed else ("coordinated" if event.get("target_agents") else "unhandled")
    db.system_events.update_one(
        {"event_id": event_id, "owner_id": owner_id},
        {"$set": {"status": status, "failed_agents": failed}},
    )
    return created


def publish_low_stock(db, *, owner_id: str, event_id: str, product_id: str, quantity: int, threshold: int) -> bool:
    """Publish only a threshold crossing; reading stock does not emit events."""
    if quantity >= threshold:
        return False
    return publish_event(
        db, owner_id=owner_id, event_id=event_id, event_type=LOW_STOCK,
        source=inventory_logistics.CODE,
        details={"product_id": product_id, "stock_quantity": quantity, "threshold": threshold},
    )
