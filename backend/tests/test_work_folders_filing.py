"""The work-folder op rule's pure core (gamma/work_folders/filing.py,
docs/dev/work_folders.md "The op rule"): tests/shared/workfiling.json's
cases, each S, R and T row for a person, a share editor, the vault and the
mirror, through root_patch, creation, may_trash, may_restore and tree_op.
frontend/tests/workFolders.test.mjs reads the same cases; the op path's
own tests (test_work_folder_ops.py) run the rule through the routes."""

import json
from pathlib import Path

import pytest

from gamma.blocks_store import FOLDERS
from gamma.work_folders import filing, marks

FILING = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "workfiling.json")
                    .read_text(encoding="utf-8"))
TREES = {name: marks.Tree.of([(n["id"], n["parent"] or FOLDERS, n["id"], {"vault": n["vault"]} if "vault" in n else {})
                              for n in nodes])
         for name, nodes in FILING["trees"].items()}


def _cases(group):
    return pytest.mark.parametrize("case", FILING[group], ids=[c["name"] for c in FILING[group]])


def _judged(case, run):
    if case["allowed"]:
        return run()
    with pytest.raises(filing.Refused) as refused:
        run()
    assert refused.value.kind == case["kind"]
    assert refused.value.detail == filing.refusal_text(case["kind"]) == str(refused.value)
    return None


@_cases("patch")
def test_root_patch(case):
    got = _judged(case, lambda: filing.root_patch(TREES[case["tree"]], case["before"], case["patch"],
                                                  case.get("content"), case["writer"],
                                                  target_is_file=case.get("target_is_file", False)))
    if case["allowed"]:
        assert got == case.get("result", case["patch"])


@_cases("create")
def test_creation(case):
    got = _judged(case, lambda: filing.creation(TREES[case["tree"]], case["props"], case["writer"],
                                                target_is_file=case.get("target_is_file", False)))
    if case["allowed"]:
        assert got == case.get("result", case["props"])


@_cases("trash")
def test_may_trash(case):
    _judged(case, lambda: filing.may_trash(TREES[case["tree"]], case["props"], case["writer"]))


@_cases("restore")
def test_may_restore(case):
    got = _judged(case, lambda: filing.may_restore(TREES[case["tree"]], case["props"], case["writer"]))
    if case["allowed"]:
        assert got == case.get("result", case["props"])


@_cases("tree")
def test_tree_op(case):
    _judged(case, lambda: filing.tree_op(TREES[case["tree"]], case["op"], case["writer"]))


def test_the_fixture_covers_every_row_and_writer():
    names = [c["name"] for group in ("patch", "create", "trash", "restore", "tree") for c in FILING[group]]
    assert len(names) == len(set(names)), "case names are unique"
    rows = " / ".join(names)
    for row in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "S1", "S2", "S3", "S4", "T1", "T2", "T3", "T4"):
        assert row in rows, row
    for words in ("share editor", "dangling", "reorders", "at a work folder's top", "does not pull",
                  "lost its mark", "the vault trashed"):
        assert words in rows, words
    writers = {c["writer"] for group in ("patch", "create", "trash", "restore", "tree") for c in FILING[group]}
    assert writers == set(filing.WRITERS)
    kinds = {c["kind"] for group in ("patch", "create", "trash", "restore", "tree") for c in FILING[group]
             if not c["allowed"]}
    assert kinds == set(filing.WORDS)


def test_the_writer_of_an_op_client():
    assert [filing.writer_of(c) for c in ("sync", "disk", "", "ai", "meta", "zotero", "t", None)] == \
        ["mirror", "vault", "person", "person", "person", "person", "person", "person"]


def test_root_patch_leaves_its_input_alone():
    before = {"work_folder": "W1", "file_name": "main.pdf", "library": True, "folders": ["D1", "L1"]}
    patch = {"library": None}
    assert filing.root_patch(TREES["main"], before, patch, None, "person") == {"library": None, "folders": ["D1"]}
    assert patch == {"library": None} and before["folders"] == ["D1", "L1"]
