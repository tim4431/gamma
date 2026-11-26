"""Text recognized on a stored PDF's scanned pages (gamma/ocr_run.py,
gamma/pdf_ocr.py, gamma/ocr_engine.py): the jobs that recognize it and the
one that forgets it, the window's half of a run in a window, and what the
viewer reads of it.

- ``POST /api/jobs/ocr`` ``{page_id, window?}``: start the ``ocr`` job for
  the page's stored PDF (the Document menu's Recognize text in this PDF, a
  background task's Try again), or, with ``window``, the ``ocr-tab`` job
  whose pages the window that asks reads (a server that reads no text
  itself: ocr_engine ``capability``). One of the OCR kinds at a time for a
  workspace's file (``ocr_run.OCR_KINDS``), whoever starts it.
- ``POST /api/jobs/ocr-drop`` ``{page_id}``: the ``ocr-drop`` job, which
  forgets what was recognized on the page's stored PDF (the Document menu's
  Forget the recognized text…).
- ``GET /api/ocr/{doc_id}/plan``, ``PUT /api/ocr/{doc_id}/pages/{n}``,
  ``POST /api/ocr/{doc_id}/done``: a run in a window, what it reads, each
  page's words, its end.
- ``GET /api/ocr/models/{tag}/{lang}.traineddata``: the models a window
  reads the pages with.
- ``GET /api/ocr/{doc_id}``: the file's recognition, for the Document menu
  and the viewer's text layer (frontend/src/pdf/ocrState.js).
- ``GET /api/ocr/{doc_id}/pages?first=&last=``: the recognized lines of
  the live pages in a range, which the viewer builds its text layer from.

The reads answer ``Cache-Control: no-store`` and no ETag: the workspace
travels in a header, so one URL names a file of every workspace, and the
text changes under it at each apply (its ``stamp``).
"""

import re
import sqlite3
import unicodedata
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .. import jobs, ocr_auto, ocr_engine, ocr_run, pdf_ocr, pdf_text, ratelimit, server_options
from ..auth import read_body, require_personal_user_id, require_user_id, require_ws, resolve_ws, share_scope
from ..blocks_store import load_props
from ..db import connect_pages_db
from ..documents.model import file_hash, is_pdf, page_document, stored_name, url_hash
from .pages import NOT_STORED
from .uploads import _share_can_read_upload

router = APIRouter(prefix="/api", tags=["ocr"])

OCR = "ocr"          # a run on the server
OCR_TAB = "ocr-tab"  # a run in a window
OCR_DROP = "ocr-drop"  # the forgetting of a file's recognized text
GUESTS = "Recognizing text needs an account."
FORGET_GUESTS = "Forgetting recognized text needs an account."
NOTHING_RECOGNIZED = "This PDF has no recognized text to forget."
ELSEWHERE = "Someone else is recognizing this PDF's text right now."
STOPPING = "the last run for this PDF is still stopping — try again in a moment"
RECOGNIZING = "This PDF's text is being recognized — wait for it, or stop it in Background tasks."
FORGETTING = "This PDF's recognized text is being forgotten — try again in a moment."
ITSELF = "This server recognizes text itself."
STALE = "Reload the page: this window's text recognition is out of date."
REPLACED = "The PDF was replaced: open it again."
STOPPED = "This PDF's recognition stopped."
OTHER_WINDOW = "This PDF's text is being recognized in another window now."
APPLIED = "This page's text is recognized already."
NOT_SCANNED = "This page is no scanned page of the PDF."
UNREADY = "This PDF's pages are still being read — try again in a moment."
UNKEPT = "the recognized text cannot be kept now — try again"
_REFUSALS = {"stopped": (409, STOPPED), "elsewhere": (409, ELSEWHERE), "other window": (409, OTHER_WINDOW),
             "applied": (409, APPLIED), "not scanned": (400, NOT_SCANNED)}
_DOC_ID_RE = re.compile(r"^[0-9a-f]{1,128}$")
_NO_STORE = {"Cache-Control": "no-store"}
# What a window is told to read its pages with (GET /api/ocr/{doc_id}/plan),
# what the server's engine does (ocr_engine.DPI, MAX_PIXELS, MAX_SIDE), and
# the most a page's words may be, which it fails the page past.
RENDER = {"dpi": ocr_engine.DPI, "max_pixels": ocr_engine.MAX_PIXELS, "max_side": ocr_engine.MAX_SIDE}
MAX_PAGE_BODY = 1 << 20
LIMITS = {"max_words": ocr_engine.MAX_WORDS, "max_word_chars": ocr_engine.MAX_WORD_CHARS, "max_body": MAX_PAGE_BODY}
# The pages one account may send a minute (a window reads one in seconds).
PAGES_PER_MINUTE = 120
# The characters a window's word loses before it is judged: controls,
# formats (a bidi override, a zero-width space), private use, surrogates and
# unassigned code points, which no engine reads off a page.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})


class WindowRun(BaseModel):
    """The window that reads a run's pages: the stored file it opened
    (``doc_id``), the engine, languages and VERSION it reads them with, and
    its per-load id (``client``)."""
    doc_id: str = Field(min_length=1, max_length=128)
    engine: str = Field(max_length=100)
    langs: str = Field(max_length=100)
    ver: int
    client: str = Field(min_length=1, max_length=64)


class OcrRequest(BaseModel):
    page_id: str
    window: WindowRun | None = None


def stored_pdf(ws: str, page_id: str) -> tuple[str, str, str]:
    """The stored PDF a run for page ``page_id`` reads: ``(doc_id, src,
    name)``, the page's own or a copy's, stored here and read by the viewer
    from those bytes (a PDF shown from an address reads its stored copy
    only when the PDF proxy serves that copy: its hash is the address's),
    and the page's title. 404 for no such page, 400 for a page without a
    PDF and 400 ``NOT_STORED`` for one whose PDF is not stored. The rule
    of every OCR job's start, the automatic recognition's too
    (gamma/ocr_auto.py)."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?",
                           (page_id,)).fetchone()
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
    return doc_id, src, " ".join((row[1] or "").split())[:80] or "Untitled"


def busy_job(busy: jobs.Busy, user_id: str, kind: str) -> dict | None:
    """What a start of ``kind`` answers when ``jobs.start`` found an OCR job
    of the file going (``Busy``, its ``exclusive`` the OCR kinds): that job
    when it is the caller's own of the kind asked for, or, for a run on the
    server, the workspace's automatic run of the file, which then no longer
    gives way (``jobs.promote``); None when that automatic run gave way, or
    ended, since the start looked (the caller starts again: a run of its
    own); 409 ``STOPPING`` while that one stops, 409 ``FORGETTING`` beside a
    forgetting, ``ELSEWHERE`` beside someone else's recognition and
    ``RECOGNIZING`` beside the caller's own of another kind or an automatic
    one."""
    job = busy.job
    automatic = kind == OCR and job["kind"] == OCR and job["owner"] == jobs.WORKSPACE
    if job["kind"] == kind and (job["owner"] == user_id or automatic):
        if job.get("stopping"):
            raise HTTPException(status_code=409, detail=STOPPING)
        if automatic and not jobs.promote(job["id"]):
            return None
        return job
    if job["kind"] == OCR_DROP:
        raise HTTPException(status_code=409, detail=FORGETTING)
    if job["owner"] not in (user_id, jobs.WORKSPACE):
        raise HTTPException(status_code=409, detail=ELSEWHERE)
    raise HTTPException(status_code=409, detail=RECOGNIZING)


def _unavailable() -> HTTPException:
    """The 503 of a server where no text is recognized at all
    (``capability`` None): turned off, or its models missing."""
    reason = ocr_engine.NO_MODELS if server_options.ocr_enabled() else ocr_engine.TURNED_OFF
    return HTTPException(status_code=503, detail=reason)


def _same_engine(engine: str, langs: str, ver: int) -> None:
    """409 ``STALE`` unless a window reads with ``ocr_engine.BROWSER_ENGINE``,
    ``LANGS`` and this ``pdf_ocr.VERSION``: a window loaded before an upgrade
    would stage words nobody reads, or read otherwise."""
    if (engine, langs, ver) != (ocr_engine.BROWSER_ENGINE, ocr_engine.LANGS, pdf_ocr.VERSION):
        raise HTTPException(status_code=409, detail=STALE)


# Sync def: the check sets the engine up (a fraction of a second) and the page
# read is a database's, both in the threadpool.
@router.post("/jobs/ocr")
def start_ocr(payload: OcrRequest, request: Request):
    """Start the recognition of the page's stored PDF (``stored_pdf``) by
    a session account that is no guest (403 ``GUESTS``: a guest's hours of
    CPU on a demo server; a token gets 403 too, as for every job) and edits
    the workspace.

    - Without ``window``: the ``ocr`` job (gamma/ocr_run.py ``run``), its
      scanned pages' words read from their pictures here, applied at once
      when it ends. 503 with a plain sentence when this server recognizes
      no text itself (turned off, no engine or models, an engine that does
      not start, a server that leaves it to the window:
      ``ocr_engine.check``). Its params ``{page_id, doc_id, title}``, its
      title "Recognizing the text of “{title}”". Its end settles the
      file's automatic recognition, when one waits for it, and gives the
      next one its turn (gamma/ocr_auto.py ``ended``).
    - With ``window`` ``{doc_id, engine, langs, ver, client}``: the
      ``ocr-tab`` job (``ocr_run.window_run``), which waits while that
      window reads the pages and sends their words (the routes below). 409
      ``ITSELF`` where the server recognizes text itself, 503 where no text
      is recognized; 409 ``STALE`` for another engine, languages or
      version; 409 ``REPLACED`` when the page's PDF is no longer the
      window's ``doc_id``. Outside the lane and the running limits (the
      window's CPU does the reading: jobs ``AT_ONCE``). Its params
      ``{page_id, doc_id, title, window}``, its title "Reading the text of
      “{title}” in a window". The caller's own one going for the file goes
      on in this window (``ocr_run.window_take``: a reloaded window), even
      when its job has not begun waiting for pages yet.

    One of the OCR kinds at a time for the workspace's file
    (``ocr_run.OCR_KINDS``), the look for one and the start in one turn of
    the jobs' lock (``jobs.start``'s ``exclusive``), whoever starts it:
    ``busy_job`` says what a start beside one gets. A start that finds the
    file's automatic run is that run, promoted, unless it gave way already:
    then it is a run of its own (never a job that ends given way). 429 when
    the account's jobs are full. → the job."""
    user_id = require_personal_user_id(request, GUESTS)
    ws = require_ws(request, write=True)
    doc_id, src, name = stored_pdf(ws, payload.page_id)
    key = f"{ws}:{doc_id}"
    window = payload.window
    if window is None:
        reason = ocr_engine.check()
        if reason:
            raise HTTPException(status_code=503, detail=reason)

        def run(job):
            return ocr_run.run(ws, doc_id, src, actor=user_id, progress=job.progress)

        def ended(state):  # the file's automatic run settled, if one waits, and the next one's turn
            ocr_auto.ended(ws, doc_id, state)

        kind, title, params = OCR, f"Recognizing the text of “{name}”", {}
    else:
        capability = ocr_engine.capability()
        if capability is None:
            raise _unavailable()
        if capability["where"] != "window":
            raise HTTPException(status_code=409, detail=ITSELF)
        _same_engine(window.engine, window.langs, window.ver)
        if window.doc_id != doc_id:
            raise HTTPException(status_code=409, detail=REPLACED)

        def run(job):
            return ocr_run.window_run(ws, doc_id, src, actor=user_id, client=window.client, progress=job.progress,
                                      job_id=job.id)

        kind, title, params = OCR_TAB, f"Reading the text of “{name}” in a window", {"window": window.model_dump()}
        ended = None
    for _ in range(2):  # twice: the automatic run found may give way before it is promoted (busy_job None)
        try:
            # exclusive: whoever owns the job going for this file, found in the same turn of the lock as the start
            return jobs.start(kind, owner=user_id, ws=ws, key=key, title=title,
                              params={"page_id": payload.page_id, "doc_id": doc_id, "title": name, **params},
                              run=run, writes=True, exclusive=ocr_run.OCR_KINDS, ended=ended)
        except jobs.Busy as busy:
            job = busy_job(busy, user_id, kind)
            if job is None:
                continue
            if window is not None:
                ocr_run.window_take(ws, doc_id, job["id"], user_id, window.client)
            return job
    raise HTTPException(status_code=409, detail=STOPPING)


class ForgetRequest(BaseModel):
    page_id: str


# Sync def: the state is a database's read, in the threadpool.
@router.post("/jobs/ocr-drop")
def start_forget(payload: ForgetRequest, request: Request):
    """Start forgetting the text recognized on the page's stored PDF
    (``stored_pdf``), the ``ocr-drop`` job (gamma/pdf_ocr.py
    ``forget_recognized``), by a session account that is no guest (403
    ``FORGET_GUESTS``) and edits the workspace: every page of it reads the
    PDF's own text again, for everyone in the workspace, and its
    highlights take that text as their quote where it reads under them. No
    engine needed: a server that recognizes nothing (the apps, macOS
    before 15, recognition turned off since) forgets too. 400
    ``NOTHING_RECOGNIZED`` when no page reads recognized text. Not
    stoppable (it updates from its start), outside the lane. One of the OCR
    kinds at a time for the workspace's file (``ocr_run.OCR_KINDS``):
    ``busy_job`` says what a start beside one gets (the caller's own
    forgetting going is answered). Its params ``{page_id, doc_id,
    title}``, its title "Forgetting the recognized text of “{title}”". →
    the job."""
    user_id = require_personal_user_id(request, FORGET_GUESTS)
    ws = require_ws(request, write=True)
    doc_id, _src, name = stored_pdf(ws, payload.page_id)
    found = pdf_ocr.state(ws, doc_id)
    if found is None or not found["recognized"]:
        raise HTTPException(status_code=400, detail=NOTHING_RECOGNIZED)

    def run(job):
        return ocr_run.forget_recognized(ws, doc_id, actor=user_id, progress=job.progress)

    try:
        return jobs.start(OCR_DROP, owner=user_id, ws=ws, key=f"{ws}:{doc_id}",
                          title=f"Forgetting the recognized text of “{name}”",
                          params={"page_id": payload.page_id, "doc_id": doc_id, "title": name}, run=run,
                          writes=True, exclusive=ocr_run.OCR_KINDS)
    except jobs.Busy as busy:
        return busy_job(busy, user_id, OCR_DROP)


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


def _window_writer(request: Request, doc_id: str) -> tuple[str, str]:
    """``(account, workspace)`` of a window's request about a run in a
    window: a session account that is no guest and edits the workspace,
    and a file id. 403/400 as for the start."""
    user_id = require_personal_user_id(request, GUESTS)
    ws = require_ws(request, write=True)
    if not _DOC_ID_RE.match(doc_id or ""):
        raise HTTPException(status_code=400, detail="invalid document id")
    return user_id, ws


def _failed_now(ws: str, doc_id: str) -> frozenset:
    """The pages the run in a window going for the file found no result on
    (``ocr_run.window``), which its plan leaves out; none without one."""
    found = ocr_run.window(ws, doc_id)
    return found["failed"] if found else frozenset()


@router.get("/ocr/models/{tag}/{lang}.traineddata")
def ocr_model(tag: str, lang: str, request: Request):
    """A model a window reads its pages with (``LANGS``'s ``lang``, of the
    models tagged ``tag`` now: ocr_engine ``models_tag``, the URL
    ``capability``'s ``models`` names), for a session; 404 for another tag
    or name, and unless this server leaves the reading to the window. Kept
    a year by the browser: the tag changes with the files."""
    require_user_id(request)
    capability = ocr_engine.capability()
    path = ocr_engine.model_path(tag, lang) if capability and capability["where"] == "window" else None
    if path is None:
        raise HTTPException(status_code=404, detail="no such model")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/ocr/{doc_id}")
def ocr_state(doc_id: str, request: Request):
    """The stored PDF ``doc_id``'s recognition: ``{stamp, rev, engine,
    langs, pages, scanned, recognized, kept, at, pending, good_layer, todo,
    staged}``. ``stamp`` names the recognized text the readers read (""
    before any: then ``recognized`` is empty), new at every apply;
    ``recognized`` the pages reading recognized words (1-based, sorted; a
    picture page with no real text among them, read as no text), ``kept``
    the pages that keep their own text (a scan page's layer, a picture
    page's own words); ``engine`` what read them (" + " between two);
    ``scanned`` how many pages are scans or pictures (null until a run
    read them), ``pages`` the file's count; ``good_layer`` whether a run
    found the file's own text reads well (ocr_run ``reads_well``);
    ``pending`` whether the last apply's finishing steps are still due;
    ``todo`` how many pages a run would read now (ocr_run ``plan_of``,
    null until a run read which pages are scans or pictures); ``staged``
    how many recognized pages a stopped run left to apply. A file never
    recognized: the same with ``stamp`` "", ``rev`` 0, ``scanned`` and
    ``todo`` null, ``staged`` 0 and empty lists."""
    ws = _readable(request, doc_id)
    found = pdf_ocr.state(ws, doc_id)
    body = {"stamp": "", "rev": 0, "engine": "", "langs": "", "pages": 0, "scanned": None, "recognized": [],
            "kept": [], "at": "", "pending": False, "good_layer": False, "todo": None, "staged": 0}
    if found is not None:
        body.update({key: found[key] for key in ("stamp", "rev", "engine", "langs", "pages", "scanned", "recognized",
                                                 "kept", "at", "pending")}, good_layer=ocr_run.reads_well(found),
                    staged=len(found["staged"]))
        if found["kinds"] is not None:
            body["todo"] = len(ocr_run.plan_of(found, found["kinds"], _failed_now(ws, doc_id))["todo"])
    return JSONResponse(body, headers=_NO_STORE)


@router.get("/ocr/{doc_id}/plan")
def ocr_plan(doc_id: str, request: Request):
    """What a run in a window reads of the stored PDF ``doc_id`` now, for
    a session account that is no guest and edits the workspace: ``{todo,
    sample, done, good_layer, render, limits}``, ocr_run ``plan_of`` over
    the pages its job found scanned and those the window going found no
    result on; ``render`` how to draw a page (``{dpi, max_pixels,
    max_side}``, the server engine's), ``limits`` the most a page's words
    may be (``{max_words, max_word_chars, max_body}``), past which the
    window sends the page as failed. 409 ``UNREADY`` until a run read which
    pages are scans or pictures (the window asks once its job reports
    ``recognizing``). Headers as ``GET /api/ocr/{doc_id}``'s."""
    _user_id, ws = _window_writer(request, doc_id)
    found = pdf_ocr.state(ws, doc_id)
    if found is None or found["kinds"] is None:
        raise HTTPException(status_code=409, detail=UNREADY)
    plan = ocr_run.plan_of(found, found["kinds"], _failed_now(ws, doc_id))
    return JSONResponse({**plan, "render": RENDER, "limits": LIMITS}, headers=_NO_STORE)


_Text = Annotated[str, Field(max_length=ocr_engine.MAX_WORD_CHARS)]
_Unit = Annotated[int, Field(ge=0, le=pdf_text.OCR_UNITS)]
_Conf = Annotated[float, Field(ge=0, le=100)]


class PageWords(BaseModel):
    """A page's words as a window read them: the window (``client``) and
    what it reads with, then its ``lines``, each word ``[text, x0, y0, x1,
    y1, confidence]``, the box in whole 1/10000 of the page as shown, at
    most ``ocr_engine.MAX_WORDS`` words of at most ``MAX_WORD_CHARS``
    characters; or ``failed``: it found no result."""
    model_config = ConfigDict(allow_inf_nan=False)
    client: str = Field(min_length=1, max_length=64)
    engine: str = Field(max_length=100)
    langs: str = Field(max_length=100)
    ver: int
    lines: list[Annotated[list[tuple[_Text, _Unit, _Unit, _Unit, _Unit, _Conf]],
                          Field(max_length=ocr_engine.MAX_WORDS)]] | None = Field(
        default=None, max_length=ocr_engine.MAX_WORDS)
    failed: bool = False

    @model_validator(mode="after")
    def _words_or_failed(self):
        if self.failed == (self.lines is not None):
            raise ValueError("a page sends its lines, or failed: true")
        if self.lines is not None and sum(len(line) for line in self.lines) > ocr_engine.MAX_WORDS:
            raise ValueError(f"more than {ocr_engine.MAX_WORDS} words")
        return self


def clean_word(text: str) -> str:
    """A window's word as it is judged: the characters no engine reads off a
    page dropped (``_DROPPED_CATEGORIES``), the rest NFC; the page rules
    then drop its white space, and the word when nothing is left."""
    return unicodedata.normalize("NFC", "".join(ch for ch in text if unicodedata.category(ch)
                                                not in _DROPPED_CATEGORIES))


@router.put("/ocr/{doc_id}/pages/{page}")
async def ocr_put_page(doc_id: str, request: Request, page: int = Path(ge=1, le=pdf_text.MAX_PAGES)):
    """A page of the run in a window going for the stored PDF ``doc_id``,
    as that window read it: body ``{client, engine, langs, ver, lines |
    failed: true}`` (``PageWords``, at most ``MAX_PAGE_BODY`` bytes: 413
    past it, 422 off its shape), from the account's own window that runs
    it. Each word loses what no engine reads off a page (``clean_word``),
    then the page rules judge it against the page's own text, read here,
    and stage it (ocr_run ``window_page``). → ``{outcome, shared, todo,
    done, total}``: "recognized", "kept", "empty", "failed", or "skipped"
    for a page the run no longer reads (its sample's verdict moved on);
    a page sent again gets its first answer. 409 ``STOPPED`` when no run
    in a window takes the file's pages (none going, stopping, or not yet
    waiting for them), ``ELSEWHERE`` for another account's, ``OTHER_WINDOW``
    for one going in another window, ``STALE`` for another engine,
    ``APPLIED`` for a page reading recognized text already, ``REPLACED``
    when the file left the store; 400 ``NOT_SCANNED`` for no scan or
    picture page of the file; 429 past ``PAGES_PER_MINUTE`` of the
    account's; 503 when data.db cannot be written now. Async only to read
    the body; the rest runs in the threadpool."""
    require_personal_user_id(request, GUESTS)
    body = await read_body(request, MAX_PAGE_BODY, f"a page's words are at most {MAX_PAGE_BODY} bytes")
    return await run_in_threadpool(_put_page, request, doc_id, page, body)


def _live_window(ws: str, doc_id: str) -> dict:
    """The file's ``ocr-tab`` job, going and not stopping, else 409
    ``STOPPED``: a page sent after a Stop lands nowhere."""
    job = jobs.live(OCR_TAB, f"{ws}:{doc_id}")
    found = ocr_run.window(ws, doc_id)
    if job is None or job.get("stopping") or found is None or found["job_id"] != job["id"]:
        raise HTTPException(status_code=409, detail=STOPPED)
    return job


def _put_page(request: Request, doc_id: str, page: int, body: bytes) -> JSONResponse:
    user_id, ws = _window_writer(request, doc_id)
    ratelimit.check(f"ocr-page:{user_id}", PAGES_PER_MINUTE, 60)
    try:
        words = PageWords.model_validate_json(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"a page's words: {e.errors(include_url=False)[0]['msg']}")
    _same_engine(words.engine, words.langs, words.ver)
    _live_window(ws, doc_id)
    src = pdf_ocr.stored_path(ws, doc_id)
    if src is None:
        raise HTTPException(status_code=409, detail=REPLACED)
    lines = None if words.lines is None else [[[clean_word(text), *rest] for text, *rest in line]
                                              for line in words.lines]
    try:
        answer = ocr_run.window_page(ws, doc_id, page, lines, account=user_id, client=words.client, src=src)
    except ocr_run.WindowRefusal as refusal:
        status, detail = _REFUSALS[refusal.reason]
        raise HTTPException(status_code=status, detail=detail)
    except (ValueError, sqlite3.Error, OSError):  # data.db not read or written now: the window sends it again
        raise HTTPException(status_code=503, detail=UNKEPT)
    return JSONResponse(answer, headers=_NO_STORE)


class WindowDone(BaseModel):
    client: str = Field(min_length=1, max_length=64)


@router.post("/ocr/{doc_id}/done")
def ocr_window_done(doc_id: str, payload: WindowDone, request: Request):
    """The window ``client`` read what its run in a window would: the run
    applies what it staged at its next report (ocr_run ``window_done``). The
    account's own window that runs it; 409 as for a page. → the job."""
    user_id, ws = _window_writer(request, doc_id)
    job = _live_window(ws, doc_id)
    try:
        ocr_run.window_done(ws, doc_id, account=user_id, client=payload.client)
    except ocr_run.WindowRefusal as refusal:
        status, detail = _REFUSALS[refusal.reason]
        raise HTTPException(status_code=status, detail=detail)
    return jobs.get(job["id"])


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
