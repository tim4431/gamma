"""ChatGPT subscription sign-in: OAuth helpers, the connect endpoints (a
pasted address, the device code, the redirect caught on this machine), and
the chatgpt wire protocol (Responses API request shape + SSE parsing). All
external calls are faked — no network beyond a loopback listener."""

import base64
import io
import json
import time
import urllib.error
import urllib.request

import pytest

import gamma.chatgpt_oauth as co
from conftest import account_of
from gamma import ai_catalog
from gamma.ai_protocols import WIRES, chatgpt as chatgpt_proto
from gamma.routers.ai import _sse_deltas

_chatgpt_request = WIRES["chatgpt"].request


def _fake_jwt(claims: dict) -> str:
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


def _fake_tokens(exp=None, email="tim@example.com", account="acct-123"):
    return {
        "access_token": _fake_jwt({
            "exp": exp or int(time.time()) + 3600,
            "https://api.openai.com/auth": {"chatgpt_account_id": account},
        }),
        "refresh_token": "rt-1",
        "id_token": _fake_jwt({"email": email}),
    }


@pytest.fixture(scope="module")
def erin(client):
    """A non-guest user (guests may not store credentials)."""
    from conftest import login, make_user

    make_user("erin", "pw")
    c = login("erin", "pw")
    return c


# --- OAuth helpers ------------------------------------------------------------

def test_parse_callback_accepts_url_and_bare_code():
    url = "http://localhost:1455/auth/callback?code=abc123&state=st1"
    assert co.parse_callback(url, "st1") == "abc123"
    assert co.parse_callback("rawcode", "st1") == "rawcode"
    with pytest.raises(ValueError):
        co.parse_callback(url, "other-state")  # state mismatch
    with pytest.raises(ValueError):
        co.parse_callback("http://localhost:1455/auth/callback?error=denied", "st1")


def test_start_auth_url_carries_pkce_and_state():
    state, verifier, url = co.start_auth()
    assert state in url and "code_challenge=" in url and "S256" in url
    assert len(verifier) > 40
    assert url.startswith("https://auth.openai.com/oauth/authorize?")


def test_exchange_code_extracts_account_and_expiry(monkeypatch):
    monkeypatch.setattr(co, "_token_request", lambda form: _fake_tokens(exp=1_900_000_000))
    oauth = co.exchange_code("code", "verifier")
    assert oauth["account_id"] == "acct-123"
    assert oauth["email"] == "tim@example.com"
    assert oauth["expires_at"] == 1_900_000_000
    assert oauth["refresh_token"] == "rt-1"


# --- Connect endpoints --------------------------------------------------------

def test_guest_cannot_start_oauth(guest):
    assert guest.post("/api/ai/oauth/chatgpt/start").status_code == 403


def test_plain_add_endpoint_refuses_chatgpt_protocol(erin):
    r = erin.post("/api/ai/providers", json={"protocol": "chatgpt", "api_key": "x"})
    assert r.status_code == 400
    assert "sign" in r.json()["detail"].lower()


def test_model_catalog_needs_signin_before_connect(erin):
    # No connected ChatGPT entry yet — the account-gated list can't be served,
    # and a stale hardcoded one must not be.
    r = erin.post("/api/ai/model-catalog", json={"protocol": "chatgpt"})
    assert r.status_code == 400
    assert "sign in" in r.json()["detail"].lower()


def test_connect_flow_creates_masked_entry_and_models(erin, monkeypatch):
    monkeypatch.setattr(co, "_token_request", lambda form: _fake_tokens())
    monkeypatch.setattr(chatgpt_proto, "codex_client_version", lambda: "9.9.9")
    # A fresh connect seeds its model list live from the account (first two).
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp({"models": [
        {"slug": "gpt-6-sol", "visibility": "list"},
        {"slug": "gpt-6-terra", "visibility": "list"},
        {"slug": "gpt-6-luna", "visibility": "list"},
    ]}))
    state = erin.post("/api/ai/oauth/chatgpt/start").json()["state"]
    r = erin.post("/api/ai/oauth/chatgpt/complete", json={
        "state": state,
        "callback": f"http://localhost:1455/auth/callback?code=abc&state={state}",
        "name": "My ChatGPT",
    })
    assert r.status_code == 200, r.text
    entry = next(p for p in r.json()["providers"] if p["protocol"] == "chatgpt")
    assert entry["oauth_connected"] is True
    assert entry["account"] == "tim@example.com"
    assert entry["models"] == "gpt-6-sol, gpt-6-terra"
    assert "access" not in json.dumps(entry)  # tokens never reach the browser

    models = erin.get("/api/ai/models").json()
    assert any(m["model"] == "gpt-6-sol" and m["provider"] == entry["id"] for m in models["models"])

    # replay of the same state is rejected (one-shot verifier)
    r2 = erin.post("/api/ai/oauth/chatgpt/complete", json={"state": state, "callback": "code"})
    assert r2.status_code == 400


def test_expired_token_is_refreshed_lazily(erin, monkeypatch):
    from gamma.ai_settings import ai_runtime, load_provider_entries, save_provider_entries

    entries = load_provider_entries(account_of("erin"))
    entry = next(e for e in entries if e.get("protocol") == "chatgpt")
    entry["oauth"]["expires_at"] = int(time.time()) - 10  # force expiry
    save_provider_entries(account_of("erin"), entries)

    fresh = _fake_tokens(exp=int(time.time()) + 7200)
    monkeypatch.setattr(co, "_token_request", lambda form: fresh)
    rt = ai_runtime(account_of("erin"))
    conf = rt["providers"][entry["id"]]
    assert conf["api_key"] == fresh["access_token"]
    # …and the refreshed token was persisted for the next request
    saved = next(e for e in load_provider_entries(account_of("erin")) if e["id"] == entry["id"])
    assert saved["oauth"]["access_token"] == fresh["access_token"]


def test_concurrent_requests_refresh_once(erin, monkeypatch):
    # OpenAI rotates refresh tokens: of two parallel refreshes the second
    # fails. Parallel requests near expiry (the translator fires dozens) must
    # share one refresh, and nobody may save stale tokens over the fresh ones.
    import threading
    from gamma.ai_settings import ai_runtime, load_provider_entries, save_provider_entries

    entries = load_provider_entries(account_of("erin"))
    entry = next(e for e in entries if e.get("protocol") == "chatgpt")
    entry["oauth"]["expires_at"] = int(time.time()) - 10
    entry["oauth"].pop("refresh_failed_at", None)
    save_provider_entries(account_of("erin"), entries)

    fresh = _fake_tokens(exp=int(time.time()) + 7200)
    calls = []

    def slow_refresh(form):
        calls.append(form)
        time.sleep(0.2)
        return fresh

    monkeypatch.setattr(co, "_token_request", slow_refresh)
    keys = []
    threads = [threading.Thread(target=lambda: keys.append(
        ai_runtime(account_of("erin"))["providers"][entry["id"]]["api_key"])) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert keys == [fresh["access_token"]] * 5
    saved = next(e for e in load_provider_entries(account_of("erin")) if e["id"] == entry["id"])
    assert saved["oauth"]["access_token"] == fresh["access_token"]


def test_sign_in_entry_cannot_become_a_key_entry(erin):
    entry = next(p for p in erin.get("/api/ai/settings").json()["providers"]
                 if p["protocol"] == "chatgpt")
    r = erin.put(f"/api/ai/providers/{entry['id']}", json={
        "protocol": "anthropic", "models": "", "base_url": ""})
    assert r.status_code == 400
    # Refused as a whole: the models of the entry are untouched.
    after = next(p for p in erin.get("/api/ai/settings").json()["providers"]
                 if p["id"] == entry["id"])
    assert after["protocol"] == "chatgpt" and after["models"] == entry["models"]


def test_provider_test_retries_a_backed_off_refresh(erin, monkeypatch):
    """The settings Test button clears the refresh backoff: a click after a
    failed refresh re-attempts the token refresh right away instead of probing
    with the stale token for up to five more minutes."""
    import gamma.routers.ai as ai_mod
    from gamma.ai_settings import load_provider_entries, save_provider_entries

    entries = load_provider_entries(account_of("erin"))
    entry = next(e for e in entries if e.get("protocol") == "chatgpt")
    entry["oauth"]["expires_at"] = int(time.time()) - 10
    entry["oauth"]["refresh_failed_at"] = int(time.time())  # inside the backoff window
    save_provider_entries(account_of("erin"), entries)

    fresh = _fake_tokens(exp=int(time.time()) + 7200)
    monkeypatch.setattr(co, "_token_request", lambda form: fresh)
    monkeypatch.setattr(ai_mod, "_call_ai", lambda *a, **k: "ok")
    r = erin.post(f"/api/ai/providers/{entry['id']}/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    saved = next(e for e in load_provider_entries(account_of("erin")) if e["id"] == entry["id"])
    assert saved["oauth"]["access_token"] == fresh["access_token"]
    assert "refresh_failed_at" not in saved["oauth"]


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return json.dumps(self._body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_chatgpt_provider_usage_reports_remaining_windows(erin, monkeypatch):
    from gamma.ai_settings import load_provider_entries

    entry = next(e for e in load_provider_entries(account_of("erin")) if e.get("protocol") == "chatgpt")
    # A crafted settings request must not redirect an OAuth bearer token.
    edited = erin.put(f"/api/ai/providers/{entry['id']}", json={
        "base_url": "https://attacker.example/codex",
        "api_key": "stolen-on-next-call",
    })
    assert edited.status_code == 200
    entry = next(e for e in load_provider_entries(account_of("erin")) if e.get("protocol") == "chatgpt")
    assert entry.get("base_url") != "https://attacker.example/codex"
    assert entry.get("api_key") != "stolen-on-next-call"
    seen = {}

    def fake_open(req, timeout=0):
        seen["url"] = req.full_url
        seen["account"] = req.get_header("Chatgpt-account-id")
        return _FakeResp({
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 23,
                    "limit_window_seconds": 18000,
                    "reset_at": 1_900_000_000,
                },
                "secondary_window": {
                    "used_percent": 61.5,
                    "limit_window_seconds": 604800,
                    "reset_at": 1_900_100_000,
                },
            },
            "credits": {"has_credits": False, "balance": "0"},
        })

    monkeypatch.setattr(ai_catalog, "urlopen", fake_open)
    r = erin.post(f"/api/ai/providers/{entry['id']}/usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True and body["plan_type"] == "plus"
    assert [(w["name"], w["remaining_percent"]) for w in body["windows"]] == [
        ("5-hour", 77.0), ("Weekly", 38.5),
    ]
    assert seen["url"] == "https://chatgpt.com/backend-api/wham/usage"
    assert seen["account"] == "acct-123"


def test_model_catalog_asks_chatgpt_backend_live(erin, monkeypatch):
    entry = next(p for p in erin.get("/api/ai/settings").json()["providers"]
                 if p["protocol"] == "chatgpt")
    seen = {}

    monkeypatch.setattr(chatgpt_proto, "codex_client_version", lambda: "9.9.9")

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _FakeResp({"models": [
            {"slug": "gpt-6-codex", "visibility": "list"},
            {"slug": "gpt-5.1-codex", "visibility": "list"},
            {"slug": "gpt-6-codex", "visibility": "list"},       # dupe collapses
            {"slug": "gpt-5-codex-mini", "visibility": "hide"},  # usable, offered last
            {"slug": "gpt-4-gone", "visibility": "none"},        # dropped
        ]})

    monkeypatch.setattr(ai_catalog, "urlopen", fake_urlopen)
    r = erin.post("/api/ai/model-catalog", json={"provider_id": entry["id"]})
    assert r.status_code == 200
    assert r.json()["models"] == ["gpt-6-codex", "gpt-5.1-codex", "gpt-5-codex-mini"]
    assert "chatgpt.com/backend-api/codex/models" in seen["url"]
    assert "client_version=9.9.9" in seen["url"]
    assert seen["auth"].startswith("Bearer ")

    # No provider_id (pre-connect form): any connected chatgpt entry serves
    r = erin.post("/api/ai/model-catalog", json={"protocol": "chatgpt"})
    assert "gpt-6-codex" in r.json()["models"]


def test_codex_client_version_is_looked_up_and_cached(monkeypatch):
    monkeypatch.setattr(chatgpt_proto, "_codex_version", {"value": "", "until": 0.0})
    calls = []

    def npm(req, timeout=0):
        calls.append(req.full_url)
        return _FakeResp({"name": "@openai/codex", "version": "1.2.3"})

    monkeypatch.setattr(chatgpt_proto, "urlopen", npm)
    assert chatgpt_proto.codex_client_version() == "1.2.3"
    assert chatgpt_proto.codex_client_version() == "1.2.3"
    assert calls == [chatgpt_proto.CODEX_VERSION_URL]  # second call served from cache

    # Expired + npm down: keep the last good version, and don't retry at once.
    def down(req, timeout=0):
        calls.append(req.full_url)
        raise OSError("offline")

    chatgpt_proto._codex_version["until"] = 0.0
    monkeypatch.setattr(chatgpt_proto, "urlopen", down)
    assert chatgpt_proto.codex_client_version() == "1.2.3"
    assert chatgpt_proto.codex_client_version() == "1.2.3"
    assert len(calls) == 2

    # Never looked up successfully: the floor.
    monkeypatch.setattr(chatgpt_proto, "_codex_version", {"value": "", "until": 0.0})
    assert chatgpt_proto.codex_client_version() == chatgpt_proto.CODEX_VERSION_FLOOR


def test_model_catalog_errors_when_listing_fails(erin, monkeypatch):
    def boom(req, timeout=0):
        raise OSError("no route to host")

    # No hardcoded model list to fall back on — the picker shows the error.
    monkeypatch.setattr(chatgpt_proto, "codex_client_version", lambda: "9.9.9")
    monkeypatch.setattr(ai_catalog, "urlopen", boom)
    r = erin.post("/api/ai/model-catalog", json={"protocol": "chatgpt"})
    assert r.status_code == 502
    assert "no route to host" in r.json()["detail"]

    # API protocols need a key (typed or stored) before asking /v1/models
    r = erin.post("/api/ai/model-catalog", json={"protocol": "openai"})
    assert r.status_code == 400
    assert "key" in r.json()["detail"]


def test_api_model_catalog_uses_one_short_attempt(erin, monkeypatch):
    calls = []

    def timed_out(req, timeout=0):
        calls.append((req, timeout))
        raise TimeoutError("timed out")

    monkeypatch.setattr(ai_catalog, "urlopen", timed_out)
    r = erin.post("/api/ai/model-catalog", json={
        "protocol": "openai",
        "api_key": "sk-test",
    })

    assert r.status_code == 400
    assert "timed out" in r.json()["detail"]
    assert len(calls) == 1
    assert calls[0][1] == 5
    assert calls[0][0].get_header("User-agent") == "Gamma/model-catalog"


def test_api_model_catalog_does_not_retry_auth_error(erin, monkeypatch):
    calls = 0

    def unauthorized(req, timeout=0):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":{"message":"bad key"}}'),
        )

    monkeypatch.setattr(ai_catalog, "urlopen", unauthorized)
    r = erin.post("/api/ai/model-catalog", json={
        "protocol": "openai",
        "api_key": "sk-bad",
    })

    assert r.status_code == 400
    assert "bad key" in r.json()["detail"]
    assert calls == 1


def test_catalog_uses_edited_endpoint_and_protocol(erin, monkeypatch):
    import gamma.routers.ai as ai_mod

    monkeypatch.setattr(ai_mod, "load_provider_entries", lambda user: [{
        "id": "saved", "protocol": "openai", "api_key": "stored-key",
        "base_url": "https://old.example",
    }])
    calls = []
    monkeypatch.setattr(ai_catalog, "fetch_json", lambda req: calls.append(req) or {"data": []})
    for fields in ({}, {"base_url": ""}, {"protocol": "anthropic", "base_url": "https://new.example"}):
        response = erin.post("/api/ai/model-catalog", json={"provider_id": "saved", **fields})
        assert response.status_code == 200
    assert calls[0].full_url == "https://old.example/v1/models"
    assert calls[1].full_url == "https://api.openai.com/v1/models"
    assert calls[2].full_url == "https://new.example/v1/models?limit=100"
    assert calls[2].get_header("X-api-key") == "stored-key"


# --- Wire protocol ------------------------------------------------------------

def test_chatgpt_request_shape_with_pdf_and_image():
    conf = {"api_key": "tok", "account_id": "acct-123", "base_url": "https://chatgpt.com/backend-api/codex"}
    msgs = [{"role": "assistant", "content": "earlier answer"}, {"role": "user", "content": "what does fig 2 show?"}]
    req = _chatgpt_request(conf, msgs, "be brief", "gpt-5.1",
                           pdf_b64s=["UERG"], images=[("image/png", "SU1H")])
    body = json.loads(req.data)
    assert req.full_url.endswith("/responses")
    assert body["stream"] is True and body["store"] is False
    assert body["model"] == "gpt-5.1" and body["instructions"] == "be brief"
    assert body["input"][0]["content"][0]["type"] == "output_text"
    last = body["input"][-1]["content"]
    assert [p["type"] for p in last] == ["input_file", "input_image", "input_text"]
    assert last[0]["file_data"].startswith("data:application/pdf;base64,")
    assert req.get_header("Chatgpt-account-id") == "acct-123"
    assert req.get_header("Authorization") == "Bearer tok"


def test_chatgpt_sse_deltas_join_and_fail():
    ok = [
        b'data: {"type":"response.output_text.delta","delta":"Hel"}\n',
        b'data: {"type":"response.output_text.delta","delta":"lo"}\n',
        b'data: {"type":"response.completed","response":{"status":"completed"}}\n',
    ]
    assert "".join(_sse_deltas(ok, "chatgpt")) == "Hello"

    failed = [b'data: {"type":"response.failed","response":{"error":{"message":"quota hit"}}}\n']
    with pytest.raises(RuntimeError, match="quota hit"):
        list(_sse_deltas(failed, "chatgpt"))

    empty = [b'data: {"type":"response.completed","response":{"status":"completed"}}\n']
    with pytest.raises(RuntimeError, match="empty response"):
        list(_sse_deltas(empty, "chatgpt"))


def test_model_facts_come_from_the_listing_then_models_dev(erin, monkeypatch):
    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "until": 0.0})
    monkeypatch.setattr(chatgpt_proto, "codex_client_version", lambda: "9.9.9")
    entry = next(p for p in erin.get("/api/ai/settings").json()["providers"]
                 if p["protocol"] == "chatgpt")
    model = next(m for m in erin.get("/api/ai/models").json()["models"] if m["provider"] == entry["id"])
    calls = []
    listing = {"models": [{"slug": model["model"], "context_window": 272_000,
                           "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"},
                                                          {"effort": "high"}, {"effort": "xhigh"}],
                           "service_tiers": [{"id": "priority", "name": "Fast"}]}]}
    catalog = {
        "openai": {"models": {model["model"]: {
            "id": model["model"], "limit": {"context": 400_000}, "reasoning": True,
            "reasoning_options": [{"type": "effort", "values": ["none", "low", "medium", "high"]}]}}},
        "gateway": {"models": {f"openai/{model['model']}": {
            "limit": {"context": 128_000}, "reasoning": True,
            "reasoning_options": [{"type": "budget_tokens", "min": 1024}]}}},
    }

    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        return _FakeResp(catalog if "models.dev" in req.full_url else listing)

    monkeypatch.setattr(ai_catalog, "urlopen", fake_urlopen)
    ask = lambda: erin.get("/api/ai/model-info", params={"model": model["id"]}).json()

    # The provider's own listing says it; asked once, then cached.
    assert ask() == {"model": model["model"], "context_window": 272_000, "source": "provider",
                     "efforts": ["low", "medium", "high", "xhigh"], "efforts_source": "provider",
                     "speeds": ["fast"], "speeds_source": "provider"}
    assert ask()["context_window"] == 272_000
    assert len(calls) == 1 and "/models?client_version=9.9.9" in calls[0]

    # A listing without sizes or levels: models.dev, the vendor behind the
    # protocol winning over a gateway that only takes a token budget.
    listing = {"models": [{"slug": model["model"]}]}
    ai_catalog._listings.clear()
    assert ask() == {"model": model["model"], "context_window": 400_000, "source": "models.dev",
                     "efforts": ["none", "low", "medium", "high"], "efforts_source": "models.dev",
                     "speeds": ["flex", "fast"], "speeds_source": "protocol"}

    # Nobody knows it: null, never a guess.
    catalog = {"openai": {"models": {}}}
    ai_catalog._listings.clear()
    ai_catalog._models_dev.update(index=None, until=0.0)
    assert ask() == {"model": model["model"], "context_window": None, "source": "",
                     "efforts": None, "efforts_source": "",
                     "speeds": ["flex", "fast"], "speeds_source": "protocol"}


def test_context_window_lookups_keep_the_last_good_answer(monkeypatch):
    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "until": 0.0})
    conf = {"protocol": "openai", "api_key": "k", "base_url": "https://api.deepseek.com", "name": "DeepSeek"}
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp(
        {"data": [{"id": "llama", "max_model_len": 32_768}, {"id": "bare"}]} if "/v1/models" in req.full_url else {
            "deepseek": {"models": {"deepseek-chat": {"limit": {"context": 131_072}}}},
            "a": {"models": {"deepseek-chat": {"limit": {"context": 64_000}}}},
            "b": {"models": {"deepseek-chat": {"limit": {"context": 64_000}}}},
        }))
    assert ai_catalog._listed_windows("p", conf) == {"llama": 32_768}
    # The provider named in the entry's host wins over the majority...
    assert ai_catalog._catalog_window("deepseek-chat", conf) == 131_072
    # ...and without one, the value most providers agree on.
    assert ai_catalog._catalog_window("deepseek-chat", {**conf, "base_url": "https://example.org"}) == 64_000

    # Expired + offline: the last good answers stay, retried only later.
    def down(req, timeout=0):
        raise OSError("offline")

    monkeypatch.setattr(ai_catalog, "urlopen", down)
    for cached in ai_catalog._listings.values():
        cached["until"] = 0.0
    ai_catalog._models_dev["until"] = 0.0
    assert ai_catalog._listed_windows("p", conf) == {"llama": 32_768}
    assert ai_catalog._catalog_window("deepseek-chat", conf) == 131_072


def test_reasoning_efforts_from_an_anthropic_listing_then_models_dev(monkeypatch):
    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "until": 0.0})
    conf = {"protocol": "anthropic", "api_key": "k", "base_url": "https://api.anthropic.com", "name": "Anthropic"}

    def effort(*on):
        return {"supported": bool(on), **{level: {"supported": level in on}
                                          for level in ("low", "medium", "high", "xhigh", "max")}}

    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp(
        {"data": [{"id": "claude-a", "capabilities": {"effort": effort("low", "medium", "high", "max")}},
                  {"id": "claude-b", "capabilities": {"effort": effort()}},
                  {"id": "claude-c"}]} if "/v1/models" in req.full_url else {
            "x": {"models": {
                "claude-c": {"reasoning": True, "reasoning_options": [{"type": "effort", "values": ["low", "high"]}]},
                "budget-only": {"reasoning": True, "reasoning_options": [{"type": "budget_tokens", "min": 1024}]},
                "no-reasoning": {"reasoning": False},
                "older-entry": {"reasoning": True, "limit": {"context": 8000}}}},
        }))
    # The listing's capabilities say it, level by level; a model it lists
    # without them is looked up in models.dev.
    assert ai_catalog.reasoning_efforts("p", conf, "claude-a") == (["low", "medium", "high", "max"], "provider")
    assert ai_catalog.reasoning_efforts("p", conf, "claude-b") == ([], "provider")
    assert ai_catalog.reasoning_efforts("p", conf, "claude-c") == (["low", "high"], "models.dev")
    # No effort control at all is [], an entry that doesn't say is unknown.
    assert ai_catalog.reasoning_efforts("p", conf, "budget-only") == ([], "models.dev")
    assert ai_catalog.reasoning_efforts("p", conf, "no-reasoning") == ([], "models.dev")
    assert ai_catalog.reasoning_efforts("p", conf, "older-entry") == (None, "")
    assert ai_catalog.context_window("p", conf, "older-entry") == (8000, "models.dev")


def test_speed_tiers_from_the_listing_then_the_wire(monkeypatch):
    monkeypatch.setattr(ai_catalog, "_listings", {})
    monkeypatch.setattr(ai_catalog, "_models_dev", {"index": None, "until": 0.0})
    monkeypatch.setattr(chatgpt_proto, "codex_client_version", lambda: "9.9.9")
    codex = {"protocol": "chatgpt", "api_key": "k", "account_id": "a",
             "base_url": "https://chatgpt.com/backend-api/codex", "name": "ChatGPT"}
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp(
        {"models": [{"slug": "fast-and-flex",
                     "service_tiers": [{"id": "flex", "name": "Flex"}, {"id": "priority", "name": "Fast"}]},
                    {"slug": "standard-only", "service_tiers": []},
                    {"slug": "older-row"}]}))
    # The backend's listing names each model's tiers, by its own ids.
    assert ai_catalog.speed_tiers("p", codex, "fast-and-flex") == (["flex", "fast"], "provider")
    # A model it lists with none has no speed control at all.
    assert ai_catalog.speed_tiers("p", codex, "standard-only") == ([], "provider")
    # A row that doesn't say falls back to what the wire itself can ask for.
    assert ai_catalog.speed_tiers("p", codex, "older-row") == (["flex", "fast"], "protocol")

    # Anthropic's listing carries no speed facts, so fast mode is the wire's
    # answer for every model its own endpoint serves...
    anthropic = {"protocol": "anthropic", "api_key": "k",
                 "base_url": "https://api.anthropic.com", "name": "Anthropic"}
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _FakeResp({"data": [{"id": "claude-a"}]}))
    assert ai_catalog.speed_tiers("p", anthropic, "claude-a") == (["fast"], "protocol")
    # ...and none for a service that merely speaks its API elsewhere.
    kimi = {**anthropic, "base_url": "https://api.moonshot.ai/anthropic", "name": "Kimi"}
    assert ai_catalog.speed_tiers("p", kimi, "kimi-k2") == ([], "")


# --- The sign-in finishing without a paste ------------------------------------

def _device_api(monkeypatch, *, pending_polls=1):
    """OpenAI's device code endpoints, faked: the code counts as entered
    after ``pending_polls`` polls that answer 403."""
    calls = {"usercode": 0, "token": 0}

    def fake(path, body):
        calls[path] += 1
        if path == "usercode":
            assert body == {"client_id": co.CLIENT_ID}
            return {"device_auth_id": "dev-1", "usercode": "ABCD-1234", "interval": "0"}
        assert body == {"device_auth_id": "dev-1", "user_code": "ABCD-1234"}
        if calls["token"] <= pending_polls:
            raise urllib.error.HTTPError("u", 403, "pending", {}, io.BytesIO(b""))
        return {"authorization_code": "dev-code", "code_challenge": "c", "code_verifier": "dev-verifier"}

    monkeypatch.setattr(co, "_device_request", fake)
    return calls


def _due(state):
    """Skip the rest of OpenAI's poll interval."""
    co._PENDING[state]["device"]["next_poll"] = 0


def test_device_code_answer_is_read_like_codex_does(monkeypatch):
    _device_api(monkeypatch)
    # `usercode` is an alias, the interval a string; 0 means the default 5 s.
    assert co.request_device_code() == {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": 5}


def test_plain_start_asks_openai_nothing(erin, monkeypatch):
    monkeypatch.setattr(co, "_device_request", lambda *a: pytest.fail("no device code was asked for"))
    body = erin.post("/api/ai/oauth/chatgpt/start").json()
    assert body["local"] is False and body["device"] is None


def test_device_code_sign_in_connects_without_a_paste(erin, monkeypatch):
    calls = _device_api(monkeypatch)
    exchanged = []
    monkeypatch.setattr(co, "_token_request", lambda form: exchanged.append(form) or _fake_tokens(email="dev@example.com"))
    start = erin.post("/api/ai/oauth/chatgpt/start", json={"device": True, "local": True}).json()
    # The test client is no loopback browser: nothing listens, a code is offered.
    assert start["local"] is False
    assert start["device"] == {"user_code": "ABCD-1234", "verification_url": "https://auth.openai.com/codex/device"}
    state = start["state"]

    def status():
        return erin.post("/api/ai/oauth/chatgpt/status", json={"state": state}).json()

    assert status() == {"ready": False, "error": ""} and calls["token"] == 0  # the interval isn't up
    _due(state)
    assert status() == {"ready": False, "error": ""} and calls["token"] == 1  # asked: not entered yet
    r = erin.post("/api/ai/oauth/chatgpt/complete", json={"state": state})
    assert r.status_code == 400 and "not signed in yet" in r.json()["detail"]
    _due(state)
    assert status() == {"ready": True, "error": ""}
    assert exchanged[-1]["redirect_uri"] == co.DEVICE_REDIRECT_URI
    assert exchanged[-1]["code_verifier"] == "dev-verifier"  # the device flow's own
    # The early complete left the sign-in waiting; this one redeems it.
    r = erin.post("/api/ai/oauth/chatgpt/complete", json={"state": state, "models": "gpt-x"})
    assert r.status_code == 200, r.text
    entry = next(p for p in r.json()["providers"] if p.get("account") == "dev@example.com")
    assert entry["oauth_connected"] is True and entry["models"] == "gpt-x"
    assert erin.delete(f"/api/ai/providers/{entry['id']}").status_code == 200
    assert status()["error"]  # redeemed: gone


def test_a_failed_device_code_says_so(monkeypatch):
    def refused(path, body):
        if path == "usercode":
            return {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": 5}
        raise urllib.error.HTTPError("u", 410, "Gone", {}, io.BytesIO(b""))

    monkeypatch.setattr(co, "_device_request", refused)
    state = co.begin("someone", device=True)["state"]
    _due(state)
    result = co.status("someone", state)
    assert result["ready"] is False and "device code sign-in failed" in result["error"]
    # The pasted address still works.
    monkeypatch.setattr(co, "_token_request", lambda form: _fake_tokens())
    assert co.redeem("someone", state, f"http://localhost:1455/auth/callback?code=x&state={state}")["account_id"]


def test_a_wrong_paste_keeps_the_sign_in_waiting(monkeypatch):
    monkeypatch.setattr(co, "_token_request", lambda form: _fake_tokens())
    state = co.begin("someone")["state"]
    with pytest.raises(ValueError, match="state mismatch"):
        co.redeem("someone", state, "http://localhost:1455/auth/callback?code=x&state=an-older-one")
    with pytest.raises(ValueError, match="expired"):  # not theirs
        co.redeem("someone-else", state, f"http://localhost:1455/auth/callback?code=x&state={state}")
    assert co.status("someone-else", state)["error"]
    assert co.redeem("someone", state, f"http://localhost:1455/auth/callback?code=x&state={state}")["account_id"]
    with pytest.raises(ValueError, match="expired"):  # one-shot
        co.redeem("someone", state, "x")


def test_the_redirect_is_caught_on_this_machine(monkeypatch):
    monkeypatch.setattr(co, "CALLBACK_PORT", 0)  # any free port, never the real 1455
    monkeypatch.setattr(co, "_device_request", lambda *a: pytest.fail("the redirect needs no device code"))
    monkeypatch.setattr(co, "_token_request", lambda form: _fake_tokens(email="local@example.com"))
    started = co.begin("someone", local=True, device=True)
    assert started["local"] is True and started["device"] is None
    state = started["state"]
    base = f"http://127.0.0.1:{co._server.server_address[1]}/auth/callback"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    assert co.status("someone", state) == {"ready": False, "error": ""}
    with pytest.raises(urllib.error.HTTPError) as unknown:
        direct.open(f"{base}?code=x&state=not-ours", timeout=5)
    assert unknown.value.code == 400
    with direct.open(f"{base}?code=abc&state={state}", timeout=5) as page:
        assert b"Signed in to ChatGPT" in page.read()
    assert co.status("someone", state) == {"ready": True, "error": ""}
    assert co.redeem("someone", state)["email"] == "local@example.com"
    # Nothing left to wait for: the port is let go.
    deadline = time.time() + 5
    while co._server is not None and time.time() < deadline:
        time.sleep(0.05)
    assert co._server is None
