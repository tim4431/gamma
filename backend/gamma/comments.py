"""Comments: threads people leave on a note, a page or a highlight
(docs/dev/comments.md).

A thread hangs on one block: a note, a highlight (its block) or the page
itself (its root block). It is its root comment, whose ``thread_id`` is its
own id, and the replies that name it. The rows live in the workspace's
pages.db, table ``comments`` (``db.COMMENTS_SCHEMA``), beside the blocks and
never inside them, so no block reader (exports, the AI context, search,
folder sync, the outliner, the merges, undo) has to skip them.

The writers here take an open pages.db connection, check and write under
the workspace's write lock (``blocks_store.write_lock``) and commit. None of
them touches the page (``touch_page``): a comment moves no change feed,
starts no mirror round and does not reorder the home library. After its
commit the caller hands the write's event to ``after_write``, the one place
the derived work hangs on: the Inbox (gamma/inbox.py).

A comment follows its block (``orphan``, ``place``, ``purge``): the block
deleted by an op marks it orphaned (hidden), the block brought back (an
undo, a revert) or moved to another page carries it along, and the page
deleted for good takes it with it. ``sweep`` deletes the comments orphaned
``ORPHAN_KEEP_DAYS`` ago.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import inbox
from .blocks_store import new_block_id, valid_block_id, write_lock
from .db import account_names, connect_pages_db, connect_users_db, format_stamp, page_now, parse_stamp, workspace_ids
from .logbuf import log

MAX_BODY = 20_000          # characters of one comment
ORPHAN_KEEP_DAYS = 7       # a comment whose block went is deleted this long after
SWEEP_INTERVAL_S = 3600    # the app lifespan runs ``sweep`` at startup and this often
STATES = ("open", "resolved", "all")

# The stored columns, in the table's order.
COLUMNS = ("id, thread_id, block_id, page_id, author, author_name, body, created_at, updated_at, "
           "resolved_at, resolved_by, deleted_at, orphaned_at")
_ROW = ", ".join(f"c.{c}" for c in COLUMNS.split(", "))
_LIVE_PAGE = "SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'"


# --- reading ----------------------------------------------------------------------

def threads_for(conn, page_ids, *, state: str | None = None) -> list[dict]:
    """The threads on the pages ``page_ids`` (the blocks they hold now, the
    page roots included), oldest first, each ``{id, block_id, page_id,
    resolved_at, resolved_by, created_at, last_at, comments: [{id,
    thread_id, author, body, created_at, updated_at, deleted}]}`` with its
    comments oldest first. ``state``: ``open`` (not resolved), ``resolved``,
    or ``all`` / None. A comment whose block was deleted (orphaned) is left
    out; a tombstone (a root deleted while it has replies) comes with
    ``deleted`` true and no body. People are named by username:
    ``author`` falls back to the name stored with the comment once the
    account is gone, ``resolved_by`` to "". The API is its one caller (the
    other readers leave comments out: docs/dev/comments.md "Not built"). The
    caller decides which pages are readable (a page in Recently deleted is
    no page of the library)."""
    if state not in (None, *STATES):
        raise HTTPException(status_code=400, detail="state must be open, resolved or all")
    pages = sorted(set(page_ids))
    if not pages:
        return []
    rows = conn.execute(
        f"SELECT {_ROW}, b.page_id FROM comments c JOIN unified_blocks b ON b.id = c.block_id "
        "WHERE b.page_id IN (SELECT value FROM json_each(?)) AND c.orphaned_at IS NULL "
        "ORDER BY c.created_at, c.id", (json.dumps(pages),)).fetchall()
    return _threads(rows, state)


def thread_of(conn, thread_id: str) -> dict | None:
    """One thread as ``threads_for`` gives it, None when it is gone or hidden."""
    rows = conn.execute(
        f"SELECT {_ROW}, b.page_id FROM comments c JOIN unified_blocks b ON b.id = c.block_id "
        "WHERE c.thread_id = ? AND c.orphaned_at IS NULL ORDER BY c.created_at, c.id", (thread_id,)).fetchall()
    threads = _threads(rows, None)
    return threads[0] if threads else None


def _threads(rows, state: str | None) -> list[dict]:
    """``(*COLUMNS, live page id)`` rows, oldest first, folded into threads."""
    names = _names({r[4] for r in rows} | {r[10] for r in rows if r[10]})
    threads: dict[str, dict] = {}
    for r in rows:
        if r[0] == r[1]:
            threads[r[0]] = {"id": r[0], "block_id": r[2], "page_id": r[13], "resolved_at": r[9],
                             "resolved_by": names.get(r[10] or "", "") if r[9] else None,
                             "created_at": r[7], "last_at": r[9] or r[7], "comments": []}
    for r in rows:
        thread = threads.get(r[1])
        if thread is None:
            continue  # a reply without its root: never written so, passed by
        thread["comments"].append(_comment(r, names))
        thread["last_at"] = max(thread["last_at"], r[7], r[8])
    return [t for t in threads.values()
            if state in (None, "all") or (state == "resolved") == bool(t["resolved_at"])]


def _comment(row, names: dict) -> dict:
    deleted = bool(row[11])
    return {"id": row[0], "thread_id": row[1], "author": names.get(row[4]) or row[5],
            "body": "" if deleted else row[6], "created_at": row[7], "updated_at": row[8], "deleted": deleted}


def _names(user_ids) -> dict[str, str]:
    with connect_users_db() as users:
        return account_names(users, user_ids)


def _located(conn, comment_id: str):
    """The comment's row (``COLUMNS``) and the page of the library its block
    is on now; 404 when there is no such comment, its block went (it is
    orphaned) or its page is in Recently deleted."""
    row = conn.execute(
        f"SELECT {_ROW}, b.page_id FROM comments c JOIN unified_blocks b ON b.id = c.block_id "
        "JOIN unified_blocks p ON p.id = b.page_id "
        "WHERE c.id = ? AND c.orphaned_at IS NULL AND p.parent_id = 'root'", (comment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="comment not found")
    return row


def _on_page(conn, comment_id: str, page_id: str):
    """``_located``'s row, 404 as well when the comment is no longer on
    ``page_id``: the page its write was authorized against, read before the
    write's lock."""
    row = _located(conn, comment_id)
    if row[13] != page_id:
        raise HTTPException(status_code=404, detail="comment not found")
    return row


def page_of(conn, comment_id: str) -> str:
    """The page a comment is on now (what its write is authorized against);
    404 as for a hidden comment."""
    return _located(conn, comment_id)[13]


# --- writing -----------------------------------------------------------------------

def _clean_body(body) -> str:
    if not isinstance(body, str) or not body.strip():
        raise HTTPException(status_code=400, detail="a comment needs text")
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=413, detail=f"a comment holds at most {MAX_BODY} characters")
    return body


def create(conn, page_id: str, *, block_id: str, body: str, author: str, author_name: str,
           comment_id: str = "", thread_id: str = "") -> dict:
    """A new thread on ``block_id`` (a block of the page, or the page
    itself), or with ``thread_id`` a reply in that thread (its root's id;
    the block must be the thread's). ``comment_id``: the client's id for the
    comment (a block id's shape), minted here when absent; a comment of that
    id by the same author on this page is answered as stored, so a retry
    writes nothing twice. Returns ``{comment, thread, created, event}`` —
    ``created`` false for such a retry, ``event`` for ``after_write``.
    Commits. 404 unless the page is a page of the library, and for a retry
    of a comment hidden since; 400 for a block outside it, a thread that is
    no root of it, an id of the wrong shape or an empty text; 413 past
    ``MAX_BODY``; 409 for an id another author's comment has, or one of a
    comment on another page (the caller authorized this page only)."""
    body = _clean_body(body)
    if comment_id and not valid_block_id(comment_id):
        raise HTTPException(status_code=400, detail=f"invalid comment id: {comment_id!r}")
    write_lock(conn)
    try:
        if comment_id:
            row = conn.execute("SELECT author, thread_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
            if row:
                if row[0] != author or _located(conn, comment_id)[13] != page_id:
                    raise HTTPException(status_code=409, detail="that comment id is taken")
                conn.rollback()
                thread = thread_of(conn, row[1])
                comment = next((c for c in (thread or {}).get("comments", []) if c["id"] == comment_id), None)
                if comment is None:
                    raise HTTPException(status_code=404, detail="comment not found")
                return {"comment": comment, "thread": thread, "created": False, "event": None}
        if not conn.execute(_LIVE_PAGE, (page_id,)).fetchone():
            raise HTTPException(status_code=404, detail="page not found")
        on = conn.execute("SELECT page_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()
        if not on or on[0] != page_id:
            raise HTTPException(status_code=400, detail="that block is not in this page")
        if thread_id:
            root = conn.execute(
                "SELECT block_id FROM comments WHERE id = ? AND thread_id = id AND orphaned_at IS NULL",
                (thread_id,)).fetchone()
            if not root or root[0] != block_id:
                raise HTTPException(status_code=400, detail="that thread is not on this block")
        comment_id = comment_id or new_block_id()
        thread_id = thread_id or comment_id
        now = page_now()
        conn.execute(f"INSERT INTO comments ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                     (comment_id, thread_id, block_id, page_id, author, author_name or "", body, now, now))
        thread = thread_of(conn, thread_id)  # under the lock: no batch can hide its block before the answer
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    comment = next(c for c in thread["comments"] if c["id"] == comment_id)
    kind = "reply" if thread_id != comment_id else "create"
    return {"comment": comment, "thread": thread, "created": True,
            "event": _event(kind, author, page_id, block_id, thread_id, comment_id, body=body)}


def edit(conn, comment_id: str, *, page_id: str, body: str, author: str) -> dict:
    """Change a comment's text, its author only (403 otherwise; 404 for a
    hidden comment, a tombstone or a comment no longer on ``page_id``, the
    page the caller authorized). ``updated_at`` moves only when the text
    changed. Returns ``{comment, thread, event}`` (``event`` None when
    nothing changed). Commits."""
    body = _clean_body(body)
    write_lock(conn)
    try:
        row = _on_page(conn, comment_id, page_id)
        if row[11]:
            raise HTTPException(status_code=404, detail="comment not found")
        if row[4] != author:
            raise HTTPException(status_code=403, detail="only its author can edit a comment")
        event = None
        if body != row[6]:
            conn.execute("UPDATE comments SET body = ?, updated_at = ? WHERE id = ?", (body, page_now(), comment_id))
            event = _event("edit", author, row[13], row[2], row[1], comment_id, body=body, before=row[6])
        thread = thread_of(conn, row[1])  # under the lock, as in ``create``
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"comment": next(c for c in thread["comments"] if c["id"] == comment_id), "thread": thread,
            "event": event}


def delete(conn, comment_id: str, *, page_id: str, user: str, owner: bool = False) -> dict:
    """Delete a comment on ``page_id`` (the page the caller authorized; 404
    for a hidden comment or one that moved off it): its author's, or any one
    for the workspace's owner (``owner``; 403 otherwise). A root that still has replies becomes a
    tombstone (its text goes, the thread stays); a reply that was the last
    one under a tombstone takes the tombstone with it. Returns
    ``{thread_id, block_id, page_id, removed: [ids], thread (None once it
    is gone), event}``. Commits."""
    write_lock(conn)
    try:
        row = _on_page(conn, comment_id, page_id)
        if row[11]:
            raise HTTPException(status_code=404, detail="comment not found")
        if row[4] != user and not owner:
            raise HTTPException(status_code=403, detail="only its author can delete a comment")
        thread_id = row[1]
        replies = conn.execute("SELECT COUNT(*) FROM comments WHERE thread_id = ? AND id != thread_id",
                               (thread_id,)).fetchone()[0]
        removed = []
        if comment_id == thread_id and replies:
            conn.execute("UPDATE comments SET body = '', deleted_at = ? WHERE id = ?", (page_now(), comment_id))
        else:
            conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            removed.append(comment_id)
            if comment_id != thread_id and replies == 1 and conn.execute(
                    "SELECT 1 FROM comments WHERE id = ? AND deleted_at IS NOT NULL", (thread_id,)).fetchone():
                conn.execute("DELETE FROM comments WHERE id = ?", (thread_id,))
                removed.append(thread_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"thread_id": thread_id, "block_id": row[2], "page_id": row[13], "removed": removed,
            "thread": thread_of(conn, thread_id),
            "event": _event("delete", user, row[13], row[2], thread_id, comment_id, comment_ids=[comment_id])}


def resolve(conn, comment_id: str, *, page_id: str, resolved: bool, by: str) -> dict:
    """Resolve a thread (``resolved``) or reopen it; ``comment_id`` is its
    root (400 for a reply), on ``page_id`` (the page the caller authorized;
    404 for a hidden thread or one that moved off it). Resolving a resolved thread keeps who and when.
    Returns ``{thread, event}`` (``event`` None when nothing changed).
    Commits."""
    write_lock(conn)
    try:
        row = _on_page(conn, comment_id, page_id)
        if row[0] != row[1]:
            raise HTTPException(status_code=400, detail="only a thread's first comment resolves it")
        event = None
        if resolved != bool(row[9]):
            if resolved:
                conn.execute("UPDATE comments SET resolved_at = ?, resolved_by = ? WHERE id = ?",
                             (page_now(), by, comment_id))
            else:
                conn.execute("UPDATE comments SET resolved_at = NULL, resolved_by = NULL WHERE id = ?", (comment_id,))
            event = _event("resolve" if resolved else "reopen", by, row[13], row[2], comment_id, comment_id,
                           thread_author=row[4])
        thread = thread_of(conn, comment_id)  # under the lock, as in ``create``
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"thread": thread, "event": event}


def _event(kind: str, actor: str, page_id: str, block_id: str, thread_id: str, comment_id: str, **more) -> dict:
    return {"kind": kind, "actor": actor, "page_id": page_id, "block_id": block_id,
            "thread_id": thread_id, "comment_id": comment_id, **more}


def after_write(ws: str, event: dict | None) -> None:
    """What follows a committed comment write, called by every writer once
    its transaction is in: the Inbox (``inbox.from_comment``: the people a
    comment mentions, the thread's others on a reply, the thread's author
    on a resolve; a deleted comment's items go). ``event`` is ``{kind,
    actor, page_id, block_id, thread_id, comment_id, ...}``, ``kind`` one
    of ``create`` (a new thread; ``body``), ``reply`` (``body``), ``edit``
    (``body``, ``before``), ``delete`` (``comment_ids``), ``resolve`` /
    ``reopen`` (``thread_author``) and ``sweep`` (``comment_ids``, the
    orphans ``sweep`` deleted; no page or thread). None: nothing changed.
    Never raises: a stored comment never answers an error because what
    follows it failed."""
    if not event:
        return
    try:
        inbox.from_comment(ws, event)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[comments] what follows a {event.get('kind')} in workspace {ws} failed: {e}")


# --- following the blocks ---------------------------------------------------------
# Called inside the block writers' transactions (gamma/ops.py, the subtree
# replace, the backup merge, create_page); each changes rows only, commits
# nothing.

def orphan(conn, block_ids, now: str = "") -> None:
    """The blocks ``block_ids`` were deleted: their comments are hidden from
    now on (``orphaned_at``), and ``sweep`` deletes them unless the blocks
    come back first (``place``)."""
    ids = list(block_ids)
    if ids:
        conn.execute("UPDATE comments SET orphaned_at = ? WHERE orphaned_at IS NULL "
                     "AND block_id IN (SELECT value FROM json_each(?))", (now or page_now(), json.dumps(ids)))


def place(conn, block_ids, page_id: str, *, any_page: bool = True) -> None:
    """The blocks ``block_ids`` live on ``page_id`` now (inserted, brought
    back under their own ids, moved to another page): their comments follow
    (``page_id``) and are shown again (``orphaned_at`` cleared).
    ``any_page`` false (a writer a share confines to its pages): only the
    comments last placed on ``page_id`` come back, so an id taken from a
    block deleted on another page never shows that page's threads."""
    ids = list(block_ids)
    if ids:
        conn.execute("UPDATE comments SET page_id = ?, orphaned_at = NULL "
                     "WHERE block_id IN (SELECT value FROM json_each(?)) "
                     "AND (page_id != ? OR orphaned_at IS NOT NULL)" + ("" if any_page else " AND page_id = ?"),
                     (page_id, json.dumps(ids), page_id, *(() if any_page else (page_id,))))


def purge(conn, page_id: str, block_ids) -> int:
    """The page ``page_id`` is deleted for good (or gives way to another
    copy of it), with the blocks ``block_ids``: their comments go, and so
    do the page's orphaned ones whose block is gone. A thread on a block
    that lives on another page now is left alone. Run after the blocks'
    delete. Returns how many went."""
    return conn.execute(
        "DELETE FROM comments WHERE block_id IN (SELECT value FROM json_each(?)) "
        "OR (page_id = ? AND NOT EXISTS (SELECT 1 FROM unified_blocks b WHERE b.id = comments.block_id))",
        (json.dumps(list(block_ids)), page_id)).rowcount


# --- copies ---------------------------------------------------------------------------

def rows_of_pages(conn, page_ids) -> list[tuple]:
    """The comments shown on the pages ``page_ids`` as stored rows
    (``COLUMNS``), ``page_id`` the page each block is on now, roots before
    their replies: what a copy of those pages carries (a Gamma export, a
    backup merge)."""
    pages = sorted(set(page_ids))
    if not pages:
        return []
    return conn.execute(
        f"SELECT {_ROW.replace('c.page_id', 'b.page_id')} FROM comments c "
        "JOIN unified_blocks b ON b.id = c.block_id "
        "WHERE b.page_id IN (SELECT value FROM json_each(?)) AND c.orphaned_at IS NULL "
        "ORDER BY c.id != c.thread_id, c.created_at, c.id", (json.dumps(pages),)).fetchall()


def copy_rows(dst, rows, *, blocks: dict | None = None) -> int:
    """Insert comment ``rows`` (``COLUMNS``, roots first) into the pages.db
    ``dst`` inside its caller's transaction. ``blocks`` maps the rows'
    block ids to the copy's (a backup merge's fresh ids); a row whose block
    is not in it is left out. A comment id ``dst`` already has is minted
    afresh, and its thread's replies follow it; a reply whose root was left
    out goes too. Returns how many were added."""
    ids: dict[str, str] = {}
    minted: set[str] = set()
    added = 0
    for row in rows:
        vals = list(row)
        if blocks is not None:
            if vals[2] not in blocks:
                continue
            vals[2] = blocks[vals[2]]
        root = vals[0] == vals[1]
        if not root and vals[1] not in ids:
            continue
        fresh = vals[0]
        while fresh in minted or dst.execute("SELECT 1 FROM comments WHERE id = ?", (fresh,)).fetchone():
            fresh = new_block_id()
        minted.add(fresh)
        ids[vals[0]] = fresh
        vals[0] = fresh
        vals[1] = fresh if root else ids[vals[1]]
        dst.execute(f"INSERT INTO comments ({COLUMNS}) VALUES ({', '.join('?' * len(vals))})", vals)
        added += 1
    return added


def count_on(conn, block_ids) -> int:
    """How many comments (tombstones left out) the blocks ``block_ids``
    show: what a backup's review says a page brings along."""
    ids = list(block_ids)
    if not ids:
        return 0
    return conn.execute("SELECT COUNT(*) FROM comments WHERE block_id IN (SELECT value FROM json_each(?)) "
                        "AND orphaned_at IS NULL AND deleted_at IS NULL", (json.dumps(ids),)).fetchone()[0]


# --- the sweep --------------------------------------------------------------------------

# What the last sweep learned per workspace: ``(the files' stamp, when its
# next orphan expires)``, so a workspace whose files have not changed since
# is left unopened until then (as trash.sweep does).
_due: dict[str, tuple[tuple, datetime | None]] = {}


def sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Delete, in every workspace, the comments orphaned more than
    ``ORPHAN_KEEP_DAYS`` ago (their block was deleted and never came back):
    ``{workspace: how many}`` for the ones that lost some. Each workspace's
    deletion is handed to ``after_write`` as a ``sweep`` event. A workspace
    that cannot be read is skipped; one whose pages.db has not changed since
    the last sweep is skipped until the expiry that sweep found."""
    from .trash import files_stamp  # local: trash imports ops, which imports this module

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
            gone, next_expiry = _sweep(ws, now)
            _due[ws] = (files_stamp(ws) or stamp, next_expiry)
        except Exception as e:  # noqa: BLE001 — one workspace never stops the sweep
            _due.pop(ws, None)
            log.warning(f"[comments] sweep of workspace {ws}: {e}")
            continue
        if gone:
            out[ws] = len(gone)
            log.info(f"[comments] deleted {len(gone)} comment(s) of workspace {ws} "
                     f"{ORPHAN_KEEP_DAYS} days after their block went")
            after_write(ws, {"kind": "sweep", "actor": "", "page_id": "", "block_id": "", "thread_id": "",
                             "comment_id": "", "comment_ids": gone})
    return out


def _sweep(ws: str, now: datetime) -> tuple[list[str], datetime | None]:
    cutoff = format_stamp(now - timedelta(days=ORPHAN_KEEP_DAYS))
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        try:
            gone = [r[0] for r in conn.execute(
                "SELECT id FROM comments WHERE orphaned_at IS NOT NULL AND orphaned_at <= ?", (cutoff,))]
            if gone:
                conn.execute("DELETE FROM comments WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(gone),))
            oldest = conn.execute("SELECT MIN(orphaned_at) FROM comments WHERE orphaned_at IS NOT NULL").fetchone()[0]
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    at = parse_stamp(oldest) if oldest else None
    return gone, (at + timedelta(days=ORPHAN_KEEP_DAYS) if at else None)
