"""Owner-only endpoints; orchestration remains in the application layer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...application.orchestrator.coordinator import process_owner_request
from ...core.security import require_demo_owner
from ...infrastructure.db.mongo import database_for_request
from ...infrastructure.db.repositories import history_for_owner
from ..schemas.chat import OwnerChatHistoryResponse, OwnerChatRequest, OwnerChatResponse


router = APIRouter(prefix="/api/owner/chat", tags=["UC-2: Owner Orchestrator Chat"])


@router.post("", response_model=OwnerChatResponse)
def send_owner_request(
    payload: OwnerChatRequest,
    request: Request,
    owner: dict = Depends(require_demo_owner),
) -> OwnerChatResponse:
    if not any(owner.get("agent_settings", {}).values()):
        raise HTTPException(status_code=409, detail="Enable at least one agent before sending a request.")
    try:
        response = process_owner_request(
            database_for_request(request),
            str(owner["_id"]),
            payload.message,
            payload.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OwnerChatResponse.model_validate(response)


@router.get("/history", response_model=OwnerChatHistoryResponse)
def read_owner_history(
    request: Request,
    conversation_id: str = Query(min_length=1, max_length=100),
    owner: dict = Depends(require_demo_owner),
) -> OwnerChatHistoryResponse:
    rows = history_for_owner(database_for_request(request), str(owner["_id"]), conversation_id)
    return OwnerChatHistoryResponse(conversation_id=conversation_id, messages=rows)
