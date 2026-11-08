"""Indexes for owner chat history, pending approvals, and event deduplication."""

from __future__ import annotations

from typing import Any


def ensure_indexes(db: Any) -> None:
    db.owners.create_index("email", unique=True)
    db.owners.create_index("singleton_key", unique=True, sparse=True)
    db.owner_sessions.create_index("token_hash", unique=True)
    db.owner_sessions.create_index("expires_at", expireAfterSeconds=0)
    db.integration_connections.create_index([("owner_id", 1), ("channel", 1)], unique=True)
    db.agent_runs.create_index([("owner_id", 1), ("conversation_id", 1), ("created_at", 1)])
    # The conversation ID itself is stored in MongoDB's already-unique _id.
    db.owner_chat_sessions.create_index("owner_id")
    db.system_events.create_index("event_id", unique=True, sparse=True)
    db.approval_requests.create_index([("owner_id", 1), ("status", 1)])
    db.approval_requests.create_index("request_key", unique=True, sparse=True)
