"""Colours as Gamma stores and names them: the server half of
frontend/src/shared/model/palette.js (docs/dev/settings.md "Colors"), the
two held to the same answers by tests/shared/colors.json.

A highlight block's ``properties.color`` is ``#rrggbbaa`` — hex with its
alpha — since migration step 37; before it the viewer wrote ``rgba(r, g,
b, a)`` strings, and an older server, an old export or another app's
annotation can still send one, so every reader here goes through
``parse_color`` / ``normalize_color``, which take both (and ``#rgb``,
``#rgba``, ``#rrggbb``). Strokes and text boxes keep ``#rrggbb``
(gamma/ink.py, gamma/text_box.py). The wash set below is the default
highlight palette the frontend ships; the Logseq import and export map
their colour names onto it by nearest hue.
"""

import math
import re

# The default wash set (palette.js WASH_COLORS): the four highlight colours
# Gamma always had.
WASH = ("#ffe28f", "#aaebaa", "#9bcdff", "#e6b4ff")
WASH_ALPHA = 0.65

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGBA_RE = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)$", re.I)


def parse_color(value) -> tuple[float, float, float, float] | None:
    """Any colour Gamma ever stored → (r, g, b, a), each 0..1; None for
    anything else."""
    v = str(value or "").strip()
    m = _HEX_RE.match(v)
    if m:
        h = m.group(1)
        if len(h) <= 4:
            h = "".join(c * 2 for c in h)
        parts = [int(h[i:i + 2], 16) / 255 for i in range(0, len(h), 2)]
        return (parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else 1.0)
    m = _RGBA_RE.match(v)
    if m:
        r, g, b = (min(int(c), 255) / 255 for c in m.groups()[:3])
        try:
            a = min(max(float(m.group(4)), 0.0), 1.0) if m.group(4) else 1.0
        except ValueError:
            return None
        return (r, g, b, a)
    return None


def _hex2(v: float) -> str:
    """One byte of the stored form: ``v`` (0..1) times 255, a half rounded
    up as palette.js's Math.round rounds it (Python's ``round`` goes to
    even, so an alpha of 0.3, 76.5, would store 4c against the app's 4d).
    It is ink.js_round's rule, written out because ink.py imports this
    module."""
    x = min(max(v, 0.0), 1.0) * 255
    n = round(x)
    return f"{n + 1 if x - n == 0.5 else n:02x}"   # (x - n is exact: n is the integer nearest x)


def normalize_color(value) -> str | None:
    """The stored form, ``#rrggbbaa`` lowercase; None when not a colour."""
    c = parse_color(value)
    return "#" + "".join(_hex2(v) for v in c) if c else None


def with_alpha(color, alpha: float) -> str | None:
    c = parse_color(color)
    return "#" + "".join(_hex2(v) for v in c[:3]) + _hex2(alpha) if c else None


def _to_hsl(r: float, g: float, b: float) -> tuple[float, float, float]:
    """(r, g, b) 0..1 → (h 0..360, s, l), as palette.js toHsl."""
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, light
    d = mx - mn
    s = d / (2 - mx - mn) if light > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60, s, light


def _from_hsl(h: float, s: float, light: float) -> tuple[float, float, float]:
    """(h, s, l) → (r, g, b) 0..255, as palette.js fromHsl."""
    def f(n):
        k = (n + h / 30) % 12
        a = s * min(light, 1 - light)
        return light - a * max(-1, min(k - 3, 9 - k, 1))
    return f(0) * 255, f(8) * 255, f(4) * 255


def _byte(x: float) -> str:
    """A channel 0..255 as two hex digits, rounded half up as Math.round."""
    return f"{math.floor(min(max(x, 0.0), 255.0) + 0.5):02x}"


LINE_MAX_LIGHTNESS = 0.6


def line_color(color) -> str | None:
    """The colour a highlight drawn as a line (an underline, a wavy
    underline, a strikethrough: the block's ``style``) takes, ``#rrggbb``,
    opaque: its own colour when that is dark enough to read as a thin line,
    and for a light one (the wash colours) a deeper tone of the same hue, a
    gray a darker gray. palette.js lineColor is the same; None when not a
    colour."""
    c = parse_color(color)
    if not c:
        return None
    h, s, light = _to_hsl(*c[:3])
    if light <= LINE_MAX_LIGHTNESS:
        rgb = tuple(v * 255 for v in c[:3])
    else:
        rgb = _from_hsl(h, s if s < 0.15 else max(s, 0.7), 0.42)
    return "#" + "".join(_byte(v) for v in rgb)


def highlight_color(swatch) -> str | None:
    """What a highlight stores for a wash swatch: the swatch at WASH_ALPHA."""
    return with_alpha(swatch, WASH_ALPHA)


DEFAULT_HIGHLIGHT = highlight_color(WASH[0])  # "#ffe28fa6", the classic yellow


def nearest(color, colors):
    """The entry of ``colors`` nearest to ``color`` by the "redmean" weighted
    RGB distance (alpha ignored); None when ``color`` is not one."""
    c = parse_color(color)
    if not c:
        return None
    best, best_d = None, float("inf")
    for p in colors:
        q = parse_color(p)
        if not q:
            continue
        r1, g1, b1 = (v * 255 for v in c[:3])
        r2, g2, b2 = (v * 255 for v in q[:3])
        rm = (r1 + r2) / 2
        d = (2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - rm) / 256) * (b1 - b2) ** 2
        if d < best_d:
            best, best_d = p, d
    return best


# Logseq's highlight colour names, each on the wash it means: the four
# defaults, and a pink and a peach of the same family for the rest (red
# has no wash of its own: it reads as the pink).
_LOGSEQ = {"yellow": WASH[0], "green": WASH[1], "blue": WASH[2], "purple": WASH[3],
           "pink": "#f9a8d4", "orange": "#fdba74", "red": "#f9a8d4"}
_LOGSEQ_NAMES = {v: k for k, v in reversed(list(_LOGSEQ.items()))}  # a swatch → its first name


def logseq_color(name) -> str:
    """A Logseq colour name → the highlight colour the import stores
    (unknown names are yellow)."""
    return highlight_color(_LOGSEQ.get(str(name or "").lower().strip(), WASH[0]))


def logseq_name(color) -> str:
    """A stored colour → the Logseq colour name nearest to it, for the
    Logseq graph export (yellow for what is no colour)."""
    swatch = nearest(color, _LOGSEQ_NAMES)
    return _LOGSEQ_NAMES[swatch] if swatch else "yellow"
