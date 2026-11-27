"""Every PDF reader through the recognized text (gamma/pdf_text.py with
gamma/pdf_ocr.py): on the pages the recognition applied to, the page text,
the glyphs under a highlight, the brief's scan test, the title guess, the
search index and the AI's excerpt read the recognized words, and every
other page reads the file as before; ``ocr=False`` reads the file's own
layer; the page-text cache keys on the recognized text's stamp; a turned
and cropped page places the recognized glyphs where the viewer draws them.
The recognized words here are what MuPDF reads of the printed page (a fake
engine); the last test runs the real one."""

import pymupdf
import pytest
from ocr_fixtures import GARBLED, H, PRINTED, PRINTED_TEXT, W, printed_scan_pdf
from test_pdf_ocr_store import DOC, WS, store
from test_pdf_text_lock import lock_checks  # noqa: F401 — the fixture

from gamma import ocr_run, pdf_ocr, pdf_text
from gamma.pdf_export import viewer_rect_to_pdf
from gamma.pdf_text import GlyphPages, quote_under

BORN_DIGITAL_TEXT = "A born-digital cover page of the paper.\n"


@pytest.fixture(autouse=True)
def _fresh_cache():
    pdf_text.text_cache_clear()
    yield
    pdf_text.text_cache_clear()


def printed_lines() -> list:
    """``PRINTED`` as a recognition of its scan gives it (a fake engine):
    its words with MuPDF's boxes of the printed page, in 1/10000 of the
    page, a line a row."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    for x, y, size, text in PRINTED:
        page.insert_text((x, y), text, fontname="helv", fontsize=size)
    lines = {}
    for x0, y0, x1, y1, word, block, line, _ in page.get_text("words"):
        lines.setdefault((block, line), []).append(
            [word, round(1e4 * x0 / W), round(1e4 * y0 / H), round(1e4 * x1 / W), round(1e4 * y1 / H)])
    return list(lines.values())


def mixed_pdf() -> bytes:
    """Page 1 born-digital, page 2 a scan under a garbled layer, page 3 a
    scan without a layer."""
    doc = pymupdf.open()
    doc.new_page(width=W, height=H).insert_text((72, 72), BORN_DIGITAL_TEXT.strip(), fontsize=12)
    for layer in ("garbled", None):
        doc.insert_pdf(pymupdf.open(stream=printed_scan_pdf(layer=layer, dpi=100), filetype="pdf"))
    return doc.tobytes()


def recognize(path, pages, lines=None, doc_id=DOC):
    """The run's end as the readers see it: ``pages`` staged with
    ``lines`` and applied."""
    total = pdf_text.page_count(str(path))
    pdf_ocr.set_scanned(WS, doc_id, total, len(pages))
    for page in pages:
        pdf_ocr.stage_page(WS, doc_id, page, printed_lines() if lines is None else lines)
    return pdf_ocr.apply(WS, doc_id, "tesseract 5.5.4")


def test_the_fake_recognition_spells_the_printed_page():
    assert pdf_ocr.page_text(printed_lines()) == PRINTED_TEXT


def test_the_page_text_reads_the_recognized_words_where_they_were_applied(data_dir):
    path = store(data=mixed_pdf())
    src = str(path)
    layer = pdf_text.extract_pages(src)
    assert layer[0] == BORN_DIGITAL_TEXT and "rc50m4mcc" in layer[1] and layer[2] == ""
    recognize(path, [2, 3])
    assert pdf_text.extract_pages(src) == [BORN_DIGITAL_TEXT, PRINTED_TEXT, PRINTED_TEXT]
    assert pdf_text.extract_pages(src, cache=False) == [BORN_DIGITAL_TEXT, PRINTED_TEXT, PRINTED_TEXT]
    assert list(pdf_text.iter_page_texts(src, start_page=3)) == [PRINTED_TEXT]
    assert "resonance" in pdf_text.extract_text(src, 10_000)
    text, pages = pdf_text.extract_text_pages(src, 10_000, label_pages=True)
    assert pages == 3 and "[PDF page 2]\nQuantum Zeno dynamics" in text
    assert pdf_text.extract_pages(src, ocr=False) == layer, "ocr=False: the file's own layer"
    assert "resonance" not in pdf_text.extract_text(src, 10_000, ocr=False)
    assert pdf_text.extract_pages(path.read_bytes())[1] == layer[1], "bytes have no recognized text"


def test_a_file_read_from_its_recognized_text_alone_is_never_opened(data_dir, monkeypatch):
    path = store(data=printed_scan_pdf(dpi=72))
    recognize(path, [1])
    opens = []
    real_open = pdf_text._open
    monkeypatch.setattr(pdf_text, "_open", lambda src: opens.append(src) or real_open(src))
    assert pdf_text.extract_pages(str(path), cache=False) == [PRINTED_TEXT]
    assert pdf_text.title_guess(str(path)) == "Quantum Zeno dynamics of entangled atoms"
    assert opens == []


def test_the_cache_keys_on_the_stamp(data_dir, monkeypatch):
    path = store(data=mixed_pdf())
    src = str(path)
    layer = pdf_text.extract_pages(src)             # read and kept before any recognition
    first = recognize(path, [2])
    assert pdf_text.extract_pages(src)[1] == PRINTED_TEXT, "the text recognized later is read at once"
    opens = []
    real_open = pdf_text._open
    monkeypatch.setattr(pdf_text, "_open", lambda s: opens.append(s) or real_open(s))
    assert pdf_text.extract_pages(src, ocr=False) == layer and opens == [], "the layer's entry, kept"
    pdf_ocr.stage_page(WS, DOC, 3, printed_lines())
    second = pdf_ocr.apply(WS, DOC, "tesseract 5.5.4")
    assert pdf_text.extract_pages(src)[2] == PRINTED_TEXT
    stamps = [key[3] for key in pdf_text._text_cache if key[0] == src]
    assert first["stamp"] not in stamps and None in stamps and second["stamp"] in stamps, \
        "an entry of an older stamp goes; the layer's stays"
    assert pdf_text.page_count(src) == 3


def test_opening_is_scan_for_a_recognized_opening(data_dir):
    path = store(data=mixed_pdf())
    src = str(path)
    assert pdf_text.opening_is_scan(src, 10_000), "the garbled layer is a scan anyway"
    recognize(path, [2, 3])
    assert not pdf_text.opening_is_scan(src, 10), "the opening is the born-digital page"
    assert pdf_text.opening_is_scan(src, 10_000)
    picture = store(doc_id="p" * 24, data=printed_scan_pdf(dpi=72))
    assert not pdf_text.opening_is_scan(str(picture), 10_000), "no text at all: nothing read from a scan"
    recognize(picture, [1], doc_id="p" * 24)
    assert pdf_text.opening_is_scan(str(picture), 10_000), "Tesseract still misreads: the brief is told"


def test_the_title_guess_reads_a_recognized_first_page(data_dir):
    path = store(data=printed_scan_pdf(dpi=72))
    assert pdf_text.title_guess(str(path)) == ""
    recognize(path, [1])
    assert pdf_text.title_guess(str(path)) == "Quantum Zeno dynamics of entangled atoms"
    assert pdf_text.title_guess(str(path), ocr=False) == ""


def _quads(rects, frame, size=(1.0, 1.0)):
    """The viewer's rects (fractions of the page as shown) in user space,
    as highlight_relocate maps a highlight's rects."""
    view, rotation = frame
    return [viewer_rect_to_pdf({"x1": x1, "y1": y1, "x2": x2, "y2": y2}, size, rotation, view)
            for x1, y1, x2, y2 in rects]


def _word_rect(lines, word: str, first=0.0, last=1.0, line_pad=0.0):
    """The viewer rect over the share ``first``..``last`` of ``word``."""
    for line in lines:
        top = sorted(w[2] for w in line)[len(line) // 2] / 1e4
        bottom = sorted(w[4] for w in line)[len(line) // 2] / 1e4
        for text, x0, _y0, x1, _y1 in line:
            if text == word:
                u0, u1 = x0 / 1e4, x1 / 1e4
                return (u0 + (u1 - u0) * first, top - line_pad, u0 + (u1 - u0) * last, bottom + line_pad)
    raise AssertionError(word)


def test_the_glyphs_under_a_highlight_are_the_recognized_words(data_dir):
    path = store(data=mixed_pdf())
    recognize(path, [2, 3])
    lines = printed_lines()
    with GlyphPages(str(path)) as glyphs:
        assert "".join(ch for ch, _box in glyphs.page(2)) == PRINTED_TEXT, "they spell the page text"
        assert "".join(ch for ch, _box in glyphs.page(1)).strip() == BORN_DIGITAL_TEXT.strip()
        frame = glyphs.frame(2)
        assert quote_under(glyphs.page(2), _quads([_word_rect(lines, "resonance")], frame)) == "resonance"
        both = [_word_rect(lines, "Lounis"), _word_rect(lines, "twice.")]
        assert quote_under(glyphs.page(3), _quads(both, frame)) == "Lounis twice."
    with GlyphPages(str(path), ocr=False) as glyphs:
        assert "rc50m4mcc" in "".join(ch for ch, _box in glyphs.page(2)).replace("\n", " ")
        assert glyphs.page(3) == []


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_half_a_word_on_a_turned_and_cropped_page_reads_that_half(data_dir, rotation):
    doc = pymupdf.open(stream=printed_scan_pdf(dpi=36), filetype="pdf")
    doc.xref_set_key(doc[0].xref, "CropBox", "[40 30 580 760]")
    doc[0].set_rotation(rotation)
    path = store(data=doc.tobytes())
    # Lines in the page as shown, whatever it shows: only the frame places them.
    lines = [[["Quantum", 1000, 1000, 2600, 1400], ["resonance", 3000, 1000, 6000, 1400]],
             [["twice", 1000, 2000, 2000, 2400]]]
    recognize(path, [1], lines=lines)
    with GlyphPages(str(path)) as glyphs:
        frame = glyphs.frame(1)
        assert frame[1] == rotation
        page = glyphs.page(1)
        assert quote_under(page, _quads([_word_rect(lines, "resonance", 0.52, 1.0)], frame)) == "ance"
        assert quote_under(page, _quads([_word_rect(lines, "resonance", 0.0, 0.48)], frame)) == "reso"
        assert quote_under(page, _quads([_word_rect(lines, "twice")], frame)) == "twice"
        x0, y0, x1, y1 = viewer_rect_to_pdf({"x1": 0, "y1": 0, "x2": 1, "y2": 1}, (1.0, 1.0), *frame[::-1])
        assert all(x0 - 1e-6 <= b[0] <= b[2] <= x1 + 1e-6 and y0 - 1e-6 <= b[1] <= b[3] <= y1 + 1e-6
                   for ch, b in page if ch.strip()), "every glyph on the page shown"


def test_a_superscript_on_the_line_is_under_the_lines_rectangle(data_dir):
    path = store(data=printed_scan_pdf(dpi=36))
    # "atoms" boxed above the others (a superscript, a stray mark): the line's band holds it.
    lines = [[["entangled", 1000, 1000, 3000, 1400], ["atoms", 3200, 700, 4000, 1000],
              ["here", 4200, 1000, 5000, 1400]]]
    recognize(path, [1], lines=lines)
    with GlyphPages(str(path)) as glyphs:
        rect = (0.1, 0.1, 0.5, 0.14)  # the line's band, as the viewer tiles it
        assert quote_under(glyphs.page(1), _quads([rect], glyphs.frame(1))) == "entangled atoms here"


def test_the_search_index_and_the_ai_read_the_recognized_words(data_dir):
    from gamma import ai_context
    from gamma.db import connect_data_db
    from gamma.search import index, indexer

    path = store(data=mixed_pdf())
    recognize(path, [2, 3])
    found = indexer.index_doc(WS, index.Doc(DOC, "pdf", ".pdf", "store"))
    with connect_data_db(WS) as conn:
        index.store_docs(conn, [found])
        hits = index.search(conn, "resonance", 10, [DOC])
        assert {hit.unit for hit in hits} == {2, 3}
        assert index.search(conn, "rc50m4mcc", 10, [DOC]) == []
    text, _next, _seen = ai_context.pdf_excerpt(WS, DOC, 5000)
    assert "Lounis and Cohen measured the resonance twice." in text and "rc50m4mcc" not in text
    assert GARBLED.splitlines()[-1] not in text


def test_every_pymupdf_call_on_the_recognized_paths_holds_the_lock(data_dir, lock_checks):  # noqa: F811
    path = store(data=mixed_pdf())
    recognize(path, [2])
    src = str(path)
    lock_checks.clear()  # the fixture's own drawing, outside the readers
    assert pdf_text.extract_pages(src)[1] == PRINTED_TEXT
    assert pdf_text.opening_is_scan(src, 10_000)
    assert pdf_text.title_guess(src).startswith("A born-digital")
    with GlyphPages(src) as glyphs:
        assert glyphs.page(2) and glyphs.frame(2)
    raster, _ = pdf_text.page_raster(src, 2, 0.5, annots=False, grey=True)
    assert raster.mode == "L"
    assert [n for n, _ in pdf_text.page_rasters(src, [3, 2], 0.25, annots=False, grey=True)] == [3, 2]
    assert [kind for _, kind in pdf_text.page_kinds(src)] == ["", "scan", "picture"]
    assert lock_checks and [name for name, owned in lock_checks if not owned] == []


def test_the_real_engine_end_to_end(data_dir):
    pytest.importorskip("tesserocr")
    from gamma import ocr_engine

    assert ocr_engine.check() is None
    path = store(data=printed_scan_pdf(layer="garbled"))
    src = str(path)
    assert "resonance" not in pdf_text.extract_text(src, 10_000)
    pdf_ocr.set_scanned(WS, DOC, 1, 1)
    (page_no, raster), = pdf_text.page_rasters(src, [1], ocr_engine.render_zoom, annots=False, grey=True)
    pdf_ocr.stage_page(WS, DOC, page_no, ocr_run.to_lines(ocr_engine.recognize(raster), raster))
    pdf_ocr.apply(WS, DOC, ocr_engine.engine())
    assert pdf_text.extract_text(src, 10_000) == PRINTED_TEXT
    lines = pdf_ocr.page_lines(WS, DOC, 1)
    with GlyphPages(src) as glyphs:
        assert quote_under(glyphs.page(1), _quads([_word_rect(lines, "resonance")], glyphs.frame(1))) == "resonance"
    assert pdf_text.title_guess(src) == "Quantum Zeno dynamics of entangled atoms"
