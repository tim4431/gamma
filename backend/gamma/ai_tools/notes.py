"""The note tools: edit_block (replace, append, prepend, patch or a
selection), create_block (a paste: Markdown made into blocks as the
editor's Paste as → Blocks makes them), move_block, delete_block (a
deletion the user can revert), and clip_region, which stores a picture of
part of a PDF page or of the user's handwriting for the notes to embed."""

import json
import re

from ..ai_pictures import ink_picture_url, parse_box, picture_url_of_page, render_region, store_picture
from ..ai_context import is_text_document
from ..blocks_store import block_kind, block_to_dict, fetch_subtree, new_block_id, tree_inserts, write_lock
from ..documents.model import is_pdf, page_document
from ..highlights import is_ink
from ..markdown_import import md_to_blocks
from ..ops import MAX_OPS, after_commit, apply_ops, move_across_pages
from ..notebook import is_sheet
from ..paste_math import dollar_math, paste_math
from ..picture import is_picture, normalize_picture
from ..storage import exists as _upload_exists, upload_refs
from ..text_box import is_text_box
from .base import (_DELETE_REVERT_MAX, _TRUNCATED_MARK, _excerpt, _load_scoped_block, _load_scoped_page,
                   _sibling_gap, _sibling_position, host_free, note_pictures, notes_seen, text_diff)


_BLOCK_CONTENT_MAX = 100_000
_PASTE_LINES = 50  # the new blocks a paste's answer lists, one line each


def _plan_clip_region(conn, scope: dict, args: dict):
    """``(plan, None)``: what clip_region would store — a region of a PDF
    page (``page_id``, ``pdf_page``, optional ``box``, ``ink``) or the
    picture of a handwriting block (``block_id``, ``area``) — with its
    ``page_id``, ``title``, the ``preview`` URL that shows it and
    ``what`` it is in words; ``(None, answer)`` for a call that cannot."""
    if args.get("block_id"):
        loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
        if error:
            return None, error
        block, page_id, title = loaded
        if not (is_ink(block["properties"]) or is_sheet(block["properties"])):
            return None, ('error: that block holds no handwriting — clip_region takes a handwriting '
                          "block's or a page of paper's id, or a PDF page (page_id + pdf_page)")
        whole = str(args.get("area") or "").strip().lower() == "page"
        return {"page_id": page_id, "title": title, "block_id": block["id"], "whole": whole,
                "preview": ink_picture_url(block["id"], whole),
                "what": ("the page of paper" if is_sheet(block["properties"])
                         else "all the handwriting on its page" if whole
                         else f'the handwriting block [{block["id"]}]')}, None
    loaded, error = _load_scoped_page(conn, scope, args)
    if error:
        return None, error
    page_id, title, props, _ = loaded
    if is_text_document(page_document(props)):
        return None, f'error: "{title}" is a text file, with no PDF page to clip from: read it with read_page'
    if not is_pdf(page_document(props)):
        return None, f'error: "{title}" has no PDF attachment to clip from'
    try:
        page_no = int(args.get("pdf_page") or 0)
    except (TypeError, ValueError):
        page_no = 0
    if page_no < 1:
        return None, "error: pdf_page (1-based) is required for a PDF page"
    box = None
    if args.get("box") is not None:
        box = parse_box(args.get("box"))
        if box is None:
            return None, "error: box must be [x0, y0, x1, y1] as fractions of the page (top-left origin), x0 < x1, y0 < y1"
    ink = bool(args.get("ink"))
    return {"page_id": page_id, "title": title, "pdf_page": page_no, "box": box, "ink": ink,
            "preview": picture_url_of_page(page_id, page_no, box, ink),
            "what": (f"PDF page {page_no}" + (" (a region of it)" if box else "")
                     + (" with the handwriting on it" if ink else ""))}, None


def _preview_clip_region(conn, scope: dict, args: dict):
    plan, answer = _plan_clip_region(conn, scope, args)
    if not plan:
        return None, answer
    return {"page_id": plan["page_id"], "title": plan["title"], "what": plan["what"],
            "picture": plan["preview"], **({"pdf_page": plan["pdf_page"]} if plan.get("pdf_page") else {})}, None


def _run_clip_region(conn, ws: str, scope: dict, args: dict):
    """Store a picture of a PDF region or of handwriting as an upload the
    notes can embed (gamma/ai_pictures.py ``store_picture``: the model's
    render size, under the workspace's storage quota) → its URL."""
    plan, answer = _plan_clip_region(conn, scope, args)
    if not plan:
        return answer, None
    if plan.get("block_id"):
        from ..ink_view import picture
        shown = picture(ws, conn, plan["block_id"], plan["page_id"], whole=plan["whole"])
        if shown.get("error"):
            return shown["error"], None
        image = shown["image"]
    else:
        image, total = render_region(ws, conn, plan["page_id"], plan["pdf_page"], plan["box"], ink=plan["ink"])
        if image is None:
            return (f'error: PDF page {plan["pdf_page"]} does not exist — "{plan["title"]}" has {total} pages'
                    if total else "error: the PDF file is not available on this server"), None
    stored = store_picture(ws, image[0], image[1])
    if not stored:
        return "error: the picture could not be stored", None
    url = stored["url"]
    text = (f"ok — stored a {stored['width']}×{stored['height']} px picture of {plan['what']} of "
            f'"{plan["title"]}" as {url}. Put it in a note with create_block or edit_block as a markdown '
            f"image line of its own: ![<a short caption>]({url}).")
    if not scope.get("link_base"):  # the chat's, not an MCP assistant's
        text += " The user sees it in the chat as well when you embed the same line in your reply."
    return text, {"kind": "clip", "page_id": plan["page_id"], "title": plan["title"], "url": url, "picture": url,
                  "upload": url,
                  **({"pdf_page": plan["pdf_page"]} if plan.get("pdf_page") else {}),
                  **({"block_id": plan["block_id"]} if plan.get("block_id") else {}),
                  "summary": f"Clipped a picture of {plan['what'][:40]} in “{plan['title'][:50]}”"}


# What kind of block a deletion takes, for its card and chip: each block
# kind (blocks_store.block_kind) as the user calls it. The frontend
# (chat/approvals.js deletedKind) translates these words.
_DELETED_KIND = {"ink": "handwriting", "sheet": "page of paper", "highlight": "highlight", "link": "link",
                 "text_box": "text box", "picture": "picture", "recording": "recording", "note": "note"}


def _plan_delete_block(conn, scope: dict, args: dict):
    """``(plan, None)``: the ``block`` (on ``page_id`` / ``title``), what
    kind of block it is (``what``, ``_DELETED_KIND``) and the ``rows`` of
    its subtree; ``(None, answer)`` when it cannot be deleted."""
    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return None, error
    block, page_id, page_title = loaded
    if block["parent_id"] == "root":
        return None, ("error: that id is a page — delete_block deletes notes; a page is deleted with "
                      "delete_page, in a folder chat")
    rows = fetch_subtree(conn, block["id"])
    what = _DELETED_KIND.get(block_kind(block["parent_id"], page_id, block["properties"]), "note")
    return {"block": block, "page_id": page_id, "title": page_title, "what": what, "rows": rows}, None


def _preview_delete_block(conn, scope: dict, args: dict):
    plan, answer = _plan_delete_block(conn, scope, args)
    if not plan:
        return None, answer
    block = plan["block"]
    return {"page_id": plan["page_id"], "title": plan["title"], "block_id": block["id"],
            "what": plan["what"], "children": len(plan["rows"]) - 1,
            "diff": [["del", _excerpt(block["content"], 600) or "(empty)"]]}, None


def _run_delete_block(conn, ws: str, scope: dict, args: dict):
    plan, answer = _plan_delete_block(conn, scope, args)
    if not plan:
        return answer, None
    block, page_id, page_title = plan["block"], plan["page_id"], plan["title"]
    write_lock(conn)
    # The subtree as it is under the lock, parent first: what a revert puts back.
    rows = fetch_subtree(conn, block["id"])
    record = [[r[0], r[1], r[2], r[3] or "", r[4] or "{}"] for r in rows]
    revert = {"blocks": record} if len(json.dumps(record)) <= _DELETE_REVERT_MAX else None
    after_commit(ws, conn, apply_ops(
        conn, page_id, [{"op": "delete", "id": block["id"]}], actor=scope.get("actor", ""), client="ai"))
    seen = notes_seen(scope)
    for r in rows:
        seen.pop(r[0], None)
    under = len(rows) - 1
    summary = f"Deleted a {plan['what']} in “{page_title[:60]}”" + (f" with {under} sub-block{'s' if under != 1 else ''}" if under else "")
    text = (f'ok — block [{block["id"]}] deleted' + (f" with its {under} sub-block(s)" if under else "")
            + ("" if revert else " (too large to record for a revert)"))
    return text, {"kind": "delete", "page_id": page_id, "block_id": block["id"], "title": page_title,
                  "summary": summary, **({"revert": revert} if revert else {})}


EDIT_MODES = ("replace", "append", "prepend", "patch", "selection")
# Lines that start a paragraph-level construct: heading, list item, quote,
# table row, fence, display math, rule.
_BLOCKY_LINE = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|\$\$|---)")
# The lines a run of continues on the next line: a list's items, a quote's
# lines, a table's rows.
_RUN_LINE = (("list", re.compile(r"^\s*([-*+]|\d+[.)])\s")), ("quote", re.compile(r"^\s*>")),
             ("table", re.compile(r"^\s*\|")))


def _run_kind(line: str) -> str:
    return next((kind for kind, line_re in _RUN_LINE if line_re.match(line)), "")


def join_block_text(existing: str, addition: str, mode: str) -> str:
    """Existing block text plus an addition, appended or prepended on its own
    line — with a blank line between when either side is a paragraph-level
    construct or multi-line, so markdown keeps rendering as intended, but
    where the line the two meet at is a list item, a quote line or a table
    row on both sides the addition continues that run on the next line, as
    the editor's paste at this level does (a tight list, one quote, one
    table). Mirrored in frontend editor/BlockTree.jsx (the streamed preview
    of an append)."""
    existing = existing.rstrip("\n")
    addition = addition.strip("\n")
    if not existing:
        return addition
    if mode == "prepend":
        head, tail = addition, existing
    else:
        head, tail = existing, addition
    run = _run_kind(head.rsplit("\n", 1)[-1])
    if run and run == _run_kind(tail.split("\n", 1)[0]):
        return head + "\n" + tail
    blank = ("\n" in head or "\n" in tail
             or _BLOCKY_LINE.match(tail) or _BLOCKY_LINE.match(head))
    return head + ("\n\n" if blank else "\n") + tail


def patch_block_text(existing: str, find: str, replacement: str):
    """The block text with its one occurrence of `find` replaced (an empty
    replacement cuts it). Exact match, then a whitespace-relaxed one (any run
    of spaces/newlines matches any other), so a model quoting a wrapped line
    still hits. Returns (text, start) — where the replacement lands — or
    (None, error message). Mirrored in frontend editor/BlockTree.jsx (the
    streamed preview)."""
    if not find:
        return None, "error: patch needs `find` — the exact text to replace or cut"
    n = existing.count(find)
    if n == 1:
        i = existing.index(find)
        return existing[:i] + replacement + existing[i + len(find):], i
    if n == 0:
        loose = re.compile(r"\s+".join(re.escape(part) for part in find.split()))
        hits = list(loose.finditer(existing))
        if len(hits) == 1:
            m = hits[0]
            return existing[:m.start()] + replacement + existing[m.end():], m.start()
        n = len(hits)
    if n == 0:
        return None, "error: `find` text not found in the block — quote it exactly as read_block shows it"
    return None, f"error: `find` matches {n} places in the block — include more surrounding text so it matches once"


def find_selection(scope: dict, label) -> dict | None:
    """The user's note selection a "selection" edit names — by its label
    ("S1"), or the only one when the call gives none."""
    sels = scope.get("note_selections") or []
    label = str(label or "").strip().upper()
    if not label:
        return sels[0] if len(sels) == 1 else None
    return next((s for s in sels if s["label"] == label), None)


def replace_selection_text(existing: str, sel: dict, replacement: str):
    """The block text with the user's selected range replaced: at the
    recorded offsets while they still hold the selected text, else at the
    text's one occurrence (the block changed since the selection). Returns
    (text, start) or (None, error message). Mirrored in frontend
    editor/BlockTree.jsx (the streamed preview)."""
    start, text = sel["from"], sel["text"]
    if existing[start:start + len(text)] != text:
        n = existing.count(text)
        if n != 1:
            return None, ("error: the selected text has changed since the user selected it — "
                          "read the block and use mode patch, or ask the user to select again")
        start = existing.index(text)
    return existing[:start] + replacement + existing[start + len(text):], start


def _plan_edit_block(conn, scope: dict, args: dict):
    """``(plan, None)``: the block (``block``) on its page (``page_id``,
    ``title``), the ``mode``, the block's whole new ``text``, the merge
    ``base`` and, for a selection edit, the selection (``sel``) with the
    offset its replacement lands at (``start``), and the model's ``content``
    as ``written`` (math in dollars). ``(None, answer)`` for a call that
    changes nothing."""
    mode = str(args.get("mode") or "replace").strip().lower()
    sel = None
    if mode == "selection":
        # The block is the selection's; a block_id the model adds must agree.
        sel = find_selection(scope, args.get("selection"))
        if not sel:
            labels = [s["label"] for s in scope.get("note_selections") or []]
            return None, ("error: the user selected no note text for this message — use another mode"
                          if not labels else
                          f"error: name the selection to edit — one of {', '.join(labels)}")
        if args.get("block_id") and args.get("block_id") != sel["block_id"]:
            return None, f'error: selection {sel["label"]} is in block [{sel["block_id"]}], not that one'
        args = {**args, "block_id": sel["block_id"]}
    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return None, error
    block, page_id, page_title = loaded
    if block["parent_id"] == "root":
        return None, "error: that id is a page — page titles change via rename_page"
    content = args.get("content")
    if not isinstance(content, str):
        return None, "error: content must be a string (the block's markdown)"
    content = host_free(scope, content)  # this library's links stored relative, as create_block's
    # read_block shows a picture as its ![caption](url) line, but the block's
    # text is the caption alone: that line written back would put the image
    # Markdown into the caption.
    placed = normalize_picture(block["properties"].get("picture")) if is_picture(block["properties"]) else None
    if placed and f"]({placed['url']})" in content:
        return None, ("error: a picture block's text is its caption alone, not its ![caption](url) line — "
                      "write just the caption (read_block shows the line so you know which file it is)")
    if mode not in EDIT_MODES:
        return None, f"error: mode must be one of {', '.join(EDIT_MODES)}"
    # A replace is the model's rewrite of the text it read: the base of the
    # merge. Without a full read this turn (only a snipped outline line, or
    # an earlier turn's read) it would drop what it never saw.
    base = notes_seen(scope).get(block["id"]) if mode == "replace" else block["content"]
    if mode == "replace" and _TRUNCATED_MARK in content:
        return None, (f'error: content carries read_block\'s truncation marker — call '
                      f'read_block(block_id="{block["id"]}") for the block\'s full text and rewrite that, '
                      "or use mode append / patch")
    if base is None:
        return None, (f'error: read the block first — call read_block(block_id="{block["id"]}") in this '
                      "turn: replace rewrites the whole text, so it must start from the full current text "
                      "(or use mode append / patch, which need no read)")
    # Notes write math in dollars: the model's \( … \) and \[ … \] become them
    # (gamma/paste_math.py) before anything compares, previews or stores the
    # text. A rewrite keeps the pairs the text it read already held; a
    # passage put in elsewhere converts unless it lands in code or math.
    existing = block["content"] or ""
    written = content
    if mode == "replace":
        content = written = dollar_math(content, keep=base)
        if content == base:
            return None, "ok — the block already says that"
    start = None
    if mode in ("patch", "selection"):
        if mode == "patch":
            # Patch rewrites one passage in place: `find` names it, `content`
            # replaces it (empty = cut). The rest of the block is never retyped.
            find = args.get("find")
            if not isinstance(find, str):
                return None, "error: patch needs `find` — the exact text to replace or cut"
            content, start = patch_block_text(existing, find, written)
        else:
            # Exactly the range the user selected; the rest is never retyped.
            content, start = replace_selection_text(existing, sel, written)
        if content is None:
            return None, start
        converted = paste_math(existing, start, written)
        content = content[:start] + converted + content[start + len(written):]
        written = converted
    elif mode != "replace":
        # Append/prepend never retype the existing text: the model sends only
        # the addition, joined on its own line(s). A blank line keeps a new
        # paragraph/heading/list/fence from gluing onto the existing text.
        if not content.strip():
            return None, "error: nothing to add — content is empty"
        # The passage lands on a line of its own, after the text or before it.
        before = existing.rstrip("\n") + "\n" if mode == "append" and existing.strip("\n") else ""
        written = paste_math(before, len(before), content)
        content = join_block_text(existing, written, mode)
    if len(content) > _BLOCK_CONTENT_MAX:
        return None, f"error: content too long (>{_BLOCK_CONTENT_MAX} chars)"
    if content == block["content"]:
        return None, "ok — the block already says that"
    if error := missing_uploads(scope, content, block["content"]):
        return None, error
    return {"block": block, "page_id": page_id, "title": page_title, "mode": mode,
            "text": content, "base": base, "sel": sel, "start": start, "written": written}, None


def _preview_edit_block(conn, scope: dict, args: dict):
    # `text`: the block's whole new text, which the note shows as tracked
    # changes against what it says while the card waits (the card's `diff`
    # cuts long unchanged stretches).
    plan, answer = _plan_edit_block(conn, scope, args)
    if not plan:
        return None, answer
    return {"page_id": plan["page_id"], "title": plan["title"], "block_id": plan["block"]["id"],
            "mode": plan["mode"], "diff": text_diff(plan["block"]["content"] or "", plan["text"]),
            "text": plan["text"], **_preview_pictures(plan["text"], plan["block"]["content"])}, None


def missing_uploads(scope: dict, content: str, before: str = "") -> str:
    """The error for a block text that names a stored file the workspace
    does not hold ("" when every ``/api/uploads/`` reference exists, or
    was in the block already): a model may only embed the URLs it was
    given — a picture's label line, clip_region's result, read_block, and
    over MCP upload_file's."""
    ws = scope.get("ws")
    if not ws or "/api/uploads/" not in (content or ""):
        return ""
    had = upload_refs(before or "", {})
    missing = sorted(n for n in upload_refs(content, {}) if n not in had and not _upload_exists(ws, n))
    if not missing:
        return ""
    given = ("clip_region's or upload_file's result, read_block's text; store a file of your own with "
             "upload_file first" if scope.get("link_base")
             else "a picture's label line, clip_region's result, read_block's text")
    return ("error: no such stored file: " + ", ".join(f"/api/uploads/{n}" for n in missing)
            + f" — embed only the URLs you were given ({given}); never invent an upload name")


MAX_CARD_PICTURES = 6  # pictures an approval card shows of a written text


def _preview_pictures(content: str, before: str = "") -> dict:
    """``{"pictures": [url, …]}`` for the pictures a written text embeds
    that it did not before (the card shows up to MAX_CARD_PICTURES), else
    nothing."""
    had = {url for _, url in note_pictures(before or "")}
    urls = [url for _, url in note_pictures(content) if url not in had]
    return {"pictures": urls[:MAX_CARD_PICTURES]} if urls else {}


def _run_edit_block(conn, ws: str, scope: dict, args: dict):
    plan, answer = _plan_edit_block(conn, scope, args)
    if not plan:
        return answer, None
    block, page_id, page_title, mode, content = (plan["block"], plan["page_id"], plan["title"],
                                                 plan["mode"], plan["text"])
    # `base`: the text the agent edited from — for a replace the text it
    # read, for the other modes the text they were applied to — so a person
    # typing in the same block meanwhile keeps their keystrokes (three-way
    # merge in ops.py). The text just before and after the write, read under
    # the write lock, is what the user's revert takes back (gamma/ai_revert.py).
    write_lock(conn)
    before = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (block["id"],)).fetchone()
    result = apply_ops(
        conn, page_id, [{"op": "set", "id": block["id"], "content": content, "base": plan["base"] or ""}],
        actor=scope.get("actor", ""), client="ai")
    after_commit(ws, conn, result)
    revert = {"before": (before[0] if before else "") or "",
              "after": result["ops"][0].get("content", content)}
    # What the model now knows the block says: its own replace. After the
    # other modes it holds only part of the text — a later replace reads again.
    seen = notes_seen(scope)
    if mode == "replace":
        seen[block["id"]] = content
    else:
        seen.pop(block["id"], None)
    if mode == "selection":
        # A second edit this turn rewrites what the first one left there.
        replacement = plan["written"]
        plan["sel"].update({"from": plan["start"], "to": plan["start"] + len(replacement),
                            "text": replacement})
    verb = {"replace": "Edited", "append": "Appended to", "prepend": "Prepended to",
            "patch": "Edited part of", "selection": "Edited the selection in"}[mode]
    return (f'ok — block [{block["id"]}] updated' + (f" ({mode})" if mode != "replace" else ""),
            {"kind": "edit", "page_id": page_id, "block_id": block["id"], "mode": mode,
             "title": page_title, "summary": f"{verb} a note in “{page_title[:60]}”",
             "revert": revert})


def _paste_tree(content: str, split: bool) -> list:
    """The blocks a create_block's content makes (``{content, children}``):
    the editor's Paste as → Blocks split (markdown_import.md_to_blocks, the
    parser of POST /api/markdown-blocks) — a heading holding what follows
    it, list items nesting by indentation, a paragraph, table, quote, fence
    or $$ math one block — or, with ``split`` off, the content as one
    block; an empty content one empty block."""
    nodes = md_to_blocks(content) if split else []
    return nodes or [{"content": "" if split else content, "children": []}]


def _tree_size(nodes) -> int:
    return sum(1 + _tree_size(n["children"]) for n in nodes)


def _outline(nodes, depth: int = 0) -> list[str]:
    """The paste's blocks as lines, each block's text indented two spaces
    a level, as the approval card and Gamma's MCP question show them."""
    out = []
    for node in nodes:
        out += ["  " * depth + line for line in (node["content"] or "").split("\n")]
        out += _outline(node["children"], depth + 1)
    return out


def _plan_create_block(conn, scope: dict, args: dict):
    """``(plan, None)``: the ``parent`` on its page (``page_id``,
    ``title``), the written ``content`` (math in dollars, this server's
    links host-free), the block trees it makes (``nodes``, ``count`` of
    them) and the ``gap`` between sibling keys they go in, ``after_id``'s
    (``first`` when it is the parent's own id); ``(None, answer)`` for a
    call that cannot add them."""
    loaded, error = _load_scoped_block(conn, scope, args.get("parent_id"))
    if error:
        return None, error.replace("no such block", "no such parent block")
    parent, page_id, page_title = loaded
    content = dollar_math(host_free(scope, str(args.get("content") or "")))  # math in dollars, as edit_block's
    if len(content) > _BLOCK_CONTENT_MAX:
        return None, (f"error: content too long (>{_BLOCK_CONTENT_MAX} chars) — paste it in parts, each part "
                      "after_id the last block the one before made")
    if error := missing_uploads(scope, content):
        return None, error
    nodes = _paste_tree(content, args.get("split") not in (False, "false"))
    count = _tree_size(nodes)
    if count > MAX_OPS:
        return None, (f"error: that makes {count} blocks, and one call pastes at most {MAX_OPS} — paste it in "
                      "parts, each part after_id the last block the one before made")
    gap, error = _sibling_gap(conn, parent["id"], args.get("after_id"))
    if error:
        return None, error
    return {"parent": parent, "page_id": page_id, "title": page_title, "content": content, "nodes": nodes,
            "count": count, "gap": gap, "first": str(args.get("after_id") or "") == parent["id"]}, None


def _preview_create_block(conn, scope: dict, args: dict):
    # `diff`: the blocks' texts as lines, indented by depth (one block: its
    # text as written); `count` the blocks, `after` the sibling they follow
    # or `first`; the pictures and files the paste shows, counted. Where the
    # note draws it while the card waits: under `parent_id`, after
    # `after_id` ("" when first), its `text` as written.
    plan, answer = _plan_create_block(conn, scope, args)
    if not plan:
        return None, answer
    parent, page_id, content = plan["parent"], plan["page_id"], plan["content"]
    after = "" if plan["first"] or not args.get("after_id") else conn.execute(
        "SELECT content FROM unified_blocks WHERE id = ?", (str(args["after_id"]),)).fetchone()[0]
    pictures = {url for _, url in note_pictures(content)}
    text = content if plan["count"] == 1 else "\n".join(_outline(plan["nodes"]))
    return {"page_id": page_id, "title": plan["title"],
            "parent": "" if parent["id"] == page_id else _excerpt(parent["content"]),
            "parent_id": parent["id"], "after_id": "" if plan["first"] else str(args.get("after_id") or ""),
            "text": content,
            "count": plan["count"], **({"first": True} if plan["first"] else {"after": _excerpt(after)} if after else {}),
            "picture_count": len(pictures), "file_count": len(upload_refs(content, {})) - len(pictures),
            "diff": text_diff("", text), **_preview_pictures(content)}, None


def _run_create_block(conn, ws: str, scope: dict, args: dict):
    # Planned under the write lock: the gap is read and its keys minted
    # there, so a block someone adds at that spot meanwhile lands beside the
    # paste, never inside it.
    write_lock(conn)
    plan, answer = _plan_create_block(conn, scope, args)
    if not plan:
        conn.rollback()
        return answer, None
    parent, page_id, page_title = plan["parent"], plan["page_id"], plan["title"]
    # Each block is the account's (the op's actor); the agent that made it is
    # said beside it (scope "agent", agents.stamp: Gamma AI's, a connection's).
    made = {"agent": scope["agent"]} if scope.get("agent") else {}

    def with_ids(node):
        return {"id": new_block_id(), "content": node["content"], "properties": dict(made),
                "children": [with_ids(c) for c in node["children"]]}

    roots = [with_ids(n) for n in plan["nodes"]]
    result = apply_ops(conn, page_id, tree_inserts(conn, parent["id"], roots, plan["gap"]),
                       actor=scope.get("actor", ""), client="ai")
    after_commit(ws, conn, result)
    made_ops = [op for op in result["ops"] if op.get("op") == "insert"]
    seen = notes_seen(scope)
    for op in made_ops:
        seen[op["id"]] = op.get("content") or ""  # the model wrote it: a later replace starts from it
    first = made_ops[0]
    action = {"kind": "create", "page_id": page_id, "block_id": first["id"], "title": page_title}
    if len(made_ops) == 1:
        return (f"ok — created block [{first['id']}]",
                {**action, "summary": f"Added a note in “{page_title[:60]}”",
                 "revert": {"after": first.get("content") or "", "parent": parent["id"],
                            "position": first.get("position")}})
    # A paste of several blocks records every block it made, parent first,
    # as a deletion does: its revert deletes them again (gamma/ai_revert.py).
    record = [[op["id"], op["parent"], op.get("position"), op.get("content") or "", json.dumps(op.get("props") or {})]
              for op in made_ops]
    revert = {"blocks": record} if len(json.dumps(record)) <= _DELETE_REVERT_MAX else None
    depth = {parent["id"]: -1}
    lines = []
    for op in made_ops:
        depth[op["id"]] = depth[op["parent"]] + 1
        lines.append("  " * depth[op["id"]] + f"[{op['id']}] " + _excerpt(op.get("content") or "(empty)", 60))
    shown = lines[:_PASTE_LINES] + ([f"… and {len(lines) - _PASTE_LINES} more (read_block shows them)"]
                                    if len(lines) > _PASTE_LINES else [])
    under = "" if parent["id"] == page_id else f" under “{_excerpt(parent['content'], 60)}”"
    text = (f"ok — added {len(made_ops)} blocks{under} in “{page_title[:60]}”"
            + ("" if revert else " (too large to record for a revert)") + ":\n" + "\n".join(shown))
    return text, {**action, "block_ids": [op["id"] for op in made_ops],
                  "summary": f"Added {len(made_ops)} notes in “{page_title[:60]}”",
                  **({"revert": revert} if revert else {})}


def _plan_move_block(conn, scope: dict, args: dict):
    """``(plan, None)``: the ``block`` from its page (``src_page_id``,
    ``src_title``), the new ``parent`` on its page (``page_id``,
    ``page_title``) and the ``position`` there. ``(None, answer)`` for a
    call that cannot move the block, or leaves it where it is."""
    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return None, error
    block, src_page_id, src_title = loaded
    if block["parent_id"] == "root":
        return None, "error: that id is a page — pages move between folders via move_page"
    loaded, error = _load_scoped_block(conn, scope, args.get("parent_id"))
    if error:
        return None, error.replace("no such block", "no such parent block")
    parent, page_id, page_title = loaded
    subtree_ids = {row[0] for row in fetch_subtree(conn, block["id"])}
    if parent["id"] in subtree_ids:
        return None, "error: cannot move a block into itself or its own children"
    if page_id != src_page_id:
        refusal = cross_page_refusal(conn, block["id"])
        if refusal:
            return None, f"error: {refusal}"
    position, error = _sibling_position(conn, parent["id"], args.get("after_id"),
                                        block["id"])
    if error:
        return None, error
    if parent["id"] == block["parent_id"] and args.get("after_id") in (block["id"], None) \
            and position == block["position"]:
        return None, "ok — the block is already there"
    if parent["id"] == block["parent_id"] and args.get("after_id") == parent["id"] and conn.execute(
            "SELECT id FROM unified_blocks WHERE parent_id = ? ORDER BY position LIMIT 1",
            (parent["id"],)).fetchone()[0] == block["id"]:
        return None, "ok — the block is already first"
    return {"block": block, "src_page_id": src_page_id, "src_title": src_title, "parent": parent,
            "page_id": page_id, "page_title": page_title, "position": position}, None


def cross_page_refusal(conn, block_id: str) -> str:
    """Why the block's subtree can't move to another page ("" when it can).
    The agent's move_block and the user's revert of one (gamma/ai_revert.py)
    both ask."""
    rows = fetch_subtree(conn, block_id)
    # Highlights and link regions anchor to a PDF region of their own
    # paper; on another page that anchor points into the wrong document.
    if any(block_to_dict(row)["kind"] in ("highlight", "link") for row in rows):
        return "highlight blocks are anchored to their paper — they can only move within the same page"
    # So is a text box's or a picture's place, on its PDF page or its
    # sheet, unless the sheet it is on moves along.
    if loose := _loose_placed(rows, block_id):
        return (f"{loose} are placed on their page — they can only move "
                "within the same page, or with the page of paper they are on")
    return ""


def _loose_placed(rows, root_id: str) -> str:
    """What a subtree (``fetch_subtree`` rows) holds that is placed on its
    page and that no sheet within it carries: "text boxes" or "pictures"
    (moved to another page, its place would name a spot on the wrong
    paper), "" for nothing."""
    parent_of, props_of = {}, {}
    for row in rows:
        parent_of[row[0]] = row[1]
        try:
            props_of[row[0]] = json.loads(row[4] or "{}")
        except ValueError:
            props_of[row[0]] = {}
    for bid, props in props_of.items():
        kind = "text boxes" if is_text_box(props) else "pictures" if is_picture(props) else ""
        if not kind:
            continue
        cur = parent_of[bid] if bid != root_id else None
        carried = False
        while cur in props_of:
            if is_sheet(props_of[cur]):
                carried = True
                break
            cur = parent_of[cur] if cur != root_id else None
        if not carried:
            return kind
    return ""


def _preview_move_block(conn, scope: dict, args: dict):
    plan, answer = _plan_move_block(conn, scope, args)
    if not plan:
        return None, answer
    parent, page_id = plan["parent"], plan["page_id"]
    preview = {"page_id": page_id, "title": plan["page_title"], "block_id": plan["block"]["id"],
               "parent": "" if parent["id"] == page_id else _excerpt(parent["content"]), "parent_id": parent["id"],
               "diff": [["ctx", _excerpt(plan["block"]["content"], 600)]]}
    if plan["src_page_id"] != page_id:
        preview.update(src_page_id=plan["src_page_id"], src_title=plan["src_title"])
    return preview, None


def _run_move_block(conn, ws: str, scope: dict, args: dict):
    plan, answer = _plan_move_block(conn, scope, args)
    if not plan:
        return answer, None
    block, src_page_id, src_title, parent, page_id, page_title, position = (
        plan["block"], plan["src_page_id"], plan["src_title"], plan["parent"],
        plan["page_id"], plan["page_title"], plan["position"])
    if page_id == src_page_id:
        after_commit(ws, conn, apply_ops(
            conn, page_id, [{"op": "move", "id": block["id"], "parent": parent["id"],
                             "position": position}], actor=scope.get("actor", ""), client="ai"))
    else:
        move_across_pages(ws, conn, block["id"], parent["id"], position, src_page_id, page_id,
                          actor=scope.get("actor", ""))
    where = (f"page “{page_title[:60]}”" if page_id != src_page_id
             else f"“{page_title[:60]}”")
    # Where it was and where it went, parent and key, for the user's revert
    # and redo (moved on from there since, either asks first).
    action = {"kind": "move", "page_id": page_id, "block_id": block["id"], "title": page_title,
              "summary": f"Moved a note within {where}" if page_id == src_page_id
                         else f"Moved a note “{src_title[:40]}” → {where}",
              "revert": {"parent": block["parent_id"], "position": block["position"],
                         "to_parent": parent["id"], "to_position": position}}
    if page_id != src_page_id:
        action["src_page_id"] = src_page_id
    return f'ok — block [{block["id"]}] moved', action


TOOLS = [
    {
        "perm": "block_edit", "kind": "edit", "scopes": ("folder", "page"), "mutating": True, "run": _run_edit_block,
        "preview": _preview_edit_block,
        "spec": {
            "name": "edit_block",
            "description": (
                "Change one note block's markdown text. `mode` \"replace\" (default) "
                "makes `content` the block's ENTIRE new text — include everything that "
                "should stay; it needs the block's full text read in this turn "
                "(read_block, read_page, or given in the context — a truncated outline "
                "line is not enough); \"append\" / \"prepend\" add `content` after / before the "
                "existing text on its own line (send ONLY the addition — the existing "
                "text is kept untouched, no read needed); \"patch\" replaces just the "
                "passage `find` (quoted exactly as read_block shows it, occurring once) "
                "with `content` — an empty `content` cuts it; \"selection\" replaces "
                "exactly the note text the user selected (`selection`: its label, e.g. "
                "\"S1\"; the block is the selection's) with `content`, touching nothing "
                "else. When the user selected text, a change to it is ALWAYS a "
                "selection edit. Otherwise prefer append when asked "
                "to add, extend, note something, or continue a block; patch to delete, "
                "shorten or correct one part of a long block; replace only for a full "
                "rewrite. Use exact block ids from read_block or the context (never page ids — "
                "titles change via rename_page). Editing a highlight block changes its "
                "note text; the highlighted PDF passage itself cannot be changed."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string"},
                               "mode": {"type": "string",
                                        "enum": list(EDIT_MODES),
                                        "description": "replace (default), append, prepend, patch or selection"},
                               "find": {"type": "string",
                                        "description": "patch only: the exact existing text "
                                                       "to replace or cut (must occur once)"},
                               "selection": {"type": "string",
                                             "description": "selection only: the label of the "
                                                            "user's selection (\"S1\", …)"},
                               "content": {"type": "string",
                                           "description": "replace: the block's full new "
                                                          "markdown; append/prepend: only "
                                                          "the text to add; patch: what "
                                                          "replaces `find` (\"\" to cut it); "
                                                          "selection: what replaces the "
                                                          "selected text. Math as $…$ inline, "
                                                          "$$…$$ display"}},
                "required": ["block_id", "content"],
            },
        },
    },
    {
        "perm": "block_edit", "kind": "create", "scopes": ("folder", "page"), "mutating": True, "run": _run_create_block,
        "preview": _preview_create_block,
        "spec": {
            "name": "create_block",
            "description": (
                "Paste Markdown into the notes as new blocks, as the editor's Paste as → Blocks does: "
                "a heading holds what follows it, list items (`- `, `1. `, `- [ ] `) nest by "
                "indentation, and a paragraph, table, quote, code fence or $$ math stays one block. "
                "One call per paste, at most 500 blocks and 100,000 characters. `parent_id` is a page "
                "id (top-level notes) or a block id (nested under it); `after_id` names the sibling to "
                "land after, or the parent_id itself to land first (default: last). `split: false` "
                "keeps the whole content in one block. A mention of another note is [[block_id]], a "
                "live copy of it ![[block_id]]; a picture or file is the Markdown line the tool that "
                "stored it returned. Returns the new blocks' ids."),
            "parameters": {
                "type": "object",
                "properties": {"parent_id": {"type": "string"},
                               "content": {"type": "string",
                                           "description": "the Markdown to paste; math as $…$ "
                                                          "inline, $$…$$ display"},
                               "after_id": {"type": "string"},
                               "split": {"type": "boolean",
                                         "description": "false keeps the whole content in one block "
                                                        "(default true: the blocks the Markdown makes)"}},
                "required": ["parent_id", "content"],
            },
        },
    },
    {
        "perm": "block_edit", "kind": "move", "scopes": ("folder", "page"), "mutating": True, "run": _run_move_block,
        "preview": _preview_move_block,
        "spec": {
            "name": "move_block",
            "description": (
                "Move a note block (with its children) under a new parent. "
                "`parent_id` is a page id or block id this chat can reach; `after_id` "
                "optionally names the sibling to land after, or the parent_id itself to go "
                "first (default: last). Highlight blocks can move only within their own page."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string"},
                               "parent_id": {"type": "string"},
                               "after_id": {"type": "string"}},
                "required": ["block_id", "parent_id"],
            },
        },
    },
    {
        "perm": "block_edit", "kind": "delete", "scopes": ("folder", "page"), "mutating": True,
        "run": _run_delete_block, "preview": _preview_delete_block,
        "spec": {
            "name": "delete_block",
            "description": (
                "Delete a note block with everything nested under it. Only for what the user "
                "asked to remove (a duplicate, a note they no longer want): prefer edit_block "
                "to delete a part of a block. A highlight block's deletion removes its mark from "
                "the PDF too; a handwriting block's removes the handwriting. The user can revert "
                "it from the reply. Use exact block ids from read_block, never page ids."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string"}},
                "required": ["block_id"],
            },
        },
    },
    {
        # It only stores a file: the note write that shows it asks (STORING_TOOLS).
        "perm": "block_edit", "kind": "clip", "scopes": ("folder", "page"), "mutating": True, "stores": True,
        "run": _run_clip_region, "preview": _preview_clip_region,
        "spec": {
            "name": "clip_region",
            "description": (
                "Store a picture of part of a PDF page, or of the user's handwriting, as a file "
                "the notes can embed, and get its URL. For a PDF: `page_id` + `pdf_page` "
                "(1-based) and optionally `box` ([x0, y0, x1, y1] as fractions of the page, "
                "top-left origin — a figure, a table, an equation; the whole page without it) "
                "and `ink: true` to include the handwriting on it. For handwriting: `block_id` "
                "of a handwriting block or page of paper (read_block labels them), `area` "
                "\"page\" for the whole page. Then put the picture in a note with "
                "create_block or edit_block as its own line, `![caption](<the url>)`. Use it "
                "when the user asks to put a figure, table or their handwriting into their "
                "notes; look at the page first (view_pdf_page) to pick the region."),
            "parameters": {
                "type": "object",
                "properties": {"page_id": {"type": "string"},
                               "pdf_page": {"type": "integer"},
                               "box": {"type": "array", "items": {"type": "number"},
                                       "description": "[x0, y0, x1, y1] page fractions; whole page when left out"},
                               "ink": {"type": "boolean", "description": "draw the user's handwriting on the page"},
                               "block_id": {"type": "string", "description": "a handwriting block or page of paper"},
                               "area": {"type": "string", "enum": ["ink", "page"]}},
                "required": [],
            },
        },
    },
]
