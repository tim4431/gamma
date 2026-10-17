"""The PDF lock (gamma/pdf_text.py) is taken per page, not per document:
a walk over a long book lets every other PDF read in between its pages —
while every PyMuPDF call (open, each page, close) still runs under the
lock, and overlapping walks still read their documents correctly. Last,
the font reason PyMuPDF reads there: a page in a CJK font the file does
not embed renders and reads from the fonts inside the library."""

import logging
import threading
import zlib

import pymupdf
import pytest

from gamma import pdf_text

_HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _pdf(font: bytes, contents: list[bytes], page_keys: bytes = b"") -> bytes:
    """A plain US Letter PDF with one page per entry of ``contents`` (a
    content stream; ``font`` is its /F1); ``page_keys`` go into every page's
    dictionary (``b"/Rotate 90"``)."""
    objs = [font, None]
    kids = []
    for body in contents:
        data = zlib.compress(body)
        objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 1 0 R >> >> " % (len(objs)) + page_keys + b" >>")
        kids.append(len(objs))
    objs[1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
               + b"] /Count %d >>" % len(contents))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, len(objs), xref)
    return bytes(out)


def _text_pdf(pages: int, tag: str) -> bytes:
    """A PDF whose page i reads "<tag> page <i>" (Helvetica)."""
    return _pdf(_HELVETICA, [f"BT /F1 12 Tf 72 720 Td ({tag} page {i}) Tj ET".encode()
                             for i in range(1, pages + 1)])


def test_another_read_runs_between_a_books_pages(monkeypatch):
    """A book being indexed (extract_pages, each page made slow here) and a
    second document's page count + manifest walk started once the book is
    under way: the second finishes long before the book, instead of
    waiting for all of it."""
    real = pymupdf.Page.get_text
    started = threading.Event()

    def slow(self, *args, **kwargs):
        started.set()
        threading.Event().wait(0.02)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", slow)
    book, other = _text_pdf(60, "book"), _text_pdf(2, "other")
    order, got = [], {}

    def index_book():
        got["book"] = pdf_text.extract_pages(book)
        order.append("book")

    def read_other():
        started.wait(5)
        got["pages"] = pdf_text.page_count(other)
        got["sizes"] = pdf_text.page_sizes(other)
        order.append("other")

    threads = [threading.Thread(target=index_book), threading.Thread(target=read_other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert order == ["other", "book"]
    assert got["pages"] == 2 and got["sizes"] == [(612.0, 792.0)] * 2
    assert [t.strip() for t in got["book"]] == [f"book page {i}" for i in range(1, 61)]


@pytest.fixture
def lock_checks(monkeypatch):
    """Record, for each PyMuPDF call a walk makes, whether the calling
    thread held pdf_text._lock: the document's open (``pymupdf.open`` is
    the Document class), its length, each page load and the close, and on
    the page the text read and the size (``rect``, a property: wrapped by
    one that records, then delegates) and, for the short walks and the
    glyph walk, the outline, the render, the rotation and the transform
    (the last three properties, like rect); and the emptying of MuPDF's
    warning store that each open starts with."""
    held: list[tuple[str, bool]] = []

    def watch(cls, name):
        real = getattr(cls, name)
        label = f"{cls.__name__}.{name}"
        if isinstance(real, property):
            def getter(self):
                held.append((label, pdf_text._lock._is_owned()))
                return real.fget(self)

            monkeypatch.setattr(cls, name, property(getter))
            return

        def wrapper(*args, **kwargs):
            held.append((label, pdf_text._lock._is_owned()))
            return real(*args, **kwargs)

        monkeypatch.setattr(cls, name, wrapper)

    for cls, names in ((pymupdf.Document, ("__init__", "__len__", "__getitem__", "close", "get_toc")),
                       (pymupdf.Page, ("get_text", "get_pixmap", "rect", "rotation", "transformation_matrix")),
                       (pymupdf.TOOLS, ("reset_mupdf_warnings",))):
        for name in names:
            watch(cls, name)
    return held


def test_every_pymupdf_call_holds_the_lock(lock_checks):
    """The per-page walks (extract_pages, page_sizes, extract_text_pages),
    the short walks (render_page whole and cropped, outline, page_count) and
    the glyph walk, on bytes, which are never cached: each one opens the
    file."""
    data = _text_pdf(4, "locked")
    texts = pdf_text.extract_pages(data)
    assert [t.strip() for t in texts] == [f"locked page {i}" for i in range(1, 5)]
    assert pdf_text.page_sizes(data) == [(612.0, 792.0)] * 4
    text, pages = pdf_text.extract_text_pages(data, char_limit=5)  # stops after one page
    assert pages == 1 and "locked page 1" in text
    image, total = pdf_text.render_page(data, 1, 300)
    assert total == 4 and image is not None
    image, total = pdf_text.render_page(data, 2, 300, box=(0.1, 0.1, 0.6, 0.5))
    assert total == 4 and image is not None
    assert pdf_text.outline(data) == []
    assert pdf_text.page_count(data) == 4
    with pdf_text.GlyphPages(data) as glyphs:
        assert "".join(c for c, _ in glyphs.page(1)).startswith("locked page 1")
    calls = {name for name, _ in lock_checks}
    assert {"Document.__init__", "Document.__len__", "Document.__getitem__", "Document.close", "Document.get_toc",
            "Page.get_text", "Page.get_pixmap", "Page.rect", "Page.rotation", "Page.transformation_matrix",
            "TOOLS.reset_mupdf_warnings"} <= calls
    assert [name for name, owned in lock_checks if not owned] == []


def test_page_sizes_are_in_points_on_a_user_unit_page():
    """MuPDF scales page.rect by /UserUnit; pdf.js's scale-1 viewport, which
    the manifest's sizes stand in for, is not scaled, so the walk divides it
    out (pdf_text.user_unit) — on a rotated page too."""
    assert pdf_text.page_sizes(_pdf(_HELVETICA, [b""], b"/UserUnit 2")) == [(612.0, 792.0)]
    assert pdf_text.page_sizes(_pdf(_HELVETICA, [b""], b"/UserUnit 2 /Rotate 90")) == [(792.0, 612.0)]


def test_a_failed_opens_log_line_carries_its_own_warnings(monkeypatch, caplog):
    """PyMuPDF keeps every warning MuPDF raises, in any call, in one list
    for the process, and nothing of its own empties it. ``_mupdf_open``
    empties it, and a failed open reads it under the same turn of the
    lock: the line names that open's trouble, not the repairs of every
    damaged file read since the last failure."""
    real, held = pymupdf.TOOLS.mupdf_warnings, []

    def recording(reset=1):
        held.append(pdf_text._lock._is_owned())
        return real(reset=reset)

    monkeypatch.setattr(pymupdf.TOOLS, "mupdf_warnings", recording)
    good = _text_pdf(2, "damaged")
    damaged = good[:good.rfind(b"startxref")] + b"startxref\n999999\n%%EOF\n"  # a wrong offset: MuPDF repairs it, warning

    def failed_open_line() -> str:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="gamma"):
            assert pdf_text.page_sizes(b"not a pdf at all") == []
        lines = [r.message for r in caplog.records if "PyMuPDF open failed" in r.message]
        assert len(lines) == 1 and "; MuPDF: " in lines[0]
        return lines[0]

    assert [t.strip() for t in pdf_text.extract_pages(damaged)] == ["damaged page 1", "damaged page 2"]
    assert real(reset=False)  # the repair's warnings are in the list ...
    first = failed_open_line()  # ... and not on the line
    assert first == failed_open_line()
    assert held == [True, True]


def test_overlapping_walks_read_their_own_documents():
    docs = {tag: _text_pdf(30, tag) for tag in ("alpha", "beta", "gamma")}
    failures = []

    def walk(tag):
        for _ in range(5):
            texts = [t.strip() for t in pdf_text.extract_pages(docs[tag])]
            if texts != [f"{tag} page {i}" for i in range(1, 31)]:
                failures.append((tag, texts[:3]))

    threads = [threading.Thread(target=walk, args=(tag,)) for tag in docs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert failures == []


def test_a_cjk_font_the_file_does_not_embed_renders_and_reads(monkeypatch):
    """The font reason for PyMuPDF (docs/research/pypdfium2-vs-pymupdf.md):
    MuPDF carries Droid Sans Fallback inside the library, so a page in a CJK
    font the file names but does not embed — Adobe's STSong-Light through
    the UniGB-UCS2-H CMap, the usual shape of a Chinese PDF made without
    embedding — is drawn, and read as its characters, on any server, with
    no system font to find. (pdfium drew such a page from the system's
    fonts, which the Docker image had to install and an iPad does not
    offer.)"""
    text = "中文测试"
    font = (b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H"
            b" /DescendantFonts [<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light"
            b" /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> /DW 1000"
            b" /FontDescriptor << /Type /FontDescriptor /FontName /STSong-Light /Flags 4"
            b" /FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120"
            b" /CapHeight 700 /StemV 80 >> >>] >>")
    # 36 pt glyphs on the baseline y=700: in the displayed page they stand
    # at x 72..216, y 60..96 (top-left origin).
    pdf = _pdf(font, [b"BT /F1 36 Tf 72 700 Td <" + text.encode("utf-16-be").hex().encode() + b"> Tj ET"])
    assert [t.strip() for t in pdf_text.extract_pages(pdf)] == [text]

    rasters = []
    real = pdf_text._encode_raster
    monkeypatch.setattr(pdf_text, "_encode_raster", lambda raster: rasters.append(raster) or real(raster))
    image, total = pdf_text.render_page(pdf, 1, 792)  # the page's longer side: 1 pt per pixel
    assert total == 1 and image is not None and image[2:] == (612, 792)
    [raster] = rasters
    data, stride, channels = raster.data, raster.stride, raster.channels
    dark = [(x, y) for y in range(raster.height) for x in range(raster.width)
            if data[y * stride + x * channels] < 128]
    assert len(dark) > 500, "four 36 pt glyphs, not a blank page"
    assert all(60 <= x <= 230 and 50 <= y <= 110 for x, y in dark), "and drawn where the text stands"
