"""PUT /blocks/{id}/children is refused for the library itself and checks
what it writes like an op would; a subtree replace, a page deletion and a
cross-page move are each one transaction — a failure half way leaves
everything as it was (blocks_store.delete_subtree / delete_children start
with DELETE, so they run inside the caller's transaction)."""

import sqlite3
from contextlib import closing

import pytest

from conftest import login, make_user, workspace_of
from gamma import blocks_store, ops
from gamma.db import PAGES_SCHEMA, connect_pages_db, register_functions


@pytest.fixture
def dee():
    make_user("pc_dee", "pw")
    return login("pc_dee", "pw"), workspace_of("pc_dee")


def _page(c, title, notes=()):
    pid = c.post("/api/pages", json={"title": title}).json()["id"]
    if notes:
        r = c.post(f"/api/pages/{pid}/ops", json={"client": "t", "ops": [
            {"op": "insert", "id": bid, "parent": pid, "content": text} for bid, text in notes]})
        assert r.status_code == 200, r.text
    return pid


def _children(ws, block_id):
    with closing(connect_pages_db(ws)) as conn:
        return [r[0] for r in conn.execute(
            "SELECT id FROM unified_blocks WHERE parent_id = ? ORDER BY position", (block_id,))]


def test_the_library_itself_cannot_be_replaced(dee):
    c, ws = dee
    _page(c, "keep me")
    before = _children(ws, "root")
    r = c.put("/api/blocks/root/children", json={"blocks": []})
    assert r.status_code == 400
    assert _children(ws, "root") == before and before


def test_put_children_checks_what_it_writes(dee):
    c, ws = dee
    page = _page(c, "checked", [("pcK1", "precious")])
    for blocks, status in (([{"id": "has space", "content": "x"}], 400),
                           ([{"id": "a" * 65, "content": "x"}], 400),
                           ([{"id": "trash", "content": "x"}], 400),
                           (["not an object"], 400),
                           ([{"content": {"not": "text"}}], 400),
                           ([{"content": "x" * (ops.MAX_CONTENT + 1)}], 413)):
        assert c.put(f"/api/blocks/{page}/children", json={"blocks": blocks}).status_code == status
    assert _children(ws, page) == ["pcK1"]


def test_a_failed_replace_leaves_the_old_children(dee):
    c, ws = dee
    page = _page(c, "notes", [(f"pcN{i}", f"precious note {i}") for i in range(3)])
    _page(c, "other", [("pcTaken", "lives elsewhere")])
    r = c.put(f"/api/blocks/{page}/children", json={"blocks": [
        {"id": "pcFresh", "content": "new"}, {"id": "pcTaken", "content": "clashes"}]})
    assert r.status_code == 409
    assert _children(ws, page) == ["pcN0", "pcN1", "pcN2"]


def test_duplicating_a_page_still_works(dee):
    # the one UI caller: a fresh page filled with a copy of another's tree
    c, ws = dee
    copy = c.post("/api/blocks", json={"parent_id": "root", "content": "Paper (copy)"}).json()
    r = c.put(f"/api/blocks/{copy['id']}/children", json={"blocks": [
        {"id": "pcD1", "content": "one", "properties": {"pdf_position": {"pageNumber": 1}}, "parent_id": "x",
         "position": "a0", "children": [{"id": "pcD2", "content": "nested", "children": []}]},
        {"id": "pcD3", "content": "two"}]})
    assert r.status_code == 200 and r.json()["count"] == 3
    tree = c.get(f"/api/blocks/{copy['id']}/subtree").json()["block"]["children"]
    assert [b["id"] for b in tree] == ["pcD1", "pcD3"] and tree[0]["children"][0]["content"] == "nested"


def test_subtree_writes_run_inside_the_transaction(tmp_path):
    with closing(sqlite3.connect(str(tmp_path / "pages.db"))) as conn:
        register_functions(conn)
        for stmt in PAGES_SCHEMA:
            conn.execute(stmt)
        conn.executemany("INSERT INTO unified_blocks VALUES (?, ?, 'a0', '', '{}', 'now', 'now', ?)",
                         [("root", None, ""), ("p", "root", "p"), ("c1", "p", "p"), ("c2", "c1", "p")])
        conn.commit()
        blocks_store.delete_children(conn, "p")
        assert conn.in_transaction  # the implicit BEGIN opened before the delete
        conn.rollback()
        blocks_store.delete_subtree(conn, "p")
        assert conn.in_transaction
        conn.rollback()
        blocks_store.move_subtree_to_page(conn, "c1", "q")
        assert conn.in_transaction
        conn.rollback()
        assert dict(conn.execute("SELECT id, page_id FROM unified_blocks")) == {"root": "", "p": "p", "c1": "p",
                                                                                 "c2": "p"}


def test_a_page_deletion_that_fails_keeps_the_page(dee):
    c, ws = dee
    page = _page(c, "tombstone", [("pcT1", "a"), ("pcT2", "b")])
    with closing(connect_pages_db(ws)) as conn:
        # the change log's write fails (a full disk, a lock timeout) after the subtree went
        conn.execute("CREATE TEMP TRIGGER no_tombstone BEFORE UPDATE ON page_changes "
                     "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        with pytest.raises(sqlite3.DatabaseError):
            ops.delete_page(ws, conn, page, actor="pc_dee")
    assert _children(ws, page) == ["pcT1", "pcT2"]
    with closing(connect_pages_db(ws)) as conn:
        assert conn.execute("SELECT kind FROM page_changes WHERE page_id = ?", (page,)).fetchone() == ("live",)


def test_a_cross_page_move_that_fails_moves_nothing(dee, monkeypatch):
    c, ws = dee
    src = _page(c, "from", [("pcMv", "travelling")])
    dst = _page(c, "to")

    def log_fails(*a, **kw):
        raise RuntimeError("the op log could not be written")

    monkeypatch.setattr(ops, "record_ops", log_fails)
    with pytest.raises(RuntimeError):
        c.post("/api/blocks/pcMv/reorder", json={"parent_id": dst})
    assert _children(ws, src) == ["pcMv"] and _children(ws, dst) == []
