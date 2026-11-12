"""The block table's typed hot fields (migration step 26, db.BLOCK_HOT_COLUMNS):
``page_id`` kept by every writer — op inserts, moves within and across
pages, a subtree replace, imports, Recently deleted and back — and the
generated ``kind`` / ``doc_id`` the readers filter on; the block dict
carrying both read-only; the library listing's page summaries; a
generated column of an older definition rebuilt with every index that
names it (normalize.block_columns). The suite's
autouse ``_page_ids_kept`` checks every written workspace against the
parent walk as well; here each writer is checked by name."""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import doc_props, login, make_folder, make_label, make_user, page_id_drift, workspace_of
from gamma.blocks_store import BLOCK_COLUMNS, block_kind, fetch_subtree, page_for_doc, page_root_id, trash_entry
from gamma.db import BLOCK_HOT_COLUMNS, BLOCK_HOT_INDEXES, PAGES_SCHEMA, connect_pages_db
from gamma.normalize import block_columns
from gamma.highlights import is_highlight, is_ink, is_link
from gamma.picture import is_picture
from gamma.routers import blocks as blocks_router

USER, PASSWORD = "bcol_owner", "pw-bcol-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _page(c, title, props=None):
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": props or {}})
    assert r.status_code == 200, r.text
    return r.json()


def _ops(c, page, ops):
    r = c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": ops})
    assert r.status_code == 200, r.text
    return r.json()


def _row(block_id):
    with connect_pages_db(workspace_of(USER)) as conn:
        return conn.execute("SELECT page_id, kind, doc_id FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()


def _no_drift():
    with connect_pages_db(workspace_of(USER)) as conn:
        assert page_id_drift(conn) == []


def test_a_page_and_its_blocks_carry_the_page_id(owner):
    page = _page(owner, "Hot fields")
    assert (page["page_id"], page["kind"]) == (page["id"], "page")
    _ops(owner, page["id"], [
        {"op": "insert", "id": "bcTop", "parent": page["id"], "content": "top"},
        {"op": "insert", "id": "bcKid", "parent": "bcTop", "content": "nested"}])
    assert _row("bcTop")[0] == _row("bcKid")[0] == page["id"]
    nested = owner.post("/api/blocks", json={"parent_id": "bcKid", "content": "deeper"}).json()
    assert (nested["page_id"], nested["kind"], nested["parent_id"]) == (page["id"], "note", "bcKid")
    got = owner.get("/api/blocks/bcKid").json()
    assert (got["page_id"], got["kind"]) == (page["id"], "note")
    _no_drift()


def test_kind_and_doc_id_follow_the_properties(owner):
    page = _page(owner, "Kinds", doc_props("c" * 24))
    assert _row(page["id"]) == (page["id"], "page", "c" * 24)
    # the column is the hash a row owns: none for a copy or an address alone
    copy = _page(owner, "A copy", doc_props(url=f"/api/uploads/{'c' * 24}.pdf"))
    shown = _page(owner, "Shown", doc_props(url="https://x.test/p.pdf"))
    cached = _page(owner, "Cached", doc_props("d" * 24, url="https://x.test/q.pdf"))
    assert copy["properties"]["doc"]["copy"] is True
    assert (_row(copy["id"])[2], _row(shown["id"])[2], _row(cached["id"])[2]) == (None, None, "d" * 24)
    rect = {"x1": 10, "y1": 20, "x2": 110, "y2": 32}
    pos = {"pageNumber": 1, "width": 612, "height": 792, "boundingRect": rect, "rects": [rect]}
    # A highlight is a block with a pdf_position, tested after the ink,
    # text-box, picture, sheet and link cases: an ink group and a link
    # region carry one too.
    pic = {"url": "/api/uploads/3f2a9c.png", "x": 10, "y": 20, "w": 144, "h": 96}
    cases = {
        "bcNote": ({}, "note"),
        "bcHl": ({"pdf_position": pos, "quote": "q"}, "highlight"),
        "bcPageOnly": ({"pdf_position": {"pageNumber": 2}}, "highlight"),
        "bcLink": ({"pdf_position": pos, "link_url": "", "link_page_id": page["id"]}, "link"),
        "bcInk": ({"ink_url": "", "pdf_position": pos}, "ink"),
        "bcBox": ({"text_box": {"x": 1}, "pdf_page": 1}, "text_box"),
        "bcBoxPlaced": ({"text_box": {"x": 1}, "pdf_position": pos}, "text_box"),
        # a picture is tested after the text box and before the sheet
        "bcPic": ({"picture": pic, "pdf_page": 1}, "picture"),
        "bcPicPlaced": ({"picture": pic, "pdf_position": pos, "sheet": {"size": "a4"}}, "picture"),
        "bcPicBox": ({"picture": pic, "text_box": {"x": 1}}, "text_box"),
        "bcPicGone": ({"picture": {"url": "/api/uploads/gone.svg"}}, "picture"),  # still a picture, drawn nowhere
        "bcSheet": ({"sheet": {"size": "a4"}, "pdf_position": pos}, "sheet"),
        # a recording is tested after the sheet and before the link
        "bcRec": ({"recording": {"started": 1, "segments": [], "state": "done"}, "link_page_id": page["id"]},
                  "recording"),
        "bcRecNull": ({"recording": None}, "note"),
        "bcNoPos": ({"pdf_position": None, "quote": "q"}, "note"),
        "bcPdfPage": ({"pdf_page": 1}, "note"),  # a page number alone, no position: no highlight
        "bcFileBlock": (doc_props("e" * 24), "note"),
    }
    _ops(owner, page["id"], [{"op": "insert", "id": bid, "parent": page["id"], "content": bid, "props": props}
                             for bid, (props, _) in cases.items()])
    assert {bid: _row(bid)[1] for bid in cases} == {bid: kind for bid, (_, kind) in cases.items()}
    assert _row("bcFileBlock")[2] == "e" * 24
    # a property change moves the generated kind with it
    _ops(owner, page["id"], [{"op": "set", "id": "bcNote", "props": {"pdf_position": {"pageNumber": 3}}}])
    assert _row("bcNote")[1] == "highlight"
    with connect_pages_db(workspace_of(USER)) as conn:
        assert page_for_doc(conn, "c" * 24)[0] == page["id"]
        assert page_for_doc(conn, "e" * 24) is None  # a nested block never counts
    # the notes search reports the stored kind
    kinds = {b["id"]: b["kind"] for b in owner.get("/api/block-search", params={"ids": ",".join(cases)}).json()["blocks"]}
    assert kinds["bcBox"] == "text_box" and kinds["bcLink"] == "link" and kinds["bcInk"] == "ink"
    assert kinds["bcRec"] == "recording" and kinds["bcPic"] == "picture"


def test_folders_and_labels_are_kinds_of_their_trees(owner):
    folder, sub, label = make_folder(owner, "bc/top"), make_folder(owner, "bc/top/sub"), make_label(owner, "bc")
    assert _row(folder)[:2] == ("folders", "folder") and _row(sub)[:2] == ("folders", "folder")
    assert _row(label)[:2] == ("labels", "label")
    assert _row("folders")[:2] == ("", None) and _row("labels")[:2] == ("", None)
    # a folder's properties never make it anything else
    _ops(owner, "folders", [{"op": "set", "id": sub, "props": {"link_url": "https://x", "pinned": "2026"}}])
    assert _row(sub)[1] == "folder"
    _no_drift()


def test_a_move_to_another_page_moves_the_subtree_page_id(owner):
    src, dst = _page(owner, "From"), _page(owner, "To")
    _ops(owner, src["id"], [
        {"op": "insert", "id": "bcMover", "parent": src["id"], "content": "moving"},
        {"op": "insert", "id": "bcMoverKid", "parent": "bcMover", "content": "along"},
        {"op": "insert", "id": "bcStays", "parent": src["id"], "content": "stays"}])
    # within the page: nothing about the page changes
    _ops(owner, src["id"], [{"op": "move", "id": "bcMoverKid", "parent": "bcStays"}])
    assert _row("bcMoverKid")[0] == src["id"]
    _ops(owner, src["id"], [{"op": "move", "id": "bcMoverKid", "parent": "bcMover"}])
    r = owner.post("/api/blocks/bcMover/reorder", json={"parent_id": dst["id"]})
    assert r.status_code == 200, r.text
    assert _row("bcMover")[0] == _row("bcMoverKid")[0] == dst["id"]
    assert _row("bcStays")[0] == src["id"]
    with connect_pages_db(workspace_of(USER)) as conn:
        assert page_root_id(conn, "bcMoverKid") == dst["id"]
    _no_drift()


def test_recently_deleted_keeps_the_page_ids(owner):
    page = _page(owner, "Deleted and back")
    _ops(owner, page["id"], [{"op": "insert", "id": "bcTrashed", "parent": page["id"], "content": "inside"}])
    assert owner.delete(f"/api/blocks/{page['id']}").status_code == 200
    assert _row(page["id"]) == (page["id"], "page", None) and _row("bcTrashed")[0] == page["id"]
    with connect_pages_db(workspace_of(USER)) as conn:
        assert page_root_id(conn, "bcTrashed") is None  # a trashed page reads as gone
        assert trash_entry(conn, "bcTrashed")["id"] == page["id"]
    r = owner.get("/api/blocks/bcTrashed")
    assert r.status_code == 404 and r.json()["trashed"]["id"] == page["id"]
    assert owner.post(f"/api/trash/{page['id']}/restore").status_code == 200
    assert owner.get("/api/blocks/bcTrashed").json()["page_id"] == page["id"]
    _no_drift()


def test_a_subtree_replace_and_an_import_write_the_page_id(owner):
    page = _page(owner, "Replaced")
    _ops(owner, page["id"], [{"op": "insert", "id": "bcHolder", "parent": page["id"], "content": "holder"}])
    r = owner.put("/api/blocks/bcHolder/children", json={"blocks": [
        {"id": "bcNew1", "content": "one", "children": [{"id": "bcNew2", "content": "two"}]}]})
    assert r.status_code == 200, r.text
    assert _row("bcNew1")[0] == _row("bcNew2")[0] == page["id"]
    r = owner.post("/api/import/markdown", files={"file": ("n.md", "# Imported\n\n- a\n  - b\n", "text/markdown")})
    assert r.status_code == 200, r.text
    imported = r.json()["block_id"]
    with connect_pages_db(workspace_of(USER)) as conn:
        rows = fetch_subtree(conn, imported)
    assert len(rows) > 2 and {r[7] for r in rows} == {imported}  # (BLOCK_COLUMNS: page_id)
    _no_drift()


def test_the_listing_carries_page_summaries(owner):
    meta = {"title": "On cavities", "authors": ["A. Author", "B. Author"], "year": 2024, "venue": "PRX",
            "volume": "14", "doi": "10.1/x", "arxiv_id": "2401.00001", "publisher": "APS", "abstract": "long"}
    filed = {"folders": [make_folder(owner, "bc/lab")], "labels": [make_label(owner, "read")]}
    doc = doc_props("a" * 24, name="x.pdf", at="path")  # read in place: the menus read doc.at
    page = _page(owner, "Summarized", {
        **doc, **filed, "pinned": True, "seeded": "welcome", "opened_files": True, "meta": meta,
        "bibtex": "@article{x}", "ppt_cite": "Author 2024", "summary": "a summary", "auto_title": "x.pdf",
        "web_url": "https://x"})
    _ops(owner, page["id"], [{"op": "insert", "id": "bcPreview", "parent": page["id"], "content": "first note"}])
    entry = next(p for p in owner.get("/api/blocks/root/children").json()["children"] if p["id"] == page["id"])
    assert set(entry) == {"id", "content", "position", "created_at", "updated_at", "preview", "parent",
                          "properties"}
    assert entry["preview"] == "first note" and entry["content"] == "Summarized" and entry["parent"] == ""
    assert entry["properties"] == {
        **doc, **filed, "pinned": True, "seeded": "welcome", "opened_files": True,
        "meta": {k: meta[k] for k in blocks_router.SUMMARY_META}}
    assert not {"doc_id", "source_url", "original_filename", "linked_file"} & set(entry["properties"])
    bare = _page(owner, "Bare")
    entry = next(p for p in owner.get("/api/blocks/root/children").json()["children"] if p["id"] == bare["id"])
    assert entry["properties"] == {} and entry["preview"] == ""
    # the page itself still has everything
    assert owner.get(f"/api/blocks/{page['id']}").json()["properties"]["bibtex"] == "@article{x}"


# The dict's ``kind`` is computed in Python from the parsed properties
# (blocks_store.block_kind) while the generated column stays for the
# filters: both must say the same for every shape a row can have, and so
# must the client's blockKind (frontend/tests/blockProps.test.mjs), which
# reads the same cases: tests/shared/blockkind.json.
_KINDS = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "blockkind.json").read_text(encoding="utf-8"))
_KIND_SHAPES = [(case["props"], case["kind"]) for case in _KINDS["props"]]


def test_the_python_kind_agrees_with_the_generated_column(owner):
    page = _page(owner, "Kinds")
    with connect_pages_db(workspace_of(USER)) as conn:
        for n, (props, expected) in enumerate(_KIND_SHAPES):
            block_id = f"bcKind{n}"
            conn.execute(
                "INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at, page_id) "
                "VALUES (?, ?, ?, '', ?, 't', 't', ?)", (block_id, page["id"], f"a{n:02d}", json.dumps(props), page["id"]))
            stored = conn.execute("SELECT kind FROM unified_blocks WHERE id = ?", (block_id,)).fetchone()[0]
            assert stored == expected, (props, stored)
            assert block_kind(page["id"], page["id"], props) == expected, props
            # the ink and picture tests agree with the column, and neither is a highlight
            assert is_ink(props) == (expected == "ink"), props
            assert not (expected == "ink" and is_highlight(props)), props
            assert is_picture(props) == (expected == "picture"), props
            assert not (expected == "picture" and is_highlight(props)), props
            # the exporters' link test agrees with it once the kinds before link are ruled out
            if expected in ("link", "highlight", "note"):
                assert is_link(props) == (expected == "link"), props
        conn.commit()
    assert block_kind(None, "", {}) is None
    assert not is_ink(None) and not is_ink([]) and not is_ink("ink_url")
    for case in _KINDS["tree"]:
        assert block_kind(case["parent_id"], case["page_id"], case["props"]) == case["kind"], case["note"]
    for n, (props, expected) in enumerate(_KIND_SHAPES):
        assert owner.get(f"/api/blocks/bcKind{n}").json()["kind"] == expected
    _no_drift()


def test_a_page_subtree_by_page_id_is_the_recursive_walk(owner):
    """A page's rows come by their ``page_id`` (one indexed read); they are
    the rows the walk down the parents finds, the page first."""
    page = _page(owner, "Walk")
    _ops(owner, page["id"], [
        {"op": "insert", "id": "bcW1", "parent": page["id"], "content": "one"},
        {"op": "insert", "id": "bcW2", "parent": "bcW1", "content": "two"},
        {"op": "insert", "id": "bcW3", "parent": "bcW2", "content": "three"},
        {"op": "insert", "id": "bcW4", "parent": page["id"], "content": "four"}])
    with connect_pages_db(workspace_of(USER)) as conn:
        rows = fetch_subtree(conn, page["id"])
        walked = conn.execute(f"""
            WITH RECURSIVE subtree AS (
                SELECT {BLOCK_COLUMNS} FROM unified_blocks WHERE id = ?
                UNION ALL
                SELECT {", ".join("ub." + c for c in BLOCK_COLUMNS.split(", "))}
                FROM unified_blocks ub JOIN subtree s ON ub.parent_id = s.id)
            SELECT {BLOCK_COLUMNS} FROM subtree""", (page["id"],)).fetchall()
        assert rows[0][0] == page["id"]
        assert sorted(rows) == sorted(walked) and len(rows) == 5
        assert {r[0] for r in fetch_subtree(conn, "bcW1")} == {"bcW1", "bcW2", "bcW3"}, "a block still walks"
        assert fetch_subtree(conn, "bcNone") == []
    tree = owner.get(f"/api/blocks/{page['id']}/subtree").json()["block"]
    assert [c["id"] for c in tree["children"]] == ["bcW1", "bcW4"]
    assert tree["children"][0]["children"][0]["children"][0]["id"] == "bcW3"


def test_a_stale_column_takes_every_index_that_names_it_and_gets_them_back():
    """``normalize.block_columns`` over a ``work_folder`` of an older
    definition: the indexes that name it anywhere, in their condition or
    after another column (``idx_ub_listing``, ``idx_ub_dir``,
    ``idx_ub_work_folder``, and one of no hot list), are dropped before the
    column and made again after it; a rerun changes nothing."""
    older = "work_folder TEXT GENERATED ALWAYS AS (json_extract(properties, '$.work_folder')) VIRTUAL"
    table = next(s for s in PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS unified_blocks"))
    conn = sqlite3.connect(":memory:")
    conn.execute(table.replace(BLOCK_HOT_COLUMNS["work_folder"], older))
    for stmt in BLOCK_HOT_INDEXES:
        conn.execute(stmt)
    conn.execute("CREATE INDEX idx_other ON unified_blocks(created_at) WHERE work_folder IS NOT NULL")
    conn.execute("INSERT INTO unified_blocks (id, parent_id, position, properties, created_at, updated_at, page_id) "
                 "VALUES ('p', 'root', 'a0', '{\"work_folder\": \"\", \"folders\": [\"L1\"]}', 't', 't', 'p')")
    conn.commit()
    assert conn.execute("SELECT work_folder FROM unified_blocks").fetchone() == ("",)
    assert block_columns(conn)
    assert conn.execute("SELECT work_folder, first_folder FROM unified_blocks").fetchone() == (None, "L1")
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_ub_listing", "idx_ub_dir", "idx_ub_work_folder", "idx_ub_deleted_with", "idx_other"} <= indexes
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0]
    assert BLOCK_HOT_COLUMNS["work_folder"] in ddl and older not in ddl
    assert not block_columns(conn)
