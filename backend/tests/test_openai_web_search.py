"""Hosted OpenAI search: real request/response shapes, evidence and credentials."""

import io
import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler

import pytest

from gamma import ai_usage, web_search, web_search_settings


class Response(io.BytesIO):
    headers = {"Content-Type": "application/json"}


@pytest.fixture
def search_config(monkeypatch):
    conf = {"provider": "openai", "api_key": "secret-search-key", "model": "gpt-4.1-mini",
            "url": "https://api.openai.com/v1/responses"}
    def credentials(user):
        assert user == "researcher"
        return conf
    monkeypatch.setattr(web_search_settings, "credentials", credentials)
    return conf


def result(*, sources=None, annotations=None, text="Cooling in an optical lattice. ", **fields):
    return {"status": "completed", "output": [
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "search", "sources": sources or []}},
        {"type": "message", "content": [{"type": "output_text", "text": text,
            "annotations": annotations or []}]}], **fields}


def transport(monkeypatch, data):
    calls = []
    def send(request, timeout):
        assert timeout == 60
        calls.append(request)
        return Response(json.dumps(data).encode())
    monkeypatch.setattr(web_search, "guarded_urlopen", send)
    return calls


def test_openai_uses_hosted_search_and_citations_not_invented_links(search_config, monkeypatch):
    text = "Cooling in an optical lattice. [source] An invented https://fake.example/paper.pdf"
    calls = transport(monkeypatch, result(text=text, annotations=[
        {"type": "url_citation", "url": "https://lab.example/paper.pdf", "title": "<b>Cooling</b>",
         "start_index": 30, "end_index": 38}], sources=[
        {"type": "url", "url": "https://lab.example/paper.pdf"},
        {"type": "url", "url": "https://journal.example/article", "title": "Journal article"}]))
    rows = web_search.search_web("ytterbium quantum gas", 2, user="researcher")
    assert [r["url"] for r in rows] == ["https://lab.example/paper.pdf", "https://journal.example/article"]
    assert rows[0]["title"] == "Cooling"
    assert rows[0]["snippet"] == "Search summary: Cooling in an optical lattice."
    assert all(r["provider"] == "openai" for r in rows)
    req = calls[0]
    body = json.loads(req.data)
    assert req.full_url == "https://api.openai.com/v1/responses"
    assert body["model"] == search_config["model"]
    assert body["tools"] == [{"type": "web_search"}]
    assert body["tool_choice"] == "required"
    assert body["include"] == ["web_search_call.action.sources"]
    assert body["store"] is False and body["stream"] is False
    assert "ytterbium quantum gas" in body["input"]
    assert "secret-search-key" not in req.full_url + req.data.decode()
    assert req.get_header("Authorization") == "Bearer secret-search-key"
    assert "Authorization" not in req.headers
    redirect = HTTPRedirectHandler().redirect_request(req, None, 302, "Found", {}, "https://elsewhere.example/")
    assert redirect.get_header("Authorization") is None


def test_consulted_sources_are_bounded_deduplicated_and_filtered(search_config, monkeypatch):
    transport(monkeypatch, result(sources=[
        {"url": "javascript:alert(1)"}, {"url": "https://user:pass@lab.example/paper"},
        {"url": "https://lab.example/a", "title": "First"},
        {"url": "https://LAB.example/a#section", "title": "Again"},
        {"url": "https://lab.example/b"}, {"url": "https://lab.example/c"}]))
    rows = web_search.search_web("cooling", 2, user="researcher")
    assert [r["url"] for r in rows] == ["https://lab.example/a", "https://lab.example/b"]
    assert rows[1]["title"] == "lab.example" and rows[1]["snippet"] == ""


@pytest.mark.parametrize("data", [
    {"status": "incomplete", "output": []}, {"status": "failed", "output": []},
    {"status": "completed", "output": []},
    {"status": "completed", "output": [{"type": "web_search_call", "status": "failed"}]},
    {"status": "completed", "output": [{"type": "web_search_call", "status": "completed", "action": "bad"}]},
    {"status": "completed", "output": [{"type": "web_search_call", "status": "completed",
                                          "action": {"type": "search", "sources": "bad"}}]},
])
def test_unsearched_or_incomplete_answers_are_errors(search_config, monkeypatch, data):
    transport(monkeypatch, data)
    with pytest.raises(web_search.WebSearchError) as exc:
        web_search.search_web("cooling", user="researcher")
    assert exc.value.code == "invalid_response"


def test_completed_search_can_have_zero_sources(search_config, monkeypatch):
    transport(monkeypatch, result())
    assert web_search.search_web("cooling", user="researcher") == []


@pytest.mark.parametrize("status,code", [(400, "configuration"), (401, "auth"), (403, "auth"),
                                          (404, "configuration"), (429, "rate_limit"), (500, "unavailable")])
def test_openai_errors_do_not_leak_provider_bodies_or_keys(search_config, monkeypatch, status, code):
    def send(request, timeout):
        raise HTTPError(request.full_url, status, "secret-search-key", {}, io.BytesIO(b"secret-search-key"))
    monkeypatch.setattr(web_search, "guarded_urlopen", send)
    with pytest.raises(web_search.WebSearchError) as exc:
        web_search.search_web("cooling", user="researcher")
    assert exc.value.code == code
    assert "secret-search-key" not in str(exc.value)


def test_search_usage_is_recorded_for_the_authenticated_account(search_config, monkeypatch):
    transport(monkeypatch, result(usage={"input_tokens": 43, "output_tokens": 12,
                                       "input_tokens_details": {"cached_tokens": 7}}))
    recorded = []
    monkeypatch.setattr(ai_usage, "record", lambda *args: recorded.append(args))
    web_search.search_web("cooling", user="researcher")
    assert recorded == [("researcher", "web_search", "web-search:openai", "OpenAI web search", "gpt-4.1-mini",
                         {"input": 43, "output": 12, "cache_read": 7, "cache_write": 0})]


def test_account_configuration_error_makes_no_network_call(monkeypatch):
    def credentials(user):
        raise web_search_settings.SearchConfigurationError("Choose a search service in Settings", code="not_configured")
    monkeypatch.setattr(web_search_settings, "credentials", credentials)
    monkeypatch.setattr(web_search, "guarded_urlopen", lambda *a, **k: pytest.fail("must not request"))
    with pytest.raises(web_search.WebSearchError) as exc:
        web_search.search_web("cooling", user="researcher")
    assert exc.value.code == "not_configured"
