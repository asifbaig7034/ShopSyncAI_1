"""UC-1 owner signup and login for the local single-brand prototype."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status

from ...application.services.account_service import (
    SignupUnavailable, authenticate_owner, confirm_password_reset, deliver_password_reset,
    issue_session, notify_password_changed, prepare_password_reset, public_owner,
    public_owner_settings, register_owner, revoke_session, update_owner_settings,
)
from ...core.config import get_settings
from ...core.security import _is_local_request, require_owner, require_real_owner
from ...infrastructure.db.mongo import database_for_request
from ...infrastructure.integrations.reset_email import smtp_configured
from ..schemas.account import (
    AuthResponse, OwnerInfo, OwnerLogin, OwnerSettingsResponse, OwnerSettingsUpdate, OwnerSignup,
    PasswordResetConfirm, PasswordResetRequest,
)


router = APIRouter(prefix="/api/account", tags=["UC-1: Owner Account"])
RESET_REQUEST_MESSAGE = "If an owner account exists for that email, check your inbox for a recovery code."


def _local_auth_only(request: Request) -> None:
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Local UC-1 account prototype only.")


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: OwnerSignup, request: Request) -> AuthResponse:
    _local_auth_only(request)
    db = database_for_request(request)
    try:
        owner = register_owner(db, payload)
    except SignupUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AuthResponse.model_validate(issue_session(db, owner))


@router.post("/login", response_model=AuthResponse)
def login(payload: OwnerLogin, request: Request) -> AuthResponse:
    _local_auth_only(request)
    db = database_for_request(request)
    owner = authenticate_owner(db, payload)
    if owner is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return AuthResponse.model_validate(issue_session(db, owner))


@router.post("/password-reset/request")
def password_reset_request(payload: PasswordResetRequest, request: Request,
                           background_tasks: BackgroundTasks) -> dict[str, str]:
    _local_auth_only(request)
    settings = get_settings()
    if not smtp_configured(settings):
        raise HTTPException(status_code=503, detail="Password recovery is unavailable; configure mail delivery.")
    db = database_for_request(request)
    prepared = prepare_password_reset(db, str(payload.email))
    if prepared is not None:
        owner_id, address, code = prepared
        background_tasks.add_task(deliver_password_reset, db, settings, owner_id, address, code)
    return {"message": RESET_REQUEST_MESSAGE}


@router.post("/password-reset/confirm")
def password_reset_confirm(payload: PasswordResetConfirm, request: Request,
                           background_tasks: BackgroundTasks) -> dict[str, str]:
    _local_auth_only(request)
    if not confirm_password_reset(database_for_request(request), payload):
        raise HTTPException(status_code=400, detail="Invalid or expired recovery code. Request a new one.")
    settings = get_settings()
    if smtp_configured(settings):
        background_tasks.add_task(notify_password_changed, settings, str(payload.email))
    return {"message": "Password changed. Log in with your new password."}


@router.get("/me", response_model=OwnerInfo)
def me(owner: dict = Depends(require_owner)) -> OwnerInfo:
    return OwnerInfo.model_validate(public_owner(owner))


@router.get("/settings", response_model=OwnerSettingsResponse)
def settings(owner: dict = Depends(require_real_owner)) -> OwnerSettingsResponse:
    return OwnerSettingsResponse.model_validate(public_owner_settings(owner))


@router.patch("/settings", response_model=OwnerSettingsResponse)
def edit_settings(
    payload: OwnerSettingsUpdate,
    request: Request,
    owner: dict = Depends(require_real_owner),
) -> OwnerSettingsResponse:
    updated = update_owner_settings(database_for_request(request), owner, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="Business owner account is unavailable.")
    return OwnerSettingsResponse.model_validate(public_owner_settings(updated))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, _owner: dict = Depends(require_owner), authorization: str = Header()) -> None:
    token = authorization.split(" ", 1)[1]
    revoke_session(database_for_request(request), token)
