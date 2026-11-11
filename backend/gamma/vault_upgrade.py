"""A folder kept on disk before synced vaults, taken onto the vault engine
at its first round (docs/research/vaults.md "What changes for today's links",
"What happens to what exists", "In steps" 4).

Such a link is a folder link whose state names no vault marker
(``folder_links.older``), its directory holding the state file the gamma-sync
client writes (``gamma_sync.STATE``: what it wrote, its page, identity, size
and time). It becomes a synced vault that pulls, with push off or PDFs as its
arrow was, notes files or not as it had them; push may then be turned on for
everything. ``upgrade`` takes it over in four steps, each left either as it
was or done by a kill at any point, the row's state being the commit point:
until it names the marker, the state file is the link's knowledge and the
next round starts over; from then on vaults.db holds it all.

1. The marker written (``vault_disk.make_marker``), or the one an earlier try
   wrote taken, when no other link holds it: ``.gamma/`` is all a round
   writes beside the files, and the status says so (``upgraded_at``, kept in
   the row's state from the commit point on).
2. The state file's files and directories into vaults.db, in one transaction
   (``vault_store.take``), each file with the stat the client recorded or,
   when it is as recorded (``gamma_sync.modified``'s rule), the file's own:
   - a PDF: its page and identity, made by pull (``ours``); one changed on
     disk whose page's PDF Gamma replaced too is kept, as the client keeps
     it, until it changes again or a force decides;
   - a note: its page, and a base that names none of its blocks
     (``vault_sync._unknown``). One as the client wrote it keeps its hash, so
     the first pull writes it once more by the lens's encoder, the bytes it
     held going to the vault's history, and its base is known from then on;
     one changed on disk keeps none, so it is kept and reported as the
     client kept it, and a force pull or a force push decides;
   - a picture under ``attachments/``: its upload name;
   - each folder's directory that is there, by the client's naming rule
     (``obsidian_export.page_dir``); a sub-page's directory is no folder.
   A cloud placeholder is taken by its stat and never read: a note as the
   client wrote it has its hash learnt by the first pull that finds its
   bytes here (``vault_sync.UNREAD``), a PDF keeps its identity. A file that
   is not there is not taken: pull writes it again. But a pass during which
   the disk went (the marker gone by its end) is ``Offline``, nothing taken,
   as a scan's is: files the disk no longer answers for are not files gone.
   A title and a first filing are not known (none), so pull moves and
   renames nothing until Gamma changes them. A directory whose state file is
   gone has its files taken by what they say (``_found``): a note by its
   ``gamma_id``, kept as one changed on disk, a PDF by its bytes, so that
   pull writes none of them a second time beside it.
3. The row's state names the marker.
4. The state file goes, read-only or not. A kill just before, or another
   program holding it open, leaves it for the scan's sweep of Gamma's
   temporary names, which ``.gamma-sync.json`` at the vault's root is one of
   (``vault_disk``), and for the vault's removal, which takes it first: from
   the commit point on it stands for nothing.

The ``gamma_id`` line those notes carry is read where a file has it, when its
row does not say which page it is (``vault_sync._gamma_id``), and no pull
writes it again.
"""

import stat
import time
from pathlib import Path

from . import folder_links, gamma_sync, vault_disk, vault_store
from .db import page_now
from .folder_sync import ROOT
from .logbuf import log
from .obsidian_export import page_dir
from .vault_sync import BOTH_PDF, UNREAD, _gamma_id
from .work_folders import ignore_rules

UPGRADED = "upgraded_at"   # the state's key, and the status's, of when the folder was taken onto the engine


def old_state(root: Path) -> dict | None:
    """The state file the gamma-sync client keeps in ``root`` (its ``files``
    and ``dirs``), None when there is none. ``vault_disk.Offline`` when the
    directory does not answer; ``gamma_sync.SyncError`` for one of another
    version."""
    try:
        if not root.is_dir():
            raise vault_disk.Offline("the vault's directory is not there")
        if not (root / gamma_sync.STATE).is_file():
            return None
        return gamma_sync.Link.load(root).state
    except OSError as e:
        raise vault_disk.Offline(f"the vault's directory does not answer: {e.strerror or type(e).__name__}") from e
    except ValueError as e:
        raise gamma_sync.SyncError(f"{gamma_sync.STATE} cannot be read") from e


def belongs(link: dict, state: dict) -> bool:
    """Whether a state file is the link's folder: the same folder of the same
    workspace, of this server (``local``) or of the link's other server."""
    server = (state.get("server") or "local").rstrip("/")
    return (state.get("folder"), state.get("workspace")) == (link["folder_id"], link["workspace_id"]) and \
        server == (link["remote_url"] or "local")


def pages_of(link: dict) -> int:
    """How many pages a folder kept before has files of, by its state file (0
    when it cannot be read): what removing it would leave."""
    try:
        state = old_state(Path(link["path"])) or {}
    except (vault_disk.Offline, gamma_sync.SyncError):
        state = {}
    return len({e["page"] for e in (state.get("files") or {}).values() if e.get("page")})


def upgrade(link: dict, remote=None) -> dict:
    """The older folder ``link`` taken onto the engine (the module doc); its
    directories' folders read from ``remote``'s layout (none without one: a
    removal's, which needs no folder). → the link with its new state.
    ``vault_disk.Offline`` when the directory does not answer, ``Stopped`` when
    it holds another vault's marker or another folder's state file."""
    root = Path(link["path"])
    try:
        state = old_state(root)
    except gamma_sync.SyncError as e:
        raise vault_disk.Stopped(str(e)) from e
    if state is not None and not belongs(link, state):
        raise vault_disk.Stopped("the directory holds another folder's files")
    marker = vault_disk.read_marker(root)
    if marker is not None and any(o["state"].get("vault") == marker["id"] for o in folder_links.all_links()
                                  if o["id"] != link["id"]):
        raise vault_disk.Stopped("the directory's vault marker is another vault's: was it copied?")
    if marker is None:
        marker = vault_disk.make_marker(root)
    try:
        if state is not None:
            docs = {page_id: info.doc_id for page_id, info in remote.layout().pages.items()} if remote is not None else {}
            files = _files(root, link, state["files"], docs)
        else:
            files = _found(root, link, remote) if remote is not None else {}
        dirs = _dirs(root, link, remote) if remote is not None else {}
    except OSError as e:
        raise vault_disk.Offline(f"the vault's directory stopped answering: {vault_disk._why(e)}") from e
    if vault_disk.read_marker(root) is None:     # the disk went during the pass: what it missed is not gone
        raise vault_disk.Offline("the vault's directory lost its marker while its files were read")
    vault_store.take(link["id"], files, dirs)
    link = {**link, "state": {**link["state"], "vault": marker["id"], UPGRADED: page_now()}}
    folder_links.keep_state(link["id"], link["state"])
    if state is not None:
        try:
            vault_disk.remove(root / gamma_sync.STATE)
        except OSError as e:     # another program holds it: the scan's sweep takes it later
            log.info(f"[vault] {root}: {gamma_sync.STATE} stays for the sweep ({vault_disk._why(e)})")
    return link


def _files(root: Path, link: dict, entries: dict, docs: dict) -> dict:
    """The rows of the files the client wrote that are there (step 2).
    ``docs``: each page's PDF in Gamma now. A PDF changed on disk whose page's
    PDF Gamma replaced too, which the client keeps, is kept: its stat the
    file's, so that push reads it once it changes again (a PDF replaced in
    Gamma stands until then), and pull reports it (``vault_sync.BOTH_PDF``).
    A cloud placeholder is never read: a note as the client wrote it waits
    for its hash (``vault_sync.UNREAD``), a PDF keeps no ``/ID``."""
    out, now = {}, time.time()
    for path, entry in entries.items():
        kind = gamma_sync.kind(entry)
        if not gamma_sync.safe_rel(path) or kind == "notes" and not link["notes"]:
            continue
        try:
            st = vault_disk.lstat(root, path)
        except vault_disk.Outside:
            continue
        if st is None or not stat.S_ISREG(st.st_mode):
            continue
        cloud = vault_disk._placeholder(st)
        same = _as_written(st, entry)
        both = kind == "pdf" and not same and docs.get(entry.get("page")) not in (None, "", entry.get("doc"))
        if same or both:
            seen = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "file_id": "" if cloud else str(st.st_ino),
                    "read_at": 0.0 if cloud else now}
        else:
            seen = {"size": int(entry.get("size") or 0), "mtime_ns": int(float(entry.get("mtime") or 0) * 1e9),
                    "file_id": "", "read_at": 0.0}
        row = {**seen, "page_id": entry.get("page") or "", "ours": 1, "seq": -1, "title": None, "folder": None}
        if both:
            row["problem"] = BOTH_PDF
        if kind == "pdf":
            row.update(kind="pdf", hash=entry.get("doc") or "",
                       text=vault_disk.pdf_id(root / path) if same and not cloud else "")
        elif kind == "notes":
            hashed = (UNREAD if cloud else vault_disk.sha(vault_disk.read(root, path))) if same else ""
            row.update(kind="notes", text="", nodes=None, hash=hashed)
        else:
            row.update(kind="picture", page_id="", hash=path.rsplit("/", 1)[-1])
        out[path] = row
    return out


def _as_written(st, entry) -> bool:
    """Whether a file's stat is the one the client recorded of it
    (``gamma_sync.modified``'s rule: the size, and the time within a second),
    from the ``lstat`` already taken: one that fails to answer now is the
    disk's, never a file as written."""
    return st.st_size == entry.get("size") and abs(st.st_mtime - float(entry.get("mtime") or 0)) <= 1.0


def _found(root: Path, link: dict, remote) -> dict:
    """The rows of a directory whose state file is gone (deleted, not in a
    backup, swept by an older scan): the files that say which page of the
    vault they are, so that pull writes none a second time beside them. A
    note whose ``gamma_id`` names a page has a base that names none of its
    blocks and no hash, kept as one changed on disk until a force decides; a
    PDF holding a page's bytes is that page's, as pull wrote it. The walk
    reads what the directory's ``.gammaignore`` tracks, as its rounds will;
    one that cannot be read stops the upgrade (``Stopped``)."""
    try:
        rules = ignore_rules.read(root)
    except ignore_rules.Unreadable as e:
        raise vault_disk.Stopped(f"its .gammaignore cannot be read: {e.why}") from e
    pages = remote.layout().pages
    docs = {info.doc_id: page_id for page_id, info in pages.items() if info.doc_id}
    out, now = {}, time.time()
    for path, found in vault_disk.walk(root, rules).files.items():
        if found.placeholder:
            continue
        target = vault_disk.path(root, path)
        try:
            if found.kind == "notes":
                text = vault_disk.read(root, path).decode("utf-8", "replace")
                page_id, entry = _gamma_id(text), {"kind": "notes", "hash": "", "text": "", "nodes": None}
            else:
                doc = gamma_sync.doc_of(target)
                page_id, entry = docs.get(doc, ""), {"kind": "pdf", "hash": doc, "text": vault_disk.pdf_id(target)}
        except OSError:
            continue
        if page_id not in pages or any(row["page_id"] == page_id and row["kind"] == entry["kind"] for row in out.values()):
            continue
        out[path] = {**entry, "page_id": page_id, "size": found.size, "mtime_ns": found.mtime_ns,
                     "file_id": found.file_id, "read_at": now if found.kind == "pdf" else 0.0, "ours": 1, "seq": -1,
                     "title": None, "folder": None}
    return out


def _dirs(root: Path, link: dict, remote) -> dict:
    """``{path: (folder id, file id)}`` of the folders' directories that are
    there, named as the client named them: each folder's names below the
    vault's folder, the first of two folders one path names."""
    folders = remote.layout().folders
    top = "" if link["folder_id"] == ROOT else link["folder_id"]
    out = {}
    for folder_id in folders:
        names, at = [], folder_id
        while at in folders and at != top:
            parent, name = folders[at]
            names.append(name)
            at = parent
        path = page_dir(reversed(names)).rstrip("/")
        if not path or path in out:
            continue
        try:
            st = vault_disk.lstat(root, path)
        except vault_disk.Outside:
            continue
        if st is not None and stat.S_ISDIR(st.st_mode):
            out[path] = (folder_id, str(st.st_ino))
    return out
