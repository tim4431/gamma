"""The Sketchbook: the notebook every new account starts with beside the
Welcome page (docs/dev/onboarding.md "The Sketchbook"). Four sheets of
paper with hand-drawn steps on what to try with the pen: writing, the pen
styles, the highlighter, the eraser, the lasso, undo, draw and hold, the
Text tool, the paper, replay, the two views and new pages. Each step with a
control draws that control as the app shows it, with the app's own icons,
and circles the button to press.

Everything on it is ordinary: each sheet is a sheet block, its drawings
one ink group (a gamma-ink v2 file in the sheet's ``canvas`` frame), its
notes text boxes. So the user erases, lassos and draws over the guide with
the real tools, and deletes it like any page.

The drawings are made here, as code, so there is no binary asset to keep in
step: each line is SVG path data (``M L H V C S Q Z``, upper or lower case)
sampled into a stroke. A pen line presses in and lifts off as a stylus
does: ``p``, and the plain Pen's ``w = 1 + (p − 0.5)`` from the brush table
(docs/dev/handwriting.md), so a restyle bakes the same widths again. Every
stroke is timed along a made-up afternoon, so a sheet's replay draws it
again; the afternoon is in the past, so no recording ever plays it. The
output is deterministic: every workspace stores the same files.
"""

import functools
import math
import re

from .ink import InkFile, dumps, encode_points, group_files, parse_ink
from .notebook import normalize_paper
from .text_box import keep_on_surface, measure, normalize_text_box

TITLE = "Sketchbook"

# Colours: a soft black for outlines and text, the accents, and the
# highlighter tints the drawings are coloured in with (multiplied onto the
# paper under the ink, as a highlighter is).
INK = "#2d2a32"
MUTED = "#6b6573"
CORAL = "#f0525a"
BLUE = "#2f7fd8"
GREEN = "#1f9e74"
PURPLE = "#7b5cd6"
ORANGE = "#f08c24"
YELLOW_HI = "#ffd43b"
PINK_HI = "#ff9fb8"
GREEN_HI = "#9be6b8"
ORANGE_HI = "#ffc078"
PURPLE_HI = "#d0bfff"

# The afternoon the drawings were "written" (2026-01-01 14:00 UTC), and
# the pace: the replay reads the times between samples and strokes.
T0 = 1767276000000
SPEED = 170.0       # pt per second along a line
LETTER_SPEED = 240.0
GAP = 180           # ms between the strokes of one drawing
PAUSE = 650         # ms between drawings
STEP = 2.0          # pt between samples along a line


# ---------------------------------------------------------------- paths

_TOKEN = re.compile(r"[A-Za-z]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _cubic(p0, p1, p2, p3, step):
    reach = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    n = max(2, math.ceil(reach / step))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _line(p0, p1, step):
    n = max(1, math.ceil(math.dist(p0, p1) / step))
    return [(p0[0] + (p1[0] - p0[0]) * i / n, p0[1] + (p1[1] - p0[1]) * i / n) for i in range(1, n + 1)]


def _arc(p0, rx: float, ry: float, phi: float, large: bool, sweep: bool, p1, step: float):
    """An elliptical arc from ``p0`` to ``p1`` as SVG draws it (SVG 1.1
    F.6.5: the endpoints to a centre, the radii grown when too small)."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if not rx or not ry:
        return _line(p0, p1, step)
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    grow = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if grow > 1:
        rx, ry = rx * math.sqrt(grow), ry * math.sqrt(grow)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    k = math.sqrt(max(0.0, num / (rx * rx * y1 * y1 + ry * ry * x1 * x1))) * (-1 if large == sweep else 1)
    ccx, ccy = k * rx * y1 / ry, -k * ry * x1 / rx
    cx, cy = c * ccx - s * ccy + (p0[0] + p1[0]) / 2, s * ccx + c * ccy + (p0[1] + p1[1]) / 2
    a0 = math.atan2((y1 - ccy) / ry, (x1 - ccx) / rx)
    turn = math.atan2((-y1 - ccy) / ry, (-x1 - ccx) / rx) - a0
    if sweep and turn < 0:
        turn += 2 * math.pi
    elif not sweep and turn > 0:
        turn -= 2 * math.pi
    n = max(2, math.ceil(abs(turn) * max(rx, ry) / step))
    out = []
    for i in range(1, n + 1):
        a = a0 + turn * i / n
        out.append((cx + rx * math.cos(a) * c - ry * math.sin(a) * s, cy + rx * math.cos(a) * s + ry * math.sin(a) * c))
    out[-1] = p1
    return out


def flatten(d: str, step: float = STEP) -> list[list[tuple[float, float]]]:
    """SVG path data as polylines, one per subpath (each ``M`` starts one),
    sampled every ``step`` units or closer: a drawing scaled by k on the page
    passes ``STEP / k``."""
    tokens = _TOKEN.findall(d)
    paths: list[list] = []
    cur = start = (0.0, 0.0)
    ctrl = None  # the last cubic's second control point, which S mirrors
    cmd = ""
    i = 0

    def nums(k):
        nonlocal i
        vals = [float(v) for v in tokens[i:i + k]]
        i += k
        return vals

    def flag():
        # an arc's flag is one digit, and minified data runs it into what follows ("a2 2 0 012-2")
        nonlocal i
        tok = tokens[i]
        if len(tok) > 1:
            tokens[i] = tok[1:]
        else:
            i += 1
        return tok[0] == "1"

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if paths:
                    paths[-1] += _line(cur, start, step)
                cur, ctrl = start, None
                continue
        ox, oy = cur if cmd.islower() else (0.0, 0.0)
        c = cmd.upper()
        if c == "M":
            x, y = nums(2)
            cur = start = (ox + x, oy + y)
            paths.append([cur])
            cmd = "l" if cmd == "m" else "L"  # further pairs draw lines
            ctrl = None
        elif c in "LHV":
            if c == "L":
                x, y = nums(2)
                nxt = (ox + x, oy + y)
            elif c == "H":
                nxt = (ox + nums(1)[0], cur[1])
            else:
                nxt = (cur[0], oy + nums(1)[0])
            paths[-1] += _line(cur, nxt, step)
            cur, ctrl = nxt, None
        elif c in "CS":
            if c == "C":
                x1, y1, x2, y2, x, y = nums(6)
                p1 = (ox + x1, oy + y1)
            else:
                x2, y2, x, y = nums(4)
                p1 = (2 * cur[0] - ctrl[0], 2 * cur[1] - ctrl[1]) if ctrl else cur
            p2, nxt = (ox + x2, oy + y2), (ox + x, oy + y)
            paths[-1] += _cubic(cur, p1, p2, nxt, step)
            cur, ctrl = nxt, p2
        elif c == "Q":
            x1, y1, x, y = nums(4)
            q, nxt = (ox + x1, oy + y1), (ox + x, oy + y)
            paths[-1] += _cubic(cur, (cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1])),
                                (nxt[0] + 2 / 3 * (q[0] - nxt[0]), nxt[1] + 2 / 3 * (q[1] - nxt[1])), nxt, step)
            cur, ctrl = nxt, None
        elif c == "A":
            rx, ry, phi = nums(3)
            large, sweep = flag(), flag()
            x, y = nums(2)
            nxt = (ox + x, oy + y)
            paths[-1] += _arc(cur, rx, ry, phi, large, sweep, nxt, step)
            cur, ctrl = nxt, None
        else:
            raise ValueError(f"path command {cmd!r} is not drawn here")
    return paths


def ellipse(cx: float, cy: float, rx: float, ry: float, start: float = -100.0, sweep: float = 370.0,
            wobble: float = 0.0, step: float = STEP):
    """A hand-drawn ellipse as one polyline: begun at ``start`` degrees
    (clockwise from 3 o'clock) and carried ``sweep`` degrees, a little past
    closed, as a pen closes a circle; ``wobble`` (a share of the radius)
    makes it a shaky one."""
    n = max(8, math.ceil(math.pi * (rx + ry) * abs(sweep) / 360 / step))
    out = []
    for k in range(n + 1):
        a = math.radians(start + sweep * k / n)
        r = 1 + wobble * (math.sin(3 * a + 1) * 0.6 + math.sin(5 * a + 2) * 0.4) + wobble * k / n
        out.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return out


def _placed(polys, at=(0.0, 0.0), scale=1.0, turn=0.0):
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    return [[(at[0] + scale * (x * c - y * s), at[1] + scale * (x * s + y * c)) for x, y in poly] for poly in polys]


# ---------------------------------------------------------------- lettering

# A single-line capital alphabet for the headings: path data in a box 100
# units tall (the baseline at 100), with each glyph's advance.
GLYPHS = {
    "A": (60, "M0 100 L30 0 L60 100 M12 62 L48 62"),
    "B": (54, "M0 100 L0 0 L26 0 C50 0 50 46 26 46 L0 46 M26 46 C56 46 56 100 26 100 L0 100"),
    "C": (60, "M58 18 C50 4 38 0 30 0 C8 0 0 26 0 50 C0 76 10 100 32 100 C44 100 54 92 60 80"),
    "D": (58, "M0 0 L0 100 L20 100 C64 100 64 0 20 0 Z"),
    "E": (50, "M50 0 L0 0 L0 100 L50 100 M0 50 L40 50"),
    "F": (48, "M48 0 L0 0 L0 100 M0 50 L38 50"),
    "G": (62, "M58 18 C50 4 38 0 30 0 C8 0 0 26 0 50 C0 76 10 100 32 100 C52 100 62 86 62 64 L62 56 L36 56"),
    "H": (56, "M0 0 L0 100 M56 0 L56 100 M0 50 L56 50"),
    "I": (14, "M7 0 L7 100"),
    "J": (46, "M46 0 L46 70 C46 92 36 100 24 100 C10 100 2 92 0 78"),
    "K": (54, "M0 0 L0 100 M52 0 L0 62 M18 42 L56 100"),
    "L": (46, "M0 0 L0 100 L46 100"),
    "M": (70, "M0 100 L0 0 L35 66 L70 0 L70 100"),
    "N": (58, "M0 100 L0 0 L58 100 L58 0"),
    "O": (64, "M32 0 C8 0 0 26 0 50 C0 76 8 100 32 100 C56 100 64 76 64 50 C64 24 56 0 32 0 Z"),
    "P": (52, "M0 100 L0 0 L26 0 C56 0 56 52 26 52 L0 52"),
    "Q": (66, "M32 0 C8 0 0 26 0 50 C0 76 8 100 32 100 C56 100 64 76 64 50 C64 24 56 0 32 0 Z M38 72 L66 104"),
    "R": (54, "M0 100 L0 0 L26 0 C56 0 56 52 26 52 L0 52 M22 52 L54 100"),
    "S": (54, "M52 14 C46 4 36 0 26 0 C10 0 2 10 2 24 C2 52 54 44 54 74 C54 92 42 100 26 100 C12 100 4 94 0 84"),
    "T": (56, "M0 0 L56 0 M28 0 L28 100"),
    "U": (56, "M0 0 L0 66 C0 90 12 100 28 100 C44 100 56 90 56 66 L56 0"),
    "V": (60, "M0 0 L30 100 L60 0"),
    "W": (84, "M0 0 L20 100 L42 28 L64 100 L84 0"),
    "X": (56, "M0 0 L56 100 M56 0 L0 100"),
    "Y": (58, "M0 0 L29 50 L58 0 M29 50 L29 100"),
    "Z": (54, "M0 0 L54 0 L0 100 L54 100"),
    "0": (56, "M28 0 C6 0 0 26 0 50 C0 76 6 100 28 100 C50 100 56 76 56 50 C56 24 50 0 28 0 Z"),
    "1": (34, "M4 18 L24 0 L24 100"),
    "2": (54, "M2 22 C6 8 16 0 28 0 C44 0 54 10 54 26 C54 52 0 70 0 100 L56 100"),
    "3": (54, "M4 10 C10 4 18 0 28 0 C46 0 52 12 52 24 C52 40 40 48 24 48 C44 48 56 58 56 74 "
              "C56 92 42 100 28 100 C14 100 4 94 0 86"),
    "4": (58, "M44 100 L44 0 L0 70 L58 70"),
    "5": (54, "M50 0 L8 0 L4 44 C12 38 20 36 28 36 C46 36 56 50 56 68 C56 88 42 100 26 100 C14 100 4 94 0 86"),
    "6": (56, "M48 6 C42 2 36 0 30 0 C10 0 0 24 0 56 C0 86 12 100 30 100 C46 100 56 88 56 70 "
              "C56 52 44 40 30 40 C16 40 4 50 2 62"),
    "7": (54, "M0 0 L54 0 L18 100"),
    "8": (56, "M28 48 C10 48 4 38 4 24 C4 10 14 0 28 0 C42 0 52 10 52 24 C52 38 46 48 28 48 "
              "C10 48 0 60 0 74 C0 90 12 100 28 100 C44 100 56 90 56 74 C56 60 46 48 28 48 Z"),
    "9": (56, "M54 38 C52 50 42 60 26 60 C12 60 0 48 0 30 C0 12 12 0 28 0 C46 0 56 14 56 44 "
              "C56 76 46 100 26 100 C18 100 12 98 6 94"),
    "!": (12, "M6 0 L6 68 M6 94 L6 96"),
    "?": (52, "M2 22 C4 8 14 0 28 0 C44 0 52 10 52 24 C52 42 28 44 28 64 L28 70 M28 94 L28 96"),
    "'": (8, "M5 0 L3 26"),
    ",": (8, "M5 90 L2 108"),
    ".": (8, "M4 94 L4 96"),
    "&": (60, "M60 100 L14 38 C4 24 10 2 28 2 C42 2 48 18 40 30 L10 64 C-2 80 8 100 28 100 C42 100 52 90 60 70"),
    "/": (40, "M40 0 L0 100"),
}
LETTER_GAP = 20     # units between glyphs
WORD_GAP = 46       # a space's advance


def text_width(text: str, cap: float) -> float:
    """How wide ``Sheet.letters`` writes ``text`` at a cap height of ``cap``."""
    units = sum(WORD_GAP if ch == " " else GLYPHS[ch][0] + LETTER_GAP for ch in text) - LETTER_GAP
    return units * cap / 100


# ---------------------------------------------------------------- a sheet

class Sheet:
    """One sheet of the Sketchbook being drawn: its paper, its strokes in
    the order they are drawn (each timed after the one before) and its text
    boxes. ``d`` is path data or a list of polylines; ``at``, ``scale`` and
    ``turn`` (degrees, clockwise) place it."""

    def __init__(self, title: str, paper: dict, start: int):
        self.title = title
        self.paper = normalize_paper(paper)
        self.strokes: list[dict] = []
        self.boxes: list[dict] = []
        self.clock = start

    def pen(self, d, color: str = INK, size: float = 2.2, at=(0.0, 0.0), scale: float = 1.0, turn: float = 0.0,
            speed: float = SPEED):
        """A Pen line for each subpath, pressed in and lifted off."""
        for poly in _placed(flatten(d, STEP / scale) if isinstance(d, str) else d, at, scale, turn):
            self._add(poly, {"color": color, "size": size}, pressure=True, speed=speed)
        self.clock += PAUSE - GAP

    def even(self, d, color: str = INK, size: float = 2.0, dash: str | None = None, at=(0.0, 0.0),
             scale: float = 1.0, turn: float = 0.0):
        """A Ballpoint line of one width for each subpath: solid, dashed or
        dotted."""
        style = {"brush": "monoline", "color": color, "size": size, "pen": False, "dash": dash}
        for poly in _placed(flatten(d, STEP / scale) if isinstance(d, str) else d, at, scale, turn):
            self._add(poly, {k: v for k, v in style.items() if v is not None}, pressure=False)
        self.clock += PAUSE - GAP

    def marker(self, d, color: str = YELLOW_HI, size: float = 14.0, at=(0.0, 0.0), scale: float = 1.0,
               turn: float = 0.0):
        """A highlighter line: drawn under the ink, multiplied onto the paper."""
        for poly in _placed(flatten(d, STEP / scale) if isinstance(d, str) else d, at, scale, turn):
            self._add(poly, {"tool": "highlighter", "color": color, "size": size, "opacity": 0.6}, pressure=False)
        self.clock += PAUSE - GAP

    def letters(self, text: str, x: float, y: float, cap: float, color: str = INK, size: float = 3.0):
        """``text`` in the capital alphabet, top-left at (x, y), each glyph
        a little turned and lifted off the line, as by hand."""
        k = cap / 100
        for n, ch in enumerate(text):
            if ch == " ":
                x += WORD_GAP * k
                continue
            advance, d = GLYPHS[ch]
            centred = [[(px - advance / 2, py - 50) for px, py in poly] for poly in flatten(d, STEP / k)]
            turn = 3.0 * math.sin(n * 2.1 + len(text))
            at = (x + advance * k / 2, y + 50 * k + cap * 0.035 * math.sin(n * 1.7 + 0.5))
            for poly in _placed(centred, at, k, turn):
                self._add(poly, {"color": color, "size": size}, pressure=True, speed=LETTER_SPEED)
            x += (advance + LETTER_GAP) * k
        self.clock += PAUSE - GAP

    def note(self, md: str, x: float, y: float, w: float, size: float = 12, color: str = INK, bg: str | None = None):
        """A text box ``w`` wide at (x, y), as tall as its text."""
        box = normalize_text_box({"x": x, "y": y, "w": w, "auto": False, "size": size, "color": color, "bg": bg})
        box["h"] = measure(md, box, self.paper["width"])[1]
        self.boxes.append({"content": md, "text_box": keep_on_surface(box, self.paper["width"], self.paper["height"])})

    def _add(self, poly, style: dict, pressure: bool, speed: float = SPEED):
        arcs = [0.0]
        for a, b in zip(poly, poly[1:]):
            arcs.append(arcs[-1] + math.dist(a, b))
        length = arcs[-1]
        duration = max(60.0, 1000 * length / speed)
        samples = []
        for (x, y), s in zip(poly, arcs):
            sample = {"x": x, "y": y, "t": duration * s / length if length else 0}
            if pressure:
                # In over the first few points, out over the last ones, a
                # slow swell between.
                attack, release = min(5.0, length / 3), min(9.0, length / 3)
                p = 0.62 + 0.06 * math.sin(s / 17)
                if s < attack:
                    p = 0.3 + (p - 0.3) * math.sin(math.pi / 2 * s / attack)
                if length - s < release:
                    p = 0.22 + (p - 0.22) * math.sin(math.pi / 2 * (length - s) / release)
                milli = round(p * 1000)
                sample.update(p=milli / 1000, w=(500 + milli) / 1000)  # the Pen's 1 + (p − 0.5)
            samples.append(sample)
        ch = "xyptw" if pressure else "xyt"
        self.strokes.append({**style, "t0": self.clock, "ch": ch, "pts": encode_points(samples, ch)})
        self.clock += round(duration) + GAP

    def ink_files(self) -> list[InkFile]:
        """The sheet's drawings as gamma-ink v2 files, checked as an upload
        is (from their bytes, so ``MAX_BYTES`` too)."""
        space = {"kind": "canvas", "width": self.paper["width"], "height": self.paper["height"]}
        return [parse_ink(dumps(file)) for file in group_files(self.strokes, space)]


# ---------------------------------------------------------------- drawings

def _arrow(s: Sheet, d: str, color: str = INK, size: float = 2.0, head: float = 9.0, dash: str | None = None):
    """A line with a head at its end, pointing along its last stretch."""
    [poly] = flatten(d)
    end = poly[-1]
    back = next((p for p in reversed(poly) if math.dist(p, end) >= head), poly[0])
    ang = math.atan2(end[1] - back[1], end[0] - back[0])
    wings = [(end[0] - head * math.cos(ang + a), end[1] - head * math.sin(ang + a)) for a in (0.5, -0.5)]
    if dash:
        s.even([poly], color, size, dash)
    else:
        s.pen([poly], color, size)
    s.pen([[wings[0], end, wings[1]]], color, size)


def _star(s: Sheet, cx: float, cy: float, r: float, color: str = ORANGE, fill: str = YELLOW_HI):
    pts = [(cx + (r if k % 2 == 0 else r * 0.45) * math.sin(math.pi * k / 5),
            cy - (r if k % 2 == 0 else r * 0.45) * math.cos(math.pi * k / 5)) for k in range(11)]
    s.pen([pts], color, 1.8)
    s.marker([[(cx - r * 0.15, cy + r * 0.05), (cx + r * 0.15, cy + r * 0.05)]], fill, r * 1.1)


def _title(s: Sheet, text: str, color: str, swash: str, cap: float = 44):
    """A sheet's title in big capitals over a highlighter swash."""
    s.letters(text, 46, 48, cap, color, 4.2)
    right = 46 + text_width(text, cap)
    s.marker(f"M40 {48 + cap * 0.84} C{right * 0.4} {44 + cap * 0.74} {right * 0.7} {50 + cap * 0.92} "
             f"{right + 10} {46 + cap * 0.8}", swash, cap * 0.38)


def _heading(s: Sheet, n: int, text: str, y: float, color: str):
    """A step: its number in a circle, its title in capitals beside it."""
    s.pen([ellipse(59, y + 9, 15, 14.5, -120, 380)], color, 2.4)
    s.letters(str(n), 59 - text_width(str(n), 15) / 2, y + 1.5, 15, color, 2.4)
    s.letters(text, 86, y, 18, color, 2.6)


def _scribble(s: Sheet, cx: float, cy: float, w: float, h: float):
    """A tangle of loops, dense enough to be worth erasing."""
    pts = [(cx + w / 2 * (0.62 * math.sin(t * 1.3) + 0.38 * math.sin(t * 4.1 + 1)),
            cy + h / 2 * (0.55 * math.cos(t * 1.9) + 0.45 * math.sin(t * 5.3)))
           for t in (k * 0.035 for k in range(520))]
    s.pen([pts], INK, 1.8)


# ---------------------------------------------------------------- the app's controls, drawn

# The app's own glyphs, copied from frontend/src/shared/ui/Icons.jsx (the
# children of each icon's 24-unit <svg>; test_sketchbook.py holds them to
# it), so a drawn button is the button on screen.
ICONS = {
    "PenIcon": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 '
               '.623.622l4.353-1.32a2 2 0 0 0 .83-.497z" />',
    "HighlightIcon": '<path d="m9 11-4 4v3h3l4-4" /><path d="m13 7 4 4" /><path d="M15 3.5 20.5 9 13 16.5 7.5 11z" />'
                     '<path d="M4 21h16" />',
    "EraserIcon": '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21" />'
                  '<path d="M22 21H7" /><path d="m5 11 9 9" />',
    "LassoIcon": '<path d="M7 22a5 5 0 0 1-2-4" /><path d="M7 16.93c.96.43 1.96.74 2.99.91" />'
                 '<path d="M3.34 14A6.8 6.8 0 0 1 2 10c0-4.42 4.48-8 10-8s10 3.58 10 8a7.19 7.19 0 0 1-.33 2" />'
                 '<path d="M5 18a2 2 0 1 0 0-4 2 2 0 0 0 0 4z" />'
                 '<path d="M14.33 22h-.09a.35.35 0 0 1-.24-.32v-10a.34.34 0 0 1 .33-.34c.08 0 .15.03.21.08l7.34 '
                 '6a.33.33 0 0 1-.21.59h-4.49l-2.57 3.85a.35.35 0 0 1-.28.14z" />',
    "TypeIcon": '<path d="M4 7V5h16v2" /><path d="M12 5v14" /><path d="M9 19h6" />',
    "HandIcon": '<path d="M18 11V6a2 2 0 0 0-4 0v5" /><path d="M14 10V4a2 2 0 0 0-4 0v2" />'
                '<path d="M10 10.5V6a2 2 0 0 0-4 0v8" /><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 '
                '0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />',
    "MoreIcon": '<circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /><circle cx="5" cy="12" r="1" />',
    "XIcon": '<path d="M18 6 6 18" /><path d="m6 6 12 12" />',
    "UndoIcon": '<path d="m9 4-5 5 5 5" /><path d="M4 9h10a6 6 0 0 1 0 12" />',
    "RedoIcon": '<path d="m15 4 5 5-5 5" /><path d="M20 9H10a6 6 0 0 0 0 12" />',
    "ZoomOutIcon": '<circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /><path d="M8 11h6" />',
    "ZoomInIcon": '<circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /><path d="M8 11h6" /><path d="M11 8v6" />',
    "FitWidthIcon": '<path d="M3 5v14" /><path d="M21 5v14" /><path d="M7 12h10" /><path d="m9 9-3 3 3 3" />'
                    '<path d="m15 9 3 3-3 3" />',
    "MicIcon": '<path d="M12 19v3" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><rect x="9" y="2" width="6" height="12" rx="3" />',
    "SheetIcon": '<rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8" /><path d="M8 12h8" />'
                 '<path d="M8 16h8" />',
    "FileTextIcon": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />'
                    '<polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" />'
                    '<line x1="16" y1="17" x2="8" y2="17" />',
    "NotebookIcon": '<path d="M2 6h4" /><path d="M2 10h4" /><path d="M2 14h4" /><path d="M2 18h4" />'
                    '<rect width="16" height="20" x="4" y="2" rx="2" /><path d="M16 2v20" />',
    "PlayIcon": '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" />',
    "PlusIcon": '<path d="M12 5v14" /><path d="M5 12h14" />',
    "ActivityIcon": '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 '
                    '0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" />',
    "SearchIcon": '<circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />',
    "BellIcon": '<path d="M10.27 21a2 2 0 0 0 3.46 0" /><path d="M3.26 15.33A1 1 0 0 0 4 17h16a1 1 0 0 0 '
                '.74-1.67C19.41 13.96 18 12.5 18 8A6 6 0 0 0 6 8c0 4.5-1.41 5.96-2.74 7.33" />',
    "UserIcon": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />',
    "MenuIcon": '<line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="20" y2="12" />'
                '<line x1="4" y1="18" x2="20" y2="18" />',
    "ChevronDownIcon": '<path d="m6 9 6 6 6-6" />',
}
FILLED = frozenset({"PlayIcon"})  # drawn filled on screen: a heavy line stands in for the fill

_ELEMENT = re.compile(r"<(path|circle|rect|line|polyline)\b([^>]*?)/>")
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')

# The controls are drawn at about the size the notes view shows them: K
# points to a CSS pixel, with the app's 31 px buttons and 16 px icons.
K = 0.7
BTN = 31 * K
ICON = 16 * K
UI_FRAME = "#c9cdd3"     # a bar's outline, a separator
UI_ICON = "#3a3a40"
UI_QUIET = "#b4b8be"     # a control greyed out, or one the picture leaves unnamed
UI_PRESSED = "#e2e5e9"   # a pressed button's ground
UI_ARMED = "#2f6fd6"     # the armed pen
RING = CORAL


def rounded(x: float, y: float, w: float, h: float, r: float) -> str:
    """A rounded rectangle as path data."""
    r = min(r, w / 2, h / 2)
    return (f"M{x + r} {y} H{x + w - r} A{r} {r} 0 0 1 {x + w} {y + r} V{y + h - r} A{r} {r} 0 0 1 {x + w - r} {y + h} "
            f"H{x + r} A{r} {r} 0 0 1 {x} {y + h - r} V{y + r} A{r} {r} 0 0 1 {x + r} {y} Z")


def icon_polys(markup: str, step: float = STEP) -> list[list[tuple[float, float]]]:
    """An icon's SVG children (path, circle, rect, line, polyline) as
    polylines in its 24-unit square, sampled every ``step`` units."""
    out = []
    for tag, attrs in _ELEMENT.findall(markup):
        a = dict(_ATTR.findall(attrs))

        def num(key):
            return float(a.get(key, 0))
        if tag == "path":
            out += flatten(a["d"], step)
        elif tag == "circle":
            out.append(ellipse(num("cx"), num("cy"), num("r"), num("r"), 0, 360, step=step))
        elif tag == "rect":
            out += flatten(rounded(num("x"), num("y"), num("width"), num("height"), num("rx")), step)
        elif tag == "line":
            out += flatten(f"M{a['x1']} {a['y1']} L{a['x2']} {a['y2']}", step)
        else:
            out += flatten("M" + a["points"], step)
    return out


def _icon(s: Sheet, name: str, cx: float, cy: float, color: str = UI_ICON, side: float = ICON):
    """One of the app's icons centred on (cx, cy), its line as heavy as on
    screen (1.6 px at 16)."""
    k = side / 24
    polys = [[(cx + (x - 12) * k, cy + (y - 12) * k) for x, y in poly] for poly in icon_polys(ICONS[name], 0.8 / k)]
    s.even(polys, color, side * (0.2 if name in FILLED else 0.1))


def _fill(s: Sheet, x0: float, y0: float, x1: float, y1: float, color: str = UI_PRESSED):
    """A rounded patch of ground: one opaque zigzag, drawn before what
    stands on it."""
    w = min(7.0, y1 - y0, x1 - x0)
    rows = max(1, math.ceil((y1 - y0 - w) / (w * 0.8)))
    ys = [y0 + w / 2 + (y1 - y0 - w) * i / rows for i in range(rows + 1)]
    pts = []
    for i, y in enumerate(ys):
        pts += [(x0 + w / 2, y), (x1 - w / 2, y)] if i % 2 == 0 else [(x1 - w / 2, y), (x0 + w / 2, y)]
    s.even([pts], color, w)


def _label(s: Sheet, text: str, x: float, y: float, color: str = RING, cap: float = 8.0):
    s.letters(text, x, y, cap, color, 1.6)


def _ring(s: Sheet, points, label: str = "", side: str = "below", cap: float = 8.0):
    """The red circle round the button or buttons centred at ``points``, as
    a pen circles a control on a printout, with ``label`` lettered on
    ``side`` of it."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    rx, ry = (max(xs) - min(xs)) / 2 + BTN * 0.62 + 3, (max(ys) - min(ys)) / 2 + BTN * 0.5 + 3
    s.pen([ellipse(cx, cy, rx, ry, -125, 378)], RING, 2.0)
    if label:
        w = text_width(label, cap)
        x, y = {"below": (cx - w / 2, cy + ry + 5), "above": (cx - w / 2, cy - ry - 5 - cap),
                "left": (cx - rx - 6 - w, cy - cap / 2), "right": (cx + rx + 6, cy - cap / 2)}[side]
        _label(s, label, x, y, cap=cap)


# The strip's buttons (MarkupToolbar) in order, "|" a separator: the four
# pens and three highlighters to start, each over its colour bar.
STRIP = (("pen1", "PenIcon", "#1f1f1f"), ("pen2", "PenIcon", "#1f4fd8"), ("pen3", "PenIcon", "#d62828"),
         ("pen4", "PenIcon", "#18794e"), ("hl1", "HighlightIcon", "#ffe08a"), ("hl2", "HighlightIcon", "#b2f2bb"),
         ("hl3", "HighlightIcon", "#fcc2d7"), "|", ("eraser", "EraserIcon", None), ("lasso", "LassoIcon", None),
         ("text", "TypeIcon", None), "|", ("hand", "HandIcon", None), "|", ("more", "MoreIcon", None),
         ("close", "XIcon", None), "|", ("undo", "UndoIcon", None), ("redo", "RedoIcon", None))
SEP = 8 * K


def strip_width() -> float:
    return 20 * K + sum(SEP if b == "|" else BTN for b in STRIP)


def _strip(s: Sheet, x: float, y: float, style: bool = False) -> dict:
    """The markup strip as it opens along the top of the page: the pens and
    highlighters, the first armed; the eraser, the lasso and the Text tool;
    the hand; More and close; undo and redo, greyed with nothing to undo.
    With ``style``, the armed pen's options row hangs under it, its Style
    picker showing "Pen" and the rest of it unnamed. Each button's centre
    by name; "style" is the picker's two ends."""
    h, w = 36 * K, strip_width()
    s.even(rounded(x, y, w, h + (32 * K if style else 0), 9 * K), UI_FRAME, 0.9)
    _fill(s, x + 11 * K, y + 3 * K, x + 39 * K, y + h - 3 * K)
    at, cx = {}, x + 10 * K + BTN / 2
    for b in STRIP:
        if b == "|":
            s.even([[(cx - BTN / 2 + SEP / 2, y + 10 * K), (cx - BTN / 2 + SEP / 2, y + h - 10 * K)]], UI_FRAME, 0.9)
            cx += SEP
            continue
        key, icon, bar = b
        if bar:
            at[key] = (cx, y + 14 * K)
            _icon(s, icon, cx, y + 14 * K, UI_ARMED if key == "pen1" else UI_ICON)
            s.even([[(cx - 8 * K, y + 29 * K), (cx + 8 * K, y + 29 * K)]], bar, 5 * K)
        else:
            at[key] = (cx, y + h / 2)
            _icon(s, icon, cx, y + h / 2, UI_QUIET if key in ("undo", "redo") else UI_ICON)
        cx += BTN
    if style:
        row = y + h + 16 * K
        s.even([[(x + 6 * K, y + h), (x + w - 6 * K, y + h)]], UI_FRAME, 0.7)
        s.note("**Style**", x + 8 * K, row - 7, 40, 9.5)
        s.even("M0 0 C3 -4.5 6 -4.5 9 0 S15 4.5 18 0", INK, 1.4, at=(x + 72 * K, row))
        s.note("Pen", x + 106 * K, row - 7, 26, 9.5)
        _icon(s, "ChevronDownIcon", x + 146 * K, row, UI_ICON, 9)
        for k, length in enumerate((52, 66, 60)):
            left = x + (176 + 80 * k) * K
            s.even([[(left, row), (left + length * K, row)]], UI_QUIET, 4)
        at["style"] = [(x + 80 * K, row), (x + 142 * K, row)]
    return at


def _column(s: Sheet, x: float, y: float) -> dict:
    """The notebook view's column of controls down its left edge: zoom out
    and in, fit to width, the pen, record, the paper and the notes view.
    Each button's centre by name."""
    names = (("zoomOut", "ZoomOutIcon"), ("zoomIn", "ZoomInIcon"), ("fit", "FitWidthIcon"), ("pen", "PenIcon"),
             ("mic", "MicIcon"), ("paper", "SheetIcon"), ("notes", "FileTextIcon"))
    s.even(rounded(x, y, 34 * K, (8 + 31 * len(names)) * K, 9 * K), UI_FRAME, 0.9)
    at = {}
    for k, (key, icon) in enumerate(names):
        at[key] = (x + 17 * K, y + (4 + 15.5 + 31 * k) * K)
        _icon(s, icon, *at[key])
    return at


def _page_in_notes(s: Sheet, x: float, y: float, w: float, title: str) -> dict:
    """A page among the notes: its title, its own buttons at the top right
    (the pen, the paper, replay, the notebook view) and the top of the
    paper under them. Each button's centre by name."""
    h = 64
    s.even(rounded(x, y, w, h, 6), UI_FRAME, 0.8)
    s.note(title, x + 8, y + 5, 110, 9.5)
    at = {}
    for k, (key, icon) in enumerate((("pen", "PenIcon"), ("paper", "SheetIcon"), ("play", "PlayIcon"),
                                     ("notebook", "NotebookIcon"))):
        at[key] = (x + w - 14 - BTN * (3 - k), y + 13)
        _icon(s, icon, *at[key], side=ICON * (0.8 if icon in FILLED else 1))
    s.even(f"M{x + 12} {y + h} V{y + 30} H{x + w - 12} V{y + h}", UI_FRAME, 0.8)
    return at


def _title_row(s: Sheet, x: float, y: float, title: str) -> tuple[float, float]:
    """A page's title and, beside it, the Notebook button. The button's
    centre."""
    s.note(f"**{title}**", x, y - 9, 110, 13)
    _icon(s, "NotebookIcon", x + 140, y)
    s.note("Notebook", x + 150, y - 7, 60, 9.5)
    return (x + 172, y)


def _add_menu(s: Sheet, x: float, y: float) -> dict:
    """The right end of the top bar (+, the background tasks, search, the
    inbox, the account, View) and the Add menu under its +: the address
    field, then Upload files, Upload folder and New page unnamed, then New
    notebook. The + and the New notebook row's ends by name."""
    plus = (x + 20 * K, y + 18 * K)
    _fill(s, plus[0] - 17 * K, plus[1] - 17 * K, plus[0] + 17 * K, plus[1] + 17 * K)
    for k, icon in enumerate(("PlusIcon", "ActivityIcon", "SearchIcon", "BellIcon", "UserIcon", "MenuIcon")):
        _icon(s, icon, plus[0] + 41 * K * k, plus[1])
    s.even([[(x - 6, y + 40 * K), (plus[0] + 41 * K * 5 + 24 * K, y + 40 * K)]], UI_FRAME, 0.8)
    left, top, w = plus[0] - 30 * K, y + 48 * K, 218 * K
    s.even(rounded(left, top, w, 194 * K, 8 * K), UI_FRAME, 0.9)
    s.even(rounded(left + 9 * K, top + 8 * K, w - 18 * K, 34 * K, 8 * K), UI_FRAME, 0.9)
    s.even([[(left + 22 * K, top + 25 * K), (left + 140 * K, top + 25 * K)]], UI_QUIET, 4)
    for k, length in enumerate((90, 100, 66)):
        row = top + (62 + 36 * k) * K
        s.even(rounded(left + 16 * K, row - 7 * K, 14 * K, 14 * K, 3 * K), UI_QUIET, 0.9)
        s.even([[(left + 44 * K, row), (left + (44 + length) * K, row)]], UI_QUIET, 4)
    row = top + 170 * K
    _icon(s, "NotebookIcon", left + 23 * K, row)
    s.note("New notebook", left + 38 * K, row - 7, 90, 9.5)
    return {"plus": plus, "notebook": [(left + 30 * K, row), (left + w - 40 * K, row)]}


# ---------------------------------------------------------------- the pages

def _sheet_one(start: int) -> Sheet:
    """Hello: where the pen is, its styles, a name to write."""
    s = Sheet("Hi there", {"color": "#fffaf0", "pattern": "dots", "spacing": 24, "line": "#e3d9c4"}, start)
    _title(s, "HI THERE!", CORAL, YELLOW_HI, 46)
    s.note("This notebook is yours to scribble in. Everything on it is ink, so you can erase it, move it or "
           "draw right over it. The red circles show where to press.", 42, 116, 420, 13)

    _heading(s, 1, "PICK UP THE PEN", 186, BLUE)
    s.note("Each page in your notes has a **pen** at its top right. In the notebook view it is in the column "
           "on the left. A stylus writes right away; the mouse draws once the tools are open.", 80, 216, 330, 12)
    pen = _page_in_notes(s, 80, 290, 300, "Hi there")
    _ring(s, [pen["pen"]], "IN THE NOTES", "above")
    col = _column(s, 476, 200)
    _ring(s, [col["pen"]], "NOTEBOOK VIEW", "left")
    s.note("Write your name here:", 80, 388, 160, 12)
    s.even("M232 406 L512 406", BLUE, 2.4, "dotted")
    _star(s, 532, 398, 11)

    _heading(s, 2, "TRY EVERY PEN", 452, PURPLE)
    s.note("The tools open in a strip along the top. Tap the armed pen once more: **Style** switches between "
           "Pen, Ballpoint, Brush, Pencil and Calligraphy, with colour and width below it.", 80, 482, 440, 12)
    tools = _strip(s, 80, 544, style=True)
    _ring(s, [tools["pen1"]], "1", "left", 12)
    _ring(s, tools["style"], "2", "below", 12)
    s.note("Try each on its line:", 80, 640, 200, 12)
    for k, name in enumerate(("Pen", "Ballpoint", "Brush", "Pencil", "Calligraphy")):
        x = 82 + k * 94
        s.note(name, x - 4, 668, 90, 11, MUTED)
        s.even([[(x, 720), (x + 78, 720)]], "#c9c1d1", 1.2)

    s.note("More on the next page.", 230, 796, 140, 11, MUTED)
    _arrow(s, "M300 816 C297 822 303 828 300 834", MUTED, 1.6, 6)
    return s


def _sheet_two(start: int) -> Sheet:
    """Fixing and moving: the highlighter, the eraser, the lasso, undo."""
    s = Sheet("Oops? No problem", {"color": "#ffffff", "pattern": "ruled", "spacing": 28, "line": "#d3e3f3"}, start)
    _title(s, "OOPS? NO PROBLEM!", GREEN, GREEN_HI, 34)

    _heading(s, 3, "SWIPE A HIGHLIGHTER", 120, ORANGE)
    s.note("The **highlighters** come after the pens. Swipe one across the words on the right: it goes under "
           "the ink, like a real marker.", 80, 150, 250, 12)
    s.letters("HIGHLIGHT ME!", 362, 158, 20, INK, 2.8)
    _arrow(s, "M364 196 L540 196", ORANGE, 1.6, dash="dashed")
    tools = _strip(s, 80, 214)
    _ring(s, [tools["hl1"], tools["hl3"]], "HIGHLIGHTERS")

    _heading(s, 4, "ERASE THE MESS", 288, CORAL)
    s.note("Pick the **eraser** and rub out the scribble. Tap it again to erase whole strokes or only the "
           "part you touch.", 80, 318, 250, 12)
    _scribble(s, 456, 338, 140, 60)
    tools = _strip(s, 80, 378)
    _ring(s, [tools["eraser"]], "ERASER")

    _heading(s, 5, "LASSO THE STAR", 452, BLUE)
    s.note("Pick the **lasso**, draw a loop around the star and drag it into the box. Its handles resize and "
           "turn it; its menu recolours, copies or deletes it.", 80, 482, 270, 12)
    tools = _strip(s, 80, 540)
    _ring(s, [tools["lasso"]], "LASSO")
    _star(s, 130, 630, 20)
    _arrow(s, "M170 624 C240 604 330 606 392 624", MUTED, 1.6, dash="dashed")
    s.even("M410 594 L520 594 L520 664 L410 664 Z", "#9aa3ad", 1.4, "dashed")
    _label(s, "DROP IT HERE", 425, 674, MUTED)

    s.note("**Changed your mind?** Ctrl+Z undoes (Cmd+Z on a Mac). The arrows at the right end of the strip "
           "undo and redo too.", 80, 708, 300, 12, INK, "#fff4b8")
    tools = _strip(s, 80, 772)
    _ring(s, [tools["undo"], tools["redo"]], "UNDO/REDO", "right")
    return s


def _sheet_three(start: int) -> Sheet:
    """Shapes, text, paper and replay."""
    s = Sheet("Make it yours", {"color": "#ffffff", "pattern": "grid", "spacing": 20, "line": "#e6eaf1"}, start)
    _title(s, "MAKE IT YOURS", PURPLE, PURPLE_HI, 40)

    _heading(s, 6, "DRAW AND HOLD", 126, PURPLE)
    s.note("Draw a circle and keep the pen still at the end. A moment later it snaps into a neat one. Lines, "
           "arrows, triangles and rectangles snap too.", 80, 156, 260, 12)
    s.pen([ellipse(394, 190, 28, 25, -100, 365, wobble=0.08)], INK, 2.4)
    _arrow(s, "M434 190 L476 190", MUTED, 1.6)
    _label(s, "HOLD", 438, 200, MUTED)
    s.even([ellipse(518, 190, 27, 27, 0, 360)], PURPLE, 2.4)

    _heading(s, 7, "TYPE ANYWHERE", 256, BLUE)
    s.note("The **T** tool puts typed text on the page: tap to place a box, then type. This note is one of "
           "them; drag it somewhere else.", 80, 286, 270, 12, INK, "#dcecff")
    tools = _strip(s, 80, 350)
    _ring(s, [tools["text"]], "TEXT")

    _heading(s, 8, "CHANGE THE PAPER", 430, GREEN)
    s.note("**Paper** sets this page's size, lines and colour: blank, ruled, grid or dots, or even a dark "
           "page. It sits beside the pen.", 80, 460, 300, 12)
    page = _page_in_notes(s, 80, 530, 300, "Make it yours")
    _ring(s, [page["paper"]], "IN THE NOTES", "above")
    col = _column(s, 476, 436)
    _ring(s, [col["paper"]], "NOTEBOOK VIEW", "left")

    _heading(s, 9, "WATCH IT AGAIN", 640, CORAL)
    s.note("Every stroke remembers how it was drawn. Press **play** on a page, or on a drawing in the notes, "
           "and watch it draw itself again.", 80, 670, 300, 12)
    page = _page_in_notes(s, 80, 740, 300, "Make it yours")
    _ring(s, [page["play"]], "REPLAY", "above")
    return s


def _sheet_four(start: int) -> Sheet:
    """The two views, more pages, and goodbye."""
    s = Sheet("Keep going", {"color": "#fffaf0", "pattern": "dots", "spacing": 24, "line": "#e3d9c4"}, start)
    _title(s, "KEEP GOING", ORANGE, ORANGE_HI, 40)

    _heading(s, 10, "TWO WAYS TO SEE IT", 126, BLUE)
    s.note("**Notebook**, beside the page's title, shows these pages large with the tools down the left. "
           "**Notes view**, at the bottom of that column, puts them back among your notes.", 80, 156, 290, 12)
    button = _title_row(s, 84, 238, "Sketchbook")
    _ring(s, [(button[0] - 30, button[1]), (button[0] + 18, button[1])], "NOTEBOOK VIEW", "below")
    col = _column(s, 476, 150)
    _ring(s, [col["notes"]], "NOTES VIEW", "left")

    _heading(s, 11, "MORE PAGES", 346, GREEN)
    s.note("Write near the bottom of the last page and a fresh one appears below it. To start a notebook of "
           "your own, press **+** at the top and pick **New notebook**.", 80, 376, 290, 12)
    menu = _add_menu(s, 200, 446)
    _ring(s, [menu["plus"]], "1", "left", 12)
    _ring(s, menu["notebook"], "2", "left", 12)

    s.letters("HAVE FUN!", 150, 700, 40, CORAL, 4.0)
    s.marker("M144 736 C226 730 316 742 376 732", YELLOW_HI, 14)
    _star(s, 118, 692, 12)
    _star(s, 406, 682, 9, PURPLE, PINK_HI)
    _star(s, 398, 740, 7, GREEN, GREEN_HI)
    return s


@functools.lru_cache(maxsize=1)
def sheets() -> tuple[Sheet, ...]:
    """The Sketchbook's sheets, drawn once per process, one after another
    on the afternoon."""
    out = []
    for draw in (_sheet_one, _sheet_two, _sheet_three, _sheet_four):
        out.append(draw(out[-1].clock + 60_000 if out else T0))
    return tuple(out)


def blocks(store) -> list[dict]:
    """The Sketchbook's notes as a ``{content, props, children}`` tree:
    each sheet (titled, on its paper, folded as a new sheet is) holding
    its ink group, then its text boxes. ``store`` keeps an ink file's bytes
    and returns its ``/api/uploads/`` URL."""
    tree = []
    for sheet in sheets():
        groups = [{"content": "", "props": {"ink_url": store(dumps(ink)), "ink_strokes": len(ink.strokes)}}
                  for ink in sheet.ink_files()]
        boxes = [{"content": box["content"], "props": {"text_box": box["text_box"]}} for box in sheet.boxes]
        tree.append({"content": sheet.title, "props": {"sheet": sheet.paper, "collapsed": True},
                     "children": groups + boxes})
    return tree
