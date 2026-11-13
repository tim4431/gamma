"""The anchor a thread keeps (gamma/feedback/anchors.py,
docs/dev/feedback.md "The anchor"): read from the rows of a fresh pages.db
table for each kind — a passage, an area (flagged, and without a quote),
ink on a PDF page and on a sheet, a region of a picture (and a link block
drawn as one), a range of a text file (and a link on one, and one whose
place does not read), a note under a highlight, a region or a range, a note
alone, a text box, the page itself —; the file the place was measured on
and its extension; a position without a frame; the crop boxes' minimums and
clamps, the ink's being view_ink's (``ink_view.grow_box``); and ``parse``,
``held_file``, ``top`` and ``place_words`` on what is stored."""

import json
import sqlite3

import pytest

from conftest import doc_props
from gamma import ink as inkmod
from gamma.db import PAGES_SCHEMA
from gamma.documents.model import document
from gamma.feedback import anchors
from gamma.ink_view import crop_box, grow_box

HASH = "a" * 24
OLD = "b" * 24
PNG = "c" * 24
FRAME = {"width": 600, "height": 800}
PICTURE = {"doc": document(PNG, name="fig.png", format="image", ext=".png")}   # a 1200 × 800 picture


def _db(*blocks, doc=None) -> sqlite3.Connection:
    """A pages.db table holding the page ``P`` (its PDF ``HASH``, or the
    document ``doc``) and ``blocks`` (``(id, parent, properties)``) on it."""
    conn = sqlite3.connect(":memory:")
    conn.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks")))
    rows = [("P", "root", "P", doc or doc_props(HASH)), *((i, parent, "P", props) for i, parent, props in blocks)]
    conn.executemany("INSERT INTO unified_blocks (id, parent_id, position, properties, created_at, updated_at, page_id) "
                     "VALUES (?, ?, 'a0', ?, 't0', 't0', ?)", [(i, p, json.dumps(props), pg) for i, p, pg, props in rows])
    return conn


def _pos(page, *rects, area=False, **more):
    out = {"pageNumber": page, **FRAME, "boundingRect": {
        "x1": min(r[0] for r in rects), "y1": min(r[1] for r in rects),
        "x2": max(r[2] for r in rects), "y2": max(r[3] for r in rects)},
        "rects": [dict(zip(("x1", "y1", "x2", "y2"), r)) for r in rects], **more}
    if area:
        out["area"] = True
    return out


def _region(*rect, **more):
    """An ``image_position`` over ``rect`` of the 1200 × 800 picture, drawn
    on its version ``PNG`` unless ``more`` says otherwise."""
    box = dict(zip(("x1", "y1", "x2", "y2"), rect))
    return {"width": 1200, "height": 800, "boundingRect": box, "rects": [box], "hash": PNG, **more}


def test_a_passage():
    conn = _db(("H", "P", {"pdf_position": _pos(3, (60, 80, 300, 96), (60, 100, 120, 116)),
                           "quote": "the loss is defined as"}))
    anchor = anchors.anchor_of(conn, "P", "H")
    assert anchor == {"v": 1, "kind": "passage", "block": "H", "doc_id": HASH, "ext": ".pdf", "page": 3,
                      "rects": [[0.1, 0.1, 0.5, 0.12], [0.1, 0.125, 0.2, 0.145]], "quote": "the loss is defined as",
                      "box": [0.09, 0.0875, 0.51, 0.1575]}
    assert list(anchor) == ["v", "kind", "block", "doc_id", "ext", "page", "rects", "quote", "box"]


def test_an_area_flagged_or_without_a_quote():
    conn = _db(("A", "P", {"pdf_position": _pos(5, (60, 400, 540, 720), area=True), "quote": "Figure 1"}),
               ("Q", "P", {"pdf_position": _pos(5, (60, 400, 540, 720)), "quote": "  "}))
    for block in ("A", "Q"):
        anchor = anchors.anchor_of(conn, "P", block)
        assert (anchor["kind"], anchor["page"], anchor["quote"]) == ("area", 5, "")
        assert anchor["rects"] == [[0.1, 0.5, 0.9, 0.9]] and anchor["box"] == [0.095, 0.495, 0.905, 0.905]


def test_ink_on_a_pdf_page_and_on_a_sheet():
    pos = {"pageNumber": 2, "width": 612, "height": 792, "boundingRect": {"x1": 100, "y1": 100, "x2": 110, "y2": 120},
           "rects": [{"x1": 100, "y1": 100, "x2": 110, "y2": 120}]}
    conn = _db(("I", "P", {"ink_url": "", "pdf_position": pos}), ("S", "P", {"ink_url": "/api/uploads/x.json"}))
    anchor = anchors.anchor_of(conn, "P", "I")
    assert (anchor["kind"], anchor["block"], anchor["doc_id"], anchor["page"], anchor["quote"]) == (
        "ink", "I", HASH, 2, "")
    assert anchor["rects"] == [[round(100 / 612, 4), round(100 / 792, 4), round(110 / 612, 4), round(120 / 792, 4)]]
    assert anchor["box"] == [round(v, 4) for v in grow_box(100, 100, 110, 120, 612, 792)]
    assert (anchor["box"][2] - anchor["box"][0]) * 612 == pytest.approx(144, abs=0.1)  # at least two inches a side
    # ink on a sheet has no PDF page: a note
    assert anchors.anchor_of(conn, "P", "S") == {"v": 1, "kind": "note", "block": "S", "doc_id": HASH, "ext": ".pdf",
                                                "page": None, "rects": [], "quote": "", "box": None}


def test_a_note_takes_the_place_of_the_highlight_it_is_under():
    conn = _db(("H", "P", {"pdf_position": _pos(3, (60, 80, 300, 96)), "quote": "a passage"}),
               ("N", "H", {}), ("NN", "N", {}))
    under = anchors.anchor_of(conn, "P", "NN")
    assert under == {**anchors.anchor_of(conn, "P", "H")} and under["block"] == "H" and under["kind"] == "passage"


def test_a_note_alone_a_text_box_and_the_page():
    conn = _db(("N", "P", {}), ("T", "P", {"text_box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.1}}),
               ("L", "P", {"link_url": "https://example.org"}))
    for block in ("N", "T", "L"):
        assert anchors.anchor_of(conn, "P", block) == {"v": 1, "kind": "note", "block": block, "doc_id": HASH,
                                                       "ext": ".pdf", "page": None, "rects": [], "quote": "", "box": None}
    assert anchors.anchor_of(conn, "P", "P") == {"v": 1, "kind": "file", "block": "P", "doc_id": HASH, "ext": ".pdf",
                                                 "page": None, "rects": [], "quote": "", "box": None}


def test_the_file_a_place_was_measured_on():
    """A highlight not yet placed in a replacement keeps the old file's
    rectangles: its ``doc_id`` is that file; a link region measured on
    this one is the page's."""
    conn = _db(("H", "P", {"pdf_position": _pos(1, (60, 80, 300, 96), doc_id=OLD, approx=True), "quote": "q"}),
               ("K", "P", {"pdf_position": _pos(1, (60, 80, 300, 96)), "link_url": "https://example.org"}))
    assert anchors.anchor_of(conn, "P", "H")["doc_id"] == OLD
    link = anchors.anchor_of(conn, "P", "K")
    assert (link["kind"], link["doc_id"]) == ("area", HASH)
    # a page without a document measures nothing and names no extension
    bare = sqlite3.connect(":memory:")
    bare.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks")))
    bare.execute("INSERT INTO unified_blocks (id, parent_id, position, properties, created_at, updated_at, page_id) "
                 "VALUES ('P', 'root', 'a0', '{}', 't0', 't0', 'P')")
    assert (anchors.anchor_of(bare, "P", "P")["doc_id"], anchors.anchor_of(bare, "P", "P")["ext"]) == ("", "")


def test_a_position_without_a_frame():
    conn = _db(("H", "P", {"pdf_position": {"pageNumber": 4}, "quote": "from Logseq"}),
               ("I", "P", {"ink_url": "", "pdf_position": {"pageNumber": 2}}))
    passage = anchors.anchor_of(conn, "P", "H")
    assert (passage["kind"], passage["page"], passage["rects"], passage["quote"], passage["box"]) == (
        "passage", 4, [], "from Logseq", None)
    ink = anchors.anchor_of(conn, "P", "I")
    assert (ink["kind"], ink["page"], ink["rects"], ink["box"]) == ("ink", 2, [], None)


def test_a_region_of_a_picture_a_note_under_it_and_the_pictures_page():
    conn = _db(("R", "P", {"image_position": _region(120, 80, 360, 240), "color": "#ff0000"}),
               ("N", "R", {}), ("NN", "N", {}),
               ("K", "P", {"image_position": _region(600, 400, 1200, 800), "link_url": "https://example.org"}),
               doc=PICTURE)
    anchor = anchors.anchor_of(conn, "P", "R")
    assert anchor == {"v": 1, "kind": "region", "block": "R", "doc_id": PNG, "ext": ".png", "page": None,
                      "rects": [[0.1, 0.1, 0.3, 0.3]], "quote": "", "box": [0.095, 0.095, 0.305, 0.305]}
    assert anchors.anchor_of(conn, "P", "NN") == anchor          # a note under a region takes its place
    link = anchors.anchor_of(conn, "P", "K")                      # a link block drawn on the picture is a region too
    assert (link["kind"], link["block"], link["rects"]) == ("region", "K", [[0.5, 0.5, 1.0, 1.0]])
    assert anchors.anchor_of(conn, "P", "P") == {"v": 1, "kind": "file", "block": "P", "doc_id": PNG, "ext": ".png",
                                                 "page": None, "rects": [], "quote": "", "box": None}


def test_the_version_a_region_was_drawn_on_a_frameless_one_and_ink_on_a_picture():
    conn = _db(("E", "P", {"image_position": _region(0, 0, 600, 400, hash=OLD)}),
               ("U", "P", {"image_position": {**_region(0, 0, 600, 400), "hash": ""}}),
               ("F", "P", {"image_position": {"boundingRect": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}}),
               ("I", "P", {"ink_url": "", "image_position": _region(0, 0, 10, 10)}),
               ("B", "P", {"pdf_position": _pos(1, (60, 80, 300, 96)), "image_position": _region(0, 0, 10, 10),
                           "quote": "both"}),
               doc=PICTURE)
    assert anchors.anchor_of(conn, "P", "E")["doc_id"] == OLD      # drawn on an earlier version: measured there
    assert anchors.anchor_of(conn, "P", "U")["doc_id"] == PNG      # one naming no version: the page's
    frameless = anchors.anchor_of(conn, "P", "F")
    assert (frameless["kind"], frameless["rects"], frameless["box"]) == ("region", [], None)
    assert anchors.anchor_of(conn, "P", "I")["kind"] == "note"     # ink is placed by a pdf_position alone
    assert anchors.anchor_of(conn, "P", "B")["kind"] == "passage"  # a block with both positions is on the PDF page


TEX = "d" * 24
SOURCE = {"doc": document(TEX, name="main.tex", format="text", ext=".tex")}


def _lines(line, end, *, col=0, end_col=5, hash=TEX, **more):
    """A ``text_position`` over lines ``line``..``end`` of ``main.tex``,
    measured on its version ``hash``."""
    return {"line": line, "col": col, "end": end, "end_col": end_col, "prefix": "", "suffix": "", "hash": hash,
            **more}


def test_a_range_of_a_text_file_a_note_under_it_and_a_link_on_one():
    conn = _db(("R", "P", {"text_position": _lines(16, 17), "quote": "Weight decay is\nthe oldest"}),
               ("N", "R", {}), ("NN", "N", {}),
               ("K", "P", {"text_position": _lines(30, 30), "quote": "x", "link_url": "https://example.org"}),
               ("O", "P", {"text_position": _lines(4, 4, hash=OLD, lost=True), "quote": "gone"}),
               ("U", "P", {"text_position": _lines(5, 5, hash=""), "quote": "unnamed"}),
               doc=SOURCE)
    anchor = anchors.anchor_of(conn, "P", "R")
    assert anchor == {"v": 1, "kind": "lines", "block": "R", "doc_id": TEX, "ext": ".tex", "page": None, "rects": [],
                      "quote": "Weight decay is\nthe oldest", "box": None, "lines": [16, 17]}
    assert list(anchor) == ["v", "kind", "block", "doc_id", "ext", "page", "rects", "quote", "box", "lines"]
    assert anchors.anchor_of(conn, "P", "NN") == anchor             # a note under a range takes its place
    link = anchors.anchor_of(conn, "P", "K")                        # a link block on lines is a range too
    assert (link["kind"], link["block"], link["lines"]) == ("lines", "K", [30, 30])
    assert anchors.anchor_of(conn, "P", "O")["doc_id"] == OLD       # measured on an earlier version: that one
    assert anchors.anchor_of(conn, "P", "U")["doc_id"] == TEX       # one naming no version: the page's
    assert anchors.anchor_of(conn, "P", "P") == {"v": 1, "kind": "file", "block": "P", "doc_id": TEX, "ext": ".tex",
                                                 "page": None, "rects": [], "quote": "", "box": None}
    # the other kinds carry no ``lines``: their pinned keys stand
    assert "lines" not in anchors.anchor_of(conn, "P", "P")


def test_a_ranges_place_is_named_by_the_lines_it_holds_now():
    """``following``: a ``lines`` anchor stored when its thread opened is
    named by its range's lines after a relocation moved it; one whose block
    is no range any more, and every other kind, as stored."""
    conn = _db(("R", "P", {"text_position": _lines(19, 20), "quote": "moved down"}),
               ("G", "P", {"quote": "no range now"}), doc=SOURCE)
    stored = {**anchors.anchor_of(conn, "P", "R"), "lines": [16, 17]}
    assert anchors.following(conn, "P", stored)["lines"] == [19, 20]
    assert anchors.place_words(anchors.following(conn, "P", stored)) == "lines 19–20, “moved down”"
    gone = {**stored, "block": "G"}
    assert anchors.following(conn, "P", gone) is gone
    whole = anchors.anchor_of(conn, "P", "P")
    assert anchors.following(conn, "P", whole) is whole


def test_a_range_whose_place_does_not_read_is_a_note():
    """Nothing validates a ``text_position`` on write: a malformed one, or one
    on ink, is no range (``highlights.text_span``), and a note under it walks
    on up. A block with a PDF position too is placed on the PDF page."""
    conn = _db(("Z", "P", {"text_position": _lines(0, 1), "quote": "zero"}),
               ("S", "P", {"text_position": "1-3", "quote": "a string"}),
               ("E", "P", {"text_position": _lines(3, 3, col=4, end_col=4), "quote": "empty"}),
               ("I", "P", {"ink_url": "", "text_position": _lines(3, 3)}),
               ("ZN", "Z", {}),
               ("B", "P", {"pdf_position": _pos(1, (60, 80, 300, 96)), "text_position": _lines(2, 2), "quote": "both"}),
               doc=SOURCE)
    for block in ("Z", "S", "E", "I", "ZN"):
        assert anchors.anchor_of(conn, "P", block)["kind"] == "note", block
    assert anchors.anchor_of(conn, "P", "B")["kind"] == "passage"


def test_a_ranges_anchor_parsed_held_and_named():
    good = {"v": 1, "kind": "lines", "block": "R", "doc_id": TEX, "ext": ".tex", "page": None, "rects": [],
            "quote": "for step in range(10):\n    train(step)", "box": None, "lines": [118, 120]}
    assert anchors.parse(json.dumps(good)) == good
    for lines in (None, [118], [120, 118], [0, 1], ["1", "2"], [True, 2], "118-120"):
        assert anchors.parse(json.dumps({**good, "lines": lines})) is None, lines
    assert anchors.held_file(json.dumps(good)) == ""                # a range carries its own context: nothing held
    assert anchors.lines_of(good) == (118, 120) and anchors.lines_of({"kind": "passage"}) is None
    assert anchors.top(good) == 0
    assert anchors.place_words(good) == "lines 118–120, “for step in range(10): train(step)”"
    assert anchors.place_words({**good, "lines": [7, 7], "quote": "x" * 70}) == f"line 7, “{'x' * 60}…”"
    assert anchors.place_words({**good, "quote": ""}) == "lines 118–120"
    assert anchors.place_words({**good, "lines": None}) == "a note"


def test_boxes_grow_to_their_minimum_and_stay_on_the_page():
    conn = _db(("W", "P", {"pdf_position": _pos(1, (0, 0, 6, 8)), "quote": "x"}),
               ("E", "P", {"pdf_position": _pos(1, (590, 790, 600, 800), area=True)}),
               ("C", "P", {"ink_url": "", "pdf_position": _pos(1, (1, 1, 3, 3))}))
    word = anchors.anchor_of(conn, "P", "W")["box"]
    assert word[0] == 0 and word[1] == 0  # clamped at the corner
    assert word[2] == pytest.approx(0.01 + (anchors.PASSAGE_MIN_W - 0.01) / 2 + anchors.PASSAGE_PAD, abs=1e-4)
    assert word[3] == pytest.approx(0.01 + (anchors.PASSAGE_MIN_H - 0.01) / 2 + anchors.PASSAGE_PAD, abs=1e-4)
    assert anchors.anchor_of(conn, "P", "E")["box"] == [round(590 / 600 - anchors.AREA_PAD, 4),
                                                        round(790 / 800 - anchors.AREA_PAD, 4), 1.0, 1.0]
    corner = anchors.anchor_of(conn, "P", "C")["box"]
    assert corner[:2] == [0, 0] and corner[2] * 600 == pytest.approx(144, abs=0.1)


def test_grow_box_is_view_inks_crop():
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2,
                            "space": {"kind": "pdf-page", "width": 612, "height": 792, "page": 1},
                            "strokes": [{"id": "s1", "color": "#000000", "size": 6, "ch": "xy", "pts": inkmod.encode_points(
                                [{"x": 100, "y": 200}, {"x": 180, "y": 260}], "xy")}]})
    box = inkmod.bounding_box(ink)
    assert box and grow_box(*box, 612, 792) == crop_box([ink], 612, 792)
    assert grow_box(0, 0, 1, 1, 0, 792) is None


def test_parse_held_file_and_top():
    good = json.dumps({"v": 1, "kind": "passage", "block": "H", "doc_id": HASH, "page": 2,
                       "rects": [[0.1, 0.4, 0.5, 0.42], [0.1, 0.3, 0.2, 0.32]], "quote": "q", "box": [0, 0.2, 1, 0.5]})
    assert anchors.parse(good)["block"] == "H"
    for bad in (None, "", "{bad", "[]", '"text"', json.dumps({"kind": "elsewhere", "block": "H"}),
                json.dumps({"kind": "note"}), json.dumps({"kind": "note", "block": "N", "rects": "no"})):
        assert anchors.parse(bad) is None and anchors.held_file(bad) == ""
    assert anchors.held_file(good) == f"{HASH}.pdf"
    for doc_id in ("", "../../etc", "unstored"):
        assert anchors.held_file(json.dumps({"kind": "file", "block": "P", "doc_id": doc_id})) == ""
    # the file held is named by the anchor's ext; one from before step 3 (no ext) holds a PDF, a malformed ext nothing
    assert anchors.held_file(json.dumps({"kind": "region", "block": "R", "doc_id": HASH, "ext": ".png"})) == f"{HASH}.png"
    assert anchors.held_file(json.dumps({"kind": "file", "block": "P", "doc_id": HASH, "ext": ""})) == f"{HASH}.pdf"
    assert anchors.held_file(json.dumps({"kind": "file", "block": "P", "doc_id": HASH, "ext": "png"})) == ""
    assert anchors.parse(json.dumps({"kind": "file", "block": "P", "doc_id": HASH, "ext": 3})) is None
    assert anchors.top(anchors.parse(good)) == 0.3
    assert anchors.top({"rects": [], "box": [0.1, 0.25, 0.3, 0.4]}) == 0.25
    assert anchors.top({"kind": "note", "rects": [], "box": None}) == 0 and anchors.top(None) == 0
