"""The anchor a thread keeps (gamma/feedback/anchors.py,
docs/dev/feedback.md "The anchor"): read from the rows of a fresh pages.db
table for each kind — a passage, an area (flagged, and without a quote),
ink on a PDF page and on a sheet, a note under a highlight, a note alone, a
text box, the page itself —; the file the place was measured on; a
position without a frame; the crop boxes' minimums and clamps, the ink's
being view_ink's (``ink_view.grow_box``); and ``parse``, ``held_file`` and
``top`` on what is stored."""

import json
import sqlite3

import pytest

from conftest import doc_props
from gamma import ink as inkmod
from gamma.db import PAGES_SCHEMA
from gamma.feedback import anchors
from gamma.ink_view import crop_box, grow_box

HASH = "a" * 24
OLD = "b" * 24
FRAME = {"width": 600, "height": 800}


def _db(*blocks) -> sqlite3.Connection:
    """A pages.db table holding the page ``P`` (its PDF ``HASH``) and
    ``blocks`` (``(id, parent, properties)``) on it."""
    conn = sqlite3.connect(":memory:")
    conn.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks")))
    rows = [("P", "root", "P", doc_props(HASH)), *((i, parent, "P", props) for i, parent, props in blocks)]
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


def test_a_passage():
    conn = _db(("H", "P", {"pdf_position": _pos(3, (60, 80, 300, 96), (60, 100, 120, 116)),
                           "quote": "the loss is defined as"}))
    anchor = anchors.anchor_of(conn, "P", "H")
    assert anchor == {"v": 1, "kind": "passage", "block": "H", "doc_id": HASH, "page": 3,
                      "rects": [[0.1, 0.1, 0.5, 0.12], [0.1, 0.125, 0.2, 0.145]], "quote": "the loss is defined as",
                      "box": [0.09, 0.0875, 0.51, 0.1575]}
    assert list(anchor) == ["v", "kind", "block", "doc_id", "page", "rects", "quote", "box"]


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
    assert anchors.anchor_of(conn, "P", "S") == {"v": 1, "kind": "note", "block": "S", "doc_id": HASH, "page": None,
                                                "rects": [], "quote": "", "box": None}


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
                                                       "page": None, "rects": [], "quote": "", "box": None}
    assert anchors.anchor_of(conn, "P", "P") == {"v": 1, "kind": "file", "block": "P", "doc_id": HASH, "page": None,
                                                 "rects": [], "quote": "", "box": None}


def test_the_file_a_place_was_measured_on():
    """A highlight not yet placed in a replacement keeps the old file's
    rectangles: its ``doc_id`` is that file; a link region measured on
    this one is the page's."""
    conn = _db(("H", "P", {"pdf_position": _pos(1, (60, 80, 300, 96), doc_id=OLD, approx=True), "quote": "q"}),
               ("K", "P", {"pdf_position": _pos(1, (60, 80, 300, 96)), "link_url": "https://example.org"}))
    assert anchors.anchor_of(conn, "P", "H")["doc_id"] == OLD
    link = anchors.anchor_of(conn, "P", "K")
    assert (link["kind"], link["doc_id"]) == ("area", HASH)
    # a page without a document measures nothing
    bare = sqlite3.connect(":memory:")
    bare.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks")))
    bare.execute("INSERT INTO unified_blocks (id, parent_id, position, properties, created_at, updated_at, page_id) "
                 "VALUES ('P', 'root', 'a0', '{}', 't0', 't0', 'P')")
    assert anchors.anchor_of(bare, "P", "P")["doc_id"] == ""


def test_a_position_without_a_frame():
    conn = _db(("H", "P", {"pdf_position": {"pageNumber": 4}, "quote": "from Logseq"}),
               ("I", "P", {"ink_url": "", "pdf_position": {"pageNumber": 2}}))
    passage = anchors.anchor_of(conn, "P", "H")
    assert (passage["kind"], passage["page"], passage["rects"], passage["quote"], passage["box"]) == (
        "passage", 4, [], "from Logseq", None)
    ink = anchors.anchor_of(conn, "P", "I")
    assert (ink["kind"], ink["page"], ink["rects"], ink["box"]) == ("ink", 2, [], None)


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
    assert anchors.top(anchors.parse(good)) == 0.3
    assert anchors.top({"rects": [], "box": [0.1, 0.25, 0.3, 0.4]}) == 0.25
    assert anchors.top({"kind": "note", "rects": [], "box": None}) == 0 and anchors.top(None) == 0
