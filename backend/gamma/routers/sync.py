"""The workspace change feed: what changed since a cursor, for anything that
keeps a copy of a workspace in step (a desktop mirror, the iPad's replica).

``GET /api/sync/changes?since=&limit=`` reads the workspace's change log
(``page_changes``, gamma/blocks_store.py ``touch_page``): every page whose
row took a seq above ``since``, in seq order — a live page with its latest
op-log seq, a deleted one (in Recently deleted, or gone for good) as a
tombstone. Every writer of a page touches it inside its transaction, under
the write lock, so the seqs are handed out in commit order: a cursor is
exact, nothing is listed twice for one change and nothing slips behind it.
What changed on a page is still its own op log (``seq``,
``GET /pages/{id}/ops?since=``). The cursor is the highest seq listed, as a
string the consumer keeps as it is (``""`` = from the start); one this log
cannot have given out — not a count, or past its newest seq (a workspace
put back from an older snapshot) — lists from the start again, which a
consumer takes in its stride: it compares each page's ``seq`` with its own.

Members read it (viewers too); share links do not.
"""

from fastapi import APIRouter, Request

from .. import workspaces
from ..auth import require_ws, ws_role
from ..db import connect_pages_db

router = APIRouter(prefix="/api", tags=["sync"])

MAX_LIMIT = 2000


def changes(conn, since: str, limit: int) -> dict:
    """The feed over an open pages.db: ``{since, cursor, more, pages: [{id,
    created_at, updated_at, seq}], deleted: [{id, deleted_at, actor}]}``,
    at most ``limit`` entries after the cursor ``since``."""
    newest = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
    after = int(since) if since.isascii() and since.isdigit() and int(since) <= newest else 0
    rows = conn.execute(
        "SELECT c.page_id, c.seq, c.kind, c.at, c.actor, b.created_at, b.updated_at, "
        "(SELECT COALESCE(MAX(o.seq), 0) FROM page_ops o WHERE o.page_id = c.page_id) "
        "FROM page_changes c LEFT JOIN unified_blocks b ON b.id = c.page_id "
        "WHERE c.seq > ? ORDER BY c.seq LIMIT ?", (after, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = {"since": since, "pages": [], "deleted": [], "more": more,
           "cursor": str(rows[-1][1] if rows else after)}
    for page_id, _, kind, at, actor, created_at, updated_at, seq in rows:
        if kind == "deleted":
            out["deleted"].append({"id": page_id, "deleted_at": at, "actor": actor})
        else:
            out["pages"].append({"id": page_id, "created_at": created_at, "updated_at": updated_at, "seq": seq})
    return out


@router.get("/sync/whoami")
def sync_whoami(request: Request):
    """Who the credential is and what it may do here: ``{user, workspace:
    {id, name}, role, scope}`` — ``scope`` is the integration token's
    (``read`` / ``write``), ``session`` for a signed-in browser. A mirror
    checks this before it is created and at the start of every round."""
    ws = require_ws(request)
    info = workspaces.get(ws) or {}
    return {"user": request.state.user, "workspace": {"id": ws, "name": info.get("name", "")},
            "role": ws_role(request),
            "scope": request.state.token_scope if getattr(request.state, "auth", "") == "token" else "session"}


@router.get("/sync/changes")
def sync_changes(request: Request, since: str = "", limit: int = 500):
    """Pages changed and pages deleted since ``since`` (``""`` = everything),
    at most ``limit`` entries, with the cursor to continue from."""
    ws = require_ws(request)
    limit = max(1, min(int(limit or 500), MAX_LIMIT))
    with connect_pages_db(ws) as conn:
        return changes(conn, since, limit)
