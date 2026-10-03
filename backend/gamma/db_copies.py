"""Copies of the databases in the store, so a lost disk costs one interval
of work instead of a day's (docs/dev/debugging.md "Database copies in the
bucket").

With the stored files in a bucket (``GAMMA_BLOBS=s3``; ``GAMMA_DB_COPIES``,
``config.db_copies_env``), ``tick``, which the app's ``every()`` loop runs
at startup and every ``GAMMA_DB_COPIES_INTERVAL``, copies users.db and each
workspace's pages.db and data.db through the store's object calls
(gamma/blobs.py):

- To ``dbcopies/users/<stamp>.db`` and ``dbcopies/<ws>/<stamp>-pages.db`` /
  ``-data.db`` (after the bucket's ``GAMMA_S3_PREFIX``), ``<stamp>`` the
  round's UTC time (``20261003T140000Z``), one for every copy of a round.
  The newest ``GAMMA_DB_COPIES_KEEP`` copies of each database are kept.
- Each copy is taken with the backup API into a temp file under
  ``backups/.dbcopies/`` (consistent while the server writes:
  ``backups.snapshot_db``), quick-checked and uploaded only when it passes.
  One that fails is skipped (the check's warning and the admins'
  ``db-damage`` notice, gamma/integrity.py) and tried again next round.
- Only what changed is copied: a round compares each file's mtime and size,
  and its WAL's, with what they were when its last copy was taken
  (``backups/dbcopies.json``), so an unchanged database costs two stats.
  Before a copy a WAL with frames in it is checkpointed and truncated when
  nothing holds it (``db_maintenance.truncate_wal``, which never waits): the
  server folding the WAL into the file later, as its last connection to it
  closes, then changes nothing. users.db changes with every sign-in and is
  copied most rounds.
- A copy or an upload that fails is logged and the round goes on with the
  others; a round logs one line when it copied or failed something.
- A data directory never prunes the copies older than its own first round
  (``since`` in the state file): a server started on an empty disk (a lost
  volume, before anyone restored it) copies its fresh users.db, and must
  not push the old copies out. A restore makes them its own.

``restore`` (``manage.py db-copies --restore``, the server stopped) puts
copies back in place; ``litestream_config`` writes a Litestream
configuration for the same bucket (``manage.py litestream-config``).
"""

import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import blobs, config, db, integrity
from .backups import snapshot_db
from .db_maintenance import truncate_wal
from .logbuf import log

SPACE = "dbcopies"                       # the object calls' namespace (blobs.OBJECT_SPACES)
STATE_FILE = "dbcopies.json"             # under backups/: what each database's last copy saw, and since when
WORK_DIR = ".dbcopies"                   # under backups/: the copies on their way up
STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
_COPY_RE = re.compile(r"^(\d{8}T\d{6}Z)(?:-(pages|data))?\.db$")
KINDS = ("pages", "data")                # a workspace's databases; users.db is kind ""


class RestoreError(ValueError):
    """A restore that cannot be done; nothing was changed."""


def interval_s() -> int:
    """Seconds between rounds (``GAMMA_DB_COPIES_INTERVAL``), for the app's
    ``every()`` loop."""
    return config.db_copies_env()["interval"]


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def key(group: str, kind: str, stamp: str) -> str:
    """The object of one copy: ``dbcopies/users/<stamp>.db`` for users.db
    (``kind`` ""), ``dbcopies/<ws>/<stamp>-<kind>.db`` for a workspace's."""
    return f"{SPACE}/{group}/{stamp}{'-' + kind if kind else ''}.db"


def _label(group: str, kind: str) -> str:
    return f"{group}/{kind}.db" if kind else "users.db"


def _rel(group: str, kind: str) -> str:
    """The database's path in the data directory, as gamma/integrity.py
    names it."""
    return f"workspaces/{group}/{kind}.db" if kind else "users.db"


def _path(group: str, kind: str) -> Path:
    return Path(db.ws_db_path(group, f"{kind}.db")) if kind else Path(db.USERS_DB)


def _where(store) -> str:
    """Where the store keeps the copies, for the log and the state file."""
    return str(store.root / SPACE) if isinstance(store, blobs.LocalBlobs) else store.where


def _databases() -> list[tuple[str, str]]:
    """``(group, kind)`` of users.db and of every workspace's two databases."""
    return [("users", "")] + [(ws, kind) for ws in db.workspace_ids() for kind in KINDS]


def _signal(path: Path) -> list | None:
    """What says a database changed: the mtime and size of the file and of
    its WAL (an empty WAL counts as none); None when there is no file."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    seen = [st.st_mtime_ns, st.st_size]
    try:
        wal = os.stat(f"{path}-wal")
    except FileNotFoundError:
        return seen
    return seen + [wal.st_mtime_ns, wal.st_size] if wal.st_size else seen


# --- the state file ------------------------------------------------------------------

def _state_path() -> Path:
    return config.BACKUPS_DIR / STATE_FILE


def _read_state(where: str, stamp: str) -> dict:
    """``{store, since, files: {label: signal}}``; a fresh one (nothing
    copied, ``since`` this round) when there is none or it was another
    store's."""
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = None
    if not isinstance(state, dict) or state.get("store") != where or not isinstance(state.get("files"), dict):
        return {"store": where, "since": stamp, "files": {}}
    return state


def _write_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


# --- the round -----------------------------------------------------------------------

def _generations(store, group: str | None = None) -> dict:
    """``{(group, kind): [(stamp, size), ...]}``, oldest first: the copies
    the store holds of one group (``users`` or a workspace) or of all."""
    held: dict = {}
    for object_key, size, _mtime in store.list_objects(f"{SPACE}/{group}/" if group else f"{SPACE}/"):
        parts = object_key.split("/")
        m = _COPY_RE.match(parts[2]) if len(parts) == 3 else None
        if not m or (not m.group(2) and parts[1] != "users"):
            continue
        held.setdefault((parts[1], m.group(2) or ""), []).append((m.group(1), size))
    for gens in held.values():
        gens.sort()
    return held


def _copy(store, path: Path, object_key: str, work: Path) -> str:
    """Copy one database to ``object_key``: a backup-API copy into
    ``work``, quick-checked, uploaded when the check passes. Returns the
    check's result."""
    tmp = work / f"{secrets.token_hex(8)}.db"
    try:
        check = snapshot_db(path, tmp)
        if check == "ok":
            store.put_object(object_key, tmp)
        return check
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{tmp}{suffix}").unlink(missing_ok=True)


def _prune(store, group: str, kinds, keep: int, since: str) -> int:
    """Remove all but the newest ``keep`` copies of the group's ``kinds``,
    never one from before ``since``; how many went."""
    removed = 0
    held = _generations(store, group)
    for kind in kinds:
        for stamp, _size in held.get((group, kind), [])[:-keep]:
            if stamp >= since:
                store.delete_object(key(group, kind, stamp))
                removed += 1
    return removed


def _some(names, n: int = 5) -> str:
    return ", ".join(names[:n]) + (f" and {len(names) - n} more" if len(names) > n else "")


def tick() -> dict:
    """One round (above); nothing at all with the copies off. Returns
    ``copied`` (labels: ``users.db``, ``<ws>/pages.db``), ``failed``
    ({label: why}), ``unchanged`` (how many were passed by) and ``pruned``
    (old copies removed)."""
    done = {"copied": [], "failed": {}, "unchanged": 0, "pruned": 0}
    settings = config.db_copies_env()
    if not settings["on"]:
        return done
    started, stamp = time.monotonic(), _stamp()
    store = blobs.driver()
    state = _read_state(_where(store), stamp)
    work = config.BACKUPS_DIR / WORK_DIR
    shutil.rmtree(work, ignore_errors=True)  # what a killed round left
    work.mkdir(parents=True, exist_ok=True)
    checks, copied = {}, {}
    for group, kind in _databases():
        label, path = _label(group, kind), _path(group, kind)
        try:
            seen = _signal(path)
            if seen is None:
                continue
            if state["files"].get(label) == seen:
                done["unchanged"] += 1
                continue
            if len(seen) > 2 and truncate_wal(str(path)):  # a WAL with frames, folded in now if nothing holds it
                seen = _signal(path)
                if seen is None:
                    continue
            checks[_rel(group, kind)] = check = _copy(store, path, key(group, kind, stamp), work)
        except (OSError, sqlite3.Error, ValueError) as e:  # a bucket's refusal is a BlobError, an OSError
            done["failed"][label] = str(e)
            continue
        if check != "ok":
            done["failed"][label] = f"the copy failed its check: {check}"
            continue
        state["files"][label] = seen
        done["copied"].append(label)
        copied.setdefault(group, []).append(kind)
    for group, kinds in copied.items():
        try:
            done["pruned"] += _prune(store, group, kinds, settings["keep"], state.get("since", ""))
        except (OSError, ValueError) as e:
            done["failed"][f"pruning {group}"] = str(e)
    shutil.rmtree(work, ignore_errors=True)
    if done["copied"]:
        try:
            _write_state(state)
        except OSError as e:  # the next round copies these again
            done["failed"][str(_state_path())] = str(e)
    if checks:
        integrity.record(checks, "copy to the store")
    if done["copied"] or done["failed"]:
        parts = []
        if done["copied"]:
            pruned = f"{done['pruned']} old cop{'y' if done['pruned'] == 1 else 'ies'} removed"
            parts.append(f"copied {_some(done['copied'])} to {_where(store)} in {time.monotonic() - started:.1f} s "
                         f"({done['unchanged']} unchanged, {pruned})")
        if done["failed"]:
            failed = [f"{label} ({why})" for label, why in done["failed"].items()]
            parts.append(f"failed: {_some(failed, 3)}")
        (log.warning if done["failed"] else log.info)(f"[dbcopies] {'; '.join(parts)}")
    return done


# --- listing and restoring (manage.py db-copies) -------------------------------------

def listing(group: str | None = None) -> dict:
    """``{label: [(stamp, size), ...]}``, oldest first: the copies the store
    holds of ``group`` (``users``, a workspace id) or of every database."""
    if group and group != "users":
        db.safe_ws_id(group)
    held = _generations(blobs.driver(), group)
    return {_label(g, k): gens for (g, k), gens in sorted(held.items()) if group != "users" or not k}


def _in_use(path: Path) -> str:
    """Why the database at ``path`` cannot be had alone, "" when it can: an
    exclusive open that never waits, which fails while any other
    connection, in this process or another, has the file open and has
    read it (a running server's do)."""
    try:
        uri = path.resolve().as_uri() + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)) as conn:
            conn.execute("PRAGMA locking_mode = EXCLUSIVE")
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.execute("ROLLBACK")
    except sqlite3.OperationalError as e:
        return str(e)
    except sqlite3.DatabaseError:
        return ""  # not a database SQLite can read: nothing uses it as one
    return ""


def _move_aside(path: Path, now: str) -> Path | None:
    """Move the database at ``path``, with its WAL or rollback journal, to
    ``<name>.pre-restore-<now>`` (it opens there as it was) and drop its
    ``-shm``; the moved file, None when there was none."""
    aside = path.with_name(f"{path.name}.pre-restore-{now}")
    had = path.exists()
    for suffix in ("", "-wal", "-journal"):
        if Path(f"{path}{suffix}").exists():
            os.replace(f"{path}{suffix}", f"{aside}{suffix}")
    Path(f"{path}-shm").unlink(missing_ok=True)
    return aside if had else None


def restore(target: str, at: str | None = None) -> list[dict]:
    """Put copies back in place, the server STOPPED. ``target``: ``users``
    (users.db), a workspace id (its pages.db and data.db) or ``all``
    (users.db and every workspace the store holds copies of). Each
    database gets its newest copy or, with ``at`` (a stamp as ``listing``
    shows it), its newest at or before it: what it held at that round,
    since a database is copied whenever it changed. A workspace without a
    pages.db copy that early is left out of ``all``; its data.db without
    one stays as it is (all of it can be rebuilt).

    Refused (RestoreError) with nothing changed when users.db or a
    database to replace is in use (``_in_use``: a running server holds
    the files it used lately), when there is no copy to restore, when a
    download fails its quick check (every copy is downloaded beside its
    database first), and without ``at`` when a database has copies from
    both sides of this data directory's first round: a server started on
    an empty disk copied its fresh databases, which are the newest now,
    and the operator must say which to take. Then each database in place
    is moved aside, with its
    WAL, as ``<name>.pre-restore-<time>``, and the copy takes its name; a
    restored pages.db's unreferenced-file clocks start over
    (``upload_gc.restart_clocks``). The data directory takes the store's
    copies for its own: its rounds may prune the older ones again. Returns
    ``[{"label", "stamp", "aside"}]``."""
    from . import upload_gc  # local: only a restore needs it

    if at is not None and not STAMP_RE.match(at):
        raise RestoreError(f"--at takes a stamp as --list shows it (like 20261003T140000Z), not {at!r}")
    if target not in ("users", "all"):
        try:
            db.safe_ws_id(target)
        except ValueError:
            raise RestoreError(f"{target!r} is no workspace id") from None
    store = blobs.driver()
    held = {(group, kind): gens
            for (group, kind), gens in _generations(store, None if target == "all" else target).items()
            if target != "users" or not kind}
    since = _read_state(_where(store), "").get("since", "")
    if at is None and since:
        mixed = [gens for gens in held.values() if gens[0][0] < since <= gens[-1][0]]
        if mixed:
            before = max(stamp for gens in mixed for stamp, _size in gens if stamp < since)
            raise RestoreError(f"the store holds copies from before this data directory's first round ({since}): "
                               f"it may have started on an empty disk, so its own are the newest. Pass --at {before} "
                               f"for the newest from before, or --at {since} or later for its own")
    plan = []
    for (group, kind), gens in sorted(held.items()):
        picked = [stamp for stamp, _size in gens if at is None or stamp <= at]
        if picked:
            plan.append((group, kind, picked[-1]))
    primary = {group for group, kind, _ in plan if kind in ("", "pages")}
    plan = [step for step in plan if step[0] in primary]  # a data.db comes back with its pages.db only
    if not plan:
        raise RestoreError(f"{_where(store)} holds no copy of {target}" + (f" from {at} or before" if at else ""))

    checked = sorted({("users", "")} | {(group, kind) for group, kind, _ in plan})
    busy = [f"{_rel(group, kind)} ({why})" for group, kind in checked
            if _path(group, kind).exists() and (why := _in_use(_path(group, kind)))]
    if busy:
        raise RestoreError(f"in use, so the server is running: stop it first ({_some(busy, 3)})")

    fetched = []
    try:
        for group, kind, stamp in plan:
            path = _path(group, kind)
            tmp = path.with_name(f".{path.name}.restoring")
            if not store.get_object(key(group, kind, stamp), tmp):
                raise RestoreError(f"{key(group, kind, stamp)} is gone from {_where(store)}")
            fetched.append(tmp)
            check = integrity.quick_check(tmp)
            if check != "ok":
                raise RestoreError(f"the copy of {_label(group, kind)} from {stamp} failed its check: {check}")
    except BaseException:
        for tmp in fetched:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{tmp}{suffix}").unlink(missing_ok=True)
        raise

    db.close_connections()  # this process's cached handles, before files move under them
    now, done = _stamp(), []
    for (group, kind, stamp), tmp in zip(plan, fetched):
        path = _path(group, kind)
        aside = _move_aside(path, now)
        os.replace(tmp, path)
        if kind == "pages":
            with closing(sqlite3.connect(str(path))) as conn:
                upload_gc.restart_clocks(conn)
        done.append({"label": _label(group, kind), "stamp": stamp, "aside": aside.name if aside else ""})
    try:
        state = _read_state(_where(store), "")
        state["since"] = ""  # the copies are this data directory's now: older ones may be pruned
        _write_state(state)
    except OSError:
        pass  # without it the next round starts a state of its own
    return done


# --- Litestream ----------------------------------------------------------------------

def litestream_config() -> tuple[str, int]:
    """A ``litestream.yml`` (Litestream 0.5: one ``replica`` per database)
    for the bucket the ``GAMMA_S3_*`` variables name, with an entry for
    users.db and for each workspace's pages.db and data.db as the data
    directory holds them now, replicated to ``<prefix>litestream/users.db``
    and ``<prefix>litestream/<ws>/<name>``. The keys stay in the
    environment: the file names ``${GAMMA_S3_ACCESS_KEY}`` and
    ``${GAMMA_S3_SECRET_KEY}``, which Litestream expands as it reads it.
    Returns the text and how many databases it names; ValueError without a
    bucket."""
    env = config.blob_env()
    if not env["bucket"]:
        raise ValueError("set GAMMA_S3_BUCKET (and the other GAMMA_S3_* variables) to the bucket to replicate to")
    prefix = blobs.key_prefix(env["prefix"])  # the store's own, so both live under one prefix
    q = json.dumps  # a JSON string is a YAML double-quoted one
    lines = ["# Litestream's configuration for Gamma's databases, written by `manage.py litestream-config`",
             f"# at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} with an entry for each database there was.",
             "# Litestream does not watch this file: write it again and restart Litestream whenever a",
             "# workspace is added or deleted. docs/dev/debugging.md \"Database copies in the bucket\"."]
    if env["access_key"]:
        lines += ["access-key-id: '${GAMMA_S3_ACCESS_KEY}'", "secret-access-key: '${GAMMA_S3_SECRET_KEY}'"]
    lines.append("dbs:")
    count = 0
    for group, kind in _databases():
        path = _path(group, kind)
        if not path.is_file():
            continue
        lines += [f"  - path: {q(str(path.absolute()))}",
                  "    replica:",
                  "      type: s3",
                  f"      bucket: {q(env['bucket'])}",
                  f"      path: {q(prefix + 'litestream/' + (f'{group}/{kind}.db' if kind else 'users.db'))}"]
        if env["endpoint"]:
            lines += [f"      endpoint: {q(env['endpoint'])}", "      force-path-style: true"]
        if env["region"]:
            lines.append(f"      region: {q(env['region'])}")
        count += 1
    if not count:
        lines[-1] = "dbs: []"
    return "\n".join(lines) + "\n", count
