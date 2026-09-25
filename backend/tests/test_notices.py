"""The red dot's feed (gamma/notices.py, /api/notices): admin-only sources,
the fingerprint ack, and what guests get."""

import pytest

from conftest import login as _login, make_user as _make_user
from gamma import logbuf, notices, version


def _drop_user(username):
    from gamma.db import connect_users_db

    with connect_users_db() as conn:
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.execute("DELETE FROM user_prefs WHERE username = ?", (username,))
        conn.commit()


@pytest.fixture(scope="module")
def nadmin(client):
    _make_user("nadmin", "nadminpw", is_admin=1)
    yield _login("nadmin", "nadminpw")
    _drop_user("nadmin")


@pytest.fixture(scope="module")
def nuser(client):
    _make_user("nuser", "nuserpw", is_admin=0)
    yield _login("nuser", "nuserpw")
    _drop_user("nuser")


@pytest.fixture(autouse=True)
def _quiet_sources(monkeypatch):
    monkeypatch.setattr(version, "_cache", {"at": 0.0, "ttl": 0.0, "release": None, "error": ""})
    monkeypatch.setattr(version, "VERSION", "0.2.1")
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.2.1", "url": "", "published_at": ""})
    monkeypatch.setattr(logbuf, "_last_seq", {"info": 0, "warning": 0, "error": 0})


def _ids(client):
    r = client.get("/api/notices")
    assert r.status_code == 200, r.text
    return [n["id"] for n in r.json()["notices"]]


def test_needs_a_session_and_is_empty_for_guests_and_up_to_date_admins(anon, guest, nadmin):
    assert anon.get("/api/notices").status_code == 401
    assert guest.get("/api/notices").json() == {"notices": []}
    assert guest.post("/api/notices/update/seen", json={"fingerprint": "x"}).status_code == 403
    assert _ids(nadmin) == []


def test_update_notice_until_the_release_is_seen(nadmin, nuser, monkeypatch):
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.3.0", "url": "https://example/rel", "published_at": ""})
    notice = nadmin.get("/api/notices").json()["notices"][0]
    assert notice["id"] == "update" and notice["fingerprint"] == "0.3.0"
    assert notice["pane"] == "server" and notice["tone"] == "warn" and "v0.3.0" in notice["title"]
    assert _ids(nuser) == []  # admin-only: a member is never told to pull an image
    # the ack names the fingerprint; a stale ack (another version) changes nothing
    assert nadmin.post("/api/notices/update/seen", json={"fingerprint": "0.2.9"}).status_code == 200
    assert _ids(nadmin) == ["update"]
    assert nadmin.post("/api/notices/update/seen", json={"fingerprint": "0.3.0"}).status_code == 200
    assert _ids(nadmin) == []
    # the next release brings it back
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.4.0", "url": "", "published_at": ""})
    version._cache["ttl"] = 0.0
    assert _ids(nadmin) == ["update"]


def test_log_errors_notice_follows_the_newest_error(nadmin, monkeypatch):
    assert _ids(nadmin) == []
    logbuf.log.error("[test] something broke")
    notice = nadmin.get("/api/notices").json()["notices"][0]
    assert notice["id"] == "log-errors" and notice["tone"] == "error" and notice["pane"] == "server"
    assert notice["fingerprint"].endswith(f":{logbuf.last_seq('error')}")
    nadmin.post("/api/notices/log-errors/seen", json={"fingerprint": notice["fingerprint"]})
    assert _ids(nadmin) == []
    logbuf.log.warning("[test] a warning is not a notice")
    assert _ids(nadmin) == []
    logbuf.log.error("[test] and again")
    assert _ids(nadmin) == ["log-errors"]


def test_strongest_first_and_bad_acks(nadmin, monkeypatch):
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "9.0.0", "url": "", "published_at": ""})
    logbuf.log.error("[test] boom")
    assert _ids(nadmin) == ["log-errors", "update"]
    assert nadmin.post("/api/notices/Bad Id/seen", json={"fingerprint": "x"}).status_code == 400
    assert nadmin.post("/api/notices/update/seen", json={"fingerprint": "x" * 300}).status_code == 400
    assert nadmin.post("/api/notices/update/seen", json={}).status_code == 422
    for n in nadmin.get("/api/notices").json()["notices"]:
        nadmin.post(f"/api/notices/{n['id']}/seen", json={"fingerprint": n["fingerprint"]})
    assert _ids(nadmin) == []
    assert set(notices.seen_map("nadmin")) == {"update", "log-errors"}


# --- the account sources (their helpers stubbed: each is a plain read) ------

def _only(client, notice_id):
    found = [n for n in client.get("/api/notices").json()["notices"] if n["id"] == notice_id]
    return found[0] if found else None


def test_backup_failed_until_seen_and_again_on_the_next_failure(nuser, monkeypatch):
    tasks = [{"id": "a" * 32, "name": "Nightly", "state": "finished", "last_run": "2026-09-20T01:00:00"}]
    monkeypatch.setattr(notices.backup_schedule, "list_tasks", lambda owner: tasks if owner == "nuser" else [])
    assert _only(nuser, "backup-failed") is None
    tasks[0].update(state="failed", last_run="2026-09-21T01:00:00", last_error="disk full")
    notice = _only(nuser, "backup-failed")
    assert notice["tone"] == "error" and notice["pane"] == "backups" and 'task "Nightly" failed' in notice["title"]
    nuser.post("/api/notices/backup-failed/seen", json={"fingerprint": notice["fingerprint"]})
    assert _only(nuser, "backup-failed") is None
    tasks[0]["last_run"] = "2026-09-22T01:00:00"  # failed again
    assert _only(nuser, "backup-failed")["fingerprint"] != notice["fingerprint"]
    tasks.append({"id": "b" * 32, "name": "Weekly", "state": "failed", "last_run": "2026-09-22T02:00:00"})
    assert _only(nuser, "backup-failed")["title"] == "2 backup tasks failed"


def test_mirror_conflicts_count_new_ones_only(nuser, monkeypatch):
    marks = {"ws-clone": (0, 0)}
    monkeypatch.setattr(notices.sync_engine, "list_mirrors", lambda owner: [{"workspace_id": "ws-clone"}] if owner == "nuser" else [])
    monkeypatch.setattr(notices.sync_engine, "open_conflict_mark", lambda ws: marks[ws])
    assert _only(nuser, "mirror-conflicts") is None
    marks["ws-clone"] = (3, 7)
    notice = _only(nuser, "mirror-conflicts")
    assert notice["pane"] == "account" and notice["tone"] == "warn" and notice["title"].startswith("3 sync conflicts")
    nuser.post("/api/notices/mirror-conflicts/seen", json={"fingerprint": notice["fingerprint"]})
    marks["ws-clone"] = (3, 7)
    assert _only(nuser, "mirror-conflicts") is None
    marks["ws-clone"] = (2, 8)  # one resolved, one new
    assert _only(nuser, "mirror-conflicts")["title"].startswith("2 sync conflicts")


def test_publication_conflicts_point_at_the_sync_pane(nuser, monkeypatch):
    mirrors = [{"workspace_id": "ws-clone"}, {"workspace_id": "ws-pub", "page_filter": ["p1"]}]
    marks = {"ws-clone": (0, 0), "ws-pub": (2, 5)}
    monkeypatch.setattr(notices.sync_engine, "list_mirrors", lambda owner: mirrors if owner == "nuser" else [])
    monkeypatch.setattr(notices.sync_engine, "open_conflict_mark", lambda ws: marks[ws])
    assert _only(nuser, "mirror-conflicts") is None
    notice = _only(nuser, "publish-conflicts")
    assert notice["pane"] == "account" and notice["title"].startswith("2 sync conflicts")
    marks["ws-clone"] = (1, 9)
    assert _only(nuser, "mirror-conflicts")["title"].startswith("1 sync conflict ")


def test_cloud_sync_error_names_the_reason(nuser, monkeypatch):
    status = {"state": "off", "at": "", "error": ""}
    monkeypatch.setattr(notices.cloud_sync, "profile_status", lambda username: status)
    assert _only(nuser, "cloud-sync") is None
    status.update(state="error", at="2026-09-24T10:00:00Z", error="Gamma Cloud could not be reached.")
    notice = _only(nuser, "cloud-sync")
    assert notice["pane"] == "account" and notice["title"] == "Gamma Cloud sync failed: Gamma Cloud could not be reached"
    assert notice["fingerprint"] == "2026-09-24T10:00:00Z"
    status.update(state="synced")
    assert _only(nuser, "cloud-sync") is None


def test_storage_thresholds_and_the_remembered_walk(nuser, monkeypatch):
    limits = {"quota_mb": 0}
    walks = []

    def usage(username):
        walks.append(username)
        return used[0]
    used = [0]
    monkeypatch.setattr(notices.server_settings, "user_limits", lambda username: dict(limits))
    monkeypatch.setattr(notices.server_settings, "usage_bytes", usage)
    notices.forget_usage()
    assert _only(nuser, "storage") is None and walks == []  # no quota: no walk at all
    limits["quota_mb"] = 100
    used[0] = 50 * notices.MB
    assert _only(nuser, "storage") is None and walks == ["nuser"]
    nuser.get("/api/notices")
    assert walks == ["nuser"]  # remembered
    notices.forget_usage("nuser")
    used[0] = 95 * notices.MB
    notice = _only(nuser, "storage")
    assert notice["tone"] == "warn" and notice["fingerprint"] == "90" and "95 of 100 MB" in notice["title"]
    nuser.post("/api/notices/storage/seen", json={"fingerprint": "90"})
    assert _only(nuser, "storage") is None
    notices.forget_usage()
    used[0] = 100 * notices.MB
    notice = _only(nuser, "storage")
    assert notice["tone"] == "error" and notice["fingerprint"] == "full"


def test_many_clones_with_conflicts_still_fit_one_fingerprint(nuser, monkeypatch):
    mirrors = [{"workspace_id": f"ws-clone-{i:02d}"} for i in range(15)]
    monkeypatch.setattr(notices.sync_engine, "list_mirrors", lambda owner: mirrors if owner == "nuser" else [])
    monkeypatch.setattr(notices.sync_engine, "open_conflict_mark", lambda ws: (12, 345))
    notice = _only(nuser, "mirror-conflicts")
    assert notice["title"].startswith("180 sync conflicts") and len(notice["fingerprint"]) <= 32
    assert nuser.post("/api/notices/mirror-conflicts/seen", json={"fingerprint": notice["fingerprint"]}).status_code == 200
    assert _only(nuser, "mirror-conflicts") is None
