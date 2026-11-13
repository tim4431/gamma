"""The feedback files below a vault's ``.gamma/feedback/`` (docs/dev/feedback.md
"The files"): for a work folder of this computer, the feedback is files in
the repository too, so an agent at the terminal reads them with no
connection to Gamma.

    .gamma/feedback/
      .gitignore          "*": the repository's git never shows the churn
      open.json           the work folder's open threads (entries.document), 2-space JSON
      open.md             the same as Markdown (markdown.render, pictures="files")
      img/<key>.jpg       the regions' pictures, write-once by key (pictures.cached)
      requests/<id>.json  one per Send (requests.document_of), its pictures as ../img/<key>.jpg

``write(link)`` writes them for a work-folder link, under the link's lock:
at every whole round (``folder_links._vault_round``) and, between rounds,
within a hot pass of a change (``folder_links.hot_tick``). A comment write
or an op commit marks its page (``mark``; gamma/feedback/listeners.py), a
request's write its work folder (``mark_folder``); ``due`` resolves the
marked pages to their work folders in one query a workspace and names the
work folders marked ``HOT_S`` ago or more and not since, so a burst of
writes is one pass; ``settle`` drops a mark a pass covered unless a newer
came. A pass reads the document (the vault's rows naming the files, the
maker its owner), stores what the kept version left unstored
(gamma/feedback/kept.py ``repair``), draws the pictures the entries name
that ``img/`` lacks, at most ``PICTURES_PER_WRITE`` of them (a picture not
there yet is null in that pass's document; the next pass fills it), writes
each file only when its bytes differ (``vault_disk.write_own``; a replace a
reader holds on Windows is left for the next pass and is no error of the
vault's), rewrites the request files an answer still changes or whose
pictures were not drawn when they were written, prunes
``img/`` of what no file names and sweeps Gamma's own stale temporary
files, which the vault's scan never meets (it never enters ``.gamma/``). A
paused or choosing vault writes nothing (``hot_tick`` drops its mark; its
next whole round writes). ``remove`` takes the directory away when the
vault goes or stops being a work folder: the files are Gamma's own and
regenerable, deleted directly, never through the trash. Errors are caught
into the summary (``{"open": n, "error": why}``) and logged once per change
of text: a round never fails for its feedback files.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

from .. import vault_disk, vault_store
from ..db import account_names, connect_pages_db, connect_users_db
from ..logbuf import log
from . import entries, kept, markdown, pictures, requests
from .entries import FeedbackError

DIR = f"{vault_disk.MARKER_DIR}/feedback"
IMG = f"{DIR}/img"
REQUESTS = f"{DIR}/requests"
GITIGNORE = b"*\n"
PICTURES_PER_WRITE = 20     # pictures drawn in one pass; the rest wait for the next
REQUEST_FILES = 100         # request files kept; an older one goes once its request is done, failed or gone
LIVE = ("sent", "running")  # the states of a request an answer still changes: its file is read again each pass
_PICTURE_RE = re.compile(r"^([0-9a-f]{16})\.jpg$")
_REQUEST_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})\.json$")

_pages: dict[tuple[str, str], float] = {}    # (workspace, page id) -> when it was marked (monotonic)
_folders: dict[tuple[str, str], float] = {}  # (workspace, W) -> when it was last marked
_guard = threading.Lock()
_said: dict[str, str] = {}                   # link id -> the error last logged for it


# --- the marks ----------------------------------------------------------------------

def mark(ws: str, page_id: str) -> None:
    """A page of some work folder changed (a comment write, an op commit):
    its work folder's files are due. Cheap, no IO: ``due`` resolves the
    page, and a page of no work folder marks nothing."""
    with _guard:
        _pages[(ws, page_id)] = time.monotonic()


def mark_folder(ws: str, w: str) -> None:
    """A request of the work folder ``w`` changed: its files are due."""
    with _guard:
        _folders[(ws, w)] = time.monotonic()


def due(min_age_s: float) -> list[tuple[str, str]]:
    """The work folders ``(ws, W)`` whose files are due: those marked
    ``min_age_s`` ago or more, and not since. The pages marked since the
    last call are resolved to their work folders first, one query a
    workspace (the pages' ``work_folder`` column); a workspace that cannot
    be read drops its marks. A page's mark goes only once resolved, and
    stays when the page was marked again meanwhile, so a caller running
    beside this one resolves the same marks rather than finding them gone."""
    with _guard:
        pages = dict(_pages)
    by_ws: dict = {}
    for (ws, page_id), at in pages.items():
        by_ws.setdefault(ws, {})[page_id] = at
    for ws, marked in by_ws.items():
        try:
            with connect_pages_db(ws) as conn:
                rows = conn.execute(
                    "SELECT id, work_folder FROM unified_blocks WHERE work_folder IS NOT NULL "
                    "AND id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(marked)),)).fetchall()
        except Exception as e:  # noqa: BLE001 — a workspace gone or unreadable: nothing of it is due
            log.warning(f"[feedback] resolving the marked pages of workspace {ws}: {e}")
            continue
        with _guard:
            for page_id, w in rows:
                _folders[(ws, w)] = max(_folders.get((ws, w), 0.0), marked[page_id])
    now = time.monotonic()
    with _guard:
        for key, at in pages.items():
            if _pages.get(key) == at:
                del _pages[key]
        return [key for key, at in _folders.items() if now - at >= min_age_s]


def settle(ws: str, w: str, at: float) -> None:
    """The mark of the work folder ``w`` a pass that began at ``at``
    (monotonic) covered goes, unless a newer one came since."""
    with _guard:
        if _folders.get((ws, w), float("inf")) <= at:
            del _folders[(ws, w)]


# --- the files ----------------------------------------------------------------------

def write(link: dict) -> dict:
    """The files of the work-folder link ``link`` written as they stand
    (the module doc), under the link's lock, which the caller holds:
    ``{"open": n}``, the open threads, with ``"left": m`` when ``m``
    pictures or files wait for the next pass (the pictures' cap, a replace a
    reader held), or ``{"open": n, "error": why}`` when the directory is
    not the vault's or does not answer, the folder is no work folder's, or a
    write failed, logged once per change of text. Never raises."""
    ws, w = link["workspace_id"], link["folder_id"]
    root = Path(link["path"])
    count = 0
    try:
        vault_disk.check_marker(root, link["state"]["vault"])
        paths = entries.paths_of(vault_store.files(link["id"]))
        with connect_users_db() as users:
            owner = account_names(users, {link["created_by"]}).get(link["created_by"], "")
        regions: dict = {}
        with connect_pages_db(ws) as conn:
            scope = entries.resolve_scope(conn, w)
            doc = entries.document(ws, conn, scope, state="open", paths=paths, owner=owner, regions=regions)
            asked, states = _requests(ws, conn, root, w, paths, regions)
        count = doc["count"]
        kept.repair(ws, [e["page_id"] for e in doc["entries"]])
        with connect_pages_db(ws) as conn:
            have, left = _pictures(ws, conn, root, regions)
        _without_missing(doc["entries"], have)
        for record in asked.values():
            _without_missing(record["entries"], have)
        before = _named(_parsed(_read(root, f"{DIR}/open.json")))
        left += _write_files(root, doc, asked)
        _prune_requests(root, states)
        named = _named(doc) | before | _named_by_requests(root)
        _prune_pictures(root, named)
        for rel in (DIR, IMG, REQUESTS):
            vault_disk.sweep_own(root, rel)
    except (FeedbackError, OSError, vault_disk.Stopped, vault_disk.Offline) as e:   # Outside is an OSError
        return _error(link, count, str(e))
    except Exception as e:  # noqa: BLE001 — a renderer's or a database's failure never fails the round
        log.exception(f"[feedback] the files of {link['path']} could not be written")
        return _error(link, count, f"unexpected error: {e!r}")
    _said.pop(link["id"], None)
    return {"open": count, "left": left} if left else {"open": count}


def write_request(link: dict, request_id: str) -> None:
    """The request's file written now (``requests.document_of``; the
    pictures ``img/`` holds already, the rest null until a pass draws
    them), best effort: nothing while a round or a pass holds the vault's
    lock (the work folder is marked, so the hot pass writes it), and an
    error is only logged. Never raises."""
    from .. import folder_links  # local: it imports ops, which imports comments, which imports this package

    with folder_links.try_holding(link["id"]) as held:
        if not held:
            return
        try:
            root = Path(link["path"])
            vault_disk.check_marker(root, link["state"]["vault"])
            ws = link["workspace_id"]
            paths = entries.paths_of(vault_store.files(link["id"]))
            with connect_pages_db(ws) as conn:
                record = requests.document_of(ws, conn, request_id, paths=paths)
            if record is None:
                return
            _without_missing(record["entries"], _keys_in(root))
            rel = f"{REQUESTS}/{request_id}.json"
            data = _json(_in_requests(record))
            if _read(root, rel) != data:
                vault_disk.write_own(root, rel, data)
        except Exception as e:  # noqa: BLE001 — the next pass writes what this could not
            log.warning(f"[feedback] the request file {request_id} of {link['path']}: {e}")


def remove(root: Path) -> None:
    """``.gamma/feedback/`` of the vault at ``root`` deleted, nothing else
    (through ``vault_disk.path``, and a link or a junction met inside it
    goes as the entry it is, never entered: ``_remove_tree``), when the
    vault goes or stops being a work folder. The files are Gamma's own and
    regenerable: deleted directly, never through the trash. A file another
    program holds is logged and stays. Never raises."""
    try:
        top = vault_disk.path(root, DIR)
        if not top.is_dir():
            return
        _remove_tree(top)
    except OSError as e:
        log.warning(f"[feedback] removing the feedback files of {root}: {vault_disk._why(e)}")


def _remove_tree(directory: Path) -> None:
    """The directory and everything in it deleted. A symlink or a junction
    is unlinked where it stands (``os.walk`` would walk into a junction,
    which is no symlink to it): what it points at is someone else's."""
    with os.scandir(directory) as found:
        entries = list(found)
    for entry in entries:
        here = Path(entry.path)
        if entry.is_symlink() or vault_disk._junction(entry):
            os.unlink(here)
        elif entry.is_dir(follow_symlinks=False):
            _remove_tree(here)
        else:
            vault_disk.remove(here)
    os.rmdir(directory)


# --- what a pass does ------------------------------------------------------------------

def _requests(ws: str, conn, root: Path, w: str, paths: dict, regions: dict) -> tuple[dict, dict]:
    """The request files to write this pass, ``{id: its document}``, and
    ``{id: state}`` of every request of the work folder ``w`` (the
    retention, ``_prune_requests``). Of the newest ``REQUEST_FILES``
    requests, one ``LIVE`` is read again every pass (an answer changes its
    entries), another only while its file does not stand (``_stands``):
    missing, behind its ``rev`` (a write at the change of state that could
    not land), or with a place on this build whose picture was not drawn
    when it was written."""
    rows = conn.execute("SELECT id, rev, state FROM feedback_requests WHERE work_folder = ? ORDER BY rev DESC",
                        (w,)).fetchall()
    asked = {}
    for request_id, rev, state in rows[:REQUEST_FILES]:
        if state not in LIVE and _stands(_parsed(_read(root, f"{REQUESTS}/{request_id}.json")), rev):
            continue
        record = requests.document_of(ws, conn, request_id, paths=paths, regions=regions)
        if record is not None:
            asked[request_id] = _in_requests(record)
    return asked, {request_id: state for request_id, _rev, state in rows}


def _stands(current: dict | None, rev: int) -> bool:
    """Whether a done or failed request's file is as a pass leaves it: it
    carries the row's ``rev`` and no entry placed on this build waits for
    its picture (a Send answered before a pass drew them, or past the cap,
    has null pictures until one does)."""
    if current is None or current.get("rev") != rev or not isinstance(current.get("entries"), list):
        return False
    return not any(isinstance(e, dict) and e.get("picture") is None and e.get("kind") in pictures.KINDS
                   and isinstance(e.get("where"), dict) and e["where"].get("placed") in entries.ON_THIS_BUILD
                   for e in current["entries"])


def _pictures(ws: str, conn, root: Path, regions: dict) -> tuple[set, int]:
    """The pictures ``regions`` names that ``img/`` lacks drawn
    (``pictures.cached``) and written, at most ``PICTURES_PER_WRITE``: the
    keys ``img/`` holds now, and how many wait for the next pass (past the
    cap, or a file a reader held). One that cannot be drawn now (a build
    being written, a broken file) is left for the next round, taking no
    slot of the cap and holding no mark."""
    have = _keys_in(root)
    wanted = [key for key in regions if key not in have]
    left = drawn = 0
    for n, key in enumerate(wanted):
        if drawn >= PICTURES_PER_WRITE:
            left += len(wanted) - n
            break
        page_id, region = regions[key]
        data = pictures.cached(ws, conn, page_id, region)
        if data is None:
            continue
        drawn += 1
        if vault_disk.write_own(root, f"{IMG}/{key}.jpg", data):
            have.add(key)
        else:
            left += 1
    return have, left


def _write_files(root: Path, doc: dict, asked: dict) -> int:
    """``.gitignore``, ``open.json``, ``open.md`` and the request files
    ``asked``, each written only when its bytes differ from the file's: how
    many a reader held to the last try."""
    wanted = [(f"{DIR}/.gitignore", GITIGNORE), (f"{DIR}/open.json", _json(doc)),
              (f"{DIR}/open.md", markdown.render(doc, pictures="files").encode("utf-8"))]
    wanted += [(f"{REQUESTS}/{request_id}.json", _json(record)) for request_id, record in asked.items()]
    left = 0
    for rel, data in wanted:
        if _read(root, rel) != data and not vault_disk.write_own(root, rel, data):
            left += 1
    return left


def _prune_requests(root: Path, states: dict) -> None:
    """The request files past the newest ``REQUEST_FILES`` requests go
    once their request is done, failed or gone; one still ``LIVE`` stays."""
    newest = set(list(states)[:REQUEST_FILES])
    for entry in _listed(root, REQUESTS):
        found = _REQUEST_RE.match(entry.name)
        if not found or found[1] in newest or states.get(found[1]) in LIVE:
            continue
        try:
            vault_disk.remove(Path(entry.path))
        except OSError:
            continue


def _prune_pictures(root: Path, named: set) -> None:
    """Gamma's pictures in ``img/`` that none of ``named`` is go; one a
    reader holds waits for the next pass."""
    for entry in _listed(root, IMG):
        found = _PICTURE_RE.match(entry.name)
        if found and found[1] not in named:
            try:
                vault_disk.remove(Path(entry.path))
            except OSError:
                continue


def _error(link: dict, count: int, why: str) -> dict:
    if _said.get(link["id"]) != why:
        _said[link["id"]] = why
        log.warning(f"[feedback] the files of {link['path']}: {why}")
    return {"open": count, "error": why}


# --- the files' shapes ----------------------------------------------------------------------

def _json(doc: dict) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _in_requests(record: dict) -> dict:
    """A request's document as its file below ``requests/`` holds it: the
    pictures named from there (``../img/<key>.jpg``)."""
    out = []
    for entry in record["entries"]:
        entry = dict(entry)
        for key in ("picture", "before"):
            if entry.get(key):
                entry[key] = f"../{entry[key]}"
        out.append(entry)
    return {**record, "entries": out}


def _without_missing(items: list, have: set) -> None:
    """The pictures ``img/`` does not hold yet are null in the entries."""
    for entry in items:
        for key in ("picture", "before"):
            if entry.get(key) and entries.key_of(entry[key]) not in have:
                entry[key] = None


def _named(doc) -> set:
    """The keys of the pictures a document's entries name ("" for none)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        return set()
    out = set()
    for entry in doc["entries"]:
        if isinstance(entry, dict):
            for key in ("picture", "before"):
                if isinstance(entry.get(key), str):
                    out.add(entries.key_of(entry[key]))
    return out


def _named_by_requests(root: Path) -> set:
    """The keys of the pictures every file in ``requests/`` names."""
    out: set = set()
    for entry in _listed(root, REQUESTS):
        if _REQUEST_RE.match(entry.name):
            try:
                out |= _named(_parsed(Path(entry.path).read_bytes()))
            except OSError:
                continue
    return out


def _keys_in(root: Path) -> set:
    """The keys of the pictures ``img/`` holds."""
    return {found[1] for entry in _listed(root, IMG) if (found := _PICTURE_RE.match(entry.name))}


def _listed(root: Path, rel_dir: str) -> list:
    """The files of the vault's directory ``rel_dir`` (``vault_disk.path``:
    never below a link), none for a directory that is not there."""
    try:
        with os.scandir(vault_disk.path(root, rel_dir)) as found:
            return [entry for entry in found if entry.is_file(follow_symlinks=False)]
    except FileNotFoundError:
        return []


def _read(root: Path, rel: str) -> bytes | None:
    """The bytes of the vault's file ``rel`` (``vault_disk.path``), None for none."""
    try:
        return vault_disk.read(root, rel)
    except FileNotFoundError:
        return None


def _parsed(data: bytes | None) -> dict | None:
    """A JSON file's object, None for no file or one that is not JSON."""
    if data is None:
        return None
    try:
        found = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return found if isinstance(found, dict) else None
