"""Sign in with Gamma Cloud — the wire around ``gamma/cloud_auth.py``:

- ``GET /api/server-config`` (public): what the login page needs — whether
  cloud sign-in is on, the account server's address and whether the page
  leads with it (``cloud.lead``: a hosted container or a share host, whose
  people all come from Gamma Cloud), whether guests may
  sign in, for how long (``guest_ttl_hours``), whether a guest starts with
  the ``GAMMA_GUEST_SEED`` library (``guest_seeded``) and ``demo`` mode
  (docs/dev/guests.md) — and ``page_host``, the per-account page hostname
  pattern (``GAMMA_PAGE_HOST``, "" = none), by which the app knows it was
  opened on a page host (gamma/publish.py) — and, for a hosted container
  (gamma/hosted.py), ``read_only`` and ``hosted: {plan, status}`` (null
  elsewhere, or before the first sync);
- ``GET /api/auth/cloud/start?next=&link=1`` → redirect to the account
  server (``link=1`` with a session attaches the identity to that account);
- ``GET /api/auth/cloud/callback?code=&state=`` → session cookie + redirect
  to ``next``, or back to the login page with ``?cloud_error=``; the
  preference profile is pulled before the redirect and this server put on
  the person's server list (gamma/cloud_sync.py). A sign-in the desktop app
  started instead sends the browser to the app's loopback address with
  ``?result=`` or ``?error=``, and sets no cookie;
- ``POST /api/auth/cloud/app-start`` ``{next, link, return_to, challenge}``
  → ``{url}``: the desktop app's sign-in, which it opens in the system
  browser; ``POST /api/auth/cloud/app-claim`` (a form: ``result``,
  ``verifier``, from the app's window with ``X-Gamma-Desktop: claim``) →
  session cookie + redirect to ``next``, or the login page with
  ``?cloud_error=``;
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

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import cloud_auth, cloud_sync, config, guests, hosted, ratelimit, server_settings
from ..auth import require_admin, require_personal_user_id, require_user_id, set_session_cookie
from ..cloud_auth import CloudAuthError
from ..db import connect_users_db
from ..logbuf import log
from .auth import new_session

router = APIRouter()


@router.get("/api/server-config")
def server_config():
    cfg = cloud_auth.settings()
    enabled = cfg["enabled"] and not cloud_auth.needs_connect()  # an unconnected server offers no cloud button
    plan = hosted.limits()
    lead = enabled and (config.hosted() or cfg["share_host"])
    return {"cloud": {"enabled": enabled, "issuer": cfg["issuer"] if enabled else "", "lead": lead},
            "password_login": True, "registration": False, "guest": guests.logins_open(),
            "guest_ttl_hours": server_settings.guest_ttl_hours(), "demo": server_settings.demo_mode(),
            "guest_seeded": bool(config.guest_seed_path()), "page_host": config.page_host_pattern(),
            "read_only": bool(plan and plan["read_only"]),
            "hosted": {"plan": plan["plan"], "status": plan["status"]} if plan else None}


@router.get("/api/auth/cloud/start")
def cloud_start(request: Request, next: str = "/", link: str = ""):
    ratelimit.check(f"cloud-start:ip:{ratelimit.client_ip(request)}", 30, 600)
    link_user = None
    if link:
        link_user = require_personal_user_id(request, "Sign in with a password first to link a Gamma Cloud account.")
        if request.state.is_guest:
            raise HTTPException(403, "The guest account cannot be linked.")
    try:
        url = cloud_auth.begin(request, link_user=link_user, next_path=cloud_auth.safe_next(next))
    except CloudAuthError as e:
        if str(e) == cloud_auth.NOT_CONNECTED:  # a page to show it on, not an error body
            return _login_redirect(str(e), cloud_auth.safe_next(next))
        raise HTTPException(503, str(e))
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


def _to_app(app: dict, **params: str) -> RedirectResponse:
    """The browser on to the desktop app's loopback address with ``result``
    or ``error``."""
    return RedirectResponse(cloud_auth.app_redirect(app["return_to"], **params), status_code=302,
                            headers={"Cache-Control": "no-store"})


def _login_redirect(error: str, next_path: str = "/") -> RedirectResponse:
    query = urlencode({"cloud_error": error})
    return RedirectResponse(f"{next_path.split('?')[0] or '/'}?{query}", status_code=302,
                            headers={"Cache-Control": "no-store"})


class AppStartRequest(BaseModel):
    next: str = Field("/", max_length=2048)
    link: bool = False
    return_to: str = Field(max_length=256)  # the app's loopback address (cloud_auth.app_return)
    challenge: str = Field(max_length=64)   # S256 of the verifier the app keeps


@router.post("/api/auth/cloud/app-start")
def cloud_app_start(payload: AppStartRequest, request: Request):
    """A sign-in the desktop app started (gamma/cloud_auth.py, the app's
    sign-in): the authorize URL for it to open in the system browser."""
    ratelimit.check(f"cloud-start:ip:{ratelimit.client_ip(request)}", 30, 600)
    link_user = None
    if payload.link:
        link_user = require_personal_user_id(request, "Sign in with a password first to link a Gamma Cloud account.")
        if request.state.is_guest:
            raise HTTPException(403, "The guest account cannot be linked.")
    try:
        app = cloud_auth.app_params(payload.return_to, payload.challenge)
    except CloudAuthError as e:
        raise HTTPException(400, str(e))
    try:
        url = cloud_auth.begin(request, link_user=link_user, next_path=cloud_auth.safe_next(payload.next), app=app)
    except CloudAuthError as e:
        raise HTTPException(503, str(e))
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})


@router.get("/api/auth/cloud/callback")
def cloud_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   error_description: str = ""):
    ratelimit.check(f"cloud-callback:ip:{ratelimit.client_ip(request)}", 30, 600)
    pending = cloud_auth.take_pending(request, state)
    app = (pending or {}).get("app")
    # A sign-in the desktop app started reports back to the app, not this browser.
    refuse = (lambda e: _to_app(app, error=e)) if app else _login_redirect
    if error:
        return refuse(error_description or ("Sign-in cancelled." if error == "access_denied" else error))
    refresh = ""
    try:
        claims, tokens, next_path = cloud_auth.exchange(request, code=code, pending=pending)
        refresh = claims["_refresh_token"] = tokens.get("refresh_token", "")
        user_id, _username = cloud_auth.resolve_account(claims)
    except CloudAuthError as e:
        log.info(f"cloud sign-in refused: {e}")
        cloud_auth.revoke_later([refresh])  # a refused sign-in leaves no device behind at the account server
        return refuse(str(e))
    cloud_sync.signed_in(request, user_id, claims["sub"], tokens)
    if app:
        # No session in this browser: the app redeems the result in its own window (app-claim).
        return _to_app(app, result=cloud_auth.app_result(user_id, next_path, app["challenge"]))
    token = new_session(user_id, via="cloud")
    resp = RedirectResponse(next_path, status_code=302, headers={"Cache-Control": "no-store"})
    set_session_cookie(resp, token, request)
    return resp


# What the desktop app's window sends with its claim. A page elsewhere cannot
# (a cross-site form sets no headers, a fetch with one asks first and is
# refused), so no site can sign this browser into an account of its choosing.
APP_CLAIM_HEADER = "X-Gamma-Desktop"


@router.post("/api/auth/cloud/app-claim")
def cloud_app_claim(request: Request, result: str = Form("", max_length=128), verifier: str = Form("", max_length=128)):
    """The desktop app's window redeems the result its loopback address was
    given, with its verifier: the session cookie, and on to ``next``."""
    ratelimit.check(f"cloud-claim:ip:{ratelimit.client_ip(request)}", 30, 600)
    if request.headers.get(APP_CLAIM_HEADER) != "claim":
        raise HTTPException(403, "Only the Gamma desktop app redeems a sign-in here.")
    try:
        user_id, next_path = cloud_auth.app_claim(result, verifier)
    except CloudAuthError as e:
        log.info(f"cloud sign-in: an app claim was refused: {e}")
        return _login_redirect(str(e))
    token = new_session(user_id, via="cloud")
    resp = RedirectResponse(next_path, status_code=303, headers={"Cache-Control": "no-store"})
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
def cloud_status(request: Request):
    user_id = require_user_id(request)
    cfg = cloud_auth.settings()
    # the issuer is the portal's address too: Settings → Account & sync opens it from here;
    # ``connected`` false: the Link button waits for an admin to connect this server
    return {"identity": cloud_auth.status_of(user_id), "enabled": cfg["enabled"], "issuer": cfg["issuer"],
            "connected": not cloud_auth.needs_connect()}


@router.get("/api/auth/cloud/sync-status")
def cloud_sync_status(request: Request):
    """The caller's profile sync state (``cloud_sync.profile_status``) and
    whether a cloud identity is linked. A browser session only; no network."""
    user_id = require_personal_user_id(request, "The guest account keeps its settings in the browser.")
    identity = cloud_auth.status_of(user_id)
    linked = {"linked": True, "username": identity.get("username", "")} if identity else {"linked": False}
    return {"profile": cloud_sync.profile_status(user_id), "identity": linked}


class SyncRequest(BaseModel):
    action: Literal["sync", "merge", "fetch", "push"] = "sync"
    defaults: dict = {}  # "merge": the web app's default profile, the base of a first merge


@router.post("/api/auth/cloud/sync")
def cloud_sync_now(payload: SyncRequest, request: Request):
    """Sync the caller's profile with Gamma Cloud now: "sync" merges as the
    automatic sync does, "merge" / "fetch" / "push" also settle a first
    sync's choice. Answers the outcome and the new sync state."""
    user_id = require_personal_user_id(request, "The guest account keeps its settings in the browser.")
    if not cloud_sync.syncs(user_id):
        raise HTTPException(400, "Link a Gamma Cloud account first.")
    resolve = "" if payload.action == "sync" else payload.action
    try:
        outcome = cloud_sync.sync_profile(user_id, resolve=resolve, defaults=payload.defaults)
    except cloud_sync.NothingToFetch:
        raise HTTPException(409, "Gamma Cloud holds no settings yet.")
    status = cloud_sync.profile_status(user_id)
    if not outcome:
        raise HTTPException(502, status.get("error") or cloud_sync.UNREACHABLE)
    return {"outcome": outcome, "profile": status}


@router.post("/api/auth/cloud/unlink")
def cloud_unlink(request: Request):
    """Detach the cloud identity. An account the cloud provisioned has no
    password, so unlinking would lock it out: refused until a password is
    set."""
    user_id = require_personal_user_id(request, "unlink from a browser session")
    subject, held = cloud_auth.grant_of(user_id)
    with connect_users_db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(400, "Set a password for this account first, or it could not sign in any more.")
        if not cloud_auth.unlink(conn, user_id):
            raise HTTPException(404, "no Gamma Cloud account is linked")
        conn.commit()
    cloud_sync.release_later(subject, held)
    log.info(f"cloud sign-in: {request.state.user} unlinked")
    return {"ok": True}
