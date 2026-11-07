"""Explicit owner-only event demonstration; there is no UC-5 stock update yet."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from ...application.events.dispatcher import publish_event, publish_low_stock
from ...application.events.event_types import DEMAND_SPIKE, FRAUD_FLAG, LOW_STOCK
from ...core.security import require_demo_owner
from ...infrastructure.db.mongo import database_for_request


router = APIRouter(prefix="/api/owner/demo-events", tags=["UC-2: Demo Agent Events"])


@router.post("/low-stock")
def demonstrate_low_stock(request: Request, owner: dict = Depends(require_demo_owner)) -> dict:
    """Record a repeatable synthetic event; do not change product/order data."""
    if not owner.get("agent_settings", {}).get("inventory_logistics", False):
        raise HTTPException(status_code=409, detail="Enable the Inventory & Logistics demo agent first.")
    db = database_for_request(request)
    owner_id = str(owner["_id"])
    product = db.products.find_one({"owner_id": owner_id, "demo": True})
    if not product:
        raise HTTPException(status_code=404, detail="No fictional demo product found.")
    quantity = int(product["stock_quantity"])
    threshold = int(product["reorder_threshold"])
    if quantity >= threshold:
        raise HTTPException(status_code=409, detail="Demo stock is not below its configured threshold.")
    product_id = str(product["_id"])
    event_id = f"demo-low-stock:{owner_id}:{product_id}:{quantity}:{threshold}"
    created = publish_low_stock(
        db,
        owner_id=owner_id,
        event_id=event_id,
        product_id=product_id,
        quantity=quantity,
        threshold=threshold,
    )
    return {
        "event_id": event_id,
        "created": created,
        "simulated": True,
        "note": "Synthetic low-stock coordination requested; no stock or order was changed.",
    }


@router.post("/demand-spike")
def demonstrate_demand_spike(request: Request, owner: dict = Depends(require_demo_owner)) -> dict:
    """Send a fictional demand signal; it does not represent real order counts."""
    if not owner.get("agent_settings", {}).get("customer_commerce", False):
        raise HTTPException(status_code=409, detail="Enable the Customer & Commerce demo agent first.")
    db = database_for_request(request)
    owner_id = str(owner["_id"])
    product = db.products.find_one({"owner_id": owner_id, "demo": True})
    if not product:
        raise HTTPException(status_code=404, detail="No fictional demo product found.")
    product_id = str(product["_id"])
    event_id = f"demo-demand-spike:{owner_id}:{product_id}:12"
    created = publish_event(
        db, owner_id=owner_id, event_id=event_id, event_type=DEMAND_SPIKE,
        source="customer_commerce", details={"product_id": product_id, "fictional_order_count": 12},
    )
    return {
        "event_id": event_id,
        "created": created,
        "simulated": True,
        "note": "Fictional demand signal sent; no real order trend was inferred or stock changed.",
    }


@router.get("")
def list_demo_events(request: Request, owner: dict = Depends(require_demo_owner)) -> dict:
    owner_id = str(owner["_id"])
    db = database_for_request(request)
    events = []
    for event in db.system_events.find({"owner_id": owner_id, "event_type": {"$in": [LOW_STOCK, DEMAND_SPIKE, FRAUD_FLAG]}}).sort("created_at", -1).limit(20):
        created_at = event.get("created_at")
        events.append({
            "event_id": event.get("event_id", ""),
            "event_type": event.get("event_type", ""),
            "source": event.get("source", ""),
            "target_agents": event.get("target_agents", []),
            "status": event.get("status", "recorded"),
            "agent_responses": event.get("agent_responses", []),
            "failed_agents": event.get("failed_agents", []),
            "details": event.get("details", {}),
            "created_at": created_at.isoformat() if isinstance(created_at, datetime) else "",
        })
    return {"simulated": True, "events": events}
