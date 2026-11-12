# UC-3 / UC-4 integration checkpoint

The `integration/uc3-uc4` branch already contains the independently developed
UC-3 and UC-4 applications. Their own folders do not register routes in the
root application. This checkpoint connects the **root** FastAPI and Vite apps,
the UC-1 owner session, and one owner workspace.

## Local demo flow

1. Start your existing MongoDB, or use the disposable local demo DB. Keep
   your private `.env`; optionally set `SHOPSYNC_DB` to a persistent SQLite
   path. The default is `data/shopsync-orders.sqlite3` relative to the root.
2. Start the backend from the repository root with
   `..\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload`.
   Start the root frontend from `frontend/` with `npm run dev`.
3. Sign in as the real UC-1 owner. Open **Orders** to add a product and create
   an order; open **Inbox** to simulate a fictional customer message. A
   customer does not receive an owner session.
4. Try an order command using the same phone in the Inbox. UC-4 owns order
   rules and stock; UC-3 stores the conversation and handles owner handover.
   Repeating a message ID must not create a second order.
5. Ask the UC-2 owner assistant about products or recent orders. The real
   owner reads the order ledger while the synthetic UC-2 verification owner
   retains its separate fixture data.

`/api/products`, `/api/orders`, `/api/returns`, `/api/conversations` and
`/api/demo/messages` share the root backend address. Owner endpoints require
the UC-1 real owner session. The customer simulator also requires that owner
session **from the owner's browser**, but stores distinct customer identities;
customers cannot call owner-only endpoints.

## Deliberate integration limits

- UC-4 retains its tested SQLite transaction engine for the order/product
  ledger. UC-1/UC-2 and the UC-3 inbox use MongoDB or an explicitly disposable
  local demo store. The SRS requirement for one shared data layer is **not yet
  met**. Back up SQLite and MongoDB. Moving orders to MongoDB later requires
  a transaction-capable replica set and a data migration. Do not run the
  separate UC-4 server against the same SQLite file as the root app.
- UC-1 WhatsApp read verification does not provide message sending/webhook
  authorization. Live WhatsApp inbound and outbound traffic remains disabled
  until signed webhook verification, an agreed single ingress path, provider
  permissions and delivery handling are implemented. Use the fictional Inbox
  simulator in the local demo.
- UC-4's original test harness uses a separate bearer token; integrated
  routes use UC-1 sessions instead. UC-3's original OpenAI setting is not
  copied into the root app, which retains UC-2's local Ollama option. Local
  customer replies use deterministic intent and catalogue rules for English,
  Urdu and Roman Urdu with owner handover when uncertain; UC-3 customer-side
  LLM classification is not yet integrated.
- UC-2 can now answer owner questions from the UC-4 product/order ledger, but
  UC-4 stock changes are not yet published automatically into UC-2's event
  dispatcher. Synthetic low-stock and demand event checks remain separate.
- This is a single-brand local prototype. Approvals in the local simulator
  do not charge customers or send external messages. Run the local demo with
  one backend worker; customer-command processing and owner handover use a
  per-process conversation lock, not a distributed queue.

## Verification

From the repository root in PowerShell (the Python environment for the
existing project lives in the parent directory):

```powershell
..\.venv\Scripts\python.exe -m pytest -q backend\tests
```

Then from `frontend/`:

```powershell
npm run build
```

After both pass, keep the integration branch for review. Merge into `main`
only after inspecting the owner screens, simulated conversation/order flow,
and the known two-store limit.
