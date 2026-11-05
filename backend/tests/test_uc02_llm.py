"""Contract and privacy checks for the optional free-tier AI adapter."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

from backend.app.infrastructure.integrations import llm


def settings(*, key="demo-key", allow=True):
    return SimpleNamespace(
        gemini_api_key=key,
        gemini_synthetic_demo=allow,
        demo_mode=True,
        gemini_model="gemini-3.5-flash-lite",
        gemini_classify_timeout_seconds=8.0,
    )


class FakeResponse:
    def __init__(self, labels):
        self.body = io.BytesIO(json.dumps({
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{
                "type": "text", "text": json.dumps({"intents": labels})
            }]}],
        }).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, count):
        return self.body.read(count)


def test_gemini_classifies_only_known_intents_and_keeps_key_in_header(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", settings)
    requests = []

    def fake_send(request, timeout):
        requests.append(request)
        assert 0 < timeout <= 8.0
        return FakeResponse(["stock", "orders", "stock"])

    monkeypatch.setattr(llm, "urlopen", fake_send)
    labels, mode = llm.classify_owner_request("Show demo stock and orders")
    assert (labels, mode) == (["stock", "orders"], "gemini")
    assert len(requests) == 1
    payload = json.loads(requests[0].data)
    assert payload["model"] == "gemini-3.5-flash-lite"
    assert payload["input"] == "Show demo stock and orders"
    assert payload["response_format"]["mime_type"] == "application/json"
    assert payload["store"] is False
    assert "demo-key" not in requests[0].data.decode()
    assert requests[0].get_header("X-goog-api-key") == "demo-key"


def test_no_key_or_no_consent_never_contacts_provider(monkeypatch):
    monkeypatch.setattr(llm, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    monkeypatch.setattr(llm, "get_settings", lambda: settings(key=""))
    assert llm.classify_owner_request("Show stock") == (None, "unconfigured")
    monkeypatch.setattr(llm, "get_settings", lambda: settings(allow=False))
    assert llm.classify_owner_request("Show stock") == (None, "unconfigured")


def test_contact_details_are_blocked_before_external_call(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", settings)
    monkeypatch.setattr(llm, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    assert llm.classify_owner_request("Check customer maria@example.com") == (None, "blocked")
    assert llm.classify_owner_request("Review order 923001234567") == (None, "blocked")


def test_transient_provider_error_is_retried_and_persistent_failure_reported(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", settings)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(1)
        raise HTTPError(llm._URL, 503, "unavailable", {}, None)

    monkeypatch.setattr(llm, "urlopen", fail)
    assert llm.classify_owner_request("Show stock") == (None, "failed")
    assert len(attempts) == 2


def test_invalid_model_labels_never_route(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", settings)
    monkeypatch.setattr(llm, "urlopen", lambda *_args, **_kwargs: FakeResponse(["delete_all_orders"]))
    assert llm.classify_owner_request("Show orders") == (None, "failed")
