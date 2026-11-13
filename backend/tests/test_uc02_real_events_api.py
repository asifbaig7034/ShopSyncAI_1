"""A real UC-4 order reaches UC-2's owner-visible, isolated event history."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def event_settings(monkeypatch):
    monkeypatch.setenv("SHOPSYNC_LOW_STOCK_THRESHOLD", "5")
    monkeypatch.setenv("SHOPSYNC_RISK_AMOUNT_CENTS", "2000000")


def _real_owner(client):
    response = client.post("/api/account/signup", json={
        "name": "Brand Owner",
        "business_name": "Test Brand",
        "email": "events-owner@example.com",
        "password": "a-private-password-123",
    })
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_confirmed_order_coordinates_real_events_without_exposing_customer_data(demo):
    client, db, demo_sample, demo_headers = demo
    synthetic = client.post("/api/owner/demo-events/low-stock", headers=demo_headers)
    assert synthetic.status_code == 200, synthetic.text
    headers = _real_owner(client)

    # Real and synthetic events belong to different owners and cannot be mixed.
    initially = client.get("/api/owner/events", headers=headers)
    assert initially.status_code == 200, initially.text
    assert initially.json() == {"events": [], "pending": 0}

    item = client.post("/api/products", headers=headers, json={
        "sku": "TEE-UC2", "name": "Cotton Tee", "price_cents": 125_000,
        "stock_qty": 5,
    })
    assert item.status_code == 201, item.text
    draft = client.post("/api/orders/drafts", headers=headers, json={
        "phone": "923001234567", "name": "Example Customer",
        "items": [{"product_id": item.json()["id"], "quantity": 1}],
        "delivery_address": "Example Street, Lahore", "payment_method": "COD",
        "source_key": uuid4().hex,
    })
    assert draft.status_code == 201, draft.text
    order_id = draft.json()["id"]
    assert client.get("/api/owner/events", headers=headers).json()["events"] == []

    confirmed = client.post(f"/api/orders/{order_id}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 4

    fetched = client.get("/api/owner/events", headers=headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["pending"] == 0
    events = {event["event_type"]: event for event in body["events"]}
    assert set(events) == {"order.confirmed", "stock.low"}
    assert all(event["event_id"].startswith("uc4:") for event in events.values())
    assert all(event["status"] == "coordinated" for event in events.values())
    assert events["order.confirmed"]["target_agents"] == ["inventory_logistics"]
    assert events["stock.low"]["target_agents"] == ["customer_commerce"]
    assert all(len(event["agent_responses"]) == 1 for event in events.values())
    assert events["stock.low"]["details"]["stock_quantity"] == 4
    assert events["order.confirmed"]["details"]["order_id"] == order_id
    assert "923001234567" not in fetched.text
    assert "Example Street" not in fetched.text
    assert "Example Customer" not in fetched.text

    # Reading or replaying a completed order must never generate another event.
    again = client.post(f"/api/orders/{order_id}/confirm", headers=headers)
    assert again.status_code == 200, again.text
    replayed = client.get("/api/owner/events", headers=headers).json()
    assert {event["event_id"] for event in replayed["events"]} == {
        event["event_id"] for event in body["events"]
    }

    demo_feed = client.get("/api/owner/demo-events", headers=demo_headers)
    assert demo_feed.status_code == 200, demo_feed.text
    assert demo_feed.json()["simulated"] is True
    assert all(not event["event_id"].startswith("uc4:") for event in demo_feed.json()["events"])
    assert db.system_events.count_documents({"owner_id": demo_sample["owner_id"],
                                             "event_id": {"$regex": "^uc4:"}}) == 0


def test_real_events_require_signed_in_owner_and_respect_disabled_agent(demo):
    client, _db, _sample, demo_headers = demo
    assert client.get("/api/owner/events").status_code == 401
    assert client.get("/api/owner/events", headers=demo_headers).status_code == 403
    headers = _real_owner(client)
    saved = client.patch("/api/account/settings", headers=headers, json={
        "agent_settings": {"customer_commerce": False},
    })
    assert saved.status_code == 200, saved.text

    item = client.post("/api/products", headers=headers, json={
        "sku": "SHIRT-EVENT", "name": "Test Shirt", "price_cents": 50_000,
        "stock_qty": 5,
    })
    assert item.status_code == 201, item.text
    draft = client.post("/api/orders/drafts", headers=headers, json={
        "phone": "923009999999", "name": "Test Customer",
        "items": [{"product_id": item.json()["id"], "quantity": 1}],
        "delivery_address": "Test Street", "payment_method": "COD",
        "source_key": uuid4().hex,
    })
    assert draft.status_code == 201, draft.text
    confirmed = client.post(f"/api/orders/{draft.json()['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.text

    events_response = client.get("/api/owner/events", headers=headers)
    assert events_response.status_code == 200, events_response.text
    events = {event["event_type"]: event for event in events_response.json()["events"]}
    assert events["stock.low"]["target_agents"] == []
    assert events["stock.low"]["status"] == "unhandled"
    assert events["order.confirmed"]["status"] == "coordinated"
    assert client.get("/api/owner/events", headers=demo_headers).status_code == 403

    assert client.post("/api/account/logout", headers=headers).status_code == 204
    assert client.get("/api/owner/events", headers=headers).status_code == 401


def test_customer_chat_confirmation_reaches_real_agent_event_feed(demo):
    client, _db, _sample, _demo_headers = demo
    headers = _real_owner(client)
    item = client.post("/api/products", headers=headers, json={
        "sku": "CHAT-UC2", "name": "Chat Product", "price_cents": 80_000,
        "stock_qty": 5,
    })
    assert item.status_code == 201, item.text

    started = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": "923001234567", "external_id": "first-chat-order",
        "text": "ORDER CHAT-UC2 1 | Lahore | COD",
    })
    assert started.status_code == 200, started.text
    order_id = client.get("/api/orders", headers=headers).json()[0]["id"]
    assert client.get("/api/owner/events", headers=headers).json()["events"] == []

    confirmed = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": "923001234567", "external_id": "confirm-chat-order",
        "text": f"CONFIRM {order_id}",
    })
    assert confirmed.status_code == 200, confirmed.text
    assert "CONFIRMED" in confirmed.json()["conversation"]["messages"][-1]["text"]

    feed = client.get("/api/owner/events", headers=headers)
    assert feed.status_code == 200, feed.text
    assert {event["event_type"] for event in feed.json()["events"]} == {
        "stock.low", "order.confirmed",
    }
    assert all(event["status"] == "coordinated" for event in feed.json()["events"])
