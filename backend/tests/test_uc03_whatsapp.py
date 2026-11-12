"""UC-3 integrated owner inbox, synthetic customer chat and safe channel boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event

import pytest

from backend.app.application.services import customer_service, order_service


def owner_login(client):
    response = client.post("/api/account/signup", json={
        "name": "Brand Owner", "business_name": "Own Brand",
        "email": "uc3-owner@samplebrand.com", "password": "a-private-password-123",
    })
    assert response.status_code == 201, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_simulator_owner_auth_and_disabled_live_webhook(demo):
    client, _db, _, demo_headers = demo
    msg = {"customer_id": "923001234567", "text": "Hello"}
    assert client.post("/api/demo/messages", json=msg).status_code == 401
    assert client.post("/api/demo/messages", headers=demo_headers, json=msg).status_code == 403
    headers = owner_login(client)
    assert client.post("/api/demo/messages", headers=headers, json=msg).status_code == 200
    assert client.get("/api/webhooks/whatsapp").status_code == 503
    assert client.post("/api/webhooks/whatsapp", json={"entry": []}).status_code == 503


def test_customer_reply_uses_uc4_products_in_three_languages(demo, monkeypatch):
    client, _db, _, _ = demo
    headers = owner_login(client)
    monkeypatch.setattr(order_service, "list_products", lambda owner: [{
        "id": "p1", "sku": "SHIRT-1", "name": "Cotton Shirt", "price_cents": 120000,
        "stock_qty": 3,
    }])
    samples = [
        ("en", "Cotton Shirt price?", "Price: PKR 1,200.00"),
        ("roman", "Cotton Shirt ki qeemat kya hai?", "Qeemat: PKR 1,200.00"),
        ("ur", "Cotton Shirt کی قیمت کیا ہے؟", "قیمت: PKR 1,200.00"),
    ]
    for customer_id, text, expected in samples:
        response = client.post("/api/demo/messages", headers=headers, json={
            "customer_id": customer_id, "external_id": customer_id, "text": text,
        })
        assert response.status_code == 200, response.text
        thread = response.json()["conversation"]
        assert expected in thread["messages"][-1]["text"]
        assert thread["messages"][-1]["delivery_status"] == "simulated"
        assert thread["id"] and thread["business_id"]
    items = client.get("/api/conversations", headers=headers).json()["items"]
    assert len(items) == 3 and items[0]["last_message"]["text"]


def test_inbound_idempotency_owner_takeover_and_reply(demo, monkeypatch):
    client, _db, _, _ = demo
    headers = owner_login(client)
    commands = []

    def command(owner, message_id, phone, text, name=""):
        commands.append((message_id, phone, text))
        return "Order draft created; please confirm it"

    monkeypatch.setattr(order_service, "process_customer_command", command)
    payload = {"customer_id": "923001234567", "external_id": "order-one", "text": "ORDER SHIRT-1 1 | Lahore | COD"}
    first = client.post("/api/demo/messages", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["conversation"]["messages"][-1]["text"].startswith("Order draft")
    second = client.post("/api/demo/messages", headers=headers, json=payload)
    assert second.status_code == 200
    assert len(second.json()["conversation"]["messages"]) == 2
    assert len(commands) == 1
    changed = client.post("/api/demo/messages", headers=headers, json={**payload, "text": "CANCEL other"})
    assert changed.status_code == 409

    cid = first.json()["conversation"]["id"]
    takeover = client.post(f"/api/conversations/{cid}/handover", headers=headers, json={"mode": "human"})
    assert takeover.status_code == 200
    reply = {"external_id": "owner-reply-one", "text": "I can help with this order."}
    send = client.post(f"/api/conversations/{cid}/messages", headers=headers, json=reply)
    assert send.status_code == 200 and send.json()["conversation"]["messages"][-1]["role"] == "owner"
    assert client.post(f"/api/conversations/{cid}/messages", headers=headers, json=reply).status_code == 200
    assert len(client.get(f"/api/conversations/{cid}", headers=headers).json()["conversation"]["messages"]) == 3
    assert client.post(f"/api/conversations/{cid}/messages", headers=headers,
                       json={**reply, "text": "Different reply"}).status_code == 409
    post_takeover = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": payload["customer_id"], "external_id": "other", "text": "Cotton Shirt price?",
    })
    assert post_takeover.status_code == 200
    assert post_takeover.json()["conversation"]["messages"][-1]["role"] == "customer"


def test_agent_off_and_unsupported_order_request_hand_over(demo):
    client, db, _, _ = demo
    headers = owner_login(client)
    switched = client.patch("/api/account/settings", headers=headers, json={
        "agent_settings": {"customer_commerce": False},
    })
    assert switched.status_code == 200
    off = client.post("/api/demo/messages", headers=headers,
                      json={"customer_id": "off", "text": "Hi", "external_id": "a"})
    assert off.status_code == 200
    assert off.json()["conversation"]["mode"] == "human"
    client.patch("/api/account/settings", headers=headers, json={
        "agent_settings": {"customer_commerce": True},
    })
    unclear = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": "needs-help", "text": "I want to buy something", "external_id": "b",
    })
    assert unclear.status_code == 200
    assert unclear.json()["conversation"]["mode"] == "human"
    assert not db.orders.find_one({"customer_id": "needs-help"})


def test_conversation_id_scoped_to_real_owner(demo):
    client, db, _, demo_headers = demo
    headers = owner_login(client)
    response = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": "target-customer", "text": "Hello", "external_id": "one",
    })
    cid = response.json()["conversation"]["id"]
    assert client.get(f"/api/conversations/{cid}", headers=demo_headers).status_code == 403
    assert client.get("/api/conversations/missing", headers=headers).status_code == 404
    assert db.conversations.count_documents({"owner_id": response.json()["conversation"]["business_id"]}) == 1


def test_owner_reply_requires_human_mode(demo):
    client, _, _, _ = demo
    headers = owner_login(client)
    response = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": "1234567", "text": "Hi", "external_id": "one",
    })
    cid = response.json()["conversation"]["id"]
    assert client.post(f"/api/conversations/{cid}/messages", headers=headers,
                       json={"text": "Owner reply"}).status_code == 409


def test_customer_chat_places_real_uc4_order_and_keeps_customer_data_private(demo, monkeypatch, tmp_path):
    """Exercise the actual UC-3 to UC-4 boundary with one temporary ledger."""
    client, _db, _, _ = demo
    monkeypatch.setenv("SHOPSYNC_DB", str(tmp_path / "uc3-uc4.sqlite3"))
    headers = owner_login(client)
    created = client.post("/api/products", headers=headers, json={
        "sku": "TEE-01", "name": "Cotton Shirt", "price_cents": 90000, "stock_qty": 2,
    })
    assert created.status_code == 201, created.text
    draft_message = {"customer_id": "923001234567", "external_id": "first-tee-order",
                     "text": "ORDER TEE-01 1 | 12 Main Street, Lahore | COD"}
    first = client.post("/api/demo/messages", headers=headers, json=draft_message)
    assert first.status_code == 200, first.text
    assert "CONFIRM" in first.json()["conversation"]["messages"][-1]["text"]
    assert client.post("/api/demo/messages", headers=headers, json=draft_message).status_code == 200
    orders = client.get("/api/orders", headers=headers).json()
    assert len(orders) == 1 and orders[0]["status"] == "DRAFT"
    order_id = orders[0]["id"]

    confirmed = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": draft_message["customer_id"], "external_id": "confirm-tee-order",
        "text": f"CONFIRM {order_id}",
    })
    assert confirmed.status_code == 200, confirmed.text
    assert "CONFIRMED" in confirmed.json()["conversation"]["messages"][-1]["text"]
    assert client.get("/api/products", headers=headers).json()[0]["stock_qty"] == 1
    other = client.post("/api/demo/messages", headers=headers, json={
        "customer_id": "923009999999", "external_id": "someone-else-status",
        "text": f"STATUS {order_id}",
    })
    assert other.status_code == 200
    assert "Order not found" in other.json()["conversation"]["messages"][-1]["text"]
    assert "12 Main Street" not in other.text


def test_owner_takeover_waits_for_inflight_local_order_command(demo, monkeypatch):
    """The owner cannot silently suppress the acknowledgement after an order commits."""
    client, db, _, _ = demo
    owner_login(client)
    owner = db.owners.find_one({"email": "uc3-owner@samplebrand.com"})
    customer_id = "923001234567"
    cid = customer_service._id(str(owner["_id"]), customer_id)
    inside_order = Event()
    release_order = Event()
    entering_handover = Event()

    def command(*_args, **_kwargs):
        inside_order.set()
        if not release_order.wait(2):
            raise RuntimeError("Order test timed out")
        return "Order draft recorded"

    monkeypatch.setattr(order_service, "process_customer_command", command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        submitted = pool.submit(customer_service.receive_demo_message, db, owner, customer_id,
                                "ORDER TEE-01 1 | Lahore | COD", "order-inflight")
        assert inside_order.wait(2)

        def takeover():
            entering_handover.set()
            return customer_service.change_mode(db, owner, cid, "human")

        handover = pool.submit(takeover)
        try:
            assert entering_handover.wait(2)
            with pytest.raises(FutureTimeout):
                handover.result(timeout=0.05)
        finally:
            release_order.set()
        first = submitted.result(timeout=2)
        taken_over = handover.result(timeout=2)

    assert first["messages"][-1]["text"] == "Order draft recorded"
    assert taken_over["mode"] == "human"
    assert taken_over["messages"][-1]["text"] == "Order draft recorded"


def test_language_and_id_helpers_stable():
    assert customer_service.language_of("یہ کتنے کی ہے؟") == "ur"
    assert customer_service.language_of("yeh kitne ki hai?") == "roman_ur"
