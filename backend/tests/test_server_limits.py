"""Server-wide limits: the worker-thread pool sized for slow outbound work,
the per-account cap on AI calls open at once, gzip for JSON answers only,
and the rate limiter's bounded memory and client address."""

import gc
import gzip
import http.client
import io
import ipaddress
import json
import re
import threading
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
from conftest import account_of, login, make_page, make_user

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
    """Every provider call answers "<s1>ok</s1>" (one translated segment),
    streamed or whole (faked below open_ai, so the cap runs for real)."""
    def fake_urlopen(req, timeout=None):
        if json.loads(req.data).get("stream"):
            return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "<s1>ok</s1>"}}])
        return _reply("<s1>ok</s1>")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _reply(text):
    """A whole Anthropic reply answering ``text``."""
    return io.BytesIO(json.dumps({"content": [{"type": "text", "text": text}],
                                  "usage": {"input_tokens": 1, "output_tokens": 1}}).encode())


def _open(n):
    rt = ai_runtime(account_of("sl_ai"))
    return [ai_client.open_ai([{"role": "user", "content": "hi"}], "", rt["default"], rt, stream=True)
            for _ in range(n)]


def _in_use():
    return ai_client._open_calls.get(account_of("sl_ai"), 0)


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
    assert r.headers["retry-after"] == str(ai_client.CALL_RETRY_AFTER)  # the translator waits that long

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


def test_a_slot_may_be_waited_for_and_held_across_calls(ai_user, upstream):
    held = _open(ai_client.MAX_OPEN_CALLS)
    rt = ai_runtime(account_of("sl_ai"))
    hi = [{"role": "user", "content": "hi"}]
    in_use = []

    def wait_then_call_twice():
        with ai_client.call_slot(account_of("sl_ai"), wait=30) as slot:
            for _ in range(2):
                ai_client.call_ai(hi, "", rt["default"], rt, slot=slot)
                in_use.append(_in_use())

    waiter = threading.Thread(target=wait_then_call_twice)
    waiter.start()
    try:
        waiter.join(0.3)
        assert waiter.is_alive() and not in_use, "every slot is taken: it waits"
        held.pop().close()
        waiter.join(5)
        # The freed slot went to it, and both calls ran under it: neither
        # took another (there was none to take) nor gave it back.
        assert in_use == [ai_client.MAX_OPEN_CALLS] * 2
        assert _in_use() == ai_client.MAX_OPEN_CALLS - 1, "the block's end frees it"
        held += _open(1)
        # a wait that runs out is refused, like a call that may not wait
        with pytest.raises(ai_client.TooManyCalls):
            with ai_client.call_slot(account_of("sl_ai"), wait=0.2):
                pass
    finally:
        for resp in held:
            resp.close()
    assert _in_use() == 0


def test_a_translation_asks_again_under_the_slot_it_holds(ai_user, monkeypatch):
    # The account's last slot free, and two texts whose reply leaves the
    # second out: it is asked again under the request's own slot, not a
    # second one (there is none), and comes back translated.
    in_use = []

    def upstream(req, timeout=None):
        in_use.append(_in_use())
        return _reply("<s1>ok</s1>")

    monkeypatch.setattr(urllib.request, "urlopen", upstream)
    held = _open(ai_client.MAX_OPEN_CALLS - 1)
    in_use.clear()
    try:
        r = ai_user.post("/api/ai/translate", json={"texts": ["Erster Absatz", "Zweiter Absatz"], "lang": "en"})
    finally:
        for resp in held:
            resp.close()
    assert r.status_code == 200
    assert r.json()["translations"] == ["ok", "ok"] and r.json()["failed"] == [] and r.json()["redone"] == 1
    assert in_use == [ai_client.MAX_OPEN_CALLS] * 2
    assert _in_use() == 0


def test_a_streamed_chunk_whose_client_left_while_it_waited_for_a_slot_opens_no_call(ai_user, upstream, monkeypatch):
    # Past the door, every slot taken: the chunk's stream waits for one.
    # The viewer aborts the chunk meanwhile (translation off, the PDF
    # closed), and when a slot frees, the stream lets it go without paying
    # for a provider call for no one.
    from contextlib import contextmanager
    from functools import partial

    from gamma.routers import translate as translate_mod

    opened, let_go = [], threading.Event()

    @contextmanager
    def watched_slot(*a, **kw):
        try:
            with ai_client.call_slot(*a, **kw) as slot:
                yield slot
        finally:
            let_go.set()

    monkeypatch.setattr(translate_mod, "_call_slot", watched_slot)
    monkeypatch.setattr(translate_mod, "_open_ai", lambda *a, **kw: opened.append(kw) or FakeResp([]))
    monkeypatch.setattr(translate_mod, "require_user_id", lambda request: account_of("sl_ai"))
    monkeypatch.setattr(translate_mod, "keepalive_lines", partial(translate_mod.keepalive_lines, interval=0.05))
    held = _open(ai_client.MAX_OPEN_CALLS - 1)
    runner = None
    try:
        response = translate_mod.ai_translate(
            translate_mod.AITranslateRequest(texts=["Verlassen"], lang="en", stream=True), None)
        held += _open(1)

        async def receive():
            await anyio.sleep(0.2)
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        runner = threading.Thread(target=anyio.run, args=(
            response, {"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send))
        runner.start()
        assert response.gone.wait(2), "the client's leaving is heard"
        assert not let_go.is_set(), "it waits for a slot"
        held.pop().close()
        assert let_go.wait(5)
        assert opened == []
        assert _in_use() == ai_client.MAX_OPEN_CALLS - 1, "the slot it took is free again"
    finally:
        for resp in held:
            resp.close()
        if runner:
            runner.join(10)
    assert _in_use() == 0


# --- a call that failed before its reply, tried again ---------------------------------

@pytest.fixture
def waits(monkeypatch):
    """The waits between attempts, recorded instead of slept."""
    seen = []
    monkeypatch.setattr(ai_client, "_sleep", seen.append)
    return seen


def _http_error(code, headers=None):
    return urllib.error.HTTPError("https://api.example/v1/messages", code, "error", headers or {},
                                  io.BytesIO(json.dumps({"error": {"message": f"status {code}"}}).encode()))


def _failing_then(monkeypatch, failures, answer="<s1>ok</s1>"):
    """urlopen raising each of ``failures`` in turn, then answering; returns
    the account's slots in use at each attempt."""
    attempts, left = [], list(failures)

    def upstream(req, timeout=None):
        attempts.append(_in_use())
        if left:
            raise left.pop(0)
        if json.loads(req.data).get("stream"):
            return FakeResp([{"type": "content_block_delta", "delta": {"type": "text_delta", "text": answer}}])
        return _reply(answer)

    monkeypatch.setattr(urllib.request, "urlopen", upstream)
    return attempts


def test_a_translation_whose_connection_failed_is_tried_again_under_its_one_slot(ai_user, monkeypatch, waits):
    # The connect to the provider timed out (as Windows' 21 s one did in the
    # baseline): asked again a second later, under the slot it holds.
    attempts = _failing_then(monkeypatch, [urllib.error.URLError(TimeoutError("timed out"))])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Erneut verbunden"], "lang": "en"})
    assert r.status_code == 200 and r.json()["translations"] == ["ok"]
    assert attempts == [1, 1] and waits == [1]
    assert _in_use() == 0

    # Streamed, a provider that hung up before answering is tried again too.
    attempts = _failing_then(monkeypatch, [ConnectionResetError("reset by peer")])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Erneut gestreamt"], "lang": "en", "stream": True})
    assert json.loads(r.text.splitlines()[-1])["translations"] == ["ok"]
    assert attempts == [1, 1] and waits == [1, 1]
    assert _in_use() == 0


def test_a_rejected_request_is_not_tried_again(ai_user, monkeypatch, waits):
    attempts = _failing_then(monkeypatch, [_http_error(400)])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Abgelehnt"], "lang": "en"})
    assert r.status_code == 502 and "upstream 400" in r.json()["detail"]
    assert len(attempts) == 1 and waits == []
    assert _in_use() == 0


def test_a_busy_provider_is_asked_again_after_its_retry_after(ai_user, monkeypatch, waits):
    attempts = _failing_then(monkeypatch, [_http_error(503, {"Retry-After": "3"})])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Später bitte"], "lang": "en"})
    assert r.status_code == 200 and len(attempts) == 2 and waits == [3]

    # A Retry-After past what a chunk may wait is a limit for a while: not
    # tried again.
    attempts = _failing_then(monkeypatch, [_http_error(429, {"Retry-After": "600"})])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Viel später"], "lang": "en"})
    assert r.status_code == 502 and len(attempts) == 1 and waits == [3]
    assert _in_use() == 0


def test_tries_stop_after_the_retries_and_a_chat_call_has_none(ai_user, monkeypatch, waits):
    attempts = _failing_then(monkeypatch, [_http_error(502) for _ in range(5)])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Nie erreicht"], "lang": "en"})
    assert r.status_code == 502 and "upstream 502" in r.json()["detail"]
    assert len(attempts) == 3 and waits == [1, 4]
    assert _in_use() == 0

    # A call that asks for no retries (the chat's) fails at the first.
    attempts = _failing_then(monkeypatch, [urllib.error.URLError("no route")])
    rt = ai_runtime(account_of("sl_ai"))
    with pytest.raises(urllib.error.URLError):
        ai_client.call_ai([{"role": "user", "content": "hi"}], "", rt["default"], rt)
    assert len(attempts) == 1 and waits == [1, 4]
    assert _in_use() == 0



def test_a_whole_reply_slow_to_come_is_not_asked_for_again(ai_user, monkeypatch, waits):
    # A request sent whole and left unanswered (urllib's bare TimeoutError
    # from waiting for the status line, a hang-up, a gateway's 504): the
    # provider is most likely still making the reply, and billing it, so a
    # whole reply is not asked for again.
    for failure in (TimeoutError("timed out"), http.client.RemoteDisconnected("closed"), _http_error(504)):
        attempts = _failing_then(monkeypatch, [failure])
        r = ai_user.post("/api/ai/translate", json={"texts": ["Langsam gedacht"], "lang": "en"})
        assert r.status_code != 200, failure
        assert len(attempts) == 1 and waits == [], failure
        assert _in_use() == 0

    # Streamed, the headers come before the model begins: a request left
    # unanswered never got to it, and is tried again.
    attempts = _failing_then(monkeypatch, [TimeoutError("timed out")])
    r = ai_user.post("/api/ai/translate", json={"texts": ["Langsam gestreamt"], "lang": "en", "stream": True})
    assert json.loads(r.text.splitlines()[-1])["translations"] == ["ok"]
    assert attempts == [1, 1] and waits == [1]
    assert _in_use() == 0


_BRIEF_REPLY = json.dumps({"field": "optics", "summary": "s", "notation": [],
                           "terms": [{"source": "waveguide", "target": "Wellenleiter"}], "keep": []})


def _brief_lines(c, head):
    r = c.post("/api/ai/translate/brief", json={"lang": "en", "head": head})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip() and "ping" not in line]


def test_the_brief_is_tried_again_only_while_its_chunks_wait_for_it(ai_user, monkeypatch, waits):
    # A brief whose connect failed is asked again under its one slot: the
    # job's chunks get it, not a translation without it.
    attempts = _failing_then(monkeypatch, [urllib.error.URLError(TimeoutError("timed out"))], _BRIEF_REPLY)
    assert _brief_lines(ai_user, "Ein Wellenleiter, einmal verbunden.")[-1] == {"ready": True}
    assert attempts == [1, 1] and waits == [1]
    assert _in_use() == 0

    # Each try taking the brief's whole timeout (a blackholed connect where
    # the socket timeout covers it): another would end after the chunks
    # have stopped waiting for the brief (_BRIEF_WAIT), so none is begun.
    now = [0.0]
    attempts = []

    def slow(req, timeout=None):
        attempts.append(_in_use())
        now[0] += timeout
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(ai_client, "_clock", lambda: now[0])
    monkeypatch.setattr(urllib.request, "urlopen", slow)
    last = _brief_lines(ai_user, "Ein Wellenleiter, nie erreicht.")[-1]
    assert last["ready"] is False and last.get("error")
    assert attempts == [1] and waits == [1]
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
