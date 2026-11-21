"""Automatic text recognition (gamma/ocr_auto.py): the admin's switch and
where it comes from (GET/PUT /api/admin/settings, /api/server-config's
``ocr.auto``); which uploads it takes up (the routes that make a page of
one, a scan with no text but a cover, never a born-digital paper with a
full-page figure, a guest's or a layered scan); its queue (one run at a
time, the next when one ends, the ones a restart left at the jobs' sweep);
a run giving way to one a person asks for and going on after, a person's
start of the same file joining it; the markers a stop (running or
waiting), an end and a failure leave, and a workspace delete does not; the
failed metadata lookup it clears. A fake engine
(tests/ocr_fake.py), the switch on (conftest turns it off for every other
test)."""

import secrets
import threading

import pytest
from conftest import account_of, drop_user, guest_name, login, make_user, workspace_of
from ocr_fake import install, joined, layered_scan
from ocr_fixtures import picture_pdf, printed_scan_pdf
from test_jobs import Gate
from test_ocr_api import scan_page, start, upload
from test_pdf_ocr_run import born_digital

from gamma import jobs, ocr_auto, ocr_engine, pdf_ocr, pdf_text, server_options, workspaces
from gamma.db import connect_pages_db, connect_users_db, page_now

PW = "oau-pass-12345"


@pytest.fixture(scope="module")
def amy(client):
    make_user("oau_amy", PW)
    return login("oau_amy", PW)


@pytest.fixture(scope="module")
def admin(client):
    make_user("oau_admin", PW, is_admin=1)
    yield login("oau_admin", PW)
    drop_user("oau_admin")


@pytest.fixture(autouse=True)
def fake(monkeypatch, server_option):
    """The switch on, a fake engine; nothing of the queue left from a test
    before, and every file handed to the worker considered before the test
    ends (its jobs then end with it: conftest)."""
    server_option("ocr_auto", True)
    found = install(monkeypatch)
    ocr_auto.reset()
    pdf_text.text_cache_clear()
    yield found
    assert ocr_auto.drain(), "the worker never got through the files handed to it"
    ocr_auto.reset()
    pdf_text.text_cache_clear()


def scan(pages=2) -> bytes:
    """A scan with no text: ``pages`` pictures of the printed page."""
    return joined(*[printed_scan_pdf(dpi=72)] * pages)


def uploaded(c, data, title="A scanned book", headers=None):
    """A library page made of an upload of ``data``, as the app makes one
    (POST /api/uploads, then by-doc): ``(page id, doc id)``."""
    doc = upload(c, data, headers)
    r = c.post(f"/api/blocks/by-doc/{doc}", json={"default_title": title}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"], doc


def automatic(ws, doc):
    """The file's latest automatic run once the worker considered it, ended
    (with its result); None when it has none."""
    assert ocr_auto.drain()
    job = jobs.latest(jobs.WORKSPACE, "ocr", f"{ws}:{doc}")
    return jobs.wait(job["id"]) if job else None


class Held:
    """The fake engine held at its first page until ``release()``:
    ``reading`` once it reads it."""

    def __init__(self, monkeypatch, fake):
        self.reading, self.go = threading.Event(), threading.Event()
        real = fake.recognize

        def recognize(raster):
            self.reading.set()
            assert self.go.wait(10), "the test never let the page be read"
            return real(raster)

        monkeypatch.setattr(ocr_engine, "recognize", recognize)

    def release(self):
        self.go.set()


def test_a_scan_uploaded_with_no_text_is_recognized_by_itself(amy):
    ws = workspace_of("oau_amy")
    page_id, doc = uploaded(amy, scan())
    job = automatic(ws, doc)
    assert job["state"] == "done", job
    assert (job["owner"], job["workspace"], job["title"]) == (jobs.WORKSPACE, ws,
                                                              "Recognizing the text of “A scanned book” automatically")
    assert job["params"] == {"page_id": page_id, "doc_id": doc, "title": "A scanned book", "auto": True}
    assert job["result"]["recognized"] == 2 and ocr_auto.marker(ws, doc) == ocr_auto.DONE
    found = amy.get(f"/api/ocr/{doc}").json()
    assert (found["recognized"], found["scanned"]) == ([1, 2], 2)
    # considered once: the same file's page asked for again makes nothing
    assert amy.post(f"/api/blocks/by-doc/{doc}", json={"default_title": "A scanned book"}).json()["id"] == page_id
    assert ocr_auto.drain() and jobs.latest(jobs.WORKSPACE, "ocr", f"{ws}:{doc}")["id"] == job["id"]


def test_which_pdfs_are_scans_with_no_text(tmp_path, monkeypatch):
    def path_of(data):
        path = tmp_path / f"{secrets.token_hex(6)}.pdf"
        path.write_bytes(data)
        return path

    cover = joined(born_digital(), printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72))
    assert ocr_auto.qualifies(path_of(cover)) == (3, {2: "picture", 3: "picture"}), "a publisher's cover is fine"
    two = joined(born_digital(), picture_pdf(footer="Downloaded on 1 April 1992"), born_digital(), picture_pdf())
    assert ocr_auto.qualifies(path_of(two)) == (4, {2: "picture", 4: "picture"})
    figure = joined(born_digital(), born_digital(), born_digital(), picture_pdf())
    assert ocr_auto.qualifies(path_of(figure)) is None, "a born-digital paper with a full-page figure is no scan"
    assert ocr_auto.qualifies(path_of(born_digital())) is None
    assert ocr_auto.qualifies(path_of(joined(printed_scan_pdf(dpi=72), layered_scan()))) is None, \
        "a scan with a layer of its own waits for someone to ask"
    assert ocr_auto.qualifies(path_of(b"%PDF-1.4 not really")) is None
    monkeypatch.setattr(ocr_auto, "AUTO_MAX_PAGES", 2)
    assert ocr_auto.qualifies(path_of(scan(3))) is None, "too long to read by itself"
    assert ocr_auto.qualifies(path_of(scan(2))) == (2, {1: "picture", 2: "picture"})
    # the walk stops at the first page that settles it
    read, real = [], pdf_text.page_kinds

    def counted(src, *args, **kwargs):
        for found in real(src, *args, **kwargs):
            read.append(found[0])
            yield found

    monkeypatch.setattr(pdf_text, "page_kinds", counted)
    assert ocr_auto.qualifies(path_of(joined(layered_scan(), scan(4)))) is None and read == [1]


def test_what_an_upload_is_taken_up_by(amy, guest, server_option):
    ws = workspace_of("oau_amy")
    _page, figure = uploaded(amy, joined(born_digital(), born_digital(), born_digital(), picture_pdf()))
    _page, layered = uploaded(amy, layered_scan())
    _page, covered = uploaded(amy, joined(born_digital(), scan()))
    assert automatic(ws, figure) is None and ocr_auto.marker(ws, figure) == ocr_auto.SKIPPED
    assert automatic(ws, layered) is None and ocr_auto.marker(ws, layered) == ocr_auto.SKIPPED
    assert pdf_ocr.state(ws, figure) is None, "a file it skips gets no row of the recognition's"
    assert automatic(ws, covered)["result"]["recognized"] == 2 and ocr_auto.marker(ws, covered) == ocr_auto.DONE
    # a guest's upload: never (a guest's hours of CPU)
    guest_ws = workspace_of(guest_name())
    _page, theirs = uploaded(guest, scan())
    assert automatic(guest_ws, theirs) is None and ocr_auto.marker(guest_ws, theirs) is None
    # a page made by hand (POST /api/pages, then the attachment) is no upload's: nothing
    _page, by_hand = scan_page(amy, scan())
    assert automatic(ws, by_hand) is None and ocr_auto.marker(ws, by_hand) is None
    # the switch off: nothing considered
    server_option("ocr_auto", False)
    _page, off = uploaded(amy, scan())
    assert automatic(ws, off) is None and ocr_auto.marker(ws, off) is None


def test_the_routes_that_make_a_page_of_an_upload_hand_it_over(amy, monkeypatch):
    calls = []
    monkeypatch.setattr(ocr_auto, "consider", lambda ws, doc, page, actor: calls.append((ws, doc, page, actor)))
    ws, me = workspace_of("oau_amy"), account_of("oau_amy")
    doc = upload(amy, picture_pdf())
    page = amy.post(f"/api/blocks/by-doc/{doc}", json={"default_title": "By doc"}).json()
    assert amy.post(f"/api/blocks/by-doc/{doc}", json={"default_title": "By doc"}).json()["id"] == page["id"]
    assert calls == [(ws, doc, page["id"], me)], "a page made, never one found"
    calls.clear()
    other = upload(amy, picture_pdf())
    r = amy.post("/api/pages/by-file", json={"file": f"{other}.pdf", "name": "by-file.pdf"})
    assert r.status_code == 200 and r.json()["created"], r.text
    assert calls == [(ws, other, r.json()["page"]["id"], me)]
    assert amy.post("/api/pages/by-file", json={"file": f"{other}.pdf"}).json()["created"] is False
    assert len(calls) == 1
    calls.clear()
    clipped = upload(amy, picture_pdf())
    r = amy.post("/api/clip", json={"doc_id": clipped, "fetch_metadata": False})
    assert r.status_code == 200 and not r.json()["existed"], r.text
    assert calls == [(ws, clipped, r.json()["block_id"], me)]
    assert amy.post("/api/clip", json={"doc_id": clipped, "fetch_metadata": False}).json()["existed"]
    assert len(calls) == 1


def test_one_automatic_run_at_a_time_the_next_when_it_ends(amy, fake, monkeypatch):
    ws = workspace_of("oau_amy")
    held = Held(monkeypatch, fake)
    try:
        _page, first = uploaded(amy, scan(1))
        assert held.reading.wait(10)
        _page, second = uploaded(amy, scan(1))
        assert ocr_auto.drain()
        assert jobs.live_count("ocr") == 1 and jobs.live("ocr", f"{ws}:{first}") is not None
        assert ocr_auto.marker(ws, second) == ocr_auto.QUEUED, "its turn comes when the run going ends"
        assert jobs.latest(jobs.WORKSPACE, "ocr", f"{ws}:{second}") is None
    finally:
        held.release()
    assert automatic(ws, first)["state"] == "done"
    then = automatic(ws, second)
    assert then is not None and then["state"] == "done" and ocr_auto.marker(ws, second) == ocr_auto.DONE


def test_an_automatic_run_gives_way_to_a_person_and_goes_on_after(amy, fake, monkeypatch):
    ws = workspace_of("oau_amy")
    held, mine, go_on = Held(monkeypatch, fake), [], threading.Event()
    real = pdf_ocr.run

    def run(ws_, doc_id, src, **kwargs):  # the person's run held until the test has looked
        if doc_id in mine:
            assert go_on.wait(10), "the test never let the person's run go on"
        return real(ws_, doc_id, src, **kwargs)

    monkeypatch.setattr(pdf_ocr, "run", run)
    try:
        _page, doc = uploaded(amy, scan(2))
        assert held.reading.wait(10)
        auto = jobs.live("ocr", f"{ws}:{doc}")
        page_id, other = scan_page(amy, scan(1))  # a page made by hand: a person's Recognize text
        mine.append(other)
        manual = start(amy, page_id).json()
        assert manual["state"] == "queued", "the lane is the automatic run's"
        held.release()
        gave_way = jobs.wait(auto["id"])
        assert gave_way["state"] == "cancelled" and gave_way["error"] == ""
        assert ocr_auto.marker(ws, doc) == ocr_auto.QUEUED, "nobody stopped it: it goes on later"
        assert pdf_ocr.state(ws, doc)["staged"] == [1], "the page it read stays, for the run that goes on"
        assert jobs.get(manual["id"])["state"] == "running"
    finally:
        held.release()
        go_on.set()
    assert jobs.wait(manual["id"])["state"] == "done"
    again = automatic(ws, doc)
    assert again["id"] != auto["id"] and again["state"] == "done" and again["result"]["recognized"] == 2
    assert fake.calls == 3, "the first page read once, the other file's, then the second page"
    assert ocr_auto.marker(ws, doc) == ocr_auto.DONE


def test_a_person_asking_for_the_same_file_joins_its_automatic_run(amy, fake, monkeypatch):
    ws = workspace_of("oau_amy")
    held = Held(monkeypatch, fake)
    try:
        page_id, doc = uploaded(amy, scan(1))
        assert held.reading.wait(10)
        auto = jobs.live("ocr", f"{ws}:{doc}")
        joined_ = start(amy, page_id)
        assert joined_.status_code == 200 and joined_.json()["id"] == auto["id"], "the run going is the one asked for"
        other_page, _other = scan_page(amy, scan(1))
        waits = start(amy, other_page).json()
        assert waits["state"] == "queued"
        with jobs._lock:
            live = jobs._live[auto["id"]]
            assert not live.yields and not live.giving_way, "asked for, it no longer gives way"
    finally:
        held.release()
    assert jobs.wait(auto["id"])["state"] == "done" and ocr_auto.marker(ws, doc) == ocr_auto.DONE
    assert jobs.wait(waits["id"])["state"] == "done"


def test_a_stop_declines_the_file_and_a_failure_settles_it(amy, fake, monkeypatch):
    ws = workspace_of("oau_amy")
    held = Held(monkeypatch, fake)
    try:
        _page, doc = uploaded(amy, scan(1))
        assert held.reading.wait(10)
        auto = jobs.live("ocr", f"{ws}:{doc}")
        assert jobs.cancel(auto["id"])["stopping"]
    finally:
        held.release()
    assert jobs.wait(auto["id"])["state"] == "cancelled"
    assert ocr_auto.marker(ws, doc) == ocr_auto.DECLINED and jobs.live_count("ocr") == 0, "never started again"
    ocr_auto.next()
    assert jobs.latest(jobs.WORKSPACE, "ocr", f"{ws}:{doc}")["id"] == auto["id"]

    def too_long(*_args, **_kwargs):
        raise ValueError("This PDF has 3000 scanned pages; text recognition reads at most 2000 pages of a file.")

    monkeypatch.setattr(pdf_ocr, "run", too_long)
    _page, long = uploaded(amy, scan(1))
    failed = automatic(ws, long)
    assert failed["state"] == "failed" and "3000" in failed["error"]
    assert ocr_auto.marker(ws, long) == ocr_auto.DONE, "a file it cannot read is not tried again"
    assert jobs.live_count("ocr") == 0


def test_a_run_a_workspace_delete_stopped_waits_for_its_turn_again(amy, fake, monkeypatch):
    ws = workspace_of("oau_amy")
    held = Held(monkeypatch, fake)
    try:
        _page, doc = uploaded(amy, scan(1))
        assert held.reading.wait(10)
        auto = jobs.live("ocr", f"{ws}:{doc}")
        why = "the workspace is being deleted"
        with workspaces._claimed([ws]):  # what a delete does before its final copy
            jobs.refuse_writers([ws], why, progress=held.release)
            refused = jobs.get(auto["id"])
            assert (refused["state"], refused["error"]) == ("failed", why)
            assert ocr_auto.marker(ws, doc) == ocr_auto.QUEUED, "no fault of the file's: it waits"
            assert jobs.live_count("ocr") == 0, "nothing starts in a workspace being deleted"
    finally:
        held.release()
    ocr_auto.resume()  # the delete was called off (its final copy failed): the next sweep
    again = automatic(ws, doc)
    assert again["id"] != auto["id"] and again["state"] == "done" and again["result"]["recognized"] == 1
    assert ocr_auto.marker(ws, doc) == ocr_auto.DONE


def test_an_automatic_run_stopped_while_it_waits_is_declined(amy, monkeypatch):
    ws, me = workspace_of("oau_amy"), account_of("oau_amy")
    _page, doc = scan_page(amy, scan(1))
    pdf_ocr.set_scanned(ws, doc, 1, 1, {1: "picture"})
    assert ocr_auto.mark(ws, doc, ocr_auto.QUEUED, me, first=True)
    gate = Gate()
    holder = jobs.start("ocr", owner=me, ws=ws, key=f"{ws}:the-lane", run=gate)  # a person's run holds the lane
    assert gate.started.wait(10)
    try:
        auto = ocr_auto._start(ws, doc, me)
        assert auto["state"] == "queued"
        r = amy.post(f"/api/jobs/{auto['id']}/cancel")
        assert r.status_code == 200 and r.json()["state"] == "cancelled", r.text
        assert ocr_auto.marker(ws, doc) == ocr_auto.DECLINED
    finally:
        gate.release()
    jobs.wait(holder["id"])
    ocr_auto.resume()
    assert jobs.latest(jobs.WORKSPACE, "ocr", f"{ws}:{doc}")["id"] == auto["id"], "never started again"


def test_the_jobs_sweep_starts_what_a_restart_left_queued(amy):
    ws, me = workspace_of("oau_amy"), account_of("oau_amy")
    _page, doc = scan_page(amy, scan(1))
    key = f"{ws}:{doc}"
    pdf_ocr.set_scanned(ws, doc, 1, 1, {1: "picture"})
    assert ocr_auto.mark(ws, doc, ocr_auto.QUEUED, me, first=True)
    # what a restart leaves: the run failed as interrupted (jobs.recover), its marker queued
    old = secrets.token_hex(12)
    with connect_users_db() as conn:
        conn.execute("INSERT INTO jobs (id, owner, workspace_id, kind, key, title, params, state, progress, error, "
                     "instance, created_at, finished_at) VALUES (?, '', ?, 'ocr', ?, 't', '{}', 'failed', '{}', ?, "
                     "'gone', ?, ?)", (old, ws, key, jobs.INTERRUPTED, page_now(), page_now()))
        conn.commit()
    ocr_auto.reset()  # a process that knows of no queue yet
    jobs.sweep()
    job = automatic(ws, doc)
    assert job is not None and job["state"] == "done" and job["result"]["recognized"] == 1
    assert jobs.get(old) is None, "the interrupted row goes: it would read as failed beside the run that went on"
    assert ocr_auto.marker(ws, doc) == ocr_auto.DONE


def test_a_failed_metadata_lookup_is_cleared_once_the_text_is_recognized(amy, fake, monkeypatch):
    ws, me = workspace_of("oau_amy"), account_of("oau_amy")
    held = Held(monkeypatch, fake)
    try:
        page_id, doc = uploaded(amy, scan(1))
        assert held.reading.wait(10)
        r = amy.put(f"/api/blocks/{page_id}", json={"properties": {"meta_error": {"at": page_now(), "detail": "none"}}})
        assert r.status_code == 200, r.text
    finally:
        held.release()
    assert automatic(ws, doc)["state"] == "done"
    assert "meta_error" not in amy.get(f"/api/blocks/{page_id}").json()["properties"], \
        "the next open looks the paper up with its recognized text"
    with connect_pages_db(ws) as conn:
        actor, client = conn.execute("SELECT actor, client FROM page_ops WHERE page_id = ? ORDER BY seq DESC LIMIT 1",
                                     (page_id,)).fetchone()
    assert (actor, client) == (me, "ocr"), "written under the uploader"


def test_the_switch_is_a_server_option(admin, anon, monkeypatch):
    """Settings → Server → Text recognition: the automatic recognition's
    switch is the server option ``ocr_auto`` (on by default; the suite
    starts with it off, conftest), and it acts only where the server
    recognizes text itself."""
    assert server_options.OPTIONS["ocr_auto"].default is False, "the suite's default (conftest)"
    r = admin.put("/api/admin/settings", json={"options": {"ocr_auto": False}})
    assert r.status_code == 200, r.text
    got = r.json()["options"]
    assert (got["ocr_auto"], got["text_recognition"]["server"]) == (False, True)
    assert anon.get("/api/server-config").json()["ocr"]["auto"] is False and not ocr_auto.enabled()
    got = admin.put("/api/admin/settings", json={"options": {"ocr_auto": True}}).json()["options"]
    assert got["ocr_auto"] is True and ocr_auto.enabled()
    assert anon.get("/api/server-config").json()["ocr"]["auto"] is True
    # recognition itself: the admin's too, unless GAMMA_OCR decides (the row read-only)
    monkeypatch.setenv("GAMMA_OCR", "on")
    assert admin.get("/api/admin/settings").json()["options"]["ocr_env"] is True
    refused = admin.put("/api/admin/settings", json={"options": {"ocr": False}})
    assert refused.status_code == 400 and "GAMMA_OCR" in refused.json()["detail"]
    # a server whose pages are read in the window: nothing would run by itself, whatever the switch says
    monkeypatch.setattr(ocr_engine, "capability", lambda: {"where": "window", "engine": "e", "langs": [],
                                                            "models": "/m"})
    got = admin.get("/api/admin/settings").json()["options"]
    assert (got["ocr_auto"], got["text_recognition"]["server"]) == (True, False)
    assert anon.get("/api/server-config").json()["ocr"]["auto"] is False and not ocr_auto.enabled()
    assert anon.get("/api/admin/settings").status_code == 401
