"""Cross-agent event is recorded once; reading stock never creates it."""

from __future__ import annotations


def test_simulated_low_stock_event_deduplicates_without_mutating_product(demo):
    client, db, sample, headers = demo
    assert db.system_events.count_documents({"event_type": "stock.low"}) == 0
    client.post("/api/owner/chat", headers=headers, json={"message": "Show stock"})
    assert db.system_events.count_documents({"event_type": "stock.low"}) == 0

    first = client.post("/api/owner/demo-events/low-stock", headers=headers)
    second = client.post("/api/owner/demo-events/low-stock", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["simulated"] is True
    assert db.system_events.count_documents({"event_type": "stock.low", "owner_id": sample["owner_id"]}) == 1
    event = db.system_events.find_one({"event_type": "stock.low", "owner_id": sample["owner_id"]})
    assert event["status"] == "coordinated"
    assert len(event["agent_responses"]) == 1
    assert db.products.find_one({"owner_id": sample["owner_id"]})["stock_quantity"] == 3
    listed = client.get("/api/owner/demo-events", headers=headers)
    assert len(listed.json()["events"]) == 1
