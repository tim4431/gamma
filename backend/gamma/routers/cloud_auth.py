"""Sign in with Gamma Cloud — the wire around ``gamma/cloud_auth.py``:

- ``GET /api/server-config`` (public): what the login page needs — whether
  cloud sign-in is on and the account server's address — and ``page_host``,
  the per-account page hostname pattern (``GAMMA_PAGE_HOST``, "" = none),
  by which the app knows it was opened on a page host (gamma/publish.py);
- ``GET /api/auth/cloud/start?next=&link=1`` → redirect to the account
  server (``link=1`` with a session attaches the identity to that account);
- ``GET /api/auth/cloud/callback?code=&state=`` → session cookie + redirect
  to ``next``, or back to the login page with ``?cloud_error=``; the
  preference profile is pulled before the redirect and this server put on
  the person's server list (gamma/cloud_sync.py);
- ``GET /api/auth/cloud/status`` / ``POST /api/auth/cloud/unlink`` for the
  signed-in account's own identity (Settings → Account); an unlink takes
  this server off the person's server list and revokes the grant.
- ``GET /api/auth/cloud/sync-status``: the signed-in account's own
  preference profile sync state (Settings' section tags), from memory.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import cloud_auth, cloud_sync, config, ratelimit
from ..auth import require_personal_user, require_user, set_session_cookie
from ..cloud_auth import CloudAuthError
from ..db import connect_users_db
from ..logbuf import log
from .auth import new_session

router = APIRouter()


@router.get("/api/server-config")
async def server_config():
    cfg = cloud_auth.settings()
    return {"cloud": {"enabled": cfg["enabled"], "issuer": cfg["issuer"] if cfg["enabled"] else ""},
            "password_login": True, "registration": False, "guest": not cfg["share_host"],
            "page_host": config.page_host_pattern()}


@router.get("/api/auth/cloud/start")
def cloud_start(request: Request, next: str = "/", link: str = ""):
    ratelimit.check(f"cloud-start:ip:{ratelimit.client_ip(request)}", 30, 600)
    link_user = None
    if link:
        link_user = require_personal_user(request, "Sign in with a password first to link a Gamma Cloud account.")
        if request.state.is_guest:
            raise HTTPException(403, "The guest account cannot be linked.")
    try:
        url = cloud_auth.begin(request, link_user=link_user, next_path=cloud_auth.safe_next(next))
    except CloudAuthError as e:
        raise HTTPException(503, str(e))
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


def _login_redirect(error: str, next_path: str = "/") -> RedirectResponse:
    query = urlencode({"cloud_error": error})
    return RedirectResponse(f"{next_path.split('?')[0] or '/'}?{query}", status_code=302,
                            headers={"Cache-Control": "no-store"})


@router.get("/api/auth/cloud/callback")
def cloud_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   error_description: str = ""):
    ratelimit.check(f"cloud-callback:ip:{ratelimit.client_ip(request)}", 30, 600)
    if error:
        return _login_redirect(error_description or ("Sign-in cancelled." if error == "access_denied" else error))
    refresh = ""
    try:
        claims, tokens, next_path = cloud_auth.exchange(request, code=code, state=state)
        refresh = claims["_refresh_token"] = tokens.get("refresh_token", "")
        username = cloud_auth.resolve_account(claims)
    except CloudAuthError as e:
        log.info(f"cloud sign-in refused: {e}")
        cloud_auth.revoke_later([refresh])  # a refused sign-in leaves no device behind at the account server
        return _login_redirect(str(e))
    cloud_sync.signed_in(request, username, claims["sub"], tokens)
    token = new_session(username, via="cloud")
    resp = RedirectResponse(next_path, status_code=302, headers={"Cache-Control": "no-store"})
    set_session_cookie(resp, token, request)
    return resp


@router.get("/api/auth/cloud/status")
async def cloud_status(request: Request):
    user = require_user(request)
    cfg = cloud_auth.settings()
    # the issuer is the portal's address too: Settings → Account opens it from here
    return {"identity": cloud_auth.status_of(user), "enabled": cfg["enabled"], "issuer": cfg["issuer"]}


@router.get("/api/auth/cloud/sync-status")
def cloud_sync_status(request: Request):
    """The caller's profile sync state (``cloud_sync.profile_status``) and
    whether a cloud identity is linked. A browser session only; no network."""
    user = require_personal_user(request, "The guest account keeps its settings in the browser.")
    identity = cloud_auth.status_of(user)
    linked = {"linked": True, "username": identity.get("username", "")} if identity else {"linked": False}
    return {"profile": cloud_sync.profile_status(user), "identity": linked}


@router.post("/api/auth/cloud/unlink")
async def cloud_unlink(request: Request):
    """Detach the cloud identity. An account the cloud provisioned has no
    password, so unlinking would lock it out: refused until a password is
    set."""
    user = require_personal_user(request, "unlink from a browser session")
    subject, held = cloud_auth.grant_of(user)
    with connect_users_db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE username = ?", (user,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(400, "Set a password for this account first, or it could not sign in any more.")
        if not cloud_auth.unlink(conn, user):
            raise HTTPException(404, "no Gamma Cloud account is linked")
        conn.commit()
    cloud_sync.release_later(subject, held)
    log.info(f"cloud sign-in: {user} unlinked")
    return {"ok": True}
