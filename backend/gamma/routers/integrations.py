"""Session-only management of external assistant connections."""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import require_personal_user_id, require_ws, ws_role
from ..db import connect_users_db
from ..integrations import create_token
from ..mcp_oauth import public_base

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


def _owner(request: Request) -> tuple[str, str]:
    user_id = require_personal_user_id(request, "Sign in with a personal account to connect an assistant.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin token management is not allowed.")
    return user_id, require_ws(request)


class TokenCreate(BaseModel):
    name: str = Field(default="Codex", min_length=1, max_length=80)
    expires_in_days: int = Field(default=90, ge=1, le=365)
    scope: str = Field(default="read", pattern="^(read|write)$")  # write: a mirror's push credential


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
        rows = conn.execute("SELECT id, name, created_at, expires_at, scope FROM integration_tokens "
                            "WHERE user_id = ? AND workspace_id = ? ORDER BY created_at DESC", (user_id, ws))
        return {"tokens": [dict(zip(("id", "name", "created_at", "expires_at", "scope"), r)) for r in rows],
                "workspace_id": ws, "mcp_url": base + "/mcp", "oauth_available": oauth_error is None,
                "oauth_error": oauth_error}


@router.post("/tokens", status_code=201)
def new_token(payload: TokenCreate, request: Request, response: Response):
    user_id, ws = _owner(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Give the connection a name.")
    response.headers["Cache-Control"] = "no-store"
    if payload.scope == "write" and ws_role(request) == "viewer":
        raise HTTPException(403, "You can only view this workspace, so a write token cannot be made for it.")
    return create_token(user_id, ws, name, payload.expires_in_days, scope=payload.scope)


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, request: Request):
    user_id, ws = _owner(request)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id = ? AND user_id = ? AND workspace_id = ?",
                     (token_id, user_id, ws))
    return {"ok": True}
