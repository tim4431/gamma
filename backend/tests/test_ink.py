"""Handwriting (gamma/ink.py, routers/ink.py): the version 2 stroke-file
schema and codec and their limits, the upload endpoints (and their refusal
of version 1), the merge's answer on PUT, the /Ink round trip through the
annotated PDF export and the embedded-annotation importer, and the Markdown
/ notes-PDF renderings of an ink block. The shared fixtures (upgrade, width,
merge cases) run in test_shared_fixtures.py."""

import io
import json
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
    assert not hasattr(inkmod, "THINNING")   # the old width law is gone


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
    (lambda d: d["strokes"][0].__setitem__("pts", [100, 100, 500, 0, 2500]), "channel w"),  # w over 2.000
    (lambda d: d["strokes"][0].__setitem__("pts", [100, 100, 500, 0, -600]), "channel w"),  # w below 0
    (lambda d: d["strokes"][0].update(size=80, pts=[100, 100, 500, 0, 1500]), "max(w)"),   # 80 × 1.5 > 100
    (lambda d: d["strokes"][0].__setitem__("note", {"a": {"b": {"c": 1}}}), "deeper"),
    (lambda d: d["strokes"][0].__setitem__("note", "x" * 1100), "string"),
    (lambda d: d["strokes"][0].__setitem__("note", list(range(300))), "array"),
    (lambda d: d["strokes"][0].__setitem__("bad key", 1), "key"),
    (lambda d: d["strokes"][0].__setitem__("note", "x" * 600) or d["strokes"][0].__setitem__("note2", "y" * 600), "bytes"),
    (lambda d: d["strokes"][0].__setitem__("note", float("nan")), "finite"),
    (lambda d: d.__setitem__("meta", {f"k{i}": "x" * 1000 for i in range(20)}), "bytes"),
])
def test_schema_rejects_bad_files(mutate, message):
    data = _ink()
    mutate(data)
    with pytest.raises(inkmod.InkError) as e:
        inkmod.parse_ink(data)
    assert message.lower() in str(e.value).lower()


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
    assert "ink_conflict" not in r.json()
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "two", "ops": [
        {"op": "set", "id": "mg1", "props": {"ink_url": ours, "ink_strokes": 2, "pdf_position": None},
         "base_props": {"ink_url": u0}}]})
    assert r.status_code == 200, r.text
    applied = r.json()["ops"][0]["props"]
    assert applied["ink_url"] not in (u0, ours, theirs) and applied["ink_strokes"] == 3
    assert applied["pdf_position"]["pageNumber"] == 1 and "pdf_page" not in applied
    assert r.json()["ink_conflict"] == {"clean": True, "lost_ours": [], "returned": []}
    assert "ink_conflict" not in r.json()["ops"][0]   # never in the echo (the op log, the fan-out)
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
    # without base_ids a gone base is the union by id, as before
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


def _pdf_with_ink_annotation(gamma_ink: str | None = None):
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

    w = PdfWriter()
    w.add_blank_page(width=PAGE_W, height=PAGE_H)
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Ink"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (10, 10, 60, 60)),
        NameObject("/InkList"): ArrayObject([ArrayObject(FloatObject(v) for v in (10, 10, 30, 40, 60, 60))]),
        NameObject("/BS"): DictionaryObject({NameObject("/W"): FloatObject(3)}),
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


def test_notes_pdf_draws_the_strokes(guest):
    page, _ = _page_with_ink(guest, "Ink notes pdf")
    r = guest.get(f"/api/pages/{page['id']}/export?mode=notes-pdf")
    assert r.status_code == 200, r.text
    from PyPDF2 import PdfReader
    reader = PdfReader(io.BytesIO(r.content))
    stream = b"".join(p.get_contents().get_data() for p in reader.pages)
    assert b"1 J 1 j" in stream and stream.count(b" l S") == 4
    assert "handwriting, p. 2" in reader.pages[0].extract_text()
