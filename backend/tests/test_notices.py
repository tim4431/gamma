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
