"""The document text index: ``text_fts`` in the workspace's data.db (derived
from the stored files, so it can be dropped and built again; the notes
index is in pages.db, gamma/block_index.py). The schema, the writes (a
document's rows stored or dropped) and the queries every consumer of the
index shares: which documents still need extracting, the ranked hits for a
MATCH, a document's units. Extraction and the background job are
gamma/search/indexer.py's; the same normalization rules as the notes index
apply (gamma.textnorm: bump INDEX_VERSION to re-index lazily).

A row is a unit: a PDF's page, or a text file's run of
``documents.text.RUN_LINES`` lines (``unit`` the run's number, its lines
``text.unit_lines(unit, lines)``). The two are of one size
(``text.PAGE_CHARS``), so bm25 ranks them together in one table.

FTS5 finds a row only by its rowid or by MATCH: a filter on ``doc_id`` (an
UNINDEXED column) reads the whole table. So ``text_fts_rows`` maps each
document to the rowids of its rows, and everything that deletes or reads
one document goes through it.

FTS5 keeps its index in segments it merges as it goes (automerge): a write
does a share of merge work in proportion to what it wrote, so a bulk write
leaves a merge half done, and the next small write in a request (a
replacement's ``purge_doc``) would carry it on, the 64 x levels pages of
one work quantum. The bulk writers — ``store_docs`` (the indexing job's
batches) and ``purge_unused`` (a sweep) — finish the merges they start
(``settle``), in steps of SETTLE_PAGES pages, each its own transaction."""

import json
import sqlite3
from collections import namedtuple
from contextlib import contextmanager

from .. import pdf_meta, storage, translate_store
from ..blocks_store import load_props
from ..db import connect_data_db, page_now
from ..documents.model import file_hash, page_document, stored_name
from ..logbuf import log
from ..textnorm import INDEX_VERSION

SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(doc_id UNINDEXED, unit UNINDEXED, content)",
    "CREATE TABLE IF NOT EXISTS text_fts_docs (doc_id TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, units INTEGER, "
    "ver INTEGER NOT NULL DEFAULT 0, chars INTEGER, format TEXT NOT NULL DEFAULT 'pdf', "
    "lines INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS text_fts_rows (doc_id TEXT NOT NULL, fts_rowid INTEGER NOT NULL, "
    "PRIMARY KEY (doc_id, fts_rowid)) WITHOUT ROWID",
)
DOC_COLUMNS = {"doc_id", "indexed_at", "units", "ver", "chars", "format", "lines", "truncated"}
# Every table an index of any layout was kept in: dropped when the layout is not this one.
OLD_TABLES = ("pdf_fts", "pdf_fts_docs", "pdf_fts_rows", "text_fts", "text_fts_docs", "text_fts_rows")

SNIPPET_TOKENS = 14
STORE_CHUNK = 500   # units written per transaction: a book never holds data.db's write lock for long
SETTLE_PAGES = 256  # pages of segment merging per transaction while the index settles (``settle``)
# The linked documents ``pending`` tests for reach, at most, in one search.
PROBE_LIMIT = 200

_TABLE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"


class Doc(namedtuple("Doc", "hash format ext at")):
    """A document the index may hold: its hash, its format, its stored
    file's extension and where its bytes are read from (documents.model)."""
    __slots__ = ()

    @property
    def name(self) -> str:
        """The stored file's name (``stored_name``)."""
        return stored_name(self.hash, self.ext)


def as_doc(doc) -> Doc:
    """A ``Doc`` from a Doc, or from a view or dict with ``hash``,
    ``format``, ``ext`` and ``at`` (documents.model.page_document)."""
    if isinstance(doc, Doc):
        return doc
    return Doc(doc["hash"], doc["format"], doc["ext"], doc["at"])


Hit = namedtuple("Hit", "doc_id unit snippet content")


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


def _insert_rows(conn: sqlite3.Connection, doc_id: str, units) -> None:
    """Insert a document's ``(unit, content)`` rows, each rowid recorded in
    ``text_fts_rows``."""
    tracked = []
    for unit, content in units:
        cur = conn.execute("INSERT INTO text_fts (doc_id, unit, content) VALUES (?, ?, ?)", (doc_id, unit, content))
        tracked.append((doc_id, cur.lastrowid))
    conn.executemany("INSERT INTO text_fts_rows (doc_id, fts_rowid) VALUES (?, ?)", tracked)


def _delete_rows(conn: sqlite3.Connection, doc_id: str) -> None:
    """Delete a document's rows, by rowid through ``text_fts_rows``."""
    rowids = conn.execute("SELECT fts_rowid FROM text_fts_rows WHERE doc_id = ?", (doc_id,)).fetchall()
    conn.executemany("DELETE FROM text_fts WHERE rowid = ?", rowids)
    conn.execute("DELETE FROM text_fts_rows WHERE doc_id = ?", (doc_id,))


def _current(conn: sqlite3.Connection) -> bool:
    """Whether data.db holds the index in this layout: the rows table, and a
    docs table with every column of the schema."""
    if not conn.execute(_TABLE, ("text_fts_rows",)).fetchone():
        return False
    return DOC_COLUMNS <= {r[1] for r in conn.execute("PRAGMA table_info(text_fts_docs)")}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply the schema. An index in any other layout (an older Gamma's
    ``pdf_fts``, a restored backup) is not taken over: it is dropped and the
    schema created in one transaction, and every document reads as missing,
    so the lazy indexer fills it again, as a bumped INDEX_VERSION does."""
    if _current(conn):
        for stmt in SCHEMA:
            conn.execute(stmt)
        return
    with write_txn(conn):
        if _current(conn):  # again, under the write lock
            return
        for table in OLD_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        for stmt in SCHEMA:
            conn.execute(stmt)
    log.info("[search] search index rebuilt from scratch: the old layout is dropped, "
             "documents are indexed again at the next search")


def _stamp(conn: sqlite3.Connection, doc_id: str, units, format: str, lines: int, truncated: bool) -> None:
    """The document's bookkeeping row, current."""
    conn.execute(
        "INSERT OR REPLACE INTO text_fts_docs (doc_id, indexed_at, units, ver, chars, format, lines, truncated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (doc_id, page_now(), len(units), INDEX_VERSION, sum(len(content) for _, content in units), format,
         int(lines or 0), int(bool(truncated))))


def store_doc(conn: sqlite3.Connection, doc_id: str, units, *, format: str = "pdf", lines: int = 0,
              truncated: bool = False) -> None:
    """Replace a document's rows with ``units`` — ``(unit number, normalized
    text)`` pairs; none records a document without text, so a broken file
    isn't re-parsed on every search — and stamp it current, with its count
    of characters (``chars``, what ``doc_chars`` reads), its ``format``, its
    count of ``lines`` indexed (a text file's) and whether the file was cut
    (``truncated``). On its own, STORE_CHUNK units per transaction
    (committed here); until the last one the document counts as missing
    (``missing``), and an interrupted run leaves only rows the next one
    deletes. Inside a transaction the caller holds (``store_docs``) every
    write is part of that one."""
    ensure_schema(conn)
    units = list(units)
    with write_txn(conn):
        conn.execute("DELETE FROM text_fts_docs WHERE doc_id = ?", (doc_id,))
        _delete_rows(conn, doc_id)
    for start in range(0, len(units), STORE_CHUNK):
        with write_txn(conn):
            _insert_rows(conn, doc_id, units[start:start + STORE_CHUNK])
    with write_txn(conn):
        _stamp(conn, doc_id, units, format, lines, truncated)


def store_docs(conn: sqlite3.Connection, stored) -> None:
    """Store several documents, ``[(doc_id, units, {format, lines,
    truncated})]``, in one write transaction: the indexer's batch, so a
    work folder of small files is a few hundred transactions, not three per
    file. A document of more than STORE_CHUNK units is stored on its own
    (``store_doc``'s chunks), never holding the write lock for a book."""
    ensure_schema(conn)
    small, large = [], []
    for doc_id, units, meta in stored:
        (large if len(units) > STORE_CHUNK else small).append((doc_id, list(units), meta or {}))
    if small:
        with write_txn(conn):
            for doc_id, units, meta in small:
                conn.execute("DELETE FROM text_fts_docs WHERE doc_id = ?", (doc_id,))
                _delete_rows(conn, doc_id)
                _insert_rows(conn, doc_id, units)
                _stamp(conn, doc_id, units, meta.get("format", "pdf"), meta.get("lines", 0),
                       meta.get("truncated", False))
    for doc_id, units, meta in large:
        store_doc(conn, doc_id, units, format=meta.get("format", "pdf"), lines=meta.get("lines", 0),
                  truncated=meta.get("truncated", False))
    settle(conn)


def settle(conn: sqlite3.Connection) -> None:
    """Finish the segment merges the index has due — one in progress, and
    any level of ``usermerge`` (4) segments — after a bulk write, so the
    writer that made the segments merges them, not the next small write
    (FTS5's ``'merge'`` command, SETTLE_PAGES pages a transaction, until a
    step does no work: ``total_changes`` moving by less than 2). Inside a
    transaction the caller holds it does nothing (that one isn't its to
    commit). Never raises on a busy database: what is left is merged by
    later writes, as FTS5 would anyway."""
    if conn.in_transaction:
        return
    try:
        while True:
            before = conn.total_changes
            with write_txn(conn):
                conn.execute("INSERT INTO text_fts (text_fts, rank) VALUES ('merge', ?)", (SETTLE_PAGES,))
            if conn.total_changes - before < 2:
                return
    except sqlite3.OperationalError as e:
        log.warning(f"[search] the index's segment merge stopped: {e}")


def drop_docs(conn: sqlite3.Connection, doc_ids) -> None:
    """Delete these documents' rows and bookkeeping. The caller commits."""
    ensure_schema(conn)
    for doc_id in doc_ids:
        _delete_rows(conn, doc_id)
        conn.execute("DELETE FROM text_fts_docs WHERE doc_id = ?", (doc_id,))


def _live_docs(pages_conn: sqlite3.Connection) -> set:
    """The stored documents the workspace's blocks carry, of every format,
    Recently deleted included: by the ``doc_id`` column (the document a
    block owns), or by the file a copy shows (a duplicated page,
    ``file_hash``), which the column leaves out."""
    live = {r[0] for r in pages_conn.execute(
        "SELECT DISTINCT doc_id FROM unified_blocks WHERE doc_id IS NOT NULL")}
    for row in pages_conn.execute("""SELECT properties FROM unified_blocks WHERE instr(properties, '"copy"')"""):
        live.add(file_hash(page_document(load_props(row[0]))))
    live.discard("")
    return live


def purge_unused(ws: str, pages_conn: sqlite3.Connection) -> None:
    """Sweep data.db after a document may have left the workspace (a page
    deleted for good, a block carrying a document deleted, a document
    detached, a restore): the index rows, the manifests and the kept
    translations of documents no block carries any more (``_live_docs``) —
    nothing else cleans them up. Reads every block's properties, so callers
    run it only when a document may have gone, and once for a pass over
    many pages (``trash._swept_after``); a replacement purges the one hash
    it let go (``purge_doc``). Never raises (derived data)."""
    try:
        live = _live_docs(pages_conn)
        with connect_data_db(ws) as conn:
            ensure_schema(conn)
            drop_docs(conn, [r[0] for r in conn.execute("SELECT doc_id FROM text_fts_docs") if r[0] not in live])
            pdf_meta.purge(conn, live)
            translate_store.purge(conn, live)
            conn.commit()
            settle(conn)
    except Exception as e:  # noqa: BLE001 — derived data: the next sweep tries again
        log.warning(f"[search] cleanup of unused documents failed: {e}")


def purge_doc(ws: str, data_conn: sqlite3.Connection | None, pages_conn: sqlite3.Connection, doc_id: str) -> None:
    """The index rows and the manifest of the one document ``doc_id`` when
    no block owns it any more (the ``doc_id`` column, idx_ub_doc: no pass
    over the blocks' properties): what a replacement lets go. A copy still
    showing the hash is indexed again at the next search, one document;
    the kept translations are ``purge_unused``'s (kept text costs money,
    and a lingering one is swept later). ``data_conn`` None opens one.
    Never raises (derived data)."""
    if not doc_id:
        return
    try:
        if pages_conn.execute("SELECT 1 FROM unified_blocks WHERE doc_id = ? LIMIT 1", (doc_id,)).fetchone():
            return
        if data_conn is None:
            with connect_data_db(ws) as conn:
                _purge_one(conn, doc_id)
        else:
            _purge_one(data_conn, doc_id)
    except Exception as e:  # noqa: BLE001 — derived data: the next sweep tries again
        log.warning(f"[search] cleanup of {doc_id} failed: {e}")


def _purge_one(conn: sqlite3.Connection, doc_id: str) -> None:
    ensure_schema(conn)
    with write_txn(conn):
        drop_docs(conn, [doc_id])
        pdf_meta.drop(conn, [doc_id])


def doc_units(conn: sqlite3.Connection, doc_id: str, chars: int) -> list[tuple[int, str]]:
    """``(unit, its first chars characters)`` of a document's indexed units,
    in unit order — whichever index version is stored."""
    ensure_schema(conn)
    return conn.execute(
        "SELECT f.unit, substr(f.content, 1, ?) FROM text_fts_rows r "
        "JOIN text_fts f ON f.rowid = r.fts_rowid WHERE r.doc_id = ? ORDER BY f.unit",
        (chars, doc_id)).fetchall()


def doc_info(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    """The bookkeeping of one indexed document, ``{units, ver, chars,
    format, lines, truncated}``; None when the index never stored it."""
    ensure_schema(conn)
    row = conn.execute("SELECT units, ver, chars, format, lines, truncated FROM text_fts_docs WHERE doc_id = ?",
                       (doc_id,)).fetchone()
    if row is None:
        return None
    return {"units": row[0] or 0, "ver": row[1], "chars": row[2], "format": row[3], "lines": row[4] or 0,
            "truncated": bool(row[5])}


def doc_lines(conn: sqlite3.Connection, doc_ids) -> dict:
    """{doc_id: lines indexed} of these documents (a text file's count, 0
    for a PDF): what a hit's run is bounded by (``text.unit_lines``)."""
    ensure_schema(conn)
    return dict(conn.execute("SELECT doc_id, lines FROM text_fts_docs WHERE doc_id IN (SELECT value FROM json_each(?))",
                             (json.dumps(list(doc_ids)),)).fetchall())


def doc_chars(conn: sqlite3.Connection) -> dict:
    """{doc_id: characters of indexed text} for every indexed document: the
    count ``store_doc`` keeps on its ``text_fts_docs`` row, a lookup, never
    a pass over the text."""
    ensure_schema(conn)
    return dict(conn.execute("SELECT doc_id, chars FROM text_fts_docs").fetchall())


def missing(conn: sqlite3.Connection, docs) -> list[str]:
    """The hashes of ``docs`` (a dict hash → Doc, or an iterable of hashes)
    the index doesn't hold at the current INDEX_VERSION (never extracted,
    or extracted under older rules), sorted — what a search hands to the
    background indexer and reports as still pending."""
    ensure_schema(conn)
    current = {r[0] for r in conn.execute("SELECT doc_id FROM text_fts_docs WHERE ver = ?", (INDEX_VERSION,))}
    return sorted(d for d in set(docs) if d and d not in current)


def pending(ws: str, conn: sqlite3.Connection, docs: dict) -> list[str]:
    """``missing`` for a search, which starts the indexer on them and
    reports how many are pending. A linked file that cannot be read now
    (gamma/linked_files.py) is left out: no run could index it, so one would
    start on every search and the count would never come down. Which
    missing documents are linked is one query (``storage.linked_names``);
    the first PROBE_LIMIT of them are tested for reach, the rest count
    pending unprobed (the job skips one out of reach and records nothing,
    so it costs a read attempt there, not a stat here). As the job indexes
    the reachable ones, the ones out of reach come within the probe and
    drop out. One is back when it is within reach again, or under a new
    name."""
    found = missing(conn, docs)
    if not found:
        return found
    linked = storage.linked_names(ws, [docs[h].name for h in found])
    out, probed = [], 0
    for h in found:
        name = docs[h].name
        if name in linked and probed < PROBE_LIMIT:
            probed += 1
            if not storage.readable(ws, name):
                continue
        out.append(h)
    return out


def search(conn: sqlite3.Connection, match: str, limit: int, docs, offset: int = 0,
           per_doc: int = 0) -> list[Hit]:
    """bm25-ranked hits (``Hit(doc_id, unit, snippet, content)``: FTS5's
    snippet, and the unit's text a file hit's line is found in) for an FTS
    MATCH in the documents ``docs`` (a scope — rows of documents deleted
    or out of scope linger in the index and are passed by), ``limit`` of
    them from the ``offset``-th on. The scope is applied in SQL through
    ``text_fts_rows``, before the ranking, so a narrow scope finds its own
    hits however many others outrank them (``+rowid``: a constraint FTS5
    would otherwise run as one lookup per rowid). Ties keep the index's
    order, the same on every call, so consecutive offsets neither repeat
    nor skip a hit.

    With ``per_doc``, a document's hits past its best ``per_doc`` wait
    until every document has listed its best ``per_doc`` (then each its
    next ``per_doc``, and so on), so one long book can't fill a page of
    results. The best ones come in rank order, so a first page stops
    reading early; a page past them reads every hit. A malformed MATCH is
    no results, not an error."""
    if not match or limit <= 0:
        return []
    ensure_schema(conn)
    offset = max(0, offset)
    sql = (f"SELECT doc_id, unit, snippet(text_fts, 2, '', '', '…', {SNIPPET_TOKENS}), content FROM text_fts "
           "WHERE text_fts MATCH ? AND +rowid IN (SELECT fts_rowid FROM text_fts_rows "
           "WHERE doc_id IN (SELECT value FROM json_each(?))) ORDER BY rank")
    args = (match, json.dumps(list(docs)))
    try:
        if per_doc <= 0:
            return [Hit(*r) for r in conn.execute(sql + " LIMIT ? OFFSET ?", (*args, limit, offset))]
        best, rest, seen = [], {}, {}
        for hit in conn.execute(sql, args):
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
        return [Hit(*r) for r in best[offset:offset + limit]]
    except sqlite3.OperationalError:
        return []
