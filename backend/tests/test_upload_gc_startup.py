"""One damaged workspace never stops the others: startup maintenance, the
per-workspace migration steps (9, 12, 13, 22) and the background upload
reconciliation log it and go on. Step 22 gives every workspace's pages.db
its ``upload_orphans`` table; the startup path does not touch uploads.
Runs in its own temp data directory (the ``data_dir`` fixture)."""

import sqlite3
from contextlib import closing

import gamma.app as app_mod
from gamma import migrations, upload_gc
from gamma.db import connect_users_db
from gamma.logbuf import tail
from gamma.seed import create_workspace_files


def _workspaces(root):
    """Two healthy workspaces and one whose pages.db is not a database."""
    for ws in ("wsgood1", "wsgood2", "wsbroken"):
        create_workspace_files(ws)
    bad = root / "workspaces" / "wsbroken" / "pages.db"
    for side in ("-wal", "-shm"):
        (bad.parent / f"pages.db{side}").unlink(missing_ok=True)
    bad.write_bytes(b"this is not a database" * 100)


def _errors_since(seq):
    return [e["msg"] for e in tail(seq) if e["level"] == "ERROR"]


def _last_seq():
    seen = tail(0)
    return seen[-1]["seq"] if seen else 0


def test_step_22_adds_upload_orphans_and_skips_a_damaged_workspace(data_dir):
    _workspaces(data_dir)
    for ws in ("wsgood1", "wsgood2"):  # files from before the step
        with closing(sqlite3.connect(str(data_dir / "workspaces" / ws / "pages.db"))) as conn:
            conn.execute("DROP TABLE upload_orphans")
            conn.commit()
    seq = _last_seq()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        migrations._v22_upload_orphans(conn)  # does not raise
    for ws in ("wsgood1", "wsgood2"):
        with closing(sqlite3.connect(str(data_dir / "workspaces" / ws / "pages.db"))) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'upload_orphans'").fetchone()
    assert any("wsbroken" in m and "step 22" in m for m in _errors_since(seq))
    # the other step that walks every workspace does the same
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        migrations._v23_page_trash(conn)


def test_startup_serves_the_others_when_one_workspace_is_damaged(data_dir, monkeypatch):
    connect_users_db().close()
    _workspaces(data_dir)

    def no_sweep(ws):
        raise AssertionError("the startup path must not reconcile uploads")

    monkeypatch.setattr(upload_gc, "reconcile", no_sweep)
    seq = _last_seq()
    app_mod._startup_maintenance()  # does not raise
    # a file that cannot be read counts as behind on the workspace steps
    # while one is pending: the background walk opens it, and says so
    migrations.warm()
    assert any("wsbroken" in m for m in _errors_since(seq))


def test_the_background_pass_goes_on_past_a_damaged_workspace(data_dir, monkeypatch):
    connect_users_db().close()
    _workspaces(data_dir)
    seen = []
    real = upload_gc.reconcile
    monkeypatch.setattr(upload_gc, "reconcile", lambda ws: (seen.append(ws), real(ws))[1])
    seq = _last_seq()
    upload_gc.reconcile_all()
    assert seen == ["wsbroken", "wsgood1", "wsgood2"]
    assert any("wsbroken" in m for m in _errors_since(seq))
