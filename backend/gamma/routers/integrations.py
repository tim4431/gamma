"""Session-only management of external assistant connections, account-wide:
each connection names the workspaces it may read (a write token, its one
workspace; gamma/integrations.py).

A client that cannot sign in through the browser (DeepSeek Harness) is
connected with a setup code: the signed-in browser asks for one, and the
setup command the user runs claims it once for the connection's token, so
the token is never shown or pasted.
"""

import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import ratelimit
from ..auth import SESSION_COOKIE, require_personal_user_id, require_ws, ws_role
from ..db import connect_users_db
from ..integrations import ALL, create_token, grant, granted, set_workspaces, token_digest
from ..mcp_oauth import load, public_base, store
from ..workspaces import list_for_user

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
SETUP_SECONDS = 600


def _owner(request: Request) -> tuple[str, str]:
    user_id = require_personal_user_id(request, "Sign in with a personal account to connect an assistant.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin token management is not allowed.")
    return user_id, require_ws(request)


class Access(BaseModel):
    """The workspaces a connection may read: every one the account can open
    (now and later), or those listed, else the current one."""
    workspaces: list[str] | None = Field(default=None, max_length=200)
    all_workspaces: bool = False

    def wanted(self, ws: str):
        return ALL if self.all_workspaces else self.workspaces or [ws]


class TokenCreate(Access):
    name: str = Field(default="Codex", min_length=1, max_length=80)
    expires_in_days: int = Field(default=90, ge=1, le=365)
    # write: a mirror's push credential; feedback: a read token that also
    # answers the comments on a work folder's files
    scope: str = Field(default="read", pattern="^(read|write|feedback)$")


def _access(primary: str, value: str) -> dict:
    found = granted(primary, value)
    return {"all_workspaces": True, "workspaces": []} if found == ALL else {"all_workspaces": False, "workspaces": found}


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
        rows = conn.execute("SELECT id, name, created_at, expires_at, scope, workspace_id, workspaces "
                            "FROM integration_tokens WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    tokens = [{**dict(zip(("id", "name", "created_at", "expires_at", "scope", "workspace_id"), r[:6])),
               **_access(r[5], r[6])} for r in rows]
    spaces = [{"id": w["id"], "name": w["name"], "personal": w["personal"], "default": w["default"]}
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
    # A read or feedback token, a viewer's too (viewers comment): the workspaces it was given.
    primary, value = grant(user_id, payload.wanted(ws), payload.scope)
    item = create_token(user_id, primary, name, payload.expires_in_days, scope=payload.scope, workspaces=value)
    return {**item, **_access(primary, value)}


@router.patch("/tokens/{token_id}")
def change_access(token_id: str, payload: Access, request: Request):
    user_id, ws = _owner(request)
    primary, value = set_workspaces(user_id, token_id, payload.wanted(ws))
    return {"id": token_id, "workspace_id": primary, **_access(primary, value)}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, request: Request):
    user_id, _ws = _owner(request)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id = ? AND user_id = ?", (token_id, user_id))
    return {"ok": True}


class SetupCreate(Access):
    name: str = Field(min_length=1, max_length=80)


@router.post("/setup-codes", status_code=201)
def new_setup_code(payload: SetupCreate, request: Request, response: Response):
    """A one-time code a setup command exchanges for a read token
    (``claim``): valid for ten minutes, while this session lasts."""
    user_id, ws = _owner(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Give the connection a name.")
    primary, value = grant(user_id, payload.wanted(ws))
    code = secrets.token_urlsafe(32)
    store("setup", "", code, {"user": user_id, "name": name, "workspace_id": primary, "workspaces": value,
                              "session": token_digest(request.cookies.get(SESSION_COOKIE, ""))}, SETUP_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    return {"code": code, "expires_in": SETUP_SECONDS}


class Claim(BaseModel):
    code: str = Field(min_length=20, max_length=128)


@router.post("/claim")
def claim(payload: Claim, request: Request, response: Response):
    """Exchange a setup code for its connection's token, once. Needs no
    session: the code is the credential, and the account's session that
    asked for it must still be signed in."""
    ratelimit.check("integration-claim:" + ratelimit.client_ip(request), 20, 600)
    pending = load("setup", "", payload.code, consume=True)
    if not pending:
        raise HTTPException(400, "This setup command has expired or was used already. Copy a new one from Gamma.")
    with connect_users_db() as conn:
        sessions = conn.execute("SELECT token FROM sessions WHERE user_id = ?", (pending["user"],)).fetchall()
    if not any(token_digest(s[0]) == pending["session"] for s in sessions):
        raise HTTPException(400, "You signed out of Gamma since copying this command. Copy a new one.")
    primary, value = grant(pending["user"], granted(pending["workspace_id"], pending["workspaces"]))
    item = create_token(pending["user"], primary, pending["name"], 90, workspaces=value)
    response.headers["Cache-Control"] = "no-store"
    return {"token": item["token"], "name": item["name"], "expires_at": item["expires_at"]}
