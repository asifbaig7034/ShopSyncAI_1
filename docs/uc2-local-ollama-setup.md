# Add a free local LLM to your existing Windows UC-2 project

This patch updates only UC-2 code, its tests and documentation. It does not
include or change your `.env`, `.venv`, MongoDB volume, demo owner token or
teammates' empty use-case files. You must run commands from the existing
`E:\ShopSyncAI_UC2_Working_Upgrade\shopsync-ai` root.

1. Stop FastAPI with **Ctrl+C** in its VS Code terminal. Leave Docker Desktop
   running. Download the UC-2 Ollama patch ZIP and extract its **contents**
   into this existing `shopsync-ai` project root, replacing only matching
   files. In PowerShell, substitute your actual downloaded ZIP path:

   ```powershell
   Expand-Archive -LiteralPath "E:\Downloads\ShopSyncAI_UC2_Ollama_Patch.zip" -DestinationPath "E:\ShopSyncAI_UC2_Working_Upgrade\shopsync-ai" -Force
   Set-Location "E:\ShopSyncAI_UC2_Working_Upgrade\shopsync-ai"
   ```

2. Start the local Ollama Docker service, then download its approximately
   986 MB `qwen2.5:1.5b` model. MongoDB already running under this Compose
   project is preserved:

   ```powershell
   docker compose up -d ollama
   docker compose exec ollama ollama pull qwen2.5:1.5b
   docker compose ps
   ```

   The pull can take several minutes. An Ollama image and the model require
   additional disk space and RAM. This setup uses CPU; no GPU is required.

3. Edit the **existing** `.env`; keep its owner token and MongoDB URI. Add
   these four lines, or update existing lines with the same names:

   ```dotenv
   LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   OLLAMA_MODEL=qwen2.5:1.5b
   OLLAMA_TIMEOUT_SECONDS=60
   ```

   A previously configured `GEMINI_API_KEY` may remain, but Ollama mode does
   not send requests to Gemini. Never share the key or `.env`.

4. Start FastAPI again from the project root:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

   Leave that terminal running. Your React frontend at
   `http://127.0.0.1:5173` can stay open; reload it if necessary.

5. In **another** VS Code terminal from the project root, verify live local
   LLM classification and both saved demo agents:

   ```powershell
   .\.venv\Scripts\python.exe -m backend.scripts.verify_uc2 --require-llm
   ```

   Expect `PASS ollama classified this fictional owner request.`. The browser
   reply for `Show products, stock and recent orders` should say
   **Local Ollama AI** and include fictional Cotton Shirt and DEMO-1001 facts.
   The first CPU inference may take tens of seconds. A warning above three
   seconds records a performance-target miss; it does not mean that Ollama
   failed. The test exits nonzero if the reply was only **Local rules demo**.

6. Optional: run all backend tests from a third terminal:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q backend\tests
   ```

Only **UC-2 and two read-only demo agents** are implemented. Account login,
real order execution, WhatsApp/customer integrations and the remaining use
cases are separate team work.
