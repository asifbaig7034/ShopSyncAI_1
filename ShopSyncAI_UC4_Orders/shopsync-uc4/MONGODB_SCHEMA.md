# MongoDB data contract for UC-4

MONGODB_DB selects the application database (default shopsync_uc4). MONGODB_URI selects the deployment. These defaults do not connect to a supplied shared team database.

## IDs and values

Entity MongoDB _id values are **strings**, preserving earlier IDs. New product/customer/order/request/audit IDs are 16-character uppercase UUID-derived strings. Incoming-message IDs use provider message-ID strings; delivery-event IDs are deterministic SHA-256 strings; the migration marker uses sqlite-import-v1. They are not ObjectId values.

The API exposes _id as id. References customer_id/product_id/order_id contain the same string. If another team module uses ObjectId, agree on a mapping/shared type first: converting a displayed ID to text does not change another collection's BSON type.

Money uses integer minor units: PKR 1500 is 150000 price_cents. Times are UTC ISO-8601 strings. risk_reasons is an array, audit details is a document, and products.active is boolean.

## Collections

| Collection | Main fields | Meaning |
| --- | --- | --- |
| products | sku, name, price_cents, stock_qty, active | Authoritative stock in this standalone module |
| customers | phone, name, last_inbound_at | Country-code phone, digits only |
| orders | customer_id, channel, status, items, delivery_address, payment_method, payment_status, total_cents, risk_score, risk_reasons, source_key, source_payload, timestamps, revision | Embedded item snapshots; customer reference |
| approvals | order_id, action, status, owner_reason, timestamps | High-risk review |
| return_requests | order_id, kind, reason, status, owner_reason, timestamps | One full-order request per order |
| stock_movements | order_id, product_id, kind, change_qty, created_at | Stock audit |
| financial_entries | order_id, kind, amount_cents, created_at | Sale/cancellation/refund bookkeeping |
| audit_events | order_id, actor, event, details, created_at | Business history |
| inbound_messages | message_id, phone, body, response, created_at | Message deduplication with sender check |
| outbox | phone, body, status, attempts, next_attempt_at, provider_id, provider_ids, provider_attempt_number, provider_status, delivery_timestamp, delivery_rank, last_error, claim_id, lease_until, created_at | Leased message delivery and current/historical provider attempts |
| delivery_events | provider_id, provider_status, provider_timestamp, error_codes, recipient, rank, applied, received_at, next_check_at, outcome, processed_at | Deduplicated status receipt; buffered until related message can be resolved |
| migration_metadata | status, source_sha256, target_sha256, counts, order_item_count, imported_at | Verified SQLite import marker |

Orders.items entries contain product_id, sku, name, unit_price_cents and quantity. Names/prices are snapshots, so catalog changes do not silently alter orders. API responses may add order_id to each returned item for compatibility. There is no separate order_items collection.

New orders start with revision=0. Mutations increment revision to coordinate decisions across collections; imported orders may acquire this field on first mutation.

## Constraints and transactions

Startup creates collections and unique indexes for:

- products.sku; customers.phone; orders.source_key
- approvals (order_id, action); return_requests.order_id
- stock_movements (order_id, product_id, kind)
- financial_entries (order_id, kind)
- Incoming message _id uniqueness
- Delivery-event _id uniqueness; receipt indexes on provider_id/applied and applied/next_check_at
- Outbox indexes on provider_id and provider_ids for current/historical attempt mapping

Business writes use one session/transaction with snapshot reads and majority writes. Stock deduction is conditional on sufficient current stock. Order writes coordinate competing cancellation/refund decisions. Transaction retries may replay database callbacks; external HTTP runs outside them. Standalone MongoDB is rejected because it cannot provide these required multi-document transactions.

## State boundaries

Order states are DRAFT, RISK_HOLD, CONFIRMED, SHIPPED, CANCELLED and REJECTED. Return/refund outcome is a separate return_requests.status; completed refunds do not introduce a REFUNDED order state.

Approved refunds create a negative REFUND entry and set payment_status=REFUND_REQUIRES_MANUAL_ACTION. This is not evidence that money has been transferred. Active/completed return/refund prevents cancellation from reversing the sale again. Accepted full returns restore stock; rejected requests/inspection require a reason.

Outbox ACCEPTED means the provider returned a message ID; it is not a delivered receipt. Signed callback states are SENT, DELIVERED and READ. Known temporary failures become FAILED and can retry while attempts are below five; permanent/unknown failures become MANUAL_REVIEW. NEEDS_CONFIGURATION and NEEDS_TEMPLATE identify configuration and messaging-window issues. UNCERTAIN requires review before retry.

Callbacks never mutate order/stock/financial records. Receipt duplicates are deduplicated. Early callbacks are buffered in delivery_events and reconciled when provider-ID mapping becomes available. Historical provider_ids allow confirmed delivery from an earlier attempt to stop queued retries. Timestamp/rank checks and attempt matching prevent stale sent/failed receipts from downgrading delivered/read or a newer attempt.

The signed webhook counts incoming messages as processed and valid stored receipts as statuses_processed. If WA_PHONE_NUMBER_ID is configured, metadata.phone_number_id must match it. A counted receipt can still be waiting for its message mapping.

## Idempotent draft contract

An owner source_key identifies one original normalized source_payload. Sending the same key and same original payload returns the existing order, including after the draft was edited. Reusing the key for different details returns HTTP 409; choose a new key for a genuinely new order. Legacy imported orders without source_payload compare reconstructable current fields.

The demo UI retains the complete pending draft request in sessionStorage until a successful response containing an order ID. This supports exact retry after an ambiguous response or same-tab reload. Explicitly changing form data starts a new attempt. This pending entry includes customer/address/item data and source_key; it does not contain auth. The existing demo owner-token entry is separate.

## Team agreement

Agree on database/collection names, customer/product/business IDs and field mappings before sharing data. UC-1 supplies permissions/settings; UC-3 supplies trusted sender identity. Agree which service owns stock deduction and call UC-4 APIs for transitions instead of repeating stock/financial writes in another module.

This version is single-business. No per-business tenant filter is implemented. A combined multi-business project needs an agreed business ID and authorization checks before sharing populated collections.
