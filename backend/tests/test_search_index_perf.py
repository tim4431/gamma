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


def test_an_index_older_than_its_version_column_gains_it_once(owner):
    """An index from before pdf_fts_docs had ``ver`` (so from before
    pdf_fts_rows too): the transaction that takes its rows over adds the
    column, 0, so the next search extracts the paper again. A current index
    is never altered."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        for table in ("pdf_fts_rows", "pdf_fts_docs", "pdf_fts"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("CREATE VIRTUAL TABLE pdf_fts USING fts5(doc_id UNINDEXED, page UNINDEXED, content)")
        conn.execute("CREATE TABLE pdf_fts_docs (doc_id TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, pages INTEGER)")
        conn.execute("INSERT INTO pdf_fts VALUES ('sipdoc21', 1, 'old one')")
        conn.execute("INSERT INTO pdf_fts_docs VALUES ('sipdoc21', '2026-07-01T00:00:00.000000Z', 1)")
        conn.commit()
        assert pdf_index.doc_chars(conn) == {"sipdoc21": len("old one")}
        assert conn.execute("SELECT doc_id, ver, pages FROM pdf_fts_docs").fetchall() == [("sipdoc21", 0, 1)]
        assert conn.execute("SELECT doc_id FROM pdf_fts_rows").fetchall() == [("sipdoc21",)]
        assert pdf_index.pdf_missing(conn, ["sipdoc21"]) == ["sipdoc21"]
        seen: list[str] = []
        conn.set_trace_callback(seen.append)
        pdf_index.ensure_schema(conn)
        assert not _ours(seen, r"ALTER|INSERT")


def test_a_papers_characters_are_counted_as_it_is_stored(owner):
    """The health table's counts (``doc_chars``) are a lookup of what
    ``store_doc`` kept, never a pass over the text; an index from before
    the count gains the column and counts each paper once."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        pdf_index.store_doc(conn, "sipdoc30", [(1, "abc"), (2, "défgh")])
        seen: list[str] = []
        conn.set_trace_callback(seen.append)
        assert pdf_index.doc_chars(conn)["sipdoc30"] == 8
        assert not _ours(seen, r"(FROM|JOIN) pdf_fts\b")
        conn.set_trace_callback(None)
        conn.execute("ALTER TABLE pdf_fts_docs DROP COLUMN chars")  # the index as an older Gamma left it
        conn.commit()
        assert pdf_index.doc_chars(conn)["sipdoc30"] == 8
        assert conn.execute("SELECT chars FROM pdf_fts_docs WHERE doc_id = 'sipdoc30'").fetchone() == (8,)
        seen.clear()
        conn.set_trace_callback(seen.append)
        assert pdf_index.doc_chars(conn)["sipdoc30"] == 8
        assert not _ours(seen, r"(FROM|JOIN) pdf_fts\b|ALTER|UPDATE")
        pdf_index.drop_docs(conn, ["sipdoc30"])
        conn.commit()
