"""Per-workspace migration steps (gamma/migrations.py WORKSPACE_STEPS): a
workspace's own pages.db stamp, new workspaces stamped current, a workspace
at the base upgraded on its first open with its snapshot first (once, even
when two threads open it together), the background walk, a failing step
refusing that workspace and only that one (a 503 with the guidance) until
a retry succeeds, ``manage.py migrate``, a restored backup going through
the steps above its stamp, and the startup snapshot leaving the
workspaces' files to them. A test-only step 34 is put into the step list;
every test runs in a data directory of its own (``data_dir``)."""

import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from conftest import login, make_user, together
from gamma import backups, config, db, migrations, seed, ws_backup
from gamma.db import connect_pages_db, connect_users_db

BASE = db.WS_VERSION_BASE
V = db.SCHEMA_VERSION + 1  # the test-only step's version


class Step:
    """The workspace part of step ``V``: a table in pages.db and one in
    data.db, the workspace named in a row. ``ran`` lists every run (the
    workspace id, '' for a backup's copy), ``fail`` the workspaces it fails
    on after writing, ``delay`` how long it takes."""

    def __init__(self):
        self.ran, self.fail, self.delay = [], set(), 0.0

    def __call__(self, ws, pages, data):
        self.ran.append(ws)
        pages.execute("CREATE TABLE IF NOT EXISTS lazy_step (ws TEXT NOT NULL)")
        pages.execute("INSERT INTO lazy_step (ws) VALUES (?)", (ws,))
        if data is not None:
            data.execute("CREATE TABLE IF NOT EXISTS lazy_step_data (ws TEXT NOT NULL)")
        time.sleep(self.delay)
        if ws in self.fail:
            raise RuntimeError("disk I/O error (simulated)")


@pytest.fixture
def step(data_dir, monkeypatch):
    """users.db at the current version, then this build's step ``V``: the
    data directory is one version behind, and every workspace at the base
    is behind on the step."""
    connect_users_db().close()
    step = Step()
    for module in (db, migrations, seed):
        monkeypatch.setattr(module, "SCHEMA_VERSION", V)
    monkeypatch.setattr(migrations, "WORKSPACE_STEPS", [(V, "lazy_step", step)])
    monkeypatch.setattr(migrations, "_snapshot", ())
    yield step
    for ws in db.workspace_ids():
        db.close_workspace_connections(ws)


def pages_db(ws: str) -> Path:
    return config.WORKSPACES_DIR / ws / "pages.db"


def stamp_of(path: Path) -> int:
    with closing(sqlite3.connect(str(path))) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def tables_of(path: Path) -> set:
    with closing(sqlite3.connect(str(path))) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def at_base(ws: str) -> None:
    """A workspace as every one that existed before the stamps: the current
    files, ``user_version`` 0."""
    seed.create_workspace_files(ws)
    with closing(sqlite3.connect(str(pages_db(ws)))) as conn:
        conn.execute("PRAGMA user_version = 0")


def test_new_workspaces_are_stamped_current_and_run_nothing(step):
    migrations.ensure_current()
    seed.create_workspace_files("lzFresh")
    assert stamp_of(pages_db("lzFresh")) == V
    db.ws_dir("lzEmpty").mkdir(parents=True)  # a pages.db the first connection creates
    with connect_pages_db("lzFresh") as conn, connect_pages_db("lzEmpty") as empty:
        assert conn.execute("SELECT 1 FROM unified_blocks WHERE id = 'root'").fetchone()
        assert empty.execute("SELECT name FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()
    assert stamp_of(pages_db("lzEmpty")) == V
    ws = make_user("lz_new", "pw")  # an account's personal workspace (workspaces.create)
    assert stamp_of(pages_db(ws)) == V
    assert step.ran == [] and not migrations.workspaces_behind()


def test_the_startup_snapshot_leaves_the_workspaces_to_their_steps(step):
    # With only a workspace step pending, the upgrade copies users.db alone
    # and stamps it; no workspace is touched. With a step that walks them
    # all pending too (33 here), every workspace's files are copied first,
    # and the walk stamps each one it changed.
    at_base("lzUntouched")
    result = migrations.ensure_current()
    assert result["applied"] == [] and result["workspace_steps"] == ["lazy_step"]
    assert migrations.data_version() == V
    snap = backups.info(Path(result["backup"]).name)
    assert snap["auto"] and snap["files"] == ["users.db"] and snap["workspaces"] is False
    assert stamp_of(pages_db("lzUntouched")) == 0 and "lazy_step" not in tables_of(pages_db("lzUntouched"))
    assert step.ran == [] and migrations.workspaces_behind() == ["lzUntouched"]
    assert not backups.unfinished_upgrade()

    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 32")
    result = migrations.ensure_current()
    assert result["applied"] == ["page_ops_batch_id", "folder_links", "folder_links_remote"]
    snap = backups.info(Path(result["backup"]).name)
    assert "workspaces/lzUntouched/pages.db" in snap["files"] and snap["workspaces"] is True
    assert stamp_of(pages_db("lzUntouched")) == 33  # stamped by the walk, still behind on step V
    assert step.ran == [] and migrations.is_behind("lzUntouched")


def test_a_workspace_at_the_base_is_upgraded_on_first_open_after_its_snapshot(step, monkeypatch):
    at_base("lzFirst")
    snap_name = Path(migrations.ensure_current()["backup"]).name
    # The step runs before the schema statements: one that indexes what the
    # step creates finds it.
    monkeypatch.setattr(db, "PAGES_SCHEMA", [*db.PAGES_SCHEMA, "CREATE INDEX IF NOT EXISTS idx_lazy ON lazy_step(ws)"])
    with connect_pages_db("lzFirst") as conn:
        assert conn.execute("SELECT ws FROM lazy_step").fetchall() == [("lzFirst",)]
    assert step.ran == ["lzFirst"] and stamp_of(pages_db("lzFirst")) == V
    assert "lazy_step_data" in tables_of(pages_db("lzFirst").with_name("data.db"))
    # its files from before the step, beside users.db in the upgrade's snapshot
    snap = backups.info(snap_name)
    copy = Path(snap["path"]) / "workspaces" / "lzFirst"
    assert stamp_of(copy / "pages.db") == 0 and "lazy_step" not in tables_of(copy / "pages.db")
    assert (copy / "data.db").is_file()
    assert snap["files"] == ["users.db", "workspaces/lzFirst/data.db", "workspaces/lzFirst/pages.db"]
    assert snap["integrity"]["workspaces/lzFirst/pages.db"] == "ok" and snap["damaged"] == []
    db.close_workspace_connections("lzFirst")
    with connect_pages_db("lzFirst"):
        pass
    assert step.ran == ["lzFirst"]  # once
    # a server restore of that snapshot puts the workspace back as it was
    db.close_workspace_connections("lzFirst")
    backups.restore(snap_name)
    assert stamp_of(pages_db("lzFirst")) == 0 and "lazy_step" not in tables_of(pages_db("lzFirst"))


def test_two_threads_opening_a_workspace_at_once_upgrade_it_once(step):
    at_base("lzRace")
    migrations.ensure_current()
    step.delay = 0.3

    def open_it():
        with connect_pages_db("lzRace") as conn:
            return conn.execute("SELECT COUNT(*) FROM lazy_step").fetchone()[0]

    assert together(3, open_it) == [1, 1, 1]
    assert step.ran == ["lzRace"]
    lines = (config.BACKUPS_DIR / backups.latest_auto()["name"] / backups.ADDED_LIST).read_text().splitlines()
    assert [json.loads(line)["workspace"] for line in lines] == ["lzRace"]


def test_the_background_walk_upgrades_the_workspaces_nobody_opened(step, monkeypatch):
    at_base("lzWalkA")
    at_base("lzWalkB")
    migrations.ensure_current()
    seed.create_workspace_files("lzWalkNew")
    done = migrations.warm()
    assert done == {"upgraded": ["lzWalkA", "lzWalkB"], "failed": {}}
    assert step.ran == ["lzWalkA", "lzWalkB"] and migrations.workspaces_behind() == []
    # the lifespan's thread does the same, in the background
    at_base("lzWalkLater")
    with migrations.warming():
        for _ in range(200):
            if stamp_of(pages_db("lzWalkLater")) == V:
                break
            time.sleep(0.05)
    assert stamp_of(pages_db("lzWalkLater")) == V and step.ran[-1] == "lzWalkLater"
    # no workspace step above the base: nothing is opened, no thread starts
    monkeypatch.setattr(migrations, "WORKSPACE_STEPS", [])

    def opened(*_args):
        raise AssertionError("the walk opened a workspace")

    monkeypatch.setattr(migrations, "workspace_ids", opened)
    monkeypatch.setattr(migrations, "ws_dir", opened)
    assert migrations.warm() == {"upgraded": [], "failed": {}}
    assert migrations.is_behind("lzWalkA") is False and migrations.workspaces_behind() == []
    for earlier in [t for t in threading.enumerate() if t.name == "migrate-warm"]:
        earlier.join(10)
    with migrations.warming():
        assert "migrate-warm" not in {t.name for t in threading.enumerate()}


def test_a_failing_step_refuses_that_workspace_only_until_a_retry_succeeds(step):
    migrations.ensure_current()
    ws = make_user("lz_owner", "pw")
    at_base("lzFine")
    with closing(sqlite3.connect(str(pages_db(ws)))) as conn:
        conn.execute("PRAGMA user_version = 0")  # an account's workspace from before the stamps
    db.close_workspace_connections(ws)
    step.fail.add(ws)

    with pytest.raises(migrations.MigrationError) as caught:
        connect_pages_db(ws)
    error = caught.value
    assert error.workspace == ws and error.step == f"{V} (lazy_step)" and error.version == BASE
    assert "disk I/O error" in str(error) and Path(error.snapshot).is_dir()
    assert stamp_of(pages_db(ws)) == 0 and "lazy_step" not in tables_of(pages_db(ws))  # rolled back
    guide = migrations.guidance(error)
    assert guide["title"] == "This workspace could not be upgraded"
    assert ws in guide["summary"] and "Every other workspace is served" in guide["summary"]
    assert any(f"workspaces/{ws}/" in s for s in guide["steps"])
    with connect_pages_db("lzFine") as conn:  # every other workspace is served
        assert conn.execute("SELECT ws FROM lazy_step").fetchall() == [("lzFine",)]

    client = login("lz_owner", "pw")
    r = client.get("/api/blocks/root/children", headers={"X-Gamma-Workspace": ws})
    assert r.status_code == 503 and r.headers["retry-after"] == "60"
    assert r.json()["error"] == "workspace_not_upgradable" and r.json()["title"] == guide["title"]

    step.fail.clear()  # fixed: the next open runs the step
    r = client.get("/api/blocks/root/children", headers={"X-Gamma-Workspace": ws})
    assert r.status_code == 200, r.text
    assert stamp_of(pages_db(ws)) == V and step.ran.count(ws) == 3  # the open above, the 503, this one
    copy = Path(error.snapshot) / "workspaces" / ws / "pages.db"
    assert stamp_of(copy) == 0  # the copy from before the first try is the one kept


def test_manage_py_migrate_upgrades_every_workspace(step, capsys):
    import manage

    at_base("lzCliA")
    at_base("lzCliB")
    manage.migrate(status_only=True)
    out = capsys.readouterr().out
    assert f"Pending steps: {V} lazy_step (per workspace)" in out
    assert "Workspaces behind on their own steps: 2" in out
    assert migrations.data_version() == V - 1 and stamp_of(pages_db("lzCliA")) == 0

    manage.migrate(global_only=True)
    out = capsys.readouterr().out
    assert f"Now at schema version {V}." in out and "left to the server" in out
    assert migrations.data_version() == V and step.ran == []

    manage.migrate()
    out = capsys.readouterr().out
    assert "Up to date." in out and "Workspaces upgraded: 2" in out
    assert step.ran == ["lzCliA", "lzCliB"] and stamp_of(pages_db("lzCliB")) == V
    manage.migrate(status_only=True)
    assert "Workspaces behind on their own steps: 0" in capsys.readouterr().out


def test_a_restored_backup_runs_the_steps_above_its_stamp(step, tmp_path):
    # A backup's copy stamped 0 (from before the stamps) or at the base runs
    # the step, ahead of the schema statements, and is stamped current; a
    # copy taken at the current version runs nothing.
    migrations.ensure_current()
    seed.create_workspace_files("lzSource")
    db.close_workspace_connections("lzSource")
    copies = {}
    for name, stamp in (("old", 0), ("base", BASE), ("current", V)):
        copies[name] = tmp_path / f"copy-{name}"
        copies[name].mkdir()
        for file in ("pages.db", "data.db"):
            (copies[name] / file).write_bytes((config.WORKSPACES_DIR / "lzSource" / file).read_bytes())
        with closing(sqlite3.connect(str(copies[name] / "pages.db"))) as conn:
            conn.execute(f"PRAGMA user_version = {stamp}")
    for name in ("old", "base", "current"):
        ws_backup._normalize_copies(copies[name])
        assert stamp_of(copies[name] / "pages.db") == V
    assert step.ran == ["", ""]
    for name in ("old", "base"):
        assert "lazy_step" in tables_of(copies[name] / "pages.db")
        assert "lazy_step_data" in tables_of(copies[name] / "data.db")
    assert "lazy_step" not in tables_of(copies["current"] / "pages.db")
    # a step that fails refuses the backup, nothing live touched
    step.fail.add("")
    broken = tmp_path / "copy-broken"
    broken.mkdir()
    (broken / "pages.db").write_bytes((copies["current"] / "pages.db").read_bytes())
    with closing(sqlite3.connect(str(broken / "pages.db"))) as conn:
        conn.execute("PRAGMA user_version = 0")
    with pytest.raises(ws_backup.BackupError, match="could not be brought to this version"):
        ws_backup._normalize_copies(broken)
