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
``"area": true``. After the page's PDF is replaced
(gamma/highlight_relocate.py, docs/dev/highlight_relocation.md) a
highlight's position may also carry ``doc_id``, the stored file its
rectangles were measured on when that is no longer the page's PDF (absent:
measured on the current one, which is what the viewer, the imports and a
move by hand write), ``"lost": true`` when its text was not found in the
new file (drawn hollow where it was), and ``"approx": true`` when it is
drawn dashed, pending until the person accepts it or moves it: placed
where its text was found roughly, with ``"match"`` saying how alike the
two read (0 to 1, two decimals, at most 0.99), or, without ``match``, an
area placed by estimate or left where it was when nothing placed it. A
pending highlight also carries ``"prev"``, where it was before the run
that first drew it dashed, for the person to compare
(``previous_place``)::

    {"doc_id": "<the file it was measured on>", "pageNumber": 2,
     "rects": [[0.1176, 0.1136, 0.4902, 0.1313], …], "quote": "…"}

its page there, its rectangles as fractions of that page as shown
(top-left origin, four decimals, at most ``PREV_MAX_RECTS``; none when the
position had no frame), and its quote before the run respelled it (""
for an area, at most ``PREV_MAX_QUOTE`` characters: a longer one is its
first that many, cut wherever they end, which the review card knows,
pdf/highlightReview.js PREV_QUOTE_MAX). Accepting it drops ``approx``,
``match``, ``prev`` and ``doc_id``, and a move by hand writes a position
without them. A lost text highlight may also carry ``"closest"``, the
best place the run found for its text that read too unlike it to be
applied (under the account's threshold, at least
``highlight_relocate.CLOSEST_MIN`` alike; ``valid_closest``): a place in
the page's current file in this same shape, with how alike it read and
its text there::

    {"pageNumber": 4, "width": 612, "height": 792,
     "boundingRect": {…}, "rects": [{…}, …], "match": 0.71, "quote": "…"}

``match`` two decimals (at most 0.99), one to ``PREV_MAX_RECTS`` rects,
the quote as the file spells it, at most ``PREV_MAX_QUOTE`` characters.
A run that leaves the highlight lost writes its own (or none), one that
places it drops it, and so does a replacement of the PDF (it is a place
in the outgoing file); a move by hand writes a position without it, and
the review card's Use closest match takes its place and quote, dropping
it with ``lost`` and ``doc_id``. A highlight whose place on its page is
not known (a Logseq import without positions) carries ``pageNumber``
alone. A text box and a picture have none: their place is their
``text_box`` (gamma/text_box.py) or ``picture`` (gamma/picture.py). The
block id is the highlight's id. The frontend's twin is
frontend/src/shared/model/blockModel.js; docs/dev/api.md "The highlight
shape" describes it.
"""

COORDS = ("x1", "y1", "x2", "y2")
PREV_MAX_RECTS = 200      # rectangles a position's ``prev`` holds at most
PREV_MAX_QUOTE = 1000     # characters of its quote (the web app's PREV_QUOTE_MAX)


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


def placed(rect) -> bool:
    return isinstance(rect, dict) and all(_number(rect.get(k)) for k in COORDS)


def _is_page(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def previous_place(pos: dict, doc_id: str, quote: str = "") -> dict:
    """``pdf_position.prev`` for a stored position ``pos`` measured on the
    file ``doc_id``: its page, its rectangles as fractions of that page
    (``[x1, y1, x2, y2]``, top-left origin, clamped to the page, four
    decimals, at most ``PREV_MAX_RECTS``; none when ``pos`` has no frame
    to measure them by) and ``quote`` (at most ``PREV_MAX_QUOTE``
    characters)."""
    rects = []
    size = _size(pos)
    if size:
        for rect in pos.get("rects") or ():
            if placed(rect):
                rects.append([round(min(1.0, max(0.0, rect[k] / size[0 if k[0] == "x" else 1])), 4) for k in COORDS])
    return {"doc_id": doc_id, "pageNumber": pos["pageNumber"], "rects": rects[:PREV_MAX_RECTS],
            "quote": (quote or "")[:PREV_MAX_QUOTE]}


def _fraction(value) -> bool:
    return _number(value) and 0 <= value <= 1


def valid_prev(value) -> dict | None:
    """A position's ``prev`` (``previous_place``) in its own form, or None
    for anything else: a string ``doc_id``, a page, at most
    ``PREV_MAX_RECTS`` rectangles of four numbers from 0 to 1 and a string
    ``quote``, clipped to ``PREV_MAX_QUOTE`` characters."""
    if not isinstance(value, dict):
        return None
    doc_id, page, rects, quote = (value.get(k) for k in ("doc_id", "pageNumber", "rects", "quote"))
    if not (isinstance(doc_id, str) and _is_page(page) and isinstance(quote, str)
            and isinstance(rects, (list, tuple)) and len(rects) <= PREV_MAX_RECTS):
        return None
    if not all(isinstance(r, (list, tuple)) and len(r) == 4 and all(map(_fraction, r)) for r in rects):
        return None
    return {"doc_id": doc_id, "pageNumber": page, "rects": [list(r) for r in rects], "quote": quote[:PREV_MAX_QUOTE]}


def valid_closest(value) -> dict | None:
    """A lost highlight's ``closest`` in its own form, or None for anything
    else: a page, a positive ``width`` and ``height``, a placed
    ``boundingRect``, one to ``PREV_MAX_RECTS`` placed ``rects``, a
    ``match`` from 0 to 1 and a string ``quote``, clipped to
    ``PREV_MAX_QUOTE`` characters; the rects bare."""
    if not isinstance(value, dict):
        return None
    page, bound, rects, match, quote = (value.get(k) for k in ("pageNumber", "boundingRect", "rects", "match", "quote"))
    size = _size(value)
    if not (_is_page(page) and size and placed(bound) and _fraction(match) and isinstance(quote, str)
            and isinstance(rects, (list, tuple)) and 0 < len(rects) <= PREV_MAX_RECTS and all(map(placed, rects))):
        return None
    return {"pageNumber": page, "width": size[0], "height": size[1], "boundingRect": {k: bound[k] for k in COORDS},
            "rects": [{k: r[k] for k in COORDS} for r in rects], "match": match, "quote": quote[:PREV_MAX_QUOTE]}


def from_scaled(pos: dict, page=None) -> dict | None:
    """The stored shape of a position in react-pdf-highlighter's "scaled"
    form — every rect, the bounding one included, with the ``width`` and
    ``height`` it was measured at and its ``pageNumber`` — as Logseq's EDN
    has it and Gamma kept it before migration step 30. The frame is the
    position's own size, else the first measured rect's; a rect measured at
    another size is scaled into it, so nothing moves. ``page`` stands in
    for a position that names none. A position in the stored shape comes
    back as it is, the relocation's ``doc_id``, ``lost``, ``approx``,
    ``match``, ``prev`` (``valid_prev``) and ``closest`` (``valid_closest``)
    included, each only in its own form (a restore passes every position
    through here). None without a page."""
    old = pos.get("boundingRect") if isinstance(pos.get("boundingRect"), dict) else {}
    rects = [r for r in pos.get("rects") or () if placed(r)]
    page = pos.get("pageNumber") or old.get("pageNumber") or next(
        (r["pageNumber"] for r in rects if r.get("pageNumber")), None) or page
    if not _is_page(page):
        return None
    frame = _size(pos) or next(filter(None, (_size(r) for r in (old, *rects))), None)

    def into_frame(rect):
        size = _size(rect)
        if not (frame and size) or size == frame:
            return {k: rect[k] for k in COORDS}
        kx, ky = frame[0] / size[0], frame[1] / size[1]
        return {"x1": rect["x1"] * kx, "y1": rect["y1"] * ky, "x2": rect["x2"] * kx, "y2": rect["y2"] * ky}

    rects = [into_frame(r) for r in rects]
    if not (rects or placed(old)):
        return {"pageNumber": page}
    bounding = into_frame(old) if placed(old) else _union(rects)
    out = {"pageNumber": page, **({"width": frame[0], "height": frame[1]} if frame else {}),
           "boundingRect": bounding, "rects": rects or [dict(bounding)]}
    if pos.get("area") or old.get("area"):
        out["area"] = True
    if isinstance(pos.get("doc_id"), str) and pos["doc_id"]:
        out["doc_id"] = pos["doc_id"]
    for flag in ("lost", "approx"):
        if pos.get(flag) is True:
            out[flag] = True
    if _fraction(pos.get("match")):
        out["match"] = pos["match"]
    prev = valid_prev(pos.get("prev"))
    if prev:
        out["prev"] = prev
    closest = valid_closest(pos.get("closest"))
    if closest:
        out["closest"] = closest
    return out


def page_of(props: dict | None) -> int | None:
    """The PDF page a highlight, link region or ink group is on
    (``pdf_position.pageNumber``), None for a block placed on none."""
    pos = (props or {}).get("pdf_position")
    page = pos.get("pageNumber") if isinstance(pos, dict) else None
    return page if _is_page(page) else None


def is_highlight(props: dict | None) -> bool:
    """A highlight or a link region: a block with a ``pdf_position`` that
    is no ink group, text box, picture or sheet — the ``kind`` column's
    ``highlight``, and its ``link`` made on the page (db.BLOCK_HOT_COLUMNS
    tests the other kinds first)."""
    props = props or {}
    return (isinstance(props.get("pdf_position"), dict) and "ink_url" not in props
            and not isinstance(props.get("text_box"), dict) and not isinstance(props.get("picture"), dict)
            and not isinstance(props.get("sheet"), dict))
