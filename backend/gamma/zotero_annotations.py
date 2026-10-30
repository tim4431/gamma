"""Zotero annotation items and Gamma's annotation blocks, mapped both ways:
the shape rules of a Zotero link (docs/dev/zotero_sync.md). The engine
decides what travels and when; this module says what a block looks like as
items, what an item looks like as a block, and how one field of an item
lands on a block it already has.

A highlight is a ``highlight`` (or the ``underline`` Zotero made it), an area
highlight an ``image``, a text box on a PDF page a ``text`` (or the ``note``
Zotero made it), and an ink group one ``ink`` item per look, since an item has
one colour and one width. Zotero keeps positions in the page's user space,
Gamma in the page as shown; every conversion goes through the frame (view box
and rotation) and pdf_export's mappers, never a plain media-box flip, so
cropped, offset and turned pages land where they are drawn. ``Pages`` reads a
stored PDF once per round for the frames, the characters and the page labels.

The fields Zotero derives are computed as its reader computes them: the sort
index from the top-most rect (the reader's "top-most" is the one with the
greatest x2), the index of the character nearest it among the page's
characters, and the page height above it; the page label as pdf.js reads
the PDF's own /PageLabels, else the page number, as the reader labels an
annotation (``item_label``), so the reader's numbering of later annotations
follows the book's. A comment is the block's own note and the notes nested under it, as
the RDF export's popup text writes them, with bold and italic as the
``<b>``/``<i>`` tags the reader shows; a ``text`` annotation's comment is
plain text, as the reader draws it.

Each item has a projection (``projection``: type, colour, comment, quote,
position rounded to hundredths of a point) that the engine keeps two bases
of, one per side, so a lossy mapping (a turned text box, a ``<sub>`` comment,
pressure in a stroke) never reads as a change of the other side. Applying a
pulled change touches only the fields that changed (``apply_fields``). An ink
item owns the ids of its strokes: a row keeps its item when its strokes change
look, and a pull replaces exactly the strokes the item owns. A position over
Zotero's limit is split into pieces, each its own item, as the Zotero client
splits one.
"""

import hashlib
import html
import json
import math
import os
import threading
from collections import OrderedDict
from typing import NamedTuple

from . import colors, highlights, text_box
from . import ink as inkmod
from .highlight_relocate import to_viewer_rect
from .note_markup import INLINE_RE, LINK_PARTS
from .pdf_export import (annotation_key, display_size, pdf_point_to_viewer, still_embedded,
                         viewer_point_to_pdf, viewer_rect_to_pdf)
from .pdf_text import GlyphPages, has_box, quote_under
from .zotero_notes import note_tree

TYPES = ("highlight", "underline", "image", "ink", "note", "text")
TEXT_KINDS = ("highlight", "underline")      # the kinds that carry annotationText
# The PDF annotation Zotero embeds for each kind: the subtype an import key names.
SUBTYPES = {"highlight": "/Highlight", "underline": "/Underline", "image": "/Square", "ink": "/Ink",
            "note": "/Text", "text": "/FreeText"}
DEFAULT_COLOR = "#ffd400"                    # Zotero's yellow, the reader's first colour
HIGHLIGHT_ALPHA = 0.45                       # a colour without alpha, as every Gamma importer reads it
NOTE_FILL = "#fff4b8"                        # a sticky note's box (text_box.TEXT_BACKGROUNDS' note yellow)
NOTE_SIDE = 22.0                             # a note's icon, in points, as the reader writes its rect
POSITION_MAX = 65000                         # the client's ANNOTATION_POSITION_MAX_SIZE (the server's is 65,535 bytes)
TEXT_MAX, LABEL_MAX = 7500, 50               # what the server keeps of annotationText, and allows of a page label
# The page-label reading a link row's Zotero label is settled under (the row's ``detail.labels``): a row without
# it may hold a label sent as PyMuPDF reads the PDF's (``Pages.mupdf_label``), checked once (``label_correction``)
LABELS = 1
POSITION_TOL = 0.5                           # points apart two positions are still one place (``positions_close``)
PAGES_KEPT = 32                              # pages a ``Pages`` keeps read (a page's glyphs are a megabyte or so)
# Why an annotation block is held (``hold_reason``; a row's ``detail.reason``,
# ``status.waiting.annotations.reasons``)
WAIT_PLACING = "being placed in the new PDF"
WAIT_DASHED = "dashed in Gamma until accepted"
WAIT_LOST = "not found in the new PDF"
WAIT_EMBEDDED = "still drawn in the PDF"
WAIT_NO_PLACE = "not placed on the PDF"
# A projection's fields → the item's.
FIELDS = {"type": "annotationType", "color": "annotationColor", "comment": "annotationComment",
          "text": "annotationText", "position": "annotationPosition"}
_OUT_DIGITS = 3                              # positions as the reader writes them
_DIGITS = 2                                  # the canonical rounding: the hundredths Gamma stores
_FORMATS = ("i", "b", "sub", "sup")          # the reader's comment marks (editor.js supportedFormats)
_WS = " \t\n\r\f"                            # what a note's text is trimmed of (a no-break space is text)


class Item(NamedTuple):
    """One item ``to_items`` makes of a block: its ``slot`` among the
    block's items ("" for the one item of a highlight or a text box; an ink
    group's "0", "1", …; a split piece's "<slot>.<n>"), its item ``fields``
    (no key, version, parent or tags: the engine adds those), the ids of the
    strokes it carries and their ``look`` (ink only)."""

    slot: str
    fields: dict
    stroke_ids: tuple = ()
    look: str = ""


class Page(NamedTuple):
    """One page of a stored PDF as the mapping needs it: its 1-based
    ``number``, its ``view`` box and ``rotation`` (``GlyphPages.frame``), its
    characters as the reader counts them for a sort index (``page_chars``),
    its /PageLabels ``label`` as pdf.js reads it ("" when it has none), and
    its raw ``glyphs`` (a highlight's quote when Zotero sent none)."""

    number: int
    view: tuple
    rotation: int
    chars: list
    label: str
    glyphs: tuple = ()

    @property
    def frame(self) -> tuple:
        return self.view, self.rotation

    @property
    def size(self) -> tuple:
        """The page as shown, in points (``pdf_export.display_size``)."""
        return display_size(self.view, self.rotation)


class Pages:
    """A stored PDF's pages, each read as it is asked for (``GlyphPages``:
    one open, one read per page under pdf_text's lock) and kept while it is
    among the last ``PAGES_KEPT`` asked for, so a round that walks a
    document's annotations in page order reads each page once. A context
    manager; close it when the round is done with the file. ``src`` is a
    path or PDF bytes. Never call it from ``async def``."""

    def __init__(self, src):
        self._src = src
        self._glyphs = GlyphPages(src)
        self._pages: OrderedDict = OrderedDict()
        self._count = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self._glyphs.close()
        self._pages.clear()

    def count(self) -> int:
        """How many pages the file has; 0 when it can't be opened."""
        if self._count is None:
            self._count = self._glyphs.page_count()
        return self._count

    def page(self, n: int) -> Page | None:
        """Page ``n`` (1-based); None past the end or when the file can't
        be read."""
        if n in self._pages:
            self._pages.move_to_end(n)
            return self._pages[n]
        page = None
        if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= self.count():
            glyphs = tuple(self._glyphs.page(n, cache=False))
            view, rotation = self._glyphs.frame(n)
            page = Page(n, tuple(view), rotation, page_chars(glyphs), self._glyphs.label(n), glyphs)
        self._pages[n] = page
        while len(self._pages) > PAGES_KEPT:
            self._pages.popitem(last=False)
        return page

    def label(self, n: int) -> str:
        """The page label Zotero's reader gives an annotation on page ``n``
        (``item_label``), read without the page's glyphs; "" past the end or
        when the file can't be read."""
        return item_label(self._glyphs.label(n), n) if self._has(n) else ""

    def mupdf_label(self, n: int) -> str:
        """``item_label`` of page ``n``'s label as PyMuPDF reads it, its prefix
        undecoded (``GlyphPages.mupdf_label``): the label the create of a
        row without ``detail.labels`` may have carried; "" past the end or
        when the file can't be read."""
        return item_label(self._glyphs.mupdf_label(n), n) if self._has(n) else ""

    def _has(self, n) -> bool:
        return isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= self.count()

    def md5(self) -> str:
        """The file's MD5 (``file_md5``)."""
        return file_md5(self._src)


def item_label(label: str, number: int) -> str:
    """An annotation's ``annotationPageLabel`` on page ``number`` whose
    label as pdf.js reads it is ``label``: the page number when that is ""
    (the reader's ``_getPageLabel``: ``pageLabels[pageIndex] || pageIndex +
    1``; it numbers every page so when pdf.js reads no labels), at most
    ``LABEL_MAX`` characters."""
    return (label or str(number))[:LABEL_MAX]


def label_correction(theirs: str, ours: str, sent: str) -> str:
    """The page label to send over Zotero's ``theirs`` for an annotation
    whose label is not settled (``LABELS``): ``ours`` (``Pages.label``) when
    Zotero holds ``sent``, the label the annotation's create carried
    (``Pages.mupdf_label``), and it is not ours; "" when theirs is ours
    already, or is Zotero's own (its reader's, or one a person typed),
    which stays."""
    return ours if theirs == sent and theirs != ours else ""


def page_chars(glyphs) -> list:
    """A page's characters as the reader counts them for a sort index, from
    ``GlyphPages.page``'s glyphs: whitespace and boxless entries (MuPDF's
    line breaks, zero-advance marks) left out, and a ligature MuPDF spells
    out as several characters kept as one, as pdf.js keeps it. MuPDF gives
    each character after a ligature's first the box of a glyph of its own
    starting where the next glyph starts (the same left edge, bottom and
    top), so a character whose box shares three sides with the next glyph's
    is taken as the tail of the one before it."""
    out = []
    for k, (c, box) in enumerate(glyphs):
        if c.isspace() or not has_box(box):
            continue
        nxt = glyphs[k + 1][1] if k + 1 < len(glyphs) else None
        if out and nxt is not None and has_box(nxt) and sum(abs(a - b) < 1e-3 for a, b in zip(box, nxt)) >= 3:
            out[-1] = (out[-1][0] + c, out[-1][1])
            continue
        out.append((c, box))
    return out


_md5s: OrderedDict = OrderedDict()
_md5_lock = threading.Lock()
_MD5_MAX = 512


def file_md5(src) -> str:
    """The MD5 of a stored PDF (a path) or of PDF bytes, lowercase hex, as
    Zotero's attachment ``md5`` spells it; "" for a file that can't be
    read. A path's is kept by (path, size, mtime_ns), never by doc id: a
    strip rewrites a stored file in place under the same name."""
    if isinstance(src, (bytes, bytearray)):
        return hashlib.md5(src).hexdigest()
    path = os.path.abspath(os.fspath(src))
    try:
        st = os.stat(path)
    except OSError:
        return ""
    key = (path, st.st_size, st.st_mtime_ns)
    with _md5_lock:
        if key in _md5s:
            _md5s.move_to_end(key)
            return _md5s[key]
    digest = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    with _md5_lock:
        _md5s[key] = digest.hexdigest()
        while len(_md5s) > _MD5_MAX:
            _md5s.popitem(last=False)
    return digest.hexdigest()


# --- numbers, positions, colours ----------------------------------------------------

def _num(value, digits: int):
    """A coordinate rounded to ``digits``, a whole number as an int (as
    JSON.stringify writes it); a value that is no number as it is."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if not math.isfinite(value):
        return value
    r = round(float(value), digits)
    return int(r) if r == int(r) else r


def _dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _parse_position(raw) -> dict | None:
    """``annotationPosition``: a JSON string as Zotero sends it, or a dict."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def _user_rects(position: dict) -> list[tuple]:
    """A position's rects as ``(x0, y0, x1, y1)`` in user space, corners in
    order; the ones that are no four numbers left out."""
    out = []
    for rect in position.get("rects") or ():
        try:
            x1, y1, x2, y2 = (float(v) for v in rect)
        except (TypeError, ValueError):
            continue
        if all(map(math.isfinite, (x1, y1, x2, y2))):
            out.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    return out


def _union(boxes) -> tuple:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _rect_out(box) -> list:
    return [_num(v, _OUT_DIGITS) for v in box]


def _positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _number(value, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def color_out(color) -> str:
    """Any colour Gamma stores (``#rrggbbaa``, ``rgba(…)``, a stroke's
    ``#rrggbb``) → Zotero's lowercase ``#rrggbb``, its alpha dropped;
    Zotero's yellow for what is no colour."""
    return (colors.with_alpha(color, 1.0) or DEFAULT_COLOR)[:7]


def color_in(hex_, keep_alpha: float | None = None) -> str:
    """Zotero's ``#rrggbb`` → a highlight's ``#rrggbbaa``, at ``keep_alpha``
    (the block's own, when a pull recolours it) or ``HIGHLIGHT_ALPHA``;
    Zotero's yellow for what is no colour."""
    alpha = HIGHLIGHT_ALPHA if keep_alpha is None else keep_alpha
    return colors.with_alpha(hex_, alpha) or colors.with_alpha(DEFAULT_COLOR, alpha)


def _alpha(color) -> float | None:
    parsed = colors.parse_color(color)
    return parsed[3] if parsed else None


# --- the sort index ----------------------------------------------------------------

def _rects_dist(a, b) -> float:
    """The reader's ``rectsDist`` (src/pdf/selection.js): the gap between two
    boxes, by the nearest corners when they lie diagonally apart, 0 when they
    meet."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    left, right, bottom, top = bx2 < ax1, ax2 < bx1, by2 < ay1, ay2 < by1
    if top and left:
        return math.hypot(ax1 - bx2, ay2 - by1)
    if left and bottom:
        return math.hypot(ax1 - bx2, ay1 - by2)
    if bottom and right:
        return math.hypot(ax2 - bx1, ay1 - by2)
    if right and top:
        return math.hypot(ax2 - bx1, ay2 - by1)
    if left:
        return ax1 - bx2
    if right:
        return bx1 - ax2
    if bottom:
        return ay1 - by2
    if top:
        return by1 - ay2
    return 0.0


def _sort_rect(position: dict) -> tuple | None:
    """The reader's ``getSortIndexRect``: the rect with the greatest x2 (the
    first of equals), else the paths' bounding box, their width left out."""
    rects = [r for r in position.get("rects") or () if isinstance(r, (list, tuple)) and len(r) == 4]
    if rects:
        return tuple(float(v) for v in max(rects, key=lambda r: float(r[2])))
    points = [float(v) for path in position.get("paths") or () for v in path[:len(path) - len(path) % 2]]
    if not points:
        return None
    xs, ys = points[0::2], points[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def sort_index(page: Page, position: dict) -> str:
    """``annotationSortIndex`` of ``position`` (Zotero's shape, user space)
    on ``page``, as the reader's ``getSortIndex`` makes it:
    ``PPPPP|OOOOOO|TTTTT``, the page index, the index of the character
    nearest the sort rect by ``rectsDist`` (the first of equals; 0 on a page
    without text) and ``floor(max(0, (view[3] - view[1]) - rect[3]))``, the
    view box's height less the rect's top, not offset by ``view[1]``, as the
    reader computes it. Each part is cut to its width and zero-padded, as
    ``formatSortIndex`` does. MuPDF's characters stand in for the reader's
    pdf.js ones, so the offset can miss by a few characters on a page whose
    two readers order or split glyphs differently; the order it gives the
    sidebar is the same."""
    index = position.get("pageIndex")
    index = index if isinstance(index, int) and not isinstance(index, bool) else page.number - 1
    rect = _sort_rect(position)
    offset = top = 0
    if rect is not None:
        best = math.inf
        for i, (_c, box) in enumerate(page.chars):
            d = _rects_dist(box, rect)
            if d < best:
                best, offset = d, i
        top = max(0.0, (page.view[3] - page.view[1]) - rect[3])
    return "|".join((str(index)[:5].rjust(5, "0"), str(offset)[:6].rjust(6, "0"),
                     str(math.floor(top))[:5].rjust(5, "0")))


# --- comments -------------------------------------------------------------------------

def _format_html(text: str) -> str:
    """One line of a Zotero comment as HTML, its marks read as the reader
    reads them (editor.js ``walkFormat``/``getFormatter``): the earliest
    opening tag, matched without regard to case, that has a closing tag
    after it, its inside read again and the rest after it too; any other
    ``<`` is text."""
    low = text.lower()
    for offset, tag in sorted((low.find(f"<{tag}>"), tag) for tag in _FORMATS):
        if offset < 0:
            continue
        end = low.find(f"</{tag}>", offset)
        if end >= 0:
            inner, rest = text[offset + len(tag) + 2:end], text[end + len(tag) + 3:]
            return (html.escape(text[:offset], quote=False) + f"<{tag}>" + _format_html(inner)
                    + f"</{tag}>" + _format_html(rest))
    return html.escape(text, quote=False)


def comment_in(text: str) -> str:
    """A Zotero comment (plain text with the reader's ``<b> <i> <sub>
    <sup>`` marks, any case; a format never spans a line, as the reader's
    editor never shows one that does) → Markdown: bold and italic as
    Markdown's, sub- and superscript as their text, everything else shown as
    written (escaped), through the Zotero note import (``zotero_notes``), so
    a mark never opens or closes on a space and whitespace collapses as in a
    note. ``comment_out`` of the result gives the comment back with those
    normalisations, and ``comment_in`` of that the same Markdown."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tree = note_tree("<p>" + "<br>".join(_format_html(line) for line in lines) + "</p>")
    return tree["content"] if tree else ""


def _comment_text(md: str) -> str:
    """Markdown → the reader's comment text: bold and italic as ``<b>`` and
    ``<i>`` (closed and opened again across a line break, which the reader
    never formats across), an escaped character as itself, a link whose
    label is its address as the address; any other construct as written."""
    out, pos = [], 0
    for m in INLINE_RE.finditer(md):
        out.append(md[pos:m.start()])
        pos = m.end()
        which = m.lastgroup
        body = m.group(which)
        if which == "esc":
            out.append(body[1])
        elif which in ("bold", "italic"):
            tag, cut = ("b", 2) if which == "bold" else ("i", 1)
            inner = _comment_text(body[cut:-cut])
            out.append("\n".join(f"<{tag}>{part}</{tag}>" if part.strip() else part for part in inner.split("\n")))
        elif which == "link":
            label, href = LINK_PARTS.match(body).groups()
            out.append(href if label == href else body)
        else:
            out.append(body)
    out.append(md[pos:])
    return "".join(out)


def _nested_lines(nested) -> list[str]:
    """The nested notes (``(depth, markdown, …)``, depth 1 under the block;
    a depth-0 entry, the block's own note, is skipped) as popup_text writes
    them: an indented bullet list."""
    out = []
    for entry in nested or ():
        depth, md = entry[0], (entry[1] or "").strip(_WS)
        if depth >= 1 and md:
            out.append("  " * (depth - 1) + "- " + _comment_text(md))
    return out


def comment_out(markdown: str, nested=()) -> str:
    """``annotationComment`` of a block whose own note is ``markdown`` and
    whose nested notes are ``nested`` (``pdf_export.highlight_note_blocks``
    of the block, as it is): the RDF export's popup text
    (``pdf_export.popup_text``), its marks the reader's (``_comment_text``)."""
    own = (markdown or "").strip(_WS)
    return "\n".join(([_comment_text(own)] if own else []) + _nested_lines(nested))


def comment_parts(comment: str, nested=()) -> tuple[str, str] | None:
    """A Zotero comment split as ``comment_out`` joined it: ``(own,
    nested_suffix)``, the comment text of the block's own note and of the
    notes ``nested`` under it (Gamma's, as ``comment_out`` takes them);
    ``(comment, "")`` without nested notes. None when the comment no longer
    ends with those notes' text: Zotero changed that part, which is no
    change Gamma can apply to the block's own note."""
    comment = (comment or "").replace("\r\n", "\n").replace("\r", "\n")
    suffix = "\n".join(_nested_lines(nested))
    if not suffix:
        return comment, ""
    if comment in (suffix, suffix.strip()):
        return "", suffix
    if comment.endswith("\n" + suffix):
        return comment[:-len(suffix) - 1].rstrip("\n"), suffix
    return None


# --- ink -------------------------------------------------------------------------------

def ink_look(stroke) -> str:
    """A stroke's look as ``ink.ink_buckets`` groups strokes: tool, colour,
    size and opacity, as a short stable string ("pen:#1f1f1f:1.6:1")."""
    return f"{stroke.tool}:{stroke.color.lower()}:{round(stroke.size, 2):g}:{round(stroke.opacity, 3):g}"


def _slot_order(slot: str) -> tuple:
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in str(slot).split("."))


def _free_slot(used: set, base: str | None = None) -> str:
    """A slot name no row uses: the next whole number for a new ink item,
    ``<base>.<n>`` (n from 1) for a piece split off the item ``base``."""
    if base is not None:
        n = 1
        while f"{base}.{n}" in used:
            n += 1
        return f"{base}.{n}"
    tops = [int(s.split(".")[0]) for s in used if s.split(".")[0].isdigit()]
    return str(max(tops) + 1 if tops else 0)


def _ink_groups(strokes: list, rows) -> list[tuple[str, list, str]]:
    """The block's strokes as items: ``[(slot, strokes, look)]`` in slot
    order. A row (``(slot, stroke_ids, look[, locked])``) keeps the strokes
    it owns by id, all of them while they share a look (a recolour or a new
    width is a change of that item); of mixed looks, the ones of its own
    look (else of its first stroke's), the others left over. A stroke no row
    keeps joins the first row of its look that is not ``locked`` (another
    member's in a group library), else a new slot of its look, in
    first-seen order (``ink_buckets``' rule)."""
    index = {s.id: i for i, s in enumerate(strokes)}
    rows = sorted(((str(r[0]), tuple(r[1] or ()), str(r[2]) if len(r) > 2 and r[2] else "",
                    bool(r[3]) if len(r) > 3 else False) for r in rows or ()), key=lambda r: _slot_order(r[0]))
    owner = {}
    for slot, ids, _look, _locked in rows:
        for sid in ids:
            if sid in index and sid not in owner:
                owner[sid] = slot
    groups: dict = {}
    looks: dict = {}
    loose = [s for s in strokes if s.id not in owner]
    for slot, _ids, look, locked in rows:
        mine = [s for s in strokes if owner.get(s.id) == slot]
        if not mine:
            continue
        keep = look if any(ink_look(s) == look for s in mine) else ink_look(mine[0])
        groups[slot] = [s for s in mine if ink_look(s) == keep]
        loose += [s for s in mine if ink_look(s) != keep]
        looks[slot] = (keep, locked)
    used = {slot for slot, *_ in rows}
    for stroke in sorted(loose, key=lambda s: index[s.id]):
        look = ink_look(stroke)
        slot = next((k for k, (lk, locked) in looks.items() if lk == look and not locked), None)
        if slot is None:
            slot = _free_slot(used)
            used.add(slot)
            looks[slot] = (look, False)
            groups[slot] = []
        groups[slot].append(stroke)
    return [(slot, sorted(group, key=lambda s: index[s.id]), looks[slot][0])
            for slot, group in sorted(groups.items(), key=lambda kv: _slot_order(kv[0]))]


def _ink_scale(ink_file, page: Page) -> float:
    """User-space units per point of the ink file's frame, for widths
    (``pdf_export._ink_scale``'s rule: 1 unless the page was replaced by one
    of another size)."""
    sw = float(ink_file.space.width)
    cw, ch = page.view[2] - page.view[0], page.view[3] - page.view[1]
    return ((cw if page.rotation in (0, 180) else ch) / sw) if sw else 1.0


def _ink_position(ink_file, strokes: list, page: Page) -> dict:
    """The ``ink`` position of ``strokes``: each stroke one path in user
    space (a dot a zero-length segment), the width their mean drawn width,
    as ``pdf_export._ink_annotations`` writes an /Ink."""
    sw, sh = float(ink_file.space.width), float(ink_file.space.height)
    paths, widths = [], []
    for stroke in strokes:
        poly = inkmod.stroke_polyline(stroke)
        flat = []
        for x, y, w in poly:
            px, py = viewer_point_to_pdf(x / sw, y / sh, page.rotation, page.view)
            flat += [_num(px, _OUT_DIGITS), _num(py, _OUT_DIGITS)]
            widths.append(w)
        if len(poly) == 1:
            flat += flat[:2]
        paths.append(flat)
    width = sum(widths) / len(widths) * _ink_scale(ink_file, page) if widths else 1.0
    return {"pageIndex": page.number - 1, "width": _num(width, _OUT_DIGITS), "paths": paths}


def replace_strokes(ink_file, stroke_ids, new_strokes) -> inkmod.InkFile:
    """``ink_file`` with the strokes ``stroke_ids`` names replaced by
    ``new_strokes`` (``Stroke``s or their dicts), put where the first of
    them was (at the end when none is there), the other strokes as they are
    → a new ``InkFile``; ``ink.InkError`` for one the format refuses (a
    stroke id twice, too many samples)."""
    ids = set(stroke_ids)
    new = [s if isinstance(s, dict) else inkmod.to_dict(s) for s in new_strokes]
    out, placed = [], False
    for stroke in ink_file.strokes:
        if stroke.id in ids:
            if not placed:
                out += new
                placed = True
            continue
        out.append(inkmod.to_dict(stroke))
    if not placed:
        out += new
    return inkmod.parse_ink({**inkmod.to_dict(ink_file), "strokes": out})


def _strokes_from(position: dict, page: Page, color: str, key: str) -> list[dict] | None:
    """An ``ink`` position's paths as strokes in the page's display frame at
    scale 1, ids ``z<key>-<n>``, one look (a pen of the item's colour and
    width, opaque); None for a point no ink file can hold."""
    width = position.get("width")
    size = min(100.0, max(0.2, float(width))) if _positive(width) else 1.0
    w, h = page.size
    out = []
    for path in position.get("paths") or ():
        try:
            nums = [float(v) for v in path]
        except (TypeError, ValueError):
            continue
        samples = []
        for i in range(0, len(nums) - 1, 2):
            u, v = pdf_point_to_viewer(nums[i], nums[i + 1], page.rotation, page.view)
            samples.append({"x": u * w, "y": v * h})
        if not samples:
            continue
        if not all(math.isfinite(s[c]) and abs(s[c]) * inkmod.COORD_UNIT <= inkmod.MAX_XY
                   for s in samples for c in ("x", "y")):
            return None
        out.append({"id": f"z{key}-{len(out)}", "tool": "pen", "pen": False, "color": color, "size": size,
                    "opacity": 1.0, "ch": "xy", "pts": inkmod.encode_points(samples)})
    return out


def _ink_key(n: int, position: dict, strokes: int) -> str:
    """The embedded importer's key for the /Ink Zotero embeds for an ink
    item: its page, its first point in user space, its stroke count."""
    paths = position.get("paths") or ()
    first = paths[0][:2] if paths and len(paths[0]) >= 2 else (0, 0)
    return f"{n}:/Ink:{round(float(first[0]))}:{round(float(first[1]))}:{strokes}"


# --- splitting ---------------------------------------------------------------------------

def _pack(position: dict) -> list[tuple[dict, list[int]]]:
    """``position`` in pieces whose JSON stays within ``POSITION_MAX``, each
    with the indexes of the rects or paths it holds: rect by rect, as
    Zotero's ``splitAnnotationJSON`` does; paths whole where they fit (so a
    stroke belongs to one piece), a path too long for a piece of its own cut
    by points as Zotero cuts it (a ValueError for a single rect or point
    that fits none)."""
    if len(_dumps(position)) <= POSITION_MAX:
        n = len(position.get("rects") or position.get("paths") or ())
        return [(position, list(range(n)))]
    key = "rects" if position.get("rects") else "paths"
    empty = {**position, key: []}
    base = len(_dumps(empty))
    pieces, items, idx, size = [], [], [], base

    def flush():
        nonlocal items, idx, size
        if items:
            pieces.append(({**position, key: items}, idx))
        items, idx, size = [], [], base

    for i, item in enumerate(position.get(key) or ()):
        cost = len(_dumps(item)) + (1 if items else 0)
        if size + cost <= POSITION_MAX:
            items.append(item)
            idx.append(i)
            size += cost
            continue
        flush()
        cost = len(_dumps(item))
        if base + cost <= POSITION_MAX:
            items, idx, size = [item], [i], base + cost
            continue
        if key == "rects":
            raise ValueError("a single rect does not fit an annotation's position")
        part, length = [], 2       # a path too long for a piece: its points, piece by piece
        for j in range(0, len(item) - 1, 2):
            point = item[j:j + 2]
            step = len(_dumps(point)) - 2 + (1 if part else 0)
            if base + length + step > POSITION_MAX:
                if not part:
                    raise ValueError("a single point does not fit an annotation's position")
                pieces.append(({**position, key: [part]}, [i]))
                part, length, step = [], 2, len(_dumps(point)) - 2
            part += point
            length += step
        items, idx, size = [part], [i], base + length
    flush()
    return pieces


# --- Gamma → Zotero -----------------------------------------------------------------------

def _highlight_fields(props: dict, content: str, page: Page, zotero_type, nested) -> dict | None:
    pos = props.get("pdf_position")
    if not isinstance(pos, dict) or pos.get("pageNumber") != page.number:
        return None
    size = (pos.get("width"), pos.get("height"))
    if not all(map(_positive, size)):
        return None
    boxes = [viewer_rect_to_pdf(r, size, page.rotation, page.view) for r in pos.get("rects") or ()
             if highlights.placed(r)]
    if not boxes:
        return None
    if pos.get("area"):
        kind, boxes = "image", [_union(boxes)]
    else:
        kind = zotero_type if zotero_type in TEXT_KINDS else "highlight"
    fields = {"annotationType": kind}
    if kind in TEXT_KINDS:
        fields["annotationText"] = str(props.get("quote") or "")[:TEXT_MAX]
    fields["annotationComment"] = comment_out(content, nested)
    fields["annotationColor"] = color_out(props.get("color"))
    fields["annotationPosition"] = {"pageIndex": page.number - 1, "rects": [_rect_out(b) for b in boxes]}
    return fields


def _text_box_fields(props: dict, content: str, page: Page, zotero_type, nested) -> dict | None:
    box = text_box.normalize_text_box(props.get("text_box"))
    if box is None or text_box.box_page(props) != page.number:
        return None
    w, h = page.size
    if zotero_type == "note":
        rect = viewer_rect_to_pdf({"x1": box["x"], "y1": box["y"], "x2": box["x"] + NOTE_SIDE,
                                   "y2": box["y"] + NOTE_SIDE}, (w, h), page.rotation, page.view)
        return {"annotationType": "note", "annotationComment": comment_out(content, nested),
                "annotationColor": DEFAULT_COLOR,
                "annotationPosition": {"pageIndex": page.number - 1, "rects": [_rect_out(rect)]}}
    # A text annotation is its box as shown, unturned, about the box's centre in
    # user space, turned by the page's rotation: upright on screen, as
    # pdf_export writes /Zotero:Rect and /Zotero:Rotation.
    (ax, ay), (bx, by) = (viewer_point_to_pdf(x / w, y / h, page.rotation, page.view)
                          for x, y in ((box["x"], box["y"]), (box["x"] + box["w"], box["y"] + box["h"])))
    cx, cy = (ax + bx) / 2, (ay + by) / 2
    rect = (cx - box["w"] / 2, cy - box["h"] / 2, cx + box["w"] / 2, cy + box["h"] / 2)
    return {"annotationType": "text", "annotationComment": text_box.plain_text(content),
            "annotationColor": color_out(box["color"]),
            "annotationPosition": {"pageIndex": page.number - 1, "fontSize": _num(box["size"], _OUT_DIGITS),
                                   "rotation": page.rotation, "rects": [_rect_out(rect)]}}


def _items(fields: dict, page: Page, slot: str, author: str, used: set,
           strokes: list | None = None, look: str = "") -> list[Item]:
    """``fields`` (its position a dict) finished as items: the page label,
    the author, the sort index, the position as Zotero's JSON string; split
    into pieces past ``POSITION_MAX``, the first keeping ``slot``, the
    comment on the first alone."""
    label = item_label(page.label, page.number)
    out = []
    for n, (piece, idx) in enumerate(_pack(fields["annotationPosition"])):
        f = {"itemType": "annotation", **fields, "annotationPageLabel": label,
             "annotationSortIndex": sort_index(page, piece), "annotationPosition": _dumps(piece)}
        if n:
            f["annotationComment"] = ""
        if author:
            f["annotationAuthorName"] = author
        name = slot
        if n:
            name = _free_slot(used, slot)
            used.add(name)
        ids = tuple(strokes[i].id for i in sorted(set(idx))) if strokes is not None else ()
        out.append(Item(name, f, ids, look))
    return out


def to_items(block: dict, page: Page, *, ink_file=None, rows=(), zotero_type: str | None = None,
             nested=(), author: str = "") -> list[Item]:
    """A Gamma annotation block (``content``, ``properties``) on ``page`` as
    Zotero items: a highlight as a ``highlight`` (``zotero_type``
    "underline" keeps an underline one), an area highlight as an ``image``,
    a text box as a ``text`` (``zotero_type`` "note": a ``note``), an ink
    group (``ink_file``, its parsed file) as one ``ink`` item per look, its
    strokes assigned to the block's ink ``rows`` (``(slot, stroke_ids,
    look[, locked])``, ``_ink_groups``). The comment is ``comment_out`` of
    the block's own note and its ``nested`` notes (a ``text`` annotation's:
    the box's plain text), on the first item of a group. [] for a block
    that is none of these or is not on ``page``."""
    props = block.get("properties") or {}
    content = block.get("content") or ""
    used: set = {str(r[0]) for r in rows or ()}
    if "ink_url" in props:
        if (ink_file is None or ink_file.space.kind != "pdf-page" or ink_file.space.page != page.number
                or not ink_file.strokes):
            return []
        comment = comment_out(content, nested)
        out = []
        for n, (slot, strokes, look) in enumerate(_ink_groups(ink_file.strokes, rows)):
            used.add(slot)
            fields = {"annotationType": "ink", "annotationComment": comment if n == 0 else "",
                      "annotationColor": color_out(strokes[0].color),
                      "annotationPosition": _ink_position(ink_file, strokes, page)}
            out += _items(fields, page, slot, author, used, strokes, look)
        return out
    if text_box.is_text_box(props):
        fields = _text_box_fields(props, content, page, zotero_type, nested)
    else:
        fields = _highlight_fields(props, content, page, zotero_type, nested)
    return _items(fields, page, "", author, used) if fields else []


def patch_fields(fields: dict, names, base: dict | None = None) -> dict:
    """The item fields a PATCH sends when Gamma changed the projection
    fields ``names`` of the item ``fields`` (``to_items``'): those fields
    (never the type, which Zotero refuses to change, and the quote only
    where the kind carries one); the sort index with a position; the page
    label only when the page changed from ``base``'s (Gamma's base
    projection), so a label corrected in Zotero stays."""
    out = {}
    for name in names:
        key = FIELDS.get(name)
        if not key or name == "type" or (name == "text" and fields.get("annotationType") not in TEXT_KINDS):
            continue
        if key in fields:
            out[key] = fields[key]
    if "position" in names:
        out["annotationSortIndex"] = fields.get("annotationSortIndex")
        page = projection(fields)["position"].get("pageIndex")
        if base is None or (base.get("position") or {}).get("pageIndex") != page:
            out["annotationPageLabel"] = fields.get("annotationPageLabel")
    return out


# --- Zotero → Gamma -------------------------------------------------------------------------

def _key_props(data: dict, imported: str) -> dict:
    return {"imported_annot": imported, "annot_stripped": True, "zotero_annotation": str(data.get("key") or "")}


def _text_box_from(kind: str, rect: tuple, position: dict, data: dict, md: str, page: Page,
                   keep: dict | None = None) -> dict:
    """A note's or a text annotation's box on ``page``: a note's at its
    icon's top-left, note yellow, as wide as its text (``measure``), a text
    annotation's upright on its rect's centre at its font size. ``keep`` is
    the block's box now: what Zotero has no place for (a fill, the width
    mode, a note's size) stays."""
    w, h = page.size
    if kind == "note":
        corners = [pdf_point_to_viewer(px, py, page.rotation, page.view)
                   for px, py in ((rect[0], rect[1]), (rect[2], rect[3]))]
        x, y = min(u for u, _ in corners) * w, min(v for _, v in corners) * h
        if keep:
            box = {**keep, "x": x, "y": y}
        else:
            box = text_box.normalize_text_box({"x": x, "y": y, "w": max(text_box.MIN_WIDTH, w - x), "bg": NOTE_FILL})
            box["w"], box["h"] = text_box.measure(md, box, w)
    else:
        bw, bh = rect[2] - rect[0], rect[3] - rect[1]
        u, v = pdf_point_to_viewer((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2, page.rotation, page.view)
        size = position.get("fontSize")
        box = {**(keep or {"auto": False, "bg": None, "color": data.get("annotationColor")}),
               "x": u * w - bw / 2, "y": v * h - bh / 2, "w": bw, "h": bh}
        if _positive(size):
            box["size"] = size
    return text_box.keep_on_surface(text_box.normalize_text_box(box), w, h)


def _turned_box(rect, degrees: float) -> tuple:
    """The bounding box of ``rect`` turned by ``degrees`` about its centre:
    the /Rect Zotero embeds for a turned text annotation."""
    x0, y0, x1, y1 = rect
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    turn = math.radians(degrees)
    cos, sin = abs(math.cos(turn)), abs(math.sin(turn))
    dx, dy = hw * cos + hh * sin, hw * sin + hh * cos
    return cx - dx, cy - dy, cx + dx, cy + dy


def from_item(data: dict, page: Page) -> dict | None:
    """A Zotero annotation item's ``data`` on ``page`` (the page its
    ``pageIndex`` names) as a new block: ``{"zotero_type", "kind"
    (highlight | ink | text_box), "content", "props", "ink" (an InkFile for
    ink, to be stored before the block: ``props.ink_url`` is "" until
    then), "stroke_ids", "slot"}``. Props carry ``zotero_annotation``,
    ``imported_annot`` (the key the embedded importer gives the annotation
    Zotero would embed for it, so an RDF import adds it nothing twice) and
    ``annot_stripped: True``. A highlight's quote is ``annotationText`` as
    sent; its colour takes ``HIGHLIGHT_ALPHA``; an ink item's strokes are
    ids ``z<key>-<n>``; a note is a note-yellow text box, a text annotation
    a box upright on the same centre. None for an item that makes no block:
    a kind Gamma has none of, no place, another page, no strokes, a note or
    text without text."""
    kind = data.get("annotationType")
    position = _parse_position(data.get("annotationPosition"))
    if kind not in TYPES or position is None or page is None or position.get("pageIndex") != page.number - 1:
        return None
    n = page.number
    comment = str(data.get("annotationComment") or "")
    if kind == "ink":
        hue = color_out(data.get("annotationColor"))
        strokes = _strokes_from(position, page, hue, str(data.get("key") or ""))
        if not strokes:
            return None
        w, h = page.size
        try:
            ink_file = inkmod.parse_ink({"format": inkmod.FORMAT, "version": inkmod.VERSION,
                                         "space": {"kind": "pdf-page", "page": n, "width": round(w, 2),
                                                   "height": round(h, 2)}, "strokes": strokes})
        except inkmod.InkError:
            return None
        props = {"ink_url": "", "pdf_position": inkmod.pdf_position(ink_file), "ink_strokes": len(strokes),
                 "color": hue, **_key_props(data, _ink_key(n, position, len(strokes)))}
        return {"zotero_type": kind, "kind": "ink", "content": comment_in(comment), "props": props,
                "ink": ink_file, "stroke_ids": tuple(s.id for s in ink_file.strokes), "slot": "0"}
    rects = _user_rects(position)
    if not rects:
        return None
    if kind in ("note", "text"):
        md = comment_in(comment) if kind == "note" else text_box.markdown_of(comment)
        if not md.strip():
            return None
        box = _text_box_from(kind, rects[0], position, data, md, page)
        turned = rects[0] if kind == "note" else _turned_box(rects[0], _number(position.get("rotation")))
        props = {"text_box": box, "pdf_page": n, **_key_props(data, annotation_key(n, SUBTYPES[kind], turned))}
        return {"zotero_type": kind, "kind": "text_box", "content": md, "props": props, "ink": None,
                "stroke_ids": (), "slot": ""}
    area = kind == "image"
    boxes = [_union(rects)] if area else rects
    w, h = page.size
    quote = "" if area else (str(data.get("annotationText") or "")[:TEXT_MAX]
                             or quote_under(page.glyphs, rects, limit=TEXT_MAX))
    props = {"color": color_in(data.get("annotationColor")), "quote": quote,
             "pdf_position": highlights.position(n, round(w, 2), round(h, 2),
                                                 [to_viewer_rect(b, page.frame) for b in boxes], area=area),
             **_key_props(data, annotation_key(n, SUBTYPES[kind], rects[0]))}
    return {"zotero_type": kind, "kind": "highlight", "content": comment_in(comment), "props": props,
            "ink": None, "stroke_ids": (), "slot": ""}


# --- projections ------------------------------------------------------------------------

def _canon(value):
    """Numbers at the canonical rounding, lists and dicts all the way down."""
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return _num(value, _DIGITS)


def _canonical_position(raw) -> dict:
    """A position reduced to what places the annotation: its page, rects
    (and ``nextPageRects``), or paths and width, a text's font size and
    rotation, at the canonical rounding; keys another client added are left
    out."""
    pos = _parse_position(raw) or {}
    out = {}
    if "pageIndex" in pos:
        out["pageIndex"] = pos["pageIndex"]
    for key in ("rects", "nextPageRects", "paths"):
        if isinstance(pos.get(key), list):
            out[key] = _canon(pos[key])
    if "paths" in out and "width" in pos:
        out["width"] = _canon(pos["width"])
    if "rects" in out:
        for key in ("fontSize", "rotation"):
            if key in pos:
                out[key] = _canon(pos[key])
    return out


def projection(fields: dict) -> dict:
    """An item's fields (Zotero's ``data`` or ``to_items``') in the form the
    sync compares: ``{type, color, comment, text, position}``, the position
    canonical (``_canonical_position``), the quote "" for a kind that has
    none, the colour "" for a ``note``: a Gamma text box has no place for a
    sticky note's colour (a new one goes as ``DEFAULT_COLOR``), so the sync
    never compares or sends it and Zotero's stays. The sort index and the
    page label are derived from the position, so they are no part of it."""
    kind = str(fields.get("annotationType") or "")
    return {"type": kind, "color": "" if kind == "note" else str(fields.get("annotationColor") or "").lower(),
            "comment": str(fields.get("annotationComment") or ""),
            "text": str(fields.get("annotationText") or "") if kind in TEXT_KINDS else "",
            "position": _canonical_position(fields.get("annotationPosition"))}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def positions_close(a, b, tol: float = POSITION_TOL) -> bool:
    """Whether two positions (or any of their parts) are one place: equal,
    their numbers within ``tol`` points of each other at any depth (a float
    epsilon over, so 0.57 and 1.07 are half a point apart)."""
    seqs = (list, tuple)
    if isinstance(a, seqs) and isinstance(b, seqs):
        return len(a) == len(b) and all(positions_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(positions_close(a[k], b[k], tol) for k in a)
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= tol + 1e-9
    return a == b


def same(a: dict, b: dict, *, tol: float = POSITION_TOL) -> bool:
    """Whether two projections are one annotation: every field equal, the
    positions one place (``positions_close``; a first link or a force
    compares Zotero's item with Gamma's block so)."""
    return (all(a.get(k) == b.get(k) for k in ("type", "color", "comment", "text"))
            and positions_close(a.get("position") or {}, b.get("position") or {}, tol))


def block_sig(block: dict, nested=()) -> str:
    """A cheap signature of what ``to_items`` reads of a block: its own
    note, colour, quote, place (``pdf_position``, ``ink_url``, ``text_box``,
    ``pdf_page``) and the text of its ``nested`` notes. When it is unchanged,
    so is the block's projection."""
    props = block.get("properties") or {}
    return _hash([block.get("content") or "",
                  [props.get(k) for k in ("color", "quote", "pdf_position", "ink_url", "text_box", "pdf_page")],
                  [[entry[0], entry[1]] for entry in nested or ()]])


# --- which blocks ------------------------------------------------------------------------

def _kind(props: dict) -> str:
    """The block's kind as the generated ``kind`` column reads it (db.py
    BLOCK_HOT_COLUMNS), for a block that is no page."""
    if "ink_url" in props:
        return "ink"
    for kind in ("text_box", "picture", "sheet", "recording"):
        if isinstance(props.get(kind), dict):
            return kind
    if props.get("link_url") not in (None, "") or props.get("link_page_id") not in (None, ""):
        return "link"
    return "highlight" if isinstance(props.get("pdf_position"), dict) else "note"


def page_number(props: dict, ink_file=None, *, on_sheet: bool = False) -> int | None:
    """The PDF page an annotation block is on: an ink group's file's page
    (its ``pdf_position``'s without the file), a text box's ``box_page``, a
    highlight's ``pdf_position.pageNumber``; None for one on no PDF page."""
    props = props or {}
    kind = _kind(props)
    if kind == "ink":
        if on_sheet:
            return None
        if ink_file is not None:
            return ink_file.space.page if ink_file.space.kind == "pdf-page" else None
        return highlights.page_of(props)
    if kind == "text_box":
        return text_box.box_page(props, on_sheet)
    return highlights.page_of(props) if kind == "highlight" else None


def is_annotation_block(block: dict, *, on_sheet: bool = False) -> bool:
    """Whether a block is of a kind that is a Zotero annotation: an ink
    group on a PDF page, a text box on a PDF page (not on a sheet: the
    nearest sheet wins), a highlight (not a link region), whatever its
    state. A block with a row that still is one is held while it is not
    ``eligible``, never gone."""
    props = block.get("properties") or {}
    kind = _kind(props)
    if kind == "ink":
        return not on_sheet and isinstance(props.get("pdf_position"), dict)
    if kind == "text_box":
        return text_box.box_page(props, on_sheet) is not None
    return kind == "highlight"


def hold_reason(block: dict) -> str:
    """Why an annotation block (``is_annotation_block``) may not be created or
    written in Zotero now, "" when it may (see
    docs/dev/zotero_sync.md#annotations): its original is still drawn in the
    file (``still_embedded``: Zotero would show both), an ink group's file is
    not stored yet, a highlight is ``lost``, ``approx`` (dashed, pending
    review), measured on another file (``doc_id``) or has no frame and placed
    rect. A block with a row that has a reason is held, never gone; the rule
    ``eligible``, ``held_blocks`` and the engine's merges all read."""
    props = block.get("properties") or {}
    if still_embedded(props):
        return WAIT_EMBEDDED
    kind = _kind(props)
    if kind == "ink":
        return "" if props.get("ink_url") else WAIT_PLACING
    if kind != "highlight":  # a text box, or no annotation
        return ""
    pos = props["pdf_position"]
    if pos.get("lost"):
        return WAIT_LOST
    if pos.get("approx"):
        return WAIT_DASHED
    if pos.get("doc_id"):
        return WAIT_PLACING
    if not (_positive(pos.get("width")) and _positive(pos.get("height"))
            and any(highlights.placed(r) for r in pos.get("rects") or ())):
        return WAIT_NO_PLACE
    return ""


def eligible(block: dict, page_md5: str, attachment_md5: str, *, on_sheet: bool = False) -> bool:
    """Whether an annotation block may be created or written in Zotero now:
    the page's PDF is the attachment's file (its MD5 is ``attachment_md5``),
    the block is an annotation (``is_annotation_block``) and nothing holds
    it (``hold_reason``). The engine checks that its page is in the file."""
    return (bool(page_md5 and attachment_md5 and page_md5.lower() == attachment_md5.lower())
            and is_annotation_block(block, on_sheet=on_sheet) and not hold_reason(block))


# --- applying Zotero's changes -------------------------------------------------------------

def _current(block: dict, page: Page, ink_file, stroke_ids, zotero_type, nested, first: bool) -> dict:
    """Gamma's projection of the item a pull is applied to (the strokes
    ``stroke_ids`` of an ink group), unsplit, its comment "" unless it is
    the block's ``first`` item; {} when there is none."""
    props = block.get("properties") or {}
    content = block.get("content") or ""
    if "ink_url" in props:
        if ink_file is None or ink_file.space.page != page.number:
            return {}
        ids = set(stroke_ids)
        strokes = [s for s in ink_file.strokes if s.id in ids]
        if not strokes:
            return {}
        fields = {"annotationType": "ink", "annotationColor": color_out(strokes[0].color),
                  "annotationComment": comment_out(content, nested),
                  "annotationPosition": _ink_position(ink_file, strokes, page)}
    elif text_box.is_text_box(props):
        fields = _text_box_fields(props, content, page, zotero_type, nested)
    else:
        fields = _highlight_fields(props, content, page, zotero_type, nested)
    if fields and not first:
        fields["annotationComment"] = ""
    return projection(fields) if fields else {}


def apply_fields(block: dict, page: Page, changed: dict, *, ink_file=None, stroke_ids=(), nested=(),
                 zotero_type: str | None = None, key: str = "", first: bool = True) -> dict:
    """What a pull of the projection fields ``changed`` (``{field: Zotero's
    value}``, Zotero's projection) makes of ``block`` on ``page`` (the page
    the item is on now), touching only those fields and only where they
    differ from what the block reads as now: ``{"props": patch,
    "content"?, "ink"?, "stroke_ids"?, "conflicts"?}``. ``props`` holds only
    the keys that change. The colour keeps the block's alpha; a text box's
    colour is its text's (a note has none); the comment becomes the block's
    own note when it still ends with the ``nested`` notes' text, else
    "comment" is in ``conflicts`` (``comment_parts``); a text annotation's is
    taken as plain text; the quote is set as sent; the position becomes the
    block's geometry, which drops a highlight's relocation marks, as a move
    by hand does. For ink (``ink_file``, the item's ``stroke_ids``; ``key``
    the item's key) a new colour or width restyles exactly those strokes,
    new paths replace them (``replace_strokes``, ids ``z<key>-<n>``, in
    ``stroke_ids``), and ``ink`` is the new file to store; an ink item now on
    another page is a "position" conflict. The type never changes. An item
    that is not its block's ``first`` (an ink group's later looks, a split
    piece) carries no comment: a comment Zotero gives one is a "comment"
    conflict, never the block's note."""
    props = block.get("properties") or {}
    zotero_type = zotero_type or changed.get("type")
    current = _current(block, page, ink_file, stroke_ids, zotero_type, nested, first)
    todo = {}
    for f, v in (changed or {}).items():
        if f in FIELDS and f != "type" and (_canonical_position(v) if f == "position" else v) != current.get(f):
            todo[f] = v
    out: dict = {"props": {}}
    conflicts = []
    if not todo:
        return out
    kind = _kind(props)
    if "comment" in todo:
        content = None
        if not first:
            conflicts.append("comment")
        elif kind == "text_box" and zotero_type != "note":
            content = text_box.markdown_of(str(todo["comment"] or ""))
        else:
            parts = comment_parts(todo["comment"], nested)
            content = None if parts is None else comment_in(parts[0])
            if parts is None:
                conflicts.append("comment")
        if content is not None and content != (block.get("content") or ""):
            out["content"] = content
    position = _parse_position(todo.get("position")) if "position" in todo else None
    if position is not None and position.get("pageIndex") != page.number - 1:
        conflicts.append("position")
        position = None
    if kind == "ink":
        _apply_ink(out, conflicts, props, page, todo, position, ink_file, stroke_ids, key, current)
    elif kind == "text_box":
        box = text_box.normalize_text_box(props.get("text_box"))
        new = dict(box) if box else None
        if new is not None and "color" in todo and zotero_type != "note":
            new["color"] = color_out(todo["color"])
        rects = _user_rects(position) if position is not None else []
        if new is not None and rects:
            new = _text_box_from(zotero_type if zotero_type == "note" else "text", rects[0], position, {}, "",
                                 page, keep=new)
            if props.get("pdf_page") != page.number:
                out["props"]["pdf_page"] = page.number
        if new is not None and new != box:
            out["props"]["text_box"] = new
    else:
        if "color" in todo:
            color = color_in(todo["color"], _alpha(props.get("color")))
            if colors.parse_color(color) != colors.parse_color(props.get("color")):
                out["props"]["color"] = color
        if "text" in todo and (zotero_type or "highlight") in TEXT_KINDS and todo["text"] != props.get("quote"):
            out["props"]["quote"] = todo["text"]
        rects = _user_rects(position) if position is not None else []
        if rects:
            pos = props.get("pdf_position") if isinstance(props.get("pdf_position"), dict) else {}
            area = bool(pos.get("area"))
            boxes = [_union(rects)] if area else rects
            w, h = page.size
            out["props"]["pdf_position"] = highlights.position(
                page.number, round(w, 2), round(h, 2), [to_viewer_rect(b, page.frame) for b in boxes], area=area)
    if conflicts:
        out["conflicts"] = conflicts
    return out


def _apply_ink(out: dict, conflicts: list, props: dict, page: Page, todo: dict, position, ink_file, stroke_ids,
               key: str, current: dict) -> None:
    """``apply_fields``' ink part: restyle or replace the item's strokes."""
    if ink_file is None:
        return
    if position is not None and ink_file.space.page != page.number:
        conflicts.append("position")        # another page: a block of its own, the engine's to make
        return
    ids = set(stroke_ids)
    mine = [s for s in ink_file.strokes if s.id in ids]
    if not mine:
        return
    color = color_out(todo["color"]) if "color" in todo else None
    old = current.get("position") or {}
    paths_changed = position is not None and _canon(position.get("paths") or []) != old.get("paths")
    if paths_changed:
        hue = color or color_out(mine[0].color)
        strokes = _strokes_from(position, page, hue, key)
        if not strokes:
            conflicts.append("position")
            return
        taken = {s.id for s in ink_file.strokes} - ids
        for n, s in enumerate(strokes):       # never an id another stroke keeps
            while s["id"] in taken:
                s["id"] = f"{s['id']}x{n}"
        new = replace_strokes(ink_file, ids, strokes)
        out["stroke_ids"] = tuple(s["id"] for s in strokes)
    else:
        width = position.get("width") if position is not None else None
        scale = (float(width) / float(old["width"])) if (_positive(width) and _positive(old.get("width"))
                                                         and _canon(width) != old.get("width")) else None
        restyled = []
        for s in ink_file.strokes:
            d = inkmod.to_dict(s)
            if s.id in ids:
                if color:
                    d["color"] = color
                if scale:
                    d["size"] = min(100.0, max(0.2, s.size * scale))
            restyled.append(d)
        if not (color or scale):
            return
        new = inkmod.parse_ink({**inkmod.to_dict(ink_file), "strokes": restyled})
        out["stroke_ids"] = tuple(s.id for s in mine)
    out["ink"] = new
    out["props"].update({"pdf_position": inkmod.pdf_position(new), "ink_strokes": len(new.strokes)})
    if "color" in props and color:
        out["props"]["color"] = color
