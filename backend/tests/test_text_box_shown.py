"""A Zotero box's text as gamma/text_box.py lays it out and sends it to
Zotero, pinned by tests/shared/zoteroshown.json, whose node half
(frontend/tests/textBoxShown.test.mjs) checks that read mode shows the same
lines and the same text: a blank line between two others takes a line,
blank lines at either end are neither laid out nor sent, a line of a
no-break or full-width space is blank, and a link reads as its text."""

import json
from pathlib import Path

import pytest

from gamma.text_box import LINE, PAD, _layout, normalize_text_box, plain_text

SHOWN = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "zoteroshown.json")
                   .read_text(encoding="utf-8"))
BOX = normalize_text_box(SHOWN["box"])


@pytest.mark.parametrize("case", SHOWN["cases"], ids=[c["note"] for c in SHOWN["cases"]])
def test_a_zotero_box_lays_out_and_sends_the_lines_read_mode_shows(case):
    _rows, _w, h = _layout(case["md"], BOX, SHOWN["surface_width"])
    assert h / (BOX["size"] * LINE) == pytest.approx(case["lines"])
    assert plain_text(case["md"], gaps=True) == case["sent"]


def test_a_gamma_box_still_gives_blank_lines_no_height():
    gamma = {k: v for k, v in BOX.items() if k != "zotero"}
    for md, lines in (("a\n", 1), ("\n\na", 1), ("a\n\nb", 2), ("a\n\n\nb", 2)):
        _rows, _w, h = _layout(md, gamma, SHOWN["surface_width"])
        assert h == pytest.approx(lines * gamma["size"] * LINE + 2 * PAD), md
