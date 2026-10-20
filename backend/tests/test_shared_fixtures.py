"""Rules the frontend mirrors, pinned by one set of cases both sides read:
tests/shared/*.json at the repository root. The node half is
frontend/tests/textnorm.test.mjs, ink.test.mjs, finishStroke.test.mjs,
notebook.test.mjs, mentions.test.mjs and textBox.test.mjs; a case added here fails whichever side drifts. The
mirror's cases (synctree.json, textmerge.json) have only this Python side."""

import json
from pathlib import Path

import pytest

from gamma import ink as inkmod
from gamma.textnorm import fuzzy_pattern, normalize_text

SHARED = Path(__file__).resolve().parents[2] / "tests" / "shared"


def _load(name):
    return json.loads((SHARED / name).read_text(encoding="utf-8"))


TEXTNORM = _load("textnorm.json")
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


INK_SPACE = {"kind": "pdf-page", "page": 1, "width": 612, "height": 792}
INK_DEFAULT = {"tool": "pen", "color": "#1f1f1f", "size": 1.6, "opacity": 1, "pen": True, "ch": "xy"}
INK_LOOK_KEYS = ("color", "size", "opacity", "brush", "dash", "nib", "band", "fill", "from", "tool", "pen")


def _merge_file(strokes):
    """An ink file of the fixture's ``[id, v]`` / ``[id, v, look]`` strokes:
    ``v`` places the stroke (its geometry), ``look`` spreads over the
    default stroke."""
    if strokes is None:
        return None
    out = []
    for entry in strokes:
        sid, v = entry[0], entry[1]
        look = entry[2] if len(entry) > 2 else {}
        out.append({"id": sid, **INK_DEFAULT, "pts": [100 + 100 * v, 100], **look})
    return inkmod.parse_ink({"format": "gamma-ink", "version": 2, "space": INK_SPACE, "strokes": out})


def _merge_entry(stroke):
    """A stroke back in the fixture's shape: ``[id, v]`` plus the keys that
    differ from the default (or ``from``), when any."""
    d = inkmod.to_dict(stroke)
    look = {k: d[k] for k in INK_LOOK_KEYS if k in d and (k == "from" or d[k] != INK_DEFAULT.get(k))}
    return [stroke.id, (stroke.pts[0] - 100) // 100] + ([look] if look else [])


@pytest.mark.parametrize("case", INKMERGE["cases"], ids=[c["note"] for c in INKMERGE["cases"]])
def test_ink_merge(case):
    base_ids = case.get("base_ids") if case["base"] is None else None
    got = inkmod.merge_ink(_merge_file(case["base"]), _merge_file(case["ours"]), _merge_file(case["theirs"]),
                           base_ids=base_ids)
    assert [_merge_entry(s) for s in got.ink.strokes] == case["result"]
    assert got.clean is case["clean"]
    if "lost_ours" in case:
        assert sorted(got.lost_ours) == sorted(case["lost_ours"])
    if "returned" in case:
        assert sorted(got.returned) == sorted(case["returned"])
    # the two-tuple unpacking the older callers used still works
    ink, clean = got
    assert ink is got.ink and clean is got.clean


INKV2 = _load("inkv2.json")


@pytest.mark.parametrize("case", INKV2["cases"], ids=[c["note"] for c in INKV2["cases"]])
def test_ink_upgrade_v1(case):
    """The one-time version 1 → 2 conversion, byte for byte, and idempotent."""
    assert inkmod.dumps(inkmod.upgrade_v1(json.loads(case["v1"]))) == case["v2"].encode()
    assert inkmod.dumps(inkmod.upgrade_v1(json.loads(case["v2"]))) == case["v2"].encode()
    # the canonical bytes of the parsed version 2 file are the file itself
    assert inkmod.dumps(inkmod.parse_ink(case["v2"].encode())) == case["v2"].encode()
    # version 1 is refused everywhere but the converter
    with pytest.raises(inkmod.InkVersionError):
        inkmod.parse_ink(case["v1"].encode())


INKWIDTH = _load("inkwidth.json")


@pytest.mark.parametrize("row", INKWIDTH["rows"], ids=[r["note"] for r in INKWIDTH["rows"]])
def test_ink_width(row):
    """The drawn width is size × w (or size without the channel): the
    server reads the baked multiplier and knows no law."""
    stroke = {"id": "a", "tool": row["tool"], "pen": row["pen"], "size": row["size"], "ch": "xy", "pts": [100, 100]}
    if row["brush"]:
        stroke["brush"] = row["brush"]
    if row["w_enc"] is not None:
        stroke.update(ch="xyw", pts=[100, 100, row["w_enc"]])
    ink = inkmod.parse_ink({"format": "gamma-ink", "version": 2,
                            "space": {"kind": "canvas", "width": 10, "height": 10}, "strokes": [stroke]})
    s = ink.strokes[0]
    sample = inkmod.decode_stroke(s)[0]
    assert sample.get("w") == row["w"]
    assert inkmod.stroke_width(s, sample) == pytest.approx(row["width"], abs=1e-6)
    assert inkmod.stroke_polyline(s)[0][2] == pytest.approx(row["width"], abs=1e-6)


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
    """The mirror's tree rules: the ops between two trees, applied back."""
    from gamma import sync_tree
    page = SYNCTREE["page"]
    assert sync_tree.diff(case["base"], case["target"], page) == case["ops"]
    assert sync_tree.apply(case["base"], case["ops"]) == case["applied"]
    assert sorted(sync_tree.moved(case["base"], case["target"])) == case["moved"]


@pytest.mark.parametrize("case", SYNCTREE["unlanded"], ids=[c["note"] for c in SYNCTREE["unlanded"]])
def test_sync_unlanded(case):
    """What a push whose answer was lost sends again."""
    from gamma import sync_engine
    assert list(sync_engine._unlanded(case["op"], case["base"], case["remote"])) == case["result"]


MENTIONS = _load("mentions.json")


@pytest.mark.parametrize("case", MENTIONS["scan"], ids=[c["note"] for c in MENTIONS["scan"]])
def test_mention_scan(case):
    from gamma import mentions
    assert mentions.scan(case["text"]) == case["tokens"]
