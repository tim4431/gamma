"""SQLite helpers: schemas, connections, workspace paths, prefs, timestamps.

Two kinds of database (docs/dev/user_db.md):

- ``users.db`` — global: accounts, sessions, workspaces + memberships, page
  shares, personal prefs, server settings. Every column that names an
  account holds its ``users.id`` (``new_account_id``), never its username,
  so a rename is one UPDATE; ``account_id`` / ``account_name`` translate.
  Its ``PRAGMA user_version`` is
  the data directory's schema version (``SCHEMA_VERSION``); a data
  directory behind it is upgraded by ``gamma/migrations.py`` before the
  server serves anything, one ahead of it is refused.
- per workspace, under ``workspaces/<id>/``: ``pages.db`` (the block tree,
  the op and change logs, the AI chats, the notes index) and ``data.db``
  (only what can be deleted and rebuilt: the PDF index and manifests,
  cover snapshots). These files carry no version: their statements are
  ``CREATE ... IF NOT EXISTS`` applied on every connect, and a restored
  backup is normalized by ``gamma/normalize.py`` when it is imported.

``USERS_SCHEMA`` is always the CURRENT shape. Older shapes are not patched
here on connect — that is what the numbered migration steps are for.
"""

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import USERS_DB, WORKSPACES_DIR
from .textnorm import normalize_text

# The data-directory schema version this code expects (users.db
# ``PRAGMA user_version``). Bump it together with a new step in
# gamma/migrations.py — never without one, never without bumping.
SCHEMA_VERSION = 31


# How long a connection waits for another connection's write lock before
# "database is locked" (sqlite3's own default is 5 s). Pass it to every
# sqlite3.connect of users.db and of a workspace's databases.
BUSY_TIMEOUT_S = 10


class SchemaOutdated(RuntimeError):
    """users.db is behind SCHEMA_VERSION: run the migrations first (the app
    does at startup; ``python manage.py migrate`` by hand)."""


def page_now() -> str:
    # UTC ISO string with Z suffix so clients parse it correctly.
    return format_stamp(datetime.now(timezone.utc))


def format_stamp(t: datetime) -> str:
    """A UTC datetime in the ``page_now`` shape."""
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_stamp(stamp) -> datetime | None:
    """A stored time (the ``page_now`` shape, or any ISO time) as a
    datetime; None when it cannot be read — what that means (expired, due,
    now) is the caller's call."""
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# Identifiers that become a single path segment. Both exclude '/' and '\', so
# a validated value can never introduce a path separator; '.'/'..' are
# rejected outright so they can't climb out of the data directory either.
# These guard every filesystem path built from a workspace id or doc id — the
# last line of defense against traversal even after upstream auth checks.
_WS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def safe_ws_id(ws: str) -> str:
    """A workspace id names the directory workspaces/<id>/."""
    if not isinstance(ws, str) or not _WS_ID_RE.match(ws):
        raise ValueError(f"unsafe workspace id: {ws!r}")
    return ws


def safe_doc_id(doc_id: str) -> str:
    if not isinstance(doc_id, str) or doc_id in (".", "..") or not _DOC_ID_RE.match(doc_id):
        raise ValueError(f"unsafe doc id: {doc_id!r}")
    return doc_id


USERS_SCHEMA = [
    # One row per AI call an account made, from the provider's own token
    # report (gamma/ai_usage.py); Settings -> AI sums them.
    """CREATE TABLE IF NOT EXISTS ai_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
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
    "CREATE INDEX IF NOT EXISTS ai_usage_user_at ON ai_usage (user_id, at)",
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
        user_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        scope TEXT NOT NULL DEFAULT 'read'
    )""",
    # mirrors = local workspaces that are offline copies of a workspace on
    # another Gamma server (gamma/sync_engine.py): where it lives, the
    # write-scope token that signs the sync in (Fernet-encrypted with the
    # data directory's key, publisher_sessions.cipher), the change-feed
    # cursors, and the last run's status as JSON.
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
        user_id TEXT NOT NULL,
        host TEXT NOT NULL,
        encrypted TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, host)
    )""",
    # id: the account's stable key (new_account_id), what every column that
    # names an account holds — NOT NULL, which a TEXT primary key in SQLite
    # is not by itself: a row inserted without one would be an account
    # nothing can name; username: the name people see and sign in with,
    # which a rename alone changes. default_workspace: the personal
    # workspace created with the account — where a request lands when it
    # names no workspace (the browser extension, older clients), and the one
    # that cannot be left or deleted.
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT NOT NULL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        is_guest INTEGER NOT NULL DEFAULT 0,
        is_admin INTEGER NOT NULL DEFAULT 0,
        max_upload_mb INTEGER,
        quota_mb INTEGER,
        default_workspace TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    # A cloud identity linked to an account (gamma/cloud_auth.py): the
    # account server's stable subject, the last verified claims (handle,
    # plan, email), the refresh token (Fernet-encrypted with the data
    # directory's key; empty when the sign-in handed none out) and
    # revoked_at, when the account server last refused that grant (cleared
    # by the next sign-in). One per account and provider.
    """CREATE TABLE IF NOT EXISTS identities (
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id),
        email TEXT NOT NULL DEFAULT '',
        claims TEXT NOT NULL DEFAULT '{}',
        refresh_token TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL,
        revoked_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (provider, subject)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS identities_account ON identities(provider, user_id)""",
    # via: how the session was minted — '' a password (or the guest), 'cloud'
    # a Gamma Cloud sign-in; the grant check ends only the 'cloud' ones.
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        via TEXT NOT NULL DEFAULT ''
    )""",
    # A workspace is a library: its own pages.db / data.db / uploads under
    # workspaces/<id>/. `id` is a random token (never a name, so renaming a
    # workspace or an account moves no files). kind (gamma/workspaces.py):
    # "personal" = one account's own library (its single member; counts
    # against that account's quota; one of them is users.default_workspace);
    # "shared" = admin-created, members with roles — owner (manage members,
    # rename, delete), editor (read + write), viewer (read). access:
    # "private" = members only; "public" = every signed-in account on the
    # server is in at public_role, explicit members keep their own role.
    # quota_mb: a shared workspace's own storage cap (NULL = unlimited).
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
        user_id TEXT NOT NULL REFERENCES users(id),
        role TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        PRIMARY KEY (workspace_id, user_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_wm_user ON workspace_members(user_id)",
    # An invitation to a shared workspace for someone who has no account on
    # this server yet, named by their Gamma Cloud account (gamma/workspaces.py
    # invite_cloud): subject = the account server's stable id, username = the
    # cloud username as typed (lowercase). Their first cloud sign-in turns it
    # into a workspace_members row (claim_pending_memberships).
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
    # Share links, one per (workspace, page) or per (workspace, folder): a row
    # names a page (page_id, the shared page's root block) OR a folder
    # (folder, a folder block's id — the pages filed there or below it,
    # read live); the other column is ''. audience: who may open the link —
    # "anyone" (no login), "users" (any signed-in non-guest account), "list"
    # (only the people share_users invites). role: "view" or "edit" (edit
    # never applies to anonymous viewers — see gamma/auth.py share_access).
    """CREATE TABLE IF NOT EXISTS shares (
        token TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        page_id TEXT NOT NULL DEFAULT '',
        folder TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        audience TEXT NOT NULL DEFAULT 'anyone',
        role TEXT NOT NULL DEFAULT 'view',
        created_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_page ON shares(workspace_id, page_id) WHERE page_id != ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_folder ON shares(workspace_id, folder) WHERE folder != ''",
    # The people a share link invites, each with their own role ("view" or
    # "edit") whatever the link's general access says. The rows go with
    # their share and with their account.
    """CREATE TABLE IF NOT EXISTS share_users (
        token TEXT NOT NULL,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        PRIMARY KEY (token, user_id)
    )""",
    # Small JSON values that follow the ACCOUNT (docs/dev/settings.md):
    # workspace_id '' = personal (appearance, the AI provider entries),
    # otherwise per account AND workspace (open tabs, recents — they name
    # pages of that workspace). See USER_PREF_KEYS / pref_scope.
    """CREATE TABLE IF NOT EXISTS user_prefs (
        user_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL DEFAULT '',
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, workspace_id, key)
    )""",
    # Server-wide admin-tunable settings (see gamma/server_settings.py) — a
    # tiny KV, global because limits like upload size apply to every user.
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # Background jobs (gamma/jobs.py, docs/dev/tasks.md): exports, backups,
    # restores, imports, the search indexer. owner: the id of the account that
    # started it, '' for a workspace's own work (the indexer). key: what makes two
    # jobs of one owner and kind the same work (an import's review id).
    # params / progress / result are JSON. The artifact_* columns describe
    # the file a finished job produced (jobs/<id>/artifact); instance is the
    # server process that runs it, so a restart can tell its own jobs from
    # the ones a stopped process left behind.
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

# unified_blocks' typed hot fields, after the seven columns blocks always
# had (docs/dev/user_db.md "pages.db"). ``page_id`` is stored, and every
# writer sets it: the top block the row lives under — a page's own id on the
# page and on every block inside it, kept while the page is in Recently
# deleted; ``folders`` / ``labels`` on a folder or label block (the
# pseudo-pages, gamma/blocks_store.py ``TREES``); '' on the reserved
# parentless rows (``root``, ``trash``, ``folders``, ``labels``).
# ``kind`` and ``doc_id`` are generated VIRTUAL columns, computed from
# ``parent_id``, ``page_id`` and ``properties`` when read and never
# written: the op log and the mirror carry ``properties`` alone. ``kind``
# is NULL on the reserved rows, ``page`` on a page (in the library or the
# trash), ``folder`` / ``label`` in the pseudo-pages, else what the block's
# properties make it, tested in this order the way the frontend tests them
# (ink: an ``ink_url`` key; text box, sheet: an object; link: a non-empty
# value; highlight: a ``pdf_position`` object, which ink groups and link
# regions carry too — gamma/highlights.py). An older file gains them, or a
# ``kind`` of an older definition, through normalize.block_columns.
BLOCK_HOT_COLUMNS = {
    "page_id": "page_id TEXT NOT NULL DEFAULT ''",
    "kind": """kind TEXT GENERATED ALWAYS AS (CASE
            WHEN parent_id IS NULL THEN NULL
            WHEN parent_id IN ('root', 'trash') THEN 'page'
            WHEN page_id = 'folders' THEN 'folder'
            WHEN page_id = 'labels' THEN 'label'
            WHEN json_type(properties, '$.ink_url') IS NOT NULL THEN 'ink'
            WHEN json_type(properties, '$.text_box') = 'object' THEN 'text_box'
            WHEN json_type(properties, '$.sheet') = 'object' THEN 'sheet'
            WHEN json_extract(properties, '$.link_url') != ''
                OR json_extract(properties, '$.link_page_id') != '' THEN 'link'
            WHEN json_type(properties, '$.pdf_position') = 'object' THEN 'highlight'
            ELSE 'note' END) VIRTUAL""",
    "doc_id": "doc_id TEXT GENERATED ALWAYS AS (json_extract(properties, '$.doc_id')) VIRTUAL",
}
BLOCK_HOT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ub_page ON unified_blocks(page_id)",
    "CREATE INDEX IF NOT EXISTS idx_ub_kind ON unified_blocks(kind)",
    "CREATE INDEX IF NOT EXISTS idx_ub_doc ON unified_blocks(doc_id) WHERE doc_id IS NOT NULL",
]
_HOT_COLUMNS_SQL = ",\n        ".join(BLOCK_HOT_COLUMNS.values())

# The notes index (docs/dev/user_db.md "pages.db"): ``block_fts``, an FTS5
# table whose content is the view ``block_fts_src`` — every block inside a
# page, highlights included (not a page's own row, nor the reserved rows;
# folder and label names are rows of their pseudo-pages), keyed by the
# block's rowid, its text in the search form (``textnorm``, the function
# ``register_functions`` gives a pages.db connection) cut at
# NOTES_INDEX_CHARS. A page in Recently deleted keeps its rows (its blocks
# keep their ``page_id``), and a search keeps to the pages it reaches
# (gamma/block_index.py), so trashing and restoring a page, which move only
# its own row, change nothing here. The triggers keep the index current
# inside each write's own transaction, for the rows the write touched. The
# index holds no text of its own: snippets and the UNINDEXED columns are
# read back through the view, and a trigger's 'delete' must hand FTS5
# exactly what was indexed. Hence two rules: a change to the textnorm rules
# ships with a migration step that rebuilds every pages.db's index
# (normalize.block_fts), and nothing may renumber the block rowids — no
# VACUUM of a pages.db (unified_blocks has no INTEGER PRIMARY KEY, so VACUUM
# may give its rows new rowids).
NOTES_INDEX_CHARS = 20000  # per block


def _fts_row(row: str) -> str:
    """A trigger's ``old`` or ``new`` row as the view shows it."""
    return f"{row}.rowid, {row}.id, {row}.page_id, substr(textnorm({row}.content), 1, {NOTES_INDEX_CHARS})"


def _fts_indexed(row: str) -> str:
    """Whether the view shows a trigger's ``old`` or ``new`` row."""
    return f"{row}.parent_id NOT IN ('root', 'trash') AND {row}.page_id != ''"


BLOCK_FTS_SCHEMA = [
    f"""CREATE VIEW IF NOT EXISTS block_fts_src (rowid, block_id, page_id, content) AS
        SELECT {_fts_row('b')} FROM unified_blocks b WHERE {_fts_indexed('b')}""",
    "CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(block_id UNINDEXED, page_id UNINDEXED, content, "
    "content='block_fts_src')",
    f"""CREATE TRIGGER IF NOT EXISTS block_fts_insert AFTER INSERT ON unified_blocks
        WHEN {_fts_indexed('new')} BEGIN
            INSERT INTO block_fts (rowid, block_id, page_id, content) VALUES ({_fts_row('new')});
        END""",
    f"""CREATE TRIGGER IF NOT EXISTS block_fts_delete AFTER DELETE ON unified_blocks
        WHEN {_fts_indexed('old')} BEGIN
            INSERT INTO block_fts (block_fts, rowid, block_id, page_id, content) VALUES ('delete', {_fts_row('old')});
        END""",
    f"""CREATE TRIGGER IF NOT EXISTS block_fts_update AFTER UPDATE OF content, page_id, parent_id ON unified_blocks
        WHEN old.content IS NOT new.content OR old.page_id IS NOT new.page_id
            OR old.parent_id IS NOT new.parent_id BEGIN
            INSERT INTO block_fts (block_fts, rowid, block_id, page_id, content)
                SELECT 'delete', {_fts_row('old')} WHERE {_fts_indexed('old')};
            INSERT INTO block_fts (rowid, block_id, page_id, content)
                SELECT {_fts_row('new')} WHERE {_fts_indexed('new')};
        END""",
]

# The AI chats (gamma/routers/chats.py), kept with the pages they are about.
CHATS_SCHEMA = [
    # chats = the ACTIVE conversation per bucket: a page's id, a folder
    # block's id (the folder view's chat) or "home" (the library root);
    # `title` is the user-given name of that conversation, `updated_at` its
    # version. A page's chats go when the page is deleted for good
    # (ops.delete_page); a deleted folder's are filed into the library's
    # history (routers/folders.py).
    """CREATE TABLE IF NOT EXISTS chats (
        bucket TEXT PRIMARY KEY,
        messages TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT ''
    )""",
    # chat_history = earlier conversations of a bucket ("New chat" archives
    # the active one here; opening an entry swaps it back into `chats`).
    """CREATE TABLE IF NOT EXISTS chat_history (
        id TEXT PRIMARY KEY,
        bucket TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        messages TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS chat_history_bucket ON chat_history (bucket, updated_at)",
]
_CHAT_COLUMNS = {"chats": "bucket, messages, updated_at, title",
                 "chat_history": "id, bucket, title, messages, created_at, updated_at"}


def copy_chats(src: sqlite3.Connection, dst: sqlite3.Connection, where: str = "1", args=()) -> int:
    """Copy the conversations ``where`` (a condition on ``bucket``, with
    ``args``) selects from the pages.db ``src`` into the pages.db ``dst``,
    both tables, keeping every one ``dst`` has (a bucket's active
    conversation, an archived one by its id) — inside the callers'
    transactions: a backup merge, a Gamma export. Returns how many were
    added."""
    added = 0
    for table, columns in _CHAT_COLUMNS.items():
        rows = src.execute(f"SELECT {columns} FROM {table} WHERE {where}", args).fetchall()
        marks = ", ".join("?" * len(columns.split(", ")))
        added += dst.executemany(f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({marks})", rows).rowcount
    return added


PAGES_SCHEMA = [
    # The implicit rowid is the notes index's key (BLOCK_FTS_SCHEMA): never
    # VACUUM a pages.db.
    f"""CREATE TABLE IF NOT EXISTS unified_blocks (
        id TEXT PRIMARY KEY,
        parent_id TEXT REFERENCES unified_blocks(id),
        position TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        properties TEXT NOT NULL DEFAULT '{{}}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        {_HOT_COLUMNS_SQL}
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ub_parent ON unified_blocks(parent_id, position)",
    *BLOCK_HOT_INDEXES,
    # page_changes = the workspace's change log (gamma/blocks_store.py
    # touch_page): one row per page that exists or ever existed, whose `seq`
    # every write to the page moves to the next value in the workspace —
    # `kind` live, or deleted (in Recently deleted, or deleted for good);
    # `at` when, `actor` who (an account id, or the label of a writer that
    # is none — gamma/auth.py actor_of). GET /api/sync/changes lists the
    # rows after a seq, so anything that keeps a copy of the workspace (a
    # mirror, a backup merge) finds what moved and tells "deleted" from
    # "never seen".
    """CREATE TABLE IF NOT EXISTS page_changes (
        page_id TEXT PRIMARY KEY,
        seq INTEGER NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        at TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT ''
    )""",
    # sync_pages / sync_conflicts: a mirror's per-page state
    # (gamma/sync_engine.py) — the remote seq the page was last reconciled
    # at and the tree as of then (the base of the three-way merge), and the
    # merges it had to decide on its own. Empty in a workspace that mirrors
    # nothing.
    """CREATE TABLE IF NOT EXISTS sync_pages (
        page_id TEXT PRIMARY KEY,
        remote_seq INTEGER NOT NULL DEFAULT 0,
        base TEXT NOT NULL DEFAULT '{}',
        synced_at TEXT NOT NULL
    )""",
    # sync_log = what the last rounds did, page by page (the header pill's
    # log), each row with its git-style block counts (stats: JSON
    # {add, del, mod}); pruned to the newest SYNC_LOG_KEEP rows.
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
    # upload_orphans = stored files nothing references any more
    # (gamma/upload_gc.py): the file name and since when. The file stays on
    # disk and is served as before; a reference that comes back clears the
    # row, and a file still unreferenced 30 days on is purged.
    """CREATE TABLE IF NOT EXISTS upload_orphans (
        name TEXT PRIMARY KEY,
        since TEXT NOT NULL
    )""",
    # page_ops = the per-page operation log (gamma/ops.py): one row per
    # applied batch, `seq` counting up per page, `actor` who wrote it (an
    # account id, or a label — gamma/auth.py actor_of). Live clients follow
    # it over the page's websocket; a reconnecting client catches up with
    # GET /api/pages/{id}/ops?since=. Pruned to the newest rows per page.
    """CREATE TABLE IF NOT EXISTS page_ops (
        page_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        client TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        ops TEXT NOT NULL,
        PRIMARY KEY (page_id, seq)
    ) WITHOUT ROWID""",
    *CHATS_SCHEMA,
    *BLOCK_FTS_SCHEMA,
]

# data.db = what the workspace can lose and rebuild: the file may be
# deleted, and everything in it comes back on demand. The PDF text index
# (pdf_fts, gamma/pdf_index.py — extracted again by the next search) and
# the viewer's PDF manifests (pdf_docs, gamma/pdf_meta.py — computed again
# when a PDF opens), both created lazily by their modules, and the cover
# thumbnails below (captured again as the PDF is read). Nothing a person
# wrote lives here: the chats and the notes index are in pages.db.
DATA_SCHEMA = [
    # page_snaps = the recents-card cover thumbnails (small JPEG data URLs
    # captured client-side from the rendered viewer), shared by the
    # workspace's members. Too big for the prefs KV, hence their own table
    # + /api/page-snaps.
    "CREATE TABLE IF NOT EXISTS page_snaps (page_id TEXT PRIMARY KEY, img TEXT NOT NULL, at TEXT NOT NULL)",
]

# Snapshots exist only to cover the (24-entry) recents queue; keep a few
# spares so multi-device merge timing never evicts a still-live cover.
PAGE_SNAPS_CAP = 30


# --- users.db ----------------------------------------------------------------

def users_db_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def new_account_id() -> str:
    """A new account's ``users.id``: a random token like a workspace id."""
    return secrets.token_urlsafe(9)


def account_id(conn: sqlite3.Connection, username: str) -> str:
    """The id of the account named ``username``; "" when no account is."""
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return row[0] if row else ""


def delete_shares(conn: sqlite3.Connection, where: str, params) -> int:
    """Delete the shares ``WHERE where`` together with their invitations
    (``share_users``, keyed by token): the two always go together, or an
    invitation is left pointing at a token that no longer exists. Returns
    how many shares went."""
    conn.execute(f"DELETE FROM share_users WHERE token IN (SELECT token FROM shares WHERE {where})", params)
    return conn.execute(f"DELETE FROM shares WHERE {where}", params).rowcount


def account_name(conn: sqlite3.Connection, user_id: str) -> str:
    """The username of the account ``user_id``; "" when there is none (a
    deleted account, an actor that is no account)."""
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row[0] if row else ""


def account_names(conn: sqlite3.Connection, user_ids) -> dict[str, str]:
    """``{id: username}`` for the accounts among ``user_ids``, in one query:
    ``account_name`` for a list that shows people."""
    wanted = sorted({u for u in user_ids if u})
    if not wanted:
        return {}
    marks = ",".join("?" * len(wanted))
    return dict(conn.execute(f"SELECT id, username FROM users WHERE id IN ({marks})", wanted).fetchall())


class Connection(sqlite3.Connection):
    """What the ``connect_*`` helpers return: a ``with`` block commits (or
    rolls back on an exception) and then CLOSES the connection. sqlite3's
    own Connection only commits and leaves the closing to the garbage
    collector, so a connection a traceback holds keeps its file open (on
    Windows a workspace directory cannot be deleted under it). Never use a
    connection after its ``with`` block; one opened without ``with`` is
    closed by its owner. ``ws`` names the workspace whose database it is
    ("" for users.db), for writers that reach the workspace's files through
    the connection (the ink merge in gamma/ops.py)."""

    ws = ""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def _wal(conn: sqlite3.Connection) -> None:
    """WAL journal mode, as every Gamma database uses (readers never wait on
    a writer, nor a writer on readers), with the sync level WAL makes safe.
    Backups copy the files with the sqlite backup API, which is WAL-safe.
    The mode is stored in the file, so usually this only reads it. A file
    still in rollback mode (created before WAL, or copied in) is switched,
    but the switch needs the file to itself: while another connection has
    it open, SQLite answers "database is locked" at once, without waiting.
    So the switch is tried without waiting, and a connection that cannot
    switch works in the file's current mode. The startup pass and
    ``seed.create_workspace_files`` switch every file while nobody else has
    it open."""
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if mode != "wal":
        conn.execute("PRAGMA busy_timeout = 0")
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        except sqlite3.OperationalError as e:
            if "locked" not in str(e):
                raise
        finally:
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_S * 1000}")
    if mode == "wal":
        conn.execute("PRAGMA synchronous=NORMAL")


def connect_users_db() -> Connection:
    """Open the global users.db, creating it at SCHEMA_VERSION when it does
    not exist yet. An existing file behind SCHEMA_VERSION raises
    ``SchemaOutdated`` — the migration runner (gamma/migrations.py) is the
    only code that touches an old-shape users.db, so nothing can ever read
    or write it with the wrong assumptions. WAL mode and the busy timeout,
    like the workspace databases; a ``with`` block closes it
    (``Connection``)."""
    USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(USERS_DB), timeout=BUSY_TIMEOUT_S, factory=Connection)
    try:
        has_users = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        version = users_db_version(conn)
        if has_users and version < SCHEMA_VERSION:
            raise SchemaOutdated(
                f"the data directory is at schema version {version}, this Gamma expects "
                f"{SCHEMA_VERSION} — run `python manage.py migrate` (the server does so at startup)")
        _wal(conn)
        for stmt in USERS_SCHEMA:
            conn.execute(stmt)
        if not has_users:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


# Prefs that follow the account regardless of workspace (stored with
# workspace_id ''): the AI provider entries, the active entry, the
# translation engine keys (gamma/translate_engines.py), and the preference
# profile. Everything else is per account + workspace, because the
# value names that workspace's pages (open tabs, recents, reading
# positions).
PROFILE_PREF_KEY = "profile"
# gamma/cloud_sync.py: the profile as this server and Gamma Cloud last agreed
# on it, the base of the next three-way merge. Never served by /api/prefs.
PROFILE_BASE_PREF_KEY = "profile-base"
NOTICES_SEEN_PREF_KEY = "notices-seen"  # gamma/notices.py: {notice id: fingerprint seen}
USER_PREF_KEYS = frozenset({"ai-settings", "ai-provider", "translate-engines", "search-services",
                            PROFILE_PREF_KEY, PROFILE_BASE_PREF_KEY, NOTICES_SEEN_PREF_KEY})


def pref_scope(key: str, ws: str) -> str:
    return "" if key in USER_PREF_KEYS else (ws or "")


# (user_id, workspace_id, key, value, updated_at) in; last write wins.
_UPSERT_PREF = ("INSERT INTO user_prefs (user_id, workspace_id, key, value, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, workspace_id, key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at")


# The pref helpers below take the account's id (``users.id``).

def get_pref(user_id: str, key: str, ws: str = ""):
    """(value, updated_at) from the account's prefs, or (None, "") when unset."""
    with connect_users_db() as db:
        row = db.execute(
            "SELECT value, updated_at FROM user_prefs WHERE user_id = ? AND workspace_id = ? AND key = ?",
            (user_id, pref_scope(key, ws), key)).fetchone()
    if not row:
        return None, ""
    try:
        return json.loads(row[0]), row[1]
    except ValueError:
        return None, ""


def set_pref(user_id: str, key: str, value, ws: str = "", *, updated_at: str | None = None) -> str:
    """Store a pref (last write wins); returns the updated_at in effect.

    Without ``updated_at`` this is a change made here, stamped now. The
    profile's stamp never goes back: one at or before the stored stamp
    becomes that plus a millisecond, so an edit right after a pull from a
    clock that runs ahead still counts as newer. A profile change of an
    account linked to Gamma Cloud is then pushed there (gamma/cloud_sync.py).
    With ``updated_at`` it is a copy synced from elsewhere: written only
    when newer than the stored one, and never pushed back."""
    scope = pref_scope(key, ws)
    with connect_users_db() as db:
        if updated_at is None:
            stamp = page_now()
            if key == PROFILE_PREF_KEY:
                db.execute("BEGIN IMMEDIATE")  # the stamp read and the write as one step
                row = db.execute("SELECT updated_at FROM user_prefs WHERE user_id = ? AND workspace_id = ? AND key = ?",
                                 (user_id, scope, key)).fetchone()
                stamp = stamp_after(row[0] if row else "")
            db.execute(_UPSERT_PREF, (user_id, scope, key, json.dumps(value), stamp))
        else:
            db.execute(_UPSERT_PREF + " WHERE excluded.updated_at > user_prefs.updated_at",
                       (user_id, scope, key, json.dumps(value), updated_at))
            stamp = db.execute("SELECT updated_at FROM user_prefs WHERE user_id = ? AND workspace_id = ? AND key = ?",
                               (user_id, scope, key)).fetchone()[0]
        db.commit()
    if updated_at is None and key == PROFILE_PREF_KEY:
        from . import cloud_sync  # local: cloud_sync imports this module
        cloud_sync.profile_changed(user_id)
    return stamp


def update_pref(user_id: str, key: str, change, ws: str = ""):
    """Read-modify-write one pref in one write transaction, so two edits of
    one value (a provider added in one tab while another tab's sign-in
    stores its tokens) never undo each other, as a get_pref + set_pref pair
    can. ``change`` gets the stored value (None when unset or unreadable)
    and returns the value to store; an exception from it aborts with nothing
    written. An unchanged value is not written. Returns the value in
    effect. Not for the profile: its stamp and cloud push are set_pref's."""
    scope = pref_scope(key, ws)
    with connect_users_db() as db:
        db.execute("BEGIN IMMEDIATE")  # the write lock before the read
        row = db.execute("SELECT value FROM user_prefs WHERE user_id = ? AND workspace_id = ? AND key = ?",
                         (user_id, scope, key)).fetchone()
        try:
            value = json.loads(row[0]) if row else None
        except ValueError:
            value = None
        new = change(value)  # may edit ``value`` in place: compare as stored
        text = json.dumps(new)
        if not row or text != row[0]:
            db.execute(_UPSERT_PREF, (user_id, scope, key, text, page_now()))
        db.commit()
    return new


def restamp_pref(user_id: str, key: str, old: str, new: str, ws: str = "") -> bool:
    """Move a pref's version from ``old`` to ``new`` without touching its
    value, only while it is still at ``old`` (a change made meanwhile wins).
    The cloud sync does this when the account server stored a pushed value
    under another time (its own clock, millisecond precision)."""
    with connect_users_db() as db:
        cur = db.execute("UPDATE user_prefs SET updated_at = ? WHERE user_id = ? AND workspace_id = ? AND key = ? "
                         "AND updated_at = ?", (new, user_id, pref_scope(key, ws), key, old))
        db.commit()
    return bool(cur.rowcount)


def stamp_after(stored: str) -> str:
    """A version stamp that never goes back: ``page_now()``, or ``stored``
    plus one millisecond when the clock has not passed it (a pull from a
    clock that runs ahead, two writes in one tick). The profile's stamp and
    a chat's version (routers/chats.py)."""
    stamp = page_now()
    if not stored or stored < stamp:
        return stamp
    t = parse_stamp(stored)
    if t is None:
        return stamp
    return format_stamp((t + timedelta(milliseconds=1)).astimezone(timezone.utc))


# The preference profile: every account-scoped setting of the web app in one
# JSON object keyed by preference name (frontend/src/app/prefDefs.js declares
# which). Opaque to the server; it never holds secrets — the AI provider
# entries keep their own key.

def get_profile(user_id: str) -> tuple[dict, str]:
    """(profile, updated_at); ({}, "") when the account has none yet."""
    value, updated_at = get_pref(user_id, PROFILE_PREF_KEY)
    return (value if isinstance(value, dict) else {}), updated_at


def set_profile(user_id: str, value: dict, *, updated_at: str | None = None) -> str:
    """Replace the account's profile (last write wins); returns updated_at.
    ``updated_at`` marks a copy synced from Gamma Cloud (see set_pref)."""
    if not isinstance(value, dict):
        raise ValueError("a profile is a JSON object")
    return set_pref(user_id, PROFILE_PREF_KEY, value, updated_at=updated_at)


def replace_profile_if(user_id: str, value: dict, old: str, new: str) -> bool:
    """Store ``value`` under ``new`` only while the profile is still at
    ``old`` ("" = none stored yet): the cloud sync's write, which loses to a
    change made meanwhile. Never pushes back."""
    with connect_users_db() as db:
        if old:
            cur = db.execute("UPDATE user_prefs SET value = ?, updated_at = ? WHERE user_id = ? AND workspace_id = '' "
                             "AND key = ? AND updated_at = ?", (json.dumps(value), new, user_id, PROFILE_PREF_KEY, old))
        else:
            cur = db.execute("INSERT OR IGNORE INTO user_prefs (user_id, workspace_id, key, value, updated_at) "
                             "VALUES (?, '', ?, ?, ?)", (user_id, PROFILE_PREF_KEY, json.dumps(value), new))
        db.commit()
    return bool(cur.rowcount)


def patch_profile(user_id: str, changes: dict) -> tuple[dict, str]:
    """Set the preferences in ``changes`` and keep every other entry as
    stored: how a browser saves, so its stale copy of a preference it did not
    touch never undoes one synced from elsewhere. A change made here (pushed
    like set_profile's). Returns (profile, updated_at)."""
    for _ in range(5):  # another write landed between the read and this one: read again
        value, at = get_profile(user_id)
        merged = {**value, **changes}
        stamp = stamp_after(at)
        if replace_profile_if(user_id, merged, at, stamp):
            break
    else:  # an unreadable stored row: replace it
        return merged, set_profile(user_id, merged)
    from . import cloud_sync  # local: cloud_sync imports this module
    cloud_sync.profile_changed(user_id)
    return merged, stamp


# --- workspace files ---------------------------------------------------------

def ws_dir(ws: str) -> Path:
    return WORKSPACES_DIR / safe_ws_id(ws)


def workspace_ids() -> list[str]:
    """The workspaces on disk, sorted: every directory under workspaces/
    whether or not users.db still names it (an orphaned directory, one being
    deleted). A name ``safe_ws_id`` refuses is no workspace and is left
    out."""
    if not WORKSPACES_DIR.is_dir():
        return []
    return sorted(d.name for d in WORKSPACES_DIR.iterdir() if d.is_dir() and _WS_ID_RE.match(d.name))


def ws_db_path(ws: str, db_name: str) -> str:
    return str(ws_dir(ws) / db_name)


def ws_uploads_dir(ws: str) -> Path:
    return ws_dir(ws) / "uploads"


def pdf_upload_path(ws: str, doc_id: str) -> Path:
    """Validated path to a document's stored PDF. Use instead of joining an
    untrusted doc id into a filename by hand."""
    return ws_uploads_dir(ws) / f"{safe_doc_id(doc_id)}.pdf"


def register_functions(conn: sqlite3.Connection) -> None:
    """The SQL functions a pages.db's schema calls: ``textnorm(text)``, the
    search form of a text (gamma/textnorm.py ``normalize_text``), in the
    notes index's view and triggers (BLOCK_FTS_SCHEMA). A connection that
    writes a pages.db's blocks or reads its notes index needs them:
    ``connect_pages_db`` registers them, and so does every raw
    ``sqlite3.connect`` that does either (a backup copy, a migration step,
    an export). Without them such a write fails ("no such function:
    textnorm") instead of leaving the index behind."""
    conn.create_function("textnorm", 1, normalize_text, deterministic=True)


def _open_ws_db(ws: str, db_name: str, schema) -> Connection:
    conn = sqlite3.connect(ws_db_path(ws, db_name), timeout=BUSY_TIMEOUT_S, factory=Connection)
    conn.ws = ws
    try:
        _wal(conn)
        for stmt in schema:
            conn.execute(stmt)
    except BaseException:
        conn.close()  # a locked or damaged file: never leave the handle open
        raise
    return conn


def connect_pages_db(ws: str) -> Connection:
    """THE way to open a workspace's pages.db. WAL mode (``_wal``), a busy
    timeout instead of an instant "database is locked", the SQL functions
    its schema calls (``register_functions``) and the schema statements
    (cheap no-ops once applied). A ``with`` block closes it
    (``Connection``)."""
    conn = _open_ws_db(ws, "pages.db", PAGES_SCHEMA)
    register_functions(conn)
    return conn


def connect_data_db(ws: str) -> Connection:
    """THE way to open a workspace's data.db: WAL mode, the busy timeout and
    the schema statements, like pages.db. The PDF index and manifests add
    their own tables (gamma/pdf_index.py, gamma/pdf_meta.py)."""
    return _open_ws_db(ws, "data.db", DATA_SCHEMA)


def get_page_snaps(ws: str, after: str = "") -> dict:
    """{page_id: {img, at}} — optionally only entries newer than `after`."""
    with connect_data_db(ws) as db:
        rows = db.execute(
            "SELECT page_id, img, at FROM page_snaps WHERE at > ? ORDER BY at DESC",
            (after or "",),
        ).fetchall()
    return {pid: {"img": img, "at": at} for pid, img, at in rows}


def set_page_snap(ws: str, page_id: str, img: str, at: str = "") -> str:
    """Store a snapshot (newest `at` wins) and prune past the cap; returns the stored at."""
    at = at or page_now()
    with connect_data_db(ws) as db:
        db.execute("BEGIN IMMEDIATE")  # compare and write as one step: the newest capture wins
        row = db.execute("SELECT at FROM page_snaps WHERE page_id = ?", (page_id,)).fetchone()
        if row and row[0] >= at:
            return row[0]  # a newer capture (another device) already landed
        db.execute(
            "INSERT INTO page_snaps (page_id, img, at) VALUES (?, ?, ?) "
            "ON CONFLICT(page_id) DO UPDATE SET img = excluded.img, at = excluded.at",
            (page_id, img, at),
        )
        db.execute(
            "DELETE FROM page_snaps WHERE page_id NOT IN "
            "(SELECT page_id FROM page_snaps ORDER BY at DESC LIMIT ?)",
            (PAGE_SNAPS_CAP,),
        )
        db.commit()
    return at


def delete_page_snap(ws: str, page_id: str):
    with connect_data_db(ws) as db:
        db.execute("DELETE FROM page_snaps WHERE page_id = ?", (page_id,))
        db.commit()
