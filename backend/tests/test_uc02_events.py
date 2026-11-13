"""Cross-agent event is recorded once; reading stock never creates it."""

from __future__ import annotations

from time import time

import pytest
from bson import ObjectId

from backend.app.application.events.dispatcher import publish_event
from backend.app.application.events.event_types import DEMAND_SPIKE, FRAUD_FLAG


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


def test_two_event_types_route_between_demo_agents_without_business_writes(demo):
    client, db, sample, headers = demo
    first = client.post("/api/owner/demo-events/demand-spike", headers=headers)
    second = client.post("/api/owner/demo-events/demand-spike", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True and second.json()["created"] is False
    events = client.get("/api/owner/demo-events", headers=headers).json()["events"]
    demand = next(row for row in events if row["event_type"] == DEMAND_SPIKE)
    assert demand["source"] == "customer_commerce"
    assert demand["target_agents"] == ["inventory_logistics"]
    assert demand["status"] == "coordinated"
    assert len(demand["agent_responses"]) == 1
    assert demand["agent_responses"][0]["agent"] == "inventory_logistics"
    assert demand["details"]["fictional_order_count"] == 12
    assert db.products.find_one({"owner_id": sample["owner_id"]})["stock_quantity"] == 3
    assert db.orders.find_one({"owner_id": sample["owner_id"]})["status"] == "confirmed"


def test_general_event_dispatcher_retries_only_failed_subscriber(demo):
    _, db, sample, _ = demo
    calls = {"customer_commerce": 0, "inventory_logistics": 0, "social_media": 0}

    def subscriber(code, fail_first=False):
        def handle(event):
            calls[code] += 1
            assert event["event_id"] == "synthetic-multi-agent-event"
            if fail_first and calls[code] == 1:
                raise RuntimeError("private failure must not be persisted")
            return {"agent": code, "status": "observed_demo_only"}
        return handle

    handlers = {
        "customer_commerce": subscriber("customer_commerce"),
        "inventory_logistics": subscriber("inventory_logistics", fail_first=True),
        "social_media": subscriber("social_media"),
    }
    request = dict(
        owner_id=sample["owner_id"], event_id="synthetic-multi-agent-event",
        event_type=DEMAND_SPIKE, source="synthetic_test",
        details={"fictional_order_count": 12}, handlers=handlers,
    )
    assert publish_event(db, **request) is True
    event = db.system_events.find_one({"event_id": request["event_id"]})
    assert event["status"] == "subscriber_failed"
    assert event["failed_agents"] == ["inventory_logistics"]
    assert [row["agent"] for row in event["agent_responses"]] == ["customer_commerce"]
    assert publish_event(db, **request) is False
    assert publish_event(db, **request) is False
    event = db.system_events.find_one({"event_id": request["event_id"]})
    assert event["status"] == "coordinated" and event["failed_agents"] == []
    assert [row["agent"] for row in event["agent_responses"]] == ["customer_commerce", "inventory_logistics"]
    assert calls == {"customer_commerce": 1, "inventory_logistics": 2, "social_media": 0}


def test_event_id_cannot_be_reused_across_owners_or_different_details(demo):
    _, db, sample, _ = demo
    event = dict(owner_id=sample["owner_id"], event_id="synthetic-collision",
                 event_type=DEMAND_SPIKE, source="synthetic_test",
                 details={"fictional_order_count": 12})
    assert publish_event(db, **event) is True
    another_owner = ObjectId()
    db.owners.insert_one({"_id": another_owner, "email": "another@shopsync.local",
                          "agent_settings": {"inventory_logistics": True, "customer_commerce": True}})
    with pytest.raises(ValueError, match="different event"):
        publish_event(db, **{**event, "owner_id": str(another_owner)})
    with pytest.raises(ValueError, match="different event"):
        publish_event(db, **{**event, "details": {"fictional_order_count": 13}})
    with pytest.raises(ValueError, match="different event"):
        publish_event(db, **{**event, "event_type": FRAUD_FLAG})
    assert db.system_events.count_documents({"event_id": event["event_id"]}) == 1
    assert db.system_events.find_one({"event_id": event["event_id"]})["owner_id"] == sample["owner_id"]


def test_stale_processing_claim_is_retryable_without_a_new_event(demo):
    _, db, sample, _ = demo
    calls = []

    def receiver(_event):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("fictional interruption")
        return {"agent": "inventory_logistics", "status": "observed_demo_only"}

    request = dict(owner_id=sample["owner_id"], event_id="fictional-stale-event",
                   event_type=DEMAND_SPIKE, source="customer_commerce",
                   details={"fictional_order_count": 12},
                   handlers={"inventory_logistics": receiver})
    assert publish_event(db, **request) is True
    db.system_events.update_one(
        {"event_id": request["event_id"]},
        {"$set": {"status": "processing", "processing_started_at": time() - 180}},
    )
    assert publish_event(db, **request) is False
    event = db.system_events.find_one({"event_id": request["event_id"]})
    assert event["status"] == "coordinated" and len(event["agent_responses"]) == 1
    assert len(calls) == 2


def test_unimplemented_or_disabled_subscribers_never_claim_coordination(demo):
    _, db, sample, _ = demo
    assert publish_event(db, owner_id=sample["owner_id"], event_id="fictional-risk-only",
                         event_type=FRAUD_FLAG, source="risk_trust", details={"fictional_order": "DEMO-1001"}) is True
    fraud = db.system_events.find_one({"event_id": "fictional-risk-only"})
    assert fraud["status"] == "unhandled" and fraud["agent_responses"] == []

    db.owners.update_one({"_id": ObjectId(sample["owner_id"])},
                         {"$set": {"agent_settings.inventory_logistics": False}})
    assert publish_event(db, owner_id=sample["owner_id"], event_id="fictional-disabled-target",
                         event_type=DEMAND_SPIKE, source="customer_commerce",
                         details={"fictional_order_count": 12}) is True
    disabled = db.system_events.find_one({"event_id": "fictional-disabled-target"})
    assert disabled["status"] == "unhandled" and disabled["target_agents"] == []


def test_subscriber_disabled_after_failure_is_skipped_without_replay(demo):
    _client, db, sample, _headers = demo
    calls = []

    def temporarily_failed(_event):
        calls.append(True)
        raise RuntimeError("the owner must not see this detail")

    request = dict(
        owner_id=sample["owner_id"], event_id="fictional-disable-after-recording",
        event_type=DEMAND_SPIKE, source="customer_commerce",
        details={"fictional_order_count": 12},
        handlers={"inventory_logistics": temporarily_failed},
    )
    assert publish_event(db, **request) is True
    db.owners.update_one({"_id": ObjectId(sample["owner_id"])},
                         {"$set": {"agent_settings.inventory_logistics": False}})
    assert publish_event(db, **request) is False
    event = db.system_events.find_one({"event_id": request["event_id"]})
    assert event["status"] == "skipped_disabled"
    assert event["skipped_agents"] == ["inventory_logistics"]
    assert event["agent_responses"] == []
    assert len(calls) == 1
    db.owners.update_one({"_id": ObjectId(sample["owner_id"])},
                         {"$set": {"agent_settings.inventory_logistics": True}})
    assert publish_event(db, **request) is False
    assert len(calls) == 1
