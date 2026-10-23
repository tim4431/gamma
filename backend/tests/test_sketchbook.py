"""The Sketchbook's drawing (gamma/sketchbook.py, docs/dev/onboarding.md
"The Sketchbook"): SVG path data sampled into strokes, the app's icons the
drawn controls copy, the files every workspace stores, the Pen's widths, the
replay's times and the text boxes. Seeding it is test_welcome.py's."""

import math
import re
import time
from pathlib import Path

import pytest

from gamma import ink as inkmod
from gamma import sketchbook
from gamma.sketchbook import GLYPHS, ICONS, flatten, icon_polys, sheets
from gamma.text_box import normalize_text_box


def test_paths_follow_svg_commands():
    [square] = flatten("M10 10 h20 v20 H10 z")
    assert square[0] == (10, 10) and square[-1] == (10, 10)
    assert {(round(x), round(y)) for x, y in square} >= {(30, 10), (30, 30), (10, 30)}
    assert all(math.dist(a, b) <= sketchbook.STEP + 1e-9 for a, b in zip(square, square[1:]))
    # relative pairs after m draw lines; each M starts a subpath
    assert [p[-1] for p in flatten("m5 5 10 0 M0 0 L1 1")] == [(15, 5), (1, 1)]
    # S mirrors the last control point: this S curve is the C mirrored about x = 20
    [curve] = flatten("M0 0 C0 10 20 10 20 0 S40 -10 40 0")
    left = [(x, y) for x, y in curve if x < 20]
    assert all(any(math.isclose(40 - x, x2, abs_tol=1e-6) and math.isclose(-y, y2, abs_tol=1e-6)
                   for x2, y2 in curve) for x, y in left)
    [quad] = flatten("M0 0 Q10 20 20 0")
    assert max(y for _, y in quad) == pytest.approx(10, abs=0.1)  # a quadratic peaks halfway to its control
    # an arc: sweep 1 turns the positive way, over the top with y down; the flags may run together
    [arc] = flatten("M0 0 A10 10 0 0 1 20 0")
    assert min(y for _, y in arc) == pytest.approx(-10, abs=0.1) and arc[-1] == (20, 0)
    assert flatten("M0 0 a10 10 0 0120 0") == [arc]
    assert all(math.dist(p, (10, 0)) == pytest.approx(10, abs=1e-6) for p in arc)
    # radii too small for the endpoints grow until they reach
    [grown] = flatten("M0 0 A1 1 0 0 0 20 0")
    assert max(y for _, y in grown) == pytest.approx(10, abs=0.1)
    # sampled finer for a drawing that will be scaled down
    assert len(flatten("M0 0 L100 0", 0.5)[0]) == 201
    with pytest.raises(ValueError):
        flatten("M0 0 T10 10")


def test_every_glyph_draws():
    for ch, (advance, d) in GLYPHS.items():
        polys = flatten(d)
        assert polys and all(-10 <= x <= advance + 10 and -10 <= y <= 110 for poly in polys for x, y in poly), ch


def test_the_drawn_controls_use_the_apps_icons():
    """Each icon the drawings copy is the one frontend/src/shared/ui/Icons.jsx
    draws (the children of its <svg>), so a drawn button is the button on
    screen; and each fits its 24-unit square."""
    source = Path(__file__).resolve().parents[2] / "frontend" / "src" / "shared" / "ui" / "Icons.jsx"
    if not source.exists():
        pytest.skip("no frontend checkout beside the backend")
    text = source.read_text(encoding="utf-8")
    for name, markup in ICONS.items():
        found = re.search(rf"export const {name} = .*?\(\s*<(Icon|svg)\b[^>]*>(.*?)</\1>", text, re.S)
        assert found, f"{name} is no longer in Icons.jsx"
        assert " ".join(found.group(2).split()) == " ".join(markup.split()), f"{name} changed in Icons.jsx"
        polys = icon_polys(markup)
        assert polys and all(-1 <= v <= 25 for poly in polys for p in poly for v in p), name


def test_the_drawing_is_the_same_every_time():
    """Every workspace stores the same files (named by their bytes' hash)."""
    first = [[inkmod.dumps(f) for f in s.ink_files()] for s in sheets()]
    sheets.cache_clear()
    try:
        assert [[inkmod.dumps(f) for f in s.ink_files()] for s in sheets()] == first
    finally:
        sheets.cache_clear()


def test_each_sheet_is_one_small_group_drawn_on_it():
    for sheet in sheets():
        [ink] = sheet.ink_files()
        assert len(inkmod.dumps(ink)) < 300_000
        x0, y0, x1, y1 = inkmod.bounding_box(ink)
        assert 0 <= x0 and 0 <= y0 and x1 <= sheet.paper["width"] and y1 <= sheet.paper["height"], sheet.title


def test_strokes_are_what_their_tools_draw():
    """A Pen line carries the plain Pen's width, 1 + (p − 0.5), so a restyle
    bakes the same; a highlighter and a Ballpoint line are one width."""
    kinds = set()
    for sheet in sheets():
        for stroke in sheet.ink_files()[0].strokes:
            samples = inkmod.decode_stroke(stroke)
            if stroke.tool == "highlighter":
                kinds.add("highlighter")
                assert stroke.ch == "xyt" and stroke.brush is None
            elif "w" in stroke.ch:
                kinds.add("pen")
                assert stroke.pen and stroke.brush is None and stroke.ch == "xyptw"
                assert all(math.isclose(s["w"], 0.5 + s["p"], abs_tol=1e-9) for s in samples)
            else:
                kinds.add("even")
                assert stroke.pen is False and stroke.ch == "xyt"
    assert kinds == {"pen", "highlighter", "even"}


def test_strokes_replay_in_drawing_order_from_the_past():
    """The replay orders strokes by their start, so each starts after the
    one before; the afternoon is long gone, so no recording covers it."""
    last = 0
    for sheet in sheets():
        for stroke in sheet.ink_files()[0].strokes:
            ts = [s["t"] for s in inkmod.decode_stroke(stroke)]
            assert stroke.t0 > last and ts == sorted(ts)
            last = stroke.t0 + ts[-1]
    assert last < time.time() * 1000


def test_text_boxes_sit_on_their_sheet_apart():
    for sheet in sheets():
        rects = []
        for box in (b["text_box"] for b in sheet.boxes):
            assert normalize_text_box(box) == box and box["auto"] is False
            assert box["x"] + box["w"] <= sheet.paper["width"] and box["y"] + box["h"] <= sheet.paper["height"]
            rects.append((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (sheet.title, a, b)
