"""Handwriting: the ``gamma-ink`` stroke file (version 2) and the pure
geometry every consumer shares (docs/dev/handwriting.md).

An ink group is a block whose ``properties.ink_url`` names one of these
files (``uploads/<sha>.ink``, plain JSON). The file is the only source of
truth for the strokes; nothing derived is stored. Coordinates are the page
as displayed at scale 1 (pdf.js viewport: points, origin top-left, y down,
rotation applied) — the same frame highlight rects normalise to, so the PDF
writers map ink through the exact conversion they already use for rects.

    {"format": "gamma-ink", "version": 2,
     "space": {"kind": "pdf-page", "page": 3, "width": 612, "height": 792},
     "strokes": [{"id": "k7Qm2x", "tool": "pen", "color": "#1f1f1f",
                  "size": 1.6, "opacity": 1, "pen": true, "t0": 1757760000000,
                  "ch": "xyptw", "pts": [12040, 30512, 620, 0, 1120, 18, -3, 700, 8, 80]}]}

``ch`` declares the channels of every sample (InkML's idea): ``x`` ``y``
always, then any letters: ``p`` pressure, ``t`` time, ``a`` altitude, ``z``
azimuth, ``w`` the baked drawn-width multiplier, ``o`` the baked opacity
multiplier (the pencil writes it). ``pts`` is one flat integer array — x/y
in 1/100 pt, t in ms, w and o in thousandths are delta-encoded after the
first sample; p is 0..1000, a/z degrees, absolute. A letter this code does
not know is carried by stride and copied through. The drawn width at a
sample is ``size × w`` when the stroke has ``w``, else ``size``, and the
drawn opacity ``opacity × o``, else ``opacity``: there is no width or
opacity law here (the client's finishStroke.js bakes both). The frontend's
``src/ink/ink.js`` is the mirror of the codec and of the dash table; the
shared fixtures under tests/shared pin both.
"""

import bisect
import json
import math
import re
from dataclasses import dataclass, field
from itertools import accumulate
from operator import add, sub
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .colors import parse_color as _parse_color
from .highlights import position

FORMAT = "gamma-ink"
VERSION = 2
MAX_STROKES = 5000
MAX_SAMPLES = 500_000
MAX_BYTES = 6 * 1024 * 1024
COORD_UNIT = 100        # stored x/y are hundredths of a point
PRESSURE_UNIT = 1000    # stored p is 0..1000
MULT_UNIT = 1000        # stored w / o are multipliers × 1000
MAX_W = 4000            # the running w sum (0..4.000 decoded; a slanted pencil reaches 3.5)
MAX_O = 1000
MAX_CH = 12
EXTRA_BYTES_STROKE = 1024
EXTRA_BYTES_FILE = 16 * 1024
EXTRA_DEPTH = 3
EXTRA_STRING = 1024
EXTRA_ARRAY = 256
SHAPE_PARAMS = 64
DELTA_CH = frozenset("xytwo")      # delta-encoded after the first sample
KNOWN_CH = frozenset("xyptazwo")
TOOL_RE = r"^[a-z][a-z0-9-]{0,15}$"
_CH_RE = re.compile(r"^(?!.*(.).*\1)xy[a-z]*$")
_COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{6}|rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(,\s*[0-9.]+\s*)?\))$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EXTRA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
_UPLOAD_RE = re.compile(r"/api/uploads/([0-9a-f]{24}\.ink)$")
SHAPE_KINDS = ("line", "arrow", "rect", "ellipse", "triangle", "polygon", "curve")
# A stroke's dash as (on, off) in sizes — never stored, derived from the
# nominal size at render time (DASH in ink.js; tests/shared/inkdash.json).
DASH = {"dashed": (2.0, 2.0), "dotted": (0.1, 2.0)}
# The shades a pencil is drawn in: ``o`` quantised up to one of these.
OPACITY_BANDS = (0.33, 0.66, 1.0)
UPDATE_GAMMA = "update Gamma"  # the 400 detail for a version 1 upload: every writer is version 2 since release T


class InkError(ValueError):
    pass


class InkVersionError(InkError):
    """A version 1 (or unversioned) file where version 2 is required: the
    writer has to update (``upgrade_v1`` is the one reader of it)."""


# --- extras ----------------------------------------------------------------------

def _check_extra(value, depth: int, where: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError(f"{where}: not a finite number")
        return
    if isinstance(value, str):
        if len(value) > EXTRA_STRING:
            raise ValueError(f"{where}: string over {EXTRA_STRING} chars")
        return
    if depth >= EXTRA_DEPTH:
        raise ValueError(f"{where}: nested deeper than {EXTRA_DEPTH}")
    if isinstance(value, list):
        if len(value) > EXTRA_ARRAY:
            raise ValueError(f"{where}: array over {EXTRA_ARRAY} items")
        for v in value:
            _check_extra(v, depth + 1, where)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not _EXTRA_KEY_RE.match(k):
                raise ValueError(f"{where}: bad key {k!r}")
            _check_extra(v, depth + 1, where)
        return
    raise ValueError(f"{where}: unsupported value")


def _check_extras(model: BaseModel, budget: int, where: str) -> None:
    """The caps on a model's unknown keys (handwriting.md "anything else"):
    a null-valued key is dropped, the rest kept verbatim."""
    extra = model.__pydantic_extra__
    if not extra:
        return
    for k in [k for k, v in extra.items() if v is None]:
        del extra[k]
    for k, v in extra.items():
        if not _EXTRA_KEY_RE.match(k):
            raise ValueError(f"{where}: bad key {k!r}")
        _check_extra(v, 1, f"{where}.{k}")
    if extra and len(json.dumps(extra, separators=(",", ":"))) > budget:
        raise ValueError(f"{where}: extra keys over {budget} bytes")


class Space(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    kind: Literal["pdf-page", "canvas"] = "pdf-page"
    page: int | None = Field(default=None, ge=1)
    width: float = Field(gt=0, le=100_000)
    height: float = Field(gt=0, le=100_000)

    @model_validator(mode="after")
    def _page_for_pdf(self):
        if self.kind == "pdf-page" and self.page is None:
            raise ValueError("a pdf-page space needs a page number")
        _check_extras(self, EXTRA_BYTES_FILE, "space")
        return self


class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["line", "arrow", "rect", "ellipse", "triangle", "polygon", "curve"]
    params: dict[str, float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _finite(self):
        if len(self.params) > SHAPE_PARAMS:
            raise ValueError(f"shape params over {SHAPE_PARAMS}")
        for k, v in self.params.items():
            if not _EXTRA_KEY_RE.match(k):
                raise ValueError(f"shape: bad param {k!r}")
            if not isinstance(v, bool) and not math.isfinite(v):
                raise ValueError("shape: not a finite number")
        return self


class Fill(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    opacity: float = Field(default=0.25, gt=0, le=1)


class Stroke(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    tool: str = Field(default="pen", pattern=TOOL_RE)
    brush: str | None = Field(default=None, pattern=TOOL_RE)
    color: str = Field(default="#1f1f1f", max_length=40)
    size: float = Field(default=1.6, gt=0, le=100)
    opacity: float = Field(default=1.0, gt=0, le=1)
    pen: bool = True
    t0: int | None = Field(default=None, ge=0)
    band: Literal[0, 1] | None = None
    from_: str | None = Field(default=None, alias="from", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    shape: Shape | None = None
    fill: Fill | None = None
    dash: Literal["dashed", "dotted"] | None = None
    nib: int | None = Field(default=None, ge=0, le=179)
    ch: str = Field(default="xy", max_length=MAX_CH)
    pts: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self):
        if not _CH_RE.match(self.ch):
            raise ValueError(f"bad channel list {self.ch!r}")
        if not _COLOR_RE.match(self.color):
            raise ValueError(f"bad color {self.color!r}")
        n = len(self.ch)
        if not self.pts or len(self.pts) % n:
            raise ValueError("pts length must be a positive multiple of the channel count")
        for letter, cap in (("w", MAX_W), ("o", MAX_O)):
            slot = self.ch.find(letter)
            if slot < 0:
                continue
            total, peak = 0, 0
            for i in range(slot, len(self.pts), n):
                total += self.pts[i]
                if not 0 <= total <= cap:
                    raise ValueError(f"channel {letter} out of 0..{cap}")
                peak = max(peak, total)
            if letter == "w" and self.size * peak / MULT_UNIT > 100:
                raise ValueError("size × max(w) over 100")
        _check_extras(self, EXTRA_BYTES_STROKE, f"stroke {self.id}")
        return self

    @property
    def samples(self) -> int:
        return len(self.pts) // len(self.ch)


class InkFile(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    format: Literal["gamma-ink"]
    version: Literal[2]
    space: Space
    strokes: list[Stroke] = Field(default_factory=list, max_length=MAX_STROKES)

    @model_validator(mode="after")
    def _budget(self):
        if sum(s.samples for s in self.strokes) > MAX_SAMPLES:
            raise ValueError(f"more than {MAX_SAMPLES} samples")
        if len({s.id for s in self.strokes}) != len(self.strokes):
            raise ValueError("duplicate stroke ids")
        _check_extras(self, EXTRA_BYTES_FILE, "file")
        return self


def _reject_constant(name: str):
    raise InkError(f"not a finite number: {name}")


def load_json(data: bytes | str) -> dict:
    """``json.loads`` for an ink file: refuses NaN / Infinity (pydantic would
    keep one in an extra key, and every browser's JSON.parse then fails on
    the file) and anything that is not an object."""
    try:
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        obj = json.loads(data, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InkError(str(e)) from e
    if not isinstance(obj, dict):
        raise InkError("an ink file is a JSON object")
    return obj


def parse_ink(data: bytes | str | dict) -> InkFile:
    """Validate bytes/JSON into an InkFile; InkError on anything off,
    InkVersionError for a version 1 file (``upgrade_v1`` first)."""
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_BYTES:
            raise InkError(f"ink file over {MAX_BYTES // (1024 * 1024)} MB")
        data = load_json(data)
    elif isinstance(data, str):
        data = load_json(data)
    if isinstance(data, dict) and data.get("format") == FORMAT and data.get("version", 1) == 1:
        raise InkVersionError("gamma-ink version 1: update Gamma")
    try:
        return InkFile.model_validate(data)
    except ValidationError as e:
        raise InkError(str(e)) from e


_cache: dict = {}
_CACHE_MAX = 64


def read_upload(uploads_dir, url: str) -> InkFile | None:
    """The parsed file a block's ``ink_url`` (``/api/uploads/<sha>.ink``)
    names, or None when the reference, the file or its contents are off;
    the exporters skip such a group. Parsed files are kept in a small LRU
    keyed by path and stat (the name is the content's hash), so a page's
    exporters and merges parse each file once; callers never mutate what
    they get."""
    m = _UPLOAD_RE.search(url or "")
    path = uploads_dir / m.group(1) if (m and uploads_dir) else None
    if not path:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _cache.pop(key, None)
    if hit is None:
        try:
            hit = parse_ink(path.read_bytes())
        except (InkError, OSError):
            return None
    _cache[key] = hit
    while len(_cache) > _CACHE_MAX:
        del _cache[next(iter(_cache))]
    return hit


# --- codec -------------------------------------------------------------------

def decode_stroke(stroke: Stroke) -> list[dict]:
    """A stroke's samples in points: ``[{x, y, p?, t?, a?, z?, w?, o?,
    rest}]`` — ``p`` 0..1, ``t`` ms since t0, ``a``/``z`` degrees, ``w``
    and ``o`` as multipliers, ``rest`` the integers of the channels this
    code does not know, in order (``encode_points`` copies them back)."""
    ch, pts, n = stroke.ch, stroke.pts, len(stroke.ch)
    out = []
    acc = {c: 0 for c in ch if c in DELTA_CH}
    for i in range(0, len(pts), n):
        rec: dict = {"rest": []}
        for c, v in zip(ch, pts[i:i + n]):
            if c in acc:
                acc[c] += v
                v = acc[c]
            if c == "x":
                rec["x"] = v / COORD_UNIT
            elif c == "y":
                rec["y"] = v / COORD_UNIT
            elif c == "p":
                rec["p"] = max(0.0, min(1.0, v / PRESSURE_UNIT))
            elif c == "t":
                rec["t"] = v
            elif c in ("a", "z"):
                rec[c] = v
            elif c in ("w", "o"):
                rec[c] = v / MULT_UNIT
            else:
                rec["rest"].append(v)
        out.append(rec)
    return out


def encode_points(samples: list[dict], ch: str = "xy") -> list[int]:
    """The inverse of :func:`decode_stroke` (the importer and tests use it):
    ``samples`` carry ``x``/``y`` in points and the optional channels; a
    letter not known here takes the next integer of the sample's ``rest``."""
    out = []
    prev = {c: 0 for c in ch if c in DELTA_CH}
    for s in samples:
        rest = iter(s.get("rest") or ())
        for c in ch:
            if c in ("x", "y"):
                v = round(s[c] * COORD_UNIT)
            elif c == "p":
                v = round(max(0.0, min(1.0, s.get("p", 0.5))) * PRESSURE_UNIT)
            elif c == "t":
                v = int(s.get("t") or 0)
            elif c == "a":   # a sample without tilt in a tilted stroke is upright (ink.js encodePoints)
                v = round(max(0.0, min(90.0, s["a"] if s.get("a") is not None else 90.0)))
            elif c == "z":
                v = round(s["z"]) if s.get("z") is not None else 0
            elif c in ("w", "o"):
                v = round((s.get(c) if s.get(c) is not None else 1.0) * MULT_UNIT)
            else:
                v = int(next(rest, 0))
            if c in prev:
                out.append(v - prev[c])
                prev[c] = v
            else:
                out.append(v)
    return out


def stroke_width(stroke: Stroke, sample: dict) -> float:
    """The drawn diameter at a decoded sample: ``size × w`` when the stroke
    carries the ``w`` channel, else ``size``. The whole rule."""
    return stroke.size * sample["w"] if "w" in sample else stroke.size


def stroke_opacity(stroke: Stroke, sample: dict) -> float:
    """The drawn opacity at a decoded sample: ``opacity × o`` when the
    stroke carries the ``o`` channel, else ``opacity``. The whole rule."""
    return stroke.opacity * sample["o"] if "o" in sample else stroke.opacity


def stroke_polyline(stroke: Stroke) -> list[tuple[float, float, float]]:
    """``[(x, y, width)]`` per sample — what every renderer here draws from
    (Xournal++'s model: variable-width polylines). Read off the raw arrays,
    no per-sample dicts."""
    ch, pts, n, size = stroke.ch, stroke.pts, len(stroke.ch), stroke.size
    xi, yi, wi = ch.index("x"), ch.index("y"), ch.find("w")
    out = []
    x = y = w = 0
    for i in range(0, len(pts), n):
        x += pts[i + xi]
        y += pts[i + yi]
        if wi >= 0:
            w += pts[i + wi]
            out.append((x / COORD_UNIT, y / COORD_UNIT, size * w / MULT_UNIT))
        else:
            out.append((x / COORD_UNIT, y / COORD_UNIT, size))
    return out


def band_of(stroke: Stroke) -> int:
    """0 draws under, 1 as ink: the stored ``band`` or the tool's default."""
    if stroke.band is not None:
        return stroke.band
    return 0 if stroke.tool == "highlighter" else 1


def in_draw_order(strokes) -> list[Stroke]:
    """Band 0 first, then band 1, each in file order: the order every
    renderer draws in."""
    return [s for s in strokes if band_of(s) == 0] + [s for s in strokes if band_of(s) == 1]


def bounding_box(ink: InkFile) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) in points around every stroke, width included;
    None for an empty file. Read off the raw arrays — the running sums of
    x, y and w — with no per-sample objects: the upload route computes it
    on every flush."""
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    half = COORD_UNIT / MULT_UNIT / 2    # a w integer → a radius in coordinate units, per point of size
    for s in ink.strokes:
        ch, pts, n = s.ch, s.pts, len(s.ch)
        xs = list(accumulate(pts[ch.index("x")::n]))
        ys = list(accumulate(pts[ch.index("y")::n]))
        wi = ch.find("w")
        if wi < 0:
            r = s.size * COORD_UNIT / 2
            sx0, sx1, sy0, sy1 = min(xs) - r, max(xs) + r, min(ys) - r, max(ys) + r
        else:
            rs = list(map((s.size * half).__mul__, accumulate(pts[wi::n])))
            sx0, sx1 = min(map(sub, xs, rs)), max(map(add, xs, rs))
            sy0, sy1 = min(map(sub, ys, rs)), max(map(add, ys, rs))
        x0, y0, x1, y1 = min(x0, sx0), min(y0, sy0), max(x1, sx1), max(y1, sy1)
    if x0 is math.inf:
        return None
    return (x0 / COORD_UNIT, y0 / COORD_UNIT, x1 / COORD_UNIT, y1 / COORD_UNIT)


def pdf_position(ink: InkFile) -> dict | None:
    """The group's bounding box as a ``pdf_position`` (gamma/highlights.py)
    in the file's page frame, so jump-to-position, markers and export
    anchoring treat ink like any other region on the page."""
    box = bounding_box(ink)
    if not box or ink.space.kind != "pdf-page":
        return None
    return position(ink.space.page, ink.space.width, ink.space.height, [tuple(round(v, 2) for v in box)])


def parse_color(value: str) -> tuple[float, float, float, float]:
    """``#rrggbb`` or ``rgb[a](…)`` (any form gamma/colors.py reads) → (r, g,
    b, a) in 0..1; near-black for anything else."""
    return _parse_color(value) or (0.12, 0.12, 0.12, 1.0)


# --- the drawn look: dashes, opacity bands, outlines -----------------------------

def dash_pattern(stroke: Stroke) -> tuple[float, float] | None:
    """``(on, off)`` in points for a dashed or dotted stroke — ``DASH`` times
    its nominal ``size``, never the drawn width — or None for a solid one."""
    rule = DASH.get(stroke.dash) if stroke.dash else None
    return (rule[0] * stroke.size, rule[1] * stroke.size) if rule else None


def opacity_band(o: float) -> float:
    """``o`` quantised up to 0.33, 0.66 or 1: the three shades a pencil is
    drawn in (``opacityBand`` in ink.js)."""
    low, mid, top = OPACITY_BANDS
    return low if o <= low else mid if o <= mid else top


def _arcs(samples: list[dict]) -> list[float]:
    """The arc length at each sample, from 0."""
    out, total = [0.0], 0.0
    for a, b in zip(samples, samples[1:]):
        total += math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        out.append(total)
    return out


_LERPED = ("x", "y", "p", "t", "w", "o")


def _at(samples: list[dict], arcs: list[float], at: float) -> dict:
    """The sample at arc length ``at``: x, y, p, t, w and o linear in the
    arc between the samples around it, the other channels (a, z, unknown
    letters) those of the sample before."""
    i = bisect.bisect_left(arcs, at)
    if i == 0:
        return dict(samples[0])
    if i >= len(samples):
        return dict(samples[-1])
    a, b = samples[i - 1], samples[i]
    span = arcs[i] - arcs[i - 1]
    f = (at - arcs[i - 1]) / span if span else 1.0
    out = dict(a)
    for k in _LERPED:
        if k in a and k in b:
            out[k] = a[k] + (b[k] - a[k]) * f
    return out


def _cut(samples: list[dict], arcs: list[float], lo: float, hi: float) -> list[dict]:
    """The run of ``samples`` from arc ``lo`` to ``hi``: the samples strictly
    inside, and one lerped at each end (``_at``) — the run splitting the
    dashes and the pencil's bands share, as the client's eraser cuts."""
    inside = samples[bisect.bisect_right(arcs, lo):bisect.bisect_left(arcs, hi)]
    return [_at(samples, arcs, lo), *inside, _at(samples, arcs, hi)]


def dash_runs(stroke: Stroke, samples: list[dict] | None = None) -> list[list[dict]]:
    """A dashed or dotted stroke's on-runs by arc length, phase 0 at its
    first sample: ``[k (on + off), k (on + off) + on)`` clipped to its
    length, each a run of ``_cut``; a run of zero length is skipped. A
    solid stroke, or one of zero length, is one run. The client's
    ``dashRuns``: tests/shared/inkdash.json pins the two, and with them
    the run splitting the pencil's bands are cut with."""
    samples = decode_stroke(stroke) if samples is None else samples
    pattern = dash_pattern(stroke)
    arcs = _arcs(samples)
    if not pattern or not arcs[-1]:
        return [samples]
    on, off = pattern
    out, k = [], 0
    while k * (on + off) < arcs[-1]:
        lo = k * (on + off)
        hi = min(lo + on, arcs[-1])
        if hi > lo:
            out.append(_cut(samples, arcs, lo, hi))
        k += 1
    return out


def stroke_bands(stroke: Stroke, samples: list[dict] | None = None) -> list[tuple[float, list[dict]]]:
    """The stroke cut into runs of one opacity band, in drawing order:
    ``[(band, run)]``, ``o`` quantised up (``opacity_band``), with a sample
    lerped where ``o`` crosses a band's edge (``_cut``) — the pencil's
    shading. A stroke without ``o`` is one run at band 1."""
    samples = decode_stroke(stroke) if samples is None else samples
    if not samples or "o" not in samples[0]:
        return [(1.0, samples)]
    arcs = _arcs(samples)
    band = opacity_band(samples[0]["o"])
    out, lo = [], 0.0
    for i in range(1, len(samples)):
        o0, o1 = samples[i - 1]["o"], samples[i]["o"]
        if opacity_band(o1) == band:
            continue
        # the band edges this segment crosses, in the order it crosses them
        edges = [e for e in OPACITY_BANDS[:-1] if min(o0, o1) <= e < max(o0, o1)]
        for edge in (edges if o1 > o0 else edges[::-1]):
            at = arcs[i - 1] + (arcs[i] - arcs[i - 1]) * (edge - o0) / (o1 - o0)
            if at > lo:
                out.append((band, _cut(samples, arcs, lo, at)))
            lo = at
            band = OPACITY_BANDS[OPACITY_BANDS.index(edge) + 1] if o1 > o0 else edge
    if not out:
        return [(band, samples)]
    if arcs[-1] > lo:
        out.append((band, _cut(samples, arcs, lo, arcs[-1])))
    return out


# perfect-freehand 1.2.2 (MIT, Steve Ruiz) — the client's outline — at its
# ``outlineOptions``: thinning 1, smoothing 0.5, streamline 0.4, no
# simulated pressure, no tapers, round caps, ``last``. Run at ten times the
# page's scale: its one absolute constant, the 3 units at the end of a path
# whose points it drops, would otherwise blot a dash or a band run shorter
# than 3 pt into a disc about its length across.
_PF_SCALE = 10.0
_PF_T = 0.15 + (1 - 0.4) * 0.85      # getStrokePoints' step toward each point at streamline 0.4
_PF_PI = math.pi + 1e-4


def _pf_rotate(p, c, r):
    s, k = math.sin(r), math.cos(r)
    x, y = p[0] - c[0], p[1] - c[1]
    return (x * k - y * s + c[0], x * s + y * k + c[1])


def _pf_points(pts: list, size: float) -> list:
    """getStrokePoints over ``(x, y, pressure)`` — never two of them: the
    caller adds a midpoint, since perfect-freehand's two-point path drops
    their pressure — → ``[point, pressure, vector, running length]``."""
    if len(pts) == 1:
        x, y, p = pts[0]
        pts = [pts[0], (x + 1, y + 1, p)]
    x, y, p = pts[0]
    out = [[(x, y), p, (1.0, 1.0), 0.0]]
    prev, running, reached, last = (x, y), 0.0, False, len(pts) - 1
    for i in range(1, len(pts)):
        x, y, p = pts[i]
        pt = (x, y) if i == last else (prev[0] + (x - prev[0]) * _PF_T, prev[1] + (y - prev[1]) * _PF_T)
        if pt == prev:
            continue
        running += math.hypot(pt[1] - prev[1], pt[0] - prev[0])
        if i < last and not reached:
            if running < size:
                continue
            reached = True
        dx, dy = prev[0] - pt[0], prev[1] - pt[1]
        ln = math.hypot(dx, dy)
        out.append([pt, p, (dx / ln, dy / ln), running])
        prev = pt
    out[0][2] = out[1][2] if len(out) > 1 else (0.0, 0.0)
    return out


def _pf_outline(points: list, size: float, caps: tuple = (True, True)) -> list:
    """getStrokeOutlinePoints at the options above (the radius is ``size ×
    pressure`` at thinning 1): the outline polygon's points. ``caps`` are
    ``start.cap`` and ``end.cap``: a flat end is where a run meets the
    next."""
    total = points[-1][3]
    min_dist = (size * 0.5) ** 2
    left, right = [], []
    radius = first_radius = None
    prev_vec = points[0][2]
    pl = pr = tl = tr = points[0][0]
    prev_sharp = False
    n = len(points)
    for i, (point, pressure, vec, running) in enumerate(points):
        if i < n - 1 and total - running < 3:
            continue
        radius = size * (0.5 - (0.5 - pressure))
        if first_radius is None:
            first_radius = radius
        radius = max(0.01, radius)
        next_vec = points[i + 1][2] if i < n - 1 else vec
        next_dpr = vec[0] * next_vec[0] + vec[1] * next_vec[1] if i < n - 1 else 1.0
        prev_dpr = vec[0] * prev_vec[0] + vec[1] * prev_vec[1]
        sharp, next_sharp = prev_dpr < 0 and not prev_sharp, next_dpr < 0
        if sharp or next_sharp:          # a sharp corner: a half-turn of points around it
            ox, oy = prev_vec[1] * radius, -prev_vec[0] * radius
            step, t = 1 / 13, 0.0
            while t <= 1:
                tl = _pf_rotate((point[0] - ox, point[1] - oy), point, _PF_PI * t)
                left.append(tl)
                tr = _pf_rotate((point[0] + ox, point[1] + oy), point, _PF_PI * -t)
                right.append(tr)
                t += step
            pl, pr = tl, tr
            if next_sharp:
                prev_sharp = True
            continue
        prev_sharp = False
        if i == n - 1:
            ox, oy = vec[1] * radius, -vec[0] * radius
            left.append((point[0] - ox, point[1] - oy))
            right.append((point[0] + ox, point[1] + oy))
            continue
        lx, ly = next_vec[0] + (vec[0] - next_vec[0]) * next_dpr, next_vec[1] + (vec[1] - next_vec[1]) * next_dpr
        ox, oy = ly * radius, -lx * radius
        tl = (point[0] - ox, point[1] - oy)
        if i <= 1 or (pl[0] - tl[0]) ** 2 + (pl[1] - tl[1]) ** 2 > min_dist:
            left.append(tl)
            pl = tl
        tr = (point[0] + ox, point[1] + oy)
        if i <= 1 or (pr[0] - tr[0]) ** 2 + (pr[1] - tr[1]) ** 2 > min_dist:
            right.append(tr)
            pr = tr
        prev_vec = vec
    first = points[0][0]
    last = points[-1][0] if n > 1 else (first[0] + 1, first[1] + 1)
    if n == 1:                           # a dot
        dx, dy = first[1] - last[1], -(first[0] - last[0])
        ln = math.hypot(dx, dy)
        r = -(first_radius or radius)
        start = (first[0] + dx / ln * r, first[1] + dy / ln * r)
        dots, step = [], 1 / 13
        t = step
        while t <= 1:
            dots.append(_pf_rotate(start, first, _PF_PI * 2 * t))
            t += step
        return dots
    start_cap, step = [], 1 / 13
    if caps[0]:
        t = step
        while t <= 1:
            start_cap.append(_pf_rotate(right[0], first, _PF_PI * t))
            t += step
    else:
        cx, cy = left[0][0] - right[0][0], left[0][1] - right[0][1]
        start_cap = [(first[0] - cx * f, first[1] - cy * f) for f in (0.5, 0.51)] + \
                    [(first[0] + cx * f, first[1] + cy * f) for f in (0.51, 0.5)]
    vx, vy = points[-1][2]
    dx, dy = -vy, vx
    end_cap, step = [], 1 / 29
    if caps[1]:
        start = (last[0] + dx * radius, last[1] + dy * radius)
        t = step
        while t < 1:
            end_cap.append(_pf_rotate(start, last, _PF_PI * 3 * t))
            t += step
    else:
        end_cap = [(last[0] + dx * radius * f, last[1] + dy * radius * f) for f in (1, 0.99)] + \
                  [(last[0] - dx * radius * f, last[1] - dy * radius * f) for f in (0.99, 1)]
    return left + end_cap + right[::-1] + start_cap


def stroke_outline(stroke: Stroke, samples: list[dict] | None = None,
                   caps: tuple = (True, True)) -> list[tuple[float, float]]:
    """The filled outline polygon of a pen stroke — or of ``samples``, a run
    of it, with ``caps`` round at the ends that are the stroke's own and
    flat where it meets the next run, as the client's ``runOutline`` — as
    the screen draws it: perfect-freehand's ``getStroke`` driven by pressure
    ``w / 2`` (0.5 without ``w``), so its radius is ``size × w / 2``, the
    baked width, and no law is involved. Points in page units."""
    samples = decode_stroke(stroke) if samples is None else samples
    if not samples:
        return []
    if len(samples) == 2:
        samples = [samples[0], _at(samples, [0.0, 1.0], 0.5), samples[1]]
    k = _PF_SCALE
    pts = [(s["x"] * k, s["y"] * k, s["w"] / 2 if "w" in s else 0.5) for s in samples]
    size = stroke.size * k
    return [(x / k, y / k) for x, y in _pf_outline(_pf_points(pts, size), size, caps)]


# --- renderers ------------------------------------------------------------------

def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _svg_line(xys) -> str:
    """A centreline path; a single point is a tiny segment, so its round
    caps draw a dot."""
    xys = list(xys)
    d = "M" + " L".join(f"{_fmt(x)} {_fmt(y)}" for x, y in xys)
    return d + (f" L{_fmt(xys[0][0] + 0.01)} {_fmt(xys[0][1])}" if len(xys) == 1 else "")


def _svg_outline(points) -> str:
    """An outline as the client draws it (``svgPathFromPoints``): a closed
    path of quadratic curves through the points' midpoints."""
    if len(points) < 3:
        return ""
    d = [f"M{points[0][0]:.2f} {points[0][1]:.2f} Q"]
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        d.append(f"{ax:.2f} {ay:.2f} {(ax + bx) / 2:.2f} {(ay + by) / 2:.2f}")
    return " ".join(d) + "Z"


def _band_outlines(stroke: Stroke, samples: list[dict]) -> list[tuple[float, list]]:
    """The outlines of ``stroke_bands`` gathered per band, lightest first:
    each band is one filled path, painted once, so it never darkens where
    the stroke crosses itself, and a run's cut ends are flat, so two bands
    meet without overlapping."""
    runs = stroke_bands(stroke, samples)
    out: dict = {}
    for i, (band, run) in enumerate(runs):
        out.setdefault(band, []).append(stroke_outline(stroke, run, (i == 0, i == len(runs) - 1)))
    return sorted(out.items())


def _drawn_as(stroke: Stroke, alpha: float) -> str:
    """How the exporters draw a stroke: ``marker`` (a highlighter's
    multiplied centreline), ``dashed`` (one dashed centreline at the mean
    drawn width), ``bands`` (a filled outline per opacity band: a pencil,
    or a translucent pen, which stroked segments would darken where they
    overlap) or ``segments`` (an opaque solid pen's round-capped segments,
    each at its baked width)."""
    if stroke.tool == "highlighter":
        return "marker"
    if stroke.dash:
        return "dashed"
    return "bands" if alpha < 1 or "o" in stroke.ch else "segments"


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 1.0


def to_svg(ink: InkFile, box=None, pad: float = 4.0) -> str:
    """An SVG of the strokes cropped to ``box`` (default: the bounding box),
    band 0 then band 1. An opaque solid pen (and any tool this code does
    not know) draws one round-capped segment per sample pair at that pair's
    baked width; a translucent one, and a pencil, one filled outline per
    opacity band (``stroke_bands``, ``stroke_outline``) at ``fill-opacity``
    band × opacity, untextured; a dashed stroke one centreline path at its
    mean drawn width and opacity with ``stroke-dasharray``; a highlighter
    one constant-width path with its opacity, multiplied onto what lies
    beneath."""
    box = box or bounding_box(ink) or (0, 0, 1, 1)
    x0, y0, x1, y1 = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_fmt(x0)} {_fmt(y0)} {_fmt(w)} {_fmt(h)}" '
             f'width="{_fmt(w)}" height="{_fmt(h)}">']
    for stroke in in_draw_order(ink.strokes):
        r, g, b, a = parse_color(stroke.color)
        color = f"rgb({round(r * 255)},{round(g * 255)},{round(b * 255)})"
        alpha = min(a, stroke.opacity)
        pattern = dash_pattern(stroke)
        dash = f' stroke-dasharray="{_fmt(pattern[0])} {_fmt(pattern[1])}"' if pattern else ""
        kind = _drawn_as(stroke, alpha)
        if kind == "marker":
            parts.append(f'<path d="{_svg_line((x, y) for x, y, _ in stroke_polyline(stroke))}" fill="none" '
                         f'stroke="{color}" stroke-width="{_fmt(stroke.size)}" stroke-opacity="{_fmt(alpha)}" '
                         f'stroke-linecap="round" stroke-linejoin="round"{dash} style="mix-blend-mode:multiply"/>')
            continue
        if kind == "dashed":
            samples = decode_stroke(stroke)
            alpha *= _mean(s["o"] for s in samples if "o" in s)
            parts.append(f'<path d="{_svg_line((s["x"], s["y"]) for s in samples)}" fill="none" stroke="{color}" '
                         f'stroke-width="{_fmt(_mean(stroke_width(stroke, s) for s in samples))}"'
                         + (f' stroke-opacity="{_fmt(alpha)}"' if alpha < 1 else "")
                         + f' stroke-linecap="round" stroke-linejoin="round"{dash}/>')
            continue
        if kind == "bands":
            for band, outlines in _band_outlines(stroke, decode_stroke(stroke)):
                d = " ".join(p for outline in outlines if (p := _svg_outline(outline)))
                parts.append(f'<path d="{d}" fill="{color}"'
                             + (f' fill-opacity="{_fmt(band * alpha)}"' if band * alpha < 1 else "") + "/>")
            continue
        poly = stroke_polyline(stroke)
        parts.append(f'<g fill="none" stroke="{color}" stroke-linecap="round" stroke-linejoin="round">')
        if len(poly) == 1:
            x, y, wd = poly[0]
            parts.append(f'<path d="M{_fmt(x)} {_fmt(y)} L{_fmt(x + 0.01)} {_fmt(y)}" stroke-width="{_fmt(wd)}"/>')
        for (ax, ay, aw), (bx, by, bw) in zip(poly, poly[1:]):
            parts.append(f'<path d="M{_fmt(ax)} {_fmt(ay)} L{_fmt(bx)} {_fmt(by)}" '
                         f'stroke-width="{_fmt((aw + bw) / 2)}"/>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def _gs(alpha: float, multiply: bool = False) -> bytes:
    """The ``gs`` operator ``pdf_path_ops`` sets a drawn opacity with. The
    state is in its name — ``/GSink<α × 1000>`` (``/CA`` and ``/ca``) and
    ``/GSinkM<α × 1000>`` (also ``/BM /Multiply``) — so
    ``pdf_path_resources`` declares it from the ops alone."""
    return b"/GSink%s%d gs" % (b"M" if multiply else b"", max(0, min(1000, round(alpha * 1000))))


_GS_RE = re.compile(rb"/GSink(M?)(\d{1,4}) gs")


def _pdf_line(pts, num) -> bytes:
    return (b"%s %s m " % (num(pts[0][0]), num(pts[0][1]))
            + b" ".join(b"%s %s l" % (num(x), num(y)) for x, y in pts[1:] or pts[:1]))


def _pdf_outline(points, to_pdf) -> bytes:
    """An outline as one closed subpath: the client's quadratic curves
    through the midpoints, each as the cubic PDF draws. The points are
    mapped first: ``to_pdf`` is affine, so the curves map exactly."""
    if len(points) < 3:
        return b""
    pts = [to_pdf(x, y) for x, y in points]
    cur = pts[0]
    out = [f"{cur[0]:.2f} {cur[1]:.2f} m"]
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        ex, ey = (ax + bx) / 2, (ay + by) / 2
        out.append(f"{cur[0] + (ax - cur[0]) * 2 / 3:.2f} {cur[1] + (ay - cur[1]) * 2 / 3:.2f} "
                   f"{ex + (ax - ex) * 2 / 3:.2f} {ey + (ay - ey) * 2 / 3:.2f} {ex:.2f} {ey:.2f} c")
        cur = (ex, ey)
    return (" ".join(out) + " h").encode()


def pdf_path_ops(ink: InkFile, to_pdf, scale: float = 1.0, band: int | None = None) -> bytes:
    """PDF content-stream operators drawing the strokes, band 0 then band 1
    (``band`` keeps one, for a writer drawing several groups band by band);
    ``b""`` when nothing is left to draw. ``to_pdf(x, y)`` maps a
    display-space point to user space (the notes-as-PDF writer draws in a
    frame of its own) and ``scale`` the widths. An opaque solid pen is a
    round-capped segment per sample pair at its baked width; a translucent
    pen, and a pencil, one filled outline per opacity band under the
    ExtGState of its opacity (``/GSink<α>``, ``/CA`` and ``/ca``); a dashed
    stroke one path at its mean drawn width between ``[on off] 0 d`` and
    ``[] 0 d``; a highlighter its centreline under ``/GSinkM<α>``, which
    multiplies like the screen. A writer embedding the ops declares the
    states they set with ``pdf_path_resources``."""
    from .pdf_typeset import num
    strokes = [s for s in in_draw_order(ink.strokes) if band is None or band_of(s) == band]
    if not strokes:
        return b""
    ops = [b"q 1 J 1 j"]
    for stroke in strokes:
        r, g, b, a = parse_color(stroke.color)
        alpha = min(a, stroke.opacity)
        rgb = b"%s %s %s" % (num(r), num(g), num(b))
        pattern = dash_pattern(stroke)
        dash = b"[%s %s] 0 d" % tuple(_fmt(v * scale).encode() for v in pattern) if pattern else b""
        kind = _drawn_as(stroke, alpha)
        if kind == "marker":
            line = _pdf_line([to_pdf(x, y) for x, y, _ in stroke_polyline(stroke)], num) + b" S"
            ops.append(b"q %s %s RG %s w" % (_gs(alpha, multiply=True), rgb, num(stroke.size * scale)))
            ops += [dash, line, b"[] 0 d", b"Q"] if dash else [line, b"Q"]
            continue
        if kind == "dashed":
            samples = decode_stroke(stroke)
            alpha *= _mean(s["o"] for s in samples if "o" in s)
            width = _mean(stroke_width(stroke, s) for s in samples) * scale
            line = _pdf_line([to_pdf(s["x"], s["y"]) for s in samples], num) + b" S"
            body = [b"%s RG %s w %s" % (rgb, num(width), dash), line, b"[] 0 d"]
            ops += [b"q " + _gs(alpha), *body, b"Q"] if alpha < 1 else body
            continue
        if kind == "bands":
            for band_alpha, outlines in _band_outlines(stroke, decode_stroke(stroke)):
                shape = [p for outline in outlines if (p := _pdf_outline(outline, to_pdf))]
                if not shape:
                    continue
                body = [rgb + b" rg", *shape, b"f"]
                ops += [b"q " + _gs(band_alpha * alpha), *body, b"Q"] if band_alpha * alpha < 1 else body
            continue
        ops.append(rgb + b" RG")
        poly = stroke_polyline(stroke)
        for (ax, ay, aw), (bx, by, bw) in zip(poly, poly[1:] or poly):
            (px, py), (qx, qy) = to_pdf(ax, ay), to_pdf(bx, by)
            ops.append(b"%s w %s %s m %s %s l S" % (num((aw + bw) / 2 * scale), num(px), num(py), num(qx), num(qy)))
    ops.append(b"Q")
    return b"\n".join(ops)


def pdf_path_resources(ops: bytes):
    """The ``/ExtGState`` dictionary declaring every graphics state ``ops``
    sets — the output of ``pdf_path_ops``, or a whole content stream that
    holds it — or None when it sets none. The name carries the state
    (``_gs``)."""
    from PyPDF2.generic import DictionaryObject, FloatObject, NameObject
    found = sorted({(m.group(1), int(m.group(2))) for m in _GS_RE.finditer(ops)})
    if not found:
        return None
    out = DictionaryObject()
    for multiply, alpha in found:
        value = f"{alpha / 1000:g}"      # a string, or PyPDF2 writes the float's every binary digit
        state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"),
                                  NameObject("/CA"): FloatObject(value), NameObject("/ca"): FloatObject(value)})
        if multiply:
            state[NameObject("/BM")] = NameObject("/Multiply")
        out[NameObject(f"/GSink{multiply.decode()}{alpha}")] = state
    return out


# --- PDF /Ink interchange --------------------------------------------------------

def ink_buckets(ink: InkFile) -> list[list[Stroke]]:
    """Strokes grouped by look (tool, colour, size, opacity), in first-seen
    order: one ``/Ink`` annotation per bucket, since the PDF annotation has
    one width and one colour. The bucket's index is its identity (the note
    carrier, the Zotero ``/NM``); the writer orders band-0 buckets first."""
    buckets: dict[tuple, list[Stroke]] = {}
    for s in ink.strokes:
        buckets.setdefault((s.tool, s.color.lower(), round(s.size, 2), round(s.opacity, 3)), []).append(s)
    return list(buckets.values())


def from_pdf_ink(ink_list, width_pt: float, color: str, opacity: float, page: int,
                 page_w: float, page_h: float, private_json: str | None = None,
                 dash: str | None = None) -> dict:
    """An embedded ``/Ink`` annotation → an ink file dict. ``ink_list`` is the
    ``/InkList`` (paths of x y pairs in PDF user space, origin bottom-left);
    ``private_json`` is the ``/GammaInk`` string a Gamma export left behind,
    which wins when it parses (it carries pressure, time and the true dash
    of every stroke); one written before release T is a version 1 file and
    is upgraded first. ``dash`` is a foreign annotation's border style
    (``"dashed"`` for a ``/BS /S /D``), given to each of its strokes."""
    if private_json:
        try:
            obj = load_json(private_json)
            if obj.get("version", 1) == 1:
                obj = upgrade_v1(obj)
            ink = parse_ink(obj)
            if ink.space.kind == "pdf-page":
                ink.space.page = page
            return to_dict(ink)
        except InkError:
            pass
    strokes = []
    for n, path in enumerate(ink_list or []):
        nums = [float(v) for v in path]
        samples = [{"x": nums[i], "y": page_h - nums[i + 1]} for i in range(0, len(nums) - 1, 2)]
        if not samples:
            continue
        strokes.append({"id": f"i{n}", "tool": "pen", "pen": False, "color": color,
                        "size": max(0.2, min(100.0, width_pt or 1.0)), "opacity": max(0.05, min(1.0, opacity)),
                        **({"dash": dash} if dash in DASH else {}),
                        "ch": "xy", "pts": encode_points(samples, "xy")})
    return {"format": FORMAT, "version": VERSION,
            "space": {"kind": "pdf-page", "page": page, "width": page_w, "height": page_h},
            "strokes": strokes}


# --- canonical bytes ---------------------------------------------------------------

def to_dict(model: BaseModel) -> dict:
    """A model as the JSON object it was parsed from: the keys that were set
    (defaults never invented), ``from`` by its name, null keys dropped,
    unknown keys verbatim."""
    return model.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)


def _canon(value):
    """Whole-number floats as integers, null keys dropped, at every level —
    the client's ``serializeInk`` writes ``612``, never ``612.0``."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def dumps(ink: InkFile | dict) -> bytes:
    """Canonical bytes (equal to the client's ``serializeInk``): sorted keys
    at every level, no whitespace, null keys dropped, whole floats as
    integers, never NaN, non-ASCII text as UTF-8 (``JSON.stringify`` never
    escapes it, so an extra key holding "café" hashes the same on both
    sides). For the files the server writes itself — a merge, an import,
    the upgrade step; uploads are stored as they came."""
    data = to_dict(ink) if isinstance(ink, InkFile) else ink
    return json.dumps(_canon(data), separators=(",", ":"), sort_keys=True, allow_nan=False,
                      ensure_ascii=False).encode("utf-8")


# --- version 1 → 2 ---------------------------------------------------------------

def upgrade_v1(obj: dict) -> dict:
    """The one reader of a version 1 file (handwriting.md "Upgrading version
    1"), on the raw parsed object so no default is invented: ``version``
    becomes 2; a stroke whose tool is ``pen`` (or absent), ``pen`` not
    false, ``brush`` absent and ``ch`` has ``p`` but no ``w`` gains ``w``
    (appended to ``ch``) with ``500 + clamp(p, 0, 1000)`` per sample,
    absolute on the first and a delta after; everything else is kept. Any
    other version (2, a newer one, a malformed one) comes back as it is:
    the converter never rewrites what is not version 1, ``parse_ink``
    judges it. Deterministic; the output's ``dumps`` is pinned by
    tests/shared/inkv2.json."""
    if not isinstance(obj, dict):
        raise InkError("an ink file is a JSON object")
    if obj.get("version", 1) != 1:
        return obj
    out = {k: v for k, v in obj.items() if k != "version"}
    out["version"] = 2
    strokes = obj.get("strokes")
    if isinstance(strokes, list):
        out["strokes"] = [_upgrade_stroke(s) for s in strokes]
    return out


def _upgrade_stroke(s):
    if not isinstance(s, dict):
        return s
    ch = s.get("ch", "xy")
    if (s.get("tool", "pen") != "pen" or s.get("pen", True) is False or s.get("brush") is not None
            or not isinstance(ch, str) or "p" not in ch or "w" in ch or not isinstance(s.get("pts"), list)):
        return s
    pts, n, pi = s["pts"], len(ch), ch.index("p")
    if n == 0 or len(pts) % n:
        return s
    new, prev = [], None
    for i in range(0, len(pts), n):
        row = pts[i:i + n]
        p = row[pi]
        w = 500 + max(0, min(1000, int(p) if isinstance(p, (int, float)) and math.isfinite(p) else 0))
        new.extend(row)
        new.append(w if prev is None else w - prev)
        prev = w
    return {**s, "ch": ch + "w", "pts": new}


# --- three-way merge -------------------------------------------------------------

@dataclass
class MergeResult:
    ink: InkFile
    clean: bool
    lost_ours: list = field(default_factory=list)   # the writer's changes the result dropped
    returned: list = field(default_factory=list)    # strokes theirs had erased that come back

    def __iter__(self):  # (ink, clean) for the callers that unpack two
        yield self.ink
        yield self.clean

    def report(self) -> dict:
        return {"clean": self.clean, "lost_ours": list(self.lost_ours), "returned": list(self.returned)}


# A stroke's geometry: its samples and what they were made for. ``brush``
# is geometry too, since a brush change bakes the ``w`` in ``pts`` again:
# the key travels with the widths baked for it, so a merge never pairs one
# brush with another's widths.
_GEOM_KEYS = ("pts", "ch", "size", "shape", "brush")
_SKIP_LOOK = frozenset(("id", "from", *_GEOM_KEYS))


def _geom(s: Stroke) -> tuple:
    return (tuple(s.pts), s.ch, s.size, s.shape.model_dump() if s.shape else None, s.brush)


def _look(s: Stroke) -> tuple:
    extra = s.__pydantic_extra__ or {}
    return (s.tool, s.color, s.opacity, s.pen, s.t0, band_of(s),
            s.fill.model_dump() if s.fill else None, s.dash, s.nib,
            json.dumps(extra, sort_keys=True, separators=(",", ":")) if extra else "")


def _same(a: Stroke, b: Stroke) -> bool:
    return _geom(a) == _geom(b) and _look(a) == _look(b) and a.from_ == b.from_


def _compose(geom_src: Stroke, look_src: Stroke) -> Stroke:
    """One stroke from another's geometry and a third's look (the id and
    ``from`` are the same stroke's)."""
    g, lk = to_dict(geom_src), to_dict(look_src)
    d = {k: v for k, v in lk.items() if k not in _GEOM_KEYS}
    for k in _GEOM_KEYS:
        if k in g:
            d[k] = g[k]
    d["id"] = geom_src.id
    if geom_src.from_ or look_src.from_:
        d["from"] = geom_src.from_ or look_src.from_
    else:
        d.pop("from", None)
    return Stroke.model_validate(d)


def _with_look(target: Stroke, look_src: Stroke) -> Stroke:
    """``target`` wearing ``look_src``'s look, its own id, ``from`` and geometry."""
    lk = {k: v for k, v in to_dict(look_src).items() if k not in _SKIP_LOOK}
    d = {k: v for k, v in to_dict(target).items() if k in _SKIP_LOOK}
    return Stroke.model_validate({**d, **lk})


def _family(base_strokes: list, root: str, side: dict) -> list:
    """The base strokes of ``root``'s family (the stroke itself or a piece
    naming it) that ``side`` no longer has: the parents a cut replaced."""
    return [p for p in base_strokes if (p.id == root or p.from_ == root) and p.id not in side]


def _pieces(side_file: InkFile, b: dict, base_strokes: list) -> dict:
    """``{root id: [pieces]}`` of the strokes this side added as cuts of a
    base stroke (``from`` naming a family the side no longer has whole)."""
    out: dict = {}
    for s in side_file.strokes:
        if s.from_ and s.id not in b and _family(base_strokes, s.from_, {x.id for x in side_file.strokes}):
            out.setdefault(s.from_, []).append(s)
    return out


def merge_ink(base: InkFile | None, ours: InkFile, theirs: InkFile, base_ids=None) -> MergeResult:
    """Apply the change ``base → ours`` to ``theirs`` (the group as stored
    now), stroke by stroke (handwriting.md "Two writers, one group"). A
    stroke is two halves, its geometry (``pts``, ``ch``, ``size``,
    ``shape``, ``brush``) and its look (everything else but ``id`` and
    ``from``): a half one side changed is taken from it, a half both
    changed differently takes theirs and marks the merge unclean. An edit
    beats a delete. A piece (``from: X``) follows its parent's fate on the
    other side. With ``base`` None and ``base_ids`` (the base's stroke ids)
    a stroke one side lacks is erased; with neither it is a union by id.
    Returns the file, ``clean``, ``lost_ours`` (ours' changes dropped) and
    ``returned`` (strokes theirs erased that come back).
    frontend/src/ink/ink.js ``mergeInk`` is the same rule;
    tests/shared/inkmerge.json pins both."""
    if base is None and base_ids is not None:
        return _merge_by_ids(ours, theirs, set(base_ids))
    b_strokes = base.strokes if base is not None else []
    if base is not None and _equal(theirs.strokes, b_strokes):
        return MergeResult(ours, True)
    if _equal(ours.strokes, b_strokes) or _equal(ours.strokes, theirs.strokes):
        return MergeResult(theirs, True)
    b = {s.id: s for s in b_strokes}
    o = {s.id: s for s in ours.strokes}
    t = {s.id: s for s in theirs.strokes}
    clean, lost, returned = True, [], []
    our_pieces = _pieces(ours, b, b_strokes) if base is not None else {}
    their_pieces = _pieces(theirs, b, b_strokes) if base is not None else {}

    # the fate of ours' cuts, by root: what theirs did to the parents
    fate: dict = {}      # root -> ("theirs" | "look" | "erased" | "stand", look source)
    cut_parent: dict = {}  # parent id -> root, for the parents ours cut
    for root, pieces in our_pieces.items():
        states, look_src = set(), None
        for p in _family(b_strokes, root, o):
            cut_parent[p.id] = root
            tp = t.get(p.id)
            if tp is None:
                states.add("cut" if root in their_pieces else "erased")
            elif _geom(tp) != _geom(p):
                states.add("geom")
            elif _look(tp) != _look(p):
                states.add("look")
                look_src = look_src or tp
            else:
                states.add("stand")
        if states & {"cut", "geom"}:
            fate[root] = ("theirs", None)
            clean = False
        elif "look" in states:
            fate[root] = ("look", look_src)
        elif states == {"erased"}:
            fate[root] = ("erased", None)
        else:
            fate[root] = ("stand", None)

    # theirs' cuts: what ours did to their parents
    consumed: set = set()  # base ids theirs cut whose fate is settled here (never returned by ours)
    their_piece_look: dict = {}   # root -> ours' look to put on theirs' pieces
    their_piece_drop: set = set()  # roots whose pieces go (ours erased the drawing)
    for root, pieces in their_pieces.items():
        states, look_src = set(), None
        for p in _family(b_strokes, root, t):
            consumed.add(p.id)
            op = o.get(p.id)
            if op is None:
                states.add("cut" if root in our_pieces else "erased")
            elif _geom(op) != _geom(p):
                states.add("geom")
                lost.append(p.id)
            elif _look(op) != _look(p):
                states.add("look")
                look_src = look_src or op
            else:
                states.add("stand")
        if "geom" in states:
            clean = False
        elif "look" in states:
            their_piece_look[root] = look_src
        elif states == {"erased"}:
            their_piece_drop.add(root)

    out: list[Stroke] = []
    for tp in theirs.strokes:
        was, mine = b.get(tp.id), o.get(tp.id)
        if was is None:                  # theirs added it
            if tp.from_ in their_piece_drop and tp.id not in o:
                continue                 # a piece of a drawing ours undid
            if tp.from_ in their_piece_look and tp.id not in o:
                out.append(_with_look(tp, their_piece_look[tp.from_]))
                continue
            if mine is not None and not _same(mine, tp):
                clean = False
                lost.append(tp.id)
            out.append(tp)
        elif tp.id in cut_parent:        # ours cut it into pieces
            if fate[cut_parent[tp.id]][0] == "theirs":
                out.append(tp)           # theirs moved it: it stands, the pieces go
            continue                     # the pieces replace it (wearing theirs' look)
        elif mine is None:               # ours erased it
            if not _same(tp, was):
                clean = False            # theirs changed it: it stays
                lost.append(tp.id)
                out.append(tp)
        elif _same(mine, was) or _same(tp, mine):
            out.append(tp)
        elif _same(tp, was):
            out.append(mine)             # only ours changed it
        else:
            out.append(_merge_halves(was, mine, tp, lost))
            if lost and lost[-1] == tp.id:
                clean = False
    present = {s.id for s in out}
    anchor = None
    for s in ours.strokes:
        if s.id in present:
            anchor = s.id
            continue
        was = b.get(s.id)
        if was is not None:
            if _same(s, was) or s.id in consumed:
                continue                 # theirs erased (or cut) a stroke ours kept as it was
            clean = False                # ours changed what theirs erased: it comes back
            returned.append(s.id)
        elif s.from_ in fate:
            kind, look_src = fate[s.from_]
            if kind == "theirs":
                lost.append(s.id)
                continue
            if kind == "erased":
                continue
            if kind == "look":
                parent = next(p for p in b_strokes if p.id == s.from_ or p.from_ == s.from_)
                if _look(s) != _look(parent):
                    clean = False        # both restyled: theirs' look wins
                    lost.append(s.id)
                s = _with_look(s, look_src)
        at = next(i for i, x in enumerate(out) if x.id == anchor) + 1 if anchor else 0
        while at < len(out) and out[at].id not in b and out[at].id not in o:
            at += 1                      # theirs' own additions there come first
        out.insert(at, s)
        present.add(s.id)
        anchor = s.id
    return _result(out, ours, theirs, clean, lost, returned)


def _merge_halves(was: Stroke, mine: Stroke, tp: Stroke, lost: list) -> Stroke:
    """Both sides changed one stroke: each half from the side that changed
    it, theirs where both did (``lost`` then names the stroke)."""
    g_o, g_t = _geom(mine) != _geom(was), _geom(tp) != _geom(was)
    l_o, l_t = _look(mine) != _look(was), _look(tp) != _look(was)
    conflict = (g_o and g_t and _geom(mine) != _geom(tp)) or (l_o and l_t and _look(mine) != _look(tp))
    if conflict:
        lost.append(tp.id)
    geom_src = mine if g_o and not (g_t and _geom(mine) != _geom(tp)) else tp
    look_src = mine if l_o and not (l_t and _look(mine) != _look(tp)) else tp
    if geom_src is tp and look_src is tp:
        return tp
    if geom_src is mine and look_src is mine:
        return mine
    return _compose(geom_src, look_src)


def _merge_by_ids(ours: InkFile, theirs: InkFile, base_ids: set) -> MergeResult:
    """The base file is gone but its stroke ids are known: a stroke one side
    lacks whose id the base had was erased by that side — deletions win
    both ways — and the merge is unclean when one was applied; the rest is
    the union by id."""
    o = {s.id: s for s in ours.strokes}
    t = {s.id: s for s in theirs.strokes}
    clean, lost = True, []
    out: list[Stroke] = []
    for tp in theirs.strokes:
        mine = o.get(tp.id)
        if tp.id in base_ids and mine is None:
            clean = False                # ours erased it
            continue
        if mine is not None and not _same(mine, tp):
            clean = False
            lost.append(tp.id)
        out.append(tp)
    present = {s.id for s in out}
    anchor = None
    for s in ours.strokes:
        if s.id in present:
            anchor = s.id
            continue
        if s.id in base_ids:
            clean = False                # theirs erased it: it stays gone
            lost.append(s.id)
            continue
        at = next(i for i, x in enumerate(out) if x.id == anchor) + 1 if anchor else 0
        while at < len(out) and out[at].id not in base_ids and out[at].id not in o:
            at += 1
        out.insert(at, s)
        present.add(s.id)
        anchor = s.id
    return _result(out, ours, theirs, clean, lost, [])


def _equal(a: list, b: list) -> bool:
    return len(a) == len(b) and all(x.id == y.id and _same(x, y) for x, y in zip(a, b))


def _result(out: list, ours: InkFile, theirs: InkFile, clean: bool, lost: list, returned: list) -> MergeResult:
    # a merge that changes nothing is that file itself (a change sent again)
    if _equal(out, theirs.strokes):
        return MergeResult(theirs, clean, lost, returned)
    if _equal(out, ours.strokes) and ours.space == theirs.space:
        return MergeResult(ours, clean, lost, returned)
    merged = InkFile.model_validate({**to_dict(ours), "space": to_dict(theirs.space),
                                     "strokes": [to_dict(s) for s in out]})
    return MergeResult(merged, clean, lost, returned)
