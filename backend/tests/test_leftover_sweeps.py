"""What a killed process or a locked file leaves behind goes later: the
work files of snapshots that never finished (ws_backup.sweep_stale_temp,
also before a workspace's next snapshot), and the directories of deleted
workspaces a held-open file kept (workspaces.remove_files sets the
directory aside first and retries; remove_leftovers takes the rest)."""

import os
import sqlite3
import threading
import time

from conftest import login, make_user, workspace_of
from gamma import config, workspaces, ws_backup
from gamma.db import workspace_ids, ws_dir

HOUR = 3600


def _aged(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_stale_snapshot_temp_files_are_swept():
    make_user("lsw_snap", "lswsnappw1")
    ws = workspace_of("lsw_snap")
    d = ws_backup.store_dir(ws)
    d.mkdir(parents=True, exist_ok=True)
    stale = [d / ".20260101-000000-manual.ab12cd34.part", d / ".20260101-000000-manual.ab12cd34.part.pages.db",
             d / ".20260101-000000-manual.ab12cd34.part.data.db-wal"]
    fresh = d / ".20260101-000001-manual.ef56ab78.part"  # a snapshot writing right now
    kept = d / "20260101-000000-manual.zip"
    for f in stale + [fresh, kept]:
        f.write_bytes(b"x")
    for f in stale + [kept]:
        _aged(f, 2 * HOUR)
    server_work = config.BACKUPS_DIR / ".20260101-000000-manual.part"
    (server_work / "workspaces").mkdir(parents=True, exist_ok=True)
    (server_work / "users.db").write_bytes(b"x")
    _aged(server_work / "users.db", 2 * HOUR)
    _aged(server_work / "workspaces", 2 * HOUR)
    _aged(server_work, 2 * HOUR)

    assert ws_backup.sweep_stale_temp() >= len(stale) + 1
    assert not any(f.exists() for f in stale) and not server_work.exists()
    assert fresh.exists() and kept.exists()
    # the next snapshot of the workspace sweeps its own folder too
    _aged(fresh, 2 * HOUR)
    ws_backup.create(ws, label="next", uploads=False)
    assert not fresh.exists() and kept.exists()


def test_a_deleted_workspace_waits_for_a_file_held_open(monkeypatch):
    make_user("lsw_del", "lswdelpw12")
    owner = login("lsw_del", "lswdelpw12")
    second = owner.post("/api/workspaces", json={"name": "Second"}).json()["id"]
    path = ws_dir(second)
    held = sqlite3.connect(str(path / "data.db"), check_same_thread=False)  # a background pass reading it
    held.execute("SELECT 1").fetchone()

    def let_go():
        time.sleep(0.3)
        held.close()

    real_remove_all = ws_backup.remove_all

    def removing(ws):  # the delete reaches the directory while the file is still open
        threading.Thread(target=let_go).start()
        real_remove_all(ws)

    monkeypatch.setattr(ws_backup, "remove_all", removing)
    r = owner.delete(f"/api/workspaces/{second}")
    assert r.status_code == 200 and not r.json().get("warning"), r.text
    assert not path.exists()
    assert second not in workspace_ids()


def test_leftover_directories_are_removed_and_never_listed():
    leftover = config.WORKSPACES_DIR / f"{workspaces.LEFTOVER_PREFIX}gone123-abcdef"
    (leftover / "uploads").mkdir(parents=True, exist_ok=True)
    (leftover / "pages.db").write_bytes(b"")
    assert leftover.name not in workspace_ids() and leftover.name not in workspaces.orphan_dirs()
    assert leftover.name in workspaces.remove_leftovers()
    assert not leftover.exists()
