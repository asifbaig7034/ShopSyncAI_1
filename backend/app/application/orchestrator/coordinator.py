"""UC-2 Owner request routing and coordination.

The six SRS agent domains share one orchestrator. This FYP-1 implementation
has two read-only handlers; other domains are explicitly marked unavailable.
"""

from __future__ import annotations

from time import perf_counter

from bson import ObjectId

from ..agents import customer_commerce, inventory_logistics
from ..services.approval_service import queue_owner_approval
from ..services.audit_service import record_uc2_audit
from .clarification import clear_pending, get_or_create_session, message_with_context, save_pending
from .history import record_run
from .intent_router import detect_intents, sensitive_action, validated_intents
from .response_builder import build_reply
from ...infrastructure.integrations.llm import classify_owner_request

_HANDLERS = {
    "products": inventory_logistics,
    "stock": inventory_logistics,
    "orders": customer_commerce,
}


def _owner_record(db, owner_id: str) -> dict | None:
    primary_key = ObjectId(owner_id) if ObjectId.is_valid(owner_id) else owner_id
    return db.owners.find_one({"_id": primary_key})


def _llm_warning(status: str) -> str | None:
    return {
        "unconfigured": "LLM is not configured. Local demo rules were used; this does not complete the SRS LLM requirement.",
        "failed": "LLM failed or was rejected. Transient failures are retried; local demo rules were used instead.",
        "blocked": "LLM was not called because this request may contain private data. Local demo rules were used.",
    }.get(status)


def _save_interaction(db, *, owner_id: str, conversation_id: str, request: str, response: dict, status: str) -> None:
    run_id = record_run(db, owner_id=owner_id, conversation_id=conversation_id, request=request, response=response, status=status)
    if response["approval_request_id"]:
        db.approval_requests.update_one(
            {"_id": ObjectId(response["approval_request_id"])},
            {"$set": {"run_id": ObjectId(run_id)}},
        )
    record_uc2_audit(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        run_id=run_id,
        status=status,
        routed_agents=response["routed_agents"],
    )


def process_owner_request(db, owner_id: str, message: str, conversation_id: str | None = None) -> dict:
    """Process and persist an Owner request; never execute a business action."""
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        raise ValueError("Owner message must have between 1 and 2000 characters")
    owner = _owner_record(db, owner_id)
    if not owner:
        raise ValueError("Business Owner account is unavailable")
    agent_settings = owner.get("agent_settings") or {}
    if not any(agent_settings.get(code, False) for code in (customer_commerce.CODE, inventory_logistics.CODE)):
        raise ValueError("Enable at least one available agent before sending a request")

    original_message = message.strip()
    session = get_or_create_session(db, owner_id, conversation_id)
    full_request = message_with_context(session, original_message)
    started = perf_counter()
    try:
        ai_labels, ai_status = classify_owner_request(full_request)
    except Exception:
        # Provider errors are reported without disclosing internals to Owner.
        ai_labels, ai_status = None, "failed"
    labels = validated_intents(ai_labels)
    if ai_status in {"gemini", "ollama"} and labels:
        # A safe local read-only hint helps a multi-intent request if the
        # classifier omitted one domain; the response still uses stored data.
        labels = list(dict.fromkeys(labels + detect_intents(full_request)))
        mode = ai_status
    else:
        labels = detect_intents(full_request)
        mode = "rule-based-demo"
    warnings = [warning] if (warning := _llm_warning(ai_status)) else []

    meaningful = [label for label in labels if label in _HANDLERS]
    outside = any(label in {"risk", "unsupported"} for label in labels)
    pending_action = sensitive_action(full_request)
    # A refund approval needs an existing saved order and never executes here.
    target_found = True
    if pending_action == "refund":
        current_orders = db.orders.find({"owner_id": owner_id}, {"_id": 1, "order_number": 1, "demo_order_ref": 1})
        casefolded = full_request.casefold()
        target_found = any(
            str(order.get("order_number") or order.get("demo_order_ref") or order["_id"]).casefold() in casefolded
            for order in current_orders
        )
    if (not meaningful and not outside) or (pending_action == "refund" and not target_found):
        save_pending(db, session, full_request)
        response = {
            "conversation_id": session["_id"],
            "reply": (
                "Which saved order ID should I use for the refund approval request? No refund was processed."
                if pending_action == "refund" else
                "Could you specify whether you need products, stock, or recent orders?"
            ),
            "routed_agents": [],
            "needs_clarification": True,
            "mode": mode,
            "warnings": warnings,
            "approval_request_id": None,
        }
        _save_interaction(db, owner_id=owner_id, conversation_id=session["_id"], request=original_message, response=response, status="clarification_pending")
        return response

    clear_pending(db, session)
    # The routing timer stops before processing data in the agent handlers.
    routing_seconds = perf_counter() - started
    if routing_seconds > 3.0:
        warnings.append(f"Intent routing took {routing_seconds:.2f}s; target is at most 3s before agent processing.")

    results: list[str] = []
    unavailable: list[str] = []
    routed_agents: list[str] = []
    for label in meaningful:
        agent = _HANDLERS[label]
        if not agent_settings.get(agent.CODE, False):
            unavailable.append(f"{agent.LABEL} (disabled)")
            continue
        try:
            result = agent.handle(db, owner_id, label, full_request)
            if result:
                results.append(result)
            if agent.CODE not in routed_agents:
                routed_agents.append(agent.CODE)
        except Exception:
            # The exception is not shown to the owner (it might contain
            # private database/provider details). Other handlers continue.
            unavailable.append(f"{agent.LABEL} (failed)")

    if outside:
        unavailable.append("Risk/market/social/finance features (later phase)")
    approval_request_id = None
    can_queue_approval = bool(pending_action == "refund" and "orders" in meaningful and agent_settings.get(customer_commerce.CODE, False))
    if can_queue_approval:
        approval_request_id = queue_owner_approval(
            db,
            owner_id=owner_id,
            conversation_id=session["_id"],
            action=pending_action,
            message=full_request,
        )
    reply = build_reply(
        results,
        unavailable,
        out_of_scope=outside,
        approval_action=pending_action if can_queue_approval else None,
        future_approval_action=pending_action if pending_action and not can_queue_approval else None,
    )
    response = {
        "conversation_id": session["_id"],
        "reply": reply,
        "routed_agents": routed_agents,
        "needs_clarification": False,
        "mode": mode,
        "warnings": warnings,
        "approval_request_id": approval_request_id,
    }
    status = "partial" if unavailable else ("pending_approval" if approval_request_id else "completed")
    _save_interaction(db, owner_id=owner_id, conversation_id=session["_id"], request=original_message, response=response, status=status)
    return response
