"""A fake text recognition engine for the tests of the ``ocr`` run, its
API and the quotes it respells (gamma/ocr_run.py, routers/ocr.py,
highlight_relocate ``respell_quotes``): Tesseract's part played by MuPDF's
reading of the printed page ``ocr_fixtures.PRINTED`` draws, so a run reads
every scanned page of the fixtures in a blink and exactly.

``install(monkeypatch)`` puts it in place of gamma/ocr_engine.py's
recognize, check, probe and engine, and draws pages small (the fake reads no
pixels but to tell a printed picture from a blank one). The pages it was
given are in ``calls``."""

import pymupdf
from ocr_fixtures import GARBLE, H, PRINTED, W, printed_scan_pdf

ENGINE = "tesseract 5.5.4"
LIGHT = str.maketrans("e", "c")  # a layer that misreads one letter: what a respell takes the text of


def printed_words() -> list:
    """``PRINTED``'s words as MuPDF reads the printed page: a line a row,
    each word ``(text, x0, y0, x1, y1)`` in points of the page, top-left
    origin."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    for x, y, size, text in PRINTED:
        page.insert_text((x, y), text, fontname="helv", fontsize=size)
    lines = {}
    for x0, y0, x1, y1, word, block, line, _ in page.get_text("words"):
        lines.setdefault((block, line), []).append((word, x0, y0, x1, y1))
    return list(lines.values())


def printed_lines() -> list:
    """``printed_words`` as pdf_ocr stores them: in 1/10000 of the page."""
    return [[[w, round(1e4 * x0 / W), round(1e4 * y0 / H), round(1e4 * x1 / W), round(1e4 * y1 / H)]
             for w, x0, y0, x1, y1 in line] for line in printed_words()]


def layered_scan(translate=LIGHT) -> bytes:
    """A scan of ``PRINTED`` under an invisible layer that spells it through
    ``translate`` (``LIGHT``: one letter misread; ``GARBLE``: six)."""
    doc = pymupdf.open(stream=printed_scan_pdf(dpi=72), filetype="pdf")
    page = doc[0]
    for x, y, size, text in PRINTED:
        page.insert_text((x, y), text.translate(translate), fontname="helv", fontsize=size, render_mode=3)
    return doc.tobytes()


def joined(*parts: bytes) -> bytes:
    """One PDF of the pages of ``parts``, in order."""
    doc = pymupdf.open()
    for part in parts:
        doc.insert_pdf(pymupdf.open(stream=part, filetype="pdf"))
    return doc.tobytes()


class Fake:
    """The engine: a page whose picture holds ink where ``PRINTED`` is
    printed reads ``PRINTED``, any other nothing (a blank picture, a stamp
    line at its foot); ``conf`` is every word's confidence, ``fail`` the
    calls (1 the first) that have no result."""

    def __init__(self, conf: float = 90.0):
        self.conf = conf
        self.calls = 0
        self.fail: set = set()
        self.words = printed_words()

    def recognize(self, raster):
        self.calls += 1
        if self.calls in self.fail:
            return None
        s = raster.scale
        rows = raster.data[int(80 * s) * raster.stride:int(250 * s) * raster.stride]  # where PRINTED is printed
        if not rows or min(rows) > 200:
            return []
        return [[(w, x0 * s, y0 * s, x1 * s, y1 * s, self.conf) for w, x0, y0, x1, y1 in line] for line in self.words]


def install(monkeypatch, conf: float = 90.0) -> Fake:
    from gamma import ocr_engine

    fake = Fake(conf)
    monkeypatch.setattr(ocr_engine, "recognize", fake.recognize)
    monkeypatch.setattr(ocr_engine, "render_zoom", lambda w, h: 0.5)
    monkeypatch.setattr(ocr_engine, "check", lambda: None)
    monkeypatch.setattr(ocr_engine, "probe", lambda: {"engine": "tesseract", "langs": list(ocr_engine.LANG_LIST)})
    monkeypatch.setattr(ocr_engine, "engine", lambda: ENGINE)
    return fake


__all__ = ["ENGINE", "GARBLE", "LIGHT", "Fake", "install", "joined", "layered_scan", "printed_lines", "printed_words"]
