"""The Settings → Server dashboard endpoint: admin-only, the build stamp,
log counts, and the GitHub release check (stubbed — tests never reach the
network)."""

import pytest

from conftest import drop_user, login as _login, make_user as _make_user
from gamma import version


@pytest.fixture(scope="module")
def infoadmin(client):
    _make_user("infoadmin", "infoadminpw", is_admin=1)
    yield _login("infoadmin", "infoadminpw")
    drop_user("infoadmin")


@pytest.fixture(scope="module")
def infouser(client):
    _make_user("infouser", "infouserpw", is_admin=0)
    yield _login("infouser", "infouserpw")
    drop_user("infouser")


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(version, "_cache", {})


def test_parse_version():
    assert version.parse_version("v1.2.3") == (1, 2, 3, 0)
    assert version.parse_version("0.2") == (0, 2, 0, 0)
    assert version.parse_version("0.2.9-dev.12") == (0, 2, 9, 12)
    assert version.parse_version("0.2.9") < version.parse_version("0.2.9-dev.1") < version.parse_version("0.2.10")
    assert version.parse_version("0.2.9-rc.1") is None
    assert version.parse_version("extension-v0.2.0") is None
    assert version.parse_version("") is None


def test_server_info_requires_admin(anon, infouser):
    assert anon.get("/api/admin/server-info").status_code == 401
    assert infouser.get("/api/admin/server-info").status_code == 403


def test_server_info_reports_the_build_and_a_newer_release(infoadmin, monkeypatch):
    monkeypatch.setattr(version, "VERSION", "0.2.1")
    monkeypatch.setattr(version, "COMMIT", "abc1234")
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.3.0", "url": "https://example/rel", "published_at": "2026-09-01T00:00:00Z"})
    from gamma.logbuf import log
    log.warning("[test] a warning for the dashboard")
    r = infoadmin.get("/api/admin/server-info")
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["version"] == "0.2.1" and info["commit"] == "abc1234" and info["label"] == "v0.2.1 (abc1234)"
    assert info["latest"]["version"] == "0.3.0" and info["update_available"] is True and info["latest_error"] == ""
    assert info["uptime_seconds"] >= 0 and info["started_at"].endswith("Z")
    assert info["log_counts"]["warning"] >= 1 and set(info["log_counts"]) == {"info", "warning", "error"}
    assert info["schema_version"] >= 7 and info["image"].startswith("ghcr.io/")
    # same or older release: no update
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.2.1", "url": "", "published_at": ""})
    assert infoadmin.get("/api/admin/server-info", params={"refresh": 1}).json()["update_available"] is False


def test_server_info_without_a_version_or_network(infoadmin, monkeypatch):
    monkeypatch.setattr(version, "VERSION", "")
    monkeypatch.setattr(version, "COMMIT", "")
    calls = []

    def boom():
        calls.append(1)
        raise OSError("no route to github")
    monkeypatch.setattr(version, "_fetch_latest", boom)
    info = infoadmin.get("/api/admin/server-info").json()
    assert info["label"] == "development build" and info["latest"] is None
    assert "no route to github" in info["latest_error"] and info["update_available"] is None
    # a failed check is cached: the next read does not retry until refresh
    infoadmin.get("/api/admin/server-info")
    assert len(calls) == 1
    infoadmin.get("/api/admin/server-info", params={"refresh": 1})
    assert len(calls) == 2
    # a versioned build with no reachable release: known version, unknown update state
    monkeypatch.setattr(version, "VERSION", "1.0.0")
    info = infoadmin.get("/api/admin/server-info", params={"refresh": 1}).json()
    assert info["label"] == "v1.0.0" and info["update_available"] is None


def test_a_dev_build_is_compared_with_its_branch(infoadmin, monkeypatch):
    monkeypatch.setattr(version, "VERSION", "0.2.9-dev.12")
    monkeypatch.setattr(version, "COMMIT", "4bd648ce474a")
    monkeypatch.setattr(version, "BRANCH", "main")
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.2.9", "url": "https://example/rel", "published_at": ""})
    urls = []

    def compare(ahead, behind):
        def get(url):
            urls.append(url)
            return {"ahead_by": ahead, "behind_by": behind, "html_url": "https://example/compare"}
        return get
    # main has three commits this build lacks (and one of its own, from a branch build)
    monkeypatch.setattr(version, "_get_json", compare(3, 1))
    info = infoadmin.get("/api/admin/server-info").json()
    assert urls == [f"{version.COMPARE_API}/4bd648ce474a...main?per_page=1&page=2"]
    assert info["branch"] == "main" and info["label"] == "v0.2.9-dev.12 (4bd648ce474a)"
    assert info["latest_build"] == {"version": "0.2.9-dev.14", "branch": "main", "ahead_by": 3, "url": "https://example/compare"}
    assert info["update"] == {"kind": "build", "version": "0.2.9-dev.14", "url": "https://example/compare"}
    assert info["update_available"] is True
    # the branch has nothing new: up to date, although the release is older
    monkeypatch.setattr(version, "_get_json", compare(0, 0))
    info = infoadmin.get("/api/admin/server-info", params={"refresh": 1}).json()
    assert info["update"] is None and info["update_available"] is False
    # a newer release wins over the branch
    monkeypatch.setattr(version, "_get_json", compare(5, 0))
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.3.0", "url": "https://example/rel", "published_at": ""})
    info = infoadmin.get("/api/admin/server-info", params={"refresh": 1}).json()
    assert info["update"] == {"kind": "release", "version": "0.3.0", "url": "https://example/rel"}


def test_a_release_build_never_asks_about_a_branch(infoadmin, monkeypatch):
    monkeypatch.setattr(version, "VERSION", "0.2.9")
    monkeypatch.setattr(version, "COMMIT", "4bd648ce474a")
    monkeypatch.setattr(version, "BRANCH", "main")
    monkeypatch.setattr(version, "_fetch_latest", lambda: {"version": "0.2.9", "url": "", "published_at": ""})
    monkeypatch.setattr(version, "_get_json", lambda url: pytest.fail(f"asked {url}"))
    info = infoadmin.get("/api/admin/server-info").json()
    assert info["latest_build"] is None and info["update_available"] is False


# --- the stored files and the databases' copies (read-only, set by the environment) ----

@pytest.fixture
def store():
    """``use(store)`` makes ``store`` the active blob driver for the test;
    the one before comes back after it. The copies' last round starts
    unrecorded."""
    from gamma import blobs, db_copies

    before = blobs.driver()
    last = db_copies._last_round
    db_copies._last_round = None
    yield blobs.use
    blobs.use(before)
    db_copies._last_round = last


def test_server_info_reports_local_storage_and_the_copies_off(infoadmin, infouser, store, monkeypatch):
    from gamma import blobs, config

    monkeypatch.delenv("GAMMA_DB_COPIES", raising=False)
    monkeypatch.delenv("GAMMA_BLOBS", raising=False)
    store(blobs.LocalBlobs())
    info = infoadmin.get("/api/admin/server-info").json()
    assert info["storage"] == {"kind": "local", "presign": False,
                               "where": str((config.DATA_DIR / "workspaces/<id>/uploads/").absolute())}
    assert info["db_copies"] == {"enabled": False}
    assert infouser.get("/api/admin/server-info").status_code == 403


def test_server_info_reports_a_bucket_its_cache_and_the_copies(infoadmin, infouser, store, s3_bucket, tmp_path,
                                                               monkeypatch):
    from gamma import blobs, db_copies
    from conftest import S3_TEST_BUCKET

    monkeypatch.setenv("GAMMA_DB_COPIES", "1")
    monkeypatch.setenv("GAMMA_DB_COPIES_INTERVAL", "900")
    monkeypatch.setenv("GAMMA_DB_COPIES_KEEP", "3")
    bucket = blobs.S3Blobs(S3_TEST_BUCKET, region="us-east-1", access_key="testing", secret_key="testing",
                           prefix="prod", cache_dir=tmp_path / "cache", cache_bytes=10_000, presign=False)
    bucket.put("ws-info", "a.bin", b"x" * 1234)  # stored, and its bytes left in the node's cache
    store(bucket)
    info = infoadmin.get("/api/admin/server-info").json()
    assert info["storage"] == {"kind": "s3", "where": f"s3://{S3_TEST_BUCKET}/prod/", "presign": False,
                               "cache": {"bytes": 1234, "cap": 10_000}}
    # on, but no round has finished in this process yet
    assert info["db_copies"] == {"enabled": True, "interval_s": 900, "keep": 3,
                                 "last_round_at": None, "copied": None, "failed": None}
    monkeypatch.setattr(db_copies, "_last_round", {"stamp": "20261003T140000Z", "copied": 2, "failed": 1})
    info = infoadmin.get("/api/admin/server-info").json()
    assert info["db_copies"] == {"enabled": True, "interval_s": 900, "keep": 3,
                                 "last_round_at": "2026-10-03T14:00:00Z", "copied": 2, "failed": 1}
    r = infouser.get("/api/admin/server-info")
    assert r.status_code == 403 and "storage" not in r.text


def test_a_round_is_what_the_copies_status_reports(data_dir, store, monkeypatch):
    """The status opens no database: a round records how it went, and the
    status reads that back (and the environment)."""
    import sqlite3
    from contextlib import closing
    from unittest import mock

    from gamma import blobs, db_copies

    monkeypatch.setenv("GAMMA_DB_COPIES", "1")
    monkeypatch.delenv("GAMMA_DB_COPIES_INTERVAL", raising=False)
    monkeypatch.delenv("GAMMA_DB_COPIES_KEEP", raising=False)
    monkeypatch.setattr(db_copies, "_stamp", lambda: "20261003T150000Z")
    store(blobs.LocalBlobs())  # its objects under data_dir, the test's own
    with closing(sqlite3.connect(data_dir / "users.db")) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
    with mock.patch("sqlite3.connect", side_effect=AssertionError("the status opened a database")):
        assert db_copies.status()["last_round_at"] is None
    assert db_copies.tick()["copied"] == ["users.db"]
    with mock.patch("sqlite3.connect", side_effect=AssertionError("the status opened a database")):
        assert db_copies.status() == {"enabled": True, "interval_s": 3600, "keep": 7,
                                      "last_round_at": "2026-10-03T15:00:00Z", "copied": 1, "failed": 0}
