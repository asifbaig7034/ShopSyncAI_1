# UC-2 implementation and SRS alignment

The attached ShopSync AI SRS places owner natural-language routing in **FR-04**
and cross-agent events plus a combined reply in **FR-05**. It calls this use case
**UC-2: Send Request to Orchestrator**. Its expanded description is on printed
pages 16-17. The SRS separates six eventual specialist agents, while FYP-1
focuses on a basic orchestrator and one or two functional agents.

| SRS behavior | UC-2 code location | Prototype limitation |
| --- | --- | --- |
| Authenticated Business Owner; at least one agent enabled | `core/security.py`, `api/routes/owner_chat.py`, `application/orchestrator/coordinator.py` | The local owner token is a demo substitute for UC-1 login; it must never be used as production authentication. |
| LLM recognizes the request and selects relevant agents | `infrastructure/integrations/llm.py`, `infrastructure/integrations/ollama.py`, `application/orchestrator/intent_router.py` | Select Ollama (local model, no key) or Gemini (key and explicit synthetic-only flag). Run `python -m backend.scripts.verify_uc2 --require-llm` with a running backend to verify a real LLM response. Rules-only fallback does not fulfill the normal LLM flow. |
| Clarification before routing | `application/orchestrator/clarification.py`, `history.py` | A pending request must retain its conversation ID. |
| Relevant agents obtain stored business facts | `application/agents/customer_commerce.py`, `inventory_logistics.py` | Synthetic sample data and only two specialist domains exist in this UC-2 deliverable. |
| Combined reply, partial results if one agent fails | `application/orchestrator/coordinator.py`, `response_builder.py` | Unsupported domains are reported, never fabricated. |
| Agent events, audit and stored history | `application/events/`, `application/services/audit_service.py`, `infrastructure/db/` | A synthetic low-stock event reaches a read-only Customer & Commerce handler; no live customer action is performed. In-memory records disappear on restart. |
| Sensitive actions remain pending for owner | `application/services/approval_service.py` | A refund request for a saved order becomes pending; no refund occurs. Price changes and publishing are identified as future features without creating actionable requests. |
| Routing within 3 seconds, excluding agent work | `application/orchestrator/coordinator.py` | Live Gemini has a configurable 8-second total timeout. Local Ollama's CPU inference may exceed three seconds, particularly on first load. Slow routing is warned about, not treated as satisfying the SRS performance target. |

The SRS database figure includes `agent_runs`, `approval_requests`, and
`system_events`; this starter uses those names. An additional
`owner_chat_sessions` collection records clarification state because the SRS
`conversations` collection is for customer chats. MongoDB is a practical
implementation of the ObjectId-based schema but the SRS does not mandate
FastAPI, React, or MongoDB as products.

## Open SRS scope mismatch

FR-01 and FR-02 mention Staff accounts, while the final use-case diagram has
only Business Owner and Customer and NFR-01 restricts the dashboard to the
Business Owner. This UC-2 starter follows the diagram and the stated
single-brand, owner-only implementation decision. Reconcile the text before
claiming full SRS compliance; UC-1 account and staff work is not included.

## Gemini free-tier limit

Google lists a free tier for `gemini-3.5-flash-lite`, with rate limits and
eligibility that depend on the project. The unpaid-service terms prohibit
sending sensitive, confidential or personal data. Use **fictional owner
prompts only**; do not connect the free-tier demo to real customer messages,
orders, personal data or confidential brand information. The no-key local
rules mode remains available for testing but does not fulfill UC-2's final
LLM-service requirement.

If a project refuses Gemini generation with HTTP 403, local Ollama is an
alternative to paying for provider access; it is a separate, real LLM
classifier, not a relabelled rules-only result. The Docker model and server
are local, while domain data continues to come from the application's DB.

Official documentation: https://ai.google.dev/gemini-api/docs/pricing
and https://ai.google.dev/gemini-api/terms
