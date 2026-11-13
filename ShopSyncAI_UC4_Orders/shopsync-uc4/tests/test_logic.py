"""Offline business-flow tests, with no PyMongo or MongoDB dependency.

The fake store serializes writes with an RLock and rolls back deep copies. These
tests validate domain decisions and rollback boundaries; they DO NOT validate
MongoDB transactions, indexes, driver retry behavior, or real concurrency.
An isolated import of service.py leaves the application's normal imports alone.
"""

import copy
import importlib.util
import os
import sys
import threading
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


def matches(document, query):
    for key, expected in query.items():
        actual = document.get("id" if key == "_id" else key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$gte" and not (actual is not None and actual >= value):
                    return False
                if operator == "$lte" and not (actual is not None and actual <= value):
                    return False
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$ne" and actual == value:
                    return False
                if operator not in ("$gte", "$lte", "$in", "$ne"):
                    raise AssertionError(f"Unsupported fake query operator: {operator}")
        elif actual != expected:
            return False
    return True


class MemoryStore:
    UNIQUE_FIELDS = {
        "products": ("sku",), "customers": ("phone",), "orders": ("source_key",),
        "approvals": ("order_id", "action"), "return_requests": ("order_id",),
        "stock_movements": ("order_id", "product_id", "kind"),
        "financial_entries": ("order_id", "kind"),
    }

    def __init__(self):
        self.documents = {}

    def get(self, collection, query):
        return next((copy.deepcopy(row) for row in self.documents.get(collection, [])
                     if matches(row, query)), None)

    def all(self, collection, query=None, sort=None, limit=0):
        rows = [copy.deepcopy(row) for row in self.documents.get(collection, [])
                if matches(row, query or {})]
        for field, direction in reversed(sort or []):
            key = "id" if field == "_id" else field
            rows.sort(key=lambda row: row.get(key), reverse=direction < 0)
        return rows[:limit] if limit else rows

    def count(self, collection, query=None):
        return len(self.all(collection, query))

    def insert(self, collection, document):
        if self.get(collection, {"_id": document["id"]}):
            raise AssertionError(f"Duplicate ID in fake {collection}")
        fields = self.UNIQUE_FIELDS.get(collection)
        if fields and self.get(collection, {field: document[field] for field in fields}):
            raise AssertionError(f"Duplicate operation in fake {collection}")
        self.documents.setdefault(collection, []).append(copy.deepcopy(document))

    def update(self, collection, query, changes):
        for row in self.documents.get(collection, []):
            if not matches(row, query):
                continue
            previous = copy.deepcopy(row)
            for key, value in changes.get("$set", {}).items():
                row[key] = copy.deepcopy(value)
            for key, value in changes.get("$inc", {}).items():
                row[key] = row.get(key, 0) + value
            if set(changes) - {"$set", "$inc"}:
                raise AssertionError("Unsupported fake update operator")
            return types.SimpleNamespace(matched_count=1, modified_count=int(row != previous))
        return types.SimpleNamespace(matched_count=0, modified_count=0)


class MemoryDatabase:
    def __init__(self):
        self.store = MemoryStore()
        self.lock = threading.RLock()

    def run_transaction(self, callback):
        with self.lock:
            previous = copy.deepcopy(self.store.documents)
            try:
                return callback(self.store)
            except Exception:
                self.store.documents = previous
                raise


class OrderLogicOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_name = "_uc4_offline_" + uuid.uuid4().hex
        package = types.ModuleType(cls.package_name)
        package.__path__ = [str(Path(__file__).resolve().parents[1] / "app")]
        cls.db_module = types.ModuleType(cls.package_name + ".db")
        cls.db_module.target = None
        cls.db_module.get_store = lambda: cls.db_module.target.store
        cls.db_module.run_transaction = lambda callback: cls.db_module.target.run_transaction(callback)
        sys.modules[cls.package_name] = package
        sys.modules[cls.package_name + ".db"] = cls.db_module
        filename = Path(__file__).resolve().parents[1] / "app" / "service.py"
        spec = importlib.util.spec_from_file_location(cls.package_name + ".service", filename)
        cls.service = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.service
        spec.loader.exec_module(cls.service)

    @classmethod
    def tearDownClass(cls):
        for name in (cls.package_name + ".service", cls.package_name + ".db", cls.package_name):
            sys.modules.pop(name, None)

    def setUp(self):
        self.database = MemoryDatabase()
        self.store = self.database.store
        self.db_module.target = self.database
        self.environment = patch.dict(os.environ, {
            "SHOPSYNC_RISK_AMOUNT_CENTS": "2000000", "SHOPSYNC_RISK_THRESHOLD": "50",
            "SHOPSYNC_RETURN_DAYS": "14",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.phone = "923001234567"
        self.product = self.service.add_product("TEE-01", "Demo shirt", 150000, 2)

    def draft(self, quantity=1, phone=None, source_key=None, items=None):
        return self.service.create_draft(
            phone or self.phone, "Demo customer",
            items or [{"product_id": self.product["id"], "quantity": quantity}],
            "12 Main Street, Lahore", "COD", source_key or uuid.uuid4().hex)

    def stock(self, product=None):
        return self.store.get("products", {"_id": (product or self.product)["id"]})["stock_qty"]

    def net(self):
        return self.service.financial_summary()["booked_net_cents"]

    def assert_order_error(self, function, status, *args, **kwargs):
        with self.assertRaises(self.service.OrderError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def requested(self, kind="REFUND", shipped=False):
        order = self.draft()
        self.service.confirm(order["id"])
        if shipped:
            self.service.mark_shipped(order["id"])
        result = self.service.request_return(order["id"], kind, "Wrong size")
        return result, result["return_request"]["id"]

    def test_payment_choice_complete_summary_and_chat_edit(self):
        reply = self.service.chat("missing", self.phone, "ORDER TEE-01 1 | Lahore", "Ayesha")
        self.assertIn("Choose payment method", reply)
        invalid = self.service.chat("invalid", self.phone, "ORDER TEE-01 1 | Lahore | CHEQUE")
        self.assertIn("COD or BANK_TRANSFER", invalid)
        self.assertEqual(self.store.count("orders"), 0)
        reply = self.service.chat("valid", self.phone,
                                  "ORDER TEE-01 1 | House 12, Lahore | BANK_TRANSFER", "Ayesha")
        for value in ("Customer: Ayesha", "923001234567", "Demo shirt", "1 x", "PKR 1,500.00",
                      "Delivery address: House 12, Lahore", "Payment method: BANK_TRANSFER", "CONFIRM"):
            self.assertIn(value, reply)
        order = self.service.list_orders()[0]
        edited = self.service.chat("edit", self.phone,
                                   f"EDIT {order['id']} TEE-01 2 | House 15, Lahore | COD")
        self.assertIn("Payment method: COD", edited)
        self.assertIn("House 15, Lahore", edited)
        self.assertIn("Total: PKR 3,000.00", edited)
        self.assertEqual(self.stock(), 2)

    def test_owner_edit_and_repeated_confirmation_book_once(self):
        order = self.draft()
        edited = self.service.edit_draft(order["id"],
            [{"product_id": self.product["id"], "quantity": 2}], "Changed address", "BANK_TRANSFER")
        self.assertEqual(edited["payment_method"], "BANK_TRANSFER")
        self.assertEqual(edited["total_cents"], 300000)
        self.assertEqual(self.stock(), 2)
        self.service.confirm(order["id"])
        self.service.confirm(order["id"])
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.net(), 300000)
        self.assertEqual(self.store.count("financial_entries", {"kind": "SALE"}), 1)
        self.assert_order_error(self.service.edit_draft, 409, order["id"],
                                [{"product_id": self.product["id"], "quantity": 1}], "Address")

    def test_source_key_reuses_draft_without_cross_customer_disclosure(self):
        order = self.draft(source_key="same-source")
        self.assertEqual(self.draft(source_key="same-source")["id"], order["id"])
        self.assert_order_error(self.draft, 409, phone="923009990000", source_key="same-source")
        self.assertEqual(self.store.count("orders"), 1)

    def test_risk_hold_approval_and_rejection(self):
        os.environ["SHOPSYNC_RISK_AMOUNT_CENTS"] = "100000"
        first, second = self.draft(), self.draft()
        for order in (first, second):
            result = self.service.confirm(order["id"])
            self.assertEqual(result["status"], "RISK_HOLD")
            self.assertEqual(result["risk_score"], 60)
            self.assertTrue(result["risk_reasons"])
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.net(), 0)
        self.assertEqual(self.service.decide_risk(first["id"], True, "Reviewed")["status"], "CONFIRMED")
        self.assertEqual(self.service.decide_risk(second["id"], False, "Review failed")["status"], "REJECTED")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.net(), 150000)

    def test_risk_approval_rechecks_stock_and_rolls_back_on_failure(self):
        os.environ["SHOPSYNC_RISK_AMOUNT_CENTS"] = "100000"
        held = self.draft()
        self.service.confirm(held["id"])
        os.environ["SHOPSYNC_RISK_AMOUNT_CENTS"] = "2000000"
        competing = self.draft(quantity=2)
        self.service.confirm(competing["id"])
        self.assert_order_error(self.service.decide_risk, 409, held["id"], True, "Reviewed")
        result = self.service.get_order(held["id"])
        self.assertEqual(result["status"], "RISK_HOLD")
        self.assertEqual(result["approval"]["status"], "PENDING")
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.net(), 300000)

    def test_cancellation_restores_stock_and_reverses_sale_once(self):
        order = self.draft()
        self.service.confirm(order["id"])
        self.service.cancel(order["id"])
        self.service.cancel(order["id"])
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.net(), 0)
        self.assertEqual(self.store.count("stock_movements", {"kind": "CANCEL"}), 1)
        self.assert_order_error(self.service.confirm, 409, order["id"])

    def test_return_waits_for_receipt_then_restocks_and_refunds_once(self):
        order, request_id = self.requested("RETURN", shipped=True)
        self.assert_order_error(self.service.cancel, 409, order["id"])
        accepted = self.service.decide_return(request_id, True, "Within policy")
        self.assertEqual(accepted["return_request"]["status"], "APPROVED_AWAITING_RECEIPT")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.net(), 150000)
        self.service.receive_return(request_id, True)
        result = self.service.receive_return(request_id, True)
        self.assertEqual(result["return_request"]["status"], "COMPLETED")
        self.assertEqual(result["payment_status"], "REFUND_REQUIRES_MANUAL_ACTION")
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.net(), 0)
        self.assertEqual(self.store.count("financial_entries", {"kind": "REFUND"}), 1)

    def test_approved_refund_never_restocks_or_allows_second_reversal(self):
        order, request_id = self.requested()
        self.service.decide_return(request_id, True, "Approved")
        self.service.decide_return(request_id, True, "Repeated")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.net(), 0)
        self.assert_order_error(self.service.cancel, 409, order["id"])
        self.assert_order_error(self.service.mark_shipped, 409, order["id"])
        self.assertEqual(self.net(), 0)
        self.assertEqual(self.store.count("financial_entries", {"kind": "REFUND"}), 1)

    def test_rejected_refund_requires_reason_and_customer_can_read_it(self):
        order, request_id = self.requested()
        for reason in ("", "   "):
            self.assert_order_error(self.service.decide_return, 400, request_id, False, reason)
        self.assertEqual(self.service.get_order(order["id"])["return_request"]["status"], "PENDING")
        self.service.decide_return(request_id, False, "Outside policy")
        reply = self.service.chat("status", self.phone, f"STATUS {order['id']}")
        self.assertIn("Reason: Outside policy", reply)
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.net(), 150000)

    def test_pending_request_blocks_cancellation(self):
        order, request_id = self.requested()
        self.assert_order_error(self.service.cancel, 409, order["id"])
        result = self.service.get_order(order["id"])
        self.assertEqual(result["status"], "CONFIRMED")
        self.assertEqual(result["return_request"]["id"], request_id)
        self.assertEqual(result["return_request"]["status"], "PENDING")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.net(), 150000)

    def test_cancelled_order_cannot_request_refund(self):
        order = self.draft()
        self.service.confirm(order["id"])
        self.service.cancel(order["id"])
        self.assert_order_error(self.service.request_return, 409, order["id"], "REFUND", "Wrong size")
        self.assertEqual(self.store.count("return_requests"), 0)
        self.assertEqual(self.net(), 0)

    def test_rejected_inspection_requires_reason_and_never_changes_stock_or_sale(self):
        order, request_id = self.requested("RETURN", shipped=True)
        self.service.decide_return(request_id, True, "Await item")
        self.assert_order_error(self.service.receive_return, 400, request_id, False, "   ")
        result = self.service.receive_return(request_id, False, "Item failed inspection")
        self.assertEqual(result["return_request"]["status"], "REJECTED_AFTER_INSPECTION")
        self.assertEqual(self.stock(), 1)
        self.assertEqual(self.net(), 150000)
        self.assertIn("Item failed inspection", self.service.chat("inspection-status", self.phone,
                                                                f"STATUS {order['id']}"))

    def test_customer_chat_cannot_view_edit_or_confirm_another_customers_order(self):
        order = self.draft()
        other = "923009990000"
        for index, message in enumerate((f"STATUS {order['id']}", f"CONFIRM {order['id']}",
                                         f"EDIT {order['id']} TEE-01 2 | Other address | COD")):
            self.assertEqual(self.service.chat(f"other-{index}", other, message), "Order not found.")
        current = self.service.get_order(order["id"])
        self.assertEqual(current["status"], "DRAFT")
        self.assertEqual(current["delivery_address"], "12 Main Street, Lahore")
        self.assertEqual(self.stock(), 2)

    def test_message_id_is_idempotent_and_bound_to_sender(self):
        text = "ORDER TEE-01 1 | Lahore address | COD"
        first = self.service.chat("same-message", self.phone, text, live=True)
        self.assertEqual(self.service.chat("same-message", self.phone, text, live=True), first)
        self.assert_order_error(self.service.chat, 409, "same-message", "923009990000", text, live=True)
        self.assertEqual(self.store.count("orders"), 1)
        self.assertEqual(self.store.count("inbound_messages"), 1)
        self.assertEqual(self.store.count("outbox"), 1)

    def competing_multiitem_order(self):
        cup = self.service.add_product("CUP-01", "Demo cup", 80000, 1)
        mixed = self.draft(items=[{"product_id": self.product["id"], "quantity": 1},
                                  {"product_id": cup["id"], "quantity": 1}])
        competing = self.draft(phone="923009990000", items=[{"product_id": cup["id"], "quantity": 1}])
        self.service.confirm(competing["id"])
        return mixed, cup

    def test_failed_multiitem_confirmation_rolls_back_every_stock_and_sale_write(self):
        mixed, cup = self.competing_multiitem_order()
        self.assert_order_error(self.service.confirm, 409, mixed["id"])
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.stock(cup), 0)
        result = self.service.get_order(mixed["id"])
        self.assertEqual(result["status"], "DRAFT")
        self.assertIsNone(result["risk_score"])
        self.assertEqual(self.net(), 80000)
        self.assertEqual(self.store.count("stock_movements"), 1)

    def test_failed_chat_aborts_business_writes_but_persists_one_error_and_notification(self):
        mixed, cup = self.competing_multiitem_order()
        command = f"CONFIRM {mixed['id']}"
        reply = self.service.chat("failed-confirm", self.phone, command, live=True)
        self.assertIn("Insufficient stock", reply)
        self.assertEqual(self.stock(), 2)
        self.assertEqual(self.stock(cup), 0)
        self.assertEqual(self.service.get_order(mixed["id"])["status"], "DRAFT")
        self.assertIsNone(self.service.get_order(mixed["id"])["risk_score"])
        self.assertEqual(self.net(), 80000)
        self.assertEqual(self.store.get("inbound_messages", {"_id": "failed-confirm"})["response"], reply)
        self.assertEqual(self.store.count("outbox"), 1)
        self.assertEqual(self.service.chat("failed-confirm", self.phone, command, live=True), reply)
        self.assertEqual(self.store.count("outbox"), 1)


if __name__ == "__main__":
    unittest.main()
