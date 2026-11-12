import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from pydantic import BaseModel, Field
from typing import Literal

from .catalogue import DEMO_PRODUCTS
from .config import Settings
from .providers import DemoChannel, DemoClassifier, IntegrationAdapter, OpenAIClassifier, WhatsAppChannel
from .repository import MemoryStore, MongoStore, public_conversation
from .service import ConversationService


class DemoMessage(BaseModel):
    customer_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    external_id: str | None = Field(default=None, max_length=180)


class OwnerMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    external_id: str | None = Field(default=None, max_length=180)


class Handover(BaseModel):
    mode: Literal["human", "bot"]


def create_app(settings=None, store=None, classifier=None, channel=None, adapter=None, run_worker=True):
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        config.validate()
        repository = store or (MemoryStore(config.business_id) if config.storage == "memory" else MongoStore(config.business_id, config.mongo_uri, config.mongo_database))
        await repository.initialize()
        if config.demo_mode:
            await repository.seed(DEMO_PRODUCTS)
        async with httpx.AsyncClient() as client:
            engine = classifier or (DemoClassifier() if config.llm_provider == "demo" else OpenAIClassifier(config, client))
            service = ConversationService(config, repository, engine, DemoChannel(), channel or WhatsAppChannel(config, client), adapter or IntegrationAdapter(config, client))
            app.state.store, app.state.service = repository, service
            task = asyncio.create_task(service.worker()) if run_worker else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                await repository.close()

    app = FastAPI(title="ShopSync AI UC-3", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[s.strip() for s in config.cors_origins.split(",") if s.strip()], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    bearer = HTTPBearer(auto_error=False)

    async def dashboard_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        # UC-1 boundary: replace this shared development/service token with the
        # team's verified owner/staff identity and business-scoped authorization.
        if not credentials or credentials.scheme.lower() != "bearer" or not hmac.compare_digest(credentials.credentials, config.dashboard_api_token):
            raise HTTPException(401, "Owner authentication required", headers={"WWW-Authenticate": "Bearer"})
        return config.business_id

    async def get_conversation(cid):
        c = await app.state.store.get(cid)
        if not c:
            raise HTTPException(404, "Conversation not found")
        return c

    @app.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "storage": config.storage, "llm": config.llm_provider, "channel": config.channel, "demo_mode": config.demo_mode, "agent_enabled": config.customer_agent_enabled}

    @app.get("/api/products", dependencies=[Depends(dashboard_auth)])
    async def products():
        return {"items": await app.state.store.products()}

    @app.get("/api/conversations", dependencies=[Depends(dashboard_auth)])
    async def conversations():
        return {"items": [public_conversation(c, full=False) for c in await app.state.store.list()]}

    @app.get("/api/conversations/{cid}", dependencies=[Depends(dashboard_auth)])
    async def conversation(cid: str):
        return {"conversation": public_conversation(await get_conversation(cid))}

    @app.post("/api/demo/messages", dependencies=[Depends(dashboard_auth)])
    async def demo_message(message: DemoMessage):
        if not config.demo_mode:
            raise HTTPException(404, "Demo simulator is disabled")
        if not message.text.strip() or not message.customer_id.strip():
            raise HTTPException(422, "Message and customer id must not be blank")
        cid, jid = await app.state.service.receive(message.customer_id.strip(), message.text.strip(), external_id=message.external_id)
        await app.state.service.process_job(jid)
        # Flush all currently due demo replies, never a real provider call.
        for _ in range(5):
            if not await app.state.service.deliver_one():
                break
        return {"conversation": public_conversation(await get_conversation(cid))}

    @app.post("/api/conversations/{cid}/handover", dependencies=[Depends(dashboard_auth)])
    async def handover(cid: str, body: Handover):
        await get_conversation(cid)
        await app.state.store.set_mode(cid, body.mode, "Owner took over this conversation")
        return {"conversation": public_conversation(await get_conversation(cid))}

    @app.post("/api/conversations/{cid}/messages", dependencies=[Depends(dashboard_auth)])
    async def owner_message(cid: str, body: OwnerMessage):
        if not body.text.strip():
            raise HTTPException(422, "Message must not be blank")
        await app.state.service.owner_reply(await get_conversation(cid), body.text.strip(), body.external_id)
        return {"conversation": public_conversation(await get_conversation(cid))}

    @app.get("/api/webhooks/whatsapp", response_class=PlainTextResponse)
    async def verify_webhook(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"), challenge: str = Query("", alias="hub.challenge")):
        if config.channel != "whatsapp" or config.demo_mode or not config.whatsapp_connected:
            raise HTTPException(404, "WhatsApp integration is disabled")
        if mode != "subscribe" or not hmac.compare_digest(token, config.whatsapp_verify_token):
            raise HTTPException(403, "Webhook verification failed")
        return challenge

    @app.post("/api/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request):
        if config.channel != "whatsapp" or config.demo_mode or not config.whatsapp_connected:
            raise HTTPException(404, "WhatsApp integration is disabled")
        if int(request.headers.get("content-length", "0")) > 262144:
            raise HTTPException(413, "Webhook payload too large")
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > 262144:
                raise HTTPException(413, "Webhook payload too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
        expected = "sha256=" + hmac.new(config.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get("x-hub-signature-256", ""), expected):
            raise HTTPException(403, "Invalid webhook signature")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Invalid JSON payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("entry", []), list):
            raise HTTPException(400, "Webhook payload must be an object with an entry array")
        accepted = 0
        for entry in payload.get("entry", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("changes", []), list):
                raise HTTPException(400, "Invalid webhook entry")
            for change in entry.get("changes", []):
                if not isinstance(change, dict) or not isinstance(change.get("value", {}), dict):
                    raise HTTPException(400, "Invalid webhook change")
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                if not isinstance(metadata, dict) or not isinstance(value.get("messages", []), list):
                    raise HTTPException(400, "Invalid webhook message data")
                if metadata.get("phone_number_id") != config.whatsapp_phone_number_id:
                    continue
                for message in value.get("messages", []):
                    if not isinstance(message, dict):
                        raise HTTPException(400, "Invalid inbound message")
                    sender, mid = message.get("from", ""), message.get("id", "")
                    if not isinstance(sender, str) or not re.fullmatch(r"\d{7,20}", sender) or not isinstance(mid, str) or not mid:
                        continue
                    content = message.get("text", {})
                    if not isinstance(content, dict):
                        raise HTTPException(400, "Invalid text message")
                    text = content.get("body", "") if message.get("type") == "text" else "[Unsupported media message: human support needed]"
                    if not isinstance(text, str) or not text.strip():
                        continue
                    try:
                        received = datetime.fromtimestamp(int(message["timestamp"]), timezone.utc)
                    except (KeyError, ValueError, TypeError, OverflowError):
                        raise HTTPException(400, "Inbound message timestamp required")
                    received = min(received, datetime.now(timezone.utc))
                    # Durable enqueue completes before acknowledgement. Processing
                    # and sending happen in the recoverable background worker.
                    await app.state.service.receive(sender, text[:2000], channel="whatsapp", external_id=mid, received_at=received.isoformat())
                    accepted += 1
        return {"accepted": accepted}

    return app


app = create_app()
