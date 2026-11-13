"""Where a thread was opened: the anchor a root comment keeps
(``comments.anchor``, docs/dev/feedback.md "The anchor").

::

    {"v": 1, "kind": "passage" | "area" | "ink" | "region" | "note" | "file",
     "block": "<the block the place is read from>",
     "doc_id": "<the file the place was measured on>" | "",
     "ext": ".pdf" | ".png" | … | "",
     "page": 3 | None,
     "rects": [[x1, y1, x2, y2], …],
     "quote": "…",
     "box": [x0, y0, x1, y1] | None,
     "source": {…}}

``ext`` is the page's document's extension when the thread opened ("" for
none), so ``doc_id`` and ``ext`` name the stored file the place was
measured on (``held_file``). ``page`` is the 1-based PDF page (None for a
region of a picture); ``rects`` are fractions of the page, or of the
picture, as shown (top-left origin, four decimals, at most
``highlights.PREV_MAX_RECTS``), ``quote`` at most
``highlights.PREV_MAX_QUOTE`` characters ("" for an area, ink, a region, a
note and the file), ``box`` the region picture's crop (fractions, four
decimals; None for a note and the file). ``source`` is added after the
commit (gamma/feedback/kept.py) and absent until then.

``anchor_of`` computes it from the rows alone, no file read, inside
``comments.create``'s transaction. The kinds: the page root is the
``file``; a highlight or link region (``passage``, or ``area`` for an area
or one without a quote) and an ink group on a PDF page (``ink``), and a
region of a picture (``region``: a highlight or link block with an
``image_position``, its ``doc_id`` the version it was drawn on), are their
own place; any other block is placed by its nearest ancestor below the page
that is one of those (a note under a highlight: that highlight's kind and
id, the annotated export's rule), else it is a ``note`` with no page. A
position with no frame (a Logseq import) has no rects and no box.
"""

import json

from .. import highlights, storage
from ..blocks_store import load_props
from ..documents import formats
from ..documents.model import file_hash, page_document, stored_name

V = 1
KINDS = ("passage", "area", "ink", "region", "note", "file")
PLACED_KINDS = ("highlight", "link", "ink")  # the block kinds a pdf_position places on a page
REGION_KINDS = ("highlight", "link")         # the block kinds an image_position places on a picture
MAX_DEPTH = 64             # ancestors looked through for a note's place

# The region picture's crop: an area's rectangle padded by AREA_PAD; a
# passage's rectangles' union grown to at least PASSAGE_MIN_W ×
# PASSAGE_MIN_H of the page and padded by PASSAGE_PAD (ai_context's
# selection crop); ink by ink_view.grow_box (view_ink's crop).
AREA_PAD = 0.005
PASSAGE_MIN_W, PASSAGE_MIN_H = 0.3, 0.05
PASSAGE_PAD = 0.01


def _round(values) -> list:
    return [round(min(1.0, max(0.0, v)), 4) for v in values]


def _union(rects) -> tuple:
    return (min(min(r[0], r[2]) for r in rects), min(min(r[1], r[3]) for r in rects),
            max(max(r[0], r[2]) for r in rects), max(max(r[1], r[3]) for r in rects))


def _page_number(pos: dict):
    page = pos.get("pageNumber")
    return page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else None


def _area_box(rects) -> list:
    x0, y0, x1, y1 = _union(rects)
    return _round((x0 - AREA_PAD, y0 - AREA_PAD, x1 + AREA_PAD, y1 + AREA_PAD))


def _passage_box(rects) -> list:
    x0, y0, x1, y1 = _union(rects)
    grow_w = max(0.0, PASSAGE_MIN_W - (x1 - x0)) / 2 + PASSAGE_PAD
    grow_h = max(0.0, PASSAGE_MIN_H - (y1 - y0)) / 2 + PASSAGE_PAD
    return _round((x0 - grow_w, y0 - grow_h, x1 + grow_w, y1 + grow_h))


def _ink_box(pos: dict, size) -> list | None:
    from ..ink_view import grow_box  # local: ink_view reaches the page readers, which import comments

    bound = pos.get("boundingRect")
    if not highlights.placed(bound):
        return None
    box = grow_box(min(bound["x1"], bound["x2"]), min(bound["y1"], bound["y2"]),
                   max(bound["x1"], bound["x2"]), max(bound["y1"], bound["y2"]), size[0], size[1])
    return _round(box) if box else None


def _placed(kind, props: dict) -> str:
    """The position that places the block ``kind`` on its document:
    ``pdf_position`` (a highlight, link region or ink group on a PDF page),
    ``image_position`` (a highlight or link block on a picture), "" for
    none. A block with both is placed on the PDF page."""
    if kind in PLACED_KINDS and isinstance(props.get("pdf_position"), dict):
        return "pdf_position"
    if kind in REGION_KINDS and isinstance(props.get("image_position"), dict):
        return "image_position"
    return ""


def _anchor(kind: str, block: str, doc_id: str, page=None, rects=(), quote: str = "", box=None, *,
            ext: str = "") -> dict:
    return {"v": V, "kind": kind, "block": block, "doc_id": doc_id, "ext": ext, "page": page, "rects": list(rects),
            "quote": quote, "box": box}


def _on_page(block_id: str, kind: str, props: dict, page_hash: str, ext: str) -> dict:
    """The anchor of a highlight, link region or ink group placed on a PDF
    page (``_placed``)."""
    pos = props["pdf_position"]
    measured = pos.get("doc_id") if isinstance(pos.get("doc_id"), str) and pos["doc_id"] else page_hash
    page = _page_number(pos)
    size = highlights._size(pos)
    if kind == "ink":
        rects, box = [], None
        bound = pos.get("boundingRect")
        if size and highlights.placed(bound):
            rects = [_round((bound["x1"] / size[0], bound["y1"] / size[1], bound["x2"] / size[0],
                             bound["y2"] / size[1]))]
            box = _ink_box(pos, size)
        return _anchor("ink", block_id, measured, page, rects, "", box, ext=ext)
    quote = props.get("quote") if isinstance(props.get("quote"), str) else ""
    area = bool(pos.get("area")) or not quote.strip()
    rects = highlights.previous_place({**pos, "pageNumber": page}, measured, quote)["rects"] if size else []
    box = (_area_box(rects) if area else _passage_box(rects)) if rects else None
    return _anchor("area" if area else "passage", block_id, measured, page, rects,
                   "" if area else quote[:highlights.PREV_MAX_QUOTE], box, ext=ext)


def _on_image(block_id: str, props: dict, page_hash: str, ext: str) -> dict:
    """The anchor of a region of a picture (``_placed``): measured on the
    version its ``image_position.hash`` names, else the page's; its one
    rectangle as fractions of that version (``highlights.region_box``) and
    the crop an area gets."""
    pos = props["image_position"]
    measured = pos.get("hash") if isinstance(pos.get("hash"), str) and pos["hash"] else page_hash
    box = highlights.region_box(props)
    rects = [list(box)] if box else []
    return _anchor("region", block_id, measured, None, rects, "", _area_box(rects) if rects else None, ext=ext)


def anchor_of(conn, page_id: str, block_id: str) -> dict:
    """The anchor of a thread opened on ``block_id`` of the page
    ``page_id`` now, read from the rows of the pages.db ``conn``."""
    page = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    view = page_document(load_props(page[0]) if page else {})
    page_hash, ext = file_hash(view) or "", view["ext"] if view else ""
    if block_id == page_id:
        return _anchor("file", page_id, page_hash, ext=ext)
    current, seen = block_id, 0
    while current and current != page_id and seen < MAX_DEPTH:
        row = conn.execute("SELECT parent_id, kind, properties FROM unified_blocks WHERE id = ? AND page_id = ?",
                           (current, page_id)).fetchone()
        if not row:
            break
        props = load_props(row[2])
        placed = _placed(row[1], props)
        if placed == "pdf_position":
            return _on_page(current, row[1], props, page_hash, ext)
        if placed:
            return _on_image(current, props, page_hash, ext)
        current, seen = row[0], seen + 1
    return _anchor("note", block_id, page_hash, ext=ext)


def parse(text) -> dict | None:
    """A stored anchor as an object; None for NULL and for one that is
    malformed (no object, an unknown kind, no block)."""
    if not text:
        return None
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        return None
    if not (isinstance(value, dict) and value.get("kind") in KINDS and isinstance(value.get("block"), str)
            and isinstance(value.get("doc_id", ""), str) and isinstance(value.get("ext", ""), str)
            and isinstance(value.get("rects", []), list)):
        return None
    return value


def place_words(anchor: dict) -> str:
    """Where a thread is, in words, from its anchor: "the whole file", "a
    region of the picture", "a note", or on a PDF page "page 3, “the loss
    is defined…”" (a passage's quote cut at 60), "page 5, an area", "page
    2, handwriting". What the feedback tools' answers and the chat's
    context name a thread's place by."""
    if anchor["kind"] == "file":
        return "the whole file"
    if anchor["kind"] == "region":
        return "a region of the picture"
    if not anchor.get("page"):
        return "a note"
    quote = " ".join((anchor.get("quote") or "").split())
    what = {"passage": f"“{quote[:60]}{'…' if len(quote) > 60 else ''}”", "area": "an area",
            "ink": "handwriting"}.get(anchor["kind"], "a note")
    return f"page {anchor['page']}, {what}"


def held_file(text) -> str:
    """The stored file an anchor holds while its thread is open:
    ``stored_name(doc_id, ext)`` (the stem through
    ``storage.stored_pdf_stem``), "" for none. An anchor without ``ext``
    (written before step 3) holds a PDF; one whose ``ext`` is no extension
    holds nothing."""
    anchor = parse(text)
    stem = storage.stored_pdf_stem(anchor.get("doc_id")) if anchor else ""
    if not stem:
        return ""
    ext = anchor.get("ext") or ".pdf"
    return stored_name(stem, ext) if formats.EXTENSION_RE.match(ext) else ""


def top(anchor: dict | None) -> float:
    """The place's top, as a fraction of the page: the highest rectangle's,
    else the box's, else 0 (the order of a page's threads)."""
    anchor = anchor or {}
    tops = [min(r[1], r[3]) for r in anchor.get("rects") or () if isinstance(r, (list, tuple)) and len(r) == 4]
    if tops:
        return min(tops)
    box = anchor.get("box")
    return box[1] if isinstance(box, (list, tuple)) and len(box) == 4 else 0.0
