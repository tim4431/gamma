"""Highlights after a PDF is replaced (gamma/highlight_relocate.py,
PUT /api/pages/{id}/attachment's ``relocate-highlights`` job): each one is
found again in the new file by its text and moved there; the ones whose
text is gone are marked ``lost``; an area highlight follows the text or
the picture inside it, or its page's other highlights."""

import io
import threading
import time

import pymupdf
import pytest
from conftest import guest_name, workspace_of

from gamma import highlight_relocate as hr
from gamma import jobs
from gamma.pdf_text import GlyphPages, has_box

W, H = 612, 792
SIZE = 11


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


def _measured(pdf_bytes, page_no, text):
    """The position the relocation gives ``text`` where page ``page_no`` of
    a file draws it: a highlight made right on it."""
    with hr.Doc(pdf_bytes) as doc:
        page = doc.text(page_no)
        at = page.text.index(hr.normalize_quote(text))
        boxes = hr.rects_for_span(page, at, at + len(hr.normalize_quote(text)))
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
        needle, (before, after), anchor = hr._old_passage(doc, across)
        assert needle == "the end of a line next words follow" and anchor is None
        assert before.endswith("here we reach ") and after.startswith(" on the line below")
        hyphen = _position(1, [_part_rect(72, 200, h1, "entangle-"), _part_rect(72, 214, h2, "ment of two")])
        assert hr._old_passage(doc, hyphen)[0] == "entanglement of two"
        assert hr._old_passage(doc, _position(1, _line_rect(72, 400, l1))) is None  # nothing under it


def test_a_highlight_across_two_columns_skips_the_caption_drawn_between_them():
    col1 = ["Earlier work used slow methods to", "prepare the state, while in this", "work we propose and test a"]
    caption = ["Figure 1: A long caption that runs under both of the columns and that the file",
               "draws between the two columns in its content stream, so that it reads in between"]
    col2 = ["new method for entangle-", "ment of two atoms in a trap with", "light at a fixed detuning."]
    old = _pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(col1)] + [(72, 600 + 14 * i, t) for i, t in enumerate(caption)]
                + [(320, 100 + 14 * i, t) for i, t in enumerate(col2)]])
    with GlyphPages(old) as glyphs:
        assert _lines_of(glyphs.page(1))[2:5] == [col1[2], caption[0], caption[1]]  # the caption between the columns
    new = _pdf([[(72, 300, "Earlier work used slow methods to prepare the state, while in this work we propose"),
                 (72, 314, "and test a new method for entanglement of two atoms in a trap with light at a fixed"),
                 (72, 328, "detuning."), (72, 600, caption[0]), (72, 614, caption[1])]])
    rects = [_part_rect(72, 128, col1[2], "test a"), _line_rect(320, 100, col2[0]), _part_rect(320, 114, col2[1], "ment of two atoms")]
    quote = "test a new method for entangle-\nment of two atoms"
    items = [("hl", {"pdf_position": {**_position(1, rects), "doc_id": "v1"}, "quote": quote})]
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        placed = hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)
    assert placed["kinds"] == {"hl": "moved"}
    op = placed["ops"]["hl"]
    assert "quote" not in op  # found exactly: the highlight's words are the file's
    rects = op["pdf_position"]["rects"]
    assert len(rects) == 1 and 299 < rects[0]["y1"] < 319  # all on the new file's second line
    assert rects[0]["x1"] == pytest.approx(72 + _width("and "), abs=0.6)


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
    assert hr.find_passage(pages, "atoms are cooled in a trap", hint_page=1) == hr.Match(2, 9, 35, 0.0)
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


def test_the_word_by_word_search_allows_a_few_changed_words_but_never_a_number():
    old = "the cavity is driven at a frequency slightly below the atomic resonance so that the atoms are cooled"
    pages = _pages("preamble. the cavity is pumped at a frequency slightly below the atomic resonance so that atoms are cooled. end.",
                   "the cavity is driven at a frequency slightly below the atomic resonance so that the 2 atoms are heated")
    found = hr.find_passage(pages, old, hint_page=1)
    assert found is not None and found.page == 1
    assert 0 < found.cost <= 3 and pages[1].text[found.start:found.end] == old.replace("driven", "pumped").replace("that the atoms", "that atoms")
    # a changed number is another statement
    assert hr.find_passage(_pages("the sample was held at 4 k for an hour before the run"), "the sample was held at 5 k for an hour before the run") is None
    # but the copyedited punctuation around a number or a variable is a quarter, as around any word
    for old_text, new_text in (("in Fig. 2 we show the spectrum of the doublet", "in Fig. 2, we show the spectrum of the doublet"),
                               ("as shown in Fig. 3, the gate voltage tunes the dot", "as shown in Fig. 3. The gate voltage tunes the dot"),
                               ("as reported before [12] and confirmed by later work", "as reported before 12 and confirmed by later work"),
                               ("the energy follows from Eq. 4 for small fields", "the energy follows from Eq. (4) for small fields"),
                               ("the sample was cooled to 4 K and measured at once", "the sample was cooled to 4 K, and measured at once"),
                               ("the coordinates x, y, and z of each atom", "the coordinates x, y and z of each atom")):
        page = _pages(new_text)
        assert hr.find_passage(page, hr.normalize_quote(old_text)) == hr.Match(1, 0, len(page[1].text), 0.25), old_text
    # a prime, a factorial or a negation dropped is not punctuation
    for old_text, new_text in (("the derivative x' of the coordinate is small", "the derivative x of the coordinate is small"),
                               ("the number of states grows as n! for large n", "the number of states grows as n for large n"),
                               ("the atoms are not cooled by the probe beam", "the atoms are cooled by the probe beam")):
        assert hr.find_passage(_pages(new_text), hr.normalize_quote(old_text)) is None, old_text
    # too many edits: not the same passage
    assert hr.find_passage(_pages("one two three four five six seven eight nine ten"), "one dos tres cuatro five six seven eight nine ten") is None
    # a passage under four words is found exactly or not at all
    assert hr.find_passage(_pages("the atom's trap"), "the atoms trap") is None
    assert hr.find_passage(_pages("in the atom's trap today"), "in the atoms trap today") == hr.Match(1, 0, 24, 0.5)


def test_a_dropped_first_word_does_not_pull_in_the_word_before():
    needle = "moreover the cavity is driven below the atomic resonance"
    pages = _pages("the atoms are held in a trap. the cavity is driven below the atomic resonance so that they cool")
    found = hr.find_passage(pages, needle)
    assert found.cost == 1 and pages[1].text[found.start:found.end] == needle.removeprefix("moreover ")
    # on a page where that passage starts a line, it covers that line alone
    line1, line2 = "The atoms are held in a trap.", "The cavity is driven below the atomic resonance so that"
    new = _pdf([[(72, 100, line1), (72, 114, line2)]])
    items = [("hl", {"pdf_position": {**_position(1, _line_rect(72, 300, "x")), "doc_id": "gone"},
                     "quote": "Moreover the cavity is driven below the atomic resonance"})]
    with hr.Doc(new) as new_doc:
        op = hr.place_all(new_doc, items)["ops"]["hl"]
    assert len(op["pdf_position"]["rects"]) == 1 and 99 < op["pdf_position"]["rects"][0]["y1"] < 119
    assert op["quote"] == "The cavity is driven below the atomic resonance"


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
    assert placed["kinds"] == {"hl": "moved", "copy": "kept", "bare": "moved"}
    assert placed["ops"]["hl"]["pdf_position"]["pageNumber"] == 1 and placed["ops"]["hl"]["quote"] == "the atoms are cooled inside the trap"
    assert placed["ops"]["bare"]["pdf_position"]["pageNumber"] == 2 and "quote" not in placed["ops"]["bare"]


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
    assert _counts(placed) == {"highlights": 7, "moved": 4, "kept": 1, "lost": 1, "approx": 1}
    assert placed["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "lost", "areaCap": "moved", "areaPic": "moved",
                               "areaRide": "approx", "hlKept": "kept"}
    ops = placed["ops"]
    assert "hlKept" not in ops
    # the exact passage: on page 2 now, at the line's place; a new position names no file
    a = ops["hlA"]["pdf_position"]
    assert a["pageNumber"] == 2 and "lost" not in a and "doc_id" not in a and "quote" not in ops["hlA"]
    assert a["rects"][0]["x1"] == pytest.approx(72, abs=0.6) and 85 < a["rects"][0]["y1"] < a["rects"][0]["y2"] < 105
    # the reworded one: found by the word-by-word search, its quote now the new wording
    b = ops["hlB"]["pdf_position"]
    assert b["pageNumber"] == 2 and 99 < b["rects"][0]["y1"] < b["rects"][0]["y2"] < 119
    assert ops["hlB"]["quote"] == "The spectrum shows a triplet at the resonant frequency"
    # the sentence that is gone: where it was, marked lost, still naming the file its rects belong to
    c = ops["hlC"]["pdf_position"]
    assert c["lost"] is True and c["pageNumber"] == 1 and c["rects"] == items[2][1]["pdf_position"]["rects"]
    assert c["doc_id"] == "v1" and "quote" not in ops["hlC"]
    # the caption's area: around the caption on page 2, as big as before, the picture inside it
    cap = ops["areaCap"]["pdf_position"]
    assert cap["area"] is True and cap["pageNumber"] == 2 and "approx" not in cap and "doc_id" not in cap
    assert cap["boundingRect"] == pytest.approx({"x1": 80, "y1": 320, "x2": 310, "y2": 450}, abs=2)
    # the picture's area: where the same picture is drawn now
    pic = ops["areaPic"]["pdf_position"]
    assert pic["pageNumber"] == 3 and "approx" not in pic
    assert pic["boundingRect"] == pytest.approx({"x1": 198, "y1": 395, "x2": 303, "y2": 505}, abs=2)
    # the area over nothing: carried to the page its neighbours went to, shifted by the median of their shifts (two
    # of the three did not move on the page), and said to be a guess
    ride = ops["areaRide"]["pdf_position"]
    assert ride["pageNumber"] == 2 and ride["approx"] is True and "doc_id" not in ride
    assert ride["boundingRect"] == pytest.approx({"x1": 400, "y1": 600, "x2": 500, "y2": 700}, abs=2)
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


def test_a_highlight_kept_in_place_takes_the_new_spelling_of_its_words():
    line = "We analyse the spectrum of the doublet at the resonance"
    respelled = line.replace("analyse", "analyze")  # as wide in Helvetica: the highlight does not move
    pos = {**_position(1, _line_rect(72, 100, line)), "doc_id": "v1"}
    with hr.Doc(_pdf([[(72, 100, line)]])) as old_doc, hr.Doc(_pdf([[(72, 100, respelled)]])) as new_doc:
        placed = hr.place_all(new_doc, [("hl", {"pdf_position": pos, "quote": line})], read_from={"v1": old_doc}.get)
    assert _counts(placed) == {"highlights": 1, "moved": 0, "kept": 1, "lost": 0, "approx": 0}
    assert placed["ops"] == {"hl": {"pdf_position": _position(1, _line_rect(72, 100, line)), "quote": respelled}}


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

def _ride(old, new):
    """place_all over three text highlights made on ``old``'s lines A, B, C
    (naming that file) and an area over nothing on its first page."""
    items = [(f"hl{k}", {"pdf_position": {**_measured(old, 1, t), "doc_id": "v1"}, "quote": t})
             for k, t in zip("ABC", (LINE_A, LINE_B, LINE_C))]
    items.append(("area", {"pdf_position": _position(1, {"x1": 400, "y1": 600, "x2": 500, "y2": 700}, area=True)}))
    with hr.Doc(old) as old_doc, hr.Doc(new) as new_doc:
        return hr.place_all(new_doc, items, read_from={"v1": old_doc}.get)


def test_an_area_rides_along_only_when_its_page_s_highlights_all_moved_alike():
    old = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B), (72, 128, LINE_C)]])

    def page(*lines):
        return [(72, y, t) for y, t in lines]

    # A and B stayed, C went to the next page: the area has no one page to go to
    placed = _ride(old, _pdf([page((100, LINE_A), (114, LINE_B)), page((100, LINE_C))]))
    assert placed["kinds"] == {"hlA": "kept", "hlB": "kept", "hlC": "moved", "area": "kept"} and "area" not in placed["ops"]
    # A and B stayed, C went far down the page: they do not agree
    placed = _ride(old, _pdf([page((100, LINE_A), (114, LINE_B), (365, LINE_C))]))
    assert placed["kinds"]["area"] == "kept" and "area" not in placed["ops"]
    # all three went to the next page, 40 points lower: the area goes with them, by just as much
    placed = _ride(old, _pdf([page((100, "A new front page")), page((140, LINE_A), (154, LINE_B), (168, LINE_C))]))
    assert placed["kinds"]["area"] == "approx"
    ride = placed["ops"]["area"]["pdf_position"]
    assert ride["pageNumber"] == 2 and ride["approx"] is True
    assert ride["boundingRect"] == pytest.approx({"x1": 400, "y1": 640, "x2": 500, "y2": 740}, abs=0.05)
    # all three went so far down the page that the area would leave it: it stays
    placed = _ride(old, _pdf([page((700, LINE_A), (714, LINE_B), (728, LINE_C))]))
    assert placed["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "moved", "area": "kept"} and "area" not in placed["ops"]
    # all three on the next page, but shifted by more than RIDE_SPREAD apart
    placed = _ride(old, _pdf([page((100, "A new front page")), page((120, LINE_A), (134, LINE_B), (400, LINE_C))]))
    assert placed["kinds"]["area"] == "kept" and "area" not in placed["ops"]


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
        # Find highlights again on the same file: the inset under the estimate is found where it is, which
        # confirms nothing, so the area stays as it was, still an estimate
        items = applied(items, first)
        again = hr.place_all(v2_doc, items)
        assert again["kinds"] == {"hlA": "kept", "hlB": "kept", "hlC": "kept", "area": "kept"} and again["ops"] == {}
        # the next version, everything 30 points lower: the area follows the inset, and is still an estimate
        stamped = [(bid, {**props, "pdf_position": {**props["pdf_position"], "doc_id": "v2"}}) for bid, props in items]
        later = hr.place_all(v3_doc, stamped, read_from={"v2": v2_doc}.get)
    assert later["kinds"] == {"hlA": "moved", "hlB": "moved", "hlC": "moved", "area": "approx"}
    assert _counts(later) == {"highlights": 4, "moved": 3, "kept": 0, "lost": 0, "approx": 1}
    moved = later["ops"]["area"]["pdf_position"]
    assert moved["approx"] is True and "doc_id" not in moved
    assert moved["boundingRect"] == pytest.approx({"x1": 400, "y1": 670, "x2": 500, "y2": 770}, abs=0.05)


def test_an_area_with_nothing_to_go_by_stays():
    pdf = _pdf([[(72, 100, LINE_A)], [(72, 100, LINE_B)]])
    with hr.Doc(pdf) as doc:
        placed = hr.place_all(doc, [("area", {"pdf_position": _position(1, {"x1": 300, "y1": 400, "x2": 400, "y2": 500}, area=True)})])
    assert placed["kinds"] == {"area": "kept"} and placed["ops"] == {}


# --- the cost of the search ---------------------------------------------------------------

def test_the_pruned_word_by_word_search_finds_what_aligning_every_seed_window_finds(monkeypatch):
    texts = [["The cavity is driven at a frequency slightly below the atomic resonance, so that",
              "the atoms are cooled; the sample was held at 4 K for an hour before the run began.",
              "We then measured the spectrum of the doublet with a narrow probe laser."],
             ["In a second run the cavity was pumped at a frequency far below the resonance",
              "and the atoms were heated instead, as the spectrum of the triplet shows."],
             ["Nothing here reads like the rest of the paper at all, said the referee.",
              "We analyze the color and behavior of the center line, then optimize the modeled spectrum"]]
    pages = _texts(_pdf([[(72, 100 + 14 * i, t) for i, t in enumerate(lines)] for lines in texts]))

    def unpruned(page, words):
        n = len(words)
        if n < hr.FUZZY_MIN_WORDS or not page.word_texts:
            return []
        budget = hr._budget(n)
        out = []
        for lo, hi in hr._seed_windows(page, words, budget):
            cost, a, b = hr._align(words, page.word_texts, lo, hi)
            if cost <= budget and b > a:
                out.append((cost, *hr._trimmed(page, page.word_starts[a], page.word_ends[b - 1], words)))
        return out

    needles = {
        "edited": "the cavity is pumped at a frequency slightly below the atomic resonance so that the atoms are cooled",
        "gone": "an entirely different sentence about superconducting qubits and their coherence times",
        "punctuation": "the atoms are cooled, the sample was held at 4 K for an hour before the run",
        "digits": "the sample was held at 5 K for an hour before the run began",
        # five words spelled otherwise, a half each: the whole budget of fifteen words, five of them not on the page
        "respelled": "we analyse the colour and behaviour of the centre line, then optimise the modeled spectrum",
    }
    found = set()
    for name, needle in needles.items():
        words = hr.normalize_quote(needle).split()
        for n, page in pages.items():
            expected = unpruned(page, words)
            assert hr._fuzzy_on_page(page, words) == expected, (name, n)
            if expected:
                found.add(name)
    assert found == {"edited", "punctuation", "respelled"}
    # the needle that is nowhere is not aligned at all
    aligned = []
    real = hr._align
    monkeypatch.setattr(hr, "_align", lambda *a, **kw: aligned.append(a) or real(*a, **kw))
    words = hr.normalize_quote(needles["gone"]).split()
    assert all(hr._fuzzy_on_page(page, words) == [] for page in pages.values()) and aligned == []


def test_a_stop_lands_within_a_page_of_the_word_by_word_search(monkeypatch):
    lines = [(72, 100 + 14 * i, f"Line {i} of a page that says little of any interest") for i in range(20)]
    pdf = _pdf([lines] * 40)
    searched = []
    real = hr._fuzzy_on_page
    monkeypatch.setattr(hr, "_fuzzy_on_page", lambda page, words: searched.append(page) or real(page, words))

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

NOTHING = {"highlights": 0, "moved": 0, "kept": 0, "lost": 0, "approx": 0, "changed": 0}


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
    assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": new_doc}
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


def test_a_replacement_names_each_highlight_s_file_and_says_when_its_run_cannot_start(guest, monkeypatch):
    v1 = _pdf([[(72, 100, LINE_A), (72, 114, LINE_B)]])
    page_id, doc1, (hl_a, hl_b) = _page_with(guest, v1, [
        (LINE_A, _position(1, _line_rect(72, 100, LINE_A))),
        # measured on a file before this one, as a run that never wrote leaves it
        (LINE_B, {**_position(1, _line_rect(72, 114, LINE_B)), "doc_id": "an-older-file"})])
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
        r = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id})
        assert r.status_code == 429, r.text
    # by hand later: A read from the file its rects name, B (whose file is not stored) by its quote
    job = guest.post("/api/jobs/relocate-highlights", json={"page_id": page_id}).json()
    assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": doc2}
    assert _done(guest, job) == {**NOTHING, "highlights": 2, "moved": 2}
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
    assert _props(guest, hl)["pdf_position"]["doc_id"] == hr.UNSTORED
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
    assert _props(guest, hl)["pdf_position"]["doc_id"] == hr.UNSTORED


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
    assert job["params"] == {"page_id": page_id, "title": "Replaced paper", "doc_id": doc}
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
