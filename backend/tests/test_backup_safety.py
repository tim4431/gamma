"""Backups that protect instead of endanger: upgrades prune only their own
snapshots and reuse the one of an unfinished upgrade (gamma/migrations.py,
gamma/backups.py); snapshots survive a concurrent orphan sweep and never
share a temp file; scheduled backups have limits (gamma/backup_schedule.py);
every snapshot and the admin's check quick-check the databases
(gamma/integrity.py, the db-damage notice); migration step 2 resumes into
the workspace it recorded; deleting a workspace keeps a final copy
(ws_backup.keep_final_copy)."""

import os
import sqlite3
import threading
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_user
from gamma import backup_schedule as tasks
from gamma import backups, config, integrity, migrations, notices, workspaces, ws_backup
from gamma.db import SCHEMA_VERSION, connect_data_db, connect_users_db, ws_dir, ws_uploads_dir
from test_migrations import WS25, build_v24_accounts

PW = "bs-pass-12345"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x89\x0b"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


def _account(name, is_admin=0):
    ws = make_user(name, PW, is_admin=is_admin)
    return ws, login(name, PW)


def _set_version(v):
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute(f"PRAGMA user_version = {v}")
        conn.commit()


def _damage(path: Path) -> None:
    """Give the database enough pages, then trash one in the middle: it
    still opens, only an integrity check finds the damage."""
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS filler (x TEXT)")
        conn.executemany("INSERT INTO filler VALUES (?)", [("filler " * 30,) for _ in range(2000)])
        conn.commit()
        pages, size = conn.execute("PRAGMA page_count").fetchone()[0], conn.execute("PRAGMA page_size").fetchone()[0]
    raw = bytearray(path.read_bytes())
    mid = (pages // 2) * size
    raw[mid + 8: mid + 200] = b"\xff" * 192
    path.write_bytes(bytes(raw))


@pytest.fixture
def noop_steps(data_dir, monkeypatch):
    """A current data directory whose upgrade steps do nothing: the runner's
    snapshot policy is what is under test."""
    connect_users_db().close()
    monkeypatch.setattr(migrations, "STEPS", [(v, f"step{v}", lambda conn: None) for v in range(1, SCHEMA_VERSION + 1)])
    return data_dir


# --- upgrades prune only their own snapshots ----------------------------------------

def test_upgrades_never_prune_hand_made_backups(noop_steps):
    made = [backups.create(f"hand{i}", uploads=(i == 0))["name"] for i in range(4)]
    for back in (1, 2, 3, 4):
        _set_version(SCHEMA_VERSION - back)
        assert migrations.ensure_current()["applied"]
    names = [b["name"] for b in backups.list_backups()]
    assert set(made) <= set(names)
    auto = [b for b in backups.list_backups() if backups.is_auto(b)]
    assert len(auto) == backups.KEEP_BACKUPS
    assert not backups.unfinished_upgrade()
    # manage.py backups --prune: the automatic ones only
    assert backups.prune_backups(keep=0) == [b["name"] for b in auto]
    assert [b["name"] for b in backups.list_backups()] == made


def test_a_failing_upgrade_reuses_its_clean_snapshot_across_restarts(noop_steps, monkeypatch):
    older = [backups.create(f"v{n}", auto=True)["name"] for n in (10, 11, 12)]

    def half_done(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS half_written (x TEXT)")  # a committed partial change
        conn.commit()
        raise RuntimeError("disk I/O error (simulated)")

    steps = list(migrations.STEPS)
    monkeypatch.setattr(migrations, "STEPS", steps[:-1] + [(steps[-1][0], "breaks", half_done)])
    _set_version(SCHEMA_VERSION - 1)
    snapshots = set()
    for _ in range(4):  # a restart loop
        with pytest.raises(migrations.MigrationError, match="breaks"):
            migrations.ensure_current()
        snapshots.add(backups.unfinished_upgrade()["backup"])
    assert len(snapshots) == 1
    clean = snapshots.pop()
    listed = [b["name"] for b in backups.list_backups()]
    assert listed == sorted(older + [clean])  # nothing pruned while the upgrade is unfinished
    with closing(sqlite3.connect(str(Path(backups.info(clean)["path"]) / "users.db"))) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half_written'").fetchone()
    # fixed: the upgrade finishes, forgets its marker, keeps the newest three automatic ones
    monkeypatch.setattr(migrations, "STEPS", steps)
    assert migrations.ensure_current()["backup"] == backups.info(clean)["path"]
    assert not backups.unfinished_upgrade()
    assert [b["name"] for b in backups.list_backups()] == sorted(older[1:] + [clean])


def test_a_snapshot_that_cannot_be_written_stops_the_upgrade_cleanly(noop_steps, monkeypatch):
    _set_version(SCHEMA_VERSION - 1)

    def full(src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"partial")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backups, "snapshot_db", full)
    with pytest.raises(migrations.MigrationError, match="could not snapshot"):
        migrations.ensure_current()
    assert migrations.data_version() == SCHEMA_VERSION - 1
    assert not backups.unfinished_upgrade()
    assert list(config.BACKUPS_DIR.iterdir()) == []  # no half-written, manifest-less directory


def test_a_server_restore_restarts_the_unreferenced_file_clocks(noop_steps):
    from gamma.db import connect_pages_db

    ws_dir("bsgc").mkdir(parents=True)
    with closing(connect_pages_db("bsgc")) as conn:
        conn.execute("INSERT INTO upload_orphans (name, since) VALUES ('old.pdf', '2020-01-01T00:00:00.000000Z')")
        conn.commit()
    snap = backups.create("with-orphans")
    backups.restore(snap["name"])
    with closing(connect_pages_db("bsgc")) as conn:
        since = conn.execute("SELECT since FROM upload_orphans WHERE name = 'old.pdf'").fetchone()[0]
    assert since > "2026"  # its 30 days start at the restore, not years ago


def test_a_restore_writes_each_file_whole(monkeypatch, tmp_path):
    from gamma import storage

    ws, c = _account("bs_atomic")
    name = _upload(c, "restored")
    snap = ws_backup.create(ws, label="with-file")
    (ws_uploads_dir(ws) / name).unlink()
    real, calls = storage.write_atomic, []

    def cut_short(path, data):
        calls.append(path.name)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage, "write_atomic", cut_short)
    with pytest.raises(OSError):
        ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snap["name"]), "merge")
    assert calls == [name] and not (ws_uploads_dir(ws) / name).exists()  # nothing truncated under the name
    monkeypatch.setattr(storage, "write_atomic", real)
    ws_backup.restore_zip(ws, ws_backup.backup_path(ws, snap["name"]), "merge")
    assert (ws_uploads_dir(ws) / name).read_bytes() == PNG + b"restored"


def test_an_interrupted_snapshot_directory_is_never_listed_or_pruned(noop_steps):
    stray = config.BACKUPS_DIR / "20200101-000000-v5"
    stray.mkdir(parents=True)
    (stray / "users.db").write_bytes(b"half")
    assert backups.list_backups() == [] and backups.info(stray.name) is None
    backups.create("v6", auto=True)
    assert backups.prune_backups(keep=0) and stray.is_dir()


# --- snapshots survive a concurrent sweep, never share a temp file -------------------

def _upload(c, tag):
    r = c.post("/api/upload-image", files={"file": (f"{tag}.png", PNG + tag.encode(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["url"].rsplit("/", 1)[1]


def test_a_file_swept_while_a_snapshot_runs_is_recorded_not_fatal(monkeypatch):
    ws, c = _account("bs_sweep")
    names = sorted([_upload(c, "one"), _upload(c, "two")])
    real = zipfile.ZipFile.write

    def write(self, filename, arcname=None, *a, **k):
        # the first upload is copied while an orphan sweep removes the second
        if str(arcname or "").startswith("uploads/") and (ws_uploads_dir(ws) / names[1]).exists():
            (ws_uploads_dir(ws) / names[1]).unlink()
        return real(self, filename, arcname, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "write", write)
    b = ws_backup.create(ws, label="nightly", scheduled=True, task_id="t")
    assert b["upload_files"] == 1 and b["missing_uploads"] == 1
    assert ws_backup.read_manifest(ws_backup.backup_path(ws, b["name"]))["missing_uploads"] == [names[1]]


def test_uploads_are_listed_after_the_databases_are_copied(monkeypatch):
    ws, c = _account("bs_order")
    real = ws_backup.snapshot_db
    late = {}

    def copy_then_upload(src, dst):
        result = real(src, dst)
        if src.name == "pages.db" and not late:  # a paste lands while the databases are copied
            late["name"] = _upload(c, "late")
        return result

    monkeypatch.setattr(ws_backup, "snapshot_db", copy_then_upload)
    b = ws_backup.create(ws, label="db-first")
    with zipfile.ZipFile(ws_backup.backup_path(ws, b["name"])) as z:
        assert f"uploads/{late['name']}" in z.namelist()


def test_two_snapshots_in_the_same_second_get_their_own_names_and_files(monkeypatch):
    ws, c = _account("bs_double")
    for i in range(5):
        c.post("/api/upload-file", files={"file": (f"f{i}.bin", os.urandom(200_000), "application/octet-stream")})
    monkeypatch.setattr(ws_backup, "time", SimpleNamespace(strftime=lambda _fmt: "20260101-000000"))
    out, errors = [], []

    def take():
        try:
            out.append(ws_backup.create(ws, label="manual")["name"])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=take) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and sorted(out) == ["20260101-000000-manual", "20260101-000000-manual.2",
                                          "20260101-000000-manual.3"]
    for name in out:
        with zipfile.ZipFile(ws_backup.backup_path(ws, name)) as z:
            assert z.testzip() is None and "pages.db" in z.namelist()
    assert not list(ws_backup.store_dir(ws).glob("*.part"))


# --- limits on scheduled backups ---------------------------------------------------

@pytest.fixture
def task_store(tmp_path, monkeypatch, client):
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(tasks, "_wake", lambda: None)
    return _account("bs_tasks")


@pytest.mark.parametrize("cron", ["* * * * *", "*/30 * * * *", "0,30 3 * * *", "0-5 3 * * 1"])
def test_schedules_that_run_more_than_hourly_are_refused(cron):
    with pytest.raises(tasks.TaskError, match="at most once an hour"):
        tasks.check_schedule(cron)


@pytest.mark.parametrize("cron", ["0 * * * *", "15 3 * * *", "5 */2 * * 1-5", "0 0 1 * *"])
def test_hourly_or_rarer_schedules_pass(cron):
    tasks.check_schedule(cron)


def test_task_api_limits(task_store):
    ws, c = task_store
    base = {"name": "Nightly", "workspaces": [ws], "cron": "0 3 * * *"}
    r = c.post("/api/backup-tasks", json={**base, "cron": "*/5 * * * *"})
    assert r.status_code == 400 and "once an hour" in r.json()["detail"]
    assert c.post("/api/backup-tasks/preview", json={"cron": "*/15 * * * *"}).status_code == 400
    assert c.post("/api/backup-tasks", json={**base, "retention_value": tasks.MAX_RETENTION + 1}).status_code == 422
    for i in range(tasks.MAX_TASKS):
        assert c.post("/api/backup-tasks", json={**base, "name": f"t{i}"}).status_code == 200
    r = c.post("/api/backup-tasks", json={**base, "name": "one too many"})
    assert r.status_code == 400 and str(tasks.MAX_TASKS) in r.json()["detail"]


def test_a_task_saved_before_the_limits_fails_with_the_reason(task_store):
    ws, _c = task_store
    at = datetime(2026, 9, 21, 4, tzinfo=timezone.utc)
    legacy = dict(id="a" * 32, owner=account_of("bs_tasks"), created_at=at.isoformat(), name="Every five minutes",
                  enabled=True, scope="selected", workspaces=[ws], cron="*/5 * * * *", uploads=False,
                  retention_mode="count", retention_value=3650, next_run=(at - timedelta(minutes=1)).isoformat(),
                  last_run=None, last_success=None, last_error=None, state="pending", requested=False)
    tasks._write(legacy)
    tasks.run_due(at)
    state = tasks.read(legacy["id"])
    assert state["state"] == "failed" and "once an hour" in state["last_error"]
    assert ws_backup.list_backups(ws) == []
    assert any(n["id"] == "backup-failed" for n in notices.for_user(account_of("bs_tasks"), False))
    # the owner can still pause it
    data = {k: legacy[k] for k in tasks_input_fields()}
    assert tasks.save(account_of("bs_tasks"), {**data, "enabled": False}, legacy["id"])["enabled"] is False


def tasks_input_fields():
    from gamma.routers.backup_tasks import TaskInput
    return TaskInput.model_fields


def test_scheduled_snapshots_respect_a_per_workspace_cap_and_the_free_disk_floor(task_store, monkeypatch):
    ws, c = task_store
    monkeypatch.setattr(ws_backup, "MAX_SCHEDULED_PER_WORKSPACE", 1)
    ws_backup.create(ws, label="t1", uploads=False, scheduled=True, task_id="x")
    with pytest.raises(ws_backup.BackupError, match="scheduled backups"):
        ws_backup.create(ws, label="t2", uploads=False, scheduled=True, task_id="x")
    monkeypatch.setattr(ws_backup, "MAX_SCHEDULED_PER_WORKSPACE", 100)
    monkeypatch.setattr(ws_backup, "MIN_FREE_BYTES", 1 << 62)
    r = c.post(f"/api/workspaces/{ws}/backups", json={"label": "manual", "uploads": False})
    assert r.status_code == 400 and "free on the server's disk" in r.json()["detail"]
    task = tasks.save(account_of("bs_tasks"), {"name": "Hourly", "enabled": True, "scope": "selected", "workspaces": [ws],
                                   "cron": "0 * * * *", "uploads": False, "retention_mode": "count",
                                   "retention_value": 2})
    tasks.mutate(account_of("bs_tasks"), task["id"], "run")
    tasks.run_due()
    state = tasks.read(task["id"])
    assert state["state"] == "failed" and "free on the server's disk" in state["last_error"]


# --- integrity checks -------------------------------------------------------------

def test_every_snapshot_records_the_check_of_its_database_copies():
    ws, _c = _account("bs_check")
    b = ws_backup.create(ws, label="checked", uploads=False)
    assert b["damaged"] == []
    manifest = ws_backup.read_manifest(ws_backup.backup_path(ws, b["name"]))
    assert manifest["integrity"] == {"pages.db": "ok", "data.db": "ok"}


def test_a_server_backup_of_a_damaged_workspace_records_it_and_tells_admins(data_dir):
    build_v24_accounts()
    migrations.ensure_current()
    bob = WS25
    connect_data_db(bob).close()  # the workspace's derived file, created on first use
    good = (data_dir / "workspaces" / bob / "data.db").read_bytes()
    _damage(data_dir / "workspaces" / bob / "data.db")
    b = backups.create("with-damage")  # one damaged workspace does not sink the whole snapshot
    assert b["damaged"] == [f"workspaces/{bob}/data.db"]
    assert b["integrity"]["users.db"] == "ok"
    assert set(integrity.failures()) == {f"workspaces/{bob}/data.db"}
    found = notices.database_damage("admin")
    assert found and found.id == "db-damage" and found.pane == "server" and bob in found.title
    # repaired: the next check clears it
    (data_dir / "workspaces" / bob / "data.db").write_bytes(good)
    assert backups.create("repaired")["damaged"] == []
    assert integrity.failures() == {} and notices.database_damage("admin") is None


def test_admins_check_every_database_on_demand():
    ws, _c = _account("bs_dmg")
    _aws, admin = _account("bs_admin", is_admin=1)
    _pws, plain = _account("bs_plain")
    assert plain.post("/api/admin/check-databases").status_code == 403
    path = ws_dir(ws) / "data.db"
    good = path.read_bytes()
    rel = f"workspaces/{ws}/data.db"
    try:
        _damage(path)
        r = admin.post("/api/admin/check-databases")
        assert r.status_code == 200 and r.json()["ok"] is False
        files = {f["file"]: f for f in r.json()["files"]}
        assert "users.db" in files and files["users.db"]["ok"]
        assert not files[rel]["ok"] and files[rel]["name"] == workspaces.get(ws)["name"]
        assert notices.database_damage(account_of("bs_admin")).fingerprint
        assert "db-damage" not in [n["id"] for n in notices.for_user(account_of("bs_plain"), False)]  # admins only
    finally:
        path.write_bytes(good)
        integrity.record({rel: "ok"}, "test cleanup")
    assert admin.post("/api/admin/check-databases").json()["ok"] is True
    assert notices.database_damage("bs_admin") is None


# --- deleting a workspace keeps a final copy ----------------------------------------

def test_deleting_a_workspace_keeps_a_final_copy_that_restores():
    _ws, c = _account("bs_final")
    doomed = c.post("/api/workspaces", json={"name": "Lab notes"}).json()["id"]
    page = c.post("/api/pages", json={"title": "Worth keeping"}, headers={"X-Gamma-Workspace": doomed}).json()["id"]
    r = c.delete(f"/api/workspaces/{doomed}")
    assert r.status_code == 200, r.text
    copy = ws_backup.deleted_dir() / r.json()["final_copy"]
    assert copy.is_file() and copy.name.startswith(f"{doomed}-Lab-notes-")
    assert not ws_dir(doomed).exists() and not ws_backup.store_dir(doomed).exists()
    assert ws_backup.read_manifest(copy)["workspace"] == doomed
    # the one zip format: import-data takes it back
    fresh = c.post("/api/workspaces", json={"name": "Recovered"}).json()["id"]
    with open(copy, "rb") as f:
        r = c.post(f"/api/import-data?mode=merge&ws={fresh}", files={"file": ("copy.zip", f.read(), "application/zip")})
    assert r.status_code == 200 and r.json()["pages_added"] == 1, r.text
    assert c.get(f"/api/blocks/{page}", headers={"X-Gamma-Workspace": fresh}).status_code == 200


def test_a_workspace_is_not_deleted_when_its_final_copy_fails(monkeypatch):
    _ws, c = _account("bs_nocopy")
    doomed = c.post("/api/workspaces", json={"name": "Keep me"}).json()["id"]
    before = set(ws_backup.deleted_dir().glob("*")) if ws_backup.deleted_dir().is_dir() else set()

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ws_backup, "write_zip", full)
    r = c.delete(f"/api/workspaces/{doomed}")
    assert r.status_code == 507 and "nothing was deleted" in r.json()["detail"]
    assert c.get(f"/api/workspaces/{doomed}").status_code == 200 and ws_dir(doomed).is_dir()
    assert set(ws_backup.deleted_dir().glob("*")) == before


def test_a_delete_refused_after_its_final_copy_leaves_no_copy(monkeypatch):
    """The account's other personal workspace goes while this one's final
    copy is being written: the locked re-check refuses (it is the last one
    now) and the copy of the delete that never happened goes too."""
    home, c = _account("bs_race")
    other = c.post("/api/workspaces", json={"name": "Other half"}).json()["id"]
    real = ws_backup.keep_final_copy

    def copy_while_the_other_goes(ws, **kw):
        name = real(ws, **kw)
        monkeypatch.setattr(ws_backup, "keep_final_copy", real)
        workspaces.delete(other, by=account_of("bs_race"))
        return name

    monkeypatch.setattr(ws_backup, "keep_final_copy", copy_while_the_other_goes)
    r = c.delete(f"/api/workspaces/{home}")
    assert r.status_code == 400 and "last personal workspace" in r.json()["detail"]
    assert workspaces.get(home) and ws_dir(home).is_dir() and not workspaces.get(other)
    assert not list(ws_backup.deleted_dir().glob(f"{home}-*.zip"))
    assert list(ws_backup.deleted_dir().glob(f"{other}-*.zip"))  # the delete that happened keeps its copy


def test_account_deletion_keeps_every_workspace_it_takes_all_or_nothing(monkeypatch):
    _aws, admin = _account("bs_boss", is_admin=1)
    home = make_user("bs_leaver", PW)
    shared = admin.post("/api/workspaces", json={"name": "Solo lab", "kind": "shared", "owner": "bs_leaver"}).json()["id"]
    real = ws_backup.keep_final_copy

    def second_fails(ws, **kw):
        if ws == shared:
            raise ws_backup.BackupError("its final copy could not be written (disk full)")
        return real(ws, **kw)

    monkeypatch.setattr(ws_backup, "keep_final_copy", second_fails)
    r = admin.delete("/api/admin/users/bs_leaver")
    assert r.status_code == 507 and "Solo lab" in r.json()["detail"]
    assert workspaces.get(home) and workspaces.get(shared)
    assert not list(ws_backup.deleted_dir().glob(f"{home}-*.zip"))  # the copy made first went again
    monkeypatch.setattr(ws_backup, "keep_final_copy", real)
    r = admin.delete("/api/admin/users/bs_leaver")
    assert r.status_code == 200 and set(r.json()["deleted_workspaces"]) == {home, shared}
    assert list(ws_backup.deleted_dir().glob(f"{home}-*.zip")) and list(ws_backup.deleted_dir().glob(f"{shared}-*.zip"))


def test_guests_keep_no_final_copy_and_old_copies_expire(client):
    from gamma import guests

    user_id, _name = guests.new_guest()
    gws = workspaces.default_workspace(user_id)
    ws_backup.deleted_dir().mkdir(parents=True, exist_ok=True)
    stale = ws_backup.deleted_dir() / "stale-workspace-20200101-000000.zip"
    stale.write_bytes(b"old")
    old = time.time() - (ws_backup.DELETED_KEEP_DAYS + 1) * 86400
    os.utime(stale, (old, old))
    assert workspaces.delete_account(user_id) == [gws]
    assert not list(ws_backup.deleted_dir().glob(f"{gws}-*.zip"))
    _ws, c = _account("bs_expiry")
    doomed = c.post("/api/workspaces", json={"name": "Short-lived"}).json()["id"]
    assert c.delete(f"/api/workspaces/{doomed}").status_code == 200
    assert not stale.exists()
