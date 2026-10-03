"""The PDF text index at scale (gamma/pdf_index.py): a paper's rows are
found through ``pdf_fts_rows`` and deleted by rowid (a filter on the
UNINDEXED doc_id column reads the whole FTS table), stored a chunk of pages
per transaction, an index written before the side table is taken over
without duplicates, and the rows of papers no block carries any more are
purged. The notes index keeps itself (tests/test_notes_index.py)."""

import re
import sqlite3
from contextlib import closing

import pytest

from conftest import login, make_page, make_user, workspace_of
from gamma import pdf_index
from gamma.db import ws_db_path

USER, PASSWORD = "sip_owner", "pw-sip-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _block(c, parent, content, props=None):
    r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _delete_page(c, page_id):
    """Delete a page for good: to Recently deleted, then out of it."""
    assert c.delete(f"/api/blocks/{page_id}").status_code == 200
    assert c.delete(f"/api/trash/{page_id}").status_code == 200


def _data(ws):
    return closing(sqlite3.connect(ws_db_path(ws, "data.db")))


def _ours(statements, pattern):
    return [s for s in statements if re.search(pattern, s)]


def _pdf_rows(ws, doc_id):
    with _data(ws) as conn:
        return sorted(conn.execute("SELECT page, content FROM pdf_fts WHERE doc_id = ?", (doc_id,)))


def test_a_paper_is_replaced_and_dropped_by_rowid(owner, monkeypatch):
    ws = workspace_of(USER)
    monkeypatch.setattr(pdf_index, "STORE_CHUNK", 2)  # several transactions for one paper
    seen: list[str] = []
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        conn.set_trace_callback(seen.append)
        pdf_index.store_doc(conn, "sipdoc01", [(1, "old one"), (2, "old two"), (3, "old three")])
        pdf_index.store_doc(conn, "sipdoc02", [(1, "neighbour")])
        pdf_index.store_doc(conn, "sipdoc01", [(1, "new one"), (3, "new three"), (4, "new four"),
                                               (5, "new five"), (7, "new seven")])
        assert pdf_index.doc_pages(conn, "sipdoc01", 5) == [(1, "new o"), (3, "new t"), (4, "new f"),
                                                            (5, "new f"), (7, "new s")]
        assert pdf_index.pdf_missing(conn, ["sipdoc01", "sipdoc02"]) == []
        assert pdf_index.doc_chars(conn)["sipdoc01"] == sum(len(t) for t in
                                                             ("new one", "new three", "new four", "new five", "new seven"))
        pdf_index.drop_docs(conn, ["sipdoc01"])
        conn.commit()
        assert pdf_index.pdf_missing(conn, ["sipdoc01"]) == ["sipdoc01"]
    assert _pdf_rows(ws, "sipdoc01") == []
    assert _pdf_rows(ws, "sipdoc02") == [(1, "neighbour")]
    assert _ours(seen, r"DELETE FROM pdf_fts WHERE rowid")
    assert not _ours(seen, r"FROM pdf_fts\b(?!_)[^;]*\bdoc_id\s*=")
    assert len(_ours(seen, r"^BEGIN IMMEDIATE")) >= 3 + 2  # the 5-page paper alone: delete, 3 chunks, stamp


def test_purge_keeps_papers_a_block_still_carries(owner):
    """The PDF rows of a deleted page go; a paper some other block still
    carries (only the full pass over properties can tell) stays."""
    ws = workspace_of(USER)
    gone = make_page(owner, "Paper to delete", properties={"doc_id": "sipdoc10"})
    holder = make_page(owner, "Holder page")
    _block(owner, holder["id"], "a nested carrier", {"doc_id": "sipdoc11"})
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        pdf_index.store_doc(conn, "sipdoc10", [(1, "doomed text")])
        pdf_index.store_doc(conn, "sipdoc11", [(1, "kept text")])
    _delete_page(owner, gone["id"])
    assert _pdf_rows(ws, "sipdoc10") == []
    assert _pdf_rows(ws, "sipdoc11") == [(1, "kept text")]


def test_an_index_written_before_the_side_table_is_taken_over(owner):
    """The layout an older Gamma wrote: pdf_fts and pdf_fts_docs, no
    pdf_fts_rows. Taken over on first use: every row tracked, a re-store
    replaces them by rowid instead of doubling them."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        for table in ("pdf_fts_rows", "pdf_fts_docs", "pdf_fts"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("CREATE VIRTUAL TABLE pdf_fts USING fts5(doc_id UNINDEXED, page UNINDEXED, content)")
        conn.executemany("INSERT INTO pdf_fts VALUES ('sipdoc20', ?, ?)", [(1, "old one"), (2, "old two")])
        conn.commit()
        assert pdf_index.doc_pages(conn, "sipdoc20", 10) == [(1, "old one"), (2, "old two")]
        pdf_index.store_doc(conn, "sipdoc20", [(1, "new one")])
    assert _pdf_rows(ws, "sipdoc20") == [(1, "new one")]
