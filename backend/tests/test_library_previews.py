"""The library listing's card previews (GET /api/blocks/root/children): one
index seek per listed page instead of a window query over every child of
every page — and the same text the window query gave, for every page shape:
more than five children, leading highlights, empty and whitespace-only
blocks, nested blocks (not counted), long text cut at PREVIEW_CHARS, no
children at all, and a folder share's listing."""

import inspect
from contextlib import closing

import pytest

from conftest import login, make_folder, make_page, make_user, workspace_of
from gamma.db import connect_pages_db
from gamma.routers import blocks as blocks_router

USER, PASSWORD = "lpv_owner", "pw-lpv-1"


def _window_previews(conn) -> dict:
    """The old query, kept here as the reference."""
    rows = conn.execute(
        f"""
        SELECT parent_id, content FROM (
            SELECT c.parent_id, c.content,
                   ROW_NUMBER() OVER (PARTITION BY c.parent_id ORDER BY c.position) AS rn
            FROM unified_blocks c
            JOIN unified_blocks p ON p.id = c.parent_id AND p.parent_id = 'root'
            WHERE c.content != ''
              AND json_type(c.properties, '$.pdf_position') IS NOT 'object'
        ) WHERE rn <= {blocks_router._PREVIEW_BLOCKS}
        ORDER BY parent_id, rn
        """).fetchall()
    previews: dict = {}
    for page_id, content in rows:
        current = previews.get(page_id, "")
        if len(current) >= blocks_router.PREVIEW_CHARS:
            continue
        piece = " ".join((content or "").split())
        previews[page_id] = (current + " · " + piece) if current else piece
    return {k: v[:blocks_router.PREVIEW_CHARS] for k, v in previews.items()}


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    c = login(USER, PASSWORD)

    def fill(title, blocks, folder=""):
        page = make_page(c, title, properties={"folders": [make_folder(c, folder)]} if folder else None)
        r = c.put(f"/api/blocks/{page['id']}/children", json={"blocks": blocks})
        assert r.status_code == 200, r.text
        return page["id"]

    hl = {"pdf_position": {"pageNumber": 1}}
    fill("Many children", [{"content": f"note number {i}"} for i in range(9)], folder="shared/deep")
    fill("Leading highlights", [{"content": f"quoted {i}", "properties": hl} for i in range(8)]
         + [{"content": "first real note"}, {"content": "second real note"}], folder="shared")
    fill("Blank blocks", [{"content": ""}, {"content": "  spaced\n\tout   text  "}, {"content": ""},
                          {"content": "after the blanks"}])
    fill("Nested", [{"content": "parent", "children": [{"content": "child is not a preview"}]},
                    {"content": "sibling"}])
    fill("Long text", [{"content": "word " * 80}, {"content": "never reached"}])
    fill("Highlights only", [{"content": "only a quote", "properties": hl}])
    make_page(c, "Empty page")
    return c


def test_previews_equal_the_window_query(owner):
    r = owner.get("/api/blocks/root/children")
    assert r.status_code == 200, r.text
    listed = {p["id"]: p["preview"] for p in r.json()["children"]}
    with closing(connect_pages_db(workspace_of(USER))) as conn:
        expected = _window_previews(conn)
    assert listed == {page_id: expected.get(page_id, "") for page_id in listed}
    by_title = {p["content"]: p["preview"] for p in r.json()["children"]}
    assert by_title["Leading highlights"] == "first real note · second real note"
    assert by_title["Nested"] == "parent · sibling"
    assert by_title["Blank blocks"] == "spaced out text · after the blanks"
    assert len(by_title["Long text"]) == blocks_router.PREVIEW_CHARS
    assert by_title["Highlights only"] == "" and by_title["Empty page"] == ""


def test_a_folder_share_lists_its_pages_with_the_same_previews(owner, anon):
    r = owner.post(f"/api/share/folder/{make_folder(owner, 'shared')}", json={"audience": "anyone", "role": "view"})
    assert r.status_code == 200, r.text
    listing = anon.get("/api/blocks/root/children", params={"share": r.json()["token"]})
    assert listing.status_code == 200, listing.text
    titles = {p["content"]: p["preview"] for p in listing.json()["children"]}
    assert set(titles) == {"Many children", "Leading highlights"}
    assert titles["Leading highlights"] == "first real note · second real note"
    assert titles["Many children"] == " · ".join(f"note number {i}" for i in range(5))


def test_previews_seek_the_parent_index():
    with closing(connect_pages_db(workspace_of(USER))) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT content FROM unified_blocks WHERE parent_id = ? AND content != '' "
            "AND kind != 'highlight' ORDER BY position LIMIT ?", ("x", 5)).fetchall()
    assert any("idx_ub_parent" in row[-1] for row in plan)


def test_the_listing_runs_in_the_threadpool():
    assert not inspect.iscoroutinefunction(blocks_router.ub_get_children)
