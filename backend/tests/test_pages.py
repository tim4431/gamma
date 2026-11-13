"""Stage-1 page endpoints (routers/pages.py): create a page, attach / replace /
detach a PDF, generic file uploads + serving, and the root listing's text preview."""

import io
import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from conftest import account_of, doc_props, login, make_folder, make_page, make_user, workspace_of, guest_name
from gamma import pdf_meta, upload_gc
from gamma.db import connect_data_db, connect_pages_db, ws_uploads_dir
from gamma.search import index as text_index

PDF_BYTES = b"%PDF-1.4 pages test\n" + b"z" * 2000


@pytest.fixture
def no_grace(monkeypatch):
    """No upload grace (upload_gc.UPLOAD_GRACE_S): these tests upload a file
    and drop its reference within milliseconds."""
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)


def _upload_pdf(client, data=PDF_BYTES):
    r = client.post("/api/uploads", files={"file": ("paper.pdf", io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


# --- POST /api/pages -------------------------------------------------------------

def test_create_page_defaults_and_folder(guest):
    r = guest.post("/api/pages", json={})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["parent_id"] == "root" and page["content"] == "Untitled" and page["properties"] == {}

    folder = make_folder(guest, "a/b")
    r = guest.post("/api/pages", json={"title": "  Reading list ", "folders": [folder, folder]})
    page = r.json()
    assert page["content"] == "Reading list" and page["properties"] == {"folders": [folder]}
    # a filing is stored as written, an id this copy has no folder for included
    # (a mirror's page); what is no list of ids is no filing
    kept = guest.post("/api/pages", json={"title": "Mirrored", "folders": ["notHereYet"]}).json()
    assert kept["properties"] == {"folders": ["notHereYet"]}
    dropped = guest.post("/api/pages", json={"title": "Odd", "properties": {"folders": "a/b"}}).json()
    assert dropped["properties"] == {}
    # it is a real root page: listed at root, no attachment
    ids = [b["id"] for b in guest.get("/api/blocks/root/children").json()["children"]]
    assert page["id"] in ids


def test_create_page_keeps_a_document_in_its_doc_only(guest):
    # the flat keys a document was kept in are refused; a doc is stored in canonical form
    for legacy in ({"doc_id": "d" * 24}, {"source_url": "https://example.org/a.pdf"}, {"linked_file": True}):
        r = guest.post("/api/pages", json={"title": "Old shape", "properties": legacy})
        assert r.status_code == 400 and "no longer a block property" in r.json()["detail"], legacy
    r = guest.post("/api/pages", json={"title": "Bad doc", "properties": {"doc": {"format": "pdf", "at": "store"}}})
    assert r.status_code == 400
    # a removal of an old key is no document (an older client's stale base)
    assert guest.post("/api/pages", json={"title": "x", "properties": {"doc_id": None}}).status_code == 200
    doc = {"at": "store", "hash": "d" * 24, "name": "dir/a.pdf", "format": "pdf"}
    page = guest.post("/api/pages", json={"title": "New shape", "properties": {"doc": doc}}).json()
    assert page["properties"]["doc"] == {"format": "pdf", "name": "a.pdf", "hash": "d" * 24, "ext": ".pdf",
                                         "at": "store"}
    assert list(page["properties"]["doc"]) == ["format", "name", "hash", "ext", "at"]


def test_create_page_needs_a_session():
    from gamma.app import app
    assert TestClient(app).post("/api/pages", json={"title": "x"}).status_code == 401


# --- attachment --------------------------------------------------------------------

def test_attach_stored_pdf_sets_attachment_and_automatic_title(guest):
    doc_id = _upload_pdf(guest, PDF_BYTES + b"a")
    page = guest.post("/api/pages", json={}).json()
    r = guest.post(f"/api/pages/{page['id']}/attachment",
                   json={"doc_id": doc_id, "original_filename": "dir/My Paper.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    props = body["properties"]
    # the stored copy's address is derived from its hash, the name is a leaf, the size the stored file's
    assert props["doc"] == {"format": "pdf", "name": "My Paper.pdf", "hash": doc_id, "ext": ".pdf", "at": "store",
                            "size": len(PDF_BYTES + b"a")}
    assert not any(k in props for k in ("doc_id", "source_url", "original_filename"))
    # "Untitled" was automatic → the file name, marked for the metadata worker
    assert body["content"] == "My Paper.pdf" and props["auto_title"] == "My Paper.pdf"
    assert guest.get(f"/api/blocks/{page['id']}").json()["content"] == "My Paper.pdf"
    # the by-attachment lookup now finds this page
    assert guest.get(f"/api/blocks/by-doc/{doc_id}").json()["id"] == page["id"]


def test_attach_keeps_a_user_title(guest):
    doc_id = _upload_pdf(guest, PDF_BYTES + b"b")
    page = guest.post("/api/pages", json={"title": "My own title"}).json()
    body = guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id}).json()
    assert body["content"] == "My own title" and "auto_title" not in body["properties"]


def test_attach_by_url_only_is_lazy(guest):
    """A URL-opened PDF: the client hashes the URL into doc_id, nothing is
    stored yet (the proxy fetches on open) — attach must not demand a file."""
    import hashlib
    url = "https://example.org/papers/lazy%20one.pdf"
    doc_id = hashlib.sha256(url.encode()).hexdigest()[:24]  # what the client's getDocIdForUrl yields
    page = guest.post("/api/pages", json={}).json()
    r = guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id, "source_url": url})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["properties"]["doc"] == {"format": "pdf", "name": "", "hash": doc_id, "ext": ".pdf", "at": "url",
                                         "url": url}
    assert body["content"] == "lazy one.pdf"  # URL tail while the title is automatic

    page2 = guest.post("/api/pages", json={}).json()
    r = guest.post(f"/api/pages/{page2['id']}/attachment", json={"source_url": "https://example.org/x.pdf"})
    assert r.status_code == 200
    assert r.json()["properties"]["doc"] == {"format": "pdf", "name": "", "hash": "", "ext": ".pdf", "at": "url",
                                             "url": "https://example.org/x.pdf"}


def test_attach_validation(guest):
    page = guest.post("/api/pages", json={}).json()
    assert guest.post(f"/api/pages/{page['id']}/attachment", json={}).status_code == 400
    assert guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": "../etc"}).status_code == 400
    assert guest.post("/api/pages/nope/attachment", json={"doc_id": "a" * 24}).status_code == 404
    child = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "note"}).json()
    assert guest.post(f"/api/pages/{child['id']}/attachment", json={"doc_id": "a" * 24}).status_code == 400


def test_second_attach_is_a_conflict(guest):
    doc_id = _upload_pdf(guest, PDF_BYTES + b"c")
    page = guest.post("/api/pages", json={}).json()
    assert guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id}).status_code == 200
    r = guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": "e" * 24})
    assert r.status_code == 409 and r.json()["detail"] == "page already has an attachment"


def test_attach_a_doc_another_page_owns_names_that_page(guest):
    doc_id = _upload_pdf(guest, PDF_BYTES + b"d")
    owner = guest.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "Owner"}).json()
    page = guest.post("/api/pages", json={}).json()
    r = guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id})
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": "attachment belongs to another page", "page_id": owner["id"]}
    # the page stayed text-only
    assert "doc" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


def _orphans(ws):
    """The workspace's upload_orphans names (gamma/upload_gc.py)."""
    upload_gc.flush(ws)
    with closing(connect_pages_db(ws)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM upload_orphans")}


def test_detach_clears_attachment_and_keeps_the_file(guest, no_grace):
    # the detached PDF stays, recorded as unreferenced, and a re-attach finds it
    ws = workspace_of(guest_name())
    doc_id = _upload_pdf(guest, PDF_BYTES + b"e")
    page = guest.post("/api/pages", json={"title": "Detach me"}).json()
    guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id, "original_filename": "x.pdf"})
    # a highlight child keeps its anchor
    hl = guest.post("/api/blocks", json={
        "parent_id": page["id"], "content": "quoted",
        "properties": {"pdf_position": {"pageNumber": 1}}}).json()
    assert (ws_uploads_dir(ws) / f"{doc_id}.pdf").is_file()

    r = guest.delete(f"/api/pages/{page['id']}/attachment")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and "removed_uploads" not in body
    assert (ws_uploads_dir(ws) / f"{doc_id}.pdf").is_file() and f"{doc_id}.pdf" in _orphans(ws)
    props = body["block"]["properties"]
    assert not any(k in props for k in ("doc", "original_filename"))
    assert guest.get(f"/api/blocks/{page['id']}").json()["properties"] == props
    assert guest.get(f"/api/blocks/{hl['id']}").json()["properties"]["pdf_position"] == {"pageNumber": 1}
    assert guest.get(f"/api/blocks/by-doc/{doc_id}").status_code == 404
    # nothing left to detach
    assert guest.delete(f"/api/pages/{page['id']}/attachment").status_code == 404
    # attached again, the file is in use again
    assert guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id}).status_code == 200
    assert f"{doc_id}.pdf" not in _orphans(ws)
    assert guest.get(f"/api/uploads/{doc_id}.pdf").status_code == 200


def test_attach_and_detach_take_the_markdown_source_name_along(guest):
    # a page made from a Markdown file with a PDF keeps the .md name beside its doc;
    # replacing or removing the PDF drops both, the import's own key stays
    doc_id = _upload_pdf(guest, PDF_BYTES + b"md-source")
    props = {"original_filename": "Reading.md", "markdown_import": "m" * 24,
             "doc": {"format": "pdf", "name": "", "hash": doc_id, "ext": ".pdf", "at": "store"}}
    page = guest.post("/api/pages", json={"title": "Reading", "properties": props}).json()
    assert page["properties"]["original_filename"] == "Reading.md"
    body = guest.delete(f"/api/pages/{page['id']}/attachment").json()
    assert body["block"]["properties"] == {"markdown_import": "m" * 24}
    other = guest.post("/api/pages", json={"title": "Reading 2", "properties": {**props, "doc": None}}).json()
    assert other["properties"] == {"original_filename": "Reading.md", "markdown_import": "m" * 24}
    # (the first page let it go, so the PDF is no other page's)
    attached = guest.post(f"/api/pages/{other['id']}/attachment",
                          json={"doc_id": doc_id, "original_filename": "r.pdf"}).json()
    assert "original_filename" not in attached["properties"] and attached["properties"]["doc"]["name"] == "r.pdf"


def test_replace_keeps_a_pdf_read_in_place_there_unless_it_comes_from_an_address(guest):
    # a page reading its PDF from a file on this computer (doc.at "path", gamma/linked_files.py)
    # reads the new bytes there too; a URL makes it one shown from that address
    old = "1" * 24
    page = guest.post("/api/pages", json={"title": "On disk", "properties": {
        "doc": {"format": "pdf", "name": "thesis.pdf", "hash": old, "ext": ".pdf", "at": "path"}}}).json()
    new = _upload_pdf(guest, PDF_BYTES + b"in-place")
    r = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": new, "original_filename": "thesis.pdf"})
    assert r.status_code == 200, r.text
    assert r.json()["properties"]["doc"] == {"format": "pdf", "name": "thesis.pdf", "hash": new, "ext": ".pdf",
                                             "at": "path", "size": len(PDF_BYTES + b"in-place")}
    # read in place, it is no library page of those bytes
    assert guest.get(f"/api/blocks/by-doc/{new}").status_code == 404
    url = "https://example.org/thesis.pdf"
    r = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": "2" * 24, "source_url": url})
    assert r.json()["properties"]["doc"] == {"format": "pdf", "name": "", "hash": "2" * 24, "ext": ".pdf",
                                             "at": "url", "url": url}
    # a stored page stays stored
    stored = guest.post("/api/pages", json={"title": "Stored"}).json()
    guest.post(f"/api/pages/{stored['id']}/attachment", json={"doc_id": _upload_pdf(guest, PDF_BYTES + b"s1")})
    again = _upload_pdf(guest, PDF_BYTES + b"s2")
    assert guest.put(f"/api/pages/{stored['id']}/attachment",
                     json={"doc_id": again}).json()["properties"]["doc"]["at"] == "store"


def test_detach_keeps_a_file_another_page_still_uses(guest, no_grace):
    ws = workspace_of(guest_name())
    doc_id = _upload_pdf(guest, PDF_BYTES + b"f")
    keeper = guest.post("/api/pages", json={"title": "Keeper"}).json()
    guest.post(f"/api/pages/{keeper['id']}/attachment", json={"doc_id": doc_id})
    other = guest.post("/api/pages", json={"title": "Other"}).json()
    # a chip reference (not an attachment) on another page also counts
    guest.post("/api/blocks", json={"parent_id": other["id"], "content": f"[paper](/api/uploads/{doc_id}.pdf)"})
    r = guest.delete(f"/api/pages/{keeper['id']}/attachment")
    assert r.status_code == 200
    assert f"{doc_id}.pdf" not in _orphans(ws)
    assert (ws_uploads_dir(ws) / f"{doc_id}.pdf").is_file()


# --- PUT /api/pages/{id}/attachment: replace the PDF --------------------------------

def _indexed(ws):
    """The papers data.db holds search rows for."""
    with closing(connect_data_db(ws)) as conn:
        text_index.ensure_schema(conn)
        return {r[0] for r in conn.execute("SELECT doc_id FROM text_fts_docs")}


def test_replace_swaps_the_pdf_and_keeps_the_page(guest, no_grace):
    # a new version of the file: the page keeps its id, title and highlights,
    # the old file is left to the orphan bookkeeping and its derived data goes
    ws = workspace_of(guest_name())
    old = _upload_pdf(guest, PDF_BYTES + b"r1")
    page = guest.post("/api/pages", json={"title": "My paper"}).json()
    guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": old, "original_filename": "main.pdf"})
    rect = {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    position = {"pageNumber": 2, "width": 612, "height": 792, "boundingRect": rect, "rects": [rect]}
    hl = guest.post("/api/blocks", json={
        "parent_id": page["id"], "content": "quoted", "properties": {"pdf_position": position}}).json()
    assert pdf_meta.ensure(ws, old)["doc_id"] == old  # the old file's manifest...
    with closing(connect_data_db(ws)) as conn:
        text_index.store_doc(conn, old, [(1, "old text")])  # ...and search rows
    assert old in _indexed(ws)

    new = _upload_pdf(guest, PDF_BYTES + b"r2")
    r = guest.put(f"/api/pages/{page['id']}/attachment",
                  json={"doc_id": new, "source_url": f"/api/uploads/{new}.pdf", "original_filename": "main-v2.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == page["id"] and body["content"] == "My paper"
    props = body["properties"]
    # the stored copy's address is derived, never stored (documents.model.document)
    assert props["doc"] == {"format": "pdf", "name": "main-v2.pdf", "hash": new, "ext": ".pdf", "at": "store",
                            "size": len(PDF_BYTES + b"r2")}
    assert guest.get(f"/api/blocks/{page['id']}").json()["properties"] == props
    # the highlight keeps its place, now naming the file it was measured on
    # (gamma/highlight_relocate.py; the run that would move it cannot read these bytes)
    assert guest.get(f"/api/blocks/{hl['id']}").json()["properties"]["pdf_position"] == {**position, "doc_id": old}
    # by attachment, the page is the new file's and no longer the old one's
    assert guest.get(f"/api/blocks/by-doc/{new}").json()["id"] == page["id"]
    assert guest.get(f"/api/blocks/by-doc/{old}").status_code == 404
    # the old file stays stored and in use while the highlight names it; the new one is the page's
    assert not {f"{old}.pdf", f"{new}.pdf"} & _orphans(ws)
    # the highlight drawn where it is (as Accept does): the old file is left to the orphan bookkeeping
    assert guest.put(f"/api/blocks/{hl['id']}", json={"properties": {"pdf_position": position}}).status_code == 200
    orphans = _orphans(ws)
    assert f"{old}.pdf" in orphans and f"{new}.pdf" not in orphans
    assert (ws_uploads_dir(ws) / f"{old}.pdf").is_file()
    # nothing carries the old paper, so its search rows and manifest went
    assert old not in _indexed(ws) and pdf_meta.get(ws, old) is None


def test_a_replacement_purges_the_outgoing_hash_alone(guest, no_grace):
    """A replacement lets go of one hash and purges that one (``purge_doc``,
    no pass over every block): another document's rows nobody carries wait
    for the next sweep; a copy still showing the outgoing file keeps its
    bytes, and its document is pending again, for the next search that
    reaches it (the agent's, which searches what a copy shows)."""
    ws = workspace_of(guest_name())
    old = _upload_pdf(guest, PDF_BYTES + b"p1")
    page = guest.post("/api/pages", json={"title": "Replaced"}).json()
    guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": old, "original_filename": "a.pdf"})
    copy = make_page(guest, "Replaced (copy)", properties=doc_props(url=f"/api/uploads/{old}.pdf"))
    with closing(connect_data_db(ws)) as conn:
        text_index.store_doc(conn, old, [(1, "outgoing text")])
        text_index.store_doc(conn, "pgstale0001", [(1, "nobody's text")])
    new = _upload_pdf(guest, PDF_BYTES + b"p2")
    r = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": new, "original_filename": "a-v2.pdf"})
    assert r.status_code == 200, r.text
    held = _indexed(ws)
    assert old not in held and "pgstale0001" in held
    assert guest.get(f"/api/blocks/{copy['id']}").json()["properties"]["doc"]["hash"] == old
    assert (ws_uploads_dir(ws) / f"{old}.pdf").is_file()
    with closing(connect_data_db(ws)) as conn:
        assert text_index.pending(ws, conn, {old: text_index.Doc(old, "pdf", ".pdf", "store")}) == [old]


def test_replace_a_proxied_pdf_with_an_upload_drops_its_address(guest):
    page = guest.post("/api/pages", json={"title": "Linked"}).json()
    r = guest.post(f"/api/pages/{page['id']}/attachment",
                   json={"doc_id": "c" * 24, "source_url": "https://example.org/paper.pdf"})
    assert r.json()["properties"]["doc"]["url"] == "https://example.org/paper.pdf"
    new = _upload_pdf(guest, PDF_BYTES + b"r3")
    props = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": new}).json()["properties"]
    assert props["doc"] == {"format": "pdf", "name": "", "hash": new, "ext": ".pdf", "at": "store",
                            "size": len(PDF_BYTES + b"r3")}


def test_replace_refuses_a_pdf_another_page_carries(guest):
    owner = guest.post("/api/pages", json={"title": "Owner"}).json()
    taken = _upload_pdf(guest, PDF_BYTES + b"r4")
    guest.post(f"/api/pages/{owner['id']}/attachment", json={"doc_id": taken})
    page = guest.post("/api/pages", json={"title": "Mine"}).json()
    mine = _upload_pdf(guest, PDF_BYTES + b"r5")
    guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": mine})
    r = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": taken})
    assert r.status_code == 409
    assert r.json() == {"detail": "attachment belongs to another page", "page_id": owner["id"]}
    assert guest.get(f"/api/blocks/{page['id']}").json()["properties"]["doc"]["hash"] == mine


def test_replace_with_the_same_pdf_changes_nothing(guest):
    page = guest.post("/api/pages", json={"title": "Same"}).json()
    doc_id = _upload_pdf(guest, PDF_BYTES + b"r6")
    body = {"doc_id": doc_id, "original_filename": "main.pdf"}
    attached = guest.post(f"/api/pages/{page['id']}/attachment", json=body).json()
    seq = guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"]
    r = guest.put(f"/api/pages/{page['id']}/attachment", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["properties"] == attached["properties"]
    assert r.json()["updated_at"] == attached["updated_at"]
    assert guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"] == seq  # no batch logged


def test_replace_drops_a_failed_lookup_of_the_old_file(guest):
    # the failed lookup read the old file; the new one is looked up again
    page = guest.post("/api/pages", json={"title": "Lookup"}).json()
    old = _upload_pdf(guest, PDF_BYTES + b"r8")
    guest.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": old})
    failed = {"at": "2026-10-01T00:00:00Z", "detail": "no arXiv id, DOI, Crossref, or AI match"}
    assert guest.put(f"/api/blocks/{page['id']}", json={"properties": {"meta_error": failed}}).status_code == 200
    # the same file again keeps it
    assert guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": old}).json()["properties"]["meta_error"] == failed
    new = _upload_pdf(guest, PDF_BYTES + b"r9")
    props = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": new}).json()["properties"]
    assert "meta_error" not in props
    assert "meta_error" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


def test_replace_on_a_page_without_a_pdf_attaches_it(guest):
    # as POST would: the automatic title becomes the file name
    page = guest.post("/api/pages", json={}).json()
    doc_id = _upload_pdf(guest, PDF_BYTES + b"r7")
    r = guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc_id, "original_filename": "notes.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "notes.pdf" and body["properties"]["auto_title"] == "notes.pdf"
    assert body["properties"]["doc"]["hash"] == doc_id


def test_replace_rejects_bad_input(guest):
    page = guest.post("/api/pages", json={"title": "x"}).json()
    assert guest.put(f"/api/pages/{page['id']}/attachment", json={}).status_code == 400
    assert guest.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": "../etc"}).status_code == 400
    assert guest.put("/api/pages/nope/attachment", json={"doc_id": "a" * 24}).status_code == 404
    child = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "c"}).json()
    assert guest.put(f"/api/pages/{child['id']}/attachment", json={"doc_id": "a" * 24}).status_code == 400


@pytest.fixture
def bob_page():
    make_user("pages_bob", "pw")
    bob = login("pages_bob", "pw")
    page = make_page(bob, "Bob's page")
    r = bob.post(f"/api/share/{page['id']}", json={"audience": "users", "role": "edit"})
    assert r.status_code == 200, r.text
    return bob, page, r.json()["token"]


def test_share_token_never_attaches(bob_page, guest):
    """Attachment is a page property: the owner's only. Anonymous with an edit
    share → 401; a guest (or any other account) with the token → the page is
    not in THEIR library (404), the token is ignored for this write."""
    from gamma.app import app
    bob, page, token = bob_page
    anon = TestClient(app)
    r = anon.post(f"/api/pages/{page['id']}/attachment", params={"share": token}, json={"doc_id": "a" * 24})
    assert r.status_code == 401
    r = guest.post(f"/api/pages/{page['id']}/attachment", params={"share": token}, json={"doc_id": "a" * 24})
    assert r.status_code == 404
    make_user("pages_carol", "pw")
    carol = login("pages_carol", "pw")
    r = carol.post(f"/api/pages/{page['id']}/attachment", params={"share": token}, json={"doc_id": "a" * 24})
    assert r.status_code == 404
    # nor replaces
    assert anon.put(f"/api/pages/{page['id']}/attachment", params={"share": token},
                    json={"doc_id": "a" * 24}).status_code == 401
    assert carol.put(f"/api/pages/{page['id']}/attachment", params={"share": token},
                     json={"doc_id": "a" * 24}).status_code == 404
    assert "doc" not in bob.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert anon.post("/api/pages", params={"share": token}, json={"title": "x"}).status_code == 401


def test_a_work_folders_document_is_its_vaults(no_grace):
    """A work folder's file reads its document where it is: a person's
    attach, replace and detach are refused with the ``locked`` conflict in
    the rule's words; the vault's own, from a token that names its client
    (``X-Gamma-Client: disk``), go through."""
    from conftest import fresh_client, mark_work_folder, work_props
    from gamma.auth import DISK_CLIENT
    from gamma.blocks_store import create_page
    from gamma.documents.model import document
    from gamma.integrations import create_token
    from gamma.work_folders.filing import refusal_text

    make_user("pages_vault", "pw-pages-vault")
    owner, ws, account = login("pages_vault", "pw-pages-vault"), workspace_of("pages_vault"), account_of("pages_vault")
    w = make_folder(owner, "pages-W")
    mark_work_folder(owner, w)
    with connect_pages_db(ws) as conn:
        pdf = create_page(conn, "main", work_props(w, "main.pdf", doc=document("4d" * 12, name="main.pdf", at="path")),
                          actor=account, client=DISK_CLIENT)["id"]
        note = create_page(conn, "notes", work_props(w, "notes.md"), actor=account, client=DISK_CLIENT)["id"]
    new = _upload_pdf(owner, b"%PDF-1.4 the vault's next version\n")

    def refused(r):
        assert r.status_code == 403 and r.json() == {"detail": refusal_text("document"), "conflict": "locked"}, r.text

    refused(owner.put(f"/api/pages/{pdf}/attachment", json={"doc_id": new}))
    refused(owner.delete(f"/api/pages/{pdf}/attachment"))
    refused(owner.post(f"/api/pages/{note}/attachment", json={"doc_id": new}))
    refused(owner.put(f"/api/pages/{pdf}/attachment", json={"doc_id": new}, headers={"X-Gamma-Client": "disk"}))
    vault = fresh_client()
    token = create_token(account, ws, "pages-vault", 90, scope="write")["token"]
    vault.headers.update({"Authorization": "Bearer " + token, "X-Gamma-Workspace": ws, "X-Gamma-Client": "disk"})
    r = vault.put(f"/api/pages/{pdf}/attachment", json={"doc_id": new, "original_filename": "main.pdf"})
    assert r.status_code == 200, r.text
    assert owner.get(f"/api/blocks/{pdf}").json()["properties"]["doc"]["hash"] == new


# --- generic file uploads --------------------------------------------------------------

def _upload_file(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/upload-file", files={"file": (name, io.BytesIO(data), ctype)})


def test_upload_file_takes_anything_but_executables(guest):
    r = _upload_file(guest, "notes.docx", b"PK\x03\x04docx-ish")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("/api/uploads/") and body["url"].endswith(".docx")
    assert body["name"] == "notes.docx" and body["size"] == len(b"PK\x03\x04docx-ish")
    assert body["already_existed"] is False
    assert _upload_file(guest, "notes.docx", b"PK\x03\x04docx-ish").json()["already_existed"] is True
    # extension is normalized from the display name (case, leaked directories)
    r = _upload_file(guest, "C:\\stuff\\Data.CSV", b"a,b\n1,2\n")
    assert r.status_code == 200 and r.json()["url"].endswith(".csv") and r.json()["name"] == "Data.CSV"
    # images route through the image path (extension from the declared type)
    r = _upload_file(guest, "shot", b"\x89PNG fake", "image/png")
    assert r.status_code == 200 and r.json()["url"].endswith(".png")
    # lab files: unknown extensions are fine (served as octet-stream downloads),
    # a compound suffix keeps its last part, no extension becomes .bin
    for name, ext in (("sim.nb", ".nb"), ("run.sh", ".sh"), ("fit.mat", ".mat"), ("pkg.tar.gz", ".gz"),
                      ("Makefile", ".bin"), ("weird.a-b", ".bin")):
        r = _upload_file(guest, name, b"whatever " + name.encode())
        assert r.status_code == 200 and r.json()["url"].endswith(ext) and r.json()["name"] == name, name
    r = guest.get(_upload_file(guest, "fit.mat", b"whatever fit.mat").json()["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")
    # executables are the one refusal
    for name in ("run.exe", "lib.dll", "setup.msi", "go.bat", "x.ps1", "Run.EXE"):
        r = _upload_file(guest, name, b"whatever")
        assert r.status_code == 400 and "executable" in r.json()["detail"], name
    assert _upload_file(guest, "fake.pdf", b"not a pdf").status_code == 400


def test_pdf_file_block_promotes_to_a_document_page(guest, no_grace):
    """A PDF dropped into a block (upload-file) is stored under the hash the
    PDF ingest uses, so by-doc on that hash opens it as a document page
    without a second upload; by-docs reports which hashes have pages."""
    data = PDF_BYTES + b"file-block"
    up = _upload_file(guest, "Supplement.pdf", data, "application/pdf").json()
    doc_id = up["url"].rsplit("/", 1)[-1][:-4]
    assert up["url"] == f"/api/uploads/{doc_id}.pdf"
    assert guest.get(f"/api/blocks/by-doc/{doc_id}").status_code == 404
    other = _upload_pdf(guest, PDF_BYTES + b"other")
    r = guest.post("/api/pages/by-docs", json={"doc_ids": [doc_id, other, "", "nope"]})
    assert r.status_code == 200 and r.json() == {"pages": {}, "files": {}}
    # promote: same hash, no re-upload, filed in the asking page's folder
    rydberg = make_folder(guest, "Projects/Rydberg")
    r = guest.post(f"/api/blocks/by-doc/{doc_id}", json={
        "default_title": "", "source_url": up["url"], "original_filename": "Supplement.pdf",
        "folder": rydberg})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["parent_id"] == "root" and page["content"] == "Supplement.pdf"
    # the file's own address is no address: the page stores it
    assert page["properties"]["doc"] == {"format": "pdf", "name": "Supplement.pdf", "hash": doc_id, "ext": ".pdf",
                                         "at": "store"}
    assert page["properties"]["folders"] == [rydberg]
    assert page["properties"]["auto_title"] == "Supplement.pdf"
    # a second promotion finds the page; the folder of an existing page is left alone
    again = guest.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "",
                                                             "folder": make_folder(guest, "Elsewhere")}).json()
    assert again["id"] == page["id"] and again["properties"]["folders"] == [rydberg]
    r = guest.post("/api/pages/by-docs", json={"doc_ids": [doc_id, other], "files": [f"{doc_id}.pdf", f"{other}.pdf"]})
    assert r.json() == {"pages": {doc_id: {"id": page["id"], "title": "Supplement.pdf"}},
                        "files": {f"{doc_id}.pdf": {"id": page["id"], "title": "Supplement.pdf"}}}
    # the same file referenced by a file block AND carried by a page survives either going away
    ref = guest.post("/api/blocks", json={"parent_id": page["id"], "content": f"[Supplement.pdf]({up['url']})"}).json()
    assert guest.delete(f"/api/blocks/{ref['id']}").status_code == 200
    ws = workspace_of(guest_name())
    upload_gc.reconcile(ws)
    orphans = _orphans(ws)
    assert f"{doc_id}.pdf" not in orphans and f"{other}.pdf" in orphans  # the never-attached one is the orphan
    assert (ws_uploads_dir(ws) / f"{doc_id}.pdf").exists()


def test_uploaded_files_are_served_with_the_right_headers(guest):
    docx = _upload_file(guest, "report.docx", b"PK\x03\x04report").json()["url"]
    r = guest.get(docx)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"

    md = _upload_file(guest, "readme.md", b"# hi\n").json()["url"]
    r = guest.get(md)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "content-disposition" not in r.headers  # inline

    html = _upload_file(guest, "page.html", b"<script>alert(1)</script>").json()["url"]
    r = guest.get(html)
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in r.headers["content-security-policy"]

    stem = docx.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    assert guest.get(f"/api/uploads/{stem}.exe").status_code == 400


def test_file_chips_keep_files_alive_and_shares_can_read_them(bob_page, no_grace):
    from gamma.app import app
    bob, page, token = bob_page
    ws = workspace_of("pages_bob")
    url = _upload_file(bob, "data.json", b'{"k": 1}').json()["url"]
    filename = url.rsplit("/", 1)[-1]
    # unreferenced → recorded as an orphan by the next reconciliation (kept 30 days)
    upload_gc.reconcile(ws)
    assert filename in _orphans(ws)
    chip = bob.post("/api/blocks", json={"parent_id": page["id"], "content": f"[data.json]({url})"}).json()
    # a chip naming it again clears the record in the same write
    assert filename not in _orphans(ws)
    # a share of the page reads the referenced file; the unreferenced one is refused
    anon = TestClient(app)
    make_user("pages_dave", "pw")
    dave = login("pages_dave", "pw")
    assert dave.get(url, params={"share": token}).status_code == 200
    other = _upload_file(bob, "secret.csv", b"x,y\n").json()["url"]
    assert dave.get(other, params={"share": token}).status_code == 403
    assert anon.get(url, params={"share": token}).status_code == 401  # audience: users
    bob.delete(f"/api/blocks/{chip['id']}")


def test_markdown_file_block_promotes_to_a_note_page(guest):
    """A .md dropped into a block is a file; POST /pages/from-file turns the
    stored upload into a note page (same importer as /import/markdown),
    once — by-docs then reports the page for the file's hash. Editing the
    page never touches the file."""
    md = b"---\ntitle: Squeezing notes\n---\n# Setup\n\n- first point\n  - nested\n- second point\n"
    up = _upload_file(guest, "Qubit controlled squeezing (2).md", md, "text/markdown").json()
    filename = up["url"].rsplit("/", 1)[-1]
    stem = filename[:-3]
    assert guest.post("/api/pages/by-docs", json={"doc_ids": [stem]}).json() == {"pages": {}, "files": {}}
    rydberg = make_folder(guest, "Projects/Rydberg")
    r = guest.post("/api/pages/from-file", json={"filename": filename, "folder": rydberg,
                                                 "original": "Qubit controlled squeezing (2).md"})
    assert r.status_code == 200, r.text
    body = r.json()
    page = body["page"]
    assert body["created"] is True and body["imported"] == 4
    assert page["parent_id"] == "root" and page["content"] == "Squeezing notes"
    assert page["properties"]["folders"] == [rydberg]
    assert page["properties"]["markdown_import"] == stem and "doc" not in page["properties"]
    tree = guest.get(f"/api/blocks/{page['id']}/subtree").json()["block"]["children"]
    assert [b["content"] for b in tree] == ["# Setup", "- first point", "- second point"] or tree[0]["content"].startswith("#")
    # the lookup now names the page, by hash and by stored name, and a second promotion returns it unchanged
    found = {"id": page["id"], "title": "Squeezing notes"}
    assert guest.post("/api/pages/by-docs", json={"doc_ids": [stem], "files": [filename]}).json() == {
        "pages": {stem: found}, "files": {filename: found}}
    again = guest.post("/api/pages/from-file", json={"filename": filename, "folder": "Elsewhere"}).json()
    assert again["created"] is False and again["page"]["id"] == page["id"]
    # editing the page leaves the stored file byte-identical
    guest.put(f"/api/blocks/{tree[0]['id']}", json={"content": "changed"})
    assert guest.get(up["url"]).content == md
    # only stored markdown names qualify
    assert guest.post("/api/pages/from-file", json={"filename": "notes.md"}).status_code == 400
    assert guest.post("/api/pages/from-file", json={"filename": f"{stem}.pdf"}).status_code == 400
    assert guest.post("/api/pages/from-file", json={"filename": "a" * 24 + ".md"}).status_code == 404
    # a title-less file is named after the chip's display name, minus the extension
    up2 = _upload_file(guest, "Plain notes.md", b"just text\n", "text/markdown").json()
    made = guest.post("/api/pages/from-file", json={"filename": up2["url"].rsplit("/", 1)[-1], "original": "Plain notes.md"}).json()
    assert made["page"]["content"] == "Plain notes"


# --- documents of any format: attach {file}, by-file, by-docs files ---------------------------

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x01" * 64


def _stored(client, name, data, ctype="application/octet-stream") -> str:
    """The stored name (``<hash><ext>``) of ``data`` uploaded as ``name``."""
    r = _upload_file(client, name, data, ctype)
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[-1]


def _attach(client, page_id, body, method="post"):
    return getattr(client, method)(f"/api/pages/{page_id}/attachment", json=body)


def test_attach_a_stored_file_of_any_format(guest):
    """``{file}`` names a stored file by its name; the format is settled on
    the server from the name, the extension and the first bytes
    (formats.stored_format), ``size`` is written, and one library page owns
    one stored file. A PDF's title is automatic, any other's is the name
    alone."""
    cases = [
        ("fig.png", PNG, "image/png", ".png", "image"),
        ("main.tex", b"\\documentclass{article}\n", "application/x-tex", ".tex", "text"),
        ("Makefile", b"all:\n\tmake\n", "application/octet-stream", ".bin", "text"),
        ("notes.foo", b"plain words of an unknown extension\n", "application/octet-stream", ".foo", "text"),
        ("blob.dat", b"\x00\x01\x02binary\x00bytes", "application/octet-stream", ".dat", "binary"),
    ]
    for name, data, ctype, ext, format in cases:
        stored = _stored(guest, name, data, ctype)
        assert stored.endswith(ext), (name, stored)
        page = guest.post("/api/pages", json={}).json()
        r = _attach(guest, page["id"], {"file": stored, "original_filename": f"dir/{name}"})
        assert r.status_code == 200, (name, r.text)
        props = r.json()["properties"]
        assert props["doc"] == {"format": format, "name": name, "hash": stored[:-len(ext)], "ext": ext,
                                "at": "store", "size": len(data)}, name
        assert r.json()["content"] == name and "auto_title" not in props, name
        assert guest.get(f"/api/blocks/{page['id']}").json()["properties"]["doc"] == props["doc"]
        other = guest.post("/api/pages", json={}).json()
        r = _attach(guest, other["id"], {"file": stored})
        assert r.status_code == 409 and r.json()["page_id"] == page["id"], name
        assert guest.post("/api/pages/by-docs", json={"files": [stored]}).json()["files"] == {
            stored: {"id": page["id"], "title": name}}, name


def test_a_pdf_under_another_extension_is_attached_as_a_pdf_copy(guest):
    """An arXiv download named by its id is stored as ``<hash>.12345v2``; its
    bytes say PDF, so the attach stores the same bytes as ``<hash>.pdf`` and
    the page carries a PDF, title automatic."""
    data = PDF_BYTES + b"arxiv"
    stored = _stored(guest, "2301.12345v2", data)
    assert stored.endswith(".12345v2")
    page = guest.post("/api/pages", json={}).json()
    r = _attach(guest, page["id"], {"file": stored, "original_filename": "2301.12345v2"})
    assert r.status_code == 200, r.text
    doc = r.json()["properties"]["doc"]
    assert doc == {"format": "pdf", "name": "2301.12345v2", "hash": stored.rsplit(".", 1)[0], "ext": ".pdf",
                   "at": "store", "size": len(data)}
    assert r.json()["properties"]["auto_title"] == "2301.12345v2"
    assert (ws_uploads_dir(workspace_of(guest_name())) / f"{doc['hash']}.pdf").read_bytes() == data
    assert guest.get(f"/api/blocks/by-doc/{doc['hash']}").json()["id"] == page["id"]


def test_a_stored_files_address_is_no_pdf_to_attach(guest):
    """A non-PDF upload's address as ``source_url`` is refused rather than
    shown as a PDF from there; ``file`` comes alone, names a stored file the
    workspace holds, and never a Markdown file (a note is made from it)."""
    stored = _stored(guest, "fig.png", PNG, "image/png")
    page = guest.post("/api/pages", json={}).json()
    r = _attach(guest, page["id"], {"source_url": f"/api/uploads/{stored}"})
    assert r.status_code == 400 and r.json()["detail"] == "Attach a stored file by its name (file)"
    assert _attach(guest, page["id"], {"file": stored, "doc_id": "a" * 24}).status_code == 400
    assert _attach(guest, page["id"], {"file": "not-a-stored-name"}).status_code == 400
    assert _attach(guest, page["id"], {"file": "a" * 24 + ".png"}).status_code == 404
    md = _stored(guest, "notes.md", b"# hi\n", "text/markdown")
    r = _attach(guest, page["id"], {"file": md})
    assert r.status_code == 400 and r.json()["detail"] == "A Markdown file becomes a note (POST /pages/from-file)"
    assert "doc" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


def test_a_replacement_keeps_the_pages_format(guest):
    """PUT replaces a picture by a picture (no relocation run, no stamp on
    its regions, which read as drawn on another version by their own hash)
    and refuses another format: detach first. A page without a document
    takes any."""
    v1, v2 = PNG + b"keep-v1", PNG + b"keep-v2"
    png, png2 = _stored(guest, "fig.png", v1, "image/png"), _stored(guest, "fig.png", v2, "image/png")
    page = guest.post("/api/pages", json={"title": "Figure"}).json()
    assert _attach(guest, page["id"], {"file": png, "original_filename": "fig.png"}).status_code == 200
    box = {"x1": 1, "y1": 1, "x2": 5, "y2": 5}
    region = {"width": 10, "height": 10, "boundingRect": box, "rects": [box], "hash": png[:-4]}
    block = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "",
                                            "properties": {"image_position": region}}).json()
    r = _attach(guest, page["id"], {"file": png2, "original_filename": "fig.png"}, "put")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["properties"]["doc"]["hash"] == png2[:-4] and body["properties"]["doc"]["size"] == len(v2)
    assert "job" not in body and "relocation_error" not in body
    assert guest.get(f"/api/blocks/{block['id']}").json()["properties"]["image_position"] == region
    tex = _stored(guest, "main.tex", b"\\relax\n")
    r = _attach(guest, page["id"], {"file": tex}, "put")
    assert r.status_code == 400 and r.json()["detail"] == "This page's document is image; detach it first"
    pdf = _upload_pdf(guest, PDF_BYTES + b"not-an-image")
    assert _attach(guest, page["id"], {"doc_id": pdf}, "put").status_code == 400
    assert guest.get(f"/api/blocks/{page['id']}").json()["properties"]["doc"]["hash"] == png2[:-4]
    empty = guest.post("/api/pages", json={}).json()
    assert _attach(guest, empty["id"], {"file": tex}, "put").status_code == 200


def test_by_file_makes_one_library_page_per_stored_file(guest):
    """``POST /pages/by-file``: the page that owns the stored file, else a
    new one, keyed by hash and extension; a PDF goes the by-doc way, a
    Markdown file is a note's (400)."""
    data = PNG + b"byfile"
    stored = _stored(guest, "fig.png", data, "image/png")
    folder = make_folder(guest, "Figures/2026")
    r = guest.post("/api/pages/by-file", json={"file": stored, "name": "dir/fig.png", "folder": folder})
    assert r.status_code == 200, r.text
    body = r.json()
    page = body["page"]
    assert body["created"] is True and page["parent_id"] == "root" and page["content"] == "fig.png"
    assert page["properties"]["doc"] == {"format": "image", "name": "fig.png", "hash": stored[:-4], "ext": ".png",
                                         "at": "store", "size": len(data)}
    assert page["properties"]["folders"] == [folder] and "auto_title" not in page["properties"]
    again = guest.post("/api/pages/by-file", json={"file": stored, "name": "fig.png",
                                                   "folder": make_folder(guest, "Elsewhere")}).json()
    assert again["created"] is False and again["page"]["id"] == page["id"]
    assert again["page"]["properties"]["folders"] == [folder]
    # the same bytes under another extension are another stored file, and another page
    txt = _stored(guest, "fig.txt", data)
    assert txt[:-4] == stored[:-4]
    other = guest.post("/api/pages/by-file", json={"file": txt, "name": "fig.txt"}).json()
    assert other["created"] is True and other["page"]["id"] != page["id"]
    assert other["page"]["properties"]["doc"]["format"] == "text"
    r = guest.post("/api/pages/by-docs", json={"files": [stored, txt, "nope", f"{stored[:-4]}.gif"]})
    assert r.json() == {"pages": {}, "files": {stored: {"id": page["id"], "title": "fig.png"},
                                              txt: {"id": other["page"]["id"], "title": "fig.txt"}}}
    md = _stored(guest, "notes.md", b"# hi\n", "text/markdown")
    r = guest.post("/api/pages/by-file", json={"file": md, "name": "notes.md"})
    assert r.status_code == 400 and "from-file" in r.json()["detail"]
    pdf_data = PDF_BYTES + b"byfile-pdf"
    pdf = _stored(guest, "paper.pdf", pdf_data, "application/pdf")
    r = guest.post("/api/pages/by-file", json={"file": pdf, "name": "paper.pdf"})
    assert r.status_code == 200 and r.json()["created"] is True
    pdf_page = r.json()["page"]
    assert pdf_page["properties"]["auto_title"] == "paper.pdf" and pdf_page["properties"]["doc"]["size"] == len(pdf_data)
    assert guest.get(f"/api/blocks/by-doc/{pdf[:-4]}").json()["id"] == pdf_page["id"]
    assert guest.post("/api/pages/by-file", json={"file": "a" * 24 + ".png"}).status_code == 404
    assert guest.post("/api/pages/by-file", json={"file": "../x"}).status_code == 400


def test_by_file_files_in_the_library_never_in_a_work_folder(guest):
    from conftest import mark_work_folder
    w = make_folder(guest, "byfile-W")
    mark_work_folder(guest, w)
    stored = _stored(guest, "data.json", b'{"k": 1}\n', "application/json")
    r = guest.post("/api/pages/by-file", json={"file": stored, "name": "data.json", "folder": w})
    assert r.status_code == 403 and r.json().get("conflict") == "locked", r.text


def test_an_images_own_extension_is_kept_and_media_are_served_with_their_types(guest):
    """``/upload-file`` keeps ``.jpeg`` for an image/jpeg (a document's stored
    name is its hash and extension, and a mirror pushing one lands it under
    the same name); a name without an extension, or with another image's,
    takes the declared type's. A ``.mov`` and a ``.m4v`` are served as video;
    a range of a stored file is answered (a player's seek, the code view's
    head)."""
    jpeg = _upload_file(guest, "photo.jpeg", b"\xff\xd8\xff" + b"j" * 40, "image/jpeg").json()["url"]
    assert jpeg.endswith(".jpeg")
    jpg = _upload_file(guest, "photo.jpg", b"\xff\xd8\xff" + b"j" * 40, "image/jpeg").json()["url"]
    assert jpg.endswith(".jpg") and jpg[:-4] == jpeg[:-5]
    assert _upload_file(guest, "shot", b"\x89PNG fake", "image/png").json()["url"].endswith(".png")
    assert _upload_file(guest, "pic.gif", b"\x89PNG fake2", "image/png").json()["url"].endswith(".png")
    for name, ctype in (("clip.mov", "video/quicktime"), ("clip.m4v", "video/x-m4v")):
        url = _upload_file(guest, name, b"\x00\x00\x00\x1cftypqt  " + name.encode() + b"v" * 40).json()["url"]
        r = guest.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith(ctype), name
    png = _upload_file(guest, "fig.png", PNG + b"range", "image/png").json()["url"]
    r = guest.get(png, headers={"Range": "bytes=0-7"})
    assert r.status_code == 206 and r.content == PNG[:8]
    assert r.headers["content-range"] == f"bytes 0-7/{len(PNG) + 5}"


# --- root listing preview ---------------------------------------------------------------

def test_root_listing_carries_a_text_preview(guest):
    empty = make_page(guest, "Empty page")
    page = make_page(guest, "Preview page")
    tree = [
        {"id": "pv1", "content": "First   line\nof notes", "properties": {}, "children": [
            {"id": "pv1a", "content": "nested (not in preview)", "properties": {}, "children": []},
        ]},
        {"id": "pv2", "content": "a highlight", "properties": {"pdf_position": {"pageNumber": 1}}, "children": []},
        {"id": "pv3", "content": "", "properties": {}, "children": []},
        {"id": "pv4", "content": "Second", "properties": {}, "children": []},
        {"id": "pv5", "content": "x" * 300, "properties": {}, "children": []},
        {"id": "pv6", "content": "never reached", "properties": {}, "children": []},
    ]
    assert guest.put(f"/api/blocks/{page['id']}/children", json={"blocks": tree}).status_code == 200
    by_id = {b["id"]: b for b in guest.get("/api/blocks/root/children").json()["children"]}
    assert by_id[empty["id"]]["preview"] == ""
    preview = by_id[page["id"]]["preview"]
    assert preview.startswith("First line of notes · Second · xxx")
    assert len(preview) == 240 and "highlight" not in preview and "nested" not in preview
    # only the root listing carries previews
    kids = guest.get(f"/api/blocks/{page['id']}/children").json()["children"]
    assert all("preview" not in k for k in kids)


def test_orphan_bookkeeping_spares_fresh_uploads(guest, monkeypatch):
    """A file is stored BEFORE the page/block referencing it is written; a
    reconciliation in that window must not count it as unreferenced."""
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 15 * 60)
    ws = workspace_of(guest_name())
    up = guest.post("/api/uploads", files={"file": ("fresh.pdf", io.BytesIO(PDF_BYTES + b"fresh"), "application/pdf")}).json()
    path = ws_uploads_dir(ws) / f"{up['doc_id']}.pdf"
    assert path.is_file()
    upload_gc.reconcile(ws)
    assert f"{up['doc_id']}.pdf" not in _orphans(ws)
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    upload_gc.reconcile(ws)
    assert f"{up['doc_id']}.pdf" in _orphans(ws)
    assert path.is_file()  # recorded, not deleted: that is 30 days away


# --- the page log's hygiene ---------------------------------------------------------

def _pages_conn(user=None):
    from gamma.db import connect_pages_db
    return connect_pages_db(workspace_of(user or guest_name()))


def test_root_listing_is_a_pure_read(guest):
    """A leaked upload path is repaired by the migration/restore normalizer
    (gamma/normalize.py), never by a listing: reads write nothing."""
    from gamma.blocks_store import touch_page
    from gamma.normalize import normalize_pages_db

    page = make_page(guest, "dir/leaked.pdf")
    leaked = {"format": "pdf", "name": "dir/leaked.pdf", "hash": "l" * 24, "ext": ".pdf", "at": "store"}
    with _pages_conn() as conn:
        conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?",
                     (json.dumps({"doc": leaked, "auto_title": "dir/leaked.pdf"}), page["id"]))
        conn.commit()
    listed = {b["id"]: b for b in guest.get("/api/blocks/root/children").json()["children"]}
    assert listed[page["id"]]["content"] == "dir/leaked.pdf"
    assert listed[page["id"]]["properties"]["doc"] == leaked
    with _pages_conn() as conn:
        assert normalize_pages_db(conn)["upload_path_titles"] == 1
        touch_page(conn, page["id"], "")  # as the restore that runs it touches what it puts back
        conn.commit()
    fixed = guest.get(f"/api/blocks/{page['id']}").json()
    assert fixed["content"] == "leaked.pdf" and fixed["properties"]["doc"] == {**leaked, "name": "leaked.pdf"}


def test_deleting_a_page_for_good_marks_it_deleted_and_drops_its_op_log(guest):
    from gamma.blocks_store import create_page

    page = make_page(guest, "Doomed")
    guest.post("/api/blocks", json={"parent_id": page["id"], "content": "note"}).raise_for_status()
    with _pages_conn() as conn:
        assert conn.execute("SELECT count(*) FROM page_ops WHERE page_id = ?", (page["id"],)).fetchone()[0] == 1
    # deleting moves it to Recently deleted; "Delete permanently" drops the log
    # (tests/test_page_trash.py covers the trash itself)
    r = guest.delete(f"/api/blocks/{page['id']}")
    assert r.status_code == 200, r.text
    assert guest.delete(f"/api/trash/{page['id']}").status_code == 200
    with _pages_conn() as conn:
        assert conn.execute("SELECT count(*) FROM page_ops WHERE page_id = ?", (page["id"],)).fetchone()[0] == 0
        row = conn.execute("SELECT kind, actor FROM page_changes WHERE page_id = ?", (page["id"],)).fetchone()
        assert row == ("deleted", account_of(guest_name()))
        # a page brought back under the same id is no longer "deleted"
        create_page(conn, "Back", actor=account_of(guest_name()), block_id=page["id"])
        assert conn.execute("SELECT kind FROM page_changes WHERE page_id = ?", (page["id"],)).fetchone() == ("live",)
    assert guest.get(f"/api/blocks/{page['id']}").json()["content"] == "Back"


def test_by_doc_backfill_on_an_existing_page_is_an_op(guest):
    """get_or_create_doc_page's marker backfill is a logged op batch on the
    page, like every write to an existing page."""
    doc_id = _upload_pdf(guest, PDF_BYTES + b"backfill")
    page = guest.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "paper.pdf"}).json()
    seq0 = guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"]
    again = guest.post(f"/api/blocks/by-doc/{doc_id}", json={
        "default_title": "paper.pdf", "original_filename": "paper.pdf"}).json()
    named = {"format": "pdf", "name": "paper.pdf", "hash": doc_id, "ext": ".pdf", "at": "store"}
    assert again["id"] == page["id"] and again["properties"]["doc"] == named
    log = guest.get(f"/api/pages/{page['id']}/ops?since={seq0}").json()
    assert len(log["batches"]) == 1
    (op,) = log["batches"][0]["ops"]
    assert op["op"] == "set" and op["id"] == page["id"] and op["props"] == {"doc": named}
    assert log["batches"][0]["actor"] == account_of(guest_name())
    # an address that came later is backfilled the same way; the name stays
    seq1 = guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"]
    url = "https://example.org/paper.pdf"
    again = guest.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "paper.pdf", "source_url": url}).json()
    assert again["properties"]["doc"] == {**named, "at": "url", "url": url}
    (op,) = guest.get(f"/api/pages/{page['id']}/ops?since={seq1}").json()["batches"][0]["ops"]
    assert op["props"] == {"doc": {**named, "at": "url", "url": url}}
    # and nothing is written when nothing is missing
    seq2 = guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"]
    guest.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "paper.pdf", "original_filename": "other.pdf"})
    assert guest.get(f"/api/blocks/{page['id']}/subtree").json()["seq"] == seq2
