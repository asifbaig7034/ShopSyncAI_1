"""Provider and callback regression tests with mocked HTTP and an isolated store.

These exercise real delivery/worker/webhook functions without credentials or
network access. They do not substitute for a real MongoDB transaction test.
"""
import ast
import asyncio
import copy
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import types
import unittest
import uuid
from unittest.mock import patch

import httpx
from test_logic import MemoryStore, matches


class DeliveryStore(MemoryStore):
    def get(self, collection, query):
        return next((copy.deepcopy(row) for row in self.all(collection, query)), None)

    def all(self, collection, query=None, sort=None, limit=0):
        def match(row):
            for key, value in (query or {}).items():
                actual = row.get("id" if key == "_id" else key)
                if isinstance(actual, list) and not isinstance(value, (dict, list)):
                    if value not in actual:
                        return False
                elif not matches(row, {key: value}):
                    return False
            return True
        rows = [copy.deepcopy(row) for row in self.documents.get(collection, []) if match(row)]
        for key, direction in reversed(sort or []):
            rows.sort(key=lambda row: row.get("id" if key == "_id" else key), reverse=direction < 0)
        return rows[:limit] if limit else rows


class DeliveryDatabase:
    def __init__(self):
        self.store, self.lock = DeliveryStore(), threading.RLock()

    def run_transaction(self, callback):
        with self.lock:
            snapshot = copy.deepcopy(self.store.documents)
            try:
                return callback(self.store)
            except Exception:
                self.store.documents = snapshot
                raise


class HttpError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class DeliveryOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.prefix = "_uc4_delivery_" + uuid.uuid4().hex
        package = types.ModuleType(cls.prefix)
        package.__path__ = [str(cls.root / "app")]
        cls.db_module = types.ModuleType(cls.prefix + ".db")
        cls.db_module.get_store = lambda: cls.target.store
        cls.db_module.run_transaction = lambda callback: cls.target.run_transaction(callback)
        sys.modules[cls.prefix] = package
        sys.modules[cls.prefix + ".db"] = cls.db_module
        for name in ("service", "delivery", "whatsapp"):
            spec = importlib.util.spec_from_file_location(cls.prefix + "." + name, cls.root / "app" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(cls, name, module)

    @classmethod
    def tearDownClass(cls):
        for name in ("whatsapp", "delivery", "service", "db"):
            sys.modules.pop(cls.prefix + "." + name, None)
        sys.modules.pop(cls.prefix, None)

    def setUp(self):
        type(self).target = DeliveryDatabase()
        self.store = self.target.store
        self.environment = patch.dict(os.environ, {
            "WA_ACCESS_TOKEN": "offline-test-token", "WA_PHONE_NUMBER_ID": "12345",
            "WA_GRAPH_VERSION": "v99.0", "WA_APP_SECRET": "offline-webhook-secret",
            "SHOPSYNC_RISK_AMOUNT_CENTS": "2000000", "SHOPSYNC_RISK_THRESHOLD": "50"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.service.chat("inbound-start", "923001234567", "HELP", live=True)
        self.message_id = self.store.all("outbox")[0]["id"]

    def saved(self):
        return self.store.get("outbox", {"_id": self.message_id})

    def accepted(self, provider="wamid.first"):
        message = self.whatsapp.claim_due()
        self.whatsapp.finish(message, status="ACCEPTED", provider_id=provider, last_error=None)
        return message

    def event(self, status, provider="wamid.first", timestamp="1730000000", code=None, **extra):
        value = dict(id=provider, status=status, timestamp=timestamp, recipient_id="923001234567")
        value.update(extra)
        if code is not None:
            value["errors"] = [{"code": code}]
        return self.delivery.handle_delivery_status(value)

    def due_now(self):
        self.store.update("outbox", {"_id": self.message_id},
                          {"$set": {"next_attempt_at": "2000-01-01T00:00:00+00:00"}})

    def response(self, status=200, data=None, content=None):
        kwargs = {"content": content} if content is not None else {"json": data}
        return httpx.Response(status, request=httpx.Request("POST", "https://graph.facebook.com/offline-test"), **kwargs)

    def send_with(self, response=None, failure=None, before_response=None):
        calls = []
        class Client:
            async def __aenter__(inner):
                return inner
            async def __aexit__(inner, *args):
                return False
            async def post(inner, *args, **kwargs):
                calls.append(kwargs["json"])
                if before_response:
                    before_response()
                if failure:
                    raise failure
                return response
        with patch.object(self.whatsapp.httpx, "AsyncClient", return_value=Client()):
            asyncio.run(self.whatsapp.send_due(limit=1))
        return calls

    def test_http_acceptance_is_distinct_from_delivery(self):
        self.send_with(self.response(data={"messages": [{"id": "wamid.first"}]}))
        self.assertEqual(self.saved()["status"], "ACCEPTED")
        self.event("delivered")
        self.assertEqual(self.saved()["status"], "DELIVERED")

    def test_transient_failure_retries_once_without_duplicate_rescheduling(self):
        self.accepted()
        self.assertEqual(self.event("failed", code=131000)["outcome"], "FAILED")
        due = self.saved()["next_attempt_at"]
        self.assertEqual(self.event("failed", code=131000)["outcome"], "DUPLICATE")
        self.assertEqual(self.saved()["next_attempt_at"], due)
        self.assertEqual(self.store.count("delivery_events"), 1)
        self.assertEqual(self.saved()["attempts"], 1)

    def test_permanent_template_and_configuration_failures_need_action(self):
        self.accepted()
        for code, expected in ((131026, "MANUAL_REVIEW"), (131047, "NEEDS_TEMPLATE"), (190, "NEEDS_CONFIGURATION")):
            with self.subTest(code=code):
                self.store.update("outbox", {"_id": self.message_id},
                                  {"$set": {"status": "ACCEPTED", "delivery_timestamp": -1, "delivery_rank": 0}})
                self.event("failed", timestamp=str(1730000000 + code), code=code)
                self.assertEqual(self.saved()["status"], expected)

    def test_callback_before_provider_mapping_is_replayed_by_finish(self):
        message = self.whatsapp.claim_due()
        result = self.event("failed", code=131000)
        self.assertEqual(result["outcome"], "WAITING_FOR_MESSAGE")
        self.whatsapp.finish(message, status="ACCEPTED", provider_id="wamid.first")
        self.assertEqual(self.saved()["status"], "FAILED")
        self.assertTrue(self.store.all("delivery_events")[0]["applied"])

    def test_periodic_reconciliation_covers_callback_commit_race(self):
        self.accepted()
        value = self.delivery.normalize_status({"id": "wamid.first", "status": "delivered",
                                               "timestamp": "1730000000"})
        self.store.insert("delivery_events", value)
        self.delivery.reconcile_waiting()
        self.assertEqual(self.saved()["status"], "DELIVERED")

    def test_unknown_callbacks_are_buffered_without_creating_outbox_records(self):
        self.event("sent", provider="wamid.unknown")
        self.assertEqual(self.store.count("outbox"), 1)
        self.assertEqual(self.saved()["status"], "PENDING")
        self.assertFalse(self.store.all("delivery_events")[0]["applied"])

    def test_read_and_delivery_never_regress_on_late_callbacks(self):
        self.accepted()
        self.event("read", timestamp="1730000003")
        self.event("delivered", timestamp="1730000002")
        self.event("sent", timestamp="1730000001")
        self.event("failed", timestamp="1730000004", code=131000)
        self.assertEqual(self.saved()["status"], "READ")
        self.assertIsNone(self.whatsapp.claim_due())

    def test_old_attempt_failure_does_not_override_new_acceptance(self):
        self.accepted()
        self.event("failed", code=131000)
        self.due_now()
        self.accepted("wamid.second")
        result = self.event("failed", timestamp="1730000010", code=131016)
        self.assertEqual(result["outcome"], "STALE_ATTEMPT")
        self.assertEqual(self.saved()["status"], "ACCEPTED")
        self.assertEqual(self.saved()["provider_id"], "wamid.second")

    def test_old_attempt_delivery_stops_new_attempt_retries(self):
        self.accepted()
        self.event("failed", code=131000)
        self.due_now()
        current = self.whatsapp.claim_due()
        self.event("delivered", timestamp="1730000020")
        self.whatsapp.finish(current, status="ACCEPTED", provider_id="wamid.second")
        self.assertEqual(self.saved()["status"], "DELIVERED")
        self.assertIn("wamid.second", self.saved()["provider_ids"])

    def test_recipient_mismatch_and_malformed_status_are_safe(self):
        self.accepted()
        result = self.event("failed", code=131000, recipient_id="923009990000")
        self.assertEqual(result["outcome"], "RECIPIENT_MISMATCH")
        self.assertEqual(self.saved()["status"], "ACCEPTED")
        count = self.store.count("delivery_events")
        for bad in ([], {}, None, 123):
            result = self.delivery.handle_delivery_status(
                {"id": "wamid.first", "status": bad, "timestamp": "1730000000"})
            self.assertFalse(result["handled"])
        self.assertEqual(self.store.count("delivery_events"), count)
        # A malformed recipient must not suppress a later valid receipt.
        self.event("failed", code=131000)
        self.assertEqual(self.saved()["status"], "FAILED")

    def test_expired_lease_requires_review_and_stale_finish_is_ignored(self):
        message = self.whatsapp.claim_due()
        self.store.update("outbox", {"_id": self.message_id},
                          {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}})
        self.assertIsNone(self.whatsapp.claim_due())
        self.assertEqual(self.saved()["status"], "UNCERTAIN")
        self.assertFalse(self.whatsapp.finish(message, status="SENT", provider_id="wamid.late"))
        self.assertEqual(self.saved()["status"], "UNCERTAIN")

    def test_fifth_transient_attempt_requires_review(self):
        self.store.update("outbox", {"_id": self.message_id}, {"$set": {"attempts": 4}})
        self.accepted()
        self.event("failed", code=131000)
        self.assertEqual(self.saved()["attempts"], 5)
        self.assertEqual(self.saved()["status"], "MANUAL_REVIEW")

    def test_http_rate_limit_is_retried_but_bad_recipient_is_not(self):
        self.send_with(self.response(429, {"error": {"code": 130429}}))
        self.assertEqual(self.saved()["status"], "FAILED")
        self.due_now()
        self.send_with(self.response(400, {"error": {"code": 131026}}))
        self.assertEqual(self.saved()["status"], "MANUAL_REVIEW")

    def test_ambiguous_timeout_is_not_automatically_resent(self):
        calls = self.send_with(failure=httpx.ReadTimeout("offline simulated timeout"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.saved()["status"], "UNCERTAIN")
        self.assertIsNone(self.whatsapp.claim_due())

    def test_malformed_success_response_requires_review(self):
        self.send_with(self.response(200, content=b"{malformed"))
        self.assertEqual(self.saved()["status"], "UNCERTAIN")

    def test_mock_provider_callback_before_http_return_updates_delivery(self):
        self.send_with(self.response(data={"messages": [{"id": "wamid.first"}]}),
                       before_response=lambda: self.event("failed", code=131000))
        self.assertEqual(self.saved()["status"], "FAILED")

    def test_delivery_failure_never_reverses_order_stock_or_sale(self):
        product = self.service.add_product("TEST-01", "Test", 150000, 2)
        order = self.service.create_draft("923001234567", "Ayesha",
            [{"product_id": product["id"], "quantity": 1}], "Lahore", "COD", "order-source-test")
        self.service.confirm(order["id"])
        snapshot = {name: self.store.all(name) for name in ("orders", "products", "financial_entries", "stock_movements")}
        self.accepted()
        self.event("failed", code=131000)
        self.assertEqual(snapshot, {name: self.store.all(name) for name in snapshot})

    def webhook(self, statuses, phone_id="12345", valid_signature=True):
        node = next(node for node in ast.parse((self.root / "app/main.py").read_text()).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "inbound_whatsapp")
        node.decorator_list = []
        tree = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": phone_id}, "statuses": statuses}}]}]}
        body = json.dumps(payload).encode()
        digest = hmac.new(b"offline-webhook-secret", body, hashlib.sha256).hexdigest()
        class Request:
            headers = {"x-hub-signature-256": "sha256=" + (digest if valid_signature else "invalid")}
            async def body(inner):
                return body
        scope = dict(os=os, Request=Request, HTTPException=HttpError, hmac=hmac, hashlib=hashlib,
                     json=json, asyncio=asyncio, service=self.service,
                     handle_delivery_status=self.delivery.handle_delivery_status)
        exec(compile(tree, "<actual-webhook-handler>", "exec"), scope)
        return asyncio.run(scope["inbound_whatsapp"](Request()))

    def test_signed_failed_status_webhook_is_processed(self):
        self.accepted()
        result = self.webhook([{"id": "wamid.first", "status": "failed", "timestamp": "1730000000",
                                "errors": [{"code": 131000}]}])
        self.assertEqual(result["statuses_processed"], 1)
        self.assertEqual(self.saved()["status"], "FAILED")

    def test_wrong_number_webhook_is_ignored_and_invalid_signature_rejected(self):
        self.accepted()
        value = {"id": "wamid.first", "status": "delivered", "timestamp": "1730000000"}
        self.assertEqual(self.webhook([value], phone_id="other-number")["statuses_processed"], 0)
        self.assertEqual(self.saved()["status"], "ACCEPTED")
        with self.assertRaises(HttpError) as caught:
            self.webhook([value], valid_signature=False)
        self.assertEqual(caught.exception.status_code, 403)

    def test_malformed_status_does_not_abort_later_valid_callback(self):
        self.accepted()
        result = self.webhook([{"id": "wamid.first", "status": [], "timestamp": "1730000000"},
                               {"id": "wamid.first", "status": "delivered", "timestamp": "1730000001"}])
        self.assertEqual(result["statuses_processed"], 1)
        self.assertEqual(self.saved()["status"], "DELIVERED")


if __name__ == "__main__":
    unittest.main()
