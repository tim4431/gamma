"""Where a block sits on a PDF page: ``properties.pdf_position``.

One shape for every block a rectangle places on a PDF page — a highlight
(text or area), a link region, an ink group::

    {"pageNumber": 3, "width": 612, "height": 792,
     "boundingRect": {"x1": 72, "y1": 90, "x2": 300, "y2": 118},
     "rects": [{"x1": 72, "y1": 90, "x2": 300, "y2": 104}, …]}

``pageNumber`` is the 1-based PDF page, ``width`` × ``height`` the page as
it was measured when the place was taken (the viewer's page at its zoom
then, an ink file's page in points, an imported annotation's page box),
and the rectangles are in that frame, top-left origin: a reader scales them
by its own page size over ``width`` / ``height``. ``boundingRect`` is their
union, and ``rects`` is never empty beside it. An area highlight adds
``"area": true``. A highlight whose place on its page is not known (a
Logseq import without positions) carries ``pageNumber`` alone. A text box
has none: its place is its ``text_box`` (gamma/text_box.py). The block id
is the highlight's id. The frontend's twin is
frontend/src/shared/model/blockModel.js; docs/dev/api.md "Block
properties" lists the keys.
"""

COORDS = ("x1", "y1", "x2", "y2")


def _union(rects: list[dict]) -> dict:
    return {"x1": min(r["x1"] for r in rects), "y1": min(r["y1"] for r in rects),
            "x2": max(r["x2"] for r in rects), "y2": max(r["y2"] for r in rects)}


def position(page: int, width: float, height: float, rects, *, area: bool = False) -> dict:
    """The stored shape of ``rects`` (``(x1, y1, x2, y2)`` tuples, top-left
    origin, in a ``width`` × ``height`` frame) on PDF page ``page``; at
    least one rectangle."""
    rects = [dict(zip(COORDS, r)) for r in rects]
    out = {"pageNumber": page, "width": width, "height": height, "boundingRect": _union(rects), "rects": rects}
    if area:
        out["area"] = True
    return out


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _size(rect: dict):
    size = (rect.get("width"), rect.get("height"))
    return size if all(_number(v) and v > 0 for v in size) else None


def _placed(rect) -> bool:
    return isinstance(rect, dict) and all(_number(rect.get(k)) for k in COORDS)


def from_scaled(pos: dict, page=None) -> dict | None:
    """The stored shape of a position in react-pdf-highlighter's "scaled"
    form — every rect, the bounding one included, with the ``width`` and
    ``height`` it was measured at and its ``pageNumber`` — as Logseq's EDN
    has it and Gamma kept it before migration step 30. The frame is the
    position's own size, else the first measured rect's; a rect measured at
    another size is scaled into it, so nothing moves. ``page`` stands in
    for a position that names none. A position in the stored shape comes
    back as it is. None without a page."""
    old = pos.get("boundingRect") if isinstance(pos.get("boundingRect"), dict) else {}
    rects = [r for r in pos.get("rects") or () if _placed(r)]
    page = pos.get("pageNumber") or old.get("pageNumber") or next(
        (r["pageNumber"] for r in rects if r.get("pageNumber")), None) or page
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return None
    frame = _size(pos) or next(filter(None, (_size(r) for r in (old, *rects))), None)

    def into_frame(rect):
        size = _size(rect)
        if not (frame and size) or size == frame:
            return {k: rect[k] for k in COORDS}
        kx, ky = frame[0] / size[0], frame[1] / size[1]
        return {"x1": rect["x1"] * kx, "y1": rect["y1"] * ky, "x2": rect["x2"] * kx, "y2": rect["y2"] * ky}

    rects = [into_frame(r) for r in rects]
    if not (rects or _placed(old)):
        return {"pageNumber": page}
    bounding = into_frame(old) if _placed(old) else _union(rects)
    out = {"pageNumber": page, **({"width": frame[0], "height": frame[1]} if frame else {}),
           "boundingRect": bounding, "rects": rects or [dict(bounding)]}
    if pos.get("area") or old.get("area"):
        out["area"] = True
    return out


def page_of(props: dict | None) -> int | None:
    """The PDF page a highlight, link region or ink group is on
    (``pdf_position.pageNumber``), None for a block placed on none."""
    pos = (props or {}).get("pdf_position")
    page = pos.get("pageNumber") if isinstance(pos, dict) else None
    return page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else None


def is_highlight(props: dict | None) -> bool:
    """A highlight or a link region: a block with a ``pdf_position`` that
    is no ink group, text box or sheet — the ``kind`` column's
    ``highlight``, and its ``link`` made on the page (db.BLOCK_HOT_COLUMNS
    tests the other kinds first)."""
    props = props or {}
    return (isinstance(props.get("pdf_position"), dict) and "ink_url" not in props
            and not isinstance(props.get("text_box"), dict) and not isinstance(props.get("sheet"), dict))
