"""The block table's typed hot fields (migration step 26, db.BLOCK_HOT_COLUMNS):
``page_id`` kept by every writer — op inserts, moves within and across
pages, a subtree replace, imports, Recently deleted and back — and the
generated ``kind`` / ``doc_id`` the readers filter on; the block dict
carrying both read-only; the library listing's page summaries. The suite's
autouse ``_page_ids_kept`` checks every written workspace against the
parent walk as well; here each writer is checked by name."""

import json

import pytest

from conftest import login, make_folder, make_label, make_user, page_id_drift, workspace_of
from gamma.blocks_store import BLOCK_COLUMNS, block_kind, fetch_subtree, page_for_doc, page_root_id, trash_entry
from gamma.db import connect_pages_db
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
    page = _page(owner, "Kinds", {"doc_id": "c" * 24})
    assert _row(page["id"]) == (page["id"], "page", "c" * 24)
    rect = {"x1": 10, "y1": 20, "x2": 110, "y2": 32}
    pos = {"pageNumber": 1, "width": 612, "height": 792, "boundingRect": rect, "rects": [rect]}
    # A highlight is a block with a pdf_position, tested after the ink,
    # text-box, sheet and link cases: an ink group and a link region carry
    # one too.
    cases = {
        "bcNote": ({}, "note"),
        "bcHl": ({"pdf_position": pos, "quote": "q"}, "highlight"),
        "bcPageOnly": ({"pdf_position": {"pageNumber": 2}}, "highlight"),
        "bcLink": ({"pdf_position": pos, "link_url": "", "link_page_id": page["id"]}, "link"),
        "bcInk": ({"ink_url": "", "pdf_position": pos}, "ink"),
        "bcBox": ({"text_box": {"x": 1}, "pdf_page": 1}, "text_box"),
        "bcBoxPlaced": ({"text_box": {"x": 1}, "pdf_position": pos}, "text_box"),
        "bcSheet": ({"sheet": {"size": "a4"}, "pdf_position": pos}, "sheet"),
        "bcNoPos": ({"pdf_position": None, "quote": "q"}, "note"),
        "bcOldId": ({"highlight_id": "h1", "pdf_page": 1}, "note"),  # no position: no highlight
        "bcFileBlock": ({"doc_id": "e" * 24}, "note"),
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
    page = _page(owner, "Summarized", {
        "doc_id": "a" * 24, "source_url": "/api/uploads/x.pdf", "original_filename": "x.pdf", **filed,
        "pinned": True, "seeded": "welcome", "meta": meta, "bibtex": "@article{x}",
        "ppt_cite": "Author 2024", "summary": "a summary", "auto_title": "x.pdf", "web_url": "https://x"})
    _ops(owner, page["id"], [{"op": "insert", "id": "bcPreview", "parent": page["id"], "content": "first note"}])
    entry = next(p for p in owner.get("/api/blocks/root/children").json()["children"] if p["id"] == page["id"])
    assert set(entry) == {"id", "content", "position", "created_at", "updated_at", "preview", "properties"}
    assert entry["preview"] == "first note" and entry["content"] == "Summarized"
    assert entry["properties"] == {
        "doc_id": "a" * 24, "source_url": "/api/uploads/x.pdf", "original_filename": "x.pdf", **filed,
        "pinned": True, "seeded": "welcome",
        "meta": {k: meta[k] for k in blocks_router.SUMMARY_META}}
    bare = _page(owner, "Bare")
    entry = next(p for p in owner.get("/api/blocks/root/children").json()["children"] if p["id"] == bare["id"])
    assert entry["properties"] == {} and entry["preview"] == ""
    # the page itself still has everything
    assert owner.get(f"/api/blocks/{page['id']}").json()["properties"]["bibtex"] == "@article{x}"


# The dict's ``kind`` is computed in Python from the parsed properties
# (blocks_store.block_kind) while the generated column stays for the
# filters: both must say the same for every shape a row can have.
_KIND_SHAPES = [
    ({}, "note"),
    ({"ink_url": "/api/uploads/x.gamma-ink"}, "ink"),
    ({"ink_url": None}, "ink"),                       # the key alone makes it ink, as json_type does
    ({"text_box": {"x": 1}}, "text_box"),
    ({"text_box": "no"}, "note"),
    ({"sheet": {}}, "sheet"),
    ({"link_url": "https://example.org"}, "link"),
    ({"link_url": ""}, "note"),
    ({"link_url": None}, "note"),
    ({"link_page_id": "p1"}, "link"),
    ({"link_page_id": "", "link_url": ""}, "note"),
    ({"link_url": 0}, "link"),                        # a number is not the empty string
    ({"pdf_position": {"pageNumber": 1}}, "highlight"),
    ({"pdf_position": "1"}, "note"),
    ({"text_box": {}, "pdf_position": {}}, "text_box"),   # the first rule wins, in the column's order
    ({"link_url": "u", "pdf_position": {}}, "link"),
]


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
        conn.commit()
    assert block_kind(None, "", {}) is None
    assert block_kind("root", "p", {}) == "page" and block_kind("trash", "p", {}) == "page"
    assert block_kind("f1", "folders", {}) == "folder" and block_kind("labels", "labels", {}) == "label"
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
