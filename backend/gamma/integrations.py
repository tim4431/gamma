"""Revocable credentials for one account: ``read`` tokens (an assistant's
MCP connection, the HTTP API's reads), ``write`` tokens (a mirror pushing
its edits, docs/dev/mirror.md) and ``feedback`` tokens (a read token that
may also reply to and resolve the comment threads on a work folder's files,
docs/dev/feedback.md).

A token belongs to one workspace, ``workspace_id``, the one the HTTP API
reads. A read or feedback token's MCP connection may read more
(``workspaces``): ``""`` for that workspace alone, ``"*"`` for every
workspace the account can open, now and later, or a JSON list of ids
(``workspace_id`` first). Of those, it may also change the notes of
``edits`` (``edit_grant``): ``""`` none, ``"*"`` every one it reads, now
and later, or a JSON list of ids; only where the account itself may edit
(an owner or editor), and only through MCP (gamma/mcp_server.py): the HTTP
API stays read-only for such a token.

Only token hashes are stored. Recheck the account and workspace access on
every request, so removing membership also removes integration access.
"""

import hashlib
import json
import secrets
import time
from typing import NamedTuple

from fastapi import HTTPException

from .db import connect_users_db, page_now
from .workspaces import list_for_user, role_of


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


SCOPES = ("read", "write", "feedback")
ALL = "*"
EDITING_ROLES = ("owner", "editor")
DISPLAY_NAME_CHARS = 80


class Grant(NamedTuple):
    """The HTTP API's credential (``resolve_token_scope``)."""
    user_id: str
    username: str
    workspace_id: str
    scope: str
    token_id: str
    name: str


class Connection(NamedTuple):
    """An MCP connection (``resolve_connection``): ``user_id`` and
    ``workspaces`` first, as the pair it once was; ``editable`` the ones of
    them whose notes it may change now."""
    user_id: str
    workspaces: list
    scope: str
    token_id: str
    name: str
    username: str
    editable: tuple = ()


def display_name(name: str) -> str:
    """What a comment a token wrote is signed with: the token's name, an
    OAuth client's without its " (OAuth)", whitespace collapsed, at most
    ``DISPLAY_NAME_CHARS`` characters; "an agent" for none."""
    words = (name or "").split()
    if words and words[-1] == "(OAuth)":
        words.pop()
    return " ".join(words)[:DISPLAY_NAME_CHARS] or "an agent"


def grant(user_id: str, workspaces, scope: str = "read") -> tuple[str, str]:
    """``(workspace_id, workspaces)`` to store for the workspaces a token
    may read: ``"*"`` or a list of ids, each one the account can open (a
    write token: exactly one). HTTPException 400/403 otherwise."""
    if workspaces == ALL:
        if scope == "write":
            raise HTTPException(400, "A read and write token is for one workspace.")
        opened = [w for w in list_for_user(user_id) if not w["deleting"]]
        if not opened:
            raise HTTPException(403, "You have no workspace to connect.")
        return next((w for w in opened if w["default"]), opened[0])["id"], ALL
    ids = list(dict.fromkeys(w for w in workspaces or () if isinstance(w, str) and w))
    if not ids:
        raise HTTPException(400, "Choose at least one workspace.")
    if scope == "write" and len(ids) > 1:
        raise HTTPException(400, "A read and write token is for one workspace.")
    if len(ids) > 200 or not all(role_of(w, user_id) for w in ids):
        raise HTTPException(403, "You cannot connect this workspace.")
    return ids[0], json.dumps(ids) if len(ids) > 1 else ""


def edit_grant(user_id: str, primary: str, reads: str, edits) -> str:
    """``edits`` to store for the workspaces a connection that reads
    ``reads`` (``grant``'s stored value) may also change: ``""`` for none;
    ``"*"`` with ``reads`` ``"*"``, every one it reads where the account
    edits, now and later; else a JSON list of ids, each one it reads and the
    account edits. HTTPException 400/403 otherwise."""
    if edits == ALL:
        if reads != ALL:
            raise HTTPException(400, "Choose the workspaces it may edit.")
        return ALL
    ids = list(dict.fromkeys(w for w in edits or () if isinstance(w, str) and w))
    if not ids:
        return ""
    readable = granted(primary, reads)
    if len(ids) > 200 or (readable != ALL and not set(ids) <= set(readable)):
        raise HTTPException(400, "A connection can only edit the workspaces it reads.")
    if not all(role_of(w, user_id) in EDITING_ROLES for w in ids):
        raise HTTPException(403, "You can only view one of these workspaces, so the assistant cannot edit it.")
    return json.dumps(ids)


def editing(value: str) -> list[str] | str:
    """The workspaces a stored ``edits`` names: ``"*"`` or its list of ids."""
    if value == ALL:
        return ALL
    try:
        ids = json.loads(value) if value else []
    except ValueError:
        ids = []
    return [w for w in ids if isinstance(w, str)]


def granted(row_ws: str, value: str) -> list[str] | str:
    """The workspaces a stored token names: ``"*"`` or its list of ids."""
    if value == ALL:
        return ALL
    try:
        ids = json.loads(value) if value else []
    except ValueError:
        ids = []
    return [w for w in ids if isinstance(w, str)] or [row_ws]


def create_token(user_id: str, ws: str, name: str, days: int, *, oauth_resource: str | None = None,
                 scope: str = "read", workspaces: str = "", edits: str = "") -> dict:
    """A new token of ``ws``; ``workspaces`` is the stored grant ``grant``
    made (``""``: that workspace alone), ``edits`` the one ``edit_grant``
    made (``""``: it changes nothing)."""
    if scope not in SCOPES:
        raise HTTPException(400, "scope must be read, write or feedback")
    now = int(time.time())
    token = ("gamma_oauth_" if oauth_resource else "gamma_") + secrets.token_urlsafe(32)
    item = {"id": secrets.token_hex(16), "name": name, "workspace_id": ws, "scope": scope,
            "created_at": page_now(), "expires_at": now + days * 86400}
    with connect_users_db() as conn:
        # Serialize count + insert, including concurrent requests.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM integration_tokens WHERE user_id = ? AND expires_at <= ?", (user_id, now))
        count = conn.execute("SELECT COUNT(*) FROM integration_tokens WHERE user_id = ?", (user_id,)).fetchone()[0]
        if count >= 20:
            raise HTTPException(400, "Revoke an existing connection before creating another (limit 20).")
        conn.execute("INSERT INTO integration_tokens (id, token_hash, user_id, workspace_id, name, "
                     "created_at, expires_at, scope, workspaces, edits) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (item["id"], token_digest(token), user_id, ws, name, item["created_at"],
                      item["expires_at"], scope, workspaces, edits if scope != "write" else ""))
        if oauth_resource:
            conn.execute("INSERT INTO mcp_oauth VALUES ('access', ?, ?, ?)",
                         (token_digest(token), json.dumps({"resource": oauth_resource}), item["expires_at"]))
    return {**item, "token": token}


def set_workspaces(user_id: str, token_id: str, workspaces, edits=()) -> tuple[str, str, str]:
    """Change which workspaces one of the account's read or feedback
    tokens may read, and which of them it may edit; the stored
    ``(workspace_id, workspaces, edits)``."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT scope FROM integration_tokens WHERE id = ? AND user_id = ?",
                           (token_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "This connection is gone.")
    if row[0] == "write":
        raise HTTPException(400, "A read and write token is for one workspace.")
    ws, value = grant(user_id, workspaces, row[0])
    changes = edit_grant(user_id, ws, value, edits)
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET workspace_id = ?, workspaces = ?, edits = ? "
                     "WHERE id = ? AND user_id = ?", (ws, value, changes, token_id, user_id))
    return ws, value, changes


def forget_workspace(conn, ws: str) -> None:
    """A deleted workspace leaves every token, in the caller's transaction:
    one that read it alone goes, one that reads others too keeps them."""
    conn.execute("DELETE FROM integration_tokens WHERE workspace_id = ? AND workspaces = ''", (ws,))
    rows = conn.execute("SELECT id, workspace_id, workspaces FROM integration_tokens WHERE workspaces LIKE '[%' "
                        "AND EXISTS (SELECT 1 FROM json_each(workspaces) WHERE value = ?)", (ws,)).fetchall()
    for token_id, primary, value in rows:
        left = [w for w in granted(primary, value) if w != ws]
        if not left:
            conn.execute("DELETE FROM integration_tokens WHERE id = ?", (token_id,))
        else:
            conn.execute("UPDATE integration_tokens SET workspace_id = ?, workspaces = ? WHERE id = ?",
                         (left[0] if primary == ws else primary, json.dumps(left) if len(left) > 1 else "", token_id))
    rows = conn.execute("SELECT id, edits FROM integration_tokens WHERE edits LIKE '[%' "
                        "AND EXISTS (SELECT 1 FROM json_each(edits) WHERE value = ?)", (ws,)).fetchall()
    for token_id, value in rows:
        left = [w for w in editing(value) if w != ws]
        conn.execute("UPDATE integration_tokens SET edits = ? WHERE id = ?", (json.dumps(left) if left else "", token_id))


def _row(token: str, resource: str | None):
    """The live token's ``(user_id, username, workspace_id, scope,
    workspaces, id, name, edits)``, else None. ``resource`` is the MCP URL an OAuth
    token must be bound to."""
    if not token.startswith("gamma_") or len(token) > 128:
        return None
    with connect_users_db() as conn:
        if token.startswith("gamma_oauth_"):
            audience = conn.execute("SELECT value FROM mcp_oauth WHERE kind = 'access' AND key_hash = ? AND expires_at > ?",
                                    (token_digest(token), int(time.time()))).fetchone()
            if not audience or json.loads(audience[0]).get("resource") != resource:
                return None
        return conn.execute(
            "SELECT t.user_id, u.username, t.workspace_id, t.scope, t.workspaces, t.id, t.name, t.edits "
            "FROM integration_tokens t "
            "JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = ? AND t.expires_at > ? AND u.is_guest = 0",
            (token_digest(token), int(time.time()))).fetchone()


def resolve_token(token: str, resource: str | None = None) -> tuple[str, str] | None:
    """``(user_id, workspace_id)`` for a live token whose account still has
    access to its workspace, else None."""
    found = resolve_token_scope(token, resource)
    return (found[0], found[2]) if found else None


def _scope(value: str) -> str:
    """A stored scope, an unknown one read as ``read``."""
    return value if value in SCOPES else "read"


def resolve_token_scope(token: str, resource: str | None = None) -> Grant | None:
    """Like ``resolve_token`` plus the account's username, the token's
    scope, id and name: a ``Grant``. The HTTP API's credential: its own
    workspace only."""
    row = _row(token, resource)
    if not row or not role_of(row[2], row[0]):
        return None
    return Grant(row[0], row[1], row[2], _scope(row[3]), row[5], row[6])


def resolve_connection(token: str, resource: str | None = None) -> Connection | None:
    """The MCP connection: every workspace the token may read that its
    account can still open, its own first (the default for a call that
    names none), with the token's scope, id and name, the account's
    username, and those of them it may edit that the account still edits;
    None when it reads none."""
    row = _row(token, resource)
    if not row:
        return None
    user_id, username, primary, scope, value, token_id, name, edits = row
    wanted = granted(primary, value)
    if wanted == ALL:
        wanted = [w["id"] for w in list_for_user(user_id) if not w["deleting"]]
    ordered = [primary] + [w for w in wanted if w != primary] if primary in wanted else wanted
    roles = {w: role_of(w, user_id) for w in ordered}
    readable = [w for w in ordered if roles[w]]
    changing = editing(edits) if _scope(scope) != "write" else []
    editable = tuple(w for w in readable if roles[w] in EDITING_ROLES and (changing == ALL or w in changing))
    return Connection(user_id, readable, _scope(scope), token_id, name, username, editable) if readable else None
