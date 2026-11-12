# UC-3 acceptance and validation

Use the supplied SRS section 6.3, FR-06 through FR-08, the Customer Agent objective, and relevant non-functional requirements as the acceptance basis. This starter also exposes the integration boundary with FR-09 and UC-4.

## Functional checks

| Requirement | How to check |
| --- | --- |
| English, Urdu and Roman Urdu | Ask the same catalogue question in all three languages; inspect the reply language and factual values. |
| Product facts from system data | Change a sample product price/stock in the repository or database, ask again, and check the new values. |
| Missing product | Ask for an absent product; receive clarification or actual available alternatives. |
| Recommendations | Request suggestions; only product IDs and values present in the catalogue may appear. |
| Saved conversation | Check inbound/outbound roles, timestamps and customer link; restart with MongoDB and confirm persistence. |
| Owner review | Open the conversation list and detail in the React owner inbox. |
| Human handover | Ask for a human; confirm owner notification/status, human mode and suppression of bot replies. |
| Owner response | Send an owner reply during human mode; confirm history and channel delivery status. |
| Complaints | Raise a complaint; route to the configured UC-2 adapter or explicitly hand over to the owner. |
| LLM outage or uncertainty | Simulate an error or uncertain classification; hand over without invented facts. |
| Duplicate webhook | Re-deliver one external message ID; preserve one customer message and one corresponding reply. |
| Delivery failure | Retry known failures within configured limits; preserve ambiguous outcomes for review instead of blindly resending. |
| Order boundary | Ask to order, cancel or return; UC-4 must execute business rules and approvals. No stock mutation belongs to this module. |
| Access control | Reject dashboard calls without the configured development credential; replace this credential with UC-1 permissions in the combined project. |
| Webhook authenticity | Reject invalid signatures and incorrect webhook verification tokens. |

## Targets that need real-provider evaluation

**Response time:** Measure time from receipt of a simple customer query to provider acknowledgement of the response. Record at least median and 95th percentile, along with the number of failures and timeouts. Test with the selected LLM, database, deployment region and WhatsApp account. The SRS target is within 10 seconds for simple queries.

**Concurrency:** Run at least 50 independent customer conversations concurrently. Inspect lost messages, duplicate replies, conversation ordering and error rates, as well as latency. Passing sequential unit tests does not establish this target.

**Intent classification:** Prepare a held-out set with labels for product questions, price, availability, recommendations, delivery, order operations, complaints, human requests, unclear input and unsupported input. Include English, Urdu and Roman Urdu. Evaluate the actual LLM configuration against the SRS objective of at least 85% correct intent classification. Do not count the deterministic demo rules as LLM evaluation.

## Starter limitations

The project is a single-business integration starter. The shared development credential does not implement UC-1 roles or encrypted credential storage. Live account onboarding, deployment HTTPS, privacy removal workflow, notification delivery outside the inbox, and production monitoring still need integration with the wider system.

External delivery cannot be promised exactly once after an ambiguous network timeout unless the provider supports an appropriate idempotency or reconciliation mechanism. Preserve ambiguous attempts and review them before sending again. An accepted send is not proof that a recipient has received or read a message.

The sample catalogue exists because UC-5 is outside this assignment. Real catalogue maintenance and inventory/order transactions belong to the corresponding team modules.

The live worker processes one inbound job at a time in this starter. Add a measured concurrency strategy before claiming the 50-conversation or 10-second live targets. The handover state and one-time acknowledgement use one atomic repository operation; the Mongo implementation still needs a database-backed integration check.
