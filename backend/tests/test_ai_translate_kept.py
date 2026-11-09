"""Kept translations (gamma/translate_store.py): a stored paper's translations
and brief kept in the workspace's data.db, read before the server's memory,
written when the request asks to keep them and its account may
change the workspace, counted, cleared and forgotten through
/api/translations, and dropped with the PDF (pdf_index.purge_unused)."""

import json
import sqlite3
import threading
from contextlib import closing

import pytest

from ai_fixtures import JsonResp, segmented, texts_of
from conftest import account_of, login, make_user, workspace_of

MODEL = "claude-test"
BRIEF_SYSTEM = "You prepare a translator's brief"
BRIEF = json.dumps({"field": "atomic physics",
                    "terms": [{"source": "Rydberg blockade", "target": "里德堡阻塞"}], "keep": ["EIT"]})


def _pdf(text):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text, fontsize=11)
    return doc.tobytes()


def _in(ws):
    return {"X-Gamma-Workspace": ws} if ws else None


def _paper(c, tag, ws=None):
    """A page with a stored PDF of its own: (page id, doc id)."""
    up = c.post("/api/uploads", files={"file": (f"{tag}.pdf", _pdf(f"A paper about {tag}."), "application/pdf")},
                headers=_in(ws)).json()
    page = c.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": f"Paper {tag}"}, headers=_in(ws)).json()
    return page["id"], up["doc_id"]


def _account(name):
    make_user(name, "pw")
    c = login(name, "pw")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-ant-key-4321", "models": MODEL})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="module")
def frank(client):
    """An account with an Anthropic provider."""
    return _account("tkept_frank")


@pytest.fixture(scope="module")
def lab(frank):
    """A shared workspace frank owns and tkept_vera (with a provider of her
    own) only views: (vera's client, the workspace)."""
    from gamma import workspaces

    vera = _account("tkept_vera")
    info = workspaces.create("Kept lab", account_of("tkept_frank"), kind="shared")
    workspaces.set_member(info["id"], account_of("tkept_vera"), "viewer", by=account_of("tkept_frank"))
    return vera, info["id"]


def _fake(monkeypatch):
    """A _call_ai stand-in: the brief call answers BRIEF, a translation
    "T:"+text; returns the translation batches it was sent."""
    batches = []

    def fake(messages, system, entry, rt, **kw):
        if system.startswith(BRIEF_SYSTEM):
            return BRIEF
        batches.append(texts_of(messages))
        return segmented([f"T:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    return batches


def _translate(c, texts, ws=None, **body):
    r = c.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN", **body}, headers=_in(ws))
    assert r.status_code == 200, r.text
    return r.json()


def _restart():
    """What a restart does to the server's memory: no paragraph, no brief."""
    from gamma import translate, translate_context

    with translate._LOCK:
        translate._CACHE.clear()
    with translate_context._lock:
        translate_context._briefs.clear()


def _kept(ws, doc, texts, lang="zh-CN", model=MODEL, need_brief=False):
    """The kept translations of ``texts``, by text."""
    from gamma import translate_store

    found = translate_store.lookup(ws, doc, lang, model, [translate_store.src_hash(t) for t in texts], need_brief)
    return {t: found[translate_store.src_hash(t)] for t in texts if translate_store.src_hash(t) in found}


def test_kept_translations_are_served_after_a_restart(frank, monkeypatch):
    page, doc = _paper(frank, "restart")
    batches = _fake(monkeypatch)
    _translate(frank, ["kept one", "kept two"], page_id=page)
    _restart()
    out = _translate(frank, ["kept two", "kept one", "new three"], page_id=page)
    assert batches == [["kept one", "kept two"], ["new three"]]
    assert out["translations"] == ["T:kept two", "T:kept one", "T:new three"]
    assert _kept(workspace_of("tkept_frank"), doc, ["kept one", "new three"]) == {
        "kept one": "T:kept one", "new three": "T:new three"}
    # All found: a cached answer, also streamed.
    _restart()
    r = frank.post("/api/ai/translate", json={"texts": ["new three"], "lang": "zh-CN", "page_id": page, "stream": True})
    assert json.loads(r.text.splitlines()[-1])["cached"] is True
    assert len(batches) == 2


def test_keep_off_writes_nothing(frank, monkeypatch):
    page, doc = _paper(frank, "keep-off")
    batches = _fake(monkeypatch)
    _translate(frank, ["not to be kept"], page_id=page, keep=False)
    assert _kept(workspace_of("tkept_frank"), doc, ["not to be kept"]) == {}
    _restart()
    _translate(frank, ["not to be kept"], page_id=page, keep=False)
    assert batches == [["not to be kept"], ["not to be kept"]]


def test_a_viewer_reads_kept_translations_but_keeps_none(frank, lab, monkeypatch):
    vera, ws = lab
    page, doc = _paper(frank, "lab", ws)
    batches = _fake(monkeypatch)
    _translate(vera, ["a viewer's paragraph"], ws, page_id=page)
    assert _kept(ws, doc, ["a viewer's paragraph"]) == {}
    _translate(frank, ["an owner's paragraph"], ws, page_id=page)
    _restart()
    out = _translate(vera, ["an owner's paragraph"], ws, page_id=page)
    assert out["cached"] is True and out["translations"] == ["T:an owner's paragraph"]
    assert len(batches) == 2


def test_a_row_made_with_the_brief_is_never_replaced_by_one_made_without(frank):
    from gamma import translate_store

    ws, doc = workspace_of("tkept_frank"), "b1" * 12
    translate_store.put(ws, doc, "zh-CN", MODEL, [("h1", "with the brief")], brief=True)
    translate_store.put(ws, doc, "zh-CN", MODEL, [("h1", "without"), ("h2", "without")], brief=False)
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["h1", "h2"]) == {"h1": "with the brief", "h2": "without"}
    # need_brief: only the rows made with the brief.
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["h1", "h2"], need_brief=True) == {"h1": "with the brief"}
    translate_store.put(ws, doc, "zh-CN", MODEL, [("h2", "now with it")], brief=True)
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["h2"], need_brief=True) == {"h2": "now with it"}
    # Another language or model is another row.
    assert translate_store.lookup(ws, doc, "ja", MODEL, ["h1"]) == {}
    assert translate_store.lookup(ws, doc, "zh-CN", "other-model", ["h1"]) == {}


def test_a_replacing_put_takes_the_place_of_a_row_made_with_the_brief(frank):
    from gamma import translate_store

    ws, doc = workspace_of("tkept_frank"), "f1" * 12
    translate_store.put(ws, doc, "zh-CN", MODEL, [("h1", "with the brief")], brief=True)
    translate_store.put(ws, doc, "zh-CN", MODEL, [("h1", "asked afresh")], brief=False, replace=True)
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["h1"]) == {"h1": "asked afresh"}
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["h1"], need_brief=True) == {}


def _rounds(monkeypatch, fail=()):
    """A _call_ai stand-in whose every call answers in new words ("T1:", "T2:"
    …), and none at all for a text in ``fail``; returns the batches sent."""
    batches = []

    def fake(messages, system, entry, rt, **kw):
        if system.startswith(BRIEF_SYSTEM):
            return BRIEF
        batches.append(texts_of(messages))
        n = len(batches)
        return segmented(["" if t in fail else f"T{n}:{t}" for t in texts_of(messages)])

    monkeypatch.setattr("gamma.routers.translate._call_ai", fake)
    return batches


def test_fresh_asks_the_model_again_and_its_answer_replaces_memory_and_the_kept_row(frank, monkeypatch):
    page, doc = _paper(frank, "fresh")
    batches = _rounds(monkeypatch)
    text = ["a paragraph to translate again"]
    assert _translate(frank, text, page_id=page)["translations"] == ["T1:" + text[0]]
    # Asked again plainly, memory answers; afresh, the model does.
    assert _translate(frank, text, page_id=page)["cached"] is True
    out = _translate(frank, text, page_id=page, fresh=True)
    assert out["cached"] is False and out["translations"] == ["T2:" + text[0]]
    assert batches == [text, text]
    # The new words are what memory and the kept rows hold from now on.
    assert _translate(frank, text, page_id=page)["translations"] == ["T2:" + text[0]]
    _restart()
    assert _translate(frank, text, page_id=page)["translations"] == ["T2:" + text[0]]
    assert _kept(workspace_of("tkept_frank"), doc, text) == {text[0]: "T2:" + text[0]}
    assert len(batches) == 2


def test_a_fresh_answer_reaches_the_other_editors_of_the_workspace_at_once(frank, monkeypatch):
    """Memory is per account; the kept rows are the workspace's and answer
    first, so another editor's memory never serves the words a Translate
    again replaced."""
    from gamma import workspaces

    ed = _account("tkept_ed")
    info = workspaces.create("Kept fresh lab", account_of("tkept_frank"), kind="shared")
    workspaces.set_member(info["id"], account_of("tkept_ed"), "editor", by=account_of("tkept_frank"))
    ws = info["id"]
    page, doc = _paper(frank, "fresh-shared", ws)
    batches = _rounds(monkeypatch)
    text = ["a shared paragraph"]
    assert _translate(frank, text, ws, page_id=page)["translations"] == ["T1:" + text[0]]
    # The other editor is served the kept row, which goes into his memory.
    assert _translate(ed, text, ws, page_id=page)["translations"] == ["T1:" + text[0]]
    assert _translate(frank, text, ws, page_id=page, fresh=True)["translations"] == ["T2:" + text[0]]
    assert _kept(ws, doc, text) == {text[0]: "T2:" + text[0]}
    assert _translate(ed, text, ws, page_id=page)["translations"] == ["T2:" + text[0]]
    assert len(batches) == 2


def test_fresh_without_the_brief_replaces_a_row_made_with_it(frank, monkeypatch):
    from gamma import translate_store

    page, doc = _paper(frank, "fresh-brief")
    batches = _rounds(monkeypatch)
    text = ["kept with the paper's brief"]
    ws = workspace_of("tkept_frank")
    translate_store.put(ws, doc, "zh-CN", MODEL, [(translate_store.src_hash(text[0]), "made with the brief")], brief=True)
    # The paper has no brief made, and the request does not wait for one.
    out = _translate(frank, text, page_id=page, fresh=True, wait_brief=False)
    assert out["brief"] is False and out["translations"] == ["T1:" + text[0]]
    assert _kept(ws, doc, text) == {text[0]: "T1:" + text[0]}
    assert len(batches) == 1


def test_a_fresh_text_that_still_fails_leaves_what_was_kept(frank, monkeypatch):
    page, doc = _paper(frank, "fresh-fail")
    _rounds(monkeypatch)
    text = ["a paragraph that will not translate again"]
    _translate(frank, text, page_id=page)
    batches = _rounds(monkeypatch, fail=set(text))
    out = _translate(frank, text, page_id=page, fresh=True)
    # Asked and asked again, it comes back as itself and named as failed.
    assert len(batches) == 2 and out["failed"] == [0] and out["translations"] == text
    assert _translate(frank, text, page_id=page)["translations"] == ["T1:" + text[0]]
    _restart()
    assert _kept(workspace_of("tkept_frank"), doc, text) == {text[0]: "T1:" + text[0]}


def test_a_request_with_the_brief_translates_again_what_was_kept_without_it(frank, monkeypatch):
    page, doc = _paper(frank, "need-brief")
    batches = _fake(monkeypatch)
    text = ["made before the brief"]
    assert _translate(frank, text, page_id=page)["brief"] is False
    assert frank.post("/api/ai/translate/brief", json={"lang": "zh-CN", "page_id": page}).status_code == 200
    _restart()
    out = _translate(frank, text, page_id=page)
    assert batches == [text, text] and out["brief"] is True
    assert _kept(workspace_of("tkept_frank"), doc, text, need_brief=True) == {text[0]: "T:" + text[0]}


def test_rows_past_the_cap_are_skipped(frank, monkeypatch):
    from gamma import translate_store

    monkeypatch.setattr(translate_store, "MAX_ROWS", 3)
    ws, doc = workspace_of("tkept_frank"), "c2" * 12
    translate_store.put(ws, doc, "zh-CN", MODEL, [("r1", "1"), ("r2", "2")], brief=False)
    translate_store.put(ws, doc, "zh-CN", MODEL, [("r2", "2 again"), ("r3", "3"), ("r4", "4")], brief=False)
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["r1", "r2", "r3", "r4"]) == {
        "r1": "1", "r2": "2 again", "r3": "3"}


def test_stats_clear_and_forget(monkeypatch, client):
    """A workspace of its own, so its counts are this test's."""
    from gamma.translate_context import parse_brief

    c = _account("tkept_sam")
    ws = workspace_of("tkept_sam")
    batches = _fake(monkeypatch)
    page_a, doc_a = _paper(c, "stats-a")
    page_b, _ = _paper(c, "stats-b")
    assert c.get("/api/translations/stats").json() == {"documents": 0, "paragraphs": 0, "bytes": 0}
    c.post("/api/ai/translate/brief", json={"lang": "zh-CN", "page_id": page_a})
    _translate(c, ["α one", "α two"], page_id=page_a)
    _translate(c, ["β one"], page_id=page_b)
    size = sum(len(f"T:{t}".encode()) for t in ("α one", "α two", "β one")) + len(parse_brief(BRIEF).encode())
    assert c.get("/api/translations/stats").json() == {"documents": 2, "paragraphs": 3, "bytes": size}

    # Forgetting a document drops what is kept of it and what memory holds:
    # its next job translates afresh, and its brief is to be made again.
    assert c.delete(f"/api/translations/{doc_a}").json() == {"removed": 2}
    assert c.get("/api/translations/stats").json()["paragraphs"] == 1
    again = _translate(c, ["α one"], page_id=page_a)
    assert batches[-1] == ["α one"] and again["brief"] is False
    assert _translate(c, ["β one"], page_id=page_b)["cached"] is True, "the other document is untouched"

    # Clearing the workspace drops all it keeps and what memory holds of
    # every document: each translates afresh.
    assert c.delete("/api/translations").json() == {"removed": 2}
    assert c.get("/api/translations/stats").json() == {"documents": 0, "paragraphs": 0, "bytes": 0}
    assert _kept(ws, doc_a, ["α one"]) == {}
    asked = len(batches)
    assert _translate(c, ["β one"], page_id=page_b)["cached"] is False
    assert _translate(c, ["α one"], page_id=page_a)["cached"] is False
    assert batches[asked:] == [["β one"], ["α one"]]


def test_a_duplicated_page_is_the_paper_it_was_copied_from(frank, monkeypatch):
    """A duplicated page carries only the stored upload's URL, no doc_id
    (blocks_store.attachment_file): the viewer counts it as stored, so the
    server reads it as that file, kept with it, and forgetting the file's
    translation (the menu on the copy) makes the copy translate afresh."""
    ws = workspace_of("tkept_frank")
    page, doc = _paper(frank, "duplicated")
    r = frank.post("/api/blocks", json={"parent_id": "root", "content": "Paper duplicated (copy)",
                                        "properties": {"source_url": f"/api/uploads/{doc}.pdf"}})
    assert r.status_code == 200, r.text
    copy = r.json()["id"]
    batches = _fake(monkeypatch)
    _translate(frank, ["copied one"], page_id=copy)
    assert _kept(ws, doc, ["copied one"]) == {"copied one": "T:copied one"}
    _restart()
    assert _translate(frank, ["copied one"], page_id=page)["cached"] is True, "the original finds it"
    assert frank.delete(f"/api/translations/{doc}").json() == {"removed": 1}
    assert _translate(frank, ["copied one"], page_id=copy)["cached"] is False
    assert batches == [["copied one"], ["copied one"]]


def test_forgetting_a_pdf_without_a_stored_copy_makes_its_page_translate_afresh(frank, monkeypatch):
    """A PDF opened from its link without a copy on the server keeps nothing:
    its translations live in memory under its page, which the forget names
    beside the file id it would have had."""
    r = frank.post("/api/blocks", json={"parent_id": "root", "content": "Linked paper",
                                        "properties": {"source_url": "https://example.org/paper.pdf"}})
    assert r.status_code == 200, r.text
    page = r.json()["id"]
    batches = _fake(monkeypatch)
    _translate(frank, ["linked one"], page_id=page)
    assert _translate(frank, ["linked one"], page_id=page)["cached"] is True
    assert frank.delete(f"/api/translations/{'0' * 24}").json() == {"removed": 0}
    assert _translate(frank, ["linked one"], page_id=page)["cached"] is True, "the file id alone names no memory"
    assert frank.delete(f"/api/translations/{'0' * 24}?page={page}").json() == {"removed": 0}
    assert _translate(frank, ["linked one"], page_id=page)["cached"] is False
    assert batches == [["linked one"], ["linked one"]]
    assert frank.delete(f"/api/translations/{'0' * 24}?page={'x' * 65}").status_code == 400


def test_only_an_editor_clears_or_forgets_and_the_id_is_checked(frank, lab):
    vera, ws = lab
    assert vera.get("/api/translations/stats", headers=_in(ws)).status_code == 200
    assert vera.delete("/api/translations", headers=_in(ws)).status_code == 403
    assert vera.delete("/api/translations/" + "d" * 24, headers=_in(ws)).status_code == 403
    assert frank.delete("/api/translations/bad%20id").status_code == 400
    make_user("tkept_mallory", "pw")
    assert frank.get("/api/translations/stats", headers=_in(workspace_of("tkept_mallory"))).status_code == 403


def test_a_forget_during_a_request_drops_what_it_would_keep(frank):
    from gamma import translate_store
    from gamma.translate import Kept, forget, generation

    ws, doc = workspace_of("tkept_frank"), "e3" * 12
    kept = Kept(ws, doc, "zh-CN", MODEL, write=True, generation=generation(ws, doc))
    forget(ws, doc)
    kept.put([("h", "from before the forget")], brief=True)
    kept.put_brief("a brief from before the forget")
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["h"]) == {}
    assert translate_store.get_brief(ws, doc, "zh-CN", MODEL) is None


def _forget_while_writing(monkeypatch, doc, store_write, write, drop=None, ws=None):
    """Forget ``doc`` (or ``drop(ws, doc)`` instead) while ``write(kept)`` is
    inside the store's ``store_write`` (its check passed already), held
    there until the forget has had its chance to run. In frank's workspace
    unless ``ws`` names another."""
    from gamma import translate_store
    from gamma.translate import Kept, forget, generation

    ws = ws or workspace_of("tkept_frank")
    drop = drop or forget
    real = getattr(translate_store, store_write)
    entered, gate = threading.Event(), threading.Event()

    def held(*a, **kw):
        entered.set()
        gate.wait(5)
        return real(*a, **kw)

    monkeypatch.setattr(translate_store, store_write, held)
    kept = Kept(ws, doc, "zh-CN", MODEL, write=True, generation=generation(ws, doc))
    writer = threading.Thread(target=write, args=(kept,))
    writer.start()
    assert entered.wait(5)
    forgetting = threading.Thread(target=drop, args=(ws, doc))
    forgetting.start()
    forgetting.join(0.3)
    gate.set()
    writer.join(5)
    forgetting.join(5)
    monkeypatch.setattr(translate_store, store_write, real)
    return ws


def test_a_forget_waits_for_a_kept_write_under_way(frank, monkeypatch):
    """A write whose check passed before the forget lands before the
    forget's delete, not after it, so the forgotten rows stay gone."""
    from gamma import translate_store

    doc = "e4" * 12
    ws = _forget_while_writing(monkeypatch, doc, "put",
                               lambda kept: kept.put([("w", "written as the forget came")], brief=True))
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["w"]) == {}
    doc = "e5" * 12
    ws = _forget_while_writing(monkeypatch, doc, "put_brief",
                               lambda kept: kept.put_brief("a brief written as the forget came"))
    assert translate_store.get_brief(ws, doc, "zh-CN", MODEL) is None


def test_a_clear_waits_for_a_kept_write_under_way_and_drops_what_comes_after(monkeypatch, client):
    """As a forget does: a write whose check passed lands before the
    clear's delete, and one whose request began before the clear is
    dropped. A workspace of its own, since a clear takes all of it."""
    from gamma import translate_store
    from gamma.translate import Kept, clear, generation

    _account("tkept_clara")
    ws, doc = workspace_of("tkept_clara"), "e6" * 12
    _forget_while_writing(monkeypatch, doc, "put",
                          lambda kept: kept.put([("c", "written as the clear came")], brief=True),
                          drop=lambda w, d: clear(w), ws=ws)
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["c"]) == {}

    kept = Kept(ws, doc, "zh-CN", MODEL, write=True, generation=generation(ws, doc))
    clear(ws)
    assert generation(ws, doc) > kept.generation
    kept.put([("c", "from before the clear")], brief=True)
    kept.put_brief("a brief from before the clear")
    assert translate_store.lookup(ws, doc, "zh-CN", MODEL, ["c"]) == {}
    assert translate_store.get_brief(ws, doc, "zh-CN", MODEL) is None


def test_a_request_reads_the_documents_generation_once(frank, monkeypatch):
    """A forget just after a request began (between its reads, were there
    two) still drops what the request would keep: its memory keys and its
    kept writes go by the one generation it read."""
    import gamma.routers.translate as translate_mod
    from gamma import translate_store
    from gamma.translate import forget

    page, doc = _paper(frank, "read-once")
    ws = workspace_of("tkept_frank")
    _fake(monkeypatch)
    real, armed = translate_mod.generation, []

    def read_then_forget(w, d):
        g = real(w, d)
        if armed and d == doc:
            armed.clear()
            forget(w, d)
        return g

    monkeypatch.setattr(translate_mod, "generation", read_then_forget)
    armed.append(1)
    frank.post("/api/ai/translate/brief", json={"lang": "zh-CN", "page_id": page})
    assert translate_store.get_brief(ws, doc, "zh-CN", MODEL) is None
    armed.append(1)
    _translate(frank, ["a paragraph translated as it is forgotten"], page_id=page)
    assert _kept(ws, doc, ["a paragraph translated as it is forgotten"]) == {}


def test_a_services_translations_are_kept_as_made_with_a_brief(frank, monkeypatch):
    import gamma.translate_engines as engines

    frank.put("/api/translate/engines/google", json={"fields": {"api_key": "AIza-kept-key-1234"}})
    asked = []

    def fake(req, timeout=None):
        asked.append(json.loads(req.data)["q"])
        return JsonResp({"data": {"translations": [{"translatedText": f"G:{t}"} for t in json.loads(req.data)["q"]]}})

    monkeypatch.setattr(engines, "urlopen", fake)
    page, doc = _paper(frank, "service")
    out = _translate(frank, ["a sentence for the service"], page_id=page, model="engine:google")
    assert out["brief"] is True
    assert _kept(workspace_of("tkept_frank"), doc, ["a sentence for the service"], model="engine:google",
                 need_brief=True) == {"a sentence for the service": "G:a sentence for the service"}
    _restart()
    assert _translate(frank, ["a sentence for the service"], page_id=page, model="engine:google")["cached"] is True
    assert len(asked) == 1


def _lookup(c, texts, ws=None, **body):
    r = c.post("/api/translations/lookup", json={"texts": texts, "lang": "zh-CN", **body}, headers=_in(ws))
    assert r.status_code == 200, r.text
    return r.json()


def _no_model(monkeypatch):
    """Any model call, a service's included, fails the test."""
    import gamma.translate_engines as engines

    def called(*a, **kw):
        raise AssertionError("the lookup called a model")

    monkeypatch.setattr("gamma.routers.translate._call_ai", called)
    monkeypatch.setattr("gamma.routers.translate._open_ai", called)
    monkeypatch.setattr(engines, "urlopen", called)


def test_the_lookup_answers_kept_rows_and_none_where_nothing_is_kept(frank, monkeypatch):
    from gamma import translate
    from gamma.ai_settings import ai_runtime

    page, doc = _paper(frank, "lookup")
    _fake(monkeypatch)
    _translate(frank, ["looked up one", "looked up two"], page_id=page)
    _restart()
    _no_model(monkeypatch)
    rid = ai_runtime(account_of("tkept_frank"))["models"][0]["id"]
    for model in (rid, MODEL, ""):
        out = _lookup(frank, ["looked up two", "never kept", "  ", "looked up one"], page_id=page, model=model)
        assert out == {"translations": ["T:looked up two", None, None, "T:looked up one"], "model": MODEL}
    # Another language or model keeps none of them.
    assert _lookup(frank, ["looked up one"], page_id=page, lang="ja")["translations"] == [None]
    assert _lookup(frank, ["looked up one"], page_id=page, model="other-model")["translations"] == [None]
    # No page, or a page with no stored file: nothing is kept.
    assert _lookup(frank, ["looked up one"])["translations"] == [None]
    linked = frank.post("/api/blocks", json={"parent_id": "root", "content": "Linked lookup",
                                             "properties": {"source_url": "https://example.org/l.pdf"}}).json()["id"]
    assert _lookup(frank, ["looked up one"], page_id=linked)["translations"] == [None]
    # The lookup never fills the account's memory.
    with translate._LOCK:
        assert not translate._CACHE


def test_a_reader_with_no_ai_reads_what_the_workspace_keeps(frank, lab, monkeypatch):
    """A viewer, and a member with no AI provider at all: the rows are found
    by the model's registry id, its bare name or a shared entry's id, with no
    runtime needed. What a viewer translated stays in its memory, not
    among the rows, so the lookup does not find it."""
    from gamma import workspaces

    vera, ws = lab
    make_user("tkept_noai", "pw")
    noai = login("tkept_noai", "pw")
    workspaces.set_member(ws, account_of("tkept_noai"), "viewer", by=account_of("tkept_frank"))
    page, _ = _paper(frank, "lookup-lab", ws)
    _fake(monkeypatch)
    _translate(frank, ["kept by the owner"], ws, page_id=page)
    _translate(vera, ["in the viewer's memory"], ws, page_id=page)
    _no_model(monkeypatch)
    texts = ["kept by the owner", "in the viewer's memory"]
    assert _lookup(vera, texts, ws, page_id=page, model=MODEL)["translations"] == ["T:kept by the owner", None]
    for model in ("Ab3_x-9z:" + MODEL, MODEL, "server:Zq8_k-2w:" + MODEL):
        out = _lookup(noai, texts, ws, page_id=page, model=model)
        assert out == {"translations": ["T:kept by the owner", None], "model": MODEL}
    # No model named and none to default to: nothing to look under.
    assert _lookup(noai, texts, ws, page_id=page) == {"translations": [None, None], "model": ""}
    # The AI routes need a provider; the lookup does not.
    r = noai.post("/api/ai/translate", json={"texts": texts, "lang": "zh-CN", "page_id": page, "model": MODEL},
                  headers=_in(ws))
    assert r.status_code == 503


def test_a_lookup_by_registry_id_builds_no_ai_runtime(frank, monkeypatch):
    """What the viewer sends, a registry id, names its model by itself: no
    key is decrypted and no sign-in refreshed (ai_runtime) on the path of a
    seed. A bare name, which only the runtime can tell, still goes through
    it, a colon of its own and all."""
    from gamma import ai_settings

    page, _ = _paper(frank, "lookup-no-runtime")
    _fake(monkeypatch)
    _translate(frank, ["read without a runtime"], page_id=page)
    rid = ai_settings.ai_runtime(account_of("tkept_frank"))["models"][0]["id"]
    _no_model(monkeypatch)

    def built(*a, **kw):
        raise AssertionError("the lookup built an AI runtime")

    monkeypatch.setattr("gamma.routers.translate.ai_runtime", built)
    for model in (rid, "Gone_id-:" + MODEL, "server:Zq8_k-2w:" + MODEL):
        out = _lookup(frank, ["read without a runtime"], page_id=page, model=model)
        assert out == {"translations": ["T:read without a runtime"], "model": MODEL}, model
    monkeypatch.setattr("gamma.routers.translate.ai_runtime", ai_settings.ai_runtime)
    assert _lookup(frank, ["read without a runtime"], page_id=page, model="qwen2.5:7b")["model"] == "qwen2.5:7b"


def test_the_lookup_reads_a_service_s_rows_under_its_id(frank, monkeypatch):
    import gamma.translate_engines as engines

    frank.put("/api/translate/engines/google", json={"fields": {"api_key": "AIza-lookup-key-1234"}})
    monkeypatch.setattr(engines, "urlopen", lambda req, timeout=None: JsonResp(
        {"data": {"translations": [{"translatedText": f"G:{t}"} for t in json.loads(req.data)["q"]]}}))
    page, _ = _paper(frank, "lookup-service")
    _translate(frank, ["a sentence a service kept"], page_id=page, model="engine:google")
    _no_model(monkeypatch)
    out = _lookup(frank, ["a sentence a service kept"], page_id=page, model="engine:google")
    assert out == {"translations": ["G:a sentence a service kept"], "model": "engine:google"}
    assert _lookup(frank, ["a sentence a service kept"], page_id=page, model=MODEL)["translations"] == [None]


def test_the_lookup_stays_in_its_workspace(frank, monkeypatch):
    page, _ = _paper(frank, "lookup-mine")
    _fake(monkeypatch)
    _translate(frank, ["frank's own paragraph"], page_id=page)
    make_user("tkept_oscar", "pw")
    oscar = login("tkept_oscar", "pw")
    # Frank's page named from oscar's own workspace: no such page there.
    assert _lookup(oscar, ["frank's own paragraph"], page_id=page, model=MODEL)["translations"] == [None]
    # Frank's workspace: oscar is no member.
    r = oscar.post("/api/translations/lookup", json={"texts": ["x"], "lang": "zh-CN", "page_id": page},
                   headers=_in(workspace_of("tkept_frank")))
    assert r.status_code == 403


def test_the_lookup_has_the_translation_request_s_caps(frank):
    def status(**body):
        return frank.post("/api/translations/lookup", json={"lang": "zh-CN", "page_id": "p", **body}).status_code

    assert status(texts=[]) == 400
    assert status(texts=["x"] * 201) == 400
    assert status(texts=[1]) == 400
    assert status(texts=["x" * 30001, "y" * 30000]) == 413
    assert status(texts=["x"], lang="xx") == 400
    assert status(texts=["x"] * 200) == 200


def test_purge_unused_drops_the_kept_translations_of_a_pdf_no_page_carries(frank, monkeypatch):
    from gamma import pdf_index, translate_store
    from gamma.db import connect_pages_db

    ws = workspace_of("tkept_frank")
    page, live = _paper(frank, "purge")
    gone = "f4" * 12  # a stored paper whose page was deleted for good
    _fake(monkeypatch)
    _translate(frank, ["a paragraph of a live paper"], page_id=page)
    translate_store.put(ws, gone, "zh-CN", MODEL, [("h", "of a deleted paper")], brief=True)
    translate_store.put_brief(ws, gone, "zh-CN", MODEL, "a deleted paper's brief")
    with closing(connect_pages_db(ws)) as conn:
        pdf_index.purge_unused(ws, conn)
    assert translate_store.lookup(ws, gone, "zh-CN", MODEL, ["h"]) == {}
    assert translate_store.get_brief(ws, gone, "zh-CN", MODEL) is None
    assert _kept(ws, live, ["a paragraph of a live paper"]) == {"a paragraph of a live paper": "T:a paragraph of a live paper"}


def test_a_duplicated_page_keeps_the_kept_translations_of_the_file_it_shows(frank, monkeypatch):
    """A copy carries only the upload's address, and shows the same file
    with the same kept translations. The original deleted for good takes
    neither away; the last page showing the file deleted for good does."""
    from gamma.db import connect_pages_db
    from gamma import pdf_index

    ws = workspace_of("tkept_frank")
    page, doc = _paper(frank, "duplicated")
    copy = frank.post("/api/blocks", json={"parent_id": "root", "content": "Paper duplicated (copy)",
                                           "properties": {"source_url": f"/api/uploads/{doc}.pdf"}}).json()["id"]
    batches = _fake(monkeypatch)
    text = "a paragraph translated on the copy"
    _translate(frank, [text], page_id=copy)
    assert _kept(ws, doc, [text]) == {text: f"T:{text}"}

    assert frank.delete(f"/api/blocks/{page}").status_code == 200
    assert frank.delete(f"/api/trash/{page}").status_code == 200
    assert _kept(ws, doc, [text]) == {text: f"T:{text}"}
    assert frank.get(f"/api/uploads/{doc}.pdf").status_code == 200
    _restart()
    assert _translate(frank, [text], page_id=copy)["cached"] is True
    assert batches == [[text]]

    # In Recently deleted the copy can still come back, with them.
    assert frank.delete(f"/api/blocks/{copy}").status_code == 200
    with closing(connect_pages_db(ws)) as conn:
        pdf_index.purge_unused(ws, conn)
    assert _kept(ws, doc, [text]) == {text: f"T:{text}"}

    assert frank.delete(f"/api/trash/{copy}").status_code == 200
    assert _kept(ws, doc, [text]) == {}


@pytest.mark.parametrize("tag,stored", [
    ("list", '["source_url"]'),
    ("string", '"source_url"'),
    pytest.param("json5", '{"source_url": "x",}', marks=pytest.mark.skipif(
        sqlite3.sqlite_version_info < (3, 45), reason="this SQLite refuses JSON5 as properties")),
])
def test_properties_no_parser_reads_as_an_object_never_stop_the_sweep(frank, tag, stored):
    """A merged backup copies rows as they are, so a row may hold what no
    writer here stores. It carries no paper, and the sweep goes on."""
    from gamma import pdf_index, translate_store
    from gamma.db import connect_pages_db

    ws = workspace_of("tkept_frank")
    page, live = _paper(frank, tag)
    gone = "f5" * 12
    translate_store.put(ws, gone, "zh-CN", MODEL, [("h", "of a deleted paper")], brief=True)
    with closing(connect_pages_db(ws)) as conn:
        conn.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                     "updated_at, page_id) VALUES ('oddrow', ?, 'a', '', ?, '', '', ?)", (page, stored, page))
        conn.commit()
        try:
            assert live in pdf_index._live_docs(conn)
            pdf_index.purge_unused(ws, conn)
        finally:
            conn.execute("DELETE FROM unified_blocks WHERE id = 'oddrow'")
            conn.commit()
    assert translate_store.lookup(ws, gone, "zh-CN", MODEL, ["h"]) == {}


def test_a_broken_store_never_fails_a_translation(frank, monkeypatch):
    from gamma import translate_store

    def broken(ws):
        raise sqlite3.OperationalError("disk I/O error")

    page, _ = _paper(frank, "broken")
    batches = _fake(monkeypatch)
    monkeypatch.setattr(translate_store, "connect_data_db", broken)
    out = _translate(frank, ["translated all the same"], page_id=page)
    assert out["translations"] == ["T:translated all the same"] and batches == [["translated all the same"]]
    assert frank.get("/api/translations/stats").json() == {"documents": 0, "paragraphs": 0, "bytes": 0}
