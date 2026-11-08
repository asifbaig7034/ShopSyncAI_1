"""UC-1 WhatsApp configuration; all Meta HTTP requests are simulated."""

from __future__ import annotations

from cryptography.fernet import Fernet
import httpx

from backend.app.core.config import get_settings
from backend.app.infrastructure.integrations import whatsapp


ENDPOINT = "/api/integrations/whatsapp"
WABA = "123456789012345"
PHONE = "987654321098765"
TOKEN = "EA_test_only_fictional_access_token_12345678"


def _real_owner(client):
    created = client.post("/api/account/signup", json={
        "name": "Brand Owner", "business_name": "One Brand", "email": "owner@example.com",
        "password": "a-private-password-123",
    })
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['access_token']}"}, created.json()["owner"]["id"]


def _use_meta(monkeypatch, handler):
    monkeypatch.setattr(whatsapp, "_meta_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False,
    ))


def _enable_key(monkeypatch):
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("META_GRAPH_VERSION", "v26.0")
    get_settings.cache_clear()


def test_whatsapp_verified_access_uses_fixed_meta_host_encrypts_token_and_disconnects(demo, monkeypatch):
    client, db, _, _ = demo
    headers, owner_id = _real_owner(client)
    _enable_key(monkeypatch)
    calls = []

    def meta(request):
        calls.append(request)
        assert request.url.scheme == "https" and request.url.host == "graph.facebook.com"
        assert request.url.path == f"/v26.0/{WABA}/phone_numbers"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.url.params["fields"] == "id,display_phone_number,verified_name"
        if "after" not in request.url.params:
            return httpx.Response(200, json={
                "data": [{"id": "111111111111111"}],
                # A provider 'next' URL must NEVER be followed as a URL.
                "paging": {"next": "https://not-meta.example/steal-token",
                           "cursors": {"after": "cursor-2"}},
            })
        assert request.url.params["after"] == "cursor-2"
        return httpx.Response(200, json={
            "data": [{"id": PHONE, "display_phone_number": "+92 300 1234567",
                      "verified_name": "Fictional Brand"}],
        })

    _use_meta(monkeypatch, meta)
    empty = client.get(ENDPOINT, headers=headers)
    assert empty.status_code == 200 and empty.json()["status"] == "not_connected"
    connected = client.post(f"{ENDPOINT}/connect", headers=headers, json={
        "waba_id": WABA, "phone_number_id": PHONE, "access_token": TOKEN,
    })
    assert connected.status_code == 200, connected.text
    result = connected.json()
    assert set(result) == {"status", "waba_id", "phone_number_id", "display_phone_number",
                           "verified_name", "verified_at"}
    assert result["status"] == "meta_read_verified" and result["phone_number_id"] == PHONE
    assert result["display_phone_number"] == "+92 300 1234567"
    assert result["verified_name"] == "Fictional Brand" and result["verified_at"]
    assert TOKEN not in connected.text
    assert len(calls) == 2
    record = db.integration_connections.find_one({"owner_id": owner_id, "channel": "whatsapp"})
    assert TOKEN not in str(record) and record["token_ciphertext"] != TOKEN
    assert client.get(ENDPOINT, headers=headers).json() == result

    checked = client.post(f"{ENDPOINT}/verify", headers=headers)
    assert checked.status_code == 200 and checked.json()["status"] == "meta_read_verified"
    assert len(calls) == 4
    deleted = client.delete(ENDPOINT, headers=headers)
    assert deleted.status_code == 200 and deleted.json()["status"] == "not_connected"
    assert db.integration_connections.count_documents({"owner_id": owner_id}) == 0
    assert client.post(f"{ENDPOINT}/verify", headers=headers).status_code == 404


def test_whatsapp_requires_real_owner_and_encryption_key(demo, monkeypatch):
    client, db, _, demo_headers = demo
    headers, owner_id = _real_owner(client)
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    payload = {"waba_id": WABA, "phone_number_id": PHONE, "access_token": TOKEN}
    for method, path in (("get", ENDPOINT), ("delete", ENDPOINT),
                         ("post", f"{ENDPOINT}/verify"), ("post", f"{ENDPOINT}/connect")):
        response = getattr(client, method)(path, headers=demo_headers,
                                           **({"json": payload} if path.endswith("/connect") else {}))
        assert response.status_code == 403
        response = getattr(client, method)(path,
                                           **({"json": payload} if path.endswith("/connect") else {}))
        assert response.status_code == 401
    assert client.get(ENDPOINT, headers=headers).json()["status"] == "not_connected"
    assert client.post(f"{ENDPOINT}/connect", headers=headers, json=payload).status_code == 503
    assert client.post(f"{ENDPOINT}/verify", headers=headers).status_code == 503
    assert client.delete(ENDPOINT, headers=headers).json()["status"] == "not_connected"
    assert db.integration_connections.count_documents({"owner_id": owner_id}) == 0


def test_whatsapp_invalid_id_or_meta_denial_does_not_save_secrets(demo, monkeypatch):
    client, db, _, _ = demo
    headers, owner_id = _real_owner(client)
    _enable_key(monkeypatch)
    calls = []

    def meta(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": TOKEN}})

    _use_meta(monkeypatch, meta)
    bad = client.post(f"{ENDPOINT}/connect", headers=headers,
                      json={"waba_id": "12345/evil", "phone_number_id": PHONE, "access_token": TOKEN})
    assert bad.status_code == 422 and not calls
    denied = client.post(f"{ENDPOINT}/connect", headers=headers,
                         json={"waba_id": WABA, "phone_number_id": PHONE, "access_token": TOKEN})
    assert denied.status_code == 400 and TOKEN not in denied.text
    assert db.integration_connections.count_documents({"owner_id": owner_id}) == 0


def test_whatsapp_reverify_marks_revoked_token_attention_and_get_handles_lost_key(demo, monkeypatch):
    client, db, _, _ = demo
    headers, owner_id = _real_owner(client)
    _enable_key(monkeypatch)
    _use_meta(monkeypatch, lambda request: httpx.Response(200, json={
        "data": [{"id": PHONE}],
    }))
    assert client.post(f"{ENDPOINT}/connect", headers=headers, json={
        "waba_id": WABA, "phone_number_id": PHONE, "access_token": TOKEN,
    }).json()["status"] == "meta_read_verified"
    _use_meta(monkeypatch, lambda request: httpx.Response(403, json={"error": {"message": TOKEN}}))
    changed = client.post(f"{ENDPOINT}/verify", headers=headers)
    assert changed.status_code == 200 and changed.json()["status"] == "needs_attention"
    assert TOKEN not in changed.text
    assert db.integration_connections.find_one({"owner_id": owner_id})["status"] == "needs_attention"
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    assert client.get(ENDPOINT, headers=headers).json()["status"] == "needs_attention"
    assert client.post(f"{ENDPOINT}/verify", headers=headers).status_code == 503
    assert client.delete(ENDPOINT, headers=headers).json()["status"] == "not_connected"


def test_whatsapp_rejects_mismatched_phone_and_cors_allows_delete(demo, monkeypatch):
    client, db, _, _ = demo
    headers, owner_id = _real_owner(client)
    _enable_key(monkeypatch)
    _use_meta(monkeypatch, lambda request: httpx.Response(200, json={
        "data": [{"id": "111111111111111"}],
    }))
    missing = client.post(f"{ENDPOINT}/connect", headers=headers, json={
        "waba_id": WABA, "phone_number_id": PHONE, "access_token": TOKEN,
    })
    assert missing.status_code == 400 and db.integration_connections.count_documents({"owner_id": owner_id}) == 0
    preflight = client.options(ENDPOINT, headers={
        "Origin": "http://127.0.0.1:5173",
        "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "authorization",
    })
    assert preflight.status_code == 200
    assert "DELETE" in preflight.headers.get("access-control-allow-methods", "")
