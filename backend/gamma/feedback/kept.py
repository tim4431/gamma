"""The version of a PDF a comment thread was opened on, kept while the
thread is open (docs/dev/feedback.md "The commented version kept").

A page whose document is read in place (``documents.model.in_place``: a
work folder's build, an Opened file, a vault's PDF) has no stored copy of
its bytes, and the next build at its path takes them away. While such a
page has an open thread it keeps the bytes it shows stored
(``uploads/<hash>.pdf``), so the version the thread was opened on stays
readable: the relocation run places the highlights again from it
(``find_upload_file`` looks in the store before a linked path), and the
feedback reader draws the region as it was commented on
(gamma/feedback/pictures.py).

- At a thread's open (``after_comment``, a ``create``), one daemon worker,
  never the request, stores the page's current bytes from its path
  (``linked_files.store_page_bytes``, hash-checked) and records the source
  line of the place on the root's anchor (``anchor.source``,
  gamma/feedback/sources.py), which the next build's ``.synctex.gz`` no
  longer describes.
- At every replacement while it holds (``keeps_bytes``), the incoming bytes
  are stored first (``vault_sync.LocalRemote.replace_pdf``,
  ``linked_files._take_bytes``): the outgoing ones, stored at the open or at
  the replacement before, are the run's reference and get the
  replacement's short life (``upload_gc.retain``).
- ``repair`` stores what a busy file, the quota or a crash between the
  commit and the worker left unstored (the feedback files' writer and
  ``linked_files.sweep`` call it).
- An open root's ``anchor.doc_id`` is a held reference (``held_files``,
  which ``upload_gc._references`` reads): a resolve releases it
  (``upload_gc.schedule``), a reopen claims it again, and a delete, an
  orphaning or the sweep is left to the full pass.

A highlight made on a build replaced before its thread was opened is placed
by its quote, as any highlight on a file read in place is: nothing is stored
at the first highlight.
"""

import json
import sqlite3
import threading
import time

from fastapi import HTTPException

from .. import comments, storage, upload_gc
from ..blocks_store import load_props, write_lock
from ..db import connect_pages_db
from ..documents.model import SQL_IN_PLACE, file_hash, in_place, is_pdf, page_document
from ..logbuf import log
from ..work_folders.library import work_folder_of
from . import anchors

_waiting: dict = {}             # (ws, page id, thread id) the worker has yet to take, oldest first
_guard = threading.Condition()  # ``_waiting`` and ``_working``; told when the worker stops
_working = False                # the worker is running
_refused: dict = {}             # (ws, page id) → why its bytes were last refused (the quota), logged once a change


def keeps_bytes(ws: str, page_id: str, props: dict | None = None) -> bool:
    """Whether each new version of the page ``page_id``'s document read in
    place is stored as it comes (``props`` its properties, when the caller
    has them): a work folder's file in the library (docs/dev/work_folders.md
    "The round in a work folder"), or a page with an open thread
    (``comments.has_open_thread``)."""
    if props is not None and work_folder_of(props) and props.get("library") is True:
        return True
    with connect_pages_db(ws) as conn:
        if props is None:
            row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            props = load_props(row[0]) if row else {}
            if work_folder_of(props) and props.get("library") is True:
                return True
        return comments.has_open_thread(conn, page_id)


def held_files(conn, pages=None) -> set[str]:
    """The stored files the open threads of the pages.db ``conn`` hold: the
    version each open root (neither resolved nor orphaned, a tombstone with
    replies included) was opened on (``anchors.held_file``), of the pages
    ``pages`` (the pages their blocks are on now) or of the whole
    workspace. What ``upload_gc`` counts as held, never as shown, and what a
    copy of those pages (a backup merge, the owner's Gamma export) carries
    beside their blocks' files."""
    open_roots = "c.id = c.thread_id AND c.resolved_at IS NULL AND c.orphaned_at IS NULL AND c.anchor IS NOT NULL"
    try:
        if pages is None:
            rows = conn.execute(f"SELECT c.anchor FROM comments c WHERE {open_roots}").fetchall()
        else:
            rows = conn.execute(
                "SELECT c.anchor FROM comments c JOIN unified_blocks b ON b.id = c.block_id "
                f"WHERE b.page_id IN (SELECT value FROM json_each(?)) AND {open_roots}",
                (json.dumps(sorted(set(pages))),)).fetchall()
    except sqlite3.OperationalError:  # a database from before the comments' anchor (migration step 54)
        return set()
    return {name for (anchor,) in rows if (name := anchors.held_file(anchor))}


def after_comment(ws: str, event: dict) -> None:
    """The kept version's part of a committed comment write (``event``,
    ``comments.after_write``'s; gamma/feedback/listeners.py hands it over):
    a new thread queues its page for the worker, a resolve releases the
    version its root holds (``upload_gc.schedule``: kept its own short
    life, or RETAIN_S, once nothing else holds it), a reopen claims it
    again (a version purged meanwhile stays gone). Never raises."""
    kind = event.get("kind")
    try:
        if kind == "create":
            _queue((ws, event["page_id"], event["thread_id"]))
        elif kind in ("resolve", "reopen"):
            name = _held_by(ws, event["thread_id"])
            if name and kind == "resolve":
                upload_gc.schedule(ws, [name])
            elif name:
                _claim(ws, name)
    except Exception as e:  # noqa: BLE001: a stored comment never answers an error for this
        log.warning(f"[feedback] the kept version after a {kind} in workspace {ws} failed: {e}")


def repair(ws: str, page_ids) -> int:
    """Store the current bytes of each of the pages ``page_ids`` that has an
    open thread, reads its PDF in place, has no stored copy of it and whose
    file is still the one its row hashed (``storage.linked_path``): what a
    file busy on Windows, the quota or a crash between a thread's commit
    and the worker left unstored. A quota refusal is logged when it
    changes. Returns how many pages' bytes it stored."""
    pages = sorted(set(page_ids))
    if not pages:
        return 0
    with connect_pages_db(ws) as conn:
        held = sorted(comments.open_counts(conn, pages))
        rows = conn.execute(f"SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?)) "
                            f"AND parent_id = 'root' AND {SQL_IN_PLACE}", (json.dumps(held),)).fetchall() if held else []
    stored = 0
    for page_id, raw in rows:
        view = page_document(load_props(raw))
        if (_unstored(ws, view) and storage.linked_path(ws, view["file"]) is not None
                and _store(ws, page_id, view)):
            stored += 1
    return stored


def wait(timeout: float = 10.0) -> bool:
    """Back once the worker has nothing left to do (True), or after
    ``timeout`` seconds (False): for a caller that reads what it stored."""
    deadline = time.monotonic() + timeout
    with _guard:
        while _working or _waiting:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _guard.wait(left)
    return True


def _unstored(ws: str, view) -> bool:
    """Whether the document ``view`` is a PDF read in place with no stored
    copy of its bytes."""
    return is_pdf(view) and in_place(view) and bool(view["file"]) and not storage.exists(ws, view["file"])


def _queue(key: tuple) -> None:
    """``(ws, page id, thread id)`` on the worker's queue (once), the worker
    started when it is not running."""
    global _working
    with _guard:
        _waiting[key] = None
        if _working:
            return
        _working = True
    threading.Thread(target=_work, daemon=True, name="feedback-kept").start()


def _work() -> None:
    global _working
    while True:
        with _guard:
            if not _waiting:
                _working = False
                _guard.notify_all()
                return
            key = next(iter(_waiting))
            del _waiting[key]
        try:
            _keep(*key)
        except Exception:  # noqa: BLE001: one thread's page never stops the others
            log.exception(f"[feedback] keeping the version of page {key[1]} in workspace {key[0]} failed")


def _keep(ws: str, page_id: str, thread_id: str) -> None:
    """The worker's part of a thread's open on the page ``page_id``: its
    bytes stored when it reads them in place, then the place's source
    recorded when the page is a work folder's file and the thread was
    opened on the file it shows (a page read from another server has no
    source here: ``sources.source_for`` answers none)."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (page_id,)).fetchone()
        root = conn.execute("SELECT anchor FROM comments WHERE id = ? AND thread_id = id", (thread_id,)).fetchone()
    if row is None:
        return
    props = load_props(row[0])
    view = page_document(props)
    if _unstored(ws, view):
        _store(ws, page_id, view)
    anchor = anchors.parse(root[0]) if root else None
    if (anchor and work_folder_of(props) and anchor.get("page") and "source" not in anchor
            and anchor.get("doc_id") and anchor["doc_id"] == file_hash(view)):
        _record_source(ws, page_id, props, thread_id, anchor)


def _record_source(ws: str, page_id: str, props: dict, thread_id: str, anchor: dict) -> None:
    """The source of the anchor's place (``sources.source_for``) written
    into the root's stored anchor as ``source``, under the write lock and
    in a commit of its own, while the root still holds that anchor."""
    from . import sources  # local: the SyncTeX reader loads only for a work folder's thread

    place = {"kind": anchor["kind"], "page": anchor["page"], "rects": anchor.get("rects") or []}
    with connect_pages_db(ws) as conn:
        source = sources.source_for(ws, conn, page_id, props, place)
    if not source:
        return
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            row = conn.execute("SELECT anchor FROM comments WHERE id = ? AND thread_id = id", (thread_id,)).fetchone()
            now = anchors.parse(row[0]) if row else None
            if now is not None and now.get("doc_id") == anchor["doc_id"] and "source" not in now:
                conn.execute("UPDATE comments SET anchor = ? WHERE id = ?",
                             (json.dumps({**now, "source": source}), thread_id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _store(ws: str, page_id: str, view: dict) -> bool:
    """The page's current bytes stored from its path
    (``linked_files.store_page_bytes``) and the name claimed: False when no
    path here holds them, when the file there is other bytes by now (that
    version is gone: the build changed under the read) or when the quota
    refuses them (logged when the reason changes; ``repair`` tries again)."""
    from ..folder_links import LinkError
    from ..linked_files import store_page_bytes  # local: linked_files imports this module

    key = (ws, page_id)
    try:
        done = store_page_bytes(ws, page_id, view["file"], view["hash"])
    except LinkError as e:
        log.info(f"[feedback] page {page_id} in workspace {ws}: its version {view['file']} was not stored: {e}")
        return False
    except HTTPException as e:  # the quota (check_upload_allowed)
        why = str(e.detail)
        if _refused.get(key) != why:
            _refused[key] = why
            log.warning(f"[feedback] page {page_id} in workspace {ws}: its version {view['file']} is not stored "
                        f"while its threads are open: {why}")
        return False
    if done:
        if _refused.pop(key, None) is not None:
            log.info(f"[feedback] page {page_id} in workspace {ws}: its version {view['file']} is stored now")
        _claim(ws, view["file"])
    return done


def _held_by(ws: str, thread_id: str) -> str:
    """The stored file the root ``thread_id`` holds while open, "" for none."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT anchor FROM comments WHERE id = ? AND thread_id = id", (thread_id,)).fetchone()
    return anchors.held_file(row[0]) if row else ""


def _claim(ws: str, name: str) -> None:
    """``upload_gc.claim`` of ``name`` in a short write transaction of its own."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            upload_gc.claim(conn, [name])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
