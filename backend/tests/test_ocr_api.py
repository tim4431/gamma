"""The text recognition API (routers/ocr.py): POST /api/jobs/ocr starts the
``ocr`` job for a page's stored PDF (who may, which PDFs, one run at a time
for a workspace's file whoever starts it, 503 without an engine), GET
/api/ocr/{doc_id} and its pages read the recognition by the file's own read
rule (pages in bounds), /api/server-config says whether the server
recognizes text and /api/pdf-text-status whether a document reads
recognized words, a page recognized without any not counted. A fake
engine (tests/ocr_fake.py) but for the last test, which runs Tesseract on
a printed scan end to end."""

import io
import itertools
import threading

import pymupdf
import pytest
from conftest import drop_user, login, make_user, workspace_of
from ocr_fake import GARBLE, install, layered_scan
from ocr_fixtures import printed_scan_pdf

from gamma import jobs, ocr_engine, ocr_run, pdf_ocr, pdf_text, storage
from gamma.db import connect_data_db
from gamma.documents.model import url_hash
from gamma.routers.ocr import ELSEWHERE, GUESTS, STOPPING
from gamma.routers.pages import NOT_STORED
from gamma.search import index

PW = "oa-pass-12345"


@pytest.fixture(scope="module")
def alice(client):
    make_user("oa_alice", PW)
    return login("oa_alice", PW)


@pytest.fixture(scope="module")
def lab(client, alice):
    """A shared workspace alice owns and bob edits: ``(ws, bob's client)``."""
    make_user("oa_boss", PW, is_admin=1)
    make_user("oa_bob", PW)
    boss = login("oa_boss", PW)
    ws = boss.post("/api/workspaces", json={"name": "OCR lab", "kind": "shared", "owner": "oa_alice"}).json()["id"]
    assert alice.put(f"/api/workspaces/{ws}/members/oa_bob", json={"role": "editor"}).status_code == 200
    yield ws, login("oa_bob", PW)
    drop_user("oa_boss")


@pytest.fixture(autouse=True)
def _fresh_cache():
    pdf_text.text_cache_clear()
    yield
    pdf_text.text_cache_clear()


def _in(ws):
    return {"X-Gamma-Workspace": ws}


_copies = itertools.count()


def upload(c, data, headers=None):
    """Store ``data`` made a file of its own (a title of its own): a stored
    file belongs to one page of a workspace."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    doc.set_metadata({"title": f"Copy {next(_copies)}"})
    r = c.post("/api/uploads", files={"file": ("scan.pdf", io.BytesIO(doc.tobytes()), "application/pdf")},
               headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def scan_page(c, data=None, headers=None, title="A scanned paper"):
    """A page showing a stored scan: ``(page id, doc id)``."""
    doc = upload(c, data or printed_scan_pdf(dpi=72), headers)
    page = c.post("/api/pages", json={"title": title}, headers=headers).json()
    r = c.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc}, headers=headers)
    assert r.status_code == 200, r.text
    return page["id"], doc


def start(c, page_id, headers=None):
    return c.post("/api/jobs/ocr", json={"page_id": page_id}, headers=headers)


def test_a_run_through_the_api_and_what_it_reads_back(alice, monkeypatch):
    install(monkeypatch)
    page_id, doc = scan_page(alice, layered_scan(GARBLE))
    never = alice.get(f"/api/ocr/{doc}")
    assert never.status_code == 200 and never.headers["cache-control"] == "no-store"
    assert never.json() == {"stamp": "", "rev": 0, "engine": "", "langs": "", "pages": 0, "scanned": None,
                            "recognized": [], "kept": [], "at": "", "pending": False, "good_layer": False,
                            "todo": None, "staged": 0}
    r = start(alice, page_id)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "ocr" and job["title"] == "Recognizing the text of “A scanned paper”"
    assert job["params"] == {"page_id": page_id, "doc_id": doc, "title": "A scanned paper"}
    done = jobs.wait(job["id"])
    assert done["state"] == "done" and done["result"]["recognized"] == 1 and done["result"]["scanned"] == 1
    found = alice.get(f"/api/ocr/{doc}").json()
    assert (found["recognized"], found["kept"], found["scanned"], found["pages"], found["rev"]) == ([1], [], 1, 1, 1)
    assert (found["todo"], found["staged"]) == (0, 0), "nothing left for a run to read"
    assert len(found["stamp"]) == 16 and found["engine"] == "tesseract 5.5.4" and found["langs"] == "eng+fra+deu"
    pages = alice.get(f"/api/ocr/{doc}/pages", params={"first": 1, "last": 40})
    assert pages.status_code == 200 and pages.headers["cache-control"] == "no-store"
    body = pages.json()
    assert (body["stamp"], body["first"], body["last"], list(body["pages"])) == (found["stamp"], 1, 40, ["1"])
    words = [word[0] for line in body["pages"]["1"]["lines"] for word in line]
    assert "resonance" in words and all(len(word) == 5 for line in body["pages"]["1"]["lines"] for word in line)
    assert alice.get(f"/api/ocr/{doc}/pages", params={"first": 1, "last": 500}).json()["last"] == 64
    assert alice.get(f"/api/ocr/{doc}/pages", params={"first": 1, "last": 10 ** 20}).json()["last"] == 64
    assert alice.get(f"/api/ocr/{doc}/pages", params={"first": 3, "last": 2}).status_code == 400
    for first in (0, 10 ** 20):
        assert alice.get(f"/api/ocr/{doc}/pages", params={"first": first}).status_code == 422, \
            "a page past the pages a file is read to is refused, never handed to SQLite"
    assert alice.get("/api/ocr/not-a-hash").status_code == 400
    status = alice.get("/api/pdf-text-status", params={"doc_id": doc}).json()
    assert status["ok"] and status["ocr"]["recognized"] == 1 and status["ocr"]["at"] == found["at"]


def test_the_text_status_counts_only_pages_reading_recognized_words(alice, monkeypatch):
    install(monkeypatch, conf=40.0)  # a photo read as letters, none sure: its page is recognized without words
    page_id, doc = scan_page(alice)
    assert jobs.wait(start(alice, page_id).json()["id"])["result"]["empty"] == 1
    assert alice.get(f"/api/ocr/{doc}").json()["recognized"] == [1], "done: the menu need not offer it again"
    status = alice.get("/api/pdf-text-status", params={"doc_id": doc}).json()
    assert not status["ok"] and "ocr" not in status, "no page reads recognized words"


def test_who_may_start_and_which_pdfs(alice, guest, monkeypatch):
    install(monkeypatch)
    page_id, _doc = scan_page(guest)
    r = start(guest, page_id)
    assert r.status_code == 403 and r.json()["detail"] == GUESTS
    assert start(alice, "no-such-page").status_code == 404
    bare = alice.post("/api/pages", json={"title": "No PDF"}).json()
    assert start(alice, bare["id"]).status_code == 400
    # a PDF shown from its address: the proxy serves the copy stored under the address's hash, and only that
    url = "https://example.org/scan.pdf"
    stored = upload(alice, printed_scan_pdf(dpi=72))
    shown = alice.post("/api/pages", json={"title": "From its address"}).json()
    r = alice.post(f"/api/pages/{shown['id']}/attachment", json={"doc_id": stored, "source_url": url})
    assert r.status_code == 200, r.text
    refused = start(alice, shown["id"])
    assert refused.status_code == 400 and refused.json()["detail"] == NOT_STORED
    cached = url_hash(url)
    storage.put(workspace_of("oa_alice"), f"{cached}.pdf", printed_scan_pdf(dpi=72))
    proxied = alice.post("/api/pages", json={"title": "Its cached copy"}).json()
    r = alice.post(f"/api/pages/{proxied['id']}/attachment", json={"doc_id": cached, "source_url": url})
    assert r.status_code == 200, r.text
    job = start(alice, proxied["id"])
    assert job.status_code == 200, job.text
    assert jobs.wait(job.json()["id"])["state"] == "done"


def test_a_server_without_the_engine_answers_503(alice, monkeypatch):
    install(monkeypatch)
    page_id, _doc = scan_page(alice)
    monkeypatch.setattr(ocr_engine, "check", lambda: "Text recognition is turned off on this server.")
    r = start(alice, page_id)
    assert r.status_code == 503 and r.json()["detail"] == "Text recognition is turned off on this server."


def test_one_run_at_a_time_for_a_workspaces_file_whoever_starts_it(alice, lab, monkeypatch):
    install(monkeypatch)
    ws, bob = lab
    page_id, doc = scan_page(alice, headers=_in(ws))
    started, release = threading.Event(), threading.Event()
    real = ocr_run.run

    def held(*args, progress=None, **kwargs):
        started.set()
        assert release.wait(10), "the test never let the run go on"
        return real(*args, progress=progress, **kwargs)

    monkeypatch.setattr(ocr_run, "run", held)
    try:
        first = start(alice, page_id, _in(ws)).json()
        assert started.wait(10)
        # the look for a run going and the start are one turn of the jobs' lock: a start that looked in the
        # moment before the other's job went in still finds it
        monkeypatch.setattr(jobs, "live", lambda kind, key: None)
        again = start(alice, page_id, _in(ws))
        assert again.status_code == 200 and again.json()["id"] == first["id"], "the owner gets its own run back"
        other = start(bob, page_id, _in(ws))
        assert other.status_code == 409 and other.json()["detail"] == ELSEWHERE
        # another workspace's file is other work: it waits for its turn in the lane
        mine, _ = scan_page(alice)
        elsewhere = start(alice, mine)
        assert elsewhere.status_code == 200 and elsewhere.json()["id"] != first["id"]
        assert elsewhere.json()["state"] == "queued"
        assert jobs.cancel(first["id"])["stopping"]
        stopping = start(alice, page_id, _in(ws))
        assert stopping.status_code == 409 and stopping.json()["detail"] == STOPPING
    finally:
        release.set()
    assert jobs.wait(first["id"])["state"] == "cancelled"
    assert jobs.wait(elsewhere.json()["id"])["state"] == "done"
    assert (pdf_ocr.state(ws, doc) or {"recognized": []})["recognized"] == [], "a stopped run applies nothing"


def test_the_reads_follow_the_files_own_rule(alice, anon, monkeypatch):
    install(monkeypatch)
    page_id, doc = scan_page(alice)
    assert jobs.wait(start(alice, page_id).json()["id"])["state"] == "done"
    other_id, other_doc = scan_page(alice, printed_scan_pdf(dpi=72, layer="garbled"), title="Not shared")
    token = alice.post(f"/api/share/{page_id}", json={"audience": "anyone", "role": "view"}).json()["token"]
    seen = anon.get(f"/api/ocr/{doc}", params={"share": token})
    assert seen.status_code == 200 and seen.json()["recognized"] == [1]
    lines = anon.get(f"/api/ocr/{doc}/pages", params={"share": token, "first": 1})
    assert lines.status_code == 200 and list(lines.json()["pages"]) == ["1"]
    assert anon.get(f"/api/ocr/{other_doc}", params={"share": token}).status_code == 403
    assert anon.get(f"/api/ocr/{other_doc}/pages", params={"share": token}).status_code == 403
    assert anon.get(f"/api/ocr/{doc}").status_code == 401


def test_server_config_says_where_text_is_recognized(anon, monkeypatch):
    install(monkeypatch)
    assert anon.get("/api/server-config").json()["ocr"] == {"where": "server", "engine": "tesseract",
                                                            "langs": ["eng", "fra", "deu"], "auto": False}
    monkeypatch.setattr(ocr_engine, "capability", lambda: None)
    assert anon.get("/api/server-config").json()["ocr"] is None


def test_the_real_engine_recognizes_a_printed_scan_end_to_end(alice):
    pytest.importorskip("tesserocr")
    if ocr_engine.check():
        pytest.skip(f"no engine here: {ocr_engine.check()}")
    page_id, doc = scan_page(alice, printed_scan_pdf())
    job = start(alice, page_id)
    assert job.status_code == 200, job.text
    done = jobs.wait(job.json()["id"], timeout=120)
    assert done["state"] == "done" and done["result"]["recognized"] == 1, done
    ws = workspace_of("oa_alice")
    with connect_data_db(ws) as conn:
        assert [hit.doc_id for hit in index.search(conn, "resonance", 10, [doc])] == [doc]
    lines = alice.get(f"/api/ocr/{doc}/pages", params={"first": 1}).json()["pages"]["1"]["lines"]
    words = [word[0] for line in lines for word in line]
    assert "resonance" in words and "Quantum" in words
