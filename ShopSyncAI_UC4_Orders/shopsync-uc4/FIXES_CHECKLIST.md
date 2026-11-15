# UC-4 ke paanch fixes ka demo

Updated: 15 November 2026

Updated code apply karke server restart aur browser Ctrl+F5 karein. Apne actual product SKUs/order IDs use karein. Har independent test ke liye naya order banayein. Existing MongoDB data ko reset karna zaroori nahi.

Automated Python checks:

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v

UI checks:

    node --test tests/ui_orders.test.cjs

Python offline/provider-mock aur Node UI tests real WhatsApp send nahi karte. Real MongoDB verification ke liye TEST_MONGODB_URI configure karke scripts.run_mongo_tests chalana alag required check hai.

## 1. Multi-item draft edit mein baqi items safe

Server chalta rehne dein. Catalog mein TEE-01 aur CUP-01 products add karein; dono ka stock kam azkam 5 ho. Agar aapke SKUs alag hain, neeche product-selection lines mein woh naam likhein.

VS Code mein ek **doosra PowerShell terminal** kholein, usi shopsync-uc4 folder mein. Neeche poora block ek saath paste karein. Agar token terminal environment mein set nahi hai, prompt par apni local .env wala SHOPSYNC_OWNER_TOKEN enter karein; input masked rahega. Token ko kisi screenshot/chat mein share na karein.

    $ownerToken = $env:SHOPSYNC_OWNER_TOKEN
    if ([string]::IsNullOrWhiteSpace($ownerToken)) {
        $secretToken = Read-Host "Apni local .env wala SHOPSYNC_OWNER_TOKEN" -AsSecureString
        $ownerToken = ([System.Net.NetworkCredential]::new("", $secretToken)).Password
    }
    $apiBase = "http://127.0.0.1:8000"
    $headers = @{ Authorization = "Bearer $ownerToken" }
    $products = Invoke-RestMethod -Method Get -Uri "$apiBase/api/products" -Headers $headers
    $firstProduct = $products | Where-Object { $_.sku -eq "TEE-01" } | Select-Object -First 1
    $secondProduct = $products | Where-Object { $_.sku -eq "CUP-01" } | Select-Object -First 1
    if (-not $firstProduct -or -not $secondProduct) {
        throw "Catalog mein dono SKUs add karein, phir block dobara chalayein."
    }
    $body = @{
        phone = "923001234567"
        name = "Ayesha"
        items = @(
            @{ product_id = $firstProduct.id; quantity = 1 }
            @{ product_id = $secondProduct.id; quantity = 2 }
        )
        delivery_address = "House 12, Lahore"
        payment_method = "COD"
        source_key = [guid]::NewGuid().ToString()
    } | ConvertTo-Json -Depth 6
    $draft = Invoke-RestMethod -Method Post -Uri "$apiBase/api/orders/drafts" -Headers $headers -ContentType "application/json" -Body $body
    $draft | Select-Object id, status, total_cents
    $draft.items | Format-Table sku, quantity

Expected: DRAFT status aur do lines aayein: first SKU quantity 1, second SKU quantity 2. Har genuinely naye test draft ke liye block nayi source_key banata hai.

Browser Orders tab par Refresh karein. Terminal mein dikhayi gayi ID ka draft dhoondein. **Edit first item and details** dabayein aur first item ki quantity 2, address ya payment change karein.

Expected:

- First item edit ho; doosra product aur uski quantity 2 rahe.
- Doosri line ka SKU first item mein choose karein to edit refuse ho; original items safe rahen.
- Draft edit se stock/sale record change na ho.

Saved items dobara check karne ke liye usi doosre terminal mein:

    $updated = Invoke-RestMethod -Method Get -Uri "$apiBase/api/orders/$($draft.id)" -Headers $headers
    $updated.items | Format-Table sku, quantity

Dono lines abhi bhi honi chahiye. Dashboard create form aur chat simulator abhi one-SKU input hain; is two-item case ke liye upar authenticated API request use hoti hai. Node tests bhi dropped-items regression ko mocked API ke saath check karte hain.

## 2. Draft retry se duplicate order na bane

Owner create form fill karein. Browser DevTools Network mein Offline karke submit karein: failed request ke baad **Retry draft** aana chahiye. Online karke same form dobara submit karein. Same-tab refresh par pending details/retry key restore honi chahiye.

Expected:

- Retry same complete payload aur same source_key use kare.
- Double-click/in-flight second submit additional request na bheje.
- Success ke baad pending attempt clear ho; genuinely naya order nayi key le.
- Form details explicitly badalne par naya attempt bane.
- API par same source_key ko different payload ke saath bhejne par HTTP 409 aaye.

Offline mode ka manual case request ko server tak jaane se rok sakta hai. Server ne save kiya magar response kho gaya wala exact case Node tests simulate karte hain. Pending-draft sessionStorage mein customer/address/items aur source_key hain; auth token us entry mein nahi. Existing demo owner-token storage alag hai.

## 3. Owner confirmation ka WhatsApp response ek baar queue ho

Order card ka channel check karein:

| Channel | Expected |
| --- | --- |
| simulation | Simulator reply/UI update; real WhatsApp notification ka claim nahi |
| owner | Manual owner order; WhatsApp chat-origin notification ka claim nahi |
| whatsapp | Owner confirm/risk hold par customer response outbox mein queue hota hai |

WhatsApp-channel draft ko owner Confirm order se confirm kare. Low-risk case mein confirmation, high-risk case mein hold response queue hona chahiye. Same confirmation dobara call karne par extra confirmation/stock/sale entry na bane.

Is channel-specific behavior ko offline domain tests fake incoming WhatsApp path se check karte hain; valid credentials ya real send ki zaroorat nahi. Actual customer delivery ka test sirf configured WhatsApp account/public webhook ke saath ho sakta hai.

## 4. Repeat return/refund ka sahi current result

Confirmed order se simulator mein:

    REFUND ORDER_ID Wrong item

Owner Orders tab par reject karke reason likhein. **Naye chat message** mein exact same REFUND command dobara bhejein. Expected: saved rejected state/reason aaye, dobara pending owner review na kaha jaye.

Alag order par refund approve karke same command dobara new message ki tarah bhejein. Expected: request completed ho aur reply kahe accounting refund recorded hai, payment owner ko manually complete karni hai. UI par bhi manual payment note aaye. Top-level order status CONFIRMED/SHIPPED reh sakta hai; request outcome alag field hai.

- Ek hi transport message-ID replay ho to pehla cached response milta hai; new-message repeat test is se alag hai.
- Request ki reason/kind badal kar doosri request banane par existing-request conflict aata hai.
- Completed refund par cancellation se doosri financial reversal nahi honi chahiye.

## 5. Provider delivery receipts aur failure states

Activity → Delivery notifications mein statuses ka matlab:

| State | Matlab |
| --- | --- |
| PENDING / SENDING | Queue mein hai / worker ne attempt claim kiya |
| ACCEPTED | Provider ne message ID return ki; delivery proof abhi nahi |
| SENT | Signed sent receipt aa gayi |
| DELIVERED / READ | Signed delivery/read receipt aa gayi |
| FAILED | Known temporary error; next eligible retry scheduled hai |
| MANUAL_REVIEW | Permanent/unknown error ya automatic attempt limit reached |
| NEEDS_CONFIGURATION | Account/token/number configuration resolve karni hai |
| NEEDS_TEMPLATE | Window/template integration required |
| UNCERTAIN | Send ka result unknown; retry se pehle review |

Mocked provider/callback tests check karte hain:

- Valid signed receipt ka provider message ID sahi outbox record se match ho.
- Duplicate callback dobara apply na ho; old sent/failed callback delivered/read ko downgrade na kare.
- Provider response save hone se pehle aane wali receipt delivery_events mein wait kare aur mapping milne par reconcile ho.
- Known temporary delivery failure par maximum five total automatic attempts; permanent, configuration aur template errors alag states mein jayen.
- Callback aane se order, stock ya finance dobara mutate na hon.
- Configured WA_PHONE_NUMBER_ID ke saath wrong-number webhook metadata ignore ho.

Webhook ka statuses_processed valid stored receipts ka count hai, delivered customers ka count nahi. Provider ACCEPTED ko delivered na samjhein. Is generation run mein real MongoDB callbacks/real WhatsApp transport verify nahi hue; apne development deployment par live suite aur phir account-level delivery test chalayein.
