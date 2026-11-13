"""Read-only UC-2 adapter for the future Supply Chain & Operations Agent."""

from __future__ import annotations

from bson import ObjectId

from ..services import order_service


CODE = "inventory_logistics"
LABEL = "Supply Chain & Operations"


def handle(db, owner_id: str, intent: str, message: str) -> str:
    if intent not in {"products", "stock"}:
        raise ValueError(f"Unsupported Supply Chain request: {intent}")
    owner_key = ObjectId(owner_id) if ObjectId.is_valid(owner_id) else owner_id
    owner = db.owners.find_one({"_id": owner_key})
    if owner and owner.get("demo") is False:
        stored_mongo = db.products.count_documents({"owner_id": owner_id}, limit=1)
        if not (db.__class__.__module__.startswith("mongomock") and stored_mongo):
            records = order_service.list_products(owner)[:10]
            if records:
                lines = []
                for product in records:
                    name = product["name"]
                    if intent == "products":
                        lines.append(f"{name} — PKR {product['price_cents'] / 100:,.2f}")
                    else:
                        lines.append(f"{name}: {product['stock_qty']}")
                return ("Products" if intent == "products" else "Stock") + ":\n" + "\n".join(lines)
            # The Mongo records below are synthetic UC-2/test fixtures, not
            # part of the real owner's UC-4 catalogue.
            return f"{'Products' if intent == 'products' else 'Stock'}: No products saved yet."
    products = list(db.products.find({"owner_id": owner_id}).sort("name", 1).limit(10))
    if not products:
        return f"{'Products' if intent == 'products' else 'Stock'}: No products saved yet."
    lines = []
    for product in products:
        name = str(product.get("name") or "Unnamed product")
        if intent == "products":
            price = product.get("price")
            price_part = f" — PKR {price:,.2f}" if isinstance(price, (int, float)) and not isinstance(price, bool) else ""
            lines.append(f"{name}{price_part}")
        else:
            quantity = product.get("stock_quantity")
            amount = str(quantity) if isinstance(quantity, int) and not isinstance(quantity, bool) else "quantity unavailable"
            threshold = product.get("reorder_threshold")
            low = " (low stock)" if isinstance(quantity, int) and isinstance(threshold, int) and quantity < threshold else ""
            lines.append(f"{name}: {amount}{low}")
    return ("Products" if intent == "products" else "Stock") + ":\n" + "\n".join(lines)


def on_demand_spike(event: dict) -> dict:
    """Acknowledge a fictional demand signal without changing product stock."""
    return {
        "agent": CODE,
        "status": "observed_demo_only",
        "note": "Demand signal received. Reorder forecasting is not implemented yet.",
    }


def on_order_change(event: dict) -> dict:
    """Acknowledge an actual order's effect on stock, without mutating stock."""
    return {
        "agent": CODE,
        "status": "observed",
        "note": "Order change recorded; stock was updated in the order ledger. No shipment or reorder was created.",
    }
