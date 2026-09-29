"""Search settings isolate credentials, keep secrets off APIs, and select one service."""

import json

import pytest
from fastapi.testclient import TestClient

from conftest import login, make_user, workspace_of
from gamma import ai_settings, db, web_search, web_search_settings as settings
from gamma.integrations import create_token

URL = "/api/ai/web-search"
OPENAI_KEY = "sk-search-private-1234"
BRAVE_KEY = "brave-search-private-5678"
USERS = ("search_settings_alice", "search_settings_bob")


@pytest.fixture(scope="module")
def accounts(client):
    for user in USERS:
        make_user(user, "password")
    return tuple(login(user, "password") for user in USERS)


@pytest.fixture(autouse=True)
def clean_settings(accounts, monkeypatch):
    for name in ("GAMMA_WEB_SEARCH_PROVIDER", "GAMMA_BRAVE_SEARCH_API_KEY", "GAMMA_SEARXNG_URL"):
        monkeypatch.delenv(name, raising=False)
    with db.connect_users_db() as conn:
        for user in USERS:
            conn.execute("DELETE FROM user_prefs WHERE username=? AND key IN (?, ?)",
                         (user, settings.SEARCH_PREF_KEY, ai_settings.AI_SETTINGS_PREF_KEY))


def connections(*entries):
    ai_settings.save_provider_entries(USERS[0], list(entries))


def official(entry_id="openai-one", key="sk-own-official-1234", **extra):
    return {"id": entry_id, "protocol": "openai", "api_key": key,
            "base_url": "https://api.openai.com", "name": "My OpenAI", **extra}


def put(caller, **fields):
    response = caller.put(URL, json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_are_masked_encrypted_and_account_wide(accounts):
    alice, bob = accounts
    before = alice.get(URL).json()
    assert before["provider"] == "auto" and before["openai_model"] == settings.DEFAULT_MODEL
    assert before["can_edit"] and not before["configured"]
    view = put(alice, provider="openai", openai_api_key=OPENAI_KEY, brave_api_key=BRAVE_KEY)
    assert view["configured"] and view["effective_provider"] == "openai"
    assert view["openai_key_hint"] == "…1234" and view["brave_key_hint"] == "…5678"
    assert OPENAI_KEY not in json.dumps(view) and BRAVE_KEY not in json.dumps(view)
    stored = db.get_pref(USERS[0], settings.SEARCH_PREF_KEY, "unrelated-workspace")[0]
    assert OPENAI_KEY not in json.dumps(stored) and BRAVE_KEY not in json.dumps(stored)
    assert stored["openai_api_key"].startswith("gAAAA")
    assert settings.credentials(USERS[0]) == {
        "provider": "openai", "api_key": OPENAI_KEY, "model": settings.DEFAULT_MODEL, "url": settings.OPENAI_URL,
    }
    assert not bob.get(URL).json()["configured"]
    assert alice.get(URL, headers={"X-Gamma-Workspace": "unrelated-workspace"}).json() == view
    assert settings.SEARCH_PREF_KEY in db.USER_PREF_KEYS
    assert alice.get("/api/prefs/" + settings.SEARCH_PREF_KEY).status_code == 400
    assert alice.put("/api/prefs/" + settings.SEARCH_PREF_KEY, json={"value": {}}).status_code == 400


def test_blank_secrets_keep_ciphertext_clear_flags_remove_and_partial_saves_preserve(accounts):
    alice, _ = accounts
    put(alice, provider="brave", openai_api_key=OPENAI_KEY, brave_api_key=BRAVE_KEY)
    before = db.get_pref(USERS[0], settings.SEARCH_PREF_KEY)[0]
    put(alice, openai_api_key="", brave_api_key="  ", openai_model="gpt-4.1")
    stored = db.get_pref(USERS[0], settings.SEARCH_PREF_KEY)[0]
    assert stored["openai_api_key"] == before["openai_api_key"]
    assert stored["brave_api_key"] == before["brave_api_key"]
    assert stored["provider"] == "brave" and stored["openai_model"] == "gpt-4.1"
    assert settings.credentials(USERS[0])["api_key"] == BRAVE_KEY
    view = put(alice, clear_brave_api_key=True)
    assert not view["configured"] and not view["brave_key_hint"]
    assert view["openai_key_hint"] == "…1234"
    with pytest.raises(settings.SearchConfigurationError) as error:
        settings.credentials(USERS[0])
    assert error.value.code == "not_configured"  # explicit Brave does not switch to OpenAI
    view = put(alice, clear_openai_api_key=True)
    assert not view["openai_key_hint"]


def test_auto_prefers_openai_then_brave_then_searxng_and_off_disables(accounts):
    alice, _ = accounts
    put(alice, openai_api_key=OPENAI_KEY, brave_api_key=BRAVE_KEY, searxng_url="https://search.example/searx")
    assert settings.credentials(USERS[0])["provider"] == "openai"
    put(alice, clear_openai_api_key=True)
    assert settings.credentials(USERS[0])["provider"] == "brave"
    put(alice, clear_brave_api_key=True)
    assert settings.credentials(USERS[0]) == {
        "provider": "searxng", "api_key": "", "model": "", "url": "https://search.example/searx/search",
    }
    view = put(alice, provider="off")
    assert not view["configured"] and view["effective_provider"] == ""
    with pytest.raises(settings.SearchConfigurationError) as error:
        settings.credentials(USERS[0])
    assert error.value.code == "disabled"


def test_only_own_official_api_connections_are_eligible(accounts, monkeypatch):
    alice, _ = accounts
    connections(official(), official("v1", base_url="https://api.openai.com/v1/"),
                official("proxy", base_url="https://openrouter.ai"),
                official("lookalike", base_url="https://api.openai.com.attacker.example"),
                official("credentials", base_url="https://user:pass@api.openai.com"),
                official("path", base_url="https://api.openai.com/proxy"),
                official("query", base_url="https://api.openai.com?key=private"),
                official("port", base_url="https://api.openai.com:8443"),
                official("http", base_url="http://api.openai.com"),
                official("oauth", protocol="chatgpt"), official("server:shared"))
    monkeypatch.setattr(ai_settings, "shared_access", lambda *args: pytest.fail("shared keys must not be inspected"))
    view = alice.get(URL).json()
    assert view["connections"] == [{"id": "openai-one", "label": "My OpenAI"}, {"id": "v1", "label": "My OpenAI"}]
    assert view["configured"] and view["effective_provider"] == "openai"
    assert settings.credentials(USERS[0])["api_key"] == "sk-own-official-1234"
    assert not view["openai_key_hint"]  # only a separately saved key has this hint
    response = alice.put(URL, json={"openai_connection": "server:shared"})
    assert response.status_code == 400
    response = alice.put(URL, json={"openai_connection": "oauth"})
    assert response.status_code == 400


def test_default_endpoint_override_cannot_make_a_gateway_key_eligible(accounts, monkeypatch):
    from gamma import config
    connections(official(base_url=""))
    monkeypatch.setitem(config.AI_BASE_URLS, "openai", "https://gateway.example")
    assert accounts[0].get(URL).json()["connections"] == []


def test_invalid_reused_key_fails_without_echoing_it(accounts):
    connections(official(key="sk-private-密钥"))
    view = accounts[0].get(URL).json()
    assert not view["configured"] and "sk-private" not in json.dumps(view)
    with pytest.raises(settings.SearchConfigurationError, match="API key is invalid"):
        settings.credentials(USERS[0])


def test_explicit_connection_beats_dedicated_key_and_never_substitutes_when_removed(accounts):
    alice, _ = accounts
    connections(official(), official("second", key="sk-second-personal-9876"))
    put(alice, provider="openai", openai_api_key=OPENAI_KEY)
    assert settings.credentials(USERS[0])["api_key"] == OPENAI_KEY
    put(alice, openai_connection="second")
    assert settings.credentials(USERS[0])["api_key"] == "sk-second-personal-9876"
    connections(official())
    view = alice.get(URL).json()
    assert view["openai_connection"] == "second" and not view["configured"]
    with pytest.raises(settings.SearchConfigurationError, match="selected OpenAI connection"):
        settings.credentials(USERS[0])
    put(alice, openai_connection="", clear_openai_api_key=True)
    assert settings.credentials(USERS[0])["api_key"] == "sk-own-official-1234"


@pytest.mark.parametrize("destination", ["other_account", "other_field", "corrupt"])
def test_encrypted_credentials_are_bound_to_account_and_field(accounts, destination):
    alice, _ = accounts
    put(alice, provider="openai", openai_api_key=OPENAI_KEY)
    stored = db.get_pref(USERS[0], settings.SEARCH_PREF_KEY)[0]
    target = USERS[0]
    if destination == "other_account":
        target = USERS[1]
    elif destination == "other_field":
        stored.update(provider="brave", brave_api_key=stored.pop("openai_api_key"))
    else:
        stored["openai_api_key"] = "corrupted-ciphertext"
    db.set_pref(target, settings.SEARCH_PREF_KEY, stored)
    with pytest.raises(settings.SearchConfigurationError, match="cannot be read"):
        settings.credentials(target)
    view = settings.masked(target, can_edit=True)
    assert not view["configured"] and OPENAI_KEY not in json.dumps(view)
    assert view["brave_key_hint" if destination == "other_field" else "openai_key_hint"] == "set"


def test_legacy_environment_is_used_only_before_account_settings_are_saved(accounts, monkeypatch):
    alice, _ = accounts
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", BRAVE_KEY)
    view = alice.get(URL).json()
    assert view["configured"] and view["effective_provider"] == "brave"
    assert not view["brave_key_hint"]  # no server credential hint in account settings
    assert settings.credentials(USERS[0])["api_key"] == BRAVE_KEY
    put(alice)  # an intentional empty account configuration suppresses legacy fallback
    assert not alice.get(URL).json()["configured"]
    with pytest.raises(settings.SearchConfigurationError) as error:
        settings.credentials(USERS[0])
    assert error.value.code == "not_configured"


def test_legacy_explicit_provider_does_not_fall_back_and_own_openai_precedes_legacy(accounts, monkeypatch):
    monkeypatch.setenv("GAMMA_WEB_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "https://search.example")
    with pytest.raises(settings.SearchConfigurationError, match="Brave"):
        settings.credentials(USERS[0])
    connections(official())
    assert settings.credentials(USERS[0])["provider"] == "openai"


def test_account_resolution_never_spends_server_credentials_without_an_account(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", BRAVE_KEY)
    with pytest.raises(settings.SearchConfigurationError) as error:
        settings.credentials("")
    assert error.value.code == "not_configured"


@pytest.mark.parametrize("fields", [
    {"searxng_url": "file:///tmp/search"},
    {"searxng_url": "https://user:private-secret@search.example"},
    {"searxng_url": "https://search.example?token=private-secret"},
    {"searxng_url": "https://search.example/#private-secret"},
    {"searxng_url": "https://bad^host.example"},
    {"searxng_url": "https://search.example:bad"},
    {"openai_model": "model\r\nprivate-secret"},
    {"openai_api_key": "private-secret\r\nheader"},
    {"brave_api_key": "private-secret-" * 50},
    {"openai_api_key": {"key": "private-secret"}},
    {"openai_api_key": "private-secret", "clear_openai_api_key": True},
])
def test_invalid_configuration_never_echoes_keys(accounts, fields):
    response = accounts[0].put(URL, json=fields)
    assert response.status_code == 400
    assert "private-secret" not in response.text
    assert db.get_pref(USERS[0], settings.SEARCH_PREF_KEY)[0] is None


def test_guest_anonymous_and_integration_tokens_cannot_change_or_test_settings(accounts, anon):
    assert anon.get(URL).status_code == 401
    guest = TestClient(anon.app)
    assert guest.post("/api/login-guest").status_code == 200
    assert guest.get(URL).json()["can_edit"] is False
    for method, path in (("PUT", URL), ("POST", URL + "/test")):
        assert guest.request(method, path, json={}).status_code == 403
    for scope in ("read", "write"):
        token = create_token(USERS[0], workspace_of(USERS[0]), "search-settings", 90, scope=scope)["token"]
        caller = TestClient(anon.app, headers={"Authorization": "Bearer " + token})
        for method, path in (("GET", URL), ("PUT", URL), ("POST", URL + "/test")):
            assert caller.request(method, path, json={}).status_code == 403


def test_service_check_uses_only_saved_account_and_a_fixed_sample(accounts, monkeypatch):
    alice, _ = accounts
    put(alice, provider="brave", brave_api_key=BRAVE_KEY)
    calls = []

    def search(query, limit, *, user):
        calls.append((query, limit, user))
        assert settings.credentials(user)["api_key"] == BRAVE_KEY
        return [{"title": "Raman cooling", "url": "https://lab.example/paper", "snippet": "", "provider": "brave"}]

    monkeypatch.setattr(web_search, "search_web", search)
    result = alice.post(URL + "/test", json={"query": "private document context", "brave_api_key": "ignored-key"})
    assert result.json() == {"ok": True, "provider": "brave", "count": 1}
    assert calls == [("Raman sideband cooling paper", 1, USERS[0])]
    assert "private document" not in result.text and BRAVE_KEY not in result.text


def test_service_check_reports_safe_failures_and_missing_settings(accounts, monkeypatch):
    alice, _ = accounts
    monkeypatch.setattr(web_search, "search_web", lambda *args, **kw: pytest.fail("no configured service"))
    result = alice.post(URL + "/test").json()
    assert not result["ok"] and result["code"] == "not_configured"
    put(alice, provider="brave", brave_api_key=BRAVE_KEY)

    def fail(*args, **kwargs):
        raise web_search.WebSearchError("Web search is rate limited; retry later.", code="rate_limit")

    monkeypatch.setattr(web_search, "search_web", fail)
    result = alice.post(URL + "/test").json()
    assert result == {"ok": False, "provider": "brave", "code": "rate_limit",
                      "error": "Web search is rate limited; retry later."}
