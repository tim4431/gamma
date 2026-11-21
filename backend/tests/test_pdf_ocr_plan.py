"""What a run reads of a file (gamma/pdf_ocr.py ``plan_of``, shared by the
run on the server and the run in a window): the sample first, the sample's
verdict on the file's own text, the pages a run found no result on left
out; the kinds a run records (``set_scanned``), the engine each page
records and the file's engines once applied, and the page rules over a
window's words (``stage_words``)."""

from ocr_fake import install, joined, layered_scan
from ocr_fixtures import printed_scan_pdf
from test_pdf_ocr_store import LINES, store

from gamma import pdf_ocr

WS, DOC = "wplan", "f" * 24
KINDS = {1: "scan", 2: "picture", 3: "scan", 4: "scan", 5: "scan", 7: "picture"}  # page 6 born-digital


def _state(recognized=(), kept=(), staged=(), shared=None):
    return {"recognized": list(recognized), "kept": list(kept), "staged": list(staged), "shared": shared or {}}


def test_a_run_reads_its_sample_first_then_every_other_scanned_page():
    plan = pdf_ocr.plan_of(None, KINDS)
    assert plan == {"sample": [1, 3, 4], "done": [], "todo": [1, 3, 4, 2, 5, 7], "good_layer": None}, \
        "the verdict waits for the sample: every layered page may still be read"
    assert pdf_ocr.plan_of(None, {2: "picture"}) == {"sample": [], "done": [], "todo": [2], "good_layer": False}


def test_a_sample_whose_layers_read_well_leaves_the_other_layered_pages():
    good = _state(kept=[1, 3, 4], shared={1: 0.99, 3: 0.95, 4: 0.93})
    assert pdf_ocr.plan_of(good, KINDS) == {"sample": [1, 3, 4], "done": [1, 3, 4], "todo": [2, 7],
                                            "good_layer": True}
    staged = _state(kept=[1, 3, 4], staged=[2], shared={1: 0.99, 3: 0.95, 4: 0.93})
    assert pdf_ocr.plan_of(staged, KINDS)["todo"] == [7], "a page staged before is done"
    half = _state(kept=[1], shared={1: 0.99})
    assert pdf_ocr.plan_of(half, KINDS) == {"sample": [1, 3, 4], "done": [1], "todo": [3, 4, 2, 5, 7],
                                            "good_layer": None}


def test_one_sample_page_that_does_not_keep_its_layer_well_reads_them_all():
    for found in (_state(kept=[1, 3], recognized=[4], shared={1: 0.99, 3: 0.99, 4: 0.4}),   # replaced
                  _state(kept=[1, 3, 4], shared={1: 0.99, 3: 0.99, 4: 0.5}),                # kept, read too few
                  _state(kept=[1, 3], staged=[4], shared={1: 0.99, 3: 0.99, 4: 0.2})):     # staged, not applied
        plan = pdf_ocr.plan_of(found, KINDS)
        assert plan["good_layer"] is False and plan["todo"] == [2, 5, 7], found


def test_the_pages_a_run_found_no_result_on_are_left_out():
    plan = pdf_ocr.plan_of(_state(kept=[1, 4], shared={1: 0.99, 4: 0.99}), KINDS, failed={3, 7})
    assert plan["good_layer"] is False, "a sample page without a result does not keep its layer"
    assert plan["todo"] == [2, 5], "not read again by this run"
    again = pdf_ocr.plan_of(_state(kept=[1, 4], shared={1: 0.99, 4: 0.99}), KINDS)
    assert again["todo"] == [3, 2, 5, 7] and again["good_layer"] is None, "the next run reads them again"


def test_a_run_records_the_kinds_it_classified(data_dir, monkeypatch):
    install(monkeypatch)
    path = store(WS, DOC, joined(layered_scan(), printed_scan_pdf(dpi=72)))
    total, kinds = pdf_ocr.classify(str(path))
    assert (total, kinds) == (2, {1: "scan", 2: "picture"})
    assert pdf_ocr.state(WS, DOC) is None
    pdf_ocr.run(WS, DOC, str(path), actor="u-plan")
    found = pdf_ocr.state(WS, DOC)
    assert found["kinds"] == {1: "scan", 2: "picture"} and found["scanned"] == 2
    pdf_ocr.set_scanned(WS, "e" * 24, 3, 1)
    assert pdf_ocr.state(WS, "e" * 24)["kinds"] is None, "not classified by a run: none recorded"


def test_each_page_records_its_engine_and_the_file_all_of_them(data_dir):
    store(WS, DOC)
    pdf_ocr.set_scanned(WS, DOC, 3, 3, {1: "picture", 2: "picture", 3: "picture"})
    pdf_ocr.stage_page(WS, DOC, 1, LINES, engine="tesseract 5.5.4")  # a run stopped on the server...
    words = [[[*word, 90.0] for word in line] for line in LINES * 5]  # ...gone on in a window
    assert pdf_ocr.stage_words(WS, DOC, 2, "picture", words, "", "tesseract.js 7.0.0") == ("recognized", None)
    pdf_ocr.stage_page(WS, DOC, 3, LINES)  # recorded by none: the apply's engine
    pdf_ocr.apply(WS, DOC, "tesseract 5.5.6")
    found = pdf_ocr.state(WS, DOC)
    assert found["engine"] == "tesseract 5.5.4 + tesseract 5.5.6 + tesseract.js 7.0.0"
    assert found["applied"] == [1, 2, 3] and found["recognized"] == [1, 2, 3]


def test_a_windows_empty_words_count_for_nothing(data_dir):
    store(WS, DOC)
    pdf_ocr.set_scanned(WS, DOC, 1, 1, {1: "picture"})
    sure = [["word", 100 * n, 100, 100 * n + 90, 200, 95.0] for n in range(pdf_ocr.PICTURE_WORDS - 1)]
    blanks = [[" ", 0, 0, 10, 10, 99.0], ["\t", 0, 0, 10, 10, 99.0]]
    assert pdf_ocr.stage_words(WS, DOC, 1, "picture", [sure + blanks], "", "tesseract.js 7.0.0") == ("empty", None), \
        "nine sure words and two blank ones: no real text"
    assert pdf_ocr.stage_words(WS, DOC, 1, "picture", None, "", "x") == ("failed", None)
    assert pdf_ocr.state(WS, DOC)["staged"] == [1], "the empty page stays staged; a failed one stages nothing"
