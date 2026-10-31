"""The paper's brief (gamma/translate_context.py): made once per paper,
language and model by /api/ai/translate/brief from the stored PDF or the
client's opening text, carried in every /api/ai/translate request's system
prompt (which waits for it while it is made, and never makes it), kept apart
in the paragraph cache, and never blocking a request when it fails."""

import json
import threading
import time

import pytest

from ai_fixtures import segmented, texts_of
from conftest import account_of, at_once, login, make_user, together, workspace_of

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


def _paper(c, tag):
    """A page of its own with its own stored PDF, so that a test's brief and
    kept translations are its own."""
    data = _pdf([f"Collective dephasing of a Rydberg superatom ({tag})",
                 "We study how an ensemble of atoms that shares one Rydberg excitation",
                 "loses coherence when the Rydberg blockade is imperfect."])
    up = c.post("/api/uploads", files={"file": (f"{tag}.pdf", data, "application/pdf")}).json()
    return c.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": f"Superatom paper {tag}"}).json()["id"]


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


def _fake(monkeypatch, brief=BRIEF, delay=0.0, while_making=None):
    """A _call_ai stand-in that answers the brief call with `brief` (or
    raises it), after `delay` seconds and `while_making()`, and translation
    calls with "T:"+text; records both."""
    calls = {"brief": [], "brief_kwargs": [], "translate": []}

    def fake(messages, system, entry, rt, **kw):
        if system.startswith(BRIEF_SYSTEM):
            calls["brief"].append(messages[-1]["content"])
            calls["brief_kwargs"].append(kw)
            time.sleep(delay)
            if while_making:
                while_making()
            if isinstance(brief, Exception):
                raise brief
            return brief
        batch = texts_of(messages)
        calls["translate"].append({"system": system, "batch": batch, "kw": kw})
        return segmented([f"T:{t}" for t in batch])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    return calls


def _translate(c, texts, **body):
    r = c.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN", **body})
    assert r.status_code == 200, r.text
    return r.json()


def _lines(r):
    return [json.loads(line) for line in r.text.splitlines() if line.strip() and "ping" not in line]


def _brief(c, **body):
    """The brief request's NDJSON lines."""
    r = c.post("/api/ai/translate/brief", json={"lang": "zh-CN", **body})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    return _lines(r)


def _in_background(c, **body):
    """A brief request on a thread of its own (not started): the thread, and
    the list its lines land in."""
    out = []
    return threading.Thread(target=lambda: out.append(_brief(c, **body))), out


def test_the_brief_request_makes_the_brief_and_every_chunk_carries_it(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    monkeypatch.setattr("gamma.ai_catalog.speed_tiers", lambda *a: (["fast"], "provider"))
    assert _brief(c, page_id=page, speed="fast") == [{"stage": "brief"}, {"ready": True}]
    first = _translate(c, ["a first brief-test paragraph"], page_id=page, speed="fast")
    _translate(c, ["a second brief-test paragraph"], page_id=page)
    assert len(calls["brief"]) == 1
    kw = calls["brief_kwargs"][0]
    assert (kw["speed"], kw["max_tokens"], kw["timeout"]) == ("fast", 3000, 90)
    assert [call["kw"]["speed"] for call in calls["translate"]] == ["fast", ""]
    material = calls["brief"][0]
    assert "Title: Superatom paper" in material and "imperfect" in material
    for call in calls["translate"]:
        assert "Rydberg blockade → 里德堡阻塞" in call["system"]
        assert "Keep as written: EIT" in call["system"]
    keys = {call["kw"]["cache_key"] for call in calls["translate"]}
    assert len(keys) == 1 and keys != {""}
    assert first["brief"] is True
    # Made already: the brief request answers at once, with nothing to wait for.
    assert _brief(c, page_id=page) == [{"ready": True}]
    assert len(calls["brief"]) == 1


def test_the_translation_request_never_makes_the_brief(eve, monkeypatch):
    c, _ = eve
    page = _paper(c, "never-makes")
    calls = _fake(monkeypatch)
    plain = _translate(c, ["a paragraph before any brief"], page_id=page)
    _stream_fake(monkeypatch, calls)
    streamed = _lines(c.post("/api/ai/translate", json={"texts": ["a streamed paragraph before any brief"],
                                                        "lang": "zh-CN", "page_id": page, "stream": True}))
    assert calls["brief"] == []
    assert all("The paper's brief" not in call["system"] for call in calls["translate"])
    # The final object says the brief was missing, so the viewer asks again once it is made.
    assert plain["brief"] is False
    assert not any("stage" in line for line in streamed) and streamed[-1]["brief"] is False


def test_parallel_brief_requests_make_one_brief(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch, delay=0.3)
    results = together(4, lambda: c.post("/api/ai/translate/brief", json={"lang": "de", "page_id": page}))
    assert all(r.status_code == 200 and _lines(r)[-1] == {"ready": True} for r in results)
    assert len(calls["brief"]) == 1


def test_translation_requests_wait_for_the_brief_being_made_and_hold_no_call_slot(eve, monkeypatch):
    """While the brief request makes the brief, a plain and a streamed
    translation request wait for it, holding none of the account's call
    slots meanwhile (the one in use is the brief call's), then carry it."""
    from gamma import ai_client, translate_context

    c, page = eve
    account = account_of("tbrief_eve")
    started = threading.Event()
    in_use = []

    def while_making():
        started.set()
        deadline = time.monotonic() + 5
        while translate_context._waiting.get(account, 0) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        in_use.append((translate_context._waiting.get(account, 0), ai_client._open_calls.get(account, 0)))

    calls = _fake(monkeypatch, while_making=while_making)
    _stream_fake(monkeypatch, calls)
    maker, made = _in_background(c, page_id=page, lang="en")
    maker.start()
    assert started.wait(5)
    body = {"lang": "en", "page_id": page}
    plain, streamed = at_once([
        lambda: c.post("/api/ai/translate", json={"texts": ["a paragraph asked plainly"], **body}),
        lambda: c.post("/api/ai/translate", json={"texts": ["a paragraph asked streamed"], "stream": True, **body})])
    maker.join(10)
    assert made == [[{"stage": "brief"}, {"ready": True}]]
    assert plain.status_code == streamed.status_code == 200
    assert in_use == [(2, 1)], "both wait for the brief, and the only slot in use is the brief call's"
    lines = _lines(streamed)
    # The stream says when it is waiting for the paper, and when that is over.
    assert lines[:2] == [{"stage": "brief"}, {"stage": "translate"}]
    assert lines[-1]["translations"] == ["S:a paragraph asked streamed"] and lines[-1]["brief"] is True
    assert plain.json()["brief"] is True
    assert len(calls["brief"]) == 1 and len(calls["translate"]) == 2
    assert all("里德堡阻塞" in call["system"] for call in calls["translate"])
    assert ai_client._open_calls.get(account, 0) == 0


def test_without_wait_brief_a_request_only_reads_a_brief_already_made(eve, monkeypatch):
    c, page = eve
    gate, started = threading.Event(), threading.Event()

    def while_making():
        started.set()
        gate.wait(10)

    calls = _fake(monkeypatch, while_making=while_making)
    maker, made = _in_background(c, page_id=page, lang="it")
    maker.start()
    assert started.wait(5)
    try:
        during = _translate(c, ["a selection while the brief is made"], page_id=page, lang="it", wait_brief=False)
    finally:
        gate.set()
        maker.join(10)
    assert during["brief"] is False and "The paper's brief" not in calls["translate"][-1]["system"]
    assert made == [[{"stage": "brief"}, {"ready": True}]]
    after = _translate(c, ["a selection after the brief"], page_id=page, lang="it", wait_brief=False)
    assert after["brief"] is True and "里德堡阻塞" in calls["translate"][-1]["system"]
    assert len(calls["brief"]) == 1


def test_a_failed_brief_is_not_ready_and_is_not_asked_for_again_at_once(eve, monkeypatch):
    from gamma import translate_context

    c, page = eve
    calls = _fake(monkeypatch, brief=RuntimeError("provider down"))
    failed = {"ready": False, "error": "making the paper's brief failed: provider down"}
    assert _brief(c, page_id=page, lang="fr") == [{"stage": "brief"}, failed]
    assert _brief(c, page_id=page, lang="fr") == [failed], "known at once: nothing is under way"
    assert len(calls["brief"]) == 1
    out = _translate(c, ["a paragraph whose brief fails"], page_id=page, lang="fr")
    assert out["translations"] == ["T:a paragraph whose brief fails"] and out["brief"] is False
    assert "The paper's brief" not in calls["translate"][0]["system"]
    assert calls["translate"][0]["kw"]["cache_key"] == ""
    # Once the failure is old enough, the next brief request tries again.
    monkeypatch.setattr(translate_context, "_BRIEF_FAILED_TTL", 0.0)
    _brief(c, page_id=page, lang="fr")
    assert len(calls["brief"]) == 2


def test_an_unusable_brief_reply_is_no_brief(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch, brief='["ok"]')
    assert _brief(c, page_id=page, lang="es")[-1] == {"ready": False, "error": "the paper's brief came back unusable"}
    _translate(c, ["a paragraph with an unusable brief"], page_id=page, lang="es")
    assert len(calls["brief"]) == 1
    assert "The paper's brief" not in calls["translate"][0]["system"]


def test_the_brief_request_is_not_ready_for_a_service_or_without_a_paper(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    assert _brief(c, page_id=page, model="engine:google") == [{"ready": False}]
    assert _brief(c) == [{"ready": False, "error": "there is no paper to read: no page and no opening text"}]
    assert calls["brief"] == []


def test_the_brief_request_is_refused_at_the_door_when_every_call_slot_is_in_use(eve, monkeypatch):
    from gamma import ai_client

    c, _ = eve
    page = _paper(c, "door")
    calls = _fake(monkeypatch)
    monkeypatch.setitem(ai_client._open_calls, account_of("tbrief_eve"), ai_client.MAX_OPEN_CALLS)
    r = c.post("/api/ai/translate/brief", json={"lang": "zh-CN", "page_id": page})
    assert r.status_code == 429 and r.headers["retry-after"]
    assert calls["brief"] == []


def test_the_opening_text_makes_the_brief_for_a_paper_without_a_stored_file(eve, monkeypatch):
    c, _ = eve
    calls = _fake(monkeypatch)
    head = "Imperfect blockade in Rydberg superatoms. Ensembles of atoms interacting via their Rydberg levels."
    assert _brief(c, head=head)[-1] == {"ready": True}
    _translate(c, ["a paragraph of a source-only paper"], head=head)
    _translate(c, ["another paragraph of it"], head=head)
    assert len(calls["brief"]) == 1
    assert calls["brief"][0].endswith(f"Opening text:\n{head}")
    assert all("超原子" in call["system"] for call in calls["translate"])


def test_translations_with_and_without_the_brief_are_cached_apart(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    text = ["one paragraph translated both ways"]
    before = _translate(c, text, lang="ru", page_id=page)
    _brief(c, page_id=page, lang="ru")
    after = _translate(c, text, lang="ru", page_id=page)
    again = _translate(c, text, lang="ru", page_id=page)
    assert [call["batch"] for call in calls["translate"]] == [text, text]
    assert (before["brief"], after["brief"], again["cached"]) == (False, True, True)


def test_a_page_of_a_workspace_the_account_is_not_in_is_refused(eve, monkeypatch):
    c, page = eve
    _fake(monkeypatch)
    make_user("tbrief_mallory", "pw")
    elsewhere = {"X-Gamma-Workspace": workspace_of("tbrief_mallory")}
    r = c.post("/api/ai/translate", json={"texts": ["x"], "lang": "zh-CN", "page_id": page}, headers=elsewhere)
    assert r.status_code == 403
    r = c.post("/api/ai/translate/brief", json={"lang": "zh-CN", "page_id": page}, headers=elsewhere)
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
    # The brief carries no summary any more, and ten symbols at most.
    assert "Summary" not in brief and "超原子的集体退相干" not in brief
    many = render_brief({"notation": [f"x{n} - a symbol" for n in range(15)], "keep": ["EIT"]})
    assert "x9 - a symbol" in many and "x10" not in many


def _stream_fake(monkeypatch, calls):
    """A streamed provider stand-in under _open_ai: replies "S:"+text for
    each text of the request, as Anthropic SSE deltas."""
    import gamma.routers.translate as translate_mod

    class Resp:
        def __init__(self, text):
            self.lines = [f'data: {json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})}\n'.encode(),
                          b"data: [DONE]\n"]

        def __iter__(self):
            return iter(self.lines)

        def close(self):
            pass

    def fake_open(messages, system, entry, rt, **kw):
        batch = texts_of(messages)
        calls["translate"].append({"system": system, "batch": batch, "kw": kw})
        return Resp(segmented([f"S:{t}" for t in batch]))

    monkeypatch.setattr(translate_mod, "_open_ai", fake_open)
    monkeypatch.setattr(translate_mod, "_TRANSLATE_STREAM_INTERVAL", 0)


def test_a_streamed_request_with_the_brief_made_says_nothing_of_it(eve, monkeypatch):
    c, page = eve
    calls = _fake(monkeypatch)
    _stream_fake(monkeypatch, calls)
    _brief(c, page_id=page, lang="pt")
    r = c.post("/api/ai/translate", json={"texts": ["a streamed paragraph"], "lang": "pt", "page_id": page, "stream": True})
    lines = _lines(r)
    assert not any("stage" in line for line in lines)
    assert lines[-1]["translations"] == ["S:a streamed paragraph"] and lines[-1]["brief"] is True
    assert "里德堡阻塞" in calls["translate"][0]["system"]


def test_an_unstored_page_is_named_by_its_id_so_a_selection_finds_the_jobs_brief(eve, monkeypatch):
    c, _ = eve
    calls = _fake(monkeypatch)
    page = c.post("/api/blocks", json={"parent_id": "root", "content": "Source-only paper",
                                       "properties": {"source_url": "https://example.org/paper.pdf"}}).json()["id"]
    assert _brief(c, page_id=page, head="Opening text of a source-only paper.", lang="ko")[-1] == {"ready": True}
    _translate(c, ["a job paragraph"], page_id=page, lang="ko")
    _translate(c, ["a selected sentence"], page_id=page, wait_brief=False, lang="ko")
    assert len(calls["brief"]) == 1
    assert all("里德堡阻塞" in call["system"] for call in calls["translate"])


def test_a_kept_brief_is_found_after_the_memory_lets_it_go(eve, monkeypatch):
    from gamma import translate_context

    c, page = eve
    calls = _fake(monkeypatch)
    _brief(c, page_id=page, lang="ja")
    _translate(c, ["a paragraph cached under its brief"], page_id=page, lang="ja")
    with translate_context._lock:
        translate_context._briefs.clear()
    assert _brief(c, page_id=page, lang="ja") == [{"ready": True}], "found at once, kept with the paper"
    again = _translate(c, ["a paragraph cached under its brief"], page_id=page, lang="ja")
    assert again["cached"] is True and again["brief"] is True
    assert len(calls["brief"]) == 1 and len(calls["translate"]) == 1


def test_a_brief_made_with_keep_off_lives_in_memory_only(eve, monkeypatch):
    from gamma import translate_context

    c, _ = eve
    page = _paper(c, "keep-off")
    calls = _fake(monkeypatch)
    _brief(c, page_id=page, keep=False)
    with translate_context._lock:
        translate_context._briefs.clear()
    assert _brief(c, page_id=page)[-1] == {"ready": True}
    assert len(calls["brief"]) == 2, "nothing was kept, so it is made again"


def test_waiting_is_capped_per_account_and_a_refused_call_is_no_failed_brief():
    from gamma import translate_context
    from gamma.ai_client import TooManyCalls

    gate = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        gate.wait(5)
        return BRIEF

    def brief_for(key, make):
        with translate_context.BriefTurn(key, "acct") as turn:
            return turn.settle(make)

    owner = threading.Thread(target=lambda: brief_for("cap-test", slow))
    owner.start()
    started.wait(5)
    waiters = [threading.Thread(target=lambda: brief_for("cap-test", slow))
               for _ in range(translate_context._BRIEF_WAITERS)]
    for w in waiters:
        w.start()
    deadline = time.monotonic() + 5
    while translate_context._waiting.get("acct", 0) < translate_context._BRIEF_WAITERS and time.monotonic() < deadline:
        time.sleep(0.01)
    t0 = time.monotonic()
    assert translate_context.wait_brief("cap-test", "acct") is None, "past the cap a request goes on without"
    assert brief_for("cap-test", slow)[0] is None
    assert time.monotonic() - t0 < 1
    gate.set()
    owner.join(5)
    for w in waiters:
        w.join(5)
    assert translate_context.made_brief("cap-test") is not None

    def refused():
        raise TooManyCalls(6)

    brief, why = brief_for("refused-test", refused)
    assert brief is None and why.startswith("Too many AI requests")
    assert "refused-test" not in translate_context._failed, "a refusal is not remembered"


def test_the_brief_call_has_room_to_think_at_an_effort_the_user_picked(eve, monkeypatch):
    """At the model's lowest effort the brief's JSON fits in 3,000 tokens; at
    a level the user picked, the model's thinking spends from the same cap,
    so it gets the translation's room for that on top."""
    c, _ = eve
    calls = _fake(monkeypatch)
    _brief(c, page_id=_paper(c, "effort-high"), effort="high")
    _brief(c, page_id=_paper(c, "effort-none"), effort="turbo")
    assert [(kw["effort"], kw["max_tokens"]) for kw in calls["brief_kwargs"]] == [("high", 11000), ("", 3000)]


def test_the_brief_request_claims_the_brief_before_it_says_it_is_under_way(eve, monkeypatch):
    """The viewer sends its chunks the moment it reads {"stage": "brief"}:
    by then the brief must be being made, or they would go without it (or
    wait before their streams start)."""
    from gamma import translate_context

    c, _ = eve
    _fake(monkeypatch)
    being_made = []

    def relay(lines, what="ai", **kw):
        for line in lines:
            if json.loads(line).get("stage") == "brief":
                with translate_context._lock:
                    being_made.append(len(translate_context._making))
            yield line

    monkeypatch.setattr("gamma.routers.translate.keepalive_lines", relay)
    assert _brief(c, page_id=_paper(c, "claim-first")) == [{"stage": "brief"}, {"ready": True}]
    assert being_made == [1]
    assert not translate_context._making


def test_a_turn_left_unsettled_lets_its_claim_go():
    """A brief request whose client went away before it got to the call (its
    stream closed at the first line) leaves nothing being made, and its
    waiters are no longer counted."""
    from gamma import translate_context

    with translate_context.BriefTurn("unsettled-test", "acct") as owner:
        assert owner.pending and translate_context.brief_making("unsettled-test")
        with translate_context.BriefTurn("unsettled-test", "acct") as waiter:
            assert waiter.pending and translate_context._waiting["acct"] == 1
        assert "acct" not in translate_context._waiting
    assert not translate_context.brief_making("unsettled-test")
    assert "unsettled-test" not in translate_context._failed


def test_a_streamed_request_never_waits_for_the_brief_before_its_stream_starts(eve, monkeypatch):
    """A brief claimed just after a streamed request looked (so it did not
    start its stream to wait in) is not waited for outside the stream: the
    request goes on without it at once, and says so."""
    from gamma import translate_context

    c, _ = eve
    page = _paper(c, "late-claim")
    gate, started = threading.Event(), threading.Event()

    def while_making():
        started.set()
        gate.wait(10)

    calls = _fake(monkeypatch, while_making=while_making)
    _stream_fake(monkeypatch, calls)
    maker, made = _in_background(c, page_id=page)
    maker.start()
    assert started.wait(5)
    # The request looks before the brief request's claim lands.
    monkeypatch.setattr(translate_context, "brief_making", lambda key: False)
    out = []
    chunk = threading.Thread(target=lambda: out.append(_lines(c.post(
        "/api/ai/translate", json={"texts": ["a paragraph sent as the brief starts"], "lang": "zh-CN",
                                   "page_id": page, "stream": True}))))
    try:
        chunk.start()
        chunk.join(5)
        assert not chunk.is_alive(), "the streamed request waited for the brief before its stream"
    finally:
        gate.set()
        maker.join(10)
        chunk.join(10)
    lines = out[0]
    assert not any("stage" in line for line in lines)
    assert lines[-1]["translations"] == ["S:a paragraph sent as the brief starts"] and lines[-1]["brief"] is False
    assert made == [[{"stage": "brief"}, {"ready": True}]]
