"""A share visitor's file reads (GET /api/uploads/<file>?share=, /pdf-info)
check the shared pages only — a page share its page's subtree, a folder
share the pages filed there — never an instr() scan over every block of the
workspace; a yes is remembered for a while (bounded), a no never is; a
share editor's own upload is a yes at once; the endpoint runs in the
threadpool."""

import inspect
import io
from contextlib import closing

import pytest

from conftest import doc_props, login, make_folder, make_page, make_user, workspace_of
from gamma.db import connect_pages_db
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


def _pdf(tag: str = "") -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    if tag:
        w.add_metadata({"/Title": tag})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _upload_pdf(c, tag: str = "") -> str:
    r = c.post("/api/uploads", files={"file": ("p.pdf", _pdf(tag), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


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
    doc = _upload_pdf(owner)
    assert owner.put(f"/api/blocks/{page['id']}", json={"properties": doc_props(doc)}).status_code == 200
    token = _share(owner, page["id"])

    traced.clear()
    assert anon.get(mine, params={"share": token}).status_code == 200
    assert anon.get(f"/api/uploads/{doc}.pdf", params={"share": token}).status_code == 200
    assert anon.get(f"/api/pdf-info/{doc}", params={"share": token}).status_code == 200
    assert anon.get(theirs, params={"share": token}).status_code == 403
    # Every reference lookup read the shared page's blocks (by page_id);
    # none scanned the workspace's blocks.
    scans = [s for s in traced if "instr(content" in s]
    assert scans and all("WHERE page_id IN" in s for s in scans)


def test_a_page_share_reads_the_file_its_document_shows(owner, anon):
    """The file a shared page's ``doc`` names is the share's to read, of
    any kind of document: one shown from an address whose copy is stored
    under its hash, and the stored file a copy (a duplicated page) shows.
    No other file."""
    cached, copied, private = (_upload_pdf(owner, tag) for tag in ("cached", "copied", "private"))
    by_url = _share(owner, make_page(owner, "Shown from an address",
                                     doc_props(cached, url="https://example.org/sus.pdf"))["id"])
    duplicate = make_page(owner, "A duplicate", doc_props(url=f"/api/uploads/{copied}.pdf"))
    assert owner.get(f"/api/blocks/{duplicate['id']}").json()["properties"]["doc"]["copy"] is True
    by_copy = _share(owner, duplicate["id"])
    make_page(owner, "Owner of the private one", doc_props(private))
    assert anon.get(f"/api/uploads/{cached}.pdf", params={"share": by_url}).status_code == 200
    assert anon.get(f"/api/uploads/{copied}.pdf", params={"share": by_copy}).status_code == 200
    assert anon.get(f"/api/pdf-info/{copied}", params={"share": by_copy}).status_code == 200
    for token in (by_url, by_copy):
        assert anon.get(f"/api/uploads/{private}.pdf", params={"share": token}).status_code == 403
    assert anon.get(f"/api/uploads/{copied}.pdf", params={"share": by_url}).status_code == 403
    # a whole export through the share carries the same files (_pages_upload_names)
    with closing(connect_pages_db(workspace_of(USER))) as conn:
        assert uploads_router._pages_upload_names(conn, [duplicate["id"]]) == {f"{copied}.pdf"}


def test_a_page_share_reads_a_document_of_any_format(owner, anon):
    """A shared page's document is the share's to read whatever its format
    (a figure's PNG); another page's is refused."""
    from gamma.documents.model import document

    def png(tag):
        r = owner.post("/api/upload-file", files={"file": ("fig.png", b"\x89PNG\r\n\x1a\n" + tag * 16, "image/png")})
        assert r.status_code == 200, r.text
        return r.json()["url"].rsplit("/", 1)[1]

    mine, theirs = png(b"\x21"), png(b"\x22")
    page = make_page(owner, "A figure", properties={"doc": document(mine[:-4], name="fig.png", format="image", ext=".png")})
    make_page(owner, "Another figure", properties={"doc": document(theirs[:-4], name="fig2.png", format="image", ext=".png")})
    token = _share(owner, page["id"])
    assert anon.get(f"/api/uploads/{mine}", params={"share": token}).status_code == 200
    assert anon.get(f"/api/uploads/{theirs}", params={"share": token}).status_code == 403


def test_a_page_share_reads_the_file_a_copy_inside_it_shows(owner, anon):
    """A block inside the page that shows a stored file as a copy: the
    share reads it, as any upload a block names, and an export through it
    packs it. A document a block inside owns is no file of the share."""
    copied, owned = _upload_pdf(owner, "nested copy"), _upload_pdf(owner, "nested own")
    page = make_page(owner, "Holds a copy")
    r = owner.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "shNestedCopy", "parent": page["id"], "content": "",
         "props": doc_props(url=f"/api/uploads/{copied}.pdf")},
        {"op": "insert", "id": "shNestedOwn", "parent": page["id"], "content": "", "props": doc_props(owned)}]})
    assert r.status_code == 200, r.text
    token = _share(owner, page["id"])
    assert anon.get(f"/api/uploads/{copied}.pdf", params={"share": token}).status_code == 200
    assert anon.get(f"/api/pdf-info/{copied}", params={"share": token}).status_code == 200
    assert anon.get(f"/api/uploads/{owned}.pdf", params={"share": token}).status_code == 403
    with closing(connect_pages_db(workspace_of(USER))) as conn:
        assert uploads_router._pages_upload_names(conn, [page["id"]]) == {f"{copied}.pdf"}


def test_a_folder_share_reads_the_files_of_its_pages(owner, anon):
    lab = make_folder(owner, "sus/lab")
    inside = make_page(owner, "Filed page", properties={"folders": [lab]})
    outside = make_page(owner, "Unfiled page", properties={"folders": [make_folder(owner, "sus/other")]})
    fig_in, fig_out = _image(owner, b"\x03"), _image(owner, b"\x04")
    _note(owner, inside["id"], f"![a]({fig_in})")
    _note(owner, outside["id"], f"![b]({fig_out})")
    r = owner.post(f"/api/share/folder/{lab}", json={"audience": "anyone", "role": "view"})
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


def test_an_editor_reads_its_own_fresh_upload(owner, anon):
    """A share editor's image or file is readable through its share at
    once, before a saved block names it (a dropped picture shows without a
    reload); through another share it is not."""
    page = make_page(owner, "Edit share page")
    r = owner.post(f"/api/share/{page['id']}", json={"audience": "anyone", "role": "edit"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    other = _share(owner, make_page(owner, "Another shared page")["id"])
    png = b"\x89PNG\r\n\x1a\n" + b"\x06" * 16
    image = anon.post("/api/upload-image", params={"share": token}, files={"file": ("p.png", png, "image/png")})
    attached = anon.post("/api/upload-file", params={"share": token},
                         files={"file": ("n.txt", b"fresh notes", "text/plain")})
    for r in (image, attached):
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        assert anon.get(url, params={"share": token}).status_code == 200
        assert anon.get(url, params={"share": other}).status_code == 403


def test_serving_a_file_runs_in_the_threadpool():
    assert not inspect.iscoroutinefunction(uploads_router.serve_upload)
