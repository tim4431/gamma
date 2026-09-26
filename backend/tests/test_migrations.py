"""gamma/migrations.py — the versioned upgrade of the data directory, run
against a hand-built pre-workspace layout (schema version 0): every account
directory becomes a workspace, personal prefs move to users.db, shares are
re-keyed, a snapshot is taken first, a second run is a no-op, and a newer
data directory is refused. Runs in its own temp data directory so the
suite's shared one is never touched."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import gamma.app as app_mod  # noqa: F401  (builds the app on the suite's data dir, before any fixture repoints it)
from gamma import backups, config, migrations
from gamma.db import SCHEMA_VERSION, SchemaOutdated, connect_users_db

OLD = "2024-01-01T00:00:00.000000Z"


def test_v7_adds_mcp_oauth_and_preserves_tokens(data_dir):
    connect_users_db().close()
    with sqlite3.connect(str(config.USERS_DB)) as conn:
        conn.execute("DROP TABLE mcp_oauth")
        conn.execute("PRAGMA user_version = 6")
        conn.execute("INSERT INTO integration_tokens VALUES ('id', 'hash', 'user', 'ws', 'Codex', 'now', 9999999999, 'read')")
    assert migrations.ensure_current()["applied"] == ["mcp_oauth", "ai_usage", "upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT id FROM integration_tokens").fetchone()[0] == 'id'
        assert conn.execute("SELECT * FROM mcp_oauth").fetchall() == []
    assert migrations.ensure_current()["applied"] == []


def test_v6_adds_integration_tokens_and_is_repeatable(data_dir):
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE integration_tokens")
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["integration_tokens", "mcp_oauth", "ai_usage", "upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT * FROM integration_tokens").fetchall() == []
    assert migrations.ensure_current()["applied"] == []


def test_v5_adds_publisher_sessions_and_is_repeatable(data_dir):
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE publisher_sessions")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    result = migrations.ensure_current()
    assert result["applied"] == ["publisher_sessions", "integration_tokens", "mcp_oauth", "ai_usage", "upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT * FROM publisher_sessions").fetchall() == []
    assert migrations.ensure_current()["applied"] == []


def test_v16_adds_pending_memberships_and_is_repeatable(data_dir):
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE pending_memberships")
        conn.execute("PRAGMA user_version = 15")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT * FROM pending_memberships").fetchall() == []
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_pending_subject'").fetchone()
    assert migrations.ensure_current()["applied"] == []


def test_v17_folds_appearance_into_the_profile(data_dir):
    # {theme, pdfDark} becomes the profile's {theme, pdfDarkPage} with the
    # same updated_at; an existing profile wins; every appearance row goes.
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        rows = [
            ("ann", "", "appearance", {"theme": "sepia", "pdfDark": True}),
            ("bob", "", "appearance", {"theme": "dark", "pdfDark": False}),
            ("bob", "", "profile", {"theme": "gray", "enterNewNote": True}),
            ("cat", "", "appearance", "garbage"),
        ]
        for user, ws, key, value in rows:
            conn.execute("INSERT INTO user_prefs VALUES (?, ?, ?, ?, ?)", (user, ws, key, json.dumps(value), OLD))
        conn.execute("PRAGMA user_version = 16")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        stored = {(r[0], r[1]): (json.loads(r[2]), r[3]) for r in conn.execute(
            "SELECT username, key, value, updated_at FROM user_prefs")}
    assert stored == {
        ("ann", "profile"): ({"theme": "sepia", "pdfDarkPage": True}, OLD),
        ("bob", "profile"): ({"theme": "gray", "enterNewNote": True}, OLD),
    }
    assert migrations.ensure_current()["applied"] == []



def test_v18_marks_session_origin_and_grant_refusal(data_dir):
    # sessions gain via (existing ones count as password sessions),
    # identities gain revoked_at; the rows themselves are kept
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('mig18', 'x', ?)", (OLD,))
        conn.execute("DROP TABLE sessions")
        conn.execute("CREATE TABLE sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username), "
                     "guest_date TEXT, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO sessions VALUES ('tok-18', 'mig18', NULL, ?)", (OLD,))
        conn.execute("DROP TABLE identities")
        conn.execute("CREATE TABLE identities (provider TEXT NOT NULL, subject TEXT NOT NULL, username TEXT NOT NULL, "
                     "email TEXT NOT NULL DEFAULT '', claims TEXT NOT NULL DEFAULT '{}', refresh_token TEXT NOT NULL DEFAULT '', "
                     "created_at TEXT NOT NULL, last_login_at TEXT NOT NULL, PRIMARY KEY (provider, subject))")
        conn.execute("INSERT INTO identities (provider, subject, username, created_at, last_login_at) "
                     "VALUES ('gamma-cloud', 'sub-18', 'mig18', ?, ?)", (OLD, OLD))
        conn.execute("PRAGMA user_version = 17")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT via FROM sessions WHERE token = 'tok-18'").fetchone() == ("",)
        assert conn.execute("SELECT revoked_at FROM identities WHERE subject = 'sub-18'").fetchone() == ("",)
    assert migrations.ensure_current()["applied"] == []

def test_v19_gives_mirrors_a_page_filter(data_dir):
    # mirrors gain page_filter; an existing mirror keeps NULL (every page travels)
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE mirrors")
        conn.execute("CREATE TABLE mirrors (workspace_id TEXT PRIMARY KEY, remote_url TEXT NOT NULL, "
                     "remote_ws TEXT NOT NULL, remote_name TEXT NOT NULL DEFAULT '', token TEXT NOT NULL, "
                     "owner TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'two-way', remote_cursor TEXT NOT NULL DEFAULT '', "
                     "local_cursor TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, "
                     "poll_s INTEGER NOT NULL DEFAULT 30, on_change INTEGER NOT NULL DEFAULT 1)")
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at) "
                     "VALUES ('ws19', 'https://nas', 'r', 't', 'u', ?)", (OLD,))
        conn.execute("PRAGMA user_version = 18")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT page_filter FROM mirrors WHERE workspace_id = 'ws19'").fetchone() == (None,)
    assert migrations.ensure_current()["applied"] == []


def test_v20_removes_the_legacy_guest_account(data_dir):
    # the shared guest account goes with its rows and its workspace directory;
    # a password-less is_guest row made by create-user becomes a normal account
    connect_users_db().close()
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
    assert migrations.ensure_current()["applied"] == ["guest_accounts", "folder_shares"]
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
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("DROP TABLE shares")
        conn.execute("CREATE TABLE shares (token TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, page_id TEXT NOT NULL, "
                     "created_by TEXT NOT NULL DEFAULT '', audience TEXT NOT NULL DEFAULT 'anyone', "
                     "role TEXT NOT NULL DEFAULT 'view', allowed_users TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
        conn.execute("CREATE UNIQUE INDEX idx_shares_page ON shares(workspace_id, page_id)")
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, created_at) VALUES ('sh21', 'ws21', 'p1', ?)", (OLD,))
        conn.execute("PRAGMA user_version = 20")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["folder_shares"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT page_id, folder FROM shares WHERE token = 'sh21'").fetchone() == ("p1", "")
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('f1', 'ws21', '', 'a/b', ?)", (OLD,))
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('f2', 'ws21', '', 'c', ?)", (OLD,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('f3', 'ws21', '', 'a/b', ?)", (OLD,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO shares (token, workspace_id, page_id, folder, created_at) VALUES ('p2', 'ws21', 'p1', '', ?)", (OLD,))
    assert migrations.ensure_current()["applied"] == []


def test_v15_writes_the_old_default_into_modelless_ai_entries(data_dir):
    # Entries no longer fall back to a built-in model: an entry that had none
    # picked gets the default it was using; picked lists are left alone.
    connect_users_db().close()
    providers = [
        {"id": "a", "protocol": "anthropic", "api_key": "k", "models": ""},
        {"id": "o", "protocol": "openai", "api_key": "k", "models": "gpt-x, gpt-y"},
        {"id": "c", "protocol": "chatgpt", "oauth": {}},
    ]
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("INSERT INTO user_prefs VALUES ('u', '', 'ai-settings', ?, ?)",
                     (json.dumps({"providers": providers}), OLD))
        conn.execute("PRAGMA user_version = 14")
        conn.commit()
    assert migrations.ensure_current()["applied"] == ["ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with connect_users_db() as conn:
        stored = json.loads(conn.execute(
            "SELECT value FROM user_prefs WHERE key = 'ai-settings'").fetchone()[0])["providers"]
    assert [p["models"] for p in stored] == ["claude-haiku-4-5-20251001", "gpt-x, gpt-y", "gpt-5.1"]
    assert migrations.ensure_current()["applied"] == []


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point every module that caches a data-directory path at tmp_path."""
    import gamma.auth as auth_mod
    import gamma.db as db_mod
    import gamma.seed as seed_mod
    import gamma.workspaces as ws_mod

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "USERS_DB", tmp_path / "users.db")
    monkeypatch.setattr(config, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(config, "LEGACY_USERS_DIR", tmp_path / "users")
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(db_mod, "USERS_DB", tmp_path / "users.db")
    monkeypatch.setattr(db_mod, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(auth_mod, "USERS_DB", tmp_path / "users.db")
    monkeypatch.setattr(seed_mod, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path / "workspaces")
    return tmp_path


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


def _legacy_data_db(path: Path, prefs: dict):
    with closing(sqlite3.connect(str(path))) as conn:
        # the oldest chats shape (no title column) + legacy tables + prefs
        conn.execute("CREATE TABLE chats (block_id TEXT PRIMARY KEY, messages TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE annotations (id TEXT)")
        conn.execute("CREATE TABLE prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        for k, v in prefs.items():
            conn.execute("INSERT INTO prefs VALUES (?, ?, ?)", (k, json.dumps(v), OLD))
        conn.commit()


def build_v0(root: Path):
    """The layout every pre-workspace Gamma wrote: users.db without the
    later columns, users/<name>/ with pages.db + data.db + uploads/."""
    with closing(sqlite3.connect(str(root / "users.db"))) as conn:
        conn.execute("CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, "
                     "is_guest INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, "
                     "guest_date TEXT, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE shares (token TEXT PRIMARY KEY, username TEXT NOT NULL, "
                     "doc_id TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", [
            ("alice", "hash", 0, OLD), ("bob", "hash", 0, OLD), ("guest", "", 1, OLD)])
        conn.execute("INSERT INTO sessions VALUES ('tok-alice', 'alice', NULL, ?)", (OLD,))
        conn.executemany("INSERT INTO shares VALUES (?, ?, ?, ?)", [
            ("share-doc", "alice", "docA", OLD),       # doc-keyed: resolves to alice's page
            ("share-gone", "alice", "vanished", OLD),  # unresolvable: dropped
            ("share-ghost", "nobody", "docA", OLD),    # unknown account: dropped
        ])
        conn.commit()
    for name in ("alice", "bob", "guest"):
        d = root / "users" / name
        (d / "uploads").mkdir(parents=True)
        (d / "uploads" / "docA.pdf").write_bytes(b"%PDF-1.4 " + name.encode())
    _legacy_pages_db(root / "users/alice/pages.db", [
        ("pageA", "root", "PDF Notes - a.pdf", {"doc_id": "docA", "sourceUrl": "https://x/a.pdf"}),
        ("noteA", "pageA", "see ![c](/api/uploads/i.png){:width 120}", {}),
    ])
    _legacy_pages_db(root / "users/bob/pages.db", [("pageB", "root", "Bob page", {})])
    _legacy_pages_db(root / "users/guest/pages.db", [])
    _legacy_data_db(root / "users/alice/data.db", {
        "open-tabs": [{"id": "pageA"}], "ai-settings": {"providers": [{"id": "p1", "protocol": "openai"}]},
        "appearance": {"theme": "dark"}})
    _legacy_data_db(root / "users/bob/data.db", {"recent-views": ["pageB"]})
    _legacy_data_db(root / "users/guest/data.db", {})


def test_status_and_refusal_on_a_v0_directory(data_dir):
    build_v0(data_dir)
    st = migrations.status()
    assert st["version"] == 0 and st["target"] == SCHEMA_VERSION and not st["fresh"]
    assert [p["name"] for p in st["pending"]] == ["baseline", "workspaces", "workspace_access", "workspace_kinds", "publisher_sessions", "integration_tokens", "mcp_oauth", "ai_usage", "upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    # Nothing but the runner may open an old users.db.
    with pytest.raises(SchemaOutdated):
        connect_users_db()
    assert migrations.ensure_current(dry_run=True)["applied"] == []
    assert migrations.data_version() == 0  # a dry run changes nothing


def test_upgrade_v0_to_current(data_dir):
    build_v0(data_dir)
    result = migrations.ensure_current()
    assert result["from"] == 0 and result["to"] == SCHEMA_VERSION
    assert result["applied"] == ["baseline", "workspaces", "workspace_access", "workspace_kinds", "publisher_sessions", "integration_tokens", "mcp_oauth", "ai_usage", "upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    assert migrations.data_version() == SCHEMA_VERSION

    # A snapshot of every database was taken first, with a manifest.
    backup = Path(result["backup"])
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["schema_version"] == 0
    assert "users.db" in manifest["files"] and "users/alice/pages.db" in manifest["files"]
    assert (backup / "users/alice/pages.db").is_file() and not (backup / "users/alice/uploads").exists()
    assert migrations.status()["backups"] == [backup.name]
    assert backups.info(backup.name)["uploads"] is False

    with connect_users_db() as conn:
        users = {r[0]: r[1] for r in conn.execute("SELECT username, default_workspace FROM users")}
        # step 20 removed the legacy shared guest account and its workspace
        assert set(users) == {"alice", "bob"} and all(users.values())
        ws_alice, ws_bob = users["alice"], users["bob"]
        # one personal workspace per account, the account its owner
        rows = conn.execute("SELECT id, name, created_by FROM workspaces ORDER BY created_at").fetchall()
        assert {r[0] for r in rows} == {ws_alice, ws_bob}
        assert {d.name for d in (data_dir / "workspaces").iterdir()} == {ws_alice, ws_bob}
        assert dict((r[0], r[1]) for r in rows)[ws_alice] == "alice"
        members = conn.execute("SELECT workspace_id, username, role FROM workspace_members").fetchall()
        assert (ws_alice, "alice", "owner") in members and len(members) == 2
        # the columns that used to be added lazily exist
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert {"is_admin", "max_upload_mb", "quota_mb", "default_workspace"} <= cols
        # shares: doc-keyed row resolved and re-keyed by workspace; the rest dropped
        shares = conn.execute("SELECT token, workspace_id, page_id, created_by FROM shares").fetchall()
        assert shares == [("share-doc", ws_alice, "pageA", "alice")]
        assert "doc_id" not in {r[1] for r in conn.execute("PRAGMA table_info(shares)")}
        # personal prefs moved: account-wide keys under '', page-naming keys under the workspace
        prefs = {(r[0], r[1], r[2]): json.loads(r[3]) for r in conn.execute(
            "SELECT username, workspace_id, key, value FROM user_prefs")}
        assert prefs[("alice", "", "ai-settings")]["providers"][0]["id"] == "p1"
        # ...and step 17 folded appearance into the preference profile
        assert prefs[("alice", "", "profile")] == {"theme": "dark"}
        assert ("alice", "", "appearance") not in prefs
        assert prefs[("alice", ws_alice, "open-tabs")] == [{"id": "pageA"}]
        assert prefs[("bob", ws_bob, "recent-views")] == ["pageB"]
        # the session row survived untouched
        assert conn.execute("SELECT username FROM sessions WHERE token = 'tok-alice'").fetchone() == ("alice",)

    # Files moved, not copied; the legacy directory is gone.
    ws_dir = data_dir / "workspaces" / ws_alice
    assert (ws_dir / "pages.db").is_file() and (ws_dir / "uploads" / "docA.pdf").read_bytes().endswith(b"alice")
    assert not (data_dir / "users").exists()

    # Per-workspace files normalized: content shapes, legacy tables, chats.title.
    with closing(sqlite3.connect(str(ws_dir / "pages.db"))) as conn:
        content, props = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = 'pageA'").fetchone()
        assert content == "a.pdf" and json.loads(props) == {"doc_id": "docA", "source_url": "https://x/a.pdf", "auto_title": "a.pdf"}
        assert conn.execute("SELECT content FROM unified_blocks WHERE id = 'noteA'").fetchone()[0] == "see ![c|120](/api/uploads/i.png)"
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'page_ops'").fetchone()
    with closing(sqlite3.connect(str(ws_dir / "data.db"))) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "prefs" not in tables and "annotations" not in tables
        assert "title" in {r[1] for r in conn.execute("PRAGMA table_info(chats)")}

    # Idempotent: a second run applies nothing and takes no new snapshot.
    again = migrations.ensure_current()
    assert again["applied"] == [] and again["backup"] is None
    assert len(backups.list_backups()) == 1


def test_interrupted_upgrade_resumes(data_dir):
    """A crash mid-step leaves the version at the last completed step; the
    next start finishes the rest without redoing accounts already moved."""
    build_v0(data_dir)
    # Run the baseline step only, then half of the workspaces step by hand.
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        migrations._v1_baseline(conn)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    assert migrations.data_version() == 1
    calls = {"n": 0}
    real = migrations._move_prefs

    def crash_after_first(conn, username, ws_id, data_db):
        real(conn, username, ws_id, data_db)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")

    import gamma.migrations as m
    original = m._move_prefs
    m._move_prefs = crash_after_first
    try:
        with pytest.raises(migrations.MigrationError, match="workspaces"):
            migrations.ensure_current()
    finally:
        m._move_prefs = original
    assert migrations.data_version() == 1  # the failed step did not stamp
    result = migrations.ensure_current()   # resumes: the moved account is skipped, the rest done
    assert result["applied"] == ["workspaces", "workspace_access", "workspace_kinds", "publisher_sessions", "integration_tokens", "mcp_oauth", "ai_usage", "upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"] and migrations.data_version() == SCHEMA_VERSION
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE default_workspace = ''").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 2  # the guest's went in step 20
    assert not (data_dir / "users").exists()


def test_newer_data_directory_is_refused(data_dir):
    build_v0(data_dir)
    migrations.ensure_current()
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        conn.commit()
    with pytest.raises(migrations.NewerDataError):
        migrations.ensure_current()
    # …and so is running the app's startup on it.
    with pytest.raises(SystemExit):
        app_mod._startup_maintenance()


def test_fresh_directory_needs_no_migration(data_dir):
    assert migrations.status()["fresh"] is True
    assert migrations.ensure_current()["applied"] == []
    connect_users_db().close()  # created at the current version
    assert migrations.data_version() == SCHEMA_VERSION
    assert migrations.ensure_current()["applied"] == []


def test_backups_are_pruned_only_on_request(data_dir, monkeypatch):
    build_v0(data_dir)
    monkeypatch.setattr(backups, "KEEP_BACKUPS", 2)
    backups.create("a")
    backups.create("b")   # a second one within the same second gets the next stamp
    backups.create("c")
    names = [b["name"] for b in backups.list_backups()]
    assert len(names) == 3  # hand-made backups are never pruned by themselves
    assert backups.prune_backups() == names[:1]
    assert backups.prune_backups(keep=0) == names[1:] and backups.list_backups() == []


def test_backup_with_uploads_zip_and_restore(data_dir):
    build_v0(data_dir)
    migrations.ensure_current()
    ws = connect_users_db().execute("SELECT default_workspace FROM users WHERE username = 'alice'").fetchone()[0]
    pdf = data_dir / "workspaces" / ws / "uploads" / "docA.pdf"
    b = backups.create("full", uploads=True)
    assert b["uploads"] is True and b["upload_files"] == 2 and b["size_bytes"] > 0  # alice's, bob's (the guest's went in step 20)
    assert (Path(b["path"]) / "workspaces" / ws / "uploads" / "docA.pdf").read_bytes() == pdf.read_bytes()
    assert backups.backup_path("../../etc") is None and backups.info("nope") is None
    # the zip holds every file at its relative path
    import zipfile
    z = zipfile.ZipFile(backups.zip_backup(b["name"]))
    assert "manifest.json" in z.namelist() and f"workspaces/{ws}/uploads/docA.pdf" in z.namelist()
    # damage the live data, restore, and it is back
    pdf.unlink()
    with closing(sqlite3.connect(str(data_dir / "workspaces" / ws / "pages.db"))) as conn:
        conn.execute("DELETE FROM unified_blocks WHERE id = 'noteA'")
        conn.commit()
    r = backups.restore(b["name"])
    assert r["files"] >= 7 and pdf.is_file()
    with closing(sqlite3.connect(str(data_dir / "workspaces" / ws / "pages.db"))) as conn:
        assert conn.execute("SELECT 1 FROM unified_blocks WHERE id = 'noteA'").fetchone()
    assert backups.delete(b["name"]) is True and backups.delete(b["name"]) is False


def test_v9_repairs_leaked_upload_paths_once(data_dir):
    """The upload_path_titles content step: a directory path that leaked into
    original_filename (and the generated title) becomes the leaf, a
    user-renamed page keeps its title, and a second run touches nothing."""
    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        conn.execute("PRAGMA user_version = 8")
        conn.commit()
    ws_root = data_dir / "workspaces" / "wsx"
    ws_root.mkdir(parents=True)
    _legacy_pages_db(ws_root / "pages.db", [
        ("auto", "root", "papers/readout/a.pdf",
         {"original_filename": "papers\\readout\\a.pdf", "auto_title": "papers/readout/a.pdf"}),
        ("renamed", "root", "My title",
         {"original_filename": "dir/b.pdf", "auto_title": "dir/b.pdf"}),
        ("md", "root", "notes/c", {"original_filename": "notes/c.md", "markdown_import": True}),
        ("clean", "root", "d.pdf", {"original_filename": "d.pdf", "auto_title": "d.pdf"}),
    ])
    assert migrations.ensure_current()["applied"] == ["upload_path_titles", "mirrors", "mirror_cadence", "sync_log_stats", "sync_conflict_base", "identities", "ai_explicit_models", "pending_memberships", "profile", "cloud_grant", "mirror_page_filter", "guest_accounts", "folder_shares"]
    with closing(sqlite3.connect(str(ws_root / "pages.db"))) as conn:
        rows = {r[0]: (r[1], json.loads(r[2]), r[3]) for r in conn.execute(
            "SELECT id, content, properties, updated_at FROM unified_blocks WHERE parent_id = 'root'")}
    assert rows["auto"][0] == "a.pdf"
    assert rows["auto"][1] == {"original_filename": "a.pdf", "auto_title": "a.pdf"}
    assert rows["renamed"][0] == "My title" and rows["renamed"][1]["original_filename"] == "b.pdf"
    assert rows["md"][0] == "c" and rows["md"][1]["original_filename"] == "c.md"
    assert rows["clean"] == ("d.pdf", {"original_filename": "d.pdf", "auto_title": "d.pdf"}, OLD)
    assert migrations.ensure_current()["applied"] == []
