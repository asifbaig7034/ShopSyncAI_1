"""UC-2 consumes actual committed UC-4 events without replaying agent work."""

from __future__ import annotations

import json
from contextlib import closing
from uuid import uuid4

import pytest

from backend.app.application.events import order_bridge
from backend.app.application.events.event_types import ORDER_CONFIRMED
from backend.app.application.services.order_engine.db import connect, transaction


@pytest.fixture(autouse=True)
def event_settings(monkeypatch):
    monkeypatch.setenv("SHOPSYNC_LOW_STOCK_THRESHOLD", "5")
    monkeypatch.setenv("SHOPSYNC_RISK_AMOUNT_CENTS", "2000000")


def _owner_with_order(demo):
    client, mongo, _sample, _demo_headers = demo
    signup = client.post("/api/account/signup", json={
        "name": "Brand Owner", "business_name": "Test Brand",
        "email": "uc2-bridge@example.com", "password": "a-long-private-password-123",
    })
    assert signup.status_code == 201, signup.text
    owner = mongo.owners.find_one({"singleton_key": "primary_owner", "demo": False})
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    product = client.post("/api/products", headers=headers, json={
        "sku": "UC2-BRIDGE", "name": "Bridge Cotton Tee",
        "price_cents": 120000, "stock_qty": 5,
    })
    assert product.status_code == 201, product.text
    new = client.post("/api/orders/drafts", headers=headers, json={
        "phone": "923001234567", "name": "Customer",
        "items": [{"product_id": product.json()["id"], "quantity": 1}],
        "delivery_address": "Private customer delivery address", "payment_method": "COD",
        "source_key": uuid4().hex,
    })
    assert new.status_code == 201, new.text
    order_id = new.json()["id"]
    confirmed = client.post(f"/api/orders/{order_id}/confirm", headers=headers)
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "CONFIRMED"
    return client, mongo, headers, str(owner["_id"]), order_id


def test_committed_order_and_stock_events_reach_only_real_owner(demo):
    client, mongo, headers, owner_id, order_id = _owner_with_order(demo)
    with closing(connect()) as sqlite:
        outgoing = [dict(row) for row in sqlite.execute("SELECT * FROM agent_event_outbox")]
    assert {row["event_type"] for row in outgoing} == {"stock.low", "order.confirmed"}
    for row in outgoing:
        details = json.loads(row["payload_json"])
        assert row["event_id"].startswith("uc4:")
        assert not any("phone" in key or "address" in key or "name" in key for key in details)
        assert "923001234567" not in row["payload_json"]

    first = order_bridge.drain_order_events(mongo, owner_id)
    assert first == {"delivered": 2, "retry": 0, "pending": 0}
    assert order_bridge.drain_order_events(mongo, owner_id) == {"delivered": 0, "retry": 0, "pending": 0}
    real = list(mongo.system_events.find({"owner_id": owner_id, "event_id": {"$regex": "^uc4:"}}))
    assert len(real) == 2
    assert {event["status"] for event in real} == {"coordinated"}
    assert all(event["agent_responses"][0]["status"] == "observed" for event in real)
    assert mongo.system_events.count_documents({"owner_id": owner_id, "event_type": ORDER_CONFIRMED}) == 1
    assert client.get(f"/api/orders/{order_id}", headers=headers).json()["status"] == "CONFIRMED"

    cancelled = client.post(f"/api/orders/{order_id}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert order_bridge.drain_order_events(mongo, owner_id)["delivered"] == 2
    assert {event["event_type"] for event in mongo.system_events.find({"owner_id": owner_id, "event_id": {"$regex": "^uc4:"}})} == {
        "stock.low", "order.confirmed", "stock.replenished", "order.cancelled",
    }


def test_mongo_failure_retains_sqlite_event_and_retries_after_backoff(demo, monkeypatch):
    _client, mongo, _headers, owner_id, _order_id = _owner_with_order(demo)
    actual_publish = order_bridge.publish_event
    calls = []

    def briefly_unavailable(*args, **kwargs):
        calls.append(kwargs["event_id"])
        monkeypatch.setattr(order_bridge, "publish_event", actual_publish)
        raise ConnectionError("private Mongo connection details")

    monkeypatch.setattr(order_bridge, "publish_event", briefly_unavailable)
    attempt = order_bridge.drain_order_events(mongo, owner_id)
    assert attempt == {"delivered": 1, "retry": 1, "pending": 1}
    with transaction() as sqlite:
        retry = sqlite.execute("SELECT * FROM agent_event_outbox WHERE event_id=?", (calls[0],)).fetchone()
        assert retry["status"] == "RETRY"
        assert "private" not in retry["last_error"]
        sqlite.execute("UPDATE agent_event_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE event_id=?", (calls[0],))
    recovered = order_bridge.drain_order_events(mongo, owner_id)
    assert recovered == {"delivered": 1, "retry": 0, "pending": 0}
    assert mongo.system_events.count_documents({"owner_id": owner_id, "event_id": calls[0]}) == 1


def test_interruption_after_mongo_ack_does_not_redeliver_agent_action(demo, monkeypatch):
    _client, mongo, _headers, owner_id, _order_id = _owner_with_order(demo)
    real_ack = order_bridge._mark_delivered
    ack_calls = []

    def interrupted(event_id, attempt):
        ack_calls.append(event_id)
        monkeypatch.setattr(order_bridge, "_mark_delivered", real_ack)
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(order_bridge, "_mark_delivered", interrupted)
    first = order_bridge.drain_order_events(mongo, owner_id)
    assert first["retry"] == 1 and first["pending"] == 1
    event = mongo.system_events.find_one({"event_id": ack_calls[0]})
    assert event["status"] == "coordinated" and len(event["agent_responses"]) == 1
    with transaction() as sqlite:
        sqlite.execute("UPDATE agent_event_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE event_id=?", (ack_calls[0],))
    assert order_bridge.drain_order_events(mongo, owner_id)["pending"] == 0
    event = mongo.system_events.find_one({"event_id": ack_calls[0]})
    assert len(event["agent_responses"]) == 1


def test_expired_lease_cannot_be_completed_or_reset_by_old_worker(demo):
    _client, _mongo, _headers, _owner_id, _order_id = _owner_with_order(demo)
    older = order_bridge._claim_due(1)[0]
    with transaction() as sqlite:
        sqlite.execute(
            "UPDATE agent_event_outbox SET next_attempt_at='2000-01-01T00:00:00+00:00' "
            "WHERE event_id=?", (older["event_id"],),
        )
    newer = order_bridge._claim_due(1)[0]
    assert newer["event_id"] == older["event_id"]
    assert newer["attempts"] == older["attempts"] + 1
    assert order_bridge._mark_delivered(older["event_id"], older["attempts"]) is False
    order_bridge._schedule_retry(older["event_id"], older["attempts"])
    with closing(connect()) as sqlite:
        current = sqlite.execute(
            "SELECT status,attempts FROM agent_event_outbox WHERE event_id=?",
            (newer["event_id"],),
        ).fetchone()
        assert current["status"] == "PROCESSING" and current["attempts"] == newer["attempts"]
    assert order_bridge._mark_delivered(newer["event_id"], newer["attempts"]) is True
