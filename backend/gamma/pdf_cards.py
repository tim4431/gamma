"""Cards: a highlight's notes, or one of its comment threads, typeset as a
small box for the annotated PDF (``pdf_notes`` places them).

A card's content is laid out once (``notes``, ``thread``) into rows, each a
drawable piece measured from the content's top-left; ``clip`` shortens a
card to a height, and ``draw`` paints one at a point of a y-down frame.

**Notes** are the highlight's own note and the notes under it,
``(depth, markdown)`` as ``pdf_export.highlight_note_blocks`` walks them,
each deeper one a bullet indented by its depth. Their Markdown goes through
``pdf_document.chunks``, the parser the notes PDF and text boxes use, and
is set in the compact look of a rendered note: headings a little larger,
lists, to-dos with a drawn box, quotes with a bar, fenced code on a tint,
tables a line per row, pictures, display math; bold, italic, code, marks,
links and inline math within a line. A note card carries the highlight's
colour down its left edge.

**A thread** is its comments oldest first, each as the viewer's margin
draws it (``frontend/src/comments/Thread.jsx``): the author's avatar, an
initial on their colour (``person_color``), their name and the day, and the
comment's Markdown under them, past the avatar. The replies step in to the
first comment's text, so each thread reads as one comment and its answers
even where several follow one another under a note. A thread on one of the
notes goes under that note in the note card, as replies to it; on a card
of its own (the notes elsewhere, or not printed) it opens with the note's
first line, quoted, so the reply keeps what it answers.
"""

import math
from collections import namedtuple

from . import vector_text
from .note_markup import TEXT, latex_spans
from .pdf_document import MUTED, QUOTE_COLOR, RULE_COLOR, _ref_label, chunks, inline
from .pdf_typeset import (
    BOLD,
    CODE_BG,
    ITALIC,
    LINK,
    MONO,
    PLAIN,
    TEXT_COLOR,
    Style,
    draw_spans,
    fill_rect,
    line_metrics,
    num,
    plain,
    resolve,
    span_width,
    spans_width,
    wrap,
)

# A card, in points of the frame it is drawn in.
PAD_X, PAD_Y = 7.0, 5.5
BAR = 2.6                           # the highlight's colour down a note card's left edge
RADIUS = 3.0
FONT_SIZE = 8.0
LEADING = 1.3
HEADING_SCALE = {1: 1.3, 2: 1.15, 3: 1.05}
NEST = 10.0                         # a nested note's or a list item's indent
MAX_NEST = 6                        # deeper notes stop marching right
MARKER_GAP = 3.5                    # a bullet or to-do box to its text
BULLET, ELLIPSIS = "\N{BULLET}", "\N{HORIZONTAL ELLIPSIS}"
QUOTE_PAD = 6.0
QUOTE_BAR = 1.6
CODE_SCALE = 0.92
BLOCK_GAP = 2.5                     # between a highlight's own note and each note under it
PARA_GAP = 2.0                      # a blank line in a note
IMAGE_GAP = 2.5
IMAGE_MAX_H = 150.0
PX_PT = 0.75                        # CSS px → pt: pasted screenshots are 96 dpi
DISPLAY_MATH_SCALE = 1.15
MIN_PICTURE = 16.0                  # a picture a cut falls in shrinks to the room left, down to this
AVATAR = 11.0                       # a comment's avatar, across
AVATAR_GAP = 4.0                    # the avatar to the name, and the name to the day
COMMENT_GAP = 5.0                   # between two comments of a thread, and a note and its replies
REPLY_INDENT = 6.0                  # a note's replies, past its text
DAY_SCALE = 0.9                     # the day beside the name

CARD_FILL = (1.0, 1.0, 1.0)
CARD_BORDER = (0.85, 0.85, 0.87)
WHITE = (1.0, 1.0, 1.0)

# The eight collaborator colours, --peer-0 … --peer-7 (frontend app.css).
PEOPLE = ("#e0534f", "#2f8f9d", "#d98a1f", "#7a5cc7", "#2f9e5a", "#c94c9a", "#4e7be0", "#8a7a2b")

# One drawable piece of a card, from the content's top-left: "text" (data:
# spans, size, ascent, colour), "fill" (data: colour), "image" (data: the
# XObject's name), "math" (data: drawing, scale), "check" (data: ticked) or
# "avatar" (data: initial, colour). ``h`` is the height it takes, so a cut
# can keep what ends above it.
Row = namedtuple("Row", "kind x top w h data")


def person_color(name: str):
    """A person's colour from their name, as the app picks it
    (``frontend/src/collaboration/personColor.js``): ``(r, g, b)`` in 0..1."""
    h = 0
    for ch in str(name or ""):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    value = PEOPLE[h % len(PEOPLE)]
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def inner_width(card_w: float, bar: bool) -> float:
    """The content's width in a card ``card_w`` wide, with or without the bar."""
    return card_w - 2 * PAD_X - (BAR if bar else 0.0)


# --- content as rows ------------------------------------------------------------

class Layout:
    """A card's content laid out ``width`` wide: ``rows`` from the content's
    top-left, ``height`` the height they take, ``squeeze`` the least a
    picture or equation had to shrink to fit the width (1 when none did)."""

    def __init__(self, width: float, images, resolve_ref=None):
        self.width, self.images, self.resolve_ref = width, images, resolve_ref
        self.rows, self.y, self.gap, self.squeeze = [], 0.0, 0.0, 1.0

    @property
    def height(self) -> float:
        return self.y

    @property
    def natural_width(self) -> float:
        """How wide the rows reach: a one-line note needs no full-width card."""
        return max((r.x + r.w for r in self.rows), default=0.0)

    def _place(self) -> float:
        """The top of the next row: the pending gap goes in before it, never
        after the last one."""
        self.y += self.gap
        self.gap = 0.0
        return self.y

    def _space(self, gap: float):
        if self.rows:
            self.gap = max(self.gap, gap)

    # -- the two kinds of card --

    def notes(self, blocks):
        """A highlight's notes, ``[(depth, markdown[, threads])]``: depth 0
        its own note, a deeper one a bullet indented by its depth. A note's
        threads (each its comments, oldest first) go under it, a little
        past its text, as replies to it."""
        for depth, md, *rest in blocks:
            x = min(depth, MAX_NEST) * NEST
            self._space(BLOCK_GAP)
            self._markdown(md, x, BULLET if depth else None)
            for comments in rest[0] if rest else ():
                self._space(COMMENT_GAP)
                self._comments(comments, x + REPLY_INDENT)
        return self

    def thread(self, comments, quote: str = ""):
        """A thread's comments, oldest first: ``{author, created_at, body,
        deleted}`` each. ``quote``: the note the thread answers, when the
        card stands apart from it, as one line over the comments."""
        if quote.strip():
            self._quote(quote)
            self._space(COMMENT_GAP)
        self._comments(comments, 0.0)
        return self

    def _quote(self, md: str):
        """A note's first line, as one line behind a quote bar: italic and
        muted, ending in an ellipsis when there is more of it."""
        line = md.strip().split("\n")[0]
        style = Style(ITALIC, None)
        more = len(md.strip().split("\n")) > 1
        room = max(8.0, self.width - QUOTE_PAD - span_width(ELLIPSIS, FONT_SIZE))
        lines = wrap(resolve(inline(line, style, self.resolve_ref), FONT_SIZE, room), room, FONT_SIZE, hang=0.0)
        first = lines[0][1]
        if more or len(lines) > 1:
            first = first + [(TEXT, ELLIPSIS, 0, style)]
        ascent, h = line_metrics(first, FONT_SIZE, LEADING)
        top = self._place()
        self.rows.append(Row("fill", 0.0, top, QUOTE_BAR, h, RULE_COLOR))
        self.rows.append(Row("text", QUOTE_PAD, top, spans_width(first, FONT_SIZE), h,
                             (first, FONT_SIZE, ascent, QUOTE_COLOR)))
        self.y = top + h

    def _comments(self, comments, x: float):
        """A thread's comments, as the viewer's margin draws them: the
        author's avatar, name and day, and the text under them past the
        avatar. The first is at ``x``; the replies to it step in to its
        text."""
        for n, c in enumerate(comments):
            if n:
                self._space(COMMENT_GAP)
            at = x + (AVATAR + AVATAR_GAP if n else 0.0)
            indent = at + AVATAR + AVATAR_GAP
            name = (c.get("author") or "").strip()
            initial = name[0].upper() if name and name != "Anonymous" else "?"
            ascent, line_h = line_metrics([], FONT_SIZE, LEADING)
            h = max(line_h, AVATAR)
            top = self._place()
            self.rows.append(Row("avatar", at, top + (h - AVATAR) / 2, AVATAR, AVATAR,
                                 (initial, person_color(name))))
            base = top + (h - line_h) / 2
            who = [(TEXT, name or "Anonymous", 0, Style(BOLD, None))]
            w = spans_width(who, FONT_SIZE)
            self.rows.append(Row("text", indent, base, w, line_h, (who, FONT_SIZE, ascent, TEXT_COLOR)))
            day = (c.get("created_at") or "")[:10]
            if day:
                size = FONT_SIZE * DAY_SCALE
                when = [(TEXT, day, 0, PLAIN)]
                self.rows.append(Row("text", indent + w + AVATAR_GAP, base + ascent - size * 0.82,
                                     spans_width(when, size), line_h, (when, size, size * 0.82, MUTED)))
            self.y = top + h
            if c.get("deleted"):
                self._lines([(TEXT, "Comment deleted", 0, Style(ITALIC, None))], indent, color=MUTED)
            else:
                self._markdown(c.get("body") or "", indent, None)

    # -- Markdown --

    def _markdown(self, md: str, x: float, marker):
        """One note's chunks at ``x``; ``marker`` hangs left of its first row."""
        for c in chunks(md, self.resolve_ref):
            kind = c["kind"]
            if kind == "gap":
                self._space(PARA_GAP)
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
        natural = (c.get("px_w") or px_w) * PX_PT
        w = min(self.width - x, natural)
        h = w * px_h / max(px_w, 1)
        if h > IMAGE_MAX_H:
            w, h = w * IMAGE_MAX_H / h, IMAGE_MAX_H
        self.squeeze = min(self.squeeze, w / max(natural, 1.0))
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
        self.squeeze = min(self.squeeze, scale)
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
        self._space(1.5)
        for line in lines or [""]:
            body = (line or "").rstrip()
            offset = min((len(body) - len(body.lstrip(" "))) * space, (self.width - x) * 0.5)
            self._lines([(TEXT, body.strip() or " ", 0, style)], x, size, background=CODE_BG, offset=offset)
        self.gap = 1.5


def clip(rows, height: float, cap: float):
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


# --- drawing ----------------------------------------------------------------------

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


def _row(ops, r: Row, x: float, y: float, page):
    """One row with the content's top-left at (``x``, ``y``)."""
    x, y = x + r.x, y + r.top
    if r.kind == "text":
        spans, size, ascent, color = r.data
        draw_spans(ops, x, y + ascent, spans, size, color=color, fonts=page.fonts, links=page.links,
                   glyphs=page.glyphs)
    elif r.kind == "fill":
        fill_rect(ops, x, y, x + r.w, y + r.h, r.data)
    elif r.kind == "image":
        # The frame has y running down, so the image matrix flips it back.
        ops.append(b"q %s 0 0 %s %s %s cm /%s Do Q" % (num(r.w), num(-r.h), num(x), num(y + r.h),
                                                     r.data.encode()))
        page.xobjects[r.data] = page.images.refs[r.data]
    elif r.kind == "math":
        drawing, scale = r.data
        ops += [b"q %s 0 0 %s %s %s cm" % (num(scale), num(scale), num(x), num(y)),
                page.glyphs.draw(drawing), b"Q"]
    elif r.kind == "check":
        s = r.w
        ops.append(b"%s %s %s RG 0.6 w %s %s %s %s re S" % (
            num(MUTED[0]), num(MUTED[1]), num(MUTED[2]), num(x), num(y), num(s), num(s)))
        if r.data:
            ops.append(b"1 w %s %s m %s %s l %s %s l S" % (
                num(x + s * 0.2), num(y + s * 0.55), num(x + s * 0.42), num(y + s * 0.8),
                num(x + s * 0.82), num(y + s * 0.22)))
    elif r.kind == "avatar":
        initial, color = r.data
        ops.append(b"%s %s %s rg" % tuple(num(v) for v in color))
        _rounded(ops, x, y, r.w, r.h, r.w / 2)
        ops.append(b"f")
        size = r.w * 0.55
        spans = [(TEXT, initial, 0, Style(BOLD, None))]
        draw_spans(ops, x + (r.w - spans_width(spans, size)) / 2, y + r.h / 2 + size * 0.36, spans, size,
                   color=WHITE, fonts=page.fonts, glyphs=page.glyphs)


def draw(ops, x: float, y: float, w: float, h: float, rows, page, bar=None):
    """A card with its top-left at (``x``, ``y``): white, edged, rounded,
    ``bar`` (a colour) down its left side, the rows inside its padding.
    ``page`` collects what the drawing uses: ``fonts``, ``links``,
    ``xobjects``, and holds the document's ``glyphs`` and ``images``."""
    ops.append(b"q %s %s %s rg" % tuple(num(v) for v in CARD_FILL))
    _rounded(ops, x, y, w, h, RADIUS)
    ops.append(b"f")
    if bar:
        _rounded(ops, x, y, w, h, RADIUS)           # the bar, clipped to the corners
        ops.append(b"W n")
        fill_rect(ops, x, y, x + BAR, y + h, bar[:3])
    ops.append(b"Q q %s %s %s RG 0.6 w" % tuple(num(v) for v in CARD_BORDER))
    _rounded(ops, x, y, w, h, RADIUS)
    ops.append(b"S Q")
    for r in rows:
        _row(ops, r, x + PAD_X + (BAR if bar else 0.0), y + PAD_Y, page)
