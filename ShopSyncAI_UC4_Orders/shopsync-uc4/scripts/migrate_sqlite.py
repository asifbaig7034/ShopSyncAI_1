"""Read-only SQLite migration into an empty UC-4 MongoDB database.

Preview: python -m scripts.migrate_sqlite data/shopsync.sqlite3
Import:  python -m scripts.migrate_sqlite data/shopsync.sqlite3 --apply

The source is opened in read-only mode in one SQLite read transaction. Its
fingerprint covers the logical snapshot, including committed WAL content,
rather than just the main .sqlite3 file. This module intentionally imports no
MongoDB dependency until --apply is requested.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


COLLECTIONS = (
    "products", "customers", "orders", "approvals", "return_requests",
    "stock_movements", "financial_entries", "audit_events",
    "inbound_messages", "outbox",
)
METADATA_COLLECTION = "migration_metadata"
MARKER_ID = "sqlite-import-v1"
SOURCE_COLUMNS = {
    "products": ("id", "sku", "name", "price_cents", "stock_qty", "active"),
    "customers": ("id", "phone", "name", "last_inbound_at"),
    "orders": (
        "id", "customer_id", "channel", "status", "delivery_address",
        "payment_method", "payment_status", "total_cents", "risk_score",
        "risk_reasons", "source_key", "created_at", "updated_at", "shipped_at",
        "cancelled_reason",
    ),
    "order_items": ("order_id", "product_id", "sku", "name", "unit_price_cents", "quantity"),
    "approvals": ("id", "order_id", "action", "status", "owner_reason", "created_at", "decided_at"),
    "return_requests": (
        "id", "order_id", "kind", "reason", "status", "owner_reason",
        "created_at", "decided_at", "received_at",
    ),
    "stock_movements": ("id", "order_id", "product_id", "kind", "change_qty", "created_at"),
    "financial_entries": ("id", "order_id", "kind", "amount_cents", "created_at"),
    "audit_events": ("id", "order_id", "actor", "event", "details", "created_at"),
    "inbound_messages": ("message_id", "phone", "body", "response", "created_at"),
    "outbox": (
        "id", "phone", "body", "status", "attempts", "next_attempt_at",
        "provider_id", "last_error", "created_at",
    ),
}


class MigrationError(RuntimeError):
    """The source or target cannot safely be migrated."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def documents_digest(documents):
    """Fingerprint documents independently of MongoDB's natural ordering."""
    return _digest({name: sorted(documents[name], key=lambda row: row["id"])
                    for name in COLLECTIONS})


@dataclass(frozen=True)
class Snapshot:
    documents: dict
    source_sha256: str
    target_sha256: str
    order_item_count: int

    @property
    def counts(self):
        return {name: len(self.documents[name]) for name in COLLECTIONS}


def _json_value(value, expected, field, record_id, default=None):
    if value is None and default is not None:
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"Invalid JSON in {field} for record {record_id}.") from exc
    if not isinstance(parsed, expected):
        raise MigrationError(f"Unexpected JSON type in {field} for record {record_id}.")
    return parsed


def _validate_source(rows):
    ids = {}
    for name, records in rows.items():
        if name == "order_items":
            continue
        key = "message_id" if name == "inbound_messages" else "id"
        values = [r[key] for r in records]
        if any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
            raise MigrationError(f"Missing or duplicate IDs in {name}.")
        ids[name] = set(values)
    for order in rows["orders"]:
        if order["customer_id"] not in ids["customers"]:
            raise MigrationError(f"Order {order['id']} references a missing customer.")
    for name in ("order_items", "approvals", "return_requests", "stock_movements", "financial_entries"):
        for row in rows[name]:
            if row["order_id"] not in ids["orders"]:
                raise MigrationError(f"A {name} record references a missing order.")
            if "product_id" in row and row["product_id"] not in ids["products"]:
                raise MigrationError(f"A {name} record references a missing product.")
    unique_keys = {
        "products": ("sku",), "customers": ("phone",),
        "orders": ("source_key",), "order_items": ("order_id", "product_id"),
        "approvals": ("order_id", "action"), "return_requests": ("order_id",),
        "stock_movements": ("order_id", "product_id", "kind"),
        "financial_entries": ("order_id", "kind"),
    }
    for name, fields in unique_keys.items():
        keys = [tuple(row[field] for field in fields) for row in rows[name]]
        if len(set(keys)) != len(keys):
            raise MigrationError(f"Duplicate {', '.join(fields)} in {name}.")


def read_snapshot(source):
    """Read and validate a consistent source snapshot without modifying SQLite."""
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise MigrationError("SQLite source file does not exist; no file was created.")
    rows = {}
    try:
        connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            for name, columns in SOURCE_COLUMNS.items():
                found = {row["name"] for row in connection.execute(f'PRAGMA table_info("{name}")')}
                if not set(columns).issubset(found):
                    raise MigrationError(f"Source table {name} is missing or has an incompatible schema.")
                fields = ",".join(f'"{column}"' for column in columns)
                records = [dict(row) for row in connection.execute(f'SELECT {fields} FROM "{name}"')]
                rows[name] = sorted(records, key=_canonical)
            connection.rollback()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise MigrationError("Could not read the SQLite source. Stop the old app and check the file.") from exc
    _validate_source(rows)
    source_sha256 = _digest(rows)
    documents = {name: [dict(row) for row in rows[name]] for name in COLLECTIONS}
    for product in documents["products"]:
        product["active"] = product["active"] == 1
    items_by_order = {}
    for item in rows["order_items"]:
        items_by_order.setdefault(item["order_id"], []).append(
            {key: value for key, value in item.items() if key != "order_id"})
    for order in documents["orders"]:
        order["items"] = sorted(items_by_order.get(order["id"], []), key=lambda item: item["product_id"])
        order["risk_reasons"] = _json_value(order["risk_reasons"], list, "risk_reasons", order["id"], [])
    for event in documents["audit_events"]:
        event["details"] = _json_value(event["details"], dict, "details", event["id"])
    for message in documents["inbound_messages"]:
        message["id"] = message["message_id"]
    for message in documents["outbox"]:
        if message["status"] == "SENDING":
            message["status"] = "UNCERTAIN"
            message["last_error"] = "Imported interrupted send; review delivery before retry"
    return Snapshot(documents, source_sha256, documents_digest(documents), len(rows["order_items"]))


def apply_snapshot(snapshot, db_module=None):
    """Insert all records and the COMPLETE marker in one MongoDB transaction."""
    if db_module is None:
        from app import db as db_module
    db_module.initialize()

    def import_transaction(store):
        marker = store.get(METADATA_COLLECTION, {"_id": MARKER_ID})
        actual_counts = {name: store.count(name, {}) for name in COLLECTIONS}
        if marker:
            if (marker.get("status") != "COMPLETE" or
                    marker.get("source_sha256") != snapshot.source_sha256 or
                    marker.get("target_sha256") != snapshot.target_sha256 or
                    marker.get("counts") != snapshot.counts or
                    store.count(METADATA_COLLECTION, {}) != 1):
                raise MigrationError("Target contains a different migration; nothing was overwritten.")
            if actual_counts != snapshot.counts:
                raise MigrationError("Imported target has changed since migration; refusing to reimport.")
            actual = {name: store.all(name, {}) for name in COLLECTIONS}
            if documents_digest(actual) != snapshot.target_sha256:
                raise MigrationError("Imported target records have changed; refusing to reimport.")
            return {"status": "already_imported", "source_sha256": snapshot.source_sha256,
                    "counts": actual_counts}
        if any(actual_counts.values()) or store.count(METADATA_COLLECTION, {}):
            raise MigrationError("Target UC-4 collections must be empty. Use a separate empty database; nothing was overwritten.")
        for name in COLLECTIONS:
            for document in snapshot.documents[name]:
                store.insert(name, dict(document))
        store.insert(METADATA_COLLECTION, {
            "id": MARKER_ID, "status": "COMPLETE",
            "source_sha256": snapshot.source_sha256,
            "fingerprint_scheme": "canonical-sqlite-snapshot-v1",
            "target_sha256": snapshot.target_sha256, "counts": snapshot.counts,
            "order_item_count": snapshot.order_item_count,
            "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        return {"status": "imported", "source_sha256": snapshot.source_sha256,
                "counts": snapshot.counts}

    return db_module.run_transaction(import_transaction)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Existing UC-4 SQLite file (read only)")
    parser.add_argument("--apply", action="store_true", help="Import into an empty configured MongoDB database")
    args = parser.parse_args(argv)
    try:
        snapshot = read_snapshot(args.source)
        if args.apply:
            result = apply_snapshot(snapshot)
        else:
            result = {"status": "dry_run", "source_sha256": snapshot.source_sha256,
                      "counts": snapshot.counts, "embedded_order_items": snapshot.order_item_count,
                      "message": "No database writes. Stop the app, select an empty MongoDB database, then add --apply."}
        print(json.dumps(result, indent=2))
        return 0
    except (MigrationError, ImportError) as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions can contain the connection URI or provider details.
        print("Migration stopped: MongoDB import failed. Check the configured database and replica-set connection. No partial import should be committed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
