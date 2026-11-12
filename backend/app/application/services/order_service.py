"""UC-4 single-brand order service boundary.

Order and product writes share one SQLite transaction engine. Conversation
handlers must pass a validated real UC-1 owner, not an ID from client input.
This local prototype uses a separate store from the Mongo owner inbox.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from types import ModuleType

from ...core.config import get_settings
from .order_engine import db, service


OrderError = service.OrderError
_initialization_lock = Lock()
_initialized_paths: set[Path] = set()


def ensure_order_engine() -> None:
    """Initialize the configured ledger once per path, including in tests.

    Each operation still uses a fresh SQLite connection; this guard only
    creates the schema and recovers an interrupted outbox lease.
    """
    path = db.database_path().expanduser().resolve()
    with _initialization_lock:
        if path not in _initialized_paths or not path.is_file():
            db.initialize()
            _initialized_paths.add(path)


def service_for_owner(owner: dict) -> ModuleType:
    """Expose UC-4 operations to a verified real owner of this single brand."""
    if owner.get("role") != "owner" or owner.get("demo") is not False:
        raise PermissionError("A real signed-in brand owner is required for UC-4.")
    ensure_order_engine()
    return service


def list_products(owner: dict) -> list[dict]:
    return service_for_owner(owner).product_list()


def process_customer_command(
    owner: dict, message_id: str, phone: str, text: str, name: str = ""
) -> str:
    """Run the verified UC-4 command interpreter for a simulated customer.

    The caller, usually UC-3, must first validate the owner's UC-1 session
    and supply a stable message ID. No live WhatsApp sender is enabled here.
    """
    if not get_settings().demo_mode:
        raise OrderError("Local customer simulator is disabled.", 404)
    if not message_id or len(message_id) > 100 or len(text) > 2000:
        raise OrderError("Invalid customer command reference or text.", 400)
    return service_for_owner(owner).chat(message_id, phone, text, name, live=False)
