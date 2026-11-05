"""Demonstrate low-stock coordination without implementing UC-5 stock writes."""

from __future__ import annotations

from ..agents import customer_commerce
from .event_types import LOW_STOCK
from .outbox import save_event


def publish_low_stock(db, *, owner_id: str, event_id: str, product_id: str, quantity: int, threshold: int) -> bool:
    """Call on a real threshold crossing or labelled synthetic demo event.

    Reading stock through owner chat must *never* call this function.
    Returns True only for a first-time event with quantity below threshold.
    """
    if quantity >= threshold:
        return False
    created = save_event(
        db,
        event_id=event_id,
        event_type=LOW_STOCK,
        owner_id=owner_id,
        source="inventory_logistics",
        targets=["customer_commerce"],
        details={"product_id": product_id, "stock_quantity": quantity, "threshold": threshold},
    )
    # A repeated event cannot create another record; it only retries a
    # subscriber that previously failed. Claiming the status is atomic, so
    # two simultaneous requests cannot acknowledge the same event twice.
    claimed = db.system_events.update_one(
        {"event_id": event_id, "owner_id": owner_id, "status": {"$in": ["recorded", "subscriber_failed"]}},
        {"$set": {"status": "processing"}},
    )
    if claimed.matched_count:
        event = db.system_events.find_one({"event_id": event_id, "owner_id": owner_id})
        try:
            response = customer_commerce.on_low_stock(event)
        except Exception:
            # The same event ID can be retried without duplicating the event.
            db.system_events.update_one({"event_id": event_id, "owner_id": owner_id}, {"$set": {"status": "subscriber_failed"}})
        else:
            db.system_events.update_one(
                {"event_id": event_id, "owner_id": owner_id},
                {"$set": {"status": "coordinated", "agent_responses": [response]}},
            )
    return created
