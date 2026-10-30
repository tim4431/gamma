"""Handwriting (gamma/ink.py, routers/ink.py): the version 2 stroke-file
schema and codec and their limits, the upload endpoints (and their refusal
of version 1), the merge's answer on PUT, the /Ink round trip through the
annotated PDF export and the embedded-annotation importer, and the Markdown
/ notes-PDF renderings of an ink block. The shared fixtures (upgrade, width,
merge cases) run in test_shared_fixtures.py."""

import base64
import io
import json
import math
import re
import zipfile

import pytest
from conftest import make_page, guest_name, workspace_of

from gamma import ink as inkmod

PAGE_W, PAGE_H = 612, 792


def _samples(n=5, x0=100.0, y0=200.0):
    """Samples with the default pen's baked width (w = 1 + (p − 0.5))."""
    return [{"x": x0 + 10 * i, "y": y0 + 3 * i, "p": 0.2 + 0.15 * i, "t": 16 * i, "w": 0.7 + 0.15 * i}
            for i in range(n)]


def _ink(page=1, strokes=None, **space):
    strokes = strokes if strokes is not None else [{
        "id": "s1", "tool": "pen", "color": "#1f1f1f", "size": 2, "opacity": 1, "pen": True,
        "t0": 1757760000000, "ch": "xyptw", "pts": inkmod.encode_points(_samples(), "xyptw"),
    }]
    return {"format": "gamma-ink", "version": 2,
            "space": {"kind": "pdf-page", "page": page, "width": PAGE_W, "height": PAGE_H, **space},
            "strokes": strokes}


def _blank_pdf(pages=1):
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=PAGE_W, height=PAGE_H)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# --- codec ------------------------------------------------------------------------

def test_codec_round_trips_and_delta_encodes():
    ink = inkmod.parse_ink(_ink())
    stroke = ink.strokes[0]
    # deltas after the first sample: x steps of 10 pt = 1000 hundredths, w in thousandths
    assert stroke.pts[:5] == [10000, 20000, 200, 0, 700]
    assert stroke.pts[5:10] == [1000, 300, 350, 16, 150]
    decoded = inkmod.decode_stroke(stroke)
    assert [(round(s["x"], 2), round(s["y"], 2), s["t"]) for s in decoded] == \
        [(100 + 10 * i, 200 + 3 * i, 16 * i) for i in range(5)]
    assert decoded[1]["p"] == pytest.approx(0.35) and decoded[1]["w"] == pytest.approx(0.85)
    assert all(s["rest"] == [] for s in decoded)
    # the drawn width is size × w, read, never computed from p
    assert [w for _, _, w in inkmod.stroke_polyline(stroke)] == pytest.approx([2 * (0.7 + 0.15 * i) for i in range(5)])
    assert inkmod.stroke_width(stroke, decoded[0]) == pytest.approx(1.4)
    # a stroke without w draws its size whatever its pressure
    flat = inkmod.parse_ink(_ink(strokes=[{"id": "f", "ch": "xyp", "size": 3,
                                          "pts": inkmod.encode_points(_samples(), "xyp")}])).strokes[0]
    assert {w for _, _, w in inkmod.stroke_polyline(flat)} == {3}
    assert "w" not in inkmod.decode_stroke(flat)[0]


def test_unknown_channel_letters_ride_along_by_stride():
    """A letter neither codec knows is carried in ``rest`` and written back
    in place, so a cut on an older copy keeps the stride."""
    samples = [{"x": 1, "y": 2, "p": 0.5, "rest": [7]}, {"x": 2, "y": 3, "p": 0.6, "rest": [9]}]
    pts = inkmod.encode_points(samples, "xypq")
    assert pts == [100, 200, 500, 7, 100, 100, 600, 9]
    s = inkmod.parse_ink(_ink(strokes=[{"id": "q", "ch": "xypq", "pts": pts}])).strokes[0]
    decoded = inkmod.decode_stroke(s)
    assert [d["rest"] for d in decoded] == [[7], [9]]
    assert inkmod.encode_points(decoded, "xypq") == pts
    # the reserved o channel decodes as a multiplier too
    o = inkmod.parse_ink(_ink(strokes=[{"id": "o", "ch": "xyo", "pts": [100, 100, 1000, 10, 10, -500]}])).strokes[0]
    assert [d["o"] for d in inkmod.decode_stroke(o)] == [1.0, 0.5]


def test_tilt_defaults_match_the_client_codec():
    """A sample without tilt in a tilted stroke is written upright, a 90 and
    z 0, and the altitude is clamped to 0..90 — as ink.js encodePoints does."""
    samples = [{"x": 1, "y": 1, "a": 40, "z": 120}, {"x": 2, "y": 1}, {"x": 3, "y": 1, "a": 120.4, "z": 7.6}]
    assert inkmod.encode_points(samples, "xyaz") == [100, 100, 40, 120, 100, 0, 90, 0, 100, 0, 90, 8]


def test_bounding_box_and_pdf_position():
    ink = inkmod.parse_ink(_ink())
    x0, y0, x1, y1 = inkmod.bounding_box(ink)
    assert x0 < 100 and x1 > 140 and y0 < 200 and y1 > 212   # half the width around the samples
    pos = inkmod.pdf_position(ink)
    # the highlight shape: the page and its size once, the rects bare
    assert (pos["pageNumber"], pos["width"], pos["height"]) == (1, PAGE_W, PAGE_H)
    assert list(pos["boundingRect"]) == ["x1", "y1", "x2", "y2"] and pos["rects"] == [pos["boundingRect"]]
    assert inkmod.bounding_box(inkmod.parse_ink(_ink(strokes=[]))) is None


def test_monoline_preserves_samples_and_constant_width_in_exports(guest):
    data = _ink()
    data["strokes"][0].update(brush="monoline", ch="xypt", pts=inkmod.encode_points(_samples(), "xypt"))
    r = guest.post("/api/upload-ink", json=data)
    assert r.status_code == 200, r.text
    stored = guest.get(r.json()["url"])
    ink = inkmod.parse_ink(stored.content)
    assert ink.strokes[0].brush == "monoline"
    assert [s["p"] for s in inkmod.decode_stroke(ink.strokes[0])] == pytest.approx([0.2 + 0.15 * i for i in range(5)])
    assert {w for _, _, w in inkmod.stroke_polyline(ink.strokes[0])} == {2}
    # Both SVG export and the notes PDF keep a uniform 2pt stroke despite
    # varying pressure, while the native JSON retains those pressure samples.
    svg = inkmod.to_svg(ink)
    assert svg.count('stroke-width="2"') == 4
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    assert ops.count(b"2.00 w") == 4
    # `brush` is an open string on any tool: a highlighter with one is
    # stored, and draws at its size like any highlighter
    odd = _ink()
    odd["strokes"][0].update(tool="highlighter", brush="monoline")
    assert guest.post("/api/upload-ink", json=odd).status_code == 200


# --- schema -----------------------------------------------------------------------

@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.__setitem__("format", "other"), "format"),
    (lambda d: d["space"].pop("page"), "page"),
    (lambda d: d["strokes"][0].__setitem__("ch", "xx"), "channel"),
    (lambda d: d["strokes"][0].__setitem__("ch", "xyptp"), "channel"),       # a repeated letter
    (lambda d: d["strokes"][0].__setitem__("ch", "xyabcdefghijk"), "ch"),    # over 12 letters
    (lambda d: d["strokes"][0].__setitem__("pts", [1, 2, 3]), "multiple"),
    (lambda d: d["strokes"][0].__setitem__("color", "red"), "color"),
    (lambda d: d["strokes"].append(dict(d["strokes"][0])), "duplicate"),
    (lambda d: d["strokes"][0].__setitem__("tool", "Pen"), "tool"),
    (lambda d: d["strokes"][0].__setitem__("band", 2), "band"),
    (lambda d: d["strokes"][0].__setitem__("dash", "dash-dot"), "dash"),
    (lambda d: d["strokes"][0].__setitem__("nib", 180), "nib"),
    (lambda d: d["strokes"][0].__setitem__("shape", {"kind": "star", "params": {}}), "kind"),
    (lambda d: d["strokes"][0].__setitem__("shape", {"kind": "rect", "params": {str(i): 1 for i in range(65)}}), "64"),
    (lambda d: d["strokes"][0].__setitem__("fill", {"color": "red"}), "color"),
    (lambda d: d["strokes"][0].__setitem__("pts", [100, 100, 500, 0, 4500]), "channel w"),  # w over 4.000
    (lambda d: d["strokes"][0].__setitem__("pts", [100, 100, 500, 0, -600]), "channel w"),  # w below 0
    (lambda d: d["strokes"][0].update(size=80, pts=[100, 100, 500, 0, 1500]), "max(w)"),   # 80 × 1.5 > 100
    (lambda d: d["strokes"][0].__setitem__("note", {"a": {"b": {"c": 1}}}), "deeper"),
    (lambda d: d["strokes"][0].__setitem__("note", "x" * 1100), "string"),
    (lambda d: d["strokes"][0].__setitem__("note", list(range(300))), "array"),
    (lambda d: d["strokes"][0].__setitem__("bad key", 1), "key"),
    (lambda d: d["strokes"][0].__setitem__("note", "x" * 600) or d["strokes"][0].__setitem__("note2", "y" * 600), "bytes"),
    (lambda d: d["strokes"][0].__setitem__("note", float("nan")), "finite"),
    (lambda d: d["strokes"][0].__setitem__("pts", [10 ** 400, 100, 500, 0, 1000]), "finite"),   # a client's Infinity
    (lambda d: d["strokes"][0].__setitem__("pts", [100, -(10 ** 400), 500, 0, 1000]), "finite"),
    (lambda d: d["strokes"][0].__setitem__("note", 10 ** 400), "finite"),     # an extra key's too, at every level
    (lambda d: d["strokes"][0].__setitem__("note", {"n": [1, -(10 ** 400)]}), "finite"),
    (lambda d: d["space"].__setitem__("dpi", 10 ** 400), "finite"),
    (lambda d: d.__setitem__("meta", 10 ** 400), "finite"),
    (lambda d: d.__setitem__("meta", {f"k{i}": "x" * 1000 for i in range(20)}), "bytes"),
])
def test_schema_rejects_bad_files(mutate, message):
    data = _ink()
    mutate(data)
    with pytest.raises(inkmod.InkError) as e:
        inkmod.parse_ink(data)
    assert message.lower() in str(e.value).lower()


def test_a_slanted_pencil_fits_the_w_and_o_ranges():
    """The pencil's widest, 3.5 times its size, is inside the w channel's
    0..4.000; the drawn ceiling is still size × max(w) ≤ 100, and o is 0..1."""
    stroke = {"id": "pc", "brush": "pencil", "size": 28.5, "ch": "xyptwo", "pts": [100, 100, 1000, 0, 3500, 1000]}
    assert inkmod.parse_ink(_ink(strokes=[stroke])).strokes[0].brush == "pencil"
    for change, message in (({"size": 29}, "max(w)"), ({"pts": [100, 100, 1000, 0, 3500, 1001]}, "channel o")):
        with pytest.raises(inkmod.InkError) as e:
            inkmod.parse_ink(_ink(strokes=[{**stroke, **change}]))
        assert message in str(e.value)


def test_a_coordinate_stays_where_every_writer_can_place_it():
    """x and y, their running sums, stay within ±MAX_XY hundredths of a
    point (a hundred times the largest page). A PDF writer cannot place a
    number past 28 digits, so a file or a foreign /InkList past the bound
    is refused, rather than taken and then failing every annotated-PDF
    export of its page."""
    from gamma.pdf_export import annotate_pdf, page_with_ink

    far = [inkmod.MAX_XY, -inkmod.MAX_XY, -2 * inkmod.MAX_XY, 2 * inkmod.MAX_XY]
    edge = inkmod.parse_ink(_ink(strokes=[{"id": "far", "ch": "xy", "pts": far}]))
    assert annotate_pdf(_blank_pdf(), [], ink=[{"ink": edge, "note": "", "id": "blk"}])[1] == 1
    assert page_with_ink(_blank_pdf(), 1, [edge])[1] == 1
    for pts, channel in (([inkmod.MAX_XY + 1, 0], "x"), ([0, 0, 1, inkmod.MAX_XY, 0, 1], "y"), ([10 ** 32, 0], "x")):
        with pytest.raises(inkmod.InkError, match=f"channel {channel}"):
            inkmod.parse_ink(_ink(strokes=[{"id": "far", "ch": "xy", "pts": pts}]))
    for x in (1e30, -1e30, 1e306):
        with pytest.raises(inkmod.InkError):
            inkmod.from_pdf_ink([[10, 10, x, 40]], 1, "#000000", 1, 1, PAGE_W, PAGE_H)
    assert inkmod.parse_ink(inkmod.from_pdf_ink([[10, 10, 1e7, 40]], 1, "#000000", 1, 1, PAGE_W, PAGE_H)).strokes


def test_nan_is_refused_at_parse_and_never_written():
    body = b'{"format": "gamma-ink", "version": 2, "space": {"kind": "canvas", "width": 1, "height": 1}, "strokes": [], "x": NaN}'
    with pytest.raises(inkmod.InkError):
        inkmod.parse_ink(body)
    with pytest.raises(ValueError):
        inkmod.dumps({"x": float("inf")})


def test_schema_keeps_the_open_keys_and_unknown_ones_verbatim():
    data = _ink(strokes=[{
        "id": "a", "tool": "fountain", "brush": "nib-ish", "color": "#2563eb", "size": 3, "band": 0, "from": "root1",
        "shape": {"kind": "rect", "params": {"x": 1, "y": 2.5, "w": 10, "h": 5, "closed": True}},
        "fill": {"color": "#ffe066", "opacity": 0.5}, "dash": "dotted", "nib": 45, "ch": "xyw",
        "pts": [100, 100, 1200, 10, 10, -400], "mood": {"tags": ["a", "b"], "n": 3}, "gone": None,
    }], dpi=72)
    data.update(author="me", nothing=None)
    ink = inkmod.parse_ink(data)
    s = ink.strokes[0]
    assert (s.tool, s.brush, s.band, s.from_, s.dash, s.nib) == ("fountain", "nib-ish", 0, "root1", "dotted", 45)
    assert s.shape.kind == "rect" and s.shape.params["closed"] is True and s.fill.opacity == 0.5
    assert s.model_extra == {"mood": {"tags": ["a", "b"], "n": 3}}        # the null key is dropped
    assert ink.model_extra == {"author": "me"} and ink.space.model_extra == {"dpi": 72}
    d = json.loads(inkmod.dumps(ink))
    assert d["strokes"][0]["from"] == "root1" and d["strokes"][0]["mood"]["n"] == 3 and "gone" not in d["strokes"][0]
    assert d["author"] == "me" and d["space"]["dpi"] == 72 and "nothing" not in d
    # canonical bytes: sorted keys, no whitespace, whole floats as integers, defaults never invented
    assert inkmod.dumps({"b": 1.0, "a": [2.0, 2.5], "c": None}) == b'{"a":[2,2.5],"b":1}'
    bare = inkmod.parse_ink(_ink(strokes=[{"id": "x", "pts": [1, 2]}]))
    assert inkmod.dumps(bare).endswith(b'"strokes":[{"id":"x","pts":[1,2]}],"version":2}')
    # an unknown tool draws as a round-capped polyline in its own bucket, under its band
    assert inkmod.band_of(s) == 0 and inkmod.band_of(bare.strokes[0]) == 1
    assert "<path" in inkmod.to_svg(ink)


def test_version_1_is_read_by_the_converter_alone():
    old = {"format": "gamma-ink", "version": 1, "space": {"kind": "pdf-page", "page": 1, "width": 1, "height": 1},
           "strokes": [{"id": "a", "ch": "xyp", "pts": [10, 10, 1000]}]}
    with pytest.raises(inkmod.InkVersionError):
        inkmod.parse_ink(old)
    with pytest.raises(inkmod.InkVersionError):
        inkmod.parse_ink({k: v for k, v in old.items() if k != "version"})   # unversioned counts as 1
    ink = inkmod.parse_ink(inkmod.upgrade_v1(old))
    assert ink.strokes[0].ch == "xypw" and ink.strokes[0].pts == [10, 10, 1000, 1500]
    assert inkmod.decode_stroke(ink.strokes[0])[0]["w"] == 1.5
    # anything but version 1 is handed back untouched: a newer file is never
    # rewritten down to 2, a malformed version is left for parse_ink to refuse
    newer = {**old, "version": 3}
    assert inkmod.upgrade_v1(newer) is newer and newer["strokes"][0]["ch"] == "xyp"
    odd = {**old, "version": "1"}
    assert inkmod.upgrade_v1(odd) is odd
    with pytest.raises(inkmod.InkError):
        inkmod.parse_ink(odd)


def test_budgets(monkeypatch):
    monkeypatch.setattr(inkmod, "MAX_SAMPLES", 8)
    with pytest.raises(inkmod.InkError):
        inkmod.parse_ink(_ink(strokes=[{"id": "a", "ch": "xy", "pts": [0] * 10}, {"id": "b", "ch": "xy", "pts": [0] * 10}]))
    monkeypatch.setattr(inkmod, "MAX_BYTES", 10)
    with pytest.raises(inkmod.InkError):
        inkmod.parse_ink(json.dumps(_ink()).encode())
    assert inkmod.MAX_BYTES == 10 and inkmod.__dict__["MAX_BYTES"] == 10
    monkeypatch.undo()
    assert inkmod.MAX_BYTES == 6 * 1024 * 1024


# --- renderers -------------------------------------------------------------------

def test_svg_and_pdf_ops_render_every_stroke():
    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "a", "ch": "xypw", "pts": inkmod.encode_points(_samples(), "xypw")},
        {"id": "b", "tool": "highlighter", "color": "rgba(255, 226, 143, 0.65)", "size": 8, "opacity": 0.5,
         "ch": "xy", "pts": inkmod.encode_points(_samples(3, 300, 300), "xy")},
    ]))
    svg = inkmod.to_svg(ink)
    assert svg.startswith("<svg") and svg.count("<path") == 5   # 4 pen segments + 1 highlighter path
    assert "multiply" in svg and 'stroke="rgb(255,226,143)"' in svg
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    # 4 pen segments each stroked alone + the highlighter's one polyline
    assert ops.count(b" l S") == 5 and ops.count(b" m ") == 5 and ops.startswith(b"q 1 J 1 j")


def test_a_colour_s_alpha_is_drawn_as_the_screen_draws_it():
    """The screen fills an rgba() colour under fill-opacity, so the two
    multiply, but paints a pencil with its grain tile, which takes the
    colour's r, g and b alone: every exporter draws the same opacity."""
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf

    line = inkmod.encode_points([{"x": 100, "y": 100}, {"x": 140, "y": 120}, {"x": 180, "y": 100}], "xy")
    pencil = {"id": "pc", "brush": "pencil", "pen": False, "color": "rgba(30,136,229,0.5)", "size": 9, "ch": "xy", "pts": line}
    nib = {"id": "nb", "brush": "calligraphy", "nib": 45, "pen": False, "color": "rgba(198,40,40,0.6)", "opacity": 0.5,
           "size": 6, "ch": "xyw", "pts": inkmod.encode_points([{"x": 100, "y": 200, "w": 1}, {"x": 160, "y": 230, "w": 1}], "xyw")}
    marker = {"id": "hl", "tool": "highlighter", "color": "rgba(255,226,143,0.65)", "size": 8, "opacity": 0.5, "ch": "xy", "pts": line}
    ink = inkmod.parse_ink(_ink(strokes=[pencil, nib, marker]))
    assert [inkmod.drawn_alpha(s, inkmod.parse_color(s.color)[3]) for s in ink.strokes] == pytest.approx([1, 0.3, 0.325])
    svg = inkmod.to_svg(ink)
    assert 'fill="url(#inkGrain-1e88e5)"/>' in svg, "a pencil at its opacity, the colour's alpha left out"
    assert 'fill="rgb(198,40,40)" fill-opacity="0.3"/>' in svg and 'stroke-opacity="0.33"' in svg
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    assert sorted(inkmod.pdf_path_resources(ops)) == ["/GSink300", "/GSinkM325"]
    out, _ = annotate_pdf(_blank_pdf(), [], ink=[{"ink": ink, "note": "", "id": "blk"}])
    alphas = sorted(float(a.get_object()["/CA"]) for a in PdfReader(io.BytesIO(out)).pages[0]["/Annots"])
    assert alphas == pytest.approx([0.3, 0.325, 1])


def test_band_0_is_drawn_first_in_every_renderer():
    """A highlighter drawn after the ink still goes under it; a pen moved
    to band 0 does too, and a highlighter raised to band 1 draws as ink."""
    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "pen", "color": "#1f1f1f", "ch": "xy", "pts": [10000, 10000, 1000, 0]},
        {"id": "hl", "tool": "highlighter", "color": "#ffe066", "size": 8, "ch": "xy", "pts": [10000, 10000, 1000, 0]},
        {"id": "under", "color": "#dc2626", "band": 0, "ch": "xy", "pts": [10000, 10000, 1000, 0]},
        {"id": "top", "tool": "highlighter", "color": "#2563eb", "band": 1, "size": 8, "ch": "xy",
         "pts": [10000, 10000, 1000, 0]},
    ]))
    assert [s.id for s in inkmod.in_draw_order(ink.strokes)] == ["hl", "under", "pen", "top"]
    svg = inkmod.to_svg(ink)
    order = sorted(("rgb(255,224,102)", "rgb(220,38,38)", "rgb(31,31,31)", "rgb(37,99,235)"), key=svg.index)
    assert order == ["rgb(255,224,102)", "rgb(220,38,38)", "rgb(31,31,31)", "rgb(37,99,235)"]
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, y)).decode()
    assert ops.index("1.00 0.88 0.40 RG") < ops.index("0.86 0.15 0.15 RG") < ops.index("0.12 0.12 0.12 RG") \
        < ops.index("0.15 0.39 0.92 RG")
    # one band alone, for a writer drawing several groups band by band
    under = inkmod.pdf_path_ops(ink, lambda x, y: (x, y), band=0).decode()
    assert "1.00 0.88 0.40 RG" in under and "0.86 0.15 0.15 RG" in under and "0.12 0.12 0.12 RG" not in under
    assert inkmod.pdf_path_ops(inkmod.parse_ink(_ink(strokes=[])), lambda x, y: (x, y)) == b""


def _pencil(n=21, size=2.0, opacity=1.0, falling=False):
    """A pencil line pressed harder and harder (or, ``falling``, lighter):
    o = 0.15 + 0.85p crosses both band edges."""
    ps = [i / (n - 1) for i in range(n)]
    samples = [{"x": 10 + 2 * i, "y": 20, "p": p, "t": 8 * i, "w": 1 + (p - 0.5), "o": 0.15 + 0.85 * p}
               for i, p in enumerate(ps[::-1] if falling else ps)]
    return {"id": "pc", "brush": "pencil", "size": size, "opacity": opacity, "ch": "xyptwo",
            "pts": inkmod.encode_points(samples, "xyptwo")}


def test_pencil_bands_split_where_o_crosses_a_band_edge():
    assert [inkmod.opacity_band(o) for o in (0.15, 0.33, 0.331, 0.66, 0.7, 1)] == [0.33, 0.33, 0.66, 0.66, 1, 1]
    stroke = inkmod.parse_ink(_ink(strokes=[_pencil()])).strokes[0]
    bands = inkmod.stroke_bands(stroke)
    assert [band for band, _ in bands] == [0.33, 0.66, 1.0]
    (_, light), (_, mid), (_, dark) = bands
    # each run ends where the next begins, lerped by arc to the edge's opacity
    assert light[0]["x"] == 10 and dark[-1]["x"] == 50
    assert light[-1] == mid[0] and mid[-1] == dark[0]
    assert light[-1]["o"] == pytest.approx(0.33) and mid[-1]["o"] == pytest.approx(0.66)
    assert 18 < light[-1]["x"] < 20 and light[-1]["w"] == pytest.approx(1 + (light[-1]["p"] - 0.5), abs=2e-3)
    # lighter and lighter crosses the edges the other way round
    falling = inkmod.parse_ink(_ink(strokes=[_pencil(falling=True)])).strokes[0]
    assert [band for band, _ in inkmod.stroke_bands(falling)] == [1.0, 0.66, 0.33]
    # a stroke without o is one band, the whole stroke
    plain = inkmod.parse_ink(_ink()).strokes[0]
    assert inkmod.stroke_bands(plain) == [(1.0, inkmod.decode_stroke(plain))]


def test_a_pencil_draws_three_shades_painted_once_each():
    ink = inkmod.parse_ink(_ink(strokes=[_pencil()]))
    svg = inkmod.to_svg(ink)
    # one filled outline per band, painted with the grain; the darkest band is opaque
    assert svg.count("<path") == 3 and svg.count('fill="url(#inkGrain-1f1f1f)"') == 3
    assert 'fill-opacity="0.33"' in svg and 'fill-opacity="0.66"' in svg and svg.count("fill-opacity") == 2
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    assert ops.count(b"\nf\n") == 3 and b"/GSink330 gs" in ops and b"/GSink660 gs" in ops
    assert ops.count(b"/Pattern cs /Grain1f1f1f_1_0_0_1_0_792 scn\n") == 3
    states = inkmod.pdf_path_resources(ops)
    assert sorted(states) == ["/GSink330", "/GSink660"]
    assert (float(states["/GSink330"]["/CA"]), float(states["/GSink330"]["/ca"])) == (0.33, 0.33)
    # the preset's opacity scales every band; a translucent pen is one band at it
    ops = inkmod.pdf_path_ops(inkmod.parse_ink(_ink(strokes=[_pencil(opacity=0.6)])), lambda x, y: (x, y))
    assert sorted(inkmod.pdf_path_resources(ops)) == ["/GSink198", "/GSink396", "/GSink600"]
    pale = inkmod.parse_ink(_ink(strokes=[{**_ink()["strokes"][0], "opacity": 0.5}]))
    assert inkmod.to_svg(pale).count('fill-opacity="0.5"') == 1 and "<pattern" not in inkmod.to_svg(pale)
    ops = inkmod.pdf_path_ops(pale, lambda x, y: (x, y))
    assert ops.count(b"/GSink500 gs") == 1 and ops.count(b"\nf\n") == 1 and b" l S" not in ops
    assert b"/Pattern" not in ops and inkmod.GrainPatterns(None).resources(ops) is None


def test_a_pencil_is_painted_with_its_grain_anchored_at_the_page_corner():
    """One <pattern> per pencil colour, the screen's tile GRAIN_UNITS page
    units square from the page's (0, 0) whatever the crop; every pencil
    path painted with it at the opacity it had, a mouse pencil's even
    outline and a dashed pencil's stroked centreline included; any other
    pen, and a highlighter, plain."""
    pencil = _pencil()
    mouse = {"id": "m", "brush": "pencil", "pen": False, "color": "#dc2626", "size": 3, "ch": "xyt",
             "pts": inkmod.encode_points([{"x": 300, "y": 300 + i, "t": 8 * i} for i in range(20)], "xyt")}
    dashed = {**mouse, "id": "d", "color": "#1F1F1F", "dash": "dashed", "opacity": 0.5}
    pen = {**mouse, "id": "pen", "brush": None, "color": "#2563eb"}
    marker = {**mouse, "id": "hl", "tool": "highlighter", "color": "#ffe066"}
    ink = inkmod.parse_ink(_ink(strokes=[pencil, mouse, dashed, pen, marker]))
    svg = inkmod.to_svg(ink)
    defs = svg[svg.index("<defs>"):svg.index("</defs>")]
    assert defs.count("<pattern") == 2 and defs.index("inkGrain-1f1f1f") < defs.index("inkGrain-dc2626")
    for rgb in ("1f1f1f", "dc2626"):
        tile = base64.b64encode(inkmod.grain_png("#" + rgb)).decode()
        assert (f'<pattern id="inkGrain-{rgb}" patternUnits="userSpaceOnUse" width="32" height="32">'
                f'<image href="data:image/png;base64,{tile}" width="32" height="32" preserveAspectRatio="none"/>'
                "</pattern>") in defs
    assert svg.count('fill="url(#inkGrain-1f1f1f)"') == 3, "the shaded pencil's three bands"
    assert svg.count('fill="url(#inkGrain-dc2626)"') == 1, "a mouse pencil is one outline, painted once"
    assert '<path d="M300 300 L300 301' in svg and 'stroke="url(#inkGrain-1f1f1f)" stroke-width="3" stroke-opacity="0.5"' in svg
    assert 'stroke="rgb(37,99,235)"' in svg and 'stroke="rgb(255,224,102)"' in svg and svg.count("url(#") == 5
    # a crop moves the view, never the grain: the patterns are the same
    cropped = inkmod.to_svg(ink, box=(250, 280, 320, 330), pad=0)
    assert cropped[cropped.index("<defs>"):cropped.index("</defs>")] == defs
    # the PDF: the pencils' fills and the dashed one's stroke paint with a
    # pattern named for its colour and matrix; the pen and the highlighter plain
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y)).decode()
    assert ops.count("/Pattern cs /Grain1f1f1f_1_0_0_1_0_792 scn") == 3
    assert ops.count("/Pattern cs /Graindc2626_1_0_0_1_0_792 scn") == 1
    assert "q /GSink500 gs\n/Pattern CS /Grain1f1f1f_1_0_0_1_0_792 SCN 3.00 w [6 6] 0 d" in ops
    assert "0.15 0.39 0.92 RG" in ops and "1.00 0.88 0.40 RG" in ops
    # the tile is smoothed when magnified, as on the screen (pdf.js draws it in blocks otherwise)
    from PyPDF2 import PdfReader
    from gamma.pdf_export import page_with_ink
    page = PdfReader(io.BytesIO(page_with_ink(_blank_pdf(), 1, [ink])[0])).pages[0]
    tiles = [p.get_object()["/Resources"]["/XObject"]["/Tile"] for p in page["/Resources"]["/Pattern"].values()]
    assert len(tiles) == 2 and all(t["/Interpolate"].value is True for t in tiles)


def test_grain_tiles_keep_nothing_per_colour():
    """The Markdown export's to_svg writes a tile per pencil colour, and the
    colours are a writer's data: nothing is kept per colour (only the
    palette differs; the rest of the PNG is made once). A first batch of
    other colours fills the interpreter's own caches first: Python 3.12
    keeps up to 2000 freed tuples of a size for reuse, 128 KB of the
    channels' 3-tuples, which tracemalloc counts as kept."""
    import tracemalloc
    assert inkmod.grain_png("#1F1F1F") == inkmod.grain_png("rgb(31, 31, 31)")
    for i in range(3000):
        inkmod.grain_png(f"rgb({i % 256},{i // 256},8)")
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for i in range(3000):
            inkmod.grain_png(f"rgb({i % 256},{i // 256},7)")
        kept = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert kept < 100_000, f"{kept} bytes kept for 3000 colours"


def test_the_grain_s_pattern_matrix_carries_the_strokes_mapping():
    """A pattern is placed in the page's default space, not under the
    current transformation: its matrix is the strokes' own mapping
    (``to_pdf``, then ``frame``) with y turned, so a cell's corner is the
    corner of a 32-unit cell of the drawing and its first row is on top."""
    ink = inkmod.parse_ink(_ink(strokes=[_pencil()]))

    def matrix(ops):
        name = re.search(rb"/(Grain\S+) scn", ops).group(1).decode()
        return [float(v) for v in name.split("_")[1:]]
    # a writer drawing in a y-down frame flipped by its content (notebook, notes PDF)
    assert matrix(inkmod.pdf_path_ops(ink, lambda x, y: (x, y), frame=(1, 0, 0, -1, 0, 800))) == [1, 0, 0, 1, 0, 800]
    # scaled and moved (the notes PDF fits a drawing to its column)
    scaled = inkmod.pdf_path_ops(ink, lambda x, y: (60 + (x - 10) * 0.5, 54 + (y - 20) * 0.5), 0.5, frame=(1, 0, 0, -1, 0, 841.89))
    a, b, c, d, e, f = matrix(scaled)
    assert (a, b, c, d) == (0.5, 0, 0, 0.5) and (e, f) == pytest.approx((60 - 5, 841.89 - 54 + 10), abs=1e-4)
    # a page shown turned a quarter: the cell turns with the page (pdf_export.page_frame's mapping)
    from gamma.pdf_export import viewer_point_to_pdf
    turned = inkmod.pdf_path_ops(ink, lambda x, y: viewer_point_to_pdf(x / 400, y / 300, 90, (0, 0, 300, 400)))
    assert matrix(turned) == [0, 1, -1, 0, 0, 0]
    # every display point is the pattern point the screen's tile has there: (x, -y)
    for px, py in ((0, 0), (33.25, 70.5), (100, 12.75)):
        X, Y = viewer_point_to_pdf(px / 400, py / 300, 90, (0, 0, 300, 400))
        u, v = Y, -X                                 # the inverse of [0 1 -1 0 0 0]
        assert (u, -v) == pytest.approx((px, py))


def test_dashed_strokes_draw_one_dashed_centreline():
    """Dash geometry comes from the size: a pressure pen's dashes run along
    its mean-width centreline, a highlighter's along its own."""
    pen = {**_ink()["strokes"][0], "dash": "dashed"}            # size 2, w 0.7 … 1.3
    marker = {"id": "hl", "tool": "highlighter", "color": "#ffe066", "size": 8, "opacity": 0.6, "dash": "dotted",
              "ch": "xy", "pts": inkmod.encode_points(_samples(3, 300, 300), "xy")}
    ink = inkmod.parse_ink(_ink(strokes=[pen, marker]))
    svg = inkmod.to_svg(ink)
    assert svg.count("<path") == 2
    assert 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="4 4"' in svg
    assert 'stroke-dasharray="0.8 16" style="mix-blend-mode:multiply"' in svg
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y)).decode()
    pen_ops = ops[ops.index("0.12 0.12 0.12 RG"):]
    assert pen_ops.startswith("0.12 0.12 0.12 RG 2.00 w [4 4] 0 d\n") and pen_ops.count(" l S") == 1
    assert pen_ops.index(" S\n") < pen_ops.index("[] 0 d")
    assert "/GSinkM600 gs 1.00 0.88 0.40 RG 8.00 w\n[0.8 16] 0 d" in ops
    # the notes PDF scales the pattern with the drawing
    assert b"[2 2] 0 d" in inkmod.pdf_path_ops(ink, lambda x, y: (x, y), scale=0.5)


def test_highlighters_multiply_in_the_pdf_ops():
    """With a graphics state the highlighter is translucent and multiplied
    like the screen, not a pale opaque band over whatever lies beneath."""
    ink = inkmod.parse_ink(_ink(strokes=[{"id": "hl", "tool": "highlighter", "color": "#ffe066", "size": 8,
                                          "opacity": 0.6, "ch": "xy", "pts": [10000, 10000, 1000, 0]}]))
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, y))
    assert b"q /GSinkM600 gs 1.00 0.88 0.40 RG 8.00 w" in ops and ops.rstrip().endswith(b"S\nQ\nQ")
    state = inkmod.pdf_path_resources(ops)["/GSinkM600"]
    assert (str(state["/BM"]), float(state["/CA"]), float(state["/ca"])) == ("/Multiply", 0.6, 0.6)
    assert inkmod.pdf_path_resources(b"q 1 J 1 j\nQ") is None


def test_stroke_outline_is_the_screen_outline_at_the_baked_width():
    """perfect-freehand's outline, driven by w: its half-width is size × w / 2."""
    def outline(samples, ch, size):
        stroke = inkmod.parse_ink(_ink(strokes=[{"id": "o", "size": size, "ch": ch,
                                                  "pts": inkmod.encode_points(samples, ch)}])).strokes[0]
        return inkmod.stroke_outline(stroke)

    # a single sample: perfect-freehand's dot, a capsule a hair long (it
    # adds a point one unit on, a tenth of a point at the scale it runs at)
    dot = outline([{"x": 50, "y": 60, "w": 1.5}], "xyw", 4)
    assert len(dot) > 10 and all(2.85 < ((x - 50) ** 2 + (y - 60) ** 2) ** 0.5 < 3.15 for x, y in dot)
    line = outline([{"x": 10 + i, "y": 40, "w": 1.2} for i in range(31)], "xyw", 2)
    mid = [abs(y - 40) for x, y in line if 15 < x < 35]
    assert mid and max(mid) == pytest.approx(1.2, abs=1e-6) and min(mid) == pytest.approx(1.2, abs=1e-6)
    assert min(x for x, _ in line) == pytest.approx(8.8, abs=0.01) and max(x for x, _ in line) == pytest.approx(41.2, abs=0.01)
    # two samples are given a midpoint, so the middle keeps the baked width
    # (perfect-freehand's own two-point path would draw the size there)
    pair = outline([{"x": 0, "y": 40, "w": 2}, {"x": 20, "y": 40, "w": 2}], "xyw", 2)
    assert max(abs(y - 40) for x, y in pair if 5 < x < 15) == pytest.approx(2, abs=1e-6)
    # without w a stroke is its size
    flat = outline([{"x": 10 + i, "y": 40} for i in range(31)], "xy", 3)
    assert max(abs(y - 40) for x, y in flat if 15 < x < 35) == pytest.approx(1.5, abs=1e-6)


# perfect-freehand 1.2.2's getStroke on a stroke that turns back on itself,
# at the client's outlineOptions (size 2): the outline the port must give.
_PF_REVERSAL_IN = [(10, 10, 0.5), (15, 10, 0.6), (20, 10, 0.7), (25, 10, 0.7), (30, 10, 0.6), (25, 10.5, 0.5),
                   (20, 10.5, 0.5), (15, 10.5, 0.4), (10, 10.5, 0.4)]
_PF_REVERSAL_OUT = [
    (10, 9), (13.3, 8.8), (17.722, 8.6), (22.525, 8.6), (27.459, 8.8), (27.746, 8.835), (28.016, 8.937),
    (28.254, 9.102), (28.446, 9.318), (28.581, 9.575), (28.65, 9.855), (28.65, 10.145), (28.581, 10.426),
    (28.446, 10.682), (28.254, 10.898), (28.016, 11.063), (27.746, 11.165), (27.459, 11.2), (26.033, 11.31),
    (22.013, 11.442), (17.381, 11.28), (10.002, 11.3), (9.747, 11.259), (9.518, 11.138), (9.339, 10.951),
    (9.23, 10.716), (9.201, 10.459), (9.256, 10.206), (9.389, 9.984), (9.586, 9.816), (9.826, 9.719),
    (10.084, 9.704), (10.334, 9.773), (10.549, 9.918), (10.706, 10.123), (10.789, 10.369), (10.79, 10.627),
    (10.708, 10.873), (10.552, 11.079), (10.338, 11.225), (10.088, 11.295), (9.83, 11.282), (9.589, 11.186),
    (9.392, 11.019), (9.258, 10.798), (9.201, 10.545), (9.229, 10.288), (9.337, 10.053), (9.514, 9.864),
    (9.743, 9.742), (9.998, 9.7), (9.998, 9.7), (17.368, 9.68), (21.955, 9.443), (25.639, 9.35), (27.459,
    8.8), (27.746, 8.835), (28.016, 8.937), (28.254, 9.102), (28.446, 9.318), (28.581, 9.574), (28.65,
    9.855), (28.65, 10.145), (28.581, 10.425), (28.446, 10.682), (28.254, 10.898), (28.016, 11.063),
    (27.746, 11.165), (27.459, 11.2), (22.525, 11.4), (17.722, 11.4), (13.3, 11.2), (10, 11), (9.761,
    10.971), (9.535, 10.885), (9.337, 10.748), (9.177, 10.568), (9.065, 10.355), (9.007, 10.12), (9.007,
    9.879), (9.065, 9.645), (9.177, 9.432), (9.337, 9.251), (9.535, 9.115), (9.761, 9.029), (10, 9)
]


# the same with flat caps (start.cap and end.cap false), as a band run's cut ends
_PF_REVERSAL_FLAT = [
    (10, 9), (13.3, 8.8), (17.722, 8.6), (22.525, 8.6), (27.459, 8.8), (27.746, 8.835), (28.016, 8.937),
    (28.254, 9.102), (28.446, 9.318), (28.581, 9.575), (28.65, 9.855), (28.65, 10.145), (28.581, 10.426),
    (28.446, 10.682), (28.254, 10.898), (28.016, 11.063), (27.746, 11.165), (27.459, 11.2), (26.033, 11.31),
    (22.013, 11.442), (17.381, 11.28), (10.002, 11.3), (10.002, 11.3), (10.002, 11.292), (9.998, 9.708),
    (9.998, 9.7), (9.998, 9.7), (17.368, 9.68), (21.955, 9.443), (25.639, 9.35), (27.459, 8.8), (27.746,
    8.835), (28.016, 8.937), (28.254, 9.102), (28.446, 9.318), (28.581, 9.574), (28.65, 9.855), (28.65,
    10.145), (28.581, 10.425), (28.446, 10.682), (28.254, 10.898), (28.016, 11.063), (27.746, 11.165),
    (27.459, 11.2), (22.525, 11.4), (17.722, 11.4), (13.3, 11.2), (10, 11), (10, 11), (10, 11.02), (10,
    8.98), (10, 9)
]


@pytest.mark.parametrize("caps, want", [((True, True), _PF_REVERSAL_OUT), ((False, False), _PF_REVERSAL_FLAT)])
def test_the_outline_port_is_perfect_freehand(caps, want):
    got = inkmod._pf_outline(inkmod._pf_points(_PF_REVERSAL_IN, 2), 2, caps)
    assert len(got) == len(want)
    assert all(abs(x - u) < 6e-4 and abs(y - v) < 6e-4 for (x, y), (u, v) in zip(got, want))


def test_bounding_box_reads_the_raw_arrays_as_the_polyline_does():
    import random
    rnd = random.Random(5)
    strokes = []
    for i in range(30):
        ch = rnd.choice(["xy", "xyptw", "xyptazwo", "xypt"])
        samples = [{"x": rnd.uniform(0, 600), "y": rnd.uniform(0, 780), "p": rnd.random(), "t": 4 * j,
                    "a": rnd.randint(20, 90), "z": rnd.randint(0, 359), "w": rnd.uniform(0.2, 3.4),
                    "o": rnd.uniform(0.15, 1)} for j in range(rnd.randint(1, 40))]
        strokes.append({"id": f"s{i}", "size": rnd.uniform(0.5, 25), "ch": ch, "pts": inkmod.encode_points(samples, ch)})
    ink = inkmod.parse_ink(_ink(strokes=strokes))
    polys = [inkmod.stroke_polyline(s) for s in ink.strokes]
    want = (min(x - w / 2 for p in polys for x, _, w in p), min(y - w / 2 for p in polys for _, y, w in p),
            max(x + w / 2 for p in polys for x, _, w in p), max(y + w / 2 for p in polys for _, y, w in p))
    assert inkmod.bounding_box(ink) == pytest.approx(want, abs=1e-9)


# --- upload endpoint ----------------------------------------------------------------

def _bytes(data):
    """The upload bytes the clients send (ink.js serializeInk: sorted keys, no whitespace)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def test_upload_ink_stores_the_bytes_as_sent_and_serves(guest):
    from gamma.storage import content_digest
    body = _bytes(_ink())
    r = guest.post("/api/upload-ink", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    out = r.json()
    # named by the hash of what was sent, so a client can name it offline
    assert out["url"] == f"/api/uploads/{content_digest(body)}.ink"
    assert out["strokes"] == 1 and out["already_existed"] is False and out["size"] == len(body)
    assert out["pdf_position"]["pageNumber"] == 1
    again = guest.post("/api/upload-ink", content=body, headers={"Content-Type": "application/json"}).json()
    assert again["url"] == out["url"] and again["already_existed"] is True
    served = guest.get(out["url"])
    assert served.status_code == 200 and served.content == body
    assert served.headers["content-type"].startswith("application/json")
    assert inkmod.parse_ink(served.content).strokes[0].id == "s1"


def test_generic_upload_stores_only_valid_ink(guest):
    """The route a mirror pushes files by: an .ink must be a version 2 drawing."""
    from gamma.storage import content_digest
    good = _bytes(_ink())
    r = guest.post("/api/upload-file", files={"file": ("x.ink", good, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == f"/api/uploads/{content_digest(good)}.ink"
    bad = guest.post("/api/upload-file", files={"file": ("x.ink", b'{"format": "gamma-ink"}', "application/octet-stream")})
    assert bad.status_code == 400 and bad.json()["detail"] == "update Gamma"   # no version: a version 1 file
    bad = guest.post("/api/upload-file", files={"file": ("x.ink", b'{"format": "gamma-ink", "version": 2}', "application/octet-stream")})
    assert bad.status_code == 400 and "invalid ink" in bad.json()["detail"]


def test_upload_routes_refuse_version_1_with_update_gamma(guest):
    old = _bytes({**_ink(), "version": 1})
    r = guest.post("/api/upload-ink", content=old, headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["detail"] == "update Gamma"
    r = guest.post("/api/upload-file", files={"file": ("x.ink", old, "application/octet-stream")})
    assert r.status_code == 400 and r.json()["detail"] == "update Gamma"
    # the same strokes as version 2 are taken
    assert guest.post("/api/upload-ink", content=_bytes(_ink()), headers={"Content-Type": "application/json"}).status_code == 200


def test_upload_ink_rejects_bad_files(guest):
    r = guest.post("/api/upload-ink", json={"format": "gamma-ink", "version": 2, "space": {"width": 1, "height": 1}})
    assert r.status_code == 400 and "page" in r.json()["detail"]
    r = guest.post("/api/upload-ink", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = guest.post("/api/upload-ink", content=b'{"format": "gamma-ink", "version": 2, "space": {"kind": "canvas", "width": 1, "height": 1}, "strokes": [], "x": Infinity}',
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and "finite" in r.json()["detail"]
    # an integer past the largest double in an extra key is a client's Infinity too: refused, not a 500
    head = b'{"format": "gamma-ink", "version": 2, "space": {"kind": "canvas", "width": 1, "height": 1}, "strokes": [], "x": '
    r = guest.post("/api/upload-ink", content=head + b"1" + b"0" * 400 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and "finite" in r.json()["detail"]
    # and what the parser itself refuses: an integer past Python's 4300 digits, nesting deeper than its stack
    for value in (b"1" + b"0" * 5000, b"[" * 200_000 + b"]" * 200_000):
        r = guest.post("/api/upload-ink", content=head + value + b"}", headers={"Content-Type": "application/json"})
        assert r.status_code == 400 and r.json()["detail"].startswith("invalid ink file")


def test_orphan_bookkeeping_follows_ink_url(guest, monkeypatch):
    # a dropped ink block's file is recorded as unreferenced and kept, so an
    # ink undo still finds it
    from contextlib import closing
    from gamma import upload_gc
    from gamma.db import connect_pages_db, ws_uploads_dir
    monkeypatch.setattr(upload_gc, "UPLOAD_GRACE_S", 0)
    ws = workspace_of(guest_name())
    url = guest.post("/api/upload-ink", json=_ink(strokes=[{"id": "keep", "ch": "xy", "pts": [100, 100]}])).json()["url"]
    name = url.rsplit("/", 1)[1]
    page = make_page(guest, "Ink page")
    r = guest.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "inkblk1", "parent": page["id"], "content": "caption",
         "props": {"ink_url": url}},
    ]})
    assert r.status_code == 200, r.text
    # a full reconciliation sees the ink_url reference: the file is in use
    assert name not in upload_gc.reconcile(ws)["recorded"]
    # dropping the block leaves the file where it was, recorded as unreferenced
    r = guest.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [{"op": "delete", "id": "inkblk1"}]})
    assert r.status_code == 200, r.text
    upload_gc.flush(ws)
    uploads = ws_uploads_dir(ws)
    assert (uploads / name).is_file()
    with closing(connect_pages_db(ws)) as conn:
        assert conn.execute("SELECT 1 FROM upload_orphans WHERE name = ?", (name,)).fetchone()
    # the undo brings the block back, and the file is in use again
    r = guest.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "inkblk1", "parent": page["id"], "content": "caption",
         "props": {"ink_url": url}},
    ]})
    assert r.status_code == 200, r.text
    with closing(connect_pages_db(ws)) as conn:
        assert not conn.execute("SELECT 1 FROM upload_orphans WHERE name = ?", (name,)).fetchone()
    assert guest.get(url).status_code == 200


def test_read_upload_caches_the_parsed_file_by_path_and_stat(guest, tmp_path):
    body = _bytes(_ink())
    (tmp_path / ("a" * 24 + ".ink")).write_bytes(body)
    url = f"/api/uploads/{'a' * 24}.ink"
    first = inkmod.read_upload(tmp_path, url)
    assert first is not None and inkmod.read_upload(tmp_path, url) is first
    assert inkmod.read_upload(tmp_path, "/api/uploads/" + "b" * 24 + ".ink") is None
    assert inkmod.read_upload(tmp_path, "/api/uploads/x.gamma-ink") is None
    # a stored file no client could read (an extra key past the largest double) is unreadable, not an error
    (tmp_path / ("c" * 24 + ".ink")).write_bytes(_bytes({**_ink(), "x": 10 ** 400}))
    assert inkmod.read_upload(tmp_path, f"/api/uploads/{'c' * 24}.ink") is None
    # nor one the parser refuses: an integer past Python's 4300 digits
    (tmp_path / ("d" * 24 + ".ink")).write_bytes(_bytes(_ink())[:-1] + b',"x":1' + b"0" * 5000 + b"}")
    assert inkmod.read_upload(tmp_path, f"/api/uploads/{'d' * 24}.ink") is None


# --- two writers, one group ------------------------------------------------------------

def _stroke(sid, x=100.0, color="#1f1f1f", **more):
    return {"id": sid, "tool": "pen", "color": color, "size": 2, "opacity": 1, "pen": True, "ch": "xy",
            "pts": inkmod.encode_points([{"x": x, "y": 200.0}, {"x": x + 20, "y": 210.0}], "xy"), **more}


def _upload(client, strokes):
    r = client.post("/api/upload-ink", content=_bytes(_ink(strokes=strokes)), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _group(client, strokes, bid):
    """A page with one ink group block holding ``strokes``: (page id, the file's url)."""
    page = make_page(client, f"Merge {bid}")
    url = _upload(client, strokes)
    r = client.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": bid, "parent": page["id"], "content": "",
         "props": {"ink_url": url, "ink_strokes": len(strokes)}}]})
    assert r.status_code == 200, r.text
    return page["id"], url


def _stored(client, bid):
    block = client.get(f"/api/blocks/{bid}").json()
    return block["properties"], inkmod.parse_ink(client.get(block["properties"]["ink_url"]).content)


def test_two_writers_drawing_in_one_group_both_keep_their_strokes(guest):
    """A set whose base_props ink_url is not the stored one is merged by
    stroke (ops.py merge_ink), not replaced: the applied op names the merged
    file with its count and box, and the batch result reports the merge."""
    page, u0 = _group(guest, [_stroke("a")], "mg1")
    theirs = _upload(guest, [_stroke("a"), _stroke("x", 300)])
    ours = _upload(guest, [_stroke("a", color="#dc2626"), _stroke("b", 400)])
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "one", "ops": [
        {"op": "set", "id": "mg1", "props": {"ink_url": theirs, "ink_strokes": 2}, "base_props": {"ink_url": u0}}]})
    assert r.json()["ops"][0]["props"]["ink_url"] == theirs  # its base was current: as sent
    assert "ink_conflicts" not in r.json()
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "two", "ops": [
        {"op": "set", "id": "mg1", "props": {"ink_url": ours, "ink_strokes": 2, "pdf_position": None},
         "base_props": {"ink_url": u0}}]})
    assert r.status_code == 200, r.text
    applied = r.json()["ops"][0]["props"]
    assert applied["ink_url"] not in (u0, ours, theirs) and applied["ink_strokes"] == 3
    assert applied["pdf_position"]["pageNumber"] == 1 and "pdf_page" not in applied
    assert r.json()["ink_conflicts"] == {"mg1": {"clean": True, "lost_ours": [], "returned": []}}   # by block
    assert "ink_conflicts" not in r.json()["ops"][0]   # never in the echo (the op log, the fan-out)
    props, ink = _stored(guest, "mg1")
    assert props["ink_url"] == applied["ink_url"]
    assert [(s.id, s.color) for s in ink.strokes] == [("a", "#dc2626"), ("x", "#1f1f1f"), ("b", "#1f1f1f")]
    # the same change sent again merges to the same drawing (a mirror's resend)
    again = guest.post(f"/api/pages/{page}/ops", json={"client": "two", "ops": [
        {"op": "set", "id": "mg1", "props": {"ink_url": ours, "ink_strokes": 2}, "base_props": {"ink_url": u0}}]})
    assert again.json()["ops"][0]["props"]["ink_url"] == applied["ink_url"]


def test_ink_without_a_base_or_a_readable_file_is_last_writer_wins(guest):
    page, u0 = _group(guest, [_stroke("a")], "mg2")
    theirs = _upload(guest, [_stroke("a"), _stroke("x", 300)])
    guest.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [{"op": "set", "id": "mg2", "props": {"ink_url": theirs}}]})
    ours = _upload(guest, [_stroke("b", 400)])
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [{"op": "set", "id": "mg2", "props": {"ink_url": ours}}]})
    assert r.json()["ops"][0]["props"]["ink_url"] == ours  # no base_props: replaced, as before
    missing = "/api/uploads/" + "0" * 24 + ".ink"
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": "mg2", "props": {"ink_url": missing}, "base_props": {"ink_url": u0}}]})
    assert r.json()["ops"][0]["props"]["ink_url"] == missing  # nothing to read: as sent


def test_block_update_merges_ink_and_answers_what_it_stored(guest):
    """PUT /blocks/{id}, the browser's ink flush: base_properties, the
    applied properties in the answer, and ink_conflict naming what the
    merge dropped or brought back."""
    page, u0 = _group(guest, [_stroke("a")], "mg3")
    theirs = _upload(guest, [_stroke("a", color="#dc2626"), _stroke("x", 300)])
    guest.put("/api/blocks/mg3", json={"properties": {"ink_url": theirs}, "base_properties": {"ink_url": u0}})
    ours = _upload(guest, [_stroke("b", 400)])  # a erased here, b drawn
    r = guest.put("/api/blocks/mg3", json={"properties": {"ink_url": ours, "ink_strokes": 1},
                                            "base_properties": {"ink_url": u0}})
    assert r.status_code == 200, r.text
    stored = r.json()["properties"]
    props, ink = _stored(guest, "mg3")
    assert stored["ink_url"] == props["ink_url"] and stored["ink_strokes"] == 3
    # a was recoloured there: the edit beats our erasure, and the answer
    # says so; b, drawn with nothing before it, goes in front
    assert [s.id for s in ink.strokes] == ["b", "a", "x"]
    assert r.json()["ink_conflict"] == {"clean": False, "lost_ours": ["a"], "returned": []}
    # the geometry / look split: our move of x composes with a recolour there
    moved = _upload(guest, [_stroke("b", 400), _stroke("a", color="#dc2626"), _stroke("x", 350)])
    recol = _upload(guest, [_stroke("b", 400), _stroke("a", color="#dc2626"), _stroke("x", 300, color="#2563eb")])
    base = props["ink_url"]
    guest.put("/api/blocks/mg3", json={"properties": {"ink_url": recol}, "base_properties": {"ink_url": base}})
    r = guest.put("/api/blocks/mg3", json={"properties": {"ink_url": moved}, "base_properties": {"ink_url": base}})
    assert r.json()["ink_conflict"]["clean"] is True
    _, ink = _stored(guest, "mg3")
    x = next(s for s in ink.strokes if s.id == "x")
    assert x.color == "#2563eb" and x.pts[0] == 35000


def test_block_update_with_base_ids_merges_a_purged_base_by_deletions(guest):
    """When the base file is gone, the stroke ids the client loaded it with
    let deletions win on both sides instead of a blind union."""
    page, u0 = _group(guest, [_stroke("a"), _stroke("b", 200)], "mg4")
    theirs = _upload(guest, [_stroke("b", 200), _stroke("x", 300)])    # a erased there
    guest.put("/api/blocks/mg4", json={"properties": {"ink_url": theirs}, "base_properties": {"ink_url": u0}})
    ours = _upload(guest, [_stroke("a", 150), _stroke("c", 400)])     # a moved here, b erased here
    gone = "/api/uploads/" + "0" * 24 + ".ink"
    r = guest.put("/api/blocks/mg4", json={"properties": {"ink_url": ours, "ink_strokes": 2},
                                            "base_properties": {"ink_url": gone}, "base_ids": ["a", "b"]})
    assert r.status_code == 200, r.text
    _, ink = _stored(guest, "mg4")
    assert [s.id for s in ink.strokes] == ["x", "c"]
    assert r.json()["ink_conflict"] == {"clean": False, "lost_ours": ["a"], "returned": []}
    # without base_ids a gone base is the union by id
    r = guest.put("/api/blocks/mg4", json={"properties": {"ink_url": ours}, "base_properties": {"ink_url": gone}})
    _, ink = _stored(guest, "mg4")
    assert sorted(s.id for s in ink.strokes) == ["a", "c", "x"]


# --- PDF interchange ------------------------------------------------------------------

def test_annotated_pdf_writes_ink_and_importer_reads_it_back():
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf
    from gamma.routers.imports import _extract_pdf_annotations

    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "a", "ch": "xyptw", "pts": inkmod.encode_points(_samples(), "xyptw"), "t0": 5, "mood": "calm"},
        {"id": "b", "color": "#ff0000", "ch": "xy", "pts": inkmod.encode_points(_samples(3, 300, 300), "xy")},
    ]))
    out, written = annotate_pdf(_blank_pdf(), [], author="tester", ink=[{"ink": ink, "note": "my ink", "id": "blk"}])
    assert written == 2   # two looks → two /Ink annotations
    reader = PdfReader(io.BytesIO(out))
    annots = [a.get_object() for a in reader.pages[0]["/Annots"]]
    assert [str(a["/Subtype"]) for a in annots] == ["/Ink", "/Ink"]
    first = annots[0]
    assert str(first["/Contents"]) == "my ink" and "/GammaInk" in first and str(first["/NM"]).startswith("Zotero-")
    # /InkList is in PDF user space: y flipped, first sample (100, 200) → (100, 592)
    path = [float(v) for v in first["/InkList"][0]]
    assert path[:2] == pytest.approx([100.0, PAGE_H - 200.0])
    # the mean drawn width comes from the baked w: 1.6 × mean(0.7 … 1.3) = 1.6
    assert float(first["/BS"]["/W"]) == pytest.approx(1.6, abs=0.01)
    # the private key is the version 2 file, every key verbatim
    private = json.loads(str(first["/GammaInk"]))
    assert private["version"] == 2 and private["strokes"][0]["mood"] == "calm" and private["strokes"][0]["ch"] == "xyptw"

    found = _extract_pdf_annotations(reader)
    assert len(found) == 2 and all(f["kind"] == "ink" for f in found)
    back = found[0]["ink"]
    assert back.strokes[0].ch == "xyptw" and back.strokes[0].t0 == 5   # the private key kept pressure + time
    assert back.strokes[0].model_extra == {"mood": "calm"}
    assert found[0]["content"] == "my ink" and found[0]["position"]["pageNumber"] == 1


def test_annots_come_band_0_first_and_keep_their_bucket_index():
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf, zotero_annot_key

    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "a", "ch": "xy", "pts": [10000, 10000, 1000, 0]},
        {"id": "h", "tool": "highlighter", "color": "#ffe066", "size": 8, "opacity": 0.6, "ch": "xy",
         "pts": [10000, 10000, 1000, 0]},
    ]))
    out, written = annotate_pdf(_blank_pdf(), [], ink=[{"ink": ink, "note": "note", "id": "blk"}])
    assert written == 2
    annots = [a.get_object() for a in PdfReader(io.BytesIO(out)).pages[0]["/Annots"]]
    tools = [json.loads(str(a["/GammaInk"]))["strokes"][0].get("tool", "pen") for a in annots]
    assert tools == ["highlighter", "pen"]                      # band 0 drawn first
    assert str(annots[1]["/Contents"]) == "note"                # the note on the first-seen bucket
    assert str(annots[1]["/NM"]) == f"Zotero-{zotero_annot_key('blk:0')}"
    assert str(annots[0]["/NM"]) == f"Zotero-{zotero_annot_key('blk:1')}"
    # the order holds across groups: a later group's highlighter goes under
    # an earlier group's pen, as InkLayer draws every band 0 first
    later = inkmod.parse_ink(_ink(strokes=[
        {"id": "h2", "tool": "highlighter", "color": "#ffe066", "size": 8, "opacity": 0.6, "ch": "xy",
         "pts": [20000, 20000, 1000, 0]},
        {"id": "b", "ch": "xy", "pts": [20000, 20000, 1000, 0]},
    ]))
    out, written = annotate_pdf(_blank_pdf(), [], ink=[{"ink": ink, "note": "", "id": "blk"},
                                                      {"ink": later, "note": "", "id": "blk2"}])
    assert written == 4
    annots = [a.get_object() for a in PdfReader(io.BytesIO(out)).pages[0]["/Annots"]]
    ids = [json.loads(str(a["/GammaInk"]))["strokes"][0]["id"] for a in annots]
    assert ids == ["h", "h2", "a", "b"]


def test_an_exported_highlighter_multiplies_into_the_page():
    """/Ink has no blend mode, so a highlighter's annotation carries an
    appearance that multiplies (#147): drawn by a viewer, black under it
    stays black and white paper takes the colour at its opacity, where
    the /InkList painted at /CA would wash the black out to the colour.
    A pen's annotation keeps no appearance."""
    import pymupdf
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf

    src = pymupdf.open()
    src.new_page(width=PAGE_W, height=PAGE_H).draw_rect(pymupdf.Rect(100, 90, 200, 110), color=None, fill=(0, 0, 0))
    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "h", "tool": "highlighter", "color": "#ffe28f", "size": 20, "opacity": 0.6, "ch": "xy",
         "pts": inkmod.encode_points([{"x": 50.0, "y": 100.0}, {"x": 250.0, "y": 100.0}], "xy")},
        {"id": "p", "ch": "xy", "pts": [10000, 10000, 1000, 0]},
    ]))
    out, written = annotate_pdf(src.tobytes(), [], ink=[{"ink": ink, "note": "", "id": "blk"}])
    assert written == 2
    hl, pen = [a.get_object() for a in PdfReader(io.BytesIO(out)).pages[0]["/Annots"]]
    assert "/AP" not in pen
    form = hl["/AP"]["/N"].get_object()
    assert [float(v) for v in form["/BBox"]] == [float(v) for v in hl["/Rect"]]
    states = form["/Resources"]["/ExtGState"]
    assert [str(states[name]["/BM"]) for name in states] == ["/Multiply"]
    pix = pymupdf.open(stream=out, filetype="pdf")[0].get_pixmap(annots=True)
    assert pix.pixel(150, 100) == (0, 0, 0)            # the black under it stays black
    paper = pix.pixel(70, 100)
    assert abs(paper[1] - 238) <= 2 and abs(paper[2] - 188) <= 2   # white × #ffe28f at 0.6


def _pdf_with_ink_annotation(gamma_ink: str | None = None, border=None):
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

    w = PdfWriter()
    w.add_blank_page(width=PAGE_W, height=PAGE_H)
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Ink"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (10, 10, 60, 60)),
        NameObject("/InkList"): ArrayObject([ArrayObject(FloatObject(v) for v in (10, 10, 30, 40, 60, 60))]),
        NameObject("/BS"): DictionaryObject({NameObject("/W"): FloatObject(3), **(border or {})}),
        NameObject("/C"): ArrayObject(FloatObject(v) for v in (0, 0, 1)),
    })
    if gamma_ink is not None:
        annot[NameObject("/GammaInk")] = TextStringObject(gamma_ink)
    w.add_annotation(page_number=0, annotation=annot)
    buf = io.BytesIO()
    w.write(buf)
    return PdfReader(io.BytesIO(buf.getvalue()))


def test_importer_reads_foreign_ink_without_private_key():
    from gamma.routers.imports import _extract_pdf_annotations

    found = _extract_pdf_annotations(_pdf_with_ink_annotation())
    assert len(found) == 1
    ink = found[0]["ink"]
    s = ink.strokes[0]
    assert s.size == 3 and s.color == "#0000ff" and s.pen is False
    pts = inkmod.decode_stroke(s)
    assert (pts[0]["x"], pts[0]["y"]) == (10, PAGE_H - 10) and pts[-1]["x"] == 60
    assert s.dash is None


def test_importer_reads_a_dashed_foreign_border_as_a_dashed_line():
    """A foreign /Ink whose border style is dashed imports dashed, whatever
    its /D array says: Gamma's pattern follows the width."""
    from PyPDF2.generic import ArrayObject, FloatObject, NameObject
    from gamma.routers.imports import _extract_pdf_annotations

    dashed = {NameObject("/S"): NameObject("/D"), NameObject("/D"): ArrayObject([FloatObject(3), FloatObject(2)])}
    for border in (dashed, {NameObject("/S"): NameObject("/D")}):
        (found,) = _extract_pdf_annotations(_pdf_with_ink_annotation(border=border))
        assert [s.dash for s in found["ink"].strokes] == ["dashed"]
    (found,) = _extract_pdf_annotations(_pdf_with_ink_annotation(border={NameObject("/S"): NameObject("/S")}))
    assert found["ink"].strokes[0].dash is None


def test_importer_takes_the_ink_list_when_gamma_ink_holds_a_number_no_client_reads():
    """A /GammaInk whose pts hold an integer past the largest double (a
    client's JSON.parse reads Infinity, and dumps writes no such number) is
    refused like any bad file: the import takes the /InkList, whose file
    can be written, instead of failing at dumps."""
    from gamma.routers.imports import _extract_pdf_annotations

    bad = json.dumps(_ink(strokes=[{"id": "huge", "ch": "xy", "pts": [10 ** 400, 100]}]))
    # one the parser refuses (past Python's 4300 digits, nested past its stack) falls back the same way
    unparsable = [bad.replace(str(10 ** 400), "1" + "0" * 5000), bad[:-1] + ', "x": ' + "[" * 200_000 + "]" * 200_000 + "}"]
    for payload in (bad, *unparsable):
        (found,) = _extract_pdf_annotations(_pdf_with_ink_annotation(gamma_ink=payload))
        assert [s.id for s in found["ink"].strokes] == ["i0"]
        assert inkmod.dumps(found["ink"]).startswith(b'{"format":"gamma-ink"')


def test_foreign_ink_past_the_largest_number_is_refused_as_any_bad_annotation():
    """A foreign /InkList coordinate whose hundredths overflow (a PDF number
    of hundreds of digits reads as one) is an InkError, so the importer
    skips the annotation as it skips any unreadable one; a version 1
    /GammaInk whose pressure is an integer past the largest double falls
    back to the /InkList, as any payload that does not parse."""
    from PyPDF2.generic import ArrayObject, FloatObject, NameObject
    from gamma.routers.imports import _extract_pdf_annotations

    for huge in (1e307, -1e307, float("inf"), float("nan")):
        with pytest.raises(inkmod.InkError):
            inkmod.from_pdf_ink([[10, 10, huge, 40]], 1, "#000000", 1, 1, PAGE_W, PAGE_H)
    reader = _pdf_with_ink_annotation()
    annot = reader.pages[0]["/Annots"][0].get_object()
    annot[NameObject("/InkList")] = ArrayObject([ArrayObject(FloatObject(v) for v in ("1" + "0" * 400 + ".5", 10, 30, 40))])
    assert float(annot["/InkList"][0][0]) == math.inf and _extract_pdf_annotations(reader) == []
    v1 = json.dumps({"format": "gamma-ink", "version": 1, "space": {"kind": "pdf-page", "page": 1, "width": PAGE_W, "height": PAGE_H},
                     "strokes": [{"id": "old", "ch": "xyp", "pts": [100, 100, 10 ** 400]}]})
    (found,) = _extract_pdf_annotations(_pdf_with_ink_annotation(gamma_ink=v1))
    assert [s.id for s in found["ink"].strokes] == ["i0"]
    assert inkmod.upgrade_v1(json.loads(v1))["strokes"][0]["pts"] == [100, 100, 10 ** 400, 1500]


def test_dashed_ink_writes_a_dashed_border_and_comes_back_as_drawn():
    """/BS /S /D /D [on off] from a bucket's first stroke; the bucket key
    has no dash, so a dashed and a solid stroke of one look share a bucket,
    and /GammaInk carries each stroke's own dash back."""
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf
    from gamma.routers.imports import _extract_pdf_annotations

    a, b = _stroke("a", dash="dashed"), _stroke("b", 200)
    c, d = _stroke("c", 300, color="#dc2626"), _stroke("d", 400, color="#dc2626", dash="dotted")
    ink = inkmod.parse_ink(_ink(strokes=[a, b, c, d]))
    assert [[s.id for s in bucket] for bucket in inkmod.ink_buckets(ink)] == [["a", "b"], ["c", "d"]]
    out, written = annotate_pdf(_blank_pdf(), [], ink=[{"ink": ink, "note": "", "id": "blk"}])
    assert written == 2
    reader = PdfReader(io.BytesIO(out))
    first, second = (x.get_object() for x in reader.pages[0]["/Annots"])
    assert str(first["/BS"]["/S"]) == "/D" and [float(v) for v in first["/BS"]["/D"]] == [4, 4]
    assert str(second["/BS"]["/S"]) == "/S" and "/D" not in second["/BS"]   # its first stroke is solid
    found = _extract_pdf_annotations(reader)
    assert [[(s.id, s.dash) for s in f["ink"].strokes] for f in found] == \
        [[("a", "dashed"), ("b", None)], [("c", None), ("d", "dotted")]]


def test_importer_upgrades_a_version_1_private_payload():
    """A PDF exported before release T carries version 1 strokes: the
    importer converts them (w baked from p) rather than falling back to
    the polyline."""
    from gamma.routers.imports import _extract_pdf_annotations

    old = json.dumps({"format": "gamma-ink", "version": 1,
                      "space": {"kind": "pdf-page", "page": 1, "width": PAGE_W, "height": PAGE_H},
                      "strokes": [{"id": "old", "tool": "pen", "color": "#1f1f1f", "size": 2, "opacity": 1,
                                   "pen": True, "ch": "xyp", "pts": [1000, 1000, 1000, 500, 0, 0]}]})
    found = _extract_pdf_annotations(_pdf_with_ink_annotation(old))
    s = found[0]["ink"].strokes[0]
    assert s.id == "old" and s.ch == "xypw" and s.pts == [1000, 1000, 1000, 1500, 500, 0, 0, -1000]
    assert [w for _, _, w in inkmod.stroke_polyline(s)] == pytest.approx([3.0, 1.0])


def test_import_endpoint_creates_ink_blocks(guest):
    from gamma.pdf_export import annotate_pdf
    ink = inkmod.parse_ink(_ink())
    pdf, _ = annotate_pdf(_blank_pdf(), [], ink=[{"ink": ink, "note": "note", "id": "b"}])
    r = guest.post("/api/uploads", files={"file": ("ink.pdf", io.BytesIO(pdf), "application/pdf")})
    assert r.status_code == 200, r.text
    doc_id = r.json()["doc_id"]
    page = guest.post(f"/api/blocks/by-doc/{doc_id}", json={"default_title": "Ink import"}).json()
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": True})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1 and r.json()["stripped"] == 1
    children = guest.get(f"/api/blocks/{page['id']}/children").json()["children"]
    props = children[0]["properties"]
    assert props["ink_url"].endswith(".ink") and props["ink_strokes"] == 1 and "pdf_page" not in props
    stored = inkmod.parse_ink(guest.get(props["ink_url"]).content)
    assert props["pdf_position"] == inkmod.pdf_position(stored) and stored.version == 2
    assert props["annot_stripped"] is True and children[0]["content"] == "note"
    assert guest.get(props["ink_url"]).status_code == 200
    # idempotent
    assert guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id}).json()["imported"] == 0


# --- other exports --------------------------------------------------------------------

def _page_with_ink(guest, title="Ink export"):
    up = guest.post("/api/upload-ink", json=_ink(page=2)).json()
    url = up["url"]
    page = make_page(guest, title)
    r = guest.put(f"/api/blocks/{page['id']}/children", json={"blocks": [
        {"id": f"ink-{page['id']}", "content": "a derivation", "children": [], "properties": {
            "ink_url": url, "pdf_position": up["pdf_position"], "ink_strokes": 1}},
    ]})
    assert r.status_code == 200, r.text
    return page, url


def test_markdown_export_bundles_an_svg_picture(guest):
    page, url = _page_with_ink(guest)
    r = guest.get(f"/api/pages/{page['id']}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    stem = url.rsplit("/", 1)[1][:-4]
    md = z.read([n for n in z.namelist() if n.endswith(".md")][0]).decode()
    assert f"- ![Handwriting (p.2)](assets/{stem}.svg)" in md and "  a derivation" in md
    assert z.read(f"assets/{stem}.svg").startswith(b"<svg")


def test_notebook_pdf_declares_the_states_its_ink_sets():
    from PyPDF2 import PdfReader
    from gamma.notebook import normalize_paper, notebook_pdf

    ink = inkmod.parse_ink(_ink(strokes=[_pencil(), {"id": "hl", "tool": "highlighter", "color": "#ffe066", "size": 8,
                                                     "opacity": 0.6, "ch": "xy", "pts": [10000, 10000, 1000, 0]}]))
    pdf = notebook_pdf([(normalize_paper({"width": PAGE_W, "height": PAGE_H}), [], [ink])])
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    assert sorted(page["/Resources"]["/ExtGState"]) == ["/GSink330", "/GSink660", "/GSinkM600"]
    stream = page.get_contents().get_data()
    assert stream.index(b"/GSinkM600 gs") < stream.index(b"/GSink330 gs")   # the highlighter goes under


def test_notes_pdf_draws_the_strokes(guest):
    page, _ = _page_with_ink(guest, "Ink notes pdf")
    r = guest.get(f"/api/pages/{page['id']}/export?mode=notes-pdf")
    assert r.status_code == 200, r.text
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(r.content))
    stream = b"".join(p.get_contents().get_data() for p in reader.pages)
    assert b"1 J 1 j" in stream and stream.count(b" l S") == 4
    assert "handwriting, p. 2" in reader.pages[0].extract_text()
    assert "/ExtGState" not in reader.pages[0]["/Resources"]      # an opaque pen sets no state
    # a pencil's shades are graphics states the page declares
    up = guest.post("/api/upload-ink", json=_ink(page=2, strokes=[_pencil()])).json()
    pencil = make_page(guest, "Pencil notes pdf")
    guest.put(f"/api/blocks/{pencil['id']}/children", json={"blocks": [
        {"id": f"ink-{pencil['id']}", "content": "shaded", "children": [], "properties": {
            "ink_url": up["url"], "pdf_position": up["pdf_position"], "ink_strokes": 1}}]})
    r = guest.get(f"/api/pages/{pencil['id']}/export?mode=notes-pdf")
    assert r.status_code == 200, r.text
    page = PdfReader(io.BytesIO(r.content)).pages[0]
    assert sorted(page["/Resources"]["/ExtGState"]) == ["/GSink330", "/GSink660"]


# --- the calligraphy pen and fills (handwriting.md "Calligraphy and fills on the server") ---

def _rect(fill=None, kind="rect", **over):
    """A rectangle as the Shapes tool or a hold commits it: its corners with a
    sample every 2 pt, closed, pen false, written xyt, its shape record."""
    corners = [(100, 300), (180, 300), (180, 340), (100, 340), (100, 300)]
    samples = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) // 2))
        samples += [{"x": ax + (bx - ax) * j / n, "y": ay + (by - ay) * j / n} for j in range(n)]
    samples.append({"x": 100, "y": 300})
    for i, s in enumerate(samples):
        s["t"] = 4 * i
    params = {"cx": 140, "cy": 320, "w": 80, "h": 40, "angle": 0} if kind == "rect" else {"x1": 100, "y1": 300, "x2": 180, "y2": 340}
    return {"id": over.pop("id", "r"), "tool": "pen", "brush": "monoline", "pen": False, "color": "#1d4ed8", "size": 2, "ch": "xyt",
            "pts": inkmod.encode_points(samples, "xyt"), "shape": {"kind": kind, "params": params},
            **({"fill": fill} if fill else {}), **over}


def test_calligraphy_bounds_reach_the_nib_corners_whatever_w():
    """The screen draws a calligraphy stroke with its nib, which reaches
    size × hypot(1, 0.12) / 2 from every sample; the box says so, as the
    client's strokeBounds does, and a calligraphy shape is its size."""
    samples = [{"x": 100 + 2 * i, "y": 200 + 2 * i, "p": 0.5, "t": 8 * i, "w": 1} for i in range(21)]
    stroke = {"id": "c", "brush": "calligraphy", "nib": 45, "size": 2, "ch": "xyptw", "pts": inkmod.encode_points(samples, "xyptw")}
    reach = 2 * inkmod.NIB_REACH / 2
    box = inkmod.bounding_box(inkmod.parse_ink(_ink(strokes=[stroke])))
    assert box == pytest.approx((100 - reach, 200 - reach, 140 + reach, 240 + reach))
    # a hairline's w would give a box a hair wide: the nib's corners still reach
    thin = {**stroke, "pts": inkmod.encode_points([{**s, "w": 0.12} for s in samples], "xyptw")}
    assert inkmod.bounding_box(inkmod.parse_ink(_ink(strokes=[thin]))) == pytest.approx(box)
    shaped = {**stroke, "pen": False, "ch": "xyt", "shape": {"kind": "line", "params": {"x1": 100, "y1": 200, "x2": 140, "y2": 240}},
              "pts": inkmod.encode_points([{"x": 100, "y": 200, "t": 0}, {"x": 140, "y": 240, "t": 90}], "xyt")}
    assert inkmod.bounding_box(inkmod.parse_ink(_ink(strokes=[shaped]))) == pytest.approx((99, 199, 141, 241))


def test_the_exporters_draw_a_calligraphy_stroke_with_its_nib():
    """The nib's swept outline, one path filled with the nonzero rule, the
    screen's (nib_outline; tests/shared/inknib.json); every dash its own
    nib; dots round at the drawn width; a calligraphy shape an even line;
    and a translucent one painted once under its opacity."""
    samples = [{"x": 100 + 2 * i, "y": 200 + 2 * i, "p": 0.5, "t": 8 * i, "w": 1} for i in range(21)]
    stroke = {"id": "c", "brush": "calligraphy", "nib": 45, "size": 2, "ch": "xyptw", "pts": inkmod.encode_points(samples, "xyptw")}
    ink = inkmod.parse_ink(_ink(strokes=[stroke]))
    polys = inkmod.nib_outline(inkmod.decode_stroke(ink.strokes[0]), 2, 45)
    # the nib's footprint at each end and its sweep (the hairline runs along this travel: it sweeps nothing)
    assert len(polys) == 3 and all(len(p) == 4 for p in (polys[0], polys[-1]))
    svg = inkmod.to_svg(ink)
    assert svg.count("<path") == 1 and svg.count(" Z") == 3 and 'fill="rgb(31,31,31)"' in svg and "stroke=" not in svg
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    assert ops.count(b" h\n") == 3 and ops.count(b"\nf\n") == 1 and b" S" not in ops
    # the nib's corners, not a round cap: the start's footprint is the nib's rectangle, 45° to the page
    (ax, ay), (bx, by) = polys[0][0], polys[0][1]
    assert math.hypot(ax - bx, ay - by) in (pytest.approx(0.24), pytest.approx(2))
    dashed = inkmod.parse_ink(_ink(strokes=[{**stroke, "dash": "dashed"}]))
    runs, dots = inkmod.nib_shape(dashed.strokes[0])
    assert len(runs) == len(inkmod.dash_runs(dashed.strokes[0])) > 1 and not dots
    dotted = inkmod.parse_ink(_ink(strokes=[{**stroke, "dash": "dotted"}]))
    runs, dots = inkmod.nib_shape(dotted.strokes[0])
    assert not runs and len(dots) > 1 and all(r == pytest.approx(1) for _, _, r in dots)   # size × w / 2
    assert inkmod.to_svg(dotted).count(" a1.00,1.00 0 1,0 2.00,0") == len(dots)
    assert inkmod.pdf_path_ops(dotted, lambda x, y: (x, y)).count(b" c") == 4 * len(dots)
    # a shape is an even line, whatever its brush, as any pen
    shaped = {**stroke, "pen": False, "ch": "xyt", "shape": {"kind": "line", "params": {"x1": 100, "y1": 200, "x2": 140, "y2": 240}},
              "pts": inkmod.encode_points([{"x": 100, "y": 200, "t": 0}, {"x": 140, "y": 240, "t": 90}], "xyt")}
    assert 'stroke-width="2"' in inkmod.to_svg(inkmod.parse_ink(_ink(strokes=[shaped])))
    # translucent: the union painted once, under its state
    pale = inkmod.parse_ink(_ink(strokes=[{**stroke, "opacity": 0.4}]))
    assert inkmod.to_svg(pale).count('fill-opacity="0.4"') == 1
    ops = inkmod.pdf_path_ops(pale, lambda x, y: (x, y))
    assert ops.count(b"\nf\n") == 1 and b"q /GSink400 gs\n" in ops and sorted(inkmod.pdf_path_resources(ops)) == ["/GSink400"]


def test_a_nib_is_geometry_in_the_merge():
    base = inkmod.parse_ink(_ink(strokes=[{**_ink()["strokes"][0], "brush": "calligraphy", "nib": 45}]))
    turned = inkmod.parse_ink(_ink(strokes=[{**_ink()["strokes"][0], "brush": "calligraphy", "nib": 60}]))
    moved = inkmod.parse_ink(_ink(strokes=[{**_ink()["strokes"][0], "brush": "calligraphy", "nib": 45,
                                            "pts": inkmod.encode_points(_samples(x0=150), "xyptw")}]))
    got = inkmod.merge_ink(base, turned, moved)
    assert not got.clean and got.lost_ours == ["s1"] and got.ink.strokes[0].nib == 45 and got.ink.strokes[0].pts == moved.strokes[0].pts


def test_a_filled_closed_shape_draws_its_fill_under_its_outline():
    ink = inkmod.parse_ink(_ink(strokes=[_rect({"color": "#fde047"}), _rect({"color": "#fde047"}, kind="line", id="l")]))
    svg = inkmod.to_svg(ink)
    fill = '" fill="rgb(253,224,71)" fill-opacity="0.25"/>'
    assert svg.count(fill) == 1, "the rectangle's fill, not the line's"
    at = svg.index(fill)
    assert svg.rindex("<path", 0, at) < svg.index('<g fill="none" stroke="rgb(29,78,216)"'), "the fill comes first"
    assert svg[svg.rindex("<path", 0, at):at].endswith(" Z"), "a closed polygon"
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    assert ops.count(b" h f") == 1 and b"q /GSink250 gs\n0.99 0.88 0.28 rg\n" in ops
    assert ops.index(b" h f") < ops.index(b"0.11 0.31 0.85 RG"), "under the outline"
    assert sorted(inkmod.pdf_path_resources(ops)) == ["/GSink250"]
    # an opaque fill sets no state; none at all, no fill drawn
    solid = inkmod.parse_ink(_ink(strokes=[_rect({"color": "#fde047", "opacity": 1})]))
    assert inkmod.pdf_path_resources(inkmod.pdf_path_ops(solid, lambda x, y: (x, y))) is None
    assert b" h f" not in inkmod.pdf_path_ops(inkmod.parse_ink(_ink(strokes=[_rect()])), lambda x, y: (x, y))
    # the AI's picture: the page's content stamped with the fill, its state declared
    from PyPDF2 import PdfReader
    from gamma.pdf_export import page_with_ink
    out, _ = page_with_ink(_blank_pdf(), 1, [ink])
    page = PdfReader(io.BytesIO(out)).pages[0]
    stamped = b"".join(part.get_object().get_data() for part in page["/Contents"])
    assert b" h f" in stamped and "/GSink250" in page["/Resources"]["/ExtGState"]


def test_the_annotated_pdf_keeps_a_filled_shape_as_ink_and_brings_the_fill_back():
    """/Ink-pure: a filled rectangle is its outline in its bucket's /Ink, the
    fill only in /GammaInk; no /Square, /Circle or /Polygon is written for
    ink, and a Gamma import reads the fill back."""
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf
    from gamma.routers.imports import _extract_pdf_annotations

    ink = inkmod.parse_ink(_ink(strokes=[_rect({"color": "#fde047", "opacity": 0.4})]))
    out, written = annotate_pdf(_blank_pdf(), [], ink=[{"ink": ink, "note": "", "id": "blk"}])
    assert written == 1
    reader = PdfReader(io.BytesIO(out))
    annots = [a.get_object() for a in reader.pages[0]["/Annots"]]
    assert [str(a["/Subtype"]) for a in annots] == ["/Ink"]
    assert json.loads(str(annots[0]["/GammaInk"]))["strokes"][0]["fill"] == {"color": "#fde047", "opacity": 0.4}
    back = _extract_pdf_annotations(reader)[0]["ink"].strokes[0]
    assert back.fill.color == "#fde047" and back.fill.opacity == 0.4 and back.shape.kind == "rect"
