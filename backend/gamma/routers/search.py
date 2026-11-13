"""Library-wide full-text search: notes (block_fts) and the text of the
documents (text_fts: PDF pages and text files' runs of lines).

Both indexes are SQLite FTS5 tables. The notes one is in the workspace's
pages.db, kept current by triggers in every write's own transaction
(gamma.block_index), so a note is found the moment its write commits. The
document one is in data.db (gamma/search/): each document's text is
extracted once, so searching a library is a millisecond-range query instead
of opening every file; missing documents are indexed lazily by a background
job the first time a search runs (gamma/search/indexer.py), and the answer
reports how many are still pending so the UI can hint that results are
incomplete. Text is stored in normalized form (gamma.textnorm) and queries
are normalized the same way; bumping textnorm.INDEX_VERSION re-extracts
lazily.

``GET /api/search`` is the one-call API over both indexes (hits carry
``source: "notes" | "pdf" | "file"``); the Ctrl+F panel sends its two
groups as POSTs, ``/block-search`` (routers/blocks.py) and
``/document-search`` here, with the folders and labels of its chips
resolved here (``scope_pages``) and the page ids its predicates keep. The
hits are built in one place, gamma/search/hits.py."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import block_index
from ..auth import require_ws
from ..block_index import fts_query
from ..blocks_store import MAX_SEARCH_PAGES, pages_in_scope, root_pages, write_lock
from ..db import connect_data_db, connect_pages_db
from ..documents.text import INDEXED_FORMATS
from ..json_response import OrjsonResponse
from ..search import hits, indexer
from ..search import index as text_index

router = APIRouter(prefix="/api", tags=["search"])

# A document's hits listed before every other document's best ones (/document-search).
HITS_PER_DOCUMENT = 3


def scope_pages(conn, payload) -> set | None:
    """The pages a POST search keeps to: the pages inside every folder and
    label of ``folders`` and ``labels`` (``pages_in_scope``, resolved here,
    so a work folder's thousands of files travel as one id), narrowed to
    ``pages`` when it is given too (the page ids the panel's predicates
    keep), ``pages`` alone without chips; None for the whole library."""
    chips = [*(payload.folders or []), *(payload.labels or [])]
    scope = pages_in_scope(conn, chips) if chips else None
    if payload.pages is None:
        return scope
    pages = set(payload.pages)
    return pages if scope is None else scope & pages


class ReindexRequest(BaseModel):
    doc_ids: list[str] = []  # empty = rebuild the whole library


@router.post("/search-reindex")
def search_reindex(request: Request, payload: ReindexRequest | None = None):
    """Settings: re-extract documents into the text index. With doc_ids,
    just those documents (the Library pane's per-paper button — no global
    stale stamp); without, the whole library, and the notes index is
    rebuilt from the blocks too, here, in one write transaction. Either way
    the extraction is the workspace's indexing job (Background tasks, GET
    /api/jobs)."""
    ws = require_ws(request, write=True)
    wanted = {d for d in (payload.doc_ids if payload else []) if d}
    with connect_pages_db(ws) as conn:
        library, _, _ = hits.docs_of(root_pages(conn))
        if not wanted:
            write_lock(conn)
            block_index.rebuild(conn)
            conn.commit()
    if wanted:
        docs = {h: doc for h, doc in library.items() if h in wanted}  # only own documents
    else:
        docs = library
        # Stamp everything stale first: if the run is interrupted, the next
        # search still sees the remainder as missing and finishes the job.
        with connect_data_db(ws) as conn:
            text_index.ensure_schema(conn)
            conn.execute("UPDATE text_fts_docs SET ver = 0")
            conn.commit()
    started = bool(docs) and indexer.index_missing_async(ws, docs)
    return {"scheduled": len(docs) if started else 0,
            "busy": bool(docs) and not started}


@router.get("/search")
def library_search(request: Request, q: str = "", limit: int = 20, scope: str = ""):
    """One search over the workspace's knowledge base: notes (block_fts) and
    the text of its documents (text_fts). Any member, like
    /document-search. ``scope`` (a folder's id) keeps to the pages filed in
    that folder or below it. Results are notes first (bm25 order), then the
    documents' hits in one rank order over PDF pages and file runs, the
    notes and the documents each capped at ``limit`` index hits (a PDF hit
    names the first page of its document, a file hit every page of it, so
    the documents may list more); ``indexing`` counts the
    documents the background extractor hasn't reached (the notes index is
    never behind)."""
    ws = require_ws(request)
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    if not q:
        return {"results": [], "indexing": 0}
    match = fts_query(q)
    indexing = 0
    with connect_pages_db(ws) as conn:
        found = root_pages(conn, scope)
        results = [{"source": "notes", "block_id": block_id, "page_id": page_id,
                    "title": found[page_id]["title"], "snippet": snippet}
                   for block_id, page_id, snippet in block_index.search_blocks(conn, match, limit, found)]
        docs, pages, titles = hits.docs_of(found)
        if docs:
            with connect_data_db(ws) as data:
                indexing = len(indexer.kick(ws, data, docs))
                results += hits.build(ws, conn, data, match, docs, pages, titles, limit, query=q)
    return OrjsonResponse({"results": results, "indexing": indexing})


class DocumentSearch(BaseModel):
    """POST /document-search: the Ctrl+F panel's document group. ``pages``
    (None for the whole library, [] for none), ``folders`` and ``labels``
    keep to a scope (``scope_pages``); ``exclude`` leaves out one page's
    document (the open PDF, whose matches pdf.js finds); ``formats`` the
    formats searched (``pdf``, ``text``; None for both)."""
    q: str = ""
    limit: int = 20
    cursor: str = ""
    exclude: str = ""
    pages: list[str] | None = None
    folders: list[str] | None = None
    labels: list[str] | None = None
    formats: list[str] | None = None


@router.post("/document-search")
def document_search(payload: DocumentSearch, request: Request):
    """The hits in the documents of the scope (``scope_pages``), at most
    HITS_PER_DOCUMENT of one document's before every other document's best
    ones (``index.search``'s ``per_doc``), each on every page of its
    document; ``next`` is the ``cursor`` of the following page (the
    index's hits listed so far), there when more follow; 400 for a cursor
    that is no offset or a format the index does not hold, 413 for more
    than MAX_SEARCH_PAGES ``pages``. ``indexing`` counts the documents of
    the scope not indexed yet. Any member: a share token is no way in. A
    read, though a POST (hosted.POST_READS), since a URL cannot carry a
    library's ids."""
    ws = require_ws(request)
    if payload.pages is not None and len(payload.pages) > MAX_SEARCH_PAGES:
        raise HTTPException(status_code=413, detail=f"a search names at most {MAX_SEARCH_PAGES} pages")
    if payload.cursor and not payload.cursor.isdigit():
        raise HTTPException(status_code=400, detail="bad cursor")
    formats = INDEXED_FORMATS if payload.formats is None else tuple(payload.formats)
    bad = sorted(set(formats) - set(INDEXED_FORMATS))
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown format: {', '.join(bad)}")
    q = (payload.q or "").strip()
    limit = max(1, min(int(payload.limit or 20), 100))
    offset = int(payload.cursor or 0)
    if not q:
        return {"results": [], "indexing": 0}
    with connect_pages_db(ws) as conn:
        docs, pages, titles = hits.docs_of(root_pages(conn), keep=scope_pages(conn, payload), formats=formats)
        if payload.exclude:
            for hash in list(pages):
                pages[hash] = [p for p in pages[hash] if p != payload.exclude]
                if not pages[hash]:
                    del pages[hash], docs[hash]
        if not docs:
            return {"results": [], "indexing": 0}
        with connect_data_db(ws) as data:
            indexing = len(indexer.kick(ws, data, docs))
            found = hits.build(ws, conn, data, fts_query(q), docs, pages, titles, limit, offset=offset,
                               per_doc=HITS_PER_DOCUMENT, query=q, fan_out=True)
    more = {"next": str(offset + limit)} if found.more else {}
    return OrjsonResponse({"results": list(found), "indexing": indexing, **more})
