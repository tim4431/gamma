"""Automatic text recognition: a PDF a person uploads whose pages are all
pictures, with no text to select or search, has its words recognized by the
server by itself, one file at a time, by the ``ocr`` job of
gamma/ocr_run.py (docs/dev/documents.md "Recognized text").

The switch is the admin's (the server option ``ocr_auto``, on by default:
Settings → Server → Text recognition), and it works only where the
server recognizes text itself (``available``: never on a hosted container,
a demo server or the apps, where the window that asks reads the pages).

Which files: the routes that make a library page of a person's upload (POST
/api/blocks/by-doc when it made the page, /api/clip for a new page, POST
/api/pages/by-file, Add to library and Save a copy to library) call
``consider``, which hands the file to this module's one worker thread (a
queue, one entry a file). The worker takes a file up only while the switch
is on, its workspace is no guest's and it has no marker and no recognized
page, and only when the page shows its stored copy (routers/ocr.py
``stored_pdf``); then it reads which pages are pictures (``qualifies``): no
page may be a scan with a layer, all but ``AUTO_OTHER_PAGES`` must be
pictures (a publisher's cover, a download stamp) and at least one, at most
``AUTO_MAX_PAGES``. A born-digital paper with a full-page figure is none.
Whatever it finds, the file gets its marker, so it is classified once.

The markers are the queue (data.db ``pdf_ocr_auto``: ``doc_id``, ``state``,
``by`` the uploader, ``at``): ``queued`` (its run to start, or running),
``skipped`` (no such scan), ``done`` (its run ended, or failed: never tried
again; a run a workspace delete stopped stays queued), ``declined`` (a
person stopped its run, or forgot what it recognized: gamma/ocr_run.py
``forget_recognized``). ``next`` starts the
oldest ``queued`` file of any workspace as the workspace's own ``ocr`` job
(owner ``jobs.WORKSPACE``: its members see it, its editors stop it) when no
``ocr`` job is live on the server, so at most one runs at a time and a
person's comes first: the automatic one ``yields`` (jobs ``Yield``: it stops
at its next page, its staged pages and its marker kept, and goes on from
them later). The end of every ``ocr`` job, an automatic one's or one a
person asked for, settles the file's marker and starts the next
(``ended``); the app's round beside the jobs' sweep, at startup the first
time, starts the files a restart left queued (``resume``).

An automatic run's writes (its highlights' quotes respelled, its page's
failed metadata lookup forgotten, ``meta_error``, so the next open looks
the paper up with its new text) go under the uploader, else the
workspace's personal owner.
"""

import json
import threading
import time
from contextlib import closing

from . import db, jobs, ocr_engine, ocr_run, pdf_ocr, pdf_text, server_options, workspaces
from .logbuf import log

OCR = "ocr"
# A file is recognized by itself only when it has at most so many pages that
# are no picture (a publisher's cover, a terms sheet, a download stamp)...
AUTO_OTHER_PAGES = 2
# ...and at most so many picture pages (an hour or more on a NAS past it:
# such a book waits for someone to ask, from the Document menu).
AUTO_MAX_PAGES = 300
QUEUED, SKIPPED, DONE, DECLINED = "queued", "skipped", "done", "declined"

_ASK = object()


# --- The switch ------------------------------------------------------------------

def available(capability=_ASK) -> bool:
    """Whether this server can recognize scans by itself: it recognizes
    text itself (ocr_engine ``capability``, or the one given, says
    "server")."""
    found = ocr_engine.capability() if capability is _ASK else capability
    return bool(found) and found.get("where") == "server"


def switched_on() -> bool:
    """The admin's switch (the server option ``ocr_auto``); off while
    users.db cannot be read (logged)."""
    try:
        return bool(server_options.get("ocr_auto"))
    except Exception as e:  # noqa: BLE001 — nothing starts by itself until the switch reads again
        log.warning(f"[ocr] the automatic recognition's switch cannot be read: {e}")
        return False


def enabled(capability=_ASK) -> bool:
    """Whether a scan uploaded with no text is recognized by itself now: the
    switch on, on a server that can (``available``)."""
    return switched_on() and available(capability)


# --- The markers ------------------------------------------------------------------

def marker(ws: str, doc_id: str) -> str | None:
    """The state of the file's marker, None when it has none (or data.db,
    opened only when it exists, cannot be read: logged)."""
    if not pdf_ocr.has_data_db(ws):
        return None
    try:
        with db.connect_data_db(ws) as conn:
            row = conn.execute("SELECT state FROM pdf_ocr_auto WHERE doc_id = ?", (doc_id,)).fetchone()
    except Exception as e:  # noqa: BLE001
        log.warning(f"[ocr] reading the automatic recognition's marker of {doc_id} failed: {e}")
        return None
    return row[0] if row else None


def mark(ws: str, doc_id: str, state: str, by: str = "", *, first: bool = False) -> bool:
    """The file's marker set to ``state`` now (``by``: who uploaded it, the
    one recorded unless the file has a marker already). ``first``: only
    when it has none. Whether a marker was written; never raises (logged:
    a marker not written leaves the file as it was)."""
    on_conflict = "NOTHING" if first else "UPDATE SET state = excluded.state, at = excluded.at"
    try:
        with db.connect_data_db(ws) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(f"INSERT INTO pdf_ocr_auto (doc_id, state, by, at) VALUES (?, ?, ?, ?) "
                               f"ON CONFLICT (doc_id) DO {on_conflict}", (doc_id, state, by or "", db.page_now()))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:  # noqa: BLE001
        log.warning(f"[ocr] marking {doc_id} {state} for the automatic recognition failed: {e}")
        return False


def _queued_in(ws: str) -> list[tuple[str, str, str]]:
    """``(at, doc_id, by)`` of the workspace's queued markers; none when it
    has no data.db. Raises as a read of data.db does."""
    if not pdf_ocr.has_data_db(ws):
        return []
    with db.connect_data_db(ws) as conn:
        return conn.execute("SELECT at, doc_id, by FROM pdf_ocr_auto WHERE state = ?", (QUEUED,)).fetchall()


# --- Considering a file -------------------------------------------------------------

def qualifies(src) -> tuple[int, dict] | None:
    """Whether the PDF at ``src`` is a scan with no text, which this module
    recognizes by itself: ``(pages, kinds)``, its page count and {page:
    "picture"} of its picture pages (ocr_run ``classify``'s shape), or
    None. Its pages are classified in turn (pdf_text ``page_kinds``), and
    the walk stops at the first scan page with a layer, at the first page
    past ``AUTO_OTHER_PAGES`` that is no picture and at the picture page
    past ``AUTO_MAX_PAGES``, none of which qualify, nor does a file with no
    picture page or one that does not open."""
    total = pdf_text.page_count(src)
    if not total:
        return None
    kinds, other = {}, 0
    try:
        with closing(pdf_text.page_kinds(src)) as walk:
            for page, kind in walk:
                if kind == "scan":
                    return None
                if kind == "picture":
                    kinds[page] = kind
                    if len(kinds) > AUTO_MAX_PAGES:
                        return None
                else:
                    other += 1
                    if other > AUTO_OTHER_PAGES:
                        return None
    except Exception as e:  # noqa: BLE001 — a file that does not read is no scan to recognize by itself
        log.info(f"[ocr] a PDF considered for automatic recognition does not read: {e}")
        return None
    return (total, kinds) if kinds else None


_queue: dict[tuple[str, str], tuple[str, str]] = {}  # (ws, doc_id) → (page_id, actor), to consider, oldest first
_cond = threading.Condition()
_considering = 0  # files the worker is considering now: ``drain`` waits for them too
_worker: threading.Thread | None = None


def consider(ws: str, doc_id: str, page_id: str, actor: str) -> None:
    """A route made the library page ``page_id`` of the stored PDF
    ``doc_id``, a person's (``actor``) upload: hand it to the worker
    thread, which decides off the request whether it is a scan to recognize
    by itself (``_consider``). A file handed over twice before the worker
    took it is considered once. Nothing while the switch is off (read
    here, the environment first). Never raises."""
    global _worker
    if not doc_id or not switched_on():
        return
    with _cond:
        _queue.setdefault((ws, doc_id), (page_id, actor))
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="ocr-auto", daemon=True)
            _worker.start()
        _cond.notify_all()


def _work() -> None:
    global _considering
    while True:
        with _cond:
            while not _queue:
                _cond.wait()
            for key in _queue:  # the oldest
                break
            page_id, actor = _queue.pop(key)
            _considering += 1
        try:
            _consider(*key, page_id, actor)
        except Exception:  # noqa: BLE001 — the worker goes on to the next file
            log.exception(f"[ocr] considering {key[1]} for automatic recognition failed")
        finally:
            with _cond:
                _considering -= 1
                _cond.notify_all()


def _consider(ws: str, doc_id: str, page_id: str, actor: str) -> None:
    """The worker's look at one file (``consider``): taken up only while
    the switch is on, in a workspace that is no guest's and is not being
    deleted, when the file has
    no marker and no recognized page and the page shows its stored copy;
    then marked ``queued`` with its kinds recorded (pdf_ocr
    ``set_scanned``) when it ``qualifies``, its run started when its turn
    has come (``next``), else ``skipped``."""
    from fastapi import HTTPException

    from .routers.ocr import stored_pdf  # local: keep gamma.* free of the routers package

    if (not enabled() or workspaces.is_deleting(ws) or workspaces.is_guest_workspace(ws)
            or marker(ws, doc_id) is not None):
        return
    found = pdf_ocr.state(ws, doc_id)
    if found is not None and found["recognized"]:
        return
    try:
        shown, src, _title = stored_pdf(ws, page_id)
    except HTTPException:
        return  # no stored copy to read: nothing to recognize, by itself or when asked
    if shown != doc_id:
        return
    scan = qualifies(src)
    if scan is None:
        mark(ws, doc_id, SKIPPED, actor, first=True)
        return
    total, kinds = scan
    pdf_ocr.set_scanned(ws, doc_id, total, len(kinds), kinds)
    if mark(ws, doc_id, QUEUED, actor, first=True):
        with _lock:
            _waiting.add(ws)
        next()


def drain(timeout: float = 30.0) -> bool:
    """Wait until the worker has considered every file handed to it (or
    ``timeout``): whether it did. For tests."""
    deadline = time.monotonic() + timeout
    with _cond:
        while _queue or _considering:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _cond.wait(left)
    return True


# --- The queue ------------------------------------------------------------------------

_lock = threading.RLock()  # ``next`` one at a time, and ``_waiting``
_waiting: set[str] = set()  # the workspaces that may hold queued markers
_scanned = False  # whether ``resume`` looked through every workspace for them


def next() -> dict | None:  # noqa: A001 — the queue's own word (ocr_auto.next), never the builtin here
    """Start the automatic run of the oldest queued file of any workspace,
    when the switch is on and no ``ocr`` job is live on the server (a
    person's included: the lane is theirs first), as the workspace's own
    ``ocr`` job (``_start``). → the job started, or None. A file whose run
    cannot start now is passed over for the next one. Never raises
    (logged)."""
    with _lock:
        try:
            if jobs.live_count(OCR) or not enabled() or ocr_engine.check():
                return None
            for ws, doc_id, by in _queued():
                try:
                    job = _start(ws, doc_id, by)
                except Exception:  # noqa: BLE001 — left queued: the next file's turn now, its own later
                    log.exception(f"[ocr] the automatic recognition of {doc_id} did not start")
                    continue
                if job is not None:
                    return job
        except Exception:  # noqa: BLE001 — the next end of an ``ocr`` job or sweep tries again
            log.exception("[ocr] starting the next automatic recognition failed")
    return None


def _queued() -> list[tuple[str, str, str]]:
    """``(ws, doc_id, by)`` of the queued files of the workspaces that may
    hold some (``_waiting``, ``_lock`` held), oldest first. A workspace
    with none leaves the set; one whose data.db does not read is passed
    over (logged)."""
    found = []
    for ws in sorted(_waiting):
        try:
            rows = _queued_in(ws)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[ocr] reading the automatic recognition's queue of a workspace failed: {e}")
            continue
        if not rows:
            _waiting.discard(ws)
        found += [(at, ws, doc_id, by) for at, doc_id, by in rows]
    return [(ws, doc_id, by) for _at, ws, doc_id, by in sorted(found)]


def _page_of(ws: str, doc_id: str) -> str:
    """The page an automatic run of the file is for: the library page that
    owns it, else the first page showing it; "" for none."""
    from .blocks_store import page_for_doc, pages_showing  # local: they import this module's neighbours

    with db.connect_pages_db(ws) as conn:
        row = page_for_doc(conn, doc_id)
        if row:
            return row[0]
        shown = pages_showing(conn, doc_id)
    return shown[0] if shown else ""


def _start(ws: str, doc_id: str, by: str) -> dict | None:
    """The queued file's automatic run started: the ``ocr`` job of the
    workspace (``jobs.WORKSPACE``) for the page that shows its stored copy,
    keyed by the workspace and the file as a person's run is, one of the
    OCR kinds at a time (``ocr_run.OCR_KINDS``), ``yields`` so a run
    someone asks for goes first, its params ``{page_id, doc_id, title,
    auto: true}``. Its row of a run a restart cut short goes first (it
    would read as failed beside the new one). None when it does not start:
    marked ``done`` when its text was recognized meanwhile, ``skipped`` when
    no page shows its stored copy any more, left queued while another OCR
    job of the file goes (its end decides) or its workspace is being
    deleted (whose writers a delete stopped: none may start there now)."""
    from fastapi import HTTPException

    from .routers.ocr import stored_pdf  # local: keep gamma.* free of the routers package

    if workspaces.is_deleting(ws):
        return None
    found = pdf_ocr.state(ws, doc_id)
    if found is not None and found["recognized"]:
        mark(ws, doc_id, DONE)
        return None
    page_id = _page_of(ws, doc_id)
    try:
        shown, src, title = stored_pdf(ws, page_id) if page_id else ("", "", "")
    except HTTPException:
        shown = ""
    if shown != doc_id:
        mark(ws, doc_id, SKIPPED)
        return None
    actor = by or workspaces.personal_owner(ws)
    key = f"{ws}:{doc_id}"
    before = jobs.latest(jobs.WORKSPACE, OCR, key)
    if before and before["state"] == "failed" and before["error"] == jobs.INTERRUPTED:
        jobs.dismiss(before["id"])

    def run(job):
        result = ocr_run.run(ws, doc_id, src, actor=actor, progress=job.progress)
        if result["recognized"]:
            _clear_meta_error(ws, page_id, actor)
        return result

    try:
        return jobs.start(OCR, owner=jobs.WORKSPACE, ws=ws, key=key, title=f"Recognizing the text of “{title}” automatically",
                          params={"page_id": page_id, "doc_id": doc_id, "title": title, "auto": True}, run=run,
                          writes=True, exclusive=ocr_run.OCR_KINDS, yields=True,
                          ended=lambda state: ended(ws, doc_id, state))
    except jobs.Busy:
        return None


def _clear_meta_error(ws: str, page_id: str, actor: str) -> None:
    """The page's failed metadata lookup forgotten (``meta_error``, which
    keeps the app from looking a paper up again on open): it read the
    scan's missing text, and the next open looks the paper up with the
    recognized one. One op under ``actor``; nothing when the page has none.
    Never raises (logged)."""
    from .blocks_store import write_lock  # local: they import this module's neighbours
    from .highlight_relocate import RESPELL_CLIENT
    from .ops import after_commit, apply_ops

    try:
        with db.connect_pages_db(ws) as conn:
            write_lock(conn)
            row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                               (page_id,)).fetchone()
            if row is None or "meta_error" not in json.loads(row[0] or "{}"):
                conn.commit()  # nothing to write: let go of the lock
                return
            after_commit(ws, conn, apply_ops(conn, page_id, [{"op": "set", "id": page_id, "props": {"meta_error": None}}],
                                             actor=actor, client=RESPELL_CLIENT))
    except Exception as e:  # noqa: BLE001 — the text is recognized; the lookup waits for a recheck
        log.warning(f"[ocr] clearing the failed metadata lookup of {page_id} failed: {e}")


def ended(ws: str, doc_id: str, state: str) -> None:
    """An ``ocr`` job of the file ended (``state``, as jobs' ``ended`` hook
    gets it), an automatic one or one a person asked for: a queued marker of
    the file settled — ``done`` for a run that ended done or failed (a file
    too long or unreadable stays so: never tried again, and no failure
    starts the same run over and over), ``declined`` for one a person
    stopped, running or waiting, and still ``queued`` for one that gave way
    or that a delete of its workspace stopped ("refused": the workspace is
    still held then, so ``next`` passes it over; a delete that goes ahead
    takes the marker with it, one called off leaves the file to the next
    sweep's ``resume``) — then the next file's run started (``next``).
    Never raises."""
    if state not in ("yielded", "refused") and marker(ws, doc_id) == QUEUED:
        mark(ws, doc_id, DECLINED if state == "cancelled" else DONE)
    next()


def resume() -> None:
    """The app's round after the jobs' sweep (gamma/app.py ``sweep_jobs``:
    at startup, after ``jobs.recover`` failed the runs a restart cut short,
    then every ``jobs.SWEEP_INTERVAL_S``): the queued files started again (``next``),
    every workspace looked through for them the first time the switch is
    on (``consider`` names the ones queued since). Nothing while the switch
    is off, which an admin turning it on calls this for. Never raises."""
    global _scanned
    try:
        if not enabled():
            return
        with _lock:
            if not _scanned:
                for ws in db.workspace_ids():
                    try:
                        if _queued_in(ws):
                            _waiting.add(ws)
                    except Exception as e:  # noqa: BLE001 — one library that does not read keeps no other waiting
                        log.warning(f"[ocr] reading the automatic recognition's queue of a workspace failed: {e}")
                _scanned = True
        next()
    except Exception:  # noqa: BLE001 — the next sweep tries again
        log.exception("[ocr] resuming the automatic recognitions failed")


def reset() -> None:
    """Forget what this process holds of the queue: the files handed to the
    worker and not yet considered, and the workspaces it knows hold queued
    ones (tests, between two data directories)."""
    global _scanned
    with _cond:
        _queue.clear()
    with _lock:
        _waiting.clear()
        _scanned = False
