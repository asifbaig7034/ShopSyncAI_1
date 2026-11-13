"""ShopSync AI application and real UC-4 to UC-2 event coordination."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes.demo_events import router as demo_events_router
from .api.routes.agent_events import router as agent_events_router
from .api.routes.account import router as account_router
from .api.routes.integrations import router as integrations_router
from .api.routes.owner_chat import router as owner_chat_router
from .api.routes.conversations import router as conversations_router
from .api.routes.whatsapp_webhook import router as whatsapp_webhook_router
from .api.routes.products import router as products_router
from .api.routes.orders import router as orders_router
from .core.config import get_settings
from .application.events.order_bridge import drain_order_events
from .infrastructure.db.mongo import get_database


logger = logging.getLogger(__name__)


async def _coordinate_real_orders() -> None:
    """Retry committed SQLite events without blocking order writes or requests."""
    while True:
        try:
            await asyncio.to_thread(drain_order_events, get_database())
        except Exception as exc:
            logger.warning("Agent event poll deferred: %s", type(exc).__name__)
        await asyncio.sleep(2)


def create_app(database: Any = None) -> FastAPI:
    """Allow a test database to be injected without changing the deployed route."""
    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        # Injected test databases have no background worker; tests call the
        # bridge explicitly against their disposable SQLite/Mongo fixtures.
        poller = asyncio.create_task(_coordinate_real_orders()) if database is None else None
        try:
            yield
        finally:
            if poller is not None:
                poller.cancel()
                with suppress(asyncio.CancelledError):
                    await poller

    application = FastAPI(title="ShopSync AI", version="0.1.0", lifespan=lifespan)
    application.state.database = database
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.include_router(account_router)
    application.include_router(integrations_router)
    application.include_router(owner_chat_router)
    application.include_router(demo_events_router)
    application.include_router(agent_events_router)
    application.include_router(products_router)
    application.include_router(orders_router)
    application.include_router(conversations_router)
    application.include_router(whatsapp_webhook_router)

    @application.get("/api/health", tags=["System"])
    def health() -> dict:
        return {"status": "ok", "local_demo": get_settings().demo_mode}

    return application


app = create_app()
