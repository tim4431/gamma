"""The Zotero link's collections ↔ folders (gamma/zotero_sync_folders.py)
against the fake Zotero (zotero_fake.py): collections arrive as folders, and
a round after a round writes nothing; Zotero's renames and moves reach the
folders, the folder changes here go to Zotero; a Zotero trash deletes the
folders and its restore brings them back with the items' membership (a
collection trashed alone takes its subfolders; a shared folder, or one kept
on disk, stays whole until Zotero's trash is emptied), a folder deleted here
trashes its collections and an undo restores one; a name changed on both
sides keeps Gamma's and records Zotero's, which "Take Zotero's" brings (its
name or its place; refused once Zotero renamed it again); a collection
removed for good goes unless its folder changed here, also while the removal
is decided; a create whose answer was lost, a derived key Zotero holds
elsewhere, receive only, and a first link's matching by path. Rounds run
through ``POST …/sync?wait=1`` with the budget lifted; background rounds are
stubbed. Accounts are ``zd_…``."""

import pytest
from zotero_engine import (LIB, _by_name, _collection, _collection_rows, _conflicts, _folder_ops,  # noqa: F401
                           _folders, _link, _page, _page_of, _paths, _props, _queue_entries, _rows, _sync, _writes,
                           background, fake, held, idle, made, me, owner_id)
from zotero_idle import assert_idle

from gamma import ops, workspaces, zotero_local, zotero_sync_rows
from gamma import zotero_remote as zr
from gamma.blocks_store import FOLDERS, newest_change_seq
from gamma.db import connect_pages_db, connect_users_db, page_now
from gamma.zotero_web import object_key

USER, PASSWORD = "zd_owner", "pw-zd_owner-123"


# --- the first fill -------------------------------------------------------------------------------

def test_collections_arrive_as_folders_and_a_round_after_a_round_writes_nothing(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    status = _sync(me, ws)
    paths = _paths(ws)
    assert set(paths) == {("ML",), ("ML", "Transformers"), ("ML", "Transformers", "Efficient"), ("Reading",)}
    rows = _collection_rows(ws)
    assert {key: row["block_id"] for key, row in rows.items()} == {
        keys["ml"]: paths[("ML",)], keys["transformers"]: paths[("ML", "Transformers")],
        keys["efficient"]: paths[("ML", "Transformers", "Efficient")], keys["reading"]: paths[("Reading",)]}
    assert rows[keys["transformers"]]["zbase"] == rows[keys["transformers"]]["gbase"] == {
        "name": "Transformers", "parent": keys["ml"]}
    assert all(row["state"] == "live" and row["version"] == fake.data(LIB, key)["version"]
               for key, row in rows.items())
    assert status["blocks_added"] == 4 + status["pages_pulled"] and _writes(fake) == []  # the folders, the pages
    with connect_pages_db(ws) as conn:
        logged = conn.execute("SELECT actor, client FROM page_ops WHERE page_id = 'folders'").fetchall()
    assert set(logged) == {("zotero", "zotero")}
    folders = _folders(ws)
    for _ in range(2):
        calls = len(fake.calls)
        status = _sync(me, ws)
        assert _writes(fake, calls) == [] and status["blocks_added"] == status["blocks_changed"] == 0
    assert _folders(ws) == folders
    unstamped = lambda found: {k: {**row, "synced_at": ""} for k, row in found.items()}  # noqa: E731
    assert unstamped(_collection_rows(ws)) == unstamped(rows)


# --- Zotero's changes, Gamma's --------------------------------------------------------------------

def test_zotero_renames_and_moves_reach_the_folders(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    fake.edit(LIB, keys["reading"], name="To read")
    fake.edit(LIB, keys["efficient"], parentCollection=keys["ml"])
    calls = len(fake.calls)
    status = _sync(me, ws)
    assert set(_paths(ws)) == {("ML",), ("ML", "Transformers"), ("ML", "Efficient"), ("To read",)}
    assert _writes(fake, calls) == [] and status["blocks_changed"] == 2
    assert _collection_rows(ws)[keys["efficient"]]["gbase"] == {"name": "Efficient", "parent": keys["ml"]}
    assert _writes(fake, len(fake.calls)) == [] and _sync(me, ws)["blocks_changed"] == 0


def test_folder_changes_here_go_to_zotero(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    paths = _paths(ws)
    _folder_ops(ws, owner_id, [
        {"op": "set", "id": paths[("Reading",)], "content": "Later"},
        {"op": "move", "id": paths[("ML", "Transformers")], "parent": FOLDERS},
        {"op": "insert", "id": "zsNewFolder", "parent": paths[("ML",)], "content": "New"},
        {"op": "insert", "id": "zsNewChild", "parent": "zsNewFolder", "content": "Child"}])
    status = _sync(me, ws)
    assert _collection(fake, keys["reading"])["name"] == "Later"
    assert _collection(fake, keys["transformers"])["parentCollection"] is False
    new_key = object_key(ws, LIB, "collection", keys["ml"], "zsNewFolder", "")
    child_key = object_key(ws, LIB, "collection", new_key, "zsNewChild", "")
    assert _collection(fake, new_key)["name"] == "New" and _collection(fake, new_key)["parentCollection"] == keys["ml"]
    assert _collection(fake, child_key)["parentCollection"] == new_key
    rows = _collection_rows(ws)
    assert rows[new_key]["state"] == rows[child_key]["state"] == "live"
    assert rows[child_key]["version"] == fake.data(LIB, child_key)["version"]
    assert rows[keys["reading"]]["gbase"] == rows[keys["reading"]]["zbase"] == {"name": "Later", "parent": ""}
    assert status["last_error"] == ""
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []  # the next round finds nothing to send, nor to apply
    assert set(_paths(ws)) == {("ML",), ("Transformers",), ("Transformers", "Efficient"), ("Later",),
                               ("ML", "New"), ("ML", "New", "Child")}


# --- trash and restore ----------------------------------------------------------------------------

def test_a_zotero_trash_deletes_the_folders_and_its_restore_brings_them_back_with_membership(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    transformers = _paths(ws)[("ML", "Transformers")]
    page = _rows(ws, "item")[keys["attention"]]["page_id"]
    assert _props(ws, page)["folders"] == [transformers]
    fake.trash(LIB, keys["ml"])  # as Zotero desktop does: ML with Transformers and Efficient
    calls = len(fake.calls)
    _sync(me, ws)
    assert set(_paths(ws)) == {("Reading",)}
    rows = _collection_rows(ws)
    assert {k for k, row in rows.items() if row["state"] == "trashed"} == {
        keys["ml"], keys["transformers"], keys["efficient"]}
    assert _writes(fake, calls) == []  # nothing goes back: the items keep their membership in Zotero
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["transformers"]]
    item = _rows(ws, "item")[keys["attention"]]
    assert item["zbase"]["collections"] == item["gbase"]["collections"] == []
    assert keys["attention"] in rows[keys["transformers"]]["detail"]["members"]
    assert "folders" not in _props(ws, page)
    _sync(me, ws)  # the refiled pages read as no change of Gamma's
    assert _writes(fake, calls) == []
    fake.restore(LIB, keys["ml"])
    calls = len(fake.calls)
    _sync(me, ws)
    paths = _paths(ws)
    assert set(paths) == {("ML",), ("ML", "Transformers"), ("ML", "Transformers", "Efficient"), ("Reading",)}
    assert paths[("ML", "Transformers")] == transformers  # the folder again, under its old id
    rows = _collection_rows(ws)
    assert all(row["state"] == "live" for row in rows.values())
    assert rows[keys["transformers"]]["block_id"] == paths[("ML", "Transformers")]
    assert _props(ws, page)["folders"] == [paths[("ML", "Transformers")]]  # its item filed the page again
    assert _writes(fake, calls) == []
    item = _rows(ws, "item")[keys["attention"]]
    assert item["zbase"]["collections"] == item["gbase"]["collections"] == [keys["transformers"]]


def test_a_collection_trashed_alone_takes_its_subfolders_here_and_brings_them_back(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    fake.edit(LIB, keys["ml"], deleted=True)  # through the API: only ML is in the trash
    calls = len(fake.calls)
    _sync(me, ws)
    assert set(_paths(ws)) == {("Reading",)} and _writes(fake, calls) == []
    rows = _collection_rows(ws)
    assert rows[keys["transformers"]]["detail"].get("collateral") and rows[keys["transformers"]]["state"] == "trashed"
    assert not fake.data(LIB, keys["transformers"]).get("deleted")  # never trashed in Zotero for it
    _sync(me, ws)
    assert _writes(fake, calls) == []
    fake.edit(LIB, keys["ml"], deleted=False)
    _sync(me, ws)
    assert set(_paths(ws)) == {("ML",), ("ML", "Transformers"), ("ML", "Transformers", "Efficient"), ("Reading",)}
    assert all(row["state"] == "live" and not row["detail"].get("collateral") for row in _collection_rows(ws).values())
    assert _writes(fake, calls) == []


def _shares(ws) -> dict:
    with connect_users_db() as conn:
        return dict(conn.execute("SELECT folder, token FROM shares WHERE workspace_id = ? AND folder != ''", (ws,)))


def test_a_zotero_trash_keeps_a_shared_folder_whole_and_a_restore_finds_it(me, made, fake):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    transformers = _paths(ws)[("ML", "Transformers")]
    page = _page_of(ws, keys["attention"])
    r = me.post(f"/api/share/folder/{transformers}?ws={ws}")
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    fake.trash(LIB, keys["ml"])
    calls = len(fake.calls)
    _sync(me, ws)
    assert _shares(ws) == {transformers: token}
    assert set(_paths(ws)) == {("ML",), ("ML", "Transformers"), ("ML", "Transformers", "Efficient"), ("Reading",)}
    rows = _collection_rows(ws)
    for name in ("ml", "transformers", "efficient"):
        assert rows[keys[name]]["state"] == "trashed" and rows[keys[name]]["detail"].get("kept")
    assert _writes(fake, calls) == []  # no restore sent: the trash is Zotero's to undo
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["transformers"]]
    assert _props(ws, page)["folders"] == [transformers]
    _sync(me, ws)
    assert _writes(fake, calls) == []
    log = me.get(f"/api/zotero/links/{ws}/log?limit=50").json()["changes"]
    assert any(e["action"] == "kept here" for e in log)
    fake.restore(LIB, keys["ml"])
    _sync(me, ws)
    assert _paths(ws)[("ML", "Transformers")] == transformers and _shares(ws) == {transformers: token}
    assert all(row["state"] == "live" and not row["detail"].get("kept") for row in _collection_rows(ws).values())
    assert _props(ws, page)["folders"] == [transformers]
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["transformers"]]


def test_a_folder_kept_on_disk_stays_and_emptying_zoteros_trash_removes_it(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    reading = _paths(ws)[("Reading",)]
    with connect_users_db() as conn:
        conn.execute("INSERT INTO folder_links (id, workspace_id, folder_id, path, created_by, created_at) "
                     "VALUES ('zbDisk', ?, ?, ?, ?, ?)", (ws, reading, f"C:/zb/{ws}/Reading", owner_id, page_now()))
        conn.commit()
    try:
        fake.trash(LIB, keys["reading"])
        _sync(me, ws)
        assert ("Reading",) in _paths(ws) and _collection_rows(ws)[keys["reading"]]["detail"].get("kept")
        fake.delete(LIB, keys["reading"])  # the trash emptied: a removal for good
        _sync(me, ws)
        assert ("Reading",) not in _paths(ws) and keys["reading"] not in _collection_rows(ws)
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM folder_links WHERE id = 'zbDisk'")
            conn.commit()


def test_a_folder_deleted_here_trashes_its_collections_and_an_undo_restores_one(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    paths = _paths(ws)
    transformers, ml = paths[("ML", "Transformers")], paths[("ML",)]
    with connect_pages_db(ws) as conn:
        ops.delete_tree(ws, conn, FOLDERS, transformers, actor=owner_id, client="t")
    _sync(me, ws)
    assert _collection(fake, keys["transformers"])["deleted"] is True
    assert _collection(fake, keys["efficient"])["deleted"] is True
    assert not _collection(fake, keys["ml"]).get("deleted")
    # membership is never stripped: the items still name the collection for a restore in Zotero
    assert fake.data(LIB, keys["attention"])["collections"] == [keys["transformers"]]
    assert fake.data(LIB, keys["conference"])["collections"] == [keys["efficient"], keys["reading"]]
    rows = _collection_rows(ws)
    assert rows[keys["transformers"]]["state"] == rows[keys["efficient"]]["state"] == "trashed"
    calls = len(fake.calls)
    _sync(me, ws)
    assert _writes(fake, calls) == []
    # undo: the folder comes back under its own id, and so does its collection
    _folder_ops(ws, owner_id, [{"op": "insert", "id": transformers, "parent": ml, "content": "Transformers"}])
    _sync(me, ws)
    assert not _collection(fake, keys["transformers"]).get("deleted")
    assert _collection(fake, keys["efficient"])["deleted"] is True
    assert _collection_rows(ws)[keys["transformers"]]["state"] == "live"


# --- both sides changed ---------------------------------------------------------------------------

def test_a_name_changed_on_both_sides_keeps_gammas_and_records_zoteros(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    reading = _paths(ws)[("Reading",)]
    fake.edit(LIB, keys["reading"], name="Zotero's name")
    _folder_ops(ws, owner_id, [{"op": "set", "id": reading, "content": "Gamma's name"}])
    _sync(me, ws)
    assert _folders(ws)[reading][0] == "Gamma's name" and _collection(fake, keys["reading"])["name"] == "Gamma's name"
    [row] = me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
    assert (row["kind"], row["page_id"], row["block_id"]) == ("zotero_field", FOLDERS, reading)
    assert (row["mine"], row["theirs"]) == ("Name: Gamma's name", "Name: Zotero's name")


def test_taking_zoteros_name_of_a_folder_renamed_on_both_sides_renames_it_here_and_there(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    reading = _paths(ws)[("Reading",)]
    fake.edit(LIB, keys["reading"], name="Zotero's name")
    _folder_ops(ws, owner_id, [{"op": "set", "id": reading, "content": "Gamma's name"}])
    _sync(me, ws)
    [row] = _conflicts(me, ws, "zotero_field")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _folders(ws)[reading][0] == "Zotero's name"
    _sync(me, ws)
    assert fake.data(LIB, keys["reading"])["name"] == "Zotero's name"


def test_taking_zoteros_place_of_a_folder_moved_on_both_sides_moves_it_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    paths = _paths(ws)
    reading, ml = paths[("Reading",)], paths[("ML",)]
    fake.edit(LIB, keys["reading"], parentCollection=keys["transformers"])
    _folder_ops(ws, owner_id, [{"op": "move", "id": reading, "parent": ml}])
    _sync(me, ws)
    [row] = _conflicts(me, ws, "zotero_field")
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{row['id']}", json={"choice": "theirs"})
    assert r.status_code == 200, r.text
    assert _folders(ws)[reading][1] == paths[("ML", "Transformers")]
    _sync(me, ws)
    assert fake.data(LIB, keys["reading"])["parentCollection"] == keys["transformers"]


def test_taking_zoteros_folder_name_is_refused_once_zotero_renamed_it_again(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    reading = _paths(ws)[("Reading",)]
    _folder_ops(ws, owner_id, [{"op": "set", "id": reading, "content": "Reading (Gamma)"}])
    fake.edit(LIB, keys["reading"], name="Reading (Zotero)")
    _sync(me, ws)
    (conflict,) = [c for c in me.get(f"/api/zotero/links/{ws}/conflicts").json()["conflicts"]
                   if c["kind"] == "zotero_field" and c["block_id"] == reading]
    fake.edit(LIB, keys["reading"], name="Reading (Zotero, later)")
    _sync(me, ws)
    assert ("Reading (Zotero, later)",) in _paths(ws)
    r = me.post(f"/api/zotero/links/{ws}/conflicts/{conflict['id']}", json={"choice": "theirs"})
    assert r.status_code == 409
    assert ("Reading (Zotero, later)",) in _paths(ws)


# --- removals for good ----------------------------------------------------------------------------

def test_a_collection_removed_for_good_goes_unless_its_folder_changed_here(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    paths = _paths(ws)
    fake.delete(LIB, keys["efficient"])
    _sync(me, ws)
    assert ("ML", "Transformers", "Efficient") not in _paths(ws) and keys["efficient"] not in _collection_rows(ws)
    _folder_ops(ws, owner_id, [{"op": "set", "id": paths[("Reading",)], "content": "Reading (mine)"}])
    fake.delete(LIB, keys["reading"])
    _sync(me, ws)
    assert _folders(ws)[paths[("Reading",)]][0] == "Reading (mine)"  # changed here: it stays...
    [(key, row)] = [(k, r) for k, r in _collection_rows(ws).items() if r["block_id"] == paths[("Reading",)]]
    assert key != keys["reading"] and row["state"] == "live"  # ...as a new collection under a fresh key
    assert _collection(fake, key)["name"] == "Reading (mine)"
    # its pages were looked at again, so their items joined it (Zotero had taken them out of the old one)
    assert fake.data(LIB, keys["thesis"])["collections"] == [key]
    assert set(fake.data(LIB, keys["conference"])["collections"]) == {key}  # Efficient went for good too


def test_a_folder_renamed_while_zoteros_removal_is_decided_stays_and_goes_to_zotero_anew(me, made, fake, owner_id,
                                                                                       monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    reading = _paths(ws)[("Reading",)]
    real = zotero_local.delete_folder

    def rename_first(ws_, fid, **kw):
        _folder_ops(ws_, owner_id, [{"op": "set", "id": fid, "content": "Reading, renamed"}])
        monkeypatch.setattr(zotero_local, "delete_folder", real)
        return real(ws_, fid, **kw)

    monkeypatch.setattr(zotero_local, "delete_folder", rename_first)
    fake.delete(LIB, keys["reading"])
    _sync(me, ws)
    assert _paths(ws)[("Reading, renamed",)] == reading
    _sync(me, ws)
    _sync(me, ws)
    assert _paths(ws)[("Reading, renamed",)] == reading
    assert "Reading, renamed" in {c["name"] for c in fake.collections(LIB) if not c.get("deleted")}


def test_a_folder_touched_while_zoteros_removal_is_decided_is_decided_again_and_goes_found_unchanged(
        me, made, fake, owner_id, monkeypatch):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    real = zotero_local.delete_folder

    def touch_first(ws_, fid, **kw):  # renamed and named back as the removal goes: changed, the same after
        _folder_ops(ws_, owner_id, [{"op": "set", "id": fid, "content": "Reading, renamed"}])
        _folder_ops(ws_, owner_id, [{"op": "set", "id": fid, "content": "Reading"}])
        monkeypatch.setattr(zotero_local, "delete_folder", real)
        return real(ws_, fid, **kw)

    monkeypatch.setattr(zotero_local, "delete_folder", touch_first)
    fake.delete(LIB, keys["reading"])
    _sync(me, ws)
    _sync(me, ws)
    assert ("Reading",) not in _paths(ws) and keys["reading"] not in _collection_rows(ws)
    assert "Reading" not in {c["name"] for c in fake.collections(LIB) if not c.get("deleted")}
    assert_idle(fake, ws)


def test_a_folder_touched_again_while_the_collections_pass_decides_zoteros_removal_is_decided_again_and_goes(
        me, made, fake, owner_id, monkeypatch):
    """The same race twice: the removal the listing put off is decided
    again in that round's collections pass, and the folder changes again
    as it is. The entry that removal queued again stays, though the next
    listing no longer names the collection."""
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    real, touched = zotero_local.delete_folder, []

    def touch_twice(ws_, fid, **kw):  # renamed and named back as each of the first two removals goes
        if len(touched) < 2:
            touched.append(fid)
            _folder_ops(ws_, owner_id, [{"op": "set", "id": fid, "content": "Reading, renamed"}])
            _folder_ops(ws_, owner_id, [{"op": "set", "id": fid, "content": "Reading"}])
        return real(ws_, fid, **kw)

    monkeypatch.setattr(zotero_local, "delete_folder", touch_twice)
    fake.delete(LIB, keys["reading"])
    _sync(me, ws)
    _sync(me, ws)
    assert len(touched) == 2
    assert ("Reading",) not in _paths(ws) and keys["reading"] not in _collection_rows(ws)
    assert_idle(fake, ws)


# --- a create whose answer was lost, receive only, a first link -----------------------------------

def test_a_create_whose_answer_was_lost_is_confirmed_and_never_made_twice(me, made, fake, owner_id):
    ws = _link(me)
    _sync(me, ws)
    _folder_ops(ws, owner_id, [{"op": "insert", "id": "zsLost1", "parent": FOLDERS, "content": "Lost answer"}])
    key = object_key(ws, LIB, "collection", "", "zsLost1", "")
    sent = {"name": "Lost answer", "parent": ""}
    with connect_pages_db(ws) as conn:  # the row went first, then the create landed and the answer was lost
        zotero_sync_rows.write_rows(conn, [zotero_sync_rows.new_row(key, "collection", page_id=FOLDERS,
                                                                    block_id="zsLost1", version=-1, state="pending",
                                                                    detail={"sent": sent})])
        conn.commit()
    fake.add_collection(LIB, "Lost answer", key=key)
    calls = len(fake.calls)
    _sync(me, ws)
    row = _collection_rows(ws)[key]
    assert row["state"] == "live" and row["version"] == fake.data(LIB, key)["version"]
    assert row["zbase"] == row["gbase"] == sent and "sent" not in row["detail"]
    assert _writes(fake, calls) == [] and list(_by_name(fake)).count("Lost answer") == 1


def test_a_derived_key_zotero_holds_elsewhere_gets_a_fresh_one(me, made, fake, owner_id, held):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    key = object_key(ws, LIB, "collection", "", "zsClash1", "")
    fake.add_collection(LIB, "Someone else's", parent=keys["ml"], key=key)  # listed with the next round
    _folder_ops(ws, owner_id, [{"op": "insert", "id": "zsClash1", "parent": FOLDERS, "content": "Mine"}])
    page = _page(ws, "zsInClash", "Filed in mine", folders=["zsClash1"])
    with connect_pages_db(ws) as conn:  # the feed read already: only the folder tree is left to look at
        zr.set_state(conn, local_cursor=str(newest_change_seq(conn)))
        conn.execute("DELETE FROM zotero_queue WHERE kind = 'page'")
        zr.enqueue(conn, [zr.page_entry(FOLDERS)])
        conn.commit()
    _sync(me, ws)
    assert ("ML", "Someone else's") in _paths(ws)  # Zotero's collection came as a folder of its own
    assert f"page:{page}" in _queue_entries(ws)  # the folder's key changed: its pages' items join the new one
    _sync(me, ws)
    [(mine, row)] = [(k, r) for k, r in _collection_rows(ws).items() if r["block_id"] == "zsClash1"]
    assert mine != key and row["state"] == "live" and _collection(fake, mine)["name"] == "Mine"
    assert _collection(fake, key)["name"] == "Someone else's"
    # Zotero holding a folder's derived key in its trash, under the same parent: ours, restored with our fields
    trashed = object_key(ws, LIB, "collection", "", "zsClash2", "")
    fake.add_collection(LIB, "Old name", key=trashed)
    fake.trash(LIB, trashed)
    _folder_ops(ws, owner_id, [{"op": "insert", "id": "zsClash2", "parent": FOLDERS, "content": "New name"}])
    _sync(me, ws)
    assert not _collection(fake, trashed).get("deleted") and _collection(fake, trashed)["name"] == "New name"
    assert _collection_rows(ws)[trashed]["block_id"] == "zsClash2" and _collection_rows(ws)[trashed]["state"] == "live"


def test_receive_only_keeps_folder_changes_here_until_two_way(me, made, fake, owner_id):
    keys = made["keys"]
    ws = _link(me)
    _sync(me, ws)
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "pull"})
    reading = _paths(ws)[("Reading",)]
    _folder_ops(ws, owner_id, [{"op": "set", "id": reading, "content": "Read later"}])
    fake.edit(LIB, keys["ml"], name="Machine learning")
    calls = len(fake.calls)
    _sync(me, ws)
    assert ("Machine learning",) in _paths(ws) and _writes(fake, calls) == []
    assert _collection(fake, keys["reading"])["name"] == "Reading"
    me.patch(f"/api/zotero/links/{ws}", json={"mode": "two-way"})
    _sync(me, ws)
    assert _collection(fake, keys["reading"])["name"] == "Read later"


@pytest.mark.parametrize("adopt, ml", [("theirs", "ML"), ("mine", "ml")])
def test_a_first_link_of_an_existing_workspace_matches_folders_by_path(me, made, fake, owner_id, adopt, ml):
    keys = made["keys"]
    ws = workspaces.create(f"Existing {adopt}", owner_id)["id"]
    _folder_ops(ws, owner_id, [{"op": "insert", "id": "zsMl", "parent": FOLDERS, "content": "ml"},
                               {"op": "insert", "id": "zsTr", "parent": "zsMl", "content": "Transformers"},
                               {"op": "insert", "id": "zsMine", "parent": FOLDERS, "content": "Mine"}])
    _link(me, workspace_id=ws, adopt=adopt)
    _sync(me, ws)
    paths = _paths(ws)
    assert set(paths) == {(ml,), (ml, "Transformers"), (ml, "Transformers", "Efficient"), ("Reading",), ("Mine",)}
    assert paths[(ml,)] == "zsMl" and paths[(ml, "Transformers")] == "zsTr"  # matched, not made twice
    rows = _collection_rows(ws)
    assert rows[keys["ml"]]["block_id"] == "zsMl" and rows[keys["transformers"]]["block_id"] == "zsTr"
    assert _collection(fake, keys["ml"])["name"] == ml  # the side ``adopt`` names took it
    assert "Mine" in _by_name(fake)
    assert me.get(f"/api/zotero/links/{ws}").json()["conflicts_open"] == 0  # decided by adopt: kept as resolved
    resolved = me.get(f"/api/zotero/links/{ws}/conflicts?resolved=1").json()["conflicts"]
    assert [(c["mine"], c["theirs"]) for c in resolved] == [("Name: ml", "Name: ML")]
