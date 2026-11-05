"""Local-only Ollama intent classifier; no cloud account or API key required."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from backend.app.core.config import get_settings
from .llm import _ALLOWED, _synthetic_text, parse_intent_json


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _open_local(request: Request, timeout: float):
    # Never forward a supposedly local owner prompt via an environment proxy
    # or a redirect supplied by a misconfigured local Ollama endpoint.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def _local_endpoint(base_url: str) -> str | None:
    """Reject URLs that could forward owner requests outside this computer."""
    if not isinstance(base_url, str):
        return None
    try:
        address = urlsplit(base_url.strip())
        port = address.port
    except ValueError:
        return None
    if (
        address.scheme != "http"
        or address.hostname not in {"127.0.0.1", "localhost", "::1"}
        or address.username
        or address.password
        or address.path not in {"", "/"}
        or address.query
        or address.fragment
    ):
        return None
    host = f"[{address.hostname}]" if address.hostname == "::1" else address.hostname
    return f"http://{host}:{port or 11434}/api/chat"


def classify_local_request(message: str, settings=None) -> tuple[list[str] | None, str]:
    """Ask the locally running model for an allowlisted intent list.

    Only validated, completed responses count as AI classifications. Model
    errors never become invented results; the coordinator reports a fallback.
    """
    settings = settings or get_settings()
    if not settings.demo_mode:
        return None, "unconfigured"
    endpoint = _local_endpoint(settings.ollama_base_url)
    if endpoint is None:
        return None, "failed"
    cleaned = _synthetic_text(message)
    if cleaned is None:
        return None, "blocked"

    schema = {
        "type": "object",
        "properties": {
            "intents": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(_ALLOWED)},
            }
        },
        "required": ["intents"],
        "additionalProperties": False,
    }
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Classify a fictional shop owner's request. Return JSON only, with an intents "
                    "array containing one or more of: products, stock, orders, risk, unsupported. "
                    "Use all matching labels, without repeating them. For unrelated requests use "
                    "unsupported. Never answer the user's question or invent product facts. "
                    "Use the schema: " + json.dumps(schema, separators=(",", ":"))
                ),
            },
            {"role": "user", "content": cleaned},
        ],
        "format": schema,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 128},
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _open_local(request, timeout=settings.ollama_timeout_seconds) as response:
            raw = json.loads(response.read(65_536))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
        return None, "failed"
    if not isinstance(raw, dict) or raw.get("done") is not True:
        return None, "failed"
    body = raw.get("message")
    intents = parse_intent_json(body.get("content") if isinstance(body, dict) else None)
    return (intents, "ollama") if intents else (None, "failed")
