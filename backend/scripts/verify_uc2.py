"""Check the running local UC-2 demo with fictional data only.

Run from the repository root after starting FastAPI::

    python -m backend.scripts.verify_uc2

The check does not create an order, approve a refund, or send a customer message.
It does record one owner chat and a repeatable synthetic low-stock event.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from backend.app.core.config import get_settings


class VerificationError(Exception):
    """A missing prerequisite or a failed UC-2 acceptance check."""


def _request_json(
    base_url: str, path: str, token: str | None = None,
    body: dict | None = None, timeout: float = 15,
) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(base_url + path, headers=headers, data=data, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except HTTPError as error:
        # The backend should return an ordinary validation/authorization detail.
        try:
            detail = json.loads(error.read(2048)).get("detail", "")
        except (ValueError, AttributeError, UnicodeDecodeError):
            detail = ""
        suffix = f": {str(detail)[:200]}" if detail else ""
        raise VerificationError(f"{path} returned HTTP {error.code}{suffix}".replace(token or "\0", "[redacted]")) from None
    except (URLError, OSError, TimeoutError) as error:
        raise VerificationError(
            f"Cannot reach {base_url}. Start FastAPI on 127.0.0.1:8000 and check the .env configuration. "
            f"Network error: {type(error).__name__}."
        ) from None
    except (ValueError, UnicodeDecodeError) as error:
        raise VerificationError(f"{path} returned invalid JSON ({type(error).__name__}).") from None
    if not isinstance(value, dict):
        raise VerificationError(f"{path} did not return a JSON object.")
    return value


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ShopSync AI UC-2 against a running local backend.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Local FastAPI address (default: %(default)s)")
    parser.add_argument("--require-gemini", action="store_true", help="Fail unless the request was classified by live Gemini AI.")
    parser.add_argument("--require-llm", action="store_true", help="Fail unless the configured Gemini or local Ollama model classified the request.")
    args = parser.parse_args()
    url = args.base_url.rstrip("/")
    address = urlsplit(url)
    if address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost", "::1"} or address.username or address.password or address.path or address.query or address.fragment:
        print("FAIL: Only a local http://127.0.0.1 (or localhost) backend is allowed; never send a demo token to a remote URL.", file=sys.stderr)
        return 1

    try:
        settings = get_settings()
        token = settings.shopsync_demo_owner_token.strip()
        _check(settings.demo_mode, "DEMO_MODE must be true for the local owner-token flow.")
        _check(len(token) >= 16 and not token.startswith("CHANGE_ME"), "Set a long private SHOPSYNC_DEMO_OWNER_TOKEN in the project-root .env file, then restart FastAPI.")
        health = _request_json(url, "/api/health")
        _check(health.get("status") == "ok" and health.get("local_demo") is True, "Backend is reachable but local UC-2 demo mode is not ready.")
        print("PASS Backend responds on the local API address.")

        prompt = "Show products, stock and recent orders"
        provider = settings.llm_provider
        if args.require_gemini:
            _check(provider == "gemini", "LLM_PROVIDER=ollama is selected. Use --require-llm to verify local Ollama instead.")
        chat_timeout = max(15, settings.ollama_timeout_seconds + 5) if provider == "ollama" else 15
        chat = _request_json(url, "/api/owner/chat", token, {"message": prompt}, timeout=chat_timeout)
        expected_agents = {"customer_commerce", "inventory_logistics"}
        _check(expected_agents.issubset(set(chat.get("routed_agents") or [])), "Combined request did not route to both demo agents. Check synthetic seed data and agent settings.")
        _check("Cotton Shirt" in chat.get("reply", "") and "DEMO-1001" in chat.get("reply", ""), "Combined reply is missing the saved synthetic product or order. Run python -m backend.scripts.seed_demo for MongoDB.")
        _check(chat.get("conversation_id"), "Owner chat did not return a conversation ID.")
        print("PASS One owner request routed to both demo agents; combined reply uses saved sample data.")

        mode = chat.get("mode")
        require_ai = (
            args.require_llm or args.require_gemini or provider == "ollama"
            or bool(settings.gemini_api_key and settings.gemini_synthetic_demo)
        )
        if require_ai:
            warnings = "; ".join(chat.get("warnings") or [])
            _check(
                mode == provider,
                f"{provider} was selected/requested but backend used {mode!r}. {warnings} "
                + ("Check docker compose ps, the Ollama model pull and backend restart."
                   if provider == "ollama" else
                   "Check Gemini key, project permissions, model and quota."),
            )
            print(f"PASS {provider} classified this fictional owner request.")
        else:
            print("DEMO ONLY No live LLM checked: configure local Ollama or a working Gemini key; then re-run with --require-llm.")

        query = urlencode({"conversation_id": chat["conversation_id"]})
        history = _request_json(url, "/api/owner/chat/history?" + query, token)
        _check(any(row.get("request") == prompt for row in history.get("messages", [])), "The owner chat is not present in its saved conversation history.")
        print("PASS Chat entry is present in the conversation history.")

        first = _request_json(url, "/api/owner/demo-events/low-stock", token, {})
        second = _request_json(url, "/api/owner/demo-events/low-stock", token, {})
        _check(bool(first.get("event_id")) and first.get("event_id") == second.get("event_id") and second.get("created") is False, "Low-stock event was not deduplicated across repeated requests.")
        event_list = _request_json(url, "/api/owner/demo-events", token)
        _check(any(event.get("event_id") == first["event_id"] and event.get("status") == "coordinated" and event.get("agent_responses") for event in event_list.get("events", [])), "No acknowledged cross-agent low-stock event was saved.")
        print("PASS Synthetic low-stock event reached another agent, was acknowledged, and did not duplicate.")
        if not settings.mongodb_uri:
            print("NOTE MongoDB URI is empty: history and events will reset when the backend restarts.")
        else:
            print("NOTE MongoDB is configured: restart FastAPI and open the same conversation to verify persistence.")
        print("UC-2 local verification finished successfully. No order, stock, or refund was changed.")
        return 0
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
