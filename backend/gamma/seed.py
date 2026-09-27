"""Workspace file creation and seeding: the empty pages.db / data.db /
uploads/ of a new workspace, the guest welcome page, the first admin.

Shared by the app (guest logins, first run) and manage.py (user CRUD)
so the welcome page and schemas never drift between the two. Account and
membership rows are gamma/workspaces.py's job; this module only writes
files.
"""

import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt

from fractional_indexing import generate_n_keys_between

from .config import WORKSPACES_DIR
from .db import DATA_SCHEMA, PAGES_SCHEMA, connect_users_db, page_now, safe_ws_id
from .logbuf import log
from .server_settings import guest_ttl_hours

# The seeded Welcome page: a normal markdown outline, imported through the
# .md parser (docs/dev/onboarding.md "The welcome page and its sample PDF").
WELCOME_MD = Path(__file__).resolve().parent / "onboarding" / "welcome.md"


def _guest_lifetime() -> str:
    """"24 hours" — how long a guest account lives (server setting)."""
    hours = guest_ttl_hours()
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _guest_note() -> str:
    """The callout a guest's Welcome page ends with: when the workspace goes."""
    return ("> [!note] Guest workspace\n"
            f"> It stays for {_guest_lifetime()} or until you log out, then it is deleted "
            "with everything in it. To keep your work, ask the admin for an account.")


def welcome_tree() -> tuple[str, list]:
    """``(title, [{content, children}])`` of the Welcome page, parsed from
    welcome.md like any imported markdown file. ``("", [])`` when the file is
    missing (a broken install seeds nothing rather than failing a login)."""
    from .markdown_import import fm_text, md_to_blocks, parse_frontmatter
    try:
        text = WELCOME_MD.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"[seed] welcome page skipped: {e}")
        return "", []
    fields, body = parse_frontmatter(text)
    return fm_text(fields, "title") or "Welcome", md_to_blocks(body)


def _welcome_blocks():
    """The guest Welcome page as ``(id, parent, position, content, props)``
    rows, parents before children."""
    title, tree = welcome_tree()
    if not title:
        return []
    tree = [*tree, {"content": _guest_note(), "children": []}]
    wid = secrets.token_urlsafe(9)
    rows = [(wid, "root", "a0V", title, "{}")]
    pending = [(wid, tree)]
    while pending:
        parent, nodes = pending.pop(0)
        for node, pos in zip(nodes, generate_n_keys_between(None, None, n=len(nodes))):
            bid = secrets.token_urlsafe(9)
            rows.append((bid, parent, pos, node["content"], "{}"))
            if node.get("children"):
                pending.append((bid, node["children"]))
    return rows


def create_workspace_files(ws_id: str, welcome: bool = False):
    """Create fresh pages.db, data.db and uploads/ under workspaces/<id>/
    (existing files are kept). ``welcome`` seeds the guest welcome page."""
    target = WORKSPACES_DIR / safe_ws_id(ws_id)
    target.mkdir(parents=True, exist_ok=True)
    nw = page_now()

    # closing(), not just the context manager: sqlite3's `with` commits but
    # does NOT close, and the open handle would block renaming/deleting the
    # directory on Windows.
    with closing(sqlite3.connect(str(target / "pages.db"))) as pages_db:
        # WAL from the start: connect_pages_db would switch it on first
        # open, which needs the file to itself — two first openers race.
        pages_db.execute("PRAGMA journal_mode=WAL")
        for stmt in PAGES_SCHEMA:
            pages_db.execute(stmt)
        if not pages_db.execute("SELECT 1 FROM unified_blocks WHERE id = 'root'").fetchone():
            pages_db.execute(
                "INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at) "
                "VALUES ('root', NULL, 'a0', '', '{}', ?, ?)",
                (nw, nw),
            )
        if welcome and not pages_db.execute(
                "SELECT 1 FROM unified_blocks WHERE parent_id = 'root' LIMIT 1").fetchone():
            for bid, pid, pos, content, props in _welcome_blocks():
                pages_db.execute(
                    "INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (bid, pid, pos, content, props or "{}", nw, nw),
                )
        pages_db.commit()

    with closing(sqlite3.connect(str(target / "data.db"))) as data_db:
        for stmt in DATA_SCHEMA:
            data_db.execute(stmt)
        data_db.commit()

    (target / "uploads").mkdir(parents=True, exist_ok=True)


def ensure_admin_seed():
    """A fresh instance seeds its own first admin at app startup — account
    logic lives here, not in launcher scripts. Returns (username, password)
    when it seeded, else None.

    Runs only while the instance has NO real (non-guest) accounts at all.
    The password is RANDOM and printed to the console exactly once (a fixed
    default would be guessable on a LAN-exposed server); GAMMA_ADMIN_USER /
    GAMMA_ADMIN_PASSWORD can override the one-time seed. As soon as any
    account exists this is a strict no-op — deliberately NOT keyed on "no
    admin exists", because silently adding an admin login to an upgraded
    multi-user instance would be a backdoor; those grant the privilege via
    `manage.py set-admin`."""
    from . import workspaces

    username = os.environ.get("GAMMA_ADMIN_USER", "").strip() or "admin"
    env_password = os.environ.get("GAMMA_ADMIN_PASSWORD", "")
    password = env_password or secrets.token_urlsafe(9)  # 12 chars, URL-safe alphabet
    with connect_users_db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE is_guest = 0").fetchone():
            if not conn.execute("SELECT 1 FROM users WHERE is_admin = 1 AND is_guest = 0").fetchone():
                log.info("[startup] no account has the admin privilege - grant one with: "
                         "python manage.py set-admin <user> on")
            return None
        pwhash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO users (username, password_hash, is_guest, is_admin, created_at) VALUES (?, ?, 0, 1, ?)",
            (username, pwhash, page_now()),
        )
        conn.commit()
    workspaces.ensure_personal(username)
    # ASCII only: this prints during startup, and a redirected Windows console
    # (GBK) raises UnicodeEncodeError on characters it can't encode.
    # Raw print()s on purpose — the one-time password must go to the console
    # ONLY, never through the log buffer that admins can read later.
    print(f"[startup] fresh instance - created the admin account:")
    print(f"[startup]   username: {username}")
    print(f"[startup]   password: {'(from GAMMA_ADMIN_PASSWORD)' if env_password else password}")
    if not env_password:
        print("[startup]   shown only this once - log in and change it in account menu -> Manage users")
    return username, password


def create_cloud_account(username: str, is_admin: bool = False) -> str:
    """An account only its cloud identity can sign in as: a real (non-guest)
    row with an EMPTY password hash — the password login refuses those —
    plus its personal workspace (gamma/cloud_auth.py ``provision``)."""
    from . import workspaces

    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_guest, is_admin, created_at) VALUES (?, '', 0, ?, ?)",
            (username, 1 if is_admin else 0, page_now()),
        )
        conn.commit()
    return workspaces.ensure_personal(username)


def create_account(username: str, password: str | None, is_admin: bool = False) -> str:
    """Insert an account row and its personal workspace. Returns the
    workspace id. Shared by manage.py and the admin API. Without a password
    the account has an empty hash: the password login refuses it until
    ``manage.py set-password`` gives it one (never a guest — guest accounts
    are gamma/guests.py's and expire)."""
    from . import workspaces

    pwhash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else ""
    with connect_users_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_guest, is_admin, created_at) VALUES (?, ?, 0, ?, ?)",
            (username, pwhash, 1 if is_admin else 0, page_now()),
        )
        conn.commit()
    return workspaces.ensure_personal(username)
