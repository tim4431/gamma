"""The Inbox: what concerns an account, across every workspace and share it
can reach (docs/dev/comments.md "The inbox").

One row of users.db ``inbox_items`` per event and recipient, written when
the event happens: someone mentioned in a note (``mention``) or in a
comment (``comment_mention``), a reply in a thread they are in (``reply``),
their thread resolved by someone else (``resolved``), a page or folder
shared with them (``shared``), added to a shared workspace (``invited``),
someone without an account here asking to join through a share they
manage (``join_request``: its actor is ``cloud:<subject>``, its snippet the
cloud username; gamma/join_requests.py).
A notes-index scan could find a block that says ``@alice``, but not who
typed it or when, and would notify again on every edit; so the producers
write the row, after their own commit, through ``emit``, which never
raises: a stored note or comment never answers an error because users.db
was busy.

Who an item may go to is ``reach``: a member of the workspace, or an
account that a share naming it, or the share the event came through,
admits to the page (the item then carries that share's token, the way
in). The listing (``listing``) settles the items
that no longer lead anywhere, as the reminders' listing settles its marks
(gamma/mentions.py ``_settle``): the one read that writes. After a write
each recipient's open page sockets hear a content-free ``{t: "inbox"}``
(``collab.publish_account``); ``unread`` is what a tab without one polls.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from . import collab, join_requests, mentions, workspaces
from .auth import (LINK_ACTOR_PREFIX, MIRROR_ACTOR, ZOTERO_ACTOR, ZOTERO_CLIENT, ShareScope, share_access,
                   share_lookup)
from .blocks_store import new_block_id
from .db import account_names, connect_pages_db, connect_users_db, format_stamp, page_now
from .logbuf import log

FILTERS = ("unread", "all", "archived")
MAX_RECIPIENTS = 25        # accounts one event notifies at most
REACH_CANDIDATES = 4 * MAX_RECIPIENTS  # accounts ``reach`` checks for one event, to fill those 25
SNIPPET_CHARS = 200        # of the text at the time, kept with the item
PAGE_SIZE = 50             # items a listing answers by default
MAX_PAGE = 100             # and at most
MAX_MARK = 100             # ids one mark names
SWEEP_INTERVAL_S = 86_400  # the app lifespan runs ``sweep`` at startup and daily
READ_KEEP_DAYS = 90        # a read item goes this long after it happened
ARCHIVED_KEEP_DAYS = 30    # an archived one this long after it was archived
UNREAD_KEEP_DAYS = 180     # an unread one is archived this long after it happened
NUDGE = {"t": "inbox"}     # what the recipient's page sockets hear: no content
SYNC_CLIENT = "sync"       # sync_engine.CLIENT: an offline copy's own writes, typed elsewhere

_COLUMNS = ("id, kind, actor, workspace_id, page_id, block_id, thread_id, comment_id, share_token, snippet, "
            "at, read_at, archived_at")
_INSERT = ("id, user_id, kind, actor, workspace_id, page_id, block_id, thread_id, comment_id, share_token, "
           "snippet, at")
_WHERE = {"unread": "read_at IS NULL AND archived_at IS NULL", "all": "archived_at IS NULL",
          "archived": "archived_at IS NOT NULL"}


# --- who may hear of it ------------------------------------------------------------

def _accounts(user_ids) -> dict[str, bool]:
    """``{id: is_guest}`` for the accounts among ``user_ids`` that exist."""
    wanted = sorted({u for u in user_ids if u})
    if not wanted:
        return {}
    with connect_users_db() as conn:
        return {r[0]: bool(r[1]) for r in conn.execute(
            "SELECT id, is_guest FROM users WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(wanted),))}


def _ids_of(usernames) -> dict[str, str]:
    """``{username: id}`` for the accounts named among ``usernames``."""
    wanted = sorted({n for n in usernames if n})
    if not wanted:
        return {}
    with connect_users_db() as conn:
        return dict(conn.execute("SELECT username, id FROM users WHERE username IN (SELECT value FROM json_each(?))",
                                 (json.dumps(wanted),)).fetchall())


def _shares_to(ws: str, page_id: str) -> list[dict]:
    """The shares of the workspace whose scope reaches the page now: its
    page share first, then the folder shares holding it."""
    with connect_users_db() as conn:
        tokens = [r[0] for r in conn.execute(
            "SELECT token FROM shares WHERE workspace_id = ? AND (page_id = ? OR folder != '') "
            "ORDER BY folder != '', token", (ws, page_id))]
    shares = [s for s in map(share_lookup, tokens) if s]
    if not shares:
        return []
    with connect_pages_db(ws) as conn:
        return [s for s in shares if ShareScope.of(s).allows_page(conn, page_id)]


def reach(ws: str, page_id: str, user_ids, via: str = "") -> dict[str, str]:
    """``{user_id: share_token | ""}`` for the accounts among ``user_ids``
    that can open the page: ``""`` for one with a role in the workspace
    (``workspaces.role_of``: a member, or anyone a public workspace lets
    in), else the token of a share of the workspace whose scope reaches the
    page and whose audience admits the account (``auth.share_access``),
    when that share names the account among its invitees or is ``via``,
    the share the event came through. Any other share is never handed out:
    an anyone-with-the-link or any-signed-in-account share the account was
    never given may grant more than the actor's own way in. The others are
    left out. Without ``page_id`` (an invitation, a folder share) the role
    counts, and ``via`` when its audience admits the account."""
    ids = sorted({u for u in user_ids if u})
    out: dict[str, str] = {}
    rest = []
    for uid in ids:
        if workspaces.role_of(ws, uid):
            out[uid] = ""
        else:
            rest.append(uid)
    if not rest or not (page_id or via):
        return out
    known = _accounts(rest)
    if not known:
        return out
    if page_id:
        shares = _shares_to(ws, page_id)
    else:
        share = share_lookup(via)
        shares = [share] if share and share["workspace_id"] == ws else []
    for uid in rest:
        if uid not in known:
            continue
        for share in shares:
            named = any(u["user_id"] == uid for u in share["users"])
            if (named or share["token"] == via) and share_access(share, uid, known[uid])[0]:
                out[uid] = share["token"]
                break
    return out


# --- writing -----------------------------------------------------------------------

def _snippet(text: str) -> str:
    return " ".join((text or "").split())[:SNIPPET_CHARS]


_REF_RE = re.compile(r"!?\[\[([A-Za-z0-9_-]{1,64})\]\]")
_REF_NAME_CHARS = 80


def _named_refs(ws: str, text: str) -> str:
    """The text with its [[id]] links as the words they show: a page's
    title, a note's first line, read from the workspace's pages.db when the
    item is written, so the Inbox names what a comment or note links to
    rather than its id. A link to nothing it finds, or a read that fails,
    leaves the text as it is."""
    ids = list(dict.fromkeys(_REF_RE.findall(text or "")))[:50]
    if not ids:
        return text
    try:
        with connect_pages_db(ws) as conn:
            rows = conn.execute(
                "SELECT id, content FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),)).fetchall()
    except Exception as e:  # noqa: BLE001 — a name is a nicety; the item still goes out
        log.warning(f"[inbox] the links of an item in workspace {ws} could not be named: {e}")
        return text
    names = {}
    for bid, content in rows:
        first = " ".join((content or "").split("\n", 1)[0].split())
        if first:
            names[bid] = first[:_REF_NAME_CHARS]
    return _REF_RE.sub(lambda m: names.get(m.group(1), m.group(0)), text)


def emit(kind: str, recipients, actor: str, ws: str, *, page_id: str = "", block_id: str = "",
         thread_id: str = "", comment_id: str = "", snippet: str = "", via: str = "") -> list[str]:
    """Write one ``kind`` item for each of ``recipients`` (account ids) who
    may hear of it, then nudge their open page sockets; the ids written.
    Run after the producer's own commit. The actor, guests and accounts
    that are gone are left out; of the first ``REACH_CANDIDATES`` of the
    rest, the first ``MAX_RECIPIENTS`` whom ``reach`` admits to the page
    are told. ``via``: the token of the share the event came through (the
    actor's share link, a share's own token for its invitees), which
    ``reach`` hands to a recipient its audience admits. A ``mention`` of someone who has the same block's mention
    unread and not archived writes nothing more while it stays so (a name
    typed again; an undo of the block's deletion before the recipient's
    listing settled the item). The check and the rows go in one users.db
    write transaction. Never raises: an error is logged and no one is
    told."""
    try:
        return _emit(kind, recipients, actor, ws, page_id=page_id, block_id=block_id, thread_id=thread_id,
                     comment_id=comment_id, snippet=snippet, via=via)
    except Exception as e:  # noqa: BLE001 — what follows a write never fails it
        log.warning(f"[inbox] a {kind} item in workspace {ws} could not be written: {e}")
        return []


def _emit(kind, recipients, actor, ws, *, page_id, block_id, thread_id, comment_id, snippet, via) -> list[str]:
    ids = [u for u in dict.fromkeys(recipients) if u and u != actor]
    if not ids:
        return []
    known = _accounts(ids)
    ids = [u for u in ids if u in known and not known[u]][:REACH_CANDIDATES]
    reached = reach(ws, page_id, ids, via) if ids else {}
    now, text = page_now(), _snippet(_named_refs(ws, snippet))
    rows = []
    for uid in ids:
        token = reached.get(uid)
        if token is not None:
            rows.append((new_block_id(), uid, kind, actor, ws, page_id, block_id, thread_id, comment_id, token,
                         text, now))
    rows = rows[:MAX_RECIPIENTS]
    if not rows:
        return []
    with connect_users_db() as conn:
        if kind == "mention":
            conn.execute("BEGIN IMMEDIATE")  # the unread check and the write as one step
            told = {r[0] for r in conn.execute(
                "SELECT user_id FROM inbox_items WHERE user_id IN (SELECT value FROM json_each(?)) "
                "AND read_at IS NULL AND archived_at IS NULL AND kind = 'mention' AND workspace_id = ? "
                "AND block_id = ?", (json.dumps([r[1] for r in rows]), ws, block_id))}
            rows = [r for r in rows if r[1] not in told]
        conn.executemany(f"INSERT INTO inbox_items ({_INSERT}) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    for row in rows:
        collab.publish_account(row[1], NUDGE)
    return [r[1] for r in rows]


def from_ops(ws: str, result: dict, via: str = "") -> None:
    """The Inbox side of a committed batch (``ops.apply_ops``' result). The
    batch is diffed whole, so a name that moves from one note to another
    (Enter before ``@name``, a join) tells no one again:
    ``mentions_added`` (``{block_id, users, text}``: a block whose text
    gained a person no other text of the batch lost) writes ``mention``
    items; ``mentions_moved`` (``{user, from, to}``: a person some texts of
    the batch lost and another gained) moves the person's items on the
    ``from`` blocks to ``to``; ``mentions_dropped`` (``{block_id,
    users}``: a text that names a person no more, whom no other text of
    the batch gained, as a half-typed ``@ben`` saved on its way to
    ``@benjamin``) archives their item on that block. ``via``: the share
    the writer came through. A batch an offline copy wrote (client
    ``sync``: the names were typed on the other server), a mirror's round,
    a Zotero link's writes (client or actor ``zotero``: typed in Zotero, or
    a conflict's "theirs" taken from there) and a link visitor (no account
    to name) write no item, though their moves and drops are followed; the
    AI's batch is its chat owner's. Never raises."""
    try:
        _follow(ws, result.get("mentions_moved") or [], result.get("mentions_dropped") or [])
    except Exception as e:  # noqa: BLE001
        log.warning(f"[inbox] the moved mentions of a batch in workspace {ws} were not followed: {e}")
    found, actor = result.get("mentions_added"), result.get("actor") or ""
    if (not found or not actor or result.get("client") in (SYNC_CLIENT, ZOTERO_CLIENT)
            or actor in (MIRROR_ACTOR, ZOTERO_ACTOR) or actor.startswith(LINK_ACTOR_PREFIX)):
        return
    try:
        ids = _ids_of({u for f in found for u in f["users"]})
    except Exception as e:  # noqa: BLE001
        log.warning(f"[inbox] the mentions of a batch in workspace {ws} were not read: {e}")
        return
    for f in found:
        emit("mention", [ids[u] for u in f["users"] if u in ids], actor, ws, page_id=result["page_id"],
             block_id=f["block_id"], snippet=f["text"], via=via)


def _follow(ws: str, moved: list, dropped: list) -> None:
    """``from_ops``' moves and drops: the items not archived follow their
    name to its new block, or are archived; the accounts whose count
    changed hear a nudge."""
    if not (moved or dropped):
        return
    ids = _ids_of({m["user"] for m in moved} | {u for d in dropped for u in d["users"]})
    if not ids:
        return
    told = set()
    now = page_now()
    with connect_users_db() as conn:
        for m in moved:
            if m["user"] in ids:
                conn.execute("UPDATE inbox_items SET block_id = ? WHERE user_id = ? AND workspace_id = ? "
                             "AND kind = 'mention' AND archived_at IS NULL AND block_id IN "
                             "(SELECT value FROM json_each(?))", (m["to"], ids[m["user"]], ws, json.dumps(m["from"])))
        for d in dropped:
            for user in d["users"]:
                if user in ids and conn.execute(
                        "UPDATE inbox_items SET archived_at = ? WHERE user_id = ? AND workspace_id = ? "
                        "AND kind = 'mention' AND archived_at IS NULL AND block_id = ?",
                        (now, ids[user], ws, d["block_id"])).rowcount:
                    told.add(ids[user])
    for uid in sorted(told):
        collab.publish_account(uid, NUDGE)


def from_comment(ws: str, event: dict) -> None:
    """What a committed comment write (``comments.after_write``'s event)
    tells: ``comment_mention`` to each person a comment's text gains,
    ``reply`` to the thread's other authors and the people it mentions
    (less those just mentioned), ``resolved`` to the author of a thread
    someone else resolved. A deleted comment's items go with it. Never
    raises."""
    try:
        _from_comment(ws, event)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[inbox] a comment's {event.get('kind')} in workspace {ws} told no one: {e}")


def _from_comment(ws: str, event: dict) -> None:
    kind, actor = event["kind"], event["actor"]
    if kind in ("delete", "sweep"):
        forget(ws, event.get("comment_ids") or [])
        return
    where = {"page_id": event["page_id"], "block_id": event["block_id"], "thread_id": event["thread_id"],
             "comment_id": event["comment_id"], "via": event.get("via") or ""}
    if kind == "resolve":
        if event.get("thread_author") and event["thread_author"] != actor:
            with connect_pages_db(ws) as conn:
                root = conn.execute("SELECT body FROM comments WHERE id = ?", (event["thread_id"],)).fetchone()
            emit("resolved", [event["thread_author"]], actor, ws, snippet=root[0] if root else "", **where)
        return
    if kind not in ("create", "reply", "edit"):
        return  # reopen tells no one
    body = event.get("body") or ""
    named = mentions.people_in(body) - (mentions.people_in(event.get("before") or "") if kind == "edit" else set())
    ids = _ids_of(named)
    mentioned = set(ids.values())
    if mentioned:
        emit("comment_mention", sorted(mentioned), actor, ws, snippet=body, **where)
    if kind != "reply":
        return
    with connect_pages_db(ws) as conn:
        others = conn.execute("SELECT author, body FROM comments WHERE thread_id = ? AND id != ? "
                              "ORDER BY created_at, id", (event["thread_id"], event["comment_id"])).fetchall()
    in_thread = set()
    for _author, text in others:
        in_thread |= mentions.people_in(text)
    people = [a for a, _ in others] + sorted(_ids_of(in_thread).values())
    emit("reply", [p for p in dict.fromkeys(people) if p not in mentioned], actor, ws, snippet=body, **where)


def forget(ws: str, comment_ids) -> None:
    """The items of the comments ``comment_ids`` of the workspace go (the
    comments were deleted, or swept)."""
    ids = sorted(set(comment_ids))
    if ids:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM inbox_items WHERE workspace_id = ? AND comment_id IN "
                         "(SELECT value FROM json_each(?))", (ws, json.dumps(ids)))


# --- reading -----------------------------------------------------------------------

def unread(user_id: str) -> int:
    """How many of the account's items are unread (and not archived): a
    count on the partial index, what the bell shows."""
    with connect_users_db() as conn:
        return _unread(conn, user_id)


def _unread(conn, user_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM inbox_items WHERE user_id = ? AND read_at IS NULL "
                        "AND archived_at IS NULL", (user_id,)).fetchone()[0]


def listing(user_id: str, *, filter: str = "unread", before: str = "", limit: int = PAGE_SIZE) -> dict:
    """``{items, unread, more}``: the account's items, newest first —
    ``filter`` ``unread``, ``all`` (not archived) or ``archived`` —
    happened before ``before`` (an ``at`` the last listing ended on), at
    most ``limit`` but never splitting the items of one moment across two
    listings. Each is ``{id, kind, actor (a username, "" once gone),
    workspace_id, workspace_name, page_id, page_title (a folder share: the
    folder's name), block_id, thread_id, comment_id, share_token, snippet,
    at, read_at, archived_at}``. The items that lead nowhere any more are
    archived first (``_settle``), so ``unread`` and the Unread tab agree.
    ValueError for another ``filter``."""
    if filter not in FILTERS:
        raise ValueError("filter must be unread, all or archived")
    limit = max(1, min(int(limit), MAX_PAGE))
    alive = _settle(user_id)
    where, args = f"user_id = ? AND {_WHERE[filter]}", [user_id]
    if before:
        where += " AND at < ?"
        args.append(before)
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM inbox_items WHERE {where} ORDER BY at DESC, id DESC LIMIT ?",
                            (*args, limit + 1)).fetchall()
        more = len(rows) > limit
        if more:  # the whole of the last moment, so the next listing (before it) skips none
            edge = rows[limit - 1][10]
            rows = conn.execute(f"SELECT {_COLUMNS} FROM inbox_items WHERE {where} AND at >= ? "
                                "ORDER BY at DESC, id DESC", (*args, edge)).fetchall()
            more = conn.execute(f"SELECT 1 FROM inbox_items WHERE {where} AND at < ? LIMIT 1",
                                (*args, edge)).fetchone() is not None
        actors = account_names(conn, {r[2] for r in rows})
        spaces = sorted({r[3] for r in rows})
        names = dict(conn.execute("SELECT id, name FROM workspaces WHERE id IN (SELECT value FROM json_each(?))",
                                  (json.dumps(spaces),)).fetchall())
        count = _unread(conn, user_id)
    # a title only where the account still gets in: an archived item of a
    # page it lost keeps the snippet of its moment, never the page's name now
    archived = [(r[0], r[1], r[3], r[4], r[5], r[7], r[8]) for r in rows if r[12]]
    for ws in sorted({r[2] for r in archived}):
        try:
            alive |= _entitled(ws, user_id, [r for r in archived if r[2] == ws])
        except Exception as e:  # noqa: BLE001 — no title then
            log.warning(f"[inbox] the archived items of workspace {ws} could not be checked: {e}")
    titles = {}
    for ws in spaces:
        titles.update(_titles(ws, {_target(r) for r in rows if r[3] == ws and r[0] in alive}))
    items = []
    for r in rows:
        target = _target(r) if r[0] in alive else ""
        items.append({"id": r[0], "kind": r[1], "actor": actors.get(r[2], ""), "workspace_id": r[3],
                      "workspace_name": names.get(r[3], ""), "page_id": r[4],
                      "page_title": titles.get((r[3], target), ""), "block_id": r[5], "thread_id": r[6],
                      "comment_id": r[7], "share_token": r[8], "snippet": r[9], "at": r[10], "read_at": r[11],
                      "archived_at": r[12]})
    return {"items": items, "unread": count, "more": more}


def _entitled(ws: str, user_id: str, items: list) -> set[str]:
    """The ids among one workspace's ``items`` (rows as ``_settle`` reads
    them) whose page or folder the account may still read the name of: all
    of them for a member (a trashed page is in its Recently deleted), else
    those whose way in still admits it."""
    if not workspaces.get(ws):
        return set()
    if workspaces.role_of(ws, user_id):
        return {r[0] for r in items}
    out, ways = set(), {}
    for item_id, kind, _ws, page_id, block_id, _comment_id, token in items:
        if page_id:
            key = (page_id, token or "")
            if key not in ways:
                ways[key] = user_id in reach(ws, page_id, [user_id], token or "")
            ok = ways[key]
        elif kind == "shared":  # a folder share: the share still letting the account in
            ok = _folder_share_admits(token, block_id, user_id)
        else:
            ok = False
        if ok:
            out.add(item_id)
    return out


def _folder_share_admits(token: str, folder: str, user_id: str) -> bool:
    """Whether a folder share's item still leads in: its share (``token``)
    exists, is the share of ``folder`` and admits the account."""
    share = share_lookup(token) if token else None
    return bool(share and share["folder"] == folder and share_access(share, user_id, False)[0])


def _target(row) -> str:
    """What a listed row (``_COLUMNS``) is titled by: its page, or a folder
    share's folder (a ``shared`` item's, a ``join_request``'s)."""
    return row[4] or (row[5] if row[1] in ("shared", "join_request") else "")


def _titles(ws: str, ids) -> dict:
    """``{(ws, id): the first line of the block's text}`` for pages and
    folders; nothing for a workspace that cannot be read."""
    wanted = sorted(i for i in ids if i)
    if not wanted:
        return {}
    try:
        with connect_pages_db(ws) as conn:
            rows = conn.execute("SELECT id, content FROM unified_blocks WHERE id IN (SELECT value FROM json_each(?))",
                                (json.dumps(wanted),)).fetchall()
    except Exception as e:  # noqa: BLE001
        log.warning(f"[inbox] the titles in workspace {ws} could not be read: {e}")
        return {}
    return {(ws, i): (text or "").split("\n", 1)[0] for i, text in rows}


def _settle(user_id: str) -> set[str]:
    """Archive the account's items that lead nowhere any more: the page is
    in Recently deleted or gone, the block or the comment is gone, a
    ``mention``'s note names the account no more, a ``join_request``'s
    request no longer waits on that page's or folder's share, or the
    account can no longer reach the page (or the workspace, for an
    invitation; the share, for a ``shared`` item). An item whose way in is
    another now takes the token ``reach`` gives: ``""`` once the account is
    a member, the token of a share that names it after the old one
    stopped. Only the workspaces read whole count: one that cannot be read
    keeps its items. Returns the ids of the items checked that still lead
    somewhere."""
    with connect_users_db() as conn:
        rows = conn.execute("SELECT id, kind, workspace_id, page_id, block_id, comment_id, share_token, actor "
                            "FROM inbox_items WHERE user_id = ? AND archived_at IS NULL", (user_id,)).fetchall()
    by_ws: dict[str, list] = {}
    for r in rows:
        by_ws.setdefault(r[2], []).append(r)
    gone, tokens, alive = [], {}, set()
    for ws, items in by_ws.items():
        try:
            lost, moved = _check(ws, user_id, items)
        except Exception as e:  # noqa: BLE001 — one workspace never settles the others' items wrong
            log.warning(f"[inbox] the items of workspace {ws} could not be checked: {e}")
            continue
        gone += lost
        tokens.update(moved)
        alive |= {r[0] for r in items} - set(lost)
    if gone or tokens:
        with connect_users_db() as conn:
            conn.execute("UPDATE inbox_items SET archived_at = ? WHERE archived_at IS NULL "
                         "AND id IN (SELECT value FROM json_each(?))", (page_now(), json.dumps(gone)))
            conn.executemany("UPDATE inbox_items SET share_token = ? WHERE id = ?",
                             [(token, item_id) for item_id, token in tokens.items()])
    return alive


def _check(ws: str, user_id: str, items: list) -> tuple[list[str], dict[str, str]]:
    """``(gone, tokens)`` for one workspace's ``items`` (``_settle``'s
    rows): the ids that lead nowhere any more, and ``{id: token}`` for the
    others whose way in is another now."""
    if not workspaces.get(ws):
        return [r[0] for r in items], {}
    pages = sorted({r[3] for r in items if r[3]})
    blocks = sorted({r[4] for r in items if r[4]})
    said = sorted({r[5] for r in items if r[5]})
    with connect_pages_db(ws) as conn:
        live = {r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE parent_id = 'root' "
                                           "AND id IN (SELECT value FROM json_each(?))", (json.dumps(pages),))}
        texts = dict(conn.execute("SELECT id, content FROM unified_blocks WHERE id IN "
                                  "(SELECT value FROM json_each(?))", (json.dumps(blocks),)).fetchall())
        shown = {r[0] for r in conn.execute("SELECT id FROM comments WHERE orphaned_at IS NULL "
                                            "AND id IN (SELECT value FROM json_each(?))", (json.dumps(said),))}
    name = ""
    if any(r[1] == "mention" for r in items):
        with connect_users_db() as users:
            name = account_names(users, [user_id]).get(user_id, "")
    prefix = join_requests.ACTOR_PREFIX
    asking = join_requests.asking(r[7][len(prefix):] for r in items if r[1] == "join_request")
    member = bool(workspaces.role_of(ws, user_id))
    ways: dict[tuple, str | None] = {}  # (page, token) → the token reach gives now, None for none
    gone, tokens = [], {}
    for item_id, kind, _ws, page_id, block_id, comment_id, token, actor in items:
        now = token
        if page_id:
            key = (page_id, token or "")
            if key not in ways:
                ways[key] = (None if page_id not in live else "" if member
                             else reach(ws, page_id, [user_id], token or "").get(user_id))
            now = ways[key]
            # a ``shared`` item tells of its own share: another share is no way in to it
            lost = now is None or (kind == "shared" and bool(token) and now not in ("", token))
        elif kind == "shared":  # a folder share: the folder, and the share still letting the account in
            lost = not member and not _folder_share_admits(token, block_id, user_id)
        else:
            lost = not member
        if kind == "mention" and block_id in texts and name not in mentions.people_in(texts[block_id] or ""):
            lost = True  # the note names the account no more
        if kind == "join_request" and asking.get(actor[len(prefix):]) != (ws, page_id, block_id):
            lost = True  # the request was decided, or no longer asks to open this
        if lost or (block_id and block_id not in texts) or (comment_id and comment_id not in shown):
            gone.append(item_id)
        elif now is not None and now != token:
            tokens[item_id] = now
    return gone, tokens


def mark(user_id: str, *, ids=(), every: bool = False, read: bool | None = None,
         archived: bool | None = None) -> int:
    """Mark the account's items ``ids`` (or ``every`` one) read or unread
    (``read``), archived or back (``archived``); a stamp already set is
    kept. Returns the unread count after."""
    sets, args = [], []
    now = page_now()
    if read is not None:
        sets.append("read_at = COALESCE(read_at, ?)" if read else "read_at = NULL")
        args += [now] if read else []
    if archived is not None:
        sets.append("archived_at = COALESCE(archived_at, ?)" if archived else "archived_at = NULL")
        args += [now] if archived else []
    with connect_users_db() as conn:
        if sets and (every or ids):
            where = "user_id = ?" + ("" if every else " AND id IN (SELECT value FROM json_each(?))")
            conn.execute(f"UPDATE inbox_items SET {', '.join(sets)} WHERE {where}",
                         (*args, user_id, *([] if every else [json.dumps(sorted(set(ids)))])))
        return _unread(conn, user_id)


# --- upkeep ------------------------------------------------------------------------

def sweep(*, now: datetime | None = None) -> dict[str, int]:
    """The daily upkeep: read items older than ``READ_KEEP_DAYS`` and items
    archived more than ``ARCHIVED_KEEP_DAYS`` ago go, unread ones older
    than ``UNREAD_KEEP_DAYS`` are archived. ``{deleted, archived}``."""
    now = now or datetime.now(timezone.utc)

    def ago(days):
        return format_stamp(now - timedelta(days=days))

    with connect_users_db() as conn:
        deleted = conn.execute("DELETE FROM inbox_items WHERE archived_at IS NOT NULL AND archived_at < ?",
                               (ago(ARCHIVED_KEEP_DAYS),)).rowcount
        deleted += conn.execute("DELETE FROM inbox_items WHERE archived_at IS NULL AND read_at IS NOT NULL "
                                "AND at < ?", (ago(READ_KEEP_DAYS),)).rowcount
        archived = conn.execute("UPDATE inbox_items SET archived_at = ? WHERE read_at IS NULL "
                                "AND archived_at IS NULL AND at < ?",
                                (format_stamp(now), ago(UNREAD_KEEP_DAYS))).rowcount
    if deleted or archived:
        log.info(f"[inbox] sweep: {deleted} item(s) deleted, {archived} archived")
    return {"deleted": deleted, "archived": archived}
