"""Links to disk: a folder of a workspace kept as a directory on this
computer, or a page that follows one PDF file on it (docs/dev/folder_sync.md
"Folders kept by the desktop app").

Only the desktop app's own server keeps them: the local server it runs as
the user, started with ``GAMMA_FOLDER_LINKS``
(``config.folder_links_enabled``). Any other
server, a NAS among them, answers the API with 404 and its tick does
nothing; it only serves the folder reads a link elsewhere asks for.

A link is a row of users.db ``folder_links``. A **folder link** (kind
``folder``) holds the workspace, the folder (a folder block id, or ``root``
for the whole library), the directory's full path, whether notes files are
written, whether PDFs changed there go back to Gamma (``push``), the
change-log seq the last round saw, and that round's status. The directory
itself is the client's: a ``gamma_sync.Round`` over a **source** —
``LocalSource``, which reads a workspace of this server in-process, or the
client's own ``gamma_sync.RemoteSource``, which reads a workspace of
*another* Gamma server over HTTP with a token of that server
(``remote_url``, ``token``; the desktop app keeps a NAS folder on a PC this
way, no clone needed) — so the state file in the directory, the layout,
and the rules — only files a round wrote are ever touched — are exactly
what ``gamma_sync.py`` writes on a PC. With push on, the round first sends
the PDFs changed on disk through a **sink**: ``LocalSink``, which writes a
workspace of this server in-process as the account that made the link, or
the client's own ``gamma_sync.RemoteSink`` with a token that may write.

A **file link** (kind ``file``) makes the PDF at its path the page
``page_id``'s file each time the file changes, and never writes the file:
a ``gamma_sync.Push`` of that one file through the same sinks, with what it
knows of the file in the row's ``state``.

Rounds run from ``tick`` (the app's periodic loop, every ``TICK_S``) for
a link whose source moved past its cursor — a local workspace's change
log, read directly; a remote server's change feed, asked at most every
``REMOTE_POLL_S`` and waited on at most ``ASK_TIMEOUT_S`` — or whose files
on disk moved (a file link's file, the PDFs of a folder link that pushes),
in the background when a link is made or asked to sync, and inline for a
sync asked to wait. A remote link's round from the tick runs in a thread of
its own. One round per link at a time (``_locks``); a round that failed is
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
from fastapi import HTTPException

from . import config, folder_sync, gamma_sync, workspaces
from .blocks_store import folder_paths, load_json, newest_change_seq
from .db import connect_pages_db, connect_users_db, page_now
from .logbuf import log
from .publisher_sessions import seal, unseal
from .storage import find_upload_file, store_pdf_stream

TICK_S = 30            # how often the loop looks for links whose source changed
REMOTE_POLL_S = 60     # how often a link asks its remote server whether anything changed
ASK_TIMEOUT_S = 15     # how long that question, or the check of a new link's token, may wait
MAX_LINKS = 10         # folders per workspace
MAX_FILE_LINKS = 50    # files followed per workspace
RETRY_S = 300          # a link whose last round failed waits this long for the next try
STALE_RUN_S = 600      # a "running" older than this (the process died) is not running
KEPT_SHOWN = 50        # kept files listed in the status

_COLS = ("id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, status, remote_url, token, token_id, "
         "kind, page_id, push, state")
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_status_guard = threading.Lock()  # one read-modify-write of a status at a time (_write_status, set_paused)
_polled: dict[str, float] = {}   # link id -> when its remote server was last asked
_files: dict[str, tuple] = {}    # link id -> (its state file's stat, the files in it): _disk_moved
PAUSED = "paused_at"   # the status key of a pause: the user's, which no round writes


class LinkError(ValueError):
    pass


def _absolute(text: str) -> bool:
    """A full path: absolute, a drive, a share, the home directory, or a path
    from the top of a drive (Windows does not count that one absolute)."""
    return os.path.isabs(text) or bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith(("~", "/", "\\"))


def _in_data_dir(target: Path) -> bool:
    """Whether ``target`` (resolved) is Gamma's data directory or inside it:
    by its spelling, and by identity (``os.stat``'s device and file id), so
    that another spelling of the same place counts too — a device path
    (``//?/C:/...``) or an administrative share (``//host/C$/...``), which
    ``resolve`` keeps as given."""
    data_dir = config.DATA_DIR.resolve()
    if target == data_dir or data_dir in target.parents:
        return True
    try:
        data = data_dir.stat()
    except OSError:
        return False
    if not data.st_ino:   # a filesystem without file ids: the spelling is all there is
        return False
    for place in (target, *target.parents):
        try:
            st = place.stat()
        except OSError:
            continue   # not made yet (a directory a link will make), or not reachable
        if (st.st_dev, st.st_ino) == (data.st_dev, data.st_ino):
            return True
    return False


def clean_path(text: str) -> str:
    """A directory as a link stores it: the full path the user picked, as
    this computer resolves it. A relative path, a filesystem root, and
    anything inside Gamma's data directory are refused."""
    text = str(text or "").strip()
    if not _absolute(text):
        raise LinkError("Give the full path of a directory on this computer.")
    target = Path(text).expanduser().resolve()
    if target.parent == target:
        raise LinkError("Pick a directory, not the root of a drive.")
    if _in_data_dir(target):
        raise LinkError("That directory is inside Gamma's data directory; pick another.")
    return str(target)


def clean_file_path(text: str) -> str:
    """A followed file as a link stores it: the full path of a PDF the user
    picked, as this computer resolves it. A relative path, another kind of
    file, a file that is not there, and anything inside Gamma's data
    directory are refused."""
    text = str(text or "").strip()
    if not _absolute(text):
        raise LinkError("Give the full path of a PDF on this computer.")
    target = Path(text).expanduser().resolve()
    if target.suffix.lower() != ".pdf":
        raise LinkError("Pick a PDF file.")
    if _in_data_dir(target):
        raise LinkError("That file is inside Gamma's data directory; pick another.")
    if not target.is_file():
        raise LinkError(f"There is no file {target}.")
    return str(target)


def _unseal(sealed: str) -> str:
    """The stored token (``publisher_sessions.seal``, the mirrors' way)."""
    try:
        return unseal(sealed)
    except (InvalidToken, ValueError) as e:
        raise LinkError("the stored token cannot be read: the data directory's key changed") from e


def _object_of(text: str) -> dict:
    """A JSON object column (a status, a state) as a dict; {} for anything
    else."""
    try:
        value = json.loads(text or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _row(r, with_token: bool = False) -> dict:
    link = {"id": r[0], "workspace_id": r[1], "folder_id": r[2], "path": r[3], "notes": bool(r[4]),
            "created_by": r[5], "created_at": r[6], "cursor": r[7] or "", "status": _object_of(r[8]),
            "remote_url": r[9] or "", "token_id": r[11] or "", "kind": r[12] or "folder", "page_id": r[13] or "",
            "push": bool(r[14]), "state": _object_of(r[15])}
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
            "folder": link["folder_id"], "folder_path": [], "notes": link["notes"], "push": link["push"], "files": {},
            "dirs": [], "cursor": ""}


def _ensure_state(link: dict) -> gamma_sync.Link:
    """A folder link's directory and its state file, made when missing, its
    ``notes`` and ``push`` the row's. One left by an earlier link to the
    same folder of the same server is adopted, its files staying the
    round's to keep up to date; one of another folder is refused."""
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
            existing.state["push"] = link["push"]
            existing.save()
            return existing
        fresh = gamma_sync.Link(dest, _state_for(link))
        fresh.save()
        return fresh
    except (OSError, gamma_sync.SyncError) as e:
        raise LinkError(f"Cannot write {dest}: {e}") from e


def _insert(link: dict, token: str = "") -> dict:
    """The row, once a folder link's directory has its state file: the path
    must not be another link's (UNIQUE, ignoring case), and the workspace
    keeps at most ``MAX_LINKS`` folders and follows at most
    ``MAX_FILE_LINKS`` files."""
    folder = link["kind"] == "folder"
    taken = f"Another link already {'writes' if folder else 'follows'} {link['path']}."
    with connect_users_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM folder_links WHERE workspace_id = ? AND kind = ?",
                             (link["workspace_id"], link["kind"])).fetchone()[0]
        if folder and count >= MAX_LINKS:
            raise LinkError(f"A workspace keeps at most {MAX_LINKS} folders on disk.")
        if not folder and count >= MAX_FILE_LINKS:
            raise LinkError(f"A workspace follows at most {MAX_FILE_LINKS} files on disk.")
        if conn.execute("SELECT 1 FROM folder_links WHERE path = ?", (link["path"],)).fetchone():
            raise LinkError(taken)
        if folder:
            _ensure_state(link)
        try:
            conn.execute(f"INSERT INTO folder_links ({_COLS}) VALUES ({', '.join('?' * 16)})",
                         (link["id"], link["workspace_id"], link["folder_id"], link["path"], int(link["notes"]), link["created_by"],
                          link["created_at"], "", "{}", link["remote_url"], seal(token), link["token_id"], link["kind"],
                          link["page_id"], int(link["push"]), json.dumps(link["state"])))
        except sqlite3.IntegrityError as e:   # the path, taken meanwhile (UNIQUE, ignoring case)
            raise LinkError(taken) from e
    return link


def _new(ws: str, path: str, user_id: str, **fields) -> dict:
    """A link not stored yet: a folder link unless ``fields`` say otherwise."""
    return {"id": uuid.uuid4().hex, "workspace_id": ws, "kind": "folder", "folder_id": "", "page_id": "", "path": path,
            "notes": False, "push": False, "state": {}, "created_by": user_id, "created_at": page_now(), "cursor": "",
            "status": {}, "remote_url": "", "token_id": "", **fields}


def create_link(ws: str, folder_id: str, path: str, notes: bool, user_id: str, push: bool = False) -> dict:
    """A new link to a folder of this server's workspace ``ws``: the folder
    must exist (or be ``root``), the path is cleaned (``clean_path``) and
    must not be another link's, the directory is made with its state
    file. ``push``: PDFs changed there go back to Gamma."""
    path = clean_path(path)
    with connect_pages_db(ws) as conn:
        if folder_id != folder_sync.ROOT and folder_id not in folder_paths(conn):
            raise LinkError("That folder does not exist.")
    return _insert(_new(ws, path, user_id, folder_id=folder_id, notes=bool(notes), push=bool(push)))


def _remote(url: str, token: str, workspace: str) -> tuple[gamma_sync.Server, dict]:
    """The other Gamma server at ``url`` with ``token``, and what it says of
    the token (``/api/sync/whoami``: its ``workspace``, which must be
    ``workspace`` when one is given, and its ``scope``). Waits at most
    ``ASK_TIMEOUT_S``."""
    url = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+(/\S*)?$", url):
        raise LinkError("Give the other server's address as http(s)://host[:port].")
    if not token:
        raise LinkError("A token of the other server is needed.")
    server = gamma_sync.Server(url, token, timeout=ASK_TIMEOUT_S)
    try:
        who = server.get_json("/api/sync/whoami")
    except gamma_sync.SyncError as e:
        raise LinkError(str(e)) from e
    remote_ws = (who.get("workspace") or {}).get("id") or ""
    if not remote_ws:
        raise LinkError("The other server did not say which workspace the token opens.")
    if workspace and workspace != remote_ws:
        raise LinkError("The token belongs to another workspace of that server.")
    return server, who


def create_remote_link(url: str, token: str, token_id: str, workspace: str, folder_id: str, path: str, notes: bool,
                       user_id: str, push: bool = False) -> dict:
    """A new link whose source is a folder on *another* Gamma server, read
    with a token of that server (read access is enough unless ``push``,
    which takes a token that may write; the token is kept Fernet-encrypted,
    ``token_id`` for whoever revokes it there later). The token is checked
    on that server (``_remote``), and the folder must exist there."""
    path = clean_path(path)
    server, who = _remote(url, token, workspace)
    if push and who.get("scope") != "write":
        raise LinkError("Sending PDFs back needs a token that may write.")
    try:
        tree = server.get_json("/api/sync/folders")
    except gamma_sync.SyncError as e:
        raise LinkError(str(e)) from e
    folders = {f["id"]: f["path"] for f in tree.get("folders", []) if f.get("id")}
    if folder_id != folder_sync.ROOT and folder_id not in folders:
        raise LinkError("That folder does not exist on the other server.")
    link = _new(who["workspace"]["id"], path, user_id, folder_id=folder_id, notes=bool(notes), push=bool(push),
                remote_url=server.url, token_id=str(token_id or ""))
    return _insert(link, token)


def create_file_link(ws: str, page_id: str, path: str, user_id: str) -> dict:
    """A new file link: the PDF at ``path`` (``clean_file_path``), not
    another link's, becomes the page ``page_id`` of this server's workspace
    ``ws`` each time it changes. The page must be one of the library."""
    path = clean_file_path(path)
    page = _page(ws, page_id)
    if page is None:
        raise LinkError("That page does not exist.")
    return _insert(_new(ws, path, user_id, kind="file", page_id=page_id, push=True, state={"title": page["title"]}))


def create_remote_file_link(url: str, token: str, token_id: str, workspace: str, page_id: str, path: str,
                            user_id: str) -> dict:
    """A new file link to a page on *another* Gamma server, sent there with
    a token of it that may write (kept as ``create_remote_link`` keeps
    one). The token is checked on that server (``_remote``), and the page
    must exist there."""
    path = clean_file_path(path)
    server, who = _remote(url, token, workspace)
    if who.get("scope") != "write":
        raise LinkError("Sending a file to that server needs a token that may write.")
    try:
        page = gamma_sync.RemoteSink(server).current(page_id) if page_id else None
    except gamma_sync.SyncError as e:
        raise LinkError(str(e)) from e
    if page is None:
        raise LinkError("That page does not exist on the other server.")
    link = _new(who["workspace"]["id"], path, user_id, kind="file", page_id=page_id, push=True,
                state={"title": page["title"]}, remote_url=server.url, token_id=str(token_id or ""))
    return _insert(link, token)


def set_notes(link_id: str, notes: bool) -> dict | None:
    """Whether a folder link writes notes files; None for no such link. Only
    the row changes: the directory's state file is the rounds' (one may be
    running and save its files map over a write made here), and each round
    first copies the row's ``notes`` and ``push`` into it (``_ensure_state``)."""
    link = get_link(link_id)
    if link is None:
        return None
    if link["kind"] == "file":
        raise LinkError("A followed file has no notes file.")
    link["notes"] = bool(notes)
    with connect_users_db() as conn:   # the next round copies it into the state file (_ensure_state)
        conn.execute("UPDATE folder_links SET notes = ? WHERE id = ?", (int(link["notes"]), link_id))
    return link


def set_push(link_id: str, push: bool, token: str = "", token_id: str = "") -> dict | None:
    """Whether a folder link sends PDFs changed on disk back to Gamma; None
    for no such link. A link to another server turning it on needs a token
    of it that may write: ``token`` (with its ``token_id``), checked there
    and stored in place of the one the link had, or else the stored one. A
    file link always sends: LinkError. Only the row changes, as in
    ``set_notes``."""
    link = get_link(link_id, with_token=True)
    if link is None:
        return None
    if link["kind"] == "file":
        raise LinkError("A followed file always sends its new versions.")
    if push and link["remote_url"]:
        _server, who = _remote(link["remote_url"], token or link["token"], link["workspace_id"])
        if who.get("scope") != "write":
            raise LinkError("Sending PDFs back needs a token that may write.")
    link["push"] = bool(push)
    with connect_users_db() as conn:   # the next round copies it into the state file (_ensure_state)
        conn.execute("UPDATE folder_links SET push = ? WHERE id = ?", (int(link["push"]), link_id))
        if push and link["remote_url"] and token:
            conn.execute("UPDATE folder_links SET token = ?, token_id = ? WHERE id = ?",
                         (seal(token), str(token_id or ""), link_id))
    return get_link(link_id)


def set_paused(link_id: str, paused: bool) -> dict | None:
    """Pause the link — the tick leaves it alone; a sync asked for still
    runs, and a round already running finishes — or resume it. The pause
    is ``status.paused_at``, when it began; None for no such link."""
    with _status_guard, connect_users_db() as conn:
        r = conn.execute("SELECT status FROM folder_links WHERE id = ?", (link_id,)).fetchone()
        if r is None:
            return None
        status = _object_of(r[0])
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
    """Forget the link; with ``remove_files`` also take back what a folder
    link wrote (``_remove_written``; a followed file is never touched). The
    row goes whatever the disk allows. A remote source's token stays on that
    server (whoever minted it revokes it: the desktop app does when it drops
    a link it made)."""
    link = get_link(link_id)
    if link is None:
        return False
    with _lock(link_id):
        if remove_files and link["kind"] == "folder":
            try:
                _remove_written(dest_of(link))
            except OSError as e:
                log.warning(f"[folders] removing {link['path']}: {e}")
        with connect_users_db() as conn:
            conn.execute("DELETE FROM folder_links WHERE id = ?", (link_id,))
        _polled.pop(link_id, None)
        _files.pop(link_id, None)
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


def _page(ws: str, page_id: str) -> dict | None:
    """``{doc_id, filename, title}`` of a page of this server's workspace
    (``""`` for no PDF or no file name); None when ``page_id`` is no page of
    the library: not there, a block inside a page, or in the trash."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row or row[0] != "root":
        return None
    props = load_json(row[2] or "{}") or {}
    return {"doc_id": str(props.get("doc_id") or ""), "filename": str(props.get("original_filename") or ""),
            "title": row[1] or ""}


class LocalSink:
    """Where a push writes in this server's own workspace, as the account
    that made the link: the three calls of ``gamma_sync.RemoteSink``
    without HTTP. Each write first asks whether the account may still edit
    the workspace."""

    def __init__(self, ws: str, user_id: str):
        self.ws, self.user_id = ws, user_id

    def _may_write(self) -> None:
        if not workspaces.at_least(workspaces.role_of(self.ws, self.user_id), "editor"):
            raise gamma_sync.SyncError("you may no longer edit this workspace")

    def store(self, path: Path) -> str:
        """The file stored as an upload is (spooled, hashed, the quota and
        the PDF header checked), under its doc_id."""
        self._may_write()
        try:
            with open(path, "rb") as f:
                doc_id, _existed, _size = store_pdf_stream(self.ws, f)
        except ValueError as e:
            raise gamma_sync.SyncError(str(e)) from e
        except HTTPException as e:   # the quota
            raise gamma_sync.SyncError(str(e.detail)) from e
        return doc_id

    def replace(self, page_id: str, doc_id: str, filename: str) -> dict:
        from .routers.pages import replace_attachment  # local: keep gamma.* free of the routers package

        self._may_write()
        try:
            return replace_attachment(self.ws, page_id, doc_id, None, filename, actor=self.user_id, owner=self.user_id,
                                      keep_old_days=gamma_sync.KEEP_OLD_DAYS)
        except HTTPException as e:   # the page gone, or the bytes another page's (pages.HeldElsewhere)
            raise gamma_sync.SyncError(str(e.detail)) from e

    def current(self, page_id: str) -> dict | None:
        return _page(self.ws, page_id)


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


def sink_of(link: dict):
    """Where the link's pushes write: the remote server over HTTP with the
    link's token (``get_link`` with ``with_token``), or this server's
    workspace in-process as the account that made the link."""
    if link["remote_url"]:
        return gamma_sync.RemoteSink(remote_server(link))
    return LocalSink(link["workspace_id"], link["created_by"])


def _write_status(link_id: str, status: dict, cursor: str | None = None, state: dict | None = None) -> None:
    """A round's status, written whole but for the pause: that stays as
    stored, whatever the round read when it began (``set_paused``). With it
    a folder link's ``cursor``, or a file link's ``state``."""
    with _status_guard, connect_users_db() as conn:
        r = conn.execute("SELECT status FROM folder_links WHERE id = ?", (link_id,)).fetchone()
        since = _object_of(r[0]).get(PAUSED) if r else None
        status = {k: v for k, v in status.items() if k != PAUSED}
        if since:
            status[PAUSED] = since
        conn.execute("UPDATE folder_links SET status = ? WHERE id = ?", (json.dumps(status), link_id))
        if cursor is not None:
            conn.execute("UPDATE folder_links SET cursor = ? WHERE id = ?", (cursor, link_id))
        if state is not None:
            conn.execute("UPDATE folder_links SET state = ? WHERE id = ?", (json.dumps(state), link_id))


def _failed(link: dict, error: str) -> dict:
    status = {**link["status"], "running": False, "last_error": error, "failed_at": time.time()}
    _write_status(link["id"], status)
    log.warning(f"[folders] {link['path']}: {error}")
    return status


def run_link(link_id: str, *, full: bool = False, force: bool = False) -> dict | None:
    """One round of the link now, in this thread: its status afterwards
    (``_folder_round``, ``_file_round``), the current status when a round
    is already running, None for no such link. ``full`` and ``force`` are a
    folder link's."""
    link = get_link(link_id)
    if link is None:
        return None
    lock = _lock(link_id)
    if not lock.acquire(blocking=False):
        return link["status"]
    try:
        started = time.time()
        _write_status(link_id, {**link["status"], "running": True, "started_at": started})
        try:
            link = get_link(link_id, with_token=True) or link
            if link["kind"] == "file":
                return _file_round(link, started)
            return _folder_round(link, full, force)
        except (gamma_sync.SyncError, LinkError, OSError) as e:
            return _failed(link, str(e))
        except Exception as e:  # noqa: BLE001 — never leave the link "running" with no reason shown
            log.exception(f"[folders] {link['path']}: the round failed")
            return _failed(link, f"unexpected error: {e!r}")
    finally:
        lock.release()


def _folder_round(link: dict, full: bool, force: bool) -> dict:
    """A folder link's round (``gamma_sync.Round``), the PDFs changed on
    disk sent back first when the link pushes. → ``{running, last_sync,
    last_error, counts, kept, folder_path}``, each of ``kept`` ``{path, why,
    force}``: ``force`` when a forced round would replace the file with
    Gamma's (one changed on disk), not for one a send could not deliver or
    one in the way."""
    state = _ensure_state(link)
    sink = sink_of(link) if link["push"] else None
    rnd = gamma_sync.Round(state, source_of(link), sink=sink, full=full, force=force, say=lambda *_: None)
    counts = rnd.run()
    status = {"running": False, "last_sync": page_now(), "last_error": "", "counts": counts,
              "kept": [{"path": p, "why": w, "force": f} for p, w, f in rnd.kept[:KEPT_SHOWN]],
              "folder_path": state.state.get("folder_path") or []}
    _write_status(link["id"], status, cursor=str(state.state.get("cursor", "")))
    return status


def _file_round(link: dict, started: float) -> dict:
    """A file link's round: one push of its file (``gamma_sync.Push``, mode
    "file"), what it learnt of the file kept in the row's ``state``. A file
    the page did not take (the page gone, the bytes another page's, the
    account no longer an editor) fails the link, so ``RETRY_S`` applies; a
    file still being written only waits. → ``{running, started_at,
    last_sync, last_error, failed_at?, counts, waiting, last_sent, title,
    relocation_error?}``: ``last_sent`` and ``relocation_error`` are the
    last send's, ``title`` the page's as the last send left it."""
    before = link["status"]
    entry = {**link["state"], "page": link["page_id"]}
    push = gamma_sync.Push([(link["path"], dest_of(link), entry)], sink_of(link), mode="file", say=lambda *_: None)
    counts = push.run()
    status = {"running": False, "started_at": started, "last_sync": page_now(), "last_error": "", "counts": counts,
              "waiting": push.waiting[0][1] if push.waiting else "", "last_sent": before.get("last_sent", ""),
              "title": link["state"].get("title", "")}
    if push.sent:
        answer = push.sent[0][1]
        status.update(last_sent=page_now(), title=answer.get("content") or status["title"])
        if answer.get("relocation_error"):
            status["relocation_error"] = answer["relocation_error"]
    elif before.get("relocation_error"):
        status["relocation_error"] = before["relocation_error"]
    if push.kept:
        status.update(last_error=push.kept[0][1], failed_at=time.time())
        log.warning(f"[folders] {link['path']}: {push.kept[0][1]}")
    state = {k: v for k, v in entry.items() if k != "page"}
    state["title"] = status["title"]
    _write_status(link["id"], status, state=state)
    return status


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


def _disk_moved(link: dict) -> bool:
    """Whether a PDF of a folder link's directory moved since its round
    recorded it (``gamma_sync.pdfs_moved``). The state file is read again
    only when it changed itself (``_files``), so a quiet tick costs a stat
    per PDF."""
    dest = dest_of(link)
    try:
        st = (dest / gamma_sync.STATE).stat()
    except OSError:
        return False
    stamp = (st.st_mtime_ns, st.st_size)
    cached = _files.get(link["id"])
    if cached is None or cached[0] != stamp:
        try:
            files = gamma_sync.Link.load(dest).state["files"]
        except (gamma_sync.SyncError, OSError, ValueError):
            return False
        _files[link["id"]] = cached = (stamp, files)
    return gamma_sync.pdfs_moved(dest, cached[1])


def due(link: dict) -> bool:
    """Whether the loop runs the link now: never while it is paused, not
    while a round runs (unless that mark is stale), not for ``RETRY_S``
    after a failure, and always when it has never synced. Otherwise a file
    link when its file moved (``gamma_sync.moved``), and a folder link when
    its source moved past the cursor it holds or, when it pushes, a PDF in
    its directory moved (``_disk_moved``)."""
    st = link["status"]
    if st.get(PAUSED):
        return False
    if st.get("running") and _age(st.get("started_at")) < STALE_RUN_S:
        return False
    if st.get("last_error"):
        return _age(st.get("failed_at")) >= RETRY_S
    if not st.get("last_sync"):
        return True
    if link["kind"] == "file":
        return gamma_sync.moved(dest_of(link), link["state"])
    if link["push"] and _disk_moved(link):
        return True
    if link["remote_url"]:
        return _remote_moved(link)
    return str(_newest_seq(link["workspace_id"])) != link["cursor"]


def tick() -> None:
    """The loop's round: every due link. A link of this server's workspace
    runs here, one after another; a link to another server, folder or file,
    runs in a thread of its own (its ``running`` mark keeps the next tick
    off it), so a slow or silent server never holds up the rest. A link
    whose workspace on this server is gone is forgotten. Nothing runs where
    links are not kept (``config.folder_links_enabled``): a row left from
    before stays as it is."""
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
