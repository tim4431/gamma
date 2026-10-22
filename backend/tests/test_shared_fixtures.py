"""Rules the frontend mirrors, pinned by one set of cases both sides read:
tests/shared/*.json at the repository root. The node half is
frontend/tests/textnorm.test.mjs, ink.test.mjs, inkRender.test.mjs,
finishStroke.test.mjs, notebook.test.mjs, mentions.test.mjs, textBox.test.mjs,
shapes.test.mjs and shapeTool.test.mjs; a case added here fails whichever
side drifts. The mirror's cases (synctree.json, textmerge.json) have only
this Python side; the client's recognisers (inkshapes.json, inkscribble.json)
have only the node side, and this side checks their shape, but both sides
draw inkshapes.json's ``samples`` and count the samples of its Shapes tool
drags."""

import base64
import json
import math
import re
from pathlib import Path

import pytest

from gamma import ink as inkmod
from gamma.textnorm import fuzzy_pattern, normalize_text

SHARED = Path(__file__).resolve().parents[2] / "tests" / "shared"


def _load(name):
    return json.loads((SHARED / name).read_text(encoding="utf-8"))


TEXTNORM = _load("textnorm.json")
INKMERGE = _load("inkmerge.json")


@pytest.mark.parametrize("case", TEXTNORM["normalize"], ids=[c["note"] for c in TEXTNORM["normalize"]])
def test_normalize_text(case):
    assert normalize_text(case["input"]) == case["output"]


@pytest.mark.parametrize("case", TEXTNORM["fuzzy"], ids=[c["note"] for c in TEXTNORM["fuzzy"]])
def test_fuzzy_pattern(case):
    pat = fuzzy_pattern(case["query"], case=case.get("case", False), whole=case.get("whole", False))
    if "pattern" in case and case["pattern"] is None:
        assert pat is None
        return
    assert pat is not None
    assert bool(pat.search(case["text"])) is case["match"], pat.pattern


INK_SPACE = {"kind": "pdf-page", "page": 1, "width": 612, "height": 792}
INK_DEFAULT = {"tool": "pen", "color": "#1f1f1f", "size": 1.6, "opacity": 1, "pen": True, "ch": "xy"}
INK_LOOK_KEYS = ("color", "size", "opacity", "brush", "dash", "nib", "band", "fill", "from", "tool", "pen")


def _merge_file(strokes):
    """An ink file of the fixture's ``[id, v]`` / ``[id, v, look]`` strokes:
    ``v`` places the stroke (its geometry), ``look`` spreads over the
    default stroke."""
    if strokes is None:
        return None
    out = []
    for entry in strokes:
        sid, v = entry[0], entry[1]
        look = entry[2] if len(entry) > 2 else {}
        out.append({"id": sid, **INK_DEFAULT, "pts": [100 + 100 * v, 100], **look})
    return inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": INK_SPACE, "strokes": out})


def _merge_entry(stroke):
    """A stroke back in the fixture's shape: ``[id, v]`` plus the keys that
    differ from the default (or ``from``), when any."""
    d = inkmod.to_dict(stroke)
    look = {k: d[k] for k in INK_LOOK_KEYS if k in d and (k == "from" or d[k] != INK_DEFAULT.get(k))}
    return [stroke.id, (stroke.pts[0] - 100) // 100] + ([look] if look else [])


@pytest.mark.parametrize("case", INKMERGE["cases"], ids=[c["note"] for c in INKMERGE["cases"]])
def test_ink_merge(case):
    base_ids = case.get("base_ids") if case["base"] is None else None
    got = inkmod.merge_ink(_merge_file(case["base"]), _merge_file(case["ours"]), _merge_file(case["theirs"]),
                           base_ids=base_ids)
    assert [_merge_entry(s) for s in got.ink.strokes] == case["result"]
    assert got.clean is case["clean"]
    if "lost_ours" in case:
        assert sorted(got.lost_ours) == sorted(case["lost_ours"])
    if "returned" in case:
        assert sorted(got.returned) == sorted(case["returned"])


INKV2 = _load("inkv2.json")


@pytest.mark.parametrize("case", INKV2["cases"], ids=[c["note"] for c in INKV2["cases"]])
def test_ink_upgrade_v1(case):
    """The one-time version 1 → 2 conversion, byte for byte, and idempotent."""
    assert inkmod.dumps(inkmod.upgrade_v1(json.loads(case["v1"]))) == case["v2"].encode()
    assert inkmod.dumps(inkmod.upgrade_v1(json.loads(case["v2"]))) == case["v2"].encode()
    # the canonical bytes of the parsed version 2 file are the file itself
    assert inkmod.dumps(inkmod.parse_ink(case["v2"].encode())) == case["v2"].encode()
    # version 1 is refused everywhere but the converter
    with pytest.raises(inkmod.InkVersionError):
        inkmod.parse_ink(case["v1"].encode())


INKCODEC = _load("inkcodec.json")


@pytest.mark.parametrize("case", INKCODEC["encode"], ids=[c["note"] for c in INKCODEC["encode"]])
def test_ink_encode_points(case):
    """encode_points writes encodePoints' integers: Math.round's halves, the client's clamps and defaults."""
    assert inkmod.encode_points(case["samples"], case["ch"]) == case["pts"]


@pytest.mark.parametrize("case", INKCODEC["files"], ids=[c["note"] for c in INKCODEC["files"]])
def test_ink_canonical_numbers(case):
    """dumps writes serializeInk's bytes: every number as JSON.stringify writes it."""
    assert inkmod.dumps(json.loads(case["file"])) == case["bytes"].encode()
    assert inkmod.dumps(inkmod.parse_ink(case["file"].encode())) == case["bytes"].encode()


INKWIDTH = _load("inkwidth.json")


def _width_file(stroke):
    return inkmod.parse_ink({"format": "gamma-ink", "version": 2,
                             "space": {"kind": "canvas", "width": 10, "height": 10}, "strokes": [stroke]})


@pytest.mark.parametrize("row", INKWIDTH["rows"], ids=[r["note"] for r in INKWIDTH["rows"]])
def test_ink_width(row):
    """The drawn width is size × w (or size without the channel) and the
    drawn opacity opacity × o (or opacity): the server reads the baked
    multipliers and knows no law. A multi-sample row is encoded with the
    multipliers the client baked: its w slots are the row's w_encs, the
    polyline's widths size × ws — or, a row without ws (a stroke with a
    shape record, constant width whatever its brush), no w channel and the
    size at every sample."""
    stroke = {"id": "a", "tool": row["tool"], "pen": row["pen"], "size": row["size"], "opacity": 0.8}
    if row["brush"]:
        stroke["brush"] = row["brush"]
    if row.get("nib") is not None:
        stroke["nib"] = row["nib"]
    if row.get("shape"):
        stroke["shape"] = row["shape"]
    if "samples" in row:
        pressure, shaded, ws = any(s[2] is not None for s in row["samples"]), "os" in row, row["ws"]
        ch = "xy" + ("p" if pressure else "") + "t" + ("w" if ws else "") + ("o" if shaded else "")
        samples = [{"x": x, "y": y, "t": t, **({"w": ws[i]} if ws else {}), **({"p": p} if pressure else {})}
                   for i, (x, y, p, t, _a) in enumerate(row["samples"])]
        for sample, o in zip(samples, row.get("os") or []):
            sample["o"] = o
        stroke.update(ch=ch, pts=inkmod.encode_points(samples, ch))
        if ws:
            assert stroke["pts"][ch.index("w")::len(ch)] == row["w_encs"]
        else:
            assert row["w_encs"] is None
        s = _width_file(stroke).strokes[0]
        widths = [row["size"] * w for w in ws] if ws else [row["size"]] * len(samples)
        assert [w for _, _, w in inkmod.stroke_polyline(s)] == pytest.approx(widths)
        sample = inkmod.decode_stroke(s)[row["at"]]
    else:
        ch, pts = "xy", [100, 100]
        for letter in ("w", "o"):
            if row.get(f"{letter}_enc") is not None:
                ch, pts = ch + letter, pts + [row[f"{letter}_enc"]]
        stroke.update(ch=ch, pts=pts)
        s = _width_file(stroke).strokes[0]
        sample = inkmod.decode_stroke(s)[0]
        assert inkmod.stroke_polyline(s)[0][2] == pytest.approx(row["width"], abs=1e-6)
    assert sample.get("w") == row["w"]
    assert sample.get("o") == row.get("o")
    assert inkmod.stroke_width(s, sample) == pytest.approx(row["width"], abs=1e-6)
    assert inkmod.stroke_opacity(s, sample) == pytest.approx(0.8 * (row.get("o") or 1))


INKDASH = _load("inkdash.json")


@pytest.mark.parametrize("row", INKDASH["patterns"], ids=[r["note"] for r in INKDASH["patterns"]])
def test_ink_dash_pattern(row):
    """The dash table is the client's: the pattern from the nominal size
    alone, the SVG dasharray and the PDF d operator. The server strokes a
    pressure stroke's dashes along its mean-width centreline, so it writes
    a dasharray where the client's outlined runs need none."""
    ch = "xyptw" if row["w"] else "xypt"
    samples = [{"x": 10 + 5 * i, "y": 20, "p": 0.5, "t": 10 * i, "w": 1.0 + 0.1 * i} for i in range(3)]
    stroke = {"id": "d", "size": row["size"], "ch": ch, "pts": inkmod.encode_points(samples, ch)}
    if row["dash"]:
        stroke["dash"] = row["dash"]
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": INK_SPACE, "strokes": [stroke]})
    svg, ops = inkmod.to_svg(ink), inkmod.pdf_path_ops(ink, lambda x, y: (x, y)).decode()
    pattern = inkmod.dash_pattern(ink.strokes[0])
    if row["on"] is None:
        assert pattern is None and "stroke-dasharray" not in svg and "] 0 d" not in ops
        return
    assert pattern == (pytest.approx(row["on"]), pytest.approx(row["off"]))
    dasharray = f'{row["on"]:g} {row["off"]:g}'
    assert row["dasharray"] in (None, dasharray) and f'stroke-dasharray="{dasharray}"' in svg
    assert row["pdf"] in ops and ops.index(row["pdf"]) < ops.index(" S\n") < ops.index("[] 0 d")


def _rounded(samples):
    return [{k: round(v, 3) for k, v in s.items()} for s in samples]


def test_ink_dash_runs():
    """The on-runs of a dashed pressure stroke by arc length with their
    boundary samples lerped — the run splitting the pencil's bands use."""
    case = INKDASH["runs"]
    stroke = inkmod.Stroke.model_validate({"id": "r", "size": case["size"], "dash": case["dash"], "pts": [0, 0]})
    assert inkmod.dash_pattern(stroke) == (case["on"], case["off"])
    assert [_rounded(run) for run in inkmod.dash_runs(stroke, case["samples"])] == [r["samples"] for r in case["runs"]]
    # dotted: one dot at the middle of each on-run, size × w across (the
    # runs are shorter than the sample spacing, so the middle is their ends' mean)
    case = INKDASH["dots"]
    stroke = stroke.model_copy(update={"dash": case["dash"]})
    runs = inkmod.dash_runs(stroke, case["samples"])
    middles = [{k: (run[0][k] + run[-1][k]) / 2 for k in ("x", "y", "w")} for run in runs]
    assert [{**_rounded([m])[0], "diameter": round(case["size"] * m["w"], 3)} for m in middles] == \
        [{k: d[k] for k in ("x", "y", "w", "diameter")} for d in case["dots"]]
    # dash_dots, the dots the nib's dotted stroke draws, is the client's dashDots
    dots = inkmod.dash_dots(stroke, case["samples"])
    assert [{**_rounded([{k: q[k] for k in ("x", "y", "w")}])[0], "diameter": round(case["size"] * q["w"], 3)}
            for q in dots] == [{k: d[k] for k in ("x", "y", "w", "diameter")} for d in case["dots"]]


# tests/shared/inkgrain.json: the pencil's grain tile, which the exporters
# fill a pencil with as the screen does.
INKGRAIN = _load("inkgrain.json")


def _png_chunks(png: bytes) -> dict:
    out, i = {}, 8
    while i < len(png):
        n = int.from_bytes(png[i:i + 4], "big")
        out[png[i + 4:i + 8]] = png[i + 8:i + 8 + n]
        i += 12 + n
    return out


@pytest.mark.parametrize("tile", INKGRAIN["tiles"], ids=[t["note"] for t in INKGRAIN["tiles"]])
def test_ink_grain_tile(tile):
    """grain_png writes grainUri's PNG byte for byte, in grain_rgb's colour."""
    assert (inkmod.GRAIN_PX, inkmod.GRAIN_UNITS) == (INKGRAIN["GRAIN_PX"], INKGRAIN["GRAIN_UNITS"])
    png = base64.b64decode(tile["png"])
    assert inkmod.grain_png(tile["color"]) == png
    assert _png_chunks(png)[b"PLTE"] == bytes(inkmod.grain_rgb(tile["color"])) * 4
    # only the palette differs from one colour to another
    assert _png_chunks(png)[b"IDAT"] == _png_chunks(base64.b64decode(INKGRAIN["tiles"][0]["png"]))[b"IDAT"]


def test_ink_grain_tile_in_the_svg_export():
    """A pencil's pattern holds the fixture's tile, for every colour a file can hold."""
    tiles = {t["color"]: t["png"] for t in INKGRAIN["tiles"]}
    for color in ("#1f1f1f", "#DC2626", "rgb(37, 99, 235)"):
        ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": INK_SPACE, "strokes": [
            {"id": "p", "brush": "pencil", "color": color, "ch": "xy", "pts": [1000, 1000, 500, 0]}]})
        assert f'href="data:image/png;base64,{tiles[color]}"' in inkmod.to_svg(ink)


# tests/shared/inknib.json: the calligraphy nib, which the exporters draw as
# the screen does — the SVG export the screen's own path.
INKNIB = _load("inknib.json")
NIB_SPACE = {"kind": "canvas", "width": 600, "height": 600}
_NUM = r"-?\d+(?:\.\d+)?"


def _nib_stroke(case) -> dict:
    """The stroke a case draws: its own, or a calligraphy stroke through its samples."""
    if "stroke" in case:
        return case["stroke"]
    samples = [{"x": x, "y": y} for x, y in case["samples"]]
    return {"id": "n", "brush": "calligraphy", "nib": case["nib"], "size": case["size"], "ch": "xy",
            "pts": inkmod.encode_points(samples, "xy")}


def _d_pieces(d: str) -> list:
    """A path's pieces: each polygon as its [x, y] points, each dot as ("dot", cx, cy, r)."""
    out = []
    for piece in filter(str.strip, d.split("M")):
        if " a" in piece:
            x0, y, r = (float(v) for v in re.match(rf"({_NUM}),({_NUM}) a({_NUM}),", piece).groups())
            out.append(("dot", x0 + r, y, r))
        else:
            out.append([[float(v) for v in xy.split(",")] for xy in piece.replace("Z", "").strip().split(" L")])
    return out


def _pdf_pieces(ops: str) -> list:
    """The pieces of pdf_path_ops' one nib fill, in the same terms (to_pdf the identity)."""
    out = []
    for sub in re.findall(r"(\S+ \S+ m .*? h)", ops):
        nums = [float(v) for v in re.findall(_NUM, sub)]
        if " c " in sub + " ":       # a dot: four cubics from (cx + r, cy)
            xs, ys = nums[0::2], nums[1::2]
            r = (max(xs) - min(xs)) / 2
            out.append(("dot", nums[0] - r, nums[1], r))
        else:
            out.append([[x, y] for x, y in zip(nums[0::2], nums[1::2])])
    return out


def _close_pieces(got, want, tol):
    assert len(got) == len(want)
    for g, w in zip(got, want):
        if isinstance(w, tuple):
            assert g[0] == "dot" and all(abs(a - b) <= tol for a, b in zip(g[1:], w[1:])), (g, w)
        else:
            assert len(g) == len(w) and all(abs(a - b) <= tol for p, q in zip(g, w) for a, b in zip(p, q)), (g, w)


@pytest.mark.parametrize("case", INKNIB["cases"] + INKNIB["strokes"], ids=[c["note"] for c in INKNIB["cases"] + INKNIB["strokes"]])
def test_ink_nib(case):
    """nib_outline is nibOutline: the SVG export writes the screen's path
    character for character, and the PDF writers fill the same pieces."""
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": NIB_SPACE, "strokes": [_nib_stroke(case)]})
    svg = inkmod.to_svg(ink)
    assert re.findall(r'<path d="([^"]*)"', svg) == [case["d"]]
    if "samples" in case:   # the polygons themselves, before any rounding
        polys = inkmod.nib_outline([{"x": x, "y": y} for x, y in case["samples"]], case["size"], case["nib"])
        _close_pieces([[list(p) for p in poly] for poly in polys], _d_pieces(case["d"]), 0.005 + 1e-9)
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, y)).decode()
    assert ops.count("\nf\n") == 1, "every piece in one nonzero fill"
    dotted = case.get("stroke", {}).get("dash") == "dotted"    # (a dot's centre is read off two rounded numbers)
    _close_pieces(_pdf_pieces(ops), _d_pieces(case["d"]), 0.02 if dotted else 0.01 + 1e-9)


# tests/shared/inkoutline.json: a stroke the screen outlines whole
# (penOutline), which the exporters draw the same.
INKOUTLINE = _load("inkoutline.json")


@pytest.mark.parametrize("case", INKOUTLINE["strokes"], ids=[c["note"] for c in INKOUTLINE["strokes"]])
def test_ink_whole_outline(case):
    """pen_outline is penOutline: perfect-freehand at the page's own scale,
    so the SVG export's outline is the screen's, number for number (each
    written to the hundredth; toFixed and Python round a tie apart)."""
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": NIB_SPACE, "strokes": [case["stroke"]]})
    (d,) = re.findall(r'<path d="([^"]*)"', inkmod.to_svg(ink))
    got, want = ([float(v) for v in re.findall(_NUM, path)] for path in (d, case["d"]))
    assert len(got) == len(want) and all(abs(a - b) <= 0.01 + 1e-9 for a, b in zip(got, want))


def test_ink_nib_fixture_covers_the_cases():
    cases, strokes = INKNIB["cases"], INKNIB["strokes"]
    assert {0, 45, 90, 135, 179} <= {c["nib"] for c in cases}
    assert any(len(c["samples"]) == 1 for c in cases), "a single sample"
    assert any(any(a == b for a, b in zip(c["samples"], c["samples"][1:])) for c in cases), "repeated samples"
    assert any("turn back" in c["note"] for c in cases), "a turn back on itself"
    assert {s["stroke"].get("dash") for s in strokes} == {None, "dashed", "dotted"}
    assert all(s["stroke"]["brush"] == "calligraphy" and "shape" not in s["stroke"] for s in strokes)


PAPER = _load("paper.json")


@pytest.mark.parametrize("case", PAPER["normalize"], ids=[c["note"] for c in PAPER["normalize"]])
def test_paper_normalize(case):
    from gamma.notebook import normalize_paper
    assert normalize_paper(case["input"], case["fallback"]) == case["output"]


@pytest.mark.parametrize("case", PAPER["lines"], ids=[c["note"] for c in PAPER["lines"]])
def test_paper_lines(case):
    from gamma.notebook import normalize_paper, paper_lines
    geo = paper_lines(normalize_paper(case["paper"]))
    assert geo == {"lines": case["lines"], "dots": case["dots"]}


@pytest.mark.parametrize("case", PAPER["sheets"], ids=[c["note"] for c in PAPER["sheets"]])
def test_page_sheets(case):
    from gamma.notebook import sheets_of
    blocks = [{"id": bid, "parent_id": b["parent"], "position": b["position"], "properties": b["props"]}
              for bid, b in case["blocks"].items()]
    got = [{"id": s["id"], "paper": s["paper"],
            "ink": [b["id"] for b in s["blocks"] if (b["properties"] or {}).get("ink_url")]}
           for s in sheets_of(blocks, case["page"])]
    assert got == case["sheets"]


TEXTBOX = _load("textbox.json")


@pytest.mark.parametrize("case", TEXTBOX["normalize"], ids=[c["note"] for c in TEXTBOX["normalize"]])
def test_text_box_normalize(case):
    from gamma.text_box import normalize_text_box
    # as JSON text, so True is not 1 and a whole number is written as the client writes it (23, not 23.0)
    got = json.dumps(normalize_text_box(case["input"]), sort_keys=True)
    assert got == json.dumps(case["output"], sort_keys=True)


TEXTBOXMERGE = _load("textboxmerge.json")


@pytest.mark.parametrize("case", TEXTBOXMERGE["merge"], ids=[c["note"] for c in TEXTBOXMERGE["merge"]])
def test_text_box_merge(case):
    from gamma.text_box import merge_text_box
    got = json.dumps(merge_text_box(case["stored"], case["mine"], case["base"]), sort_keys=True)
    assert got == json.dumps(case["result"], sort_keys=True)


TEXTMERGE = _load("textmerge.json")


@pytest.mark.parametrize("case", TEXTMERGE["merge"], ids=[c["note"] for c in TEXTMERGE["merge"]])
def test_text_merge(case):
    from gamma import textmerge
    assert list(textmerge.merge(case["base"], case["ours"], case["theirs"])) == case["result"]


@pytest.mark.parametrize("case", TEXTMERGE["contains"], ids=[c["note"] for c in TEXTMERGE["contains"]])
def test_text_contains(case):
    from gamma import textmerge
    assert textmerge.contains(case["base"], case["ours"], case["theirs"]) is case["result"]


@pytest.mark.parametrize("case", TEXTMERGE["map_offset"], ids=[c["note"] for c in TEXTMERGE["map_offset"]])
def test_text_map_offset(case):
    from gamma import textmerge
    assert textmerge.map_offset(case["src"], case["dst"], case["offset"]) == case["result"]


SYNCTREE = _load("synctree.json")


@pytest.mark.parametrize("case", SYNCTREE["diff"], ids=[c["note"] for c in SYNCTREE["diff"]])
def test_sync_tree_diff(case):
    """The mirror's tree rules: the ops between two trees, applied back."""
    from gamma import sync_tree
    page = SYNCTREE["page"]
    assert sync_tree.diff(case["base"], case["target"], page) == case["ops"]
    assert sync_tree.apply(case["base"], case["ops"]) == case["applied"]
    assert sorted(sync_tree.moved(case["base"], case["target"])) == case["moved"]


@pytest.mark.parametrize("case", SYNCTREE["unlanded"], ids=[c["note"] for c in SYNCTREE["unlanded"]])
def test_sync_unlanded(case):
    """What a push whose answer was lost sends again."""
    from gamma import sync_engine
    assert list(sync_engine._unlanded(case["op"], case["base"], case["remote"])) == case["result"]


MENTIONS = _load("mentions.json")


@pytest.mark.parametrize("case", MENTIONS["scan"], ids=[c["note"] for c in MENTIONS["scan"]])
def test_mention_scan(case):
    from gamma import mentions
    assert mentions.scan(case["text"]) == case["tokens"]


# tests/shared/inkshapes.json and inkscribble.json: the client's recognisers
# (frontend/src/ink/shapes.js, gestures.js), which the server never runs;
# this side pins the fixtures' shape, so a case the node tests read is well
# formed, and that every shape a snapped stroke is stored with is one
# parse_ink accepts (a recognised circle is written as an ellipse). The
# samples a shape record is drawn as (shapes.js shapeSamples) the server
# draws too (ink.shape_samples): inkshapes.json ``samples`` and the Shapes
# tool's counts hold both to the same answers.
INKSHAPES = _load("inkshapes.json")
INKSCRIBBLE = _load("inkscribble.json")
SHAPE_PARAMS = {
    "line": {"x1", "y1", "x2", "y2"},
    "arrow": {"x1", "y1", "x2", "y2", "head"},
    "triangle": {"x1", "y1", "x2", "y2", "x3", "y3"},
    "rect": {"cx", "cy", "w", "h", "angle"},
    "polygon": {"cx", "cy", "r", "n", "angle"},
    "circle": {"cx", "cy", "r"},
    "ellipse": {"cx", "cy", "rx", "ry", "angle"},
}
# What the Shapes tool draws (frontend/src/ink/shapeTool.js; inkshapes.json `tool`).
SHAPE_TOOL_KINDS = ("line", "arrow", "rect", "ellipse")


def _check_samples(samples, timed=True):
    """One stroke's samples as the fixtures write them: [x, y, p, t] in page
    points to 0.01, p in 0..1, t integer ms from 0 and never going back."""
    assert len(samples) >= 2
    last_t = 0
    for i, s in enumerate(samples):
        assert len(s) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in s), s
        x, y, p, t = s
        assert round(x, 2) == x and round(y, 2) == y
        assert 0 <= p <= 1
        assert isinstance(t, int) and t >= last_t and (t == 0 if i == 0 else True), s
        last_t = t


def _stored_shape(expect):
    """The shape record the client stores for a recognised shape (ink/finishStroke.js shapeRecord)."""
    kind, params = expect["kind"], expect["params"]
    if kind == "circle":
        return {"kind": "ellipse", "params": {"cx": params["cx"], "cy": params["cy"], "rx": params["r"], "ry": params["r"], "angle": 0}}
    return {"kind": kind, "params": dict(params)}


def test_ink_fixture_notes_are_unique():
    for fixture in (INKSHAPES, INKSCRIBBLE):
        notes = [c["note"] for c in fixture["cases"]]
        assert len(set(notes)) == len(notes)
    kinds = {c["expect"]["kind"] for c in INKSHAPES["cases"] if c["expect"]}
    assert kinds == set(SHAPE_PARAMS), "every kind the recogniser answers has a positive case"
    assert sum(1 for c in INKSHAPES["cases"] if c["expect"] is None) >= 4, "letters and zigzags that must not recognise"
    notes = [c["note"] for c in INKSHAPES["tool"]]
    assert len(set(notes)) == len(notes)
    assert {c["kind"] for c in INKSHAPES["tool"]} == set(SHAPE_TOOL_KINDS), "every kind the Shapes tool draws has a drag"
    assert any(c["expect"] is None for c in INKSHAPES["tool"]), "a drag too short to draw"
    notes = [c["note"] for c in INKSHAPES["samples"]]
    assert len(set(notes)) == len(notes)
    assert {c["kind"] for c in INKSHAPES["samples"]} >= set(SHAPE_PARAMS), "every kind shapes.js draws has samples"
    assert any(c["pts"] == [] for c in INKSHAPES["samples"]), "a kind drawn from no params"
    assert any(c["scribble"] for c in INKSCRIBBLE["cases"]) and any(not c["scribble"] for c in INKSCRIBBLE["cases"])


@pytest.mark.parametrize("case", INKSHAPES["cases"], ids=[c["note"] for c in INKSHAPES["cases"]])
def test_ink_shape_fixture(case):
    assert set(case) == {"note", "k", "samples", "expect"}
    assert case["k"] > 0
    _check_samples(case["samples"])
    expect = case["expect"]
    if expect is None:
        return
    assert set(expect) <= {"kind", "params", "tol"} and {"kind", "params"} <= set(expect)
    assert set(expect["params"]) == SHAPE_PARAMS[expect["kind"]], expect
    assert expect.get("tol", 2) > 0
    if expect["kind"] == "polygon":
        assert isinstance(expect["params"]["n"], int) and 5 <= expect["params"]["n"] <= 8
    # the stroke the client commits for it parses on the server, shape and all
    shape = _stored_shape(expect)
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": INK_SPACE, "strokes": [
        {"id": "s", "tool": "pen", "pen": False, "color": "#1f1f1f", "size": 1.6, "ch": "xyt",
         "pts": [100, 100, 0, 1000, 500, 300], "shape": shape}]})
    assert ink.strokes[0].shape.kind == shape["kind"] and ink.strokes[0].shape.params == shape["params"]
    assert shape["kind"] in inkmod.SHAPE_KINDS


@pytest.mark.parametrize("case", INKSHAPES["tool"], ids=[c["note"] for c in INKSHAPES["tool"]])
def test_ink_shape_tool_fixture(case):
    """A drag of the Shapes tool, which the server never runs: the case is
    well formed, only a drag under 4 css px draws nothing, and the stroke it
    commits — pen false, brush monoline, written xyt, its shape record —
    parses on the server."""
    assert set(case) == {"note", "kind", "from", "to", "shift", "k", "expect"}
    assert case["kind"] in SHAPE_TOOL_KINDS and isinstance(case["shift"], bool) and case["k"] > 0
    for point in (case["from"], case["to"]):
        assert len(point) == 2 and all(isinstance(v, (int, float)) and round(v, 2) == v for v in point)
    expect = case["expect"]
    reach = math.hypot(case["to"][0] - case["from"][0], case["to"][1] - case["from"][1]) * case["k"]
    if expect is None:
        assert reach < 4, "only a drag under 4 css px draws nothing"
        return
    assert reach >= 4
    assert set(expect) == {"params", "count", "closed"}
    params = expect["params"]
    assert set(params) == SHAPE_PARAMS[case["kind"]] - {"head"}, "the tool writes no arrow head"
    assert all(isinstance(v, (int, float)) and round(v, 2) == v for v in params.values()), "on the 1/100 pt grid"
    assert isinstance(expect["count"], int) and expect["count"] >= 2
    assert expect["closed"] is (case["kind"] in ("rect", "ellipse"))
    if case["kind"] == "line":
        assert expect["count"] == 2
    if case["kind"] == "ellipse":
        assert params["rx"] >= params["ry"] and params["angle"] in (0, 90) and expect["count"] >= 49
    if case["kind"] == "rect":
        assert params["angle"] == 0
    drawn = inkmod.shape_samples(case["kind"], params)
    assert len(drawn) == expect["count"] and (drawn[-1] == drawn[0]) is expect["closed"], "the server draws it alike"
    shape = {"kind": case["kind"], "params": dict(params)}
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": INK_SPACE, "strokes": [
        {"id": "s", "tool": "pen", "brush": "monoline", "pen": False, "color": "#1d4ed8", "size": 2, "ch": "xyt",
         "pts": [100, 100, 0, 1000, 500, 300], "shape": shape}]})
    assert ink.strokes[0].shape.kind == case["kind"] and ink.strokes[0].shape.params == params


@pytest.mark.parametrize("case", INKSHAPES["samples"], ids=[c["note"] for c in INKSHAPES["samples"]])
def test_ink_shape_samples(case):
    """A shape record drawn as shapes.js shapeSamples draws it: every sample
    on the 1/100 pt grid, and ink.shape_samples gives each of them."""
    assert set(case) == {"note", "kind", "params", "pts"}
    assert case["kind"] in SHAPE_PARAMS or case["kind"] in inkmod.SHAPE_KINDS
    assert all(isinstance(v, (int, float)) and round(v, 2) == v for v in case["params"].values())
    assert all(len(p) == 2 and all(round(v, 2) == v for v in p) for p in case["pts"])
    assert [list(p) for p in inkmod.shape_samples(case["kind"], case["params"])] == case["pts"]


@pytest.mark.parametrize("case", INKSCRIBBLE["cases"], ids=[c["note"] for c in INKSCRIBBLE["cases"]])
def test_ink_scribble_fixture(case):
    assert {"note", "k", "samples", "scribble"} <= set(case) <= {"note", "k", "samples", "scribble", "groups", "filter", "targets"}
    assert case["k"] > 0 and isinstance(case["scribble"], bool)
    _check_samples(case["samples"])
    if "groups" not in case:
        assert "filter" not in case and "targets" not in case
        return
    assert case["scribble"], "targets are only asked of a scribble"
    assert case["filter"] in ("all", "pen", "highlighter")
    ids_by_group = {}
    for g in case["groups"]:
        assert set(g) == {"gid", "strokes"} and g["gid"] not in ids_by_group
        ids_by_group[g["gid"]] = [s["id"] for s in g["strokes"]]
        for s in g["strokes"]:
            assert {"id", "tool", "size", "samples"} <= set(s) <= {"id", "tool", "brush", "size", "samples"}
            assert s["size"] > 0 and len(s["samples"]) >= 2 and all(len(xy) == 2 for xy in s["samples"])
        assert len(set(ids_by_group[g["gid"]])) == len(ids_by_group[g["gid"]])
    order = list(ids_by_group)
    seen = []
    for target in case["targets"]:
        assert set(target) == {"gid", "ids"} and target["gid"] in ids_by_group and target["ids"]
        seen.append(order.index(target["gid"]))
        file_order = [i for i in ids_by_group[target["gid"]] if i in target["ids"]]
        assert target["ids"] == file_order, "ids in file order"
    assert seen == sorted(seen) and len(set(seen)) == len(seen), "groups in order, each once"
