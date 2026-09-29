"""General-web discovery transport, provider adapters, and safe failures."""

import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler

import pytest

from gamma import web_search


class Response(io.BytesIO):
    def __init__(self, body, *, content_type="application/json; charset=utf-8", length=None):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)


@pytest.fixture(autouse=True)
def clear_search_config(monkeypatch):
    for name in ("GAMMA_WEB_SEARCH_PROVIDER", "GAMMA_BRAVE_SEARCH_API_KEY", "GAMMA_SEARXNG_URL"):
        monkeypatch.delenv(name, raising=False)


def transport(monkeypatch, data=None, *, body=None, **response_options):
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        assert timeout == 20
        return Response(body if body is not None else json.dumps(data).encode(), **response_options)

    monkeypatch.setattr(web_search, "guarded_urlopen", open_request)
    return requests


def test_brave_normalizes_results_and_keeps_token_off_redirects(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret-test-token")
    calls = transport(monkeypatch, {"web": {"results": [
        {"title": "<b>Lab</b> &amp; publications", "url": "https://lab.example/papers", "description": "<p>Rb cooling.</p><p>15&#x3bc;K</p>"},
        {"title": "duplicate", "url": "https://LAB.example/papers#paper-2", "description": "Duplicate"},
        {"title": "Paper", "url": "https://lab.example/paper.pdf", "description": "<script>ignore</script>Full <b>text</b>"},
    ]}})
    assert web_search.search_web('  "Raman cooling"  filetype:pdf ', 2) == [
        {"title": "Lab & publications", "url": "https://lab.example/papers", "snippet": "Rb cooling. 15μK", "provider": "brave"},
        {"title": "Paper", "url": "https://lab.example/paper.pdf", "snippet": "Full text", "provider": "brave"},
    ]
    request = calls[0]
    parsed = urlsplit(request.full_url)
    assert parsed.netloc == "api.search.brave.com" and parsed.path == "/res/v1/web/search"
    assert parse_qs(parsed.query) == {"q": ['"Raman cooling" filetype:pdf'], "count": ["2"]}
    assert request.get_header("X-subscription-token") == "secret-test-token"
    assert "secret-test-token" not in request.full_url
    assert "X-subscription-token" not in request.headers
    redirected = HTTPRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://other.example/search")
    assert redirected.get_header("X-subscription-token") is None


@pytest.mark.parametrize("base,path", [
    ("https://search.example", "/search"),
    ("https://search.example/", "/search"),
    ("https://search.example/search", "/search"),
    ("https://search.example/search/", "/search"),
    ("https://search.example/searx", "/searx/search"),
    ("https://search.example/searx/search", "/searx/search"),
])
def test_searxng_json_endpoint_and_results(monkeypatch, base, path):
    monkeypatch.setenv("GAMMA_SEARXNG_URL", base)
    calls = transport(monkeypatch, {"query": "laser cooling", "results": [
        {"title": "<b>Laser</b> cooling", "url": "https://archive.example/paper", "content": "Author manuscript &amp; data.", "engine": "duckduckgo"},
    ]})
    results = web_search.search_web("laser cooling")
    assert results == [{"title": "Laser cooling", "url": "https://archive.example/paper",
                        "snippet": "Author manuscript & data.", "provider": "searxng"}]
    parsed = urlsplit(calls[0].full_url)
    assert parsed.path == path
    assert parse_qs(parsed.query) == {"format": ["json"], "q": ["laser cooling"]}
    assert calls[0].get_header("X-subscription-token") is None


def test_explicit_provider_overrides_inference(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "https://search.example")
    monkeypatch.setenv("GAMMA_WEB_SEARCH_PROVIDER", "searxng")
    calls = transport(monkeypatch, {"results": []})
    assert web_search.search_web("cooling") == []
    assert urlsplit(calls[0].full_url).hostname == "search.example"
    assert calls[0].get_header("X-subscription-token") is None


def test_config_is_lazy_and_brave_wins_inference(monkeypatch):
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "not_configured"
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "https://search.example")
    calls = transport(monkeypatch, {"web": {"results": []}})
    assert web_search.search_web("cooling") == []
    assert urlsplit(calls[0].full_url).hostname == "api.search.brave.com"


@pytest.mark.parametrize("settings", [
    {"GAMMA_WEB_SEARCH_PROVIDER": "unsupported-secret"},
    {"GAMMA_WEB_SEARCH_PROVIDER": "brave"},
    {"GAMMA_WEB_SEARCH_PROVIDER": "brave", "GAMMA_BRAVE_SEARCH_API_KEY": "bad\r\ntoken"},
    {"GAMMA_WEB_SEARCH_PROVIDER": "searxng"},
    {"GAMMA_SEARXNG_URL": "https://user:secret@search.example"},
    {"GAMMA_SEARXNG_URL": "https://search.example?token=secret"},
    {"GAMMA_SEARXNG_URL": "https://search.example/#secret"},
    {"GAMMA_SEARXNG_URL": "file:///search"},
])
def test_invalid_config_is_actionable_and_does_not_echo_values(monkeypatch, settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "configuration"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("query", [None, "", " \n ", "x" * 601, "word " * 76])
def test_invalid_queries_fail_before_network(query):
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web(query)
    assert error.value.code == "invalid_query"


@pytest.mark.parametrize("limit,expected", [(100, 20), (0, 1), (-3, 1), (None, 8)])
def test_result_limit_is_bounded(monkeypatch, limit, expected):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    calls = transport(monkeypatch, {"web": {"results": [
        {"title": "Paper", "url": f"https://lab.example/{i}"} for i in range(30)
    ]}})
    assert len(web_search.search_web("cooling", limit)) == expected
    assert parse_qs(urlsplit(calls[0].full_url).query)["count"] == [str(expected)]


def test_invalid_and_credentialed_result_urls_are_filtered(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    urls = ["javascript:alert(1)", "ftp://lab.example/paper", "//lab.example/paper", "https:///paper",
            "https://user:secret@lab.example/paper", "https://lab.example:bad/paper", "https://[broken/paper",
            "https://lab.example/\r\npaper", "https://lab.example/paper%ZZ", None,
            "https://bad^host.example/paper", "https://bad%0ahost.example/paper",
            "https://lab.example/paper.pdf"]
    transport(monkeypatch, {"web": {"results": [{"title": "Paper", "url": url} for url in urls]}})
    assert [item["url"] for item in web_search.search_web("cooling")] == ["https://lab.example/paper.pdf"]


def test_result_text_is_bounded(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    transport(monkeypatch, {"web": {"results": [
        {"title": "<b>" + "x" * 500 + "</b>", "url": "https://lab.example", "description": "atoms &amp; light " * 300},
    ]}})
    result = web_search.search_web("cooling")[0]
    assert len(result["title"]) <= web_search.TITLE_CHARS
    assert len(result["snippet"]) <= web_search.SNIPPET_CHARS
    assert "&amp;" not in result["snippet"] and "<b>" not in result["title"]


@pytest.mark.parametrize("payload", [[], {}, {"web": []}, {"web": {"results": {}}},
                                    {"web": {"results": ["broken"]}}, {"error": {"message": "secret"}}])
def test_invalid_brave_schema_is_not_empty_success(monkeypatch, payload):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    transport(monkeypatch, payload)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "invalid_response"
    assert "secret" not in str(error.value)


def test_brave_can_omit_web_section_when_no_results(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    transport(monkeypatch, {"type": "search", "query": {"original": "no match"}})
    assert web_search.search_web("no match") == []


@pytest.mark.parametrize("payload,code", [
    ({"results": None}, "invalid_response"),
    ({"results": [], "unresponsive_engines": [["google", "timeout"]]}, "unavailable"),
])
def test_searxng_errors_are_not_empty_success(monkeypatch, payload, code):
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "https://search.example")
    transport(monkeypatch, payload)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == code


@pytest.mark.parametrize("body,content_type", [
    (b'{"secret":', "application/json"),
    (b'<html>secret error page</html>', "text/html"),
    (b'{"results": []}', "text/plain"),
])
def test_response_must_be_valid_json(monkeypatch, body, content_type):
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "https://search.example")
    transport(monkeypatch, body=body, content_type=content_type)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "invalid_response"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("announced", [True, False])
def test_response_size_bound_with_and_without_content_length(monkeypatch, announced):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")
    monkeypatch.setattr(web_search, "RESPONSE_MAX_BYTES", 100)
    transport(monkeypatch, body=b" " * 101, length=101 if announced else None)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "invalid_response"
    assert "2 MB" in str(error.value)


@pytest.mark.parametrize("status,code", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "unavailable")])
def test_http_errors_do_not_expose_provider_messages(monkeypatch, status, code):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")

    def fail(request, timeout):
        raise HTTPError("https://search.example/?secret", status, "secret response", {}, io.BytesIO(b"secret body"))

    monkeypatch.setattr(web_search, "guarded_urlopen", fail)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == code
    assert "secret" not in str(error.value)


def test_searxng_forbidden_explains_json_requirement(monkeypatch):
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "https://search.example")

    def fail(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(web_search, "guarded_urlopen", fail)
    with pytest.raises(web_search.WebSearchError, match="JSON output"):
        web_search.search_web("cooling")


def test_network_errors_are_sanitized(monkeypatch):
    monkeypatch.setenv("GAMMA_BRAVE_SEARCH_API_KEY", "secret")

    def fail(request, timeout):
        raise URLError("secret transport detail")

    monkeypatch.setattr(web_search, "guarded_urlopen", fail)
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "unavailable"
    assert "secret" not in str(error.value)


def test_real_ssrf_guard_blocks_private_searxng_before_connection(monkeypatch):
    monkeypatch.setenv("GAMMA_SEARXNG_URL", "http://127.0.0.1:8888")
    with pytest.raises(web_search.WebSearchError) as error:
        web_search.search_web("cooling")
    assert error.value.code == "unavailable"
