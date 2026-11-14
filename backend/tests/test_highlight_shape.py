"""The highlight shape (gamma/highlights.py, migration step 30): one
pdf_position — the page and its size once, the rects bare — written by
every writer that places a block on a PDF page (the annotation and Logseq
importers, ink), the block id as the highlight's id, and a page's PDF URL
derived from its doc rather than stored (attach, by-doc, the op path,
page creation, the clip and its lookup). A range of a text file's
text_position: its test, its span read defensively, its prev and closest,
and its quote and context cut from the lines (tests/shared/textposition.json
holds the client to the same, test_shared_fixtures.py)."""

import io

import pytest

from conftest import doc_props, login, make_page, make_user
from gamma.highlights import (CONTEXT_CHARS, PREV_MAX_QUOTE, PREV_MAX_RECTS, TEXT_QUOTE_MAX, clamp_span, from_scaled,
                              is_highlight, is_region, is_text_range, page_of, position, previous_place, span_text,
                              text_position, text_span, valid_text_closest, valid_text_prev)
from gamma.pdf_export import annotate_pdf
from test_pdf_export import PAGE_H, PAGE_W, _blank_pdf

USER, PASSWORD = "hs_owner", "pw-hs-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


# --- the shape -----------------------------------------------------------------------

def test_a_position_is_the_page_its_size_once_and_bare_rects():
    pos = position(3, 612, 792, [(10, 20, 30, 40), (5, 50, 25, 60)], area=True)
    assert pos == {"pageNumber": 3, "width": 612, "height": 792,
                   "boundingRect": {"x1": 5, "y1": 20, "x2": 30, "y2": 60},
                   "rects": [{"x1": 10, "y1": 20, "x2": 30, "y2": 40}, {"x1": 5, "y1": 50, "x2": 25, "y2": 60}],
                   "area": True}
    assert page_of({"pdf_position": pos}) == 3 and page_of({"pdf_position": {"pageNumber": True}}) is None
    assert page_of({"pdf_page": 3}) is None  # the position's page, nothing else
    assert is_highlight({"pdf_position": {"pageNumber": 1}}) and is_highlight({"pdf_position": pos, "link_url": "x"})
    assert not any(is_highlight(p) for p in (
        {}, {"pdf_position": None}, {"ink_url": "", "pdf_position": pos},
        {"text_box": {}, "pdf_position": pos}, {"sheet": {}, "pdf_position": pos}))


def test_from_scaled_keeps_every_rect_in_one_frame():
    """The scaled form (Logseq's EDN, Gamma before step 30): the frame is
    the position's size, else the first measured rect's, and a rect
    measured at another size is scaled into it; a stored position comes
    back as it is."""
    old = {"boundingRect": {"x1": 10, "y1": 10, "x2": 90, "y2": 40, "width": 100, "height": 200, "pageNumber": 2},
           "rects": [{"x1": 10, "y1": 10, "x2": 90, "y2": 20, "width": 100, "height": 200, "pageNumber": 2},
                     {"x1": 20, "y1": 60, "x2": 100, "y2": 80, "width": 200, "height": 400}]}
    new = from_scaled(old)
    assert new == {"pageNumber": 2, "width": 100, "height": 200,
                   "boundingRect": {"x1": 10, "y1": 10, "x2": 90, "y2": 40},
                   "rects": [{"x1": 10, "y1": 10, "x2": 90, "y2": 20}, {"x1": 10.0, "y1": 30.0, "x2": 50.0, "y2": 40.0}]}
    assert from_scaled(new) == new
    # the page from the caller when the position names none; no rect, no place
    assert from_scaled({"boundingRect": {"x1": 1, "y1": 1, "x2": 2, "y2": 2}}, 5)["pageNumber"] == 5
    assert from_scaled({"boundingRect": {}, "rects": []}, 4) == {"pageNumber": 4}
    assert from_scaled({"rects": [{"x1": 1, "y1": 1, "x2": 2, "y2": 2}]}) is None  # no page anywhere
    # an area flag inside the bounding rect, where Logseq-era positions kept it
    assert from_scaled({"pageNumber": 1, "boundingRect": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "area": True}})["area"]


def test_from_scaled_keeps_what_the_relocation_wrote_on_a_stored_position():
    """A replaced PDF's relocation (gamma/highlight_relocate.py) marks a
    position with the file its rects were measured on, lost, placed by
    estimate, or matched roughly with how alike it read; a restore passes
    every position through from_scaled, which must keep them."""
    lost = {**position(1, 612, 792, [(72, 90, 300, 104)]), "doc_id": "a" * 64, "lost": True}
    approx = {**position(2, 612, 792, [(10, 20, 110, 120)], area=True), "approx": True}
    rough = {**position(1, 612, 792, [(72, 90, 300, 104)]), "approx": True, "match": 0.87}
    assert from_scaled(lost) == lost and from_scaled(approx) == approx and from_scaled(rough) == rough
    # only those keys, and only in their own form
    odd = {**position(1, 612, 792, [(1, 1, 2, 2)]), "doc_id": "", "lost": "yes", "usePdfCoordinates": True}
    assert from_scaled(odd) == position(1, 612, 792, [(1, 1, 2, 2)])
    for match in ("0.9", True, 1.5, -0.1, None):
        assert from_scaled({**position(1, 612, 792, [(1, 1, 2, 2)]), "match": match}) == position(1, 612, 792, [(1, 1, 2, 2)])


def test_from_scaled_keeps_where_a_pending_highlight_was_before_only_in_its_own_form():
    """A dashed placement's ``prev`` (where the highlight was before the
    run, for the review card): a file, a page, rects as page fractions and
    a quote; kept as it is, its quote clipped, anything else dropped."""
    prev = {"doc_id": "a" * 64, "pageNumber": 3, "rects": [[0.1176, 0.1136, 0.4902, 0.1313], [0, 0, 1, 1]],
            "quote": "the passage as it read"}
    rough = {**position(1, 612, 792, [(72, 90, 300, 104)]), "approx": True, "match": 0.87, "prev": prev}
    assert from_scaled(rough) == rough
    # no rects (a position without a frame), an area's empty quote, a file no longer stored: still its form
    for ok in ({**prev, "rects": [], "quote": ""}, {**prev, "doc_id": "unstored"}):
        assert from_scaled({**rough, "prev": ok})["prev"] == ok
    # the quote clipped to PREV_MAX_QUOTE characters, the rects at most PREV_MAX_RECTS
    assert from_scaled({**rough, "prev": {**prev, "quote": "x" * 5000}})["prev"]["quote"] == "x" * PREV_MAX_QUOTE
    assert from_scaled({**rough, "prev": {**prev, "rects": [[0, 0, 1, 1]] * PREV_MAX_RECTS}})["prev"]["rects"] == [
        [0, 0, 1, 1]] * PREV_MAX_RECTS
    bare = {k: v for k, v in rough.items() if k != "prev"}
    for bad in ("prev", [prev], {**prev, "doc_id": 7}, {**prev, "doc_id": None}, {**prev, "pageNumber": 0},
                {**prev, "pageNumber": "3"}, {**prev, "pageNumber": True}, {**prev, "quote": None},
                {k: v for k, v in prev.items() if k != "quote"}, {**prev, "rects": "0,0,1,1"},
                {**prev, "rects": [[0, 0, 1]]}, {**prev, "rects": [[0, 0, 1, 1.5]]}, {**prev, "rects": [[0, -0.1, 1, 1]]},
                {**prev, "rects": [[0, 0, "1", 1]]}, {**prev, "rects": [[0, 0, True, 1]]}, {**prev, "rects": [{"x1": 0}]},
                {**prev, "rects": [[0, 0, 1, 1]] * (PREV_MAX_RECTS + 1)}):
        assert from_scaled({**rough, "prev": bad}) == bare, bad


def test_from_scaled_keeps_a_lost_highlight_s_closest_place_only_in_its_own_form():
    """A lost highlight's ``closest`` (the best place the run found under the
    threshold, for the review card): a place in the page's file in the
    stored shape, how alike it read and its text there; kept as it is, its
    quote clipped and its rects bare, anything else dropped."""
    closest = {**position(2, 612, 792, [(72, 300, 400, 314), (72, 314, 200, 328)]), "match": 0.71,
               "quote": "the place most alike"}
    lost = {**position(1, 612, 792, [(72, 90, 300, 104)]), "doc_id": "a" * 64, "lost": True, "closest": closest}
    assert from_scaled(lost) == lost
    assert from_scaled({**lost, "closest": {**closest, "match": 0, "quote": ""}})["closest"] == {
        **closest, "match": 0, "quote": ""}
    assert from_scaled({**lost, "closest": {**closest, "quote": "x" * 5000}})["closest"]["quote"] == "x" * PREV_MAX_QUOTE
    many = {**closest, **position(2, 612, 792, [(1, 1, 2, 2)] * PREV_MAX_RECTS)}
    assert from_scaled({**lost, "closest": many})["closest"] == many
    # only its own keys, a rect's included
    extra = {**closest, "lost": True, "doc_id": "b" * 64, "rects": [{**r, "width": 612} for r in closest["rects"]],
             "boundingRect": {**closest["boundingRect"], "pageNumber": 2}}
    assert from_scaled({**lost, "closest": extra})["closest"] == closest
    bare = {k: v for k, v in lost.items() if k != "closest"}
    without = lambda key: {k: v for k, v in closest.items() if k != key}  # noqa: E731
    for bad in ("closest", [closest], None, {**closest, "pageNumber": 0}, {**closest, "pageNumber": "2"},
                {**closest, "pageNumber": True}, without("pageNumber"), {**closest, "width": 0}, {**closest, "height": -1},
                {**closest, "width": "612"}, without("height"), {**closest, "boundingRect": None},
                {**closest, "boundingRect": {"x1": 1, "y1": 1, "x2": 2}}, without("boundingRect"), {**closest, "rects": []},
                {**closest, "rects": "72,300,400,314"}, {**closest, "rects": [[72, 300, 400, 314]]},
                {**closest, "rects": [{"x1": 1, "y1": 1, "x2": "2", "y2": 2}]},
                {**closest, "rects": [{"x1": 1, "y1": 1, "x2": True, "y2": 2}]},
                {**closest, "rects": [{"x1": 1, "y1": 1, "x2": 2, "y2": 2}] * (PREV_MAX_RECTS + 1)},
                without("rects"), {**closest, "match": 1.5}, {**closest, "match": -0.1}, {**closest, "match": "0.7"},
                {**closest, "match": True}, without("match"), {**closest, "quote": None}, {**closest, "quote": 7},
                without("quote")):
        assert from_scaled({**lost, "closest": bad}) == bare, bad


def test_a_prev_is_its_position_measured_as_fractions_of_its_page():
    pos = position(2, 612, 792, [(61.2, 79.2, 306, 158.4), (-6, 700, 650, 800)])
    assert previous_place(pos, "v1", "a quote") == {
        "doc_id": "v1", "pageNumber": 2, "quote": "a quote",
        "rects": [[0.1, 0.1, 0.5, 0.2], [0.0, round(700 / 792, 4), 1.0, 1.0]]}  # clamped to the page
    assert previous_place(pos, "v1", "y" * 2000)["quote"] == "y" * PREV_MAX_QUOTE
    many = position(1, 100, 100, [(1, 1, 2, 2)] * (PREV_MAX_RECTS + 5))
    assert len(previous_place(many, "v1")["rects"]) == PREV_MAX_RECTS
    # a position without a frame has no measure for its rects
    assert previous_place({k: v for k, v in pos.items() if k not in ("width", "height")}, "v1")["rects"] == []


# --- a range of a text file ----------------------------------------------------------------

RANGE = {"line": 2, "col": 4, "end": 3, "end_col": 6, "prefix": "a", "suffix": "b", "hash": "9f2c"}


def test_a_text_range_is_its_own_kind_beside_the_other_two():
    assert is_text_range({"text_position": RANGE}) and is_text_range({"text_position": {}, "link_url": "x"})
    assert is_text_range({"text_position": RANGE, "pdf_position": {"pageNumber": 1}})
    assert not any(is_text_range(p) for p in (
        None, {}, {"text_position": None}, {"text_position": "1-3"}, {"text_position": [1, 3]},
        {"ink_url": "", "text_position": RANGE}, {"text_box": {}, "text_position": RANGE},
        {"picture": {}, "text_position": RANGE}, {"sheet": {}, "text_position": RANGE}))
    # a range is on no PDF page and no region
    assert page_of({"text_position": RANGE}) is None
    assert not is_highlight({"text_position": RANGE}) and not is_region({"text_position": RANGE})


def test_a_text_span_reads_only_a_well_formed_range():
    assert text_span({"text_position": RANGE}) == (2, 4, 3, 6)
    assert text_span({"text_position": {**RANGE, "end": 2, "end_col": 5}}) == (2, 4, 2, 5)
    assert text_span({"text_position": {**RANGE, "end_col": 0}}) == (2, 4, 3, 0)  # a later line's start, before the trim
    # a whole number written with a point, which a browser reads as one (blockModel.textSpan)
    assert text_span({"text_position": {**RANGE, "line": 2.0}}) == (2, 4, 3, 6)
    for bad in ({**RANGE, "line": 0}, {**RANGE, "line": True}, {**RANGE, "col": -1}, {**RANGE, "end": 1},
                {**RANGE, "end_col": -1}, {**RANGE, "end": 2, "end_col": 4}, {**RANGE, "end": 2, "end_col": 3},
                {**RANGE, "line": 2.5}, {**RANGE, "end": float("nan")}, {**RANGE, "col": "4"}, {**RANGE, "end_col": None},
                {k: v for k, v in RANGE.items() if k != "end"}, {}):
        assert text_span({"text_position": bad}) is None, bad
    assert text_span({"text_box": {}, "text_position": RANGE}) is None and text_span(None) is None


def test_a_text_position_is_its_span_context_and_version():
    pos = text_position(118, 4, 120, 37, hash="9f2c", prefix="p", suffix="s")
    assert list(pos) == ["line", "col", "end", "end_col", "prefix", "suffix", "hash"]
    assert pos == {"line": 118, "col": 4, "end": 120, "end_col": 37, "prefix": "p", "suffix": "s", "hash": "9f2c"}
    assert text_span({"text_position": pos}) == (118, 4, 120, 37)


def test_a_text_range_s_prev_and_closest_only_in_their_own_form():
    prev = {"hash": "v1", "line": 3, "col": 4, "end": 5, "end_col": 2, "quote": "where it was"}
    assert valid_text_prev(prev) == prev and valid_text_prev({**prev, "end": 3, "end_col": 9, "extra": 1}) == {
        **prev, "end": 3, "end_col": 9}
    # a range's whole quote, not a PDF's first PREV_MAX_QUOTE characters: a later run matches it by this
    assert valid_text_prev({**prev, "quote": "x" * 5000})["quote"] == "x" * 5000
    assert valid_text_prev({**prev, "quote": "x" * (TEXT_QUOTE_MAX + 5)})["quote"] == "x" * TEXT_QUOTE_MAX
    for bad in (None, "prev", [prev], {**prev, "hash": None}, {**prev, "hash": 7}, {**prev, "line": 0},
                {**prev, "line": True}, {**prev, "end": 2}, {**prev, "end": "5"}, {**prev, "quote": None},
                {**prev, "end": 3, "end_col": 4}, {k: v for k, v in prev.items() if k != "col"},
                {k: v for k, v in prev.items() if k != "quote"}):
        assert valid_text_prev(bad) is None, bad
    closest = {"line": 9, "col": 0, "end": 9, "end_col": 12, "match": 0.71, "quote": "most alike", "hash": "v2"}
    assert valid_text_closest(closest) == closest and list(valid_text_closest({**closest, "lost": True})) == list(closest)
    assert valid_text_closest({**closest, "match": 0, "quote": ""}) == {**closest, "match": 0, "quote": ""}
    assert valid_text_closest({**closest, "quote": "y" * (TEXT_QUOTE_MAX + 5)})["quote"] == "y" * TEXT_QUOTE_MAX
    for bad in (None, [closest], {**closest, "line": 0}, {**closest, "end": 8}, {**closest, "end_col": 0},
                {**closest, "col": -1}, {**closest, "match": 1.5}, {**closest, "match": -0.1},
                {**closest, "match": "0.7"}, {**closest, "match": True}, {**closest, "quote": 7},
                {**closest, "hash": None}, {k: v for k, v in closest.items() if k != "hash"}):
        assert valid_text_closest(bad) is None, bad


def test_a_span_s_text_is_cut_from_the_lines_by_code_points():
    lines = ["def f(x):", "    return x", "", "f(1)"]
    assert span_text(lines, (2, 4, 2, 10)) == ("return", "def f(x):\n    ", " x\n\nf(1)")
    assert span_text(lines, {"line": 1, "col": 4, "end": 2, "end_col": 12}) == ("f(x):\n    return x", "def ", "\n\nf(1)")
    # an end at column 0 of a later line, past an empty one too, ends at the line before
    assert clamp_span(lines, (2, 4, 4, 0)) == (2, 4, 2, 12)
    assert span_text(lines, (2, 4, 4, 0)) == span_text(lines, (2, 4, 2, 12)) == ("return x", "def f(x):\n    ", "\n\nf(1)")
    # columns past their lines clamped, an end past the file at its last line, a start at its line's end
    # on the next line; a line break alone is nothing
    assert clamp_span(lines, (1, 40, 2, 99)) == (2, 0, 2, 12) and clamp_span(lines, (2, 0, 9, 3)) == (2, 0, 4, 4)
    assert clamp_span(lines, (2, 12, 4, 2)) == (4, 0, 4, 2), "past the empty line too"
    assert clamp_span(lines, (1, 9, 2, 0)) is None and span_text(lines, (1, 9, 2, 0)) == ("", "", "")
    assert clamp_span(lines, (5, 0, 5, 1)) is None and clamp_span([], (1, 0, 1, 1)) is None
    # the context is 40 code points either side, line breaks kept; an astral character is one
    long = ["\U0001F600" * 45 + "name" + "\U0001F680" * 45]
    assert span_text(long, (1, 45, 1, 49)) == ("name", "\U0001F600" * CONTEXT_CHARS, "\U0001F680" * CONTEXT_CHARS)
    # a quote at the cap is its first TEXT_QUOTE_MAX code points, never ending in a line break, its suffix after them
    big = ["x" * (TEXT_QUOTE_MAX - 1), "y" * 50, "tail"]
    quote, prefix, suffix = span_text(big, (1, 0, 3, 4))
    assert quote == "x" * (TEXT_QUOTE_MAX - 1) and prefix == "" and suffix == ("\n" + "y" * 50)[:CONTEXT_CHARS]
    quote, _, suffix = span_text(["x" * TEXT_QUOTE_MAX + "z", "w"], (1, 0, 2, 1))
    assert quote == "x" * TEXT_QUOTE_MAX and suffix == "z\nw"


def test_the_highlight_shape_leaves_a_text_position_alone():
    """Step 30's rewrite (run on a restore too) reshapes ``pdf_position``
    alone: a range's position comes through untouched, beside a page
    number it drops."""
    import json
    import sqlite3

    from gamma.normalize import highlight_shape
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, page_id TEXT, properties TEXT)")
    ranged = {"text_position": {**RANGE, "approx": True, "match": 0.8, "prev": {"hash": "v0", "line": 1, "end": 1,
                                                                                "quote": "q"}}, "pdf_page": 2, "quote": "q"}
    scaled = {"pdf_position": {"boundingRect": {"x1": 1, "y1": 1, "x2": 2, "y2": 2, "width": 10, "height": 10},
                               "rects": [], "pageNumber": 1}, "text_position": RANGE}
    conn.executemany("INSERT INTO unified_blocks VALUES (?, 'p', ?)",
                     [("r", json.dumps(ranged)), ("s", json.dumps(scaled))])
    assert highlight_shape(conn)
    got = {bid: json.loads(raw) for bid, raw in conn.execute("SELECT id, properties FROM unified_blocks")}
    assert got["r"] == {k: v for k, v in ranged.items() if k != "pdf_page"}
    assert got["s"]["text_position"] == RANGE and got["s"]["pdf_position"]["width"] == 10


# --- the writers -------------------------------------------------------------------------

def _upload(c, data, name="p.pdf"):
    r = c.post("/api/uploads", files={"file": (name, io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_annotation_importer_writes_the_shape_under_the_block_id(owner):
    rect = {"x1": 100, "y1": 72, "x2": 300, "y2": 92}
    pdf, _ = annotate_pdf(_blank_pdf(), [
        {"position": {"pageNumber": 1, "width": PAGE_W, "height": PAGE_H, "boundingRect": rect, "rects": [rect]},
         "color": "rgba(170, 235, 170, 0.65)", "note": "a note"},
        {"position": {"pageNumber": 1, "width": PAGE_W, "height": PAGE_H, "boundingRect": rect, "rects": [rect],
                      "area": True}, "color": None, "note": "", "id": "area"}])
    up = _upload(owner, pdf)
    page = owner.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "Imported"}).json()
    r = owner.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": up["doc_id"]})
    assert r.status_code == 200 and r.json()["imported"] == 2, r.text
    kids = owner.get(f"/api/blocks/{page['id']}/children").json()["children"]
    assert [k["kind"] for k in kids] == ["highlight", "highlight"]
    for k in kids:
        props = k["properties"]
        assert "highlight_id" not in props and "pdf_page" not in props
        pos = props["pdf_position"]
        assert (pos["pageNumber"], pos["width"], pos["height"]) == (1, PAGE_W, PAGE_H)
        assert all(set(r) == {"x1", "y1", "x2", "y2"} for r in (pos["boundingRect"], *pos["rects"]))
        assert abs(pos["boundingRect"]["x1"] - 100) < 0.01 and abs(pos["boundingRect"]["y2"] - 92) < 0.01
    assert [bool(k["properties"]["pdf_position"].get("area")) for k in kids] == [False, True]


EDN = ('{:highlights [{:id #uuid "6500e1f4-0000-4000-8000-0000000000aa" :page 2 :position '
       '{:bounding {:x1 10 :y1 20 :x2 110 :y2 60 :width 600 :height 800} '
       ':rects [{:x1 10 :y1 20 :x2 110 :y2 40 :width 600 :height 800}] :page 2} '
       ':content {:text "an edn quote"} :properties {:color "green"}}]}')
MD = ("- an edn quote\n  ls-type:: annotation\n  hl-page:: 2\n  id:: 6500e1f4-0000-4000-8000-0000000000aa\n"
      "- a quote the EDN lost\n  ls-type:: annotation\n  hl-page:: 7\n")


def test_the_logseq_importer_writes_the_shape(owner):
    from test_writer_races import _pdf

    pdf = _pdf("hs-logseq")
    r = owner.post("/api/import/logseq", files={
        "pdf": ("paper.pdf", pdf, "application/pdf"),
        "edn": ("hls.edn", EDN.encode(), "application/octet-stream"),
        "md": ("hls.md", MD.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    kids = {k["properties"]["quote"]: k for k in
            owner.get(f"/api/blocks/{r.json()['block_id']}/children").json()["children"]}
    placed = kids["an edn quote"]["properties"]
    assert placed["pdf_position"] == {"pageNumber": 2, "width": 600, "height": 800,
                                      "boundingRect": {"x1": 10, "y1": 20, "x2": 110, "y2": 60},
                                      "rects": [{"x1": 10, "y1": 20, "x2": 110, "y2": 40}]}
    # no box in the EDN: its page alone, still a highlight
    lost = kids["a quote the EDN lost"]
    assert lost["properties"]["pdf_position"] == {"pageNumber": 7} and lost["kind"] == "highlight"
    assert not any("highlight_id" in k["properties"] or "pdf_page" in k["properties"] for k in kids.values())


# --- the PDF's URL ---------------------------------------------------------------------

def test_a_pages_pdf_url_is_derived_from_its_doc_id(owner):
    """No writer stores the stored copy's URL: a document's own stored
    address is no address (documents.model.document), on by-doc, the op
    path and a page created with it; an address of its own stays. An older
    client's flat source_url is refused."""
    up = _upload(owner, b"%PDF-1.4 the derived url\n")
    doc, url = up["doc_id"], up["source_url"]
    assert url == f"/api/uploads/{doc}.pdf"  # the upload still answers where its file is
    stored = {"format": "pdf", "name": "", "hash": doc, "ext": ".pdf", "at": "store"}
    page = owner.post(f"/api/blocks/by-doc/{doc}", json={"default_title": "By doc", "source_url": url}).json()
    assert page["properties"]["doc"] == stored and page["content"] == "By doc"
    r = owner.post(f"/api/pages/{page['id']}/ops", json={"client": "old", "ops": [
        {"op": "set", "id": page["id"], "props": {"source_url": url}}]})
    assert r.status_code == 400, r.text
    r = owner.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": page["id"], "props": {"doc": {**stored, "at": "url", "url": url}}}]})
    assert r.status_code == 200 and r.json()["ops"][0]["props"] == {"doc": stored}, r.text
    assert owner.get(f"/api/blocks/{page['id']}").json()["properties"]["doc"] == stored
    a = {**stored, "hash": "a" * 24}
    made = owner.post("/api/pages", json={"title": "Made", "properties": {
        "doc": {**a, "at": "url", "url": f"/api/uploads/{'a' * 24}.pdf"}}})
    assert made.status_code == 200 and made.json()["properties"] == {"doc": a}, made.text
    own = make_page(owner, "Proxied", properties=doc_props("b" * 24, url="https://x.test/p.pdf"))
    assert owner.get(f"/api/blocks/{own['id']}").json()["properties"]["doc"]["url"] == "https://x.test/p.pdf"


def test_a_lookup_by_the_stored_copys_url_still_finds_the_page(owner):
    up = _upload(owner, b"%PDF-1.4 looked up by its url\n")
    page = owner.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "Looked up"}).json()
    r = owner.get("/api/library/lookup", params={"url": up["source_url"]})
    assert r.status_code == 200 and r.json()["block_id"] == page["id"], r.text
