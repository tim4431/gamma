"""Handwriting and AI (docs/dev/handwriting_ai.md): what the two one-shot
calls behind the lasso selection's Convert menu send, and how their replies
are read. Redraw as shapes sends a digest of the pen strokes (numbered,
simplified, in a frame at the selection's corner) and a picture of them,
and reads back clean shape records in the stroke file's own kinds
(docs/dev/handwriting.md "The stroke file"). Convert to text sends the
picture alone and reads back Markdown.

Pure: the routes (routers/ink_ai.py) make the calls. Nothing here does I/O
but drawing the picture, which MuPDF rasterizes from the notebook export's
one-page PDF, as ink_view draws a selection on blank paper."""

import json
import math
import re

from . import ink as inkmod
from .ink_view import crop_box
from .notebook import normalize_paper, notebook_pdf
from .pdf_text import RENDER_MAX_SIDE, render_page
from .translate_context import _FENCED

# Pen strokes one redraw takes: past this many the digest outgrows what a
# model reads well in one turn (24 points a stroke), and the client refuses
# with the same sentence first.
MAX_REDRAW_STROKES = 300
TOO_MANY_STROKES = "Too much handwriting to redraw at once; select less."
# The digest's points per stroke, at most: enough for a box's corners or a
# loop's turns, few enough that 300 strokes stay a readable message.
DIGEST_POINTS = 24
# What RDP reads of a stroke: at most RDP_SAMPLES of its samples, evenly
# spaced, and fewer the more strokes there are, so that the digest's work,
# which grows as the square of a stroke's samples when every split peels
# off one point (a zigzag with exact ties), stays near RDP_WORK. One pen
# stroke may hold 500 000 samples; a digest of 24 points needs few of them.
RDP_SAMPLES = 1000
RDP_WORK = 4_000_000
# A curve record's points (the file format's cap: 31 pairs and two flags
# fill SHAPE_PARAMS).
CURVE_POINTS = 31
# A transcription longer than this is not one selection's handwriting.
MAX_TEXT_CHARS = 20_000
# Pen colours too light to show on the picture's white paper (white ink,
# picked for a dark slide or dark paper), by the luminance notebook.js
# paperIsDark reads, and the ink they are drawn in there instead.
LIGHT_INK = 0.8
DARK_INK = "#1f1f1f"
NO_TEXT = "[no text]"
NOT_SHAPES = "the AI's answer was not a list of shapes"

# Snaps the server makes whatever the model wrote: a line or arrow within
# this many degrees of level or plumb, a rectangle's or ellipse's turn
# within it of upright; an ellipse whose radii are this close is a circle.
SNAP_DEG = 3.0
ROUND_RATIO = 0.02
_SNAP_TAN = math.tan(math.radians(SNAP_DEG))

SHAPES_SYSTEM = """\
You tidy hand-drawn figures. The user selected some handwriting and wants the figures in it redrawn as clean shapes. You get the selection's strokes as numbered lists of points and, usually, a picture of them.

Each stroke line reads: its number, its width, its bounding box (x0,y0,x1,y1), whether its ends meet (closed) or not (open), then points along it in the order it was drawn. Coordinates are points, x to the right and y down, in the frame the message states. Use the picture to see what the strokes are, and the numbers for where they are.

Find the strokes that are parts of geometric figures: lines, arrows, rectangles, circles and ellipses, triangles and other polygons, curves, axes and plotted graphs. Describe each figure as one clean shape that replaces the strokes it is drawn with.

Rules:
- Only strokes that are clearly parts of figures. Never letters, words, numbers or formula symbols, not even a label inside or beside a figure. When unsure, leave the stroke out: what you leave out stays as drawn.
- Several strokes may form one shape: a box drawn in four strokes, an arrow whose head is a separate stroke. Each stroke belongs to at most one shape.
- Tidy what the drawing means: nearly horizontal or vertical lines exactly so, nearly right angles right, near-equal sizes of similar shapes equal, nearly aligned centres or edges aligned, and line and arrow ends that touch a shape ending exactly on its outline. Otherwise stay within a few points of the drawing.
- Angles are in degrees, clockwise (y points down); 0 is upright.

Reply with ONLY a JSON object, no code fence and no other text: {"shapes": [...]}, each shape one of these, naming the strokes it replaces:
{"kind": "line", "strokes": [1], "from": [x, y], "to": [x, y]}
{"kind": "arrow", "strokes": [2, 3], "from": [x, y], "to": [x, y]}
  "to" is the tip; an optional "head" is the barb length in points.
{"kind": "rect", "strokes": [4], "cx": x, "cy": y, "w": w, "h": h, "angle": 0}
  its centre, width and height; "angle" turns it about the centre.
{"kind": "ellipse", "strokes": [5], "cx": x, "cy": y, "rx": rx, "ry": ry, "angle": 0}
  its centre and two radii; a circle has rx = ry.
{"kind": "triangle", "strokes": [6], "points": [[x, y], [x, y], [x, y]]}
{"kind": "polygon", "strokes": [7], "points": [[x, y], ...]}
  any other closed figure with straight sides, its corners in order.
{"kind": "curve", "strokes": [8], "points": [[x, y], ...], "closed": false, "smooth": true}
  up to 31 points. "smooth": false draws straight segments between them (a zigzag, a plotted graph's line), "smooth": true a smooth curve through them (a wave, a parabola, a rounded outline); "closed": true joins the last point back to the first.
If no stroke is part of a figure, reply {"shapes": []}.

Example. These strokes
1: 2 pt; box 1,2,118,80; closed; 2,3 118,2 117,79 1,80 3,5
2: 2 pt; box 120,38,229,44; open; 120,44 175,41 229,38
3: 2 pt; box 219,30,231,50; open; 219,30 231,40 220,50
4: 1.4 pt; box 40,30,72,52; open; 40,30 48,52 56,34 64,52 72,30
are a box, an arrow drawn in two strokes leaving its right side, and the letter W written inside the box. The reply:
{"shapes": [{"kind": "rect", "strokes": [1], "cx": 59.5, "cy": 41, "w": 117, "h": 78, "angle": 0}, {"kind": "arrow", "strokes": [2, 3], "from": [118, 41], "to": [231, 41]}]}"""

TEXT_SYSTEM = """\
You transcribe handwriting. The picture shows handwriting the user selected; write out exactly what it says.

Rules:
- Transcribe exactly: the words as written, in reading order. Do not correct, complete, translate or explain them.
- Keep the lines as written: one output line per handwritten line.
- Write Markdown. A handwritten list stays a list as written: "- " for bullets or dashes, "1. " for numbers.
- Math as LaTeX: inline as $…$, and a formula displayed on a line of its own as $$…$$ on its own line.
- A word you cannot read is [illegible]; never guess.
- Reply with only the transcription: no preamble, no notes, no code fence.
- If the picture holds no writing, reply [no text].

Example. For a picture of four handwritten lines (the word "Energy" with a smudged word after it, a dashed list of "kinetic: ½mv²" and "potential: mgh", then E = ∫F dx written large on a line of its own), the reply is:
Energy [illegible]
- kinetic: $\\frac{1}{2}mv^2$
- potential: $mgh$
$$E = \\int F \\, dx$$"""

TEXT_TURN = "Transcribe the handwriting in the picture."


# --- What is sent ----------------------------------------------------------------

def pen_strokes(ink: inkmod.InkFile) -> tuple[inkmod.InkFile, list[int]]:
    """The strokes the AI is offered — pen strokes, never a highlighter's
    (nor a tool this build does not know) — as a file of their own, and
    each one's index in ``ink.strokes``, in order: stroke n of the digest
    is ``ink.strokes[indices[n - 1]]``."""
    indices = [i for i, s in enumerate(ink.strokes) if s.tool == "pen"]
    return ink.model_copy(update={"strokes": [ink.strokes[i] for i in indices]}), indices


def _light(color: str) -> bool:
    r, g, b, _ = inkmod.parse_color(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > LIGHT_INK


def picture(ink: inkmod.InkFile):
    """The strokes alone on blank white paper the size of their space,
    cropped around them (ink_view's crop: padded, at least 144 pt a side)
    and rasterized for a vision model: ``(image, shown)`` with ``image``
    as ``render_page`` makes it and ``shown`` the ``(x0, y0, x1, y1)`` in
    page points the picture covers; None when nothing could be drawn. The
    paper is not held to a sheet's 144..2000 pt (``normalize_paper``): a
    poster's PDF page runs past 2000 pt, and only the crop is rasterized.
    A stroke too light to show on white (LIGHT_INK) is drawn in DARK_INK,
    in the picture alone."""
    width, height = ink.space.width, ink.space.height
    paper = {**normalize_paper(None), "width": width, "height": height}
    box = crop_box([ink], width, height)
    if box is None:
        return None
    ink = ink.model_copy(update={"strokes": [s.model_copy(update={"color": DARK_INK}) if _light(s.color) else s
                                             for s in ink.strokes]})
    image, _ = render_page(notebook_pdf([(paper, [], [ink])]), 1, RENDER_MAX_SIDE, box)
    if image is None:
        return None
    return image, (box[0] * width, box[1] * height, box[2] * width, box[3] * height)


def _int(v: float) -> str:
    """A digest number: the nearest integer, a half going up, never "-0"."""
    return str(math.floor(v + 0.5) or 0)


def _width(v: float) -> str:
    """A stroke's width: to a tenth of a point, trailing zeros dropped."""
    return f"{v:.1f}".rstrip("0").rstrip(".") or "0"


def _segment_distance(p, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    f = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - f * dx, p[1] - a[1] - f * dy)


def importance(points: list, floor: float = 0.0) -> list[float]:
    """Each point's Ramer–Douglas–Peucker standing: RDP at tolerance ε
    keeps exactly the points whose standing is above ε. The span a split
    looks into, and the point it splits at (the farthest from the span's
    segment), do not depend on ε, only whether it splits: so a point's
    standing is its distance at its split, or less when a split above it
    was nearer. The ends stand at infinity. The distance is to the segment,
    not its line, so a closed stroke, whose ends meet, still keeps its far
    side. A span whose farthest point is within ``floor`` is not split
    further (its points stand at 0, true for every ε ≥ ``floor``): a
    straight run would otherwise split one point at a time."""
    n = len(points)
    out = [math.inf] * n
    spans = [(0, n - 1, math.inf)]
    while spans:
        i, j, above = spans.pop()
        far, at = -1.0, -1
        for k in range(i + 1, j):
            d = _segment_distance(points[k], points[i], points[j])
            if d > far:
                far, at = d, k
        if at < 0:
            continue
        if far <= floor:
            out[i + 1:j] = [0.0] * (j - i - 1)
            continue
        out[at] = min(far, above)
        spans += [(i, at, out[at]), (at, j, out[at])]
    return out


def rdp(points: list, epsilon: float, standing: list[float] | None = None) -> list:
    """Ramer–Douglas–Peucker: the fewest of ``points`` (first and last
    kept) that stay within ``epsilon`` of the rest."""
    standing = importance(points) if standing is None else standing
    return [p for p, v in zip(points, standing) if v > epsilon]


def _thinned(points: list, cap: int) -> list:
    """At most ``cap`` of ``points``, evenly spaced, the first and last kept."""
    n = len(points)
    if n <= cap:
        return points
    return [points[k * (n - 1) // (cap - 1)] for k in range(cap)]


def simplified(points: list, diagonal: float, cap: int = RDP_SAMPLES) -> list:
    """A stroke's samples as the digest gives them: RDP at
    max(1 pt, 1.5% of the stroke's diagonal), the tolerance half as much
    again until at most DIGEST_POINTS remain. RDP reads at most ``cap`` of
    the samples (``_thinned``), its standings once (``importance``), down
    to the first tolerance, the least it takes. When every point stands
    alike (a long zigzag, a scribble) a step can fall from more than
    DIGEST_POINTS to the two ends: the DIGEST_POINTS that stand highest are
    kept instead, in drawing order, so a long stroke never comes down to a
    segment."""
    points = _thinned(points, max(2, cap))
    epsilon = max(1.0, 0.015 * diagonal)
    standing = importance(points, epsilon)
    out = rdp(points, epsilon, standing)
    while len(out) > DIGEST_POINTS:
        epsilon *= 1.5
        fewer = rdp(points, epsilon, standing)
        if len(fewer) < 4:
            # The ends stand at infinity, so they are among the highest.
            highest = sorted(range(len(points)), key=lambda k: -standing[k])[:DIGEST_POINTS]
            return [points[k] for k in sorted(highest)]
        out = fewer
    return out


def _stroke_line(number: int, stroke: inkmod.Stroke, ox: float, oy: float, cap: int) -> str:
    samples = inkmod.stroke_polyline(stroke)
    width = sum(w for _, _, w in samples) / len(samples)
    points = [(x - ox, y - oy) for x, y, _ in samples]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    diagonal = _spread(points)
    ends = math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1])
    # Within 6 pt alone, a tick or a dash under 6 pt long would be a loop:
    # its ends must also come back past half its diagonal.
    closed = len(points) > 2 and ends <= max(6.0, 0.12 * diagonal) and ends <= 0.5 * diagonal
    shown: list[str] = []
    for x, y in simplified(points, diagonal, cap):
        pair = f"{_int(x)},{_int(y)}"
        if not shown or shown[-1] != pair:
            shown.append(pair)
    box = ",".join(_int(v) for v in (min(xs), min(ys), max(xs), max(ys)))
    return f"{number}: {_width(width)} pt; box {box}; {'closed' if closed else 'open'}; {' '.join(shown)}"


def digest(ink: inkmod.InkFile, shown=None) -> str:
    """The redraw call's user turn: the selection's size, the frame (points
    from the strokes' top-left corner, x right, y down, integers), the part
    of it the picture shows (``shown``, page points; the line is left out
    without a picture) and one line per stroke, numbered from 1 in order:
    its mean width, its box, open or closed (its ends within max(6 pt, 12%
    of its diagonal) and within half of it, so a short dash is open) and
    its points, simplified (``simplified``, reading at most
    sqrt(RDP_WORK / strokes) samples of each)."""
    x0, y0, x1, y1 = inkmod.bounding_box(ink)
    cap = min(RDP_SAMPLES, max(4 * DIGEST_POINTS, math.isqrt(RDP_WORK // max(1, len(ink.strokes)))))
    lines = ["Redraw the figures in this handwriting as clean shapes.",
             f"Selection: {_int(x1 - x0)} × {_int(y1 - y0)} pt. Coordinates are points from the "
             "selection's top-left corner, x to the right, y down."]
    if shown:
        lines.append(f"The picture shows x from {_int(shown[0] - x0)} to {_int(shown[2] - x0)} and y from "
                     f"{_int(shown[1] - y0)} to {_int(shown[3] - y0)} of this frame.")
    lines.append("Strokes (number: width in pt; box x0,y0,x1,y1; open|closed; points along it):")
    lines += [_stroke_line(n, s, x0, y0, cap) for n, s in enumerate(ink.strokes, 1)]
    return "\n".join(lines)


# --- The redraw's reply -------------------------------------------------------------

def _answer(raw: str):
    """The JSON object a reply carries that holds a ``shapes`` list: the
    last one, wherever it sits (reasoning text, code fences and braces of
    its own around it are passed over), as metadata._answer reads its
    record; None when there is none."""
    decoder, found = json.JSONDecoder(), None
    for m in re.finditer(r"\{", raw or ""):
        try:
            value, _ = decoder.raw_decode(raw, m.start())
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("shapes"), list):
            found = value
    return found


def _num(v):
    """A finite number as a float, or None (a bool is not one, nor an
    integer past a double's range)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        v = float(v)
    except OverflowError:
        return None
    return v if math.isfinite(v) else None


def _point(v):
    """An ``[x, y]`` pair (or ``{"x", "y"}``) as a tuple of floats, or None."""
    if isinstance(v, dict):
        v = [v.get("x"), v.get("y")]
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    x, y = _num(v[0]), _num(v[1])
    return None if x is None or y is None else (x, y)


def _points(v, closed: bool = False):
    """A list of points, or None when any is not one; a closed figure's
    last point that repeats its first is dropped."""
    if not isinstance(v, list):
        return None
    out = [_point(p) for p in v]
    if any(p is None for p in out):
        return None
    if closed and len(out) > 3 and math.hypot(out[-1][0] - out[0][0], out[-1][1] - out[0][1]) < 0.5:
        out.pop()
    return out


def _stroke_numbers(v, offered: int) -> list[int]:
    """The 1-based stroke numbers an entry names, in 1..offered, each once."""
    if not isinstance(v, list):
        v = [v]
    out: list[int] = []
    for n in v:
        n = _num(n)
        if n is not None and n.is_integer() and 1 <= n <= offered and int(n) not in out:
            out.append(int(n))
    return out


def _level(a, b):
    """A line's ends, made exactly level or plumb when it is within
    SNAP_DEG of it: both ends take their mean y (or x)."""
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    if dy <= _SNAP_TAN * dx:
        y = (a[1] + b[1]) / 2
        return (a[0], y), (b[0], y)
    if dx <= _SNAP_TAN * dy:
        x = (a[0] + b[0]) / 2
        return (x, a[1]), (x, b[1])
    return a, b


def _line(entry, kind):
    a, b = _point(entry.get("from")), _point(entry.get("to"))
    if a is None or b is None:
        return None
    a, b = _level(a, b)
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1:
        return None
    params = {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}
    head = _num(entry.get("head"))
    if kind == "arrow" and head is not None and 2 <= head <= 48:
        params["head"] = head
    return kind, params, [a, b]


def _turn(v) -> float:
    """An angle in degrees, taken into (-360, 360) before it is rounded: a
    huge one (1e307) would overflow the 1/100 grid. Exact for any finite
    float, and the rect's quarter turns and the ellipse's half turns both
    divide a whole one."""
    angle = _num(v)
    return 0.0 if angle is None else inkmod.round2(math.fmod(angle, 360))


def _rect(entry):
    cx, cy, w, h = (_num(entry.get(k)) for k in ("cx", "cy", "w", "h"))
    if None in (cx, cy, w, h):
        return None
    w, h = abs(w), abs(h)
    if w <= 0.5 or h <= 0.5:
        return None
    # The client's rule (shapes.js normaliseRect): the angle in [-45, 45),
    # each quarter turn taken out swapping the sides, so w stays the side
    # along the angle.
    raw = _turn(entry.get("angle"))
    angle = inkmod.round2((raw + 45) % 90 - 45)
    if inkmod.js_round((raw - angle) / 90) % 2:
        w, h = h, w
    if abs(angle) <= SNAP_DEG:
        angle = 0.0
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    corners = [(cx + u * c - v * s, cy + u * s + v * c)
               for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    return "rect", {"cx": cx, "cy": cy, "w": w, "h": h, "angle": angle}, corners


def _ellipse(entry, circle=False):
    cx, cy = _num(entry.get("cx")), _num(entry.get("cy"))
    if circle:
        rx = ry = _num(entry.get("r"))
    else:
        rx, ry = _num(entry.get("rx")), _num(entry.get("ry"))
    if None in (cx, cy, rx, ry):
        return None
    rx, ry = abs(rx), abs(ry)
    if rx <= 0.25 or ry <= 0.25:
        return None
    angle = _turn(entry.get("angle"))
    # shapes.js normaliseEllipse: rx the longer radius, the angle in [0, 180).
    if rx < ry:
        rx, ry, angle = ry, rx, angle + 90
    angle = inkmod.round2(angle % 180)
    if rx - ry <= ROUND_RATIO * rx:
        rx = ry = (rx + ry) / 2
        angle = 0.0
    elif min(angle, 180 - angle) <= SNAP_DEG:
        angle = 0.0
    elif abs(angle - 90) <= SNAP_DEG:
        angle = 90.0
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    hx, hy = math.hypot(rx * c, ry * s), math.hypot(rx * s, ry * c)
    reach = [(cx - hx, cy - hy), (cx + hx, cy + hy)]
    return "ellipse", {"cx": cx, "cy": cy, "rx": rx, "ry": ry, "angle": angle}, reach


def _corners(points) -> dict:
    out = {}
    for k, (x, y) in enumerate(points, 1):
        out[f"x{k}"], out[f"y{k}"] = x, y
    return out


def _spread(points) -> float:
    """The diagonal of the points' box: a figure under 1 pt across is
    dropped, as a line under 1 pt is, since in place of its strokes it
    would look like their deletion."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _triangle(points):
    if points is None or len(points) != 3 or _spread(points) < 1:
        return None
    return "triangle", _corners(points), points


def _curve(points, closed: bool, smooth: bool):
    if points is None or not 2 <= len(points) <= CURVE_POINTS or _spread(points) < 1:
        return None
    if len(points) == 2 and not smooth:
        a, b = _level(*points)
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1:
            return None
        return "line", {"x1": a[0], "y1": a[1], "x2": b[0], "y2": b[1]}, [a, b]
    return "curve", {**_corners(points), "closed": closed, "smooth": smooth}, points


def figure(entry):
    """``(kind, params, points)`` for one shape entry, in the frame its
    numbers are in (the redraw's digest; the page itself for the chat's
    ``draw``, gamma/ai_draw.py) — the stored kinds' params (handwriting.md
    "The stroke file"), normalised and snapped, and the points the box
    check reads — or None when the entry is not a shape this file format
    can hold."""
    kind = str(entry.get("kind") or "").strip().lower()
    kind = {"rectangle": "rect"}.get(kind, kind)
    if kind in ("line", "arrow"):
        return _line(entry, kind)
    if kind == "rect":
        return _rect(entry)
    if kind in ("ellipse", "circle"):
        return _ellipse(entry, circle=kind == "circle" and "r" in entry)
    if kind == "triangle":
        return _triangle(_points(entry.get("points"), closed=True))
    if kind == "polygon":
        points = _points(entry.get("points"), closed=True)
        if points is not None and len(points) == 3:
            return _triangle(points)
        if points is None or not 4 <= len(points) <= CURVE_POINTS:
            return None
        return _curve(points, True, False)
    if kind == "curve":
        closed = entry.get("closed") is True
        return _curve(_points(entry.get("points"), closed=closed), closed, entry.get("smooth") is True)
    return None


def placed(params: dict, ox: float, oy: float) -> dict:
    """Params moved from the digest's frame to page points and put on the
    1/100 pt grid: x-like keys gain ``ox``, y-like ones ``oy``; lengths and
    the angle are only rounded; the curve's flags stay booleans."""
    out = {}
    for k, v in params.items():
        if isinstance(v, bool):
            out[k] = v
        elif k == "cx" or re.fullmatch(r"x\d+", k):
            out[k] = inkmod.round2(v + ox)
        elif k == "cy" or re.fullmatch(r"y\d+", k):
            out[k] = inkmod.round2(v + oy)
        else:
            out[k] = inkmod.round2(v)
    return out


def read_shapes(raw: str, offered: list[int], box) -> list[dict]:
    """The redraw's reply as shape records ``[{kind, params, strokes}]``:
    ``params`` in page points as the stroke file stores them, ``strokes``
    the request's indices (``offered`` maps the digest's stroke n to
    ``offered[n - 1]``), each in one shape at most — a stroke an earlier
    shape took is dropped from a later one, and a shape left with none is
    skipped. A polygon of 3 points is a triangle and of 4..31 a closed
    straight curve; a curve of 2 points, not smooth, is a line. A shape
    with a point outside ``box`` (the selection, page points) grown by
    max(24 pt, a quarter of its longer side) is skipped, as is one too
    small or not a figure at all. ValueError when the reply holds no
    ``{"shapes": [...]}`` object."""
    answer = _answer(raw)
    if answer is None:
        raise ValueError(NOT_SHAPES)
    x0, y0, x1, y1 = box
    grow = max(24.0, 0.25 * max(x1 - x0, y1 - y0))
    lo_x, lo_y, hi_x, hi_y = x0 - grow, y0 - grow, x1 + grow, y1 + grow
    taken: set[int] = set()
    out = []
    for entry in answer["shapes"]:
        if not isinstance(entry, dict):
            continue
        made = figure(entry)
        if made is None:
            continue
        kind, params, points = made
        if not all(lo_x <= x + x0 <= hi_x and lo_y <= y + y0 <= hi_y for x, y in points):
            continue
        numbers = [n for n in _stroke_numbers(entry.get("strokes"), len(offered)) if n not in taken]
        if not numbers:
            continue
        taken.update(numbers)
        out.append({"kind": kind, "params": placed(params, x0, y0),
                    "strokes": sorted(offered[n - 1] for n in numbers)})
    return out


# --- The transcription's reply -------------------------------------------------------

def read_text(raw: str) -> str:
    """The transcription as the text box takes it: stripped, out of one
    enclosing code fence, "" for the model's ``[no text]``, at most
    MAX_TEXT_CHARS."""
    text = (raw or "").strip()
    fenced = _FENCED.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if text.rstrip(".").lower() == NO_TEXT:
        return ""
    return text[:MAX_TEXT_CHARS]
