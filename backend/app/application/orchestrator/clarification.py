"""Remember incomplete owner requests across UC-2 chat messages."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_session(db, owner_id: str, conversation_id: str | None) -> dict:
    if conversation_id:
        session = db.owner_chat_sessions.find_one({"_id": conversation_id, "owner_id": owner_id})
        if session is None:
            raise ValueError("Owner conversation does not exist or is not accessible")
        return session
    session = {"_id": uuid4().hex, "owner_id": owner_id, "pending_request": None, "created_at": _now(), "updated_at": _now()}
    db.owner_chat_sessions.insert_one(session)
    return session


def message_with_context(session: dict, message: str) -> str:
    pending = session.get("pending_request")
    return f"{pending} {message}" if pending else message


def save_pending(db, session: dict, original: str) -> None:
    # Keep the first incomplete request instead of accumulating repeated text.
    pending = session.get("pending_request") or original
    db.owner_chat_sessions.update_one({"_id": session["_id"], "owner_id": session["owner_id"]}, {"$set": {"pending_request": pending, "updated_at": _now()}})


def clear_pending(db, session: dict) -> None:
    db.owner_chat_sessions.update_one({"_id": session["_id"], "owner_id": session["owner_id"]}, {"$set": {"pending_request": None, "updated_at": _now()}})
