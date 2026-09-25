"""Per-user prefs KV store (tab sync) + GUI-configured AI provider keys."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def alice(client):
    """A separate TestClient logged in as a real (non-guest) user."""
    from conftest import login, make_user
    make_user("prefs_alice", "pw")
    return login("prefs_alice", "pw")


# --- prefs -------------------------------------------------------------------

def test_prefs_unset_key_reads_empty(guest):
    r = guest.get("/api/prefs/open-tabs")
    assert r.status_code == 200
    body = r.json()
    assert body["value"] is None and body["updated_at"] == ""


def test_prefs_roundtrip_and_updated_at(guest):
    tabs = [{"id": "b1", "title": "Paper A"}, {"id": "b2", "title": "Paper B"}]
    r = guest.put("/api/prefs/open-tabs", json={"value": tabs})
    assert r.status_code == 200
    first = r.json()["updated_at"]
    assert first

    r = guest.get("/api/prefs/open-tabs")
    assert r.json()["value"] == tabs
    assert r.json()["updated_at"] == first

    # Last write wins, updated_at moves forward
    r = guest.put("/api/prefs/open-tabs", json={"value": []})
    assert r.json()["updated_at"] > first
    assert guest.get("/api/prefs/open-tabs").json()["value"] == []


def test_prefs_rejects_bad_keys_and_huge_values(guest):
    assert guest.get("/api/prefs/No%20Spaces").status_code == 400
    assert guest.put("/api/prefs/UPPER", json={"value": 1}).status_code == 400
    big = "x" * (70 * 1024)
    assert guest.put("/api/prefs/open-tabs", json={"value": big}).status_code == 413


def test_prefs_never_serve_the_reserved_ai_settings_key(guest):
    # The raw AI keys live under this prefs key — only the masked
    # /api/ai/settings endpoint may read it.
    assert guest.get("/api/prefs/ai-settings").status_code == 400
    assert guest.put("/api/prefs/ai-settings", json={"value": {}}).status_code == 400


def test_profile_is_one_account_wide_object(alice):
    # The preference profile follows the account, not the workspace: a write
    # through one workspace reads back through any other (or none).
    from gamma import db
    from conftest import workspace_of
    assert "profile" in db.USER_PREF_KEYS
    profile = {"theme": "sepia", "enterNewNote": True, "agentPerms": {"pdf": {"block_edit": False}}}
    r = alice.put("/api/prefs/profile", json={"value": profile}, headers={"X-Gamma-Workspace": workspace_of("prefs_alice")})
    assert r.status_code == 200 and r.json()["updated_at"]
    body = alice.get("/api/prefs/profile", headers={"X-Gamma-Workspace": "some-other-workspace"}).json()
    assert body["value"] == profile and body["updated_at"] == r.json()["updated_at"]
    assert db.get_profile("prefs_alice") == (profile, body["updated_at"])
    # set_profile goes through set_pref (last write wins, a newer updated_at)
    later = db.set_profile("prefs_alice", {"theme": "gray"})
    assert later > body["updated_at"]
    assert alice.get("/api/prefs/profile").json() == {"key": "profile", "cloud_choice": False, "value": {"theme": "gray"},
                                                      "updated_at": later}
    with pytest.raises(ValueError):
        db.set_profile("prefs_alice", ["not", "an", "object"])


def test_profile_patch_sets_only_the_named_entries(alice):
    # the web app's save: the entries it changed, every other one kept as stored
    from gamma import db
    db.set_profile("prefs_alice", {"theme": "dark", "language": "en"})
    before = db.get_profile("prefs_alice")[1]
    r = alice.patch("/api/prefs/profile", json={"set": {"language": "zh", "enterNewNote": True}})
    assert r.status_code == 200
    assert r.json()["value"] == {"theme": "dark", "language": "zh", "enterNewNote": True}
    assert r.json()["updated_at"] > before
    assert db.get_profile("prefs_alice") == (r.json()["value"], r.json()["updated_at"])
    assert alice.patch("/api/prefs/profile", json={"set": {"chatSystem": "x" * (70 * 1024)}}).status_code == 413
    assert alice.patch("/api/prefs/profile", json={"set": ["theme"]}).status_code == 422
    # the cloud sync's merge base is not a pref the generic endpoints serve
    assert alice.get("/api/prefs/profile-base").status_code == 400


def test_profile_must_be_an_object_within_the_size_cap(alice):
    from gamma import db
    assert db.get_profile("prefs_nobody") == ({}, "")
    assert alice.put("/api/prefs/profile", json={"value": ["theme"]}).status_code == 400
    assert alice.put("/api/prefs/profile", json={"value": "dark"}).status_code == 400
    assert alice.put("/api/prefs/profile", json={"value": {"chatSystem": "x" * (70 * 1024)}}).status_code == 413
    # four long custom prompts still fit
    prompts = {k: "p" * 12000 for k in ("chatSystem", "agentSystem", "metaPrompt", "citePrompt")}
    assert alice.put("/api/prefs/profile", json={"value": prompts}).status_code == 200


def test_prefs_require_session(client):
    from gamma.app import app
    anon = TestClient(app)
    assert anon.get("/api/prefs/open-tabs").status_code == 401
    assert anon.put("/api/prefs/open-tabs", json={"value": []}).status_code == 401


# --- AI provider entries (GUI key management) ---------------------------------

def test_guest_cannot_store_keys(guest):
    r = guest.get("/api/ai/settings")
    assert r.status_code == 200
    assert r.json()["can_edit"] is False
    assert r.json()["providers"] == []
    r = guest.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-x-guest-key"})
    assert r.status_code == 403


def test_added_provider_is_masked_and_enables_ai(alice):
    # No env keys exist at all — AI starts disabled until the user adds a provider
    assert alice.get("/api/ai/models").json()["enabled"] is False

    key = "sk-ant-api03-test-key-12345678"
    r = alice.post("/api/ai/providers", json={
        "protocol": "anthropic", "name": "My DeepSeek", "api_key": key,
        "base_url": "https://example.com/v1x", "models": "claude-test-model, claude-other",
    })
    assert r.status_code == 200, r.text
    provs = r.json()["providers"]
    assert len(provs) == 1
    p = provs[0]
    # Never echo the key — only a short hint
    assert key not in r.text
    assert p["key_hint"] == "…5678"
    assert p["name"] == "My DeepSeek" and p["protocol"] == "anthropic"
    assert p["base_url"] == "https://example.com/v1x"
    assert p["created_at"]

    models = alice.get("/api/ai/models").json()
    assert models["enabled"] is True
    assert models["default"] == f"{p['id']}:claude-test-model"
    assert [m["model"] for m in models["models"]] == ["claude-test-model", "claude-other"]
    assert models["models"][0]["provider_name"] == "My DeepSeek"

    # ...and the same masked view comes back on GET
    g = alice.get("/api/ai/settings").json()
    assert key not in str(g)
    assert g["providers"][0]["key_hint"] == "…5678"


def test_edit_without_key_keeps_the_stored_one(alice):
    pid = alice.get("/api/ai/settings").json()["providers"][0]["id"]
    r = alice.put(f"/api/ai/providers/{pid}", json={"name": "Renamed", "models": "claude-solo"})
    assert r.status_code == 200, r.text
    p = r.json()["providers"][0]
    assert p["name"] == "Renamed" and p["key_hint"] == "…5678"
    models = alice.get("/api/ai/models").json()
    assert models["enabled"] is True  # key survived the edit
    assert models["default"] == f"{pid}:claude-solo"


def test_second_provider_adds_its_models(alice):
    r = alice.post("/api/ai/providers", json={
        "protocol": "openai", "api_key": "sk-openai-test-key-9876",
    })
    assert r.status_code == 200, r.text
    assert len(r.json()["providers"]) == 2
    models = alice.get("/api/ai/models").json()["models"]
    # The openai entry has no models picked — it offers none (there is no
    # built-in default model), and the Test button says so instead of failing.
    assert [m["model"] for m in models] == ["claude-solo"]
    pid = r.json()["providers"][1]["id"]
    body = alice.post(f"/api/ai/providers/{pid}/test").json()
    assert body["ok"] is False and "no model" in body["error"]


def test_deepseek_service_preset(alice, monkeypatch):
    # DeepSeek is offered as a named service: the openai protocol at its
    # endpoint. Such an entry is labelled DeepSeek, and its live model list is
    # not narrowed to OpenAI's gpt-/o-families.
    from gamma import ai_catalog

    g = alice.get("/api/ai/settings").json()
    svc = next(s for s in g["services"] if s["id"] == "deepseek")
    assert svc["protocol"] == "openai" and svc["base_url"] == "https://api.deepseek.com"

    r = alice.post("/api/ai/providers", json={
        "protocol": "openai", "api_key": "sk-deepseek-test-1234",
        "base_url": svc["base_url"], "models": "deepseek-flash",
    })
    assert r.status_code == 200, r.text
    entry = next(p for p in r.json()["providers"] if p["base_url"] == svc["base_url"])
    assert entry["name"] == "" and entry["label"] == "DeepSeek"
    models = alice.get("/api/ai/models").json()["models"]
    assert any(m["model"] == "deepseek-flash" and m["provider_name"] == "DeepSeek" for m in models)

    seen = {}
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: seen.update(url=req.full_url) or {
        "data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}, {"id": "deepseek-embed"}]})
    r = alice.post("/api/ai/model-catalog", json={"provider_id": entry["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert seen["url"] == "https://api.deepseek.com/v1/models"

    alice.delete(f"/api/ai/providers/{entry['id']}")


def test_provider_validation(alice):
    assert alice.post("/api/ai/providers", json={"protocol": "nope", "api_key": "k" * 20}).status_code == 400
    assert alice.post("/api/ai/providers", json={"protocol": "openai"}).status_code == 400  # no key
    assert alice.post("/api/ai/providers",
                      json={"protocol": "openai", "api_key": "sk-ok-key-123", "base_url": "ftp://x"}).status_code == 400
    assert alice.post("/api/ai/providers", json={"protocol": "openai", "api_key": "has space"}).status_code == 400
    assert alice.put("/api/ai/providers/does-not-exist", json={"name": "x"}).status_code == 404


# --- provider Test button (live probe) ----------------------------------------

def test_provider_test_probe_reports_ok(alice, monkeypatch):
    import gamma.routers.ai as ai_mod

    seen = {}

    def fake_call(messages, system, entry, rt, **kw):
        seen.update(entry)
        return "ok"

    monkeypatch.setattr(ai_mod, "_call_ai", fake_call)
    pid = alice.get("/api/ai/settings").json()["providers"][0]["id"]
    r = alice.post(f"/api/ai/providers/{pid}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["model"] == "claude-solo"
    assert isinstance(body["latency_ms"], int)
    # The probe goes through the tested entry's first model, not the default.
    assert seen == {"provider": pid, "model": "claude-solo"}


def test_provider_test_reports_upstream_failure_in_body(alice, monkeypatch):
    # A failed probe is a successful test: 200 with the upstream error in-body.
    # A 401 additionally carries the auth flag — the UI renders "reconnect"
    # instead of the raw upstream body.
    import gamma.routers.ai as ai_mod
    from gamma.ai_client import UpstreamError

    def fake_call(*a, **kw):
        raise UpstreamError(401, "upstream 401: token expired")

    monkeypatch.setattr(ai_mod, "_call_ai", fake_call)
    pid = alice.get("/api/ai/settings").json()["providers"][0]["id"]
    r = alice.post(f"/api/ai/providers/{pid}/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["auth"] is True
    assert "401" in r.json()["error"]


def test_provider_test_non_auth_failure_is_not_flagged(alice, monkeypatch):
    import gamma.routers.ai as ai_mod
    from gamma.ai_client import UpstreamError

    def fake_call(*a, **kw):
        raise UpstreamError(502, "upstream 502: Bad gateway")

    monkeypatch.setattr(ai_mod, "_call_ai", fake_call)
    pid = alice.get("/api/ai/settings").json()["providers"][0]["id"]
    body = alice.post(f"/api/ai/providers/{pid}/test").json()
    assert body["ok"] is False and body["auth"] is False


def test_provider_test_unknown_entry_404(alice):
    assert alice.post("/api/ai/providers/does-not-exist/test").status_code == 404


def test_probe_uses_configured_test_model(alice, monkeypatch):
    # Probe model priority: the entry-level test_model, else the model sent
    # with the request (the client's effective metadata model), else the first
    # model. The masked settings echo test_model so the form can edit it.
    import gamma.routers.ai as ai_mod

    seen = {}
    monkeypatch.setattr(ai_mod, "_call_ai", lambda m, s, entry, rt, **kw: seen.update(entry))
    pid = alice.get("/api/ai/settings").json()["providers"][0]["id"]
    r = alice.put(f"/api/ai/providers/{pid}", json={"test_model": "claude-cheap"})
    assert r.status_code == 200
    assert r.json()["providers"][0]["test_model"] == "claude-cheap"
    assert alice.post(f"/api/ai/providers/{pid}/test").json()["ok"] is True
    assert seen["model"] == "claude-cheap"
    # An explicit test_model beats the request's fallback model.
    alice.post(f"/api/ai/providers/{pid}/test", json={"model": "claude-meta"})
    assert seen["model"] == "claude-cheap"

    alice.put(f"/api/ai/providers/{pid}", json={"test_model": ""})
    alice.post(f"/api/ai/providers/{pid}/test")
    assert seen["model"] == "claude-solo"
    # With no test_model, the request's metadata-model fallback wins over the
    # first model.
    alice.post(f"/api/ai/providers/{pid}/test", json={"model": "claude-meta"})
    assert seen["model"] == "claude-meta"
    assert alice.put(f"/api/ai/providers/{pid}", json={"test_model": "x" * 101}).status_code == 400


# --- upstream error summarization ---------------------------------------------

def _http_error(code, body, reason="err"):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://x", code, reason, None, io.BytesIO(body.encode()))


def test_upstream_detail_summarizes_noise_bodies():
    # HTML error pages (a proxy's 502) collapse to their <title>, JSON errors
    # to their message — a settings row never shows raw markup.
    from gamma.ai_client import upstream_detail

    html = "<!DOCTYPE html> <html><head><title>xwtim.com | 502:\n Bad gateway</title></head><body>Error</body></html>"
    assert upstream_detail(_http_error(502, html)) == "upstream 502: xwtim.com | 502: Bad gateway"
    js = '{"error": {"message": "Provided authentication token is expired.", "code": "token_expired"}}'
    assert upstream_detail(_http_error(401, js)) == "upstream 401: Provided authentication token is expired."
    assert upstream_detail(_http_error(500, "plain text failure")) == "upstream 500: plain text failure"
    assert upstream_detail(_http_error(502, "<html><body>no title</body></html>", "Bad Gateway")) \
        == "upstream 502: Bad Gateway"


# --- login connection check (/api/ai/health) ----------------------------------

def test_ai_health_reports_unconfigured(guest):
    r = guest.post("/api/ai/health", json={})
    assert r.status_code == 200
    assert r.json() == {"configured": False, "ok": True}


def test_ai_health_ping_checks_credential_for_free(alice, monkeypatch):
    # "ping" mode never runs a completion: API keys are checked via the
    # provider's model listing; 401 comes back as a broken-credential flag,
    # 404 (gateway without /v1/models) as ok-but-unverified.
    from gamma import ai_catalog

    pid = alice.get("/api/ai/settings").json()["providers"][0]["id"]
    seen = {}
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: seen.update(url=req.full_url) or {})
    body = alice.post("/api/ai/health", json={"provider_id": pid, "mode": "ping"}).json()
    assert body["configured"] and body["ok"] is True
    assert "/v1/models" in seen["url"]

    def dead_key(req):
        raise _http_error(401, '{"error": {"message": "invalid x-api-key"}}')
    monkeypatch.setattr(ai_catalog, "fetch_json", dead_key)
    body = alice.post("/api/ai/health", json={"provider_id": pid, "mode": "ping"}).json()
    assert body["ok"] is False and body["auth"] is True
    assert "invalid x-api-key" in body["error"]

    def no_listing(req):
        raise _http_error(404, "")
    monkeypatch.setattr(ai_catalog, "fetch_json", no_listing)
    body = alice.post("/api/ai/health", json={"provider_id": pid, "mode": "ping"}).json()
    assert body["ok"] is True and body["unverified"] is True


def test_ai_health_test_mode_runs_the_probe(alice, monkeypatch):
    # provider_id "" targets the first entry; "test" mode is the Test button's
    # tiny live completion.
    import gamma.routers.ai as ai_mod

    monkeypatch.setattr(ai_mod, "_call_ai", lambda *a, **kw: "ok")
    body = alice.post("/api/ai/health", json={"mode": "test"}).json()
    assert body["configured"] and body["ok"] is True
    assert body["model"] == "claude-solo" and body["provider_name"]


def test_export_stays_owner_only(alice):
    # Keys ride along inside data.db in the owner's backup — which is fine
    # exactly because only the owner's session can request it.
    from gamma.app import app
    anon = TestClient(app)
    assert anon.get("/api/export").status_code == 401
    r = alice.get("/api/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "data.db" in z.namelist()


def test_deleting_all_providers_disables_ai(alice):
    for p in alice.get("/api/ai/settings").json()["providers"]:
        assert alice.delete(f"/api/ai/providers/{p['id']}").status_code == 200
    assert alice.get("/api/ai/settings").json()["providers"] == []
    assert alice.get("/api/ai/models").json()["enabled"] is False


# --- the server's shared AI connections (/api/admin/ai-providers*) -----------

SHARED_KEY = "sk-shared-lab-key-4242"


@pytest.fixture(scope="module")
def admin(client):
    from conftest import login, make_user
    make_user("prefs_admin", "pw", is_admin=1)
    return login("prefs_admin", "pw")


@pytest.fixture
def shared(admin):
    """One shared entry for the test; the server's list is emptied after it
    (the whole run shares one users.db, and other tests expect no AI)."""
    from gamma import ai_settings
    r = admin.post("/api/admin/ai-providers", json={
        "protocol": "openai", "name": "Lab key", "api_key": SHARED_KEY,
        "base_url": "https://llm.example.org", "models": "lab-model, lab-big",
    })
    assert r.status_code == 200, r.text
    try:
        yield r.json()["providers"][0]
    finally:
        ai_settings.save_server_ai({"providers": [], "guests": False})


def test_shared_provider_is_masked_and_encrypted_at_rest(admin, shared):
    from gamma.server_settings import _get_raw
    assert shared["id"].startswith("server:") and shared["shared"] is True
    assert shared["key_hint"] == "…4242" and shared["label"] == "Lab key"
    listed = admin.get("/api/admin/ai-providers").json()
    assert SHARED_KEY not in str(listed) and listed["guests"] is False
    # API-key protocols only: the form never offers the ChatGPT sign-in.
    assert "chatgpt" not in [p["id"] for p in listed["protocols"]]
    raw = _get_raw("ai_providers")
    assert raw and SHARED_KEY not in raw and "lab-model" in raw
    # Edits keep the stored key unless a new one is sent; validation is the
    # user entries' own.
    r = admin.put(f"/api/admin/ai-providers/{shared['id']}", json={"models": "lab-model"})
    assert r.json()["providers"][0]["models"] == "lab-model"
    assert r.json()["providers"][0]["key_hint"] == "…4242"
    assert admin.put(f"/api/admin/ai-providers/{shared['id']}", json={"protocol": "chatgpt"}).status_code == 400
    assert admin.post("/api/admin/ai-providers", json={"protocol": "chatgpt", "api_key": "sk-x-123456"}).status_code == 400
    assert admin.post("/api/admin/ai-providers", json={"protocol": "openai"}).status_code == 400
    assert admin.post("/api/admin/ai-providers",
                      json={"protocol": "openai", "api_key": "sk-ok-123456", "base_url": "ftp://x"}).status_code == 400
    assert admin.put("/api/admin/ai-providers/server:nope", json={"name": "x"}).status_code == 404


def test_shared_provider_admin_api_is_admin_session_only(alice, admin, shared):
    from gamma.app import app
    from gamma.integrations import create_token
    from conftest import workspace_of
    for method, path in (("get", "/api/admin/ai-providers"), ("put", "/api/admin/ai-providers"),
                         ("post", "/api/admin/ai-providers"),
                         ("put", f"/api/admin/ai-providers/{shared['id']}"),
                         ("delete", f"/api/admin/ai-providers/{shared['id']}")):
        assert getattr(alice, method)(path, **({} if method in ("get", "delete") else {"json": {}})).status_code == 403
    # An admin's integration token is not an admin session.
    token = create_token("prefs_admin", workspace_of("prefs_admin"), "script", 1)["token"]
    bearer = TestClient(app)
    bearer.headers["Authorization"] = f"Bearer {token}"
    assert bearer.get("/api/admin/ai-providers").status_code == 403
    assert bearer.post("/api/admin/ai-providers", json={"protocol": "openai", "api_key": "sk-tok-123456"}).status_code == 403


def test_every_account_gets_shared_models_after_its_own(alice, admin, shared, monkeypatch):
    import gamma.routers.ai as ai_mod
    sid = shared["id"]
    models = alice.get("/api/ai/models").json()
    assert models["enabled"] is True
    assert models["default"] == f"{sid}:lab-model"
    assert [m["id"] for m in models["models"]] == [f"{sid}:lab-model", f"{sid}:lab-big"]
    assert all(m["shared"] and m["provider_name"] == "Lab key" for m in models["models"])

    # A member sees a read-only row without the key hint; the admin sees it.
    row = next(p for p in alice.get("/api/ai/settings").json()["providers"] if p["id"] == sid)
    assert row["shared"] is True and row["key_hint"] == ""
    admin_row = next(p for p in admin.get("/api/ai/settings").json()["providers"] if p["id"] == sid)
    assert admin_row["key_hint"] == "…4242"
    # ...and cannot touch it through the account's own endpoints.
    assert alice.put(f"/api/ai/providers/{sid}", json={"name": "mine"}).status_code == 404
    alice.delete(f"/api/ai/providers/{sid}")
    assert any(p["id"] == sid for p in alice.get("/api/ai/settings").json()["providers"])
    assert alice.post(f"/api/ai/providers/{sid}/test").status_code == 404
    assert alice.post("/api/ai/model-catalog", json={"provider_id": sid}).status_code == 400

    # The account's own entry wins the default; the shared models follow.
    own = alice.post("/api/ai/providers", json={
        "protocol": "anthropic", "api_key": "sk-ant-own-key-0001", "models": "own-model"}).json()["providers"][0]
    models = alice.get("/api/ai/models").json()
    assert models["default"] == f"{own['id']}:own-model"
    assert [m["id"] for m in models["models"]][1:] == [f"{sid}:lab-model", f"{sid}:lab-big"]

    # Chat through the shared entry: the stored key goes upstream, the
    # tokens are recorded on the member's account.
    seen = {}

    def fake_call(messages, system, entry, rt, **kw):
        seen.update(provider=rt["providers"][entry["provider"]], model=entry["model"])
        kw["on_usage"]({"input": 7, "output": 3})
        return "ok"
    monkeypatch.setattr(ai_mod, "_call_ai", fake_call)
    # The login check reaches the shared entry for any account.
    body = alice.post("/api/ai/health", json={"provider_id": sid, "mode": "test"}).json()
    assert body["ok"] is True and body["provider_id"] == sid
    assert seen["provider"]["api_key"] == SHARED_KEY and seen["provider"]["base_url"] == "https://llm.example.org"
    usage = alice.get("/api/ai/usage").json()
    assert any(m["provider_id"] == sid for m in usage["models"])
    assert alice.delete(f"/api/ai/providers/{own['id']}").status_code == 200


def test_admin_tests_and_lists_models_of_a_shared_entry(admin, shared, monkeypatch):
    import gamma.routers.ai as ai_mod
    from gamma import ai_catalog
    sid = shared["id"]
    seen = {}
    monkeypatch.setattr(ai_mod, "_call_ai", lambda m, s, entry, rt, **kw: seen.update(entry) or "ok")
    body = admin.post(f"/api/ai/providers/{sid}/test").json()
    assert body["ok"] is True and seen == {"provider": sid, "model": "lab-model"}

    def listing(req):
        seen.update(url=req.full_url, auth=req.headers.get("Authorization"))
        return {"data": [{"id": "lab-model"}, {"id": "lab-new"}]}
    monkeypatch.setattr(ai_catalog, "fetch_json", listing)
    r = admin.post("/api/ai/model-catalog", json={"provider_id": sid})
    assert r.status_code == 200 and r.json()["models"] == ["lab-model", "lab-new"]
    assert seen["url"] == "https://llm.example.org/v1/models" and seen["auth"] == f"Bearer {SHARED_KEY}"


def test_guests_get_shared_entries_only_when_switched_on(guest, admin, shared):
    assert guest.get("/api/ai/models").json()["enabled"] is False
    assert guest.get("/api/ai/settings").json()["providers"] == []
    r = admin.put("/api/admin/ai-providers", json={"guests": True})
    assert r.status_code == 200 and r.json()["guests"] is True
    models = guest.get("/api/ai/models").json()
    assert models["enabled"] is True and models["default"] == f"{shared['id']}:lab-model"
    rows = guest.get("/api/ai/settings").json()
    assert rows["can_edit"] is False and rows["providers"][0]["key_hint"] == ""
    assert admin.put("/api/admin/ai-providers", json={"guests": False}).json()["guests"] is False
    assert guest.get("/api/ai/models").json()["enabled"] is False


def test_deleting_a_shared_entry_removes_it_everywhere(alice, admin, shared):
    r = admin.delete(f"/api/admin/ai-providers/{shared['id']}")
    assert r.status_code == 200 and r.json()["providers"] == []
    assert alice.get("/api/ai/models").json()["enabled"] is False


def test_shared_provider_cap(admin, shared):
    from gamma.ai_settings import MAX_PROVIDERS
    for i in range(MAX_PROVIDERS - 1):
        assert admin.post("/api/admin/ai-providers",
                          json={"protocol": "openai", "api_key": f"sk-cap-key-{i:04d}"}).status_code == 200
    assert admin.post("/api/admin/ai-providers",
                      json={"protocol": "openai", "api_key": "sk-cap-key-over"}).status_code == 400


# --- manage.py rename-user ----------------------------------------------------

def test_rename_user_moves_rows_and_directory(client):
    import manage
    from conftest import workspace_of
    from gamma.db import ws_dir
    from gamma.app import app

    manage.create_user("prefs_bob", "pw2")
    ws = workspace_of("prefs_bob")
    assert (ws_dir(ws) / "pages.db").exists()

    manage.rename_user("prefs_bob", "prefs_bobby")
    # Rows follow the account; the workspace directory (named by id) stays put.
    assert workspace_of("prefs_bobby") == ws and (ws_dir(ws) / "pages.db").exists()

    c = TestClient(app)
    assert c.post("/api/login", json={"username": "prefs_bob", "password": "pw2"}).status_code == 401
    assert c.post("/api/login", json={"username": "prefs_bobby", "password": "pw2"}).status_code == 200


def test_rename_user_refuses_guest_and_collisions(client, capsys):
    import manage
    manage.rename_user("guest", "prefs_someone")
    assert "cannot be renamed" in capsys.readouterr().out
    manage.create_user("prefs_carol", "pw3")
    manage.rename_user("prefs_carol", "prefs_bobby")  # prefs_bobby exists from the test above
    assert "already exists" in capsys.readouterr().out
    manage.rename_user("prefs_carol", "bad/name")
    assert "must be" in capsys.readouterr().out
