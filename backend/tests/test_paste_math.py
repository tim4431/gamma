"""paste_math.dollar_math against tests/shared/pastemath.json,
the cases frontend/src/editor/pasteMath.js passes too."""

import json
from pathlib import Path

import pytest

from gamma.paste_math import _fences, dollar_math, paste_math

SHARED = Path(__file__).resolve().parents[2] / "tests/shared"
CASES = json.loads((SHARED / "pastemath.json").read_text(encoding="utf-8"))
FENCES = json.loads((SHARED / "codefences.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["why"] for c in CASES])
def test_dollar_math_follows_the_shared_cases(case):
    assert dollar_math(case["in"], case.get("keep", "")) == case["out"]


@pytest.mark.parametrize("case", CASES, ids=[c["why"] for c in CASES])
def test_converted_text_converts_no_further(case):
    assert dollar_math(case["out"], case.get("keep", "")) == case["out"]


@pytest.mark.parametrize("case", FENCES, ids=[c["why"] for c in FENCES])
def test_fences_follow_the_shared_cases(case):
    assert [list(f) for f in _fences(case["in"])] == case["fences"]


def test_fences_of_every_shape_are_source():
    # Tildes, a quoted fence and one still open keep what they hold; a
    # backtick line with a backtick in its info string opens no fence.
    for text in ("~~~\n\\(x\\)\n~~~", "> ```\n> \\(x\\)\n> ```", "- ```\n  \\(x\\)", "```\n\\(x\\)"):
        assert dollar_math(text) == text
    assert dollar_math("```js `x`\n\\(y\\)") == "```js `x`\n$y$"
    assert dollar_math("~~~\n\\(x\\)\n~~~\n\\(y\\)") == "~~~\n\\(x\\)\n~~~\n$y$"


def test_a_line_of_nothing_but_markers_costs_its_length():
    for text in ("- " * 100000 + "x", "```\n" + "- " * 100000, "- " * 5000 + "```\n" + "\n" * 50000):
        assert dollar_math(text) == text


def test_empty_text():
    assert dollar_math("") == ""


def test_text_put_into_source_stays_as_written():
    """paste_math, as pasteMath.pasteMath: converted, unless the text lands
    in a code fence, inline code or math ("|" marks where). The positions
    are frontend/tests/pasteMath.test.mjs's."""
    def at(with_caret, text):
        return paste_math(with_caret.replace("|", ""), with_caret.index("|"), text)
    assert at("see |", r"\(x\)") == "$x$"
    assert at("$a + | b$", r"\(x\)") == r"\(x\)"
    assert at("$$\na |\n$$", r"\[x^2\]") == r"\[x^2\]"
    assert at("price $5 and |", r"\(x\)") == r"\(x\)"  # an unclosed $ reads as math to its line's end
    assert at("```\nconst s = |\n```", r"\(x\)") == r"\(x\)"
    assert at("```\nopen |", r"\(x\)") == r"\(x\)"
    assert at("```py|thon", r"\(x\)") == "$x$"  # an open fence's info string
    assert at("> ```latex\n> |\n> ```", r"\(x\)") == r"\(x\)"
    assert at("- step\n  ~~~\n  |\n  ~~~", r"\(x\)") == r"\(x\)"
    assert at("- ```\n  ab\n  |```", r"\(x\)") == r"\(x\)"  # before an indented closing mark
    assert at("```\ncode\n```\nafter |", r"\(x\)") == "$x$"
    assert at("- ```\n  code\nafter |", r"\(x\)") == "$x$"  # after the list item that held a fence
    assert at("> ```\n> code\n\nafter |", r"\(x\)") == "$x$"  # after the quote that held one
    assert at("use `$$` for |", r"\(x\)") == "$x$"  # a $$ in code is no math
    assert at("Costs $5 a month.\n\nSet it up: |\n\n```sh\necho $HOME\n```", r"\(x\)") == "$x$"  # nor closes a dollar above
    assert at("$$\na `x$y` b\n|\n$$", r"\(x\)") == r"\(x\)"  # nor a $$ block
    assert at("Costs $5.\n\nRun `echo $HOME`.\n\n$$\nx|\n$$", r"\(x\)") == r"\(x\)"  # a lone $ ends with its paragraph
    assert at("run `echo |` now", r"\(x\)") == r"\(x\)"
    assert at("`code` and |", r"\(x\)") == "$x$"
    assert at("Set `$HOME` first, then |", r"\(x\)") == "$x$"  # a dollar in code is no math
