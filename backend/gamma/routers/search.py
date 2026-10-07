"""Library-wide full-text search: notes (block_fts) and PDF contents (pdf_fts).

Both indexes are SQLite FTS5 tables. The notes one is in the workspace's
pages.db, kept current by triggers in every write's own transaction
(gamma.block_index), so a note is found the moment its write commits. The
PDF one is in data.db and built here: each paper's text is extracted once,
so searching ~1000 papers is a millisecond-range query instead of opening a
thousand PDFs; missing papers are indexed lazily by a background job the
first time a search runs, and the response reports how many are still
pending so the UI can hint that results are incomplete.

Extraction prefers pypdfium2 (PDFium — proper word spacing and unicode) and
falls back to PyPDF2. Text is stored in normalized form (see gamma.textnorm)
so queries like "3000" hit "3,000-qubit"; queries are normalized the same way
at search time. Bumping textnorm.INDEX_VERSION re-extracts the PDFs lazily.

Positions are deliberately NOT stored here: the frontend re-finds the matched
text with pdf.js (the engine that renders the page) when a hit is opened, so
highlight rects always agree with what's on screen.

``GET /api/search`` is the one endpoint over both indexes (hits carry
``source: "notes" | "pdf"``); ``/pdf-search`` is the PDF-only predecessor the
frontend still uses.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import block_index, jobs, pdf_index, pdf_meta
from ..ai_context import pdf_path as _pdf_path
from ..auth import require_ws
from ..block_index import fts_query
from ..blocks_store import root_pages, write_lock
from ..db import connect_data_db, connect_pages_db
from ..json_response import OrjsonResponse
from ..logbuf import log
from ..pdf_text import extract_pages
from ..textnorm import normalize_text

router = APIRouter(prefix="/api", tags=["search"])

_MAX_PAGE_CHARS = 20000   # per page


def _extract_pages(path) -> list[str]:
    """Text per page (1-based order) — the shared extractor in gamma.pdf_text.
    Past its page-text cache: a walk over a library's papers would push out
    the ones the chat is reading."""
    return extract_pages(str(path), cache=False)


def _index_doc(ws: str, doc_id: str):
    """Extract a PDF's text into the FTS index. Failures are recorded (pages=0)
    so a broken file isn't re-parsed on every search."""
    rows = []
    try:
        path = _pdf_path(ws, doc_id)
        if path:
            pdf_meta.ensure(ws, doc_id)  # the viewer's manifest, while the file is at hand anyway
            for i, raw in enumerate(_extract_pages(path), start=1):
                text = normalize_text(raw)
                if text:
                    rows.append((i, text[:_MAX_PAGE_CHARS]))
    except Exception as e:
        log.warning(f"[pdf-search] indexing {doc_id} failed: {e}")
    with connect_data_db(ws) as conn:
        pdf_index.store_doc(conn, doc_id, rows)


def _index_missing_async(ws: str, doc_ids: list[str]) -> bool:
    """Index the papers in the background as the workspace's ``indexing``
    job (gamma/jobs.py): one per workspace at a time, its progress in every
    member's Background tasks, stoppable there (the papers it did not reach
    stay stamped stale, so the next search finishes the work). Returns False
    when one already runs — the request is dropped, not queued: the next
    search computes again what is missing."""
    doc_ids = list(doc_ids)

    def run(job):
        for n, doc_id in enumerate(doc_ids):
            job.progress(done=n, total=len(doc_ids), unit="papers")
            _index_doc(ws, doc_id)
        job.progress(done=len(doc_ids), total=len(doc_ids), unit="papers")
        return {"papers": len(doc_ids)}

    try:
        job = jobs.start("indexing", owner=jobs.WORKSPACE, ws=ws, key="indexing", run=run,
                         title="Indexing PDFs for search", params={"papers": len(doc_ids)})
    except jobs.Busy:
        return False
    jobs.prune(jobs.WORKSPACE, "indexing", ws, keep=job["id"])  # the workspace keeps its latest run
    return True


class ReindexRequest(BaseModel):
    doc_ids: list[str] = []  # empty = rebuild the whole library


@router.post("/search-reindex")
def search_reindex(request: Request, payload: ReindexRequest | None = None):
    """Settings: re-extract papers into the FTS index. With doc_ids, just those
    papers (the Library pane's per-paper button — no global stale stamp);
    without, the whole library, and the notes index is rebuilt from the
    blocks too, here, in one write transaction. Either way the extraction
    is the workspace's indexing job (Background tasks, GET /api/jobs)."""
    ws = require_ws(request, write=True)
    wanted = [d for d in (payload.doc_ids if payload else []) if d]
    with connect_pages_db(ws) as conn:
        library = [info["doc_id"] for info in root_pages(conn).values() if info["doc_id"]]
        if not wanted:
            write_lock(conn)
            block_index.rebuild(conn)
            conn.commit()
    if wanted:
        doc_ids = [d for d in library if d in set(wanted)]  # only own papers
    else:
        doc_ids = library
        # Stamp everything stale first: if the run is interrupted, the next
        # search still sees the remainder as missing and finishes the job.
        with connect_data_db(ws) as conn:
            pdf_index.ensure_schema(conn)
            conn.execute("UPDATE pdf_fts_docs SET ver = 0")
            conn.commit()
    started = doc_ids and _index_missing_async(ws, doc_ids)
    return {"scheduled": len(doc_ids) if started else 0,
            "busy": bool(doc_ids) and not started}


@router.get("/search")
def library_search(request: Request, q: str = "", limit: int = 20, scope: str = ""):
    """One search over the workspace's knowledge base: notes (block_fts) and the
    text of PDF attachments (pdf_fts). Any member, like /pdf-search.
    ``scope`` (a folder's id) keeps to the pages filed in that folder or
    below it. Results are notes first (bm25 order), then PDF hits, each
    capped at ``limit``;
    ``indexing`` counts the PDFs the background extractor hasn't reached
    (the notes index is never behind)."""
    ws = require_ws(request)
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    if not q:
        return {"results": [], "indexing": 0}
    match = fts_query(q)
    with connect_pages_db(ws) as conn:
        pages = root_pages(conn, scope)
        results = [{"source": "notes", "block_id": block_id, "page_id": page_id,
                    "title": pages[page_id]["title"], "snippet": snippet}
                   for block_id, page_id, snippet in block_index.search_blocks(conn, match, limit, pages)]
    docs = {info["doc_id"]: page_id for page_id, info in pages.items() if info["doc_id"]}
    missing: list = []
    with connect_data_db(ws) as conn:
        if docs:
            missing = pdf_index.pdf_missing(conn, docs)
            if missing:
                _index_missing_async(ws, missing)
            for doc_id, page, snippet in pdf_index.search_pdf(conn, match, limit, docs):
                page_id = docs[doc_id]
                results.append({"source": "pdf", "block_id": page_id, "page_id": page_id,
                                "doc_id": doc_id, "title": pages[page_id]["title"],
                                "page": page, "snippet": snippet})
    return OrjsonResponse({"results": results, "indexing": len(missing)})


@router.get("/pdf-search")
def pdf_search(request: Request, q: str = "", limit: int = 20):
    ws = require_ws(request)
    q = (q or "").strip()
    if not q:
        return {"results": [], "indexing": 0}

    # Library papers: doc_id → page block (title + id to open)
    with connect_pages_db(ws) as conn:
        docs = {info["doc_id"]: {"block_id": page_id, "title": info["title"]}
                for page_id, info in root_pages(conn).items() if info["doc_id"]}
    if not docs:
        return {"results": [], "indexing": 0}

    with connect_data_db(ws) as conn:
        missing = pdf_index.pdf_missing(conn, docs)  # never indexed or stale version
        if missing:
            _index_missing_async(ws, missing)
        results = [{"block_id": docs[doc_id]["block_id"], "doc_id": doc_id,
                    "title": docs[doc_id]["title"], "page": page, "snippet": snip}
                   for doc_id, page, snip in pdf_index.search_pdf(conn, fts_query(q), limit, docs)]
    return OrjsonResponse({"results": results, "indexing": len(missing)})
