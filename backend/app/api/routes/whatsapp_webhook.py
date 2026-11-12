"""Reserved UC-3 webhook boundary; real Meta messaging is not configured.

UC-1 currently verifies read access to a WABA and phone number. That check
cannot authenticate incoming webhook payloads or authorize outgoing messages.
Keep the endpoint closed until an app secret, subscription verification token,
signed ingress deduplication and a real delivery worker are connected.
"""

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/api/webhooks/whatsapp", tags=["UC-3: WhatsApp Webhook"])


@router.get("")
def verify_webhook() -> None:
    raise HTTPException(status_code=503, detail="WhatsApp customer messaging is not configured")


@router.post("")
def receive_webhook() -> None:
    raise HTTPException(status_code=503, detail="WhatsApp customer messaging is not configured")
