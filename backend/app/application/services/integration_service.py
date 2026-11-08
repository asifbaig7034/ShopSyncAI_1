"""Owner-scoped WhatsApp credentials and truthful Meta read-access status."""

from __future__ import annotations

from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from ...core.config import Settings
from ...infrastructure.integrations.whatsapp import (
    MetaVerificationError, verify_phone_number,
)


class EncryptionUnavailable(Exception):
    """The local key required for encrypting/decrypting tokens is unavailable."""


class NoWhatsAppConnection(Exception):
    """No saved verified WhatsApp credentials exist for this owner."""


def _fernet(settings: Settings) -> Fernet:
    value = settings.integration_encryption_key.get_secret_value()
    if not value:
        raise EncryptionUnavailable()
    try:
        return Fernet(value.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise EncryptionUnavailable() from exc


def _filter(owner_id: str) -> dict:
    return {"owner_id": owner_id, "channel": "whatsapp"}


def _public(record: dict | None, *, attention: bool = False) -> dict:
    if record is None:
        return {"status": "not_connected", "waba_id": None, "phone_number_id": None,
                "display_phone_number": None, "verified_name": None, "verified_at": None}
    return {
        "status": "needs_attention" if attention else record.get("status", "needs_attention"),
        "waba_id": record.get("waba_id"),
        "phone_number_id": record.get("phone_number_id"),
        "display_phone_number": record.get("display_phone_number"),
        "verified_name": record.get("verified_name"),
        "verified_at": record.get("verified_at"),
    }


def whatsapp_status(db, owner_id: str, settings: Settings) -> dict:
    record = db.integration_connections.find_one(_filter(owner_id))
    if record is None:
        return _public(None)
    try:
        _fernet(settings).decrypt(record["token_ciphertext"].encode("ascii"))
    except (EncryptionUnavailable, InvalidToken, KeyError, UnicodeError):
        return _public(record, attention=True)
    return _public(record)


def connect_whatsapp(db, owner_id: str, settings: Settings, *, waba_id: str,
                     phone_number_id: str, access_token: str) -> dict:
    cipher = _fernet(settings)
    display, name = verify_phone_number(
        version=settings.meta_graph_version, waba_id=waba_id,
        phone_number_id=phone_number_id, access_token=access_token,
    )
    # Never write plaintext credentials, even if DB writes fail. Upsert is
    # unique on (owner_id, channel); other owners and channels remain untouched.
    record = {
        **_filter(owner_id), "waba_id": waba_id, "phone_number_id": phone_number_id,
        "token_ciphertext": cipher.encrypt(access_token.encode("utf-8")).decode("ascii"),
        "display_phone_number": display, "verified_name": name,
        "status": "meta_read_verified", "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    db.integration_connections.update_one(_filter(owner_id), {"$set": record}, upsert=True)
    return _public(record)


def verify_whatsapp(db, owner_id: str, settings: Settings) -> dict:
    cipher = _fernet(settings)
    record = db.integration_connections.find_one(_filter(owner_id))
    if record is None:
        raise NoWhatsAppConnection()
    try:
        token = cipher.decrypt(record["token_ciphertext"].encode("ascii")).decode("utf-8")
    except (InvalidToken, KeyError, UnicodeError):
        db.integration_connections.update_one(
            {**_filter(owner_id), "token_ciphertext": record.get("token_ciphertext")},
            {"$set": {"status": "needs_attention"}},
        )
        return _public(record, attention=True)
    try:
        display, name = verify_phone_number(
            version=settings.meta_graph_version, waba_id=record["waba_id"],
            phone_number_id=record["phone_number_id"], access_token=token,
        )
    except MetaVerificationError:
        db.integration_connections.update_one(
            {**_filter(owner_id), "token_ciphertext": record["token_ciphertext"]},
            {"$set": {"status": "needs_attention"}},
        )
        return _public(record, attention=True)
    new_values = {"status": "meta_read_verified",
                  "verified_at": datetime.now(timezone.utc).isoformat(),
                  "display_phone_number": display, "verified_name": name}
    db.integration_connections.update_one(
        {**_filter(owner_id), "token_ciphertext": record["token_ciphertext"]},
        {"$set": new_values},
    )
    return _public({**record, **new_values})


def disconnect_whatsapp(db, owner_id: str) -> dict:
    """Delete only locally saved credentials; do not deregister with Meta."""
    db.integration_connections.delete_one(_filter(owner_id))
    return _public(None)
