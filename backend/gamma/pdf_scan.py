"""Scanned pages, with an invisible OCR text layer or without one, and the
visual rows of the first kind.

A scanned paper is a picture of each page with the OCR engine's words drawn
over it in an invisible render mode, so the page can be searched and
selected. Many such layers (the APS and HAL scans above all) draw each word
on its own, and MuPDF then cuts a printed line into several lines (about one
a word on the HAL scan, a few words each on the APS ones): the page text
comes out in those pieces, a bare number in a sentence looks like a line
number, and a hyphen at a word's end looks like a line-end hyphen.
pdf_text uses the two rules here to put the words of one visual row back on
one line, keeping their order and their boxes (docs/dev/documents.md,
"Scanned pages"; the study behind the rule is docs/research/ocr.md):

- ``is_scan_page(page)``: is this page such a scan?
- ``rows(bboxes)``: which of the page's MuPDF lines start a new visual row?

frontend/src/pdf/scanDetect.js applies the same scan rule to pdf.js's
operator list, and frontend/src/pdf/scanText.js the same row rule to its
words, so the viewer and the server agree on which pages are scans and how
their words make lines. Change a threshold in both.

A third rule picks the pages text recognition reads with no layer to
compare against (gamma/ocr_run.py): ``is_picture_page(page)``, a scan
nobody ran OCR on. Only the server applies it; the viewer notes a page
without text instead.

Callers hold pdf_text's lock, as for every PyMuPDF call. This module never
imports pymupdf: it calls methods of the page it is given, and pdf_text
imports pymupdf lazily.
"""

import math
import re
from statistics import median

# A page is a scan when at least this share of its shown non-space glyphs
# is invisible (render mode 3 or 7). Below 1 so a visible stamp a publisher
# set over the scan does not hide it; on the scans measured it is 1.0.
INVISIBLE_SHARE = 0.9
# ...and the union of the images it paints covers at least this share of
# the page. The lowest scan measured is 0.81 (an APS scan with margins);
# the highest born-digital page that also draws invisible text (LaTeXiT's
# size-0 source text under its equations) is 0.17.
IMAGE_COVERAGE = 0.7
# The coverage is measured on a grid of this many cells a side: a cell
# counts when its centre lies in an image's box.
GRID = 100
# A page with no invisible text is a picture page when its images cover
# IMAGE_COVERAGE of it and it shows at most this many non-space characters:
# room for a library's "Downloaded from ..." line, a publisher's stamp or a
# page number beside the picture, not for a page of text.
PICTURE_STAMP_CHARS = 300

# Two MuPDF lines in a row share a visual row when they overlap vertically
# by at least this share of the smaller one's height...
ROW_OVERLAP = 0.5
# ...and the second does not start more than this many row heights past
# the first one's end: a wider gap is a column gutter.
ROW_GAP = 3.0

# `3 Tr` / `7 Tr` as an operator: the operator ends at a delimiter or white
# space, and its operand, just before it, stands alone.
_TR_OP_RE = re.compile(rb"Tr(?![A-Za-z0-9_*'\"])")
_TR_OPERAND_RE = re.compile(rb"(?<![\w.+\-])[37](?:\.0*)?\s+$")


def is_scan_page(page, blocks=None) -> bool:
    """Whether ``page`` (a PyMuPDF page) is a scanned picture under an
    invisible OCR text layer: at least one glyph is shown, nearly all of
    them invisibly (``INVISIBLE_SHARE``), and images cover most of the page
    (``IMAGE_COVERAGE``). Annotations do not count.

    Three steps, cheapest first, each run only when the one before it says
    "maybe": a byte scan of the content streams for an invisible render
    mode (the end of the check for nearly every born-digital page), then
    the glyphs' visibility from the text dictionary, then the images'
    boxes. ``blocks``, when given, is a function returning the blocks of a
    text dictionary of the page the caller reads anyway ("dict" or
    "rawdict"), called only when the second step runs; otherwise the step
    makes its own. A page that cannot be read is no scan."""
    try:
        if not _invisible_mode_used(page):
            return False
        invisible, visible = _glyph_counts(page, blocks() if blocks else page.get_text("dict", flags=0)["blocks"])
        total = invisible + visible
        if not total or invisible / total < INVISIBLE_SHARE:
            return False
        return _image_coverage(page) >= IMAGE_COVERAGE
    except Exception:  # noqa: BLE001 — an unreadable page keeps its plain text
        return False


def is_picture_page(page, blocks=None) -> bool:
    """Whether ``page`` (a PyMuPDF page) is a scanned picture with no text
    layer: no invisible glyph, at most ``PICTURE_STAMP_CHARS`` shown
    non-space characters (annotations do not count, as for
    ``is_scan_page``), and images covering most of the page
    (``IMAGE_COVERAGE``). Text recognition reads such a page from its
    picture alone; a cover, a plate or a photo is one too, and the
    recognition finds no text there.

    The glyph count first, from ``blocks`` (as for ``is_scan_page``, a
    function returning a text dictionary's blocks the caller has anyway)
    or a dictionary of its own; the images' boxes only for a page that
    passes it. A run asks ``is_scan_page`` first: a scan with a layer has
    invisible glyphs, and this rule says no to it. A page that cannot be
    read is no picture page.

    Known miss: a scan whose OCR text is drawn visibly but under the
    picture (render mode 0, hidden by the drawing order rather than by its
    render mode) has its words counted as shown text, so it is neither a
    scan to ``is_scan_page`` nor a picture page here, and keeps its text."""
    try:
        invisible, visible = _glyph_counts(page, blocks() if blocks else page.get_text("dict", flags=0)["blocks"])
        if invisible or visible > PICTURE_STAMP_CHARS:
            return False
        return _image_coverage(page) >= IMAGE_COVERAGE
    except Exception:  # noqa: BLE001 — an unreadable page keeps its plain text
        return False


def rows(bboxes) -> list:
    """For each of a page's lines after the first, whether it starts a new
    visual row: ``bboxes`` are MuPDF's line boxes ``(x0, y0, x1, y1)``, y
    down, in the page's text order.

    The lines are chained in that order, never re-sorted (a scan's stream is
    in reading order already, and sorting by position scrambles stacked
    fractions and sub- and superscripts). A line joins the row of the line
    before it unless it overlaps the row's band (the median top and the
    median bottom of the row's lines so far) by less than ``ROW_OVERLAP``
    of the smaller of the two heights, starts before the line before it
    ends, or starts more than ``ROW_GAP`` row heights past that line's end.

    The words of a row stand side by side, so a line that starts left of
    the previous one's end is the next row, even when it overlaps the band:
    on a layer whose lines MuPDF already reads whole, a tall line (a
    formula in it) overlaps the one above, and taking it for the same row
    would hide the line break after a hyphenated word."""
    out = []
    tops, bottoms, prev = [], [], None
    for x0, y0, x1, y1 in bboxes:
        if prev is not None:
            top, bottom = median(tops), median(bottoms)
            band = bottom - top
            overlap = min(bottom, y1) - max(top, y0)
            new = (overlap < ROW_OVERLAP * min(band, y1 - y0)
                   or x0 < prev[2]
                   or x0 - prev[2] > ROW_GAP * band)
            out.append(new)
            if new:
                tops, bottoms = [], []
        tops.append(y0)
        bottoms.append(y1)
        prev = (x0, y0, x1, y1)
    return out


def _invisible_mode_used(page) -> bool:
    """Step 1: does a content stream the page draws, its own or a form's
    (nested, or named in resources the page inherits), set render mode 3
    or 7? Decompression only, no interpretation. A stream that cannot be
    read says "maybe"."""
    try:
        if _sets_invisible(page.read_contents()):
            return True
        doc = page.parent
        forms = {x for x, *_ in page.get_xobjects() if doc.xref_get_key(x, "Subtype")[1] == "/Form"}
        return any(_sets_invisible(doc.xref_stream(x) or b"") for x in forms)
    except Exception:  # noqa: BLE001
        return True


def _sets_invisible(stream: bytes) -> bool:
    """Whether ``stream`` holds a ``3 Tr`` or ``7 Tr`` operator. The
    operator is found as a literal and its operand read from the bytes just
    before it: a pattern that starts with the operand is tried at every
    byte, and took 20 to 37 ms on a 1.6 MB born-digital page."""
    for m in _TR_OP_RE.finditer(stream):
        if _TR_OPERAND_RE.search(stream, max(0, m.start() - 32), m.start()):
            return True
    return False


def _glyph_counts(page, blocks) -> tuple:
    """Step 2: the page's non-space glyphs as ``(invisible, visible)``,
    from its text dictionary's ``blocks`` ("dict" or "rawdict"). MuPDF
    reports render modes 3 and 7 as a span of alpha 0 (its text trace is no
    help: it drops mode 7 and doubles mode 2). Text inside an annotation's
    box is an annotation's appearance, which pdf.js's operator list leaves
    out too: it is not counted."""
    # an annotation's text runs a little past its box (its font's
    # ascender), so a span is the annotation's when its centre is inside
    annots = [tuple(a.rect) for a in page.annots()] if page.first_annot else []
    invisible = visible = 0
    for block in blocks:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                x0, y0, x1, y1 = span["bbox"]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if any(a0 <= cx <= a1 and b0 <= cy <= b1 for a0, b0, a1, b1 in annots):
                    continue
                text = span["text"] if "text" in span else "".join(ch["c"] for ch in span.get("chars", ()))
                n = sum(not ch.isspace() for ch in text)
                if span.get("alpha", 255) == 0:
                    invisible += n
                else:
                    visible += n
    return invisible, visible


def _image_coverage(page) -> float:
    """Step 3: the share of the page under the union of the boxes of the
    images it paints, measured on a ``GRID`` x ``GRID`` grid of cell
    centres. MuPDF gives the boxes in its page space unrotated: the page
    as shown (page.rect, the crop box clipped to the media box, its corner
    the origin, /UserUnit folded in) with its sides swapped back on a
    quarter turn. Measured against that, not the crop box as written,
    whose size is the page's neither under a /UserUnit nor for a crop box
    reaching past the media box."""
    rect = page.rect
    w, h = (rect.height, rect.width) if page.rotation % 180 else (rect.width, rect.height)
    if w <= 0 or h <= 0:
        return 0.0
    infos = page.get_image_info()
    if not infos:
        return 0.0
    cells = bytearray(GRID * GRID)
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        # the first and last cell whose centre lies in the box, each way
        i0 = max(0, math.ceil(x0 / w * GRID - 0.5))
        i1 = min(GRID - 1, math.floor(x1 / w * GRID - 0.5))
        j0 = max(0, math.ceil(y0 / h * GRID - 0.5))
        j1 = min(GRID - 1, math.floor(y1 / h * GRID - 0.5))
        if i1 < i0 or j1 < j0:
            continue
        row = b"\x01" * (i1 - i0 + 1)
        for j in range(j0, j1 + 1):
            cells[j * GRID + i0:j * GRID + i1 + 1] = row
    return sum(cells) / (GRID * GRID)
