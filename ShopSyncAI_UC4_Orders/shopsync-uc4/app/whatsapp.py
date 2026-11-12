import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from .db import transaction
from .service import now


def configured():
    return bool(os.getenv("WA_ACCESS_TOKEN") and os.getenv("WA_PHONE_NUMBER_ID")
                and os.getenv("WA_GRAPH_VERSION"))


async def send_due(limit=20):
    """Claim messages in SQLite; send outside the transaction; track the result."""
    for _ in range(limit):
        with transaction() as db:
            row = db.execute("SELECT o.*, c.last_inbound_at FROM outbox o "
                             "LEFT JOIN customers c ON c.phone=o.phone "
                             "WHERE o.status IN ('PENDING','FAILED') AND o.next_attempt_at<=? "
                             "ORDER BY o.created_at, o.rowid LIMIT 1", (now(),)).fetchone()
            if not row:
                return
            message = dict(row)
            if not configured():
                db.execute("UPDATE outbox SET status='NEEDS_CONFIGURATION',last_error=? WHERE id=?",
                           ("Set WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, and WA_GRAPH_VERSION", row["id"]))
                continue
            last_inbound = row["last_inbound_at"]
            if not last_inbound or datetime.fromisoformat(last_inbound) + timedelta(hours=24) < datetime.now(timezone.utc):
                db.execute("UPDATE outbox SET status='NEEDS_TEMPLATE',last_error=? WHERE id=?",
                           ("Text message outside customer service window; use an approved template", row["id"]))
                continue
            db.execute("UPDATE outbox SET status='SENDING',attempts=attempts+1 WHERE id=?", (row["id"],))
        version = os.getenv("WA_GRAPH_VERSION", "")
        phone_id = os.getenv("WA_PHONE_NUMBER_ID", "")
        if not re.fullmatch(r"v\d+\.\d+", version) or not re.fullmatch(r"\d+", phone_id):
            error, provider_id = "Invalid WA_GRAPH_VERSION or WA_PHONE_NUMBER_ID", None
        else:
            url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    response = await client.post(url, headers={
                        "Authorization": "Bearer " + os.environ["WA_ACCESS_TOKEN"]}, json={
                        "messaging_product": "whatsapp", "recipient_type": "individual",
                        "to": message["phone"], "type": "text", "text": {"body": message["body"]}})
                response.raise_for_status()
                provider_id = response.json().get("messages", [{}])[0].get("id")
                error = None
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                # Do not include provider response data or secrets in persistent errors.
                error, provider_id = type(exc).__name__, None
        with transaction() as db:
            if error:
                attempts = message["attempts"] + 1
                status = "FAILED" if attempts < 5 else "MANUAL_REVIEW"
                next_try = (datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** attempts * 10))).isoformat(timespec="seconds")
                db.execute("UPDATE outbox SET status=?,next_attempt_at=?,last_error=? WHERE id=?",
                           (status, next_try, error, message["id"]))
            else:
                db.execute("UPDATE outbox SET status='SENT',provider_id=?,last_error=NULL WHERE id=?",
                           (provider_id, message["id"]))


async def outbox_worker():
    try:
        while True:
            try:
                await send_due()
            except Exception:
                # The worker must remain available for the next pass.
                pass
            await asyncio.sleep(15)
    except asyncio.CancelledError:
        raise


def list_outbox():
    from .db import connect
    from contextlib import closing
    with closing(connect()) as db:
        return [dict(x) for x in db.execute("SELECT * FROM outbox ORDER BY created_at DESC, rowid DESC LIMIT 100")]


def retry_message(message_id):
    with transaction() as db:
        row = db.execute("SELECT status FROM outbox WHERE id=?", (message_id,)).fetchone()
        if not row:
            return False
        if row["status"] not in ("FAILED", "MANUAL_REVIEW", "NEEDS_CONFIGURATION", "NEEDS_TEMPLATE", "UNCERTAIN"):
            return False
        db.execute("UPDATE outbox SET status='PENDING',next_attempt_at=?,last_error=NULL WHERE id=?",
                   (now(), message_id))
        return True
