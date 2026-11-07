"""Meaningful UC-2 acceptance flows from the final SRS."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.application.orchestrator import coordinator


def test_only_authenticated_owner_can_use_uc2(demo):
    client, _, _, headers = demo
    assert client.post("/api/owner/chat", json={"message": "Show stock"}).status_code == 401
    assert client.post("/api/owner/chat", headers={"Authorization": "Bearer incorrect"}, json={"message": "Show stock"}).status_code == 401
    assert client.post("/api/owner/chat", headers=headers, json={"message": "Show stock"}).status_code == 200


def test_combined_results_come_from_saved_business_data_and_are_audited(demo):
    client, db, sample, headers = demo
    result = client.post("/api/owner/chat", headers=headers, json={"message": "Show stock and recent orders"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert "Cotton Shirt" in body["reply"]
    assert "DEMO-1001" in body["reply"]
    assert "inventory_logistics" in body["routed_agents"]
    assert "customer_commerce" in body["routed_agents"]
    assert body["mode"] == "rule-based-demo"
    assert isinstance(body["routing_seconds"], float) and body["routing_seconds"] >= 0
    assert any("LLM" in warning for warning in body["warnings"])
    assert db.agent_runs.count_documents({"owner_id": sample["owner_id"]}) == 1
    assert db.system_events.count_documents({"owner_id": sample["owner_id"], "event_type": "audit.uc2.request"}) == 1


def test_clarification_resumes_same_conversation_and_history(demo):
    client, _, _, headers = demo
    first = client.post("/api/owner/chat", headers=headers, json={"message": "Show me"}).json()
    assert first["needs_clarification"] is True
    assert isinstance(first["routing_seconds"], float) and first["routing_seconds"] >= 0
    second = client.post("/api/owner/chat", headers=headers, json={"message": "stock", "conversation_id": first["conversation_id"]}).json()
    assert second["needs_clarification"] is False
    assert second["conversation_id"] == first["conversation_id"]
    assert "Cotton Shirt" in second["reply"]
    saved = client.get("/api/owner/chat/history", headers=headers, params={"conversation_id": first["conversation_id"]})
    assert saved.status_code == 200
    assert len(saved.json()["messages"]) == 2


def test_disabled_agent_and_agent_failure_yield_partial_results(demo, monkeypatch):
    client, db, sample, headers = demo
    from bson import ObjectId

    db.owners.update_one({"_id": ObjectId(sample["owner_id"])}, {"$set": {"agent_settings.inventory_logistics": False}})
    disabled = client.post("/api/owner/chat", headers=headers, json={"message": "Show stock and orders"}).json()
    assert "customer_commerce" in disabled["routed_agents"]
    assert "inventory_logistics" not in disabled["routed_agents"]
    assert "disabled" in disabled["reply"].lower()

    db.owners.update_one({"_id": ObjectId(sample["owner_id"])}, {"$set": {"agent_settings.inventory_logistics": True}})
    monkeypatch.setattr(coordinator.inventory_logistics, "handle", lambda *_: (_ for _ in ()).throw(RuntimeError("private DB error")))
    partial = client.post("/api/owner/chat", headers=headers, json={"message": "Show stock and orders"}).json()
    assert "DEMO-1001" in partial["reply"]
    assert "failed" in partial["reply"].lower()
    assert "private DB error" not in partial["reply"]


def test_saved_order_refund_only_creates_one_pending_approval_and_never_changes_order(demo):
    client, db, sample, headers = demo
    request = {"message": "Refund order DEMO-1001"}
    first = client.post("/api/owner/chat", headers=headers, json=request).json()
    second = client.post("/api/owner/chat", headers=headers, json={**request, "conversation_id": first["conversation_id"]}).json()
    assert first["approval_request_id"] == second["approval_request_id"]
    assert db.approval_requests.count_documents({"owner_id": sample["owner_id"]}) == 1
    assert db.orders.find_one({"owner_id": sample["owner_id"]})["status"] == "confirmed"


def test_mixed_refund_and_status_request_still_requires_approval(demo):
    client, db, sample, headers = demo
    body = client.post("/api/owner/chat", headers=headers, json={"message": "Refund order DEMO-1001 and show status"}).json()
    assert body["approval_request_id"] is not None
    assert db.orders.find_one({"owner_id": sample["owner_id"]})["status"] == "confirmed"


def test_persistent_llm_failure_is_visible_to_owner_in_demo_mode(demo, monkeypatch):
    client, _, _, headers = demo
    monkeypatch.setattr(coordinator, "classify_owner_request", lambda *_: (None, "failed"))
    body = client.post("/api/owner/chat", headers=headers, json={"message": "Show stock"}).json()
    assert body["mode"] == "rule-based-demo"
    assert any("LLM failed" in warning for warning in body["warnings"])


def test_slow_clarification_reports_routing_time(demo, monkeypatch):
    client, _, _, headers = demo
    monkeypatch.setattr(coordinator, "classify_owner_request", lambda *_: (None, "unconfigured"))
    times = iter([0.0, 3.25])
    monkeypatch.setattr(coordinator, "perf_counter", lambda: next(times))
    response = client.post("/api/owner/chat", headers=headers, json={"message": "Show me"})
    assert response.status_code == 200
    body = response.json()
    assert body["needs_clarification"] is True
    assert body["routing_seconds"] == 3.25
    assert any("Intent routing took 3.25s" in warning for warning in body["warnings"])


def test_history_returns_latest_100_records_in_chronological_order(demo):
    client, db, sample, headers = demo
    conversation_id = "history-pagination-demo"
    baseline = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.agent_runs.insert_many([
        {
            "owner_id": sample["owner_id"],
            "conversation_id": conversation_id,
            "request": f"request-{i:03d}",
            "result": {"reply": f"reply-{i:03d}", "warnings": []},
            "created_at": baseline + timedelta(minutes=i),
        }
        for i in range(105)
    ])
    response = client.get(
        "/api/owner/chat/history",
        headers=headers,
        params={"conversation_id": conversation_id},
    )
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert len(messages) == 100
    assert messages[0]["request"] == "request-005"
    assert messages[-1]["request"] == "request-104"
