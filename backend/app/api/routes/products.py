"""Single-brand catalogue backed by the UC-4 transactional order ledger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...application.services import order_service
from ...core.security import require_real_owner
from ..schemas.orders import ProductIn, RestockIn


router = APIRouter(prefix="/api/products", tags=["UC-4: Products and Stock"])


def _call(owner: dict, operation: str, *args, **kwargs):
    try:
        engine = order_service.service_for_owner(owner)
        return getattr(engine, operation)(*args, **kwargs)
    except order_service.OrderError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.get("")
def list_products(owner: dict = Depends(require_real_owner)) -> list[dict]:
    return _call(owner, "product_list")


@router.post("", status_code=201)
def add_product(payload: ProductIn, owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "add_product", **payload.model_dump())


@router.post("/{product_id}/restock")
def restock(product_id: str, payload: RestockIn,
            owner: dict = Depends(require_real_owner)) -> dict:
    return _call(owner, "restock_product", product_id, payload.quantity)
