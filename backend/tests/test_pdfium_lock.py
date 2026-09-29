"""The pdfium lock (gamma/pdf_text.py) is taken per page, not per document:
a walk over a long book lets every other PDF read in between its pages —
while every pdfium call (open, each page, close) still runs under the lock,
and overlapping walks still read their documents correctly."""

import threading
import zlib

import pypdfium2
import pytest

from gamma import pdf_text


def _text_pdf(pages: int, tag: str) -> bytes:
    """A plain PDF whose page i reads "<tag> page <i>" (Helvetica)."""
    objs = [b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", None]
    kids = []
    for i in range(1, pages + 1):
        data = zlib.compress(f"BT /F1 12 Tf 72 720 Td ({tag} page {i}) Tj ET".encode())
        objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    b"/Resources << /Font << /F1 1 0 R >> >> >>" % (len(objs)))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % pages
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


def test_another_read_runs_between_a_books_pages(monkeypatch):
    """A book being indexed (extract_pages, each page made slow here) and a
    second document's page count + manifest walk started once the book is
    under way: the second finishes long before the book, instead of
    waiting for all of it."""
    real = pypdfium2.PdfTextPage.get_text_bounded
    started = threading.Event()

    def slow(self, *args, **kwargs):
        started.set()
        threading.Event().wait(0.02)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pypdfium2.PdfTextPage, "get_text_bounded", slow)
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
    """Record, for each pdfium call a walk makes, whether the calling thread
    held pdf_text._lock."""
    held: list[tuple[str, bool]] = []

    def watch(cls, name):
        real = getattr(cls, name)

        def wrapper(*args, **kwargs):
            held.append((f"{cls.__name__}.{name}", pdf_text._lock._is_owned()))
            return real(*args, **kwargs)

        monkeypatch.setattr(cls, name, wrapper)

    for cls, names in ((pypdfium2.PdfDocument, ("__init__", "__len__", "__getitem__", "close")),
                       (pypdfium2.PdfPage, ("get_textpage", "get_size", "close")),
                       (pypdfium2.PdfTextPage, ("get_text_bounded", "close"))):
        for name in names:
            watch(cls, name)
    return held


def test_every_pdfium_call_holds_the_lock(lock_checks):
    data = _text_pdf(4, "locked")
    texts = pdf_text.extract_pages(data)
    assert [t.strip() for t in texts] == [f"locked page {i}" for i in range(1, 5)]
    assert pdf_text.page_sizes(data) == [(612.0, 792.0)] * 4
    text, pages = pdf_text.extract_text_pages(data, char_limit=5)  # stops after one page
    assert pages == 1 and "locked page 1" in text
    calls = {name for name, _ in lock_checks}
    assert {"PdfDocument.__init__", "PdfDocument.__getitem__", "PdfDocument.close",
            "PdfPage.get_textpage", "PdfPage.get_size", "PdfTextPage.get_text_bounded"} <= calls
    assert [name for name, owned in lock_checks if not owned] == []


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
