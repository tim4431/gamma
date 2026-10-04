"""The off-site copies (gamma/offsite.py, gamma/s3.py, docs/dev/debugging.md
"Off-site copies in a bucket") against moto's in-process bucket (skipped
without moto): a round copying the databases that changed and the uploads
the bucket lacks, a second round copying nothing, the pruning to the keep
count (never past the data directory's first round), a copy or an upload
that fails, the restore of databases and uploads and its refusals, the
status and Copy now, the settings saved with the secret key encrypted and
the environment's override, the admin endpoints, and the Litestream
configuration. manage.py's ``offsite`` command is in test_manage_cli.py."""

import calendar
import itertools
import json
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from unittest import mock

import pytest

from conftest import S3_TEST_BUCKET as BUCKET, drop_user, login, make_user
from gamma import db, integrity, offsite, s3, storage
from gamma.logbuf import tail

BUCKET_ENV = {"GAMMA_S3_BUCKET": BUCKET, "GAMMA_S3_REGION": "us-east-1", "GAMMA_S3_ACCESS_KEY": "testing",
              "GAMMA_S3_SECRET_KEY": "testing", "GAMMA_S3_PREFIX": "tenant-1"}
ENV_VARS = (*BUCKET_ENV, "GAMMA_S3_ENDPOINT", "GAMMA_OFFSITE", "GAMMA_OFFSITE_INTERVAL", "GAMMA_OFFSITE_KEEP")


def _make_db(path: Path, rows=("first",)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS notes (text TEXT)")
        conn.executemany("INSERT INTO notes VALUES (?)", [(r,) for r in rows])
        conn.commit()


def _rows(path: Path) -> list[str]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        return [r[0] for r in conn.execute("SELECT text FROM notes ORDER BY rowid")]


def _upload(root: Path, ws: str, data: bytes, ext: str = ".bin") -> str:
    """A stored file in the workspace's uploads/, named by its content."""
    name = storage.content_digest(data) + ext
    folder = root / "workspaces" / ws / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return name


def _last_seq():
    seen = tail(0)
    return seen[-1]["seq"] if seen else 0


def _lines(seq):
    return [e for e in tail(seq) if e["msg"].startswith("[offsite]")]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """No off-site variables from the outside, and no round or schedule
    this process remembers."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(offsite, "_last_round", None)
    monkeypatch.setattr(offsite, "_next_round_at", None)


@pytest.fixture
def data(data_dir, s3_bucket, monkeypatch):
    """conftest's data directory of the test's own, holding users.db, two
    workspaces' databases and their uploads (and a write in progress in
    ws-a's ``.partial/``), the copies on through the environment, to moto's
    bucket under ``tenant-1/``, rounds stamped a second apart. ``.raw`` is
    the bucket's own client, ``.keys(prefix)`` its keys under the prefix."""
    for var, value in BUCKET_ENV.items():
        monkeypatch.setenv(var, value)
    clock = itertools.count(1)
    monkeypatch.setattr(offsite, "_stamp", lambda: f"20261003T10{next(clock):04d}Z")
    _make_db(data_dir / "users.db", ["alice"])
    for ws in ("ws-a", "ws-b"):
        _make_db(data_dir / "workspaces" / ws / "pages.db", [f"{ws} page"])
        _make_db(data_dir / "workspaces" / ws / "data.db", [f"{ws} data"])
    files = {"ws-a": [_upload(data_dir, "ws-a", b"paper one" * 100, ".pdf"), _upload(data_dir, "ws-a", b"image")],
             "ws-b": [_upload(data_dir, "ws-b", b"paper two" * 100, ".pdf")]}
    partial = data_dir / "workspaces" / "ws-a" / "uploads" / ".partial"
    partial.mkdir()
    (partial / "half-written").write_bytes(b"x")

    class Data:
        root, raw, uploads = data_dir, s3_bucket, files

        @staticmethod
        def keys(prefix=""):
            listed = s3_bucket.list_objects_v2(Bucket=BUCKET, Prefix="tenant-1/" + prefix)
            return sorted(o["Key"][len("tenant-1/"):] for o in listed.get("Contents", ()))

        @staticmethod
        def body(key):
            return s3_bucket.get_object(Bucket=BUCKET, Key="tenant-1/" + key)["Body"].read()

    return Data


def _counting(monkeypatch, name):
    """Count the calls of ``s3.Client.<name>`` (their first argument)."""
    seen, real = [], getattr(s3.Client, name)

    def counted(self, first, *args, **kwargs):
        seen.append(first)
        return real(self, first, *args, **kwargs)
    monkeypatch.setattr(s3.Client, name, counted)
    return seen


# --- a round -------------------------------------------------------------------------

def test_a_round_copies_changed_databases_and_missing_uploads(data, monkeypatch):
    root = data.root
    there = data.uploads["ws-b"][0]  # in the bucket already, at its size: not sent again
    data.raw.put_object(Bucket=BUCKET, Key=f"tenant-1/uploads/ws-b/{there}", Body=b"paper two" * 100)
    seq = _last_seq()
    first = offsite.tick()
    assert sorted(first["copied"]) == ["users.db", "ws-a/data.db", "ws-a/pages.db", "ws-b/data.db", "ws-b/pages.db"]
    assert first["uploads_copied"] == 2 and first["failed"] == {} and first["unchanged"] == 0
    assert data.keys("offsite/") == [
        "offsite/users/20261003T100001Z.db", "offsite/ws-a/20261003T100001Z-data.db",
        "offsite/ws-a/20261003T100001Z-pages.db", "offsite/ws-b/20261003T100001Z-data.db",
        "offsite/ws-b/20261003T100001Z-pages.db"]
    assert data.keys("uploads/") == sorted(f"uploads/{ws}/{n}" for ws, names in data.uploads.items() for n in names)
    assert data.body(f"uploads/ws-a/{data.uploads['ws-a'][0]}") == b"paper one" * 100
    got = root / "got.db"
    got.write_bytes(data.body("offsite/ws-a/20261003T100001Z-pages.db"))
    assert _rows(got) == ["ws-a page"]
    assert not (root / "backups" / ".offsite").exists()  # the temp copies are gone
    line, = _lines(seq)
    assert "copied" in line["msg"] and "2 upload(s)" in line["msg"] and line["level"] == "INFO"

    # nothing changed: nothing copied, no workspace's objects listed, nothing said
    seq, listed = _last_seq(), _counting(monkeypatch, "list_objects")
    assert offsite.tick() == {"copied": [], "uploads_copied": 0, "failed": {}, "unchanged": 5, "pruned": 0}
    assert listed == [] and _lines(seq) == []

    # a new file in ws-a: its objects listed once, that file sent; ws-b left alone
    added = _upload(root, "ws-a", b"a new figure")
    done = offsite.tick()
    assert done["uploads_copied"] == 1 and done["copied"] == [] and listed == ["uploads/ws-a/"]
    assert f"uploads/ws-a/{added}" in data.keys("uploads/ws-a/")
    # a deleted file stays in the bucket
    (root / "workspaces" / "ws-a" / "uploads" / added).unlink()
    assert offsite.tick()["uploads_copied"] == 0 and f"uploads/ws-a/{added}" in data.keys("uploads/ws-a/")

    # a server writes ws-a's pages and users.db and keeps its connection open
    held = sqlite3.connect(root / "workspaces" / "ws-a" / "pages.db")
    try:
        held.execute("INSERT INTO notes VALUES ('second')")
        held.commit()
        _make_db(root / "users.db", ["bob"])
        done = offsite.tick()
        assert sorted(done["copied"]) == ["users.db", "ws-a/pages.db"] and done["unchanged"] == 3
        stamp = sorted(data.keys("offsite/ws-a/"))[-1]
        got.write_bytes(data.body(stamp))
        assert _rows(got) == ["ws-a page", "second"]
    finally:
        held.close()  # the last connection: SQLite folds the WAL into the file, which is no change
    assert offsite.tick()["copied"] == []
    assert {label: [s for s, _ in gens] for label, gens in offsite.listing("ws-a").items()} == {
        "ws-a/data.db": ["20261003T100001Z"], "ws-a/pages.db": ["20261003T100001Z", "20261003T100005Z"]}


def test_the_copies_off_do_nothing(data, monkeypatch):
    monkeypatch.setenv("GAMMA_OFFSITE", "off")
    assert offsite.tick() == {"copied": [], "uploads_copied": 0, "failed": {}, "unchanged": 0, "pruned": 0}
    assert data.keys() == [] and not (data.root / "backups").exists()
    assert offsite.status(offsite.settings())["enabled"] is False and offsite.run_now() == {
        "started": False, "message": "off-site copies are off"}


def test_copies_are_pruned_to_the_keep_count_never_past_the_first_round(data, monkeypatch):
    root = data.root
    monkeypatch.setenv("GAMMA_OFFSITE_KEEP", "3")
    older = root / "older.db"
    _make_db(older)
    data.raw.upload_file(str(older), BUCKET, "tenant-1/offsite/users/20200101T000000Z.db")  # another directory's
    for n in range(5):
        _make_db(root / "users.db", [f"change {n}"])
        done = offsite.tick()
    assert done["pruned"] == 1 and done["copied"] == ["users.db"]
    assert [s for s, _ in offsite.listing("users")["users.db"]] == [
        "20200101T000000Z", "20261003T100003Z", "20261003T100004Z", "20261003T100005Z"]
    assert len(offsite.listing("ws-a")["ws-a/pages.db"]) == 1  # unchanged: one copy

    # copies from both sides of this directory's first round: which one is meant must be said
    with pytest.raises(offsite.RestoreError, match="--at 20200101T000000Z for the newest from before"):
        offsite.restore("users")
    offsite.restore("users", at="20200101T000000Z")
    assert _rows(root / "users.db") == ["first"]
    # a restore makes the copies this data directory's: the next round prunes the old one too
    _make_db(root / "users.db", ["after the restore"])
    offsite.tick()
    assert [s for s, _ in offsite.listing("users")["users.db"]] == [
        "20261003T100004Z", "20261003T100005Z", "20261003T100007Z"]


def test_a_copy_that_fails_its_check_and_uploads_that_fail_are_tried_again(data, monkeypatch):
    root = data.root
    real_snapshot, real_put = offsite.snapshot_db, s3.Client.put_object
    damaged = str(root / "workspaces" / "ws-a" / "pages.db")

    def snapshot(src, dst):
        result = real_snapshot(src, dst)
        return "*** in database main ***\nPage 3 is never used" if str(src) == damaged else result

    def put(self, key, path):
        if key.startswith(("offsite/ws-b/", "uploads/ws-b/")):
            raise s3.S3Error("PUT refused: the bucket is out of reach")
        real_put(self, key, path)

    monkeypatch.setattr(offsite, "snapshot_db", snapshot)
    monkeypatch.setattr(s3.Client, "put_object", put)
    seq = _last_seq()
    done = offsite.tick()
    assert sorted(done["copied"]) == ["users.db", "ws-a/data.db"] and done["uploads_copied"] == 2
    assert "Page 3 is never used" in done["failed"]["ws-a/pages.db"]
    assert "out of reach" in done["failed"]["ws-b/pages.db"] and "ws-b/data.db" in done["failed"]
    assert "out of reach" in done["failed"]["ws-b/uploads"]
    assert data.keys("offsite/") == ["offsite/users/20261003T100001Z.db", "offsite/ws-a/20261003T100001Z-data.db"]
    assert data.keys("uploads/ws-b/") == []
    warned = [e for e in tail(seq) if e["level"] == "WARNING"]
    assert any("workspaces/ws-a/pages.db failed its check" in e["msg"] for e in warned)  # gamma/integrity.py
    round_line, = _lines(seq)
    assert round_line["level"] == "WARNING" and "failed: " in round_line["msg"]
    assert "workspaces/ws-a/pages.db" in integrity._read()  # the admins' db-damage notice
    shown = offsite.status(offsite.settings())
    assert shown["copied"] == 2 and shown["uploads_copied"] == 2 and shown["failed"] == 4
    assert shown["error"].startswith("ws-a/pages.db: the copy failed its check")

    monkeypatch.setattr(offsite, "snapshot_db", real_snapshot)
    monkeypatch.setattr(s3.Client, "put_object", real_put)
    done = offsite.tick()
    assert sorted(done["copied"]) == ["ws-a/pages.db", "ws-b/data.db", "ws-b/pages.db"]
    assert done["uploads_copied"] == 1 and done["failed"] == {}  # ws-b's file, its directory untouched since
    assert "workspaces/ws-a/pages.db" not in integrity._read()
    assert offsite.status(offsite.settings())["error"] is None


def test_a_bucket_that_cannot_be_used_is_the_rounds_error(data, monkeypatch):
    monkeypatch.setenv("GAMMA_S3_SECRET_KEY", "")  # half a key pair
    seq = _last_seq()
    done = offsite.tick()
    assert "both the access key and the secret key" in done["error"] and done["copied"] == []
    assert any("no round" in e["msg"] for e in _lines(seq))
    shown = offsite.status(offsite.settings())
    assert shown["last_round_at"] == "2026-10-03T10:00:01Z" and "both the access key" in shown["error"]
    assert not (data.root / "backups" / offsite.STATE_FILE).exists()


# --- status and Copy now -------------------------------------------------------------

def test_the_status_opens_no_database_and_survives_a_restart(data, monkeypatch):
    conf = offsite.settings()
    with mock.patch("sqlite3.connect", side_effect=AssertionError("the status opened a database")):
        assert offsite.status(conf) == {
            "enabled": True, "running": False, "interval_s": 3600, "keep": 7, "last_round_at": None,
            "copied": None, "uploads_copied": None, "failed": None, "error": None, "next_round_at": None}
    offsite.tick()
    before = time.time()
    assert offsite.wait_s() == 3600  # what the every() loop waits after the round
    with mock.patch("sqlite3.connect", side_effect=AssertionError("the status opened a database")):
        shown = offsite.status(conf)
    assert {k: shown[k] for k in ("last_round_at", "copied", "uploads_copied", "failed", "error")} == {
        "last_round_at": "2026-10-03T10:00:01Z", "copied": 5, "uploads_copied": 3, "failed": 0, "error": None}
    next_at = calendar.timegm(time.strptime(shown["next_round_at"], "%Y-%m-%dT%H:%M:%SZ"))
    assert before + 3590 < next_at < time.time() + 3610
    # a restart: the state file still says how the last round went
    monkeypatch.setattr(offsite, "_last_round", None)
    assert offsite.status(conf)["last_round_at"] == "2026-10-03T10:00:01Z" and offsite.status(conf)["copied"] == 5
    # another bucket: that round was not its
    monkeypatch.setenv("GAMMA_S3_PREFIX", "tenant-2")
    assert offsite.status(offsite.settings())["last_round_at"] is None


def test_copy_now_runs_one_round_at_a_time(data, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_round = offsite._round

    def slow_round():
        entered.set()
        release.wait(10)
        return real_round()

    monkeypatch.setattr(offsite, "_round", slow_round)
    assert offsite.run_now() == {"started": True}
    assert entered.wait(10)
    assert offsite.status(offsite.settings())["running"] is True
    assert offsite.run_now() == {"started": False, "message": "a round is running"}
    assert offsite.tick()["skipped"] == "a round is running"  # the scheduled round passes this one by
    release.set()
    for _ in range(200):
        if not offsite.status(offsite.settings())["running"]:
            break
        time.sleep(0.05)
    shown = offsite.status(offsite.settings())
    assert shown["running"] is False and shown["copied"] == 5


# --- restoring -----------------------------------------------------------------------

def test_a_restore_puts_the_copies_back_and_moves_the_files_aside(data):
    root = data.root
    pages = root / "workspaces" / "ws-a" / "pages.db"
    offsite.tick()
    _make_db(pages, ["later"])
    _make_db(root / "workspaces" / "ws-a" / "data.db", ["later data"])
    offsite.tick()  # stamp 2: both of ws-a's changed
    _make_db(pages, ["latest"])
    offsite.tick()  # stamp 3: its pages.db only

    done = offsite.restore("ws-a", at="20261003T100002Z")
    assert [(r["label"], r["stamp"]) for r in done] == [("ws-a/data.db", "20261003T100002Z"),
                                                        ("ws-a/pages.db", "20261003T100002Z")]
    assert not Path(f"{pages}-shm").exists() and not pages.with_name(".pages.db.restoring").exists()
    assert _rows(pages) == ["ws-a page", "later"]
    aside = pages.with_name(done[1]["aside"])
    assert aside.name.startswith("pages.db.pre-restore-") and _rows(aside) == ["ws-a page", "later", "latest"]

    offsite.restore("ws-a")  # the newest of each: pages from round 3, data from round 2
    assert _rows(pages) == ["ws-a page", "later", "latest"]
    assert _rows(root / "workspaces" / "ws-a" / "data.db") == ["ws-a data", "later data"]

    with pytest.raises(offsite.RestoreError, match="holds no copy of ws-a from 20200101T000000Z"):
        offsite.restore("ws-a", at="20200101T000000Z")
    with pytest.raises(offsite.RestoreError, match="--at takes a stamp"):
        offsite.restore("ws-a", at="yesterday")
    with pytest.raises(offsite.RestoreError, match="no workspace id"):
        offsite.restore("../users.db")


def test_a_restore_refuses_a_database_in_use_and_a_copy_that_fails_its_check(data, monkeypatch):
    root = data.root
    pages = root / "workspaces" / "ws-a" / "pages.db"
    offsite.tick()
    _make_db(pages, ["unsaved elsewhere"])
    lost = data.uploads["ws-a"][0]
    (root / "workspaces" / "ws-a" / "uploads" / lost).unlink()
    before = sorted(p.name for p in pages.parent.iterdir())
    for held_file in (pages, root / "users.db"):  # users.db open: the server is running
        with closing(sqlite3.connect(held_file)) as held:
            held.execute("SELECT count(*) FROM notes").fetchone()
            with pytest.raises(offsite.RestoreError, match="in use"):
                offsite.restore("ws-a", uploads=True)
    monkeypatch.setattr(integrity, "quick_check", lambda path: "row 1 missing from index")
    with pytest.raises(offsite.RestoreError, match="failed its check"):
        offsite.restore("ws-a", uploads=True)
    assert sorted(p.name for p in pages.parent.iterdir()) == before  # nothing moved, no download left
    assert _rows(pages) == ["ws-a page", "unsaved elsewhere"]
    assert not (root / "workspaces" / "ws-a" / "uploads" / lost).exists()  # refused before the files came


def test_a_lost_disk_comes_back_whole_with_its_uploads(data):
    """Everything restored onto an empty data directory: users.db and every
    workspace the bucket holds copies of, with their files."""
    root = data.root
    offsite.tick()
    data.raw.put_object(Bucket=BUCKET, Key="tenant-1/uploads/ws-a/.hidden", Body=b"x")  # no stored file's name
    data.raw.put_object(Bucket=BUCKET, Key="tenant-1/uploads/ws-a/odd/nested.bin", Body=b"x")
    shutil.rmtree(root / "workspaces")
    for side in ("", "-wal", "-shm"):
        Path(f"{root / 'users.db'}{side}").unlink(missing_ok=True)
    done = offsite.restore("all", uploads=True)
    assert [r["label"] for r in done] == ["users.db", "ws-a/data.db", "ws-a/pages.db", "ws-b/data.db",
                                          "ws-b/pages.db", "ws-a/uploads", "ws-b/uploads"]
    assert all(r["aside"] == "" for r in done if "aside" in r)
    assert [(r["files"], r["bytes"] > 0) for r in done if "files" in r] == [(2, True), (1, True)]
    assert _rows(root / "users.db") == ["alice"] and _rows(root / "workspaces" / "ws-b" / "pages.db") == ["ws-b page"]
    for ws, names in data.uploads.items():
        stored = [p.name for p in (root / "workspaces" / ws / "uploads").iterdir() if not p.name.startswith(".")]
        assert sorted(stored) == sorted(names)  # beside the store's own .partial/
    assert (root / "workspaces" / "ws-a" / "uploads" / data.uploads["ws-a"][0]).read_bytes() == b"paper one" * 100
    # again, after a server ran: the databases come back, the files there already stay
    done = offsite.restore("ws-a", uploads=True)
    assert done[-1] == {"label": "ws-a/uploads", "files": 0, "bytes": 0}


# --- the settings --------------------------------------------------------------------

def _stored(root: Path) -> dict:
    with closing(sqlite3.connect(root / "users.db")) as conn:
        return json.loads(conn.execute("SELECT value FROM settings WHERE key = 'offsite'").fetchone()[0])


@pytest.fixture
def users_db(data_dir):
    """A real users.db in the test's own data directory (the settings table
    the saved settings live in); its cached connections closed after."""
    db.connect_users_db().close()
    yield data_dir
    db.close_connections()


def test_the_settings_round_trip_with_the_secret_encrypted(users_db):
    assert offsite.settings() == {"enabled": False, "bucket": "", "endpoint": "", "region": "", "access_key": "",
                                  "secret_key": "", "prefix": "", "interval_s": 3600, "keep": 7, "from_env": False}
    conf = offsite.save({"enabled": True, "bucket": "gamma-backups", "endpoint": "https://acct.r2.example.com/",
                         "region": "auto", "access_key": "AK1", "secret_key": "the-secret-1", "prefix": "/tenant-1/",
                         "interval_s": 900, "keep": 3})
    assert conf == {"enabled": True, "bucket": "gamma-backups", "endpoint": "https://acct.r2.example.com",
                    "region": "auto", "access_key": "AK1", "secret_key": "the-secret-1", "prefix": "tenant-1",
                    "interval_s": 900, "keep": 3, "from_env": False}
    stored = _stored(users_db)
    assert stored["secret_key"] and "the-secret-1" not in json.dumps(stored)  # encrypted at rest
    assert "secret_key" not in offsite.public(conf) and offsite.public(conf)["secret_set"] is True

    # a save without the secret (or with "") keeps it; one field changes alone
    assert offsite.save({"keep": 5, "secret_key": ""})["secret_key"] == "the-secret-1"
    assert offsite.settings()["keep"] == 5 and offsite.settings()["interval_s"] == 900
    assert offsite.save({"secret_key": "the-secret-2"})["secret_key"] == "the-secret-2"
    # the access key cleared: the secret goes with it (boto3's own credentials)
    conf = offsite.save({"access_key": ""})
    assert conf["access_key"] == "" and conf["secret_key"] == "" and _stored(users_db)["secret_key"] == ""

    for fields, reason in (({"interval_s": 59}, "interval"), ({"keep": 0}, "copies kept"),
                           ({"interval_s": True}, "interval"), ({"bucket": "a"}, "bucket's name"),
                           ({"bucket": "has space"}, "bucket"), ({"bucket": "", "enabled": True}, "Set a bucket"),
                           ({"endpoint": "ftp://x"}, "endpoint"), ({"endpoint": "https://h/?q=1"}, "endpoint"),
                           ({"region": "us east"}, "region"), ({"access_key": "AK2"}, "secret key that goes"),
                           ({"secret_key": "lonely"}, "access key that goes"), ({"prefix": "a/../b"}, "prefix"),
                           ({"colour": "red"}, "Unknown setting")):
        with pytest.raises(ValueError, match=reason):
            offsite.save(fields)
    assert offsite.settings()["keep"] == 5  # nothing of a refused save was kept


def test_a_secret_that_no_longer_decrypts_reads_as_none(users_db, monkeypatch):
    from cryptography.fernet import Fernet

    offsite.save({"bucket": "gamma-backups", "access_key": "AK1", "secret_key": "the-secret-1"})
    monkeypatch.setattr(offsite, "cipher", lambda: Fernet(Fernet.generate_key()))  # the key changed
    assert offsite.settings()["secret_key"] == ""


def test_the_environment_overrides_the_saved_settings(users_db, monkeypatch):
    offsite.save({"enabled": True, "bucket": "saved-bucket", "keep": 3})
    monkeypatch.setenv("GAMMA_S3_BUCKET", "env-bucket")
    monkeypatch.setenv("GAMMA_S3_ENDPOINT", "https://minio.example:9000/")
    monkeypatch.setenv("GAMMA_S3_ACCESS_KEY", "AKE")
    monkeypatch.setenv("GAMMA_S3_SECRET_KEY", "SKE")
    monkeypatch.setenv("GAMMA_S3_PREFIX", "prod")
    conf = offsite.settings()
    assert conf == {"enabled": True, "bucket": "env-bucket", "endpoint": "https://minio.example:9000", "region": "",
                    "access_key": "AKE", "secret_key": "SKE", "prefix": "prod", "interval_s": 3600, "keep": 7,
                    "from_env": True}
    with pytest.raises(offsite.FromEnvError, match="GAMMA_S3_BUCKET"):
        offsite.save({"keep": 4})
    monkeypatch.setenv("GAMMA_OFFSITE", "off")
    monkeypatch.setenv("GAMMA_OFFSITE_INTERVAL", "5")      # at least 60
    monkeypatch.setenv("GAMMA_OFFSITE_KEEP", "several")    # does not parse: the default
    conf = offsite.settings()
    assert conf["enabled"] is False and conf["interval_s"] == 60 and conf["keep"] == 7
    monkeypatch.delenv("GAMMA_S3_BUCKET")
    assert offsite.settings()["bucket"] == "saved-bucket" and offsite.settings()["keep"] == 3


def test_a_round_with_the_saved_settings(users_db, s3_bucket, monkeypatch):
    """The settings as an admin saves them in the pane, no environment."""
    monkeypatch.setattr(offsite, "_stamp", lambda: "20261003T120000Z")
    offsite.save({"enabled": True, "bucket": BUCKET, "region": "us-east-1", "access_key": "testing",
                  "secret_key": "testing", "prefix": "gui"})
    done = offsite.tick()
    assert done["copied"] == ["users.db"] and done["failed"] == {}
    listed = s3_bucket.list_objects_v2(Bucket=BUCKET, Prefix="gui/")
    assert [o["Key"] for o in listed["Contents"]] == ["gui/offsite/users/20261003T120000Z.db"]
    assert offsite.test() == {"ok": True, "message": f"{BUCKET}: 1 object under gui/"}
    assert offsite.test({"prefix": "", "secret_key": ""}) == {"ok": True, "message": f"{BUCKET}: 1 object in the bucket"}
    assert offsite.test({"bucket": "missing-bucket"})["message"].startswith("there is no bucket 'missing-bucket'")
    assert offsite.test({"keep": 0}) == {"ok": False, "message": "The copies kept must be a whole number from 1 to 1000."}


def test_the_bucket_client_says_why_it_cannot_work(s3_bucket, tmp_path):
    with pytest.raises(s3.S3ConfigError, match="no bucket 'missing-bucket'"):
        s3.Client("missing-bucket", region="us-east-1", access_key="testing", secret_key="testing").check()
    with pytest.raises(s3.S3ConfigError, match="no bucket is set"):
        s3.Client("")
    with pytest.raises(s3.S3ConfigError, match="both the access key"):
        s3.Client(BUCKET, access_key="only-half")
    client = s3.Client(BUCKET, region="us-east-1", access_key="testing", secret_key="testing", prefix="/p/")
    assert client.check() == (0, False) and client.where == f"s3://{BUCKET}/p/"
    src = tmp_path / "src"
    src.write_bytes(b"x" * 5000)
    client.put_object("offsite/ws-1/one.db", src)
    assert client.list_objects("offsite/")[0][:2] == ("offsite/ws-1/one.db", 5000)
    assert client.get_object("offsite/ws-1/one.db", tmp_path / "back" / "one.db")
    assert (tmp_path / "back" / "one.db").read_bytes() == b"x" * 5000
    assert not client.get_object("offsite/ws-1/none.db", tmp_path / "none.db")
    assert [p.name for p in tmp_path.iterdir() if "none" in p.name] == []  # no temp file left
    client.delete_object("offsite/ws-1/one.db")
    client.delete_object("offsite/ws-1/one.db")  # gone already: no error
    assert client.list_objects("offsite/") == []
    with pytest.raises(FileNotFoundError):
        client.put_object("offsite/ws-1/two.db", tmp_path / "not-there")
    for bad in ("offsite/../users.db", "a//b", "a\\b", "", "/abs"):
        with pytest.raises(ValueError):
            client.put_object(bad, src)


def test_the_bucket_client_reports_an_endpoint_out_of_reach():
    client = s3.Client(BUCKET, endpoint="http://127.0.0.1:1", region="us-east-1", access_key="a", secret_key="b",
                       attempts=1)
    with pytest.raises(s3.S3ConfigError, match="cannot reach"):
        client.check()


# --- Litestream ----------------------------------------------------------------------

def test_the_litestream_configuration_names_the_saved_bucket(users_db):
    (users_db / "workspaces" / "ws-l").mkdir(parents=True)
    _make_db(users_db / "workspaces" / "ws-l" / "pages.db")
    with pytest.raises(ValueError, match="no bucket is set"):
        offsite.litestream_config()
    offsite.save({"bucket": "gamma-files", "endpoint": "https://minio.example:9000", "region": "auto",
                  "access_key": "AK1", "secret_key": "SK1", "prefix": "tenant-1"})
    text, count = offsite.litestream_config()
    assert count == 2 and "AK1" not in text and "SK1" not in text  # the keys stay out of the file
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(text)
    assert parsed["access-key-id"] == "${GAMMA_S3_ACCESS_KEY}"
    entries = {Path(d["path"]).relative_to(users_db).as_posix(): d["replica"] for d in parsed["dbs"]}
    assert entries == {
        "users.db": {"type": "s3", "bucket": "gamma-files", "path": "tenant-1/litestream/users.db",
                     "endpoint": "https://minio.example:9000", "force-path-style": True, "region": "auto"},
        "workspaces/ws-l/pages.db": {"type": "s3", "bucket": "gamma-files",
                                     "path": "tenant-1/litestream/ws-l/pages.db",
                                     "endpoint": "https://minio.example:9000", "force-path-style": True,
                                     "region": "auto"}}


# --- the admin endpoints -------------------------------------------------------------

@pytest.fixture(scope="module")
def admins(client):
    make_user("os_admin", "os-admin-pw", is_admin=1)
    make_user("os_user", "os-user-pw")
    yield login("os_admin", "os-admin-pw"), login("os_user", "os-user-pw")
    drop_user("os_admin")
    drop_user("os_user")


@pytest.fixture
def admin(admins):
    """The admin's client; the suite's saved off-site settings removed
    after the test, so no later startup round finds them."""
    yield admins[0]
    with db.connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'offsite'")
        conn.commit()


def test_the_endpoints_are_admin_only(admins, anon):
    _, user = admins
    for method, path in (("GET", "/api/admin/offsite"), ("PUT", "/api/admin/offsite"),
                         ("POST", "/api/admin/offsite/test"), ("POST", "/api/admin/offsite/run")):
        assert user.request(method, path, json={}).status_code == 403
        assert anon.request(method, path, json={}).status_code == 401


def test_the_settings_through_the_api(admin, monkeypatch):
    r = admin.get("/api/admin/offsite")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["from_env"] is False and view["settings"] == {
        "enabled": False, "bucket": "", "endpoint": "", "region": "", "access_key": "", "secret_set": False,
        "prefix": "", "interval_s": 3600, "keep": 7}
    assert set(view["status"]) == {"enabled", "running", "interval_s", "keep", "last_round_at", "copied",
                                   "uploads_copied", "failed", "error", "next_round_at"}

    r = admin.put("/api/admin/offsite", json={"enabled": True, "bucket": "gamma-backups", "region": "auto",
                                              "access_key": "AK1", "secret_key": "api-secret-1", "interval_s": 600,
                                              "keep": 4})
    assert r.status_code == 200, r.text
    assert "api-secret-1" not in r.text and "secret_key" not in r.json()["settings"]
    assert r.json()["settings"] == {"enabled": True, "bucket": "gamma-backups", "endpoint": "", "region": "auto",
                                    "access_key": "AK1", "secret_set": True, "prefix": "", "interval_s": 600,
                                    "keep": 4}
    assert r.json()["status"]["enabled"] is True and r.json()["status"]["interval_s"] == 600
    # the secret left out (or empty) stays
    r = admin.put("/api/admin/offsite", json={"enabled": False, "bucket": "gamma-backups", "secret_key": ""})
    assert r.json()["settings"]["secret_set"] is True and offsite.settings()["secret_key"] == "api-secret-1"
    assert "api-secret-1" not in admin.get("/api/admin/offsite").text

    for body in ({"interval_s": 30}, {"keep": 0}, {"bucket": "x"}):
        r = admin.put("/api/admin/offsite", json=body)
        assert r.status_code == 400 and r.json()["detail"], body
    monkeypatch.setenv("GAMMA_S3_BUCKET", "env-bucket")
    r = admin.put("/api/admin/offsite", json={"keep": 3})
    assert r.status_code == 409 and "GAMMA_S3_BUCKET" in r.json()["detail"]
    view = admin.get("/api/admin/offsite").json()
    assert view["from_env"] is True and view["settings"]["bucket"] == "env-bucket"
    assert view["settings"]["secret_set"] is False


def test_the_test_button_with_a_bad_endpoint(admin):
    r = admin.post("/api/admin/offsite/test")  # nothing saved, nothing sent
    assert r.status_code == 200 and r.json() == {"ok": False, "message": "No bucket is set."}
    r = admin.post("/api/admin/offsite/test", json={"bucket": BUCKET, "endpoint": "http://127.0.0.1:1",
                                                    "region": "us-east-1", "access_key": "a", "secret_key": "b"})
    assert r.status_code == 200 and r.json()["ok"] is False and "cannot reach" in r.json()["message"]
    r = admin.post("/api/admin/offsite/test", json={"bucket": "x"})
    assert r.status_code == 200 and r.json()["ok"] is False and "bucket's name" in r.json()["message"]


def test_the_test_button(admin, s3_bucket):
    s3_bucket.put_object(Bucket=BUCKET, Key="team/one", Body=b"1")
    unsaved = {"bucket": BUCKET, "region": "us-east-1", "access_key": "testing", "secret_key": "testing",
               "prefix": "team"}
    r = admin.post("/api/admin/offsite/test", json=unsaved)
    assert r.json() == {"ok": True, "message": f"{BUCKET}: 1 object under team/"}
    assert admin.get("/api/admin/offsite").json()["settings"]["bucket"] == ""  # a test saves nothing
    admin.put("/api/admin/offsite", json=unsaved)
    r = admin.post("/api/admin/offsite/test", json={"prefix": ""})  # the saved secret, an unsaved prefix
    assert r.json() == {"ok": True, "message": f"{BUCKET}: 1 object in the bucket"}


def test_copy_now_through_the_api(admin, monkeypatch):
    assert admin.post("/api/admin/offsite/run").json() == {"started": False, "message": "off-site copies are off"}
    admin.put("/api/admin/offsite", json={"enabled": True, "bucket": "gamma-backups"})
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(offsite, "_round", lambda: (entered.set(), release.wait(10)))
    try:
        assert admin.post("/api/admin/offsite/run").json() == {"started": True}
        assert entered.wait(10)
        assert admin.get("/api/admin/offsite").json()["status"]["running"] is True
        assert admin.post("/api/admin/offsite/run").json() == {"started": False, "message": "a round is running"}
    finally:
        release.set()
    for _ in range(200):
        if not offsite._round_lock.locked():
            break
        time.sleep(0.05)
    assert admin.get("/api/admin/offsite").json()["status"]["running"] is False
