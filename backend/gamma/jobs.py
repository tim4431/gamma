"""Background jobs: work that outlives the request that started it — exports,
workspace backups and snapshots, restores, library imports, the search
indexer (docs/dev/tasks.md).

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
job can stop; ``stoppable=False`` marks the point after which it no longer
can (a restore swapping the databases). A job that produces a file writes
it to ``job.artifact_path`` and names the download with
``job.set_artifact``. An exception fails the job with its message.

One server process runs the jobs (the app is one uvicorn process, like the
page sockets). An account runs at most ``MAX_RUNNING_PER_ACCOUNT`` jobs at
once and the server ``MAX_RUNNING``; the rest wait queued, oldest first. A
workspace's own work (owner ``WORKSPACE``, the indexer) runs outside those
limits. A finished job is kept ``RETENTION_S``, then ``sweep`` removes it
with its file, and the files of one account are capped together
(``ARTIFACT_CAP``). At startup, the jobs a stopped process left queued or
running are failed as interrupted (``recover``).
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
_COLUMNS = ("id", "owner", "workspace_id", "kind", "key", "title", "params", "state", "progress", "result",
            "error", "artifact_name", "artifact_type", "artifact_size", "downloaded_at", "instance",
            "created_at", "started_at", "finished_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM jobs"
_FINISHED_SQL = "('done', 'failed', 'cancelled')"


class Cancelled(BaseException):
    """Raised in a job's run by ``Job.progress`` once the job was stopped.
    A BaseException, like KeyboardInterrupt, so a run's own ``except
    Exception`` (an import skipping one bad item) never swallows it."""


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

    def __init__(self, row: dict, run):
        self.id = row["id"]
        self.kind = row["kind"]
        self.owner = row["owner"]
        self.key = row["key"]
        self.created_at = row["created_at"]
        self.run = run
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
            live.stoppable = False
        if live.stoppable and live.cancel.is_set():
            raise Cancelled()


def no_progress(**_) -> None:
    """The progress report of a caller that does not watch."""


_lock = threading.RLock()
_live: dict[str, _Live] = {}


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
          key: str = "", artifact: bool = False) -> dict:
    """Queue ``run(job)`` as a job of ``kind`` for ``owner`` (an account, or
    ``WORKSPACE`` for a workspace's own work) and return it. ``key``: work
    already queued or running for the same owner, kind and key raises
    ``Busy``. ``artifact``: the job writes a file, so it needs room — 507
    when the disk or the account's share of finished files is full. 429
    when the account already has ``MAX_ACTIVE_PER_ACCOUNT`` jobs going."""
    with _lock:
        if key:
            for other in _live.values():
                if (other.owner, other.kind, other.key) == (owner, kind, key):
                    raise Busy(_public(_read(other.id), other))
        if owner != WORKSPACE and sum(1 for other in _live.values() if other.owner == owner) >= MAX_ACTIVE_PER_ACCOUNT:
            raise HTTPException(status_code=429, detail=(
                f"you already have {MAX_ACTIVE_PER_ACCOUNT} background tasks waiting or running — "
                "let some of them finish first"))
        if artifact:
            _check_room(owner)
        row = {"id": secrets.token_hex(12), "owner": owner, "workspace_id": ws or "", "kind": kind,
               "key": key, "title": title[:300], "params": json.dumps(params or {}), "state": "queued",
               "progress": "{}", "instance": INSTANCE, "created_at": page_now()}
        with connect_users_db() as conn:
            conn.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                         tuple(row.values()))
            conn.commit()
        live = _Live(row, run)
        _live[live.id] = live
        _pump()
    return get(live.id)


def _check_room(owner: str) -> None:
    root().mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root()).free < MIN_FREE_BYTES:
        raise HTTPException(status_code=507, detail=(
            "the server's disk is almost full, so no export can be written now — ask the server's admin"))
    if owner != WORKSPACE:
        with connect_users_db() as conn:
            held = conn.execute("SELECT COALESCE(SUM(artifact_size), 0) FROM jobs "
                                "WHERE owner = ? AND state = 'done'", (owner,)).fetchone()[0]
        if held >= ARTIFACT_CAP:
            raise HTTPException(status_code=507, detail=(
                f"your finished exports take {held / (1 << 30):.1f} GB on the server — "
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


def _message(live: _Live, exc: Exception) -> str:
    """What a failed job says. An HTTPException's detail and the domain
    errors (ValueError: a bad backup, a refused task; OSError: a full disk)
    are sentences for the person; anything else is a bug, logged with its
    traceback."""
    if isinstance(exc, HTTPException):
        text = str(exc.detail)
    elif isinstance(exc, (ValueError, OSError)):
        text = str(exc) or type(exc).__name__
    else:
        log.exception(f"[jobs] {live.kind} {live.id} failed")
        return f"unexpected error ({type(exc).__name__}: {exc})"[:500]
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
            part = _dir(live.id) / "artifact.part"
            if not part.is_file():
                raise RuntimeError("the task finished without writing its file")
            final = part.with_name("artifact")
            part.replace(final)
            artifact = {"artifact_name": live.artifact[0], "artifact_type": live.artifact[1],
                        "artifact_size": final.stat().st_size}
    except Cancelled:
        state = "cancelled"
    except Exception as exc:  # noqa: BLE001 — every failure ends the job with its message
        state, error = "failed", _message(live, exc)
    finally:
        if state != "done":
            result = encoded = None
            artifact = {}
            shutil.rmtree(root() / live.id, ignore_errors=True)
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
            _pump()
    log.info(f"[jobs] {live.kind} {live.id} {state}")


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
    """Block until the job is no longer queued or running here (or
    ``timeout``); the job with its result. For tests."""
    deadline = time.monotonic() + timeout
    while job_id in _live and time.monotonic() < deadline:
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
        shutil.rmtree(_dir(job_id), ignore_errors=True)
    if ids:
        log.warning(f"[jobs] {len(ids)} job(s) were interrupted by the last shutdown")
    return len(ids)


def sweep(now: float | None = None) -> int:
    """Remove the jobs that finished more than ``RETENTION_S`` ago with
    their files, job directories no row names, and uploads no job took.
    Returns how many jobs went."""
    now = time.time() if now is None else now
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


def forget_account(username: str) -> None:
    """An account is deleted: its jobs stop and go, with their files."""
    with _lock:
        for live in list(_live.values()):
            if live.owner != username:
                continue
            if live.state == "queued":
                live.state = "cancelled"
                _live.pop(live.id, None)
            else:
                live.cancel.set()
    _drop("owner = ?", (username,))


def renamed(old: str, new: str) -> None:
    """An account was renamed (its rows by ``admin.rename_account_rows``):
    the jobs this process runs for it follow."""
    with _lock:
        for live in _live.values():
            if live.owner == old:
                live.owner = new
