"""Ranges of a text file found again after the file changes
(gamma/documents/text_anchors.py, docs/dev/highlight_relocation.md "Text
files"): the fold, the ladder (exactly near the old line, word by word,
lost and kept) and what a run writes, pure, on the sample paper's
main.tex and small files. The trigger, the job and the write are
test_text_relocation.py's."""

import time
from pathlib import Path

import pytest

from gamma import highlight_relocate as pdf_run
from gamma.documents import text as doctext
from gamma.documents.text_anchors import (MAX_EXACT, TEXT_FLAGS, _fold_map, find_range, fold_line, place_all,
                                          prepare)
from gamma.highlights import (PREV_MAX_QUOTE, TEXT_QUOTE_MAX, clamp_span, span_text, text_position, text_span,
                              valid_text_closest, valid_text_prev)

SAMPLE = Path(__file__).parent / "samples" / "synctex" / "paper" / "v1" / "main.tex"
MAIN = doctext.split_lines(doctext.decode(SAMPLE.read_bytes()).text)


def _range(lines, span, hash="v1", **flags) -> dict:
    """A range's properties as the viewer writes them over ``lines``."""
    quote, prefix, suffix = span_text(lines, span)
    pos = text_position(*clamp_span(lines, span), hash=hash, prefix=prefix, suffix=suffix)
    return {"text_position": {**pos, **flags}, "quote": quote}


def _at(lines, needle, line=None, nth=0):
    """The span of ``needle`` on its line of ``lines`` (the first holding it, or ``line``)."""
    no = line or next(i for i, text in enumerate(lines, start=1) if needle in text)
    col = lines[no - 1].index(needle) if not nth else [i for i in range(len(lines[no - 1])) if
                                                          lines[no - 1].startswith(needle, i)][nth]
    return no, col, no, col + len(needle)


def _run(lines, items, new_hash="v2", **limits):
    return place_all(prepare(lines), items, new_hash, **limits)


def _place(out, bid):
    return out["ops"][bid]["text_position"]


# --- the fold ----------------------------------------------------------------------------

def test_the_fold_reads_whitespace_and_composition_alike_and_keeps_case():
    assert fold_line("\t  x  =\t\tfoo( a,  b )  \r") == "x = foo( a, b )"
    assert fold_line("    return x") == fold_line("\treturn x") == fold_line("return   x\r") == "return x"
    assert fold_line("café = 1") == fold_line("café = 1") == "café = 1"   # NFC
    assert fold_line("x ≠ y") != fold_line("x != y") and fold_line("ﬁle") == "ﬁle"     # not NFKC
    assert fold_line("LoadConfig") != fold_line("loadconfig")                        # case kept
    assert fold_line("   ") == "" and fold_line("") == ""
    # the map's text is the fold's, and its offsets say where each character came from
    for line in ("\t  x  =\t\tfoo( a,  b )  \r", "café  = 1", "  　日本\t語 ", "a ́b", "\r\n"):
        folded = _fold_map(line)
        assert folded.text == fold_line(line), line
        assert len(folded.starts) == len(folded.ends) == len(folded.text)
    folded = _fold_map("  ab  cd")
    assert (folded.text, folded.starts, folded.ends) == ("ab cd", [2, 3, 4, 6, 7], [3, 4, 6, 7, 8])
    # one rule for whitespace, the regex's and the map's str.isspace: a carriage return is dropped, any other a space
    spaces = [chr(c) for c in range(0x3100) if chr(c).isspace() and c != 0x0D]
    assert all(fold_line(f"a{c}b") == _fold_map(f"a{c}b").text == "a b" for c in spaces)
    assert fold_line("a\rb") == _fold_map("a\rb").text == "ab"


def test_prepare_folds_once_and_indexes_lines_and_words():
    text = prepare(["  def f():", "\treturn 1", "", "def f():"])
    assert text.folded == ["def f():", "return 1", "", "def f():"]
    assert text.where["def f():"] == [1, 4] and text.words["f"] == [1, 4] and text.words["return"] == [2]
    assert text.joined == "def f():\nreturn 1\n\ndef f():" and text.starts == [0, 9, 18, 19]


# --- exactly, near the old line ----------------------------------------------------------

def test_lines_inserted_above_move_a_range_solid():
    span = _at(MAIN, "Weight decay is the oldest remedy")
    items = [("a", _range(MAIN, span))]
    new = ["% a comment", "% another", "% a third"] + MAIN
    out = _run(new, items)
    assert out["kinds"] == {"a": "moved"} and (out["moved"], out["highlights"]) == (1, 1)
    pos = _place(out, "a")
    assert (pos["line"], pos["col"], pos["end"], pos["end_col"]) == (span[0] + 3, span[1], span[2] + 3, span[3])
    assert pos["hash"] == "v2" and not set(TEXT_FLAGS) & set(pos) and "quote" not in out["ops"]["a"]
    assert (pos["prefix"], pos["suffix"]) == (items[0][1]["text_position"]["prefix"], items[0][1]["text_position"]["suffix"])


def test_the_same_lines_keep_a_range_and_on_its_own_version_write_nothing():
    span = _at(MAIN, "Early stopping alone")
    items = [("a", _range(MAIN, span))]
    out = _run(MAIN, items)
    assert out["kinds"] == {"a": "kept"} and _place(out, "a")["hash"] == "v2"
    # measured on this very version and still over its quote: nothing to write
    out = _run(MAIN, [("a", _range(MAIN, span, hash="v2"))])
    assert out["kinds"] == {"a": "kept"} and out["ops"] == {}
    # ... unless it carries a run's flags (a lost one whose version came back)
    lost = _range(MAIN, span, hash="v2", lost=True, closest={"line": 1, "col": 0, "end": 1, "end_col": 3,
                                                               "match": 0.6, "quote": "x", "hash": "v2"})
    out = _run(MAIN, [("a", lost)])
    assert out["kinds"] == {"a": "kept"} and _place(out, "a") == _range(MAIN, span, hash="v2")["text_position"]


def test_a_range_on_the_new_version_whose_text_is_not_at_its_lines_is_searched():
    """Made on another computer's copy that was then edited, or written by
    hand: its hash is the new file's but its quote is elsewhere."""
    span = _at(MAIN, "Early stopping alone")
    props = _range(MAIN, span, hash="v2")
    props["text_position"].update(line=3, end=3)
    out = _run(MAIN, [("a", props)])
    assert out["kinds"] == {"a": "moved"} and _place(out, "a")["line"] == span[0]


def test_a_sentence_across_two_partial_lines_is_exact_on_the_unchanged_file():
    assert MAIN[14].endswith("held-out data turns and") and MAIN[15].startswith("rises again.")
    span = (15, MAIN[14].index("held-out"), 16, len("rises again."))
    props = _range(MAIN, span)
    assert props["quote"] == "held-out data turns and\nrises again."
    out = _run(MAIN, [("a", props)])
    assert out["kinds"] == {"a": "kept"}
    assert text_span({"text_position": _place(out, "a")}) == span
    # re-indented and with CRLF line ends, still exact
    crlf = [("  " + line + "\r") for line in MAIN]
    out = _run(crlf, [("a", props)])
    pos = _place(out, "a")
    assert out["kinds"] == {"a": "kept"} and (pos["line"], pos["col"], pos["end"]) == (15, span[1] + 2, 16)
    assert out["ops"]["a"]["quote"] == "held-out data turns and\r\n  rises again."


def test_a_bom_gained_or_lost_changes_nothing():
    data = SAMPLE.read_bytes()
    with_bom = doctext.split_lines(doctext.decode(b"\xef\xbb\xbf" + data).text)
    assert with_bom == MAIN
    span = _at(MAIN, "\\documentclass")
    out = _run(with_bom, [("a", _range(MAIN, span))])
    assert out["kinds"] == {"a": "kept"} and text_span({"text_position": _place(out, "a")}) == span


def test_the_same_text_twice_is_told_apart_by_its_surroundings_then_by_nearness():
    lines = ["Intro.", "\\begin{figure}", "\\includegraphics{loss.pdf}", "\\end{figure}", "Results follow.",
             "\\begin{figure}", "\\includegraphics{accuracy.pdf}", "\\end{figure}", "Conclusion here."]
    second = _range(lines, (8, 0, 8, 12))
    new = ["Intro.", "\\begin{figure}", "\\includegraphics{accuracy.pdf}", "\\end{figure}", "Conclusion here.",
           "\\begin{figure}", "\\includegraphics{loss.pdf}", "\\end{figure}", "Results follow."]
    out = _run(new, [("b", second)])
    assert _place(out, "b")["line"] == 4 and out["kinds"] == {"b": "moved"}   # its surroundings, not its line, win
    # surroundings alike: the nearer copy
    lines = ["}"] * 100
    out = _run(["x", "x"] + lines, [("m", _range(lines, (50, 0, 50, 1)))])
    assert _place(out, "m")["line"] == 50 and out["kinds"] == {"m": "kept"}


def test_a_selection_inside_a_word_matches_inside_a_word_and_a_word_only_on_its_edges():
    lines = ["the motion is slow", "an ion trap"]
    inside = _range(lines, (1, 7, 1, 10))         # "ion" of "motion"
    assert inside["quote"] == "ion"
    out = _run(["intro", "the motion is slow", "an ion trap"], [("i", inside)])
    assert _place(out, "i")["line"] == 2 and _place(out, "i")["col"] == 7
    word = _range(lines, (2, 3, 2, 6))            # the word "ion"
    out = _run(["the motion is slow", "the ion trap moved"], [("w", word)])
    assert (_place(out, "w")["line"], _place(out, "w")["col"]) == (2, 4)
    out = _run(["the motion is slow"], [("w", word)])
    assert out["kinds"] == {"w": "lost"}          # "ion" in "motion" is not the word


def test_a_whole_line_quote_matches_a_whole_line_only():
    lines = ["a", "return x", "b"]
    whole = _range(lines, (2, 0, 2, 8))
    out = _run(["return x + 1", "a", "    return x", "b"], [("w", whole)])
    pos = _place(out, "w")
    assert (pos["line"], pos["col"], pos["end_col"]) == (3, 0, 12) and out["ops"]["w"]["quote"] == "    return x"


def test_a_quote_at_the_cap_is_matched_as_its_first_lines():
    lines = ["head"] + [f"line {i:05d} " + "x" * 60 for i in range(200)]
    props = _range(lines, (2, 0, 201, 10))
    assert len(props["quote"]) == TEXT_QUOTE_MAX and not props["quote"].endswith("\n")
    out = _run(["new head", "more"] + lines[1:], [("q", props)])
    pos = _place(out, "q")
    assert out["kinds"] == {"q": "moved"} and pos["line"] == 3 and len(out["ops"]["q"].get("quote", props["quote"])) <= TEXT_QUOTE_MAX


def test_many_copies_are_ranked_among_the_nearest():
    lines = ["}"] * (MAX_EXACT * 3)
    out = _run(lines, [("c", _range(lines, (MAX_EXACT * 2, 0, MAX_EXACT * 2, 1)))])
    assert out["kinds"] == {"c": "kept"}


# --- word by word ------------------------------------------------------------------------

def test_a_renamed_identifier_is_placed_dashed_with_its_prev():
    lines = ["import json", "", "def load_config(path):", "    with open(path) as handle:",
             "        return json.load(handle)"]
    props = _range(lines, (4, 4, 5, 32))
    new = ["import json", "", "def load_config(path):", "    with open(path) as fh:", "        return json.load(fh)"]
    out = _run(new, [("r", props)])
    pos = _place(out, "r")
    assert out["kinds"] == {"r": "approx"} and pos["approx"] is True and 0.8 <= pos["match"] < 1
    assert (pos["line"], pos["col"], pos["end"]) == (4, 4, 5)
    assert out["ops"]["r"]["quote"] == "with open(path) as fh:\n        return json.load(fh)"
    assert pos["prev"] == {"hash": "v1", "line": 4, "col": 4, "end": 5, "end_col": 32, "quote": props["quote"]}
    assert (pos["prefix"], pos["suffix"]) == (props["text_position"]["prefix"], props["text_position"]["suffix"])
    assert valid_text_prev(pos["prev"]) == pos["prev"] and text_span({"text_position": pos})
    # at 100% a rough match is never applied: lost, the rough place its closest
    out = _run(new, [("r", props)], min_match=1.0)
    lost = _place(out, "r")
    assert out["kinds"] == {"r": "lost"} and lost["lost"] is True and lost["line"] == 4 and lost["hash"] == "v1"
    assert valid_text_closest(lost["closest"]) == lost["closest"] and lost["closest"]["hash"] == "v2"
    assert lost["closest"]["match"] == pos["match"] and "quote" not in out["ops"]["r"]


def test_a_reworded_sentence_a_changed_number_and_a_rewrapped_paragraph_read_roughly():
    span = (16, MAIN[15].index("Weight"), 17, len("try."))
    props = _range(MAIN, span)
    assert props["quote"] == "Weight decay is the oldest remedy, and the cheapest one to\ntry."
    reworded = list(MAIN)
    reworded[15] = MAIN[15].replace("the oldest remedy", "the oldest known remedy")
    out = _run(reworded, [("s", props)])
    assert out["kinds"] == {"s": "approx"} and 0.8 <= _place(out, "s")["match"] <= 0.99
    assert "known" in out["ops"]["s"]["quote"]
    number = _range(MAIN, (33, 0, 33, len(MAIN[32])))
    renumbered = list(MAIN)
    renumbered[32] = MAIN[32].replace("a fifth", "a sixth")
    out = _run(renumbered, [("n", number)])
    assert out["kinds"] == {"n": "approx"} and _place(out, "n")["line"] == 33
    # a paragraph wrapped again: the same words over other lines
    para = _range(MAIN, (35, 0, 38, len(MAIN[37])))
    words = " ".join(MAIN[34:38]).split()
    rewrapped = MAIN[:34] + [" ".join(words[:9]), " ".join(words[9:20]), " ".join(words[20:31]),
                             " ".join(words[31:42]), " ".join(words[42:])] + MAIN[38:]
    out = _run(rewrapped, [("p", para)])
    pos = _place(out, "p")
    assert out["kinds"] == {"p": "approx"} and pos["match"] >= 0.9 and (pos["line"], pos["end"]) == (35, 39)


def test_a_pending_range_is_matched_from_its_prev_and_keeps_it_through_two_rewrites():
    lines = ["one", "def handler(request, response):", "    return response", "two"]
    props = _range(lines, (2, 0, 3, 19))
    v2 = ["one", "def handler(req, response):", "    return response", "two"]
    out = _run(v2, [("p", props)], new_hash="v2")
    first = {**props, **out["ops"]["p"]}
    assert out["kinds"] == {"p": "approx"} and first["text_position"]["prev"]["hash"] == "v1"
    v3 = ["zero", "one", "def handler(req, resp):", "    return response", "two"]
    out = _run(v3, [("p", first)], new_hash="v3")
    second = _place(out, "p")
    assert out["kinds"] == {"p": "approx"} and second["prev"] == first["text_position"]["prev"]
    assert second["line"] == 3 and second["hash"] == "v3"
    # matched from prev's quote: the first version's text found exactly again is solid, without prev
    out = _run(["x"] + lines, [("p", {**first, **out["ops"]["p"]})], new_hash="v4")
    solid = _place(out, "p")
    assert out["kinds"] == {"p": "moved"} and not set(TEXT_FLAGS) & set(solid) and solid["line"] == 3
    assert out["ops"]["p"]["quote"] == props["quote"]
    # pending on this very version: it waits for the person
    out = _run(v2, [("p", first)], new_hash="v2")
    assert out["kinds"] == {"p": "approx"} and out["ops"] == {}


# --- lost and kept -----------------------------------------------------------------------

def test_a_lost_range_stays_where_it_was_with_or_without_its_closest():
    lines = ["alpha", "the quick brown fox jumps over the lazy dog", "omega"]
    props = _range(lines, (2, 0, 2, 43))
    near = ["alpha", "the quick red fox walks under a lazy cat", "omega"]
    out = _run(near, [("l", props)])
    pos = _place(out, "l")
    assert out["kinds"] == {"l": "lost"} and pos["lost"] is True and pos["closest"]["match"] >= pdf_run.CLOSEST_MIN
    assert {k: v for k, v in pos.items() if k not in ("lost", "closest")} == props["text_position"]
    far = ["alpha", "nothing of it remains in this line at all", "omega"]
    out = _run(far, [("l", props)])
    assert out["kinds"] == {"l": "lost"} and "closest" not in _place(out, "l")
    # a lost one run again on the same miss writes nothing new
    again = {**props, **out["ops"]["l"]}
    assert _run(far, [("l", again)])["ops"] == {}
    # under exact_chars a quote is looked for exactly or not at all: no closest either
    short = _range(["x = 1", "y = foo"], (2, 4, 2, 7))
    out = _run(["x = 1", "y = fob"], [("s", short)])
    assert out["kinds"] == {"s": "lost"} and "closest" not in _place(out, "s")
    # a lost one found again is solid, without lost
    out = _run(["new"] + lines, [("l", again)])
    assert out["kinds"] == {"l": "moved"} and not set(TEXT_FLAGS) & set(_place(out, "l"))


def test_a_one_character_quote_is_never_put_on_a_far_copy():
    lines = ["a", "b", "c"]
    out = _run(["x", "y", "z", "b"], [("c", _range(lines, (3, 0, 3, 1)))])
    assert out["kinds"] == {"c": "lost"} and _place(out, "c")["line"] == 3


def test_a_pending_range_not_found_goes_back_to_its_reference_lost():
    lines = ["one", "def handler(request, response):", "    return response", "two"]
    props = _range(lines, (2, 0, 3, 19))
    out = _run(["one", "def handler(req, response):", "    return response", "two"], [("p", props)])
    pending = {**props, **out["ops"]["p"]}
    out = _run(["completely", "different", "text"], [("p", pending)], new_hash="v3")
    pos = _place(out, "p")
    assert out["kinds"] == {"p": "lost"} and pos["lost"] is True and pos["hash"] == "v1"
    assert (pos["line"], pos["end"]) == (2, 3) and not {"approx", "match", "prev"} & set(pos)
    assert out["ops"]["p"]["quote"] == props["quote"] and text_span({"text_position": pos})


def test_a_file_rewritten_under_ten_ranges():
    """The gate: lines inserted above, some sentences changed, some gone;
    each range found again or marked lost and kept, the counts adding up."""
    spans = {
        "intro": _at(MAIN, "A model with many parameters"),
        "decay": _at(MAIN, "Weight decay is the oldest remedy"),
        "caption": _at(MAIN, "Training and test loss per epoch"),
        "fifth": _at(MAIN, "fifth epoch the test loss"),
        "grid": _at(MAIN, "from a grid of six values"),
        "seed": _at(MAIN, "every seed we tried"),
        "shrinks": _at(MAIN, "the benefit shrinks as the"),
        "early": _at(MAIN, "Early stopping alone recovered most of the"),
        "simple": _at(MAIN, "Weight decay is a simple and reliable way"),
        "shared": _at(MAIN, "shared across layers"),
    }
    items = [(bid, _range(MAIN, span)) for bid, span in spans.items()]
    new = list(MAIN)
    new[47] = "gain, and both together did a little better than either alone, which"   # line 48, not in a range
    new[39] = "Larger data sets need a smaller weight, and the gain shrinks as the"      # "shrinks" reworded
    del new[54:56]                                                                    # "shared across layers" gone
    del new[34:36]                                                                    # "grid" gone
    new = new[:3] + ["\\usepackage{hyperref}", "\\usepackage{xcolor}", "\\usepackage{booktabs}"] + new[3:]
    out = _run(new, items)
    kinds = out["kinds"]
    assert {bid for bid, kind in kinds.items() if kind == "moved"} == {"intro", "decay", "caption", "fifth", "seed",
                                                                        "early", "simple"}
    assert kinds["grid"] == kinds["shared"] == "lost" and kinds["shrinks"] in ("approx", "lost")
    assert sum(out[k] for k in ("moved", "kept", "approx", "lost")) == out["highlights"] == 10
    for bid, by in (("intro", 3), ("decay", 3), ("early", 3 - 2), ("simple", 3 - 2)):
        assert _place(out, bid)["line"] == spans[bid][0] + by
    for bid in ("grid", "shared"):
        lost = _place(out, bid)
        assert lost["lost"] is True and lost["line"] == spans[bid][0] and lost["hash"] == "v1"


def test_a_malformed_range_is_skipped_and_counted_nowhere():
    props = _range(MAIN, _at(MAIN, "Early stopping"))
    bad = {**props, "text_position": {**props["text_position"], "line": 0}}
    out = _run(MAIN, [("a", props), ("b", bad), ("c", {"quote": "x"})])
    assert out["highlights"] == 1 and set(out["kinds"]) == {"a"}


def test_find_range_answers_the_place_and_the_closest_when_asked():
    text = prepare(["alpha", "the quick red fox walks under a lazy cat", "omega"])
    needle = {"quote": "the quick brown fox jumps over the lazy dog", "prefix": "alpha\n", "suffix": "\nomega",
              "line": 2, "end": 2}
    assert find_range(text, needle) is None
    found = find_range(text, needle, closest=pdf_run.CLOSEST_MIN)
    assert found.below and not found.exact and found.line == 2 and pdf_run.CLOSEST_MIN <= found.match < 0.8
    part = {**needle, "quote": "red fox", "prefix": "the quick ", "suffix": " walks"}
    assert find_range(text, part).exact and find_range(text, part).col == 10
    assert find_range(text, {**part, "quote": " \n "}) is None
    # a quote stored as a whole line is looked for as one
    assert find_range(text, {**part, "prefix": "alpha\n", "suffix": "\nomega"}) is None
    assert find_range(prepare([]), needle) is None


def test_a_long_range_keeps_its_whole_quote_and_place_through_prev():
    """A prev holds the range's whole quote and span, not a PDF's first
    PREV_MAX_QUOTE characters: a pending range of sixty lines matched again
    stays sixty lines, and one lost and then restored is solid again."""
    lines = [f"    statement_{i} = compute(alpha_{i}, beta_{i})  # step {i}" for i in range(1, 201)]
    span = (50, 4, 109, len(lines[108]))
    props = _range(lines, span)
    assert len(props["quote"]) > PREV_MAX_QUOTE
    v2 = list(lines)
    v2[79] = "    statement_80 = compute(alpha_80, gamma_80)  # step 80 changed"
    out = _run(v2, [("r", props)])
    pending = {**props, **out["ops"]["r"]}
    prev = pending["text_position"]["prev"]
    assert out["kinds"] == {"r": "approx"} and prev == {"hash": "v1", "line": 50, "col": 4, "end": 109,
                                                        "end_col": span[3], "quote": props["quote"]}
    out = _run(["# a new first line"] + v2, [("r", pending)], new_hash="v3")
    pos = _place(out, "r")
    assert out["kinds"] == {"r": "approx"} and (pos["line"], pos["col"], pos["end"]) == (51, 4, 110)
    assert pos["prev"] == prev
    # the range's lines gone: lost back at its reference, its span and quote whole
    gone = v2[:49] + ["    removed()"] * 60 + v2[109:]
    out = _run(gone, [("r", pending)], new_hash="v4")
    lost = {**pending, **out["ops"]["r"]}
    assert out["kinds"] == {"r": "lost"} and text_span(lost) == span and lost["quote"] == props["quote"]
    assert lost["text_position"]["hash"] == "v1"
    # and the file back as it was: solid where it was
    out = _run(lines, [("r", lost)], new_hash="v5")
    assert out["kinds"] == {"r": "kept"} and text_span({"text_position": _place(out, "r")}) == span
    assert not set(TEXT_FLAGS) & set(_place(out, "r"))


def test_a_rough_place_keeps_to_one_alignment_and_to_the_quote_s_words():
    """The quote's edited head or tail is not matched to a stray copy of its
    words lines away: the place starts and ends where the range's own lines
    do, never inside a word."""
    body = ["    result = value * 2", "    if result > 10:", "        result -= 10", "    log(result)",
            "    total = result + offset", "    return total"]
    old = ["from compute import thing", "x = 1", "def compute(value):"] + body + ["", "print(x)"]
    new = ["from compute import thing", "x = 1", "def f(value):"] + body + ["", "print(x)"]
    out = _run(new, [("h", _range(old, (3, 0, 9, len(body[-1]))))])
    pos = _place(out, "h")
    assert out["kinds"] == {"h": "approx"} and (pos["line"], pos["col"], pos["end"]) == (3, 0, 9)
    assert out["ops"]["h"]["quote"].startswith("def f(value):")
    old = ["def g(v):"] + body[:-1] + ["    return finalize(total)", "", "x = finalize(total) + 1", "y = 2"]
    new = ["def g(v):"] + body[:-1] + ["    return total", "", "x = finalize(total) + 1", "y = 2"]
    out = _run(new, [("t", _range(old, (1, 0, 7, len(old[6]))))])
    pos = _place(out, "t")
    assert out["kinds"] == {"t": "approx"} and (pos["line"], pos["end"], pos["end_col"]) == (1, 7, len(new[6]))
    assert out["ops"]["t"]["quote"].endswith("return total")


# --- the cost ------------------------------------------------------------------------------

@pytest.mark.perf
def test_a_large_file_s_ranges_are_placed_within_a_second():
    """50,000 lines (about 3 MB) with 100 ranges, lines inserted above half
    of them and a word changed in a quarter: prepare once, then each range
    an exact lookup or six windows."""
    lines = [f"    value_{i:05d} = compute(alpha_{i % 97}, beta_{i % 89}, gamma_{i})  # step {i}" for i in range(50_000)]
    items = [(f"r{n}", _range(lines, (500 * n + 1, 4, 500 * n + 2, 20))) for n in range(100)]
    new = list(lines)
    for n in range(0, 100, 4):
        new[500 * n] = new[500 * n].replace("compute", "evaluate")
    new = new[:20_000] + [f"# inserted {i}" for i in range(30)] + new[20_000:]
    start = time.perf_counter()
    out = place_all(prepare(new), items, "v2")
    took = time.perf_counter() - start
    assert out["highlights"] == 100 and out["lost"] == 0
    assert took < 1.0, f"{took:.2f}s"


@pytest.mark.perf
def test_a_long_range_read_roughly_and_an_oversized_context_take_milliseconds():
    """The rough step's work is bounded whatever the quote's length (by
    tokens, then by whole lines), and what a range stores is read clipped:
    a range at the quote's cap with every tenth line edited, and a stored
    prefix of 100,000 characters over a quote found 500 times."""
    lines = [f"    value_{i:05d} = compute(alpha_{i % 97}, beta_{i % 89}, gamma_{i})  # step {i}" for i in range(9000)]
    props = _range(lines, (4500, 4, 4700, 20))
    assert len(props["quote"]) == TEXT_QUOTE_MAX
    new = [line + " changed" if 4499 <= i < 4700 and i % 10 == 0 else line for i, line in enumerate(lines)]
    start = time.perf_counter()
    out = _run(new, [("r", props)])
    took = time.perf_counter() - start
    pos = _place(out, "r")
    assert out["kinds"] == {"r": "approx"} and pos["line"] == 4500 and pos["match"] >= 0.9
    assert took < 1.0, f"{took:.2f}s"
    rep = [f"    x = foo(a{i}, b) + bar(c)" for i in range(2000)]
    planted = _range(rep, (1, 11, 1, 12))
    planted["text_position"].update(prefix="q w " * 25_000, suffix="zz" * 25_000)
    start = time.perf_counter()
    out = _run(rep, [("p", planted)])
    took = time.perf_counter() - start
    assert out["kinds"]["p"] in ("kept", "moved") and took < 1.0, f"{took:.2f}s"
