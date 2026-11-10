"""Revocable credentials for one account: ``read`` tokens (an assistant's
MCP connection, the HTTP API's reads) and ``write`` tokens (a mirror
pushing its edits, docs/dev/mirror.md).

A token belongs to one workspace, ``workspace_id``, the one the HTTP API
reads. A read token's MCP connection may read more (``workspaces``): ``""``
for that workspace alone, ``"*"`` for every workspace the account can open,
now and later, or a JSON list of ids (``workspace_id`` first).

Only token hashes are stored. Recheck the account and workspace access on
every request, so removing membership also removes integration access.
"""

import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from .db import connect_users_db, page_now
from .workspaces import list_for_user, role_of


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


SCOPES = ("read", "write")
ALL = "*"


def grant(user_id: str, workspaces, scope: str = "read") -> tuple[str, str]:
    """``(workspace_id, workspaces)`` to store for the workspaces a token
    may read: ``"*"`` or a list of ids, each one the account can open (a
    write token: exactly one). HTTPException 400/403 otherwise."""
    if workspaces == ALL:
        if scope != "read":
            raise HTTPException(400, "A read and write token is for one workspace.")
        opened = [w for w in list_for_user(user_id) if not w["deleting"]]
        if not opened:
            raise HTTPException(403, "You have no workspace to connect.")
        return next((w for w in opened if w["default"]), opened[0])["id"], ALL
    ids = list(dict.fromkeys(w for w in workspaces or () if isinstance(w, str) and w))
    if not ids:
        raise HTTPException(400, "Choose at least one workspace.")
    if scope != "read" and len(ids) > 1:
        raise HTTPException(400, "A read and write token is for one workspace.")
    if len(ids) > 200 or not all(role_of(w, user_id) for w in ids):
        raise HTTPException(403, "You cannot connect this workspace.")
    return ids[0], json.dumps(ids) if len(ids) > 1 else ""


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
                 scope: str = "read", workspaces: str = "") -> dict:
    """A new token of ``ws``; ``workspaces`` is the stored grant ``grant``
    made (``""``: that workspace alone)."""
    if scope not in SCOPES:
        raise HTTPException(400, "scope must be read or write")
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
                     "created_at, expires_at, scope, workspaces) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (item["id"], token_digest(token), user_id, ws, name, item["created_at"],
                      item["expires_at"], scope, workspaces))
        if oauth_resource:
            conn.execute("INSERT INTO mcp_oauth VALUES ('access', ?, ?, ?)",
                         (token_digest(token), json.dumps({"resource": oauth_resource}), item["expires_at"]))
    return {**item, "token": token}


def set_workspaces(user_id: str, token_id: str, workspaces) -> tuple[str, str]:
    """Change which workspaces one of the account's read tokens may read;
    the stored ``(workspace_id, workspaces)``."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT scope FROM integration_tokens WHERE id = ? AND user_id = ?",
                           (token_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "This connection is gone.")
    if row[0] != "read":
        raise HTTPException(400, "A read and write token is for one workspace.")
    ws, value = grant(user_id, workspaces)
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET workspace_id = ?, workspaces = ? WHERE id = ? AND user_id = ?",
                     (ws, value, token_id, user_id))
    return ws, value


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


def _row(token: str, resource: str | None):
    """The live token's ``(user_id, username, workspace_id, scope,
    workspaces)``, else None. ``resource`` is the MCP URL an OAuth token
    must be bound to."""
    if not token.startswith("gamma_") or len(token) > 128:
        return None
    with connect_users_db() as conn:
        if token.startswith("gamma_oauth_"):
            audience = conn.execute("SELECT value FROM mcp_oauth WHERE kind = 'access' AND key_hash = ? AND expires_at > ?",
                                    (token_digest(token), int(time.time()))).fetchone()
            if not audience or json.loads(audience[0]).get("resource") != resource:
                return None
        return conn.execute(
            "SELECT t.user_id, u.username, t.workspace_id, t.scope, t.workspaces FROM integration_tokens t "
            "JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = ? AND t.expires_at > ? AND u.is_guest = 0",
            (token_digest(token), int(time.time()))).fetchone()


def resolve_token(token: str, resource: str | None = None) -> tuple[str, str] | None:
    """``(user_id, workspace_id)`` for a live token whose account still has
    access to its workspace, else None."""
    found = resolve_token_scope(token, resource)
    return (found[0], found[2]) if found else None


def resolve_token_scope(token: str, resource: str | None = None) -> tuple[str, str, str, str] | None:
    """Like ``resolve_token`` plus the account's username and the token's
    scope: ``(user_id, username, workspace_id, scope)``. The HTTP API's
    credential: its own workspace only."""
    row = _row(token, resource)
    if not row or not role_of(row[2], row[0]):
        return None
    return row[0], row[1], row[2], row[3] if row[3] in SCOPES else "read"


def resolve_connection(token: str, resource: str | None = None) -> tuple[str, list[str]] | None:
    """The MCP connection's ``(user_id, workspaces)``: every workspace the
    token may read that its account can still open, its own first (the
    default for a call that names none), else None."""
    row = _row(token, resource)
    if not row:
        return None
    user_id, _name, primary, _scope, value = row
    wanted = granted(primary, value)
    if wanted == ALL:
        wanted = [w["id"] for w in list_for_user(user_id) if not w["deleting"]]
    ordered = [primary] + [w for w in wanted if w != primary] if primary in wanted else wanted
    readable = [w for w in ordered if role_of(w, user_id)]
    return (user_id, readable) if readable else None
