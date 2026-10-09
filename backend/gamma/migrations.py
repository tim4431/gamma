"""Versioned upgrades of the data directory.

Two stamps, one number. ``PRAGMA user_version`` of ``users.db`` is the data
directory's schema version (``db.SCHEMA_VERSION`` is what this code
expects); each workspace's ``pages.db`` has its own, the newest step whose
per-workspace part has run on it (``db.workspace_version``: a file stamped
0 is at ``db.WS_VERSION_BASE``). A release that changes stored shapes ships
a numbered step here and bumps the constant; the step is the whole change —
moved files, rebuilt tables, rewritten rows — and ``db.py``'s ``CREATE
TABLE`` statements always describe the CURRENT shape, so nothing is
patched lazily on a read.

A step has a global part (``STEPS``: a function of the users.db
connection, run at startup) and/or, from version 34 on, a workspace part
(``WORKSPACE_STEPS``: a function of one workspace's files, run on each
workspace when it is first opened after the upgrade, ``upgrade_workspace``;
in a background walk after startup, ``warm``; by ``manage.py migrate``; and
on a restored backup's copy). Steps up to the base walk every workspace
themselves (``_each_pages_db``).

The rules that keep this safe and small:

- **Runs before anything else.** ``ensure_current()`` is the first thing
  the server does at startup (and ``python manage.py migrate`` by hand). A
  data directory AHEAD of the binary, or below ``MIN_UPGRADABLE``, is
  refused: the server then serves one page saying what to run instead
  (``guidance()``, gamma/app.py), and an older Gamma never opens files it
  does not understand.
- **Backup first.** Before an upgrade changes anything users.db — and
  every workspace's databases when a pending step walks them — is
  snapshotted with the SQLite backup API into ``backups/<time>-v<N>/``
  (``gamma/backups.py``; uploads are never copied — steps move them, never
  rewrite them). A workspace's own steps copy its two databases into that
  snapshot first (``workspaces/<id>/``). An upgrade that does not finish
  keeps that snapshot named in ``backups/upgrade.json`` and every retry
  reuses it, so a restart loop neither piles up copies nor rotates the
  clean one out. Once an upgrade finishes, only the newest
  ``backups.KEEP_BACKUPS`` automatic snapshots are kept; hand-made ones are
  never pruned.
- **One step, one stamp.** Steps run in order; the version is stamped after
  each one (in users.db for a global part, in the pages.db for a workspace
  part), so an interrupted upgrade resumes at the step that did not
  finish. Every step is written to be re-runnable (it checks what it is
  about to do).
- **Nothing piles up.** A step is kept only while ``MIN_UPGRADABLE`` is
  below it. Raising ``MIN_UPGRADABLE`` (a release or two later) deletes the
  older steps; a data directory that old must first run a release that
  still has them (the refusal says which). Content normalization of
  per-workspace files (``gamma/normalize.py``) is not a step: it also runs
  on backup restore, because a backup can be older than any step.

Docs: docs/dev/migrations.md.
"""

import json
import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path

from . import backups, config
from .blocks_store import FOLDERS, folder_by_path
from .db import (BUSY_TIMEOUT_S, SCHEMA_VERSION, USER_PREF_KEYS, USERS_SCHEMA, WORKSPACE_PREFS_SCHEMA, WS_VERSION_BASE,
                 connect_pages_db, new_account_id, page_now, register_functions, safe_ws_id, users_db_version,
                 workspace_ids, workspace_version, ws_dir)
from .logbuf import log
from .normalize import (block_columns, block_fts, folder_blocks, highlight_shape, normalize_data_db, page_changes,
                        page_ops_batch_id, pages_db_chats)

# The lowest version this release upgrades from: a data directory at it
# has had the steps up to it, and this release carries the steps after it
# (docs/dev/migrations.md "Nothing piles up"). One below it must first run
# UPGRADE_VIA, the newest release that still carries the deleted steps.
MIN_UPGRADABLE = 19
UPGRADE_VIA = {
    "image": "ghcr.io/tim4431/gamma:sha-8708ebb",  # the server image built from that release
    "release": "the Gamma release of 2026-10-01",
    "schema": 24,                                   # what it brings a data directory to
}


class MigrationError(RuntimeError):
    """The data directory cannot be brought to SCHEMA_VERSION by this
    process: a step failed (``step``, ``snapshot``), or one of the two
    refusals below. With ``workspace``, one workspace's own steps failed as
    it opened (``upgrade_workspace``): that workspace is not served, every
    other one is. ``guidance()`` turns any of them into what the person
    should do."""

    step = ""
    snapshot = ""
    version: int | None = None
    workspace = ""


class NewerDataError(MigrationError):
    """The data directory was written by a newer Gamma."""


class TooOldDataError(MigrationError):
    """The data directory predates MIN_UPGRADABLE."""


# --- inspection ---------------------------------------------------------------

def data_version() -> int | None:
    """The data directory's schema version; None when it has no users.db yet
    (a fresh install — ``db.connect_users_db`` creates it at SCHEMA_VERSION)."""
    if not config.USERS_DB.exists():
        return None
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        has_users = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        if not has_users:
            return None
        return users_db_version(conn)


def pending_steps(version: int) -> list:
    """The global steps a data directory at ``version`` has still to run."""
    return [(v, name, fn) for v, name, fn in STEPS if v > version]


def workspace_steps_after(version: int) -> list:
    """The workspace steps a pages.db at ``version`` has still to run."""
    return [(v, name, fn) for v, name, fn in WORKSPACE_STEPS if v > version]


def _newest_step() -> int:
    """The version the steps lead to (SCHEMA_VERSION, as the bump rule
    keeps it)."""
    return max(v for v, _, _ in STEPS + WORKSPACE_STEPS)


def status() -> dict:
    """``{"version", "target", "fresh", "pending": [{"version", "name"}],
    "workspace_steps": [{"version", "name"}], "backups": [...]}`` for the
    CLI and the startup line: the global steps still to run, and every
    workspace step a workspace can be behind on. Cheap: it opens no
    workspace (``workspaces_behind`` counts those, for the CLI)."""
    version = data_version()
    fresh = version is None
    pending = [] if fresh else pending_steps(version)
    return {
        "version": SCHEMA_VERSION if fresh else version,
        "target": SCHEMA_VERSION,
        "fresh": fresh,
        "pending": [{"version": v, "name": name} for v, name, _ in pending],
        "workspace_steps": [{"version": v, "name": name} for v, name, _ in workspace_steps_after(WS_VERSION_BASE)],
        "backups": [b["name"] for b in backups.list_backups()],
    }


# --- the runner ---------------------------------------------------------------

def ensure_current(dry_run: bool = False) -> dict:
    """Bring the data directory to SCHEMA_VERSION: the global steps, then
    users.db stamped. The workspace steps this upgrade brings run on each
    workspace later (``upgrade_workspace``). Returns ``{"from", "to",
    "applied": [names], "workspace_steps": [names], "backup": path |
    None}``. Raises ``NewerDataError`` / ``TooOldDataError`` (refuse to
    run) or ``MigrationError`` (a step failed — the version stays at the
    last completed step; fix or restore the backup and rerun)."""
    version = data_version()
    if version is None:
        return {"from": SCHEMA_VERSION, "to": SCHEMA_VERSION, "applied": [], "workspace_steps": [], "backup": None}
    if version > SCHEMA_VERSION:
        raise _refusal(NewerDataError, version,
                       f"the data directory ({config.DATA_DIR}) is at schema version {version}, newer than "
                       f"this Gamma (version {SCHEMA_VERSION}). Run the Gamma release that wrote it, or "
                       f"restore the matching snapshot from {config.BACKUPS_DIR}.")
    if version < MIN_UPGRADABLE:
        raise _refusal(TooOldDataError, version,
                       f"the data directory is at schema version {version}; this release upgrades from "
                       f"{MIN_UPGRADABLE} at the earliest. Run {UPGRADE_VIA['release']} "
                       f"({UPGRADE_VIA['image']}) once on the same data directory first: it brings it "
                       f"to schema version {UPGRADE_VIA['schema']}.")
    pending = pending_steps(version)
    result = {"from": version, "to": SCHEMA_VERSION, "applied": [],
              "workspace_steps": [name for _, name, _ in workspace_steps_after(version)], "backup": None}
    if dry_run:
        return result
    newest = _newest_step()
    if version >= newest:
        if backups.unfinished_upgrade():  # stamped its last step, stopped before tidying up
            _finish_upgrade()
        return result
    # The workspaces' files are copied up front only when a pending step
    # walks them all (the steps up to the base); a workspace step copies its
    # own workspace's into the same snapshot when it runs.
    result["backup"] = _snapshot_before(version, workspaces=any(v <= WS_VERSION_BASE for v, _, _ in pending))
    log.info(f"[migrate] upgrading data directory from schema version {version} to "
             f"{SCHEMA_VERSION}; snapshot in {result['backup']}")
    for v, name, fn in pending:
        try:
            with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
                fn(conn)
                conn.execute(f"PRAGMA user_version = {v}")
                conn.commit()
        except Exception as e:
            failed = MigrationError(
                f"migration step {v} ({name}) failed: {e}. The data directory is at the last "
                f"completed step; fix the cause and rerun `manage.py migrate`, or restore "
                f"{result['backup']}.")
            failed.step, failed.snapshot, failed.version = f"{v} ({name})", result["backup"], data_version()
            raise failed from e
        result["applied"].append(name)
        log.info(f"[migrate] step {v} ({name}) done")
    if data_version() < newest:  # the newest steps have a workspace part only
        with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
            conn.execute(f"PRAGMA user_version = {newest}")
            conn.commit()
    _finish_upgrade()
    if result["workspace_steps"]:
        log.info(f"[migrate] workspace steps {', '.join(result['workspace_steps'])} run on each workspace as it "
                 f"is opened, and in the background")
    return result


def _refusal(kind, version: int, message: str) -> MigrationError:
    error = kind(message)
    error.version = version
    return error


def guidance(error: MigrationError) -> dict:
    """What the person running this server should do about ``error``, as
    ``{"title", "status", "summary", "steps": [str], "fallback": {"lead",
    "steps": [str]}, "data_dir", "backups_dir", "snapshot"}`` — the one
    text the startup page, the API's 503, the CLI and the log share
    (gamma/app.py ``_blocked_app``, ``manage.py migrate``). ``status`` is
    the one calm line that comes first (the data is intact, the other
    workspaces are served); ``steps`` is the way forward, which for data a
    newer Gamma wrote is to update this one; ``fallback`` is the other way
    (staying on this release, going back), folded away on the page and
    empty when there is none. Nothing below changes the data directory."""
    data_dir, backups_dir = str(config.DATA_DIR), str(config.BACKUPS_DIR)
    intact = "Your data is intact. Nothing has been changed."
    none = {"lead": "", "steps": []}
    if isinstance(error, TooOldDataError):
        via = UPGRADE_VIA
        return {
            "title": "This Gamma needs an earlier release to upgrade your data first",
            "status": intact,
            "summary": (f"Your data directory is at schema version {error.version}; this Gamma (schema "
                        f"{SCHEMA_VERSION}) upgrades from version {MIN_UPGRADABLE} on, and {via['release']} "
                        f"carries the earlier steps. Run it once on the same data directory: it upgrades it to "
                        f"schema version {via['schema']}, taking a snapshot of the databases first; then start "
                        f"this version again and it finishes the upgrade."),
            "steps": [
                f"Back up the data directory ({data_dir}): a plain copy of the folder or volume is enough.",
                f"Docker Compose: in docker-compose.yml set `image: {via['image']}`, run `docker compose up -d`, "
                f"wait for the log line \"data directory upgraded\" (`docker logs gamma`), then put the image "
                f"back and run `docker compose up -d` again.",
                f"Docker without Compose: `docker run --rm -v <your data volume>:/data {via['image']}`, wait for "
                f"the same log line, stop it with Ctrl+C, then start your usual container.",
                "Desktop app: install that release from the GitHub releases page, open it once with this data "
                "directory, then install the current version again.",
            ],
            "fallback": none,
            "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": "",
        }
    if isinstance(error, NewerDataError):
        return {
            "title": "This data was last opened by a newer Gamma",
            "status": intact,
            "summary": (f"Your data directory is at schema version {error.version} and this Gamma reads version "
                        f"{SCHEMA_VERSION}, so it is standing aside rather than guessing at the newer layout. "
                        f"Updating this Gamma is all it takes: the newer release opens the data as it is."),
            "steps": [
                "Docker: `docker compose pull && docker compose up -d` (or the image tag that wrote the data), "
                "then reload this page.",
                "Desktop app: install the latest release from https://github.com/tim4431/gamma/releases and "
                "open it again.",
                "Several Gammas sharing one data directory: update them all, or give each its own directory.",
            ],
            "fallback": {
                "lead": "Would you rather stay on this release?",
                "steps": [
                    f"The newer Gamma took a snapshot before it upgraded, in {backups_dir}. With the server "
                    f"stopped, `manage.py backups` lists them and `manage.py backups --restore <name>` puts "
                    f"one back; anything saved after that snapshot is not in it.",
                ],
            },
            "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": "",
        }
    if error.workspace:
        kept = (f"A copy of its databases from before the upgrade is in {error.snapshot}, under "
                f"workspaces/{error.workspace}/." if error.snapshot else
                "No copy of its databases could be taken, so nothing in it was changed.")
        return {
            "title": "One workspace could not be upgraded yet",
            "status": "Every other workspace is served.",
            "summary": (f"Workspace {error.workspace} stopped at migration step {error.step}; it stays at schema "
                        f"version {error.version}, the last step that completed, and is not served until the "
                        f"step succeeds. The upgrade is tried again the next time the workspace is opened."),
            "steps": [
                f"Read the cause in the server log: {error}",
                "Fix it (disk space, file permissions, a damaged database) and open the workspace again, or run "
                "`manage.py migrate` with the server stopped: the upgrade continues where it stopped.",
                kept,
            ],
            "fallback": none,
            "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": error.snapshot,
        }
    back = (f"With the server stopped, `manage.py backups --restore {Path(error.snapshot).name}` restores the "
            f"snapshot from before the upgrade; then run the previous release." if error.snapshot else
            "Go back to the previous release with the snapshot `manage.py backups` lists.")
    return {
        "title": "The upgrade paused before it finished",
        "status": "A snapshot from before the upgrade is kept.",
        "summary": (f"Migration step {error.step} failed; the data directory is at schema version "
                    f"{error.version}, the last step that completed. The upgrade resumes from this step at "
                    f"the next start, with the same snapshot."),
        "steps": [
            f"Read the cause in the server log: {error}",
            "Fix it (disk space, file permissions, a damaged database) and start the server again: the "
            "upgrade continues where it stopped.",
        ],
        "fallback": {"lead": "Would you rather go back?", "steps": [back]},
        "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": error.snapshot,
    }


def _snapshot_before(version: int, workspaces: bool) -> str:
    """The snapshot this upgrade rolls back to: users.db, and every
    workspace's databases when ``workspaces`` (a pending step walks them).
    An earlier attempt that did not finish (a failed step, a crash — and a
    restart loop retrying it) took one before it changed anything, and that
    one is reused: retries neither pile up copies nor push the clean one
    out. A snapshot that cannot be written stops the upgrade before any
    step runs."""
    unfinished = backups.unfinished_upgrade()
    if unfinished:
        kept = backups.info(unfinished.get("backup") or "")
        if kept:
            log.info(f"[migrate] resuming the upgrade that started at schema version "
                     f"{unfinished.get('from')}; its snapshot {kept['name']} is kept")
            return kept["path"]
    try:
        taken = backups.create(f"v{version}", auto=True, workspaces=workspaces)
        backups.mark_upgrade({"from": version, "to": SCHEMA_VERSION, "backup": taken["name"],
                              "started_at": page_now()})
    except Exception as e:
        raise MigrationError(
            f"could not snapshot the data directory before upgrading it: {e}. Nothing was "
            f"changed; free disk space (or fix the cause) and start again.") from e
    return taken["path"]


def _finish_upgrade() -> None:
    """The upgrade is done: forget its marker, then keep only the newest
    automatic snapshots (a failure to prune never stops the server)."""
    backups.clear_upgrade()
    try:
        backups.prune_backups()
    except OSError as e:
        log.warning(f"[migrate] could not prune old pre-upgrade snapshots: {e}")


# --- per workspace --------------------------------------------------------------
# A workspace's steps (WORKSPACE_STEPS) run when its pages.db is first
# opened behind them (db._open_ws_db calls upgrade_workspace), in the
# background walk after startup (``warming``), by ``manage.py migrate``
# (``upgrade_workspaces``) and on a restored backup's copy
# (ws_backup._normalize_copies, ``run_workspace_steps``).

WARM_PAUSE_MAX_S = 1.0  # the background walk rests as long as its last workspace took, at most this

_locks_guard = threading.Lock()
_ws_locks: dict[str, threading.Lock] = {}  # per workspace: one thread runs its steps, the others wait
_snapshot_lock = threading.Lock()
_snapshot: tuple = ()  # (backups dir, name, the data version it holds): where the workspaces' copies go


def _workspace_lock(ws: str) -> threading.Lock:
    with _locks_guard:
        return _ws_locks.setdefault(ws, threading.Lock())


def upgrade_workspace(conn) -> None:
    """Run the workspace steps a pages.db is behind on, as a connection
    opens it (db._open_ws_db: the SQL functions registered, the schema
    statements not applied yet). Under this process's lock for the
    workspace, so a second thread opening it waits here rather than on
    SQLite's busy timeout, and finds the version current when it reads it
    again. A file with no tables yet (the connection created it) is
    stamped current: the schema statements give it the current shape.
    Otherwise the workspace's two databases are copied into the upgrade's
    snapshot first (``_snapshot_workspace``), then the steps run
    (``run_workspace_steps``). Raises MigrationError when the copy cannot
    be written or a step fails: the workspace is not served (a request gets
    a 503 with ``guidance``), every other one is, and the next open tries
    again."""
    ws = conn.ws
    with _workspace_lock(ws):
        stamp = workspace_version(conn)
        pending = workspace_steps_after(stamp)
        if not pending:
            return
        if not conn.execute("SELECT 1 FROM sqlite_master").fetchone():
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        started = time.monotonic()
        snapshot = _snapshot_workspace(ws, stamp, pending)
        data_db = Path(conn._path).with_name("data.db")
        with (closing(sqlite3.connect(str(data_db), timeout=BUSY_TIMEOUT_S)) if data_db.is_file()
              else nullcontext()) as data:
            run_workspace_steps(ws, conn, data, pending, snapshot=snapshot)
        log.debug(f"[migrate] workspace {ws}: steps {', '.join(str(v) for v, _, _ in pending)} done in "
                  f"{time.monotonic() - started:.2f} s; its files from before are in {snapshot}")


def run_workspace_steps(ws: str, pages, data, steps, *, snapshot: str = "") -> None:
    """``steps`` (``workspace_steps_after`` the file's version) on one
    workspace's files, in order: each in one transaction on ``pages``, taken
    with ``BEGIN IMMEDIATE`` (the workspace's write lock), which also
    writes its stamp; ``data`` (the workspace's data.db, None when there is
    none) has a transaction of its own, committed just before. A step does
    not commit. ``ws`` is '' for a backup's copy. The version is read again
    under the lock, so a step another process ran meanwhile is not run
    twice. Raises MigrationError naming the step that failed, both its
    transactions rolled back: the version stays at the last completed
    step."""
    for v, name, fn in steps:
        try:
            pages.execute("BEGIN IMMEDIATE")
            if workspace_version(pages) >= v:
                pages.rollback()
                continue
            if data is not None:
                data.execute("BEGIN")
            fn(ws, pages, data)
            if data is not None:
                data.commit()
            pages.execute(f"PRAGMA user_version = {v}")
            pages.commit()
        except Exception as e:
            pages.rollback()
            if data is not None:
                data.rollback()
            where = f"workspace {ws}" if ws else "a backup's copy"
            failed = MigrationError(
                f"migration step {v} ({name}) failed on {where}: {e}. It is at the last completed step; "
                f"fix the cause and open it again (or run `manage.py migrate`).")
            failed.step, failed.snapshot, failed.workspace = f"{v} ({name})", snapshot, ws
            failed.version = workspace_version(pages)
            raise failed from e


def _snapshot_workspace(ws: str, stamp: int, pending: list) -> str:
    """Copy the workspace's databases into the upgrade's snapshot before
    its steps run (``backups.add_workspace``: one it holds already keeps
    its first copy); returns the snapshot's path. Raises MigrationError
    when the copy cannot be written: nothing was changed then."""
    try:
        return backups.add_workspace(_upgrade_snapshot(stamp), ws)
    except Exception as e:
        failed = MigrationError(f"could not snapshot workspace {ws} before upgrading it: {e}. Nothing was "
                                f"changed; free disk space (or fix the cause) and open it again.")
        failed.step, failed.version, failed.workspace = f"{pending[0][0]} ({pending[0][1]})", stamp, ws
        raise failed from e


def _upgrade_snapshot(stamp: int) -> str:
    """The snapshot a workspace at ``stamp`` is copied into: the newest
    automatic one (the snapshot of the latest upgrade), once it holds a
    data directory at least that new (its ``schema_version``) — restoring
    it then gives users.db and the workspace's files of one time. When
    there is none (deleted, or a workspace copied in behind a current data
    directory), a new one of users.db alone is taken. Looked up once per
    process."""
    global _snapshot
    with _snapshot_lock:
        where = str(config.BACKUPS_DIR)
        if (_snapshot and _snapshot[0] == where and _snapshot[2] >= stamp
                and backups.backup_path(_snapshot[1]).is_dir()):
            return _snapshot[1]
        newest = backups.latest_auto()
        if newest is None or (newest.get("schema_version") or 0) < stamp:
            newest = backups.create(f"v{data_version()}", auto=True, workspaces=False)
        _snapshot = (where, newest["name"], newest.get("schema_version") or 0)
        return newest["name"]


def is_behind(ws: str) -> bool:
    """Whether the workspace's pages.db has a workspace step still to run,
    read from its stamp without opening it for real. False at once,
    opening nothing, while no workspace step is above WS_VERSION_BASE. A
    file that cannot be read counts as behind: opening it says what is
    wrong."""
    if not workspace_steps_after(WS_VERSION_BASE):
        return False
    path = ws_dir(ws) / "pages.db"
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S)) as conn:
            return bool(workspace_steps_after(workspace_version(conn)))
    except sqlite3.Error:
        return True


def workspaces_behind() -> list[str]:
    """Every workspace ``is_behind`` (``manage.py migrate --status``: one
    read of every workspace's pages.db, so never on the startup path)."""
    if not workspace_steps_after(WS_VERSION_BASE):
        return []
    return [ws for ws in workspace_ids() if is_behind(ws)]


def upgrade_workspaces(stop: threading.Event | None = None) -> dict:
    """Open every workspace that is behind (``is_behind``), past the
    connection cache, so its steps run (``upgrade_workspace``); returns
    ``{"upgraded": [ids], "failed": {id: error}}``. A workspace that fails
    is logged and the walk goes on. ``stop`` (the background walk's) makes
    it rest after each workspace as long as that one took, at most
    WARM_PAUSE_MAX_S, so requests keep most of the disk, and end early once
    set."""
    done = {"upgraded": [], "failed": {}}
    for ws in workspace_ids():
        if stop is not None and stop.is_set():
            break
        if not is_behind(ws):
            continue
        started = time.monotonic()
        try:
            connect_pages_db(ws).close()
        except Exception as e:  # noqa: BLE001 — one workspace never stops the walk
            done["failed"][ws] = str(e)
            log.error(f"[migrate] workspace {ws} could not be upgraded, the others go on: {e}")
        else:
            done["upgraded"].append(ws)
        if stop is not None:
            stop.wait(min(time.monotonic() - started, WARM_PAUSE_MAX_S))
    return done


def warm(stop: threading.Event | None = None) -> dict:
    """The background walk after startup: ``upgrade_workspaces``, resting
    between workspaces, so most are upgraded before anyone opens them (the
    first request to one that is not does it inline). Returns at once,
    opening nothing, while no workspace step is above WS_VERSION_BASE."""
    if not workspace_steps_after(WS_VERSION_BASE):
        return {"upgraded": [], "failed": {}}
    done = upgrade_workspaces(stop or threading.Event())
    if done["upgraded"] or done["failed"]:
        log.info(f"[migrate] background walk: {len(done['upgraded'])} workspace(s) upgraded, "
                 f"{len(done['failed'])} failed")
    return done


def _warm_thread(stop: threading.Event) -> None:
    try:
        warm(stop)
    except Exception:  # noqa: BLE001 — a daemon thread's error goes to the log
        log.exception("[migrate] the background walk failed")


@contextmanager
def warming():
    """While the app runs (its lifespan): ``warm`` in a daemon thread,
    started only when a workspace step is above WS_VERSION_BASE, and told
    to stop at shutdown (it finishes the workspace it is on; one cut off by
    the exit rolls back and runs again at the next open)."""
    stop = threading.Event()
    if workspace_steps_after(WS_VERSION_BASE):
        threading.Thread(target=_warm_thread, args=(stop,), name="migrate-warm", daemon=True).start()
    try:
        yield
    finally:
        stop.set()


# --- steps --------------------------------------------------------------------
# A global step (STEPS) gets an open users.db connection (autocommit off)
# and must leave the database consistent when it returns; the runner stamps
# the version. From version 34 on it keeps the previous release able to
# read users.db (columns and tables are added; a rename or a drop waits a
# release), and it never touches a workspace's files: that is a workspace
# step (WORKSPACE_STEPS), ``fn(ws, pages, data)`` on one workspace's
# pages.db and data.db connections (data None when there is no data.db; ws
# '' on a backup's copy), inside the runner's transaction (it does not
# commit). A workspace step is also what a restored backup goes through,
# so it is re-runnable and finds a table it changes missing on an old copy
# (the schema statements, applied after it, create that one).

def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# users.db as schema version 24 shaped it, frozen: steps 20-24 create their
# tables from this, never from db.USERS_SCHEMA, which step 25 moved on (an
# account is named by its id there).
_V24_USERS_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        provider_id TEXT NOT NULL DEFAULT '',
        provider_name TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input INTEGER NOT NULL DEFAULT 0,
        output INTEGER NOT NULL DEFAULT 0,
        cache_read INTEGER NOT NULL DEFAULT 0,
        cache_write INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ai_usage_user_at ON ai_usage (username, at)",
    """CREATE TABLE IF NOT EXISTS mcp_oauth (
        kind TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        value TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        PRIMARY KEY (kind, key_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS integration_tokens (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        scope TEXT NOT NULL DEFAULT 'read'
    )""",
    """CREATE TABLE IF NOT EXISTS mirrors (
        workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
        remote_url TEXT NOT NULL,
        remote_ws TEXT NOT NULL,
        remote_name TEXT NOT NULL DEFAULT '',
        token TEXT NOT NULL,
        owner TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'two-way',
        remote_cursor TEXT NOT NULL DEFAULT '',
        local_cursor TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        poll_s INTEGER NOT NULL DEFAULT 30,
        on_change INTEGER NOT NULL DEFAULT 1,
        page_filter TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS publisher_sessions (
        username TEXT NOT NULL,
        host TEXT NOT NULL,
        encrypted TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (username, host)
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        is_guest INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        max_upload_mb INTEGER,
        quota_mb INTEGER,
        default_workspace TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS identities (
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        username TEXT NOT NULL REFERENCES users(username),
        email TEXT NOT NULL DEFAULT '',
        claims TEXT NOT NULL DEFAULT '{}',
        refresh_token TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL,
        revoked_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (provider, subject)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS identities_account ON identities(provider, username)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL REFERENCES users(username),
        guest_date TEXT,
        created_at TEXT NOT NULL,
        via TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'personal',
        access TEXT NOT NULL DEFAULT 'private',
        public_role TEXT NOT NULL DEFAULT 'viewer',
        quota_mb INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS workspace_members (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        username TEXT NOT NULL REFERENCES users(username),
        role TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, username)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(username)",
    """CREATE TABLE IF NOT EXISTS pending_memberships (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        subject TEXT NOT NULL,
        username TEXT NOT NULL,
        role TEXT NOT NULL,
        invited_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, subject)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pending_subject ON pending_memberships(subject)",
    """CREATE TABLE IF NOT EXISTS shares (
        token TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        page_id TEXT NOT NULL DEFAULT '',
        folder TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        audience TEXT NOT NULL DEFAULT 'anyone',
        role TEXT NOT NULL DEFAULT 'view',
        allowed_users TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_page ON shares(workspace_id, page_id) WHERE page_id != ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_folder ON shares(workspace_id, folder) WHERE folder != ''",
    """CREATE TABLE IF NOT EXISTS user_prefs (
        username TEXT NOT NULL,
        workspace_id TEXT NOT NULL DEFAULT '',
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (username, workspace_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        workspace_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        key TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        params TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL,
        progress TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        error TEXT NOT NULL DEFAULT '',
        artifact_name TEXT NOT NULL DEFAULT '',
        artifact_type TEXT NOT NULL DEFAULT '',
        artifact_size INTEGER NOT NULL DEFAULT 0,
        downloaded_at TEXT NOT NULL DEFAULT '',
        instance TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT '',
        finished_at TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON jobs(workspace_id, owner)",
]

# pages.db as schema version 25 shaped it, frozen: the statements steps 20-25
# found a workspace's file with (``_each_pages_db`` applies them), never
# db.PAGES_SCHEMA, whose block table step 26 moved on (its typed hot fields)
# and whose tombstones step 27 folded into the change log. A step after 27
# that walks the workspaces applies none of them (``schema=()``: its files
# are in the shape step 27 left, and these would give every file its
# ``deleted_pages`` back) or a frozen copy of its own time.
_V25_PAGES_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS unified_blocks (
        id TEXT PRIMARY KEY,
        parent_id TEXT REFERENCES unified_blocks(id),
        position TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        properties TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ub_parent ON unified_blocks(parent_id, position)",
    """CREATE TABLE IF NOT EXISTS deleted_pages (
        page_id TEXT PRIMARY KEY,
        deleted_at TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS sync_pages (
        page_id TEXT PRIMARY KEY,
        remote_seq INTEGER NOT NULL DEFAULT 0,
        base TEXT NOT NULL DEFAULT '{}',
        synced_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        page_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        stats TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS sync_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id TEXT NOT NULL,
        block_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        mine TEXT NOT NULL DEFAULT '',
        theirs TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT '',
        base TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS upload_orphans (
        name TEXT PRIMARY KEY,
        since TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS page_ops (
        page_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        client TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        ops TEXT NOT NULL,
        PRIMARY KEY (page_id, seq)
    ) WITHOUT ROWID""",
]


def _each_pages_db(step: str, fn, schema=_V25_PAGES_SCHEMA) -> None:
    """``fn(conn)`` on every workspace's pages.db, the ``schema`` statements
    applied first (``_V25_PAGES_SCHEMA`` unless the step says otherwise) and
    the SQL functions registered (``db.register_functions``), then the
    file's own version raised to the step's (``step`` is ``"<version>
    (<name>)"``), so a file the steps up to the base walked carries a
    stamp. A file that fails — a damaged database — is logged as an error
    and skipped: one broken library must not keep every other account's
    server from starting. That workspace stays as it was for an admin to
    restore; a restore from the step's snapshot needs the step run again on
    that file. Steps from version 34 on never walk the workspaces: their
    workspace part is a WORKSPACE_STEPS function."""
    if not config.WORKSPACES_DIR.is_dir():
        return
    version = int(step.split(" ", 1)[0])
    for ws_root in sorted(config.WORKSPACES_DIR.iterdir()):
        pages_db = ws_root / "pages.db"
        if not ws_root.is_dir() or not pages_db.is_file() or ws_root.name.startswith("."):
            continue  # (a dot-name: a deleted workspace's leftover, workspaces.remove_leftovers)
        try:
            with closing(sqlite3.connect(str(pages_db))) as pdb:
                register_functions(pdb)
                for stmt in schema:
                    pdb.execute(stmt)
                fn(pdb)
                if pdb.execute("PRAGMA user_version").fetchone()[0] < version:
                    pdb.execute(f"PRAGMA user_version = {version}")
                pdb.commit()
        except sqlite3.Error as e:
            log.error(f"[migrate] step {step}: workspace {ws_root.name} skipped, its pages.db failed: {e}")


def _v20_guest_accounts(conn: sqlite3.Connection) -> None:
    """Guests became throwaway accounts minted per visitor (gamma/guests.py,
    docs/dev/guests.md): the legacy shared ``guest`` account goes with its
    sessions, memberships, prefs and personal workspace (rows, directory and
    stored snapshots). Any other ``is_guest`` row — ``create-user`` without a
    password used to make one — becomes a normal password-less account, so
    the new guest expiry never deletes it."""
    legacy = conn.execute("SELECT default_workspace FROM users WHERE username = 'guest' AND is_guest = 1").fetchone()
    if legacy:
        ws_ids = {r[0] for r in conn.execute(
            "SELECT w.id FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id "
            "WHERE m.username = 'guest' AND w.kind = 'personal'")}
        if legacy[0]:
            ws_ids.add(legacy[0])
        for ws in sorted(ws_ids):
            try:
                safe_ws_id(ws)
            except ValueError:
                continue
            for path in (config.WORKSPACES_DIR / ws, config.BACKUPS_DIR / "workspaces" / ws):
                if path.is_dir():
                    try:
                        shutil.rmtree(str(path))
                    except OSError as e:  # a leftover directory is listed to admins as an orphan
                        log.warning(f"[migrate] could not remove the guest workspace directory {path}: {e}")
            for table, column in (("integration_tokens", "workspace_id"), ("workspace_members", "workspace_id"),
                                  ("pending_memberships", "workspace_id"), ("shares", "workspace_id"),
                                  ("user_prefs", "workspace_id"), ("mirrors", "workspace_id"),
                                  ("workspaces", "id")):
                conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (ws,))
        for table in ("sessions", "identities", "integration_tokens", "publisher_sessions", "user_prefs",
                      "workspace_members", "ai_usage"):
            conn.execute(f"DELETE FROM {table} WHERE username = 'guest'")
        conn.execute("DELETE FROM users WHERE username = 'guest' AND is_guest = 1")
        log.info("[migrate] removed the legacy shared guest account and its workspace")
    conn.execute("UPDATE users SET is_guest = 0 WHERE is_guest = 1")
    conn.commit()


def _v21_folder_shares(conn: sqlite3.Connection) -> None:
    """``shares`` gains ``folder``: a share names a page (``page_id``) or a
    folder-label path (``folder``, the pages filed there or below it), the
    other column ''. The page unique index becomes partial and a folder
    twin joins it (docs/dev/api.md "Shares")."""
    if "folder" not in _columns(conn, "shares"):
        conn.execute("ALTER TABLE shares ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
    conn.execute("DROP INDEX IF EXISTS idx_shares_page")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_page ON shares(workspace_id, page_id) WHERE page_id != ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_folder ON shares(workspace_id, folder) WHERE folder != ''")
    conn.commit()


def _v22_upload_orphans(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db gains ``upload_orphans`` (``name``,
    ``since``): stored files nothing references any more are kept 30 days
    before they are purged, instead of being deleted with their last
    reference (gamma/upload_gc.py). The table starts empty — the old sweep
    left no unreferenced file older than its 15-minute grace; the first
    background pass after startup records whatever there is."""
    _each_pages_db("22 (upload_orphans)", lambda pdb: pdb.commit())


def _v23_page_trash(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db gains the reserved ``trash`` row beside
    ``root``: the parent of the pages in Recently deleted (gamma/trash.py),
    written now so no block can take the id before the first page is
    deleted. A workspace where a block already holds it is named in the log
    and left as it is (moving a page to the trash then refuses)."""
    def add_trash(pdb):
        row = pdb.execute("SELECT parent_id FROM unified_blocks WHERE id = 'trash'").fetchone()
        if row is None:
            now = page_now()
            pdb.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                        "updated_at) VALUES ('trash', NULL, 'a1', '', '{}', ?, ?)", (now, now))
            pdb.commit()
        elif row[0] is not None:
            log.warning("[migrate] step 23 (page_trash): a block holds the reserved id 'trash' in "
                        f"{pdb.execute('PRAGMA database_list').fetchone()[2]}; that workspace has no trash")

    _each_pages_db("23 (page_trash)", add_trash)


def _v24_jobs(conn: sqlite3.Connection) -> None:
    """Adds the ``jobs`` table (+ its owner and workspace indexes) in
    users.db: background jobs — exports, backups, restores, imports, the
    search indexer — with their progress, result and produced file
    (gamma/jobs.py). Nothing else changes."""
    for stmt in _V24_USERS_SCHEMA:
        if stmt.startswith(("CREATE TABLE IF NOT EXISTS jobs ", "CREATE INDEX IF NOT EXISTS idx_jobs_")):
            conn.execute(stmt)
    conn.commit()


# The op-log labels of writers that are no account, frozen: a share link's
# visitor (gamma/auth.py LINK_ACTOR_PREFIX) and a mirror's round
# (gamma/sync_engine.py ACTOR). Step 25 keeps them as they are.
_V25_LINK_PREFIX = "link:"
_V25_MIRROR_ACTOR = "mirror"
# The users.db tables whose ``username`` column becomes ``user_id``.
_V25_RENAMED = ("sessions", "identities", "integration_tokens", "publisher_sessions", "workspace_members",
                "user_prefs", "ai_usage")
# The users.db columns that keep their name and hold the id from step 25 on.
_V25_PEOPLE = (("workspaces", "created_by"), ("workspace_members", "added_by"),
               ("pending_memberships", "invited_by"), ("mirrors", "owner"), ("jobs", "owner"))
# The users.db tables step 25 rebuilds or creates, as schema versions 25-30
# shaped them, frozen: step 25 builds them from this, never from
# db.USERS_SCHEMA, whose ``sessions`` step 31 moved on (``guest_date`` went).
# The tables step 25 leaves standing (mirrors, workspaces, jobs, ...) are not
# here: it only rewrites their rows.
_V25_USERS_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        provider_id TEXT NOT NULL DEFAULT '',
        provider_name TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input INTEGER NOT NULL DEFAULT 0,
        output INTEGER NOT NULL DEFAULT 0,
        cache_read INTEGER NOT NULL DEFAULT 0,
        cache_write INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ai_usage_user_at ON ai_usage (user_id, at)",
    """CREATE TABLE IF NOT EXISTS integration_tokens (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        user_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        scope TEXT NOT NULL DEFAULT 'read'
    )""",
    """CREATE TABLE IF NOT EXISTS publisher_sessions (
        user_id TEXT NOT NULL,
        host TEXT NOT NULL,
        encrypted TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, host)
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT NOT NULL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        is_guest INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        max_upload_mb INTEGER,
        quota_mb INTEGER,
        default_workspace TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS identities (
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id),
        email TEXT NOT NULL DEFAULT '',
        claims TEXT NOT NULL DEFAULT '{}',
        refresh_token TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL,
        revoked_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (provider, subject)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS identities_account ON identities(provider, user_id)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        guest_date TEXT,
        created_at TEXT NOT NULL,
        via TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS workspace_members (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        user_id TEXT NOT NULL REFERENCES users(id),
        role TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, user_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(user_id)",
    """CREATE TABLE IF NOT EXISTS shares (
        token TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        page_id TEXT NOT NULL DEFAULT '',
        folder TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        audience TEXT NOT NULL DEFAULT 'anyone',
        role TEXT NOT NULL DEFAULT 'view',
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_page ON shares(workspace_id, page_id) WHERE page_id != ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_folder ON shares(workspace_id, folder) WHERE folder != ''",
    """CREATE TABLE IF NOT EXISTS share_users (
        token TEXT NOT NULL,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        PRIMARY KEY (token, user_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_prefs (
        user_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL DEFAULT '',
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, workspace_id, key)
    )""",
]
# mcp_oauth records that name an account (an assistant's consent and code, a
# cloud sign-in that links): minutes-long, dropped rather than rewritten.
_V25_NAMED_OAUTH = ("consent", "code", "cloud_login")


def _v25_account_ids(conn: sqlite3.Connection) -> None:
    """Accounts are keyed by a stable id: ``users`` gains ``id`` (a random
    token, the primary key) beside a unique ``username``, and every column
    that named an account holds the id — ``username`` columns become
    ``user_id`` (rows of no account dropped), ``created_by`` / ``added_by``
    / ``invited_by`` / ``owner`` keep their names (a name no account has
    becomes ''; a job of one goes). ``shares.allowed_users`` becomes
    ``share_users`` rows (names no account has dropped). The publisher
    sessions are sealed under the id. Then every workspace's op log,
    tombstones and trashed pages' ``deleted_by``, and the backup task files,
    name the id. users.db changes as one transaction; the rest is rewritten
    where it still names an account, so a rerun finishes what a crash
    left."""
    if "id" not in _columns(conn, "users"):
        conn.execute("BEGIN")
        _v25_users_db(conn)
        conn.commit()
    ids = dict(conn.execute("SELECT username, id FROM users").fetchall())
    known = set(ids.values())
    _each_pages_db("25 (account_ids)", lambda pdb: _v25_actors(pdb, ids, known))
    tasks = config.BACKUPS_DIR / "tasks"
    for path in sorted(tasks.glob("*.json")) if tasks.is_dir() else []:
        task = json.loads(path.read_text(encoding="utf-8"))
        owner = task.get("owner") or ""
        if owner and owner not in known:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({**task, "owner": ids.get(owner, "")}), encoding="utf-8")
            temp.replace(path)


def _rebuild_table(conn, schema: list, table: str, columns: str, select: str) -> None:
    """Recreate ``table`` as the ``schema`` statements shape it, with its
    indexes, from the rows ``select`` reads out of the old one (into
    ``columns``). ``schema`` is the list of the step's time: a frozen one
    for a step a later step moved past, db.USERS_SCHEMA for the newest."""
    create = next(s for s in schema if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} ("))
    conn.execute(f"DROP TABLE IF EXISTS {table}_new")
    conn.execute(create.replace(f"IF NOT EXISTS {table} (", f"{table}_new (", 1))
    conn.execute(f"INSERT INTO {table}_new ({columns}) {select}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    for stmt in schema:
        if re.search(rf" ON {table} ?\(", stmt):
            conn.execute(stmt)


def _v25_users_db(conn) -> None:
    """Step 25's users.db half (one transaction, the caller's)."""
    conn.execute("ALTER TABLE users ADD COLUMN id TEXT")
    for name, in conn.execute("SELECT username FROM users").fetchall():
        conn.execute("UPDATE users SET id = ? WHERE username = ?", (new_account_id(), name))
    cols = _columns(conn, "users")
    _rebuild_table(conn, _V25_USERS_SCHEMA, "users", ", ".join(cols), f"SELECT {', '.join(cols)} FROM users")
    for table in _V25_RENAMED:
        cols = _columns(conn, table)
        new = ", ".join("user_id" if c == "username" else c for c in cols)
        old = ", ".join("u.id" if c == "username" else f"t.{c}" for c in cols)
        _rebuild_table(conn, _V25_USERS_SCHEMA, table, new,
                       f"SELECT {old} FROM {table} t JOIN users u ON u.username = t.username")
    conn.execute("DELETE FROM jobs WHERE owner != '' AND owner NOT IN (SELECT username FROM users)")
    for table, column in _V25_PEOPLE:
        conn.execute(f"UPDATE {table} SET {column} = "
                     f"COALESCE((SELECT u.id FROM users u WHERE u.username = {table}.{column}), '')")
    conn.execute(next(s for s in _V25_USERS_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS share_users (")))
    for token, allowed in conn.execute("SELECT token, allowed_users FROM shares WHERE allowed_users != ''").fetchall():
        for item in allowed.split(","):
            name, _, role = item.strip().partition(":")
            conn.execute("INSERT OR IGNORE INTO share_users (token, user_id, role) "
                         "SELECT ?, id, ? FROM users WHERE username = ?",
                         (token, role if role in ("view", "edit") else "view", name))
    cols = [c for c in _columns(conn, "shares") if c != "allowed_users"]
    old = ", ".join("COALESCE(u.id, '')" if c == "created_by" else f"s.{c}" for c in cols)
    _rebuild_table(conn, _V25_USERS_SCHEMA, "shares", ", ".join(cols),
                   f"SELECT {old} FROM shares s LEFT JOIN users u ON u.username = s.created_by")
    conn.execute(f"DELETE FROM mcp_oauth WHERE kind IN ({', '.join('?' * len(_V25_NAMED_OAUTH))})", _V25_NAMED_OAUTH)
    _v25_reseal_publisher_sessions(conn)


def _v25_reseal_publisher_sessions(conn) -> None:
    """A publisher snapshot is sealed with the account it belongs to
    (gamma/publisher_sessions.py checks it on use): seal each under the id.
    One that no longer opens could never be used again and goes."""
    rows = conn.execute("SELECT user_id, host, encrypted FROM publisher_sessions").fetchall()
    if not rows:
        return
    from cryptography.fernet import InvalidToken

    from .publisher_sessions import cipher  # local: the key file is only needed here

    box = cipher()
    for user_id, host, encrypted in rows:
        try:
            payload = json.loads(box.decrypt(encrypted.encode("ascii")))
        except (InvalidToken, ValueError):
            conn.execute("DELETE FROM publisher_sessions WHERE user_id = ? AND host = ?", (user_id, host))
            continue
        payload["user"] = user_id
        conn.execute("UPDATE publisher_sessions SET encrypted = ? WHERE user_id = ? AND host = ?",
                     (box.encrypt(json.dumps(payload).encode()).decode("ascii"), user_id, host))


def _v25_actor(actor: str, ids: dict, known: set) -> str:
    """A writer as step 25 records it: an account's name becomes its id; an
    id, a label of a writer that is no account and '' stay; a name no
    account has becomes ''."""
    if not actor or actor in known or actor.startswith(_V25_LINK_PREFIX) or actor == _V25_MIRROR_ACTOR:
        return actor
    return ids.get(actor, "")


def _v25_actors(pdb, ids: dict, known: set) -> None:
    """One workspace's op log, tombstones and trashed pages name the id."""
    for table in ("page_ops", "deleted_pages"):
        for actor, in pdb.execute(f"SELECT DISTINCT actor FROM {table}").fetchall():
            new = _v25_actor(actor, ids, known)
            if new != actor:
                pdb.execute(f"UPDATE {table} SET actor = ? WHERE actor = ?", (new, actor))
    for page_id, props in pdb.execute(
            "SELECT id, properties FROM unified_blocks WHERE parent_id = 'trash'").fetchall():
        try:
            data = json.loads(props or "{}")
        except ValueError:
            continue
        by = data.get("deleted_by")
        new = _v25_actor(by, ids, known) if isinstance(by, str) else by
        if new != by:
            pdb.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?",
                        (json.dumps({**data, "deleted_by": new}), page_id))
    pdb.commit()


def _v26_block_columns(conn: sqlite3.Connection) -> None:
    """Every workspace's block table gains its typed hot fields, indexed:
    ``page_id`` (stored, filled in by the parent walk — the page a row lives
    under, '' on the reserved rows) and the generated ``kind`` and
    ``doc_id`` (gamma/normalize.py ``block_columns``, which a restored older
    backup goes through as well). One transaction per file; a file that has
    them is left as it is."""
    _each_pages_db("26 (block_columns)", block_columns)


def _v27_page_changes(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db gains its change log, ``page_changes``
    (gamma/normalize.py ``page_changes``, which a restored older backup goes
    through as well): a row per page with seqs in the order the pages were
    last written — live in the library, deleted in Recently deleted — then
    one per ``deleted_pages`` tombstone, which is dropped. One transaction
    per file. A mirror's cursors into the old time-ordered feeds mean
    nothing in the log: they start over (``''``), and the next round walks
    both feeds whole, finding nothing to do for a page that did not move."""
    _each_pages_db("27 (page_changes)", page_changes)
    conn.execute("UPDATE mirrors SET remote_cursor = '', local_cursor = '' "
                 "WHERE remote_cursor != '' OR local_cursor != ''")
    conn.commit()


def _v28_chats_and_notes_index(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db takes what its data.db held that is not
    derived, and the notes index: the AI chats move in (gamma/normalize.py
    ``pages_db_chats`` — ``chats.block_id`` becomes ``bucket``), the notes
    index is created there and built from the blocks (``block_fts``: the
    view, the FTS5 table, the triggers), and data.db drops its own notes
    index and bookkeeping (``normalize_data_db``). A restored older backup
    goes through the same. The files are at step 27's shape: no frozen
    statements first. Re-running finds the chats moved and builds the
    index again."""
    _each_pages_db("28 (chats_and_notes_index)", _v28_workspace, schema=())


def _v28_workspace(pdb: sqlite3.Connection) -> None:
    data_db = Path(pdb.execute("PRAGMA database_list").fetchone()[2]).with_name("data.db")
    pages_db_chats(pdb, data_db)
    block_fts(pdb)
    if data_db.is_file():
        with closing(sqlite3.connect(str(data_db))) as ddb:
            normalize_data_db(ddb)


def _v29_folder_blocks(conn: sqlite3.Connection) -> None:
    """Folders and labels become blocks (docs/dev/home_library.md). Every
    workspace's pages.db: the ``kind`` column gains its ``folder`` /
    ``label`` cases (``block_columns``), and the folder and label trees are
    built from the paths and names in use, the pages' filing and the folder
    chats rewritten to their ids (``folder_blocks``; a restored older backup
    goes through both). Then in users.db, per workspace: a folder share
    names its folder's id (one whose folder is gone is deleted), and each
    account's ``pinned-folders`` pref becomes ``pinned`` on those folders —
    the newest pin of any member, as the folder is one block for all of
    them — and is dropped. Re-runnable: a converted file is left as it is,
    and the users.db half resolves the paths against the trees it finds."""
    _each_pages_db("29 (folder_blocks)", lambda pdb: _v29_workspace(conn, pdb), schema=())
    conn.execute("DELETE FROM user_prefs WHERE key = 'pinned-folders'")
    conn.commit()


def _v29_workspace(conn: sqlite3.Connection, pdb: sqlite3.Connection) -> None:
    block_columns(pdb)
    folder_blocks(pdb)
    ws = Path(pdb.execute("PRAGMA database_list").fetchone()[2]).parent.name
    folders = {r[0] for r in pdb.execute("SELECT id FROM unified_blocks WHERE page_id = ?", (FOLDERS,))}

    def folder_of(value: str) -> str:
        if value in folders:
            return value
        found = folder_by_path(pdb, [s for s in value.split("/") if s.strip()])
        return found[0] if found else ""

    pins: dict[str, str] = {}
    for (raw,) in conn.execute("SELECT value FROM user_prefs WHERE key = 'pinned-folders' AND workspace_id = ?",
                               (ws,)).fetchall():
        try:
            listed = json.loads(raw)
        except ValueError:
            continue
        for pin in listed if isinstance(listed, list) else []:
            folder_id = folder_of(str(pin.get("path") or "")) if isinstance(pin, dict) else ""
            if folder_id:
                pins[folder_id] = max(pins.get(folder_id, ""), str(pin.get("at") or "") or page_now())
    for folder_id, at in pins.items():
        props = json.loads(pdb.execute("SELECT properties FROM unified_blocks WHERE id = ?", (folder_id,)).fetchone()[0])
        if str(props.get("pinned") or "") < at:
            pdb.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?",
                        (json.dumps({**props, "pinned": at}), folder_id))
    pdb.commit()
    for token, value in conn.execute("SELECT token, folder FROM shares WHERE workspace_id = ? AND folder != ''",
                                     (ws,)).fetchall():
        folder_id = folder_of(value)
        # One share per folder (idx_shares_folder): a second path that
        # resolves to the same folder (a case variant) would keep its path
        # and never open, so it goes like a share whose folder is gone.
        if folder_id and folder_id != value:
            folder_id = "" if conn.execute("UPDATE OR IGNORE shares SET folder = ? WHERE token = ?",
                                           (folder_id, token)).rowcount == 0 else folder_id
        if not folder_id:
            conn.execute("DELETE FROM share_users WHERE token = ?", (token,))
            conn.execute("DELETE FROM shares WHERE token = ?", (token,))
    conn.commit()


def _v30_highlight_shape(conn: sqlite3.Connection) -> None:
    """The highlight shape (gamma/highlights.py). Every workspace's
    pages.db: the ``kind`` column's ``highlight`` case reads
    ``pdf_position`` (``block_columns``), and the blocks take the shape
    (``highlight_shape``: the block id is the highlight's id, a position
    keeps the page size once, ``pdf_page`` goes but on text boxes, links
    to a highlight name its block, a page's PDF URL is derived from its
    ``doc_id``). Nothing is stamped or touched: a shape is no edit, and
    a mirror and its remote upgraded apart rewrite their copies of a page
    alike. users.db is untouched. Re-runnable: a converted file is left as
    it is; a restored older backup goes through the same."""
    _each_pages_db("30 (highlight_shape)", _v30_workspace, schema=())


def _v30_workspace(pdb: sqlite3.Connection) -> None:
    block_columns(pdb)
    highlight_shape(pdb)


def _v31_session_columns(conn: sqlite3.Connection) -> None:
    """``sessions`` loses ``guest_date``, a column the guest login wrote and
    nothing read: the table is rebuilt in its db.USERS_SCHEMA shape with its
    rows, so every session stays signed in. This is the newest step, so it
    builds from the live statements; a step that moves ``sessions`` on
    again freezes them. A table without the column is left as it is."""
    cols = [c for c in _columns(conn, "sessions") if c != "guest_date"]
    if len(cols) < len(_columns(conn, "sessions")):
        _rebuild_table(conn, USERS_SCHEMA, "sessions", ", ".join(cols), f"SELECT {', '.join(cols)} FROM sessions")
    conn.commit()


def _v32_share_token_workspace(conn: sqlite3.Connection) -> None:
    """A share token carries its workspace, ``<workspace id>.<secret>``
    (``db.share_token_workspace``), so a router can place share traffic by
    the prefix without a lookup. Every token of ``shares`` without a dot
    (the bare secret every share had until now) gains its share's workspace
    id and a dot in front, and the ``share_users`` rows keyed by it follow,
    in one transaction. Links sent out before this step stop opening.
    Re-runnable: a token with a dot is left as it is."""
    conn.execute("UPDATE share_users SET token = (SELECT s.workspace_id || '.' || s.token FROM shares s "
                 "WHERE s.token = share_users.token) "
                 "WHERE instr(token, '.') = 0 AND token IN (SELECT token FROM shares)")
    conn.execute("UPDATE shares SET token = workspace_id || '.' || token WHERE instr(token, '.') = 0")
    conn.commit()


def _v33_page_ops_batch_id(conn: sqlite3.Connection) -> None:
    """Every workspace's op log keeps the client's name for each batch
    (gamma/ops.py ``apply_ops``): ``page_ops`` gains ``batch_id`` and
    ``cursor`` ('' on the rows logged before) and the unique index on page,
    client and batch id (gamma/normalize.py ``page_ops_batch_id``, which a
    restored older backup goes through as well), so a retried batch is
    answered from its row across restarts instead of from memory. No frozen
    statements first (``schema=()``); users.db is untouched. Re-runnable: a
    file that has them is left as it is."""
    _each_pages_db("33 (page_ops_batch_id)", page_ops_batch_id, schema=())


def _v34_workspace_prefs(ws: str, pages: sqlite3.Connection, data) -> None:
    """The prefs that name a workspace's pages (open tabs, recents, reading
    positions: every key but ``db.USER_PREF_KEYS``) move into the workspace.
    Its pages.db gains ``workspace_prefs`` (db.WORKSPACE_PREFS_SCHEMA), and
    the rows users.db ``user_prefs`` holds under the workspace's id are
    copied in through a read connection of the step's own; a row the
    workspace has at the same or a later time is kept, so a second run
    changes nothing and never undoes a later write. A backup's copy (``ws``
    '') has nothing to copy: its table starts empty. users.db is not
    written: the release before reads its rows there."""
    pages.execute(WORKSPACE_PREFS_SCHEMA)
    if not ws or not config.USERS_DB.is_file():
        return
    with closing(sqlite3.connect(str(config.USERS_DB), timeout=BUSY_TIMEOUT_S)) as users:
        if not users.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'user_prefs'").fetchone():
            return
        account_wide = sorted(USER_PREF_KEYS)
        rows = users.execute(
            "SELECT user_id, key, value, updated_at FROM user_prefs WHERE workspace_id = ? "
            f"AND key NOT IN ({', '.join('?' * len(account_wide))})", (ws, *account_wide)).fetchall()
    pages.executemany(
        "INSERT INTO workspace_prefs (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at "
        "WHERE excluded.updated_at > workspace_prefs.updated_at", rows)



# users.db ``folder_links`` as schema version 35 shaped it, frozen: step 35
# creates it from this, and step 36 brings a table release 0.2.16 shaped
# otherwise to it. ``workspace_id`` is a workspace of this server or, with
# ``remote_url``, of the other server, so it is no foreign key.
_V35_FOLDER_LINKS = """CREATE TABLE IF NOT EXISTS folder_links (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        folder_id TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE COLLATE NOCASE,
        notes INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        cursor TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '{}',
        remote_url TEXT NOT NULL DEFAULT '',
        token TEXT NOT NULL DEFAULT '',
        token_id TEXT NOT NULL DEFAULT ''
    )"""


def _v35_folder_links(conn: sqlite3.Connection) -> None:
    """users.db gains ``folder_links`` (gamma/folder_links.py): the folders
    kept as directories on disk, with the change-log seq and the status of
    their last round, and for a folder of another Gamma server that
    server's address and a token of it (``remote_url``, ``token``,
    ``token_id``), in the frozen shape ``_V35_FOLDER_LINKS``. Re-runnable:
    the statement is ``IF NOT EXISTS``. No workspace is touched."""
    conn.execute(_V35_FOLDER_LINKS)
    conn.commit()


# The remote source's columns, which release 0.2.16's folder_links lacked.
_V36_COLUMNS = (("remote_url", "TEXT NOT NULL DEFAULT ''"), ("token", "TEXT NOT NULL DEFAULT ''"),
                ("token_id", "TEXT NOT NULL DEFAULT ''"))


def _v36_folder_links_remote(conn: sqlite3.Connection) -> None:
    """``folder_links`` in the shape ``_V35_FOLDER_LINKS`` wherever an
    earlier build shaped it otherwise. Release 0.2.16 created the table
    with ``workspace_id`` a foreign key to ``workspaces`` and without the
    remote source's columns, and added the columns as its own step 36, so
    its data is stamped 36 already; the release after folded the columns
    into step 35, dropped the foreign key (a link to another server's
    workspace cannot satisfy it) and, by mistake, the version with it,
    which made that data "newer" than the code. This step is what makes
    36 current again: a 0.2.16 directory is simply current (its foreign
    key stays, inert: no connection turns ``foreign_keys`` on), and a
    directory at 35 gets the table created if missing, each missing
    column added, and a table that has the foreign key rebuilt without
    it, rows kept. Re-runnable. No workspace is touched."""
    conn.execute(_V35_FOLDER_LINKS)
    have = _columns(conn, "folder_links")
    for name, decl in _V36_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE folder_links ADD COLUMN {name} {decl}")
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'folder_links'").fetchone()[0]
    if "REFERENCES" in ddl:
        cols = ", ".join(_columns(conn, "folder_links"))
        conn.execute("ALTER TABLE folder_links RENAME TO folder_links_v35")
        conn.execute(_V35_FOLDER_LINKS)
        conn.execute(f"INSERT INTO folder_links ({cols}) SELECT {cols} FROM folder_links_v35")
        conn.execute("DROP TABLE folder_links_v35")
    conn.commit()


STEPS = [
    (20, "guest_accounts", _v20_guest_accounts),
    (21, "folder_shares", _v21_folder_shares),
    (22, "upload_orphans", _v22_upload_orphans),
    (23, "page_trash", _v23_page_trash),
    (24, "jobs", _v24_jobs),
    (25, "account_ids", _v25_account_ids),
    (26, "block_columns", _v26_block_columns),
    (27, "page_changes", _v27_page_changes),
    (28, "chats_and_notes_index", _v28_chats_and_notes_index),
    (29, "folder_blocks", _v29_folder_blocks),
    (30, "highlight_shape", _v30_highlight_shape),
    (31, "session_columns", _v31_session_columns),
    (32, "share_token_workspace", _v32_share_token_workspace),
    (33, "page_ops_batch_id", _v33_page_ops_batch_id),
    (35, "folder_links", _v35_folder_links),
    (36, "folder_links_remote", _v36_folder_links_remote),
]

# The workspace parts of the steps from version 34 on: (version, name,
# fn(ws, pages, data)), in order; a step with a global part too has its
# entry in STEPS under the same version and name.
WORKSPACE_STEPS = [
    # Next release: a global step deletes the users.db user_prefs rows whose workspace_id is not '' (34 kept them).
    (34, "workspace_prefs", _v34_workspace_prefs),
]
