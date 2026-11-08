"""A PDF's translated view written into a copy of the file: what an
annotated-PDF export carries while the viewer shows the page's
translation (docs/dev/translation.md "Exporting the translation").

The viewer sends what it shows (frontend/src/pdf/translateExport.js): for
each page, its translated blocks, each with the boxes of its original
lines, its font size and its text, a formula of the original standing in
the text as the box it is printed in. Coordinates are the viewer's: pdf.js's
page at scale 1, the view box in PDF units with /Rotate applied, top-left
origin, /UserUnit left out (``pdf_text.user_unit``). ``write`` does to the
file what the overlay does on screen (frontend/src/pdf/TransOverlay.jsx):

1. **The original lines go.** Their text is redacted: MuPDF takes the
   glyphs out of the content stream and leaves the pictures and drawings
   as they are. Only the middle of each line is redacted (``BAND``):
   MuPDF takes every glyph whose box (the font's full height, not its
   outline) meets the area, so the middle takes all of the line's own, and
   a tall glyph of the line above or below that reaches into the box (a
   display equation's big operator) stays in the file. Then the lines are
   masked in the block's ground, as the overlay masks them (``_masks``),
   which also covers a line drawn rather than written: a scanned page under
   its text layer. A page's grounds and inks are read here, not sent: a page
   exported may never have been shown. They come from one coarse picture of
   the page, by the overlay's rules (``_Sample``, ``_paint``).
   The annotations a redaction drops (MuPDF takes the links and FreeText
   boxes that meet it) are put back as they were.
2. **The translation is laid out** in the block's box by MuPDF's HTML
   engine (``Story``), at the block's size and with the overlay's leading,
   justified where the original is (``_setting``), shrunk until it fits
   (down to ``MIN_SCALE``, the overlay's floor too), in the paper's own
   font when the file holds it and it draws every letter of the
   translation (``_paper_faces``), else in a serif face (Charis SIL, Noto
   Serif for a letter it lacks, Droid Sans Fallback for CJK: MuPDF's own
   fonts, not the overlay's). Every page's blocks go into one temporary
   document, so each font is embedded once, subset to the characters used,
   and shared by every page it is stamped on. A formula takes its place in
   the text as an empty picture of its size, which reports where the line
   put it.
3. **Stamped.** The temporary page is drawn over the page, and each
   formula goes on the baseline at its picture's place as a picture of
   its box on the original page (``FORMULA_DPI``), drawn before any page
   was changed: the overlay's crop, at print resolution. A vector crop
   (the original page shown through a clip) would carry the whole page's
   text with it, invisible but there for every reader that ignores clips
   (pdf.js among them), around every formula. Under the picture lies the
   formula's text, written where its placeholder was drawn and painted
   nowhere, as the overlay keeps it under its crop: copying the words
   around it copies the formula with them (``_formula_texts``).

PyMuPDF is not thread-safe: every call on a document, a page or a writer
runs under ``pdf_text._lock``, a turn per page.
"""

import collections
import html
import itertools
import io
import math
import re
import unicodedata

from . import pdf_font_cmap, pdf_text

# Of a line's height, the part above and below its middle that is not
# redacted. On the library's fixture paper, 0.3 removed every glyph of the
# lines redacted and none of the others; 0.4 left three of the redacted.
BAND = 0.3
# Room around a block's lines for its translation, in the viewer's units: the
# overlay's (frontend/src/pdf/transBlock.js PAD).
PAD = 0.75
# A line's pitch over its size, the overlay's (frontend/src/pdf/transBlock.js
# LEADING): the block's own within these, the usual one for a single line, and
# CJK's, whose glyphs fill the em.
LEADING_MIN, LEADING_MAX, LEADING_SINGLE, LEADING_CJK = 1.12, 1.3, 1.15, 1.28
INK = "#1c1c1c"     # the overlay's usual text colour (.pdfTransText)
# A box narrower than this many em of its size is set ragged even where the
# original is justified: its gaps open wide (transBlock.js JUSTIFY_EM).
JUSTIFY_EM = 18
MIN_SCALE = 0.05    # the smallest a translation is shrunk to make it fit (transFit.js MIN_FIT)
_CJK = {"zh", "ja", "ko"}  # the language tags whose glyphs fill the em and have no italic
FORMULA_DPI = 600  # a formula's picture: about 1 KB for an inline symbol
FORMULA_PIXELS = 4_000_000  # at most, a bigger box drawn coarser (16 MB a picture)
# Lines per redaction: MuPDF's cost grows faster than their number (a
# thousand on a page took 9.3 s in one call, 1.4 s in calls of a hundred).
REDACT_BATCH = 100
_FIT_DELTA = 0.01   # how close the fit is found, of the size
# MuPDF reports a picture it placed in a line (Story.element_positions)
# 0.8 of its height above where it draws it, and draws its bottom on the
# baseline: measured on PyMuPDF 1.28.2 at sizes 7 to 20 with pictures of
# 0.2 to 2.5 em, and held there by tests/test_translated_pdf.py.
_IMAGE_DROP = 0.8
_PLACEHOLDER = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
_PLACEHOLDER_NAME = "formula.svg"
_DEFAULT_SPACES = ("DefaultGray", "DefaultRGB", "DefaultCMYK")
_FORMULA_FONTS = []  # a formula's hidden text's faces, loaded on first use (_formula_fonts)
# A font's name as the viewer sends it and as the file names it: no subset
# tag, and a composite font's encoding off its end.
_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")
_ENCODING_SUFFIX = re.compile(r"-Identity-[HV]$")
_BLANKS = re.compile(r"[ \t\r\n]+|\xa0+")  # runs of the blanks set in the serif when the paper's font has none
# A page's sample, and the rules that read a block's ground and ink from it: the
# overlay's (frontend/src/pdf/transPaint.js), which says why each is what it is.
SAMPLE_SCALE = 2           # pixels per pt
SAMPLE_PIXELS = 4_000_000  # at most, a bigger page sampled coarser
_RING = 1.5                # pt around a line
_MOST_PIXELS = 2000        # read of a block, about: a third of the overlay's, here at Python's pace
_BUCKET_BITS = 3
_IN_RING, _LINE_SHARE, _OWN_IN_RING = 0.05, 0.5, 0.01
_PAPER = 250
_FAR, _FARTHEST, _FEWEST = 0.7, 0.98, 6
_MOST_INK, _SAME_INK = 0.5, 48
_CHROMA, _DARK_GROUND = 40, 128
_MIN_CONTRAST = 2


def _to_page(rotation: int, width: float, height: float):
    """The map from a page's display frame (/Rotate applied, top-left
    origin) to MuPDF's unrotated page space, for a page ``width`` by
    ``height`` unrotated: a point (x, y) shown at the display's (x, y)."""
    if rotation == 90:
        return lambda x, y: (y, height - x)
    if rotation == 180:
        return lambda x, y: (width - x, height - y)
    if rotation == 270:
        return lambda x, y: (width - y, x)
    return lambda x, y: (x, y)


class _Frame:
    """A page's frames: its display frame in MuPDF's units (``size``,
    the viewer's units times ``unit``, the page's /UserUnit) and the map
    from there to its unrotated space, where the redactions and the stamps
    go. Made under the lock."""

    def __init__(self, page):
        self.rotation = page.rotation % 360
        self.unit = pdf_text.user_unit(page)
        with pdf_text.unrotated(page):
            width, height = page.rect.width, page.rect.height
        self.size = (height, width) if self.rotation in (90, 270) else (width, height)
        self._map = _to_page(self.rotation, width, height)

    def rect(self, x0, y0, x1, y1, mupdf):
        """A display-frame rectangle in MuPDF's units, in unrotated space."""
        (ax, ay), (bx, by) = self._map(x0, y0), self._map(x1, y1)
        return mupdf.Rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))

    def viewer_rect(self, box, mupdf):
        """A rectangle in the viewer's units, in unrotated space."""
        u = self.unit
        return self.rect(box[0] * u, box[1] * u, box[2] * u, box[3] * u, mupdf)


def _band(line):
    """The middle of a line's box that its redaction takes (``BAND``)."""
    x0, y0, x1, y1 = line
    h = y1 - y0
    return (x0, y0 + BAND * h, x1, y1 - BAND * h)


def _formula_ok(part, frames, frame) -> bool:
    """Whether a formula can be drawn as a picture of its page: the page is
    in the file, its box has an area and lies on that page (half a unit
    over its edge allowed), and it turns as the page it goes on (a formula
    a page join moved from a page turned otherwise shows its text, as does
    one the page does not show)."""
    src = frames.get(part["page"])
    if src is None or src.rotation != frame.rotation:
        return False
    x0, y0, x1, y1 = part["box"]
    width, height = src.size[0] / src.unit, src.size[1] / src.unit
    return x1 > x0 and y1 > y0 and x0 >= -0.5 and y0 >= -0.5 and x1 <= width + 0.5 and y1 <= height + 0.5


def _set_text(block, frames, frame):
    """What a block's story sets as text: its words, and the text of each
    formula that cannot be its picture."""
    for part in block["parts"]:
        if isinstance(part, str):
            yield part
        elif not _formula_ok(part, frames, frame):
            yield part.get("text") or ""


def _blanks(text: str, paper) -> str:
    """An escaped text with each run of blanks the paper's font does not
    draw (TeX's fonts have no space) set in the serif (``span.gs``), Charis
    SIL, which the file has for the other blocks anyway: MuPDF's own fallback
    would bring in Noto Serif, 60 KB, for the spaces alone."""
    if paper is None:
        return text

    def wrap(match):
        blank = match[0] if match[0][0] == "\xa0" else " "
        return blank if paper.draws_blank(blank[0]) else f'<span class="gs">{blank}</span>'

    return _BLANKS.sub(wrap, text)


def _block_html(block, lang, frames, frame):
    """A block's translation as the HTML its story lays out, and the
    formulas in it in order: ``(part, width, ascent, descent)`` in MuPDF's
    units, a picture ``f<k>`` of ``width`` by ``ascent`` standing for each."""
    out, formulas = [], []
    paper = block.get("paper")
    for part in block["parts"]:
        if isinstance(part, str):
            out.append(_blanks(html.escape(part, quote=False), paper))
            continue
        if not _formula_ok(part, frames, frame):
            out.append(_blanks(html.escape(part.get("text") or "", quote=False), paper))
            continue
        u = frame.unit  # in the viewer's units, the same size on the page it goes on
        x0, _, x1, _ = part["box"]
        width = (x1 - x0) * u
        ascent, descent = (v * u for v in _rise(part))
        out.append(f'<img id="f{len(formulas)}" src="{_PLACEHOLDER_NAME}" '
                   f'style="width:{width:.3f}px;height:{ascent:.3f}px">')
        formulas.append((part, width, ascent, descent))
    return f'<p lang="{html.escape(lang)}">{"".join(out)}</p>', formulas


def _rise(part):
    """A formula's height above and below its baseline, in the viewer's
    units: all of it above when its baseline is at its top or higher, so it
    stands on the line."""
    _, y0, _, y1 = part["box"]
    base = min(max(part["base"], y0), y1)
    ascent, descent = base - y0, y1 - base
    return (descent, 0.0) if ascent <= 0 else (ascent, descent)


def _is_cjk(lang: str) -> bool:
    return lang.split("-")[0] in _CJK


def _leading(block, lang: str) -> float:
    """A block's line height over its size, the overlay's rule
    (transBlock.js blockLeading): the median step between its lines' tops,
    held to what papers set."""
    if _is_cjk(lang):
        return LEADING_CJK
    tops = [line[1] for line in block["lines"]]
    if len(tops) < 2:
        return LEADING_SINGLE
    steps = sorted(b - a for a, b in zip(tops, tops[1:]))
    return min(LEADING_MAX, max(LEADING_MIN, steps[(len(steps) - 1) // 2] / block["size"]))


def _edges(block):
    """The left and right of the box a block's text fills, in the viewer's
    units: a one-line block's room (``box``), else its lines'."""
    if block.get("box") and block["box"][2] > block["box"][0]:
        return block["box"][0], block["box"][2]
    return min(line[0] for line in block["lines"]), max(line[2] for line in block["lines"])


def _setting(block, lang: str) -> str:
    """How a block is set besides its size, as CSS, the overlay's rules
    (transBlock.js blockSetting): centred, or justified where the original is
    (pageShape.js isJustified) and the box is ``JUSTIFY_EM`` wide, else
    ragged. As on screen, CJK text is never italic: its fonts have none, and
    a block in the paper's own font takes its weight and slant from it. The
    first line's indent is in em, so it shrinks with the text, where the
    overlay's stays the page's."""
    left, right = _edges(block)
    if block.get("centred"):
        out = "text-align:center;"
    elif block.get("justified") and right - left >= block["size"] * JUSTIFY_EM:
        out = "text-align:justify;"
    else:
        out = ""
    if block.get("paper") is None:
        out += "font-weight:bold;" if block.get("bold") else ""
        if block.get("italic") and not _is_cjk(lang):
            out += "font-style:italic;"
    if block.get("indent", 0) > 0:
        out += f"text-indent:{block['indent'] / block['size']:.3f}em;"
    return out


def _hex(colour) -> str:
    return "#{:02x}{:02x}{:02x}".format(*colour)


def _css(size: float, lead: float = LEADING_SINGLE, ink: str = INK, setting: str = "", paper=None) -> str:
    """A block's style sheet: the serif, or the ``paper``'s own font (a
    ``_PaperFont`` the story's archive holds) with the serif behind it, for
    the blanks it lacks."""
    family, face = "serif", ""
    if paper is not None:
        family = f"{paper.family},serif"
        face = f"@font-face{{font-family:{paper.family};src:url({paper.file})}} span.gs{{font-family:serif}} "
    return (f"{face}body{{margin:0}} p{{margin:0;font-family:{family};font-size:{size:.3f}px;"
            f"line-height:{lead:.3f};color:{ink};{setting}}}")


def _layout_block(mupdf, device, archive, block, lang, frames, frame):
    """Draw one block's translation into the temporary page (``device``)
    in its box, and say where its formulas went: ``[(part, dest)]``, dest
    the rectangle in the page's display frame, MuPDF's units."""
    u = frame.unit
    _, ys0, _, ys1 = zip(*block["lines"])
    left, right = _edges(block)
    bottom = max(ys1) + PAD
    if block.get("foot") is not None:  # the next block starts inside the box: its text opens PAD above that
        bottom = min(bottom, block["foot"] - PAD)
    box = mupdf.Rect((left - PAD) * u, (min(ys0) - PAD) * u, (right + PAD) * u, bottom * u)
    markup, formulas = _block_html(block, lang, frames, frame)
    ink = _hex(block["ink"]) if block.get("ink") else INK
    css = _css(block["size"] * u, _leading(block, lang), ink, _setting(block, lang), block.get("paper"))
    story = mupdf.Story(markup, user_css=css, archive=archive)
    no_overflow = mupdf.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW
    fit = story.fit_scale(mupdf.Rect(0, 0, box.width, box.height), scale_min=1, scale_max=1 / MIN_SCALE,
                          delta=_FIT_DELTA, flags=no_overflow)
    if fit.big_enough:
        scale, room, flags = 1 / fit.parameter, fit.rect, no_overflow
    else:  # a word wider than the box at the smallest size: it runs over
        scale, flags = MIN_SCALE, 0
        room = mupdf.Rect(0, 0, box.width / MIN_SCALE, box.height / MIN_SCALE)
    story.reset()
    story.place(room, flags=flags)
    placed = {}

    def seen(position):
        # A picture is reported three times: as it opens and closes (no
        # width) and as itself.
        r = position.rect
        if position.id and position.id.startswith("f") and r[2] - r[0] > 0:
            placed.setdefault(int(position.id[1:]), r)

    story.element_positions(seen)
    story.draw(device, mupdf.Matrix(scale, 0, 0, scale, box.x0, box.y0))
    dests = []
    for k, (part, width, ascent, descent) in enumerate(formulas):
        r = placed.get(k)
        if r is None:
            continue
        baseline = box.y0 + (r[3] + _IMAGE_DROP * (r[3] - r[1])) * scale
        x = box.x0 + r[0] * scale
        dests.append((part, mupdf.Rect(x, baseline - ascent * scale, x + width * scale, baseline + descent * scale)))
    return dests


def _pages(pages, count: int) -> dict:
    """page number → its blocks, for the pages the file has: a page sent
    twice takes both lists, a line box without an area is left out and so
    is a block left without lines, and a block whose text is only blanks
    writes nothing (its lines still go)."""
    out = {}
    for page in pages:
        if not 1 <= page["page"] <= count:
            continue
        for block in page["blocks"]:
            lines = [tuple(line) for line in block["lines"] if line[2] > line[0] and line[3] > line[1]]
            if not lines:
                continue
            parts = [p for p in block["parts"] if not isinstance(p, str) or p]
            if all(isinstance(p, str) and not p.strip() for p in parts):
                parts = []
            out.setdefault(page["page"], []).append({**block, "lines": lines, "parts": parts})
    return dict(sorted(out.items()))


def _font_name(name) -> str:
    return _ENCODING_SUFFIX.sub("", _SUBSET_TAG.sub("", name or "")).strip()


def _unseen(c: str) -> bool:
    """A character that needs no glyph to be read: a blank or a format
    character (pdf/paperFace.js UNSEEN)."""
    return c.isspace() or unicodedata.category(c) == "Cf"


class _PaperFont:
    """A font of the file a translation may be set in: its program as the
    temporary document's story reads it (``file`` in its archive, ``family``
    in its CSS), and what it draws. Made and asked under the lock."""

    def __init__(self, mupdf, xref: int, ext: str, buffer: bytes):
        self.family, self.file, self.buffer = f"pf{xref}", f"pf{xref}.{ext}", buffer
        self._low = mupdf.mupdf
        self._font = mupdf.Font(fontbuffer=buffer)
        self._inked = {}

    def inked(self, c: str) -> bool:
        """Whether the font has a glyph for ``c`` with an outline: a subset
        can keep a letter's place in the font and empty its outline (MuPDF's
        own subsetter does), which ``has_glyph`` still counts. A glyph MuPDF
        cannot outline counts as none."""
        if c not in self._inked:
            low = self._low
            try:
                gid = self._font.has_glyph(ord(c))
                path = low.fz_outline_glyph(self._font.this, gid, low.FzMatrix()) if gid else None
                self._inked[c] = bool(path is not None and path.m_internal is not None and not low.fz_is_empty_rect(
                    low.fz_bound_path(path, low.FzStrokeState(), low.FzMatrix())))
            except Exception:  # a damaged glyph program
                self._inked[c] = False
        return self._inked[c]

    def draws_blank(self, c: str) -> bool:
        return bool(self._font.has_glyph(ord(c))) and self._font.glyph_advance(ord(c)) > 0

    def covers(self, chars) -> bool:
        return all(self.inked(c) for c in chars if not _unseen(c))


def _paper_faces(mupdf, doc, todo, frames) -> dict:
    """The file's own font each block is set in, as its ``paper``: the one
    the viewer says most of its text is in (``face``, a name), found by that
    name among its page's fonts, embedded and no Type 3, when it has a glyph
    with ink for every letter the block sets (``_set_text``). A subset holds
    only the letters the paper used, so a translation into another language
    often needs one it lacks, and the block then keeps the serif whole: one
    face a block, not a letter here and there in another. The overlay
    decides the same on pdf.js's copy of the font (pdf/paperFace.js). →
    {family: _PaperFont} of the fonts used."""
    fonts, used = {}, {}
    for pn, blocks in todo.items():
        wanted = [block for block in blocks if block.get("face") and block["parts"]]
        if not wanted:
            continue
        with pdf_text._lock:
            listed = doc.get_page_fonts(pn - 1, full=True)
            named = {}
            for xref, ext, kind, base, *_ in listed:
                if ext not in ("", "n/a") and kind != "Type3":
                    named.setdefault(_font_name(base), []).append((xref, ext))
            for block in wanted:
                chars = set("".join(_set_text(block, frames, frames[pn])))
                for xref, ext in named.get(_font_name(block["face"]), []):
                    if xref not in fonts:
                        fonts[xref] = _paper_font(mupdf, doc, xref, ext)
                    font = fonts[xref]
                    if font is not None and font.covers(chars):
                        block["paper"] = used[font.family] = font
                        break
    return used


def _paper_font(mupdf, doc, xref: int, ext: str):
    """The font object ``xref`` of the file as a ``_PaperFont``, or None for
    one MuPDF cannot read. A composite font's program that maps no
    character to its glyphs (a bare CID-keyed CFF, as LuaLaTeX and XeLaTeX
    embed every OpenType font) is given a cmap from the file's ToUnicode
    first (``pdf_font_cmap``), as pdf.js does for the overlay. Under the
    lock."""
    try:
        buffer = doc.extract_font(xref)[3]
        if not buffer:
            return None
        buffer, ext = pdf_font_cmap.unicode_program(mupdf, doc, xref, ext, buffer) or (buffer, ext)
        return _PaperFont(mupdf, xref, ext, buffer)
    except Exception:  # a damaged font program: the block keeps the serif
        return None


def _sheets(mupdf, todo, frames, lang, papers=None):
    """Step 2: every page's translation laid out on a page of one new
    document (``_layout_block``), its fonts subset. → (the document, page
    number → the formulas placed on it, the pages with anything drawn).
    The k-th page of the document is the k-th page of ``todo``. MuPDF's
    writer draws each formula's empty placeholder as a picture of a pixel
    stretched over its place, opaque; a sheet has no other picture, so each
    drawing of one becomes its formula's hidden text (``_formula_texts``)
    before the fonts are subset. ``papers`` (``_paper_faces``) go into the
    stories' archive: MuPDF embeds a font program once however many
    stories load it."""
    archive = mupdf.Archive()
    archive.add((_PLACEHOLDER, _PLACEHOLDER_NAME))
    for paper in (papers or {}).values():
        archive.add((paper.buffer, paper.file))
    out = io.BytesIO()
    formulas, drawn = {}, set()
    with pdf_text._lock:
        writer = mupdf.DocumentWriter(out)
    try:
        for pn, blocks in todo.items():
            frame = frames[pn]
            with pdf_text._lock:
                device = writer.begin_page(mupdf.Rect(0, 0, *frame.size))
                formulas[pn] = []
                for block in blocks:
                    if block["parts"]:
                        formulas[pn] += _layout_block(mupdf, device, archive, block, lang, frames, frame)
                        drawn.add(pn)
                writer.end_page()
    finally:
        with pdf_text._lock:
            writer.close()
    with pdf_text._lock:
        sheets = pdf_text._mupdf_open(out.getvalue())
        try:
            fonts = _formula_fonts(mupdf)
            for page, placed in zip(sheets, formulas.values()):
                _formula_texts(mupdf, page, placed, fonts)
            sheets.subset_fonts()
            _surrogates(sheets)
            data = sheets.tobytes(garbage=1, deflate=True)
        finally:
            sheets.close()
        return pdf_text._mupdf_open(data), formulas, drawn


def _formula_fonts(mupdf):
    """The faces a formula's hidden text is set in, each letter in the first
    that has it: Noto Sans Math, which MuPDF builds in (SIL Open Font
    License), for ASCII, Greek, the operators, arrows and math alphanumerics,
    then Charis SIL, the translation's own, for the accented Latin and the
    Cyrillic Noto Sans Math lacks (µ, ·, é). So a file takes one font more,
    subset, where MuPDF's fallback alone would add Noto Serif for a micro
    sign. That fallback, the one ``Story`` takes, finds any other letter. A
    MuPDF built without them starts in Times. Made under the lock, once."""
    if not _FORMULA_FONTS:
        low = mupdf.mupdf
        for data, size in (low.fz_lookup_noto_math_font(), low.fz_lookup_builtin_font("Charis SIL", 0, 0)):
            if size:
                _FORMULA_FONTS.append(mupdf.Font(fontbuffer=low.raw_to_python_bytes(data, size)))
        if not _FORMULA_FONTS:
            _FORMULA_FONTS.append(mupdf.Font("tiro"))
    return _FORMULA_FONTS


def _show(mupdf, fonts, text):
    """``text`` as MuPDF text at size 1 from the origin, each run of letters
    in the first of ``fonts`` that has them (the first, with MuPDF's
    fallback, for a letter none has). → (the text, its advance)."""
    low = mupdf.mupdf
    run, trm = low.FzText(), low.fz_make_matrix(1, 0, 0, 1, 0, 0)

    def face(c):
        return next((k for k, f in enumerate(fonts) if f.has_glyph(ord(c))), 0)

    for k, letters in itertools.groupby(text, face):
        trm = low.fz_show_string(run, fonts[k].this, trm, "".join(letters), 0, 0, 0, low.FZ_LANG_UNSET)
    return run, trm.e


def _hidden_text(mupdf, pdf, fonts, part):
    """A formula's text as content that paints nothing (render mode 3), in
    the space its placeholder is drawn in: the unit square over the formula's
    part above the baseline, the baseline at 0. Its em is the formula's whole
    height and its run the formula's width, so a reader's selection covers
    the picture. → (content, {font name: xref}), the fonts named for their
    objects so they meet no name of the sheet's own; None for a text of
    blanks."""
    text = (part.get("text") or "").strip()
    if not text:
        return None
    low = mupdf.mupdf
    run, advance = _show(mupdf, fonts, text)
    if advance <= 0:
        return None
    ascent, descent = _rise(part)
    resources, content = low.pdf_new_dict(pdf, 1), low.fz_new_buffer(256)
    device = low.pdf_new_pdf_device(pdf, low.FzMatrix(), resources, content)
    low.fz_ignore_text(device, run, low.FzMatrix(1 / advance, 0, 0, (ascent + descent) / ascent, 0, 0))
    low.fz_close_device(device)
    named = low.pdf_dict_gets(resources, "Font")
    names = {low.pdf_to_name(low.pdf_dict_get_key(named, i)): low.pdf_to_num(low.pdf_dict_get_val(named, i))
             for i in range(low.pdf_dict_len(named))}
    chunk = re.sub(rb"/([A-Za-z0-9]+) (?=[0-9.]+ Tf)",
                   lambda m: b"/Fm%d " % names[m[1].decode()] if m[1].decode() in names else m[0],
                   low.fz_buffer_extract(content))
    # MuPDF 1.28's device sets the render mode itself; 1.27's (the mobile
    # builds') paints the text in the sheet's ink.
    return b"q\n3 Tr\n" + chunk + b"Q\n", {f"Fm{xref}": xref for xref in names.values()}


def _formula_texts(mupdf, page, placed, fonts) -> None:
    """A sheet's formula placeholders put to use, under the lock: each
    drawing of one (the writer's single content stream draws them in the
    order the formulas were placed) becomes that formula's hidden text
    (``_hidden_text``), and the pictures leave the page. Should the count
    differ from the formulas', the drawings only go."""
    names = [item[7] for item in page.get_images(full=True)]
    if not names:
        return
    doc = page.parent
    stream = page.get_contents()[0]
    content = doc.xref_stream(stream)
    draw = re.compile(rb"/(" + b"|".join(re.escape(n.encode()) for n in names) + rb") Do\b")
    count = len(draw.findall(content))
    pdf = mupdf._as_pdf_document(doc)
    hidden = iter([_hidden_text(mupdf, pdf, fonts, part) for part, _ in placed] if count == len(placed)
                  else [None] * count)
    used = {}

    def put(_match):
        made = next(hidden)
        if made is None:
            return b""
        used.update(made[1])
        return made[0]

    doc.update_stream(stream, draw.sub(put, content))
    _put_resources(doc, page, "XObject", {name: "null" for name in names})
    _put_resources(doc, page, "Font", {name: f"{xref} 0 R" for name, xref in used.items()})


def _utf16(match) -> bytes:
    code = int(match[1], 16) - 0x10000
    return b"<%04x%04x>" % (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))


def _surrogates(doc) -> None:
    """MuPDF writes a character past U+FFFF into a font's ToUnicode map as its
    bare number (``<1d465>``), which readers garble (MuPDF and pdf.js read
    U+1D46): each becomes its pair of UTF-16 surrogates (``<d835dc65>``). A
    formula's math italic is one, and so are an emoji and a rare CJK letter in
    the translation. The codes beside them are four digits."""
    seen = set()
    for xref in range(1, doc.xref_length()):
        kind, value = doc.xref_get_key(xref, "ToUnicode")
        if kind != "xref" or value in seen:
            continue
        seen.add(value)
        cmap = int(value.split()[0])
        data = doc.xref_stream(cmap)
        fixed = re.sub(rb"<([0-9a-fA-F]{5,6})>", _utf16, data)
        if fixed != data:
            doc.update_stream(cmap, fixed)


def _pictures(mupdf, doc, formulas, frames) -> dict:
    """Every formula's picture, ``(page number, k) → (dest, png)``: its box
    on its page drawn at ``FORMULA_DPI`` on a clear ground, before any page
    is changed, and the rectangle it goes in on the page it was placed on,
    unrotated. A page is drawn into a display list once for all its
    formulas, a turn of the lock each. A box past ``FORMULA_PIXELS`` at
    ``FORMULA_DPI`` is drawn coarser."""
    by_source = {}
    for pn, placed in formulas.items():
        for k, (part, dest) in enumerate(placed):
            by_source.setdefault(part["page"], []).append((pn, k, part, dest))
    out = {}
    for source, items in sorted(by_source.items()):
        with pdf_text._lock:
            page = doc[source - 1]
            try:
                with pdf_text.unrotated(page):
                    shown = page.get_displaylist()
                    bounds = page.rect
                src = frames[source]
                for pn, k, part, dest in items:
                    clip = src.viewer_rect(part["box"], mupdf) & bounds
                    zoom = min(FORMULA_DPI / 72, (FORMULA_PIXELS / max(clip.width * clip.height, 1e-6)) ** 0.5)
                    pixmap = shown.get_pixmap(matrix=mupdf.Matrix(zoom, zoom), clip=clip, alpha=True)
                    out[(pn, k)] = (frames[pn].rect(*dest, mupdf), pixmap.tobytes("png"))
                    del pixmap
                del shown
            finally:
                del page
    return out


class _Sample:
    """A page's picture to read colours from, in its display frame: one for
    the page, not one a block, since a block's own picture would cost a
    drawing of the page. Unsmoothed, so a stem keeps its colour at this low a
    resolution, and without the annotations, which are no ground. MuPDF's
    level is global, so it is put back at once, under the lock that holds every
    other call. ``scale``: pixels per unit of the viewer's. Made under the lock."""

    def __init__(self, page, frame, mupdf):
        zoom = min(SAMPLE_SCALE / frame.unit, (SAMPLE_PIXELS / max(frame.size[0] * frame.size[1], 1)) ** 0.5)
        level = mupdf.TOOLS.show_aa_level()
        mupdf.TOOLS.set_aa_level(0)
        try:
            pixmap = page.get_pixmap(matrix=mupdf.Matrix(zoom, zoom), alpha=False, annots=False)
        finally:
            mupdf.TOOLS.set_aa_level(level["graphics"])
        self.data, self.stride, self.width, self.height = pixmap.samples, pixmap.stride, pixmap.width, pixmap.height
        self.scale = zoom * frame.unit

    def colours(self, block):
        """``(lines, ring)``, each a Counter of (r, g, b) → how many: what a
        block's lines cover and the ring around each, every ``step``-th pixel
        each way, so a long paragraph costs what a short one does. A row of a
        ring in the next line's height is that line's, since ink reaches it."""
        k = self.scale
        boxes = [(max(0, math.floor((x0 - _RING) * k)), max(0, math.floor((y0 - _RING) * k)),
                  min(self.width, math.ceil((x1 + _RING) * k)), min(self.height, math.ceil((y1 + _RING) * k)),
                  math.floor(x0 * k), math.ceil(x1 * k), math.floor(y0 * k), math.ceil(y1 * k))
                 for x0, y0, x1, y1 in block["lines"]]
        area = sum(max(0, b[2] - b[0]) * max(0, b[3] - b[1]) for b in boxes)
        step = max(1, round((area / _MOST_PIXELS) ** 0.5))
        lines, ring = collections.Counter(), collections.Counter()

        def add(out, y, x0, x1):
            row = self.data[y * self.stride + x0 * 3:y * self.stride + x1 * 3]
            out.update(zip(row[0::3 * step], row[1::3 * step], row[2::3 * step]))

        def in_height(j, y):
            return 0 <= j < len(boxes) and boxes[j][6] <= y < boxes[j][7]

        for j, (x0, y0, x1, y1, left, right, _top, _bottom) in enumerate(boxes):
            # On the lattice of every ``step``-th pixel from x0, in the box.
            start = min(x1, max(x0, x0 + math.ceil((left - x0) / step) * step))
            end = min(x1, max(start, x0 + math.ceil((right - x0) / step) * step))
            for y in range(y0, y1, step):
                if in_height(j, y):
                    add(ring, y, x0, start)
                    add(lines, y, start, end)
                    add(ring, y, end, x1)
                else:
                    add(lines if in_height(j - 1, y) or in_height(j + 1, y) else ring, y, x0, x1)
        return lines, ring


def _modal(counts):
    """The commonest colour: the exact one that leads the commonest bucket."""
    buckets = collections.Counter()
    for (r, g, b), n in counts.items():
        buckets[(r >> _BUCKET_BITS, g >> _BUCKET_BITS, b >> _BUCKET_BITS)] += n
    top = max(buckets, key=buckets.get)
    return max((c for c in counts if tuple(v >> _BUCKET_BITS for v in c) == top), key=counts.get)


def _share(counts, colour) -> float:
    """The share of ``counts`` in ``colour``'s bucket."""
    top = tuple(v >> _BUCKET_BITS for v in colour)
    return sum(n for c, n in counts.items() if tuple(v >> _BUCKET_BITS for v in c) == top) / sum(counts.values())


def _ground_of(lines, ring):
    """The ground under a block's ``lines``, with the ``ring`` around them:
    their own commonest colour when it fills them and reaches the ring, else
    the commonest of both, None when that is not in the ring."""

    def around(colour, share):
        return not ring or _share(ring, colour) >= share

    if lines:
        own = _modal(lines)
        if _share(lines, own) >= _LINE_SHARE and around(own, _OWN_IN_RING):
            return own
    ground = _modal(lines + ring)
    return ground if around(ground, _IN_RING) else None


def _luminance(colour) -> float:
    r, g, b = (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in (c / 255 for c in colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _distance(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _ink_of(counts, ground):
    """The ink among ``counts``, apart from ``ground``: the commonest colour
    among what lies farthest from it, None when no one colour holds them."""
    far = [0] * 256
    for colour, n in counts.items():
        far[_distance(colour, ground)] += n
    farthest, stray = 255, sum(counts.values()) * (1 - _FARTHEST)
    while farthest > 0 and stray > 0:
        stray -= far[farthest]
        farthest -= 1
    if not farthest:
        return None
    inked = {c: n for c, n in counts.items() if _distance(c, ground) >= farthest * _FAR}
    total = sum(inked.values())
    if total < _FEWEST:
        return None
    ink = _modal(inked)
    return ink if sum(n for c, n in inked.items() if _distance(c, ink) <= _SAME_INK) >= total * _MOST_INK else None


def _paint(sample, block) -> dict:
    """What a block sits on and is written in, when it is not the usual:
    ``ground`` none on plain paper, ``ink`` none for the usual dark one, and
    both none with no ground to tell, or a dark one with no ink of its own:
    the usual ink could not be read on it, on paper it can."""
    none = {"ground": None, "ink": None}
    lines, ring = sample.colours(block)
    if not lines and not ring:
        return none
    ground = _ground_of(lines, ring)
    if ground is None:
        return none
    ink = _ink_of(lines + ring, ground)
    dark = sum(ground) / 3 < _DARK_GROUND
    own = ink and (max(ink) - min(ink) >= _CHROMA or dark) and _contrast(ink, ground) >= _MIN_CONTRAST
    if dark and not own:
        return none
    return {"ground": None if min(ground) >= _PAPER else ground, "ink": ink if own else None}


def _masks(block):
    """The boxes the overlay masks for a block, in the viewer's units: each
    line padded by ``PAD``, down to the next line's top when that is less
    than a line away, so the leading between them is covered too. A line
    whose glyph box runs into the next one's keeps its own bottom: the next
    line may be shorter."""
    lines = block["lines"]
    out = []
    for j, (x0, y0, x1, y1) in enumerate(lines):
        below = lines[j + 1][1] if j + 1 < len(lines) else None
        bottom = below if below is not None and 0 <= below - y1 < block["size"] * 1.2 else y1
        out.append((x0 - PAD, y0 - PAD, x1 + PAD, bottom + PAD))
    return out


def _put_resources(doc, page, kind, entries) -> None:
    """Entries into a page's /Resources of a ``kind`` (/ColorSpace, /Font…),
    by name, ``"null"`` taking one out. MuPDF sets a key by its path only
    through direct dictionaries, so the path starts at the last object on the
    way."""
    owner, path = page.xref, ""
    for key in ("Resources", kind):
        got, value = doc.xref_get_key(owner, path + key)
        if got == "xref":
            owner, path = int(value.split()[0]), ""
        else:
            path += key + "/"
    for name, value in entries.items():
        doc.xref_set_key(owner, path + name, value)


def _stamp(mupdf, page, frame, blocks, sheets, sheet_index, pictures):
    """Steps 1 and 3 on one page, under the lock: its translated blocks'
    lines redacted (``REDACT_BATCH`` at a time) and masked in their grounds,
    white on plain paper, the laid-out sheet drawn over it (``sheet_index`` None: nothing to draw),
    then the formulas' pictures, ``(dest, png)``. The annotations the
    redactions dropped, a link the viewer keeps working over the
    translation or another app's text box, are put back in the page's
    /Annots as they are, rectangle, action and all. So are its default colour
    spaces (``_DEFAULT_SPACES``), which the rewritten content names nowhere and
    MuPDF leaves out of its resources: without them every colour of the page
    shifts, and a tinted panel no longer matches the ground read from it."""
    doc = page.parent
    with pdf_text.unrotated(page):
        before = [xref for xref, *_ in page.annot_xrefs()]
        spaces = {name: doc.xref_get_key(page.xref, f"Resources/ColorSpace/{name}")[1] for name in _DEFAULT_SPACES}
        bands = [frame.viewer_rect(_band(line), mupdf) for block in blocks for line in block["lines"]]
        for at in range(0, len(bands), REDACT_BATCH):
            for band in bands[at:at + REDACT_BATCH]:
                page.add_redact_annot(band, fill=False)
            page.apply_redactions(images=mupdf.PDF_REDACT_IMAGE_NONE, graphics=mupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=mupdf.PDF_REDACT_TEXT_REMOVE)
        now = [xref for xref, *_ in page.annot_xrefs()]
        lost = [xref for xref in before if xref not in now]
        if lost:
            doc.xref_set_key(page.xref, "Annots", "[" + " ".join(f"{xref} 0 R" for xref in now + lost) + "]")
        _put_resources(doc, page, "ColorSpace", {name: value for name, value in spaces.items() if value != "null"})
        grounds = {}
        for block in blocks:
            grounds.setdefault(block.get("ground") or (255, 255, 255), []).append(block)
        shape = page.new_shape()
        for ground, grouped in grounds.items():
            for block in grouped:
                for mask in _masks(block):
                    shape.draw_rect(frame.viewer_rect(mask, mupdf))
            shape.finish(color=None, fill=tuple(v / 255 for v in ground), width=0)
        shape.commit()
        if sheet_index is not None:
            page.show_pdf_page(page.rect, sheets, sheet_index, rotate=frame.rotation)
        for dest, png in pictures:
            page.insert_image(dest, stream=png, keep_proportion=False)


def write(pdf_bytes: bytes, pages, lang: str) -> bytes:
    """``pdf_bytes`` with each page's translated blocks in place of their
    original lines (the module's docstring). ``pages``: ``[{page, blocks:
    [{lines: [[x0, y0, x1, y1], …], size, parts: [text | {page, box, base,
    text}]}]}]``, the viewer's units, and how a block is set where the
    overlay sets it so, each optional: ``box``, ``foot``, ``centred``,
    ``justified``, ``bold``, ``italic``, ``indent``, and ``face``, the name of
    the font most of it is in. A block whose ``parts`` are empty loses its lines
    and gets nothing (its words went to the part before it). Pages outside the file are left out, and nothing to write answers
    the bytes as they came. ValueError for a file that needs a password."""
    mupdf = pdf_text._pymupdf()
    with pdf_text._lock:
        doc = pdf_text._mupdf_open(pdf_bytes)
    sheets = None
    try:
        todo = _pages(pages, doc.page_count)
        if not todo:
            return pdf_bytes
        wanted = set(todo) | {part["page"] for blocks in todo.values() for block in blocks
                              for part in block["parts"] if isinstance(part, dict)}
        frames = {}
        for pn in sorted(n for n in wanted if 1 <= n <= doc.page_count):
            with pdf_text._lock:
                frames[pn] = _Frame(doc[pn - 1])
        for pn, blocks in todo.items():
            with pdf_text._lock:
                page = doc[pn - 1]
                try:
                    sample = _Sample(page, frames[pn], mupdf)
                finally:
                    del page
            for block in blocks:
                block.update(_paint(sample, block))
        papers = _paper_faces(mupdf, doc, todo, frames)
        sheets, formulas, drawn = _sheets(mupdf, todo, frames, lang, papers)
        pictures = _pictures(mupdf, doc, formulas, frames)
        for k, (pn, blocks) in enumerate(todo.items()):
            mine = [pictures[(pn, i)] for i in range(len(formulas[pn]))]
            with pdf_text._lock:
                page = doc[pn - 1]
                try:
                    _stamp(mupdf, page, frames[pn], blocks, sheets, k if pn in drawn else None, mine)
                finally:
                    del page
        # Unused objects go (the fonts the redaction left unused). Merging
        # duplicates (garbage=3) would only add time under the lock: the
        # writer makes none.
        with pdf_text._lock:
            return doc.tobytes(garbage=1, deflate=True)
    finally:
        with pdf_text._lock:
            if sheets is not None:
                sheets.close()
            doc.close()
