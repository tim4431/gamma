"""The text recognition engine (gamma/ocr_engine.py) and the page pictures it
reads (pdf_text.page_raster drawn grey, without annotations): the zoom a
page is drawn at, whether a server can recognize text at all, the walk over
Tesseract's results, and the models in backend/tessdata against the hashes
their README lists. The last tests run the real engine on a printed scan
(``ocr_fixtures.printed_scan_pdf``) and skip where tesserocr is not
installed (the mobile requirements' CI job)."""

import hashlib
import re
import shutil
from pathlib import Path

import pymupdf
import pytest
from ocr_fixtures import PRINTED_TEXT, printed_scan_pdf

from gamma import config, ocr_engine, pdf_ocr, pdf_text

TESSDATA = Path(__file__).resolve().parents[1] / "tessdata"


@pytest.fixture(autouse=True)
def _fresh_engine(monkeypatch):
    """Each test starts with no engine loaded and nothing checked, OCR on
    (as on a self-hosted server) and the repository's models."""
    monkeypatch.setenv("GAMMA_OCR", "on")
    monkeypatch.delenv("GAMMA_TESSDATA_DIR", raising=False)
    ocr_engine.reset()
    yield
    ocr_engine.reset()


# --- The zoom a page is drawn at ------------------------------------------------

def test_a_page_is_drawn_at_300_dpi_unless_that_is_too_many_pixels():
    assert ocr_engine.render_zoom(595, 842) == pytest.approx(300 / 72)            # A4
    poster = ocr_engine.render_zoom(2384, 3370)                                   # A0
    assert poster < 300 / 72 and (2384 * poster) * (3370 * poster) == pytest.approx(ocr_engine.MAX_PIXELS)
    strip = ocr_engine.render_zoom(100, 20000)                                    # a long strip
    assert 20000 * strip == pytest.approx(ocr_engine.MAX_SIDE)


# --- Whether this server recognizes text ------------------------------------------

def test_the_switch_and_where_it_is_off_by_default(monkeypatch):
    from gamma import server_settings

    monkeypatch.setattr(server_settings, "demo_mode", lambda: False)
    monkeypatch.delenv("GAMMA_OCR")
    monkeypatch.delenv("GAMMA_HOSTED", raising=False)
    assert config.ocr_enabled()
    monkeypatch.setattr(server_settings, "demo_mode", lambda: True)
    assert not config.ocr_enabled(), "a demo server: guests would share its CPU"
    monkeypatch.setattr(server_settings, "demo_mode", lambda: False)
    monkeypatch.setenv("GAMMA_HOSTED", "1")
    assert not config.ocr_enabled(), "a hosted container"
    monkeypatch.setenv("GAMMA_OCR", "on")
    assert config.ocr_enabled(), "said on, it is on"
    monkeypatch.delenv("GAMMA_HOSTED")
    monkeypatch.setenv("GAMMA_OCR", "off")
    assert not config.ocr_enabled()
    assert ocr_engine.probe() is None
    assert ocr_engine.check() == "Text recognition is turned off on this server."


def test_workers_and_the_models_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("GAMMA_OCR_WORKERS", raising=False)
    assert config.ocr_workers() == 1
    for raw, n in (("3", 3), ("0", 1), ("many", 1)):
        monkeypatch.setenv("GAMMA_OCR_WORKERS", raw)
        assert config.ocr_workers() == n
    assert config.tessdata_dir() == TESSDATA
    monkeypatch.setenv("GAMMA_TESSDATA_DIR", str(tmp_path))
    assert config.tessdata_dir() == tmp_path


def test_without_models_or_engine_or_on_a_mobile_app_nothing_is_offered(monkeypatch, tmp_path):
    installed = object()
    monkeypatch.setattr(ocr_engine.importlib.util, "find_spec", lambda name: installed)
    monkeypatch.setenv("GAMMA_TESSDATA_DIR", str(tmp_path))  # an empty folder
    assert ocr_engine.probe() is None
    assert ocr_engine.check() == "The text recognition models are missing on this server."
    assert str(tmp_path) not in ocr_engine.check(), "a reason without paths"
    ocr_engine.reset()
    monkeypatch.delenv("GAMMA_TESSDATA_DIR")
    monkeypatch.setattr(ocr_engine.importlib.util, "find_spec", lambda name: None)
    assert ocr_engine.probe() is None
    assert ocr_engine.check() == "The text recognition engine is not installed on this server."
    ocr_engine.reset()
    monkeypatch.setattr(ocr_engine, "_mobile", lambda: True)
    assert ocr_engine.check() == "Text recognition is not available in the mobile apps yet."


def test_an_older_macos_installs_no_engine_and_is_told_so(monkeypatch):
    from packaging.requirements import Requirement

    lines = (TESSDATA.parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
    marker = Requirement(next(line for line in lines if line.startswith("tesserocr"))).marker

    def installed(platform, release):
        return marker is None or marker.evaluate({"sys_platform": platform, "platform_release": release})

    assert not installed("darwin", "23.6.0"), "macOS 14: no wheel, and pip would fail to build one"
    assert installed("darwin", "24.1.0") and installed("linux", "6.8.0-45-generic") and installed("win32", "10")
    monkeypatch.setattr(ocr_engine.importlib.util, "find_spec", lambda name: None)  # what the marker leaves there
    monkeypatch.setattr(ocr_engine, "_old_macos", lambda: True)
    assert ocr_engine.probe() is None and ocr_engine.check() == "Text recognition needs macOS 15 or later."


def test_a_failed_full_check_turns_the_probe_off(monkeypatch):
    def broken(path):
        raise RuntimeError(f"Failed to init API, possibly an invalid tessdata path: {path}")

    monkeypatch.setattr(ocr_engine.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(ocr_engine, "_new_api", broken)
    assert ocr_engine.probe() == {"engine": "tesseract", "langs": ["eng", "fra", "deu"]}, "cheap: no Init"
    assert ocr_engine.check() == "The text recognition engine does not start on this server."
    assert ocr_engine.probe() is None


# --- The walk over Tesseract's results ---------------------------------------------

class _Word:
    def __init__(self, text, box, conf=90.0, starts=False):
        self.text, self.box, self.conf, self.starts = text, box, conf, starts


class _FakeTess:
    """tesserocr as ``_lines`` uses it: RIL's two levels and iterate_level
    over a list of words."""

    class RIL:
        WORD, TEXTLINE = "word", "textline"

    class _Cursor:
        def __init__(self, word):
            self.word = word

        def IsAtBeginningOf(self, level):  # noqa: N802 — tesserocr's names
            assert level == "textline"
            return self.word.starts

        def GetUTF8Text(self, level):  # noqa: N802
            if isinstance(self.word.text, Exception):
                raise self.word.text
            return self.word.text

        def BoundingBox(self, level):  # noqa: N802
            return self.word.box

        def Confidence(self, level):  # noqa: N802
            return self.word.conf

    @classmethod
    def iterate_level(cls, words, level):
        for word in words:
            yield cls._Cursor(word)


def test_the_walk_makes_lines_of_words_in_reading_order():
    words = [_Word("Quantum", (10, 10, 80, 30), 96.44, starts=True), _Word(" Zeno\n", (90, 10, 140, 30)),
             _Word("", (150, 10, 160, 30)),                      # an empty word: dropped
             _Word(None, (0, 0, 1, 1), starts=True),               # a line that holds nothing: dropped
             _Word(RuntimeError("No text returned"), (0, 0, 1, 1)),  # tesserocr's word of a blank page
             _Word("of", (10, 40, 30, 60), starts=True), _Word("atoms", None),  # no box: dropped
             _Word("ato ms", (40, 40, 90, 60))]                    # white space inside a word goes
    assert ocr_engine._lines(words, _FakeTess) == [
        [("Quantum", 10, 10, 80, 30, 96.4), ("Zeno", 90, 10, 140, 30, 90.0)],
        [("of", 10, 40, 30, 60, 90.0), ("atoms", 40, 40, 90, 60, 90.0)]]
    assert ocr_engine._lines(None, _FakeTess) == [], "no result iterator: an empty page"


def test_a_page_of_noise_has_no_result(monkeypatch):
    with pytest.raises(ocr_engine._TooMuch):
        ocr_engine._lines([_Word("x" * (ocr_engine.MAX_WORD_CHARS + 1), (0, 0, 1, 1), starts=True)], _FakeTess)
    monkeypatch.setattr(ocr_engine, "MAX_WORDS", 3)
    with pytest.raises(ocr_engine._TooMuch):
        ocr_engine._lines([_Word("a", (0, 0, 1, 1), starts=True)] * 4, _FakeTess)


class _FakeApi:
    """An engine whose recognition answers ``ok``; records what it was given."""

    made = []

    def __init__(self, ok=True):
        self.ok, self.calls, self.ended = ok, [], False
        _FakeApi.made.append(self)

    def SetImageBytes(self, *args):  # noqa: N802
        self.calls.append(("image", args[1:]))

    def SetSourceResolution(self, ppi):  # noqa: N802
        self.calls.append(("ppi", ppi))

    def Recognize(self, timeout=0):  # noqa: N802
        self.calls.append(("recognize", timeout))
        return self.ok

    def GetIterator(self):  # noqa: N802
        return None

    def Clear(self):  # noqa: N802
        self.calls.append(("clear",))

    def End(self):  # noqa: N802
        self.ended = True


def test_engines_are_lent_one_page_at_a_time_and_kept(monkeypatch):
    _FakeApi.made.clear()
    monkeypatch.setattr(ocr_engine, "_new_api", lambda path: _FakeApi())
    monkeypatch.setattr(ocr_engine, "_tesserocr", lambda: _FakeTess)
    raster, _ = pdf_text.page_raster(printed_scan_pdf(dpi=50), 1, 0.5, grey=True)
    assert ocr_engine.recognize(raster) == [] and ocr_engine.recognize(raster) == []
    (api,) = _FakeApi.made  # one engine, kept between pages
    assert api.calls[:4] == [("image", (raster.width, raster.height, 1, raster.stride)), ("ppi", 36),
                             ("recognize", ocr_engine.PAGE_TIMEOUT_MS), ("clear",)]
    ocr_engine.reset()
    assert api.ended


def test_a_page_whose_recognition_fails_has_no_result(monkeypatch):
    monkeypatch.setattr(ocr_engine, "_new_api", lambda path: _FakeApi(ok=False))
    raster, _ = pdf_text.page_raster(printed_scan_pdf(dpi=50), 1, 0.5, grey=True)
    assert ocr_engine.recognize(raster) is None


# --- The page pictures recognition reads -----------------------------------------

def _png_colour_type(png: bytes) -> int:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return png[25]


def test_a_grey_raster_records_its_zoom_and_size(tmp_path):
    data = printed_scan_pdf(dpi=72, rotate=90)
    raster, total = pdf_text.page_raster(data, 1, ocr_engine.render_zoom, grey=True)
    assert total == 1 and (raster.mode, raster.channels) == ("L", 1)
    assert (raster.width_pt, raster.height_pt) == (792, 612), "the page as shown, turned"
    assert raster.scale == pytest.approx(300 / 72) and raster.width == round(792 * raster.scale)
    assert _png_colour_type(raster.png()) == 0
    rgb, _ = pdf_text.page_raster(data, 1, 1.0)
    assert (rgb.mode, rgb.scale, _png_colour_type(rgb.png())) == ("RGB", 1.0, 2), "colour unless asked"
    grey_alpha, _ = pdf_text.page_raster(data, 1, 0.5, alpha=True, grey=True)
    assert grey_alpha.mode == "LA" and _png_colour_type(grey_alpha.png()) == 4
    image, _ = pdf_text._encode_raster(raster)
    assert image[:2] == b"\xff\xd8", "a grey raster encodes like any other"


def test_annotations_can_be_left_out_of_the_picture():
    doc = pymupdf.open()
    doc.new_page(width=200, height=200).add_freetext_annot(pymupdf.Rect(20, 20, 180, 180), "NOTE " * 20,
                                                           fontsize=20, fill_color=(0, 0, 0))
    data = doc.tobytes()
    drawn, _ = pdf_text.page_raster(data, 1, 1.0, grey=True)
    bare, _ = pdf_text.page_raster(data, 1, 1.0, grey=True, annots=False)
    assert min(drawn.data) < 128 and min(bare.data) == 255, "the note drawn, then left out"
    (page_no, raster), = pdf_text.page_rasters(data, [1], 1.0, annots=False, grey=True)
    assert raster.data == bare.data


def test_stored_lines_are_fractions_of_the_page_as_drawn():
    class Raster:  # drawn at 2 px a point: what MuPDF's rounding makes of the pixel size does not count
        scale, width_pt, height_pt, width, height = 2.0, 500.0, 250.0, 1001, 501

    assert pdf_ocr.to_lines([[("Zeno", 100, 50, 300, 100, 95.0)]], Raster) == [[["Zeno", 1000, 1000, 3000, 2000]]]


# --- The models ------------------------------------------------------------------

def test_the_models_are_the_ones_the_readme_names():
    readme = (TESSDATA / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\w+\.traineddata) \| (\d+) \| `([0-9a-f]{64})` \|$", readme, re.M)
    assert sorted(name for name, *_ in rows) == sorted(f"{lang}.traineddata" for lang in ocr_engine.LANG_LIST)
    for name, size, digest in rows:
        data = (TESSDATA / name).read_bytes()
        assert len(data) == int(size) and hashlib.sha256(data).hexdigest() == digest, name
    assert "Apache License" in (TESSDATA / "LICENSE").read_text(encoding="utf-8")
    assert "Leptonica" in (TESSDATA / "NOTICE").read_text(encoding="utf-8")


# --- The real engine -------------------------------------------------------------

@pytest.fixture
def tesserocr():
    return pytest.importorskip("tesserocr")


def test_the_engine_reads_a_printed_scan(tesserocr):
    assert ocr_engine.check() is None
    assert ocr_engine.engine().startswith("tesseract 5.5")
    raster, _ = pdf_text.page_raster(printed_scan_pdf(), 1, ocr_engine.render_zoom, annots=False, grey=True)
    lines = ocr_engine.recognize(raster)
    assert [" ".join(word[0] for word in line) + "\n" for line in lines] == PRINTED_TEXT.splitlines(keepends=True)
    assert all(word[5] >= pdf_ocr.PICTURE_CONF for line in lines for word in line)
    stored = pdf_ocr.to_lines(lines, raster)
    assert pdf_ocr.page_text(stored) == PRINTED_TEXT
    x0, y0, x1, y1 = stored[0][0][1:]  # "Quantum", printed at 72 pt from the left on a 110 pt baseline
    assert abs(x0 - round(10000 * 72 / 612)) < 40 and round(10000 * 80 / 792) < y0 < y1 < round(10000 * 116 / 792)


def test_models_under_a_path_outside_ascii_open(tesserocr, tmp_path, monkeypatch):
    folder = tmp_path / "Données d'Ève" / "tessdata"
    folder.mkdir(parents=True)
    for lang in ocr_engine.LANG_LIST:
        shutil.copyfile(TESSDATA / f"{lang}.traineddata", folder / f"{lang}.traineddata")
    monkeypatch.setenv("GAMMA_TESSDATA_DIR", str(folder))
    assert ocr_engine.check() is None
    raster, _ = pdf_text.page_raster(printed_scan_pdf(dpi=150), 1, ocr_engine.render_zoom, grey=True)
    assert "resonance" in pdf_ocr.page_text(pdf_ocr.to_lines(ocr_engine.recognize(raster), raster))


def test_a_blank_picture_reads_as_no_words(tesserocr):
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 400, 400), False)
    pix.set_rect(pix.irect, (255,))
    doc = pymupdf.open()
    doc.new_page(width=400, height=400).insert_image(pymupdf.Rect(0, 0, 400, 400), pixmap=pix)
    raster, _ = pdf_text.page_raster(doc.tobytes(), 1, ocr_engine.render_zoom, grey=True)
    assert ocr_engine.recognize(raster) == []
