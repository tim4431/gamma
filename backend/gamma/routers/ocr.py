"""Text recognized on a stored PDF's scanned pages (gamma/pdf_ocr.py,
gamma/ocr_engine.py): the job that recognizes it and what the viewer reads
of it.

- ``POST /api/jobs/ocr`` ``{page_id}``: start the ``ocr`` job for the
  page's stored PDF (the Document menu's Recognize text in this PDF, a
  background task's Try again). One run at a time for a workspace's file,
  whoever starts it.
- ``GET /api/ocr/{doc_id}``: the file's recognition, for the Document menu
  and the viewer's text layer (frontend/src/pdf/ocrState.js).
- ``GET /api/ocr/{doc_id}/pages?first=&last=``: the recognized lines of
  the live pages in a range, which the viewer builds its text layer from.

The reads answer ``Cache-Control: no-store`` and no ETag: the workspace
travels in a header, so one URL names a file of every workspace, and the
text changes under it at each apply (its ``stamp``).
"""

import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import jobs, ocr_engine, pdf_ocr, pdf_text
from ..auth import require_personal_user_id, require_ws, resolve_ws, share_scope
from ..blocks_store import load_props
from ..db import connect_pages_db
from ..documents.model import file_hash, is_pdf, page_document, stored_name, url_hash
from .pages import NOT_STORED
from .uploads import _share_can_read_upload

router = APIRouter(prefix="/api", tags=["ocr"])

OCR = "ocr"  # the job's kind
GUESTS = "Recognizing text needs an account."
ELSEWHERE = "Someone else is recognizing this PDF's text right now."
STOPPING = "the last run for this PDF is still stopping — try again in a moment"
_DOC_ID_RE = re.compile(r"^[0-9a-f]{1,128}$")
_NO_STORE = {"Cache-Control": "no-store"}


class OcrRequest(BaseModel):
    page_id: str


# Sync def: the check sets the engine up (a fraction of a second) and the page
# read is a database's, both in the threadpool.
@router.post("/jobs/ocr")
def start_ocr(payload: OcrRequest, request: Request):
    """Start the ``ocr`` job (gamma/pdf_ocr.py ``run``) for the page's
    stored PDF: its scanned pages' words read from their pictures, applied
    at once when it ends. A session account that is no guest (403
    ``GUESTS``: a guest's hours of CPU on a demo server; a token gets 403
    too, as for every job) and edits the workspace. The PDF is the page's
    own or a copy's, stored here and read by the viewer from those bytes: a
    PDF shown from an address reads its stored copy only when the PDF proxy
    serves that copy (its hash is the address's), else 400
    ``NOT_STORED``, as for a page whose PDF is not stored at all; 400 for a
    page without a PDF, 404 for no such page. 503 with a plain sentence
    when this server recognizes no text (turned off, no engine or models,
    an engine that does not start: ``ocr_engine.check``). One run at a
    time for the workspace's file, the look for one and the start in one
    turn of the jobs' lock (``jobs.start``'s ``exclusive``): while one is
    going, its own owner gets it back, anyone else 409 ``ELSEWHERE``; 409
    while the owner's last run is still stopping; 429 when the account's
    jobs are full. → the job:
    params ``{page_id, doc_id, title}``, title "Recognizing the text of
    “{title}”"."""
    user_id = require_personal_user_id(request, GUESTS)
    ws = require_ws(request, write=True)
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?",
                           (payload.page_id,)).fetchone()
    if not row or row[0] != "root":
        raise HTTPException(status_code=404, detail="page not found")
    view = page_document(load_props(row[2]))
    if not is_pdf(view):
        raise HTTPException(status_code=400, detail="the page has no PDF")
    doc_id = file_hash(view)
    # A PDF at an address is shown through the proxy, which serves the copy
    # stored under the address's hash, and only that one.
    shown = doc_id and (view["at"] != "url" or url_hash(view["url"]) == doc_id)
    src = pdf_ocr.stored_path(ws, doc_id) if shown else None
    if src is None:
        raise HTTPException(status_code=400, detail=NOT_STORED)
    reason = ocr_engine.check()
    if reason:
        raise HTTPException(status_code=503, detail=reason)
    name = " ".join((row[1] or "").split())[:80] or "Untitled"

    def run(job):
        return pdf_ocr.run(ws, doc_id, src, actor=user_id, progress=job.progress)

    try:
        # exclusive: whoever owns the run going for this file, found in the same turn of the lock as the start
        return jobs.start(OCR, owner=user_id, ws=ws, key=f"{ws}:{doc_id}", title=f"Recognizing the text of “{name}”",
                          params={"page_id": payload.page_id, "doc_id": doc_id, "title": name}, run=run,
                          writes=True, exclusive=True)
    except jobs.Busy as busy:
        if busy.job["owner"] != user_id:
            raise HTTPException(status_code=409, detail=ELSEWHERE)
        if busy.job.get("stopping"):
            raise HTTPException(status_code=409, detail=STOPPING)
        return busy.job


def _readable(request: Request, doc_id: str) -> str:
    """The workspace whose recognition of ``doc_id`` the request reads, by
    the file's own rule (/api/pdf-info's): any member, or a share whose
    pages show the file. 400 for no file id, 403 past the share."""
    if not _DOC_ID_RE.match(doc_id or ""):
        raise HTTPException(status_code=400, detail="invalid document id")
    ws = resolve_ws(request)
    scope = share_scope(request)
    if scope is not None and not _share_can_read_upload(ws, scope, stored_name(doc_id, ".pdf")):
        raise HTTPException(status_code=403, detail="not accessible via this share link")
    return ws


@router.get("/ocr/{doc_id}")
def ocr_state(doc_id: str, request: Request):
    """The stored PDF ``doc_id``'s recognition: ``{stamp, rev, engine,
    langs, pages, scanned, recognized, kept, at, pending, good_layer}``.
    ``stamp`` names the recognized text the readers read ("" before any:
    then ``recognized`` is empty), new at every apply; ``recognized`` the
    pages reading recognized words (1-based, sorted; a picture page with no
    real text among them, read as no text), ``kept`` the pages that keep
    their own text (a scan page's layer, a picture page's own words);
    ``scanned`` how many pages are scans or pictures (null
    until a run read them), ``pages`` the file's count; ``good_layer``
    whether a run found the file's own text reads well (pdf_ocr
    ``reads_well``); ``pending`` whether the last apply's finishing steps
    are still due. A file never recognized: the same with ``stamp`` "",
    ``rev`` 0, ``scanned`` null and empty lists."""
    ws = _readable(request, doc_id)
    found = pdf_ocr.state(ws, doc_id)
    body = {"stamp": "", "rev": 0, "engine": "", "langs": "", "pages": 0, "scanned": None, "recognized": [],
            "kept": [], "at": "", "pending": False, "good_layer": False}
    if found is not None:
        body.update({key: found[key] for key in ("stamp", "rev", "engine", "langs", "pages", "scanned", "recognized",
                                                 "kept", "at", "pending")}, good_layer=pdf_ocr.reads_well(found))
    return JSONResponse(body, headers=_NO_STORE)


@router.get("/ocr/{doc_id}/pages")
def ocr_pages(doc_id: str, request: Request, first: int = Query(1, ge=1, le=pdf_text.MAX_PAGES),
              last: int | None = Query(None, ge=1)):
    """The recognized lines of the stored PDF ``doc_id``'s live pages among
    ``first``..``last`` (1-based, ``first`` at most pdf_text's
    ``MAX_PAGES``, the last page a file is read to; ``last`` defaults to
    ``first``; at most ``pdf_ocr.MAX_RANGE`` pages from ``first`` are
    read): ``{stamp, first, last, pages: {"<n>": {"lines": [[[text, x0,
    y0, x1, y1], ...], ...]}}}``, ``last`` the range read, a page left out
    when it reads its own layer. A word's box is in 1/10000 of the page as
    shown (x of its width, y of its height, top-left origin). The read
    rule and headers as ``GET /api/ocr/{doc_id}``'s. 422 for a page out of
    those bounds, 400 for a range that ends before it begins, 503 when the
    recognized text cannot be read now."""
    ws = _readable(request, doc_id)
    last = first if last is None else last
    if last < first:
        raise HTTPException(status_code=400, detail="first is a page from 1 on, and last is not before it")
    last = min(last, first + pdf_ocr.MAX_RANGE - 1, pdf_text.MAX_PAGES)
    found = pdf_ocr.pages_lines(ws, doc_id, first, last)
    if found is None:
        raise HTTPException(status_code=503, detail="the recognized text cannot be read now — try again")
    pages = {str(page): {"lines": lines} for page, lines in sorted(found["pages"].items())}
    return JSONResponse({"stamp": found["stamp"], "first": first, "last": last, "pages": pages}, headers=_NO_STORE)
