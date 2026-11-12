# UC-3 team integration

UC-3 owns customer messaging and the owner conversation inbox. The API prefix is `/api`. All owner and simulator routes require `Authorization: Bearer <DASHBOARD_API_TOKEN>`. The health route is public. WhatsApp webhooks use their own verification token and raw-body signature, not the dashboard credential.

## API contract

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Inspect storage, provider and channel modes. |
| GET | `/api/products` | Read the configured business catalogue. |
| GET | `/api/conversations` | List owner inbox summaries. |
| GET | `/api/conversations/{id}` | Read the conversation and message history. |
| POST | `/api/demo/messages` | Simulate a customer message; available only in demo mode. |
| POST | `/api/conversations/{id}/handover` | Set `mode` to `human` or `bot`. |
| POST | `/api/conversations/{id}/messages` | Send an owner message during human mode. |
| GET | `/api/webhooks/whatsapp` | Verify the webhook subscription. |
| POST | `/api/webhooks/whatsapp` | Authenticate and queue incoming WhatsApp messages. |

Use the generated OpenAPI document at `/openapi.json` or `/docs` as the exact request schema.

Example simulator request:

```http
POST /api/demo/messages
Authorization: Bearer demo-owner-token
Content-Type: application/json

{"customer_id":"demo-customer","text":"What products are available?","external_id":"demo-request-001"}
```

Reuse the same `external_id` when retrying the same message after a failed browser request. A new user message must get a new ID. Reusing an ID for different text is an error. The returned conversation is scoped to the configured business and channel.

The owner-reply route also accepts an optional `external_id` alongside `text`. The supplied React client retains that ID across retries of the same pending owner reply. Other clients should do the same to avoid creating a new message after a lost HTTP acknowledgement.

List responses have an `items` array. Conversation mutations and detail responses have a `conversation` object with `id`, `business_id`, `customer_id`, `customer_name`, `mode`, `language`, `updated_at`, `last_message`, `handover_reason`, and `messages`. Message roles are `customer`, `assistant`, `owner` and `system`. Read individual delivery statuses instead of assuming that a stored message was delivered.

## UC-1 account and setup

Replace the development bearer credential check with the team's validated owner session and permission checks. Derive the business context from the authenticated owner on the server. Do not accept a client-supplied business ID as authorization. The starter is one business per backend instance and does not implement multi-tenant sign-in.

Supply the configured agent-enabled and WhatsApp-connected flags. Keep external provider keys on the backend; the settings view in UC-1 should show connection status without returning those secrets to the browser. Use the wider project's encrypted credential storage for real accounts.

## UC-2 orchestrator and UC-4 orders

The backend has configurable integration adapters for `ORCHESTRATOR_URL` and `ORDER_SERVICE_URL`. Treat their request and response shape as a proposed contract to agree with teammates. An adapter being configured does not implement the receiving service. Set `INTEGRATION_TOKEN` consistently on the calling and receiving services.

Each adapter POSTs directly to its configured full URL, with this JSON body:

```json
{
  "business_id": "demo-business",
  "conversation_id": "conversation-reference",
  "customer_id": "customer-reference",
  "intent": "order_status",
  "text": "Where is my order?",
  "request_id": "stable-request-reference"
}
```

The request includes `Idempotency-Key: <request_id>` and, when configured, an integration Bearer credential. The expected response is `{"reply":"A verified, customer-facing result from the receiving module."}`. The receiving service must authenticate this call, verify access to customer/order data, and deduplicate mutating actions. Configure only trusted internal service URLs; the raw request text can contain personal information needed for an order/support task.

UC-2 owns inter-agent coordination and complaint routing. When no receiving adapter is available, UC-3 explicitly hands the conversation to the owner rather than claiming that another agent completed the work.

UC-4 owns order details collection, customer/order ownership checks, confirmation, fraud scoring, stock deduction, cancellation eligibility, and returns/refund approval. UC-3 passes the conversation intent to that module and shows its verified result. Never permit the LLM to write orders or alter stock directly. If your teammate's UC-4 routes have a different shape, adapt the UC-3 adapter rather than duplicating the order rules.

Agree these shared fields before merging: `business_id`, `customer_id`, product ID, product name, description, price/currency, available stock, order ID, order owner and delivery-policy source. WhatsApp customer IDs should use the same normalized representation in all modules.

## MongoDB

Set `STORAGE=mongo`, `MONGO_URI`, and `MONGO_DATABASE` in the backend environment, then restart. Startup checks connectivity and creates indexes. The collections are `products`, `conversations` and `ingress`, scoped by `business_id`.

Demo mode inserts sample catalogue records if they do not exist. Use a dedicated demo database/business ID so examples cannot mix with actual business data. In-memory storage is for local demonstration only. For deliberate sample-data insertion there is also `python -m app.seed --confirm-demo-data`.

The starter stores a bounded message array in each conversation document and durable inbound jobs separately. A conversation is limited to 500 messages. Add an archive or separate-message collection strategy before supporting long-lived customer histories.

## Real LLM connection

Keep `CHANNEL=demo` while testing the LLM. Set `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL` to a model available to your account. The provider uses the Responses API and a structured interpretation; business values are rendered from retrieved data. No API keys are included in the frontend.

Do not send the entire customer profile or irrelevant history to a model. The provider has limited input and text redaction; this is not a complete anonymization or privacy-removal system. Evaluate privacy with realistic messages before live use.

## Real WhatsApp connection

Use a separate configured environment with `DEMO_MODE=false`, `STORAGE=mongo`, `LLM_PROVIDER=openai`, `CHANNEL=whatsapp`, a non-demo dashboard credential, and all required `WHATSAPP_*` settings from `backend/.env.example`. Set the Graph API version explicitly for the account. The application fails configuration checks rather than silently falling back to a simulated live channel.

Expose the webhook through your team's HTTPS backend, verify the subscription, and subscribe the configured WhatsApp business account. Validate incoming signatures before parsing message content and match the configured phone-number ID. Free-form reply delivery is constrained to the customer service window; template messaging is outside this starter.

Owner handover changes conversation state and suppresses queued bot replies. A reply already accepted by the external provider cannot be recalled. Ambiguous delivery outcomes remain for review; do not automatically resend them as if the first attempt certainly failed.

## Official references

- [FastAPI lifespan and testing](https://fastapi.tiangolo.com/advanced/testing-events/)
- [PyMongo asynchronous migration](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [WhatsApp messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)
- [WhatsApp webhook setup](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint)

The live Meta documentation could not be fetched during the implementation review (HTTP 429). Meta's archived official SDK documentation was available for the webhook verification/signature conventions. Recheck current account/version requirements when enabling live delivery.
