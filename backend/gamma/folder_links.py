"""Folder links: a folder of a workspace kept as a directory on this
server's own disk (docs/dev/folder_sync.md "Links kept by the server").

A link is a row of users.db ``folder_links``: the workspace, the folder (a
folder block id, or ``root`` for the whole library), the directory's path
below the folders root (``root_dir``: ``GAMMA_FOLDERS_DIR``, else
``<data>/folders``), whether notes files are written, the change-log seq
the last round saw, and that round's status. The directory itself is the
client's: a ``gamma_sync.Round`` over a ``LocalSource`` that reads the
workspace in-process instead of over HTTP, so the state file in it, the
layout, and the rules — only files a round wrote are ever touched — are
exactly what ``gamma_sync.py`` writes on a PC.

Rounds run from ``tick`` (the app's periodic loop, every ``TICK_S``) for
a link whose workspace's change log moved past its cursor, in the
background when a link is made or asked to sync, and inline for a sync
asked to wait. One round per link at a time (``_locks``); a round that
failed is tried again after ``RETRY_S``.
"""

import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import config, folder_sync, gamma_sync, workspaces
from .blocks_store import folder_paths
from .db import connect_pages_db, connect_users_db, page_now
from .logbuf import log
from .obsidian_export import page_dir, vault_name
from .storage import find_upload_file

TICK_S = 30            # how often the loop looks for links whose workspace changed
MAX_LINKS = 10         # per workspace
RETRY_S = 300          # a link whose last round failed waits this long for the next try
STALE_RUN_S = 600      # a "running" older than this (the process died) is not running
KEPT_SHOWN = 50        # kept files listed in the status
RECENT_SHOWN = 50      # last actions listed in the status

_COLS = "id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, status"
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class LinkError(ValueError):
    pass


def root_dir() -> Path:
    return config.folders_dir()


def anywhere() -> bool:
    return config.folders_anywhere()


def _absolute(text: str) -> bool:
    return os.path.isabs(text) or bool(re.match(r"^[A-Za-z]:[\/]", text))


def clean_path(text: str) -> str:
    """A directory path as a link stores it. Below the root: forward slashes
    between names, each name a valid file name (``vault_name``, which also
    drops what could leave the root), up to 200 characters. Where links may
    go anywhere (``anywhere``), an absolute path is kept as the machine
    resolves it, except a filesystem root or anything inside the data
    directory that is not below the folders root."""
    text = str(text or "").strip()
    if anywhere() and _absolute(text):
        target = Path(text).expanduser().resolve()
        data_dir = config.DATA_DIR.resolve()
        inside_root = target == root_dir().resolve() or root_dir().resolve() in target.parents
        if target.parent == target:
            raise LinkError("Pick a directory, not the root of a drive.")
        if (target == data_dir or data_dir in target.parents) and not inside_root:
            raise LinkError("That directory is inside Gamma's data directory; pick another.")
        return str(target)
    parts = [vault_name(p) for p in text.replace("\\", "/").split("/") if p.strip(". ")]
    path = "/".join(parts)
    if not path or len(path) > 200:
        raise LinkError("Give the directory a name of up to 200 characters.")
    return path


def default_path(conn, ws: str, folder_id: str) -> str:
    """The directory a folder gets when none is named: its path's names,
    or the workspace's name for the whole library."""
    if folder_id == folder_sync.ROOT:
        info = workspaces.get(ws) or {}
        return vault_name(info.get("name") or "Library")
    return page_dir(folder_paths(conn).get(folder_id) or []).rstrip("/") or "Folder"


def _row(r) -> dict:
    try:
        status = json.loads(r[8] or "{}")
    except ValueError:
        status = {}
    return {"id": r[0], "workspace_id": r[1], "folder_id": r[2], "path": r[3], "notes": bool(r[4]),
            "created_by": r[5], "created_at": r[6], "cursor": r[7] or "", "status": status if isinstance(status, dict) else {}}


def list_links(ws: str | None = None) -> list[dict]:
    """The links of one workspace, or every link of the server."""
    with connect_users_db() as conn:
        if ws is None:
            rows = conn.execute(f"SELECT {_COLS} FROM folder_links ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(f"SELECT {_COLS} FROM folder_links WHERE workspace_id = ? ORDER BY created_at",
                                (ws,)).fetchall()
    return [_row(r) for r in rows]


def get_link(link_id: str) -> dict | None:
    with connect_users_db() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM folder_links WHERE id = ?", (link_id,)).fetchone()
    return _row(r) if r else None


def dest_of(link: dict) -> Path:
    return Path(link["path"]) if _absolute(link["path"]) else root_dir() / link["path"]


def _lock(link_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(link_id, threading.Lock())


def _state_for(link: dict) -> dict:
    return {"format": gamma_sync.FORMAT, "server": "local", "workspace": link["workspace_id"],
            "folder": link["folder_id"], "folder_path": [], "notes": link["notes"], "files": {}, "dirs": [],
            "cursor": ""}


def _ensure_state(link: dict) -> gamma_sync.Link:
    """The directory and its state file, made when missing. One left by an
    earlier link to the same folder is adopted, its files staying the
    round's to keep up to date; one of another folder is refused."""
    dest = dest_of(link)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if (dest / gamma_sync.STATE).is_file():
            existing = gamma_sync.Link.load(dest)
            if existing.state.get("folder") != link["folder_id"] or existing.state.get("workspace") != link["workspace_id"]:
                raise LinkError(f"{dest} already holds another folder's files; pick another name or remove it first.")
            existing.state["notes"] = link["notes"]
            existing.save()
            return existing
        fresh = gamma_sync.Link(dest, _state_for(link))
        fresh.save()
        return fresh
    except (OSError, gamma_sync.SyncError) as e:
        raise LinkError(f"Cannot write {dest}: {e}") from e


def create_link(ws: str, folder_id: str, path: str, notes: bool, user_id: str) -> dict:
    """A new link: the folder must exist (or be ``root``), the path is
    cleaned (``clean_path``; the folder's own when empty) and must not be
    another link's, the directory is made with its state file."""
    with connect_pages_db(ws) as conn:
        if folder_id != folder_sync.ROOT and folder_id not in folder_paths(conn):
            raise LinkError("That folder does not exist.")
        path = clean_path(path or default_path(conn, ws, folder_id))
    if not _absolute(path):
        try:
            root_dir().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LinkError(f"The folders directory {root_dir()} cannot be written: {e}") from e
    link = {"id": uuid.uuid4().hex, "workspace_id": ws, "folder_id": folder_id, "path": path, "notes": bool(notes),
            "created_by": user_id, "created_at": page_now(), "cursor": "", "status": {}}
    with connect_users_db() as conn:
        if conn.execute("SELECT COUNT(*) FROM folder_links WHERE workspace_id = ?", (ws,)).fetchone()[0] >= MAX_LINKS:
            raise LinkError(f"A workspace keeps at most {MAX_LINKS} folders on disk.")
        if conn.execute("SELECT 1 FROM folder_links WHERE path = ?", (path,)).fetchone():
            raise LinkError(f"Another link already writes {path}.")
        _ensure_state(link)
        try:
            conn.execute(f"INSERT INTO folder_links ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (link["id"], ws, folder_id, path, int(link["notes"]), user_id, link["created_at"], "", "{}"))
        except sqlite3.IntegrityError as e:   # the path, taken meanwhile (UNIQUE, ignoring case)
            raise LinkError(f"Another link already writes {path}.") from e
    return link


def set_notes(link_id: str, notes: bool) -> dict | None:
    link = get_link(link_id)
    if link is None:
        return None
    link["notes"] = bool(notes)
    _ensure_state(link)
    with connect_users_db() as conn:
        conn.execute("UPDATE folder_links SET notes = ? WHERE id = ?", (int(link["notes"]), link_id))
    return link


def _remove_written(dest: Path) -> None:
    """What the rounds wrote and nothing else: the files as recorded and
    unchanged since, the directories they made when empty, the state file,
    and the directory itself when that leaves it empty."""
    if not (dest / gamma_sync.STATE).is_file():
        return
    try:
        link = gamma_sync.Link.load(dest)
    except gamma_sync.SyncError:
        return
    for path, entry in link.state["files"].items():
        if not gamma_sync.safe_rel(path):
            continue
        target = dest / path
        if target.is_file() and not gamma_sync.modified(target, entry):
            target.unlink(missing_ok=True)
    for d in sorted([*link.state["dirs"], "attachments"], key=len, reverse=True):
        p = dest / d
        if gamma_sync.safe_rel(d) and p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    link.file.unlink(missing_ok=True)
    if dest.is_dir() and not any(dest.iterdir()):
        dest.rmdir()


def delete_link(link_id: str, remove_files: bool = False) -> bool:
    """Forget the link; with ``remove_files`` also take back what it wrote
    (``_remove_written``). The row goes whatever the disk allows."""
    link = get_link(link_id)
    if link is None:
        return False
    with _lock(link_id):
        if remove_files:
            try:
                _remove_written(dest_of(link))
            except OSError as e:
                log.warning(f"[folders] removing {link['path']}: {e}")
        with connect_users_db() as conn:
            conn.execute("DELETE FROM folder_links WHERE id = ?", (link_id,))
    return True


class LocalSource:
    """What a round reads of one folder, from this server's own workspace:
    the three reads of ``gamma_sync.RemoteSource`` without HTTP."""

    def __init__(self, ws: str, folder_id: str):
        self.ws, self.folder = ws, folder_id

    def manifest(self) -> dict:
        with connect_pages_db(self.ws) as conn:
            m = folder_sync.manifest(conn, self.ws, self.folder)
        if m is None:
            raise gamma_sync.SyncError("the folder no longer exists")
        return m

    def notes(self, page_ids) -> dict:
        with connect_pages_db(self.ws) as conn:
            return folder_sync.notes(conn, self.ws, self.folder, list(page_ids)) or {}

    def download(self, name: str, target: Path) -> None:
        source = find_upload_file(name, self.ws)
        if source is None:
            raise gamma_sync.SyncError(f"{name} is not stored on this server")
        part = target.with_name(target.name + gamma_sync.PART)
        try:
            shutil.copyfile(source, part)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)


def _write_status(link_id: str, status: dict, cursor: str | None = None) -> None:
    with connect_users_db() as conn:
        if cursor is None:
            conn.execute("UPDATE folder_links SET status = ? WHERE id = ?", (json.dumps(status), link_id))
        else:
            conn.execute("UPDATE folder_links SET status = ?, cursor = ? WHERE id = ?", (json.dumps(status), cursor, link_id))


def run_link(link_id: str, *, full: bool = False, force: bool = False) -> dict | None:
    """One round of the link now, in this thread: its status afterwards
    (``{running, last_sync, last_error, counts, kept, recent, dest}``), the
    current status when a round is already running, None for no such link."""
    link = get_link(link_id)
    if link is None:
        return None
    lock = _lock(link_id)
    if not lock.acquire(blocking=False):
        return link["status"]
    try:
        actions: list[str] = []
        _write_status(link_id, {**link["status"], "running": True, "started_at": time.time()})
        try:
            state = _ensure_state(link)
            rnd = gamma_sync.Round(state, LocalSource(link["workspace_id"], link["folder_id"]), full=full, force=force,
                                   say=lambda verb, what: actions.append(f"{verb} {what}"))
            counts = rnd.run()
            status = {"running": False, "last_sync": page_now(), "last_error": "", "counts": counts,
                      "kept": [{"path": p, "why": w} for p, w in rnd.kept[:KEPT_SHOWN]],
                      "recent": actions[-RECENT_SHOWN:], "dest": str(state.dest)}
            _write_status(link_id, status, cursor=str(state.state.get("cursor", "")))
        except (gamma_sync.SyncError, LinkError, OSError) as e:
            status = {**link["status"], "running": False, "last_error": str(e), "failed_at": time.time()}
            _write_status(link_id, status)
            log.warning(f"[folders] {link['path']}: {e}")
        return status
    finally:
        lock.release()


def run_in_background(link_id: str, **kw) -> None:
    threading.Thread(target=run_link, args=(link_id,), kwargs=kw, daemon=True,
                     name=f"folder-link-{link_id[:8]}").start()


def _newest_seq(ws: str) -> int:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]


def _age(stamp) -> float:
    return time.time() - float(stamp) if stamp else float("inf")


def due(link: dict) -> bool:
    """Whether the loop runs the link now: not while a round runs (unless
    that mark is stale), not for ``RETRY_S`` after a failure, and otherwise
    only when its workspace's change log moved past the cursor it holds —
    or it has never synced."""
    st = link["status"]
    if st.get("running") and _age(st.get("started_at")) < STALE_RUN_S:
        return False
    if st.get("last_error"):
        return _age(st.get("failed_at")) >= RETRY_S
    return not st.get("last_sync") or str(_newest_seq(link["workspace_id"])) != link["cursor"]


def tick() -> None:
    """The loop's round: every due link, one after another; a link whose
    workspace is gone is forgotten."""
    for link in list_links():
        if not workspaces.get(link["workspace_id"]):
            with connect_users_db() as conn:
                conn.execute("DELETE FROM folder_links WHERE id = ?", (link["id"],))
            continue
        if due(link):
            run_link(link["id"])
