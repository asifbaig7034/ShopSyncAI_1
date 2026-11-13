# ShopSync AI UC-4 Manage Orders with MongoDB

UC-4 is a separately runnable order module using **Python, FastAPI and MongoDB through PyMongo**. Products, customers, orders, stock movements, refund records, audit events and notifications now use MongoDB. SQLite is used only by the optional read-only importer for your older demo database.

**Start with [SETUP_WINDOWS.md](SETUP_WINDOWS.md).** It explains how to keep the same working folder and environment, connect MongoDB and preserve previous orders. [PROGRESS.md](PROGRESS.md) records verified and pending work. [MONGODB_SCHEMA.md](MONGODB_SCHEMA.md) describes collections and IDs for integration.

Requires Python 3.10+ and a transaction-capable MongoDB deployment. On a fresh Git clone without an environment, run `py -3 -m venv .venv` once before installing requirements. An existing working folder reuses its current environment.

## Update the same folder

Stop Uvicorn with Ctrl+C. Copy updated code into the existing team-repository shopsync-uc4 folder and replace code files. Keep the existing .venv, .env and data directory. Install the new MongoDB dependencies once:

    .\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt

Copy .env.example to .env only if .env does not already exist. Configure the connection as described in the Windows guide. Existing shell variables take precedence over .env.

## MongoDB connection

Multi-document transactions are required: use an Atlas cluster, a replica set, or a sharded cluster. A plain standalone MongoDB server is rejected at startup because order confirmation must save order, stock and sale changes together.

For Atlas, put the driver's connection string from your own cluster into .env:

    MONGODB_URI="mongodb+srv://DB_USER:ENCODED_PASSWORD@YOUR_CLUSTER_HOST/?retryWrites=true&w=majority"
    MONGODB_DB="shopsync_uc4"
    SHOPSYNC_OWNER_TOKEN="my-local-project-token"

The placeholder URI is not a working account. Follow the Windows guide for database-user and network-access setup.

If Docker is already installed, the bundled Compose file runs a local development replica set:

    docker compose up -d --wait

Use the local URI from .env.example:

    MONGODB_URI="mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true"

The named Docker volume retains data across restarts. Do not use docker compose down -v when you want to retain it. Compass can inspect MongoDB; installing Compass alone does not run a database server.

## Check the MongoDB connection

After configuring .env, run:

    .\.venv\Scripts\python.exe -m scripts.check_mongodb

This checks connection, required indexes and a real transaction, then prints product/customer/order counts. Resolve connection/setup errors before importing old data or starting Uvicorn.

## Previous SQLite records

Stop the old app and back up its data folder. Preview:

    .\.venv\Scripts\python.exe -m scripts.migrate_sqlite data/shopsync.sqlite3

If preview counts are correct, import into an **empty** target UC-4 database:

    .\.venv\Scripts\python.exe -m scripts.migrate_sqlite data/shopsync.sqlite3 --apply

The importer opens SQLite read-only, preserves IDs/stock/state/history, embeds order items and commits all records in one MongoDB transaction. It refuses populated target collections. A repeated import is a verified no-op only while both source and imported target remain unchanged; it is not synchronization. For a fresh demonstration, skip import and add products after startup.

## Start the app

From the existing shopsync-uc4 folder:

    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

Open http://127.0.0.1:8000/. Enter SHOPSYNC_OWNER_TOKEN from .env in the owner-token box and select Connect. Keep the terminal open. API docs are at http://127.0.0.1:8000/docs. The /api/health endpoint identifies the backend as mongodb.

On Linux/macOS, activate the existing environment and run equivalent python -m pip and python -m uvicorn commands.

## Demonstration

1. Add SKU TEE-01, name Demo Shirt, price 1500.00 PKR and stock 3 on Catalog. Use a new SKU if it exists.
2. In Chat simulator, use phone 923001234567, name Ayesha, and send:

       ORDER TEE-01 1 | 12 Main Street, Lahore | BANK_TRANSFER

3. Review customer, item/quantity/price, address, payment and total in the reply. Omitting the payment choice asks for one instead of creating a draft.
4. Copy the actual ID and send:

       EDIT ID TEE-01 1 | 15 Main Street, Lahore | COD

5. Send CONFIRM ID. A low-risk order becomes CONFIRMED, stock decreases once and a sale is recorded. Repeated confirmation does not deduct again.
6. Send CANCEL ID before shipment. Stock and booked sale are reversed once.
7. On a different confirmed order, send REFUND ID Wrong item. Reject with a reason or approve on Orders. Empty rejection reasons are refused. STATUS ID from the same customer shows the decision/reason.
8. For a return, mark another order shipped, send RETURN ID Wrong size, approve, then accept or reject the received item. Stock returns only after acceptance; rejecting an item also requires a reason.

An active/completed return or refund prevents cancellation from reversing the same sale again. Actual bank/gateway transfer remains manual.

The simulator accepts one SKU per order. The owner API supports 1–25 distinct products. Commands are ORDER, EDIT, CONFIRM, STATUS, CANCEL, RETURN and REFUND using the formats shown in the screen.

## Risk and eligibility

Default rules: total at least PKR 20,000 adds 60 points; six or more items adds 25; two previous cancellations adds 25. Score 50 or greater holds an order. Approval rechecks current stock.

| Variable | Default | Purpose |
| --- | --- | --- |
| SHOPSYNC_RISK_AMOUNT_CENTS | 2000000 | Amount rule, in integer minor units |
| SHOPSYNC_RISK_THRESHOLD | 50 | Risk-hold score |
| SHOPSYNC_RETURN_DAYS | 14 | Full-order request window |

For a risk demo set amount to 100000 in .env and restart. A PKR 1500 order should stay on hold with no stock/sale booking until approval. Restore 2000000 afterward. The return window starts at shipment for shipped orders, otherwise at creation.

## Tests

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

The generation run passed **16 offline domain checks and 6 offline migration checks**. These do not prove MongoDB transaction behavior. Real integration tests are explicitly skipped when TEST_MONGODB_URI is unset. To verify a real deployment, set that variable to a development replica-set/Atlas URI and run:

    .\.venv\Scripts\python.exe -m scripts.run_mongo_tests

The runner requires a configured URI, refuses a result containing skipped tests and uses temporary test databases. The live suite currently contains 25 order/API cases plus 2 migration cases, including concurrent confirmation, cancellation/refund races and delivery leases. Use a development test user permitted to create/drop those databases.

**Live MongoDB integration tests were not executed in the generation environment:** MongoDB/PyMongo were unavailable and downloads were blocked. The old SQLite version's 13 passing tests are historical, not proof of MongoDB concurrency or live WhatsApp delivery. Current evidence is recorded in PROGRESS.md.

## Optional WhatsApp

Configure WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID, WA_GRAPH_VERSION, WA_APP_SECRET and WA_VERIFY_TOKEN on the server. No provider credentials are included.

Register GET/POST https://YOUR_PUBLIC_HOST/webhooks/whatsapp. POST verifies X-Hub-Signature-256. Inbound IDs are deduplicated and checked against sender identity. The MongoDB outbox claims a lease, sends outside the order transaction and records status/retries. Ambiguous/interrupted sends become UNCERTAIN for review. Approved-template delivery outside the customer service window remains an integration handoff.

## API map

Owner endpoints currently require Authorization: Bearer followed by SHOPSYNC_OWNER_TOKEN.

- GET/POST /api/products; POST /api/products/{id}/restock
- GET /api/orders; GET /api/orders/{id}
- POST /api/orders/drafts; PATCH /api/orders/{id}/draft
- POST /api/orders/{id}/confirm, /risk-decision, /cancel, /ship
- POST /api/orders/{id}/returns with kind RETURN or REFUND
- POST /api/returns/{id}/decision, /inspect
- GET /api/finance, /api/audit, /api/notifications
- POST /api/notifications/{id}/retry; POST /api/chat/simulate
- GET/POST /webhooks/whatsapp; GET /api/health

Money uses integer minor units: PKR 1500 is 150000 cents. Entity IDs are strings. See MONGODB_SCHEMA.md before connecting other modules.

## Team boundaries

This remains a single-business UC-4 prototype. The local HTML/CSS/JavaScript screen tests the API; the team's React dashboard still needs connecting. UC-1 supplies actual login/permissions and business settings; UC-2 routes owner requests; UC-3 supplies natural-language operations and trusted sender identity.

Agree on MongoDB names, IDs and mappings before sharing products/customers. Avoid independent duplicate stock deduction. This version supports one full-order return/refund per order, manual shipment marking and accounting adjustments rather than gateway settlement.

Commit code/documentation on the existing branch. Keep .env, .venv and data out of Git.
