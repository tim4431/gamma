"""Admin user-management API: privilege gating, CRUD, and lockout rails."""

import pytest
from fastapi.testclient import TestClient

from conftest import workspace_of, login as _login, make_user as _make_user


@pytest.fixture(scope="module")
def boss(client):
    """A TestClient logged in as an admin-privileged user."""
    _make_user("boss", "bosspw", is_admin=1)
    return _login("boss", "bosspw")


def test_admin_is_a_privilege_not_a_name(boss):
    s = boss.get("/api/session").json()
    assert s["user"] == "boss" and s["is_admin"] is True


def test_startup_seeds_first_admin_once(boss):
    """The empty test instance seeded an 'admin' account with a RANDOM
    password at app startup; once any account exists the seed is a strict
    no-op (no backdoor on upgrades)."""
    from gamma.seed import ensure_admin_seed

    users = {u["username"]: u for u in boss.get("/api/admin/users").json()["users"]}
    assert users["admin"]["is_admin"] is True
    assert ensure_admin_seed() is None  # accounts exist → never seeds again


def test_seed_password_is_random_and_works(tmp_path):
    """On a genuinely fresh data dir, the seed's returned/printed password
    actually logs in. (Subprocess: config reads GAMMA_DATA_DIR at import,
    so a fresh dir needs a fresh process.)"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "from gamma.seed import ensure_admin_seed\n"
        "import bcrypt\n"
        "from gamma.db import connect_users_db\n"
        "user, pw = ensure_admin_seed()\n"
        "with connect_users_db() as c:\n"
        "    h = c.execute(\"SELECT password_hash FROM users WHERE username = ?\", (user,)).fetchone()[0]\n"
        "print('PW:' + pw)\n"
        "print('MATCH' if bcrypt.checkpw(pw.encode(), h.encode()) else 'MISMATCH')\n"
    )
    env = {**os.environ, "GAMMA_DATA_DIR": str(tmp_path)}
    env.pop("GAMMA_ADMIN_USER", None)
    env.pop("GAMMA_ADMIN_PASSWORD", None)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, cwd=str(Path(__file__).resolve().parent.parent)).stdout
    assert "MATCH" in out, out
    pw = next(l for l in out.splitlines() if l.startswith("PW:"))[3:]
    assert len(pw) >= 12
    # ...and the console output actually shows it (that's the only place it exists)
    assert f"password: {pw}" in out


def test_non_admins_are_locked_out(client):
    _make_user("pleb", "plebpw")
    pleb = _login("pleb", "plebpw")
    assert pleb.get("/api/session").json()["is_admin"] is False
    assert pleb.get("/api/admin/users").status_code == 403
    assert pleb.post("/api/admin/users", json={"username": "x", "password": "y"}).status_code == 403
    # A guest session of its own (not the shared `guest` fixture — that would
    # log the session-scoped client in before test_auth asserts it is anonymous)
    from gamma.app import app
    g = TestClient(app)
    assert g.post("/api/login-guest").status_code == 200
    assert g.get("/api/admin/users").status_code == 403
    assert TestClient(app).get("/api/admin/users").status_code == 401


def test_create_list_and_login(boss):
    r = boss.post("/api/admin/users", json={"username": "newbie", "password": "npw"})
    assert r.status_code == 200, r.text
    users = {u["username"]: u for u in r.json()["users"]}
    assert "newbie" in users and users["newbie"]["is_admin"] is False
    _login("newbie", "npw")  # account actually works
    # duplicate + bad names rejected
    assert boss.post("/api/admin/users", json={"username": "newbie", "password": "x"}).status_code == 409
    assert boss.post("/api/admin/users", json={"username": "bad name", "password": "x"}).status_code == 400
    assert boss.post("/api/admin/users", json={"username": "nopw", "password": ""}).status_code == 400


def test_set_password(boss):
    r = boss.put("/api/admin/users/newbie", json={"password": "rotated"})
    assert r.status_code == 200
    from gamma.app import app
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "newbie", "password": "npw"}).status_code == 401
    _login("newbie", "rotated")


def test_grant_and_revoke_admin(boss):
    r = boss.put("/api/admin/users/newbie", json={"is_admin": True})
    assert {u["username"]: u["is_admin"] for u in r.json()["users"]}["newbie"] is True
    # the new admin can use the API too
    newbie = _login("newbie", "rotated")
    assert newbie.get("/api/admin/users").status_code == 200
    r = boss.put("/api/admin/users/newbie", json={"is_admin": False})
    assert {u["username"]: u["is_admin"] for u in r.json()["users"]}["newbie"] is False


def test_lockout_rails(boss):
    # the startup-seeded 'admin' holds the privilege too, and so may admins
    # made by other test files sharing this worker's data dir — demote every
    # one of them so boss is the last admin, then the rails must hold
    users = boss.get("/api/admin/users").json()["users"]
    for u in users:
        if u["is_admin"] and u["username"] != "boss":
            assert boss.put(f"/api/admin/users/{u['username']}", json={"is_admin": False}).status_code == 200
    assert boss.put("/api/admin/users/boss", json={"is_admin": False}).status_code == 400
    assert boss.delete("/api/admin/users/boss").status_code == 400  # also self-delete
    # a guest account takes no password, but an admin may delete it (its
    # workspace goes with it)
    from gamma import guests, workspaces
    from gamma.db import ws_dir
    user_id, name = guests.new_guest()
    ws = workspaces.default_workspace(user_id)
    assert boss.put(f"/api/admin/users/{name}", json={"password": "x"}).status_code == 400
    r = boss.delete(f"/api/admin/users/{name}")
    assert r.status_code == 200 and r.json()["deleted_workspaces"] == [ws]
    assert name not in [u["username"] for u in r.json()["users"]] and not ws_dir(ws).exists()
    assert boss.delete("/api/admin/users/ghost-user").status_code == 404


def test_rename_user_via_gui(boss):
    from gamma.app import app
    from gamma.db import ws_dir

    boss.post("/api/admin/users", json={"username": "rene", "password": "rpw"})
    ws = workspace_of("rene")
    assert (ws_dir(ws) / "pages.db").exists()
    r = boss.post("/api/admin/users/rene/rename", json={"new_username": "renata"})
    assert r.status_code == 200, r.text
    names = [u["username"] for u in r.json()["users"]]
    assert "renata" in names and "rene" not in names
    # Workspace directories are named by id: nothing moves, the rows follow.
    assert workspace_of("renata") == ws and (ws_dir(ws) / "pages.db").exists()
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "rene", "password": "rpw"}).status_code == 401
    _login("renata", "rpw")
    # collisions / guest / bad names / ghosts rejected
    assert boss.post("/api/admin/users/renata/rename", json={"new_username": "boss"}).status_code == 409
    from gamma import guests
    _, name = guests.new_guest()
    assert boss.post(f"/api/admin/users/{name}/rename", json={"new_username": "g2"}).status_code == 400
    assert boss.delete(f"/api/admin/users/{name}").status_code == 200
    assert boss.post("/api/admin/users/renata/rename", json={"new_username": "bad name"}).status_code == 400
    assert boss.post("/api/admin/users/ghost/rename", json={"new_username": "x"}).status_code == 404


def _table_rows(conn) -> dict:
    """Every table of a database, its rows as a sorted list (to compare)."""
    tables = [t for t, in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                         "AND name NOT LIKE 'sqlite_%'")]
    return {t: sorted(map(repr, conn.execute(f"SELECT * FROM {t}").fetchall())) for t in tables}


def test_a_rename_rewrites_no_row_but_the_name(boss):
    """Every row and file names an account by its id (docs/dev/user_db.md
    "Accounts are named by id"), so a rename is the users row's username and
    the personal workspace named after the account — no other row of
    users.db or of the workspace's pages.db changes — and the account's
    session, prefs, op log, trashed page, integration token, membership,
    share invitation, mirror, job and publisher session all keep working."""
    import json

    from conftest import account_of, make_page
    from gamma import jobs, publisher_sessions, sync_engine, workspaces
    from gamma.app import app
    from gamma.db import connect_pages_db, connect_users_db
    from gamma.integrations import create_token

    _make_user("ren_carol", "rcpw")
    carol = _login("ren_carol", "rcpw")
    user_id, ws = account_of("ren_carol"), workspace_of("ren_carol")
    # What an account leaves behind, in every place that names it.
    carol.put("/api/prefs/profile", json={"value": {"theme": "sepia"}}).raise_for_status()
    page = make_page(carol, "Carol's notes")
    carol.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "ren-note", "parent": page["id"], "content": "a note"}]}).raise_for_status()
    draft = make_page(carol, "Carol's draft")
    carol.delete(f"/api/blocks/{draft['id']}").raise_for_status()
    token = create_token(user_id, ws, "script", 1)["token"]
    lab = boss.post("/api/workspaces", json={"name": "Rename lab", "kind": "shared"}).json()["id"]
    boss.put(f"/api/workspaces/{lab}/members/ren_carol", json={"role": "editor"}).raise_for_status()
    shared = make_page(boss, "For Carol")
    share = boss.post(f"/api/share/{shared['id']}").json()["token"]
    boss.put(f"/api/share-settings/{shared['id']}", json={
        "audience": "list", "users": [{"name": "ren_carol", "role": "edit"}]}).raise_for_status()
    whoami = json.dumps({"user": "carol", "workspace": {"id": "remote-ws", "name": "Remote lab"},
                         "role": "editor", "scope": "write"}).encode()
    mirror = sync_engine.create_mirror(user_id, "https://remote.example", "gamma_rename",
                                       fetch=lambda *request: (200, whoami))
    job = jobs.start("export", owner=user_id, title="Carol's export", run=lambda job: {"ok": True})
    assert jobs.wait(job["id"])["state"] == "done"
    host = "journals.aps.org"
    carol.post("/api/publisher-sessions", headers={"x-forwarded-proto": "https"}, json={
        "host": host, "cookies": [{"name": "access", "value": "v", "domain": host, "hostOnly": True,
                                   "path": "/"}]}).raise_for_status()
    assert workspaces.get(ws)["name"] == "ren_carol"
    with connect_users_db() as conn:
        users_before = _table_rows(conn)
    with connect_pages_db(ws) as conn:
        pages_before = _table_rows(conn)

    r = boss.post("/api/admin/users/ren_carol/rename", json={"new_username": "ren_caroline"})
    assert r.status_code == 200, r.text

    with connect_users_db() as conn:
        users_after = _table_rows(conn)
        assert conn.execute("SELECT id FROM users WHERE username = 'ren_caroline'").fetchone() == (user_id,)
    with connect_pages_db(ws) as conn:
        assert _table_rows(conn) == pages_before
    # One row changed in each of two tables: the name, and the personal
    # workspace's name. The mirror's workspace keeps its own.
    assert {t for t in users_before if users_before[t] != users_after[t]} == {"users", "workspaces"}
    for table in ("users", "workspaces"):
        assert len(set(users_before[table]) ^ set(users_after[table])) == 2, table
    assert workspaces.get(ws)["name"] == "ren_caroline"
    assert workspaces.get(mirror["workspace_id"])["name"] == "Remote lab (offline copy)"

    # The session: no new login, and the new name everywhere it is shown.
    assert carol.get("/api/session").json()["user"] == "ren_caroline"
    assert carol.get("/api/prefs/profile").json()["value"] == {"theme": "sepia"}
    batches = carol.get(f"/api/pages/{page['id']}/ops", params={"since": 0}).json()["batches"]
    assert [b["actor"] for b in batches] == [user_id]
    assert {p["id"]: p["deleted_by"] for p in carol.get("/api/trash").json()["pages"]} == {draft["id"]: "ren_caroline"}
    bearer = TestClient(app)
    bearer.headers["Authorization"] = f"Bearer {token}"
    assert page["id"] in {b["id"] for b in bearer.get("/api/blocks/root/children").json()["children"]}
    assert ("ren_caroline", "editor") in {(m["username"], m["role"]) for m in workspaces.members(lab)}
    assert carol.get("/api/blocks/root/children", headers={"X-Gamma-Workspace": lab}).status_code == 200
    assert carol.get(f"/api/share/{share}").json()["can_edit"] is True
    assert boss.get(f"/api/share-settings/{shared['id']}").json()["users"] == [{"name": "ren_caroline", "role": "edit"}]
    assert [m["workspace_id"] for m in carol.get("/api/mirrors").json()["mirrors"]] == [mirror["workspace_id"]]
    assert job["id"] in {j["id"] for j in carol.get("/api/jobs").json()["jobs"]}
    assert [s["host"] for s in carol.get("/api/publisher-sessions").json()["sessions"]] == [host]
    bound = publisher_sessions.current_user.set(user_id)
    try:  # the snapshot is sealed with the id: it still opens
        assert [c.name for c in publisher_sessions.cookie_jar()] == ["access"]
    finally:
        publisher_sessions.current_user.reset(bound)
    assert TestClient(app).post("/api/login", json={"username": "ren_carol", "password": "rcpw"}).status_code == 401
    _login("ren_caroline", "rcpw")


def test_self_rename_keeps_the_session_working(boss):
    r = boss.post("/api/admin/users/boss/rename", json={"new_username": "bigboss"})
    assert r.status_code == 200, r.text
    s = boss.get("/api/session").json()  # same cookie, no re-login
    assert s["user"] == "bigboss" and s["is_admin"] is True


def test_seed_hints_but_never_backdoors_an_adminless_instance(boss):
    """Accounts exist but nobody has the privilege (upgraded instance) — the
    seed must NOT create an admin login; it only logs a hint."""
    from gamma.db import connect_users_db
    from gamma.logbuf import tail
    from gamma.seed import ensure_admin_seed

    with connect_users_db() as conn:
        conn.execute("UPDATE users SET is_admin = 0")
        conn.commit()
    try:
        seen = tail(0)
        last_seq = seen[-1]["seq"] if seen else 0
        assert ensure_admin_seed() is None
        assert any("set-admin" in e["msg"] for e in tail(last_seq))
        with connect_users_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] == 0
    finally:  # restore for the tests below
        with connect_users_db() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE username = 'bigboss'")
            conn.commit()


def test_delete_user_removes_account_and_data(boss):
    from gamma.db import ws_dir
    ws = workspace_of("newbie")
    assert (ws_dir(ws) / "pages.db").exists()
    r = boss.delete("/api/admin/users/newbie")
    assert r.status_code == 200, r.text
    assert "newbie" not in [u["username"] for u in r.json()["users"]]
    assert r.json()["deleted_workspaces"] == [ws]  # the personal workspace went with the account
    from gamma.app import app
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "newbie", "password": "rotated"}).status_code == 401
    assert not (ws_dir(ws) / "pages.db").exists()
