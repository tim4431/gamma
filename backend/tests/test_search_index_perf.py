"""The document text index at scale (gamma/search/index.py): a document's
rows are found through ``text_fts_rows`` and deleted by rowid (a filter on
the UNINDEXED doc_id column reads the whole FTS table), stored a chunk of
units per transaction, several small documents in one (``store_docs``), an
index of an older layout dropped and made again, and the rows of documents
no block carries any more purged. The notes index keeps itself
(tests/test_notes_index.py)."""

import re
import sqlite3
from contextlib import closing

import pytest

from conftest import doc_props, login, make_page, make_user, workspace_of
from gamma.db import connect_pages_db, ws_db_path
from gamma.search import index as text_index

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


def _rows(ws, doc_id):
    with _data(ws) as conn:
        return sorted(conn.execute("SELECT unit, content FROM text_fts WHERE doc_id = ?", (doc_id,)))


def test_a_document_is_replaced_and_dropped_by_rowid(owner, monkeypatch):
    ws = workspace_of(USER)
    monkeypatch.setattr(text_index, "STORE_CHUNK", 2)  # several transactions for one document
    seen: list[str] = []
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        conn.set_trace_callback(seen.append)
        text_index.store_doc(conn, "sipdoc01", [(1, "old one"), (2, "old two"), (3, "old three")])
        text_index.store_doc(conn, "sipdoc02", [(1, "neighbour")])
        text_index.store_doc(conn, "sipdoc01", [(1, "new one"), (3, "new three"), (4, "new four"),
                                                (5, "new five"), (7, "new seven")])
        assert text_index.doc_units(conn, "sipdoc01", 5) == [(1, "new o"), (3, "new t"), (4, "new f"),
                                                             (5, "new f"), (7, "new s")]
        assert text_index.missing(conn, ["sipdoc02", "sipdoc01"]) == []
        assert text_index.doc_chars(conn)["sipdoc01"] == sum(len(t) for t in
                                                              ("new one", "new three", "new four", "new five", "new seven"))
        text_index.drop_docs(conn, ["sipdoc01"])
        conn.commit()
        assert text_index.missing(conn, ["sipdoc02", "sipdoc01"]) == ["sipdoc01"]
    assert _rows(ws, "sipdoc01") == []
    assert _rows(ws, "sipdoc02") == [(1, "neighbour")]
    assert _ours(seen, r"DELETE FROM text_fts WHERE rowid")
    assert not _ours(seen, r"FROM text_fts\b(?!_)[^;]*\bdoc_id\s*=")
    assert len(_ours(seen, r"^BEGIN IMMEDIATE")) >= 3 + 2  # the 5-unit document alone: delete, 3 chunks, stamp


def test_a_text_document_keeps_its_format_and_lines(owner):
    """A text file's row says its format, the lines indexed and whether the
    file was cut; a PDF's says ``pdf``, no lines."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        text_index.store_doc(conn, "sipdoc03", [(1, "\\section intro"), (3, "the end")], format="text",
                             lines=95, truncated=True)
        text_index.store_doc(conn, "sipdoc04", [(2, "a page")])
        assert text_index.doc_info(conn, "sipdoc03") == {
            "units": 2, "ver": text_index.INDEX_VERSION, "chars": len("\\section intro") + len("the end"),
            "format": "text", "lines": 95, "truncated": True}
        assert text_index.doc_info(conn, "sipdoc04")["format"] == "pdf"
        assert text_index.doc_info(conn, "sipdoc04")["lines"] == 0
        assert text_index.doc_info(conn, "sipdoc05") is None
        assert text_index.doc_lines(conn, ["sipdoc03", "sipdoc04", "sipdoc05"]) == {"sipdoc03": 95, "sipdoc04": 0}
        text_index.drop_docs(conn, ["sipdoc03", "sipdoc04"])
        conn.commit()


def test_a_batch_of_documents_is_one_transaction(owner, monkeypatch):
    """``store_docs`` writes small documents in one transaction; one past
    STORE_CHUNK units is stored on its own, in its chunks; then the index
    settles, a transaction a step (``settle``)."""
    ws = workspace_of(USER)
    monkeypatch.setattr(text_index, "STORE_CHUNK", 3)
    seen: list[str] = []
    with _data(ws) as conn:
        conn.set_trace_callback(seen.append)
        text_index.store_docs(conn, [
            (f"sipbatch{i}", [(1, f"small {i}")], {"format": "text", "lines": 1, "truncated": False})
            for i in range(10)] + [("sipbig01", [(p, f"page {p}") for p in range(1, 8)], {"format": "pdf"})])
        steps = len(_ours(seen, r"'merge'"))
        assert steps >= 1
        # the batch; the book: delete, 3 chunks, stamp; the settle's steps
        assert len(_ours(seen, r"^BEGIN IMMEDIATE")) == 1 + 1 + 3 + 1 + steps
        assert text_index.missing(conn, [f"sipbatch{i}" for i in range(10)] + ["sipbig01"]) == []
        assert text_index.doc_info(conn, "sipbatch3")["lines"] == 1
        assert len(text_index.doc_units(conn, "sipbig01", 10)) == 7
        text_index.drop_docs(conn, [f"sipbatch{i}" for i in range(10)] + ["sipbig01"])
        conn.commit()


def _merge_due(conn) -> bool:
    """Whether FTS5 has merge work due (one in progress, or a level of 4
    segments): a 'merge' step changes rows. Rolled back."""
    before = conn.total_changes
    conn.execute("INSERT INTO text_fts (text_fts, rank) VALUES ('merge', 100000)")
    due = conn.total_changes - before >= 2
    conn.rollback()
    return due


def test_a_bulk_write_leaves_no_merge_to_the_next_writer(owner, monkeypatch):
    """FTS5 merges its segments a share at a time as it writes, so a bulk
    write would leave a merge half done for the next small write (a
    replacement's ``purge_doc``, in a request) to carry on. The indexer's
    batches (``store_docs``) and the sweep (``purge_unused``) finish the
    merges they start (``settle``)."""
    ws = workspace_of(USER)
    monkeypatch.setattr(text_index, "STORE_CHUNK", 8)  # a segment a chunk: levels fill up
    book = [(p, " ".join(f"t{p}x{k}" for k in range(400))) for p in range(1, 241)]
    with _data(ws) as conn:
        text_index.store_docs(conn, [("sipbulk01", book, {"format": "pdf"})])
        assert not _merge_due(conn)
        text_index.store_docs(conn, [("sipbulk02", book[:80], {"format": "pdf"})])
    with closing(connect_pages_db(ws)) as pages:
        text_index.purge_unused(ws, pages)  # neither is a block's: both go
    with _data(ws) as conn:
        assert text_index.missing(conn, ["sipbulk01", "sipbulk02"]) == ["sipbulk01", "sipbulk02"]
        assert not _merge_due(conn)


def test_purge_keeps_documents_a_block_still_carries(owner):
    """The rows of a deleted page go; a document some other block still
    carries (only the full pass over properties can tell) stays."""
    ws = workspace_of(USER)
    gone = make_page(owner, "Paper to delete", properties=doc_props("sipdoc10"))
    holder = make_page(owner, "Holder page")
    _block(owner, holder["id"], "a nested carrier", doc_props("sipdoc11"))
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        text_index.store_doc(conn, "sipdoc10", [(1, "doomed text")])
        text_index.store_doc(conn, "sipdoc11", [(1, "kept text")])
    _delete_page(owner, gone["id"])
    assert _rows(ws, "sipdoc10") == []
    assert _rows(ws, "sipdoc11") == [(1, "kept text")]


def test_purge_keeps_the_documents_a_copy_shows(owner):
    """A duplicated page owns no file (the doc_id column leaves it out) but
    shows the original's: with the original deleted for good, the
    document's rows stay while the copy is there, in Recently deleted too,
    and go with it."""
    ws = workspace_of(USER)
    original = make_page(owner, "Paper copied", properties=doc_props("sipdoc12"))
    copy = make_page(owner, "Paper copied (copy)", properties=doc_props(url="/api/uploads/sipdoc12.pdf"))
    with closing(sqlite3.connect(ws_db_path(ws, "data.db"))) as conn:
        text_index.store_doc(conn, "sipdoc12", [(1, "shown text")])
    _delete_page(owner, original["id"])
    assert _rows(ws, "sipdoc12") == [(1, "shown text")]
    assert owner.delete(f"/api/blocks/{copy['id']}").status_code == 200
    with closing(connect_pages_db(ws)) as conn:
        text_index.purge_unused(ws, conn)
    assert _rows(ws, "sipdoc12") == [(1, "shown text")]
    assert owner.delete(f"/api/trash/{copy['id']}").status_code == 200
    assert _rows(ws, "sipdoc12") == []


def test_purge_doc_drops_one_hash_no_block_owns(owner):
    """``purge_doc`` asks the doc_id column for the one hash: rows of a
    hash a page owns stay, rows and the manifest of one nobody owns go."""
    ws = workspace_of(USER)
    make_page(owner, "Owner", properties=doc_props("sipdoc13"))
    with _data(ws) as conn:
        text_index.store_doc(conn, "sipdoc13", [(1, "owned")])
        text_index.store_doc(conn, "sipdoc14", [(1, "orphan")])
        conn.execute("INSERT OR REPLACE INTO pdf_docs (doc_id, bytes, pages, dims, ver, at) "
                     "VALUES ('sipdoc14', 1, 1, '[]', 1, '2026')")
        conn.commit()
    seen: list[str] = []
    with closing(connect_pages_db(ws)) as pages:
        pages.set_trace_callback(seen.append)
        text_index.purge_doc(ws, None, pages, "sipdoc13")
        text_index.purge_doc(ws, None, pages, "sipdoc14")
    assert not _ours(seen, r"properties")
    assert _rows(ws, "sipdoc13") == [(1, "owned")]
    assert _rows(ws, "sipdoc14") == []
    with _data(ws) as conn:
        assert not conn.execute("SELECT 1 FROM pdf_docs WHERE doc_id = 'sipdoc14'").fetchone()


def test_an_index_of_an_older_layout_is_dropped_and_made_again(owner):
    """The layout an older Gamma wrote (pdf_fts and its side tables) is not
    taken over: dropped on first use, every document missing, so the lazy
    indexer fills the new one; a current index is never touched again."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        for table in text_index.OLD_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute("CREATE VIRTUAL TABLE pdf_fts USING fts5(doc_id UNINDEXED, page UNINDEXED, content)")
        conn.execute("CREATE TABLE pdf_fts_docs (doc_id TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, pages INTEGER, "
                     "ver INTEGER NOT NULL DEFAULT 0, chars INTEGER)")
        conn.executemany("INSERT INTO pdf_fts VALUES ('sipdoc20', ?, ?)", [(1, "old one"), (2, "old two")])
        conn.execute("INSERT INTO pdf_fts_docs VALUES ('sipdoc20', '2026', 2, ?, 14)", (text_index.INDEX_VERSION,))
        conn.commit()
        assert text_index.missing(conn, ["sipdoc20"]) == ["sipdoc20"]
        assert text_index.doc_units(conn, "sipdoc20", 10) == []
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'pdf_fts'").fetchone()
        seen: list[str] = []
        conn.set_trace_callback(seen.append)
        text_index.ensure_schema(conn)
        assert not _ours(seen, r"DROP|ALTER|INSERT|CREATE (VIRTUAL )?TABLE (?!IF NOT EXISTS)")


def test_a_docs_table_missing_a_column_is_made_again(owner):
    """A ``text_fts_docs`` without a column of the schema (an index this
    layout's predecessor left) is no index to fill in: the three tables go
    and come back empty."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        text_index.store_doc(conn, "sipdoc22", [(1, "kept for a moment")])
        conn.execute("ALTER TABLE text_fts_docs DROP COLUMN truncated")
        conn.commit()
        assert text_index.missing(conn, ["sipdoc22"]) == ["sipdoc22"]
        assert "truncated" in [r[1] for r in conn.execute("PRAGMA table_info(text_fts_docs)")]
        assert text_index.doc_units(conn, "sipdoc22", 10) == []


def test_a_documents_characters_are_counted_as_it_is_stored(owner):
    """The health table's counts (``doc_chars``) are a lookup of what
    ``store_doc`` kept, never a pass over the text."""
    ws = workspace_of(USER)
    with _data(ws) as conn:
        text_index.store_doc(conn, "sipdoc30", [(1, "abc"), (2, "défgh")])
        seen: list[str] = []
        conn.set_trace_callback(seen.append)
        assert text_index.doc_chars(conn)["sipdoc30"] == 8
        assert not _ours(seen, r"(FROM|JOIN) text_fts\b")
        text_index.drop_docs(conn, ["sipdoc30"])
        conn.commit()
