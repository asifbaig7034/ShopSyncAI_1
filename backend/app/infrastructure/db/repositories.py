"""Small Mongo-compatible repository helpers and explicit synthetic fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from ...core.config import get_settings


DEMO_OWNER_EMAIL = "owner@shopsync.local"


def seed_demo(db: Any) -> dict[str, str]:
    """Idempotently install fictional data only when local demo mode is on."""
    if not get_settings().demo_mode:
        raise RuntimeError("Synthetic fixture seeding is disabled outside DEMO_MODE.")

    owner = db.owners.find_one({"email": DEMO_OWNER_EMAIL})
    if owner is None:
        owner = {
            "_id": ObjectId(),
            "email": DEMO_OWNER_EMAIL,
            "name": "Demo Business Owner",
            "business_profile": {"name": "ShopSync Demo Brand", "currency": "PKR"},
            "agent_settings": {"customer_commerce": True, "inventory_logistics": True},
            "demo": True,
            "created_at": datetime.now(timezone.utc),
        }
        db.owners.insert_one(owner)
    owner_id = str(owner["_id"])

    product = db.products.find_one({"owner_id": owner_id, "demo_sku": "COTTON-SHIRT-001"})
    if product is None:
        product = {
            "_id": ObjectId(),
            "owner_id": owner_id,
            "demo_sku": "COTTON-SHIRT-001",
            "name": "Cotton Shirt",
            "price": 1200.0,
            "currency": "PKR",
            "stock_quantity": 3,
            "reorder_threshold": 5,
            "demo": True,
        }
        db.products.insert_one(product)

    order = db.orders.find_one({"owner_id": owner_id, "demo_order_ref": "DEMO-1001"})
    if order is None:
        order = {
            "_id": ObjectId(),
            "owner_id": owner_id,
            "demo_order_ref": "DEMO-1001",
            "customer_id": "fictional-customer",
            "items": [{"product_id": str(product["_id"]), "quantity": 1, "unit_price": 1200.0}],
            "total": 1200.0,
            "status": "confirmed",
            "demo": True,
            "created_at": datetime.now(timezone.utc),
        }
        db.orders.insert_one(order)
    return {"owner_id": owner_id, "product_id": str(product["_id"]), "order_id": str(order["_id"])}


def history_for_owner(db: Any, owner_id: str, conversation_id: str, limit: int = 100) -> list[dict]:
    """Return the most recent owner-scoped requests in chronological order."""
    cursor = db.agent_runs.find({"owner_id": owner_id, "conversation_id": conversation_id}).sort(
        [("created_at", -1), ("_id", -1)]
    ).limit(min(limit, 100))
    rows: list[dict] = []
    for run in reversed(list(cursor)):
        raw_result = run.get("result", run.get("reply", ""))
        warnings = raw_result.get("warnings", []) if isinstance(raw_result, dict) else run.get("warnings", [])
        result = raw_result.get("reply", "") if isinstance(raw_result, dict) else raw_result
        created = run.get("created_at")
        if isinstance(created, datetime) and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    

        rows.append({
            "request": run.get("request", ""),
            "result": str(result),
            "routed_agents": run.get("routed_agents", []),
            "mode": run.get("mode", "unknown"),
            "warnings": warnings if isinstance(warnings, list) else [],
            "approval_request_id": run.get("approval_request_id"),
            "created_at": created.isoformat() if isinstance(created, datetime) else str(created or ""),
        })
    return rows
