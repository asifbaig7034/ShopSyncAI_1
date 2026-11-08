"""UC-1 local owner accounts with revocable, expiring sessions."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pwdlib import PasswordHash
from pymongo.errors import DuplicateKeyError

from ...api.schemas.account import OwnerLogin, OwnerSettingsUpdate, OwnerSignup, PasswordResetConfirm
from ...core.config import Settings
from ...infrastructure.integrations.reset_email import send_password_changed_email, send_password_reset_email


PASSWORD_HASH = PasswordHash.recommended()
DUMMY_HASH = PASSWORD_HASH.hash("dummy-password-for-timing-only")
SESSION_HOURS = 12
RESET_MINUTES = 15
RESET_COOLDOWN_SECONDS = 60


class SignupUnavailable(Exception):
    """The one-brand owner account already exists."""


def public_owner(owner: dict) -> dict:
    return {
        "id": str(owner["_id"]), "name": owner["name"],
        "email": owner["email"], "business_name": owner["business_profile"]["name"],
        "role": owner.get("role", "owner"),
    }


def public_owner_settings(owner: dict) -> dict:
    profile = owner.get("business_profile") or {}
    switches = owner.get("agent_settings") or {}
    return {
        "business_profile": {
            "name": profile["name"],
            "contact": profile.get("contact", ""),
            "currency": profile.get("currency", "PKR"),
            "language": profile.get("language", "English"),
        },
        "agent_settings": {
            "customer_commerce": switches.get("customer_commerce", False),
            "inventory_logistics": switches.get("inventory_logistics", False),
        },
    }


def update_owner_settings(db, owner: dict, payload: OwnerSettingsUpdate) -> dict | None:
    """Change only allowlisted fields on an actual owner in one Mongo write."""
    updates = {}
    if payload.business_profile is not None:
        updates.update({f"business_profile.{key}": value
                        for key, value in payload.business_profile.model_dump(exclude_unset=True).items()})
    if payload.agent_settings is not None:
        updates.update({f"agent_settings.{key}": value
                        for key, value in payload.agent_settings.model_dump(exclude_unset=True).items()})
    owner_filter = {"_id": owner["_id"], "role": "owner", "demo": False}
    if not db.owners.update_one(owner_filter, {"$set": updates}).matched_count:
        return None
    return db.owners.find_one(owner_filter)


def register_owner(db, payload: OwnerSignup) -> dict:
    if db.owners.count_documents({"role": "owner", "demo": False}, limit=1):
        raise SignupUnavailable("An owner account already exists for this brand.")
    owner = {
        "name": payload.name.strip(),
        "email": str(payload.email).lower(),
        "role": "owner",
        "singleton_key": "primary_owner",
        "password_hash": PASSWORD_HASH.hash(payload.password),
        "business_profile": {"name": payload.business_name.strip(), "contact": "", "currency": "PKR", "language": "English"},
        "agent_settings": {"customer_commerce": True, "inventory_logistics": True},
        "demo": False,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        owner["_id"] = db.owners.insert_one(owner).inserted_id
    except DuplicateKeyError as exc:
        raise SignupUnavailable("An owner account already exists for this brand.") from exc
    return owner


def authenticate_owner(db, payload: OwnerLogin) -> dict | None:
    owner = db.owners.find_one({"email": str(payload.email).lower(), "role": "owner", "demo": False})
    encoded = owner.get("password_hash") if owner else None
    # A nonexistent account still runs a password verification; do not expose
    # whether an email is registered through a different error message.
    if not PASSWORD_HASH.verify(payload.password, encoded or DUMMY_HASH) or not encoded:
        return None
    return owner


def issue_session(db, owner: dict) -> dict:
    token = secrets.token_urlsafe(32)
    db.owner_sessions.insert_one({
        "token_hash": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "owner_id": str(owner["_id"]),
        "credential_version": owner.get("credential_version", 0),
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS),
    })
    return {"access_token": token, "token_type": "bearer", "owner": public_owner(owner)}


def owner_for_session(db, token: str) -> dict | None:
    if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
        return None
    session = db.owner_sessions.find_one({"token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()})
    if not session:
        return None
    expires_at = session["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return None
    owner_id = session.get("owner_id", "")
    if not ObjectId.is_valid(owner_id):
        return None
    owner = db.owners.find_one({"_id": ObjectId(owner_id), "role": "owner", "demo": False})
    if owner is None or session.get("credential_version", 0) != owner.get("credential_version", 0):
        return None
    return owner


def revoke_session(db, token: str) -> None:
    db.owner_sessions.delete_one({"token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()})


def prepare_password_reset(db, email: str) -> tuple[ObjectId, str, str] | None:
    """Rate-limit per owner, store only a digest, and return code for email delivery."""
    owner = db.owners.find_one({"email": email.lower(), "role": "owner", "demo": False}, {"_id": 1, "email": 1})
    if owner is None:
        return None
    now = datetime.now(timezone.utc)
    code = secrets.token_urlsafe(32)
    code_hash = hashlib.sha256(code.encode("ascii")).hexdigest()
    result = db.owners.update_one({
        "_id": owner["_id"], "role": "owner", "demo": False,
        "$or": [
            {"password_reset_requested_at": {"$exists": False}},
            {"password_reset_requested_at": {"$lte": now - timedelta(seconds=RESET_COOLDOWN_SECONDS)}},
        ],
    }, {"$set": {
        "password_reset": {
            "code_hash": code_hash,
            "expires_at": now + timedelta(minutes=RESET_MINUTES),
        },
        "password_reset_requested_at": now,
    }})
    if not result.matched_count:
        return None
    return owner["_id"], owner["email"], code


def deliver_password_reset(db, settings: Settings, owner_id: ObjectId, email: str, code: str) -> None:
    """A failed email cannot leave a valid undisclosed code in the database."""
    try:
        send_password_reset_email(settings, email, code)
    except Exception:
        db.owners.update_one({
            "_id": owner_id,
            "password_reset.code_hash": hashlib.sha256(code.encode("ascii")).hexdigest(),
        }, {"$unset": {"password_reset": ""}})
        # Neither SMTP exception details nor the reset code are logged.


def confirm_password_reset(db, payload: PasswordResetConfirm) -> bool:
    """Change password and consume code in one conditional owner-document update."""
    now = datetime.now(timezone.utc)
    owner_filter = {
        "email": str(payload.email).lower(), "role": "owner", "demo": False,
        "password_reset.code_hash": hashlib.sha256(payload.code.encode("utf-8")).hexdigest(),
        "password_reset.expires_at": {"$gt": now},
    }
    # Hash before the conditional write so the code cannot be consumed if
    # Argon2 fails; concurrent submissions can only update this document once.
    result = db.owners.update_one(owner_filter, {
        "$set": {"password_hash": PASSWORD_HASH.hash(payload.password)},
        "$inc": {"credential_version": 1},
        "$unset": {"password_reset": ""},
    })
    if not result.matched_count:
        return False

    owner = db.owners.find_one({"email": str(payload.email).lower(), "role": "owner", "demo": False},
                               {"_id": 1, "credential_version": 1})
    if owner is not None:
        try:
            db.owner_sessions.delete_many({
                "owner_id": str(owner["_id"]),
                "$or": [
                    {"credential_version": {"$lt": owner["credential_version"]}},
                    {"credential_version": {"$exists": False}},
                ],
            })
        except Exception:
            # Even if cleanup fails, owner_for_session rejects old versions.
            pass
    return True


def notify_password_changed(settings: Settings, email: str) -> None:
    try:
        send_password_changed_email(settings, email)
    except Exception:
        # Notification delivery must not undo a successful credential change.
        pass
