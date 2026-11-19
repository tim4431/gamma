"""view_pdf_page, view_ink and view_image: a PDF page, the user's
handwriting and a note's pictures, shown to the model as pictures (a model
that reads text only gets none of them: ``registry.PICTURE_TOOLS``)."""

import json

from ..ai_pictures import ink_picture_url, picture_url_of_page, read_stored, upload_name
from ..ai_context import is_text_document, pdf_path
from ..documents.model import is_pdf, page_document, pdf_file_hash
from ..notebook import is_sheet
from ..pdf_text import RENDER_MAX_SIDE, image_part, render_page
from ..picture import caption_text, is_picture, normalize_picture
from ..storage import exists as _upload_exists
from .base import _PAGE_ID_ARG, _load_scoped_block, _load_scoped_page, gamma_link, note_pictures


def _run_view_pdf_page(conn, ws: str, scope: dict, args: dict):
    """One page of the page's PDF as a picture for a vision model — the way
    to read a scan with no usable text layer, or a figure. The image rides on
    the action under ``images`` (``[(media_type, base64)]``); run_agent_tool
    lifts it off the chip so it reaches the model's tool result but never the
    saved chat."""
    loaded, error = _load_scoped_page(conn, scope, args)
    if error:
        return error, None
    page_id, title, props, _ = loaded
    view = page_document(props)
    if is_text_document(view):
        return f'"{title}" is a text file: read it with read_page', None
    if not is_pdf(view):
        return f'"{title}" has no PDF attachment to look at', None
    try:
        page_no = max(1, int(args.get("pdf_page", 1)))
    except (TypeError, ValueError):
        page_no = 1
    path = pdf_path(ws, pdf_file_hash(view))
    if not path:
        return "error: the PDF file is not available on this server", None
    image, total = render_page(str(path), page_no, RENDER_MAX_SIDE)
    if not total:
        return "error: the PDF could not be rendered", None
    if image is None:
        return f'error: PDF page {page_no} does not exist — "{title}" has {total} pages', None
    _, _, width, height = image
    link = gamma_link(scope, page_id, pdf_page=page_no)
    result = (f'PDF page {page_no} of {total} of "{title}" is attached as a {width}×{height} px '
              "picture: read it visually and cite it as PDF page "
              f"{page_no}{f' ({link})' if link else ''}. The picture is not kept in the chat history — "
              "call again to look at it later.")
    url = picture_url_of_page(page_id, page_no)
    if not scope.get("link_base"):
        # The chat renders the same-origin picture route; an MCP client cannot.
        result += (" To show the user this page in your reply, embed it as a markdown image: "
                   f"![PDF page {page_no} of {title[:60]}]({url}) (add &box=x0,y0,x1,y1 in page fractions "
                   "for a region of it).")
    return result, {"kind": "view", "page_id": page_id, "pdf_page": page_no,
                    "summary": f"Looked at p. {page_no} of “{title[:60]}”",
                    "picture": url, "images": [image_part(image)]}


def _run_view_ink(conn, ws: str, scope: dict, args: dict):
    """Handwriting as a picture (gamma/ink_view.py): a group's strokes on
    their PDF page or sheet of paper, cropped to them, or with ``area``
    "page" the whole page with all its handwriting; a sheet's id shows the
    sheet. Like view_pdf_page's page, the picture rides on the chip's
    ``images`` and never reaches the saved chat."""
    from ..ink_view import picture

    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return error, None
    block, page_id, page_title = loaded
    whole = str(args.get("area") or "").strip().lower() == "page"
    shown = picture(ws, conn, block["id"], page_id, whole=whole)
    if shown.get("error"):
        return shown["error"], None
    _, _, width, height = shown["image"]
    page_no = shown["pdf_page"]
    sheet = is_sheet(block["properties"])
    if sheet:
        what = f'The page of paper [{block["id"]}] in "{page_title}", with all the handwriting on it,'
    else:
        where = f'PDF page {page_no} of "{page_title}"' if page_no else f'a page of paper in "{page_title}"'
        what = (f"All the handwriting on {where}" if shown["whole"]
                else f'Handwriting block [{block["id"]}] on {where} (cropped to it, with a margin)')
    caption = (block["content"] or "").strip()
    picture_url = ink_picture_url(block["id"], whole)
    text = (f"{what} is attached as a {width}×{height} px picture ({shown['strokes']} strokes). "
            + ("The PDF page could not be copied, so the strokes are drawn on blank paper. "
               if shown["bare"] else "")
            + ("" if sheet else f"Its caption (the block's text): {json.dumps(caption[:500], ensure_ascii=False)}. "
               if caption else "It has no caption yet. ")
            + "Read the strokes visually and say when an answer comes from handwriting; a word "
            "you cannot read is [illegible], never a guess. The picture is not kept in the chat "
            "history — call again to look at it later."
            + ("" if scope.get("link_base") else  # the chat renders the same-origin URL; an MCP client cannot
               f" To show the user this picture in your reply, embed it as a markdown image: ![handwriting]({picture_url})"))
    chip = {"kind": "ink", "page_id": page_id, "block_id": block["id"],
            "summary": f"Looked at handwriting in “{page_title[:60]}”" + (f" p. {page_no}" if page_no else ""),
            "picture": picture_url, "images": [image_part(shown["image"])]}
    if page_no:
        chip["pdf_page"] = page_no
    return text, chip


MAX_VIEW_IMAGES = 6  # pictures one view_image call shows


def _placed_as(pic: dict) -> str:
    """How a picture placed on a page differs from its file, in words: the
    crop as the part of the image it keeps, the turn, the flips, the
    opacity; "" for one shown whole, upright, unflipped and opaque."""
    crop, bits = pic["crop"], []
    if (crop["x"], crop["y"], crop["w"], crop["h"]) != (0, 0, 1, 1):
        bits.append("cropped to {:.0%}–{:.0%} across and {:.0%}–{:.0%} down".format(
            crop["x"], crop["x"] + crop["w"], crop["y"], crop["y"] + crop["h"]))
    if pic["angle"]:
        bits.append(f"turned {abs(pic['angle']):g}° {'clockwise' if pic['angle'] > 0 else 'anticlockwise'}")
    bits += [f"flipped {axis}" for axis, on in (("left to right", pic["flip_h"]), ("top to bottom", pic["flip_v"]))
             if on]
    if "opacity" in pic:
        bits.append(f"at {pic['opacity']:.0%} opacity")
    return ", ".join(bits)


def _run_view_image(conn, ws: str, scope: dict, args: dict):
    """The pictures a note block embeds, for a vision model: read_block
    shows them as ``![alt](/api/uploads/…)`` lines, which say nothing of
    what the picture holds. A picture block's own picture comes first (its
    caption the alt text), the whole file, with how the page shows it
    otherwise. Up to MAX_VIEW_IMAGES of the block's pictures, or the one
    ``index`` names (1-based). Like view_pdf_page's page they ride on the
    chip's ``images`` and never reach the saved chat."""
    loaded, error = _load_scoped_block(conn, scope, args.get("block_id"))
    if error:
        return error, None
    block, page_id, page_title = loaded
    placed = normalize_picture(block["properties"]["picture"]) if is_picture(block["properties"]) else None
    found = ([(caption_text(block["content"]), placed["url"])] if placed else []) + note_pictures(block["content"])
    if not found:
        return (f'error: block [{block["id"]}] embeds no picture — a note\'s pictures are '
                "![alt](/api/uploads/…) lines in its markdown, and a picture placed on a page is a "
                "block of its own (read_block shows both); pass the id of a block that has one", None)
    try:
        index = int(args.get("index") or 0)
    except (TypeError, ValueError):
        index = 0
    if index:
        if not 1 <= index <= len(found):
            return f'error: block [{block["id"]}] has {len(found)} picture(s) — index must be 1–{len(found)}', None
        chosen = [(index, *found[index - 1])]
    else:
        chosen = [(n, alt, url) for n, (alt, url) in enumerate(found[:MAX_VIEW_IMAGES], start=1)]
    images, lines = [], []
    for n, alt, url in chosen:
        shown = read_stored(ws, url)
        if not shown:
            why = ("its file does not read as a picture (an SVG that cannot be drawn, or a damaged or oversized "
                   "file)" if _upload_exists(ws, upload_name(url)) else "its file is missing on this server")
            lines.append(f"Picture {n} ({url}): {why}.")
            continue
        images.append(image_part(shown))
        on_page = _placed_as(placed) if placed and n == 1 else ""
        lines.append(f"Picture {n} of {len(found)}" + (f', alt text "{alt[:120]}"' if alt else "")
                     + f", {shown[2]}×{shown[3]} px, {url}: attached."
                     + (f" This is the whole file; the page shows it {on_page}." if on_page else ""))
    if not images:
        return "error: " + " ".join(lines), None
    more = len(found) - len(chosen) if not index else 0
    text = (f'Pictures embedded in block [{block["id"]}] of "{page_title}":\n' + "\n".join(lines)
            + (f"\n(+{more} more — call again with index=N for one of them.)" if more > 0 else "")
            + "\nRead them visually and say when an answer comes from a picture. They are not kept "
            "in the chat history — call again to look at them later."
            + ("" if scope.get("link_base") else  # the chat renders the same-origin URL; an MCP client cannot
               " To show the user one in your reply, embed its URL as a markdown image: ![…](<url>)."))
    return text, {"kind": "image", "page_id": page_id, "block_id": block["id"],
                  "summary": (f"Looked at {len(images)} picture{'s' if len(images) != 1 else ''} in a note of "
                              f"“{page_title[:60]}”"),
                  "picture": chosen[0][2], "images": images}


TOOLS = [
    {
        "perm": "view", "kind": "view", "scopes": ("folder", "page"), "mutating": False, "run": _run_view_pdf_page,
        "spec": {
            "name": "view_pdf_page",
            "description": (
                "Look at one page of a page's PDF attachment as a picture (a text file "
                "is read with read_page). Use it when "
                "the extracted text is missing or garbled (a scanned document with no "
                "usable text layer), or when a figure, table, diagram, equation layout "
                "or handwriting matters and the text alone cannot answer. `pdf_page` is "
                "1-based. A picture costs many tokens, so find the right page first "
                "(search_library, read_page) and look only at the pages you need."),
            "parameters": {
                "type": "object",
                "properties": {**_PAGE_ID_ARG, "pdf_page": {"type": "integer"}},
                "required": ["page_id", "pdf_page"],
            },
        },
    },
    {
        "perm": "view", "kind": "ink", "scopes": ("folder", "page"), "mutating": False, "run": _run_view_ink,
        "spec": {
            "name": "view_ink",
            "description": (
                "Look at the user's handwriting as a picture: a handwriting block's strokes "
                "(read_block labels it \"handwriting on p. N\" or \"on the page of paper above\") "
                "drawn where they were written — on their PDF page or their page of paper — "
                "cropped to them with a margin. `area` \"page\" shows the whole PDF page or page "
                "of paper with all its handwriting instead; the id of a page of paper (\"a page of "
                "paper: the handwriting under it…\") shows that page. Use it to read or transcribe "
                "handwriting, or to see what the user drew or marked on a PDF page. A picture "
                "costs many tokens: look only at the blocks you need. A recording block (read_block "
                "labels it \"a recording, m:ss\") is audio the user recorded, not handwriting: its "
                "text is its title, and its sub-blocks marked (transcript) are the words spoken, in "
                "the order they were said."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string",
                                            "description": "a handwriting block's or a page of paper's id"},
                               "area": {"type": "string", "enum": ["ink", "page"],
                                        "description": "ink (default): cropped to the handwriting; "
                                                       "page: the whole page it is on"}},
                "required": ["block_id"],
            },
        },
    },
    {
        "perm": "view", "kind": "image", "scopes": ("folder", "page"), "mutating": False, "run": _run_view_image,
        "spec": {
            "name": "view_image",
            "description": (
                "Look at the pictures a note block embeds, or a picture placed on a page (read_block "
                "labels it \"(picture on …)\"). read_block shows a picture only as "
                "its markdown, `![alt](/api/uploads/…)`, which says nothing of what it holds; "
                "this shows you the pictures of `block_id` (up to " + str(MAX_VIEW_IMAGES) + ", or the one "
                "`index` names, 1-based). Use it when a question is about a figure, screenshot or "
                "photo the user put in their notes. A picture costs many tokens: look only at "
                "the blocks you need."),
            "parameters": {
                "type": "object",
                "properties": {"block_id": {"type": "string", "description": "a note block's id"},
                               "index": {"type": "integer",
                                         "description": "one picture of the block, 1-based (default: all)"}},
                "required": ["block_id"],
            },
        },
    },
]
