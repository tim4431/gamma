"""The ``ocr`` run (gamma/pdf_ocr.py ``run``) with a fake engine
(tests/ocr_fake.py): which pages it reads and what each becomes (a layer
kept or replaced, a picture page read, keeping its own text or empty,
born-digital pages never touched), the sample that stops it on a file
whose own text reads well, nothing anyone sees changing until it applies,
a stopped run going on where it stopped, an interrupted finish done by the
next run, and what the finishing steps do: the search index (and a text
read before the apply kept out of it), the briefs, the purge."""

import pytest
from ocr_fake import GARBLE, LIGHT, install, joined, layered_scan
from ocr_fixtures import PRINTED_TEXT, W, H, picture_pdf, printed_scan_pdf
from test_pdf_ocr_store import store

from gamma import db, jobs, pdf_ocr, pdf_text, translate, translate_store
from gamma.search import index

WS, DOC = "wrun", "c" * 24
ACTOR = "u-ocr-run"


@pytest.fixture(autouse=True)
def _fresh_cache():
    pdf_text.text_cache_clear()
    yield
    pdf_text.text_cache_clear()


def born_digital() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=W, height=H).insert_text((72, 72), "A born-digital page of the paper.", fontsize=12)
    return doc.tobytes()


def run(path, progress=None, doc_id=DOC):
    return pdf_ocr.run(WS, doc_id, str(path), actor=ACTOR, progress=progress)


def test_a_mixed_file_reads_its_scans_and_pictures_and_leaves_its_born_digital_pages(data_dir, monkeypatch):
    fake = install(monkeypatch)
    path = store(WS, DOC, joined(born_digital(), layered_scan(GARBLE), printed_scan_pdf(dpi=72), picture_pdf(),
                                 picture_pdf(footer="Downloaded from a library on 1 April 1992")))
    reports = []
    result = run(path, lambda **p: reports.append(p))
    assert result == {"scanned": 4, "recognized": 2, "kept": 1, "empty": 1, "failed": 0, "good_layer": False,
                      "shared": 0.0, "highlights_respelled": 0, "highlights_kept": 0, "highlights_changed": 0}
    assert fake.calls == 4, "the born-digital page is never drawn"
    found = pdf_ocr.state(WS, DOC)
    assert (found["pages"], found["scanned"], found["recognized"], found["kept"], found["pending"]) == \
        (5, 4, [2, 3, 4], [5], False)
    texts = pdf_text.extract_pages(str(path))
    assert texts[0].startswith("A born-digital page") and texts[1:3] == [PRINTED_TEXT, PRINTED_TEXT]
    assert texts[3] == "", "a plate without text of its own reads no text"
    assert texts[4] == "Downloaded from a library on 1 April 1992\n", "a stamp line under a picture is its own"
    # the phases, each in pages; the recognizing phase says when it began and from which count
    phases = [p.get("phase") for p in reports]
    assert phases[0] == "reading" and "recognizing" in phases and phases[-1] == "updating"
    reading = [p for p in reports if p.get("phase") == "reading"]
    assert reading[-1]["done"] == reading[-1]["total"] == 5 and reading[-1]["unit"] == "pages"
    recognizing = [p for p in reports if p.get("phase") == "recognizing"]
    assert recognizing[0]["done"] == 0 and recognizing[-1]["done"] == recognizing[-1]["total"] == 4
    assert all(p["from"] == 0 and isinstance(p["since"], int) for p in recognizing)
    assert reports[-1] == {"phase": "updating", "stoppable": False}


def test_a_good_layer_is_kept_and_the_sample_stops_the_run(data_dir, monkeypatch):
    fake = install(monkeypatch)
    true_layer = printed_scan_pdf(layer="true", dpi=72)
    path = store(WS, DOC, joined(true_layer, true_layer, true_layer, true_layer, printed_scan_pdf(dpi=72)))
    result = run(path)
    assert result["good_layer"] and result["shared"] == 1.0 and result["kept"] == 3 and result["recognized"] == 1
    assert fake.calls == 4, "the sample's three pages and the picture page; the fourth layered page is not read"
    found = pdf_ocr.state(WS, DOC)
    assert found["kept"] == [1, 2, 3] and found["recognized"] == [5] and pdf_ocr.reads_well(found), \
        "page 4 is left unread: the file's own text reads well"
    assert pdf_text.extract_pages(str(path))[:4] == pdf_text.extract_pages(str(path), ocr=False)[:4], \
        "their own layers"
    again = run(path)
    assert fake.calls == 4 and again["good_layer"], "the sample is decided already: nothing is read again"


def test_a_good_layer_alone_changes_nothing_anyone_reads(data_dir, monkeypatch):
    install(monkeypatch)
    true_layer = printed_scan_pdf(layer="true", dpi=72)
    path = store(WS, DOC, joined(true_layer, true_layer))
    result = run(path)
    assert result["good_layer"] and result["recognized"] == 0 and result["kept"] == 2
    found = pdf_ocr.state(WS, DOC)
    assert found["stamp"] == "" and not found["pending"] and pdf_ocr.view_of(str(path)) is None
    assert pdf_ocr.reads_well(found), "every scanned page kept its layer"


def test_a_layer_kept_when_the_recognition_reads_too_few_of_its_words(data_dir, monkeypatch):
    fake = install(monkeypatch)
    fake.words = fake.words[:1]  # a page drawn too small, a figure: three words for a layer of twenty-nine
    path = store(WS, DOC, joined(layered_scan(GARBLE), layered_scan(GARBLE)))
    result = run(path)
    assert (result["kept"], result["recognized"], result["good_layer"]) == (2, 0, False), \
        "kept for a poor recognition, which says nothing of how the layer reads: every page is read"
    found = pdf_ocr.state(WS, DOC)
    assert found["kept"] == [1, 2] and found["shared"] == {1: 0.0, 2: 0.0} and pdf_ocr.view_of(str(path)) is None
    assert not pdf_ocr.reads_well(found)


def test_a_picture_page_without_real_text_is_read_as_none(data_dir, monkeypatch):
    install(monkeypatch, conf=40.0)  # words, but none Tesseract is sure of: a photo read as letters
    path = store(WS, DOC, printed_scan_pdf(dpi=72))
    result = run(path)
    assert (result["recognized"], result["empty"]) == (0, 1)
    assert pdf_text.extract_pages(str(path)) == [""]


def test_a_picture_page_keeps_the_text_it_shows_of_its_own(data_dir, monkeypatch):
    install(monkeypatch)  # the cover's picture holds no printed text: the recognition reads nothing sure
    true_layer = printed_scan_pdf(layer="true", dpi=72)
    path = store(WS, DOC, joined(true_layer, true_layer, true_layer, picture_pdf(text="On the Origin of Species")))
    cover = pdf_text.extract_pages(str(path))[3]
    assert "Origin of Species" in cover
    result = run(path)
    assert (result["kept"], result["recognized"], result["empty"], result["good_layer"]) == (4, 0, 0, True)
    assert pdf_text.extract_pages(str(path))[3] == cover, "the cover's title is read as before"
    found = pdf_ocr.state(WS, DOC)
    assert found["kept"] == [1, 2, 3, 4] and set(found["shared"]) == {1, 2, 3}
    assert pdf_ocr.reads_well(found), "a picture page keeping its own text has no layer to count against it"


def test_a_failed_page_is_not_kept_and_is_read_again_next_time(data_dir, monkeypatch):
    fake = install(monkeypatch)
    fake.fail = {1}
    path = store(WS, DOC, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    first = run(path)
    assert (first["failed"], first["recognized"]) == (1, 1) and pdf_ocr.state(WS, DOC)["recognized"] == [2]
    second = run(path)
    assert (second["failed"], second["recognized"]) == (0, 2) and fake.calls == 3


def test_a_stopped_run_changes_nothing_seen_and_the_next_goes_on_from_there(data_dir, monkeypatch):
    fake = install(monkeypatch)
    path = store(WS, DOC, joined(*[printed_scan_pdf(dpi=72)] * 3))

    def stop_after_one(**p):
        if p.get("phase") == "recognizing" and p.get("done") == 1:
            raise jobs.Cancelled()

    with pytest.raises(jobs.Cancelled):
        run(path, stop_after_one)
    found = pdf_ocr.state(WS, DOC)
    assert found["staged"] == [1] and found["recognized"] == [] and found["stamp"] == ""
    assert pdf_ocr.view_of(str(path)) is None and pdf_text.extract_pages(str(path)) == ["", "", ""], \
        "no reader sees a staged page"
    reports = []
    result = run(path, lambda **p: reports.append(p))
    assert fake.calls == 3, "the page staged before is not read again"
    recognizing = [p for p in reports if p.get("phase") == "recognizing"]
    assert recognizing[0]["done"] == recognizing[0]["from"] == 1 and recognizing[-1]["done"] == 3
    assert result["recognized"] == 3 and pdf_text.extract_pages(str(path)) == [PRINTED_TEXT] * 3


def test_an_interrupted_finish_is_done_by_the_next_run(data_dir, monkeypatch):
    install(monkeypatch)
    path = store(WS, DOC, printed_scan_pdf(dpi=72))
    from gamma import highlight_relocate

    real, respells = highlight_relocate.respell_quotes, []

    def crash(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(highlight_relocate, "respell_quotes", crash)
    result = run(path)
    assert result["recognized"] == 1, "its text went live: the run did its work and does not read as failed"
    found = pdf_ocr.state(WS, DOC)
    assert found["recognized"] == [1] and found["pending"], "applied, its finishing steps still due"

    def counted(*args, **kwargs):
        respells.append(args)
        return real(*args, **kwargs)

    def stopped(**_):
        raise jobs.Cancelled()

    monkeypatch.setattr(highlight_relocate, "respell_quotes", counted)
    with pytest.raises(jobs.Cancelled):
        run(path, stopped)  # stopped at its first report: only what comes before it runs
    assert not pdf_ocr.state(WS, DOC)["pending"] and len(respells) == 1, \
        "the next run finishes it first, however that run ends"


def test_the_search_index_reads_the_recognized_words_at_once(data_dir, monkeypatch):
    install(monkeypatch)
    path = store(WS, DOC, layered_scan(GARBLE))
    with db.connect_data_db(WS) as conn:  # indexed before: the garbled layer
        index.store_docs(conn, [(DOC, [(1, "rc50m4mcc")], {"format": "pdf"})])
    run(path)
    with db.connect_data_db(WS) as conn:
        assert [hit.doc_id for hit in index.search(conn, "resonance", 10, [DOC])] == [DOC]
        assert index.search(conn, "rc50m4mcc", 10, [DOC]) == []
        assert index.missing(conn, [DOC]) == [], "stamped current"


@pytest.mark.parametrize("chunk", [index.STORE_CHUNK, 1], ids=["one-batch", "in-chunks"])
def test_a_text_the_indexer_read_before_the_apply_is_not_stored_over_the_reindex(data_dir, monkeypatch, chunk):
    from gamma.search import indexer

    install(monkeypatch)
    monkeypatch.setattr(index, "STORE_CHUNK", chunk)  # 1: two pages are a book, stored a chunk a transaction
    path = store(WS, DOC, joined(layered_scan(GARBLE), layered_scan(GARBLE)))
    early = indexer.index_doc(WS, index.Doc(DOC, "pdf", ".pdf", "store"))  # the workspace's indexing job reads...
    assert early[2]["ocr_stamp"] == "" and "rc50m4mcc" in early[1][0][1]
    run(path)  # ...the recognition applies and indexes the recognized words...
    with db.connect_data_db(WS) as conn:
        index.store_docs(conn, [early])  # ...and then the job's batch lands
        assert sorted(hit.unit for hit in index.search(conn, "resonance", 10, [DOC])) == [1, 2]
        assert index.search(conn, "rc50m4mcc", 10, [DOC]) == []
        assert index.missing(conn, [DOC]) == []
    late = indexer.index_doc(WS, index.Doc(DOC, "pdf", ".pdf", "store"))
    assert late[2]["ocr_stamp"] == pdf_ocr.state(WS, DOC)["stamp"] != ""
    with db.connect_data_db(WS) as conn:
        index.store_docs(conn, [late])  # read after it: stored
        assert sorted(hit.unit for hit in index.search(conn, "resonance", 10, [DOC])) == [1, 2]


def test_a_run_opens_the_file_as_often_whatever_its_length(data_dir, monkeypatch):
    install(monkeypatch)
    real, opened = pdf_text._open, []

    def counted(src):
        opened.append(src)
        return real(src)

    monkeypatch.setattr(pdf_text, "_open", counted)
    counts = []
    for pages, doc_id in ((4, DOC), (8, "e" * 24)):  # the sample, then the rest: each a walk of its own
        path = store(WS, doc_id, joined(*[layered_scan(GARBLE)] * pages))
        opened.clear()
        assert run(path, doc_id=doc_id)["recognized"] == pages
        counts.append(len(opened))
    assert counts[0] == counts[1], "each page's own text is read in the walk that draws them, not a file open a page"


def test_the_briefs_go_and_the_paid_translations_stay(data_dir, monkeypatch):
    install(monkeypatch)
    path = store(WS, DOC, layered_scan(LIGHT))
    translate_store.put_brief(WS, DOC, "fr", "model", "The paper's brief")
    translate_store.put(WS, DOC, "fr", "model", [("src1", "Une traduction")], brief=True)
    generation = translate.generation(WS, DOC)
    run(path)
    assert translate_store.get_brief(WS, DOC, "fr", "model") is None
    assert translate_store.lookup(WS, DOC, "fr", "model", ["src1"]) == {"src1": "Une traduction"}
    assert translate.generation(WS, DOC) > generation, "the brief in memory goes too"


def test_more_scanned_pages_than_a_run_reads_are_refused(data_dir, monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr(pdf_ocr, "MAX_PAGES", 1)
    path = store(WS, DOC, joined(printed_scan_pdf(dpi=72), printed_scan_pdf(dpi=72)))
    with pytest.raises(ValueError, match="2 scanned pages"):
        run(path)
    assert pdf_ocr.state(WS, DOC)["scanned"] == 2, "what it found is recorded"


def test_a_file_without_scanned_pages_ends_after_reading(data_dir, monkeypatch):
    fake = install(monkeypatch)
    path = store(WS, DOC, born_digital())
    result = run(path)
    assert result["scanned"] == 0 and fake.calls == 0 and pdf_ocr.state(WS, DOC)["scanned"] == 0


def test_the_purge_keeps_a_stored_files_recognition(data_dir, monkeypatch):
    from gamma import storage

    install(monkeypatch)
    path = store(WS, DOC, printed_scan_pdf(dpi=72))
    run(path)
    gone = "d" * 24
    pdf_ocr.set_scanned(WS, gone, 1, 1)  # a file no longer stored, no block carries

    def listed(ws):
        raise AssertionError("a sweep never lists the workspace's files: it asks of the recognized ones alone")

    monkeypatch.setattr(storage, "list", listed)
    with db.connect_pages_db(WS) as pages:
        index.purge_unused(WS, pages)
    assert pdf_ocr.state(WS, DOC)["recognized"] == [1], "no block carries it, but it is stored"
    assert pdf_ocr.state(WS, gone) is None
    path.unlink()
    with db.connect_pages_db(WS) as pages:
        index.purge_unused(WS, pages)
    assert pdf_ocr.state(WS, DOC) is None
