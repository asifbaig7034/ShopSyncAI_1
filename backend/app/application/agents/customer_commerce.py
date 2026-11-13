"""Read-only UC-2 adapter for the future Customer & Commerce Agent."""

from __future__ import annotations

from bson import ObjectId

from ..services import order_service


CODE = "customer_commerce"
LABEL = "Customer & Commerce"


def handle(db, owner_id: str, intent: str, message: str) -> str:
    if intent != "orders":
        raise ValueError(f"Unsupported Customer & Commerce request: {intent}")
    # A real signed-in owner sees the same UC-4 ledger used by the order API.
    # Existing synthetic Mongo fixtures remain available to the demo owner
    # and isolated UC-1/UC-2 contract tests.
    owner_key = ObjectId(owner_id) if ObjectId.is_valid(owner_id) else owner_id
    owner = db.owners.find_one({"_id": owner_key})
    if owner and owner.get("demo") is False:
        stored_mongo = db.orders.count_documents({"owner_id": owner_id}, limit=1)
        if not (db.__class__.__module__.startswith("mongomock") and stored_mongo):
            records = order_service.service_for_owner(owner).list_orders()[:5]
            if records:
                lines = [f"{row['id']}: {row['status']}, total PKR {row['total_cents'] / 100:,.2f}"
                         for row in records]
                return "Recent orders:\n" + "\n".join(lines)
            # Do not quote stale Mongo records in the real owner's workspace:
            # UC-3 and UC-4 both use this SQLite ledger for live demo orders.
            return "Recent orders: No orders saved yet."
    orders = list(db.orders.find({"owner_id": owner_id}).sort("created_at", -1).limit(5))
    if not orders:
        return "Recent orders: No orders saved yet."
    lines = []
    for order in orders:
        identifier = str(order.get("order_number") or order.get("demo_order_ref") or order["_id"])
        status = str(order.get("status") or "status unavailable")
        total = order.get("total")
        amount = f", total PKR {total:,.2f}" if isinstance(total, (int, float)) and not isinstance(total, bool) else ""
        lines.append(f"{identifier}: {status}{amount}")
    return "Recent orders (saved demo records):\n" + "\n".join(lines)


def on_low_stock(event: dict) -> dict:
    """Acknowledge inventory change without messaging a customer."""
    if event.get("event_id", "").startswith("uc4:"):
        return {
            "agent": CODE,
            "status": "observed",
            "note": "Low stock recorded; customer replies must consult current inventory. No customer message was sent.",
        }
    return {
        "agent": CODE,
        "status": "observed_demo_only",
        "note": "Low-stock event received. Customer recommendations are not implemented yet.",
    }


def on_stock_replenished(event: dict) -> dict:
    """Acknowledge restored inventory; customer-facing replies still use live stock."""
    return {
        "agent": CODE,
        "status": "observed",
        "note": "Stock replenishment recorded; current inventory is available to customer replies. No customer message was sent.",
    }
