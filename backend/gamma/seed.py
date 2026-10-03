"""Workspace file creation and seeding: the empty pages.db / data.db /
uploads/ of a new workspace, the Welcome page every new account starts
with, the first admin.

Shared by the app (guest logins, first run, cloud sign-ups, the admin API)
and manage.py (user CRUD) so the welcome page and schemas never drift
between the two. Account and membership rows are gamma/workspaces.py's job;
this module writes files and the Welcome page's blocks.
"""

import functools
import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

import bcrypt

from fractional_indexing import generate_n_keys_between

from .config import WORKSPACES_DIR
from .db import (DATA_SCHEMA, PAGES_SCHEMA, connect_pages_db, connect_users_db, new_account_id, page_now,
                 register_functions, safe_ws_id)
from .logbuf import log
from .server_settings import guest_ttl_hours

# The seeded Welcome page: a normal markdown outline, imported through the
# .md parser, plus a sample PDF the notes-as-PDF writer renders from the same
# text (docs/dev/onboarding.md "The welcome page and its sample PDF").
WELCOME_MD = Path(__file__).resolve().parent / "onboarding" / "welcome.md"
SEEDED_WELCOME = "welcome"  # the page's properties.seeded: tours and the library find it by this
_WELCOME_PDF_NAME = "Welcome to Gamma.pdf"


def _guest_lifetime() -> str:
    """"24 hours" — how long a guest account lives (server setting)."""
    hours = guest_ttl_hours()
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _guest_note() -> str:
    """The callout a guest's Welcome page ends with: when the workspace goes."""
    return ("> [!note] Guest workspace\n"
            f"> It stays for {_guest_lifetime()} or until you log out, then it is deleted "
            "with everything in it. To keep your work, ask the admin for an account.")


def _welcome_text() -> str:
    try:
        return WELCOME_MD.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"[seed] welcome page skipped: {e}")
        return ""


def welcome_source(text: str) -> tuple[dict, list]:
    """``(front matter, [{content, children}])`` of welcome.md's ``text``,
    parsed like any imported markdown file. ``({}, [])`` for no text (a
    broken install seeds nothing rather than failing a login)."""
    from .markdown_import import md_to_blocks, parse_frontmatter
    if not text:
        return {}, []
    fields, body = parse_frontmatter(text)
    return fields, md_to_blocks(body)


def _welcome_titles(fields: dict) -> tuple[str, str]:
    """The page's title and the PDF's: front matter ``title`` and
    ``document`` (which defaults to the title)."""
    from .markdown_import import fm_text
    title = fm_text(fields, "title") or "Welcome"
    return title, fm_text(fields, "document") or title


@functools.lru_cache(maxsize=2)
def welcome_pdf(text: str) -> bytes:
    """The sample PDF: welcome.md (``text``) typeset by the notes-as-PDF
    writer under its ``document`` title. The writer is deterministic, so
    every workspace stores the same file; one render per process and text."""
    from .pdf_document import render_document
    fields, tree = welcome_source(text)
    _title, document = _welcome_titles(fields)
    return render_document([{"content": document, "properties": {}, "children": tree}])


def _insert_ops(page_id: str, tree: list) -> list[dict]:
    """``insert`` ops for a ``{content, children}`` tree under the page,
    parents before children, positions minted per sibling run."""
    ops, pending = [], [(page_id, tree)]
    while pending:
        parent, nodes = pending.pop(0)
        for node, pos in zip(nodes, generate_n_keys_between(None, None, n=len(nodes))):
            bid = secrets.token_urlsafe(9)
            ops.append({"op": "insert", "id": bid, "parent": parent, "position": pos,
                        "content": node.get("content", ""), "props": {}})
            if node.get("children"):
                pending.append((bid, node["children"]))
    return ops


def seed_welcome(ws: str, *, actor: str, guest: bool = False) -> str | None:
    """Seed the Welcome page into a workspace that has no pages yet: a page
    marked ``properties.seeded = "welcome"`` carrying the sample PDF, its
    notes inserted as one op batch like every other block writer's (logged
    under ``actor``, the account's id). A
    guest's page ends with a callout naming the lifetime. Returns the page
    id, or None when skipped (the workspace already has pages, welcome.md is
    missing, or this server is a share host, whose workspaces hold published
    pages only — each counts against the plan's cap). A PDF the writer or the
    storage limits refuse leaves the page without one."""
    from . import cloud_auth
    from .blocks_store import attachment_props, create_page
    from .ops import after_commit, apply_ops
    from .storage import store_file

    if cloud_auth.settings()["share_host"]:
        return None
    text = _welcome_text()
    fields, tree = welcome_source(text)
    if not tree:
        return None
    title, document = _welcome_titles(fields)
    if guest:
        tree = [*tree, {"content": _guest_note(), "children": []}]
    try:
        with connect_pages_db(ws) as conn:
            if conn.execute("SELECT 1 FROM unified_blocks WHERE parent_id = 'root' LIMIT 1").fetchone():
                return None
            props = {"seeded": SEEDED_WELCOME}
            try:
                # The content-hash store, like any upload — but not
                # store_pdf, whose background manifest walk could still hold
                # data.db open when a guest who just arrived logs out (which
                # deletes the workspace); /api/pdf-info makes it on first open.
                filename, _existed = store_file(ws, welcome_pdf(text), ".pdf")
                doc_id = filename[:-len(".pdf")]
                attachment, _auto = attachment_props(doc_id, original_filename=_WELCOME_PDF_NAME)
                # A record of its own, so opening the page looks nothing up
                # (the tour works offline) and asks no AI for a citation.
                props.update(attachment, meta={"title": document, "kind": "notes", "source": "manual"},
                             ppt_cite=f"Gamma, *{document}*")
            except Exception as e:  # noqa: BLE001 — the notes still make a Welcome page
                log.warning(f"[seed] welcome PDF skipped: {e}")
            page = create_page(conn, title, props, actor=actor)
            # commit_ops on a connection closed here (a handle left for the
            # GC would keep the directory from being deleted on Windows)
            after_commit(ws, conn, apply_ops(conn, page["id"], _insert_ops(page["id"], tree), actor=actor))
    except Exception as e:  # noqa: BLE001 — never fail the account being created
        log.warning(f"[seed] welcome page failed: {e}")
        return None
    return page["id"]


def create_workspace_files(ws_id: str):
    """Create fresh pages.db, data.db and uploads/ under workspaces/<id>/
    (existing files are kept). The Welcome page is ``seed_welcome``'s, once
    the workspace's rows exist (gamma/workspaces.py)."""
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
        register_functions(pages_db)
        for stmt in PAGES_SCHEMA:
            pages_db.execute(stmt)
        if not pages_db.execute("SELECT 1 FROM unified_blocks WHERE id = 'root'").fetchone():
            pages_db.execute(
                "INSERT INTO unified_blocks (id, parent_id, position, content, properties, created_at, updated_at) "
                "VALUES ('root', NULL, 'a0', '', '{}', ?, ?)",
                (nw, nw),
            )
        from .blocks_store import TRASH, TREES, ensure_reserved  # local, like seed_welcome's

        for reserved in (TRASH, *TREES):  # Recently deleted's parent and the folder and label trees, beside root
            ensure_reserved(pages_db, reserved)
        pages_db.commit()

    with closing(sqlite3.connect(str(target / "data.db"))) as data_db:
        data_db.execute("PRAGMA journal_mode=WAL")  # likewise
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
        user_id = insert_account(conn, username, pwhash, is_admin=True)
        conn.commit()
    workspaces.ensure_personal(user_id, welcome=True)
    # ASCII only: this prints during startup, and a redirected Windows console
    # (GBK) raises UnicodeEncodeError on characters it can't encode.
    # Raw print()s on purpose — the one-time password must go to the console
    # ONLY, never through the log buffer that admins can read later.
    print("[startup] fresh instance - created the admin account:")
    print(f"[startup]   username: {username}")
    print(f"[startup]   password: {'(from GAMMA_ADMIN_PASSWORD)' if env_password else password}")
    if not env_password:
        print("[startup]   shown only this once - log in and change it in account menu -> Manage users")
    return username, password


def insert_account(conn, username: str, password_hash: str, *, is_admin: bool = False) -> str:
    """The users row of a new (non-guest) account under a fresh id
    (``db.new_account_id``); returns the id. sqlite3.IntegrityError when
    the username is taken. The caller commits and gives it its personal
    workspace."""
    user_id = new_account_id()
    conn.execute(
        "INSERT INTO users (id, username, password_hash, is_guest, is_admin, created_at) VALUES (?, ?, ?, 0, ?, ?)",
        (user_id, username, password_hash, 1 if is_admin else 0, page_now()),
    )
    return user_id


def create_cloud_account(username: str, is_admin: bool = False) -> str:
    """An account only its cloud identity can sign in as: a real (non-guest)
    row with an EMPTY password hash — the password login refuses those —
    plus its personal workspace (gamma/cloud_auth.py ``provision``). Returns
    the workspace id."""
    return create_account(username, None, is_admin)


def create_account(username: str, password: str | None, is_admin: bool = False) -> str:
    """Insert an account row and its personal workspace. Returns the
    workspace id. Shared by manage.py and the admin API. Without a password
    the account has an empty hash: the password login refuses it until
    ``manage.py set-password`` gives it one (never a guest — guest accounts
    are gamma/guests.py's and expire)."""
    from . import workspaces

    pwhash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else ""
    with connect_users_db() as conn:
        user_id = insert_account(conn, username, pwhash, is_admin=is_admin)
        conn.commit()
    return workspaces.ensure_personal(user_id, welcome=True)
