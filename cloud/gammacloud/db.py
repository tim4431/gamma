"""The one database, ``cloud.db``: schema, connections, timestamps, the
versioned upgrade.

``SCHEMA`` is always the CURRENT shape and is applied with ``CREATE TABLE IF
NOT EXISTS`` on a fresh file. An existing file is brought up to
``SCHEMA_VERSION`` (its ``PRAGMA user_version``) by the numbered steps in
``STEPS``, run by ``ensure_current()`` before the server serves anything —
the same rules as Gamma's data directory (docs/dev/migrations.md): a file
ahead of the code is refused, each step is re-runnable, the version is
stamped after each step. A backup copy of the file is taken before the first
pending step.
"""

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

from . import config

SCHEMA_VERSION = 9
BUSY_TIMEOUT = 10  # seconds a connection waits for another writer


class NewerDataError(RuntimeError):
    """cloud.db was written by a newer build."""


def after(seconds: float) -> str:
    """UTC, fixed width, ``Z`` suffix — so timestamps compare as strings."""
    t = datetime.now(timezone.utc).timestamp() + seconds
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now() -> str:
    return after(0)


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def token_hash(token: str) -> str:
    """Every secret at rest is stored as its SHA-256 (the secrets themselves
    are random and long, so no salt is needed)."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def new_id() -> str:
    """Account and grant ids: 20 url-safe chars, random — an account id is
    the stable OIDC ``sub`` and never changes when the username does."""
    return secrets.token_urlsafe(15)


# One Google/GitHub sign-in in flight (``identities.py``): ``redirect`` while
# the browser is at the provider, ``signup`` once the identity is known but
# no account has it yet (the claims wait here for the username form). Keyed
# by the hash of the browser's ``gc_ext`` cookie.
EXTERNAL_LOGINS = """CREATE TABLE IF NOT EXISTS external_logins (
        token_hash TEXT PRIMARY KEY,
        stage TEXT NOT NULL,
        provider TEXT NOT NULL,
        next TEXT NOT NULL DEFAULT '/',
        request_id TEXT NOT NULL DEFAULT '',
        link_account TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        handle TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )"""

# Every refresh token a live grant has rotated away from: a retry of the
# newest within REFRESH_REUSE_GRACE rotates again, any other reuse revokes
# the grant (``oidc.refresh_grant``).
REFRESH_HISTORY = """CREATE TABLE IF NOT EXISTS refresh_history (
        refresh_hash TEXT PRIMARY KEY,
        grant_id TEXT NOT NULL,
        replaced_at TEXT NOT NULL
    )"""

# The preference profile (``prefs.py``): one JSON value per (account, key);
# ``updated_at`` is the version, per-key last-writer-wins.
PREFS = """CREATE TABLE IF NOT EXISTS prefs (
        account_id TEXT NOT NULL REFERENCES accounts(id),
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_id, key)
    )"""

# The Gamma servers a person linked their identity on (``servers.py``):
# each registers its confirmed public URL (normalized, one row per URL).
# ``grant_id`` is the grant of the token it last registered with, which is
# how the portal shows a server and its sign-in as one row. ``version`` (its
# build label) and ``schema`` (its data directory's schema version) are what
# it last reported: '' and NULL until it does.
SERVERS_LINKED = """CREATE TABLE IF NOT EXISTS servers_linked (
        account_id TEXT NOT NULL REFERENCES accounts(id),
        url TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        linked_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        grant_id TEXT NOT NULL DEFAULT '',
        version TEXT NOT NULL DEFAULT '',
        schema INTEGER,
        PRIMARY KEY (account_id, url)
    )"""

# Server settings an admin edits at runtime (``settings.py``). Only the keys
# in ``settings.DEFAULTS`` mean anything; a row for any other key is ignored,
# so a rolled-back build leaves nothing behind.
SETTINGS = """CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
    )"""

# --- plans, billing and hosted servers (step 9; docs/research/cloud-plans.md) ---
# ``subscriptions``: the account server's copy of the Stripe subscription,
# one per account (``billing.py``). ``status`` is Stripe's word; ``plan`` the
# plan its price buys. ``billing_events`` makes webhook deliveries idempotent.
SUBSCRIPTIONS = """CREATE TABLE IF NOT EXISTS subscriptions (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    stripe_customer_id TEXT NOT NULL DEFAULT '',
    stripe_subscription_id TEXT NOT NULL DEFAULT '',
    price_id TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT 'free',
    status TEXT NOT NULL DEFAULT 'none',
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    current_period_end TEXT,
    seats INTEGER NOT NULL DEFAULT 1,
    past_due_since TEXT,
    ended_at TEXT,
    updated_at TEXT NOT NULL
)"""
BILLING_EVENTS = """CREATE TABLE IF NOT EXISTS billing_events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL
)"""
# ``hosts``: the machines the fleet agents run on (``fleet.py``); the token
# is what an agent authenticates with. ``hosted_servers``: one paid
# container per account (``hosted.py``); ``label`` is its hostname label
# (the username at provisioning), ``limits`` the last answer the container
# got from ``/api/hosted/sync``, ``report`` what it last reported.
# ``fleet_jobs``: the queue a host's agent works through.
HOSTS = """CREATE TABLE IF NOT EXISTS hosts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    address TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL,
    memory_mb INTEGER NOT NULL DEFAULT 0,
    disk_mb INTEGER NOT NULL DEFAULT 0,
    memory_used_mb INTEGER NOT NULL DEFAULT 0,
    disk_used_mb INTEGER NOT NULL DEFAULT 0,
    accepting INTEGER NOT NULL DEFAULT 1,
    agent_version TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT,
    created_at TEXT NOT NULL
)"""
HOSTED_SERVERS = """CREATE TABLE IF NOT EXISTS hosted_servers (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    label TEXT NOT NULL UNIQUE,
    host_id TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL DEFAULT '',
    image_tag TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'provisioning',
    read_only INTEGER NOT NULL DEFAULT 0,
    limits TEXT NOT NULL DEFAULT '{}',
    report TEXT NOT NULL DEFAULT '{}',
    reported_at TEXT,
    synced_at TEXT,
    state_changed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT
)"""
FLEET_JOBS = """CREATE TABLE IF NOT EXISTS fleet_jobs (
    id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    server_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '',
    wave TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
)"""
FLEET_JOBS_INDEX = "CREATE INDEX IF NOT EXISTS fleet_jobs_host ON fleet_jobs(host_id, state)"
SUBSCRIPTIONS_INDEX = "CREATE INDEX IF NOT EXISTS subscriptions_customer ON subscriptions(stripe_customer_id)"

# A server connection a person approved (``connect.py``), waiting for the
# server to fetch its client with the code (expires quickly).
SERVER_CONNECTS ="""CREATE TABLE IF NOT EXISTS server_connects (
        code_hash TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        server TEXT NOT NULL,
        code_challenge TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )"""

SCHEMA = [
    # ``email`` is the address as it was typed; ``email_canon`` is the inbox
    # it reaches (accounts.py ``email_canon``), what uniqueness is judged on.
    # Its index is deliberately not UNIQUE: two accounts predating the rule
    # may share a canonical form, and the constraint on ``email`` is enough
    # of a backstop.
    """CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL UNIQUE,
        email_canon TEXT NOT NULL DEFAULT '',
        email_verified_at TEXT,
        password_hash TEXT,
        display_name TEXT NOT NULL DEFAULT '',
        plan TEXT NOT NULL DEFAULT 'free',
        granted_plan TEXT NOT NULL DEFAULT 'free',
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        app_signed_in_at TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS accounts_email_canon ON accounts(email_canon)""",
    # An account's sign-in through an outside provider (google, github):
    # ``identities.py``. ``email`` is the provider's address at the last
    # sign-in, shown on the Settings page.
    """CREATE TABLE IF NOT EXISTS identities (
        provider TEXT NOT NULL,
        subject TEXT NOT NULL,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        email TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (provider, subject)
    )""",
    EXTERNAL_LOGINS,
    """CREATE TABLE IF NOT EXISTS portal_sessions (
        token_hash TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE INDEX IF NOT EXISTS portal_sessions_account ON portal_sessions(account_id)""",
    # verify / reset / change-email links; ``payload`` is the new address
    # for change-email.
    """CREATE TABLE IF NOT EXISTS email_tokens (
        token_hash TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        payload TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS invites (
        code TEXT PRIMARY KEY,
        uses_left INTEGER NOT NULL,
        plan TEXT NOT NULL DEFAULT 'free',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    )""",
    # OIDC clients: ``secret_hash`` NULL = public client (PKCE only).
    # ``kind``: desktop / share-host / container / server. ``redirect_uris``
    # is a JSON list; the desktop client's is empty because loopback is
    # matched by rule. ``owner_account_id``: the person who connected a
    # ``server`` client (``connect.py``); "" for the ones admins create.
    """CREATE TABLE IF NOT EXISTS oauth_clients (
        client_id TEXT PRIMARY KEY,
        secret_hash TEXT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        redirect_uris TEXT NOT NULL DEFAULT '[]',
        server_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        owner_account_id TEXT NOT NULL DEFAULT ''
    )""",
    # A sign-in in progress on the authorize page (expires quickly).
    """CREATE TABLE IF NOT EXISTS oauth_requests (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        scope TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT '',
        nonce TEXT NOT NULL DEFAULT '',
        code_challenge TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS oauth_codes (
        code_hash TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        scope TEXT NOT NULL,
        nonce TEXT NOT NULL DEFAULT '',
        code_challenge TEXT NOT NULL,
        auth_time TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        grant_id TEXT NOT NULL DEFAULT '',
        access_hash TEXT NOT NULL DEFAULT ''
    )""",
    # One grant per (account, client, device): the refresh token, rotated on
    # every use. Revoking it kills its access tokens too. ``device_id`` is a
    # client's stable per-install id: a new sign-in with the same one
    # replaces the old grant.
    """CREATE TABLE IF NOT EXISTS grants (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        client_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        refresh_hash TEXT UNIQUE,
        created_at TEXT NOT NULL,
        rotated_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        device_id TEXT NOT NULL DEFAULT '',
        device_name TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE INDEX IF NOT EXISTS grants_account ON grants(account_id)""",
    REFRESH_HISTORY,
    """CREATE INDEX IF NOT EXISTS refresh_history_grant ON refresh_history(grant_id)""",
    """CREATE TABLE IF NOT EXISTS access_tokens (
        token_hash TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        client_id TEXT NOT NULL,
        grant_id TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    # Ed25519 signing keys for ID tokens; the newest unretired one signs,
    # retired ones stay in the JWKS for RETIRED_KEY_GRACE.
    """CREATE TABLE IF NOT EXISTS signing_keys (
        kid TEXT PRIMARY KEY,
        private_pem TEXT NOT NULL,
        created_at TEXT NOT NULL,
        retired_at TEXT
    )""",
    SUBSCRIPTIONS, SUBSCRIPTIONS_INDEX, BILLING_EVENTS, HOSTS, HOSTED_SERVERS, FLEET_JOBS, FLEET_JOBS_INDEX,
    """CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        account_id TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT '',
        event TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    )""",
    PREFS,
    SERVERS_LINKED,
    SERVER_CONNECTS,
    SETTINGS,
]


def connect() -> sqlite3.Connection:
    """A connection to cloud.db (WAL, ``BUSY_TIMEOUT``). Use as
    ``with closing(connect()) as conn``. The schema is applied on every
    connect, which is what makes a fresh file complete; an old file must
    have been upgraded by ``ensure_current()`` first."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    fresh = conn.execute("PRAGMA user_version").fetchone()[0] == 0
    for stmt in SCHEMA:
        conn.execute(stmt)
    if fresh:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def begin_write(conn) -> None:
    """Take the write lock before reading what a write depends on, so no
    other request can change it in between (a refresh racing a revoke).
    A no-op inside a transaction that already wrote."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def is_busy(e: Exception) -> bool:
    """A write lock another request held past ``BUSY_TIMEOUT``."""
    return isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e))


def audit(conn, event: str, account_id: str = "", actor: str = "", detail: str = "") -> None:
    conn.execute("INSERT INTO audit (at, account_id, actor, event, detail) VALUES (?, ?, ?, ?, ?)",
                 (now(), account_id, actor, event, detail[:500]))


# --- upgrades -----------------------------------------------------------------

def _step_external_logins(conn) -> None:
    conn.execute(EXTERNAL_LOGINS)


def _add_column(conn, table: str, column: str, decl: str) -> None:
    if column not in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _step_devices(conn) -> None:
    """Per-device grants, exact code replay, the refresh history, and when
    an account first signed in to a Gamma app."""
    _add_column(conn, "grants", "device_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "grants", "device_name", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "oauth_codes", "grant_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "oauth_codes", "access_hash", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "accounts", "app_signed_in_at", "TEXT")
    conn.execute(REFRESH_HISTORY)
    conn.execute("CREATE INDEX IF NOT EXISTS refresh_history_grant ON refresh_history(grant_id)")
    conn.execute("UPDATE accounts SET app_signed_in_at = (SELECT MIN(at) FROM audit WHERE audit.account_id = accounts.id "
                 "AND audit.event = 'oidc.authorize') WHERE app_signed_in_at IS NULL")


def _step_profile(conn) -> None:
    """The preference profile and the linked-server list."""
    conn.execute(PREFS)
    conn.execute(SERVERS_LINKED)


def _step_connect(conn) -> None:
    """Servers a person connects themselves: the client's owner, the
    pending connections, and the grant a linked server registered with."""
    _add_column(conn, "oauth_clients", "owner_account_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "servers_linked", "grant_id", "TEXT NOT NULL DEFAULT ''")
    conn.execute(SERVER_CONNECTS)


def _step_email_canon(conn) -> None:
    """The inbox an address reaches, so aliases of one mailbox cannot become
    separate accounts. Backfilled here; a row whose canonical form already
    belongs to an older account keeps it, which only means that address
    cannot be re-registered."""
    from .accounts import email_canon  # local: accounts.py imports this module
    _add_column(conn, "accounts", "email_canon", "TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS accounts_email_canon ON accounts(email_canon)")
    for row in conn.execute("SELECT id, email FROM accounts WHERE email_canon = ''").fetchall():
        conn.execute("UPDATE accounts SET email_canon = ? WHERE id = ?", (email_canon(row["email"]), row["id"]))


def _step_settings(conn) -> None:
    """The settings table, seeded once from the environment variables it
    replaces (``settings.py``). After this those variables are not read; a
    deployment that still sets one is warned about at startup. A fresh
    cloud.db never runs this and takes the defaults, so a first install is
    configured on the Admin page and nowhere else."""
    conn.execute(SETTINGS)
    imported = {
        "registration": os.environ.get("GAMMA_CLOUD_REGISTRATION", "").strip().lower(),
        "turnstile_sitekey": os.environ.get("GAMMA_CLOUD_TURNSTILE_SITEKEY", "").strip(),
        "turnstile_secret": os.environ.get("GAMMA_CLOUD_TURNSTILE_SECRET", "").strip(),
        "blocked_email_domains": "\n".join(
            d.strip().lower().lstrip("@") for d in os.environ.get("GAMMA_CLOUD_BLOCKED_EMAIL_DOMAINS", "").split(",")
            if d.strip()),
    }
    for key, value in imported.items():
        if value:
            conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT (key) DO NOTHING", (key, value, now()))


def _step_server_build(conn) -> None:
    """The build and data schema version a linked server reports."""
    _add_column(conn, "servers_linked", "version", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "servers_linked", "schema", "INTEGER")


def _step_plans(conn) -> None:
    """Plans that are paid for: the subscription copy, the webhook event
    log, the hosts, the hosted servers and the fleet's job queue. The plan
    an admin or an invite gave moves to ``granted_plan`` (backfilled from
    ``plan``); ``plan`` stays the effective one every claim reads."""
    _add_column(conn, "accounts", "granted_plan", "TEXT NOT NULL DEFAULT 'free'")
    conn.execute("UPDATE accounts SET granted_plan = plan WHERE granted_plan = 'free' AND plan != 'free'")
    for stmt in (SUBSCRIPTIONS, SUBSCRIPTIONS_INDEX, BILLING_EVENTS, HOSTS, HOSTED_SERVERS, FLEET_JOBS, FLEET_JOBS_INDEX):
        conn.execute(stmt)


STEPS: list = [
    # (version, name, fn(conn)) — append only; see docs/dev/cloud_accounts.md.
    (2, "external_logins", _step_external_logins),
    (3, "devices", _step_devices),
    (4, "profile", _step_profile),
    (5, "connect", _step_connect),
    (6, "email_canon", _step_email_canon),
    (7, "settings", _step_settings),
    (8, "server_build", _step_server_build),
    (9, "plans", _step_plans),
]


def data_version() -> int | None:
    if not config.DB_PATH.exists():
        return None
    with closing(sqlite3.connect(str(config.DB_PATH))) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'accounts'").fetchone():
            return None
        return conn.execute("PRAGMA user_version").fetchone()[0]


def ensure_current() -> list[str]:
    """Upgrade cloud.db to SCHEMA_VERSION; returns the names of the steps
    run. Refuses a newer file."""
    version = data_version()
    if version is None:
        return []
    if version > SCHEMA_VERSION:
        raise NewerDataError(f"cloud.db is at schema version {version}; this build understands {SCHEMA_VERSION}")
    pending = [(v, name, fn) for v, name, fn in STEPS if v > version]
    if not pending:
        return []
    snapshot(f"v{version}")
    for older in sorted((config.DATA_DIR / "backups").glob("*-v*.db"))[:-3]:
        older.unlink()
    done = []
    for v, name, fn in pending:
        with closing(sqlite3.connect(str(config.DB_PATH), timeout=BUSY_TIMEOUT)) as conn:
            conn.row_factory = sqlite3.Row
            fn(conn)
            conn.execute(f"PRAGMA user_version = {v}")
            conn.commit()
        done.append(name)
    return done


def snapshot(label: str = "manual") -> str:
    """A consistent copy of cloud.db under ``backups/`` (the SQLite backup
    API, WAL-safe). Returns the path."""
    backups = config.DATA_DIR / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    path = backups / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}.db"
    with closing(sqlite3.connect(str(config.DB_PATH))) as src, closing(sqlite3.connect(str(path))) as dst:
        src.backup(dst)
    return str(path)

