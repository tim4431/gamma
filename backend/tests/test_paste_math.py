"""paste_math.dollar_math and paste_math.paste_math against
tests/shared/pastemath.json, the cases frontend/src/editor/pasteMath.js
passes too: ``dollar`` the conversion, ``paste`` where text put in stays as
written ("|" marks where)."""

import json
from pathlib import Path

import pytest

from gamma.paste_math import _fences, dollar_math, paste_math

SHARED = Path(__file__).resolve().parents[2] / "tests/shared"
CASES = json.loads((SHARED / "pastemath.json").read_text(encoding="utf-8"))
FENCES = json.loads((SHARED / "codefences.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES["dollar"], ids=[c["why"] for c in CASES["dollar"]])
def test_dollar_math_follows_the_shared_cases(case):
    assert dollar_math(case["in"], case.get("keep", "")) == case["out"]


@pytest.mark.parametrize("case", CASES["dollar"], ids=[c["why"] for c in CASES["dollar"]])
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


@pytest.mark.parametrize("case", CASES["paste"], ids=[c["why"] for c in CASES["paste"]])
def test_text_put_into_source_stays_as_written(case):
    assert paste_math(case["at"].replace("|", ""), case["at"].index("|"), case["text"]) == case["out"]
