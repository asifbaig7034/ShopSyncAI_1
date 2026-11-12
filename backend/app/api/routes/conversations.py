"""UC-3 owner inbox and local customer-chat simulator."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...application.services import customer_service as conversations
from ...core.config import get_settings
from ...core.security import require_real_owner
from ...infrastructure.db.mongo import database_for_request
from ..schemas.conversations import DemoCustomerMessage, HandoverRequest, OwnerConversationMessage


router = APIRouter(tags=["UC-3: Customer Conversations"])


def _raise_conversation_error(exc: conversations.ConversationError):
    if isinstance(exc, conversations.ConversationNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/conversations")
def inbox(request: Request, owner: dict = Depends(require_real_owner)) -> dict:
    return {"items": conversations.list_conversations(database_for_request(request), owner)}


@router.get("/api/conversations/{conversation_id}")
def thread(conversation_id: str, request: Request, owner: dict = Depends(require_real_owner)) -> dict:
    try:
        return {"conversation": conversations.get_conversation(database_for_request(request), owner, conversation_id)}
    except conversations.ConversationError as exc:
        _raise_conversation_error(exc)


@router.post("/api/demo/messages")
def simulate(payload: DemoCustomerMessage, request: Request,
             owner: dict = Depends(require_real_owner)) -> dict:
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="Local customer simulator is disabled")
    try:
        conversation = conversations.receive_demo_message(
            database_for_request(request), owner, payload.customer_id,
            payload.text, payload.external_id,
        )
    except conversations.ConversationError as exc:
        _raise_conversation_error(exc)
    return {"conversation": conversation}


@router.post("/api/conversations/{conversation_id}/handover")
def handover(conversation_id: str, payload: HandoverRequest, request: Request,
             owner: dict = Depends(require_real_owner)) -> dict:
    try:
        return {"conversation": conversations.change_mode(
            database_for_request(request), owner, conversation_id, payload.mode)}
    except conversations.ConversationError as exc:
        _raise_conversation_error(exc)


@router.post("/api/conversations/{conversation_id}/messages")
def send_owner_message(conversation_id: str, payload: OwnerConversationMessage, request: Request,
                       owner: dict = Depends(require_real_owner)) -> dict:
    try:
        return {"conversation": conversations.owner_reply(
            database_for_request(request), owner, conversation_id, payload.text, payload.external_id)}
    except conversations.ConversationError as exc:
        _raise_conversation_error(exc)
