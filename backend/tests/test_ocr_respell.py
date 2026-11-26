"""The highlights of a PDF whose scanned pages' text was recognized
(highlight_relocate ``respell_quotes``, run by gamma/pdf_ocr.py once the
text goes live): each keeps its place and takes the recognized words under
its rectangles as its quote when its quote is what the layer read there
and the two readings agree; a corrected quote, a rectangle off its text,
an area, a lost highlight and one measured on another file are left
alone. A relocation reads the two sides of each comparison alike, so a
scan replaced by a copy of itself keeps every highlight solid whichever of
the two was recognized (the answer saying when the new file's text is
not), a highlight made on recognized words is matched in them, and the
quotes are respelled after its write, also when the recognition went live
while it ran."""

import io
import itertools

import pymupdf
import pytest
from conftest import account_of, login, make_user, workspace_of
from ocr_fake import GARBLE, LIGHT, install, joined, layered_scan
from ocr_fixtures import PRINTED, W, H, printed_scan_pdf

from gamma import highlight_relocate as hr
from gamma import jobs, ocr_run, pdf_ocr, pdf_text

USER, PW = "or_alice", "or-alice-pw"
LINES = {n: (x, y, size, text) for n, (x, y, size, text) in enumerate(PRINTED, start=1)}
SHIFT = 18  # points the shifted layer's body lines sit above their print: a line
EVEN = str.maketrans("e", "a")  # a layer misreading a letter for one as wide (Helvetica's): its lines are the print's


@pytest.fixture(scope="module")
def alice(client):
    make_user(USER, PW)
    return login(USER, PW)


@pytest.fixture(autouse=True)
def _fresh_cache():
    pdf_text.text_cache_clear()
    yield
    pdf_text.text_cache_clear()


def rect(n, dy=0.0):
    """The viewer's rect over PRINTED's line ``n`` (on a page of W x H points,
    unrotated: the viewer frame is the page in points), ``dy`` points lower."""
    x, y, size, text = LINES[n]
    y += dy
    width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    return {"x1": x, "y1": round(y - 0.9 * size, 2), "x2": round(x + width, 2), "y2": round(y + 0.3 * size, 2)}


def position(page, rects, **extra):
    rects = rects if isinstance(rects, list) else [rects]
    bound = {"x1": min(r["x1"] for r in rects), "y1": min(r["y1"] for r in rects),
             "x2": max(r["x2"] for r in rects), "y2": max(r["y2"] for r in rects)}
    return {"pageNumber": page, "width": W, "height": H, "boundingRect": bound, "rects": rects, **extra}


def shifted_scan() -> bytes:
    """A scan whose LIGHT layer sets each body line a line above its print
    (the title where it is): a layer a highlight was drawn over before its
    text was recognized sits off the words now read under it."""
    doc = pymupdf.open(stream=printed_scan_pdf(dpi=72), filetype="pdf")
    for x, y, size, text in PRINTED:
        doc[0].insert_text((x, y - (SHIFT if size < 20 else 0)), text.translate(LIGHT), fontname="helv",
                           fontsize=size, render_mode=3)
    return doc.tobytes()


_copies = itertools.count()


def upload(c, data, name="scan.pdf"):
    """Store ``data`` made a file of its own (a subject of its own): a
    stored file belongs to one page of a workspace."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    doc.set_metadata({"subject": f"Copy {next(_copies)}"})
    r = c.post("/api/uploads", files={"file": (name, io.BytesIO(doc.tobytes()), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def page_with(c, data, highlights, title="A scanned paper"):
    """A page showing ``data`` with highlight blocks ``[(quote, position)]``:
    ``(page id, doc id, [block ids])``."""
    doc = upload(c, data)
    page = c.post("/api/pages", json={"title": title}).json()
    assert c.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc}).status_code == 200
    ids = []
    for quote, pos in highlights:
        r = c.post("/api/blocks", json={"parent_id": page["id"], "content": "",
                                        "properties": {"pdf_position": pos, "quote": quote, "color": "#ffe28fa6"}})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return page["id"], doc, ids


def props(c, block_id):
    return c.get(f"/api/blocks/{block_id}").json()["properties"]


def recognize(doc):
    ws = workspace_of(USER)
    return ocr_run.run(ws, doc, pdf_ocr.stored_path(ws, doc), actor=account_of(USER))


def layer_line(n, translate=LIGHT):
    return LINES[n][3].translate(translate)


def test_a_highlight_takes_the_recognized_text_under_it_when_its_quote_is_the_layers(alice, monkeypatch):
    install(monkeypatch)
    page_id, doc, ids = page_with(alice, joined(layered_scan(LIGHT), shifted_scan()), [
        (layer_line(5), position(1, rect(5))),                                    # what the layer reads: respelled
        ("scanned page, and each highlight keeps its place!", position(1, rect(4))),  # corrected by hand: kept
        ("Quantum Zeno dynamics", position(1, rect(1))),                          # already the recognized words
        (layer_line(5), position(2, rect(5, dy=-SHIFT))),                         # over the layer a line off: kept
        ("", position(1, rect(3), area=True)),                                    # an area: never
        (layer_line(3), position(1, rect(3), lost=True)),                         # lost: never
        (layer_line(2), position(1, rect(2), doc_id="e" * 24)),                   # another file's rects: never
    ])
    matching, corrected, same, off, area, lost, other = ids
    before = {bid: props(alice, bid) for bid in ids}
    result = recognize(doc)
    assert (result["highlights_respelled"], result["highlights_kept"], result["highlights_changed"]) == (1, 2, 0)
    assert props(alice, matching)["quote"] == LINES[5][3]
    assert props(alice, matching)["pdf_position"] == before[matching]["pdf_position"], "its place stays"
    for bid in (corrected, same, off, area, lost, other):
        assert props(alice, bid) == before[bid]
    # run again: nothing to write, the same ones kept
    ws = workspace_of(USER)
    again = hr.respell_quotes(ws, doc, [1, 2], actor=account_of(USER))
    assert again == {"respelled": 0, "kept": 2, "changed": 0}


def test_a_heavily_garbled_quote_is_left_as_it_is(alice, monkeypatch):
    install(monkeypatch)
    _page, doc, (garbled,) = page_with(alice, layered_scan(GARBLE), [(layer_line(5, GARBLE), position(1, rect(5)))])
    result = recognize(doc)
    assert (result["highlights_respelled"], result["highlights_kept"]) == (0, 1), \
        "its letters read too unlike the recognized ones to be the same words"
    assert props(alice, garbled)["quote"] == layer_line(5, GARBLE)


def test_find_highlights_after_a_recognition_leaves_a_quote_the_respell_kept_where_it_is(alice, monkeypatch):
    # Kept by the respell (too unlike the recognized words), its quote is the layer's: matched in the
    # recognized words it would be found nowhere and marked lost; matched in the layer it stays put.
    install(monkeypatch)
    page_id, doc, (garbled, matching) = page_with(alice, layered_scan(GARBLE), [
        (layer_line(5, GARBLE), position(1, rect(5))), ("Quantum Zeno dynamics", position(1, rect(1)))])
    recognize(doc)
    assert props(alice, garbled)["quote"] == layer_line(5, GARBLE)
    before = {bid: props(alice, bid) for bid in (garbled, matching)}
    r = alice.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
    assert r.status_code == 200, r.text
    done = jobs.wait(r.json()["id"])
    assert done["state"] == "done" and (done["result"]["lost"], done["result"]["kept"]) == (0, 2), done["result"]
    for bid in (garbled, matching):
        assert props(alice, bid) == before[bid], "nothing written: each is where its quote reads"


def test_a_page_changed_meanwhile_counts_its_quotes_changed(alice, monkeypatch):
    install(monkeypatch)
    _page, doc, (matching,) = page_with(alice, layered_scan(LIGHT), [(layer_line(5), position(1, rect(5)))])

    def refused(*args, **kwargs):
        raise ValueError("the page's PDF changed again before the highlights were placed")

    monkeypatch.setattr(hr, "_write", refused)
    result = recognize(doc)
    assert (result["highlights_respelled"], result["highlights_changed"]) == (0, 1)
    assert props(alice, matching)["quote"] == layer_line(5)


def test_a_recognized_scan_replaced_by_a_copy_of_itself_keeps_every_highlight_solid(alice, monkeypatch):
    install(monkeypatch)
    scan = layered_scan(GARBLE)
    page_id, doc, (first, second) = page_with(alice, scan, [
        (layer_line(5, GARBLE), position(1, rect(5))), (layer_line(3, GARBLE), position(1, rect(3)))])
    recognize(doc)
    assert pdf_ocr.state(workspace_of(USER), doc)["recognized"] == [1]
    copy = upload(alice, pymupdf.open(stream=scan, filetype="pdf").tobytes(garbage=3), "copy.pdf")
    assert copy != doc
    r = alice.put(f"/api/pages/{page_id}/attachment", json={"doc_id": copy})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text_not_recognized"] is True, "the new file's text is the layer's until it is recognized"
    done = jobs.wait(body["job"]["id"])
    assert done["state"] == "done" and done["result"]["kept"] == 2 and done["result"]["lost"] == 0, done["result"]
    for bid in (first, second):
        pos = props(alice, bid)["pdf_position"]
        assert not {"lost", "approx", "doc_id"} & set(pos), pos


def test_an_unrecognized_scan_replaced_by_a_recognized_copy_of_itself_keeps_every_highlight_solid(alice, monkeypatch):
    install(monkeypatch)
    scan = layered_scan(EVEN)
    page_id, doc, (first, second) = page_with(alice, scan, [
        (layer_line(5, EVEN), position(1, rect(5))), (layer_line(3, EVEN), position(1, rect(3)))])
    copy = upload(alice, pymupdf.open(stream=scan, filetype="pdf").tobytes(garbage=3), "copy.pdf")
    recognize(copy)  # before it shows on the page: on another page of the workspace, or a run that went first
    assert pdf_ocr.state(workspace_of(USER), doc) is None
    r = alice.put(f"/api/pages/{page_id}/attachment", json={"doc_id": copy})
    assert r.status_code == 200, r.text
    done = jobs.wait(r.json()["job"]["id"])
    result = done["result"]
    assert done["state"] == "done" and (result["kept"], result["lost"], result["approx"]) == (2, 0, 0), result
    for bid, n in ((first, 5), (second, 3)):
        found = props(alice, bid)
        assert not {"lost", "approx", "doc_id"} & set(found["pdf_position"]), found["pdf_position"]
        assert found["quote"] == LINES[n][3], "placed layer against layer, then respelled as the copy's words read"


def test_a_highlight_on_recognized_words_is_matched_in_them_beside_one_from_an_unrecognized_file(alice, monkeypatch):
    install(monkeypatch)
    old = upload(alice, layered_scan(LIGHT), "old.pdf")  # stored, never recognized
    page_id, doc, (made, lost) = page_with(alice, layered_scan(GARBLE), [
        (LINES[5][3], position(1, rect(5))),                             # made on the recognized words
        (layer_line(3), position(1, rect(3), lost=True, doc_id=old)),    # lost by an earlier replacement, from old
    ])
    recognize(doc)
    before = props(alice, made)
    hr.relocate(workspace_of(USER), page_id, doc, actor=account_of(USER))  # Find highlights in this PDF
    assert props(alice, made) == before, "read in the words it was made on, not in the garbled layer"
    assert props(alice, lost)["pdf_position"].get("doc_id") == old, "the other file's layer against this one's"


def test_a_recognition_that_goes_live_while_a_relocation_runs_still_respells_its_quotes(alice, monkeypatch):
    install(monkeypatch)
    scan = layered_scan(EVEN)
    page_id, _doc, (bid,) = page_with(alice, scan, [(layer_line(5, EVEN), position(1, rect(5)))])
    copy = upload(alice, pymupdf.open(stream=scan, filetype="pdf").tobytes(garbage=3), "copy.pdf")
    real, during = hr.place_all, []

    def held(*args, **kwargs):  # the relocation chose to read the layers; the copy's recognition goes live now
        if not during:
            during.append(recognize(copy))
        return real(*args, **kwargs)

    monkeypatch.setattr(hr, "place_all", held)
    r = alice.put(f"/api/pages/{page_id}/attachment", json={"doc_id": copy})
    assert r.status_code == 200, r.text
    done = jobs.wait(r.json()["job"]["id"])
    assert done["state"] == "done" and done["result"]["kept"] == 1, done["result"]
    assert (during[0]["recognized"], during[0]["highlights_respelled"]) == (1, 0), \
        "its own respell passed the highlight by: its position still named the outgoing file"
    found = props(alice, bid)
    assert found["quote"] == LINES[5][3] and "doc_id" not in found["pdf_position"]


def test_a_replacement_between_unrecognized_files_says_nothing_of_recognition(alice):
    page_id, _doc, _ids = page_with(alice, printed_scan_pdf(layer="garbled", dpi=72), [])
    copy = upload(alice, printed_scan_pdf(layer="garbled", dpi=72), "other.pdf")
    body = alice.put(f"/api/pages/{page_id}/attachment", json={"doc_id": copy}).json()
    assert "text_not_recognized" not in body
