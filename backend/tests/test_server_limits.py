"""Server-wide limits: the worker-thread pool sized for slow outbound work,
the per-account cap on AI calls open at once, gzip for JSON answers only,
and the rate limiter's bounded memory and client address."""

import gc
import gzip
import io
import ipaddress
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ai_fixtures import FakeResp
from conftest import login, make_page, make_user

from gamma import ai_client, ratelimit
from gamma.ai_settings import ai_runtime
from gamma.compression import JsonGzip


# --- the worker threads ---------------------------------------------------------

def test_thread_limiter_is_raised_at_startup(client):
    from gamma.app import THREAD_TOKENS
    tokens = client.portal.call(lambda: anyio.to_thread.current_default_thread_limiter().total_tokens)
    assert tokens == THREAD_TOKENS > 40


# --- AI calls open at once, per account --------------------------------------------

@pytest.fixture(scope="module")
def ai_user():
    make_user("sl_ai", "sl-password-1")
    c = login("sl_ai", "sl-password-1")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-test-key-123",
                                          "models": "claude-solo"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def upstream(monkeypatch):
    """Every provider call answers '["ok"]', streamed or whole (faked below
    open_ai, so the cap runs for real)."""
    def fake_urlopen(req, timeout=None):
        if json.loads(req.data).get("stream"):
            return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": '["ok"]'}}])
        return io.BytesIO(json.dumps({"content": [{"type": "text", "text": '["ok"]'}],
                                      "usage": {"input_tokens": 1, "output_tokens": 1}}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _open(n):
    rt = ai_runtime("sl_ai")
    return [ai_client.open_ai([{"role": "user", "content": "hi"}], "", rt["default"], rt, stream=True)
            for _ in range(n)]


def _in_use():
    return ai_client._open_calls.get("sl_ai", 0)


def test_open_calls_are_capped_and_released(ai_user, upstream, monkeypatch):
    held = _open(ai_client.MAX_OPEN_CALLS)
    assert _in_use() == ai_client.MAX_OPEN_CALLS
    with pytest.raises(ai_client.TooManyCalls) as refused:
        _open(1)
    assert refused.value.status_code == 429 and "at once" in refused.value.detail
    assert ai_client.failure_kind(refused.value) == "rate"

    # over HTTP: the chat and the streamed translation are refused up front
    r = ai_user.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 429 and "at once" in r.json()["detail"]
    r = ai_user.post("/api/ai/translate", json={"texts": ["Hallo"], "lang": "en", "stream": True})
    assert r.status_code == 429 and "at once" in r.json()["detail"]

    # closing one frees its slot; a finished stream gives it back
    held.pop().close()
    r = ai_user.post("/api/ai/chat", json={"prompt": "hi", "stream": True})
    assert r.status_code == 200 and '"delta"' in r.text
    assert _in_use() == ai_client.MAX_OPEN_CALLS - 1
    for resp in held:
        with resp:  # the with-block closes it
            pass
    assert _in_use() == 0

    # a response dropped unread frees its slot when collected
    dropped = _open(2)
    assert _in_use() == 2
    del dropped
    gc.collect()
    assert _in_use() == 0

    # a call that fails to open never keeps one
    def refuse(req, timeout=None):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(urllib.error.URLError):
        _open(1)
    assert _in_use() == 0


def test_a_translation_past_the_cap_is_a_429_not_a_502(ai_user, upstream):
    held = _open(ai_client.MAX_OPEN_CALLS)
    try:
        r = ai_user.post("/api/ai/translate", json={"texts": ["Guten Tag"], "lang": "en"})
        assert r.status_code == 429
    finally:
        for resp in held:
            resp.close()
    r = ai_user.post("/api/ai/translate", json={"texts": ["Guten Tag"], "lang": "en"})
    assert r.status_code == 200 and r.json()["translations"] == ["ok"]
    assert _in_use() == 0


# --- gzip, JSON only -----------------------------------------------------------------

def _mini_app():
    big = {"items": ["x" * 40] * 200}

    def chunks():
        for i in range(3):
            yield json.dumps({"i": i, "pad": "y" * 800}) + "\n"
    return TestClient(JsonGzip(Starlette(routes=[
        Route("/big", lambda r: JSONResponse(big)),
        Route("/small", lambda r: JSONResponse({"ok": True})),
        Route("/partial", lambda r: JSONResponse(big, status_code=206)),
        Route("/stream-json", lambda r: StreamingResponse(chunks(), media_type="application/json")),
        Route("/ndjson", lambda r: StreamingResponse(chunks(), media_type="application/x-ndjson")),
        Route("/encoded", lambda r: JSONResponse(big, headers={"Content-Encoding": "identity"})),
    ])))


def test_gzip_only_whole_json_bodies():
    c = _mini_app()
    r = c.get("/big", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and "accept-encoding" in r.headers["vary"].lower()
    assert int(r.headers["content-length"]) < 2000 and r.json()["items"][0] == "x" * 40
    raw = c.get("/big", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers and len(raw.content) > 8000
    for path in ("/small", "/partial", "/stream-json", "/ndjson", "/encoded"):
        r = c.get(path, headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding", "identity") == "identity", path
    lines = c.get("/ndjson", headers={"Accept-Encoding": "gzip"}).text.splitlines()
    assert [json.loads(line)["i"] for line in lines] == [0, 1, 2]


def test_gzip_passes_streams_through_as_they_come():
    """Each piece of a stream (NDJSON, or JSON without a length) leaves as
    it arrives — nothing is held back to be compressed."""
    async def streaming_app(scope, receive, send):
        media = scope["path"].strip("/").replace("_", "/")
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", media.encode())]})
        for i in range(3):
            await send({"type": "http.response.body", "body": b"x" * 2000, "more_body": i < 2})

    async def run(path):
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        await JsonGzip(streaming_app)({"type": "http", "method": "GET", "path": path,
                                       "headers": [(b"accept-encoding", b"gzip")]}, receive, send)
        return sent

    for path in ("/application_x-ndjson", "/application_json"):
        sent = anyio.run(run, path)
        assert [m["type"] for m in sent] == ["http.response.start"] + ["http.response.body"] * 3
        assert all(m["body"] == b"x" * 2000 for m in sent[1:])
        assert b"content-encoding" not in dict(sent[0]["headers"])


def test_gzip_on_the_app(ai_user, upstream):
    make_user("sl_gzip", "sl-password-1")
    c = login("sl_gzip", "sl-password-1")
    page = make_page(c, "Big page")["id"]
    ops = [{"op": "insert", "id": f"gz{i}", "parent": page, "content": f"note {i} " + "lorem ipsum " * 20}
           for i in range(60)]
    assert c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": ops}).status_code == 200
    r = c.get(f"/api/blocks/{page}/subtree", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert len(r.json()["block"]["children"]) == 60
    assert "content-encoding" not in c.get("/api/health", headers={"Accept-Encoding": "gzip"}).headers

    # a JSON upload is a file: served whole or by range, never re-encoded
    data = json.dumps({"rows": ["z" * 50] * 200}).encode()
    url = c.post("/api/upload-file", files={"file": ("data.json", data, "application/json")}).json()["url"]
    r = c.get(url, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.content == data and "content-encoding" not in r.headers
    r = c.get(url, headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == data[:100] and "content-encoding" not in r.headers

    # an AI stream goes out as it comes, uncompressed
    r = ai_user.post("/api/ai/translate", json={"texts": ["Hallo"], "lang": "en", "stream": True},
                     headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert "content-encoding" not in r.headers
    assert json.loads(r.text.splitlines()[-1])["translations"] == ["ok"]


def test_gzip_level_is_cheap():
    from gamma import compression
    assert 1 <= compression.LEVEL <= 4 and compression.MIN_SIZE >= 1000
    assert gzip.decompress(JsonGzip(None)._compress(b"{}" * 1000)) == b"{}" * 1000


# --- the rate limiter ---------------------------------------------------------------

def test_client_address_ignores_x_forwarded_for(anon):
    """A spoofed X-Forwarded-For per request used to be a fresh address each
    time: the per-address login limit never tripped, and every value was a
    new counter kept forever."""
    codes = []
    for i in range(11):
        r = anon.post("/api/login", json={"username": f"sl-nobody-{i}", "password": "x"},
                      headers={"X-Forwarded-For": f"203.0.113.{i}"})
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10 and codes[10] == 429
    assert not any(k.startswith("login:ip:203.0.113.") for k in ratelimit._buckets)


def test_rate_limit_table_is_bounded(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 100)
    for i in range(1000):
        ratelimit.check(f"flood:{i}", 5, 3600)
    assert len(ratelimit._buckets) <= 100
    # expired windows go first, live ones stay
    ratelimit._buckets.clear()
    ratelimit.check("live", 5, 3600)
    for i in range(99):
        ratelimit.check(f"short:{i}", 5, 3600)
        ratelimit._buckets[f"short:{i}"][0] -= 7200  # its window is long over
    ratelimit.check("one-more", 5, 3600)
    assert "live" in ratelimit._buckets and not any(k.startswith("short:") for k in ratelimit._buckets)
    # a counter still counts across the sweep
    for _ in range(5):
        ratelimit.check("live", 7, 3600)
    with pytest.raises(Exception) as over:
        ratelimit.check("live", 6, 3600)
    assert getattr(over.value, "status_code", None) == 429


def test_docker_image_trusts_forwarded_headers_from_loopback_only():
    """uvicorn rewrites the client address from X-Forwarded-For only for the
    peers FORWARDED_ALLOW_IPS lists; the image's default is loopback only
    (uvicorn's own), so a deployment behind a proxy names the proxy. The
    demo and the share host do: each trusts exactly the subnet its Caddy
    connects from, pinned in its compose files."""
    root = Path(__file__).resolve().parents[2]
    value = re.search(r'FORWARDED_ALLOW_IPS="([^"]+)"', (root / "Dockerfile").read_text(encoding="utf-8")).group(1)
    nets = [ipaddress.ip_network(v) for v in value.split(",")]
    assert all(n.is_loopback for n in nets)
    assert not any(ipaddress.ip_address("172.18.0.5") in n for n in nets)
    assert "--proxy-headers" in (root / "docker-entrypoint.sh").read_text(encoding="utf-8")
    deploy = root / "cloud" / "deploy"
    compose = (deploy / "compose.yml").read_text(encoding="utf-8")
    share = re.search(r"FORWARDED_ALLOW_IPS: (\S+)", compose).group(1)
    assert f"- subnet: {share}" in compose  # the project network Caddy reaches the share host over
    assert "FORWARDED_ALLOW_IPS: ${EDGE_SUBNET" in (deploy / "demo" / "compose.yml").read_text(encoding="utf-8")
    edge = re.search(r"^EDGE_SUBNET=(\S+)", (deploy / "demo" / ".env.example").read_text(encoding="utf-8"),
                     re.M).group(1)
    assert f"--subnet {edge} gamma-edge" in (deploy / "demo" / "README.md").read_text(encoding="utf-8")
