"""A Gamma Cloud account deleted at the account server takes its library on
the share host with it (gamma/cloud_deletions.py): the share host asks for
the deletions with its client secret, deletes each linked account through
the account-delete job (its workspaces, their files and the shares that
published its pages, a final copy kept first), moves its cursor past
every deletion it read and keeps the linked ones on its list of those due
until they are gone, a kept one holding back nothing and a restored one
taken off. The fake account server
is test_cloud_auth's, grown the deletions endpoint."""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from test_cloud_auth import ISSUER, FakeAccountServer, browser, callback, start

from conftest import account_of, workspace_of
from gamma import cloud_auth, cloud_deletions, cloud_sync, jobs, workspaces, ws_backup
from gamma.db import connect_users_db
from gamma.server_settings import _get_raw, _set_raw

CLIENT_ID, SECRET = "gc_share", "share-secret"


class Cloud(FakeAccountServer):
    """``deleted``: the audit entries the deletions endpoint lists, as (id,
    subject); ``asked``: the ``after`` of each call, with its Basic pair."""

    def __init__(self):
        super().__init__()
        self.deleted = []
        self.asked = []
        self.last = 0   # the newest audit entry there is

    def http(self, url, data=None, headers=None, method=None, timeout=None):
        if url.startswith(ISSUER + cloud_deletions.PATH + "?after="):
            after = int(url.rsplit("=", 1)[1])
            self.asked.append((after, (headers or {}).get("Authorization", "")))
            if self.offline:
                raise cloud_auth.CloudAuthError("cannot reach the account server: offline")
            found = [{"id": i, "account_id": s, "deleted_at": "2026-11-08T10:00:00.000Z"}
                     for i, s in self.deleted if i > after][:2]  # pages of two
            last = max([self.last, *(i for i, _ in self.deleted)])
            return {"deletions": found, "next": found[-1]["id"] if len(found) == 2 else max(after, last)}
        return super().http(url, data=data, headers=headers, method=method, timeout=timeout)


@pytest.fixture
def cloud(monkeypatch):
    fake = Cloud()
    monkeypatch.setattr(cloud_auth, "_http", fake.http)
    monkeypatch.setattr(cloud_auth, "revoke_later", lambda tokens: None)
    monkeypatch.setattr(cloud_sync, "_background", lambda fn: fn())
    monkeypatch.setattr(cloud_deletions, "_background", lambda fn: fn())
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    monkeypatch.setenv("GAMMA_CLOUD_SHARE_HOST", "1")
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", SECRET)
    monkeypatch.delenv("GAMMA_CLOUD_ADMIN_SUBJECT", raising=False)
    cloud_auth._discovery_cache.clear()
    cloud_auth._jwks_cache.clear()
    cloud_auth._access.clear()
    _set_raw(cloud_deletions.CURSOR_KEY, "")
    _set_raw(cloud_deletions.PENDING_KEY, "")
    yield fake
    cloud_auth._access.clear()
    cloud_auth._discovery_cache.clear()
    _set_raw(cloud_deletions.CURSOR_KEY, "")
    _set_raw(cloud_deletions.PENDING_KEY, "")
    with connect_users_db() as conn:
        conn.execute("DELETE FROM identities")
        conn.commit()


def signed_in(cloud, name):
    """``name`` signs in to the share host with Gamma Cloud (subject
    ``sub-<name>``), which makes the account; returns its browser."""
    cloud.person.update({"sub": f"sub-{name}", "preferred_username": name, "email": f"{name}@example.org"})
    c = browser()
    assert callback(c, start(c)).headers["location"] == "/"
    c.headers["X-Gamma-Workspace"] = workspace_of(name)
    return c


def published(c, title):
    """A page shared to anyone, as Publish leaves it on the share host:
    (page id, share token)."""
    page = c.post("/api/pages", json={"title": title}).json()
    r = c.post(f"/api/share/{page['id']}")
    assert r.status_code == 200, r.text
    return page["id"], r.json()["token"]


def readable(page_id, token):
    from gamma.app import app
    return TestClient(app).get(f"/api/blocks/{page_id}/subtree", params={"share": token}).status_code == 200


def finished(name):
    """Wait for the server's account-delete job of ``name``'s account."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT id FROM jobs WHERE owner = '' AND kind = 'account-delete' AND title LIKE ? "
                           "ORDER BY created_at DESC LIMIT 1", (f"Delete account {name},%",)).fetchone()
    assert row, f"no delete job for {name}"
    return jobs.wait(row[0])


def test_a_deleted_cloud_account_takes_its_library_and_pages_with_it(cloud):
    gone = signed_in(cloud, "cd_ana")
    ana, ana_ws = account_of("cd_ana"), workspace_of("cd_ana")
    page, token = published(gone, "Ana's notes")
    stays = signed_in(cloud, "cd_ben")
    ben_page, ben_token = published(stays, "Ben's notes")
    assert readable(page, token) and readable(ben_page, ben_token)
    # ana's account is deleted at Gamma Cloud, and one that never came here
    cloud.deleted = [(3, "sub-nobody-here"), (5, "sub-cd_ana")]
    cloud.last = 9
    out = cloud_deletions.tick()
    assert out["started"] == ["cd_ana"] and out["kept"] == []
    job = finished("cd_ana")
    assert job["state"] == "done" and job["result"] == {"deleted_workspaces": [ana_ws]}, job
    assert account_of("cd_ana") == "" and workspaces.get(ana_ws) is None
    assert not readable(page, token) and readable(ben_page, ben_token)
    assert [f for f in ws_backup.deleted_dir().glob(f"{ana_ws}-*.zip")]   # its final copy, kept
    # asked with this server's client pair, once more from the end of the full page,
    # then whether ana's deletion still stands before deleting
    basic = "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    assert cloud.asked[:3] == [(0, basic), (5, basic), (4, basic)]
    # the job's end asked again at once; the cursor is past every entry now
    assert _get_raw(cloud_deletions.CURSOR_KEY) == "9" and pending() == {}
    cloud.asked.clear()
    assert cloud_deletions.tick() == {"started": [], "running": 0, "waiting": 0, "kept": [], "after": 9}
    assert cloud.asked[0][0] == 9
    assert account_of("cd_ben")


def pending():
    return json.loads(_get_raw(cloud_deletions.PENDING_KEY, "{}") or "{}")


def keeping(monkeypatch, name):
    """``name``'s account is kept as this server's last admin would be."""
    real, kept = cloud_deletions._allowed, account_of(name)

    def allowed(user_id, subject):
        if user_id != kept:
            return real(user_id, subject)

        def check(conn):
            raise ValueError("it is this server's last admin, so it is kept")
        return check
    monkeypatch.setattr(cloud_deletions, "_allowed", allowed)


def test_a_delete_that_waits_its_turn_runs_at_the_next_tick(cloud, monkeypatch):
    signed_in(cloud, "cd_cy")
    signed_in(cloud, "cd_dee")
    cloud.deleted = [(2, "sub-cd_cy"), (4, "sub-nobody"), (6, "sub-cd_dee")]
    monkeypatch.setattr(cloud_deletions, "AT_ONCE", 1)
    monkeypatch.setattr(cloud_deletions, "_background", lambda fn: None)   # no tick at a job's end
    out = cloud_deletions.tick()
    assert out["started"] == ["cd_cy"] and out["waiting"] == 1 and out["after"] == 6
    assert finished("cd_cy")["state"] == "done"
    assert pending() == {"sub-cd_cy": 2, "sub-cd_dee": 6}   # cy was still here when it asked
    out = cloud_deletions.tick()
    assert out["started"] == ["cd_dee"] and pending() == {"sub-cd_dee": 6}
    assert finished("cd_dee")["state"] == "done"
    assert cloud_deletions.tick()["started"] == [] and pending() == {}
    assert account_of("cd_cy") == account_of("cd_dee") == ""


def test_a_kept_account_does_not_hold_back_the_deletions_after_it(cloud, monkeypatch):
    signed_in(cloud, "cd_boss")
    signed_in(cloud, "cd_late")
    keeping(monkeypatch, "cd_boss")
    monkeypatch.setattr(cloud_deletions, "MAX_PAGES", 2)   # four deletions a tick, in pages of two
    cloud.deleted = [(1, "sub-cd_boss"), *((i, f"sub-stranger-{i}") for i in range(2, 10)), (10, "sub-cd_late")]
    for after in (4, 8):
        out = cloud_deletions.tick()
        assert out["kept"] == ["cd_boss"] and out["started"] == [] and out["after"] == after
    out = cloud_deletions.tick()
    assert out["kept"] == ["cd_boss"] and out["started"] == ["cd_late"] and out["after"] == 10
    assert finished("cd_late")["state"] == "done" and account_of("cd_late") == ""
    assert account_of("cd_boss") and pending() == {"sub-cd_boss": 1}


def test_a_restored_cloud_account_is_taken_off_the_list(cloud, monkeypatch):
    signed_in(cloud, "cd_fay")
    fay = account_of("cd_fay")
    real = cloud_deletions._allowed
    keeping(monkeypatch, "cd_fay")
    cloud.deleted = [(3, "sub-cd_fay")]
    assert cloud_deletions.tick()["kept"] == ["cd_fay"] and pending() == {"sub-cd_fay": 3}
    monkeypatch.setattr(cloud_deletions, "_allowed", real)   # no longer kept here, but restored at Gamma Cloud meanwhile
    cloud.deleted, cloud.last = [], 3
    out = cloud_deletions.tick()
    assert out["started"] == [] and out["kept"] == [] and pending() == {}
    assert account_of("cd_fay") == fay


def test_an_unlinked_account_is_kept(cloud):
    signed_in(cloud, "cd_eve")
    eve = account_of("cd_eve")
    with connect_users_db() as conn:   # an admin unlinked it from Gamma Cloud (manage.py unlink-identity)
        conn.execute("DELETE FROM identities WHERE user_id = ?", (eve,))
        conn.commit()
    cloud.deleted = [(7, "sub-cd_eve")]
    out = cloud_deletions.tick()
    assert out["started"] == [] and out["after"] == 7 and account_of("cd_eve") == eve


def test_only_a_share_host_with_its_secret_asks(cloud, monkeypatch):
    cloud.deleted = [(1, "sub-whoever")]
    monkeypatch.delenv("GAMMA_CLOUD_SHARE_HOST")
    assert cloud_deletions.tick() == {}
    monkeypatch.setenv("GAMMA_CLOUD_SHARE_HOST", "1")
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", "")
    assert cloud_deletions.tick() == {}
    assert cloud.asked == []
    # unreachable, or an answer that is no list: a warning, and the cursor stays
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", SECRET)
    cloud.offline = True
    assert "offline" in cloud_deletions.tick()["error"]
    cloud.offline = False
    monkeypatch.setattr(cloud_auth, "_http", lambda url, data=None, headers=None, **kw: {"deletions": "x", "next": 1})
    assert "not one" in cloud_deletions.tick()["error"]
    assert _get_raw(cloud_deletions.CURSOR_KEY, "") == ""


def test_the_last_admin_is_kept(cloud, monkeypatch):
    signed_in(cloud, "cd_root")
    root = account_of("cd_root")
    check = cloud_deletions._allowed(root, "sub-cd_root")
    with connect_users_db() as conn:
        check(conn)                                           # no admin: it may go
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (root,))
        others = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_guest = 0 AND id != ?",
                              (root,)).fetchone()[0]
        if not others:
            with pytest.raises(ValueError, match="last admin"):
                check(conn)
        conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (root,))
        conn.commit()
    with pytest.raises(ValueError, match="no longer linked"):
        with connect_users_db() as conn:
            cloud_deletions._allowed(root, "sub-someone-else")(conn)
