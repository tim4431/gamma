"""How a failed chat is reported: ai_client.failure_kind names the failure,
and /api/ai/chat carries that kind (plus the connection's name) next to
its plain-string ``detail``, which names the upstream status — on the HTTP error
and on the stream's closing error line — so the chat can show an error card
with the action that fixes it."""

import io
import json
import socket
import urllib.error

import pytest

from ai_fixtures import FakeResp, ai_provider, org  # noqa: F401  (fixtures)
from conftest import login, make_user
from gamma import ai_client
from gamma.ai_client import AllowanceExhausted, UpstreamError, failure_kind, sse_events
from gamma.ai_protocols.base import NotAnAIStream


@pytest.fixture(scope="module", autouse=True)
def _provider(ai_provider):
    """Every chat here needs a provider entry on the module's account."""


@pytest.mark.parametrize("error, kind", [
    (UpstreamError(401, "upstream 401: Incorrect API key provided"), "auth"),
    (UpstreamError(403, "upstream 403: forbidden"), "auth"),
    (UpstreamError(429, "upstream 429: You exceeded your current quota"), "rate"),
    (UpstreamError(529, "upstream 529: Overloaded"), "overloaded"),
    (UpstreamError(502, "upstream 502: Bad Gateway"), "overloaded"),
    (UpstreamError(400, "upstream 400: prompt is too long: 213000 tokens > 200000 maximum"), "too_long"),
    (UpstreamError(400, "upstream 400: This model's maximum context length is 128000 tokens"), "too_long"),
    (UpstreamError(413, "upstream 413: Request Entity Too Large"), "too_long"),
    (UpstreamError(400, "upstream 400: unsupported parameter"), "other"),
    (UpstreamError(404, "upstream 404: 404 Not Found"), "bad_endpoint"),
    (UpstreamError(404, "upstream 404: The model `gpt-9` does not exist"), "other"),
    (AllowanceExhausted(10, 5), "allowance"),
    (urllib.error.URLError(ConnectionRefusedError("refused")), "unreachable"),
    (socket.timeout("timed out"), "unreachable"),
    (ConnectionResetError("reset"), "unreachable"),
    (json.JSONDecodeError("Expecting value", "<html>", 0), "bad_endpoint"),
    (NotAnAIStream("no events"), "bad_endpoint"),
    (RuntimeError("Overloaded"), "overloaded"),       # an error event inside the stream
    (RuntimeError("rate_limit_error: slow down"), "rate"),
    (RuntimeError("something else"), "other"),
])
def test_failure_kind(error, kind):
    assert failure_kind(error) == kind


REGION = (b'{"error": {"code": "unsupported_country_region_territory", "message": '
          b'"Country, region, or territory not supported", "param": null, "type": "request_forbidden"}}')


def _answer(status, body, headers=None):
    """An HTTP error answer, as urllib raises it."""
    return urllib.error.HTTPError("https://api.example.test", status, "error", headers or {}, io.BytesIO(body))


@pytest.mark.parametrize("answer, kind", [
    (lambda: _answer(403, REGION, {"Server": "cloudflare", "CF-RAY": "1"}), "blocked"),
    (lambda: _answer(403, b"<!DOCTYPE html><title>Just a moment...</title>", {"cf-mitigated": "challenge"}), "blocked"),
    (lambda: _answer(403, b"<!DOCTYPE html><title>Attention Required! | Cloudflare</title>",
                     {"Server": "cloudflare"}), "blocked"),
    (lambda: _answer(403, b'{"error": {"message": "insufficient permissions"}}', {"Server": "cloudflare", "CF-RAY": "1"}),
     "auth"),
    (lambda: _answer(403, b"<html><title>403 Forbidden</title></html>", {"Server": "nginx"}), "auth"),
    (lambda: _answer(401, b"<!DOCTYPE html>", {"cf-mitigated": "challenge"}), "auth"),
    (lambda: UpstreamError(403, "upstream 403: Just a moment...", "Cloudflare in front of it refused"), "blocked"),
], ids=["region", "challenge", "cloudflare page", "the API's own 403", "another server's page", "a 401",
        "an upstream error"])
def test_a_403_refusing_the_network_is_blocked_not_auth(answer, kind):
    """OpenAI refusing the region the server connects from, or Cloudflare's
    challenge or block page in front of a service, is about the network,
    not the key or the sign-in; a service's own JSON 403 still is."""
    assert failure_kind(answer()) == kind


def test_a_refused_call_keeps_its_words_and_its_kind(monkeypatch):
    """open_ai reads the refusal's body once for both: the detail still has
    OpenAI's words, and the error knows the network was refused."""
    def urlopen(req, timeout=None):
        raise _answer(403, REGION)

    monkeypatch.setattr(ai_client.urllib.request, "urlopen", urlopen)
    rt = {"providers": {"p": {"protocol": "openai", "api_key": "k", "base_url": "https://api.openai.test/v1",
                              "name": "OpenAI"}}}
    with pytest.raises(UpstreamError) as failed:
        ai_client.open_ai([{"role": "user", "content": "hi"}], "", {"provider": "p", "model": "gpt-x"}, rt)
    assert str(failed.value) == "upstream 403: Country, region, or territory not supported"
    assert failure_kind(failed.value) == "blocked"


def test_a_stream_without_any_event_is_not_an_ai_api():
    """A 200 that isn't server-sent events at all (an HTML page from a base
    URL missing its path) is a bad endpoint, not an "empty response"."""
    with pytest.raises(NotAnAIStream):
        list(sse_events(iter([b"<!doctype html><title>Login</title>\n"]), "anthropic"))


def test_http_failure_carries_its_kind_next_to_the_detail(org, monkeypatch):
    c, _ = org
    import gamma.routers.ai as ai_mod

    def refuse(*a, **kw):
        raise UpstreamError(401, "upstream 401: Incorrect API key provided: sk-t***123")

    monkeypatch.setattr(ai_mod, "_open_ai", refuse)
    for stream in (True, False):
        r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": stream})
        assert r.status_code == 502, r.text
        body = r.json()
        assert body["detail"] == "AI call failed: upstream 401: Incorrect API key provided: sk-t***123"
        assert body["kind"] == "auth"
        assert body["provider_name"].startswith("Anthropic") and body["provider_auth"] == "key"
        assert body["provider_id"]


def test_stream_error_line_carries_its_kind(org, monkeypatch):
    """A reply that breaks off mid-stream ends with an error line that says
    what kind of failure it was; the text streamed before it stays."""
    c, _ = org
    import gamma.routers.ai as ai_mod

    class Broken(FakeResp):
        def __iter__(self):
            yield from list(super().__iter__())[:1]
            raise UpstreamError(529, "upstream 529: Overloaded")

    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **kw: Broken([
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Partial"}}]))
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 200, r.text
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert "".join(line.get("delta", "") for line in lines) == "Partial"
    assert lines[-1]["error"] == "AI call failed: upstream 529: Overloaded"
    assert lines[-1]["kind"] == "overloaded"


def test_no_connection_is_not_configured(client):
    make_user("failures-nobody", "pw")
    c = login("failures-nobody", "pw")
    r = c.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 503
    assert r.json()["kind"] == "not_configured"
    assert r.json()["detail"].startswith("AI not configured")
