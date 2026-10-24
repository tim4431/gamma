"""Notes beside the page: the annotated-PDF export's notes switch.

The plain annotated export (``pdf_export.annotate_pdf``) keeps a highlight's
notes in its annotation popup: invisible on paper, and a click away in every
viewer. With the switch on, this module also prints them in a column beside
each page, where the viewer shows a highlight's comment threads once a lower
zoom leaves room for them (docs/dev/comments.md "The PDF margin",
``frontend/src/comments/marginLayout.js``): a card per highlight, level with
its first line, the cards stacked down the column so none overlaps.

**The column.** Every page of the copy is widened on the right of the page
as shown (``_widen``): its media and crop boxes grow on the user-space side
the display frame's x axis runs to, so the page's content, its annotations
and the display origin stay where they were, on every /Rotate. The column
has one width for the whole document, a share of its widest page
(``_column_width``), and every page gets it, notes or not, so the pages
keep one width as in the viewer. The strip is painted the viewer's backdrop
grey, which also covers whatever the old crop box hid there.

**A card** holds the highlight's own note and the notes under it as
bullets (``pdf_export.highlight_note_blocks``), typeset from their Markdown
through ``pdf_document.chunks`` in the compact look of a rendered note:
headings, lists, to-dos, quotes, code, tables, pictures and display math;
bold, italic, code, marks, links and inline math within a line. A bar in
the highlight's colour runs down its left edge. In the gap beside the page
a thin bar spans the highlight's lines, and a line joins it to the card.

**Stacking** (``_stack``) is the viewer's ``stackCards`` without an active
card: by their highlight's top, each card at that top or ``CARD_GAP`` under
the card above; then, since a page cannot run on into the next one's
column, the run is pushed up off the page's foot. Cards too tall to fit are
shortened, the tallest first, to one height (``_water_level``), each keeping
``MIN_LINES`` lines and ending in an ellipsis. When that is not enough the
column zooms out (``SCALES``): laid out wider and drawn smaller, as the
viewer's column is at a lower zoom. The full text stays in the popup.

**Drawing.** Everything is laid out in the page's display frame (x right, y
down, /Rotate applied: the frame the viewer stores highlight rects in) and
drawn as a Form XObject whose /Matrix maps that frame to user space. It is
appended to the page's content behind a ``q`` … ``Q`` pair that keeps the
page's own graphics state out. The form has resources of its own, so
nothing on the page is renamed, and the page's content streams are left as
they are: PyPDF2's ``merge_page`` would parse every one of them and write it
back uncompressed. Links in the notes become /Link annotations.
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

from . import vector_text
from .logbuf import log
from .note_markup import TEXT, latex_spans
from .pdf_document import MUTED, QUOTE_COLOR, RULE_COLOR, _ref_label, chunks
from .pdf_export import ExportPdfReader, display_size, page_frame, parse_css_color
from .pdf_glyphs import GlyphFonts
from .pdf_image import XObjectStore
from .pdf_typeset import (
    CODE_BG,
    ITALIC,
    LINK,
    MONO,
    PLAIN,
    TEXT_COLOR,
    Style,
    draw_spans,
    fill_rect,
    finalize_cid,
    font_resources,
    line_metrics,
    num,
    plain,
    resolve,
    span_width,
    spans_width,
    wrap,
)

# The column, in points of the page as shown.
COLUMN_SHARE = 0.45                 # its width, × the document's widest page
COLUMN_MIN, COLUMN_MAX = 170.0, 300.0
COLUMN_GAP = 18.0                   # the page's edge to the cards
EDGE = 12.0                         # the cards' room at the page's top and foot, and right of them
TIE_X, TIE_W = 4.0, 1.4             # the bar beside a highlight's lines, from the page's edge

# A card, in points of the column's own frame (``SCALES``).
CARD_GAP = 6.0
PAD_X, PAD_Y = 7.0, 5.5
BAR = 2.6                           # the highlight's colour down its left edge
RADIUS = 3.0
FONT_SIZE = 8.0
LEADING = 1.3
HEADING_SCALE = {1: 1.3, 2: 1.15, 3: 1.05}
NEST = 10.0                         # a nested note's or a list item's indent
MAX_NEST = 6                        # deeper notes stop marching right
MARKER_GAP = 3.5                    # a bullet or to-do box to its text
BULLET, ELLIPSIS = "•", "…"
QUOTE_PAD = 6.0
QUOTE_BAR = 1.6
CODE_SCALE = 0.92
BLOCK_GAP = 2.5                     # between a highlight's own note and each note under it
PARA_GAP = 2.0                      # a blank line in a note
IMAGE_GAP = 2.5
IMAGE_MAX_H = 150.0
PX_PT = 0.75                        # CSS px → pt: pasted screenshots are 96 dpi
DISPLAY_MATH_SCALE = 1.15

# When the cards do not fit: each keeps MIN_LINES lines (the last an
# ellipsis; a card at rest in the viewer shows three), then the column zooms
# out through SCALES, keeping two lines at the last. A picture or equation
# the cut falls in shrinks to the room left, down to MIN_PICTURE.
MIN_LINES = 3
SCALES = (1.0, 0.85, 0.7, 0.55, 0.45)
MIN_PICTURE = 16.0

BACKDROP = (0.945, 0.945, 0.95)     # the viewer's backdrop, light theme
PAGE_EDGE = (0.80, 0.80, 0.82)
CARD_FILL = (1.0, 1.0, 1.0)
CARD_BORDER = (0.85, 0.85, 0.87)

# A highlight's notes and where its lines are on the page as shown: their
# top and bottom, and the middle of the first line.
Note = namedtuple("Note", "blocks top bottom mid color")

# One drawable piece of a card, from the card's content top-left: "text"
# (data: spans, size, ascent, colour), "fill" (data: colour), "image" (data:
# the XObject's name), "math" (data: drawing, scale) or "check" (data:
# ticked). ``h`` is the height it takes, so a cut can keep what ends above it.
Row = namedtuple("Row", "kind x top w h data")


# --- a card's notes as rows ----------------------------------------------------

class _Layout:
    """A highlight's notes (``[(depth, markdown)]``) laid out ``width``
    wide: ``rows`` from the content's top-left and ``height``, the height
    they take. Depth 0 is the highlight's own note; a deeper one is a note
    under it, a bullet indented by its depth."""

    def __init__(self, blocks, width: float, images, resolve_ref=None):
        self.width, self.images, self.resolve_ref = width, images, resolve_ref
        self.rows, self.y, self.gap = [], 0.0, 0.0
        for depth, md in blocks:
            if self.rows:
                self.gap = max(self.gap, BLOCK_GAP)
            self._markdown(md, min(depth, MAX_NEST) * NEST, BULLET if depth else None)
        self.height = self.y

    def _place(self) -> float:
        """The top of the next row: the pending gap goes in before it, never
        after the last one."""
        self.y += self.gap
        self.gap = 0.0
        return self.y

    def _markdown(self, md: str, x: float, marker):
        """One note's chunks at ``x``; ``marker`` hangs left of its first row."""
        for c in chunks(md, self.resolve_ref):
            kind = c["kind"]
            if kind == "gap":
                if self.rows:
                    self.gap = max(self.gap, PARA_GAP)
                continue
            if kind == "rule":
                top = self._place()
                self.rows.append(Row("fill", x, top + 2.0, self.width - x, 0.6, RULE_COLOR))
                self.y = top + 4.6
                continue
            if marker and kind != "text":
                self._marker(marker, x, self._place(), FONT_SIZE)
                marker = None
            if kind == "text":
                self._text(c, x, marker)
                marker = None
            elif kind == "image":
                self._image(c, x)
            elif kind == "math":
                self._display_math(c["tex"], x)
            elif kind == "code":
                self._code(c["lines"], x)
            elif kind == "table":
                for row in c["rows"]:
                    spans = [s for n, cell in enumerate(row)
                             for s in ([(TEXT, "   ", 0, PLAIN)] if n else []) + cell]
                    self._lines(spans, x)
            elif kind == "embed":
                label = _ref_label(c["id"], self.resolve_ref)
                self._lines([(TEXT, label, 0, Style(LINK | ITALIC, None))], x)

    def _text(self, c, x: float, marker):
        level, sub = c.get("heading"), c.get("sub", 0)
        size = FONT_SIZE * HEADING_SCALE.get(level, 1.0)
        check = c.get("todo")
        if check is not None:                       # its box hangs in the indent, for a bullet
            sub, marker = max(sub, 1), None
        elif "bullet" in c:
            marker = c["bullet"] or BULLET          # a numbered item keeps its number
        tx = x + sub * NEST
        bar = None
        if c.get("quote"):
            tx += QUOTE_PAD
            bar = RULE_COLOR
        self._lines(c["spans"], tx, size, QUOTE_COLOR if bar else TEXT_COLOR,
                    marker=marker, check=check, bar=bar)

    def _lines(self, spans, x: float, size: float = FONT_SIZE, color=TEXT_COLOR,
               marker=None, check=None, bar=None, background=None, offset: float = 0.0):
        """Wrapped prose at ``x`` (its text ``offset`` further in): one text
        row per line, with the line's quote bar or background as a fill row
        under it, and the marker or to-do box hanging left of the first (and
        of the bar)."""
        width = max(8.0, self.width - x - offset)
        hang = x - QUOTE_PAD if bar else x
        for n, (indent, line) in enumerate(wrap(resolve(spans, size, width), width, size, hang=0.0)):
            ascent, h = line_metrics(line, size, LEADING)
            top = self._place()
            if background:
                self.rows.append(Row("fill", x - 2.0, top, self.width - x + 2.0, h, background))
            if bar:
                self.rows.append(Row("fill", x - QUOTE_PAD, top, QUOTE_BAR, h, bar))
            if not n and marker:
                self._marker(marker, hang, top, size)
            if not n and check is not None:
                side = size * 0.68
                self.rows.append(Row("check", hang - MARKER_GAP - side, top + ascent - side * 0.9,
                                     side, side, check))
            self.rows.append(Row("text", x + offset + indent, top, spans_width(line, size), h,
                                 (line, size, ascent, color)))
            self.y = top + h

    def _marker(self, marker: str, x: float, top: float, size: float):
        ascent, h = line_metrics([], size, LEADING)
        w = span_width(marker, size)
        self.rows.append(Row("text", x - MARKER_GAP - w, top, w, h,
                             ([(TEXT, marker, 0, PLAIN)], size, ascent, MUTED)))

    def _image(self, c, x: float):
        info = self.images.resolve(c["src"])
        if not info:
            self._lines([(TEXT, (c.get("alt") or "").strip() or "image", 0, Style(ITALIC, None))],
                        x, color=MUTED)
            return
        name, px_w, px_h = info
        w = min(self.width - x, (c.get("px_w") or px_w) * PX_PT)
        h = w * px_h / max(px_w, 1)
        if h > IMAGE_MAX_H:
            w, h = w * IMAGE_MAX_H / h, IMAGE_MAX_H
        top = self._place()
        self.rows.append(Row("image", x, top, w, h, name))
        self.y, self.gap = top + h, IMAGE_GAP

    def _display_math(self, tex: str, x: float):
        drawn = vector_text.math(tex, FONT_SIZE * DISPLAY_MATH_SCALE)
        if not drawn:                               # no ziamath, or it choked
            self._lines(plain(latex_spans(tex)), x)
            return
        drawing, w, h, _ascent = drawn
        room = self.width - x
        scale = min(1.0, room / w) if w else 1.0
        top = self._place()
        self.rows.append(Row("math", x + max(0.0, (room - w * scale) / 2), top, w * scale, h * scale,
                             (drawing, scale)))
        self.y, self.gap = top + h * scale, IMAGE_GAP

    def _code(self, lines, x: float):
        """Fenced code on a tint, line by line so a cut keeps whole lines.
        Leading spaces are an x offset: wrapping drops them."""
        size = FONT_SIZE * CODE_SCALE
        style = Style(MONO, None)
        space = span_width(" ", size, 0, style)
        self.gap = max(self.gap, 1.5)
        for line in lines or [""]:
            body = (line or "").rstrip()
            offset = min((len(body) - len(body.lstrip(" "))) * space, (self.width - x) * 0.5)
            self._lines([(TEXT, body.strip() or " ", 0, style)], x, size, background=CODE_BG, offset=offset)
        self.gap = 1.5


def _clip(rows, height: float, cap: float):
    """The rows that fit ``cap`` high, and the height they take: all of them
    when they fit, else those ending above a closing ellipsis, a picture or
    equation the cut falls in shrunk to the room left (when that is at least
    ``MIN_PICTURE``)."""
    if height <= cap:
        return rows, height
    ascent, line_h = line_metrics([], FONT_SIZE, LEADING)
    limit = cap - line_h

    def shrinks(r):
        return r.kind in ("image", "math") and limit - r.top >= MIN_PICTURE

    # The cut is at the top of the first row that does not fit, and what
    # starts there goes with it: a line's marker, to-do box, bar and tint,
    # a picture's bullet.
    cut = min((r.top for r in rows if r.top + r.h > limit and not shrinks(r)), default=math.inf)
    kept, bottom = [], 0.0
    for r in rows:
        if r.top >= cut:
            continue
        if r.top + r.h > limit:
            f = (limit - r.top) / r.h
            data = (r.data[0], r.data[1] * f) if r.kind == "math" else r.data
            r = r._replace(w=r.w * f, h=r.h * f, data=data)
        kept.append(r)
        bottom = max(bottom, r.top + r.h)
    w = span_width(ELLIPSIS, FONT_SIZE)
    kept.append(Row("text", 0.0, bottom, w, line_h, ([(TEXT, ELLIPSIS, 0, PLAIN)], FONT_SIZE, ascent, MUTED)))
    return kept, bottom + line_h


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


def _column(notes, col_w: float, page_h: float, images, resolve_ref=None):
    """Lays a page's notes (sorted by top) out down the column: ``(scale,
    [(note, rows, height, top)])``, the cards in the column's own frame,
    which is drawn ``scale`` times its size from the column's top-left.
    Notes that do not fit even at the last scale are left out."""
    for scale in SCALES:
        width, height = col_w / scale, page_h / scale
        layouts = [_Layout(n.blocks, width - BAR - 2 * PAD_X, images, resolve_ref) for n in notes]
        room = height - 2 * EDGE - CARD_GAP * (len(notes) - 1)
        level = _water_level([lay.height + 2 * PAD_Y for lay in layouts], room)
        floor = 2 * PAD_Y + (MIN_LINES if scale != SCALES[-1] else 2) * FONT_SIZE * LEADING
        if level >= floor:
            break
    cards = [_clip(lay.rows, lay.height, max(level, floor) - 2 * PAD_Y) for lay in layouts]
    heights = [h + 2 * PAD_Y for _rows, h in cards]
    keep = len(notes)
    while keep and sum(heights[:keep]) + CARD_GAP * (keep - 1) > height - 2 * EDGE:
        keep -= 1
    tops = _stack([n.top / scale for n in notes[:keep]], heights[:keep], EDGE, height - EDGE)
    return scale, [(n, rows, h, top) for n, (rows, _h), h, top in zip(notes, cards, heights, tops)]


# --- drawing ------------------------------------------------------------------

def _rounded(ops, x: float, y: float, w: float, h: float, r: float):
    """A rounded rectangle's path."""
    k = r * 0.5523
    pts = (x + r, y, x + w - r, y,
           x + w - r + k, y, x + w, y + r - k, x + w, y + r,
           x + w, y + h - r,
           x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h,
           x + r, y + h,
           x + r - k, y + h, x, y + h - r + k, x, y + h - r,
           x, y + r,
           x, y + r - k, x + r - k, y, x + r, y)
    ops.append(b"%s %s m %s %s l %s %s %s %s %s %s c %s %s l %s %s %s %s %s %s c "
               b"%s %s l %s %s %s %s %s %s c %s %s l %s %s %s %s %s %s c h" % tuple(num(v) for v in pts))


def _draw_row(ops, r: Row, x: float, y: float, glyphs, fonts, links, xobjects, images):
    """One row with the content's top-left at (``x``, ``y``)."""
    x, y = x + r.x, y + r.top
    if r.kind == "text":
        spans, size, ascent, color = r.data
        draw_spans(ops, x, y + ascent, spans, size, color=color, fonts=fonts, links=links, glyphs=glyphs)
    elif r.kind == "fill":
        fill_rect(ops, x, y, x + r.w, y + r.h, r.data)
    elif r.kind == "image":
        # The frame has y running down, so the image matrix flips it back.
        ops.append(b"q %s 0 0 %s %s %s cm /%s Do Q" % (num(r.w), num(-r.h), num(x), num(y + r.h),
                                                     r.data.encode()))
        xobjects[r.data] = images.refs[r.data]
    elif r.kind == "math":
        drawing, scale = r.data
        ops += [b"q %s 0 0 %s %s %s cm" % (num(scale), num(scale), num(x), num(y)),
                glyphs.draw(drawing), b"Q"]
    elif r.kind == "check":
        s = r.w
        ops.append(b"%s %s %s RG 0.6 w %s %s %s %s re S" % (
            num(MUTED[0]), num(MUTED[1]), num(MUTED[2]), num(x), num(y), num(s), num(s)))
        if r.data:
            ops.append(b"1 w %s %s m %s %s l %s %s l S" % (
                num(x + s * 0.2), num(y + s * 0.55), num(x + s * 0.42), num(y + s * 0.8),
                num(x + s * 0.82), num(y + s * 0.22)))


def _draw_card(ops, y: float, w: float, h: float, rows, color, glyphs, fonts, links, xobjects, images):
    """A card at the column's left, ``y`` from its top: white, edged, the
    highlight's colour down its left side, the rows inside its padding."""
    rgb = color[:3]
    ops.append(b"q %s %s %s rg" % tuple(num(v) for v in CARD_FILL))
    _rounded(ops, 0.0, y, w, h, RADIUS)
    ops.append(b"f")
    _rounded(ops, 0.0, y, w, h, RADIUS)             # the bar, clipped to the corners
    ops.append(b"W n")
    fill_rect(ops, 0.0, y, BAR, y + h, rgb)
    ops.append(b"Q q %s %s %s RG 0.6 w" % tuple(num(v) for v in CARD_BORDER))
    _rounded(ops, 0.0, y, w, h, RADIUS)
    ops.append(b"S Q")
    for r in rows:
        _draw_row(ops, r, BAR + PAD_X, y + PAD_Y, glyphs, fonts, links, xobjects, images)


def _backdrop(page_w: float, page_h: float, extra: float) -> list:
    """The strip beside the page, and the page's edge."""
    ops = []
    fill_rect(ops, page_w, 0.0, page_w + extra, page_h, BACKDROP)
    fill_rect(ops, page_w, 0.0, page_w + 0.6, page_h, PAGE_EDGE)
    return ops


def _draw_page(notes, page_w: float, page_h: float, extra: float, col_w: float, writer,
               images, glyphs, resolve_ref):
    """One page's strip and cards: ``(ops, resources, links, cards drawn)``,
    the links ``(x0, y0, x1, y1, href)`` in the page's display frame."""
    ops = _backdrop(page_w, page_h, extra)
    scale, placed = _column(sorted(notes, key=lambda n: n.top), col_w, page_h, images, resolve_ref)
    col_x = page_w + COLUMN_GAP
    # In the gap: a bar beside each highlight's lines, and a line from it to
    # its card, at the same height within the card as the first line's
    # middle is within the highlight (level, when the card is).
    for note, _rows, h, top in placed:
        line = tuple(c * 0.7 for c in note.color[:3])
        tie = page_w + TIE_X
        fill_rect(ops, tie, note.top, tie + TIE_W, max(note.bottom, note.top + 2.0), line)
        at = min(max(top * scale + note.mid - note.top, (top + PAD_Y) * scale), (top + h - PAD_Y) * scale)
        ops.append(b"%s %s %s RG 0.5 w %s %s m %s %s l S" % (
            num(line[0]), num(line[1]), num(line[2]), num(tie + TIE_W), num(note.mid), num(col_x), num(at)))
    fonts, links, xobjects = set(), [], {}
    ops.append(b"q %s 0 0 %s %s 0 cm" % (num(scale), num(scale), num(col_x)))
    for note, rows, h, top in placed:
        _draw_card(ops, top, col_w / scale, h, rows, note.color, glyphs, fonts, links, xobjects, images)
    ops.append(b"Q")
    resources = DictionaryObject()
    font_dict = font_resources(sorted(fonts), writer)
    font_dict.update({NameObject("/" + name): ref for name, ref in glyphs.resources().items()})
    if font_dict:
        resources[NameObject("/Font")] = font_dict
    if xobjects:
        resources[NameObject("/XObject")] = DictionaryObject(
            {NameObject("/" + name): ref for name, ref in xobjects.items()})
    links = [(col_x + x0 * scale, y0 * scale, col_x + x1 * scale, y1 * scale, href)
             for x0, y0, x1, y1, href in links]
    return ops, resources, links, len(placed)


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
    """A highlight's lines on the page as shown: ``(top, bottom, mid)``,
    ``mid`` the middle of its first line; None without a usable rect. Rects
    are stored in the frame of the position's width × height."""
    rw = float(pos.get("width") or 0) or page_w
    rh = float(pos.get("height") or 0) or page_h
    lines = []
    for r in pos.get("rects") or []:
        if not r or r.get("y1") is None or r.get("y2") is None:
            continue
        y1, y2 = sorted(min(max(float(v) / rh * page_h, 0.0), page_h) for v in (r["y1"], r["y2"]))
        lines.append((y1, y2))
    if not lines:
        return None
    top, first = min(lines)
    return top, max(y2 for _y1, y2 in lines), top + min(first - top, 2 * FONT_SIZE) / 2


# --- entry point --------------------------------------------------------------

def render_notes(pdf_bytes: bytes, notes, uploads_dir=None, resolve_ref=None) -> tuple[bytes, int]:
    """Return (pdf bytes with the notes beside its pages, cards drawn).

    ``notes``: the marks ``pdf_export.annotate_pdf`` takes, ``[{position:
    <pdf_position dict>, color: <css string>, note: <str>, blocks:
    [(depth, markdown)]}]``; ``blocks`` is what the card shows
    (``pdf_export.highlight_note_blocks``), and without it ``note`` is one
    block. Notes without text, without usable rects or on a page out of
    range are skipped; nothing to draw leaves the bytes as they are.
    ``uploads_dir`` is where ``/api/uploads/…`` pictures are read from
    (without it they read as their alt text), and ``resolve_ref`` (block
    id → {content, …} or None) reads ``[[refs]]`` as the notes PDF does.
    """
    reader = ExportPdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    writer.append(reader)
    pages = writer.pages
    frames = [page_frame(page) for page in pages]
    sizes = [display_size(view, rotation) for view, rotation in frames]

    by_page: dict[int, list] = {}
    for n in notes:
        blocks = [(depth, md.strip()) for depth, md in (n.get("blocks") or [(0, n.get("note") or "")])
                  if (md or "").strip()]
        pos = n.get("position") or {}
        try:
            page_num = int(pos.get("pageNumber") or 0)
        except (TypeError, ValueError):
            continue
        if not blocks or not 1 <= page_num <= len(pages):
            continue
        anchor = _anchor(pos, *sizes[page_num - 1])
        if anchor:
            by_page.setdefault(page_num - 1, []).append(Note(blocks, *anchor, parse_css_color(n.get("color"))))
    if not by_page:
        return pdf_bytes, 0

    col_w = _column_width(max(w for w, _h in sizes))
    extra = COLUMN_GAP + col_w + EDGE
    images = XObjectStore(writer, uploads_dir)
    glyphs = GlyphFonts(writer)
    head = writer._add_object(_stream(b"q\n"))
    bare = {}                   # (size, matrix) → the strip's form, shared by pages without notes
    drawn = 0
    for index, page in enumerate(pages):
        view, rotation = frames[index]
        (page_w, page_h), matrix = _frame(view, rotation)
        size = (page_w + extra, page_h)
        if index in by_page:
            ops, resources, links, count = _draw_page(by_page[index], page_w, page_h, extra, col_w,
                                                      writer, images, glyphs, resolve_ref)
            if count < len(by_page[index]):
                log.info(f"[pdf-notes] page {index + 1}: {len(by_page[index]) - count} notes "
                         f"did not fit beside the page")
            form = _form(writer, ops, *size, matrix, resources)
            drawn += count
        else:
            key, links = (size, matrix), ()
            if key not in bare:
                bare[key] = _form(writer, _backdrop(page_w, page_h, extra), *size, matrix, DictionaryObject())
            form = bare[key]
        _widen(page, view, rotation, extra)
        _stamp(writer, page, form, head)
        for link in links:
            _link(writer, index, matrix, *link)

    glyphs.finalize()
    finalize_cid(writer)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), drawn
