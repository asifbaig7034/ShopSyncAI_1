"""WhatsApp delivery uses MongoDB leases; external HTTP never runs in a transaction."""
import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from pymongo import ReturnDocument

from .db import get_database, get_store
from .service import now


def configured():
    return bool(os.getenv("WA_ACCESS_TOKEN") and os.getenv("WA_PHONE_NUMBER_ID")
                and os.getenv("WA_GRAPH_VERSION"))


def claim_due():
    outbox = get_database()["outbox"]
    timestamp = now()
    outbox.update_many(
        {"status": "SENDING", "lease_until": {"$lte": timestamp}},
        {"$set": {"status": "UNCERTAIN", "last_error": "Interrupted send; review before retry",
                  "claim_id": None, "lease_until": None}},
    )
    return outbox.find_one_and_update(
        {"status": {"$in": ["PENDING", "FAILED"]}, "next_attempt_at": {"$lte": timestamp}},
        {"$set": {"status": "SENDING", "claim_id": uuid.uuid4().hex,
                  "lease_until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(timespec="microseconds")},
         "$inc": {"attempts": 1}},
        sort=[("created_at", 1), ("_id", 1)], return_document=ReturnDocument.AFTER,
    )


def finish(message, **fields):
    fields.update(claim_id=None, lease_until=None)
    get_database()["outbox"].update_one(
        {"_id": message["_id"], "status": "SENDING", "claim_id": message["claim_id"]},
        {"$set": fields},
    )


async def send_due(limit=20):
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
            provider_id = response.json().get("messages", [{}])[0].get("id")
            if not provider_id:
                await asyncio.to_thread(finish, message, status="UNCERTAIN",
                                        last_error="Provider response missing message id; review before retry")
            else:
                await asyncio.to_thread(finish, message, status="SENT", provider_id=provider_id, last_error=None)
        except httpx.HTTPStatusError:
            attempts = message["attempts"]
            await asyncio.to_thread(
                finish, message, status="FAILED" if attempts < 5 else "MANUAL_REVIEW",
                last_error="Provider rejected request",
                next_attempt_at=(datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** attempts * 10))).isoformat(timespec="microseconds"),
            )
        except (httpx.RequestError, ValueError, KeyError, IndexError):
            # A timeout/disconnect may occur after provider acceptance. Automatic
            # retry could send a duplicate; an owner must review this case.
            await asyncio.to_thread(finish, message, status="UNCERTAIN",
                                    last_error="Delivery result unknown; review before retry")


async def outbox_worker():
    while True:
        try:
            await send_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Database outages leave a recoverable lease and must not kill retries.
            pass
        await asyncio.sleep(2)


def list_outbox():
    return get_store().all("outbox", sort=[("created_at", -1), ("_id", -1)], limit=100)


def retry_message(message_id):
    result = get_database()["outbox"].update_one(
        {"_id": message_id, "status": {"$in": [
            "FAILED", "MANUAL_REVIEW", "NEEDS_CONFIGURATION", "NEEDS_TEMPLATE", "UNCERTAIN"]}},
        {"$set": {"status": "PENDING", "next_attempt_at": now(), "last_error": None,
                  "claim_id": None, "lease_until": None}},
    )
    return bool(result.modified_count)
