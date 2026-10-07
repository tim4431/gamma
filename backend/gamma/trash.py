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
"""

from datetime import datetime, timedelta, timezone

from .auth import actor_names
from .blocks_store import TRASH, trashed_page, write_lock
from .db import connect_pages_db, format_stamp, parse_stamp, workspace_ids, ws_dir
from .logbuf import log
from .ops import delete_page

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
    folders, deleted_at, deleted_by, purge_at}`` (``named``; ``folders``
    the ids of the folders it was filed in)."""
    pages = named([trashed_page(*row) for row in conn.execute(
        "SELECT id, content, properties FROM unified_blocks WHERE parent_id = ?", (TRASH,))])
    for page in pages:
        page["purge_at"] = purge_at(page["deleted_at"])
    return sorted(pages, key=lambda p: p["deleted_at"], reverse=True)


def purge(ws: str, page_id: str, *, actor: str = "") -> dict | None:
    """Delete one page of the trash for good (``ops.delete_page``); None
    when ``page_id`` is not in it. The check and the delete hold one write
    lock, so a restore in between never loses the page it restored."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
        if not row or row[0] != TRASH:
            conn.rollback()
            return None
        return delete_page(ws, conn, page_id, actor=actor)


def _trashed(ws: str) -> list[dict]:
    with connect_pages_db(ws) as conn:
        return list_trash(conn)


def empty(ws: str, *, actor: str = "") -> list[str]:
    """Delete every page of the trash for good; their ids."""
    return [p["id"] for p in _trashed(ws) if purge(ws, p["id"], actor=actor) is not None]


def purge_expired(ws: str, *, now: datetime | None = None) -> list[str]:
    """Delete for good the pages trashed more than ``KEEP_DAYS`` ago; their
    ids. A stamp that cannot be read counts as expired (the page was deleted
    and cannot be dated)."""
    now = now or datetime.now(timezone.utc)
    gone = []
    for page in _trashed(ws):
        at = parse_stamp(page["deleted_at"])
        if (at is None or now >= at + timedelta(days=KEEP_DAYS)) and purge(ws, page["id"]) is not None:
            gone.append(page["id"])
    return gone


# What the last sweep learned per workspace: ``(the files' stamp, when its
# next page expires)`` — a workspace whose files have not changed since is
# left alone until then, instead of being opened every hour (a host with a
# thousand workspaces opened them all, churning the connection cache). The
# stamp covers the WAL file too: in WAL mode a write dates pages.db itself
# only at a checkpoint.
_due: dict[str, tuple[tuple, datetime | None]] = {}


def _stamp(ws: str) -> tuple | None:
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


def _next_expiry(ws: str) -> datetime | None:
    """When the earliest page still in the workspace's trash expires; None
    with the trash empty (or an undatable entry, which the next sweep purges)."""
    stamps = [parse_stamp(page["deleted_at"]) for page in _trashed(ws)]
    if not stamps:
        return None
    return min((at + timedelta(days=KEEP_DAYS) if at else datetime.min.replace(tzinfo=timezone.utc))
               for at in stamps)


def sweep(*, now: datetime | None = None) -> dict[str, list[str]]:
    """``purge_expired`` over every workspace: ``{workspace: [page ids]}``
    for the ones that lost pages. A workspace that cannot be read (deleted
    meanwhile) is skipped; one whose pages.db has not changed since the
    last sweep is skipped until the expiry that sweep found (``_due``)."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for ws in workspace_ids():
        stamp = _stamp(ws)
        if stamp is None:
            _due.pop(ws, None)
            continue
        known = _due.get(ws)
        if known and known[0] == stamp and (known[1] is None or now < known[1]):
            continue
        try:
            gone = purge_expired(ws, now=now)
            _due[ws] = (_stamp(ws) or stamp, _next_expiry(ws))
        except Exception as e:  # noqa: BLE001 — one workspace never stops the sweep
            _due.pop(ws, None)
            log.warning(f"[trash] workspace {ws}: {e}")
            continue
        if gone:
            out[ws] = gone
            log.info(f"[trash] deleted {len(gone)} page(s) of workspace {ws} "
                     f"after {KEEP_DAYS} days in Recently deleted")
    return out
