"""General web search for the agent (gamma/search_services.py): the stored
settings and their masked view, which engine a chat gets, the Brave,
SearXNG and AI-connection engines, the search_web tool, and the settings
routes. Every engine call is faked; nothing reaches the network."""

import io
import json

import pytest

from gamma import ai_client, search_services
from gamma.ai_tools import MAX_WEB_SEARCHES, run_agent_tool

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)

ANTHROPIC = {"providers": {"p1": {"protocol": "anthropic", "base_url": "https://api.anthropic.com",
                                  "api_key": "k", "name": "Anthropic"}}}
GATEWAY = {"providers": {"p1": {"protocol": "openai", "base_url": "https://gateway.example.org",
                                "api_key": "k", "name": "Gateway"}}}
ENTRY = {"id": "p1:m", "provider": "p1", "model": "m"}


class Reply(io.BytesIO):
    """A JSON body the fake urlopen answers with (a context manager)."""

    def __init__(self, payload):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())


@pytest.fixture
def account(org):
    c, ids = org
    for service in search_services.SERVICES:
        c.delete(f"/api/ai/search-services/{service}")
    c.put("/api/ai/search-services/engine", json={"engine": "auto"})
    return c, ids["user"]


# ------------------------------------------------------------- settings

def test_settings_are_masked_and_secrets_kept(account):
    c, user = account
    view = c.get("/api/ai/search-services").json()
    assert view["engine"] == "auto" and view["can_edit"]
    assert [s["id"] for s in view["services"]] == ["brave", "searxng", "openalex"]
    assert not any(s["configured"] for s in view["services"])

    view = c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}}).json()
    brave = next(s for s in view["services"] if s["id"] == "brave")
    assert brave["configured"] and brave["fields"] == {"api_key": "…3456"}
    assert "BSA-secret-123456" not in json.dumps(view)
    # An empty secret keeps the stored one.
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": ""}})
    assert search_services.load(user)["brave"]["api_key"] == "BSA-secret-123456"

    assert c.put("/api/ai/search-services/searxng", json={"fields": {"url": "ftp://x"}}).status_code == 400
    c.put("/api/ai/search-services/searxng", json={"fields": {"url": "https://search.example.org/"}})
    assert search_services.load(user)["searxng"]["url"] == "https://search.example.org"
    assert c.put("/api/ai/search-services/engine", json={"engine": "bing"}).status_code == 400
    assert c.put("/api/ai/search-services/engine", json={"engine": "off"}).json()["engine"] == "off"
    assert c.delete("/api/ai/search-services/nope").status_code == 404
    # The generic prefs endpoints never serve the stored keys.
    assert c.get("/api/prefs/search-services").status_code == 400
    assert c.put("/api/prefs/search-services", json={"value": {}}).status_code == 400


def test_which_engine_a_chat_gets(account, monkeypatch):
    c, user = account
    monkeypatch.setattr(search_services, "SERVER_SEARXNG_URL", "")
    assert search_services.web_engine(user, GATEWAY, ENTRY) == ""  # a gateway hosts no search
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "ai"
    assert search_services.web_engine(user) == ""  # no connection (a context export)
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "brave"  # a set-up service first
    # A named engine is used only when it is ready — never a silent switch.
    c.put("/api/ai/search-services/engine", json={"engine": "searxng"})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == ""
    monkeypatch.setattr(search_services, "SERVER_SEARXNG_URL", "http://searxng:8080")
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "searxng"  # the admin's instance
    c.put("/api/ai/search-services/engine", json={"engine": "ai"})
    assert search_services.web_engine(user, GATEWAY, ENTRY) == ""
    c.put("/api/ai/search-services/engine", json={"engine": "off"})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == ""


# ------------------------------------------------------------- engines

def test_brave_sends_the_key_in_a_header_and_cleans_results(account, monkeypatch):
    c, user = account
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}})
    seen = []

    def fake_urlopen(req, timeout=20):
        seen.append(req)
        return Reply({"web": {"results": [
            {"title": "Vuletić <strong>lab</strong> publications", "url": "https://lab.example.edu/pubs",
             "description": "All papers &amp; talks"},
            {"title": "dup", "url": "https://lab.example.edu/pubs/"},
            {"title": "javascript", "url": "javascript:alert(1)"},
            {"title": "creds", "url": "https://user:pw@example.org/x"}]}})

    monkeypatch.setattr(search_services, "urlopen", fake_urlopen)
    results = search_services.search("brave", user, "raman sideband cooling", 5)
    assert results == [{"title": "Vuletić lab publications", "url": "https://lab.example.edu/pubs",
                        "snippet": "All papers & talks"}]
    req = seen[0]
    assert req.get_header("X-subscription-token") == "BSA-secret-123456"
    assert "BSA-secret" not in req.full_url and "q=raman+sideband+cooling" in req.full_url


def test_searxng_guards_an_accounts_url_but_not_the_admins(account, monkeypatch):
    c, user = account
    opened = []
    monkeypatch.setattr(search_services, "guarded_urlopen",
                        lambda req, timeout=20: opened.append(("guarded", req.full_url)) or Reply({"results": []}))
    monkeypatch.setattr(search_services, "urlopen",
                        lambda req, timeout=20: opened.append(("plain", req.full_url)) or Reply(b"<html>"))
    monkeypatch.setattr(search_services, "SERVER_SEARXNG_URL", "http://searxng:8080/search")
    with pytest.raises(search_services.SearchError, match="enable the json format"):
        search_services.search("searxng", user, "q", 3)
    c.put("/api/ai/search-services/searxng", json={"fields": {"url": "https://search.example.org"}})
    assert search_services.search("searxng", user, "q", 3) == []
    assert opened == [("plain", "http://searxng:8080/search?q=q&format=json"),
                      ("guarded", "https://search.example.org/search?q=q&format=json")]


class Stream:
    """What ai_client.open_ai returns, faked: SSE lines, as a context manager."""

    def __init__(self, events):
        self.lines = [f"data: {json.dumps(e)}\n".encode() for e in events] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _text(text):
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}


def test_ai_engine_keeps_only_pages_the_search_returned(monkeypatch):
    calls = []

    def fake_open(messages, system, entry, runtime, **kw):
        calls.append(kw)
        return Stream([
            {"type": "content_block_start", "content_block": {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result", "url": "https://lab.example.edu/prl.pdf", "title": "PRL 81, 5768"},
                {"type": "web_search_result", "url": "https://arxiv.org/abs/quant-ph/9901001", "title": "Preprint"}]}},
            _text("Degenerate Raman cooling | https://lab.example.edu/prl.pdf | The lab's copy.\n"),
            _text("- Made up | https://invented.example.com/x | Not from the search.\n")])

    monkeypatch.setattr(ai_client, "open_ai", fake_open)
    results = search_services.search("ai", "", "raman cooling", 5,
                                     ai={"runtime": ANTHROPIC, "entry": ENTRY, "effort": "high"})
    assert results == [
        {"title": "Degenerate Raman cooling", "url": "https://lab.example.edu/prl.pdf", "snippet": "The lab's copy."},
        {"title": "Preprint", "url": "https://arxiv.org/abs/quant-ph/9901001", "snippet": ""}]
    kw = calls[0]
    assert kw["effort"] == "low" and kw["stream"] and kw["tools"][0]["hosted"]["type"] == "web_search_20250305"
    # Without reported sources the listed lines are all there is.
    monkeypatch.setattr(ai_client, "open_ai", lambda *a, **kw: Stream([
        _text("1. [Lab page](https://lab.example.edu/pubs) — publications\n")]))
    assert search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY}) == [
        {"title": "Lab page", "url": "https://lab.example.edu/pubs", "snippet": "publications"}]

    def refused(*a, **kw):
        raise ai_client.UpstreamError(400, "web search is not enabled for this organization")

    monkeypatch.setattr(ai_client, "open_ai", refused)
    with pytest.raises(search_services.SearchError, match="refused the search"):
        search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY})
    with pytest.raises(search_services.SearchError, match="no web search of its own"):
        search_services.search("ai", "", "q", 5, ai={"runtime": GATEWAY, "entry": ENTRY})


# ------------------------------------------------------------- search_web

def test_search_web_tool_labels_identifiers_and_caps_searches(org, monkeypatch):
    ws = org[1]["ws"]
    monkeypatch.setattr(search_services, "search", lambda engine, user, query, limit, ai=None: [
        {"title": "PRL [81]", "url": "https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.81.5768",
         "snippet": "Degenerate Raman sideband cooling"},
        {"title": "Preprint", "url": "https://arxiv.org/abs/quant-ph/9901001", "snippet": ""},
        {"title": "Lab page", "url": "https://lab.example.edu/pubs", "snippet": ""}])
    text, action = run_agent_tool(ws, folder(""), "search_web", {"query": "raman"})
    assert action["error"] and "blocked by permissions" in action["summary"]  # no engine, no tool

    scope = {**folder(""), "web_engine": "brave"}
    text, action = run_agent_tool(ws, scope, "search_web", {"query": "raman sideband cooling"})
    assert action["kind"] == "websearch" and action["summary"].endswith("3 results")
    assert text.startswith('Web results for "raman sideband cooling" (3, via Brave Search — leads')
    assert r"- [PRL \[81\]](https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.81.5768)" in text
    assert 'doi:10.1103/PhysRevLett.81.5768 → search_papers(query="doi:10.1103/PhysRevLett.81.5768")' in text
    assert "arXiv:quant-ph/9901001 →" in text and "\n  Degenerate Raman sideband cooling" in text
    for _ in range(MAX_WEB_SEARCHES - 1):
        run_agent_tool(ws, scope, "search_web", {"query": "again"})
    text, action = run_agent_tool(ws, scope, "search_web", {"query": "one too many"})
    assert action["error"] and "is the limit for one message" in text

    def failing(*a, **kw):
        raise search_services.SearchError("Brave Search answered HTTP 429 — its rate limit or plan quota is reached")

    monkeypatch.setattr(search_services, "search", failing)
    text, action = run_agent_tool(ws, {**folder(""), "web_engine": "brave"}, "search_web", {"query": "q"})
    assert action["error"] and "HTTP 429" in text and action["summary"].startswith("Web search failed")


def test_service_test_route_reports_in_the_body(account, monkeypatch):
    c, _ = account
    assert c.post("/api/ai/search-services/brave/test").json() == {
        "ok": False, "error": "Brave Search has no key set up"}
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}})
    monkeypatch.setattr(search_services, "urlopen", lambda req, timeout=20: Reply(
        {"web": {"results": [{"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762"}]}}))
    assert c.post("/api/ai/search-services/brave/test").json() == {
        "ok": True, "text": "1 result — Attention Is All You Need"}
    assert c.post("/api/ai/search-services/openalex/test").json() == {"ok": False, "error": "no key set up"}
    assert c.post("/api/ai/search-services/bing/test").status_code == 404
