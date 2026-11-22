"""Handwriting and AI (gamma/ink_ai.py, routers/ink_ai.py;
docs/dev/handwriting_ai.md): the digest Redraw as shapes sends, the shape
records its reply becomes, the transcription Convert to text reads back,
and the two routes around them."""

import base64
import io
import json
import math
import random

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gamma import ink as inkmod
from gamma import ink_ai

SPACE = {"kind": "pdf-page", "page": 1, "width": 612, "height": 792}


def _stroke(sid, points, tool="pen", size=2, **extra):
    return {"id": sid, "tool": tool, "size": size, "ch": "xy",
            "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in points], "xy"), **extra}


def _ink(*strokes, space=SPACE):
    return inkmod.parse_ink({"format": inkmod.FORMAT, "version": inkmod.VERSION, "space": space,
                             "strokes": list(strokes)})


def _box_points(x0, y0, x1, y1):
    """A rough box drawn in one stroke, a sample every point, clockwise from
    near its top-left corner and ending a little short of it."""
    top = [(x, y0) for x in range(x0, x1)]
    right = [(x1, y) for y in range(y0, y1)]
    bottom = [(x, y1) for x in range(x1, x0, -1)]
    left = [(x0, y) for y in range(y1, y0 + 2, -1)]
    return top + right + bottom + left


def _reply(*shapes):
    return json.dumps({"shapes": list(shapes)})


# Box (100, 100)–(300, 200) in page points, so the digest's frame starts at
# (99, 99) with the 2 pt pen's half width, and a reply's frame point (x, y)
# is the page's (x + 99, y + 99).
BOX = (99.0, 99.0, 301.0, 201.0)
OFFERED = [0, 2, 3]


def _shapes(*shapes, offered=OFFERED, box=BOX):
    return ink_ai.read_shapes(_reply(*shapes), offered, box)


# --- The digest -----------------------------------------------------------------------

def test_the_digest_numbers_the_pen_strokes_in_a_frame_at_the_selections_corner():
    """Stroke n is the n-th pen stroke; the frame is the strokes' box,
    widths included, x right and y down, numbers rounded to integers."""
    ink = _ink(_stroke("a", _box_points(100, 100, 300, 200)),
               _stroke("h", [(0, 0), (500, 500)], tool="highlighter", size=14),
               _stroke("b", [(310.4, 150.6), (400.2, 151.4)], size=1.4))
    pen, offered = ink_ai.pen_strokes(ink)
    assert offered == [0, 2] and [s.id for s in pen.strokes] == ["a", "b"]
    lines = ink_ai.digest(pen).splitlines()
    x0, y0, x1, y1 = inkmod.bounding_box(pen)
    assert (x0, y0) == (99.0, 99.0) and x1 == pytest.approx(400.9)
    assert lines[1].startswith("Selection: 302 × 102 pt. Coordinates are points from the selection's top-left")
    assert not any(line.startswith("The picture shows") for line in lines)
    assert lines[2] == "Strokes (number: width in pt; box x0,y0,x1,y1; open|closed; points along it):"
    box_line, line_line = lines[3], lines[4]
    assert box_line.startswith("1: 2 pt; box 1,1,201,101; closed; 1,1 ")
    # RDP keeps the corners and drops the samples along the sides.
    points = box_line.split("; ")[-1].split()
    assert {"201,1", "201,101", "1,101"} <= set(points) and len(points) <= ink_ai.DIGEST_POINTS
    assert line_line == "2: 1.4 pt; box 211,52,301,52; open; 211,52 301,52"


def test_the_digest_says_what_part_of_the_frame_the_picture_shows():
    pen = _ink(_stroke("a", [(100, 100), (200, 140)]))
    text = ink_ai.digest(pen, (60.0, 48.5, 240.0, 192.0))
    assert "The picture shows x from -39 to 141 and y from -50 to 93 of this frame." in text


def test_a_stroke_is_closed_when_its_ends_meet_within_6_pt_or_12_percent_of_its_diagonal_and_half_of_it():
    def closed(points):
        return ink_ai.digest(_ink(_stroke("a", points))).splitlines()[-1].split("; ")[2]

    assert closed([(0, 0), (100, 0), (100, 100), (5, 3)]) == "closed"       # 5.8 pt apart
    assert closed([(0, 0), (100, 0), (100, 100), (16, 4)]) == "closed"      # 16.5 ≤ 12% of 141
    assert closed([(0, 0), (100, 0), (100, 100), (20, 4)]) == "open"
    assert closed([(0, 0), (3, 0)]) == "open"                                # a dash is no loop
    # Ends within 6 pt, but no nearer than the stroke is long: a tick, a
    # minus sign, a short V. A small loop or a dot comes back to its start.
    assert closed([(0, i * 0.1) for i in range(50)]) == "open"              # a 4.9 pt tick, densely sampled
    assert closed([(i * 0.5, 0) for i in range(12)]) == "open"
    assert closed([(0, 0), (2, 4), (4, 0)]) == "open"
    loop = [(1.5 + 1.5 * math.cos(t / 8 * math.pi), 1.5 * math.sin(t / 8 * math.pi)) for t in range(17)]
    assert closed(loop) == "closed"                                          # 3 pt across
    assert closed([(5, 5), (5.2, 5.1), (5, 5)]) == "closed"


def _classic_rdp(points, epsilon):
    """Ramer–Douglas–Peucker as it is usually written, recursively."""
    if len(points) < 3:
        return list(points)
    far, at = -1.0, -1
    for k in range(1, len(points) - 1):
        d = ink_ai._segment_distance(points[k], points[0], points[-1])
        if d > far:
            far, at = d, k
    if far <= epsilon:
        return [points[0], points[-1]]
    return _classic_rdp(points[:at + 1], epsilon)[:-1] + _classic_rdp(points[at:], epsilon)


def test_rdp_from_one_pass_of_standings_is_rdp_at_every_tolerance():
    rng = random.Random(7)
    for _ in range(200):
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(rng.randint(0, 50))]
        for epsilon in (0.5, 2, 8, 30):
            assert ink_ai.rdp(points, epsilon) == _classic_rdp(points, epsilon)
        # Read down to a floor, the standings still answer every tolerance from it up.
        floor = rng.uniform(0, 20)
        standing = ink_ai.importance(points, floor)
        for epsilon in (floor, 1.5 * floor, 3 * floor + 1):
            assert ink_ai.rdp(points, epsilon, standing) == _classic_rdp(points, epsilon)


def test_a_long_straight_run_is_read_at_once():
    """Samples on one segment, or within the first tolerance of it, are not
    split one by one (20 000 of them would take seconds)."""
    exact = [(x * 0.25, 0.0) for x in range(20000)]
    gridded = [(round(x * 0.25, 2), round(x * 0.0731, 2)) for x in range(20000)]
    assert ink_ai.simplified(exact, 5000) == [exact[0], exact[-1]]
    assert ink_ai.simplified(gridded, 5000) == [gridded[0], gridded[-1]]
    assert ink_ai.importance(gridded, 1.0)[1:-1] == [0.0] * 19998


def test_rdp_reads_a_bounded_number_of_samples_however_long_the_strokes(monkeypatch):
    """A zigzag whose every split peels off one point makes RDP quadratic
    in what it reads: a stroke is read at most RDP_SAMPLES samples, evenly
    spaced, and a digest of many strokes fewer of each, so that one
    request's digest is a fraction of a second, not hours."""
    calls = [0]
    distance = ink_ai._segment_distance

    def counted(*args):
        calls[0] += 1
        return distance(*args)

    monkeypatch.setattr(ink_ai, "_segment_distance", counted)
    zigzag = [(i * 0.01, 100.0 * (i % 2)) for i in range(400_000)]
    out = ink_ai.simplified(zigzag, 4001.25)
    assert calls[0] <= ink_ai.RDP_SAMPLES ** 2 // 2 and len(out) <= ink_ai.DIGEST_POINTS
    assert out[0] == zigzag[0] and out[-1] == zigzag[-1]
    # The worst case it reads, exactly: every split peels off one point.
    calls[0] = 0
    ink_ai.simplified([(i * 1.0, 100.0 * (i % 2)) for i in range(ink_ai.RDP_SAMPLES)], 1004)
    assert ink_ai.RDP_SAMPLES ** 2 // 4 < calls[0] <= ink_ai.RDP_SAMPLES ** 2 // 2
    # 300 strokes share RDP_WORK: each is read about 115 samples.
    calls[0] = 0
    strokes = [_stroke(f"z{j}", [(i * 2, 10 * j + 60 * (i % 2)) for i in range(1200)], size=1)
               for j in range(300)]
    digest = ink_ai.digest(_ink(*strokes, space={"kind": "canvas", "width": 2000, "height": 2000}))
    assert calls[0] <= ink_ai.RDP_WORK // 2 and len(digest.splitlines()) == 303


def test_a_dense_stroke_whose_points_stand_alike_keeps_24_points_not_its_ends():
    """A zigzag's points all stand about as high, so one ×1.5 step can drop
    from more than 24 straight to its two ends: the 24 that stand highest
    are kept, ends included, in the order drawn."""
    for n, amplitude in ((300, 100), (600, 60), (900, 30)):
        zigzag = [(float(i), amplitude * (i % 2)) for i in range(n)]
        diagonal = math.hypot(n - 1, amplitude)
        out = ink_ai.simplified(zigzag, diagonal)
        assert len(out) == ink_ai.DIGEST_POINTS, n
        assert out[0] == zigzag[0] and out[-1] == zigzag[-1]
        assert [p[0] for p in out] == sorted(p[0] for p in out)


def test_a_long_stroke_comes_down_to_24_points_its_ends_kept():
    spiral = [(t * 0.06 * math.cos(t / 40), t * 0.06 * math.sin(t / 40)) for t in range(3000)]
    diagonal = math.hypot(*(max(p[k] for p in spiral) - min(p[k] for p in spiral) for k in (0, 1)))
    # Its first tolerance keeps far more than 24 (six turns); raised, 16.
    assert len(ink_ai.rdp(spiral, 0.015 * diagonal)) > 100
    out = ink_ai.simplified(spiral, diagonal)
    assert 2 < len(out) <= ink_ai.DIGEST_POINTS and out[0] == spiral[0] and out[-1] == spiral[-1]
    # A straight stroke is its two ends.
    assert ink_ai.simplified([(x, 0.2 * (x % 2)) for x in range(300)], 299) == [(0, 0), (299, 0.2)]


# --- The redraw's reply -------------------------------------------------------------

def test_each_kind_becomes_the_stroke_files_record_in_page_points():
    out = _shapes(
        {"kind": "line", "strokes": [1], "from": [10, 20], "to": [60, 80]},
        {"kind": "arrow", "strokes": [2], "from": [0, 0], "to": [40, 30], "head": 9},
        {"kind": "rect", "strokes": [3], "cx": 50.123, "cy": 40, "w": -30, "h": 20, "angle": 20},
    )
    assert out == [
        {"kind": "line", "params": {"x1": 109, "y1": 119, "x2": 159, "y2": 179}, "strokes": [0]},
        {"kind": "arrow", "params": {"x1": 99, "y1": 99, "x2": 139, "y2": 129, "head": 9}, "strokes": [2]},
        {"kind": "rect", "params": {"cx": 149.12, "cy": 139, "w": 30, "h": 20, "angle": 20}, "strokes": [3]},
    ]
    out = _shapes(
        {"kind": "ellipse", "strokes": [1], "cx": 50, "cy": 40, "rx": 30, "ry": 10, "angle": 30},
        {"kind": "triangle", "strokes": [2], "points": [[0, 0], [40, 0], [20, 30]]},
        {"kind": "curve", "strokes": [3], "points": [[0, 50], [50, 0], [100, 50]], "smooth": True},
    )
    assert out == [
        {"kind": "ellipse", "params": {"cx": 149, "cy": 139, "rx": 30, "ry": 10, "angle": 30}, "strokes": [0]},
        {"kind": "triangle", "params": {"x1": 99, "y1": 99, "x2": 139, "y2": 99, "x3": 119, "y3": 129},
         "strokes": [2]},
        {"kind": "curve", "params": {"x1": 99, "y1": 149, "x2": 149, "y2": 99, "x3": 199, "y3": 149,
                                     "closed": False, "smooth": True}, "strokes": [3]},
    ]
    # Every record is one the file format takes, its flags booleans.
    for shape in out:
        inkmod.Shape.model_validate({"kind": shape["kind"], "params": shape["params"]})
    assert out[2]["params"]["smooth"] is True and out[2]["params"]["closed"] is False


def test_records_of_the_drawn_kinds_bake_into_samples():
    """The params are the sampler's (ink.shape_samples), not just numbers."""
    out = _shapes(
        {"kind": "line", "strokes": [1], "from": [10, 20], "to": [60, 80]},
        {"kind": "arrow", "strokes": [2], "from": [0, 0], "to": [40, 30]},
        {"kind": "rect", "strokes": [3], "cx": 50, "cy": 40, "w": 30, "h": 20, "angle": 20},
        offered=[0, 1, 2])
    out += _shapes(
        {"kind": "circle", "strokes": [1], "cx": 50, "cy": 40, "r": 10},
        {"kind": "triangle", "strokes": [2], "points": [[0, 0], [40, 0], [20, 30]]},
        offered=[0, 1])
    for shape in out:
        assert len(inkmod.shape_samples(shape["kind"], shape["params"])) >= 2, shape


def test_a_polygon_is_a_triangle_or_a_closed_straight_curve():
    square = [[0, 0], [40, 0], [40, 40], [0, 40]]
    out = _shapes({"kind": "polygon", "strokes": [1], "points": [[0, 0], [40, 0], [20, 30]]},
                  {"kind": "polygon", "strokes": [2], "points": square + [[0, 0]]},
                  {"kind": "polygon", "strokes": [3], "points": [[0, 0], [40, 0]]})
    assert [s["kind"] for s in out] == ["triangle", "curve"]
    # The repeated first corner closing the list is dropped.
    assert out[1]["params"] == {"x1": 99, "y1": 99, "x2": 139, "y2": 99, "x3": 139, "y3": 139,
                                "x4": 99, "y4": 139, "closed": True, "smooth": False}
    many = [[i, (i * 7) % 13] for i in range(32)]
    assert _shapes({"kind": "polygon", "strokes": [1], "points": many}) == []


def test_a_curve_takes_2_to_31_points_and_two_of_them_not_smooth_are_a_line():
    out = _shapes({"kind": "curve", "strokes": [1], "points": [[0, 0], [50, 40]], "smooth": False},
                  {"kind": "curve", "strokes": [2], "points": [[0, 0], [50, 40]], "smooth": True},
                  {"kind": "curve", "strokes": [3], "points": [[0, 0]]})
    assert out[0] == {"kind": "line", "params": {"x1": 99, "y1": 99, "x2": 149, "y2": 139}, "strokes": [0]}
    assert out[1]["kind"] == "curve" and len(out) == 2
    points = [[i * 3, (i % 2) * 10] for i in range(31)]
    full = _shapes({"kind": "curve", "strokes": [1], "points": points, "closed": "yes"})[0]
    assert len(full["params"]) == inkmod.SHAPE_PARAMS and full["params"]["closed"] is False
    assert _shapes({"kind": "curve", "strokes": [1], "points": points + [[100, 0]]}) == []


def test_a_rectangles_angle_comes_into_minus_45_to_45_its_sides_swapping_on_a_quarter_turn():
    def rect(angle, w=30, h=20):
        out = _shapes({"kind": "rect", "strokes": [1], "cx": 50, "cy": 50, "w": w, "h": h, "angle": angle})
        return {k: out[0]["params"][k] for k in ("w", "h", "angle")}

    assert rect(60) == {"w": 20, "h": 30, "angle": -30}
    assert rect(-50) == {"w": 20, "h": 30, "angle": 40}
    assert rect(45) == {"w": 20, "h": 30, "angle": -45}
    assert rect(180) == {"w": 30, "h": 20, "angle": 0}
    # Within 3° of upright it is upright, whichever quarter it came in.
    assert rect(2.5) == {"w": 30, "h": 20, "angle": 0}
    assert rect(88) == {"w": 20, "h": 30, "angle": 0}
    assert rect(4) == {"w": 30, "h": 20, "angle": 4}
    assert _shapes({"kind": "rect", "strokes": [1], "cx": 5, "cy": 5, "w": 0.4, "h": 20}) == []


def test_an_ellipses_longer_radius_is_rx_its_angle_in_0_to_180_and_near_round_is_a_circle():
    def ellipse(rx, ry, angle):
        out = _shapes({"kind": "ellipse", "strokes": [1], "cx": 50, "cy": 50, "rx": rx, "ry": ry, "angle": angle})
        return {k: out[0]["params"][k] for k in ("rx", "ry", "angle")} if out else None

    assert ellipse(10, 30, 20) == {"rx": 30, "ry": 10, "angle": 110}
    assert ellipse(30, 10, 200) == {"rx": 30, "ry": 10, "angle": 20}
    assert ellipse(30, 10, -30) == {"rx": 30, "ry": 10, "angle": 150}
    assert ellipse(30, 10, 178) == {"rx": 30, "ry": 10, "angle": 0}
    assert ellipse(30, 10, 2) == {"rx": 30, "ry": 10, "angle": 0}
    assert ellipse(10, 30, 1) == {"rx": 30, "ry": 10, "angle": 90}
    assert ellipse(20, 19.7, 40) == {"rx": 19.85, "ry": 19.85, "angle": 0}
    assert ellipse(0.2, 10, 0) is None


def test_a_huge_angle_is_taken_into_a_turn_not_an_overflow():
    """An angle of 1e307 would overflow the 1/100 grid and lose the whole
    reply: it is taken mod 360 first, exactly, and normalised as any."""
    out = _shapes({"kind": "line", "strokes": [1], "from": [0, 0], "to": [40, 30]},
                  {"kind": "rect", "strokes": [2], "cx": 50, "cy": 50, "w": 30, "h": 20, "angle": 1e307},
                  {"kind": "ellipse", "strokes": [3], "cx": 50, "cy": 50, "rx": 30, "ry": 10, "angle": -1e307},
                  offered=[0, 1, 2])
    assert [s["kind"] for s in out] == ["line", "rect", "ellipse"]
    rect, ellipse = out[1]["params"], out[2]["params"]
    assert -45 <= rect["angle"] < 45 and 0 <= ellipse["angle"] < 180
    out = _shapes({"kind": "rect", "strokes": [1], "cx": 50, "cy": 50, "w": 30, "h": 20, "angle": -1e307},
                  {"kind": "ellipse", "strokes": [2], "cx": 50, "cy": 50, "rx": 30, "ry": 10, "angle": 1e307})
    assert all(math.isfinite(v) for s in out for v in s["params"].values()) and len(out) == 2
    # An angle already in range rounds as it always did.
    assert _shapes({"kind": "rect", "strokes": [1], "cx": 50, "cy": 50, "w": 30, "h": 20,
                    "angle": 450.004})[0]["params"] == {"cx": 149, "cy": 149, "w": 20, "h": 30, "angle": 0}


def test_a_figure_whose_points_span_under_a_point_is_skipped():
    """Drawn in place of the strokes it claims, a collapsed figure would
    look like their deletion; the strokes stay as drawn instead."""
    dot = [[5, 5], [5, 5], [5, 5]]
    assert _shapes({"kind": "triangle", "strokes": [1], "points": dot},
                   {"kind": "polygon", "strokes": [1], "points": dot},
                   {"kind": "polygon", "strokes": [1], "points": dot + [[5.5, 5.5]]},
                   {"kind": "curve", "strokes": [1], "points": [[5, 5], [5, 5]], "smooth": True},
                   {"kind": "curve", "strokes": [1], "points": dot, "smooth": True},
                   {"kind": "curve", "strokes": [1], "points": dot + [[5.4, 5.4]], "closed": True}) == []
    # Just over a point across is kept, and claims its stroke.
    out = _shapes({"kind": "triangle", "strokes": [1], "points": [[5, 5], [6, 5], [5, 5.5]]})
    assert [s["strokes"] for s in out] == [[0]]


def test_a_line_within_3_degrees_of_level_or_plumb_is_made_so_and_one_under_a_point_is_dropped():
    out = _shapes({"kind": "line", "strokes": [1], "from": [0, 10], "to": [100, 14]},
                  {"kind": "arrow", "strokes": [2], "from": [50, 0], "to": [47, 90], "head": 60},
                  {"kind": "line", "strokes": [3], "from": [0, 0], "to": [100, 8]})
    assert out[0]["params"] == {"x1": 99, "y1": 111, "x2": 199, "y2": 111}
    # A head past 48 pt is left to the drawing's default.
    assert out[1]["params"] == {"x1": 147.5, "y1": 99, "x2": 147.5, "y2": 189}
    assert out[2]["params"]["y2"] == 107
    assert _shapes({"kind": "line", "strokes": [1], "from": [5, 5], "to": [5.5, 5.5]}) == []


def test_a_shape_reaching_past_the_grown_selection_is_skipped():
    """The selection is 202 × 102 pt: it grows by 50.5 pt (a quarter of its
    longer side) each way, so the frame runs from -50.5 to 252.5 in x."""
    out = _shapes({"kind": "line", "strokes": [1], "from": [-50, 0], "to": [252, 0]},
                  {"kind": "line", "strokes": [2], "from": [-51, 20], "to": [100, 60]},
                  {"kind": "ellipse", "strokes": [3], "cx": 230, "cy": 50, "rx": 30, "ry": 10})
    assert [s["strokes"] for s in out] == [[0]]
    small = ink_ai.read_shapes(_reply({"kind": "line", "strokes": [1], "from": [-23, 0], "to": [10, 0]}),
                               [0], (0.0, 0.0, 10.0, 10.0))
    assert small and ink_ai.read_shapes(
        _reply({"kind": "line", "strokes": [1], "from": [-25, 0], "to": [10, 0]}), [0], (0.0, 0.0, 10.0, 10.0)) == []


def test_each_stroke_goes_to_one_shape_at_most():
    out = _shapes({"kind": "line", "strokes": [3, 1, 1, 9, 0, "2", True], "from": [0, 0], "to": [50, 50]},
                  {"kind": "line", "strokes": [1, 2.0], "from": [0, 0], "to": [80, 10]},
                  {"kind": "line", "strokes": [3], "from": [0, 0], "to": [10, 80]},
                  {"kind": "rect", "strokes": [], "cx": 1, "cy": 1, "w": 5, "h": 5})
    assert [s["strokes"] for s in out] == [[0, 3], [2]]
    # A shape skipped for its geometry takes no stroke from a later one.
    out = _shapes({"kind": "rect", "strokes": [1], "cx": 1, "cy": 1, "w": 0, "h": 5},
                  {"kind": "line", "strokes": 1, "from": [0, 0], "to": [50, 0]})
    assert [s["strokes"] for s in out] == [[0]]


def test_entries_that_are_no_shape_are_passed_over():
    out = _shapes("rect", None, {"kind": "star", "strokes": [1]}, {"strokes": [1]},
                  {"kind": "line", "strokes": [1], "from": [0, "a"], "to": [5, 5]},
                  {"kind": "line", "strokes": [1], "from": [0, 1e400], "to": [5, 5]},
                  {"kind": "line", "strokes": [1], "from": [0, 10 ** 400], "to": [5, 5]},
                  {"kind": "triangle", "strokes": [1], "points": [[0, 0], [5, 5]]},
                  {"kind": "rect", "strokes": [1], "cx": 5, "cy": 5, "w": 5},
                  {"kind": "line", "strokes": [1], "from": {"x": 0, "y": 0}, "to": [9, 40]})
    assert out == [{"kind": "line", "params": {"x1": 99, "y1": 99, "x2": 108, "y2": 139}, "strokes": [0]}]
    # A kind's name is read in any case; "rectangle" is a rect.
    out = _shapes({"kind": " Rectangle", "strokes": [1], "cx": 5, "cy": 5, "w": 5, "h": 5},
                  {"kind": "LINE", "strokes": [2], "from": [0, 0], "to": [9, 40]})
    assert [s["kind"] for s in out] == ["rect", "line"]


def test_the_reply_is_found_past_reasoning_fences_and_earlier_drafts():
    line = {"kind": "line", "strokes": [1], "from": [0, 0], "to": [40, 30]}
    fenced = "Here you go:\n```json\n" + _reply(line) + "\n```"
    assert len(ink_ai.read_shapes(fenced, [0], BOX)) == 1
    drafted = ('Stroke 1 is a line {"rough": true}; a first try {"shapes": []} — no, better:\n'
               + _reply(line) + "\nDone {not json}.")
    assert len(ink_ai.read_shapes(drafted, [0], BOX)) == 1
    assert ink_ai.read_shapes('{"shapes": []}', [0], BOX) == []
    for garbage in ("", "I see a box.", '{"shape": []}', '{"shapes": {"kind": "line"}}', "[1, 2]", None):
        with pytest.raises(ValueError, match="the AI's answer was not a list of shapes"):
            ink_ai.read_shapes(garbage, [0], BOX)


# --- The transcription's reply -------------------------------------------------------

def test_a_transcription_is_stripped_unfenced_and_capped():
    assert ink_ai.read_text("  Hello $x^2$\n- one\n- two \n") == "Hello $x^2$\n- one\n- two"
    assert ink_ai.read_text("```markdown\nLine one\n\nLine two\n```") == "Line one\n\nLine two"
    assert ink_ai.read_text("```\n$$E = mc^2$$\n```") == "$$E = mc^2$$"
    for empty in ("[no text]", "  [No Text]\n", "[NO TEXT].", "```\n[no text]\n```", "", None):
        assert ink_ai.read_text(empty) == ""
    assert ink_ai.read_text("x" * 30000) == "x" * ink_ai.MAX_TEXT_CHARS
    assert ink_ai.read_text("[no text] but also this") == "[no text] but also this"


def test_the_prompts_carry_their_rules_and_reply_formats():
    for words in ('{"shapes": [...]}', '"kind": "curve"', '"kind": "polygon"', "at most one shape",
                  "When unsure, leave the stroke out", "clockwise", "up to 31 points", '{"shapes": []}'):
        assert words in ink_ai.SHAPES_SYSTEM, words
    for words in ("one output line per handwritten line", "$…$", "$$…$$", "[illegible]", "never guess",
                  "no code fence", "[no text]", '"- "', '"1. "'):
        assert words in ink_ai.TEXT_SYSTEM, words
    # The shapes example's reply is one the parser takes, whole.
    example = ink_ai.SHAPES_SYSTEM.rsplit("The reply:\n", 1)[1]
    assert [s["kind"] for s in ink_ai.read_shapes(example, [0, 1, 2, 3], (0, 0, 232, 81))] == ["rect", "arrow"]


# --- The routes -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def ann(client):
    """An account with an Anthropic entry; its name is this file's own (the
    worker's data dir is shared across the files it runs)."""
    from conftest import login, make_user
    make_user("inkai_ann", "pw")
    c = login("inkai_ann", "pw")
    r = c.post("/api/ai/providers", json={"protocol": "anthropic", "api_key": "sk-ant-inkai-5678",
                                          "models": "claude-ink"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(autouse=True)
def _sees(monkeypatch):
    """The model takes pictures unless a test says otherwise."""
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *a: (True, "provider"))


def _fake(monkeypatch, reply):
    """A _call_ai stand-in answering ``reply`` (a callable on the call's
    arguments, or an exception to raise); returns the calls it got."""
    calls = []

    def fake(messages, system, entry, rt, **kw):
        calls.append({"messages": messages, "system": system, "entry": entry, **kw})
        if isinstance(reply, Exception):
            raise reply
        return reply(calls[-1]) if callable(reply) else reply
    monkeypatch.setattr("gamma.routers.ink_ai._call_ai", fake)
    return calls


def _lines(r):
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert all(line == {"ping": 1} for line in lines[:-1])
    return lines[-1]


def _body(*strokes):
    return {"space": SPACE, "strokes": list(strokes)}


def _picture_of(call):
    (media_type, b64), = call["images"]
    assert media_type in ("image/jpeg", "image/png")
    return Image.open(io.BytesIO(base64.b64decode(b64)))


ROUGH_BOX = _stroke("box", _box_points(100, 100, 300, 200))
HIGHLIGHT = _stroke("hl", [(90, 90), (320, 210)], tool="highlighter", size=14, color="#ffe066")
LABEL = _stroke("w", [(150, 130), (160, 170), (170, 140), (180, 170), (190, 130)], size=1.4)


def test_a_redraw_sends_the_digest_and_a_picture_and_maps_the_strokes_back(ann, monkeypatch):
    calls = _fake(monkeypatch, _reply(
        {"kind": "rect", "strokes": [1], "cx": 101, "cy": 51, "w": 200, "h": 100, "angle": 1},
        {"kind": "line", "strokes": [2, 3], "from": [0, 0], "to": [40, 40]}))
    r = ann.post("/api/ai/ink/shapes", json=_body(ROUGH_BOX, HIGHLIGHT, LABEL))
    # Stroke 2 of the digest is the request's third (the highlighter is not
    # offered), and the reply's stroke 3, past the two offered, is dropped.
    assert _lines(r) == {"shapes": [
        {"kind": "rect", "params": {"cx": 200, "cy": 150, "w": 200, "h": 100, "angle": 0}, "strokes": [0]},
        {"kind": "line", "params": {"x1": 99, "y1": 99, "x2": 139, "y2": 139}, "strokes": [2]}]}
    (call,) = calls
    assert call["system"] == ink_ai.SHAPES_SYSTEM and call["entry"]["model"] == "claude-ink"
    (turn,) = call["messages"]
    assert turn["role"] == "user"
    text = turn["content"]
    assert "Selection: 202 × 102 pt." in text and "The picture shows x from" in text
    assert [line.split(":")[0] for line in text.splitlines() if line[:1].isdigit()] == ["1", "2"]
    assert call["max_tokens"] == 8192 and call["timeout"] == 180 and call["retries"] == 1
    assert call["slot"] is not None and callable(call["on_usage"]) and "effort" not in call
    picture = _picture_of(call)
    assert min(picture.size) >= 144 and max(picture.size) <= 1568


def test_a_model_that_reads_text_only_gets_the_digest_alone(ann, monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *a: (False, "models.dev"))
    calls = _fake(monkeypatch, '{"shapes": []}')
    assert _lines(ann.post("/api/ai/ink/shapes", json=_body(ROUGH_BOX))) == {"shapes": []}
    assert calls[0]["images"] is None
    assert "The picture shows" not in calls[0]["messages"][0]["content"]


def test_a_redraw_that_fails_ends_with_its_error_line(ann, monkeypatch):
    _fake(monkeypatch, "It is a box, I think.")
    assert _lines(ann.post("/api/ai/ink/shapes", json=_body(ROUGH_BOX))) == {
        "error": "redrawing failed: the AI's answer was not a list of shapes"}
    _fake(monkeypatch, RuntimeError("upstream 529: overloaded"))
    assert _lines(ann.post("/api/ai/ink/shapes", json=_body(ROUGH_BOX))) == {
        "error": "redrawing failed: upstream 529: overloaded"}


def test_a_redraw_takes_300_pen_strokes_and_no_more(ann, monkeypatch):
    calls = _fake(monkeypatch, '{"shapes": []}')
    dashes = [_stroke(f"d{i}", [(10 + i, 10), (10 + i, 14)], size=0.6) for i in range(300)]
    highlights = [_stroke(f"h{i}", [(10, 20), (90, 20)], tool="highlighter") for i in range(5)]
    assert _lines(ann.post("/api/ai/ink/shapes", json=_body(*dashes, *highlights))) == {"shapes": []}
    r = ann.post("/api/ai/ink/shapes", json=_body(*dashes, _stroke("one-more", [(5, 5), (6, 9)])))
    assert r.status_code == 413 and r.json()["detail"] == "Too much handwriting to redraw at once; select less."
    assert len(calls) == 1


def test_handwriting_the_file_format_refuses_is_a_400(ann, monkeypatch):
    calls = _fake(monkeypatch, '{"shapes": []}')
    bad = [
        {"strokes": [ROUGH_BOX]},                                                    # no space
        {"space": {"kind": "pdf-page", "width": 600, "height": 800}, "strokes": [ROUGH_BOX]},  # no page number
        _body({**ROUGH_BOX, "color": "red"}),
        _body(ROUGH_BOX, ROUGH_BOX),                                                 # one id twice
        _body({**ROUGH_BOX, "pts": []}),
        {"space": SPACE, "strokes": "box"},
    ]
    for route in ("shapes", "text"):
        for body in bad:
            r = ann.post(f"/api/ai/ink/{route}", json=body)
            assert r.status_code == 400 and r.json()["detail"].startswith("invalid handwriting: "), (body, r.text)
        for body in (_body(), _body(HIGHLIGHT)):
            r = ann.post(f"/api/ai/ink/{route}", json=body)
            assert r.status_code == 400 and r.json()["detail"] == "the selection holds no pen strokes"
    assert calls == []


def test_a_transcription_sends_the_picture_and_reads_back_markdown(ann, monkeypatch):
    calls = _fake(monkeypatch, "```markdown\nW is for $W = Fd$\n```")
    canvas = {"kind": "canvas", "width": 595.28, "height": 841.89}
    r = ann.post("/api/ai/ink/text", json={"space": canvas, "strokes": [HIGHLIGHT, LABEL]})
    assert _lines(r) == {"text": "W is for $W = Fd$"}
    (call,) = calls
    assert call["system"] == ink_ai.TEXT_SYSTEM
    assert call["messages"] == [{"role": "user", "content": ink_ai.TEXT_TURN}]
    assert call["max_tokens"] == 8192 and call["timeout"] == 180 and call["retries"] == 1
    # The pen stroke alone, dark on white: the yellow highlighter is not drawn.
    picture = _picture_of(call).convert("RGB")
    colours = {c for _, c in picture.getcolors(1 << 24)}
    assert any(max(c) < 100 for c in colours)
    assert not any(r > 200 and g > 180 and b < 140 for r, g, b in colours)

    _fake(monkeypatch, "[No text]")
    assert _lines(ann.post("/api/ai/ink/text", json=_body(LABEL))) == {"text": ""}
    _fake(monkeypatch, RuntimeError("upstream 500: oops"))
    assert _lines(ann.post("/api/ai/ink/text", json=_body(LABEL))) == {
        "error": "reading the handwriting failed: upstream 500: oops"}


def _dark_rows(picture, threshold=128):
    """The rows of a picture with a pixel darker than ``threshold``."""
    grey = picture.convert("L")
    width, height = grey.size
    data = grey.tobytes()
    return [y for y in range(height) if min(data[y * width:(y + 1) * width]) < threshold]


def test_light_ink_is_drawn_dark_in_the_picture_and_left_as_it_is():
    """White ink, picked for a dark slide or dark paper, would vanish on the
    picture's white paper: it is drawn dark there, and nowhere else."""
    pen, _ = ink_ai.pen_strokes(_ink(_stroke("w", [(100, 100), (200, 140), (150, 200)], color="#ffffff"),
                                     _stroke("y", [(120, 190), (220, 180)], color="rgb(255, 240, 180)")))
    image, shown = ink_ai.picture(pen)
    assert _dark_rows(Image.open(io.BytesIO(image[0])))
    assert [s.color for s in pen.strokes] == ["#ffffff", "rgb(255, 240, 180)"]
    # A dark colour is drawn as it is.
    red, _ = ink_ai.pen_strokes(_ink(_stroke("r", [(100, 100), (200, 140)], color="#d03030", size=6)))
    colours = {c for _, c in Image.open(io.BytesIO(ink_ai.picture(red)[0][0])).convert("RGB").getcolors(1 << 24)}
    assert any(r > 150 and g < 100 and b < 100 for r, g, b in colours)


def test_the_picture_reaches_handwriting_past_2000_pt_on_a_large_pdf_page():
    """A sheet's paper is held to 2000 pt a side, a PDF page's space is
    not: an A0 poster's handwriting far down the page is in the picture,
    and ``shown`` is where it is."""
    a0 = {"kind": "pdf-page", "page": 1, "width": 2384, "height": 3370}
    lines = [_stroke(f"l{y}", [(500, y), (700, y + 6), (900, y)]) for y in (1950, 2100, 3000)]
    pen, _ = ink_ai.pen_strokes(_ink(*lines, space=a0))
    image, shown = ink_ai.picture(pen)
    x0, y0, x1, y1 = inkmod.bounding_box(pen)
    assert shown[0] <= x0 and shown[1] <= y0 and shown[2] >= x1 and shown[3] >= y1
    picture = Image.open(io.BytesIO(image[0]))
    rows = _dark_rows(picture)
    for y in (1950, 2100, 3000):
        row = (y + 3 - shown[1]) / (shown[3] - shown[1]) * picture.size[1]
        assert any(abs(r - row) < 12 for r in rows), y
    # One line alone at y = 2500 is the picture's middle.
    pen, _ = ink_ai.pen_strokes(_ink(_stroke("far", [(500, 2500), (900, 2500)]), space=a0))
    image, shown = ink_ai.picture(pen)
    assert shown[1] < 2500 < shown[3] and _dark_rows(Image.open(io.BytesIO(image[0])))


def test_a_transcription_needs_a_model_that_sees_pictures(ann, monkeypatch):
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *a: (False, "models.dev"))
    calls = _fake(monkeypatch, "hello")
    r = ann.post("/api/ai/ink/text", json=_body(LABEL))
    assert r.status_code == 400
    assert r.json()["detail"] == ("This model cannot see pictures, which reading handwriting needs; "
                                  "pick another handwriting model in Settings → AI → Models.")
    assert calls == []
    # Unknown is not "cannot": the picture goes, as chat sends it.
    monkeypatch.setattr("gamma.ai_catalog.image_input", lambda *a: (None, ""))
    assert _lines(ann.post("/api/ai/ink/text", json=_body(LABEL))) == {"text": "hello"}


def test_a_reply_cut_off_at_its_length_limit_is_an_error_not_an_answer(ann, monkeypatch):
    """A transcription stopped partway would replace all the strokes with
    part of their text, a shapes object left open read as no shapes: both
    routes answer an error, and the strokes stay as drawn."""
    def cut(call):
        call["on_stop"]("length")
        return ("Energy and its\n- kinetic" if call["system"] == ink_ai.TEXT_SYSTEM
                else '{"shapes": [{"kind": "line", "strokes": [1], "from": [0, 0], "to": [40, 30]}, {"kind"')
    _fake(monkeypatch, cut)
    assert _lines(ann.post("/api/ai/ink/text", json=_body(LABEL))) == {
        "error": "reading the handwriting failed: the AI's answer was cut off at its length limit; select less"}
    assert _lines(ann.post("/api/ai/ink/shapes", json=_body(LABEL))) == {
        "error": "redrawing failed: the AI's answer was cut off at its length limit; select less"}

    def ended(call):
        call["on_stop"]("end_turn")
        return "W"
    _fake(monkeypatch, ended)
    assert _lines(ann.post("/api/ai/ink/text", json=_body(LABEL))) == {"text": "W"}


def test_a_thinking_service_gets_the_chats_larger_reply_cap(ann, monkeypatch):
    """Where a service's models count their thinking toward the cap, 8192
    can go on the thinking alone: the chat's cap is used, never less."""
    calls = _fake(monkeypatch, "W")
    monkeypatch.setattr("gamma.ai_catalog.reply_cap", lambda conf, model: 32768)
    _lines(ann.post("/api/ai/ink/text", json=_body(LABEL)))
    monkeypatch.setattr("gamma.ai_catalog.reply_cap", lambda conf, model: 4096)
    _lines(ann.post("/api/ai/ink/text", json=_body(LABEL)))
    assert [c["max_tokens"] for c in calls] == [32768, 8192]


def test_a_selection_past_the_stroke_files_caps_is_a_plain_413(ann, monkeypatch):
    """Too many strokes, samples or bytes is said in a sentence, not the
    validator's dump; and highlighters, which are never offered, count
    toward none of it."""
    calls = _fake(monkeypatch, '{"shapes": []}')
    many = [_stroke(f"p{i}", [(10, 10), (12, 12)], size=0.6) for i in range(inkmod.MAX_STROKES + 1)]
    r = ann.post("/api/ai/ink/text", json=_body(*many))
    assert r.status_code == 413 and r.json()["detail"] == "Too much handwriting to convert at once; select less."
    r = ann.post("/api/ai/ink/shapes", json=_body(*many))
    assert r.status_code == 413 and r.json()["detail"] == ink_ai.TOO_MANY_STROKES
    monkeypatch.setattr(inkmod, "MAX_SAMPLES", 4)
    for route in ("text", "shapes"):
        r = ann.post(f"/api/ai/ink/{route}", json=_body(LABEL))
        assert r.status_code == 413 and "select less" in r.json()["detail"], route
    monkeypatch.setattr(inkmod, "MAX_SAMPLES", 500_000)
    monkeypatch.setattr(inkmod, "MAX_BYTES", 200)
    r = ann.post("/api/ai/ink/text", json=_body(LABEL))
    assert r.status_code == 413 and r.json()["detail"] == "Too much handwriting to convert at once; select less."
    monkeypatch.setattr(inkmod, "MAX_BYTES", 6 * 1024 * 1024)
    assert calls == []
    # 10 pen strokes and 5000 highlighters: the highlighters are not read,
    # nor do they move the indices the answer names.
    highlights = [_stroke(f"h{i}", [(10, 20), (90, 20)], tool="highlighter") for i in range(inkmod.MAX_STROKES)]
    pens = [_stroke(f"p{i}", [(100, 100 + 10 * i), (300, 100 + 10 * i)]) for i in range(10)]
    _fake(monkeypatch, _reply({"kind": "line", "strokes": [2], "from": [0, 10], "to": [200, 10]}))
    out = _lines(ann.post("/api/ai/ink/shapes", json=_body(*highlights[:3], *pens, *highlights[3:])))
    assert [s["strokes"] for s in out["shapes"]] == [[4]]
    _fake(monkeypatch, "lines")
    assert _lines(ann.post("/api/ai/ink/text", json=_body(*pens, *highlights))) == {"text": "lines"}
    # Malformed pen strokes are still the 400, named.
    r = ann.post("/api/ai/ink/text", json=_body({**LABEL, "color": "red"}))
    assert r.status_code == 400 and r.json()["detail"].startswith("invalid handwriting: ")


def test_the_calls_tokens_are_counted_as_handwriting(ann, monkeypatch):
    assert ann.delete("/api/ai/usage").status_code == 200

    def counted(call):
        call["on_usage"]({"input": 120, "output": 30, "cache_read": 0, "cache_write": 0})
        return "[no text]" if call["system"] == ink_ai.TEXT_SYSTEM else '{"shapes": []}'
    _fake(monkeypatch, counted)
    _lines(ann.post("/api/ai/ink/text", json=_body(LABEL)))
    _lines(ann.post("/api/ai/ink/shapes", json=_body(LABEL)))
    kinds = ann.get("/api/ai/usage").json()["kinds"]
    assert set(kinds) == {"ink"} and kinds["ink"]["calls"] == 2 and kinds["ink"]["input"] == 240


def test_the_routes_need_an_ai_provider():
    # Own TestClient: logging the shared session client in as guest would
    # leak that cookie into later test modules.
    from gamma.app import app

    c = TestClient(app)
    assert c.post("/api/login-guest").status_code == 200
    for route in ("shapes", "text"):
        assert c.post(f"/api/ai/ink/{route}", json=_body(LABEL)).status_code == 503
