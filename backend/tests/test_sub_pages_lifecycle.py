"""Sub-pages through Recently deleted and the mirror (docs/research/sub-pages.md
"Trash", "Reach"): trashing a page takes the pages inside it along, each
stamped ``deleted_with`` it (ops.trash_page); Recently deleted lists the page
alone with how many went with it, and a 404 inside one of them names the
page (gamma/trash.py, blocks_store.trash_entry); a restore brings the tree
back, a page trashed on its own goes back inside a live parent or to the
library root (ops.restore_page); the deletion for good takes the tree too
(ops.delete_page: Delete permanently, Empty, the sweeper, a share host's
delete). A mirror round meets a parent's deletion before its sub-pages'
(sync_engine._round_order) and takes the cascade as it comes; a publication
of a page carries the pages inside it (sync_engine._reach,
publish.unpublish)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import login, make_folder, make_user, workspace_of
from test_mirror import Side, _pair, _sync, _transport  # noqa: F401 — the transport fixture applies here too
from test_publish import cloud, linked, publishing  # noqa: F401 — the share host's fixtures
from gamma import collab, ops, sync_engine, trash
from gamma.blocks_store import TRASH, newest_change_seq, page_parent
from gamma.db import connect_pages_db
from gamma.integrations import create_token

USER, PASSWORD = "spl_owner", "pw-spl-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


def _ws():
    return workspace_of(USER)


def _page(c, title, parent="", **body):
    r = c.post("/api/pages", json={"title": title, **({"parent_page": parent} if parent else {}), **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _tree(c, name):
    """``top`` with ``a`` (holding ``g``) and ``b`` inside it, made in that
    order: library order a, g, b."""
    top = _page(c, f"{name} top")
    a = _page(c, f"{name} a", top)
    g = _page(c, f"{name} g", a)
    b = _page(c, f"{name} b", top)
    return top, a, g, b


def _child(c, page_id, content):
    r = c.post("/api/blocks", json={"parent_id": page_id, "content": content})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _row(page_id, ws=None):
    """``(parent_id, properties)`` of the row, None when it is gone."""
    with connect_pages_db(ws or _ws()) as conn:
        row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    return (row[0], json.loads(row[1] or "{}")) if row else None


def _changes(ids, ws=None):
    """``{page id: (seq, kind)}`` of the pages' rows of the change log."""
    with connect_pages_db(ws or _ws()) as conn:
        return {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT page_id, seq, kind FROM page_changes WHERE page_id IN (SELECT value FROM json_each(?))",
            (json.dumps(list(ids)),))}


def _newest():
    with connect_pages_db(_ws()) as conn:
        return newest_change_seq(conn)


def _trashed(c):
    r = c.get("/api/trash")
    assert r.status_code == 200, r.text
    return {p["id"]: p for p in r.json()["pages"]}


def _listened(monkeypatch):
    """What the rooms hear and the commit listeners are told, recorded:
    ``(heard [(page, message type)], committed [page])``."""
    heard, committed = [], []
    monkeypatch.setattr(collab, "publish", lambda ws, page, msg, exclude="": heard.append((page, msg["t"])))
    monkeypatch.setattr(ops, "commit_listeners", [lambda ws, client, page: committed.append(page)])
    return heard, committed


def test_trashing_a_page_takes_the_pages_inside_it_along(owner, monkeypatch):
    top, a, g, b = _tree(owner, "Cascade")
    bystander = _page(owner, "Cascade bystander")
    note = _child(owner, g, "deep in the tree")
    before = _newest()
    heard, committed = _listened(monkeypatch)

    r = owner.delete(f"/api/blocks/{top}")
    assert r.status_code == 200, r.text
    assert r.json()["trashed"]["id"] == top
    stamps = _row(top)[1]
    assert _row(top)[0] == TRASH and "deleted_with" not in stamps and stamps["deleted_at"]
    for page in (a, g, b):
        parent_id, props = _row(page)
        assert parent_id == TRASH and props["deleted_with"] == top
        assert (props["deleted_at"], props["deleted_by"]) == (stamps["deleted_at"], stamps["deleted_by"])
        assert props["parent_page"] in (top, a)  # where it was is kept for the restore
    assert _row(bystander)[0] == "root"
    # the change log: the page first, then the pages inside it in library order
    log = _changes([top, a, g, b])
    assert [log[p] for p in (top, a, g, b)] == [(before + 1, "deleted"), (before + 2, "deleted"),
                                                (before + 3, "deleted"), (before + 4, "deleted")]
    assert heard == [(p, "trashed") for p in (top, a, g, b)] and committed == [top, a, g, b]

    # Recently deleted lists the page alone, with how many went with it
    listed = _trashed(owner)
    assert listed[top]["sub_pages"] == 3 and not {a, g, b} & set(listed)
    # a link into one of them names the page whose restore brings it back
    for block in (note, g):
        r = owner.get(f"/api/blocks/{block}")
        assert r.status_code == 404 and r.json()["trashed"]["id"] == top, r.text
    assert owner.get(f"/api/blocks/{g}/subtree").json()["trashed"]["title"] == "Cascade top"


def test_a_restore_brings_the_tree_back(owner, monkeypatch):
    top, a, g, b = _tree(owner, "Return")
    owner.delete(f"/api/blocks/{top}").raise_for_status()
    trashed = _newest()
    heard, committed = _listened(monkeypatch)

    r = owner.post(f"/api/trash/{top}/restore")
    assert r.status_code == 200, r.text
    for page, parent in ((top, None), (a, top), (g, a), (b, top)):
        parent_id, props = _row(page)
        assert parent_id == "root" and props.get("parent_page") == parent
        assert not {"deleted_at", "deleted_by", "deleted_with"} & set(props)
    # last in the library, in their trash order, each touched live after the page
    assert [p["id"] for p in owner.get("/api/blocks/root/children").json()["children"]][-4:] == [top, a, g, b]
    log = _changes([top, a, g, b])
    assert [log[p] for p in (top, a, g, b)] == [(trashed + n, "live") for n in (1, 2, 3, 4)]
    assert heard == [(p, "reload") for p in (top, a, g, b)] and committed == [top, a, g, b]
    with connect_pages_db(_ws()) as conn:
        assert page_parent(conn, g) == a
    family = owner.get(f"/api/pages/{top}/family").json()
    assert [p["id"] for p in family["sub_pages"]] == [a, b]
    assert _trashed(owner).keys().isdisjoint({top, a, g, b})


def test_a_page_trashed_on_its_own_goes_back_inside_a_live_parent_or_to_the_library(owner):
    top, a, g, b = _tree(owner, "Alone")
    # trashed alone, restored while its parent is live: inside it again
    owner.delete(f"/api/blocks/{b}").raise_for_status()
    assert "deleted_with" not in _row(b)[1] and _trashed(owner)[b]["sub_pages"] == 0
    owner.post(f"/api/trash/{b}/restore").raise_for_status()
    assert _row(b)[0] == "root" and _row(b)[1]["parent_page"] == top

    # trashed alone before its parent went: listed on its own, not counted with the parent
    owner.delete(f"/api/blocks/{b}").raise_for_status()
    owner.delete(f"/api/blocks/{top}").raise_for_status()
    listed = _trashed(owner)
    assert listed[top]["sub_pages"] == 2 and b in listed and a not in listed
    # restored while the parent is in Recently deleted: to the library root, the key removed
    owner.post(f"/api/trash/{b}/restore").raise_for_status()
    assert _row(b)[0] == "root" and "parent_page" not in _row(b)[1]
    # one of the pages that went with another, restored by itself, is a page trashed alone;
    # the page inside it, which went with the same page, comes back inside it
    owner.post(f"/api/trash/{a}/restore").raise_for_status()
    parent_id, props = _row(a)
    assert parent_id == "root" and "parent_page" not in props and "deleted_with" not in props
    parent_id, props = _row(g)
    assert parent_id == "root" and props["parent_page"] == a and "deleted_with" not in props
    assert _trashed(owner)[top]["sub_pages"] == 0
    owner.post(f"/api/trash/{top}/restore").raise_for_status()
    assert _row(g)[0] == "root" and _row(g)[1]["parent_page"] == a
    with connect_pages_db(_ws()) as conn:
        assert page_parent(conn, a) == "" and page_parent(conn, g) == a


def test_a_page_trashed_on_its_own_names_the_page_its_restore_puts_it_inside(owner):
    folder = make_folder(owner, "spl/named")
    top = _page(owner, "Named top", folders=[folder])
    a = _page(owner, "Named a", top)
    g = _page(owner, "Named g", a)
    # the delete's answer, the listing and a link's 404 name the live parent,
    # the folders its top is filed in beside it
    r = owner.delete(f"/api/blocks/{g}")
    assert r.status_code == 200, r.text
    assert (r.json()["trashed"]["parent"], r.json()["trashed"]["folders"]) == (a, [folder])
    entry = _trashed(owner)[g]
    assert (entry["parent"], entry["folders"]) == (a, [folder])
    assert owner.get(f"/api/blocks/{g}").json()["trashed"]["parent"] == a
    # a page deleted with its parent is the parent's entry, which goes back in the library
    owner.delete(f"/api/blocks/{top}").raise_for_status()
    listed = _trashed(owner)
    assert listed[top]["parent"] == "" and a not in listed
    # its parent gone too, the page alone comes back in the library: no parent named
    assert listed[g]["parent"] == "" and listed[g]["folders"] == []
    owner.post(f"/api/trash/{top}/restore").raise_for_status()
    assert _trashed(owner)[g]["parent"] == a
    owner.post(f"/api/trash/{g}/restore").raise_for_status()
    assert _row(g)[1]["parent_page"] == a


def _with_things(c, page_id):
    """A note, a comment, a chat and an op log on the page."""
    note = _child(c, page_id, "a note of its own")
    r = c.post(f"/api/pages/{page_id}/comments", json={"block_id": note, "body": "a comment"})
    assert r.status_code == 201, r.text
    c.put(f"/api/chats/{page_id}", json={"messages": [{"role": "user", "text": "hi"}]}).raise_for_status()
    return note


def _gone(page_id, note):
    with connect_pages_db(_ws()) as conn:
        for sql in ("SELECT 1 FROM unified_blocks WHERE id IN (?, ?)",
                    "SELECT 1 FROM comments WHERE page_id = ? OR block_id = ?",
                    "SELECT 1 FROM page_ops WHERE page_id IN (?, ?)",
                    "SELECT 1 FROM chats WHERE bucket IN (?, ?)"):
            if conn.execute(sql, (page_id, note)).fetchone():
                return False
    return True


def test_delete_permanently_empty_and_the_sweeper_take_the_tree(owner):
    top, a, g, b = _tree(owner, "Purge")
    notes = {page: _with_things(owner, page) for page in (a, g)}
    owner.delete(f"/api/blocks/{top}").raise_for_status()
    assert owner.delete(f"/api/trash/{top}").status_code == 200
    for page in (top, b):
        assert _row(page) is None
    assert all(_gone(page, note) for page, note in notes.items())
    assert _changes([a, g]) and {kind for _, kind in _changes([top, a, g, b]).values()} == {"deleted"}

    one, two = _tree(owner, "Empty one"), _tree(owner, "Empty two")
    for tree in (one, two):
        owner.delete(f"/api/blocks/{tree[0]}").raise_for_status()
    emptied = owner.delete("/api/trash").json()["deleted"]
    assert {one[0], two[0]} <= set(emptied) and not {*one[1:], *two[1:]} & set(emptied)
    assert all(_row(page) is None for page in (*one, *two))

    old = _tree(owner, "Swept")
    note = _with_things(owner, old[3])
    owner.delete(f"/api/blocks/{old[0]}").raise_for_status()
    later = datetime.now(timezone.utc) + timedelta(days=trash.KEEP_DAYS, minutes=5)
    swept = trash.purge_expired(_ws(), now=later)
    assert old[0] in swept and not set(old[1:]) & set(swept)
    assert all(_row(page) is None for page in old) and _gone(old[3], note)


def test_empty_reads_the_trash_once_on_one_connection(owner, monkeypatch):
    """Empty (and the sweeper, which walks the trash the same way) reads
    what went with each page from its one read of the trash
    (``trash._trashed``), never ``ops._went_with`` per page, and a page of
    the tree restored alone since that read is passed by."""
    trees = [_tree(owner, f"Once {n}") for n in range(3)]
    for tree in trees:
        owner.delete(f"/api/blocks/{tree[0]}").raise_for_status()
    asked, reads, conns = [], [], set()
    real_went, real_trashed, real_entry, real_purge = ops._went_with, trash._trashed, trash._purge_entry, \
        trash.pdf_index.purge_unused
    monkeypatch.setattr(ops, "_went_with", lambda conn, page: asked.append(page) or real_went(conn, page))

    def entry(ws, conn, *args, **kw):
        conns.add(id(conn))
        return real_entry(ws, conn, *args, **kw)

    def purge(ws, conn=None, *args, **kw):
        conns.add(id(conn))
        reads.append("purge")
        return real_purge(ws, conn, *args, **kw)

    monkeypatch.setattr(trash, "_purge_entry", entry)
    monkeypatch.setattr(trash.pdf_index, "purge_unused", purge)

    def trashed(conn):
        out = real_trashed(conn)
        reads.append("trash")
        conns.add(id(conn))
        with connect_pages_db(_ws()) as other:  # restored alone after the read: it stays
            ops.restore_page(_ws(), other, trees[1][3], actor="t")
        asked.clear()  # (the restore's own reads)
        return out

    monkeypatch.setattr(trash, "_trashed", trashed)
    emptied = trash.empty(_ws())
    assert {tree[0] for tree in trees} <= set(emptied) and asked == []
    assert reads == ["trash", "purge"] and len(conns) == 1  # one read, one purge after, all on one connection
    assert all(_row(page) is None for tree in trees for page in tree if page != trees[1][3])
    assert _row(trees[1][3])[0] == "root"


def test_step_48_indexes_what_went_with_a_page(data_dir):
    """``deleted_with`` is a generated column with a partial index
    (migration step 48): a workspace from before it gains both on its first
    open, ``ops._went_with`` reads the index, and the step leaves the
    workspace format as it is (a derived column)."""
    import sqlite3
    from contextlib import closing

    from gamma import db, migrations, seed
    from gamma.db import connect_users_db

    connect_users_db().close()
    seed.create_workspace_files("splOld48")
    path = data_dir / "workspaces" / "splOld48" / "pages.db"
    db.close_workspace_connections("splOld48")
    with closing(sqlite3.connect(str(path))) as old:  # the file as release 47 left it
        db.register_functions(old)
        old.execute("DROP INDEX idx_ub_deleted_with")
        old.execute("ALTER TABLE unified_blocks DROP COLUMN deleted_with")
        old.execute("INSERT OR IGNORE INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                    "updated_at, page_id) VALUES ('trash', NULL, 'a0', '', '{}', 't', 't', '')")
        old.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                    "updated_at, page_id) VALUES ('splKid48', 'trash', 'a5', 'kid', "
                    "'{\"deleted_with\": \"splTop48\"}', 't', 't', 'splKid48')")
        old.execute("INSERT INTO page_changes (page_id, seq, kind, at, actor) "
                    "SELECT 'splKid48', COALESCE(MAX(seq), 0) + 1, 'deleted', 't', '' FROM page_changes")
        old.execute("PRAGMA user_version = 47")
        old.commit()
    try:
        with connect_pages_db("splOld48") as cx:
            assert cx.execute("PRAGMA user_version").fetchone()[0] == 51  # the newest workspace step
            assert ops._went_with(cx, "splTop48") == ["splKid48"]
            plan = " ".join(str(r[-1]) for r in cx.execute(  # _went_with's own statement
                f"EXPLAIN QUERY PLAN {ops.WENT_WITH_SQL}", ("splTop48", TRASH)))
            assert "idx_ub_deleted_with" in plan, plan
            ddl = cx.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0]
            migrations._v48_deleted_with("splOld48", cx, None)  # re-runnable: nothing to do
            assert cx.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0] == ddl
    finally:
        db.close_workspace_connections("splOld48")
    migrations._v48_deleted_with("", sqlite3.connect(":memory:"), None)  # a copy without the table
    assert (48, "deleted_with", migrations._v48_deleted_with) in migrations.WORKSPACE_STEPS
    # a derived column: it moves no workspace format (51 is step 51's documents)
    assert 48 in migrations.DERIVED_STEPS and migrations.workspace_format() == 51


def test_deleting_a_live_page_for_good_takes_its_descendants(owner):
    top, a, g, b = _tree(owner, "Hard")
    note = _with_things(owner, g)
    # a page of the tree that went to Recently deleted with the page, which came back
    # under its id since (a mirror's page made again, ``create_page``): listed on its own
    owner.delete(f"/api/blocks/{top}").raise_for_status()
    owner.post("/api/pages", json={"id": top, "title": "Hard top, again"}).raise_for_status()
    assert {a, g, b} <= set(_trashed(owner))
    owner.post(f"/api/trash/{a}/restore").raise_for_status()  # inside the page again: it is live
    assert _row(g)[0] == "root"  # (and the page inside it with it)
    before = _newest()

    with connect_pages_db(_ws()) as conn:
        out = ops.delete_page(_ws(), conn, top, actor="t")
    assert {top, a, g, b, note} <= set(out["deleted_ids"])
    assert all(_row(page) is None for page in (top, a, g, b)) and _gone(g, note)
    log = _changes([top, a, g])
    assert [log[p] for p in (top, a, g)] == [(before + 1, "deleted"), (before + 2, "deleted"),
                                              (before + 3, "deleted")]
    assert _changes([b])[b][1] == "deleted"  # the trashed one keeps the row its trashing wrote


def test_filing_and_a_folder_listing_pass_the_cascade_by(owner):
    folder = make_folder(owner, "spl/kept")
    top = _page(owner, "Filed top", folders=[folder])
    kid = _page(owner, "Filed kid", top)
    owner.delete(f"/api/blocks/{top}").raise_for_status()
    # the agent's list_deleted reads the listing: the page, by its folder, alone
    from gamma.ai_tools import run_agent_tool

    text, _ = run_agent_tool(_ws(), {"type": "folder", "folder": ""}, "list_deleted", {"title_contains": "Filed"})
    assert top in text and kid not in text
    owner.post(f"/api/trash/{top}/restore").raise_for_status()
    assert _row(top)[1]["folders"] == [folder] and _row(kid)[1]["parent_page"] == top


# --- the mirror ------------------------------------------------------------------------

def _remote_tree(side, prefix):
    """A page and two pages inside it on ``side`` whose ids sort before
    it (``_round_order`` must still put the page first): ``(top, a, b)``."""
    top = _page(side.client, f"{prefix} top", id=f"zz{prefix}top")
    a = _page(side.client, f"{prefix} a", top, id=f"aa{prefix}a")
    b = _page(side.client, f"{prefix} b", a, id=f"ab{prefix}b")
    return top, a, b


def _log(side, page_id):
    """The sync log's actions on the page, the newest first."""
    return [r["action"] for r in sync_engine.list_log(side.ws, 200) if r["page_id"] == page_id]


def test_a_round_takes_a_trashed_tree_as_a_tree_both_ways():
    remote, local, _ = _pair()
    top, a, b = _remote_tree(remote, "mt")
    _sync(local)
    assert {top, a, b} <= set(local.pages()) and _row(b, local.ws)[1]["parent_page"] == a

    # trashed there: the page's deletion comes first and takes its sub-pages along here;
    # their own deletions find them gone already
    remote.client.delete(f"/api/blocks/{top}").raise_for_status()
    status = _sync(local)
    assert status["pages_deleted"] == 3
    for page in (top, a, b):
        assert _row(page, local.ws)[0] == TRASH
    assert [_row(p, local.ws)[1].get("deleted_with") for p in (top, a, b)] == [None, top, top]
    listed = _trashed(local.client)
    assert listed[top]["sub_pages"] == 2 and a not in listed and b not in listed
    assert all(_log(local, p) == ["deleted here", "created here"] for p in (top, a, b))
    with connect_pages_db(local.ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_pages WHERE page_id IN (?, ?, ?)", (top, a, b)).fetchone()[0] == 0
    assert not _sync(local)["pages_deleted"]  # the next round has nothing left to do

    # restored there: the tree comes back here whole, inside the pages it was in
    remote.client.post(f"/api/trash/{top}/restore").raise_for_status()
    _sync(local)
    pages = local.pages()
    assert (pages[a]["parent"], pages[b]["parent"]) == (top, a) and _row(b, local.ws)[1]["parent_page"] == a
    assert _trashed(local.client).keys().isdisjoint({top, a, b})

    # trashed here: the same the other way
    local.client.delete(f"/api/blocks/{top}").raise_for_status()
    _sync(local)
    for page in (top, a, b):
        assert _row(page, remote.ws)[0] == TRASH
    listed = _trashed(remote.client)
    assert listed[top]["sub_pages"] == 2 and a not in listed and b not in listed
    assert _log(local, top)[0] == "deleted there"


def test_a_publication_carries_the_pages_inside_its_page():
    remote = Side("spl_pub_remote")
    local = Side("spl_pub_local")
    top = _page(local.client, "Published top")
    kid = _page(local.client, "Published kid", top)
    private = _page(local.client, "Private")
    token = create_token(remote.id, remote.ws, "mirror", 30, scope="write")["token"]
    sync_engine.create_mirror(local.id, "http://testserver", token, workspace_id=local.ws, adopt="mine",
                              page_filter=[top])
    sync_engine.sync_workspace(local.ws)
    there = remote.pages()
    assert {top, kid} <= set(there) and private not in there
    assert _row(kid, remote.ws)[1]["parent_page"] == top and there[kid]["parent"] == top

    # a page made inside it later goes at the next round; a write inside it counts as waiting
    grand = _page(local.client, "Published grandchild", kid)
    assert not _sync_ok(local.ws)["last_error"] and grand in remote.pages()
    sync_engine._dirty.pop(local.ws, None)
    local.insert(grand, "spl_g1", "typed in the grandchild")
    assert sync_engine.has_local_changes(local.ws)
    local.insert(private, "spl_p1", "still private")
    _sync_ok(local.ws)
    assert remote.texts(grand) == {"spl_g1": "typed in the grandchild"} and private not in remote.pages()

    # a page inside it trashed here on its own: its deletion goes there too
    local.client.delete(f"/api/blocks/{grand}").raise_for_status()
    _sync_ok(local.ws)
    assert grand not in remote.pages() and _row(grand, remote.ws)[0] == TRASH
    assert sync_engine.get_mirror(local.ws)["page_filter"] == [top]


def _sync_ok(ws):
    status = sync_engine.sync_workspace(ws)
    assert not status.get("last_error"), status
    return status


def test_publishing_and_unpublishing_a_page_with_sub_pages(publishing):
    from test_publish import anonymous, bound, page_ids

    local, local_ws = linked(publishing, "spl_pb")
    host_ws = workspace_of("spl_pb")
    host = bound(login("spl_pb", "pw"), host_ws)
    top = _page(local, "Shared tree")
    kid = _page(local, "Shared kid", top)
    r = local.post(f"/api/pages/{top}/publish")
    assert r.status_code == 200, r.text
    token = r.json()["share"]["token"]
    assert {top, kid} <= page_ids(host)
    # the page's share reaches the page inside it there
    assert anonymous().get(f"/api/blocks/{kid}", params={"share": token}).status_code == 200

    r = local.delete(f"/api/pages/{top}/publish")
    assert r.status_code == 200, r.text
    assert not {top, kid} & page_ids(host)
    with connect_pages_db(local_ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_pages").fetchone()[0] == 0
    assert sync_engine.sync_workspace(local_ws)["last_error"] == ""
    assert {top, kid} <= page_ids(local) and not {top, kid} & page_ids(host)


def _conflicts(side, page_id):
    with connect_pages_db(side.ws) as conn:
        return [r[0] for r in conn.execute("SELECT kind FROM sync_conflicts WHERE page_id = ?", (page_id,))]


def test_copies_moving_two_pages_into_each_other_keep_syncing():
    remote, local, _ = _pair()
    a = _page(remote.client, "Cycle A")
    b = _page(remote.client, "Cycle B")
    _sync(local)
    move = {a: b, b: a}
    for side, page in ((local, a), (remote, b)):  # each copy moves one into the other
        r = side.client.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
            {"op": "set", "id": page, "props": {"parent_page": move[page]}}]})
        assert r.status_code == 200, r.text
    _sync(local)  # the merged cycle is stored on both sides, read as two library pages
    for side in (local, remote):
        assert (_row(a, side.ws)[1]["parent_page"], _row(b, side.ws)[1]["parent_page"]) == (b, a)
        assert side.pages()[a]["parent"] == side.pages()[b]["parent"] == ""
    remote.insert(b, "spl_cyc1", "typed there later")
    local.insert(a, "spl_cyc2", "typed here later")
    _sync(local)
    assert local.texts(b) == {"spl_cyc1": "typed there later"} and remote.texts(a) == {"spl_cyc2": "typed here later"}


def test_a_sub_page_edited_there_survives_its_parent_s_deletion_here():
    remote, local, _ = _pair()
    top, a, b = _remote_tree(remote, "ed")
    folder = remote.folder("spl_edF", "ed folder")
    _move_to(remote.client, top, {"folders": [folder]})
    _sync(local)
    remote.insert(a, "spl_ed1", "typed there")
    local.client.delete(f"/api/blocks/{top}").raise_for_status()  # a and b go with it here
    _sync(local)
    # top is deleted there; a, edited there, comes back on its own, with b, untouched,
    # inside it on both sides
    assert _row(top, remote.ws)[0] == TRASH
    assert _row(a, remote.ws)[0] == "root" and "parent_page" not in _row(a, remote.ws)[1]
    assert local.texts(a) == {"spl_ed1": "typed there"} and _row(a, local.ws)[0] == "root"
    for side in (local, remote):  # filed where top was, not loose in the library
        assert _row(a, side.ws)[1]["folders"] == [folder]
    for side in (local, remote):
        assert _row(b, side.ws)[0] == "root" and _row(b, side.ws)[1]["parent_page"] == a
    assert "page_restored_from_remote" in _conflicts(local, a)
    assert not _sync(local)["pages_deleted"]


def test_a_sub_page_moved_out_there_keeps_what_was_typed_here():
    remote, local, _ = _pair()
    top, a, _ = _remote_tree(remote, "mo")
    _sync(local)
    local.insert(a, "spl_mo1", "typed here")
    r = remote.client.post(f"/api/pages/{a}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": a, "props": {"parent_page": None}}]})
    assert r.status_code == 200, r.text
    remote.client.delete(f"/api/blocks/{top}").raise_for_status()  # without a, moved out first
    _sync(local)
    assert _row(top, local.ws)[0] == TRASH and _row(a, local.ws)[0] == "root"
    assert "parent_page" not in _row(a, local.ws)[1]
    assert local.texts(a) == {"spl_mo1": "typed here"} == remote.texts(a)


def test_a_share_host_keeps_a_sub_page_a_share_editor_wrote_in(publishing):
    from test_publish import bound, page_ids

    local, local_ws = linked(publishing, "spl_hed")
    host_ws = workspace_of("spl_hed")
    host = bound(login("spl_hed", "pw"), host_ws)
    folder = make_folder(local, "spl/hosted")
    top = _page(local, "Host top", folders=[folder])
    kid = _page(local, "Host kid", top)
    assert local.post(f"/api/pages/{top}/publish").status_code == 200
    r = host.post(f"/api/pages/{kid}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "spl_hed1", "parent": kid, "content": "typed by a share editor"}]})
    assert r.status_code == 200, r.text
    with connect_pages_db(local_ws) as conn:  # (this process is a share host too: its DELETE deletes for good)
        ops.trash_page(local_ws, conn, top, actor="spl")
    _sync_ok(local_ws)
    # the edited page comes back here on its own, with what was typed there
    assert top not in page_ids(host) and _row(kid, local_ws)[0] == "root"
    texts = [c["content"] for c in local.get(f"/api/blocks/{kid}/children").json()["children"]]
    assert texts == ["typed by a share editor"]
    with connect_pages_db(local_ws) as conn:
        assert conn.execute("SELECT kind FROM sync_conflicts WHERE page_id = ?", (kid,)).fetchall() == [
            ("page_restored_from_remote",)]
    # filed where its parent was, and published on its own: it stays on the share host
    for ws in (local_ws, host_ws):
        assert "parent_page" not in _row(kid, ws)[1] and _row(kid, ws)[1]["folders"] == [folder]
    assert kid in sync_engine.get_mirror(local_ws)["page_filter"]
    _sync_ok(local_ws)
    assert kid in page_ids(host) and _row(kid, local_ws)[0] == "root"
    assert sync_engine.get_mirror(local_ws)["page_filter"] == [kid]  # (the deleted page left it)
    assert local.get(f"/api/pages/{kid}/publish").json()["published"]


def test_a_sub_page_moved_out_of_a_published_page_leaves_the_share_host(publishing):
    from test_publish import bound, page_ids

    local, local_ws = linked(publishing, "spl_hmo")
    host = bound(login("spl_hmo", "pw"), workspace_of("spl_hmo"))
    top = _page(local, "Moved top")
    kid = _page(local, "Moved kid", top)
    private = _page(local, "Private home")
    assert local.post(f"/api/pages/{top}/publish").status_code == 200
    assert kid in page_ids(host)
    r = local.post(f"/api/pages/{kid}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": kid, "props": {"parent_page": private}}]})
    assert r.status_code == 200, r.text
    _sync_ok(local_ws)
    assert kid not in page_ids(host) and top in page_ids(host)
    with connect_pages_db(local_ws) as conn:
        assert not conn.execute("SELECT 1 FROM sync_pages WHERE page_id = ?", (kid,)).fetchone()
    assert _row(kid, local_ws)[1]["parent_page"] == private  # the page here stays where it was put


def _move(client, page, parent):
    _move_to(client, page, {"parent_page": parent})


def _move_to(client, page, props):
    r = client.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": page, "props": props}]})
    assert r.status_code == 200, r.text


def test_a_sub_page_edited_here_survives_its_parent_s_deletion_there():
    remote, local, _ = _pair()
    top, a, b = _remote_tree(remote, "eh")
    lone = _page(remote.client, "eh lone", id="aaehlone")
    folder = remote.folder("spl_ehF", "eh folder")
    _move_to(remote.client, top, {"folders": [folder]})
    _sync(local)
    local.insert(a, "spl_eh1", "typed here in a")
    local.insert(lone, "spl_eh2", "typed here in lone")
    for page in (top, lone):
        remote.client.delete(f"/api/blocks/{page}").raise_for_status()
    _sync(local)
    # a, edited here, goes back there as a library page does, out of top (which both
    # sides deleted)
    for page in (a, lone):
        assert _row(page, local.ws)[0] == "root" == _row(page, remote.ws)[0], page
        assert "page_restored" in _conflicts(local, page)
    assert "parent_page" not in _row(a, local.ws)[1] and "parent_page" not in _row(a, remote.ws)[1]
    assert _row(a, local.ws)[1]["folders"] == [folder] == _row(a, remote.ws)[1]["folders"]
    assert remote.texts(a) == {"spl_eh1": "typed here in a"} == local.texts(a)
    assert _row(top, local.ws)[0] == TRASH and a not in _trashed(local.client)
    _sync(local)
    assert _row(a, local.ws)[0] == "root" == _row(a, remote.ws)[0]


def test_a_page_moved_into_one_there_survives_that_one_s_deletion_here():
    remote, local, _ = _pair()
    top = _page(remote.client, "mi top", id="aami_top")
    moved = _page(remote.client, "mi moved", id="zzmi_moved")
    _sync(local)
    _move(remote.client, moved, top)
    remote.insert(moved, "spl_mi1", "typed there in the moved page")
    made = _page(remote.client, "mi made there", top, id="zzmi_made")  # only there so far
    local.client.delete(f"/api/blocks/{top}").raise_for_status()
    _sync(local)
    _sync(local)
    assert _row(top, remote.ws)[0] == TRASH
    for page in (moved, made):  # neither went with top here: the deletion there leaves them
        assert _row(page, local.ws)[0] == "root" == _row(page, remote.ws)[0], page
    assert local.texts(moved) == {"spl_mi1": "typed there in the moved page"}


def test_a_page_moved_into_one_there_before_its_turn_comes_out_of_it_on_both_sides():
    """The moved page's id sorts before the page's, and here it is a library
    page until its turn brings the move, so that turn comes first: the move
    lands here inside a page in Recently deleted. The round's end takes it
    out here too (``_settle_parents``), as the deletion's ``spare_inside``
    did there, filed where that page was; nothing names the deleted page
    any more, so its restore does not take the page back inside."""
    remote, local, _ = _pair()
    folder = remote.folder("spl_mbF", "mb folder")
    top = _page(remote.client, "mb top", id="zzmb_top", folders=[folder])
    moved = _page(remote.client, "mb moved", id="aamb_moved")
    _sync(local)
    _move(remote.client, moved, top)
    remote.insert(moved, "spl_mb1", "typed there in the moved page")
    local.client.delete(f"/api/blocks/{top}").raise_for_status()
    _sync(local)
    assert _row(moved, local.ws)[0] == "root" and "parent_page" not in _row(moved, local.ws)[1]
    _sync(local)
    assert _row(top, remote.ws)[0] == TRASH
    for side in (local, remote):
        parent_id, props = _row(moved, side.ws)
        assert parent_id == "root" and "parent_page" not in props and props["folders"] == [folder], side.ws
    assert local.texts(moved) == {"spl_mb1": "typed there in the moved page"} == remote.texts(moved)
    local.client.post(f"/api/trash/{top}/restore").raise_for_status()
    _sync(local)
    for side in (local, remote):
        assert "parent_page" not in _row(moved, side.ws)[1] and _row(top, side.ws)[0] == "root"


def test_a_round_takes_a_page_an_earlier_round_left_inside_a_deleted_page_out_of_it():
    """A page an earlier release left live here inside a page in Recently
    deleted, and that nobody touches since, is in no round's work list; the
    round's end still takes it out (``_settle_parents`` reads every such
    page, not only the ones the round worked), filed where that page was,
    so restoring the page later does not take it back inside."""
    remote, local, _ = _pair()
    folder = remote.folder("spl_lgF", "lg folder")
    top = _page(remote.client, "lg top", folders=[folder])
    kid = _page(remote.client, "lg kid")
    _sync(local)
    local.client.delete(f"/api/blocks/{top}").raise_for_status()
    _sync(local)
    assert _row(top, remote.ws)[0] == TRASH
    with connect_pages_db(local.ws) as conn:  # as the old round left it: no change logged
        conn.execute("UPDATE unified_blocks SET properties = json_set(properties, '$.parent_page', ?) WHERE id = ?",
                     (top, kid))
        conn.commit()
    assert sync_engine._settle_parents(local.ws, {top}, {}) == []  # out of the reach: left
    _sync(local)
    parent_id, props = _row(kid, local.ws)
    assert parent_id == "root" and "parent_page" not in props and props["folders"] == [folder]
    _sync(local)
    assert "parent_page" not in _row(kid, remote.ws)[1]
    local.client.post(f"/api/trash/{top}/restore").raise_for_status()
    _sync(local)
    for side in (local, remote):
        assert "parent_page" not in _row(kid, side.ws)[1] and _row(top, side.ws)[0] == "root", side.ws


def test_a_round_leaves_a_parent_this_copy_never_held_alone():
    """A stored parent naming a page this copy has no row of and never
    deleted (a mirror may bring a sub-page before its parent) is no
    deletion: the round's end leaves it as it is."""
    remote, local, _ = _pair()
    kid = _page(remote.client, "nh kid")
    _move(remote.client, kid, "spl_nowhere1")
    _sync(local)
    _sync(local)
    for side in (local, remote):
        assert _row(kid, side.ws)[1]["parent_page"] == "spl_nowhere1"


def test_a_page_coming_back_alone_brings_the_pages_inside_it():
    remote, local, _ = _pair()
    top, a, b = _remote_tree(remote, "gc")
    _sync(local)
    _sync(local)
    _move(remote.client, a, None)
    remote.client.delete(f"/api/blocks/{top}").raise_for_status()  # without a, moved out first
    _sync(local)
    # a comes back here, out of the trash top went to, and b, inside it, with it
    assert _row(a, local.ws)[0] == "root" and _row(b, local.ws)[0] == "root"
    assert _row(b, local.ws)[1]["parent_page"] == a and "deleted_with" not in _row(b, local.ws)[1]
    _sync(local)
    for side in (local, remote):
        assert _row(b, side.ws)[0] == "root" and _row(b, side.ws)[1]["parent_page"] == a


def test_pages_edited_there_inside_each_other_come_back_still_inside_each_other():
    remote, local, _ = _pair()
    top, s, s2 = _remote_tree(remote, "ne")
    _sync(local)
    remote.insert(s, "spl_ne1", "typed there in s")
    remote.insert(s2, "spl_ne2", "typed there in s2")
    local.client.delete(f"/api/blocks/{top}").raise_for_status()
    _sync(local)
    _sync(local)
    for side in (local, remote):
        assert _row(s, side.ws)[0] == "root" and "parent_page" not in _row(s, side.ws)[1]
        assert _row(s2, side.ws)[0] == "root" and _row(s2, side.ws)[1]["parent_page"] == s
    assert local.texts(s2) == {"spl_ne2": "typed there in s2"}
    assert "page_restored_from_remote" in _conflicts(local, s2)


def _filtered_pair(name):
    remote = Side(f"{name}_r")
    local = Side(f"{name}_l")
    top = _page(local.client, f"{name} top")
    token = create_token(remote.id, remote.ws, "mirror", 30, scope="write")["token"]
    sync_engine.create_mirror(local.id, "http://testserver", token, workspace_id=local.ws, adopt="mine",
                              page_filter=[top])
    return remote, local, top


def test_a_receive_only_copy_never_deletes_a_page_moved_out_there():
    remote, local, top = _filtered_pair("spl_ro")
    kid = _page(local.client, "spl_ro kid", top)
    _sync_ok(local.ws)
    assert _row(kid, remote.ws)[0] == "root"
    sync_engine.set_cadence(local.ws, mode="pull")
    _move(local.client, kid, None)
    _sync_ok(local.ws)
    assert _row(kid, remote.ws)[0] == "root"


def test_a_sub_page_made_after_the_reach_was_read_counts_as_waiting():
    remote, local, top = _filtered_pair("spl_cw")
    other = _page(local.client, "spl_cw elsewhere")
    _sync_ok(local.ws)
    local.insert(top, "spl_cw0", "a write that reads the reach")
    sync_engine._dirty.pop(local.ws, None)
    kid = _page(local.client, "spl_cw kid", top)
    local.insert(kid, "spl_cw1", "typed in the new sub-page")
    assert sync_engine.has_local_changes(local.ws)
    sync_engine._dirty.pop(local.ws, None)
    local.insert(other, "spl_cw2", "outside the publication")
    assert not sync_engine.has_local_changes(local.ws)
    _move(local.client, other, top)  # moved in: from now on it counts
    sync_engine._dirty.pop(local.ws, None)
    local.insert(other, "spl_cw3", "typed after the move")
    assert sync_engine.has_local_changes(local.ws)


def test_a_sub_page_moved_out_of_a_published_page_brings_a_share_editor_s_text_first(publishing):
    from test_publish import bound, page_ids

    local, local_ws = linked(publishing, "spl_hse")
    host = bound(login("spl_hse", "pw"), workspace_of("spl_hse"))
    top = _page(local, "Editor top")
    kid = _page(local, "Editor kid", top)
    private = _page(local, "Editor private")
    assert local.post(f"/api/pages/{top}/publish").status_code == 200
    r = host.post(f"/api/pages/{kid}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "spl_hse1", "parent": kid, "content": "typed by a share editor"}]})
    assert r.status_code == 200, r.text
    _move(local, kid, private)
    _sync_ok(local_ws)
    assert kid not in page_ids(host) and top in page_ids(host)
    texts = [c["content"] for c in local.get(f"/api/blocks/{kid}/children").json()["children"]]
    assert texts == ["typed by a share editor"]
    assert _row(kid, local_ws)[1]["parent_page"] == private


def test_unpublishing_a_page_keeps_a_page_inside_it_published_on_its_own(publishing):
    from test_publish import anonymous, bound, page_ids

    local, local_ws = linked(publishing, "spl_own")
    host = bound(login("spl_own", "pw"), workspace_of("spl_own"))
    top = _page(local, "Own top")
    kid = _page(local, "Own kid", top)
    grand = _page(local, "Own grandchild", kid)
    assert local.post(f"/api/pages/{top}/publish").status_code == 200
    r = local.post(f"/api/pages/{kid}/publish")
    assert r.status_code == 200, r.text
    token = r.json()["share"]["token"]
    assert local.delete(f"/api/pages/{top}/publish").status_code == 200
    assert sync_engine.get_mirror(local_ws)["page_filter"] == [kid]
    assert top not in page_ids(host) and {kid, grand} <= page_ids(host)
    assert anonymous().get(f"/api/blocks/{grand}", params={"share": token}).status_code == 200
    assert local.get(f"/api/pages/{kid}/publish").json()["published"] is True
    # here it stays inside the page, and the rounds after keep it so
    for _ in range(2):
        _sync_ok(local_ws)
    assert _row(kid, local_ws)[1]["parent_page"] == top and _row(grand, local_ws)[1]["parent_page"] == kid
    assert {kid, grand} <= page_ids(host) and top not in page_ids(host)
    _child(local, kid, "typed after the unpublish")
    _sync_ok(local_ws)
    assert [c["content"] for c in host.get(f"/api/blocks/{kid}/children").json()["children"]] == [
        "typed after the unpublish"]


def test_a_page_and_the_pages_inside_it_count_against_the_plan(publishing):
    from test_publish import bound, page_ids
    from gamma import publish

    local, local_ws = linked(publishing, "spl_cap")
    host_ws = workspace_of("spl_cap")
    host = bound(login("spl_cap", "pw"), host_ws)
    tops = [_page(local, f"Capped {n}") for n in range(2)]
    kids = [_page(local, f"Capped kid {n}", tops[0]) for n in range(2)]
    assert local.post(f"/api/pages/{tops[0]}/publish").status_code == 200
    _sync_ok(local_ws)
    assert set(kids) <= page_ids(host)
    # each published page counts, the ones inside a published page too
    assert host.get("/api/publish/limit").json() == {"used": 3, "max": 5, "plan": "free"}
    # a page whose tree would pass the cap is refused before anything goes
    big = _page(local, "Capped big")
    for n in range(2):
        _page(local, f"Capped big kid {n}", big)
    refused = local.post(f"/api/pages/{big}/publish")
    assert refused.status_code == 409 and refused.json()["limit"]["max"] == 5
    assert host.get("/api/publish/limit").json()["used"] == 3
    # one that fits goes, and the workspace there is then full
    assert local.post(f"/api/pages/{tops[1]}/publish").status_code == 200
    _sync_ok(local_ws)
    assert host.get("/api/publish/limit").json()["used"] == 4
    assert publish.cap_refusal(host_ws) is None
    _page(local, "Capped kid 2", tops[0])
    _sync_ok(local_ws)
    assert publish.cap_refusal(host_ws) is not None
