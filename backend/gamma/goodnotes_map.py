"""Where a GoodNotes notebook lands in Gamma, as pure functions of the
``Notebook`` model (gamma/goodnotes.py): what it becomes, each page's frame
and paper, its strokes as gamma-ink v2 dicts, its text boxes and its
pictures' boxes (docs/dev/import_export.md "GoodNotes notebooks"; the
format: docs/research/goodnotes-onenote-import.md).

Every page of a notebook is bound to a PDF page of its own size: a paper
GoodNotes ships, or a page of a PDF the user brought. So a notebook
becomes (``plan_notebook``):

- ``notebook``: every page a catalogue paper (one of the rulings, or a
  cover): a Gamma notebook, a sheet per page with that paper's size,
  colour, pattern and pitch;
- ``original``: its pages exactly one PDF's, 1..n in order: a page carrying
  that PDF as it is;
- ``composed``: anything else: a page carrying one PDF made of its pages in
  order, each copied from the PDF page it is bound to, a catalogue paper's
  own page among them, so it looks as GoodNotes drew it.

A page is a ``notability_map.Frame`` at the origin whose scale is points
per canvas unit (6/11 on a page of the size its template says), so the
Notability planner's shapes, text boxes and picture boxes place GoodNotes'
too. Nothing here stores, decodes or writes.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from . import goodnotes as gn
from . import notability as nb
from . import notability_map as nmap
from .ink import encode_points
from .notebook import normalize_paper
from .text_box import PAD

STANDARD = (455.04, 588.45)      # pt: GoodNotes Standard paper, for a page whose template names no size
MAX_FORCE = nmap.MAX_FORCE       # UIKit's maximumPossibleForce for the Apple Pencil: raw force → p
_RGB = re.compile(rb"(?<![\w.-])([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+(?:scn|sc|rg)\b")


@dataclass
class PagePlan:
    frame: nmap.Frame
    page: gn.Page


@dataclass
class NotebookPlan:
    pages: list                  # [PagePlan], in display order
    kind: str                    # "notebook" | "original" | "composed"
    pdf: str | None              # for "original": its attachment id
    papers: list                 # each page's sheet paper, normalized (None for a PDF page)
    paper: dict                  # the first page's paper, or blank Standard
    warnings: list = field(default_factory=list)
    left_out: Counter = field(default_factory=Counter)


# --- papers -----------------------------------------------------------------------------------

def colours_of(content: bytes, rules: bytes | None) -> tuple[str | None, str | None]:
    """A catalogue paper's ``(fill, line)`` colours as ``#rrggbb``: the first
    colour its page's content stream sets (it fills the page with the
    paper), and the first its rules' Form XObject sets, or, where the rules
    are drawn in the page itself (the Mac app's papers), the next colour
    the page sets; None for one not found."""
    def colours(data, n):
        out = []
        for m in _RGB.finditer(data or b""):
            try:
                out.append(nmap.hex_color([min(1.0, max(0.0, float(v))) for v in m.groups()]))
            except ValueError:
                continue
            if len(out) >= n:
                break
        return out
    page = colours(content, 2)
    fill = page[0] if page else None
    drawn = colours(rules, 1) or page[1:]
    return fill, drawn[0] if drawn else None


def paper_of(template: gn.Template | None, colours: tuple | None) -> dict | None:
    """A catalogue template's sheet paper, normalized: its size (the
    template's canvas in points), its colour and its rules' colour (the
    PDF's, ``colours_of``), its pattern and pitch (the design's, the pitch
    as the writing guide states it when it does); a cover a blank sheet of
    its colour. None for any other template: a user's PDF, a design not
    known."""
    if template is None:
        return None
    design = template.design
    if design in gn.PAPERS:
        pattern, pitch = gn.PAPERS[design]
        pitch = template.pitch or pitch
    elif design in gn.COVERS:
        pattern, pitch = "blank", None
    else:
        return None
    width, height = _canvas_pt(template)
    fill, line = colours or (None, None)
    raw = {"width": width, "height": height, "color": fill or "#ffffff", "pattern": pattern}
    if pitch:
        raw["spacing"] = pitch
    if line:
        raw["line"] = line
    return normalize_paper(raw)


def _canvas_pt(template: gn.Template | None) -> tuple[float, float]:
    if template is None or template.canvas is None:
        return STANDARD
    return template.canvas[0] * gn.CANVAS_PT, template.canvas[1] * gn.CANVAS_PT


# --- the plan --------------------------------------------------------------------------------

def plan_notebook(notebook: gn.Notebook, pdf_pages: dict, colours: dict) -> NotebookPlan:
    """What the notebook becomes, and each page's frame. ``pdf_pages`` holds
    the display sizes of each readable attachment PDF's pages, ``colours``
    each catalogue paper's (``colours_of``), both by attachment id; with
    neither (a preview) the kind still follows from the templates. A page
    whose template, or its PDF or its page, cannot be read is blank paper
    of its size, with a warning; a turned page is imported upright
    (counted)."""
    warnings, left_out, warned = [], Counter(), set()

    def warn(message):
        if message not in warned:
            warned.add(message)
            warnings.append(message)

    rows = []  # (page, sheet paper or None, (attachment, page number, size) or None)
    for page in notebook.pages:
        t = page.template
        if page.turns:
            left_out["rotated page"] += 1
        paper = paper_of(t, colours.get(t.attachment) if t is not None else None)
        sizes = pdf_pages.get(t.attachment) if t is not None and t.attachment else None
        source = None
        if t is not None and t.attachment and sizes is not None and 1 <= t.page <= len(sizes):
            source = (t.attachment, t.page, sizes[t.page - 1])
        elif paper is None:
            warn("A page names no paper: it is blank paper" if t is None
                 else "A page's PDF could not be read: it is blank paper")
            width, height = _canvas_pt(t)
            paper = normalize_paper({"width": width, "height": height, "pattern": "blank"})
        rows.append((page, paper, source))
    kind, pdf = "notebook", None
    if any(paper is None for _page, paper, _source in rows):  # a page of a user's PDF
        names = {source[0] for _page, _paper, source in rows if source is not None}
        numbers = [source[1] if source is not None else None for _page, _paper, source in rows]
        name = next(iter(names)) if len(names) == 1 else None
        if (name is not None and all(paper is None for _page, paper, _source in rows)
                and numbers == list(range(1, len(rows) + 1)) and len(rows) == len(pdf_pages.get(name, ()))
                and not any(page.turns for page, _paper, _source in rows)):
            kind, pdf = "original", name
        else:
            kind = "composed"
    pages, papers = [], []
    for page, paper, source in rows:
        t = page.template
        canvas = t.canvas if t is not None and t.canvas else None
        if kind != "notebook" and source is not None:  # a PDF page: the user's, or a catalogue paper's own
            attachment, number, (dw, dh) = source
            canvas = canvas or (dw / gn.CANVAS_PT, dh / gn.CANVAS_PT)
            k = dw / canvas[0]
            if abs(dh / canvas[1] - k) > 0.01 * k:
                warn("A page's PDF is not the shape GoodNotes drew it: its handwriting is placed by its width")
            pages.append(PagePlan(nmap.Frame(0, canvas[1], 0, 0, k, dw, dh, attachment, number), page))
            papers.append(None)
            continue
        canvas = canvas or (paper["width"] / gn.CANVAS_PT, paper["height"] / gn.CANVAS_PT)
        k = paper["width"] / canvas[0]
        pages.append(PagePlan(nmap.Frame(0, canvas[1], 0, 0, k, paper["width"], paper["height"]), page))
        papers.append(paper)
    first = next((p for p in papers if p is not None), None)
    paper = first or normalize_paper({"width": STANDARD[0], "height": STANDARD[1], "pattern": "blank"})
    return NotebookPlan(pages, kind, pdf, papers, paper, warnings, left_out)


def preview_kind(notebook: gn.Notebook) -> str:
    """``"page"`` when the notebook becomes a notebook of sheets, ``"pdf"``
    when it becomes a page carrying a PDF: what a preview, which opens no
    PDF, can tell from the templates alone."""
    return "page" if all(paper_of(p.template, None) is not None or p.template is None or not p.template.attachment
                         for p in notebook.pages) else "pdf"


# --- strokes ---------------------------------------------------------------------------------

def decimate(samples: list[dict]) -> list[dict]:
    """finishStroke.js ``decimate``: a sample closer than DECIMATE_PT to the
    previous kept one is dropped; the first and the last stay."""
    if len(samples) < 3:
        return samples
    out = [samples[0]]
    for s in samples[1:-1]:
        if math.hypot(s["x"] - out[-1]["x"], s["y"] - out[-1]["y"]) >= nmap.DECIMATE_PT:
            out.append(s)
    last = samples[-1]
    if len(out) > 1 and math.hypot(last["x"] - out[-1]["x"], last["y"] - out[-1]["y"]) < nmap.DECIMATE_PT:
        out[-1] = last
    else:
        out.append(last)
    return out


def _on_page(points: list, frame: nmap.Frame) -> list[tuple[float, float]]:
    k = frame.k
    return [((x - frame.ox) * k, (y - frame.oy) * k) for x, y in points]


def _quadratic_samples(points: list) -> list[dict] | None:
    """A chain of quadratics (start, then control and end per segment) in
    page points as samples: each raised to the identical cubic and cut as
    the Notability planner cuts a cubic (within TOLERANCE of the curve, at
    most STEP_PT apart), then decimated. None past MAX_STROKE_SAMPLES,
    counted before a sample is made."""
    segments, total = [], 1
    for i in range(1, len(points) - 1, 2):
        p0, q, p1 = points[i - 1], points[i], points[i + 1]
        c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
        c2 = (p1[0] + 2 / 3 * (q[0] - p1[0]), p1[1] + 2 / 3 * (q[1] - p1[1]))
        n = nmap.cubic_pieces(p0, c1, c2, p1)
        segments.append((p0, c1, c2, p1, n))
        total += n
    if total > nmap.MAX_STROKE_SAMPLES:
        return None
    out = [{"x": points[0][0], "y": points[0][1]}]
    for p0, c1, c2, p1, n in segments:
        out += [{"x": x, "y": y} for x, y, _t in nmap.cubic_points(p0, c1, c2, p1, n)]
    return decimate(out)


def _polyline_samples(points: list, channels: dict) -> list[dict] | None:
    """Samples along a polyline in page points, a segment longer than
    STEP_PT cut into equal steps with each channel (a list of values per
    point) linear along it, then decimated. None past MAX_STROKE_SAMPLES."""
    steps = [max(1, math.ceil(math.dist(points[i], points[i + 1]) / nmap.STEP_PT)) for i in range(len(points) - 1)]
    if 1 + sum(steps) > nmap.MAX_STROKE_SAMPLES:
        return None
    out = [{"x": points[0][0], "y": points[0][1], **{c: v[0] for c, v in channels.items()}}]
    for i, n in enumerate(steps):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        for j in range(1, n + 1):
            t = j / n
            out.append({"x": x0 + (x1 - x0) * t, "y": y0 + (y1 - y0) * t,
                        **{c: v[i] + (v[i + 1] - v[i]) * t for c, v in channels.items()}})
    return decimate(out)


def pencil_law(p: float) -> tuple[float, float]:
    """``(w, o)`` of the brush table's Pencil (finishStroke.js BRUSHES.pencil,
    pinned by tests/shared/inkwidth.json) at pressure ``p``, held upright
    (no tilt is written, its columns being in dispute): what a restyle to
    the Pencil bakes again from ``p``."""
    p = min(1.0, max(0.0, p))
    return round(min(4.0, max(0.0, 1 + (p - 0.5))), 3), round(min(1.0, max(0.0, 0.15 + 0.85 * p)), 3)


def ink_stroke(ink: gn.Ink, frame: nmap.Frame) -> dict | None:
    """One sub-path of a GoodNotes stroke as a gamma-ink v2 stroke dict
    without an ``id``, or None when it cannot be one:

    - a constant-width pen: the Ballpoint (``monoline``), even, W/2 pt wide;
      a highlighter the highlighter tool at its width and alpha;
    - a variable-width pen (fountain, brush): the Pen with ``w`` the half
      width at each sample over its largest, ``size`` that largest width;
      of even width it is even (``pen: false``);
    - tape: an opaque even Ballpoint band as wide as the tape;
    - a pencil: the Pencil, with ``p`` (force ÷ MAX_FORCE) and its ``w``
      and ``o`` baked by the brush table's law when an Apple Pencil drew it
      (``pencil_law``), else even.

    A line style is the stroke's ``dash``. Widths scale with the page when
    its PDF is drawn at another size than 6/11 pt per unit."""
    r, g, b, alpha = ink.rgba
    if alpha <= 0 or len(ink.points) == 0:
        return None
    pts = _on_page(ink.points, frame)
    scale = frame.k / gn.CANVAS_PT
    stroke = {"color": nmap.hex_color(ink.rgba)}
    if alpha < 1 and not ink.tape:
        stroke["opacity"] = max(0.001, round(alpha, 3))
    if ink.highlighter:
        stroke["tool"] = "highlighter"
    ch = "xy"
    if ink.kind == "flat":
        samples = _quadratic_samples(pts)
        size = ink.width / 2 * scale
        if not ink.highlighter:
            stroke.update(brush="monoline", pen=False)
    elif ink.kind == "ribbon":
        peak = max(ink.radii)
        size = 2 * peak * frame.k
        varies = peak > 0 and any(abs(v - peak) > 1e-3 * peak for v in ink.radii)
        if ink.tape or ink.highlighter or not varies:
            samples = _polyline_samples(pts, {})
            if not ink.highlighter:
                stroke["pen"] = False
            if ink.tape:
                stroke["brush"] = "monoline"
        else:
            samples = _polyline_samples(pts, {"w": [v / peak for v in ink.radii]})
            ch = "xyw"
    else:
        size = ink.width / 2 * scale
        stroke["brush"] = "pencil"
        if not ink.highlighter and any(f > 0 for f in ink.force):
            samples = _polyline_samples(pts, {"p": [min(1.0, f / MAX_FORCE) for f in ink.force]})
            if samples is not None:
                for s in samples:
                    s["w"], s["o"] = pencil_law(s["p"])
            ch = "xypwo"
        else:
            samples = _polyline_samples(pts, {})
            stroke["pen"] = False
    if samples is None:
        return None
    if ink.dash:
        stroke["dash"] = ink.dash
    size = min(100.0, max(0.1, size))
    if "w" in ch:
        peak = max(s["w"] for s in samples)
        if size * peak > 100:
            size = 100 / peak * 0.999
    stroke["size"] = round(size, 3)
    stroke["ch"] = ch
    stroke["pts"] = encode_points(samples, ch)
    return nmap.checked_stroke(stroke)


def page_strokes(plan: PagePlan, left_out: Counter, left: list | None = None):
    """The page's stroke dicts in GoodNotes' drawing order (ink and shapes
    share it). A stroke that cannot be one is counted in ``left_out``
    ("stroke"). ``left`` (``[n]``, shared by the notebook's pages) is what
    is left of the notebook's samples: the stroke that would go past it and
    every one after it are counted ("samples") and never made."""
    frame, page = plan.frame, plan.page
    drawn = sorted([(item.z, 0, i, item) for i, item in enumerate(page.ink)]
                   + [(item.z, 1, i, item) for i, item in enumerate(page.shapes)])
    for _z, which, _i, item in drawn:
        if left is not None and left[0] <= 0:
            left_out["samples"] += 1
            continue
        stroke = ink_stroke(item, frame) if which == 0 else nmap.shape_stroke(item, frame, left_out)
        if stroke is None:
            left_out["stroke" if which == 0 else "shape"] += 1
            continue
        if left is not None:
            n = len(stroke["pts"]) // len(stroke["ch"])
            if n > left[0]:
                left[0] = 0
                left_out["samples"] += 1
                continue
            left[0] -= n
        yield stroke


# --- boxes -----------------------------------------------------------------------------------

def text_box(media: nb.Media, frame: nmap.Frame, left_out: Counter | None = None) -> dict | None:
    """A text box as ``{"content", "text_box"}``, by the Notability planner's
    rules (fixed width, the size and colour of most of its characters, its
    fill, upright), its box grown by Gamma's padding so its text starts
    where GoodNotes' frame puts it."""
    pad = PAD / frame.k
    grown = replace(media, x=media.x - pad, y=media.y - pad, w=media.w + 2 * pad, h=media.h + 2 * pad)
    return nmap.text_box(grown, frame, left_out)


def page_media(plan: PagePlan) -> list[nb.Media]:
    """The page's pictures and text boxes, in drawing order."""
    return sorted(plan.page.media, key=lambda m: m.z)
