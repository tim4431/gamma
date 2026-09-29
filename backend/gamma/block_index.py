"""Full-text index over note blocks — ``block_fts`` in the workspace's data.db,
next to the PDF index (``pdf_fts``, gamma/pdf_index.py).

Every non-root block's content is indexed under its page root (highlights
included — their note text is a note like any other; the quoted PDF passage
is already in ``pdf_fts``). Text is stored normalized (gamma.textnorm — the
same rules and INDEX_VERSION as the PDF index) so one query matches both.

The index is kept per page. A page is stale when its bookkeeping row
(``block_fts_meta``) is missing, older than INDEX_VERSION (``ver`` 0 after
``mark_page_dirty`` / ``mark_all_dirty``) or no longer matches the page
root's ``updated_at``. That fingerprint covers every write that touches the
page root (every op batch, imports, the agent tools, clips); the writers
that change a child without touching the root (a subtree replaced under a
nested block, a cross-page move) call ``mark_page_dirty``. A stale page is
rebuilt in one short data.db transaction (``index_page``), by:

- a search (``refresh``), for up to REFRESH_BUDGET_S, so a page edited a
  moment ago is found; what is left goes to the background refresher and
  the search reports it as still indexing;
- the background refresher — one thread per process — which also re-indexes
  every page an op batch changed once the page has been quiet for QUIET_S
  (``page_changed``, an ``ops.commit_listeners`` entry registered by
  routers/search.py), so an import is indexed before anyone searches.

FTS5 finds a row only by its rowid or by MATCH — a filter on ``page_id``, an
UNINDEXED column, reads the whole table — so ``block_fts_rows`` maps each
page to the rowids of its rows, and a page's rows are deleted by rowid
(``pdf_index.ensure_tracked`` and its two helpers). ``block_fts_meta`` lists
every page that has rows, so pruning deleted pages reads it alone.

No positions are stored: the frontend re-finds the match in the block text.
"""

import sqlite3
import threading
import time

from . import pdf_index, pdf_meta
from .blocks_store import fetch_subtree
from .db import connect_data_db, connect_pages_db, ws_dir
from .logbuf import log
from .textnorm import INDEX_VERSION, normalize_text

SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(block_id UNINDEXED, page_id UNINDEXED, content)",
    "CREATE TABLE IF NOT EXISTS block_fts_meta (page_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, "
    "ver INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS block_fts_rows (page_id TEXT NOT NULL, fts_rowid INTEGER NOT NULL, "
    "PRIMARY KEY (page_id, fts_rowid)) WITHOUT ROWID",
)
# Taken over with rows an older Gamma wrote: pages whose bookkeeping row it
# had deleted (its mark_page_dirty) get one, stale, so pruning finds them.
_TAKEOVER = ("INSERT OR IGNORE INTO block_fts_meta (page_id, updated_at, ver) "
             "SELECT DISTINCT page_id, '', 0 FROM block_fts_rows",)

MAX_BLOCK_CHARS = 20000   # per block
REFRESH_BUDGET_S = 0.2    # a search rebuilds stale pages itself for this long; the rest in the background
WORKER_SLICE_S = 2.0      # the background refresher's turn per workspace before it serves the next one
QUIET_S = 2.0             # an edited page is re-indexed once it has been left alone this long
SNIPPET_TOKENS = 14


def ensure_schema(conn: sqlite3.Connection) -> None:
    pdf_index.ensure_tracked(conn, SCHEMA, "block_fts", "page_id", _TAKEOVER)


def fts_query(q: str) -> str:
    """User text → safe FTS5 MATCH: AND of quoted terms, prefix on the last.
    Normalized first so "3,000" and "3000" build the same query the indexes
    store. Shared by the PDF and the block index (same tokenizer, same rules)."""
    terms = [t for t in normalize_text(q).split(" ") if t]
    if not terms:
        return ""
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    quoted[-1] += "*"
    return " ".join(quoted)


def mark_page_dirty(ws: str, page_id: str | None) -> None:
    """Stamp a page stale so it is rebuilt. Called by the block writers whose
    change doesn't move the page root's updated_at. The row stays (the
    page's rows are still there to prune). Never raises — the index is
    derived data."""
    if not page_id:
        return
    try:
        with connect_data_db(ws) as conn:
            ensure_schema(conn)
            conn.execute("UPDATE block_fts_meta SET ver = 0 WHERE page_id = ?", (page_id,))
            conn.commit()
    except sqlite3.Error:
        pass


def mark_all_dirty(ws: str) -> None:
    """Stamp every page stale (the Settings "rebuild index" path): rows keep
    their text until rebuilt, so search stays usable meanwhile."""
    try:
        with connect_data_db(ws) as conn:
            ensure_schema(conn)
            conn.execute("UPDATE block_fts_meta SET ver = 0")
            conn.commit()
    except sqlite3.Error:
        pass


def _root_pages(pages_conn: sqlite3.Connection, page_ids=None) -> dict:
    """{page_id: updated_at} for the root pages (all, or just page_ids)."""
    if page_ids is not None:
        ids = [p for p in page_ids if p]
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = pages_conn.execute(
            f"SELECT id, updated_at FROM unified_blocks WHERE parent_id = 'root' AND id IN ({placeholders})",
            ids).fetchall()
    else:
        rows = pages_conn.execute(
            "SELECT id, updated_at FROM unified_blocks WHERE parent_id = 'root'").fetchall()
    return {r[0]: r[1] or "" for r in rows}


def index_page(pages_conn: sqlite3.Connection, data_conn: sqlite3.Connection,
               page_id: str, updated_at: str) -> int:
    """Rebuild one page's rows in one data.db transaction (committed here),
    unless someone else rebuilt it at ``updated_at`` meanwhile (a search and
    the background refresher can reach the same page). ``updated_at`` is read
    before the subtree, so a change landing in between only means one more
    rebuild later. Returns how many blocks were indexed."""
    rows = []
    for row in fetch_subtree(pages_conn, page_id):
        if row[0] == page_id:
            continue
        text = normalize_text(row[3] or "")
        if text:
            rows.append((row[0], text[:MAX_BLOCK_CHARS]))
    with pdf_index.write_txn(data_conn):
        if data_conn.execute("SELECT 1 FROM block_fts_meta WHERE page_id = ? AND updated_at = ? AND ver = ?",
                             (page_id, updated_at, INDEX_VERSION)).fetchone():
            return 0
        pdf_index.delete_tracked(data_conn, "block_fts", "page_id", page_id)
        pdf_index.insert_tracked(data_conn, "block_fts", "page_id", page_id, "block_id", rows)
        data_conn.execute(
            "INSERT OR REPLACE INTO block_fts_meta (page_id, updated_at, ver) VALUES (?, ?, ?)",
            (page_id, updated_at, INDEX_VERSION))
    return len(rows)


def refresh(ws: str, pages_conn: sqlite3.Connection, page_ids=None, budget: float | None = None) -> int:
    """Bring the index up to date for the given pages (None = every page):
    rebuild the stale ones, a transaction each, for up to ``budget`` seconds
    (REFRESH_BUDGET_S when not given — a search's share), and hand what is
    left to the background refresher. Returns how many are still stale (the
    "indexing" count a search reports)."""
    live = _root_pages(pages_conn, page_ids)
    deadline = time.monotonic() + (REFRESH_BUDGET_S if budget is None else budget)
    with connect_data_db(ws) as data_conn:
        ensure_schema(data_conn)
        current = {r[0]: r[1] for r in data_conn.execute(
            "SELECT page_id, updated_at FROM block_fts_meta WHERE ver = ?", (INDEX_VERSION,))}
        stale = [p for p, at in live.items() if current.get(p) != at]
        done = 0
        for page_id in stale:
            if time.monotonic() >= deadline:
                break
            index_page(pages_conn, data_conn, page_id, live[page_id])
            done += 1
    left = len(stale) - done
    if left:
        schedule(ws)
    return left


def prune(data_conn: sqlite3.Connection, live_page_ids) -> int:
    """Drop rows of pages that no longer exist — the pages block_fts_meta
    lists (every page with rows has its row there). Returns pages pruned.
    The caller commits."""
    ensure_schema(data_conn)
    live = set(live_page_ids)
    gone = [r[0] for r in data_conn.execute("SELECT page_id FROM block_fts_meta") if r[0] not in live]
    for page_id in gone:
        pdf_index.delete_tracked(data_conn, "block_fts", "page_id", page_id)
        data_conn.execute("DELETE FROM block_fts_meta WHERE page_id = ?", (page_id,))
    return len(gone)


# --- the background refresher ---------------------------------------------------

_due: dict[str, dict] = {}       # workspace -> {page id, or None for every page: when it is due}
_wake = threading.Condition()
_worker: threading.Thread | None = None


def schedule(ws: str, page_id: str | None = None, delay: float = 0.0) -> None:
    """Have the background refresher bring ``page_id`` (None: every page of
    the workspace) up to date ``delay`` seconds from now. Asking again for a
    page before then moves its time, so an edit burst is indexed once,
    after it."""
    global _worker
    with _wake:
        _due.setdefault(ws, {})[page_id] = time.monotonic() + delay
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="notes-index", daemon=True)
            _worker.start()
        _wake.notify()


def page_changed(ws: str, client: str = "", page_id: str = "") -> None:
    """``ops.commit_listeners``: re-index a written page once it has been
    quiet for QUIET_S."""
    if page_id:
        schedule(ws, page_id, QUIET_S)


def _take_due() -> tuple[str, list | None]:
    """Wait until some workspace has pages due; take them off the queue.
    Returns ``(workspace, page ids)``, None for every page."""
    with _wake:
        while True:
            now = time.monotonic()
            for ws, pages in _due.items():
                ready = [p for p, at in pages.items() if at <= now]
                if ready:
                    for p in ready:
                        del pages[p]
                    # to the back of the queue, even with pages due later
                    # (an edited page waiting for QUIET_S): workspaces take
                    # turns, so one with a long backlog never starves the rest
                    del _due[ws]
                    if pages:
                        _due[ws] = pages
                    return ws, (None if None in ready else ready)
            soonest = min((at for pages in _due.values() for at in pages.values()), default=None)
            _wake.wait(None if soonest is None else soonest - now)


def _run() -> None:
    while True:
        ws, page_ids = _take_due()
        try:
            if not ws_dir(ws).is_dir():
                continue  # the workspace was deleted meanwhile
            with connect_pages_db(ws) as conn:
                refresh(ws, conn, page_ids, budget=WORKER_SLICE_S)
        except Exception as e:  # noqa: BLE001 — derived data; the next search retries
            log.warning(f"[block_index] background refresh of workspace {ws} failed: {e}")


def search_blocks(data_conn: sqlite3.Connection, match: str, limit: int,
                  page_ids) -> list[tuple[str, str, str]]:
    """bm25-ranked ``(block_id, page_id, snippet)`` hits for an FTS MATCH,
    restricted to ``page_ids`` — the pages the search reaches (a scope may be
    a folder; rows of deleted pages linger until pruned). A malformed MATCH
    is no results, not an error."""
    if not match or limit <= 0:
        return []
    ensure_schema(data_conn)
    found = []
    allowed = set(page_ids)
    try:
        cur = data_conn.execute(
            f"SELECT block_id, page_id, snippet(block_fts, 2, '', '', '…', {SNIPPET_TOKENS}) "
            "FROM block_fts WHERE block_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit * 3))
        for block_id, page_id, snippet in cur:
            if page_id not in allowed:
                continue
            found.append((block_id, page_id, snippet))
            if len(found) >= limit:
                break
    except sqlite3.OperationalError:
        pass
    return found


def purge_page_data(ws: str, pages_conn: sqlite3.Connection, deleted_ids, *, library: bool = True) -> None:
    """Sweep data.db after blocks were deleted or a PDF detached: chats of the
    deleted blocks, and — with ``library`` — ``pdf_fts`` rows of papers no
    page carries any more and the notes-index rows of pages that are gone;
    none of it cleans itself. ``library`` costs scans of the whole library,
    so an op batch passes it only when a deleted block carried a PDF (ops
    never delete pages). Never raises (derived data)."""
    try:
        with connect_data_db(ws) as ddb:
            ddb.executemany("DELETE FROM chats WHERE block_id = ?", [(i,) for i in deleted_ids])
            ddb.executemany("DELETE FROM chat_history WHERE bucket = ?", [(i,) for i in deleted_ids])
            if library:
                pdf_index.ensure_schema(ddb)
                pdf_meta.ensure_schema(ddb)
                indexed = [r[0] for r in ddb.execute("SELECT doc_id FROM pdf_fts_docs")]
                manifests = [r[0] for r in ddb.execute("SELECT doc_id FROM pdf_docs")]
                live_docs = _live_docs(pages_conn, indexed + manifests)
                pdf_index.drop_docs(ddb, [d for d in indexed if d not in live_docs])
                pdf_meta.purge(ddb, live_docs)
                prune(ddb, [r[0] for r in pages_conn.execute(
                    "SELECT id FROM unified_blocks WHERE parent_id = 'root'").fetchall()])
            ddb.commit()
    except Exception as e:
        log.warning(f"[block_index] derived-data cleanup failed: {e}")


def _live_docs(pages_conn: sqlite3.Connection, known) -> set:
    """The doc ids blocks still carry — complete for every id in ``known``.
    Pages carry their PDFs, and the root pages are one index seek away; only
    a doc none of them carries costs a pass over every block's properties."""
    live = {r[0] for r in pages_conn.execute(
        "SELECT json_extract(properties, '$.doc_id') FROM unified_blocks "
        "WHERE parent_id = 'root' AND json_extract(properties, '$.doc_id') IS NOT NULL")}
    if set(known) - live:
        live |= {r[0] for r in pages_conn.execute(
            "SELECT json_extract(properties, '$.doc_id') FROM unified_blocks "
            "WHERE instr(properties, '\"doc_id\"') > 0 AND json_extract(properties, '$.doc_id') IS NOT NULL")}
    return live
