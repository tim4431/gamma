"""Clickable PDF citations (docs/dev/pdf_citations.md): the page labels the
model cites by, and where a citation's quote is found
(``highlight_relocate.cite``, ``GET /api/pages/{id}/citation``): exactly, past
a hyphen inside a word or a reference mark the quote leaves out, on the page
beside the named one, roughly with its match, ambiguous or missing; who may
ask."""

import pymupdf
import pytest

from conftest import doc_props, login, make_page, make_user
from gamma import ai_context, pdf_text
from gamma import highlight_relocate as hr

W, H, SIZE = 612, 792, 11


def test_physical_page_labels_include_blank_pages_and_start_page(monkeypatch):
    pages = ["first", "", "third", "fourth"]
    monkeypatch.setattr(pdf_text, "iter_page_texts",
                        lambda src, start_page=1: iter(pages[start_page - 1:]))
    text, count = pdf_text.extract_text_pages("unused", 1000, label_pages=True)
    assert text == "[PDF page 1]\nfirst\n\n[PDF page 3]\nthird\n\n[PDF page 4]\nfourth"
    assert count == 4
    assert pdf_text.extract_text("unused", 1000, start_page=3, label_pages=True).startswith("[PDF page 3]")
    assert pdf_text.extract_text("unused", 1000) == "first\n\nthird\n\nfourth"


def test_continuation_repeats_page_label_without_changing_offsets(monkeypatch):
    monkeypatch.setattr(ai_context, "pdf_path", lambda *args: "unused")
    monkeypatch.setattr(pdf_text, "iter_page_texts",
                        lambda src, start_page=1: iter(["A" * 100, "B" * 100]))
    text, next_offset, _ = ai_context.pdf_excerpt("ws", "doc", 20, offset=45)
    assert text == "[PDF page 1; continued]\n" + "A" * 20
    assert next_offset == 65
    text, _, _ = ai_context.pdf_excerpt("ws", "doc", 20, offset=118)
    assert text.startswith("[PDF page 2; continued]\n")
    assert "PDF page 1" not in text


def test_selection_windows_label_each_physical_page():
    assert ai_context._join_upto(["first", "second", "third"], 1, 1000) == (
        "[PDF page 2]\nsecond\n\n[PDF page 3]\nthird")


# --- where a quote is found ---------------------------------------------------------

def _line(*parts, y=700.0):
    """One MuPDF line of glyphs (``GlyphPages.page``'s shape) from
    ``parts``: a string set on the line, each character 6 wide and 10
    tall, or ``("sup", text)`` set as a superscript after it, 4 wide, 7
    tall and raised by 4; a boxless break after the line."""
    out, x = [], 72.0
    for part in parts:
        sup = isinstance(part, tuple)
        for ch in part[1] if sup else part:
            box = (x, y + 2, x + 4, y + 9) if sup else (x, y - 2, x + 6, y + 8)
            out.append((ch, box))
            x = box[2]
    return out + [("\n", (0.0, 0.0, 0.0, 0.0))]


def _found(page, quote):
    """``find_passage``'s match of ``quote`` on the one ``page``, with the
    text it covers."""
    m = hr.find_passage({1: page}, hr.normalize_quote(quote), closest=hr.CLOSEST_MIN)
    return m, page.text[m.start:m.end] if m else ""


def test_a_hyphen_inside_a_word_may_be_missing_where_a_line_s_end_joined_it():
    # "Carr-Purcell-" + "Meiboom-Gill" reads "carr-purcellmeiboom-gill": the
    # page cannot tell the compound's hyphen from a word broken at the edge.
    page = hr.PageText(_line("the Carr-Purcell-") + _line("Meiboom-Gill sequence of pi-", y=688)
                       + _line("pulses is short.", y=676))
    assert "purcellmeiboom" in page.text and "pipulses" in page.text
    m, text = _found(page, "the Carr-Purcell-Meiboom-Gill sequence of pi-pulses is short.")
    assert m.confidence == 1.0 and text == "the carr-purcellmeiboom-gill sequence of pipulses is short."
    # A hyphen the page kept still matches, and one between two letters
    # alone or beside a digit is no word's: "x-y" is not "xy".
    assert _found(hr.PageText(_line("an x-ray of it")), "an x-ray of it")[0].confidence == 1.0
    page = hr.PageText(_line("the term xy vanishes at 5370 K here"))
    for quote in ("the term x-y vanishes", "vanishes at 53-70 K"):
        m, _text = _found(page, quote)
        assert m is None or m.confidence < 1.0, quote


def test_a_reference_mark_the_quote_leaves_out_is_still_its_exact_copy():
    page = hr.PageText(_line("We call it a CPMG sequence", ("sup", "159,160"), ". Then a long free evolution",
                             ("sup", "78"), ", as before."))
    m, text = _found(page, "We call it a CPMG sequence.")
    assert m.confidence == 1.0 and text == "we call it a cpmg sequence159,160."
    # without its period the copy ends at the word, the mark left out
    assert _found(page, "We call it a CPMG sequence")[1] == "we call it a cpmg sequence"
    # a mark inside the quote is covered with the passage
    m, text = _found(page, "Then a long free evolution, as before.")
    assert m.confidence == 1.0 and text == "then a long free evolution78, as before."
    # quoted with its marks, the plain copy
    assert _found(page, "a CPMG sequence159,160.")[0].confidence == 1.0


def test_only_a_raised_smaller_number_after_a_word_is_a_reference_mark():
    # on the baseline it is the text's own number; a single raised digit is
    # as often a power (x2, cm3); a word of two letters is a unit (Hz10)
    for parts, quote in (
        (("A value of 2021", "."), "A value of."),
        (("the dose in grams", ("sup", "2"), "."), "the dose in grams."),
        (("a field in Hz", ("sup", "10"), "."), "a field in Hz."),
        (("a sequence", ("sup", "158"), " is long"), "a sequence is long"),  # no punctuation after it
    ):
        assert hr.PageText(_line(*parts)).without_marks() is None, parts
        m, _text = _found(hr.PageText(_line(*parts)), quote)
        assert m is None or m.confidence < 1.0, quote


def _pdf(*pages, sup=None):
    """A PDF of ``pages``, each a list of lines set from 72 pt down, 20 pt
    apart, in Helvetica at SIZE; ``sup`` (page, line, after, mark) sets
    ``mark`` as a superscript after the text ``after`` of that line."""
    doc = pymupdf.open()
    for n, lines in enumerate(pages, start=1):
        page = doc.new_page(width=W, height=H)
        for i, text in enumerate(lines):
            y = 100 + 20 * i
            if sup and sup[:2] == (n, i):
                head, tail = text.split(sup[2], 1)
                head += sup[2]
                x = 72 + pymupdf.get_text_length(head, fontname="helv", fontsize=SIZE)
                page.insert_text((72, y), head, fontname="helv", fontsize=SIZE)
                page.insert_text((x, y - 4), sup[3], fontname="helv", fontsize=7)
                x += pymupdf.get_text_length(sup[3], fontname="helv", fontsize=7)
                page.insert_text((x, y), tail, fontname="helv", fontsize=SIZE)
            else:
                page.insert_text((72, y), text, fontname="helv", fontsize=SIZE)
    return doc.tobytes()


PAPER = _pdf(
    ["The qubit relaxes within a few microseconds of its preparation.",
     "Its coherence is limited by two-level systems in the oxide."],
    ["Dynamical decoupling suppresses low-frequency noise very well.",
     "We measured 53 atoms in the trap at a temperature of 4 K (Methods).",
     "A repeated sentence stands here. A repeated sentence stands here."],
    sup=(2, 0, "very well", "78"))


def test_cite_answers_the_page_the_match_and_the_rects_as_fractions():
    answer = hr.cite(PAPER, "The qubit relaxes within a few microseconds of its preparation.", 1)
    assert (answer["status"], answer["page"], answer["match"]) == ("matched", 1, 1.0)
    [(x1, y1, x2, y2)] = answer["rects"]
    assert x1 == pytest.approx(72 / W, abs=0.002) and 0 < y1 < 100 / H < y2 < 0.15 and x2 > 0.5
    # a superscript mark MuPDF reads from the file, left out of the quote
    answer = hr.cite(PAPER, "Dynamical decoupling suppresses low-frequency noise very well.", 2)
    assert (answer["status"], answer["match"]) == ("matched", 1.0)


def test_cite_looks_on_the_pages_beside_the_named_one():
    # a model naming the printed page, one off the physical one
    answer = hr.cite(PAPER, "Dynamical decoupling suppresses low-frequency noise", 1)
    assert (answer["status"], answer["page"], answer["match"]) == ("matched", 2, 1.0)
    # the named page wins a tie, and a page two away is not looked at
    assert hr.cite(_pdf(["Same words on both pages here."], ["Same words on both pages here."]),
                   "Same words on both pages here.", 2)["page"] == 2
    assert hr.cite(_pdf(["Only here is it."], ["x"], ["y"]), "Only here is it.", 3) == {"status": "missing"}


def test_cite_matches_roughly_and_says_how_alike():
    for quote in ("We measured 53 atoms in the trap at a temperature of 4 K.",  # an editorial pointer left out
                  "We measured 70 atoms in the trap at a temperature of 4 K (Methods).",  # a number misquoted
                  "We measured 53 atom in the trap at a temprature of 4 K (Methods)."):
        answer = hr.cite(PAPER, quote, 2)
        assert answer["status"] == "matched" and hr.ROUGH_APPLY <= answer["match"] <= 0.99, (quote, answer)
    assert hr.cite(PAPER, "An unrelated claim about superconducting resonators and their loss.", 2) == {
        "status": "missing"}


def test_two_places_that_read_as_alike_are_ambiguous():
    assert hr.cite(PAPER, "A repeated sentence stands here.", 2) == {"status": "ambiguous", "page": 2}
    # roughly as well: a typo reads like both copies alike
    assert hr.cite(PAPER, "A repeated sentense stands here.", 2) == {"status": "ambiguous", "page": 2}
    # the agent's highlight takes the first, as before
    position, spelled, confidence = hr.place_quote(PAPER, "A repeated sentence stands here.", 2)
    assert position["pageNumber"] == 2 and confidence == 1.0


def test_cite_refuses_a_page_the_file_does_not_have():
    with pytest.raises(ValueError, match="no page 9"):
        hr.cite(PAPER, "The qubit relaxes", 9)


# --- the route ------------------------------------------------------------------------

USER = "cite_tim"


@pytest.fixture(scope="module")
def tim(client):
    make_user(USER, "pw")
    c = login(USER, "pw")
    up = c.post("/api/uploads", files={"file": ("paper.pdf", PAPER, "application/pdf")})
    assert up.status_code == 200, up.text
    c.paper = make_page(c, "The paper", properties=doc_props(up.json()["doc_id"]))["id"]
    c.notes = make_page(c, "Notes")["id"]
    yield c
    c.close()


def _cite(c, page_id, quote, pdf_page, **params):
    return c.get(f"/api/pages/{page_id}/citation", params={"pdf_page": pdf_page, "quote": quote, **params})


def test_the_route_answers_a_member(tim):
    r = _cite(tim, tim.paper, "Its coherence is limited by two-level systems in the oxide.", 1)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["page"], r.json()["match"]) == ("matched", 1, 1.0)
    assert _cite(tim, tim.paper, "Nothing like this sentence is in the paper at all.", 1).json() == {
        "status": "missing"}


def test_the_route_refuses_what_it_cannot_look_for(tim):
    for quote, page, page_id in (("", 1, None), (" " * 3, 1, None), ("x" * (hr.CITE_MAX_QUOTE + 1), 1, None),
                                 ("The qubit relaxes", 9, None), ("The qubit relaxes", 1, "notes")):
        r = _cite(tim, getattr(tim, page_id) if page_id else tim.paper, quote, page)
        assert r.status_code == 400, (quote[:20], page, page_id, r.text)
    assert _cite(tim, "no-such-page", "The qubit relaxes", 1).status_code == 404


def test_a_share_reads_the_pages_it_reaches_and_no_other(tim, anon):
    token = tim.post(f"/api/share/{tim.paper}").json()["token"]
    r = _cite(anon, tim.paper, "The qubit relaxes within a few microseconds", 1, share=token)
    assert r.status_code == 200 and r.json()["status"] == "matched", r.text
    other = tim.post(f"/api/share/{tim.notes}").json()["token"]
    assert _cite(anon, tim.paper, "The qubit relaxes", 1, share=other).status_code == 403
    assert _cite(anon, tim.paper, "The qubit relaxes", 1).status_code == 401
