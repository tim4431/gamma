"""Sign in with Gamma Cloud — the wire around ``gamma/cloud_auth.py``:

- ``GET /api/server-config`` (public): what the login page needs — whether
  cloud sign-in is on and the account server's address, whether guests may
  sign in, for how long (``guest_ttl_hours``) and ``demo`` mode
  (docs/dev/guests.md) — and ``page_host``, the per-account page hostname
  pattern (``GAMMA_PAGE_HOST``, "" = none), by which the app knows it was
  opened on a page host (gamma/publish.py);
- ``GET /api/auth/cloud/start?next=&link=1`` → redirect to the account
  server (``link=1`` with a session attaches the identity to that account);
- ``GET /api/auth/cloud/callback?code=&state=`` → session cookie + redirect
  to ``next``, or back to the login page with ``?cloud_error=``; the
  preference profile is pulled before the redirect and this server put on
  the person's server list (gamma/cloud_sync.py);
- ``GET /api/auth/cloud/connect/start?next=`` (admins) → the account
  server's page that connects this server; ``…/connect/callback`` saves the
  client it hands back and returns to ``next`` with ``?cloud_connect=ok``
  or ``?cloud_connect_error=`` (Settings → Server → Sign-in shows it);
- ``GET /api/auth/cloud/status`` / ``POST /api/auth/cloud/unlink`` for the
  signed-in account's own identity (Settings → Account & sync); an unlink takes
  this server off the person's server list and revokes the grant.
- ``GET /api/auth/cloud/sync-status``: the signed-in account's own
  preference profile sync state (Settings' section tags), from memory.
- ``POST /api/auth/cloud/sync``: Settings → Account & sync's Sync now, and the
  answer to a first sync's choice (its Fetch from cloud / Push to cloud
  dialog; ``merge`` is API-only).
"""

from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .. import cloud_auth, cloud_sync, config, ratelimit, server_settings
from ..auth import require_admin, require_personal_user, require_user, set_session_cookie
from ..cloud_auth import CloudAuthError
from ..db import connect_users_db
from ..logbuf import log
from .auth import new_session

router = APIRouter()


@router.get("/api/server-config")
async def server_config():
    cfg = cloud_auth.settings()
    enabled = cfg["enabled"] and not cloud_auth.needs_connect()  # an unconnected server offers no cloud button
    return {"cloud": {"enabled": enabled, "issuer": cfg["issuer"] if enabled else ""},
            "password_login": True, "registration": False, "guest": not cfg["share_host"],
            "guest_ttl_hours": server_settings.guest_ttl_hours(), "demo": server_settings.demo_mode(),
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
        if str(e) == cloud_auth.NOT_CONNECTED:  # a page to show it on, not an error body
            return _login_redirect(str(e), cloud_auth.safe_next(next))
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


@router.get("/api/auth/cloud/connect/start")
def cloud_connect_start(request: Request, next: str = "/"):
    require_admin(request)
    next_path = cloud_auth.safe_next(next)
    try:
        url = cloud_auth.connect_begin(next_path=next_path)
    except CloudAuthError as e:
        return _back(next_path, cloud_connect_error=str(e))
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/api/auth/cloud/connect/callback")
def cloud_connect_callback(request: Request, code: str = "", state: str = "", error: str = "",
                           error_description: str = ""):
    require_admin(request)
    ratelimit.check(f"cloud-connect:ip:{ratelimit.client_ip(request)}", 30, 600)
    next_path, problem = cloud_auth.connect_finish(code=code, state=state, error=error_description or error)
    return _back(next_path, **({"cloud_connect_error": problem} if problem else {"cloud_connect": "ok"}))


def _back(path: str, **params) -> RedirectResponse:
    """A redirect to ``path`` with ``params`` added to its query."""
    url = urlsplit(path)
    query = urlencode([*parse_qsl(url.query), *params.items()])
    return RedirectResponse(f"{url.path or '/'}?{query}", status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/api/auth/cloud/status")
async def cloud_status(request: Request):
    user = require_user(request)
    cfg = cloud_auth.settings()
    # the issuer is the portal's address too: Settings → Account & sync opens it from here;
    # ``connected`` false: the Link button waits for an admin to connect this server
    return {"identity": cloud_auth.status_of(user), "enabled": cfg["enabled"], "issuer": cfg["issuer"],
            "connected": not cloud_auth.needs_connect()}


@router.get("/api/auth/cloud/sync-status")
def cloud_sync_status(request: Request):
    """The caller's profile sync state (``cloud_sync.profile_status``) and
    whether a cloud identity is linked. A browser session only; no network."""
    user = require_personal_user(request, "The guest account keeps its settings in the browser.")
    identity = cloud_auth.status_of(user)
    linked = {"linked": True, "username": identity.get("username", "")} if identity else {"linked": False}
    return {"profile": cloud_sync.profile_status(user), "identity": linked}


class SyncRequest(BaseModel):
    action: Literal["sync", "merge", "fetch", "push"] = "sync"
    defaults: dict = {}  # "merge": the web app's default profile, the base of a first merge


@router.post("/api/auth/cloud/sync")
def cloud_sync_now(payload: SyncRequest, request: Request):
    """Sync the caller's profile with Gamma Cloud now: "sync" merges as the
    automatic sync does, "merge" / "fetch" / "push" also settle a first
    sync's choice. Answers the outcome and the new sync state."""
    user = require_personal_user(request, "The guest account keeps its settings in the browser.")
    if not cloud_sync.syncs(user):
        raise HTTPException(400, "Link a Gamma Cloud account first.")
    resolve = "" if payload.action == "sync" else payload.action
    try:
        outcome = cloud_sync.sync_profile(user, resolve=resolve, defaults=payload.defaults)
    except cloud_sync.NothingToFetch:
        raise HTTPException(409, "Gamma Cloud holds no settings yet.")
    status = cloud_sync.profile_status(user)
    if not outcome:
        raise HTTPException(502, status.get("error") or cloud_sync.UNREACHABLE)
    return {"outcome": outcome, "profile": status}


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
