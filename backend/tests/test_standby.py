"""The standby page a library written by a newer Gamma gets (gamma/standby.py):
the way forward in the words of the device the server runs on, a sign-in
that writes nothing, and an admin's copy of each workspace, which an
up-to-date Gamma restores."""

import io
import json
import sqlite3
import sys
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
from gamma.workspaces import ensure_personal

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
    account is) with a page and its PDF, bob with a page of his, then
    users.db stamped one version above this build. ``bob_session`` is the
    app's session cookie bob had before."""
    monkeypatch.setattr(version, "UPDATES", "desktop")
    with TestClient(app_mod.create_app()) as c:
        with connect_users_db() as conn:
            alice = insert_account(conn, "alice", bcrypt.hashpw(b"alicepw1", bcrypt.gensalt()).decode(),
                                   is_admin=True)
            bob = insert_account(conn, "bob", bcrypt.hashpw(b"bobpw1", bcrypt.gensalt()).decode())
            conn.commit()
        spaces = {"alice": ensure_personal(alice), "bob": ensure_personal(bob)}
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


SURFACES = [  # version.UPDATES, on Android, the surface, words of its step
    ("app", False, "ipad", "Open TestFlight or the App Store on this iPad"),
    ("app", True, "android", "Update Gamma from Google Play"),
    ("desktop", False, "desktop", "Download and install the latest Gamma"),
    ("store", False, "store", "Update Gamma from the Microsoft Store"),
    ("mas", False, "mas", "Update Gamma from the Mac App Store"),
    ("", False, "server", "Update this Gamma server to the latest release"),
]


@pytest.mark.parametrize("updates,android,surface,words", SURFACES)
def test_the_page_says_how_to_update_where_it_runs(library, monkeypatch, updates, android, surface, words):
    monkeypatch.setattr(version, "UPDATES", updates)
    if android:
        monkeypatch.setattr(sys, "getandroidapilevel", lambda: 34, raising=False)
    before = _dump(config.USERS_DB)
    with _standby() as c:
        page = c.get("/library/anything")
        assert page.status_code == 503 and "text/html" in page.headers["content-type"]
        assert "Gamma needs an update to open your library" in page.text and words in page.text
        assert ("Get the latest version" in page.text) == (updates == "desktop" or android)
        assert "Sign in with" in page.text and "/api/standby/backup/" not in page.text
        # the schema numbers and the commands are folded away, and only a server gets the commands
        lead, folded = page.text.split("Technical details")
        assert "schema" not in lead and f"schema version {SCHEMA_VERSION + 1}" in folded
        assert ("docker compose pull" in folded) == (updates == "")
        assert page.headers["x-frame-options"] == "SAMEORIGIN"
        # the shells' contract: up, answering its guidance, but for the sign-in routes
        health = c.get("/api/health")
        assert health.status_code == 503 and health.json()["error"] == "data_directory_not_upgradable"
        assert health.json()["kind"] == "newer" and health.json()["steps"] == [migrations.UPDATE_STEPS[surface]]
        assert c.post("/api/blocks", json={}).status_code == 503
        assert c.get("/api/session").json()["user"] is None
    assert _dump(config.USERS_DB) == before


def test_an_admins_copy_restores_in_a_current_gamma(library):
    spaces = library["spaces"]
    before = _dump(config.USERS_DB)
    with _standby() as c:
        assert c.post("/api/login", json={"username": "alice", "password": "nope"}).status_code == 401
        _signed_in(c, "alice", "alicepw1")  # as the shells sign in
        assert c.get("/api/session").json()["user"] == "alice"
        page = c.get("/").text
        assert f"/api/standby/backup/{spaces['alice']}" in page and f"/api/standby/backup/{spaces['bob']}" in page
        r = c.get(f"/api/standby/backup/{spaces['alice']}")
        assert r.status_code == 200 and "gamma-export-alice-" in r.headers["content-disposition"]
        copy = r.content
        bobs = zipfile.ZipFile(io.BytesIO(c.get(f"/api/standby/backup/{spaces['bob']}").content))
        assert json.loads(bobs.read("manifest.json"))["user"] == "bob"  # whose it is, not who saved it
    assert _dump(config.USERS_DB) == before  # signing in and copying wrote nothing
    zf = zipfile.ZipFile(io.BytesIO(copy))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == ws_backup.FORMAT and manifest["workspace"] == spaces["alice"]
    assert manifest["user"] == manifest["exported_by"] == "alice" and manifest["upload_files"] == 1
    assert PDF in [zf.read(n) for n in zf.namelist() if n.startswith("uploads/")]
    # an up-to-date Gamma (this one, the library stamped back) restores it from Settings
    _stamp(config.USERS_DB, SCHEMA_VERSION)
    with TestClient(app_mod.create_app()) as c:
        _signed_in(c, "alice", "alicepw1")
        assert c.post("/api/blocks", json={"parent_id": "root", "content": "Written since"}).status_code == 200
        r = c.post("/api/import-data?mode=replace", files={"file": ("copy.zip", copy, "application/zip")})
        assert r.status_code == 200, r.text
        titles = {b["content"] for b in c.get("/api/blocks/root/children").json()["children"]}
        assert "Read before the newer Gamma" in titles and "Written since" not in titles


def test_a_member_is_known_but_saves_nothing(library):
    """Bob, no admin, by a sign-in here and by the session cookie he had:
    the newer files may hold what this build does not know is private."""
    with _standby() as c:
        c.cookies.set("session", library["bob_session"])
        assert c.get("/api/session").json()["user"] == "bob"
        page = c.get("/").text
        assert "Saving a copy is for this server's admins" in page and "/api/standby/backup/" not in page
        assert c.get(f"/api/standby/backup/{library['spaces']['bob']}").status_code == 404
    with _standby() as c:
        _signed_in(c, "bob", "bobpw1")
        assert c.get(f"/api/standby/backup/{library['spaces']['bob']}").status_code == 404
        _signed_in(c, "alice", "alicepw1")  # a sign-in here is who is asking, whatever else the browser holds
        assert c.get("/api/session").json()["user"] == "alice"
