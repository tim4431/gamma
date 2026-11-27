"""Forgetting the recognized text (routers/ocr.py POST /api/jobs/ocr-drop,
gamma/ocr_run.py ``forget_recognized`` and ``unapply``, highlight_relocate
``respell_quotes(back=True)``): the PDF's own text read again everywhere
(the page text, search, the highlights' quotes where it reads under them,
a picture page's left with the recognized words), its row kept with a
stamp never seen before, automatic recognition declining the file, no
engine needed, who may and when (one OCR job of a file at a time, whatever
its kind); and telling the open tabs (ocr_run ``announce``, collab
``publish_file``): after an apply that staged anything and after a forget,
even when the finishing steps fail, to the file's pages and the members'
other tabs, never another page's share visitors. A fake engine
(tests/ocr_fake.py)."""

import threading

import pytest
from conftest import doc_props, drop_user, fresh_client, login, make_user, recv, recv_hello, workspace_of
from fastapi import HTTPException
from ocr_fake import LIGHT, install, layered_scan
from ocr_fixtures import PRINTED, printed_scan_pdf
from test_ocr_api import _in, scan_page, start

from gamma import collab, highlight_relocate, jobs, ocr_auto, ocr_engine, ocr_run, pdf_ocr, pdf_text
from gamma.db import connect_data_db
from gamma.routers.ocr import ELSEWHERE, FORGET_GUESTS, FORGETTING, NOTHING_RECOGNIZED, RECOGNIZING
from gamma.search import index

USER, PW = "ofg_fay", "ofg-pass-12345"
LINES = {n: text for n, (_x, _y, _size, text) in enumerate(PRINTED, start=1)}


@pytest.fixture(scope="module")
def fay(client):
    make_user(USER, PW)
    return login(USER, PW)


@pytest.fixture(scope="module")
def lab(client, fay):
    """A shared workspace fay owns and gus edits: ``(ws, gus's client)``."""
    make_user("ofg_boss", PW, is_admin=1)
    make_user("ofg_gus", PW)
    boss = login("ofg_boss", PW)
    ws = boss.post("/api/workspaces", json={"name": "Forget lab", "kind": "shared", "owner": USER}).json()["id"]
    assert fay.put(f"/api/workspaces/{ws}/members/ofg_gus", json={"role": "editor"}).status_code == 200
    yield ws, login("ofg_gus", PW)
    drop_user("ofg_boss")


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    found = install(monkeypatch)
    pdf_text.text_cache_clear()
    yield found
    pdf_text.text_cache_clear()


def rect(n):
    """The viewer's rect over PRINTED's line ``n`` (tests/test_ocr_respell.py's)."""
    import pymupdf

    x, y, size, text = PRINTED[n - 1]
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    return {"x1": x, "y1": round(y - 0.9 * size, 2), "x2": round(x + width, 2), "y2": round(y + 0.3 * size, 2)}


def highlight(c, page_id, quote, n, headers=None):
    from ocr_fixtures import H, W

    box = rect(n)
    pos = {"pageNumber": 1, "width": W, "height": H, "boundingRect": box, "rects": [box]}
    r = c.post("/api/blocks", json={"parent_id": page_id, "content": "",
                                    "properties": {"pdf_position": pos, "quote": quote, "color": "#ffe28fa6"}},
               headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def quote(c, block_id):
    return c.get(f"/api/blocks/{block_id}").json()["properties"]["quote"]


def forget(c, page_id, headers=None):
    return c.post("/api/jobs/ocr-drop", json={"page_id": page_id}, headers=headers)


def recognized(c, page_id, headers=None):
    job = jobs.wait(start(c, page_id, headers).json()["id"])
    assert job["state"] == "done", job
    return job


def searched(ws, doc, word):
    with connect_data_db(ws) as conn:
        return [hit.doc_id for hit in index.search(conn, word, 10, [doc])]


def test_forgetting_reads_the_pdfs_own_text_again(fay):
    ws = workspace_of(USER)
    page_id, doc = scan_page(fay, layered_scan(LIGHT))
    light = highlight(fay, page_id, LINES[5].translate(LIGHT), 5)  # drawn on the layer: respelled, then back
    assert recognized(fay, page_id)["result"]["highlights_respelled"] == 1 and quote(fay, light) == LINES[5]
    assert searched(ws, doc, "resonance") == [doc]
    before = pdf_ocr.state(ws, doc)
    r = forget(fay, page_id)
    assert r.status_code == 200, r.text
    job = r.json()
    assert (job["kind"], job["title"]) == ("ocr-drop", "Forgetting the recognized text of “A scanned paper”")
    assert job["params"] == {"page_id": page_id, "doc_id": doc, "title": "A scanned paper"}
    done = jobs.wait(job["id"])
    assert done["state"] == "done", done
    assert done["result"] == {"pages": 1, "highlights_respelled": 1, "highlights_kept": 0, "highlights_changed": 0,
                              "highlights_unread": 0}
    assert done["progress"]["phase"] == "updating" and not done["stoppable"]
    assert quote(fay, light) == LINES[5].translate(LIGHT), "the layer's words under it again"
    after = pdf_ocr.state(ws, doc)
    assert (after["recognized"], after["kept"], after["staged"], after["applied"]) == ([], [], [], [])
    assert (after["scanned"], after["kinds"], after["engine"], after["pending"]) == (1, {1: "scan"}, "", False)
    assert after["rev"] == before["rev"] + 1 and after["stamp"] not in ("", before["stamp"])
    with connect_data_db(ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pdf_ocr_pages WHERE doc_id = ?", (doc,)).fetchone()[0] == 0
    assert pdf_ocr.view_of(pdf_ocr.stored_path(ws, doc)) is None, "every reader reads the file's own layer"
    assert fay.get(f"/api/ocr/{doc}/pages", params={"first": 1}).json()["pages"] == {}
    assert searched(ws, doc, "resonance") == [], "searched in its own text again"
    assert ocr_auto.marker(ws, doc) == ocr_auto.DECLINED, "never recognized by itself again"
    # recognized again: every page read anew, under a stamp never seen before
    again = recognized(fay, page_id)
    assert again["result"]["recognized"] == 1 and quote(fay, light) == LINES[5]
    assert pdf_ocr.state(ws, doc)["stamp"] not in (before["stamp"], after["stamp"])
    assert pdf_ocr.state(ws, doc)["rev"] == after["rev"] + 1


def test_a_highlight_on_a_page_with_no_text_of_its_own_keeps_the_recognized_words(fay):
    page_id, _doc = scan_page(fay, printed_scan_pdf(dpi=72))
    recognized(fay, page_id)
    drawn = highlight(fay, page_id, LINES[1], 1)  # drawn on the recognized words of a picture
    done = jobs.wait(forget(fay, page_id).json()["id"])
    assert done["state"] == "done", done
    assert (done["result"]["highlights_unread"], done["result"]["highlights_respelled"]) == (1, 0)
    assert quote(fay, drawn) == LINES[1], "the page has no text of its own to take"


def test_who_may_forget_and_when(fay, guest, monkeypatch):
    page_id, _doc = scan_page(guest)
    refused = forget(guest, page_id)
    assert refused.status_code == 403 and refused.json()["detail"] == FORGET_GUESTS
    mine, _doc = scan_page(fay)
    nothing = forget(fay, mine)
    assert nothing.status_code == 400 and nothing.json()["detail"] == NOTHING_RECOGNIZED
    assert forget(fay, "no-such-page").status_code == 404
    # no engine needed: a server that recognizes nothing (the apps, recognition turned off since) forgets
    recognized(fay, mine)
    monkeypatch.setattr(ocr_engine, "check", lambda: "Text recognition is turned off on this server.")
    monkeypatch.setattr(ocr_engine, "capability", lambda: None)
    r = forget(fay, mine)
    assert r.status_code == 200 and jobs.wait(r.json()["id"])["state"] == "done"


def test_one_ocr_job_of_a_file_at_a_time_whatever_its_kind(fay, lab, monkeypatch):
    ws, gus = lab
    page_id, _doc = scan_page(fay, headers=_in(ws))
    recognized(fay, page_id, _in(ws))
    go, held = threading.Event(), threading.Event()

    def hold(module, name):
        real = getattr(module, name)

        def held_there(*args, **kwargs):
            held.set()
            assert go.wait(10), "the test never let the job go on"
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, held_there)

    # beside a recognition (another run of the file): the caller's own, someone else's
    hold(ocr_run, "run")
    try:
        running = start(fay, page_id, _in(ws)).json()
        assert held.wait(10)
        own = forget(fay, page_id, _in(ws))
        assert own.status_code == 409 and own.json()["detail"] == RECOGNIZING
        other = forget(gus, page_id, _in(ws))
        assert other.status_code == 409 and other.json()["detail"] == ELSEWHERE
    finally:
        go.set()
    assert jobs.wait(running["id"])["state"] == "done"
    # beside a forgetting, held past its last stop
    go.clear()
    held.clear()
    hold(highlight_relocate, "respell_quotes")
    try:
        first = forget(fay, page_id, _in(ws)).json()
        assert held.wait(10)
        again = forget(fay, page_id, _in(ws))
        assert again.status_code == 200 and again.json()["id"] == first["id"], "the caller's own forgetting"
        for who in (fay, gus):
            busy = start(who, page_id, _in(ws))
            assert busy.status_code == 409 and busy.json()["detail"] == FORGETTING
        theirs = forget(gus, page_id, _in(ws))
        assert theirs.status_code == 409 and theirs.json()["detail"] == FORGETTING
        with pytest.raises(HTTPException):
            jobs.cancel(first["id"])  # it updates from its start: no stop
    finally:
        go.set()
    assert jobs.wait(first["id"])["state"] == "done"


def test_the_open_tabs_are_told_after_an_apply_and_a_forget(fay, monkeypatch):
    ws = workspace_of(USER)
    told = []
    monkeypatch.setattr(collab, "open_pages", lambda: [(ws, "a page someone has open")])
    monkeypatch.setattr(collab, "publish_file", lambda ws_, pages, msg: told.append((ws_, list(pages), msg)))
    page_id, doc = scan_page(fay)
    copy = fay.post("/api/blocks", json={"parent_id": "root", "content": "Its copy",
                                         "properties": doc_props(url=f"/api/uploads/{doc}.pdf")})
    assert copy.status_code == 200, copy.text
    recognized(fay, page_id)
    stamp = pdf_ocr.stamp(ws, doc)
    assert told == [(ws, sorted([page_id, copy.json()["id"]]), {"t": "ocr", "doc_id": doc, "stamp": stamp})], \
        "the rooms of its page and of its copy"
    told.clear()
    recognized(fay, page_id)
    assert told == [], "a run that found nothing to read changed nothing: nobody is told"
    # a run whose finishing steps fail is told all the same: its text is live
    other_id, other = scan_page(fay)
    real_respell = highlight_relocate.respell_quotes

    def crash(*_args, **_kwargs):
        raise RuntimeError("the respell failed")

    monkeypatch.setattr(highlight_relocate, "respell_quotes", crash)
    recognized(fay, other_id)
    assert [msg["doc_id"] for _ws, _pages, msg in told] == [other] and pdf_ocr.state(ws, other)["pending"]
    monkeypatch.setattr(highlight_relocate, "respell_quotes", real_respell)
    told.clear()
    assert jobs.wait(forget(fay, page_id).json()["id"])["state"] == "done"
    assert [msg for _ws, _pages, msg in told] == [{"t": "ocr", "doc_id": doc, "stamp": pdf_ocr.stamp(ws, doc)}]
    assert pdf_ocr.stamp(ws, doc) != stamp
    # no room of the workspace open: nothing read, nothing sent
    told.clear()
    monkeypatch.setattr(collab, "open_pages", lambda: [("another workspace", "its page")])
    ocr_run.announce(ws, doc)
    assert told == []


def test_the_message_reaches_the_files_pages_and_the_members_never_another_pages_visitors(fay):
    ws = workspace_of(USER)
    page_id, doc = scan_page(fay)
    elsewhere = fay.post("/api/pages", json={"title": "Another page"}).json()["id"]
    shared = fay.post("/api/pages", json={"title": "A page shared on its own"}).json()["id"]
    token_file = fay.post(f"/api/share/{page_id}", json={"audience": "anyone", "role": "view"}).json()["token"]
    token_other = fay.post(f"/api/share/{shared}", json={"audience": "anyone", "role": "view"}).json()["token"]
    visitor, stranger = fresh_client(), fresh_client()
    with fay.websocket_connect(f"/api/ws/page/{elsewhere}?client=ofgM") as member, \
            visitor.websocket_connect(f"/api/ws/page/{page_id}?client=ofgV&share={token_file}") as on_file, \
            stranger.websocket_connect(f"/api/ws/page/{shared}?client=ofgS&share={token_other}") as off_file:
        for sock in (member, on_file, off_file):
            recv_hello(sock)
        recognized(fay, page_id)
        stamp = pdf_ocr.stamp(ws, doc)
        assert recv(member, "ocr") == {"t": "ocr", "doc_id": doc, "stamp": stamp}, "a member's other tab"
        assert recv(on_file, "ocr") == {"t": "ocr", "doc_id": doc, "stamp": stamp}, "a visitor of its page"
        # the sends of one message go out together: the next message the other share's visitor gets is this one
        collab.publish(ws, shared, {"t": "probe"})
        assert recv(off_file, "probe", ("join", "leave", "cursor")) == {"t": "probe"}, \
            "nothing of a file its share does not reach"
    collab.publish_file(ws, [], {"t": "ocr"})  # no room open now: a no-op
