"""GET /api/metadata/status — the Settings pane's library-wide health list."""

from conftest import make_page, workspace_of, guest_name
from gamma.db import connect_data_db
from gamma.pdf_index import store_doc
from gamma.textnorm import normalize_text


def _paper(r, block_id):
    return next(p for p in r.json()["papers"] if p["id"] == block_id)


def test_status_lists_papers_not_notes(guest):
    with_meta = make_page(guest, "Has meta", properties={
        "doc_id": "a" * 24,
        "meta": {"title": "Proper Title", "source": "arxiv"},
    })
    failed = make_page(guest, "Lookup failed", properties={
        "source_url": "https://example.org/x.pdf",
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


def test_status_reads_index_state(guest):
    doc = "b" * 24
    page = make_page(guest, "Indexed paper", properties={"doc_id": doc})
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
        conn.execute("UPDATE pdf_fts_docs SET ver = ver - 1 WHERE doc_id = ?", (doc,))
    r = guest.get("/api/metadata/status")
    p = _paper(r, page["id"])
    assert p["indexed"] is False
    assert p["index_stale"] is True
