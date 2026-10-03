"""Per-workspace backups (/api/workspaces/{ws}/backups*, gamma/ws_backup.py):
take a snapshot, list, download, restore in place (replace / merge), delete;
who may do what; the cap; snapshots go with the workspace. Plus /export-all
(every personal workspace of an account in one zip) and /workspaces/mine."""

import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from conftest import login, make_page, make_user, workspace_of, guest_name


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
    page = make_page(owner, "Before the snapshot", properties={"source_url": up.json()["source_url"]})
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
    assert "quota_mb" in mine["account"] and "max_upload_mb" in mine["account"]
    assert owner.delete(f"/api/workspaces/{play}").status_code == 200
