"""UC-1 local account flow and compatibility with UC-2 owner data isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from bson import ObjectId
from backend.app.application.services import account_service as account_service
from backend.app.api.schemas.account import PasswordResetConfirm
from backend.app.core.config import get_settings


def signup(client, email="owner@samplebrand.com", password="a-private-password-123"):
    return client.post("/api/account/signup", json={
        "name": "Brand Owner", "business_name": "Our Brand", "email": email,
        "password": password,
    })


def test_owner_signup_login_logout_and_own_data(demo):
    client, db, sample, demo_headers = demo
    created = signup(client)
    assert created.status_code == 201, created.text
    body = created.json()
    owner_id = body["owner"]["id"]
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert body["owner"]["role"] == "owner"
    assert body["owner"]["business_name"] == "Our Brand"
    assert body["access_token"] not in str(db.owners.find_one({"_id": ObjectId(owner_id)}))
    owner = db.owners.find_one({"_id": ObjectId(owner_id)})
    assert owner["password_hash"].startswith("$argon2")
    assert owner["password_hash"] != "a-private-password-123"
    assert db.owner_sessions.find_one({"owner_id": owner_id})["token_hash"] != body["access_token"]
    assert client.get("/api/account/me", headers=headers).json()["email"] == "owner@samplebrand.com"
    assert "password_hash" not in client.get("/api/account/me", headers=headers).text

    db.products.insert_one({"owner_id": owner_id, "name": "Our Brand Mug", "price": 600.0,
                            "stock_quantity": 6, "reorder_threshold": 3})
    own = client.post("/api/owner/chat", headers=headers, json={"message": "Show products"})
    assert own.status_code == 200 and "Our Brand Mug" in own.json()["reply"]
    assert "Cotton Shirt" not in own.json()["reply"]
    old_demo = client.post("/api/owner/chat", headers=demo_headers, json={"message": "Show products"})
    assert old_demo.status_code == 200 and "Cotton Shirt" in old_demo.json()["reply"]
    assert "Our Brand Mug" not in old_demo.json()["reply"]
    assert client.get("/api/account/me", headers=demo_headers).status_code == 200

    assert client.post("/api/account/logout", headers=headers).status_code == 204
    assert client.get("/api/account/me", headers=headers).status_code == 401
    assert client.post("/api/owner/chat", headers=headers, json={"message": "Show products"}).status_code == 401
    again = client.post("/api/account/login", json={"email": "owner@samplebrand.com",
                                                      "password": "a-private-password-123"})
    assert again.status_code == 200 and again.json()["access_token"] != body["access_token"]


def test_signup_cannot_claim_demo_owner_or_register_a_second_brand(demo):
    client, db, sample, _ = demo
    assert signup(client, email="owner@shopsync.local").status_code in {409, 422}
    assert db.owners.find_one({"_id": ObjectId(sample["owner_id"]) }).get("password_hash") is None
    assert signup(client).status_code == 201
    assert db.owners.find_one({"email": "owner@samplebrand.com"})["singleton_key"] == "primary_owner"
    assert signup(client, email="second@samplebrand.com").status_code == 409
    assert client.post("/api/account/signup", json={"name": "   ", "business_name": "Valid Brand",
                                                    "email": "third@samplebrand.com", "password": "a-private-password-123"}).status_code == 422
    assert client.post("/api/account/login", json={"email": "owner@shopsync.local",
                                                      "password": "a-private-password-123"}).status_code in {401, 422}
    assert client.post("/api/account/login", json={"email": "owner@samplebrand.com",
                                                      "password": "wrong-password"}).status_code == 401


def test_expired_owner_session_is_rejected(demo):
    client, db, _, _ = demo
    response = signup(client)
    assert response.status_code == 201
    token = response.json()["access_token"]
    db.owner_sessions.update_one({}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}})
    expired_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/account/me", headers=expired_headers).status_code == 401
    assert client.get("/api/account/settings", headers=expired_headers).status_code == 401
    assert client.patch("/api/account/settings", headers=expired_headers,
                        json={"agent_settings": {"customer_commerce": False}}).status_code == 401


def test_owner_settings_save_profile_and_toggle_live_agents(demo):
    client, db, _, _ = demo
    signup_response = signup(client)
    assert signup_response.status_code == 201
    owner_id = ObjectId(signup_response.json()["owner"]["id"])
    headers = {"Authorization": f"Bearer {signup_response.json()['access_token']}"}
    db.owners.update_one({"_id": owner_id}, {"$set": {"business_profile.future_field": "keep",
                                                    "agent_settings.future_agent": False}})

    initial = client.get("/api/account/settings", headers=headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "business_profile": {"name": "Our Brand", "contact": "", "currency": "PKR", "language": "English"},
        "agent_settings": {"customer_commerce": True, "inventory_logistics": True},
    }
    saved = client.patch("/api/account/settings", headers=headers, json={
        "business_profile": {"name": "  Updated Brand  ", "contact": "  03001234567  ",
                             "currency": " usd ", "language": "Roman Urdu"},
        "agent_settings": {"inventory_logistics": False},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "business_profile": {"name": "Updated Brand", "contact": "03001234567", "currency": "USD",
                             "language": "Roman Urdu"},
        "agent_settings": {"customer_commerce": True, "inventory_logistics": False},
    }
    assert client.get("/api/account/settings", headers=headers).json() == saved.json()
    assert client.get("/api/account/me", headers=headers).json()["business_name"] == "Updated Brand"
    stored = db.owners.find_one({"_id": owner_id})
    assert stored["business_profile"]["future_field"] == "keep"
    assert stored["agent_settings"]["future_agent"] is False

    db.orders.insert_one({"owner_id": str(owner_id), "order_number": "OWN-1", "status": "confirmed", "total": 100})
    db.products.insert_one({"owner_id": str(owner_id), "name": "Updated Product", "price": 100,
                            "stock_quantity": 2, "reorder_threshold": 3})
    chat = client.post("/api/owner/chat", headers=headers,
                       json={"message": "Show products, stock and recent orders"})
    assert chat.status_code == 200, chat.text
    assert chat.json()["routed_agents"] == ["customer_commerce"]
    assert "Supply Chain & Operations (disabled)" in chat.json()["reply"]


def test_demo_token_cannot_read_or_change_real_owner_settings(demo):
    client, db, sample, demo_headers = demo
    signup_response = signup(client)
    owner_id = ObjectId(signup_response.json()["owner"]["id"])
    before_demo = db.owners.find_one({"_id": ObjectId(sample["owner_id"])})
    before_real = db.owners.find_one({"_id": owner_id})
    assert client.get("/api/account/settings", headers=demo_headers).status_code == 403
    denied = client.patch("/api/account/settings", headers=demo_headers, json={
        "business_profile": {"name": "Taken Over"},
        "agent_settings": {"customer_commerce": False},
    })
    assert denied.status_code == 403
    assert db.owners.find_one({"_id": ObjectId(sample["owner_id"])}) == before_demo
    assert db.owners.find_one({"_id": owner_id}) == before_real
    assert client.patch("/api/account/settings", json={"agent_settings": {"customer_commerce": False}}).status_code == 401


def test_settings_reject_unknown_fields_nulls_and_invalid_values_without_writes(demo):
    client, db, _, _ = demo
    response = signup(client)
    owner_id = ObjectId(response.json()["owner"]["id"])
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    before = db.owners.find_one({"_id": owner_id})
    invalid_requests = [
        {}, {"business_profile": {}}, {"agent_settings": {}},
        {"agent_settings": {"risk_trust": True}},
        {"agent_settings": {"customer_commerce": "false"}},
        {"agent_settings": {"customer_commerce": None}},
        {"business_profile": {"name": "  "}},
        {"business_profile": {"currency": "PK123"}},
        {"business_profile": {"language": "French"}},
        {"business_profile": {"name": "Valid Brand", "admin": True}},
        {"business_profile": {"name": "Valid Brand"}, "role": "admin"},
        {"business_profile": {"name": "Valid Brand"}, "agent_settings": {"risk_trust": True}},
    ]
    for payload in invalid_requests:
        result = client.patch("/api/account/settings", headers=headers, json=payload)
        assert result.status_code == 422, (payload, result.text)
        assert db.owners.find_one({"_id": owner_id}) == before


def test_settings_patch_cors_preflight_for_local_frontend(demo):
    client, _, _, _ = demo
    preflight = client.options("/api/account/settings", headers={
        "Origin": "http://127.0.0.1:5173",
        "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert "PATCH" in preflight.headers.get("access-control-allow-methods", "")


def test_password_reset_requires_email_delivery_for_every_account(demo, monkeypatch):
    client, db, _, _ = demo
    assert signup(client).status_code == 201
    monkeypatch.setenv("SMTP_HOST", "")
    get_settings.cache_clear()
    for address in ("owner@samplebrand.com", "unknown@example.com"):
        response = client.post("/api/account/password-reset/request", json={"email": address})
        assert response.status_code == 503
        assert response.json()["detail"] == "Password recovery is unavailable; configure mail delivery."
    assert "password_reset" not in db.owners.find_one({"email": "owner@samplebrand.com"})


def test_password_reset_email_is_generic_one_use_and_revokes_old_sessions(demo, monkeypatch):
    client, db, _, _ = demo
    created = signup(client)
    assert created.status_code == 201
    old_session = created.json()["access_token"]
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    get_settings.cache_clear()
    sent = []
    changed = []
    monkeypatch.setattr(account_service, "send_password_reset_email",
                        lambda settings, address, code: sent.append((address, code)))
    monkeypatch.setattr(account_service, "send_password_changed_email",
                        lambda settings, address: changed.append(address))
    known = client.post("/api/account/password-reset/request", json={"email": "owner@samplebrand.com"})
    unknown = client.post("/api/account/password-reset/request", json={"email": "unknown@example.com"})
    # The seeded .local email is intentionally rejected by EmailStr. Give the
    # isolated demo owner a valid test email to check that demo accounts cannot
    # request a reset without weakening the API's email validation.
    assert db.owners.update_one(
        {"email": "owner@shopsync.local", "demo": True},
        {"$set": {"email": "demo-owner@example.com"}},
    ).matched_count == 1
    demo_owner = client.post("/api/account/password-reset/request", json={"email": "demo-owner@example.com"})
    assert known.status_code == unknown.status_code == demo_owner.status_code == 200
    assert known.json() == unknown.json() == demo_owner.json()
    assert len(sent) == 1 and sent[0][0] == "owner@samplebrand.com"
    code = sent[0][1]
    owner = db.owners.find_one({"email": "owner@samplebrand.com"})
    assert code not in str(owner)
    assert len(code) >= 32
    assert client.post("/api/account/password-reset/request", json={"email": "owner@samplebrand.com"}).json() == known.json()
    assert len(sent) == 1  # A second request within 60 seconds cannot flood the inbox.
    bad = client.post("/api/account/password-reset/confirm", json={
        "email": "owner@samplebrand.com", "code": "X" * 43, "password": "a-new-private-password-123",
    })
    assert bad.status_code == 400
    assert client.get("/api/account/me", headers={"Authorization": f"Bearer {old_session}"}).status_code == 200

    reset = client.post("/api/account/password-reset/confirm", json={
        "email": "owner@samplebrand.com", "code": code, "password": "a-new-private-password-123",
    })
    assert reset.status_code == 200, reset.text
    assert "access_token" not in reset.json() and "code" not in str(reset.json())
    assert changed == ["owner@samplebrand.com"]
    assert client.get("/api/account/me", headers={"Authorization": f"Bearer {old_session}"}).status_code == 401
    assert client.post("/api/account/password-reset/confirm", json={
        "email": "owner@samplebrand.com", "code": code, "password": "a-new-private-password-123",
    }).status_code == 400
    assert client.post("/api/account/login", json={
        "email": "owner@samplebrand.com", "password": "a-private-password-123",
    }).status_code == 401
    assert client.post("/api/account/login", json={
        "email": "owner@samplebrand.com", "password": "a-new-private-password-123",
    }).status_code == 200


def test_expired_reset_code_and_request_cooldown(demo, monkeypatch):
    client, db, _, _ = demo
    created = signup(client)
    assert created.status_code == 201
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    get_settings.cache_clear()
    sent = []
    monkeypatch.setattr(account_service, "send_password_reset_email",
                        lambda settings, address, code: sent.append(code))
    client.post("/api/account/password-reset/request", json={"email": "owner@samplebrand.com"})
    first_code = sent[0]
    db.owners.update_one({"email": "owner@samplebrand.com"}, {"$set": {
        "password_reset.expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
    }})
    assert client.post("/api/account/password-reset/confirm", json={
        "email": "owner@samplebrand.com", "code": first_code, "password": "a-new-private-password-123",
    }).status_code == 400
    assert client.post("/api/account/password-reset/request", json={
        "email": "owner@samplebrand.com",
    }).status_code == 200
    assert len(sent) == 1  # Cooldown still applies after token expiration.
    db.owners.update_one({"email": "owner@samplebrand.com"}, {"$set": {
        "password_reset_requested_at": datetime.now(timezone.utc) - timedelta(seconds=70),
    }})
    assert client.post("/api/account/password-reset/request", json={
        "email": "owner@samplebrand.com",
    }).status_code == 200
    assert len(sent) == 2 and sent[1] != first_code
    assert client.post("/api/account/password-reset/confirm", json={
        "email": "owner@samplebrand.com", "code": first_code, "password": "a-new-private-password-123",
    }).status_code == 400


def test_failed_smtp_delivery_discards_code_without_disclosing_account(demo, monkeypatch):
    client, db, _, _ = demo
    assert signup(client).status_code == 201
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    get_settings.cache_clear()
    def fail_to_send(*args):
        raise OSError("mail server unavailable")
    monkeypatch.setattr(account_service, "send_password_reset_email", fail_to_send)
    known = client.post("/api/account/password-reset/request", json={"email": "owner@samplebrand.com"})
    unknown = client.post("/api/account/password-reset/request", json={"email": "unknown@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert "password_reset" not in db.owners.find_one({"email": "owner@samplebrand.com"})


def test_concurrent_confirmation_consumes_code_once(demo, monkeypatch):
    client, db, _, _ = demo
    assert signup(client).status_code == 201
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    get_settings.cache_clear()
    codes = []
    monkeypatch.setattr(account_service, "send_password_reset_email",
                        lambda settings, address, code: codes.append(code))
    client.post("/api/account/password-reset/request", json={"email": "owner@samplebrand.com"})
    payload = PasswordResetConfirm(email="owner@samplebrand.com", code=codes[0],
                                   password="a-new-private-password-123")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: account_service.confirm_password_reset(db, payload), range(2)))
    assert sorted(results) == [False, True]
    owner = db.owners.find_one({"email": "owner@samplebrand.com"})
    assert owner["credential_version"] == 1
