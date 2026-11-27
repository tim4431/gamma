"""The writing tools: a comment (a new thread on a place of a page, or a
reply), a thread resolved and a highlight, written as the scope's agent for
its account (gamma/annotations/hand.py; the scope's ``agent``,
agents.stamp): Gamma AI's in the chat, an MCP connection's at the Comment
level, a computer agent's turn. The places are annotations/places.py's.
Each answers with the id it made and the link list_annotations gives it
(entries.link_of), and a call made again answers what the first one
wrote."""

import json

from ..annotations import anchors
from .base import _annotation_origin, _card_place, _load_scoped_page, _writer, text_diff


def _thread_place(conn, found: dict) -> dict:
    """A thread's place for the approval card: where it was opened, a range
    as it holds now (``anchors.following``)."""
    row = conn.execute("SELECT anchor FROM comments WHERE id = ?", (found["id"],)).fetchone()
    anchor = anchors.parse(row[0] if row else None) or anchors.anchor_of(conn, found["page_id"], found["block_id"])
    anchor = anchors.following(conn, found["page_id"], anchor)
    return _card_place(anchor["kind"], anchor.get("page"), anchors.lines_of(anchor), anchor.get("quote") or "")


def _plan_write(conn, scope: dict, args: dict, *, mark: bool = False):
    """``(plan, None)``: what a writing tool's call writes on, ``{page_id,
    title}`` with a reply's or a resolve's ``thread`` (``thread_id``) or a
    new mark's ``place`` (``places.find``; ``mark``: a highlight's, which
    needs a passage or a range); the page by ``page_id``, a work folder's
    ``path``, a ``block_id``'s page, else a page chat's. ``(None, answer)``
    for a call that cannot write: a thread or a page out of reach, a place
    not found, a passage or a range where the account may not edit
    (``hand.may_mark``)."""
    from ..annotations import hand, places

    thread_id = args.get("thread_id")
    if thread_id and not mark:
        try:
            found = hand.thread(conn, str(thread_id))
        except ValueError as e:
            return None, f"error: {e}"
        loaded, error = _load_scoped_page(conn, scope, {"page_id": found["page_id"]})
        if error:
            return None, error
        return {"page_id": loaded[0], "title": loaded[1], "thread": found}, None
    target = {k: args[k] for k in ("page_id", "path") if isinstance(args.get(k), str) and args[k].strip()}
    if not target and isinstance(args.get("block_id"), str):
        row = conn.execute("SELECT page_id FROM unified_blocks WHERE id = ?", (args["block_id"],)).fetchone()
        target = {"page_id": row[0]} if row else {}
    if not target and scope.get("type") == "page":
        target = {"page_id": scope.get("page_id")}
    if not target:
        return None, "error: name the page (page_id, or a work folder's file by path)" + (
            "" if mark else ", a block_id, or the thread_id to reply in")
    loaded, error = _load_scoped_page(conn, scope, target)
    if error:
        return None, error
    writer = _writer(scope)
    try:
        place = places.find(conn, scope["ws"], loaded[0], args, mark=mark)
        if writer and not place.block_id:
            hand.may_mark(writer, scope["ws"])
    except ValueError as e:
        return None, f"error: {e}"
    return {"page_id": loaded[0], "title": loaded[1], "place": place}, None


def _written(scope: dict, page_id: str, block_id: str, thread_id: str = "") -> str:
    """The link list_annotations gives what a writing tool wrote."""
    from ..annotations.entries import link_of

    return link_of(scope["ws"], page_id, block_id, _annotation_origin(scope), thread_id)


def _preview_comment(conn, scope: dict, args: dict):
    if not isinstance(args.get("body"), str) or not args["body"].strip():
        return None, "error: body is the comment's text"
    plan, answer = _plan_write(conn, scope, args)
    if not plan:
        return None, answer
    if "thread" in plan:
        place = _thread_place(conn, plan["thread"])
    else:
        found = plan["place"]
        place = _card_place(found.kind, found.page, found.lines, found.props.get("quote", ""))
    return {"page_id": plan["page_id"], "title": plan["title"], "reply": "thread" in plan, "place": place,
            "diff": text_diff("", args["body"])}, None


def _run_comment(conn, ws: str, scope: dict, args: dict):
    from ..annotations import hand

    writer = _writer(scope)
    if writer is None:
        return "error: this chat writes no comments", None
    plan, answer = _plan_write(conn, scope, args)
    if not plan:
        return answer, None
    reply = "thread" in plan
    try:
        if reply:
            out = hand.reply(conn, writer, ws, plan["thread"]["id"], args.get("body"))
            where = hand.named(conn, plan["thread"])
        else:
            out = hand.open_thread(conn, writer, ws, plan["page_id"], plan["place"], args.get("body"))
            where = f"{plan['place'].words} of “{plan['title']}”"
    except ValueError as e:
        return f"error: {e}", None
    thread, comment = out["thread"], out["comment"]
    link = _written(scope, thread["page_id"], thread["block_id"], thread["id"])
    ids = f"thread {thread['id']}, comment {comment['id']}"
    if not out["created"]:
        return (f"error: you wrote this {'reply' if reply else 'comment'} on {where} moments ago ({ids}; {link}), "
                "so it was not written again"), None
    said = f"ok — replied on {where}" if reply else f"ok — commented on {where}"
    if out.get("highlight"):
        ids += f", on the new highlight {out['highlight']}"
    return (f"{said}: {ids}. Link: {link}",
            {"kind": "comment", "page_id": thread["page_id"], "block_id": thread["block_id"], "thread_id": thread["id"],
             "comment_id": comment["id"], "title": plan["title"], "reply": reply, "link": link,
             "summary": (f"Replied in a thread in “{plan['title'][:60]}”" if reply
                         else f"Commented on “{plan['title'][:60]}”")})


def _preview_resolve(conn, scope: dict, args: dict):
    plan, answer = _plan_write(conn, scope, {"thread_id": args.get("thread_id") or ""})
    if not plan:
        return None, answer
    if plan["thread"]["resolved_at"]:
        return None, f"ok — thread {plan['thread']['id']} is resolved already"
    return {"page_id": plan["page_id"], "title": plan["title"], "place": _thread_place(conn, plan["thread"])}, None


def _run_resolve(conn, ws: str, scope: dict, args: dict):
    from ..annotations import hand

    writer = _writer(scope)
    if writer is None:
        return "error: this chat resolves no comments", None
    if not args.get("thread_id"):
        return "error: thread_id names the thread to resolve", None
    plan, answer = _plan_write(conn, scope, {"thread_id": args["thread_id"]})
    if not plan:
        return answer, None
    try:
        out = hand.resolve(conn, writer, ws, plan["thread"]["id"])
        where = hand.named(conn, plan["thread"])
    except ValueError as e:
        return f"error: {e}", None
    thread = out["thread"]
    link = _written(scope, thread["page_id"], thread["block_id"], thread["id"])
    if not out["changed"]:
        return f"ok — thread {thread['id']} on {where} was resolved already ({link})", None
    return (f"ok — resolved thread {thread['id']} on {where}. Link: {link}",
            {"kind": "resolve", "page_id": thread["page_id"], "block_id": thread["block_id"],
             "thread_id": thread["id"], "title": plan["title"], "link": link,
             "summary": f"Resolved a thread in “{plan['title'][:60]}”"})


def _preview_highlight(conn, scope: dict, args: dict):
    from ..annotations import hand

    plan, answer = _plan_write(conn, scope, args, mark=True)
    if not plan:
        return None, answer
    try:  # a colour it has not is refused before anyone is asked
        color = hand.stored_color(str(args.get("color") or ""))
    except ValueError as e:
        return None, f"error: {e}"
    found = plan["place"]
    return {"page_id": plan["page_id"], "title": plan["title"],
            "place": _card_place(found.kind, found.page, found.lines, found.props.get("quote", "")),
            **_highlight_shape(found, color, str(args.get("style") or "highlight")),
            **({"diff": text_diff("", args["note"])} if isinstance(args.get("note"), str) and args["note"].strip()
               else {})}, None


def _highlight_shape(found, color: str, style: str) -> dict:
    """Where a highlight would go on its PDF page, for the page to draw it
    while the card waits (frontend markup/ReviewLayer.jsx): ``pdf_page`` and
    a ``shape`` of its rectangles in its position's frame, in the colour the
    highlight will store (``hand.stored_color``, drawn as a stored one is)
    and its style. None for a text file's lines."""
    pos = found.props.get("pdf_position") if found.kind in ("passage", "area") else None
    if not isinstance(pos, dict) or not pos.get("rects"):
        return {}
    rects = [[round(r[k], 1) for k in ("x1", "y1", "x2", "y2")] for r in pos["rects"]]
    return {"pdf_page": pos.get("pageNumber") or found.page,
            "shape": {"kind": "highlight", "frame": [pos.get("width"), pos.get("height")], "rects": rects,
                      "color": color, "style": style,
                      **({"area": True} if pos.get("area") else {})}}


def _run_highlight(conn, ws: str, scope: dict, args: dict):
    from ..annotations import hand

    writer = _writer(scope)
    if writer is None:
        return "error: this chat makes no highlights", None
    plan, answer = _plan_write(conn, scope, args, mark=True)
    if not plan:
        return answer, None
    place = plan["place"]
    try:
        out = hand.highlight(conn, writer, ws, plan["page_id"], place, note=args.get("note") or "",
                             color=str(args.get("color") or ""), style=str(args.get("style") or ""))
    except ValueError as e:
        return f"error: {e}", None
    link = _written(scope, plan["page_id"], out["block_id"])
    where = f"{place.words} of “{plan['title']}”"
    if not out["created"]:
        return f"error: you highlighted {where} already (highlight {out['block_id']}; {link})", None
    area = place.kind == "area"
    # What taking it back needs: a new block's record (docs/dev/ai_tools.md
    # "Reverting a note change"), so the person can undo it from its note or
    # its row, as a note the agent made; one commented on since stops.
    row = conn.execute("SELECT parent_id, position, content, properties FROM unified_blocks WHERE id = ?",
                       (out["block_id"],)).fetchone()
    revert = {"after": row[2] or "", "parent": row[0], "position": row[1],
              "props": json.loads(row[3] or "{}")} if row else None
    return (f"ok — highlighted {where}: highlight {out['block_id']}. Link: {link}",
            {"kind": "highlight", "page_id": plan["page_id"], "block_id": out["block_id"], "title": plan["title"],
             "link": link, **({"area": True} if area else {}), **({"revert": revert} if revert else {}),
             "summary": f"Highlighted {'an area' if area else 'a passage'} in “{plan['title'][:60]}”"})


# A region of a PDF page, as clip_region's box: an area to highlight or
# comment on.
_BOX_ARG = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
            "description": "an area of the PDF page: [x0, y0, x1, y1] as fractions of the page, top-left origin"}


TOOLS = [
    {
        "perm": "comment", "kind": "comment", "scopes": ("folder", "page"), "mutating": True, "commenting": True,
        "run": _run_comment, "preview": _preview_comment,
        "spec": {
            "name": "comment",
            "description": (
                "Write a comment under your own name, for the user: a reply in a comment thread (`thread_id`, "
                "from list_annotations or read_annotation), or a new thread on a place of a page: a PDF passage "
                "(`quote`, verbatim as read_page gives the text, with its `pdf_page`, 1-based), an area of a PDF "
                "page (`box` with its `pdf_page`: a figure, a table), lines of a text "
                "file (`line` and `end_line` as read_page numbers them, with their `quote` when you have it), a "
                "note (`block_id`), or none of these for the page's own thread. Name the page by `page_id`, or a "
                "work folder's file by `path` (a `block_id` finds its page). A new thread on a passage, an area or "
                "lines highlights them. A quote that is not there is refused with the closest passage the page has: "
                "nothing is placed by guess. Use it to answer the user's comments, to say what you changed and "
                "where, or to ask about a passage on that passage. Returns the thread's id and its link."),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string", "description": "the comment's text, Markdown as a note's"},
                    "thread_id": {"type": "string", "description": "the thread to reply in"},
                    "page_id": {"type": "string"},
                    "path": {"type": "string", "description": "a work folder's file by its path, in place of page_id"},
                    "quote": {"type": "string",
                              "description": "the passage, verbatim: a PDF's with pdf_page, a text file's lines"},
                    "pdf_page": {"type": "integer", "minimum": 1},
                    "box": _BOX_ARG,
                    "line": {"type": "integer", "minimum": 1, "description": "a text file's first line, 1-based"},
                    "end_line": {"type": "integer", "minimum": 1, "description": "its last line, inclusive"},
                    "block_id": {"type": "string", "description": "a note of the page (read_block)"},
                },
                "required": ["body"],
            },
        },
    },
    {
        "perm": "comment", "kind": "resolve", "scopes": ("folder", "page"), "mutating": True, "commenting": True,
        "run": _run_resolve, "preview": _preview_resolve,
        "spec": {
            "name": "resolve",
            "description": (
                "Resolve a comment thread once what it asks is done; a resolved thread stays resolved. Never "
                "resolve one you did not address: leave one that needs the user's decision open, with a reply."),
            "parameters": {
                "type": "object",
                "properties": {"thread_id": {"type": "string", "description": "the thread's id (list_annotations)"}},
                "required": ["thread_id"],
            },
        },
    },
    {
        "perm": "comment", "kind": "highlight", "scopes": ("folder", "page"), "mutating": True, "commenting": True,
        "run": _run_highlight, "preview": _preview_highlight,
        "spec": {
            "name": "highlight",
            "description": (
                "Highlight a passage under your own name, for the user: a PDF passage (`quote`, verbatim, with "
                "its `pdf_page`), an area of a PDF page (`box` with its `pdf_page`: a figure, a table, an "
                "equation; drawn as a wash, no style) or lines of a text file (`line`, `end_line`, with their "
                "`quote` when you have it) of the page `page_id` (or a work folder's file by `path`). `note` writes a note on it; `color` and "
                "`style` say how it is drawn (a yellow wash by default). A quote that is not there is refused with "
                "the closest passage: nothing is placed by guess. Highlight only what the user asked you to mark. "
                "Returns the highlight's id and its link."),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "path": {"type": "string", "description": "a work folder's file by its path, in place of page_id"},
                    "quote": {"type": "string", "description": "the passage, verbatim"},
                    "pdf_page": {"type": "integer", "minimum": 1},
                    "box": _BOX_ARG,
                    "line": {"type": "integer", "minimum": 1, "description": "a text file's first line, 1-based"},
                    "end_line": {"type": "integer", "minimum": 1, "description": "its last line, inclusive"},
                    "note": {"type": "string", "description": "a note on the highlight"},
                    "color": {"type": "string", "enum": ["yellow", "green", "blue", "purple"]},
                    "style": {"type": "string", "enum": ["highlight", "underline", "squiggly", "strikeout"],
                              "description": "highlight (a wash, the default), underline, squiggly (wavy) or "
                                             "strikeout"},
                },
                "required": [],
            },
        },
    },
]
