"""The translated view written into an exported PDF (gamma/pdf_translated.py,
docs/dev/translation.md "Exporting the translation"): the original lines go
and only they, the translation is laid out in their place and shrunk to
fit, upright on a turned page, a formula comes back as a picture of itself
on the baseline, the original text stays out of the file even behind a
clip, and one subset font serves every page. Then the export job that
carries it (POST /api/jobs/export with ``translation``): the annotated PDF
of the page with its translation, and what it refuses."""

import io
import zipfile

import pytest

from conftest import login, make_page, make_user

PAGE_W, PAGE_H = 300, 400


def _mupdf():
    import pymupdf

    return pymupdf


def _band(line):
    from gamma.pdf_translated import _band

    return _band(line)


def _paper(rotation=0, pages=1, extra=None):
    """A PDF of ``pages`` pages, each turned by ``rotation`` and written so
    its text reads upright on screen: two lines of a paragraph, a line with
    a formula ("x" in Times-Italic) inside it, a kept paragraph, a drawn
    frame and a link. ``extra(page, put)`` writes more on each page."""
    pymupdf = _mupdf()
    doc = pymupdf.open()
    for n in range(pages):
        # shown PAGE_W by PAGE_H once turned
        turned = rotation in (90, 270)
        page = doc.new_page(width=PAGE_H if turned else PAGE_W, height=PAGE_W if turned else PAGE_H)
        page.set_rotation(rotation)

        def put(x, y, text, size=10, font="helv"):
            page.insert_text(pymupdf.Point(x, y) * page.derotation_matrix, text, fontname=font, fontsize=size,
                             rotate=rotation)

        put(40, 60, f"Alpha line one on page {n + 1}")
        put(40, 74, "Alpha line two")
        put(40, 120, "Beta speaks of")
        put(112, 120, "x", font="tiit")
        put(120, 120, "and more")
        put(40, 200, "Kept paragraph stays")
        page.draw_rect(pymupdf.Rect(40, 250, 200, 330) * page.derotation_matrix, color=(0, 0, 1))
        page.insert_link({"kind": pymupdf.LINK_URI, "uri": "https://example.org",
                          "from": pymupdf.Rect(40, 50, 120, 62) * page.derotation_matrix})
        if extra:
            extra(page, put)
    return doc.tobytes()


def _shown(page):
    """The page's text as the viewer sees it: ``[(text, box, direction,
    font, baseline)]`` per span, in the display frame."""
    m = page.rotation_matrix
    out = []
    for block in page.get_text("dict", flags=0)["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line["dir"]
            direction = (round(dx * m.a + dy * m.c, 3), round(dx * m.b + dy * m.d, 3))
            for span in line["spans"]:
                box = _mupdf().Rect(span["bbox"]) * m
                origin = _mupdf().Point(span["origin"]) * m
                out.append((span["text"], box, direction, span["font"], origin.y))
    return out


def _line_box(page, startswith):
    """The display box of the line whose text starts so: what the viewer
    sends as that line's box."""
    boxes = [box for text, box, *_ in _shown(page) if text.startswith(startswith)]
    assert boxes, startswith
    return list(boxes[0])


def _within(area, box, baseline, slack=2):
    """Whether a span set at ``baseline`` with ``box`` (its font's full
    height, taller than its line) sits in ``area``: its sides inside, its
    baseline between the top and the bottom."""
    return (area.x0 - slack <= box.x0 and box.x1 <= area.x1 + slack
            and area.y0 < baseline <= area.y1 + slack)


def _formula(page, pn=1):
    """The formula span ("x" in Times-Italic) as the viewer sends it."""
    (box, base), = [(box, base) for text, box, _, font, base in _shown(page) if text == "x"]
    return {"page": pn, "box": list(box), "base": base, "text": "x"}


def _write(data, pages, lang="en"):
    from gamma import pdf_translated

    return pdf_translated.write(data, pages, lang)


def _text(data, page=0, clip=True):
    pymupdf = _mupdf()
    flags = pymupdf.TEXTFLAGS_TEXT if clip else pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_MEDIABOX_CLIP
    return pymupdf.open(stream=data, filetype="pdf")[page].get_text("text", flags=flags)


def _alpha_block(page, parts):
    return {"lines": [_line_box(page, "Alpha line one"), _line_box(page, "Alpha line two")], "size": 10,
            "parts": parts}


def test_the_translation_takes_the_place_of_the_original_lines_and_only_theirs():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Die übersetzten Worte des Absatzes"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    text = page.get_text("text")
    assert "Alpha" not in text and "Die übersetzten Worte des Absatzes" in text.replace("\n", " ")
    assert "Beta speaks of" in text and "Kept paragraph stays" in text
    # the translation sits in the paragraph's box, upright
    (box, direction, base), = [(b, d, y) for t, b, d, _, y in _shown(page) if "übersetzten" in t]
    alpha = pymupdf.Rect(_line_box(src, "Alpha line one")) | pymupdf.Rect(_line_box(src, "Alpha line two"))
    assert direction == (1.0, 0.0) and _within(alpha, box, base)
    # the drawing and the link are the file's still; the lines are masked
    # white, as the overlay masks them
    drawn = page.get_drawings()
    assert [d["rect"] for d in drawn if d["color"] == (0.0, 0.0, 1.0)] == [d["rect"] for d in src.get_drawings()]
    masks = [d["rect"] for d in drawn if d.get("fill") == (1.0, 1.0, 1.0)]
    assert all(any(m.contains(pymupdf.Rect(_band(line))) for m in masks)
               for line in (_line_box(src, "Alpha line one"), _line_box(src, "Alpha line two")))
    assert [link["uri"] for link in page.get_links()] == ["https://example.org"]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_on_a_turned_page_the_translation_reads_upright_where_the_lines_were(rotation):
    pymupdf = _mupdf()
    data = _paper(rotation)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    assert src.rotation == rotation and all(d == (1.0, 0.0) for _, _, d, *_ in _shown(src))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Translated words"])]}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    shown = _shown(page)
    assert not any("Alpha" in t for t, *_ in shown)
    (box, direction, base), = [(b, d, y) for t, b, d, _, y in shown if "Translated" in t]
    alpha = pymupdf.Rect(_line_box(src, "Alpha line one")) | pymupdf.Rect(_line_box(src, "Alpha line two"))
    assert direction == (1.0, 0.0) and _within(alpha, box, base) and abs(box.x0 - alpha.x0) < 3
    assert any(t.startswith("Kept paragraph") for t, *_ in shown)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_files_annotations_stay_as_they_were_at_every_turn(rotation):
    """A redaction drops the links and FreeText boxes it meets; they come
    back as they were stored, wherever the page turns, and so does a link
    MuPDF cannot follow (no action), which once hid the others from a
    restore by link."""
    pymupdf = _mupdf()

    def annotate(page, put):
        line = pymupdf.Rect(40, 64, 100, 76) * page.derotation_matrix
        page.add_freetext_annot(line, "typed in another app")
        doc = page.parent
        bare = doc.get_new_xref()
        x0, y0, x1, y1 = line * page.transformation_matrix
        doc.update_object(bare, f"<< /Type /Annot /Subtype /Link /Rect [{min(x0, x1)} {min(y0, y1)} {max(x0, x1)} {max(y0, y1)}] >>")
        annots = doc.xref_get_key(page.xref, "Annots")[1].strip("[]")
        doc.xref_set_key(page.xref, "Annots", f"[{annots} {bare} 0 R]")

    data = _paper(rotation, extra=annotate)

    def stored(raw):
        doc = pymupdf.open(stream=raw, filetype="pdf")
        page = doc[0]
        return sorted((doc.xref_get_key(x, "Subtype")[1], doc.xref_get_key(x, "Rect")[1], doc.xref_get_key(x, "A")[1],
                       doc.xref_get_key(x, "Contents")[1]) for x, *_ in page.annot_xrefs())

    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Übersetzt"])]}], "de")
    before, after = stored(data), stored(out)
    assert len(before) == 3 and after == before


def test_a_long_translation_shrinks_until_it_fits_its_box():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    long = " ".join(f"word{n}" for n in range(60))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, [long])]}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    spans = [(t, b, y) for t, b, _, _, y in _shown(page) if "word" in t]
    assert " ".join(t for t, _, _ in spans).split() == long.split()  # every word, in order
    alpha = pymupdf.Rect(_line_box(src, "Alpha line one")) | pymupdf.Rect(_line_box(src, "Alpha line two"))
    assert all(_within(alpha, b, y) for _, b, y in spans)
    sizes = {round(s["size"], 2) for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", [])
             for s in ln["spans"] if "word" in s["text"]}
    assert max(sizes) < 10


def test_a_formula_comes_back_as_a_picture_of_itself_on_the_baseline():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    formula = _formula(src)
    block = {"lines": [_line_box(src, "Beta"), _line_box(src, "and more")], "size": 10,
             "parts": ["Vor ", formula, " und nach"]}
    out = _write(data, [{"page": 1, "blocks": [block]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    text = page.get_text("text")
    assert "Beta" not in text and "Vor" in text and "und nach" in text
    (image,) = page.get_image_info()
    picture = pymupdf.Rect(image["bbox"])
    (baseline,) = {round(base, 2) for t, _, _, _, base in _shown(page) if t.startswith("Vor")}
    # the picture's share below the baseline is the formula's own descent
    x0, y0, x1, y1 = formula["box"]
    below = (y1 - formula["base"]) / (y1 - y0)
    assert abs((picture.y1 - baseline) - below * picture.height) < 0.3
    assert abs(picture.width / picture.height - (x1 - x0) / (y1 - y0)) < 0.05
    # it is the formula, drawn on a clear ground: black ink whose shape is
    # in the picture's mask, at the print resolution
    doc = pymupdf.open(stream=out, filetype="pdf")
    (xref, smask, width, *_), = doc[0].get_images(full=True)
    shape = pymupdf.Pixmap(doc.extract_image(smask)["image"])
    assert smask and width >= (x1 - x0) * 600 / 72 - 1
    assert min(shape.samples) == 0 and max(shape.samples) == 255


def test_no_original_text_rides_along_with_a_formula_even_behind_a_clip():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    block = {"lines": [_line_box(src, "Beta"), _line_box(src, "and more")], "size": 10,
             "parts": ["Vor ", _formula(src), " und nach"]}
    out = _write(data, [{"page": 1, "blocks": [block]}], "de")
    everything = _text(out, clip=False)
    assert "Beta" not in everything and "speaks" not in everything
    assert everything.count("Alpha line one") == 1 and everything.count("Kept paragraph") == 1


def test_a_formula_from_another_page_is_drawn_from_that_page():
    pymupdf = _mupdf()
    data = _paper(pages=2)
    doc = pymupdf.open(stream=data, filetype="pdf")
    block = _alpha_block(doc[0], ["joined ", _formula(doc[1], pn=2), " here"])
    out = _write(data, [{"page": 1, "blocks": [block]}])
    result = pymupdf.open(stream=out, filetype="pdf")
    assert len(result[0].get_image_info()) == 1 and not result[1].get_image_info()
    assert "Beta speaks of" in result[1].get_text("text")  # page 2 itself untouched


def test_a_formula_from_a_page_turned_otherwise_is_written_as_its_text():
    pymupdf = _mupdf()
    flat, turned = _paper(), _paper(rotation=90)
    doc = pymupdf.open(stream=flat, filetype="pdf")
    doc.insert_pdf(pymupdf.open(stream=turned, filetype="pdf"))
    data = doc.tobytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    block = _alpha_block(doc[0], ["joined ", _formula(doc[1], pn=2), " here"])
    out = _write(data, [{"page": 1, "blocks": [block]}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert not page.get_image_info() and "joined x here" in page.get_text("text").replace("\n", " ")


def test_a_formula_box_off_its_page_is_written_as_its_text():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    for box in ([1000, 1000, 1010, 1012], [-50, -50, -40, -38], [290, 390, 310, 410]):
        formula = {"page": 1, "box": box, "base": box[3] - 2, "text": "y"}
        out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["vor ", formula, " nach"])]}], "de")
        page = pymupdf.open(stream=out, filetype="pdf")[0]
        assert not page.get_image_info() and "vor y nach" in page.get_text("text").replace("\n", " "), box


def test_a_block_without_parts_loses_its_lines_and_gets_nothing():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    for parts in ([], ["   "]):
        out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, parts)]}])
        text = _text(out)
        assert "Alpha" not in text and "Beta speaks of" in text and "Kept paragraph" in text


def test_what_is_left_out_and_nothing_to_write():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    # a page the file has not, a block whose boxes have no area
    assert _write(data, [{"page": 9, "blocks": [_alpha_block(src, ["far away"])]}]) == data
    flat = {"lines": [[40, 60, 40, 70], [50, 60, 90, 60]], "size": 10, "parts": ["nothing"]}
    assert _write(data, [{"page": 1, "blocks": [flat]}]) == data
    # a page sent twice writes both lists
    beta = {"lines": [_line_box(src, "Beta"), _line_box(src, "and more")], "size": 10, "parts": ["Zwei"]}
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Eins"])]}, {"page": 1, "blocks": [beta]}])
    text = _text(out)
    assert "Eins" in text and "Zwei" in text and "Alpha" not in text and "Beta" not in text


def test_a_tall_glyph_of_the_next_line_stays():
    pymupdf = _mupdf()
    def operator(page, put):
        put(60, 107, "Q", size=30)  # a display formula's tall glyph, reaching up into the line above

    data = _paper(extra=operator)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    (tall,) = [b for t, b, *_ in _shown(src) if t == "Q"]
    line = _line_box(src, "Alpha line two")
    assert tall.intersects(pymupdf.Rect(line)) and not tall.intersects(pymupdf.Rect(_band(line)))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Übersetzt"])]}], "de")
    assert "Q" in _text(out) and "Alpha" not in _text(out)


def test_cjk_and_cyrillic_share_one_subset_font_across_pages():
    pymupdf = _mupdf()
    data = _paper(pages=2)
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = [{"page": 1, "blocks": [_alpha_block(doc[0], ["译文：原子在光镊中排列整齐。"])]},
             {"page": 2, "blocks": [_alpha_block(doc[1], ["译文：Привет, мир — 第二页。"])]}]
    out = _write(data, pages, "zh-CN")
    result = pymupdf.open(stream=out, filetype="pdf")
    first, second = (page.get_text("text").replace("\n", "") for page in result)
    assert "原子在光镊中排列整齐" in first and "Привет, мир" in second
    fonts = {(xref, name) for page in result for xref, _, _, name, *_ in page.get_fonts()}
    cjk = {xref for xref, name in fonts if "Droid" in name}
    assert len(cjk) == 1  # embedded once, shared by both pages
    assert len(out) < len(data) + 150_000  # subset: the full font is 4 MB


def test_a_file_that_needs_a_password_is_refused():
    pymupdf = _mupdf()
    doc = pymupdf.open(stream=_paper(), filetype="pdf")
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(ValueError):
        _write(locked, [{"page": 1, "blocks": [{"lines": [[40, 50, 200, 64]], "size": 10, "parts": ["x"]}]}])


@pytest.mark.parametrize("size,height", [(7, 0.3), (10, 1.0), (20, 2.5)])
def test_mupdf_draws_a_picture_in_a_line_where_the_writer_expects_it(size, height):
    """The rule ``pdf_translated._IMAGE_DROP`` stands on: MuPDF reports a
    picture in a line 0.8 of its height above where it draws it, its bottom
    on the baseline. A PyMuPDF that moves it fails here, not in a paper."""
    pymupdf = _mupdf()
    from gamma import pdf_translated

    red = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>'
    archive = pymupdf.Archive()
    archive.add((red, "r.svg"))
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    device = writer.begin_page(pymupdf.Rect(0, 0, 300, 300))
    story = pymupdf.Story(f'<p>Some words <img id="f0" src="r.svg" style="width:{size}px;height:{height * size}px"> after</p>',
                          user_css=pdf_translated._css(size), archive=archive)
    story.place(pymupdf.Rect(0, 0, 280, 280))
    seen = []
    story.element_positions(lambda p: seen.append(p.rect) if p.id == "f0" and p.rect[2] > p.rect[0] else None)
    story.draw(device)
    writer.end_page()
    writer.close()
    page = pymupdf.open(stream=out.getvalue(), filetype="pdf")[0]
    drawn = pymupdf.Rect(page.get_image_info()[0]["bbox"])
    (reported,) = seen
    h = reported[3] - reported[1]
    assert abs(drawn.y1 - (reported[3] + pdf_translated._IMAGE_DROP * h)) < 0.01
    baseline = {round(s["origin"][1], 2) for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
                for s in ln["spans"] if "words" in s["text"]}
    assert baseline == {round(drawn.y1, 2)}


# --- The export job ----------------------------------------------------------

@pytest.fixture(scope="module")
def user(client):
    make_user("tpdf_user", "tpdf-user-pw")
    return login("tpdf_user", "tpdf-user-pw")


def _pdf_page(c, title, data, **props):
    up = c.post("/api/uploads", files={"file": (f"{title}.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(c, title, {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"], **props})
    return page, up.json()["doc_id"]


def _highlight(c, page_id):
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": [
        {"id": f"hl-{page_id}", "content": "a note", "properties": {
            "quote": "Kept paragraph", "color": "rgba(255, 226, 143, 0.65)",
            "pdf_position": {"pageNumber": 1, "width": PAGE_W, "height": PAGE_H,
                             "boundingRect": {"x1": 40, "y1": 190, "x2": 160, "y2": 203},
                             "rects": [{"x1": 40, "y1": 190, "x2": 160, "y2": 203}]}},
         "children": []}]})
    assert r.status_code == 200, r.text


def _translation(doc_id, data, text="Übersetzt bleibt übersetzt", lang="de"):
    src = _mupdf().open(stream=data, filetype="pdf")[0]
    return {"doc_id": doc_id, "lang": lang, "pages": [{"page": 1, "blocks": [_alpha_block(src, [text])]}]}


def _start(c, **body):
    return c.post("/api/jobs/export", json=body)


def _finished(c, **body):
    from gamma import jobs

    started = _start(c, **body)
    assert started.status_code == 200, started.text
    return jobs.wait(started.json()["id"])


def _file(c, job):
    assert job["state"] == "done", job["error"]
    r = c.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200, r.text
    return r.content


@pytest.fixture(scope="module")
def paper(user):
    data = _paper(extra=lambda page, put: put(40, 380, "The export job's paper"))
    page, doc_id = _pdf_page(user, "Translated paper", data)
    _highlight(user, page["id"])
    return {"id": page["id"], "doc_id": doc_id, "data": data}


def test_a_page_exports_as_its_translated_annotated_pdf(user, paper):
    from pypdf import PdfReader

    job = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                    translation=_translation(paper["doc_id"], paper["data"]))
    out = _file(user, job)
    text = _text(out)
    assert "Übersetzt bleibt übersetzt" in text.replace("\n", " ") and "Alpha" not in text
    assert "Kept paragraph stays" in text
    annots = PdfReader(io.BytesIO(out)).pages[0]["/Annots"]
    assert any(a.get_object()["/Subtype"] == "/Highlight" for a in annots)
    assert job["params"]["translation"] == "de"
    # without one: the original, and no language in the params
    plain = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False)
    assert "Alpha line one" in _text(_file(user, plain)) and plain["params"]["translation"] == ""


def test_the_translation_goes_with_the_page_not_the_pages_inside_it(user, paper):
    inner, inner_doc = _pdf_page(user, "Inner paper", _paper(extra=lambda page, put: put(40, 380, "Inner")),
                                 parent_page=paper["id"])
    try:
        job = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=False, notes=False,
                        sub_pages=True, translation=_translation(paper["doc_id"], paper["data"]))
        z = zipfile.ZipFile(io.BytesIO(_file(user, job)))
        names = z.namelist()
        top = next(n for n in names if n.endswith("Translated paper.pdf"))
        below = next(n for n in names if n.endswith("Inner paper.pdf"))
        assert "Übersetzt" in _text(z.read(top)) and "Alpha" not in _text(z.read(top))
        assert "Alpha line one" in _text(z.read(below))
    finally:
        user.delete(f"/api/blocks/{inner['id']}")


def test_a_translation_of_another_file_fails_the_export(user, paper):
    job = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                    translation=_translation("0" * 64, paper["data"]))
    assert job["state"] == "failed" and "changed after it was translated" in job["error"]


def test_what_a_translated_export_refuses(user, paper, monkeypatch):
    translation = _translation(paper["doc_id"], paper["data"])
    for body, status in [
        ({"page_id": paper["id"], "mode": "readable"}, 400),                         # another format
        ({"folder": "anything", "mode": "annotated-pdf"}, 400),                      # a folder
        ({"page_id": paper["id"], "mode": "annotated-pdf",
          "translation": {**translation, "lang": "xx"}}, 422),                       # no such language
        ({"page_id": paper["id"], "mode": "annotated-pdf",
          "translation": {**translation, "pages": []}}, 422),                        # nothing in it
    ]:
        r = _start(user, **body, **({} if "translation" in body else {"translation": translation}))
        assert r.status_code == status, (body, r.text)
    from gamma.routers import export

    # the paper's one block has two line boxes, one part and 26 characters;
    # a formula's box counts its area
    formula = {"page": 1, "box": [40, 50, 140, 70], "base": 66, "text": "x"}
    with_formula = {**translation, "pages": [{"page": 1, "blocks": [
        {**translation["pages"][0]["blocks"][0], "parts": ["a ", formula]}]}]}
    for cap, value, sent in (("TRANSLATION_BLOCKS_MAX", 0, translation), ("TRANSLATION_ITEMS_MAX", 1, translation),
                             ("TRANSLATION_CHARS_MAX", 25, translation),
                             ("TRANSLATION_FORMULA_AREA_MAX", 1999, with_formula)):
        with monkeypatch.context() as m:
            m.setattr(export, cap, value)
            r = _start(user, page_id=paper["id"], mode="annotated-pdf", translation=sent)
            assert r.status_code == 413 and r.json()["detail"].startswith("a translation holds at most"), (cap, r.text)
