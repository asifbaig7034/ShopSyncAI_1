import asyncio
import hashlib
import hmac
import json
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

if not os.getenv("TEST_MONGODB_URI"):
    raise unittest.SkipTest("Real MongoDB integration tests need TEST_MONGODB_URI (replica set/Atlas).")

from fastapi.testclient import TestClient
from app import db as persistence

from app import service, whatsapp
from app.main import app


class OrderFlowTests(unittest.TestCase):
    def setUp(self):
        self.previous = {key: os.environ.get(key) for key in (
            "MONGODB_URI", "MONGODB_DB", "SHOPSYNC_OWNER_TOKEN", "SHOPSYNC_RISK_AMOUNT_CENTS",
            "SHOPSYNC_RISK_THRESHOLD", "WA_APP_SECRET", "SHOPSYNC_RETURN_DAYS",
            "WA_ACCESS_TOKEN", "WA_PHONE_NUMBER_ID", "WA_GRAPH_VERSION", "WA_VERIFY_TOKEN")}
        self.db_name = "shopsync_uc4_test_orders_" + uuid.uuid4().hex
        os.environ["MONGODB_URI"] = os.environ["TEST_MONGODB_URI"]
        os.environ["MONGODB_DB"] = self.db_name
        os.environ["SHOPSYNC_OWNER_TOKEN"] = "test-token-please-change"
        os.environ["SHOPSYNC_RISK_AMOUNT_CENTS"] = "2000000"
        os.environ["SHOPSYNC_RISK_THRESHOLD"] = "50"
        os.environ["SHOPSYNC_RETURN_DAYS"] = "14"
        # Integration tests must never send messages through real Meta credentials.
        for key in ("WA_ACCESS_TOKEN", "WA_PHONE_NUMBER_ID", "WA_GRAPH_VERSION", "WA_VERIFY_TOKEN", "WA_APP_SECRET"):
            os.environ[key] = ""
        persistence.close_connection()
        async def idle_worker():
            await asyncio.Event().wait()
        worker_patch = patch("app.main.outbox_worker", idle_worker)
        worker_patch.start()
        self.addCleanup(worker_patch.stop)
        self.client = TestClient(app)
        self.entered = False
        self.addCleanup(self.cleanup_database)
        self.client.__enter__()
        self.entered = True
        self.auth = {"Authorization": "Bearer test-token-please-change"}
        self.product = self.call("POST", "/api/products", {"sku": "TEE-01", "name": "Shirt",
                                "price_cents": 150000, "stock_qty": 2}, 201)

    def cleanup_database(self):
        try:
            if self.entered:
                self.client.__exit__(None, None, None)
            if not self.db_name.startswith("shopsync_uc4_test_"):
                raise RuntimeError("Refusing to drop non-test database")
            persistence.get_database().client.drop_database(self.db_name)
        finally:
            persistence.close_connection()
            for key, value in self.previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def call(self, method, path, data=None, status=200):
        response = self.client.request(method, path, json=data, headers=self.auth)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def draft(self, key=None, phone="923001234567", qty=1):
        return self.call("POST", "/api/orders/drafts", {
            "phone": phone, "name": "Test customer", "delivery_address": "12 Main Street, Lahore",
            "payment_method": "COD", "items": [{"product_id": self.product["id"], "quantity": qty}],
            "source_key": key or uuid.uuid4().hex}, 201)

    def stock(self):
        return self.call("GET", "/api/products")[0]["stock_qty"]

    def test_owner_auth_and_confirmation_books_once(self):
        self.assertEqual(self.client.get("/api/orders").status_code, 401)
        order = self.draft()
        self.assertEqual(self.stock(), 2)
        self.assertEqual(order["items"][0]["unit_price_cents"], 150000)
        first = self.call("POST", f"/api/orders/{order['id']}/confirm")
        second = self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["risk_reasons"], ["No configured review indicators matched"])
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 150000)

    def test_draft_idempotency_and_cancellation_restore_once(self):
        key = uuid.uuid4().hex
        order = self.draft(key)
        self.assertEqual(self.draft(key)["id"], order["id"])
        self.assertEqual(len(self.call("GET", "/api/orders")), 1)
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("POST", f"/api/orders/{order['id']}/cancel")
        self.call("POST", f"/api/orders/{order['id']}/cancel")
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 0)
        self.call("POST", f"/api/orders/{order['id']}/confirm", status=409)

    def test_risk_hold_and_recheck_on_approval(self):
        os.environ["SHOPSYNC_RISK_AMOUNT_CENTS"] = "100000"
        held = self.draft()
        held = self.call("POST", f"/api/orders/{held['id']}/confirm")
        self.assertEqual(held["status"], "RISK_HOLD")
        self.assertEqual(held["risk_score"], 60)
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 0)
        os.environ["SHOPSYNC_RISK_AMOUNT_CENTS"] = "2000000"
        for _ in range(2):
            order = self.draft(phone="923009990000")
            self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("POST", f"/api/orders/{held['id']}/risk-decision",
                  {"approve": True, "reason": "Owner review"}, 409)
        self.assertEqual(self.call("GET", f"/api/orders/{held['id']}")["status"], "RISK_HOLD")
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 300000)

    def test_parallel_confirmation_cannot_oversell(self):
        first, second = self.draft(), self.draft(phone="923009990000")
        self.call("POST", f"/api/orders/{first['id']}/confirm")
        # One unit remains, and both drafts were created while it was available.
        third = self.draft(phone="923008880000")
        ids = [second["id"], third["id"]]
        gate = threading.Barrier(2)

        def confirm_concurrently(order_id):
            gate.wait()
            try:
                return service.confirm(order_id)["status"]
            except service.OrderError as error:
                return error.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm_concurrently, ids))
        self.assertCountEqual(results, ["CONFIRMED", 409])
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 300000)

    def test_return_waits_for_inspection_and_refund_requires_approval(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("POST", f"/api/orders/{order['id']}/ship")
        self.call("POST", f"/api/orders/{order['id']}/cancel", status=409)
        requested = self.call("POST", f"/api/orders/{order['id']}/returns",
                              {"kind": "RETURN", "reason": "Wrong size"}, 201)
        request_id = requested["return_request"]["id"]
        approved = self.call("POST", f"/api/returns/{request_id}/decision",
                             {"approve": True, "reason": "Within policy"})
        self.assertEqual(approved["return_request"]["status"], "APPROVED_AWAITING_RECEIPT")
        self.assertEqual(self.stock(), 1)
        self.call("POST", f"/api/returns/{request_id}/inspect", {"accept": True})
        self.call("POST", f"/api/returns/{request_id}/inspect", {"accept": True})
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 0)

    def test_refund_only_does_not_restock_and_rejected_request_does_not_reverse_sale(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        request = self.call("POST", f"/api/orders/{order['id']}/returns",
                            {"kind": "REFUND", "reason": "Damaged packaging"}, 201)
        request_id = request["return_request"]["id"]
        self.call("POST", f"/api/returns/{request_id}/decision",
                  {"approve": False, "reason": "Outside policy"})
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 150000)
        second = self.draft(phone="923008887777")
        self.call("POST", f"/api/orders/{second['id']}/confirm")
        request = self.call("POST", f"/api/orders/{second['id']}/returns",
                            {"kind": "REFUND", "reason": "Wrong item"}, 201)
        request_id = request["return_request"]["id"]
        self.call("POST", f"/api/returns/{request_id}/decision",
                  {"approve": True, "reason": "Approved"})
        self.call("POST", f"/api/returns/{request_id}/decision",
                  {"approve": True, "reason": "Duplicate"})
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 150000)

    def test_multi_item_failure_rolls_back_all_stock_and_sale(self):
        other = self.call("POST", "/api/products", {"sku": "CUP-01", "name": "Cup",
                          "price_cents": 80000, "stock_qty": 1}, 201)
        mixed = self.call("POST", "/api/orders/drafts", {
            "phone": "923001234567", "delivery_address": "Lahore", "payment_method": "COD",
            "items": [{"product_id": self.product["id"], "quantity": 1},
                      {"product_id": other["id"], "quantity": 1}], "source_key": uuid.uuid4().hex}, 201)
        competing = self.call("POST", "/api/orders/drafts", {
            "phone": "923009991111", "delivery_address": "Lahore", "payment_method": "COD",
            "items": [{"product_id": other["id"], "quantity": 1}],
            "source_key": uuid.uuid4().hex}, 201)
        self.call("POST", f"/api/orders/{competing['id']}/confirm")
        self.call("POST", f"/api/orders/{mixed['id']}/confirm", status=409)
        stock = {p["sku"]: p["stock_qty"] for p in self.call("GET", "/api/products")}
        self.assertEqual(stock, {"CUP-01": 0, "TEE-01": 2})
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 80000)
        self.assertEqual(self.call("GET", f"/api/orders/{mixed['id']}")["status"], "DRAFT")

    def test_owner_can_edit_draft_before_confirmation(self):
        order = self.draft()
        edited = self.call("PATCH", f"/api/orders/{order['id']}/draft", {
            "items": [{"product_id": self.product["id"], "quantity": 2}],
            "delivery_address": "Updated Lahore address", "payment_method": "BANK_TRANSFER"})
        self.assertEqual(edited["total_cents"], 300000)
        self.assertEqual(edited["payment_method"], "BANK_TRANSFER")
        self.assertEqual(self.stock(), 2)
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("PATCH", f"/api/orders/{order['id']}/draft", {
            "items": [{"product_id": self.product["id"], "quantity": 1}],
            "delivery_address": "Changed"}, 409)

    def test_simulated_chat_ownership_and_duplicate_webhook_message(self):
        message_id = uuid.uuid4().hex
        payload = {"message_id": message_id, "phone": "923001234567", "name": "Ayesha",
                   "text": "ORDER TEE-01 1 | Lahore delivery address | BANK_TRANSFER"}
        reply = self.call("POST", "/api/chat/simulate", payload)["reply"]
        self.assertIn("Delivery address: Lahore delivery address", reply)
        self.assertIn("Payment method: BANK_TRANSFER", reply)
        self.assertIn("Customer: Ayesha (923001234567)", reply)
        self.assertIn("Total: PKR 1,500.00", reply)
        self.assertEqual(self.call("POST", "/api/chat/simulate", payload)["reply"], reply)
        self.assertEqual(len(self.call("GET", "/api/orders")), 1)
        order_id = self.call("GET", "/api/orders")[0]["id"]
        other = {"message_id": uuid.uuid4().hex, "phone": "923009990000",
                 "text": f"STATUS {order_id}"}
        self.assertEqual(self.call("POST", "/api/chat/simulate", other)["reply"], "Order not found.")
        edit = {**other, "message_id": uuid.uuid4().hex, "phone": "923001234567",
                "text": f"EDIT {order_id} TEE-01 2 | Updated Lahore address | COD"}
        edit_reply = self.call("POST", "/api/chat/simulate", edit)["reply"]
        self.assertIn("Updated Lahore address", edit_reply)
        self.assertIn("Payment method: COD", edit_reply)
        own = {**other, "message_id": uuid.uuid4().hex, "phone": "923001234567",
               "text": f"CONFIRM {order_id}"}
        self.assertIn("CONFIRMED", self.call("POST", "/api/chat/simulate", own)["reply"])
        self.assertEqual(self.stock(), 0)

    def test_webhook_rejects_invalid_signature_then_deduplicates(self):
        os.environ["WA_APP_SECRET"] = "test-webhook-secret"
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "messages": [{"id": "wamid.test-1", "from": "923001234567", "type": "text",
                          "text": {"body": "ORDER TEE-01 1 | Lahore | COD"}}]}}]}]}
        data = json.dumps(payload).encode()
        self.assertEqual(self.client.post("/webhooks/whatsapp", content=data,
                                         headers={"x-hub-signature-256": "sha256=invalid"}).status_code, 403)
        signature = "sha256=" + hmac.new(b"test-webhook-secret", data, hashlib.sha256).hexdigest()
        headers = {"x-hub-signature-256": signature}
        self.assertEqual(self.client.post("/webhooks/whatsapp", content=data, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/webhooks/whatsapp", content=data, headers=headers).status_code, 200)
        self.assertEqual(len(self.call("GET", "/api/orders")), 1)
        self.assertEqual(len(self.call("GET", "/api/notifications")), 1)
        order_id = self.call("GET", "/api/orders")[0]["id"]
        service.chat("wamid.test-confirm", "923001234567", f"CONFIRM {order_id}", live=True)
        service.chat("wamid.test-refund", "923001234567", f"REFUND {order_id} Wrong item", live=True)
        request_id = self.call("GET", f"/api/orders/{order_id}")["return_request"]["id"]
        self.call("POST", f"/api/returns/{request_id}/decision",
                  {"approve": False, "reason": "Outside policy"})
        notifications = self.call("GET", "/api/notifications")
        self.assertTrue(any("Reason: Outside policy" in entry["body"] for entry in notifications))

    def test_chat_requires_explicit_payment_choice(self):
        missing = {"message_id": uuid.uuid4().hex, "phone": "923001234567",
                   "text": "ORDER TEE-01 1 | Lahore"}
        reply = self.call("POST", "/api/chat/simulate", missing)["reply"]
        self.assertIn("Choose payment method", reply)
        invalid = {**missing, "message_id": uuid.uuid4().hex,
                   "text": "ORDER TEE-01 1 | Lahore | CHEQUE"}
        reply = self.call("POST", "/api/chat/simulate", invalid)["reply"]
        self.assertIn("COD or BANK_TRANSFER", reply)
        self.assertEqual(self.call("GET", "/api/orders"), [])
        valid = {**missing, "message_id": uuid.uuid4().hex,
                 "text": "ORDER TEE-01 1 | Lahore | COD"}
        self.assertIn("Payment method: COD", self.call("POST", "/api/chat/simulate", valid)["reply"])
        self.assertEqual(self.call("GET", "/api/orders")[0]["payment_method"], "COD")

    def test_rejected_return_requires_reason_and_keeps_request_pending_without_one(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        requested = self.call("POST", f"/api/orders/{order['id']}/returns",
                              {"kind": "REFUND", "reason": "Item damaged"}, 201)
        request_id = requested["return_request"]["id"]
        for note in ("", "   "):
            response = self.call("POST", f"/api/returns/{request_id}/decision",
                                 {"approve": False, "reason": note}, 400)
            self.assertIn("rejection reason", response["detail"])
        still_pending = self.call("GET", f"/api/orders/{order['id']}")
        self.assertEqual(still_pending["return_request"]["status"], "PENDING")
        rejected = self.call("POST", f"/api/returns/{request_id}/decision",
                             {"approve": False, "reason": "Outside return policy"})
        self.assertEqual(rejected["return_request"]["status"], "REJECTED")
        self.assertEqual(rejected["return_request"]["owner_reason"], "Outside return policy")
        status = self.call("POST", "/api/chat/simulate", {
            "message_id": uuid.uuid4().hex, "phone": "923001234567",
            "text": f"STATUS {order['id']}"})["reply"]
        self.assertIn("Reason: Outside return policy", status)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 150000)

    def test_rejected_returned_item_requires_reason_and_keeps_stock_unchanged(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("POST", f"/api/orders/{order['id']}/ship")
        requested = self.call("POST", f"/api/orders/{order['id']}/returns",
                              {"kind": "RETURN", "reason": "Wrong size"}, 201)
        request_id = requested["return_request"]["id"]
        self.call("POST", f"/api/returns/{request_id}/decision",
                  {"approve": True, "reason": "Awaiting returned item"})
        self.call("POST", f"/api/returns/{request_id}/inspect",
                  {"accept": False, "reason": ""}, 400)
        self.assertEqual(self.call("GET", f"/api/orders/{order['id']}")["return_request"]["status"],
                         "APPROVED_AWAITING_RECEIPT")
        rejected = self.call("POST", f"/api/returns/{request_id}/inspect",
                             {"accept": False, "reason": "Item damaged beyond return policy"})
        self.assertEqual(rejected["return_request"]["status"], "REJECTED_AFTER_INSPECTION")
        self.assertEqual(rejected["return_request"]["owner_reason"], "Item damaged beyond return policy")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 150000)
        status = self.call("POST", "/api/chat/simulate", {
            "message_id": uuid.uuid4().hex, "phone": "923001234567",
            "text": f"STATUS {order['id']}"})["reply"]
        self.assertIn("Reason: Item damaged beyond return policy", status)

    def test_refund_prevents_second_reversal_and_shipment(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        requested = self.call("POST", f"/api/orders/{order['id']}/returns",
                              {"kind": "REFUND", "reason": "Damaged item"}, 201)
        self.call("POST", f"/api/returns/{requested['return_request']['id']}/decision",
                  {"approve": True})
        self.call("POST", f"/api/orders/{order['id']}/cancel", status=409)
        self.call("POST", f"/api/orders/{order['id']}/ship", status=409)
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 0)

    def test_pending_refund_prevents_cancellation(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("POST", f"/api/orders/{order['id']}/returns",
                  {"kind": "REFUND", "reason": "Wrong item"}, 201)
        self.call("POST", f"/api/orders/{order['id']}/cancel", status=409)
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 150000)

    def test_cancelled_order_cannot_request_refund(self):
        order = self.draft()
        self.call("POST", f"/api/orders/{order['id']}/confirm")
        self.call("POST", f"/api/orders/{order['id']}/cancel")
        self.call("POST", f"/api/orders/{order['id']}/returns",
                  {"kind": "REFUND", "reason": "Wrong item"}, 409)
        self.assertEqual(self.call("GET", "/api/finance")["booked_net_cents"], 0)

    def test_parallel_confirmation_of_same_order_books_once(self):
        order = self.draft()
        gate = threading.Barrier(2)
        def confirm():
            gate.wait()
            return service.confirm(order["id"])["status"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(confirm) for _ in range(2)]
            self.assertEqual([f.result() for f in futures], ["CONFIRMED", "CONFIRMED"])
        self.assertEqual(self.stock(), 1)
        self.assertEqual(persistence.get_store().count("financial_entries"), 1)

    def test_parallel_chat_message_is_deduplicated(self):
        gate = threading.Barrier(2)
        def message():
            gate.wait()
            return service.chat("same-inbound-id", "923001234567",
                                "ORDER TEE-01 1 | Lahore | COD", live=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(message) for _ in range(2)]
            replies = [f.result() for f in futures]
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(persistence.get_store().count("orders"), 1)
        self.assertEqual(persistence.get_store().count("inbound_messages"), 1)
        self.assertEqual(persistence.get_store().count("outbox"), 1)

    def test_message_id_is_bound_to_original_sender(self):
        first = {"message_id": "sender-bound-id", "phone": "923001234567",
                 "text": "ORDER TEE-01 1 | Lahore | COD"}
        self.call("POST", "/api/chat/simulate", first)
        other = {**first, "phone": "923009990000"}
        response = self.call("POST", "/api/chat/simulate", other, 409)
        self.assertIn("another sender", response["detail"])

    def test_cancel_racing_refund_request_has_one_consistent_outcome(self):
        order = self.draft()
        service.confirm(order["id"])
        gate = threading.Barrier(2)
        def act(which):
            gate.wait()
            try:
                if which == "cancel":
                    return service.cancel(order["id"])["status"]
                return service.request_return(order["id"], "REFUND", "Wrong item")["return_request"]["status"]
            except service.OrderError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(act, ["cancel", "refund"]))
        saved = service.get_order(order["id"])
        if saved["status"] == "CANCELLED":
            self.assertEqual(self.stock(), 2)
            self.assertEqual(service.financial_summary()["booked_net_cents"], 0)
            self.assertIsNone(saved["return_request"])
        else:
            self.assertEqual(saved["return_request"]["status"], "PENDING")
            self.assertEqual(self.stock(), 1)
            self.assertEqual(service.financial_summary()["booked_net_cents"], 150000)
        self.assertIn(409, results)

    def test_chat_multi_item_failure_rolls_back_and_records_error(self):
        cup = service.add_product("CUP-01", "Cup", 80000, 1)
        mixed = service.create_draft("923001234567", "Ayesha",
            [{"product_id": self.product["id"], "quantity": 1},
             {"product_id": cup["id"], "quantity": 1}], "Lahore", "COD", uuid.uuid4().hex)
        competing = service.create_draft("923009990000", "Other",
            [{"product_id": cup["id"], "quantity": 1}], "Lahore", "COD", uuid.uuid4().hex)
        service.confirm(competing["id"])
        text = f"CONFIRM {mixed['id']}"
        reply = service.chat("failed-confirm-message", "923001234567", text)
        self.assertIn("Insufficient stock", reply)
        self.assertEqual(service.chat("failed-confirm-message", "923001234567", text), reply)
        self.assertEqual(self.stock_for("TEE-01"), 2)
        self.assertEqual(service.get_order(mixed["id"])["status"], "DRAFT")
        self.assertEqual(service.financial_summary()["booked_net_cents"], 80000)
        self.assertEqual(persistence.get_store().count("inbound_messages"), 1)

    def stock_for(self, sku):
        return next(p["stock_qty"] for p in service.product_list() if p["sku"] == sku)

    def test_application_restart_keeps_products_and_orders(self):
        order = self.draft()
        service.confirm(order["id"])
        self.client.__exit__(None, None, None)
        self.entered = False
        self.client.__enter__()
        self.entered = True
        self.assertEqual(self.stock(), 1)
        self.assertEqual(service.get_order(order["id"])["status"], "CONFIRMED")

    def test_only_one_worker_can_claim_a_pending_message(self):
        service.chat("claim-message", "923001234567", "HELP", live=True)
        gate = threading.Barrier(2)
        def claim():
            gate.wait()
            return whatsapp.claim_due()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            claimed = [future.result() for future in futures]
        self.assertEqual(sum(message is not None for message in claimed), 1)
        message = next(message for message in claimed if message is not None)
        persistence.get_database()["outbox"].update_one(
            {"_id": message["_id"]}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}})
        self.assertIsNone(whatsapp.claim_due())
        self.assertEqual(persistence.get_store().get("outbox", {"_id": message["_id"]})["status"], "UNCERTAIN")

    def test_stale_worker_cannot_finish_another_claim(self):
        service.chat("claim-owner-message", "923001234567", "HELP", live=True)
        original = whatsapp.claim_due()
        persistence.get_database()["outbox"].update_one(
            {"_id": original["_id"]}, {"$set": {"claim_id": "new-worker-claim"}})
        whatsapp.finish(original, status="SENT", provider_id="old-provider-id")
        saved = persistence.get_store().get("outbox", {"_id": original["_id"]})
        self.assertEqual(saved["status"], "SENDING")
        self.assertEqual(saved["claim_id"], "new-worker-claim")

    def test_missing_whatsapp_credentials_keeps_order_and_records_delivery_state(self):
        service.chat("live-draft-test", "923001234567", "ORDER TEE-01 1 | Lahore | COD", live=True)
        asyncio.run(whatsapp.send_due())
        self.assertEqual(persistence.get_store().count("orders"), 1)
        self.assertEqual(persistence.get_store().all("outbox")[0]["status"], "NEEDS_CONFIGURATION")


if __name__ == "__main__":
    unittest.main()
