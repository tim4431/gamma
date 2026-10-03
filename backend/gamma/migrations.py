"""Versioned upgrades of the data directory.

The data directory has ONE schema version, stored as ``PRAGMA user_version``
of ``users.db`` (``db.SCHEMA_VERSION`` is what this code expects). A
release that changes stored shapes ships a numbered step here and bumps the
constant; the step is the whole change — moved files, rebuilt tables,
rewritten rows — and ``db.py``'s ``CREATE TABLE`` statements always describe
the CURRENT shape, so nothing is patched lazily on connect.

The rules that keep this safe and small:

- **Runs before anything else.** ``ensure_current()`` is the first thing
  the server does at startup (and ``python manage.py migrate`` by hand). A
  data directory AHEAD of the binary, or below ``MIN_UPGRADABLE``, is
  refused: the server then serves one page saying what to run instead
  (``guidance()``, gamma/app.py), and an older Gamma never opens files it
  does not understand.
- **Backup first.** Before the first pending step every database file is
  snapshotted with the SQLite backup API into ``backups/<time>-v<N>/``
  (``gamma/backups.py``; uploads are never copied — steps move them, never
  rewrite them). An upgrade that does not finish keeps that snapshot named
  in ``backups/upgrade.json`` and every retry reuses it, so a restart loop
  neither piles up copies nor rotates the clean one out. Once an upgrade
  finishes, only the newest ``backups.KEEP_BACKUPS`` automatic snapshots
  are kept; hand-made ones are never pruned.
- **One step, one stamp.** Steps run in order; the version is stamped after
  each one, so an interrupted upgrade resumes at the step that did not
  finish. Every step is written to be re-runnable (it checks what it is
  about to do).
- **Nothing piles up.** A step is kept only while ``MIN_UPGRADABLE`` is
  below it. Raising ``MIN_UPGRADABLE`` (a release or two later) deletes the
  older steps; a data directory that old must first run a release that
  still has them (the refusal says which). Content normalization of
  per-workspace files (``gamma/normalize.py``) is not a step: it also runs
  on backup restore, because a backup can be older than any step.

Docs: docs/dev/migrations.md.
"""

import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from . import backups, config
from .blocks_store import FOLDERS, folder_by_path
from .db import (SCHEMA_VERSION, USERS_SCHEMA, new_account_id, page_now, register_functions, safe_ws_id,
                 users_db_version)
from .logbuf import log
from .normalize import (block_columns, block_fts, folder_blocks, highlight_shape, normalize_data_db, page_changes,
                        pages_db_chats)

# The lowest version this release upgrades from: a data directory at it
# has had the steps up to it, and this release carries the steps after it
# (docs/dev/migrations.md "Nothing piles up"). One below it must first run
# UPGRADE_VIA, the newest release that still carries the deleted steps.
MIN_UPGRADABLE = 19
UPGRADE_VIA = {
    "image": "ghcr.io/tim4431/gamma:sha-8708ebb",  # the server image built from that release
    "release": "the Gamma release of 2026-10-01",
    "schema": 24,                                   # what it brings a data directory to
}


class MigrationError(RuntimeError):
    """The data directory cannot be brought to SCHEMA_VERSION by this
    process: a step failed (``step``, ``snapshot``), or one of the two
    refusals below. ``guidance()`` turns any of them into what the person
    should do."""

    step = ""
    snapshot = ""
    version: int | None = None


class NewerDataError(MigrationError):
    """The data directory was written by a newer Gamma."""


class TooOldDataError(MigrationError):
    """The data directory predates MIN_UPGRADABLE."""


# --- inspection ---------------------------------------------------------------

def data_version() -> int | None:
    """The data directory's schema version; None when it has no users.db yet
    (a fresh install — ``db.connect_users_db`` creates it at SCHEMA_VERSION)."""
    if not config.USERS_DB.exists():
        return None
    with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
        has_users = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        if not has_users:
            return None
        return users_db_version(conn)


def pending_steps(version: int) -> list:
    return [(v, name, fn) for v, name, fn in STEPS if v > version]


def status() -> dict:
    """``{"version", "target", "fresh", "pending": [{"version", "name"}],
    "backups": [...]}`` for the CLI and the startup line."""
    version = data_version()
    fresh = version is None
    pending = [] if fresh else pending_steps(version)
    return {
        "version": SCHEMA_VERSION if fresh else version,
        "target": SCHEMA_VERSION,
        "fresh": fresh,
        "pending": [{"version": v, "name": name} for v, name, _ in pending],
        "backups": [b["name"] for b in backups.list_backups()],
    }


# --- the runner ---------------------------------------------------------------

def ensure_current(dry_run: bool = False) -> dict:
    """Bring the data directory to SCHEMA_VERSION. Returns ``{"from", "to",
    "applied": [names], "backup": path | None}``. Raises ``NewerDataError``
    / ``TooOldDataError`` (refuse to run) or ``MigrationError`` (a step
    failed — the version stays at the last completed step; fix or restore
    the backup and rerun)."""
    version = data_version()
    if version is None:
        return {"from": SCHEMA_VERSION, "to": SCHEMA_VERSION, "applied": [], "backup": None}
    if version > SCHEMA_VERSION:
        raise _refusal(NewerDataError, version,
                       f"the data directory ({config.DATA_DIR}) is at schema version {version}, newer than "
                       f"this Gamma (version {SCHEMA_VERSION}). Run the Gamma release that wrote it, or "
                       f"restore the matching snapshot from {config.BACKUPS_DIR}.")
    if version < MIN_UPGRADABLE:
        raise _refusal(TooOldDataError, version,
                       f"the data directory is at schema version {version}; this release upgrades from "
                       f"{MIN_UPGRADABLE} at the earliest. Run {UPGRADE_VIA['release']} "
                       f"({UPGRADE_VIA['image']}) once on the same data directory first: it brings it "
                       f"to schema version {UPGRADE_VIA['schema']}.")
    pending = pending_steps(version)
    result = {"from": version, "to": SCHEMA_VERSION, "applied": [], "backup": None}
    if dry_run:
        return result
    if not pending:
        if backups.unfinished_upgrade():  # stamped its last step, stopped before tidying up
            _finish_upgrade()
        return result
    result["backup"] = _snapshot_before(version)
    log.info(f"[migrate] upgrading data directory from schema version {version} to "
             f"{SCHEMA_VERSION}; snapshot in {result['backup']}")
    for v, name, fn in pending:
        try:
            with closing(sqlite3.connect(str(config.USERS_DB))) as conn:
                fn(conn)
                conn.execute(f"PRAGMA user_version = {v}")
                conn.commit()
        except Exception as e:
            failed = MigrationError(
                f"migration step {v} ({name}) failed: {e}. The data directory is at the last "
                f"completed step; fix the cause and rerun `manage.py migrate`, or restore "
                f"{result['backup']}.")
            failed.step, failed.snapshot, failed.version = f"{v} ({name})", result["backup"], data_version()
            raise failed from e
        result["applied"].append(name)
        log.info(f"[migrate] step {v} ({name}) done")
    _finish_upgrade()
    return result


def _refusal(kind, version: int, message: str) -> MigrationError:
    error = kind(message)
    error.version = version
    return error


def guidance(error: MigrationError) -> dict:
    """What the person running this server should do about ``error``, as
    ``{"title", "summary", "steps": [str], "data_dir", "backups_dir",
    "snapshot"}`` — the one text the startup page, the API's 503, the
    CLI and the log share (gamma/app.py ``_blocked_app``, ``manage.py
    migrate``). Nothing below changes the data directory; every path tells
    the person their data is intact before it tells them what to run."""
    data_dir, backups_dir = str(config.DATA_DIR), str(config.BACKUPS_DIR)
    if isinstance(error, TooOldDataError):
        via = UPGRADE_VIA
        return {
            "title": "This Gamma needs an earlier release to upgrade your data first",
            "summary": (f"Your data directory is at schema version {error.version}; this Gamma (schema "
                        f"{SCHEMA_VERSION}) upgrades from version {MIN_UPGRADABLE} on. Nothing has been "
                        f"changed. Run {via['release']} once on the same data directory: it upgrades it to "
                        f"schema version {via['schema']}, taking a snapshot of the databases first; then "
                        f"start this version again and it finishes the upgrade."),
            "steps": [
                f"Back up the data directory ({data_dir}): a plain copy of the folder or volume is enough.",
                f"Docker Compose: in docker-compose.yml set `image: {via['image']}`, run `docker compose up -d`, "
                f"wait for the log line \"data directory upgraded\" (`docker logs gamma`), then put the image "
                f"back and run `docker compose up -d` again.",
                f"Docker without Compose: `docker run --rm -v <your data volume>:/data {via['image']}`, wait for "
                f"the same log line, stop it with Ctrl+C, then start your usual container.",
                "Desktop app: install that release from the GitHub releases page, open it once with this data "
                "directory, then install the current version again.",
            ],
            "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": "",
        }
    if isinstance(error, NewerDataError):
        return {
            "title": "This data directory was written by a newer Gamma",
            "summary": (f"Your data directory is at schema version {error.version}; this Gamma expects "
                        f"{SCHEMA_VERSION} and will not touch it. Nothing has been changed."),
            "steps": [
                "Run the Gamma release that wrote it (the newer one), or",
                f"restore the snapshot that release took before upgrading, from {backups_dir}: with the server "
                f"stopped, `manage.py backups` lists them and `manage.py backups --restore <name>` puts one back.",
            ],
            "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": "",
        }
    back = (f"Or go back: with the server stopped, `manage.py backups --restore {Path(error.snapshot).name}` "
            f"restores the snapshot, then run the previous release." if error.snapshot else
            "Or go back to the previous release with the snapshot `manage.py backups` lists.")
    return {
        "title": "The upgrade of your data directory stopped",
        "summary": (f"Migration step {error.step} failed; the data directory is at schema version "
                    f"{error.version}, the last step that completed. A snapshot of every database from before "
                    f"the upgrade is kept, and the upgrade resumes from this step at the next start."),
        "steps": [
            f"Read the cause in the server log: {error}",
            "Fix it (disk space, file permissions, a damaged database) and start the server again: the "
            "upgrade continues where it stopped, with the same snapshot.",
            back,
        ],
        "data_dir": data_dir, "backups_dir": backups_dir, "snapshot": error.snapshot,
    }


def _snapshot_before(version: int) -> str:
    """The snapshot this upgrade rolls back to. An earlier attempt that did
    not finish (a failed step, a crash — and a restart loop retrying it)
    took one before it changed anything, and that one is reused: retries
    neither pile up copies nor push the clean one out. A snapshot that
    cannot be written stops the upgrade before any step runs."""
    unfinished = backups.unfinished_upgrade()
    if unfinished:
        kept = backups.info(unfinished.get("backup") or "")
        if kept:
            log.info(f"[migrate] resuming the upgrade that started at schema version "
                     f"{unfinished.get('from')}; its snapshot {kept['name']} is kept")
            return kept["path"]
    try:
        taken = backups.create(f"v{version}", auto=True)
        backups.mark_upgrade({"from": version, "to": SCHEMA_VERSION, "backup": taken["name"],
                              "started_at": page_now()})
    except Exception as e:
        raise MigrationError(
            f"could not snapshot the data directory before upgrading it: {e}. Nothing was "
            f"changed; free disk space (or fix the cause) and start again.") from e
    return taken["path"]


def _finish_upgrade() -> None:
    """The upgrade is done: forget its marker, then keep only the newest
    automatic snapshots (a failure to prune never stops the server)."""
    backups.clear_upgrade()
    try:
        backups.prune_backups()
    except OSError as e:
        log.warning(f"[migrate] could not prune old pre-upgrade snapshots: {e}")


# --- steps --------------------------------------------------------------------
# Each step gets an open users.db connection (autocommit off) and must leave
# the database consistent when it returns; the runner stamps the version.

def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# users.db as schema version 24 shaped it, frozen: steps 20-24 create their
# tables from this, never from db.USERS_SCHEMA, which step 25 moved on (an
# account is named by its id there).
_V24_USERS_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        provider_id TEXT NOT NULL DEFAULT '',
        provider_name TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input INTEGER NOT NULL DEFAULT 0,
        output INTEGER NOT NULL DEFAULT 0,
        cache_read INTEGER NOT NULL DEFAULT 0,
        cache_write INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ai_usage_user_at ON ai_usage (username, at)",
    """CREATE TABLE IF NOT EXISTS mcp_oauth (
        kind TEXT NOT NULL,
        key_hash TEXT NOT NULL,
        value TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        PRIMARY KEY (kind, key_hash)
    )""",
    """CREATE TABLE IF NOT EXISTS integration_tokens (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        scope TEXT NOT NULL DEFAULT 'read'
    )""",
    """CREATE TABLE IF NOT EXISTS mirrors (
        workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
        remote_url TEXT NOT NULL,
        remote_ws TEXT NOT NULL,
        remote_name TEXT NOT NULL DEFAULT '',
        token TEXT NOT NULL,
        owner TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'two-way',
        remote_cursor TEXT NOT NULL DEFAULT '',
        local_cursor TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        poll_s INTEGER NOT NULL DEFAULT 30,
        on_change INTEGER NOT NULL DEFAULT 1,
        page_filter TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS publisher_sessions (
        username TEXT NOT NULL,
        host TEXT NOT NULL,
        encrypted TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (username, host)
    )""",
    """CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        is_guest INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        max_upload_mb INTEGER,
        quota_mb INTEGER,
        default_workspace TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS identities (
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        username TEXT NOT NULL REFERENCES users(username),
        email TEXT NOT NULL DEFAULT '',
        claims TEXT NOT NULL DEFAULT '{}',
        refresh_token TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL,
        revoked_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (provider, subject)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS identities_account ON identities(provider, username)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL REFERENCES users(username),
        guest_date TEXT,
        created_at TEXT NOT NULL,
        via TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'personal',
        access TEXT NOT NULL DEFAULT 'private',
        public_role TEXT NOT NULL DEFAULT 'viewer',
        quota_mb INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS workspace_members (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        username TEXT NOT NULL REFERENCES users(username),
        role TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, username)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(username)",
    """CREATE TABLE IF NOT EXISTS pending_memberships (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        subject TEXT NOT NULL,
        username TEXT NOT NULL,
        role TEXT NOT NULL,
        invited_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, subject)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pending_subject ON pending_memberships(subject)",
    """CREATE TABLE IF NOT EXISTS shares (
        token TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        page_id TEXT NOT NULL DEFAULT '',
        folder TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        audience TEXT NOT NULL DEFAULT 'anyone',
        role TEXT NOT NULL DEFAULT 'view',
        allowed_users TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_page ON shares(workspace_id, page_id) WHERE page_id != ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_folder ON shares(workspace_id, folder) WHERE folder != ''",
    """CREATE TABLE IF NOT EXISTS user_prefs (
        username TEXT NOT NULL,
        workspace_id TEXT NOT NULL DEFAULT '',
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (username, workspace_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        workspace_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        key TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        params TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL,
        progress TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        error TEXT NOT NULL DEFAULT '',
        artifact_name TEXT NOT NULL DEFAULT '',
        artifact_type TEXT NOT NULL DEFAULT '',
        artifact_size INTEGER NOT NULL DEFAULT 0,
        downloaded_at TEXT NOT NULL DEFAULT '',
        instance TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT '',
        finished_at TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON jobs(workspace_id, owner)",
]

# pages.db as schema version 25 shaped it, frozen: the statements steps 20-25
# found a workspace's file with (``_each_pages_db`` applies them), never
# db.PAGES_SCHEMA, whose block table step 26 moved on (its typed hot fields)
# and whose tombstones step 27 folded into the change log. A step after 27
# that walks the workspaces applies none of them (``schema=()``: its files
# are in the shape step 27 left, and these would give every file its
# ``deleted_pages`` back) or a frozen copy of its own time.
_V25_PAGES_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS unified_blocks (
        id TEXT PRIMARY KEY,
        parent_id TEXT REFERENCES unified_blocks(id),
        position TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        properties TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ub_parent ON unified_blocks(parent_id, position)",
    """CREATE TABLE IF NOT EXISTS deleted_pages (
        page_id TEXT PRIMARY KEY,
        deleted_at TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS sync_pages (
        page_id TEXT PRIMARY KEY,
        remote_seq INTEGER NOT NULL DEFAULT 0,
        base TEXT NOT NULL DEFAULT '{}',
        synced_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        page_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        stats TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS sync_conflicts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id TEXT NOT NULL,
        block_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        mine TEXT NOT NULL DEFAULT '',
        theirs TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT '',
        base TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS upload_orphans (
        name TEXT PRIMARY KEY,
        since TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS page_ops (
        page_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        client TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        ops TEXT NOT NULL,
        PRIMARY KEY (page_id, seq)
    ) WITHOUT ROWID""",
]


def _each_pages_db(step: str, fn, schema=_V25_PAGES_SCHEMA) -> None:
    """``fn(conn)`` on every workspace's pages.db, the ``schema`` statements
    applied first (``_V25_PAGES_SCHEMA`` unless the step says otherwise) and
    the SQL functions registered (``db.register_functions``). A file that
    fails — a damaged database — is logged as an error and skipped: one
    broken library must not keep every other account's server from
    starting. That workspace stays as it was for an admin to restore; a
    restore from the step's snapshot needs the step run again on that
    file."""
    if not config.WORKSPACES_DIR.is_dir():
        return
    for ws_root in sorted(config.WORKSPACES_DIR.iterdir()):
        pages_db = ws_root / "pages.db"
        if not ws_root.is_dir() or not pages_db.is_file() or ws_root.name.startswith("."):
            continue  # (a dot-name: a deleted workspace's leftover, workspaces.remove_leftovers)
        try:
            with closing(sqlite3.connect(str(pages_db))) as pdb:
                register_functions(pdb)
                for stmt in schema:
                    pdb.execute(stmt)
                fn(pdb)
        except sqlite3.Error as e:
            log.error(f"[migrate] step {step}: workspace {ws_root.name} skipped, its pages.db failed: {e}")


def _v20_guest_accounts(conn: sqlite3.Connection) -> None:
    """Guests became throwaway accounts minted per visitor (gamma/guests.py,
    docs/dev/guests.md): the legacy shared ``guest`` account goes with its
    sessions, memberships, prefs and personal workspace (rows, directory and
    stored snapshots). Any other ``is_guest`` row — ``create-user`` without a
    password used to make one — becomes a normal password-less account, so
    the new guest expiry never deletes it."""
    legacy = conn.execute("SELECT default_workspace FROM users WHERE username = 'guest' AND is_guest = 1").fetchone()
    if legacy:
        ws_ids = {r[0] for r in conn.execute(
            "SELECT w.id FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id "
            "WHERE m.username = 'guest' AND w.kind = 'personal'")}
        if legacy[0]:
            ws_ids.add(legacy[0])
        for ws in sorted(ws_ids):
            try:
                safe_ws_id(ws)
            except ValueError:
                continue
            for path in (config.WORKSPACES_DIR / ws, config.BACKUPS_DIR / "workspaces" / ws):
                if path.is_dir():
                    try:
                        shutil.rmtree(str(path))
                    except OSError as e:  # a leftover directory is listed to admins as an orphan
                        log.warning(f"[migrate] could not remove the guest workspace directory {path}: {e}")
            for table, column in (("integration_tokens", "workspace_id"), ("workspace_members", "workspace_id"),
                                  ("pending_memberships", "workspace_id"), ("shares", "workspace_id"),
                                  ("user_prefs", "workspace_id"), ("mirrors", "workspace_id"),
                                  ("workspaces", "id")):
                conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (ws,))
        for table in ("sessions", "identities", "integration_tokens", "publisher_sessions", "user_prefs",
                      "workspace_members", "ai_usage"):
            conn.execute(f"DELETE FROM {table} WHERE username = 'guest'")
        conn.execute("DELETE FROM users WHERE username = 'guest' AND is_guest = 1")
        log.info("[migrate] removed the legacy shared guest account and its workspace")
    conn.execute("UPDATE users SET is_guest = 0 WHERE is_guest = 1")
    conn.commit()


def _v21_folder_shares(conn: sqlite3.Connection) -> None:
    """``shares`` gains ``folder``: a share names a page (``page_id``) or a
    folder-label path (``folder``, the pages filed there or below it), the
    other column ''. The page unique index becomes partial and a folder
    twin joins it (docs/dev/api.md "Shares")."""
    if "folder" not in _columns(conn, "shares"):
        conn.execute("ALTER TABLE shares ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
    conn.execute("DROP INDEX IF EXISTS idx_shares_page")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_page ON shares(workspace_id, page_id) WHERE page_id != ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_folder ON shares(workspace_id, folder) WHERE folder != ''")
    conn.commit()


def _v22_upload_orphans(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db gains ``upload_orphans`` (``name``,
    ``since``): stored files nothing references any more are kept 30 days
    before they are purged, instead of being deleted with their last
    reference (gamma/upload_gc.py). The table starts empty — the old sweep
    left no unreferenced file older than its 15-minute grace; the first
    background pass after startup records whatever there is."""
    _each_pages_db("22 (upload_orphans)", lambda pdb: pdb.commit())


def _v23_page_trash(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db gains the reserved ``trash`` row beside
    ``root``: the parent of the pages in Recently deleted (gamma/trash.py),
    written now so no block can take the id before the first page is
    deleted. A workspace where a block already holds it is named in the log
    and left as it is (moving a page to the trash then refuses)."""
    def add_trash(pdb):
        row = pdb.execute("SELECT parent_id FROM unified_blocks WHERE id = 'trash'").fetchone()
        if row is None:
            now = page_now()
            pdb.execute("INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, "
                        "updated_at) VALUES ('trash', NULL, 'a1', '', '{}', ?, ?)", (now, now))
            pdb.commit()
        elif row[0] is not None:
            log.warning("[migrate] step 23 (page_trash): a block holds the reserved id 'trash' in "
                        f"{pdb.execute('PRAGMA database_list').fetchone()[2]}; that workspace has no trash")

    _each_pages_db("23 (page_trash)", add_trash)


def _v24_jobs(conn: sqlite3.Connection) -> None:
    """Adds the ``jobs`` table (+ its owner and workspace indexes) in
    users.db: background jobs — exports, backups, restores, imports, the
    search indexer — with their progress, result and produced file
    (gamma/jobs.py). Nothing else changes."""
    for stmt in _V24_USERS_SCHEMA:
        if stmt.startswith(("CREATE TABLE IF NOT EXISTS jobs ", "CREATE INDEX IF NOT EXISTS idx_jobs_")):
            conn.execute(stmt)
    conn.commit()


# The op-log labels of writers that are no account, frozen: a share link's
# visitor (gamma/auth.py LINK_ACTOR_PREFIX) and a mirror's round
# (gamma/sync_engine.py ACTOR). Step 25 keeps them as they are.
_V25_LINK_PREFIX = "link:"
_V25_MIRROR_ACTOR = "mirror"
# The users.db tables whose ``username`` column becomes ``user_id``.
_V25_RENAMED = ("sessions", "identities", "integration_tokens", "publisher_sessions", "workspace_members",
                "user_prefs", "ai_usage")
# The users.db columns that keep their name and hold the id from step 25 on.
_V25_PEOPLE = (("workspaces", "created_by"), ("workspace_members", "added_by"),
               ("pending_memberships", "invited_by"), ("mirrors", "owner"), ("jobs", "owner"))
# Columns step 31 drops. Step 25 rebuilds these tables in db.USERS_SCHEMA's
# shape, which has not got them, so it leaves them out as well.
_V31_DROPPED = {"sessions": ("guest_date",)}
# mcp_oauth records that name an account (an assistant's consent and code, a
# cloud sign-in that links): minutes-long, dropped rather than rewritten.
_V25_NAMED_OAUTH = ("consent", "code", "cloud_login")


def _v25_account_ids(conn: sqlite3.Connection) -> None:
    """Accounts are keyed by a stable id: ``users`` gains ``id`` (a random
    token, the primary key) beside a unique ``username``, and every column
    that named an account holds the id — ``username`` columns become
    ``user_id`` (rows of no account dropped), ``created_by`` / ``added_by``
    / ``invited_by`` / ``owner`` keep their names (a name no account has
    becomes ''; a job of one goes). ``shares.allowed_users`` becomes
    ``share_users`` rows (names no account has dropped). The publisher
    sessions are sealed under the id. Then every workspace's op log,
    tombstones and trashed pages' ``deleted_by``, and the backup task files,
    name the id. users.db changes as one transaction; the rest is rewritten
    where it still names an account, so a rerun finishes what a crash
    left."""
    if "id" not in _columns(conn, "users"):
        conn.execute("BEGIN")
        _v25_users_db(conn)
        conn.commit()
    ids = dict(conn.execute("SELECT username, id FROM users").fetchall())
    known = set(ids.values())
    _each_pages_db("25 (account_ids)", lambda pdb: _v25_actors(pdb, ids, known))
    tasks = config.BACKUPS_DIR / "tasks"
    for path in sorted(tasks.glob("*.json")) if tasks.is_dir() else []:
        task = json.loads(path.read_text(encoding="utf-8"))
        owner = task.get("owner") or ""
        if owner and owner not in known:
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({**task, "owner": ids.get(owner, "")}), encoding="utf-8")
            temp.replace(path)


def _v25_rebuild(conn, table: str, columns: str, select: str) -> None:
    """Recreate ``table`` in its db.USERS_SCHEMA shape, with its indexes,
    from the rows ``select`` reads out of the old one (into ``columns``)."""
    create = next(s for s in USERS_SCHEMA if s.startswith(f"CREATE TABLE IF NOT EXISTS {table} ("))
    conn.execute(f"DROP TABLE IF EXISTS {table}_new")
    conn.execute(create.replace(f"IF NOT EXISTS {table} (", f"{table}_new (", 1))
    conn.execute(f"INSERT INTO {table}_new ({columns}) {select}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    for stmt in USERS_SCHEMA:
        if re.search(rf" ON {table} ?\(", stmt):
            conn.execute(stmt)


def _v25_users_db(conn) -> None:
    """Step 25's users.db half (one transaction, the caller's)."""
    conn.execute("ALTER TABLE users ADD COLUMN id TEXT")
    for name, in conn.execute("SELECT username FROM users").fetchall():
        conn.execute("UPDATE users SET id = ? WHERE username = ?", (new_account_id(), name))
    cols = _columns(conn, "users")
    _v25_rebuild(conn, "users", ", ".join(cols), f"SELECT {', '.join(cols)} FROM users")
    for table in _V25_RENAMED:
        cols = [c for c in _columns(conn, table) if c not in _V31_DROPPED.get(table, ())]
        new = ", ".join("user_id" if c == "username" else c for c in cols)
        old = ", ".join("u.id" if c == "username" else f"t.{c}" for c in cols)
        _v25_rebuild(conn, table, new, f"SELECT {old} FROM {table} t JOIN users u ON u.username = t.username")
    conn.execute("DELETE FROM jobs WHERE owner != '' AND owner NOT IN (SELECT username FROM users)")
    for table, column in _V25_PEOPLE:
        conn.execute(f"UPDATE {table} SET {column} = "
                     f"COALESCE((SELECT u.id FROM users u WHERE u.username = {table}.{column}), '')")
    conn.execute(next(s for s in USERS_SCHEMA if s.startswith("CREATE TABLE IF NOT EXISTS share_users (")))
    for token, allowed in conn.execute("SELECT token, allowed_users FROM shares WHERE allowed_users != ''").fetchall():
        for item in allowed.split(","):
            name, _, role = item.strip().partition(":")
            conn.execute("INSERT OR IGNORE INTO share_users (token, user_id, role) "
                         "SELECT ?, id, ? FROM users WHERE username = ?",
                         (token, role if role in ("view", "edit") else "view", name))
    cols = [c for c in _columns(conn, "shares") if c != "allowed_users"]
    old = ", ".join("COALESCE(u.id, '')" if c == "created_by" else f"s.{c}" for c in cols)
    _v25_rebuild(conn, "shares", ", ".join(cols),
                 f"SELECT {old} FROM shares s LEFT JOIN users u ON u.username = s.created_by")
    conn.execute(f"DELETE FROM mcp_oauth WHERE kind IN ({', '.join('?' * len(_V25_NAMED_OAUTH))})", _V25_NAMED_OAUTH)
    _v25_reseal_publisher_sessions(conn)


def _v25_reseal_publisher_sessions(conn) -> None:
    """A publisher snapshot is sealed with the account it belongs to
    (gamma/publisher_sessions.py checks it on use): seal each under the id.
    One that no longer opens could never be used again and goes."""
    rows = conn.execute("SELECT user_id, host, encrypted FROM publisher_sessions").fetchall()
    if not rows:
        return
    from cryptography.fernet import InvalidToken

    from .publisher_sessions import cipher  # local: the key file is only needed here

    box = cipher()
    for user_id, host, encrypted in rows:
        try:
            payload = json.loads(box.decrypt(encrypted.encode("ascii")))
        except (InvalidToken, ValueError):
            conn.execute("DELETE FROM publisher_sessions WHERE user_id = ? AND host = ?", (user_id, host))
            continue
        payload["user"] = user_id
        conn.execute("UPDATE publisher_sessions SET encrypted = ? WHERE user_id = ? AND host = ?",
                     (box.encrypt(json.dumps(payload).encode()).decode("ascii"), user_id, host))


def _v25_actor(actor: str, ids: dict, known: set) -> str:
    """A writer as step 25 records it: an account's name becomes its id; an
    id, a label of a writer that is no account and '' stay; a name no
    account has becomes ''."""
    if not actor or actor in known or actor.startswith(_V25_LINK_PREFIX) or actor == _V25_MIRROR_ACTOR:
        return actor
    return ids.get(actor, "")


def _v25_actors(pdb, ids: dict, known: set) -> None:
    """One workspace's op log, tombstones and trashed pages name the id."""
    for table in ("page_ops", "deleted_pages"):
        for actor, in pdb.execute(f"SELECT DISTINCT actor FROM {table}").fetchall():
            new = _v25_actor(actor, ids, known)
            if new != actor:
                pdb.execute(f"UPDATE {table} SET actor = ? WHERE actor = ?", (new, actor))
    for page_id, props in pdb.execute(
            "SELECT id, properties FROM unified_blocks WHERE parent_id = 'trash'").fetchall():
        try:
            data = json.loads(props or "{}")
        except ValueError:
            continue
        by = data.get("deleted_by")
        new = _v25_actor(by, ids, known) if isinstance(by, str) else by
        if new != by:
            pdb.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?",
                        (json.dumps({**data, "deleted_by": new}), page_id))
    pdb.commit()


def _v26_block_columns(conn: sqlite3.Connection) -> None:
    """Every workspace's block table gains its typed hot fields, indexed:
    ``page_id`` (stored, filled in by the parent walk — the page a row lives
    under, '' on the reserved rows) and the generated ``kind`` and
    ``doc_id`` (gamma/normalize.py ``block_columns``, which a restored older
    backup goes through as well). One transaction per file; a file that has
    them is left as it is."""
    _each_pages_db("26 (block_columns)", block_columns)


def _v27_page_changes(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db gains its change log, ``page_changes``
    (gamma/normalize.py ``page_changes``, which a restored older backup goes
    through as well): a row per page with seqs in the order the pages were
    last written — live in the library, deleted in Recently deleted — then
    one per ``deleted_pages`` tombstone, which is dropped. One transaction
    per file. A mirror's cursors into the old time-ordered feeds mean
    nothing in the log: they start over (``''``), and the next round walks
    both feeds whole, finding nothing to do for a page that did not move."""
    _each_pages_db("27 (page_changes)", page_changes)
    conn.execute("UPDATE mirrors SET remote_cursor = '', local_cursor = '' "
                 "WHERE remote_cursor != '' OR local_cursor != ''")
    conn.commit()


def _v28_chats_and_notes_index(conn: sqlite3.Connection) -> None:
    """Every workspace's pages.db takes what its data.db held that is not
    derived, and the notes index: the AI chats move in (gamma/normalize.py
    ``pages_db_chats`` — ``chats.block_id`` becomes ``bucket``), the notes
    index is created there and built from the blocks (``block_fts``: the
    view, the FTS5 table, the triggers), and data.db drops its own notes
    index and bookkeeping (``normalize_data_db``). A restored older backup
    goes through the same. The files are at step 27's shape: no frozen
    statements first. Re-running finds the chats moved and builds the
    index again."""
    _each_pages_db("28 (chats_and_notes_index)", _v28_workspace, schema=())


def _v28_workspace(pdb: sqlite3.Connection) -> None:
    data_db = Path(pdb.execute("PRAGMA database_list").fetchone()[2]).with_name("data.db")
    pages_db_chats(pdb, data_db)
    block_fts(pdb)
    if data_db.is_file():
        with closing(sqlite3.connect(str(data_db))) as ddb:
            normalize_data_db(ddb)


def _v29_folder_blocks(conn: sqlite3.Connection) -> None:
    """Folders and labels become blocks (docs/dev/home_library.md). Every
    workspace's pages.db: the ``kind`` column gains its ``folder`` /
    ``label`` cases (``block_columns``), and the folder and label trees are
    built from the paths and names in use, the pages' filing and the folder
    chats rewritten to their ids (``folder_blocks``; a restored older backup
    goes through both). Then in users.db, per workspace: a folder share
    names its folder's id (one whose folder is gone is deleted), and each
    account's ``pinned-folders`` pref becomes ``pinned`` on those folders —
    the newest pin of any member, as the folder is one block for all of
    them — and is dropped. Re-runnable: a converted file is left as it is,
    and the users.db half resolves the paths against the trees it finds."""
    _each_pages_db("29 (folder_blocks)", lambda pdb: _v29_workspace(conn, pdb), schema=())
    conn.execute("DELETE FROM user_prefs WHERE key = 'pinned-folders'")
    conn.commit()


def _v29_workspace(conn: sqlite3.Connection, pdb: sqlite3.Connection) -> None:
    block_columns(pdb)
    folder_blocks(pdb)
    ws = Path(pdb.execute("PRAGMA database_list").fetchone()[2]).parent.name
    folders = {r[0] for r in pdb.execute("SELECT id FROM unified_blocks WHERE page_id = ?", (FOLDERS,))}

    def folder_of(value: str) -> str:
        if value in folders:
            return value
        found = folder_by_path(pdb, [s for s in value.split("/") if s.strip()])
        return found[0] if found else ""

    pins: dict[str, str] = {}
    for (raw,) in conn.execute("SELECT value FROM user_prefs WHERE key = 'pinned-folders' AND workspace_id = ?",
                               (ws,)).fetchall():
        try:
            listed = json.loads(raw)
        except ValueError:
            continue
        for pin in listed if isinstance(listed, list) else []:
            folder_id = folder_of(str(pin.get("path") or "")) if isinstance(pin, dict) else ""
            if folder_id:
                pins[folder_id] = max(pins.get(folder_id, ""), str(pin.get("at") or "") or page_now())
    for folder_id, at in pins.items():
        props = json.loads(pdb.execute("SELECT properties FROM unified_blocks WHERE id = ?", (folder_id,)).fetchone()[0])
        if str(props.get("pinned") or "") < at:
            pdb.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?",
                        (json.dumps({**props, "pinned": at}), folder_id))
    pdb.commit()
    for token, value in conn.execute("SELECT token, folder FROM shares WHERE workspace_id = ? AND folder != ''",
                                     (ws,)).fetchall():
        folder_id = folder_of(value)
        # One share per folder (idx_shares_folder): a second path that
        # resolves to the same folder (a case variant) would keep its path
        # and never open, so it goes like a share whose folder is gone.
        if folder_id and folder_id != value:
            folder_id = "" if conn.execute("UPDATE OR IGNORE shares SET folder = ? WHERE token = ?",
                                           (folder_id, token)).rowcount == 0 else folder_id
        if not folder_id:
            conn.execute("DELETE FROM share_users WHERE token = ?", (token,))
            conn.execute("DELETE FROM shares WHERE token = ?", (token,))
    conn.commit()


def _v30_highlight_shape(conn: sqlite3.Connection) -> None:
    """The highlight shape (gamma/highlights.py). Every workspace's
    pages.db: the ``kind`` column's ``highlight`` case reads
    ``pdf_position`` (``block_columns``), and the blocks take the shape
    (``highlight_shape``: the block id is the highlight's id, a position
    keeps the page size once, ``pdf_page`` goes but on text boxes, links
    to a highlight name its block, a page's PDF URL is derived from its
    ``doc_id``). Nothing is stamped or touched: a shape is no edit, and
    a mirror and its remote upgraded apart rewrite their copies of a page
    alike. users.db is untouched. Re-runnable: a converted file is left as
    it is; a restored older backup goes through the same."""
    _each_pages_db("30 (highlight_shape)", _v30_workspace, schema=())


def _v30_workspace(pdb: sqlite3.Connection) -> None:
    block_columns(pdb)
    highlight_shape(pdb)


def _v31_session_columns(conn: sqlite3.Connection) -> None:
    """``sessions`` loses ``guest_date``, a column the guest login wrote and
    nothing read (``_V31_DROPPED``): the table is rebuilt in its
    db.USERS_SCHEMA shape with its rows, so every session stays signed in.
    A directory that step 25 brought past this already is left as it is."""
    for table, dropped in _V31_DROPPED.items():
        cols = [c for c in _columns(conn, table) if c not in dropped]
        if len(cols) < len(_columns(conn, table)):
            _v25_rebuild(conn, table, ", ".join(cols), f"SELECT {', '.join(cols)} FROM {table}")
    conn.commit()


STEPS = [
    (20, "guest_accounts", _v20_guest_accounts),
    (21, "folder_shares", _v21_folder_shares),
    (22, "upload_orphans", _v22_upload_orphans),
    (23, "page_trash", _v23_page_trash),
    (24, "jobs", _v24_jobs),
    (25, "account_ids", _v25_account_ids),
    (26, "block_columns", _v26_block_columns),
    (27, "page_changes", _v27_page_changes),
    (28, "chats_and_notes_index", _v28_chats_and_notes_index),
    (29, "folder_blocks", _v29_folder_blocks),
    (30, "highlight_shape", _v30_highlight_shape),
    (31, "session_columns", _v31_session_columns),
]
