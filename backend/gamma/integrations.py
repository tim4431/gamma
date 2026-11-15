"""Revocable credentials for one account: ``read`` tokens (an assistant's
MCP connection, the HTTP API's reads), ``write`` tokens (a mirror pushing
its edits, docs/dev/mirror.md), ``computer`` tokens (a computer's channel
to this server, gamma/computers/: they open the channel's routes and
nothing else, ``auth.CHANNEL_PREFIX``, and no MCP connection) and ``turn``
tokens (the token of one turn of an agent on this computer, ``mint_turn``:
Gamma's read tools on MCP in its one workspace, and the writing tools there
(``comment``, ``resolve``, ``highlight``), and nothing else,
``auth.TURN_ONLY``; no connection a person sees or manages).

A token belongs to one workspace, ``workspace_id``, the one the HTTP API
reads. A read token's MCP connection may read more (``workspaces``):
``""`` for that workspace alone, ``"*"`` for every workspace the account
can open, now and later, or a JSON list of ids (``workspace_id`` first).
Of those, it may also comment in ``comments`` (``comment_grant``: write
comments, reply, resolve and highlight, docs/dev/mcp.md "The Comment
level") and change the notes of ``edits`` (``edit_grant``), each ``""``
none, ``"*"`` every one it reads, now and later, or a JSON list of ids.
Comments go where the account may comment (any role, a viewer's too), and
through the HTTP API in the token's own workspace as well; edits only where
the account itself may edit (an owner or editor), and only through MCP
(gamma/mcp_server.py): the HTTP API stays read-only for such a token. A
workspace it may edit it comments in too. ``asks``: whether each note
change waits for the person's yes first (mcp_server's elicitation), 1 by
default. A write token comments in its one workspace, as it writes there.

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


SCOPES = ("read", "write", "computer", "turn")
TURN = "turn"
ONE_WORKSPACE = {"write": "A read and write token is for one workspace.",   # the scopes of exactly one workspace
                 "computer": "A computer's token is for one workspace."}
ALL = "*"
EDITING_ROLES = ("owner", "editor")
DISPLAY_NAME_CHARS = 80


class Grant(NamedTuple):
    """The HTTP API's credential (``resolve_token_scope``); ``comments``
    whether it may comment in its workspace (``comment_grant``, or a write
    token)."""
    user_id: str
    username: str
    workspace_id: str
    scope: str
    token_id: str
    name: str
    comments: bool = False


class Connection(NamedTuple):
    """An MCP connection (``resolve_connection``): ``user_id`` and
    ``workspaces`` first, as the pair it once was; ``editable`` the ones of
    them whose notes it may change now, and whether each change asks first;
    ``commentable`` those it may comment in."""
    user_id: str
    workspaces: list
    scope: str
    token_id: str
    name: str
    username: str
    editable: tuple = ()
    asks: bool = True
    commentable: tuple = ()


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
    write or computer token: exactly one). HTTPException 400/403 otherwise."""
    if workspaces == ALL:
        if scope in ONE_WORKSPACE:
            raise HTTPException(400, ONE_WORKSPACE[scope])
        opened = [w for w in list_for_user(user_id) if not w["deleting"]]
        if not opened:
            raise HTTPException(403, "You have no workspace to connect.")
        return next((w for w in opened if w["default"]), opened[0])["id"], ALL
    ids = list(dict.fromkeys(w for w in workspaces or () if isinstance(w, str) and w))
    if not ids:
        raise HTTPException(400, "Choose at least one workspace.")
    if scope in ONE_WORKSPACE and len(ids) > 1:
        raise HTTPException(400, ONE_WORKSPACE[scope])
    if len(ids) > 200 or not all(role_of(w, user_id) for w in ids):
        raise HTTPException(403, "You cannot connect this workspace.")
    return ids[0], json.dumps(ids) if len(ids) > 1 else ""


def _level_grant(primary: str, reads: str, wanted, what: str) -> str | list[str]:
    """``wanted`` (``"*"`` or ids) as a level over the workspaces a
    connection reads (``reads``, ``grant``'s stored value): ``""`` for
    none, ``"*"`` with ``reads`` ``"*"``, else the ids, each one it reads.
    HTTPException 400 otherwise; ``what`` names the level ("comment in")."""
    if wanted == ALL:
        if reads != ALL:
            raise HTTPException(400, f"Choose the workspaces it may {what}.")
        return ALL
    ids = list(dict.fromkeys(w for w in wanted or () if isinstance(w, str) and w))
    if not ids:
        return ""
    readable = granted(primary, reads)
    if len(ids) > 200 or (readable != ALL and not set(ids) <= set(readable)):
        raise HTTPException(400, f"A connection can only {what} the workspaces it reads.")
    return ids


def edit_grant(user_id: str, primary: str, reads: str, edits) -> str:
    """``edits`` to store for the workspaces a connection that reads
    ``reads`` (``grant``'s stored value) may also change: ``""`` for none;
    ``"*"`` with ``reads`` ``"*"``, every one it reads where the account
    edits, now and later; else a JSON list of ids, each one it reads and the
    account edits. HTTPException 400/403 otherwise."""
    ids = _level_grant(primary, reads, edits, "edit")
    if not isinstance(ids, list):
        return ids
    if not all(role_of(w, user_id) in EDITING_ROLES for w in ids):
        raise HTTPException(403, "You can only view one of these workspaces, so the assistant cannot edit it.")
    return json.dumps(ids)


def comment_grant(user_id: str, primary: str, reads: str, comments) -> str:
    """``comments`` to store for the workspaces a connection that reads
    ``reads`` may also comment in (the Comment level: comments, replies,
    resolves and highlights): ``""`` for none; ``"*"`` with ``reads``
    ``"*"``, every one it reads, now and later; else a JSON list of ids, each
    one it reads. Any role comments, a viewer's too. HTTPException 400/403
    otherwise."""
    ids = _level_grant(primary, reads, comments, "comment in")
    if not isinstance(ids, list):
        return ids
    if not all(role_of(w, user_id) for w in ids):
        raise HTTPException(403, "You cannot connect this workspace.")
    return json.dumps(ids)


def named(value: str) -> list[str] | str:
    """The workspaces a stored ``edits`` or ``comments`` names: ``"*"`` or
    its list of ids."""
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
                 scope: str = "read", workspaces: str = "", edits: str = "", comments: str = "",
                 asks: bool = True) -> dict:
    """A new token of ``ws``; ``workspaces`` is the stored grant ``grant``
    made (``""``: that workspace alone), ``comments`` and ``edits`` the ones
    ``comment_grant`` and ``edit_grant`` made (``""``: it comments on and
    changes nothing), ``asks`` whether each change waits for the person's
    yes."""
    if scope not in SCOPES or scope == TURN:
        raise HTTPException(400, "scope must be read, write or computer")
    now = int(time.time())
    token = ("gamma_oauth_" if oauth_resource else "gamma_") + secrets.token_urlsafe(32)
    item = {"id": secrets.token_hex(16), "name": name, "workspace_id": ws, "scope": scope,
            "created_at": page_now(), "expires_at": now + days * 86400}
    with connect_users_db() as conn:
        # Serialize count + insert, including concurrent requests.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM integration_tokens WHERE user_id = ? AND expires_at <= ?", (user_id, now))
        count = conn.execute("SELECT COUNT(*) FROM integration_tokens WHERE user_id = ? AND scope != ?",
                             (user_id, TURN)).fetchone()[0]
        if count >= 20:
            raise HTTPException(400, "Revoke an existing connection before creating another (limit 20).")
        one = scope in ONE_WORKSPACE
        conn.execute("INSERT INTO integration_tokens (id, token_hash, user_id, workspace_id, name, created_at, "
                     "expires_at, scope, workspaces, edits, comments, asks) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (item["id"], token_digest(token), user_id, ws, name, item["created_at"],
                      item["expires_at"], scope, workspaces, "" if one else edits, "" if one else comments,
                      int(bool(asks))))
        if oauth_resource:
            conn.execute("INSERT INTO mcp_oauth VALUES ('access', ?, ?, ?)",
                         (token_digest(token), json.dumps({"resource": oauth_resource}), item["expires_at"]))
    return {**item, "token": token}


def mint_turn(user_id: str, ws: str, name: str, seconds: float) -> tuple[str, str]:
    """The token of one turn of an agent, on this computer
    (gamma/local_agents/tools.py) or on another one that takes the turn
    over its channel (gamma/routers/computers.py) → ``(its id, the
    token)``: the account's, of ``ws`` alone, named ``name`` (the agent's
    id, gamma/agents.py), living ``seconds``. It reads and comments through
    MCP and opens nothing else (``auth.TURN_ONLY``), is never listed and
    does not count toward the account's connections; the turn deletes it at
    its end (``drop``), a desktop app's next start any a crash left
    (``drop_turns``)."""
    token = "gamma_" + secrets.token_urlsafe(32)
    token_id = secrets.token_hex(16)
    with connect_users_db() as conn:
        conn.execute("INSERT INTO integration_tokens (id, token_hash, user_id, workspace_id, name, created_at, "
                     "expires_at, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (token_id, token_digest(token), user_id, ws, name, page_now(),
                      int(time.time() + max(1.0, seconds)), TURN))
    return token_id, token


def renew_turn(token_id: str, seconds: float, *, within: float | None = None) -> None:
    """A turn's token lives ``seconds`` more from now (its turn's deadline
    moved: it waited for the person, or its agent started again; a far
    turn wrote); with ``within``, only one that would expire within that
    many seconds, so a renewal at every write writes once in a while."""
    now = time.time()
    soon = "" if within is None else f" AND expires_at < {int(now + within)}"
    with connect_users_db() as conn:
        conn.execute(f"UPDATE integration_tokens SET expires_at = ? WHERE id = ? AND scope = ?{soon}",
                     (int(now + max(1.0, seconds)), token_id, TURN))


def drop(token_id: str) -> None:
    """A token gone: what the next request with it is refused."""
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id = ?", (token_id,))


def drop_turns() -> int:
    """Every turn's token gone (at start: no turn runs yet) → how many."""
    with connect_users_db() as conn:
        return conn.execute("DELETE FROM integration_tokens WHERE scope = ?", (TURN,)).rowcount


def set_workspaces(user_id: str, token_id: str, workspaces, edits=(), comments=()) -> tuple[str, str, str, str]:
    """Change which workspaces one of the account's read tokens may read,
    and which of them it may comment in and edit; the stored
    ``(workspace_id, workspaces, edits, comments)``."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT scope FROM integration_tokens WHERE id = ? AND user_id = ? AND scope != ?",
                           (token_id, user_id, TURN)).fetchone()
    if not row:
        raise HTTPException(404, "This connection is gone.")
    if row[0] in ONE_WORKSPACE:
        raise HTTPException(400, ONE_WORKSPACE[row[0]])
    ws, value = grant(user_id, workspaces, row[0])
    changes = edit_grant(user_id, ws, value, edits)
    talks = comment_grant(user_id, ws, value, comments)
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET workspace_id = ?, workspaces = ?, edits = ?, comments = ? "
                     "WHERE id = ? AND user_id = ?", (ws, value, changes, talks, token_id, user_id))
    return ws, value, changes, talks


def set_asks(user_id: str, token_id: str, asks: bool) -> None:
    """Whether one of the account's connections asks before each change it
    makes (Settings, or the person's "don't ask again" on a question)."""
    with connect_users_db() as conn:
        found = conn.execute("UPDATE integration_tokens SET asks = ? WHERE id = ? AND user_id = ? AND scope != ?",
                             (int(bool(asks)), token_id, user_id, TURN)).rowcount
    if not found:
        raise HTTPException(404, "This connection is gone.")


def forget_workspace(conn, ws: str) -> None:
    """A deleted workspace leaves every token, in the caller's transaction:
    one that read it alone goes, one that reads others too keeps them; what
    the computers of its tokens announced goes too (gamma/computers/)."""
    from .computers import registry  # local: the registry imports workspaces

    conn.execute("DELETE FROM integration_tokens WHERE workspace_id = ? AND workspaces = ''", (ws,))
    gone = [token_id for token_id, in conn.execute("SELECT token_id FROM computers WHERE workspace_id = ?", (ws,))]
    conn.execute("DELETE FROM computers WHERE workspace_id = ?", (ws,))
    registry.drop(gone)
    rows = conn.execute("SELECT id, workspace_id, workspaces FROM integration_tokens WHERE workspaces LIKE '[%' "
                        "AND EXISTS (SELECT 1 FROM json_each(workspaces) WHERE value = ?)", (ws,)).fetchall()
    for token_id, primary, value in rows:
        left = [w for w in granted(primary, value) if w != ws]
        if not left:
            conn.execute("DELETE FROM integration_tokens WHERE id = ?", (token_id,))
        else:
            conn.execute("UPDATE integration_tokens SET workspace_id = ?, workspaces = ? WHERE id = ?",
                         (left[0] if primary == ws else primary, json.dumps(left) if len(left) > 1 else "", token_id))
    for column in ("edits", "comments"):
        rows = conn.execute(f"SELECT id, {column} FROM integration_tokens WHERE {column} LIKE '[%' "
                            f"AND EXISTS (SELECT 1 FROM json_each({column}) WHERE value = ?)", (ws,)).fetchall()
        for token_id, value in rows:
            left = [w for w in named(value) if w != ws]
            conn.execute(f"UPDATE integration_tokens SET {column} = ? WHERE id = ?",
                         (json.dumps(left) if left else "", token_id))


def _row(token: str, resource: str | None):
    """The live token's ``(user_id, username, workspace_id, scope,
    workspaces, id, name, edits, asks, comments)``, else None. ``resource``
    is the MCP URL an OAuth token must be bound to."""
    if not token.startswith("gamma_") or len(token) > 128:
        return None
    with connect_users_db() as conn:
        if token.startswith("gamma_oauth_"):
            audience = conn.execute("SELECT value FROM mcp_oauth WHERE kind = 'access' AND key_hash = ? AND expires_at > ?",
                                    (token_digest(token), int(time.time()))).fetchone()
            if not audience or json.loads(audience[0]).get("resource") != resource:
                return None
        return conn.execute(
            "SELECT t.user_id, u.username, t.workspace_id, t.scope, t.workspaces, t.id, t.name, t.edits, t.asks, "
            "t.comments FROM integration_tokens t "
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


def _covers(value: str, ws: str) -> bool:
    """Whether a stored ``edits`` or ``comments`` names ``ws``."""
    found = named(value)
    return found == ALL or ws in found


def comments_in(scope: str, edits: str, comments: str, ws: str) -> bool:
    """Whether a token of ``scope`` with these stored levels comments in
    ``ws``, one it reads: a write or turn token in its one workspace, a read
    token where its Comment level or its edit level names it."""
    scope = _scope(scope)
    return scope in ("write", TURN) or (scope == "read" and (_covers(comments, ws) or _covers(edits, ws)))


def resolve_token_scope(token: str, resource: str | None = None) -> Grant | None:
    """Like ``resolve_token`` plus the account's username, the token's
    scope, id and name, and whether it comments there: a ``Grant``. The
    HTTP API's credential: its own workspace only."""
    row = _row(token, resource)
    if not row or not role_of(row[2], row[0]):
        return None
    return Grant(row[0], row[1], row[2], _scope(row[3]), row[5], row[6],
                 comments_in(row[3], row[7], row[9], row[2]))


def resolve_connection(token: str, resource: str | None = None) -> Connection | None:
    """The MCP connection: every workspace the token may read that its
    account can still open, its own first (the default for a call that
    names none), with the token's scope, id and name, the account's
    username, those of them it may edit that the account still edits,
    whether each change asks first and those it may comment in
    (``comments_in``); None when it reads none, and for a computer's token,
    which opens its channel alone."""
    row = _row(token, resource)
    if not row or row[3] == "computer":
        return None
    user_id, username, primary, scope, value, token_id, name, edits, asks, comments = row
    wanted = granted(primary, value)
    if wanted == ALL:
        wanted = [w["id"] for w in list_for_user(user_id) if not w["deleting"]]
    ordered = [primary] + [w for w in wanted if w != primary] if primary in wanted else wanted
    roles = {w: role_of(w, user_id) for w in ordered}
    readable = [w for w in ordered if roles[w]]
    editable = tuple(w for w in readable if _scope(scope) == "read" and roles[w] in EDITING_ROLES
                     and _covers(edits, w))
    commentable = tuple(w for w in readable if comments_in(scope, edits, comments, w))
    return Connection(user_id, readable, _scope(scope), token_id, name, username, editable,
                      bool(asks), commentable) if readable else None
