"""Handwriting (gamma/ink.py, routers/ink.py): the stroke-file codec and
its limits, the upload endpoint, the /Ink round trip through the annotated
PDF export and the embedded-annotation importer, and the Markdown / notes-PDF
renderings of an ink block."""

import io
import json
import zipfile

import pytest
from conftest import make_page, guest_name, workspace_of

from gamma import ink as inkmod

PAGE_W, PAGE_H = 612, 792


def _samples(n=5, x0=100.0, y0=200.0):
    return [{"x": x0 + 10 * i, "y": y0 + 3 * i, "p": 0.2 + 0.15 * i, "t": 16 * i} for i in range(n)]


def _ink(page=1, strokes=None, **space):
    strokes = strokes if strokes is not None else [{
        "id": "s1", "tool": "pen", "color": "#1f1f1f", "size": 2, "opacity": 1, "pen": True,
        "t0": 1757760000000, "ch": "xypt", "pts": inkmod.encode_points(_samples(), "xypt"),
    }]
    return {"format": "gamma-ink", "version": 1,
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
    # deltas after the first sample: x steps of 10 pt = 1000 hundredths
    assert stroke.pts[:4] == [10000, 20000, 200, 0]
    assert stroke.pts[4:8] == [1000, 300, 350, 16]
    decoded = inkmod.decode_stroke(stroke)
    assert [(round(s["x"], 2), round(s["y"], 2), s["t"]) for s in decoded] == \
        [(100 + 10 * i, 200 + 3 * i, 16 * i) for i in range(5)]
    assert decoded[1]["p"] == pytest.approx(0.35)
    # widths follow pressure for a real pen, stay flat for a highlighter
    assert inkmod.stroke_width(stroke, 1.0) > stroke.size > inkmod.stroke_width(stroke, 0.0)
    hl = stroke.model_copy(update={"tool": "highlighter"})
    assert inkmod.stroke_width(hl, 0.1) == inkmod.stroke_width(hl, 0.9) == hl.size


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
    legacy = inkmod.parse_ink(_ink())
    assert b'"brush"' not in inkmod.dumps(legacy)
    data = _ink()
    data["strokes"][0]["brush"] = "monoline"
    r = guest.post("/api/upload-ink", json=data)
    assert r.status_code == 200, r.text
    stored = guest.get(r.json()["url"])
    ink = inkmod.parse_ink(stored.content)
    assert ink.strokes[0].brush == "monoline"
    assert inkmod.decode_stroke(ink.strokes[0]) == inkmod.decode_stroke(legacy.strokes[0])
    assert {w for _, _, w in inkmod.stroke_polyline(ink.strokes[0])} == {2}
    # Both SVG export and the notes PDF keep a uniform 2pt stroke despite
    # varying pressure, while the native JSON retains those pressure samples.
    svg = inkmod.to_svg(ink)
    assert svg.count('stroke-width="2"') == 4
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    assert ops.count(b"2.00 w") == 4
    invalid = _ink()
    invalid["strokes"][0].update(tool="highlighter", brush="monoline")
    assert guest.post("/api/upload-ink", json=invalid).status_code == 400


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.__setitem__("format", "other"), "format"),
    (lambda d: d["space"].pop("page"), "page"),
    (lambda d: d["strokes"][0].__setitem__("ch", "xx"), "channel"),
    (lambda d: d["strokes"][0].__setitem__("pts", [1, 2, 3]), "multiple"),
    (lambda d: d["strokes"][0].__setitem__("color", "red"), "color"),
    (lambda d: d["strokes"].append(dict(d["strokes"][0])), "duplicate"),
    (lambda d: d["strokes"][0].__setitem__("extra", 1), "extra"),
])
def test_schema_rejects_bad_files(mutate, message):
    data = _ink()
    mutate(data)
    with pytest.raises(inkmod.InkError) as e:
        inkmod.parse_ink(data)
    assert message.lower() in str(e.value).lower()


def test_budgets(monkeypatch):
    monkeypatch.setattr(inkmod, "MAX_SAMPLES", 8)
    with pytest.raises(inkmod.InkError):
        inkmod.parse_ink(_ink(strokes=[{"id": "a", "ch": "xy", "pts": [0] * 10}, {"id": "b", "ch": "xy", "pts": [0] * 10}]))
    monkeypatch.setattr(inkmod, "MAX_BYTES", 10)
    with pytest.raises(inkmod.InkError):
        inkmod.parse_ink(json.dumps(_ink()).encode())


def test_svg_and_pdf_ops_render_every_stroke():
    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "a", "ch": "xyp", "pts": inkmod.encode_points(_samples(), "xyp")},
        {"id": "b", "tool": "highlighter", "color": "rgba(255, 226, 143, 0.65)", "size": 8, "opacity": 0.5,
         "ch": "xy", "pts": inkmod.encode_points(_samples(3, 300, 300), "xy")},
    ]))
    svg = inkmod.to_svg(ink)
    assert svg.startswith("<svg") and svg.count("<path") == 5   # 4 pen segments + 1 highlighter path
    assert "multiply" in svg and 'stroke="rgb(255,226,143)"' in svg
    ops = inkmod.pdf_path_ops(ink, lambda x, y: (x, PAGE_H - y))
    # 4 pen segments each stroked alone + the highlighter's one polyline
    assert ops.count(b" l S") == 5 and ops.count(b" m ") == 5 and ops.startswith(b"q 1 J 1 j")


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
    """The route a mirror pushes files by: an .ink must be a drawing."""
    from gamma.storage import content_digest
    good = _bytes(_ink())
    r = guest.post("/api/upload-file", files={"file": ("x.ink", good, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == f"/api/uploads/{content_digest(good)}.ink"
    bad = guest.post("/api/upload-file", files={"file": ("x.ink", b'{"format": "gamma-ink"}', "application/octet-stream")})
    assert bad.status_code == 400 and "invalid ink" in bad.json()["detail"]


def test_upload_ink_rejects_bad_files(guest):
    r = guest.post("/api/upload-ink", json={"format": "gamma-ink", "version": 1, "space": {"width": 1, "height": 1}})
    assert r.status_code == 400 and "page" in r.json()["detail"]
    r = guest.post("/api/upload-ink", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


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


# --- two writers, one group ------------------------------------------------------------

def _stroke(sid, x=100.0, color="#1f1f1f"):
    return {"id": sid, "tool": "pen", "color": color, "size": 2, "opacity": 1, "pen": True, "ch": "xy",
            "pts": inkmod.encode_points([{"x": x, "y": 200.0}, {"x": x + 20, "y": 210.0}], "xy")}


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
    file with its count and box."""
    page, u0 = _group(guest, [_stroke("a")], "mg1")
    theirs = _upload(guest, [_stroke("a"), _stroke("x", 300)])
    ours = _upload(guest, [_stroke("a", color="#dc2626"), _stroke("b", 400)])
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "one", "ops": [
        {"op": "set", "id": "mg1", "props": {"ink_url": theirs, "ink_strokes": 2}, "base_props": {"ink_url": u0}}]})
    assert r.json()["ops"][0]["props"]["ink_url"] == theirs  # its base was current: as sent
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "two", "ops": [
        {"op": "set", "id": "mg1", "props": {"ink_url": ours, "ink_strokes": 2, "pdf_position": None},
         "base_props": {"ink_url": u0}}]})
    assert r.status_code == 200, r.text
    applied = r.json()["ops"][0]["props"]
    assert applied["ink_url"] not in (u0, ours, theirs) and applied["ink_strokes"] == 3
    assert applied["pdf_position"]["pageNumber"] == 1 and "pdf_page" not in applied
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
    """PUT /blocks/{id}, the browser's ink flush: base_properties, and the
    applied properties in the answer."""
    page, u0 = _group(guest, [_stroke("a")], "mg3")
    theirs = _upload(guest, [_stroke("a"), _stroke("x", 300)])
    guest.put("/api/blocks/mg3", json={"properties": {"ink_url": theirs}, "base_properties": {"ink_url": u0}})
    ours = _upload(guest, [_stroke("b", 400)])  # a erased here, b drawn
    r = guest.put("/api/blocks/mg3", json={"properties": {"ink_url": ours, "ink_strokes": 1},
                                            "base_properties": {"ink_url": u0}})
    assert r.status_code == 200, r.text
    stored = r.json()["properties"]
    props, ink = _stored(guest, "mg3")
    assert stored["ink_url"] == props["ink_url"] and stored["ink_strokes"] == 2
    assert [s.id for s in ink.strokes] == ["x", "b"]


# --- PDF interchange ------------------------------------------------------------------

def test_annotated_pdf_writes_ink_and_importer_reads_it_back():
    from PyPDF2 import PdfReader
    from gamma.pdf_export import annotate_pdf
    from gamma.routers.imports import _extract_pdf_annotations

    ink = inkmod.parse_ink(_ink(strokes=[
        {"id": "a", "ch": "xypt", "pts": inkmod.encode_points(_samples(), "xypt"), "t0": 5},
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

    found = _extract_pdf_annotations(reader)
    assert len(found) == 2 and all(f["kind"] == "ink" for f in found)
    back = found[0]["ink"]
    assert back.strokes[0].ch == "xypt" and back.strokes[0].t0 == 5   # the private key kept pressure + time
    assert found[0]["content"] == "my ink" and found[0]["position"]["pageNumber"] == 1


def test_importer_reads_foreign_ink_without_private_key():
    from PyPDF2 import PdfReader, PdfWriter
    from PyPDF2.generic import ArrayObject, DictionaryObject, FloatObject, NameObject
    from gamma.routers.imports import _extract_pdf_annotations

    w = PdfWriter()
    w.add_blank_page(width=PAGE_W, height=PAGE_H)
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Ink"),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in (10, 10, 60, 60)),
        NameObject("/InkList"): ArrayObject([ArrayObject(FloatObject(v) for v in (10, 10, 30, 40, 60, 60))]),
        NameObject("/BS"): DictionaryObject({NameObject("/W"): FloatObject(3)}),
        NameObject("/C"): ArrayObject(FloatObject(v) for v in (0, 0, 1)),
    })
    w.add_annotation(page_number=0, annotation=annot)
    buf = io.BytesIO()
    w.write(buf)
    found = _extract_pdf_annotations(PdfReader(io.BytesIO(buf.getvalue())))
    assert len(found) == 1
    ink = found[0]["ink"]
    s = ink.strokes[0]
    assert s.size == 3 and s.color == "#0000ff" and s.pen is False
    pts = inkmod.decode_stroke(s)
    assert (pts[0]["x"], pts[0]["y"]) == (10, PAGE_H - 10) and pts[-1]["x"] == 60


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
    assert props["pdf_position"] == inkmod.pdf_position(inkmod.parse_ink(guest.get(props["ink_url"]).content))
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
