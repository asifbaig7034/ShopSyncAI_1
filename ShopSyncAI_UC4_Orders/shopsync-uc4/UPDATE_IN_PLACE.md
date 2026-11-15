# Apne existing UC-4 folder mein update karein

Updated: 15 November 2026

Is update ke liye **code-only update ZIP** use karein. Apna current team-repository folder hi working folder rakhein.

1. VS Code server terminal mein Ctrl+C dabayein.
2. Current shopsync-uc4 folder ka backup repository se bahar rakh lein.
3. Code-only ZIP ko temporary jagah extract karein. Uske andar ShopSyncAI_UC4_Orders → shopsync-uc4 kholein.
4. Extracted shopsync-uc4 ke code/documentation files aur folders ko apne current shopsync-uc4 mein copy/paste karein. Windows pooche to code files replace karein.
5. **Apni actual local .env, .venv, data aur .git ko preserve karein.** Code-only ZIP mein yeh chaar cheezen nahi hotin. .env.example ek example hai; use apni configured .env ke upar copy na karein.
6. Pichla MongoDB version already chal raha tha to is update ki dependencies same hain: naya venv ya dobara pip install zaroori nahi. Agar SQLite version se aa rahi hain ya module missing ho to existing .venv mein requirements install karein.

Full ZIP backup mein jo .venv/data hain, woh pehle attached project ka snapshot hai. Woh aapke aaj ke local files ya Atlas cloud records ka replacement nahi hai. Current local setup/data rakhne ke liye code-only update use karein.

## Update ke baad run karein

Isi existing shopsync-uc4 ke PowerShell terminal mein:

    .\.venv\Scripts\python.exe -m scripts.check_mongodb

Connection/index/transaction check successful ho to:

    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

Browser mein http://127.0.0.1:8000/ kholein. Updated JavaScript load karne ke liye Ctrl+F5 karein. Apni existing .env wala owner token use karein.

Is update ke liye purane SQLite records dobara import na karein. MONGODB_URI aur MONGODB_DB same hon to existing MongoDB records wahi milenge.

## Tests

Doosre terminal mein Python checks:

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

UI regression tests, agar Node.js installed hai:

    node --test tests/ui_orders.test.cjs

Node sirf UI tests ke liye chahiye; FastAPI app ke liye nahi. Real MongoDB tests ke liye apni DEVELOPMENT cluster URI reuse karne ki line .env mein add karein:

    TEST_MONGODB_URI="${MONGODB_URI}"

Phir:

    .\.venv\Scripts\python.exe -m scripts.run_mongo_tests

Test database user ko temporary shopsync_uc4_test_* databases create/drop karne ki permission chahiye. Missing URI ya skipped cases ka matlab live verification complete nahi hui. Five fixes ki manual/automated checking FIXES_CHECKLIST.md mein hai.

## Apni branch par commit

Current working folder se branch check karein:

    git branch --show-current

Aapki branch uc4 ho to code/docs stage karein:

    git add app static scripts tests README.md PROGRESS.md SETUP_WINDOWS.md MONGODB_SCHEMA.md FIXES_CHECKLIST.md UPDATE_IN_PLACE.md .env.example requirements.txt
    git diff --cached --stat
    git commit -m "Fix UC4 editing retries notifications and delivery status"
    git push origin uc4

.env, .venv, data ya credentials stage na karein. ZIP download ya local copy/paste se apne aap GitHub commit nahi hota.
