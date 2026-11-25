"""The Gemini sign-in (docs/dev/ai.md "The Gemini sign-in"): Gemini CLI's
Google OAuth flow (gamma/gemini_oauth.py) and the Code Assist wire
(gamma/ai_protocols/gemini.py). Google is faked throughout: its OAuth
endpoints by tests/gdrive_fake.py's FakeGoogle (the token exchange with
PKCE and the client's secret, the refresh, a revoked grant), its Code Assist
backend by FakeCodeAssist below, and the chat and quota calls by fakes of
urlopen. Nothing reaches the network."""

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

import cloud_fake
import gamma.gemini_oauth as go
from ai_fixtures import FakeResp
from conftest import account_of, login, make_user
from gamma import ai_catalog, ai_settings, cloud_oauth, config
from gamma.ai_protocols import WIRES
from gamma.ai_settings import ai_runtime, load_provider_entries
from gamma.chatgpt_oauth import RefreshUnavailable
from gdrive_fake import DESKTOP_ID, DESKTOP_SECRET, FakeGoogle

WIRE = WIRES["gemini-signin"]
BASE = config.AI_BASE_URLS["gemini-signin"]
PROJECT = "witty-lark-123"


class GeminiGoogle(FakeGoogle):
    """FakeGoogle answering the userinfo call the sign-in makes for its e-mail."""

    def _api(self, method, path, query, headers, body):
        if path == "/oauth2/v2/userinfo":
            return self._json(200, {"email": self.email})
        return super()._api(method, path, query, headers, body)


class FakeCodeAssist:
    """cloudcode-pa.googleapis.com's setup calls: loadCodeAssist answers
    ``loaded`` (or ``loaded(body)``: what it says depends on the project the
    request names); onboardUser starts an operation that is done the second
    time it is asked about, on the project the request named, else Google's
    own (PROJECT). Every call needs an access token FakeGoogle issued."""

    def __init__(self, google, loaded):
        self.base, self.google, self.loaded = BASE, google, loaded
        self.sleeps, self.calls, self.asked = [], [], 0

    def handle(self, method, url, headers, body=None):
        line = f"{method} {url[len(BASE):]}"
        sent = json.loads(body) if body else None
        self.calls.append((line, sent))
        token = {k.lower(): v for k, v in headers.items()}.get("authorization", "")[7:]
        if token not in self.google.access:
            return cloud_fake.Reply(401, {}, json.dumps({"error": {"message": "Request had invalid credentials."}}).encode())
        if line == "POST :loadCodeAssist":
            return self._json(self.loaded(sent) if callable(self.loaded) else self.loaded)
        if line == "POST :onboardUser":
            self.onboarded = sent.get("cloudaicompanionProject") or PROJECT
            return self._json({"name": "operations/onboard-1", "done": False})
        if line == "GET /operations/onboard-1":
            self.asked += 1
            return self._json({"name": "operations/onboard-1", "done": True, "response": {
                "cloudaicompanionProject": {"id": self.onboarded, "name": "Gemini"}}} if self.asked > 1 else
                {"name": "operations/onboard-1", "done": False})
        return cloud_fake.Reply(404, {}, b"{}")

    @staticmethod
    def _json(data):
        return cloud_fake.Reply(200, {"content-type": "application/json"}, json.dumps(data).encode())


FREE_TIER = {"allowedTiers": [{"id": "free-tier", "name": "Gemini Code Assist for individuals", "isDefault": True}]}
SIGNED_UP = {"currentTier": {"id": "standard-tier", "name": "Standard"}, "paidTier": {"id": "g1-pro-tier", "name": "Google AI Pro"},
             "cloudaicompanionProject": PROJECT}
# Google's answer to an individual account since June 2026.
RETIRED = {"ineligibleTiers": [{"reasonCode": "INELIGIBLE_ACCOUNT", "tierId": "free-tier", "reasonMessage": (
    "This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, "
    "please migrate to the Antigravity suite of products: https://antigravity.google")}]}
ORG_PROJECT = "acme-gemini-01"


def org_license(sent):
    """An organization's Standard license: refused as an individual without
    its project, a tier on the user's own project to set up with it."""
    if sent.get("cloudaicompanionProject") != ORG_PROJECT:
        return RETIRED
    return {"allowedTiers": [{"id": "standard-tier", "name": "Gemini Code Assist Standard", "isDefault": True,
                              "userDefinedCloudaicompanionProject": True}]}


class _Json:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


BUCKETS = {"buckets": [
    {"modelId": "gemini-2.5-pro", "tokenType": "REQUESTS", "remainingFraction": 0.75, "resetTime": "2026-11-24T08:00:00Z"},
    {"modelId": "gemini-2.5-flash", "tokenType": "REQUESTS", "remainingFraction": 1, "resetTime": "2026-11-24T08:00:00Z"},
    {"modelId": "gemini-3-pro-preview", "tokenType": "REQUESTS", "remainingFraction": 0.1},
]}


@pytest.fixture
def google(monkeypatch):
    """Google faked with Gemini CLI's client standing for the fake's desktop
    one, every scope the sign-in asks for granted, and onboarding not waited for."""
    fake = GeminiGoogle(email="ada@gmail.com").install(monkeypatch)
    fake.granted = " ".join(go.GOOGLE.scopes)
    monkeypatch.setattr(go, "CLIENT", cloud_oauth.OAuthClient(DESKTOP_ID, DESKTOP_SECRET))
    monkeypatch.setattr(go, "ONBOARD_POLL_S", 0)
    return fake


def _code_assist(monkeypatch, google, loaded):
    return cloud_fake.install(monkeypatch, FakeCodeAssist(google, loaded))


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _pasted_code(google, auth_url):
    """What Google's code page shows after the person allows: a code bound
    to the start's PKCE challenge and its redirect."""
    q = _query(auth_url)
    google.codes["4/0Afake-code"] = {"client_id": DESKTOP_ID, "redirect_uri": q["redirect_uri"],
                                     "challenge": q["code_challenge"], "used": False}
    return "4/0Afake-code"


@pytest.fixture(scope="module")
def ada(client):
    make_user("gemini_ada", "pw")
    return login("gemini_ada", "pw")


# --- the sign-in ---------------------------------------------------------------

def test_the_sign_in_page_is_google_s_for_gemini_cli_s_client():
    started = go.begin("someone")
    q = _query(started["auth_url"])
    assert started["auth_url"].startswith(f"{config.google_accounts_base()}/o/oauth2/v2/auth?")
    assert q["client_id"] == "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    # From another machine Google ends on its own page, which shows the code.
    assert q["redirect_uri"] == "https://codeassist.google.com/authcode"
    assert "https://www.googleapis.com/auth/cloud-platform" in q["scope"].split()
    assert (q["access_type"], q["prompt"], q["code_challenge_method"]) == ("offline", "consent", "S256")
    assert q["state"] == started["state"] and started["local"] is False and started["device"] is None


def test_a_pasted_code_connects_and_sets_up_the_free_tier(ada, google, monkeypatch):
    assist = _code_assist(monkeypatch, google, FREE_TIER)
    seen = {}

    def quota(req, timeout=0):
        seen.update(url=req.full_url, body=json.loads(req.data), auth=req.get_header("Authorization"))
        return _Json(BUCKETS)
    monkeypatch.setattr(ai_catalog, "urlopen", quota)
    started = ada.post("/api/ai/oauth/gemini-signin/start", json={"local": False}).json()
    code = _pasted_code(google, started["auth_url"])
    r = ada.post("/api/ai/oauth/gemini-signin/complete", json={"state": started["state"], "callback": code})
    assert r.status_code == 200, r.text
    entry = next(p for p in r.json()["providers"] if p["protocol"] == "gemini-signin")
    assert (entry["name"], entry["account"], entry["oauth_connected"]) == ("Gemini", "ada@gmail.com", True)
    assert (entry["billing"], entry["service"], entry["quota"]) == ("subscription", "gemini-signin", True)
    # The account's first models, read off its quota buckets (no model name in the code).
    assert entry["models"] == "gemini-2.5-flash, gemini-2.5-pro"
    assert "access_token" not in json.dumps(r.json())
    # The free tier was set up once: an onboarding operation, polled until done.
    assert [line for line, _ in assist.calls] == [
        "POST :loadCodeAssist", "POST :onboardUser", "GET /operations/onboard-1", "GET /operations/onboard-1"]
    assert assist.calls[1][1]["tierId"] == "free-tier" and "cloudaicompanionProject" not in assist.calls[1][1]
    stored = next(e for e in load_provider_entries(account_of("gemini_ada")) if e["protocol"] == "gemini-signin")
    assert stored["oauth"]["account_id"] == PROJECT and stored["oauth"]["plan"] == "Gemini Code Assist for individuals"
    # The listing named the project, on the protocol's own endpoint.
    assert seen["url"] == f"{BASE}:retrieveUserQuota" and seen["body"] == {"project": PROJECT}
    assert seen["auth"] == f"Bearer {stored['oauth']['access_token']}"
    # The code is spent with the sign-in.
    again = ada.post("/api/ai/oauth/gemini-signin/complete", json={"state": started["state"], "callback": code})
    assert again.status_code == 400


def test_the_redirect_is_caught_on_this_machine(google, monkeypatch):
    _code_assist(monkeypatch, google, SIGNED_UP)
    started = go.begin("someone", local=True)
    assert started["local"] is True
    port = go._server.server_address[1]
    assert _query(started["auth_url"])["redirect_uri"] == f"http://127.0.0.1:{port}/oauth2callback"
    back = google.authorize(started["auth_url"])  # the person allows on Google's page
    assert go.status("someone", started["state"]) == {"ready": False, "error": ""}
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with direct.open(back, timeout=10) as page:
        assert "<title>Signed in with Google · Gamma</title>".encode() in page.read()
    assert go.status("someone", started["state"]) == {"ready": True, "error": ""}
    oauth = go.redeem("someone", started["state"])
    # An account already set up is read as it is: its project, its paid plan.
    assert (oauth["account_id"], oauth["plan"], oauth["email"]) == (PROJECT, "Google AI Pro", "ada@gmail.com")
    with pytest.raises(ValueError, match="expired"):  # one-shot
        go.redeem("someone", started["state"], "4/again")


@pytest.mark.parametrize("loaded, project, said", [
    ({"ineligibleTiers": [{"reasonCode": "VALIDATION_REQUIRED", "validationUrl": "https://accounts.google.com/verify"}]},
     "", ["verify this account first: open https://accounts.google.com/verify"]),
    # An individual account since June 2026: Google's words, then what is left.
    (RETIRED, "", ["can't use Gemini here: This client is no longer supported for Gemini Code Assist for individuals",
                   "Google retired this sign-in for individual accounts",
                   "With Gemini Code Assist Standard or Enterprise, enter your organization's Google Cloud project"]),
    ({"allowedTiers": [{"id": "standard-tier", "isDefault": True, "userDefinedCloudaicompanionProject": True}]},
     "", ["needs its Google Cloud project: enter the project's ID"]),
    (RETIRED, "acme-other", ["can't use Gemini here: This client is no longer supported"]),
    (FREE_TIER, "123456789012", ["'123456789012' is no Google Cloud project ID", "not its number"]),
])
def test_setup_says_why_an_account_cannot_use_gemini(google, monkeypatch, loaded, project, said):
    _code_assist(monkeypatch, google, loaded)
    started = go.begin("someone")
    code = _pasted_code(google, started["auth_url"])
    with pytest.raises(ValueError, match="Gemini setup failed") as failed:
        go.redeem("someone", started["state"], code, project=project)
    for words in said:
        assert words in str(failed.value)
    if project:
        assert "Google retired this sign-in" not in str(failed.value), "a project named: no talk of individual accounts"


def test_an_organization_s_license_connects_through_its_project_without_signing_in_again(ada, google, monkeypatch):
    """Refused as an individual, the sign-in waits with its tokens: the
    project typed then is tried on the next Connect, and the code Google
    gave (one-shot) is not traded again."""
    assist = _code_assist(monkeypatch, google, org_license)
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _Json(BUCKETS))
    started = ada.post("/api/ai/oauth/gemini-signin/start").json()
    code = _pasted_code(google, started["auth_url"])
    refused = ada.post("/api/ai/oauth/gemini-signin/complete", json={"state": started["state"], "callback": code})
    assert refused.status_code == 400 and "Google retired this sign-in" in refused.json()["detail"]
    assert go.status(account_of("gemini_ada"), started["state"]) == {"ready": True, "error": ""}
    r = ada.post("/api/ai/oauth/gemini-signin/complete", json={
        "state": started["state"], "callback": code, "project": ORG_PROJECT, "name": "Acme Gemini"})
    assert r.status_code == 200, r.text
    assert sum(1 for call in google.calls if call == "POST /token") == 1, "the code traded once"
    # Set up as Gemini CLI does with GOOGLE_CLOUD_PROJECT: the project named
    # on loadCodeAssist and on the onboarding of a tier on the user's own project.
    load = [body for line, body in assist.calls if line == "POST :loadCodeAssist"][-1]
    onboard = next(body for line, body in assist.calls if line == "POST :onboardUser")
    assert load["cloudaicompanionProject"] == ORG_PROJECT and load["metadata"]["duetProject"] == ORG_PROJECT
    assert (onboard["tierId"], onboard["cloudaicompanionProject"]) == ("standard-tier", ORG_PROJECT)
    stored = next(e for e in load_provider_entries(account_of("gemini_ada")) if e.get("name") == "Acme Gemini")
    assert (stored["oauth"]["account_id"], stored["oauth"]["plan"]) == (ORG_PROJECT, "Gemini Code Assist Standard")


def test_a_license_already_set_up_runs_on_the_project_named(google, monkeypatch):
    """An organization's account Code Assist already knows: its current tier
    and no project of Google's, so the project the user named is the one."""
    _code_assist(monkeypatch, google, lambda sent: {"currentTier": {"id": "standard-tier", "name": "Standard"}}
                 if sent.get("cloudaicompanionProject") == ORG_PROJECT else RETIRED)
    started = go.begin("someone")
    oauth = go.redeem("someone", started["state"], _pasted_code(google, started["auth_url"]), project=ORG_PROJECT)
    assert (oauth["account_id"], oauth["plan"]) == (ORG_PROJECT, "Standard")


def test_a_refresh_renews_and_a_refused_grant_ends_the_sign_in(google, monkeypatch):
    _code_assist(monkeypatch, google, SIGNED_UP)
    started = go.begin("someone")
    oauth = go.redeem("someone", started["state"], _pasted_code(google, started["auth_url"]))
    renewed = go.refresh({**oauth, "expires_at": 0, "refresh_failed_at": 1, "refresh_unreachable": True})
    assert renewed["access_token"] != oauth["access_token"] and renewed["access_token"] in google.access
    assert renewed["refresh_token"] == oauth["refresh_token"]  # Google doesn't rotate them
    assert (renewed["account_id"], renewed["email"]) == (PROJECT, "ada@gmail.com")
    assert "refresh_failed_at" not in renewed and "refresh_unreachable" not in renewed
    assert not go.needs_refresh(renewed)
    google.fail_next("/token", 0)  # no answer: no verdict, the tokens are kept
    with pytest.raises(RefreshUnavailable):
        go.refresh(oauth)
    google.revoke_all()  # the person removed the app
    assert go.refresh(oauth) is None


# --- the wire ------------------------------------------------------------------

def test_the_request_wraps_gemini_s_contents_for_code_assist():
    conf = {"base_url": BASE, "api_key": "at-1", "account_id": PROJECT}
    messages = [
        {"role": "user", "content": "Summarize", "files": ["UERG"]},
        {"role": "assistant", "content": "Reading.", "tool_calls": [
            {"id": "c1", "name": "read_page", "arguments": {"page": 1}},
            {"id": "c2", "name": "search", "arguments": {"q": "x"}}],
         "reasoning": {"thought": "plan", "thought_signatures": {"c1": "sig-1"}}},
        {"role": "tool", "call_id": "c1", "content": "page one", "images": [("image/png", "iVBO")]},
        {"role": "tool", "call_id": "c2", "content": "hits"},
        {"role": "user", "content": "And the figure?"},
        # A turn kept without its signatures (an older conversation).
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c3", "name": "read_page", "arguments": {}}]},
        {"role": "tool", "call_id": "c3", "content": "ok"},
    ]
    tools = [{"name": "read_page", "description": "Read a page", "parameters": {"type": "object", "additionalProperties": False}},
             {"name": "web", "description": "", "parameters": {}, "hosted": {"googleSearch": {}}}]
    req = WIRE.request(conf, messages, "Be brief.", "gemini-2.5-pro", images=[("image/jpeg", "/9j/")],
                       effort="low", max_tokens=8192, tools=tools, cache_key="chat-1")
    assert req.full_url == f"{BASE}:streamGenerateContent?alt=sse"
    assert req.get_header("Authorization") == "Bearer at-1" and req.get_header("User-agent") == "Gamma"
    body = json.loads(req.data)
    assert (body["model"], body["project"]) == ("gemini-2.5-pro", PROJECT) and body["user_prompt_id"]
    inner = body["request"]
    assert inner["systemInstruction"] == {"role": "user", "parts": [{"text": "Be brief."}]}
    assert inner["session_id"] == "chat-1"
    # Thinking counts toward the cap: the reply's room on top of the budget.
    assert inner["generationConfig"] == {"maxOutputTokens": 8192 + 1024,
                                         "thinkingConfig": {"includeThoughts": True, "thinkingBudget": 1024}}
    # The provider's hosted tools aren't this wire's; the schema goes as JSON Schema.
    assert inner["tools"] == [{"functionDeclarations": [{"name": "read_page", "description": "Read a page",
                                                         "parametersJsonSchema": tools[0]["parameters"]}]}]
    roles = [c["role"] for c in inner["contents"]]
    assert roles == ["user", "model", "user", "model", "user"]
    first, model_turn, results, older, last = inner["contents"]
    assert first["parts"] == [{"inlineData": {"mimeType": "application/pdf", "data": "UERG"}}, {"text": "Summarize"}]
    assert model_turn["parts"] == [
        {"text": "Reading."},
        {"functionCall": {"name": "read_page", "args": {"page": 1}}, "thoughtSignature": "sig-1"},
        {"functionCall": {"name": "search", "args": {"q": "x"}}}]
    # Both results and the user's next message are one user turn; the request's
    # own picture goes on the last plain user message, before its text.
    assert results["parts"] == [
        {"functionResponse": {"name": "read_page", "response": {"output": "page one"}}},
        {"inlineData": {"mimeType": "image/png", "data": "iVBO"}},
        {"functionResponse": {"name": "search", "response": {"output": "hits"}}},
        {"inlineData": {"mimeType": "image/jpeg", "data": "/9j/"}}, {"text": "And the figure?"}]
    assert older["parts"] == [{"functionCall": {"name": "read_page", "args": {}},
                               "thoughtSignature": "skip_thought_signature_validator"}]
    assert last["parts"] == [{"functionResponse": {"name": "read_page", "response": {"output": "ok"}}}]
    no_effort = json.loads(WIRE.request(conf, messages[:1], "", "gemini-2.5-flash", max_tokens=60000).data)["request"]
    assert no_effort["generationConfig"] == {"maxOutputTokens": 65536, "thinkingConfig": {"includeThoughts": True}}
    assert "systemInstruction" not in no_effort and "tools" not in no_effort


class _Lines:
    def __init__(self, text):
        self.lines = [line.encode() + b"\n" for line in text.split("\n")]

    def __iter__(self):
        return iter(self.lines)


def test_the_stream_reads_text_thoughts_signed_calls_and_usage():
    def event(parts, finish=None, usage=None):
        candidate = {"content": {"role": "model", "parts": parts}, **({"finishReason": finish} if finish else {})}
        return {"response": {"candidates": [candidate], **({"usageMetadata": usage} if usage else {})}, "traceId": "t"}
    stream = FakeResp([
        event([{"text": "Weighing it", "thought": True}]),
        event([{"text": "The answer"}]),
        event([{"text": " is 4."}, {"functionCall": {"name": "calc", "args": {"x": 2}}, "thoughtSignature": "sig-a"},
               {"functionCall": {"name": "calc", "args": {"x": 3}}}], finish="STOP",
              usage={"promptTokenCount": 120, "cachedContentTokenCount": 100, "candidatesTokenCount": 9,
                     "thoughtsTokenCount": 30}),
    ])
    events = list(WIRE.events(stream))
    assert [e for e in events if e[0] == "text"] == [("text", "The answer"), ("text", " is 4.")]
    reasoning = next(data for kind, data in events if kind == "reasoning")
    calls = [data for kind, data in events if kind == "tool"]
    assert [kind for kind, _ in events].index("reasoning") < [kind for kind, _ in events].index("tool")
    assert [(c["name"], c["arguments"]) for c in calls] == [("calc", {"x": 2}), ("calc", {"x": 3})]
    assert reasoning == {"thought": "Weighing it", "thought_signatures": {calls[0]["id"]: "sig-a"}}
    assert ("usage", {"input": 120, "output": 39, "cache_read": 100, "cache_write": 0}) in events
    assert events[-1] == ("stop", "stop")
    # An event may span data: lines; the output cap reads as a cut-off reply.
    cut = _Lines('data: {"response": {"candidates": [{"content": {"parts":\n'
                 'data: [{"text": "Half"}]}, "finishReason": "MAX_TOKENS"}]}}\n\n')
    assert list(WIRE.events(cut)) == [("text", "Half"), ("stop", "max_tokens")]
    refused = FakeResp([{"response": {"promptFeedback": {"blockReason": "SAFETY"}}}])
    with pytest.raises(RuntimeError, match="SAFETY"):
        list(WIRE.events(refused))


def test_the_quota_says_what_is_left_of_each_model(ada, google, monkeypatch):
    _code_assist(monkeypatch, google, SIGNED_UP)
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _Json(BUCKETS))
    started = ada.post("/api/ai/oauth/gemini-signin/start").json()
    assert ada.post("/api/ai/oauth/gemini-signin/complete", json={
        "state": started["state"], "callback": _pasted_code(google, started["auth_url"]),
        "name": "Lab Gemini"}).status_code == 200
    entry = next(e for e in load_provider_entries(account_of("gemini_ada")) if e.get("name") == "Lab Gemini")
    usage = ada.post(f"/api/ai/providers/{entry['id']}/usage").json()
    assert usage["available"] is True
    assert [(w["name"], round(w["remaining_percent"])) for w in usage["windows"]] == [
        ("gemini-2.5-pro", 75), ("gemini-2.5-flash", 100), ("gemini-3-pro-preview", 10)]
    assert usage["windows"][0]["reset_at"] == 1_795_507_200
    # The runtime offers the entry's models through the Code Assist project.
    conf = ai_runtime(account_of("gemini_ada"))["providers"][entry["id"]]
    assert conf["account_id"] == PROJECT and conf["base_url"] == BASE


def test_only_a_sign_in_protocol_has_sign_in_routes(ada, guest, client):
    assert ada.post("/api/ai/oauth/openai/start").status_code == 404
    assert ada.post("/api/ai/oauth/nope/status", json={"state": "x"}).status_code == 404
    assert guest.post("/api/ai/oauth/gemini-signin/start").status_code == 403
    protocols = {p["id"]: p for p in ada.get("/api/ai/settings").json()["protocols"]}
    assert protocols["gemini-signin"]["auth"] == "oauth"
    assert "terms" in protocols["gemini-signin"]["note"] and protocols["chatgpt"]["note"] == ""
    # The form asks for the project where the sign-in takes one, and only there.
    assert protocols["gemini-signin"]["signin_fields"] == ["project"] and protocols["chatgpt"]["signin_fields"] == []


def test_an_admin_signs_in_a_shared_entry_the_same_way(client, google, monkeypatch):
    _code_assist(monkeypatch, google, SIGNED_UP)
    monkeypatch.setattr(ai_catalog, "urlopen", lambda req, timeout=0: _Json(BUCKETS))
    make_user("gemini_admin", "pw", is_admin=1)
    admin = login("gemini_admin", "pw")
    started = admin.post("/api/admin/ai-providers/gemini-signin/start").json()
    # The account flow's state doesn't redeem on the shared one, nor back.
    assert admin.post("/api/ai/oauth/gemini-signin/complete", json={
        "state": started["state"], "callback": "4/x"}).status_code == 400
    try:
        r = admin.post("/api/admin/ai-providers/gemini-signin/complete", json={
            "state": started["state"], "callback": _pasted_code(google, started["auth_url"])})
        assert r.status_code == 200, r.text
        shared = next(p for p in r.json()["providers"] if p["protocol"] == "gemini-signin")
        assert shared["id"].startswith("server:") and shared["models"] == "gemini-2.5-flash, gemini-2.5-pro"
    finally:
        # The run shares one users.db, and a shared entry is offered to every account.
        ai_settings.save_server_ai({"providers": []})
