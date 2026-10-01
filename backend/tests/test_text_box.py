"""Text boxes in PDFs (gamma/text_box.py, docs/dev/text_boxes.md): the
/FreeText the annotated export writes (keys, place on turned and cropped
pages, the appearance), its lossless re-import, the /FreeText and /Text
notes other apps embed, and a sheet's boxes as real text in the notebook
PDF. Normalization itself is pinned by tests/shared/textbox.json."""

import io
import json

import pytest
from conftest import make_page, require_math_renderer
from fractional_indexing import generate_key_between
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, RectangleObject,
                            TextStringObject)

from test_pdf_export import _page_text

from gamma import notebook, text_box
from gamma.pdf_export import annotate_pdf, highlight_note_text, page_frame, zotero_annot_key
from gamma.routers.imports import _extract_pdf_annotations

PAGE_W, PAGE_H = 612, 792
CROP = (30, 40, 580, 760)  # 550 × 720, off the media box's origin


def _pdf(rotate=0, crop=None, annots=()):
    w = PdfWriter()
    w.add_blank_page(width=PAGE_W, height=PAGE_H)
    page = w.pages[0]
    if crop:
        page[NameObject("/CropBox")] = RectangleObject(crop)
    if rotate:
        page.rotate(rotate)
    for annot in annots:
        w.add_annotation(page_number=0, annotation=annot)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _tb(content="Hello box", bid="tb1", **box):
    return {"box": text_box.normalize_text_box({"x": 100, "y": 50, **box}), "content": content,
            "page": 1, "id": bid, "modified": "2026-09-29T12:34:56.789000Z"}


def _annots(pdf_bytes):
    return [a.get_object() for a in PdfReader(io.BytesIO(pdf_bytes)).pages[0].get("/Annots") or []]


def _free_texts(pdf_bytes):
    return [a for a in _annots(pdf_bytes) if a["/Subtype"] == "/FreeText"]


def _imported(pdf_bytes):
    """The records the import makes of a PDF's annotations."""
    return _extract_pdf_annotations(PdfReader(io.BytesIO(pdf_bytes)))


def _reimported(pdf_bytes, edit):
    """``_imported`` after ``edit(annot)`` changed the first annotation, as
    another viewer would."""
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(pdf_bytes)))
    edit(writer.pages[0]["/Annots"][0].get_object())
    buf = io.BytesIO()
    writer.write(buf)
    return _imported(buf.getvalue())


def _dark_box(pdf_bytes):
    """(left, top, right, bottom) in display points of the dark pixels a
    render with annotations paints, the page turned as a viewer shows it."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        bitmap = page.render(scale=1, draw_annots=True)
        buf, width, height = bytes(bitmap.buffer), bitmap.width, bitmap.height
        stride, n = bitmap.stride, bitmap.n_channels
        bitmap.close()
        page.close()
    finally:
        doc.close()
    xs, ys = [], []
    for y in range(height):
        row = buf[y * stride:y * stride + width * n]
        for x in range(width):
            if max(row[x * n:x * n + min(n, 3)]) < 128:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def test_a_box_is_not_an_object_to_normalize():
    assert text_box.normalize_text_box("x") is None and not text_box.is_text_box({"text_box": []})


def test_the_export_writes_a_free_text_with_its_keys():
    md = "**Bold** and $x^2$\n- item"
    tb = _tb(md, bg="#FFF4B8", color="#c0392b", size=16)
    out, written = annotate_pdf(_pdf(), [], author="tester", text_boxes=[tb])
    assert written == 1
    [a] = _free_texts(out)
    assert a["/IT"] == "/FreeText" and a["/F"] == 4 and a["/BS"]["/W"] == 0
    assert a["/Contents"] == "Bold and $x^2$\n• item"      # plain text, the marks dropped
    assert a["/DA"] == "/Helv 16 Tf 0.7529 0.2235 0.1686 rg"
    assert [float(v) for v in a["/C"]] == pytest.approx([1, 0xF4 / 255, 0xB8 / 255], abs=1e-4)
    assert a["/T"] == "tester" and a["/M"] == "D:20260929123456Z"
    assert a["/NM"] == f"Zotero-{zotero_annot_key('tb1')}" and "/Rotate" not in a
    form = a["/AP"]["/N"].get_object()
    assert form["/Subtype"] == "/Form" and [float(v) for v in form["/Matrix"]] == [1, 0, 0, 1, 0, 0]
    fonts = [f.get_object() for f in form["/Resources"]["/Font"].values()]
    assert {"/Helvetica-Bold", "/Type3"} <= {str(f.get("/BaseFont") or f["/Subtype"]) for f in fonts}
    require_math_renderer()
    assert b"0.753 0.224 0.169 rg" in form.get_data()        # the typeset math takes the box's colour
    assert json.loads(a["/GammaTextBox"]) == {"v": 1, "md": md, "box": tb["box"], "text": a["/Contents"]}
    # no fill without a background, no author without one
    [plain] = _free_texts(annotate_pdf(_pdf(), [], text_boxes=[_tb()])[0])
    assert "/C" not in plain and "/T" not in plain


@pytest.mark.parametrize("rotation, rect, matrix", [
    (0, (130, 687, 330, 710), (1, 0, 0, 1, 0, 0)),
    (90, (80, 140, 103, 340), (0, 1, -1, 0, 23, 0)),
    (180, (280, 90, 480, 113), (-1, 0, 0, -1, 200, 23)),
    (270, (507, 460, 530, 660), (0, -1, 1, 0, 0, 200)),
])
def test_the_rect_follows_the_crop_box_and_the_rotation(rotation, rect, matrix):
    """The box (100, 50) 200 × 23 in the display frame of a page cropped to
    550 × 720 at (30, 40): its user-space /Rect, and a /Matrix that turns
    the appearance back so it reads upright."""
    out, _ = annotate_pdf(_pdf(rotation, CROP), [], text_boxes=[_tb()])
    [a] = _free_texts(out)
    assert [float(v) for v in a["/Rect"]] == pytest.approx(rect, abs=0.001)
    form = a["/AP"]["/N"].get_object()
    assert [float(v) for v in form["/BBox"]] == [0, 0, 200, 23]
    assert [float(v) for v in form["/Matrix"]] == list(matrix)
    assert a.get("/Rotate", 0) == rotation


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_appearance_reads_upright_at_the_top_left_of_the_box(rotation):
    """Rendered as a viewer shows the turned page, one line of text sits
    across the top of its (taller) box, starting at the left padding."""
    tb = _tb("HELLO", size=24, w=300, h=100, auto=False)
    left, top, right, bottom = _dark_box(annotate_pdf(_pdf(rotation, CROP), [], text_boxes=[tb])[0])
    assert 100 + text_box.PAD <= left <= 100 + text_box.PAD + 3
    assert 50 + text_box.PAD <= top and bottom <= 50 + text_box.PAD + 24 * text_box.LINE
    assert right - left > 3 * (bottom - top)


def test_the_appearance_grows_down_when_the_text_needs_more_room():
    tb = _tb("A narrow fixed box whose text wraps onto many lines", w=80, h=20, auto=False)
    out, _ = annotate_pdf(_pdf(), [], text_boxes=[tb])
    [a] = _free_texts(out)
    x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
    assert x1 - x0 == pytest.approx(80) and y1 - y0 > 60
    assert float(a["/AP"]["/N"].get_object()["/BBox"][3]) == pytest.approx(y1 - y0, abs=0.01)
    assert y1 == pytest.approx(PAGE_H - 50)                  # the top stays put
    _left, _top, _right, bottom = _dark_box(out)
    assert bottom > 50 + 60


def test_a_list_is_laid_out_as_the_screen_draws_it():
    """As CSS draws a list (frontend/src/markup/markup.css): each level's
    text 1.5 em in, its wrapped lines under it, the marker (a to-do's box
    too) hanging in the indent and ending where the text starts."""
    from gamma.pdf_typeset import span_width

    size = 10
    box = text_box.normalize_text_box({"w": 130, "auto": False, "size": size})
    md = "- one two three four five six seven\n  - nested\n- [ ] to do"
    rows, _w, _h = text_box._layout(md, box, 612)
    lines = [(x, "".join(p for _k, p, *_ in spans)) for _kind, x, _base, spans, _fs in rows]
    column, nested = text_box.PAD + 1.5 * size, text_box.PAD + 3 * size
    marker, first, wrapped, sub_marker, sub, todo_box, todo = lines
    assert marker[1] == "• " and marker[0] + span_width("• ", size) == pytest.approx(column)
    assert first[0] == pytest.approx(column) and wrapped[0] == pytest.approx(column)
    assert first[1].startswith("one") and wrapped[1]
    assert sub_marker[0] + span_width("• ", size) == pytest.approx(nested) and sub == (pytest.approx(nested), "nested")
    assert todo_box[1] == "[ ] " and todo_box[0] + span_width("[ ] ", size) == pytest.approx(column)
    assert todo == (pytest.approx(column), "to do")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_export_then_import_restores_every_box(rotation):
    boxes = [
        _tb("Plain *auto* box", bid="a"),
        _tb("# Head\nA fixed box that grows past its height", bid="b",
            x=210.37, y=333.33, w=90.5, h=12.5, auto=False, size=10.5, color="#1d4ed8", bg="#dcecff"),
        _tb("中文 and $\\frac{a}{b}$", bid="c", x=0, y=0, size=36, bg="#ffffff"),
    ]
    out, written = annotate_pdf(_pdf(rotation, CROP), [], text_boxes=boxes)
    assert written == 3
    found = _imported(out)
    assert [f["kind"] for f in found] == ["text_box"] * 3
    assert [(f["content"], f["box"], f["page"]) for f in found] == [
        (b["content"], b["box"], 1) for b in boxes]


def test_a_box_moved_or_retyped_in_another_viewer_keeps_that_change():
    out, _ = annotate_pdf(_pdf(), [], text_boxes=[_tb("**Mine**", size=16, color="#c0392b")])

    def edit(annot):
        x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        annot[NameObject("/Rect")] = ArrayObject(FloatObject(v) for v in (x0 + 10, y0 - 20, x1 + 10, y1 - 20))
        annot[NameObject("/Contents")] = TextStringObject("Theirs")

    [f] = _reimported(out, edit)
    assert f["content"] == "Theirs"
    assert (f["box"]["x"], f["box"]["y"], f["box"]["size"], f["box"]["color"]) == (110, 70, 16, "#c0392b")


def _annot(subtype, rect, **keys):
    """An annotation dictionary: a tuple is a number array, an int a
    number, a str a text string, anything else (a name, a reference) as is."""
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject(subtype),
        NameObject("/Rect"): ArrayObject(FloatObject(v) for v in rect),
    })
    for key, value in keys.items():
        if isinstance(value, tuple):
            value = ArrayObject(FloatObject(v) for v in value)
        elif isinstance(value, int):
            value = NumberObject(value)
        elif isinstance(value, str):
            value = TextStringObject(value)
        annot[NameObject("/" + key)] = value
    return annot


def _pdf_with(build):
    """A one-page PDF whose annotations ``build(add)`` makes: ``add(annot)``
    puts one on the page and returns its reference, for /IRT, /Popup and
    /Parent."""
    w = PdfWriter()
    w.add_blank_page(width=PAGE_W, height=PAGE_H)
    annots = ArrayObject()
    w.pages[0][NameObject("/Annots")] = annots

    def add(annot):
        ref = w._add_object(annot)
        annots.append(ref)
        return ref

    build(add)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _sticky_thread(add):
    """A sticky note with its popup and a thread as Acrobat keeps one: two
    replies, a reply to the first (no /NM), and a hidden review state."""
    parent = add(_annot("/Text", (500, 700, 520, 720), Contents="Parent note", NM="p1"))
    parent.get_object()[NameObject("/Popup")] = add(_annot("/Popup", (520, 600, 700, 720), Parent=parent))
    first = add(_annot("/Text", (500, 700, 520, 720), Contents="First reply", NM="r1", IRT=parent))
    add(_annot("/Text", (500, 700, 520, 720), Contents="Accepted set by Tim", IRT=parent,
               State="Accepted", StateModel="Review", F=30))
    add(_annot("/Text", (500, 700, 520, 720), Contents="A reply to the reply", IRT=first))
    add(_annot("/Text", (500, 700, 520, 720), Contents="Second reply", NM="r2", IRT=parent))


def test_a_foreign_free_text_becomes_a_fixed_box_in_its_style():
    found = _imported(_pdf(annots=[
        _annot("/FreeText", (100, 600, 300, 650), Contents="Typed elsewhere",
               DA="0 0 1 rg /Helv 14 Tf", C=(1, 1, 0)),
        _annot("/FreeText", (100, 400, 300, 450), DA="/Helv 0 Tf 0.5 g",
               RC="<?xml version=\"1.0\"?><body><p>Rich <b>text</b></p></body>"),
    ]))
    assert [f["content"] for f in found] == ["Typed elsewhere", "Rich **text**"]
    assert found[0]["box"] == {"x": 100, "y": 142, "w": 200, "h": 50, "auto": False,
                               "size": 14, "color": "#0000ff", "bg": "#ffff00"}
    # size 0 is fit-to-box: the default size; a gray colour; no fill
    assert (found[1]["box"]["size"], found[1]["box"]["color"], found[1]["box"]["bg"]) == (12, "#808080", None)
    assert found[0]["key"] == "1:/FreeText:100:600:300"      # the key these always had


def test_a_sticky_note_becomes_a_yellow_box_at_its_icon():
    found = _imported(_pdf(annots=[
        _annot("/Text", (500, 700, 520, 720), Contents="Sticky"),
        _annot("/Text", (595, 500, 612, 520), Contents="Right margin note"),
        _annot("/Text", (50, 50, 70, 70)),                         # no text: nothing to show
    ]))
    assert len(found) == 2
    sticky, margin = (f["box"] for f in found)
    # "Sticky" at 12 pt Helvetica is 32 pt wide, plus the padding
    assert sticky == {"x": 500, "y": 72, "w": 40, "h": 23, "auto": True,
                      "size": 12, "color": text_box.DEFAULT_COLOR, "bg": "#fff4b8"}
    assert margin["x"] + margin["w"] == pytest.approx(PAGE_W) and margin["y"] == 272


def _upload_pdf(guest, data, title):
    up = guest.post("/api/uploads", files={"file": ("t.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    return make_page(guest, title, properties={"doc_id": up.json()["doc_id"],
                                               "source_url": up.json()["source_url"]}), up.json()["doc_id"]


def _put_children(guest, page_id, tree):
    r = guest.put(f"/api/blocks/{page_id}/children", json={"blocks": tree})
    assert r.status_code == 200, r.text


def _stored_pdf(guest, page_id):
    source = guest.get(f"/api/blocks/{page_id}").json()["properties"]["source_url"]
    return PdfReader(io.BytesIO(guest.get(source).content))


def _kids(guest, block_id):
    return guest.get(f"/api/blocks/{block_id}/children").json()["children"]


def test_import_then_export_through_the_endpoints(guest):
    tb = _tb("Round *trip*", bg="#fff4b8")
    page, doc_id = _upload_pdf(guest, annotate_pdf(_pdf(90), [], text_boxes=[tb])[0], "Text box import")
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "found": 1, "imported": 1, "stripped": 1}
    [block] = _kids(guest, page["id"])
    props = block["properties"]
    assert block["content"] == "Round *trip*" and props["text_box"] == tb["box"] and props["pdf_page"] == 1
    assert props["imported_annot"] and props["annot_stripped"] is True and "highlight_id" not in props
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id})
    assert r.json()["imported"] == 0                          # idempotent
    # stripped from the file, so the export writes it again
    r = guest.get(f"/api/pages/{page['id']}/export-pdf")
    assert r.status_code == 200, r.text
    assert r.headers["x-annotations-written"] == "1"
    [a] = _free_texts(r.content)
    assert a["/Contents"] == "Round trip" and a["/Rotate"] == 90


def test_the_export_writes_the_boxes_on_the_pdf(guest):
    """Every box with text on a PDF page, one still embedded in the file
    included (its original, not in this file, has nothing to replace);
    none that is empty or on a sheet."""
    page, _doc = _upload_pdf(guest, _pdf(), "Text box export")
    _put_children(guest, page["id"], [
        {"id": "tbA", "content": "On the page", "children": [], "properties": {
            "pdf_page": 1, "text_box": {"x": 20, "y": 30, "size": "big", "color": "red", "junk": 1}}},
        {"id": "tbEmbedded", "content": "Still in the file", "children": [], "properties": {
            "pdf_page": 1, "text_box": {"x": 20, "y": 90}, "imported_annot": "1:/FreeText:1:2:3"}},
        {"id": "tbEmpty", "content": "  ", "children": [], "properties": {"pdf_page": 1, "text_box": {}}},
        {"id": "tbSheet", "content": "", "properties": {"sheet": notebook.normalize_paper(None)}, "children": [
            {"id": "tbOnSheet", "content": "On the sheet", "children": [], "properties": {
                "pdf_page": 1, "text_box": {"x": 20, "y": 150}}},
        ]},
    ])
    r = guest.get(f"/api/pages/{page['id']}/export-pdf")
    assert r.status_code == 200, r.text
    assert r.headers["x-annotations-written"] == "2"
    boxes = {str(a["/Contents"]): a for a in _free_texts(r.content)}
    assert set(boxes) == {"On the page", "Still in the file"}
    a = boxes["On the page"]
    # read through normalize: the bad size and colour take their defaults
    assert a["/DA"] == "/Helv 12 Tf 0.1216 0.1216 0.1216 rg"
    assert "junk" not in json.loads(a["/GammaTextBox"])["box"]


def test_a_sheets_boxes_are_real_text_in_the_notebook_pdf(guest):
    import pypdfium2 as pdfium

    page = guest.post("/api/pages", json={"title": "Typed notebook"}).json()["id"]
    r = guest.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "tbNb", "parent": page, "position": generate_key_between(None, None),
         "content": "", "props": {"sheet": notebook.normalize_paper({"pattern": "ruled"})}},
        {"op": "insert", "id": "tbNbBox", "parent": "tbNb", "content": "Typed on **paper**",
         "props": {"text_box": {"x": 72, "y": 100, "size": 16, "bg": "#fff4b8"}}},
    ]})
    assert r.status_code == 200, r.text
    r = guest.get(f"/api/pages/{page}/export-pdf")
    assert r.status_code == 200, r.text
    assert r.headers["x-annotations-written"] == "1"
    doc = pdfium.PdfDocument(r.content)
    try:
        pdf_page = doc[0]
        textpage = pdf_page.get_textpage()
        text = textpage.get_text_bounded()
        left, bottom, _right, top = textpage.get_charbox(0)
        textpage.close()
        pdf_page.close()
    finally:
        doc.close()
    assert text.strip() == "Typed on paper"
    height = notebook.normalize_paper(None)["height"]
    assert 72 + text_box.PAD <= left < 72 + text_box.PAD + 2
    assert height - 100 - text_box.PAD - 16 * text_box.LINE < bottom < top < height - 100 - text_box.PAD


_RECT = {"x1": 50.0, "y1": 60.0, "x2": 250.0, "y2": 160.0, "width": 612.0, "height": 792.0}


def test_a_box_under_a_highlight_is_written_once(guest):
    """A text box nested under a highlight is its own /FreeText, so the
    highlight's popup, and the note notes=1 paints from it, leave it out
    with the notes under it; a box on no page stays a note line there."""
    page, _doc = _upload_pdf(guest, _pdf(), "Nested box")
    _put_children(guest, page["id"], [
        {"id": "tbNestHl", "content": "my comment", "properties": {
            "highlight_id": "tbNestHl", "quote": "q", "pdf_page": 1, "color": "rgba(255, 226, 143, 0.65)",
            "pdf_position": {"pageNumber": 1, "boundingRect": _RECT, "rects": [_RECT]}}, "children": [
            {"id": "tbNestBox", "content": "TYPED BOX TEXT", "properties": {
                "pdf_page": 1, "text_box": {"x": 300, "y": 300}}, "children": [
                {"id": "tbNestUnder", "content": "about the box", "properties": {}, "children": []}]},
            {"id": "tbNestNote", "content": "plain note", "properties": {}, "children": []},
        ]},
    ])
    r = guest.get(f"/api/pages/{page['id']}/export-pdf")
    assert r.headers["x-annotations-written"] == "2"
    annots = _annots(r.content)
    assert [str(a["/Contents"]) for a in annots if a["/Subtype"] == "/FreeText"] == ["TYPED BOX TEXT"]
    [hl] = [a for a in annots if a["/Subtype"] == "/Highlight"]
    assert hl["/Contents"] == "my comment\n- plain note"
    painted = guest.get(f"/api/pages/{page['id']}/export-pdf?notes=1")
    assert painted.headers["x-notes-rendered"] == "1"
    text = _page_text(painted.content)
    assert "my comment" in text and "TYPED" not in text and "about the box" not in text
    # A box placed on no page is a note like any other.
    kids = {"h": [{"id": "b", "content": "on no page", "properties": {"text_box": {}}}]}
    assert highlight_note_text({"id": "h", "content": ""}, kids) == "- on no page"
    # A box under a sheet is on the sheet whatever its pdf_page says, so the
    # annotated PDF does not write it as a FreeText: it stays in the popup.
    kids = {"h": [{"id": "s", "content": "Page 1", "properties": {"sheet": {}}}],
            "s": [{"id": "b", "content": "on the sheet", "properties": {"pdf_page": 1, "text_box": {}}}]}
    assert highlight_note_text({"id": "h", "content": ""}, kids) == "- Page 1\n  - on the sheet"


def test_the_view_box_is_the_crop_box_clipped_to_the_media_box():
    """pdf.js, pdfium and MuPDF show a page's crop box clipped to its media
    box, the frame the client stores places in: a box exported there lands
    where the viewer drew it and comes back to the same place."""
    big = _pdf(crop=(-50, -60, 700, 900))
    assert page_frame(PdfReader(io.BytesIO(big)).pages[0]) == ((0, 0, 612, 792), 0)
    apart = _pdf(crop=(700, 800, 900, 1000))                 # meets the media box nowhere
    assert page_frame(PdfReader(io.BytesIO(apart)).pages[0]) == ((0, 0, 612, 792), 0)
    tb = _tb()
    out, _ = annotate_pdf(big, [], text_boxes=[tb])
    [a] = _free_texts(out)
    assert [float(v) for v in a["/Rect"]] == pytest.approx((100, 792 - 50 - 23, 300, 792 - 50), abs=0.001)
    [f] = _imported(out)
    assert f["box"] == tb["box"]


def test_replies_fold_into_their_parent_and_what_is_not_shown_stays_out():
    """A reply (/IRT) is a note under the annotation it answers, in page
    order, keyed apart from its parent (whose rectangle it shares) by its
    /NM or its place. A review-state stamp, a Hidden or NoView annotation
    and a reply to something not imported make nothing; a /RT /Group member
    is an annotation of its own."""
    def build(add):
        _sticky_thread(add)
        add(_annot("/FreeText", (100, 600, 300, 650), Contents="hidden", F=2))
        add(_annot("/FreeText", (100, 500, 300, 550), Contents="not on screen", F=32 | 4))
        line = add(_annot("/Line", (10, 10, 90, 90), Contents="a line"))
        add(_annot("/Text", (10, 10, 30, 30), Contents="on the line", IRT=line))
        add(_annot("/FreeText", (100, 400, 300, 450), Contents="grouped", IRT=line, RT=NameObject("/Group")))

    sticky, grouped = _imported(_pdf_with(build))
    assert (sticky["content"], sticky["key"]) == ("Parent note", "1:/Text:500:700:520")
    first, second = sticky["replies"]
    assert (first["content"], first["key"], first["legacy"]) == (
        "First reply", "1:/Text:500:700:520:r1", "1:/Text:500:700:520")
    assert (second["content"], second["key"]) == ("Second reply", "1:/Text:500:700:520:r2")
    [nested] = first["replies"]
    assert (nested["content"], nested["key"]) == ("A reply to the reply", "1:/Text:500:700:520:#4")
    assert grouped["content"] == "grouped" and grouped["kind"] == "text_box" and not second["replies"]


def test_a_comment_thread_imports_as_a_box_with_its_replies(guest):
    """The thread lands as the box with its replies as notes under it, in
    order, each with a key of its own, so a second import adds nothing.
    Stripping takes the thread out of the file (the review state and the
    popup with it) and leaves what made no block: notes with no text."""
    def build(add):
        _sticky_thread(add)
        add(_annot("/Text", (50, 50, 70, 70)))
        add(_annot("/FreeText", (100, 300, 300, 350), Contents=""))

    page, doc_id = _upload_pdf(guest, _pdf_with(build), "Thread")
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id})
    assert r.json() == {"ok": True, "found": 4, "imported": 4, "stripped": 0}
    [box] = _kids(guest, page["id"])
    assert box["content"] == "Parent note" and box["properties"]["text_box"]["bg"] == "#fff4b8"
    replies = _kids(guest, box["id"])
    assert [b["content"] for b in replies] == ["First reply", "Second reply"]
    assert [b["content"] for b in _kids(guest, replies[0]["id"])] == ["A reply to the reply"]
    assert all("text_box" not in b["properties"] for b in replies)
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id, "strip": True})
    assert r.json() == {"ok": True, "found": 4, "imported": 0, "stripped": 6}
    kept = [a.get_object() for a in _stored_pdf(guest, page["id"]).pages[0]["/Annots"]]
    assert sorted(str(a["/Subtype"]) for a in kept) == ["/FreeText", "/Text"]
    assert not any(str(a.get("/Contents", "")) for a in kept)
    assert _kids(guest, box["id"])[0]["properties"]["annot_stripped"] is True


def test_a_thread_imported_before_replies_were_notes_adds_nothing(guest):
    """Each reply used to be an annotation of its own with its rectangle's
    key, its parent's: those blocks stand for it, so nothing comes twice."""
    page, doc_id = _upload_pdf(guest, _pdf_with(_sticky_thread), "Old thread")
    old = {"pdf_page": 1, "text_box": {"x": 500, "y": 72}, "imported_annot": "1:/Text:500:700:520"}
    _put_children(guest, page["id"], [
        {"id": f"tbOld{n}", "content": text, "children": [], "properties": old}
        for n, text in enumerate(["Parent note", "First reply", "A reply to the reply", "Second reply"])])
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id})
    assert r.json()["imported"] == 0


def test_foreign_text_is_escaped_so_it_reads_as_typed():
    """Plain text from another app is escaped into Markdown that shows it as
    written, its \\r line breaks normalized; the next export's /Contents is
    that text again. /RC keeps its bold, its text escaped too."""
    typed = "Total: $5 and $10\r* not a list\r\n# not [[a ref]] <b>"
    free, sticky = _imported(_pdf(annots=[
        _annot("/FreeText", (100, 600, 300, 650), Contents=typed, DA="/Helv 12 Tf 0 g"),
        _annot("/Text", (500, 700, 520, 720), RC="<body><p>$5 <b>*bold*</b></p><p>- no list</p></body>"),
    ]))
    shown = "Total: $5 and $10\n* not a list\n# not [[a ref]] <b>"
    assert "\\$5" in free["content"] and text_box.plain_text(free["content"]) == shown
    [a] = _free_texts(annotate_pdf(_pdf(), [], text_boxes=[_tb(free["content"])])[0])
    assert a["/Contents"] == shown
    assert "**" in sticky["content"] and text_box.plain_text(sticky["content"]) == "$5 *bold*\n- no list"


def test_a_gamma_box_keeps_its_markdown_when_another_app_only_rewrote_the_newlines():
    out, _ = annotate_pdf(_pdf(), [], text_boxes=[_tb("**Line** one\nLine two")])

    def edit(annot):
        annot[NameObject("/Contents")] = TextStringObject("Line one\r\nLine two")

    [f] = _reimported(out, edit)
    assert f["content"] == "**Line** one\nLine two"


def test_references_in_a_box_read_as_the_text_they_name(guest):
    """[[ref]] and ![[embed]] in a box read as the first line of the block
    they name, in the appearance, /Contents and the notebook PDF, as its
    chips do; the re-import keeps the reference, since /Contents is what
    the export wrote."""
    other = make_page(guest, "Other page")
    _put_children(guest, other["id"], [
        {"id": "tbRefTarget", "content": "Target note text\nsecond line", "children": [], "properties": {}}])
    page, _doc = _upload_pdf(guest, _pdf(), "Refs")
    _put_children(guest, page["id"], [
        {"id": "tbRefBox", "content": "See [[tbRefTarget]]\n![[tbRefTarget]]", "children": [],
         "properties": {"pdf_page": 1, "text_box": {"x": 20, "y": 30}}}])
    r = guest.get(f"/api/pages/{page['id']}/export-pdf")
    [a] = _free_texts(r.content)
    assert a["/Contents"] == "See Target note text\nTarget note text"
    assert b"(Target note text)" in a["/AP"]["/N"].get_object().get_data()
    [f] = _imported(r.content)
    assert f["content"] == "See [[tbRefTarget]]\n![[tbRefTarget]]"

    notebook_page = guest.post("/api/pages", json={"title": "Refs on paper"}).json()["id"]
    r = guest.post(f"/api/pages/{notebook_page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "tbRefSheet", "parent": notebook_page, "position": generate_key_between(None, None),
         "content": "", "props": {"sheet": notebook.normalize_paper(None)}},
        {"op": "insert", "id": "tbRefSheetBox", "parent": "tbRefSheet", "content": "See [[tbRefTarget]]",
         "props": {"text_box": {"x": 72, "y": 100}}},
    ]})
    assert r.status_code == 200, r.text
    assert _page_text(guest.get(f"/api/pages/{notebook_page}/export-pdf").content).strip() == "See Target note text"


def test_a_callout_box_covers_its_text_not_its_line():
    found = _imported(_pdf(annots=[
        _annot("/FreeText", (100, 500, 400, 700), IT=NameObject("/FreeTextCallout"), RD=(150, 0, 0, 120),
               CL=(110, 510, 200, 600, 250, 600), Contents="callout text", DA="/Helv 12 Tf 0 g"),
        _annot("/FreeText", (100, 300, 400, 350), RD=(200, 0, 200, 0), Contents="no room left"),
    ]))
    callout, whole = (f["box"] for f in found)
    assert (callout["x"], callout["y"], callout["w"], callout["h"]) == (250, 792 - 580, 150, 80)
    assert found[0]["key"] == "1:/FreeText:100:500:400"      # from the whole /Rect, as always
    assert (whole["x"], whole["w"]) == (100, 300)             # an /RD that leaves nothing is ignored


def test_imported_boxes_are_kept_inside_the_page():
    huge, corner, sticky = (f["box"] for f in _imported(_pdf(annots=[
        _annot("/FreeText", (-50, -50, 99999, 99999), Contents="huge"),
        _annot("/FreeText", (500, -40, 700, 20), Contents="off the corner"),
        _annot("/Text", (100, 0, 120, 10), Contents="at the bottom"),
    ])))
    assert (huge["x"], huge["y"], huge["w"], huge["h"]) == (0, 0, PAGE_W, PAGE_H)
    assert (corner["x"], corner["y"], corner["w"], corner["h"]) == (PAGE_W - 200, PAGE_H - 60, 200, 60)
    assert sticky["y"] + sticky["h"] == PAGE_H


def test_an_imported_box_still_in_the_file_exports_as_gamma_has_it(guest):
    """Imported without stripping (the viewer hides the file's own
    annotations), a box is written as it is here, and its embedded original
    leaves the copy with its popup and its thread, so the file shows what the
    page shows; a highlight still in the file keeps the old rule. The
    notes-only export keeps the boxes. A box deleted here leaves its
    original in the file: nothing records it."""
    def build(add):
        box = add(_annot("/FreeText", (100, 600, 300, 650), Contents="Typed elsewhere", DA="/Helv 12 Tf 0 g"))
        box.get_object()[NameObject("/Popup")] = add(_annot("/Popup", (300, 600, 400, 700), Parent=box))
        add(_annot("/Text", (100, 600, 300, 650), Contents="a reply", IRT=box))
        add(_annot("/Highlight", (100, 400, 300, 420), QuadPoints=(100, 420, 300, 420, 100, 400, 300, 400),
                   Contents="theirs"))

    page, doc_id = _upload_pdf(guest, _pdf_with(build), "Kept in the file")
    r = guest.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": doc_id})
    assert r.json()["imported"] == 3
    box = next(b for b in _kids(guest, page["id"]) if "text_box" in b["properties"])
    assert guest.put(f"/api/blocks/{box['id']}", json={"content": "Edited in **Gamma**"}).status_code == 200

    def exported(query=""):
        r = guest.get(f"/api/pages/{page['id']}/export-pdf{query}")
        assert r.status_code == 200, r.text
        return r, sorted((str(a["/Subtype"]), str(a.get("/Contents", ""))) for a in _annots(r.content))

    r, annots = exported()
    assert r.headers["x-annotations-written"] == "1"
    assert annots == [("/FreeText", "Edited in Gamma"), ("/Highlight", "theirs")]
    r, annots = exported("?highlights=0&notes=1")
    assert r.headers["x-annotations-written"] == "1"
    assert annots == [("/FreeText", "Edited in Gamma"), ("/Highlight", "theirs")]
    assert guest.delete(f"/api/blocks/{box['id']}").status_code == 200
    _r, annots = exported()
    assert ("/FreeText", "Typed elsewhere") in annots and ("/Text", "a reply") in annots


def test_the_notes_only_pdf_keeps_the_boxes(guest):
    """A text box is the user's writing on the page: the annotated PDF
    writes it with either switch on, and with both off returns the file."""
    original = _pdf()
    page, _doc = _upload_pdf(guest, original, "Switches")
    _put_children(guest, page["id"], [
        {"id": "tbSwBox", "content": "typed", "children": [], "properties": {"pdf_page": 1, "text_box": {}}}])
    for query, written in (("?highlights=0&notes=1", "1"), ("?highlights=1&notes=0", "1")):
        r = guest.get(f"/api/pages/{page['id']}/export-pdf{query}")
        assert r.headers["x-annotations-written"] == written
        assert [str(a["/Contents"]) for a in _free_texts(r.content)] == ["typed"]
    bare = guest.get(f"/api/pages/{page['id']}/export-pdf?highlights=0&notes=0")
    assert bare.content == original


def test_a_sheets_text_boxes_as_the_notebook_pdf_takes_them():
    blocks = [
        {"id": "a", "content": "typed", "properties": {"text_box": {"x": 1.234, "size": 200}}},
        {"id": "b", "content": "  ", "properties": {"text_box": {}}},
        {"id": "c", "content": "a note", "properties": {}},
        {"id": "d", "content": "bad box", "properties": {"text_box": "nope"}},
    ]
    [(content, box)] = notebook.sheet_text_boxes(blocks)
    assert content == "typed" and box == text_box.normalize_text_box({"x": 1.234, "size": 200})
