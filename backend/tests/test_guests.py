"""Guest accounts keep nothing (gamma/guests.py, docs/dev/guests.md): one
fresh account per guest login with its own workspace, gone after
guest_ttl_hours — the middleware signs an expired guest out and deletes it,
the sweeper deletes the rest; the live-guest cap, the seed library, the
admin settings and the server-config fields."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import login, make_page, make_user


def _new_client():
    from gamma.app import app
    return TestClient(app)


def _guest_client():
    c = _new_client()
    r = c.post("/api/login-guest")
    assert r.status_code == 200, r.text
    return c, r.json()["username"]


def _backdate(username, hours):
    """Make the account ``hours`` older than now."""
    from gamma.db import connect_users_db
    then = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    with connect_users_db() as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE username = ?", (then, username))
        conn.commit()


def _exists(username):
    from gamma.db import connect_users_db
    with connect_users_db() as conn:
        return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None


def _titles(c):
    return [b["content"] for b in c.get("/api/blocks/root/children").json()["children"]]


@pytest.fixture(autouse=True)
def _stock_guest_settings(monkeypatch):
    """Every test starts from the default lifetime, demo off and no env."""
    from gamma.db import connect_users_db
    for var in ("GAMMA_GUEST_TTL_HOURS", "GAMMA_DEMO", "GAMMA_GUEST_MAX", "GAMMA_GUEST_SEED"):
        monkeypatch.delenv(var, raising=False)

    def reset():
        with connect_users_db() as conn:
            conn.execute("DELETE FROM settings WHERE key IN ('guest_ttl_hours', 'demo_mode')")
            conn.commit()
    reset()
    yield
    reset()


@pytest.fixture(scope="module")
def gadmin(client):
    make_user("gu_admin", "gu_adminpw", is_admin=1)
    return login("gu_admin", "gu_adminpw")


def test_a_guest_login_mints_an_account_with_its_own_workspace():
    from gamma import guests, workspaces
    from gamma.db import connect_users_db

    c, name = _guest_client()
    assert name.startswith("guest-") and len(name) == 14
    with connect_users_db() as conn:
        is_guest, pwhash, created, guest_date = conn.execute(
            "SELECT u.is_guest, u.password_hash, u.created_at, s.guest_date FROM users u "
            "JOIN sessions s ON s.username = u.username WHERE u.username = ?", (name,)).fetchone()
    assert is_guest == 1 and pwhash == "" and guest_date == created[:10]
    s = c.get("/api/session").json()
    assert s["user"] == name and s["is_guest"] is True and s["is_admin"] is False
    assert len(s["workspaces"]) == 1 and s["workspaces"][0]["id"] == s["default_workspace"]
    assert s["guest_expires_at"] == guests.expires_at(created, 24)
    ws = s["default_workspace"]
    assert workspaces.is_guest_workspace(ws) and workspaces.personal_owner(ws) == name
    # the welcome page, naming the lifetime
    children = c.get("/api/blocks/root/children").json()["children"]
    welcome = next(b for b in children if b["content"] == "Welcome")
    text = str(c.get(f"/api/blocks/{welcome['id']}/subtree").json())
    assert "24 hours" in text and "midnight" not in text
    # nobody signs in to it with a password
    assert c.post("/api/login", json={"username": name, "password": ""}).status_code == 401


def test_two_guests_are_isolated():
    a, a_name = _guest_client()
    b, b_name = _guest_client()
    assert a_name != b_name
    page = make_page(a, "Only mine")
    assert "Only mine" not in _titles(b)
    a_ws = a.get("/api/session").json()["default_workspace"]
    assert b.get(f"/api/blocks/{page['id']}", headers={"X-Gamma-Workspace": a_ws}).status_code in (403, 404)
    assert b.get("/api/blocks/root/children", params={"ws": a_ws}).status_code in (403, 404)


def test_expired_guest_is_signed_out_and_deleted():
    from gamma import auth, workspaces
    from gamma.db import ws_dir

    c, name = _guest_client()
    ws = workspaces.default_workspace(name)
    token = c.cookies.get("session")
    assert auth.session_lookup(token)[0] == name  # the websocket handshake's view
    _backdate(name, 25)
    assert auth.session_lookup(token) is None     # an expired guest opens no socket either
    r = c.get("/api/session")
    assert r.status_code == 200 and r.json()["user"] is None
    assert "session=" in r.headers.get("set-cookie", "")  # the cookie is cleared
    assert not _exists(name) and not ws_dir(ws).exists()


def test_an_expired_guest_tab_is_told_to_reload():
    c, name = _guest_client()
    _backdate(name, 25)
    r = c.get("/api/blocks/root/children", headers={"X-Gamma-User": name})
    assert r.status_code == 409 and r.headers["X-Gamma-Session-User"] == ""
    assert not _exists(name)


def test_a_shorter_lifetime_applies_to_existing_guests(monkeypatch):
    c, name = _guest_client()
    _backdate(name, 2)
    assert c.get("/api/session").json()["user"] == name
    monkeypatch.setenv("GAMMA_GUEST_TTL_HOURS", "1")
    assert c.get("/api/session").json()["user"] is None and not _exists(name)


def test_sweeper_deletes_expired_guests_only(gadmin):
    from gamma import guests

    _old_c, old = _guest_client()
    new_c, new = _guest_client()
    _backdate(old, 30)
    gone = guests.delete_expired()
    assert old in gone and new not in gone and "gu_admin" not in gone
    assert not _exists(old) and _exists(new)
    assert new_c.get("/api/session").json()["user"] == new
    assert guests.is_expired("2020-01-01T00:00:00.000000Z", ttl_hours=24)
    assert not guests.is_expired("2020-01-01T00:00:00.000000Z", ttl_hours=24,
                                 now=datetime(2020, 1, 1, 23, tzinfo=timezone.utc))
    assert guests.is_expired("garbage")  # fail closed


def test_the_sweeper_runs_in_the_app_lifespan(monkeypatch):
    from gamma import guests
    calls = []
    monkeypatch.setattr(guests, "delete_expired", lambda: calls.append(1) or [])

    async def run():
        async with guests.lifespan():
            for _ in range(100):
                if calls:
                    break
                await asyncio.sleep(0.01)
    asyncio.run(run())
    assert calls


def test_delete_account_takes_everything_that_is_only_the_accounts():
    from gamma import guests, workspaces
    from gamma.db import connect_users_db, page_now, ws_dir

    name = guests.new_guest()
    ws = workspaces.default_workspace(name)
    with connect_users_db() as conn:
        conn.execute("INSERT INTO ai_usage (username, at, kind) VALUES (?, ?, 'chat')", (name, page_now()))
        conn.execute("INSERT INTO user_prefs VALUES (?, '', 'profile', '{}', ?)", (name, page_now()))
        conn.execute("INSERT INTO sessions (token, username, created_at) VALUES (?, ?, ?)",
                     (f"tok-{name}", name, page_now()))
        conn.commit()
    assert workspaces.delete_account(name) == [ws]
    with connect_users_db() as conn:
        for table in ("users", "ai_usage", "user_prefs", "sessions", "workspace_members"):
            assert not conn.execute(f"SELECT 1 FROM {table} WHERE username = ?", (name,)).fetchone(), table
        assert not conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (ws,)).fetchone()
    assert not ws_dir(ws).exists()
    assert workspaces.delete_account(name) == []  # unknown account: nothing to do


def test_logout_deletes_the_guest():
    c, name = _guest_client()
    assert c.post("/api/logout").status_code == 200
    assert not _exists(name)


def test_live_guest_cap(monkeypatch):
    from gamma.db import connect_users_db
    with connect_users_db() as conn:
        live = conn.execute("SELECT COUNT(*) FROM users WHERE is_guest = 1").fetchone()[0]
    monkeypatch.setenv("GAMMA_GUEST_MAX", str(live))
    r = _new_client().post("/api/login-guest")
    assert r.status_code == 503 and "guests" in r.json()["detail"]
    monkeypatch.setenv("GAMMA_GUEST_MAX", str(live + 1))
    assert _new_client().post("/api/login-guest").status_code == 200


def test_guest_logins_are_rate_limited_per_ip(monkeypatch):
    from gamma import guests, workspaces
    made = []

    def fake_new_guest():  # the account row only: this test counts requests
        name = guests._insert_account()
        made.append(name)
        return name
    monkeypatch.setattr(guests, "new_guest", fake_new_guest)
    c = _new_client()
    codes = [c.post("/api/login-guest").status_code for _ in range(11)]
    assert codes == [200] * 10 + [429]
    for name in made:
        workspaces.delete_account(name)


def test_seed_library_is_restored_into_every_new_guest(tmp_path, monkeypatch):
    from gamma import ws_backup

    src, _ = _guest_client()
    make_page(src, "Sample paper from the seed")
    ws_backup.write_zip(src.get("/api/session").json()["default_workspace"], tmp_path / "seed.zip",
                        uploads=True, by="test")
    monkeypatch.setenv("GAMMA_GUEST_SEED", str(tmp_path / "seed.zip"))
    c, _ = _guest_client()
    assert "Sample paper from the seed" in _titles(c)
    # a seed that cannot be restored still lets the visitor in (the welcome page only)
    monkeypatch.setenv("GAMMA_GUEST_SEED", str(tmp_path / "missing.zip"))
    c, _ = _guest_client()
    assert "Welcome" in _titles(c)


def test_guest_workspace_rails():
    from gamma import workspaces
    c, name = _guest_client()
    ws = workspaces.default_workspace(name)
    assert workspaces.is_guest_workspace(ws) and not workspaces.is_guest_workspace("no-such-ws")
    assert c.post(f"/api/workspaces/{ws}/backups", json={}).status_code == 403
    assert c.get("/api/export-all").status_code == 403
    assert c.post("/api/workspaces", json={"name": "more"}).status_code == 403


def test_admin_guest_settings_round_trip(gadmin, monkeypatch):
    s = gadmin.get("/api/admin/settings").json()
    assert (s["guest_ttl_hours"], s["guest_ttl_source"]) == (24, "default")
    assert (s["demo_mode"], s["demo_mode_source"]) == (False, "default")
    assert s["guest_ttl_hours_range"] == [1, 720]
    cfg = _new_client().get("/api/server-config").json()
    assert cfg["guest_ttl_hours"] == 24 and cfg["demo"] is False and cfg["guest"] is True

    r = gadmin.put("/api/admin/settings", json={"guest_ttl_hours": 48, "demo_mode": True})
    assert r.status_code == 200, r.text
    assert (r.json()["guest_ttl_hours"], r.json()["guest_ttl_source"]) == (48, "saved")
    assert (r.json()["demo_mode"], r.json()["demo_mode_source"]) == (True, "saved")
    cfg = _new_client().get("/api/server-config").json()
    assert cfg["guest_ttl_hours"] == 48 and cfg["demo"] is True
    for bad in (0, 721, -5):
        assert gadmin.put("/api/admin/settings", json={"guest_ttl_hours": bad}).status_code == 400
    # a bad value refuses the whole request: nothing else was saved
    before = gadmin.get("/api/admin/settings").json()["quota_mb"]
    assert gadmin.put("/api/admin/settings", json={"quota_mb": before + 7, "guest_ttl_hours": 0}).status_code == 400
    assert gadmin.get("/api/admin/settings").json()["quota_mb"] == before
    assert gadmin.put("/api/admin/settings", json={"demo_mode": False}).json()["demo_mode"] is False

    # the environment wins, and the saved value cannot be changed under it
    monkeypatch.setenv("GAMMA_GUEST_TTL_HOURS", "6")
    monkeypatch.setenv("GAMMA_DEMO", "1")
    s = gadmin.get("/api/admin/settings").json()
    assert (s["guest_ttl_hours"], s["guest_ttl_source"]) == (6, "environment")
    assert (s["demo_mode"], s["demo_mode_source"]) == (True, "environment")
    assert gadmin.put("/api/admin/settings", json={"guest_ttl_hours": 12}).status_code == 400
    assert gadmin.put("/api/admin/settings", json={"demo_mode": False}).status_code == 400
    cfg = _new_client().get("/api/server-config").json()
    assert cfg["guest_ttl_hours"] == 6 and cfg["demo"] is True
    # an unusable override is ignored
    monkeypatch.setenv("GAMMA_GUEST_TTL_HOURS", "forever")
    assert gadmin.get("/api/admin/settings").json()["guest_ttl_source"] == "saved"
    # non-admins cannot change them
    c, _ = _guest_client()
    assert c.put("/api/admin/settings", json={"demo_mode": True}).status_code == 403


def test_manage_sweep_guests(capsys):
    import manage
    _c, name = _guest_client()
    _backdate(name, 100)
    manage.sweep_guests()
    assert name in capsys.readouterr().out and not _exists(name)


def test_manage_setup_creates_no_guest():
    import manage
    from gamma.db import connect_users_db
    with connect_users_db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM users WHERE is_guest = 1").fetchone()[0]
    manage.setup()
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE is_guest = 1").fetchone()[0] == before
        assert not conn.execute("SELECT 1 FROM users WHERE username = 'guest'").fetchone()
