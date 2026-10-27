"""/api/ai/translate: validation, the paragraph cache, and the JSON wire."""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def carol(client):
    """A non-guest user with an Anthropic provider entry (translate needs one).

    The account name is file-unique: other test modules make their own 'carol'
    and the worker's data dir is shared across the files it runs.
    """
    from conftest import login, make_user

    make_user("translate_carol", "pw")
    c = login("translate_carol", "pw")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-ant-key-1234",
                                          "models": "claude-test"})
    assert r.status_code == 200, r.text
    return c


def texts_of(messages):
    """The texts a translation call's user turn carries (a JSON object:
    texts plus their context, translate_context.user_turn)."""
    return json.loads(messages[-1]["content"])["texts"]


def _fake_call(replies):
    """A _call_ai stand-in: records each batch it was sent, answers from
    `replies` (a callable on the batch) as the model's JSON-array reply."""
    calls = []

    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
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
        return json.dumps([f"x:{t}" for t in texts_of(messages)])

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


@pytest.mark.parametrize("requested,tiers,expected", [
    ("fast", ["flex", "fast"], "fast"),
    ("flex", ["flex", "fast"], "flex"),
    (" FAST ", ["fast"], "fast"),
    ("flex", ["fast"], ""),
    ("fast", [], ""),
    ("fast", None, ""),
    ("turbo", ["fast"], ""),
    ("", ["fast"], ""),
])
def test_translate_speed_uses_only_a_supported_tier(carol, monkeypatch, requested, tiers, expected):
    seen = []

    def fake(messages, system, entry, rt, **kw):
        seen.append(kw["speed"])
        return json.dumps([f"T:{text}" for text in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: (tiers, "provider"))
    r = carol.post("/api/ai/translate", json={
        "texts": [f"speed {requested!r} with {tiers!r}"], "lang": "fr", "speed": requested})
    assert r.status_code == 200, r.text
    assert seen == [expected]


def test_translate_speed_lookup_failure_uses_default(carol, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("catalog unavailable")

    def fake(*args, **kw):
        assert kw["speed"] == ""
        return '["traduit"]'

    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", unavailable)
    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate", json={
        "texts": ["speed catalog failure"], "lang": "fr", "speed": "fast"})
    assert r.status_code == 200, r.text


def test_translate_prompt_names_the_language_and_the_break_marker(carol, monkeypatch):
    seen = {}

    def fake(messages, system, entry, rt, **kw):
        seen["system"] = system
        return json.dumps(texts_of(messages))

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["a column-break {br} paragraph for the prompt test"], "lang": "ja"})
    assert r.status_code == 200
    assert "into Japanese" in seen["system"]
    assert "{br}" in seen["system"] and "{lang}" not in seen["system"]


def test_translate_sends_the_context_and_salvages_without_the_neighbours(carol, monkeypatch):
    bodies = []
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: (["fast"], "provider"))

    def fake(messages, system, entry, rt, **kw):
        body = json.loads(messages[-1]["content"])
        bodies.append(body)
        assert kw["speed"] == "fast"  # the original call and every repair
        # The batch comes back one element short: salvaged per paragraph.
        return json.dumps([f"c:{t}" for t in body["texts"]][: max(1, len(body["texts"]) - 1)])

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate", json={
        "texts": ["a context-test paragraph", "its context-test neighbour"], "lang": "ko", "speed": "fast",
        "before": "x" * 2000 + " the end of the paragraph before", "after": "the start of the one after",
        "section": "  II.   MODEL  "})
    assert r.status_code == 200
    first, *salvaged = bodies
    assert first["section"] == "II. MODEL"
    assert first["before"].endswith("the end of the paragraph before") and len(first["before"]) == 1500
    assert first["after"] == "the start of the one after"
    assert len(salvaged) == 2
    assert all(b["section"] == "II. MODEL" and "before" not in b and "after" not in b for b in salvaged)
    assert r.json()["translations"] == ["c:a context-test paragraph", "c:its context-test neighbour"]


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
        batch = texts_of(messages)
        if len(batch) > 1:
            return json.dumps(["merged!"])  # wrong length
        return json.dumps([f"ok:{batch[0]}"])

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["salvage paragraph one", "salvage paragraph two"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["ok:salvage paragraph one", "ok:salvage paragraph two"]


def test_translate_unparseable_reply_degrades_to_original(carol, monkeypatch):
    # Provider reachable but answering no translation: paragraphs come back
    # verbatim (the viewer shows the original), named as failed so the
    # viewer asks for them again, and are NOT cached, so that retry can
    # still improve them.
    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda *a, **k: '{"note": "no array here"}')
    r = carol.post("/api/ai/translate", json={"texts": ["another fresh paragraph"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["another fresh paragraph"]
    assert r.json()["failed"] == [0]

    def good(messages, system, entry, rt, **kw):
        return json.dumps([f"besser:{t}" for t in texts_of(messages)])

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
    monkeypatch.setattr(ai_mod.ai_catalog, "speed_tiers", lambda *a: (["fast"], "provider"))
    opened = []

    def fake_open(messages, system, entry, rt, **kw):
        opened.append(texts_of(messages))
        assert kw.get("stream") is True
        assert kw["speed"] == "fast"
        return _StreamResp(['```json\n["第一', '段", "第', '二段"]\n```'])

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    r = carol.post("/api/ai/translate", json={
        "texts": ["stream one", "stream two", "stream one"], "lang": "zh-CN", "stream": True, "speed": "fast"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert opened == [["stream one", "stream two"]]  # duplicate collapsed upstream
    assert lines[-1] == {"translations": ["第一段", "第二段", "第一段"],
                         "model": lines[-1]["model"], "cached": False, "redone": 0, "failed": [], "effort": ""}
    partials = lines[:-1]
    assert partials[0] == {"i": [0, 2], "text": "第一"}  # both slots of the shared source
    assert {"i": [0, 2], "text": "第一段"} in partials
    assert {"i": [1], "text": "第"} in partials
    # Cached now: the stream is just the final line, nothing goes upstream.
    r = carol.post("/api/ai/translate", json={
        "texts": ["stream two", "stream one"], "lang": "zh-CN", "stream": True})
    assert r.status_code == 200
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert lines == [{"translations": ["第二段", "第一段"], "model": lines[0]["model"], "cached": True, "redone": 0,
                      "failed": [], "effort": ""}]
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
    from conftest import login, make_user

    make_user("translate_dave", "pw")
    c = login("translate_dave", "pw")
    return c


def test_engine_settings_are_masked_and_reserved(dave):
    body = dave.get("/api/translate/engines").json()
    assert body["can_edit"] is True
    # Microsoft needs no key: always ready, nothing to store.
    assert {e["id"]: e["configured"] for e in body["engines"]} == {"microsoft": True, "google": False, "youdao": False}
    assert [e["id"] for e in body["engines"] if not e["needs_key"]] == ["microsoft"]
    assert dave.put("/api/translate/engines/microsoft", json={"fields": {}}).status_code == 400

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
    assert [e["id"] for e in models["translate_engines"]] == ["engine:microsoft", "engine:google", "engine:youdao"]

    r = dave.delete("/api/translate/engines/youdao")
    assert [e["id"] for e in r.json()["engines"] if e["configured"]] == ["microsoft", "google"]


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
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: pytest.fail("engines have no speed tier"))
    r = dave.post("/api/ai/translate", json={"texts": texts, "lang": "zh-TW", "model": "engine:google", "speed": "fast"})
    assert r.status_code == 200, r.text
    assert r.json() == {"translations": ["G:Google one.", "  ", "G:Google two.", "G:Google one."],
                        "model": "engine:google", "cached": False, "redone": 0, "failed": []}
    (req,) = calls
    assert req.get_header("X-goog-api-key") == "AIza-secret-key-9876"
    assert "key=" not in req.full_url
    assert json.loads(req.data) == {"q": ["Google one.", "Google two."], "target": "zh-TW", "format": "text"}

    # Cached per engine; the streamed form answers with the final line.
    r = dave.post("/api/ai/translate", json={"texts": ["Google two."], "lang": "zh-TW",
                                             "model": "engine:google", "stream": True})
    assert [json.loads(line) for line in r.text.splitlines() if line.strip()] == [
        {"translations": ["G:Google two."], "model": "engine:google", "cached": True, "redone": 0, "failed": []}]
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


def test_translate_with_microsoft(dave, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda *a, **k: pytest.fail("no LLM on the engine path"))

    def reply(req):
        texts = json.loads(req.data)
        # Translator v3's shape; the second text comes back without a translation.
        return [{"detectedLanguage": {"language": "en", "score": 1.0},
                 "translations": [{"text": f"M:{t}", "to": "zh-Hant"}] if j != 1 else []}
                for j, t in enumerate(texts)]

    fake, calls = _fake_urlopen(reply)
    monkeypatch.setattr("gamma.translate_engines.urlopen", fake)
    r = dave.post("/api/ai/translate", json={"texts": ["Edge one.", "Edge two.", "Edge three."],
                                             "lang": "zh-TW", "model": "engine:microsoft"})
    assert r.status_code == 200, r.text
    # No setup, no key; a text without a translation stays as it was.
    assert r.json()["translations"] == ["M:Edge one.", "Edge two.", "M:Edge three."]
    (req,) = calls
    url = urlsplit(req.full_url)
    assert url.netloc == "edge.microsoft.com" and url.path == "/translate/translatetext"
    assert parse_qs(url.query) == {"to": ["zh-Hant"], "isEnterpriseClient": ["false"]}
    assert req.get_header("Authorization") is None
    assert json.loads(req.data) == ["Edge one.", "Edge two.", "Edge three."]

    # A plain-text error body is surfaced as it is.
    from urllib.error import HTTPError
    import io

    def too_big(req, timeout=None):
        raise HTTPError(req.full_url, 400, "Bad Request", {},
                        io.BytesIO(b"Request exceeds the maximum allowed translation size."))

    monkeypatch.setattr("gamma.translate_engines.urlopen", too_big)
    r = dave.post("/api/translate/engines/microsoft/test", json={"lang": "ja"})
    assert r.json() == {"ok": False,
                        "error": "Microsoft: HTTP 400 \u2014 Request exceeds the maximum allowed translation size."}


def test_free_service_failures_warn_and_notify_until_it_answers(dave, carol, monkeypatch):
    from urllib.error import URLError
    from gamma import logbuf, translate_engines

    monkeypatch.setattr(translate_engines, "_health",
                        {"failures": 0, "since": "", "error": "", "users": set(), "warned": False})
    notices = lambda c: [n for n in c.get("/api/notices").json()["notices"] if n["id"] == "free-translate"]

    def down(req, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr("gamma.translate_engines.urlopen", down)
    start = logbuf.last_seq("warning")
    streak_warnings = lambda: sum("times in a row" in e["msg"] for e in logbuf.tail(start))
    for i in range(translate_engines.FREE_ALERT_AFTER):
        assert notices(dave) == []  # not before the streak is long enough
        r = dave.post("/api/ai/translate", json={"texts": [f"down {i}"], "lang": "de", "model": "engine:microsoft"})
        assert r.status_code == 502
    (notice,) = notices(dave)
    assert notice["pane"] == "translation" and notice["tone"] == "warn"
    assert notices(carol) == []  # an account that never met the failures isn't told
    row = next(e for e in dave.get("/api/translate/engines").json()["engines"] if e["id"] == "microsoft")
    assert row["failing"] == {"since": notice["fingerprint"], "error": "Microsoft: connection refused"}
    # One streak warning in the log, however long the streak gets.
    assert streak_warnings() == 1
    dave.post("/api/ai/translate", json={"texts": ["down again"], "lang": "de", "model": "engine:microsoft"})
    assert streak_warnings() == 1

    # One answer ends the streak: the notice and the row's error are gone.
    monkeypatch.setattr("gamma.translate_engines.urlopen", _fake_urlopen(
        lambda req: [{"translations": [{"text": "wieder da"}]} for _ in json.loads(req.data)])[0])
    assert dave.post("/api/ai/translate", json={"texts": ["back up"], "lang": "de",
                                                "model": "engine:microsoft"}).status_code == 200
    assert notices(dave) == []
    row = next(e for e in dave.get("/api/translate/engines").json()["engines"] if e["id"] == "microsoft")
    assert row["failing"] is None


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


def test_effort_defaults_to_the_models_lowest_level(carol, monkeypatch):
    import gamma.routers.ai as ai_mod

    seen = []

    def fake(messages, system, entry, rt, **kw):
        seen.append(kw["effort"])
        return json.dumps([f"e:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    for n, levels in enumerate([["low", "medium", "high"], ["none", "low", "high"], None, []]):
        monkeypatch.setattr(ai_mod.ai_catalog, "reasoning_efforts", lambda *a, levels=levels: (levels, "provider"))
        r = carol.post("/api/ai/translate", json={"texts": [f"an effort-default paragraph {n}"], "lang": "pt"})
        assert r.status_code == 200
    # Anthropic's lowest is "low": its "none" would leave the parameter out
    # (the model's default). Unknown levels, or none, send nothing.
    assert seen == ["low", "low", "", ""]
    monkeypatch.setattr(ai_mod.ai_catalog, "reasoning_efforts", lambda *a: pytest.fail("a pick needs no lookup"))
    carol.post("/api/ai/translate", json={"texts": ["an effort-pick paragraph"], "lang": "pt", "effort": "high"})
    assert seen[-1] == "high"


def test_default_effort_on_the_openai_wires_is_the_first_level():
    from gamma.translate_context import default_effort

    assert default_effort(["none", "minimal", "low"], "openai-responses") == "none"
    assert default_effort(["minimal", "low"], "openai") == "minimal"
    assert default_effort(["none"], "anthropic") == ""


def test_a_paragraph_that_loses_a_formula_token_is_redone_on_its_own(carol, monkeypatch):
    batches = []

    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        batches.append(batch)
        if len(batch) > 1:  # the batch drops {v2} from its second paragraph, writes {v1} spaced in the first
            return json.dumps(["译 ｛ v1 ｝ 文", "译 {v1} 文"], ensure_ascii=False)
        return json.dumps([batch[0].replace("where", "其中")], ensure_ascii=False)

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    texts = ["token test one {v1} here", "where {v1} and {v2} stand"]
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["译 {v1} 文", "其中 {v1} and {v2} stand"]
    assert batches == [texts, ["where {v1} and {v2} stand"]]


def test_a_paragraph_whose_tokens_stay_lost_comes_back_verbatim(carol, monkeypatch):
    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda messages, *a, **k: json.dumps(["没有公式"] * len(texts_of(messages))))
    r = carol.post("/api/ai/translate", json={"texts": ["a lost-token paragraph {v1}"], "lang": "zh-CN"})
    assert r.json()["translations"] == ["a lost-token paragraph {v1}"]


def test_a_service_that_drops_a_token_shows_the_original(dave, monkeypatch):
    def reply(req):
        return [{"translations": [{"text": t.replace(" {v1}", ""), "to": "zh-Hans"}]} for t in json.loads(req.data)]

    fake, _ = _fake_urlopen(reply)
    monkeypatch.setattr("gamma.translate_engines.urlopen", fake)
    r = dave.post("/api/ai/translate", json={"texts": ["keeps {v1} it", "no token here"],
                                             "lang": "zh-CN", "model": "engine:microsoft"})
    assert r.json()["translations"] == ["keeps {v1} it", "no token here"]


def test_the_prompt_carries_the_target_languages_conventions():
    from gamma.translate_context import system_prompt

    assert "full-width punctuation" in system_prompt("zh-CN")
    assert "である調" in system_prompt("ja")
    assert "full-width" not in system_prompt("de")


def test_paragraphs_that_broke_their_tokens_are_redone_in_one_call(carol, monkeypatch):
    batches = []

    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        batches.append(batch)
        if len(batch) == 4:  # three of four lose their token
            return json.dumps(["好 {v1}", "坏", "坏", "坏"], ensure_ascii=False)
        return json.dumps([t.replace("broken", "修好") for t in batch], ensure_ascii=False)

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    texts = ["fine {v1}", "broken one {v1}", "broken two {v1}", "broken three {v1}"]
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"})
    body = r.json()
    assert body["translations"] == ["好 {v1}", "修好 one {v1}", "修好 two {v1}", "修好 three {v1}"]
    assert body["redone"] == 3
    assert batches[1] == texts[1:], "one call for the three, not three calls"


def test_one_text_may_come_back_without_brackets(carol, monkeypatch):
    # Asked for one text, some models answer with the translation alone.
    replies = {"bare reply one": "裸的回答", "bare reply two": "```\n围起来的回答\n```",
               "bare reply three": '"一个 JSON 字符串"', "bare reply four": "见 [12] 的回答"}
    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda messages, *a, **k: replies[texts_of(messages)[0]])
    for src, want in zip(replies, ["裸的回答", "围起来的回答", "一个 JSON 字符串", "见 [12] 的回答"]):
        body = carol.post("/api/ai/translate", json={"texts": [src], "lang": "zh-CN"}).json()
        assert (body["translations"], body["failed"]) == ([want], []), src
    # Nothing, or JSON that is no string, is no translation.
    for reply in ("  ", '{"text": "不是"}', '[["嵌套"]]'):
        monkeypatch.setattr("gamma.routers.ai._call_ai", lambda *a, reply=reply, **k: reply)
        body = carol.post("/api/ai/translate", json={"texts": ["an unanswered paragraph"], "lang": "zh-CN"}).json()
        assert (body["translations"], body["failed"]) == (["an unanswered paragraph"], [0]), reply


def test_a_miscounted_chunk_is_asked_again_in_halves(carol, monkeypatch):
    # Ten texts come back nine: the halves come back whole, so two calls
    # finish the chunk rather than one per paragraph.
    fake, calls = _fake_call(lambda batch: [f"半:{t}" for t in batch][: len(batch) - (len(batch) > 5)])
    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    texts = [f"halves paragraph {k}" for k in range(10)]
    body = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"}).json()
    assert body["translations"] == [f"半:{t}" for t in texts]
    assert (body["redone"], body["failed"]) == (10, [])
    assert calls[0] == texts and sorted(calls[1:]) == [texts[:5], texts[5:]]


def test_a_half_that_is_off_too_is_asked_one_text_per_call(carol, monkeypatch):
    fake, calls = _fake_call(lambda batch: [f"单:{t}" for t in batch][: max(1, len(batch) - 1)])
    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    texts = [f"one-by-one paragraph {k}" for k in range(6)]
    body = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"}).json()
    assert body["translations"] == [f"单:{t}" for t in texts] and body["failed"] == []
    assert sorted(len(c) for c in calls) == [1] * 6 + [3, 3, 6]


def test_texts_that_still_fail_are_named_with_their_duplicates(carol, monkeypatch):
    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        if len(batch) > 1:
            return json.dumps(["merged"])
        if batch[0].startswith("stubborn"):
            return '{"error": "no"}'
        return json.dumps([f"好:{batch[0]}"], ensure_ascii=False)

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    body = carol.post("/api/ai/translate", json={
        "texts": ["stubborn twin", "willing paragraph", "stubborn twin"], "lang": "zh-CN"}).json()
    assert body["translations"] == ["stubborn twin", "好:willing paragraph", "stubborn twin"]
    assert body["failed"] == [0, 2]
    # Not cached: asked for again, the failed text goes upstream again.
    monkeypatch.setattr("gamma.routers.ai._call_ai", lambda *a, **k: json.dumps(["终于"], ensure_ascii=False))
    body = carol.post("/api/ai/translate", json={"texts": ["stubborn twin"], "lang": "zh-CN"}).json()
    assert (body["translations"], body["failed"]) == (["终于"], [])


def test_a_streamed_chunk_that_comes_back_short_is_asked_again(carol, monkeypatch):
    import gamma.routers.ai as ai_mod
    monkeypatch.setattr(ai_mod, "_open_ai", lambda *a, **k: _StreamResp(['["只有', '一段"]']))
    fake, calls = _fake_call(lambda batch: [f"补:{t}" for t in batch])
    monkeypatch.setattr(ai_mod, "_call_ai", fake)
    r = carol.post("/api/ai/translate", json={
        "texts": ["short stream one", "short stream two"], "lang": "zh-CN", "stream": True})
    final = [json.loads(line) for line in r.text.splitlines() if line.strip()][-1]
    assert final["translations"] == ["补:short stream one", "补:short stream two"]
    assert (final["redone"], final["failed"]) == (2, [])
    assert sorted(calls) == [["short stream one"], ["short stream two"]]
