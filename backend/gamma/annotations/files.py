"""The annotation files a vault writes on its disk (docs/dev/annotations.md
"The files"): the highlights and comment threads of a work folder's files,
or of the page a vault of one file follows, as files beside them, so an
agent at the terminal reads them with no connection to Gamma.

    <work folder>/.gamma/annotations/
      .gitignore          "*": the repository's git never shows the churn
      open.json           the open threads and the bare highlights (entries.document), 2-space JSON
      open.md             the same as Markdown (markdown.render, pictures="files")
      img/<key>.jpg       the regions' pictures, write-once by key (pictures.cached)
      requests/<id>.json  one per Send (requests.document_of), its pictures as ../img/<key>.jpg

    <the file's directory>/.gamma/annotations/<file name>/
      .gitignore, open.json, open.md, img/   the same, for the one page the vault of one file follows

``place`` says where a vault's are: below a work folder's ``.gamma/``, or
for a vault of one file in a directory of their own beside the file, named
by the file's name, so that two files followed in one directory never meet;
a directory there holds no marker and is never taken for a vault. ``write(link)``
writes them under the link's lock, which the caller holds: at every whole
round of a work folder (``folder_links._vault_round``) or a vault of one
file (``_file_round``) and, between rounds, within a hot pass of a change
(``folder_links.hot_tick``). A comment write or an op commit marks its page
(``mark``; gamma/annotations/listeners.py), a request's write its work
folder (``mark_folder``); ``due`` resolves the marked pages to what writes
them in one query a workspace (the pages' ``work_folder`` column, and the
vaults of one file of this server that follow them) and names the scopes
(a work folder, a followed page) marked ``HOT_S`` ago or more and not
since, so a burst of writes is one pass; ``settle`` drops a mark a pass
covered unless a newer came.

A pass of a vault of this server reads the document (the vault's rows
naming a work folder's files, the file's own name for a vault of one file,
the maker its owner), stores what the kept version left unstored
(gamma/annotations/kept.py ``repair``) and draws the pictures the entries
name that ``img/`` lacks. One of a vault of another server reads the same
document from that server with the vault's token (``GET /api/annotations``
of its folder or page, a member's read there) and fetches those pictures by
the routes it names; that server's own paths give way to the vault's rows.
Either way at most ``PICTURES_PER_WRITE`` pictures a pass (a picture not
there yet is null in that pass's document; the next pass fills it), each
file written only when its bytes differ (``vault_disk.write_own``; a replace
a reader holds on Windows is left for the next pass and is no error of the
vault's). A work folder of this server also rewrites the request files an
answer still changes or whose pictures were not drawn when they were
written. Then ``img/`` is pruned of what no file names and Gamma's own
stale temporary files are swept, which the vault's scan never meets (it
never enters a dot directory), and a work folder's ``.gamma/feedback/``,
where its files were before (``FORMER``), goes. A paused or choosing vault
writes nothing between rounds (``hot_tick`` drops its mark; its next whole
round writes). ``remove`` takes the files away when the vault goes, stops
being a work folder or follows its file elsewhere: they are Gamma's own and
regenerable, deleted directly, never through the trash. Errors are caught
into the summary (``{"open": n, "error": why}``) and logged once per change
of text: a round never fails for its annotation files.
"""

import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path

from .. import gamma_sync
from ..vault import disk as vault_disk, store as vault_store
from ..db import account_names, connect_pages_db, connect_users_db
from ..logbuf import log
from . import entries, kept, markdown, pictures, requests
from .entries import AnnotationError

DIR = f"{vault_disk.MARKER_DIR}/annotations"
IMG = f"{DIR}/img"
REQUESTS = f"{DIR}/requests"
FORMER = f"{vault_disk.MARKER_DIR}/feedback"   # a work folder's files before step 3.2: removed at its rounds
GITIGNORE = b"*\n"
JPEG = b"\xff\xd8\xff"
PICTURES_PER_WRITE = 20     # pictures drawn in one pass; the rest wait for the next
MAX_PICTURE = 16 << 20      # bytes of one picture fetched from another server
REQUEST_FILES = 100         # request files kept; an older one goes once its request is done, failed or gone
LIVE = ("sent", "running")  # the states of a request an answer still changes: its file is read again each pass
DOCUMENT = ("format", "workspace", "work_folder", "scope", "owner", "state", "updated_at", "count", "truncated",
            "truncated_threads", "answer", "entries")   # a document's fields: what another server's answer is cut to
_PICTURE_RE = re.compile(r"^([0-9a-f]{16})\.jpg$")
_KEY_RE = re.compile(r"[0-9a-f]{16}")
_REQUEST_RE = re.compile(r"^([A-Za-z0-9_-]{1,64})\.json$")

_pages: dict[tuple[str, str], float] = {}    # (workspace, page id) -> when it was marked (monotonic)
_scopes: dict[tuple[str, str], float] = {}   # (workspace, W or a followed page) -> when it was last marked
_guard = threading.Lock()
_said: dict[str, str] = {}                   # link id -> the error last logged for it


# --- the marks ----------------------------------------------------------------------

def mark(ws: str, page_id: str) -> None:
    """A page changed (a comment write, an op commit): the files of its
    work folder, or of the vault of one file that follows it, are due.
    Cheap, no IO: ``due`` resolves the page, and a page of neither marks
    nothing."""
    with _guard:
        _pages[(ws, page_id)] = time.monotonic()


def mark_folder(ws: str, w: str) -> None:
    """A request of the work folder ``w`` changed: its files are due."""
    with _guard:
        _scopes[(ws, w)] = time.monotonic()


def due(min_age_s: float) -> list[tuple[str, str]]:
    """The scopes ``(ws, W or page id)`` whose files are due: those marked
    ``min_age_s`` ago or more, and not since. The pages marked since the
    last call are resolved first, one query a workspace: to their work
    folder (the pages' ``work_folder`` column), and to themselves when a
    vault of one file of this server follows them; a workspace that cannot
    be read drops its marks. A page's mark goes only once resolved, and
    stays when the page was marked again meanwhile, so a caller running
    beside this one resolves the same marks rather than finding them gone."""
    with _guard:
        pages = dict(_pages)
    by_ws: dict = {}
    for (ws, page_id), at in pages.items():
        by_ws.setdefault(ws, {})[page_id] = at
    for ws, marked in by_ws.items():
        ids = json.dumps(sorted(marked))
        try:
            with connect_pages_db(ws) as conn:
                rows = conn.execute("SELECT id, work_folder FROM unified_blocks WHERE work_folder IS NOT NULL "
                                    "AND id IN (SELECT value FROM json_each(?))", (ids,)).fetchall()
            with connect_users_db() as conn:
                rows += [(page_id, page_id) for (page_id,) in conn.execute(
                    "SELECT page_id FROM folder_links WHERE kind = 'file' AND remote_url = '' AND workspace_id = ? "
                    "AND page_id IN (SELECT value FROM json_each(?))", (ws, ids))]
        except Exception as e:  # noqa: BLE001 — a workspace gone or unreadable: nothing of it is due
            log.warning(f"[annotations] resolving the marked pages of workspace {ws}: {e}")
            continue
        with _guard:
            for page_id, scope in rows:
                _scopes[(ws, scope)] = max(_scopes.get((ws, scope), 0.0), marked[page_id])
    now = time.monotonic()
    with _guard:
        for key, at in pages.items():
            if _pages.get(key) == at:
                del _pages[key]
        return [key for key, at in _scopes.items() if now - at >= min_age_s]


def settle(ws: str, scope: str, at: float) -> None:
    """The mark of the scope (a work folder, a followed page) a pass that
    began at ``at`` (monotonic) covered goes, unless a newer one came
    since."""
    with _guard:
        if _scopes.get((ws, scope), float("inf")) <= at:
            del _scopes[(ws, scope)]


# --- the files ----------------------------------------------------------------------

def place(link: dict) -> tuple[Path, str]:
    """Where the vault's files are: ``(root, rel)``, the directory every
    path is taken in (``vault_disk.path``) and theirs in it, ``/`` between
    names. A folder vault's are below its ``.gamma/`` (``DIR``); a vault of
    one file's below the ``.gamma/`` of the file's directory, under the
    file's own name."""
    if link["kind"] == "file":
        file = Path(link["path"])
        return file.parent, f"{DIR}/{file.name}"
    return Path(link["path"]), DIR


def write(link: dict) -> dict:
    """The files of the vault ``link`` written as they stand (the module
    doc), under the link's lock, which the caller holds: ``{"open": n}``,
    the open threads, with ``"left": m`` when ``m`` pictures or files wait
    for the next pass (the pictures' cap, a replace a reader held), or
    ``{"open": n, "error": why}`` when the directory is not the vault's or
    does not answer, a followed file is not there, the folder is no work
    folder's, the page is gone, the vault's maker has no role in its
    workspace any more, another server does not answer or answers no
    document, or a write failed, logged once per change of text. Never
    raises."""
    root, here = place(link)
    beside = Path(link["path"]).name if link["kind"] == "file" else ""
    count = 0
    try:
        _check(link, root)
        if link["remote_url"]:
            doc, server, urls = _far(link)
            asked, states = {}, None
            count = _threads(doc)
            have, left = _pictures(root, here, beside, urls, lambda key: _fetched(server, urls[key]))
        else:
            ws, regions = link["workspace_id"], {}
            doc, asked, states = _document(link, regions)
            count = _threads(doc)
            kept.repair(ws, [e["page_id"] for e in doc["entries"]])
            with connect_pages_db(ws) as conn:
                have, left = _pictures(root, here, beside, regions,
                                       lambda key: pictures.cached(ws, conn, *regions[key]))
        _without_missing(doc["entries"], have)
        for record in asked.values():
            _without_missing(record["entries"], have)
        before = _named(_parsed(_read(root, f"{here}/open.json")))
        left += _write_files(root, here, beside, doc, asked)
        named = _named(doc) | before
        if states is not None:
            _prune_requests(root, states)
            named |= _named_by_requests(root)
        if not _run_pictures(link):
            _prune_pictures(root, f"{here}/img", named)
        for rel in (here, f"{here}/img", *((REQUESTS,) if states is not None else ())):
            vault_disk.sweep_own(root, rel)
        if link["kind"] == "folder":
            try:
                _remove_tree_at(root, FORMER)
            except OSError:
                pass   # a file of it another program holds: the next round takes the rest
    except (AnnotationError, OSError, vault_disk.Stopped, vault_disk.Offline, gamma_sync.SyncError) as e:
        return _error(link, count, str(getattr(e, "detail", "") or e))   # Outside is an OSError
    except Exception as e:  # noqa: BLE001 — a renderer's or a database's failure never fails the round
        log.exception(f"[annotations] the files of {link['path']} could not be written")
        return _error(link, count, f"unexpected error: {e!r}")
    _said.pop(link["id"], None)
    return {"open": count, "left": left} if left else {"open": count}


def write_request(link: dict, request_id: str) -> None:
    """The request's file written now (``requests.document_of``; the
    pictures ``img/`` holds already, the rest null until a pass draws
    them), best effort: nothing while a round or a pass holds the vault's
    lock (the work folder is marked, so the hot pass writes it), and an
    error is only logged; nothing for a vault of another server, whose
    Sends are that server's. Never raises."""
    from .. import folder_links  # local: it imports ops, which imports comments, which imports this package

    if link["remote_url"]:
        return
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
            _without_missing(record["entries"], _keys_in(root, IMG))
            rel = f"{REQUESTS}/{request_id}.json"
            data = _json(_in_requests(record))
            if _read(root, rel) != data:
                vault_disk.write_own(root, rel, data)
        except Exception as e:  # noqa: BLE001 — the next pass writes what this could not
            log.warning(f"[annotations] the request file {request_id} of {link['path']}: {e}")


def remove(link: dict) -> None:
    """The vault's files deleted (``place``), nothing else (through
    ``vault_disk.path``, and a link or a junction met inside them goes as
    the entry it is, never entered: ``_remove_tree``), when the vault goes,
    stops being a work folder or follows its file elsewhere: a work folder's
    ``.gamma/annotations/`` and its ``FORMER`` directory; a vault of one
    file's directory beside the file, then ``.gamma/annotations/`` and
    ``.gamma/`` there when that leaves them empty. The files are Gamma's own
    and regenerable: deleted directly, never through the trash. A file
    another program holds is logged and stays. Never raises."""
    root, here = place(link)
    try:
        for rel in (here, FORMER) if link["kind"] == "folder" else (here,):
            _remove_tree_at(root, rel)
        if link["kind"] == "file":
            for rel in (DIR, vault_disk.MARKER_DIR):
                try:
                    vault_disk.path(root, rel).rmdir()
                except OSError:
                    break      # not there, not empty: what else is there is someone's
    except OSError as e:
        log.warning(f"[annotations] removing the annotation files of {link['path']}: {vault_disk._why(e)}")


def _remove_tree_at(root: Path, rel: str) -> None:
    """The directory ``rel`` of ``root`` deleted when it is there
    (``vault_disk.path``: never through a link)."""
    top = vault_disk.path(root, rel)
    if top.is_dir():
        _remove_tree(top)


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

def _check(link: dict, root: Path) -> None:
    """The place answers as the vault's: a folder vault's marker is its own
    (``vault_disk.check_marker``); a vault of one file's file is there and,
    on this server, its maker still has a role in its workspace (it learns
    nothing new of one it left). Stopped, Offline or AnnotationError."""
    if link["kind"] == "folder":
        vault_disk.check_marker(root, link["state"]["vault"])
        return
    if not Path(link["path"]).is_file():
        raise vault_disk.Offline(gamma_sync.NOT_THERE)
    if not link["remote_url"]:
        from .. import workspaces  # local: the workspaces' module reaches the stores
        from ..vault.local_remote import NO_ACCESS  # local: it imports the vaults' module, which imports this one

        if not workspaces.role_of(link["workspace_id"], link["created_by"]):
            raise AnnotationError(403, NO_ACCESS)


def _document(link: dict, regions: dict) -> tuple[dict, dict, dict | None]:
    """The document of a vault of this server, ``regions`` gaining its
    pictures' (``entries.document``): a work folder's open threads and bare
    highlights named by the vault's rows, with the request files to write
    and every request's state (``_requests``); or the followed page's,
    named by the file's name, with neither (``{}``, None)."""
    ws = link["workspace_id"]
    with connect_users_db() as users:
        owner = account_names(users, {link["created_by"]}).get(link["created_by"], "")
    if link["kind"] == "file":
        paths = {link["page_id"]: Path(link["path"]).name}
        with connect_pages_db(ws) as conn:
            scope = entries.resolve_scope(conn, link["page_id"])
            return entries.document(ws, conn, scope, state="open", paths=paths, owner=owner, regions=regions), {}, None
    paths = entries.paths_of(vault_store.files(link["id"]))
    root = Path(link["path"])
    with connect_pages_db(ws) as conn:
        scope = entries.work_scope(conn, link["folder_id"])
        doc = entries.document(ws, conn, scope, state="open", paths=paths, owner=owner, regions=regions)
        asked, states = _requests(ws, conn, root, link["folder_id"], paths, regions)
    return doc, asked, states


def _far(link: dict) -> tuple[dict, gamma_sync.Server, dict]:
    """The document of a vault of another server, read there with its
    token (``GET /api/annotations`` of its folder or its page, open): cut
    to a document's fields (``DOCUMENT``; that server adds the Feedback
    dialog's to a work folder's), each entry's pictures named by their key
    alone (one that is no key is none), its file the vault's path where its
    rows name the page, the entries in their order again. → ``(the document,
    the server its pictures are fetched from, {key: the route of its
    picture there})``, the document asked for within ``ASK_TIMEOUT_S``. AnnotationError
    for an answer that is no such document: another release's."""
    from .. import folder_links  # local: it imports ops, which imports comments, which imports this package

    scope = link["page_id"] if link["kind"] == "file" else link["folder_id"]
    asked = folder_links.remote_server(link, folder_links.ASK_TIMEOUT_S)  # a question between rounds: it waits no longer
    got = asked.get_json("/api/annotations?" + urllib.parse.urlencode({"scope": scope, "state": "open"}))
    if got.get("format") != entries.FORMAT or not isinstance(got.get("entries"), list):
        raise AnnotationError(502, "the other server's annotations are another release's: update both to one")
    if link["kind"] == "file":
        paths = {link["page_id"]: Path(link["path"]).name}
    else:
        paths = entries.paths_of(vault_store.files(link["id"]))
    urls: dict = {}
    out = []
    for entry in got["entries"]:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        for field, route in (("picture", "picture_url"), ("before", "before_url")):
            key, url = entries.key_of(entry.get(field) if isinstance(entry.get(field), str) else None), entry.pop(route, None)
            if _KEY_RE.fullmatch(key) and isinstance(url, str) and url.startswith("/api/annotations/"):
                entry[field], urls[key] = f"img/{key}.jpg", url
            else:
                entry[field] = None
        if entry.get("page_id") in paths:
            entry["file"] = paths[entry["page_id"]]
        out.append(entry)
    out.sort(key=lambda e: (str(e.get("file") or "").casefold(), str(e.get("file") or "")))
    doc = {k: got[k] for k in DOCUMENT if k in got}
    doc["entries"] = out
    return doc, folder_links.remote_server(link), urls


def _fetched(server: gamma_sync.Server, url: str) -> bytes | None:
    """A picture of another server's (``GET /api/annotations/{id}/picture``):
    its JPEG, None when that server has none to give now (or cannot be
    reached: the files are written all the same)."""
    try:
        status, data = server.ask("GET", url)
    except gamma_sync.SyncError:
        return None
    return data if status == 200 and data.startswith(JPEG) and len(data) <= MAX_PICTURE else None


def _threads(doc: dict) -> int:
    return sum(1 for e in doc["entries"] if e.get("type") == "thread")


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
    carries the row's ``rev`` and no entry that gets a picture waits for it
    (a Send answered before a pass drew them, or past the cap, has null
    pictures until one does): one placed on this build, a region of a
    picture (drawn whatever version it was made on) or a thread on a
    picture's page (``entries._pictures``)."""
    if current is None or current.get("rev") != rev or not isinstance(current.get("entries"), list):
        return False

    def drawn(e: dict) -> bool:
        if e.get("kind") == entries.REGION or (e.get("kind") == "file" and e.get("format") == "image"):
            return True
        return (e.get("kind") in pictures.KINDS and isinstance(e.get("where"), dict)
                and e["where"].get("placed") in entries.ON_THIS_BUILD)

    return not any(isinstance(e, dict) and e.get("picture") is None and drawn(e) for e in current["entries"])


def _pictures(root: Path, here: str, beside: str, wanted, draw) -> tuple[set, int]:
    """The pictures of ``wanted`` (their keys) that ``img/`` lacks drawn
    (``draw(key)``: its JPEG, None for one that cannot be had now) and
    written (``beside``: ``vault_disk.write_own``'s, the followed file of a
    vault of one file), at most ``PICTURES_PER_WRITE``: the keys ``img/`` holds now,
    and how many wait for the next pass (past the cap, or a file a reader
    held). One that cannot be had now (a build being written, a broken
    file, another server with none to give) is left for the next round,
    taking no slot of the cap and holding no mark."""
    img = f"{here}/img"
    have = _keys_in(root, img)
    missing = [key for key in wanted if key not in have]
    left = drawn = 0
    for n, key in enumerate(missing):
        if drawn >= PICTURES_PER_WRITE:
            left += len(missing) - n
            break
        data = draw(key)
        if data is None:
            continue
        drawn += 1
        if vault_disk.write_own(root, f"{img}/{key}.jpg", data, beside=beside):
            have.add(key)
        else:
            left += 1
    return have, left


def _write_files(root: Path, here: str, beside: str, doc: dict, asked: dict) -> int:
    """``.gitignore``, ``open.json``, ``open.md`` and the request files
    ``asked``, each written only when its bytes differ from the file's: how
    many a reader held to the last try."""
    wanted = [(f"{here}/.gitignore", GITIGNORE), (f"{here}/open.json", _json(doc)),
              (f"{here}/open.md", markdown.render(doc, pictures="files").encode("utf-8"))]
    wanted += [(f"{REQUESTS}/{request_id}.json", _json(record)) for request_id, record in asked.items()]
    left = 0
    for rel, data in wanted:
        if _read(root, rel) != data and not vault_disk.write_own(root, rel, data, beside=beside):
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


def _run_pictures(link: dict) -> bool:
    """Whether ``img/`` holds pictures a run reads now that no document
    here names: those a run of another server's Send or chat fetched into a
    work folder of that server (gamma/local_agents/home.py ``pictures``),
    kept while an agent works in the vault; the round after it prunes."""
    if not link["remote_url"] or link["kind"] != "folder":
        return False
    from ..local_agents import runner  # local: the runner reaches the vaults, which import this module

    return bool(runner.at_work([link["id"]]))


def _prune_pictures(root: Path, img: str, named: set) -> None:
    """Gamma's pictures in ``img`` that none of ``named`` is go; one a
    reader holds waits for the next pass."""
    for entry in _listed(root, img):
        found = _PICTURE_RE.match(entry.name)
        if found and found[1] not in named:
            try:
                vault_disk.remove(Path(entry.path))
            except OSError:
                continue


def _error(link: dict, count: int, why: str) -> dict:
    if _said.get(link["id"]) != why:
        _said[link["id"]] = why
        log.warning(f"[annotations] the files of {link['path']}: {why}")
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


def _keys_in(root: Path, img: str) -> set:
    """The keys of the pictures the directory ``img`` holds."""
    return {found[1] for entry in _listed(root, img) if (found := _PICTURE_RE.match(entry.name))}


def _listed(root: Path, rel_dir: str) -> list:
    """The files of the directory ``rel_dir`` of ``root`` (``vault_disk.path``:
    never below a link), none for a directory that is not there."""
    try:
        with os.scandir(vault_disk.path(root, rel_dir)) as found:
            return [entry for entry in found if entry.is_file(follow_symlinks=False)]
    except FileNotFoundError:
        return []


def _read(root: Path, rel: str) -> bytes | None:
    """The bytes of the file ``rel`` of ``root`` (``vault_disk.path``), None for none."""
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
