"""Atomic per-conversation messages and delivery state, durable ingress jobs.

Mongo stores each conversation and its messages in one document. Appending a bot
reply checks the handover mode in the same atomic update. Do not keep unbounded
chat histories in production: archive older conversation documents well before
MongoDB's 16 MiB document limit. This starter limits each conversation to 500
messages and asks the operator to archive it before continuing.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def conversation_id(business_id, customer_id, channel):
    return hashlib.sha256(f"{business_id}:{channel}:{customer_id}".encode()).hexdigest()[:24]


def new_message(message_id, role, text, status):
    return {"id": message_id, "role": role, "text": text, "created_at": now(), "delivery_status": status, "attempts": 0, "next_attempt": now()}


def public_conversation(document, full=True):
    result = deepcopy(document)
    for key in list(result):
        if key.startswith("_") or key in {"lock_until", "lock_owner", "channel"}:
            result.pop(key)
    if full:
        for message in result.get("messages", []):
            for key in list(message):
                if key in {"attempts", "next_attempt", "lease_until"}:
                    message.pop(key)
            if message["delivery_status"] == "sending":
                message["delivery_status"] = "queued"
    else:
        result.pop("messages", None)
    return result


class MemoryStore:
    def __init__(self, business_id):
        self.business_id = business_id
        self.conversations = {}
        self.jobs = {}
        self.catalogue = {}
        self.lock = asyncio.Lock()

    async def initialize(self):
        pass

    async def close(self):
        pass

    async def seed(self, products):
        for product in products:
            self.catalogue[product["id"]] = deepcopy(product)

    async def products(self):
        return list(deepcopy(self.catalogue).values())

    async def ensure_conversation(self, customer_id, channel, customer_name="Customer"):
        cid = conversation_id(self.business_id, customer_id, channel)
        async with self.lock:
            if cid not in self.conversations:
                self.conversations[cid] = {"id": cid, "business_id": self.business_id, "customer_id": customer_id, "customer_name": customer_name, "channel": channel, "mode": "bot", "language": "en", "updated_at": now(), "last_message": "", "handover_reason": None, "messages": []}
        return await self.get(cid)

    async def get(self, cid):
        return deepcopy(self.conversations.get(cid))

    async def list(self):
        return sorted(deepcopy(list(self.conversations.values())), key=lambda c: c["updated_at"], reverse=True)

    async def add_job(self, cid, external_id, text, received_at=None):
        jid = f"{self.business_id}:{cid}:{external_id}"
        async with self.lock:
            existing = self.jobs.get(jid)
            if existing:
                if existing["text"] != text:
                    raise ValueError("external_id was already used for different message text")
                return deepcopy(existing), False
            job = {"id": jid, "business_id": self.business_id, "conversation_id": cid, "text": text, "state": "pending", "attempts": 0, "created_at": now(), "received_at": received_at or now(), "next_attempt": now()}
            self.jobs[jid] = job
            return deepcopy(job), True

    async def claim_job(self, job_id=None):
        async with self.lock:
            for job in self.jobs.values():
                if job_id is not None and job["id"] != job_id:
                    continue
                if ((job["state"] == "pending" and job["next_attempt"] <= now()) or (job["state"] == "processing" and job["lease_until"] <= now())):
                    job.update(state="processing", lease_until=later(60), attempts=job["attempts"] + 1)
                    return deepcopy(job)
        return None

    async def finish_job(self, jid, state="done", error=None):
        async with self.lock:
            self.jobs[jid].update(state=state, error=error, next_attempt=later(2))

    async def acquire(self, cid, owner):
        async with self.lock:
            c = self.conversations[cid]
            if c.get("lock_until", "") > now():
                return False
            c.update(lock_until=later(60), lock_owner=owner)
            return True

    async def release(self, cid, owner):
        async with self.lock:
            c = self.conversations[cid]
            if c.get("lock_owner") == owner:
                c.update(lock_until="", lock_owner="")

    async def append(self, cid, message, expected_mode=None):
        async with self.lock:
            c = self.conversations[cid]
            if expected_mode and c["mode"] != expected_mode:
                return False
            if any(m["id"] == message["id"] for m in c["messages"]):
                return False
            if len(c["messages"]) >= 500:
                raise ValueError("Conversation limit reached; archive this conversation before continuing")
            c["messages"].append(deepcopy(message))
            c.update(updated_at=now(), last_message=message["text"])
            return True

    async def set_language(self, cid, language):
        self.conversations[cid]["language"] = language

    async def handover(self, cid, message, language, reason):
        async with self.lock:
            c = self.conversations[cid]
            if any(m["id"] == message["id"] for m in c["messages"]):
                return
            if len(c["messages"]) >= 500:
                raise ValueError("Conversation limit reached; archive this conversation before continuing")
            for m in c["messages"]:
                if m["role"] == "assistant" and m["delivery_status"] == "queued":
                    m.update(delivery_status="failed", error="Suppressed after owner takeover", next_attempt=None)
            c["messages"].append(deepcopy(message))
            c.update(mode="human", language=language, handover_reason=reason, updated_at=now(), last_message=message["text"])

    async def set_mode(self, cid, mode, reason=None):
        async with self.lock:
            c = self.conversations[cid]
            c.update(mode=mode, handover_reason=reason if mode == "human" else None, updated_at=now())
            if mode == "human":
                for m in c["messages"]:
                    if m["role"] == "assistant" and m["delivery_status"] == "queued":
                        m.update(delivery_status="failed", error="Suppressed after owner takeover", next_attempt=None)

    async def claim_delivery(self):
        async with self.lock:
            for c in self.conversations.values():
                for m in c["messages"]:
                    if m["delivery_status"] == "sending" and m["lease_until"] <= now():
                        m.update(delivery_status="unknown", error="Delivery was interrupted; check provider before retrying")
                    if m["delivery_status"] == "queued" and m.get("next_attempt", "") <= now():
                        m.update(delivery_status="sending", lease_until=later(60), attempts=m["attempts"] + 1)
                        return deepcopy(c), deepcopy(m)
        return None

    async def delivery_result(self, cid, mid, status, error=None, provider_id=None, retry=False):
        async with self.lock:
            m = next(m for m in self.conversations[cid]["messages"] if m["id"] == mid)
            m.update(delivery_status="queued" if retry else status, error=error, provider_id=provider_id, next_attempt=later(min(60, 2 ** m["attempts"])) if retry else None)


class MongoStore:
    def __init__(self, business_id, uri, database):
        try:
            from pymongo import AsyncMongoClient, ReturnDocument
        except ImportError as exc:
            raise RuntimeError("Install requirements.txt to use STORAGE=mongo (PyMongo >= 4.13)") from exc
        self.business_id = business_id
        self.client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.db = self.client[database]
        self.after = ReturnDocument.AFTER

    def scope(self, **extra):
        return {"business_id": self.business_id, **extra}

    async def initialize(self):
        await self.client.admin.command("ping")
        await self.db.conversations.create_index([("business_id", 1), ("customer_id", 1), ("channel", 1)], unique=True)
        await self.db.ingress.create_index([("business_id", 1), ("state", 1), ("next_attempt", 1)])
        await self.db.products.create_index([("business_id", 1), ("id", 1)], unique=True)

    async def close(self):
        await self.client.close()

    async def seed(self, products):
        for product in products:
            await self.db.products.update_one(self.scope(id=product["id"]), {"$setOnInsert": {**product, "business_id": self.business_id}}, upsert=True)

    async def products(self):
        return [{k: v for k, v in p.items() if k not in {"_id", "business_id"}} async for p in self.db.products.find(self.scope())]

    async def ensure_conversation(self, customer_id, channel, customer_name="Customer"):
        from pymongo.errors import DuplicateKeyError
        cid = conversation_id(self.business_id, customer_id, channel)
        doc = {"id": cid, "business_id": self.business_id, "customer_id": customer_id, "customer_name": customer_name, "channel": channel, "mode": "bot", "language": "en", "updated_at": now(), "last_message": "", "handover_reason": None, "messages": []}
        try:
            await self.db.conversations.update_one(self.scope(id=cid), {"$setOnInsert": doc}, upsert=True)
        except DuplicateKeyError:
            pass  # Another worker inserted this same unique business/customer/channel.
        return await self.get(cid)

    async def get(self, cid):
        return await self.db.conversations.find_one(self.scope(id=cid), {"_id": 0})

    async def list(self):
        return [c async for c in self.db.conversations.find(self.scope(), {"_id": 0, "messages": 0}).sort("updated_at", -1).limit(200)]

    async def add_job(self, cid, external_id, text, received_at=None):
        from pymongo.errors import DuplicateKeyError
        jid = f"{self.business_id}:{cid}:{external_id}"
        doc = {"_id": jid, "id": jid, "business_id": self.business_id, "conversation_id": cid, "text": text, "state": "pending", "attempts": 0, "created_at": now(), "received_at": received_at or now(), "next_attempt": now()}
        try:
            await self.db.ingress.insert_one(doc)
            return doc, True
        except DuplicateKeyError:
            existing = await self.db.ingress.find_one(self.scope(_id=jid))
            if existing["text"] != text:
                raise ValueError("external_id was already used for different message text")
            return existing, False

    async def claim_job(self, job_id=None):
        query = self.scope(**{"$or": [{"state": "pending", "next_attempt": {"$lte": now()}}, {"state": "processing", "lease_until": {"$lte": now()}}]})
        if job_id is not None:
            query["id"] = job_id
        return await self.db.ingress.find_one_and_update(query, {"$set": {"state": "processing", "lease_until": later(60)}, "$inc": {"attempts": 1}}, sort=[("created_at", 1)], return_document=self.after)

    async def finish_job(self, jid, state="done", error=None):
        await self.db.ingress.update_one(self.scope(id=jid), {"$set": {"state": state, "error": error, "next_attempt": later(2)}})

    async def acquire(self, cid, owner):
        r = await self.db.conversations.update_one(self.scope(id=cid, **{"$or": [{"lock_until": {"$exists": False}}, {"lock_until": {"$lte": now()}}]}), {"$set": {"lock_until": later(60), "lock_owner": owner}})
        return r.modified_count == 1

    async def release(self, cid, owner):
        await self.db.conversations.update_one(self.scope(id=cid, lock_owner=owner), {"$set": {"lock_until": "", "lock_owner": ""}})

    async def append(self, cid, message, expected_mode=None):
        query = self.scope(id=cid, **{"messages.id": {"$ne": message["id"]}, "messages.499": {"$exists": False}})
        if expected_mode:
            query["mode"] = expected_mode
        r = await self.db.conversations.update_one(query, {"$push": {"messages": message}, "$set": {"updated_at": now(), "last_message": message["text"]}})
        if not r.modified_count:
            c = await self.get(cid)
            if c and len(c["messages"]) >= 500:
                raise ValueError("Conversation limit reached; archive this conversation before continuing")
        return r.modified_count == 1

    async def set_language(self, cid, language):
        await self.db.conversations.update_one(self.scope(id=cid), {"$set": {"language": language}})

    async def handover(self, cid, message, language, reason):
        # Pipeline updates preserve atomic mode switch + suppression + one-time
        # acknowledgement without a multi-document transaction/replica set.
        pending_suppression = {"$map": {"input": "$messages", "as": "m", "in": {"$cond": [
            {"$and": [{"$eq": ["$$m.role", "assistant"]}, {"$eq": ["$$m.delivery_status", "queued"]}]},
            {"$mergeObjects": ["$$m", {"delivery_status": "failed", "error": "Suppressed after owner takeover", "next_attempt": None}]},
            "$$m",
        ]}}}
        await self.db.conversations.update_one(self.scope(id=cid, **{"messages.id": {"$ne": message["id"]}, "messages.499": {"$exists": False}}), [{"$set": {"mode": "human", "language": language, "handover_reason": reason, "updated_at": now(), "last_message": {"$literal": message["text"]}, "messages": {"$concatArrays": [pending_suppression, {"$literal": [message]}]}}}])

    async def set_mode(self, cid, mode, reason=None):
        update = {"$set": {"mode": mode, "handover_reason": reason if mode == "human" else None, "updated_at": now()}}
        # The mode switch and suppression of pending bot messages are one atomic update.
        if mode == "human":
            update["$set"].update({"messages.$[m].delivery_status": "failed", "messages.$[m].error": "Suppressed after owner takeover", "messages.$[m].next_attempt": None})
            await self.db.conversations.update_one(self.scope(id=cid), update, array_filters=[{"m.role": "assistant", "m.delivery_status": "queued"}])
        else:
            await self.db.conversations.update_one(self.scope(id=cid), update)

    async def claim_delivery(self):
        await self.db.conversations.update_many(self.scope(messages={"$elemMatch": {"delivery_status": "sending", "lease_until": {"$lte": now()}}}), {"$set": {"messages.$[m].delivery_status": "unknown", "messages.$[m].error": "Delivery interrupted; check provider before retrying"}}, array_filters=[{"m.delivery_status": "sending", "m.lease_until": {"$lte": now()}}])
        c = await self.db.conversations.find_one(self.scope(messages={"$elemMatch": {"delivery_status": "queued", "next_attempt": {"$lte": now()}}}))
        if not c:
            return None
        m = next(m for m in c["messages"] if m["delivery_status"] == "queued" and m.get("next_attempt", "") <= now())
        query = self.scope(id=c["id"], messages={"$elemMatch": {"id": m["id"], "delivery_status": "queued", "next_attempt": {"$lte": now()}}})
        updated = await self.db.conversations.find_one_and_update(query, {"$set": {"messages.$.delivery_status": "sending", "messages.$.lease_until": later(60)}, "$inc": {"messages.$.attempts": 1}}, return_document=self.after)
        if updated:
            return updated, next(x for x in updated["messages"] if x["id"] == m["id"])
        return None

    async def delivery_result(self, cid, mid, status, error=None, provider_id=None, retry=False):
        c = await self.get(cid)
        m = next(m for m in c["messages"] if m["id"] == mid)
        fields = {"delivery_status": "queued" if retry else status, "error": error, "provider_id": provider_id, "next_attempt": later(min(60, 2 ** m["attempts"])) if retry else None}
        await self.db.conversations.update_one(self.scope(id=cid, **{"messages.id": mid}), {"$set": {f"messages.$.{key}": value for key, value in fields.items()}})
