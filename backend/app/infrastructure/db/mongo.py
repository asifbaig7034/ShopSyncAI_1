"""One shared data layer for UC-2 and the eventual domain agents.

The in-memory Mongo-compatible database is only an explicitly labelled local
demo. A configured MongoDB URI is required for durable chat history.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import Request

from ...core.config import get_settings
from .indexes import ensure_indexes


@lru_cache(maxsize=1)
def get_database() -> Any:
    settings = get_settings()
    if settings.mongodb_uri:
        from pymongo import MongoClient

        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        database = client[settings.mongodb_db]
        ensure_indexes(database)
        return database
    if not settings.demo_mode:
        raise RuntimeError("MONGODB_URI is required outside demo mode.")

    import mongomock

    database = mongomock.MongoClient()[settings.mongodb_db]
    ensure_indexes(database)
    # A separately run seed command cannot persist in another process's memory.
    # Seed ONLY this disposable local demo database when first constructed.
    from .repositories import seed_demo

    seed_demo(database)
    return database


def database_for_request(request: Request) -> Any:
    """Use dependency-injected DB in tests; otherwise use the configured store."""
    database = getattr(request.app.state, "database", None)
    return database if database is not None else get_database()
