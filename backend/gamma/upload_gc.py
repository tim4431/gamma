"""Unreferenced uploads: kept for RETAIN_S, then purged.

A stored file (``uploads/<name>``) is in use while a block names it —
``/api/uploads/<name>`` in its content or properties, or a page's ``doc_id``
(``storage.upload_refs``, the one grammar). When the last reference goes the
file is NOT deleted: its name goes into the workspace's ``upload_orphans``
table (pages.db: ``name``, ``since``) and the file stays on disk, served as
before — so an undo, a cut pasted in a later batch, an AI edit, an ink undo
or a re-attached PDF finds it again. A reference that comes back clears the
row. A file still unreferenced RETAIN_S after both its row's ``since`` and
its own mtime (an upload of the same bytes re-dates it,
``storage._store``) is purged. Nothing here touches the quota: an
unreferenced file counts like any stored one until it goes.

Who notices what:

- An op batch knows the names it dropped and the ones it added
  (``ops._Batch``). The added ones clear their rows inside the batch's own
  transaction (``claim``); the dropped ones go to ``schedule`` — a check
  DEBOUNCE_S later on this module's thread, never on the request.
  ``PUT /blocks/{id}/children`` does both, ``ops.delete_page`` schedules
  the names the page held, ``blocks_store.create_page`` claims its PDF
  (what a trashed copy it replaces held is left to ``reconcile``). A batch
  that drops nothing (typing in a block that keeps its image) costs nothing.
- ``reconcile`` is the whole picture of one workspace in one pass: one scan
  of the blocks that mention an upload, diffed against the uploads
  directory — a row for every unreferenced file past UPLOAD_GRACE_S (an
  upload is stored before the block naming it is written), the rows of
  referenced or vanished files cleared, then the purge. The thread runs it
  for every workspace FIRST_PASS_S after startup and every
  RECONCILE_EVERY_S after: the safety net for writers that bypass the op
  path (imports, a restore, a mirror).
- The purge reads the references again under the write lock, so no batch
  can add one while it deletes, and refuses a database that looks wrong
  (``purge_blocker``): logged as a warning, nothing deleted.

Docs: docs/dev/user_db.md "Stored files".
"""

import re
import threading
import time
from datetime import datetime

from . import config
from .db import connect_pages_db, page_now, ws_dir, ws_uploads_dir
from .logbuf import log
from .storage import upload_refs

RETAIN_S = 30 * 86400         # how long an unreferenced file is kept
UPLOAD_GRACE_S = 15 * 60      # a younger file is never recorded (upload → attach window)
DEBOUNCE_S = 2.0              # a check runs this long after the last batch that dropped a name
FIRST_PASS_S = 60             # the first full pass, after startup
RECONCILE_EVERY_S = 6 * 3600  # the full pass's cadence afterwards
PARTIAL_MAX_AGE_S = 86400     # a temp file of storage.write_atomic older than this is a dead write

# The purge refuses to delete more than PURGE_MAX files at once, or more than
# PURGE_SHARE of the workspace's files when that is over PURGE_FLOOR.
PURGE_MAX = 100
PURGE_FLOOR = 10
PURGE_SHARE = 0.2

_WS_DIR_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # db.safe_ws_id's rule: anything else is no workspace


# --- references ----------------------------------------------------------------------

def referenced(conn) -> set[str]:
    """Every stored-file name the workspace's blocks reference, lowercased
    (compared without case, as the old ``LIKE`` did), in one pass over the
    rows that mention an upload or carry a ``doc_id``."""
    names = set()
    for content, props in conn.execute(
            "SELECT content, properties FROM unified_blocks WHERE instr(content, '/api/uploads/') > 0 "
            "OR instr(properties, '/api/uploads/') > 0 OR instr(properties, '\"doc_id\"') > 0"):
        names.update(n.lower() for n in upload_refs(content or "", props or "{}"))
    return names


def claim(conn, names) -> None:
    """Clear the rows of ``names``: the writer holding ``conn`` just wrote a
    reference to them (inside its own transaction — the caller commits)."""
    if names:
        conn.executemany("DELETE FROM upload_orphans WHERE name = ? COLLATE NOCASE", [(n,) for n in names])


def restart_clocks(conn) -> None:
    """A pages.db put back from a backup carries the rows it had when the
    backup was taken; their clocks start over now, so a restore never makes
    a file due at once (the state it replaced may have used it). Commits."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'upload_orphans'").fetchone():
        conn.execute("UPDATE upload_orphans SET since = ?", (page_now(),))
        conn.commit()


def _stored(ws: str) -> dict:
    """``{name: stat}`` of the workspace's stored files (not ``.partial/``)."""
    uploads = ws_uploads_dir(ws)
    out = {}
    if uploads.is_dir():
        for f in uploads.iterdir():
            if f.name.startswith(".") or not f.is_file():
                continue
            try:
                out[f.name] = f.stat()
            except OSError:
                continue
    return out


def _since_s(since: str, now: float) -> float:
    try:
        return datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return now  # unreadable: the clock starts now


def _sweep_partial(ws: str) -> None:
    """Remove temp files a write that never finished left in ``.partial/``."""
    partial = ws_uploads_dir(ws) / ".partial"
    if not partial.is_dir():
        return
    cutoff = time.time() - PARTIAL_MAX_AGE_S
    for f in partial.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(ws: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(ws, threading.Lock())


def _has_pages_db(ws: str) -> bool:
    # never let a check create the file: a workspace being deleted must stay gone
    return (ws_dir(ws) / "pages.db").is_file()


# --- the check, the full pass, the purge ---------------------------------------------

def check(ws: str, names) -> list[str]:
    """Record the ``names`` a writer dropped that nothing references any
    more — stored files past the grace, not recorded yet. Returns the file
    names recorded."""
    with _lock(ws):
        if not _has_pages_db(ws):
            return []
        files = _stored(ws)
        by_lower = {n.lower(): n for n in files}
        wanted = {by_lower[n.lower()] for n in names if n.lower() in by_lower}
        if not wanted:
            return []
        with connect_pages_db(ws) as conn:
            refs = referenced(conn)
            now = time.time()
            recorded = sorted(n for n in wanted
                              if n.lower() not in refs and now - files[n].st_mtime >= UPLOAD_GRACE_S)
            if recorded:
                stamp = page_now()
                conn.executemany("INSERT OR IGNORE INTO upload_orphans (name, since) VALUES (?, ?)",
                                 [(n, stamp) for n in recorded])
                conn.commit()
    return recorded


def reconcile(ws: str) -> dict:
    """The whole picture of one workspace (see the module doc). Returns
    ``{recorded, cleared, purged, blocked}`` — the file names that got a
    row, the rows cleared, the files deleted, and why a due purge was
    refused ("" when it was not)."""
    out = {"recorded": [], "cleared": [], "purged": [], "blocked": ""}
    with _lock(ws):
        if not _has_pages_db(ws):
            return out
        _sweep_partial(ws)
        with connect_pages_db(ws) as conn:
            refs = referenced(conn)
            files = _stored(ws)
            rows = dict(conn.execute("SELECT name, since FROM upload_orphans").fetchall())
            now = time.time()
            out["cleared"] = sorted(n for n in rows if n not in files or n.lower() in refs)
            out["recorded"] = sorted(n for n, st in files.items()
                                     if n not in rows and n.lower() not in refs
                                     and now - st.st_mtime >= UPLOAD_GRACE_S)
            if out["cleared"] or out["recorded"]:
                stamp = page_now()
                conn.executemany("DELETE FROM upload_orphans WHERE name = ?", [(n,) for n in out["cleared"]])
                conn.executemany("INSERT OR IGNORE INTO upload_orphans (name, since) VALUES (?, ?)",
                                 [(n, stamp) for n in out["recorded"]])
                conn.commit()
            cleared = set(out["cleared"])
            due = sorted(n for n, since in rows.items() if n not in cleared
                         and now - max(_since_s(since, now), files[n].st_mtime) >= RETAIN_S)
            if due:
                out["purged"], out["blocked"] = _purge(ws, conn, due, len(files))
    return out


def purge_blocker(conn, due: int, stored: int) -> str:
    """Why a purge of ``due`` of the workspace's ``stored`` files must not
    run on this database, or "". A pages.db that reads wrong — an
    interrupted restore, a placeholder a sync client left, a file sqlite3
    recreated empty after the real one went missing — must never turn every
    upload of the workspace into an orphan that is then deleted."""
    if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = 'root'").fetchone():
        return "its pages.db has no root row"
    if not conn.execute("SELECT 1 FROM unified_blocks WHERE parent_id = 'root' LIMIT 1").fetchone():
        return "its pages.db has no pages"
    if due > PURGE_MAX or (due > PURGE_FLOOR and due > PURGE_SHARE * stored):
        return f"{due} of its {stored} files at once is more than one purge may delete"
    result = conn.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        return f"its pages.db fails quick_check ({result})"
    return ""


def _purge(ws: str, conn, due: list, stored: int) -> tuple[list, str]:
    """Delete the ``due`` files that are still unreferenced, under the write
    lock. Returns ``(purged names, blocker)``."""
    from .blocks_store import write_lock  # local: blocks_store imports this module

    uploads = ws_uploads_dir(ws)
    write_lock(conn)
    try:
        refs = referenced(conn)  # again: no batch can add a reference while we hold the lock
        now = time.time()
        gone = []
        for name in due:
            try:
                recent = now - (uploads / name).stat().st_mtime < RETAIN_S  # re-dated by an upload meanwhile
            except FileNotFoundError:
                continue
            if name.lower() not in refs and not recent:
                gone.append(name)
        blocker = purge_blocker(conn, len(gone), stored) if gone else ""
        if blocker:
            conn.rollback()
            log.warning(f"[uploads] workspace {ws}: not purging {len(gone)} file(s) unreferenced for "
                        f"{RETAIN_S // 86400} days — {blocker}; they stay on disk")
            return [], blocker
        purged = []
        for name in gone:
            try:
                (uploads / name).unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning(f"[uploads] workspace {ws}: could not purge {name}: {e}")
                continue
            purged.append(name)
        conn.executemany("DELETE FROM upload_orphans WHERE name = ?", [(n,) for n in purged])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if purged:
        log.info(f"[uploads] workspace {ws}: purged {len(purged)} file(s) unreferenced for "
                 f"{RETAIN_S // 86400} days: {purged}")
    return purged, ""


# --- the thread ----------------------------------------------------------------------

_cond = threading.Condition()
_pending: dict[str, list] = {}   # ws -> [monotonic due time, set of dropped names]
_next_full: float | None = None  # when the next full pass is due (None: start() not called)
_thread: threading.Thread | None = None


def schedule(ws: str, names) -> None:
    """Check ``names`` (upload names a writer dropped) DEBOUNCE_S from now,
    together with whatever else the workspace drops meanwhile. Cheap: safe
    to call from a request, even on the event loop."""
    names = set(names or ())
    if not names:
        return
    with _cond:
        entry = _pending.setdefault(ws, [0.0, set()])
        entry[0] = time.monotonic() + DEBOUNCE_S
        entry[1] |= names
        _ensure_thread()
        _cond.notify()


def flush(ws: str | None = None) -> dict:
    """Run the scheduled checks now (of one workspace, or all) instead of
    waiting for the thread: ``{ws: names recorded}``."""
    with _cond:
        batch = [(w, _pending.pop(w)[1]) for w in list(_pending) if ws is None or w == ws]
    return {w: check(w, names) for w, names in batch}


def start() -> None:
    """At app startup: the thread, with its first full pass over every
    workspace FIRST_PASS_S from now — in the background, once the server
    is up, never in the startup path."""
    global _next_full
    with _cond:
        _next_full = time.monotonic() + FIRST_PASS_S
        _ensure_thread()
        _cond.notify()


def _ensure_thread() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_run, name="upload-gc", daemon=True)
        _thread.start()


def _workspaces() -> list[str]:
    root = config.WORKSPACES_DIR
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and _WS_DIR_RE.match(d.name))


def _guarded(what: str, fn, ws: str, *args) -> None:
    # one broken workspace (a damaged pages.db) never stops the others
    try:
        fn(ws, *args)
    except Exception as e:  # noqa: BLE001
        log.error(f"[uploads] {what} of workspace {ws} failed: {e}")


def reconcile_all() -> None:
    """The full pass: ``reconcile`` for every workspace; one that fails is
    logged as an error and the pass goes on."""
    for w in _workspaces():
        _guarded("the upload reconciliation", reconcile, w)


def _run() -> None:
    global _next_full
    while True:
        with _cond:
            while True:
                now = time.monotonic()
                due = [w for w, (at, _) in _pending.items() if at <= now]
                full = _next_full is not None and _next_full <= now
                if due or full:
                    break
                waits = [at for at, _ in _pending.values()] + ([_next_full] if _next_full is not None else [])
                _cond.wait(timeout=max(0.0, min(waits) - now) if waits else None)
            batch = [(w, _pending.pop(w)[1]) for w in due]
            if full:
                _next_full = now + RECONCILE_EVERY_S
        for w, names in batch:
            _guarded("the orphan check", check, w, names)
        if full:
            reconcile_all()
