"""The library rule (gamma/work_folders/library.py, docs/dev/work_folders.md
"The library rule"): tests/shared/library.json's cases through the Python
predicates, and the same answers from the SQL conditions over each case as
a root row of a fresh pages.db table, read through the generated
``work_folder`` column. frontend/tests/library.test.mjs reads the same
cases. Then the partial indexes the listing and a directory's read are
planned on, the mark helpers of gamma/work_folders/marks.py, and the
conftest helpers that mark a work folder and stamp a file."""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import login, make_folder, make_user, mark_work_folder, work_props, workspace_of
from gamma.db import BLOCK_HOT_INDEXES, PAGES_SCHEMA, SQL_IN_LISTING, connect_pages_db
from gamma.work_folders import library, marks
from gamma.work_folders.library import (SQL_LIBRARY_PDF, SQL_LISTED, in_listing, is_library_pdf, library_document,
                                        listed, work_folder_of)

LIBRARY = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "library.json")
                     .read_text(encoding="utf-8"))
CASES = LIBRARY["cases"]


def _table() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks")))
    conn.execute("CREATE INDEX idx_ub_parent ON unified_blocks(parent_id, position)")
    for stmt in BLOCK_HOT_INDEXES:
        conn.execute(stmt)
    return conn


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_rule_in_python(case):
    props = case["props"]
    got = {"listed": listed(props), "in_listing": in_listing(props), "library_document": library_document(props),
           "library_pdf": is_library_pdf(props)}
    assert got == {key: case[key] for key in got}


def test_the_rule_in_sql():
    """Every case as a root row: SQL_LISTED, SQL_IN_LISTING and the library
    PDF lookup's condition (with the doc_id column, as page_for_doc reads
    it) answer as the predicates do."""
    conn = _table()
    conn.executemany("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                     "updated_at, page_id) VALUES (?, 'root', ?, '', ?, 't', 't', ?)",
                     [(f"p{i}", f"a{i:03}", json.dumps(c["props"]), f"p{i}") for i, c in enumerate(CASES)])
    for key, where in (("listed", SQL_LISTED), ("in_listing", SQL_IN_LISTING),
                       ("library_pdf", f"doc_id IS NOT NULL AND {SQL_LIBRARY_PDF}")):
        hits = {r[0] for r in conn.execute(f"SELECT id FROM unified_blocks WHERE parent_id = 'root' AND {where}")}
        assert {c["name"] for i, c in enumerate(CASES) if f"p{i}" in hits} == \
            {c["name"] for c in CASES if c[key]}, key
    # the column is the predicate's work folder: "" and a value that is no text are none
    got = dict(conn.execute("SELECT id, work_folder FROM unified_blocks"))
    assert {f"p{i}": work_folder_of(c["props"]) or None for i, c in enumerate(CASES)} == got
    assert library.SQL_IN_LISTING is SQL_IN_LISTING


def test_the_fixture_covers_the_cases():
    names = " / ".join(c["name"] for c in CASES)
    for words in ("a stored PDF page", "a copy", "a URL page", "an Opened files page", "after Add to library",
                  "a note", "not in the library", "in the library", "a work folder's note", "a pinned file",
                  "library true without work_folder", "work_folder empty", "a doc that is no object"):
        assert words in names, words


def test_the_listing_and_a_directory_are_read_on_their_indexes():
    """The listing's statement is planned on idx_ub_listing, a work folder
    directory's files and their counts on idx_ub_dir, the work folder's
    files on idx_ub_work_folder, as their readers write them (INDEXED BY,
    +parent_id), before and after db_maintenance's PRAGMA optimize has
    statistics in which idx_ub_parent gives few rows a parent."""
    conn = _table()
    rows = [("root", None, "{}"), ("trash", None, "{}")]
    for i in range(40):
        rows.append((f"l{i}", "root", json.dumps({"folders": ["L1"]})))
        rows += [(f"l{i}b{j}", f"l{i}", "{}") for j in range(20)]
    for i in range(1200):
        rows.append((f"f{i}", "root", json.dumps({"work_folder": "W1", "file_name": f"{i}.md",
                                                  "folders": [f"D{i % 30}"]})))
        rows += [(f"f{i}b{j}", f"f{i}", "{}") for j in range(i % 3)]
    conn.executemany("INSERT INTO unified_blocks (id, parent_id, position, properties, created_at, updated_at, "
                     "page_id) VALUES (?, ?, 'a0', ?, 't', 't', ?)", [(i, p, props, i) for i, p, props in rows])

    def plan(sql, args=()):
        return " ".join(r[-1] for r in conn.execute(f"EXPLAIN QUERY PLAN {sql}", args))

    for analyzed in (False, True):
        if analyzed:
            conn.execute("PRAGMA analysis_limit = 1000")
            conn.execute("PRAGMA optimize = 0x10002")
            assert conn.execute("SELECT 1 FROM sqlite_stat1 WHERE idx = 'idx_ub_parent'").fetchone()
        assert "idx_ub_listing" in plan(f"SELECT id FROM unified_blocks INDEXED BY idx_ub_listing WHERE "
                                        f"parent_id = 'root' AND {SQL_IN_LISTING} ORDER BY position ASC")
        assert "idx_ub_dir" in plan("SELECT id FROM unified_blocks INDEXED BY idx_ub_dir WHERE parent_id = 'root' "
                                    "AND work_folder IS NOT NULL AND first_folder = ?", ("D1",))
        assert "idx_ub_dir" in plan("SELECT first_folder, COUNT(*) FROM unified_blocks INDEXED BY idx_ub_dir "
                                    "WHERE parent_id = 'root' AND work_folder IS NOT NULL GROUP BY first_folder")
        assert "idx_ub_work_folder" in plan("SELECT id FROM unified_blocks WHERE work_folder = ? "
                                            "AND +parent_id = 'root'", ("W1",))
    assert conn.execute(f"SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root' AND {SQL_IN_LISTING}"
                        ).fetchone() == (40,)


def _tree():
    """L1 > L1a; W1 (pulls) > D1 > D1a, W1 > D2; H > W2 (in the library) >
    D3; and a mark that is no object, which is none."""
    rows = [("L1", "folders", "Library", "{}"), ("L1a", "L1", "Sub", {}),
            ("W1", "folders", "Paper", json.dumps({"vault": {"pull": True, "library": False}})),
            ("D1", "W1", "figures", "{}"), ("D1a", "D1", "old", "{}"), ("D2", "W1", "src", None),
            ("H", "folders", "Holder", "{}"), ("W2", "H", "Thesis", {"vault": {"pull": 1, "library": True}}),
            ("D3", "W2", "ch1", "{}"), ("X", "folders", "Odd", {"vault": "yes"})]
    return marks.Tree.of(rows)


def test_a_folders_work_folder_is_the_nearest_mark_on_its_chain():
    tree = _tree()
    assert [tree.work_of(f) for f in ("W1", "D1", "D1a", "D2", "L1", "L1a", "H", "W2", "D3", "X", "Gone", "folders")] \
        == ["W1", "W1", "W1", "W1", "", "", "", "W2", "W2", "", "", ""]
    assert tree.mark("W1") == {"pull": True, "library": False}
    assert tree.mark("W2") == {"pull": False, "library": True}  # only true is true
    assert tree.mark("D1") is None and tree.mark("X") is None
    assert tree.roots() == ["W1", "W2"]
    assert tree.subtree("W1") == {"W1", "D1", "D1a", "D2"} and tree.subtree("Gone") == set()
    assert tree.subtree("H") == {"H", "W2", "D3"}
    assert "D1" in tree and "Gone" not in tree and tree.names["D2"] == "src"


def test_filing_targets():
    tree = _tree()
    assert marks.filing_targets(tree) == ["L1", "L1a", "H", "X"]


def test_a_cycle_in_the_parents_ends_the_walk():
    tree = marks.Tree.of([("A", "B", "", "{}"), ("B", "A", "", "{}")])
    assert tree.work_of("A") == "" and tree.subtree("A") == {"A", "B"}


def test_a_marked_folder_is_read_from_the_workspace():
    """conftest.mark_work_folder writes the mark as the vault does; the tree
    a batch reads (Tree.read) carries it, and a folder below it is in it."""
    make_user("wfl_owner", "pw-wfl-1")
    owner = login("wfl_owner", "pw-wfl-1")
    w = make_folder(owner, "WFL Paper")
    figures = make_folder(owner, "WFL Paper/figures")
    plain = make_folder(owner, "WFL Library")
    assert mark_work_folder(owner, w, library=True) == {"pull": True, "library": True}
    with connect_pages_db(workspace_of("wfl_owner")) as conn:
        tree = marks.Tree.read(conn)
    assert (tree.work_of(figures), tree.work_of(plain), tree.mark(w)) == (w, "", {"pull": True, "library": True})
    assert tree.names[figures] == "figures" and w in tree.roots()
    assert work_props(w, "main.pdf", directory=figures, library=True) == {
        "work_folder": w, "file_name": "main.pdf", "folders": [figures], "library": True}


def test_follows_needs_a_note_a_mark_and_pull():
    note = {"work_folder": "W1", "file_name": "n.md", "folders": ["D1"]}
    assert library.follows(note, {"pull": True, "library": False})
    assert not library.follows(note, {"pull": False, "library": True})
    assert not library.follows(note, None)
    assert not library.follows({**note, "doc": {"format": "pdf", "hash": "p1"}}, {"pull": True})
    assert not library.follows({"folders": ["D1"]}, {"pull": True})
