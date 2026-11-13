# UC-2: coordination from real order and stock changes

UC-2 routes an owner's natural-language question through Ollama to the two
currently enabled read-only agents. It also receives **committed** UC-4 order
and stock events without requiring the owner to click the synthetic demo
event buttons. The Owner workspace has an **Agent activity** tab showing these
events. A local customer simulator can create a genuine saved UC-4 order; this
does **not** send a live WhatsApp message.

## Flow

1. UC-4 confirms or cancels an order, puts it on risk hold, or updates stock.
   The order ledger and its `agent_event_outbox` row commit together in SQLite.
   A failed order transaction rolls back both changes.
2. The running API polls for queued events every two seconds. A read of
   `GET /api/owner/events` also drains due work for the signed-in real owner.
   The dispatcher assigns the event to the one registered brand owner in
   MongoDB, saves a stable event ID, then invokes enabled agent subscribers.
3. Order changes reach Inventory & Logistics; low or replenished stock reaches
   Customer & Commerce. These subscribers acknowledge the event and never
   change stock, refund an order or send a customer message. `risk.held` is
   visible to the owner but remains `unhandled` until UC-9's Risk & Trust agent
   exists. Its owner approval is handled through the UC-4 Orders screen.
4. Acknowledged deliveries are marked `DELIVERED` in SQLite. If MongoDB or a
   subscriber fails, the outbox keeps the event and retries with the same
   ID after a short backoff. Duplicate confirmations/cancellations do not
   create another event. Event payloads exclude phone, email and address.

The initial SQLite-to-Mongo delivery is at least once; no user-visible
business action runs in event handlers. A restarted app replays outstanding
events. MongoDB and SQLite are still separate stores; this is a reliable
bridge between the current UC-4 engine and UC-2, rather than a full migration
to one database.

## Verify on Windows

Run each command in the project root (`shopsync-ai`) with your existing
virtual environment. Keep your existing private `.env` file; do not copy a
new template over it.

```powershell
docker desktop start
docker compose -p shopsync-ai up -d mongo ollama
..\.venv\Scripts\python.exe -m pytest -q backend\tests
..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

If your `.venv` lives inside `shopsync-ai`, replace `..\.venv` above with
`.\.venv`. Run Uvicorn in its own terminal; if port 8000 is already occupied,
restart the earlier Uvicorn process instead of launching another copy.
Start the frontend from `shopsync-ai\frontend` in a second terminal:

```powershell
npm run dev
```

Sign in as the real brand owner. In **Orders**, add a fictional product with
stock of 5 and create/confirm a one-item order. Check **Agent activity**:
`order.confirmed` should be observed by Inventory & Logistics, and a
threshold crossing below the default `SHOPSYNC_LOW_STOCK_THRESHOLD=5` should
also produce `stock.low`, observed by Customer & Commerce. In **Owner chat**,
ask `Show stock and recent orders` to see the updated saved ledger. Cancel
that order to see `order.cancelled` and `stock.replenished`. An order held for
risk review needs the owner's decision in **Orders**; the event notification
does not approve it.

For a retry check, stop MongoDB, confirm another test order, restart MongoDB,
and inspect **Agent activity** after a short wait. The order should remain
saved during the outage and appear once MongoDB returns. This check is
optional; never stop a shared or production MongoDB instance.

Only the two integrated agents are functional here. Shipping, finance,
marketing and risk specialists are future use cases; automatic business
actions and a cold-model three-second LLM guarantee are not claimed.
