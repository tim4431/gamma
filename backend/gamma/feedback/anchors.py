"""Where a thread was opened: the anchor a root comment keeps
(``comments.anchor``, docs/dev/feedback.md "The anchor").

::

    {"v": 1, "kind": "passage" | "area" | "ink" | "note" | "file",
     "block": "<the block the place is read from>",
     "doc_id": "<the file the place was measured on>" | "",
     "page": 3 | None,
     "rects": [[x1, y1, x2, y2], …],
     "quote": "…",
     "box": [x0, y0, x1, y1] | None,
     "source": {…}}

``page`` is the 1-based PDF page; ``rects`` are fractions of the page as
shown (top-left origin, four decimals, at most
``highlights.PREV_MAX_RECTS``), ``quote`` at most
``highlights.PREV_MAX_QUOTE`` characters ("" for an area, ink, a note and
the file), ``box`` the region picture's crop (fractions, four decimals;
None for a note and the file). ``source`` is added after the commit
(gamma/feedback/kept.py) and absent until then.

``anchor_of`` computes it from the rows alone, no file read, inside
``comments.create``'s transaction. The kinds: the page root is the
``file``; a highlight or link region (``passage``, or ``area`` for an area
or one without a quote) and an ink group on a PDF page (``ink``) are their
own place; any other block is placed by its nearest ancestor below the page
that is one of those (a note under a highlight: that highlight's kind and
id, the annotated export's rule), else it is a ``note`` with no page. A
position with no frame (a Logseq import) has no rects and no box.
"""

import json

from .. import highlights, storage
from ..blocks_store import load_props
from ..documents.model import file_hash, page_document

V = 1
KINDS = ("passage", "area", "ink", "note", "file")
PLACED_KINDS = ("highlight", "link", "ink")  # the block kinds a pdf_position places on a page
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


def _placed(kind, props: dict) -> bool:
    return kind in PLACED_KINDS and isinstance(props.get("pdf_position"), dict)


def _anchor(kind: str, block: str, doc_id: str, page=None, rects=(), quote: str = "", box=None) -> dict:
    return {"v": V, "kind": kind, "block": block, "doc_id": doc_id, "page": page, "rects": list(rects),
            "quote": quote, "box": box}


def _on_page(block_id: str, kind: str, props: dict, page_hash: str) -> dict:
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
        return _anchor("ink", block_id, measured, page, rects, "", box)
    quote = props.get("quote") if isinstance(props.get("quote"), str) else ""
    area = bool(pos.get("area")) or not quote.strip()
    rects = highlights.previous_place({**pos, "pageNumber": page}, measured, quote)["rects"] if size else []
    box = (_area_box(rects) if area else _passage_box(rects)) if rects else None
    return _anchor("area" if area else "passage", block_id, measured, page, rects,
                   "" if area else quote[:highlights.PREV_MAX_QUOTE], box)


def anchor_of(conn, page_id: str, block_id: str) -> dict:
    """The anchor of a thread opened on ``block_id`` of the page
    ``page_id`` now, read from the rows of the pages.db ``conn``."""
    page = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    page_hash = file_hash(page_document(load_props(page[0]) if page else {})) or ""
    if block_id == page_id:
        return _anchor("file", page_id, page_hash)
    current, seen = block_id, 0
    while current and current != page_id and seen < MAX_DEPTH:
        row = conn.execute("SELECT parent_id, kind, properties FROM unified_blocks WHERE id = ? AND page_id = ?",
                           (current, page_id)).fetchone()
        if not row:
            break
        props = load_props(row[2])
        if _placed(row[1], props):
            return _on_page(current, row[1], props, page_hash)
        current, seen = row[0], seen + 1
    return _anchor("note", block_id, page_hash)


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
            and isinstance(value.get("doc_id", ""), str) and isinstance(value.get("rects", []), list)):
        return None
    return value


def held_file(text) -> str:
    """The stored file an anchor holds while its thread is open:
    ``"<doc_id>.pdf"`` (``storage.stored_pdf_stem``), "" for none."""
    anchor = parse(text)
    stem = storage.stored_pdf_stem(anchor.get("doc_id")) if anchor else ""
    return f"{stem}.pdf" if stem else ""


def top(anchor: dict | None) -> float:
    """The place's top, as a fraction of the page: the highest rectangle's,
    else the box's, else 0 (the order of a page's threads)."""
    anchor = anchor or {}
    tops = [min(r[1], r[3]) for r in anchor.get("rects") or () if isinstance(r, (list, tuple)) and len(r) == 4]
    if tops:
        return min(tops)
    box = anchor.get("box")
    return box[1] if isinstance(box, (list, tuple)) and len(box) == 4 else 0.0
