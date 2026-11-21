"""General web search for the agent (gamma/search_services.py): the stored
settings and their masked view, which engine a chat gets, the Brave,
Tavily, SearXNG and AI-connection engines (recency, site, dates), the
search_web tool, and the settings routes. Every engine call is faked;
nothing reaches the network."""

import io
import json
from datetime import datetime, timezone

import pytest

from gamma import ai_client, search_services
from gamma.ai_tools.registry import run_agent_tool
from gamma.ai_tools.web import MAX_WEB_SEARCHES
from gamma.db import page_now

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)

ANTHROPIC = {"providers": {"p1": {"protocol": "anthropic", "base_url": "https://api.anthropic.com",
                                  "api_key": "k", "name": "Anthropic"}}}
OPENAI = {"providers": {"p1": {"protocol": "openai", "base_url": "https://api.openai.com",
                               "api_key": "k", "name": "OpenAI"}}}
CHATGPT = {"providers": {"p1": {"protocol": "chatgpt", "base_url": "https://chatgpt.com/backend-api/codex",
                                "api_key": "t", "account_id": "a", "name": "ChatGPT"}}}
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
    return c, ids["user_id"]


# ------------------------------------------------------------- settings

def test_settings_are_masked_and_secrets_kept(account):
    c, user = account
    view = c.get("/api/ai/search-services").json()
    assert view["engine"] == "auto" and view["can_edit"]
    # The AI connection is a choice, never a service row.
    assert [s["id"] for s in view["services"]] == ["brave", "tavily", "searxng", "openalex"]
    assert not any(s["configured"] for s in view["services"])

    view = c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}}).json()
    brave = next(s for s in view["services"] if s["id"] == "brave")
    assert brave["configured"] and brave["fields"] == {"api_key": "…3456"}
    assert "BSA-secret-123456" not in json.dumps(view)
    view = c.put("/api/ai/search-services/tavily", json={"fields": {"api_key": "tvly-secret-7890"}}).json()
    tavily = next(s for s in view["services"] if s["id"] == "tavily")
    assert tavily["configured"] and tavily["fields"] == {"api_key": "…7890"} and tavily["web"]
    assert "tvly-secret" not in json.dumps(view)
    # An empty secret keeps the stored one.
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": ""}})
    assert search_services.load(user)["brave"]["api_key"] == "BSA-secret-123456"

    assert c.put("/api/ai/search-services/searxng", json={"fields": {"url": "ftp://x"}}).status_code == 400
    c.put("/api/ai/search-services/searxng", json={"fields": {"url": "https://search.example.org/"}})
    assert search_services.load(user)["searxng"]["url"] == "https://search.example.org"
    assert c.put("/api/ai/search-services/engine", json={"engine": "bing"}).status_code == 400
    assert c.put("/api/ai/search-services/engine", json={"engine": "tavily"}).json()["engine"] == "tavily"
    assert c.put("/api/ai/search-services/engine", json={"engine": "off"}).json()["engine"] == "off"
    assert c.delete("/api/ai/search-services/nope").status_code == 404
    assert c.put("/api/ai/search-services/ai", json={"fields": {}}).status_code == 404
    # The generic prefs endpoints never serve the stored keys.
    assert c.get("/api/prefs/search-services").status_code == 400
    assert c.put("/api/prefs/search-services", json={"value": {}}).status_code == 400


def test_which_engine_a_chat_gets(account, server_option):
    c, user = account
    server_option("searxng_url", "")
    assert search_services.web_engine(user, GATEWAY, ENTRY) == ""  # a gateway hosts no search
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "ai"
    assert search_services.web_engine(user, OPENAI, ENTRY) == "ai"  # through its Responses wire
    assert search_services.web_engine(user) == ""  # no connection (a context export)
    # Automatic: Brave, Tavily, SearXNG, then the AI connection.
    server_option("searxng_url", "http://searxng:8080")
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "searxng"
    c.put("/api/ai/search-services/tavily", json={"fields": {"api_key": "tvly-secret-7890"}})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "tavily"
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "brave"
    # A named engine is used only when it is ready — never a silent switch.
    server_option("searxng_url", "")
    c.put("/api/ai/search-services/engine", json={"engine": "searxng"})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == ""
    server_option("searxng_url", "http://searxng:8080")
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == "searxng"  # the admin's instance
    c.put("/api/ai/search-services/engine", json={"engine": "ai"})
    assert search_services.web_engine(user, GATEWAY, ENTRY) == ""
    c.put("/api/ai/search-services/engine", json={"engine": "off"})
    assert search_services.web_engine(user, ANTHROPIC, ENTRY) == ""


# ------------------------------------------------------------- the request

@pytest.mark.parametrize("value, host", [
    ("https://Docs.Python.org/3/library/", "docs.python.org"),
    ("site:arxiv.org", "arxiv.org"),
    ("*.example.com", "example.com"),
    ("github.com/psf/requests", "github.com"),
    ("bücher.de", "xn--bcher-kva.de"),
    ("a.com b.com", ""),
    ("a.com,b.com", ""),
    ("python docs", ""),
    ("localhost", ""),
    ("127.0.0.1", ""),
])
def test_site_of_gives_one_bare_host(value, host):
    assert search_services.site_of(value) == host


# ------------------------------------------------------------- engines

def test_brave_sends_the_key_in_a_header_and_cleans_results(account, monkeypatch):
    c, user = account
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}})
    seen = []

    def fake_urlopen(req, timeout=20):
        seen.append(req)
        return Reply({"web": {"results": [
            {"title": "Vuletić <strong>lab</strong> publications", "url": "https://lab.example.edu/pubs",
             "description": "All papers &amp; talks", "page_age": "2025-03-11T07:13:52", "age": "March 11, 2025"},
            {"title": "dup", "url": "https://lab.example.edu/pubs/?utm_source=feed"},
            {"title": "News", "url": "https://news.example.org/a?id=7&utm_medium=rss", "age": "3 days ago"},
            {"title": "javascript", "url": "javascript:alert(1)"},
            {"title": "creds", "url": "https://user:pw@example.org/x"}]}})

    monkeypatch.setattr(search_services, "urlopen", fake_urlopen)
    results = search_services.search("brave", user, "raman sideband cooling", 5)
    assert results == [
        {"title": "Vuletić lab publications", "url": "https://lab.example.edu/pubs",
         "snippet": "All papers & talks", "date": "2025-03-11"},
        {"title": "News", "url": "https://news.example.org/a?id=7", "snippet": "", "date": "3 days ago"}]
    req = seen[0]
    assert req.get_header("X-subscription-token") == "BSA-secret-123456"
    assert "BSA-secret" not in req.full_url and "q=raman+sideband+cooling&" in req.full_url
    assert "freshness" not in req.full_url

    search_services.search("brave", user, "asyncio TaskGroup", 5, recency="week",
                           site="https://docs.python.org/3/")
    assert "q=asyncio+TaskGroup+site%3Adocs.python.org&" in seen[1].full_url
    assert "freshness=pw" in seen[1].full_url
    search_services.search("brave", user, "q", 5, recency="fortnight", site="a.com b.com")
    assert "freshness" not in seen[2].full_url and "site" not in seen[2].full_url


def test_tavily_posts_a_bearer_key_and_its_filters(account, monkeypatch):
    c, user = account
    with pytest.raises(search_services.SearchError, match="Tavily has no key set up"):
        search_services.search("tavily", user, "q", 3)
    c.put("/api/ai/search-services/tavily", json={"fields": {"api_key": "tvly-secret-7890"}})
    seen = []

    def fake_urlopen(req, timeout=20):
        seen.append(req)
        return Reply({"query": "q", "response_time": 0.4, "results": [
            {"title": "Python 3.14 released", "url": "https://blog.python.org/2025/10/python-3140.html",
             "content": "Python 3.14.0 is out. [...] Free-threaded", "score": 0.9,
             "published_date": "Tue, 07 Oct 2025 17:00:00 GMT"},
            {"title": "Undated", "url": "https://www.python.org/", "content": "Home", "published_date": None}]})

    monkeypatch.setattr(search_services, "urlopen", fake_urlopen)
    results = search_services.search("tavily", user, "python 3.14 release", 5, recency="month",
                                     site="site:python.org")
    assert results == [
        {"title": "Python 3.14 released", "url": "https://blog.python.org/2025/10/python-3140.html",
         "snippet": "Python 3.14.0 is out. [...] Free-threaded", "date": "2025-10-07"},
        {"title": "Undated", "url": "https://www.python.org/", "snippet": "Home"}]
    req = seen[0]
    assert req.full_url == search_services.TAVILY_URL and req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer tvly-secret-7890"
    assert json.loads(req.data) == {"query": "python 3.14 release", "max_results": 5, "search_depth": "basic",
                                    "include_published_date": True, "time_range": "month",
                                    "include_domains": ["python.org"]}
    search_services.search("tavily", user, "q", 3)
    assert "time_range" not in json.loads(seen[1].data) and "include_domains" not in json.loads(seen[1].data)


def test_searxng_guards_an_accounts_url_but_not_the_admins(account, monkeypatch, server_option):
    c, user = account
    opened = []
    monkeypatch.setattr(search_services, "guarded_urlopen",
                        lambda req, timeout=20: opened.append(("guarded", req.full_url)) or Reply({"results": [
                            {"title": "Release", "url": "https://example.org/r", "content": "x",
                             "publishedDate": "2025-10-07T00:00:00"}]}))
    monkeypatch.setattr(search_services, "urlopen",
                        lambda req, timeout=20: opened.append(("plain", req.full_url)) or Reply(b"<html>"))
    server_option("searxng_url", "http://searxng:8080/search")
    with pytest.raises(search_services.SearchError, match="enable the json format"):
        search_services.search("searxng", user, "q", 3)
    c.put("/api/ai/search-services/searxng", json={"fields": {"url": "https://search.example.org"}})
    assert search_services.search("searxng", user, "q", 3, recency="year", site="example.org") == [
        {"title": "Release", "url": "https://example.org/r", "snippet": "x", "date": "2025-10-07"}]
    assert opened == [("plain", "http://searxng:8080/search?q=q&format=json"),
                      ("guarded", "https://search.example.org/search?q=q+site%3Aexample.org&format=json"
                                  "&time_range=year")]


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


SEARCH_CALL = {"type": "content_block_start", "content_block": {"type": "server_tool_use", "id": "s1",
                                                                "name": "web_search"}}
USAGE_START = {"type": "message_start", "message": {"usage": {"input_tokens": 120, "speed": "standard"}}}
USAGE_END = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 40}}
SPENT = {"input": 120, "output": 40, "cache_read": 0, "cache_write": 0}


def test_ai_engine_keeps_only_pages_the_search_returned(monkeypatch):
    calls = []

    def fake_open(messages, system, entry, runtime, **kw):
        calls.append({"prompt": messages[0]["content"], **kw})
        return Stream([
            USAGE_START, SEARCH_CALL, {"type": "content_block_stop"},
            {"type": "content_block_start", "content_block": {"type": "web_search_tool_result", "content": [
                {"type": "web_search_result", "url": "https://docs.python.org/3/whatsnew/3.14.html",
                 "title": "What's New In Python 3.14", "page_age": "October 7, 2025"},
                {"type": "web_search_result", "url": "https://www.python.org/downloads/release/python-3140/",
                 "title": "Python 3.14.0"}]}},
            # A date the model writes is never the result's date.
            _text("What's new | https://docs.python.org/3/whatsnew/3.14.html | Released 2024-01-01.\n"),
            _text("- Made up | https://invented.example.com/x | Not from the search.\n"),
            USAGE_END])

    monkeypatch.setattr(ai_client, "open_ai", fake_open)
    meter = {}
    results = search_services.search("ai", "", "python 3.14 changes", 5, recency="week", site="docs.python.org",
                                     ai={"runtime": ANTHROPIC, "entry": ENTRY, "effort": "high"}, meter=meter)
    assert results == [
        {"title": "What's new", "url": "https://docs.python.org/3/whatsnew/3.14.html",
         "snippet": "Released 2024-01-01.", "date": "2025-10-07"},
        {"title": "Python 3.14.0", "url": "https://www.python.org/downloads/release/python-3140/", "snippet": ""}]
    # The sub-call's counts, without the served speed (the chat's reply keeps its own).
    assert meter == SPENT
    call = calls[0]
    assert call["effort"] == "low" and call["stream"]
    assert call["tools"][0]["hosted"] == {"type": "web_search_20250305", "name": "web_search", "max_uses": 5,
                                          "allowed_domains": ["docs.python.org"]}
    today = datetime.now(timezone.utc).date().isoformat()
    assert "Search the web for: python 3.14 changes site:docs.python.org" in call["prompt"]
    assert f"past week: today is {today} (UTC)" in call["prompt"] and "arXiv" not in call["prompt"]

    # A reply that never searched lists URLs from memory: no results.
    monkeypatch.setattr(ai_client, "open_ai", lambda *a, **kw: Stream([
        _text("1. [Lab page](https://lab.example.edu/pubs) — publications\n")]))
    with pytest.raises(search_services.SearchError, match="without searching the web"):
        search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY})

    # Nor does one whose search failed — and what it spent still counts.
    monkeypatch.setattr(ai_client, "open_ai", lambda *a, **kw: Stream([
        USAGE_START, SEARCH_CALL,
        {"type": "content_block_start", "content_block": {"type": "web_search_tool_result", "content": {
            "type": "web_search_tool_result_error", "error_code": "too_many_requests"}}},
        _text("Lab | https://lab.example.edu/pubs | from memory\n"), USAGE_END]))
    meter = {}
    with pytest.raises(search_services.SearchError, match=r"did not complete \(too_many_requests\)"):
        search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY}, meter=meter)
    assert meter == SPENT

    # A search that ran and found nothing: what the model lists is from memory.
    monkeypatch.setattr(ai_client, "open_ai", lambda *a, **kw: Stream([
        SEARCH_CALL, {"type": "content_block_start", "content_block": {"type": "web_search_tool_result", "content": []}},
        _text("Remembered | https://remembered.example/page | from memory\n"), USAGE_END]))
    assert search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY}) == []


def test_ai_engine_on_the_responses_wires(monkeypatch):
    calls = []
    page = "https://docs.python.org/3/whatsnew/3.14.html"

    def fake_open(messages, system, entry, runtime, **kw):
        calls.append(kw)
        return Stream([
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": "completed",
                                                           "action": {"type": "search", "sources": [
                                                               {"type": "url", "url": page + "?utm_source=openai"}]}}},
            {"type": "response.output_text.delta", "delta": f"What's new | {page} | Release notes.\n"},
            {"type": "response.output_item.done", "item": {"type": "message", "content": [{
                "type": "output_text", "text": "…", "annotations": [
                    {"type": "url_citation", "url": page + "?utm_source=openai", "title": "What's New"}]}]}},
            {"type": "response.completed", "response": {"status": "completed"}}])

    monkeypatch.setattr(ai_client, "open_ai", fake_open)
    # OpenAI's platform searches over its Responses wire; the citation's
    # utm_source neither hides the listed line nor shows in its address.
    assert search_services.search("ai", "", "python 3.14", 5, site="docs.python.org",
                                  ai={"runtime": OPENAI, "entry": ENTRY}) == [
        {"title": "What's new", "url": page, "snippet": "Release notes."}]
    assert calls[0]["tools"][0]["hosted"] == {"type": "web_search",
                                              "filters": {"allowed_domains": ["docs.python.org"]}}

    # The ChatGPT backend reports no sources: a search that ran keeps the
    # lines it listed, one that failed gives none.
    def chatgpt(status):
        return lambda *a, **kw: Stream([
            {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": status,
                                                           "action": {"type": "search", "query": "q"}}},
            {"type": "response.output_text.delta", "delta": "1. [Lab page](https://lab.example.edu/pubs) — papers\n"},
            {"type": "response.completed", "response": {"status": "completed"}}])

    monkeypatch.setattr(ai_client, "open_ai", chatgpt("completed"))
    assert search_services.search("ai", "", "q", 5, ai={"runtime": CHATGPT, "entry": ENTRY}) == [
        {"title": "Lab page", "url": "https://lab.example.edu/pubs", "snippet": "papers"}]
    monkeypatch.setattr(ai_client, "open_ai", chatgpt("failed"))
    with pytest.raises(search_services.SearchError, match="did not complete"):
        search_services.search("ai", "", "q", 5, ai={"runtime": CHATGPT, "entry": ENTRY})


def test_ai_engine_refusals_are_search_errors(monkeypatch):
    def refused(*a, **kw):
        raise ai_client.UpstreamError(400, "web search is not enabled for this organization")

    monkeypatch.setattr(ai_client, "open_ai", refused)
    with pytest.raises(search_services.SearchError, match="refused the search"):
        search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY})

    def busy(*a, **kw):
        raise ai_client.TooManyCalls(ai_client.MAX_OPEN_CALLS)

    monkeypatch.setattr(ai_client, "open_ai", busy)
    with pytest.raises(search_services.SearchError, match="Too many AI requests are running"):
        search_services.search("ai", "", "q", 5, ai={"runtime": ANTHROPIC, "entry": ENTRY})
    with pytest.raises(search_services.SearchError, match="no web search of its own"):
        search_services.search("ai", "", "q", 5, ai={"runtime": GATEWAY, "entry": ENTRY})


# ------------------------------------------------------------- search_web

def test_search_web_tool_labels_identifiers_and_caps_searches(org, monkeypatch):
    ws = org[1]["ws"]
    asked = []

    def search(engine, user, query, limit, **kw):
        asked.append(kw)
        if query == "nothing":
            return []
        return [{"title": "PRL [81]", "url": "https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.81.5768",
                 "snippet": "Degenerate Raman sideband cooling", "date": "1998-12-28"},
                {"title": "Preprint", "url": "https://arxiv.org/abs/quant-ph/9901001", "snippet": "",
                 "date": "3 days ago"},
                {"title": "Lab page", "url": "https://lab.example.edu/pubs", "snippet": ""}]

    monkeypatch.setattr(search_services, "search", search)
    text, action = run_agent_tool(ws, folder(""), "search_web", {"query": "raman"})
    assert action["error"] and "blocked by permissions" in action["summary"]  # no engine, no tool

    scope = {**folder(""), "web_engine": "brave"}
    text, action = run_agent_tool(ws, scope, "search_web", {"query": "raman sideband cooling"})
    assert action["kind"] == "websearch" and action["summary"].endswith("3 results") and "spent" not in action
    assert text.startswith(f'Web results for "raman sideband cooling" (3, via Brave Search, searched {page_now()[:10]} '
                           "— leads: a snippet is what a page says about itself; read a page with fetch_url, "
                           "or a paper with fetch_paper, before relying on it):\n")
    assert (r"- [PRL \[81\]](https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.81.5768) · 1998-12-28 · "
            'doi:10.1103/PhysRevLett.81.5768 → search_papers(query="doi:10.1103/PhysRevLett.81.5768")') in text
    assert "(https://arxiv.org/abs/quant-ph/9901001) · 3 days ago · arXiv:quant-ph/9901001 →" in text
    assert "\n  Degenerate Raman sideband cooling" in text
    assert text.endswith("\n- [Lab page](https://lab.example.edu/pubs)")  # no date, no snippet: the bare link
    for _ in range(MAX_WEB_SEARCHES - 1):
        run_agent_tool(ws, scope, "search_web", {"query": "again"})
    text, action = run_agent_tool(ws, scope, "search_web", {"query": "one too many"})
    assert action["error"] and "is the limit for one message" in text

    # The filters reach the engine as asked, and the head says which applied.
    scope = {**folder(""), "web_engine": "brave"}
    text, _ = run_agent_tool(ws, scope, "search_web", {"query": "asyncio", "recency": "Week",
                                                       "site": "https://docs.python.org/3/"})
    assert (asked[-1]["recency"], asked[-1]["site"]) == ("week", "docs.python.org")
    assert text.startswith(f'Web results for "asyncio" (3, via Brave Search, past week, docs.python.org, '
                           f"searched {page_now()[:10]} — leads")
    text, _ = run_agent_tool(ws, scope, "search_web", {"query": "nothing", "recency": "year"})
    assert text == (f'No web results for "nothing" (via Brave Search, past year, searched {page_now()[:10]}). '
                    "Widening the filters may help.")
    searched = len(asked)
    text, _ = run_agent_tool(ws, scope, "search_web", {"query": "q", "recency": "decade"})
    assert text == "error: recency must be one of day, week, month, year"
    text, _ = run_agent_tool(ws, scope, "search_web", {"query": "q", "site": "a.org, b.org"})
    assert text.startswith("error: site must be one domain")
    assert len(asked) == searched  # refused before any engine call

    # What the AI engine's call spent rides on the action, failed or not.
    def metered(engine, user, query, limit, *, meter, **kw):
        meter.update(input=900, output=40)
        if query == "fails":
            raise search_services.SearchError("the AI connection answered without searching the web")
        return [{"title": "Python", "url": "https://www.python.org/", "snippet": ""}]

    monkeypatch.setattr(search_services, "search", metered)
    scope = {**folder(""), "web_engine": "ai"}
    _, action = run_agent_tool(ws, scope, "search_web", {"query": "python"})
    assert action["spent"] == {"input": 900, "output": 40}
    text, action = run_agent_tool(ws, scope, "search_web", {"query": "fails"})
    assert action["error"] and action["spent"] == {"input": 900, "output": 40}
    assert text == "error: the AI connection answered without searching the web"

    def failing(*a, **kw):
        raise search_services.SearchError("Brave Search answered HTTP 429 — its rate limit or plan quota is reached")

    monkeypatch.setattr(search_services, "search", failing)
    text, action = run_agent_tool(ws, {**folder(""), "web_engine": "brave"}, "search_web", {"query": "q"})
    assert action["error"] and "HTTP 429" in text and action["summary"].startswith("Web search failed")
    assert "spent" not in action


def test_service_test_route_reports_in_the_body(account, monkeypatch):
    c, _ = account
    assert c.post("/api/ai/search-services/brave/test").json() == {
        "ok": False, "error": "Brave Search has no key set up"}
    c.put("/api/ai/search-services/brave", json={"fields": {"api_key": "BSA-secret-123456"}})
    monkeypatch.setattr(search_services, "urlopen", lambda req, timeout=20: Reply(
        {"web": {"results": [{"title": "Python (programming language)", "url": "https://www.python.org/"}]}}))
    assert c.post("/api/ai/search-services/brave/test").json() == {
        "ok": True, "text": "1 result — Python (programming language)"}
    c.put("/api/ai/search-services/tavily", json={"fields": {"api_key": "tvly-secret-7890"}})
    monkeypatch.setattr(search_services, "urlopen", lambda req, timeout=20: Reply(
        {"results": [{"title": "Welcome to Python.org", "url": "https://www.python.org/", "content": "…"}]}))
    assert c.post("/api/ai/search-services/tavily/test").json() == {
        "ok": True, "text": "1 result — Welcome to Python.org"}
    assert c.post("/api/ai/search-services/openalex/test").json() == {"ok": False, "error": "no key set up"}
    assert c.post("/api/ai/search-services/bing/test").status_code == 404
    assert c.post("/api/ai/search-services/ai/test").status_code == 404
