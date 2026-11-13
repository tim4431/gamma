"""Recently deleted: the 30-day trash for pages (docs/dev/home_library.md
"Recently deleted").

Deleting a page moves it here (``ops.trash_page``): its root goes under the
reserved ``trash`` block (``blocks_store.TRASH``) with ``deleted_at`` /
``deleted_by`` in its properties, and its blocks, files, chats and op log
stay. Nothing that lists or reads pages reaches it — they ask for
``parent_id = 'root'`` or for a block's page in the library
(``blocks_store.page_root_id``), which a trashed block has none of — and a copy of the workspace sees it deleted, as after a hard
delete (its row of the change log, ``page_changes``). ``ops.restore_page``
puts it back. ``KEEP_DAYS`` after it went, the sweeper here deletes it for
good through ``ops.delete_page``, the one path that drops a page's chats and
indexes; "Delete permanently" and Empty take the same path at once.

A page trashed with the pages inside it takes them along, each stamped
``deleted_with`` it: the listing shows the page alone (with how many went
with it), and its restore and its deletion for good take them too, so
nothing here reaches them but through their page.

A work folder's file comes back as the work-folder rule says
(gamma/work_folders/filing.py ``may_restore``): one its vault sent here
(``deleted_by_vault``: its file deleted or untracked) comes back only with
its file, and a person restores only a note where its work folder pulls.
Each entry says which it is, so Recently deleted offers Restore only where
it works.
"""

import json
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .auth import actor_names
from .blocks_store import TRASH, load_props, trashed_page, write_lock
from .db import connect_pages_db, format_stamp, parse_stamp, workspace_ids, ws_dir
from .logbuf import log
from .ops import delete_page
from .search import index as text_index

KEEP_DAYS = 30
SWEEP_INTERVAL_S = 3600  # the app lifespan runs ``sweep`` at startup and this often


def purge_at(deleted_at: str) -> str:
    """When a page deleted at ``deleted_at`` is deleted for good (the
    ``page_now`` shape); "" for an unparseable time."""
    at = parse_stamp(deleted_at)
    return format_stamp(at + timedelta(days=KEEP_DAYS)) if at else ""


def named(pages: list[dict]) -> list[dict]:
    """Trash entries (``blocks_store.trashed_page``, whose ``deleted_by`` is
    the actor stored) with ``deleted_by`` as people see it
    (``auth.actor_names``): what goes out to be shown."""
    shown = actor_names([page["deleted_by"] for page in pages])
    for page in pages:
        page["deleted_by"] = shown[page["deleted_by"]]
    return pages


def list_trash(conn) -> list[dict]:
    """The pages in Recently deleted, the last deleted first: ``{id, title,
    folders, parent, deleted_at, deleted_by, work_folder, deleted_by_vault,
    restorable?, purge_at, sub_pages}`` (``named``; ``folders`` the ids of the folders a
    restore puts it back in, ``blocks_store.trashed_folders``: a sub-page's
    are its live parent's top's; ``parent`` the live page a restore puts it
    back inside, "" for none, ``blocks_store.trashed_parent``;
    ``work_folder`` and ``deleted_by_vault`` a work folder's file's, as
    ``blocks_store.trashed_page`` reads them, and ``restorable`` on a
    work folder's file whether a person's restore passes
    (``filing.may_restore``); ``sub_pages`` how many pages
    went there with it, ``ops.trash_page``). A page that went with another
    still in the trash is not listed: it comes back, and goes for good,
    with that one. One read of the trash, counted here."""
    rows = conn.execute("SELECT id, content, properties FROM unified_blocks WHERE parent_id = ?",
                        (TRASH,)).fetchall()
    props = {row[0]: load_props(row[2]) for row in rows}
    went = {page_id: p["deleted_with"] for page_id, p in props.items() if isinstance(p.get("deleted_with"), str)}
    counts = Counter(went.values())
    memo: dict = {}
    pages = named([{**trashed_page(conn, *row, memo=memo), "sub_pages": counts[row[0]]}
                   for row in rows if went.get(row[0]) not in props])
    for page in pages:
        page["purge_at"] = purge_at(page["deleted_at"])
    return sorted(pages, key=lambda p: p["deleted_at"], reverse=True)


def purge(ws: str, page_id: str, *, actor: str = "", sweep: bool = True) -> dict | None:
    """Delete one page of the trash for good (``ops.delete_page``, ``sweep``
    as there); None when ``page_id`` is not in it. The check and the delete
    hold one write lock, so a restore in between never loses the page it
    restored."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != TRASH:
            conn.rollback()
            return None
        return delete_page(ws, conn, page_id, actor=actor, sweep=sweep)


def _trashed(conn) -> list[tuple[str, str, list[str]]]:
    """The trash as a pass of deletions for good walks it, from one read of
    it: ``(page id, deleted_at, the pages that went there with it)`` for
    each entry Recently deleted lists (``list_trash``: a page that went
    with another still there is none), the last deleted first, the pages
    that went with it in their trash order (``ops._went_with``'s)."""
    rows = conn.execute("SELECT id, json_extract(properties, '$.deleted_at'), deleted_with FROM unified_blocks "
                        "WHERE parent_id = ? ORDER BY position, id", (TRASH,)).fetchall()
    ids = {row[0] for row in rows}
    went: dict[str, list[str]] = {}
    for page_id, _, stamp in rows:
        if stamp in ids:
            went.setdefault(stamp, []).append(page_id)
    entries = [(page_id, str(at or ""), went.get(page_id, [])) for page_id, at, stamp in rows if stamp not in ids]
    return sorted(entries, key=lambda e: e[1], reverse=True)


def _purge_entry(ws: str, conn, page_id: str, deleted_at: str, went: list[str], *, actor: str) -> dict | None:
    """``purge`` of one entry of a pass (``_trashed``), on the pass's
    connection and with ``sweep=False``: under one write lock, the check
    that the page is still in the trash, as the pass read it, and its
    deletion with the pages that went with it, without reading the trash
    again. A page restored and deleted again since the read (another
    ``deleted_at``) has what went with it read again (``delete_page``'s
    ``_went_with``); a page of ``went`` restored since is passed by. None
    when the page is no longer in the trash."""
    write_lock(conn)
    rows = {row[0]: row[1:] for row in conn.execute(
        "SELECT id, parent_id, json_extract(properties, '$.deleted_at'), deleted_with FROM unified_blocks "
        "WHERE id IN (SELECT value FROM json_each(?))", (json.dumps([page_id, *went]),))}
    page = rows.get(page_id)
    if not page or page[0] != TRASH:
        conn.rollback()
        return None
    still = None if str(page[1] or "") != deleted_at else [
        sub for sub in went if sub in rows and rows[sub][0] == TRASH and rows[sub][2] == page_id]
    return delete_page(ws, conn, page_id, actor=actor, sweep=False, went=still)


@contextmanager
def _swept_after(ws: str, conn):
    """A pass of deletions for good on ``conn``, each with ``sweep=False``:
    yields the list the pass adds the ids it deleted to, and when that is
    not empty purges data.db once at the end, on the same connection, also
    after a deletion that raised (the pages before it are gone, and a later
    pass may delete nothing)."""
    gone = []
    try:
        yield gone
    finally:
        if gone:
            text_index.purge_unused(ws, conn)


def empty(ws: str, *, actor: str = "") -> list[str]:
    """Delete every page of the trash for good; their ids. One connection
    and one read of the trash for the whole pass (``_trashed``), one
    data.db purge after it (``_swept_after``)."""
    with connect_pages_db(ws) as conn, _swept_after(ws, conn) as gone:
        for page_id, deleted_at, went in _trashed(conn):
            if _purge_entry(ws, conn, page_id, deleted_at, went, actor=actor) is not None:
                gone.append(page_id)
    return gone


def purge_expired(ws: str, *, now: datetime | None = None) -> list[str]:
    """Delete for good the pages trashed more than ``KEEP_DAYS`` ago; their
    ids. A stamp that cannot be read counts as expired (the page was deleted
    and cannot be dated)."""
    return _purge_expired(ws, now or datetime.now(timezone.utc))[0]


def _purge_expired(ws: str, now: datetime) -> tuple[list[str], datetime | None]:
    """``purge_expired`` with, from the same listing, when the earliest page
    still in the trash expires: None with the trash left empty; a page that
    could not be dated or purged is due at once (the next sweep tries again).
    One connection and one read of the trash, as ``empty``."""
    expiries = []
    with connect_pages_db(ws) as conn, _swept_after(ws, conn) as gone:
        for page_id, deleted_at, went in _trashed(conn):
            at = parse_stamp(deleted_at)
            if (at is None or now >= at + timedelta(days=KEEP_DAYS)) and _purge_entry(
                    ws, conn, page_id, deleted_at, went, actor="") is not None:
                gone.append(page_id)
            else:
                expiries.append(at + timedelta(days=KEEP_DAYS) if at else datetime.min.replace(tzinfo=timezone.utc))
    return gone, min(expiries, default=None)


# What the last sweep learned per workspace: ``(the files' stamp, when its
# next page expires)`` — a workspace whose files have not changed since is
# left alone until then, instead of being opened every hour (a host with a
# thousand workspaces opened them all, churning the connection cache). The
# stamp covers the WAL file too: in WAL mode a write dates pages.db itself
# only at a checkpoint.
_due: dict[str, tuple[tuple, datetime | None]] = {}


def files_stamp(ws: str) -> tuple | None:
    """``(pages.db mtime, its WAL's mtime and size)``; None without a pages.db."""
    try:
        db = (ws_dir(ws) / "pages.db").stat()
    except OSError:
        return None
    try:
        wal = (ws_dir(ws) / "pages.db-wal").stat()
        return (db.st_mtime_ns, wal.st_mtime_ns, wal.st_size)
    except OSError:
        return (db.st_mtime_ns, 0, 0)


def sweep(*, now: datetime | None = None) -> dict[str, list[str]]:
    """``purge_expired`` over every workspace: ``{workspace: [page ids]}``
    for the ones that lost pages. A workspace that cannot be read (deleted
    meanwhile) is skipped; one whose pages.db has not changed since the
    last sweep is skipped until the expiry that sweep found (``_due``)."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for ws in workspace_ids():
        stamp = files_stamp(ws)
        if stamp is None:
            _due.pop(ws, None)
            continue
        known = _due.get(ws)
        if known and known[0] == stamp and (known[1] is None or now < known[1]):
            continue
        try:
            gone, next_expiry = _purge_expired(ws, now)
            _due[ws] = (files_stamp(ws) or stamp, next_expiry)
        except Exception as e:  # noqa: BLE001 — one workspace never stops the sweep
            _due.pop(ws, None)
            log.warning(f"[trash] workspace {ws}: {e}")
            continue
        if gone:
            out[ws] = gone
            log.info(f"[trash] deleted {len(gone)} page(s) of workspace {ws} "
                     f"after {KEEP_DAYS} days in Recently deleted")
    return out
