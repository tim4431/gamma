"""Where a block sits on its page's document: ``properties.pdf_position``
on a PDF page, ``image_position`` on a picture, ``text_position`` in a
text file.

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
that first drew it dashed, for the person to compare and for later runs
to match it from (its reference, the last place someone confirmed; one
those runs do not find goes back there, lost, without ``prev``)
(``previous_place``)::

    {"doc_id": "<the file it was measured on>", "pageNumber": 2,
     "rects": [[0.1176, 0.1136, 0.4902, 0.1313], …], "quote": "…"}

its page there, its rectangles as fractions of that page as shown
(top-left origin, four decimals, at most ``PREV_MAX_RECTS``; none when the
position had no frame), and its quote before the run respelled it (""
for an area, at most ``PREV_MAX_QUOTE`` characters: a longer one is its
first that many, cut wherever they end, which the review card knows,
shared/model/blockModel.js PREV_MAX_QUOTE). Accepting it drops ``approx``,
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

A region of a picture (a page whose document is an image) is the same
block with ``properties.image_position`` in place of ``pdf_position``::

    {"width": 2400, "height": 1600,
     "boundingRect": {"x1": 120, "y1": 64, "x2": 530, "y2": 284},
     "rects": [{"x1": 120, "y1": 64, "x2": 530, "y2": 284}],
     "hash": "9f2c…"}

the frame the picture's own pixels as drawn (its natural size, EXIF
orientation applied), one rectangle equal to ``boundingRect``, whole
pixels, and ``hash`` the stored file's stem of the version it was drawn
on, always written: "drawn on another version" is read, never stamped
(``hash`` differs from the page's ``file_hash``), so a replacement writes
no op on regions, and ``hash`` holds no file (storage._position_ids names
nothing for it; the version a thread needs is held by its anchor,
gamma/feedback/anchors.py). Regions are never relocated; readers scale by
fractions (``region_box``). ``is_region`` is their test, the twin of
``is_highlight``; the ``kind`` column calls both ``highlight``.

A range of a text file (a page whose document is ``format: "text"``: a
source file, a log) is the same block again, with
``properties.text_position``::

    {"line": 118, "col": 4, "end": 120, "end_col": 37,
     "prefix": "…", "suffix": "…", "hash": "9f2c…"}

``line`` and ``end`` its first and last line (1-based, ``end`` inclusive,
the words a feedback entry's ``source`` uses), ``col`` and ``end_col``
columns in them (0-based, in code points of the decoded line, the line
break not counted; ``end_col`` exclusive, so a range of one line has
``end_col > col``), and ``properties.quote`` the selected text verbatim,
its lines joined with ``\\n``, at most ``TEXT_QUOTE_MAX`` code points and
never starting or ending in a line break: a selection that ends at column 0
of a later line ends at the end of the line before, and one that starts at
the end of a line starts at the next (``clamp_span``). ``prefix`` and
``suffix`` are the file's text just before and just after the quote,
``CONTEXT_CHARS`` code points each (line breaks kept as ``\\n``, shorter at
the file's ends), and ``hash`` the stored stem of the version the range was
measured on, always written, as a region's is: "measured on another
version" is read (``hash`` differs from the page's ``file_hash``), ``hash``
holds no file, and a replacement stamps nothing on a range. A range
carries its own context, so the run that places it in a new version of
the file reads nothing of the old one (gamma/documents/text_anchors.py).
That run writes the PDF's words beside the place: ``lost``, ``approx``
with ``match``, ``prev`` (``{hash, line, col, end, end_col, quote}``,
``valid_text_prev``: where it was before the run that first drew it
dashed, its quote whole) and a lost one's ``closest`` (``{line, col, end,
end_col, match, quote, hash}``, ``valid_text_closest``: its best place
under the threshold in the version ``hash`` names); the quotes of both at
most ``TEXT_QUOTE_MAX``, not a PDF's ``PREV_MAX_QUOTE``, since a range is
matched and placed by them. Readers never trust the columns alone: the
quote at ``line``..``end`` decides, and a column past its line's end is
clamped. Nothing validates the shape on write: ``text_span`` answers None
for a malformed one, which the ``kind`` column still calls ``highlight``
and the readers take for a note. A range may have a line ``style``, never
an ``area``; a restore passes the position through untouched
(``normalize.highlight_shape`` reshapes ``pdf_position`` alone), and a
vault's Markdown file writes a range moved into a notes page as any
highlight, a ``[!quote]`` callout, without a page (vault/sync.py ``_g``,
vault/links.py ``_highlight_to_file`` and ``_head``).
``is_text_range`` is its test, the twin of ``is_highlight`` and
``is_region``; tests/shared/textposition.json holds the client
(frontend/src/documents/viewers/textAnchors.js) to ``span_text``.
"""

COORDS = ("x1", "y1", "x2", "y2")
PREV_MAX_RECTS = 200      # rectangles a position's ``prev`` holds at most
PREV_MAX_QUOTE = 1000     # characters of its quote (shared/model/blockModel.js PREV_MAX_QUOTE)
TEXT_QUOTE_MAX = 10_000   # code points of a text range's quote (tests/shared/textposition.json quote_max)
CONTEXT_CHARS = 40        # of text before and after a passage, kept to tell places apart (textposition.json context)
TEXT_SPAN = ("line", "col", "end", "end_col")


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
    (``pdf_position.pageNumber``), None for a block placed on none (a
    region of a picture and a range of a text file are on no page)."""
    pos = (props or {}).get("pdf_position")
    page = pos.get("pageNumber") if isinstance(pos, dict) else None
    return page if _is_page(page) else None


def is_ink(props: dict | None) -> bool:
    """An ink group (a drawing, gamma/ink.py): an ``ink_url`` key, whatever
    its value — "" or null on a group whose file is not stored yet — the
    ``kind`` column's rule for ``ink`` (blocks_store.block_kind) and the
    client's (shared/model/blockModel.js isInkGroup). Code that draws the
    group reads the URL itself (``ink.read_upload``), which is None for
    such a one."""
    return isinstance(props, dict) and "ink_url" in props


def _placed_block(props: dict, key: str) -> bool:
    return (isinstance(props.get(key), dict) and not is_ink(props)
            and not isinstance(props.get("text_box"), dict) and not isinstance(props.get("picture"), dict)
            and not isinstance(props.get("sheet"), dict))


def is_highlight(props: dict | None) -> bool:
    """A highlight or a link region on a PDF page: a block with a
    ``pdf_position`` that is no ink group, text box, picture or sheet
    (db.BLOCK_HOT_COLUMNS tests the other kinds first). The ``kind``
    column's ``highlight`` is wider: a region of a picture (``is_region``)
    and a range of a text file (``is_text_range``) read as one too, so a
    reader of ``pdf_position`` tests this, never the kind."""
    return _placed_block(props or {}, "pdf_position")


def is_region(props: dict | None) -> bool:
    """A region of a picture: a block with an ``image_position`` that is no
    ink group, text box, picture or sheet, the twin of ``is_highlight`` on
    an image document. A block with both positions is both."""
    return _placed_block(props or {}, "image_position")


def is_text_range(props: dict | None) -> bool:
    """A range of a text file: a block with a ``text_position`` that is no
    ink group, text box, picture or sheet, the twin of ``is_region`` on a
    text document; ``text_span`` says whether its place can be read."""
    return _placed_block(props or {}, "text_position")


def _whole(value) -> bool:
    """A whole number as JSON has it: an int, or a float of a whole value
    (``3.0``, which a browser reads as 3: blockModel.textSpan), never a bool."""
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())


def _span(line, col, end, end_col) -> tuple[int, int, int, int] | None:
    if not all(map(_whole, (line, col, end, end_col))):
        return None
    line, col, end, end_col = int(line), int(col), int(end), int(end_col)
    if line < 1 or col < 0 or end < line or end_col < 0 or (end == line and end_col <= col):
        return None
    return line, col, end, end_col


def text_span(props: dict | None) -> tuple[int, int, int, int] | None:
    """A text range's ``(line, col, end, end_col)``: whole numbers, ``line``
    from 1, ``end`` not before it, columns from 0 and ``end_col`` past
    ``col`` on a range of one line; None for a block that is no text range
    or whose position is malformed (nothing validates it on write)."""
    if not is_text_range(props):
        return None
    pos = props["text_position"]
    return _span(*(pos.get(k) for k in TEXT_SPAN))


def text_position(line: int, col: int, end: int, end_col: int, *, hash: str, prefix: str, suffix: str) -> dict:
    """The stored shape of a text range measured on the version ``hash``."""
    return {"line": line, "col": col, "end": end, "end_col": end_col, "prefix": prefix, "suffix": suffix,
            "hash": hash}


def valid_text_prev(value) -> dict | None:
    """A text range's ``prev`` in its own form, or None for anything else:
    a string ``hash``, a span as ``text_span`` reads one and a string
    ``quote``, clipped to ``TEXT_QUOTE_MAX`` characters (the range's whole
    quote, which a later run matches it by and a lost one goes back to)."""
    if not isinstance(value, dict):
        return None
    span = _span(*(value.get(k) for k in TEXT_SPAN))
    hash_, quote = value.get("hash"), value.get("quote")
    if not (span and isinstance(hash_, str) and isinstance(quote, str)):
        return None
    return {"hash": hash_, **dict(zip(TEXT_SPAN, span)), "quote": quote[:TEXT_QUOTE_MAX]}


def valid_text_closest(value) -> dict | None:
    """A lost text range's ``closest`` in its own form, or None for anything
    else: a span as ``text_span`` reads one, a ``match`` from 0 to 1, a
    string ``quote``, clipped to ``TEXT_QUOTE_MAX`` characters (the quote
    the range takes when it is placed there), and the string ``hash`` of
    the version it was found in."""
    if not isinstance(value, dict):
        return None
    span = _span(*(value.get(k) for k in TEXT_SPAN))
    match, quote, hash_ = value.get("match"), value.get("quote"), value.get("hash")
    if not (span and _fraction(match) and isinstance(quote, str) and isinstance(hash_, str)):
        return None
    return {**dict(zip(TEXT_SPAN, span)), "match": match, "quote": quote[:TEXT_QUOTE_MAX], "hash": hash_}


def clamp_span(lines: list[str], span) -> tuple[int, int, int, int] | None:
    """A span (``(line, col, end, end_col)`` or a dict of them) as it is
    stored over ``lines``, the decoded file's: its lines within the file
    (an end past the last line ends with it), each column at most its
    line's length, an end at column 0 of a later line, or after line breaks
    alone, moved back to the end of the line before, and a start at its
    line's end, or before line breaks alone, moved on to the start of the
    next line, so the span never ends or starts in a line break. None when
    nothing is left between its two ends: a selection of a line break
    alone, or a span past the file."""
    line, col, end, end_col = (span[k] for k in TEXT_SPAN) if isinstance(span, dict) else span
    n = len(lines)
    if not lines or line > n:
        return None
    if end > n:
        end, end_col = n, len(lines[n - 1])
    col, end_col = min(col, len(lines[line - 1])), min(end_col, len(lines[end - 1]))
    while end > line and end_col == 0:
        end -= 1
        end_col = len(lines[end - 1])
    while line < end and col == len(lines[line - 1]):
        line, col = line + 1, 0
    return _span(line, col, end, end_col)


def span_text(lines: list[str], span) -> tuple[str, str, str]:
    """``(quote, prefix, suffix)`` of a span in the decoded file's ``lines``
    (``clamp_span``'s, which a caller stores): the text between its two
    ends, lines joined with ``\\n``, cut to ``TEXT_QUOTE_MAX`` code points
    (and never ending in a line break), and the ``CONTEXT_CHARS`` code
    points just before it and just after the quote, line breaks kept.
    ``("", "", "")`` for a span with nothing in it. The client's
    ``contextOf`` gives the same (tests/shared/textposition.json)."""
    clamped = clamp_span(lines, span)
    if clamped is None:
        return "", "", ""
    line, col, end, end_col = clamped
    # only the lines the quote and its context reach are joined: a run respells ranges of a long file
    first, room = line, col
    while first > 1 and room < CONTEXT_CHARS:
        first -= 1
        room += len(lines[first - 1]) + 1
    last, room = end, len(lines[end - 1]) - end_col
    while last < len(lines) and room < CONTEXT_CHARS:
        last += 1
        room += len(lines[last - 1]) + 1
    text = "\n".join(lines[first - 1:last])
    start = sum(len(lines[i]) + 1 for i in range(first - 1, line - 1)) + col
    stop = start + sum(len(lines[i]) + 1 for i in range(line - 1, end - 1)) - col + end_col
    stop = min(stop, start + TEXT_QUOTE_MAX)
    while stop > start and text[stop - 1] == "\n":
        stop -= 1
    return text[start:stop], text[max(0, start - CONTEXT_CHARS):start], text[stop:stop + CONTEXT_CHARS]


def region_box(props: dict | None) -> tuple[float, float, float, float] | None:
    """A region's rectangle as fractions of its picture, ``(x0, y0, x1,
    y1)``, top-left origin, clamped to the picture, four decimals; None for
    a block that is no region or whose position has no frame or no placed
    rectangle (nothing validates the shape on write). What every reader of
    a region's place scales by: a figure rebuilt at another size keeps the
    region's relative place."""
    if not is_region(props):
        return None
    pos = props["image_position"]
    size = _size(pos)
    rect = pos.get("boundingRect")
    if not size or not placed(rect):
        return None
    box = tuple(round(min(1.0, max(0.0, rect[k] / size[0 if k[0] == "x" else 1])), 4) for k in COORDS)
    return box if box[0] < box[2] and box[1] < box[3] else None


def is_link(props: dict | None) -> bool:
    """A block that links somewhere: a ``link_url`` (a web or document
    address) or a ``link_page_id`` (a Gamma page) that is neither missing,
    null nor empty, the ``kind`` column's rule for ``link``
    (blocks_store.block_kind). A link region on the PDF is a highlight too
    (``is_highlight``), so ``is_highlight and not is_link`` is a highlight
    proper."""
    props = props or {}
    return any(props.get(key) not in (None, "") for key in ("link_url", "link_page_id"))
