"""Highlights after a PDF is replaced (gamma/highlight_relocate.py,
PUT /api/pages/{id}/attachment's ``relocate-highlights`` job): each one is
found again in the new file by its text and moved there, marked ``approx``
with its ``match`` when found only roughly; the ones whose text is gone are
marked ``lost``; an area highlight follows the text or the picture inside
it, or its page's other highlights."""

import io
import threading
import time

import pymupdf
import pytest
from conftest import guest_name, workspace_of

from gamma import highlight_relocate as hr
from gamma import jobs, storage
from gamma.highlights import PREV_MAX_QUOTE, from_scaled, previous_place
from gamma.pdf_text import GlyphPages, has_box

W, H = 612, 792
SIZE = 11
DEFAULTS = {"min_match": hr.ROUGH_APPLY, "exact_chars": hr.ROUGH_MIN_CHARS}  # the thresholds of an account that set none


def _png(color):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.set_rect(pix.irect, color)
    return pix.tobytes("png")


RED, BLUE = _png((200, 30, 30)), _png((30, 30, 200))


def _pdf(pages, *, rotate=0, cropbox=None):
    """A PDF of ``pages``, each a list of ``(x, y, text)`` lines (``y`` the
    baseline, top-left origin) and optionally ``("image", rect, png)``
    pictures, in Helvetica at SIZE points."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=W, height=H)
        for line in lines:
            if line[0] == "image":
                page.insert_image(pymupdf.Rect(*line[1]), stream=line[2])
            else:
                x, y, text = line
                page.insert_text((x, y), text, fontname="helv", fontsize=SIZE)
        if cropbox:
            page.set_cropbox(pymupdf.Rect(*cropbox))
        if rotate:
            page.set_rotation(rotate)
    return doc.tobytes()


def _width(text):
    return pymupdf.get_text_length(text, fontname="helv", fontsize=SIZE)


def _line_rect(x, y, text):
    """The viewer-frame rect of a line drawn at ``(x, y)`` on an unrotated,
    uncropped page: the frame is the page in points, top-left origin."""
    return {"x1": round(x, 2), "y1": round(y - 0.9 * SIZE, 2), "x2": round(x + _width(text), 2), "y2": round(y + 0.3 * SIZE, 2)}


def _part_rect(x, y, line, part):
    """The rect of ``part`` (its first copy) in a line drawn at ``(x, y)``."""
    return _line_rect(x + _width(line[:line.index(part)]), y, part)


def _position(page_no, rects, area=False):
    rects = rects if isinstance(rects, list) else [rects]
    bound = {"x1": min(r["x1"] for r in rects), "y1": min(r["y1"] for r in rects),
             "x2": max(r["x2"] for r in rects), "y2": max(r["y2"] for r in rects)}
    pos = {"pageNumber": page_no, "width": W, "height": H, "boundingRect": bound, "rects": rects}
    if area:
        pos["area"] = True
    return pos


def _prev(pos, doc_id, quote=""):
    """The ``prev`` a run writes on a highlight it draws dashed, whose
    position before was ``pos`` (on an unrotated, uncropped page, as
    ``_position`` makes it) measured on the file ``doc_id``: its rects as
    fractions of the page."""
    return {"doc_id": doc_id, "pageNumber": pos["pageNumber"], "quote": quote,
            "rects": [[round(r["x1"] / W, 4), round(r["y1"] / H, 4), round(r["x2"] / W, 4), round(r["y2"] / H, 4)]
                      for r in pos["rects"]]}


def _measured(pdf_bytes, page_no, *texts):
    """The position the relocation gives ``texts`` (each its first copy)
    where page ``page_no`` of a file draws them: a highlight made right on
    them."""
    with hr.Doc(pdf_bytes) as doc:
        page = doc.text(page_no)
        boxes = []
        for text in texts:
            at = page.text.index(hr.normalize_quote(text))
            boxes += hr.rects_for_span(page, at, at + len(hr.normalize_quote(text)))
        return hr._position(page_no, doc.frame(page_no), boxes)


def _texts(pdf_bytes):
    with GlyphPages(pdf_bytes) as glyphs:
        return {n: hr.PageText(glyphs.page(n)) for n in range(1, glyphs.page_count() + 1)}


def _lines_of(glyphs):
    """MuPDF's lines of a glyph list, as strings."""
    return "".join(ch for ch, _box in glyphs).split("\n")[:-1]


# --- the text of a page and of a quote ------------------------------------------------

def _glyphs(*lines):
    """A glyph list like GlyphPages.page's: each line's characters with a
    box, a boxless break after every line."""
    out, y = [], 700.0
    for line in lines:
        x = 72.0
        for ch in line:
            out.append((ch, (x, y - 2, x + 6, y + 8)))
            x += 6
        out.append(("\n", (0.0, 0.0, 0.0, 0.0)))
        y -= 12
    return out


def _pages(*texts):
    """``PageText``s from plain strings, a page each, its lines split at
    ``\\n`` (each character a box 6 wide, a line 12 below the one before)."""
    return {n: hr.PageText(_glyphs(*text.split("\n"))) for n, text in enumerate(texts, start=1)}


def test_a_page_reads_as_one_folded_string_with_hyphenated_words_joined():
    page = hr.PageText(_glyphs("The entangle-", "ment of “Rydberg” atoms — ﬁne", "and a 2-level system"))
    assert page.text == "the entanglement of \"rydberg\" atoms - fine and a 2-level system"
    # every character knows its glyph, and a glyph its first character
    gi = page.glyph_at[page.text.index("rydberg")]
    assert page.char(gi) == "R"
    assert page.text[page.pos_of_glyph(gi):].startswith("rydberg")
    i = page.text.index("fine")
    assert page.original(i, i + 4) == "ﬁne"
    # as the file spells it: its case, and the word hyphenated at the line's end whole, as the text reads it
    assert page.original(0, page.text.index(" of") + 3) == "The entanglement of"
    i = page.text.index("2-level")
    assert page.original(i, i + 7) == "2-level"
    # the same through a real PDF (ASCII, which Helvetica has)
    pdf = _pdf([[(72, 100, "The entangle-"), (72, 114, "ment of atoms")]])
    assert _texts(pdf)[1].text == "the entanglement of atoms"


def test_a_page_keeps_each_glyph_s_char_and_box_compactly():
    pdf = _pdf([[(72, 100, LINE_A), (72, 114, "The entangle-"), (72, 128, "ment of atoms")]])
    with GlyphPages(pdf) as glyphs:
        raw = glyphs.page(1)
    page = hr.PageText(raw)
    assert page.glyph_count == len(raw)
    assert [(page.char(gi), page.box(gi)) for gi in range(page.glyph_count)] == raw


def test_a_line_that_is_a_number_alone_is_left_out_of_the_page():
    body = ["The first line of the body text runs here", "and the second line continues the thought",
            "while the third line ends the sentence."]
    numbered = _pdf([[line for i, t in enumerate(body) for line in ((72, 100 + 14 * i, t), (50, 100 + 14 * i, str(i + 1)))]
                     + [(300, 760, "12")]])
    with GlyphPages(numbered) as glyphs:
        raw = glyphs.page(1)
    assert _lines_of(raw) == [body[0], "1", body[1], "2", body[2], "3", "12"]  # MuPDF reads each number as a line
    page = hr.PageText(raw)
    assert page.text == " ".join(body).lower()
    assert not any(ch.isdigit() for gi in range(page.glyph_count) for ch in page.char(gi))
    # a number inside a line stays
    assert hr.PageText(_glyphs("held at 4 K for 12 hours")).text == "held at 4 k for 12 hours"


def test_a_quote_folds_like_the_page():
    assert hr.normalize_quote("The Entangle-\nment of “Rydberg”  atoms — ﬁne") == "the entanglement of \"rydberg\" atoms - fine"
    assert hr.normalize_quote("state-\nof-the-art") == "stateof-the-art"  # a hyphen that was a real one is lost, the word still found fuzzily
    assert hr.normalize_quote("") == ""


# --- the passage under a highlight ------------------------------------------------------

def test_the_passage_under_a_highlight_is_the_text_it_covers_read_as_a_selection():
    l1, l2 = "Here we reach the end of a line", "next words follow on the line below"
    h1, h2 = "This is the entangle-", "ment of two atoms in a trap"
    pdf = _pdf([[(72, 100, l1), (72, 114, l2), (72, 200, h1), (72, 214, h2)]])
    with hr.Doc(pdf) as doc:
        across = _position(1, [_part_rect(72, 100, l1, "the end of a line"), _part_rect(72, 114, l2, "next words follow")])
        needle, (before, after), anchor, pieces = hr._old_passage(doc, across)
        assert needle == "the end of a line next words follow" and anchor is None
        assert before.endswith("here we reach ") and after.startswith(" on the line below")
        assert [(p.text, p.skipped) for p in pieces] == [(needle, 0)]  # a line's end skips no word
        hyphen = _position(1, [_part_rect(72, 200, h1, "entangle-"), _part_rect(72, 214, h2, "ment of two")])
        assert hr._old_passage(doc, hyphen)[0] == "entanglement of two"
        assert hr._old_passage(doc, _position(1, _line_rect(72, 400, l1))) is None  # nothing under it


COL1 = ["Earlier work used slow methods to", "prepare the state, while in this", "work we propose and test a"]
BETWEEN = ["Figure 1: A long caption that runs under both of the columns and that the file",
           "draws between the two columns in its content stream, so that it reads in between"]
COL2 = ["new method for entangle-", "ment of two atoms in a trap with", "light at a fixed detuning."]
ACROSS_QUOTE = "test a new method for entangle-\nment of two atoms"  # what the viewer stores for the highlight below


def _columns(dy=0, between=BETWEEN, col2=COL2, first=COL1):
    """A page's lines in two columns, ``first`` at x 72 and ``col2`` at x
    320 from y 100 + ``dy``, with ``between`` (a caption under them, at y
    600 + ``dy``) drawn after the first column and before the second, so
    that MuPDF reads it between the two."""
    return ([(72, 100 + dy + 14 * i, t) for i, t in enumerate(first)]
            + [(72, 600 + dy + 14 * i, t) for i, t in enumerate(between)]
            + [(320, 100 + dy + 14 * i, t) for i, t in enumerate(col2)])


def _across_columns(dy=0):
    """The rects of a highlight from "test a" at the first column's foot of
    ``_columns(dy)`` to "ment of two atoms" in the second column."""
    return [_part_rect(72, 128 + dy, COL1[2], "test a"), _line_rect(320, 100 + dy, COL2[0]),
            _part_rect(320, 114 + dy, COL2[1], "ment of two atoms")]


def test_a_highlight_across_two_columns_skips_the_caption_drawn_between_them():
    old = _pdf([_columns()])
    with GlyphPages(old) as glyphs:
        assert _lines_of(glyphs.page(1))[2:5] == [COL1[2], BETWEEN[0], BETWEEN[1]]  # the caption between the columns
    with hr.Doc(old) as doc:
        needle, _context, _anchor, pieces = hr._old_passage(doc, _position(1, _across_columns()))
    assert needle == "test a new method for entanglement of two atoms"
    # the pieces the file read with no word between: the caption's words come between the two
    assert [(p.text, p.skipped) for p in pieces] == [("test a", 0), ("new method for entanglement of two atoms", 31)]
    new = _pdf([[(72, 300, "Earlier work used slow methods to prepare the state, while in this work we propose"),
                 (72, 314, "and test a new method for entanglement of two atoms in a trap with light at a fixed"),
                 (72, 328, "detuning."), (72, 600, BETWEEN[0]), (72, 614, BETWEEN[1])]])
    items = [("hl", {"pdf_position": {**_position(1, _across_columns()), "doc_id": "v1"}, "quote": ACROSS_QUOTE})]
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"hl": "moved"}
    op = placed["ops"]["hl"]
    assert "quote" not in op  # found exactly: the highlight's words are the file's
    rects = op["pdf_position"]["rects"]
    assert len(rects) == 1 and 299 < rects[0]["y1"] < 319  # all on the new file's second line
    assert rects[0]["x1"] == pytest.approx(72 + _width("and "), abs=0.6)


def test_a_highlight_across_two_columns_is_found_piece_by_piece_while_the_caption_stays_between_them():
    old = _pdf([_columns()])

    def place(new):
        items = [("hl", {"pdf_position": {**_position(1, _across_columns()), "doc_id": "v1"}, "quote": ACROSS_QUOTE})]
        with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
            return hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)

    # the same two columns 40 points lower: no page reads the passage whole, so it is found piece by piece,
    # one rect per piece's line, at the column's foot and the next column's top
    shifted = _pdf([_columns(40)])
    placed = place(shifted)
    assert placed["kinds"] == {"hl": "moved"}
    assert placed["ops"]["hl"] == {"pdf_position": _measured(shifted, 1, "test a", "new method for entanglement of two atoms")}
    assert len(placed["ops"]["hl"]["pdf_position"]["rects"]) == 3
    # a word changed in the second piece and a longer caption: that piece found roughly, the chain dashed with
    # the confidence of the pieces joined, the quote respelled as the new file reads it, each piece's own words
    # and the word hyphenated at a line's end whole
    scheme = COL2[0].replace("method", "scheme")
    edited = _pdf([_columns(40, between=[BETWEEN[0], BETWEEN[1] + " and on"], col2=[scheme, COL2[1], COL2[2]])])
    placed = place(edited)
    assert placed["kinds"] == {"hl": "approx"}
    match = round(hr._similar("test a new method for entanglement of two atoms", "test a new scheme for entanglement of two atoms"), 2)
    before = _prev(_position(1, _across_columns()), "v1", ACROSS_QUOTE)
    rough = {"pdf_position": {**_measured(edited, 1, "test a", "new scheme for entanglement of two atoms"),
                              "approx": True, "match": match, "prev": before},
             "quote": "test a new scheme for entanglement of two atoms"}
    assert placed["ops"]["hl"] == rough
    # a comma added there too: the comma at the match's end stays outside it
    placed = place(_pdf([_columns(40, col2=[scheme, COL2[1].replace("atoms", "atoms,"), COL2[2]])]))
    assert placed["kinds"] == {"hl": "approx"} and placed["ops"]["hl"] == rough
    # the first column's foot now ends a page and the caption heads the next one, the second column after it:
    # the highlight goes to the larger piece's page, covering that piece
    filler = [f"Filler sentence {i} keeps this page full of other words" for i in range(30)]
    broken = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(filler + COL1)],
                   [(72, 50, "Phys. Rev. A 104, 033101 (2021)")] + _columns(dy=0, first=[])])
    placed = place(broken)
    assert placed["kinds"] == {"hl": "moved"}
    assert placed["ops"]["hl"] == {"pdf_position": _measured(broken, 2, "new method for entanglement of two atoms"),
                                   "quote": "new method for entanglement of two atoms"}
    # no piece is found far from the one before: the first column's words a page away are not its foot, and the
    # passage without them, the second piece alone, is the best rough place
    far = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(COL1 + filler)], [(72, 50, "A running head")] + _columns(first=[])])
    placed = place(far)
    assert placed["kinds"] == {"hl": "approx"}
    match = round(hr._similar("test a new method for entanglement of two atoms", "new method for entanglement of two atoms"), 2)
    assert placed["ops"]["hl"] == {"pdf_position": {**_measured(far, 2, "new method for entanglement of two atoms"),
                                                    "approx": True, "match": match, "prev": before},
                                   "quote": "new method for entanglement of two atoms"}


TRAP_COL = ["Optical tweezers hold single atoms at", "a spacing of a few micrometres here.",
            "The trap light is detuned far to the", "red of the D2 line, which keeps the",
            "scattering rate low while the atoms", "are held for several seconds. Similar",
            "schemes [3] were demonstrated with ions", "and with superconducting circuits in",
            "several other groups, and the method", "has since become a standard tool."]
TRAP_SENTENCE = TRAP_COL[2:5] + ["are held for several seconds."]
NEXT_COL = ["Imaging uses an objective that also", "focuses the tweezers, so the two are",
            "aligned by construction. We calibrate", "the trap depth from the light shift of",
            "the cooling transition and find it", "uniform across the array to within",
            "three percent of its mean depth."]


def _two_columns(first=TRAP_COL):
    """``first`` at x 72 from y 100, then ``NEXT_COL`` at x 330 from y 105,
    each line a little below the first column's of the same row."""
    return [(72, 100 + 14 * i, t) for i, t in enumerate(first)] + [(330, 105 + 14 * i, t) for i, t in enumerate(NEXT_COL)]


def test_rects_that_read_a_line_low_are_placed_by_their_quote_s_own_words():
    """Line rects stored in a frame that does not match their scale (here a
    page height a tenth short of the one they were measured at) sit a line
    low on the file's text: they read the sentence from "red of the D2"
    on and the next sentence's start with its citation, which is alike
    enough to count as the quote. Searched for as it read, that passage
    was found roughly in the new file, where the citation was renumbered:
    drawn dashed, its first rect from "red of the D2" and a fourth over
    the next sentence's start. The copy of the quote on the page that the
    rects read most of is the passage instead."""
    old = _pdf([_two_columns()])
    # a line added above, and the next sentence's citation renumbered
    new = _pdf([_two_columns(["A new first line was added in revision."] + [t.replace("[3]", "[4]") for t in TRAP_COL])])
    rects = [_line_rect(72, 128 + 14 * i, t) for i, t in enumerate(TRAP_SENTENCE)]  # the viewer's, over the sentence
    pos = {**_position(1, rects), "height": H * 0.9, "doc_id": "v1"}
    quote = "\n".join(TRAP_SENTENCE)
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        page = old_doc.text(1)
        read = hr._joined(page, hr._stretches(page, hr.glyphs_under(page, hr._quads(pos, old_doc.frame(1)))))
        assert read.startswith("red of the d2 line") and "schemes [3]" in read
        assert hr._agrees(read, hr.normalize_quote(quote))
        needle, (before, after), _anchor, pieces = hr._old_passage(old_doc, pos, hr.normalize_quote(quote))
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": quote})], read_from={"v1": old_doc}.get)
    # the passage is the sentence, with its own surroundings
    assert needle == hr.normalize_quote(" ".join(TRAP_SENTENCE)) and len(pieces) == 1
    assert before.endswith("micrometres here. ") and after.startswith(" similar schemes [3]")
    # found exactly, solid, its rects over its own words alone
    assert placed["kinds"] == {"hl": "moved"}
    assert placed["ops"]["hl"] == {"pdf_position": _measured(new, 1, *TRAP_SENTENCE)}


def test_another_column_s_words_under_a_rect_that_runs_past_its_column_are_not_the_passage():
    """Line rects that run on past the column's edge, over the start of the
    next column's lines, read those words too. Read with the passage, each
    became a piece of its own, found beside it in the new file and given a
    rect of its own there; the quote's own copy on the page is the
    passage."""
    old = _pdf([_two_columns()])
    new = _pdf([_two_columns(["A new first line was added in revision."] + TRAP_COL)])  # the column moved down a line

    def wide(row, part=None):  # a row's rect running on into the next column, unless it ends mid-line
        line = TRAP_COL[row]
        rect = _part_rect(72, 100 + 14 * row, line, part) if part else _line_rect(72, 100 + 14 * row, line)
        return rect if part else {**rect, "x2": 360}

    sentence = TRAP_SENTENCE
    cases = {"the sentence": ([wide(2), wide(3), wide(4), wide(5, sentence[3])], sentence),
             "ending mid-sentence": ([wide(2), wide(3, "red of the D2 line, which keeps")],
                                     sentence[:1] + ["red of the D2 line, which keeps"]),
             "from the paragraph's start": ([wide(0), wide(1), wide(2), wide(3), wide(4), wide(5, sentence[3])],
                                            TRAP_COL[:2] + sentence)}
    with hr.Doc(old) as old_doc:
        page = old_doc.text(1)
        rect = {**_position(1, wide(2)), "rects": [wide(2)]}
        assert len(hr._stretches(page, hr.glyphs_under(page, hr._quads(rect, old_doc.frame(1))))) == 2  # "align" too
    for label, (rects, texts) in cases.items():
        quote = "\n".join(texts)
        pos = {**_position(1, rects), "doc_id": "v1"}
        with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
            needle, _context, _anchor, pieces = hr._old_passage(old_doc, pos, hr.normalize_quote(quote))
            placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": quote})], read_from={"v1": old_doc}.get)
        # found exactly, its rects over its own words alone
        assert placed["ops"]["hl"] == {"pdf_position": _measured(new, 1, *texts)}, label
        assert placed["kinds"] == {"hl": "moved"}, label
        # because what it reads is the passage alone, one piece
        assert needle == hr.normalize_quote(" ".join(texts)) and len(pieces) == 1, label


def test_rects_within_their_quote_s_copy_say_which_of_its_words_are_the_passage():
    # a quote that holds what the rects read (they cover its first words only): the rects' reading, as before
    old = _pdf([_two_columns()])
    rects = [_line_rect(72, 128, TRAP_SENTENCE[0])]
    with hr.Doc(old) as old_doc:
        needle = hr._old_passage(old_doc, _position(1, rects), hr.normalize_quote(" ".join(TRAP_SENTENCE)))[0]
        assert needle == hr.normalize_quote(TRAP_SENTENCE[0])
        # and a quote the page does not hold leaves the reading as it is, past it or not
        assert hr._old_passage(old_doc, _position(1, rects), "not on this page at all")[0] == needle


def test_a_highlight_read_from_the_page_s_own_file_over_its_quote_is_kept_without_a_search(monkeypatch):
    searched = []
    real = hr.find_passage
    monkeypatch.setattr(hr, "find_passage", lambda pages, needle, **kw: searched.append(needle) or real(pages, needle, **kw))
    pdf = _pdf([_columns() + [(72, 700, "A trap of Rb and Cs atoms")]])
    items = [("cols", {"pdf_position": _position(1, _across_columns()), "quote": ACROSS_QUOTE}),
             # a word too short to look for, under its own rect
             ("short", {"pdf_position": _position(1, _part_rect(72, 700, "A trap of Rb and Cs atoms", "Rb")), "quote": "Rb"}),
             # a doc_id naming this very file: written once more without it
             ("named", {"pdf_position": {**_position(1, _part_rect(72, 700, "A trap of Rb and Cs atoms", "Cs atoms")),
                                         "doc_id": "this"}, "quote": "Cs atoms"})]
    with hr.Doc(pdf) as doc:
        placed = hr.place_all(doc, items, read_from=lambda doc_id: doc if doc_id == "this" else None)
    assert placed["kinds"] == {"cols": "kept", "short": "kept", "named": "kept"} and searched == []
    assert placed["ops"] == {"named": {"pdf_position": _position(1, _part_rect(72, 700, "A trap of Rb and Cs atoms", "Cs atoms"))}}


def test_an_area_over_a_table_column_is_measured_alike_in_both_files():
    def table(dx=0, dy=0):
        rows = [("Alpha one", "Beta one"), ("Alpha two", "Beta two"), ("Alpha three", "Beta three")]
        return _pdf([[line for r, (a, b) in enumerate(rows)
                      for line in ((72 + dx, 100 + dy + 14 * r, a), (200 + dx, 100 + dy + 14 * r, b))]])

    column = {"x1": 66, "y1": 88, "x2": 150, "y2": 134}
    with GlyphPages(table()) as glyphs:
        assert _lines_of(glyphs.page(1))[:3] == ["Alpha one", "Beta one", "Alpha two"]  # drawn row by row
    # the page's own file: where it is
    with hr.Doc(table()) as doc:
        placed = hr.place_all(doc, [("area", {"pdf_position": _position(1, column, area=True)})])
    assert placed["kinds"] == {"area": "kept"} and placed["ops"] == {}
    # the same table drawn elsewhere: the area moves by exactly as much, as wide as before
    items = [("area", {"pdf_position": {**_position(1, column, area=True), "doc_id": "v1"}})]
    with hr.Doc(table()) as old_doc, hr.Doc(table(20, 50)) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"area": "moved"}
    assert placed["ops"]["area"]["pdf_position"]["boundingRect"] == pytest.approx(
        {"x1": 86, "y1": 138, "x2": 170, "y2": 184}, abs=0.02)


# --- finding a passage ----------------------------------------------------------------

def test_an_exact_passage_is_found_on_whatever_page_it_moved_to():
    pages = _pages("the first page says little", "here the atoms are cooled in a trap and held", "the end")
    assert hr.find_passage(pages, "atoms are cooled in a trap", hint_page=1) == hr.Match(2, 9, 35, 1.0)
    assert hr.find_passage(pages, "nowhere to be found", hint_page=1) is None
    assert hr.find_passage(pages, "th", hint_page=1) is None  # too short to mean anything


def test_several_places_are_told_apart_by_context_then_by_page_then_by_place():
    pages = _pages("see fig. 3 for the spectrum. the rest.", "", "as fig. 3 shows, the lines split.\nmore text.\nand fig. 3 again")
    # the context from the old file picks the right occurrence
    assert hr.find_passage(pages, "fig. 3", hint_page=2, context=("as ", " shows, the lines"))[:2] == (3, 3)
    # without context, the nearest page
    assert hr.find_passage(pages, "fig. 3", hint_page=1).page == 1
    # and on that page the place nearest the old rectangles, by the distance near measures to each copy's glyphs

    def near(y):
        return lambda page_no, s, e: abs(hr.rects_for_span(pages[page_no], s, e)[0][1] - y)

    first, last = pages[3].text.index("fig. 3"), pages[3].text.index("fig. 3 again")
    assert hr.find_passage(pages, "fig. 3", hint_page=3, near=near(698))[:2] == (3, first)
    assert hr.find_passage(pages, "fig. 3", hint_page=3, near=near(674))[:2] == (3, last)


def test_the_rough_search_finds_a_passage_with_changed_words_and_says_how_alike_it_reads():
    old = "the cavity is driven at a frequency slightly below the atomic resonance so that the atoms are cooled"
    pages = _pages("preamble. the cavity is pumped at a frequency slightly below the atomic resonance so that atoms are cooled. end.")
    found = hr.find_passage(pages, old)
    assert pages[1].text[found.start:found.end] == old.replace("driven", "pumped").replace("that the atoms", "that atoms")
    assert found.confidence == hr._similar(old, pages[1].text[found.start:found.end]) and 0.9 < found.confidence < 1
    # no word is frozen: a changed number, a renumbered citation, a symbol or a negation dropped, punctuation
    # around a number, each read as how alike the texts are
    for old_text, new_text in (("the sample was held at 4 K for an hour before the run", "the sample was held at 5 K for an hour before the run"),
                               ("as reported before [25], however, and confirmed by later work",
                                "as reported before [27], however, and confirmed by later work"),
                               ("as shown in Fig. 3, the gate voltage tunes the dot", "as shown in Fig. 3. The gate voltage tunes the dot"),
                               ("the energy follows from Eq. 4 for small fields", "the energy follows from Eq. (4) for small fields"),
                               ("the derivative x' of the coordinate is small", "the derivative x of the coordinate is small"),
                               ("the atoms are not cooled by the probe beam", "the atoms are cooled by the probe beam")):
        page = _pages(new_text)
        found = hr.find_passage(page, hr.normalize_quote(old_text))
        assert found == hr.Match(1, 0, len(page[1].text), found.confidence), old_text
        assert hr.ROUGH_APPLY <= found.confidence < 1, old_text
    # a number changed at the passage's edge is read as changed, not dropped: the place found covers it
    found = hr.find_passage(_pages("so 5 k was the temperature of the sample during the whole run"),
                            "4 k was the temperature of the sample during the whole run")
    assert found.start == len("so ") and found.confidence > 0.95
    # too unlike to be the passage
    assert hr.find_passage(_pages("we estimated the width of the ground state with a continuous probe"),
                           "we measured the lifetime of the excited state with a pulsed laser") is None
    # from twelve characters on, whatever the number of words
    assert hr.find_passage(_pages("in the atom's trap"), "the atoms trap") == hr.Match(1, 3, 18, hr._similar("the atoms trap", "the atom's trap"))


def test_a_changed_passage_under_twelve_characters_is_lost_however_alike():
    assert hr._similar("4 k cooling", "5 k cooling") > 0.9
    assert hr.find_passage(_pages("the 5 k cooling stage"), "4 k cooling") is None
    assert hr.find_passage(_pages("the 4 k cooling stage"), "4 k cooling") == hr.Match(1, 4, 15, 1.0)
    old = _pdf([[(72, 100, "Then the 4 K cooling stage is switched on")]])
    new = _pdf([[(72, 300, "Then the 5 K cooling stage is switched on")]])
    pos = {**_position(1, _part_rect(72, 100, "Then the 4 K cooling stage is switched on", "4 K cooling")), "doc_id": "v1"}
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": "4 K cooling"})], read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"hl": "lost"}


def _place_one(old_lines, new_lines, rect, quote, limits=None, **item):
    """place_all over one highlight measured on a file of ``old_lines``
    (page 1, the file "v1"), its PDF now one of ``new_lines`` (pages of
    lines), by the thresholds ``limits`` (``min_match``, ``exact_chars``)."""
    old = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(old_lines)]])
    new = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(lines)] for lines in new_lines])
    props = {"pdf_position": {**_position(1, rect), "doc_id": "v1", **item}, "quote": quote}
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        return hr.place_all(new_doc, [("hl", props)], read_from={"v1": old_doc}.get, **(limits or {})), new


def test_a_renumbered_citation_is_placed_dashed_with_how_alike_it_reads():
    from gamma.highlights import from_scaled

    line = "In all but a few cases [25], however, such attention mechanisms are used"
    renumbered = line.replace("[25]", "[27]")
    placed, new = _place_one(["A first line", line], [["A new first page"], ["Front", "matter", renumbered]],
                             _line_rect(72, 114, line), line)
    assert _counts(placed) == {"highlights": 1, "moved": 0, "kept": 0, "lost": 0, "approx": 1}
    op = placed["ops"]["hl"]
    pos = op["pdf_position"]
    # with where it was before, on the file it was read from, its quote as it was, for the person to compare
    assert pos == {**_measured(new, 2, renumbered), "approx": True, "match": pos["match"],
                   "prev": _prev(_position(1, _line_rect(72, 114, line)), "v1", line)}
    assert 0.9 <= pos["match"] < 1 and pos["match"] == round(pos["match"], 2)
    assert op["quote"] == renumbered  # what it covers now
    # the stored shape keeps it: every restore and import passes a position through from_scaled
    assert from_scaled(pos) == pos


def test_a_sentence_with_a_few_words_changed_is_placed_dashed():
    line = "The spectrum shows a doublet at the resonance frequency when the second beam is on"
    edited = line.replace("doublet at", "triplet near").replace("when", "once")
    placed, new = _place_one([line], [["Front matter", edited]], _line_rect(72, 100, line), line)
    assert placed["kinds"] == {"hl": "approx"}
    pos = placed["ops"]["hl"]["pdf_position"]
    assert pos == {**_measured(new, 1, edited), "approx": True, "match": round(hr._similar(
        hr.normalize_quote(line), hr.normalize_quote(edited)), 2), "prev": _prev(_position(1, _line_rect(72, 100, line)), "v1", line)}
    assert placed["ops"]["hl"]["quote"] == edited


def test_a_changed_number_is_placed_dashed_and_covered():
    line = "4 K was the temperature of the sample during the whole run"
    placed, new = _place_one([line], [["So that " + line.replace("4 K", "5 K")]], _line_rect(72, 100, line), line)
    assert placed["kinds"] == {"hl": "approx"}
    op = placed["ops"]["hl"]
    assert op["quote"] == line.replace("4 K", "5 K") and op["pdf_position"]["approx"] is True
    assert op["pdf_position"]["rects"][0]["x1"] == pytest.approx(72 + _width("So that "), abs=0.6)


def test_a_stray_symbol_inside_the_old_rectangles_still_lets_the_passage_be_found():
    line = "The ions are cooled in a deep trap and then imaged on a camera"
    # a formula's large plus sign drawn just below the line, its box reaching into the highlight's rectangle
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((72, 100), line, fontname="helv", fontsize=SIZE)
    page.insert_text((72 + _width(line) / 2, 104), "+", fontname="helv", fontsize=30)
    old = doc.tobytes()
    pos = {**_position(1, _line_rect(72, 100, line)), "doc_id": "v1"}
    with hr.Doc(old) as old_doc:
        assert hr._old_passage(old_doc, pos)[0] == hr.normalize_quote(line) + " +"
        # the stored quote has no such symbol: what the rectangles read runs past it, and its own copy is the passage
        assert hr._old_passage(old_doc, pos, hr.normalize_quote(line))[0] == hr.normalize_quote(line)
    new = _pdf([[(72, 100, "Front matter"), (72, 300, line)]])

    def place(quote):
        with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
            return hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": quote})], read_from={"v1": old_doc}.get)

    # found exactly, solid
    placed = place(line)
    assert placed["kinds"] == {"hl": "moved"} and placed["ops"]["hl"] == {"pdf_position": _measured(new, 1, line)}
    # a highlight without a quote (imported without one) has only its rectangles: the symbol read with the passage,
    # found roughly, dashed, its text taken from the new file
    placed = place("")
    assert placed["kinds"] == {"hl": "approx"}
    assert placed["ops"]["hl"] == {"pdf_position": {
        **_measured(new, 1, line), "approx": True, "prev": _prev(pos, "v1", ""),
        "match": round(hr._similar(hr.normalize_quote(line) + " +", hr.normalize_quote(line)), 2)}, "quote": line}


STALE_PREV = {"doc_id": "v0", "pageNumber": 1, "rects": [[0.1, 0.1, 0.5, 0.12]], "quote": "an older wording"}


def test_an_exact_placement_of_a_highlight_not_pending_is_solid_and_drops_a_stale_match_and_prev():
    rect = _line_rect(72, 100, LINE_A)
    stale = {"match": 0.86, "prev": STALE_PREV}  # without approx: what only a pending highlight carries
    # found exactly elsewhere: moved, solid
    placed, new = _place_one([LINE_A], [["Front matter", LINE_A]], rect, LINE_A, **stale)
    assert placed["kinds"] == {"hl": "moved"} and placed["ops"]["hl"] == {"pdf_position": _measured(new, 1, LINE_A)}
    # found exactly where it was: kept, written once to drop them (and the doc_id)
    placed, _new = _place_one([LINE_A], [[LINE_A]], rect, LINE_A, **stale)
    assert placed["kinds"] == {"hl": "kept"} and placed["ops"]["hl"] == {"pdf_position": _position(1, rect)}
    # read from the page's own file over its quote, without a search: the same
    pdf = _pdf([[(72, 100, LINE_A)]])
    with hr.Doc(pdf) as doc:
        placed = hr.place_all(doc, [("hl", {"pdf_position": {**_position(1, rect), **stale}, "quote": LINE_A})])
    assert placed["kinds"] == {"hl": "kept"} and placed["ops"]["hl"] == {"pdf_position": _position(1, rect)}
    # and a lost one carries none either
    placed, _new = _place_one([LINE_A], [["Nothing of the old text"]], rect, LINE_A, **stale)
    assert placed["kinds"] == {"hl": "lost"}
    assert placed["ops"]["hl"] == {"pdf_position": {**_position(1, rect), "doc_id": "v1", "lost": True}}


CITED = "Similar schemes were demonstrated with ions [{n}] and with superconducting circuits"
CITE_A = "Trapped atoms in optical tweezers form arrays of qubits"
CITE_C = "Gate fidelities above ninety nine percent were reported"
ADDED = "A paragraph added in this revision of the paper"
# v1's sentence, v2's rough match of it, and a v3 sentence that reads like v2's but too unlike v1's
V3_LOOKS_LIKE_V2 = ["The spectrum shows a narrow doublet at the resonance frequency when the second probe beam is switched on",
        "The spectra show a narrow doublet near the resonance frequency once the second probe beam is switched on",
        "The spectra show a broad doublet near the resonance line once the second pump beam is turned on"]
# v1's sentence, v2's rough match of it, and a v3 sentence that reads like v1's but too unlike v2's
V3_LOOKS_LIKE_V1 = ["The spectrum shows a narrow doublet at the resonance frequency when the second probe beam is switched on",
         "The spectra show a broad doublet near the resonance frequency when the second probe beam is switched on",
         "The spectrum shows a narrow doublet at the resonance frequency once the third pump beam is turned on"]


def _cited(n, lower=False, tag=""):
    """A page citing ``[n]`` on its second line, or on its third under an added one (``lower``)."""
    lines = [(72, 100, CITE_A)] + ([(72, 114, ADDED), (72, 128, CITED.format(n=n)), (72, 142, CITE_C)] if lower
                                   else [(72, 114, CITED.format(n=n)), (72, 128, CITE_C)])
    return _pdf([lines + ([(72, 760, tag)] if tag else [])])


def _one(text):
    return _pdf([[(24, 100, "Header line of the page"), (24, 114, text)]])


def _applied(items, placed):  # the run's write
    return [(bid, {**props, **placed["ops"].get(bid, {})}) for bid, props in items]


def _stamped(items, doc_id):  # what a replacement writes before its run
    return [(bid, {**props, "pdf_position": {**{k: v for k, v in props["pdf_position"].items() if k != "closest"},
                                             "doc_id": props["pdf_position"].get("doc_id") or doc_id}})
            for bid, props in items]


def _twice(v1, v2, v3, items, *, stored=("v1", "v2"), v3_id="v3", **limits):
    """``items``, measured on ``v1``, placed in ``v2`` (the file "v2") and
    then, stamped as v2's, in ``v3`` (the file ``v3_id``) with only the
    files ``stored`` to read from; and the run over v3 again:
    ``(items after v2, the v3 run, items after it, the run again)``."""
    with hr.Doc(v1) as d1, hr.Doc(v2) as d2, hr.Doc(v3) as d3:
        first = hr.place_all(d2, items, read_from={"v1": d1}.get, new_doc="v2")
        pending = _applied(items, first)
        files = {k: d for k, d in (("v1", d1), ("v2", d2)) if k in stored}.get
        third = hr.place_all(d3, _stamped(pending, "v2"), read_from=files, new_doc=v3_id, **limits)
        after = _applied(_stamped(pending, "v2"), third)
        again = hr.place_all(d3, after, read_from=files, new_doc=v3_id, **limits)
    return pending, third, after, again


def _cite_item():
    q = CITED.format(n=3)
    return [("hl", {"pdf_position": {**_position(1, _line_rect(72, 114, q)), "doc_id": "v1"}, "quote": q})]


def _one_item(text):
    return [("hl", {"pdf_position": {**_position(1, _line_rect(24, 114, text)), "doc_id": "v1"}, "quote": text})]


def _at_reference(pending_pos, **flags):
    """Where a pending highlight goes back to: its prev's place on the 612 × 792 page, naming prev's file."""
    return {**hr.reference_place(pending_pos, pending_pos["prev"], (W, H)), **flags}


def test_a_pending_highlight_is_matched_from_the_file_it_was_confirmed_on_not_from_its_rough_place():
    v1, v2 = _cited(3), _cited(4)  # the citation renumbered: dashed on v2, pending
    v1_line = CITED.format(n=3)
    pending, third, after, again = _twice(v1, v2, _cited(5, lower=True), _cite_item())
    first = pending[0][1]["pdf_position"]
    assert first["approx"] is True and first["prev"] == _prev(_position(1, _line_rect(72, 114, v1_line)), "v1", v1_line)
    # renumbered again, a line lower: dashed there, matched against v1's words, its prev still v1's place
    assert third["kinds"] == {"hl": "approx"}
    pos = third["ops"]["hl"]["pdf_position"]
    assert pos == {**_measured(_cited(5, lower=True), 1, CITED.format(n=5)), "approx": True, "prev": first["prev"],
                   "match": round(hr._similar(hr.normalize_quote(v1_line), hr.normalize_quote(CITED.format(n=5))), 2)}
    assert third["ops"]["hl"]["quote"] == CITED.format(n=5)
    assert again["ops"] == {}  # the same file again: nothing to write
    # v1's words back, where v2 had its guess: solid, kept there, its quote v1's again
    v3 = _cited(3, tag="v3")
    _pending, third, after, again = _twice(v1, v2, v3, _cite_item())
    assert third["kinds"] == {"hl": "kept"}
    assert third["ops"]["hl"] == {"pdf_position": {k: v for k, v in first.items() if k not in ("approx", "match", "prev")},
                                  "quote": v1_line}
    assert again["kinds"] == {"hl": "kept"} and again["ops"] == {}
    # ...and a line lower: solid, moved there
    v3 = _cited(3, lower=True)
    _pending, third, after, again = _twice(v1, v2, v3, _cite_item())
    assert third["kinds"] == {"hl": "moved"}
    assert third["ops"]["hl"] == {"pdf_position": _measured(v3, 1, v1_line), "quote": v1_line}
    assert again["ops"] == {}
    # the passage gone: hollow at v1's place, naming v1, its quote v1's, the pending flags and prev gone with it
    _pending, third, after, again = _twice(v1, v2, _pdf([[(72, 100, CITE_A), (72, 128, CITE_C)]]), _cite_item())
    assert third["kinds"] == {"hl": "lost"}
    assert third["ops"]["hl"] == {"pdf_position": _at_reference(first, lost=True), "quote": v1_line}
    assert third["ops"]["hl"]["pdf_position"]["rects"][0] == pytest.approx(_line_rect(72, 114, v1_line), abs=0.1)
    assert third["ops"]["hl"]["pdf_position"]["doc_id"] == "v1"
    assert again["kinds"] == {"hl": "lost"} and again["ops"] == {}  # read from v1 now, as any lost highlight is
    # v3 is v1's own file again: where v1 had it, solid, without a search
    _pending, third, after, again = _twice(v1, v2, v1, _cite_item(), v3_id="v1")
    assert third["kinds"] == {"hl": "kept"} and third["ops"]["hl"]["quote"] == v1_line
    assert not {"approx", "match", "prev", "doc_id", "lost"} & set(third["ops"]["hl"]["pdf_position"])
    assert again["ops"] == {}


def test_a_pending_highlight_is_not_put_on_text_that_reads_only_like_its_rough_place():
    # v1's sentence is gone from v3, which has one like v2's guess: hollow at v1's place, not dashed on the look-alike
    pending, third, after, again = _twice(_one(V3_LOOKS_LIKE_V2[0]), _one(V3_LOOKS_LIKE_V2[1]), _one(V3_LOOKS_LIKE_V2[2]), _one_item(V3_LOOKS_LIKE_V2[0]))
    first = pending[0][1]["pdf_position"]
    assert first["approx"] is True and hr._similar(hr.normalize_quote(V3_LOOKS_LIKE_V2[1]), hr.normalize_quote(V3_LOOKS_LIKE_V2[2])) >= hr.ROUGH_APPLY
    assert third["kinds"] == {"hl": "lost"}
    op = third["ops"]["hl"]
    assert op["quote"] == V3_LOOKS_LIKE_V2[0]
    assert {k: v for k, v in op["pdf_position"].items() if k != "closest"} == _at_reference(first, lost=True)
    assert again["ops"] == {}
    # v1's text changed further and v2's guess drifted from it: dashed on the sentence that reads like v1's
    pending, third, after, again = _twice(_one(V3_LOOKS_LIKE_V1[0]), _one(V3_LOOKS_LIKE_V1[1]), _one(V3_LOOKS_LIKE_V1[2]), _one_item(V3_LOOKS_LIKE_V1[0]))
    first = pending[0][1]["pdf_position"]
    assert hr._similar(hr.normalize_quote(V3_LOOKS_LIKE_V1[1]), hr.normalize_quote(V3_LOOKS_LIKE_V1[2])) < hr.ROUGH_APPLY
    assert third["kinds"] == {"hl": "approx"}
    pos = third["ops"]["hl"]["pdf_position"]
    assert pos == {**_measured(_one(V3_LOOKS_LIKE_V1[2]), 1, V3_LOOKS_LIKE_V1[2]), "approx": True, "prev": first["prev"],
                   "match": round(hr._similar(hr.normalize_quote(V3_LOOKS_LIKE_V1[0]), hr.normalize_quote(V3_LOOKS_LIKE_V1[2])), 2)}
    assert third["ops"]["hl"]["quote"] == V3_LOOKS_LIKE_V1[2]
    assert again["ops"] == {}
    # a stricter threshold on the same file: hollow at v1's place, the rough place offered as its closest match
    with hr.Doc(_one(V3_LOOKS_LIKE_V1[0])) as d1, hr.Doc(_one(V3_LOOKS_LIKE_V1[2])) as d3:
        strict = hr.place_all(d3, after, read_from={"v1": d1}.get, new_doc="v3", min_match=0.95)
    lost = strict["ops"]["hl"]["pdf_position"]
    assert strict["kinds"] == {"hl": "lost"} and lost["closest"]["match"] == pos["match"]
    assert {k: v for k, v in lost.items() if k != "closest"} == _at_reference(first, lost=True)


def test_a_pending_highlight_whose_first_file_is_gone_is_matched_by_its_prev_s_quote():
    v1_line = CITED.format(n=3)
    # renumbered again: found by v1's quote, dashed, near v1's place
    pending, third, after, again = _twice(_cited(3), _cited(4), _cited(5, lower=True), _cite_item(), stored=("v2",))
    first = pending[0][1]["pdf_position"]
    assert third["kinds"] == {"hl": "approx"}
    pos = third["ops"]["hl"]["pdf_position"]
    assert pos["prev"] == first["prev"] and pos["match"] == round(hr._similar(
        hr.normalize_quote(v1_line), hr.normalize_quote(CITED.format(n=5))), 2)
    assert again["ops"] == {}
    # v1's sentence gone: hollow at v1's place (prev's fractions in the frame the position has), naming v1
    pending, third, after, again = _twice(_one(V3_LOOKS_LIKE_V2[0]), _one(V3_LOOKS_LIKE_V2[1]), _one(V3_LOOKS_LIKE_V2[2]), _one_item(V3_LOOKS_LIKE_V2[0]),
                                          stored=("v2",))
    first = pending[0][1]["pdf_position"]
    assert third["kinds"] == {"hl": "lost"} and third["ops"]["hl"]["quote"] == V3_LOOKS_LIKE_V2[0]
    assert {k: v for k, v in third["ops"]["hl"]["pdf_position"].items() if k != "closest"} == {
        **hr.reference_place(first, first["prev"]), "lost": True}
    assert again["ops"] == {}


def test_a_long_pending_highlight_left_hollow_carries_its_first_file_s_words_and_gets_them_back_solid():
    # a paragraph longer than a prev's quote keeps (PREV_MAX_QUOTE): its prev's quote is only its start
    words = "alpha beta gamma delta epsilon zeta theta iota kappa lambda sigma omega".split()
    lines = [" ".join(f"{words[(i + j) % len(words)]}{(7 * i + 3 * j) % 100}" for j in range(9)) for i in range(24)]
    quote = " ".join(lines)
    assert len(quote) > PREV_MAX_QUOTE

    def version(rows, tag):
        return _pdf([[(72, 60, "Header line of the page")] + [(72, 80 + 14 * i, t) for i, t in enumerate(rows)]
                     + [(72, 700, tag)]])

    reworded = list(lines)
    reworded[3] = "CHANGED " + reworded[3].split(" ", 1)[1]
    reworded[10] = "REWORDED " + reworded[10]
    v1, v2, gone = version(lines, "v1"), version(reworded, "v2"), version([], "nothing of the old text is here")
    items = [("hl", {"pdf_position": {**_measured(v1, 1, *lines), "doc_id": "v1"}, "quote": quote})]
    pending, third, after, again = _twice(v1, v2, gone, items)
    first = pending[0][1]
    assert first["pdf_position"]["approx"] is True and "CHANGED" in first["quote"]
    assert len(first["pdf_position"]["prev"]["quote"]) == PREV_MAX_QUOTE
    # v3 has none of it: hollow at v1's place, its words v1's as v1 spells them, not v2's guess
    assert third["kinds"] == {"hl": "lost"}
    op = third["ops"]["hl"]
    assert {k: v for k, v in op["pdf_position"].items() if k != "closest"} == _at_reference(first["pdf_position"], lost=True)
    assert op["quote"] == quote
    assert again["ops"] == {}
    # v1's file gone: the clipped start is not all of its words, so it keeps the quote it had
    _p, gone_third, _a, gone_again = _twice(v1, v2, gone, items, stored=("v2",))
    assert gone_third["kinds"] == {"hl": "lost"} and "quote" not in gone_third["ops"]["hl"] and gone_again["ops"] == {}
    # v4 prints v1's paragraph again: solid, its quote v1's, also from a hollow one that still had v2's guess
    v4 = version(lines, "v4")
    for stored_quote in (quote, first["quote"]):
        lost = [("hl", {**dict(after)["hl"], "quote": stored_quote})]
        with hr.Doc(v1) as d1, hr.Doc(v4) as d4:
            fourth = hr.place_all(d4, _stamped(lost, "v3"), read_from={"v1": d1}.get, new_doc="v4")
        assert fourth["kinds"] == {"hl": "moved"}, stored_quote
        placed = {**lost[0][1], **fourth["ops"]["hl"]}
        assert placed["quote"] == quote
        assert not {"approx", "match", "prev", "doc_id", "lost", "closest"} & set(placed["pdf_position"])


def test_a_pending_highlight_left_lost_by_an_earlier_run_is_matched_from_its_prev():
    # what a run before the reference rule wrote: lost where its rough place was, on v2, still pending with v1's prev
    v1_line = CITED.format(n=3)
    rough = _measured(_cited(4), 1, CITED.format(n=4))
    prev = _prev(_position(1, _line_rect(72, 114, v1_line)), "v1", v1_line)
    item = [("hl", {"pdf_position": {**rough, "doc_id": "v2", "approx": True, "match": 0.99, "lost": True, "prev": prev},
                    "quote": CITED.format(n=4)})]
    with hr.Doc(_cited(3)) as d1, hr.Doc(_cited(3, lower=True)) as d3:
        placed = hr.place_all(d3, item, read_from={"v1": d1}.get, new_doc="v3")
    # v1's words in v3: solid
    assert placed["kinds"] == {"hl": "moved"}
    assert placed["ops"]["hl"] == {"pdf_position": _measured(_cited(3, lower=True), 1, v1_line), "quote": v1_line}


def test_a_highlight_pending_without_a_prev_is_matched_from_its_own_place():
    # pending from before prev was kept: its own place is all it has to go by
    rect = _line_rect(72, 100, LINE_A)
    placed, new = _place_one([LINE_A], [["Front matter", LINE_A]], rect, LINE_A, approx=True, match=0.86)
    assert placed["kinds"] == {"hl": "moved"} and placed["ops"]["hl"] == {"pdf_position": _measured(new, 1, LINE_A)}
    edited = LINE_A.replace("single", "lone")
    placed, new = _place_one([LINE_A], [["Front matter", edited]], rect, LINE_A, approx=True, match=0.86)
    assert placed["kinds"] == {"hl": "approx"}
    assert placed["ops"]["hl"]["pdf_position"] == {
        **_measured(new, 1, edited), "approx": True, "prev": _prev(_position(1, rect), "v1", LINE_A),
        "match": round(hr._similar(hr.normalize_quote(LINE_A), hr.normalize_quote(edited)), 2)}


FIGURE_CAPTION = "Figure {n}: Rabi oscillations of the driven qubit at resonance"


def _figure(n, dy=0):
    return _pdf([[(72, 100, "Header line of the page"), (72, 400 + dy, FIGURE_CAPTION.format(n=n))]])


def test_a_pending_area_is_matched_from_where_it_was_confirmed():
    box = {"x1": 60, "y1": 385, "x2": 420, "y2": 410}
    area = [("area", {"pdf_position": {**_position(1, box, area=True), "doc_id": "v1"}})]
    # its caption renumbered on v2 (dashed), v1's caption back on v3, lower: solid there
    pending, third, after, again = _twice(_figure(2), _figure(3), _figure(2, 60), area)
    first = pending[0][1]["pdf_position"]
    assert first["approx"] is True and 0.9 <= first["match"] < 1 and first["prev"] == _prev(_position(1, box), "v1")
    assert third["kinds"] == {"area": "moved"}
    moved = third["ops"]["area"]["pdf_position"]
    assert not {"approx", "match", "prev", "doc_id"} & set(moved)
    assert moved["boundingRect"] == pytest.approx({"x1": 60, "y1": 445, "x2": 420, "y2": 470}, abs=0.05)
    assert again["ops"] == {}
    # renumbered again: dashed, its prev v1's
    pending, third, after, again = _twice(_figure(2), _figure(3), _figure(4, 60), area)
    assert third["kinds"] == {"area": "approx"} and third["ops"]["area"]["pdf_position"]["prev"] == first["prev"]
    assert again["ops"] == {}


def test_a_pending_area_nothing_places_goes_back_to_where_it_was_confirmed():
    def rows(*dys):
        return _pdf([[(72, 100 + dy + 14 * i, t) for i, (t, dy) in enumerate(zip((LINE_A, LINE_B, LINE_C), dys))]])

    items = [(f"hl{k}", {"pdf_position": {**_measured(rows(0, 0, 0), 1, t), "doc_id": "v1"}, "quote": t})
             for k, t in zip("ABC", (LINE_A, LINE_B, LINE_C))]
    items.append(("area", {"pdf_position": RIDER}))
    # v2: all 40 points lower, the area along with them, by estimate; v3: they go apart, nothing places it
    pending, third, after, again = _twice(rows(0, 0, 0), rows(40, 40, 40), rows(40, 300, 600), items)
    estimate = dict(pending)["area"]["pdf_position"]
    assert estimate["approx"] is True and "match" not in estimate and estimate["prev"] == _prev(RIDER, "v1")
    # back at v1's place, naming v1, still an estimate to check: v2's rectangles are no one's any more
    assert third["kinds"]["area"] == "approx"
    assert third["ops"]["area"] == {"pdf_position": _at_reference(estimate, approx=True, prev=estimate["prev"])}
    assert third["ops"]["area"]["pdf_position"]["boundingRect"] == pytest.approx(RIDER["boundingRect"], abs=0.05)
    assert again["ops"] == {}
    # v3 moves them all alike: the area rides along from where v2 put it, still an estimate with v1's prev
    pending, third, after, again = _twice(rows(0, 0, 0), rows(40, 40, 40), rows(70, 70, 70), items)
    ride = third["ops"]["area"]["pdf_position"]
    assert third["kinds"]["area"] == "approx" and "doc_id" not in ride and ride["prev"] == estimate["prev"]
    assert ride["boundingRect"] == pytest.approx({"x1": 400, "y1": 670, "x2": 500, "y2": 770}, abs=0.05)
    assert again["ops"] == {}


def test_an_estimated_area_whose_neighbours_all_go_back_to_its_reference_is_still_an_estimate():
    def rows(texts, dy, tag=None):
        return _pdf([[(72, 100 + dy + 14 * i, t) for i, t in enumerate(texts)] + ([(72, 760, tag)] if tag else [])])

    lines = (LINE_A, LINE_B, LINE_C)
    items = [(f"hl{k}", {"pdf_position": {**_measured(rows(lines, 0), 1, t), "doc_id": "v1"}, "quote": t})
             for k, t in zip("ABC", lines)]
    items.append(("area", {"pdf_position": RIDER}))
    # v2: every line reworded and 40 points lower, all dashed, the area along with them by estimate;
    # v3: v1's lines at v1's places, so its neighbours are found exactly where they were confirmed
    reworded = (LINE_A.replace("single", "lone"), LINE_B.replace("doublet", "triplet"), LINE_C.replace("first", "second"))
    pending, third, after, again = _twice(rows(lines, 0), rows(reworded, 40), rows(lines, 0, "v3"), items)
    estimate = dict(pending)["area"]["pdf_position"]
    assert estimate["approx"] is True and "match" not in estimate and estimate["prev"] == _prev(RIDER, "v1")
    assert all(dict(pending)[f"hl{k}"]["pdf_position"]["approx"] for k in "ABC")
    assert third["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "moved", "area": "approx"}  # off v2's places
    # back at v1's place, still an estimate for a person to check: its neighbours staying confirms nothing
    area = third["ops"]["area"]["pdf_position"]
    assert area["approx"] is True and "match" not in area and area["prev"] == estimate["prev"]
    assert "doc_id" not in area and area["boundingRect"] == pytest.approx(RIDER["boundingRect"], abs=0.05)
    assert again["ops"] == {}


def test_a_dashed_placement_keeps_where_the_highlight_was_even_when_that_file_is_gone():
    line = "In all but a few cases [25], however, such attention mechanisms are used"
    new = _pdf([[(72, 100, "Front matter was added in this revision")], [(72, 300, line.replace("[25]", "[27]"))]])
    gone = {**_position(1, _line_rect(72, 114, line)), "doc_id": "gone"}
    area = {**_position(1, {"x1": 300, "y1": 400, "x2": 400, "y2": 500}, area=True), "doc_id": "gone"}
    # measured on the page's file (no doc_id), its rects over nothing: placed by its quote, roughly
    here = _position(1, _line_rect(72, 600, "Front matter was added in the revision"))
    items = [("hl", {"pdf_position": gone, "quote": line}),
             ("area", {"pdf_position": area, "quote": "what the area was said to be"}),
             ("here", {"pdf_position": here, "quote": "Front matter was added in the revision"})]
    with hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, new_doc="v2")  # no read_from: the file "gone" is not stored
    assert placed["kinds"] == {"hl": "approx", "area": "approx", "here": "approx"}
    hl = placed["ops"]["hl"]["pdf_position"]
    assert hl["pageNumber"] == 2 and hl["prev"] == _prev(gone, "gone", line)
    # the area rode along with it, by estimate: its prev has no quote
    ride = placed["ops"]["area"]["pdf_position"]
    assert ride["pageNumber"] == 2 and "match" not in ride and ride["prev"] == _prev(area, "gone")
    # one measured on the page's own file names that file
    assert placed["ops"]["here"]["pdf_position"]["prev"] == _prev(here, "v2", "Front matter was added in the revision")
    # a prev is kept to the page: a rect past its edge is clamped, a position without a frame has no rects
    off = {**_position(1, {"x1": -6, "y1": 700, "x2": 650, "y2": 800})}
    assert previous_place(off, "v1")["rects"] == [[0.0, round(700 / H, 4), 1.0, 1.0]]
    assert previous_place({k: v for k, v in off.items() if k not in ("width", "height")}, "v1")["rects"] == []


def test_the_account_s_thresholds_decide_what_a_rough_match_applies():
    # a renumbered citation, 0.97 alike: applied from the default 80%, or from 95%, not when only exact copies are
    line = "In all but a few cases [25], however, such attention mechanisms are used"
    renumbered = line.replace("[25]", "[27]")
    for limits, kind in ((None, "approx"), ({"min_match": 0.95}, "approx"), ({"min_match": 1.0}, "lost")):
        placed, _new = _place_one(["A first line", line], [["Front", renumbered]], _line_rect(72, 114, line), line, limits)
        assert placed["kinds"] == {"hl": kind}, limits
    # a changed passage under twelve characters: lost, unless the account lets passages that short match roughly
    old_line, new_line = "Then the 4 K cooling stage is switched on", "Then the 5 K cooling stage is switched on"
    rect = _part_rect(72, 100, old_line, "4 K cooling")
    placed, _new = _place_one([old_line], [[new_line]], rect, "4 K cooling")
    assert placed["kinds"] == {"hl": "lost"}
    placed, _new = _place_one([old_line], [[new_line]], rect, "4 K cooling", {"exact_chars": 0})
    assert placed["kinds"] == {"hl": "approx"} and placed["ops"]["hl"]["quote"] == "5 K cooling"
    assert placed["ops"]["hl"]["pdf_position"]["match"] == round(hr._similar("4 k cooling", "5 k cooling"), 2)
    # ...which a higher minimum still turns down
    placed, _new = _place_one([old_line], [[new_line]], rect, "4 K cooling", {"exact_chars": 0, "min_match": 0.95})
    assert placed["kinds"] == {"hl": "lost"}
    # and a longer length makes a longer passage match exactly or not at all, its pieces too
    pages = _pages("the 5 k cooling stage")
    assert hr.find_passage(pages, "4 k cooling stage") == hr.Match(1, 4, 21, hr._similar("4 k cooling stage", "5 k cooling stage"))
    assert hr.find_passage(pages, "4 k cooling stage", exact_chars=20) is None
    assert hr.find_passage(pages, "4 k cooling", exact_chars=0) == hr.Match(1, 4, 15, hr._similar("4 k cooling", "5 k cooling"))
    piece = hr.Piece("4 k cooling stage", ("", ""))
    assert hr._piece_hits(pages[1], piece, 0, 5, 12) and not hr._piece_hits(pages[1], piece, 0, 5, 20)


def test_the_thresholds_are_the_account_s_own_pulled_into_range_else_the_defaults():
    assert hr.thresholds({}) == hr.thresholds(None) == hr.thresholds([90]) == DEFAULTS
    assert hr.thresholds({"relocateMinMatch": 90, "relocateExactChars": 20}) == {"min_match": 0.9, "exact_chars": 20}
    assert hr.thresholds({"relocateMinMatch": 100, "relocateExactChars": 0}) == {"min_match": 1.0, "exact_chars": 0}
    # as the web app's codec reads them: out of range pulled into it, a fraction cut to a whole number, the
    # percentage put on its step of 5
    assert hr.thresholds({"relocateMinMatch": 400, "relocateExactChars": -3}) == {"min_match": 1.0, "exact_chars": 0}
    assert hr.thresholds({"relocateMinMatch": 10, "relocateExactChars": 99}) == {"min_match": 0.5, "exact_chars": 60}
    assert hr.thresholds({"relocateMinMatch": 87.9, "relocateExactChars": 12.5}) == {"min_match": 0.85, "exact_chars": 12}
    assert [hr.thresholds({"relocateMinMatch": v})["min_match"] for v in (82, 83, 97, 98)] == [0.8, 0.85, 0.95, 1.0]
    # missing, or no number: the defaults, each on its own
    for bad in (None, "90", True, float("nan"), float("inf"), [90], {"value": 90}):
        assert hr.thresholds({"relocateMinMatch": bad, "relocateExactChars": bad}) == DEFAULTS, bad
    assert hr.thresholds({"relocateExactChars": 30}) == {"min_match": hr.ROUGH_APPLY, "exact_chars": 30}


def test_a_deleted_sentence_whose_nearest_look_alike_reads_too_unlike_is_lost():
    gone = "We then measured the lifetime of the excited state with a weak and pulsed probe laser."
    alike = "We then estimated the width of the excited state with a strong and continuous pump laser."
    assert hr._similar(hr.normalize_quote(gone), hr.normalize_quote(alike)) < hr.ROUGH_APPLY
    placed, _new = _place_one([gone], [["Front matter", alike]], _line_rect(72, 100, gone), gone)
    assert placed["kinds"] == {"hl": "lost"} and placed["ops"]["hl"]["pdf_position"]["lost"] is True
    # it was the best place found: the threshold alone turned it down
    placed, _new = _place_one([gone], [["Front matter", alike]], _line_rect(72, 100, gone), gone, {"min_match": 0.5})
    assert placed["kinds"] == {"hl": "approx"} and placed["ops"]["hl"]["quote"] == alike


GONE = "We then measured the lifetime of the excited state with a weak and pulsed probe laser."
ALIKE = "We then estimated the width of the excited state with a strong and continuous pump laser."  # 0.72 alike
GONE_RECT = _line_rect(72, 100, GONE)


def _closest(pdf_bytes, page_no, text, needle):
    """The ``closest`` a run keeps for a lost highlight of ``needle`` whose best
    place is ``text`` (its first copy) on page ``page_no`` of a file."""
    return {**_measured(pdf_bytes, page_no, text), "quote": text,
            "match": round(hr._similar(hr.normalize_quote(needle), hr.normalize_quote(text)), 2)}


def test_a_lost_highlight_keeps_the_place_most_like_it_under_the_threshold():
    assert hr.CLOSEST_MIN <= hr._similar(hr.normalize_quote(GONE), hr.normalize_quote(ALIKE)) < hr.ROUGH_APPLY
    placed, new = _place_one([GONE], [["Front matter", ALIKE]], GONE_RECT, GONE)
    assert placed["kinds"] == {"hl": "lost"}
    # hollow where it was, still read from its own file, and the best place found beside it: its place in the new
    # file, how alike it reads and its text there, for the person to look at and take
    pos = placed["ops"]["hl"]["pdf_position"]
    assert pos == {**_position(1, GONE_RECT), "doc_id": "v1", "lost": True, "closest": _closest(new, 1, ALIKE, GONE)}
    # the stored shape keeps it: every restore and import passes a position through from_scaled
    assert from_scaled(pos) == pos


def test_a_closest_place_reads_at_least_closest_min_alike_and_moves_with_the_account_s_threshold():
    # 72% alike: the closest place from the default 80% and from 95%, applied from 70%, dashed
    for limits, kind in ((None, "lost"), ({"min_match": 0.95}, "lost"), ({"min_match": 0.7}, "approx")):
        placed, _new = _place_one([GONE], [["Front matter", ALIKE]], GONE_RECT, GONE, limits)
        pos = placed["ops"]["hl"]["pdf_position"]
        assert placed["kinds"] == {"hl": kind} and ("closest" in pos) == (kind == "lost"), limits
    # the best place, but under half alike: none
    line = "We saw it at the lab on a sunny day."
    padded = ["We saw antidisestablishmentarianism it at floccinaucinihilipilification the lab",
              "pneumonoultramicroscopic on a supercalifragilisticexpialidocious sunny day."]
    pages = _pages("\n".join(padded))
    found = hr.find_passage(pages, hr.normalize_quote(line), closest=0.0)
    assert found.below and found.confidence < hr.CLOSEST_MIN
    assert hr.find_passage(pages, hr.normalize_quote(line), closest=hr.CLOSEST_MIN) is None
    placed, _new = _place_one([line], [padded], _line_rect(72, 100, line), line)
    assert placed["kinds"] == {"hl": "lost"} and "closest" not in placed["ops"]["hl"]["pdf_position"]
    # a passage under twelve characters is matched exactly or not at all: none either, however alike
    old_line, new_line = "Then the 4 K cooling stage is switched on", "Then the 5 K cooling stage is switched on"
    placed, _new = _place_one([old_line], [[new_line]], _part_rect(72, 100, old_line, "4 K cooling"), "4 K cooling")
    assert placed["kinds"] == {"hl": "lost"} and "closest" not in placed["ops"]["hl"]["pdf_position"]
    assert hr.find_passage(_pages(new_line.lower()), "4 k cooling", closest=0.0) is None


def test_a_later_run_replaces_or_drops_a_lost_highlight_s_closest_place():
    other = "We first measured the width of the excited level with a strong and continuous probe beam."
    v1, v2 = _pdf([[(72, 100, GONE)]]), _pdf([[(72, 100, "Front matter"), (72, 114, ALIKE)]])
    v3 = _pdf([[(72, 300, other)]])                          # alike in another way, elsewhere
    v4 = _pdf([[(72, 100, "Nothing of the old text")]])
    v5 = _pdf([[(72, 100, "A new front page")], [(72, 500, GONE)]])  # the sentence itself again
    lost = {**_position(1, GONE_RECT), "doc_id": "v1", "lost": True}

    def applied(item, placed):  # the run's write
        return item[0], {**item[1], **placed["ops"].get(item[0], {})}

    item = ("hl", {"pdf_position": {**_position(1, GONE_RECT), "doc_id": "v1"}, "quote": GONE})
    with hr.Doc(v1) as d1, hr.Doc(v2) as d2, hr.Doc(v3) as d3, hr.Doc(v4) as d4, hr.Doc(v5) as d5:
        files = {"v1": d1}.get
        item = applied(item, hr.place_all(d2, [item], read_from=files, new_doc="v2"))
        assert item[1]["pdf_position"] == {**lost, "closest": _closest(v2, 1, ALIKE, GONE)}
        # the same file again: the same place, nothing written
        again = hr.place_all(d2, [item], read_from=files, new_doc="v2")
        assert again["kinds"] == {"hl": "lost"} and again["ops"] == {}
        # a later version reads most like it elsewhere: that run's place replaces the one before
        later = hr.place_all(d3, [item], read_from=files, new_doc="v3")
        assert later["kinds"] == {"hl": "lost"}
        assert later["ops"]["hl"] == {"pdf_position": {**lost, "closest": _closest(
            v3, 1, "We first measured the width of the excited level with a strong and continuous probe", GONE)}}
        # nothing like it any more: the closest place goes, the highlight still hollow where it was
        assert hr.place_all(d4, [item], read_from=files, new_doc="v4")["ops"]["hl"] == {"pdf_position": lost}
        # its text found again: placed, solid, with no closest place
        found = hr.place_all(d5, [item], read_from=files, new_doc="v5")
        assert found["kinds"] == {"hl": "moved"} and found["ops"]["hl"] == {"pdf_position": _measured(v5, 2, GONE)}
        # the file it was measured on is the page's PDF again: found where it is, without a search, its lost mark,
        # file and closest place dropped
        back = hr.place_all(d1, [item], read_from=files, new_doc="v1")
        assert back["kinds"] == {"hl": "moved"} and back["ops"]["hl"] == {"pdf_position": _position(1, GONE_RECT)}


def test_a_dropped_first_word_does_not_pull_in_the_word_before():
    needle = "moreover the cavity is driven below the atomic resonance"
    pages = _pages("the atoms are held in a trap. the cavity is driven below the atomic resonance so that they cool")
    found = hr.find_passage(pages, needle)
    assert found.confidence < 1 and pages[1].text[found.start:found.end] == needle.removeprefix("moreover ")
    # on a page where that passage starts a line, it covers that line alone
    line1, line2 = "The atoms are held in a trap.", "The cavity is driven below the atomic resonance so that"
    new = _pdf([[(72, 100, line1), (72, 114, line2)]])
    items = [("hl", {"pdf_position": {**_position(1, _line_rect(72, 300, "x")), "doc_id": "gone"},
                     "quote": "Moreover the cavity is driven below the atomic resonance"})]
    with hr.Doc(new) as new_doc:
        op = hr.place_all(new_doc, items)["ops"]["hl"]
    assert len(op["pdf_position"]["rects"]) == 1 and 99 < op["pdf_position"]["rects"][0]["y1"] < 119
    assert op["quote"] == "The cavity is driven below the atomic resonance" and op["pdf_position"]["approx"] is True


def test_a_word_added_near_a_passage_s_edge_keeps_its_last_and_first_words_covered():
    # a word inserted before the last one: the whole sentence is covered and respelled, not cut before the insertion
    for old, new in (("The atoms were cooled to 4 K in the cryostat.", "The atoms were cooled to 4 K in a new cryostat."),
                     ("Spin echoes were measured by Hahn in this setup.", "Spin echoes were measured by Hahn in the improved setup."),
                     # cut short, it would read under 80% alike, and be lost
                     ("The gas was held in the trap.", "The gas was held in a deep trap."),
                     # and after the first word
                     ("In the cryostat the atoms were cooled to 4 K.", "In a new cryostat the atoms were cooled to 4 K.")):
        placed, _new = _place_one(["Front matter", old], [["Front matter", new]], _line_rect(72, 114, old), old)
        assert placed["kinds"] == {"hl": "approx"} and placed["ops"]["hl"]["quote"] == new, old
        assert placed["ops"]["hl"]["pdf_position"]["match"] == round(hr._similar(hr.normalize_quote(old), hr.normalize_quote(new)), 2)
    # a dropped last word still does not pull in the next sentence's first, even one that shares its letters
    old, new = "The sample was annealed for two hours overnight.", "The sample was annealed for two hours."
    old_line, new_line = old + " However the result was poor.", new + " However the result was poor."
    placed, _new = _place_one([old_line], [["Front matter", new_line]], _part_rect(72, 100, old_line, old), old)
    assert placed["kinds"] == {"hl": "approx"} and placed["ops"]["hl"]["quote"] == new
    # a dropped word costing a hair more than one still fits a bound of whole words (two words: one)
    assert hr.find_passage(_pages("the quantum wells are deep"), "quantum dots", min_match=0.5) == hr.Match(
        1, 4, 11, hr._similar("quantum dots", "quantum"))
    # reworded in place with a word added before its end, it still outranks a verbatim copy elsewhere
    q = "We demonstrate a two-qubit gate fidelity of 99.5 percent with this method."
    reworded = q.replace("99.5 percent with this", "99.7 percent using the new")
    intro = "Abstract. We report a new method for cooling trapped ions."
    outro = "The method scales to larger registers."
    conclusion = [(72, 100, "Conclusion. In summary, the cooling works well."), (72, 114, q), (72, 128, "Future work.")]
    old = _pdf([[(72, 100, intro), (72, 114, q), (72, 128, outro)], conclusion])
    new = _pdf([[(72, 100, intro), (72, 114, reworded), (72, 128, outro)], conclusion])
    items = [("hl", {"pdf_position": {**_position(1, _line_rect(72, 114, q)), "doc_id": "v1"}, "quote": q})]
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"hl": "approx"} and placed["ops"]["hl"]["quote"] == reworded
    assert placed["ops"]["hl"]["pdf_position"]["pageNumber"] == 1


def test_an_exact_copy_counts_only_on_word_edges_unless_the_selection_was_inside_a_word():
    def place(old_line, new_lines, part):
        old = _pdf([[(72, 100, old_line)]])
        items = [("hl", {"pdf_position": {**_position(1, _part_rect(72, 100, old_line, part)), "doc_id": "v1"}, "quote": part})]
        with hr.Doc(old) as old_doc, hr.Doc(_pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(new_lines)]])) as new_doc:
            return hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)

    # the ion is gone: not the end of "motion"
    assert place("In this trap the ion is held by radio frequency fields",
                 ["In this trap the atom is held and its motion is cooled"], "ion")["kinds"] == {"hl": "lost"}
    # Fig. 1 is not the start of Fig. 12, nor is it Fig. 2 (by its quote alone, no context, the same rule)
    new = _pdf([[(72, 100, "as Fig. 2 shows, and as Fig. 12 shows")]])
    with hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": {**_position(1, _line_rect(72, 100, "Fig. 1")), "doc_id": "gone"},
                                                "quote": "Fig. 1"})])
    assert placed["kinds"] == {"hl": "lost"}
    # a selection that began and ended inside a word is found inside it
    placed = place("the entanglement of two atoms", ["A new first line", "the entanglement of two atoms"], "tangle")
    rect = placed["ops"]["hl"]["pdf_position"]["rects"][0]
    assert placed["kinds"] == {"hl": "moved"} and 99 < rect["y1"] < 119
    assert rect["x1"] == pytest.approx(72 + _width("the en"), abs=0.6) and rect["x2"] == pytest.approx(72 + _width("the entangle"), abs=0.6)


def test_a_passage_reworded_in_place_beats_an_exact_copy_elsewhere():
    page1 = ["We load the cloud from a slow atomic beam, and in the first",
             "stage the atoms are cooled in the trap for ten milliseconds while",
             "the magnetic field is ramped down to zero over the same time."]
    page2 = ["In conclusion, we have shown with this simple setup that",
             "the atoms are cooled in the trap and then imaged on a camera."]
    passage = "the atoms are cooled in the trap"
    reworded = [page1[0], page1[1].replace("cooled in", "cooled inside"), page1[2]]

    def lines(texts):
        return [(72, 100 + 14 * i, t) for i, t in enumerate(texts)]

    old, new = _pdf([lines(page1), lines(page2)]), _pdf([lines(reworded), lines(page2)])
    in_place = _position(1, _part_rect(72, 114, page1[1], passage))
    items = [("hl", {"pdf_position": {**in_place, "doc_id": "v1"}, "quote": passage}),
             ("copy", {"pdf_position": {**_position(2, _part_rect(72, 114, page2[1], passage)), "doc_id": "v1"}, "quote": passage}),
             # by its quote alone (its file gone) there is no surroundings to go by: the exact copy wins
             ("bare", {"pdf_position": {**in_place, "doc_id": "gone"}, "quote": passage})]
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    # reworded in its own surroundings, it outranks the exact copy in others (confidence + CONTEXT_WEIGHT × fit),
    # and is dashed
    assert placed["kinds"] == {"hl": "approx", "copy": "kept", "bare": "moved"}
    hl = placed["ops"]["hl"]
    assert hl["pdf_position"]["pageNumber"] == 1 and hl["quote"] == "the atoms are cooled inside the trap"
    assert hl["pdf_position"]["match"] == round(hr._similar(passage, "the atoms are cooled inside the trap"), 2)
    bare = placed["ops"]["bare"]["pdf_position"]
    assert bare["pageNumber"] == 2 and "quote" not in placed["ops"]["bare"] and "approx" not in bare


def test_without_context_the_nearest_place_is_measured_on_the_page_not_in_its_text():
    phrase = "optical lattice clock"
    left = [f"left column line {w} of running text" for w in ("one", "two", "three", "four", "five", "six", "seven", "eight")]
    right_top = "The optical lattice clock is new"
    right = [right_top] + [f"right column line {w} of more text" for w in ("two", "three", "four", "five", "six", "seven")]
    pdf = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(left)] + [(72, 700, "and the optical lattice clock ends it")]
                + [(330, 100 + 14 * i, t) for i, t in enumerate(right)]])
    pos = _position(1, _part_rect(330, 100, right_top, phrase))
    with hr.Doc(pdf) as doc:
        placed = hr.place_all(doc, [("hl", {"pdf_position": {**pos, "doc_id": "gone"}, "quote": phrase})])
    assert placed["kinds"] == {"hl": "kept"} and placed["ops"] == {"hl": {"pdf_position": pos}}
    # one column, a term twice a few lines apart: each highlight stays on its own copy
    lines = [f"Line {i} of a single column of text that goes on" for i in range(30)]
    lines[1], lines[5] = "an optical lattice holds the atoms", "the optical lattice is deep"
    pdf = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(lines)]])
    items = [(f"hl{i}", {"pdf_position": {**_position(1, _part_rect(72, 100 + 14 * i, lines[i], "optical lattice")),
                                          "doc_id": "gone"}, "quote": "optical lattice"}) for i in (1, 5)]
    with hr.Doc(pdf) as doc:
        placed = hr.place_all(doc, items)
    assert placed["kinds"] == {"hl1": "kept", "hl5": "kept"}


def test_a_passage_split_by_a_page_break_goes_to_the_page_with_the_larger_part():
    passage = ["The trapped ions are cooled to the motional ground state by",
               "resolved sideband cooling on the narrow quadrupole transition and",
               "then probed with a weak laser beam tuned to the carrier."]
    gone = ["This paragraph of the first version was cut from the next one",
            "together with its figure and every word that it had to say",
            "about the old experiment and its many shortcomings."]
    filler = [f"Filler sentence {i} keeps this page full of other words" for i in range(40)]
    head = (72, 50, "Phys. Rev. A 104, 033101 (2021)")

    def lines(texts, y0):
        return [(72, y0 + 14 * i, t) for i, t in enumerate(texts)]

    old = _pdf([lines(filler[:10], 100) + lines(passage, 300) + lines(gone, 400)])

    def split(first):  # the passage's first ``first`` lines end page 1, above its page number; the rest start page 2
        return _pdf([lines(filler[:30], 100) + lines(passage[:first], 728 - 14 * first) + [(300, 770, "1")],
                     [head] + lines(passage[first:], 100) + lines(filler[30:], 150)])

    def rects(texts, y0):
        return [_line_rect(72, y0 + 14 * i, t) for i, t in enumerate(texts)]

    items = [("hl", {"pdf_position": {**_position(1, rects(passage, 300)), "doc_id": "v1"}, "quote": " ".join(passage)}),
             ("gone", {"pdf_position": {**_position(1, rects(gone, 400)), "doc_id": "v1"}, "quote": " ".join(gone)})]
    for first, page_no, part in ((2, 1, passage[:2]), (1, 2, passage[1:])):
        with hr.Doc(old) as old_doc, hr.Doc(split(first)) as new_doc:
            placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
        assert placed["kinds"] == {"hl": "moved", "gone": "lost"}, first
        op = placed["ops"]["hl"]
        assert op["pdf_position"]["pageNumber"] == page_no and len(op["pdf_position"]["rects"]) == 2
        assert op["quote"] == " ".join(part)  # what it covers now


# --- rectangles ------------------------------------------------------------------------

def test_a_passage_over_two_lines_becomes_one_rect_per_line_in_the_viewer_frame():
    pdf = _pdf([[(72, 100, "A first line of prose that ends here"), (72, 114, "and a second one that goes on")]])
    with GlyphPages(pdf) as glyphs:
        page = hr.PageText(glyphs.page(1))
        frame = glyphs.frame(1)
        s = page.text.index("ends here")
        e = page.text.index("second one") + len("second one")
        boxes = hr.rects_for_span(page, s, e)
        assert len(boxes) == 2
        pos = hr._position(1, frame, boxes)
    assert (pos["width"], pos["height"]) == (W, H)
    first, second = pos["rects"]
    assert 85 < first["y1"] < first["y2"] < 105 and 99 < second["y1"] < second["y2"] < 119
    assert abs(first["x1"] - (72 + _width("A first line of prose that "))) < 1 and abs(first["x2"] - (72 + _width("A first line of prose that ends here"))) < 1
    assert abs(second["x1"] - 72) < 0.5
    assert pos["boundingRect"] == {"x1": min(first["x1"], second["x1"]), "y1": first["y1"], "x2": max(first["x2"], second["x2"]), "y2": second["y2"]}


def test_a_rotated_cropped_page_frames_its_rects_as_the_viewer_shows_them():
    pdf = _pdf([[(100, 200, "Rotated words")]], rotate=90, cropbox=(50, 60, 500, 700))
    with GlyphPages(pdf) as glyphs:
        page = hr.PageText(glyphs.page(1))
        view, rotation = glyphs.frame(1)
        assert rotation == 90 and view == pytest.approx((50, 92, 500, 732))
        pos = hr._position(1, (view, rotation), hr.rects_for_span(page, 0, len(page.text)))
    # the page shows 640 wide and 450 high (the crop box on its side), and the text lies inside it
    assert (pos["width"], pos["height"]) == (640, 450)
    r = pos["rects"][0]
    assert 0 <= r["x1"] < r["x2"] <= 640 and 0 <= r["y1"] < r["y2"] <= 450
    # a quarter turn: the line runs down the page
    assert r["y2"] - r["y1"] > r["x2"] - r["x1"]
    # and back: the stored rect covers the glyphs it came from
    quads = hr._quads(pos, (view, rotation))
    assert hr.glyphs_under(page, quads) == [gi for gi in range(page.glyph_count) if has_box(page.box(gi))]


# --- placing a page's highlights --------------------------------------------------------

LINE_A = "Quantum dots confine single electrons in a small island"
LINE_B = "The spectrum shows a doublet at the resonance frequency"
LINE_C = "This sentence is only in the first version of the paper"
CAPTION = "Figure 1: the device layout and its gates"


def _counts(placed):
    return {k: v for k, v in placed.items() if k not in ("ops", "kinds")}


def test_place_all_moves_highlights_to_their_text_and_marks_the_rest_lost():
    old = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B), (72, 128, LINE_C), ("image", (72, 300, 272, 400), RED), (72, 412, CAPTION)],
                [(72, 100, "Second page text"), ("image", (72, 200, 172, 300), BLUE)]])
    # the new version: a page of front matter before, LINE_B reworded, LINE_C gone, the pictures elsewhere
    new = _pdf([[(72, 100, "Front matter added in revision")],
                [(72, 100, LINE_A), (72, 114, LINE_B.replace("doublet", "triplet").replace("resonance frequency", "resonant frequency")),
                 (72, 128, "A new sentence that replaced the old one"), ("image", (92, 330, 292, 430), RED), (92, 442, CAPTION)],
                [(72, 100, "Second page text"), ("image", (200, 400, 300, 500), BLUE)]])

    def on_v1(page_no, rect, area=False):  # measured on the file "v1", which is no longer the page's
        return {**_position(page_no, rect, area), "doc_id": "v1"}

    items = [
        ("hlA", {"pdf_position": on_v1(1, _line_rect(72, 100, LINE_A)), "quote": LINE_A}),
        ("hlB", {"pdf_position": on_v1(1, _line_rect(72, 114, LINE_B)), "quote": LINE_B}),
        ("hlC", {"pdf_position": on_v1(1, _line_rect(72, 128, LINE_C)), "quote": LINE_C}),
        # an area over the picture and its caption: placed by the caption's text
        ("areaCap", {"pdf_position": on_v1(1, {"x1": 60, "y1": 290, "x2": 290, "y2": 420}, area=True), "quote": ""}),
        # an area over the second page's picture alone: placed by the picture
        ("areaPic", {"pdf_position": on_v1(2, {"x1": 70, "y1": 195, "x2": 175, "y2": 305}, area=True), "quote": ""}),
        # an area over nothing: rides along with its page's highlights (all went to page 2, shifted a little or not at all)
        ("areaRide", {"pdf_position": on_v1(1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}, area=True), "quote": ""}),
        # made on the new file already (no doc_id), where its text still is: nothing to write
        ("hlKept", {"pdf_position": _position(3, _line_rect(72, 100, "Second page text")), "quote": "Second page text"}),
    ]
    reports = []
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        assert (old_doc.count, new_doc.count) == (2, 3)  # a Doc takes PDF bytes as well as a path
        placed = hr.place_all(new_doc, items, progress=lambda **kw: reports.append(kw), read_from={"v1": old_doc}.get)
        assert new_doc.glyphs._pages == {}  # every page was read, and only its PageText kept
    assert _counts(placed) == {"highlights": 7, "moved": 3, "kept": 1, "lost": 1, "approx": 2}
    assert placed["kinds"] == {"hlA": "moved", "hlB": "approx", "hlC": "lost", "areaCap": "moved", "areaPic": "moved",
                               "areaRide": "approx", "hlKept": "kept"}
    ops = placed["ops"]
    assert "hlKept" not in ops
    # the exact passage: on page 2 now, at the line's place, solid; a new position names no file
    a = ops["hlA"]["pdf_position"]
    assert a["pageNumber"] == 2 and "lost" not in a and "doc_id" not in a and "quote" not in ops["hlA"]
    assert "approx" not in a and "match" not in a and "prev" not in a
    assert a["rects"][0]["x1"] == pytest.approx(72, abs=0.6) and 85 < a["rects"][0]["y1"] < a["rects"][0]["y2"] < 105
    # the reworded one: found by the rough search, dashed with its confidence, its quote now the new wording
    b = ops["hlB"]["pdf_position"]
    assert b["pageNumber"] == 2 and 99 < b["rects"][0]["y1"] < b["rects"][0]["y2"] < 119
    assert b["approx"] is True and hr.ROUGH_APPLY <= b["match"] < 1
    assert ops["hlB"]["quote"] == "The spectrum shows a triplet at the resonant frequency"
    # ...and where it was before, on the file it was read from, with the quote it had
    assert b["prev"] == _prev(items[1][1]["pdf_position"], "v1", LINE_B)
    # the sentence that is gone: where it was, marked lost, still naming the file its rects belong to
    c = ops["hlC"]["pdf_position"]
    assert c["lost"] is True and c["pageNumber"] == 1 and c["rects"] == items[2][1]["pdf_position"]["rects"]
    assert c["doc_id"] == "v1" and "quote" not in ops["hlC"] and "prev" not in c
    # the caption's area: around the caption on page 2, as big as before, the picture inside it
    cap = ops["areaCap"]["pdf_position"]
    assert cap["area"] is True and cap["pageNumber"] == 2 and "approx" not in cap and "doc_id" not in cap
    assert "prev" not in cap
    assert cap["boundingRect"] == pytest.approx({"x1": 80, "y1": 320, "x2": 310, "y2": 450}, abs=2)
    # the picture's area: where the same picture is drawn now
    pic = ops["areaPic"]["pdf_position"]
    assert pic["pageNumber"] == 3 and "approx" not in pic and "prev" not in pic
    assert pic["boundingRect"] == pytest.approx({"x1": 198, "y1": 395, "x2": 303, "y2": 505}, abs=2)
    # the area over nothing: carried to the page its neighbours went to, shifted by the median of their shifts (two
    # of the three did not move on the page), and said to be a guess, with where it was (an area's prev has no quote)
    ride = ops["areaRide"]["pdf_position"]
    assert ride["pageNumber"] == 2 and ride["approx"] is True and "doc_id" not in ride and "match" not in ride
    assert ride["boundingRect"] == pytest.approx({"x1": 400, "y1": 600, "x2": 500, "y2": 700}, abs=2)
    assert ride["prev"] == _prev(items[5][1]["pdf_position"], "v1")
    # the run reported every page read and every highlight tried, and stopped by at each page it searched word by word
    assert {(r.get("unit"), r.get("phase")) for r in reports} == {("pages", "reading"), ("highlights", "matching"), (None, None)}


def test_place_all_goes_by_the_stored_quotes_when_the_file_a_highlight_names_is_gone():
    new = _pdf([[(72, 100, "Front matter")], [(72, 100, LINE_A), (72, 114, LINE_B)]])

    def on_gone(page_no, rect):
        return {**_position(page_no, rect), "doc_id": "gone"}

    items = [("hlA", {"pdf_position": on_gone(1, _line_rect(72, 100, LINE_A)), "quote": LINE_A}),
             ("hlB", {"pdf_position": on_gone(2, _line_rect(72, 114, LINE_B)), "quote": LINE_B}),
             ("none", {"pdf_position": on_gone(1, _line_rect(72, 128, "x")), "quote": ""})]
    with hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items)  # no read_from: no other file is stored
    assert _counts(placed) == {"highlights": 3, "moved": 1, "kept": 1, "lost": 1, "approx": 0}
    assert placed["ops"]["hlA"]["pdf_position"]["pageNumber"] == 2
    # B was already where its text is: its position is written once more only to say it is on this file now
    assert placed["ops"]["hlB"] == {"pdf_position": _position(2, _line_rect(72, 114, LINE_B))}
    lost = placed["ops"]["none"]["pdf_position"]  # nothing to look for
    assert lost["lost"] is True and lost["doc_id"] == "gone"


def test_a_highlight_whose_quote_is_not_the_text_under_it_is_looked_for_by_its_quote():
    old = _pdf([[(72, 100, LINE_C)]])
    new = _pdf([[(72, 100, "Front matter"), (72, 300, LINE_C)], [(72, 100, LINE_A)]])
    rect = _line_rect(72, 100, LINE_A)
    items = [
        # named with a stored file whose text under the rects is another sentence: not the file they were measured on
        ("named", {"pdf_position": {**_position(1, rect), "doc_id": "v1"}, "quote": LINE_A}),
        # measured on the page's file, as the viewer writes it, but the rects lie over the front matter
        ("current", {"pdf_position": _position(1, rect), "quote": LINE_A}),
        # a quote that reads a little differently from the file under the rects (pdf.js's text) still agrees
        ("agrees", {"pdf_position": {**_position(1, _line_rect(72, 100, LINE_C)), "doc_id": "v1"},
                    "quote": LINE_C.replace("first", "1st")}),
    ]
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert _counts(placed) == {"highlights": 3, "moved": 3, "kept": 0, "lost": 0, "approx": 0}
    for bid in ("named", "current"):
        pos = placed["ops"][bid]["pdf_position"]
        assert pos["pageNumber"] == 2 and 85 < pos["rects"][0]["y1"] < 105, bid
    agrees = placed["ops"]["agrees"]["pdf_position"]  # by the file's passage: the quote's "1st" is found nowhere
    assert agrees["pageNumber"] == 1 and 285 < agrees["rects"][0]["y1"] < 305


def test_a_short_line_under_the_rects_is_not_the_quote_it_is_a_word_of():
    quote = "The ions are cooled in a deep trap and then imaged"
    old = _pdf([[(72, 400, "deep trap")]])
    new = _pdf([[(72, 100, "Front matter"), (72, 200, "We used a deep trap for the other species"), (72, 400, "deep trap")],
                [(72, 100, quote)]])
    rect = _line_rect(72, 400, "deep trap")
    items = [
        # measured on another file, whose text under the rects is a short line holding two of the quote's words
        ("named", {"pdf_position": {**_position(1, rect), "doc_id": "v1"}, "quote": quote}),
        # named with no file (a PDF replaced before the run existed), the rects now over such a line
        ("current", {"pdf_position": _position(1, rect), "quote": quote}),
    ]
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"named": "moved", "current": "moved"}
    for bid in ("named", "current"):  # the quote is looked for, and found whole
        assert placed["ops"][bid] == {"pdf_position": _measured(new, 2, quote)}, bid
    # one that is most of the quote still agrees with it
    assert hr._agrees("the ions are cooled in a deep trap", hr.normalize_quote(quote))
    assert not hr._agrees("deep trap", hr.normalize_quote(quote))


def test_a_respelled_quote_reads_a_word_hyphenated_at_a_line_s_end_whole():
    line = "We show the entanglement of two atoms in a trap"
    new = _pdf([[(72, 100, "We show the Entangle-"), (72, 114, "ment of two ions in a trap")]])
    pos = {**_position(1, _line_rect(72, 300, line)), "doc_id": "v1"}
    with hr.Doc(_pdf([[(72, 300, line)]])) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": line})], read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"hl": "approx"}
    assert placed["ops"]["hl"]["quote"] == "We show the Entanglement of two ions in a trap"


def test_a_sentence_reworded_in_place_stays_but_dashed_with_its_new_spelling():
    line = "We analyse the spectrum of the doublet at the resonance"
    respelled = line.replace("analyse", "analyze")  # as wide in Helvetica: the highlight does not move
    pos = {**_position(1, _line_rect(72, 100, line)), "doc_id": "v1"}
    new = _pdf([[(72, 100, respelled)]])
    with hr.Doc(_pdf([[(72, 100, line)]])) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": line})], read_from={"v1": old_doc}.get)
    assert _counts(placed) == {"highlights": 1, "moved": 0, "kept": 0, "lost": 0, "approx": 1}
    match = round(hr._similar(hr.normalize_quote(line), hr.normalize_quote(respelled)), 2)
    assert placed["ops"] == {"hl": {"pdf_position": {**_measured(new, 1, respelled), "approx": True, "match": match,
                                                     "prev": _prev(pos, "v1", line)},
                                    "quote": respelled}}
    assert hr._same_place(pos, _measured(new, 1, respelled))  # where it was


def test_margin_line_numbers_on_either_side_change_nothing():
    body = ["The first line of the body text runs here", "and the second line continues the thought",
            "while the third line ends the sentence."]

    def paper(y0, numbered):
        return _pdf([[line for i, t in enumerate(body)
                      for line in ((72, y0 + 14 * i, t),) + (((50, y0 + 14 * i, str(i + 1)),) if numbered else ())]])

    quote = " ".join(body)
    for old, new in ((paper(100, True), paper(300, False)), (paper(100, False), paper(300, True))):
        pos = {**_position(1, [_line_rect(72, 100 + 14 * i, t) for i, t in enumerate(body)]), "doc_id": "v1"}
        with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
            placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": quote})], read_from={"v1": old_doc}.get)
        assert placed["kinds"] == {"hl": "moved"}
        op = placed["ops"]["hl"]
        assert "quote" not in op  # found exactly
        rects = op["pdf_position"]["rects"]
        assert len(rects) == 3 and all(r["x1"] == pytest.approx(72, abs=0.6) for r in rects)
        assert 285 < rects[0]["y1"] < 305


def test_a_new_file_without_text_fails_the_run_instead_of_losing_every_highlight():
    blank = _pdf([[("image", (72, 100, 272, 200), RED)]])
    with hr.Doc(blank) as new_doc, pytest.raises(ValueError, match="no text layer"):
        hr.place_all(new_doc, [("hl", {"pdf_position": _position(1, _line_rect(72, 100, LINE_A)), "quote": LINE_A})])


def test_a_lost_highlight_already_marked_is_not_written_again():
    new = _pdf([[(72, 100, "Nothing of the old text")]])
    pos = {**_position(1, _line_rect(72, 100, LINE_A)), "lost": True}
    with hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": LINE_A})])
    assert placed["lost"] == 1 and placed["ops"] == {}


def test_a_lost_highlight_found_again_loses_its_mark_even_in_the_same_place():
    pdf = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)]])
    here = _measured(pdf, 1, LINE_A)
    moved = _pdf([[(72, 100, "A new front page")], [(72, 300, LINE_B)]])
    with hr.Doc(pdf) as same, hr.Doc(moved) as elsewhere:
        again = hr.place_all(same, [("hl", {"pdf_position": {**here, "lost": True}, "quote": LINE_A})])
        found = hr.place_all(elsewhere, [("hl", {"pdf_position": {**_measured(pdf, 1, LINE_B), "lost": True}, "quote": LINE_B})])
    assert again["kinds"] == {"hl": "moved"} and again["ops"] == {"hl": {"pdf_position": here}}
    assert found["kinds"] == {"hl": "moved"}
    pos = found["ops"]["hl"]["pdf_position"]
    assert pos["pageNumber"] == 2 and "lost" not in pos and 285 < pos["rects"][0]["y1"] < 305


def test_the_matching_reads_no_page_past_the_cap(monkeypatch):
    monkeypatch.setattr(hr, "MAX_PAGES", 2)
    pdf = _pdf([[(72, 100, "Front matter")], [(72, 100, LINE_B)], [(72, 100, LINE_A)]])
    reports = []
    with hr.Doc(pdf) as doc:
        assert doc.count == 2
        placed = hr.place_all(doc, [("hl", {"pdf_position": _position(3, _line_rect(72, 100, LINE_A)), "quote": LINE_A})],
                              progress=lambda **kw: reports.append(kw))
    assert placed["kinds"] == {"hl": "lost"}  # like a passage not found
    assert {r["total"] for r in reports if r.get("phase") == "reading"} == {2}


# --- areas carried along ------------------------------------------------------------------

RIDER = {**_position(1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}, area=True), "doc_id": "v1"}


def _ride(old, new):
    """place_all over three text highlights made on ``old``'s lines A, B, C
    and an area over nothing on its first page, all naming that file."""
    items = [(f"hl{k}", {"pdf_position": {**_measured(old, 1, t), "doc_id": "v1"}, "quote": t})
             for k, t in zip("ABC", (LINE_A, LINE_B, LINE_C))]
    items.append(("area", {"pdf_position": RIDER}))
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        return hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)


def test_an_area_rides_along_only_when_its_page_s_highlights_all_moved_alike():
    old = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B), (72, 128, LINE_C)]])

    def page(*lines):
        return [(72, y, t) for y, t in lines]

    # nothing placed it, so it stays where it was, still the old file's measurement, and is said to be a guess,
    # its prev those same rects on that file
    unplaced = {"pdf_position": {**RIDER, "approx": True, "prev": _prev(RIDER, "v1")}}
    # A and B stayed, C went to the next page: the area has no one page to go to
    placed = _ride(old, _pdf([page((100, LINE_A), (114, LINE_B)), page((100, LINE_C))]))
    assert placed["kinds"] == {"hlA": "kept", "hlB": "kept", "hlC": "moved", "area": "approx"}
    assert placed["ops"]["area"] == unplaced
    # A and B stayed, C went far down the page: they do not agree
    placed = _ride(old, _pdf([page((100, LINE_A), (114, LINE_B), (365, LINE_C))]))
    assert placed["kinds"]["area"] == "approx" and placed["ops"]["area"] == unplaced
    # all three went to the next page, 40 points lower: the area goes with them, by just as much
    placed = _ride(old, _pdf([page((100, "A new front page")), page((140, LINE_A), (154, LINE_B), (168, LINE_C))]))
    assert placed["kinds"]["area"] == "approx"
    ride = placed["ops"]["area"]["pdf_position"]
    assert ride["pageNumber"] == 2 and ride["approx"] is True and "doc_id" not in ride and ride["prev"] == _prev(RIDER, "v1")
    assert ride["boundingRect"] == pytest.approx({"x1": 400, "y1": 640, "x2": 500, "y2": 740}, abs=0.05)
    # all three stayed where they were: so does the area, now this file's
    placed = _ride(old, old)
    assert placed["kinds"]["area"] == "kept" and placed["ops"]["area"] == {"pdf_position": _position(
        1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}, area=True)}
    # all three went so far down the page that the area would leave it: nothing places it
    placed = _ride(old, _pdf([page((700, LINE_A), (714, LINE_B), (728, LINE_C))]))
    assert placed["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "moved", "area": "approx"}
    assert placed["ops"]["area"] == unplaced
    # all three on the next page, but shifted by more than RIDE_SPREAD apart
    placed = _ride(old, _pdf([page((100, "A new front page")), page((120, LINE_A), (134, LINE_B), (400, LINE_C))]))
    assert _counts(placed) == {"highlights": 4, "moved": 3, "kept": 0, "lost": 0, "approx": 1}
    assert placed["ops"]["area"] == unplaced


def test_an_area_placed_by_estimate_stays_an_estimate_through_later_runs():
    inset = "Inset of a sketch"  # what the new file happens to draw where the estimate puts the area

    def version(dy, with_inset=True):
        return _pdf([[(72, 100, "A new front page")],
                     [(72, 100 + dy + 14 * i, t) for i, t in enumerate((LINE_A, LINE_B, LINE_C))]
                     + ([(410, 690 + dy, inset)] if with_inset else [])])

    def applied(items, placed):
        return [(bid, {**props, **placed["ops"].get(bid, {})}) for bid, props in items]

    old = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate((LINE_A, LINE_B, LINE_C))]])
    items = [(f"hl{k}", {"pdf_position": {**_measured(old, 1, t), "doc_id": "v1"}, "quote": t})
             for k, t in zip("ABC", (LINE_A, LINE_B, LINE_C))]
    items.append(("area", {"pdf_position": {**_position(1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}, area=True),
                                            "doc_id": "v1"}}))
    v2, v3 = version(40), version(70)
    with hr.Doc(old) as v1_doc, hr.Doc(v2) as v2_doc, hr.Doc(v3) as v3_doc:
        first = hr.place_all(v2_doc, items, read_from={"v1": v1_doc}.get)
        assert first["kinds"]["area"] == "approx"
        estimate = first["ops"]["area"]["pdf_position"]
        assert estimate["approx"] is True and estimate["boundingRect"] == pytest.approx(
            {"x1": 400, "y1": 640, "x2": 500, "y2": 740}, abs=0.05)
        assert estimate["prev"] == _prev(items[3][1]["pdf_position"], "v1")
        # Find highlights again on the same file: the inset under the estimate is found where it is, which
        # confirms nothing, so the area stays as it was, still an estimate, and is counted dashed again
        items = applied(items, first)
        again = hr.place_all(v2_doc, items)
        assert again["kinds"] == {"hlA": "kept", "hlB": "kept", "hlC": "kept", "area": "approx"} and again["ops"] == {}
        # the next version, everything 30 points lower: the area follows the inset, and is still an estimate
        stamped = [(bid, {**props, "pdf_position": {**props["pdf_position"], "doc_id": "v2"}}) for bid, props in items]
        later = hr.place_all(v3_doc, stamped, read_from={"v2": v2_doc}.get)
    assert later["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "moved", "area": "approx"}
    assert _counts(later) == {"highlights": 4, "moved": 3, "kept": 0, "lost": 0, "approx": 1}
    moved = later["ops"]["area"]["pdf_position"]
    assert moved["approx"] is True and "doc_id" not in moved
    assert moved["prev"] == estimate["prev"]  # still where it was before the first estimate
    assert moved["boundingRect"] == pytest.approx({"x1": 400, "y1": 670, "x2": 500, "y2": 770}, abs=0.05)


def test_an_area_with_nothing_to_go_by_stays_and_is_marked_unless_it_was_made_on_this_file():
    pdf = _pdf([[(72, 100, LINE_A)], [(72, 100, LINE_B)]])
    box = _position(1, {"x1": 300, "y1": 400, "x2": 400, "y2": 500}, area=True)
    with hr.Doc(pdf) as doc, hr.Doc(pdf) as other:
        # made on the page's own file: where it belongs
        placed = hr.place_all(doc, [("area", {"pdf_position": box})])
        assert placed["kinds"] == {"area": "kept"} and placed["ops"] == {}
        # measured on another file (gone, or there with nothing under the box): its rects are still that file's
        for read_from in (None, {"v1": other}.get):
            placed = hr.place_all(doc, [("area", {"pdf_position": {**box, "doc_id": "v1"}})], read_from=read_from)
            assert _counts(placed) == {"highlights": 1, "moved": 0, "kept": 0, "lost": 0, "approx": 1}
            assert placed["ops"] == {"area": {"pdf_position": {**box, "doc_id": "v1", "approx": True,
                                                               "prev": _prev(box, "v1")}}}
        # marked once
        placed = hr.place_all(doc, [("area", {"pdf_position": {**box, "doc_id": "v1", "approx": True}})])
        assert placed["kinds"] == {"area": "approx"} and placed["ops"] == {}


def test_an_accepted_area_is_the_current_file_s_and_rides_along_at_the_next_replacement():
    old = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B), (72, 128, LINE_C)]])
    v2 = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)], [(72, 100, LINE_C)]])  # C went to the next page
    v3 = _pdf([[(72, 150, LINE_A), (72, 164, LINE_B)], [(72, 100, LINE_C)]])  # A and B 50 points lower

    def applied(items, placed):
        return [(bid, {**props, **placed["ops"].get(bid, {})}) for bid, props in items]

    items = [(f"hl{k}", {"pdf_position": {**_measured(old, 1, t), "doc_id": "v1"}, "quote": t})
             for k, t in zip("ABC", (LINE_A, LINE_B, LINE_C))]
    items.append(("area", {"pdf_position": RIDER}))
    with hr.Doc(old) as v1_doc, hr.Doc(v2) as v2_doc, hr.Doc(v3) as v3_doc:
        files = {"v1": v1_doc, "v2": v2_doc}.get
        first = hr.place_all(v2_doc, items, read_from=files, new_doc="v2")
        assert first["kinds"]["area"] == "approx" and first["ops"]["area"]["pdf_position"]["doc_id"] == "v1"
        items = applied(items, first)
        pending = items[3][1]["pdf_position"]
        # accepted as it would be had Accept kept doc_id: the next run reads it from v1 again and dashes it again
        kept_file = {k: v for k, v in pending.items() if k not in ("approx", "match", "prev")}
        again = hr.place_all(v2_doc, items[:3] + [("area", {"pdf_position": kept_file})], read_from=files, new_doc="v2")
        assert again["kinds"]["area"] == "approx" and again["ops"]["area"]["pdf_position"]["approx"] is True
        # accepted (blockModel.acceptedPosition): approx, prev and doc_id go, so it is the page's file's own,
        # and Find highlights in this PDF leaves it be
        accepted = {k: v for k, v in pending.items() if k not in ("approx", "match", "prev", "doc_id")}
        items[3] = ("area", {"pdf_position": accepted})
        again = hr.place_all(v2_doc, items, read_from=files, new_doc="v2")
        assert again["kinds"]["area"] == "kept" and again["ops"] == {}
        # the next replacement stamps it, with the rest, as v2's measurement: it rides along with A and B
        stamped = [(bid, {**props, "pdf_position": {**props["pdf_position"], "doc_id": "v2"}}) for bid, props in items]
        later = hr.place_all(v3_doc, stamped, read_from=files, new_doc="v3")
    assert later["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "kept", "area": "approx"}
    ride = later["ops"]["area"]["pdf_position"]
    assert ride["pageNumber"] == 1 and "doc_id" not in ride and ride["prev"] == _prev(accepted, "v2")
    assert ride["boundingRect"] == pytest.approx({"x1": 400, "y1": 650, "x2": 500, "y2": 750}, abs=0.05)


def test_an_unplaced_area_does_not_ride_along_with_highlights_measured_on_another_file():
    old = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B), (72, 128, LINE_C)]])
    new = _pdf([[(72, 140 + 14 * i, t) for i, t in enumerate((LINE_A, LINE_B, LINE_C))]])
    # an area nothing placed keeps the old file's rects; Find highlights in this PDF then finds its neighbours, this
    # file's, where they are, which says nothing of where the old file's area goes
    items = [(f"hl{k}", {"pdf_position": _measured(new, 1, t), "quote": t}) for k, t in zip("ABC", (LINE_A, LINE_B, LINE_C))]
    items.append(("area", {"pdf_position": {**RIDER, "approx": True}}))
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"hlA": "kept", "hlB": "kept", "hlC": "kept", "area": "approx"} and placed["ops"] == {}


# --- the cost of the search ---------------------------------------------------------------

def test_the_rough_search_s_bound_changes_no_answer_and_a_page_without_the_words_is_not_aligned(monkeypatch):
    texts = [["The cavity is driven at a frequency slightly below the atomic resonance, so that",
              "the atoms are cooled; the sample was held at 4 K for an hour before the run began.",
              "We then measured the spectrum of the doublet with a narrow probe laser."],
             ["In a second run the cavity was pumped at a frequency far below the resonance",
              "and the atoms were heated instead, as the spectrum of the triplet shows."],
             ["Nothing here reads like the rest of the paper at all, said the referee.",
              "We analyze the color and behavior of the center line, then optimize the modeled spectrum"]]
    pages = _texts(_pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(lines)] for lines in texts]))

    def unbounded(page, words):  # each window aligned to its end, the bound applied to the answer alone
        bound, cores = hr._bound(len(words.words)), page.word_index()[1]
        out = []
        for lo, hi in hr._seed_windows(page, words, bound):
            if hr._holds(words, set(cores[lo:hi])):
                cost, a, b = hr._align(words.words, page.word_texts, lo, hi)
                if cost <= bound and b > a:
                    out.append((cost, *hr._trimmed(page, page.word_starts[a], page.word_ends[b - 1], words.words)))
        return out if hr._holds(words, page.word_index()[2]) else []

    needles = {
        "edited": "the cavity is pumped at a frequency slightly below the atomic resonance so that the atoms are cooled",
        "gone": "an entirely different sentence about superconducting qubits and their coherence times",
        "punctuation": "the atoms are cooled, the sample was held at 4 K for an hour before the run",
        "digits": "the sample was held at 5 K for an hour before the run began",
        "respelled": "we analyse the colour and behaviour of the centre line, then optimise the modeled spectrum",
    }
    found = set()
    for name, needle in needles.items():
        words = hr._words_of(hr.normalize_quote(needle))
        for n, page in pages.items():
            expected = unbounded(page, words)
            assert hr._rough_on_page(page, words) == expected, (name, n)
            if expected:
                found.add(name)
    assert found == {"edited", "punctuation", "digits", "respelled"}
    # the needle whose words are on no page is not aligned at all
    aligned = []
    real = hr._align
    monkeypatch.setattr(hr, "_align", lambda *a, **kw: aligned.append(a) or real(*a, **kw))
    words = hr._words_of(hr.normalize_quote(needles["gone"]))
    assert all(hr._rough_on_page(page, words) == [] for page in pages.values()) and aligned == []


def test_a_stop_lands_within_a_page_of_the_rough_search(monkeypatch):
    lines = [(72, 100 + 14 * i, f"Line {i} of a page that says little of any interest") for i in range(20)]
    pdf = _pdf([lines] * 40)
    searched = []
    real = hr._rough_on_page
    monkeypatch.setattr(hr, "_rough_on_page", lambda page, words: searched.append(page) or real(page, words))

    class Stop(Exception):
        pass

    bare = []

    def progress(**kw):
        if not kw:
            bare.append(True)
            if len(bare) == 3:
                raise Stop

    items = [("hl", {"pdf_position": _position(1, _line_rect(72, 100, LINE_C)), "quote": LINE_C})]
    with hr.Doc(pdf) as doc, pytest.raises(Stop):
        hr.place_all(doc, items, progress=progress)
    assert len(searched) == 2  # the third page's search never began


# --- the endpoint and the job ------------------------------------------------------------

# a run's result, with the thresholds it went by (the defaults: the guest's profile sets none)
NOTHING = {"highlights": 0, "moved": 0, "kept": 0, "lost": 0, "approx": 0, "changed": 0, **DEFAULTS}


def _upload(client, data, name="paper.pdf"):
    r = client.post("/api/uploads", files={"file": (name, io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def _page_with(client, pdf_bytes, highlights):
    """A page carrying ``pdf_bytes`` with highlight blocks ``[(quote, position)]``; ``(page_id, doc_id, [block ids])``."""
    doc = _upload(client, pdf_bytes)
    page = client.post("/api/pages", json={"title": "Replaced paper"}).json()
    assert client.post(f"/api/pages/{page['id']}/attachment", json={"doc_id": doc}).status_code == 200
    ids = []
    for quote, position in highlights:
        r = client.post("/api/blocks", json={"parent_id": page["id"], "content": "",
                                             "properties": {"pdf_position": position, "quote": quote, "color": "#ffe28fa6"}})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return page["id"], doc, ids


def _props(client, block_id):
    r = client.get(f"/api/blocks/{block_id}")
    assert r.status_code == 200, r.text
    return r.json()["properties"]


def _replace(client, page_id, pdf_bytes, name="next.pdf"):
    """Replace the page's PDF by ``pdf_bytes``: ``(doc_id, the PUT's answer)``."""
    doc = _upload(client, pdf_bytes, name)
    r = client.put(f"/api/pages/{page_id}/attachment", json={"doc_id": doc})
    assert r.status_code == 200, r.text
    return doc, r.json()


def _done(client, job):
    assert job["kind"] == "relocate-highlights", job
    finished = jobs.wait(job["id"])
    assert finished["state"] == "done", finished
    return client.get(f"/api/jobs/{job['id']}").json()["result"]


def _hold_runs(monkeypatch):
    """Every relocation run waits before its matching until the returned
    event is set, reporting progress meanwhile as a long paper's would,
    so it can be stopped. Set the event in a ``finally``."""
    release = threading.Event()
    real = hr.place_all

    def held(new, items, progress=None, **kwargs):
        while not release.wait(0.02):
            if progress:
                progress()
        return real(new, items, progress, **kwargs)

    monkeypatch.setattr(hr, "place_all", held)
    return release


def _wait_running(job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while jobs.get(job_id)["state"] != "running":
        assert time.monotonic() < deadline, "the run never started"
        time.sleep(0.02)


def test_replacing_the_pdf_starts_a_job_that_moves_the_highlights(guest):
    old = _pdf([[(72, 100, LINE_A), (72, 114, LINE_C)]])
    new = _pdf([[(72, 100, "A new front page")], [(72, 300, LINE_A)]])
    page_id, old_doc, (hl_a, hl_c) = _page_with(guest, old, [
        (LINE_A, _position(1, _line_rect(72, 100, LINE_A))), (LINE_C, _position(1, _line_rect(72, 114, LINE_C)))])
    new_doc = _upload(guest, new, "paper-v2.pdf")
    r = guest.put(f"/api/pages/{page_id}/attachment", json={"doc_id": new_doc, "original_filename": "paper-v2.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["properties"]["doc_id"] == new_doc and "relocation_error" not in body
    job = body["job"]
    assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": new_doc, **DEFAULTS}
    assert job["title"] == "Placing the highlights of “Replaced paper”"
    assert _done(guest, job) == {**NOTHING, "highlights": 2, "moved": 1, "lost": 1}
    a = _props(guest, hl_a)
    assert a["pdf_position"]["pageNumber"] == 2 and 285 < a["pdf_position"]["rects"][0]["y1"] < 305 and a["quote"] == LINE_A
    assert "doc_id" not in a["pdf_position"]  # placed on the page's file now
    c = _props(guest, hl_c)["pdf_position"]
    assert c["lost"] is True and c["pageNumber"] == 1 and c["doc_id"] == old_doc  # its rects are still the old file's
    # the job is listed with the account's others; a replacement with the same file starts none
    assert any(j["id"] == job["id"] for j in guest.get("/api/jobs").json()["jobs"])
    again = guest.put(f"/api/pages/{page_id}/attachment", json={"doc_id": new_doc}).json()
    assert "job" not in again
    # the same stored file from another address: the file did not change, so nothing is named and nothing runs
    moved = guest.put(f"/api/pages/{page_id}/attachment",
                      json={"doc_id": new_doc, "source_url": "https://example.org/paper-v2.pdf"}).json()
    assert moved["properties"]["source_url"] == "https://example.org/paper-v2.pdf"
    assert "job" not in moved and "relocation_error" not in moved
    assert "doc_id" not in _props(guest, hl_a)["pdf_position"] and _props(guest, hl_c)["pdf_position"]["doc_id"] == old_doc


def test_the_counts_say_how_the_run_leaves_each_highlight(guest):
    cited = "Similar schemes were shown with ions [3] and with superconducting circuits"
    analyse = "We analyse the spectrum of the doublet at the resonance"
    old_lines = [(100, LINE_A), (114, LINE_B), (128, cited), (142, analyse), (156, LINE_C)]
    page_id, old_doc, ids = _page_with(guest, _pdf([[(72, y, t) for y, t in old_lines]]),
                                       [(t, _position(1, _line_rect(72, y, t))) for y, t in old_lines]
                                       + [("", _position(1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}, area=True))])
    hl_a, hl_b, hl_cited, hl_analyse, hl_c, area = ids
    analyze = analyse.replace("analyse", "analyze")
    new = [(114, LINE_B), (142, analyze), (300, LINE_A), (400, cited.replace("[3]", "[4]"))]
    _new_doc, body = _replace(guest, page_id, _pdf([[(72, y, t) for y, t in new]]))
    # moved: solid, somewhere else; kept: solid, where it was; approx: dashed (the citation renumbered, the word
    # respelled in place, the area nothing placed); lost: not found
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 6, "moved": 1, "kept": 1, "approx": 3, "lost": 1}
    stored = {hl: _props(guest, hl) for hl in ids}
    for hl in (hl_a, hl_b, hl_c):
        assert not {"approx", "match", "prev"} & set(stored[hl]["pdf_position"])
    # the dashed ones keep where they were before, on the file they were made on, as the card compares them
    for hl, (y, was), now in ((hl_cited, old_lines[2], cited.replace("[3]", "[4]")), (hl_analyse, old_lines[3], analyze)):
        pos = stored[hl]["pdf_position"]
        assert pos["approx"] is True and hr.ROUGH_APPLY <= pos["match"] < 1 and stored[hl]["quote"] == now
        assert pos["prev"] == _prev(_position(1, _line_rect(72, y, was)), old_doc, was)
    assert stored[hl_c]["pdf_position"]["lost"] is True
    assert stored[area]["pdf_position"]["approx"] is True and "match" not in stored[area]["pdf_position"]
    assert stored[area]["pdf_position"]["prev"] == _prev(_position(1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}),
                                                         old_doc)


def _set_profile(client, **prefs):
    r = client.patch("/api/prefs/profile", json={"set": prefs})
    assert r.status_code == 200, r.text


def test_a_run_goes_by_its_owner_s_thresholds_as_they_were_when_it_started(guest):
    cited = "Similar schemes were shown with ions [3] and with superconducting circuits"
    cooling = "Then the 4 K cooling stage is switched on"
    page_id, old_doc, (hl_cited, hl_cooling) = _page_with(guest, _pdf([[(72, 100, cited), (72, 114, cooling)]]), [
        (cited, _position(1, _line_rect(72, 100, cited))),
        ("4 K cooling", _position(1, _part_rect(72, 114, cooling, "4 K cooling")))])
    new = _pdf([[(72, 300, cited.replace("[3]", "[4]")), (72, 314, cooling.replace("4 K", "5 K"))]])
    strict, loose = {"min_match": 1.0, "exact_chars": 12}, {"min_match": 0.8, "exact_chars": 0}
    try:
        # exact copies only: the renumbered citation and the changed short passage are both lost
        _set_profile(guest, relocateMinMatch=100, relocateExactChars=12)
        new_doc, body = _replace(guest, page_id, new)
        first = body["job"]
        assert first["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": new_doc, **strict}
        assert _done(guest, first) == {**NOTHING, "highlights": 2, "lost": 2, **strict}
        # looser, by hand: the citation from 80%, and the short passage matched roughly too
        _set_profile(guest, relocateMinMatch=80, relocateExactChars=0)
        job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
        assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": new_doc, **loose}
        assert _done(guest, job) == {**NOTHING, "highlights": 2, "approx": 2, **loose}
        cooled = _props(guest, hl_cooling)
        assert cooled["quote"] == "5 K cooling" and cooled["pdf_position"]["approx"] is True
        assert cooled["pdf_position"]["prev"] == _prev(_position(1, _part_rect(72, 114, cooling, "4 K cooling")), old_doc,
                                                       "4 K cooling")
        assert _props(guest, hl_cited)["pdf_position"]["approx"] is True
        # the run before still says what it went by
        assert guest.get(f"/api/jobs/{first['id']}").json()["result"] == {**NOTHING, "highlights": 2, "lost": 2, **strict}
        # a setting out of its range is pulled into it, one that is no number is the default
        _set_profile(guest, relocateMinMatch=400, relocateExactChars="5")
        job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
        assert (job["params"]["min_match"], job["params"]["exact_chars"]) == (1.0, hr.ROUGH_MIN_CHARS)
        # still pending, both are matched from the first file again: neither is an exact copy, so both go back
        # hollow to their place there, with the first file's wording, the citation's rough place its closest match
        assert _done(guest, job) == {**NOTHING, "highlights": 2, "lost": 2, "min_match": 1.0}
        for hl, quote, rect in ((hl_cited, cited, _line_rect(72, 100, cited)),
                                (hl_cooling, "4 K cooling", _part_rect(72, 114, cooling, "4 K cooling"))):
            props = _props(guest, hl)
            pos = props["pdf_position"]
            assert props["quote"] == quote and pos["lost"] is True and pos["doc_id"] == old_doc
            assert not {"approx", "match", "prev"} & set(pos)
            assert pos["rects"][0] == pytest.approx(rect, abs=0.1)
        assert _props(guest, hl_cited)["pdf_position"]["closest"]["quote"] == cited.replace("[3]", "[4]")
    finally:
        _set_profile(guest, relocateMinMatch=None, relocateExactChars=None)


def test_a_replacement_names_each_highlight_s_file_and_says_when_its_run_cannot_start(guest, monkeypatch):
    v1 = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)]])
    lost = {**_position(1, _line_rect(72, 128, LINE_C)), "doc_id": "an-older-file", "lost": True}
    page_id, doc1, (hl_a, hl_b, hl_c) = _page_with(guest, v1, [
        (LINE_A, _position(1, _line_rect(72, 100, LINE_A))),
        # measured on a file before this one, as a run that never wrote leaves it
        (LINE_B, {**_position(1, _line_rect(72, 114, LINE_B)), "doc_id": "an-older-file"}),
        # lost there, with the closest place a run found in this file
        (LINE_C, {**lost, "closest": {**_position(1, _line_rect(72, 114, LINE_B)), "match": 0.6, "quote": LINE_B}})])
    a_before = _props(guest, hl_a)["pdf_position"]
    with monkeypatch.context() as m:
        m.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 0)
        doc2, body = _replace(guest, page_id, _pdf([[(72, 300, LINE_A), (72, 314, LINE_B)]]), "v2.pdf")
        # the replacement stands, without a run, and says why
        assert body["properties"]["doc_id"] == doc2 and "job" not in body
        assert "background tasks" in body["relocation_error"]
        # the same commit named the file each highlight was measured on; one that named its file keeps it
        assert _props(guest, hl_a)["pdf_position"] == {**a_before, "doc_id": doc1}
        assert _props(guest, hl_b)["pdf_position"]["doc_id"] == "an-older-file"
        # and a lost one's closest place went: it was a place in the file the page no longer shows
        assert _props(guest, hl_c)["pdf_position"] == lost
        r = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
        assert r.status_code == 429, r.text
    # by hand later: A read from the file its rects name, B (whose file is not stored) by its quote
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
    assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": doc2, **DEFAULTS}
    assert _done(guest, job) == {**NOTHING, "highlights": 3, "moved": 2, "lost": 1}
    for hl, y in ((hl_a, 300), (hl_b, 314)):
        pos = _props(guest, hl)["pdf_position"]
        assert y - 15 < pos["rects"][0]["y1"] < y and "doc_id" not in pos


def test_a_run_that_never_wrote_leaves_each_highlight_pointing_at_the_file_it_was_measured_on(guest, monkeypatch):
    a = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)]])
    swapped = _pdf([[(72, 100, LINE_B), (72, 114, LINE_A)]])
    page_id, _doc, (hl_a, hl_b) = _page_with(guest, a, [
        (LINE_A, _position(1, _line_rect(72, 100, LINE_A))),
        ("", _position(1, _line_rect(72, 114, LINE_B)))])  # imported without a quote: only its file says what it covers
    with monkeypatch.context() as m:
        m.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 0)
        assert "relocation_error" in _replace(guest, page_id, swapped, "b.pdf")[1]
    # the next version: the swapped lines after a front page. Each highlight is read from the file it was made on.
    _doc_c, body = _replace(guest, page_id, _pdf([[(72, 100, "A new front page")], [(72, 100, LINE_B), (72, 114, LINE_A)]]), "c.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "moved": 2}
    a_pos, b_pos = _props(guest, hl_a)["pdf_position"], _props(guest, hl_b)["pdf_position"]
    assert a_pos["pageNumber"] == 2 and 99 < a_pos["rects"][0]["y1"] < 119  # LINE_A's line
    assert b_pos["pageNumber"] == 2 and 85 < b_pos["rects"][0]["y1"] < 105  # LINE_B's line


def test_a_lost_highlight_is_still_read_from_its_own_file_by_the_next_runs(guest):
    other = "Another sentence printed where the old one was"
    a = _pdf([[(72, 100, LINE_A), (72, 114, LINE_C)]])
    page_id, doc_a, (hl_a, hl_c) = _page_with(guest, a, [
        (LINE_A, _position(1, _line_rect(72, 100, LINE_A))), (LINE_C, _position(1, _line_rect(72, 114, LINE_C)))])
    _b, body = _replace(guest, page_id, _pdf([[(72, 100, LINE_A), (72, 114, other)]]), "b.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "kept": 1, "lost": 1}
    lost = _props(guest, hl_c)["pdf_position"]
    assert lost["lost"] is True and lost["doc_id"] == doc_a
    assert "doc_id" not in _props(guest, hl_a)["pdf_position"]  # kept: on the page's file now
    # the sentence B prints where LINE_C was is not LINE_C: the next file holding it changes nothing for C
    _c, body = _replace(guest, page_id, _pdf([[(72, 100, "A new front page")], [(72, 100, LINE_A), (72, 114, other)]]), "c.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "moved": 1, "lost": 1}
    assert _props(guest, hl_c)["pdf_position"] == lost
    # a version with LINE_C again finds it
    _d, body = _replace(guest, page_id, _pdf([[(72, 100, "A new front page")], [(72, 100, LINE_A), (72, 300, LINE_C)]]), "d.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "moved": 1, "kept": 1}
    found = _props(guest, hl_c)["pdf_position"]
    assert found["pageNumber"] == 2 and 285 < found["rects"][0]["y1"] < 305 and "lost" not in found and "doc_id" not in found


def test_after_a_wrong_file_the_right_one_finds_the_highlights_again(guest):
    lines = [LINE_A, LINE_B, LINE_C, CAPTION]

    def paper(footer):
        return _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(lines)] + [(72, 700, footer)]])

    def page_of(footer):
        return _page_with(guest, paper(footer), [(t, _position(1, _line_rect(72, 100 + 14 * i, t))) for i, t in enumerate(lines)])

    def wrong(name):
        _doc, body = _replace(guest, page_id, _pdf([[(72, 100, f"Nothing of the paper is in {name}")]]), name)
        assert _done(guest, body["job"]) == {**NOTHING, "highlights": 4, "lost": 4}

    # a wrong file, then the next version (reflowed, after a front page)
    page_id, _doc, ids = page_of("first copy")
    wrong("wrong-1.pdf")
    reflowed = _pdf([[(72, 100, "A new front page")], [(90, 300 + 20 * i, t) for i, t in enumerate(lines)]])
    _doc, body = _replace(guest, page_id, reflowed, "v2.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 4, "moved": 4}
    for i, hl in enumerate(ids):
        pos = _props(guest, hl)["pdf_position"]
        assert pos["pageNumber"] == 2 and 285 + 20 * i < pos["rects"][0]["y1"] < 305 + 20 * i and "lost" not in pos
    # a wrong file, then the original put back
    page_id, doc, ids = page_of("second copy")
    wrong("wrong-2.pdf")
    body = guest.put(f"/api/pages/{page_id}/attachment", json={"doc_id": doc}).json()
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 4, "moved": 4}
    for i, hl in enumerate(ids):
        pos = _props(guest, hl)["pdf_position"]
        assert pos["pageNumber"] == 1 and 85 + 14 * i < pos["rects"][0]["y1"] < 105 + 14 * i
        assert "lost" not in pos and "doc_id" not in pos


def test_a_position_naming_a_file_not_stored_or_no_file_at_all_is_placed_by_its_quote(guest, monkeypatch):
    from gamma import ai_context

    pdf = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)]])
    page_id, doc, (gone, bad) = _page_with(guest, pdf, [
        (LINE_A, {**_position(1, _line_rect(72, 400, LINE_A)), "doc_id": "0" * 64}),
        (LINE_B, {**_position(1, _line_rect(72, 500, LINE_B)), "doc_id": "../x"})])
    real = ai_context.pdf_path

    def the_page_s_file_only(ws, doc_id):
        assert doc_id == doc, f"pdf_path (which fetches) was asked for another file: {doc_id}"
        return real(ws, doc_id)

    monkeypatch.setattr(ai_context, "pdf_path", the_page_s_file_only)
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
    assert _done(guest, job) == {**NOTHING, "highlights": 2, "moved": 2}
    for hl, y in ((gone, 100), (bad, 114)):
        pos = _props(guest, hl)["pdf_position"]
        assert y - 15 < pos["rects"][0]["y1"] < y and "doc_id" not in pos


def test_the_highlights_of_a_page_shown_from_a_url_alone_are_placed_by_their_quotes(guest, monkeypatch):
    page = guest.post("/api/pages", json={"title": "From a link"}).json()
    r = guest.post(f"/api/pages/{page['id']}/attachment", json={"source_url": "https://example.org/paper.pdf"})
    assert r.status_code == 200, r.text
    r = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "", "properties": {
        "pdf_position": _position(1, _line_rect(72, 100, LINE_A)), "quote": LINE_A}})
    hl = r.json()["id"]
    with monkeypatch.context() as m:
        m.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 0)
        _replace(guest, page["id"], _pdf([[(72, 100, "A new front page")], [(72, 300, LINE_A)]]))
    # no stored file to read it from: named as such, so no run reads the new file under its rects
    assert _props(guest, hl)["pdf_position"]["doc_id"] == storage.UNSTORED
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page["id"]}).json()
    assert _done(guest, job) == {**NOTHING, "highlights": 1, "moved": 1}
    pos = _props(guest, hl)["pdf_position"]
    assert pos["pageNumber"] == 2 and 285 < pos["rects"][0]["y1"] < 305 and "doc_id" not in pos


def _duplicate(client, doc, highlights):
    """A copy of a page carrying ``doc``, made as the library's Duplicate
    makes one: the stored file shown by its address alone, no doc_id, the
    highlight blocks ``[(quote, position)]`` copied; ``(page_id, [ids])``."""
    r = client.post("/api/blocks", json={"parent_id": "root", "content": "Replaced paper (copy)",
                                         "properties": {"source_url": f"/api/uploads/{doc}.pdf"}})
    assert r.status_code == 200, r.text
    page_id = r.json()["id"]
    assert _props(client, page_id) == {"source_url": f"/api/uploads/{doc}.pdf"}
    ids = []
    for quote, position in highlights:
        r = client.post("/api/blocks", json={"parent_id": page_id, "content": "",
                                             "properties": {"pdf_position": position, "quote": quote}})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return page_id, ids


def test_a_duplicated_page_s_highlights_are_read_from_and_placed_in_its_stored_file(guest):
    a = _pdf([[(72, 100, LINE_A), ("image", (72, 200, 172, 300), BLUE)]])
    _page_id, doc_a, _ids = _page_with(guest, a, [])
    # Find highlights in this PDF on the copy: its file is the stored one its address serves
    copy, (hl,) = _duplicate(guest, doc_a, [(LINE_A, _position(1, _line_rect(72, 300, LINE_A)))])
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": copy}).json()
    assert job["params"]["doc_id"] == doc_a
    assert _done(guest, job) == {**NOTHING, "highlights": 1, "moved": 1}
    assert 85 < _props(guest, hl)["pdf_position"]["rects"][0]["y1"] < 105
    # replacing the copy's PDF names that stored file on its highlights, so an area over a picture follows it
    copy, (area,) = _duplicate(guest, doc_a, [("", _position(1, {"x1": 70, "y1": 195, "x2": 175, "y2": 305}, area=True))])
    _doc_b, body = _replace(guest, copy, _pdf([[(72, 100, LINE_A)], [("image", (200, 400, 300, 500), BLUE)]]), "b.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 1, "moved": 1}
    pos = _props(guest, area)["pdf_position"]
    assert pos["pageNumber"] == 2 and pos["boundingRect"] == pytest.approx({"x1": 198, "y1": 395, "x2": 303, "y2": 505}, abs=2)


def test_a_pdf_shown_from_a_url_alone_starts_no_run(guest):
    page = guest.post("/api/pages", json={"title": "From a link"}).json()
    assert guest.post(f"/api/pages/{page['id']}/attachment", json={"source_url": "https://example.org/paper.pdf"}).status_code == 200
    r = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "", "properties": {
        "pdf_position": _position(1, _line_rect(72, 100, LINE_A)), "quote": LINE_A}})
    hl = r.json()["id"]
    r = guest.post("/api/jobs/relocate-highlights", json={"page_id": page["id"]})
    assert r.status_code == 400 and r.json()["detail"] == "the PDF is not stored on the server"
    # replaced by another URL alone: the replacement stands and says why nothing places the highlights
    r = guest.put(f"/api/pages/{page['id']}/attachment", json={"source_url": "https://example.org/paper-v2.pdf"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["properties"]["source_url"] == "https://example.org/paper-v2.pdf" and "job" not in body
    assert body["relocation_error"] == "the PDF is not stored on the server"
    assert _props(guest, hl)["pdf_position"]["doc_id"] == storage.UNSTORED


def test_the_manual_run_after_a_skipped_one_reads_the_old_file(guest, monkeypatch):
    # an area over a picture, which the next version draws on its second page
    a = _pdf([[(72, 100, LINE_A), ("image", (72, 200, 172, 300), BLUE)]])
    page_id, _doc, (area,) = _page_with(guest, a, [("", _position(1, {"x1": 70, "y1": 195, "x2": 175, "y2": 305}, area=True))])
    with monkeypatch.context() as m:
        m.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 0)
        _replace(guest, page_id, _pdf([[(72, 100, LINE_A)], [("image", (200, 400, 300, 500), BLUE)]]), "b.pdf")
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
    assert _done(guest, job) == {**NOTHING, "highlights": 1, "moved": 1}
    pos = _props(guest, area)["pdf_position"]
    assert pos["pageNumber"] == 2 and pos["boundingRect"] == pytest.approx({"x1": 198, "y1": 395, "x2": 303, "y2": 505}, abs=2)


def test_a_second_replacement_stops_the_run_for_the_file_before_and_places_for_the_new_one(guest, monkeypatch):
    page_id, _doc, (hl,) = _page_with(guest, _pdf([[(72, 100, LINE_A)]]), [(LINE_A, _position(1, _line_rect(72, 100, LINE_A)))])
    release = _hold_runs(monkeypatch)
    try:
        _v2, body = _replace(guest, page_id, _pdf([[(72, 300, LINE_A)]]), "v2.pdf")
        first = body["job"]
        _wait_running(first["id"])
        v3, body = _replace(guest, page_id, _pdf([[(72, 100, "A new front page")], [(72, 500, LINE_A)]]), "v3.pdf")
        second = body["job"]
        assert second["id"] != first["id"] and second["params"]["doc_id"] == v3
        assert jobs.wait(first["id"])["state"] == "cancelled"
        # a start by hand while the run for the page's file is going answers that run
        r = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
        assert r.status_code == 200 and r.json()["id"] == second["id"]
    finally:
        release.set()
    assert _done(guest, second) == {**NOTHING, "highlights": 1, "moved": 1}
    # read from the first file, which its position still named
    pos = _props(guest, hl)["pdf_position"]
    assert pos["pageNumber"] == 2 and 485 < pos["rects"][0]["y1"] < 505 and "doc_id" not in pos


def test_a_highlight_still_pending_at_the_next_replacement_is_matched_from_the_file_it_was_made_on(guest):
    looks = V3_LOOKS_LIKE_V2

    def version(cite, sentence, *, lower=False):
        lines = [(72, 100, CITE_A)] + ([(72, 114, ADDED)] if lower else []) + [(72, 128 if lower else 114, cite)]
        return _pdf([lines + ([(24, 300, sentence)] if sentence else [])])

    v1_cite = CITED.format(n=3)
    page_id, v1, (cite, gone) = _page_with(guest, version(v1_cite, looks[0]), [
        (v1_cite, _position(1, _line_rect(72, 114, v1_cite))), (looks[0], _position(1, _line_rect(24, 300, looks[0])))])
    # v2: both matched roughly, pending, their prev v1's
    _v2, body = _replace(guest, page_id, version(CITED.format(n=4), looks[1]), "v2.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "approx": 2}
    prev = {hl: _props(guest, hl)["pdf_position"]["prev"] for hl in (cite, gone)}
    assert prev[cite]["doc_id"] == v1 == prev[gone]["doc_id"]
    # v3, before anyone looked: the citation renumbered again a line lower, dashed there against v1's words; the
    # sentence v1 had gone, only v2's guess reads like the one v3 has: hollow at v1's place, with v1's words
    _v3, body = _replace(guest, page_id, version(CITED.format(n=5), looks[2], lower=True), "v3.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "approx": 1, "lost": 1}
    dashed = _props(guest, cite)
    assert dashed["pdf_position"]["prev"] == prev[cite] and dashed["quote"] == CITED.format(n=5)
    assert 128 - SIZE < dashed["pdf_position"]["rects"][0]["y2"] < 128 + SIZE and "doc_id" not in dashed["pdf_position"]
    hollow = _props(guest, gone)
    assert hollow["quote"] == looks[0]
    assert {k: v for k, v in hollow["pdf_position"].items() if k != "closest"} == {
        **hr.reference_place(hollow["pdf_position"], prev[gone], (W, H)), "lost": True}
    assert hollow["pdf_position"]["doc_id"] == v1 and "prev" not in hollow["pdf_position"]
    # v4 prints v1's words again: both found exactly, solid, with v1's wording (the hollow one where it is drawn)
    _v4, body = _replace(guest, page_id, version(v1_cite, looks[0], lower=True), "v4.pdf")
    assert _done(guest, body["job"]) == {**NOTHING, "highlights": 2, "moved": 1, "kept": 1}
    for hl, quote in ((cite, v1_cite), (gone, looks[0])):
        props = _props(guest, hl)
        assert props["quote"] == quote and not {"approx", "match", "prev", "lost", "doc_id"} & set(props["pdf_position"])


def test_a_page_without_highlights_starts_no_job_and_the_manual_start_says_so(guest):
    page_id, _doc, _ids = _page_with(guest, _pdf([[(72, 100, LINE_A)]]), [])
    new_doc = _upload(guest, _pdf([[(72, 100, LINE_B)]]), "v2.pdf")
    assert "job" not in guest.put(f"/api/pages/{page_id}/attachment", json={"doc_id": new_doc}).json()
    r = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
    assert r.status_code == 400 and "highlights" in r.json()["detail"]
    note = guest.post("/api/pages", json={"title": "Just notes"}).json()
    r = guest.post("/api/jobs/relocate-highlights", json={"page_id": note["id"]})
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    assert guest.post("/api/jobs/relocate-highlights", json={"page_id": "nope"}).status_code == 404


def test_the_manual_run_places_by_the_stored_quotes(guest):
    # a highlight whose rects point at an empty line of the page's file (made on another version, long gone)
    pdf = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)]])
    page_id, doc, (hl,) = _page_with(guest, pdf, [(LINE_B, _position(1, _line_rect(72, 300, LINE_B)))])
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
    assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": doc, **DEFAULTS}
    assert _done(guest, job) == {**NOTHING, "highlights": 1, "moved": 1}
    pos = _props(guest, hl)["pdf_position"]
    assert 99 < pos["rects"][0]["y1"] < pos["rects"][0]["y2"] < 119 and "lost" not in pos
    # found where it is: a second run changes nothing
    assert _done(guest, guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()) == {
        **NOTHING, "highlights": 1, "kept": 1}


def test_starting_a_run_by_hand_needs_a_session_but_a_token_s_replacement_starts_one():
    from conftest import account_of, drop_user, fresh_client, login, make_user
    from gamma.integrations import create_token

    ws = make_user("rl_token", "rl-token-pw1")
    try:
        own = login("rl_token", "rl-token-pw1")
        bearer = fresh_client()
        bearer.headers["Authorization"] = f"Bearer {create_token(account_of('rl_token'), ws, 'rl', 1, scope='write')['token']}"
        page_id, _doc, (hl,) = _page_with(own, _pdf([[(72, 100, LINE_A)]]), [(LINE_A, _position(1, _line_rect(72, 100, LINE_A)))])
        r = bearer.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
        assert r.status_code == 403, r.text
        v2 = _upload(own, _pdf([[(72, 300, LINE_A)]]), "v2.pdf")
        r = bearer.put(f"/api/pages/{page_id}/attachment", json={"doc_id": v2})
        assert r.status_code == 200, r.text
        job = r.json()["job"]
        assert job["owner"] == account_of("rl_token")
        assert bearer.get(f"/api/jobs/{job['id']}").status_code == 403  # a token reads no jobs
        assert _done(own, job) == {**NOTHING, "highlights": 1, "moved": 1}
        assert 285 < _props(own, hl)["pdf_position"]["rects"][0]["y1"] < 305
    finally:
        drop_user("rl_token")


# --- the write ------------------------------------------------------------------------------

def _two_versions(guest, monkeypatch, old_lines, new_pages):
    """A page on ``old_lines`` (page 1) with a highlight per line, its PDF
    replaced by ``new_pages`` without a run: ``(ws, page_id, new doc_id,
    [highlight ids])``, for a test to call ``relocate`` itself."""
    page_id, _doc, ids = _page_with(guest, _pdf([[(72, y, t) for y, t in old_lines]]),
                                    [(t, _position(1, _line_rect(72, y, t))) for y, t in old_lines])
    with monkeypatch.context() as m:
        m.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 0)
        new_doc, _body = _replace(guest, page_id, _pdf(new_pages))
    return workspace_of(guest_name()), page_id, new_doc, ids


def _on_first_match(action):
    """A progress report that does ``action()`` once, when the run starts matching."""
    done = []

    def report(**kw):
        if kw.get("phase") == "matching" and not done:
            done.append(True)
            action()

    return report


def test_a_highlight_a_person_changes_while_the_run_matches_keeps_what_they_wrote(guest, monkeypatch):
    ws, page_id, new_doc, (hl_a, hl_b, hl_c) = _two_versions(guest, monkeypatch, [(100, LINE_A), (114, LINE_B), (128, LINE_C)],
                                                             [[(72, 100, "A new front page")], [(72, 100, LINE_A), (72, 114, LINE_B)]])
    # B would move to page 2 and C (gone from the file) be marked lost; the person moves both by hand meanwhile
    mine = {hl_b: {"pdf_position": _position(2, _line_rect(72, 114, LINE_B)), "quote": "the person's selection"},
            hl_c: {"pdf_position": _position(2, _line_rect(72, 100, LINE_A)), "quote": "another one"}}

    def person_moves():
        for hl, props in mine.items():
            assert guest.put(f"/api/blocks/{hl}", json={"properties": props}).status_code == 200

    result = hr.relocate(ws, page_id, new_doc, actor="tester", progress=_on_first_match(person_moves))
    assert result == {**NOTHING, "highlights": 3, "moved": 1, "changed": 2}
    for hl, props in mine.items():
        stored = _props(guest, hl)
        assert stored["pdf_position"] == props["pdf_position"] and stored["quote"] == props["quote"]
    assert _props(guest, hl_a)["pdf_position"]["pageNumber"] == 2


def test_a_highlight_deleted_while_the_run_matches_is_left_out(guest, monkeypatch):
    ws, page_id, new_doc, (hl_a, hl_b) = _two_versions(guest, monkeypatch, [(100, LINE_A), (114, LINE_B)],
                                                       [[(72, 100, "A new front page")], [(72, 100, LINE_A), (72, 114, LINE_B)]])

    def person_deletes():
        assert guest.delete(f"/api/blocks/{hl_b}").status_code == 200

    result = hr.relocate(ws, page_id, new_doc, actor="tester", progress=_on_first_match(person_deletes))
    assert result == {**NOTHING, "highlights": 2, "moved": 1, "changed": 1}
    assert _props(guest, hl_a)["pdf_position"]["pageNumber"] == 2
    assert guest.get(f"/api/blocks/{hl_b}").status_code == 404


def test_a_page_deleted_while_its_run_matches_is_not_written_and_says_how_to_go_on(guest, monkeypatch):
    ws, page_id, new_doc, ids = _two_versions(guest, monkeypatch, [(100, LINE_A), (114, LINE_B)],
                                              [[(72, 100, "A new front page")], [(72, 100, LINE_A), (72, 114, LINE_B)]])
    before = {hl: _props(guest, hl)["pdf_position"] for hl in ids}

    def person_trashes():
        assert guest.delete(f"/api/blocks/{page_id}").status_code == 200

    with pytest.raises(ValueError, match="deleted.*Find highlights in this PDF"):
        hr.relocate(ws, page_id, new_doc, actor="tester", progress=_on_first_match(person_trashes))
    assert guest.post(f"/api/trash/{page_id}/restore").status_code == 200
    assert {hl: _props(guest, hl)["pdf_position"] for hl in ids} == before
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
    assert _done(guest, job) == {**NOTHING, "highlights": 2, "moved": 2}


def test_a_run_writes_its_batches_in_one_transaction(guest, monkeypatch):
    ws, page_id, new_doc, ids = _two_versions(guest, monkeypatch, [(100, LINE_A), (114, LINE_B)],
                                              [[(72, 100, "A new front page")], [(72, 100, LINE_A), (72, 114, LINE_B)]])
    monkeypatch.setattr(hr, "MAX_OPS", 1)
    assert hr.relocate(ws, page_id, new_doc, actor="tester") == {**NOTHING, "highlights": 2, "moved": 2}
    assert [_props(guest, hl)["pdf_position"]["pageNumber"] for hl in ids] == [2, 2]


def test_a_run_writes_nothing_when_the_pdf_changed_again_meanwhile(guest):
    ws = workspace_of(guest_name())
    pdf = _pdf([[(72, 100, LINE_A)]])
    page_id, doc, (hl,) = _page_with(guest, pdf, [(LINE_A, _position(1, _line_rect(72, 300, LINE_A)))])
    _other, body = _replace(guest, page_id, _pdf([[(72, 100, LINE_B)]]), "other.pdf")
    assert _done(guest, body["job"])["lost"] == 1  # the replacement's own run, ended before anything is read
    before = _props(guest, hl)
    assert before["pdf_position"]["lost"] is True and before["pdf_position"]["doc_id"] == doc
    with pytest.raises(ValueError, match="changed again"):
        hr.relocate(ws, page_id, doc, actor="tester")
    assert _props(guest, hl) == before
