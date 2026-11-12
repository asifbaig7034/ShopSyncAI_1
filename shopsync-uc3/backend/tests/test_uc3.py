"""Offline behavioral checks. No Mongo server, LLM key or WhatsApp call needed."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import unittest

from fastapi.testclient import TestClient
import httpx

from app.catalogue import DEMO_PRODUCTS
from app.config import Settings
from app.main import create_app
from app.providers import DemoChannel, DemoClassifier, Intent, IntegrationAdapter, OpenAIClassifier, SendResult, WhatsAppChannel, redact
from app.repository import MemoryStore, new_message
from app.service import ConversationService


AUTH = {"Authorization": "Bearer demo-owner-token"}


class ConversationAPITests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore("demo-business")
        self.app = create_app(Settings(), store=self.store, run_worker=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def send(self, text, customer="customer-1", external_id=None):
        response = self.client.post("/api/demo/messages", headers=AUTH, json={"customer_id": customer, "text": text, "external_id": external_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["conversation"]

    def test_health_and_owner_auth(self):
        self.assertTrue(self.client.get("/api/health").json()["demo_mode"])
        self.assertEqual(self.client.get("/api/conversations").status_code, 401)
        self.assertEqual(self.client.get("/api/products", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(len(self.client.get("/api/products", headers=AUTH).json()["items"]), 3)

    def test_product_reply_uses_current_catalogue_and_description(self):
        self.store.catalogue["shirt-001"]["price"] = 1999
        self.store.catalogue["shirt-001"]["stock"] = 7
        c = self.send("What is the shirt price?")
        self.assertIn("1,999.00", c["messages"][-1]["text"])
        self.assertIn("stock: 7", c["messages"][-1]["text"])
        self.assertIn("Soft cotton", c["messages"][-1]["text"])
        self.assertEqual(c["messages"][-1]["delivery_status"], "sent")

    def test_urdu_and_roman_urdu(self):
        for customer, text, language, marker in [("ur", "شرٹ کی قیمت کیا ہے؟", "ur", "قیمت"), ("roman", "shirt ki qeemat kya hai?", "roman_ur", "Qeemat")]:
            c = self.send(text, customer)
            self.assertEqual(c["language"], language)
            self.assertIn(marker, c["messages"][-1]["text"])
            self.assertIn("1,800.00", c["messages"][-1]["text"])

    def test_missing_product_asks_for_details_without_inventing_price(self):
        c = self.send("What is the iPhone price?")
        reply = c["messages"][-1]["text"]
        self.assertIn("could not identify", reply)
        self.assertNotIn("1,800", reply)
        self.assertEqual(c["mode"], "bot")

    def test_out_of_stock_is_explicit(self):
        c = self.send("Are shoes available?")
        self.assertIn("stock: 0", c["messages"][-1]["text"])
        self.assertIn("out of stock", c["messages"][-1]["text"])

    def test_conversation_context_and_timestamp_history(self):
        c = self.send("shirt price")
        c = self.send("price?")
        self.assertEqual(len(c["messages"]), 4)
        self.assertIn("Classic Cotton Shirt", c["messages"][-1]["text"])
        self.assertTrue(all(m["created_at"] for m in c["messages"]))
        summaries = self.client.get("/api/conversations", headers=AUTH).json()["items"]
        self.assertEqual(summaries[0]["id"], c["id"])
        self.assertNotIn("messages", summaries[0])

    def test_human_takeover_pauses_bot_and_owner_can_reply_and_resume(self):
        c = self.send("Please connect me to a human")
        self.assertEqual(c["mode"], "human")
        self.assertEqual(c["messages"][-1]["role"], "system")
        self.assertEqual(c["messages"][-1]["delivery_status"], "sent")
        c = self.send("shirt price")
        self.assertEqual(c["messages"][-1]["role"], "customer")
        response = self.client.post(f"/api/conversations/{c['id']}/messages", headers=AUTH, json={"text": "Owner here, how can I help?", "external_id": "owner-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["conversation"]["messages"][-1]["role"], "owner")
        self.client.post(f"/api/conversations/{c['id']}/handover", headers=AUTH, json={"mode": "bot"})
        c = self.send("shirt price")
        self.assertEqual(c["messages"][-1]["role"], "assistant")

    def test_owner_message_http_retry_is_idempotent_and_rejects_collision(self):
        c = self.send("human please")
        url = f"/api/conversations/{c['id']}/messages"
        payload = {"text": "Hello from owner", "external_id": "owner-retry-1"}
        first = self.client.post(url, headers=AUTH, json=payload).json()["conversation"]
        retry = self.client.post(url, headers=AUTH, json=payload).json()["conversation"]
        self.assertEqual(len(first["messages"]), len(retry["messages"]))
        self.assertEqual(first["messages"][-1]["id"], retry["messages"][-1]["id"])
        self.assertEqual(self.client.post(url, headers=AUTH, json={**payload, "text": "Different"}).status_code, 409)

    def test_duplicate_inbound_message_is_processed_once(self):
        c = self.send("shirt price", external_id="wa-123")
        retry = self.send("shirt price", external_id="wa-123")
        self.assertEqual(len(retry["messages"]), 2)
        self.assertEqual(c["messages"], retry["messages"])
        bad = self.client.post("/api/demo/messages", headers=AUTH, json={"customer_id": "customer-1", "text": "Different", "external_id": "wa-123"})
        self.assertEqual(bad.status_code, 409)

    def test_same_external_id_is_scoped_to_customer(self):
        self.send("shirt price", "alice", "ref-1")
        bob = self.send("bag price", "bob", "ref-1")
        self.assertIn("Everyday Tote", bob["messages"][-1]["text"])
        self.assertNotIn("Classic Cotton Shirt", bob["messages"][-1]["text"])

    def test_orders_and_complaints_go_to_owner_when_adapters_unavailable(self):
        for customer, text, expected in [("order", "I want to buy a shirt", "No order change"), ("complaint", "I have a complaint: damaged item", "complaint")]:
            c = self.send(text, customer)
            self.assertEqual(c["mode"], "human")
            self.assertIn(expected, c["messages"][-1]["text"])
            self.assertEqual(self.store.catalogue["shirt-001"]["stock"], 12)

    def test_unknown_request_is_handed_to_owner(self):
        c = self.send("Please explain quantum gravity")
        self.assertEqual(c["mode"], "human")
        self.assertIn("unsure", c["messages"][-1]["text"])

    def test_blank_input_and_unknown_conversation_rejected(self):
        self.assertEqual(self.client.post("/api/demo/messages", headers=AUTH, json={"customer_id": "x", "text": "  "}).status_code, 422)
        self.assertEqual(self.client.get("/api/conversations/not-found", headers=AUTH).status_code, 404)

    def test_disabled_agent_preserves_message_without_reply(self):
        self.app.state.service.settings.customer_agent_enabled = False
        c = self.send("shirt price")
        self.assertEqual(len(c["messages"]), 1)
        self.assertEqual(c["mode"], "human")

    def test_llm_failure_routes_to_owner(self):
        class Broken:
            async def classify(self, *args):
                raise RuntimeError("fake outage")
        self.app.state.service.classifier = Broken()
        c = self.send("shirt price")
        self.assertEqual(c["mode"], "human")
        self.assertIn("temporarily unavailable", c["messages"][-1]["text"])


def live_settings():
    return Settings(demo_mode=False, storage="mongo", llm_provider="openai", openai_api_key="not-a-real-key", openai_model="test-model", dashboard_api_token="unique-test-owner-token-123456789", channel="whatsapp", whatsapp_access_token="not-a-real-token", whatsapp_phone_number_id="phone-1", whatsapp_app_secret="test-secret", whatsapp_verify_token="test-verify", whatsapp_graph_version="v-test")


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.settings = live_settings()
        self.store = MemoryStore(self.settings.business_id)
        self.app = create_app(self.settings, store=self.store, classifier=DemoClassifier(), run_worker=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def body(self, phone="phone-1"):
        return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": phone}, "messages": [{"id": "wamid-1", "from": "923001234567", "timestamp": str(int(datetime.now(timezone.utc).timestamp())), "type": "text", "text": {"body": "shirt price"}}]}}]}]}

    def post_signed(self, body):
        raw = json.dumps(body).encode()
        signature = "sha256=" + hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest()
        return self.client.post("/api/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": signature, "content-type": "application/json"})

    def test_signature_and_verification_token_required(self):
        self.assertEqual(self.client.post("/api/webhooks/whatsapp", json=self.body()).status_code, 403)
        good = self.client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify", "hub.challenge": "123"})
        self.assertEqual(good.text, "123")
        self.assertEqual(self.client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "wrong"}).status_code, 403)

    def test_webhook_is_enqueued_durably_before_processing_and_duplicate_is_safe(self):
        body = self.body()
        self.assertEqual(self.post_signed(body).status_code, 200)
        self.assertEqual(len(self.store.jobs), 1)
        self.assertEqual(next(iter(self.store.jobs.values()))["state"], "pending")
        self.assertEqual(len(next(iter(self.store.conversations.values()))["messages"]), 0)
        self.assertEqual(self.post_signed(body).status_code, 200)
        self.assertEqual(len(self.store.jobs), 1)

    def test_other_business_phone_is_ignored_and_signed_nonobject_rejected(self):
        self.assertEqual(self.post_signed(self.body("another-phone")).json()["accepted"], 0)
        self.assertEqual(self.post_signed([]).status_code, 400)
        self.assertEqual(len(self.store.jobs), 0)

    def test_live_mode_disables_demo_simulator(self):
        headers = {"Authorization": f"Bearer {self.settings.dashboard_api_token}"}
        self.assertEqual(self.client.post("/api/demo/messages", headers=headers, json={"customer_id": "x", "text": "hi"}).status_code, 404)

    def test_owner_cannot_send_freeform_reply_outside_24h_window(self):
        self.post_signed(self.body())
        c = next(iter(self.store.conversations.values()))
        old = new_message("old", "customer", "Hello", "received")
        old["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        c["messages"].append(old)
        response = self.client.post(f"/api/conversations/{c['id']}/messages", headers={"Authorization": f"Bearer {self.settings.dashboard_api_token}"}, json={"text": "Late reply"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(c["messages"]), 1)


class AsyncBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings()
        self.store = MemoryStore("demo-business")
        await self.store.seed(DEMO_PRODUCTS)
        self.http = httpx.AsyncClient()
        self.service = ConversationService(self.settings, self.store, DemoClassifier(), DemoChannel(), DemoChannel(), IntegrationAdapter(self.settings, self.http))

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_owner_takeover_while_llm_in_flight_suppresses_result(self):
        started, release = asyncio.Event(), asyncio.Event()
        class Slow:
            async def classify(self, *args):
                started.set()
                await release.wait()
                return Intent(intent="product", language="en", product_ids=["shirt-001"])
        self.service.classifier = Slow()
        cid, jid = await self.service.receive("x", "shirt price")
        task = asyncio.create_task(self.service.process_job(jid))
        await started.wait()
        await self.store.set_mode(cid, "human", "Owner took over")
        release.set()
        await task
        c = await self.store.get(cid)
        self.assertEqual([m["role"] for m in c["messages"]], ["customer"])

    async def test_queued_bot_reply_suppressed_on_takeover(self):
        cid, jid = await self.service.receive("x", "shirt price")
        await self.service.process_job(jid)
        await self.store.set_mode(cid, "human", "Owner took over")
        self.assertFalse(await self.service.deliver_one())
        self.assertEqual((await self.store.get(cid))["messages"][-1]["delivery_status"], "failed")

    async def test_known_rejection_retries_but_ambiguous_outcome_does_not(self):
        class FakeChannel:
            calls = 0
            async def send(self, *args):
                self.calls += 1
                return SendResult(status="failed", retryable=True, error="rate limit") if self.calls == 1 else SendResult(status="sent")
        channel = FakeChannel()
        self.service.demo_channel = channel
        cid, jid = await self.service.receive("x", "shirt price")
        await self.service.process_job(jid)
        await self.service.deliver_one()
        self.assertEqual((await self.store.get(cid))["messages"][-1]["delivery_status"], "queued")
        self.store.conversations[cid]["messages"][-1]["next_attempt"] = ""
        await self.service.deliver_one()
        self.assertEqual(channel.calls, 2)
        self.assertEqual((await self.store.get(cid))["messages"][-1]["delivery_status"], "sent")
        class Ambiguous:
            async def send(self, *args):
                return SendResult(status="unknown", error="No acknowledgement")
        self.service.demo_channel = Ambiguous()
        _, jid = await self.service.receive("x", "bag price")
        await self.service.process_job(jid)
        await self.service.deliver_one()
        self.assertEqual((await self.store.get(cid))["messages"][-1]["delivery_status"], "unknown")
        self.assertFalse(await self.service.deliver_one())

    async def test_crashed_sending_lease_is_unknown_not_resent(self):
        cid, jid = await self.service.receive("x", "shirt price")
        await self.service.process_job(jid)
        await self.store.claim_delivery()
        self.store.conversations[cid]["messages"][-1]["lease_until"] = ""
        self.assertFalse(await self.service.deliver_one())
        self.assertEqual((await self.store.get(cid))["messages"][-1]["delivery_status"], "unknown")

    async def test_model_request_minimizes_customer_data_and_rejects_unknown_product_id(self):
        seen = {}
        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps({"intent": "product", "language": "en", "product_ids": ["invented-product"], "confidence": 1})}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            classifier = OpenAIClassifier(Settings(openai_api_key="secret", openai_model="model"), client)
            result = await classifier.classify("shirt price; email asif@example.com phone +923001234567", [{"role": "customer", "text": "address: 12 Sample Street"}], DEMO_PRODUCTS)
        self.assertFalse(seen["store"])
        self.assertNotIn("asif@example.com", seen["input"])
        self.assertNotIn("923001234567", seen["input"])
        self.assertNotIn("12 Sample Street", seen["input"])
        self.assertEqual(result.intent, "unknown")

    async def test_whatsapp_send_outcomes_are_conservative(self):
        for status_code, payload, status, retry in [(429, {}, "failed", True), (503, {}, "unknown", False), (400, {}, "failed", False), (200, {"messages": [{"id": "wamid.1"}]}, "sent", False)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json=payload))) as client:
                result = await WhatsAppChannel(live_settings(), client).send("923001234567", "Hello")
            self.assertEqual((result.status, result.retryable), (status, retry))


if __name__ == "__main__":
    unittest.main()
