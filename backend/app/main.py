"""ShopSync AI UC-2 prototype: one API, one owner, two demo agent domains."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes.demo_events import router as demo_events_router
from .api.routes.owner_chat import router as owner_chat_router
from .core.config import get_settings


def create_app(database: Any = None) -> FastAPI:
    """Allow a test database to be injected without changing the deployed route."""
    application = FastAPI(title="ShopSync AI: UC-2", version="0.1.0")
    application.state.database = database
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.include_router(owner_chat_router)
    application.include_router(demo_events_router)

    @application.get("/api/health", tags=["System"])
    def health() -> dict:
        return {"status": "ok", "local_demo": get_settings().demo_mode}

    return application


app = create_app()
