import asyncio
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import service
from .db import initialize
from .whatsapp import list_outbox, outbox_worker, retry_message, send_due


@asynccontextmanager
async def lifespan(app):
    if not os.getenv("SHOPSYNC_OWNER_TOKEN"):
        raise RuntimeError("Set SHOPSYNC_OWNER_TOKEN before starting the app.")
    initialize()
    task = asyncio.create_task(outbox_worker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="ShopSync AI UC-4 Orders", lifespan=lifespan)
STATIC = Path(__file__).resolve().parent.parent / "static"


@app.exception_handler(service.OrderError)
async def order_error(_request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


def owner(authorization: str = Header(default="")):
    expected = os.getenv("SHOPSYNC_OWNER_TOKEN", "")
    provided = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Owner token required.")
    return True


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    price_cents: int = Field(ge=0, le=100_000_000)
    stock_qty: int = Field(ge=0, le=1_000_000)


class RestockIn(BaseModel):
    quantity: int = Field(gt=0, le=1_000_000)


class ItemIn(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=100)


class DraftIn(BaseModel):
    phone: str
    name: str = Field(default="", max_length=100)
    items: list[ItemIn]
    delivery_address: str
    payment_method: Literal["COD", "BANK_TRANSFER"] = "COD"
    source_key: str = Field(min_length=8, max_length=100)


class EditDraftIn(BaseModel):
    items: list[ItemIn]
    delivery_address: str


class DecisionIn(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=500)


class ReturnIn(BaseModel):
    kind: Literal["RETURN", "REFUND"]
    reason: str = Field(min_length=1, max_length=500)


class InspectionIn(BaseModel):
    accept: bool


class ChatIn(BaseModel):
    message_id: str = Field(min_length=8, max_length=100)
    phone: str
    name: str = Field(default="", max_length=100)
    text: str = Field(min_length=1, max_length=2000)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/app.js")
def script():
    return FileResponse(STATIC / "app.js", media_type="text/javascript")


@app.get("/style.css")
def style():
    return FileResponse(STATIC / "style.css", media_type="text/css")


@app.get("/api/health")
def health():
    return {"status": "ok", "module": "UC-4"}


@app.get("/api/products", dependencies=[Depends(owner)])
def products():
    return service.product_list()


@app.post("/api/products", dependencies=[Depends(owner)], status_code=201)
def product(payload: ProductIn):
    return service.add_product(**payload.model_dump())


@app.post("/api/products/{product_id}/restock", dependencies=[Depends(owner)])
def restock(product_id: str, payload: RestockIn):
    return service.restock_product(product_id, payload.quantity)


@app.get("/api/orders", dependencies=[Depends(owner)])
def orders():
    return service.list_orders()


@app.post("/api/orders/drafts", dependencies=[Depends(owner)], status_code=201)
def draft(payload: DraftIn):
    data = payload.model_dump()
    return service.create_draft(**data)


@app.get("/api/orders/{order_id}", dependencies=[Depends(owner)])
def order(order_id: str):
    return service.get_order(order_id)


@app.patch("/api/orders/{order_id}/draft", dependencies=[Depends(owner)])
def edit_draft(order_id: str, payload: EditDraftIn):
    return service.edit_draft(order_id, [i.model_dump() for i in payload.items], payload.delivery_address)


@app.post("/api/orders/{order_id}/confirm", dependencies=[Depends(owner)])
def confirm(order_id: str):
    return service.confirm(order_id)


@app.post("/api/orders/{order_id}/risk-decision", dependencies=[Depends(owner)])
def risk_decision(order_id: str, payload: DecisionIn):
    return service.decide_risk(order_id, payload.approve, payload.reason)


@app.post("/api/orders/{order_id}/cancel", dependencies=[Depends(owner)])
def cancel(order_id: str):
    return service.cancel(order_id)


@app.post("/api/orders/{order_id}/ship", dependencies=[Depends(owner)])
def ship(order_id: str):
    return service.mark_shipped(order_id)


@app.post("/api/orders/{order_id}/returns", dependencies=[Depends(owner)], status_code=201)
def return_request(order_id: str, payload: ReturnIn):
    return service.request_return(order_id, payload.kind, payload.reason)


@app.post("/api/returns/{request_id}/decision", dependencies=[Depends(owner)])
def return_decision(request_id: str, payload: DecisionIn):
    return service.decide_return(request_id, payload.approve, payload.reason)


@app.post("/api/returns/{request_id}/inspect", dependencies=[Depends(owner)])
def inspect_return(request_id: str, payload: InspectionIn):
    return service.receive_return(request_id, payload.accept)


@app.post("/api/chat/simulate", dependencies=[Depends(owner)])
def simulate(payload: ChatIn):
    return {"reply": service.chat(payload.message_id, payload.phone, payload.text, payload.name)}


@app.get("/api/finance", dependencies=[Depends(owner)])
def finance():
    return service.financial_summary()


@app.get("/api/audit", dependencies=[Depends(owner)])
def audit():
    return service.audit_log()


@app.get("/api/notifications", dependencies=[Depends(owner)])
def notifications():
    return list_outbox()


@app.post("/api/notifications/{message_id}/retry", dependencies=[Depends(owner)])
async def retry_notification(message_id: str):
    if not retry_message(message_id):
        raise HTTPException(status_code=409, detail="Message cannot be retried or was not found.")
    await send_due()
    return {"notifications": list_outbox()}


@app.get("/webhooks/whatsapp")
def verify_whatsapp(request: Request):
    q = request.query_params
    secret = os.getenv("WA_VERIFY_TOKEN", "")
    if secret and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), secret):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Webhook verification failed.")


@app.post("/webhooks/whatsapp")
async def inbound_whatsapp(request: Request):
    secret = os.getenv("WA_APP_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook signing secret is not configured.")
    body = await request.body()
    if len(body) > 500_000:
        raise HTTPException(status_code=413, detail="Payload too large.")
    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Invalid webhook signature.")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    if payload.get("object") != "whatsapp_business_account":
        raise HTTPException(status_code=400, detail="Unexpected webhook object.")
    processed = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            profiles = {p.get("wa_id"): p.get("profile", {}).get("name", "")
                        for p in value.get("contacts", [])}
            for message in value.get("messages", []):
                if message.get("type") != "text" or not message.get("id"):
                    continue
                phone = message.get("from", "")
                try:
                    service.chat(message["id"], phone, message.get("text", {}).get("body", ""),
                                 profiles.get(phone, ""), live=True)
                except service.OrderError:
                    continue
                processed += 1
    # Deliver queued replies outside the order transaction. A worker also retries failures.
    asyncio.create_task(send_due())
    return {"received": True, "processed": processed}
