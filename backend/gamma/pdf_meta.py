"""The per-document manifest: what the viewer needs to lay a PDF out before
pdf.js has parsed a byte — its size in bytes, page count, and every page's
size in points — derived once per stored file into the workspace's
``data.db`` (``pdf_docs``, a table of ``db.DATA_SCHEMA``), next to the
other derived per-document data (the document text index). Read by
``GET /api/pdf-info/{doc_id}``; the viewer lays out its page boxes from it
while the bytes are still arriving, and picks the transport (one download
or range requests) from the byte size (docs/dev/pdf_loading.md).

Computed on a background thread when a PDF is stored (``schedule``), by the
search indexer when it walks a document anyway, and on demand by the
endpoint for anything that predates the table. A file PyMuPDF cannot read
is stored with ``pages = 0`` so it is not parsed again on every open; a
PyMuPDF that does not load stores nothing (``page_sizes`` raises its
ImportError), so the file is measured once it loads. Rows of documents
no page carries any more are purged with the text index
(``search.index.purge_unused``, and ``purge_doc`` for a replaced file)."""

import json
import threading

from . import pdf_text
from .db import connect_data_db, page_now, safe_doc_id
from .documents.model import stored_name
from .logbuf import log

# Bump when the stored shape changes; older rows are recomputed lazily.
VERSION = 1
# Walks in progress, so a second caller for the same document joins the
# first instead of queueing behind pdf_text's PyMuPDF lock; WAIT_S is the
# longest such a caller waits.
WAIT_S = 60
_inflight: dict[tuple[str, str], threading.Event] = {}
_inflight_lock = threading.Lock()


def _shape(doc_id: str, size: int, dims: list) -> dict:
    return {"doc_id": doc_id, "bytes": size, "pages": len(dims), "dims": dims}


def get(ws: str, doc_id: str) -> dict | None:
    """The stored manifest at the current VERSION, or None."""
    with connect_data_db(ws) as conn:
        row = conn.execute(
            "SELECT bytes, dims FROM pdf_docs WHERE doc_id = ? AND ver = ?", (doc_id, VERSION)
        ).fetchone()
    return _shape(doc_id, row[0], json.loads(row[1])) if row else None


def ensure(ws: str, doc_id: str) -> dict | None:
    """The manifest, computed and stored when missing. None when the
    workspace has no such file. Walking a long file takes about a second, so
    request handlers calling this are sync ``def`` (threadpool). A walk
    already running for the same document (the upload's background thread,
    another open) is joined, not repeated: the second caller waits for its
    result instead of queueing a second pass behind the PyMuPDF lock."""
    found = get(ws, doc_id)
    if found:
        return found
    key = (ws, doc_id)
    with _inflight_lock:
        done = _inflight.get(key)
        owner = done is None
        if owner:
            done = _inflight[key] = threading.Event()
    if not owner:
        done.wait(WAIT_S)
        return get(ws, doc_id)
    try:
        return _compute(ws, doc_id)
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        done.set()


def _compute(ws: str, doc_id: str) -> dict | None:
    from .storage import find_upload_file, is_linked  # local: storage imports this module

    try:
        name = stored_name(safe_doc_id(doc_id), ".pdf")
    except ValueError:
        return None
    path = find_upload_file(name, ws)
    if path is None:
        return None
    size = path.stat().st_size
    dims = [[round(w, 2), round(h, 2)] for w, h in pdf_text.page_sizes(str(path))]
    if not dims and is_linked(ws, name):
        return None  # a person's file another program holds for now: no ``pages = 0`` to keep
    with connect_data_db(ws) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pdf_docs (doc_id, bytes, pages, dims, ver, at) VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, size, len(dims), json.dumps(dims, separators=(",", ":")), VERSION, page_now()),
        )
        conn.commit()
    return _shape(doc_id, size, dims)


def schedule(ws: str, doc_id: str) -> None:
    """Compute the manifest on a background thread — what every writer of a
    PDF file calls, so the walk never sits in the request that stored it and
    the first open finds the manifest ready. A no-op while one is running."""
    with _inflight_lock:
        if (ws, doc_id) in _inflight:
            return

    def run():
        try:
            ensure(ws, doc_id)
        except Exception as e:
            log.warning(f"[pdf-meta] {doc_id}: {e}")

    threading.Thread(target=run, daemon=True).start()


def purge(conn, live_docs) -> int:
    """Delete the manifests of documents not in ``live_docs``. Returns how
    many went."""
    stale = [r[0] for r in conn.execute("SELECT doc_id FROM pdf_docs").fetchall() if r[0] not in live_docs]
    conn.executemany("DELETE FROM pdf_docs WHERE doc_id = ?", [(d,) for d in stale])
    return len(stale)


def drop(conn, doc_ids) -> None:
    """Delete the manifests of these documents. The caller commits."""
    conn.executemany("DELETE FROM pdf_docs WHERE doc_id = ?", [(d,) for d in doc_ids])
