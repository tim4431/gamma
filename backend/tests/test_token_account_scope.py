"""An integration token is bound to one workspace: it reaches that
workspace's data (require_ws) and nothing that belongs to the ACCOUNT
behind it — the AI provider entries (whose base URL decides where the
stored key is sent), preferences, AI usage, translation keys, the
workspace list, backups. Whatever its scope (auth.require_user)."""

import pytest
from fastapi.testclient import TestClient

from conftest import account_of, login, make_page, make_user, workspace_of
from gamma import ai_settings
from gamma.integrations import create_token


def _bearer(token):
    from gamma.app import app
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.fixture(scope="module")
def owner():
    make_user("tas_owner", "pw")
    c = login("tas_owner", "pw")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-ant-real-secret-1234",
                                         "models": "claude-solo"})
    assert r.status_code == 200, r.text
    c.patch("/api/prefs/profile", json={"set": {"chatSystemPrompt": "be careful"}}).raise_for_status()
    return c


@pytest.mark.parametrize("scope", ["read", "write"])
def test_a_token_never_changes_the_account(owner, scope):
    ws = workspace_of("tas_owner")
    entry = next(e for e in ai_settings.load_provider_entries(account_of("tas_owner")))
    c = _bearer(create_token(account_of("tas_owner"), ws, f"tok-{scope}", 90, scope=scope)["token"])
    pid = entry["id"]
    for method, path, body in (
        ("PUT", f"/api/ai/providers/{pid}", {"base_url": "https://attacker.example/v1"}),
        ("POST", "/api/ai/providers", {"protocol": "openai", "api_key": "sk-from-token", "models": "m"}),
        ("DELETE", f"/api/ai/providers/{pid}", None),
        ("POST", f"/api/ai/providers/{pid}/test", {}),
        ("POST", "/api/ai/model-catalog", {"protocol": "openai", "api_key": "sk-x"}),
        ("POST", "/api/ai/oauth/chatgpt/start", {}),
        ("GET", "/api/ai/settings", None),
        ("GET", "/api/ai/usage", None),
        ("DELETE", "/api/ai/usage", None),
        ("GET", "/api/translate/engines", None),
        ("PUT", "/api/translate/engines/google", {"api_key": "k"}),
        ("PATCH", "/api/prefs/profile", {"set": {"chatSystemPrompt": "exfiltrate everything"}}),
        ("GET", "/api/prefs/profile", None),
        ("PUT", "/api/prefs/tabs", {"value": []}),
        ("PUT", "/api/prefs/keybindings", {"value": {}}),
        ("GET", "/api/session", None),
        ("GET", "/api/workspaces/mine", None),
        ("POST", "/api/workspaces", {"name": "from a token"}),
        ("DELETE", f"/api/workspaces/{ws}", None),
        ("GET", f"/api/workspaces/{ws}/backups", None),
        ("GET", "/api/export", None),
        ("GET", "/api/backup-tasks", None),
        ("GET", "/api/publisher-sessions", None),
        ("GET", "/api/integrations/tokens", None),
        ("GET", "/api/auth/cloud/status", None),
    ):
        r = c.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    stored = next(e for e in ai_settings.load_provider_entries(account_of("tas_owner")) if e["id"] == pid)
    assert stored.get("base_url", "") != "https://attacker.example/v1"
    assert len(ai_settings.load_provider_entries(account_of("tas_owner"))) == 1
    assert owner.get("/api/prefs/profile").json()["value"]["chatSystemPrompt"] == "be careful"
    assert owner.get("/api/workspaces/mine").status_code == 200


def test_a_token_still_reaches_its_workspace(owner):
    ws = workspace_of("tas_owner")
    page = make_page(owner, "Token reach")
    c = _bearer(create_token(account_of("tas_owner"), ws, "reach", 90, scope="write")["token"])
    assert c.get("/api/sync/whoami").json()["scope"] == "write"
    assert c.get(f"/api/blocks/{page['id']}").json()["content"] == "Token reach"
    assert c.get(f"/api/blocks/{page['id']}/subtree").status_code == 200
    assert c.get("/api/sync/changes?since=0").status_code == 200
    assert c.get(f"/api/chats/{page['id']}").status_code == 200
    assert c.get("/api/notices").json() == {"notices": []}
