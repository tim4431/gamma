"""Session-only management of external assistant connections, account-wide:
each connection names the workspaces it may read (a write token, its one
workspace) and those of them whose notes it may also change through MCP
(gamma/integrations.py).

A client without browser sign-in of its own (DeepSeek Harness) is signed
in by its setup command, the way a TV or a CLI signs in (OAuth's device
flow, RFC 8628, in Gamma's own shape): the command asks for a sign-in, opens
Gamma in the browser on it, where the user checks its code, ticks the
workspaces and allows it, and collects the connection's token, so the token
is never shown or pasted and the command holds no secret.
"""

import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import ratelimit
from ..auth import SESSION_COOKIE, require_personal_user_id, require_ws, ws_role
from ..computers import registry
from ..db import connect_users_db
from ..integrations import (ALL, create_token, edit_grant, editing, grant, granted, set_asks, set_workspaces,
                            token_digest)
from ..mcp_oauth import load, public_base, store
from ..workspaces import list_for_user

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
DEVICE_SECONDS = 600
DEVICE_INTERVAL = 2
# A user code's letters: no vowels, so no words, and none read as a digit.
CODE_LETTERS = "BCDFGHJKLMNPQRSTVWXZ"


def _owner(request: Request) -> tuple[str, str]:
    user_id = require_personal_user_id(request, "Sign in with a personal account to connect an assistant.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin token management is not allowed.")
    return user_id, require_ws(request)


class Access(BaseModel):
    """The workspaces a connection may read: every one the account can open
    (now and later), or those listed, else the current one; and of those,
    the ones it may also edit: every one (``edit_all``, with
    ``all_workspaces``) or those listed, else none."""
    workspaces: list[str] | None = Field(default=None, max_length=200)
    all_workspaces: bool = False
    edit_workspaces: list[str] | None = Field(default=None, max_length=200)
    edit_all: bool = False

    def wanted(self, ws: str):
        return ALL if self.all_workspaces else self.workspaces or [ws]

    def edits(self):
        return ALL if self.edit_all else self.edit_workspaces or []


class AccessChange(Access):
    # whether each change it makes asks first; left out, as it was
    asks: bool | None = None


class TokenCreate(Access):
    name: str = Field(default="Codex", min_length=1, max_length=80)
    expires_in_days: int = Field(default=90, ge=1, le=365)
    # write: a mirror's push credential; feedback: a read token that also
    # answers the comments on a work folder's files; computer: a computer's
    # channel to this server, which opens nothing else (gamma/computers/)
    scope: str = Field(default="read", pattern="^(read|write|feedback|computer)$")


def _access(primary: str, value: str, edits: str = "") -> dict:
    found, changing = granted(primary, value), editing(edits)
    return {"all_workspaces": found == ALL, "workspaces": [] if found == ALL else found,
            "edit_all": changing == ALL, "edit_workspaces": [] if changing == ALL else changing}


@router.get("/tokens")
def list_tokens(request: Request, response: Response):
    user_id, ws = _owner(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        base = public_base(request)
        oauth_error = None
    except HTTPException as exc:
        base = str(request.base_url).rstrip("/")
        oauth_error = str(exc.detail)
    with connect_users_db() as conn:
        rows = conn.execute("SELECT id, name, created_at, expires_at, scope, workspace_id, workspaces, edits, asks "
                            "FROM integration_tokens WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    tokens = [{**dict(zip(("id", "name", "created_at", "expires_at", "scope", "workspace_id"), r[:6])),
               **_access(r[5], r[6], r[7]), "asks": bool(r[8])} for r in rows]
    spaces = [{"id": w["id"], "name": w["name"], "personal": w["personal"], "default": w["default"], "role": w["role"]}
              for w in list_for_user(user_id) if not w["deleting"]]
    return {"tokens": tokens, "workspaces": spaces, "workspace_id": ws, "mcp_url": base + "/mcp",
            "oauth_available": oauth_error is None, "oauth_error": oauth_error}


@router.post("/tokens", status_code=201)
def new_token(payload: TokenCreate, request: Request, response: Response):
    user_id, ws = _owner(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Give the connection a name.")
    response.headers["Cache-Control"] = "no-store"
    if payload.scope == "write":
        if ws_role(request) == "viewer":
            raise HTTPException(403, "You can only view this workspace, so a write token cannot be made for it.")
        return {**create_token(user_id, ws, name, payload.expires_in_days, scope="write"), **_access(ws, "")}
    if payload.scope == "computer":
        # The desktop app's, for its channel to this server: the workspace open, a viewer's too (it only
        # shows that the account's vaults are kept on that computer).
        return {**create_token(user_id, ws, name, payload.expires_in_days, scope="computer"), **_access(ws, "")}
    # A read or feedback token, a viewer's too (viewers comment): the workspaces it was given.
    primary, value = grant(user_id, payload.wanted(ws), payload.scope)
    edits = edit_grant(user_id, primary, value, payload.edits())
    item = create_token(user_id, primary, name, payload.expires_in_days, scope=payload.scope, workspaces=value,
                        edits=edits)
    return {**item, **_access(primary, value, edits)}


@router.patch("/tokens/{token_id}")
def change_access(token_id: str, payload: AccessChange, request: Request):
    user_id, ws = _owner(request)
    primary, value, edits = set_workspaces(user_id, token_id, payload.wanted(ws), payload.edits())
    if payload.asks is not None:
        set_asks(user_id, token_id, payload.asks)
    return {"id": token_id, "workspace_id": primary, **_access(primary, value, edits)}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, request: Request):
    user_id, _ws = _owner(request)
    with connect_users_db() as conn:
        gone = conn.execute("DELETE FROM integration_tokens WHERE id = ? AND user_id = ?",
                            (token_id, user_id)).rowcount
    if gone:
        registry.forget(token_id)  # a computer's token: its presence goes with it
    # ``revoked``: whether the account had such a token (the desktop app says when one was left)
    return {"ok": True, "revoked": bool(gone)}


# --- a setup command's sign-in --------------------------------------------------------
# The records (mcp_oauth's store, ten minutes each): "device" by the
# command's device code, the code it collects with; "device_user" by the
# user code the browser opens; "device_consent" by the consent page's csrf;
# then "device_ok" (the grant) or "device_denied" by the device code.

def _user_code() -> str:
    letters = "".join(secrets.choice(CODE_LETTERS) for _ in range(8))
    return letters[:4] + "-" + letters[4:]


def _user_key(code: str) -> str:
    """A user code as stored: upper case, without its dash."""
    return code.replace("-", "").strip().upper()


class DeviceStart(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/device", status_code=201)
def start_device(payload: DeviceStart, request: Request, response: Response):
    """A sign-in for a setup command: no session, nothing granted yet. The
    command opens ``/?gamma_device=<user_code>`` and asks ``device/token``
    every ``interval`` seconds until the user allows or cancels it."""
    ratelimit.check("integration-device:" + ratelimit.client_ip(request), 20, 600)
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Give the connection a name.")
    device_code, user_code = secrets.token_urlsafe(32), _user_code()
    store("device", "", device_code, {"name": name}, DEVICE_SECONDS)
    store("device_user", "", _user_key(user_code), {"device_code": device_code, "name": name}, DEVICE_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    return {"device_code": device_code, "user_code": user_code, "expires_in": DEVICE_SECONDS,
            "interval": DEVICE_INTERVAL}


@router.get("/device/request")
def device_request(user_code: str, request: Request, response: Response):
    """The consent page's view of a waiting sign-in: who asks, the code to
    check against the terminal's, and the account's workspaces."""
    user_id, _ws = _owner(request)
    key = _user_key(user_code[:16])
    pending = load("device_user", "", key)
    if not pending:
        raise HTTPException(400, "This sign-in expired or was answered already. Run the setup command again.")
    csrf = secrets.token_urlsafe(32)
    store("device_consent", "", csrf, {"user_code": key, "user": user_id,
                                       "session": token_digest(request.cookies.get(SESSION_COOKIE, ""))},
          DEVICE_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    return {"client_name": pending["name"], "user_code": key[:4] + "-" + key[4:], "username": request.state.user,
            "csrf": csrf, "workspaces": [w for w in list_for_user(user_id) if not w["deleting"]],
            "default_workspace": request.state.default_ws, "feedback_asked": False}


class DeviceConsent(Access):
    user_code: str = Field(max_length=16)
    csrf: str = Field(max_length=128)
    # whether it may also answer the comments on a work folder's files
    feedback: bool = False
    # whether each change it makes where it edits asks first
    asks: bool = True
    approve: bool


@router.post("/device/consent")
def device_consent(payload: DeviceConsent, request: Request):
    """Allow a waiting sign-in, with the workspaces it may read, or cancel
    it; once either way."""
    user_id, ws = _owner(request)
    session = token_digest(request.cookies.get(SESSION_COOKIE, ""))
    key = _user_key(payload.user_code)
    binding = load("device_consent", "", payload.csrf)
    if not binding or binding["user_code"] != key or binding["user"] != user_id or binding["session"] != session:
        raise HTTPException(403, "Consent expired or the signed-in account changed. Start again.")
    scope = "feedback" if payload.feedback else "read"
    primary, stored = grant(user_id, payload.wanted(ws), scope) if payload.approve else ("", "")
    edits = edit_grant(user_id, primary, stored, payload.edits()) if payload.approve else ""
    pending = load("device_user", "", key, consume=True)
    load("device_consent", "", payload.csrf, consume=True)
    if not pending:
        raise HTTPException(400, "This sign-in expired or was answered already. Run the setup command again.")
    if payload.approve:
        store("device_ok", "", pending["device_code"], {"user": user_id, "workspace_id": primary,
                                                        "workspaces": stored, "edits": edits, "scope": scope,
                                                        "asks": payload.asks, "session": session}, DEVICE_SECONDS)
    else:
        store("device_denied", "", pending["device_code"], {"user": user_id}, DEVICE_SECONDS)
    return {"ok": True}


class DevicePoll(BaseModel):
    device_code: str = Field(min_length=20, max_length=128)


@router.post("/device/token")
def device_token(payload: DevicePoll, request: Request, response: Response):
    """What the setup command asks while it waits: 202 ``pending``, then
    the connection's token once, minted only now, so a connection appears
    only once something has it. The session that allowed it must still be
    signed in."""
    ratelimit.check("integration-device-token:" + ratelimit.client_ip(request), 600, 600)
    response.headers["Cache-Control"] = "no-store"
    code = payload.device_code
    allowed = load("device_ok", "", code, consume=True)
    if not allowed:
        if load("device_denied", "", code, consume=True):
            load("device", "", code, consume=True)
            raise HTTPException(403, "The connection was cancelled in Gamma.")
        if load("device", "", code):
            response.status_code = 202
            return {"status": "pending"}
        raise HTTPException(400, "This sign-in expired or was used already. Run the setup command again.")
    pending = load("device", "", code, consume=True)
    if not pending:
        raise HTTPException(400, "This sign-in expired or was used already. Run the setup command again.")
    with connect_users_db() as conn:
        sessions = conn.execute("SELECT token FROM sessions WHERE user_id = ?", (allowed["user"],)).fetchall()
    if not any(token_digest(s[0]) == allowed["session"] for s in sessions):
        raise HTTPException(400, "You signed out of Gamma before the connection was made. Run the setup command again.")
    primary, value = grant(allowed["user"], granted(allowed["workspace_id"], allowed["workspaces"]), allowed["scope"])
    edits = edit_grant(allowed["user"], primary, value, editing(allowed.get("edits", "")))
    item = create_token(allowed["user"], primary, pending["name"], 90, scope=allowed["scope"], workspaces=value,
                        edits=edits, asks=allowed.get("asks", True))
    return {"token": item["token"], "name": item["name"], "expires_at": item["expires_at"]}
