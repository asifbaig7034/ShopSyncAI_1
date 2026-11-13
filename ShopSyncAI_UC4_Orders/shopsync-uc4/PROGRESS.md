# UC-4 Manage Orders Progress

Updated: 13 November 2026

## Current result

The runtime now uses **MongoDB through PyMongo**. SQLite is only an optional read-only import source. Python/FastAPI endpoints and the command simulator remain. The current HTML/CSS/JavaScript screen demonstrates the API; the team's React frontend still needs connecting.

Keep the same working folder, .venv and data. Install new requirements once. SETUP_WINDOWS.md explains connection and optional old-data migration.

## Implementation status

| Area | Status | Evidence or boundary |
| --- | --- | --- |
| MongoDB persistence | Implemented | PyMongo sessions/collections/indexes; no SQLite runtime fallback |
| Atomic order/stock/sale | Implemented; live verification pending | One transaction, conditional stock decrement and unique operation indexes |
| Owner/customer drafts and editing | Implemented | APIs, owner form, customer commands |
| Payment choice/full summary | Implemented | Explicit COD/BANK_TRANSFER; address/payment/items/customer/total before confirmation |
| Rule-based risk and review | Implemented | Hold, approval stock recheck and rejection |
| Status/cancellation | Implemented | Customer ownership check; stock/finance reversed once |
| Return/refund decisions | Implemented | Eligibility, full-order request, approval/rejection and item inspection |
| Rejection reason | Implemented | Required for request/inspection rejection; saved reason returned by STATUS |
| Refund/cancellation safety | Improved | Active/completed request blocks cancellation; decisions recheck order state |
| Sender isolation | Improved | Reused inbound ID from another sender is rejected |
| MongoDB outbox | Implemented; live delivery pending | Atomic lease, attempt/outcome records, ambiguous/expired sends require review |
| SQLite record import | Implemented | Read-only consistent snapshot, stable IDs/items, empty-target atomic import and verified rerun |
| Windows/schema instructions | Documented | SETUP_WINDOWS.md, .env.example, MONGODB_SCHEMA.md |

## Verification evidence and limits

The older SQLite build passed 13 tests on 9 November 2026. **Those results do not verify this MongoDB migration.**

The generation environment had no MongoDB server/PyMongo installation, and downloads were blocked. Live MongoDB transaction, concurrency and API integration tests **were not executed here**. **16 offline domain tests and 6 offline migration tests passed** in the generation run. These check importer/business behavior; an in-memory test cannot prove MongoDB guarantees. The real suite currently has 25 order/API cases and 2 migration cases pending execution on MongoDB, including competing confirmations, cancellation/refund races and worker leases. Python compilation and JavaScript syntax checks also passed.

Run:

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

Use python -m scripts.check_mongodb to check the configured application connection, indexes and a real transaction. Then set TEST_MONGODB_URI to a development replica-set/Atlas URI and run:

    .\.venv\Scripts\python.exe -m scripts.run_mongo_tests

Skipped integration tests are pending, not passed. Also perform the README browser demo, check persistence after restart and test WhatsApp when credentials are available.

## Integration handoffs

| Module | Connection needed |
| --- | --- |
| UC-1 accounts/settings | Real owner/business permissions and delivery/risk/return settings replace demo token/configuration |
| UC-2 orchestrator | Route requests to UC-4 APIs and use committed results |
| UC-3 customer chat | Natural-language operations, trusted customer identity, one agreed WhatsApp receive/send path |
| React dashboard | Connect UI to endpoints/authentication |
| Product/inventory | Agree MongoDB collection/ID contract and one stock-update authority |
| Finance/gateway | Consume accounting records and implement actual transfers/reconciliation |

## Remaining limitations

- Single-business deployment; tenant authorization is not complete.
- One full-order return/refund per order; no partial-item refunds.
- Accounting/payment choice does not transfer money.
- Shipment status is owner-entered; courier integration is separate.
- Real WhatsApp credentials/public webhook/template delivery and live tests remain.
- MongoDB schema must align with team collections; import is not a merge into populated team data.
