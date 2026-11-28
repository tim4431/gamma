"""The agent's hand (docs/dev/annotations.md "The writing tools"): what an
agent writes on a page as its account, signed as itself (gamma/agents.py):
a new comment thread on a place (``places``), a reply, a thread resolved
(never reopened) and a highlight. The registry's three tools write through
it (gamma/ai_tools/commenting.py ``comment``, ``resolve`` and ``highlight``: Gamma AI's,
an MCP connection's at the Comment level, a computer agent's turn), and so
does the end of a thread's turn that its agent left unanswered
(gamma/local_agents/threads.py ``settle``).

Who writes is a ``Writer``: the account (its id and username) and the
agent. The account authors the comment and the highlight's op, so every
role check reads it as it always did; the agent is said beside it (a
comment's ``agent`` and ``agent_id``, a block's ``properties.agent``). An
account that may comment there writes a comment, a reply and a resolve; a
new highlight is a block of the page, an edit, so only an account that may
edit there makes one, a highlight or a comment on a passage or a range
(``may_mark``): a viewer's agent comments on the page or a note, as a
viewer does.
Each write goes through ``comments`` and the op path as a person's does,
and what follows, the page's room, the Inbox and the annotation files, is
``comments.written``'s and ``ops.after_commit``'s; an agent's comment makes
no one follow the page. A call made again moments later, the same agent
of the same account writing the same words on the same place, writes
nothing and answers what the first wrote (``REPEAT_S``), and a place keeps
one highlight of each agent per account: a retried call never doubles a
mark. A refusal is a ValueError.
"""

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from fastapi import HTTPException
from fractional_indexing import generate_key_between

from .. import agents, colors, comments, highlights, hosted, workspaces
from ..agents import Agent
from ..blocks_store import last_child_position, load_props, new_block_id
from ..db import format_stamp
from ..integrations import EDITING_ROLES
from ..ops import OpError, after_commit, apply_ops
from ..work_folders.marks import Tree
from . import anchors
from .entries import file_path
from .places import Place

REPEAT_S = 300            # seconds within which the same words on the same place are one call made again
# The colours a highlight is made in, by name: the default wash set
# (colors.WASH, the palette's first four), stored at the wash alpha.
COLORS = dict(zip(("yellow", "green", "blue", "purple"), colors.WASH))
STYLES = ("highlight", "underline", "squiggly", "strikeout")  # shared/model/markStyle.js MARK_STYLES
NO_THREAD = "No comment thread has that id: list_annotations lists them."


class Writer(NamedTuple):
    """Who writes: the account ``user_id`` (its ``username``), as the agent
    ``agent``."""
    user_id: str
    username: str
    agent: Agent

    @property
    def stamp(self) -> dict:
        """What a block it makes carries as ``properties.agent``."""
        return agents.stamp(self.agent, self.username)


def may_write(writer: Writer, ws: str) -> None:
    """ValueError while this server takes no writes (``hosted.read_only``),
    a delete holds ``ws``, or the account may not comment there."""
    refused = hosted.read_only()
    if refused:
        raise ValueError(refused)
    try:
        workspaces.refuse_writes(ws)
    except HTTPException as e:
        raise ValueError(str(e.detail)) from None
    if not workspaces.role_of(ws, writer.user_id):
        raise ValueError("This account cannot comment in this workspace.")


def may_mark(writer: Writer, ws: str) -> None:
    """ValueError unless the account may edit ``ws``: a new highlight is a
    block of the page, an edit, which a viewer does not make."""
    if workspaces.role_of(ws, writer.user_id) not in EDITING_ROLES:
        raise ValueError("This account can only view this workspace: comment on the page or on a note's block, "
                         "not on a passage or lines.")


def _refused(e: HTTPException) -> ValueError:
    return ValueError(str(e.detail))


def thread(conn, thread_id) -> dict:
    """The thread ``thread_id`` names (``comments.thread_of``), on a page of
    the library; ValueError otherwise (unknown, hidden, a reply's id, its
    page in Recently deleted)."""
    found = comments.thread_of(conn, thread_id) if isinstance(thread_id, str) and thread_id else None
    if found is None or found["id"] != thread_id:
        raise ValueError(NO_THREAD)
    if not conn.execute("SELECT 1 FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                        (found["page_id"],)).fetchone():
        raise ValueError("That thread's page is in Recently deleted.")
    return found


def named(conn, found: dict) -> str:
    """The page and the place a thread is on, as an answer names them:
    "main.pdf, page 3, “the loss is defined…”", "fig.png, a region of the
    picture", "main.tex, lines 118–120, “for step in…”" (the lines the range
    holds now, ``anchors.following``)."""
    row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ?", (found["page_id"],)).fetchone()
    where = file_path(Tree.read(conn), load_props(row[1]), page_id=found["page_id"], title=row[0] or "")
    row = conn.execute("SELECT anchor FROM comments WHERE id = ?", (found["id"],)).fetchone()
    anchor = anchors.parse(row[0] if row else None) or anchors.anchor_of(conn, found["page_id"], found["block_id"])
    return f"{where}, {anchors.place_words(anchors.following(conn, found['page_id'], anchor))}"


def _since() -> str:
    return format_stamp(datetime.now(timezone.utc) - timedelta(seconds=REPEAT_S))


def _check_body(body) -> str:
    try:
        return comments.clean_body(body)
    except HTTPException as e:
        raise _refused(e) from None


def reply(conn, writer: Writer, ws: str, thread_id: str, body, *, comment_id: str = "", turn_end: str = "") -> dict:
    """Write ``body`` as the writer's reply in the thread ``thread_id`` →
    ``{thread, comment, created}``. ``created`` false: the same words were
    its last moments ago (``REPEAT_S``), or ``comment_id`` (a turn's end's
    id for it, the same however often that end is written) is written
    already: ``comment`` is that one. ``turn_end``: for a turn's end's note,
    what became of the turn (gamma/local_agents/threads.py ``_end_of``),
    carried by the write's event to the Inbox. ValueError for a refusal."""
    may_write(writer, ws)
    found = thread(conn, thread_id)
    body = _check_body(body)
    if not comment_id:
        row = conn.execute("SELECT id FROM comments WHERE thread_id = ? AND id != thread_id AND agent_id = ? "
                           "AND author = ? AND body = ? AND created_at >= ? AND deleted_at IS NULL "
                           "ORDER BY created_at DESC LIMIT 1",
                           (found["id"], writer.agent.id, writer.user_id, body, _since())).fetchone()
        if row:
            return {"thread": found, "comment": next(c for c in found["comments"] if c["id"] == row[0]),
                    "created": False}
    try:
        out = comments.create(conn, found["page_id"], block_id=found["block_id"], body=body, author=writer.user_id,
                              author_name=writer.username, comment_id=comment_id, thread_id=found["id"],
                              agent=writer.agent)
    except HTTPException as e:
        raise _refused(e) from None
    if out["created"]:
        event = {**out["event"], "turn_end": turn_end} if turn_end else out["event"]
        comments.written(ws, found["page_id"], found["block_id"], found["id"], event)
    return {"thread": out["thread"], "comment": out["comment"], "created": out["created"]}


def resolve(conn, writer: Writer, ws: str, thread_id: str) -> dict:
    """Resolve the thread ``thread_id`` as the writer → ``{thread,
    changed}``; a resolved thread stays as it was resolved (``changed``
    false). ValueError for a refusal."""
    may_write(writer, ws)
    found = thread(conn, thread_id)
    try:
        out = comments.resolve(conn, found["id"], page_id=found["page_id"], resolved=True, by=writer.user_id,
                               agent=writer.agent)
    except HTTPException as e:
        raise _refused(e) from None
    if out["event"]:
        comments.written(ws, found["page_id"], found["block_id"], found["id"], out["event"])
    return {"thread": out["thread"], "changed": bool(out["event"])}


def _at(place: Place, block_id: str, anchor: dict | None) -> bool:
    """Whether a thread on ``block_id`` opened at ``anchor`` is on
    ``place``: the same block, or the same passage (its page and its quote),
    area (its page and its rectangle) or range (its lines) for a place a new
    highlight would give."""
    if place.block_id:
        return block_id == place.block_id
    anchor = anchor or {}
    if place.kind == "passage":
        return (anchor.get("kind") == "passage" and anchor.get("page") == place.page
                and anchor.get("quote") == place.props["quote"][:highlights.PREV_MAX_QUOTE])
    if place.kind == "area":
        rects = anchor.get("rects") or [()]
        return anchor.get("kind") == "area" and anchor.get("page") == place.page and _same_box(rects[0], place.box)
    return anchor.get("kind") == "lines" and anchors.lines_of(anchor) == place.lines


def _same_box(a, b) -> bool:
    """Two rectangles as page fractions, the same to a thousandth of the
    page (an anchor keeps four decimals, measured back from points)."""
    return len(a) == len(b) == 4 and all(abs(x - y) < 1e-3 for x, y in zip(a, b))


def _fractions(pos: dict) -> tuple:
    """A stored position's bounding rectangle as fractions of its page, ()
    when it has no frame to measure it by."""
    size, rect = highlights._size(pos), pos.get("boundingRect")
    if not size or not highlights.placed(rect):
        return ()
    return (rect["x1"] / size[0], rect["y1"] / size[1], rect["x2"] / size[0], rect["y2"] / size[1])


def _mark(conn, writer: Writer, ws: str, page_id: str, props: dict, note: str = "") -> str:
    """A new highlight block at the end of the page's top level, made of
    ``props`` (a place's, with its colour and style) and stamped as the
    writer's, its text ``note``; its id. ValueError where the account may
    not edit (``may_mark``) or the op path refuses it."""
    may_mark(writer, ws)
    block_id = new_block_id()
    position = generate_key_between(last_child_position(conn, page_id), None)
    _commit(conn, writer, ws, page_id, [{"op": "insert", "id": block_id, "parent": page_id, "position": position,
                                         "content": note, "props": {**props, "agent": writer.stamp}}])
    return block_id


def _commit(conn, writer: Writer, ws: str, page_id: str, ops: list[dict]) -> None:
    """Apply ``ops`` on the page as the writer's account; ValueError for a
    refused batch."""
    try:
        result = apply_ops(conn, page_id, ops, actor=writer.user_id, client="ai")
    except OpError as e:
        raise ValueError(str(e)) from None
    after_commit(ws, conn, result)


def open_thread(conn, writer: Writer, ws: str, page_id: str, place: Place, body) -> dict:
    """A new thread with the writer's ``body`` on ``place`` of the page →
    ``{thread, comment, created, highlight}``: on its block, or on a new
    highlight of its passage or range (``_mark``: where the account may
    edit), drawn in the wash as a person's commented passage is
    (``highlight`` its id, "" for none), removed again when the thread is
    refused. ``created``
    false: the writer opened one with these words on this place moments ago
    (``REPEAT_S``), which ``thread`` is. ValueError for a refusal."""
    may_write(writer, ws)
    body = _check_body(body)
    for thread_id, block_id, anchor in conn.execute(
            "SELECT id, block_id, anchor FROM comments WHERE page_id = ? AND id = thread_id AND agent_id = ? "
            "AND author = ? AND body = ? AND created_at >= ? AND orphaned_at IS NULL AND deleted_at IS NULL "
            "ORDER BY created_at DESC", (page_id, writer.agent.id, writer.user_id, body, _since())).fetchall():
        if _at(place, block_id, anchors.parse(anchor)):
            found = comments.thread_of(conn, thread_id)
            if found:
                return {"thread": found, "comment": found["comments"][0], "created": False, "highlight": ""}
    made = "" if place.block_id else _mark(conn, writer, ws, page_id,
                                          {"color": colors.DEFAULT_HIGHLIGHT, **place.props})
    try:
        out = comments.create(conn, page_id, block_id=place.block_id or made, body=body, author=writer.user_id,
                              author_name=writer.username, agent=writer.agent)
    except HTTPException as e:
        if made:  # the thread was refused (its page went meanwhile): its passage's highlight goes too
            try:
                _commit(conn, writer, ws, page_id, [{"op": "delete", "id": made}])
            except ValueError:
                pass
        raise _refused(e) from None
    comments.written(ws, page_id, out["thread"]["block_id"], out["thread"]["id"], out["event"])
    return {"thread": out["thread"], "comment": out["comment"], "created": True, "highlight": made}


def highlight(conn, writer: Writer, ws: str, page_id: str, place: Place, *, note: str = "", color: str = "",
              style: str = "") -> dict:
    """The writer's highlight of ``place`` (a passage, an area or a range)
    on the page → ``{block_id, created}``: a new highlight block, ``note``
    its text, in ``color`` (a name of ``COLORS``, the first by default) and
    ``style`` (one of ``STYLES``, the wash by default; an area takes the
    wash alone, as a person's does); ``created`` false when the writer has
    one on that place already, which ``block_id`` is. ValueError for a
    refusal: a viewer makes none (``may_mark``)."""
    may_write(writer, ws)
    may_mark(writer, ws)
    if color and color not in COLORS:
        raise ValueError(f"color is one of {', '.join(COLORS)}")
    if style and style not in STYLES:
        raise ValueError(f"style is one of {', '.join(STYLES)}")
    if not isinstance(note, str) or len(note) > comments.MAX_BODY:
        raise ValueError(f"a note is text of at most {comments.MAX_BODY} characters")
    area = place.kind == "area"
    if area and style and style != "highlight":
        raise ValueError("an area is drawn as a wash: it takes no other style")
    key = "pdf_position" if "pdf_position" in place.props else "text_position"
    for block_id, raw in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE page_id = ? AND kind = 'highlight' "
            "AND json_extract(properties, '$.agent.id') = ? AND json_extract(properties, '$.agent.for') = ?",
            (page_id, writer.agent.id, writer.username)).fetchall():
        props = load_props(raw)
        pos = props.get(key)
        if not isinstance(pos, dict) or props.get("quote") != place.props["quote"] or bool(pos.get("area")) != area:
            continue
        if area:
            same = pos.get("pageNumber") == place.page and _same_box(_fractions(pos), place.box)
        elif key == "pdf_position":
            same = pos.get("pageNumber") == place.page
        else:
            same = (pos.get("line"), pos.get("end")) == place.lines
        if same:
            return {"block_id": block_id, "created": False}
    props = {"color": colors.highlight_color(COLORS[color or "yellow"]), **place.props}
    if style and style != "highlight":
        props["style"] = style
    return {"block_id": _mark(conn, writer, ws, page_id, props, note.strip()), "created": True}
