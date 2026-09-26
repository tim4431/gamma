"""A ChatGPT subscription as a SHARED server entry (docs/dev/ai.md "Shared
provider entries"): an admin signs in through /api/admin/ai-providers/
chatgpt/start + complete, the tokens are stored encrypted in the server's
config, every account's runtime offers the entry (without the signed-in
e-mail), an expired token is refreshed once under the entry's own lock and
written back, and the allowance meters it like a shared key. All external
calls are faked — no network."""

import json
import threading
import time

import pytest

import gamma.chatgpt_oauth as co
from gamma import ai_catalog
from gamma.ai_protocols import chatgpt as chatgpt_proto

from test_chatgpt_oauth import _FakeResp, _fake_tokens


@pytest.fixture(scope="module")
def admin(client):
    from conftest import login, make_user
    make_user("gpt_admin", "pw", is_admin=1)
    return login("gpt_admin", "pw")


@pytest.fixture(scope="module")
def member(client):
    from conftest import login, make_user
    make_user("gpt_member", "pw")
    return login("gpt_member", "pw")


def _listing(monkeypatch):
    monkeypatch.setattr(chatgpt_proto, "codex_client_version", lambda: "9.9.9")
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp({"models": [
        {"slug": "gpt-6-sol", "visibility": "list"},
        {"slug": "gpt-6-terra", "visibility": "list"},
        {"slug": "gpt-6-luna", "visibility": "list"},
    ]}))


def _connect(admin, monkeypatch, tokens=None, provider_id=""):
    monkeypatch.setattr(co, "_token_request", lambda form: tokens or _fake_tokens(email="lab@example.com"))
    state = admin.post("/api/admin/ai-providers/chatgpt/start").json()["state"]
    return admin.post("/api/admin/ai-providers/chatgpt/complete", json={
        "state": state, "callback": f"http://localhost:1455/auth/callback?code=abc&state={state}",
        "provider_id": provider_id, "name": "Lab ChatGPT"})


@pytest.fixture
def shared(admin, monkeypatch):
    """One connected shared ChatGPT entry; the shared config is emptied
    after the test (the run shares one users.db)."""
    from gamma import ai_settings
    _listing(monkeypatch)
    r = _connect(admin, monkeypatch)
    assert r.status_code == 200, r.text
    try:
        yield next(p for p in r.json()["providers"] if p["protocol"] == "chatgpt")
    finally:
        ai_settings.save_server_ai({"providers": [], "guests": False})


def _expire(provider_id, *, failed=False):
    from gamma import ai_settings

    def change(config):
        for e in config["providers"]:
            if e["id"] == provider_id:
                e["oauth"]["expires_at"] = int(time.time()) - 10
                if failed:
                    e["oauth"]["refresh_failed_at"] = int(time.time())
                else:
                    e["oauth"].pop("refresh_failed_at", None)
    ai_settings.edit_server_ai(change)


def _stored_oauth(provider_id):
    from gamma import ai_settings
    return next(e for e in ai_settings.load_server_ai()["providers"] if e["id"] == provider_id)["oauth"]


def test_admin_form_offers_the_sign_in_protocol(admin):
    body = admin.get("/api/admin/ai-providers").json()
    assert any(p["id"] == "chatgpt" and p["auth"] == "oauth" for p in body["protocols"])
    # A sign-in is only made through the sign-in flow, never the key form.
    r = admin.post("/api/admin/ai-providers", json={"protocol": "chatgpt", "api_key": "x" * 20})
    assert r.status_code == 400


def test_admin_connects_a_shared_sign_in(shared, admin):
    assert shared["id"].startswith("server:")
    assert shared["oauth_connected"] is True
    assert shared["account"] == "lab@example.com"  # the admin sees whose it is
    assert shared["models"] == "gpt-6-sol, gpt-6-terra"  # seeded live, first two
    assert "access" not in json.dumps(shared)


def test_tokens_are_stored_encrypted(shared):
    from gamma.server_settings import _get_raw
    raw = _get_raw("ai_providers")
    oauth = _stored_oauth(shared["id"])
    assert oauth["access_token"] and oauth["access_token"] not in raw
    assert oauth["refresh_token"] not in raw


def test_every_account_gets_the_shared_sign_in(shared, member):
    models = member.get("/api/ai/models").json()["models"]
    assert any(m["provider"] == shared["id"] and m["model"] == "gpt-6-sol" and m["shared"] for m in models)
    row = next(p for p in member.get("/api/ai/settings").json()["providers"] if p["id"] == shared["id"])
    assert row["shared"] is True and row["oauth_connected"] is True
    assert row["account"] == ""  # the admin's e-mail stays the admin's


def test_sign_in_states_do_not_cross(admin, member, monkeypatch):
    monkeypatch.setattr(co, "_token_request", lambda form: _fake_tokens())
    assert member.post("/api/admin/ai-providers/chatgpt/start").status_code == 403
    # A shared state can't be redeemed as the admin's own entry…
    state = admin.post("/api/admin/ai-providers/chatgpt/start").json()["state"]
    r = admin.post("/api/ai/oauth/chatgpt/complete", json={"state": state, "callback": "code"})
    assert r.status_code == 400
    # …and an own state can't become a shared entry.
    state = admin.post("/api/ai/oauth/chatgpt/start").json()["state"]
    r = admin.post("/api/admin/ai-providers/chatgpt/complete", json={"state": state, "callback": "code"})
    assert r.status_code == 400


def test_reconnect_replaces_the_tokens(shared, admin, monkeypatch):
    fresh = _fake_tokens(email="other@example.com")
    r = _connect(admin, monkeypatch, tokens=fresh, provider_id=shared["id"])
    assert r.status_code == 200, r.text
    rows = [p for p in r.json()["providers"] if p["protocol"] == "chatgpt"]
    assert len(rows) == 1 and rows[0]["account"] == "other@example.com"
    assert _stored_oauth(shared["id"])["access_token"] == fresh["access_token"]


def test_expired_shared_token_refreshes_once_for_everyone(shared, monkeypatch):
    from gamma.ai_settings import ai_runtime
    _expire(shared["id"])
    fresh = _fake_tokens(exp=int(time.time()) + 7200)
    calls = []

    def slow_refresh(form):
        calls.append(form)
        time.sleep(0.2)
        return fresh

    monkeypatch.setattr(co, "_token_request", slow_refresh)
    keys = []
    users = ["gpt_member", "gpt_admin"] * 3
    threads = [threading.Thread(target=lambda u=u: keys.append(ai_runtime(u)["providers"][shared["id"]]["api_key"]))
               for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert keys == [fresh["access_token"]] * len(users)
    assert _stored_oauth(shared["id"])["access_token"] == fresh["access_token"]


def test_only_an_admin_resets_a_shared_backoff(shared, admin, member, monkeypatch):
    _expire(shared["id"], failed=True)
    calls = []
    monkeypatch.setattr(co, "_token_request", lambda form: calls.append(form) or _fake_tokens())
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: {})
    # Every account's login check: no refresh retried inside the backoff.
    member.post("/api/ai/health", json={"provider_id": shared["id"]})
    assert calls == [] and "refresh_failed_at" in _stored_oauth(shared["id"])
    # The admin's Test is an explicit retry.
    import gamma.routers.ai as ai_mod
    monkeypatch.setattr(ai_mod, "_call_ai", lambda *a, **k: "ok")
    r = admin.post(f"/api/ai/providers/{shared['id']}/test")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert len(calls) == 1 and "refresh_failed_at" not in _stored_oauth(shared["id"])


def test_subscription_usage_is_the_admins_to_see(shared, admin, member, monkeypatch):
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp({
        "plan_type": "plus", "rate_limit": {"primary_window": {
            "used_percent": 10, "limit_window_seconds": 18000, "reset_at": 1_900_000_000}}}))
    r = admin.post(f"/api/ai/providers/{shared['id']}/usage")
    assert r.status_code == 200 and r.json()["available"] is True, r.text
    assert member.post(f"/api/ai/providers/{shared['id']}/usage").status_code == 404


def test_the_allowance_meters_the_shared_sign_in(shared, admin):
    from gamma.ai_settings import ai_runtime
    assert admin.put("/api/admin/ai-providers", json={"allowance": {"accounts": 500}}).status_code == 200
    rt = ai_runtime("gpt_member")
    assert rt["providers"][shared["id"]]["allowance"] == {"user": "gpt_member", "limit": 500}
    assert rt["allowance"]["limit"] == 500
