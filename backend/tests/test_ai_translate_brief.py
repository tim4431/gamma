"""/api/ai/translate's paper brief (gamma/translate_context.py): made once per
paper, language and model from the stored PDF or the client's opening text,
carried in every request's system prompt, kept apart in the paragraph cache,
and never blocking a request when it fails."""

import json
import time

import pytest

from conftest import login, make_user, together, workspace_of

BRIEF_SYSTEM = "You prepare a translator's brief"
BRIEF = json.dumps({
    "field": "atomic physics, Rydberg atoms",
    "summary": "研究不完美阻塞下超原子的集体退相干。",
    "notation": ["Ω - the Rabi frequency"],
    "terms": [{"source": "Rydberg blockade", "target": "里德堡阻塞"},
              {"source": "superatom", "target": "超原子"}],
    "keep": ["EIT"],
})


def _pdf(lines):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    for n, line in enumerate(lines):
        page.insert_text((72, 72 + 14 * n), line, fontsize=11)
    return doc.tobytes()


@pytest.fixture(scope="module")
def eve(client):
    """An account with an Anthropic provider and a page whose PDF is stored."""
    make_user("tbrief_eve", "pw")
    c = login("tbrief_eve", "pw")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-ant-key-5678", "models": "claude-test"})
    assert r.status_code == 200, r.text
    data = _pdf(["Collective dephasing of a Rydberg superatom",
                 "We study how an ensemble of atoms that shares one Rydberg excitation",
                 "loses coherence when the Rydberg blockade is imperfect."])
    up = c.post("/api/uploads", files={"file": ("superatom.pdf", data, "application/pdf")}).json()
    page = c.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "Superatom paper"}).json()
    return c, page["id"]


def _fake(monkeypatch, brief=BRIEF, delay=0.0):
    """A _call_ai stand-in that answers the brief call with `brief` (or
    raises it) and translation calls with "T:"+text; records both."""
    calls = {"brief": [], "brief_kwargs": [], "translate": []}

    def fake(messages, system, entry, rt, **kw):
        if system.startswith(BRIEF_SYSTEM):
            calls["brief"].append(messages[-1]["content"])
            calls["brief_kwargs"].append(kw)
            time.sleep(delay)
            if isinstance(brief, Exception):
                raise brief
            return brief
        batch = json.loads(messages[-1]["content"])["texts"]
        calls["translate"].append({"system": system, "batch": batch, "kw": kw})
        return json.dumps([f"T:{t}" for t in batch], ensure_ascii=False)

    monkeypatch.setattr("gamma.routers.ai._call_ai", fake)
    return calls


def _translate(c, texts, **body):
    r = c.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN", **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_brief_is_made_once_from_the_stored_pdf_and_carried_in_every_request(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: (["fast"], "provider"))
    _translate(c, ["a first brief-test paragraph"], page_id=page, speed="fast")
    _translate(c, ["a second brief-test paragraph"], page_id=page)
    assert len(calls["brief"]) == 1
    assert calls["brief_kwargs"][0]["speed"] == "fast"
    assert [call["kw"]["speed"] for call in calls["translate"]] == ["fast", ""]
    material = calls["brief"][0]
    assert "Title: Superatom paper" in material and "imperfect" in material
    for call in calls["translate"]:
        assert "Rydberg blockade → 里德堡阻塞" in call["system"]
        assert "Keep as written: EIT" in call["system"]
    keys = {call["kw"]["cache_key"] for call in calls["translate"]}
    assert len(keys) == 1 and keys != {""}


def test_parallel_requests_wait_for_the_one_brief(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch, delay=0.3)
    results = together(4, lambda: c.post("/api/ai/translate", json={
        "texts": ["a paragraph sent in parallel"], "lang": "de", "page_id": page}))
    assert all(r.status_code == 200 for r in results)
    assert len(calls["brief"]) == 1
    assert all("里德堡阻塞" in call["system"] for call in calls["translate"])


def test_a_failed_brief_translates_without_one_and_is_not_retried_at_once(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch, brief=RuntimeError("provider down"))
    out = _translate(c, ["a paragraph whose brief fails"], page_id=page, lang="fr")
    assert out["translations"] == ["T:a paragraph whose brief fails"]
    _translate(c, ["another paragraph whose brief fails"], page_id=page, lang="fr")
    assert len(calls["brief"]) == 1
    assert all("The paper's brief" not in call["system"] for call in calls["translate"])
    assert all(call["kw"]["cache_key"] == "" for call in calls["translate"])


def test_an_unusable_brief_reply_is_no_brief(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch, brief='["ok"]')
    _translate(c, ["a paragraph with an unusable brief"], page_id=page, lang="es")
    assert len(calls["brief"]) == 1
    assert "The paper's brief" not in calls["translate"][0]["system"]


def test_the_opening_text_makes_the_brief_for_a_page_without_a_stored_file(eve, monkeypatch):
    c, _ = eve
    calls = _fake(monkeypatch)
    head = "Imperfect blockade in Rydberg superatoms. Ensembles of atoms interacting via their Rydberg levels."
    _translate(c, ["a paragraph of a source-only paper"], head=head)
    _translate(c, ["another paragraph of it"], head=head)
    assert len(calls["brief"]) == 1
    assert calls["brief"][0].endswith(f"Opening text:\n{head}")
    assert all("超原子" in call["system"] for call in calls["translate"])


def test_a_quick_request_only_reads_a_brief_already_made(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    _translate(c, ["a selection before any brief"], page_id=page, lang="it", build_brief=False)
    assert calls["brief"] == [] and "The paper's brief" not in calls["translate"][-1]["system"]
    _translate(c, ["a paragraph that makes the brief"], page_id=page, lang="it")
    _translate(c, ["a selection after the brief"], page_id=page, lang="it", build_brief=False)
    assert len(calls["brief"]) == 1 and "里德堡阻塞" in calls["translate"][-1]["system"]


def test_translations_with_and_without_the_brief_are_cached_apart(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    text = ["one paragraph translated both ways"]
    _translate(c, text, lang="ru")
    _translate(c, text, lang="ru", page_id=page)
    again = _translate(c, text, lang="ru", page_id=page)
    assert [call["batch"] for call in calls["translate"]] == [text, text]
    assert again["cached"] is True


def test_a_page_of_a_workspace_the_account_is_not_in_is_refused(eve, monkeypatch):
    c, page = eve
    _fake(monkeypatch)
    make_user("tbrief_mallory", "pw")
    r = c.post("/api/ai/translate", json={"texts": ["x"], "lang": "zh-CN", "page_id": page},
               headers={"X-Gamma-Workspace": workspace_of("tbrief_mallory")})
    assert r.status_code == 403


def test_render_brief_drops_what_it_cannot_use():
    from gamma.translate_context import parse_brief, render_brief

    assert render_brief({"field": "physics"}) == ""
    assert parse_brief("no json here") is None
    text = render_brief({"terms": [{"source": "fidelity", "target": "保真度"}, {"source": "EIT", "target": "EIT"},
                                   "stray", {"source": "", "target": "空"}]})
    assert "fidelity → 保真度" in text and "EIT → EIT" not in text and "空" not in text
    brief = parse_brief("Here you go:\n```json\n" + BRIEF + "\n```")
    assert brief and brief.startswith("The paper's brief")


def _stream_fake(monkeypatch, calls):
    """A streamed provider stand-in under _open_ai: replies "S:"+text for
    each text of the request, as Anthropic SSE deltas."""
    import gamma.routers.ai as ai_mod

    class Resp:
        def __init__(self, text):
            self.lines = [f'data: {json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})}\n'.encode(),
                          b"data: [DONE]\n"]

        def __iter__(self):
            return iter(self.lines)

        def close(self):
            pass

    def fake_open(messages, system, entry, rt, **kw):
        batch = json.loads(messages[-1]["content"])["texts"]
        calls["translate"].append({"system": system, "batch": batch, "kw": kw})
        return Resp(json.dumps([f"S:{t}" for t in batch], ensure_ascii=False))

    monkeypatch.setattr(ai_mod, "_open_ai", fake_open)
    monkeypatch.setattr(ai_mod, "_TRANSLATE_STREAM_INTERVAL", 0)


def test_a_streamed_request_makes_the_brief_inside_its_stream(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    _stream_fake(monkeypatch, calls)
    r = c.post("/api/ai/translate", json={"texts": ["a streamed paragraph"], "lang": "pt", "page_id": page, "stream": True})
    assert r.status_code == 200
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip() and "ping" not in l]
    # The stream says when it is reading the paper, and when that is over.
    assert lines[:2] == [{"stage": "brief"}, {"stage": "translate"}]
    assert lines[-1]["translations"] == ["S:a streamed paragraph"]
    assert len(calls["brief"]) == 1 and "里德堡阻塞" in calls["translate"][0]["system"]
    # Once the brief is made, a request has nothing to wait for and says nothing.
    r = c.post("/api/ai/translate", json={"texts": ["another paragraph"], "lang": "pt", "page_id": page, "stream": True})
    lines = [json.loads(l) for l in r.text.splitlines() if l.strip() and "ping" not in l]
    assert not any("stage" in l for l in lines) and lines[-1]["translations"] == ["S:another paragraph"]


def test_an_unstored_page_is_named_by_its_id_so_a_selection_finds_the_jobs_brief(eve, monkeypatch):
    c, _ = eve
    calls = _fake(monkeypatch)
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "Source-only paper",
                                       "properties": {"source_url": "https://example.org/paper.pdf"}}).json()["id"]
    _translate(c, ["a job paragraph"], page_id=page, head="Opening text of a source-only paper.", lang="ko")
    _translate(c, ["a selected sentence"], page_id=page, build_brief=False, lang="ko")
    assert len(calls["brief"]) == 1
    assert "里德堡阻塞" in calls["translate"][-1]["system"]


def test_the_paragraph_cache_outlives_the_brief_leaving_memory(eve, monkeypatch):
    from gamma import translate_context

    c, page = eve
    calls = _fake(monkeypatch)
    _translate(c, ["a paragraph cached under its brief"], page_id=page, lang="ja")
    with translate_context._lock:
        translate_context._briefs.clear()
    again = _translate(c, ["a paragraph cached under its brief"], page_id=page, lang="ja")
    assert again["cached"] is True
    assert len(calls["translate"]) == 1


def test_waiting_is_capped_per_account_and_a_refused_call_is_no_failed_brief():
    import threading

    from gamma import translate_context
    from gamma.ai_client import TooManyCalls

    gate = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        gate.wait(5)
        return BRIEF

    owner = threading.Thread(target=lambda: translate_context.brief_for("cap-test", slow, "acct"))
    owner.start()
    started.wait(5)
    waiters = [threading.Thread(target=lambda: translate_context.brief_for("cap-test", slow, "acct"))
               for _ in range(translate_context._BRIEF_WAITERS)]
    for w in waiters:
        w.start()
    import time as _t
    deadline = _t.monotonic() + 5
    while translate_context._waiting.get("acct", 0) < translate_context._BRIEF_WAITERS and _t.monotonic() < deadline:
        _t.sleep(0.01)
    t0 = _t.monotonic()
    assert translate_context.brief_for("cap-test", slow, "acct") is None, "past the cap a request goes on without"
    assert _t.monotonic() - t0 < 1
    gate.set()
    owner.join(5)
    for w in waiters:
        w.join(5)
    assert translate_context.brief_known("cap-test")[0] is not None

    def refused():
        raise TooManyCalls(6)

    assert translate_context.brief_for("refused-test", refused, "acct") is None
    assert translate_context.brief_known("refused-test") == (None, False), "a refusal is not remembered"
