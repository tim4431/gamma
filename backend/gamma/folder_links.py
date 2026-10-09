"""Folder links: a folder of a workspace kept as a directory on this
computer (docs/dev/folder_sync.md "Folders kept by the desktop app").

Only the desktop app's own server keeps them: the local server it runs as
the user, started with ``GAMMA_FOLDER_LINKS``
(``config.folder_links_enabled``). Any other
server, a NAS among them, answers the API with 404 and its tick does
nothing; it only serves the folder reads a link elsewhere asks for.

A link is a row of users.db ``folder_links``: the workspace, the folder (a
folder block id, or ``root`` for the whole library), the directory's full
path, whether notes files are written, the change-log seq the last round
saw, and that round's status. The directory itself is the client's: a
``gamma_sync.Round`` over a **source** — ``LocalSource``, which reads a
workspace of this server in-process, or the client's own
``gamma_sync.RemoteSource``, which reads a workspace of *another* Gamma
server over HTTP with a token of that server (``remote_url``, ``token``;
the desktop app keeps a NAS folder on a PC this way, no clone needed) —
so the state file in the directory, the layout, and the rules — only
files a round wrote are ever touched — are exactly what ``gamma_sync.py``
writes on a PC.

Rounds run from ``tick`` (the app's periodic loop, every ``TICK_S``) for
a link whose source moved past its cursor — a local workspace's change
log, read directly; a remote server's change feed, asked at most every
``REMOTE_POLL_S`` and waited on at most ``ASK_TIMEOUT_S`` — in the
background when a link is made or asked to sync, and inline for a sync
asked to wait. A remote link's round from the tick runs in a thread of its
own. One round per link at a time (``_locks``); a round that failed is
tried again after ``RETRY_S``. A paused link (``set_paused``) is left out
of the tick and runs only when asked to sync.
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

from cryptography.fernet import InvalidToken

from . import config, folder_sync, gamma_sync, workspaces
from .blocks_store import folder_paths, newest_change_seq
from .db import connect_pages_db, connect_users_db, page_now
from .logbuf import log
from .publisher_sessions import seal, unseal
from .storage import find_upload_file

TICK_S = 30            # how often the loop looks for links whose source changed
REMOTE_POLL_S = 60     # how often a link asks its remote server whether anything changed
ASK_TIMEOUT_S = 15     # how long that question, or the check of a new link's token, may wait
MAX_LINKS = 10         # per workspace
RETRY_S = 300          # a link whose last round failed waits this long for the next try
STALE_RUN_S = 600      # a "running" older than this (the process died) is not running
KEPT_SHOWN = 50        # kept files listed in the status

_COLS = "id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, status, remote_url, token, token_id"
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_status_guard = threading.Lock()  # one read-modify-write of a status at a time (_write_status, set_paused)
_polled: dict[str, float] = {}   # link id -> when its remote server was last asked
PAUSED = "paused_at"   # the status key of a pause: the user's, which no round writes


class LinkError(ValueError):
    pass


def _absolute(text: str) -> bool:
    """A full path: absolute, a drive, a share, the home directory, or a path
    from the top of a drive (Windows does not count that one absolute)."""
    return os.path.isabs(text) or bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith(("~", "/", "\\"))


def clean_path(text: str) -> str:
    """A directory as a link stores it: the full path the user picked, as
    this computer resolves it. A relative path, a filesystem root, and
    anything inside Gamma's data directory are refused."""
    text = str(text or "").strip()
    if not _absolute(text):
        raise LinkError("Give the full path of a directory on this computer.")
    target = Path(text).expanduser().resolve()
    data_dir = config.DATA_DIR.resolve()
    if target.parent == target:
        raise LinkError("Pick a directory, not the root of a drive.")
    if target == data_dir or data_dir in target.parents:
        raise LinkError("That directory is inside Gamma's data directory; pick another.")
    return str(target)


def _unseal(sealed: str) -> str:
    """The stored token (``publisher_sessions.seal``, the mirrors' way)."""
    try:
        return unseal(sealed)
    except (InvalidToken, ValueError) as e:
        raise LinkError("the stored token cannot be read: the data directory's key changed") from e


def _status_of(text: str) -> dict:
    try:
        status = json.loads(text or "{}")
    except ValueError:
        status = {}
    return status if isinstance(status, dict) else {}


def _row(r, with_token: bool = False) -> dict:
    link = {"id": r[0], "workspace_id": r[1], "folder_id": r[2], "path": r[3], "notes": bool(r[4]),
            "created_by": r[5], "created_at": r[6], "cursor": r[7] or "", "status": _status_of(r[8]),
            "remote_url": r[9] or "", "token_id": r[11] or ""}
    if with_token:
        link["token"] = _unseal(r[10] or "")
    return link


def visible_links(user_id: str) -> list[dict]:
    """Every link one account may see: those of this server's workspaces it
    has a role in, and those with a remote source it made."""
    out = []
    for link in all_links():
        mine = link["created_by"] == user_id if link["remote_url"] else workspaces.role_of(link["workspace_id"], user_id)
        if mine:
            out.append(link)
    return out


def all_links() -> list[dict]:
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM folder_links ORDER BY created_at").fetchall()
    return [_row(r) for r in rows]


def get_link(link_id: str, *, with_token: bool = False) -> dict | None:
    with connect_users_db() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM folder_links WHERE id = ?", (link_id,)).fetchone()
    return _row(r, with_token=with_token) if r else None


def dest_of(link: dict) -> Path:
    return Path(link["path"])


def _lock(link_id: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(link_id, threading.Lock())


def _server_of(link: dict) -> str:
    return link["remote_url"] or "local"


def _state_for(link: dict) -> dict:
    return {"format": gamma_sync.FORMAT, "server": _server_of(link), "workspace": link["workspace_id"],
            "folder": link["folder_id"], "folder_path": [], "notes": link["notes"], "files": {}, "dirs": [],
            "cursor": ""}


def _ensure_state(link: dict) -> gamma_sync.Link:
    """The directory and its state file, made when missing. One left by an
    earlier link to the same folder of the same server is adopted, its
    files staying the round's to keep up to date; one of another folder is
    refused."""
    dest = dest_of(link)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if (dest / gamma_sync.STATE).is_file():
            existing = gamma_sync.Link.load(dest)
            same = (existing.state.get("folder") == link["folder_id"] and existing.state.get("workspace") == link["workspace_id"]
                    and (existing.state.get("server") or "local").rstrip("/") == _server_of(link))
            if not same:
                raise LinkError(f"{dest} already holds another folder's files; pick another name or remove it first.")
            existing.state["notes"] = link["notes"]
            existing.save()
            return existing
        fresh = gamma_sync.Link(dest, _state_for(link))
        fresh.save()
        return fresh
    except (OSError, gamma_sync.SyncError) as e:
        raise LinkError(f"Cannot write {dest}: {e}") from e


def _insert(link: dict, token: str = "") -> dict:
    """The row, once the directory has its state file: the path must not
    be another link's (UNIQUE, ignoring case) and the workspace keeps at
    most ``MAX_LINKS``."""
    with connect_users_db() as conn:
        if conn.execute("SELECT COUNT(*) FROM folder_links WHERE workspace_id = ?", (link["workspace_id"],)).fetchone()[0] >= MAX_LINKS:
            raise LinkError(f"A workspace keeps at most {MAX_LINKS} folders on disk.")
        if conn.execute("SELECT 1 FROM folder_links WHERE path = ?", (link["path"],)).fetchone():
            raise LinkError(f"Another link already writes {link['path']}.")
        _ensure_state(link)
        try:
            conn.execute(f"INSERT INTO folder_links ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (link["id"], link["workspace_id"], link["folder_id"], link["path"], int(link["notes"]), link["created_by"],
                          link["created_at"], "", "{}", link["remote_url"], seal(token), link["token_id"]))
        except sqlite3.IntegrityError as e:   # the path, taken meanwhile (UNIQUE, ignoring case)
            raise LinkError(f"Another link already writes {link['path']}.") from e
    return link


def _new(ws: str, folder_id: str, path: str, notes: bool, user_id: str, remote_url: str = "", token_id: str = "") -> dict:
    return {"id": uuid.uuid4().hex, "workspace_id": ws, "folder_id": folder_id, "path": path, "notes": bool(notes),
            "created_by": user_id, "created_at": page_now(), "cursor": "", "status": {},
            "remote_url": remote_url, "token_id": token_id}


def create_link(ws: str, folder_id: str, path: str, notes: bool, user_id: str) -> dict:
    """A new link to a folder of this server's workspace ``ws``: the folder
    must exist (or be ``root``), the path is cleaned (``clean_path``) and
    must not be another link's, the directory is made with its state
    file."""
    path = clean_path(path)
    with connect_pages_db(ws) as conn:
        if folder_id != folder_sync.ROOT and folder_id not in folder_paths(conn):
            raise LinkError("That folder does not exist.")
    return _insert(_new(ws, folder_id, path, notes, user_id))


def create_remote_link(url: str, token: str, token_id: str, workspace: str, folder_id: str, path: str, notes: bool,
                       user_id: str) -> dict:
    """A new link whose source is a folder on *another* Gamma server, read
    with a token of that server (read access is enough; the token is kept
    Fernet-encrypted, ``token_id`` for whoever revokes it there later). The
    token is checked on that server (``/api/sync/whoami`` names its
    workspace, which must be ``workspace`` when one is given), and the
    folder must exist there."""
    path = clean_path(path)
    url = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+(/\S*)?$", url):
        raise LinkError("Give the other server's address as http(s)://host[:port].")
    if not token:
        raise LinkError("A token of the other server is needed.")
    server = gamma_sync.Server(url, token, timeout=ASK_TIMEOUT_S)
    try:
        who = server.get_json("/api/sync/whoami")
        tree = server.get_json("/api/sync/folders")
    except gamma_sync.SyncError as e:
        raise LinkError(str(e)) from e
    remote_ws = (who.get("workspace") or {}).get("id") or ""
    if not remote_ws:
        raise LinkError("The other server did not say which workspace the token opens.")
    if workspace and workspace != remote_ws:
        raise LinkError("The token belongs to another workspace of that server.")
    folders = {f["id"]: f["path"] for f in tree.get("folders", []) if f.get("id")}
    if folder_id != folder_sync.ROOT and folder_id not in folders:
        raise LinkError("That folder does not exist on the other server.")
    link = _new(remote_ws, folder_id, path, notes, user_id, remote_url=server.url, token_id=str(token_id or ""))
    return _insert(link, token)


def set_notes(link_id: str, notes: bool) -> dict | None:
    link = get_link(link_id)
    if link is None:
        return None
    link["notes"] = bool(notes)
    _ensure_state(link)
    with connect_users_db() as conn:
        conn.execute("UPDATE folder_links SET notes = ? WHERE id = ?", (int(link["notes"]), link_id))
    return link


def set_paused(link_id: str, paused: bool) -> dict | None:
    """Pause the link — the tick leaves it alone; a sync asked for still
    runs, and a round already running finishes — or resume it. The pause
    is ``status.paused_at``, when it began; None for no such link."""
    with _status_guard, connect_users_db() as conn:
        r = conn.execute("SELECT status FROM folder_links WHERE id = ?", (link_id,)).fetchone()
        if r is None:
            return None
        status = _status_of(r[0])
        since = status.pop(PAUSED, None)
        if paused:
            status[PAUSED] = since or page_now()
        conn.execute("UPDATE folder_links SET status = ? WHERE id = ?", (json.dumps(status), link_id))
    return get_link(link_id)


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
    (``_remove_written``). The row goes whatever the disk allows. A remote
    source's token stays on that server (whoever minted it revokes it:
    the desktop app does when it drops a link it made)."""
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
        _polled.pop(link_id, None)
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


def remote_server(link: dict, timeout: float = 120) -> gamma_sync.Server:
    """The other Gamma server a link reads, with its token (``get_link``
    with ``with_token``). A round's requests may wait two minutes on a
    silent server (a large PDF); a question asked between rounds
    ``ASK_TIMEOUT_S``."""
    return gamma_sync.Server(link["remote_url"], link.get("token") or "", timeout=timeout)


def source_of(link: dict):
    """What the link's rounds read: the remote server over HTTP, or this
    server's workspace in-process."""
    if link["remote_url"]:
        return remote_server(link).source(link["folder_id"])
    return LocalSource(link["workspace_id"], link["folder_id"])


def _write_status(link_id: str, status: dict, cursor: str | None = None) -> None:
    """A round's status, written whole but for the pause: that stays as
    stored, whatever the round read when it began (``set_paused``)."""
    with _status_guard, connect_users_db() as conn:
        r = conn.execute("SELECT status FROM folder_links WHERE id = ?", (link_id,)).fetchone()
        since = _status_of(r[0]).get(PAUSED) if r else None
        status = {k: v for k, v in status.items() if k != PAUSED}
        if since:
            status[PAUSED] = since
        if cursor is None:
            conn.execute("UPDATE folder_links SET status = ? WHERE id = ?", (json.dumps(status), link_id))
        else:
            conn.execute("UPDATE folder_links SET status = ?, cursor = ? WHERE id = ?", (json.dumps(status), cursor, link_id))


def _failed(link: dict, error: str) -> dict:
    status = {**link["status"], "running": False, "last_error": error, "failed_at": time.time()}
    _write_status(link["id"], status)
    log.warning(f"[folders] {link['path']}: {error}")
    return status


def run_link(link_id: str, *, full: bool = False, force: bool = False) -> dict | None:
    """One round of the link now, in this thread: its status afterwards
    (``{running, last_sync, last_error, counts, kept, folder_path}``), the
    current status when a round is already running, None for no such
    link."""
    link = get_link(link_id)
    if link is None:
        return None
    lock = _lock(link_id)
    if not lock.acquire(blocking=False):
        return link["status"]
    try:
        _write_status(link_id, {**link["status"], "running": True, "started_at": time.time()})
        try:
            link = get_link(link_id, with_token=True) or link
            state = _ensure_state(link)
            rnd = gamma_sync.Round(state, source_of(link), full=full, force=force, say=lambda *_: None)
            counts = rnd.run()
            status = {"running": False, "last_sync": page_now(), "last_error": "", "counts": counts,
                      "kept": [{"path": p, "why": w} for p, w in rnd.kept[:KEPT_SHOWN]],
                      "folder_path": state.state.get("folder_path") or []}
            _write_status(link_id, status, cursor=str(state.state.get("cursor", "")))
        except (gamma_sync.SyncError, LinkError, OSError) as e:
            status = _failed(link, str(e))
        return status
    finally:
        lock.release()


def run_in_background(link_id: str, **kw) -> None:
    threading.Thread(target=run_link, args=(link_id,), kwargs=kw, daemon=True,
                     name=f"folder-link-{link_id[:8]}").start()


def _newest_seq(ws: str) -> int:
    with connect_pages_db(ws) as conn:
        return newest_change_seq(conn)


def _age(stamp) -> float:
    return time.time() - float(stamp) if stamp else float("inf")


def _remote_moved(link: dict) -> bool:
    """Whether the remote server's change feed moved past the link's
    cursor — asked at most every ``REMOTE_POLL_S``. A server that cannot
    be reached or refuses the token counts as a failed round, so the retry
    wait applies and the status says why."""
    if time.time() - _polled.get(link["id"], 0.0) < REMOTE_POLL_S:
        return False
    _polled[link["id"]] = time.time()
    try:
        keyed = get_link(link["id"], with_token=True) or link
        return gamma_sync.changed_since(remote_server(keyed, timeout=ASK_TIMEOUT_S), link["cursor"])
    except (gamma_sync.SyncError, LinkError) as e:
        _failed(link, str(e))
        return False


def due(link: dict) -> bool:
    """Whether the loop runs the link now: never while it is paused, not
    while a round runs (unless that mark is stale), not for ``RETRY_S``
    after a failure, and otherwise only when its source moved past the
    cursor it holds — or it has never synced."""
    st = link["status"]
    if st.get(PAUSED):
        return False
    if st.get("running") and _age(st.get("started_at")) < STALE_RUN_S:
        return False
    if st.get("last_error"):
        return _age(st.get("failed_at")) >= RETRY_S
    if not st.get("last_sync"):
        return True
    if link["remote_url"]:
        return _remote_moved(link)
    return str(_newest_seq(link["workspace_id"])) != link["cursor"]


def tick() -> None:
    """The loop's round: every due link. A link of this server's workspace
    runs here, one after another; a link with a remote source runs in a
    thread of its own (its ``running`` mark keeps the next tick off it), so
    a slow or silent server never holds up the rest. A link whose workspace
    on this server is gone is forgotten. Nothing runs where links are not
    kept (``config.folder_links_enabled``): a row left from before stays as
    it is."""
    if not config.folder_links_enabled():
        return
    for link in all_links():
        if not link["remote_url"] and not workspaces.get(link["workspace_id"]):
            delete_link(link["id"])
            continue
        if due(link):
            if link["remote_url"]:
                run_in_background(link["id"])
            else:
                run_link(link["id"])
