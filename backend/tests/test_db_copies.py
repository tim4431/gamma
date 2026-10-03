"""The databases' copies in the store (gamma/db_copies.py, docs/dev/debugging.md
"Database copies in the bucket"): the object calls of the local driver, a
round copying what changed and passing by what did not, the pruning to the
keep count (never past the data directory's first round), a copy that fails
its check, the same against moto's bucket (skipped without moto), and
manage.py's db-copies and litestream-config on a throwaway data directory."""

import itertools
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from gamma import blobs, config, db_copies, integrity
from gamma.logbuf import tail

from conftest import S3_TEST_BUCKET as BUCKET
BACKEND = Path(__file__).resolve().parents[1]


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


def _last_seq():
    seen = tail(0)
    return seen[-1]["seq"] if seen else 0


def _lines(seq):
    return [e for e in tail(seq) if e["msg"].startswith("[dbcopies]")]


@pytest.fixture
def data(data_dir, monkeypatch):
    """conftest's data directory of the test's own, holding users.db and two
    workspaces' databases, with the copies on, rounds stamped a second
    apart, and ``use(store)`` making ``store`` the active driver (default:
    the local one, keeping its objects in that data directory)."""
    monkeypatch.setenv("GAMMA_DB_COPIES", "1")
    monkeypatch.delenv("GAMMA_DB_COPIES_KEEP", raising=False)
    clock = itertools.count(1)
    monkeypatch.setattr(db_copies, "_stamp", lambda: f"20261003T10{next(clock):04d}Z")
    _make_db(data_dir / "users.db", ["alice"])
    for ws in ("ws-a", "ws-b"):
        _make_db(data_dir / "workspaces" / ws / "pages.db", [f"{ws} page"])
        _make_db(data_dir / "workspaces" / ws / "data.db", [f"{ws} data"])
    before = blobs.driver()

    def use(store=None):
        store = store or blobs.LocalBlobs()
        blobs.use(store)
        return store

    use.root = data_dir
    yield use
    blobs.use(before)


# --- the object calls ----------------------------------------------------------------

def test_the_local_object_calls_round_trip(data_dir):
    store = blobs.LocalBlobs()
    src = data_dir / "src.db"
    src.write_bytes(b"x" * 5000)
    store.put_object("dbcopies/ws-1/one.db", src)
    assert (data_dir / "dbcopies" / "ws-1" / "one.db").read_bytes() == b"x" * 5000
    (data_dir / "dbcopies" / ".partial").mkdir(exist_ok=True)
    (data_dir / "dbcopies" / ".partial" / "writing").write_bytes(b"half")  # a write in progress: no object
    store.put_object("dbcopies/ws-2/two.db", src)
    assert [k for k, _, _ in store.list_objects("dbcopies/")] == ["dbcopies/ws-1/one.db", "dbcopies/ws-2/two.db"]
    (key, size, mtime), = store.list_objects("dbcopies/ws-1/")
    assert key == "dbcopies/ws-1/one.db" and size == 5000 and mtime > 0
    assert store.list_objects("dbcopies/ws-1/o") == store.list_objects("dbcopies/ws-1/")
    assert store.list_objects("dbcopies/ws-9/") == []

    dest = data_dir / "back" / "one.db"
    assert store.get_object("dbcopies/ws-1/one.db", dest) and dest.read_bytes() == b"x" * 5000
    assert not store.get_object("dbcopies/ws-1/none.db", data_dir / "none.db")
    assert not (data_dir / "none.db").exists()
    src.write_bytes(b"y")
    store.put_object("dbcopies/ws-1/one.db", src)  # replaced whole
    assert store.get_object("dbcopies/ws-1/one.db", dest) and dest.read_bytes() == b"y"

    store.delete_object("dbcopies/ws-1/one.db")
    store.delete_object("dbcopies/ws-1/one.db")  # gone already: no error
    assert [k for k, _, _ in store.list_objects("dbcopies/")] == ["dbcopies/ws-2/two.db"]
    for bad in ("uploads/ws-1/a.pdf", "dbcopies", "users.db", "dbcopies/../users.db", "dbcopies/.partial/x",
                "dbcopies/a\\b", "dbcopies//x", "workspaces/ws-1/pages.db", ""):
        with pytest.raises(ValueError):
            store.put_object(bad, src)
    with pytest.raises(ValueError):
        store.list_objects("uploads/")


# --- a round -------------------------------------------------------------------------

def test_a_round_copies_what_changed_and_passes_by_the_rest(data):
    store, root = data(), data.root
    seq = _last_seq()
    first = db_copies.tick()
    assert sorted(first["copied"]) == ["users.db", "ws-a/data.db", "ws-a/pages.db", "ws-b/data.db", "ws-b/pages.db"]
    assert first["failed"] == {} and first["unchanged"] == 0
    keys = [k for k, _, _ in store.list_objects("dbcopies/")]
    assert keys == ["dbcopies/users/20261003T100001Z.db", "dbcopies/ws-a/20261003T100001Z-data.db",
                    "dbcopies/ws-a/20261003T100001Z-pages.db", "dbcopies/ws-b/20261003T100001Z-data.db",
                    "dbcopies/ws-b/20261003T100001Z-pages.db"]
    got = root / "got.db"
    assert store.get_object("dbcopies/ws-a/20261003T100001Z-pages.db", got) and _rows(got) == ["ws-a page"]
    assert not (root / "backups" / ".dbcopies").exists()  # the temp copies are gone
    assert len(_lines(seq)) == 1 and "copied" in _lines(seq)[0]["msg"]

    seq = _last_seq()
    assert db_copies.tick() == {"copied": [], "failed": {}, "unchanged": 5, "pruned": 0}
    assert _lines(seq) == []  # a round that copied nothing says nothing

    # a server writes ws-a's pages and users.db and keeps its connection open
    held = sqlite3.connect(root / "workspaces" / "ws-a" / "pages.db")
    try:
        held.execute("INSERT INTO notes VALUES ('second')")
        held.commit()
        _make_db(root / "users.db", ["bob"])
        done = db_copies.tick()
        assert sorted(done["copied"]) == ["users.db", "ws-a/pages.db"] and done["unchanged"] == 3
        assert store.get_object("dbcopies/ws-a/20261003T100003Z-pages.db", got)
        assert _rows(got) == ["ws-a page", "second"]
    finally:
        held.close()  # the last connection: SQLite folds the WAL into the file, which is no change
    assert db_copies.tick()["copied"] == []
    assert {label: [s for s, _ in gens] for label, gens in db_copies.listing("ws-a").items()} == {
        "ws-a/data.db": ["20261003T100001Z"], "ws-a/pages.db": ["20261003T100001Z", "20261003T100003Z"]}


def test_the_copies_off_do_nothing(data, monkeypatch):
    store = data()
    monkeypatch.setenv("GAMMA_DB_COPIES", "0")
    assert db_copies.tick() == {"copied": [], "failed": {}, "unchanged": 0, "pruned": 0}
    assert store.list_objects("dbcopies/") == [] and not (data.root / "backups").exists()


def test_the_settings_and_their_defaults(monkeypatch):
    for var in ("GAMMA_DB_COPIES", "GAMMA_DB_COPIES_INTERVAL", "GAMMA_DB_COPIES_KEEP", "GAMMA_BLOBS"):
        monkeypatch.delenv(var, raising=False)
    assert config.db_copies_env() == {"on": False, "interval": 3600, "keep": 7}  # local: off
    monkeypatch.setenv("GAMMA_BLOBS", "s3")
    assert config.db_copies_env()["on"]  # a bucket: on
    monkeypatch.setenv("GAMMA_DB_COPIES", "off")
    assert not config.db_copies_env()["on"]
    monkeypatch.setenv("GAMMA_DB_COPIES_INTERVAL", "5")
    monkeypatch.setenv("GAMMA_DB_COPIES_KEEP", "zero")
    assert config.db_copies_env() == {"on": False, "interval": 60, "keep": 7}
    monkeypatch.setenv("GAMMA_DB_COPIES_KEEP", "0")
    assert config.db_copies_env()["keep"] == 1


def test_copies_are_pruned_to_the_keep_count_never_past_the_first_round(data, monkeypatch):
    store, root = data(), data.root
    monkeypatch.setenv("GAMMA_DB_COPIES_KEEP", "3")
    older = root / "older.db"
    _make_db(older)
    store.put_object("dbcopies/users/20200101T000000Z.db", older)  # another data directory's, before this one
    for n in range(5):
        _make_db(root / "users.db", [f"change {n}"])
        done = db_copies.tick()
    assert done["pruned"] == 1 and done["copied"] == ["users.db"]
    assert [s for s, _ in db_copies.listing("users")["users.db"]] == [
        "20200101T000000Z", "20261003T100003Z", "20261003T100004Z", "20261003T100005Z"]
    assert len(db_copies.listing("ws-a")["ws-a/pages.db"]) == 1  # unchanged: one copy

    # copies from both sides of this directory's first round: which one is meant must be said
    with pytest.raises(db_copies.RestoreError, match="--at 20200101T000000Z for the newest from before"):
        db_copies.restore("users")
    db_copies.restore("users", at="20200101T000000Z")
    assert _rows(root / "users.db") == ["first"]
    # a restore makes the copies this data directory's: the next round prunes the old one too
    _make_db(root / "users.db", ["after the restore"])
    db_copies.tick()
    assert [s for s, _ in db_copies.listing("users")["users.db"]] == [
        "20261003T100004Z", "20261003T100005Z", "20261003T100007Z"]


def test_a_copy_that_fails_its_check_is_skipped_and_an_upload_that_fails_too(data, monkeypatch):
    store, root = data(), data.root
    real_snapshot, real_put = db_copies.snapshot_db, store.put_object
    damaged = str(root / "workspaces" / "ws-a" / "pages.db")

    def snapshot(src, dst):
        result = real_snapshot(src, dst)
        return "*** in database main ***\nPage 3 is never used" if str(src) == damaged else result

    def put(key, path):
        if key.startswith("dbcopies/ws-b/"):
            raise blobs.BlobError("PUT refused: the bucket is out of reach")
        real_put(key, path)

    monkeypatch.setattr(db_copies, "snapshot_db", snapshot)
    monkeypatch.setattr(store, "put_object", put)
    seq = _last_seq()
    done = db_copies.tick()
    assert sorted(done["copied"]) == ["users.db", "ws-a/data.db"]
    assert "Page 3 is never used" in done["failed"]["ws-a/pages.db"]
    assert "out of reach" in done["failed"]["ws-b/pages.db"] and "ws-b/data.db" in done["failed"]
    assert [k for k, _, _ in store.list_objects("dbcopies/")] == [
        "dbcopies/users/20261003T100001Z.db", "dbcopies/ws-a/20261003T100001Z-data.db"]
    warned = [e for e in tail(seq) if e["level"] == "WARNING"]
    assert any("workspaces/ws-a/pages.db failed its check" in e["msg"] for e in warned)  # gamma/integrity.py
    round_line, = _lines(seq)
    assert round_line["level"] == "WARNING" and "failed: " in round_line["msg"]
    assert "workspaces/ws-a/pages.db" in integrity._read()  # the admins' db-damage notice

    monkeypatch.setattr(db_copies, "snapshot_db", real_snapshot)
    monkeypatch.setattr(store, "put_object", real_put)
    assert sorted(db_copies.tick()["copied"]) == ["ws-a/pages.db", "ws-b/data.db", "ws-b/pages.db"]
    assert "workspaces/ws-a/pages.db" not in integrity._read()


# --- restoring -----------------------------------------------------------------------

def test_a_restore_puts_the_copies_back_and_moves_the_files_aside(data):
    data()
    root = data.root
    pages = root / "workspaces" / "ws-a" / "pages.db"
    db_copies.tick()
    _make_db(pages, ["later"])
    _make_db(root / "workspaces" / "ws-a" / "data.db", ["later data"])
    db_copies.tick()  # stamp 2: both of ws-a's changed
    _make_db(pages, ["latest"])
    db_copies.tick()  # stamp 3: its pages.db only

    done = db_copies.restore("ws-a", at="20261003T100002Z")
    assert [(r["label"], r["stamp"]) for r in done] == [("ws-a/data.db", "20261003T100002Z"),
                                                        ("ws-a/pages.db", "20261003T100002Z")]
    assert not Path(f"{pages}-shm").exists() and not pages.with_name(".pages.db.restoring").exists()
    assert _rows(pages) == ["ws-a page", "later"]
    aside = pages.with_name(done[1]["aside"])
    assert aside.name.startswith("pages.db.pre-restore-") and _rows(aside) == ["ws-a page", "later", "latest"]

    db_copies.restore("ws-a")  # the newest of each: pages from round 3, data from round 2
    assert _rows(pages) == ["ws-a page", "later", "latest"]
    assert _rows(root / "workspaces" / "ws-a" / "data.db") == ["ws-a data", "later data"]

    with pytest.raises(db_copies.RestoreError, match="holds no copy of ws-a from 20200101T000000Z"):
        db_copies.restore("ws-a", at="20200101T000000Z")
    with pytest.raises(db_copies.RestoreError, match="--at takes a stamp"):
        db_copies.restore("ws-a", at="yesterday")
    with pytest.raises(db_copies.RestoreError, match="no workspace id"):
        db_copies.restore("../users.db")


def test_a_restore_refuses_a_database_in_use_and_a_copy_that_fails_its_check(data, monkeypatch):
    data()
    root = data.root
    pages = root / "workspaces" / "ws-a" / "pages.db"
    db_copies.tick()
    _make_db(pages, ["unsaved elsewhere"])
    before = sorted(p.name for p in pages.parent.iterdir())
    for held_file in (pages, root / "users.db"):  # users.db open: the server is running
        with closing(sqlite3.connect(held_file)) as held:
            held.execute("SELECT count(*) FROM notes").fetchone()
            with pytest.raises(db_copies.RestoreError, match="in use"):
                db_copies.restore("ws-a")
    monkeypatch.setattr(integrity, "quick_check", lambda path: "row 1 missing from index")
    with pytest.raises(db_copies.RestoreError, match="failed its check"):
        db_copies.restore("ws-a")
    assert sorted(p.name for p in pages.parent.iterdir()) == before  # nothing moved, no download left
    assert _rows(pages) == ["ws-a page", "unsaved elsewhere"]


def test_a_lost_disk_comes_back_whole(data):
    """Everything restored onto an empty data directory: users.db and every
    workspace the store holds copies of."""
    import shutil

    data()
    root = data.root
    db_copies.tick()
    shutil.rmtree(root / "workspaces")
    (root / "users.db").unlink()
    for side in ("-wal", "-shm"):
        Path(f"{root / 'users.db'}{side}").unlink(missing_ok=True)
    done = db_copies.restore("all")
    assert sorted(r["label"] for r in done) == ["users.db", "ws-a/data.db", "ws-a/pages.db",
                                                "ws-b/data.db", "ws-b/pages.db"]
    assert all(r["aside"] == "" for r in done)
    assert _rows(root / "users.db") == ["alice"] and _rows(root / "workspaces" / "ws-b" / "pages.db") == ["ws-b page"]


# --- the same through the S3 driver --------------------------------------------------

@pytest.fixture
def bucket(tmp_path, s3_bucket):
    store = blobs.S3Blobs(BUCKET, region="us-east-1", access_key="testing", secret_key="testing",
                          prefix="tenant-1", cache_dir=tmp_path / "cache")
    store.raw = s3_bucket
    return store


def test_rounds_and_a_restore_through_the_bucket(data, bucket, monkeypatch):
    root = data.root
    data(bucket)
    monkeypatch.setenv("GAMMA_DB_COPIES_KEEP", "2")
    db_copies.tick()
    for n in range(3):
        _make_db(root / "workspaces" / "ws-b" / "pages.db", [f"change {n}"])
        done = db_copies.tick()
        assert done["copied"] == ["ws-b/pages.db"] and done["unchanged"] == 4
    listed = bucket.raw.list_objects_v2(Bucket=BUCKET, Prefix="tenant-1/dbcopies/ws-b/")
    assert sorted(o["Key"] for o in listed["Contents"]) == [
        "tenant-1/dbcopies/ws-b/20261003T100001Z-data.db", "tenant-1/dbcopies/ws-b/20261003T100003Z-pages.db",
        "tenant-1/dbcopies/ws-b/20261003T100004Z-pages.db"]
    state = (root / "backups" / db_copies.STATE_FILE).read_text(encoding="utf-8")
    assert f"s3://{BUCKET}/tenant-1/" in state

    db_copies.restore("ws-b", at="20261003T100003Z")
    assert _rows(root / "workspaces" / "ws-b" / "pages.db") == ["ws-b page", "change 0", "change 1"]


# --- manage.py -----------------------------------------------------------------------

def _cli(data_dir: Path, *args, check=True, **env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("GAMMA_S3_", "GAMMA_BLOB", "GAMMA_DB_COPIES"))}
    result = subprocess.run([sys.executable, *args], cwd=BACKEND, capture_output=True, text=True, timeout=120,
                            env={**clean, "GAMMA_DATA_DIR": str(data_dir), "GAMMA_DB_COPIES": "1", **env})
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_manage_db_copies_lists_and_restores(tmp_path):
    _cli(tmp_path, "manage.py", "create-user", "dbc_alice", "pw-dbc-1234")
    ws = next(d.name for d in (tmp_path / "workspaces").iterdir())
    _cli(tmp_path, "-c", "from gamma import db_copies; db_copies.tick()")
    listed = _cli(tmp_path, "manage.py", "db-copies", "--list").stdout
    assert "users.db  1 copy" in listed and f"{ws}/pages.db  1 copy" in listed and f"{ws}/data.db" in listed
    stamp = listed.split("the newest ")[1][:16]
    assert f"  {stamp}  {ws}/pages.db  " in _cli(tmp_path, "manage.py", "db-copies", "--list", ws).stdout

    pages = tmp_path / "workspaces" / ws / "pages.db"
    with closing(sqlite3.connect(pages)) as conn:
        conn.execute("CREATE TABLE after_the_copy (x)")
        conn.commit()
        refused = _cli(tmp_path, "manage.py", "db-copies", "--restore", ws, check=False)
    assert refused.returncode == 2 and "in use" in refused.stdout
    assert not list(pages.parent.glob("*.pre-restore-*"))

    out = _cli(tmp_path, "manage.py", "db-copies", "--restore", ws, "--at", stamp).stdout
    assert f"Restored {ws}/pages.db from {stamp}" in out
    with closing(sqlite3.connect(pages)) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'after_the_copy'").fetchone()
    aside, = (p for p in pages.parent.glob("pages.db.pre-restore-*") if not p.name.endswith(("-wal", "-shm")))
    with closing(sqlite3.connect(aside)) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'after_the_copy'").fetchone()
    bad = _cli(tmp_path, "manage.py", "db-copies", "--restore", ws, "--at", "today", check=False)
    assert bad.returncode == 2 and "Refused" in bad.stdout


def test_manage_litestream_config_names_every_database(tmp_path):
    _cli(tmp_path, "manage.py", "create-user", "dbc_lit", "pw-dbc-1234")
    _cli(tmp_path, "manage.py", "create-workspace", "Second", "dbc_lit", "shared")
    wss = sorted(d.name for d in (tmp_path / "workspaces").iterdir())
    assert len(wss) == 2
    refused = _cli(tmp_path, "manage.py", "litestream-config", check=False)
    assert refused.returncode == 2 and "GAMMA_S3_BUCKET" in refused.stdout
    bucket_env = {"GAMMA_S3_BUCKET": "gamma-files", "GAMMA_S3_ENDPOINT": "https://minio.example:9000",
                  "GAMMA_S3_REGION": "auto", "GAMMA_S3_PREFIX": "tenant-1", "GAMMA_S3_ACCESS_KEY": "AK1",
                  "GAMMA_S3_SECRET_KEY": "SK1"}
    text = _cli(tmp_path, "manage.py", "litestream-config", **bucket_env).stdout
    assert "AK1" not in text and "SK1" not in text  # the keys stay in the environment
    out = tmp_path / "litestream.yml"
    assert "5 database(s)" in _cli(tmp_path, "manage.py", "litestream-config", "--out", str(out), **bucket_env).stdout
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert parsed["access-key-id"] == "${GAMMA_S3_ACCESS_KEY}"
    entries = {Path(d["path"]).relative_to(tmp_path).as_posix(): d["replica"] for d in parsed["dbs"]}
    assert sorted(entries) == ["users.db",
                               *(f"workspaces/{ws}/{name}" for ws in wss for name in ("data.db", "pages.db"))]
    assert entries["users.db"] == {"type": "s3", "bucket": "gamma-files", "path": "tenant-1/litestream/users.db",
                                   "endpoint": "https://minio.example:9000", "force-path-style": True,
                                   "region": "auto"}
    assert entries[f"workspaces/{wss[0]}/pages.db"]["path"] == f"tenant-1/litestream/{wss[0]}/pages.db"
