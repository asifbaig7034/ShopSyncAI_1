"""UC-4 owner order lifecycle; customer-facing commands remain in UC-3.

All endpoints use a UC-1 real owner session. No separate password, live
WhatsApp webhook or background sender is exposed by this router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...application.services import order_service
from ...core.config import get_settings
from ...core.security import require_real_owner
from ..schemas.orders import ChatIn, DecisionIn, DraftIn, EditDraftIn, InspectionIn, ReturnIn


router = APIRouter(tags=["UC-4: Manage Orders"])


def _call(owner: dict, operation: str, *args, **kwargs):
    try:
        engine = order_service.service_for_owner(owner)
        return getattr(engine, operation)(*args, **kwargs)
    except order_service.OrderError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.get("/api/orders")
def orders(owner: dict = Depends(require_real_owner)) -> list[dict]:
    return _call(owner, "list_orders")


@router.post("/api/orders/drafts", status_code=201)
def draft(payload: DraftIn, owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "create_draft", **payload.model_dump())


@router.get("/api/orders/{order_id}")
def order(order_id: str, owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "get_order", order_id)


@router.patch("/api/orders/{order_id}/draft")
def edit_draft(order_id: str, payload: EditDraftIn,
               owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "edit_draft", order_id,
                 [item.model_dump() for item in payload.items],
                 payload.delivery_address, payload.payment_method)


@router.post("/api/orders/{order_id}/confirm")
def confirm(order_id: str, owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "confirm", order_id)


@router.post("/api/orders/{order_id}/risk-decision")
def risk_decision(order_id: str, payload: DecisionIn,
                  owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "decide_risk", order_id, payload.approve, payload.reason)


@router.post("/api/orders/{order_id}/cancel")
def cancel(order_id: str, owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "cancel", order_id)


@router.post("/api/orders/{order_id}/ship")
def ship(order_id: str, owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "mark_shipped", order_id)


@router.post("/api/orders/{order_id}/returns", status_code=201)
def return_request(order_id: str, payload: ReturnIn,
                   owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "request_return", order_id, payload.kind, payload.reason)


@router.post("/api/returns/{request_id}/decision")
def return_decision(request_id: str, payload: DecisionIn,
                    owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "decide_return", request_id, payload.approve, payload.reason)


@router.post("/api/returns/{request_id}/inspect")
def inspect_return(request_id: str, payload: InspectionIn,
                   owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "receive_return", request_id, payload.accept, payload.reason)


@router.post("/api/chat/simulate")
def simulate(payload: ChatIn, owner: dict = Depends(require_real_owner)) -> dict:
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404, detail="Local customer simulator is disabled.")
    try:
        reply = order_service.process_customer_command(
            owner, payload.message_id, payload.phone, payload.text, payload.name
        )
    except order_service.OrderError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return {"reply": reply}


@router.get("/api/finance")
def finance(owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "financial_summary")


@router.get("/api/audit")
def audit(owner: dict = Depends(require_real_owner)) -> list[dict]:
    return _call(owner, "audit_log")
