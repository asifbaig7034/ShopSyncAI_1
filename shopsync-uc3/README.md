# ShopSync AI UC-3 implementation starter

This project implements **UC-3 Chat with Business** from the supplied ShopSync AI SRS. It contains a React owner inbox and customer simulator, a FastAPI conversation service, and a MongoDB persistence adapter. UC-1 authentication and setup, UC-2 orchestration, and UC-4 order operations remain separate integration responsibilities.

The default demo runs without paid API keys. Demo replies use a small rule-based classifier and factual templates. They demonstrate the conversation workflow; they are not evidence of LLM accuracy or live WhatsApp connectivity. In-memory demo history disappears when the backend restarts. Choose MongoDB for persistence.

## Run the demo locally

Requirements: Python 3.12 or later, Node.js 22 LTS, and npm. MongoDB is optional for the first demo.

1. Open a terminal inside `backend` and create a virtual environment:

   ```sh
   python -m venv .venv
   ```

2. Activate it. On Windows PowerShell use `.venv\Scripts\Activate.ps1`; on macOS/Linux use `source .venv/bin/activate`.

3. Install the backend dependencies and copy the example configuration:

   ```sh
   pip install -r requirements.txt
   ```

   Copy `backend/.env.example` to `backend/.env`. Keep the demo values for your first run. Do not commit `.env`.

4. Start the backend from `backend`:

   ```sh
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

5. Open a second terminal inside `frontend`:

   ```sh
   npm install
   npm run dev
   ```

6. Open the address printed by Vite, normally `http://localhost:5173`. Enter `demo-owner-token` in the connection settings when using the unchanged demo configuration.

API documentation: `http://localhost:8000/docs`. Health information: `http://localhost:8000/api/health`.

## Run with MongoDB using Docker

If you have Docker Desktop or Docker Compose, run this from the project root:

```sh
docker compose up --build
```

Open `http://localhost:5173` and use the demo credential above. This starts MongoDB, the FastAPI backend and a built React frontend. Sample conversations survive a container restart because MongoDB uses a named volume. This Compose setup is a local demo with simulated messaging and local-only exposed ports.

You can copy the root `.env.example` to `.env` to adjust the Docker demo credential and LLM settings. This is separate from `backend/.env`, which is used by the local Python startup.

## Try your UC-3 workflow

Use the customer simulator to ask about a product listed in the catalogue. Try English, Urdu and Roman Urdu. Then ask to speak to a human. Check that the conversation moves to human mode, subsequent customer messages do not trigger bot replies, and the owner can reply. Resume the bot from the conversation controls.

Ask for a product that does not exist and check that the system asks for clarification. Ask to place an order and check the explicit handoff to the UC-4 integration; an unavailable integration must not produce a fake order confirmation.

## Scope and team boundaries

| Module | Owner | Connection to this code |
| --- | --- | --- |
| UC-1 Account and System Setup | Teammate | Replace the development dashboard credential with the team's authenticated owner and business context; supply connection and agent settings. |
| UC-2 Orchestrator | Teammate | Connect the complaint and inter-agent routing adapter. |
| UC-3 Chat with Business | You | Conversation intake, multilingual interpretation, database-grounded replies, history, owner review and takeover, and channel delivery. |
| UC-4 Manage Orders | Teammate | Connect order lookup and ordering/cancellation/return workflows; keep order mutations and approvals in UC-4. |

Product maintenance is UC-5, so the starter uses a clearly identified sample catalogue. Agree on product and customer identifiers before connecting to your team's shared database.

## Configuration and integration

See `backend/.env.example` for all supported environment variables and `docs/INTEGRATION.md` for API routes and integration boundaries. External provider credentials belong only on the backend. The React app accepts the development dashboard credential in memory and does not store it in browser storage.

The development credential is a single-business integration placeholder, not the UC-1 sign-in system. Real deployment needs the team's owner authentication, permission checks, encrypted credential management and HTTPS. This starter does not certify privacy/compliance or deployment readiness.

## Verification

Run the backend suite from `backend`:

```sh
python -m unittest discover -s tests -v
```

Build the frontend from `frontend`:

```sh
npm run build
```

The acceptance checklist is in `docs/ACCEPTANCE.md`, and completed checks are recorded in `docs/VALIDATION.md`. All 26 backend tests passed with in-memory storage and fake providers. Frontend source parsing passed; dependency access restrictions prevented a Vite build here. MongoDB, Docker and real provider connections still need local integration checks. The SRS targets of 10-second replies, 50 simultaneous conversations and at least 85% intent classification need measurements with your selected real providers and a representative multilingual test set.
