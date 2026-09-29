"""A share visitor's file reads (GET /api/uploads/<file>?share=, /pdf-info)
check the shared pages only — a page share its page's subtree, a folder
share the pages filed there — never an instr() scan over every block of the
workspace; a yes is remembered for a while (bounded), a no never is; the
endpoint runs in the threadpool."""

import inspect
import io

import pytest

from conftest import login, make_page, make_user
from gamma.routers import uploads as uploads_router

USER, PASSWORD = "sus_owner", "pw-sus-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


@pytest.fixture(autouse=True)
def _fresh_cache():
    uploads_router._share_reads.clear()
    yield
    uploads_router._share_reads.clear()


def _image(c, tag: bytes) -> str:
    png = b"\x89PNG\r\n\x1a\n" + tag * 16
    r = c.post("/api/upload-image", files={"file": ("p.png", png, "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _pdf() -> bytes:
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _note(c, parent, content):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _share(c, page_id):
    r = c.post(f"/api/share/{page_id}", json={"audience": "anyone", "role": "view"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def traced(monkeypatch):
    seen: list[str] = []
    real = uploads_router.connect_pages_db

    def connect(ws):
        conn = real(ws)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(uploads_router, "connect_pages_db", connect)
    return seen


def test_a_page_share_reads_its_own_files_only(owner, anon, traced):
    page = make_page(owner, "Shared page")
    other = make_page(owner, "Private page")
    mine = _image(owner, b"\x01")
    theirs = _image(owner, b"\x02")
    parent = _note(owner, page["id"], "a note")
    _note(owner, parent, f"![fig]({mine})")  # nested: the subtree counts
    _note(owner, other["id"], f"![fig]({theirs})")
    doc = owner.post("/api/uploads", files={"file": ("p.pdf", _pdf(), "application/pdf")}).json()["doc_id"]
    assert owner.put(f"/api/blocks/{page['id']}", json={"properties": {"doc_id": doc}}).status_code == 200
    token = _share(owner, page["id"])

    traced.clear()
    assert anon.get(mine, params={"share": token}).status_code == 200
    assert anon.get(f"/api/uploads/{doc}.pdf", params={"share": token}).status_code == 200
    assert anon.get(f"/api/pdf-info/{doc}", params={"share": token}).status_code == 200
    assert anon.get(theirs, params={"share": token}).status_code == 403
    # Every reference lookup walked the shared page's subtree; none scanned
    # the workspace's blocks.
    scans = [s for s in traced if "instr(content" in s]
    assert scans and all("WITH RECURSIVE tree" in s for s in scans)


def test_a_folder_share_reads_the_files_of_its_pages(owner, anon):
    inside = make_page(owner, "Filed page", properties={"folder": "sus/lab"})
    outside = make_page(owner, "Unfiled page", properties={"folder": "sus/other"})
    fig_in, fig_out = _image(owner, b"\x03"), _image(owner, b"\x04")
    _note(owner, inside["id"], f"![a]({fig_in})")
    _note(owner, outside["id"], f"![b]({fig_out})")
    r = owner.post("/api/share/folder", params={"name": "sus/lab"}, json={"audience": "anyone", "role": "view"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert anon.get(fig_in, params={"share": token}).status_code == 200
    assert anon.get(fig_out, params={"share": token}).status_code == 403


def test_a_yes_is_remembered_a_no_is_not(owner, anon, monkeypatch):
    page = make_page(owner, "Cache page")
    fig = _image(owner, b"\x05")
    _note(owner, make_page(owner, "Keeps the file")["id"], f"![k]({fig})")  # never an orphan
    token = _share(owner, page["id"])
    # Not referenced yet: refused — and not remembered, so the reference
    # that lands a moment later is readable at once.
    assert anon.get(fig, params={"share": token}).status_code == 403
    block = _note(owner, page["id"], f"![c]({fig})")
    assert anon.get(fig, params={"share": token}).status_code == 200
    # Remembered: the reference going away doesn't refuse it within the TTL...
    assert owner.delete(f"/api/blocks/{block}").status_code == 200
    assert anon.get(fig, params={"share": token}).status_code == 200
    # ...and after it, the pages are read again.
    monkeypatch.setattr(uploads_router, "SHARE_READ_TTL_S", 0)
    uploads_router._share_reads.clear()
    assert anon.get(fig, params={"share": token}).status_code == 403


def test_the_memory_is_bounded(owner, anon, monkeypatch):
    monkeypatch.setattr(uploads_router, "SHARE_READ_MAX", 2)
    page = make_page(owner, "Bounded page")
    figs = [_image(owner, bytes([0x10 + i])) for i in range(4)]
    for fig in figs:
        _note(owner, page["id"], f"![x]({fig})")
    token = _share(owner, page["id"])
    for fig in figs:
        assert anon.get(fig, params={"share": token}).status_code == 200
    assert len(uploads_router._share_reads) == 2


def test_serving_a_file_runs_in_the_threadpool():
    assert not inspect.iscoroutinefunction(uploads_router.serve_upload)
