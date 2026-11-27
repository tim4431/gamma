"""Drawing on pages (gamma/ai_draw.py): draw, add_text_box and
place_picture make one block on a PDF page or a page of paper, add_sheet a
page of paper. Each is the block a person's client makes, stamped as the
agent's, inserted through the op path, and recorded for the user's revert
as a new block with its properties (ai_revert _plan_create)."""

import secrets

from ..ai_pictures import ink_picture_url, picture_url_of_page, render_region
from ..blocks_store import load_props, page_root_id, write_lock
from ..ops import after_commit, apply_ops
from ..logbuf import log
from ..notebook import is_sheet
from ..pdf_text import image_part
from .base import _card_place, _drawing_hand, _load_scoped_page, _sibling_position, notes_seen, text_diff


_PLANNERS = {"draw": "plan_draw", "add_text_box": "plan_text_box", "place_picture": "plan_picture"}


def _draw_page(conn, scope: dict, args: dict) -> dict:
    """The page a drawing call names: ``page_id``, else the page of its
    ``sheet_id``, else a page chat's own."""
    if isinstance(args.get("page_id"), str) and args["page_id"].strip():
        return {"page_id": args["page_id"].strip()}
    if isinstance(args.get("sheet_id"), str) and args["sheet_id"].strip():
        root = page_root_id(conn, args["sheet_id"].strip())
        return {"page_id": root} if root else {}
    return {"page_id": scope.get("page_id")} if scope.get("type") == "page" else {}


def _plan_placed(conn, scope: dict, args: dict, tool: str):
    """``(made, None)``: what ``tool`` would place (``ai_draw.Made``) on the
    surface the call names; ``(None, answer)`` for a call that cannot."""
    from .. import ai_draw

    target = _draw_page(conn, scope, args)
    if not target:
        return None, "error: name the page (page_id), or the page of paper (sheet_id)"
    loaded, error = _load_scoped_page(conn, scope, target)
    if error:
        return None, error
    page_id, title, props, _ = loaded
    try:
        surface = ai_draw.surface(conn, scope["ws"], page_id, title, props, args)
        return getattr(ai_draw, _PLANNERS[tool])(conn, scope["ws"], surface, args), None
    except ValueError as e:
        return None, f"error: {e}"


def _surface_picture(surface) -> str:
    """The URL of the surface as the user sees it: a PDF page with what is
    placed on it and the handwriting, or the page of paper."""
    if surface.kind == "pdf-page":
        return picture_url_of_page(surface.page_id, surface.page, None, ink=True)
    return ink_picture_url(surface.sheet_id, True)


def _preview_placed(tool: str):
    def preview(conn, scope: dict, args: dict):
        made, answer = _plan_placed(conn, scope, args, tool)
        if not made:
            return None, answer
        s = made.surface
        out = {"page_id": s.page_id, "title": s.title, "what": made.what, "picture": _surface_picture(s),
               **({"pdf_page": s.page} if s.page else {"sheet": True})}
        if made.anchors:
            quote, page = made.anchors[0]
            out["place"] = _card_place("passage", page, quote=quote)
        if made.content:
            out["diff"] = text_diff("", made.content)
        if tool == "place_picture":
            out["pictures"] = [made.props["picture"]["url"]]
        if s.kind == "canvas":
            out["sheet_id"] = s.sheet_id
        out["shape"] = _shape_of(made)
        return out, None
    return preview


# The most points a drawn stroke of a card's shape carries: enough to draw
# the proposal on its page, a fraction of a long curve's samples.
_SHAPE_POINTS = 240


def _shape_of(made) -> dict:
    """What a placing call would put on its surface, for the surface to draw
    while the card waits (frontend markup/ReviewLayer.jsx), in the
    surface's points (``frame``): a drawing's strokes as point lists, a text
    box's or a picture's frame, with its text or its picture."""
    from .. import ink as inkmod

    frame = [made.surface.width, made.surface.height]
    if made.ink_file:
        strokes = []
        for stroke in inkmod.parse_ink(made.ink_file).strokes:
            points = [[round(p["x"], 1), round(p["y"], 1)] for p in inkmod.decode_stroke(stroke)]
            step = max(1, len(points) // _SHAPE_POINTS)
            points = points[::step] + ([points[-1]] if (len(points) - 1) % step else [])
            strokes.append({"color": stroke.color, "size": stroke.size, "points": points,
                            **({"dash": stroke.dash} if stroke.dash else {}),
                            **({"fill": True} if stroke.fill else {})})
        return {"kind": "ink", "frame": frame, "strokes": strokes}
    placed = made.props.get("text_box") or made.props.get("picture")
    if not placed:
        return {}
    box = {k: round(float(placed[k]), 1) for k in ("x", "y", "w", "h")}
    if made.props.get("picture"):
        return {"kind": "picture", "frame": frame, "box": box, "url": placed["url"]}
    return {"kind": "box", "frame": frame, "box": box, "text": made.content,
            **{k: placed[k] for k in ("size", "color", "bg") if placed.get(k)}}


def _insert_made(conn, ws: str, scope: dict, writer, page_id: str, parent: str, made, props: dict,
                 after_id=None) -> tuple:
    """The block ``made`` describes, inserted under ``parent`` (after
    ``after_id``, else last) as the writer's account, stamped as its agent:
    ``(block_id, position, props, None)``, or ``(…, answer)`` for a refused
    op."""
    from ..ops import OpError

    write_lock(conn)
    position, error = _sibling_position(conn, parent, after_id)
    if error:
        conn.rollback()
        return "", "", props, error
    props = {**props, "agent": writer.stamp}
    block_id = secrets.token_urlsafe(9)
    try:
        after_commit(ws, conn, apply_ops(
            conn, page_id, [{"op": "insert", "id": block_id, "parent": parent, "position": position,
                             "content": made.content, "props": props}], actor=writer.user_id, client="ai"))
    except OpError as e:
        conn.rollback()
        return "", "", props, f"error: {e.detail}"
    if made.content:
        notes_seen(scope)[block_id] = made.content  # the model wrote it
    return block_id, position, props, None


def _placed_picture(ws: str, conn, scope: dict, tool: str, surface, block_id: str, props: dict):
    """``(url, images)``: the chip's picture of what was placed and, for a
    model that reads pictures, the picture itself, so it can check its
    work. Optional: a render that fails leaves the change as it is."""
    from ..ink_view import picture

    try:
        if tool == "draw":
            url, shown = ink_picture_url(block_id), picture(ws, conn, block_id, surface.page_id).get("image")
        elif surface.kind == "canvas":
            url, shown = ink_picture_url(surface.sheet_id, True), picture(ws, conn, surface.sheet_id,
                                                                          surface.page_id).get("image")
        else:
            frame = props.get("text_box") or props.get("picture")
            pad = 36
            box = (max(0.0, (frame["x"] - pad) / surface.width), max(0.0, (frame["y"] - pad) / surface.height),
                   min(1.0, (frame["x"] + frame["w"] + pad) / surface.width),
                   min(1.0, (frame["y"] + frame["h"] + pad) / surface.height))
            box = tuple(round(v, 4) for v in box)
            url = picture_url_of_page(surface.page_id, surface.page, box, ink=True)
            shown = render_region(ws, conn, surface.page_id, surface.page, box, ink=True)[0]
    except Exception as e:  # the change stands; its picture is extra
        log.warning(f"[ai_tools] {tool}'s picture failed: {e}")
        return "", []
    return url, ([image_part(shown)] if shown and scope.get("pictures") is not False else [])


def _placed_after(scope: dict, images) -> str:
    """What a placing call's answer says after it: to check the picture
    (when one is attached), and how the change is taken back — from the
    reply in Gamma's chat; over MCP (``link_base``), which has no reply, by
    undo_change with the change id the adapter adds (gamma/agent_changes.py)."""
    mcp = bool(scope.get("link_base"))
    check = (" A picture of the page as it is now is attached: check that it is where you meant"
             + ("; if not, undo_change takes it back to try again." if mcp
                else ", and delete_block it to try again if not.")) if images else ""
    return check + ("" if mcp else " The user can revert it from the reply.")


_PLACED_WORDS = {"draw": ("drew {what}", "Drew {what} on “{title}”"),
                 "add_text_box": ("added a text box", "Added a text box to “{title}”"),
                 "place_picture": ("placed a picture", "Placed a picture on “{title}”")}


def _run_placed(tool: str):
    def run(conn, ws: str, scope: dict, args: dict):
        from fastapi import HTTPException

        from .. import ink as inkmod
        from ..storage import store_file

        writer, answer = _drawing_hand(scope, ws)
        if writer is None:
            return answer, None
        made, answer = _plan_placed(conn, scope, args, tool)
        if not made:
            return answer, None
        s, props = made.surface, dict(made.props)
        if made.ink_file is not None:
            try:
                name, _ = store_file(ws, inkmod.dumps(made.ink_file), ".ink")
            except HTTPException as e:
                return f"error: {e.detail}", None
            props = {"ink_url": f"/api/uploads/{name}", **props}
        block_id, position, props, answer = _insert_made(conn, ws, scope, writer, s.page_id, s.parent, made, props)
        if answer:
            return answer, None
        url, images = _placed_picture(ws, conn, scope, tool, s, block_id, props)
        done, summary = _PLACED_WORDS[tool]
        text = (f"ok — {done.format(what=made.what)} on {s.words} of “{s.title}”: block [{block_id}]."
                + _placed_after(scope, images))
        action = {"kind": "create", "page_id": s.page_id, "block_id": block_id, "title": s.title,
                  "summary": summary.format(what=made.what[:40], title=s.title[:50]),
                  **({"pdf_page": s.page} if s.page else {}), **({"picture": url} if url else {}),
                  "revert": {"after": made.content, "parent": s.parent, "position": position, "props": props}}
        if images:
            action["images"] = images
        return text, action
    return run


def _plan_add_sheet(conn, scope: dict, args: dict):
    """``(plan, None)``: the page of paper ``ai_draw.plan_sheet`` makes, on
    ``page_id`` (a page chat's own by default), after the sheet
    ``after_id`` names, else last on the page; ``(None, answer)``."""
    from .. import ai_draw

    target = _draw_page(conn, scope, {"page_id": args.get("page_id")})
    if not target:
        return None, "error: name the page (page_id)"
    loaded, error = _load_scoped_page(conn, scope, target)
    if error:
        return None, error
    page_id, title = loaded[0], loaded[1]
    parent, after = page_id, str(args.get("after_id") or "").strip() or None
    if after:
        row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ? AND page_id = ?",
                           (after, page_id)).fetchone()
        if not row or not is_sheet(load_props(row[1])):
            return None, "error: after_id names no page of paper of this page: read_block labels them"
        parent = row[0]
    try:
        made = ai_draw.plan_sheet(args)
    except ValueError as e:
        return None, f"error: {e}"
    return {"made": made, "page_id": page_id, "title": title, "parent": parent, "after": after}, None


def _preview_add_sheet(conn, scope: dict, args: dict):
    plan, answer = _plan_add_sheet(conn, scope, args)
    if not plan:
        return None, answer
    return {"page_id": plan["page_id"], "title": plan["title"], "what": plan["made"].what,
            **({"diff": text_diff("", plan["made"].content)} if plan["made"].content else {})}, None


def _run_add_sheet(conn, ws: str, scope: dict, args: dict):
    writer, answer = _drawing_hand(scope, ws)
    if writer is None:
        return answer, None
    plan, answer = _plan_add_sheet(conn, scope, args)
    if not plan:
        return answer, None
    made, page_id, title = plan["made"], plan["page_id"], plan["title"]
    block_id, position, props, answer = _insert_made(conn, ws, scope, writer, page_id, plan["parent"], made,
                                                     made.props, plan["after"])
    if answer:
        return answer, None
    return (f"ok — added {made.what} to “{title}”: sheet [{block_id}]. Draw or write on it with sheet_id "
            f"\"{block_id}\"." + _placed_after(scope, []),
            {"kind": "create", "page_id": page_id, "block_id": block_id, "title": title,
             "summary": f"Added a page of paper to “{title[:60]}”", "picture": ink_picture_url(block_id, True),
             "revert": {"after": made.content, "parent": plan["parent"], "position": position, "props": props}})


TOOLS = [
    {
        "perm": "draw", "kind": "create", "scopes": ("folder", "page"), "mutating": True,
        "run": _run_placed("draw"), "preview": _preview_placed("draw"),
        "spec": {
            "name": "draw",
            "description": (
                "Draw shapes on a page for the user, as one new handwriting group they can revert: on a PDF page "
                "(`page_id` + `pdf_page`) or a page of paper (`sheet_id`; read_block labels them). Places are "
                "fractions of the page from its top-left corner (0 to 1), as clip_region's box. On a PDF page "
                "anchor to the text where you can: `around` a quote (verbatim, on that page) puts a rect or an "
                "ellipse around the passage, and `from_quote` / `to_quote` end a line or an arrow at one. Kinds: "
                "line and arrow (`from`, `to`), rect and ellipse (`box` or `around`), triangle, polygon and curve "
                "(`points`; a curve's `closed` and `smooth`). Look at the page first (view_pdf_page) to see where "
                "things are; the result shows the drawing. Draw only what the user asked for; words go in "
                "add_text_box."),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "pdf_page": {"type": "integer", "minimum": 1},
                    "sheet_id": {"type": "string", "description": "a page of paper, in place of pdf_page"},
                    "shapes": {
                        "type": "array", "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["line", "arrow", "rect", "ellipse", "triangle",
                                                                    "polygon", "curve"]},
                                "box": {"type": "array", "items": {"type": "number"},
                                        "description": "rect, ellipse: [x0, y0, x1, y1] page fractions"},
                                "around": {"type": "string",
                                           "description": "rect, ellipse: a passage's quote to draw around"},
                                "from": {"type": "array", "items": {"type": "number"},
                                         "description": "line, arrow: [x, y] page fractions"},
                                "to": {"type": "array", "items": {"type": "number"},
                                       "description": "line, arrow: [x, y]; an arrow's tip"},
                                "from_quote": {"type": "string", "description": "a passage the line starts at"},
                                "to_quote": {"type": "string", "description": "a passage it ends at (the tip)"},
                                "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
                                           "description": "triangle, polygon, curve: [x, y] page fractions in "
                                                          "order, a curve up to 31"},
                                "closed": {"type": "boolean"},
                                "smooth": {"type": "boolean"},
                                "fill": {"type": "boolean", "description": "fill a closed shape lightly"},
                                "color": {"type": "string"},
                                "width": {"type": "number"},
                                "dash": {"type": "string", "enum": ["solid", "dashed", "dotted"]},
                            },
                            "required": ["kind"],
                        },
                    },
                    "color": {"type": "string", "description": "red (the default), black, blue, green, orange, "
                                                               "purple, yellow, gray, pink, teal, or #rrggbb"},
                    "width": {"type": "number", "description": "line width in points, 2 by default"},
                    "dash": {"type": "string", "enum": ["solid", "dashed", "dotted"]},
                },
                "required": ["shapes"],
            },
        },
    },
    {
        "perm": "draw", "kind": "create", "scopes": ("folder", "page"), "mutating": True,
        "run": _run_placed("add_text_box"), "preview": _preview_placed("add_text_box"),
        "spec": {
            "name": "add_text_box",
            "description": (
                "Put typed text on a page for the user, as a text box they can move and revert: on a PDF page "
                "(`page_id` + `pdf_page`) or a page of paper (`sheet_id`). `near` a quote of the PDF page puts "
                "it in the nearest empty space beside that passage (a margin note), else `at` places its "
                "top-left corner ([x, y] page fractions). `text` is Markdown, math as $…$. Without `width` (a "
                "fraction of the page) the box is as wide as its text. Keep it short: a longer answer goes in "
                "the notes."),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "pdf_page": {"type": "integer", "minimum": 1},
                    "sheet_id": {"type": "string", "description": "a page of paper, in place of pdf_page"},
                    "text": {"type": "string"},
                    "near": {"type": "string", "description": "a passage's quote, verbatim, to put the box beside"},
                    "at": {"type": "array", "items": {"type": "number"},
                           "description": "[x, y] of its top-left corner, page fractions"},
                    "width": {"type": "number", "description": "the box's width, a fraction of the page"},
                    "size": {"type": "integer", "enum": [9, 12, 16, 24, 36], "description": "font size, 12 by default"},
                    "color": {"type": "string", "description": "the text's colour: black (the default), red, "
                                                               "blue, … or #rrggbb"},
                    "background": {"type": "string", "enum": ["none", "white", "yellow", "blue"]},
                },
                "required": ["text"],
            },
        },
    },
    {
        "perm": "draw", "kind": "create", "scopes": ("folder", "page"), "mutating": True,
        "run": _run_placed("place_picture"), "preview": _preview_placed("place_picture"),
        "spec": {
            "name": "place_picture",
            "description": (
                "Place a stored picture on a page for the user: one clip_region stored, or one a note embeds "
                "(`url`, /api/uploads/…), on a PDF page (`page_id` + `pdf_page`) or a page of paper (`sheet_id`). "
                "`box` ([x0, y0, x1, y1] page fractions) fits it inside, its shape kept; else `width` (a fraction "
                "of the page, 0.3 by default) with `near` a quote, beside that passage in empty space, or `at` "
                "its top-left corner. `caption` is its text."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "page_id": {"type": "string"},
                    "pdf_page": {"type": "integer", "minimum": 1},
                    "sheet_id": {"type": "string", "description": "a page of paper, in place of pdf_page"},
                    "box": {"type": "array", "items": {"type": "number"}},
                    "near": {"type": "string", "description": "a passage's quote, verbatim, to put it beside"},
                    "at": {"type": "array", "items": {"type": "number"}},
                    "width": {"type": "number"},
                    "caption": {"type": "string"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "perm": "draw", "kind": "create", "scopes": ("folder", "page"), "mutating": True,
        "run": _run_add_sheet, "preview": _preview_add_sheet,
        "spec": {
            "name": "add_sheet",
            "description": (
                "Add a page of paper to a page for the user to write on, or for draw and add_text_box to use "
                "(`sheet_id`, the result's id): after the page of paper `after_id` names, else at the end. "
                "`size` a4 (the default), letter or a5, `landscape` turns it; `pattern` blank, ruled, grid or "
                "dots; `color` white, cream, grey or dark; `title` its name in the notes."),
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "after_id": {"type": "string"},
                    "title": {"type": "string"},
                    "size": {"type": "string", "enum": ["a4", "letter", "a5"]},
                    "landscape": {"type": "boolean"},
                    "pattern": {"type": "string", "enum": ["blank", "ruled", "grid", "dots"]},
                    "color": {"type": "string", "enum": ["white", "cream", "grey", "dark"]},
                },
                "required": [],
            },
        },
    },
]
