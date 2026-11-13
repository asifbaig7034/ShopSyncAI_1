# ShopSync AI - owner account, orchestrator, customer inbox and orders

The owner app includes UC-3 customer inbox and UC-4 orders. See
[the integration checkpoint](docs/uc3-uc4-integration.md) for the local
demo flow and [real agent events](docs/uc2-real-events.md) for how committed
order and stock changes reach UC-2.

This repository implements **UC-2, Send Request to Orchestrator**, and the
UC-1 owner-account flows (signup, login, logout, settings and password recovery). The Business Owner
types a natural-language request into a small React chat. FastAPI checks an
expiring owner session (or the older local-only demo token for verification), identifies relevant enabled handlers,
obtains saved data-backed results, and displays one combined answer. Owner
requests, responses, approval proposals, and agent events are recorded in
MongoDB. UC-4 order/product writes use a separate SQLite ledger; a
transactional SQLite event outbox delivers committed order/stock changes to
MongoDB with retries. Live customer WhatsApp sending, shipping, payments,
social publishing, and the remaining specialist agents are later work. Staff
accounts, Instagram, Facebook, courier and payment integrations are later
UC-1 steps. WhatsApp account read access can now be verified; this does not
activate WhatsApp customer messaging or webhooks.

The owner can edit the brand name, contact, currency and preferred language in
**Settings**, and turn the two working read-only agents on or off. Settings
are saved for the signed-in real owner. Turning both agents off prevents owner
chat from answering until one is enabled. Other agents remain future work.
Currency and language are saved as preferences for later flows; changing the
currency does not convert existing demo product/order prices or the current
agent replies, which still display their stored PKR amounts.

The two working read-only domains are Customer & Commerce (saved orders)
and Supply Chain & Operations (saved products and stock). They use the UC-4
ledger for a real owner and seeded sample data for the older local demo owner.
The eventual architecture has six specialist agents. These two domains do
not send live external messages or make business changes.

## Requirements

- Python 3.11+ and Node.js compatible with the installed Vite release.
- Docker is **optional** for persistent local MongoDB, the free local Ollama
  LLM and the Mailpit test inbox; CPU inference may be slower than the SRS routing target.
- UC-2 needs a configured, verified LLM for its normal flow. Choose local
  Ollama without a key or use Gemini when your Google project allows it.

## Run locally (Linux/macOS)

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the generated token into the `SHOPSYNC_DEMO_OWNER_TOKEN` line of `.env`.
Do **not** commit `.env`. For the simplest demo leave `MONGODB_URI` empty; the
server automatically seeds an **in-memory** Mongo-compatible sample database,
whose contents disappear when the backend restarts. Then start FastAPI:

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000/docs` to inspect the API. In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal (`http://127.0.0.1:5173`).
Create the owner account for your brand, then log in from the web app. Passwords
are Argon2-hashed in MongoDB; only a digest of the random 12-hour session token
is stored there. The web app keeps the returned bearer token in memory, so a
page reload requires logging in again. Account creation is restricted to the
local machine and to one real owner account for this single-brand prototype.
The seeded fictional demo owner remains separate for the existing UC-2 verifier.

## UC-1 first step: owner account

Start MongoDB, install the new backend dependencies, and restart FastAPI before
opening the frontend:

```powershell
docker compose -p shopsync-ai up -d mongo
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Create the account on `http://127.0.0.1:5173`, enter your own email and a
password of at least 12 characters, then open Owner Chat. A new owner's
products and orders are initially empty: the fictional Cotton Shirt and
DEMO-1001 belong to the separate seeded demo owner. Add your brand's records
under your new owner ID in MongoDB until product/order forms exist. Password
recovery uses a single-use code sent by configured SMTP (see below).

## UC-1 password recovery (local email demo)

Start the optional Mailpit inbox alongside MongoDB, then set `SMTP_HOST=127.0.0.1`
in your **existing, private** project-root `.env` file. If absent, also set
`SMTP_PORT=1025` and `SMTP_FROM=shopsync@localhost`. Do not replace your
existing `.env` or commit it. Restart FastAPI after changing `.env`.

```powershell
docker compose -p shopsync-ai up -d mongo mailpit
```

Open `http://127.0.0.1:8025` to inspect the local test inbox. From the app's
login screen choose **Forgot password?**, submit the real owner's email, open
the message in Mailpit, copy the code into the app, and choose a new password.
After a successful reset log in again; earlier owner sessions are invalid.
The same request confirmation is shown whether or not an owner uses that email.
Mailpit captures messages locally and does **not** deliver them to real email
inboxes. Its SMTP and web ports are bound to loopback in `compose.yaml`. To
deliver real email, configure an authenticated TLS SMTP provider and use a
private deployment; do not expose this local account prototype on a public
network. The reset code is never returned by the API or displayed in the app.

## UC-1 WhatsApp Business API read-access check

The owner Settings screen can verify that a Meta access token can list a
specified phone number under a specified WhatsApp Business Account (WABA).
This needs your WABA ID, phone-number ID and a Meta-issued access token; no
such credentials are included in the repository. The backend calls Meta's
Graph API before saving the token and shows **Meta read access verified** only
when that exact number appears. This confirms read access, not permission to
send customer messages, a registered phone number or a working webhook;
UC-3 must implement and test those separately.

Install the current backend requirements if needed, then create an encryption
key **once** from the project root:

```powershell
python -m pip install -r backend/requirements.txt
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the generated key into `INTEGRATION_ENCRYPTION_KEY=` in your existing
project-root `.env`. Keep it private, do not commit `.env`, and do not replace
this key after connecting: without it, the saved token cannot be decrypted.
Keep `META_GRAPH_VERSION=v26.0` unless Meta instructs you to use another
supported version. Restart FastAPI; in the owner Settings screen enter the
three values and choose **Verify and save**. The token is sent directly to
your backend and is never returned by the settings/status API. **Recheck
access** calls Meta again, while **Remove saved connection** deletes only the
local credentials and does not disconnect or deregister the phone number at
Meta. Without genuine Meta credentials, leave the status **Not connected**;
no mock connection is presented as live.

## Run locally (Windows PowerShell)

From the project root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the generated value in `.env` as above. In the first terminal:

```powershell
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

The Vite server requires port **5173** because the backend permits that
development origin. If the port is occupied, stop the old Vite process before
running `npm run dev` again; Vite will report the conflict rather than silently
opening a different port that cannot call the backend.

If PowerShell blocks virtual-environment activation, use
`.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt` and
`.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload`
instead of changing your computer's script-execution policy.

## Try UC-2

Use **fictional** prompts with the seeded demo shop:

1. `Show products and stock` - routes to the available product and inventory
   handlers and combines results obtained from the sample database.
2. `Show recent orders` - reads the synthetic order sample.
3. `Show stock and orders` - returns two domain results in one reply.
4. `Show me` - requests clarification; your next reply uses the same
   conversation ID.
5. `Publish a social post` - tells you that the Social Media Agent belongs to a
   later use case; it must not claim a post was published.

To demonstrate FR-05 event coordination, open `http://127.0.0.1:8000/docs`,
choose **POST /api/owner/demo-events/low-stock**, click **Try it out**, and
enter `Bearer YOUR_TOKEN` in that operation's `authorization` header field.
The operation reads the fictional low-stock product and calls a read-only
Customer & Commerce event handler. **GET /api/owner/demo-events** shows the
stored event and agent response. Repeating the POST reuses its event ID, so
it does not create a second event or reprocess an acknowledged one. This is a
simulation: it does not notify a live customer or change stock.

Use **POST /api/owner/demo-events/demand-spike** to send a second, explicitly
fictional event from Customer & Commerce to Inventory & Logistics. Its order
count is made up for the demo, not inferred from actual orders. The read-only
receiver acknowledges it without changing product stock. A future agent can
subscribe to a declared event type by registering its handler in
`application/events/dispatcher.py`. Disabled agents do not receive events.
Failed subscribers can be retried by republishing the same event ID without
repeating successful acknowledgements. This is at-least-once delivery: future
subscribers that make business changes must deduplicate actions by event ID.

The response identifies its mode. **Local rules demo** means no AI provider
answered the request; this is a runnable prototype, **not full SRS UC-2
completion**. No action that changes a price, issues a refund, or publishes a
post is executed from chat. Proposals remain pending for an owner approval
flow to be completed in the relevant later use case.

## Verify the running UC-2 prototype (PowerShell)

Keep the backend running in one VS Code terminal. In **another** terminal,
from the project root with the same `.env` file:

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.verify_uc2
```

This local check makes a fictional owner request, confirms the two demo agents
produced one combined saved answer, and records acknowledged/deduplicated
synthetic low-stock and demand-spike events. It does not place orders, change stock, refund a
payment, or send messages to customers. For **either** configured LLM it
**fails** unless it receives a verified model classification:

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.verify_uc2 --require-llm
```

For a Gemini-only configuration, you can also require Gemini explicitly:

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.verify_uc2 --require-gemini
```

Run the existing backend tests from the same project-root terminal:

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests
```

The optional MongoDB persistence check is manual: once a conversation appears
in the browser, restart FastAPI, re-enter the local owner token, then reload
the page. Your existing chat should load from the database. This only works
when `MONGODB_URI` points to a running MongoDB server.

## Free local LLM: Ollama (no Gemini key, no cloud account)

Run from the project root with Docker Desktop running. MongoDB may already be
running; this command adds only the optional local Ollama service:

```powershell
docker compose up -d ollama
docker compose exec ollama ollama pull qwen2.5:1.5b
```

The CPU-capable model download is about **986 MB**; the Docker image and runtime
need additional disk/memory. The model stays in a persistent Docker volume.
Ollama's API listens on `127.0.0.1:11434`; it is not exposed to other
computers. In your **existing project-root** `.env`, add or update:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_TIMEOUT_SECONDS=60
```

Keep your existing owner token and `MONGODB_URI`; do **not** replace your
current `.env` with `.env.example`. Any Gemini key can remain in `.env`, but
is not used when `LLM_PROVIDER=ollama`. Restart FastAPI (Ctrl+C, then start
again), and run `python -m backend.scripts.verify_uc2 --require-llm` or the
PowerShell command above. Look for `PASS ollama classified` and a chat badge
`Local Ollama AI`. The model only selects intent labels; product and order
facts still come from your MongoDB data. This is a fictional, owner-only
prototype: do not treat it as the completed UC-1 or customer chat.

Before measuring UC-2 routing, preload Ollama and prime the classifier with
one fictional request. Run these from the project root after
`docker compose up -d ollama` and the model pull:

```powershell
python -m backend.scripts.warm_ollama
```

Keep FastAPI running in a second terminal, then measure three fictional owner
requests in the first terminal:

```powershell
python -m backend.scripts.measure_uc2_routing
```

The warm-up's first classification may take longer than three seconds; it is
completed before any owner chat timing begins. Each API response includes
`routing_seconds`, measured after request classification and before the agents
process it. A run passes the 3-second
target only when all three requests use `mode=ollama` and each routing time is
at most 3 seconds. Preloading and `keep_alive=30m` avoid the first-request
model load, but they do not guarantee fast enough CPU inference; if a run is
slow, report the measured values rather than claiming this SRS target is met.
The preloaded model uses local RAM while it stays loaded. Run the warm-up again
after Ollama restarts or the keep-alive expires. If the model is missing, run
the pull command again.
If Ollama is not running, the app truthfully falls back to `Local rules demo`
and `--require-llm` fails instead of claiming AI was used.

## Optional Gemini free-tier LLM for fictional demo prompts

Google currently lists free input/output usage for
[`gemini-3.5-flash-lite`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite),
subject to your project and usage limits; check the
[official pricing](https://ai.google.dev/gemini-api/docs/pricing) and your AI
Studio quota. To test the adapter, create a Gemini API key in Google AI
Studio, then set these **server-side** values in `.env`:

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-own-key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_SYNTHETIC_DEMO=true
GEMINI_CLASSIFY_TIMEOUT_SECONDS=8
```

Restart FastAPI after changing `.env`. Never put the key in `frontend/.env`,
`VITE_` variables, the chat box, or Git. The adapter classifies intent; product
facts and agent outcomes still come from the shared database. This one-shot
classification sets `store=false` so provider-side interaction storage is
not requested. The timeout above bounds both network attempts together:
requests exceeding the SRS three-second routing target are visibly warned
about, and an unavailable model falls back to **Local rules demo**, never a
falsely labelled Gemini reply. Run `--require-gemini` above to verify actual
LLM routing; if it fails, check your key, model access, free-tier quota and
network connection. The model's response time depends on the external service
and cannot be guaranteed by this prototype.

**Free Gemini usage is restricted to fictional/synthetic owner prompts.**
[Google's unpaid-service terms](https://ai.google.dev/gemini-api/terms)
explicitly prohibit submitting sensitive, confidential, or personal content
and say submitted content may be used to improve products or reviewed by
people. The adapter rejects some obvious personal-data patterns, but that
filter cannot guarantee privacy. Do not submit real customer conversations,
phone numbers, addresses, order details or confidential business information
through the free-tier demo. A real deployment requires an appropriate provider
plan and a separate privacy review.

## Optional durable MongoDB history

The zero-setup in-memory demo resets on backend restart. To persist data locally
if Docker is installed:

```bash
docker compose up -d mongo
```

Set `MONGODB_URI=mongodb://localhost:27017` in `.env`, restart the backend, and
seed **synthetic** examples with:

```bash
python -m backend.scripts.seed_demo
```

On Windows, run `docker compose up -d mongo` from the project root, set the
same `MONGODB_URI`, and seed with
`.\.venv\Scripts\python.exe -m backend.scripts.seed_demo`.

The application does not silently switch to an in-memory database if your
configured MongoDB server is unavailable; fix the connection or return to an
explicit demo configuration instead. This development Compose service binds
MongoDB to localhost only and does not provide production authentication.

## Verify the backend

```bash
python -m pytest -q backend/tests
```

See [UC-2 SRS mapping](docs/uc2-srs-mapping.md) for which requirement is met
by each module and which requirements remain for later implementation.

## Complete team file structure

This repository includes every file in the approved project layout. Only **UC-2 is implemented**; the other use-case source, test, frontend, Docker and future documentation files are intentionally **empty (0 bytes)** for your teammates to implement. Empty routes/pages are **not registered** in the FastAPI or React entry points, so the existing UC-2 demo continues to run. Keep `backend/requirements.txt`: the blank `backend/pyproject.toml` is a placeholder, not an installation file. Add approved SRS and diagrams to `docs/srs/` and `docs/diagrams/` yourself after deciding repository visibility. Never commit `.env`, real credentials, `.venv` or `node_modules`.
