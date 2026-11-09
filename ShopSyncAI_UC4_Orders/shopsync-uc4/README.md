# ShopSync AI — UC-4 Manage Orders

A runnable implementation of the **Manage Orders** use case in the ShopSync AI FYP specification. It includes an owner workspace, an order API, a customer-command simulator, optional Meta WhatsApp Cloud API webhooks and text replies, a SQLite database, and automated order-integrity tests. No AI service or payment credentials are needed for the local demonstration.

See [PROGRESS.md](PROGRESS.md) for the current UC-4 requirement checklist, tests, and integration handoffs.

## Update your existing folder (Windows)

Keep using your current `ShopSyncAI_UC4_Orders/shopsync-uc4` folder. To apply the companion **code-only update ZIP**, stop the server with Ctrl+C, extract the ZIP, then copy its `app`, `static`, and `tests` directories and `README.md`, `PROGRESS.md`, and `requirements.txt` into the same existing `shopsync-uc4` directory. Accept replacement of the code files. The update ZIP does **not** contain `data` or `.venv`, so your orders/products and existing Python environment stay in place. The full updated archive is supplied separately as a backup and retains the attached database and environment.

This update adds an explicit Pydantic dependency. From your existing `shopsync-uc4` directory, run the following **once after applying this update** (keep the existing `.venv`):

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt
```

Then set `SHOPSYNC_OWNER_TOKEN` in the current PowerShell terminal and start Uvicorn using the commands below. Future code-only updates do not need a new environment; install packages again only when `requirements.txt` changes. The `.gitignore` excludes `data/` and `.venv/` from Git; check `git status` before committing the UC-4 code on your own branch.

## Start in five minutes

Requirements: Python 3.10+ and `pip`.

```bash
cd shopsync-uc4
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SHOPSYNC_OWNER_TOKEN="replace-this-with-a-long-random-secret"
python -m uvicorn app.main:app --reload
```

On Windows PowerShell, run these commands inside the `shopsync-uc4` folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SHOPSYNC_OWNER_TOKEN="replace-this-with-a-long-random-secret"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The project requires Pydantic 2.12 or newer because its `TypeAdapter` and `model_dump()` APIs are used by the app and FastAPI. If an existing Windows environment reports `ImportError: cannot import name 'TypeAdapter'`, run `.\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt` and restart the server.

Open **http://127.0.0.1:8000/**. Enter the owner token from the terminal command. Add a product on **Catalog**, then create an order on **Orders**. The **Chat simulator** lets you try the customer flow before connecting WhatsApp. API documentation is at **http://127.0.0.1:8000/docs**. The database is created at `data/shopsync.sqlite3`; set `SHOPSYNC_DB` to change its location.

## Demonstration flow

1. Add SKU `TEE-01`, price `1500.00` PKR and stock `2`.
2. In Chat simulator, use phone `923001234567` and send `ORDER TEE-01 1 | 12 Main Street, Lahore | COD`. You can use `BANK_TRANSFER` instead of `COD`. The draft reply shows the customer's details, item price and quantity, delivery address, payment method, and total **before** confirmation.
3. Review the summary. Copy its draft ID and send `CONFIRM ID` with that ID. Check that stock becomes `1` and the order has a recorded risk score.
4. Send `STATUS ID`, then `CANCEL ID`; stock returns to `2` and a cancellation reverses the booked sale.
5. To demonstrate risk review, start the server with `SHOPSYNC_RISK_AMOUNT_CENTS=100000` (PKR 1000). Place a PKR 1500 order and confirm it. It stays on hold until the owner approves; stock is checked again at approval time.
6. To demonstrate a return, confirm and mark an order shipped, send `RETURN ID Wrong size` from the same customer number, approve the request, and then select **Item accepted**. Stock is restored only at that final step.

Commands are `ORDER SKU QTY | address | COD`, `EDIT ID SKU QTY | address | BANK_TRANSFER`, `CONFIRM ID`, `STATUS ID`, `CANCEL ID`, `RETURN ID reason`, and `REFUND ID reason`. Both order commands accept either `COD` or `BANK_TRANSFER`, and an omitted payment choice asks the customer to provide one. Editing a draft displays a fresh full summary. The simulator accepts one SKU per order; the owner API supports several distinct products in one order.

## Order rules

| Event | Result | Stock and finance |
| --- | --- | --- |
| Create or edit draft | `DRAFT` | No change |
| Confirm with low review score | `CONFIRMED` | Deduct stock and book sale atomically |
| Confirm with high review score | `RISK_HOLD` | No change until owner approval |
| Approve held order | `CONFIRMED`, after checking stock again | Deduct stock and book sale atomically |
| Reject held order | `REJECTED` | No change |
| Cancel an unshipped confirmed order | `CANCELLED` | Restore stock and reverse booked sale once |
| Approve a refund request | `COMPLETED` | Book a refund adjustment; owner handles actual payment |
| Approve a return request | `APPROVED_AWAITING_RECEIPT` | No change until inspection |
| Accept returned item | `COMPLETED` | Restore full-order stock and book refund adjustment once |

The owner must enter a reason to reject a return or refund request or a returned item during inspection. The reason is saved in the request record, included in the customer notification for a live WhatsApp order, and available through the customer's `STATUS ID` command in the simulator.

Order item names and prices are stored as snapshots. Stock never becomes negative. A database transaction groups order confirmation with stock and booked-sale changes. A source key makes draft creation idempotent; status transitions and unique stock/finance records prevent duplicate changes. Signed incoming WhatsApp messages are deduplicated by message ID. The audit history records order and approval decisions.

The risk score is a transparent **review indicator**, not a fraud diagnosis. Default rules: total at least PKR 20,000 adds 60 points; six or more items adds 25; two previous cancellations adds 25. A score of 50 or greater holds the order. Configure `SHOPSYNC_RISK_AMOUNT_CENTS`, `SHOPSYNC_RISK_THRESHOLD`, and `SHOPSYNC_RETURN_DAYS` (default 14) through environment variables. The request window starts at shipment for shipped orders, otherwise at order creation.

## Optional WhatsApp Cloud API connection

Configure a Meta app, WhatsApp Business Account, business phone number and webhook subscription. Set these values on the server:

```bash
export WA_ACCESS_TOKEN="your-system-user-access-token"
export WA_PHONE_NUMBER_ID="your-numeric-phone-number-id"
export WA_GRAPH_VERSION="your-supported-graph-api-version"
export WA_APP_SECRET="your-meta-app-secret"
export WA_VERIFY_TOKEN="your-private-webhook-verification-token"
```

Register `GET/POST https://YOUR_PUBLIC_HOST/webhooks/whatsapp` as the webhook callback. Subscribe to incoming messages. GET checks the verification token; POST verifies `X-Hub-Signature-256` with the Meta app secret. The adapter accepts text commands from the sender's WhatsApp number and stores its reply in a delivery outbox. Successful outbound messages use Meta's `/{phone-number-id}/messages` endpoint. Failed sends are retried up to five times, then require owner review. A send interrupted mid-request is marked **UNCERTAIN**, for manual inspection before retrying.

Free-form text replies are limited to an open customer-service window. When an owner decision occurs outside that window, the outbox marks the message **NEEDS_TEMPLATE** instead of attempting an invalid text send. Create an approved template and connect its delivery in a later integration milestone. WhatsApp setup, credentials, a publicly reachable deployment, permissions, and template approval cannot be supplied by this archive. See [Meta's official Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api) and [WhatsApp's business messaging policy](https://whatsappbusiness.com/policy/) for account setup and message-window rules.

## API map

Owner endpoints require `Authorization: Bearer <SHOPSYNC_OWNER_TOKEN>`.

- `POST /api/products`, `GET /api/products`, `POST /api/products/{id}/restock` — small catalog needed for orders.
- `POST /api/orders/drafts` — create an order; provide a unique `source_key` for retries.
- `PATCH /api/orders/{id}/draft` — edit a draft's products, quantities, address, or payment method.
- `POST /api/orders/{id}/confirm`, `/risk-decision`, `/cancel`, `/ship` — order transitions.
- `POST /api/orders/{id}/returns` — request a full-order return or refund.
- `POST /api/returns/{id}/decision`, `/inspect` — owner decisions and item receipt.
- `GET /api/orders`, `/api/orders/{id}`, `/api/finance`, `/api/audit`, `/api/notifications` — owner views.
- `POST /api/chat/simulate` — locally authenticated simulation; accepts a unique `message_id`.
- `GET/POST /webhooks/whatsapp` — optional Meta verification and signed inbound messages.

Example owner-created draft (the product ID comes from `GET /api/products`):

```json
{
  "phone": "923001234567",
  "name": "Ayesha",
  "delivery_address": "12 Main Street, Lahore",
  "payment_method": "COD",
  "items": [{"product_id": "PRODUCT_ID", "quantity": 1}],
  "source_key": "your-unique-client-request-id"
}
```

## Run tests

```bash
python -m unittest discover -s tests -v
```

The tests cover owner authorization, repeat requests, risk holds, stock rechecks, competing confirmations for the last item, cancellations, returns, customer isolation in chat, signed webhook deduplication, payment selection, full draft summaries, and required rejection reasons.

## Project boundaries

This is a working **single-business UC-4 prototype**. The owner bearer token is a local-project authentication boundary; replace it with UC-1 accounts and role-based permissions before deployment. Product management is deliberately small because UC-5 is a separate use case. The chat command adapter is a narrow bridge to UC-3, not a multilingual conversational agent. The orchestrator belongs to UC-2.

Financial entries represent **booked order activity**, not cash collected. Approving a refund creates an accounting adjustment and marks payment handling for manual action; it does not move money through a gateway. Returns and refunds apply to the entire order, with one request per order in this prototype. Shipping status is entered by the owner, pending the later courier integration. Live WhatsApp needs valid business credentials and approved templates for messages outside the service window.
