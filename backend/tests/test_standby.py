"""The standby a library written by a newer Gamma gets (gamma/standby.py):
the way forward by how the install updates (version.channel), a sign-in
that writes nothing, and an admin's copies, a workspace's or every
personal one's as Settings' Export all, which an up-to-date Gamma
restores."""

import io
import json
import sqlite3
import time
import zipfile
from contextlib import closing

import bcrypt
import pytest
from fastapi.testclient import TestClient

import gamma.app as app_mod
from conftest import doc_props, make_page
from gamma import config, migrations, version, ws_backup
from gamma.db import SCHEMA_VERSION, close_connections, connect_users_db
from gamma.seed import insert_account
from gamma.workspaces import create, ensure_personal

PDF = b"%PDF-1.4 a paper only the library holds"


def _stamp(path, to):
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute(f"PRAGMA user_version = {to}")
        conn.commit()


def _dump(path):
    with closing(sqlite3.connect(str(path))) as conn:
        return list(conn.iterdump())


def _signed_in(c, username, password):
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def library(data_dir, monkeypatch):
    """A library a newer Gamma wrote: alice (an admin, as a device's own
    account is) with a page and its PDF, a second personal workspace and a
    shared one; bob with a page of his; then users.db stamped one version
    above this build. ``bob_session`` is the app's session cookie bob had
    before."""
    monkeypatch.setattr(version, "UPDATES", "desktop")
    with TestClient(app_mod.create_app()) as c:
        with connect_users_db() as conn:
            alice = insert_account(conn, "alice", bcrypt.hashpw(b"alicepw1", bcrypt.gensalt()).decode(),
                                   is_admin=True)
            bob = insert_account(conn, "bob", bcrypt.hashpw(b"bobpw1", bcrypt.gensalt()).decode())
            conn.commit()
        spaces = {"alice": ensure_personal(alice), "bob": ensure_personal(bob),
                  "side": create("Side project", alice)["id"], "lab": create("Lab", alice, kind="shared")["id"]}
        _signed_in(c, "alice", "alicepw1")
        doc = c.post("/api/uploads", files={"file": ("paper.pdf", PDF, "application/pdf")}).json()["doc_id"]
        make_page(c, "Read before the newer Gamma", properties=doc_props(doc))
        bob_client = _signed_in(TestClient(c.app), "bob", "bobpw1")
        make_page(bob_client, "Bob's notes")
    close_connections()
    _stamp(config.USERS_DB, SCHEMA_VERSION + 1)
    return {"spaces": spaces, "bob_session": bob_client.cookies.get("session")}


def _standby():
    return TestClient(app_mod.create_app())


CHANNELS = [  # version.UPDATES, the system, a stamped version, the channel
    ("app", "iPadOS", "", "ipad"), ("app", "Android", "", "android"), ("desktop", "Windows", "1.0", "desktop"),
    ("store", "Windows", "1.0", "store"), ("mas", "Darwin", "1.0", "mas"), ("", "Linux", "1.0", "docker"),
    ("", "Linux", "", "checkout"),
]


@pytest.mark.parametrize("updates,system,stamped,channel", CHANNELS)
def test_the_guidance_says_how_the_install_updates(library, monkeypatch, updates, system, stamped, channel):
    """The page (auth/StandbyPage.jsx) words the update from the install's
    channel, as Settings → Server does; the log, the CLI and scripts read
    the guidance's one step. Only a Docker server gets the Docker commands,
    folded into the details with the versions."""
    monkeypatch.setattr(version, "UPDATES", updates)
    monkeypatch.setattr(version, "VERSION", stamped)
    monkeypatch.setattr(version, "system", lambda: system)
    before = _dump(config.USERS_DB)
    with _standby() as c:
        # the app's first read, which the page takes over from; and the shells' health rule
        for path in ("/api/server-config", "/api/health"):
            guide = c.get(path)
            assert guide.status_code == 503 and guide.json()["error"] == "data_directory_not_upgradable"
        guide = guide.json()
        assert guide["kind"] == "newer" and guide["install"]["channel"] == channel
        assert guide["steps"] == [migrations.UPDATE_STEPS[channel]]
        assert f"schema version {SCHEMA_VERSION + 1}" in guide["details"][0]
        assert any("--restore" in line for line in guide["details"]) == (channel in ("docker", "checkout"))
        assert c.post("/api/blocks", json={}).status_code == 503
        assert c.get("/api/session").json()["user"] is None
        assert c.get("/api/standby").json() == {"user": None, "admin": False, "workspaces": [], "export": None}
    assert _dump(config.USERS_DB) == before


def test_the_page_is_the_apps_own(library, monkeypatch, tmp_path):
    """With a frontend build the standby serves the app's shell, whose page
    shows the guidance; without one (a checkout run by hand) the guidance
    in a plain page. Either way with the app's baseline headers."""
    build = tmp_path / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html><div id=root></div>")
    (build / "assets" / "app-1.js").write_text("console.log(1)")
    monkeypatch.setattr(config, "STATIC_DIR", str(build))
    with _standby() as c:
        page = c.get("/library/anything")
        assert page.status_code == 200 and 'id=root' in page.text
        assert page.headers["x-frame-options"] == "SAMEORIGIN"
        assert c.get("/assets/app-1.js").headers["cache-control"].startswith("public, max-age=")
        assert c.get("/api/server-config").status_code == 503
    monkeypatch.setattr(config, "STATIC_DIR", "")
    with _standby() as c:
        page = c.get("/")
        assert page.status_code == 503 and "Gamma needs an update to open your library" in page.text
        assert page.headers["content-security-policy"] == "frame-ancestors 'self'"


def _copy(c, **body):
    """An export made as the page makes it: started, followed at
    GET /api/standby until it is done, then downloaded."""
    started = c.post("/api/standby/export", json=body)
    assert started.status_code == 200, started.text
    assert started.json()["kind"] == "workspace-export" and started.json()["state"] == "running"
    for _ in range(400):
        made = c.get("/api/standby").json()["export"]
        if made["state"] != "running":
            break
        time.sleep(0.05)
    assert made["state"] == "done", made
    r = c.get("/api/standby/export/file")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert made["artifact"]["name"] in r.headers["content-disposition"]
    return made, r.content


def test_an_admins_copies_restore_in_a_current_gamma(library):
    spaces = library["spaces"]
    before = _dump(config.USERS_DB)
    with _standby() as c:
        assert c.post("/api/login", json={"username": "alice", "password": "nope"}).status_code == 401
        _signed_in(c, "alice", "alicepw1")  # as the shells sign in
        assert c.get("/api/session").json()["user"] == "alice"
        listing = c.get("/api/standby").json()
        assert listing["user"] == "alice" and listing["admin"] is True
        # alice's own, as Settings → Workspaces lists them: personal first, then shared; not bob's
        assert [(w["id"], w["personal"]) for w in listing["workspaces"]] == [
            (spaces["alice"], True), (spaces["side"], True), (spaces["lab"], False)]
        made, one = _copy(c, ws=spaces["alice"], uploads=True)
        assert made["artifact"]["name"].startswith("gamma-export-alice-")
        made, every = _copy(c, all=True, uploads=False)  # Export all: every personal workspace, databases only
        assert made["params"] == {**made["params"], "all": True, "uploads": False, "count": 2}
        assert made["artifact"]["name"].startswith("gamma-export-all-db-alice-")
    assert _dump(config.USERS_DB) == before  # signing in and copying wrote nothing
    zf = zipfile.ZipFile(io.BytesIO(one))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == ws_backup.FORMAT and manifest["workspace"] == spaces["alice"]
    assert manifest["user"] == manifest["exported_by"] == "alice" and manifest["upload_files"] == 1
    assert PDF in [zf.read(n) for n in zf.namelist() if n.startswith("uploads/")]
    bundle = zipfile.ZipFile(io.BytesIO(every))
    assert sorted(bundle.namelist()) == sorted([f"alice-{spaces['alice']}.zip", f"Side-project-{spaces['side']}.zip"])
    # an up-to-date Gamma (this one, the library stamped back) restores them from Settings
    _stamp(config.USERS_DB, SCHEMA_VERSION)
    with TestClient(app_mod.create_app()) as c:
        _signed_in(c, "alice", "alicepw1")
        assert c.post("/api/blocks", json={"parent_id": "root", "content": "Written since"}).status_code == 200
        r = c.post("/api/import-data?mode=replace", files={"file": ("copy.zip", one, "application/zip")})
        assert r.status_code == 200, r.text
        titles = {b["content"] for b in c.get("/api/blocks/root/children").json()["children"]}
        assert "Read before the newer Gamma" in titles and "Written since" not in titles
        inner = bundle.read(f"alice-{spaces['alice']}.zip")
        r = c.post("/api/import-data?mode=replace", files={"file": ("inner.zip", inner, "application/zip")})
        assert r.status_code == 200, r.text


def test_a_member_is_known_but_saves_nothing(library):
    """Bob, no admin, by a sign-in here and by the session cookie he had:
    the newer files may hold what this build does not know is private."""
    with _standby() as c:
        c.cookies.set("session", library["bob_session"])
        assert c.get("/api/session").json()["user"] == "bob"
        assert c.get("/api/standby").json() == {"user": "bob", "admin": False, "workspaces": [], "export": None}
        assert c.post("/api/standby/export", json={"ws": library["spaces"]["bob"]}).status_code == 403
        assert c.get("/api/standby/export/file").status_code == 404
    with _standby() as c:
        _signed_in(c, "bob", "bobpw1")
        assert c.post("/api/standby/export", json={"all": True}).status_code == 403
        _signed_in(c, "alice", "alicepw1")  # a sign-in here is who is asking, whatever else the browser holds
        assert c.get("/api/session").json()["user"] == "alice"
        assert c.post("/api/standby/export", json={"ws": library["spaces"]["bob"]}).status_code == 404  # not hers
