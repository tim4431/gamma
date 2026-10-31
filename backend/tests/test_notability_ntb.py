"""The .ntb reader (gamma/notability_ntb.py) on bundles the test writer
makes (ntb_fixtures.py): the document's page and paper, ink and the pieces
the eraser left, shapes, groups and the drawing order, pictures (their
scale, flips, crop and caption), sticky notes and text boxes, typed text,
its owner and its styles, the log's edits and moves, deletes and restores
and versions, a PDF's pages, and damage read as a count of what was left
out (docs/dev/import_export.md "Notability notes")."""

import contextlib
import io
import math
import zipfile
from collections import Counter

import pytest

import ntb_fixtures as tx
from gamma import notability as nb, notability_map as nm, notability_ntb as ntb
from gamma.import_zip import new_walk, walk_zip

W, H = 679.0, 891.1875
INSET = W * 20 / 768
DIGEST = bytes(range(64))
PNG = f"{DIGEST.hex()}.png"


def read(*records, doc=None, assets=None, pdf_pages=None, ink=True) -> nb.Note:
    """The note of a bundle of ``records`` after its document record."""
    data = tx.ntb_zip(tx.bundle((1, tx.DOCUMENT, doc or tx.document()), *records), assets)
    with contextlib.ExitStack() as stack:
        entries = []
        walk_zip(stack.enter_context(zipfile.ZipFile(io.BytesIO(data))), "", entries, new_walk(stack),
                 nested=lambda _path: False)
        members = nb.note_members(entries)
        return ntb.read_ntb(members, members.find("noteBundle").read(), path="Lecture.ntb", digest="d", ink=ink,
                            pdf_pages=pdf_pages)


def points(curve) -> list[tuple]:
    return [(round(x, 3), round(y, 3)) for x, y in nb.curve_points(curve)]


def test_the_document_s_page_paper_and_title():
    note = read()
    assert (note.title, note.width, note.page_height, note.created.year) == ("Lecture", W, H, 2026)
    assert (note.paper.legacy, note.paper.ruling, note.paper.size) == (13, "ruled", "letter")
    assert note.paper.lines == pytest.approx(0.25, abs=1e-4) and nm.paper_page(note)[2] == H  # 19.97 of 679 ÷ 8.5
    width = 595.28
    a4 = read(doc=tx.document("", width, width * 841.89 / 595.28, pattern=2, spacing=width / 8.5 / 2, legacy=None,
                              color=(250, 240, 230, 255)))
    assert (a4.title, a4.paper.size, a4.paper.ruling, a4.paper.color) == ("Lecture", "a4", "grid", "#faf0e6")
    sheet = nm.paper(a4)
    assert (sheet["pattern"], sheet["width"], sheet["spacing"]) == ("grid", 595.28, pytest.approx(36 * 595.28 / 612,
                                                                                                   abs=0.01))
    dots = read(doc=tx.document(pattern=1, spacing=None))
    assert (dots.paper.ruling, dots.paper.lines) == ("dots", 0.25)  # no spacing: the app's quarter inch
    assert read(doc=tx.document(pattern=None)).paper.lines is None


def test_ink_its_pieces_nodes_and_place():
    """A cubic path's pieces (the eraser's breaks), its nodes per anchor,
    scaled and turned about its origin and placed on its page; a linear
    path's polylines; the tool, line style and colour."""
    nodes = [(0.5, 2.0, 128, 64), (1.0, 1.0, 255, 0), (1.5, 1.0, 255, 0)]
    stroke = tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)], [(10, 0)], nodes=nodes)
    note = read((15, (0, 10), tx.ink(stroke, page=1, origin=(100, 50), scale=(2, 2), tool=1, line=3, width=1.5,
                                     color=(9, 8, 7, 255))),
                (15, (0, 11), tx.ink(tx.linear([(0, 0), (5, 5)], [(8, 8), (9, 9)]), origin=(100, 100), tool=2,
                                     color=(250, 157, 0, 107))),
                (15, (0, 12), tx.ink(tx.cubic([(0, 0), (0, 1), (0, 2), (10, 0)]), origin=(100, 100),
                                     rotation=math.pi / 2)))
    first, dot, line, cut, turned = note.curves
    x0 = 100 - INSET
    assert points(first) == [(round(x0 + 2 * i, 3), round(H + 50, 3)) for i in range(4)] and first.bezier
    assert (first.anchors, first.base, first.style, first.dash, first.rgba) == (2, 3.0, nb.STYLE_PENCIL, "dotted",
                                                                                 (9, 8, 7, 255))
    ink = first.ink
    assert (list(ink.fractional[0:2]), list(ink.force[0:2])) == ([0.5, 1.0], [2.0, 1.0])
    assert ink.altitude[0] == pytest.approx(128 / 255 * math.pi / 2) and ink.azimuth[0:2].tolist() == pytest.approx(
        [0.0, 1.0], abs=1e-6)  # 64 of 256: a quarter turn
    assert points(dot) == [(round(x0 + 20, 3), round(H + 50, 3))] and ink.fractional[dot.f] == 1.5
    assert (line.bezier, line.anchors, line.style, cut.n) == (False, 2, nb.STYLE_HIGHLIGHTER, 2)
    assert points(cut)[0] == (round(x0 + 8, 3), 108.0) and ink.fractional[line.f:line.f + 2].tolist() == [1.0, 1.0]
    assert points(turned)[-1] == pytest.approx((x0, 110.0), abs=1e-3)  # (10, 0) a quarter turn about the origin


def test_shapes_by_kind():
    """Lines (a start, a control point, an end), polygons named by their
    corners, ellipses in their box as a .note's corners, about the origin;
    an unknown kind is counted."""
    note = read((18, (0, 20), tx.shape("line", end=(40, 0), start=(0, 10), control=(20, 30), tool=2, dash=2)),
                (18, (0, 21), tx.shape("polygon", points=[(0, 0), (30, 0), (15, 20), (0, 0)])),
                (18, (0, 22), tx.shape("polygon", points=[(0, 0), (30, 0), (30, 20), (0, 20)], tool=1)),
                (18, (0, 23), tx.shape("polygon", points=[(0, 0), (30, 0), (40, 20), (10, 30), (0, 10)], dash=3)),
                (18, (0, 24), tx.shape("ellipse", size=(40, 20), page=1, rotation=math.pi)),
                (18, (0, 25), tx.shape("line", end=(1, 1), color=(10, 20, 30, 128), width=3.0)))
    line, triangle, rectangle, polygon, ellipse, thin = note.shapes
    x0 = 100 - INSET
    assert line.points == [(x0, 110.0), (x0 + 40, 100.0)] and line.control == (x0 + 20, 130.0)
    assert (line.kind, line.style, line.dash, line.grouped) == ("line", nb.STYLE_HIGHLIGHTER, "dashed", False)
    assert [s.kind for s in (triangle, rectangle, polygon)] == ["triangle", "rectangle", "polygon"]
    assert (len(triangle.points), rectangle.style, polygon.dash) == (3, nb.STYLE_PENCIL, "dotted")
    corners = [(x0, H + 80), (x0 - 40, H + 80), (x0 - 40, H + 100), (x0, H + 100)]  # turned half about the origin
    assert [v for p in ellipse.points for v in p] == pytest.approx([v for p in corners for v in p])
    assert thin.rgba == pytest.approx((10 / 255, 20 / 255, 30 / 255, 128 / 255)) and thin.width == 3.0
    rect = nm.shape_stroke(rectangle, nm.Frame(0, 1e4, -INSET, 0, 1.0, 612, 792))
    assert rect["shape"]["kind"] == "rect" and rect["brush"] == "pencil"
    odd = tx.shape("line", end=(1, 1))
    odd.slots[4] = tx.u8(9)
    assert read((18, (0, 30), odd)).left_out == Counter({"shape": 1})


def test_groups_and_the_drawing_order():
    """Strokes and shapes are drawn in the order of their records, a group's
    members together in the place of its first; a group's shapes are
    plain strokes, as a .note's."""
    path = tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)])
    note = read((15, (0, 40), tx.ink(path, width=1.0)), (18, (0, 41), tx.shape("line", end=(5, 5), width=2.0)),
                (15, (0, 42), tx.ink(path, width=3.0)), (18, (0, 43), tx.shape("line", end=(5, 5), width=4.0)),
                (18, (0, 44), tx.shape("line", end=(5, 5), width=5.0)), (20, (0, 45), tx.ids((0, 42), (0, 40), (0, 44))))
    drawn = sorted(note.curves + note.shapes, key=lambda item: item.z)
    assert [item.base if isinstance(item, nb.Curve) else item.width for item in drawn] == [1.0, 3.0, 5.0, 2.0, 4.0]
    assert [(s.width, s.grouped) for s in note.shapes] == [(5.0, True), (2.0, False), (4.0, False)]


def test_pictures_stickies_and_text_boxes():
    """A picture's frame from its turned top-left corner, its asset by its
    content hash, its crop in the asset's logical size; a sticky's colour;
    a text box is a media record whose id holds text; what is no picture,
    or past the page cap, is counted."""
    turn = 0.5
    corner = tx.turned_corner(200, 150, 80, 60, turn)
    note = read((22, (0, 50), tx.picture(DIGEST, corner=corner, size=(80, 60), rotation=turn, crop=(0, 0, 50, 40),
                                          logical=(100, 80), key=7)),
                (22, (0, 51), tx.picture(bytes(64), corner=(10, 10), page=1)),
                (22, (0, 52), tx.sticky(color=(10, 200, 30, 255))),
                (22, (0, 53), tx.box(corner=(60, 400))), (8, (0, 54), tx.text("In a box", owner=(0, 53))),
                (22, (0, 55), tx.picture(DIGEST, mime="audio/mp4")),
                assets={PNG: b"\x89PNG"})
    lost, sticky, box, picture = note.media  # by their sort keys: the picture's is 7, the others' their record's
    assert (picture.kind, picture.picture, picture.crop, picture.crop_size) == ("picture", f"assets/{PNG}",
                                                                                (0, 0, 50, 40), (100, 80))
    assert (picture.x, picture.y, picture.rotation) == (pytest.approx(200 - INSET), pytest.approx(150), turn)
    assert (lost.picture, lost.y) == (None, pytest.approx(H + 10)) and note.missing == [f"assets/{'00' * 64}.png"]
    assert (sticky.kind, sticky.background) == ("sticky", (10 / 255, 200 / 255, 30 / 255, 1.0))
    assert (box.kind, [r.text for r in box.runs]) == ("text", ["In a box"]) and note.flow == []
    assert (note.left_out, note.classes) == (Counter({"media": 1}), Counter({"audio/mp4": 1}))


def test_a_picture_s_scale_flips_and_crop():
    """A picture is shown at its size times its scale, its stored corner
    turned back to the scaled frame's; its flips are read, and its crop,
    measured on the image as flipped, mirrored onto the image as stored;
    a scale that leaves no frame is counted."""
    turn = 0.5
    corner = tx.turned_corner(200, 150, 200, 100, turn)
    note = read((22, (0, 50), tx.picture(DIGEST, corner=corner, size=(100, 50), scale=(2, 2), rotation=turn, key=1,
                                          crop=(10, 20, 100, 50), logical=(400, 300), flip_h=True, flip_v=True)),
                (22, (0, 51), tx.picture(DIGEST, crop=(10, 20, 100, 50), logical=(400, 300), flip_h=True, key=2)),
                (22, (0, 52), tx.picture(DIGEST, scale=(0, 1))),
                assets={PNG: b"\x89PNG"})
    both, mirrored = note.media
    assert (both.x, both.y, both.w, both.h) == (pytest.approx(200 - INSET), pytest.approx(150), 200, 100)
    assert (both.flip_h, both.flip_v, both.crop) == (True, True, (290, 230, 100, 50))  # 400 - 10 - 100, 300 - 20 - 50
    assert (mirrored.flip_h, mirrored.flip_v, mirrored.crop) == (True, False, (290, 20, 100, 50))
    assert note.left_out == Counter({"object": 1})


def test_a_picture_s_text_is_its_caption():
    """The text a picture holds (its text field names the picture) is its
    caption, plain with its line breaks, when its record or a later edit
    shows one; shown or not, it is no text box and no lost text. An image
    block without its asset is a picture that cannot be read."""
    image = tx.box()
    image.slots[0] = tx.u8(1)  # the block's type: an image
    note = read((22, (0, 80), tx.picture(DIGEST, caption=True, key=1)),
                (8, (0, 81), tx.text("Two\nlines ", owner=(0, 80))),
                (22, (0, 82), tx.picture(DIGEST, key=2)), (8, (0, 83), tx.text("Hidden", owner=(0, 82))),
                (22, (0, 84), tx.picture(DIGEST, key=3)), (8, (0, 85), tx.text("Shown later", owner=(0, 84))),
                (23, (0, 86), tx.edit((0, 84), caption=True)),
                (22, (0, 87), image), (8, (0, 88), tx.text("No image", owner=(0, 87))),
                (8, (0, 89), tx.text("Body")), assets={PNG: b"\x89PNG"})
    assert [(m.kind, m.caption) for m in note.media] == [("picture", "Two\nlines"), ("picture", ""),
                                                         ("picture", "Shown later")]
    assert [r.text for r in note.flow] == ["Body"] and note.left_out == Counter({"object": 1})


@pytest.mark.parametrize("empty", [2, 4])
def test_edits_and_moves_of_blocks(monkeypatch, empty):
    """The log's edits (23) and moves (24) change the blocks they name, in
    their order: what one does not hold stays as it was, and a turn, scale
    or crop table holding none clears it, its vtable its own size alone (as
    Notability's iPad app writes an empty table) or with its table's too; a
    lock or a corner style changes nothing and is not counted. A move of
    ink is not applied, and said once a record; a change of ink (17) is a
    record of a kind not read."""
    monkeypatch.setattr(tx, "EMPTY_VTABLE", empty)
    path = tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)])
    note = read((22, (0, 50), tx.picture(DIGEST, corner=(10, 10), size=(80, 60), rotation=0.3, logical=(400, 300))),
                (22, (0, 51), tx.box(size=(200, 40))), (8, (0, 52), tx.text("Box", owner=(0, 51))),
                (22, (0, 53), tx.sticky()), (15, (0, 54), tx.ink(path, origin=(100, 100))),
                (22, (0, 55), tx.picture(DIGEST, crop=(0, 0, 10, 10), logical=(400, 300))),
                (24, (0, 60), tx.move(tx.position((0, 50), page=1, origin=(300, 30), scale=(2, 1), key=9),
                                      tx.position((0, 54), origin=(5, 5)))),
                (23, (0, 61), tx.edit((0, 50), crop=(0, 10, 100, 200), flip_h=True, flip_v=True, rotation=(),
                                      key=1)),
                (23, (0, 62), tx.edit((0, 51), (0, 53), size=(50, 20))),
                (23, (0, 63), tx.edit((0, 53), page=1, corner=(400, 20), color=(1, 2, 3, 255))),
                (24, (0, 64), tx.move(tx.position((0, 51), origin=(70, 80), rotation=0.25, key=4),
                                      tx.position((0, 53), scale=(2, 2)))),
                (23, (0, 65), tx.edit((0, 50), locked=True, rounded=1)), (23, (0, 66), tx.edit((0, 53), scale=())),
                (17, (0, 67), tx.Table(s0=tx.u8(1))), (23, (0, 68), tx.edit((0, 55), crop=())),
                assets={PNG: b"\x89PNG"})
    picture, box, sticky, cleared = note.media  # the picture's key is 1, the box's 4, the others' their record's
    assert (picture.x, picture.y, picture.w, picture.h, picture.rotation) == (300 - INSET, H + 30, 160, 60, 0.0)
    assert (picture.flip_h, picture.flip_v, picture.crop) == (True, True, (300, 90, 100, 200))  # y: 300 - 10 - 200
    assert (box.w, box.h, box.rotation, box.z, [r.text for r in box.runs]) == (50, 20, 0.25, 4, ["Box"])
    assert tx.turned_corner(box.x + INSET, box.y, 50, 20, 0.25) == pytest.approx((70, 80))
    assert (sticky.x, sticky.y, sticky.w, sticky.h) == (400 - INSET, H + 20, 50, 20)
    assert sticky.background == (1 / 255, 2 / 255, 3 / 255, 1.0)
    assert (cleared.kind, cleared.crop) == ("picture", None)
    assert nb.curve_points(note.curves[0])[0] == pytest.approx((100 - INSET, 100))  # where it was drawn
    assert note.left_out == Counter({"ink edit": 1, "record": 1})
    assert "1 later move, turn or resize of handwriting is left out: it comes in where it was drawn" in (
        nb.left_out_reasons(note.left_out))


def test_a_text_s_owner_is_its_text_field():
    """A text record belongs to the block its text field names, else to
    the document: where it was inserted (a position) names no owner."""
    note = read((8, (0, 90), tx.text("Typed ")), (8, (0, 91), tx.text("on", at=((0, 90), 6))))
    assert [r.text for r in note.flow] == ["Typed on"] and not note.left_out


def test_typed_text_its_styles_and_lists():
    """The document's text records in order; character styles over the
    range between two positions, an end anchor holding its last character;
    paragraph styles from after the newline that opens them; lists
    numbered per level as Notability numbers them; offsets in UTF-16
    units, an emoji two of them."""
    start = ((0, 60), 0)
    note = read((8, (0, 60), tx.text("😀 Title\nFirst\n")), (8, (0, 61), tx.text("Second\nThird\nplain")),
                (7, (0, 62), tx.anchor((0, 60), 7)),  # the "e" of Title: the anchor marks the last character
                (12, (0, 63), tx.style(((0, 60), 3), ((0, 62), 0), bold=True, size=20.0)),
                (13, (0, 64), tx.paragraph(((0, 60), 8), ((0, 61), 7), decoration=2)),
                (13, (0, 65), tx.paragraph(((0, 61), 6), ((0, 61), 12), decoration=2, level=1)),
                (12, (0, 66), tx.style(start, ((0, 61), 99), italic=True)))
    blocks = nm.flow_blocks(note.flow)
    assert [b["content"] for b in blocks] == ["*😀* ***Title***", "1. *First*", "2. *Second*", "*plain*"]
    assert [c["content"] for c in blocks[2]["children"]] == ["1. *Third*"]
    title = next(r for r in note.flow if r.text == "Title")
    assert (title.bold, title.size) == (True, 20.0)


def test_a_struck_run_is_not_a_superscript_one():
    """A character style holds strikethrough in slot 13, as Notability's
    schema has it; slot 11, superscript, is not read."""
    superscript = tx.style(((0, 60), 0), ((0, 60), 2))
    superscript.slots[11] = tx.Table(s0=tx.u8(1))
    note = read((8, (0, 60), tx.text("x2 gone")), (12, (0, 61), superscript),
                (12, (0, 62), tx.style(((0, 60), 3), ((0, 60), 7), strike=True)))
    assert [(r.text, r.strike) for r in note.flow] == [("x2 ", False), ("gone", True)]


def test_erased_records_and_versions():
    """An erase record's records are gone, a text box with the text it
    holds; a record given again is its last version, in the place of its
    first."""
    path = tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)])
    note = read((15, (0, 70), tx.ink(path, color=(1, 1, 1, 255))), (15, (0, 71), tx.ink(path, color=(2, 2, 2, 255))),
                (15, (0, 72), tx.ink(path, color=(3, 3, 3, 255))),
                (22, (0, 73), tx.box()), (8, (0, 74), tx.text("Gone", owner=(0, 73))),
                (15, (0, 70), tx.ink(path, color=(9, 9, 9, 255))),
                (25, (0, 75), tx.erase((0, 72), (0, 73))))
    assert [c.rgba[0] for c in sorted(note.curves, key=lambda c: c.z)] == [9, 2]
    assert note.media == [] and not note.left_out


def test_deletes_and_restores_in_their_order():
    """Erase records delete and restore records in their order: one deleted
    at the end is gone, one restored is back with the text it holds. A
    page deleted at the end is not taken out, but said to be."""
    note = read((22, (0, 70), tx.sticky()), (22, (0, 71), tx.box()), (8, (0, 72), tx.text("Back", owner=(0, 71))),
                (22, (0, 73), tx.sticky()),
                (25, (0, 74), tx.erase((0, 70), (0, 71), (0, 73), pages=(1, 2))),
                (25, (0, 75), tx.erase(restored=((0, 70), (0, 71)), restored_pages=(2,))),
                (25, (0, 76), tx.erase((0, 70))))
    box, = note.media
    assert [r.text for r in box.runs] == ["Back"] and note.left_out == Counter({"deleted page": 1})
    assert nb.left_out_reasons(note.left_out) == ["1 page deleted in Notability is not taken out"]


def test_a_pdf_s_pages_come_first():
    """The bundle's one PDF is the note's first pages, all of them in
    order, an item on one placed in its slot as the frames give it; past
    them, plain pages. Two PDFs are paper, with a warning."""
    pdf = f"{'ab' * 32}.pdf"
    stroke = tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)])
    records = [(15, (0, 80), tx.ink(stroke, page=1, origin=(100, 20))), (15, (0, 81), tx.ink(stroke, page=2))]
    note = read(*records, assets={pdf: b"%PDF-1.4"}, pdf_pages={pdf: [(612, 792), (612, 396)]})
    assert [(e.pdf, e.page) for e in note.layout] == [(pdf, 1), (pdf, 2)] and note.pdfs[pdf].version == 2
    plan = nm.plan_note(note, {pdf: [(612, 792), (612, 396)]})
    second = plan.pages[1].frame
    on_second, past = note.curves
    assert plan.pages[1].curves == [0] and plan.pages[2].curves == [1] and plan.pages[2].frame.pdf is None
    assert nb.curve_points(on_second)[0][1] == pytest.approx(second.oy + 20)  # the PDF's top, not its slot's
    assert nb.curve_points(past)[0][1] == pytest.approx(second.bottom + 100)
    preview = read(*records, assets={pdf: b"%PDF-1.4"}, ink=False)
    assert [(e.pdf, e.page) for e in preview.layout] == [(pdf, 1)] and preview.curves == []
    two = read(*records, assets={pdf: b"%PDF", f"{'cd' * 32}.pdf": b"%PDF"})
    assert two.layout == [] and two.warnings == ["The note holds 2 PDFs and not where their pages go: "
                                                 "its pages are blank paper"]


def test_damage_is_counted_not_raised(monkeypatch):
    """No root, no records: no note. A path that does not read, a record of
    a kind the reader does not know, text with no owner, a page past the
    cap: each left out and counted, the rest read."""
    members = nb.Members({})
    for data in (b"", b"\x10\x00\x00\x00", tx.dumps(tx.Table(s4=tx.u64(1)))):
        with pytest.raises(nb.NoteError):
            ntb.read_ntb(members, data, path="x.ntb")
    broken = bytearray(tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)]))
    broken[4 + 4 + 6] = 9  # the first command: no such
    note = read((15, (0, 90), tx.ink(bytes(broken))), (15, (0, 91), tx.ink(b"\x02" + bytes(broken[1:]))),
                (15, (0, 92), tx.ink(tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)])[:-3])),
                (15, (0, 93), tx.ink(tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)]), page=nb.MAX_NOTE_PAGES)),
                (15, (0, 94), tx.ink(tx.cubic([(0, 0), (math.inf, 0), (2, 0), (3, 0)]))),
                (99, (0, 95), tx.Table(s0=tx.u8(1))), (8, (0, 96), tx.text("Orphan", owner=(7, 7))),
                (15, (0, 97), tx.ink(tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)]))))
    assert note.left_out == Counter({"stroke": 4, "page": 1, "record": 1, "lost text": 1}) and len(note.curves) == 1
    assert {"4 strokes could not be read and were left out", "Past 5,000 pages the rest is left out (1 item)",
            "1 record of a kind this import does not know is left out",
            "1 text that belongs to no page or box is left out"} == set(nb.left_out_reasons(note.left_out))
    monkeypatch.setattr(nb, "MAX_RECORDS", 3)
    capped = read(*[(15, (0, 100 + i), tx.ink(tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)]))) for i in range(4)])
    assert capped.left_out["records"] == 3 and len(capped.curves) == 1  # the page list and the document come first


def test_the_note_s_caps(monkeypatch):
    monkeypatch.setattr(nb, "MAX_CURVES", 2)
    monkeypatch.setattr(nb, "MAX_SHAPES", 1)
    monkeypatch.setattr(nb, "MAX_NOTE_TEXT", 6)
    path = tx.cubic([(0, 0), (1, 0), (2, 0), (3, 0)], [(5, 5)], [(9, 9)])
    note = read((15, (0, 110), tx.ink(path)), (18, (0, 111), tx.shape("line", end=(1, 1))),
                (18, (0, 112), tx.shape("line", end=(2, 2))), (8, (0, 113), tx.text("Words and more")))
    assert (len(note.curves), len(note.shapes), [r.text for r in note.flow]) == (2, 1, ["Words "])
    assert note.left_out == Counter({"curves": 1, "shapes limit": 1, "typed text": 1})


def test_the_bundle_s_vectors_list_a_bounded_number_in_all(monkeypatch):
    """Any number of records may lead to one vector, so the ids and tables
    the bundle's vectors list are MAX_LISTED in all: past it, an edit or a
    move lists nothing."""
    monkeypatch.setattr(ntb, "MAX_LISTED", 6 + 1)  # the page list, the document and four records, then one id
    sticky, = read((22, (0, 70), tx.sticky()), (23, (0, 71), tx.edit((0, 70), size=(50, 20))),
                   (23, (0, 72), tx.edit((0, 70), size=(70, 30))),
                   (24, (0, 73), tx.move(tx.position((0, 70), origin=(10, 10))))).media
    assert (sticky.y, sticky.w, sticky.h) == (300, 50, 20)
