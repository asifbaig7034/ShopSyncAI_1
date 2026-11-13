# Run the MongoDB UC-4 update on Windows

Aap apna **wahi existing shopsync-uc4 folder** use karein. Is update ke liye naya venv banane ki zaroorat nahi. Server chal raha ho to Ctrl+C se band karein.

## 1. Updated code aur packages

Updated files current shopsync-uc4 mein copy/replace karein. Apne existing .venv, .env aur data ko rehne dein. VS Code mein isi folder ko kholein; requirements.txt nazar aani chahiye.

PowerShell mein ek baar:

    .\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt

Is se existing environment mein PyMongo aur required packages install/update honge.

## 2. MongoDB Atlas setup

Agar MongoDB server abhi nahi hai, [Atlas](https://www.mongodb.com/cloud/atlas/register) ka development cluster use kar sakti hain:

1. Apna Atlas account aur cluster banayein.
2. Database Access mein database user banayein. Yeh Atlas-login se alag hota hai. Apne development database ki read/write permissions dein.
3. Network Access mein apne PC ka current IP allow karein.
4. Cluster par Connect → Drivers → Python se URI copy karein. Database-user/password lagayein. Password ke special characters URI-encode hone chahiye; Drivers instructions follow karein.
5. Credentials ko GitHub, public screenshot ya chat mein paste na karein.

Compass data dekhne ka tool hai; database chalne ke liye Atlas cluster ya local server chahiye.

## 3. Connection file

Agar .env pehle se nahi hai:

    Copy-Item .env.example .env

VS Code mein .env edit karein:

    MONGODB_URI="mongodb+srv://APNA_DB_USER:APNA_ENCODED_PASSWORD@APNA_CLUSTER_HOST/?retryWrites=true&w=majority"
    MONGODB_DB="shopsync_uc4"
    SHOPSYNC_OWNER_TOKEN="my-local-project-token"

Actual Drivers URI lagayein; upar wali example as-is connect nahi hogi. Abhi alag empty UC-4 development database use karein. Team ke populated database par import na chalayein.

.env startup par load hoti hai. Terminal mein pehle se variable set ho to terminal ki value use hoti hai. Connection badalne ke baad server restart karein.

## 4. Connection check aur purane products/orders

Pehle connection verify karein:

    .\.venv\Scripts\python.exe -m scripts.check_mongodb

Successful connection/transaction result aaye to aage migration karein. Yeh command customer records print nahi karti; counts dikhati hai.

Purana server band rakhein, data folder ka backup rakhein. Preview:

    .\.venv\Scripts\python.exe -m scripts.migrate_sqlite data/shopsync.sqlite3

Counts theek hon to empty MongoDB target mein import:

    .\.venv\Scripts\python.exe -m scripts.migrate_sqlite data/shopsync.sqlite3 --apply

Products, customers, IDs, stock, orders aur history preserve hoti hai. Source SQLite read-only hai; populated target overwrite nahi hota. Migration sirf ek baar karein, har startup par nahi. Fresh demo ke liye yeh step skip kar sakti hain.

## 5. Server chalayein

    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

Successful startup aaye to [http://127.0.0.1:8000/](http://127.0.0.1:8000/) kholein. Owner token mein .env wala token dein aur Connect dabayein. Terminal khula rehne dein.

.env mein token saved ho to har naye terminal mein token command dena zaroori nahi. Agle din isi folder se Uvicorn command chalayein; MongoDB cluster/server available hona chahiye.

## 6. Saved data check

Catalog mein product add karein. Atlas data browser ya Compass mein shopsync_uc4 → products dekhein. Naya record aana chahiye.

Chat mein apne actual SKU se:

    ORDER TEE-01 1 | House 12, Lahore | COD

Draft ID se CONFIRM ID bhejein. MongoDB ki orders, stock_movements aur financial_entries collections mein related records dekhein. Server restart ke baad data rehna chahiye.

## Docker alternative

Docker installed ho to Atlas ke bajaye bundled database start karein:

    docker compose up -d --wait

.env mein:

    MONGODB_URI="mongodb://127.0.0.1:27017/?replicaSet=rs0&directConnection=true"
    MONGODB_DB="shopsync_uc4"
    SHOPSYNC_OWNER_TOKEN="my-local-project-token"

Phir migration/startup steps karein. docker compose down service stop karta hai; down -v data volume hata deta hai. Yeh local development setup internet publication ke liye nahi hai.

## Native MongoDB Windows installation

Plain standalone MongoDB server transactions support nahi karta. Is project ke liye replica set chahiye. Atlas/Docker use karein, ya [official conversion guide](https://www.mongodb.com/docs/manual/tutorial/convert-standalone-to-replica-set/) se development server configure karein.

## Tests

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

Asli MongoDB tests ke liye .env mein TEST_MONGODB_URI ko development replica-set/Atlas URI set karein:

    .\.venv\Scripts\python.exe -m scripts.run_mongo_tests

Test user ko temporary test databases create/drop karne ki permission chahiye. **Skipped tests passed nahi hote.** Generation environment mein live MongoDB test run available nahi tha; PROGRESS.md mein status diya hai.

## Common errors

| Symptom | Kya check karein |
| --- | --- |
| No module named pymongo/dotenv | Existing .venv se Step 1 install command chalayein. |
| Cannot connect to MongoDB | Running cluster/server, URI, database-user password aur IP access check karein. |
| Replica set/Atlas required | Standalone server se connect hain; supported setup use karein. |
| Target must be empty | Alag empty MONGODB_DB choose karein; existing data force-delete karke import na karein. |
| Browser connection refused | Terminal mein startup error check karein. |
| Owner token error | Browser value ko .env/current terminal token se match karein. |
| Purane products missing | Database name check karein; SQLite records ke liye migration zaroori hai. |
