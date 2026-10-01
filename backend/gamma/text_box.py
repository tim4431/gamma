"""Text boxes: typed text placed on a PDF page or a sheet of paper
(docs/dev/text_boxes.md).

A text box is a block whose ``content`` is its text, Markdown like any
note, so search, the text merge, exports and the AI tools read it as one.
One property places it: ``text_box: {x, y, w, h, auto, size, color, bg}``
in points on its surface. A box under a sheet is on the nearest sheet
above it (gamma/notebook.py), points from its top-left; any other box with
``pdf_page`` (made at the page's top level, like an ink group) is on that
PDF page, in the frame ink files use (the pdf.js viewport at scale 1,
rotation applied, origin top-left, y down).
``x``, ``y`` is the outer top-left corner and ``w``, ``h`` the outer size,
padding included; ``h`` is as the client last measured it. ``auto`` makes
the width follow the text; otherwise the text wraps inside ``w``. ``size``
is the font size, ``color`` the text colour, ``bg`` the fill or None.
Stored values are read through ``normalize_text_box``, never trusted.
frontend/src/markup/textBox.js is the twin of the rules here;
tests/shared/textbox.json pins both. The second half lays a box out for
the PDF writers.
"""

import math
import re

PAD = 4  # inner padding on every side, in points
LINE = 1.25  # line height, as a multiple of the font size
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
    unknown keys dropped. ``h`` defaults to one line of the box's size."""
    if not isinstance(value, dict):
        return None
    size = _num(value.get("size"), MIN_SIZE, MAX_SIZE, DEFAULT_SIZE)
    auto = value.get("auto")
    return {
        "x": _num(value.get("x"), 0, MAX_COORD, 0),
        "y": _num(value.get("y"), 0, MAX_COORD, 0),
        "w": _num(value.get("w"), MIN_WIDTH, MAX_COORD, DEFAULT_WIDTH),
        "h": _num(value.get("h"), 0, MAX_COORD, _round(size * LINE + 2 * PAD)),
        "auto": auto if isinstance(auto, bool) else True,
        "size": size,
        "color": _color(value.get("color"), DEFAULT_COLOR),
        "bg": _color(value.get("bg"), None),
    }


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
    return {**now, **{k: v for k, v in ours.items() if v != was[k]}}


# --- the box in a PDF -----------------------------------------------------------------
# Both PDF writers draw a box the same way: the annotated export as a
# /FreeText's appearance (gamma/pdf_export.py), the notebook export as page
# content (gamma/notebook.py). The layout follows the box's read mode on
# screen (frontend/src/markup/markup.css), rendered Markdown with compact
# styles in the box's size and colour: no paragraph margins, one line per
# source line, headings a little larger, lists as CSS draws them.

# Where CSS puts the baseline in a line LINE high of Arial or Liberation Sans,
# the fonts the screen stack resolves Helvetica to: the leading beyond their
# 0.905 em ascent and 0.212 em descent is split above and below.
_BASELINE = (LINE - 0.905 - 0.212) / 2 + 0.905
_HEADING_SCALE = {1: 1.45, 2: 1.25, 3: 1.1}  # rendered notes' headings (app.css)
_DISPLAY_MATH_SCALE = 1.21  # KaTeX sets math at 1.21 em
_NEST = 1.5  # em a list item's text is indented per level


def _paragraphs(md: str, resolve_ref=None) -> list:
    """The box's Markdown as drawable paragraphs, in order: ``("math", tex)``
    for display math, else ``("text", spans, scale, level, marker)``, the
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
        # blank lines and rules: the compact styles give them no height
    return out


def plain_text(md: str, resolve_ref=None) -> str:
    """The box's text as a reader sees it, Markdown marks dropped (a
    /FreeText's /Contents): a line per paragraph, math as its TeX source,
    references as ``_paragraphs`` reads them."""
    from .note_markup import TEXT

    lines = []
    for p in _paragraphs(md, resolve_ref):
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
    auto box only at the surface's right edge, as on screen."""
    from . import vector_text
    from .note_markup import TEXT, latex_spans
    from .pdf_typeset import PLAIN, plain, resolve, span_width, spans_width, wrap

    size = box["size"]
    room = (max(box["w"], surface_width - box["x"]) if box["auto"] else box["w"]) - 2 * PAD
    rows, y, widest = [], PAD, 0.0
    for p in _paragraphs(md, resolve_ref):
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
                rows.append(("text", PAD + indent - span_width(marker, fs), y + ascent, [(TEXT, marker, 0, PLAIN)], fs))
            rows.append(("text", PAD + indent + offset, y + ascent, line, fs))
            y += height
            widest = max(widest, indent + offset + spans_width(line, fs))
    return rows, widest + 2 * PAD, y + PAD


def measure(md: str, box: dict, surface_width: float) -> tuple[float, float]:
    """The outer size the box's text needs, for a box made on the server."""
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
            x = PAD + max(0.0, (width - 2 * PAD - a) / 2)
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

    font_dict = font_resources(sorted(fonts))
    font_dict.update({NameObject("/" + name): ref for name, ref in glyphs.resources().items()})
    return DictionaryObject({NameObject("/Font"): font_dict})
