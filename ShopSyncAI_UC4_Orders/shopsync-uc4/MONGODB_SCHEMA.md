# MongoDB data contract for UC-4

MONGODB_DB selects the application database (default shopsync_uc4). MONGODB_URI selects the deployment. These defaults do not connect to a supplied shared team database.

## IDs and values

Entity MongoDB _id values are **strings**, preserving earlier IDs. New product/customer/order/request/audit IDs are 16-character uppercase UUID-derived strings. Incoming-message IDs use provider message-ID strings; the migration marker uses sqlite-import-v1. They are not ObjectId values.

The API exposes _id as id. References customer_id/product_id/order_id contain the same string. If another team module uses ObjectId, agree on a mapping/shared type first: converting a displayed ID to text does not change another collection's BSON type.

Money uses integer minor units: PKR 1500 is 150000 price_cents. Times are UTC ISO-8601 strings. risk_reasons is an array, audit details is a document, and products.active is boolean.

## Collections

| Collection | Main fields | Meaning |
| --- | --- | --- |
| products | sku, name, price_cents, stock_qty, active | Authoritative stock in this standalone module |
| customers | phone, name, last_inbound_at | Country-code phone, digits only |
| orders | customer_id, channel, status, items, delivery_address, payment_method, payment_status, total_cents, risk_score, risk_reasons, source_key, timestamps, revision | Embedded item snapshots; customer reference |
| approvals | order_id, action, status, owner_reason, timestamps | High-risk review |
| return_requests | order_id, kind, reason, status, owner_reason, timestamps | One full-order request per order |
| stock_movements | order_id, product_id, kind, change_qty, created_at | Stock audit |
| financial_entries | order_id, kind, amount_cents, created_at | Sale/cancellation/refund bookkeeping |
| audit_events | order_id, actor, event, details, created_at | Business history |
| inbound_messages | message_id, phone, body, response, created_at | Message deduplication with sender check |
| outbox | phone, body, status, attempts, next_attempt_at, provider_id, last_error, claim_id, lease_until, created_at | Leased message delivery |
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

Business writes use one session/transaction with snapshot reads and majority writes. Stock deduction is conditional on sufficient current stock. Order writes coordinate competing cancellation/refund decisions. Transaction retries may replay database callbacks; external HTTP runs outside them. Standalone MongoDB is rejected because it cannot provide these required multi-document transactions.

## State boundaries

Order states are DRAFT, RISK_HOLD, CONFIRMED, SHIPPED, CANCELLED and REJECTED. Return/refund outcome is a separate return_requests.status; completed refunds do not introduce a REFUNDED order state.

Approved refunds create a negative REFUND entry and set payment_status=REFUND_REQUIRES_MANUAL_ACTION. This is not evidence that money has been transferred. Active/completed return/refund prevents cancellation from reversing the sale again. Accepted full returns restore stock; rejected requests/inspection require a reason.

Outbox UNCERTAIN means review before retry. NEEDS_TEMPLATE means approved-template delivery remains to be integrated.

## Team agreement

Agree on database/collection names, customer/product/business IDs and field mappings before sharing data. UC-1 supplies permissions/settings; UC-3 supplies trusted sender identity. Agree which service owns stock deduction and call UC-4 APIs for transitions instead of repeating stock/financial writes in another module.

This version is single-business. No per-business tenant filter is implemented. A combined multi-business project needs an agreed business ID and authorization checks before sharing populated collections.
