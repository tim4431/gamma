"""Zotero links (docs/dev/zotero_sync.md): a personal workspace kept in
two-way step with one Zotero library through the Web API, as a clone is
kept with its origin (gamma/sync_engine.py), with the same surfaces, the
same clock (gamma/sync_clock.py) and the same conflict and log tables.

This module is the engine's public face: the registry, the round and its
steps, the resolution of conflicts, the clock. The parts a round runs live
beside it, and none of them imports this one:

- gamma/zotero_sync_items.py: an item's unit (an item, or a child alone,
  with its page: the record, tags, collections and trash);
- gamma/zotero_sync_pdfs.py: an item's PDF, part of its unit, and a file's
  own unit (a download, an upload), Zotero's full storage;
- gamma/zotero_sync_blocks.py: an item's notes and annotations, which ride
  on its unit;
- gamma/zotero_sync_pages.py: a Gamma page's unit (a page made an item, a
  standalone PDF given a parent item);
- gamma/zotero_sync_gone.py: the items Zotero removed for good;
- gamma/zotero_sync_first.py: a first link's pairs, and the link dialog's
  preview of them;
- gamma/zotero_sync_resolve.py: the field and file conflicts' resolvers;
- gamma/zotero_sync_folders.py: collections ↔ folders;
- gamma/zotero_sync_reads.py: what the units read of both sides (a page,
  Zotero's objects read ahead);
- gamma/zotero_sync_rows.py: what they all share (the link's stored row,
  the round's state, the rows in pages.db, conflict rows and log lines).

The registry is users.db ``zotero_links``, one row per linked workspace
(see docs/dev/zotero_sync.md#data): ``create_link`` checks the account's
connection and the library first, and a workspace is a clone, a
publication's mirror or a Zotero link, never two of these;
``set_settings``, ``detach_link`` / ``relink_link``, ``force_sync``,
``remove_link`` (which forgets everything the link kept in the workspace's
pages.db), ``fetch_page`` (a page's PDF before anything else),
``lift_quota`` and ``preview`` (the link dialog's counts). The row's status JSON is only ever patched, under the engine's own
lock (``patch_status``).

A round (``sync_workspace``, under the workspace's round lock, which the
clock never waits on) is bounded: it stops taking new work after
``ROUND_BUDGET_S`` or ``ROUND_UNITS`` units and asks the clock for a
follow-up while work remains, so a first fill of thousands of items is
many short rounds. The clock runs up to ``WORKERS`` links' rounds at once
(gamma/sync_clock.py), and a link detached or removed while its round
waits on Zotero or moves a file stops it there (its client's heartbeat).
Its steps (see docs/dev/zotero_sync.md#the-round):

0. Access: the account's client (gamma/zotero_account.py) and, every
   ``ACCESS_TTL_S``, what its key may do in the library; a refused key, a
   library it no longer reaches, Zotero unreachable or busy become the
   status's ``error_kind``; a workspace that is no longer a personal one
   of the link's owner is refused (the key is the owner's). A key that
   cannot write makes the round receive only (write or file access granted
   again has every page looked at again; notes access granted lists the
   library again, so the notes the key could not see arrive). Another
   library's rows in pages.db are wiped. A workspace
   backup restore (``ws_backup`` marks it in ``zotero_state.restored_at``)
   lets the restored pages win their differences (see
   docs/dev/zotero_sync.md#backup-restore).
1. A force asked for: every row's bases read as a first link's, and both
   sides are listed from the start. A wait on Zotero's full storage that
   has lifted brings back the PDFs it held. A first link of an existing
   workspace pairs its pages with the library's items before anything is
   listed (``first_match``); notes access granted later pairs the note
   pages the same way (``first_notes``).
2. Zotero's changes since ``zotero_state.version`` go into the queue
   (``zotero_remote.list_changes``); what Zotero removed for good is
   applied at once. The listing, not the application, moves the version.
3. Gamma's changes since ``zotero_state.local_cursor`` go into the queue
   (two-way rounds only: a receive-only round keeps the cursor): the
   local change feed in-process, the folder and label trees included, and
   the pages carrying a label renamed since the last two-way round.
4. The queue, highest priority first: collections ↔ folders in one pass
   (``collections_pass``), then one unit at a time (``run_unit``: a
   top-level item with its children, a child alone, a Gamma page, a
   file). A page the feed lists that is as it last synced is settled at no
   cost of the budget. A unit whose page (or Zotero's object) changed under
   its decision writes nothing on either side and is set aside; a round
   comes for it a few seconds later. A unit that fails backs off; after
   ``zotero_remote.MAX_ATTEMPTS`` it is given up as a ``zotero_rejected``
   conflict. Zotero unreachable, busy or refusing the key ends the round
   instead, the unit untouched.
5. Each unit's writes: Gamma's side in one transaction, then
   Zotero's. A write that must follow another (an item's annotations once
   its PDF is here) is queued in the transaction that records the first,
   so a process killed between them loses nothing.
6. The record: the status (the round's counters, ``backlog``,
   ``waiting``, ``last_error`` / ``error_kind``; a fill's progress counted
   over the bounded rounds it takes, ``fill``) and the clock's dirty
   mark. Conflicts and log rows go to gamma/sync_engine.py's tables after
   each commit; a link and a mirror never share a workspace.

Writes to Gamma go through gamma/zotero_local.py as actor
``auth.ZOTERO_ACTOR`` and client ``CLIENT``, so the link's clock never
reads them as local edits; writes to Zotero go through one
``zotero_remote.Writes`` per unit.
"""

import contextlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

from . import (config, sync_clock, sync_engine, workspaces, zotero_account, zotero_local, zotero_merge,
               zotero_sync_items, zotero_sync_pages, zotero_sync_pdfs, zotero_sync_rows)
from . import zotero_remote as zr
from .blocks_store import FOLDERS, label_names, newest_change_seq, write_lock
from .db import connect_pages_db, connect_users_db, page_now
from .logbuf import log
from .routers.sync import changes as local_changes
from .zotero_account import AccountError
from .zotero_sync_blocks import NOTES_WAIT, label_checks, resolve_annotation, resolve_lossy, resolve_note
from .zotero_sync_first import first_match, first_notes, preview_first_link
from .zotero_sync_folders import TREE_ENTRIES, collections_due, collections_gone, collections_pass
from .zotero_sync_gone import items_gone
from .zotero_sync_pdfs import STORAGE_WAIT, quota_step
from .zotero_sync_resolve import resolve_field, resolve_file
from .zotero_sync_rows import (CLIENT, CONFLICT_KINDS, DEBOUNCE_S, ENGINE, HELD, LINKS,  # noqa: F401 (public here)
                               LIVE_POLL_S, LIVE_SAFETY_S, SETTLED, STOPS_ROUND, WORKERS, Detached, Round,
                               json_dict, new_row, patch_status, read_rows, record_conflict, save_link,
                               without_quota_wait, write_rows)
from .zotero_web import LIBRARY_RE, PAGE_LIMIT, ZoteroAuthError, ZoteroError, ZoteroNotFound, item_uri

DOWNLOADS = ("all", "open")         # open: a PDF comes when its page is opened (and for the newest items)
ADOPT = ("theirs", "mine")          # whose version a pair a first link or a force matched takes

ROUND_BUDGET_S = 60                 # a round takes no new work after this long...
ROUND_UNITS = 50                    # ...or this many units, and asks for a follow-up when work remains
ACCESS_TTL_S = 900                  # how long a round trusts what the key could do (any error forgets it)
FEED_BATCH = 1000                   # local change feed entries per read...
FEED_READS_MAX = 200                # ...and reads per round (the rest waits for the next)
DETACH_CHECK_S = 1.0                # how often a round's waits look whether the link was detached meanwhile

PREVIEW_PAGES = 20                  # pages (100 objects each) of a listing the link dialog's counts read at most
PREVIEW_DEADLINE_S = 30             # how long the counts wait on Zotero (its pauses, the key's busy slots)
PREVIEW_CACHE_S = 120               # the counts asked again this soon, neither side changed: the same answer
PREVIEW_BUSY = "Zotero is busy: try the count again in a moment"
PREVIEW_RUNNING = "A count for this account is already running: try again in a moment"
LINKED_ALREADY = "you already link that Zotero library to a workspace"  # create_link's refusal of a second link

UNIT_KINDS = ("top", "child", "page", "file")


# --- the registry -----------------------------------------------------------------------

# The link's stored row is read through gamma/zotero_sync_rows.py, which every part shares: public here
get_link = zotero_sync_rows.get_link
list_links = zotero_sync_rows.list_links

_stored_mode = LINKS.stored_mode  # the link's mode as stored now ("off": detached, or no link)


def _readable(libraries: list[dict], library: str) -> dict | None:
    """``library``'s entry in ``ZoteroClient.libraries()`` when the key reads it, else None."""
    found = next((lib for lib in libraries if lib.get("id") == library), None)
    return found if found and found.get("read") else None


@contextlib.contextmanager
def asking_zotero():
    """Zotero's refusals as the link dialog's (and Settings') AccountError:
    the key refused 409 "auth", anything else 502 "offline". A round never
    asks through it: ``zotero_remote.error_kind`` words what it meets."""
    try:
        yield
    except ZoteroAuthError as e:
        raise AccountError(str(e), 409, "auth") from None
    except ZoteroError as e:
        raise AccountError(str(e), 502, "offline") from None


def _library_entry(client, library: str) -> dict:
    """The library as the key reaches it (``ZoteroClient.libraries``):
    AccountError when Zotero refuses the key (409) or cannot be asked
    (502), ValueError when the key does not reach the library."""
    with asking_zotero():
        libraries = client.libraries()
    found = _readable(libraries, library)
    if not found:
        raise ValueError("your Zotero key cannot read that library")
    return found


def _owners_personal(ws: str, owner: str) -> bool:
    """Whether ``ws`` is a personal workspace ``owner`` owns: the only kind a
    link of the owner's key keeps."""
    info = workspaces.get(ws)
    return bool(info) and info["kind"] == "personal" and workspaces.role_of(ws, owner) == "owner"


def _access_words(lib: dict) -> dict:
    return {name: bool(lib.get(name)) for name in ("write", "files", "notes")}


def create_link(owner: str, library: str, *, workspace_id: str = "", name: str = "", mode: str = "two-way",
                download: str = "all", upload: bool = True, auto_tags: bool = False,
                send_unrecorded: bool = False, adopt: str = "theirs") -> dict:
    """Link ``library`` ("users/<id>" | "groups/<id>", one the account's key
    reads) to a new personal workspace of ``owner``'s named ``name``, or to
    ``workspace_id``, an existing one of the owner's that is neither a clone
    nor publishes pages nor is linked (a publication whose last page was
    unpublished counts as none, as for ``workspaces.update``, and is
    forgotten once the link is made, ``sync_engine.remove_mirror``, so the
    two never share the conflict and log tables): its pages and the
    library's items are then matched in the first round (a first link, see
    docs/dev/zotero_sync.md#first-link-and-force), each pair
    taking ``adopt``'s version where they differ. Talks to Zotero first, so
    a missing connection (AccountError), a refused key or a library the key
    does not read fails before anything is made. An owner links a library
    once (the check and the new row are one step: two requests at once make
    one link; users.db's unique index, the backstop, is refused the same
    way). ``mode`` is what the owner asked for; the status's ``mode``
    is the round's own (pull while the key cannot write). Returns the link
    (the caller starts its first round)."""
    if not isinstance(library, str) or not LIBRARY_RE.match(library):
        raise ValueError("that is not a Zotero library")
    if mode not in ("two-way", "pull"):
        raise ValueError("mode must be two-way or pull")
    if download not in DOWNLOADS:
        raise ValueError("download must be all or open")
    if adopt not in ADOPT:
        raise ValueError("adopt must be theirs or mine")
    lib = _library_entry(zotero_account.client_for(owner), library)
    status = {"mode": mode if lib.get("write") else "pull", "access": _access_words(lib),
              "username": zotero_account.status(owner).get("username", "")}
    with _creating:
        with connect_users_db() as conn:
            if conn.execute("SELECT 1 FROM zotero_links WHERE owner = ? AND library = ?",
                            (owner, library)).fetchone():
                raise ValueError(LINKED_ALREADY)
        emptied = False  # a publication whose last page was unpublished (page_filter '[]'), forgotten below
        if workspace_id:
            if not _owners_personal(workspace_id, owner):
                raise ValueError("that is not a workspace of yours")
            with connect_users_db() as conn:
                mirror = conn.execute("SELECT page_filter FROM mirrors WHERE workspace_id = ?",
                                      (workspace_id,)).fetchone()
                if mirror is not None and mirror[0] != "[]":
                    raise ValueError("that workspace is a clone or publishes pages — forget that link first")
                if conn.execute("SELECT 1 FROM zotero_links WHERE workspace_id = ?", (workspace_id,)).fetchone():
                    raise ValueError("that workspace is linked to a Zotero library already")
            emptied = mirror is not None
            status["adopt"] = adopt
            info = {"id": workspace_id}
        else:
            default = "Zotero" if lib.get("kind") == "user" else str(lib.get("name") or "Zotero")
            info = workspaces.create((name or "").strip() or default, owner)
        try:
            with connect_pages_db(info["id"]) as conn:
                write_lock(conn)
                zr.clear(conn)  # nothing of an earlier link of this workspace stays
                zr.set_state(conn, library=library, first="existing" if workspace_id else "new")
                conn.commit()
            with connect_users_db() as conn:
                try:
                    conn.execute(
                        "INSERT INTO zotero_links (workspace_id, owner, library, library_name, mode, download, "
                        "upload, auto_tags, send_unrecorded, status, created_at) VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (info["id"], owner, library, str(lib.get("name") or ""), mode, download, int(bool(upload)),
                         int(bool(auto_tags)), int(bool(send_unrecorded)), json.dumps(status), page_now()))
                except sqlite3.IntegrityError:  # (the owner-library index: a link made past the check)
                    raise ValueError(LINKED_ALREADY) from None
                conn.commit()
            if emptied:  # its row, sync state, conflicts and log go, as Forget would take them
                with sync_engine.round_lock(info["id"]):
                    sync_engine.remove_mirror(info["id"])
        except BaseException:
            if not workspace_id:  # the workspace made for the link goes with it, never left empty behind
                try:
                    workspaces.delete(info["id"])
                except Exception as e:  # noqa: BLE001 — the first failure is the one to answer
                    log.warning(f"[zotero] {info['id']}: the workspace made for a link that failed stayed: {e}")
            raise
    return get_link(info["id"])


_creating = threading.Lock()  # create_link's check for the owner's library and its new row, as one step


def _reset_local_cursor(ws: str) -> None:
    """Every Gamma page is looked at again by the next two-way round."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        zr.set_state(conn, local_cursor="")
        conn.commit()


def set_settings(ws: str, *, poll_s: int | None = None, on_change: bool | None = None, mode: str | None = None,
                 download: str | None = None, upload: bool | None = None, auto_tags: bool | None = None,
                 send_unrecorded: bool | None = None) -> dict:
    """How the link keeps in step: ``poll_s`` (5 = Live, 0 = only Sync
    now), ``on_change``, ``mode`` (two-way / pull; refused while
    detached), ``download`` (all / open), ``upload``, ``auto_tags``,
    ``send_unrecorded``. A change that lets more of Gamma travel (uploads,
    PDF pages without a record, receive-only back to two-way) starts the
    local feed over, so the pages it now concerns are looked at; a change
    of ``auto_tags`` has every item looked at again; any change lifts a
    wait on Zotero's full storage. Returns the link."""
    link = get_link(ws)
    if not link:
        raise ValueError("not a Zotero link")
    fields = {}
    if poll_s is not None:
        fields["poll_s"] = max(0, min(int(poll_s), sync_clock.POLL_MAX_S))
    if on_change is not None:
        fields["on_change"] = int(bool(on_change))
    if download is not None:
        if download not in DOWNLOADS:
            raise ValueError("download must be all or open")
        fields["download"] = download
    for key, value in (("upload", upload), ("auto_tags", auto_tags), ("send_unrecorded", send_unrecorded)):
        if value is not None:
            fields[key] = int(bool(value))
    if mode is not None:
        if mode not in ("two-way", "pull"):
            raise ValueError("mode must be two-way or pull")
        if link["mode"] == "off":
            raise ValueError("the link is detached — reattach it first")
        fields["mode"] = mode
    reset = ((upload and not link["upload"]) or (send_unrecorded and not link["send_unrecorded"])
             or (mode == "two-way" and link["mode"] == "pull"))
    if fields:
        save_link(ws, **fields)
    if reset:
        _reset_local_cursor(ws)
    if auto_tags is not None and bool(auto_tags) != link["auto_tags"]:
        with connect_pages_db(ws) as conn:
            write_lock(conn)
            zr.enqueue(conn, [{"id": key, "kind": "top", "version": version} for key, version in conn.execute(
                "SELECT key, version FROM zotero_objects WHERE kind = 'item' AND version > 0").fetchall()])
            conn.commit()
    if fields:
        def patch(status):
            status = without_quota_wait(status)
            if mode is not None:  # the round's own direction as it will be (pull while the key cannot write)
                can_write = (status.get("access") or {}).get("write", True)
                status = {**status, "mode": "two-way" if mode == "two-way" and can_write else "pull"}
            return status

        patch_status(ws, patch)
    return get_link(ws)


def detach_link(ws: str) -> dict:
    """Detach: no round runs and the workspace is an ordinary one again, but
    the link and every row are kept, so ``relink_link`` goes on with a
    proper merge of what both sides did meanwhile. A round in flight stops
    at its next unit."""
    link = get_link(ws)
    if not link:
        raise ValueError("not a Zotero link")
    if link["mode"] == "off":
        return link
    save_link(ws, mode="off")
    patch_status(ws, {"detached_at": page_now(), "detached_mode": link["mode"]}, drop=("force",))
    return get_link(ws)


def relink_link(ws: str) -> dict:
    """Link a detached workspace again, in the direction it had. The
    account must still have a Zotero connection (AccountError). A backup
    restored while it was detached is handled by the next round
    (``_own_library``): another library's rows are wiped and the pages
    matched again, the restored pages winning. Returns the link; the
    caller runs a round."""
    link = get_link(ws)
    if not link:
        raise ValueError("not a Zotero link")
    zotero_account.client_for(link["owner"])
    wanted = link["status"].get("detached_mode") or link["mode"]
    save_link(ws, mode=wanted if wanted in ("two-way", "pull") else "two-way")
    _access_seen.pop(ws, None)
    patch_status(ws, {}, drop=("detached_at", "detached_mode", "last_error", "error_kind"))
    return get_link(ws)


def force_sync(ws: str, direction: str) -> None:
    """Take one side's version of everything both sides have: ``pull``
    Zotero's, ``push`` Gamma's (a two-way link whose key may write). Every
    pair is decided afresh as at a first link, the forced side winning,
    its trash included: a pull sends to Recently deleted the pages whose
    items are in Zotero's trash, takes off their pages the notes and
    annotations Zotero trashed and brings back the pages whose items Zotero
    holds; a push sends to Zotero's trash what Gamma deleted and brings
    back out of it what Gamma holds. A push takes Zotero's version all the
    same of a PDF whose annotations in Zotero Gamma may not write (Zotero's
    file comes over the page's) and of a note Gamma reads lossily (Zotero's
    note comes over Gamma's, unless Send anyway was chosen for it,
    ``resolve_lossy``); a PDF a push may not send (uploads off, a file
    Zotero Storage does not hold) or one a pull cannot fetch (no file
    access) leaves both files as they are. A page a force deletes waits in
    Recently deleted, and an item in Zotero's trash; a note or annotation a
    pull takes off its page keeps no copy here, only Zotero's in its trash.
    The records, notes and annotations the forced side replaced are written
    as resolved conflict rows (``list_conflicts(resolved=True)``; the open
    ones alone are the link's conflicts) and counted in a log line for each
    round that replaced some ("replaced here: 2 records"). A page's PDF
    replaced here is kept in no row: once nothing names it,
    gamma/upload_gc.py purges it 30 days later. Gamma keeps no history or
    versions of a page, so only a workspace backup taken before holds a page
    as it was. The one replaced file kept is Zotero's PDF that a push writes
    Gamma's over: here, in an open ``zotero_file`` conflict. Noted on the
    status (``force``) for the next round, which runs in the background."""
    if direction not in ("pull", "push"):
        raise ValueError("direction must be pull or push")
    link = get_link(ws)
    if not link:
        raise ValueError("not a Zotero link")
    if link["mode"] == "off":
        raise ValueError("the link is detached — reattach it first")
    if direction == "push" and (link["mode"] != "two-way" or link["status"].get("mode") == "pull"):
        raise ValueError("a receive-only link cannot replace Zotero's copy")
    patch_status(ws, {"force": direction})
    sync_in_background(ws)


def remove_link(ws: str) -> None:
    """Forget the link: the workspace stays as an ordinary one, its pages
    and Zotero's items as they are; what the link kept in the workspace's
    pages.db (its objects, queue and state, the sync log and conflicts) goes,
    and so do the marks it left on pages and blocks (``zotero_pdf``,
    ``zotero_readonly``), on the pages in Recently deleted too, so a page
    brought back later takes a PDF like any other. A round in flight is
    stopped first (at its next unit, or at once while it waits on Zotero
    or moves a file)."""
    if not get_link(ws):
        return
    save_link(ws, mode="off")  # a round in flight stops at its next unit...
    with _clock.lock(ws):  # ...and is waited for
        with connect_users_db() as conn:
            conn.execute("DELETE FROM zotero_links WHERE workspace_id = ?", (ws,))
            conn.commit()
        with connect_pages_db(ws) as conn:
            write_lock(conn)
            zr.clear(conn)
            conn.execute("DELETE FROM sync_conflicts")
            conn.execute("DELETE FROM sync_log")
            # a page in Recently deleted is no page an op may write: its marks go in place (no session has it
            # open; bringing it back touches it, so a clone fetches it whole)
            conn.execute(f"UPDATE unified_blocks SET properties = json_remove(properties, '$.zotero_pdf', "
                         f"'$.zotero_readonly', '$.zotero_author') WHERE ({_MARKED}) AND NOT EXISTS (SELECT 1 FROM "
                         f"unified_blocks p WHERE p.id = CASE WHEN unified_blocks.page_id = '' THEN unified_blocks.id "
                         f"ELSE unified_blocks.page_id END AND p.parent_id = 'root')")
            conn.commit()
            marked = conn.execute(f"SELECT id, page_id FROM unified_blocks WHERE {_MARKED}").fetchall()
    _access_seen.pop(ws, None)
    _clock.pending.pop(ws, None)
    by_page: dict[str, list] = {}
    for block_id, page_id in marked:
        by_page.setdefault(page_id or block_id, []).append(zotero_local.props_op(
            block_id, {"zotero_pdf": None, "zotero_readonly": None, "zotero_author": None}))
    for page_id, ops in by_page.items():
        try:
            zotero_local.apply(ws, page_id, ops)
        except Exception as e:  # noqa: BLE001 — a mark left behind is no reason to keep the link
            log.warning(f"[zotero] {ws}: the link's marks on {page_id} stayed: {e}")


_MARKED = ("json_extract(properties, '$.zotero_pdf') IS NOT NULL "
           "OR json_extract(properties, '$.zotero_readonly') IS NOT NULL")


def fetch_page(ws: str, page_id: str) -> bool:
    """Put a page's PDF in Zotero (its ``zotero_pdf``) and then the page
    itself first in the link's queue, and ask for a round: someone opened
    the page. False when the workspace has no such page."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (page_id,)).fetchone()
        if not row:
            return False
        pdf = json_dict(row[0]).get("zotero_pdf")
        entries = [zr.page_entry(page_id, opened=True)]
        key = pdf.get("key") if isinstance(pdf, dict) else None
        if isinstance(key, str) and key:
            entries.insert(0, zr.file_entry(key, opened=True))
        write_lock(conn)
        zr.enqueue(conn, entries)
        conn.commit()
    _clock.request(ws, 0)
    return True


def lift_quota(ws: str) -> None:
    """Sync now: a wait on Zotero's full storage is lifted, so the PDFs it
    held are tried again."""
    patch_status(ws, without_quota_wait)


def pending_local(link: dict) -> bool:
    """The API's ``pending_local``: a two-way link (whose key may write) has
    a Gamma change no round has sent yet: a write since the last clean
    round, or a Gamma page queued and due (not one held back: a page whose
    notes wait to be still, a backoff)."""
    if link["mode"] != "two-way" or link["status"].get("mode") == "pull":
        return False
    if _clock.has_local_changes(link["workspace_id"]):
        return True
    with connect_pages_db(link["workspace_id"]) as conn:
        return zr.size(conn, kinds="page", now=page_now()) > 0


def following(link: dict) -> bool:
    """Whether the link's fill goes on (``status.fill``): a round of it is
    asked for or running in the clock's pool."""
    return (bool(link["status"].get("fill")) and config.sync_interval_s() > 0
            and _clock.queued(link["workspace_id"]))


def remote_url(link: dict) -> str:
    """The library on zotero.org: ``<web>/<username>/library`` or
    ``<web>/groups/<id>/library``."""
    base = config.zotero_web_base()
    kind, _, number = link["library"].partition("/")
    if kind == "groups":
        return f"{base}/groups/{number}/library"
    username = link["status"].get("username") or zotero_account.status(link["owner"]).get("username") or ""
    return f"{base}/{username}/library" if username else base


def preview(owner: str, library: str, workspace_id: str = "") -> dict:
    """What linking ``library`` would move, for the link dialog: ``{items,
    notes, attachments, pdfs, pdf_bytes, linked_files, partial}``
    (top-level items, notes and attachments as Zotero counts them, the
    trash left out; the PDFs in Zotero Storage and their bytes, and the
    linked PDFs Gamma cannot download, among the first ``PREVIEW_PAGES``
    pages of attachments: ``partial`` when there are more) and, into
    ``workspace_id`` (an existing personal workspace of the owner's), what
    its first link would pair and write to Zotero, by the first link's own
    match (``zotero_sync_first.preview_first_link``): ``{matches, to_send,
    to_send_bytes, collections_to_add, tags_to_add, notes_to_send,
    highlights_to_send, highlights_new}``, each None for a library past
    what the counts read (the first round pairs it all the same). One count
    per account at a time (AccountError 429 "busy"), waiting on Zotero at
    most ``PREVIEW_DEADLINE_S`` (503 "busy"); the same count again within
    ``PREVIEW_CACHE_S``, neither side changed since, is answered from the
    last. AccountError / ValueError as ``create_link``."""
    with _preview_guard:
        if owner in _previewing:
            raise AccountError(PREVIEW_RUNNING, 429, "busy")
        _previewing.add(owner)
    try:
        return _preview(owner, library, workspace_id)
    finally:
        with _preview_guard:
            _previewing.discard(owner)


def _counted(headers: dict) -> int:
    """A listing's ``Total-Results``: how many objects it holds."""
    total = headers.get("total-results", "")
    return int(total) if total.isdigit() else 0


_preview_guard = threading.Lock()
_previewing: set[str] = set()     # owners whose count runs
_previews: dict[tuple, tuple] = {}  # (owner, library, workspace) -> (library version, workspace seq, when, answer)


def _preview(owner: str, library: str, workspace_id: str) -> dict:
    give_up = time.monotonic() + PREVIEW_DEADLINE_S

    def beat():
        if time.monotonic() > give_up:
            raise AccountError(PREVIEW_BUSY, 503, "busy")

    client = zotero_account.client_for(owner, heartbeat=beat)
    if workspace_id and not _owners_personal(workspace_id, owner):
        raise ValueError("that is not a workspace of yours")
    seq = 0
    if workspace_id:
        with connect_pages_db(workspace_id) as conn:
            seq = newest_change_seq(conn)
    key = (owner, library, workspace_id)
    cached = _previews.get(key)
    with asking_zotero():
        if cached and time.monotonic() - cached[2] < PREVIEW_CACHE_S and cached[1] == seq:
            _library_entry(client, library)  # the key still reads it
            if client.library_version(library, cached[0]) is None:  # Zotero's 304: nothing changed there
                return dict(cached[3])
        _library_entry(client, library)
        items = _counted(client.get_json(f"/{library}/items/top", {"limit": 1})[1])
        notes = _counted(client.get_json(f"/{library}/items", {"itemType": "note", "limit": 1})[1])
        attachments, total, version = client.pages(f"/{library}/items", {"itemType": "attachment"},
                                                   most=PREVIEW_PAGES)
        whole = len(attachments) >= total and items <= PREVIEW_PAGES * PAGE_LIMIT
        tops = collections = []
        if workspace_id and whole:
            tops, _, _ = client.pages(f"/{library}/items/top", most=PREVIEW_PAGES)
            collections, kept, _ = client.pages(f"/{library}/collections", most=PREVIEW_PAGES)
            whole = len(collections) >= kept
    pdfs = pdf_bytes = linked = 0
    for obj in attachments:
        data = obj.get("data") or {}
        if data.get("deleted") or not zotero_merge.is_pdf_attachment(data):
            continue
        if data.get("linkMode") == "linked_file":
            linked += 1
        elif data.get("linkMode") in zotero_merge.STORED_LINK_MODES and data.get("md5"):
            pdfs += 1
            pdf_bytes += zotero_merge.stored_size(obj)
    out = {"items": items, "notes": notes, "attachments": total, "pdfs": pdfs, "pdf_bytes": pdf_bytes,
           "linked_files": linked, "partial": len(attachments) < total}
    if workspace_id:
        if whole:
            with connect_pages_db(workspace_id) as conn:
                out.update(preview_first_link(conn, workspace_id, library, tops, attachments, collections))
        else:  # past what the counts read: the first round pairs them all the same
            out.update(dict.fromkeys(("matches", "to_send", "to_send_bytes", "collections_to_add", "tags_to_add",
                                      "notes_to_send", "highlights_to_send", "highlights_new")))
    _previews[key] = (version, seq, time.monotonic(), dict(out))
    if len(_previews) > 256:  # the oldest answers go first
        for old in sorted(_previews, key=lambda k: _previews[k][2])[:len(_previews) - 128]:
            _previews.pop(old, None)
    return out


# --- conflicts --------------------------------------------------------------------------

# kind -> fn(ws, conflict row dict, choice): writes the choice ("mine" or
# "theirs") before the row is marked resolved; a kind without one is only
# ever kept.
RESOLVERS = {"zotero_field": resolve_field, "zotero_file": resolve_file, "zotero_note": resolve_note,
             "zotero_lossy": resolve_lossy, "zotero_annotation": resolve_annotation}


def resolve_conflict(ws: str, conflict_id: int, choice: str) -> dict | None:
    """``keep`` marks the row seen; ``mine`` / ``theirs`` first write that
    side through the kind's resolver (``RESOLVERS``), and a kind without
    one, or a row resolved already, is a ValueError for them. None when
    there is no such row."""
    if choice not in ("keep", "mine", "theirs"):
        raise ValueError("choice must be keep, mine or theirs")
    with connect_pages_db(ws) as conn:
        found = conn.execute("SELECT id, page_id, block_id, kind, mine, theirs, result, base, resolved "
                             "FROM sync_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    if not found:
        return None
    row = dict(zip(("id", "page_id", "block_id", "kind", "mine", "theirs", "result", "base", "resolved"), found))
    if choice != "keep":
        if row["resolved"]:
            raise ValueError("this conflict is resolved already")
        resolver = RESOLVERS.get(row["kind"])
        if resolver is None:
            raise ValueError("this conflict can only be kept")
        resolver(ws, row, choice)
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE sync_conflicts SET resolved = 1 WHERE id = ?", (conflict_id,))
        conn.commit()
    return {"id": conflict_id, "resolved": True}


list_conflicts = sync_engine.list_conflicts
list_log = sync_engine.list_log
open_conflict_mark = sync_engine.open_conflict_mark


# --- the round --------------------------------------------------------------------------

def sync_workspace(ws: str) -> dict:
    """One round for the link ``ws`` (Sync now, a background round): waits
    for a round already running, then runs with the link as it is then.
    Returns the status saved on the link."""
    with _clock.lock(ws):
        return _held_round(ws)


def _held_round(ws: str) -> dict:
    """``sync_workspace`` with the round lock held: the clock's ``run``."""
    link = get_link(ws)
    if not link:
        raise ValueError("not a Zotero link")
    if link["mode"] == "off":
        return link["status"]  # detached: nothing runs until it is linked again
    return _round(ws, link)


def sync_in_background(ws: str) -> None:
    """A round soon, out of the request that asks for it (Sync now, a
    force, a link made or reattached; the tests replace this): the clock's,
    when its loop runs (its pool bounds how many run at once); else one
    thread, and none more while one waits for the round lock (one more
    round comes after a round running now, never more)."""
    if _clock.looping():
        _clock.request(ws, 0)
        return
    with _background_guard:
        if ws in _background:
            return
        _background.add(ws)

    def run():
        try:
            with _clock.lock(ws):
                with _background_guard:
                    _background.discard(ws)
                _held_round(ws)
        except Exception as e:  # noqa: BLE001 — a background round reports, never dies silently
            log.warning(f"[zotero] {ws}: {e}")
        finally:
            with _background_guard:
                _background.discard(ws)

    threading.Thread(target=run, name=f"zotero-{ws}", daemon=True).start()


_background_guard = threading.Lock()
_background: set[str] = set()  # workspaces with a background round's thread waiting for the round lock


def _round(ws: str, link: dict) -> dict:
    r = Round(ws, link, time.monotonic(), library=link["library"])
    fill = link["status"].get("fill") if isinstance(link["status"].get("fill"), dict) else {}
    r.fill_units, r.fill_bytes = int(fill.get("units") or 0), int(fill.get("bytes") or 0)
    patch_status(ws, {"running": True, "started_at": page_now(), "progress": None}, drop=("interrupted",))
    try:
        _access(r)
        _own_library(r)
        _start_force(r)
        quota_step(r)
        if r.state["first"] == "existing" and not r.state["version"]:
            first_match(r)  # before the first listing: a run cut short matches again what has no row yet
        elif r.state["first"] == "notes" and not r.state["version"]:
            first_notes(r)  # (as a first link pairs a note page, before the listing makes it a page again)
        _list_zotero(r)
        if r.pushes:
            _list_gamma(r)
        _work(r)
        return _record(r)
    except Detached:  # detached or removed while the round waited on Zotero or moved a file: it ends there
        r.detached = True
        if r.access:  # what the key may do is known: the status is the round's, as a detached round's is
            return _record(r)
        return patch_status(ws, {"running": False}, drop=("progress",))
    except Exception as e:  # noqa: BLE001 — whatever happens, the running flag comes down
        kind = zr.error_kind(e)
        _access_seen.pop(ws, None)
        log.warning(f"[zotero] {ws}: {e}")
        patch = {"running": False, "last_error": str(e) or kind, "error_kind": kind, "last_attempt": page_now()}
        if r.units or r.bytes_done:  # the units done before it stopped count in the fill the next round goes on with
            patch["fill"] = {"units": r.fill_units + r.units, "bytes": r.fill_bytes + r.bytes_done}
        return patch_status(ws, patch, drop=("progress",))


# ws -> ((the account's connection, the library), monotonic when, the library as the key reached it)
_access_seen: dict[str, tuple] = {}


def forget_access(ws: str | None = None) -> None:
    """Have the next round of ``ws`` (of every link: None) ask Zotero again
    what its key may do: the key gained or lost a library (the stream's
    ``topicAdded`` / ``topicRemoved``). The link dialog's last counts go
    too: what they read may no longer be the key's."""
    if ws is None:
        _access_seen.clear()
    else:
        _access_seen.pop(ws, None)
    _previews.clear()


def record_error(ws: str, message: str, kind: str = "other") -> dict:
    """An error found outside a round (the stream: Zotero refuses this
    client) on the link's status, worded as a round's (``kind`` one of
    ``zotero_remote.ERROR_KINDS``); what the key may do is asked again, and
    the next clean round clears it. Returns the status."""
    forget_access(ws)
    return patch_status(ws, {"last_error": message, "error_kind": kind if kind in zr.ERROR_KINDS else "other",
                             "last_attempt": page_now()})


def _access(r: Round) -> None:
    """Step 0: the client, and what the key may do in the library (asked at
    most every ``ACCESS_TTL_S`` while the connection stays the same). A
    workspace that is no longer a personal one of the link's owner uses no
    key (``not_connected``: the key is the owner's, and it is refused
    before anything is asked). A library the key no longer reads is
    ``library_gone``; a key that cannot write makes the round receive only;
    write or file access newly granted has every page looked at again (what
    receive-only rounds passed by); notes access newly granted lists the
    library from the start (Zotero hid the notes from every listing
    before), every page looked at again too. The client's heartbeat stops
    the round (``Detached``) once the link is detached or removed."""
    link = r.link
    if not _owners_personal(r.ws, link["owner"]):
        raise AccountError("this workspace is no longer a personal workspace of the link's owner: link it again "
                           "from a workspace of yours", 409, "not_connected")
    r.client = zotero_account.client_for(link["owner"], heartbeat=_detach_check(r.ws))
    account = zotero_account.status(link["owner"])
    r.username = account.get("username") or ""
    mark = ((account.get("user_id"), account.get("hint"), account.get("connected_at")), link["library"])
    seen = _access_seen.get(r.ws)
    if seen and seen[0] == mark and time.monotonic() - seen[1] < ACCESS_TTL_S:
        lib = seen[2]
    else:
        lib = _readable(r.client.libraries(), link["library"])
        if not lib:
            raise ZoteroNotFound(f"your Zotero key no longer reaches {link['library_name'] or link['library']}", 404)
        _access_seen[r.ws] = (mark, time.monotonic(), lib)
    r.access = lib
    r.mode = "two-way" if link["mode"] == "two-way" and lib.get("write") else "pull"
    if lib.get("name") and lib["name"] != link["library_name"]:
        save_link(r.ws, library_name=lib["name"])  # a group renamed in Zotero
    before = link["status"].get("access")
    if isinstance(before, dict) and lib.get("write") and not before.get("write"):
        _reset_local_cursor(r.ws)
    if isinstance(before, dict) and lib.get("files") and not before.get("files"):
        _reset_local_cursor(r.ws)
        with connect_pages_db(r.ws) as conn:  # the PDFs that waited for file access are asked for again
            write_lock(conn)
            zr.enqueue(conn, [zr.file_entry(key) for (key,) in conn.execute(
                "SELECT key FROM zotero_objects WHERE kind = 'attachment' AND state = 'waiting' "
                "AND json_extract(detail, '$.file') = 'no_access'").fetchall()])
            conn.commit()
    if isinstance(before, dict) and lib.get("notes") and not before.get("notes"):
        with connect_pages_db(r.ws) as conn:  # the notes Zotero hid from the key, and Gamma's that waited for them
            write_lock(conn)
            first = zr.get_state(conn)["first"]
            zr.set_state(conn, version=0, local_cursor="", **({} if first else {"first": "notes"}))
            conn.commit()


def _detach_check(ws: str):
    """The round's client heartbeat: ``Detached`` once the link is detached
    or removed, looked at most every ``DETACH_CHECK_S`` while the client
    waits on Zotero or moves a file."""
    seen = [0.0]

    def beat():
        now = time.monotonic()
        if now - seen[0] < DETACH_CHECK_S:
            return
        seen[0] = now
        if _stored_mode(ws) == "off":
            raise Detached(ws)

    return beat


def _own_library(r: Round) -> None:
    """The rows in pages.db are this library's: another library's (a backup
    of another link restored here) are wiped, and the workspace's pages
    are matched again as at a first link of an existing workspace. A
    workspace backup restored since the last round: the
    restored pages win their differences and Zotero's values stay in the
    conflicts. Its rows (the backup's) read every restored record, folder
    and standalone PDF as changed here (Gamma's base blanked: their
    projections are exact, so only a real difference goes); a note's (a
    note page's, a child note's) or an annotation's keeps its bases and
    reads what Zotero changed since the backup as changed here too
    (``detail.restore``, the notes' and annotations' merges), so a mapping
    Gamma reads lossily (a note's HTML, a sticky note's colour) never goes
    back for nothing. A backup holding no rows of this library (one from
    before the link) is matched again as an existing workspace's first
    link, the restored pages winning, never filled as a new workspace."""
    status = r.link["status"]
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        state = zr.get_state(conn)
        restored = bool(state["restored_at"]) and state["restored_at"] != status.get("restore_seen")
        adopt = ""
        if state["library"] != r.library:
            if state["library"]:
                log.info(f"[zotero] {r.ws}: rows of {state['library']} found; linking {r.library} afresh")
                zr.clear(conn)
                zr.set_state(conn, library=r.library, first="existing", restored_at=state["restored_at"])
                adopt = "mine" if restored else ""
            elif restored or status.get("last_sync") or _keyed_pages(conn, r.library):
                log.info(f"[zotero] {r.ws}: a workspace restore without this link's rows: matched again")
                zr.clear(conn)
                zr.set_state(conn, library=r.library, first="existing", restored_at=state["restored_at"])
                adopt = "mine"
            else:
                zr.set_state(conn, library=r.library)
        elif restored:
            log.info(f"[zotero] {r.ws}: a workspace restore: the restored pages win their differences")
            conn.execute("UPDATE zotero_objects SET gsig = '', gbase = '{}' WHERE kind IN ('item', 'collection') "
                         "OR (kind = 'attachment' AND parent = '')")
            conn.execute("UPDATE zotero_objects SET gsig = '', detail = CASE WHEN state IN ('live', 'readonly', "
                         "'held') THEN json_set(detail, '$.restore', json('true')) ELSE detail END "
                         "WHERE kind IN ('annotation', 'note')")
            zr.set_state(conn, local_cursor="")
        conn.commit()
        r.state = zr.get_state(conn)
    if restored or adopt:
        patch = {"restore_seen": state["restored_at"]} if restored else {}
        if adopt:
            patch["adopt"] = adopt
        r.link = {**r.link, "status": patch_status(r.ws, patch)}
    r.adopt = r.link["status"].get("adopt") or ("theirs" if r.state["first"] in ("existing", "notes") else "")


def _keyed_pages(conn, library: str) -> bool:
    """Whether a page carries a ``zotero_key`` of ``library``'s items."""
    return conn.execute("SELECT 1 FROM unified_blocks WHERE parent_id = 'root' AND json_extract(properties, "
                        "'$.zotero_key') LIKE ? LIMIT 1", (item_uri(library, "") + "%",)).fetchone() is not None


def _start_force(r: Round) -> None:
    """Step 1: a force asked for (``status.force``) becomes the round's
    policy: every row's bases read as a first link's (``detail.first``),
    the side an earlier pair kept (``detail.adopt``) dropped, both sides
    are listed from the start and the forced side is ``adopt``."""
    direction = r.link["status"].get("force")
    if direction not in ("pull", "push"):
        return
    if direction == "push" and not r.pushes:
        patch_status(r.ws, {}, drop=("force",))
        r.errors.append(("a receive-only link cannot replace Zotero's copy", "other"))
        return
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        conn.execute("UPDATE zotero_objects SET detail = json_remove(json_set(detail, '$.first', json('true')), "
                     "'$.adopt') WHERE state != 'pending' AND kind != 'image'")
        r.state = zr.set_state(conn, version=0, local_cursor="")
        conn.commit()
    r.adopt = "theirs" if direction == "pull" else "mine"
    status = patch_status(r.ws, {"adopt": r.adopt, "forced": direction}, drop=("force",))
    r.link = {**r.link, "status": status}


def _list_zotero(r: Round) -> None:
    """Step 2: what changed in Zotero since the version into the queue; what
    Zotero removed for good applied at once, its entries dropped first, so
    what the removals queue again (a folder changed as its removal was
    decided) stays queued; the version moved to the listing's (a listing
    from the start's also kept as ``whole_version``: its items' units read
    their children with them, ``zotero_sync_reads._listed_children``)."""
    since = r.state["version"]
    changes = zr.list_changes(r.client, r.library, since)
    if changes is None:
        return  # Zotero's 304: nothing changed there; the queue is still worked
    deleted = [key for keys in changes.deleted.values() for key in keys]
    if deleted:  # (a round cut off after this lists them again: the version has not moved)
        with connect_pages_db(r.ws) as conn:
            write_lock(conn)
            zr.drop(conn, deleted)
            conn.commit()
    if changes.deleted.get("collections"):
        collections_gone(r, changes.deleted["collections"])
    if changes.deleted.get("items"):
        items_gone(r, changes.deleted["items"])
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        zr.queue_changes(conn, changes)
        r.state = zr.set_state(conn, version=changes.version, **({} if since else {"whole_version": changes.version}))
        conn.commit()


def _pages_with_labels(conn, label_ids) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT p.id FROM unified_blocks p WHERE p.parent_id = 'root' AND EXISTS (SELECT 1 FROM "
        "json_each(p.properties, '$.labels') l WHERE l.value IN (SELECT value FROM json_each(?)))",
        (json.dumps(sorted(label_ids)),))}


def _list_gamma(r: Round) -> None:
    """Step 3: the pages the local change feed lists since the cursor (the
    folder and label trees among them) into the queue, but for those whose
    newest change is the link's own write a unit settled them with, and
    every page carrying a label whose name is not the one the last two-way
    round read, so a label renamed here (a pulled one too) reaches each
    item's tags, and once each page with an annotation Gamma made whose
    page label is not settled (``label_checks``), so a label its create
    sent as PyMuPDF reads it is corrected without an edit, and once each
    page whose text annotation's box is not yet a Zotero box
    (``box_checks``), so it takes the key without an edit. The cursor
    moves unless a settings change started it over meanwhile; a page whose
    reconcile fails stays queued, so moving it loses nothing."""
    with connect_pages_db(r.ws) as conn:
        since = zr.get_state(conn)["local_cursor"]
        pages, cursor = set(), since
        for _ in range(FEED_READS_MAX):
            out = local_changes(conn, cursor, FEED_BATCH)
            pages.update(p["id"] for p in out["pages"])
            pages.update(d["id"] for d in out["deleted"])
            cursor = out["cursor"]
            if not out["more"]:
                break
        if since and pages:  # a page whose newest change is the link's own write, which the unit of the page's
            # own row settled whole (the change number that row keeps, ``detail.gseq``: its item's, a standalone
            # PDF's or note's while no item holds the page), is passed by; a person's edit since moves it on. A
            # feed started over (a settings change, a force, a restore) looks at every page again
            pages -= {row[0] for row in conn.execute(
                "SELECT c.page_id FROM page_changes c JOIN zotero_objects o ON o.page_id = c.page_id "
                "WHERE c.page_id IN (SELECT value FROM json_each(?)) AND json_extract(o.detail, '$.gseq') = c.seq "
                "AND (o.kind = 'item' OR NOT EXISTS (SELECT 1 FROM zotero_objects i WHERE i.page_id = c.page_id "
                "AND i.kind = 'item'))", (json.dumps(sorted(pages)),))}
        labels = label_names(conn)
        renamed = [lid for lid, name in labels.items() if r.state["labels"].get(lid) != name]
        if renamed:
            pages |= _pages_with_labels(conn, renamed)
        write_lock(conn)
        checks = label_checks(r, conn)
        zr.enqueue(conn, [zr.page_entry(page_id) for page_id in sorted(pages | checks)])
        # a page changed again is looked at now, whatever else waits for its own notes to be still: only
        # "Notes from Gamma" waits for that, never its highlights, record or Zotero's notes
        zr.wake(conn, [zr.PAGE_PREFIX + page_id for page_id in pages], reason=NOTES_WAIT)
        fields = {"labels": labels}
        if zr.get_state(conn)["local_cursor"] == since:
            fields["local_cursor"] = cursor
        r.state = zr.set_state(conn, **fields)
        conn.commit()


def _work(r: Round) -> None:
    """Step 4: collections first, then units, highest priority first, until
    the budget is spent, the queue holds nothing more due, or the link is
    detached. A unit Gamma passes over this time (``HELD``) costs nothing
    of the budget."""
    deadline = r.started + ROUND_BUDGET_S
    if collections_due(r):
        try:
            collections_pass(r)
        except Detached:
            raise
        except Exception as e:  # noqa: BLE001 — the pass is tried again next round; the units go on
            if zr.error_kind(e) in STOPS_ROUND:
                raise
            log.warning(f"[zotero] {r.ws}: collections: {e}")
            r.error(e)
            with connect_pages_db(r.ws) as conn:
                due = zr.take(conn, now=page_now(), kinds="collection")
            r.excluded.update(entry.id for entry in due)
            r.excluded.update(TREE_ENTRIES)
        r.units += 1
        _progress(r, None)
    while r.units < ROUND_UNITS and time.monotonic() < deadline:
        if _stored_mode(r.ws) == "off":
            r.detached = True  # detached while running: the rest waits for a reattach
            return
        with connect_pages_db(r.ws) as conn:
            batch = zr.take(conn, now=page_now(), limit=ROUND_UNITS, kinds=UNIT_KINDS, exclude=r.excluded)
        if not batch:
            return
        for entry in batch:
            try:
                outcome = run_unit(r, entry)
            except Detached:
                raise
            except Exception as e:  # noqa: BLE001 — one unit must not sink the round; it is tried again later
                if zr.error_kind(e) in STOPS_ROUND:
                    raise
                _failed(r, entry, e)
                outcome = None
            if outcome == HELD:
                r.excluded.add(entry.id)
                continue
            if outcome == SETTLED:
                continue
            r.units += 1
            _progress(r, entry)
            break  # a unit may have taken other entries off the queue: read it afresh


def run_unit(r: Round, entry: zr.Entry):
    """One queue entry's unit, by its kind (an item, a child, a Gamma page,
    a file): ``HELD`` or ``SETTLED`` (rows' words), or anything else once
    done (the handler takes its entry off the queue). Each is looked up on
    its module as it runs, so a test that replaces one reaches it."""
    if entry.kind == "top":
        return zotero_sync_items.unit_item(r, entry)
    if entry.kind == "child":
        return zotero_sync_items.unit_child(r, entry)
    if entry.kind == "page":
        return zotero_sync_pages.unit_page(r, entry)
    return zotero_sync_pdfs.unit_file(r, entry)


def _progress(r: Round, entry: zr.Entry | None) -> None:
    """The pill's "First sync 300 / 2,000 · PDFs 1.2 / 8 GB" while the round
    runs: the units and the PDFs' bytes done in this fill (this round and
    the bounded rounds before it that went on at once) and what is still
    due."""
    with connect_pages_db(r.ws) as conn:
        left = zr.size(conn, now=page_now())
        queued = conn.execute(  # a file entry's id is its key after the prefix: the objects' key index finds each
            "SELECT COALESCE(SUM(json_extract(o.detail, '$.size')), 0) FROM zotero_queue q JOIN zotero_objects o "
            "ON o.key = substr(q.id, length(?) + 1) WHERE q.kind = 'file'", (zr.FILE_PREFIX,)).fetchone()[0]
    page = ""
    if entry is not None and entry.id.startswith(zr.PAGE_PREFIX):
        page = sync_engine._title_of(r.ws, entry.id[len(zr.PAGE_PREFIX):])
    r.bytes_total = r.bytes_done + int(queued or 0)
    done, moved = r.fill_units + r.units, r.fill_bytes + r.bytes_done
    patch_status(r.ws, {"progress": {"phase": "first" if r.state.get("first") else "sync", "done": done,
                                     "total": done + left, "page": page, "at": page_now(),
                                     "bytes": {"done": moved, "total": r.fill_bytes + r.bytes_total}}})


def _failed(r: Round, entry: zr.Entry, e: Exception) -> None:
    """A unit that raised: its entry waits longer each time, and after
    ``MAX_ATTEMPTS`` leaves the queue, its row terminal at the version it
    failed at (a Zotero change brings it back) and a ``zotero_rejected``
    conflict saying so."""
    message = str(e) or type(e).__name__
    log.warning(f"[zotero] {r.ws}: {entry.id}: {message}")
    r.error(e)
    with connect_pages_db(r.ws) as conn:
        write_lock(conn)
        attempts = zr.backoff(conn, entry.id, message, now=page_now())
        where = None
        if attempts >= zr.MAX_ATTEMPTS:
            zr.drop(conn, [entry.id])
            where = _give_up(conn, entry, message)
        conn.commit()
    if where:
        record_conflict(r.ws, where[0], where[1], "zotero_rejected",
                        result=f"Gamma could not apply this after {attempts} tries: {message}")


def _give_up(conn, entry: zr.Entry, message: str) -> tuple[str, str]:
    """The row of an entry given up, terminal at the version it was listed
    at; ``(page id, block id)`` of its conflict (the key itself for a row
    with no block, so each keeps its own)."""
    if entry.kind == "page":
        page_id = entry.id[len(zr.PAGE_PREFIX):]
        return page_id, page_id
    key = entry.id[len(zr.FILE_PREFIX):] if entry.kind == "file" else entry.id
    row = read_rows(conn, "key = ?", (key,)).get(key)
    if row is None:
        kind = "collection" if entry.kind == "collection" else "attachment" if entry.kind == "file" else "item"
        row = new_row(key, kind, page_id=FOLDERS if kind == "collection" else "")
    if entry.kind == "file":
        row.update(state="waiting", detail={**row["detail"], "file": zr.FAILED, "reason": message})
    else:
        row.update(state="rejected", detail={**row["detail"], "reason": message})
    row["version"] = max(int(row["version"]), int(entry.version))
    write_rows(conn, [row])
    return row["page_id"], row["block_id"] or row["page_id"] or key


def _waiting(conn) -> dict:
    """``status.waiting`` from the rows: what waits on something outside
    Gamma: PDFs by the reason they cannot come or go
    (``detail.file``: ``linked``, ``unavailable``, ``no_access``, ``quota``
    for Zotero's full storage, ``storage`` for this server's, ``failed``),
    annotations by theirs (``detail.reason``), notes."""
    files = {reason or zr.FAILED: n for reason, n in conn.execute(
        "SELECT json_extract(detail, '$.file'), COUNT(*) FROM zotero_objects WHERE kind = 'attachment' "
        "AND state = 'waiting' GROUP BY 1")}
    reasons = {reason or "": n for reason, n in conn.execute(
        "SELECT json_extract(detail, '$.reason'), COUNT(*) FROM zotero_objects WHERE kind = 'annotation' "
        "AND state IN ('waiting', 'held') GROUP BY 1")}
    notes = conn.execute("SELECT COUNT(*) FROM zotero_objects WHERE kind = 'note' AND state = 'waiting'").fetchone()[0]
    return {"files": files, "annotations": {"n": sum(reasons.values()), "reasons": {k: v for k, v in reasons.items()
                                                                                    if k}},
            "notes": notes}


def _spent(conn, backlog: int) -> bool:
    """Whether a first link or a force is over: nothing is queued but the
    PDFs this server's full storage holds back (``STORAGE_WAIT``), which
    may wait for days; Zotero's full storage queues none (its rows wait).
    Anything else held back (notes waiting for the page to be still, a
    backoff after an error) is still part of it."""
    if not backlog:
        return True
    held = conn.execute("SELECT COUNT(*) FROM zotero_queue WHERE reason = ?", (STORAGE_WAIT,)).fetchone()[0]
    return backlog == held


def _record(r: Round) -> dict:
    """Step 6: the status, the first link or force spent once nothing of it
    is left but PDFs held back by full storage (``_spent``), its pairs still
    to decide keeping its side, the dirty mark settled after a clean round,
    and a follow-up round asked for: at once
    while due work remains (the fill's counts kept for it,
    ``status.fill``), a few seconds later for what was set aside to decide
    again, else when the queue's soonest wait ends."""
    now = page_now()
    with connect_pages_db(r.ws) as conn:
        backlog = zr.size(conn)
        remains = bool(zr.take(conn, now=now, limit=1, exclude=r.excluded))
        later = zr.next_wait(conn, now=now)
        again = bool(zr.due(conn, r.again, now=now))
        waiting = _waiting(conn)
        asked = r.link["status"]
        spent = (bool(r.state.get("first") or asked.get("adopt") or asked.get("forced")) and not r.errors
                 and _spent(conn, backlog))
        if spent:
            write_lock(conn)
            if r.adopt:
                # the pairs not decided yet (a PDF full storage holds, a record Zotero refused) are decided later
                # with the side they were paired under, not the "theirs" a round without ``adopt`` falls back
                # to: each row still marked ``first`` keeps it (``detail.adopt``, read before the round's)
                conn.execute("UPDATE zotero_objects SET detail = json_set(detail, '$.adopt', ?) WHERE "
                             "json_extract(detail, '$.first') IS NOT NULL AND json_extract(detail, '$.adopt') "
                             "IS NULL", (r.adopt,))
            if r.state.get("first"):
                r.state = zr.set_state(conn, first="")
            conn.commit()
    errors = r.errors
    patch = {**r.report, "running": False, "last_sync": now, "mode": r.mode, "backlog": backlog,
             "access": _access_words(r.access), "username": r.username,
             "last_error": errors[0][0] if errors else "", "error_kind": errors[0][1] if errors else ""}
    going_on = remains and not r.detached

    def finish(current):
        quota = (current.get("waiting") or {}).get("quota_until")
        current = {**current, **patch, "waiting": {**waiting, **({"quota_until": quota} if quota else {})}}
        if going_on:  # the next round, at once, goes on with the same fill
            current["fill"] = {"units": r.fill_units + r.units, "bytes": r.fill_bytes + r.bytes_done}
        else:
            current.pop("fill", None)
        if spent:
            for key in ("adopt", "forced"):  # unless a newer one was asked for while the round ran
                if current.get(key) == asked.get(key):
                    current.pop(key, None)
        return current

    status = patch_status(r.ws, finish, drop=("progress",))
    _decided_note(r)
    if not errors:
        _clock.settle(r.ws, r.started)  # every write made before the round started went out with it
    if going_on:
        _clock.request(r.ws, 0)
    elif not r.detached:
        # what was set aside to decide again, a little later; else the soonest wait of the queue (a
        # backoff, a page's notes until it is still)
        delays = [sync_clock.BUSY_RETRY_S] if again else []
        if later:
            delays.append(max(1.0, (zr.when(later) - datetime.now(timezone.utc)).total_seconds()))
        if delays:
            delay = min(delays)
            pending = _clock.pending.get(r.ws)
            if pending is None or pending > time.monotonic() + delay:
                _clock.request(r.ws, delay)
    return status


def _decided_note(r: Round) -> None:
    """The round's log line summing what a first link's or a force's side
    replaced on the other in it (its conflict rows are written resolved):
    "replaced here: 212 records, 31 notes"; a first link or a force over
    many bounded rounds writes one for each round that replaced some."""
    counts = [(n, word) for word, n in (("records", r.decided.get("records", 0)),
                                        ("notes", r.decided.get("notes", 0)),
                                        ("annotations", r.decided.get("annotations", 0))) if n]
    if not counts:
        return
    action = "replaced here" if r.adopt == "theirs" else "replaced there"
    words = ", ".join(f"{n} {word[:-1] if n == 1 else word}" for n, word in counts)
    sync_engine._note(r.ws, "", action, f"{action}: {words}")
    r.decided.clear()


# --- the clock --------------------------------------------------------------------------

def reset_interrupted() -> None:
    """At startup: a link the previous process left ``running`` is not
    running any more; its progress goes, and the next round picks up from
    the queue (nothing is lost: rows and queue are written as each unit
    lands). A link whose fill went on (``status.fill``), or whose round was
    cut short, gets its next round at the clock's first look while the
    loops run; else nothing follows on its own, and the fill's count goes."""
    looping = config.sync_interval_s() > 0

    def settle(ws, mode, status):
        if not (status.get("fill") or status.get("interrupted")):
            return False
        if looping and mode != "off":
            _clock.request(ws, 0)
            return False
        return status.pop("fill", None) is not None

    LINKS.reset_interrupted(settle)


_rows = LINKS.rows  # the clock's rows: every link whose workspace exists and is its owner's personal one


# The link's clock (gamma/sync_clock.py): a local write asks for a round 3 s
# after things went quiet; Live (poll_s 5) is the stream's, with a safety
# round every 30 s; up to ``WORKERS`` links' rounds run at once. The app
# imports this module (through its router) before the clocks start. See
# docs/dev/zotero_sync.md#clock-and-stream.
_clock = sync_clock.register(ENGINE, rows=_rows, run=_held_round, client=CLIENT, reset=reset_interrupted,
                             debounce=DEBOUNCE_S, workers=WORKERS,
                             interval=lambda ws, poll_s: LIVE_SAFETY_S if poll_s == LIVE_POLL_S else poll_s)
