"""The GoodNotes reader and planner on synthetic notebooks
(gamma/goodnotes_codec.py, gamma/rtf.py, gamma/goodnotes.py,
gamma/goodnotes_map.py; docs/dev/import_export.md "GoodNotes notebooks"):
the encodings behind their guards, the journal's pages and papers, each
kind of element, what is left out and said, and each stroke, shape and box
as Gamma draws it. The import itself is test_goodnotes_import.py; real
notebooks are test_goodnotes_samples.py."""

import contextlib
import io
import json
import math
import struct
import zipfile
from collections import Counter
from pathlib import Path

import pytest

import goodnotes_fixtures as gx
import notability_fixtures as fx
from gamma import goodnotes as gn, goodnotes_codec as gc, goodnotes_map as gm, ink, rtf
from gamma.import_zip import new_walk, walk_zip

INKWIDTH = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "inkwidth.json").read_text("utf-8"))
LINE = [(10.0, 20.0), ((15.0, 20.0), (20.0, 20.0)), ((25.0, 20.0), (30.0, 25.0))]


def bundles(data: bytes, name: str = "Notebook.goodnotes"):
    stack = contextlib.ExitStack()
    entries = []
    walk_zip(stack.enter_context(zipfile.ZipFile(io.BytesIO(data))), "", entries, new_walk(stack),
             nested=gn.nested_zip)
    return gn.find_notebooks(entries, name)


def read(notebook: gx.Notebook | bytes, **kwargs) -> gn.Notebook:
    data = notebook if isinstance(notebook, bytes) else notebook.bytes()
    [bundle] = bundles(data)
    return gn.read_notebook(bundle.members, path=bundle.path, **kwargs)


def one_page(*elements, kind: str = "grid") -> gn.Page:
    n = gx.Notebook()
    n.page(n.paper(kind), elements)
    [page] = read(n).pages
    return page


def frame(k: float = gn.CANVAS_PT):
    return gm.nmap.Frame(0, 1000, 0, 0, k, 455.04, 588.45)


def samples(stroke: dict) -> list[dict]:
    return ink.decode_stroke(ink.Stroke.model_validate({**stroke, "id": "n0"}))


# --- the encodings --------------------------------------------------------------------------

def test_records_and_messages_read_behind_their_guards():
    body = gx.msg((1, "A6B1FDA8-0000-4000-8000-000000000001"), (2, 300), (3, 1.5), (4, gx.F64(2.25)),
                  (5, gx.msg((1, 7))), (6, -30))
    [record] = gc.records(gx.stream([body]))
    m = gc.Message(record)
    assert (m.uuid(1), m.int(2), m.f32(3), m.f64(4), m.msg(5).int(1), m.signed(6)) == (
        "A6B1FDA8-0000-4000-8000-000000000001", 300, 1.5, 2.25, 7, -30)
    assert m.str(2) is None and m.int(3) is None and m.msg(9) is None and m.uuid(2) is None
    assert gc.Message(gx.msg((1, "not a uuid"))).uuid(1) is None
    with pytest.raises(gc.CodecError):
        gc.records(gx.varint(10) + b"short")
    with pytest.raises(gc.CodecError):
        gc.records(gx.stream([b"a", b"b", b"c"]), limit=2)
    for damaged in (gx.varint(1 << 3 | 3), gx.varint(0), gx.varint(1 << 3 | 2) + gx.varint(9) + b"x",
                    b"\xff" * 11):
        assert gc.Message.read(damaged) is None


def test_lz4_frames_read_compressed_stored_and_chained_blocks():
    raw = b"abcabcabcabcabcabcXYZ"
    assert gc.lz4_frame(gx.lz4_literals(raw), 1 << 10) == raw
    assert gc.lz4_frame(gx.lz4_stored(raw), 1 << 10) == raw
    # literals "abc", then a match of 15 at offset 3 (overlapping), then the literals "XYZ"
    block = bytes([0x3B]) + b"abc" + struct.pack("<H", 3) + bytes([0x30]) + b"XYZ"
    frame = b"bv41" + struct.pack("<II", len(raw), len(block)) + block + b"bv4$"
    assert gc.lz4_frame(frame, 1 << 10) == raw
    # a second block's match reaches into the first block's output
    second = bytes([0x00]) + struct.pack("<H", 3)
    chained = (b"bv4-" + struct.pack("<I", 3) + b"abc" + b"bv41" + struct.pack("<II", 4, len(second)) + second
               + b"bv4$")
    assert gc.lz4_frame(chained, 1 << 10) == b"abcabca"
    with pytest.raises(gc.CodecError, match="past"):
        gc.lz4_frame(gx.lz4_literals(raw), 10)  # declared past the limit: refused before it is decoded
    lying = b"bv41" + struct.pack("<II", 2, len(block)) + block + b"bv4$"
    for bad in (lying, gx.lz4_literals(raw)[:-4], b"bv42" + b"\0" * 8, frame.replace(struct.pack("<H", 3), b"\0\0")):
        with pytest.raises(gc.CodecError):
            gc.lz4_frame(bad, 1 << 10)


def test_tpl_images_read_and_a_count_past_the_image_is_refused():
    fmt, values = gc.tpl(gx.flat([LINE[0:1] + [LINE[1]]], width=3.0))
    assert fmt == gn.FLAT_FORMATS[0] and values[1] == 3.0 and values[2] == [0, 1]
    assert values[3] == [(10.0, 20.0)] and values[4] == [(15.0, 20.0, 20.0, 20.0)]
    image = gx.flat([LINE[0:1] + [LINE[1]]])
    huge = image.replace(struct.pack("<I", 2) + struct.pack("<H", 0), struct.pack("<I", 0xFFFFFFF0) + struct.pack(
        "<H", 0), 1)
    for bad in (huge, image[:-1], image + b"\0", b"tpl\x01" + image[4:], b"tpl\0" + struct.pack("<I", 9) + b"x",
                gx.tpl("vA(" * 10 + "v" + ")" * 10, [])):
        with pytest.raises(gc.CodecError):
            gc.tpl(bad)


def test_cocoa_rtf_reads_as_spans():
    spans = rtf.read_rtf(gx.COCOA)
    assert [(s.text, s.bold, s.size, s.color) for s in spans] == [("Plain and ", False, 24.0, (200, 0, 0)),
                                                                  ("bold", True, 24.0, (200, 0, 0))]
    marks = rtf.read_rtf(rb"{\rtf1\ansi{\fonttbl\f0 Helvetica-Oblique;}\f0 a\i0\ul u\ulnone\strike s\strike0"
                         rb"\par caf\'e9 \uc0\u8217 {\*\ignored x}{\field{\*\fldinst{HYPERLINK x}}{\fldrslt link}}}")
    assert "".join(s.text for s in marks) == "aus\ncafé ’link"
    assert [(s.text, s.italic, s.underline, s.strike) for s in marks][:3] == [
        ("a", True, False, False), ("u", True, True, False), ("s", True, False, True)]
    assert rtf.read_rtf(b"not rtf") == [] and rtf.read_rtf(b"{\\rtf1 " + b"{" * 200 + b"deep" + b"}" * 200) == []


# --- the notebook ---------------------------------------------------------------------------

def test_an_upload_of_one_notebook_or_a_zip_of_them():
    one = gx.Notebook("One")
    one.page(one.paper())
    data = one.bytes()
    [alone] = bundles(data, "One.goodnotes")
    assert alone.path == "One.goodnotes" and alone.members is not None
    packed = gx.zip_of({"Export/Biology/A.goodnotes": data, "Export/B.goodnotes": data, "Export/readme.txt": b"x",
                        "backup.goodnotes.zip": gx.zip_of({"C.goodnotes": data})})
    assert [(b.path, b.packed is not None) for b in bundles(packed, "export.zip")] == [
        ("C.goodnotes", True), ("Export/B.goodnotes", True), ("Export/Biology/A.goodnotes", True)]


def test_pages_come_in_their_order_bound_to_their_last_paper():
    n = gx.Notebook("Ordered")
    grid, ruled = n.paper("grid"), n.paper("ruled")
    later = n.page(grid, key="B")
    first = n.page(grid, key="A")
    gone = n.page(grid, key="C")
    moved = n.page(grid, key="Z")
    n.event(3, (2, later), (3, gx.msg((1, ruled))))
    n.event(55, (2, moved), (3, gx.msg((1, "0"))))
    n.event(56, (2, gone))
    notebook = read(n)
    assert [p.id for p in notebook.pages] == [moved, first, later]
    assert [p.template.design for p in notebook.pages] == [gx.PAPERS["grid"]] * 2 + [gx.PAPERS["ruled"]]
    assert notebook.title == "Ordered" and notebook.created.year == 2026
    assert notebook.pages[2].template.pitch == pytest.approx(25.0)


def test_a_page_s_member_is_its_id_plus_one_with_carry():
    assert gn._uuid_minus_one("00000000-0000-0000-0000-00000000E450") == "00000000-0000-0000-0000-00000000E44F"
    assert gx.plus_one("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF") == "00000000-0000-0000-0000-000000000000"


def test_the_digest_follows_the_files_and_not_what_goodnotes_derives():
    n = gx.Notebook("Digest")
    n.page(n.paper(), [gx.ink("A", gx.flat([LINE]))])
    base = read(n).digest
    n.extra["search/X"] = b"recognised text"
    assert read(n).digest == base
    n.page(n.paper())
    assert read(n).digest != base


def test_each_kind_of_stroke_reads_with_its_lasso_offset():
    page = one_page(
        gx.ink("F", gx.flat([LINE, [(50.0, 50.0), ((55.0, 50.0), (60.0, 50.0))]]), offset=(1.0, 2.0)),
        gx.ink("R", gx.ribbon([[(0.0, 0.0, 1.0), (5.0, 0.0, 2.0), (10.0, 0.0, 1.0)]]), kind=1),
        gx.ink("W", gx.ribbon([[(0.0, 0.0, 1.0), (5.0, 0.0, 2.0), (10.0, 0.0, 1.5)]], width_word=2.0), kind=4),
        gx.ink("T", gx.ribbon([[(0.0, 9.0, 9.0), (5.0, 9.0, 9.0), (10.0, 9.0, 9.0)]], turned=True), kind=1,
               tape=True),
        gx.ink("P", gx.pencil([(0.0, 0.0, 0.5), (5.0, 0.0, 1.0), (10.0, 0.0, 2.0)]), kind=5),
        gx.ink("H", gx.flat([LINE], width=24.0), rgba=(1.0, 1.0, 0.2, 0.5), highlighter=True, dash=[3.0, 2.0]))
    kinds = [(i.kind, i.highlighter, i.tape, i.z) for i in page.ink]
    assert kinds == [("flat", False, False, 0), ("flat", False, False, 0), ("ribbon", False, False, 1),
                     ("ribbon", False, False, 2), ("ribbon", False, True, 3), ("pencil", False, False, 4),
                     ("flat", True, False, 5)]
    assert page.ink[0].points == [(11.0, 22.0), (16.0, 22.0), (21.0, 22.0), (26.0, 22.0), (31.0, 27.0)]
    assert page.ink[2].radii == [1.0, 2.0, 1.0] and page.ink[3].radii == [1.0, 2.0, 1.5]
    assert page.ink[5].force == [0.5, 1.0, 2.0] and page.ink[6].dash == "dashed"


def test_tombstones_and_erased_content_are_passed_by():
    page = one_page(gx.ink("A", gx.flat([LINE])), gx.ink("B", gx.flat([LINE]), deleted=True),
                    gx.ink("C", gx.flat([LINE]), erased=True), gx.ink("D", None))
    assert [i.z for i in page.ink] == [0]


def test_shapes_read_with_their_fills():
    page = one_page(
        gx.ink("L", shape=gx.polyline_shape([(0, 0), (10, 0)])),
        gx.ink("C", shape=gx.curve_shape((0, 0), (5, 5), (10, 0))),
        gx.ink("R", shape=gx.rect_shape((20, 20), (10, 6), width=4.0)),
        gx.ink("E", shape=gx.ellipse_shape((50, 50), (10, 5), math.pi / 2), offset=(1.0, 0.0)),
        gx.ink("T", shape=gx.polyline_shape([(0, 0), (10, 0), (5, 8), (0, 0)])),
        gx.ink("Q", shape=gx.polyline_shape([(0, 0), (10, 0), (12, 8), (3, 9), (0, 0)])),
        gx.ink("O", shape=gx.polyline_shape([(0, 0), (10, 0), (12, 8)])),
        gx.fill("FR", "R", (0.0, 0.0, 1.0, 0.1)), gx.fill("FX", "gone"), gx.fill("FE", "E", erased=True))
    assert [s.kind for s in page.shapes] == ["line", "line", "rectangle", "ellipse", "triangle", "rectangle", "path"]
    line, curve, box, ellipse = page.shapes[:4]
    assert curve.control == (5.0, 5.0) and box.points == [(15.0, 17.0), (25.0, 17.0), (25.0, 23.0), (15.0, 23.0)]
    assert box.width == pytest.approx(2.0 / gn.CANVAS_PT) and box.fill == pytest.approx((0.0, 0.0, 1.0, 0.1))
    assert [tuple(round(v, 6) for v in p) for p in ellipse.points] == [(56.0, 40.0), (56.0, 60.0), (46.0, 60.0),
                                                                       (46.0, 40.0)]
    assert ellipse.fill is None and page.shapes[6].segments == (1, 1)


def test_pictures_text_and_unknown_kinds():
    n = gx.Notebook("Media")
    picture = n.attach(fx.png(40, 20))
    lost = gx.uuid_of(999)
    n.page(n.paper(), [
        gx.image("I", picture, (10, 10, 80, 40)),
        gx.image("J", picture, (10, 60, 80, 40), shown=(50, 80, 40, 40)),
        gx.image("K", lost, (10, 10, 80, 40)),
        gx.rtf_text("X", (100, 100, 220, 60), gx.COCOA, rgba=(1.0, 1.0, 1.0, 0.0)),
        gx.text("Y", (200, 300), (220, 60), [("Typed", None, None, False), (" bold", 30.0, (1, 0, 0, 1), True)],
                turn=math.pi / 2, scale=2.0),
        gx.text("Z", (0, 0), (100, 100), [("in a shape", None, None, False)], kind=0),
        gx.unknown("S", 20), gx.unknown("U", 99)])
    notebook = read(n)
    media = notebook.pages[0].media
    assert [m.kind for m in media] == ["picture", "picture", "text", "text"]
    assert media[0].picture == f"attachments/{picture}" and (media[0].x, media[0].w) == (10, 80)
    assert media[2].background is None and (media[2].x, media[2].y, media[2].w) == (110, 110, 200)
    assert [(r.text, r.bold, r.size) for r in media[2].runs] == [("Plain and ", False, 24.0), ("bold", True, 24.0)]
    typed = media[3]
    assert [(r.text, r.bold, r.size) for r in typed.runs] == [("Typed", False, 48.0), (" bold", True, 60.0)]
    assert (typed.w, typed.h) == (400, 80) and typed.rotation == pytest.approx(math.pi / 2)
    assert (typed.x, typed.y) == pytest.approx((-60.0, 480.0), abs=1e-4)  # turned about its origin, stood on that centre
    assert notebook.left_out == Counter({"cropped picture": 1, "picture": 1, "shape box": 1, "kind": 2})
    assert notebook.classes == Counter({"sticky note": 1, "kind 99": 1})
    assert notebook.missing == [f"attachments/{lost}"]


def test_recordings_whose_audio_is_there():
    n = gx.Notebook("Heard")
    n.page(n.paper())
    n.recording(fx.m4a(2000), when_ms=1790000200000.0)
    n.recording(fx.m4a(1000), when_ms=1790000100000.0)
    gone = n.recording(fx.m4a(1000))
    n.attachments[gone] = (n.attachments[gone][0], b"")
    data = n.bytes(drop=(n.attachments[gone][0],))
    notebook = read(data)
    assert [(r.name, r.created.timestamp()) for r in notebook.recordings] == [("Recording 1", 1790000100.0),
                                                                             ("Recording 2", 1790000200.0)]


def test_damage_reads_as_what_was_left_out():
    n = gx.Notebook("Damaged")
    page = n.page(n.paper(), [gx.ink("A", gx.flat([LINE])), gx.ink("B", b"tpl\0broken"),
                              gx.ink("C", gx.flat([[(float("nan"), 0.0), ((1.0, 1.0), (2.0, 2.0))]]))])
    member = n.pages[-1][1]
    n.contents[member] += gx.varint(50) + b"truncated"
    notebook = read(n)
    assert [i.z for i in notebook.pages[0].ink] == [0] and page == notebook.pages[0].id
    assert notebook.left_out == Counter({"element": 1, "stroke": 1})
    assert notebook.warnings == ["A page is damaged: what could not be read is left out"]
    stack = contextlib.ExitStack()
    entries = []
    walk_zip(stack.enter_context(zipfile.ZipFile(io.BytesIO(gx.zip_of({"index.events.pb": b""})))), "", entries,
             new_walk(stack))
    with pytest.raises(gn.NotebookError, match="not a GoodNotes notebook"):
        gn.read_notebook(gn.nb.Members({e.path: e for e in entries}))


def test_a_preview_reads_no_page():
    n = gx.Notebook("Preview")
    n.page(n.paper(), [gx.ink("A", gx.flat([LINE]))])
    page = read(n, ink=False).pages[0]
    assert not page.read and page.ink == []


# --- papers and plans -----------------------------------------------------------------------

def test_catalogue_papers_become_sheets_and_covers_plain_ones():
    n = gx.Notebook("Papers")
    cover = n.paper("cover", fill=(0.31764707, 0.84313726, 0.9607843), colour="Blue")
    n.page(cover)
    for kind in ("blank", "dots", "grid", "narrow", "ruled"):
        n.page(n.paper(kind, fill=(0.972549, 0.96862745, 0.9137255), colour="Yellow", inline=kind == "grid"))
    notebook = read(n)
    plan = plan_of(n, notebook)
    assert plan.kind == "notebook"
    assert [(p["pattern"], p["spacing"] if p["pattern"] != "blank" else None, p["color"], p["line"])
            for p in plan.papers] == [("blank", None, "#51d7f5", "#c8d1dc"), ("blank", None, "#f8f7e9", "#c8d1dc"),
                                      ("dots", 16, "#f8f7e9", "#d0d2d3"), ("grid", 16, "#f8f7e9", "#d0d2d3"),
                                      ("ruled", 17.5, "#f8f7e9", "#d0d2d3"), ("ruled", 25, "#f8f7e9", "#d0d2d3")]
    assert all((p["width"], p["height"]) == (455.04, 588.45) for p in plan.papers)
    assert plan.pages[0].frame.k == pytest.approx(gn.CANVAS_PT)
    assert gm.preview_kind(notebook) == "page"


def plan_of(n: gx.Notebook, notebook: gn.Notebook) -> gm.NotebookPlan:
    from gamma import goodnotes_import as gi
    [bundle] = bundles(n.bytes())
    pages, _readers, _sources, colours, missing = gi.notebook_pdfs(bundle.members, notebook)
    assert not missing
    return gm.plan_notebook(notebook, pages, colours)


def test_a_notebook_of_one_pdf_carries_it_and_any_other_is_composed():
    n = gx.Notebook("Original")
    data = fx.pdf(((612, 792), (612, 792)))
    n.page(n.pdf(data, 1))
    pdf = next(iter(n.attachments))
    n.page(n.pdf(data, 2, attachment=pdf))
    notebook = read(n)
    plan = plan_of(n, notebook)
    assert (plan.kind, plan.pdf) == ("original", pdf) and gm.preview_kind(notebook) == "pdf"
    assert [(p.frame.pdf, p.frame.pdf_page, p.frame.width) for p in plan.pages] == [(pdf, 1, 612), (pdf, 2, 612)]
    n.page(n.paper("grid"))
    mixed = plan_of(n, read(n))
    assert mixed.kind == "composed" and [p.frame.pdf is not None for p in mixed.pages] == [True, True, True]
    assert mixed.papers == [None, None, None]
    unknown = gx.Notebook("Unknown design")  # a paper not known is its own PDF, as GoodNotes draws it
    unknown.page(unknown.paper("cornell"))
    unknown.page(unknown.paper("grid"))
    assert plan_of(unknown, read(unknown)).kind == "composed"


def test_a_page_without_its_paper_is_blank_paper():
    n = gx.Notebook("Lost paper")
    n.page(None)
    n.page(n.pdf(b"not a pdf"))
    plan = plan_of(n, read(n))
    assert plan.kind == "notebook" and [p["pattern"] for p in plan.papers] == ["blank", "blank"]
    assert plan.warnings == ["A page names no paper: it is blank paper", "A page's PDF could not be read: it is blank paper"]


def test_a_turned_page_is_upright_and_counted():
    n = gx.Notebook("Turned")
    page = n.page(n.paper())
    n.event(63, (2, page), (3, gx.msg((1, 1))))
    assert plan_of(n, read(n)).left_out == Counter({"rotated page": 1})


# --- strokes --------------------------------------------------------------------------------

def test_a_ball_pen_is_an_even_ballpoint_half_its_width():
    [piece] = one_page(gx.ink("A", gx.flat([LINE], width=1.559))).ink
    stroke = gm.ink_stroke(piece, frame())
    assert (stroke["brush"], stroke["pen"], stroke["size"], stroke["ch"]) == ("monoline", False, 0.78, "xy")
    pts = samples(stroke)
    assert (pts[0]["x"], pts[0]["y"]) == pytest.approx((10 * gn.CANVAS_PT, 20 * gn.CANVAS_PT), abs=0.01)
    assert all(math.dist((a["x"], a["y"]), (b["x"], b["y"])) <= 1.1 for a, b in zip(pts, pts[1:]))
    end = (30 * gn.CANVAS_PT, 25 * gn.CANVAS_PT)
    assert (pts[-1]["x"], pts[-1]["y"]) == pytest.approx(end, abs=0.01)
    bigger = gm.ink_stroke(piece, frame(2 * gn.CANVAS_PT))  # a PDF page drawn larger: widths scale with it
    assert bigger["size"] == 1.559


def test_a_highlighter_keeps_its_alpha_and_width():
    [piece] = one_page(gx.ink("H", gx.flat([LINE], width=24.0), rgba=(1.0, 1.0, 0.2, 0.5), highlighter=True)).ink
    stroke = gm.ink_stroke(piece, frame())
    assert (stroke["tool"], stroke["opacity"], stroke["size"], stroke["color"]) == ("highlighter", 0.5, 12.0, "#ffff33")
    assert "brush" not in stroke and "pen" not in stroke


def test_a_variable_width_pen_keeps_its_widths():
    [piece] = one_page(gx.ink("R", gx.ribbon([[(0.0, 0.0, 1.0), (20.0, 0.0, 2.0), (40.0, 0.0, 0.5)]]), kind=1)).ink
    stroke = gm.ink_stroke(piece, frame())
    assert (stroke["ch"], stroke["size"]) == ("xyw", round(4.0 * gn.CANVAS_PT, 3))
    ws = [s["w"] for s in samples(stroke)]
    assert ws[0] == 0.5 and max(ws) == 1.0 and ws[-1] == 0.25
    [even] = one_page(gx.ink("E", gx.ribbon([[(0.0, 0.0, 1.0), (20.0, 0.0, 1.0), (40.0, 0.0, 1.0)]]), kind=1)).ink
    assert gm.ink_stroke(even, frame())["pen"] is False and gm.ink_stroke(even, frame())["ch"] == "xy"


def test_tape_is_an_opaque_band():
    [piece] = one_page(gx.ink("T", gx.ribbon([[(0.0, 9.0, 9.0), (5.0, 9.0, 9.0), (10.0, 9.0, 9.0)]], turned=True),
                              kind=1, tape=True, rgba=(0.925, 0.325, 0.294, 0.8))).ink
    stroke = gm.ink_stroke(piece, frame())
    assert (stroke["brush"], stroke["pen"], stroke["size"], "opacity" in stroke) == ("monoline", False, 9.818, False)


@pytest.mark.parametrize("row", [r for r in INKWIDTH["rows"] if r["brush"] == "pencil" and r["pen"]
                                 and r["a"] in (None, 90)], ids=lambda r: r["note"])
def test_the_pencil_law_is_the_brush_table_s(row):
    assert gm.pencil_law(row["p"]) == (row["w"], row["o"])


def test_a_pencil_with_force_is_shaded_and_one_without_is_even():
    [piece] = one_page(gx.ink("P", gx.pencil([(0.0, 0.0, 25 / 6), (5.0, 0.0, 25 / 12), (10.0, 0.0, 0.0)]),
                              kind=5)).ink
    stroke = gm.ink_stroke(piece, frame())
    assert (stroke["brush"], stroke["ch"], stroke["size"]) == ("pencil", "xypwo", 0.78)
    first = samples(stroke)[0]
    assert (first["p"], first["w"], first["o"]) == (1.0, 1.5, 1.0)
    [mouse] = one_page(gx.ink("M", gx.pencil([(0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0)]), kind=5)).ink
    assert (gm.ink_stroke(mouse, frame())["pen"], gm.ink_stroke(mouse, frame())["ch"]) == (False, "xy")


def test_a_shape_keeps_its_record_and_fill():
    page = one_page(gx.ink("R", shape=gx.rect_shape((110, 110), (110, 55), width=3.0)),
                    gx.fill("F", "R", (0.0, 0.0, 1.0, 0.1)))
    [stroke] = list(gm.page_strokes(gm.PagePlan(frame(), page), Counter()))
    assert stroke["shape"] == {"kind": "rect", "params": {"cx": 60.0, "cy": 60.0, "w": 60.0, "h": 30.0, "angle": 0}}
    assert stroke["fill"] == {"color": "#0000ff", "opacity": 0.1} and stroke["size"] == 1.5


def test_strokes_and_shapes_come_in_one_drawing_order_within_the_budget():
    page = one_page(gx.ink("A", gx.flat([LINE])), gx.ink("S", shape=gx.polyline_shape([(0, 0), (10, 0)])),
                    gx.ink("B", gx.flat([LINE])))
    strokes = list(gm.page_strokes(gm.PagePlan(frame(), page), Counter()))
    assert ["shape" in s for s in strokes] == [False, True, False]
    left, left_out = [5], Counter()
    assert len(list(gm.page_strokes(gm.PagePlan(frame(), page), left_out, left))) == 0
    assert left_out == Counter({"samples": 3})


def test_a_text_box_starts_where_goodnotes_frame_puts_it():
    n = gx.Notebook("Boxed")
    n.page(n.paper(), [gx.rtf_text("X", (100, 100, 220, 60), gx.COCOA)])
    [media] = read(n).pages[0].media
    box = gm.text_box(media, frame())
    k = gn.CANVAS_PT
    assert box["content"] == "Plain and **bold**"
    assert (box["text_box"]["x"], box["text_box"]["y"]) == pytest.approx((110 * k - 4, 110 * k - 4), abs=0.01)
    assert box["text_box"]["size"] == pytest.approx(24 * k, abs=0.01) and box["text_box"]["color"] == "#c80000"


# --- what a hostile or unusual notebook may cost ------------------------------------------------

def test_rtf_reads_characters_past_the_bmp_and_double_byte_code_pages():
    face = "".join(s.text for s in rtf.read_rtf(rb"{\rtf1\ansi\uc0\u55357 \u56832  smile}"))
    assert face == chr(0x1F600) + " smile"
    lone = "".join(s.text for s in rtf.read_rtf(rb"{\rtf1\ansi\uc0\u55357 alone}"))
    assert lone == chr(0xFFFD) + "alone"
    chinese = "".join(s.text for s in rtf.read_rtf(rb"{\rtf1\ansi\ansicpg936 \'c4\'e3\'ba\'c3}"))
    assert chinese == "你好"
    skipped = "".join(s.text for s in rtf.read_rtf(rb"{\rtf1\ansi\uc1\u8217\'92s}"))
    assert skipped == "’s"


def test_rtf_nesting_past_its_depth_is_passed_by_uncopied():
    deep = rb"{\rtf1 " + b"{" * 200_000 + b"lost" + b"}" * 200_000 + b" kept}"
    assert "".join(s.text for s in rtf.read_rtf(deep)) == " kept"


def test_a_date_past_year_9999_or_far_ahead_reads_as_none():
    n = gx.Notebook("Far ahead", created_ms=3.3e13)  # year 3015: past what Windows' fromtimestamp takes
    n.page(n.paper())
    n.recording(fx.m4a(1000), when_ms=1e20)
    notebook = read(n)
    assert notebook.created.year == 3015
    assert [r.created for r in notebook.recordings] == [None]
    assert gn._when(1e20) is None and gn._when(-5) is None


def test_a_shape_of_too_many_corners_is_left_out():
    many = [(float(i), 0.0) for i in range(gn.MAX_SHAPE_POINTS + 1)]
    page = one_page(gx.ink("S", shape=gx.polyline_shape(many)), gx.ink("A", gx.flat([LINE])))
    assert page.shapes == [] and [i.z for i in page.ink] == [1]


def test_a_geometry_of_a_format_not_known_is_refused_before_it_is_read():
    image = gx.tpl("A(S(cA(c)))", [("I", [3])])  # a count of three, and no values after it
    with pytest.raises(gc.CodecError, match="does not know"):
        gc.tpl(image, gn.KNOWN_FORMATS)
    with pytest.raises(gc.CodecError, match="longer"):
        gc.tpl(image)


def test_a_page_s_points_and_a_notebook_s_geometry_are_budgeted(monkeypatch):
    monkeypatch.setattr(gn, "MAX_PAGE_POINTS", 7)
    n = gx.Notebook("Dense")
    n.page(n.paper(), [gx.ink("A", gx.flat([LINE])), gx.ink("B", gx.flat([LINE]))])
    [bundle] = bundles(n.bytes())
    notebook = gn.read_notebook(bundle.members, path=bundle.path, ink=False)
    gn.read_page(notebook, notebook.pages[0], bundle.members, gn.Budget())
    assert len(notebook.pages[0].ink) == 1 and notebook.left_out == Counter({"page points": 1})
    monkeypatch.setattr(gn, "MAX_PAGE_POINTS", 1000)
    notebook = gn.read_notebook(bundle.members, path=bundle.path, ink=False)
    one = len(gx.flat([LINE]))  # a stroke's geometry unpacked: the first fits, the second does not
    gn.read_page(notebook, notebook.pages[0], bundle.members, gn.Budget(geometry=one + 10))
    assert len(notebook.pages[0].ink) == 1 and notebook.left_out == Counter({"geometry": 1})


def test_the_page_index_is_read_no_further_than_its_cap(monkeypatch):
    monkeypatch.setattr(gn, "MAX_INDEX", 2)
    n = gx.Notebook("Long index")
    for _ in range(4):
        n.page(n.paper())
    notebook = read(n, ink=False)
    assert len(notebook.pages) == 2 and notebook.left_out == Counter({"index": 1})


def test_a_dash_shorter_than_a_tenth_of_the_width_is_dotted():
    page = one_page(gx.ink("D", gx.flat([LINE]), dash=[0.05, 2.0]), gx.ink("E", gx.flat([LINE]), dash=[0.5, 2.0]))
    assert [i.dash for i in page.ink] == ["dotted", "dashed"]


def test_a_text_box_s_fill_is_its_background():
    n = gx.Notebook("Filled box")
    n.page(n.paper(), [gx.text("F", (100, 100), (220, 60), [("On yellow", None, None, False)],
                               fill=(1.0, 0.95, 0.5, 1.0)),
                       gx.text("T", (100, 300), (220, 60), [("Clear", None, None, False)], fill=(1.0, 1.0, 1.0, 0.0))])
    filled, clear = read(n).pages[0].media
    assert filled.background == pytest.approx((1.0, 0.95, 0.5, 1.0)) and clear.background is None
