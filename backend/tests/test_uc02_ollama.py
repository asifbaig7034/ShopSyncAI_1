"""UC-2 local-model acceptance and safety checks; no Ollama server is contacted."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from backend.app.application.orchestrator import coordinator
from backend.app.infrastructure.integrations import llm, ollama
from backend.scripts import measure_uc2_routing, warm_ollama


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
    assert payload["keep_alive"] == "30m"
    assert payload["format"]["properties"]["intents"]["type"] == "array"
    assert "Show demo products and recent orders" in str(payload["messages"])


def test_ollama_does_not_route_unrecognized_model_labels(monkeypatch):
    monkeypatch.setattr(ollama, "_open_local", lambda *_args, **_kwargs: FakeResponse(["delete_every_order"]))
    assert ollama.classify_local_request("Show recent orders", settings()) == (None, "failed")


def test_ollama_failure_reports_failure_without_claiming_ai_result(monkeypatch):
    attempts = []

    def unavailable(*_args, **_kwargs):
        attempts.append(True)
        raise HTTPError("http://127.0.0.1:11434/api/chat", 503, "unavailable", {}, None)

    monkeypatch.setattr(ollama, "_open_local", unavailable)
    assert ollama.classify_local_request("Show stock", settings()) == (None, "failed")
    assert len(attempts) == 2


def test_ollama_recovers_from_one_transient_failure(monkeypatch):
    timeouts = []

    def open_after_failure(_request, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            raise HTTPError("http://127.0.0.1:11434/api/chat", 503, "busy", {}, None)
        return FakeResponse(["products", "stock"])

    monkeypatch.setattr(ollama, "_open_local", open_after_failure)
    assert ollama.classify_local_request("Show products and stock", settings()) == (
        ["products", "stock"], "ollama"
    )
    assert len(timeouts) == 2
    assert 0 < timeouts[0] <= 45 * 0.7
    assert 0 < timeouts[1] <= 45


def test_ollama_retries_a_timeout_once(monkeypatch):
    attempts = []

    def open_after_timeout(_request, timeout):
        attempts.append(True)
        if len(attempts) == 1:
            raise TimeoutError("model temporarily busy")
        return FakeResponse(["orders"])

    monkeypatch.setattr(ollama, "_open_local", open_after_timeout)
    assert ollama.classify_local_request("Show recent orders", settings()) == (["orders"], "ollama")
    assert len(attempts) == 2


def test_ollama_does_not_retry_a_permanent_http_error(monkeypatch):
    attempts = []

    def bad_request(_request, timeout):
        attempts.append(True)
        raise HTTPError("http://127.0.0.1:11434/api/chat", 400, "bad request", {}, None)

    monkeypatch.setattr(ollama, "_open_local", bad_request)
    assert ollama.classify_local_request("Show stock", settings()) == (None, "failed")
    assert len(attempts) == 1


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


def test_preload_uses_empty_chat_and_same_keep_alive_as_classifier(monkeypatch):
    configured = settings()
    configured.llm_provider = "ollama"
    requests = []

    def fake_open(request, timeout):
        requests.append((request, timeout))
        return FakeResponse([])

    classifications = []

    def fake_classify(message, settings):
        classifications.append((message, settings))
        return ["products", "stock"], "ollama"

    monkeypatch.setattr(warm_ollama, "_open_local", fake_open)
    monkeypatch.setattr(warm_ollama, "classify_local_request", fake_classify)
    assert warm_ollama.warm_model(configured) >= 0
    assert classifications == [("Show products and stock", configured)]
    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:11434/api/chat"
    assert timeout >= 180
    assert json.loads(request.data) == {
        "model": "qwen2.5:1.5b", "messages": [], "stream": False, "keep_alive": "30m",
    }


def test_warmup_fails_if_real_classifier_is_unavailable(monkeypatch):
    configured = settings()
    configured.llm_provider = "ollama"
    monkeypatch.setattr(warm_ollama, "_open_local", lambda *_args, **_kwargs: FakeResponse([]))
    monkeypatch.setattr(warm_ollama, "classify_local_request", lambda *_args: (None, "failed"))
    with pytest.raises(warm_ollama.WarmupError, match="classifier warm-up failed"):
        warm_ollama.warm_model(configured)


def test_routing_measurement_rejects_fallback_or_a_slow_model(monkeypatch):
    configured = SimpleNamespace(
        llm_provider="ollama", demo_mode=True,
        shopsync_demo_owner_token="a-long-fictional-demo-token", ollama_timeout_seconds=45.0,
    )
    monkeypatch.setattr(measure_uc2_routing, "get_settings", lambda: configured)
    results = [
        {"mode": "rule-based-demo", "routing_seconds": 0.01},
        {"mode": "ollama", "routing_seconds": 3.1},
        {"mode": "ollama", "routing_seconds": 0.5},
        {"mode": "ollama", "routing_seconds": 0.6},
    ]

    def fake_request(_base, path, *_args, **_kwargs):
        if path == "/api/health":
            return {"status": "ok", "local_demo": True}
        return results.pop(0)

    monkeypatch.setattr(measure_uc2_routing, "_request_json", fake_request)
    assert measure_uc2_routing.main() == 1  # Fast rules fallback is not an AI pass.
    assert len(results) == 3
    assert measure_uc2_routing.main() == 1  # One slow Ollama request is a failure.
    assert not results


def test_routing_measurement_passes_only_three_timed_ollama_requests(monkeypatch):
    configured = SimpleNamespace(
        llm_provider="ollama", demo_mode=True,
        shopsync_demo_owner_token="a-long-fictional-demo-token", ollama_timeout_seconds=45.0,
    )
    monkeypatch.setattr(measure_uc2_routing, "get_settings", lambda: configured)
    responses = iter([{"mode": "ollama", "routing_seconds": value} for value in (0.8, 1.2, 3.0)])
    monkeypatch.setattr(
        measure_uc2_routing,
        "_request_json",
        lambda _base, path, *_args, **_kwargs: (
            {"status": "ok", "local_demo": True} if path == "/api/health" else next(responses)
        ),
    )
    assert measure_uc2_routing.main() == 0


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
