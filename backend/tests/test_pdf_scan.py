"""Scanned pages under an invisible OCR layer (gamma/pdf_scan.py) and what
pdf_text makes of them: the page text puts the words of one visual row back
on one line, the glyph walk parts them with a boxless space, and a
born-digital page reads exactly as before."""

import pymupdf
import pytest
from ocr_fixtures import ROW_TEXT, ROWS, WORD_TEXT, H, W, mupdf_glyphs, scan_pdf

from gamma import pdf_scan, pdf_text
from gamma.pdf_text import GlyphPages, has_box


@pytest.fixture(autouse=True)
def _flags():
    pdf_text._pymupdf()  # sets _TEXT_FLAGS and _RAWDICT_FLAGS, as every open does


def _page(pdf_bytes):
    return pymupdf.open(stream=pdf_bytes, filetype="pdf")[0]


SCAN = scan_pdf()
BORN_DIGITAL = scan_pdf(invisible=False, image=None)


def test_the_fixture_reads_a_word_a_line_as_the_scans_do():
    assert _page(SCAN).get_text("text", flags=pdf_text._TEXT_FLAGS) == WORD_TEXT


def test_a_picture_under_invisible_text_is_a_scan():
    assert pdf_scan.is_scan_page(_page(SCAN))
    page = _page(SCAN)
    built = []

    def blocks():
        built.append(1)
        return page.get_text("dict", flags=pdf_text._TEXT_FLAGS)["blocks"]

    assert pdf_scan.is_scan_page(page, blocks) and built == [1], "the caller's dictionary is used"
    built.clear()
    assert not pdf_scan.is_scan_page(_page(BORN_DIGITAL), blocks) and built == [], \
        "a page with no invisible render mode is decided before any dictionary is built"


@pytest.mark.parametrize("stream, found", [
    (b"BT 3 Tr /F1 9 Tf (a) Tj ET", True),
    (b"q\n7 Tr\nBT", True),
    (b"3.0 Tr", True),
    (b"13 Tr 0 Tr", False),           # mode 13 is no mode; 0 is visible
    (b"/Trans 3 Tf 1 Tr", False),     # a name, then a visible mode
    (b"(3 Tr) Tj", True),             # inside a string: a false "maybe", which the glyph step settles
    (b"", False),
])
def test_the_byte_scan_finds_an_invisible_render_mode(stream, found):
    assert pdf_scan._sets_invisible(stream) is found


def _resaved(pdf_bytes, change) -> bytes:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    change(doc)
    return doc.tobytes()


def test_a_turned_scan_with_margins_is_a_scan_in_its_text_and_its_glyphs():
    # /Rotate 90 with a picture short of the page by 40 pt each way (as the
    # APS scans are): the coverage is measured in the unrotated space the
    # image boxes come in.
    turned = _resaved(scan_pdf(image=(40, 40, W - 40, H - 40)), lambda d: d[0].set_rotation(90))
    page = _page(turned)
    assert page.rotation == 90 and pdf_scan.is_scan_page(page)
    assert pdf_text._text(page).count("\n") < WORD_TEXT.count("\n")
    assert any(c == " " and box == (0.0, 0.0, 0.0, 0.0) for c, box in pdf_text._glyphs(_page(turned)))


def test_a_scan_whose_page_inherits_its_resources_is_found():
    src = pymupdf.open(stream=SCAN, filetype="pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.show_pdf_page(page.rect, src, 0)  # the scan as one form, named in the page's resources
    kind, resources = doc.xref_get_key(page.xref, "Resources")
    pages = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
    doc.xref_set_key(pages, "Resources", resources)
    doc.xref_set_key(page.xref, "Resources", "null")
    inherited = _page(doc.tobytes())
    assert inherited.parent.xref_get_key(inherited.xref, "Resources")[0] == "null"
    assert pdf_scan.is_scan_page(inherited)


def test_an_annotation_on_a_scan_does_not_hide_it():
    # pdf.js's operator list stops at the annotations; the server's glyph
    # count leaves out the text in their boxes too.
    def note(doc):
        doc[0].add_freetext_annot(pymupdf.Rect(30, 30, 400, 200), "a reader's long note " * 30, fontsize=9)
    annotated = _page(_resaved(SCAN, note))
    assert annotated.first_annot is not None
    assert pdf_scan.is_scan_page(annotated)


@pytest.mark.parametrize("pdf_bytes", [
    BORN_DIGITAL,                                        # the same words, drawn
    scan_pdf(image=None),                                # invisible words, no picture
    scan_pdf(image=(50, 300, 250, 400)),                 # invisible words over a small picture
    scan_pdf(image=(50, 300, 250, 400), size=0),         # LaTeXiT's size-0 source text by an equation
    scan_pdf(invisible=False),                           # a picture under drawn words
], ids=["born-digital", "no-picture", "small-picture", "size-0", "visible-over-picture"])
def test_pages_that_are_not_scans(pdf_bytes):
    assert not pdf_scan.is_scan_page(_page(pdf_bytes))


def test_a_picture_without_text_is_no_scan():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=pymupdf.open(stream=SCAN, filetype="pdf")[0].get_pixmap(dpi=10).tobytes())
    assert not pdf_scan.is_scan_page(page)


def test_invisible_text_in_a_form_is_found():
    src = pymupdf.open(stream=SCAN, filetype="pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
    page.show_pdf_page(page.rect, src, 0)  # the whole scan as one form XObject
    assert b"Tr" not in page.read_contents()
    assert pdf_scan.is_scan_page(page)


def test_rows_chain_lines_in_order():
    assert pdf_scan.rows([]) == []
    assert pdf_scan.rows([(0, 0, 10, 10)]) == []
    line = (10, 100, 40, 110)
    assert pdf_scan.rows([
        line,
        (50, 101, 80, 112),    # on the band: same row
        (90, 96, 100, 106),    # a superscript overlapping the band by half: same row
        (110, 120, 140, 130),  # below the band: a new row
        (100, 121, 105, 131),  # left of the line before it: a new row
        (200, 121, 230, 131),  # past three row heights: a column gutter, a new row
        (240, 121, 260, 131),
    ]) == [False, False, True, True, True, False]
    # A whole line MuPDF read as one, then the next line, tall with a
    # formula and so overlapping it: it starts before the first one ends.
    assert pdf_scan.rows([(306, 270, 513, 284), (306, 275, 497, 297)]) == [True]


def test_rows_measure_against_the_rows_median_band():
    # One tall word does not drag the band: the next word at the row's
    # height still joins it, and one well below it does not.
    first, tall = (0, 100, 10, 110), (20, 92, 30, 118)
    assert pdf_scan.rows([first, (12, 101, 18, 110), tall, (40, 100, 50, 110)]) == [False, False, False]
    assert pdf_scan.rows([first, (12, 101, 18, 110), tall, (40, 107, 50, 117)]) == [False, False, True]


def test_a_scan_page_text_joins_each_row_and_keeps_the_order_and_the_length():
    page = _page(SCAN)
    text = pdf_text._text(page)
    assert text == ROW_TEXT
    assert len(text) == len(WORD_TEXT)
    assert text.replace(" ", "\n") == WORD_TEXT  # only separators changed


def test_a_born_digital_page_reads_as_before():
    page = _page(BORN_DIGITAL)
    assert pdf_text._text(page) == page.get_text("text", flags=pdf_text._TEXT_FLAGS)
    assert pdf_text._glyphs(page) == mupdf_glyphs(_page(BORN_DIGITAL), pdf_text._RAWDICT_FLAGS)


def test_a_scan_page_glyphs_part_a_row_with_a_boxless_space():
    glyphs = pdf_text._glyphs(_page(SCAN))
    before = mupdf_glyphs(_page(SCAN), pdf_text._RAWDICT_FLAGS)
    assert [g for g in glyphs if has_box(g[1])] == [g for g in before if has_box(g[1])]
    assert "".join(ch for ch, _box in glyphs) == ROW_TEXT
    assert all(not has_box(box) for ch, box in glyphs if ch.isspace())


def test_extract_pages_reads_the_rows_through_the_cache(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(SCAN)
    pdf_text.text_cache_clear()
    try:
        assert pdf_text.extract_pages(str(path)) == [ROW_TEXT]
        assert pdf_text._text_entry(pdf_text._text_key(str(path))).pages == [ROW_TEXT]
        assert pdf_text.extract_pages(str(path)) == [ROW_TEXT]
        assert pdf_text.extract_pages(SCAN) == [ROW_TEXT]
    finally:
        pdf_text.text_cache_clear()
    with GlyphPages(SCAN) as glyphs:
        assert "".join(ch for ch, _box in glyphs.page(1)) == ROW_TEXT
