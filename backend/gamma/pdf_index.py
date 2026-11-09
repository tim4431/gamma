"""The PDF text index — ``pdf_fts`` in the workspace's data.db (derived from
the stored files, so it can be deleted and built again; the notes index is
in pages.db, gamma/block_index.py). The schema, the writes (a paper's rows
stored or dropped) and the queries every consumer of the index shares:
which papers still need extracting, the ranked hits for a MATCH, a paper's
pages. Extraction itself (and the background indexer job) lives in
routers/search.py; the same normalization rules as the notes index apply
(gamma.textnorm — bump INDEX_VERSION to re-index lazily).

FTS5 finds a row only by its rowid or by MATCH: a filter on ``doc_id`` (an
UNINDEXED column) reads the whole table. So ``pdf_fts_rows`` maps each paper
to the rowids of its rows, and everything that deletes or reads one paper
goes through it."""

import json
import sqlite3
from contextlib import contextmanager

from . import pdf_meta, storage, translate_store
from .blocks_store import attachment_file, load_props, page_attachment
from .db import connect_data_db, page_now
from .logbuf import log
from .textnorm import INDEX_VERSION

SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5(doc_id UNINDEXED, page UNINDEXED, content)",
    "CREATE TABLE IF NOT EXISTS pdf_fts_docs (doc_id TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, pages INTEGER, "
    "ver INTEGER NOT NULL DEFAULT 0, chars INTEGER)",
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


def _insert_rows(conn: sqlite3.Connection, doc_id: str, pages) -> None:
    """Insert a paper's ``(page, content)`` rows, each rowid recorded in
    ``pdf_fts_rows``."""
    tracked = []
    for page, content in pages:
        cur = conn.execute("INSERT INTO pdf_fts (doc_id, page, content) VALUES (?, ?, ?)", (doc_id, page, content))
        tracked.append((doc_id, cur.lastrowid))
    conn.executemany("INSERT INTO pdf_fts_rows (doc_id, fts_rowid) VALUES (?, ?)", tracked)


def _delete_rows(conn: sqlite3.Connection, doc_id: str) -> None:
    """Delete a paper's rows, by rowid through ``pdf_fts_rows``."""
    rowids = conn.execute("SELECT fts_rowid FROM pdf_fts_rows WHERE doc_id = ?", (doc_id,)).fetchall()
    conn.executemany("DELETE FROM pdf_fts WHERE rowid = ?", rowids)
    conn.execute("DELETE FROM pdf_fts_rows WHERE doc_id = ?", (doc_id,))


def _doc_columns(conn: sqlite3.Connection) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(pdf_fts_docs)")}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema. An index written before ``pdf_fts_rows`` existed
    (an older Gamma, a restored backup) is brought up to it once, in the
    transaction that creates that table: its rows are taken over, and a
    ``pdf_fts_docs`` older still gains ``ver`` (0: re-indexed by the next
    search). A ``pdf_fts_docs`` from before ``chars`` gains it, NULL, which
    ``doc_chars`` fills."""
    if conn.execute(_TABLE, ("pdf_fts_rows",)).fetchone():
        for stmt in SCHEMA:
            conn.execute(stmt)
        if "chars" not in _doc_columns(conn):
            with write_txn(conn):
                if "chars" not in _doc_columns(conn):  # again, under the write lock
                    conn.execute("ALTER TABLE pdf_fts_docs ADD COLUMN chars INTEGER")
        return
    with write_txn(conn):
        new = not conn.execute(_TABLE, ("pdf_fts_rows",)).fetchone()  # again, under the write lock
        rows = conn.execute(_TABLE, ("pdf_fts",)).fetchone()
        for stmt in SCHEMA:
            conn.execute(stmt)
        if new and rows:
            conn.execute("INSERT OR IGNORE INTO pdf_fts_rows (doc_id, fts_rowid) SELECT doc_id, rowid FROM pdf_fts")
        columns = _doc_columns(conn)
        if "ver" not in columns:
            conn.execute("ALTER TABLE pdf_fts_docs ADD COLUMN ver INTEGER NOT NULL DEFAULT 0")
        if "chars" not in columns:
            conn.execute("ALTER TABLE pdf_fts_docs ADD COLUMN chars INTEGER")


def store_doc(conn: sqlite3.Connection, doc_id: str, pages) -> None:
    """Replace a paper's rows with ``pages`` — ``(page number, normalized
    text)`` pairs; none records a paper without text, so a broken file isn't
    re-parsed on every search — and stamp it current, with its count of
    characters (``chars``, what ``doc_chars`` reads). STORE_CHUNK pages per
    transaction (committed here); until the last one the paper counts as
    missing (``pdf_missing``), and an interrupted run leaves only rows the
    next one deletes."""
    ensure_schema(conn)
    with write_txn(conn):
        conn.execute("DELETE FROM pdf_fts_docs WHERE doc_id = ?", (doc_id,))
        _delete_rows(conn, doc_id)
    for start in range(0, len(pages), STORE_CHUNK):
        with write_txn(conn):
            _insert_rows(conn, doc_id, pages[start:start + STORE_CHUNK])
    with write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO pdf_fts_docs (doc_id, indexed_at, pages, ver, chars) VALUES (?, ?, ?, ?, ?)",
            (doc_id, page_now(), len(pages), INDEX_VERSION, sum(len(content) for _, content in pages)))


def drop_docs(conn: sqlite3.Connection, doc_ids) -> None:
    """Delete these papers' rows and bookkeeping. The caller commits."""
    ensure_schema(conn)
    for doc_id in doc_ids:
        _delete_rows(conn, doc_id)
        conn.execute("DELETE FROM pdf_fts_docs WHERE doc_id = ?", (doc_id,))


def _live_docs(pages_conn: sqlite3.Connection) -> set:
    """The stored papers the workspace's blocks carry, Recently deleted
    included: by ``doc_id``, or by the upload address alone, which is all a
    duplicated page holds (``attachment_file``)."""
    live = {r[0] for r in pages_conn.execute(
        "SELECT DISTINCT doc_id FROM unified_blocks WHERE doc_id IS NOT NULL")}
    for row in pages_conn.execute("""SELECT properties FROM unified_blocks WHERE instr(properties, '"source_url"')"""):
        live.add(attachment_file(page_attachment(load_props(row[0]))))
    live.discard("")
    return live


def purge_unused(ws: str, pages_conn: sqlite3.Connection) -> None:
    """Sweep data.db after a PDF may have left the workspace (a page deleted
    for good, a block carrying a PDF deleted, a PDF detached, a restore):
    the index rows, the manifests and the kept translations of papers no
    block carries any more (``_live_docs``) — nothing else cleans them up.
    Reads every block's properties, so callers run it only when a PDF may
    have gone, and once for a pass over many pages (``trash._swept_after``).
    Never raises (derived data)."""
    try:
        live = _live_docs(pages_conn)
        with connect_data_db(ws) as conn:
            ensure_schema(conn)
            drop_docs(conn, [r[0] for r in conn.execute("SELECT doc_id FROM pdf_fts_docs") if r[0] not in live])
            pdf_meta.purge(conn, live)
            translate_store.purge(conn, live)
            conn.commit()
    except Exception as e:  # noqa: BLE001 — derived data: the next sweep tries again
        log.warning(f"[pdf_index] cleanup of unused papers failed: {e}")


def doc_pages(conn: sqlite3.Connection, doc_id: str, chars: int) -> list[tuple[int, str]]:
    """``(page, its first chars characters)`` of a paper's indexed pages, in
    page order — whichever index version is stored."""
    ensure_schema(conn)
    return conn.execute(
        "SELECT f.page, substr(f.content, 1, ?) FROM pdf_fts_rows r "
        "JOIN pdf_fts f ON f.rowid = r.fts_rowid WHERE r.doc_id = ? ORDER BY f.page",
        (chars, doc_id)).fetchall()


def doc_chars(conn: sqlite3.Connection) -> dict:
    """{doc_id: characters of indexed text} for every indexed paper: the
    count ``store_doc`` keeps on its ``pdf_fts_docs`` row, a lookup, never
    a pass over the text. A row stored before the count existed (NULL) is
    counted once from its rows (through ``pdf_fts_rows``) and the count
    kept."""
    ensure_schema(conn)
    if conn.execute("SELECT 1 FROM pdf_fts_docs WHERE chars IS NULL LIMIT 1").fetchone():
        with write_txn(conn):
            conn.execute(
                "UPDATE pdf_fts_docs SET chars = (SELECT COALESCE(SUM(LENGTH(f.content)), 0) FROM pdf_fts_rows r "
                "JOIN pdf_fts f ON f.rowid = r.fts_rowid WHERE r.doc_id = pdf_fts_docs.doc_id) WHERE chars IS NULL")
    return dict(conn.execute("SELECT doc_id, chars FROM pdf_fts_docs").fetchall())


def pdf_missing(data_conn: sqlite3.Connection, doc_ids) -> list[str]:
    """The given doc ids the index doesn't hold at the current INDEX_VERSION
    (never extracted, or extracted under older normalization rules) — what a
    search hands to the background indexer and reports as still pending."""
    ensure_schema(data_conn)
    current = {r[0] for r in data_conn.execute(
        "SELECT doc_id FROM pdf_fts_docs WHERE ver = ?", (INDEX_VERSION,))}
    return [d for d in doc_ids if d not in current]


def pdf_pending(ws: str, data_conn: sqlite3.Connection, doc_ids) -> list[str]:
    """``pdf_missing`` for a search, which starts the indexer on them and
    reports how many are pending. A linked file that cannot be read now
    (gamma/linked_files.py) is left out: no run could index it, so one would
    start on every search and the count would never come down. It is back
    when it is within reach again, or under a new name."""
    return [d for d in pdf_missing(data_conn, doc_ids)
            if storage.readable(ws, f"{d}.pdf") or not storage.is_linked(ws, f"{d}.pdf")]


def search_pdf(data_conn: sqlite3.Connection, match: str, limit: int,
               docs, offset: int = 0, per_doc: int = 0) -> list[tuple[str, int, str]]:
    """bm25-ranked ``(doc_id, page, snippet)`` hits for an FTS MATCH in the
    papers ``docs`` (a scope — rows of papers deleted or out of scope linger
    in the index and are passed by), ``limit`` of them from the ``offset``-th
    on. The scope is applied in SQL through ``pdf_fts_rows``, before the
    ranking, so a narrow scope finds its own hits however many others
    outrank them (``+rowid``: a constraint FTS5 would otherwise run as one
    lookup per rowid). Ties keep the index's order, the same on every call,
    so consecutive offsets neither repeat nor skip a hit.

    With ``per_doc``, a paper's hits past its best ``per_doc`` wait until
    every paper has listed its best ``per_doc`` (then each its next
    ``per_doc``, and so on), so one long book can't fill a page of results.
    The best ones come in rank order, so a first page stops reading early;
    a page past them reads every hit. A malformed MATCH is no results, not
    an error."""
    if not match or limit <= 0:
        return []
    ensure_schema(data_conn)
    offset = max(0, offset)
    sql = (f"SELECT doc_id, page, snippet(pdf_fts, 2, '', '', '…', {SNIPPET_TOKENS}) FROM pdf_fts "
           "WHERE pdf_fts MATCH ? AND +rowid IN (SELECT fts_rowid FROM pdf_fts_rows "
           "WHERE doc_id IN (SELECT value FROM json_each(?))) ORDER BY rank")
    args = (match, json.dumps(list(docs)))
    try:
        if per_doc <= 0:
            return data_conn.execute(sql + " LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        best, rest, seen = [], {}, {}
        for hit in data_conn.execute(sql, args):
            n = seen.get(hit[0], 0)
            seen[hit[0]] = n + 1
            if n < per_doc:
                best.append(hit)
                if len(best) >= offset + limit:
                    break
            else:
                rest.setdefault(n // per_doc, []).append(hit)
        else:
            for tier in sorted(rest):
                best += rest[tier]
        return best[offset:offset + limit]
    except sqlite3.OperationalError:
        return []
