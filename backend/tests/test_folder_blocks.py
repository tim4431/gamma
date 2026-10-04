"""Folders and labels are blocks (docs/dev/home_library.md): two pseudo-pages,
``folders`` and ``labels``, whose blocks are written through the op path
like a page's, and pages filed by id (``properties.folders`` /
``properties.labels``). A rename, a move and a new folder touch no page; a
delete takes the ids off the pages that carry them, files the folders'
chats into the library's history and stops their shares."""

import pytest

from conftest import login, make_user
from gamma import blocks_store
from gamma.blocks_store import FOLDERS, LABELS
from gamma.db import connect_pages_db, connect_users_db

USER = "fb_owner"


@pytest.fixture(scope="module")
def owner(client):
    ws = make_user(USER, "fb-password-1")
    return login(USER, "fb-password-1"), ws


def _ops(c, page, ops):
    r = c.post(f"/api/pages/{page}/ops", json={"ops": ops})
    assert r.status_code == 200, r.text
    return r.json()


def _folder(c, name, parent=FOLDERS, folder_id=None):
    folder_id = folder_id or blocks_store.new_block_id()
    _ops(c, FOLDERS, [{"op": "insert", "id": folder_id, "parent": parent, "content": name}])
    return folder_id


def _label(c, name):
    label_id = blocks_store.new_block_id()
    _ops(c, LABELS, [{"op": "insert", "id": label_id, "parent": LABELS, "content": name}])
    return label_id


def _page(c, title, **props):
    r = c.post("/api/pages", json={"title": title, "properties": props})
    assert r.status_code == 200, r.text
    return r.json()


def _props(c, page_id):
    return c.get(f"/api/blocks/{page_id}").json()["properties"]


def _stamps(ws):
    """Every page's ``updated_at`` and change-log seq."""
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT b.id, b.updated_at, c.seq FROM unified_blocks b JOIN page_changes c "
                            "ON c.page_id = b.id WHERE b.parent_id = 'root' ORDER BY b.id").fetchall()


def test_a_new_workspace_has_the_reserved_rows(owner):
    _c, ws = owner
    with connect_pages_db(ws) as conn:
        rows = dict(conn.execute("SELECT id, parent_id FROM unified_blocks WHERE id IN ('folders', 'labels')"))
    assert rows == {FOLDERS: None, LABELS: None}
    assert not blocks_store.valid_block_id(FOLDERS) and not blocks_store.valid_block_id(LABELS)


def test_folders_are_made_renamed_moved_and_reordered_by_ops_touching_no_page(owner):
    c, ws = owner
    page = _page(c, "Filed")
    top = _folder(c, "Physics")
    other = _folder(c, "Math")
    child = _folder(c, "QEC", parent=top)
    _ops(c, page["id"], [{"op": "set", "id": page["id"], "props": {"folders": [child]}}])
    before = _stamps(ws)

    _ops(c, FOLDERS, [{"op": "set", "id": child, "content": "Error correction"}])   # rename
    _ops(c, FOLDERS, [{"op": "move", "id": child, "parent": other}])                 # move
    _ops(c, FOLDERS, [{"op": "move", "id": other, "parent": FOLDERS, "position": "a0"}])  # reorder
    _ops(c, FOLDERS, [{"op": "set", "id": top, "props": {"pinned": "2026-10-01T00:00:00.000Z"}}])  # pin

    assert _stamps(ws) == before  # no page moved in the feed, none was stamped
    with connect_pages_db(ws) as conn:
        assert blocks_store.folder_path(conn, child) == ["Math", "Error correction"]
        assert blocks_store.pages_in_folder(conn, other) == [page["id"]]
        assert blocks_store.pages_in_folder(conn, top) == []
        kinds = dict(conn.execute("SELECT id, kind FROM unified_blocks WHERE id IN (?, ?)", (child, FOLDERS)))
        assert kinds == {child: "folder", FOLDERS: None}
        assert conn.execute("SELECT kind FROM page_changes WHERE page_id = ?", (FOLDERS,)).fetchone()[0] == "live"
    assert _props(c, page["id"])["folders"] == [child]


def test_the_trees_refuse_what_is_no_folder_or_label(owner):
    c, _ws = owner
    page = _page(c, "Not a tree")
    folder = _folder(c, "Refusals")
    bad = [
        (FOLDERS, [{"op": "set", "id": FOLDERS, "content": "x"}], 403),            # the reserved row
        (FOLDERS, [{"op": "delete", "id": FOLDERS}], 403),
        (FOLDERS, [{"op": "move", "id": FOLDERS, "parent": folder}], 403),
        (FOLDERS, [{"op": "insert", "id": "fbx1", "parent": page["id"]}], 403),     # a page's block
        (page["id"], [{"op": "insert", "id": "fbx2", "parent": folder}], 403),      # a folder in a page
        (FOLDERS, [{"op": "move", "id": folder, "parent": "root"}], 403),
    ]
    for tree, ops, status in bad:
        r = c.post(f"/api/pages/{tree}/ops", json={"ops": ops})
        assert r.status_code == status, (ops, r.text)
    label = _label(c, "flat")
    r = c.post(f"/api/pages/{LABELS}/ops", json={"ops": [{"op": "insert", "id": "fbx3", "parent": label}]})
    assert r.status_code == 400 and "nest" in r.text
    assert c.post("/api/pages/trash/ops", json={"ops": [{"op": "set", "id": "trash", "content": "x"}]}).status_code == 404


def test_a_filing_is_a_list_of_ids_stored_as_written(owner):
    c, _ws = owner
    folder, label = _folder(c, "Kept"), _label(c, "kept")
    page = _page(c, "Filed by id", folders=[folder, folder], labels=[label])
    assert page["properties"] == {"folders": [folder], "labels": [label]}  # repeats go

    out = _ops(c, page["id"], [{"op": "set", "id": page["id"], "props": {"folders": [], "labels": [label]}}])
    assert out["ops"][0]["props"] == {"folders": None, "labels": [label]}  # an empty list unfiles
    assert "folders" not in _props(c, page["id"])
    for bad in ("Kept", [3], ["not an id!"]):
        r = c.post(f"/api/pages/{page['id']}/ops", json={"ops": [{"op": "set", "id": page["id"],
                                                                    "props": {"folders": bad}}]})
        assert r.status_code == 400, bad
    # an id this copy has no folder for is kept: a mirror's page may name a
    # folder its tree has not brought yet, a published page ones the host never sees
    _ops(c, page["id"], [{"op": "set", "id": page["id"], "props": {"folders": ["notHereYet"]}}])
    assert _props(c, page["id"])["folders"] == ["notHereYet"]


def test_a_dangling_id_is_passed_by_and_goes_with_the_next_refiling(owner):
    c, ws = owner
    gone, kept = _folder(c, "Gone soon"), _folder(c, "Stays")
    page = _page(c, "Dangling", folders=[gone, kept])
    # a delete that did not go through DELETE /folders (a raw op, a mirror)
    _ops(c, FOLDERS, [{"op": "delete", "id": gone}])
    assert _props(c, page["id"])["folders"] == [gone, kept]  # nothing repairs it on a read
    with connect_pages_db(ws) as conn:
        assert page["id"] not in blocks_store.root_pages(conn, gone)
        assert blocks_store.existing_in(conn, FOLDERS, [gone, kept, kept]) == [kept]
    # a writer that refiles the page keeps the ids that exist (here the clip)
    newer = _folder(c, "Newer")
    r = c.post("/api/clip", json={"source_url": f"https://example.org/fb/{page['id']}", "folder": newer})
    assert r.status_code == 200, r.text
    clipped = r.json()["block_id"]
    _ops(c, clipped, [{"op": "set", "id": clipped, "props": {"folders": [gone]}}])
    r = c.post("/api/clip", json={"source_url": f"https://example.org/fb/{page['id']}", "folder": kept})
    assert _props(c, clipped)["folders"] == [kept]


def test_membership_reaches_the_folders_below(owner):
    c, ws = owner
    top = _folder(c, "Membership")
    mid = _folder(c, "Mid", parent=top)
    low = _folder(c, "Low", parent=mid)
    a = _page(c, "In low", folders=[low])
    b = _page(c, "In top", folders=[top])
    _page(c, "Elsewhere")
    with connect_pages_db(ws) as conn:
        assert blocks_store.folder_subtree_ids(conn, top) == {top, mid, low}
        assert set(blocks_store.pages_in_folder(conn, top)) == {a["id"], b["id"]}
        assert blocks_store.pages_in_folder(conn, mid) == [a["id"]]
        assert set(blocks_store.root_pages(conn, top)) == {a["id"], b["id"]}
        assert blocks_store.root_pages(conn, "no-such-folder") == {}
        assert blocks_store.page_in_folder(conn, a["id"], mid) and not blocks_store.page_in_folder(conn, b["id"], mid)


def test_paths_resolve_by_name_and_report_ambiguity(owner):
    c, ws = owner
    top = _folder(c, "Paths")
    one = _folder(c, "Twin", parent=top)
    two = _folder(c, "Twin", parent=top)
    deep = _folder(c, "a / b", parent=one)  # a name may hold anything
    with connect_pages_db(ws) as conn:
        assert blocks_store.folder_by_path(conn, ["Paths"]) == [top]
        assert blocks_store.folder_by_path(conn, ["paths"]) == [top]  # ignoring case when nothing matches exactly
        assert blocks_store.folder_by_path(conn, ["Paths", "Twin"]) == [one, two]
        assert blocks_store.folder_by_path(conn, ["Paths", "Twin", "a / b"]) == [deep]
        assert blocks_store.folder_by_path(conn, ["Paths", "Nope"]) == []
        assert blocks_store.folder_paths(conn)[deep] == ["Paths", "Twin", "a / b"]
        ops, ids = blocks_store.folder_inserts(conn, [["Paths", "Twin", "New"], ["Paths"]])
    assert [op["content"] for op in ops] == ["New"] and ops[0]["parent"] == one
    assert ids[("Paths",)] == top


def test_the_listing_carries_the_trees_with_their_seqs(owner):
    c, _ws = owner
    top = _folder(c, "Listed")
    child = _folder(c, "Child", parent=top)
    label = _label(c, "listed")
    listing = c.get("/api/blocks/root/children").json()
    assert {"children", FOLDERS, LABELS} <= listing.keys()
    node = next(n for n in listing[FOLDERS]["children"] if n["id"] == top)
    assert node["kind"] == "folder" and [n["id"] for n in node["children"]] == [child]
    assert any(n["id"] == label for n in listing[LABELS]["children"])
    tree = c.get(f"/api/blocks/{FOLDERS}/subtree").json()
    assert tree["seq"] == listing[FOLDERS]["seq"] and tree["block"]["id"] == FOLDERS
    assert c.get(f"/api/pages/{FOLDERS}/ops?since=0").status_code in (200, 410)
    assert c.get(f"/api/blocks/{child}").json()["page_id"] == FOLDERS


def test_deleting_a_folder_refiles_its_pages_and_keeps_its_chats(owner):
    c, ws = owner
    top = _folder(c, "Doomed")
    sub = _folder(c, "Sub", parent=top)
    keep = _folder(c, "Survivor")
    a = _page(c, "Filed in sub and survivor", folders=[sub, keep])
    b = _page(c, "Filed in top", folders=[top])
    untouched = _page(c, "Filed in survivor", folders=[keep])
    assert c.put(f"/api/chats/{top}", json={"messages": [{"role": "user", "text": "top chat"}]}).status_code == 200
    assert c.put(f"/api/chats/{sub}", json={"messages": [{"role": "user", "text": "sub chat"}]}).status_code == 200
    share = c.post(f"/api/share/folder/{sub}").json()["token"]
    before = dict((r[0], r[2]) for r in _stamps(ws))

    out = c.delete(f"/api/folders/{top}").json()
    assert set(out["ids"]) == {top, sub} and set(out["pages"]) == {a["id"], b["id"]}
    assert out["chats"] == 2 and out["shares"] == 1
    assert _props(c, a["id"])["folders"] == [keep] and "folders" not in _props(c, b["id"])
    after = dict((r[0], r[2]) for r in _stamps(ws))
    assert after[untouched["id"]] == before[untouched["id"]]  # one batch per page that carried the ids
    with connect_pages_db(ws) as conn:
        assert not blocks_store.folder_subtree_ids(conn, top)
        assert conn.execute("SELECT COUNT(*) FROM chats WHERE bucket IN (?, ?)", (top, sub)).fetchone()[0] == 0
    history = c.get("/api/chat-history?bucket=home").json()["sessions"]
    assert {"top chat", "sub chat"} <= {s["preview"] for s in history}
    with connect_users_db() as conn:
        assert not conn.execute("SELECT 1 FROM shares WHERE token = ?", (share,)).fetchone()
    assert c.delete(f"/api/folders/{top}").status_code == 404
    assert c.delete(f"/api/folders/{FOLDERS}").status_code == 404


def test_a_tree_batch_deleting_a_folder_files_its_chats_and_stops_its_shares(owner):
    """The plain op path (a mirror round, the iPad relaying a delete) cleans
    up like DELETE /folders/{id}: the chats go to the library's history and
    the share dies; only the pages' refiling is the endpoint's own."""
    c, ws = owner
    top = _folder(c, "Relayed")
    sub = _folder(c, "Below", parent=top)
    assert c.put(f"/api/chats/{sub}", json={"messages": [{"role": "user", "text": "relayed chat"}]}).status_code == 200
    share = c.post(f"/api/share/folder/{sub}").json()["token"]
    r = c.post(f"/api/pages/{FOLDERS}/ops", json={"ops": [{"op": "delete", "id": top}]})
    assert r.status_code == 200, r.text
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chats WHERE bucket IN (?, ?)", (top, sub)).fetchone()[0] == 0
    assert "relayed chat" in {s["preview"] for s in c.get("/api/chat-history?bucket=home").json()["sessions"]}
    with connect_users_db() as conn:
        assert not conn.execute("SELECT 1 FROM shares WHERE token = ?", (share,)).fetchone()
    assert c.get(f"/api/share/{share}").status_code == 404


def test_deleting_a_label_takes_it_off_every_page(owner):
    c, _ws = owner
    gone, kept = _label(c, "gone"), _label(c, "kept too")
    page = _page(c, "Labelled", labels=[gone, kept])
    out = c.delete(f"/api/labels/{gone}").json()
    assert out["ids"] == [gone] and out["pages"] == [page["id"]]
    assert _props(c, page["id"])["labels"] == [kept]


def test_folder_chats_are_keyed_by_the_folder_id(owner):
    c, _ws = owner
    folder = _folder(c, "Chatty")
    assert c.put(f"/api/chats/{folder}", json={"messages": [{"role": "user", "text": "hi"}]}).status_code == 200
    _ops(c, FOLDERS, [{"op": "set", "id": folder, "content": "Renamed"}])
    assert c.get(f"/api/chats/{folder}").json()["messages"][0]["text"] == "hi"


def test_search_scope_is_a_folder_id(owner):
    c, _ws = owner
    top = _folder(c, "Scoped search")
    sub = _folder(c, "Below", parent=top)
    pages = {}
    for name, filed in (("inside", [sub]), ("outside", [])):
        page = pages[name] = _page(c, f"Search {name}", folders=filed)
        _ops(c, page["id"], [{"op": "insert", "id": blocks_store.new_block_id(), "parent": page["id"],
                              "content": "zebrafish notes"}])
    hits = c.get(f"/api/search?q=zebrafish&scope={top}").json()["results"]
    assert {h["page_id"] for h in hits} == {pages["inside"]["id"]}
    everywhere = c.get("/api/search?q=zebrafish").json()["results"]
    assert {p["id"] for p in pages.values()} <= {h["page_id"] for h in everywhere}


def test_the_tree_rooms_admit_members_only(owner):
    c, _ws = owner
    with c.websocket_connect(f"/api/ws/page/{FOLDERS}") as sock:
        hello = sock.receive_json()
        assert hello["t"] == "hello"
        _ops(c, FOLDERS, [{"op": "insert", "id": blocks_store.new_block_id(), "parent": FOLDERS, "content": "Live"}])
        for _ in range(5):
            msg = sock.receive_json()
            if msg["t"] == "ops":
                break
        assert msg["t"] == "ops" and msg["ops"][0]["content"] == "Live"
