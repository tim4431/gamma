"""The GoodNotes export (gamma/goodnotes_export.py, gamma/goodnotes_write.py;
docs/dev/import_export.md "GoodNotes export"): a page out as a .goodnotes is
what GoodNotes draws of it — its PDF or its sheets as the pages, its
handwriting as ball pen and highlighter strokes, its highlights as
highlighter strokes and pen lines, its text boxes and pictures — read back
by Gamma's own GoodNotes reader and brought back by its import; the file
is built as GoodNotes builds one, event for event and field for field
against GoodNotes' own export; and what a notebook cannot hold is said."""

import io
import math
import struct
import zipfile
from pathlib import Path

import pytest

import notability_fixtures as fx
from conftest import login, make_user, workspace_of
from gamma import goodnotes as gn
from gamma import goodnotes_codec as gc
from gamma import goodnotes_export as gexp
from gamma import goodnotes_write as gw
from gamma import jobs, notebook, storage
from gamma.picture import is_picture, normalize_picture
from gamma.text_box import normalize_text_box
from test_goodnotes import bundles
from test_notability_export import ink_url, insert, page, shape_stroke, stored_url, stroke, trace, tree

SAMPLE = Path(__file__).parent / "samples" / "goodnotes" / "gn-mac-mixed-pens.goodnotes"
A4 = notebook.normalize_paper(None)
C = 11 / 6
HIGHLIGHT = "rgba(255, 226, 52, 0.65)"


@pytest.fixture(scope="module")
def owner(client):
    make_user("gnx_owner", "gnx-password")
    return login("gnx_owner", "gnx-password")


@pytest.fixture(scope="module")
def ws(owner):
    return workspace_of("gnx_owner")


# --- out and in -------------------------------------------------------------------------------

def export(client, pid: str) -> bytes:
    r = client.get(f"/api/pages/{pid}/export", params={"mode": "goodnotes"})
    assert r.status_code == 200, r.text
    assert ".goodnotes" in r.headers["content-disposition"]
    return r.content


def read(data: bytes) -> gn.Notebook:
    [bundle] = bundles(data)
    return gn.read_notebook(bundle.members, path=bundle.path)


def reimport(client, data: bytes) -> dict:
    r = client.post("/api/import/goodnotes", files={"file": ("Round trip.goodnotes", data, "application/zip")},
                    data={"folder": ""})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["pages_created"] == 1, report
    return report


def records(data: bytes, member: str) -> list[gc.Message]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [gc.Message(r) for r in gc.iter_records(zf.read(member))]


def events(data: bytes) -> list[tuple[int, str, gc.Message]]:
    """The journal's events as ``(kind, target, body)``."""
    out = []
    for e in records(data, "index.events.pb"):
        kind, _w, raw = next(f for f in e.fields if f[0] != 1)
        out.append((kind, e.str(1), gc.Message(raw)))
    return out


def strokes_of(client, ws: str, pid: str) -> dict:
    """A page's strokes by surface (a PDF page's number, a sheet's index),
    in file order, each as a dict without its id."""
    from gamma import ink as inkmod
    stored = storage.files(ws)
    out: dict = {}
    root = tree(client, pid)
    sheets = [b for b in root.get("children") or [] if b["kind"] == "sheet"]
    for n, holder in enumerate(sheets or [root]):
        for b in holder.get("children") or []:
            if b["kind"] == "ink":
                ink = inkmod.read_upload(stored, b["properties"]["ink_url"])
                key = n if sheets else ink.space.page
                out.setdefault(key, []).extend(
                    {k: v for k, v in inkmod.to_dict(s).items() if k not in ("id", "from", "t0")} for s in ink.strokes)
    return out


# --- a PDF page -------------------------------------------------------------------------------

def pdf_page(client, ws: str, title: str) -> tuple[str, bytes, str]:
    """A PDF of three pages of three sizes, with handwriting of every kind
    on its first page, a highlight of every style, text boxes, a cropped,
    flipped and turned picture, and notes."""
    pdf = fx.pdf(((612, 792), (595.28, 841.89), (792, 612)))
    r = client.post("/api/uploads", files={"file": ("paper.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    doc = r.json()["doc_id"]
    pid = client.post(f"/api/blocks/by-doc/{doc}", json={"default_title": title}).json()["id"]
    tag = pid[:6]
    one = ink_url(client, [
        stroke("pen", trace(30, 100, 150, p=lambda i: i / 40, w=lambda i: 0.6 + i / 30), "xypw", size=2,
               color="#1f4e9d"),
        stroke("even", trace(20, 100, 200), "xy", pen=False, color="#aa2222", size=3),
        stroke("hi", trace(20, 100, 250), "xy", tool="highlighter", color="#ffd400", size=14, opacity=0.6),
        stroke("dash", trace(20, 100, 300), "xy", pen=False, dash="dashed", size=2),
        shape_stroke("rect", "rect", {"cx": 300, "cy": 400, "w": 120, "h": 60, "angle": 0},
                     fill={"color": "#ffeeaa", "opacity": 0.5}),
    ], {"kind": "pdf-page", "page": 1, "width": 612, "height": 792})
    three = ink_url(client, [stroke("c", trace(50, 500, 300), "xy", pen=False)],
                    {"kind": "pdf-page", "page": 3, "width": 792, "height": 612})

    def mark(key, rects, **props):
        return {"id": f"{tag}{key}", "props": {"pdf_position": {
            "pageNumber": 1, "width": 612, "height": 792, "boundingRect": rects[0], "rects": rects}, **props}}

    wash = [{"x1": 100, "y1": 72, "x2": 300, "y2": 92}, {"x1": 100, "y1": 94, "x2": 200, "y2": 114}]
    picture = stored_url(ws, fx.png(120, 80), ".png")
    insert(client, pid, pid, [
        mark("H", wash, color=HIGHLIGHT, quote="quoted text"),
        mark("U", [{"x1": 100, "y1": 500, "x2": 220, "y2": 520}], color=HIGHLIGHT, style="underline"),
        mark("S", [{"x1": 100, "y1": 530, "x2": 220, "y2": 550}], color="#1f1f1f", style="strikeout"),
        mark("W", [{"x1": 100, "y1": 560, "x2": 220, "y2": 580}], color=HIGHLIGHT, style="squiggly"),
        mark("A", [{"x1": 400, "y1": 600, "x2": 500, "y2": 700}], color=HIGHLIGHT,
             **{"pdf_position": {"pageNumber": 1, "width": 612, "height": 792, "area": True,
                                 "boundingRect": {"x1": 400, "y1": 600, "x2": 500, "y2": 700},
                                 "rects": [{"x1": 400, "y1": 600, "x2": 500, "y2": 700}]}}),
        {"id": f"{tag}I1", "props": {"ink_url": one, "ink_strokes": 5}},
        {"id": f"{tag}I3", "props": {"ink_url": three, "ink_strokes": 1}},
        {"id": f"{tag}B1", "content": "Plain **bold** and *italic*\n- one\n- two", "props": {
            "text_box": {"x": 40, "y": 700, "w": 200, "h": 60, "auto": False, "size": 12, "color": "#1f1f1f",
                         "bg": None}, "pdf_page": 1}},
        {"id": f"{tag}B2", "content": "Second page", "props": {
            "text_box": {"x": 300, "y": 50, "w": 240, "h": 40, "auto": False, "size": 18, "color": "#004488",
                         "bg": "#dcecff"}, "pdf_page": 2}},
        {"id": f"{tag}P", "props": {"pdf_page": 1, "picture": {
            "url": picture, "x": 300, "y": 520, "w": 60, "h": 80, "angle": 30,
            "crop": {"x": 0.25, "y": 0, "w": 0.5, "h": 1}, "flip_h": True}}},
        {"id": f"{tag}N", "content": "What I think of it"},
    ])
    insert(client, pid, f"{tag}H", [{"id": f"{tag}HN", "content": "a note on the highlight"}])
    return pid, pdf, picture


@pytest.fixture(scope="module")
def paper_trip(owner, ws):
    """The PDF page, its .goodnotes, the notebook read back, and the page its import makes."""
    pid, pdf, picture = pdf_page(owner, ws, "GoodNotes paper")
    data = export(owner, pid)
    report = reimport(owner, data)
    return {"pid": pid, "pdf": pdf, "picture": picture, "data": data, "notebook": read(data),
            "back": report["pages"][0]["id"], "report": report}


def test_a_pdf_page_is_a_page_per_page_of_its_pdf(paper_trip):
    nb_ = paper_trip["notebook"]
    assert nb_.title == "GoodNotes paper"
    assert [p.template.page for p in nb_.pages] == [1, 2, 3]
    assert len({p.template.attachment for p in nb_.pages}) == 1
    assert [tuple(round(v, 2) for v in p.template.canvas) for p in nb_.pages] == [
        (round(612 * C, 2), round(792 * C, 2)), (round(595.28 * C, 2), round(841.89 * C, 2)),
        (round(792 * C, 2), round(612 * C, 2))]
    with zipfile.ZipFile(io.BytesIO(paper_trip["data"])) as zf:
        [pdf] = [n for n in zf.namelist() if n.startswith("attachments/") and zf.read(n)[:5] == b"%PDF-"]
        assert zf.read(pdf) == paper_trip["pdf"]


def test_the_handwriting_is_ball_pen_and_highlighter_ink(paper_trip):
    """After the highlights' five strokes, the handwriting in the order the
    screen draws it: the highlighter (band 0), then the pens."""
    first = paper_trip["notebook"].pages[0]
    assert first.shapes == []
    assert all(i.kind == "flat" for i in first.ink)
    hi, pen, even, dash, rect = first.ink[5:]
    # the pressure pen at the median of its drawn widths: GoodNotes' W is twice that, in points
    widths = sorted(2 * (0.6 + i / 30) for i in range(30))
    assert pen.width == pytest.approx(2 * (widths[14] + widths[15]) / 2, abs=1e-3)
    assert pen.rgba == pytest.approx((0x1f / 255, 0x4e / 255, 0x9d / 255, 1.0), abs=1e-3)
    assert min(x for x, _y in pen.points) == pytest.approx(100 * C, abs=0.01)  # points × 11/6
    assert (even.width, even.highlighter, even.dash) == (pytest.approx(6.0), False, None)
    assert (hi.highlighter, hi.rgba[3], hi.width) == (True, pytest.approx(0.5), pytest.approx(28.0))
    assert (dash.dash, dash.width) == ("dashed", pytest.approx(4.0))
    assert (rect.dash, rect.rgba[:3]) == (None, pytest.approx((0x22 / 255, 0x55 / 255, 0xaa / 255), abs=1e-3))


def test_the_highlights_are_highlighter_strokes_and_pen_lines(paper_trip):
    first = paper_trip["notebook"].pages[0]
    yellow = (1.0, 226 / 255, 52 / 255)
    wash = [i for i in first.ink if i.highlighter and i.rgba[:3] == pytest.approx(yellow, abs=0.01)]
    assert len(wash) == 2
    top = wash[0]
    assert top.width == pytest.approx(40.0)  # 20 pt high: a stroke 20 pt wide
    assert [p[1] for p in top.points][0] == pytest.approx(82 * C, abs=0.01)
    assert min(x for x, _y in top.points) == pytest.approx(110 * C, abs=0.01)  # its cap ends where the line ends
    assert max(x for x, _y in top.points) == pytest.approx(290 * C, abs=0.01)
    lines = [i for i in first.ink if not i.highlighter and i.rgba[3] == 1.0
             and abs(i.points[0][0] - 100 * C) < 3 * C and i.points[0][1] > 495 * C]
    under, strike, wavy = sorted(lines, key=lambda i: i.points[0][1])
    assert under.points[0][1] == pytest.approx((520 - 0.9) * C, abs=0.05)  # at the foot, 9 % of the line thick
    assert strike.points[0][1] == pytest.approx((530 + 0.56 * 20) * C, abs=0.05)
    assert strike.rgba[:3] == pytest.approx((0x1f / 255,) * 3, abs=1e-3)
    assert len(wavy.points) > 3  # a wave


def test_a_page_is_drawn_in_order_and_its_boxes_and_pictures_are_there(paper_trip):
    first, second, third = paper_trip["notebook"].pages
    kinds = [m.kind for m in first.media]
    assert kinds == ["picture", "text"]
    picture, box = first.media
    assert picture.rotation == pytest.approx(math.radians(30))
    assert (picture.w, picture.h) == (pytest.approx(60 * C, abs=0.01), pytest.approx(80 * C, abs=0.01))
    assert "".join(r.text for r in box.runs) == "Plain bold and italic\n• one\n• two"
    assert [(r.text, r.bold, r.italic) for r in box.runs][:4] == [
        ("Plain ", False, False), ("bold", True, False), (" and ", False, False), ("italic\n", False, True)]
    assert [m.kind for m in second.media] == ["text"]
    assert second.media[0].background == pytest.approx((0xdc / 255, 0xec / 255, 1.0, 1.0), abs=1e-3)
    assert len(third.ink) == 1 and not third.media
    # the rectangle's fill and the area note are shapes GoodNotes draws, which the import leaves out
    assert paper_trip["notebook"].left_out["shape box"] == 2


def test_the_picture_goes_out_cropped_and_flipped(paper_trip, ws):
    from PIL import Image
    with zipfile.ZipFile(io.BytesIO(paper_trip["data"])) as zf:
        pngs = [zf.read(n) for n in zf.namelist() if n.startswith("attachments/") and zf.read(n)[:4] == b"\x89PNG"]
    [png] = pngs
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (60, 80)


def test_a_pdf_page_comes_back_with_its_markup(owner, ws, paper_trip):
    back = tree(owner, paper_trip["back"])
    assert back["properties"]["doc"]["hash"] == storage.content_digest(paper_trip["pdf"])
    strokes = strokes_of(owner, ws, paper_trip["back"])
    assert sorted(strokes) == [1, 3]
    first = strokes[1]
    pens = [s for s in first if s.get("tool") != "highlighter"]
    assert {s["size"] for s in first if s.get("dash") == "dashed"} == {2.0}
    assert any(s["color"] == "#1f4e9d" for s in pens)
    children = back.get("children") or []
    boxes = {b["properties"]["pdf_page"]: b for b in children if b["kind"] == "text_box"}
    assert sorted(boxes) == [1, 2]
    two = normalize_text_box(boxes[2]["properties"]["text_box"])
    assert boxes[2]["content"] == "Second page"
    assert (two["x"], two["y"], two["w"], two["h"]) == pytest.approx((300, 50, 240, 40), abs=0.05)
    assert (two["size"], two["color"], two["bg"]) == (18, "#004488", "#dcecff")
    assert boxes[1]["content"].startswith("Plain **bold** and *italic*")
    [pic] = [b for b in children if is_picture(b["properties"])]
    placed = normalize_picture(pic["properties"]["picture"])
    assert (placed["x"], placed["y"], placed["w"], placed["h"], placed["angle"]) == pytest.approx(
        (300, 520, 60, 80, 30), abs=0.05)
    assert (placed["crop"], placed["flip_h"], placed["flip_v"]) == ({"x": 0, "y": 0, "w": 1, "h": 1}, False, False)


# --- the file -----------------------------------------------------------------------------------

def _layout(data: bytes) -> dict:
    """{event kind: the field numbers its bodies use}"""
    out: dict = {}
    for kind, _target, body in events(data):
        out.setdefault(kind, set()).update(n for n, _w, _v in body.fields)
    return out


def test_the_file_is_built_as_goodnotes_builds_one(paper_trip):
    data = paper_trip["data"]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        assert names[:5] == ["schema.pb", "document.info.pb", "index.notes.pb", "index.attachments.pb",
                             "index.events.pb"]
        assert zf.read("schema.pb") == b"\x08\x18"  # {1: 24}
        assert zf.read("document.info.pb") == b""
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in zf.infolist())
    real, ours = _layout(SAMPLE.read_bytes()), _layout(data)
    assert set(real) <= set(ours)
    for kind, fields in real.items():
        optional = {2: {9}}.get(kind, set())  # 2.9: a catalogue paper's name, which a PDF of one's own has none of
        assert fields - ours[kind] - optional == set(), f"event {kind} lacks fields"
    found = events(data)
    doc = next(target for kind, target, _b in found if kind == 30)
    templates = {body.str(2): body for kind, _t, body in found if kind == 2}
    pages = [body for kind, _t, body in found if kind == 54]
    keys = [p.msg(4).str(1) for p in pages]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    indexed = [r.str(1) for r in records(data, "index.notes.pb")]
    assert indexed == [gw.plus_one(p.str(2)) for p in pages]
    assert all(p.str(1) == doc and p.msg(3).str(1) in templates for p in pages)
    attached = {r.str(1) for r in records(data, "index.attachments.pb")}
    announced = {body.str(1): body for kind, _t, body in found if kind == 6}
    assert attached == set(announced) == {t.str(4) for t in templates.values()} | {
        r.uuid(4) for r in _metas(data) if r.uuid(4)}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert all(f"attachments/{a}" in zf.namelist() for a in attached)
        for a, body in announced.items():
            assert body.int(5) == zf.getinfo(f"attachments/{a}").file_size
            pdf = zf.read(f"attachments/{a}")[:5] == b"%PDF-"
            assert (body.msg(12).int(2) if pdf else body.bytes(12)) == (3 if pdf else b"")
    assert next(body for kind, _t, body in found if kind == 10).str(2) == pages[0].str(2)
    clock_field = {6: 15, 2: 16, 104: 18}  # each event's clock; #14 in the rest
    clocks = [body.int(clock_field.get(kind, 14)) for kind, _t, body in found]
    assert clocks == sorted(clocks) and len(set(clocks)) == len(clocks)


def _metas(data: bytes) -> list[gc.Message]:
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.startswith("notes/"):
                out += [m for m in (gc.Message(r) for r in gc.iter_records(zf.read(name))) if m.int(8) is not None]
    return out


def test_the_elements_carry_the_fields_goodnotes_writes(paper_trip):
    """Each element is a metadata record and a content record with every
    field GoodNotes' own records of its kind have (but a ball pen's absent
    type), its metadata's version that of its content."""
    def kinds(data):
        out: dict = {}
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if not name.startswith("notes/"):
                    continue
                rows = [gc.Message(r) for r in gc.iter_records(zf.read(name))]
                for meta, content in zip(rows[::2], rows[1::2]):
                    kind, body = content.only()
                    out.setdefault("meta", set()).update(n for n, _w, _v in meta.fields)
                    out.setdefault(kind, set()).update(n for n, _w, _v in body.fields)
                    assert meta.str(1) == body.str(1)
        return out
    real, ours = kinds(SAMPLE.read_bytes()), kinds(paper_trip["data"])
    assert real["meta"] <= ours["meta"]
    assert real[7] - ours[7] == {3}  # the pen's type: a ball pen has none
    assert {1, 7, 21} <= set(ours)


# --- a notebook ---------------------------------------------------------------------------------

def test_a_notebook_s_sheets_come_back_as_sheets(owner, ws):
    pid = page(owner, "GoodNotes notebook")
    tag = pid[:6]
    tinted = notebook.normalize_paper({**A4, "color": "#fbf7ec", "pattern": "ruled", "spacing": 24})
    grid = notebook.normalize_paper({"width": 500, "height": 700, "pattern": "grid", "spacing": 20,
                                     "line": "#99aacc"})
    sheets = insert(owner, pid, pid, [{"id": f"{tag}S1", "props": {"sheet": tinted}},
                                      {"id": f"{tag}S2", "props": {"sheet": grid}},
                                      {"id": f"{tag}S3", "props": {"sheet": tinted}}])
    space = {"kind": "canvas", "width": A4["width"], "height": A4["height"]}
    insert(owner, pid, sheets[0], [
        {"id": f"{tag}I", "props": {"ink_url": ink_url(owner, [stroke("a", trace(30, 60, 80), "xy", pen=False)],
                                                       space), "ink_strokes": 1}},
        {"id": f"{tag}B", "content": "On the sheet", "props": {"text_box": {
            "x": 60, "y": 300, "w": 200, "h": 40, "auto": False, "size": 14, "color": "#1f1f1f", "bg": None}}},
    ])
    data = export(owner, pid)
    nb_ = read(data)
    assert len(nb_.pages) == 3
    assert nb_.pages[0].template.attachment == nb_.pages[2].template.attachment != nb_.pages[1].template.attachment
    back = tree(owner, reimport(owner, data)["pages"][0]["id"])
    assert "doc" not in back["properties"]
    papers = [b["properties"]["sheet"] for b in back["children"] if b["kind"] == "sheet"]
    assert papers == [tinted, grid, tinted]
    first = next(b for b in back["children"] if b["kind"] == "sheet")
    assert [b["kind"] for b in first["children"]] == ["ink", "text_box"]
    assert first["children"][1]["content"] == "On the sheet"


# --- what is said -------------------------------------------------------------------------------

def test_what_a_notebook_cannot_hold_is_said(owner, ws):
    pid = page(owner, "What a notebook cannot hold")
    tag = pid[:6]
    sheet = insert(owner, pid, pid, [{"id": f"{tag}S", "props": {"sheet": A4}}])[0]
    space = {"kind": "canvas", "width": A4["width"], "height": A4["height"]}
    picture = stored_url(ws, fx.png(8, 8), ".png")
    insert(owner, pid, sheet, [
        {"id": f"{tag}I", "props": {"ink_url": ink_url(owner, [
            stroke("p", trace(20, 60, 80, p=0.5, w=lambda i: 0.5 + i / 20), "xypw"),
            stroke("pencil", trace(20, 60, 120, o=lambda i: 0.3 + i / 40), "xyo", pen=False, brush="pencil"),
            stroke("plain", trace(20, 60, 160), "xy", pen=False)], space), "ink_strokes": 3}},
        {"id": f"{tag}P", "content": "a caption", "props": {"picture": {
            "url": picture, "x": 10, "y": 10, "w": 40, "h": 40, "opacity": 0.5}}},
        {"id": f"{tag}G", "props": {"picture": {"url": f"/api/uploads/{'0' * 64}.png", "x": 1, "y": 1,
                                                "w": 9, "h": 9}}},
    ])
    insert(owner, pid, pid, [
        {"id": f"{tag}N1", "content": "a note"},
        {"id": f"{tag}N2", "content": ""},
        {"id": f"{tag}R", "content": "Lecture", "props": {"recording": {"started": 1, "state": "done", "dur": 10,
                                                                        "segments": []}}},
        {"id": f"{tag}Q", "props": {"picture": {"url": picture, "x": 1, "y": 1, "w": 9, "h": 9}}},
    ])
    insert(owner, pid, f"{tag}N1", [{"id": f"{tag}N1a", "content": "a detail"}])
    r = owner.post("/api/jobs/export", json={"page_id": pid, "mode": "goodnotes"})
    assert r.status_code == 200, r.text
    jobs.wait(r.json()["id"])
    job = owner.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["state"] == "done", job.get("error")
    assert job["artifact"]["name"] == "What a notebook cannot hold.goodnotes"
    assert sorted(w["reason"] for w in job["result"]["warnings"]) == sorted([
        "2 notes stay behind: a GoodNotes page holds handwriting, text boxes and pictures, not notes",
        "1 recording is left out",
        "1 picture is on no page and is left out",
        "1 file of the page is missing on the server and is left out",
        "1 picture's caption stays behind: GoodNotes shows none",
        "1 picture shows at full opacity in GoodNotes, which keeps no picture's opacity",
        "2 strokes of varying width (pressure, a pencil, a calligraphy nib) are written at one width: "
        "GoodNotes' ball pen has one",
    ])


def test_what_is_still_embedded_in_the_pdf_is_the_pdf_s_own(owner, ws):
    """Handwriting, a highlight and a text box imported from the PDF and
    still in it are not written again: GoodNotes draws the PDF's own; once
    stripped from the PDF they are written."""
    pdf = fx.pdf(((612, 792),))
    r = owner.post("/api/uploads", files={"file": ("embedded.pdf", pdf, "application/pdf")})
    pid = owner.post(f"/api/blocks/by-doc/{r.json()['doc_id']}", json={"default_title": "Embedded"}).json()["id"]
    tag = pid[:6]
    space = {"kind": "pdf-page", "page": 1, "width": 612, "height": 792}
    rect = {"x1": 100, "y1": 72, "x2": 300, "y2": 92}
    box = {"text_box": {"x": 40, "y": 700, "w": 200, "h": 30, "auto": False, "size": 12, "color": "#1f1f1f",
                        "bg": None}, "pdf_page": 1}
    insert(owner, pid, pid, [
        {"id": f"{tag}I1", "props": {"ink_url": ink_url(owner, [stroke("a", trace(9, 100, 300), "xy")], space),
                                     "imported_annot": "ink-1"}},
        {"id": f"{tag}I2", "props": {"ink_url": ink_url(owner, [stroke("b", trace(9, 100, 400), "xy")], space),
                                     "imported_annot": "ink-2", "annot_stripped": True}},
        {"id": f"{tag}H", "props": {"pdf_position": {"pageNumber": 1, "width": 612, "height": 792,
                                                     "boundingRect": rect, "rects": [rect]},
                                    "color": HIGHLIGHT, "imported_annot": "hl-1"}},
        {"id": f"{tag}B", "content": "From the PDF", "props": {**box, "imported_annot": "box-1"}},
    ])
    [only] = read(export(owner, pid)).pages
    assert [round(i.points[0][1] / C) for i in only.ink] == [400]
    assert only.media == []


def test_a_page_with_nothing_to_draw_on_is_refused(owner):
    pid = page(owner, "Only notes")
    insert(owner, pid, pid, [{"id": f"{pid[:6]}N", "content": "a note"}])
    r = owner.get(f"/api/pages/{pid}/export", params={"mode": "goodnotes"})
    assert r.status_code == 400
    assert r.json()["detail"] == gexp.NO_SURFACE


def test_a_folder_exports_each_page_as_a_notebook_in_one_zip(owner, ws):
    from conftest import make_folder
    folder = make_folder(owner, "GoodNotes out")
    sub = make_folder(owner, "GoodNotes out/Week 2")
    first, second, notes = page(owner, "First notebook"), page(owner, "Second notebook"), page(owner, "Just notes")
    for pid, where in ((first, folder), (second, sub), (notes, folder)):
        r = owner.put(f"/api/blocks/{pid}", json={"properties": {"folders": [where]}})
        assert r.status_code == 200, r.text
        if pid != notes:
            insert(owner, pid, pid, [{"id": f"{pid[:6]}S", "props": {"sheet": A4}}])
    r = owner.post("/api/jobs/export", json={"folder": folder, "mode": "goodnotes"})
    jobs.wait(r.json()["id"])
    job = owner.get(f"/api/jobs/{r.json()['id']}").json()
    assert job["state"] == "done", job.get("error")
    assert job["result"]["skipped"] == [{"page_id": notes, "title": "Just notes", "reason": gexp.NO_SURFACE}]
    r = owner.get(f"/api/folders/{folder}/export", params={"mode": "goodnotes"})
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["First notebook.goodnotes", "Week 2/Second notebook.goodnotes"]
    assert read(zf.read("Week 2/Second notebook.goodnotes")).title == "Second notebook"


# --- the encodings ------------------------------------------------------------------------------

def test_the_encodings_read_back():
    pts = [(1.5, 2.25), (10.0, 4.0), (20.0, -3.5)]
    fmt, values = gc.tpl(gw.flat_geometry(pts, 3.25), gn.KNOWN_FORMATS)
    assert fmt == gw.FLAT
    assert gn._flat(values) == [[(1.5, 2.25), (5.75, 3.125), (10.0, 4.0), (15.0, 0.25), (20.0, -3.5)]]
    assert values[1] == 3.25
    raw = bytes(range(256)) * 300  # past one block
    framed = gw.lz4_frame(raw)
    assert framed.count(b"bv41") == math.ceil(len(raw) / gw.BLOCK)
    assert gc.lz4_frame(framed, len(raw)) == raw
    assert gc.lz4_frame(gw.lz4_frame(b""), 0) == b""
    keys = [gw.order_key(i) for i in (0, 1, 35, 36, 1295, 1296, 60_000_000)]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    p = gw.page_uuid()
    assert gw.plus_one(p)[:-1] == p[:-1]
    assert gc.Message(gw.msg((1, -60), (2, gw.F64(1.5)), (3, 2.5), (4, ""), (5, None))).fields == [
        (1, 0, (1 << 64) - 60), (2, 1, struct.pack("<d", 1.5)), (3, 5, struct.pack("<f", 2.5)), (4, 2, b"")]


def test_a_shape_s_empty_text_is_goodnotes_own():
    runs = gc.Message(gw.EMPTY_TEXT).msgs(1)
    assert len(runs) == 1 and not runs[0].has(1)  # one run, its text "" (protobuf leaves it out)


def test_a_draft_without_a_page_is_refused():
    with pytest.raises(ValueError):
        gw.write_notebook(gw.Draft("Empty"), io.BytesIO())


