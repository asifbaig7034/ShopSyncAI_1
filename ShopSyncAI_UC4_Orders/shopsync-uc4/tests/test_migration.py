"""Offline import validation plus opt-in real MongoDB migration checks.

TEST_MONGODB_URI must point to a transaction-capable test deployment. Integration
tests create and remove only a random shopsync_uc4_test_migration_* database.
"""

import copy
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.migrate_sqlite import (
    COLLECTIONS, METADATA_COLLECTION, SOURCE_COLUMNS, MigrationError,
    apply_snapshot, main, read_snapshot,
)


def make_source(path):
    connection = sqlite3.connect(path)
    integer_columns = {"price_cents", "stock_qty", "active", "total_cents", "risk_score",
                       "unit_price_cents", "quantity", "change_qty", "amount_cents", "attempts"}
    for table, columns in SOURCE_COLUMNS.items():
        fields = ",".join('"' + name + '" ' + ("INTEGER" if name in integer_columns else "TEXT")
                          for name in columns)
        connection.execute(f'CREATE TABLE "{table}" ({fields})')
    timestamp = "2026-01-01T12:00:00+00:00"
    records = {
        "products": {"id": "P1", "sku": "TEE-01", "name": "Demo shirt",
                     "price_cents": 150000, "stock_qty": 2, "active": 1},
        "customers": {"id": "C1", "phone": "923001234567", "name": "Demo customer"},
        "orders": {"id": "O1", "customer_id": "C1", "channel": "owner", "status": "DRAFT",
                   "delivery_address": "Demo address", "payment_method": "COD", "payment_status": "UNPAID",
                   "total_cents": 150000, "source_key": "source-key-1", "created_at": timestamp,
                   "updated_at": timestamp},
        "order_items": {"order_id": "O1", "product_id": "P1", "sku": "TEE-01", "name": "Demo shirt",
                        "unit_price_cents": 150000, "quantity": 1},
        "audit_events": {"id": "E1", "order_id": "O1", "actor": "owner", "event": "draft_created",
                         "details": '{"total_cents":150000}', "created_at": timestamp},
        "inbound_messages": {"message_id": "M1", "phone": "923001234567", "body": "STATUS O1",
                             "response": "Order O1: DRAFT.", "created_at": timestamp},
        "outbox": {"id": "X1", "phone": "923001234567", "body": "Demo reply", "status": "SENDING",
                   "attempts": 1, "next_attempt_at": timestamp, "created_at": timestamp},
    }
    for table, record in records.items():
        columns = SOURCE_COLUMNS[table]
        placeholders = ",".join("?" for _ in columns)
        connection.execute(f'INSERT INTO "{table}" VALUES ({placeholders})',
                           [record.get(column) for column in columns])
    connection.commit()
    return connection


class MemoryStore:
    """Importer protocol fake; it does not emulate MongoDB transaction safety."""

    def __init__(self):
        self.documents = {}

    def get(self, name, query):
        return next((copy.deepcopy(row) for row in self.documents.get(name, [])
                     if all(row.get("id" if key == "_id" else key) == value for key, value in query.items())), None)

    def count(self, name, query):
        return len(self.all(name, query))

    def all(self, name, query):
        return [copy.deepcopy(row) for row in self.documents.get(name, [])
                if all(row.get("id" if key == "_id" else key) == value for key, value in query.items())]

    def insert(self, name, document):
        if self.get(name, {"id": document["id"]}):
            raise ValueError("Duplicate test document ID")
        self.documents.setdefault(name, []).append(copy.deepcopy(document))


class MemoryDatabase:
    def __init__(self):
        self.store = MemoryStore()

    def initialize(self):
        pass

    def run_transaction(self, callback):
        original = copy.deepcopy(self.store.documents)
        try:
            return callback(self.store)
        except Exception:
            self.store.documents = original
            raise


class MigrationOfflineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.source = Path(self.folder.name) / "source.sqlite3"
        self.connection = make_source(self.source)

    def tearDown(self):
        self.connection.close()
        self.folder.cleanup()

    def test_snapshot_preserves_ids_prices_types_and_original_database(self):
        original = hashlib.sha256(self.source.read_bytes()).hexdigest()
        snapshot = read_snapshot(self.source)
        self.assertEqual(snapshot.documents["orders"][0]["id"], "O1")
        self.assertEqual(snapshot.documents["orders"][0]["customer_id"], "C1")
        self.assertEqual(snapshot.documents["orders"][0]["items"][0]["product_id"], "P1")
        self.assertEqual(snapshot.documents["orders"][0]["items"][0]["unit_price_cents"], 150000)
        self.assertNotIn("order_id", snapshot.documents["orders"][0]["items"][0])
        self.assertEqual(snapshot.documents["orders"][0]["risk_reasons"], [])
        self.assertEqual(snapshot.documents["audit_events"][0]["details"], {"total_cents": 150000})
        self.assertIs(snapshot.documents["products"][0]["active"], True)
        self.assertEqual(snapshot.documents["inbound_messages"][0]["id"], "M1")
        self.assertEqual(snapshot.documents["outbox"][0]["status"], "UNCERTAIN")
        self.assertEqual(snapshot.order_item_count, 1)
        self.assertEqual(original, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_snapshot_includes_committed_wal_content_in_fingerprint(self):
        self.connection.execute("PRAGMA journal_mode=WAL")
        before = read_snapshot(self.source)
        main_file_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.connection.execute("INSERT INTO products VALUES (?,?,?,?,?,?)",
                                ("P2", "CUP-01", "Demo cup", 80000, 1, 1))
        self.connection.commit()
        self.assertEqual(main_file_hash, hashlib.sha256(self.source.read_bytes()).hexdigest())
        after = read_snapshot(self.source)
        self.assertEqual(after.counts["products"], 2)
        self.assertNotEqual(before.source_sha256, after.source_sha256)

    def test_missing_source_is_not_created(self):
        missing = Path(self.folder.name) / "missing.sqlite3"
        with self.assertRaises(MigrationError):
            read_snapshot(missing)
        self.assertFalse(missing.exists())

    def test_malformed_json_and_orphan_records_are_rejected(self):
        self.connection.execute("UPDATE orders SET risk_reasons='not-json'")
        self.connection.commit()
        with self.assertRaisesRegex(MigrationError, "Invalid JSON"):
            read_snapshot(self.source)
        self.connection.execute("UPDATE orders SET risk_reasons='[]'")
        self.connection.execute("UPDATE order_items SET product_id='MISSING'")
        self.connection.commit()
        with self.assertRaisesRegex(MigrationError, "missing product"):
            read_snapshot(self.source)

    def test_cli_defaults_to_dry_run_without_mongodb_connection(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([str(self.source)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["counts"]["orders"], 1)

    def test_import_requires_empty_target_and_verifies_identical_rerun(self):
        snapshot = read_snapshot(self.source)
        target = MemoryDatabase()
        self.assertEqual(apply_snapshot(snapshot, target)["status"], "imported")
        self.assertEqual(apply_snapshot(snapshot, target)["status"], "already_imported")
        self.assertEqual(target.store.count(METADATA_COLLECTION, {}), 1)
        target.store.documents["products"][0]["stock_qty"] = 1
        with self.assertRaisesRegex(MigrationError, "changed"):
            apply_snapshot(snapshot, target)
        self.assertEqual(target.store.documents["products"][0]["stock_qty"], 1)
        occupied = MemoryDatabase()
        occupied.store.insert("products", {"id": "EXISTING", "sku": "KEEP"})
        with self.assertRaisesRegex(MigrationError, "must be empty"):
            apply_snapshot(snapshot, occupied)
        self.assertEqual(occupied.store.count("orders", {}), 0)
        self.assertEqual(occupied.store.count(METADATA_COLLECTION, {}), 0)


@unittest.skipUnless(os.getenv("TEST_MONGODB_URI"), "Set TEST_MONGODB_URI for real replica-set migration tests")
class MigrationMongoDBTests(unittest.TestCase):
    def setUp(self):
        from app import db
        self.db = db
        self.folder = tempfile.TemporaryDirectory()
        self.source = Path(self.folder.name) / "source.sqlite3"
        connection = make_source(self.source)
        connection.close()
        self.database_name = "shopsync_uc4_test_migration_" + uuid.uuid4().hex
        self.db.close_connection()
        self.environment = patch.dict(os.environ, {"MONGODB_URI": os.environ["TEST_MONGODB_URI"],
                                                   "MONGODB_DB": self.database_name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.db.close_connection)
        self.db.initialize()
        self.addCleanup(self._drop_test_database)

    def _drop_test_database(self):
        if not self.database_name.startswith("shopsync_uc4_test_migration_"):
            raise RuntimeError("Refusing to remove a non-test database")
        self.db.get_database().client.drop_database(self.database_name)

    def test_import_and_verified_noop_against_real_mongodb(self):
        snapshot = read_snapshot(self.source)
        self.assertEqual(apply_snapshot(snapshot, self.db)["status"], "imported")
        target = self.db.get_database()
        order = target.orders.find_one({"_id": "O1"})
        self.assertEqual(order["customer_id"], "C1")
        self.assertEqual(order["items"][0]["product_id"], "P1")
        self.assertEqual(order["items"][0]["unit_price_cents"], 150000)
        self.assertEqual(target.outbox.find_one({"_id": "X1"})["status"], "UNCERTAIN")
        for name, count in snapshot.counts.items():
            self.assertEqual(target[name].count_documents({}), count)
        self.assertEqual(apply_snapshot(snapshot, self.db)["status"], "already_imported")
        target.products.update_one({"_id": "P1"}, {"$set": {"stock_qty": 1}})
        with self.assertRaisesRegex(MigrationError, "changed"):
            apply_snapshot(snapshot, self.db)
        self.assertEqual(target.products.find_one({"_id": "P1"})["stock_qty"], 1)

    def test_existing_target_is_preserved(self):
        target = self.db.get_database()
        target.products.insert_one({"_id": "EXISTING", "sku": "KEEP", "name": "Existing product",
                                    "active": True, "stock_qty": 7, "price_cents": 100})
        with self.assertRaisesRegex(MigrationError, "must be empty"):
            apply_snapshot(read_snapshot(self.source), self.db)
        self.assertEqual(target.products.count_documents({}), 1)
        self.assertEqual(target.orders.count_documents({}), 0)
        self.assertEqual(target[METADATA_COLLECTION].count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
