"""Development-only owner token until UC-1 account authentication exists.

The endpoint deliberately rejects non-loopback clients and non-demo runs; this
token must never be treated as a production owner authentication mechanism.
"""

from __future__ import annotations

import hmac
import ipaddress

from fastapi import Header, HTTPException, Request, status

from .config import get_settings
from ..infrastructure.db.mongo import database_for_request


def _is_local_request(request: Request) -> bool:
    if request.client is None:
        return False
    host = request.client.host
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_demo_owner(request: Request, authorization: str | None = Header(default=None)) -> dict:
    """Find the explicitly seeded owner after validating the local demo token."""
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="UC-1 login is not implemented; local demo authentication is disabled.",
        )
    if not _is_local_request(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo owner access is local only.")
    token = settings.shopsync_demo_owner_token.strip()
    if len(token) < 16 or token.startswith("CHANGE_ME"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set SHOPSYNC_DEMO_OWNER_TOKEN to a long, private local token.",
        )
    provided = (authorization or "").split(" ", 1)
    if len(provided) != 2 or provided[0].lower() != "bearer" or not hmac.compare_digest(provided[1], token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid owner token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    db = database_for_request(request)
    owner = db.owners.find_one({"email": "owner@shopsync.local"})
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo owner missing. Run python -m backend.scripts.seed_demo.",
        )
    return owner
