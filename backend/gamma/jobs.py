"""Background jobs: work that outlives the request that started it — exports,
workspace backups and snapshots, restores, library imports, batch metadata
lookups, the search indexer (docs/dev/tasks.md).

A job is a row in users.db (``jobs``) and, while it is queued or running, a
worker thread in this process. The row is what every tab, device and later
visit sees: the kind, who started it and in which workspace, its parameters,
its state and, once it ends, its result, its error or the file it produced
(its *artifact*, ``<data dir>/jobs/<id>/artifact``). A running job's
progress lives in memory and is laid over the row on every read, so a
progress report writes nothing.

Starting one: the endpoint of the work authorizes the request, then calls
``start(kind, owner=..., ws=..., title=..., params=..., run=fn)``. ``fn(job)``
runs in a worker thread with a ``Job`` handle and returns the result (a
JSON value). It reports through ``job.progress(...)``, which raises
``Cancelled`` once the job was stopped, so every report is a place where the
job can stop (``Refused``, which fails it with a reason, once a delete took
the workspace a ``writes`` job changes: ``refuse_writers``);
``stoppable=False`` marks the point after which it no longer can (a restore
swapping the databases). A job that produces a file writes it to
``job.artifact_path`` and names the download with ``job.set_artifact``. An
exception fails the job with its message. What a
run needs that its params cannot hold (a translated export's layout) is
``kept`` with the job (``start(kept=...)``, ``kept(id, name)``) for as long
as the job is listed, however it ended, so the job can be started again.

One server process runs the jobs (the app is one uvicorn process, like the
page sockets). An account runs at most ``MAX_RUNNING_PER_ACCOUNT`` jobs at
once and the server ``MAX_RUNNING``; the rest wait queued, oldest first. A
workspace's own work (owner ``WORKSPACE``, the indexer) runs outside those
limits. A share view's exports are owned by the share (``share_owner``),
which stands for an account in every limit. A finished job is kept
``RETENTION_S``, then ``sweep`` removes it with its file, and the files of
one account are capped together (``ARTIFACT_CAP``). At startup, the jobs a
stopped process left queued or running are failed as interrupted
(``recover``).
"""

import json
import re
import secrets
import shutil
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import config
from .db import connect_users_db, format_stamp, page_now
from .logbuf import log

ACTIVE = ("queued", "running")
WORKSPACE = ""                   # the owner of a workspace's own work (the search indexer)
SHARE_OWNER = "share:"           # + a share token: the owner of the exports a share view starts
MAX_RUNNING = 4                  # account jobs running at once on the server
MAX_RUNNING_PER_ACCOUNT = 2      # one account's; the others wait queued
MAX_ACTIVE_PER_ACCOUNT = 20      # queued + running: past it, starting another is refused
RETENTION_S = 24 * 3600          # a finished job, and its file, is kept this long
ARTIFACT_CAP = 10 << 30          # the finished files of one account, all together
MIN_FREE_BYTES = 1 << 30         # a job that writes a file does not start on a fuller disk
ORPHAN_AGE_S = 3600              # a job directory no row names, this old, is removed
SWEEP_INTERVAL_S = 600
LIST_LIMIT = 100                 # the newest jobs a listing returns
INSTANCE = secrets.token_hex(8)  # this server process, stamped on the jobs it runs
INTERRUPTED = "interrupted: the server restarted while this ran — start it again"

_ID_RE = re.compile(r"^[0-9a-f]{24}$")
KEPT_DIR = "kept"                # <id>/kept/: what start(kept=...) holds for the job's life
_KEPT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_COLUMNS = ("id", "owner", "workspace_id", "kind", "key", "title", "params", "state", "progress", "result",
            "error", "artifact_name", "artifact_type", "artifact_size", "downloaded_at", "instance",
            "created_at", "started_at", "finished_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM jobs"
_FINISHED_SQL = "('done', 'failed', 'cancelled')"


def share_owner(token: str) -> str:
    """The owner of the jobs a share view starts under ``token``
    (``auth.share_job_owner``): the share counts as one account in the
    limits and the 10 GB of files, whoever of its visitors starts them."""
    return SHARE_OWNER + token


class Cancelled(BaseException):
    """Raised in a job's run by ``Job.progress`` once the job was stopped.
    A BaseException, like KeyboardInterrupt, so a run's own ``except
    Exception`` (an import skipping one bad item) never swallows it."""


class Refused(Cancelled):
    """Raised in a job's run by ``Job.progress`` once ``refuse_writers``
    stopped it: the job ends failed, saying why (its message)."""


class Busy(Exception):
    """``start`` found the same work already queued or running — the same
    owner, kind and key. ``job`` is that job."""

    def __init__(self, job: dict):
        super().__init__(f"{job['kind']} job {job['id']} is already {job['state']}")
        self.job = job


def root() -> Path:
    """The jobs' files: ``<id>/`` per job, ``incoming/`` for uploads."""
    return config.DATA_DIR / "jobs"


def _dir(job_id: str) -> Path:
    if not _ID_RE.match(job_id or ""):
        raise ValueError(f"not a job id: {job_id!r}")
    return root() / job_id


def _clear_run_files(job_id: str) -> None:
    """Remove what a run wrote in the job's directory (its file, whole or
    in part), leaving what the job keeps (``KEPT_DIR``)."""
    d = _dir(job_id)
    if not d.is_dir():
        return
    for entry in d.iterdir():
        if entry.name == KEPT_DIR:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    try:
        d.rmdir()  # nothing kept: no directory left behind
    except OSError:
        pass


def kept(job_id: str, name: str) -> bytes | None:
    """What ``start(kept=...)`` kept under ``name`` for this job, or None
    (no such job any more, or nothing kept under that name)."""
    if not _KEPT_NAME_RE.match(name or ""):
        return None
    try:
        return (_dir(job_id) / KEPT_DIR / name).read_bytes()
    except (ValueError, OSError):
        return None


def incoming_path(suffix: str = ".zip") -> Path:
    """A fresh path for an upload a job will read (a restore's zip), written
    before the job exists; the run deletes it, ``sweep`` one left behind."""
    d = root() / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{secrets.token_hex(12)}{suffix}"


class _Live:
    """A job of this process, queued or running. The worker thread writes
    ``progress`` (always a new dict) and ``artifact``; ``state`` and
    ``started_at`` change under ``_lock``."""

    def __init__(self, row: dict, run, ended=None, writes: bool = False):
        self.id = row["id"]
        self.kind = row["kind"]
        self.owner = row["owner"]
        self.ws = row["workspace_id"]
        self.writes = writes
        self.refused = ""  # why refuse_writers stopped it
        self.key = row["key"]
        self.created_at = row["created_at"]
        self.run = run
        self.ended = ended
        self.state = "queued"
        self.started_at = ""
        self.progress: dict = {}
        self.cancel = threading.Event()
        self.stoppable = True
        self.artifact: tuple[str, str] | None = None


class Job:
    """The handle a run gets."""

    def __init__(self, live: _Live):
        self._live = live

    @property
    def artifact_path(self) -> Path:
        """Where the run writes the file it produces (in the job's own
        directory, created here); it becomes the download once the run
        returns (see ``set_artifact``)."""
        path = _dir(self._live.id)
        path.mkdir(parents=True, exist_ok=True)
        return path / "artifact.part"

    def set_artifact(self, name: str, media_type: str = "application/octet-stream") -> None:
        """Name the download of the file at ``artifact_path``."""
        self._live.artifact = (name, media_type)

    def progress(self, done=None, total=None, unit=None, phase=None, item=None, stoppable=None) -> None:
        """Report how far the job got: ``done`` of ``total`` ``unit``s
        (pages, bytes, files, items, papers, workspaces), the ``phase`` it is
        in (a short key the interface words: packing, checking, restoring…)
        and the ``item`` at hand (a page title). A new phase or unit starts
        its own counts; what is not given is kept. ``stoppable=False``: from
        here on the job can no longer be stopped. Raises ``Cancelled`` when
        the job was stopped (and still may be)."""
        live = self._live
        current = live.progress
        if phase is not None and phase != current.get("phase"):
            current = {"phase": phase}
        if unit is not None and unit != current.get("unit"):
            current = {k: current[k] for k in ("phase",) if k in current}
        update = {"done": done, "total": total, "unit": unit,
                  "item": item[:200] if isinstance(item, str) else item}
        live.progress = {**current, **{k: v for k, v in update.items() if v is not None}}
        if stoppable is False:
            # cancel() decides under the lock too: a stop lands before this
            # point or is refused, never accepted and then ignored
            with _lock:
                if live.stoppable and live.cancel.is_set():
                    raise Refused(live.refused) if live.refused else Cancelled()
                live.stoppable = False
        if live.stoppable and live.cancel.is_set():
            raise Refused(live.refused) if live.refused else Cancelled()


def no_progress(**_) -> None:
    """The progress report of a caller that does not watch."""


_lock = threading.RLock()
_live: dict[str, _Live] = {}
_ending: set[str] = set()  # jobs of this process that read as ended, their ``ended`` hook still running


# --- rows ------------------------------------------------------------------------

def _row(values) -> dict:
    return dict(zip(_COLUMNS, values))


def _read(job_id: str) -> dict | None:
    with connect_users_db() as conn:
        found = conn.execute(f"{_SELECT} WHERE id = ?", (job_id,)).fetchone()
    return _row(found) if found else None


def _update(job_id: str, **fields) -> None:
    sets = ", ".join(f"{name} = ?" for name in fields)
    with connect_users_db() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))
        conn.commit()


def _drop(where: str, args: tuple) -> list[str]:
    """Delete the jobs ``where`` selects, with their files; their ids."""
    with connect_users_db() as conn:
        ids = [r[0] for r in conn.execute(f"SELECT id FROM jobs WHERE {where}", args)]
        conn.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    for job_id in ids:
        shutil.rmtree(_dir(job_id), ignore_errors=True)
    return ids


def _public(row: dict, live: _Live | None = None, full: bool = False) -> dict:
    """What the API shows of a job; ``full`` adds its result."""
    state, progress, started = row["state"], json.loads(row["progress"] or "{}"), row["started_at"]
    stoppable, stopping = state in ACTIVE, False
    if live is not None:
        state, progress, started = live.state, dict(live.progress), live.started_at or started
        stopping = live.cancel.is_set()
        stoppable = state in ACTIVE and live.stoppable and not stopping
    job = {
        "id": row["id"], "kind": row["kind"], "owner": row["owner"], "workspace": row["workspace_id"],
        "title": row["title"], "params": json.loads(row["params"] or "{}"), "state": state,
        "progress": progress, "error": row["error"], "created_at": row["created_at"],
        "started_at": started, "finished_at": row["finished_at"],
        "artifact": {"name": row["artifact_name"], "type": row["artifact_type"], "size": row["artifact_size"]}
        if row["artifact_name"] and state == "done" else None,
        "downloaded": bool(row["downloaded_at"]), "stoppable": stoppable, "stopping": stopping,
    }
    if full:
        job["result"] = json.loads(row["result"]) if row["result"] else None
    return job


def get(job_id: str, full: bool = False) -> dict | None:
    """The job as the API shows it (``full``: with its result), or None."""
    if not _ID_RE.match(job_id or ""):
        return None
    live = _live.get(job_id)
    row = _read(job_id)
    return _public(row, live, full) if row else None


def latest(owner: str, kind: str, key: str) -> dict | None:
    """The newest job of this owner, kind and key, whatever its state — so
    asking twice for the same work (a retried request) can answer the first."""
    with connect_users_db() as conn:
        found = conn.execute(f"{_SELECT} WHERE owner = ? AND kind = ? AND key = ? ORDER BY created_at DESC LIMIT 1",
                             (owner, kind, key)).fetchone()
    return _public(_row(found), _live.get(found[0])) if found else None


def for_account(owner: str, ws: str = "") -> list[dict]:
    """The account's jobs, newest first, plus the own jobs of workspace
    ``ws`` (the caller passes a workspace the account is a member of, or
    ""). Without results."""
    with connect_users_db() as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE owner = ? OR (owner = '' AND ? != '' AND workspace_id = ?) "
            "ORDER BY created_at DESC LIMIT ?", (owner, ws, ws, LIST_LIMIT)).fetchall()
    return [_public(_row(r), _live.get(r[0])) for r in rows]


# --- starting and running -------------------------------------------------------------

def start(kind: str, *, owner: str, run, ws: str = "", title: str = "", params: dict | None = None,
          key: str = "", artifact: bool = False, ended=None, kept: dict[str, bytes] | None = None,
          writes: bool = False) -> dict:
    """Queue ``run(job)`` as a job of ``kind`` for ``owner`` (an account's
    id, or ``WORKSPACE`` for a workspace's own work) and return it. ``key``: work
    already queued or running for the same owner, kind and key raises
    ``Busy``. ``artifact``: the job writes a file, so it needs room — 507
    when the disk or the account's share of finished files is full. 429
    when the account already has ``MAX_ACTIVE_PER_ACCOUNT`` jobs going.
    ``ended(state)``: called once a run that started has ended, however it
    ended, when the job already reads as ended (``get``, ``latest``), so
    work it wakes never finds it still running. ``kept``: files (name to
    bytes) the job holds for as long as it is listed, whatever its state —
    what a run needs that ``params`` cannot hold, read back with
    ``kept(id, name)`` by the run or by a later start of the same work.
    They count against the account's share like a finished file (507 past
    it), so a job that fails at once still cannot pile them up. ``writes``:
    the run changes workspace ``ws`` (an import, a restore, a metadata
    lookup), so a delete of it stops the job first (``refuse_writers``)."""
    with _lock:
        if key:
            for other in _live.values():
                if (other.owner, other.kind, other.key) == (owner, kind, key):
                    raise Busy(_public(_read(other.id), other))
        if owner != WORKSPACE and sum(1 for other in _live.values() if other.owner == owner) >= MAX_ACTIVE_PER_ACCOUNT:
            raise HTTPException(status_code=429, detail=(
                f"you already have {MAX_ACTIVE_PER_ACCOUNT} background tasks waiting or running — "
                "let some of them finish first"))
        if artifact or kept:
            _check_room(owner, sum(len(data) for data in (kept or {}).values()))
        row = {"id": secrets.token_hex(12), "owner": owner, "workspace_id": ws or "", "kind": kind,
               "key": key, "title": title[:300], "params": json.dumps(params or {}), "state": "queued",
               "progress": "{}", "instance": INSTANCE, "created_at": page_now()}
        if kept:
            if bad := [name for name in kept if not _KEPT_NAME_RE.match(name)]:
                raise ValueError(f"not a kept file's name: {bad[0]!r}")
            # before the row, so a listed job always has them (a row that
            # fails to go in leaves an orphan directory sweep removes)
            d = _dir(row["id"]) / KEPT_DIR
            d.mkdir(parents=True, exist_ok=True)
            for name, data in kept.items():
                (d / name).write_bytes(data)
        with connect_users_db() as conn:
            conn.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                         tuple(row.values()))
            conn.commit()
        live = _Live(row, run, ended, writes)
        _live[live.id] = live
        _pump()
    return get(live.id)


def _kept_size(job_id: str) -> int:
    """The bytes a job keeps (``KEPT_DIR``), 0 when it keeps nothing."""
    try:
        return sum(entry.stat().st_size for entry in (_dir(job_id) / KEPT_DIR).iterdir() if entry.is_file())
    except (ValueError, OSError):
        return 0


def _check_room(owner: str, adding: int = 0) -> None:
    """507 when the disk is almost full, or when the account's files on the
    server — its finished jobs' files and what all its listed jobs keep,
    a failed or stopped one's too — with ``adding`` more would pass
    ``ARTIFACT_CAP``."""
    root().mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root()).free < MIN_FREE_BYTES:
        raise HTTPException(status_code=507, detail=(
            "the server's disk is almost full, so no export can be written now — ask the server's admin"))
    if owner != WORKSPACE:
        with connect_users_db() as conn:
            rows = conn.execute("SELECT id, CASE WHEN state = 'done' THEN artifact_size ELSE 0 END FROM jobs "
                                "WHERE owner = ?", (owner,)).fetchall()
        held = sum(size + _kept_size(job_id) for job_id, size in rows)
        if held >= ARTIFACT_CAP or (adding and held + adding > ARTIFACT_CAP):
            raise HTTPException(status_code=507, detail=(
                f"your finished exports, and what your background tasks keep to start them again, take "
                f"{held / (1 << 30):.1f} GB on the server — "
                "download them and remove them in Background tasks first"))


def _pump() -> None:
    """Start the queued jobs the limits allow, oldest first (``_lock`` held)."""
    running = [live for live in _live.values() if live.state == "running" and live.owner != WORKSPACE]
    per_owner = Counter(live.owner for live in running)
    total = len(running)
    for live in sorted(_live.values(), key=lambda live: live.created_at):
        if live.state != "queued":
            continue
        if live.owner != WORKSPACE:
            if total >= MAX_RUNNING or per_owner[live.owner] >= MAX_RUNNING_PER_ACCOUNT:
                continue
            total += 1
            per_owner[live.owner] += 1
        live.state = "running"
        live.started_at = page_now()
        threading.Thread(target=_work, args=(live,), daemon=True, name=f"job-{live.kind}-{live.id[:6]}").start()


def error_text(exc: Exception) -> str:
    """An exception's text as it may be said to anyone but the log. What
    fails on a file may have landed on a linked file (gamma/linked_files.py),
    whose path is the person's own: an OSError that names its file says only
    what went wrong, and an error of MuPDF, which puts the path in its
    message, only its class."""
    if isinstance(exc, OSError) and exc.filename:
        return exc.strerror or type(exc).__name__
    if type(exc).__module__.startswith("pymupdf"):
        return type(exc).__name__
    return str(exc)


def _message(live: _Live, exc: Exception) -> str:
    """What a failed job says. An HTTPException's detail and the domain
    errors (ValueError: a bad backup, a refused task; OSError: a full disk)
    are sentences for the person (``error_text``); anything else is a bug,
    logged with its traceback."""
    if isinstance(exc, HTTPException):
        text = str(exc.detail)
    elif isinstance(exc, (ValueError, OSError)):
        text = error_text(exc) or type(exc).__name__
    else:
        log.exception(f"[jobs] {live.kind} {live.id} failed")
        return f"unexpected error ({type(exc).__name__}: {error_text(exc)})"[:500]
    log.warning(f"[jobs] {live.kind} {live.id} failed: {text}")
    return text[:500]


def _work(live: _Live) -> None:
    """The worker thread: run the job, then record how it ended."""
    job = Job(live)
    state, result, encoded, error, artifact = "done", None, None, "", {}
    try:
        _update(live.id, state="running", started_at=live.started_at)
        result = live.run(job)
        encoded = json.dumps(result) if result is not None else None
        if live.artifact:
            part = job.artifact_path
            if not part.is_file():
                raise RuntimeError("the task finished without writing its file")
            final = part.with_name("artifact")
            part.replace(final)
            artifact = {"artifact_name": live.artifact[0], "artifact_type": live.artifact[1],
                        "artifact_size": final.stat().st_size}
    except Refused as exc:
        state, error = "failed", str(exc)[:500]
        log.warning(f"[jobs] {live.kind} {live.id} refused: {exc}")
    except Cancelled:
        state = "cancelled"
    except Exception as exc:  # noqa: BLE001 — every failure ends the job with its message
        state, error = "failed", _message(live, exc)
    finally:
        if state != "done":
            result = encoded = None
            artifact = {}
            _clear_run_files(live.id)
        # The row first, then out of the live set: a read in between still
        # sees the job running, never ended without its result or its file.
        try:
            _update(live.id, state=state, result=encoded, error=error, progress=json.dumps(live.progress),
                    finished_at=page_now(), **artifact)
        except Exception:  # noqa: BLE001 — the bookkeeping below must happen anyway
            log.exception(f"[jobs] could not record how {live.kind} {live.id} ended")
        with _lock:
            live.state = state
            _live.pop(live.id, None)
            if live.ended is not None:
                _ending.add(live.id)
            _pump()
    if live.ended is not None:
        try:
            live.ended(state)
        except Exception:  # noqa: BLE001 — the job's own outcome stands
            log.exception(f"[jobs] {live.kind} {live.id}: its end hook failed")
        finally:
            _ending.discard(live.id)
    # A workspace's own job (the indexer) runs on every search and paper set
    # up: only how it ended otherwise is worth a console line.
    routine = live.owner == WORKSPACE and state == "done"
    (log.debug if routine else log.info)(f"[jobs] {live.kind} {live.id} {state}")


# --- control ------------------------------------------------------------------------------

def cancel(job_id: str) -> dict | None:
    """Stop a job: a queued one ends at once, a running one at its next
    progress report (409 once it can no longer stop). A finished job is
    left as it is. Returns the job."""
    with _lock:
        live = _live.get(job_id)
        if live is not None and live.state == "queued":
            _update(job_id, state="cancelled", finished_at=page_now())  # the row first, as in _work
            live.state = "cancelled"
            _live.pop(job_id, None)
        elif live is not None:
            if not live.stoppable:
                raise HTTPException(status_code=409, detail="this task can no longer be stopped")
            live.cancel.set()
    return get(job_id)


def refuse_writers(ws_ids, why: str, progress=no_progress, poll: float = 0.05) -> list[str]:
    """Stop the jobs of this process that change one of ``ws_ids`` (started
    with ``writes``) and wait until they have ended: what a delete does
    before its final copy, so no import, restore or lookup goes on writing
    into a workspace whose copy is being made. A queued one fails at once,
    a running one at its next progress report (``Refused``), both saying
    ``why``; one past its last stop is waited for, so what it wrote is in
    the copy. ``progress`` (the delete's own report) is called while it
    waits, so the delete can still be stopped. Returns the stopped jobs'
    ids."""
    ws_ids = set(ws_ids)
    with _lock:
        writers = [live for live in _live.values() if live.writes and live.ws in ws_ids]
        for live in writers:
            if live.state == "queued":
                _update(live.id, state="failed", error=why[:500], finished_at=page_now())  # the row first, as in _work
                live.state = "failed"
                _live.pop(live.id, None)
            elif live.stoppable:
                live.refused = why
                live.cancel.set()
    ids = [live.id for live in writers]
    for live in writers:
        log.info(f"[jobs] {live.kind} {live.id}: stopped, {why}")
    while any(job_id in _live or job_id in _ending for job_id in ids):
        progress()
        time.sleep(poll)
    return ids


def dismiss(job_id: str) -> bool:
    """Remove a finished job and its file (409 while it runs)."""
    if job_id in _live:
        raise HTTPException(status_code=409, detail="stop the task before removing it")
    return bool(_drop(f"id = ? AND state IN {_FINISHED_SQL}", (job_id,)))


def clear(owner: str, ws: str = "") -> int:
    """Remove the account's finished jobs, and the finished own jobs of
    workspace ``ws`` (the caller passes one the account may change, or
    ""). Returns how many went."""
    return len(_drop(f"state IN {_FINISHED_SQL} AND (owner = ? OR (owner = '' AND ? != '' AND workspace_id = ?))",
                     (owner, ws, ws)))


def prune(owner: str, kind: str, ws: str, keep: str = "") -> None:
    """Remove the finished jobs of one owner, kind and workspace but
    ``keep`` — a job that stands for its latest run only (the indexer)
    keeps one row."""
    _drop(f"owner = ? AND kind = ? AND workspace_id = ? AND state IN {_FINISHED_SQL} AND id != ?",
          (owner, kind, ws, keep))


def artifact(job_id: str) -> tuple[Path, str, str] | None:
    """``(path, download name, media type)`` of a finished job's file, or
    None when it has none (any more)."""
    row = _read(job_id) if _ID_RE.match(job_id or "") else None
    if not row or row["state"] != "done" or not row["artifact_name"]:
        return None
    path = _dir(job_id) / "artifact"
    return (path, row["artifact_name"], row["artifact_type"]) if path.is_file() else None


def mark_downloaded(job_id: str) -> None:
    """The file was fetched once (the interface stops pointing at it)."""
    with connect_users_db() as conn:
        conn.execute("UPDATE jobs SET downloaded_at = ? WHERE id = ? AND downloaded_at = ''", (page_now(), job_id))
        conn.commit()


def wait(job_id: str, timeout: float = 30.0) -> dict | None:
    """Block until the job is no longer queued or running here and its
    ``ended`` hook has returned (or ``timeout``); the job with its result.
    For tests."""
    deadline = time.monotonic() + timeout
    while (job_id in _live or job_id in _ending) and time.monotonic() < deadline:
        time.sleep(0.02)
    return get(job_id, full=True)


# --- lifecycle ------------------------------------------------------------------------------

def recover() -> int:
    """Fail the jobs a stopped server process left queued or running: no
    process runs them any more. Called once at startup. Returns how many."""
    with connect_users_db() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM jobs WHERE state IN ('queued', 'running') AND instance != ?", (INSTANCE,))]
        conn.executemany("UPDATE jobs SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
                         [(INTERRUPTED, page_now(), i) for i in ids])
        conn.commit()
    for job_id in ids:
        _clear_run_files(job_id)  # what it keeps stays: it can be started again
    if ids:
        log.warning(f"[jobs] {len(ids)} job(s) were interrupted by the last shutdown")
    return len(ids)


def _forget_stopped_shares() -> None:
    """The jobs of a share that no longer exists (stopped, its page or
    folder deleted, its workspace gone) stop and go with their files, as a
    deleted account's do. Its visitors could not reach them any more."""
    with connect_users_db() as conn:
        owners = [r[0] for r in conn.execute(
            "SELECT DISTINCT owner FROM jobs WHERE substr(owner, 1, ?) = ? "
            "AND substr(owner, ?) NOT IN (SELECT token FROM shares)",
            (len(SHARE_OWNER), SHARE_OWNER, len(SHARE_OWNER) + 1))]
    for owner in owners:
        forget_account(owner)


def sweep(now: float | None = None) -> int:
    """Remove the jobs that finished more than ``RETENTION_S`` ago with
    their files, the jobs of shares that are gone, job directories no row
    names, and uploads no job took. Returns how many finished jobs went
    for their age."""
    now = time.time() if now is None else now
    _forget_stopped_shares()
    cutoff = format_stamp(datetime.fromtimestamp(now - RETENTION_S, timezone.utc))
    ids = _drop(f"state IN {_FINISHED_SQL} AND finished_at != '' AND finished_at < ?", (cutoff,))
    with connect_users_db() as conn:
        known = {r[0] for r in conn.execute("SELECT id FROM jobs")}
    d = root()
    if d.is_dir():
        for entry in d.iterdir():
            try:
                age = now - entry.stat().st_mtime
                if entry.name == "incoming":
                    for upload in entry.iterdir():
                        if now - upload.stat().st_mtime > RETENTION_S:
                            upload.unlink(missing_ok=True)
                elif entry.name not in known and entry.name not in _live and age > ORPHAN_AGE_S:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    return len(ids)


def forget_account(user_id: str) -> None:
    """An account is deleted (or a share, ``share_owner``): its jobs stop
    and go, with their files."""
    with _lock:
        for live in list(_live.values()):
            if live.owner != user_id:
                continue
            if live.state == "queued":
                live.state = "cancelled"
                _live.pop(live.id, None)
            else:
                live.cancel.set()
    _drop("owner = ?", (user_id,))
