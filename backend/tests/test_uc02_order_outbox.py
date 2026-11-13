"""Real UC-4 state transitions must atomically enqueue UC-2 agent events."""

import json
from contextlib import closing

import pytest

from backend.app.application.services.order_engine import db, service


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPSYNC_DB", str(tmp_path / "orders.sqlite3"))
    monkeypatch.setenv("SHOPSYNC_LOW_STOCK_THRESHOLD", "5")
    monkeypatch.delenv("SHOPSYNC_RISK_AMOUNT_CENTS", raising=False)
    db.initialize()
    return db


def events(ledger):
    with closing(ledger.connect()) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM agent_event_outbox ORDER BY rowid"
        )]


def make_draft(product_id, *, key="draft:1", quantity=1, items=None):
    return service.create_draft(
        "923001234567", "Test Person", items or [{"product_id": product_id, "quantity": quantity}],
        "Private delivery address", "COD", key,
    )


def test_confirm_crosses_threshold_cancel_restores_and_replay_does_not_duplicate(ledger):
    item = service.add_product("P1", "Shirt", 1200, 5)
    order = make_draft(item["id"])
    assert events(ledger) == []  # Drafts do not book stock or announce a sale.

    assert service.confirm(order["id"])["status"] == "CONFIRMED"
    records = events(ledger)
    assert [r["event_type"] for r in records] == ["stock.low", "order.confirmed"]
    assert all(row["event_id"].startswith("uc4:") for row in records)
    assert all(row["status"] == "PENDING" and row["attempts"] == 0 for row in records)
    assert all(row["next_attempt_at"] for row in records)
    assert json.loads(records[0]["payload_json"])["stock_quantity"] == 4
    assert all("923001234567" not in row["payload_json"] for row in records)
    assert all("Private delivery address" not in row["payload_json"] for row in records)
    service.confirm(order["id"])
    assert len(events(ledger)) == 2

    assert service.cancel(order["id"])["status"] == "CANCELLED"
    assert [r["event_type"] for r in events(ledger)] == [
        "stock.low", "order.confirmed", "stock.replenished", "order.cancelled",
    ]
    service.cancel(order["id"])
    assert len(events(ledger)) == 4


def test_risk_hold_emits_hold_but_no_stock_or_sale_before_approval(ledger, monkeypatch):
    monkeypatch.setenv("SHOPSYNC_RISK_AMOUNT_CENTS", "100")
    item = service.add_product("P2", "Shirt", 1200, 5)
    order = make_draft(item["id"])

    assert service.confirm(order["id"])["status"] == "RISK_HOLD"
    assert [r["event_type"] for r in events(ledger)] == ["risk.held"]
    service.confirm(order["id"])
    assert [r["event_type"] for r in events(ledger)] == ["risk.held"]
    assert service.decide_risk(order["id"], True, "Reviewed")["status"] == "CONFIRMED"
    assert [r["event_type"] for r in events(ledger)] == [
        "risk.held", "stock.low", "order.confirmed",
    ]


def test_later_product_failure_rolls_back_prior_stock_and_outbox(ledger):
    first = service.add_product("P1", "Shirt", 1200, 5)
    second = service.add_product("P2", "Shoes", 1200, 5)
    order = make_draft(first["id"], items=[
        {"product_id": first["id"], "quantity": 1},
        {"product_id": second["id"], "quantity": 1},
    ])
    # Another sale exhausted the second product after the draft was created.
    with ledger.transaction() as connection:
        connection.execute("UPDATE products SET stock_qty=0 WHERE id=?", (second["id"],))
    with pytest.raises(service.OrderError, match="Insufficient stock"):
        service.confirm(order["id"])
    assert next(p for p in service.product_list() if p["id"] == first["id"])["stock_qty"] == 5
    assert service.get_order(order["id"])["status"] == "DRAFT"
    assert events(ledger) == []


def test_restock_and_received_return_produce_distinct_stock_events(ledger):
    item = service.add_product("P1", "Shirt", 1200, 5)
    service.restock_product(item["id"], 2)
    assert [r["event_type"] for r in events(ledger)] == ["stock.replenished"]
    assert json.loads(events(ledger)[0]["payload_json"])["stock_quantity"] == 7

    order = make_draft(item["id"])
    service.confirm(order["id"])
    service.mark_shipped(order["id"])
    request = service.request_return(order["id"], "RETURN", "Wrong size")["return_request"]
    service.decide_return(request["id"], True, "Eligible")
    service.receive_return(request["id"], True)
    assert [r["event_type"] for r in events(ledger)] == [
        "stock.replenished", "order.confirmed", "stock.replenished",
    ]
