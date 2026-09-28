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
from gamma.blocks_store import page_root_id, trashed_ids
from gamma.db import connect_pages_db, ws_uploads_dir
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
    with closing(sqlite3.connect(str(db))) as conn:  # before page_ops / deleted_pages existed
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
        log = ops.ops_since(conn, "oldPage", 0)[0]
        assert [b["ops"] for b in log] == [[{"op": "reload"}]]


# --- open pages reload, seqs only move forward, the feed sees it -------------------

def test_replace_moves_every_page_forward_tombstones_the_removed_and_reloads_open_tabs():
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
        assert conn.execute("SELECT 1 FROM deleted_pages WHERE page_id = ?", (later,)).fetchone()
        assert not conn.execute("SELECT 1 FROM deleted_pages WHERE page_id = ?", (pid,)).fetchone()
        feed = changes(conn, cursor, 500)
    assert pid in {p["id"] for p in feed["pages"]} and later in {d["id"] for d in feed["deleted"]}
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
        ops.delete_page(ws, conn, pid, actor="rc_mergefeed")  # for good: log dropped, tombstone left
        cursor = changes(conn, "", 500)["cursor"]
    r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=merge")
    assert r.status_code == 200 and r.json()["pages_added"] == 1, r.text
    with _conn(ws) as conn:
        assert not conn.execute("SELECT 1 FROM deleted_pages WHERE page_id = ?", (pid,)).fetchone()
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
        assert pid in trashed_ids(conn)
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
        assert not trashed_ids(conn) & {pid, "rcT1", "rcT2"}
        assert not conn.execute("SELECT 1 FROM deleted_pages WHERE page_id = ?", (pid,)).fetchone()
        assert ops.latest_seq(conn, pid) > seen  # its log kept and moved forward
    assert c.get(f"/api/chats/{pid}").json()["messages"][0]["content"] == "keep me"


def test_fresh_ids_never_keep_a_row_outside_the_copy():
    rows = [("P", "root", "a0", "", "{}", OLD, OLD), ("X", "P", "a0", "", "{}", OLD, OLD),
            ("Y", "X", "a0", "", "{}", OLD, OLD), ("Q", "elsewhere", "a1", "", "{}", OLD, OLD)]
    taken = {"X"}
    out = ws_backup._fresh_ids(rows, taken)
    ids = [r[0] for r in out]
    assert ids[0] == "P" and ids[1] != "X" and ids[2] == "Y" and len(out) == 3
    assert out[2][1] == ids[1]  # Y follows its renamed parent
    assert set(ids) <= taken
