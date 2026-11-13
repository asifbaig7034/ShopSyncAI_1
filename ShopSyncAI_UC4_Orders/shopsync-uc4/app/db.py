"""MongoDB persistence. All business writes run in retryable transactions."""
import os
from copy import deepcopy
from pathlib import Path
from threading import RLock

from dotenv import load_dotenv

from pymongo import ASCENDING, MongoClient, ReadPreference
from pymongo.errors import CollectionInvalid, DuplicateKeyError, OperationFailure, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

COLLECTIONS = (
    "products", "customers", "orders", "approvals", "return_requests",
    "stock_movements", "financial_entries", "audit_events", "inbound_messages", "outbox",
)
_client = None
_settings = None
_lock = RLock()


def get_database():
    global _client, _settings
    uri = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true")
    name = os.getenv("MONGODB_DB", "shopsync_uc4")
    if not name or name in {"admin", "config", "local"}:
        raise RuntimeError("Set MONGODB_DB to an application database name.")
    with _lock:
        if _client is None or _settings != (uri, name):
            if _client is not None:
                _client.close()
            _client = MongoClient(uri, appname="ShopSyncAI-UC4", serverSelectionTimeoutMS=5000,
                                  connectTimeoutMS=5000, socketTimeoutMS=20000)
            _settings = (uri, name)
        # Lease claims must be acknowledged durably before sending WhatsApp HTTP.
        return _client.get_database(name, write_concern=WriteConcern("majority"))


def close_connection():
    global _client, _settings
    with _lock:
        if _client is not None:
            _client.close()
        _client = _settings = None


def public(document):
    if document is None:
        return None
    result = deepcopy(document)
    result["id"] = str(result.pop("_id"))
    return result


class MongoStore:
    """Pass one session to every operation in a business transaction."""
    def __init__(self, database, session=None):
        self.database, self.session = database, session

    def get(self, collection, query):
        return public(self.database[collection].find_one(query, session=self.session))

    def all(self, collection, query=None, sort=None, limit=0):
        cursor = self.database[collection].find(query or {}, session=self.session)
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [public(row) for row in cursor]

    def count(self, collection, query=None):
        return self.database[collection].count_documents(query or {}, session=self.session)

    def insert(self, collection, document):
        value = deepcopy(document)
        value["_id"] = value.pop("id")
        return self.database[collection].insert_one(value, session=self.session)

    def update(self, collection, query, changes):
        return self.database[collection].update_one(query, changes, session=self.session)


def get_store():
    return MongoStore(get_database())


def run_transaction(callback):
    database = get_database()
    # Retry unique customer/source/message races only after the failed transaction
    # has aborted. The callback must never send external HTTP requests.
    for attempt in range(5):
        try:
            with database.client.start_session() as session:
                return session.with_transaction(
                    lambda current: callback(MongoStore(database, current)),
                    read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY, max_commit_time_ms=10000,
                )
        except DuplicateKeyError:
            if attempt == 4:
                raise


def initialize():
    database = get_database()
    try:
        hello = database.client.admin.command("hello")
    except PyMongoError:
        raise RuntimeError("Cannot connect to MongoDB. Start the database or check MONGODB_URI, credentials and network access. See README.md.") from None
    if not (hello.get("setName") or hello.get("msg") == "isdbgrid"):
        raise RuntimeError("MongoDB transactions require a replica set or Atlas cluster. A standalone mongod is not supported; see README.md for setup.")
    for name in (*COLLECTIONS, "migration_metadata"):
        try:
            database.create_collection(name)
        except CollectionInvalid:
            pass
        except OperationFailure as error:
            if error.code != 48:  # Another process may have created the collection.
                raise
    for name, fields, unique in (
        ("products", [("sku", ASCENDING)], True),
        ("customers", [("phone", ASCENDING)], True),
        ("orders", [("source_key", ASCENDING)], True),
        ("orders", [("customer_id", ASCENDING), ("created_at", ASCENDING)], False),
        ("approvals", [("order_id", ASCENDING), ("action", ASCENDING)], True),
        ("return_requests", [("order_id", ASCENDING)], True),
        ("stock_movements", [("order_id", ASCENDING), ("product_id", ASCENDING), ("kind", ASCENDING)], True),
        ("financial_entries", [("order_id", ASCENDING), ("kind", ASCENDING)], True),
        ("outbox", [("status", ASCENDING), ("next_attempt_at", ASCENDING)], False),
    ):
        database[name].create_index(fields, unique=unique)
    run_transaction(lambda store: store.count("products"))
