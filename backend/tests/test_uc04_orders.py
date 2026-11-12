"""Real owner sessions, UC-4 transactions, and UC-2 read integration."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def owner_orders(demo, tmp_path, monkeypatch):
    # UC-4 uses a new disposable ledger for each test. Never use a real owner's
    # database or the data/ file from a developer's working directory.
    monkeypatch.setenv("SHOPSYNC_DB", str(tmp_path / "shopsync-orders.sqlite3"))
    client, db, _sample, demo_headers = demo
    result = client.post("/api/account/signup", json={
        "name": "Brand Owner", "business_name": "Test Brand", "email": "uc4@example.com",
        "password": "a-private-password-123",
    })
    assert result.status_code == 201, result.text
    owner_headers = {"Authorization": f"Bearer {result.json()['access_token']}"}
    return client, db, owner_headers, demo_headers


def product(client, headers, stock=2, price=150_000):
    created = client.post("/api/products", headers=headers, json={
        "sku": "TEE-01", "name": "Cotton Tee", "price_cents": price, "stock_qty": stock,
    })
    assert created.status_code == 201, created.text
    return created.json()


def draft(client, headers, product_id, source_key=None, phone="923001234567"):
    return client.post("/api/orders/drafts", headers=headers, json={
        "phone": phone, "name": "Customer", "items": [{"product_id": product_id, "quantity": 1}],
        "delivery_address": "Main Street, Lahore", "payment_method": "COD",
        "source_key": source_key or uuid4().hex,
    })


def test_uc4_owner_login_order_and_uc2_reads_same_ledger(owner_orders):
    client, _db, headers, demo_headers = owner_orders
    assert client.get("/api/orders").status_code == 401
    assert client.get("/api/products", headers=demo_headers).status_code == 403
    item = product(client, headers)
    assert item["stock_qty"] == 2
    assert client.get("/api/products", headers=headers).json()[0]["sku"] == "TEE-01"
    created = draft(client, headers, item["id"])
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 2

    confirmed = client.post(f"/api/orders/{order_id}/confirm", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 1
    assert client.get("/api/finance", headers=headers).json()["booked_net_cents"] == 150_000
    owner_chat = client.post("/api/owner/chat", headers=headers,
                             json={"message": "Show products, stock and recent orders"})
    assert owner_chat.status_code == 200, owner_chat.text
    assert "Cotton Tee" in owner_chat.json()["reply"]
    assert order_id in owner_chat.json()["reply"]

    cancelled = client.post(f"/api/orders/{order_id}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 2
    assert client.get("/api/finance", headers=headers).json()["booked_net_cents"] == 0
    assert client.get("/api/audit", headers=headers).json()


def test_uc4_source_key_replay_must_match_customer_and_items(owner_orders):
    client, _db, headers, _demo_headers = owner_orders
    item = product(client, headers)
    key = uuid4().hex
    first = draft(client, headers, item["id"], source_key=key)
    assert first.status_code == 201
    same = draft(client, headers, item["id"], source_key=key)
    assert same.status_code == 201 and same.json()["id"] == first.json()["id"]
    different = draft(client, headers, item["id"], source_key=key, phone="923009999999")
    assert different.status_code == 409
    assert len(client.get("/api/orders", headers=headers).json()) == 1


def test_uc4_risk_requires_approval_and_rechecks_stock(owner_orders, monkeypatch):
    client, _db, headers, _demo_headers = owner_orders
    monkeypatch.setenv("SHOPSYNC_RISK_AMOUNT_CENTS", "100000")
    item = product(client, headers)
    new_order = draft(client, headers, item["id"]).json()
    held = client.post(f"/api/orders/{new_order['id']}/confirm", headers=headers)
    assert held.status_code == 200 and held.json()["status"] == "RISK_HOLD"
    assert held.json()["risk_reasons"]
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 2
    approved = client.post(f"/api/orders/{new_order['id']}/risk-decision", headers=headers,
                           json={"approve": True, "reason": "Verified"})
    assert approved.status_code == 200 and approved.json()["status"] == "CONFIRMED"
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 1


def test_uc4_customer_command_replay_and_phone_isolation(owner_orders):
    client, _db, headers, _demo_headers = owner_orders
    product(client, headers)
    message_id = uuid4().hex
    message = {"message_id": message_id, "phone": "923001234567", "name": "Customer",
               "text": "ORDER TEE-01 1 | Main Street, Lahore | COD"}
    first = client.post("/api/chat/simulate", headers=headers, json=message)
    assert first.status_code == 200 and "Reply CONFIRM" in first.json()["reply"]
    again = client.post("/api/chat/simulate", headers=headers, json=message)
    assert again.status_code == 200 and again.json() == first.json()
    assert len(client.get("/api/orders", headers=headers).json()) == 1
    order_id = client.get("/api/orders", headers=headers).json()[0]["id"]
    wrong = client.post("/api/chat/simulate", headers=headers, json={
        "message_id": uuid4().hex, "phone": "923009999999", "text": f"STATUS {order_id}",
    })
    assert wrong.status_code == 200 and wrong.json()["reply"] == "Order not found."
    recycled = client.post("/api/chat/simulate", headers=headers, json={
        **message, "phone": "923009999999",
    })
    assert recycled.status_code == 409
