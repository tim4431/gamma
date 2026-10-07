"""Restoring a workspace backup (gamma/ws_backup.restore_zip) keeps the live
workspace consistent: a damaged backup is refused before anything changes, a
replace keeps what it replaces as a "pre-restore" snapshot, every restored
page moves FORWARD in its op log (open tabs reload, catch-up never skips),
the change feed sees restored and removed pages, and a merge never grafts
backup blocks into pages nobody restored."""

import json
import sqlite3
import threading
import zipfile
from contextlib import closing

from fastapi.testclient import TestClient

from conftest import login, make_user
from gamma import ops, ws_backup
from gamma.blocks_store import page_root_id, trash_entry
from gamma.db import connect_pages_db, register_functions, ws_uploads_dir
from gamma.routers.sync import changes
from test_backup_safety import _damage

OLD = "2024-01-01T00:00:00.000000Z"
PW = "rc-pass-12345"


def _account(name):
    ws = make_user(name, PW)
    return ws, login(name, PW)


def _page(c, title):
    r = c.post("/api/pages", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _insert(c, page, bid, text, parent=None):
    r = c.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": bid, "parent": parent or page, "content": text, "props": {}}]})
    assert r.status_code == 200, r.text
    return r.json()["seq"]


def _conn(ws):
    return closing(connect_pages_db(ws))


def _kind(conn, page_id):
    """What the change log says the page is: "live", "deleted" or None."""
    row = conn.execute("SELECT kind FROM page_changes WHERE page_id = ?", (page_id,)).fetchone()
    return row[0] if row else None


def _next(sock, kind, timeout=10):
    """The next socket message of a kind; fails instead of hanging."""
    got = []

    def read():
        try:
            while True:
                msg = sock.receive_json()
                if msg.get("t") == kind:
                    got.append(msg)
                    return
        except Exception as e:  # noqa: BLE001 — the socket closed under the reader
            got.append(e)

    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(timeout)
    assert got and isinstance(got[0], dict), f"no {kind} message on the socket"
    return got[0]


def _damaged_pages_db(path):
    """A pages.db whose sqlite_master reads fine but one b-tree page in the
    middle is trashed: only an integrity check finds it."""
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, parent_id TEXT, position TEXT NOT NULL, "
                     "content TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}', "
                     "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO unified_blocks VALUES ('root', NULL, 'a0', '', '{}', 'x', 'x')")
        conn.commit()
    _damage(path)


# --- check first, keep what is replaced ----------------------------------------------

def test_a_damaged_backup_is_refused_before_anything_changes(tmp_path):
    ws, c = _account("rc_damaged")
    keep = _page(c, "Live page that must survive")
    db = tmp_path / "pages.db"
    _damaged_pages_db(db)
    zpath = tmp_path / "backup.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(db, "pages.db")
        z.writestr("manifest.json", json.dumps({"format": "gamma-backup-1"}))
    for mode in ("replace", "merge"):
        with open(zpath, "rb") as f:
            r = c.post(f"/api/import-data?mode={mode}", files={"file": ("b.zip", f.read(), "application/zip")})
        assert r.status_code == 400 and "damaged" in r.json()["detail"], r.text
    assert c.get(f"/api/blocks/{keep}").status_code == 200
    assert ws_backup.list_backups(ws) == []  # refused before the pre-restore snapshot, too
    with _conn(ws) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_replace_keeps_the_current_state_as_a_restorable_pre_restore_snapshot():
    ws, c = _account("rc_prerestore")
    empty = ws_backup.create(ws, label="empty", uploads=False)
    up = c.post("/api/uploads", files={"file": ("paper.pdf", b"%PDF-1.4 the only copy", "application/pdf")}).json()
    pid = _page(c, "Paper after the snapshot")
    c.post(f"/api/pages/{pid}/attachment", json={"doc_id": up["doc_id"], "source_url": up["source_url"]})

    r = c.post(f"/api/workspaces/{ws}/backups/{empty['name']}/restore?mode=replace")
    assert r.status_code == 200, r.text
    pre = ws_backup.info(ws, r.json()["pre_restore"])
    assert pre and pre["auto"] and pre["label"] == "pre-restore" and pre["uploads"] and pre["upload_files"] == 1
    assert c.get(f"/api/blocks/{pid}").status_code == 404
    # the pre-restore snapshot does not count against the manual cap
    listed = c.get(f"/api/workspaces/{ws}/backups").json()["backups"]
    assert [b["name"] for b in listed if b["auto"]] == [pre["name"]]

    # the replaced state comes back from it, the PDF included — even once the file left the workspace
    (ws_uploads_dir(ws) / f"{up['doc_id']}.pdf").unlink()
    r = c.post(f"/api/workspaces/{ws}/backups/{pre['name']}/restore?mode=replace")
    assert r.status_code == 200, r.text
    assert c.get(f"/api/blocks/{pid}").json()["content"] == "Paper after the snapshot"
    assert (ws_uploads_dir(ws) / f"{up['doc_id']}.pdf").read_bytes() == b"%PDF-1.4 the only copy"


def test_pre_restore_snapshots_are_capped(monkeypatch):
    ws, c = _account("rc_precap")
    snap = ws_backup.create(ws, label="base", uploads=False)
    monkeypatch.setattr(ws_backup, "PRE_RESTORE_KEEP", 2)
    taken = []
    for i in range(3):
        _page(c, f"state {i}")
        taken.append(ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snap["name"]), "replace")["pre_restore"])
    autos = [b["name"] for b in ws_backup.list_backups(ws) if b["auto"]]
    assert sorted(autos) == sorted(taken[1:]) and ws_backup.info(ws, snap["name"])


def test_replace_is_refused_when_the_current_state_cannot_be_saved(monkeypatch):
    ws, c = _account("rc_nospace")
    snap = ws_backup.create(ws, label="base", uploads=False)
    keep = _page(c, "Made after the snapshot")
    monkeypatch.setattr(ws_backup, "MIN_FREE_BYTES", 1 << 62)  # the disk is "full"
    r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=replace")
    assert r.status_code == 400 and "nothing was restored" in r.json()["detail"]
    assert c.get(f"/api/blocks/{keep}").status_code == 200


def test_an_old_backup_is_normalized_and_given_the_current_shapes_before_the_swap(tmp_path):
    ws, c = _account("rc_legacy")
    db = tmp_path / "pages.db"
    with closing(sqlite3.connect(str(db))) as conn:  # before page_ops and the change log existed
        conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, parent_id TEXT, position TEXT NOT NULL, "
                     "content TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}', "
                     "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO unified_blocks VALUES ('root', NULL, 'a0', '', '{}', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO unified_blocks VALUES ('oldPage', 'root', 'a0', 'Old page', '{}', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO unified_blocks VALUES ('oldNote', 'oldPage', 'a0', "
                     "'see ![c](/api/uploads/i.png){:width 120}', '{}', ?, ?)", (OLD, OLD))
        conn.commit()
    zpath = tmp_path / "old.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(db, "pages.db")
    ws_backup.restore_zip(ws, zpath, "replace")
    assert c.get("/api/blocks/oldNote").json()["content"] == "see ![c|120](/api/uploads/i.png)"
    with _conn(ws) as conn:
        # the block table's hot fields, the page ids filled in by the walk (migration step 26's)
        assert dict(conn.execute("SELECT id, page_id FROM unified_blocks WHERE id IN ('root', 'oldPage', 'oldNote')")) \
            == {"root": "", "oldPage": "oldPage", "oldNote": "oldPage"}
        assert conn.execute("SELECT kind FROM unified_blocks WHERE id = 'oldNote'").fetchone()[0] == "note"
        log = ops.ops_since(conn, "oldPage", 0)[0]
        assert [b["ops"] for b in log] == [[{"op": "reload"}]]
        assert _kind(conn, "oldPage") == "live"  # the change log (step 27's), the page touched


def test_a_backup_with_tombstones_restores_them_into_the_change_log(tmp_path):
    """A backup from before migration step 27 keeps its deleted pages in
    ``deleted_pages``: restored either way, they are deleted rows of the live
    change log (and the old table is gone), so a copy of the workspace still
    tells them from pages it never had."""
    ws, c = _account("rc_tombs")
    live = _page(c, "Live before the restore")
    db = tmp_path / "pages.db"
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, parent_id TEXT, position TEXT NOT NULL, "
                     "content TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}', "
                     "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE deleted_pages (page_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL, "
                     "actor TEXT NOT NULL DEFAULT '')")
        conn.execute("INSERT INTO unified_blocks VALUES ('root', NULL, 'a0', '', '{}', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO unified_blocks VALUES ('trash', NULL, 'a1', '', '{}', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO unified_blocks VALUES ('keptPage', 'root', 'a0', 'Kept', '{}', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO unified_blocks VALUES ('binned', 'trash', 'a0', 'Binned', ?, ?, ?)",
                     (json.dumps({"deleted_at": OLD, "deleted_by": "someone"}), OLD, OLD))
        conn.executemany("INSERT INTO deleted_pages VALUES (?, ?, 'someone')", [("binned", OLD), ("goneTomb", OLD)])
        conn.commit()
    zpath = tmp_path / "tombs.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(db, "pages.db")
    with _conn(ws) as conn:
        cursor = changes(conn, "", 500)["cursor"]
    ws_backup.restore_zip(ws, zpath, "replace")
    with _conn(ws) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'deleted_pages'").fetchone()
        assert {p: _kind(conn, p) for p in ("keptPage", "binned", "goneTomb", live)} == {
            "keptPage": "live", "binned": "deleted", "goneTomb": "deleted", live: "deleted"}
        feed = changes(conn, cursor, 500)
    # (the folder and label trees are restored too: a replace rewrites them)
    assert {p["id"] for p in feed["pages"]} == {"keptPage", "folders", "labels"}
    assert {d["id"] for d in feed["deleted"]} == {"binned", "goneTomb", live}
    # a merge of it: the page it brings back is live, nothing else moves
    ws2, c2 = _account("rc_tombs_merge")
    with _conn(ws2) as conn:
        cursor = changes(conn, "", 500)["cursor"]
    assert ws_backup.restore_zip(ws2, zpath, "merge")["pages_added"] == 1
    with _conn(ws2) as conn:
        assert _kind(conn, "keptPage") == "live" and _kind(conn, "goneTomb") is None
        assert [p["id"] for p in changes(conn, cursor, 500)["pages"]] == ["keptPage"]


# --- open pages reload, seqs only move forward, the feed sees it -------------------

def test_a_backup_from_before_folder_blocks_is_converted_on_restore(tmp_path):
    """A backup from before migration step 29 files its pages by path and
    label name: restored either way, it goes through step 29's conversion
    first (``normalize.folder_blocks``) — the trees made, the pages filed by
    id, the folder chats moved to their folder — and a merge into a library
    that has a folder at the same path files the pages there."""
    from test_migrations import _v28_pages_db
    from gamma.blocks_store import folder_by_path, folder_paths, label_names

    db = tmp_path / "pages.db"
    _v28_pages_db(db, [("trash", None, "", {}),
                       ("rcFiled", "root", "Filed by path", {"folder": "Lab/Readout", "category": "todo"})])
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute("INSERT INTO chats (bucket, messages, updated_at) VALUES ('home:Lab', '[]', ?)", (OLD,))
        conn.commit()
    zpath = tmp_path / "old-folders.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(db, "pages.db")

    ws, c = _account("rc_folders")
    ws_backup.restore_zip(ws, zpath, "replace")
    with _conn(ws) as conn:
        props = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = 'rcFiled'").fetchone()[0])
        readout = folder_by_path(conn, ["Lab", "Readout"])
        assert props == {"folders": readout, "labels": list(label_names(conn))}
        assert conn.execute("SELECT bucket FROM chats").fetchall() == [tuple(folder_by_path(conn, ["Lab"]))]
        assert conn.execute("SELECT kind FROM unified_blocks WHERE id = ?", (readout[0],)).fetchone()[0] == "folder"

    # a library with its own "Lab / Readout" (another id): the merge files the page there
    ws2, c2 = _account("rc_folders_merge")
    r = c2.post("/api/pages/folders/ops", json={"ops": [
        {"op": "insert", "id": "rcMyLab", "parent": "folders", "content": "Lab"},
        {"op": "insert", "id": "rcMyReadout", "parent": "rcMyLab", "content": "Readout"}]})
    assert r.status_code == 200, r.text
    assert ws_backup.restore_zip(ws2, zpath, "merge")["pages_added"] == 1
    with _conn(ws2) as conn:
        props = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = 'rcFiled'").fetchone()[0])
        assert props["folders"] == ["rcMyReadout"]
        assert sorted(map(tuple, folder_paths(conn).values())) == [("Lab",), ("Lab", "Readout")]
        assert conn.execute("SELECT bucket FROM chats").fetchall() == [("rcMyLab",)]


def test_a_backup_from_before_the_highlight_shape_is_converted_on_restore(tmp_path):
    """A backup from before migration step 30 (a second highlight id, a page
    size per rect, the stored copy's URL, links by highlight id) goes
    through step 30's rewrite on a replace and on a merge
    (``normalize.highlight_shape``), after the content normalizers: the
    oldest pages' ``sourceUrl`` becomes a ``source_url``, which goes when
    it is the stored copy's."""
    from test_migrations import _v29_highlights_db, assert_highlight_shape

    doc = "c" * 24
    db = tmp_path / "pages.db"
    _v29_highlights_db(db, doc)
    with closing(sqlite3.connect(str(db))) as conn:
        register_functions(conn)
        conn.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at, "
                     "page_id) VALUES ('camel', 'root', 'a1', 'Oldest', ?, ?, ?, 'camel')",
                     (json.dumps({"doc_id": "b" * 24, "sourceUrl": f"/api/uploads/{'b' * 24}.pdf"}), OLD, OLD))
        conn.commit()
    zpath = tmp_path / "old-highlights.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.write(db, "pages.db")
    for name, mode in (("rc_shape", "replace"), ("rc_shape_merge", "merge")):
        ws, c = _account(name)
        ws_backup.restore_zip(ws, zpath, mode)
        with _conn(ws) as conn:
            props = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT id, properties FROM unified_blocks")}
            assert conn.execute("SELECT kind FROM unified_blocks WHERE id = 'hl'").fetchone()[0] == "highlight"
        assert_highlight_shape(props, doc)
        assert props["camel"] == {"doc_id": "b" * 24}


def test_replace_moves_every_page_forward_deletes_the_removed_and_reloads_open_tabs():
    ws, c = _account("rc_seq")
    pid = _page(c, "Restored page")
    _insert(c, pid, "rcKeep", "kept")
    with _conn(ws) as conn:  # the backup's rows carry old stamps: the feed must not rely on them
        conn.execute("UPDATE unified_blocks SET updated_at = ?", (OLD,))
        conn.commit()
    snap = ws_backup.create(ws, label="before")
    for i in range(3):
        seen = _insert(c, pid, f"rcLate{i}", f"written after the backup {i}")
    later = _page(c, "Made after the backup")
    with _conn(ws) as conn:
        cursor = changes(conn, "", 500)["cursor"]

    from gamma.app import app
    with TestClient(app, cookies=c.cookies) as tab:
        with tab.websocket_connect(f"/api/ws/page/{pid}?ws={ws}&client=tabB") as sock:
            assert sock.receive_json()["seq"] == seen
            r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=replace")
            assert r.status_code == 200, r.text
            assert r.json()["pages_removed"] == 1
            _next(sock, "reload")  # the open tab refetches instead of trusting its tree

    with _conn(ws) as conn:
        top = ops.latest_seq(conn, pid)
        assert top > seen  # never back: a tab at `seen` would drop everything up to it
        assert _kind(conn, later) == "deleted" and _kind(conn, pid) == "live"
        feed = changes(conn, cursor, 500)
    # the live change log carried on: the cursor from before lists exactly what the restore did
    assert [p["id"] for p in feed["pages"]] == [pid, "folders", "labels"]
    assert [d["id"] for d in feed["deleted"]] == [later]
    # a tab at the last seq before the restore gets the reload as the next batch; one
    # further back cannot catch up from the log (its gap is gone): it reloads too
    assert top == seen + 1
    assert c.get(f"/api/pages/{pid}/ops", params={"since": seen}).json()["batches"][0]["ops"] == [{"op": "reload"}]
    assert c.get(f"/api/pages/{pid}/ops", params={"since": seen - 1}).status_code == 410
    # the next write keeps counting up; the blocks written after the backup are gone
    assert _insert(c, pid, "rcAfter", "after the restore") == top + 1
    assert c.get("/api/blocks/rcLate1").status_code == 404


def test_catch_up_answers_reload_when_the_log_cannot_continue_from_the_client():
    ws, c = _account("rc_since")
    pid = _page(c, "Log page")
    for i in range(3):
        _insert(c, pid, f"rcS{i}", str(i))
    with _conn(ws) as conn:
        assert [b["seq"] for b in ops.ops_since(conn, pid, 1)[0]] == [2, 3]
        assert ops.ops_since(conn, pid, 3) == ([], False)       # up to date
        assert ops.ops_since(conn, pid, 7) == ([], True)        # ahead of the log: it was replaced
        conn.execute("DELETE FROM page_ops WHERE page_id = ?", (pid,))
        ops.log_reload(conn, pid, "t", after=9)                  # a restore moved the page forward
        conn.commit()
        assert ops.latest_seq(conn, pid) == 10
        assert ops.ops_since(conn, pid, 3) == ([], True)        # 4..9 are not in the log
        assert [b["seq"] for b in ops.ops_since(conn, pid, 9)[0]] == [10]


def test_merge_brings_a_deleted_page_back_forward_in_its_log_and_visible_to_the_feed():
    ws, c = _account("rc_mergefeed")
    pid = _page(c, "Deleted by mistake")
    for i in range(4):
        seen = _insert(c, pid, f"rcM{i}", f"note {i}")
    with _conn(ws) as conn:
        conn.execute("UPDATE unified_blocks SET updated_at = ?", (OLD,))
        conn.commit()
    snap = ws_backup.create(ws, label="weekly")
    with _conn(ws) as conn:
        ops.delete_page(ws, conn, pid, actor="rc_mergefeed")  # for good: log dropped, a deleted row left
        cursor = changes(conn, "", 500)["cursor"]
    r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=merge")
    assert r.status_code == 200 and r.json()["pages_added"] == 1, r.text
    with _conn(ws) as conn:
        assert _kind(conn, pid) == "live"
        assert ops.latest_seq(conn, pid) == seen + 1
        feed = changes(conn, cursor, 500)
    assert pid in {p["id"] for p in feed["pages"]} and not feed["deleted"]
    # an old tab that saw the page's last batch gets the reload, not silence
    assert c.get(f"/api/pages/{pid}/ops", params={"since": seen}).json()["batches"][0]["ops"] == [{"op": "reload"}]


# --- a merge adds whole pages and grafts nothing -----------------------------------

def test_merge_gives_blocks_that_moved_to_another_page_fresh_ids():
    ws, c = _account("rc_graft")
    a, b = _page(c, "page A"), _page(c, "page B")
    _insert(c, a, "rcX", "section X")
    _insert(c, a, "rcY", "kept child", parent="rcX")
    _insert(c, a, "rcZ", "old draft the user deleted later", parent="rcX")
    snap = ws_backup.create(ws, label="weekly")
    assert c.post("/api/blocks/rcX/reorder", json={"parent_id": b}).status_code == 200  # "Move to page" B
    assert c.post(f"/api/pages/{b}/ops", json={"client": "t", "ops": [{"op": "delete", "id": "rcZ"}]}).status_code == 200
    with _conn(ws) as conn:
        ops.delete_page(ws, conn, a, actor="rc_graft")

    r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=merge")
    assert r.status_code == 200 and r.json()["pages_added"] == 1, r.text
    # page B is exactly as the user left it: nothing grafted under its X
    tree_b = c.get(f"/api/blocks/{b}/subtree").json()["block"]["children"]
    assert [(k["id"], [g["id"] for g in k["children"]]) for k in tree_b] == [("rcX", ["rcY"])]
    # page A came back whole, the blocks whose ids B holds now under fresh ones
    tree_a = c.get(f"/api/blocks/{a}/subtree").json()["block"]["children"]
    assert len(tree_a) == 1 and tree_a[0]["content"] == "section X" and tree_a[0]["id"] != "rcX"
    kids = {k["content"]: k["id"] for k in tree_a[0]["children"]}
    assert set(kids) == {"kept child", "old draft the user deleted later"}
    assert kids["kept child"] != "rcY" and kids["old draft the user deleted later"] == "rcZ"
    with _conn(ws) as conn:  # every restored row hangs under page A
        for bid in [tree_a[0]["id"], *kids.values()]:
            assert page_root_id(conn, bid) == a
    # merging the same snapshot again adds nothing
    again = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=merge").json()
    assert again["pages_added"] == 0


def test_merge_brings_back_a_page_that_is_only_in_recently_deleted():
    ws, c = _account("rc_trash")
    pid = _page(c, "Trashed by mistake")
    _insert(c, pid, "rcT1", "in the backup")
    assert c.put(f"/api/chats/{pid}", json={"messages": [{"role": "user", "content": "keep me"}]}).status_code == 200
    snap = ws_backup.create(ws, label="weekly")
    seen = _insert(c, pid, "rcT2", "written after the backup")
    assert c.delete(f"/api/blocks/{pid}").status_code == 200  # to Recently deleted
    with _conn(ws) as conn:
        assert trash_entry(conn, pid)["id"] == pid
    zpath = ws_backup.backup_path(ws, snap["name"])
    planned = {p["id"]: p["action"] for p in ws_backup.preview_zip(ws, zpath)["pages"]}
    assert planned[pid] == "create"  # the review agrees: a trashed page is not "already there"

    r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=merge")
    assert r.status_code == 200 and r.json()["pages_added"] == 1 and r.json()["from_trash"] == 1, r.text
    # the backup's version is live again, under the same ids; the trashed copy is gone
    tree = c.get(f"/api/blocks/{pid}/subtree").json()["block"]
    assert tree["content"] == "Trashed by mistake" and [k["id"] for k in tree["children"]] == ["rcT1"]
    assert c.get("/api/blocks/rcT2").status_code == 404
    with _conn(ws) as conn:
        assert not any(trash_entry(conn, b) for b in (pid, "rcT1", "rcT2"))
        assert _kind(conn, pid) == "live"
        assert ops.latest_seq(conn, pid) > seen  # its log kept and moved forward
    assert c.get(f"/api/chats/{pid}").json()["messages"][0]["content"] == "keep me"


def test_fresh_ids_never_keep_a_row_outside_the_copy():
    rows = [("P", "root", "a0", "", "{}", OLD, OLD, "P"), ("X", "P", "a0", "", "{}", OLD, OLD, "P"),
            ("Y", "X", "a0", "", "{}", OLD, OLD, "P"), ("Q", "elsewhere", "a1", "", "{}", OLD, OLD, "P")]
    taken = {"X"}
    out = ws_backup._fresh_ids(rows, taken)
    ids = [r[0] for r in out]
    assert ids[0] == "P" and ids[1] != "X" and ids[2] == "Y" and len(out) == 3
    assert out[2][1] == ids[1]  # Y follows its renamed parent
    assert all(len(r) == 8 and r[7] == "P" for r in out)  # STORED_COLUMNS, every row in the copy's page
    assert set(ids) <= taken
