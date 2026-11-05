"""Isolated real HTTP contract tests against a disposable Mongo-compatible DB."""

from __future__ import annotations

import mongomock
import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.infrastructure.db.indexes import ensure_indexes
from backend.app.infrastructure.db.repositories import seed_demo
from backend.app.main import create_app


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("SHOPSYNC_DEMO_OWNER_TOKEN", "test-owner-token-only-for-local-testing")
    # Override any real .env value: tests must never make paid or unpaid API calls.
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_SYNTHETIC_DEMO", "false")
    get_settings.cache_clear()
    db = mongomock.MongoClient()["shopsync_uc2_tests"]
    ensure_indexes(db)
    sample = seed_demo(db)
    with TestClient(create_app(db)) as client:
        yield client, db, sample, {"Authorization": "Bearer test-owner-token-only-for-local-testing"}
    get_settings.cache_clear()
