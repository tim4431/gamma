"""Text boxes: typed text placed on a PDF page or a sheet of paper
(docs/dev/text_boxes.md).

A text box is a block whose ``content`` is its text, Markdown like any
note, so search, the text merge, exports and the AI tools read it as one.
One property places it: ``text_box: {x, y, w, h, auto, size, color, bg}``
(and ``zotero``, below) in points on its surface. A box under a sheet is on
the nearest sheet above it (gamma/notebook.py), points from its top-left;
any other box with ``pdf_page`` (made at the page's top level, like an ink
group) is on that PDF page, in the frame ink files use (the pdf.js viewport
at scale 1, rotation applied, origin top-left, y down).
``x``, ``y`` is the outer top-left corner and ``w``, ``h`` the outer size,
padding included; ``h`` is as the client last measured it. ``auto`` makes
the width follow the text; otherwise the text wraps inside ``w``. ``size``
is the font size, ``color`` the text colour, ``bg`` the fill or None.
``zotero: true`` (stored only when true) makes a Zotero box, one laid out
as Zotero's reader lays out a text annotation: no padding, so ``x``, ``y``,
``w``, ``h`` are the text area itself and equal Zotero's rect, each blank
source line a line, and on screen the reader's font stack at its normal
line height. Stored values are read through ``normalize_text_box``, never
trusted.
frontend/src/markup/textBox.js is the twin of the rules here;
tests/shared/textbox.json and tests/shared/zoterotext.json pin both. The
second half lays a box out for the PDF writers.
"""

import math
import re

PAD = 4  # inner padding on every side, in points
LINE = 1.25  # line height, as a multiple of the font size
# A Zotero box on screen takes up to ZOTERO_LINE em a line (the reader's
# Segoe UI at line-height normal, 1.33 em, in Chrome's whole-pixel line
# boxes), and an auto one is sent ZOTERO_SLACK points wider than its text,
# as Zotero sizes a text annotation (text-annotation.js measureWidth).
ZOTERO_LINE = 1.4
ZOTERO_SLACK = 1
TEXT_SIZES = (9, 12, 16, 24, 36)
DEFAULT_COLOR = "#1f1f1f"
TEXT_BACKGROUNDS = (None, "#ffffff", "#fff4b8", "#dcecff")  # none, white, note yellow, light blue
# The largest page side a PDF allows (ISO 32000-1, Annex C) bounds every
# coordinate and extent.
MAX_COORD = 14400
MIN_WIDTH, DEFAULT_WIDTH = 24, 200
MIN_SIZE, MAX_SIZE, DEFAULT_SIZE = 6, 96, 12
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _round(value: float):
    """Hundredths, halves up as JavaScript's Math.round rounds them
    (Python's round goes to even), so both sides store the same number."""
    y = value * 100
    n = math.floor(y)
    if y - n >= 0.5:
        n += 1
    return n // 100 if n % 100 == 0 else n / 100


def _num(value, low, high, default):
    """A JSON number read as JavaScript reads it (a double), clamped and
    rounded; ``default`` when it is not a finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        value = float(value)
    except OverflowError:  # an integer past a double's range, which JSON.parse reads as Infinity
        return default
    return _round(min(high, max(low, value))) if math.isfinite(value) else default


def _color(value, default):
    return value.lower() if isinstance(value, str) and _HEX.fullmatch(value) else default


def normalize_text_box(value) -> dict | None:
    """A complete text box from a stored ``text_box``, or None when it is
    not an object: each missing or bad key takes its default, numbers
    clamped and rounded to hundredths, colours lowercase ``#rrggbb``,
    unknown keys dropped, ``zotero`` kept only when it is ``True``. ``h``
    defaults to one line of the box's size."""
    if not isinstance(value, dict):
        return None
    size = _num(value.get("size"), MIN_SIZE, MAX_SIZE, DEFAULT_SIZE)
    auto = value.get("auto")
    box = {
        "x": _num(value.get("x"), 0, MAX_COORD, 0),
        "y": _num(value.get("y"), 0, MAX_COORD, 0),
        "w": _num(value.get("w"), MIN_WIDTH, MAX_COORD, DEFAULT_WIDTH),
        "h": _num(value.get("h"), 0, MAX_COORD, _round(size * LINE + 2 * PAD)),
        "auto": auto if isinstance(auto, bool) else True,
        "size": size,
        "color": _color(value.get("color"), DEFAULT_COLOR),
        "bg": _color(value.get("bg"), None),
    }
    if value.get("zotero") is True:
        box["zotero"] = True
    return box


def pad_of(box: dict) -> float:
    """The box's inner padding on every side: none for a Zotero box."""
    return 0 if box.get("zotero") is True else PAD


def zotero_frame(box, width: float | None = None, height: float | None = None) -> dict | None:
    """``box`` as a Zotero box with its text where it was: a Zotero box as
    it is; a Gamma box's text area, its padding taken off (an auto box keeps
    its width, the padding becoming room for Zotero's wider face) and its
    lines' height at ``ZOTERO_LINE``. Normalized, and kept on a surface of
    ``width`` × ``height`` when that is given; None when it is no box."""
    box = normalize_text_box(box)
    if box is None:
        return None
    if not box.get("zotero"):
        box = normalize_text_box({
            **box, "x": box["x"] + PAD, "y": box["y"] + PAD, "w": box["w"] if box["auto"] else box["w"] - 2 * PAD,
            "h": max(0, box["h"] - 2 * PAD) * ZOTERO_LINE / LINE, "zotero": True})
    return box if width is None or height is None else keep_on_surface(box, width, height)


def keep_on_surface(box: dict, width: float, height: float) -> dict:
    """The box kept inside a surface of ``width`` × ``height`` points, as
    the client keeps a moved box: no wider or taller than the surface, its
    corner moved in; normalized."""
    w, h = min(box["w"], width), min(box["h"], height)
    return normalize_text_box({**box, "w": w, "h": h, "x": min(max(box["x"], 0), width - w),
                               "y": min(max(box["y"], 0), height - h)})


def is_text_box(props: dict | None) -> bool:
    return isinstance((props or {}).get("text_box"), dict)


def box_page(props: dict | None, on_sheet: bool = False) -> int | None:
    """The PDF page a text box is on: its ``pdf_page`` when that is a
    positive integer (as textBox.js reads it), else None. The nearest sheet
    wins: ``on_sheet`` says a sheet is among the block's ancestors, and then
    the box is on that sheet whatever its ``pdf_page`` says. A block that is
    not a text box names no page either. The writers label a box by it,
    passing down whether they walked through a sheet; a box that is neither
    on a sheet nor on a page is on no surface and stays a note."""
    page = (props or {}).get("pdf_page")
    if (on_sheet or not is_text_box(props) or isinstance(page, bool) or not isinstance(page, int)
            or page < 1):
        return None
    return page


# --- two writers, one box -------------------------------------------------------------
# Every write of a box sends it whole (a keystroke stores the size the box
# then measured at), so taken as one value a collaborator's move would be
# undone by someone typing. A `set` whose base_props names the box the
# change was made from is merged key by key instead (gamma/ops.py).


def merge_text_box(stored, mine, base):
    """The box a writer's change ``base`` → ``mine`` makes of the box
    stored now: the stored one, with the keys the writer changed taken from
    ``mine``, normalized (so a default spelled out is no change). Where both
    changed a key, the writer's value wins. ``mine`` as sent when it is no
    box (None deletes the key), and normalized when the stored value or the
    base is none: there is nothing to merge it into.
    frontend/src/markup/textBox.js mergeTextBox is the twin;
    tests/shared/textboxmerge.json pins both."""
    ours = normalize_text_box(mine)
    if ours is None:
        return mine
    now, was = normalize_text_box(stored), normalize_text_box(base)
    if now is None or was is None:
        return ours
    return {**now, **{k: v for k, v in ours.items() if v != was.get(k)}}


# --- the box in a PDF -----------------------------------------------------------------
# Both PDF writers draw a box the same way: the annotated export as a
# /FreeText's appearance (gamma/pdf_export.py), the notebook export as page
# content (gamma/notebook.py). The layout follows the box's read mode on
# screen (frontend/src/markup/markup.css), rendered Markdown with compact
# styles in the box's size and colour: no paragraph margins, one line per
# source line, headings a little larger, lists as CSS draws them. A Zotero
# box has no padding, and each blank source line is a line of its own, as
# the text is in Zotero's textarea.

# Where CSS puts the baseline in a line LINE high of Arial or Liberation Sans,
# the fonts the screen stack resolves Helvetica to: the leading beyond their
# 0.905 em ascent and 0.212 em descent is split above and below.
_BASELINE = (LINE - 0.905 - 0.212) / 2 + 0.905
_HEADING_SCALE = {1: 1.45, 2: 1.25, 3: 1.1}  # rendered notes' headings (app.css)
_DISPLAY_MATH_SCALE = 1.21  # KaTeX sets math at 1.21 em
_NEST = 1.5  # em a list item's text is indented per level


def _paragraphs(md: str, resolve_ref=None, gaps: bool = False) -> list:
    """The box's Markdown as drawable paragraphs, in order: ``("math", tex)``
    for display math, ``("gap",)`` for a blank line between two others
    when ``gaps``, else
    ``("text", spans, scale, level, marker)``, the
    spans styled as ``pdf_typeset`` draws them, ``scale`` the heading size,
    ``level`` the list nesting (1 for a top-level item, 0 outside a list)
    and ``marker`` the list or todo marker. ``resolve_ref`` (the notes PDF's
    block id → {content, …}) makes a ``[[ref]]`` or ``![[embed]]`` read as
    the first line of the block it names, as its chip does."""
    from .note_markup import TEXT
    from .pdf_document import _ref_label, chunks
    from .pdf_typeset import ITALIC, MONO, PLAIN, Style

    out = []
    for c in chunks(md, resolve_ref):
        kind = c["kind"]
        if kind == "math":
            out.append(("math", c["tex"]))
        elif kind == "text":
            marker = (("[x] " if c["todo"] else "[ ] ") if "todo" in c
                      else f"{c['bullet'] or '•'} " if "bullet" in c else "")
            out.append(("text", c["spans"], _HEADING_SCALE.get(c.get("heading"), 1), c.get("sub", 0), marker))
        elif kind == "code":
            out += [("text", [(TEXT, line or " ", 0, Style(MONO, None))], 1, 0, "")
                    for line in c["lines"]]
        elif kind == "table":
            for row in c["rows"]:
                spans = [s for n, cell in enumerate(row)
                         for s in ([(TEXT, "   ", 0, PLAIN)] if n else []) + cell]
                out.append(("text", spans, 1, 0, ""))
        elif kind in ("image", "embed"):
            label = _ref_label(c["id"], resolve_ref) if kind == "embed" else c.get("alt") or "image"
            out.append(("text", [(TEXT, label, 0, Style(ITALIC, None))], 1, 0, ""))
        elif kind == "gap" and gaps:
            out.append(("gap",))
        # rules, and blank lines in a Gamma box: the compact styles give them no height
    # A Zotero box's text has no blank line at either end: plain_text strips
    # them from what Zotero is sent, and read mode shows none.
    while out and out[0][0] == "gap":
        out.pop(0)
    while out and out[-1][0] == "gap":
        out.pop()
    return out


def plain_text(md: str, resolve_ref=None, gaps: bool = False) -> str:
    """The box's text as a reader sees it, Markdown marks dropped (a
    /FreeText's /Contents): a line per paragraph, math as its TeX source,
    references as ``_paragraphs`` reads them. ``gaps`` (a Zotero box's
    text) keeps each blank source line as an empty line, so ``a\n\nb``
    reads back as it is written."""
    from .note_markup import TEXT

    lines = []
    for p in _paragraphs(md, resolve_ref, gaps):
        if p[0] == "gap":
            lines.append("")
            continue
        if p[0] == "math":
            lines.append(f"$${p[1]}$$")
            continue
        _kind, spans, _scale, level, marker = p
        lines.append("  " * max(0, level - 1) + marker + "".join(
            payload if kind == TEXT else f"${payload}$" for kind, payload, *_ in spans))
    return "\n".join(lines).strip()


# What Markdown would take for syntax in plain text: backslash, code, emphasis
# and strikes, math, links and references, HTML (notes render raw HTML) and
# table pipes anywhere; a run of = (a ==mark==); an entity; and, where a line
# starts, a heading, quote, list, rule or setext underline. A bare URL is
# left whole: both readers link it as it stands (pdf_document's url rule).
_MD_ANYWHERE = re.compile(r"(https?://[^\s<>()\[\]]+)|[\\`*_~$\[\]<|]|(?<==)=|=(?==)|&(?=#?\w+;)")
_MD_LINE_START = re.compile(r"^(\s*)(?:([#>+=-])|(\d+)(?=[.)]))")


def escape_markdown(text: str) -> str:
    """``text`` with every character Markdown would read as syntax
    backslash-escaped, the start of each line included ("1." escapes its
    dot); whitespace stays as it is. A backslash before punctuation reads
    as that character anywhere, in CommonMark and in ``pdf_document``."""
    return "\n".join(
        _MD_LINE_START.sub(lambda m: m.group(1) + (f"\\{m.group(2)}" if m.group(2) else f"{m.group(3)}\\"),
                           _MD_ANYWHERE.sub(lambda m: m.group(1) or "\\" + m.group(0), line), count=1)
        for line in text.split("\n"))


def markdown_of(text: str) -> str:
    """Markdown that shows ``text`` as it is, for plain text another app
    wrote (a /FreeText's /Contents, its line breaks ``\\n``): each line
    trimmed (a leading indent would be a code block) and escaped
    (``escape_markdown``), so ``plain_text`` gives the text back and the
    page shows it as the other app did."""
    return escape_markdown("\n".join(line.strip() for line in text.split("\n"))).strip()


def _line_metrics(spans, size: float):
    """(ascent, height) of one line: LINE leading with the baseline where
    CSS puts it; tall inline math opens the line."""
    from .note_markup import MATH

    ascent, descent = size * _BASELINE, size * (LINE - _BASELINE)
    for kind, payload, *_ in spans:
        if kind == MATH:
            _drawing, _w, h, a = payload
            ascent, descent = max(ascent, a), max(descent, h - a)
    return ascent, ascent + descent


def _layout(md: str, box: dict, surface_width: float, resolve_ref=None):
    """The laid-out rows and the natural outer size, in points from the
    box's top-left: ``("text", x, baseline, spans, size)`` or ``("math",
    width, top, drawing, scale)``. A fixed box wraps inside its width; an
    auto box only at the surface's right edge, as on screen. The padding is
    ``pad_of``'s; a Zotero box gives each blank line a line's height."""
    from . import vector_text
    from .note_markup import TEXT, latex_spans
    from .pdf_typeset import PLAIN, plain, resolve, span_width, spans_width, wrap

    size, pad = box["size"], pad_of(box)
    room = (max(box["w"], surface_width - box["x"]) if box["auto"] else box["w"]) - 2 * pad
    rows, y, widest = [], pad, 0.0
    for p in _paragraphs(md, resolve_ref, gaps=box.get("zotero") is True):
        if p[0] == "gap":
            y += size * LINE
            continue
        if p[0] == "math":
            drawn = vector_text.math(p[1], size * _DISPLAY_MATH_SCALE)
            if drawn:
                drawing, w, h, _ascent = drawn
                scale = min(1.0, room / w) if w else 1.0
                rows.append(("math", w * scale, y, drawing, scale))
                y += h * scale
                widest = max(widest, w * scale)
                continue
            p = ("text", plain(latex_spans(p[1])), 1, 0, "")  # no ziamath, or it choked
        _kind, spans, scale, level, marker = p
        fs, indent = size * scale, level * _NEST * size
        if marker and not level:  # "[ ] x" outside a list reads as typed
            spans = [(TEXT, marker, 0, PLAIN)] + spans
        # Each list level's text is indented 1.5 em, its lines under one
        # another; the marker hangs in the indent, ending where the text
        # starts, as CSS draws an outside list marker (a to-do's box too).
        for n, (offset, line) in enumerate(wrap(resolve(spans, fs, room - indent), room - indent, fs, hang=0)):
            ascent, height = _line_metrics(line, fs)
            if level and marker and not n:
                rows.append(("text", pad + indent - span_width(marker, fs), y + ascent, [(TEXT, marker, 0, PLAIN)], fs))
            rows.append(("text", pad + indent + offset, y + ascent, line, fs))
            y += height
            widest = max(widest, indent + offset + spans_width(line, fs))
    return rows, widest + 2 * pad, y + pad


def measure(md: str, box: dict, surface_width: float) -> tuple[float, float]:
    """The outer size the box's text needs, for a box made on the server:
    its text's own size for a Zotero box, which has no padding. The lines
    are ``LINE`` high, as in the PDF; a rect made for Zotero's screen allows
    ``ZOTERO_LINE`` a line (``zotero_frame``)."""
    _rows, width, height = _layout(md, box, surface_width)
    return width, height


def pdf_ops(md: str, box: dict, surface_width: float, glyphs, fonts: set, resolve_ref=None):
    """A (normalized) box drawn for a PDF in a y-down frame whose origin is
    the box's outer top-left: ``(ops, width, height)``. The size is the
    box's own, grown when the text needs more (an auto box widens to its
    text, and any box grows downward), since the PDF's metrics are not the
    screen's. The background comes first, then the text in the box's colour.
    ``glyphs`` is the document's ``pdf_glyphs.GlyphFonts``, which draws
    math and CJK; ``fonts`` collects the base-14 fonts used; ``resolve_ref``
    reads references (``_paragraphs``)."""
    from .pdf_export import parse_css_color
    from .pdf_typeset import draw_spans, fill_rect, num

    rows, natural_w, natural_h = _layout(md, box, surface_width, resolve_ref)
    width = max(box["w"], natural_w) if box["auto"] else box["w"]
    height = max(box["h"], natural_h)
    ops = []
    if box["bg"]:
        fill_rect(ops, 0, 0, width, height, parse_css_color(box["bg"])[:3])
    color = parse_css_color(box["color"])[:3]
    for kind, a, y, payload, size in rows:
        if kind == "math":  # display math, centred as KaTeX centres it
            pad = pad_of(box)
            x = pad + max(0.0, (width - 2 * pad - a) / 2)
            ops += [b"q %s 0 0 %s %s %s cm" % (num(size), num(size), num(x), num(y)),
                    glyphs.draw(payload, color), b"Q"]
        else:
            draw_spans(ops, a, y, payload, size, color=color, fonts=fonts, glyphs=glyphs)
    return b"\n".join(ops), width, height


def pdf_resources(fonts: set, glyphs):
    """The /Resources of a stream drawn by ``pdf_ops``: the base-14 fonts
    it used and the document's Type 3 fonts."""
    from PyPDF2.generic import DictionaryObject, NameObject

    from .pdf_typeset import font_resources

    font_dict = font_resources(sorted(fonts), glyphs.writer)
    font_dict.update({NameObject("/" + name): ref for name, ref in glyphs.resources().items()})
    return DictionaryObject({NameObject("/Font"): font_dict})
