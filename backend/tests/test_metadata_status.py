"""GET /api/metadata/status — the Settings pane's library-wide health list."""

from conftest import doc_props, make_page, workspace_of, guest_name
from gamma.db import connect_data_db
from gamma.search.index import store_doc
from gamma.textnorm import normalize_text


def _paper(r, block_id):
    return next(p for p in r.json()["papers"] if p["id"] == block_id)


def test_status_lists_papers_not_notes(guest):
    with_meta = make_page(guest, "Has meta", properties={
        **doc_props("a" * 24),
        "meta": {"title": "Proper Title", "source": "arxiv"},
    })
    failed = make_page(guest, "Lookup failed", properties={
        **doc_props(url="https://example.org/x.pdf"),
        "meta_error": {"at": "2026-01-01T00:00:00Z", "detail": "no arXiv id, DOI, or AI match"},
    })
    note = make_page(guest, "Plain note page")
    cited = make_page(guest, "Notes on a paper I don't own", properties={
        "meta": {"title": "Remote Paper", "doi": "10.1/remote"},
    })

    r = guest.get("/api/metadata/status")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["papers"]]
    assert with_meta["id"] in ids and failed["id"] in ids
    assert note["id"] not in ids
    # metadata without an attachment still lists (it can cite), with no file
    assert cited["id"] in ids
    p3 = _paper(r, cited["id"])
    assert p3["has_file"] is False and p3["doc_id"] == "" and p3["has_meta"] is True
    assert p3["title"] == "Remote Paper" and p3["text_chars"] is None

    p1 = _paper(r, with_meta["id"])
    assert p1["has_meta"] is True
    assert p1["meta_source"] == "arxiv"
    assert p1["title"] == "Proper Title"  # metadata title wins over block content
    assert p1["text_chars"] is None       # never indexed → unknown

    p2 = _paper(r, failed["id"])
    assert p2["has_meta"] is False
    assert "no arXiv id" in p2["meta_error"]
    # when the record was stored, when a lookup last failed ("" when unknown)
    assert p1["meta_at"] == "" and p2["meta_error_at"] == "2026-01-01T00:00:00Z"


def test_status_names_the_file_a_page_owns(guest):
    """A duplicated page shows the original's file without owning it: listed,
    with no doc_id of its own. A PDF read in place (an opened file) is no
    library document, and no paper of the library until it is added."""
    copy = make_page(guest, "Copied paper", properties=doc_props(url=f"/api/uploads/{'c' * 24}.pdf"))
    linked = make_page(guest, "Paper read in place", properties=doc_props("e" * 24, at="path"))

    r = guest.get("/api/metadata/status")
    shown = _paper(r, copy["id"])
    assert shown["doc_id"] == "" and shown["has_file"] is False and shown["text_chars"] is None
    assert linked["id"] not in {p["id"] for p in r.json()["papers"]}


def test_status_lists_a_work_folders_file_once_it_is_in_the_library():
    from conftest import account_of, fresh_client, make_folder, mark_work_folder, work_props
    from gamma.auth import DISK_CLIENT
    from gamma.blocks_store import create_page
    from gamma.db import connect_pages_db
    from gamma.documents.model import document

    c = fresh_client()
    name = c.post("/api/login-guest").json()["username"]  # a library of its own
    w = make_folder(c, "status-work")
    mark_work_folder(c, w)
    with connect_pages_db(workspace_of(name)) as conn:
        def file_page(leaf, library):
            doc = document(leaf[0] * 24, name=leaf, at="path")
            return create_page(conn, leaf, work_props(w, leaf, library=library, doc=doc), actor=account_of(name),
                               client=DISK_CLIENT)["id"]
        figure, paper = file_page("figure.pdf", False), file_page("main.pdf", True)
    papers = {p["id"]: p for p in c.get("/api/metadata/status").json()["papers"]}
    assert figure not in papers and papers[paper]["doc_id"] == "m" * 24


def test_status_leaves_out_a_document_that_is_no_pdf_unless_it_has_a_record(guest):
    """A library page whose document is a picture is no paper: not listed,
    and the hand lookup refuses it (409); with a record it lists as a page
    citing a paper does, with no file."""
    from gamma.documents.model import document

    figure = make_page(guest, "A figure", properties={
        "doc": document("f" * 24, name="fig.png", format="image", ext=".png")})
    cited = make_page(guest, "A figure of a paper", properties={
        "doc": document("g" * 24, name="fig.png", format="image", ext=".png"),
        "meta": {"title": "Remote Paper", "doi": "10.1/fig"}})
    r = guest.get("/api/metadata/status")
    ids = {p["id"] for p in r.json()["papers"]}
    assert figure["id"] not in ids and cited["id"] in ids
    assert _paper(r, cited["id"])["doc_id"] == "" and _paper(r, cited["id"])["has_file"] is False
    r = guest.post("/api/metadata/fetch", json={"block_id": figure["id"]})
    assert r.status_code == 409 and r.json()["detail"] == "This page's document is no PDF: it has no paper's record to look up."


def test_status_reads_index_state(guest):
    doc = "b" * 24
    page = make_page(guest, "Indexed paper", properties=doc_props(doc))
    text = normalize_text("hello " * 20)
    ws = workspace_of(guest_name())
    with connect_data_db(ws) as conn:
        store_doc(conn, doc, [(1, text)])  # as the indexer stores a paper

    r = guest.get("/api/metadata/status")
    p = _paper(r, page["id"])
    assert p["indexed"] is True
    assert p["index_stale"] is False
    assert p["text_chars"] == len(text)

    # Bumped extraction version → the doc reads as stale, not indexed.
    with connect_data_db(ws) as conn:
        conn.execute("UPDATE text_fts_docs SET ver = ver - 1 WHERE doc_id = ?", (doc,))
    r = guest.get("/api/metadata/status")
    p = _paper(r, page["id"])
    assert p["indexed"] is False
    assert p["index_stale"] is True
