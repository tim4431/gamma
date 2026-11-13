"""Answering feedback over MCP: ``reply_feedback`` and ``resolve_feedback``,
the two tools a connection the user allowed to answer feedback (an
integration token of scope ``feedback``) has beside the reading ones
(docs/dev/mcp.md "Answering feedback"). They are no entries of the chat's
registry: the chat never answers threads.

Each writes one thing in a thread on a work folder's file, a reply or the
thread resolved (never reopened), as the connection's account, signed with
the token's display name and id (``agent``, ``agent_id``), through
``comments`` as the comment API writes it; what follows, the page's room,
the Inbox and the feedback files, is ``comments.written``'s. A token's
comment makes no one follow the page. A refusal is a ValueError, which the
adapter (gamma/mcp_server.py) answers as a tool error.
"""

from urllib.parse import urlencode

from fastapi import HTTPException

from .. import comments, hosted, workspaces
from ..ai_tools import gamma_link
from ..blocks_store import load_props
from ..db import connect_pages_db
from ..integrations import Connection, display_name
from ..work_folders.marks import Tree
from . import anchors
from .entries import file_path

_THREAD = {"type": "string", "minLength": 1, "maxLength": 64,
           "description": "The feedback item's id (list_feedback, read_feedback)."}
REPLY_SCHEMA = {"type": "object", "properties": {
    "thread_id": _THREAD,
    "body": {"type": "string", "minLength": 1, "maxLength": comments.MAX_BODY,
             "description": "What you changed and where (file:line), or the question you need answered."},
    "id": {"type": "string", "description": "Optional: your own id for this reply, the same on a retry."}},
    "required": ["thread_id", "body"], "additionalProperties": False}
RESOLVE_SCHEMA = {"type": "object", "properties": {"thread_id": _THREAD}, "required": ["thread_id"],
                  "additionalProperties": False}
REPLY_DESCRIPTION = ("Reply to a feedback item (a comment thread on a work folder's file) as this connection: "
                     "say what you changed and where. It writes nothing but the reply.")
RESOLVE_DESCRIPTION = ("Resolve a feedback item once it is done. Never resolve one you did not address; leave one "
                       "that needs the user's decision open with a reply.")
NO_ITEM = "No feedback item has that id: list_feedback lists them."
NOT_A_FILE = "That thread is not on a work folder's file."


def _may_answer(connection: Connection, ws: str) -> None:
    """ValueError while this server takes no writes (``hosted.read_only``),
    a delete holds ``ws``, or the account may not comment there."""
    refused = hosted.read_only()
    if refused:
        raise ValueError(refused)
    try:
        workspaces.refuse_writes(ws)
    except HTTPException as e:
        raise ValueError(str(e.detail)) from None
    if not workspaces.role_of(ws, connection.user_id):
        raise ValueError("This connection's account cannot comment in this workspace.")


def _thread(conn, args: dict) -> dict:
    """The thread ``args["thread_id"]`` names (``comments.thread_of``), on a
    file of a work folder; ValueError otherwise (unknown, hidden, a reply's
    id, a library page's thread, a file in Recently deleted)."""
    thread_id = args.get("thread_id")
    thread = comments.thread_of(conn, thread_id) if isinstance(thread_id, str) and thread_id else None
    if thread is None or thread["id"] != thread_id:
        raise ValueError(NO_ITEM)
    row = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (thread["page_id"],)).fetchone()
    if not (row and row[0]):
        raise ValueError(NOT_A_FILE)
    return thread


def _named(conn, thread: dict) -> str:
    """The file and the place the thread is on, as an answer names them:
    "main.pdf, page 3, “the loss is defined…”", "fig.png, a region of the
    picture"."""
    row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ?", (thread["page_id"],)).fetchone()
    where = file_path(Tree.read(conn), load_props(row[1]), page_id=thread["page_id"], title=row[0] or "")
    row = conn.execute("SELECT anchor FROM comments WHERE id = ?", (thread["id"],)).fetchone()
    anchor = anchors.parse(row[0] if row else None) or anchors.anchor_of(conn, thread["page_id"], thread["block_id"])
    return f"{where}, {anchors.place_words(anchor)}"


def _link(base: str, ws: str, thread: dict) -> str:
    """The thread's absolute link, from the adapter's base as its other
    links are (``mcp_server._link_base``)."""
    return gamma_link({"link_base": base + "/?" + urlencode({"ws": ws})}, thread["page_id"],
                      block_id=thread["block_id"], thread_id=thread["id"])


def _seen(event: dict | None, seen: str) -> dict | None:
    """The write's event, with when the writer read the thread (``seen``,
    an agent on this computer's run): the requests made after it are not
    answered by it (``requests.answered``)."""
    return {**event, "seen": seen} if event and seen else event


def reply(connection: Connection, ws: str, base: str, args: dict, *, seen: str = "") -> tuple[str, dict]:
    """Write ``args["body"]`` as this connection's reply in the thread
    ``args["thread_id"]`` (``REPLY_SCHEMA``): ``(text, {thread_id,
    comment_id, page_id, url})``. A repeated ``id`` answers the reply it
    wrote and writes nothing. ``seen``: when the writer read the thread
    (``_seen``; never one of the tool's arguments). ValueError for a
    refusal."""
    _may_answer(connection, ws)
    with connect_pages_db(ws) as conn:
        thread = _thread(conn, args)
        named = _named(conn, thread)
        try:
            out = comments.create(conn, thread["page_id"], block_id=thread["block_id"], body=args.get("body"),
                                  author=connection.user_id, author_name=connection.username,
                                  comment_id=str(args.get("id") or ""), thread_id=thread["id"],
                                  agent=display_name(connection.name), agent_id=connection.token_id)
        except HTTPException as e:
            raise ValueError(str(e.detail)) from None
    if out["created"]:
        comments.written(ws, thread["page_id"], thread["block_id"], thread["id"], _seen(out["event"], seen), client="",
                         via="")
    url = _link(base, ws, thread)
    said = (f"Replied to the feedback item {thread['id']} on {named}" if out["created"]
            else f"This reply to the feedback item {thread['id']} on {named} was written already")
    return f"{said}: {url}", {"thread_id": thread["id"], "comment_id": out["comment"]["id"],
                              "page_id": thread["page_id"], "url": url}


def resolve(connection: Connection, ws: str, base: str, args: dict, *, seen: str = "") -> tuple[str, dict]:
    """Resolve the thread ``args["thread_id"]`` as this connection
    (``RESOLVE_SCHEMA``): ``(text, {thread_id, page_id, url})``. A resolved
    thread stays as it was resolved. ``seen`` as for ``reply``. ValueError
    for a refusal."""
    _may_answer(connection, ws)
    with connect_pages_db(ws) as conn:
        thread = _thread(conn, args)
        named = _named(conn, thread)
        try:
            out = comments.resolve(conn, thread["id"], page_id=thread["page_id"], resolved=True,
                                   by=connection.user_id, agent=display_name(connection.name))
        except HTTPException as e:
            raise ValueError(str(e.detail)) from None
    if out["event"]:
        comments.written(ws, thread["page_id"], thread["block_id"], thread["id"], _seen(out["event"], seen), client="",
                         via="")
    url = _link(base, ws, thread)
    said = (f"Resolved the feedback item {thread['id']} on {named}" if out["event"]
            else f"The feedback item {thread['id']} on {named} was resolved already")
    return f"{said}: {url}", {"thread_id": thread["id"], "page_id": thread["page_id"], "url": url}
