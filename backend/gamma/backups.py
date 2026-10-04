"""Server backups: snapshots of the whole data directory under ``backups/``.

A backup is a directory ``backups/<time>-<label>/`` holding a copy of every
SQLite file (taken with the backup API, so it is consistent while the server
runs) at its relative path, optionally the ``uploads/`` directories too, and
a ``manifest.json``. Two producers, one shape:

- the migration runner takes one (databases only, marked ``auto``) before
  upgrading the data directory (``gamma/migrations.py``): users.db, and
  every workspace's databases when a pending step walks them all; a
  workspace's own steps add its two databases to it as they run
  (``add_workspace``). Only the newest ``KEEP_BACKUPS`` of those are kept,
  pruned once an upgrade has finished;
- admins take them from Settings → Server or ``manage.py backups
  --create`` (with or without uploads), download them as a zip, delete them.
  Nothing ever prunes these.

A snapshot is written under a dot-name (``.<name>.part``) and renamed to its
name once its manifest, written last, is in: a directory without a
manifest is an interrupted snapshot and is never listed or pruned. Every
database copy is quick-checked (``gamma/integrity.py``); the manifest's
``integrity`` says how each one came out.

Restoring is a copy-back over the data directory with the server stopped
(``manage.py backups --restore``): a whole-directory operation, deliberately
not an HTTP endpoint. Per-workspace backups — the snapshots users take from
Settings → Backups and the ``/api/export`` zips — are ``gamma/ws_backup.py``,
a different thing; this module does not copy ``backups/``.
"""

import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from contextlib import closing
from pathlib import Path

from . import config, integrity, jobs
from .db import close_connections, page_now

KEEP_BACKUPS = 3        # automatic (pre-upgrade) snapshots kept; hand-made ones are never pruned
NAME_RE = re.compile(r"^\d{8}-\d{6}-[A-Za-z0-9_.-]{1,40}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
AUTO_LABEL_RE = re.compile(r"^v\d+$")  # the runner's label, for snapshots from before the `auto` flag
UPGRADE_MARKER = "upgrade.json"        # names the snapshot of an upgrade that has not finished
ADDED_LIST = "workspaces.jsonl"        # the workspaces added to a snapshot after it was taken, a line each

_adding = threading.Lock()


def snapshot_db(src: Path, dst: Path) -> str:
    """Copy one database with the backup API and quick-check the copy;
    returns the check's result. A file SQLite cannot read (damaged) is
    copied byte for byte instead, so the snapshot still holds it as it is.
    A failure to write (a full disk) raises."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(str(src))) as s, closing(sqlite3.connect(str(dst))) as d:
            s.backup(d)
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError as e:
        dst.unlink(missing_ok=True)
        for suffix in ("", "-wal"):
            if Path(str(src) + suffix).is_file():
                shutil.copyfile(str(src) + suffix, str(dst) + suffix)
        return f"unreadable: {e}"
    return integrity.quick_check(dst)


def _upload_dirs() -> list[Path]:
    dirs = []
    for root in (config.LEGACY_USERS_DIR, config.WORKSPACES_DIR):
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if (d / "uploads").is_dir() and not d.name.startswith("."):
                    dirs.append(d / "uploads")
    return dirs


def _claim_name(label: str) -> tuple[Path, Path]:
    """A new snapshot's ``backups/<time>-<label>`` and its ``.part`` work
    directory, made here. The exclusive mkdir is the claim: a second
    snapshot of the label in the same second (another thread, or
    ``manage.py`` beside the server) moves on to the next second instead of
    writing into this one's copy, and a ``.part`` an interrupted snapshot
    left is passed over, never reused."""
    while True:
        target = config.BACKUPS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}"
        work = config.BACKUPS_DIR / f".{target.name}.part"
        if not target.exists():
            try:
                work.mkdir()
                return target, work
            except FileExistsError:
                pass
        time.sleep(1)


def create(label: str, uploads: bool = False, *, auto: bool = False, workspaces: bool = True,
           progress=None) -> dict:
    """Snapshot the data directory into ``backups/<time>-<label>/`` (relative
    paths kept) with a manifest. ``uploads`` copies the upload files too (a
    file removed while the copy runs is left out, never a failed backup).
    ``auto`` marks the migration runner's snapshots, the only ones
    ``prune_backups`` removes. ``workspaces=False`` copies users.db alone
    (the runner's, when only workspace steps are pending: each workspace's
    files come in with ``add_workspace``). ``progress`` (a background job's
    report, gamma/jobs.py) hears each database and each file copied.
    Returns the backup's info dict; raises ValueError on a bad label and
    OSError when the copy cannot be written (nothing is left behind
    then)."""
    progress = progress or jobs.no_progress
    from .migrations import data_version  # local: migrations imports this module

    if not LABEL_RE.match(label or ""):
        raise ValueError("label must be 1-40 chars of letters, digits, _ . -")
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    target, work = _claim_name(label)
    checks = {}
    try:
        files = []
        databases = integrity.db_files() if workspaces else [f for f in (config.USERS_DB,) if f.exists()]
        for n, src in enumerate(databases):
            rel = src.relative_to(config.DATA_DIR)
            progress(phase="databases", done=n, total=len(databases), unit="files", item=rel.as_posix())
            checks[rel.as_posix()] = snapshot_db(src, work / rel)
            files.append(rel.as_posix())
        upload_files = 0
        if uploads:
            listed = [(src, sorted(src.iterdir())) for src in _upload_dirs()]
            total = sum(len(names) for _, names in listed)
            for src, names in listed:
                dest = work / src.relative_to(config.DATA_DIR)
                dest.mkdir(parents=True, exist_ok=True)
                for f in names:
                    progress(phase="files", done=upload_files, total=total, unit="files")
                    try:
                        if f.is_file():
                            shutil.copy2(f, dest / f.name)
                            upload_files += 1
                    except FileNotFoundError:
                        continue  # swept while the copy ran: no longer part of the library
        work.mkdir(parents=True, exist_ok=True)
        (work / "manifest.json").write_text(json.dumps({
            "created_at": page_now(), "label": label, "schema_version": data_version(),
            "files": files, "workspaces": workspaces, "uploads": bool(uploads), "upload_files": upload_files,
            "auto": auto, "integrity": checks, "complete": True,
            "note": "Snapshot of the Gamma data directory (gamma/backups.py). Restore with "
                    "`manage.py backups --restore <name>` while the server is stopped.",
        }, indent=2), encoding="utf-8")
        work.rename(target)
    except BaseException:
        shutil.rmtree(str(work), ignore_errors=True)
        raise
    integrity.record(checks, f"server backup {target.name}")
    return info(target.name)


def add_workspace(name: str, ws: str) -> str:
    """Copy one workspace's databases into the snapshot ``name``, at
    ``workspaces/<ws>/`` as ``create`` lays them out: what the migration
    runner does before that workspace's own steps run
    (gamma/migrations.py). A workspace the snapshot holds already keeps
    that copy, the one from before its upgrade. The copy is written under a
    dot-name and renamed once both files are in, then listed with its
    checks as a line of ``ADDED_LIST``, which ``info`` adds to the
    manifest's ``files`` and ``integrity`` (one line each, so the walk over
    every workspace never rewrites a growing manifest). Returns the
    snapshot's path; raises FileNotFoundError for an unknown snapshot and
    OSError when the copy cannot be written (nothing is left behind
    then)."""
    path = backup_path(name)
    if not path or not (path / "manifest.json").is_file():
        raise FileNotFoundError(name)
    target = path / "workspaces" / ws
    if (target / "pages.db").is_file():
        return str(path)
    work = target.with_name(f".{ws}.part")
    shutil.rmtree(str(work), ignore_errors=True)
    checks = {}
    try:
        for db_name in ("pages.db", "data.db"):
            src = config.WORKSPACES_DIR / ws / db_name
            if src.is_file():
                checks[f"workspaces/{ws}/{db_name}"] = snapshot_db(src, work / db_name)
        shutil.rmtree(str(target), ignore_errors=True)  # a directory without its pages.db: no copy of it
        work.rename(target)
    except BaseException:
        shutil.rmtree(str(work), ignore_errors=True)
        raise
    line = json.dumps({"workspace": ws, "at": page_now(), "files": sorted(checks), "integrity": checks})
    with _adding, open(path / ADDED_LIST, "a", encoding="utf-8") as listed:
        listed.write(line + "\n")
    integrity.record(checks, f"server backup {name}")
    return str(path)


def _added(path: Path) -> list[dict]:
    """The workspaces ``add_workspace`` put into the snapshot at ``path``."""
    try:
        lines = (path / ADDED_LIST).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    added = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a line cut short by a crash: its copy was renamed in, never listed
        if isinstance(entry, dict):
            added.append(entry)
    return added


def latest_auto() -> dict | None:
    """The newest automatic snapshot's manifest, with its ``name`` (no
    size, so cheap), or None."""
    if not config.BACKUPS_DIR.is_dir():
        return None
    for d in sorted(config.BACKUPS_DIR.iterdir(), reverse=True):
        manifest = _manifest(d) if d.is_dir() and NAME_RE.match(d.name) else None
        if manifest and is_auto(manifest):
            return {**manifest, "name": d.name}
    return None


def _manifest(path: Path) -> dict | None:
    """The manifest of the backup at ``path``; None when it has none (an
    interrupted snapshot) or it cannot be read."""
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def info(name: str) -> dict | None:
    """The backup's manifest plus its name, path, size, ``damaged`` (the
    database copies that failed their check) and ``auto`` as pruning reads
    it (``is_auto``: an old manifest has no flag); None for an unknown name
    or an interrupted snapshot (no manifest). The workspaces added to it
    later (``add_workspace``) are in its ``files`` and ``integrity``."""
    path = backup_path(name)
    manifest = _manifest(path) if path and path.is_dir() else None
    if manifest is None:
        return None
    added = _added(path)
    if added:
        manifest["files"] = [*manifest.get("files", []), *(f for a in added for f in a.get("files", []))]
        manifest["integrity"] = {**manifest.get("integrity", {}),
                                 **{k: v for a in added for k, v in (a.get("integrity") or {}).items()}}
    return {"name": name, "path": str(path), "size_bytes": _dir_size(path), **manifest,
            "auto": is_auto(manifest), "damaged": integrity.damaged(manifest.get("integrity"))}


def list_backups() -> list[dict]:
    """Every backup, oldest first."""
    if not config.BACKUPS_DIR.is_dir():
        return []
    return [b for b in (info(d.name) for d in sorted(config.BACKUPS_DIR.iterdir()) if d.is_dir()) if b]


def backup_path(name: str) -> Path | None:
    """The backup directory for a validated name (None for a bad name — the
    name comes from the client on the admin endpoints)."""
    if not NAME_RE.match(name or ""):
        return None
    return config.BACKUPS_DIR / name


def delete(name: str) -> bool:
    path = backup_path(name)
    if not path or not path.is_dir():
        return False
    shutil.rmtree(str(path), ignore_errors=True)
    return True


def is_auto(backup: dict) -> bool:
    """A migration runner's snapshot (older ones carry no flag: their label
    is ``v<N>``)."""
    if "auto" in backup:
        return backup["auto"] is True
    return bool(AUTO_LABEL_RE.match(backup.get("label") or ""))


def prune_backups(keep: int | None = None) -> list[str]:
    """Delete all but the newest ``keep`` (default KEEP_BACKUPS) automatic
    snapshots; returns what was removed. Hand-made backups and the snapshot
    of an unfinished upgrade are never touched."""
    keep = KEEP_BACKUPS if keep is None else keep
    spare = unfinished_upgrade().get("backup")
    auto = [b for b in list_backups() if is_auto(b) and b["name"] != spare]
    removed = []
    for old in auto[:max(0, len(auto) - keep)]:
        shutil.rmtree(old["path"], ignore_errors=True)
        removed.append(old["name"])
    return removed


# --- the unfinished-upgrade marker (gamma/migrations.py) -----------------------

def unfinished_upgrade() -> dict:
    """``{from, to, backup, started_at}`` of an upgrade that took its
    snapshot but has not finished (a failed step, a crash), else {}."""
    try:
        data = json.loads((config.BACKUPS_DIR / UPGRADE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_upgrade(marker: dict) -> None:
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.BACKUPS_DIR / (UPGRADE_MARKER + ".tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    tmp.replace(config.BACKUPS_DIR / UPGRADE_MARKER)


def clear_upgrade() -> None:
    (config.BACKUPS_DIR / UPGRADE_MARKER).unlink(missing_ok=True)


def zip_backup(name: str) -> Path:
    """Zip a backup into a temp file (the caller deletes it after sending)."""
    path = backup_path(name)
    if not path or not path.is_dir():
        raise FileNotFoundError(name)
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(path.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(path).as_posix())
    return Path(tmp.name)


def restore(name: str) -> dict:
    """Copy a backup's files back over the data directory (server STOPPED —
    open handles would see torn writes). Files the backup lacks stay as they
    are (in a runner's snapshot without the workspaces: those of every
    workspace its upgrade has not changed yet), and the workspace copies
    added to it later (``add_workspace``) come back with it. Uploads come
    back only from a backup that carried them. The
    unreferenced-file clocks of every restored pages.db start over
    (``upload_gc.restart_clocks``: an old snapshot never makes a file due at
    once). An unfinished upgrade is abandoned with it: the next start
    snapshots the restored data afresh before upgrading. Returns
    ``{"files": n}``."""
    from . import upload_gc  # local: only a restore needs it

    path = backup_path(name)
    if not path or not path.is_dir():
        raise FileNotFoundError(name)
    close_connections()  # this process's cached handles on the files about to be copied over
    count, pages_dbs = 0, []
    for f in path.rglob("*"):
        rel = f.relative_to(path)
        if not f.is_file() or f.name == "manifest.json" or rel.as_posix() == ADDED_LIST:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue  # a workspace copy cut short (add_workspace's dot-name)
        dest = config.DATA_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # WAL/SHM sidecars of the live file would replay stale pages over the
        # restored database: drop them with it.
        if dest.suffix == ".db":
            for suffix in ("-wal", "-shm"):
                side = dest.with_name(dest.name + suffix)
                if side.exists():
                    side.unlink()
        shutil.copyfile(f, dest)
        count += 1
        if dest.name == "pages.db":
            pages_dbs.append(dest)
    for db in pages_dbs:
        with closing(sqlite3.connect(str(db))) as conn:
            upload_gc.restart_clocks(conn)
    clear_upgrade()
    return {"files": count}
