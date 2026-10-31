"""/api/ai/translate: validation, the paragraph cache, the wire of numbered
segments and the one call slot a request holds."""

import json

import pytest
from fastapi.testclient import TestClient

from ai_fixtures import segmented, texts_of


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


def context_of(messages):
    """The context lines above a translation call's segments, by label:
    {"section", "before", "after"} as far as the turn has them."""
    context = {}
    for line in messages[-1]["content"].splitlines():
        if line.startswith("<s"):
            break
        if line:
            label, _, value = line.partition(": ")
            context[label.split(" (")[0].lower()] = value
    return context


def _fake_call(replies):
    """A _call_ai stand-in: records each batch it was sent, answers from
    `replies` (a callable on the batch) in numbered segments."""
    calls = []

    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        calls.append(batch)
        return segmented(replies(batch))

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
    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)

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
    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["a twin paragraph", "a twin paragraph"], "lang": "it"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["译:a twin paragraph", "译:a twin paragraph"]
    assert calls == [["a twin paragraph"]]


def test_translate_forwards_effort(carol, monkeypatch):
    seen = {}

    def fake(messages, system, entry, rt, **kw):
        seen.update(kw)
        return segmented([f"x:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
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
        return segmented([f"T:{text}" for text in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
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
        return "<s1>traduit</s1>"

    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", unavailable)
    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    r = carol.post("/api/ai/translate", json={
        "texts": ["speed catalog failure"], "lang": "fr", "speed": "fast"})
    assert r.status_code == 200, r.text


def test_translate_prompt_names_the_language_and_the_break_marker(carol, monkeypatch):
    seen = {}

    def fake(messages, system, entry, rt, **kw):
        seen["system"] = system
        return segmented(texts_of(messages))

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    r = carol.post("/api/ai/translate",
                   json={"texts": ["a column-break {br} paragraph for the prompt test"], "lang": "ja"})
    assert r.status_code == 200
    assert "into Japanese" in seen["system"]
    assert "{br}" in seen["system"] and "{lang}" not in seen["system"]
    assert "<sN>" in seen["system"]


def test_translate_sends_the_context_and_asks_again_without_the_neighbours(carol, monkeypatch):
    turns = []
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: (["fast"], "provider"))

    def fake(messages, system, entry, rt, **kw):
        turns.append(messages)
        assert kw["speed"] == "fast"  # the call and its re-ask
        # The reply leaves its last segment out: that text is asked again.
        texts = texts_of(messages)
        return segmented([f"c:{t}" for t in texts][: max(1, len(texts) - 1)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    r = carol.post("/api/ai/translate", json={
        "texts": ["a context-test paragraph", "its context-test neighbour"], "lang": "ko", "speed": "fast",
        "before": "x" * 2000 + " the end of the paragraph\n\nbefore", "after": "the start of the one after",
        "section": "  II.   MODEL  "})
    assert r.status_code == 200
    first, again = turns
    context = context_of(first)
    assert context["section"] == "II. MODEL"
    assert context["before"].endswith("the end of the paragraph before") and len(context["before"]) == 1500
    assert context["after"] == "the start of the one after"
    assert first[-1]["content"].endswith(
        "\n\n<s1>a context-test paragraph</s1>\n<s2>its context-test neighbour</s2>")
    # Asked again on its own, numbered afresh, under the section alone.
    assert again[-1]["content"] == "Section: II. MODEL\n\n<s1>its context-test neighbour</s1>"
    assert r.json()["translations"] == ["c:a context-test paragraph", "c:its context-test neighbour"]
    assert (r.json()["redone"], r.json()["failed"]) == (1, [])


def test_translate_reads_the_segments_out_of_prose_and_fences(carol, monkeypatch):
    monkeypatch.setattr("gamma.routers.translate._call_ai",
                        lambda *a, **k: "Sure!\n```\n<s1>uno</s1>\n```\nHope this helps.")
    r = carol.post("/api/ai/translate", json={"texts": ["one fresh paragraph"], "lang": "es"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["uno"]


def test_a_segment_the_reply_leaves_out_is_asked_again(carol, monkeypatch):
    # The reply skips a number: the others are read by theirs, and only the
    # one left out is asked again.
    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        if len(batch) > 1:
            return f"<s1>ok:{batch[0]}</s1>\n<s3>ok:{batch[2]}</s3>"
        return segmented([f"again:{batch[0]}"])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    texts = ["gap paragraph one", "gap paragraph two", "gap paragraph three"]
    body = carol.post("/api/ai/translate", json={"texts": texts, "lang": "de"}).json()
    assert body["translations"] == ["ok:gap paragraph one", "again:gap paragraph two", "ok:gap paragraph three"]
    assert (body["redone"], body["failed"]) == (1, [])


def test_translate_unparseable_reply_degrades_to_original(carol, monkeypatch):
    # Provider reachable but answering no translation: paragraphs come back
    # verbatim (the viewer shows the original), named as failed so the
    # viewer asks for them again, and are NOT cached, so that retry can
    # still improve them.
    monkeypatch.setattr("gamma.routers.translate._call_ai", lambda *a, **k: '{"note": "no segments here"}')
    r = carol.post("/api/ai/translate", json={"texts": ["another fresh paragraph"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["another fresh paragraph"]
    assert r.json()["failed"] == [0]

    def good(messages, system, entry, rt, **kw):
        return segmented([f"besser:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", good)
    r = carol.post("/api/ai/translate", json={"texts": ["another fresh paragraph"], "lang": "de"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["besser:another fresh paragraph"]


def test_a_reply_the_output_cap_cut_is_no_translation_even_for_one_text(carol, monkeypatch):
    """The provider says the output cap stopped the reply: its last segment,
    open at the end, is half a translation, even when it is the only one
    (a re-ask, or a chunk of one paragraph), with or without a tag. It is
    asked again, and still cut, comes back verbatim, neither cached nor
    kept, so the next request asks once more."""
    replies = iter(["<s1>半个翻", "半个", "<s1>整段译文</s1>"])
    calls = []

    def capped(messages, system, entry, rt, **kw):
        calls.append(texts_of(messages))
        reply = next(replies)
        kw["on_stop"]("end_turn" if reply.endswith("</s1>") else "max_tokens")
        return reply

    monkeypatch.setattr("gamma.routers.translate._call_ai", capped)
    body = carol.post("/api/ai/translate", json={"texts": ["one long paragraph"], "lang": "fr"}).json()
    assert body["translations"] == ["one long paragraph"]
    assert (body["redone"], body["failed"]) == (1, [0])
    body = carol.post("/api/ai/translate", json={"texts": ["one long paragraph"], "lang": "fr"}).json()
    assert body["translations"] == ["整段译文"] and body["cached"] is False
    assert len(calls) == 3


def test_a_streamed_reply_the_output_cap_cut_asks_for_its_last_segment_again(carol, monkeypatch):
    import gamma.routers.translate as translate_mod

    monkeypatch.setattr(translate_mod, "_open_ai",
                        lambda *a, **kw: _StreamResp(["<s1>只有半"], stop="max_tokens"))
    again = []

    def whole(messages, system, entry, rt, **kw):
        again.append(texts_of(messages))
        return segmented(["完整的一段"])

    monkeypatch.setattr(translate_mod, "_call_ai", whole)
    r = carol.post("/api/ai/translate", json={"texts": ["a capped stream"], "lang": "fr", "stream": True})
    final = json.loads(r.text.splitlines()[-1])
    assert final["translations"] == ["完整的一段"] and final["redone"] == 1
    assert again == [["a capped stream"]]


def test_text_that_reads_as_a_segment_tag_goes_out_masked_and_comes_back_as_written(carol, monkeypatch):
    """A paragraph quoting a tag (<S2>, ＜s1＞, this wire's own </s1>) would,
    echoed inside its segment, end it early or open another: it goes out
    between ‹ › and its translation gets the source's brackets back."""
    from gamma.translate_context import _TAG

    sent = []

    def echo(messages, system, entry, rt, **kw):
        sent.append(messages[-1]["content"])
        return segmented([f"译:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", echo)
    texts = ["the mean spin <S2> is small", "markers ＜s1＞ and </s1> stay", "plain"]
    body = carol.post("/api/ai/translate", json={"texts": texts, "lang": "pt", "before": "see <s3>"}).json()
    assert body["translations"] == [f"译:{t}" for t in texts]
    assert (body["redone"], body["failed"]) == (0, [])
    assert len(sent) == 1
    assert [m.group(0) for m in _TAG.finditer(sent[0])] == ["<s1>", "</s1>", "<s2>", "</s2>", "<s3>", "</s3>"]
    assert "the mean spin ‹S2› is small" in sent[0] and "see ‹s3›" in sent[0]


class _StreamResp:
    """An Anthropic SSE reply whose text arrives in the given pieces, ended
    with the provider's ``stop`` reason when given."""
    def __init__(self, pieces, stop=""):
        events = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}} for t in pieces]
        if stop:
            events.append({"type": "message_delta", "delta": {"stop_reason": stop}})
        self._lines = [("data: " + json.dumps(e) + "\n").encode() for e in events] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


def test_translate_streams_partials(carol, monkeypatch):
    """stream: true answers NDJSON — a partial line per paragraph as the
    model writes it (every request index sharing that source text), then the
    same final object a plain call returns; the cache is filled the same way."""
    import gamma.routers.translate as translate_mod
    monkeypatch.setattr(translate_mod, "_TRANSLATE_STREAM_INTERVAL", 0)
    monkeypatch.setattr(translate_mod.ai_catalog, "speed_tiers", lambda *a: (["fast"], "provider"))
    opened = []

    def fake_open(messages, system, entry, rt, **kw):
        opened.append(texts_of(messages))
        assert kw.get("stream") is True
        assert kw["speed"] == "fast"
        return _StreamResp(['```\n<s1>第一', '段</s1>\n<s2>第', '二段</s2>\n```'])

    monkeypatch.setattr(translate_mod, "_open_ai", fake_open)
    r = carol.post("/api/ai/translate", json={
        "texts": ["stream one", "stream two", "stream one"], "lang": "zh-CN", "stream": True, "speed": "fast"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert opened == [["stream one", "stream two"]]  # duplicate collapsed upstream
    assert lines[-1] == {"translations": ["第一段", "第二段", "第一段"],
                         "model": lines[-1]["model"], "cached": False, "redone": 0, "failed": [], "effort": "",
                         "brief": True}
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
                      "failed": [], "effort": "", "brief": True}]
    assert len(opened) == 1


def test_translate_stream_reports_upstream_failure_in_band(carol, monkeypatch):
    import gamma.routers.translate as translate_mod

    def boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(translate_mod, "_open_ai", boom)
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
    monkeypatch.setattr("gamma.routers.translate._call_ai", lambda *a, **k: pytest.fail("no LLM on the engine path"))
    fake, calls = _fake_urlopen(lambda req: {"data": {"translations": [
        {"translatedText": f"G:{t}"} for t in json.loads(req.data)["q"]]}})
    monkeypatch.setattr("gamma.translate_engines.urlopen", fake)

    texts = ["Google one.", "  ", "Google two.", "Google one."]
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: pytest.fail("engines have no speed tier"))
    r = dave.post("/api/ai/translate", json={"texts": texts, "lang": "zh-TW", "model": "engine:google", "speed": "fast"})
    assert r.status_code == 200, r.text
    assert r.json() == {"translations": ["G:Google one.", "  ", "G:Google two.", "G:Google one."],
                        "model": "engine:google", "cached": False, "redone": 0, "failed": [],
                        "brief": True}
    (req,) = calls
    assert req.get_header("X-goog-api-key") == "AIza-secret-key-9876"
    assert "key=" not in req.full_url
    assert json.loads(req.data) == {"q": ["Google one.", "Google two."], "target": "zh-TW", "format": "text"}

    # Cached per engine; the streamed form answers with the final line.
    r = dave.post("/api/ai/translate", json={"texts": ["Google two."], "lang": "zh-TW",
                                             "model": "engine:google", "stream": True})
    assert [json.loads(line) for line in r.text.splitlines() if line.strip()] == [
        {"translations": ["G:Google two."], "model": "engine:google", "cached": True, "redone": 0, "failed": [],
         "brief": True}]
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

    monkeypatch.setattr("gamma.routers.translate._call_ai", lambda *a, **k: pytest.fail("no LLM on the engine path"))

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
    import gamma.routers.translate as translate_mod

    seen = []

    def fake(messages, system, entry, rt, **kw):
        seen.append(kw["effort"])
        return segmented([f"e:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    for n, levels in enumerate([["low", "medium", "high"], ["none", "low", "high"], None, []]):
        monkeypatch.setattr(translate_mod.ai_catalog, "reasoning_efforts", lambda *a, levels=levels: (levels, "provider"))
        r = carol.post("/api/ai/translate", json={"texts": [f"an effort-default paragraph {n}"], "lang": "pt"})
        assert r.status_code == 200
    # Anthropic's lowest is "low": its "none" would leave the parameter out
    # (the model's default). Unknown levels, or none, send nothing.
    assert seen == ["low", "low", "", ""]
    monkeypatch.setattr(translate_mod.ai_catalog, "reasoning_efforts", lambda *a: pytest.fail("a pick needs no lookup"))
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
            return segmented(["译 ｛ v1 ｝ 文", "译 {v1} 文"])
        return segmented([batch[0].replace("where", "其中")])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    texts = ["token test one {v1} here", "where {v1} and {v2} stand"]
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"})
    assert r.status_code == 200
    assert r.json()["translations"] == ["译 {v1} 文", "其中 {v1} and {v2} stand"]
    assert batches == [texts, ["where {v1} and {v2} stand"]]


def test_a_paragraph_whose_tokens_stay_lost_comes_back_verbatim(carol, monkeypatch):
    monkeypatch.setattr("gamma.routers.translate._call_ai",
                        lambda messages, *a, **k: segmented(["没有公式"] * len(texts_of(messages))))
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
            return segmented(["好 {v1}", "坏", "坏", "坏"])
        return segmented([t.replace("broken", "修好") for t in batch])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    texts = ["fine {v1}", "broken one {v1}", "broken two {v1}", "broken three {v1}"]
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"})
    body = r.json()
    assert body["translations"] == ["好 {v1}", "修好 one {v1}", "修好 two {v1}", "修好 three {v1}"]
    assert body["redone"] == 3
    assert batches[1] == texts[1:], "one call for the three, not three calls"


def test_one_text_may_come_back_without_tags(carol, monkeypatch):
    # Asked for one text, some models answer with the translation alone.
    replies = {"bare reply one": "裸的回答", "bare reply two": "```\n围起来的回答\n```",
               "bare reply three": '"一个 JSON 字符串"', "bare reply four": "见 [12] 的回答"}
    monkeypatch.setattr("gamma.routers.translate._call_ai", lambda messages, *a, **k: replies[texts_of(messages)[0]])
    for src, want in zip(replies, ["裸的回答", "围起来的回答", "一个 JSON 字符串", "见 [12] 的回答"]):
        body = carol.post("/api/ai/translate", json={"texts": [src], "lang": "zh-CN"}).json()
        assert (body["translations"], body["failed"]) == ([want], []), src
    # Nothing, or JSON that is no string, is no translation.
    for reply in ("  ", '{"text": "不是"}', '[["嵌套"]]'):
        monkeypatch.setattr("gamma.routers.translate._call_ai", lambda *a, reply=reply, **k: reply)
        body = carol.post("/api/ai/translate", json={"texts": ["an unanswered paragraph"], "lang": "zh-CN"}).json()
        assert (body["translations"], body["failed"]) == (["an unanswered paragraph"], [0]), reply


def test_missing_doubled_and_broken_segments_are_asked_again_in_one_call(carol, monkeypatch):
    # One reply leaves a segment out, writes one twice (which of the two is
    # right is unknown) and drops a formula token from a third: the three go
    # out again together, numbered afresh, in ONE call.
    calls = []

    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        calls.append(batch)
        if len(batch) == 4:
            return "<s1>留:kept one</s1>\n<s2>一</s2>\n<s2>二</s2>\n<s4>丢了公式</s4>"
        return segmented([f"再:{t}" for t in batch])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    texts = ["kept one", "doubled two", "left out three", "broken {v1} four"]
    body = carol.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN"}).json()
    assert body["translations"] == ["留:kept one", "再:doubled two", "再:left out three", "再:broken {v1} four"]
    assert (body["redone"], body["failed"]) == (3, [])
    assert calls == [texts, texts[1:]]


def test_texts_the_re_ask_gets_wrong_too_come_back_verbatim_named_with_their_duplicates(carol, monkeypatch):
    calls = []

    def fake(messages, system, entry, rt, **kw):  # "stubborn twin" never comes back
        batch = texts_of(messages)
        calls.append(batch)
        return "\n".join(f"<s{k}>好:{t}</s{k}>" for k, t in enumerate(batch, 1) if not t.startswith("stubborn"))

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    body = carol.post("/api/ai/translate", json={
        "texts": ["stubborn twin", "willing paragraph", "stubborn twin"], "lang": "zh-CN"}).json()
    assert body["translations"] == ["stubborn twin", "好:willing paragraph", "stubborn twin"]
    assert (body["redone"], body["failed"]) == (1, [0, 2])
    assert calls == [["stubborn twin", "willing paragraph"], ["stubborn twin"]], "asked again once, then left"
    # Not cached: asked for again, the failed text goes upstream again.
    monkeypatch.setattr("gamma.routers.translate._call_ai", lambda *a, **k: "<s1>终于</s1>")
    body = carol.post("/api/ai/translate", json={"texts": ["stubborn twin"], "lang": "zh-CN"}).json()
    assert (body["translations"], body["failed"]) == (["终于"], [])


def test_a_re_ask_that_fails_leaves_its_texts_verbatim(carol, monkeypatch):
    def fake(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        if len(batch) == 1:
            raise RuntimeError("provider down")
        return f"<s1>好:{batch[0]}</s1>"

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    body = carol.post("/api/ai/translate", json={
        "texts": ["a re-ask's fine paragraph", "a re-ask's doomed paragraph"], "lang": "zh-CN"}).json()
    assert body["translations"] == ["好:a re-ask's fine paragraph", "a re-ask's doomed paragraph"]
    assert (body["redone"], body["failed"]) == (1, [1])


def test_a_streamed_chunk_that_comes_back_short_is_asked_again(carol, monkeypatch):
    import gamma.routers.translate as translate_mod
    monkeypatch.setattr(translate_mod, "_open_ai", lambda *a, **k: _StreamResp(["<s1>只有", "一段</s1>"]))
    fake, calls = _fake_call(lambda batch: [f"补:{t}" for t in batch])
    monkeypatch.setattr(translate_mod, "_call_ai", fake)
    r = carol.post("/api/ai/translate", json={
        "texts": ["short stream one", "short stream two"], "lang": "zh-CN", "stream": True})
    final = [json.loads(line) for line in r.text.splitlines() if line.strip()][-1]
    assert final["translations"] == ["只有一段", "补:short stream two"]
    assert (final["redone"], final["failed"]) == (1, [])
    assert calls == [["short stream two"]]


@pytest.mark.parametrize("stream", [False, True])
def test_a_request_holds_one_call_slot_for_its_call_and_its_re_ask(carol, monkeypatch, stream):
    import gamma.routers.translate as translate_mod
    from conftest import account_of
    from gamma import ai_client

    account = account_of("translate_carol")
    held = []  # per model call: the account's slots in use, and the slot it ran under

    def first_only(messages, kw):
        held.append((ai_client._open_calls.get(account, 0), kw["slot"]))
        return segmented([f"槽:{t}" for t in texts_of(messages)][:1])

    monkeypatch.setattr(translate_mod, "_TRANSLATE_STREAM_INTERVAL", 0)
    monkeypatch.setattr(translate_mod, "_call_ai", lambda messages, *a, **kw: first_only(messages, kw))
    monkeypatch.setattr(translate_mod, "_open_ai", lambda messages, *a, **kw: _StreamResp([first_only(messages, kw)]))
    texts = [f"a one-slot paragraph ({'streamed' if stream else 'plain'}) {k}" for k in range(3)]
    r = carol.post("/api/ai/translate", json={"texts": texts, "lang": "fr", "stream": stream})
    final = [json.loads(line) for line in r.text.splitlines() if line.strip()][-1] if stream else r.json()
    assert (final["redone"], final["failed"]) == (2, [2])
    # The call and its re-ask each ran with one of the account's slots in
    # use, the same one, and the request gave it back when it ended.
    assert [in_use for in_use, _ in held] == [1, 1]
    assert held[0][1] is not None and held[0][1] is held[1][1]
    assert ai_client._open_calls.get(account, 0) == 0


# --- the wire's pieces ------------------------------------------------------------

def test_the_user_turn_is_the_context_then_one_segment_per_text():
    from gamma.translate_context import unmask, user_turn

    (turn,) = user_turn(["One {v1}.", "Two."], before="…the end\nof the one before", after="The next.",
                        section="II.  Model")
    assert turn == {"role": "user", "content": (
        "Section: II. Model\n"
        "Before (context only, not to translate): …the end of the one before\n"
        "After (context only, not to translate): The next.\n"
        "\n"
        "<s1>One {v1}.</s1>\n<s2>Two.</s2>")}
    assert user_turn(["Alone."])[0]["content"] == "<s1>Alone.</s1>"
    # Text that reads as a tag goes out masked; its translation gets it back.
    assert user_turn(["a < S 2 > b"], section="see ＜s1＞")[0]["content"] == "Section: see ‹s1›\n\n<s1>a ‹ S 2 › b</s1>"
    assert unmask("a < S 2 > b", "甲 ‹ S 2 › 乙") == "甲 < S 2 > 乙" and unmask("a", None) is None


def test_segments_are_read_by_number():
    from gamma.translate_context import segments

    # Tags as models write them back (spaced, upper case, full-width); text
    # outside the segments (prose, a fence) is skipped.
    assert segments("Here:\n```\n< s1 >一</s1>\n<S2>二</ S2 >\n＜s3＞三＜／s3＞\n```", 3) == ["一", "二", "三"]
    # By number, in any order; a segment left open runs to the next one, and
    # a closing tag with the wrong number still closes it.
    assert segments("<s2>二\n<s1>一</s1>", 2) == ["一", "二"]
    assert segments("<s1>一</s2>\n<s2>二</s2>", 2) == ["一", "二"]
    assert segments("<s1>一\n<s2>二", 2) == ["一", "二"]
    # Written twice, empty or left out: None. A number past n is ignored.
    assert segments("<s1>一</s1>\n<s1>壹</s1>\n<s3> </s3>\n<s4>四</s4>\n<s7>七</s7>", 4) == [None, None, None, "四"]
    # A reply cut short: its last segment, left open where the others were
    # closed, is half a translation.
    assert segments("<s1>一</s1>\n<s2>二的一", 2) == ["一", None]
    # The provider said the output cap stopped it: the segment left open at
    # the end is cut, even when no other was closed or it is the only one,
    # tagged or not. One closed before the cap is whole.
    assert segments("<s1>一\n<s2>二的一", 2, capped=True) == ["一", None]
    assert segments("<s1>一半", 1, capped=True) == [None]
    assert segments("一半", 1, capped=True) == [None]
    assert segments("<s1>一</s1>", 1, capped=True) == ["一"]
    # One text: a reply without any tag is the translation; with tags, only
    # they count. Several texts need their tags.
    assert segments("一", 1) == ["一"]
    assert segments("前言 <s1>一</s1>", 1) == ["一"]
    assert segments("一", 2) == [None, None]
    assert segments("", 1) == [None]


def test_partial_segments_show_what_the_stream_has_written():
    from gamma.translate_context import partial_segments

    assert partial_segments("") == {} and partial_segments("```\n") == {}
    assert partial_segments("```\n<s1>第一段</s1>\n<s2>第二") == {1: "第一段", 2: "第二"}
    # A tag half-written at the end is not text yet.
    for tail in ("<", "</", "</s", "</s2", "< / S 2 ", "＜"):
        assert partial_segments("<s1>一</s1>\n<s2>第二" + tail) == {1: "一", 2: "第二"}, tail
    assert partial_segments("<s1>一\n<s") == {1: "一"}
    assert partial_segments("<s1>一</s1>\n<s2>") == {1: "一"}
