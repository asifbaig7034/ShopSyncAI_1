"""Owner chat history stored in the SRS's agent_runs collection."""

from __future__ import annotations

from datetime import datetime, timezone


def record_run(db, *, owner_id: str, conversation_id: str, request: str, response: dict, status: str) -> str:
    record = {
        "owner_id": owner_id,
        "conversation_id": conversation_id,
        "agent_code": "orchestrator",
        "request": request,
        "result": {"reply": response["reply"], "warnings": response["warnings"]},
        "reply": response["reply"],
        "status": status,
        "routed_agents": response["routed_agents"],
        "needs_clarification": response["needs_clarification"],
        "mode": response["mode"],
        "approval_request_id": response.get("approval_request_id"),
        "created_at": datetime.now(timezone.utc),
    }
    return str(db.agent_runs.insert_one(record).inserted_id)


def get_history(db, owner_id: str, conversation_id: str) -> list[dict]:
    return [
        {
            "id": str(run["_id"]),
            "request": run["request"],
            "reply": run.get("reply", (run.get("result") or {}).get("reply", "")),
            "routed_agents": run.get("routed_agents", []),
            "created_at": run.get("created_at").isoformat() if run.get("created_at") else None,
        }
        for run in db.agent_runs.find({"owner_id": owner_id, "conversation_id": conversation_id}).sort("created_at", 1)
    ]
