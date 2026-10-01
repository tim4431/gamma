"""Database integrity: SQLite's ``quick_check`` on one file, and the latest
result per database file of the data directory.

Every snapshot checks the database copies it takes (server backups in
``gamma/backups.py``, workspace snapshots in ``gamma/ws_backup.py``), a
restore checks the copies it is about to put in place, and admins check
the live files from Settings → Server (``check_all``). A snapshot's result
goes into its manifest; the latest failure per live file is kept in
``backups/integrity.json`` (a file that passes a later check drops out),
which the admin notice ``db-damage`` reads (``gamma/notices.py``): a small
file, cheap to read on every poll.
"""

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from . import config
from .db import page_now
from .logbuf import log

_lock = threading.Lock()


def quick_check(path: Path) -> str:
    """``"ok"``, or what SQLite reports wrong with the file (its first
    problems, or why it cannot be read at all)."""
    try:
        with closing(sqlite3.connect(str(path), timeout=10)) as conn:
            rows = [r[0] for r in conn.execute("PRAGMA quick_check(20)")]
    except sqlite3.DatabaseError as e:
        return f"unreadable: {e}"
    return "ok" if rows == ["ok"] else "; ".join(rows)[:500]


def damaged(checks) -> list[str]:
    """The files of a manifest's ``integrity`` map whose check failed."""
    return sorted(name for name, result in (checks or {}).items() if result != "ok")


def _store() -> Path:
    return config.BACKUPS_DIR / "integrity.json"


def _read() -> dict:
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(results: dict, source: str) -> None:
    """Keep the latest check of each live database file: ``results`` maps
    its path relative to the data directory (``users.db``,
    ``workspaces/<id>/pages.db``) to ``"ok"`` or the problem; ``source``
    names the check (a server backup, a workspace snapshot, the admin's
    check). Never raises: a result that cannot be stored is logged."""
    for rel, result in results.items():
        if result != "ok":
            log.warning(f"[integrity] {rel} failed its check ({source}): {result}")
    try:
        with _lock:
            data = _read()
            changed = False
            for rel, result in results.items():
                if result == "ok":
                    changed |= data.pop(rel, None) is not None
                else:
                    data[rel] = {"result": result, "at": page_now(), "source": source}
                    changed = True
            if changed:
                _store().parent.mkdir(parents=True, exist_ok=True)
                tmp = _store().with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                tmp.replace(_store())
    except OSError as e:
        log.warning(f"[integrity] could not store the check results: {e}")


def failures() -> dict:
    """``{relative path: {result, at, source}}`` for every database file
    whose latest check failed and that still exists (a deleted workspace's
    entry is ignored)."""
    return {rel: v for rel, v in _read().items() if (config.DATA_DIR / rel).exists()}


def db_files() -> list[Path]:
    """Every SQLite file of the data directory, whichever layout it is in."""
    files = []
    if config.USERS_DB.exists():
        files.append(config.USERS_DB)
    for root in (config.LEGACY_USERS_DIR, config.WORKSPACES_DIR):
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.name.startswith("."):
                    continue  # a deleted workspace's leftover (workspaces.remove_leftovers)
                for name in ("pages.db", "data.db"):
                    if (d / name).is_file():
                        files.append(d / name)
    return files


def check_all() -> dict:
    """Quick-check users.db and every workspace's pages.db and data.db as
    they are now (the admin's "Check databases"). Returns ``{ok,
    checked_at, files: [{file, workspace, name, result, ok}]}`` and records
    the results."""
    from . import workspaces  # local: workspaces imports seed → db

    out, results = [], {}
    for path in db_files():
        rel = path.relative_to(config.DATA_DIR).as_posix()
        result = quick_check(path)
        results[rel] = result
        parts = rel.split("/")
        ws = parts[1] if len(parts) == 3 and parts[0] == "workspaces" else ""
        name = ((workspaces.get(ws) or {}).get("name", "")) if ws else ""
        out.append({"file": rel, "workspace": ws, "name": name, "result": result, "ok": result == "ok"})
    record(results, "admin check")
    return {"ok": all(f["ok"] for f in out), "checked_at": page_now(), "files": out}
