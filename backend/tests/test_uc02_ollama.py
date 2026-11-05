"""UC-2 local-model acceptance and safety checks; no Ollama server is contacted."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

from backend.app.application.orchestrator import coordinator
from backend.app.infrastructure.integrations import llm, ollama


def settings(base_url: str = "http://127.0.0.1:11434") -> SimpleNamespace:
    return SimpleNamespace(
        demo_mode=True,
        ollama_base_url=base_url,
        ollama_model="qwen2.5:1.5b",
        ollama_timeout_seconds=45.0,
    )


class FakeResponse:
    def __init__(self, intents: list[str]):
        self.data = io.BytesIO(json.dumps({
            "done": True,
            "message": {"role": "assistant", "content": json.dumps({"intents": intents})},
        }).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self.data.read(limit)


def test_ollama_sends_one_structured_local_request_and_validates_intents(monkeypatch):
    requests = []

    def fake_open(request, timeout):
        requests.append(request)
        assert 0 < timeout <= 45
        return FakeResponse(["products", "orders", "products"])

    monkeypatch.setattr(ollama, "_open_local", fake_open)
    labels, source = ollama.classify_local_request("Show demo products and recent orders", settings())
    assert (labels, source) == (["products", "orders"], "ollama")
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert request.get_method() == "POST"
    assert request.get_header("X-goog-api-key") is None
    payload = json.loads(request.data)
    assert payload["model"] == "qwen2.5:1.5b"
    assert payload["stream"] is False
    assert payload["format"]["properties"]["intents"]["type"] == "array"
    assert "Show demo products and recent orders" in str(payload["messages"])


def test_ollama_does_not_route_unrecognized_model_labels(monkeypatch):
    monkeypatch.setattr(ollama, "_open_local", lambda *_args, **_kwargs: FakeResponse(["delete_every_order"]))
    assert ollama.classify_local_request("Show recent orders", settings()) == (None, "failed")


def test_ollama_failure_reports_failure_without_claiming_ai_result(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise HTTPError("http://127.0.0.1:11434/api/chat", 503, "unavailable", {}, None)

    monkeypatch.setattr(ollama, "_open_local", unavailable)
    assert ollama.classify_local_request("Show stock", settings()) == (None, "failed")


def test_ollama_blocks_non_loopback_endpoint_without_network(monkeypatch):
    monkeypatch.setattr(
        ollama,
        "_open_local",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote request")),
    )
    assert ollama.classify_local_request(
        "Show stock",
        settings("http://untrusted.example:11434"),
    ) == (None, "failed")


def test_local_llm_is_selected_even_when_gemini_key_is_absent(monkeypatch):
    configured = settings()
    configured.llm_provider = "ollama"
    configured.gemini_api_key = ""
    configured.gemini_synthetic_demo = False
    monkeypatch.setattr(llm, "get_settings", lambda: configured)
    monkeypatch.setattr(
        ollama,
        "_open_local",
        lambda *_args, **_kwargs: FakeResponse(["stock", "orders"]),
    )
    assert llm.classify_owner_request("Show demo stock and orders") == (
        ["stock", "orders"], "ollama"
    )


def test_owner_chat_combines_agents_when_local_llm_classifies(demo, monkeypatch):
    client, db, sample, headers = demo
    monkeypatch.setattr(
        coordinator,
        "classify_owner_request",
        lambda _request: (["products", "orders"], "ollama"),
    )
    response = client.post(
        "/api/owner/chat",
        headers=headers,
        json={"message": "Show demo products and recent orders"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "ollama"
    assert set(result["routed_agents"]) == {"customer_commerce", "inventory_logistics"}
    assert "Cotton Shirt" in result["reply"] and "DEMO-1001" in result["reply"]
    assert not any("LLM is not configured" in warning for warning in result["warnings"])
    assert db.agent_runs.count_documents({"owner_id": sample["owner_id"], "mode": "ollama"}) == 1
