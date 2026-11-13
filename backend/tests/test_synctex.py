"""The SyncTeX reader (gamma/feedback/synctex.py, docs/dev/feedback.md
"SyncTeX") on real builds of TeX Live 2024 (samples/synctex/README.md): the
truth corpus, where every word names its own file and line, built with
pdfTeX, XeTeX and LuaTeX; the paper's figure, equation, page number and a
passage, a mark in the margin and a page past the end; ``view`` against
fixed boxes; the format's edges as hand-written snippets; a nesting too
deep and a file too large; and ``load``, which keeps a file by its stat
and holds it open no longer."""

import gzip
import json
import os
from pathlib import Path

import pytest

from gamma.feedback import synctex

SAMPLES = Path(__file__).parent / "samples" / "synctex"
CASES = json.loads((SAMPLES / "truth" / "cases.json").read_text(encoding="utf-8"))
PLACES = json.loads((SAMPLES / "paper" / "places.json").read_text(encoding="utf-8"))
ENGINES = ("pdftex", "xetex", "luatex")
LINE = [(72, 128, 540, 140)]        # the edges' one line of text, in bp
Y = 136                             # on its baseline


def _names(syn) -> dict:
    return {tag: path.rsplit("/", 1)[-1] for tag, path in syn.inputs.items()}


def _tags(syn) -> dict:
    return {path.rsplit("/", 1)[-1]: tag for tag, path in syn.inputs.items()}


def _named(syn, ranked) -> list:
    names = _names(syn)
    return [(names[tag], line, round(share, 3)) for tag, line, share in ranked]


def _edge(name: str) -> synctex.SyncTeX:
    return synctex.load(SAMPLES / "edges" / f"{name}.synctex")


def _paper(version: str = "v1") -> synctex.SyncTeX:
    return synctex.load(SAMPLES / "paper" / version / "main.synctex.gz")


# --- the truth corpus ----------------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
def test_a_word_is_given_its_line_by_point_and_by_rect(engine):
    syn = synctex.load(SAMPLES / "truth" / engine / "main.synctex.gz")
    names, cases = _names(syn), CASES[engine]
    assert syn.pages == cases["pages"] == 2
    exact = {"point": 0, "rect": 0}
    for page, (x0, y0, x1, y1), file, line in cases["words"]:
        point = syn.edit(page, (x0 + x1) / 2, (y0 + y1) / 2)
        ranked = syn.query(page, [(x0, y0, x1, y1)], text=True)
        assert point and ranked, (page, file, line)
        assert sum(share for _t, _l, share in ranked) == pytest.approx(1)
        for key, (tag, got) in (("point", point), ("rect", ranked[0][:2])):
            assert names[tag] == file and abs(got - line) <= 1, (key, page, file, line, names[tag], got)
            exact[key] += got == line
    n = len(cases["words"])
    assert n > 900 and min(exact.values()) >= 0.97 * n, exact


@pytest.mark.parametrize("engine", ENGINES)
def test_a_line_of_the_pdf_names_the_source_lines_its_words_came_from(engine):
    syn = synctex.load(SAMPLES / "truth" / engine / "main.synctex.gz")
    names, named, missed = _names(syn), 0, 0
    for page, rect, want in CASES[engine]["lines"]:
        want = {tuple(w) for w in want}
        got = {(names[tag], line) for tag, line, share in syn.query(page, [rect], text=True) if share >= 0.05}
        named, missed = named + len(want & got), missed + len(want - got)
    assert named >= 0.9 * (named + missed), (named, missed)


# --- the paper -----------------------------------------------------------------------------------

def test_the_figure_the_equation_and_the_page_number():
    syn, p = _paper(), PLACES["v1"]
    figure = _named(syn, syn.query(1, [p["figure"]["bp"]], text=False))
    assert figure[0][:2] == ("main.tex", p["figure"]["line"])                     # its \includegraphics
    assert _named(syn, syn.query(1, [p["equation"]["bp"]], text=False)) == [
        ("method.tex", p["equation"]["line"], 1.0)]                                # its \end{equation}
    shipout = syn.sheet(1).shipout
    assert _names(syn)[shipout[0]] == "main.tex"
    folio = syn.query(1, [p["page_number"]["bp"]], text=False)
    assert [r[:2] for r in folio] == [shipout]                                      # the shipout line, alone
    with_text = syn.query(1, [p["page_number"]["bp"], [72, 690, 540, 700]], text=False)
    assert with_text[-1][:2] == shipout and with_text[-1][2] < 0.01                # ranked last beside text
    assert all(r[:2] != shipout for r in with_text[:-1])


def test_a_passage_names_its_lines_and_a_mark_in_the_margin_its_line():
    syn, p = _paper(), PLACES["v1"]["passage"]
    ranked = syn.query(1, p["bp"], text=True)
    assert {(f, ln) for f, ln, s in _named(syn, ranked) if s >= 0.05} == {
        ("method.tex", n) for n in range(p["line"], p["end"] + 1)}
    assert sum(share for _t, _l, share in ranked) == pytest.approx(1)
    # a mark 5 bp left of the passage's second line (its words are line 3's) is that line's; 35 bp away, nothing's
    assert [r[:2] for r in _named(syn, syn.query(1, [[80, 494, 85, 505]], text=False))] == [("method.tex", 3)]
    assert syn.query(1, [[50, 494, 55, 505]], text=False) == []
    # a page past the end
    assert syn.query(3, [[72, 72, 100, 100]], text=False) == [] and syn.edit(3, 72, 72) is None
    assert syn.query(1, [], text=True) == []


def test_view_gives_the_boxes_a_source_line_was_set_in():
    syn, tags = _paper(), _tags(_paper())
    p = PLACES["v1"]["passage"]
    assert syn.view(tags["method.tex"], 3) == [(1, 412.09, 481.07, 522.0, 490.77), (1, 90.0, 494.62, 276.59, 504.32)]
    assert syn.view(tags["method.tex"], 11) == [(1, 254.26, 530.78, 357.74, 541.69), (1, 508.06, 530.78, 522.0, 541.69)]
    assert syn.view(tags["main.tex"], syn.sheet(1).shipout[1]) == [(1, 303.27, 724.86, 308.73, 731.89)]
    assert syn.view(tags["main.tex"], 9999) == []
    # line 3's first box is the passage's first line, as MuPDF read it
    x0, y0, x1, y1 = syn.view(tags["method.tex"], 3)[0][1:]
    r0, r1, r2, r3 = p["bp"][0]
    assert abs(x0 - r0) < 0.5 and abs(x1 - r2) < 0.5 and r1 < y0 < y1 < r3


def test_the_rebuilt_paper_moves_the_passage_down_three_lines():
    for version in ("v1", "v2"):
        syn, p = _paper(version), PLACES[version]["passage"]
        lines = sorted({ln for f, ln, s in _named(syn, syn.query(1, p["bp"], text=True)) if s >= 0.05})
        assert (lines[0], lines[-1]) == (p["line"], p["end"])
    assert (PLACES["v2"]["passage"]["line"], PLACES["v2"]["passage"]["end"]) == (6, 8)


# --- the format's edges ----------------------------------------------------------------------------

def test_an_equals_sign_is_the_v_of_the_record_before():
    syn = _edge("equals")
    assert syn.inputs == {1: "/work/edge/./main.tex"} and syn.pages == 1
    # the inner box (line 7) holds the point only when its ``=`` is the line's baseline
    assert [syn.edit(1, x, Y) for x in (80, 91, 106, 118, 130)] == [(1, 3), (1, 3), (1, 4), (1, 7), (1, 7)]
    assert {r[:2] for r in syn.query(1, LINE, text=False)} == {(1, 3), (1, 4), (1, 5), (1, 7)}


def test_a_link_with_a_column_reads_as_one_without():
    assert [_edge("column").edit(1, x, Y) for x in (80, 91, 106, 118)] == [(1, 3), (1, 3), (1, 4), (1, 7)]


def test_input_lines_inside_the_content_and_two_tags_for_one_file():
    syn = _edge("input_inside")
    assert syn.inputs == {1: "/work/edge/./main.tex", 2: "/work/edge/./chapter.tex", 3: "/work/edge/./main.aux"}
    assert syn.pages == 2
    assert (syn.edit(1, 91, Y), syn.edit(2, 91, Y), syn.edit(2, 106, Y)) == ((1, 3), (2, 3), (2, 4))
    two = _edge("two_tags")
    assert two.inputs == {1: "/work/edge/./main.tex", 2: "/work/edge/./main.tex"}
    assert {r[:2] for r in two.query(1, LINE, text=False)} == {(1, 3), (2, 5)}


def test_a_negative_font_kern_with_the_shipouts_tag_says_nothing():
    """LuaTeX writes a word's font kerns behind the ``x`` before them, with
    the line TeX was at when the page shipped out, and the ``x`` after
    them inherits it (``synctex edit`` answers that line, 9, for the whole
    word): the nearest other leaf answers."""
    syn = _edge("negative_kern")
    assert [syn.edit(1, x, Y) for x in (80, 93.64, 95.8, 106.4)] == [(1, 3), (1, 3), (1, 3), (1, 4)]
    assert {r[:2] for r in syn.query(1, LINE, text=False)} == {(1, 3), (1, 4)}


def test_xetexs_offsets_put_the_page_where_pdftex_puts_it():
    """XeTeX's ``xdv`` output puts the origin an inch in and writes the
    offsets for it, and no ``x`` records: a run before a space is the
    space's line (``synctex edit`` names line 3 for every point here)."""
    syn = _edge("xetex_offsets")
    assert [syn.edit(1, x, 136.8) for x in (80, 95, 105, 120)] == [(1, 3), (1, 3), (1, 4), (1, 5)]
    assert syn.view(1, 3) == _edge("equals").view(1, 3) == [(1, 72.0, 129.22, 91.21, 138.94)]


def test_a_post_scriptum_overrides_the_magnification_and_the_offsets():
    """``equals`` under ``Magnification:2``, ``X Offset:1in``, ``Y
    Offset:0.5in`` (what ``synctex update -m 2 -x 1in -y 0.5in`` writes;
    ``synctex edit`` then answers lines 3, 4 and 7 at the same points)."""
    syn, plain = _edge("post_scriptum"), _edge("equals")
    assert [syn.edit(1, x, 309) for x in (230, 254, 280, 300, 330)] == [(1, 3), (1, 3), (1, 4), (1, 7), (1, 7)]
    assert syn.edit(1, 130, Y) == (1, 3)                       # no longer the inner box
    for line in (3, 7):
        x0, y0, x1, y1 = plain.view(1, line)[0][1:]
        assert syn.view(1, line) == [pytest.approx((1, 72 + 2 * x0, 36 + 2 * y0, 72 + 2 * x1, 36 + 2 * y1), abs=0.02)]


def test_no_content_and_a_sheet_the_file_ends_inside():
    with pytest.raises(synctex.SyncTeXError):
        _edge("no_content")
    with pytest.raises(synctex.SyncTeXError):
        synctex.SyncTeX(b"%PDF-1.4\n")
    syn = _edge("truncated")
    assert syn.pages == 2
    assert (syn.edit(1, 91, Y), syn.edit(2, 91, Y), syn.edit(2, 106, Y)) == ((1, 3), (1, 5), (1, 6))
    assert {r[:2] for r in syn.query(2, LINE, text=False)} == {(1, 5), (1, 6)}


def test_a_nesting_too_deep_is_cut_without_recursion():
    head = (SAMPLES / "edges" / "no_content.synctex").read_bytes() + b"Content:\n!1\n{1\n"
    deep = 2000
    text = head + b"".join(b"(1,%d:4736286,9000000:30785865,500000,140000\n" % d for d in range(1, deep + 1))
    syn = synctex.SyncTeX(text + b")\n" * deep + b"}1\n")
    assert syn.pages == 1 and deep > synctex.MAX_DEPTH
    assert syn.edit(1, 100, Y) == (1, synctex.MAX_DEPTH)
    assert syn.query(1, LINE, text=False) == [(1, synctex.MAX_DEPTH, 1.0)]


def test_a_file_too_large_is_refused(tmp_path, monkeypatch):
    text = (SAMPLES / "edges" / "equals.synctex").read_bytes()
    plain, packed = tmp_path / "a.synctex", tmp_path / "b.synctex.gz"
    plain.write_bytes(text)
    packed.write_bytes(gzip.compress(text))
    assert synctex.load(packed).edit(1, 118, Y) == (1, 7)
    monkeypatch.setattr(synctex, "MAX_TEXT", len(text) - 1)
    with pytest.raises(synctex.SyncTeXError, match="too large"):
        synctex.load(plain)
    with pytest.raises(synctex.SyncTeXError, match="too large"):
        synctex.SyncTeX(synctex._text(packed.read_bytes()))
    with pytest.raises(synctex.SyncTeXError, match="gzip"):
        synctex.SyncTeX(synctex._text(b"\x1f\x8b" + text))
    monkeypatch.setattr(synctex, "MAX_TEXT", len(text))
    monkeypatch.setattr(synctex, "MAX_GZ", packed.stat().st_size - 1)
    with pytest.raises(synctex.SyncTeXError, match="too large"):
        synctex.SyncTeX(synctex._text(packed.read_bytes()))


# --- load ------------------------------------------------------------------------------------------

def test_load_keeps_a_file_by_its_stat_and_holds_it_open_no_longer(tmp_path, monkeypatch):
    path = tmp_path / "main.synctex"
    path.write_bytes((SAMPLES / "edges" / "equals.synctex").read_bytes())
    syn = synctex.load(path)
    assert synctex.load(str(path)) is syn
    built = tmp_path / "main.synctex(busy)"
    built.write_bytes((SAMPLES / "edges" / "two_tags.synctex").read_bytes())
    os.replace(built, path)                 # the next build's rename over it: refused while a handle is open
    again = synctex.load(path)
    assert again is not syn and again.inputs[2] == "/work/edge/./main.tex"
    # the last FILES stay; CRLF line ends read as LF
    monkeypatch.setattr(synctex, "FILES", 1)
    other = tmp_path / "other.synctex"
    other.write_bytes((SAMPLES / "edges" / "equals.synctex").read_bytes().replace(b"\n", b"\r\n"))
    assert synctex.load(other).edit(1, 118, Y) == (1, 7)
    assert synctex.load(path) is not again
    with pytest.raises(OSError):
        synctex.load(tmp_path / "none.synctex.gz")


def test_a_file_keeps_at_most_sheets_parsed_sheets(monkeypatch):
    monkeypatch.setattr(synctex, "SHEETS", 1)
    syn = synctex.SyncTeX((SAMPLES / "edges" / "input_inside.synctex").read_bytes())
    first = syn.sheet(1)
    assert syn.sheet(2) is not None and syn.sheet(1) is not first and syn.sheet(3) is None
