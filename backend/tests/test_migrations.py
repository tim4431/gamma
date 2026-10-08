"""gamma/migrations.py — the versioned upgrade of the data directory, run
against a hand-built pre-workspace layout (schema version 0): every account
directory becomes a workspace, personal prefs move to users.db, shares are
re-keyed, a snapshot is taken first, a second run is a no-op, and a newer
data directory is refused. Runs in its own temp data directory (the
``data_dir`` fixture) so the suite's shared one is never touched."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import gamma.app as app_mod
from gamma import backups, config, migrations
from gamma.db import (SCHEMA_VERSION, USERS_SCHEMA, SchemaOutdated, connect_users_db, page_now, register_functions,
                      share_token_workspace)

OLD = "2024-01-01T00:00:00.000000Z"


def v24_users_db(version: int = 24) -> None:
    """users.db as schema version 24 shaped it (the frozen statements steps
    1-24 create their tables from), stamped ``version``: where a test of an
    older step starts."""
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        for stmt in migrations._V24_USERS_SCHEMA:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()


def release_of(m, version: int) -> None:
    """Make the runner the release whose newest step is ``version``: its
    global and its workspace step lists end there."""
    m.setattr(migrations, "STEPS", [step for step in migrations.STEPS if step[0] <= version])
    m.setattr(migrations, "WORKSPACE_STEPS", [step for step in migrations.WORKSPACE_STEPS if step[0] <= version])


def ids_by_name() -> dict:
    with connect_users_db() as conn:
        return dict(conn.execute("SELECT username, id FROM users").fetchall())


def test_v20_removes_the_legacy_guest_account(data_dir):
    # the shared guest account goes with its rows and its workspace directory;
    # a password-less is_guest row made by create-user becomes a normal account
    v24_users_db()
    ws = "legacyGuestWs"
    (data_dir / "workspaces" / ws / "uploads").mkdir(parents=True)
    (data_dir / "workspaces" / ws / "pages.db").write_bytes(b"")
    (data_dir / "backups" / "workspaces" / ws).mkdir(parents=True)
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.executemany("INSERT INTO users (username, password_hash, is_guest, default_workspace, created_at) "
                         "VALUES (?, '', 1, ?, ?)", [("guest", ws, OLD), ("mig20_nopw", "", OLD)])
        conn.execute("INSERT INTO workspaces (id, name, created_by, created_at) VALUES (?, 'guest', 'guest', ?)", (ws, OLD))
        conn.execute("INSERT INTO workspace_members (workspace_id, username, role, added_at) VALUES (?, 'guest', 'owner', ?)",
                     (ws, OLD))
        conn.execute("INSERT INTO sessions (token, username, guest_date, created_at) VALUES ('tok-g', 'guest', '2020-01-01', ?)",
                     (OLD,))
        conn.execute("INSERT INTO user_prefs VALUES ('guest', ?, 'open-tabs', '[]', ?)", (ws, OLD))
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, created_at) VALUES ('sh-g', ?, 'p', ?)", (ws, OLD))
        conn.execute("PRAGMA user_version = 19")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["guest_accounts", "folder_shares", "upload_orphans", "page_trash", "jobs", "account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT username, is_guest FROM users").fetchall() == [("mig20_nopw", 0)]
        for table in ("sessions", "workspaces", "workspace_members", "user_prefs", "shares"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not (data_dir / "workspaces" / ws).exists() and not (data_dir / "backups" / "workspaces" / ws).exists()
    # re-runnable
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        migrations._v20_guest_accounts(conn)
    assert migrations.ensure_current()["applied"] == []


def test_v21_gives_shares_a_folder_target(data_dir):
    # shares gain folder (a share names a page OR a folder); the page unique
    # index becomes partial and a folder twin joins it, so one page share and
    # one folder share per workspace each stay unique while '' repeats freely
    v24_users_db()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE shares")
        conn.execute("CREATE TABLE shares (token TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, page_id TEXT NOT NULL, "
                     "created_by TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT 'anyone', "
                     "role TEXT NOT NULL DEFAULT 'view', allowed_users TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
        conn.execute("CREATE UNIQUE INDEX idx_shares_page ON shares(workspace_id, page_id)")
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, created_at) VALUES ('sh21', 'ws21', 'p1', ?)", (OLD,))
        conn.execute("PRAGMA user_version = 20")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_shares", "upload_orphans", "page_trash", "jobs", "account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT page_id, folder FROM shares WHERE token = 'ws21.sh21'").fetchone() == ("p1", "")
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('f1', 'ws21', '', 'a/b', ?)", (OLD,))
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('f2', 'ws21', '', 'c', ?)", (OLD,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('f3', 'ws21', '', 'a/b', ?)", (OLD,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('p2', 'ws21', 'p1', '', ?)", (OLD,))
    assert migrations.ensure_current()["applied"] == []


def test_v23_writes_the_reserved_trash_row(data_dir):
    v24_users_db()
    ws_dir = config.WORKSPACES_DIR / "wsTrash23"
    ws_dir.mkdir(parents=True)
    with closing(sqlite3.connect(str(ws_dir / "pages.db"))) as conn:
        for stmt in migrations._V25_PAGES_SCHEMA:
            conn.execute(stmt)
        conn.execute("INSERT INTO unified_blocks VALUES ('root', NULL, 'a0', '', '{}', 'x', 'x')")
        conn.commit()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 22")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["page_trash", "jobs", "account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    with closing(sqlite3.connect(str(ws_dir / "pages.db"))) as conn:
        assert conn.execute("SELECT parent_id, position FROM unified_blocks WHERE id = 'trash'").fetchone() == (None, "a1")
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 22")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["page_trash", "jobs", "account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    with closing(sqlite3.connect(str(ws_dir / "pages.db"))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE id = 'trash'").fetchone()[0] == 1


def test_v35_adds_folder_links_and_is_repeatable(data_dir):
    """users.db gains the folders-on-disk links (gamma/folder_links.py) in
    step 35's frozen shape, and step 36 the remote source's columns; both
    steps leave a table that has what they add as it is."""
    v24_users_db(34)
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'folder_links'").fetchone()
    assert migrations.ensure_current()["applied"] == ["folder_links", "folder_links_remote"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT * FROM folder_links").fetchall() == []
        assert migrations._columns(conn, "folder_links")[-3:] == ["remote_url", "token", "token_id"]
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("PRAGMA user_version = 34")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_links", "folder_links_remote"]  # re-runnable over an existing table


def test_v36_adds_the_remote_source_columns_and_is_repeatable(data_dir):
    """A users.db at version 35 holds the table in its old shape: step 36
    adds the three columns and keeps the rows; one without the table (a
    stamp without the step's work) gets it whole from the live statement."""
    v24_users_db(35)
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute(migrations._V35_FOLDER_LINKS)
        conn.execute("INSERT INTO folder_links (id, workspace_id, folder_id, path, created_by, created_at) "
                     "VALUES ('l1', 'ws1', 'root', 'Papers', 'u1', '2026-01-01T00:00:00Z')")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_links_remote"]
    with connect_users_db() as conn:
        assert migrations._columns(conn, "folder_links")[-3:] == ["remote_url", "token", "token_id"]
        assert conn.execute("SELECT id, path, remote_url, token, token_id FROM folder_links").fetchall() == [("l1", "Papers", "", "", "")]
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("PRAGMA user_version = 35")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_links_remote"]  # re-runnable: the columns are there
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:  # no table at all: made whole
        conn.execute("DROP TABLE folder_links")
        conn.execute("PRAGMA user_version = 35")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_links_remote"]
    with connect_users_db() as conn:
        assert "remote_url" in migrations._columns(conn, "folder_links")


def test_v24_adds_jobs_and_is_repeatable(data_dir):
    v24_users_db()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE jobs")
        conn.execute("PRAGMA user_version = 23")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["jobs", "account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT * FROM jobs").fetchall() == []
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'jobs'")}
        assert {"idx_jobs_owner", "idx_jobs_workspace"} <= indexes
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("PRAGMA user_version = 23")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["jobs", "account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable over an existing table


WS25 = "wsAccounts25"
TASK_A, TASK_GHOST = "a" * 32, "b" * 32


def build_v24_accounts():
    """A version-24 data directory that names its accounts (alice, bob, the
    guest guest-x) by username everywhere: every users.db table, a
    workspace's op log, tombstone and trashed page, the backup task files.
    ``ghost`` is a name no account has."""
    from gamma.publisher_sessions import cipher

    v24_users_db()
    sealed = cipher().encrypt(json.dumps({"user": "alice", "host": "www.nature.com", "cookies": []}).encode())
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.executemany("INSERT INTO users (username, password_hash, is_guest, default_workspace, created_at) "
                         "VALUES (?, 'x', ?, ?, ?)", [("alice", 0, WS25, OLD), ("bob", 0, "", OLD), ("guest-x", 1, "", OLD)])
        conn.executemany("INSERT INTO sessions (token, username, created_at) VALUES (?, ?, ?)",
                         [("tok-a", "alice", page_now()), ("tok-ghost", "ghost", OLD)])
        conn.execute("INSERT INTO identities (provider, subject, username, created_at, last_login_at) "
                     "VALUES ('gamma-cloud', 'sub-a', 'alice', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO integration_tokens VALUES ('t1', 'hash', 'bob', ?, 'Codex', ?, 9999999999, 'read')",
                     (WS25, OLD))
        conn.executemany("INSERT INTO publisher_sessions VALUES (?, 'www.nature.com', ?, 9999999999, ?)",
                         [("alice", sealed.decode("ascii"), OLD), ("bob", "not a token", OLD)])
        conn.execute("INSERT INTO workspaces (id, name, created_by, created_at, kind) VALUES (?, 'Lab', 'alice', ?, "
                     "'shared')", (WS25, OLD))
        conn.executemany("INSERT INTO workspace_members (workspace_id, username, role, added_by, added_at) "
                         "VALUES (?, ?, ?, ?, ?)", [(WS25, "alice", "owner", "alice", OLD),
                                                    (WS25, "bob", "editor", "ghost", OLD)])
        conn.execute("INSERT INTO pending_memberships VALUES (?, 'sub-c', 'carol', 'viewer', 'alice', ?)", (WS25, OLD))
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, created_by, audience, role, allowed_users, "
                     "created_at) VALUES ('sh', ?, 'p1', 'alice', 'list', 'view', 'bob:edit, ghost:view,alice', ?)",
                     (WS25, OLD))
        conn.executemany("INSERT INTO user_prefs VALUES (?, '', 'profile', '{}', ?)", [("alice", OLD), ("ghost", OLD)])
        conn.execute("INSERT INTO ai_usage (username, at, kind) VALUES ('bob', ?, 'chat')", (OLD,))
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at) "
                     "VALUES (?, 'https://nas', 'r', 't', 'alice', ?)", (WS25, OLD))
        conn.executemany("INSERT INTO jobs (id, owner, kind, state, created_at) VALUES (?, ?, 'export', 'done', ?)",
                         [("j1", "alice", OLD), ("j2", "ghost", OLD), ("j3", "", OLD)])
        conn.executemany("INSERT INTO mcp_oauth VALUES (?, ?, '{}', 9999999999)",
                         [("code", "k1"), ("consent", "k2"), ("cloud_login", "k3"), ("client", "k4")])
        conn.commit()
    (config.WORKSPACES_DIR / WS25).mkdir(parents=True)
    pages_db = config.WORKSPACES_DIR / WS25 / "pages.db"
    _legacy_pages_db(pages_db, [("p1", "root", "Shared page", {})])
    with closing(sqlite3.connect(str(pages_db))) as conn:
        for stmt in migrations._V25_PAGES_SCHEMA:
            conn.execute(stmt)
        conn.execute("INSERT INTO unified_blocks VALUES ('trash', NULL, 'a1', '', '{}', ?, ?)", (OLD, OLD))
        conn.execute("INSERT INTO unified_blocks VALUES ('gone', 'trash', 'a0', 'Gone', ?, ?, ?)",
                     (json.dumps({"deleted_at": OLD, "deleted_by": "alice"}), OLD, OLD))
        conn.executemany("INSERT INTO page_ops (page_id, seq, actor, at, ops) VALUES ('p1', ?, ?, ?, '[]')",
                         [(1, "alice", OLD), (2, "link:Visitor", OLD), (3, "mirror", OLD), (4, "ghost", OLD),
                          (5, "", OLD)])
        conn.execute("INSERT INTO deleted_pages VALUES ('gone', ?, 'bob')", (OLD,))
        conn.commit()
    tasks = config.BACKUPS_DIR / "tasks"
    tasks.mkdir(parents=True)
    for task_id, owner in ((TASK_A, "alice"), (TASK_GHOST, "ghost")):
        (tasks / f"{task_id}.json").write_text(json.dumps({"id": task_id, "owner": owner, "name": "Nightly"}))


def account_state() -> dict:
    """Everything step 25 rewrites, read back as names (``ids_by_name``
    maps them) — the users.db rows, the workspace's op log, tombstone (a
    deleted row of the change log once step 27 ran) and trashed page, the
    backup task owners."""
    with connect_users_db() as conn:
        rows = {
            table: sorted(conn.execute(f"SELECT {cols} FROM {table}").fetchall())
            for table, cols in (
                ("sessions", "token, user_id"), ("identities", "subject, user_id"),
                ("integration_tokens", "id, user_id"), ("publisher_sessions", "host, user_id"),
                ("workspaces", "id, created_by"), ("workspace_members", "user_id, role, added_by"),
                ("pending_memberships", "username, invited_by"), ("shares", "token, created_by"),
                ("share_users", "token, user_id, role"), ("user_prefs", "user_id, key"), ("ai_usage", "user_id, kind"),
                ("mirrors", "workspace_id, owner"), ("jobs", "id, owner"), ("mcp_oauth", "kind, key_hash"))}
    with closing(sqlite3.connect(str(config.WORKSPACES_DIR / WS25 / "pages.db"))) as conn:
        rows["page_ops"] = conn.execute("SELECT seq, actor FROM page_ops ORDER BY seq").fetchall()
        rows["deleted"] = conn.execute("SELECT page_id, actor FROM page_changes WHERE kind = 'deleted'").fetchall()
        rows["deleted_by"] = json.loads(conn.execute(
            "SELECT properties FROM unified_blocks WHERE id = 'gone'").fetchone()[0])["deleted_by"]
    rows["tasks"] = sorted(json.loads((config.BACKUPS_DIR / "tasks" / f"{t}.json").read_text())["owner"]
                           for t in (TASK_A, TASK_GHOST))
    return rows


def test_v25_keys_accounts_by_id(data_dir):
    # every column that named an account holds its id; a name no account
    # has is dropped with its row (a session, a pref, a job), or becomes ''
    # where the row stays; allowed_users becomes share_users rows
    from gamma.publisher_sessions import cipher

    build_v24_accounts()
    assert migrations.ensure_current()["applied"] == ["account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    ids = ids_by_name()
    alice, bob = ids["alice"], ids["bob"]
    assert len(set(ids.values())) == 3 and all(len(i) == 12 for i in ids.values())
    assert account_state() == {
        "sessions": [("tok-a", alice)],
        "identities": [("sub-a", alice)],
        "integration_tokens": [("t1", bob)],
        "publisher_sessions": [("www.nature.com", alice)],  # bob's no longer opened: dropped
        "workspaces": [(WS25, alice)],
        "workspace_members": sorted([(alice, "owner", alice), (bob, "editor", "")]),
        "pending_memberships": [("carol", alice)],  # the cloud username invited stays a name
        "shares": [(f"{WS25}.sh", alice)],  # step 32 puts the workspace in front of the token
        "share_users": sorted([(f"{WS25}.sh", bob, "edit"), (f"{WS25}.sh", alice, "view")]),
        "user_prefs": [(alice, "profile")],
        "ai_usage": [(bob, "chat")],
        "mirrors": [(WS25, alice)],
        "jobs": sorted([("j1", alice), ("j3", "")]),
        "mcp_oauth": [("client", "k4")],
        "page_ops": [(1, alice), (2, "link:Visitor"), (3, "mirror"), (4, ""), (5, "")],
        "deleted": [("gone", bob)],  # the tombstone's actor, carried into the change log (step 27)
        "deleted_by": alice,
        "tasks": sorted(["", alice]),
    }
    with connect_users_db() as conn:
        assert "allowed_users" not in {r[1] for r in conn.execute("PRAGMA table_info(shares)")}
        assert conn.execute("SELECT username FROM users WHERE id = ?", (alice,)).fetchone() == ("alice",)
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"identities_account", "idx_wm_user", "ai_usage_user_at", "idx_shares_page", "idx_shares_folder"} <= indexes
        sealed = conn.execute("SELECT encrypted FROM publisher_sessions").fetchone()[0]
    assert json.loads(cipher().decrypt(sealed.encode("ascii")))["user"] == alice  # re-sealed under the id
    # the code reads the new shape
    from gamma import auth, workspaces
    assert auth.session_lookup("tok-a")[:2] == (alice, "alice")
    assert workspaces.role_of(WS25, bob) == "editor"
    assert auth.share_access(auth.share_lookup(f"{WS25}.sh"), bob, False) == ("edit", "")

    assert migrations.ensure_current()["applied"] == []
    before = account_state()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 24")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable: nothing changes again
    assert account_state() == before and ids_by_name() == ids


def test_v25_resumes_after_a_crash(data_dir, monkeypatch):
    # users.db changes as one transaction: a failure inside it leaves the
    # old shape whole; one after it (the workspace pass) leaves the ids, and
    # the rerun finishes the files with the same ids
    build_v24_accounts()
    real_reseal, real_each = migrations._v25_reseal_publisher_sessions, migrations._each_pages_db

    def fail(*_args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_v25_reseal_publisher_sessions", fail)
    with pytest.raises(migrations.MigrationError, match="account_ids"):
        migrations.ensure_current()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        assert "id" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "allowed_users" in {r[1] for r in conn.execute("PRAGMA table_info(shares)")}
    monkeypatch.setattr(migrations, "_v25_reseal_publisher_sessions", real_reseal)
    monkeypatch.setattr(migrations, "_each_pages_db", fail)
    with pytest.raises(migrations.MigrationError, match="account_ids"):
        migrations.ensure_current()
    assert migrations.data_version() == 24
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        ids = dict(conn.execute("SELECT username, id FROM users").fetchall())
    monkeypatch.setattr(migrations, "_each_pages_db", real_each)
    assert migrations.ensure_current()["applied"] == ["account_ids", "block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    assert ids_by_name() == ids
    state = account_state()
    assert state["page_ops"][0] == (1, ids["alice"]) and state["tasks"] == sorted(["", ids["alice"]])


WS26 = "wsBlocks26"


def test_v26_gives_blocks_their_hot_fields(data_dir):
    # page_id filled in by the parent walk (a page's own id on it and below
    # it, kept in Recently deleted, '' on the reserved rows and on a row whose
    # parent is gone); kind and doc_id generated; the three indexes. Run
    # again, nothing changes.
    from conftest import page_id_drift

    v24_users_db()
    migrations.ensure_current()
    (config.WORKSPACES_DIR / WS26).mkdir(parents=True)
    pages_db = config.WORKSPACES_DIR / WS26 / "pages.db"
    _legacy_pages_db(pages_db, [
        ("trash", None, "", {}),
        ("pdf", "root", "A paper", {"doc_id": "d" * 24, "folder": "lab"}),
        ("hl", "pdf", "a quote", {"highlight_id": "h1", "pdf_position": {"pageNumber": 1}}),
        ("deep", "hl", "a reply", {}),
        ("ink", "pdf", "", {"ink_url": "/api/uploads/x.ink"}),
        ("box", "pdf", "boxed", {"text_box": {"x": 1}}),
        ("sheet", "pdf", "", {"sheet": {"size": "a4"}}),
        ("link", "pdf", "a link", {"highlight_id": "h2", "link_url": "", "link_page_id": "pdf"}),
        ("gone", "trash", "Trashed", {"deleted_at": OLD, "deleted_by": ""}),
        ("goneKid", "gone", "kept", {}),
        ("orphan", "nowhere", "lost", {}),
    ])
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 25")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]

    def blocks():
        with closing(sqlite3.connect(str(pages_db))) as conn:
            assert not page_id_drift(conn)
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'unified_blocks'")}
            assert {"idx_ub_parent", "idx_ub_page", "idx_ub_kind", "idx_ub_doc"} <= indexes
            return {r[0]: r[1:] for r in conn.execute("SELECT id, page_id, kind, doc_id FROM unified_blocks")}

    before = blocks()
    from gamma.normalize import tree_block_id
    assert before == {
        "root": ("", None, None), "trash": ("", None, None),
        "pdf": ("pdf", "page", "d" * 24), "hl": ("pdf", "highlight", None), "deep": ("pdf", "note", None),
        "ink": ("pdf", "ink", None), "box": ("pdf", "text_box", None), "sheet": ("pdf", "sheet", None),
        "link": ("pdf", "link", None), "gone": ("gone", "page", None), "goneKid": ("gone", "note", None),
        "orphan": ("", "note", None),
        # (step 29: the folder trees, the "lab" label of the paper a folder block)
        "folders": ("", None, None), "labels": ("", None, None),
        tree_block_id("folders", ("lab",)): ("folders", "folder", None),
    }
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 25")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["block_columns", "page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    assert blocks() == before
    with closing(sqlite3.connect(str(pages_db))) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_xinfo(unified_blocks)")]
    assert cols == ["id", "parent_id", "position", "content", "properties", "created_at", "updated_at",
                    "page_id", "kind", "doc_id"]  # the shape a fresh workspace gets (db.PAGES_SCHEMA)


WS27 = "wsChanges27"
NEW = "2025-06-01T00:00:00.000000Z"


def test_v27_folds_tombstones_into_the_change_log(data_dir):
    # one row per page, seqs in the order the pages were last written (live
    # in the library, deleted in Recently deleted with its tombstone's time
    # and actor), then the tombstones of pages that are gone in the order
    # they went; deleted_pages is dropped; a mirror's cursors start over.
    # Run again, nothing changes.
    from gamma.normalize import block_columns
    from gamma.routers.sync import changes

    v24_users_db()
    migrations.ensure_current()
    (config.WORKSPACES_DIR / WS27).mkdir(parents=True)
    pages_db = config.WORKSPACES_DIR / WS27 / "pages.db"
    _legacy_pages_db(pages_db, [
        ("trash", None, "", {}),
        ("newer", "root", "Edited last", {}),
        ("older", "root", "Edited first", {}),
        ("note", "older", "a note", {}),
        ("binned", "trash", "In the trash", {"deleted_at": OLD, "deleted_by": "by-props"}),
    ])
    with closing(sqlite3.connect(str(pages_db))) as conn:  # the version-26 file
        for stmt in migrations._V25_PAGES_SCHEMA:
            conn.execute(stmt)
        block_columns(conn)
        conn.execute("UPDATE unified_blocks SET updated_at = ? WHERE id = 'newer'", (NEW,))
        conn.executemany("INSERT INTO deleted_pages VALUES (?, ?, ?)", [
            ("binned", NEW, "trasher"), ("goneLate", NEW, "late"), ("goneEarly", OLD, "early"),
            ("older", OLD, "stale")])  # a tombstone left on a live page: the page wins
        conn.commit()
    with connect_users_db() as conn:
        conn.execute("INSERT INTO workspaces (id, name, kind, created_by, created_at) VALUES (?, 'w', 'personal', '', ?)",
                     (WS27, OLD))
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, remote_cursor, "
                     "local_cursor, created_at) VALUES (?, 'https://nas', 'r', 't', '', ?, ?, ?)",
                     (WS27, f"{OLD}|older", OLD, OLD))
        conn.execute("PRAGMA user_version = 26")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]

    def log():
        with closing(sqlite3.connect(str(pages_db))) as conn:
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'deleted_pages'").fetchone()
            return conn.execute("SELECT seq, page_id, kind, at, actor FROM page_changes ORDER BY seq").fetchall()

    before = log()
    assert before[:5] == [
        (1, "binned", "deleted", NEW, "trasher"), (2, "older", "live", OLD, ""),
        (3, "newer", "live", NEW, ""), (4, "goneEarly", "deleted", OLD, "early"),
        (5, "goneLate", "deleted", NEW, "late"),
    ]
    assert [row[:3] for row in before[5:]] == [(6, "folders", "live"), (7, "labels", "live")]  # step 29
    with connect_users_db() as conn:
        assert conn.execute("SELECT remote_cursor, local_cursor FROM mirrors WHERE workspace_id = ?",
                            (WS27,)).fetchone() == ("", "")
    with closing(sqlite3.connect(str(pages_db))) as conn:  # the feed reads it
        feed = changes(conn, "", 100)
    assert [p["id"] for p in feed["pages"]] == ["older", "newer", "folders", "labels"] and feed["cursor"] == "7"
    assert [d["id"] for d in feed["deleted"]] == ["binned", "goneEarly", "goneLate"]
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 26")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["page_changes", "chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    assert log() == before


WS28 = "wsChats28"


def test_v28_moves_the_chats_and_builds_the_notes_index(data_dir):
    # The chats leave data.db for pages.db (block_id becomes bucket; a row
    # without a title gets ''), the notes index is built in pages.db from the
    # blocks (every block inside a page, a page's own row not; a page in
    # Recently deleted keeps its rows), and data.db keeps only what it can
    # rebuild. A step cut short between its two commits (the chats copied,
    # still in data.db) and a second run change nothing more.
    from conftest import notes_index_drift
    from gamma.normalize import block_columns, page_changes

    v24_users_db()
    migrations.ensure_current()
    (config.WORKSPACES_DIR / WS28).mkdir(parents=True)
    pages_db, data_db = (config.WORKSPACES_DIR / WS28 / name for name in ("pages.db", "data.db"))
    _legacy_pages_db(pages_db, [
        ("trash", None, "", {}),
        ("page", "root", "Wombat page", {}),
        ("note", "page", "a wombat at dusk", {}),
        ("hl", "page", "wombat highlight", {"highlight_id": "h1"}),
        ("binned", "trash", "Wombat bin", {"deleted_at": OLD, "deleted_by": ""}),
        ("binnedKid", "binned", "a binned wombat", {}),
    ])
    with closing(sqlite3.connect(str(pages_db))) as conn:  # the version-27 file
        for stmt in migrations._V25_PAGES_SCHEMA:
            conn.execute(stmt)
        block_columns(conn)
        page_changes(conn)
    with closing(sqlite3.connect(str(data_db))) as conn:  # data.db as step 27 left it
        conn.execute("CREATE TABLE chats (block_id TEXT PRIMARY KEY, messages TEXT NOT NULL, "
                     "updated_at TEXT NOT NULL, title TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE chat_history (id TEXT PRIMARY KEY, bucket TEXT NOT NULL, title TEXT NOT NULL "
                     "DEFAULT '', messages TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO chats VALUES (?, ?, ?, ?)", [
            ("page", '[{"role": "user", "text": "hi"}]', OLD, "Named"), ("home:lab/x", "[]", OLD, "")])
        conn.execute("INSERT INTO chat_history VALUES ('h1', 'page', 'Earlier', '[]', ?, ?)", (OLD, OLD))
        conn.execute("CREATE VIRTUAL TABLE block_fts USING fts5(block_id UNINDEXED, page_id UNINDEXED, content)")
        conn.execute("INSERT INTO block_fts VALUES ('note', 'page', 'a stale wombat')")
        conn.execute("CREATE TABLE block_fts_meta (page_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, "
                     "ver INTEGER NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE block_fts_rows (page_id TEXT NOT NULL, fts_rowid INTEGER NOT NULL, "
                     "PRIMARY KEY (page_id, fts_rowid)) WITHOUT ROWID")
        conn.execute("CREATE TABLE page_snaps (page_id TEXT PRIMARY KEY, img TEXT NOT NULL, at TEXT NOT NULL)")
        conn.execute("INSERT INTO page_snaps VALUES ('page', 'data:', ?)", (OLD,))
        conn.commit()

    def stamp(version):
        with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()

    def state():
        with closing(sqlite3.connect(str(pages_db))) as conn:
            register_functions(conn)
            assert not notes_index_drift(conn)
            hits = conn.execute("SELECT block_id, page_id, content FROM block_fts WHERE block_fts MATCH 'wombat' "
                                "ORDER BY block_id").fetchall()
            chats = conn.execute("SELECT bucket, messages, updated_at, title FROM chats ORDER BY bucket").fetchall()
            history = conn.execute("SELECT id, bucket, title FROM chat_history").fetchall()
        with closing(sqlite3.connect(str(data_db))) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return hits, chats, history, tables

    stamp(27)
    assert migrations.ensure_current()["applied"] == ["chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    before = state()
    hits, chats, history, tables = before
    assert hits == [("binnedKid", "binned", "a binned wombat"), ("hl", "page", "wombat highlight"),
                    ("note", "page", "a wombat at dusk")]  # no page row, the stale data.db text gone
    from gamma.normalize import tree_block_id
    lab_x = tree_block_id("folders", ("lab", "x"))  # (step 29: a folder chat's bucket is its folder's id)
    assert chats == sorted([(lab_x, "[]", OLD, ""), ("page", '[{"role": "user", "text": "hi"}]', OLD, "Named")])
    assert history == [("h1", "page", "Earlier")]
    assert tables == {"page_snaps"}

    # Cut short after the copy: the chats are in both files.
    with closing(sqlite3.connect(str(data_db))) as conn:
        conn.execute("CREATE TABLE chats (block_id TEXT PRIMARY KEY, messages TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO chats VALUES ('page', '[]', ?)", (NEW,))
        conn.commit()
    stamp(27)
    assert migrations.ensure_current()["applied"] == ["chats_and_notes_index", "folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    assert state() == before


WS29 = "wsFolders29"
# unified_blocks' kind as steps 26-28 defined it, frozen: no folder / label cases
_V28_KIND = """kind TEXT GENERATED ALWAYS AS (CASE
            WHEN parent_id IS NULL THEN NULL
            WHEN parent_id IN ('root', 'trash') THEN 'page'
            WHEN json_type(properties, '$.ink_url') IS NOT NULL THEN 'ink'
            WHEN json_type(properties, '$.text_box') = 'object' THEN 'text_box'
            WHEN json_type(properties, '$.sheet') = 'object' THEN 'sheet'
            WHEN json_extract(properties, '$.link_url') != ''
                OR json_extract(properties, '$.link_page_id') != '' THEN 'link'
            WHEN json_extract(properties, '$.highlight_id') != '' THEN 'highlight'
            ELSE 'note' END) VIRTUAL"""


def _v28_pages_db(path: Path, blocks, kind=_V28_KIND):
    """A pages.db as step 28 left it: the block table with the hot fields
    of its time (``kind``, ``_V28_KIND`` unless a later step's), the change
    log, the chats, the notes index."""
    from gamma.db import CHATS_SCHEMA
    from gamma.normalize import block_fts, page_changes

    _legacy_pages_db(path, blocks)
    with closing(sqlite3.connect(str(path))) as conn:
        register_functions(conn)
        for stmt in migrations._V25_PAGES_SCHEMA:
            conn.execute(stmt)
        conn.execute("ALTER TABLE unified_blocks ADD COLUMN page_id TEXT NOT NULL DEFAULT ''")
        conn.execute(f"ALTER TABLE unified_blocks ADD COLUMN {kind}")
        conn.execute("ALTER TABLE unified_blocks ADD COLUMN doc_id TEXT GENERATED ALWAYS AS "
                     "(json_extract(properties, '$.doc_id')) VIRTUAL")
        conn.execute("UPDATE unified_blocks SET page_id = CASE WHEN parent_id IN ('root', 'trash') THEN id "
                     "WHEN parent_id IS NULL THEN '' ELSE parent_id END")
        for stmt in ("CREATE INDEX idx_ub_page ON unified_blocks(page_id)",
                     "CREATE INDEX idx_ub_kind ON unified_blocks(kind)",
                     "CREATE INDEX idx_ub_doc ON unified_blocks(doc_id) WHERE doc_id IS NOT NULL"):
            conn.execute(stmt)
        page_changes(conn)
        for stmt in CHATS_SCHEMA:
            conn.execute(stmt)
        conn.commit()
        block_fts(conn)


def test_v29_makes_folders_and_labels_blocks(data_dir):
    # Folder blocks for every path in use (the pages' labels, the folder chats'
    # buckets — an empty folder's chat makes its folder), nested by segment,
    # siblings by name; label blocks for every label name; each page filed by
    # id and not stamped; the chats moved to their folder's id; the kind
    # column gains the folder / label cases; the two trees get their change-log
    # row. In users.db a folder share names its folder's id (one whose folder
    # is gone goes), and the pinned-folders prefs become pins on the folder
    # blocks — the newest of every member's — and go. Run again, nothing changes.
    from conftest import notes_index_drift, page_id_drift
    from gamma.normalize import tree_block_id

    v24_users_db()
    migrations.ensure_current()
    (config.WORKSPACES_DIR / WS29).mkdir(parents=True)
    pages_db = config.WORKSPACES_DIR / WS29 / "pages.db"
    _v28_pages_db(pages_db, [
        ("trash", None, "", {}),
        ("p1", "root", "Filed twice", {"folder": "Physics/QEC, Math ", "category": "read, todo", "doi": "x"}),
        ("p1note", "p1", "a qubit note", {}),
        ("p2", "root", "Filed once", {"folder": "Physics", "category": "todo"}),
        ("p3", "trash", "Binned", {"folder": "Old/Archive", "deleted_at": OLD, "deleted_by": ""}),
        ("p4", "root", "Unfiled", {"folder": "", "category": ""}),
    ])
    with closing(sqlite3.connect(str(pages_db))) as conn:
        conn.executemany("INSERT INTO chats (bucket, messages, updated_at, title) VALUES (?, ?, ?, ?)", [
            ("home:Physics/QEC", '[{"role": "user", "text": "qec"}]', OLD, "QEC chat"),
            ("home:Empty/Chat", '[{"role": "user", "text": "empty"}]', OLD, ""),
            ("home", '[{"role": "user", "text": "root"}]', OLD, ""),
            ("home:", '[{"role": "user", "text": "stray"}]', OLD, "Stray")])
        conn.execute("INSERT INTO chat_history VALUES ('hh1', 'home:Physics', 'Old one', '[]', ?, ?)", (OLD, OLD))
        conn.commit()
        seqs = dict(conn.execute("SELECT page_id, seq FROM page_changes").fetchall())
    with connect_users_db() as conn:
        for name in ("alice29", "bob29"):
            conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, 'x', ?)",
                         (f"id-{name}", name, OLD))
        conn.execute("INSERT INTO workspaces (id, name, kind, created_by, created_at) VALUES (?, 'w', 'personal', '', ?)",
                     (WS29, OLD))
        conn.executemany("INSERT INTO shares (token, workspace_id, page_id, folder, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, '', ?)", [("shQ", WS29, "", "Physics/QEC", OLD),
                                                        ("shGone", WS29, "", "Gone/Path", OLD),
                                                        ("shTwin", WS29, "", "physics/qec", OLD),  # resolves to shQ's folder
                                                        ("shPage", WS29, "p2", "", OLD)])
        conn.executemany("INSERT INTO share_users VALUES (?, 'id-bob29', 'view')", [("shGone",), ("shTwin",)])
        conn.executemany("INSERT INTO user_prefs VALUES (?, ?, 'pinned-folders', ?, ?)", [
            ("id-alice29", WS29, json.dumps([{"path": "Physics", "at": "2025-01-01T00:00:00.000Z"},
                                              {"path": "Nowhere", "at": "2025-01-02T00:00:00.000Z"}]), OLD),
            ("id-bob29", WS29, json.dumps([{"path": "Physics", "at": "2025-06-01T00:00:00.000Z"},
                                            {"path": "Old/Archive", "at": "2025-02-01T00:00:00.000Z"}]), OLD)])
        conn.execute("INSERT INTO user_prefs VALUES ('id-bob29', ?, 'open-tabs', '[]', ?)", (WS29, OLD))
        conn.execute("PRAGMA user_version = 28")
        conn.commit()

    def folder(*names):
        return tree_block_id("folders", names)

    def label(name):
        return tree_block_id("labels", (name,))

    assert migrations.ensure_current()["applied"] == ["folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]

    def state():
        with closing(sqlite3.connect(str(pages_db))) as conn:
            register_functions(conn)
            assert not page_id_drift(conn) and not notes_index_drift(conn)
            blocks = {r[0]: r[1:] for r in conn.execute(
                "SELECT id, parent_id, content, kind, properties, updated_at FROM unified_blocks")}
            chats = sorted(conn.execute("SELECT bucket, title FROM chats").fetchall())
            history = sorted(conn.execute("SELECT bucket, title FROM chat_history").fetchall())
            changes = dict(conn.execute("SELECT page_id, seq FROM page_changes").fetchall())
            order = [r[0] for r in conn.execute(
                "SELECT content FROM unified_blocks WHERE parent_id = 'folders' ORDER BY position")]
        with connect_users_db() as conn:
            shares = sorted(conn.execute("SELECT token, folder FROM shares WHERE workspace_id = ?", (WS29,)).fetchall())
            invited = conn.execute("SELECT token FROM share_users").fetchall()
            prefs = sorted(conn.execute("SELECT user_id, key FROM user_prefs WHERE workspace_id = ?", (WS29,)).fetchall())
        return blocks, chats, history, changes, order, shares, invited, prefs

    before = state()
    blocks, chats, history, changes, order, shares, invited, prefs = before
    assert blocks["folders"][:3] == (None, "", None) and blocks["labels"][:3] == (None, "", None)
    assert order == ["Empty", "Math", "Old", "Physics"]  # siblings by name
    assert {k: v[:3] for k, v in blocks.items() if v[2] in ("folder", "label")} == {
        folder("Physics"): ("folders", "Physics", "folder"), folder("Physics", "QEC"): (folder("Physics"), "QEC", "folder"),
        folder("Math"): ("folders", "Math", "folder"), folder("Old"): ("folders", "Old", "folder"),
        folder("Old", "Archive"): (folder("Old"), "Archive", "folder"),
        folder("Empty"): ("folders", "Empty", "folder"), folder("Empty", "Chat"): (folder("Empty"), "Chat", "folder"),
        label("read"): ("labels", "read", "label"), label("todo"): ("labels", "todo", "label")}
    props = {pid: json.loads(blocks[pid][3]) for pid in ("p1", "p2", "p3", "p4")}
    assert props["p1"] == {"folders": [folder("Physics", "QEC"), folder("Math")],
                           "labels": [label("read"), label("todo")], "doi": "x"}
    assert props["p2"] == {"folders": [folder("Physics")], "labels": [label("todo")]}
    assert props["p3"] == {"folders": [folder("Old", "Archive")], "deleted_at": OLD, "deleted_by": ""}
    assert props["p4"] == {}
    assert all(blocks[pid][4] == OLD for pid in props)  # refiling is no edit: nothing stamped
    assert {pid: changes[pid] for pid in seqs} == seqs and {"folders", "labels"} <= changes.keys()
    assert json.loads(blocks[folder("Physics")][3]) == {"pinned": "2025-06-01T00:00:00.000Z"}  # the newest pin
    assert json.loads(blocks[folder("Old", "Archive")][3]) == {"pinned": "2025-02-01T00:00:00.000Z"}
    assert chats == sorted([(folder("Physics", "QEC"), "QEC chat"), (folder("Empty", "Chat"), ""), ("home", "")])
    assert history == sorted([(folder("Physics"), "Old one"), ("home", "Stray")])  # "home" was taken: filed
    # one share per folder: the twin went (step 32 puts the workspace in front of the tokens)
    assert shares == sorted([(f"{WS29}.shQ", folder("Physics", "QEC")), (f"{WS29}.shPage", "")])
    assert invited == []
    assert prefs == [("id-bob29", "open-tabs")]

    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 28")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_blocks", "highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    assert state() == before


WS30 = "wsHighlights30"
# unified_blocks' kind as step 29 defined it, frozen: a highlight by its highlight_id
_V29_KIND = """kind TEXT GENERATED ALWAYS AS (CASE
            WHEN parent_id IS NULL THEN NULL
            WHEN parent_id IN ('root', 'trash') THEN 'page'
            WHEN page_id = 'folders' THEN 'folder'
            WHEN page_id = 'labels' THEN 'label'
            WHEN json_type(properties, '$.ink_url') IS NOT NULL THEN 'ink'
            WHEN json_type(properties, '$.text_box') = 'object' THEN 'text_box'
            WHEN json_type(properties, '$.sheet') = 'object' THEN 'sheet'
            WHEN json_extract(properties, '$.link_url') != ''
                OR json_extract(properties, '$.link_page_id') != '' THEN 'link'
            WHEN json_extract(properties, '$.highlight_id') != '' THEN 'highlight'
            ELSE 'note' END) VIRTUAL"""


def _scaled(x1, y1, x2, y2, w=600, h=800, page=None, **extra):
    """A rect as positions before step 30 kept it: its own render size and page."""
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": w, "height": h,
            **({"pageNumber": page} if page else {}), **extra}


def _v29_highlights_db(path: Path, doc: str):
    """A pages.db as step 29 left it, with highlights, links, ink, a text
    box and attachments in the shapes before step 30."""
    from gamma.normalize import folder_blocks

    sized = _scaled(1, 1, 2, 2, w=100, h=100)
    _v28_pages_db(path, [
        ("trash", None, "", {}),
        ("pdf", "root", "A paper", {"doc_id": doc, "source_url": f"/api/uploads/{doc}.pdf"}),
        ("ext", "root", "Proxied", {"doc_id": "e" * 24, "source_url": "https://x.test/p.pdf"}),
        ("copy", "root", "A copy", {"source_url": f"/api/uploads/{doc}.pdf"}),
        ("pdf2", "root", "Cited", {"doc_id": "f" * 24}),
        ("hl", "pdf", "a quote", {"highlight_id": "hl", "quote": "q", "pdf_page": 2, "pdf_position": {
            "pageNumber": 2, "boundingRect": _scaled(10, 20, 110, 60, page=2),
            "rects": [_scaled(10, 20, 110, 40, page=2), _scaled(20, 80, 120, 120, w=1200, h=1600, page=2)]}}),
        ("hl2", "pdf", "a copy's", {"highlight_id": "copied-id", "quote": "", "pdf_page": 1, "pdf_position": {
            "boundingRect": _scaled(5, 5, 50, 50, w=612, h=792, page=1, area=True), "rects": []}}),
        ("tgt", "pdf2", "the cited passage", {"highlight_id": "t-hl", "pdf_page": 4, "pdf_position": {
            "boundingRect": {"x1": 1, "y1": 1, "x2": 2, "y2": 2}, "rects": [sized]}}),
        ("link", "pdf", "", {"highlight_id": "lk", "link_url": "", "link_page_id": "pdf2", "link_highlight_id": "t-hl",
                             "pdf_page": 1, "pdf_position": {"pageNumber": 1, "boundingRect": sized, "rects": [sized]}}),
        ("deadlink", "pdf", "", {"highlight_id": "dl", "link_url": "https://x.test", "link_page_id": "",
                                 "link_highlight_id": "", "pdf_position": {"pageNumber": 1, "boundingRect": sized,
                                                                           "rects": [sized]}}),
        ("attached", "pdf", "imported", {"highlight_id": "att", "quote": "imported", "pdf_page": 3,
                                         "pdf_position": None, "linked_highlight_id": "copied-id"}),
        ("unlinked", "pdf", "", {"highlight_id": "un", "pdf_page": 3, "linked_highlight_id": "nowhere"}),
        ("ink", "pdf", "", {"ink_url": "/api/uploads/a.ink", "ink_strokes": 1, "pdf_page": 1, "pdf_position": {
            "pageNumber": 1, "boundingRect": _scaled(3, 4, 30, 40, w=612, h=792, page=1),
            "rects": [_scaled(3, 4, 30, 40, w=612, h=792, page=1)]}}),
        ("box", "pdf", "typed", {"text_box": {"x": 1}, "pdf_page": 1}),
        ("paged", "pdf", "a note once on a page", {"pdf_page": 5}),
        ("pageless", "pdf", "", {"highlight_id": "q", "quote": "x"}),
    ], kind=_V29_KIND)
    with closing(sqlite3.connect(str(path))) as conn:
        register_functions(conn)
        folder_blocks(conn)  # the reserved trees step 29 wrote


def assert_highlight_shape(props: dict, doc: str) -> None:
    """What step 30 makes of ``_v29_highlights_db``'s blocks (``{id: properties}``)."""
    assert props["pdf"] == {"doc_id": doc}
    assert props["ext"] == {"doc_id": "e" * 24, "source_url": "https://x.test/p.pdf"}
    assert props["copy"] == {"source_url": f"/api/uploads/{doc}.pdf"}  # no doc_id: not derivable
    assert props["hl"] == {"quote": "q", "pdf_position": {
        "pageNumber": 2, "width": 600, "height": 800, "boundingRect": {"x1": 10, "y1": 20, "x2": 110, "y2": 60},
        "rects": [{"x1": 10, "y1": 20, "x2": 110, "y2": 40}, {"x1": 10.0, "y1": 40.0, "x2": 60.0, "y2": 60.0}]}}
    assert props["hl2"] == {"quote": "", "pdf_position": {
        "pageNumber": 1, "width": 612, "height": 792, "boundingRect": {"x1": 5, "y1": 5, "x2": 50, "y2": 50},
        "rects": [{"x1": 5, "y1": 5, "x2": 50, "y2": 50}], "area": True}}
    bare = {"x1": 1, "y1": 1, "x2": 2, "y2": 2}
    assert props["tgt"] == {"pdf_position": {"pageNumber": 4, "width": 100, "height": 100,
                                             "boundingRect": bare, "rects": [bare]}}
    assert props["link"] == {"link_url": "", "link_page_id": "pdf2", "link_block_id": "tgt", "pdf_position": {
        "pageNumber": 1, "width": 100, "height": 100, "boundingRect": bare, "rects": [bare]}}
    assert props["deadlink"] == {"link_url": "https://x.test", "link_page_id": "", "pdf_position": {
        "pageNumber": 1, "width": 100, "height": 100, "boundingRect": bare, "rects": [bare]}}
    assert props["attached"] == {"quote": "imported", "pdf_position": {"pageNumber": 3},
                                 "linked_highlight_id": "hl2"}
    assert props["unlinked"] == {"pdf_position": {"pageNumber": 3}}
    assert props["ink"] == {"ink_url": "/api/uploads/a.ink", "ink_strokes": 1, "pdf_position": {
        "pageNumber": 1, "width": 612, "height": 792, "boundingRect": {"x1": 3, "y1": 4, "x2": 30, "y2": 40},
        "rects": [{"x1": 3, "y1": 4, "x2": 30, "y2": 40}]}}
    assert props["box"] == {"text_box": {"x": 1}, "pdf_page": 1}  # a box's only page
    assert props["paged"] == {} and props["pageless"] == {"quote": "x"}


def test_v30_gives_highlights_their_shape(data_dir):
    # The block id is the highlight's: highlight_id goes, a link region's
    # link_highlight_id becomes link_block_id (that highlight's block on the
    # page it links to; one that names none goes), an attached note's
    # linked_highlight_id names the block. pdf_position keeps the page and
    # its size once (a rect measured at another size scaled into it, the
    # page from pdf_page when the position named none, area: true at the
    # top); pdf_page goes but on a text box; a highlight with no position
    # but a page keeps its page as one, one with neither is a note. The
    # stored copy's source_url goes. The kind column's highlight case reads
    # pdf_position. Nothing is stamped or touched, users.db is untouched,
    # and a second run changes nothing.
    from conftest import notes_index_drift, page_changes_drift, page_id_drift

    doc = "d" * 24
    v24_users_db()
    migrations.ensure_current()
    (config.WORKSPACES_DIR / WS30).mkdir(parents=True)
    pages_db = config.WORKSPACES_DIR / WS30 / "pages.db"
    _v29_highlights_db(pages_db, doc)
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 29")
        conn.commit()
        users_before = list(conn.iterdump())

    assert migrations.ensure_current()["applied"] == ["highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]

    def state():
        with closing(sqlite3.connect(str(pages_db))) as conn:
            register_functions(conn)
            assert not page_id_drift(conn) and not notes_index_drift(conn) and not page_changes_drift(conn)
            blocks = {r[0]: (json.loads(r[1]), r[2], r[3]) for r in conn.execute(
                "SELECT id, properties, kind, updated_at FROM unified_blocks")}
            changes = conn.execute("SELECT page_id, seq, at FROM page_changes ORDER BY seq").fetchall()
        return blocks, changes

    before = state()
    blocks, _changes = before
    assert_highlight_shape({bid: b[0] for bid, b in blocks.items()}, doc)
    assert {bid: blocks[bid][1] for bid in ("hl", "hl2", "tgt", "link", "deadlink", "attached", "unlinked", "ink",
                                            "box", "paged", "pageless")} == {
        "hl": "highlight", "hl2": "highlight", "tgt": "highlight", "link": "link", "deadlink": "link",
        "attached": "highlight", "unlinked": "highlight", "ink": "ink", "box": "text_box", "paged": "note",
        "pageless": "note"}
    assert all(updated == OLD for bid, (_p, _k, updated) in blocks.items()
               if bid not in ("folders", "labels"))  # a shape is no edit: nothing stamped
    with closing(sqlite3.connect(str(pages_db))) as conn:
        table_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'unified_blocks'").fetchone()[0]
        assert "$.pdf_position') = 'object' THEN 'highlight'" in table_sql and "highlight_id" not in table_sql
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_ub_kind'").fetchone()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        assert list(conn.iterdump()) == users_before  # nothing but its version

    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 29")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["highlight_shape", "session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    assert state() == before


def _legacy_pages_db(path: Path, blocks):
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("""CREATE TABLE unified_blocks (id TEXT PRIMARY KEY, parent_id TEXT, position TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO unified_blocks VALUES ('root', NULL, 'a0', '', '{}', ?, ?)", (OLD, OLD))
        for bid, parent, content, props in blocks:
            conn.execute("INSERT INTO unified_blocks VALUES (?, ?, 'a0', ?, ?, ?, ?)",
                         (bid, parent, content, json.dumps(props), OLD, OLD))
        conn.commit()


def test_fresh_directory_needs_no_migration(data_dir):
    assert migrations.status()["fresh"] is True
    assert migrations.ensure_current()["applied"] == []
    connect_users_db().close()  # created at the current version
    assert migrations.data_version() == SCHEMA_VERSION
    assert migrations.ensure_current()["applied"] == []


def test_a_directory_below_the_floor_is_refused_with_guidance(data_dir):
    """A data directory older than MIN_UPGRADABLE is refused untouched, and
    the refusal says what to run: the release UPGRADE_VIA names, which
    carries the steps this build no longer has."""
    v24_users_db(version=migrations.MIN_UPGRADABLE - 1)
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        before = list(conn.iterdump())
    with pytest.raises(migrations.TooOldDataError) as caught:
        migrations.ensure_current()
    assert caught.value.version == migrations.MIN_UPGRADABLE - 1
    guide = migrations.guidance(caught.value)
    assert guide["title"].startswith("This Gamma needs an earlier release")
    assert "Nothing has been changed" in guide["summary"]
    assert any(migrations.UPGRADE_VIA["image"] in step and "docker compose" in step for step in guide["steps"])
    assert any("Desktop app" in step for step in guide["steps"])
    assert guide["data_dir"] == str(config.DATA_DIR)
    with pytest.raises(SchemaOutdated):  # nothing but the runner may open it, and the runner refused
        connect_users_db()
    assert migrations.status()["version"] == migrations.MIN_UPGRADABLE - 1
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        assert list(conn.iterdump()) == before
    # The server comes up as one page saying the same, at every address and
    # as JSON under /api, instead of exiting into a restart loop.
    from fastapi.testclient import TestClient
    with TestClient(app_mod.create_app()) as client:
        page = client.get("/some/page?x=1")
        assert page.status_code == 503 and "text/html" in page.headers["content-type"]
        assert guide["title"] in page.text and migrations.UPGRADE_VIA["image"] in page.text
        api = client.get("/api/session")
        assert api.status_code == 503 and api.json()["error"] == "data_directory_not_upgradable"
        assert api.json()["steps"] == guide["steps"]
        assert client.post("/api/blocks", json={}).status_code == 503
        assert client.get("/api/health").status_code == 503
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        assert list(conn.iterdump()) == before


def test_newer_data_directory_is_refused(data_dir):
    build_v24_accounts()
    migrations.ensure_current()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
    with pytest.raises(migrations.NewerDataError) as caught:
        migrations.ensure_current()
    guide = migrations.guidance(caught.value)
    assert guide["title"].startswith("This data directory was written by a newer Gamma")
    assert any("--restore" in step for step in guide["steps"])
    # ...and the app serves that instead of its routes
    from fastapi.testclient import TestClient
    with TestClient(app_mod.create_app()) as client:
        assert client.get("/").status_code == 503 and guide["title"] in client.get("/").text


def test_a_failed_step_is_explained_with_its_snapshot(data_dir, monkeypatch):
    build_v24_accounts()

    def broken(conn):
        raise RuntimeError("disk full (simulated)")

    monkeypatch.setattr(migrations, "STEPS", [(v, n, broken if v == 26 else fn) for v, n, fn in migrations.STEPS])
    with pytest.raises(migrations.MigrationError, match="disk full") as caught:
        migrations.ensure_current()
    assert caught.value.step == "26 (block_columns)" and caught.value.version == 25
    guide = migrations.guidance(caught.value)
    assert guide["title"].startswith("The upgrade of your data directory stopped")
    assert "schema version 25" in guide["summary"] and "resumes" in guide["summary"]
    assert guide["snapshot"] == caught.value.snapshot and Path(guide["snapshot"]).is_dir()
    assert any(Path(guide["snapshot"]).name in step for step in guide["steps"])


def test_backups_are_pruned_only_on_request(data_dir, monkeypatch):
    v24_users_db()
    monkeypatch.setattr(backups, "KEEP_BACKUPS", 2)
    backups.create("a")
    backups.create("b")   # a second one within the same second gets the next stamp
    backups.create("c")
    names = [b["name"] for b in backups.list_backups()]
    assert len(names) == 3  # hand-made backups are never pruned by themselves...
    assert backups.prune_backups() == [] and backups.prune_backups(keep=0) == []  # ...nor on request
    auto = [backups.create(f"v{n}", auto=True)["name"] for n in (1, 2, 3)]  # the runner's are
    assert backups.prune_backups() == auto[:1]
    assert backups.prune_backups(keep=0) == auto[1:] and [b["name"] for b in backups.list_backups()] == names


def test_backup_with_uploads_zip_and_restore(data_dir):
    build_v24_accounts()
    uploads = data_dir / "workspaces" / WS25 / "uploads"
    uploads.mkdir()
    for name in ("docA.pdf", "docB.pdf"):
        (uploads / name).write_bytes(b"%PDF-1.4 " + name.encode())
    migrations.ensure_current()
    pdf = uploads / "docA.pdf"
    b = backups.create("full", uploads=True)
    assert b["uploads"] is True and b["upload_files"] == 2 and b["size_bytes"] > 0
    assert (Path(b["path"]) / "workspaces" / WS25 / "uploads" / "docA.pdf").read_bytes() == pdf.read_bytes()
    assert backups.backup_path("../../etc") is None and backups.info("nope") is None
    # the zip holds every file at its relative path
    import zipfile
    z = zipfile.ZipFile(backups.zip_backup(b["name"]))
    assert "manifest.json" in z.namelist() and f"workspaces/{WS25}/uploads/docA.pdf" in z.namelist()
    # damage the live data, restore, and it is back
    pdf.unlink()
    pages_db = data_dir / "workspaces" / WS25 / "pages.db"
    with closing(sqlite3.connect(str(pages_db))) as conn:
        register_functions(conn)  # the notes index's triggers call textnorm
        conn.execute("DELETE FROM unified_blocks WHERE id = 'p1'")
        conn.commit()
    r = backups.restore(b["name"])
    assert r["files"] >= 3 and pdf.is_file()
    with closing(sqlite3.connect(str(pages_db))) as conn:
        assert conn.execute("SELECT 1 FROM unified_blocks WHERE id = 'p1'").fetchone()
    assert backups.delete(b["name"]) is True and backups.delete(b["name"]) is False


def test_v31_drops_the_dead_session_column(data_dir, monkeypatch):
    # sessions loses guest_date, which the guest login wrote and nothing read;
    # the rows stay, so no one is signed out. Step 25 builds the table as its
    # own time shaped it (_V25_USERS_SCHEMA), guest_date included, so a
    # directory stopped at version 30 still has the column and step 31 is what
    # takes it away. A second run changes nothing.
    v24_users_db()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('alice31', 'x', ?)", (OLD,))
        conn.execute("INSERT INTO sessions (token, username, guest_date, created_at, via) VALUES (?, ?, ?, ?, ?)",
                     ("tok-31", "alice31", "2026-01-01", OLD, "cloud"))
        conn.commit()
    with monkeypatch.context() as m:
        release_of(m, 30)
        assert migrations.ensure_current()["applied"][-1] == "highlight_shape"
    assert migrations.data_version() == 30
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        assert [r[1] for r in conn.execute("PRAGMA table_info(sessions)")] == ["token", "user_id", "guest_date", "created_at", "via"]
        (alice,) = conn.execute("SELECT id FROM users WHERE username = 'alice31'").fetchone()
    assert migrations.ensure_current()["applied"] == ["session_columns", "share_token_workspace", "page_ops_batch_id", "folder_links", "folder_links_remote"]
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        assert [r[1] for r in conn.execute("PRAGMA table_info(sessions)")] == ["token", "user_id", "created_at", "via"]
        assert conn.execute("SELECT user_id, created_at, via FROM sessions WHERE token = 'tok-31'").fetchone() == (alice, OLD, "cloud")
    assert migrations.ensure_current()["applied"] == []


def test_v32_puts_the_workspace_in_front_of_share_tokens(data_dir, monkeypatch):
    # a share token becomes <workspace id>.<secret>, so a router can place
    # share traffic by its prefix: every bare token in shares gains its
    # workspace in front and the invitations keyed by it follow, together; a
    # token with a dot is left as it is, and a second run changes nothing.
    # The bare tokens, what links sent out before carry, name no share now.
    v24_users_db()
    with monkeypatch.context() as m:
        release_of(m, 31)
        assert migrations.ensure_current()["applied"][-1] == "session_columns"
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.executemany("INSERT INTO shares (token, workspace_id, page_id, folder, created_by, created_at) "
                         "VALUES (?, ?, ?, ?, 'u-a', ?)",
                         [("pageTok32", "ws32", "p1", "", OLD), ("folderTok32", "ws32", "", "f1", OLD),
                          ("otherTok32", "ws32b", "p1", "", OLD), ("ws32b.minted32", "ws32b", "p2", "", OLD)])
        conn.executemany("INSERT INTO share_users (token, user_id, role) VALUES (?, ?, ?)",
                         [("pageTok32", "u-b", "edit"), ("pageTok32", "u-c", "view"), ("folderTok32", "u-b", "view"),
                          ("ws32b.minted32", "u-b", "view")])
        conn.commit()
    expected_shares = [("ws32.folderTok32", "ws32"), ("ws32.pageTok32", "ws32"),
                       ("ws32b.minted32", "ws32b"), ("ws32b.otherTok32", "ws32b")]
    expected_users = [("ws32.folderTok32", "u-b", "view"), ("ws32.pageTok32", "u-b", "edit"),
                      ("ws32.pageTok32", "u-c", "view"), ("ws32b.minted32", "u-b", "view")]

    def state():
        with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
            return (conn.execute("SELECT token, workspace_id FROM shares ORDER BY token").fetchall(),
                    conn.execute("SELECT token, user_id, role FROM share_users ORDER BY token, user_id").fetchall())

    with monkeypatch.context() as m:
        release_of(m, 32)
        assert migrations.ensure_current()["applied"] == ["share_token_workspace"]
    assert state() == (expected_shares, expected_users)
    assert all(share_token_workspace(token) == ws for token, ws in expected_shares)
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        migrations._v32_share_token_workspace(conn)  # re-runnable
    assert state() == (expected_shares, expected_users)
    assert "share_token_workspace" not in migrations.ensure_current()["applied"]


WS33 = "wsOplog33"


def test_v33_gives_the_op_log_its_batch_columns(data_dir, monkeypatch):
    # page_ops gains batch_id and cursor, '' on the rows logged before (no
    # retry of them is answered from the log), and the partial unique index
    # on page, client and batch id: the table a fresh workspace gets. A file
    # without an op log is left to the schema statements, and a second run
    # changes nothing. A restored backup of the old shape goes through the
    # same (ws_backup._normalize_copies).
    from gamma import ws_backup
    from gamma.db import PAGES_SCHEMA

    v24_users_db()
    with monkeypatch.context() as m:
        release_of(m, 32)
        assert migrations.ensure_current()["applied"][-1] == "share_token_workspace"
    old_log = next(s for s in migrations._V25_PAGES_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS page_ops ("))
    pages_db = config.WORKSPACES_DIR / WS33 / "pages.db"
    restored = data_dir / "restored" / "pages.db"
    for path in (pages_db, restored):
        path.parent.mkdir(parents=True)
        _legacy_pages_db(path, [("p1", "root", "Page", {})])
        with closing(sqlite3.connect(str(path))) as conn:
            conn.execute(old_log)
            conn.executemany("INSERT INTO page_ops (page_id, seq, actor, client, at, ops) VALUES ('p1', ?, 'u-a', ?, ?, '[]')",
                             [(1, "tab", OLD), (2, "", OLD)])
            conn.commit()
    no_log = config.WORKSPACES_DIR / "wsNoLog33" / "pages.db"
    no_log.parent.mkdir()
    _legacy_pages_db(no_log, [])

    assert migrations.ensure_current()["applied"] == ["page_ops_batch_id", "folder_links", "folder_links_remote"]

    def shape(conn):
        return ([tuple(r[1:]) for r in conn.execute("PRAGMA table_info(page_ops)")],
                conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'page_ops'").fetchall())

    def state():
        with closing(sqlite3.connect(str(pages_db))) as conn:
            return shape(conn), conn.execute("SELECT * FROM page_ops ORDER BY seq").fetchall()

    with closing(sqlite3.connect(":memory:")) as fresh:
        for stmt in PAGES_SCHEMA:
            if "page_ops" in stmt:
                fresh.execute(stmt)
        expected = shape(fresh)
    before = state()
    assert before == (expected, [("p1", 1, "u-a", "tab", OLD, "[]", "", ""), ("p1", 2, "u-a", "", OLD, "[]", "", "")])
    with closing(sqlite3.connect(str(no_log))) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'page_ops'").fetchone()
    with closing(sqlite3.connect(str(pages_db))) as conn:  # a batch id once per page and client
        named = "INSERT INTO page_ops (page_id, seq, client, at, ops, batch_id) VALUES ('p1', ?, ?, ?, '[]', 'b1')"
        conn.execute(named, (3, "tab", OLD))
        conn.execute(named, (4, "other tab", OLD))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(named, (5, "tab", OLD))
        conn.rollback()

    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute("PRAGMA user_version = 32")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["page_ops_batch_id", "folder_links", "folder_links_remote"]  # re-runnable
    assert state() == before

    ws_backup._normalize_copies(restored.parent)  # a backup taken before the step
    with closing(sqlite3.connect(str(restored))) as conn:
        assert shape(conn) == expected
        assert conn.execute("SELECT seq, batch_id, cursor FROM page_ops ORDER BY seq").fetchall() == [
            (1, "", ""), (2, "", "")]


WS34 = "wsPrefs34"


def test_v34_moves_the_workspace_prefs_into_the_workspace(data_dir, monkeypatch):
    # The prefs that name a workspace's pages (every key but the account-wide
    # ones) move into its pages.db, workspace_prefs, when it is first opened
    # after the upgrade, which itself changes only users.db's stamp; users.db
    # keeps its rows for the release before. A second run keeps a row
    # written since and takes back one older than users.db's; a backup's
    # copy gets the table empty.
    from gamma import db, seed, ws_backup

    monkeypatch.setattr(migrations, "_snapshot", ())
    connect_users_db().close()
    seed.create_workspace_files(WS34)
    pages_db = config.WORKSPACES_DIR / WS34 / "pages.db"
    with closing(sqlite3.connect(str(pages_db))) as conn:  # the workspace as the steps up to 33 left it
        conn.execute("DROP TABLE workspace_prefs")
        conn.execute("PRAGMA user_version = 0")
    rows = [("u-a", WS34, "open-tabs", '["t1"]', OLD), ("u-a", WS34, "read-pos", '{"d": 3}', OLD),
            ("u-b", WS34, "recent-views", '["r"]', OLD), ("u-a", WS34, "ai-provider", '"legacy"', OLD),
            ("u-a", "", "open-tabs", '["no workspace"]', OLD), ("u-a", "", "profile", "{}", OLD),
            ("u-a", "wsElse34", "open-tabs", '["else"]', OLD)]
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.executemany("INSERT INTO user_prefs VALUES (?, ?, ?, ?, ?)", rows)
        conn.execute("PRAGMA user_version = 33")
        conn.commit()

    result = migrations.ensure_current()
    assert result["applied"] == ["folder_links", "folder_links_remote"] and result["workspace_steps"] == ["workspace_prefs"]
    assert migrations.data_version() == SCHEMA_VERSION == 36
    assert migrations.is_behind(WS34)
    with closing(sqlite3.connect(str(pages_db))) as conn:  # the upgrade left the workspace as it was
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'workspace_prefs'").fetchone()
    with db.connect_pages_db(WS34) as conn:  # the first open runs the step
        moved = sorted(conn.execute("SELECT user_id, key, value, updated_at FROM workspace_prefs"))
    assert moved == [("u-a", "open-tabs", '["t1"]', OLD), ("u-a", "read-pos", '{"d": 3}', OLD),
                     ("u-b", "recent-views", '["r"]', OLD)]
    assert not migrations.is_behind(WS34) and db.get_pref("u-a", "open-tabs", WS34) == (["t1"], OLD)
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:  # left for the release before
        assert sorted(conn.execute("SELECT * FROM user_prefs")) == sorted(rows)

    later = db.set_pref("u-a", "open-tabs", ["t2"], WS34)
    with db.connect_pages_db(WS34) as conn:
        conn.execute("UPDATE workspace_prefs SET value = '[\"stale\"]', updated_at = '2020-01-01T00:00:00.000000Z' "
                     "WHERE user_id = 'u-b'")
    with closing(sqlite3.connect(str(pages_db))) as conn:
        migrations._v34_workspace_prefs(WS34, conn, None)  # re-runnable
        conn.commit()
        assert sorted(conn.execute("SELECT user_id, key, value, updated_at FROM workspace_prefs")) == [
            ("u-a", "open-tabs", '["t2"]', later), ("u-a", "read-pos", '{"d": 3}', OLD),
            ("u-b", "recent-views", '["r"]', OLD)]

    # the workspace's files from before the step are in the upgrade's
    # snapshot; restored as a backup, that copy gets the table, empty
    copy = Path(result["backup"]) / "workspaces" / WS34 / "pages.db"
    with closing(sqlite3.connect(str(copy))) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'workspace_prefs'").fetchone()
    restored = data_dir / "restored34"
    restored.mkdir()
    (restored / "pages.db").write_bytes(copy.read_bytes())
    ws_backup._normalize_copies(restored)
    with closing(sqlite3.connect(str(restored / "pages.db"))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspace_prefs").fetchone()[0] == 0
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def users_db_shape(conn) -> dict:
    """Every table of a users.db with its columns (name, type, not null,
    default, primary key) and the indexes on it, as PRAGMA reports them."""
    tables = sorted(r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"))
    return {table: {
        "columns": [tuple(r[1:]) for r in conn.execute(f"PRAGMA table_info({table})")],
        "indexes": sorted((r[0], tuple(c[2] for c in conn.execute(f"PRAGMA index_info({r[0]})")))
                          for r in conn.execute(f"PRAGMA index_list({table})") if r[3] == "c"),
    } for table in tables}


def test_an_upgraded_users_db_has_a_fresh_installs_shape(data_dir):
    # the frozen statements the steps build from end where db.USERS_SCHEMA
    # is: a users.db brought up from version 24, rows and all, has the same
    # tables, columns (names, types, constraints, order) and indexes as one a
    # fresh install creates
    build_v24_accounts()
    assert migrations.ensure_current()["applied"][0] == "account_ids"
    with closing(sqlite3.connect(":memory:")) as fresh:
        for stmt in USERS_SCHEMA:
            fresh.execute(stmt)
        expected = users_db_shape(fresh)
    with connect_users_db() as conn:
        assert users_db_shape(conn) == expected
