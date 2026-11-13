"""Per-workspace backups (/api/workspaces/{ws}/backups*, gamma/ws_backup.py):
take a snapshot, list, download, restore in place (replace / merge), delete;
who may do what; the cap; snapshots go with the workspace; a Zotero-linked
workspace's backup restored where no link is brings none of the link's
rows. Plus /export-all (every personal workspace of an account in one zip)
and /workspaces/mine."""

import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from conftest import account_of, doc_props, login, make_page, make_user, workspace_of, guest_name
from zotero_engine import LIB, drop_link_row, link_row


@pytest.fixture(scope="module")
def owner():
    make_user("bk_owner", "bkownerpw1")
    return login("bk_owner", "bkownerpw1")


@pytest.fixture(scope="module")
def other():
    make_user("bk_other", "bkotherpw1")
    return login("bk_other", "bkotherpw1")


@pytest.fixture(scope="module")
def boss():
    make_user("bk_boss", "bkbosspw1", is_admin=1)
    return login("bk_boss", "bkbosspw1")


def _in(ws):
    return {"X-Gamma-Workspace": ws}


def test_snapshot_round_trip(owner, other):
    ws = workspace_of("bk_owner")
    up = owner.post("/api/uploads", files={"file": ("a.pdf", b"%PDF-1.4 snapshot me", "application/pdf")})
    assert up.status_code == 200
    page = make_page(owner, "Before the snapshot", properties=doc_props(up.json()["doc_id"]))
    assert owner.get(f"/api/workspaces/{ws}/backups").json() == {"backups": [], "max": 20}

    r = owner.post(f"/api/workspaces/{ws}/backups", json={"label": "before", "uploads": True})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["name"].endswith("-before") and b["uploads"] is True and b["upload_files"] == 1 and b["by"] == "bk_owner"
    small = owner.post(f"/api/workspaces/{ws}/backups", json={"label": "db", "uploads": False}).json()
    assert small["uploads"] is False and small["size_bytes"] < b["size_bytes"]
    listed = owner.get(f"/api/workspaces/{ws}/backups").json()["backups"]
    assert [x["name"] for x in listed] == [small["name"], b["name"]]  # newest first

    # the download is a plain /api/export zip
    z = owner.get(f"/api/workspaces/{ws}/backups/{b['name']}/download")
    assert z.status_code == 200 and z.headers["content-type"].startswith("application/zip")
    # the listing names the file as the download's Content-Disposition does, for a host that saves it
    assert f'filename="{listed[1]["filename"]}"' in z.headers["content-disposition"]
    assert listed[1]["filename"].startswith("gamma-backup-") and listed[1]["filename"].endswith(f"-{b['name']}.zip")
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "gamma-backup-1" and manifest["workspace"] == ws and manifest["label"] == "before"
    assert manifest["kind"] == "personal" and manifest["user"] == "bk_owner"
    assert any(n.startswith("uploads/") for n in zf.namelist())

    # change things, then restore in place: the page comes back, the new one goes
    later = make_page(owner, "After the snapshot")
    assert owner.delete(f"/api/blocks/{page['id']}").status_code == 200
    r = owner.post(f"/api/workspaces/{ws}/backups/{b['name']}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == ["pages.db", "data.db"]
    titles = {x["content"] for x in owner.get("/api/blocks/root/children").json()["children"]}
    assert "Before the snapshot" in titles and "After the snapshot" not in titles
    # merge brings a later page back without touching what is there
    later2 = make_page(owner, "Merged back in")
    merge_snap = owner.post(f"/api/workspaces/{ws}/backups", json={"label": "with-merge"}).json()
    assert owner.delete(f"/api/blocks/{later2['id']}").status_code == 200
    assert owner.delete(f"/api/trash/{later2['id']}").status_code == 200  # gone for good, not in Recently deleted
    r = owner.post(f"/api/workspaces/{ws}/backups/{merge_snap['name']}/restore", params={"mode": "merge"})
    assert r.status_code == 200 and r.json()["pages_added"] == 1
    titles = {x["content"] for x in owner.get("/api/blocks/root/children").json()["children"]}
    assert "Merged back in" in titles and "Before the snapshot" in titles
    assert later["id"] not in [x["id"] for x in owner.get("/api/blocks/root/children").json()["children"]]

    # nobody else sees or touches a personal workspace's snapshots
    assert other.get(f"/api/workspaces/{ws}/backups").status_code == 404
    assert other.post(f"/api/workspaces/{ws}/backups", json={}).status_code == 404
    assert other.get(f"/api/workspaces/{ws}/backups/{b['name']}/download").status_code == 404
    assert other.delete(f"/api/workspaces/{ws}/backups/{b['name']}").status_code == 404
    # bad names and labels
    assert owner.post(f"/api/workspaces/{ws}/backups", json={"label": "no spaces"}).status_code == 400
    assert owner.get(f"/api/workspaces/{ws}/backups/..%2F..%2Fusers.db/download").status_code == 404
    assert owner.delete(f"/api/workspaces/{ws}/backups/20260101-000000-nope").status_code == 404
    assert owner.delete(f"/api/workspaces/{ws}/backups/{b['name']}").status_code == 200
    assert owner.delete(f"/api/workspaces/{ws}/backups/{b['name']}").status_code == 404


def test_a_backup_from_a_newer_gamma_is_refused(tmp_path):
    """A zip whose pages.db is stamped above this release's SCHEMA_VERSION
    holds shapes this code cannot read: refused before anything is touched,
    with the way forward in the message."""
    from gamma import migrations, ws_backup
    with closing(sqlite3.connect(str(tmp_path / "pages.db"))) as conn:
        conn.execute("CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, properties TEXT NOT NULL DEFAULT '{}')")
        conn.execute(f"PRAGMA user_version = {migrations.SCHEMA_VERSION + 1}")
        conn.commit()
    with pytest.raises(ws_backup.BackupError) as e:
        ws_backup._normalize_copies(tmp_path)
    assert "newer Gamma" in str(e.value) and "update Gamma" in str(e.value)
    assert str(migrations.SCHEMA_VERSION + 1) in str(e.value)


def test_no_zip_carries_anyones_prefs_and_a_restore_keeps_them(owner):
    # Open tabs, recents and reading positions are each account's own: a
    # snapshot, an export and a Gamma export of the workspace hold the
    # table empty, with none of its bytes left in the file (an older, longer
    # value included); a restore, replace or merge, keeps the live rows,
    # whatever the backup holds.
    ws = workspace_of("bk_owner")
    page = make_page(owner, "Prefs stay home")
    secret = "bk-private-tab-title"
    assert owner.put("/api/prefs/read-pos", json={"value": {"long": secret * 300}}).status_code == 200
    assert owner.put("/api/prefs/read-pos", json={"value": {page["id"]: 7}}).status_code == 200  # the long one freed
    assert owner.put("/api/prefs/open-tabs", json={"value": [{"id": page["id"], "title": secret}]}).status_code == 200
    snap = owner.post(f"/api/workspaces/{ws}/backups", json={"label": "prefs", "uploads": False}).json()
    zips = {
        "snapshot": owner.get(f"/api/workspaces/{ws}/backups/{snap['name']}/download").content,
        "export": owner.get("/api/export", params={"uploads": 0}).content,
        "gamma": owner.get(f"/api/pages/{page['id']}/export", params={"mode": "gamma"}).content,
    }

    def pages_db_of(data: bytes, edit=None):
        """The zip's pages.db: (bytes, workspace_prefs rows, whether the page is in it)."""
        raw = zipfile.ZipFile(io.BytesIO(data)).read("pages.db")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pages.db"
            path.write_bytes(raw)
            with closing(sqlite3.connect(str(path))) as conn:
                if edit:
                    edit(conn)
                    conn.commit()
                rows = conn.execute("SELECT COUNT(*) FROM workspace_prefs").fetchone()[0]
                has_page = bool(conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (page["id"],)).fetchone())
            return path.read_bytes(), rows, has_page

    for kind, data in zips.items():
        raw, rows, has_page = pages_db_of(data)
        assert rows == 0 and has_page, kind
        assert secret.encode() not in raw, kind

    later = [{"id": page["id"], "title": "after the snapshot"}]
    assert owner.put("/api/prefs/open-tabs", json={"value": later}).status_code == 200
    assert owner.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore").status_code == 200
    assert owner.get("/api/prefs/open-tabs").json()["value"] == later
    assert owner.get("/api/prefs/read-pos").json()["value"] == {page["id"]: 7}
    assert owner.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore", params={"mode": "merge"}).status_code == 200
    assert owner.get("/api/prefs/open-tabs").json()["value"] == later
    # a zip that does carry rows (written elsewhere, or by hand) changes nothing either
    from conftest import account_of
    me = account_of("bk_owner")

    def planted(conn):
        conn.execute("INSERT INTO workspace_prefs VALUES (?, 'open-tabs', '[\"planted\"]', '2999-01-01T00:00:00.000000Z')",
                     (me,))

    raw, rows, _ = pages_db_of(zips["export"], planted)
    assert rows == 1
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zips["export"])) as src, zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, raw if name == "pages.db" else src.read(name))
    for mode in ("replace", "merge"):
        r = owner.post("/api/import-data", params={"mode": mode},
                       files={"file": ("planted.zip", buf.getvalue(), "application/zip")})
        assert r.status_code == 200, r.text
        assert owner.get("/api/prefs/open-tabs").json()["value"] == later, mode


def _zotero_rows(ws) -> dict:
    from gamma.db import connect_pages_db
    with closing(connect_pages_db(ws)) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("zotero_objects", "zotero_queue", "zotero_state")} | {
            "conflicts": conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE kind LIKE 'zotero%'").fetchone()[0]}


def test_a_linked_workspaces_backup_restored_where_no_link_is_brings_no_link_rows(client):
    from gamma import ws_backup
    from gamma import zotero_remote as zr
    from gamma.db import connect_pages_db, connect_users_db, page_now
    linked = make_user("bk_zotero_linked", "pw-bk_zotero_linked")
    plain = make_user("bk_zotero_plain", "pw-bk_zotero_plain")
    link_row(linked, account_of("bk_zotero_linked"))
    try:
        with closing(connect_pages_db(linked)) as conn:
            zr.set_state(conn, library=LIB, version=7)
            conn.execute("INSERT INTO zotero_objects (key, kind, state) VALUES ('ABCD2345', 'item', 'live')")
            zr.enqueue(conn, [{"id": "ABCD2346", "kind": "top"}])
            conn.execute("INSERT INTO sync_conflicts (page_id, block_id, kind, base, at) VALUES "
                         "('p', 'p', 'zotero_file', ?, ?)", (json.dumps({"doc_id": "keptHere"}), page_now()))
            conn.commit()
        made = ws_backup.create(linked, label="while-linked")
        zpath = ws_backup.backup_path(linked, made["name"])
        ws_backup.restore_zip(plain, zpath, "replace")
        assert _zotero_rows(plain) == {"zotero_objects": 0, "zotero_queue": 0, "zotero_state": 0, "conflicts": 0}
        # where the link is (detached too: a reattach goes on from them), the backup's rows come back, marked restored
        with connect_users_db() as conn:
            conn.execute("UPDATE zotero_links SET mode = 'off' WHERE workspace_id = ?", (linked,))
            conn.commit()
        with closing(connect_pages_db(linked)) as conn:
            zr.clear(conn)
            conn.commit()
        ws_backup.restore_zip(linked, zpath, "replace")
        assert _zotero_rows(linked) == {"zotero_objects": 1, "zotero_queue": 1, "zotero_state": 1, "conflicts": 1}
        with closing(connect_pages_db(linked)) as conn:
            state = zr.get_state(conn)
        assert state["library"] == LIB and state["version"] == 7 and state["restored_at"]
    finally:
        drop_link_row(linked)


def test_cap_and_guest(owner, guest, monkeypatch):
    from gamma import ws_backup
    ws = workspace_of("bk_owner")
    monkeypatch.setattr(ws_backup, "MAX_PER_WORKSPACE", 3)
    # the cap counts manual snapshots only (not a restore's automatic "pre-restore" one)
    have = sum(not b["scheduled"] and not b["auto"] for b in owner.get(f"/api/workspaces/{ws}/backups").json()["backups"])
    for i in range(3 - have):
        assert owner.post(f"/api/workspaces/{ws}/backups", json={"label": f"n{i}", "uploads": False}).status_code == 200
    r = owner.post(f"/api/workspaces/{ws}/backups", json={"label": "one-too-many"})
    assert r.status_code == 400 and "delete one first" in r.json()["detail"]
    gws = workspace_of(guest_name())
    assert guest.post(f"/api/workspaces/{gws}/backups", json={}).status_code == 403
    assert guest.get(f"/api/workspaces/{gws}/backups").json()["backups"] == []


def test_shared_workspace_roles_and_cleanup(boss, owner, other):
    from gamma import ws_backup
    r = boss.post("/api/workspaces", json={"name": "Snapshot lab", "kind": "shared", "owner": "bk_owner"})
    lab = r.json()["id"]
    assert owner.put(f"/api/workspaces/{lab}/members/bk_other", json={"role": "editor"}).status_code == 200
    make_page(owner, "Lab page", headers=_in(lab)) if False else owner.post(
        "/api/blocks", json={"parent_id": "root", "content": "Lab page"}, headers=_in(lab))
    # an editor lists and downloads, may merge, but neither takes nor replaces nor deletes
    assert other.post(f"/api/workspaces/{lab}/backups", json={}).status_code == 403
    snap = owner.post(f"/api/workspaces/{lab}/backups", json={"label": "lab"}).json()
    assert other.get(f"/api/workspaces/{lab}/backups").json()["backups"][0]["name"] == snap["name"]
    assert other.get(f"/api/workspaces/{lab}/backups/{snap['name']}/download").status_code == 200
    assert other.post(f"/api/workspaces/{lab}/backups/{snap['name']}/restore").status_code == 403
    assert other.post(f"/api/workspaces/{lab}/backups/{snap['name']}/restore", params={"mode": "merge"}).status_code == 200
    assert other.delete(f"/api/workspaces/{lab}/backups/{snap['name']}").status_code == 403
    # admins pass without membership
    assert boss.get(f"/api/workspaces/{lab}/backups").status_code == 200
    assert boss.post(f"/api/workspaces/{lab}/backups", json={"label": "admin"}).status_code == 200
    # the snapshots go with the workspace
    store = ws_backup.store_dir(lab)
    assert store.is_dir() and len(list(store.glob("*.zip"))) == 2
    assert owner.delete(f"/api/workspaces/{lab}").status_code == 200
    assert not store.exists()


def test_export_all_and_mine(owner, other, guest):
    ws = workspace_of("bk_owner")
    play = owner.post("/api/workspaces", json={"name": "Play"}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": "root", "content": "Play page"}, headers=_in(play))
    r = owner.get("/api/export-all", params={"uploads": 0})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
    bundle = zipfile.ZipFile(io.BytesIO(r.content))
    names = bundle.namelist()
    assert len(names) == 2 and any(n.endswith(f"-{ws}.zip") for n in names) and any(n.endswith(f"-{play}.zip") for n in names)
    inner = zipfile.ZipFile(io.BytesIO(bundle.read(next(n for n in names if n.endswith(f"-{play}.zip")))))
    assert json.loads(inner.read("manifest.json"))["workspace"] == play and "pages.db" in inner.namelist()
    assert not any(n.startswith("uploads/") for n in inner.namelist())
    assert guest.get("/api/export-all").status_code == 403
    # /mine: my workspaces with sizes, and my account's storage
    mine = owner.get("/api/workspaces/mine").json()
    ids = {w["id"]: w for w in mine["workspaces"]}
    assert ids[ws]["used_bytes"] > 0 and ids[play]["used_bytes"] == 0 and ids[ws]["default"] is True
    assert mine["account"]["used_bytes"] == ids[ws]["used_bytes"] + ids[play]["used_bytes"]
    assert "quota_mb" in mine["account"] and "max_upload_mb" not in mine["account"]
    assert owner.delete(f"/api/workspaces/{play}").status_code == 200


def test_an_upload_dated_before_1980_still_snapshots(owner):
    # A zip cannot say 1970 (a copy that kept a zeroed clock): such an upload
    # goes into the workspace's zip dated 1980 rather than failing it.
    import os
    from gamma import storage
    ws = workspace_of("bk_owner")
    up = owner.post("/api/uploads", files={"file": ("old.pdf", b"%PDF-1.4 an old clock", "application/pdf")})
    assert up.status_code == 200
    os.utime(storage.open_path(ws, f"{up.json()['doc_id']}.pdf"), (0, 0))
    r = owner.post(f"/api/workspaces/{ws}/backups", json={"label": "old.clock", "uploads": True})
    assert r.status_code == 200, r.text
    z = owner.get(f"/api/workspaces/{ws}/backups/{r.json()['name']}/download")
    entry = zipfile.ZipFile(io.BytesIO(z.content)).getinfo(f"uploads/{up.json()['doc_id']}.pdf")
    assert entry.date_time[0] == 1980
    assert owner.delete(f"/api/workspaces/{ws}/backups/{r.json()['name']}").status_code == 200


def test_a_merge_brings_no_work_folder_and_matches_library_documents_only():
    """A backup merged into another workspace brings its work folder's files
    as library pages, their stamps dropped, and its folders as plain ones,
    for the vault that marked them is the backup's computer's; and a page
    of either side matches one of the same PDF only when both are library
    documents: the backup's file not in the library matches no page of the
    workspace, and the workspace's file no page of the backup. The review
    says what the merge does."""
    from gamma import ws_backup
    from gamma.auth import DISK_CLIENT
    from gamma.blocks_store import create_page
    from gamma.db import connect_pages_db
    from gamma.documents.model import document
    from gamma.work_folders.library import STAMPS
    from conftest import make_folder, mark_work_folder, work_props

    def library(name):
        make_user(name, f"pw-{name}")
        c = login(name, f"pw-{name}")
        return c, workspace_of(name), account_of(name)

    def file_page(ws, account, title, props):
        with closing(connect_pages_db(ws)) as conn:
            return create_page(conn, title, props, actor=account, client=DISK_CLIENT)["id"]

    shared, mine = "1a" * 16, "2b" * 16  # a file's PDF on one side, a library page's on the other
    src, src_ws, src_account = library("bk_wf_from")
    w, d, lib = make_folder(src, "bk-W"), make_folder(src, "bk-W/dir"), make_folder(src, "bk-L")
    mark_work_folder(src, w)
    figure = file_page(src_ws, src_account, "figure", work_props(w, "figure.pdf", directory=d, doc=document(
        shared, name="figure.pdf", at="path")))
    paper = file_page(src_ws, src_account, "paper", {**work_props(w, "main.pdf", library=True, doc=document(
        "3c" * 16, name="main.pdf", at="path")), "folders": [w, lib]})
    page = make_page(src, "a library paper", properties=doc_props(mine))["id"]

    dst, dst_ws, dst_account = library("bk_wf_to")
    held = make_page(dst, "the workspace's paper", properties=doc_props(shared))["id"]
    own_w = make_folder(dst, "bk-W")  # the same name as the backup's work folder
    mark_work_folder(dst, own_w)
    own_file = file_page(dst_ws, dst_account, "own figure", work_props(own_w, "fig.pdf", doc=document(
        mine, name="fig.pdf", at="path")))

    zpath = ws_backup.backup_path(src_ws, ws_backup.create(src_ws, label="work")["name"])
    planned = {p["id"]: p["action"] for p in ws_backup.preview_zip(dst_ws, zpath)["pages"]}
    assert (planned[figure], planned[paper], planned[page]) == ("create", "create", "create")
    merged = ws_backup.restore_zip(dst_ws, zpath, "merge")
    assert {figure, paper, page} <= set(merged["added"])
    with closing(connect_pages_db(dst_ws)) as conn:
        def props_of(block_id):
            return json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                           (block_id,)).fetchone()[0])
        assert not any(set(STAMPS) & props_of(p).keys() for p in (figure, paper))
        assert props_of(figure)["folders"] == [d] and props_of(paper)["folders"] == [w, lib]
        assert "vault" not in props_of(w) and props_of(own_w)["vault"] == {"pull": True, "library": False}
        assert props_of(own_file)["work_folder"] == own_w and props_of(held)["doc"]["hash"] == shared
    listed = {p["id"] for p in dst.get("/api/blocks/root/children").json()["children"]}
    assert {figure, paper, page, held} <= listed and own_file not in listed


def test_a_merge_matches_a_library_document_of_any_format_by_its_stored_file():
    """The review's ``kind`` is the document's format; a backup's figure
    matches the workspace's page of the same stored file (its hash and
    extension) and is skipped for it, while the same bytes stored under
    another extension are another file and match no page."""
    from gamma import ws_backup
    from gamma.documents.model import document

    def library(name):
        make_user(name, f"pw-{name}")
        c = login(name, f"pw-{name}")
        return c, workspace_of(name)

    png, text = "4d" * 16, "5e" * 16
    src, src_ws = library("bk_fmt_from")
    figure = make_page(src, "a figure", properties={"doc": document(png, name="fig.png", format="image", ext=".png")})["id"]
    notes = make_page(src, "a text", properties={"doc": document(text, name="notes.txt", format="text", ext=".txt")})["id"]
    paper = make_page(src, "a paper", properties=doc_props("6f" * 16))["id"]
    dst, dst_ws = library("bk_fmt_to")
    held = make_page(dst, "the workspace's figure", properties={
        "doc": document(png, name="figure.png", format="image", ext=".png")})["id"]
    make_page(dst, "the same bytes as a log", properties={"doc": document(text, name="notes.log", format="text", ext=".log")})
    zpath = ws_backup.backup_path(src_ws, ws_backup.create(src_ws, label="formats")["name"])
    review = {p["id"]: p for p in ws_backup.preview_zip(dst_ws, zpath)["pages"]}
    assert figure not in review  # listed as the workspace's page it is
    assert (review[held]["action"], review[notes]["action"], review[paper]["action"]) == ("skip", "create", "create")
    assert (review[held]["kind"], review[notes]["kind"], review[paper]["kind"]) == ("image", "text", "pdf")
    merged = ws_backup.restore_zip(dst_ws, zpath, "merge")
    assert figure not in merged["added"] and {notes, paper} <= set(merged["added"])
    listed = {p["id"] for p in dst.get("/api/blocks/root/children").json()["children"]}
    assert {held, notes, paper} <= listed and figure not in listed


def test_dropping_the_snapshots_waits_for_one_being_written(owner):
    # ws_backup.remove_all takes the workspace's snapshot lock: a snapshot
    # half written when the workspace goes finishes first, then its folder
    # goes with the rest, rather than a zip landing in a folder already gone.
    import threading
    from gamma import ws_backup
    play = owner.post("/api/workspaces", json={"name": "Snapshot while deleted"}).json()["id"]
    owner.post("/api/blocks", json={"parent_id": "root", "content": "A page"}, headers=_in(play))
    writing, finish = threading.Event(), threading.Event()

    def progress(**_):
        writing.set()
        assert finish.wait(10)

    made = []
    snap = threading.Thread(target=lambda: made.append(ws_backup.create(play, uploads=False, progress=progress)))
    snap.start()
    drop = threading.Thread(target=ws_backup.remove_all, args=(play,))
    try:
        assert writing.wait(10)
        drop.start()
        drop.join(0.3)
        assert drop.is_alive() and ws_backup.store_dir(play).is_dir()  # waits for the snapshot
    finally:
        finish.set()
        snap.join(10)
    drop.join(10)
    assert made and made[0]["size_bytes"] > 0  # the snapshot answered before its folder went
    assert not drop.is_alive() and not ws_backup.store_dir(play).exists()
    assert owner.delete(f"/api/workspaces/{play}").status_code == 200
