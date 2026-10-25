"""Where a Notability note lands in Gamma, as pure functions of the ``Note``
model (gamma/notability.py): the pages' frames, which page each item is on,
the paper, the strokes as gamma-ink v2 dicts, the text boxes and the typed
text as notes (docs/dev/import_export.md "Notability notes"; the geometry's
sources: docs/research/notability-format.md).

A note is one continuous canvas in document units, its pages stacked with
no gap. A ``Frame`` maps one page's slot of it onto a Gamma surface: a PDF
page of the note's PDF, or a sheet of paper. Nothing here stores, decodes
or writes: the import (gamma/notability_import.py) makes a page's strokes
when it writes that page, so only one page is ever held as samples.
"""

import math
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field, replace

from . import ink as inkmod
from .markdown_import import MAX_BLOCKS
from .notability import (COORD_LIMIT, IDENTITY, MAX_NOTE_PAGES, STYLE_HIGHLIGHTER, STYLE_PENCIL, Curve, Media, Note,
                         Run, Shape, curve_points)
from .notebook import normalize_paper
from .ops import MAX_CONTENT
from .text_box import LINE, MAX_SIZE, MIN_SIZE, PAD, escape_markdown, keep_on_surface, markdown_of, normalize_text_box

INSET = 20 / 768                 # the paper's left edge is at x = −W × INSET
LETTER_PT, A4_PT = 612, 595.28   # a paper page's width in points
TOLERANCE = 0.05                 # pt: the flattened polyline stays this close to the curve
STEP_PT = 1.0                    # pt: the most two consecutive samples of a curve lie apart
MAX_PIECES = 1024                # pieces per Bézier segment
DECIMATE_PT = 0.1                # finishStroke.js DECIMATE_PT
MAX_STROKE_SAMPLES = 50_000
MAX_FORCE = 25 / 6               # UIKit's maximumPossibleForce for the Apple Pencil: raw force → p
STICKY_EDGE = 0.5                # pt: a sticky's outline, in its own colour
LINE_SEPARATOR = "\u2028"         # a line break inside one paragraph of the typed text (a note of several lines)
CREAM = "#fbf7ec"                # the paper menu's cream (notebook.js PAPER_COLORS), Notability's 2014 paper
GRID_PT = 18                     # a legacy small grid's spacing
TEXT_COLOR = "#1f1f1f"
MATH_SIZE = 12
SQUARE = math.sin(math.radians(90 * 0.02))  # |cos| of a corner within 2 % of a right angle
SIDES = 0.02                     # opposite sides within 2 %
_ALTITUDE_UNKNOWN = math.pi / 2  # the app's fill for "no tilt", and (1, 0) for the azimuth
_PAGE_RATIO = 803.25 / 612       # Notability's own export page, and every legacy thumbnail's shape


@dataclass
class Frame:
    """One page of the note: where it lies in document units, and how a
    point there maps onto the Gamma page: X = (x − ox) × k and
    Y = (y − oy) × k, in points, y down."""
    top: float                   # the page's slot, in document units
    bottom: float
    ox: float
    oy: float
    k: float
    width: float                 # the page in points as shown (a PDF page: its display size)
    height: float
    pdf: str | None = None       # the bundled PDF's file name; None for paper
    pdf_page: int | None = None  # the 1-based page in that file


@dataclass
class PagePlan:
    frame: Frame
    curves: list = field(default_factory=list)    # indices into note.curves, in drawing order
    shapes: list = field(default_factory=list)    # indices into note.shapes, in drawing order
    stickies: list = field(default_factory=list)  # indices into note.media, by z
    boxes: list = field(default_factory=list)     # indices into note.media (text, math, pictures), by z


@dataclass
class NotePlan:
    pages: list                  # [PagePlan]
    kind: str                    # "notebook" | "note" | "original" | "composed"
    pdf: str | None              # for "original": the file's name
    paper: dict                  # the normalized paper of the paper pages
    flow: list                   # flow_blocks(note.flow), or Gamma's copy of the notes (copied_flow)
    warnings: list = field(default_factory=list)
    left_out: Counter = field(default_factory=Counter)
    papers: list | None = None   # a notebook's paper per page, from Gamma's copy (copied_papers); None: ``paper``


# --- pages ------------------------------------------------------------------------------

def paper_page(note: Note) -> tuple[float, float, float]:
    """``(page width in pt, page height in pt, H)`` of a paper page, H its
    height in document units: 612 pt wide (A4 595.28), as tall as the note
    says (an .ntb's ``page_height``) or else as its size asks — A4
    841.89/595.28, legal 14/8.5, a static-width letter page 11/8.5 with H
    floored to a whole unit (as gnnote and nokcha0 read it), anything else
    803.25/612 — and landscape the inverse ratio."""
    spec, width = note.paper, note.width
    pw = A4_PT if spec.size == "a4" else LETTER_PT
    static = spec.size == "letter" and spec.sizing == "staticWidth"
    ratio = (841.89 / 595.28 if spec.size == "a4" else 14 / 8.5 if spec.size == "legal"
             else 11 / 8.5 if static else _PAGE_RATIO)
    if spec.landscape:
        ratio = 1 / ratio
    height = note.page_height or (math.floor(width * ratio) if static else width * ratio)
    return pw, height * pw / width, height


def paper(note: Note) -> dict:
    """The note's paper as a sheet's (normalized): its page size; its colour
    (the identifier's, else cream for legacy paper 0, the tinted paper of
    2014, else white); ruled (or an .ntb's grid or dots) every ``lines``
    inches, a legacy small grid every 18 pt, else blank. The ruling's
    colour stays the default: the note does not store one."""
    pw, ph, _height = paper_page(note)
    spec = note.paper
    raw = {"width": pw, "height": ph, "color": spec.color or (CREAM if spec.legacy == 0 else "#ffffff"),
           "pattern": "blank"}
    if spec.lines:
        raw.update(pattern=spec.ruling, spacing=spec.lines * 72 * pw / LETTER_PT)
    elif spec.grid:
        raw.update(pattern="grid", spacing=GRID_PT)
    return normalize_paper(raw)


def _index(tops: list[float], y: float) -> int:
    """The page whose slot holds ``y``, by the pages' tops: above the
    first page → 0, below the last → the last."""
    return min(max(bisect_right(tops, y) - 1, 0), len(tops) - 1)


def frames(note: Note, pdf_pages: dict, centres: list[float]) -> tuple[list[Frame], list[str]]:
    """The note's pages as frames, and the warnings. ``pdf_pages`` holds
    the display sizes of each readable bundled PDF's pages; ``centres`` the
    vertical centre of every item drawn or placed. The layout's entries
    come first (a PDF entry whose file or page is not readable becomes
    paper, with a warning), then paper pages while an item lies below the
    last page; trailing paper pages with nothing on them are dropped, as
    Notability's own export drops them, and one page is always kept.

    A PDF page is scaled to the page width W and bottom-aligned in a slot
    of ``ceil(h)`` units (PDFFile version 2). A version 1 page (8.x and
    earlier) is drawn 8.5 in wide, scaled down when wider and centred."""
    width = note.width
    inset = width * INSET
    pw, _ph, height = paper_page(note)
    sheet = paper(note)
    out: list[Frame] = []
    warnings, warned = [], set()
    top = 0.0

    def add_paper():
        nonlocal top
        out.append(Frame(top, top + height, -inset, top, pw / width, sheet["width"], sheet["height"]))
        top += height

    def warn(message):
        if message not in warned:
            warned.add(message)
            warnings.append(message)

    for entry in note.layout:
        if len(out) >= MAX_NOTE_PAGES:
            break
        if entry.pdf is None:
            add_paper()
            continue
        sizes = pdf_pages.get(entry.pdf)
        if sizes is None:
            warn(f"PDF {entry.pdf} could not be read: its pages are blank paper" if entry.pdf in note.pdfs
                 else f"Missing PDF {entry.pdf}: its pages are blank paper")
            add_paper()
            continue
        if not 1 <= entry.page <= len(sizes):
            warn(f"{entry.pdf} has no page {entry.page}: blank paper")
            add_paper()
            continue
        dw, dh = sizes[entry.page - 1]
        pdf = note.pdfs.get(entry.pdf)
        if pdf is None or pdf.version >= 2:
            h, ox, k = dh * width / dw, -inset, dw / width
        else:
            s1 = min(1.0, LETTER_PT / dw)
            h = dh * s1 * width / LETTER_PT
            ox, k = -inset + width * (1 - dw * s1 / LETTER_PT) / 2, LETTER_PT / (s1 * width)
        slot = max(1, math.ceil(h - 1e-6))
        out.append(Frame(top, top + slot, ox, top + (slot - h), k, dw, dh, entry.pdf, entry.page))
        top += slot
    lowest = max(centres, default=None)
    while (not out or (lowest is not None and lowest >= top)) and len(out) < MAX_NOTE_PAGES:
        add_paper()
    tops = [f.top for f in out]
    used = {_index(tops, c) for c in centres}
    while len(out) > 1 and out[-1].pdf is None and len(out) - 1 not in used:
        out.pop()
    return out, warnings


def _curve_span(curve: Curve) -> tuple[float, float] | None:
    """The curve's top and bottom in document units, or None when it cannot
    be read: a point, width, force or tilt that is not finite, a coordinate
    that is not finite or lies past COORD_LIMIT once its groups moved it, a
    base width that is not positive or a fractional width below 0. Read off
    the arrays (C loops over slices, no per-point objects) unless a group
    turns the curve."""
    ink = curve.ink
    if not (math.isfinite(curve.base) and curve.base > 0):
        return None
    pts = ink.points[2 * curve.p:2 * (curve.p + curve.n)]
    xs, ys = pts[0::2], pts[1::2]
    if not (math.isfinite(sum(xs)) and math.isfinite(sum(ys))):
        return None
    anchors = curve.anchors
    for values, per in ((ink.fractional, 1), (ink.force, 1), (ink.altitude, 1), (ink.azimuth, 2)):
        if values is not None:
            part = values[per * curve.f:per * (curve.f + anchors)]
            if not math.isfinite(sum(part)) or (values is ink.fractional and part and min(part) < 0):
                return None
    a, b, c, d, tx, ty = ink.transform
    if b == 0 and c == 0:  # no turn: the bounds follow from the extremes
        x0, x1 = sorted((a * min(xs) + tx, a * max(xs) + tx))
        y0, y1 = sorted((d * min(ys) + ty, d * max(ys) + ty))
    else:
        moved = curve_points(curve)
        x0, x1 = min(x for x, _ in moved), max(x for x, _ in moved)
        y0, y1 = min(y for _, y in moved), max(y for _, y in moved)
    if not all(math.isfinite(v) and abs(v) <= COORD_LIMIT for v in (x0, x1, y0, y1)):
        return None  # NaN too: transforms composed past a float's range
    return y0, y1


def _placed(media: Media) -> bool:
    """Whether a medium makes anything: a picture with its image, text or
    math with something in it, any sticky."""
    if media.kind == "picture":
        return media.picture is not None
    if media.kind == "math":
        return bool(media.latex.strip())
    if media.kind == "text":
        return any(r.text.strip() for r in media.runs) or bool(media.background and media.background[3] > 0)
    return True


def plan_note(note: Note, pdf_pages: dict) -> NotePlan:
    """The note's pages, each with the indices of what lies on it (an item
    is on the page holding the vertical centre of its bounding box; above
    page 1 counts as page 1), and what the page becomes:

    - ``notebook``: no PDF page, and something drawn or placed: a sheet a page;
    - ``note``: no PDF page and nothing on the pages: the typed text alone;
    - ``original``: the pages are exactly one bundled PDF's, 1..n in order;
    - ``composed``: any other note with a PDF page, whose PDF is made of its
      pages in order."""
    left_out = Counter()
    items = []
    for i, curve in enumerate(note.curves):
        span = _curve_span(curve)
        if span is None:
            left_out["stroke"] += 1
        else:
            items.append(((span[0] + span[1]) / 2, "curve", i))
    for i, shape in enumerate(note.shapes):
        ys = [y for _x, y in shape.points] + ([shape.control[1]] if shape.control else [])
        items.append(((min(ys) + max(ys)) / 2, "shape", i))
    for i, media in enumerate(note.media):
        if _placed(media):
            items.append((media.y + media.h / 2, "media", i))
    papers = copied_papers(note) if not any(e.pdf for e in note.layout) else None
    sheets = [(n + 0.5) * paper_page(note)[2] for n in range(len(papers))] if papers else []  # each sheet kept
    pages_, warnings = frames(note, pdf_pages, [centre for centre, _kind, _i in items] + sheets)
    if papers and len(pages_) != len(papers):  # the note has pages the copy does not know
        papers = None
    tops = [f.top for f in pages_]
    pages = [PagePlan(f) for f in pages_]
    capped = len(pages_) >= MAX_NOTE_PAGES
    for centre, kind, i in items:
        if capped and centre >= pages_[-1].bottom:
            left_out["page"] += 1
            continue
        page = pages[_index(tops, centre)]
        if kind == "curve":
            page.curves.append(i)
        elif kind == "shape":
            page.shapes.append(i)
        elif note.media[i].kind == "sticky":
            page.stickies.append(i)
        else:
            page.boxes.append(i)
    names = {f.pdf for f in pages_}
    kind, pdf = "notebook", None
    if names == {None}:
        if not papers and not any(p.curves or p.shapes or p.stickies or p.boxes for p in pages):
            kind = "note"
    else:
        name = next(iter(names))
        if (len(names) == 1 and [f.pdf_page for f in pages_] == list(range(1, len(pages_) + 1))
                and len(pages_) == len(pdf_pages.get(name, ()))):
            kind, pdf = "original", name
        else:
            kind = "composed"
    if note.paper.template and any(f.pdf is None for f in pages_) and kind != "note":
        warnings.append("The paper template is not imported: plain paper instead")
    flow = copied_flow(note)
    if flow is None:
        flow = flow_blocks(note.flow, left_out)
    return NotePlan(pages, kind, pdf, paper(note), flow, warnings, left_out, papers)


# --- ink ----------------------------------------------------------------------------------

def _affine(transform: tuple, frame: Frame) -> tuple:
    """The document transform and the frame's mapping as one affine map
    into page points: (A, B, C, D, E, F), X = A·x + C·y + E, Y = B·x + D·y + F."""
    a, b, c, d, tx, ty = transform
    k = frame.k
    return a * k, b * k, c * k, d * k, (tx - frame.ox) * k, (ty - frame.oy) * k


def _pieces(p0, p1, p2, p3) -> int:
    """How many equal parameter steps a cubic is cut into, the larger of
    two bounds, at most MAX_PIECES: Wang's, so the polyline stays within
    TOLERANCE of the curve, and one that keeps every step within STEP_PT —
    the curve's speed is at most three times its control polygon's longest
    leg (its derivative is a quadratic Bézier on three times the legs)."""
    m = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
            math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
    leg = max(math.dist(p0, p1), math.dist(p1, p2), math.dist(p2, p3))
    wang = math.ceil(math.sqrt(0.75 * m / TOLERANCE)) if m > 0 else 1
    return min(MAX_PIECES, max(1, wang, math.ceil(3 * leg / STEP_PT)))


def _cubic(p0, p1, p2, p3, n: int | None = None):
    """The cubic's points at t = i/n, i = 1..n (n: ``_pieces``), each with its t."""
    n = n or _pieces(p0, p1, p2, p3)
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        w0, w1, w2, w3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        yield (w0 * p0[0] + w1 * p1[0] + w2 * p2[0] + w3 * p3[0],
               w0 * p0[1] + w1 * p1[1] + w2 * p2[1] + w3 * p3[1], t)


def _edge(p0, p1, n: int):
    """A straight edge's ``n`` points after ``p0``, evenly apart, each with its t."""
    for i in range(1, n + 1):
        t = i / n
        yield p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t, t


def _steps(curve: Curve, pts: list) -> list[int]:
    """How many samples each segment of the curve is cut into: a Bézier
    chain's segments by ``_pieces``, a polyline's edges one every STEP_PT."""
    if curve.bezier:
        return [_pieces(*pts[3 * s:3 * s + 4]) for s in range(len(pts) // 3)]
    return [max(1, math.ceil(math.dist(pts[s], pts[s + 1]) / STEP_PT)) for s in range(len(pts) - 1)]


def _trace(curve: Curve, pts: list, steps: list[int]):
    """The curve's samples as ``(x, y, anchor, t)``, each segment cut into
    its ``steps``, a one-point curve one sample; ``anchor`` is the
    segment's first anchor and ``t`` the place between it and the next."""
    yield pts[0][0], pts[0][1], 0, 0.0
    for s, n in enumerate(steps):
        segment = _cubic(*pts[3 * s:3 * s + 4], n=n) if curve.bezier else _edge(pts[s], pts[s + 1], n)
        for x, y, t in segment:
            yield x, y, s, t


def flatten(curve: Curve, frame: Frame) -> list[dict] | None:
    """The curve as samples in the frame's page points, at most STEP_PT
    apart and within TOLERANCE of the curve — the client's own input comes
    about a point apart, and its pen outline (perfect-freehand at
    streamline 0.4) cuts the corners of sparser samples — then decimated
    as ``finishStroke.js decimate`` does (a sample closer than DECIMATE_PT
    to the previous kept one is dropped, so a gap may grow by that much;
    the first and the last stay).
    Each sample carries ``w`` (the fractional widths of its segment's
    anchors, linear in t), ``p`` (force ÷ MAX_FORCE) and ``a`` (the
    altitude in degrees), both linear in t, and ``z`` (the azimuth in whole
    degrees, from the segment's first anchor, its last at t = 1), when the
    note has them. None when the curve takes more than MAX_STROKE_SAMPLES
    samples before decimation, counted before one is made."""
    ink = curve.ink
    A, B, C, D, E, F = _affine(ink.transform, frame)
    raw = ink.points[2 * curve.p:2 * (curve.p + curve.n)]
    pts = [(A * x + C * y + E, B * x + D * y + F) for x, y in zip(raw[0::2], raw[1::2])]
    steps = _steps(curve, pts)
    if 1 + sum(steps) > MAX_STROKE_SAMPLES:
        return None
    anchors, f = curve.anchors, curve.f
    fw = ink.fractional[f:f + anchors] if ink.fractional is not None else None
    force = ink.force[f:f + anchors] if ink.force is not None else None
    alt = ink.altitude[f:f + anchors] if ink.altitude is not None else None
    azi = ink.azimuth[2 * f:2 * (f + anchors)] if ink.azimuth is not None else None
    last = anchors - 1

    def sample(x, y, j, t):
        nxt = min(j + 1, last)
        out = {"x": x, "y": y}
        if fw is not None:
            out["w"] = fw[j] + (fw[nxt] - fw[j]) * t
        if force is not None:
            out["p"] = min(1.0, max(0.0, (force[j] + (force[nxt] - force[j]) * t) / MAX_FORCE))
        if alt is not None:
            out["a"] = min(90.0, max(0.0, math.degrees(alt[j] + (alt[nxt] - alt[j]) * t)))
        if azi is not None:
            at = nxt if t >= 1 else j
            out["z"] = round(math.degrees(math.atan2(azi[2 * at + 1], azi[2 * at]))) % 360
        return out

    kept: list[tuple] = []
    pending = None
    for s in _trace(curve, pts, steps):
        if pending is not None and kept:
            if math.hypot(pending[0] - kept[-1][0], pending[1] - kept[-1][1]) >= DECIMATE_PT:
                kept.append(pending)
        elif pending is not None:
            kept.append(pending)
        pending = s
    if len(kept) > 1 and math.hypot(pending[0] - kept[-1][0], pending[1] - kept[-1][1]) < DECIMATE_PT:
        kept[-1] = pending
    else:
        kept.append(pending)
    return [sample(*s) for s in kept]


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(min(255, max(0, round(v * 255))) for v in rgb[:3])


def _checked(stroke: dict) -> dict | None:
    """The stroke when ``ink.Stroke`` takes it (with a placeholder id)."""
    try:
        inkmod.Stroke.model_validate({**stroke, "id": "n0"})
    except ValueError:
        return None
    return stroke


def curve_stroke(curve: Curve, frame: Frame) -> dict | None:
    """One curve as a gamma-ink v2 stroke dict without an ``id``, or None
    when it cannot be one. A pen or pencil whose widths vary along it
    keeps them as ``w`` (with ``p`` and ``a``/``z`` when the note has real
    force and tilt, in the client's channel order) at ``size`` base × k; a
    stroke of constant width is an even one (``pen: false``); a
    highlighter is the highlighter tool at its width. Widths past what a
    stroke may hold are scaled into it (w ≤ 4, size × max(w) ≤ 100). A
    curve Gamma's export wrote is read as it was written
    (``exact_stroke``)."""
    if curve.gamma:
        return exact_stroke(curve, frame)
    samples = flatten(curve, frame)
    if samples is None:
        return None
    ink, k = curve.ink, frame.k
    anchors, f = curve.anchors, curve.f
    fw = ink.fractional[f:f + anchors] if ink.fractional is not None else [1.0] * anchors
    r, g, b, alpha = curve.rgba
    stroke = {"color": "#%02x%02x%02x" % (r, g, b)}
    if alpha < 255:
        stroke["opacity"] = max(0.001, round(alpha / 255, 3))
    ch = "xy"
    if curve.style == STYLE_HIGHLIGHTER:
        stroke["tool"] = "highlighter"
        size = curve.base * fw[0] * k
    else:
        if curve.style == STYLE_PENCIL:
            stroke["brush"] = "pencil"
        if any(abs(w - fw[0]) > 0.001 for w in fw):
            size = curve.base * k
            force = ink.force[f:f + anchors] if ink.force is not None else ()
            alt = ink.altitude[f:f + anchors] if ink.altitude is not None else ()
            azi = ink.azimuth[2 * f:2 * (f + anchors)] if ink.azimuth is not None else ()
            if any(v != 1.0 for v in force):
                ch += "p"
            if (any(abs(v - _ALTITUDE_UNKNOWN) > 1e-6 for v in alt)
                    or any(abs(azi[i] - 1) > 1e-6 or abs(azi[i + 1]) > 1e-6 for i in range(0, len(azi), 2))):
                ch += "az"
            ch += "w"
            peak = max(s["w"] for s in samples)
            if peak > 4:
                size *= peak / 4
                for s in samples:
                    s["w"] *= 4 / peak
                peak = 4.0
            size = min(100.0, max(0.1, size))  # the size stored, which the cut must use
            if size * peak > 100:
                cut = 100 / (size * peak) * 0.999
                for s in samples:
                    s["w"] *= cut
        else:
            size = curve.base * fw[0] * k
            stroke["pen"] = False
    if curve.dash:
        stroke["dash"] = curve.dash
    stroke["size"] = round(min(100.0, max(0.1, size)), 3)
    stroke["ch"] = ch
    stroke["pts"] = inkmod.encode_points(samples, ch)
    return _checked(stroke)


def _opacity(alpha: int) -> float:
    """An alpha byte as the opacity of fewest decimals that writes it back
    (gamma/notability_export.py writes ``round(opacity × 255)``): a preset's
    two decimals, else three."""
    two = round(alpha / 255, 2)
    return two if inkmod.js_round(two * 255) == alpha else round(alpha / 255, 3)


def exact_stroke(curve: Curve, frame: Frame) -> dict | None:
    """A curve Gamma's export wrote (``Curve.gamma``) as the stroke it was:
    its anchors are its samples, read as they are (no flattening, no
    decimation), each with its anchor's width ``w``, force ``p`` and tilt
    ``a``/``z`` when any anchor holds more than Notability's unknown; a
    stroke with ``p`` was drawn with a stylus (``pen``); its size is
    curveswidth × k. None past MAX_STROKE_SAMPLES samples, or when
    ``ink.Stroke`` refuses it."""
    ink = curve.ink
    anchors, f = curve.anchors, curve.f
    if anchors > MAX_STROKE_SAMPLES:
        return None
    A, B, C, D, E, F = _affine(ink.transform, frame)
    raw = ink.points[2 * curve.p:2 * (curve.p + curve.n)]
    step = 6 if curve.bezier else 2  # an anchor, then two controls
    samples = [{"x": A * x + C * y + E, "y": B * x + D * y + F} for x, y in zip(raw[0::step], raw[1::step])]
    fw = ink.fractional[f:f + anchors] if ink.fractional is not None else None
    force = ink.force[f:f + anchors] if ink.force is not None else None
    alt = ink.altitude[f:f + anchors] if ink.altitude is not None else None
    azi = ink.azimuth[2 * f:2 * (f + anchors)] if ink.azimuth is not None else None
    if not all(math.isfinite(v) for s in samples for v in (s["x"], s["y"])):
        return None
    ch = "xy"
    if force is not None and any(v != 1.0 for v in force):
        ch += "p"
        for s, v in zip(samples, force):
            s["p"] = min(1.0, max(0.0, v / MAX_FORCE))
    if (alt is not None and azi is not None
            and (any(abs(v - _ALTITUDE_UNKNOWN) > 1e-6 for v in alt)
                 or any(abs(azi[i] - 1) > 1e-6 or abs(azi[i + 1]) > 1e-6 for i in range(0, len(azi), 2)))):
        ch += "az"
        for i, s in enumerate(samples):
            s["a"] = min(90.0, max(0.0, math.degrees(alt[i])))
            s["z"] = round(math.degrees(math.atan2(azi[2 * i + 1], azi[2 * i]))) % 360
    size = min(100.0, max(0.1, curve.base * frame.k))
    if fw is not None and any(v != 1.0 for v in fw):
        ch += "w"
        peak, cut = max(fw), 1.0  # a width the app scaled past what a stroke holds is scaled into it
        if peak > 4:
            cut = 4 / peak
        if size * peak * cut > 100:
            cut = 100 / (size * peak) * 0.999
        for s, v in zip(samples, fw):
            s["w"] = v * cut
    r, g, b, alpha = curve.rgba
    stroke = {"color": "#%02x%02x%02x" % (r, g, b)}
    if alpha < 255:
        stroke["opacity"] = max(0.001, _opacity(alpha))
    if curve.style == STYLE_HIGHLIGHTER:
        stroke["tool"] = "highlighter"
    elif curve.style == STYLE_PENCIL:
        stroke["brush"] = "pencil"
    if "p" not in ch:
        stroke["pen"] = False
    if curve.dash:
        stroke["dash"] = curve.dash
    stroke["size"] = round(size, 3)
    stroke["ch"] = ch
    stroke["pts"] = inkmod.encode_points(samples, ch)
    return _checked(stroke)


# --- shapes -------------------------------------------------------------------------------
# A shape with a record is drawn as shapes.js draws its params
# (``ink.shape_samples``): the stroke keeps those samples until an editor
# writes them again from the params after a handle drag. A plain outline
# takes the same step.

def normalise_rect(cx, cy, w, h, angle) -> dict:
    """shapes.js ``normaliseRect``: the angle into [−45, 45), w and h
    swapped on an odd quarter turn, so the side along the angle stays w."""
    a = (inkmod.round2(angle) + 45) % 90 - 45
    if inkmod.js_round((angle - a) / 90) % 2:
        w, h = h, w
    return {"cx": cx, "cy": cy, "w": w, "h": h, "angle": 0 if abs(a) < 1e-9 else a}


def normalise_ellipse(cx, cy, rx, ry, angle) -> dict:
    """shapes.js ``normaliseEllipse``: rx ≥ ry, the angle in [0, 180)."""
    if rx < ry:
        rx, ry, angle = ry, rx, angle + 90
    return {"cx": cx, "cy": cy, "rx": rx, "ry": ry, "angle": inkmod.round2(angle) % 180}


def _rounded(params: dict) -> dict:
    return {key: inkmod.round2(value) for key, value in params.items()}


def _ellipse_points(centre, u, v) -> list[tuple] | None:
    """A plain ellipse, ``centre + u cos t + v sin t`` (u, v its half-axes
    as vectors, which also draws the ellipse inscribed in a skewed
    rectangle), as many points as ``shape_samples`` gives an ellipse:
    max(SHAPE_ROUND_MIN, Ramanujan's perimeter ÷ SHAPE_STEP), even in t,
    closed. None past MAX_STROKE_SAMPLES, counted before one is made."""
    rx, ry = math.hypot(*u), math.hypot(*v)
    h = ((rx - ry) / (rx + ry)) ** 2 if rx + ry else 0
    perimeter = math.pi * (rx + ry) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))
    n = max(inkmod.SHAPE_ROUND_MIN, math.ceil(perimeter / inkmod.SHAPE_STEP))
    if n + 1 > MAX_STROKE_SAMPLES:
        return None
    return [(centre[0] + u[0] * math.cos(t) + v[0] * math.sin(t), centre[1] + u[1] * math.cos(t) + v[1] * math.sin(t))
            for t in (2 * math.pi * (i % n) / n for i in range(n + 1))]


def _square_corners(pts: list, equal_sides: bool) -> bool:
    """Whether four corners have right angles (and, with ``equal_sides``,
    equal opposite sides), each within 2 %."""
    if len(pts) != 4:
        return False
    sides = [math.dist(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    if min(sides) <= 0:
        return False
    for i in range(4):
        prev, here, nxt = pts[i - 1], pts[i], pts[(i + 1) % 4]
        u, v = (prev[0] - here[0], prev[1] - here[1]), (nxt[0] - here[0], nxt[1] - here[1])
        if abs(u[0] * v[0] + u[1] * v[1]) > SQUARE * math.hypot(*u) * math.hypot(*v):
            return False
    return not equal_sides or all(abs(sides[i] - sides[i + 2]) <= SIDES * max(sides[i], sides[i + 2]) for i in (0, 1))


def _path_corners(pts: list, segments: tuple) -> list | None:
    """A path's corners: the end of each line, and each cubic flattened as
    a curve is (``_cubic``); None past MAX_STROKE_SAMPLES, counted before
    one is made."""
    steps, at = [], 0
    for segment in segments:
        steps.append(_pieces(*pts[at:at + 4]) if segment == 3 else 1)
        at += segment
    if 1 + sum(steps) > MAX_STROKE_SAMPLES:
        return None
    out, at = [pts[0]], 0
    for segment, n in zip(segments, steps):
        out += [(x, y) for x, y, _t in _cubic(*pts[at:at + 4], n=n)] if segment == 3 else [pts[at + 1]]
        at += segment
    return out


def shape_stroke(shape: Shape, frame: Frame, left_out: Counter | None = None) -> dict | None:
    """One shape as an even stroke dict without an ``id``, a pencil's with
    the pencil brush. A line, an arrow, a rectangle, a triangle or an
    ellipse gets the ``shape`` record the client's recogniser would write
    for it (params in page points, rounded to hundredths, normalised as
    shapes.js does) and the samples shapes.js draws those params as
    (``ink.shape_samples``), so a handle drag rebuilds the stroke it is. A
    curved line, a polygon, a rectangle or ellipse whose corners are not
    square, every shape from an ink group and a piece of a partly erased
    shape (a path) are plain strokes of their outline: a straight line as
    one with a record is drawn, a polygon's corners and a path's with a
    sample at most SHAPE_STEP apart (``ink.densify``, a path's curves
    flattened as a curve is), an ellipse as many samples as a record's
    (``_ellipse_points``), a curved line flattened as a curve is, its head
    as an arrow's. A fill only a record can carry is dropped (counted in
    ``left_out``). None when its outline takes more than MAX_STROKE_SAMPLES
    samples."""
    r, g, b, alpha = shape.rgba
    if alpha <= 0:
        return None
    A, B, C, D, E, F = _affine(IDENTITY, frame)
    pts = [(A * x + E, D * y + F) for x, y in shape.points]
    stroke = {"color": _hex(shape.rgba), "pen": False}
    if alpha < 1:
        stroke["opacity"] = max(0.001, round(alpha, 3))
    if shape.style == STYLE_HIGHLIGHTER:
        stroke["tool"] = "highlighter"
    elif shape.style == STYLE_PENCIL:
        stroke["brush"] = "pencil"
    if shape.dash:
        stroke["dash"] = shape.dash
    record = None  # (kind, params), drawn by shape_samples
    kind, plain = shape.kind, shape.grouped
    if kind == "line" and shape.control is not None:
        p0, p1 = pts
        q = (A * shape.control[0] + E, D * shape.control[1] + F)
        c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
        c2 = (p1[0] + 2 / 3 * (q[0] - p1[0]), p1[1] + 2 / 3 * (q[1] - p1[1]))
        samples = [p0] + [(x, y) for x, y, _t in _cubic(p0, c1, c2, p1)]
        if shape.arrow:
            back = (q[0] - p1[0], q[1] - p1[1])
            length = math.hypot(*back) or 1
            head = min(math.dist(p0, p1) / 4, inkmod.ARROW_HEAD_MAX)
            samples += inkmod.densify([p1, *inkmod.arrow_barbs(p1, (back[0] / length, back[1] / length), head)],
                                      False)[1:]
    elif kind == "line":
        p0, p1 = pts
        record = ("arrow" if shape.arrow else "line", _rounded({"x1": p0[0], "y1": p0[1], "x2": p1[0], "y2": p1[1]}))
    elif kind in ("circle", "ellipse"):
        c0, c1, c2, c3 = pts
        centre = ((c0[0] + c1[0] + c2[0] + c3[0]) / 4, (c0[1] + c1[1] + c2[1] + c3[1]) / 4)
        if not plain and _square_corners(pts, False):
            record = ("ellipse", _rounded(normalise_ellipse(centre[0], centre[1], math.dist(c0, c1) / 2,
                                                            math.dist(c1, c2) / 2,
                                                            math.degrees(math.atan2(c1[1] - c0[1], c1[0] - c0[0])))))
        else:
            samples = _ellipse_points(centre, ((c1[0] - c0[0]) / 2, (c1[1] - c0[1]) / 2),
                                      ((c3[0] - c0[0]) / 2, (c3[1] - c0[1]) / 2))
    elif kind in ("rectangle", "square") and not plain and _square_corners(pts, True):
        c0, c1, c2, _c3 = pts
        record = ("rect", _rounded(normalise_rect(sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4,
                                                  math.dist(c0, c1), math.dist(c1, c2),
                                                  math.degrees(math.atan2(c1[1] - c0[1], c1[0] - c0[0])))))
    elif kind == "triangle" and not plain and len(pts) == 3:
        record = ("triangle", _rounded({"x1": pts[0][0], "y1": pts[0][1], "x2": pts[1][0], "y2": pts[1][1],
                                        "x3": pts[2][0], "y3": pts[2][1]}))
    elif kind == "path":
        corners = _path_corners(pts, shape.segments)
        samples = inkmod.densify(corners, False, MAX_STROKE_SAMPLES) if corners else None
    else:
        samples = inkmod.densify(pts, len(pts) > 2, MAX_STROKE_SAMPLES) if len(pts) > 1 else pts
    if record is not None:
        samples = inkmod.shape_samples(*record, MAX_STROKE_SAMPLES)
        if not plain:  # a straight line from an ink group is drawn as one, without a record
            stroke["shape"] = {"kind": record[0], "params": record[1]}
    if not samples:
        return None
    if shape.fill is not None and shape.fill[3] > 0:
        if stroke.get("shape", {}).get("kind") in ("rect", "triangle", "ellipse"):
            stroke["fill"] = {"color": _hex(shape.fill), "opacity": max(0.001, round(shape.fill[3], 3))}
        elif left_out is not None:
            left_out["polygon fill"] += 1
    stroke["size"] = round(min(100.0, max(0.1, shape.width * frame.k)), 3)
    stroke["ch"] = "xy"
    stroke["pts"] = inkmod.encode_points([{"x": x, "y": y} for x, y in samples], "xy")
    return _checked(stroke)


def sticky_stroke(media: Media, frame: Frame) -> dict | None:
    """A sticky note: the coloured sheet handwriting is written on, as a
    filled rectangle in band 0, drawn under the ink by every renderer and
    exporter and moved and resized by the selection like any shape. Its
    rectangle is turned about its centre as the sticky is. None when its
    outline takes more than MAX_STROKE_SAMPLES samples."""
    k = frame.k
    params = _rounded(normalise_rect((media.x + media.w / 2 - frame.ox) * k, (media.y + media.h / 2 - frame.oy) * k,
                                     media.w * k, media.h * k, math.degrees(media.rotation)))
    color = _hex(media.background or (1, 1, 1, 1))
    samples = inkmod.shape_samples("rect", params, MAX_STROKE_SAMPLES)
    if not samples:
        return None
    return _checked({"pen": False, "band": 0, "color": color, "size": STICKY_EDGE,
                     "shape": {"kind": "rect", "params": params}, "fill": {"color": color, "opacity": 1},
                     "ch": "xy", "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in samples], "xy")})


def page_strokes(note: Note, page: PagePlan, left_out: Counter, left: list | None = None):
    """The page's stroke dicts in the order they are drawn: the stickies
    (by z), then the curves and shapes in the note's drawing order
    (``Curve.z``, ``Shape.z``). A stroke that cannot be one is counted in
    ``left_out`` ("stroke"). ``left`` (``[n]``, shared by the note's pages)
    is what is left of the note's samples, which each stroke spends: the
    stroke that would go past it and every one after it are counted
    ("samples") and never made."""
    frame = page.frame
    drawn = sorted([(note.curves[i], curve_stroke) for i in page.curves]
                   + [(note.shapes[i], lambda shape, at: shape_stroke(shape, at, left_out)) for i in page.shapes],
                   key=lambda pair: pair[0].z)
    makers = [(sticky_stroke, note.media[i]) for i in page.stickies] + [(make, item) for item, make in drawn]
    private = note.private or {}
    copies = {Curve: _part(private, "strokes"), Shape: _part(private, "shapes")}
    for make, item in makers:
        if left is not None and left[0] <= 0:
            left_out["samples"] += 1
            continue
        stroke = make(item, frame)
        if stroke is None:
            left_out["stroke"] += 1
            continue
        key = item.gamma if isinstance(item, Curve) else getattr(item, "uuid", None)
        copy = copies.get(type(item), {}).get(key.hex()) if key else None
        if copy is not None:
            stroke = copied_stroke(copy, stroke, exact=isinstance(item, Curve)) or stroke
        if left is not None:
            n = len(stroke["pts"]) // len(stroke["ch"])
            if n > left[0]:
                left[0] = 0
                left_out["samples"] += 1
                continue
            left[0] -= n
        yield stroke


# --- Gamma's own copy ------------------------------------------------------------------------
# A note Gamma's export wrote carries Gamma's copy of what it holds
# (``Note.private``, gamma/notability_export.py), for what Notability's model
# has no place for: a stroke's time, a pencil's opacity, a brush or a shape
# Notability lacks, a box's Markdown, the notes as they were. Each part is
# used only where the note still holds what it describes, so once
# Notability has changed an item the note's own version is what comes in.

COPY_TOLERANCE = 0.05  # pt: how far a sample of the copy may lie from the note's (float32 coordinates)


def _part(private: dict, name: str) -> dict:
    value = private.get(name)
    return value if isinstance(value, dict) else {}


def _xy(stroke: dict) -> list[tuple[float, float]] | None:
    try:
        parsed = inkmod.Stroke.model_validate({**stroke, "id": "n0"})
    except ValueError:
        return None
    return [(s["x"], s["y"]) for s in inkmod.decode_stroke(parsed)]


def copied_stroke(copy, made: dict, *, exact: bool) -> dict | None:
    """Gamma's copy of a stroke, when it is still the stroke the note
    holds: one ``ink.Stroke`` takes, whose samples are the ones the note's
    curve gave (``exact``: as many, each within COPY_TOLERANCE), or for a
    shape whose samples span the same box; None otherwise."""
    if not isinstance(copy, dict):
        return None
    copy = {k: v for k, v in copy.items() if k not in ("id", "from")}
    theirs, mine = _xy(copy), _xy(made)
    if not theirs or not mine:
        return None
    if exact:
        if len(theirs) != len(mine) or any(abs(a - c) > COPY_TOLERANCE or abs(b - d) > COPY_TOLERANCE
                                           for (a, b), (c, d) in zip(theirs, mine)):
            return None
    else:
        box = lambda pts: (min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts),  # noqa: E731
                           max(y for _, y in pts))
        if any(abs(a - b) > COPY_TOLERANCE for a, b in zip(box(theirs), box(mine))):
            return None
    return copy


def copied_box(note: Note, media: Media, made: dict | None, frame: Frame) -> dict | None:
    """Gamma's copy of a text box or a picture's box (``{content,
    text_box}``, ``made`` the note's own), when the note still holds it:
    a box at the same place and width, a text box with the same text. A
    picture keeps ``made``'s content but for its alt text."""
    if made is None or media.uuid is None:
        return made
    parts = _part(note.private or {}, "pictures" if media.kind == "picture" else "boxes")
    copy = parts.get(media.uuid.hex())
    if not isinstance(copy, dict):
        return made
    box = normalize_text_box(copy.get("text_box"))
    ours = made["text_box"]
    if box is None or any(abs(box[k] - ours[k]) > COPY_TOLERANCE for k in ("x", "y", "w")):
        return made
    box = keep_on_surface(box, frame.width, frame.height)
    if media.kind == "picture":
        alt = copy.get("alt")
        if not isinstance(alt, str) or len(alt) > MAX_CONTENT or "]" in alt:
            return made
        return {"content": "![" + alt + made["content"][made["content"].index("]("):], "text_box": box}
    text, content = copy.get("text"), copy.get("content")
    if not isinstance(content, str) or len(content) > MAX_CONTENT or text != "".join(r.text for r in media.runs):
        return made
    return {"content": content, "text_box": box}


def copied_flow(note: Note) -> list | None:
    """Gamma's copy of the notes, when the note's typed text is still the
    text the export wrote: ``{content, children}`` nodes, at most
    MAX_BLOCKS, each content at most MAX_CONTENT characters; None
    otherwise."""
    flow = (note.private or {}).get("flow")
    if not isinstance(flow, dict) or flow.get("text") != "".join(r.text for r in note.flow):
        return None
    count = [0]

    def nodes(value, depth):
        if not isinstance(value, list) or depth > 64:
            return None
        out = []
        for node in value:
            content = node.get("content") if isinstance(node, dict) else None
            if not isinstance(content, str) or len(content) > MAX_CONTENT:
                return None
            count[0] += 1
            children = nodes(node.get("children") or [], depth + 1)
            if children is None or count[0] > MAX_BLOCKS:
                return None
            out.append({"content": content, "children": children})
        return out

    return nodes(flow.get("blocks"), 0)


def copied_papers(note: Note) -> list | None:
    """Gamma's copy of the sheets' papers (normalized), or None."""
    papers = (note.private or {}).get("papers")
    if not isinstance(papers, list) or not papers or len(papers) > MAX_NOTE_PAGES:
        return None
    if not all(isinstance(p, dict) for p in papers):
        return None
    return [normalize_paper(p) for p in papers]


# --- text ---------------------------------------------------------------------------------

def _paragraphs(runs: list[Run]):
    """The runs cut into paragraphs at each line break, one at a time."""
    current = []
    for run in runs:
        for i, part in enumerate(run.text.split("\n")):
            if i:
                yield current
                current = []
            if part:
                current.append(replace(run, text=part))
    yield current


def _inline(runs: list[Run]) -> str:
    """One paragraph as inline Markdown: runs of one style merged, each
    escaped as if it began a line (``text_box.escape_markdown``, so "$5",
    "# x" and "1." read as typed) and wrapped in ``~~``, ``==``, ``**`` and
    ``*`` with its outer spaces outside the marks; trimmed, since a leading
    indent would read as code. Underline, fonts, colours and sizes have no
    Markdown."""
    if not runs:
        return ""
    merged: list[list] = []
    for run in runs:
        style = (run.strike, run.mark, run.bold, run.italic)
        if merged and merged[-1][0] == style:
            merged[-1][1] += run.text
        else:
            merged.append([style, run.text])
    out = []
    for (strike, mark, bold, italic), text in merged:
        core = text.strip()
        if not core:
            out.append(text)
            continue
        lead, trail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        marks = ("~~" if strike else "") + ("==" if mark else "") + ("**" if bold else "") + ("*" if italic else "")
        out.append(lead + marks + escape_markdown(core) + marks[::-1] + trail)
    return "".join(out).strip()


def _marker(run: Run) -> str:
    return {1: "- ", 2: f"{run.number}. ", 3: "- [x] " if run.checked else "- [ ] "}.get(run.list_style, "")


def flow_blocks(runs: list[Run], left_out: Counter | None = None) -> list[dict]:
    """The text typed on the pages as notes (``{content, children}``): it
    has no stored place (Notability lays it out from the top of page 1),
    so each non-empty paragraph is a block (a line separator in it a line
    break), nested by its list level under
    the nearest shallower one (a jump of several levels nests one deeper).
    A bullet is its text, a numbered item "n. text", a checklist item the
    editor's to-do, "- [ ] text" or "- [x] text". At most MAX_BLOCKS
    blocks, each cut at ops.MAX_CONTENT characters (counted)."""
    left_out = left_out if left_out is not None else Counter()
    blocks: list[dict] = []
    stack: list[tuple[int, dict]] = []
    count = 0
    for para in _paragraphs(runs):
        # a line separator breaks a line inside the paragraph: a note of several lines
        text = _inline([replace(r, text=r.text.replace(LINE_SEPARATOR, "\n")) for r in para])
        if not text:
            continue
        if count >= MAX_BLOCKS:
            left_out["text"] += 1
            continue
        first = para[0]
        if first.list_style in (2, 3):
            text = _marker(first) + text
        if len(text) > MAX_CONTENT:
            text = text[:MAX_CONTENT]
            left_out["long text"] += 1
        level = first.level if first.list_style else 0
        node = {"content": text, "children": []}
        while stack and stack[-1][0] >= level:
            stack.pop()
        (stack[-1][1]["children"] if stack else blocks).append(node)
        stack.append((level, node))
        count += 1
    return blocks


def box_markdown(runs: list[Run]) -> str:
    """A text box's Markdown: a line per paragraph (a box shows a line per
    source line); list items as ``- ``, ``n. `` or ``- [ ] ``/``- [x] ``,
    indented four spaces a level (a jump of several levels one deeper); a
    plain paragraph never indented, and a blank line between a list and
    the paragraph after it."""
    lines: list[str] = []
    depth = -1
    for para in _paragraphs(runs):
        text = _inline(para)
        first = para[0] if para else None
        if first is not None and first.list_style and text:
            depth = min(first.level, depth + 1)
            lines.append("    " * depth + _marker(first) + text)
            continue
        if depth >= 0 and text:
            lines.append("")
        depth = -1
        lines.append(text)
    return "\n".join(lines).strip("\n")


def _majority(runs: list[Run], key):
    """The value ``key`` gives most of the characters, None when none has one."""
    counts = Counter()
    for run in runs:
        value = key(run)
        if value is not None:
            counts[value] += len(run.text.strip())
    return counts.most_common(1)[0][0] if counts and counts.most_common(1)[0][1] else None


def _surface_rect(media: Media, frame: Frame) -> tuple[float, float, float, float]:
    k = frame.k
    return (media.x - frame.ox) * k, (media.y - frame.oy) * k, media.w * k, media.h * k


def text_box(media: Media, frame: Frame, left_out: Counter | None = None) -> dict | None:
    """A text box or a formula as ``{"content", "text_box"}`` in the frame,
    or None when it holds nothing to show. A box cannot turn, so a turned
    one stands upright on the same centre (counted). Fixed width, as tall
    as Notability drew it (at least one line), kept on the page. A text
    box's size and colour are those of most of its characters, its fill
    its paper colour; a formula is display math in its colour. Its text is
    cut at ops.MAX_CONTENT characters, what the editor saves (counted)."""
    x, y, w, h = _surface_rect(media, frame)
    if abs(media.rotation) > 1e-6 and left_out is not None:
        left_out["rotated box"] += 1
    if media.kind == "math":
        latex = "\n".join(line for line in media.latex.split("\n") if line.strip())
        if not latex:
            return None
        content = markdown_of(latex) if "$$" in latex else f"$$\n{latex}\n$$"
        size, color, bg = MATH_SIZE, _hex(media.rgba) if media.rgba else TEXT_COLOR, None
    else:
        content = box_markdown(media.runs)
        bg = _hex(media.background) if media.background and media.background[3] > 0 else None
        if not content.strip() and bg is None:
            return None
        size = _majority(media.runs, lambda run: round(run.size, 2) if run.size else None)
        size = min(MAX_SIZE, max(MIN_SIZE, size * frame.k)) if size else 12
        color = _majority(media.runs, lambda run: _hex(run.rgba) if run.rgba else None) or TEXT_COLOR
    if len(content) > MAX_CONTENT:  # what the editor saves (counted)
        content = content[:MAX_CONTENT]
        if left_out is not None:
            left_out["long text"] += 1
    box = {"x": x, "y": y, "w": w, "h": max(h, size * LINE + 2 * PAD), "auto": False, "size": size,
           "color": color, "bg": bg}
    return {"content": content, "text_box": keep_on_surface(normalize_text_box(box), frame.width, frame.height)}


def picture_box(media: Media, frame: Frame) -> dict:
    """The box a picture is drawn in: the bounding box of the picture as
    Notability turns it about its centre, grown by the box's padding, fixed
    width, kept on the page. The picture is stored turned (the import's
    ``picture_bytes``), so it fills the box's text width."""
    x, y, w, h = _surface_rect(media, frame)
    cx, cy = x + w / 2, y + h / 2
    c, s = abs(math.cos(media.rotation)), abs(math.sin(media.rotation))
    bw, bh = w * c + h * s, w * s + h * c
    box = {"x": cx - bw / 2 - PAD, "y": cy - bh / 2 - PAD, "w": bw + 2 * PAD, "h": bh + 2 * PAD, "auto": False}
    return keep_on_surface(normalize_text_box(box), frame.width, frame.height)
