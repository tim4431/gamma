"""Comments on notes, pages and highlights (gamma/comments.py,
docs/dev/comments.md "The API").

Every handler is a sync ``def``: it opens the workspace's pages.db and a
write waits on its write lock, which must never happen on the event loop.
Who may write is ``auth.require_commenter`` (any member, viewers included,
or a signed-in account a share admits, a write token, a read token given the
Comment level in its workspace); reads are ``resolve_ws`` and
the share's scope, as for the page itself. A token's comment is signed as
its agent (``agents.of_request``: a connection by its display name and id).
After each write
``comments.written``: the page's room hears ``{t: "comments", block_id,
thread_id}``, every peer but the writer's tab (``client``), and the write's
event goes to ``comments.after_write``. A person's new comment or edit sent
to an agent (``to``, an agent's id, gamma/agents.py; or its ``@name``,
read by gamma/local_agents/mention.py for a client that sent none) is a
turn of that agent too (gamma/local_agents/threads.py), answered as
``turn``. Each thread read carries its reader's conversation of it with an
agent (``agent_chat``, ``threads.chats_of``). A page's Notify me
(``/pages/{id}/notify``) is what its comments tell the session's account
(gamma/inbox.py "following a page").
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import comments, inbox, mentions
from ..auth import is_token, require_commenter, resolve_ws, share_scope, share_via, ws_role
from ..db import connect_pages_db
from ..agents import of_request
from ..ops import StorableBody

router = APIRouter(prefix="/api", tags=["comments"])


class CommentCreate(StorableBody):
    id: str = ""         # the client's id for the comment, the same on every retry
    block_id: str
    body: str
    thread_id: str = ""  # a reply: the thread's root
    client: str = ""     # the writer's tab, left out of the room's nudge
    # the agent it is sent to (gamma/agents.py), "" for none (the box unticked); null: its @name decides
    to: str | None = Field(default=None, max_length=200)


class CommentEdit(StorableBody):
    body: str
    client: str = ""
    to: str | None = Field(default=None, max_length=200)


class CommentResolve(StorableBody):
    resolved: bool = True
    client: str = ""


class NotifyLevel(BaseModel):
    level: str  # inbox.LEVELS


def _page_of(request: Request, comment_id: str) -> str:
    """The page a comment is on now, read in the request's workspace; 404
    for a comment hidden or gone. It is read before the write's lock: the
    writer is handed it and checks under the lock that the comment is
    still on it."""
    with connect_pages_db(resolve_ws(request)) as conn:
        return comments.page_of(conn, comment_id)


@router.get("/pages/{page_id}/comments")
def list_comments(page_id: str, request: Request, state: str = "all"):
    """``{threads: [...]}`` on the page (``comments.threads_for``, each with
    ``_chats``' ``agent_chat``): ``state`` open, resolved or all. Any
    member; a share visitor for a page in the share's reach. 404 for a page
    in Recently deleted or gone."""
    ws = resolve_ws(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as conn:
        if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'", (page_id,)).fetchone():
            raise HTTPException(status_code=404, detail="page not found")
        if scope is not None and not scope.allows_page(conn, page_id):
            raise HTTPException(status_code=403, detail="not accessible via this share link")
        threads = comments.threads_for(conn, [page_id], state=state)
    return {"threads": _chats(request, ws, threads)}


def _chats(request: Request, ws: str, threads: list[dict]) -> list[dict]:
    """``threads``, each with ``agent_chat``: the session account's
    conversation of it with a computer's agent (``{conversation_id, agent,
    label, turn}``, ``threads.chats_of``), null for none, and for a token,
    a guest or a share's visitor, who have none."""
    from ..local_agents import threads as agent_threads  # local: the agents' modules import the comments

    mine = request.state.user_id if not is_token(request) and not request.state.is_guest else ""
    chats = agent_threads.chats_of(ws, mine, [t["id"] for t in threads]) if mine else {}
    return [{**t, "agent_chat": chats.get(t["id"])} for t in threads]


def _written(request: Request, ws: str, thread: dict, event: dict, client: str, to: str | None) -> dict | None:
    """``comments.written`` for a create's or an edit's event, and the turn
    it makes when it is sent to an agent: ``to``, "" for none (a field whose
    box is unticked); for a client that sent no ``to`` (None: an edit, a
    field with no box), the agent its ``@name`` names
    (gamma/local_agents/mention.py ``to_of``; decided once, before the Inbox
    reads the event, which then takes that name for the agent's) → that
    turn's answer (``threads.start``), None when it is sent to none. Only a
    person's own comment, not one made through a share, is sent."""
    from ..local_agents import mention, threads  # local: the agents' modules import the comments

    via = share_via(request)
    person = not event.get("agent_id") and not via
    if person and to is None:
        to = mention.to_of(ws, event)
    to = to or ""
    name = threads.name_of(to)
    if name and name in mentions.people_in(event.get("body") or ""):
        event = {**event, "agent_named": name}
    comments.written(ws, thread["page_id"], thread["block_id"], thread["id"], event, client=client, via=via)
    if not to:
        return None
    if not person:
        return threads.refused(name, "other", "Only a person's own comment in Gamma is sent to an agent.")
    return threads.start(ws, event, to, by_name=request.state.user or "")


def _again(request: Request, ws: str, thread: dict) -> dict:
    """The thread read again after its turn began, with its conversation's
    (``_chats``: the one the writer built under its lock has none)."""
    with connect_pages_db(ws) as conn:
        found = comments.thread_of(conn, thread["id"]) or thread
    return _chats(request, ws, [found])[0]


@router.post("/pages/{page_id}/comments", status_code=201)
def create_comment(page_id: str, payload: CommentCreate, request: Request):
    """A new thread on a block of the page (or the page itself), or a reply
    (``thread_id``) → ``{comment, thread, turn?}``, the thread with its
    reader's conversation (``_chats``), ``turn`` when it is sent to an
    agent (``to``, or its ``@name``: ``_written``, ``{agent,
    conversation_id, turn_id, state}`` or ``{agent, refused, why}``), the
    thread then read again with that turn. A repeated ``id`` answers the
    stored comment and writes nothing."""
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.create(conn, page_id, block_id=payload.block_id, body=payload.body,
                              author=request.state.user_id, author_name=request.state.user or "",
                              comment_id=payload.id, thread_id=payload.thread_id, agent=of_request(request))
    answer = {"comment": out["comment"], "thread": _chats(request, ws, [out["thread"]])[0]}
    if out["created"]:
        turn = _written(request, ws, out["thread"], out["event"], payload.client, payload.to)
        if turn is not None:
            answer.update(thread=_again(request, ws, out["thread"]), turn=turn)
    return answer


@router.patch("/comments/{comment_id}")
def edit_comment(comment_id: str, payload: CommentEdit, request: Request):
    """Change a comment's text (its author only, through the writer that
    wrote it: a session or that token) → ``{comment, thread?, turn?}``,
    the thread and the turn when the edit, of its thread's newest comment,
    is sent to an agent (``to``, or an ``@name`` it newly names: as for a
    new comment)."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.edit(conn, comment_id, page_id=page_id, body=payload.body, author=request.state.user_id,
                            agent=of_request(request))
    answer = {"comment": out["comment"]}
    if out["event"]:
        turn = _written(request, ws, out["thread"], out["event"], payload.client, payload.to)
        if turn is not None:
            answer.update(thread=_again(request, ws, out["thread"]), turn=turn)
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
                              agent=of_request(request))
    comments.written(ws, out["page_id"], out["block_id"], out["thread_id"], out["event"], client=client,
                     via=share_via(request))
    return {"deleted": True}


@router.post("/comments/{comment_id}/resolve")
def resolve_comment(comment_id: str, payload: CommentResolve, request: Request):
    """Resolve (``resolved: true``) or reopen a thread, named by its first
    comment → ``{thread}``. Anyone who may comment on the page."""
    page_id = _page_of(request, comment_id)
    ws = require_commenter(request, page_id)
    with connect_pages_db(ws) as conn:
        out = comments.resolve(conn, comment_id, page_id=page_id, resolved=payload.resolved,
                               by=request.state.user_id, agent=of_request(request))
    thread = out["thread"]
    if out["event"]:
        comments.written(ws, thread["page_id"], thread["block_id"], thread["id"], out["event"], client=payload.client,
                         via=share_via(request))
    return {"thread": _chats(request, ws, [thread])[0]}


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
