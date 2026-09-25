"""/api/ai/translate: validation, the paragraph cache, and the JSON wire."""

import json

import bcrypt
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def carol(client):
    """A non-guest user with an Anthropic provider entry (translate needs one).

    The account name is file-unique: other test modules make their own 'carol'
    and the worker's data dir is shared across the files it runs.
    """
    from gamma.app import app
    from gamma.db import connect_users_db, page_now
    from gamma import workspaces

    with connect_users_db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE username = 'translate_carol'").fetchone():
            conn.execute(
                "INSERT INTO users (username, password_hash, is_guest, created_at) VALUES (?, ?, 0, ?)",
                ("translate_carol", bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(), page_now()),
            )
            conn.commit()
    workspaces.ensure_personal("translate_carol")
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "translate_carol", "password": "pw"})
    assert r.status_code == 200, r.text
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-ant-key-1234",
                                          "models": "claude-test"})
    assert r.status_code == 200, r.text
    return c


def _fake_call(replies):
    """A _call_ai stand-in: records each batch it was sent, answers from
    `replies` (a callable on the batch) as the model's JSON-array reply."""
    calls = []

    def fake(messages, system, entry, rt, **kw):
        batch = json.loads(messages[-1]["content"])
        calls.append(batch)
        return json.dumps(replies(batch), ensure_ascii=False)

    return fake, calls


def test_translate_requires_a_provider():
    # Own TestClient: logging the shared session client in as guest would
    # leak that cookie into later test modules.
    from gamma.app import app

    c = TestClient(app)
    assert c.post("/api/login-guest").status_code == 200
    r = c.post("/api/ai/translate", json={"texts": ["hello"], "lang": "zh-CN"})
    assert r.status_code == 503


def test_translate_validation(carol):
    assert carol.post("/api/ai/translate", json={"texts": ["hi"], "lang": "klingon"}).status_code == 400
    assert carol.post("/api/ai/translate", json={"texts": [], "lang": "zh-CN"}).status_code == 400
    assert carol.post("/api/ai/translate", json={"texts": [1, 2], "lang": "zh-CN"}).status_code == 400
    assert carol.post("/api/ai/translate",
                      json={"texts": ["x" * 40000, "y" * 40000], "lang": "zh-CN"}).status_code == 413


def test_translate_caches_per_paragraph(carol, monkeypatch):
    fake, calls = _fake_call(lambda batch: [f"译:{t}" for t in batch])
    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)

    texts = ["The quick brown fox.", "  ", "Jumps over the lazy dog."]
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Whitespace-only paragraphs never reach the model and come back verbatim.
    assert body["translations"] == ["译:The quick brown fox.", "  ", "译:Jumps over the lazy dog."]
    assert calls == [["The quick brown fox.", "Jumps over the lazy dog."]]
    assert body["cached"] is False

    # Same request again: fully served from the cache, no upstream call.
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"})
    assert r.status_code == 200
    assert r.json()["translations"][0] == "译:The quick brown fox."
    assert r.json()["cached"] is True
    assert len(calls) == 1

    # One new paragraph: only the miss goes upstream.
    r = carol.post("/api/ai/translate",
                   json={"texts": ["The quick brown fox.", "A new sentence."], "lang": "zh-CN"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["译:The quick brown fox.", "译:A new sentence."]
    assert calls[-1] == ["A new sentence."]

    # A different target language is a different cache line.
    r = carol.post("/api/ai/translate", json={"texts": ["The quick brown fox."], "lang": "ja"})
    assert r.status_code == 200
    assert calls[-1] == ["The quick brown fox."]


def test_translate_dedupes_within_request(carol, monkeypatch):
    # The same paragraph appearing twice (running headers, repeated captions)
    # goes upstream once; the one translation fills both slots.
    fake, calls = _fake_call(lambda batch: [f"译:{t}" for t in batch])
    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["a twin paragraph", "a twin paragraph"], "lang": "it"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["译:a twin paragraph", "译:a twin paragraph"]
    assert calls == [["a twin paragraph"]]


def test_translate_forwards_effort(carol, monkeypatch):
    seen = {}

    def fake(messages, system, entry, rt, **kw):
        seen.update(kw)
        return json.dumps([f"x:{t}" for t in json.loads(messages[-1]["content"])])

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["an effort-test paragraph"], "lang": "fr", "effort": "low"})
    assert r.status_code == 200
    assert seen.get("effort") == "low"
    # Unknown values degrade to "" (parameter omitted), not an error.
    r = carol.post("/api/ai/translate",
                   json={"texts": ["a second effort-test paragraph"], "lang": "fr", "effort": "turbo"})
    assert r.status_code == 200
    assert seen.get("effort") == ""


def test_translate_tolerates_fenced_reply(carol, monkeypatch):
    monkeypatch.setattr("gamma.routers.ai._call_ai",
                        lambda *a, **k: 'Sure!\n```json\n["uno"]\n```')
    r = carol.post("/api/ai/translate", json={"texts": ["one fresh paragraph"], "lang": "es"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["uno"]


def test_translate_salvages_miscounted_batch(carol, monkeypatch):
    # The model merges two paragraphs into one array element (the classic
    # "expected 5, got 4") — the endpoint retries per paragraph, where a
    # 1-element array can't misalign.
    def fake(messages, system, entry, rt, **kw):
        batch = json.loads(messages[-1]["content"])
        if len(batch) > 1:
            return json.dumps(["merged!"])  # wrong length
        return json.dumps([f"ok:{batch[0]}"])

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["salvage paragraph one", "salvage paragraph two"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["ok:salvage paragraph one", "ok:salvage paragraph two"]


def test_translate_unparseable_reply_degrades_to_original(carol, monkeypatch):
    # Provider reachable but talking garbage: paragraphs come back verbatim
    # (the viewer shows the original) and are NOT cached, so a later retry
    # can still improve them.
    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda *a, **k: "no array here")
    r = carol.post("/api/ai/translate", json={"texts": ["another fresh paragraph"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["another fresh paragraph"]

    def good(messages, system, entry, rt, **kw):
        return json.dumps([f"besser:{t}" for t in json.loads(messages[-1]["content"])])

    monkeypatch.setattr("gamma.routers.ai._call_ai", good)
    r = carol.post("/api/ai/translate", json={"texts": ["another fresh paragraph"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["besser:another fresh paragraph"]


class _StreamResp:
    """An Anthropic SSE reply whose text arrives in the given pieces."""
    def __init__(self, pieces):
        self._lines = [("data: " + json.dumps({"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": t}})
                        + "\n").encode() for t in pieces] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


def test_translate_streams_partials(carol, monkeypatch):
    """stream: true answers NDJSON — a partial line per paragraph as the
    model writes it (every request index sharing that source text), then the
    same final object a plain call returns; the cache is filled the same way."""
    import gamma.routers.ai as ai_mod
    monkeypatch.setattr(ai_mod, "_TRANSLATE_STREAM_INTERVAL", 0)
    opened = []

    def fake_open(messages, system, entry, rt, **kw):
        opened.append(json.loads(messages[-1]["content"]))
        assert kw.get("stream") is True
        return _StreamResp(['```json\n["第一', '段", "第', '二段"]\n```'])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = carol.post("/api/ai/translate", json={
        "texts": ["stream one", "stream two", "stream one"], "lang": "zh-CN", "stream": True})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert opened == [["stream one", "stream two"]]  # duplicate collapsed upstream
    assert lines[-1] == {"translations": ["第一段", "第二段", "第一段"],
                         "model": lines[-1]["model"], "cached": False}
    partials = lines[:-1]
    assert partials[0] == {"i": [0, 2], "text": "第一"}  # both slots of the shared source
    assert {"i": [0, 2], "text": "第一段"} in partials
    assert {"i": [1], "text": "第"} in partials
    # Cached now: the stream is just the final line, nothing goes upstream.
    r = carol.post("/api/ai/translate", json={
        "texts": ["stream two", "stream one"], "lang": "zh-CN", "stream": True})
    assert r.status_code == 200
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert lines == [{"translations": ["第二段", "第一段"], "model": lines[0]["model"], "cached": True}]
    assert len(opened) == 1


def test_translate_stream_reports_upstream_failure_in_band(carol, monkeypatch):
    import gamma.routers.ai as ai_mod

    def boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ai_mod, "_open_ai", boom)
    r = carol.post("/api/ai/translate", json={"texts": ["fails"], "lang": "de", "stream": True})
    assert r.status_code == 200
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert lines == [{"error": "translation failed: provider down"}]


# --- machine-translation engines (Google Cloud Translation, Youdao) ----------

class _FakeResp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(reply):
    """A urlopen stand-in for gamma.translate_engines: records each request,
    answers `reply(req)` as the JSON body."""
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return _FakeResp(reply(req))

    return fake, calls


@pytest.fixture(scope="module")
def dave(client):
    """An account with NO AI provider — the engine path must not need one."""
    from gamma.app import app
    from gamma.db import connect_users_db, page_now
    from gamma import workspaces

    with connect_users_db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE username = 'translate_dave'").fetchone():
            conn.execute(
                "INSERT INTO users (username, password_hash, is_guest, created_at) VALUES (?, ?, 0, ?)",
                ("translate_dave", bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(), page_now()),
            )
            conn.commit()
    workspaces.ensure_personal("translate_dave")
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "translate_dave", "password": "pw"}).status_code == 200
    return c


def test_engine_settings_are_masked_and_reserved(dave):
    body = dave.get("/api/translate/engines").json()
    assert body["can_edit"] is True
    assert {e["id"]: e["configured"] for e in body["engines"]} == {"google": False, "youdao": False}

    assert dave.put("/api/translate/engines/google", json={"fields": {}}).status_code == 400
    assert dave.put("/api/translate/engines/bing", json={"fields": {"api_key": "x"}}).status_code == 404
    r = dave.put("/api/translate/engines/google", json={"fields": {"api_key": "AIza-secret-key-9876"}})
    assert r.status_code == 200, r.text
    google = next(e for e in r.json()["engines"] if e["id"] == "google")
    assert google["configured"] is True
    assert google["fields"]["api_key"] == "…9876"
    assert "AIza-secret-key-9876" not in r.text

    # An empty secret on edit keeps the stored one; the plain app key shows.
    r = dave.put("/api/translate/engines/youdao", json={"fields": {"app_key": "app-1", "app_secret": "s3cret-value-1234"}})
    assert r.status_code == 200
    r = dave.put("/api/translate/engines/youdao", json={"fields": {"app_key": "app-2", "app_secret": ""}})
    youdao = next(e for e in r.json()["engines"] if e["id"] == "youdao")
    assert youdao["configured"] and youdao["fields"] == {"app_key": "app-2", "app_secret": "…1234"}

    # The raw key never leaves through the generic prefs endpoints.
    assert dave.get("/api/prefs/translate-engines").status_code == 400
    assert dave.put("/api/prefs/translate-engines", json={"value": {}}).status_code == 400

    models = dave.get("/api/ai/models").json()
    assert [e["id"] for e in models["translate_engines"]] == ["engine:google", "engine:youdao"]

    r = dave.delete("/api/translate/engines/youdao")
    assert [e["id"] for e in r.json()["engines"] if e["configured"]] == ["google"]


def test_guest_cannot_store_engine_keys():
    from gamma.app import app

    c = TestClient(app)
    assert c.post("/api/login-guest").status_code == 200
    assert c.get("/api/translate/engines").json()["can_edit"] is False
    assert c.put("/api/translate/engines/google", json={"fields": {"api_key": "k" * 20}}).status_code == 403


def test_translate_with_google(dave, monkeypatch):
    dave.put("/api/translate/engines/google", json={"fields": {"api_key": "AIza-secret-key-9876"}})
    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda *a, **k: pytest.fail("no LLM on the engine path"))
    fake, calls = _fake_urlopen(lambda req: {"data": {"translations": [
        {"translatedText": f"G:{t}"} for t in json.loads(req.data)["q"]]}})
    monkeypatch.setattr("gamma.translate_engines.urlopen", fake)

    texts = ["Google one.", "  ", "Google two.", "Google one."]
    r = dave.post("/api/ai/translate", json={"texts": texts, "lang": "zh-TW", "model": "engine:google"})
    assert r.status_code == 200, r.text
    assert r.json() == {"translations": ["G:Google one.", "  ", "G:Google two.", "G:Google one."],
                        "model": "engine:google", "cached": False}
    (req,) = calls
    assert req.get_header("X-goog-api-key") == "AIza-secret-key-9876"
    assert "key=" not in req.full_url
    assert json.loads(req.data) == {"q": ["Google one.", "Google two."], "target": "zh-TW", "format": "text"}

    # Cached per engine; the streamed form answers with the final line.
    r = dave.post("/api/ai/translate", json={"texts": ["Google two."], "lang": "zh-TW",
                                             "model": "engine:google", "stream": True})
    assert [json.loads(line) for line in r.text.splitlines() if line.strip()] == [
        {"translations": ["G:Google two."], "model": "engine:google", "cached": True}]
    assert len(calls) == 1


def test_translate_with_engine_errors(dave, monkeypatch):
    import io
    from urllib.error import HTTPError

    # Not set up: 503, like a missing AI provider.
    dave.delete("/api/translate/engines/youdao")
    r = dave.post("/api/ai/translate", json={"texts": ["x y"], "lang": "de", "model": "engine:youdao"})
    assert r.status_code == 503

    dave.put("/api/translate/engines/google", json={"fields": {"api_key": "AIza-secret-key-9876"}})

    def denied(req, timeout=None):
        raise HTTPError(req.full_url, 403, "Forbidden", {},
                        io.BytesIO(json.dumps({"error": {"message": "API key not valid"}}).encode()))

    monkeypatch.setattr("gamma.translate_engines.urlopen", denied)
    r = dave.post("/api/ai/translate", json={"texts": ["An uncached line."], "lang": "de", "model": "engine:google"})
    assert r.status_code == 502
    assert "API key not valid" in r.json()["detail"]
    r = dave.post("/api/translate/engines/google/test", json={"lang": "de"})
    assert r.json() == {"ok": False, "error": "Google: HTTP 403 — API key not valid"}


def test_translate_with_youdao(dave, monkeypatch):
    from urllib.parse import parse_qs
    from gamma import translate_engines

    dave.put("/api/translate/engines/youdao", json={"fields": {"app_key": "app-1", "app_secret": "sec-1"}})

    def reply(req):
        form = parse_qs(req.data.decode())
        qs = form["q"]
        assert form["to"] == ["zh-CHS"] and form["from"] == ["auto"] and form["signType"] == ["v3"]
        assert form["sign"] == [translate_engines.youdao_sign(
            "app-1", "sec-1", qs, form["salt"][0], form["curtime"][0])]
        # The second query failed upstream: listed in errorIndex, absent from the results.
        return {"errorCode": "0", "errorIndex": [1],
                "translateResults": [{"query": q, "translation": f"Y:{q}"} for j, q in enumerate(qs) if j != 1]}

    fake, calls = _fake_urlopen(reply)
    monkeypatch.setattr("gamma.translate_engines.urlopen", fake)
    r = dave.post("/api/ai/translate", json={"texts": ["Youdao a.", "Youdao b.", "Youdao c."],
                                             "lang": "zh-CN", "model": "engine:youdao"})
    assert r.status_code == 200, r.text
    # The failed one comes back verbatim (and stays uncached for a retry).
    assert r.json()["translations"] == ["Y:Youdao a.", "Youdao b.", "Y:Youdao c."]

    monkeypatch.setattr("gamma.translate_engines.urlopen",
                        _fake_urlopen(lambda req: {"errorCode": "202"})[0])
    r = dave.post("/api/translate/engines/youdao/test", json={"lang": "zh-CN"})
    assert r.json()["ok"] is False and "signature" in r.json()["error"]


def test_youdao_sign_shortens_long_input():
    import hashlib
    from gamma.translate_engines import youdao_sign

    assert youdao_sign("k", "s", ["hello"], "salt", "1") == hashlib.sha256(b"khellosalt1s").hexdigest()
    texts = ["abcdefghijKLMN", "OPQRSTUVWXyz0123456789"]  # 36 chars joined
    assert youdao_sign("k", "s", texts, "salt", "1") == hashlib.sha256(b"kabcdefghij360123456789salt1s").hexdigest()


def test_engine_batches_split_on_limits():
    from gamma.translate_engines import _batches

    assert list(_batches(["a" * 3, "b" * 3, "c" * 3], 10, 6)) == [["aaa", "bbb"], ["ccc"]]
    assert list(_batches(["x"] * 5, 2, 100)) == [["x", "x"], ["x", "x"], ["x"]]
    assert list(_batches(["y" * 50], 2, 10)) == [["y" * 50]]  # an oversize text goes alone
