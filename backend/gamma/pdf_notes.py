"""Notes and comments as cards: the annotated-PDF export's Notes and
Comments switches.

The plain annotated export (``pdf_export.annotate_pdf``) keeps a highlight's
notes in its annotation popup: invisible on paper, and a click away in every
viewer. With the Notes switch a highlight's notes are also printed as a card
(``pdf_cards``), and with the Comments switch each of its open comment
threads is a card of its own. Notes go where ``notes_at`` says; threads
always go beside the page. A thread on one of the notes goes under that
note when its card is beside the page too, and is otherwise a card of its
own quoting the note.

**Beside the page** (``notes_at`` ``beside``, and every thread) is where the
viewer shows a highlight's comment threads once a lower zoom leaves room for
them (docs/dev/comments.md "The PDF margin",
``frontend/src/comments/marginLayout.js``): a column of cards, each level
with its highlight's first line, stacked so none overlaps. Every page of
the copy is then widened on the right of the page as shown (``_widen``): its
media and crop boxes grow on the user-space side the display frame's x
axis runs to, so the page's content, its annotations and the display origin
stay where they were, on every /Rotate. The column has one width for the
whole document, a share of its widest page (``_column_width``), and every
page gets it, cards or not, so the pages keep one width as in the viewer.
The strip is painted the viewer's backdrop grey, which also covers whatever
the old crop box hid there. In the gap a thin bar spans the highlight's
lines and a line joins it to its first card.

**Stacking** (``_stack``) is the viewer's ``stackCards`` without an active
card: by their highlight's top, each card at that top or ``CARD_GAP`` under
the card above; then, since a page cannot run on into the next one's
column, the run is pushed up off the page's foot. Cards too tall to fit are
shortened, the tallest first, to one height (``_water_level``), each keeping
``MIN_LINES`` lines and ending in an ellipsis. When that is not enough the
column zooms out (``SCALES``): laid out wider and drawn smaller, as the
viewer's column is at a lower zoom.

**On the page** (``notes_at`` ``page``) a note card goes in the nearest
patch of the page's own empty space (``pdf_free_space``), with a dashed
leader back to its highlight; the page keeps its size. A card tries a few
widths (wider ones for a picture or display math) and takes the cheapest
spot, a narrow card or a squeezed equation costing extra.

**Drawing.** Everything is laid out in the page's display frame (x right, y
down, /Rotate applied: the frame the viewer stores highlight rects in) and
drawn as a Form XObject whose /Matrix maps that frame to user space. It is
appended to the page's content behind a ``q`` … ``Q`` pair that keeps the
page's own graphics state out. The form has resources of its own, so
nothing on the page is renamed, and the page's content streams are left as
they are: PyPDF2's ``merge_page`` would parse every one of them and write it
back uncompressed. Links in the cards become /Link annotations. The full
text of the notes stays in the popup either way.
"""

import io
import math
from collections import namedtuple

from PyPDF2 import PdfWriter
from PyPDF2.generic import (
    AnnotationBuilder,
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    RectangleObject,
)

from . import pdf_cards, pdf_free_space
from .logbuf import log
from .pdf_cards import BAR, FONT_SIZE, LEADING, PAD_X, PAD_Y, clip, inner_width
from .pdf_export import ExportPdfReader, display_size, page_frame, parse_css_color
from .pdf_glyphs import GlyphFonts
from .pdf_image import XObjectStore
from .pdf_typeset import fill_rect, finalize_cid, font_resources, num

NOTES_AT = ("beside", "page")

# The column, in points of the page as shown.
COLUMN_SHARE = 0.45                 # its width, × the document's widest page
COLUMN_MIN, COLUMN_MAX = 170.0, 300.0
COLUMN_GAP = 18.0                   # the page's edge to the cards
EDGE = 12.0                         # the cards' room at the page's top and foot, and right of them
TIE_X, TIE_W = 4.0, 1.4             # the bar beside a highlight's lines, from the page's edge
CARD_GAP = 6.0                      # between two cards, in the column's own frame (SCALES)

# When the column's cards do not fit: each keeps MIN_LINES lines (the last
# an ellipsis; a card at rest in the viewer shows three), then the column
# zooms out through SCALES, keeping two lines at the last.
MIN_LINES = 3
SCALES = (1.0, 0.85, 0.7, 0.55, 0.45)

# A card on the page: the widths it tries, widest first (wider ones for a
# picture or display math), and its tallest, a share of the page's height.
BOX_WIDTHS = (190.0, 150.0, 115.0)
WIDE_WIDTHS = (270.0, 200.0, 150.0)
MAX_BOX_FRAC = 0.45

BACKDROP = (0.945, 0.945, 0.95)     # the viewer's backdrop, light theme
PAGE_EDGE = (0.80, 0.80, 0.82)

# Where a highlight's lines are on the page as shown: their box, and the
# middle of the first line.
Anchor = namedtuple("Anchor", "x0 top x1 bottom mid")

# One card: a highlight's notes (``blocks``, each with the threads on it)
# or one of its threads (``thread``, its comments, under ``quote``: the note
# it answers when that note is not in the same card). ``group`` is the
# highlight's index: its cards stand together, and the first of them is
# the one tied to it.
Card = namedtuple("Card", "group anchor color blocks thread quote")


class _Drawing:
    """What a page's drawing collects (the fonts its text used, its links,
    its pictures) and the document's glyph fonts and picture store it draws
    with: what ``pdf_cards.draw`` is handed."""

    def __init__(self, glyphs, images):
        self.glyphs, self.images = glyphs, images
        self.fonts, self.links, self.xobjects = set(), [], {}

    def resources(self, writer) -> DictionaryObject:
        resources = DictionaryObject()
        fonts = font_resources(sorted(self.fonts), writer)
        fonts.update({NameObject("/" + name): ref for name, ref in self.glyphs.resources().items()})
        if fonts:
            resources[NameObject("/Font")] = fonts
        if self.xobjects:
            resources[NameObject("/XObject")] = DictionaryObject(
                {NameObject("/" + name): ref for name, ref in self.xobjects.items()})
        return resources


def _layout(card: Card, card_w: float, images, resolve_ref):
    """The card's content laid out for a card ``card_w`` wide."""
    lay = pdf_cards.Layout(inner_width(card_w, card.blocks is not None), images, resolve_ref)
    return lay.notes(card.blocks) if card.blocks is not None else lay.thread(card.thread, card.quote)


def _bar(card: Card):
    """A note card carries its highlight's colour; a thread card, as in the
    viewer's margin, none."""
    return card.color if card.blocks is not None else None


# --- the column ---------------------------------------------------------------

def _column_width(widest: float) -> float:
    """The column's width for a document whose widest page, as shown, is
    ``widest`` points wide."""
    return min(COLUMN_MAX, max(COLUMN_MIN, COLUMN_SHARE * widest))


def _water_level(heights, room: float) -> float:
    """The largest ``c`` with Σ min(h, c) ≤ ``room``: the one height the
    tallest cards are shortened to so the column fits. Infinite when they
    fit as they are."""
    if sum(heights) <= room:
        return math.inf
    left, rest = room, len(heights)
    for h in sorted(heights):
        level = left / rest
        if h > level:
            return max(0.0, level)
        left -= h
        rest -= 1
    return math.inf


def _stack(tops, heights, low: float, high: float, gap: float = CARD_GAP):
    """The cards' tops, sorted by their highlight's (``tops``): each at its
    highlight's top, or ``gap`` under the card above when that would overlap
    it, none above ``low``; then the run pushed up off ``high``, each card
    ending a gap over the one under it. The cards stay inside when their
    heights and gaps fit between the two."""
    out, bottom = [], -math.inf
    for top, h in zip(tops, heights):
        top = max(top, low, bottom + gap)
        out.append(top)
        bottom = top + h
    ceiling = high
    for i in reversed(range(len(out))):
        out[i] = min(out[i], ceiling - heights[i])
        ceiling = out[i] - gap
    return out


def _column(cards, col_w: float, page_h: float, images, resolve_ref=None):
    """Lays a page's column cards (sorted by top) out down the column:
    ``(scale, [(card, rows, height, top)])``, the cards in the column's own
    frame, which is drawn ``scale`` times its size from the column's
    top-left. Cards that do not fit even at the last scale are left out."""
    for scale in SCALES:
        width, height = col_w / scale, page_h / scale
        layouts = [_layout(card, width, images, resolve_ref) for card in cards]
        room = height - 2 * EDGE - CARD_GAP * (len(cards) - 1)
        level = _water_level([lay.height + 2 * PAD_Y for lay in layouts], room)
        floor = 2 * PAD_Y + (MIN_LINES if scale != SCALES[-1] else 2) * FONT_SIZE * LEADING
        if level >= floor:
            break
    clipped = [clip(lay.rows, lay.height, max(level, floor) - 2 * PAD_Y) for lay in layouts]
    heights = [h + 2 * PAD_Y for _rows, h in clipped]
    keep = len(cards)
    while keep and sum(heights[:keep]) + CARD_GAP * (keep - 1) > height - 2 * EDGE:
        keep -= 1
    tops = _stack([card.anchor.top / scale for card in cards[:keep]], heights[:keep], EDGE, height - EDGE)
    return scale, [(card, rows, h, top) for card, (rows, _h), h, top in zip(cards, clipped, heights, tops)]


def _draw_column(ops, cards, page_w: float, page_h: float, col_w: float, drawing: _Drawing, resolve_ref):
    """The column's cards and their ties; the number drawn. Their links go
    into ``drawing.links`` in the page's display frame."""
    scale, placed = _column(sorted(cards, key=lambda c: c.anchor.top), col_w, page_h, drawing.images, resolve_ref)
    col_x = page_w + COLUMN_GAP
    # In the gap: a bar beside each highlight's lines, and a line from it to
    # its first card, at the same height within the card as the first line's
    # middle is within the highlight (level, when the card is).
    tied = set()
    for card, _rows, h, top in placed:
        if card.group in tied:
            continue
        tied.add(card.group)
        a, line = card.anchor, tuple(c * 0.7 for c in card.color[:3])
        tie = page_w + TIE_X
        fill_rect(ops, tie, a.top, tie + TIE_W, max(a.bottom, a.top + 2.0), line)
        at = min(max(top * scale + a.mid - a.top, (top + PAD_Y) * scale), (top + h - PAD_Y) * scale)
        ops.append(b"%s %s %s RG 0.5 w %s %s m %s %s l S" % (
            num(line[0]), num(line[1]), num(line[2]), num(tie + TIE_W), num(a.mid), num(col_x), num(at)))
    page_links, drawing.links = drawing.links, []
    ops.append(b"q %s 0 0 %s %s 0 cm" % (num(scale), num(scale), num(col_x)))
    for card, rows, h, top in placed:
        pdf_cards.draw(ops, 0.0, top, col_w / scale, h, rows, drawing, bar=_bar(card))
    ops.append(b"Q")
    page_links += [(col_x + x0 * scale, y0 * scale, col_x + x1 * scale, y1 * scale, href)
                   for x0, y0, x1, y1, href in drawing.links]
    drawing.links = page_links
    return len(placed)


# --- on the page ----------------------------------------------------------------

def _on_page(cards, space, page_w: float, page_h: float, images, resolve_ref=None):
    """The page's note cards each at its cheapest free spot near its
    highlight: ``[(card, rows, x, y, w, h)]``. A card that fits nowhere on
    the page (wider than it) is left out."""
    placed = []
    cap = page_h * MAX_BOX_FRAC - 2 * PAD_Y
    for card in sorted(cards, key=lambda c: (c.anchor.top, c.anchor.x0)):
        a = card.anchor
        probe = _layout(card, BOX_WIDTHS[0], images, resolve_ref)
        widths = WIDE_WIDTHS if any(r.kind in ("image", "math") for r in probe.rows) else BOX_WIDTHS
        best = None
        for width in widths:
            card_w = min(width, page_w - 2 * pdf_free_space.PAGE_MARGIN)
            lay = probe if card_w == BOX_WIDTHS[0] else _layout(card, card_w, images, resolve_ref)
            rows, h = clip(lay.rows, lay.height, cap)
            # A one-line note gets a one-line-wide card, not a column.
            box_w = min(card_w, lay.natural_width + 2 * PAD_X + BAR)
            box_h = h + 2 * PAD_Y
            # Narrow cards cost extra, and so does squeezing an equation or a
            # picture to fit one: a shrunk formula is unreadable.
            penalty = (widths[0] - width) * 0.35 + (1 - lay.squeeze) * 160
            spot = pdf_free_space.place(space, (a.x0, a.top, a.x1, a.bottom), box_w, box_h, penalty)
            if spot and (best is None or spot[0] < best[0][0]):
                best = (spot, box_w, box_h, rows)
            if spot and spot[3] and spot[0] < 30:
                break
        if not best:
            continue
        (_score, x, y, free), w, h, rows = best
        if not free:
            log.info("[pdf-notes] no free space on the page: a note card overlaps its content")
        space.take(x, y, x + w, y + h)
        placed.append((card, rows, x, y, w, h))
    return placed


def _leader(ops, box, anchor: Anchor, color):
    """A dashed line from the highlight to its card, a dot at the highlight,
    when the two do not touch."""
    bx0, by0, bx1, by1 = box
    px = min(max((bx0 + bx1) / 2, anchor.x0), anchor.x1)
    py = min(max((by0 + by1) / 2, anchor.top), anchor.bottom)
    qx, qy = min(max(px, bx0), bx1), min(max(py, by0), by1)
    if math.hypot(qx - px, qy - py) <= 3:
        return
    r, g, b = (c * 0.7 for c in color[:3])
    ops.append(b"%s %s %s RG 0.7 w [2 2] 0 d %s %s m %s %s l S [] 0 d" % (
        num(r), num(g), num(b), num(px), num(py), num(qx), num(qy)))
    ops.append(b"%s %s %s rg %s %s 2.6 2.6 re f" % (num(r), num(g), num(b), num(px - 1.3), num(py - 1.3)))


# --- the page -----------------------------------------------------------------

def _frame(crop, rotation):
    """Display frame (x right, y down, origin top-left of the visible page) →
    (display size, cm matrix into user space)."""
    cx0, cy0, cx1, cy1 = crop
    size = display_size(crop, rotation)
    if rotation == 90:
        return size, (0, 1, 1, 0, cx0, cy0)
    if rotation == 180:
        return size, (-1, 0, 0, 1, cx1, cy0)
    if rotation == 270:
        return size, (0, -1, -1, 0, cx1, cy1)
    return size, (1, 0, 0, -1, cx0, cy1)


def _to_display(matrix):
    """The frame's inverse: a user-space point → the display frame."""
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    return lambda u, v: ((d * (u - e) - c * (v - f)) / det, (a * (v - f) - b * (u - e)) / det)


def _widen(page, view, rotation: int, extra: float):
    """Grows the page by ``extra`` on the right of the page as shown: the
    view box's side the display frame's x axis runs to (``_frame``), so the
    frame's origin stays put. The media and crop boxes both become the new
    box; the trim, bleed and art boxes, inside the old one, go."""
    x0, y0, x1, y1 = view
    if rotation == 90:
        y1 += extra
    elif rotation == 180:
        x0 -= extra
    elif rotation == 270:
        y0 -= extra
    else:
        x1 += extra
    for key in ("/MediaBox", "/CropBox"):
        page[NameObject(key)] = RectangleObject((x0, y0, x1, y1))
    for key in ("/TrimBox", "/BleedBox", "/ArtBox"):
        page.pop(NameObject(key), None)


def _backdrop(page_w: float, page_h: float, extra: float) -> list:
    """The strip beside the page, and the page's edge."""
    ops = []
    fill_rect(ops, page_w, 0.0, page_w + extra, page_h, BACKDROP)
    fill_rect(ops, page_w, 0.0, page_w + 0.6, page_h, PAGE_EDGE)
    return ops


def _stream(data: bytes, compress: bool = False):
    stream = DecodedStreamObject()
    stream.set_data(data)
    return stream.flate_encode() if compress else stream


def _form(writer, ops, width: float, height: float, matrix, resources) -> IndirectObject:
    """A Form XObject drawing ``ops`` in the display frame, ``width`` ×
    ``height``, mapped to user space by ``matrix``."""
    form = _stream(b"\n".join(ops), compress=True)
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(width), FloatObject(height)]),
        NameObject("/Matrix"): ArrayObject([FloatObject(v) for v in matrix]),
        NameObject("/Resources"): resources,
    })
    return writer._add_object(form)


def _stamp(writer, page, form, head):
    """Draws ``form`` over the page: the page's resources gain it under a
    name of their own (a copy, since pages may share one dictionary), and its
    content becomes ``head`` (a shared ``q``), the streams it had, then a
    ``Q`` and the form."""
    resources = DictionaryObject(page[NameObject("/Resources")] if "/Resources" in page else {})
    xobjects = DictionaryObject(resources[NameObject("/XObject")] if "/XObject" in resources else {})
    name, n = "/GmNotes", 0
    while name in xobjects:
        n += 1
        name = f"/GmNotes{n}"
    xobjects[NameObject(name)] = form
    resources[NameObject("/XObject")] = xobjects
    page[NameObject("/Resources")] = resources

    raw = page.raw_get("/Contents") if "/Contents" in page else None
    own = raw.get_object() if isinstance(raw, IndirectObject) else raw
    parts = list(own) if isinstance(own, ArrayObject) else [raw] if raw is not None else []
    # Streams must be indirect objects or the file is unreadable.
    parts = [p if isinstance(p, IndirectObject) else writer._add_object(p) for p in parts]
    tail = writer._add_object(_stream(b"\nQ\n%s Do\n" % name.encode()))
    page[NameObject("/Contents")] = ArrayObject([head, *parts, tail])


def _link(writer, index: int, matrix, x0, y0, x1, y1, href: str):
    a, b, c, d, e, f = matrix
    xs, ys = zip(*((a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y1))))
    try:
        writer.add_annotation(page_number=index, annotation=AnnotationBuilder.link(
            rect=(min(xs), min(ys), max(xs), max(ys)), url=href))
    except Exception as e:
        log.info(f"[pdf-notes] could not link {href[:60]}: {e}")


def _anchor(pos, page_w: float, page_h: float):
    """A highlight's lines on the page as shown; None without a usable rect.
    Rects are stored in the frame of the position's width × height."""
    rw = float(pos.get("width") or 0) or page_w
    rh = float(pos.get("height") or 0) or page_h
    xs, lines = [], []
    for r in pos.get("rects") or []:
        if not r or any(r.get(k) is None for k in ("x1", "y1", "x2", "y2")):
            continue
        xs += [min(max(float(r[k]) / rw * page_w, 0.0), page_w) for k in ("x1", "x2")]
        lines.append(tuple(sorted(min(max(float(r[k]) / rh * page_h, 0.0), page_h) for k in ("y1", "y2"))))
    if not lines:
        return None
    top, first = min(lines)
    return Anchor(min(xs), top, max(xs), max(y2 for _y1, y2 in lines), top + min(first - top, 2 * FONT_SIZE) / 2)


# --- entry point --------------------------------------------------------------

def render_notes(pdf_bytes: bytes, highlights, *, notes_at: str = "beside", uploads_dir=None,
                 resolve_ref=None) -> tuple[bytes, int]:
    """Return (pdf bytes with the cards drawn, cards drawn).

    ``highlights``: ``[{position: <pdf_position dict>, color: <css string>,
    blocks: [(depth, markdown, threads)], threads: [thread, …],
    note_threads: [(markdown, thread), …]}]``, a thread its comments oldest
    first (``{author, created_at, body, deleted}``). ``blocks`` are the
    highlight's notes (``pdf_export.highlight_note_blocks``; a block may
    leave out its threads, and without ``blocks`` a ``note`` string is one
    block), placed as ``notes_at`` says, each with the threads on it: under
    the note in its card beside the page, else a card of their own beside
    the page, quoting the note. ``threads`` are the threads on the
    highlight itself, each a card beside the page. ``note_threads`` are the
    threads on its notes the cards do not show (the Notes switch off, an
    empty note, a text box), each a card quoting its note. A highlight with
    nothing to print, without usable rects or on a page out of range is
    skipped; nothing to draw leaves the bytes as they are. ``uploads_dir`` is
    where ``/api/uploads/…`` pictures are read from (without it they read as
    their alt text), and ``resolve_ref`` (block id → {content, …} or None)
    reads ``[[refs]]`` as the notes PDF does.
    """
    if notes_at not in NOTES_AT:
        raise ValueError(f"notes_at must be one of {NOTES_AT}")
    reader = ExportPdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    writer.append(reader)
    pages = writer.pages
    frames = [page_frame(page) for page in pages]
    sizes = [display_size(view, rotation) for view, rotation in frames]

    beside: dict[int, list] = {}
    on_page: dict[int, list] = {}
    for group, h in enumerate(highlights):
        # A note's threads ride in its card when that card is beside the
        # page with them; else they stand apart, each quoting its note.
        blocks, apart = [], []
        for depth, md, *rest in h.get("blocks") or [(0, h.get("note") or "")]:
            replies = [thread for thread in (rest[0] if rest else ()) if thread]
            md = (md or "").strip()
            together = md and notes_at == "beside"
            if md:
                blocks.append((depth, md, replies if together else []))
            if not together:
                apart += [(md, thread) for thread in replies]
        apart += [(md or "", thread) for md, thread in h.get("note_threads") or () if thread]
        threads = [("", thread) for thread in h.get("threads") or () if thread] + apart
        pos = h.get("position") or {}
        try:
            index = int(pos.get("pageNumber") or 0) - 1
        except (TypeError, ValueError):
            continue
        if not (blocks or threads) or not 0 <= index < len(pages):
            continue
        anchor = _anchor(pos, *sizes[index])
        if not anchor:
            continue
        color = parse_css_color(h.get("color"))
        if blocks:
            (beside if notes_at == "beside" else on_page).setdefault(index, []).append(
                Card(group, anchor, color, blocks, None, ""))
        if threads:
            beside.setdefault(index, []).extend(
                Card(group, anchor, color, None, thread, quote) for quote, thread in threads)
    if not beside and not on_page:
        return pdf_bytes, 0

    col_w = _column_width(max(w for w, _h in sizes))
    extra = COLUMN_GAP + col_w + EDGE if beside else 0.0
    spaces = pdf_free_space.occupancy(pdf_bytes, {
        index: (frames[index][0], _to_display(_frame(*frames[index])[1]), *sizes[index])
        for index in on_page}) if on_page else {}
    images = XObjectStore(writer, uploads_dir)
    glyphs = GlyphFonts(writer)
    head = writer._add_object(_stream(b"q\n"))
    bare = {}                   # (size, matrix) → the strip's form, shared by pages without cards
    drawn = 0
    for index, page in enumerate(pages):
        view, rotation = frames[index]
        (page_w, page_h), matrix = _frame(view, rotation)
        size = (page_w + extra, page_h)
        links = ()
        if index in beside or index in on_page:
            drawing = _Drawing(glyphs, images)
            ops = _backdrop(page_w, page_h, extra) if extra else []
            cards = on_page.get(index, [])
            placed = _on_page(cards, spaces[index], page_w, page_h, images, resolve_ref) if cards else []
            for card, rows, x, y, w, h in placed:
                _leader(ops, (x, y, x + w, y + h), card.anchor, card.color)
                pdf_cards.draw(ops, x, y, w, h, rows, drawing, bar=_bar(card))
            count = len(placed)
            if index in beside:
                count += _draw_column(ops, beside[index], page_w, page_h, col_w, drawing, resolve_ref)
            wanted = len(cards) + len(beside.get(index, []))
            if count < wanted:
                log.info(f"[pdf-notes] page {index + 1}: {wanted - count} cards did not fit")
            form = _form(writer, ops, *size, matrix, drawing.resources(writer))
            links, drawn = drawing.links, drawn + count
        elif extra:
            key = (size, matrix)
            if key not in bare:
                bare[key] = _form(writer, _backdrop(page_w, page_h, extra), *size, matrix, DictionaryObject())
            form = bare[key]
        else:
            continue
        if extra:
            _widen(page, view, rotation, extra)
        _stamp(writer, page, form, head)
        for link in links:
            _link(writer, index, matrix, *link)

    glyphs.finalize()
    finalize_cid(writer)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), drawn
