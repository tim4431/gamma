"""The indexer: a document's text extracted (gamma/documents/text.py) into
the index (gamma/search/index.py), and the workspace's ``indexing`` job
that does it for the documents a search, a chat or Rebuild finds missing.
Nothing indexes eagerly: a document is read the first time something asks
for its text, then once per file version (the index is keyed by the hash).

Positions are deliberately not stored: the viewer finds a PDF hit's text
again with pdf.js when the hit is opened, and a file hit names its line
(gamma/search/hits.py)."""

from .. import jobs, pdf_meta, storage
from ..db import connect_data_db, connect_pages_db
from ..documents import text
from ..logbuf import log
from .index import Doc, pending, store_docs

BATCH_UNITS = 500   # units written per transaction by the job
BATCH_DOCS = 50     # documents written per transaction by the job, at most


def reads_through(ws: str, name: str) -> bool:
    """Whether the file ``name`` can be read from end to end now, as the bytes
    the name hashes: another program may hold a linked file, or it may have
    moved on."""
    path = storage.find_upload_file(name, ws)
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            while f.read(storage.SPOOL_CHUNK):
                pass
    except OSError:
        return False
    return storage.find_upload_file(name, ws) == path


def _name_of(ws: str, doc: Doc) -> str:
    """The name a text document goes by on the pages that own it — a
    secrets file's when one of them is (``text.is_secret``): its text is
    never indexed. One indexed lookup (idx_ub_doc)."""
    with connect_pages_db(ws) as conn:
        names = [r[0] or "" for r in conn.execute(
            "SELECT json_extract(properties, '$.doc.name') FROM unified_blocks WHERE doc_id = ?", (doc.hash,))]
    return next((n for n in names if text.is_secret(n)), names[0] if names else "")


def _path_of(ws: str, doc: Doc):
    """Where the document's bytes are read: its stored or in-place file, or
    for a PDF opened from an address the chat's resolver, which downloads
    it when it is not stored (guarded, within the storage limits) — a
    search finds what the page shows."""
    if doc.at == "url" and doc.format == "pdf":
        from ..ai_context import pdf_path  # local: ai_context imports this module

        return pdf_path(ws, doc.hash)
    return storage.find_upload_file(doc.name, ws)


def index_doc(ws: str, doc: Doc) -> tuple[str, list, dict] | None:
    """A document's text, extracted and ready to store: ``(hash, [(unit,
    text)], {format, lines, truncated})``, or None for "record nothing".
    A file that fails to read is recorded with no text, so a broken file
    isn't re-parsed on every search, but for a linked file out of reach
    (``reads_through``): nothing is said of its bytes then, so nothing is
    recorded and a later search tries again. A linked file that reads and
    does not parse is recorded like a stored one: its name is its bytes',
    which stay broken. A text file that is no text (``binary``) or a
    secrets file records no text. A PyMuPDF that does not load
    (ImportError) records nothing: it is raised, ending the indexing job
    with it, and the documents stay missing, for a later search to index
    once the reader loads."""
    units, meta, read = [], {"format": doc.format, "lines": 0, "truncated": False}, False
    try:
        path = _path_of(ws, doc)
        if path:
            if doc.format == "pdf":
                pdf_meta.ensure(ws, doc.hash)  # the viewer's manifest, while the file is at hand anyway
            found = text.extract(path, doc.format, name=_name_of(ws, doc) if doc.format == "text" else "")
            units = [(u.no, u.text) for u in found.units]
            meta = {"format": doc.format, "lines": found.lines, "truncated": found.truncated}
            read = True
    except ImportError:
        raise
    except Exception as e:
        log.warning(f"[search] indexing {doc.hash} failed: {e}")
    if not read and storage.is_linked(ws, doc.name) and not reads_through(ws, doc.name):
        return None
    return doc.hash, units, meta


def kick(ws: str, data_conn, docs: dict) -> list[str]:
    """Start the ``indexing`` job on what of ``docs`` ({hash: Doc}) is
    pending (``index.pending``), as a search, search_library and a chat's
    document do; the hashes pending."""
    missing = pending(ws, data_conn, docs)
    if missing:
        index_missing_async(ws, {h: docs[h] for h in missing})
    return missing


def index_missing_async(ws: str, docs: dict) -> bool:
    """Index the documents ``docs`` ({hash: Doc}) in the background as the
    workspace's ``indexing`` job (gamma/jobs.py): one per workspace at a
    time, its progress in every member's Background tasks, stoppable there
    between documents (the ones it did not reach stay missing, so the next
    search finishes the work). The extracted documents are written in
    batches (``store_docs``: BATCH_UNITS units or BATCH_DOCS documents per
    transaction), and a stopped run writes its batch first. Returns False
    when one already runs — the request is dropped, not queued: the next
    search computes again what is missing."""
    todo = list(docs.values())

    def run(job):
        batch, size = [], 0

        def flush():
            nonlocal size
            if batch:
                with connect_data_db(ws) as conn:
                    store_docs(conn, batch)
                batch.clear()
            size = 0

        try:
            for n, doc in enumerate(todo):
                job.progress(done=n, total=len(todo), unit="documents")
                found = index_doc(ws, doc)
                if found is not None:
                    batch.append(found)
                    size += len(found[1])
                if size >= BATCH_UNITS or len(batch) >= BATCH_DOCS:
                    flush()
        except BaseException:
            flush()
            raise
        flush()
        job.progress(done=len(todo), total=len(todo), unit="documents")
        return {"documents": len(todo)}

    try:
        job = jobs.start("indexing", owner=jobs.WORKSPACE, ws=ws, key="indexing", run=run,
                         title="Indexing documents for search", params={"documents": len(todo)})
    except jobs.Busy:
        return False
    jobs.prune(jobs.WORKSPACE, "indexing", ws, keep=job["id"])  # the workspace keeps its latest run
    return True
