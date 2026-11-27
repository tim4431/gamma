"""A run in a window (routers/ocr.py, gamma/ocr_run.py ``window_run``), on a
server that reads no text itself (``GAMMA_OCR_ENGINE=window``, as the
mobile apps' server is): the ``ocr-tab`` job's start (who may, where, with
what), one OCR job of a file at a time whatever its kind, what a start
beside one gets (a reloaded window's take-over, even of a run not yet
registered; a manual start beside an automatic run that gave way); its
wait for the window's pages and its three ends (the plan read through, the
window done, no page for too long, counted from the wait) and a Stop; the
plan; each page's words (the body's bounds, the characters a word loses,
who may send it, a page applied, skipped or sent again, the rate); and the
models a window reads with. The window's words are the printed page's, as
an engine reads them (tests/ocr_fake.py ``printed_lines``)."""

import json
import threading
import time
from pathlib import Path

import pytest
from conftest import account_of, drop_user, login, make_user, workspace_of
from fastapi import HTTPException
from ocr_fake import install, joined, printed_lines
from ocr_fixtures import printed_scan_pdf
from test_jobs import Gate
from test_ocr_api import _in, scan_page
from test_pdf_ocr_run import born_digital
from test_pdf_ocr_store import LINES

from gamma import jobs, ocr_engine, ocr_run, pdf_ocr, pdf_text
from gamma.db import connect_data_db
from gamma.routers import ocr as ocr_router
from gamma.routers.ocr import (APPLIED, ELSEWHERE, FORGETTING, GUESTS, ITSELF, NOT_SCANNED, OTHER_WINDOW,
                               RECOGNIZING, REPLACED, STALE, STOPPED, STOPPING, UNREADY)
from gamma.search import index

PW = "ow-pass-12345"


@pytest.fixture(scope="module")
def wendy(client):
    make_user("ow_wendy", PW)
    return login("ow_wendy", PW)


@pytest.fixture(scope="module")
def team(client, wendy):
    """A shared workspace wendy owns, victor edits and vera views: ``(ws,
    victor's client, vera's client)``."""
    make_user("ow_boss", PW, is_admin=1)
    make_user("ow_victor", PW)
    make_user("ow_vera", PW)
    boss = login("ow_boss", PW)
    ws = boss.post("/api/workspaces", json={"name": "Window lab", "kind": "shared", "owner": "ow_wendy"}).json()["id"]
    assert wendy.put(f"/api/workspaces/{ws}/members/ow_victor", json={"role": "editor"}).status_code == 200
    assert wendy.put(f"/api/workspaces/{ws}/members/ow_vera", json={"role": "viewer"}).status_code == 200
    yield ws, login("ow_victor", PW), login("ow_vera", PW)
    drop_user("ow_boss")


@pytest.fixture(autouse=True)
def _in_window(monkeypatch):
    """Recognition on, read in the window; the run's job reports often."""
    monkeypatch.setenv("GAMMA_OCR", "on")
    monkeypatch.setenv("GAMMA_OCR_ENGINE", "window")
    monkeypatch.setattr(ocr_run, "WINDOW_WAIT_S", 0.05)
    pdf_text.text_cache_clear()
    yield
    pdf_text.text_cache_clear()


def window_of(doc, client="tab-1", **over):
    return {"doc_id": doc, "engine": ocr_engine.BROWSER_ENGINE, "langs": ocr_engine.LANGS, "ver": pdf_ocr.VERSION,
            "client": client, **over}


def start(c, page_id, doc, headers=None, **over):
    return c.post("/api/jobs/ocr", json={"page_id": page_id, "window": window_of(doc, **over)}, headers=headers)


def recognizing(job_id, timeout=10.0):
    """The job once it waits for the window's pages (or has ended)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get(job_id)
        if job["progress"].get("phase") == "recognizing" or job["state"] not in jobs.ACTIVE:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never waited for its pages")


def started(c, page_id, doc, headers=None, **over):
    r = start(c, page_id, doc, headers, **over)
    assert r.status_code == 200, r.text
    recognizing(r.json()["id"])
    return r.json()


def words(conf=90.0):
    """The printed page's words as a window reads them: in UNITS, each with
    its confidence."""
    return [[[*word, conf] for word in line] for line in printed_lines()]


def put(c, doc, n, lines=None, client="tab-1", headers=None, **over):
    body = {"client": client, "engine": ocr_engine.BROWSER_ENGINE, "langs": ocr_engine.LANGS,
            "ver": pdf_ocr.VERSION, **({"failed": True} if lines is None else {"lines": lines}), **over}
    return c.put(f"/api/ocr/{doc}/pages/{n}", json=body, headers=headers)


def stop(job):
    """The job ended: stopped, unless it is past its last stop already."""
    try:
        jobs.cancel(job["id"])
    except HTTPException:
        pass  # applying what was sent: it ends of itself
    return jobs.wait(job["id"])


def test_a_run_in_a_window_from_start_to_end(wendy):
    page_id, doc = scan_page(wendy)
    r = start(wendy, page_id, doc)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "ocr-tab" and job["title"] == "Reading the text of “A scanned paper” in a window"
    assert job["params"] == {"page_id": page_id, "doc_id": doc, "title": "A scanned paper", "window": window_of(doc)}
    progress = recognizing(job["id"])["progress"]
    assert (progress["phase"], progress["done"], progress["total"], progress["unit"]) == ("recognizing", 0, 1, "pages")
    assert isinstance(progress["since"], int) and progress["from"] == 0
    plan = wendy.get(f"/api/ocr/{doc}/plan")
    assert plan.status_code == 200 and plan.headers["cache-control"] == "no-store"
    assert plan.json() == {"todo": [1], "sample": [], "done": [], "good_layer": False,
                           "render": {"dpi": 300, "max_pixels": 2480 * 3508, "max_side": 10000},
                           "limits": {"max_words": 20000, "max_word_chars": 200, "max_body": 1 << 20}}
    answer = put(wendy, doc, 1, words())
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"outcome": "recognized", "shared": None, "todo": [], "done": 1, "total": 1}
    done = jobs.wait(job["id"])
    assert done["state"] == "done", done
    assert (done["result"]["recognized"], done["result"]["failed"], done["result"]["scanned"]) == (1, 0, 1), \
        "the plan read through: it applies"
    found = wendy.get(f"/api/ocr/{doc}").json()
    assert (found["recognized"], found["engine"], found["todo"], found["staged"]) == ([1], "tesseract.js 7.0.0", 0, 0)
    lines = wendy.get(f"/api/ocr/{doc}/pages", params={"first": 1}).json()["pages"]["1"]["lines"]
    assert lines == printed_lines(), "the words as stored: the confidence left out"
    ws = workspace_of("ow_wendy")
    with connect_data_db(ws) as conn:
        assert conn.execute("SELECT engine FROM pdf_ocr_pages WHERE doc_id = ?", (doc,)).fetchall() == \
            [("tesseract.js 7.0.0",)]
        assert [hit.doc_id for hit in index.search(conn, "resonance", 10, [doc])] == [doc], "indexed again"


def test_who_may_start_a_run_in_a_window_and_where(wendy, guest, monkeypatch):
    page_id, doc = scan_page(wendy)
    guest_page, guest_doc = scan_page(guest)
    r = start(guest, guest_page, guest_doc)
    assert r.status_code == 403 and r.json()["detail"] == GUESTS
    for over in ({"engine": "tesseract.js 6.0.0"}, {"ver": pdf_ocr.VERSION + 1}, {"langs": "eng"}):
        r = start(wendy, page_id, doc, **over)
        assert r.status_code == 409 and r.json()["detail"] == STALE, over
    r = start(wendy, page_id, doc, doc_id="0" * 24)
    assert r.status_code == 409 and r.json()["detail"] == REPLACED
    r = wendy.post("/api/jobs/ocr", json={"page_id": page_id})
    assert r.status_code == 503 and r.json()["detail"] == ocr_engine.IN_WINDOW, "no run on this server itself"
    monkeypatch.setenv("GAMMA_OCR", "off")
    r = start(wendy, page_id, doc)
    assert r.status_code == 503 and r.json()["detail"] == "Text recognition is turned off on this server."
    monkeypatch.setenv("GAMMA_OCR", "on")
    monkeypatch.delenv("GAMMA_OCR_ENGINE")
    install(monkeypatch)
    r = start(wendy, page_id, doc)
    assert r.status_code == 409 and r.json()["detail"] == ITSELF
    assert jobs.live("ocr-tab", f"{workspace_of('ow_wendy')}:{doc}") is None


def test_one_ocr_job_of_a_file_at_a_time_whatever_its_kind(wendy, team):
    ws, victor, _vera = team
    page_id, doc = scan_page(wendy, headers=_in(ws))
    key, wendy_id, victor_id = f"{ws}:{doc}", account_of("ow_wendy"), account_of("ow_victor")
    for kind, owner, detail in (("ocr-drop", wendy_id, FORGETTING), ("ocr-drop", victor_id, FORGETTING),
                                ("ocr", wendy_id, RECOGNIZING), ("ocr", victor_id, ELSEWHERE),
                                ("ocr", jobs.WORKSPACE, RECOGNIZING)):
        gate = Gate()
        held = jobs.start(kind, owner=owner, ws=ws, key=key, run=gate, exclusive=ocr_run.OCR_KINDS)
        assert gate.started.wait(10)
        try:
            r = start(wendy, page_id, doc, _in(ws))
            assert r.status_code == 409 and r.json()["detail"] == detail, (kind, owner)
        finally:
            gate.release()
        jobs.wait(held["id"])
    first = started(wendy, page_id, doc, _in(ws))
    try:
        again = start(wendy, page_id, doc, _in(ws))
        assert again.status_code == 200 and again.json()["id"] == first["id"], "the owner gets its own run back"
        other = start(victor, page_id, doc, _in(ws))
        assert other.status_code == 409 and other.json()["detail"] == ELSEWHERE
        reloaded = start(wendy, page_id, doc, _in(ws), client="tab-2")
        assert reloaded.json()["id"] == first["id"], "a reloaded window goes on with the run"
        r = put(wendy, doc, 1, words(), headers=_in(ws))
        assert r.status_code == 409 and r.json()["detail"] == OTHER_WINDOW, "the window before is told it moved"
        assert put(wendy, doc, 1, words(), client="tab-2", headers=_in(ws)).status_code == 200
    finally:
        assert stop(first)["state"] in ("done", "cancelled")


def _hold_the_job(monkeypatch):
    """The run's job held at its next look at the plan, until ``release``:
    ``(holding, release)``, two events. The windows' requests go on."""
    holding, release = threading.Event(), threading.Event()
    real = ocr_run.plan_of

    def plan_of(*args, **kwargs):
        if threading.current_thread().name.startswith("job-ocr-tab"):
            holding.set()
            assert release.wait(10), "the test never let the job go on"
        return real(*args, **kwargs)

    monkeypatch.setattr(ocr_run, "plan_of", plan_of)
    return holding, release


def test_a_stopped_run_takes_no_page_and_a_start_beside_it_waits(wendy, monkeypatch):
    page_id, doc = scan_page(wendy, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    first = started(wendy, page_id, doc)
    holding, release = _hold_the_job(monkeypatch)
    assert holding.wait(10)
    try:
        assert jobs.cancel(first["id"])["stopping"]
        r = start(wendy, page_id, doc)
        assert r.status_code == 409 and r.json()["detail"] == STOPPING
        r = put(wendy, doc, 1, words())
        assert r.status_code == 409 and r.json()["detail"] == STOPPED, "a page sent after a Stop lands nowhere"
    finally:
        release.set()
    assert jobs.wait(first["id"])["state"] == "cancelled"
    assert wendy.get(f"/api/ocr/{doc}").json()["staged"] == 0


def test_a_manual_start_joins_the_automatic_run_of_its_file(wendy, monkeypatch):
    monkeypatch.delenv("GAMMA_OCR_ENGINE")
    install(monkeypatch)
    page_id, doc = scan_page(wendy)
    ws = workspace_of("ow_wendy")
    gate = Gate()
    auto = jobs.start("ocr", owner=jobs.WORKSPACE, ws=ws, key=f"{ws}:{doc}", run=gate, yields=True,
                      exclusive=ocr_run.OCR_KINDS, params={"page_id": page_id, "doc_id": doc, "auto": True})
    assert gate.started.wait(10)
    try:
        r = wendy.post("/api/jobs/ocr", json={"page_id": page_id})
        assert r.status_code == 200 and r.json()["id"] == auto["id"], "the run going is the one asked for"
        assert not jobs._live[auto["id"]].yields, "someone asked for it: it no longer gives way"
    finally:
        gate.release()
    assert jobs.wait(auto["id"])["state"] == "done"


@pytest.mark.parametrize("when", ["before the start", "before the promote"])
def test_a_manual_start_beside_an_automatic_run_that_gave_way_is_a_run_of_its_own(wendy, monkeypatch, when):
    """The automatic run gives way (a person's run on another file waits for
    the lane) before the start looks, or between the look and the
    promote: the start never gets that ending run back, which would end
    stopped and start again later as an automatic run."""
    monkeypatch.delenv("GAMMA_OCR_ENGINE")
    install(monkeypatch)
    page_id, doc = scan_page(wendy)
    ws, me = workspace_of("ow_wendy"), account_of("ow_wendy")
    told, ending, hold, other = [], threading.Event(), threading.Event(), Gate()

    def auto_run(job):  # reports until it gives way, then holds while it ends
        try:
            while True:
                job.progress(phase="recognizing")
                time.sleep(0.01)
        finally:
            ending.set()
            assert hold.wait(10), "the test never let the run end"

    auto = jobs.start("ocr", owner=jobs.WORKSPACE, ws=ws, key=f"{ws}:{doc}", run=auto_run, yields=True,
                      exclusive=ocr_run.OCR_KINDS, ended=told.append,
                      params={"page_id": page_id, "doc_id": doc, "auto": True})
    waiting = []

    def give_way():
        waiting.append(jobs.start("ocr", owner=me, ws=ws, key=f"{ws}:another", run=other))
        assert ending.wait(10), "a person's run waits for the lane: it gives way"

    if when == "before the start":
        give_way()
    else:
        real = jobs.promote

        def promote(job_id):
            give_way()
            return real(job_id)

        monkeypatch.setattr(jobs, "promote", promote)
    try:
        r = wendy.post("/api/jobs/ocr", json={"page_id": page_id})
        assert r.status_code == 200, r.text
        mine = r.json()
        assert mine["id"] != auto["id"] and mine["owner"] == me and mine["state"] == "queued", mine
    finally:
        hold.set()
    assert jobs.wait(auto["id"])["state"] == "cancelled" and told == ["yielded"]
    assert other.started.wait(10), "the run that waited first goes first"
    other.release()
    assert jobs.wait(waiting[0]["id"])["state"] == "done"
    assert jobs.wait(mine["id"])["state"] == "done"


def test_the_window_saying_it_is_done_applies_what_it_sent(wendy):
    page_id, doc = scan_page(wendy, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    job = started(wendy, page_id, doc)
    assert put(wendy, doc, 1, words()).json()["todo"] == [2]
    r = wendy.post(f"/api/ocr/{doc}/done", json={"client": "tab-2"})
    assert r.status_code == 409 and r.json()["detail"] == OTHER_WINDOW
    r = wendy.post(f"/api/ocr/{doc}/done", json={"client": "tab-1"})
    assert r.status_code == 200 and r.json()["id"] == job["id"]
    done = jobs.wait(job["id"])
    assert done["state"] == "done" and done["result"]["recognized"] == 1
    found = wendy.get(f"/api/ocr/{doc}").json()
    assert (found["recognized"], found["todo"], found["staged"]) == ([1], 1, 0), "page 2 is left for the next run"
    r = put(wendy, doc, 2, words())
    assert r.status_code == 409 and r.json()["detail"] == STOPPED, "the run ended"
    assert wendy.post(f"/api/ocr/{doc}/done", json={"client": "tab-1"}).status_code == 409


def test_a_run_no_page_came_to_for_too_long_ends_and_keeps_what_was_sent(wendy, monkeypatch):
    page_id, doc = scan_page(wendy, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    monkeypatch.setattr(ocr_run, "WINDOW_IDLE_S", 0.5)
    job = started(wendy, page_id, doc)
    assert put(wendy, doc, 1, words()).status_code == 200
    ended = jobs.wait(job["id"])
    assert ended["state"] == "cancelled" and ended["progress"]["idle"] == 0.5, "nobody stopped it; nothing applied"
    found = wendy.get(f"/api/ocr/{doc}").json()
    assert (found["recognized"], found["staged"], found["todo"]) == ([], 1, 1), "page 1 waits for the next run"
    monkeypatch.setattr(ocr_run, "WINDOW_IDLE_S", 600)
    again = started(wendy, page_id, doc)
    assert wendy.get(f"/api/ocr/{doc}/plan").json()["todo"] == [2], "the next run goes on from there"
    assert put(wendy, doc, 2, words()).json()["todo"] == []
    assert jobs.wait(again["id"])["result"]["recognized"] == 2


def test_the_idle_time_is_the_windows_not_the_servers_own_steps(wendy, monkeypatch):
    page_id, doc = scan_page(wendy)
    monkeypatch.setattr(ocr_run, "WINDOW_IDLE_S", 1.0)
    real = ocr_run.classify

    def slow(*args, **kwargs):  # a long scan read on a slow server: longer than the window may idle
        time.sleep(1.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(ocr_run, "classify", slow)
    job = started(wendy, page_id, doc)
    answer = put(wendy, doc, 1, words())
    assert answer.status_code == 200, answer.text
    done = jobs.wait(job["id"])
    assert done["state"] == "done" and done["result"]["recognized"] == 1


def test_a_stop_by_its_owner_ends_the_run_and_keeps_what_was_sent(wendy):
    """What a window whose own side failed sends (POST /api/jobs/{id}/cancel,
    the Background tasks' Stop): the job ends cancelled at its next report,
    with no ``idle`` (that is the server's word for its own end), and the
    pages sent wait for the next run."""
    page_id, doc = scan_page(wendy, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    job = started(wendy, page_id, doc)
    assert put(wendy, doc, 1, words()).status_code == 200
    r = wendy.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["stopping"], r.text
    ended = jobs.wait(job["id"])
    assert ended["state"] == "cancelled" and "idle" not in ended["progress"], ended["progress"]
    found = wendy.get(f"/api/ocr/{doc}").json()
    assert (found["recognized"], found["staged"], found["todo"]) == ([], 1, 1)
    r = put(wendy, doc, 2, words())
    assert r.status_code == 409 and r.json()["detail"] == STOPPED


def test_a_window_takes_over_a_run_whose_job_just_started(wendy, monkeypatch):
    page_id, doc = scan_page(wendy)
    real, go = ocr_run.window_run, threading.Event()

    def late(*args, **kwargs):  # the job's thread not yet at the run's registration
        assert go.wait(10), "the test never let the run begin"
        return real(*args, **kwargs)

    monkeypatch.setattr(ocr_run, "window_run", late)
    first = start(wendy, page_id, doc)
    assert first.status_code == 200, first.text
    try:
        taken = start(wendy, page_id, doc, client="tab-2")
        assert taken.status_code == 200 and taken.json()["id"] == first.json()["id"]
    finally:
        go.set()
    recognizing(first.json()["id"])
    r = put(wendy, doc, 1, words())
    assert r.status_code == 409 and r.json()["detail"] == OTHER_WINDOW, "the window that took it reads its pages"
    assert put(wendy, doc, 1, words(), client="tab-2").status_code == 200
    assert jobs.wait(first.json()["id"])["state"] == "done"


def test_a_page_the_window_found_nothing_on_is_left_out(wendy):
    page_id, doc = scan_page(wendy, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    job = started(wendy, page_id, doc)
    answer = put(wendy, doc, 1).json()
    assert answer == {"outcome": "failed", "shared": None, "todo": [2], "done": 1, "total": 2}
    assert wendy.get(f"/api/ocr/{doc}/plan").json()["todo"] == [2]
    assert wendy.get(f"/api/ocr/{doc}").json()["todo"] == 1
    assert put(wendy, doc, 1, words()).json()["outcome"] == "failed", "a page sent again gets its first answer"
    assert put(wendy, doc, 2, words()).json()["todo"] == []
    result = jobs.wait(job["id"])["result"]
    assert (result["recognized"], result["failed"]) == (1, 1)
    assert wendy.get(f"/api/ocr/{doc}").json()["todo"] == 1, "the next run reads it again"


def test_a_pages_body_is_bounded(wendy):
    page_id, doc = scan_page(wendy)
    job = started(wendy, page_id, doc)
    try:
        big = json.dumps({"client": "tab-1", "engine": ocr_engine.BROWSER_ENGINE, "langs": ocr_engine.LANGS,
                          "ver": pdf_ocr.VERSION, "lines": [[["x" * 100, 0, 0, 1, 1, 50]] * 11000]})
        assert len(big) > 1 << 20
        r = wendy.put(f"/api/ocr/{doc}/pages/1", content=big, headers={"Content-Type": "application/json"})
        assert r.status_code == 413
        word = ["word", 0, 0, 10, 10, 90]
        for bad in ([[["word", 0, 0, 10, pdf_text.OCR_UNITS + 1, 90]]], [[["word", 0, 0, 10, 10, 100.5]]],
                    [[["word", -1, 0, 10, 10, 90]]], [[["word", 0.5, 0, 10, 10, 90]]],
                    [[["x" * 201, 0, 0, 10, 10, 90]]], [[["word", 0, 0, 10, 10]]],
                    [[word] * 10001, [word] * 10000]):
            r = put(wendy, doc, 1, bad)
            assert r.status_code == 422, (str(bad)[:80], r.text)
        nan = (f'{{"client": "tab-1", "engine": "{ocr_engine.BROWSER_ENGINE}", "langs": "{ocr_engine.LANGS}", '
               f'"ver": {pdf_ocr.VERSION}, "lines": [[["word", 0, 0, 10, 10, NaN]]]}}')
        r = wendy.put(f"/api/ocr/{doc}/pages/1", content=nan, headers={"Content-Type": "application/json"})
        assert r.status_code == 422, r.text
        assert put(wendy, doc, 1, words(), failed=True).status_code == 422, "its lines, or failed"
        assert wendy.put(f"/api/ocr/{doc}/pages/0", json={}).status_code == 422
        assert wendy.put(f"/api/ocr/{doc}/pages/1", content=b"{").status_code == 422
        assert wendy.put("/api/ocr/not-a-hash/pages/1", json={}).status_code in (400, 422)
        assert put(wendy, doc, 1, words(), engine="tesseract.js 6.0.0").json()["detail"] == STALE
        assert jobs.get(job["id"])["state"] == "running", "a refused page ends nothing"
    finally:
        stop(job)


def test_a_word_loses_what_no_engine_reads_off_a_page(wendy):
    page_id, doc = scan_page(wendy)
    job = started(wendy, page_id, doc)
    sent = words()
    sent[0][0][0] = "Qu​ant‮um\u0000"     # a zero-width space, a bidi override, a NUL
    sent[0][1][0] = "Zéno"                     # an accent composed (NFC)
    sent[1] = [["​﻿", 0, 0, 10, 10, 99.0]] + sent[1]  # a word of format characters alone: dropped
    assert put(wendy, doc, 1, sent).json()["outcome"] == "recognized"
    assert jobs.wait(job["id"])["state"] == "done"
    lines = wendy.get(f"/api/ocr/{doc}/pages", params={"first": 1}).json()["pages"]["1"]["lines"]
    assert lines[0][0][0] == "Quantum" and lines[0][1][0] == "Zéno" and lines[1] == printed_lines()[1]


def test_who_may_send_a_page(wendy, team, guest, anon):
    ws, victor, vera = team
    page_id, doc = scan_page(wendy, headers=_in(ws))
    job = started(wendy, page_id, doc, _in(ws))
    try:
        assert put(anon, doc, 1, words(), headers=_in(ws)).status_code == 401
        r = put(guest, doc, 1, words(), headers=_in(ws))
        assert r.status_code == 403 and r.json()["detail"] == GUESTS
        assert put(vera, doc, 1, words(), headers=_in(ws)).status_code == 403, "a viewer changes nothing"
        r = put(victor, doc, 1, words(), headers=_in(ws))
        assert r.status_code == 409 and r.json()["detail"] == ELSEWHERE, "another account's run"
        r = put(wendy, doc, 1, words(), client="tab-9", headers=_in(ws))
        assert r.status_code == 409 and r.json()["detail"] == OTHER_WINDOW
        assert vera.get(f"/api/ocr/{doc}/plan", headers=_in(ws)).status_code == 403
        assert anon.get(f"/api/ocr/{doc}/plan", headers=_in(ws)).status_code == 401
        r = put(wendy, doc, 1, words())  # her own workspace: no such run there
        assert r.status_code == 409 and r.json()["detail"] == STOPPED
    finally:
        stop(job)


def test_a_page_applied_staged_before_or_sent_again(wendy, monkeypatch):
    page_id, doc = scan_page(wendy, joined(*[printed_scan_pdf(dpi=72)] * 3, born_digital()))
    ws = workspace_of("ow_wendy")
    pdf_ocr.set_scanned(ws, doc, 4, 3)
    pdf_ocr.stage_page(ws, doc, 1, LINES, engine="tesseract 5.5.4")
    pdf_ocr.apply(ws, doc)                               # page 1: a run on the server applied it
    pdf_ocr.stage_page(ws, doc, 2, LINES, engine="tesseract 5.5.4")  # page 2: one stopped staged it
    job = started(wendy, page_id, doc)
    assert not pdf_ocr.state(ws, doc)["pending"], "the run finished the apply left due first"
    assert wendy.get(f"/api/ocr/{doc}/plan").json()["todo"] == [3]
    r = put(wendy, doc, 1, words())
    assert r.status_code == 409 and r.json()["detail"] == APPLIED
    r = put(wendy, doc, 4, words())
    assert r.status_code == 400 and r.json()["detail"] == NOT_SCANNED, "a born-digital page is never read"
    assert put(wendy, doc, 2, words()).json() == {"outcome": "skipped", "shared": None, "todo": [3], "done": 2,
                                                  "total": 3}
    staged = []
    real = ocr_run.stage_words
    monkeypatch.setattr(ocr_run, "stage_words", lambda *a, **k: staged.append(a[2]) or real(*a, **k))
    holding, release = _hold_the_job(monkeypatch)  # so it does not apply between the two sends
    assert holding.wait(10)
    try:
        first = put(wendy, doc, 3, words()).json()
        again = put(wendy, doc, 3, words()).json()
    finally:
        release.set()
    assert first == again == {"outcome": "recognized", "shared": None, "todo": [], "done": 3, "total": 3}
    assert staged == [3], "a page sent again is answered without work"
    result = jobs.wait(job["id"])["result"]
    assert result["recognized"] == 3
    assert pdf_ocr.state(ws, doc)["engine"] == "tesseract 5.5.4 + tesseract.js 7.0.0", "each page's engine"


def test_a_window_sends_so_many_pages_a_minute(wendy, monkeypatch):
    page_id, doc = scan_page(wendy, joined(*[printed_scan_pdf(dpi=72)] * 3))
    job = started(wendy, page_id, doc)
    monkeypatch.setattr(ocr_router, "PAGES_PER_MINUTE", 2)
    try:
        assert put(wendy, doc, 1, words()).status_code == 200
        assert put(wendy, doc, 1, words()).status_code == 200
        assert put(wendy, doc, 2, words()).status_code == 429
    finally:
        stop(job)


def test_the_plan_waits_for_the_pages_kinds(wendy):
    _page_id, doc = scan_page(wendy)
    r = wendy.get(f"/api/ocr/{doc}/plan")
    assert r.status_code == 409 and r.json()["detail"] == UNREADY
    assert wendy.get("/api/ocr/not-a-hash/plan").status_code == 400


def test_the_models_a_window_reads_with(wendy, anon, monkeypatch):
    config = anon.get("/api/server-config").json()["ocr"]
    tag = ocr_engine.models_tag()
    assert config == {"where": "window", "engine": "tesseract.js 7.0.0", "langs": ["eng", "fra", "deu"],
                      "models": f"/api/ocr/models/{tag}", "auto": False}
    r = wendy.get(f"{config['models']}/fra.traineddata")
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.content == Path(ocr_engine.model_path(tag, "fra")).read_bytes()
    assert wendy.get(f"/api/ocr/models/{'0' * 12}/fra.traineddata").status_code == 404, "another tag"
    assert wendy.get(f"{config['models']}/spa.traineddata").status_code == 404, "another language"
    assert wendy.get(f"{config['models']}/fra.txt").status_code == 404
    assert anon.get(f"{config['models']}/fra.traineddata").status_code == 401, "a session's"
    monkeypatch.delenv("GAMMA_OCR_ENGINE")
    install(monkeypatch)
    assert wendy.get(f"{config['models']}/fra.traineddata").status_code == 404, "a server that reads itself"
