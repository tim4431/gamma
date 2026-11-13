"""Comments on notes, pages and highlights (gamma/comments.py,
docs/dev/comments.md "The API").

Every handler is a sync ``def``: it opens the workspace's pages.db and a
write waits on its write lock, which must never happen on the event loop.
Who may write is ``auth.require_commenter`` (any member, viewers included,
or a signed-in account a share admits, a write token, a feedback token's
reply and resolve on a work folder's file); reads are ``resolve_ws`` and
the share's scope, as for the page itself. A token's comment is signed with
its display name and id (``integrations.display_name``). After each write
``comments.written``: the page's room hears ``{t: "comments", block_id,
thread_id}``, every peer but the writer's tab (``client``), and the write's
event goes to ``comments.after_write``. A new comment or an edit that
mentions the work folder's agent on this computer is a Send too
(gamma/local_agents/mention.py), answered as ``send``. A page's Notify me (``/pages/{id}/notify``) is
what its comments tell the session's account (gamma/inbox.py
"following a page").
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import comments, inbox, local_agents
from ..auth import is_token, require_commenter, resolve_ws, share_scope, share_via, ws_role
from ..db import connect_pages_db
from ..integrations import display_name
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


class NotifyLevel(BaseModel):
    level: str  # inbox.LEVELS


def _writer(request: Request) -> tuple[str, str]:
    """``(agent, agent_id)``: the display name and id of the integration
    token the request came with, ``("", "")`` for a session."""
    if not is_token(request):
        return "", ""
    return display_name(request.state.token_name), request.state.token_id


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


def _written(request: Request, ws: str, thread: dict, event: dict, client: str) -> dict | None:
    """``comments.written`` for a create's or an edit's event, and the Send
    it makes when it names the work folder's agent on this computer
    (gamma/local_agents/mention.py, decided once, before the Inbox reads the
    event) → that Send's answer, None when it names none."""
    via = share_via(request)
    mention = None
    if local_agents.enabled():
        from ..local_agents import mention  # local: the agents' modules import the feedback modules
    name = mention.agent_named(ws, event, via=via) if mention else ""
    if name:
        event = {**event, "agent_named": name}
    comments.written(ws, thread["page_id"], thread["block_id"], thread["id"], event, client=client, via=via)
    return mention.send(ws, event, name, by_name=request.state.user or "") if name else None


def _again(ws: str, thread: dict) -> dict:
    """The thread read again after a Send, with its Sent chip (the one the
    writer built under its lock has none)."""
    with connect_pages_db(ws) as conn:
        return comments.thread_of(conn, thread["id"]) or thread


@router.post("/pages/{page_id}/comments", status_code=201)
def create_comment(page_id: str, payload: CommentCreate, request: Request):
    """A new thread on a block of the page (or the page itself), or a reply
    (``thread_id``) → ``{comment, thread, send?}``, ``send`` when it
    mentions the work folder's agent on this computer (``_written``:
    ``{agent, request_id, scope, threads}`` or ``{agent, refused, why}``).
    A repeated ``id`` answers the stored comment and writes nothing."""
    ws = require_commenter(request, page_id, answer=bool(payload.thread_id))
    agent, agent_id = _writer(request)
    with connect_pages_db(ws) as conn:
        out = comments.create(conn, page_id, block_id=payload.block_id, body=payload.body,
                              author=request.state.user_id, author_name=request.state.user or "",
                              comment_id=payload.id, thread_id=payload.thread_id, agent=agent, agent_id=agent_id)
    answer = {"comment": out["comment"], "thread": out["thread"]}
    if out["created"]:
        send = _written(request, ws, out["thread"], out["event"], payload.client)
        if send is not None:
            answer.update(thread=_again(ws, out["thread"]), send=send)
    return answer


@router.patch("/comments/{comment_id}")
def edit_comment(comment_id: str, payload: CommentEdit, request: Request):
    """Change a comment's text (its author only, through the writer that
    wrote it: a session or that token) → ``{comment, thread?, send?}``,
    the thread and the Send when the edit newly mentions the work folder's
    agent on this computer (as for a new comment)."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.edit(conn, comment_id, page_id=page_id, body=payload.body, author=request.state.user_id,
                            agent_id=_writer(request)[1])
    answer = {"comment": out["comment"]}
    if out["event"]:
        send = _written(request, ws, out["thread"], out["event"], payload.client)
        if send is not None:
            answer.update(thread=_again(ws, out["thread"]), send=send)
    return answer


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: str, request: Request, client: str = ""):
    """Delete a comment (its author's, or any for the workspace's owner
    signed in as a member; an integration token deletes the comments it
    wrote only) → ``{deleted: true}``; a thread's first comment that has
    replies stays as a tombstone. ``?client=`` names the writer's tab."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    owner = ws_role(request) == "owner" and not is_token(request)  # a token deletes its own only
    with connect_pages_db(ws) as conn:
        out = comments.delete(conn, comment_id, page_id=page_id, user=request.state.user_id, owner=owner,
                              agent_id=_writer(request)[1])
    comments.written(ws, out["page_id"], out["block_id"], out["thread_id"], out["event"], client=client,
                     via=share_via(request))
    return {"deleted": True}


@router.post("/comments/{comment_id}/resolve")
def resolve_comment(comment_id: str, payload: CommentResolve, request: Request):
    """Resolve (``resolved: true``) or reopen a thread, named by its first
    comment → ``{thread}``. Anyone who may comment on the page; a feedback
    token resolves and never reopens."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id, answer=payload.resolved)
    with connect_pages_db(ws) as conn:
        out = comments.resolve(conn, comment_id, page_id=page_id, resolved=payload.resolved,
                               by=request.state.user_id, agent=_writer(request)[0])
    thread = out["thread"]
    if out["event"]:
        comments.written(ws, thread["page_id"], thread["block_id"], thread["id"], out["event"], client=payload.client,
                         via=share_via(request))
    return {"thread": thread}


def _follower(request: Request, page_id: str) -> tuple[str, str]:
    """The workspace and the account whose Notify me a request reads or
    sets: who may comment on the page (``require_commenter``), signed in
    to an account of their own — a guest and an integration token have no
    Inbox (403, as for ``/api/inbox``). 404 for a page in Recently deleted
    or gone."""
    ws = require_commenter(request, page_id)
    if request.state.is_guest or is_token(request):
        raise HTTPException(status_code=403, detail="the inbox follows a signed-in account")
    with connect_pages_db(ws) as conn:
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (page_id,)).fetchone():
            raise HTTPException(status_code=404, detail="page not found")
    return ws, request.state.user_id


@router.get("/pages/{page_id}/notify")
def get_notify(page_id: str, request: Request):
    """``{level}``: what a comment on the page tells the account —
    ``all`` (All comments) or ``replies`` (Replies and @mentions)."""
    ws, user_id = _follower(request, page_id)
    return {"level": inbox.notify_level(ws, page_id, user_id)}


@router.put("/pages/{page_id}/notify")
def set_notify(page_id: str, payload: NotifyLevel, request: Request):
    """Set the page's Notify me for the account → ``{level}``; 400 for
    another level. Kept over the account's later edits and comments."""
    ws, user_id = _follower(request, page_id)
    try:
        inbox.set_notify_level(ws, page_id, user_id, payload.level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"level": payload.level}
