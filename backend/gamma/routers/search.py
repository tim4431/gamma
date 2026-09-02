"""Library-wide full-text search: notes (block_fts) and PDF contents (pdf_fts).

Both indexes are SQLite FTS5 tables in the per-user data.db. The PDF one is
built here: each paper's text is extracted once, so searching ~1000 papers is
a millisecond-range query instead of opening a thousand PDFs; missing papers
are indexed lazily by a background thread the first time a search runs, and
the response reports how many are still pending so the UI can hint that
results are incomplete. The notes index lives in gamma.block_index (rebuilt
per page, synchronously, when a page changed since its last build).

Extraction prefers pypdfium2 (PDFium — proper word spacing and unicode) and
falls back to PyPDF2. Text is stored in normalized form (see gamma.textnorm)
so queries like "3000" hit "3,000-qubit"; queries are normalized the same way
at search time. Bumping textnorm.INDEX_VERSION re-indexes everything lazily.

Positions are deliberately NOT stored here: the frontend re-finds the matched
text with pdf.js (the engine that renders the page) when a hit is opened, so
highlight rects always agree with what's on screen.

``GET /api/search`` is the one endpoint over both indexes (hits carry
``source: "notes" | "pdf"``); ``/pdf-search`` is the PDF-only predecessor the
frontend still uses.
"""

import json
import sqlite3
import threading

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import block_index
from ..ai_context import pdf_path as _pdf_path
from ..auth import require_user
from ..block_index import fts_query
from ..db import page_now, user_db_path
from ..foldertags import clean_path, parse_tags
from ..logbuf import log
from ..pdf_text import extract_pages
from ..textnorm import INDEX_VERSION, normalize_text

router = APIRouter(prefix="/api", tags=["search"])

_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5(doc_id UNINDEXED, page UNINDEXED, content)",
    "CREATE TABLE IF NOT EXISTS pdf_fts_docs (doc_id TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, pages INTEGER, ver INTEGER NOT NULL DEFAULT 0)",
)

_MAX_PAGE_CHARS = 20000   # per page

_index_threads: dict[str, threading.Thread] = {}
_index_progress: dict[str, dict] = {}  # user -> {"total": n, "done": m}
_index_lock = threading.Lock()


def _ensure_schema(conn):
    for stmt in _FTS_SCHEMA:
        conn.execute(stmt)
    try:  # older DBs predate the ver column
        conn.execute("ALTER TABLE pdf_fts_docs ADD COLUMN ver INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass


def _extract_pages(path) -> list[str]:
    """Text per page (1-based order) — the shared extractor in gamma.pdf_text."""
    return extract_pages(str(path))


def _index_doc(user: str, doc_id: str):
    """Extract a PDF's text into the FTS index. Failures are recorded (pages=0)
    so a broken file isn't re-parsed on every search."""
    rows = []
    try:
        path = _pdf_path(user, doc_id)
        if path:
            for i, raw in enumerate(_extract_pages(path), start=1):
                text = normalize_text(raw)
                if text:
                    rows.append((doc_id, i, text[:_MAX_PAGE_CHARS]))
    except Exception as e:
        log.warning(f"[pdf-search] indexing {doc_id} failed: {e}")
    with sqlite3.connect(user_db_path(user, "data.db")) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM pdf_fts WHERE doc_id = ?", (doc_id,))
        conn.executemany("INSERT INTO pdf_fts (doc_id, page, content) VALUES (?, ?, ?)", rows)
        conn.execute(
            "INSERT OR REPLACE INTO pdf_fts_docs (doc_id, indexed_at, pages, ver) VALUES (?, ?, ?, ?)",
            (doc_id, page_now(), len(rows), INDEX_VERSION),
        )
        conn.commit()


def _index_missing_async(user: str, doc_ids: list[str]) -> bool:
    """One background indexer per user at a time, with visible progress.
    Returns False if one is already running (the request is dropped, not
    queued — the next search re-computes what's missing anyway)."""
    with _index_lock:
        t = _index_threads.get(user)
        if t and t.is_alive():
            return False

        def run():
            prog = _index_progress[user] = {"total": len(doc_ids), "done": 0}
            for d in doc_ids:
                _index_doc(user, d)
                prog["done"] += 1

        t = threading.Thread(target=run, daemon=True)
        _index_threads[user] = t
        t.start()
        return True


def _library_doc_ids(user: str) -> list[str]:
    """doc_ids of every paper page in the user's library."""
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        return [r[0] for r in conn.execute(
            "SELECT json_extract(properties, '$.doc_id') FROM unified_blocks "
            "WHERE parent_id = 'root' AND json_extract(properties, '$.doc_id') IS NOT NULL"
        ).fetchall() if r[0]]


class ReindexRequest(BaseModel):
    doc_ids: list[str] = []  # empty = rebuild the whole library


@router.post("/search-reindex")
def search_reindex(request: Request, payload: ReindexRequest | None = None):
    """Settings: re-extract papers into the FTS index. With doc_ids, just those
    papers (the Library pane's per-paper button — no global stale stamp);
    without, the whole library. Progress is visible via /api/tasks either way."""
    user = require_user(request)
    library = _library_doc_ids(user)
    wanted = [d for d in (payload.doc_ids if payload else []) if d]
    if wanted:
        doc_ids = [d for d in library if d in set(wanted)]  # only own papers
    else:
        doc_ids = library
        # Stamp everything stale first: if the run is interrupted, the next
        # search still sees the remainder as missing and finishes the job.
        with sqlite3.connect(user_db_path(user, "data.db")) as conn:
            _ensure_schema(conn)
            conn.execute("UPDATE pdf_fts_docs SET ver = 0")
            conn.commit()
    started = doc_ids and _index_missing_async(user, doc_ids)
    if not wanted:
        block_index.mark_all_dirty(user)  # notes rebuild on the next search
    return {"scheduled": len(doc_ids) if started else 0,
            "busy": bool(doc_ids) and not started}


@router.get("/tasks")
def background_tasks(request: Request):
    """Server-side background work for the tasks popover (extensible)."""
    user = require_user(request)
    with _index_lock:
        t = _index_threads.get(user)
        prog = _index_progress.get(user) or {"total": 0, "done": 0}
        return {"indexing": {**prog, "active": bool(t and t.is_alive())}}


_fts_query = fts_query  # shared with the block index (gamma.block_index)


def _library_pages(conn, scope: str = "") -> dict:
    """{page_id: {"title", "doc_id"}} for the root pages a search reaches:
    the whole library, or — with ``scope`` a folder path — the pages filed in
    that folder or below it (properties.folder, gamma.foldertags rules)."""
    path = clean_path(scope or "")
    pages = {}
    for page_id, content, props_raw in conn.execute(
            "SELECT id, content, properties FROM unified_blocks WHERE parent_id = 'root'"):
        try:
            props = json.loads(props_raw or "{}")
        except ValueError:
            props = {}
        if path and not any(t == path or t.startswith(path + "/")
                            for t in parse_tags(props.get("folder"))):
            continue
        pages[page_id] = {"title": content or "Untitled",
                          "doc_id": str(props.get("doc_id") or "")}
    return pages


@router.get("/search")
def library_search(request: Request, q: str = "", limit: int = 20, scope: str = ""):
    """One search over the user's knowledge base: notes (block_fts) and the
    text of PDF attachments (pdf_fts). Owner-only, like /pdf-search. Results
    are notes first (bm25 order), then PDF hits, each capped at ``limit``;
    ``indexing`` counts what is still being built (note pages waiting for a
    rebuild batch + PDFs the background extractor hasn't reached)."""
    user = require_user(request)
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    if not q:
        return {"results": [], "indexing": 0}
    match = fts_query(q)
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        pages = _library_pages(conn, scope)
        # Notes: rebuild what changed (synchronous, batch-capped), then query.
        pending = block_index.refresh(user, conn, list(pages)) if pages else 0
    docs = {info["doc_id"]: page_id for page_id, info in pages.items() if info["doc_id"]}
    results = []
    missing: list = []
    with sqlite3.connect(user_db_path(user, "data.db")) as conn:
        _ensure_schema(conn)
        for block_id, page_id, snippet in block_index.search_blocks(conn, match, limit, set(pages)):
            results.append({"source": "notes", "block_id": block_id, "page_id": page_id,
                            "title": pages[page_id]["title"], "snippet": snippet})
        if docs:
            current = {r[0] for r in conn.execute(
                "SELECT doc_id FROM pdf_fts_docs WHERE ver = ?", (INDEX_VERSION,)).fetchall()}
            missing = [d for d in docs if d not in current]
            if missing:
                _index_missing_async(user, missing)
            found = 0
            if match:
                try:
                    cur = conn.execute(
                        "SELECT doc_id, page, snippet(pdf_fts, 2, '', '', '…', 14) FROM pdf_fts "
                        "WHERE pdf_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit * 3))
                    for doc_id, page, snippet in cur:
                        page_id = docs.get(doc_id)  # skips out-of-scope / deleted docs
                        if not page_id:
                            continue
                        results.append({"source": "pdf", "block_id": page_id, "page_id": page_id,
                                        "doc_id": doc_id, "title": pages[page_id]["title"],
                                        "page": page, "snippet": snippet})
                        found += 1
                        if found >= limit:
                            break
                except sqlite3.OperationalError:
                    pass  # malformed MATCH — treat as no results
    return {"results": results, "indexing": pending + len(missing)}


@router.get("/pdf-search")
def pdf_search(request: Request, q: str = "", limit: int = 20):
    user = require_user(request)
    q = (q or "").strip()
    if not q:
        return {"results": [], "indexing": 0}

    # Library papers: doc_id → page block (title + id to open)
    with sqlite3.connect(user_db_path(user, "pages.db")) as conn:
        rows = conn.execute(
            "SELECT id, content, json_extract(properties, '$.doc_id') FROM unified_blocks "
            "WHERE parent_id = 'root' AND json_extract(properties, '$.doc_id') IS NOT NULL"
        ).fetchall()
    docs = {r[2]: {"block_id": r[0], "title": r[1] or "Untitled"} for r in rows if r[2]}
    if not docs:
        return {"results": [], "indexing": 0}

    with sqlite3.connect(user_db_path(user, "data.db")) as conn:
        _ensure_schema(conn)
        current = {r[0] for r in conn.execute(
            "SELECT doc_id FROM pdf_fts_docs WHERE ver = ?", (INDEX_VERSION,)).fetchall()}
        missing = [d for d in docs if d not in current]  # never indexed or stale version
        if missing:
            _index_missing_async(user, missing)

        results = []
        match = _fts_query(q)
        if match:
            try:
                cur = conn.execute(
                    "SELECT doc_id, page, snippet(pdf_fts, 2, '', '', '…', 14) FROM pdf_fts "
                    "WHERE pdf_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match, limit * 3),
                )
                for doc_id, page, snip in cur:
                    info = docs.get(doc_id)  # skips docs deleted since indexing
                    if not info:
                        continue
                    results.append({"block_id": info["block_id"], "doc_id": doc_id,
                                    "title": info["title"], "page": page, "snippet": snip})
                    if len(results) >= limit:
                        break
            except sqlite3.OperationalError:
                pass  # malformed MATCH — treat as no results

    return {"results": results, "indexing": len(missing)}
