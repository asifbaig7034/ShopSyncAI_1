import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


def database_path():
    return Path(os.getenv("SHOPSYNC_DB", "data/shopsync-orders.sqlite3"))


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("PRAGMA journal_mode=WAL")
    return db


@contextmanager
def transaction():
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with closing(connect()) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS products (
          id TEXT PRIMARY KEY, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
          stock_qty INTEGER NOT NULL CHECK(stock_qty >= 0), active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS customers (
          id TEXT PRIMARY KEY, phone TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          last_inbound_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
          id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
          channel TEXT NOT NULL, status TEXT NOT NULL, delivery_address TEXT NOT NULL,
          payment_method TEXT NOT NULL, payment_status TEXT NOT NULL DEFAULT 'UNPAID',
          total_cents INTEGER NOT NULL, risk_score INTEGER, risk_reasons TEXT,
          source_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          shipped_at TEXT, cancelled_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS order_items (
          order_id TEXT NOT NULL REFERENCES orders(id), product_id TEXT NOT NULL REFERENCES products(id),
          sku TEXT NOT NULL, name TEXT NOT NULL, unit_price_cents INTEGER NOT NULL,
          quantity INTEGER NOT NULL CHECK(quantity > 0), PRIMARY KEY(order_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS approvals (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
          action TEXT NOT NULL, status TEXT NOT NULL, owner_reason TEXT,
          created_at TEXT NOT NULL, decided_at TEXT,
          UNIQUE(order_id, action)
        );
        CREATE TABLE IF NOT EXISTS return_requests (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
          kind TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
          owner_reason TEXT, created_at TEXT NOT NULL, decided_at TEXT, received_at TEXT
        );
        CREATE TABLE IF NOT EXISTS stock_movements (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
          product_id TEXT NOT NULL REFERENCES products(id), kind TEXT NOT NULL,
          change_qty INTEGER NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(order_id, product_id, kind)
        );
        CREATE TABLE IF NOT EXISTS financial_entries (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id),
          kind TEXT NOT NULL, amount_cents INTEGER NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(order_id, kind)
        );
        CREATE TABLE IF NOT EXISTS audit_events (
          id TEXT PRIMARY KEY, order_id TEXT, actor TEXT NOT NULL, event TEXT NOT NULL,
          details TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inbound_messages (
          message_id TEXT PRIMARY KEY, phone TEXT NOT NULL, body TEXT NOT NULL,
          response TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox (
          id TEXT PRIMARY KEY, phone TEXT NOT NULL, body TEXT NOT NULL,
          status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TEXT NOT NULL, provider_id TEXT, last_error TEXT,
          created_at TEXT NOT NULL
        );
        -- Agent events are saved with the order transaction, then delivered
        -- separately to Mongo. This is distinct from the WhatsApp outbox.
        CREATE TABLE IF NOT EXISTS agent_event_outbox (
          event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
          aggregate_id TEXT NOT NULL, payload_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'PENDING',
          attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
          next_attempt_at TEXT NOT NULL,
          created_at TEXT NOT NULL, delivered_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_agent_events_pending ON agent_event_outbox(status, next_attempt_at, created_at);
        """)
        # A process killed during a send can leave a lease behind. Its status is
        # unknown; an operator can inspect and retry it, avoiding an automatic duplicate.
        db.execute("UPDATE outbox SET status='UNCERTAIN', last_error='Interrupted while sending; review before retry' WHERE status='SENDING'")
