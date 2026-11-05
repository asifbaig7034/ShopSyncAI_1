"""Read-only UC-2 adapter for the future Customer & Commerce Agent."""

from __future__ import annotations


CODE = "customer_commerce"
LABEL = "Customer & Commerce"


def handle(db, owner_id: str, intent: str, message: str) -> str:
    if intent != "orders":
        raise ValueError(f"Unsupported Customer & Commerce request: {intent}")
    # UC-4 isn't implemented in this standalone prototype. Only saved demo
    # orders appear here; never invent customer records or mutate an order.
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
    """Read-only FYP-1 subscriber: acknowledge an event; never message a customer."""
    return {
        "agent": CODE,
        "status": "observed_demo_only",
        "note": "Low-stock event received. Customer recommendations are not implemented yet.",
    }
