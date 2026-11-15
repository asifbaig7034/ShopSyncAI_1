"""WhatsApp delivery uses MongoDB leases; external HTTP never runs in a transaction."""
import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from .db import get_store, run_transaction
from .delivery import apply_pending_for, error_codes, failure_state, reconcile_waiting, retry_at
from .service import now


def configured():
    return bool(os.getenv("WA_ACCESS_TOKEN") and os.getenv("WA_PHONE_NUMBER_ID")
                and os.getenv("WA_GRAPH_VERSION"))


def claim_due():
    def claim(db):
        timestamp = now()
        expired = db.all("outbox", {"status": "SENDING", "lease_until": {"$lte": timestamp}}, limit=20)
        for row in expired:
            db.update("outbox", {"_id": row["id"]}, {"$set": {
                "status": "UNCERTAIN", "last_error": "Interrupted send; review before retry",
                "claim_id": None, "lease_until": None}})
            apply_pending_for(db, [row.get("provider_id"), *row.get("provider_ids", [])])
        rows = db.all("outbox", {"status": {"$in": ["PENDING", "FAILED"]},
                                "next_attempt_at": {"$lte": timestamp}},
                      sort=[("created_at", 1), ("_id", 1)], limit=1)
        if not rows:
            return None
        row = rows[0]
        db.update("outbox", {"_id": row["id"]}, {"$set": {
            "status": "SENDING", "claim_id": uuid.uuid4().hex,
            "lease_until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(timespec="microseconds")},
            "$inc": {"attempts": 1}})
        result = db.get("outbox", {"_id": row["id"]})
        result["_id"] = result["id"]  # Compatibility for existing operator/test helpers.
        return result
    return run_transaction(claim)


def finish(message, **fields):
    def complete(db):
        row = db.get("outbox", {"_id": message["_id"]})
        if row is None:
            return False
        provider = fields.get("provider_id")
        provider_ids = list(row.get("provider_ids", []))
        if provider and provider not in provider_ids:
            provider_ids.append(provider)
            db.update("outbox", {"_id": row["id"]}, {"$set": {"provider_ids": provider_ids}})
        updated = False
        if row["status"] == "SENDING" and row.get("claim_id") == message["claim_id"]:
            saved = {**fields, "claim_id": None, "lease_until": None}
            if provider:
                saved.update(provider_attempt_number=message["attempts"], provider_status=None,
                             delivery_timestamp=-1, delivery_rank=0)
            db.update("outbox", {"_id": row["id"]}, {"$set": saved})
            updated = True
        apply_pending_for(db, [provider, row.get("provider_id"), *provider_ids])
        return updated
    return run_transaction(complete)


async def send_due(limit=20):
    await asyncio.to_thread(reconcile_waiting)
    for _ in range(limit):
        message = await asyncio.to_thread(claim_due)
        if message is None:
            return
        if not configured():
            await asyncio.to_thread(finish, message, status="NEEDS_CONFIGURATION",
                                    last_error="Configure WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID and WA_GRAPH_VERSION")
            continue
        person = await asyncio.to_thread(get_store().get, "customers", {"phone": message["phone"]})
        last_inbound = person.get("last_inbound_at") if person else None
        if not last_inbound or datetime.fromisoformat(last_inbound) + timedelta(hours=24) < datetime.now(timezone.utc):
            await asyncio.to_thread(finish, message, status="NEEDS_TEMPLATE",
                                    last_error="Outside customer service window; approved template delivery is required")
            continue
        version, phone_id = os.getenv("WA_GRAPH_VERSION", ""), os.getenv("WA_PHONE_NUMBER_ID", "")
        if not re.fullmatch(r"v\d+\.\d+", version) or not re.fullmatch(r"\d+", phone_id):
            await asyncio.to_thread(finish, message, status="NEEDS_CONFIGURATION",
                                    last_error="Invalid WA_GRAPH_VERSION or WA_PHONE_NUMBER_ID")
            continue
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.post(
                    f"https://graph.facebook.com/{version}/{phone_id}/messages",
                    headers={"Authorization": "Bearer " + os.environ["WA_ACCESS_TOKEN"]},
                    json={"messaging_product": "whatsapp", "recipient_type": "individual",
                          "to": message["phone"], "type": "text", "text": {"body": message["body"]}},
                )
            response.raise_for_status()
            data = response.json()
            messages = data.get("messages", []) if isinstance(data, dict) else []
            provider = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
            if not isinstance(provider, str) or not 1 <= len(provider) <= 512:
                await asyncio.to_thread(finish, message, status="UNCERTAIN",
                                        last_error="Provider response missing message id; review before retry")
            else:
                await asyncio.to_thread(finish, message, status="ACCEPTED", provider_id=provider, last_error=None)
        except httpx.HTTPStatusError as error:
            try:
                data = error.response.json()
                provider_error = data.get("error", {}) if isinstance(data, dict) else {}
                codes = error_codes([provider_error])
            except ValueError:
                codes = []
            target = failure_state(codes, message["attempts"], error.response.status_code)
            fields = dict(status=target, last_error="Provider rejected request" +
                          (" (codes: " + ",".join(str(code) for code in codes) + ")" if codes else ""))
            if target == "FAILED":
                fields["next_attempt_at"] = retry_at(message["attempts"])
            await asyncio.to_thread(finish, message, **fields)
        except (httpx.RequestError, ValueError, KeyError, IndexError):
            await asyncio.to_thread(finish, message, status="UNCERTAIN",
                                    last_error="Delivery result unknown; review before retry")


async def outbox_worker():
    while True:
        try:
            await send_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # A recoverable lease remains if the database becomes unavailable.
        await asyncio.sleep(2)


def list_outbox():
    return get_store().all("outbox", sort=[("created_at", -1), ("_id", -1)], limit=100)


def retry_message(message_id):
    def retry(db):
        row = db.get("outbox", {"_id": message_id})
        if row is None or row["status"] not in (
                "FAILED", "MANUAL_REVIEW", "NEEDS_CONFIGURATION", "NEEDS_TEMPLATE", "UNCERTAIN"):
            return False
        db.update("outbox", {"_id": message_id}, {"$set": {
            "status": "PENDING", "next_attempt_at": now(), "last_error": None,
            "claim_id": None, "lease_until": None}})
        return True
    return run_transaction(retry)
