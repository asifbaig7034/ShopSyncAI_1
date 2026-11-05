# ShopSync AI - UC-2 Owner-to-Orchestrator prototype

This repository implements **only UC-2, Send Request to Orchestrator**. The
Business Owner types a natural-language request into a small React chat.
FastAPI checks a local demo owner token, identifies relevant enabled handlers,
obtains sample data-backed results, and displays one combined answer. Owner
requests, responses, approval proposals, and agent events are recorded in the
shared data layer. Real account login, customer WhatsApp, order execution,
shipping, payments, social publishing, and the rest of UC-1/UC-3-UC-10 are not
implemented here.

The two working **demo** domains are Customer & Commerce (saved sample orders)
and Supply Chain & Operations (sample products and stock). The eventual architecture
has six specialist agents. A report from a demo agent is still based on stored
sample records; it is not a live external integration.

## Requirements

- Python 3.11+ and Node.js compatible with the installed Vite release.
- Docker is **optional** for persistent local MongoDB and for the free local
  Ollama LLM; CPU inference may be slower than the SRS routing target.
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
Paste the **demo token** into the owner's local sign-in field. It is held only
in the open browser tab, not added to the frontend build or its environment.

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
produced one combined saved answer, and records an acknowledged/deduplicated
synthetic low-stock event. It does not place orders, change stock, refund a
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

The first CPU request can be slow while the model loads. Routing above three
seconds is reported in a warning; this local setup does not guarantee the SRS
performance target. If the model is missing, run the pull command again.
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
