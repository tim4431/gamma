"""The agent's eyes on handwriting: view_ink (a group on its PDF page or
sheet of paper, cropped or whole), the pictures of attached handwriting
blocks riding with a message, and the renderer under both
(gamma/ink_view.py, pdf_export.page_with_ink)."""

from types import SimpleNamespace

import pymupdf
import pytest

from gamma import ink as inkmod
from gamma.ai_context import notes_focus_section
from gamma.ai_tools import run_agent_tool
from gamma.ink_view import CROP_MIN_SIDE, crop_box
from gamma.pdf_export import page_with_ink

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)

PAGE_W, PAGE_H = 300.0, 400.0


def _ink(kind="pdf-page", color="#ff0000", width=PAGE_W, height=PAGE_H,
         points=((40, 60), (260, 340))):
    space = {"kind": kind, "width": width, "height": height, **({"page": 1} if kind == "pdf-page" else {})}
    return {"format": "gamma-ink", "version": 2, "space": space,
            "strokes": [{"id": "s1", "color": color, "size": 6, "ch": "xy",
                         "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in points], "xy")}]}


def _pdf(path, pages=1):
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=PAGE_W, height=PAGE_H)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _pixel(pdf_bytes, x, y):
    """The RGB of pixel ``(x, y)`` of the first page rendered at 1 pt per pixel."""
    return _raster(pdf_bytes)(x, y)


def _raster(pdf_bytes):
    """The first page rendered at 1 pt per pixel, as ``pixel(x, y) → (r, g, b)``."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
    finally:
        doc.close()
    return pix.pixel


def _text_pdf(path, text="Handwriting", at=(40, 100), size=40):
    """A one-page PDF with a line of black text, its baseline at ``at``."""
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H).insert_text(at, text, fontsize=size)
    doc.save(str(path))
    doc.close()
    return path


def _drawing(*strokes):
    return inkmod.parse_ink({"format": "gamma-ink", "version": 2,
                             "space": {"kind": "pdf-page", "page": 1, "width": PAGE_W, "height": PAGE_H},
                             "strokes": list(strokes)})


def _line(sid, y, x0=20, x1=280, step=2.0, ch="xy", **look):
    """A horizontal stroke at ``y``; ``look`` keys that are channels
    (p, w, o) are callables of the fraction along it."""
    channels = {k: look.pop(k) for k in ("p", "w", "o") if k in look}
    n = int((x1 - x0) / step) + 1
    samples = [{"x": x0 + step * i, "y": y, "t": 8 * i, **{k: f(i / (n - 1)) for k, f in channels.items()}}
               for i in range(n)]
    return {"id": sid, "ch": ch, "pts": inkmod.encode_points(samples, ch), **look}


def _page_count(pdf_bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return len(doc)
    finally:
        doc.close()


@pytest.fixture(scope="module")
def inked(org, tmp_path_factory):
    """A PDF page carrying a captioned handwriting group and an unrelated
    note, plus a notebook: a sheet of ruled paper with a group on it."""
    c, ids = org

    def block(parent, content, props=None):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def upload(data):
        r = c.post("/api/upload-ink", json=data)
        assert r.status_code == 200, r.text
        return r.json()

    paper = block("root", "inked paper", {"folders": [ids["readout"]], "doc_id": "e" * 24})
    drawn = upload(_ink())
    group = block(paper, "circled the key result",
                  {"ink_url": drawn["url"], "pdf_position": drawn["pdf_position"], "ink_strokes": 1})
    plain = block(paper, "a typed note")
    book = block("root", "lecture notebook", {"folders": [ids["readout"]]})
    sheet = block(book, "Page 1", {"sheet": {"pattern": "ruled"}})
    scrawl = block(sheet, "", {"ink_url": upload(_ink("canvas", "#0000ff", 595.28, 841.89))["url"],
                               "ink_strokes": 1})
    pdf = _pdf(tmp_path_factory.mktemp("ink") / "inked.pdf")
    return c, {**ids, "paper": paper, "group": group, "plain": plain, "book": book,
               "sheet": sheet, "scrawl": scrawl, "pdf": pdf}


@pytest.fixture
def stored_pdf(inked, monkeypatch):
    _, ids = inked
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc: ids["pdf"])
    return ids


def test_page_with_ink_draws_the_strokes_on_that_page(tmp_path):
    path = _pdf(tmp_path / "two.pdf", pages=2)
    data, pages = page_with_ink(path, 2, [inkmod.parse_ink(_ink())])
    assert pages == 2 and data
    assert _page_count(data) == 1  # the page alone
    # The stroke runs corner to corner through the middle; the corner is paper.
    assert _pixel(data, 150, 200) == (255, 0, 0)
    assert _pixel(data, 290, 10) == (255, 255, 255)
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        assert list(doc[0].annots()) == []          # drawn into the content, not annotated
    finally:
        doc.close()
    assert page_with_ink(path, 2, [])[0]   # nothing to stamp: the page as it is
    assert page_with_ink(path, 3, []) == (b"", 2)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_stamped_strokes_land_where_they_were_drawn_on_a_turned_page(rotation):
    """The stamp maps display space through the page's frame, as /Ink does."""
    import io
    from PyPDF2 import PdfWriter
    from PyPDF2.generic import NameObject, NumberObject

    writer = PdfWriter()
    writer.add_blank_page(width=PAGE_W, height=PAGE_H)
    if rotation:
        writer.pages[0][NameObject("/Rotate")] = NumberObject(rotation)
    buf = io.BytesIO()
    writer.write(buf)
    shown = (PAGE_H, PAGE_W) if rotation in (90, 270) else (PAGE_W, PAGE_H)
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2,
                            "space": {"kind": "pdf-page", "page": 1, "width": shown[0], "height": shown[1]},
                            "strokes": [_line("pen", 40, x0=30, x1=80, color="#ff0000", size=6),
                                        _line("hl", 90, x0=30, x1=80, tool="highlighter", color="#0000ff",
                                              size=10, opacity=0.5)]})
    pixel = _raster(page_with_ink(buf.getvalue(), 1, [ink])[0])
    assert pixel(55, 40) == (255, 0, 0) and pixel(55, 60) == (255, 255, 255)
    r, g, b = pixel(55, 90)
    assert b == 255 and 120 < r == g < 136                    # blue at half strength, multiplied


def test_text_under_a_highlighter_stays_legible(tmp_path):
    """The highlighter multiplies onto the page's text: every glyph pixel
    under it stays dark, and the paper around them turns yellow."""
    path = _text_pdf(tmp_path / "text.pdf")
    bare = _raster(page_with_ink(path, 1, [])[0])
    glyphs = [(x, y) for x in range(40, 280) for y in range(74, 100) if max(bare(x, y)) < 60]
    assert len(glyphs) > 300
    marker = _line("hl", 88, x0=30, tool="highlighter", color="#ffe066", size=30, opacity=0.6)
    lit = _raster(page_with_ink(path, 1, [_drawing(marker)])[0])
    assert all(max(lit(x, y)) < 80 for x, y in glyphs)
    r, g, b = lit(32, 88)                      # paper under the highlighter, left of the text
    assert r > 240 and b < 200 and lit(32, 160) == (255, 255, 255)


def test_a_pen_stays_over_a_highlighter_drawn_after_it(tmp_path):
    """Band 0 is drawn first across the page's groups, as on screen."""
    path = _pdf(tmp_path / "blank.pdf")
    pen = _line("pen", 200, color="#0000ff", size=6)
    marker = {**_line("hl", 0, x0=150, x1=150), "tool": "highlighter", "color": "#ffe066", "size": 30,
              "pts": inkmod.encode_points([{"x": 150, "y": 100}, {"x": 150, "y": 300}], "xy")}
    data, _ = page_with_ink(path, 1, [_drawing(pen), _drawing(marker)])
    assert _pixel(data, 150, 200) == (0, 0, 255)                # the crossing: the pen on top
    r, g, b = _pixel(data, 150, 150)
    assert r > 240 and b < 200                                  # the highlighter itself


def test_a_pencil_shows_its_three_shades(tmp_path):
    """o = 0.15 + 0.85p from no pressure to full: the light, middle and
    dark bands each read as their own grey."""
    path = _pdf(tmp_path / "blank.pdf")
    pencil = _line("pc", 200, x0=30, x1=270, ch="xyptwo", brush="pencil", color="#000000", size=6,
                   p=lambda f: f, w=lambda f: 1 + (f - 0.5), o=lambda f: 0.15 + 0.85 * f)
    shade = _raster(page_with_ink(path, 1, [_drawing(pencil)])[0])
    light, mid, dark = (shade(x, 200)[0] for x in (55, 130, 240))   # o 0.33, 0.66, 1 bands
    assert light == pytest.approx(255 * 0.67, abs=4) and mid == pytest.approx(255 * 0.34, abs=4) and dark < 4


def test_a_dashed_stroke_renders_with_gaps(tmp_path):
    """A dashed pressure pen of size 4: dashes 8 pt on, 8 off along its
    centreline (round caps eat 4 pt of every gap)."""
    path = _pdf(tmp_path / "blank.pdf")
    dashed = _line("d", 300, ch="xyptw", color="#000000", size=4, dash="dashed", p=lambda f: 0.5, w=lambda f: 1.0)
    pixel = _raster(page_with_ink(path, 1, [_drawing(dashed)])[0])
    ink = [max(pixel(x, 300)) < 128 for x in range(10, 290)]
    runs = sum(1 for a, b in zip([False] + ink, ink) if b and not a)
    assert runs == 17 and not all(ink)
    solid = _raster(page_with_ink(path, 1, [_drawing({**dashed, "dash": None})])[0])
    assert all(max(solid(x, 300)) < 128 for x in range(20, 281))


def test_crop_box_pads_grows_and_stays_on_the_page():
    ink = inkmod.parse_ink(_ink(points=((100, 200), (110, 205))))
    x0, y0, x1, y1 = crop_box([ink], PAGE_W, PAGE_H)
    assert (x1 - x0) * PAGE_W == pytest.approx(CROP_MIN_SIDE)  # a tiny scribble shows 2 inches
    assert (y1 - y0) * PAGE_H == pytest.approx(CROP_MIN_SIDE)
    assert x0 * PAGE_W < 100 and x1 * PAGE_W > 110
    corner = inkmod.parse_ink(_ink(points=((2, 2), (5, 5))))
    x0, y0, x1, y1 = crop_box([corner], PAGE_W, PAGE_H)
    assert x0 == 0 and y0 == 0 and x1 * PAGE_W == pytest.approx(CROP_MIN_SIDE)
    assert crop_box([], PAGE_W, PAGE_H) is None


def test_view_ink_shows_a_group_on_its_pdf_page(stored_pdf):
    ids = stored_pdf
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["group"]})
    assert text.startswith(f"Handwriting block [{ids['group']}] on PDF page 1"), text
    assert "cropped" in text and '"circled the key result"' in text and "[illegible]" in text
    assert action["kind"] == "ink" and action["block_id"] == ids["group"] and action["pdf_page"] == 1
    (media_type, data), = action["images"]
    assert media_type in ("image/png", "image/jpeg") and len(data) > 100
    # The whole page, with every group on it.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink",
                                  {"block_id": ids["group"], "area": "page"})
    assert text.startswith('All the handwriting on PDF page 1 of "inked paper"') and action["images"]


def test_view_ink_shows_a_sheet_of_paper(stored_pdf):
    ids = stored_pdf
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["scrawl"]})
    assert f"Handwriting block [{ids['scrawl']}] on a page of paper" in text and "no caption yet" in text
    assert "pdf_page" not in action and action["images"]
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["sheet"]})
    assert text.startswith(f"The page of paper [{ids['sheet']}]") and "with all the handwriting" in text


def test_view_ink_refusals(stored_pdf, monkeypatch):
    ids = stored_pdf
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["plain"]})
    assert "holds no handwriting" in text and action["error"] and "images" not in action
    # Outside the chat's folder, and with the permission off.
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "view_ink", {"block_id": ids["group"]})
    assert text.startswith("error") and "outside" in text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["group"]},
                             allowed_tools={"read_block"})
    assert "not enabled" in text
    # A PDF this server doesn't hold: the strokes are still drawn, on blank paper.
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc: None)
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["group"]})
    assert "drawn on blank paper" in text and action["images"]


def test_attached_handwriting_rides_with_the_message_as_a_picture(stored_pdf):
    ids = stored_pdf
    crops = []
    payload = SimpleNamespace(focus_block_id="", context_blocks=[ids["group"], ids["plain"]],
                              note_selections=[], pages=[], page_id=ids["paper"])
    section = notes_focus_section(ids["ws"], payload, crops=crops)
    assert f"[{ids['group']}] (handwriting on p. 1, 1 strokes; the text is its caption)" in section
    assert "a picture of this handwriting is attached" in section
    assert len(crops) == 1 and crops[0]["part"][0] in ("image/png", "image/jpeg")
    assert crops[0]["group"] == "ink" and crops[0]["block_id"] == ids["group"]
    assert crops[0]["label"].startswith(f"The attached handwriting block [{ids['group']}] on PDF page 1")
    # The cursor block rides with every message: labelled, never pictured.
    crops = []
    payload = SimpleNamespace(focus_block_id=ids["group"], context_blocks=[], note_selections=[],
                              pages=[], page_id=ids["paper"])
    assert "handwriting on p. 1" in notes_focus_section(ids["ws"], payload, crops=crops)
    assert crops == []


def _selected(block_id, page_id, points=((100, 100), (180, 100)), color="#0000ff", kind="pdf-page", width=PAGE_W, height=PAGE_H):
    """An ink_selections entry: one stroke the user selected (saved or not), as the client sends it."""
    space = {"kind": kind, "width": width, "height": height, **({"page": 1} if kind == "pdf-page" else {})}
    return {"page_id": page_id, "block_id": block_id, "space": space,
            "strokes": [{"id": "sel1", "color": color, "size": 6, "ch": "xy",
                         "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in points], "xy")}]}


def _colours(image):
    """(any blue pixel, any red pixel) of a rendered picture ``(bytes, media type, w, h)``."""
    import io

    from PIL import Image
    data = Image.open(io.BytesIO(image[0])).convert("RGB").tobytes()
    pixels = list(zip(data[0::3], data[1::3], data[2::3]))
    return (any(b > 200 and r < 90 and g < 90 for r, g, b in pixels),
            any(r > 200 and g < 90 and b < 90 for r, g, b in pixels))


def test_selected_handwriting_rides_with_the_message_as_a_picture_of_those_strokes(stored_pdf):
    """Part of a group: the selected strokes, drawn alone where they lie on
    their page (the group's stored stroke stays out), labelled with the
    group's block and its caption."""
    ids = stored_pdf
    crops = []
    payload = SimpleNamespace(focus_block_id="", context_blocks=[], note_selections=[], pages=[], page_id=ids["paper"],
                              ink_selections=[_selected(ids["group"], ids["paper"])])
    section = notes_focus_section(ids["ws"], payload, crops=crops)
    assert "Handwriting the user selected for this message (part of the note block in brackets):" in section
    assert f"- [{ids['group']}] (selected handwriting on p. 1, 1 stroke) circled the key result" in section
    # the picture is the selection alone: what to transcribe, not the block's whole drawing
    assert "Each picture holds only the selected strokes, saved or not: transcribe exactly those" in section
    assert "(a picture of it is attached to the message)" in section
    (crop,) = crops
    assert crop["group"] == "ink" and crop["block_id"] == ids["group"]
    assert crop["label"].startswith(f"The selected handwriting of block [{ids['group']}] on PDF page 1")
    from gamma.ink_view import picture
    from gamma.db import connect_pages_db
    with connect_pages_db(ids["ws"]) as conn:
        shown = picture(ids["ws"], conn, ids["group"], ids["paper"],
                        ink=inkmod.parse_ink({"format": "gamma-ink", "version": 2, **_selected(ids["group"], ids["paper"])}))
    assert shown["pdf_page"] == 1 and not shown["whole"] and shown["strokes"] == 1
    blue, red = _colours(shown["image"])
    assert blue and not red, "the selected stroke alone, not the group's stored one crossing the same crop"


def test_selected_handwriting_not_saved_yet_or_on_paper_is_drawn_too(stored_pdf):
    """The block need not be saved: its strokes are drawn on their PDF page
    all the same; a sheet's selection is drawn on that sheet."""
    ids = stored_pdf
    crops = []
    payload = SimpleNamespace(focus_block_id="", context_blocks=[], note_selections=[], pages=[ids["paper"], ids["book"]],
                              page_id="", ink_selections=[
                                  _selected("draftgroup1", ids["paper"]),
                                  _selected(ids["scrawl"], ids["book"], kind="canvas", width=595.28, height=841.89,
                                            points=((100, 200), (300, 220))),
                              ])
    section = notes_focus_section(ids["ws"], payload, crops=crops)
    assert "- [draftgroup1] (selected handwriting on p. 1, 1 stroke)\n  (a picture of it is attached" in section
    assert f"- [{ids['scrawl']}] (selected handwriting on a page of paper, 1 stroke)" in section
    assert [c["block_id"] for c in crops] == ["draftgroup1", ids["scrawl"]]
    assert crops[1]["label"].startswith(f"The selected handwriting of block [{ids['scrawl']}] on a page of paper")


def test_a_selection_off_the_chat_s_pages_or_unreadable_is_dropped(stored_pdf):
    from gamma.ai_context import MAX_INK_SELECTIONS, request_ink_selections
    ids = stored_pdf
    good = _selected(ids["group"], ids["paper"])
    ask = lambda entries, **kw: SimpleNamespace(pages=[], page_id=ids["paper"], ink_selections=entries, **kw)  # noqa: E731
    assert [s["block_id"] for s in request_ink_selections(ask([good]))] == [ids["group"]]
    # another page's, a broken stroke, a version 1 space, no strokes, not an object: all dropped
    broken = {**good, "strokes": [{"id": "x", "ch": "xy", "pts": [1, 2, 3]}]}
    assert request_ink_selections(ask([_selected(ids["group"], ids["book"]), broken, {**good, "space": {"kind": "pdf-page"}},
                                       {**good, "strokes": []}, "nope", {**good, "block_id": ""}])) == []
    assert len(request_ink_selections(ask([good] * (MAX_INK_SELECTIONS + 3)))) == MAX_INK_SELECTIONS
    # nothing selected, no section; a selection alone makes one
    empty = SimpleNamespace(focus_block_id="", context_blocks=[], note_selections=[], pages=[], page_id=ids["paper"],
                            ink_selections=[_selected(ids["group"], ids["book"])])
    assert notes_focus_section(ids["ws"], empty, crops=[]) == ""


def test_the_chat_takes_eight_selections_and_64_kb_of_them_and_no_more(stored_pdf, inked):
    """/ai/chat/context renders the request /ai/chat would send, with the
    selection's lines; past either cap the request is refused."""
    from gamma.ai_context import MAX_INK_SELECTION_BYTES, MAX_INK_SELECTIONS, ink_selection_bytes
    c, _ = inked
    ids = stored_pdf
    body = {"prompt": "Transcribe the selected handwriting.", "page_id": ids["paper"], "title": "t"}
    one = _selected(ids["group"], ids["paper"])
    r = c.post("/api/ai/chat/context", json={**body, "ink_selections": [one]})
    assert r.status_code == 200, r.text
    assert f"- [{ids['group']}] (selected handwriting on p. 1, 1 stroke)" in r.text
    assert c.post("/api/ai/chat/context", json={**body, "ink_selections": [one] * MAX_INK_SELECTIONS}).status_code == 200
    assert c.post("/api/ai/chat/context", json={**body, "ink_selections": [one] * (MAX_INK_SELECTIONS + 1)}).status_code == 422
    # one long stroke over the byte cap
    long = _selected(ids["group"], ids["paper"], points=[(10 + i % 280, 10 + i // 280) for i in range(12_000)])
    assert ink_selection_bytes([long]) > MAX_INK_SELECTION_BYTES
    assert c.post("/api/ai/chat/context", json={**body, "ink_selections": [long]}).status_code == 422
    assert c.post("/api/ai/chat", json={**body, "ink_selections": [long]}).status_code == 422
