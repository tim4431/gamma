"""Login, logout, session inspection, the account directory and guest login.
(Workspace backups as downloads and uploads — /export, /export-all,
/import-data — are routers/ws_backups.py.)"""

import secrets

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import cloud_auth, guests, ratelimit, version, workspaces
from ..auth import TOKEN_REFUSAL, is_token, require_user, set_session_cookie
from ..ratelimit import client_ip
from ..db import connect_users_db, page_now

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def new_session(username: str, via: str = "") -> str:
    """Mint a session row for an account; the caller sets the cookie. Shared
    by the password login and the cloud sign-in callback, which passes
    ``via="cloud"``: the grant check ends those sessions, and only those,
    when the account server refuses the account's grant."""
    token = secrets.token_urlsafe(32)
    with connect_users_db() as conn:
        conn.execute("INSERT INTO sessions (token, username, created_at, via) VALUES (?, ?, ?, ?)",
                     (token, username, page_now(), via))
        conn.commit()
    return token


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    # Throttle guessing: per-IP and per-username fixed windows. bcrypt is slow
    # by design, but that alone doesn't stop distributed/patient guessing.
    ip = client_ip(request)
    ratelimit.check(f"login:ip:{ip}", max_hits=10, window_seconds=300)
    ratelimit.check(f"login:user:{payload.username}", max_hits=10, window_seconds=300)
    with connect_users_db() as conn:
        row = conn.execute(
            "SELECT username, password_hash, is_guest FROM users WHERE username = ?",
            (payload.username,),
        ).fetchone()
    # Guest accounts have no password; a cloud-provisioned account has an
    # empty hash (only its cloud identity signs it in, gamma/cloud_auth.py).
    if not row or row[2] or not row[1]:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not bcrypt.checkpw(payload.password.encode(), row[1].encode()):
        raise HTTPException(status_code=401, detail="invalid credentials")
    ratelimit.reset(f"login:ip:{ip}")
    ratelimit.reset(f"login:user:{payload.username}")
    token = new_session(row[0])
    resp = JSONResponse({"ok": True, "username": row[0]})
    set_session_cookie(resp, token, request)
    return resp


# Sync def: a guest's logout deletes its workspace directory.
@router.post("/logout")
def logout(request: Request):
    """End the session. A guest account can never be signed into again (no
    password), so a guest's logout deletes the account on the spot instead
    of leaving it to the sweeper."""
    token = request.cookies.get("session")
    if token:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
    if request.state.is_guest and request.state.user:
        workspaces.delete_account(request.state.user)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@router.get("/session")
def get_session(request: Request):
    """Who am I, plus the workspaces I belong to (``workspaces``: [{id,
    name, role, personal, members}]) and my default one — enough for the
    frontend to pick a workspace and paint the switcher without another
    round trip. ``build`` (version, commit, label, frozen) is what a
    problem report names this server by; the login page gets it too."""
    user = request.state.user
    build = version.build_info()
    if not user:
        return {"user": None, "build": build}
    if is_token(request):  # a token is no session: GET /api/sync/whoami says who it is
        raise HTTPException(status_code=403, detail=TOKEN_REFUSAL)
    out = {"user": user, "is_guest": request.state.is_guest, "is_admin": request.state.is_admin,
           "default_workspace": request.state.default_ws or workspaces.ensure_personal(user),
           "workspaces": workspaces.list_for_user(user), "build": build}
    if request.state.is_guest:
        out["guest_expires_at"] = guests.account_expires_at(user)  # when the account and its workspace go
    return out


@router.get("/accounts")
def list_accounts(request: Request, q: str = ""):
    """The account directory — ``{accounts: [{username, is_admin}]}``, every
    non-guest account by name — for the invite and owner pickers. Any
    signed-in non-guest account may read it (a self-hosted server's
    members know each other; the guest sees nothing). On a share host
    (``cloud_share_host``: strangers' accounts side by side) only admins get
    the list; everyone else gets the account named exactly ``q``, if any."""
    require_user(request)
    if request.state.is_guest:
        raise HTTPException(status_code=403, detail="a guest account cannot list accounts")
    accounts = workspaces.accounts()
    if cloud_auth.settings()["share_host"] and not request.state.is_admin:
        accounts = [a for a in accounts if q and a["username"] == q.strip()]
    return {"accounts": accounts}


# Sync def: a guest login creates a workspace (and may restore the seed zip).
@router.post("/login-guest")
def login_guest(request: Request):
    """A fresh throwaway account for this visitor (gamma/guests.py): its own
    workspace, gone ``guest_ttl_hours`` after now."""
    if cloud_auth.settings()["share_host"]:
        # a public share host holds strangers' published pages: no guests there
        raise HTTPException(status_code=403, detail="This server has no guest access.")

    # Each call creates an account and a workspace directory: a tight rate per
    # IP, and GAMMA_GUEST_MAX live guests at most (new_guest's 503).
    ratelimit.check(f"guest:ip:{client_ip(request)}", max_hits=10, window_seconds=3600)
    username = guests.new_guest()
    now = page_now()
    token = secrets.token_urlsafe(32)
    with connect_users_db() as conn:
        # guest_date: the creation date — kept in the schema, read by nothing
        conn.execute(
            "INSERT INTO sessions (token, username, guest_date, created_at) VALUES (?, ?, ?, ?)",
            (token, username, now[:10], now),
        )
        conn.commit()
    resp = JSONResponse({"ok": True, "username": username})
    set_session_cookie(resp, token, request)
    return resp
