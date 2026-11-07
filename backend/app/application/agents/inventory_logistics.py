"""Read-only UC-2 adapter for the future Supply Chain & Operations Agent."""

from __future__ import annotations


CODE = "inventory_logistics"
LABEL = "Supply Chain & Operations"


def handle(db, owner_id: str, intent: str, message: str) -> str:
    if intent not in {"products", "stock"}:
        raise ValueError(f"Unsupported Supply Chain request: {intent}")
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
