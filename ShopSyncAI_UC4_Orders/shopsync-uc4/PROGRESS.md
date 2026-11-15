# UC-4 Manage Orders Progress

Updated: 15 November 2026

## Current result

UC-4 uses Python/FastAPI and MongoDB through PyMongo. SQLite is only an optional read-only migration source. This update fixes five review findings while keeping the same API-based module and local HTML/CSS/JavaScript demonstration screen. Team React and UC-1/2/3 integration remain pending.

Use UPDATE_IN_PLACE.md for the code-only update and FIXES_CHECKLIST.md for verification. Preserve your actual local .env, .venv, data and .git. Dependencies are unchanged from the preceding MongoDB version.

## Five review fixes

| Finding | Change |
| --- | --- |
| Multi-item UI edit could drop other lines | First-line editing now preserves every remaining product/quantity and refuses a duplicate SKU selection |
| Ambiguous draft response could create a duplicate | UI retains the entire pending payload/source_key across retry and same-tab reload; in-flight second submit is blocked; changed payload with reused source_key returns 409 |
| Owner confirming WhatsApp draft did not notify customer | Owner confirmation/hold queues the appropriate response once for WhatsApp-channel orders |
| Repeated return/refund message could claim it was pending again | New-message repeat reports the stored current decision, reason and manual payment requirement |
| Provider acceptance/failure receipts were incomplete | Transactional worker claims/finishes, ACCEPTED state, signed status callbacks, buffered/deduplicated delivery_events and failure classification |

## Existing functionality retained

| Area | Status or boundary |
| --- | --- |
| MongoDB order/customer/catalog/history | Implemented with collections, indexes and stable string IDs |
| Confirmation, stock and booked sale | One MongoDB transaction with conditional deduction and operation uniqueness |
| Draft/edit/payment/summary | Implemented; customer explicitly chooses COD/BANK_TRANSFER |
| Risk hold and owner review | Explainable rules; approval rechecks stock |
| Cancellation and return/refund | Ownership, eligibility, required rejection reasons, accepted-return stock restoration |
| Financial safety | Active/completed return/refund blocks a second cancellation reversal |
| Connection diagnostics | Configuration/auth/DNS/TLS/permission checks without printing URI/credentials |
| Legacy data transfer | Read-only SQLite snapshot into empty target, IDs/history preserved |
| Deployment/team boundaries | Single business, demo auth, full-order returns, manual payment/courier integration |

## Verification evidence and limits

Python offline domain, migration, diagnostic and mocked provider tests and Node UI regression tests are supplied. These cover business decisions, retry payload preservation, non-destructive editing and receipt processing without making real WhatsApp sends.

Verified on 15 November 2026: **56 Python offline/mocked checks and 10 Node UI regression tests passed** (66 total). Python discovery reported 59 entries with 3 skipped entries: the gated order/API integration module and two migration integration tests. Those represent **34 real MongoDB integration cases still to run**; skipped cases are not passing results. Python source compilation also passed.

**Live MongoDB transaction/concurrency verification and real WhatsApp transport were not executed in the generation environment.** MongoDB/PyMongo were unavailable and downloads were blocked. Earlier SQLite passing results are historical. Mocked or skipped cases must not be reported as passed live integration.

Run Python checks:

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

Run UI regression tests (Node.js is only needed for this command):

    node --test tests/ui_orders.test.cjs

Check the configured application database:

    .\.venv\Scripts\python.exe -m scripts.check_mongodb

Set TEST_MONGODB_URI to a development replica-set/Atlas URI, then run all required Python checks including real integration:

    .\.venv\Scripts\python.exe -m scripts.run_mongo_tests

The runner refuses missing test configuration or skipped cases. Temporary shopsync_uc4_test_* databases require create/drop permission; the application database is not the test target. Follow the manual/browser cases in FIXES_CHECKLIST.md and confirm persistence after a server restart.

## Team integration handoffs

| Module | Connection needed |
| --- | --- |
| UC-1 | Real owner/business authentication, permissions and delivery/risk/return settings |
| UC-2 | Route owner requests to the UC-4 API and use committed results |
| UC-3 | Natural-language requests, trusted customer identity and one agreed WhatsApp receive/send path |
| React | Connect team UI to endpoints and final authentication |
| Products/inventory | Agree collection/ID schema and one stock-update authority |
| Finance/gateway | Consume accounting records; actual transfer/reconciliation remains separate |

## Remaining limitations

- Single-business deployment; full tenant authorization is not implemented.
- One full-order return/refund per order; partial-item refund is not implemented.
- Accounting adjustment does not move money.
- Shipment status is owner-entered.
- Real WhatsApp setup/public webhook, template sending and live verification remain.
- Shared team collections/IDs need agreement; the importer does not merge into populated team data.
- Code-only ZIP excludes .env/.venv/data/.git. Full ZIP retains the original uploaded environment/data snapshot, not current local files or Atlas records.
