"""Exports as background jobs (POST /api/jobs/export, routers/export.py):
a page or a folder in every format, the annotated PDFs of a folder as one
zip (subfolders as directories, pages without a PDF listed as skipped),
progress per page, a stop between pages, and the refusals."""

import io
import threading
import zipfile

import pytest

from conftest import login, make_folder, make_page, make_user
from gamma.markdown_export import slugify
from test_pdf_export import _blank_pdf, _position


@pytest.fixture(scope="module")
def user(client):
    make_user("ej_user", "ej-user-pw")
    return login("ej_user", "ej-user-pw")


def _pdf_page(c, title, folder, highlight=True):
    up = c.post("/api/uploads", files={"file": (f"{title}.pdf", _blank_pdf(pages=1) + title.encode(), "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(c, title, {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"],
                         "folders": [make_folder(c, folder)]})
    if highlight:
        r = c.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
            {"id": f"hl-{page['id']}", "content": "a note", "properties": {
                "quote": "q", "color": "rgba(255, 226, 143, 0.65)", "pdf_position": _position()}, "children": []}]})
        assert r.status_code == 200, r.text
    return page


def _export(c, **body):
    from gamma import jobs
    started = c.post("/api/jobs/export", json=body)
    assert started.status_code == 200, started.text
    assert started.json()["kind"] == "export"
    return jobs.wait(started.json()["id"])


def _download(c, job):
    assert job["state"] == "done", job["error"]
    r = c.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200, r.text
    return r


@pytest.fixture(scope="module")
def lab(user):
    """Folder "EJ lab": two PDF pages (one in a subfolder), a note page; one PDF page elsewhere."""
    pages = {
        "folder": make_folder(user, "EJ lab"),
        "top": _pdf_page(user, "Top paper", "EJ lab"),
        "deep": _pdf_page(user, "Deep paper", "EJ lab/Sub"),
        "note": make_page(user, "Just notes", {"folders": [make_folder(user, "EJ lab")]}),
        "outside": _pdf_page(user, "Outside paper", "EJ elsewhere"),
    }
    return pages


def test_a_folder_as_annotated_pdfs_in_one_zip(user, lab):
    from PyPDF2 import PdfReader

    job = _export(user, folder=lab["folder"], mode="annotated-pdf", highlights=True, notes=False)
    r = _download(user, job)
    assert job["artifact"]["name"] == "EJ lab-annotated.zip" and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["Sub/Deep paper.pdf", "Top paper.pdf"]
    annots = PdfReader(io.BytesIO(z.read("Top paper.pdf"))).pages[0]["/Annots"]
    assert any(a.get_object()["/Subtype"] == "/Highlight" for a in annots)
    assert job["result"]["pages"] == 2
    # skipped rows carry the page id too, so a review can key its rows by it
    assert [(p["title"], p["reason"]) for p in job["result"]["skipped"]] == [("Just notes", "page has no PDF")]
    assert job["result"]["skipped"][0]["page_id"] == lab["note"]["id"]
    assert job["progress"] == {"phase": "packing", "unit": "files", "done": 2, "total": 2}  # the walk counted 3 pages
    assert job["params"]["folder"] == lab["folder"] and job["params"]["name"] == "EJ lab" and job["params"]["mode"] == "annotated-pdf"


def test_one_page_as_its_annotated_pdf(user, lab):
    job = _export(user, page_id=lab["top"]["id"], mode="annotated-pdf", highlights=True, notes=False)
    r = _download(user, job)
    assert job["artifact"]["name"] == f"{slugify('Top paper', lab['top']['id'])}-annotated.pdf"
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    failed = _export(user, page_id=lab["note"]["id"], mode="annotated-pdf")
    assert failed["state"] == "failed" and failed["error"] == "page has no PDF"


def test_the_folder_download_offers_annotated_pdfs_too(user, lab):
    r = user.get(f"/api/folders/{lab['folder']}/export", params={"mode": "annotated-pdf", "notes": 0})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == ["Sub/Deep paper.pdf", "Top paper.pdf"]


@pytest.mark.parametrize("mode, page_suffix, folder_suffix", [
    ("readable", ".md", ".zip"),
    ("obsidian", "-obsidian.zip", "-obsidian.zip"),
    ("logseq-graph", "-logseq.zip", "-logseq.zip"),
    ("zotero-rdf", "-zotero.zip", "-zotero.zip"),
    ("gamma", "-gamma.zip", "-gamma.zip"),
    ("notes-pdf", "-notes.pdf", "-notes.pdf"),
])
def test_every_format_exports_as_a_job(user, lab, mode, page_suffix, folder_suffix):
    page_job = _export(user, page_id=lab["note"]["id"], mode=mode)
    assert page_job["artifact"]["name"] == f"{slugify('Just notes', lab['note']['id'])}{page_suffix}"
    assert _download(user, page_job).content
    folder_job = _export(user, folder=lab["folder"], mode=mode)
    assert folder_job["artifact"]["name"] == f"EJ lab{folder_suffix}"
    assert folder_job["result"] == {"pages": 3, "skipped": []}
    body = _download(user, folder_job).content
    assert body.startswith(b"%PDF") if mode == "notes-pdf" else zipfile.ZipFile(io.BytesIO(body)).namelist()


def test_a_bundled_zotero_export_packs_the_pdf_files(user, lab):
    job = _export(user, folder=lab["folder"], mode="zotero-rdf", pdf=True, highlights=True)
    names = zipfile.ZipFile(io.BytesIO(_download(user, job).content)).namelist()
    assert sum(name.endswith(".pdf") for name in names) == 2 and any(name.endswith(".rdf") for name in names)


def test_an_export_stops_between_pages(user, lab, monkeypatch):
    from gamma import jobs
    from gamma.routers import export

    reached, release = threading.Event(), threading.Event()
    real = export._AnnotatedPdfBuilder.add_page

    def slow(self, n, rows, page):
        reached.set()
        release.wait(10)
        return real(self, n, rows, page)

    monkeypatch.setattr(export._AnnotatedPdfBuilder, "add_page", slow)
    started = user.post("/api/jobs/export", json={"folder": lab["folder"], "mode": "annotated-pdf"}).json()
    assert reached.wait(10)
    assert user.post(f"/api/jobs/{started['id']}/cancel").json()["stopping"]
    release.set()
    job = jobs.wait(started["id"])
    assert job["state"] == "cancelled" and job["artifact"] is None
    assert not (jobs.root() / job["id"]).exists()


def test_what_an_export_job_refuses(user, lab):
    assert user.post("/api/jobs/export", json={"folder": lab["folder"], "mode": "pptx"}).status_code == 400
    assert user.post("/api/jobs/export", json={"folder": "no-such-folder"}).status_code == 404
    assert user.post("/api/jobs/export", json={}).status_code == 400
    assert user.post("/api/jobs/export", json={"page_id": "nope"}).status_code == 404
    gone = make_page(user, "Soon deleted", {"folders": [make_folder(user, "EJ trash")]})
    assert user.delete(f"/api/blocks/{gone['id']}").status_code == 200
    assert user.post("/api/jobs/export", json={"page_id": gone["id"]}).status_code == 404
