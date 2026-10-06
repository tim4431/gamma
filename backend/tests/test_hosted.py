"""Hosted containers (gamma/hosted.py): the sync with the account server
and where its answer lands — the storage defaults and caps, the sign-in
policy (``invited``) and the account cap, the read-only gate, the
server-config fields, the notice and the Server pane's endpoints. The fake
account server is test_cloud_auth's, with ``/api/hosted/sync`` answered
behind HTTP Basic auth by the container's own client."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import account_of, drop_user, fresh_client, login, make_page, make_user, workspace_of
from fastapi import HTTPException
from test_cloud_auth import ISSUER, cloud  # noqa: F401  (the fake account server fixture)

from gamma import cloud_auth, guests, hosted, server_settings, workspaces
from gamma.db import connect_pages_db, connect_users_db, format_stamp, page_now
from gamma.server_settings import _get_raw, _set_raw

CLIENT_ID, SECRET = "gc_hosted", "hosted-secret"

PRO = {"plan": "pro", "status": "active", "read_only": False, "policy": "invited", "max_accounts": 0,
       "quota_mb": 1000, "max_upload_mb": 100, "offsite": {"interval_s": 3600, "keep": 30},
       "grace_until": None, "message": ""}


@pytest.fixture
def plan(cloud, monkeypatch):  # noqa: F811
    """A hosted container against the fake account server: ``plan.answer``
    is what the next sync gets, ``plan.reports`` the bodies it posted."""
    fake = cloud
    fake.answer = dict(PRO)
    fake.reports = []
    plain = fake.http

    def http(url, data=None, headers=None, method=None, timeout=None):
        if url != ISSUER + hosted.SYNC_PATH:
            return plain(url, data=data, headers=headers, method=method, timeout=timeout)
        fake.calls.append((method, hosted.SYNC_PATH))
        if fake.offline:
            raise cloud_auth.CloudAuthError("cannot reach the account server: offline")
        auth = (headers or {}).get("Authorization", "")
        if not auth.startswith("Basic ") or base64.b64decode(auth[6:]).decode() != f"{CLIENT_ID}:{SECRET}":
            raise cloud_auth.CloudAuthError("invalid_client", status=401, error="invalid_client")
        assert method == "POST" and headers["Content-Type"] == "application/json"
        fake.reports.append(json.loads(data))
        return dict(fake.answer)

    monkeypatch.setattr(cloud_auth, "_http", http)
    monkeypatch.setenv("GAMMA_HOSTED", "1")
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", SECRET)
    hosted.forget()
    yield fake
    hosted.forget()


@pytest.fixture(scope="module")
def admin():
    import gamma.app  # noqa: F401  (the startup's first-admin seed runs before this admin exists)

    make_user("ho_admin", "ho-admin-pw1", is_admin=1)
    make_user("ho_member", "ho-member-pw1")
    yield login("ho_admin", "ho-admin-pw1")
    drop_user("ho_admin")  # test_admin_users knows every admin of the shared users.db


@pytest.fixture
def no_saved_limits():
    """The suite's saved storage defaults put aside for the test."""
    before = {key: _get_raw(key) for key in ("max_upload_mb", "quota_mb")}
    for key in before:
        _set_raw(key, "")
    yield
    for key, value in before.items():
        _set_raw(key, value)


def _claims(sub, username):
    return {"sub": sub, "preferred_username": username, "email": f"{username}@example.org", "email_verified": True}


def _accounts():
    with connect_users_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE is_guest = 0").fetchone()[0]


def test_sync_stores_the_answer_and_reports_the_server(plan):
    found = hosted.sync_now()
    assert found["plan"] == "pro" and found["policy"] == "invited" and found["quota_mb"] == 1000
    assert found["offsite"] == {"interval_s": 3600, "keep": 30} and found["synced_at"]
    report = plan.reports[-1]
    assert set(report) == {"version", "schema", "accounts", "active_accounts", "uploads_bytes", "data_bytes",
                           "public_url", "last_write_at", "errors", "uptime_s"}
    assert report["accounts"] == _accounts() and report["data_bytes"] > 0 and report["schema"]
    assert 0 <= report["active_accounts"] <= report["accounts"]
    assert report["last_write_at"] is None and report["errors"] == 0  # forget() cleared both
    assert isinstance(report["uptime_s"], int) and report["uptime_s"] >= 0
    # kept in the settings KV: a restart reads it back
    assert json.loads(_get_raw(hosted.SETTINGS_KEY))["limits"]["plan"] == "pro"
    assert hosted.limits() == found
    # a plan change arrives with the next sync
    plan.answer.update(plan="plus", policy="refuse")
    assert hosted.sync_now()["plan"] == "plus"
    assert cloud_auth.settings()["policy"] == "refuse"


def test_nothing_happens_off_a_hosted_server(plan, admin, monkeypatch):
    monkeypatch.delenv("GAMMA_HOSTED")
    hosted.tick()
    assert hosted.sync_now() is None and hosted.limits() is None and hosted.pane() is None
    assert not [path for _method, path in plan.calls if path == hosted.SYNC_PATH]
    member = login("ho_member", "ho-member-pw1")
    config = member.get("/api/server-config").json()
    assert config["read_only"] is False and config["hosted"] is None
    # nor is anything noted for the report: neither a write nor a 5xx
    assert member.post("/api/blocks", json={"parent_id": "root", "content": "ho unhosted"}).status_code == 200
    _broken(monkeypatch)
    assert fresh_client(raise_server_exceptions=False).get("/api/server-config").status_code == 500
    assert (hosted._errors, hosted._last_write, _get_raw(hosted.LAST_WRITE_KEY)) == (0, None, "")


def _broken(monkeypatch):
    """GET /api/server-config fails with an exception (a 500)."""
    def boom():
        raise RuntimeError("ho: a bug")
    monkeypatch.setattr(guests, "logins_open", boom)


def test_a_failed_sync_keeps_the_last_answer(plan, monkeypatch):
    hosted.sync_now()
    stored = _get_raw(hosted.SETTINGS_KEY)
    plan.offline = True
    assert hosted.sync_now() is None
    assert _get_raw(hosted.SETTINGS_KEY) == stored and hosted.limits()["plan"] == "pro"
    assert "offline" in hosted.pane()["last_failure"]["error"]
    plan.offline = False
    plan.answer = {"plan": "pro", "status": "on fire"}  # not a plan's limits
    assert hosted.sync_now() is None and _get_raw(hosted.SETTINGS_KEY) == stored
    plan.answer = dict(PRO)
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", "wrong")  # the account server refuses the client
    assert hosted.sync_now() is None and _get_raw(hosted.SETTINGS_KEY) == stored
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", SECRET)
    assert hosted.sync_now() is not None and hosted.pane()["last_failure"] is None


def test_storage_follows_the_plan_and_an_admin_only_tightens(plan, admin, no_saved_limits):
    hosted.sync_now()
    got = admin.get("/api/admin/settings").json()
    assert (got["max_upload_mb"], got["max_upload_mb_source"]) == (100, "plan")
    assert (got["quota_mb"], got["quota_mb_source"]) == (1000, "plan")
    assert got["plan_caps"] == {"max_upload_mb": 100, "quota_mb": 1000}
    # lower is the admin's to set; higher (or unlimited) is refused
    r = admin.put("/api/admin/settings", json={"max_upload_mb": 40, "quota_mb": 500})
    assert r.status_code == 200 and (r.json()["max_upload_mb"], r.json()["max_upload_mb_source"]) == (40, "saved")
    for bad in ({"max_upload_mb": 200}, {"quota_mb": 0}, {"quota_mb": 2000}):
        r = admin.put("/api/admin/settings", json=bad)
        assert r.status_code == 400 and "plan allows at most" in r.json()["detail"], bad
    assert admin.put("/api/admin/users/ho_member", json={"quota_mb": 5000}).status_code == 400
    # a value saved before the plan came is held to the cap
    _set_raw("max_upload_mb", "500")
    assert server_settings.get_defaults()["max_upload_mb_source"] == "plan"
    member = account_of("ho_member")
    with connect_users_db() as conn:
        conn.execute("UPDATE users SET quota_mb = 0 WHERE id = ?", (member,))  # an unlimited override
        conn.commit()
    try:
        assert server_settings.user_limits(member) == {"max_upload_mb": 100, "quota_mb": 1000}
        with pytest.raises(Exception) as refused:
            server_settings.check_upload_allowed(workspaces.default_workspace(member), 101 * server_settings.MB)
        assert refused.value.status_code == 413
    finally:
        with connect_users_db() as conn:
            conn.execute("UPDATE users SET quota_mb = NULL WHERE id = ?", (member,))
            conn.commit()
    # a shared workspace without a quota of its own gets the plan's
    ws = admin.post("/api/workspaces", json={"name": "HO quota lab", "kind": "shared", "owner": "ho_admin"}).json()["id"]
    assert server_settings.workspace_quota(ws)["quota_mb"] == 1000


def _invite(admin, subject, username):
    ws = admin.post("/api/workspaces", json={"name": f"HO {username}", "kind": "shared", "owner": "ho_admin"}).json()["id"]
    with connect_users_db() as conn:
        conn.execute("INSERT INTO pending_memberships (workspace_id, subject, username, role, invited_by, created_at) "
                     "VALUES (?, ?, ?, 'editor', ?, ?)", (ws, subject, username, account_of("ho_admin"), page_now()))
        conn.commit()
    return ws


def test_invited_policy_admits_only_invited_people(plan, admin, monkeypatch):
    hosted.sync_now()
    cfg = cloud_auth.settings()
    assert (cfg["policy"], cfg["policy_source"]) == ("invited", "plan")  # over GAMMA_CLOUD_POLICY=refuse
    with pytest.raises(cloud_auth.CloudAuthError) as refused:
        cloud_auth.resolve_account(_claims("sub-ho-stranger", "ho-stranger"))
    assert str(refused.value) == cloud_auth.INVITED_ONLY
    assert account_of("ho-stranger") == ""
    ws = _invite(admin, "sub-ho-ivy", "ho-ivy")
    user_id, name = cloud_auth.resolve_account(_claims("sub-ho-ivy", "ho-ivy"))
    assert name == "ho-ivy" and workspaces.role_of(ws, user_id) == "editor"
    # an existing account is not claimed: invited is refuse for those
    with pytest.raises(cloud_auth.CloudAuthError, match="not linked"):
        cloud_auth.resolve_account(_claims("sub-ho-member", "ho_member"))
    # off a hosted server the environment (or the saved setting) may name it too
    monkeypatch.delenv("GAMMA_HOSTED")
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "invited")
    assert (cloud_auth.settings()["policy"], cloud_auth.settings()["policy_source"]) == ("invited", "environment")
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    with pytest.raises(ValueError, match="provision or invited"):
        cloud_auth.save_settings(policy="everyone")


def test_the_account_cap(plan, admin, monkeypatch):
    plan.answer.update(policy="provision", max_accounts=_accounts())
    hosted.sync_now()
    with pytest.raises(cloud_auth.CloudAuthError, match=f"reached its plan's {_accounts()} accounts"):
        cloud_auth.resolve_account(_claims("sub-ho-late", "ho-late"))
    r = admin.post("/api/admin/users", json={"username": "ho_extra", "password": "ho-extra-pw1"})
    assert r.status_code == 403 and "reached its plan" in r.json()["detail"]
    # the admin subject is let in whatever the cap
    monkeypatch.setenv("GAMMA_CLOUD_ADMIN_SUBJECT", "sub-ho-owner")
    try:
        assert cloud_auth.resolve_account(_claims("sub-ho-owner", "ho-owner"))[1] == "ho-owner"
    finally:
        drop_user("ho-owner")
    # one more allowed: one more provisioned
    plan.answer.update(max_accounts=_accounts() + 1)
    hosted.sync_now()
    assert cloud_auth.resolve_account(_claims("sub-ho-one", "ho-one"))[1] == "ho-one"
    with pytest.raises(cloud_auth.CloudAuthError, match="reached its plan"):
        cloud_auth.resolve_account(_claims("sub-ho-two", "ho-two"))


def test_read_only_refuses_writes_but_lets_people_leave_with_their_data(plan, admin):
    page = make_page(admin, "HO read-only page")
    member = login("ho_member", "ho-member-pw1")
    _invite(admin, "sub-ho-late-invite", "ho-late-invite")
    plan.answer.update(status="read_only", read_only=True, message="The subscription lapsed.")
    hosted.sync_now()
    r = admin.post("/api/blocks", json={"parent_id": "root", "content": "more"})
    assert r.status_code == 423 and r.json() == {"detail": "The subscription lapsed.", "read_only": True}
    # an op batch (what the collaboration client sends for every edit) is refused the same way
    r = admin.post(f"/api/pages/{page['id']}/ops", json={"client": "c1", "ops": []})
    assert r.status_code == 423 and r.json()["detail"] == "The subscription lapsed."
    assert admin.put("/api/prefs/ho-test", json={"value": 1}).status_code == 423
    assert admin.delete(f"/api/blocks/{page['id']}").status_code == 423
    # reads, exports, notices, sign-out and sign-in still work
    assert admin.get(f"/api/blocks/{page['id']}").status_code == 200
    r = admin.post("/api/jobs/workspace-export", json={"uploads": False})
    assert r.status_code == 200, r.text
    assert admin.get(f"/api/pages/{page['id']}/export?mode=readable").status_code == 200
    config = member.get("/api/server-config").json()
    assert config["read_only"] is True and config["hosted"] == {"plan": "pro", "status": "read_only"}
    mine = [n for n in admin.get("/api/notices").json()["notices"] if n["id"] == "hosted"]
    assert [(n["tone"], n["pane"]) for n in mine] == [("error", "server")]
    theirs = [n for n in member.get("/api/notices").json()["notices"] if n["id"] == "hosted"]
    assert [(n["tone"], n["pane"]) for n in theirs] == [("error", "account")]
    r = member.post("/api/notices/hosted/seen", json={"fingerprint": theirs[0]["fingerprint"]})
    assert r.status_code == 200
    assert member.post("/api/logout").status_code == 200
    login("ho_member", "ho-member-pw1")
    # a sign-in still works for an existing account, but makes no new one
    with pytest.raises(cloud_auth.CloudAuthError, match="takes no new accounts"):
        cloud_auth.resolve_account(_claims("sub-ho-late-invite", "ho-late-invite"))
    # the admin's Sync now goes through, and a paid-up plan lifts the gate
    plan.answer = dict(PRO)
    r = admin.post("/api/admin/hosted/sync")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["hosted"]["limits"]["read_only"] is False
    assert admin.post("/api/blocks", json={"parent_id": "root", "content": "back"}).status_code == 200


def test_grace_is_an_admin_warning(plan, admin):
    plan.answer.update(status="grace", grace_until="2026-11-01T00:00:00Z")
    hosted.sync_now()
    notices = [n for n in admin.get("/api/notices").json()["notices"] if n["id"] == "hosted"]
    assert len(notices) == 1 and notices[0]["tone"] == "warn" and notices[0]["params"] == {"date": "2026-11-01"}
    assert "2026-11-01" in notices[0]["title"]
    member = login("ho_member", "ho-member-pw1")
    assert not [n for n in member.get("/api/notices").json()["notices"] if n["id"] == "hosted"]
    assert member.post("/api/blocks", json={"parent_id": "root", "content": "still writable"}).status_code == 200


def test_the_server_pane(plan, admin, monkeypatch):
    assert admin.get("/api/admin/settings").json()["hosted"] == {
        "limits": None, "accounts": _accounts(), "last_failure": None, "issuer": ISSUER}
    r = admin.post("/api/admin/hosted/sync")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["max_upload_mb"] <= 100
    pane = admin.get("/api/admin/settings").json()["hosted"]
    assert pane["limits"]["plan"] == "pro" and pane["limits"]["synced_at"]
    plan.offline = True
    r = admin.post("/api/admin/hosted/sync").json()
    assert r["ok"] is False and r["hosted"]["last_failure"]["error"] and r["hosted"]["limits"]["plan"] == "pro"
    assert login("ho_member", "ho-member-pw1").post("/api/admin/hosted/sync").status_code == 403
    monkeypatch.delenv("GAMMA_HOSTED")
    assert admin.post("/api/admin/hosted/sync").status_code == 400
    assert admin.get("/api/admin/settings").json()["hosted"] is None


def test_the_answer_is_kept_in_memory(plan, admin, monkeypatch):
    hosted.sync_now()
    reads = []
    real = hosted._read_kv
    monkeypatch.setattr(hosted, "_read_kv", lambda conn=None: reads.append(1) or real(conn))
    # the gate and the settings read it on every write without the database
    for _ in range(3):
        assert admin.post("/api/blocks", json={"parent_id": "root", "content": "ho cached"}).status_code == 200
        assert cloud_auth.settings()["policy"] == "invited"
    assert reads == []
    # something else wrote the KV: reset() makes the next use read it again
    stored = json.loads(_get_raw(hosted.SETTINGS_KEY))
    stored["limits"]["plan"] = "plus"
    _set_raw(hosted.SETTINGS_KEY, json.dumps(stored))
    assert hosted.limits()["plan"] == "pro"
    hosted.reset()
    assert hosted.limits()["plan"] == "plus" and reads == [1]


@pytest.mark.usefixtures("admin")  # the app is up first: its startup sync would report the errors
def test_errors_count_until_a_sync_reports_them(plan, monkeypatch):
    anyone = fresh_client(raise_server_exceptions=False)
    _broken(monkeypatch)
    assert anyone.get("/api/server-config").status_code == 500  # an exception

    def unavailable():
        raise HTTPException(503, "ho: not now")
    monkeypatch.setattr(guests, "logins_open", unavailable)
    assert anyone.get("/api/server-config").status_code == 503  # an answer
    hosted.sync_now()
    assert plan.reports[-1]["errors"] == 2
    hosted.sync_now()
    assert plan.reports[-1]["errors"] == 0  # the sync that reported them took them off
    assert anyone.get("/api/server-config").status_code == 503
    plan.offline = True
    assert hosted.sync_now() is None  # a failed sync leaves them
    plan.offline = False
    hosted.sync_now()
    assert plan.reports[-1]["errors"] == 1


def test_the_last_write(plan, admin, monkeypatch):
    hosted.sync_now()
    assert plan.reports[-1]["last_write_at"] is None
    # a read and a write that failed leave it
    assert admin.get("/api/notices").status_code == 200
    assert admin.post("/api/blocks", json={}).status_code == 422
    hosted.sync_now()
    assert plan.reports[-1]["last_write_at"] is None
    before = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert admin.post("/api/blocks", json={"parent_id": "root", "content": "ho last write"}).status_code == 200
    hosted.sync_now()
    at = plan.reports[-1]["last_write_at"]
    assert at and at >= before
    # a restart: nothing in memory, the time the last sync saved
    monkeypatch.setattr(hosted, "_last_write", None)
    hosted.sync_now()
    assert plan.reports[-1]["last_write_at"] == at == _get_raw(hosted.LAST_WRITE_KEY)


def test_active_accounts(plan):
    make_user("ho_quiet", "ho-quiet-pw1")
    quiet = account_of("ho_quiet")
    week_ago = format_stamp(datetime.now(timezone.utc) - timedelta(days=hosted.ACTIVE_DAYS, hours=1))

    def active():
        hosted.sync_now()
        return plan.reports[-1]["active_accounts"]

    before = active()  # an account that has done nothing is not active
    client = login("ho_quiet", "ho-quiet-pw1")
    assert active() == before + 1  # a sign-in
    with connect_users_db() as conn:
        conn.execute("UPDATE sessions SET created_at = ? WHERE user_id = ?", (week_ago, quiet))
        conn.commit()
    assert active() == before
    assert client.put("/api/prefs/recent-views", json={"value": []}).status_code == 200
    assert active() == before + 1  # a page opened: the app saves the recents
    with connect_pages_db(workspace_of("ho_quiet")) as conn:
        conn.execute("UPDATE workspace_prefs SET updated_at = ? WHERE user_id = ?", (week_ago, quiet))
        conn.commit()
    assert active() == before
    make_page(client, "HO quiet page")
    assert active() == before + 1  # a page written


def test_the_off_site_copies_follow_the_plan_where_it_is_stricter(plan, monkeypatch):
    from gamma import offsite

    plan.answer.update(offsite={"interval_s": 1800, "keep": 30})
    hosted.sync_now()
    before = _get_raw(offsite.SETTINGS_KEY)
    try:
        # from the environment: the plan fills what it leaves unset, a stricter value of its own stands
        monkeypatch.setenv("GAMMA_S3_BUCKET", "ho-bucket")
        assert (offsite.settings()["interval_s"], offsite.settings()["keep"]) == (1800, 30)
        monkeypatch.setenv("GAMMA_OFFSITE_INTERVAL", "600")
        monkeypatch.setenv("GAMMA_OFFSITE_KEEP", "50")
        assert (offsite.settings()["interval_s"], offsite.settings()["keep"]) == (600, 50)
        monkeypatch.setenv("GAMMA_OFFSITE_INTERVAL", "7200")
        monkeypatch.setenv("GAMMA_OFFSITE_KEEP", "3")
        assert (offsite.settings()["interval_s"], offsite.settings()["keep"]) == (1800, 30)
        # saved by the admin: the plan wins only where it is stricter
        monkeypatch.delenv("GAMMA_S3_BUCKET")
        _set_raw(offsite.SETTINGS_KEY, "")
        assert (offsite.settings()["interval_s"], offsite.settings()["keep"]) == (1800, 30)  # over the defaults
        _set_raw(offsite.SETTINGS_KEY, json.dumps({"bucket": "ho-saved", "interval_s": 600, "keep": 3}))
        assert (offsite.settings()["interval_s"], offsite.settings()["keep"]) == (600, 30)
        # off a hosted server the saved values stand
        monkeypatch.delenv("GAMMA_HOSTED")
        assert (offsite.settings()["interval_s"], offsite.settings()["keep"]) == (600, 3)
    finally:
        _set_raw(offsite.SETTINGS_KEY, before)
