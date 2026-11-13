"""Synced vaults (docs/research/vaults.md; docs/dev/synced_vaults.md): a
folder of a workspace and a directory on this computer kept in step, or a
page that follows one PDF file on it.

Only the desktop app's own server keeps them: the local server it runs as
the user, started with ``GAMMA_FOLDER_LINKS``
(``config.folder_links_enabled``). Any other
server, a NAS among them, answers the API with 404 and its tick does
nothing; it only serves the folder reads a link elsewhere asks for.

A link is a row of users.db ``folder_links``; each is a **synced vault**, a
scope (``kind``) with a direction: ``pull``, whether Gamma's changes reach
the disk, and ``push``, what goes the other way (``PUSH_OFF``, ``PUSH_PDFS``,
``PUSH_ALL``), words in the API (``push_word``, ``push_value``). A **folder
link** (kind ``folder``) holds the workspace, the folder (a folder block id,
or ``root`` for the whole library), the directory's full path, whether notes
files are written, its direction, the change-log seq the last round saw,
and that round's status.

Every folder link is the vault engine's (``engine``): its rounds are
gamma/vault/sync.py's, push then pull, notes both ways, its directory holding
a vault marker (``.gamma/vault.json``, its id in the row's ``state.vault``).
One is made from a directory (``new_folder``: a new folder named after it) or
from a folder. Its remote is this server's workspace, or a folder of
*another* Gamma server reached over HTTP with a token of it (``remote_url``,
``token``; gamma/vault/http_remote.py: the desktop app keeps a NAS folder on a PC
this way, no clone needed), which pulls with a token that may read and
pushes with one that may write. A folder kept on disk before synced vaults,
its state the gamma-sync client's file in the directory (``older``), is taken
onto the engine at its first round (gamma/vault/upgrade.py); the gamma-sync
client alone runs ``gamma_sync.Round`` now.

A **file link** (kind ``file``) makes the PDF at its path the page
``page_id``'s file each time the file changes, and never writes the file:
a ``gamma_sync.Push`` of that one file through a **sink**, ``LocalSink``,
which writes a workspace of this server in-process as the account that made
the link, or the client's own ``gamma_sync.RemoteSink`` with a token that may
write, with what it knows of the file in the row's ``state``.

**Refusals** are one function, ``check_vault``, asked when a link is made or
changed and before each round (a refused round is ``status.stopped``), and
by the mirror and Zotero when they would take a workspace a vault pushes
everything into (``pushing_vault``). Vaults do not overlap on this computer
or, when both push, in Gamma; a vault made from a directory takes in the
vaults of one file inside it (``_take_in``).

A **work folder** (``work_folder_link``; docs/dev/work_folders.md) is a vault
of a folder of this server that pushes everything: its folder carries the
vault's mark (``properties.vault = {pull, library}``, ``_set_mark``), each
file it tracks is a page stamped as that folder's, and what it tracks is its
directory's ``.gammaignore``, chosen in the web app's tracking dialog
(gamma/work_folders/selection.py). One made with ``choose`` waits paused and
*choosing* until the dialog saves: every way into a round refuses it until
then, and removing it cancels it, taking back what making it made
(``_cancel``). A vault from before work folders is taken over at its first
round, and one whose push came down is let go at its next
(gamma/work_folders/takeover.py).

Rounds run from ``tick`` (the app's periodic loop, every ``TICK_S``): a
vault of this server scans each time; a vault of another server has a whole
round when that server's change feed moved past its cursor (asked at most
every ``REMOTE_POLL_S``, waited on at most ``ASK_TIMEOUT_S``), else, when it
pushes, a round of push alone, which asks that server nothing while the disk
is quiet; a file link when its file moved. Rounds run in the background too
when a link is made or asked to sync (``POST /hint`` too: a window of the
app got the focus), marked ``running`` before the request is answered, and
inline for a sync asked to wait. A remote link's round from the tick runs in
a thread of its own. One round per link at a time (``_locks``), and a link
reads ``running`` only while that lock is held, so a mark a server stopped
mid-round left is not taken for a round; a round asked for while the lock is
held follows it (``_again``), the link reading ``running`` from then until
that round ends, whatever held the lock, and a round that failed is tried
again after ``RETRY_S``. A round says how far it is in the status while it
runs (``progress``, ``_progress_writer``). A paused link (``set_paused``) is
left out of the tick and runs only when asked to sync.

The **hot loop** (``hot_tick``, every ``HOT_S``) gives a page of a vault of
this server a round of its own (``vault.sync.sync_page``, no scan): a page
written in Gamma, once ``HOT_S`` passed since its last write
(``ops.commit_listeners`` marks it, ``_dirty``, and nothing else), and a page
open in a window whose file's stat moved. A mark stays until a round of its
page ran. The same loop writes a work folder's feedback files
(gamma/feedback/files.py) within a pass of a change to its threads, as every
whole round of a work folder does after its sync (``status.feedback``).

**Removing a vault** (``delete_link``) leaves its pages as they are: the PDFs
it read in place are stored first, and one that cannot be stops the removal
before anything is undone; then its feedback files and its marker go, and
with ``files=trash``
the files its rounds made move to its trash. A vault whose disk is not there
keeps its pages reading their files, missing, to be located. A work folder's
files that are not in the library go to Recently deleted instead, and the
directory folders that leaves empty go (``_release_work``).
"""

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from . import collab, config, folder_sync, gamma_sync, md_lens, storage, workspaces
from .auth import DISK_CLIENT
from .blocks_store import (FOLDERS, PARENT_PAGE, TREES, filing, folder_path, folder_paths, folder_subtree_ids,
                           live_pages, load_json, load_props, new_block_id, page_in_folder, page_root_id, pages_in_folder)
from .db import connect_pages_db, connect_users_db, connect_vaults_db, page_now
from .vault import disk as vault_disk, store as vault_store
from .documents.model import document_at, in_place, own_hash, page_document
from .jobs import error_text
from .logbuf import log
from .ops import OpError, commit_listeners, commit_ops
from .publisher_sessions import seal, unseal
from .storage import store_file_stream, store_pdf_stream
from .work_folders import marks
from .work_folders.library import SQL_OUTSIDE_LIBRARY

TICK_S = 30            # how often the loop looks for links whose source changed
HOT_S = 2              # how often the hot loop looks, and how long a page's last write rests before its round
REMOTE_POLL_S = 60     # how often a link asks its remote server whether anything changed
ASK_TIMEOUT_S = 15     # how long that question, or the check of a new link's token, may wait
ROUND_WAIT_S = 30      # how long a request that needs a round's result waits for one already running
MAX_LINKS = 10         # folders per workspace
MAX_FILE_LINKS = 50    # files followed per workspace
RETRY_S = 300          # a link whose last round failed waits this long for the next try
KEPT_SHOWN = 50        # kept files listed in the status
PROGRESS_S = 1.0       # a vault's round writes its progress to the status at most this often
PUSH_OFF, PUSH_PDFS, PUSH_ALL = 0, 1, 2   # a link's ``push``: nothing goes back, its PDFs, everything
PUSH_WORDS = {PUSH_OFF: "off", PUSH_PDFS: "pdfs", PUSH_ALL: "everything"}   # the same in the API
DIRECTIONS = ("both", "pull", "push")
COUNTS = ("added", "updated", "renamed", "removed", "kept", "unchanged", "sent", "waiting", "read", "conflicts")
_ERRORS = ("last_error", "failed_at", "stopped", "offline")   # what a round that could not run leaves in the status
# ``check_vault``'s refusal to a maker who may no longer edit the vault's workspace
REFUSED_UNSAID = "This vault is refused as things stand; only an account that may edit its workspace is told why."

_COLS = ("id, workspace_id, folder_id, path, notes, created_by, created_at, cursor, status, remote_url, token, token_id, "
         "kind, page_id, push, state, pull, library")
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_status_guard = threading.Lock()  # one read-modify-write of a status at a time (_write_status, set_paused)
_polled: dict[str, float] = {}   # link id -> when its remote server was last asked
_again: dict[str, dict] = {}     # link id -> the round asked for while one ran (``run_link``), under _guard
_starting: dict[str, int] = {}   # link id -> rounds ``_let_go`` took out of _again, not yet marked running, under _guard
_dirty: dict[tuple[str, str], float] = {}   # (workspace, page id) -> its last write: a page round owed (hot_tick)
_looked: dict[tuple[str, str], tuple] = {}  # (workspace, page id) open -> its files' stats its last page round saw
_dirty_guard = threading.Lock()
_ticking = threading.Lock()      # one tick at a time: the loop's, or one a hint started
_tick_asked = threading.Event()  # a tick asked for since the running one began: it runs once more
PAUSED = "paused_at"   # the status key of a pause: the user's, which no round writes
CHOOSING = "choosing"  # the state key of a work folder made with ``choose`` whose files are not chosen yet
CHOOSE_FIRST = "choose its files first"   # what every way into a round answers a choosing vault
CHOOSES_ONLY = "Only a vault that pushes everything chooses its files."


class LinkError(ValueError):
    pass


class NoteMoved(LinkError):
    """A file written from Gamma had changed since it was read: ``hash`` is
    what it holds now."""

    def __init__(self, hash: str):
        super().__init__("The file changed on disk since it was opened: read it again.")
        self.hash = hash


def push_word(push: int) -> str:
    """A row's ``push`` as the API says it."""
    return PUSH_WORDS[push]


def push_value(word: str) -> int:
    """A row's ``push`` from the word a client sends (``PUSH_WORDS``); any
    other is refused: LinkError."""
    values = {word: push for push, word in PUSH_WORDS.items()}
    if word not in values:
        raise LinkError("Push is off, pdfs or everything.")
    return values[word]


def engine(link: dict) -> bool:
    """Whether the link's rounds are the vault engine's (gamma/vault/sync.py):
    every folder link, one kept on disk before synced vaults too (``older``),
    which its first round takes onto the engine; a file link's are this
    module's own."""
    return link["kind"] == "folder"


def older(link: dict) -> bool:
    """Whether the link is a folder kept on disk before synced vaults, not
    taken onto the engine yet (gamma/vault/upgrade.py, at its first round):
    its row names no marker, and its directory holds the gamma-sync client's
    state file."""
    return link["kind"] == "folder" and not link["state"].get("vault")


def work_folder_link(link: dict) -> bool:
    """Whether the vault is a work folder (docs/dev/work_folders.md): one of
    a folder of this server that pushes everything. The vault alone asks:
    the op path and the readers read its folder's mark and its pages'
    stamps, which it writes."""
    return (link["kind"] == "folder" and not link["remote_url"] and link["folder_id"] != folder_sync.ROOT
            and link["push"] == PUSH_ALL)


def choosing(link: dict) -> bool:
    """Whether the work folder waits for its files to be chosen (made with
    ``choose``, not saved yet): no round runs it."""
    return bool(link["state"].get(CHOOSING))


def _set_mark(link: dict, mark: dict | None) -> None:
    """The vault's folder carries ``mark`` (``takeover.mark_of``), or none,
    written as the vault (``DISK_CLIENT``), the one writer a person's op may
    not stand in for."""
    commit_ops(link["workspace_id"], FOLDERS, [{"op": "set", "id": link["folder_id"], "props": {"vault": mark}}],
               actor=link["created_by"], client=DISK_CLIENT)


def _absolute(text: str) -> bool:
    """A full path: absolute, a drive, a share, the home directory, or a path
    from the top of a drive (Windows does not count that one absolute)."""
    return os.path.isabs(text) or bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith(("~", "/", "\\"))


def inside(path: str, parent: str) -> bool:
    """Whether ``path`` is ``parent`` or below it, as this computer compares
    names (``os.path.normcase``: ignoring case on Windows)."""
    a, b = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(parent))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _in_data_dir(target: Path) -> bool:
    """Whether ``target`` (resolved) is Gamma's data directory, inside it, or
    around it, by its spelling or by identity (``_under``): a vault around it
    would read every workspace's stored files in place and move them to its
    trash."""
    data_dir = config.DATA_DIR.resolve()
    return (target == data_dir or data_dir in target.parents or target in data_dir.parents
            or _under(target, data_dir) or _under(data_dir, target))


def _under(target: Path, top: Path) -> bool:
    """Whether ``target`` or a directory above it is ``top`` by identity
    (``os.stat``'s device and file id), so that another spelling of the same
    place counts: a device path (``//?/C:/...``) or an administrative share
    (``//host/C$/...``), which ``resolve`` keeps as given."""
    try:
        st = top.stat()
    except OSError:
        return False
    if not st.st_ino:   # a filesystem without file ids: the spelling is all there is
        return False
    for place in (target, *target.parents):
        try:
            here = place.stat()
        except OSError:
            continue   # not made yet (a directory a link will make), or not reachable
        if (here.st_dev, here.st_ino) == (st.st_dev, st.st_ino):
            return True
    return False


def _within(path: str, parent: str) -> bool:
    """``inside``, or the same by identity (``_under``): what a new place on
    this computer is compared by, which stats both."""
    return inside(path, parent) or _under(Path(path), Path(parent))


def clean_path(text: str) -> str:
    """A directory as a link stores it: the full path the user picked, as
    this computer resolves it. A relative path, a filesystem root, and
    Gamma's data directory, anything inside it and anything around it are
    refused."""
    text = str(text or "").strip()
    if not _absolute(text):
        raise LinkError("Give the full path of a directory on this computer.")
    target = Path(text).expanduser().resolve()
    if target.parent == target:
        raise LinkError("Pick a directory, not the root of a drive.")
    if _in_data_dir(target):
        raise LinkError("That directory is Gamma's data directory, inside it or around it; pick another.")
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
    status = _object_of(r[8])
    if status.get("running") and not _round_held(r[0]):
        # a mark no round of this process holds (a server stopped mid-round left it): not running,
        # however long a round that does hold it has run
        status["running"] = False
    if not status.get("running"):
        status.pop("progress", None)  # what such a round last said of itself
    if r[0] in _again or r[0] in _starting:
        # a round asked for while something held the lock (a round, a page's round, a write from the file
        # editor, a relink): it runs once the lock is let go, so a client following Sync now waits for it
        status["running"] = True
    link = {"id": r[0], "workspace_id": r[1], "folder_id": r[2], "path": r[3], "notes": bool(r[4]),
            "created_by": r[5], "created_at": r[6], "cursor": r[7] or "", "status": status,
            "remote_url": r[9] or "", "token_id": r[11] or "", "kind": r[12] or "folder", "page_id": r[13] or "",
            "push": r[14] or PUSH_OFF, "state": _object_of(r[15]), "pull": r[16], "library": bool(r[17])}
    if with_token:
        link["token"] = _unseal(r[10] or "")
    return link


def visible_links(user_id: str) -> list[dict]:
    """Every link one account may see: those of this server's workspaces it
    has a role in, and those it made, with a remote source or of a workspace
    it has no role in any more (the directory is on its computer: it may
    still pause or remove the vault)."""
    return [link for link in all_links()
            if link["created_by"] == user_id or not link["remote_url"] and workspaces.role_of(link["workspace_id"], user_id)]


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


def _round_held(link_id: str) -> bool:
    """Whether the link's lock is held in this process (a round holds it from
    ``_take`` to ``_let_go``): what its ``running`` is read as."""
    with _guard:
        lock = _locks.get(link_id)
    return lock is not None and lock.locked()


def _kept_before(link: dict, root: Path) -> bool:
    """Whether ``root`` holds the folder of a link kept on disk before synced
    vaults, the same folder of the same server as ``link``: a link made now
    goes on with it, as an ``older`` one that its first round takes onto the
    engine. LinkError for the files of another folder, or of a gamma-sync of
    another version."""
    from .vault import upgrade as vault_upgrade  # local: it imports this module

    try:
        state = vault_upgrade.old_state(root)
    except vault_disk.Offline:
        return False
    except gamma_sync.SyncError as e:
        raise LinkError(f"{root}: {e}.") from e
    if state is None:
        return False
    if not link["folder_id"] or not vault_upgrade.belongs(link, state):
        raise LinkError(f"{root} already holds another folder's files; pick another directory or remove them first.")
    return True


# --- refusals -----------------------------------------------------------------------------

def _overlap(conn, a: str, b: str) -> bool:
    """Whether two vaults' folders hold pages in common: the same folder, one
    below the other, or the whole library. A folder "" (one not made yet, at
    the library's top) overlaps the library only."""
    if folder_sync.ROOT in (a, b) or a == b:
        return True
    return (bool(a) and b in folder_subtree_ids(conn, a)) or (bool(b) and a in folder_subtree_ids(conn, b))


def _named_overlap(a: dict, b: dict) -> bool:
    """``_overlap`` for two vaults of one workspace on another server, whose
    folder tree is that server's: the same folder or the whole library, else
    one below the other by the names from the top their rounds recorded
    (``status.folder_path``; none yet, by the ids alone). Two folders of one
    name beside each other read as one: refused rather than both pushed into."""
    if folder_sync.ROOT in (a["folder_id"], b["folder_id"]) or a["folder_id"] == b["folder_id"]:
        return True
    names = sorted((link["status"].get("folder_path") or [] for link in (a, b)), key=len)
    return bool(names[0]) and names[1][:len(names[0])] == names[0]


def _kept_elsewhere(ws: str) -> str:
    """Why nothing may push everything into ``ws`` ("" when it may): a clone
    copies another workspace and a Zotero link keeps one in step with a
    library, and each writes it too."""
    from .sync_engine import get_mirror  # local: it imports the routers

    mirror = get_mirror(ws)
    if mirror is not None and mirror["page_filter"] is None:
        return "This workspace is a clone of another: a vault in it pulls, or pushes PDFs, but cannot push everything."
    with connect_users_db() as conn:
        if conn.execute("SELECT 1 FROM zotero_links WHERE workspace_id = ?", (ws,)).fetchone():
            return ("This workspace is kept in step with a Zotero library: a vault in it pulls, or pushes PDFs, "
                    "but cannot push everything.")
    return ""


def _takes_in(link: dict, other: dict) -> bool:
    """Whether the folder vault ``link`` takes the file vault ``other`` in
    when it is made (``_take_in``): a vault of this server around a file
    vault of its workspace that its maker made, since taking one in removes
    its link, which only its maker may."""
    return (not link["remote_url"] and not other["remote_url"] and other["workspace_id"] == link["workspace_id"]
            and other["created_by"] == link["created_by"])


def _apart(link: dict, other: dict, within=inside) -> None:
    """Two links' places on this computer: a directory vault is not inside
    another's or around one, and a file vault is not inside one, except the
    file vaults a new directory vault takes in. ``within`` compares two
    places (``inside``, or ``_within`` for a place just given)."""
    mine, theirs = link["path"], other["path"]
    if within(mine, theirs) and within(theirs, mine):
        raise LinkError(_taken(link))
    if link["kind"] == "folder" and other["kind"] == "folder":
        if within(mine, theirs) or within(theirs, mine):
            raise LinkError(f"{mine} is inside or around the synced vault {theirs}: vaults do not overlap.")
    elif link["kind"] == "file" and other["kind"] == "folder":
        if within(mine, theirs):
            raise LinkError(f"That file is in the synced vault {theirs}: open its page from there.")
    elif link["kind"] == "folder" and other["kind"] == "file":
        if within(theirs, mine) and not _takes_in(link, other):
            if other["created_by"] != link["created_by"]:
                raise LinkError(f"{theirs}, a file another account syncs on its own, is inside {mine}: "
                                "vaults do not overlap.")
            raise LinkError(f"{theirs}, a file synced on its own, is inside {mine}: remove that one first.")


def check_vault(link: dict, *, earlier: bool = False) -> None:
    """The refusals of a vault, LinkError with the reason: a place inside
    Gamma's data directory; one inside or around another vault's
    (``_apart``); a marker another live link holds (a copied directory);
    pushing everything into a clone or a Zotero-linked workspace
    (``_kept_elsewhere``; pulling is allowed there, it writes nothing into
    Gamma); a folder that overlaps the folder of another vault of the
    workspace, on this server or on the same other one (``_named_overlap``),
    when both push. ``earlier``: a round's question, which only the
    links made before this one answer, so that of two vaults that came to
    overlap the older goes on; places are then compared by their spelling,
    the identity of each having been asked when it was given (a round must
    not stat every other vault's place, a dead share among them). A vault of
    this server whose maker may no longer edit its workspace (a viewer now,
    or no member) is told only that it is refused (``REFUSED_UNSAID``), in
    its round's status and in the answer to its maker's change alike: the
    reasons read the workspace as it is now and name other vaults' places,
    which the API keeps from a member who cannot act on a vault (the
    router's ``_info``). A link not made yet (``preview``'s) is an editor's."""
    try:
        _check_vault(link, earlier)
    except LinkError as e:
        if link["remote_url"] or not link["created_by"] or may_edit(link["workspace_id"], link["created_by"]):
            raise
        raise LinkError(REFUSED_UNSAID) from e


def _check_vault(link: dict, earlier: bool) -> None:
    """``check_vault``'s refusals, each with its reason."""
    others = [o for o in all_links() if o["id"] != link["id"]]
    if earlier:
        others = [o for o in others if (o["created_at"], o["id"]) < (link["created_at"], link["id"])]
    if _in_data_dir(Path(link["path"])):
        raise LinkError("That place is Gamma's data directory, inside it or around it; pick another.")
    for other in others:
        _apart(link, other, inside if earlier else _within)
    if link["kind"] != "folder":
        return
    if not link["remote_url"]:
        vault = link["state"].get("vault")
        if vault and any(o["state"].get("vault") == vault for o in others):
            raise LinkError("The directory's vault marker is another vault's: was it copied? Pick another directory.")
        if link["push"] == PUSH_ALL:
            refusal = _kept_elsewhere(link["workspace_id"])
            if refusal:
                raise LinkError(refusal)
    pushing = [o for o in others if o["kind"] == "folder" and o["push"] and o["remote_url"] == link["remote_url"]
               and o["workspace_id"] == link["workspace_id"]]
    if not link["push"] or not pushing:
        return
    if link["remote_url"]:
        overlaps = [o for o in pushing if _named_overlap(link, o)]
    else:
        with connect_pages_db(link["workspace_id"]) as conn:
            overlaps = [o for o in pushing if _overlap(conn, link["folder_id"], o["folder_id"])]
    if overlaps:
        raise LinkError(f"Its folder holds pages the synced vault {overlaps[0]['path']} pushes into too: "
                        "only one vault pushes into a page.")


def pushing_vault(ws: str) -> str:
    """The directory of a vault of this server that pushes everything into
    ``ws`` ("" for none): what a clone or a Zotero link of that workspace
    would write over (``check_vault``'s rule, asked from their side)."""
    return next((link["path"] for link in all_links() if link["kind"] == "folder" and not link["remote_url"]
                 and link["workspace_id"] == ws and link["push"] == PUSH_ALL), "")


# --- making a link --------------------------------------------------------------------------

def _room_for(conn, link: dict) -> None:
    """The path must not be another link's (UNIQUE, ignoring case), and the
    workspace has at most ``MAX_LINKS`` synced vaults of folders and
    ``MAX_FILE_LINKS`` of files."""
    folder = link["kind"] == "folder"
    count = conn.execute("SELECT COUNT(*) FROM folder_links WHERE workspace_id = ? AND kind = ?",
                         (link["workspace_id"], link["kind"])).fetchone()[0]
    if folder and count >= MAX_LINKS:
        raise LinkError(f"A workspace has at most {MAX_LINKS} synced vaults of folders.")
    if not folder and count >= MAX_FILE_LINKS:
        raise LinkError(f"A workspace has at most {MAX_FILE_LINKS} synced vaults of files.")
    if conn.execute("SELECT 1 FROM folder_links WHERE path = ?", (link["path"],)).fetchone():
        raise LinkError(_taken(link))


def _taken(link: dict) -> str:
    if link["kind"] == "folder":
        return f"{link['path']} is synced already, by another synced vault."
    return f"{link['path']} is already pushed to another page by a synced vault."


def _insert(link: dict, token: str = "") -> dict:
    """The row, once ``_room_for`` allows it."""
    with connect_users_db() as conn:
        _room_for(conn, link)
        try:
            conn.execute(f"INSERT INTO folder_links ({_COLS}) VALUES ({', '.join('?' * 18)})",
                         (link["id"], link["workspace_id"], link["folder_id"], link["path"], int(link["notes"]), link["created_by"],
                          link["created_at"], "", json.dumps(link["status"]), link["remote_url"], seal(token),
                          link["token_id"], link["kind"],
                          link["page_id"], link["push"], json.dumps(link["state"]), link["pull"], int(link["library"])))
        except sqlite3.IntegrityError as e:   # the path, taken meanwhile (UNIQUE, ignoring case)
            raise LinkError(_taken(link)) from e
    return link


def _new(ws: str, path: str, user_id: str, **fields) -> dict:
    """A link not stored yet: a folder link unless ``fields`` say otherwise."""
    return {"id": uuid.uuid4().hex, "workspace_id": ws, "kind": "folder", "folder_id": "", "page_id": "", "path": path,
            "notes": False, "pull": 1, "push": PUSH_OFF, "library": False, "state": {}, "created_by": user_id,
            "created_at": page_now(), "cursor": "", "status": {}, "remote_url": "", "token_id": "", **fields}


def _found_marker(root: Path, adopt: bool) -> dict | None:
    """The marker a new vault at ``root`` keeps: the one the directory holds,
    taken as it is with ``adopt`` when no live link holds it (a vault of
    another computer, or of a link this one forgot); None when it holds none.
    LinkError for one that cannot be read, another link's, or one not to be
    adopted."""
    try:
        marker = vault_disk.read_marker(root)
    except vault_disk.Offline:
        return None   # not there yet: a vault of a folder makes it
    except vault_disk.Stopped as e:
        raise LinkError(f"{root}: {e}.") from e
    if marker is None:
        return None
    holder = next((link for link in all_links() if link["state"].get("vault") == marker["id"]), None)
    if holder:
        raise LinkError(f"{root} holds the marker of the synced vault {holder['path']}: was it copied? "
                        "Pick another directory.")
    if not adopt:
        raise LinkError(f"{root} was a synced vault before: sync it again as it is, or pick another directory.")
    return marker


def _merges(ws: str, folder_id: str, root: Path) -> bool:
    """Whether the folder and the directory both hold what a vault would
    send the other way: a folder with pages and a directory with notes or
    PDFs, which pushing everything would mix (a linked directory is a new
    folder, a linked folder a new directory). The directory's files are
    those its ``.gammaignore`` tracks (``selection.rules_of``)."""
    from .work_folders import selection

    with connect_pages_db(ws) as conn:
        if not (live_pages(conn) if folder_id == folder_sync.ROOT else pages_in_folder(conn, folder_id)):
            return False
    try:
        return root.is_dir() and bool(vault_disk.walk(root, selection.rules_of(root)).files)
    except vault_disk.Offline:
        return False


def _new_folder(ws: str, name: str, user_id: str, mark: dict | None = None) -> str:
    """A new folder at the library's top named ``name``, even when one of the
    name is there: a vault made from a directory never fills a folder that
    held pages. A work folder's carries its ``mark`` from the start."""
    folder_id = new_block_id()
    insert = {"op": "insert", "id": folder_id, "parent": FOLDERS, "content": name}
    if mark is not None:
        insert["props"] = {"vault": mark}
    commit_ops(ws, FOLDERS, [insert], actor=user_id, client=DISK_CLIENT)
    return folder_id


def _make_vault(link: dict, marker: dict | None, *, new_folder: bool, token: str = "", choose: bool = False) -> dict:
    """A vault's row (with the token of its other server, ``token``), made
    after its folder (``new_folder``) and its marker (unless ``marker``, the
    one the directory holds), both taken back when the row cannot be stored.
    Then the file vaults inside it are its, with the link's lock held from the
    row on: a tick's round in between would read a taken-in PDF as new and
    make it a second page. A work folder's folder carries the mark, and one
    that holds no page yet has nothing to take over (``TAKEN_OVER`` now).
    With ``choose`` it is paused and choosing, what was made for it noted for
    a cancel (``_cancel``), and its file vaults wait for its files to be
    chosen (``save_selection``)."""
    from .work_folders import takeover

    root = dest_of(link)
    work = work_folder_link(link)
    mark = takeover.mark_of(link) if work else None
    made_folder = made_marker = marked = False
    made_dir = not (root / vault_disk.MARKER_DIR).exists()
    with _holding(link["id"]):    # a tick rests it (``_resting``), and nobody else knows the link yet
        try:
            if new_folder:
                link["folder_id"] = _new_folder(link["workspace_id"], root.name or "Vault", link["created_by"], mark)
                made_folder = True
            elif work:
                _set_mark(link, mark)
                marked = True
            if marker is None:
                try:
                    marker = vault_disk.make_marker(root)
                except OSError as e:
                    raise LinkError(f"Cannot write {root}: {e}") from e
                made_marker = True
            state = {"vault": marker["id"]}
            if work and (made_folder or not _holds_pages(link)):
                state[takeover.TAKEN_OVER] = page_now()
            if choose:
                state |= {CHOOSING: True, "made": {"folder": made_folder, "marker": made_marker,
                                                   "marker_dir": made_marker and made_dir}}
                link["status"] = {**link["status"], PAUSED: page_now()}
            link["state"] = state
            _insert(link, token)
        except BaseException:
            if made_marker:
                (root / vault_disk.MARKER).unlink(missing_ok=True)
                if made_dir:
                    _drop_empty(root / vault_disk.MARKER_DIR)
            if made_folder:
                commit_ops(link["workspace_id"], FOLDERS, [{"op": "delete", "id": link["folder_id"]}],
                           actor=link["created_by"], client=DISK_CLIENT)
            elif marked:
                _set_mark(link, None)
            raise
        if not choose:
            _take_in(link)
    return link


def _holds_pages(link: dict) -> bool:
    with connect_pages_db(link["workspace_id"]) as conn:
        return bool(pages_in_folder(conn, link["folder_id"]))


def _drop_empty(directory: Path) -> None:
    """``directory`` removed when it is empty; left as it is otherwise."""
    try:
        directory.rmdir()
    except OSError:
        pass


def _take_in(link: dict) -> None:
    """The vaults of one file of the workspace inside a new directory vault
    become its own (docs/research/vaults.md "A vault of one file"): each page
    keeps its id and all it holds, is filed first in the folder of its
    file's directory, its other filings kept, its file the vault's PDF row
    (its stat left unknown when the page's bytes are older, so the round
    reads it), and its own link goes. One that cannot be taken in stays a
    vault of its own, and the log says why."""
    from .vault.remote import remote_for  # local: it imports this module

    files = followed_files(link)
    remote = remote_for(link) if files else None
    for other in files:
        try:
            _take_in_one(link, remote, other)
        except (OpError, OSError, sqlite3.Error) as e:
            log.warning(f"[folders] {other['path']} stays a vault of its own: {getattr(e, 'detail', '') or e}")


def followed_files(link: dict) -> list[dict]:
    """The vaults of one file inside the directory vault ``link`` that it
    takes in (``_take_in``): its tracking dialog keeps their PDFs ticked."""
    return [o for o in all_links() if o["kind"] == "file" and _takes_in(link, o) and inside(o["path"], link["path"])]


def _take_in_one(link: dict, remote, other: dict) -> None:
    """The file vault ``other`` taken in by the vault ``link`` (``_take_in``),
    its folders made through ``remote``. A work folder's page is stamped as
    its file, in the library (it was a library page), its folders in other
    work folders let go."""
    from . import linked_files  # local: it imports this module

    ws, rel = link["workspace_id"], os.path.relpath(other["path"], dest_of(link)).replace(os.sep, "/")
    names = rel.split("/")[:-1]
    folder_id = remote.folders([names]).get(tuple(n.strip() for n in names), link["folder_id"]) if names else link["folder_id"]
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT content, properties, doc_id FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (other["page_id"],)).fetchone()
        tree = marks.Tree.read(conn)
    if row:
        try:
            seen = linked_files.hash_file(other["path"])
        except (OSError, ValueError):
            seen = None
        level = seen is not None and seen[0] == (row[2] or "")
        props = load_props(row[1])
        if folder_id != folder_sync.ROOT:
            others = [f for f in filing(props, FOLDERS) if f != folder_id]
            filed = {FOLDERS: [folder_id, *others]}
            if props.get(PARENT_PAGE):
                filed[PARENT_PAGE] = None
            if work_folder_link(link):
                filed |= {FOLDERS: [folder_id, *(f for f in others if not tree.work_of(f))], "work_folder": link["folder_id"],
                          "library": True, "file_name": rel.rsplit("/", 1)[-1]}
                doc = props.get("doc")
                if level and isinstance(doc, dict) and doc.get("size") is None:
                    filed["doc"] = {**doc, "size": seen[1]}
            commit_ops(ws, other["page_id"], [{"op": "set", "id": other["page_id"], "props": filed}],
                       actor=link["created_by"], client=DISK_CLIENT)
        vault_store.put(link["id"], rel, kind="pdf", page_id=other["page_id"], hash=row[2] or "",
                        size=seen[1] if level else 0, mtime_ns=seen[2] if level else 0,
                        file_id=seen[3] if level else "", read_at=0.0,
                        text=vault_disk.pdf_id(Path(other["path"])) if level else "", title=row[0] or "",
                        folder=folder_id if names else "", seq=0, ours=0)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM folder_links WHERE id = ?", (other["id"],))


def create_link(ws: str, folder_id: str, path: str, notes: bool, user_id: str, push: str = "off", pull: bool = True, *,
                new_folder: bool = False, adopt: bool = False, choose: bool = False, library: bool = False) -> dict:
    """A new vault of a folder of this server's workspace ``ws``: the path is
    cleaned (``clean_path``) and must not be another link's, and the link
    passes ``check_vault``. ``push`` (``push_value``) is what goes back to
    Gamma, ``pull`` whether Gamma's changes are written, ``notes`` whether
    notes files are (always where it pushes everything). With ``new_folder``
    the vault is made from the directory, which must be there, into a new
    folder named after it; else from the folder, which must exist (or be
    ``root``, which pushes everything nowhere: ``_whole_library``), and one
    that pushes everything may not mix a folder with pages and a directory
    with files (``_merges``). The directory gets its marker (``adopt``: the
    one it holds, ``_found_marker``) and takes in the file vaults inside it.
    A directory an earlier link kept the same folder in (``_kept_before``) is
    that folder's again, taken onto the engine at its first round
    (gamma/vault/upgrade.py). ``library`` is a work folder's switch; with
    ``choose`` (a work folder only) its files wait for its tracking dialog,
    no round running before (``_make_vault``)."""
    path, push, pull = clean_path(path), push_value(push), bool(pull)
    if not pull and not push:
        raise LinkError("A vault pulls, pushes, or both.")
    root = Path(path)
    if new_folder:
        if not root.is_dir():
            raise LinkError(f"There is no directory {root}.")
        folder_id = ""
    else:
        with connect_pages_db(ws) as conn:
            if folder_id != folder_sync.ROOT and folder_id not in folder_paths(conn):
                raise LinkError("That folder does not exist.")
    link = _new(ws, path, user_id, folder_id=folder_id, notes=bool(notes) or new_folder or push == PUSH_ALL, push=push,
                pull=int(pull), library=bool(library))
    _whole_library(link)
    if choose and not work_folder_link(link):
        raise LinkError(CHOOSES_ONLY)
    check_vault(link)
    if _kept_before(link, root):
        if choose:
            raise LinkError(f"{root} holds a folder kept on disk before synced vaults: sync it as it is.")
        return _insert(link)
    marker = _found_marker(root, adopt)
    if not new_folder and push == PUSH_ALL and _merges(ws, folder_id, root):
        raise LinkError("Both the folder and the directory hold files: sync an empty folder with this directory, "
                        "or this folder with an empty or new one.")
    with connect_users_db() as conn:
        _room_for(conn, link)
    return _make_vault(link, marker, new_folder=new_folder, choose=choose)


def _whole_library(link: dict) -> None:
    """LinkError for a vault of this server's whole library that would push
    everything: such a vault is a work folder, which needs a folder to carry
    its mark. One from before goes on as it is, a kept folder."""
    if (link["kind"] == "folder" and not link["remote_url"] and link["folder_id"] == folder_sync.ROOT
            and link["push"] == PUSH_ALL):
        raise LinkError("A vault of the whole library cannot push everything: make one of a folder.")


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
                       user_id: str, push: str = "off", pull: bool = True) -> dict:
    """A new vault of a folder on *another* Gamma server (gamma/vault/http_remote.py),
    read with a token of that server (read access is enough unless ``push``,
    which takes a token that may write; the token is kept Fernet-encrypted,
    ``token_id`` for whoever revokes it there later). The token is checked
    on that server (``_remote``), and the folder must exist there. The
    directory gets its marker, as ``create_link``'s does; one an earlier link
    kept that folder in is that folder's again."""
    path, push, pull = clean_path(path), push_value(push), bool(pull)
    if not pull and not push:
        raise LinkError("A vault pulls, pushes, or both.")
    server, who = _remote(url, token, workspace)
    if push and who.get("scope") != "write":
        raise LinkError("Pushing needs a token that may write.")
    try:
        tree = server.get_json("/api/sync/folders")
    except gamma_sync.SyncError as e:
        raise LinkError(str(e)) from e
    folders = {f["id"]: f["path"] for f in tree.get("folders", []) if f.get("id")}
    if folder_id != folder_sync.ROOT and folder_id not in folders:
        raise LinkError("That folder does not exist on the other server.")
    link = _new(who["workspace"]["id"], path, user_id, folder_id=folder_id, notes=bool(notes) or push == PUSH_ALL,
                push=push, pull=int(pull), remote_url=server.url, token_id=str(token_id or ""),
                status={"folder_path": folders.get(folder_id) or []})    # its names there, which ``check_vault`` reads
    root = Path(path)
    check_vault(link)
    if _kept_before(link, root):
        return _insert(link, token)
    marker = _found_marker(root, adopt=False)
    with connect_users_db() as conn:
        _room_for(conn, link)
    return _make_vault(link, marker, new_folder=False, token=token)


def create_file_link(ws: str, page_id: str, path: str, user_id: str) -> dict:
    """A new file link: the PDF at ``path`` (``clean_file_path``), not
    another link's nor inside a directory vault, becomes the page
    ``page_id`` of this server's workspace ``ws`` each time it changes. The
    page must be one of the library."""
    path = clean_file_path(path)
    page = _page(ws, page_id)
    if page is None:
        raise LinkError("That page does not exist.")
    link = _new(ws, path, user_id, kind="file", page_id=page_id, pull=0, push=PUSH_PDFS, state={"title": page["title"]})
    check_vault(link)
    return _insert(link)


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
    link = _new(who["workspace"]["id"], path, user_id, kind="file", page_id=page_id, pull=0, push=PUSH_PDFS,
                state={"title": page["title"]}, remote_url=server.url, token_id=str(token_id or ""))
    check_vault(link)
    return _insert(link, token)


def preview(ws: str, path: str, folder_id: str = "", kind: str = "folder") -> dict:
    """What making a vault of ``path`` would find and write, writing nothing:
    ``{path, notes, pdfs, pictures, untracked, empty, marker, refusal, pages,
    will_write, gammaignore}``. The counts are of what the directory's
    ``.gammaignore`` tracks (the default without one), ``untracked`` the
    files it does not read: of no class, or left out by it (the default
    leaves out every file of no class); ``empty`` a directory with no file
    or directory at all that the scan lists; ``gammaignore`` that
    file's ``{exists, ours}`` (``ours``: Gamma wrote it). ``marker`` is the
    one the directory holds, ``{id, link_id}`` (``link_id`` "" when no link
    holds it, so it may be adopted); ``refusal`` why it would be refused (""
    for none; a ``.gammaignore`` that cannot be read among the reasons);
    ``pages`` what the folder ``folder_id`` holds; ``will_write`` what it
    writes on disk, the marker's directory when there is none yet. LinkError
    for a path no vault can have, or a directory that cannot be read."""
    from .work_folders import ignore_rules

    if kind == "file":
        path = clean_file_path(path)
        link = _new(ws, path, "", kind="file", pull=0, push=PUSH_PDFS)
        return {"path": path, "notes": 0, "pdfs": 1, "pictures": 0, "untracked": 0, "empty": False, "marker": None,
                "refusal": _refusal_of(link), "pages": 0, "will_write": [], "gammaignore": {"exists": False, "ours": False}}
    path = clean_path(path)
    root, refusal, found = Path(path), "", None
    try:
        found = vault_disk.read_marker(root)
    except vault_disk.Offline:
        pass
    except vault_disk.Stopped as e:
        refusal = f"{root}: {e}."
    scan, rules = vault_disk.Scan(), None
    if root.is_dir():
        try:
            rules = ignore_rules.read(root)
        except ignore_rules.Unreadable as e:
            refusal = refusal or f"{root}: its {ignore_rules.NAME} cannot be read: {e.why}."
        try:
            scan = vault_disk.walk(root, rules)
        except vault_disk.Offline as e:
            raise LinkError(f"Cannot read {root}: {e}") from e
    marker = None
    if found:
        holder = next((link for link in all_links() if link["state"].get("vault") == found["id"]), None)
        marker = {"id": found["id"], "link_id": holder["id"] if holder else ""}
        if holder and not refusal:
            refusal = f"{root} holds the marker of the synced vault {holder['path']}: was it copied?"
    pages = 0
    if folder_id:
        with connect_pages_db(ws) as conn:
            pages = len(live_pages(conn) if folder_id == folder_sync.ROOT else pages_in_folder(conn, folder_id))
    link = _new(ws, path, "", folder_id=folder_id, state={"vault": found["id"]} if found else {})
    if not refusal:
        try:
            _kept_before(link, root)
        except LinkError as e:
            refusal = str(e)
    kinds = [f.kind for f in scan.files.values()]
    untracked = scan.untracked + sum(1 for p in scan.excluded if not p.endswith("/"))
    return {"path": path, "notes": kinds.count("notes"), "pdfs": kinds.count("pdf"), "pictures": scan.pictures,
            "untracked": untracked,
            "empty": not (scan.files or scan.dirs or scan.pictures or untracked or scan.excluded or scan.refused),
            "marker": marker, "refusal": refusal or _refusal_of(link), "pages": pages,
            "will_write": [] if found else [f"{vault_disk.MARKER_DIR}/"],
            "gammaignore": {"exists": bool(rules and rules.exists), "ours": bool(rules and rules.ours)}}


def _refusal_of(link: dict, **kw) -> str:
    try:
        check_vault(link, **kw)
    except LinkError as e:
        return str(e)
    return ""


# --- changing a link ------------------------------------------------------------------------

def set_notes(link_id: str, notes: bool) -> dict | None:
    """Whether a folder link writes notes files; None for no such link. A
    synced vault keeps its notes as files: only a folder kept before with its
    PDFs alone has them off, and may turn them on, which makes the next pull
    look at every page again (``vault_store.unlevel``, while no round runs).
    The next round reads the row."""
    link = get_link(link_id)
    if link is None:
        return None
    if link["kind"] == "file":
        raise LinkError("A vault of one file has no notes file.")
    if not notes and link["notes"]:
        raise LinkError("A synced vault keeps its notes as files.")
    if notes and not link["notes"]:
        with _holding(link_id):
            with connect_users_db() as conn:
                conn.execute("UPDATE folder_links SET notes = 1 WHERE id = ?", (link_id,))
            vault_store.unlevel(link_id)
    link["notes"] = bool(notes)
    return link


def set_push(link_id: str, word: str, token: str = "", token_id: str = "") -> dict | None:
    """What a folder link pushes, by its word (``push_value``); None for no
    such link. A link to another server turning it on needs a token of it
    that may write: ``token`` (with its ``token_id``), checked there and
    stored in place of the one the link had (``_check_token``; with push off
    too), or else the stored one. The change passes ``check_vault``; pushing
    everything turns its notes files on. A file link always sends:
    LinkError. Only the row changes; the next round reads it (a folder kept
    before, not taken onto the engine yet, is at that round, its notes
    changed on disk kept until a force decides). A kept folder that comes
    to push everything becomes a work folder, its pages in the library,
    taken over at its next round; a work folder's push comes down only while
    every file of it is in the library, and its next round lets it go
    (gamma/work_folders/takeover.py)."""
    from .work_folders import takeover

    push = push_value(word)
    link = get_link(link_id, with_token=True)
    if link is None:
        return None
    if link["kind"] == "file":
        raise LinkError("A vault of one file always pushes its new versions.")
    if not push and not link["pull"]:
        raise LinkError("A vault pulls, pushes, or both.")
    after = {**link, "push": push}
    if push != link["push"]:
        _whole_library(after)
    if work_folder_link(link) and not work_folder_link(after) and _outside_library(link):
        raise LinkError("This work folder's files are not in the library: remove the vault, or add them to the "
                        "library first.")
    check_vault(after)
    if link["remote_url"] and (push or token):
        _check_token(link, token or link["token"], bool(push))
    raised = work_folder_link(after) and not work_folder_link(link)
    with _holding(link_id) if raised else nullcontext():
        with connect_users_db() as conn:
            conn.execute("UPDATE folder_links SET push = ? WHERE id = ?", (push, link_id))
            if link["remote_url"] and token:
                _keep_token(conn, link_id, token, token_id)
            if raised:
                state = {k: v for k, v in (get_link(link_id) or link)["state"].items() if k != takeover.TAKEN_OVER}
                conn.execute("UPDATE folder_links SET library = 1, state = ? WHERE id = ?", (json.dumps(state), link_id))
    if work_folder_link(link) and not work_folder_link(after):
        _stop_agent(link_id)  # no work folder now: its agent answers no Send
    if push == PUSH_ALL and not link["notes"]:
        set_notes(link_id, True)
    return get_link(link_id)


def _outside_library(link: dict) -> bool:
    """Whether a live file of the work folder is not in the library."""
    with connect_pages_db(link["workspace_id"]) as conn:
        return conn.execute(f"SELECT 1 FROM unified_blocks WHERE work_folder = ? AND +parent_id = 'root' AND "
                            f"{SQL_OUTSIDE_LIBRARY} LIMIT 1", (link["folder_id"],)).fetchone() is not None


def set_token(link_id: str, token: str, token_id: str = "") -> dict | None:
    """A new token of the other server for a link to one (with its
    ``token_id``, for whoever revokes it there), in place of the link's: the
    way back for a link whose token was revoked there. Checked there first
    (``_check_token``: the link's workspace, and a token that may write when
    the link pushes); None for no such link; LinkError for a link of this
    server or a token that does not do, the link's own kept."""
    link = get_link(link_id)
    if link is None:
        return None
    if not link["remote_url"]:
        raise LinkError("A vault of this computer's server takes no token.")
    _check_token(link, token, bool(link["push"]))
    with connect_users_db() as conn:
        _keep_token(conn, link_id, token, token_id)
    return get_link(link_id)


def _check_token(link: dict, token: str, pushes: bool) -> None:
    """LinkError unless the link's other server takes ``token`` for the link's
    workspace (``_remote``) and, where the link ``pushes``, lets it write."""
    _server, who = _remote(link["remote_url"], token, link["workspace_id"])
    if pushes and who.get("scope") != "write":
        raise LinkError("Pushing needs a token that may write.")


def _keep_token(conn, link_id: str, token: str, token_id: str) -> None:
    conn.execute("UPDATE folder_links SET token = ?, token_id = ? WHERE id = ?", (seal(token), str(token_id or ""), link_id))


def set_pull(link_id: str, pull: bool) -> dict | None:
    """Whether a folder link writes Gamma's changes to its directory; None
    for no such link. A vault that does not pull pushes something. The row
    changes, and a work folder's mark, which tells the op path whether its
    notes follow Gamma's renames and moves."""
    link = get_link(link_id)
    if link is None:
        return None
    if link["kind"] == "file":
        raise LinkError("A vault of one file only pushes its new versions: nothing is pulled.")
    if not pull and not link["push"]:
        raise LinkError("A vault pulls, pushes, or both.")
    with connect_users_db() as conn:
        conn.execute("UPDATE folder_links SET pull = ? WHERE id = ?", (int(bool(pull)), link_id))
    link = get_link(link_id)
    _mark_again(link)
    return link


def set_library(link_id: str, on: bool) -> dict | None:
    """A work folder's library switch: whether the pages of the files it
    finds from now on are library documents too (the pages it holds keep
    theirs: a file is added or removed one by one). The row changes, and
    the folder's mark. None for no such link; LinkError for a file link."""
    link = get_link(link_id)
    if link is None:
        return None
    if link["kind"] == "file":
        raise LinkError("A vault of one file pushes its PDF to its page: it adds nothing to the library.")
    with connect_users_db() as conn:
        conn.execute("UPDATE folder_links SET library = ? WHERE id = ?", (int(bool(on)), link_id))
    link = get_link(link_id)
    _mark_again(link)
    return link


def set_agent(link_id: str, agent: str, *, by: str) -> int:
    """A work folder's agent on this computer (docs/dev/local_agents.md):
    ``agent`` (``codex``, ``claude``) answers its Sends from now on, turned
    on by the account ``by``, or ``""`` none. The row is this computer's
    (``local_agents.store``); a run of the agent it had stops when it is
    turned off or changed, the folder's open pages hear ``{t: "vault"}``
    (their chip and the comments' @ menu read the agent again), and an
    agent turned on (or changed) takes the Sends that wait now → how many
    it took. The route checks who may and whether the agent is ready.
    LinkError for a vault that is no work folder; 0 for no such link."""
    from .local_agents import runner, store as agents  # local: the runner imports the feedback modules

    link = get_link(link_id)
    if link is None:
        return 0
    if agent and not work_folder_link(link):
        raise LinkError("Only a work folder can have an agent.")
    had = (agents.agent_of(link_id) or {}).get("agent", "")
    agents.set_agent(link_id, agent, by=by)
    if had and had != agent:
        _stop_agent(link_id)
    if had != agent:
        _tell_open_pages(link)
    if agent and agent != had:
        return runner.queue_folder(link["workspace_id"], link_id)
    return 0


def _tell_open_pages(link: dict) -> None:
    """Each page of the vault's work folder open in a window now hears
    ``{t: "vault"}``, for its vault chip to look again. Never raises: the
    change is made."""
    ws = link["workspace_id"]
    opened = sorted(page_id for key_ws, page_id in collab.open_pages() if key_ws == ws)
    if not opened:
        return
    try:
        with connect_pages_db(ws) as conn:
            pages = [r[0] for r in conn.execute(
                "SELECT id FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?)) AND work_folder = ?",
                (json.dumps(opened), link["folder_id"]))]
    except Exception as e:  # noqa: BLE001
        log.warning(f"[folders] the open pages of work folder {link['folder_id']}: {e}")
        return
    for page_id in pages:
        collab.publish(ws, page_id, {"t": "vault"})


def _stop_agent(link_id: str) -> None:
    """The run of the vault's agent on this computer stops, if one runs: its
    agent was turned off or changed, the vault paused, moved, removed or no
    longer pushes everything (gamma/local_agents/runner.py ``stop_link``,
    which reads why when the run ends)."""
    from .local_agents import runner  # local: the runner imports the feedback modules

    runner.stop_link(link_id)


def _mark_again(link: dict) -> None:
    """A work folder's mark written again from its row's switches. A folder
    gone, or a mark that cannot be written now, is the next round's to put
    right (it writes the mark whenever it differs)."""
    from .work_folders import takeover

    if not work_folder_link(link):
        return
    try:
        _set_mark(link, takeover.mark_of(link))
    except OpError as e:
        log.warning(f"[folders] {link['path']}: its mark waits for the next round: {e.detail}")


def set_paused(link_id: str, paused: bool) -> dict | None:
    """Pause the link — the tick leaves it alone; a sync asked for still
    runs, and a round already running finishes — or resume it. The pause
    is ``status.paused_at``, when it began; None for no such link. A work
    folder whose files are not chosen yet stays paused: LinkError. A pause
    stops the run of the vault's agent (``_stop_agent``)."""
    with _status_guard, connect_users_db() as conn:
        r = conn.execute("SELECT status, state FROM folder_links WHERE id = ?", (link_id,)).fetchone()
        if r is None:
            return None
        if not paused and _object_of(r[1]).get(CHOOSING):
            raise LinkError("Choose its files first.")
        status = _object_of(r[0])
        since = status.pop(PAUSED, None)
        if paused:
            status[PAUSED] = since or page_now()
        conn.execute("UPDATE folder_links SET status = ? WHERE id = ?", (json.dumps(status), link_id))
    if paused:
        _stop_agent(link_id)
    return get_link(link_id)


def save_selection(link_id: str, chosen: dict, base_hash: str, library: bool | None = None) -> dict | None:
    """What the vault tracks, saved from its tracking dialog
    (gamma/work_folders/selection.py): ``chosen`` evaluated, refused when the
    ``.gammaignore`` is not the one the dialog read (``base_hash``) or it
    tracks too many files; then, under the link's lock, the vaults of one
    file inside it taken in, the new ``.gammaignore`` written, its hash kept
    as the one the rounds read (``vault.sync.SELECTION``), a vault that was
    choosing no longer, and none paused; the library switch set with it when
    given. Then a round, confirmed: the dialog has said what leaves. → the
    link; None for no such link. ``selection.SelectionError`` for a refusal,
    LinkError for a library switch refused."""
    from .vault import sync as vault_sync  # local: it imports this module
    from .work_folders import selection

    if get_link(link_id) is None:
        return None
    with _holding(link_id):
        link = get_link(link_id)
        if link is None:
            return None
        found = selection.evaluate(link, chosen)
        selection.check(link, found, base_hash)
        _take_in(link)
        written = selection.write(link, found)
        state = {k: v for k, v in (get_link(link_id) or link)["state"].items() if k not in (CHOOSING, "made")}
        keep_state(link_id, {**state, vault_sync.SELECTION: written})
        set_paused(link_id, False)
        if library is not None:
            set_library(link_id, library)
    run_in_background(link_id, confirm=True)
    return get_link(link_id)


def relink(link_id: str, path: str) -> dict | None:
    """The link found at another place, ``path``: a vault's directory moved,
    or its disk mounted elsewhere, whose marker must be this vault's (the
    state file of a folder kept before, not taken onto the engine yet, this
    folder's), or a followed file that moved. The PDFs a vault reads in place
    are read there from now on, the link's last error goes and a round
    follows (none while its files are not chosen). None for no such link;
    LinkError for a place that is not the link's, or that ``check_vault``
    refuses. A run of the vault's agent stops, and the sessions it answered
    threads in are forgotten: they belong to the directory that was."""
    link = get_link(link_id)
    if link is None:
        return None
    new = clean_file_path(path) if link["kind"] == "file" else clean_path(path)
    root = Path(new)
    if older(link):
        try:
            held = _kept_before(link, root)
        except LinkError as e:
            raise LinkError(f"{root} is not this folder on disk: {e}") from e
        if not held:
            raise LinkError(f"{root} is not this folder on disk.")
    elif engine(link):
        try:
            vault_disk.check_marker(root, link["state"]["vault"])
        except (vault_disk.Stopped, vault_disk.Offline) as e:
            raise LinkError(f"{root} is not this vault: {e}.") from e
    check_vault({**link, "path": new})
    _stop_agent(link_id)
    with _holding(link_id):
        from .vault.sync import PLACE  # local: it imports this module

        stored = (get_link(link_id) or link)["state"]           # as the last round left it
        state = {k: v for k, v in stored.items() if k != PLACE}     # the marker found there is the vault's now
        with connect_users_db() as conn:
            try:
                conn.execute("UPDATE folder_links SET path = ?, state = ? WHERE id = ?", (new, json.dumps(state), link_id))
            except sqlite3.IntegrityError as e:
                raise LinkError(_taken({**link, "path": new})) from e
        from .local_agents import store as agents  # local: the runner imports the feedback modules

        agents.forget_threads(link_id)
        if engine(link):
            from .vault.local_remote import LocalRemote  # local: it imports this module

            LocalRemote(link["workspace_id"], link["folder_id"], link["created_by"]).relink(link["path"], new)
        with _status_guard, connect_users_db() as conn:
            r = conn.execute("SELECT status FROM folder_links WHERE id = ?", (link_id,)).fetchone()
            status = {k: v for k, v in _object_of(r[0] if r else "").items() if k not in _ERRORS}
            conn.execute("UPDATE folder_links SET status = ? WHERE id = ?", (json.dumps(status), link_id))
    if not choosing(link):
        run_in_background(link_id)
    return get_link(link_id)


# --- removing a link -----------------------------------------------------------------------

def _in_place(link: dict) -> list[tuple[str, str, str]]:
    """``(page id, path in the vault, doc id)`` of each PDF the vault's pages
    read where it is, that this server stores no copy of: what a removal
    stores first. A page in Recently deleted counts, its file in the vault's
    trash, or where it is for an in-place tombstone (``vault_store.on_disk``)."""
    with connect_vaults_db() as conn:
        rows = [(page_id, vault_store.on_disk({"path": path, "was": was})) for page_id, path, was in conn.execute(
            "SELECT page_id, path, was FROM vault_files WHERE link_id = ? AND kind = 'pdf' AND page_id != ''",
            (link["id"],))]
    if not rows:
        return []
    ws = link["workspace_id"]
    with connect_pages_db(ws) as conn:
        views = {r[0]: page_document(load_props(r[1])) for r in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted({page_id for page_id, _path in rows})),))}
    out = []
    for page_id, rel in rows:
        view = views.get(page_id)
        if in_place(view) and not storage.exists(ws, view["file"]):
            out.append((page_id, rel, view["hash"]))
    return out


def removal(link: dict) -> dict:
    """What removing the link would do, before it is asked: ``{pages,
    in_place, bytes, files, in_library}``, the pages it holds, of which a
    work folder's files not in the library (``files``) go to Recently
    deleted and the rest (``in_library``) stay, and the PDFs it reads in
    place with what they weigh, which are stored first."""
    from .vault import upgrade as vault_upgrade  # local: it imports this module

    if link["kind"] == "file":
        return {"pages": 1, "in_place": 0, "bytes": 0, "files": 0, "in_library": 1}
    if older(link):
        pages = vault_upgrade.pages_of(link)
        return {"pages": pages, "in_place": 0, "bytes": 0, "files": 0, "in_library": pages}
    with connect_vaults_db() as conn:
        pages = conn.execute("SELECT COUNT(DISTINCT page_id) FROM vault_files WHERE link_id = ? AND page_id != '' "
                             "AND was = ''", (link["id"],)).fetchone()[0]
    pdfs, size = [] if link["remote_url"] else _in_place(link), 0
    for _page_id, rel, _doc in pdfs:
        try:
            size += os.stat(dest_of(link) / rel).st_size
        except OSError:
            pass
    files = 0
    if _works(link):
        with connect_pages_db(link["workspace_id"]) as conn:
            files = conn.execute(f"SELECT COUNT(*) FROM unified_blocks WHERE work_folder = ? AND +parent_id = 'root' "
                                 f"AND {SQL_OUTSIDE_LIBRARY}", (link["folder_id"],)).fetchone()[0]
    return {"pages": pages, "in_place": len(pdfs), "bytes": size, "files": files, "in_library": max(pages - files, 0)}


def _works(link: dict) -> bool:
    """Whether the link holds a work folder to let go: it is one, or was one
    its round has not let go yet."""
    from .work_folders import takeover

    return work_folder_link(link) or bool(link["state"].get(takeover.TAKEN_OVER))


def _store_copy(ws: str, path: Path, doc: str) -> None:
    """The PDF at ``path`` stored, as the bytes ``doc`` it should be. One no
    longer there (its page's files in a trash the person emptied) is passed
    by, the log saying so: its bytes are gone whatever the removal does.
    LinkError for one that cannot be read or stored, or is something else by
    now: the removal stops there."""
    try:
        with open(path, "rb") as f:
            stored, _existed, _size = store_pdf_stream(ws, f)
    except FileNotFoundError:
        log.warning(f"[folders] {path} is not there to be stored: its page goes on with its PDF missing")
        return
    except (OSError, ValueError) as e:
        raise LinkError(f"{path} could not be stored, so the vault was not removed: {e}") from e
    except HTTPException as e:   # the quota
        raise LinkError(f"{path} could not be stored, so the vault was not removed: {e.detail}") from e
    if stored != doc:
        raise LinkError(f"{path} changed since the vault last read it: sync, then remove the vault.")


def _linked_pictures(link: dict) -> list[tuple[str, str]]:
    """``(upload name, path)`` of the pictures of the vault served from where
    they are (its rows of ``linked_files`` with no page) that this server
    stores no copy of."""
    below = str(dest_of(link)).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT name, path FROM linked_files WHERE ws = ? AND page_id = '' AND substr(path, 1, ?) = ?",
                            (link["workspace_id"], len(below), below)).fetchall()
    return [(name, path) for name, path in rows if not storage.exists(link["workspace_id"], name)]


def _remove_vault(link: dict, files: str) -> None:
    """A vault's removal, the module doc's: on its disk, the PDFs and
    pictures read in place stored (``_store_in_place``; a vault of another
    server reads none), then its trash taking the files its pulls made
    (``files=trash``), its feedback files going whatever ``files`` says
    (gamma/feedback/files.py: Gamma's own, never trashed) and its marker
    going. A vault whose disk is not there
    is left as it is on disk: its pages go on reading their files, missing,
    so that they can be located. A directory that answers but is no longer
    the vault's (its marker gone, another's there) has the vault's PDFs
    stored all the same, and nothing else of it touched. First, the state
    file of the gamma-sync client that a folder kept before left at the
    root (its upgrade could not take it away, gamma/vault/upgrade.py): it
    stands for nothing since, and a link made there again must not read it
    as that folder's; one another program holds stops the removal, nothing
    done."""
    from .feedback import files as feedback_files  # local: it reads the pages and the vaults' rows

    root = dest_of(link)
    try:
        vault_disk.check_marker(root, link["state"]["vault"])
        ours = True
    except vault_disk.Offline:
        return
    except vault_disk.Stopped:
        ours = False
    if ours:
        try:
            vault_disk.remove(root / gamma_sync.STATE)
        except OSError as e:
            raise LinkError(f"{root / gamma_sync.STATE} is open in another program ({vault_disk._why(e)}): close it, "
                            "then remove the vault again.") from e
    if not link["remote_url"]:
        _store_in_place(link, root)
    if not ours:
        return
    if files == "trash":
        _trash_ours(link, root)
    feedback_files.remove(root)
    (root / vault_disk.MARKER).unlink(missing_ok=True)


def _store_in_place(link: dict, root: Path) -> None:
    """The PDFs and pictures a vault of this server reads in place stored
    (nothing changed yet when one cannot be: LinkError), and every live page
    of the vault whose document is read in place and stored no longer
    reading it there (its document at ``store``: those PDFs, and a work
    folder's file of any format whose bytes were stored already, a library
    file's or a commented version; one in Recently deleted keeps reading in
    place, its copy stored). A work folder's files of other formats are
    stored by nothing here: Gamma copies nothing of a directory but what was
    asked to be kept (docs/dev/work_folders.md "Removing a work folder"); its
    pictures are files, so a kept folder's pictures alone are stored. Of the
    linked files below the directory only the vault's own rows go: a PDF
    opened from there before it was a vault reads where it is still."""
    ws, actor = link["workspace_id"], link["created_by"]
    pdfs = _in_place(link)
    pictures = [] if _works(link) else _linked_pictures(link)
    for _page_id, rel, doc in pdfs:
        _store_copy(ws, root / rel, doc)
    for name, path in pictures:
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            continue
        except OSError as e:
            raise LinkError(f"{path} could not be stored, so the vault was not removed: {e}") from e
        if storage.matches_name(name, data):
            storage.put_upload(ws, name, data)
    with connect_pages_db(ws) as conn:
        live = {page_id for page_id in _vault_pages(link["id"]) if page_root_id(conn, page_id) == page_id}
        views = {r[0]: page_document(load_props(r[1])) for r in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(live)),))}
    for page_id in sorted(live):
        view = views.get(page_id)
        if in_place(view) and storage.exists(ws, view["file"]):
            stored = {"doc": document_at(view, "store")}
            commit_ops(ws, page_id, [{"op": "set", "id": page_id, "props": stored}], actor=actor, client=DISK_CLIENT)
    below = str(root).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE ws = ? AND substr(path, 1, ?) = ? AND (page_id = '' OR page_id IN "
                     "(SELECT value FROM json_each(?)))", (ws, len(below), below, json.dumps(sorted(_vault_pages(link["id"])))))


def _trash_ours(link: dict, root: Path) -> None:
    """The files the vault's pulls made and nobody wrote in since (a push
    of a person's change makes a file theirs, ``ours`` 0; one changed and
    not pushed yet differs from its base), into its trash
    (``.gamma/trash``), and the directories that leaves empty; a file a
    person made, the trash and the rest of ``.gamma`` stay."""
    when = vault_disk.stamp()
    for rel, row in vault_store.files(link["id"]).items():
        if not row["ours"]:
            continue
        try:
            if not _as_pulled(root, rel, row):
                continue
            vault_disk.move(root, rel, vault_disk.trash_path(root, rel, when))
        except FileNotFoundError:
            continue
        except OSError as e:
            log.warning(f"[folders] {root / rel} stays where it is: {e}")
            continue
        _prune(root, (root / rel).parent)


def _as_pulled(root: Path, rel: str, row: dict) -> bool:
    """Whether the vault's file ``rel`` is as its base says: a note by its
    hash, any other file by its stat (a stored PDF's name need not be the
    hash of its bytes)."""
    if row["kind"] == "notes":
        return vault_disk.sha(vault_disk.read(root, rel)) == row["hash"]
    st = os.stat(vault_disk.path(root, rel))
    return (st.st_size, st.st_mtime_ns) == (row["size"], row["mtime_ns"])


def _prune(root: Path, directory: Path) -> None:
    """``directory`` and those above it inside ``root``, removed while empty."""
    while directory != root and inside(str(directory), str(root)):
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _forget(link: dict) -> None:
    """The row and what this computer kept of the link."""
    with connect_users_db() as conn:
        conn.execute("DELETE FROM folder_links WHERE id = ?", (link["id"],))
    if engine(link):
        vault_store.forget_link(link["id"])
    _polled.pop(link["id"], None)


def delete_link(link_id: str, files: str = "keep") -> bool:
    """Remove the link, its pages staying as they are (the module doc): a
    vault by ``_remove_vault``, its files kept (``keep``) or the ones its
    pulls made moved to its trash (``trash``). A folder kept before and not
    taken onto the engine yet is, for ``trash``, taken onto it first
    (gamma/vault/upgrade.py, which reads which files the client wrote), and
    with ``keep`` its directory stays as it is, its state file too, for a
    link made there again. A followed file is never touched. A work folder
    is let go once its PDFs are stored (``_release_work``), and one whose
    files were never chosen is taken back as if it had not been made
    (``_cancel``). After a round that runs finishes. LinkError when a PDF
    read in place cannot be stored, or the state file a folder kept before
    left is held open (``_remove_vault``): nothing is removed. A remote
    source's token stays on that server (whoever minted it revokes it: the
    desktop app does when it drops a link it made). A run of the vault's
    agent stops first; its row goes with the vault's (``vault_store.forget_link``)."""
    from .vault import upgrade as vault_upgrade  # local: it imports this module

    link = get_link(link_id)
    if link is None:
        return False
    _stop_agent(link_id)
    with _holding(link_id):
        link = get_link(link_id) or link
        if choosing(link):
            _cancel(link)
        else:
            if older(link) and files == "trash":
                try:
                    link = vault_upgrade.upgrade(link)
                except (vault_disk.Offline, vault_disk.Stopped, OSError) as e:
                    log.warning(f"[folders] removing {link['path']}: its files stay: {e}")
            if engine(link) and not older(link):
                _remove_vault(link, files)
                if _works(link):
                    _release_work(link)
        _forget(link)
    return True


def _cancel(link: dict) -> None:
    """A work folder whose files were never chosen taken back (the tracking
    dialog's Cancel): what making it made, the marker, ``.gamma/`` when it
    is left empty and the folder when nothing is filed there, and its mark
    from a folder it was made of, a note made there meanwhile kept as a
    library page (``takeover.release``). It never wrote a ``.gammaignore``."""
    from .vault import sync as vault_sync  # local: it imports this module
    from .work_folders import takeover

    root, made = dest_of(link), link["state"].get("made") or {}
    if made.get("marker"):
        try:
            vault_disk.check_marker(root, link["state"]["vault"])
            (root / vault_disk.MARKER).unlink(missing_ok=True)
        except (vault_disk.Offline, vault_disk.Stopped, OSError) as e:
            log.warning(f"[folders] {root}: its marker stays: {e}")
        else:
            if made.get("marker_dir"):
                _drop_empty(root / vault_disk.MARKER_DIR)
    try:
        remote = vault_sync.remote_for(link)
        takeover.release(link, remote, trash_files=False)
        if made.get("folder"):
            remote.drop_folder(link["folder_id"])
    except (OpError, vault_sync.Refused) as e:
        log.warning(f"[folders] {root}: its folder stays as it is: {getattr(e, 'detail', '') or e}")


def _release_work(link: dict) -> None:
    """A removed work folder let go (``takeover.release``): its files not in
    the library to Recently deleted, the others library pages, filed where
    they were; then the directory folders below its folder that hold no
    live page deleted, deepest first, and its folder too when that leaves
    it empty: they stood for directories, and nothing is filed there now."""
    from .vault import sync as vault_sync  # local: it imports this module
    from .work_folders import takeover

    w = link["folder_id"]
    with connect_pages_db(link["workspace_id"]) as conn:
        tree = marks.Tree.read(conn)

    def depth(folder: str) -> int:
        n = 0
        while folder in tree.parent and folder != w:
            folder, n = tree.parent[folder], n + 1
        return n

    try:
        remote = vault_sync.remote_for(link)
        takeover.release(link, remote, trash_files=True)
        for folder in sorted(tree.subtree(w), key=depth, reverse=True):
            remote.drop_folder(folder)
    except (OpError, vault_sync.Refused) as e:
        log.warning(f"[folders] {link['path']}: its work folder stays as it was: {getattr(e, 'detail', '') or e}")


# --- reading and writing Gamma here -----------------------------------------------------------

def _page(ws: str, page_id: str) -> dict | None:
    """``{doc_id, filename, title}`` of a page of this server's workspace:
    the hash of the document it owns and that document's name (``""`` for
    no PDF or no file name); None when ``page_id`` is no page of the
    library: not there, a block inside a page, or in the trash."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id, content, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    if not row or row[0] != "root":
        return None
    view = page_document(load_json(row[2] or "{}") or {})
    return {"doc_id": own_hash(view), "filename": view["name"] if view else "", "title": row[1] or ""}


def may_edit(ws: str, user_id: str) -> bool:
    """Whether the account that made a link may still write its workspace:
    asked before each write a round makes there."""
    return workspaces.at_least(workspaces.role_of(ws, user_id), "editor")


class LocalSink:
    """Where a followed file's push writes in this server's own workspace, as
    the account that made the link: the three calls of ``gamma_sync.RemoteSink``
    without HTTP (a vault's round stores a PDF through it too). Each write,
    and the read of the page a push makes before one, first asks whether the
    account may still edit the workspace."""

    def __init__(self, ws: str, user_id: str):
        self.ws, self.user_id = ws, user_id

    def _may_write(self) -> None:
        if not may_edit(self.ws, self.user_id):
            raise gamma_sync.SyncError("you may no longer edit this workspace")

    def store(self, path: Path, ext: str = ".pdf") -> str:
        """The file stored as an upload is (spooled, hashed, the quota and
        the first bytes checked: a ``.pdf``'s header, a ``.webm``'s
        container, ``storage.content_refusal``), under ``ext``: → its hash,
        the stored name's stem."""
        self._may_write()
        try:
            with open(path, "rb") as f:
                if ext == ".pdf":
                    doc_id, _existed, _size = store_pdf_stream(self.ws, f)
                else:
                    doc_id = store_file_stream(self.ws, f, ext)[0].removesuffix(ext)
        except ValueError as e:
            raise gamma_sync.SyncError(str(e)) from e
        except HTTPException as e:   # the quota
            raise gamma_sync.SyncError(str(e.detail)) from e
        return doc_id

    def replace(self, page_id: str, doc_id: str, filename: str, *, check_held: bool = True) -> dict:
        """The page takes the file ``doc_id``: stored, or still read in place
        on a page that reads its file in place (``replace_attachment``).
        ``check_held`` False: bytes
        another page carries are no refusal, for a page reading a file where
        it is, whose bytes are whatever the disk holds (gamma/linked_files.py)."""
        from .routers.pages import replace_attachment, replaced_answer  # local: keep gamma.* free of the routers package

        self._may_write()
        try:
            done = replace_attachment(self.ws, page_id, doc_id, "", filename, actor=self.user_id, owner=self.user_id,
                                      check_held=check_held, keep_old_days=gamma_sync.KEEP_OLD_DAYS)
        except OpError as e:   # the page gone, its Zotero PDF on its way, or the bytes another page's (HeldElsewhere)
            raise gamma_sync.SyncError(str(e.detail)) from e
        return replaced_answer(done)

    def current(self, page_id: str) -> dict | None:
        """The page (``_page``), which a push reads just before it writes, so
        the account is asked first: one that may not write learns nothing of
        the page, whether it stands or what it holds, and every file it
        changes reads the same."""
        self._may_write()
        return _page(self.ws, page_id)


def remote_server(link: dict, timeout: float = 120) -> gamma_sync.Server:
    """The other Gamma server a link reads, with its token (``get_link``
    with ``with_token``). A round's requests may wait two minutes on a
    silent server (a large PDF); a question asked between rounds
    ``ASK_TIMEOUT_S``."""
    return gamma_sync.Server(link["remote_url"], link.get("token") or "", timeout=timeout)


def sink_of(link: dict):
    """Where a file link's pushes write: the remote server over HTTP with the
    link's token (``get_link`` with ``with_token``), or this server's
    workspace in-process as the account that made the link."""
    if link["remote_url"]:
        return gamma_sync.RemoteSink(remote_server(link))
    return LocalSink(link["workspace_id"], link["created_by"])


# --- a page and its vault -------------------------------------------------------------------

def vault_of_page(ws: str, page_id: str) -> dict | None:
    """The link of this server that keeps the page of ``ws``: its file link,
    else the folder link whose folder holds it, a page held by two being the
    one the engine gives it (``vault.remote.remote_for``: a vault that pushes
    below another's holds its pages, else the first made), one that pushes
    before one that only pulls a copy; None for none. A work folder's file
    is the vault of its work folder's, by its stamp alone, or none's here
    (another computer's work folder, mirrored)."""
    from .vault.remote import remote_for  # local: it imports this module

    links = [link for link in all_links() if not link["remote_url"] and link["workspace_id"] == ws]
    file = next((link for link in links if link["kind"] == "file" and link["page_id"] == page_id), None)
    if file or not links:
        return file
    folders = [link for link in links if link["kind"] == "folder"]
    if not folders:
        return None
    with connect_pages_db(ws) as conn:
        if page_root_id(conn, page_id) != page_id:
            return None
        stamp = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if stamp and stamp[0]:
            return next((link for link in folders if link["folder_id"] == stamp[0]), None)
        holding = [link for link in folders if link["folder_id"] == folder_sync.ROOT
                   or page_in_folder(conn, page_id, link["folder_id"])]
        for link in sorted(holding, key=lambda link: not link["push"]):
            away = remote_for(link).leave_out
            if not any(page_in_folder(conn, page_id, folder) for folder in away):
                return link
    return None


def _page_files(link: dict, page_id: str) -> dict:
    """``{kind: (path in the vault, problem)}`` of the page's files where
    they are, as the engine vault's rows have them."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT kind, path, problem FROM vault_files WHERE link_id = ? AND page_id = ? AND was = '' "
                            "ORDER BY path", (link["id"], page_id)).fetchall()
    return {kind: (rel, problem or "") for kind, rel, problem in rows}


def page_vault(ws: str, page_id: str, *, can_act: bool, user_id: str) -> dict | None:
    """How the page stands with its vault (``vault_of_page``), for its chip:
    ``{id, name, scope, path, pull, push, problem, conflicts, paused,
    work_folder, choosing, library, page: {library, file_name}}``, ``path``
    the page's file on this computer ("" for none yet, a folder kept before
    whose first round has not run among them, and for a member who cannot
    act on it, ``can_act`` false, but for the vault's maker, ``user_id``,
    whatever its role now: where the files lie is the maker's reading),
    ``problem`` why its file was not written or read, the vault's own reason
    first (``_halted``), ``conflicts`` the clashes the file won that are
    open; ``work_folder`` whether the vault is one, ``choosing`` whether its
    files wait to be chosen, ``library`` its library switch, ``mine``
    whether ``user_id`` made the vault (whose comments may name its
    agent: gamma/local_agents/mention.py), and ``page``
    the page's own stamps: whether it is in the library, and its file's
    name. On a server that runs agents, ``agent`` too: the work folder's
    agent on this computer (``local_agents.info``: its run's detail for
    those shown the path alone), null for none. None for a page of no
    vault."""
    from . import local_agents
    from .local_agents import info as agent_info  # local: the runner it asks imports the feedback modules

    link = vault_of_page(ws, page_id)
    if link is None:
        return None
    path, problem, clashes = "", "", 0
    if link["kind"] == "file":
        path = link["path"]
    else:
        files = _page_files(link, page_id)
        rel, problem = files.get("notes") or files.get("pdf") or ("", "")
        problem = (files.get("notes") or ("", ""))[1] or problem
        path = str(dest_of(link) / rel) if rel else ""
        clashes = len(conflicts(link, page_id))
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    props = load_props(row[0]) if row else {}
    shown = can_act or link["created_by"] == user_id
    out = {"id": link["id"], "name": dest_of(link).name, "scope": link["kind"], "path": path if shown else "",
           "pull": bool(link["pull"]), "push": push_word(link["push"]), "problem": _halted(link) or problem,
           "conflicts": clashes, "paused": bool(link["status"].get(PAUSED)), "work_folder": work_folder_link(link),
           "choosing": choosing(link), "library": bool(link["library"]), "mine": link["created_by"] == user_id,
           "page": {"library": props.get("library") is True, "file_name": str(props.get("file_name") or "")}}
    if local_agents.enabled():
        out["agent"] = agent_info.agent_info(link, can_act=shown)
    return out


def _halted(link: dict) -> str:
    """Why the vault reaches no file now, as its pages' chips say it ("" while
    it does): its files are not chosen yet, it stopped, its directory is not
    there, its guard holds it, its last round did not finish, or its
    followed file is gone. Words of its own, never the status's, which may
    name a place on this computer."""
    status = link["status"]
    if choosing(link):
        return "the work folder's files are not chosen yet: Choose files… in its folder's menu"
    if status.get("stopped"):
        return "the vault stopped; the sync panel says why"
    if status.get("offline"):
        return "the vault's directory is not there"
    if status.get("held"):
        return "the vault is held: many of its files went at once; the sync panel goes on"
    if status.get("last_error"):
        return "its last round did not finish; the sync panel says why"
    if status.get("waiting") == gamma_sync.NOT_THERE:
        return gamma_sync.NOT_THERE
    return ""


def _note_of(ws: str, page_id: str) -> tuple[dict, str] | None:
    """The engine vault of the page and its notes file's path in it; None
    for a page with no notes file in one. LinkError when the directory is
    not the vault's now (its marker gone or another's, its disk not there),
    as each round would find it: nothing there is read or written."""
    link = vault_of_page(ws, page_id)
    if link is None or not engine(link) or older(link):
        return None
    files = _page_files(link, page_id)
    if "notes" not in files:
        return None
    try:
        vault_disk.check_marker(dest_of(link), link["state"]["vault"])
    except (vault_disk.Stopped, vault_disk.Offline) as e:
        raise LinkError(f"The vault's directory cannot be used now: {e}.") from e
    return link, files["notes"][0]


def _not_in_the_cloud(root: Path, rel: str) -> None:
    """LinkError for a notes file that is a cloud placeholder, as the scan
    finds one (``vault_disk._placeholder``): a round never reads one, which
    would bring its bytes down, or writes over it, and neither does the file
    editor."""
    try:
        st = vault_disk.lstat(root, rel)
    except OSError as e:
        raise LinkError(f"Cannot read {root / rel}: {e}") from e
    if st is not None and vault_disk._placeholder(st):
        raise LinkError("The file is in the cloud, not on this computer, so it is not edited here: "
                        "keep it on this device first.")


def read_note(ws: str, page_id: str) -> dict | None:
    """The page's notes file as it is on disk: ``{text, hash, path}``, the
    hash of its bytes for a write to name (``write_note``). None for a page
    with no notes file; LinkError for one that cannot be read as text, or a
    cloud placeholder (``_not_in_the_cloud``)."""
    found = _note_of(ws, page_id)
    if found is None:
        return None
    link, rel = found
    target = dest_of(link) / rel
    _not_in_the_cloud(dest_of(link), rel)
    try:
        data = vault_disk.read(dest_of(link), rel)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise LinkError(f"Cannot read {target}: {e}") from e
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise LinkError("The file is not UTF-8 text, so it cannot be edited here.") from e
    return {"text": text, "hash": vault_disk.sha(data), "path": str(target)}


def write_note(ws: str, page_id: str, text: str, hash: str) -> dict | None:
    """The page's notes file written whole with ``text`` from Gamma's file
    editor, as a person's editor would save it, and its page's round
    marked: the file must still be the bytes ``hash`` names (NoteMoved with
    what it holds now), the text must read back as a note Gamma holds
    (``md_lens.parse``, no block past ``ops.MAX_CONTENT``), the file must not
    be a cloud placeholder (``_not_in_the_cloud``), and the write is
    ``vault_disk.replace_note``, the bytes it replaced kept in the vault's
    history. Only a vault that pushes everything takes it: the file is then
    what the page becomes. → ``{hash, path}``; None for a page with no notes
    file."""
    from .vault.sync import fits  # local: it imports this module

    found = _note_of(ws, page_id)
    if found is None:
        return None
    link, rel = found
    if link["push"] != PUSH_ALL:
        raise LinkError("This vault does not push notes, so its files are not edited from Gamma.")
    root, data = dest_of(link), text.encode("utf-8")
    doc = md_lens.parse(text)
    try:
        if doc.problem:
            raise md_lens.Unwritable(doc.problem)
        fits(node.content for node in doc.flat)
    except md_lens.Unwritable as e:
        raise LinkError(f"That text cannot be a note here: {e}.") from e
    lock = _lock(link["id"])
    if not lock.acquire(timeout=ROUND_WAIT_S):
        raise LinkError("The vault is syncing: try again in a moment.")
    try:
        _not_in_the_cloud(root, rel)
        try:
            current = vault_disk.read(root, rel)
        except FileNotFoundError:
            return None
        if vault_disk.sha(current) != hash:
            raise NoteMoved(vault_disk.sha(current))
        try:
            written = vault_disk.replace_note(root, rel, data)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise LinkError(f"Cannot write {root / rel}: {e}") from e
        if written.replaced is not None:     # always when it was not what the editor read: a save meanwhile
            vault_store.keep(link["id"], rel, written.replaced, always=vault_disk.sha(written.replaced) != hash)
    finally:
        _let_go(link["id"], lock)
    mark(ws, page_id, at_once=True)
    return {"hash": vault_disk.sha(data), "path": str(root / rel)}


def _vault_pages(link_id: str) -> set:
    with connect_vaults_db() as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT page_id FROM vault_files WHERE link_id = ? AND page_id != ''",
                                           (link_id,))}


def conflicts(link: dict, page_id: str = "") -> list[dict]:
    """The open clashes the vault's files won (``sync_engine.VAULT_KINDS``),
    on its pages, one page's with ``page_id``: never a mirror's or a Zotero
    link's rows."""
    from .sync_engine import VAULT_KINDS, list_conflicts  # local: it imports the routers

    if not engine(link) or link["remote_url"]:
        return []
    pages = _vault_pages(link["id"])
    return [c for c in list_conflicts(link["workspace_id"], page_id=page_id, kinds=VAULT_KINDS) if c["page_id"] in pages]


def resolve(link: dict, conflict_id: int, choice: str) -> dict | None:
    """``sync_engine.resolve_conflict`` for a clash of the vault's, as the
    account that made the vault: ``theirs`` is the file's text, ``mine``
    Gamma's, which the next pull writes to the file. None for a row that is
    not the vault's."""
    from .sync_engine import VAULT_KINDS, resolve_conflict  # local: it imports the routers

    if not engine(link) or link["remote_url"]:
        return None
    with connect_pages_db(link["workspace_id"]) as conn:
        row = conn.execute("SELECT page_id, kind FROM sync_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    if not row or row[1] not in VAULT_KINDS or row[0] not in _vault_pages(link["id"]):
        return None
    return resolve_conflict(link["workspace_id"], conflict_id, choice, actor=link["created_by"], kinds=VAULT_KINDS)


# --- rounds -----------------------------------------------------------------------------------

def _write_status(link_id: str, status: dict, cursor: str | None = None, state: dict | None = None) -> None:
    """A round's status, written whole but for the pause: that stays as
    stored, whatever the round read when it began (``set_paused``). With it
    a folder link's ``cursor``, or a link's ``state``."""
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


def keep_state(link_id: str, state: dict) -> None:
    """A link's ``state`` stored, nothing else of its row: the step that takes
    a folder kept before onto the engine (gamma/vault/upgrade.py)."""
    with _status_guard, connect_users_db() as conn:
        conn.execute("UPDATE folder_links SET state = ? WHERE id = ?", (json.dumps(state), link_id))


def _failed(link: dict, error: str) -> dict:
    status = {**link["status"], "running": False, "last_error": error, "failed_at": time.time()}
    _write_status(link["id"], status)
    log.warning(f"[folders] {link['path']}: {error}")
    return status


def _take(link_id: str, asked: dict, wait: bool = False) -> tuple[dict | None, float | None]:
    """The link's lock and the link marked ``running`` (``started_at``), as
    it stands once the lock is held: ``(link, started)``. When the lock is
    held already: with ``wait`` this waits for it (up to ``ROUND_WAIT_S``);
    else, or when it outlasts the wait, the round ``asked`` is kept and runs
    once the lock is let go (``_again``, where requests made meanwhile add
    up: none loses a force or a confirmation another asked for), and
    ``(link, None)``. ``(None, None)`` for no such link. The last try for the
    lock and the request kept are one step under ``_guard`` (``_let_go``)."""
    link = get_link(link_id)
    if link is None:
        return None, None
    lock = _lock(link_id)
    if not (wait and lock.acquire(timeout=ROUND_WAIT_S)):
        with _guard:
            if not lock.acquire(blocking=False):
                _again[link_id] = _together(_again.get(link_id), asked)
                return link, None
    started = time.time()
    try:
        link = get_link(link_id)     # read again: what held the lock (a round, a removal) may have changed it
        if link is not None:
            _write_status(link_id, {**link["status"], "running": True, "started_at": started})
    except BaseException:
        _let_go(link_id, lock)
        raise
    if link is None:
        _let_go(link_id, lock)
        return None, None
    return link, started


def _held(link_id: str, link: dict, started: float, asked: dict) -> dict:
    """The round ``asked`` of a link ``_take`` marked running, its lock let
    go after it (``_let_go``): its status afterwards (``_vault_round``,
    ``_file_round``)."""
    try:
        link = get_link(link_id, with_token=True) or link
        if link["kind"] == "file":
            return _file_round(link, started)
        return _vault_round(link, started, **asked)
    except (gamma_sync.SyncError, LinkError, OSError) as e:
        return _failed(link, error_text(e))
    except Exception as e:  # noqa: BLE001 — never leave the link "running" with no reason shown
        log.exception(f"[folders] {link['path']}: the round failed")
        return _failed(link, f"unexpected error: {e!r}")
    finally:
        _let_go(link_id, _lock(link_id))


def run_link(link_id: str, *, force: str = "", direction: str = "both", confirm: bool = False,
             wait: bool = False) -> dict | None:
    """One round of the link now, in this thread: its status afterwards
    (``_vault_round``, ``_file_round``), None for no such link. When a round
    runs already: with ``wait`` this one waits for it (up to
    ``ROUND_WAIT_S``) and runs then; else, or when that round outlasts the
    wait, the request is kept for after it (``_take``) and the answer is the
    current status, which reads ``running`` from then until the kept round
    ends (``_row``). ``force``
    is ``pull`` or ``push``, ``direction`` narrows a round to ``pull`` or
    ``push``, ``confirm`` lets a vault's round past its guard."""
    asked = {"force": force, "direction": direction, "confirm": confirm}
    link, started = _take(link_id, asked, wait)
    if started is None:
        return (get_link(link_id) or link)["status"] if link else None   # read again: kept, it reads running
    return _held(link_id, link, started, asked)


def _progress_writer(link: dict):
    """What a vault's round reports of itself (``vault.sync.Round``'s
    ``progress``) written into the status as ``progress`` while it runs,
    beside the ``running`` and ``started_at`` the round began with: once the
    round has run ``PROGRESS_S``, then at most every ``PROGRESS_S``, and
    always once it has done all it counted. A round quicker than that, as the
    tick's round of a quiet vault is, writes none, and the round's own status
    at its end has none."""
    last = [time.monotonic(), False]   # when it began or last wrote, and whether it wrote

    def write(progress: dict) -> None:
        now = time.monotonic()
        if now - last[0] < PROGRESS_S and not (last[1] and progress["done"] >= progress["total"]):
            return
        last[:] = [now, True]
        _write_status(link["id"], {**link["status"], "running": True, "progress": progress})
    return write


def _let_go(link_id: str, lock: threading.Lock) -> None:
    """The link's lock released by whatever held it (a round, a page's round,
    a write from Gamma's file editor, and through ``_holding`` a vault being
    made, a relink, a removal, the tick's forgetting), and a round asked for
    meanwhile (``_again``) started: the release and the look one step under
    ``_guard``, as ``run_link``'s request is, so none is left behind. Of a
    link that is gone the round finds nothing, and the request goes. Until
    that round is marked running the link reads running all the same
    (``_starting``), so a client following Sync now never finds it between
    the two."""
    with _guard:
        lock.release()
        again = _again.pop(link_id, None)
        if again is not None:
            _starting[link_id] = _starting.get(link_id, 0) + 1
    if again is not None:
        try:
            run_in_background(link_id, **again)
        finally:
            with _guard:
                if _starting[link_id] > 1:
                    _starting[link_id] -= 1
                else:
                    del _starting[link_id]


@contextmanager
def _holding(link_id: str):
    """The link's lock, waited for, held for the block and let go
    (``_let_go``)."""
    lock = _lock(link_id)
    lock.acquire()
    try:
        yield
    finally:
        _let_go(link_id, lock)


@contextmanager
def try_holding(link_id: str):
    """The link's lock when nothing holds it, held for the block and let go
    (``_let_go``): the block gets True; False, nothing held, while a round,
    a page's round or a feedback pass holds it (a request file written at
    once, gamma/feedback/files.py ``write_request``: the hot pass writes it
    then)."""
    lock = _lock(link_id)
    if not lock.acquire(blocking=False):
        yield False
        return
    try:
        yield True
    finally:
        _let_go(link_id, lock)


def _together(kept: dict | None, asked: dict) -> dict:
    """Two requests for a round as the one that runs: a force or a
    confirmation either asked for stays (the later force when both name one),
    and two directions are both."""
    if kept is None:
        return asked
    return {"force": asked["force"] or kept["force"],
            "direction": kept["direction"] if kept["direction"] == asked["direction"] else "both",
            "confirm": kept["confirm"] or asked["confirm"]}


def _stopped(link: dict, started: float, why: str) -> dict:
    """The status of a round ``check_vault`` refused: nothing ran."""
    status = {"running": False, "started_at": started, "last_sync": link["status"].get("last_sync", ""),
              "last_error": why, "failed_at": time.time(), "stopped": why, "counts": dict.fromkeys(COUNTS, 0),
              "kept": [], "folder_path": link["status"].get("folder_path") or []}
    _write_status(link["id"], status)
    log.warning(f"[folders] {link['path']}: {why}")
    return status


def _folder_names(link: dict, ask: bool = True) -> list[str]:
    """The names of the vault's folder from the top, as its remote has them:
    another server's asked when ``ask`` (a round that pulls), else the
    status's last ones, as when it does not answer, and as when the account
    that made it has no role in its workspace here any more (it learns
    nothing new of a workspace it left)."""
    kept = link["status"].get("folder_path") or []
    if link["folder_id"] == folder_sync.ROOT:
        return []
    if link["remote_url"] and not ask:
        return kept
    if link["remote_url"]:
        try:
            tree = remote_server(link, ASK_TIMEOUT_S).get_json("/api/sync/folders")
        except gamma_sync.SyncError:
            return kept
        return next((f["path"] for f in tree.get("folders", []) if f.get("id") == link["folder_id"]), [])
    if not workspaces.role_of(link["workspace_id"], link["created_by"]):
        return kept
    with connect_pages_db(link["workspace_id"]) as conn:
        return folder_path(conn, link["folder_id"])


def forced(link: dict, force: str) -> dict:
    """The link as ``check_vault`` judges a round of it with ``force``: a
    force push makes the pages the files, which is pushing everything."""
    return {**link, "push": PUSH_ALL} if force == "push" else link


def _vault_round(link: dict, started: float, *, force: str, direction: str, confirm: bool) -> dict:
    """A vault's round (``vault.sync.sync``), its progress in the status while
    it runs (``_progress_writer``), refused first by ``check_vault`` as it
    stands now; a folder kept before is taken onto the engine first
    (gamma/vault/upgrade.py), which the status keeps saying from then on
    (``upgraded_at``). One that pushes PDFs into a folder another vault made
    before it pushes into too, which links kept before were let do, has its
    push turned off then, and the status says why (``push_off``) while it
    stays off. → its status (the API contract): ``{running, started_at,
    last_sync, last_error, failed_at?, counts, kept, untracked, refused?,
    capped?, held?, stopped?, offline?, shared?, upgraded_at?, push_off?,
    feedback?, folder_path}``, and ``paused_at`` kept as it was (``_write_status``); a
    round that the marker, the disk or the account (or another server)
    stopped counts as failed, so ``RETRY_S`` applies. A vault of this server
    whose maker has no role in its workspace any more fails at once, before
    ``check_vault``, whose reasons read the workspace as it is now and name
    other vaults' places: it learns nothing new of a workspace it left
    (``_folder_names``). One whose maker may only view there now runs, and
    a refusal tells it no reason (``REFUSED_UNSAID``). A work folder whose
    files are not chosen yet runs no round (``CHOOSE_FIRST``); one from
    before work folders is taken over first, and a vault that stopped being
    one is let go (gamma/work_folders/takeover.py): the status says since
    when it is one (``work_folder_at``). A work folder's round then writes
    its feedback files (gamma/feedback/files.py), a held round too, unless
    the marker, the disk or the account stopped it: ``feedback`` is that
    write's summary."""
    from .vault import local_remote, sync as vault_sync, upgrade as vault_upgrade  # local: they import this module
    from .feedback import files as feedback_files
    from .work_folders import takeover

    if choosing(link):
        return _stopped(link, started, CHOOSE_FIRST)
    if not link["remote_url"] and not workspaces.role_of(link["workspace_id"], link["created_by"]):
        return _failed(link, local_remote.NO_ACCESS)
    lowered = link["status"].get("push_off")
    try:
        check_vault(forced(link, force), earlier=True)
    except LinkError as e:
        if not (older(link) and link["push"] and link["pull"] and not force
                and not _refusal_of({**link, "push": PUSH_OFF}, earlier=True)):
            return _stopped(link, started, str(e))
        with connect_users_db() as conn:      # two folders kept before, both let push one folder's PDFs then
            conn.execute("UPDATE folder_links SET push = ? WHERE id = ?", (PUSH_OFF, link["id"]))
        link, lowered = {**link, "push": PUSH_OFF}, f"push turned off when it was taken onto synced vaults: {e}"
    if older(link):
        try:
            link = vault_upgrade.upgrade(link, vault_sync.remote_for(link))
        except (vault_disk.Offline, vault_disk.Stopped, vault_sync.Refused) as e:
            why = {"stopped": str(e)} if isinstance(e, vault_disk.Stopped) else {
                "offline": True} if isinstance(e, vault_disk.Offline) else {}
            return _failed({**link, "status": {**link["status"], "started_at": started, **why}}, str(e))
    try:
        link = _work_turn(link)
    except (vault_disk.Offline, vault_disk.Stopped, vault_sync.Refused, OpError) as e:
        why = {"stopped": str(e)} if isinstance(e, vault_disk.Stopped) else {
            "offline": True} if isinstance(e, vault_disk.Offline) else {}
        return _failed({**link, "status": {**link["status"], "started_at": started, **why}},
                       str(getattr(e, "detail", "") or e))
    # kept in the state at the upgrade's commit point: a round that died after it, or failed, loses none of it
    upgraded = link["state"].get(vault_upgrade.UPGRADED) or link["status"].get("upgraded_at")
    result = vault_sync.sync(link, direction=direction, force=force, confirm=confirm, progress=_progress_writer(link))
    status = {**result.status, "started_at": started, "folder_path": _folder_names(link, ask=direction != "push")}
    if upgraded:
        status["upgraded_at"] = upgraded
    if result.state.get(takeover.TAKEN_OVER):
        status["work_folder_at"] = result.state[takeover.TAKEN_OVER]
    if lowered and not link["push"]:
        status["push_off"] = lowered
    if status.get("last_error"):
        status["failed_at"] = time.time()
        log.warning(f"[folders] {link['path']}: {status['last_error']}")
    if work_folder_link(link) and not status.get("stopped") and not status.get("offline"):
        status["feedback"] = feedback_files.write(link)
    _write_status(link["id"], status, cursor=result.cursor, state=result.state)
    return status


def _work_turn(link: dict) -> dict:
    """The vault before its round: a work folder from before work folders
    taken over (``takeover.take_over``), a vault that is no longer one let
    go (``takeover.release``, its files library pages), its state then kept
    with or without ``TAKEN_OVER``: the commit point, so a round killed
    before it does it again. A vault let go loses its feedback files
    (gamma/feedback/files.py). → the link as the round reads it."""
    from .vault import sync as vault_sync  # local: it imports this module
    from .feedback import files as feedback_files
    from .work_folders import takeover

    work, taken = work_folder_link(link), link["state"].get(takeover.TAKEN_OVER)
    if work and not taken:
        link = {**link, "state": takeover.take_over(link, vault_sync.remote_for(link))}
    elif taken and not work:
        takeover.release(link, vault_sync.remote_for(link), trash_files=False)
        feedback_files.remove(dest_of(link))
        link = {**link, "state": {k: v for k, v in link["state"].items() if k != takeover.TAKEN_OVER}}
    else:
        return link
    keep_state(link["id"], link["state"])
    return link


def _file_round(link: dict, started: float) -> dict:
    """A file link's round: one push of its file (``gamma_sync.Push``, mode
    "file"), what it learnt of the file kept in the row's ``state``. A file
    the page did not take (the page gone, the bytes another page's, the
    account no longer an editor, which is then told only that, whatever
    became of the page: ``LocalSink.current``) fails the link, so
    ``RETRY_S`` applies; a file still being written only waits. →
    ``{running, started_at, last_sync, last_error, failed_at?, counts,
    waiting, last_sent, title, relocation_error?}``: ``last_sent`` and
    ``relocation_error`` are the last send's, ``title`` the page's as the
    last send left it."""
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


def run_in_background(link_id: str, *, force: str = "", direction: str = "both", confirm: bool = False) -> None:
    """A round of the link in a thread of its own, marked ``running`` before
    this returns (the lock taken here and let go by the round), so Sync
    now's answer already says so and the client follows ``running`` down
    to the round's end, and the tick's next look passes over it. One asked
    for while the lock is held is kept for after (``_take``), the link
    reading ``running`` until that round ends (``_row``); no such link,
    nothing. ``run_link``'s words."""
    asked = {"force": force, "direction": direction, "confirm": confirm}
    link, started = _take(link_id, asked)
    if started is None:
        return
    try:
        threading.Thread(target=_held, args=(link_id, link, started, asked), daemon=True,
                         name=f"folder-link-{link_id[:8]}").start()
    except BaseException:
        _failed(link, "the round could not start")
        _let_go(link_id, _lock(link_id))
        raise


def _age(stamp) -> float:
    return time.time() - float(stamp) if stamp else float("inf")


def _remote_moved(link: dict) -> bool | None:
    """Whether the remote server's change feed moved past the link's
    cursor — asked at most every ``REMOTE_POLL_S``. A server that cannot
    be reached or refuses the token counts as a failed round, so the retry
    wait applies and the status says why: None, and no round of push alone
    runs either, which would write a status that says nothing of it."""
    if time.time() - _polled.get(link["id"], 0.0) < REMOTE_POLL_S:
        return False
    _polled[link["id"]] = time.time()
    try:
        keyed = get_link(link["id"], with_token=True) or link
        return gamma_sync.changed_since(remote_server(keyed, timeout=ASK_TIMEOUT_S), link["cursor"])
    except (gamma_sync.SyncError, LinkError) as e:
        _failed(link, str(e))
        return None


def _resting(link: dict) -> bool:
    """Whether the link waits whatever changed: paused, a round of it running
    in this process (its lock held: a ``running`` left in the status by a
    process that died is no round), or ``RETRY_S`` not over since a failure."""
    st = link["status"]
    if st.get(PAUSED) or _lock(link["id"]).locked():
        return True
    return bool(st.get("last_error")) and _age(st.get("failed_at")) < RETRY_S


def due(link: dict) -> str:
    """The round the loop runs of the link now, its direction ("" for none):
    none while it rests (``_resting``), a whole one when it has never synced
    or its last round failed long enough ago. Otherwise a vault of this
    server a whole one (its scan is the truth, a stat per file); a vault of
    another server a whole one when that server's feed moved past its cursor
    (``_remote_moved``), else, when it pushes, one of push alone, which asks
    that server nothing while the disk is quiet; none when that server could
    not be asked, which rests the link as a failed round does; a file link
    when its file moved (``gamma_sync.moved``)."""
    st = link["status"]
    if _resting(link):
        return ""
    if st.get("last_error") or not st.get("last_sync"):
        return "both"
    if link["kind"] == "file":
        return "both" if gamma_sync.moved(dest_of(link), link["state"]) else ""
    if not link["remote_url"]:
        return "both"
    moved = _remote_moved(link)
    if moved is None:
        return ""
    return "both" if moved else "push" if link["push"] else ""


def tick() -> None:
    """The loop's round: every due link. A link of this server's workspace
    runs here, one after another; a link to another server, folder or file,
    runs in a thread of its own (its lock held keeps the next tick off it),
    so a slow or silent server never holds up the rest. A link whose
    workspace on this server is gone is forgotten, its directory as it is.
    Then the linked files' sweep (``linked_files.sweep``). Nothing runs
    where links are not kept (``config.folder_links_enabled``): a row left
    from before stays as it is. One tick at a time: one a hint asks for
    while another runs runs again after it (``_tick_asked``), for a link that
    tick has passed already may have changed since."""
    if not config.folder_links_enabled():
        return
    _tick_asked.set()
    while _tick_asked.is_set() and _ticking.acquire(blocking=False):
        try:
            _tick_asked.clear()
            _tick_once()
        finally:
            _ticking.release()


def _tick_once() -> None:
    for link in all_links():
        if not link["remote_url"] and not workspaces.get(link["workspace_id"]):
            with _holding(link["id"]):
                _forget(link)
            continue
        direction = due(link)
        if direction and link["remote_url"]:
            run_in_background(link["id"], direction=direction)
        elif direction:
            run_link(link["id"], direction=direction)
    from . import linked_files  # local: it imports this module

    linked_files.sweep()


def hint() -> None:
    """A window of the app got the focus: a tick now, in the background."""
    threading.Thread(target=tick, daemon=True, name="folder-links-hint").start()


# --- the hot loop -----------------------------------------------------------------------------

def mark(ws: str, page_id: str, *, at_once: bool = False) -> None:
    """The page owes its vault a round of its own (``hot_tick``), ``HOT_S``
    after this, or at the next tick with ``at_once``."""
    with _dirty_guard:
        _dirty[(ws, page_id)] = time.monotonic() - (HOT_S if at_once else 0)


def _on_commit(ws: str, client: str, page_id: str) -> None:
    """``ops.commit_listeners``' entry: a write in Gamma marks its page
    (``mark``) and does nothing else, in the writer's thread. A vault's own
    writes, the folder and label trees and the writes across a workspace
    mark nothing: a round covers them."""
    if client == DISK_CLIENT or not page_id or page_id in TREES or not config.folder_links_enabled():
        return
    mark(ws, page_id)


commit_listeners.append(_on_commit)


def _settle(key: tuple[str, str], at: float | None) -> None:
    """A mark a round covered goes, unless a write came since (``at``)."""
    if at is None:
        return
    with _dirty_guard:
        if _dirty.get(key) == at:
            del _dirty[key]


def _stats(link: dict, page_id: str) -> tuple[tuple, bool]:
    """The stats of the page's files now, and whether one shows another stat
    than its row: a save on disk while the page is open. A file gone, or one a
    link now stands on the way to (``vault_disk.path``: never followed), is
    the full round's to judge."""
    root = dest_of(link)
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT path, size, mtime_ns FROM vault_files WHERE link_id = ? AND page_id = ? AND was = ''",
                            (link["id"], page_id)).fetchall()
    now, moved = [], False
    for rel, size, mtime_ns in rows:
        try:
            st = os.stat(vault_disk.path(root, rel))
        except OSError:
            continue
        now.append((rel, st.st_size, st.st_mtime_ns))
        moved = moved or (st.st_size, st.st_mtime_ns) != (size, mtime_ns)
    return tuple(now), moved


def _row_link(page_id: str, links: dict) -> dict | None:
    """The engine vault among ``links`` (by id) whose rows hold the page."""
    with connect_vaults_db() as conn:
        found = conn.execute("SELECT link_id FROM vault_files WHERE page_id = ? AND was = '' LIMIT 1",
                             (page_id,)).fetchone()
    return links.get(found[0]) if found else None


def run_page(link_id: str, page_id: str) -> bool:
    """A round of the page's files (``vault.sync.sync_page``), unless a round
    of the vault runs now: whether a round covered the page, which one that
    found a file still being written did not, nor one of a work folder whose
    files are not chosen yet. ``check_vault`` comes first, as for a whole
    round. A refusal, or a round the marker, the disk or the account
    stopped, is the vault's failure in its status; any other leaves the
    status as the last whole round wrote it."""
    from .vault import sync as vault_sync  # local: it imports this module

    lock = _lock(link_id)
    if not lock.acquire(blocking=False):
        return False
    covered = True
    try:
        link = get_link(link_id)
        if link is not None and choosing(link):
            return False
        if link is None or not engine(link) or older(link):
            return True
        try:
            check_vault(link, earlier=True)
        except LinkError as e:
            _stopped(link, time.time(), str(e))
            return True
        result = vault_sync.sync_page(link, page_id)
        covered = not result.status["counts"].get("waiting")
        if result.status.get("last_error"):
            errors = {k: result.status[k] for k in ("stopped", "offline") if k in result.status}
            _failed({**link, "status": {**link["status"], **errors}}, result.status["last_error"])
    except Exception:  # noqa: BLE001: the hot loop goes on; the whole round will say why
        log.exception(f"[folders] the round of page {page_id} failed")
    finally:
        _let_go(link_id, lock)
    return covered


def hot_tick() -> None:
    """The hot loop's round (every ``HOT_S``): a page marked ``HOT_S`` ago or
    more, and a page open in a window whose file's stat moved since its row
    and since its last round looked (a note kept, not pushed, keeps its row's
    stat), each have a round of their own (``run_page``) when an engine vault
    that is not resting (``_resting``) keeps them. A mark goes once a round
    covered it, or when no vault keeps its page; one whose vault was busy,
    or whose file was still being written, stays for the next tick. Then
    the feedback files of the work folders a change marked ``HOT_S`` ago or
    more are written (``_feedback_pass``)."""
    if not config.folder_links_enabled():
        return
    from .feedback import files as feedback_files  # local: it reads the pages and the vaults' rows

    now = time.monotonic()
    with _dirty_guard:
        wanted = {key: at for key, at in _dirty.items() if now - at >= HOT_S}
    opened = collab.open_pages()
    for key in set(_looked) - set(opened):
        del _looked[key]
    feedback = feedback_files.due(HOT_S)
    if not wanted and not opened and not feedback:
        return
    links = {link["id"]: link for link in all_links() if engine(link) and not link["remote_url"]}
    for key in opened:
        link = _row_link(key[1], links) if key not in wanted else None
        if link is None or link["workspace_id"] != key[0] or _resting(link):
            continue
        stats, moved = _stats(link, key[1])
        if moved and _looked.get(key) != stats and run_page(link["id"], key[1]):
            _looked[key] = stats
    for (ws, page_id), at in wanted.items():
        link = vault_of_page(ws, page_id) if links else None
        if link is None or link["id"] not in links:
            _settle((ws, page_id), at)
        elif not _resting(link) and run_page(link["id"], page_id):
            _settle((ws, page_id), at)
    for ws, w in feedback:
        _feedback_pass(ws, w, links, feedback_files)


def _feedback_pass(ws: str, w: str, links: dict, files) -> None:
    """The feedback files of the work folder ``w`` of ``ws`` written
    (``files.write``) under its link's lock, among ``links``, its mark
    settled once the pass covered it. No link, or one resting for a reason
    other than a round (paused, failed, choosing its files), drops the
    mark: its next whole round writes. A lock a round holds keeps the mark
    for the next tick, as does a pass that left something for the next
    (``left``: a file a reader held, the pictures' cap)."""
    at = time.monotonic()
    link = next((link for link in links.values()
                 if link["workspace_id"] == ws and link["folder_id"] == w and work_folder_link(link)), None)
    if link is None or choosing(link) or (_resting(link) and not _lock(link["id"]).locked()):
        files.settle(ws, w, at)
        return
    lock = _lock(link["id"])
    if not lock.acquire(blocking=False):
        return
    try:
        written = files.write(link)
    finally:
        _let_go(link["id"], lock)
    if not written.get("left"):
        files.settle(ws, w, at)
