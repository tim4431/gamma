"""Rules the frontend mirrors, pinned by one set of cases both sides read:
tests/shared/*.json at the repository root. The node half is
frontend/tests/textnorm.test.mjs, libraryUtils.test.mjs, ink.test.mjs,
replica.test.mjs and textBox.test.mjs; a case added here fails whichever
side drifts."""

import json
from pathlib import Path

import pytest

from gamma import foldertags
from gamma import ink as inkmod
from gamma.textnorm import fuzzy_pattern, normalize_text

SHARED = Path(__file__).resolve().parents[2] / "tests" / "shared"


def _load(name):
    return json.loads((SHARED / name).read_text(encoding="utf-8"))


TEXTNORM = _load("textnorm.json")
FOLDERTAGS = _load("foldertags.json")
INKMERGE = _load("inkmerge.json")


@pytest.mark.parametrize("case", TEXTNORM["normalize"], ids=[c["note"] for c in TEXTNORM["normalize"]])
def test_normalize_text(case):
    assert normalize_text(case["input"]) == case["output"]


@pytest.mark.parametrize("case", TEXTNORM["fuzzy"], ids=[c["note"] for c in TEXTNORM["fuzzy"]])
def test_fuzzy_pattern(case):
    pat = fuzzy_pattern(case["query"], case=case.get("case", False), whole=case.get("whole", False))
    if "pattern" in case and case["pattern"] is None:
        assert pat is None
        return
    assert pat is not None
    assert bool(pat.search(case["text"])) is case["match"], pat.pattern


def test_folder_tag_rules():
    for case in FOLDERTAGS["parse_tags"]:
        assert foldertags.parse_tags(case["input"]) == case["output"], case
    for case in FOLDERTAGS["clean_segment"]:
        assert foldertags.clean_segment(case["input"]) == case["output"], case
    for case in FOLDERTAGS["clean_path"]:
        assert foldertags.clean_path(case["input"]) == case["output"], case
    for case in FOLDERTAGS["add_tag"]:
        assert foldertags.add_tag(list(case["tags"]), case["path"]) == case["output"], case


def _merge_file(strokes):
    """An ink file of the fixture's ``[id, version]`` strokes (the version
    moves the stroke, so two versions of one id differ)."""
    return None if strokes is None else inkmod.parse_ink({
        "format": "gamma-ink", "version": 1, "space": {"kind": "pdf-page", "page": 1, "width": 612, "height": 792},
        "strokes": [{"id": sid, "ch": "xy", "pts": [100 + 100 * v, 100]} for sid, v in strokes]})


@pytest.mark.parametrize("case", INKMERGE["cases"], ids=[c["note"] for c in INKMERGE["cases"]])
def test_ink_merge(case):
    merged, clean = inkmod.merge_ink(_merge_file(case["base"]), _merge_file(case["ours"]), _merge_file(case["theirs"]))
    assert [[s.id, (s.pts[0] - 100) // 100] for s in merged.strokes] == case["result"]
    assert clean is case["clean"]


PAPER = _load("paper.json")


@pytest.mark.parametrize("case", PAPER["normalize"], ids=[c["note"] for c in PAPER["normalize"]])
def test_paper_normalize(case):
    from gamma.notebook import normalize_paper
    assert normalize_paper(case["input"], case["fallback"]) == case["output"]


@pytest.mark.parametrize("case", PAPER["lines"], ids=[c["note"] for c in PAPER["lines"]])
def test_paper_lines(case):
    from gamma.notebook import normalize_paper, paper_lines
    geo = paper_lines(normalize_paper(case["paper"]))
    assert geo == {"lines": case["lines"], "dots": case["dots"]}


@pytest.mark.parametrize("case", PAPER["sheets"], ids=[c["note"] for c in PAPER["sheets"]])
def test_page_sheets(case):
    from gamma.notebook import sheets_of
    blocks = [{"id": bid, "parent_id": b["parent"], "position": b["position"], "properties": b["props"]}
              for bid, b in case["blocks"].items()]
    got = [{"id": s["id"], "paper": s["paper"],
            "ink": [b["id"] for b in s["blocks"] if (b["properties"] or {}).get("ink_url")]}
           for s in sheets_of(blocks, case["page"])]
    assert got == case["sheets"]


TEXTBOX = _load("textbox.json")


@pytest.mark.parametrize("case", TEXTBOX["normalize"], ids=[c["note"] for c in TEXTBOX["normalize"]])
def test_text_box_normalize(case):
    from gamma.text_box import normalize_text_box
    # as JSON text, so True is not 1 and a whole number is written as the client writes it (23, not 23.0)
    got = json.dumps(normalize_text_box(case["input"]), sort_keys=True)
    assert got == json.dumps(case["output"], sort_keys=True)


TEXTBOXMERGE = _load("textboxmerge.json")


@pytest.mark.parametrize("case", TEXTBOXMERGE["merge"], ids=[c["note"] for c in TEXTBOXMERGE["merge"]])
def test_text_box_merge(case):
    from gamma.text_box import merge_text_box
    got = json.dumps(merge_text_box(case["stored"], case["mine"], case["base"]), sort_keys=True)
    assert got == json.dumps(case["result"], sort_keys=True)


TEXTMERGE = _load("textmerge.json")


@pytest.mark.parametrize("case", TEXTMERGE["merge"], ids=[c["note"] for c in TEXTMERGE["merge"]])
def test_text_merge(case):
    from gamma import textmerge
    assert list(textmerge.merge(case["base"], case["ours"], case["theirs"])) == case["result"]


@pytest.mark.parametrize("case", TEXTMERGE["contains"], ids=[c["note"] for c in TEXTMERGE["contains"]])
def test_text_contains(case):
    from gamma import textmerge
    assert textmerge.contains(case["base"], case["ours"], case["theirs"]) is case["result"]


@pytest.mark.parametrize("case", TEXTMERGE["map_offset"], ids=[c["note"] for c in TEXTMERGE["map_offset"]])
def test_text_map_offset(case):
    from gamma import textmerge
    assert textmerge.map_offset(case["src"], case["dst"], case["offset"]) == case["result"]


SYNCTREE = _load("synctree.json")


@pytest.mark.parametrize("case", SYNCTREE["diff"], ids=[c["note"] for c in SYNCTREE["diff"]])
def test_sync_tree_diff(case):
    """The mirror's tree rules the iPad's replica mirrors (frontend/src/replica/tree.js)."""
    from gamma import sync_tree
    page = SYNCTREE["page"]
    assert sync_tree.diff(case["base"], case["target"], page) == case["ops"]
    assert sync_tree.apply(case["base"], case["ops"]) == case["applied"]
    assert sorted(sync_tree.moved(case["base"], case["target"])) == case["moved"]


@pytest.mark.parametrize("case", SYNCTREE["unlanded"], ids=[c["note"] for c in SYNCTREE["unlanded"]])
def test_sync_unlanded(case):
    """What a push whose answer was lost sends again (frontend/src/replica/reconcile.js unlanded)."""
    from gamma import sync_engine
    assert list(sync_engine._unlanded(case["op"], case["base"], case["remote"])) == case["result"]
