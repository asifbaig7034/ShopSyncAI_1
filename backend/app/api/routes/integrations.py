"""UC-1: owner-managed WhatsApp verification, without messaging or webhooks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...application.services.integration_service import (
    EncryptionUnavailable, NoWhatsAppConnection, connect_whatsapp,
    disconnect_whatsapp, verify_whatsapp, whatsapp_status,
)
from ...core.config import get_settings
from ...core.security import require_real_owner
from ...infrastructure.db.mongo import database_for_request
from ...infrastructure.integrations.whatsapp import (
    MetaAccessDenied, MetaUnavailable, PhoneNumberNotFound,
)
from ..schemas.integrations import WhatsAppConnectRequest, WhatsAppStatus


router = APIRouter(prefix="/api/integrations/whatsapp", tags=["UC-1: Integrations"])


def _owner_id(owner: dict) -> str:
    return str(owner["_id"])


@router.get("", response_model=WhatsAppStatus)
def status(request: Request, owner: dict = Depends(require_real_owner)) -> WhatsAppStatus:
    result = whatsapp_status(database_for_request(request), _owner_id(owner), get_settings())
    return WhatsAppStatus.model_validate(result)


@router.post("/connect", response_model=WhatsAppStatus)
def connect(payload: WhatsAppConnectRequest, request: Request,
            owner: dict = Depends(require_real_owner)) -> WhatsAppStatus:
    access_token = payload.access_token.get_secret_value().strip()
    if len(access_token) < 20 or len(access_token) > 4096 or any(ch.isspace() for ch in access_token):
        raise HTTPException(status_code=422, detail="Enter a valid Meta access token.")
    try:
        result = connect_whatsapp(
            database_for_request(request), _owner_id(owner), get_settings(),
            waba_id=payload.waba_id, phone_number_id=payload.phone_number_id,
            access_token=access_token,
        )
    except EncryptionUnavailable as exc:
        raise HTTPException(status_code=503, detail="Configure the server integration encryption key.") from exc
    except (MetaAccessDenied, PhoneNumberNotFound) as exc:
        raise HTTPException(status_code=400,
                            detail="Meta could not confirm this WABA, token and phone number.") from exc
    except MetaUnavailable as exc:
        raise HTTPException(status_code=502,
                            detail="Could not reach Meta for verification. No credentials were saved.") from exc
    return WhatsAppStatus.model_validate(result)


@router.post("/verify", response_model=WhatsAppStatus)
def verify(request: Request, owner: dict = Depends(require_real_owner)) -> WhatsAppStatus:
    try:
        result = verify_whatsapp(database_for_request(request), _owner_id(owner), get_settings())
    except EncryptionUnavailable as exc:
        raise HTTPException(status_code=503, detail="Configure the server integration encryption key.") from exc
    except NoWhatsAppConnection as exc:
        raise HTTPException(status_code=404, detail="No saved WhatsApp connection to verify.") from exc
    return WhatsAppStatus.model_validate(result)


@router.delete("", response_model=WhatsAppStatus)
def disconnect(request: Request, owner: dict = Depends(require_real_owner)) -> WhatsAppStatus:
    return WhatsAppStatus.model_validate(disconnect_whatsapp(database_for_request(request), _owner_id(owner)))
