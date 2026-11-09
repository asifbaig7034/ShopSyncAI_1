from .providers import Intent


TEXT = {
    "greeting": {"en": "Hello! Ask me about our products, prices, stock or delivery. You can also ask to speak with the owner.", "roman_ur": "Assalam o alaikum! Products, qeemat, stock ya delivery ke bare mein pooch sakte hain. Owner se baat bhi kar sakte hain.", "ur": "السلام علیکم! آپ مصنوعات، قیمت، دستیابی یا ڈیلیوری کے بارے میں پوچھ سکتے ہیں۔ مالک سے بات کرنے کی درخواست بھی کر سکتے ہیں۔"},
    "missing": {"en": "I could not identify that product in our catalogue. Please share its name or product code. Available catalogue names: ", "roman_ur": "Yeh product catalogue mein identify nahi hua. Naam ya product code batayein. Catalogue mein yeh products hain: ", "ur": "یہ پراڈکٹ کیٹلاگ میں نہیں ملا۔ نام یا پراڈکٹ کوڈ بتائیں۔ کیٹلاگ میں یہ مصنوعات ہیں: "},
    "human": {"en": "I have flagged this conversation for the owner. Automatic replies are paused while you wait for a human response.", "roman_ur": "Aapki conversation owner ke liye flag kar di hai. Human reply aane tak automatic replies band hain.", "ur": "آپ کی گفتگو مالک کی توجہ کے لیے نشان زد کر دی گئی ہے۔ انسانی جواب کے انتظار میں خودکار جوابات روک دیے گئے ہیں۔"},
    "uncertain": {"en": "I am unsure how to answer accurately. I have flagged this conversation for the owner and paused automatic replies.", "roman_ur": "Is sawal ka yaqini jawab available nahi. Conversation owner ke liye flag kar di hai aur automatic replies rok diye hain.", "ur": "اس سوال کا یقینی جواب دستیاب نہیں۔ گفتگو مالک کے لیے نشان زد کر دی گئی ہے اور خودکار جوابات روک دیے گئے ہیں۔"},
    "llm_failed": {"en": "The automated assistant is temporarily unavailable. This conversation is flagged for the owner; automatic replies are paused.", "roman_ur": "Automated assistant filhal available nahi. Conversation owner ke liye flag hai aur automatic replies band hain.", "ur": "خودکار معاون فی الحال دستیاب نہیں۔ گفتگو مالک کی توجہ کے لیے نشان زد ہے اور خودکار جوابات روک دیے گئے ہیں۔"},
    "orders_unavailable": {"en": "Order handling is not connected or could not be reached. I have flagged your request for the owner. No order change has been confirmed here.", "roman_ur": "Order module connect nahi ya reachable nahi. Request owner ke liye flag kar di hai. Yahan koi order change confirm nahi hua.", "ur": "آرڈر ماڈیول منسلک نہیں یا دستیاب نہیں۔ درخواست مالک کے لیے نشان زد ہے۔ یہاں آرڈر میں کسی تبدیلی کی تصدیق نہیں ہوئی۔"},
    "complaint_unavailable": {"en": "I have flagged your complaint for the owner. The agent routing service is not connected or could not be reached.", "roman_ur": "Aapki shikayat owner ke liye flag kar di hai. Agent routing service connect nahi ya reachable nahi.", "ur": "آپ کی شکایت مالک کے لیے نشان زد کر دی گئی ہے۔ ایجنٹ روٹنگ سروس منسلک نہیں یا دستیاب نہیں۔"},
    "delivery_missing": {"en": "Delivery options have not been configured for this business. I have flagged the question for the owner.", "roman_ur": "Is business ki delivery details configure nahi hui. Sawal owner ke liye flag kar diya hai.", "ur": "اس کاروبار کی ڈیلیوری کی تفصیلات درج نہیں کی گئیں۔ سوال مالک کی توجہ کے لیے نشان زد کر دیا گیا ہے۔"},
}


def phrase(key, language):
    return TEXT[key][language]


def product_answer(intent: Intent, products):
    """Render all prices/stock locally; no LLM-generated factual answers."""
    selected = [p for p in products if p["id"] in intent.product_ids]
    if intent.intent == "recommend":
        selected = [p for p in selected if p["stock"] > 0]
    if intent.intent == "recommend" and not selected:
        selected = [p for p in products if p["stock"] > 0][:3]
    if not selected:
        names = ", ".join(p["name"] for p in products[:5]) or "(catalogue is empty)"
        return phrase("missing", intent.language) + names
    lines = []
    for p in selected:
        price = f"{p['currency']} {p['price']:,.2f}"
        if intent.language == "ur":
            line = f"{p['name']} — قیمت: {price}۔ دستیاب اسٹاک: {p['stock']}۔"
        elif intent.language == "roman_ur":
            line = f"{p['name']} — Qeemat: {price}. Available stock: {p['stock']}."
        else:
            line = f"{p['name']} — Price: {price}. Available stock: {p['stock']}."
        if p["stock"] == 0:
            line += {"en": " Currently out of stock.", "roman_ur": " Filhal stock khatam hai.", "ur": " فی الحال اسٹاک ختم ہے۔"}[intent.language]
        if p.get("description"):
            line += " " + p["description"]
        lines.append(line)
    return "\n".join(lines)
