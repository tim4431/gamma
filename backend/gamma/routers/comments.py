"""Comments on notes, pages and highlights (gamma/comments.py,
docs/dev/comments.md "The API").

Every handler is a sync ``def``: it opens the workspace's pages.db and a
write waits on its write lock, which must never happen on the event loop.
Who may write is ``auth.require_commenter`` (any member, viewers included,
or a signed-in account a share admits); reads are ``resolve_ws`` and the
share's scope, as for the page itself. After each write the page's room
hears ``{t: "comments", block_id, thread_id}``, every peer but the
writer's tab (``client``), and the write's event goes to
``comments.after_write``.
"""

from fastapi import APIRouter, HTTPException, Request

from .. import collab, comments
from ..auth import is_token, require_commenter, resolve_ws, share_scope, share_via, ws_role
from ..db import connect_pages_db
from ..ops import StorableBody

router = APIRouter(prefix="/api", tags=["comments"])


class CommentCreate(StorableBody):
    id: str = ""         # the client's id for the comment, the same on every retry
    block_id: str
    body: str
    thread_id: str = ""  # a reply: the thread's root
    client: str = ""     # the writer's tab, left out of the room's nudge


class CommentEdit(StorableBody):
    body: str
    client: str = ""


class CommentResolve(StorableBody):
    resolved: bool = True
    client: str = ""


def _written(request: Request, ws: str, page_id: str, block_id: str, thread_id: str, client: str,
             event) -> None:
    """A committed write: the page's room refetches its threads (all but
    the writer's tab), then what hangs on ``after_write``, the event
    carrying the share the writer came through (``via``)."""
    collab.publish(ws, page_id, {"t": "comments", "block_id": block_id, "thread_id": thread_id},
                   exclude=client)
    if event:
        event = {**event, "via": share_via(request)}
    comments.after_write(ws, event)


def _page_of(request: Request, comment_id: str) -> str:
    """The page a comment is on now, read in the request's workspace; 404
    for a comment hidden or gone. It is read before the write's lock: the
    writer is handed it and checks under the lock that the comment is
    still on it."""
    with connect_pages_db(resolve_ws(request)) as conn:
        return comments.page_of(conn, comment_id)


@router.get("/pages/{page_id}/comments")
def list_comments(page_id: str, request: Request, state: str = "all"):
    """``{threads: [...]}`` on the page (``comments.threads_for``):
    ``state`` open, resolved or all. Any member; a share visitor for a page
    in the share's reach. 404 for a page in Recently deleted or gone."""
    ws = resolve_ws(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as conn:
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (page_id,)).fetchone():
            raise HTTPException(status_code=404, detail="page not found")
        if scope is not None and not scope.allows_page(conn, page_id):
            raise HTTPException(status_code=403, detail="not accessible via this share link")
        return {"threads": comments.threads_for(conn, [page_id], state=state)}


@router.post("/pages/{page_id}/comments", status_code=201)
def create_comment(page_id: str, payload: CommentCreate, request: Request):
    """A new thread on a block of the page (or the page itself), or a reply
    (``thread_id``) → ``{comment, thread}``. A repeated ``id`` answers the
    stored comment and writes nothing."""
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.create(conn, page_id, block_id=payload.block_id, body=payload.body,
                              author=request.state.user_id, author_name=request.state.user or "",
                              comment_id=payload.id, thread_id=payload.thread_id)
    if out["created"]:
        _written(request, ws, page_id, payload.block_id, out["thread"]["id"], payload.client, out["event"])
    return {"comment": out["comment"], "thread": out["thread"]}


@router.patch("/comments/{comment_id}")
def edit_comment(comment_id: str, payload: CommentEdit, request: Request):
    """Change a comment's text (its author only) → ``{comment}``."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.edit(conn, comment_id, page_id=page_id, body=payload.body, author=request.state.user_id)
    if out["event"]:
        _written(request, ws, out["thread"]["page_id"], out["thread"]["block_id"], out["thread"]["id"], payload.client,
                 out["event"])
    return {"comment": out["comment"]}


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: str, request: Request, client: str = ""):
    """Delete a comment (its author's, or any for the workspace's owner
    signed in as a member; an integration token deletes its own only)
    → ``{deleted: true}``; a thread's first comment that has replies stays
    as a tombstone. ``?client=`` names the writer's tab."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    owner = ws_role(request) == "owner" and not is_token(request)  # a token deletes its own only
    with connect_pages_db(ws) as conn:
        out = comments.delete(conn, comment_id, page_id=page_id, user=request.state.user_id, owner=owner)
    _written(request, ws, out["page_id"], out["block_id"], out["thread_id"], client, out["event"])
    return {"deleted": True}


@router.post("/comments/{comment_id}/resolve")
def resolve_comment(comment_id: str, payload: CommentResolve, request: Request):
    """Resolve (``resolved: true``) or reopen a thread, named by its first
    comment → ``{thread}``. Anyone who may comment on the page."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.resolve(conn, comment_id, page_id=page_id, resolved=payload.resolved,
                               by=request.state.user_id)
    thread = out["thread"]
    if out["event"]:
        _written(request, ws, thread["page_id"], thread["block_id"], thread["id"], payload.client, out["event"])
    return {"thread": thread}
