"""The PDF text index — ``pdf_fts`` in the workspace's data.db, next to the
notes index (``block_index``). The schema, the writes (a paper's rows stored
or dropped) and the queries every consumer of the index shares: which papers
still need extracting, the ranked hits for a MATCH, a paper's pages.
Extraction itself (and the background indexer thread) lives in
routers/search.py; the same normalization rules as the notes index apply
(gamma.textnorm — bump INDEX_VERSION to re-index lazily).

FTS5 finds a row only by its rowid or by MATCH: a filter on ``doc_id`` (an
UNINDEXED column) reads the whole table. So ``pdf_fts_rows`` maps each paper
to the rowids of its rows, and everything that deletes or reads one paper
goes through it (``ensure_tracked`` / ``insert_tracked`` /
``delete_tracked``, shared with the notes index)."""

import sqlite3
from contextlib import contextmanager

from .db import page_now
from .textnorm import INDEX_VERSION

SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5(doc_id UNINDEXED, page UNINDEXED, content)",
    "CREATE TABLE IF NOT EXISTS pdf_fts_docs (doc_id TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, pages INTEGER, ver INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS pdf_fts_rows (doc_id TEXT NOT NULL, fts_rowid INTEGER NOT NULL, "
    "PRIMARY KEY (doc_id, fts_rowid)) WITHOUT ROWID",
)

SNIPPET_TOKENS = 14
STORE_CHUNK = 500   # pages written per transaction: a book never holds data.db's write lock for long

_TABLE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """One write transaction, the write lock taken up front (what is read
    inside it is what gets replaced), committed at the end — or, inside a
    transaction the caller already holds, just a part of that one."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def ensure_tracked(conn: sqlite3.Connection, schema, fts: str, key: str, takeover=()) -> None:
    """Apply an FTS index's ``schema`` (CREATE ... IF NOT EXISTS), among it
    the ``<fts>_rows`` side table mapping each ``key`` (a paper, a page) to
    the rowids of its rows. Rows written before that table existed — by an
    older Gamma, or in a restored backup — are taken over once, in the
    transaction that creates it, followed by the ``takeover`` statements."""
    side = f"{fts}_rows"
    if conn.execute(_TABLE, (side,)).fetchone():
        for stmt in schema:
            conn.execute(stmt)
        return
    with write_txn(conn):
        new = not conn.execute(_TABLE, (side,)).fetchone()  # again, under the write lock
        rows = conn.execute(_TABLE, (fts,)).fetchone()
        for stmt in schema:
            conn.execute(stmt)
        if new and rows:
            conn.execute(f"INSERT OR IGNORE INTO {side} ({key}, fts_rowid) SELECT {key}, rowid FROM {fts}")
            for stmt in takeover:
                conn.execute(stmt)


def insert_tracked(conn: sqlite3.Connection, fts: str, key: str, value: str, column: str, rows) -> None:
    """Insert ``(column value, content)`` rows under ``key`` = ``value`` (a
    paper, a page) into an ``ensure_tracked`` index, each rowid recorded in
    its side table."""
    tracked = []
    for other, content in rows:
        cur = conn.execute(f"INSERT INTO {fts} ({key}, {column}, content) VALUES (?, ?, ?)",
                           (value, other, content))
        tracked.append((value, cur.lastrowid))
    conn.executemany(f"INSERT INTO {fts}_rows ({key}, fts_rowid) VALUES (?, ?)", tracked)


def delete_tracked(conn: sqlite3.Connection, fts: str, key: str, value: str) -> None:
    """Delete the rows of ``key`` = ``value`` from an ``ensure_tracked``
    index, by rowid through its side table."""
    rowids = conn.execute(f"SELECT fts_rowid FROM {fts}_rows WHERE {key} = ?", (value,)).fetchall()
    conn.executemany(f"DELETE FROM {fts} WHERE rowid = ?", rowids)
    conn.execute(f"DELETE FROM {fts}_rows WHERE {key} = ?", (value,))


def ensure_schema(conn: sqlite3.Connection) -> None:
    ensure_tracked(conn, SCHEMA, "pdf_fts", "doc_id")
    try:  # older DBs predate the ver column
        conn.execute("ALTER TABLE pdf_fts_docs ADD COLUMN ver INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


def store_doc(conn: sqlite3.Connection, doc_id: str, pages) -> None:
    """Replace a paper's rows with ``pages`` — ``(page number, normalized
    text)`` pairs; none records a paper without text, so a broken file isn't
    re-parsed on every search — and stamp it current. STORE_CHUNK pages per
    transaction (committed here); until the last one the paper counts as
    missing (``pdf_missing``), and an interrupted run leaves only rows the
    next one deletes."""
    ensure_schema(conn)
    with write_txn(conn):
        conn.execute("DELETE FROM pdf_fts_docs WHERE doc_id = ?", (doc_id,))
        delete_tracked(conn, "pdf_fts", "doc_id", doc_id)
    for start in range(0, len(pages), STORE_CHUNK):
        with write_txn(conn):
            insert_tracked(conn, "pdf_fts", "doc_id", doc_id, "page", pages[start:start + STORE_CHUNK])
    with write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO pdf_fts_docs (doc_id, indexed_at, pages, ver) VALUES (?, ?, ?, ?)",
            (doc_id, page_now(), len(pages), INDEX_VERSION))


def drop_docs(conn: sqlite3.Connection, doc_ids) -> None:
    """Delete these papers' rows and bookkeeping. The caller commits."""
    ensure_schema(conn)
    for doc_id in doc_ids:
        delete_tracked(conn, "pdf_fts", "doc_id", doc_id)
        conn.execute("DELETE FROM pdf_fts_docs WHERE doc_id = ?", (doc_id,))


def doc_pages(conn: sqlite3.Connection, doc_id: str, chars: int) -> list[tuple[int, str]]:
    """``(page, its first chars characters)`` of a paper's indexed pages, in
    page order — whichever index version is stored."""
    ensure_schema(conn)
    return conn.execute(
        "SELECT f.page, substr(f.content, 1, ?) FROM pdf_fts_rows r "
        "JOIN pdf_fts f ON f.rowid = r.fts_rowid WHERE r.doc_id = ? ORDER BY f.page",
        (chars, doc_id)).fetchall()


def doc_chars(conn: sqlite3.Connection) -> dict:
    """{doc_id: characters of indexed text} for every paper with rows — one
    pass over the index, never one per paper."""
    ensure_schema(conn)
    return dict(conn.execute("SELECT doc_id, SUM(LENGTH(content)) FROM pdf_fts GROUP BY doc_id").fetchall())


def pdf_missing(data_conn: sqlite3.Connection, doc_ids) -> list[str]:
    """The given doc ids the index doesn't hold at the current INDEX_VERSION
    (never extracted, or extracted under older normalization rules) — what a
    search hands to the background indexer and reports as still pending."""
    ensure_schema(data_conn)
    current = {r[0] for r in data_conn.execute(
        "SELECT doc_id FROM pdf_fts_docs WHERE ver = ?", (INDEX_VERSION,))}
    return [d for d in doc_ids if d not in current]


def search_pdf(data_conn: sqlite3.Connection, match: str, limit: int,
               docs) -> list[tuple[str, int, str]]:
    """bm25-ranked ``(doc_id, page, snippet)`` hits for an FTS MATCH, limited
    to the doc ids in ``docs`` (a scope — rows of papers deleted or out of
    scope linger in the index and are skipped). A malformed MATCH is no
    results, not an error."""
    if not match or limit <= 0:
        return []
    ensure_schema(data_conn)
    found = []
    try:
        cur = data_conn.execute(
            f"SELECT doc_id, page, snippet(pdf_fts, 2, '', '', '…', {SNIPPET_TOKENS}) FROM pdf_fts "
            "WHERE pdf_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit * 3))
        for doc_id, page, snippet in cur:
            if doc_id not in docs:
                continue
            found.append((doc_id, page, snippet))
            if len(found) >= limit:
                break
    except sqlite3.OperationalError:
        pass
    return found
