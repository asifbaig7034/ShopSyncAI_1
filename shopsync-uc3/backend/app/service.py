import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import uuid

from .agent import phrase, product_answer
from .providers import language_of
from .repository import new_message, now

logger = logging.getLogger(__name__)


def reply_window_open(conversation):
    inbound_times = [m["created_at"] for m in conversation["messages"] if m["role"] == "customer"]
    if not inbound_times:
        return False
    latest = max(datetime.fromisoformat(t) for t in inbound_times)
    return latest > datetime.now(timezone.utc) - timedelta(hours=24)


class ConversationService:
    def __init__(self, settings, store, classifier, demo_channel, whatsapp_channel, adapter):
        self.settings, self.store, self.classifier = settings, store, classifier
        self.demo_channel, self.whatsapp_channel, self.adapter = demo_channel, whatsapp_channel, adapter

    async def receive(self, customer_id, text, channel="demo", external_id=None, received_at=None, customer_name="Customer"):
        conversation = await self.store.ensure_conversation(customer_id, channel, customer_name)
        job, _ = await self.store.add_job(conversation["id"], external_id or str(uuid.uuid4()), text, received_at)
        return conversation["id"], job["id"]

    async def process_job(self, job_id=None):
        job = await self.store.claim_job(job_id)
        if not job:
            return False
        cid, jid = job["conversation_id"], job["id"]
        owner = str(uuid.uuid4())
        if not await self.store.acquire(cid, owner):
            await self.store.finish_job(jid, "pending")
            return False
        digest = hashlib.sha256(jid.encode()).hexdigest()
        customer_mid, reply_mid = "in-" + digest, "out-" + digest
        try:
            c = await self.store.get(cid)
            if any(m["id"] == reply_mid for m in c["messages"]):
                await self.store.finish_job(jid)
                return True
            inbound = new_message(customer_mid, "customer", job["text"], "received")
            inbound["created_at"] = job["received_at"]
            await self.store.append(cid, inbound)
            c = await self.store.get(cid)
            if c["mode"] == "human":
                await self.store.finish_job(jid)
                return True
            language = language_of(job["text"])
            if not self.settings.customer_agent_enabled:
                await self.store.set_mode(cid, "human", "Customer Agent is disabled in business settings")
                await self.store.finish_job(jid)
                return True
            products = await self.store.products()
            try:
                intent = await asyncio.wait_for(self.classifier.classify(job["text"], c["messages"][:-1], products), timeout=6.5)
            except Exception:
                logger.warning("Intent service unavailable; conversation flagged for owner")
                await self.handover(cid, reply_mid, language, "LLM unavailable", "llm_failed")
                await self.store.finish_job(jid)
                return True
            language = intent.language
            await self.store.set_language(cid, language)
            # The UI may have taken over while the provider was processing.
            c = await self.store.get(cid)
            if c["mode"] != "bot":
                await self.store.finish_job(jid)
                return True
            if intent.intent == "human":
                await self.handover(cid, reply_mid, language, "Customer requested a human", "human")
            elif intent.intent == "unknown" or intent.confidence < 0.65:
                await self.handover(cid, reply_mid, language, "Assistant uncertain", "uncertain")
            elif intent.intent in {"place_order", "order_status", "cancel_order", "return_refund", "complaint"}:
                kind = "orchestrator" if intent.intent == "complaint" else "orders"
                try:
                    reply = await self.adapter.call(kind, intent.intent, c, jid, job["text"])
                except Exception:
                    logger.warning("Integration unavailable; conversation flagged for owner")
                    reply = None
                if reply:
                    await self.store.append(cid, new_message(reply_mid, "assistant", reply, "queued"), expected_mode="bot")
                else:
                    key = "complaint_unavailable" if kind == "orchestrator" else "orders_unavailable"
                    await self.handover(cid, reply_mid, language, f"{kind} integration unavailable", key)
            elif intent.intent == "delivery":
                if self.settings.delivery_policy:
                    await self.store.append(cid, new_message(reply_mid, "assistant", self.settings.delivery_policy, "queued"), expected_mode="bot")
                else:
                    await self.handover(cid, reply_mid, language, "Delivery policy not configured", "delivery_missing")
            else:
                reply = phrase("greeting", language) if intent.intent == "greeting" else product_answer(intent, products)
                await self.store.append(cid, new_message(reply_mid, "assistant", reply, "queued"), expected_mode="bot")
            await self.store.finish_job(jid)
            return True
        except Exception as exc:
            # Persist bounded retry state; never print customer text or credentials.
            logger.error("Conversation processing failed (%s)", type(exc).__name__)
            state = "failed" if job["attempts"] >= 3 else "pending"
            await self.store.finish_job(jid, state, type(exc).__name__)
            if state == "failed":
                await self.store.set_mode(cid, "human", "Message processing failed; owner review required")
            return False
        finally:
            await self.store.release(cid, owner)

    async def handover(self, cid, mid, language, reason, key):
        # System role carries the one-time handover acknowledgement while bot
        # replies are paused. Subsequent incoming messages receive no bot reply.
        await self.store.handover(cid, new_message(mid, "system", phrase(key, language), "queued"), language, reason)

    async def deliver_one(self):
        item = await self.store.claim_delivery()
        if not item:
            return False
        conversation, message = item
        cid, mid = conversation["id"], message["id"]
        channel = self.demo_channel
        if conversation["channel"] == "whatsapp":
            if self.settings.demo_mode or self.settings.channel != "whatsapp" or not self.settings.whatsapp_connected:
                await self.store.delivery_result(cid, mid, "failed", "WhatsApp is not connected")
                return True
            if not reply_window_open(conversation):
                await self.store.delivery_result(cid, mid, "failed", "24-hour customer reply window has closed; template messaging is not implemented")
                return True
            channel = self.whatsapp_channel
        try:
            result = await asyncio.wait_for(channel.send(conversation["customer_id"], message["text"]), timeout=9)
        except Exception:
            # Request may already have been submitted; never blindly resend.
            await self.store.delivery_result(cid, mid, "unknown", "Send outcome uncertain; inspect provider before retrying")
            return True
        retry = result.retryable and message["attempts"] < 3
        await self.store.delivery_result(cid, mid, result.status, result.error, result.provider_id, retry)
        return True

    async def owner_reply(self, conversation, text, external_id=None):
        cid = conversation["id"]
        mid = "owner-" + hashlib.sha256(f"{cid}:{external_id}".encode()).hexdigest() if external_id else str(uuid.uuid4())
        existing = next((m for m in conversation["messages"] if m["id"] == mid), None)
        if existing:
            if existing["text"] != text:
                raise ValueError("external_id was already used for different message text")
            return  # Reuse the original delivery state; never resend on HTTP retry.
        if conversation["channel"] == "whatsapp" and not reply_window_open(conversation):
            raise ValueError("24-hour customer reply window is closed; template messaging is not implemented")
        await self.store.set_mode(cid, "human", "Owner is handling this conversation")
        added = await self.store.append(cid, new_message(mid, "owner", text, "queued"), expected_mode="human")
        if not added:
            current = await self.store.get(cid)
            existing = next((m for m in current["messages"] if m["id"] == mid), None)
            if existing and existing["text"] != text:
                raise ValueError("external_id was already used for different message text")
        await self.deliver_one()

    async def worker(self):
        while True:
            try:
                processed = await self.process_job()
                delivered = await self.deliver_one()
                if not (processed or delivered):
                    await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker operation failed (%s)", type(exc).__name__)
                await asyncio.sleep(1)
