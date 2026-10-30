"""The Zotero account connection (docs/dev/zotero_sync.md), the wire
around gamma/zotero_account.py:

- ``GET /api/zotero/account``: the account's connection (``status``, never
  the key), ``can_edit`` (false for a guest) and ``oauth: {available,
  mode}``, how Settings may offer signing in at zotero.org;
- ``PUT /api/zotero/account`` ``{api_key}``: connect a pasted key, checked
  with Zotero first; ``DELETE``: forget it (a key Gamma had Zotero make is
  revoked there too);
- ``GET /api/zotero/libraries``: My Library and the groups the key
  reaches, with what it may do in each, for the link dialog;
- ``POST /api/zotero/oauth/start`` → ``{state, url}``: the page opens
  ``url`` and polls ``POST /api/zotero/oauth/status`` ``{state}``;
  ``GET /api/zotero/oauth/callback`` ends the round trip with a small page
  that says how it went or asks first (``POST /api/zotero/oauth/confirm``).

Signing in has two modes: ``server``, this server's own Zotero app
(``config.zotero_client``) running zotero.org's OAuth 1.0a steps
(gamma/zotero_oauth.py); ``cloud``, Gamma Cloud's broker running them with
its app (``cloud_auth.zotero_broker``) and sending the browser back with a
one-time code this server trades, with its PKCE verifier and its address,
for the key. Without either, a key is pasted. The round trip comes back to
``oauth_base``: the admin-confirmed public URL, else a loopback request's
own origin; anywhere else signing in is not offered, since an origin read
from an unchecked Host header could send the approval anywhere.

The callback needs no session (a desktop app's system browser has none),
so it connects the account that started only in a browser shown to be
that person's; otherwise whoever started could pass the approval link to
someone else and receive their key (OAuth 1.0's session fixation). A
browser signed in to another account is refused; on the desktop app's own
server (``_own_computer``) a round trip started from this machine on a
loopback base connects; any other start sets a cookie (``COOKIE``, a nonce
whose sha256 the pending record keeps) and the browser carrying it
connects; any other browser gets a page naming the Gamma account and
server that would receive the key, whose Connect button posts a one-time
token. A session alone proves nothing: a login CSRF can plant one. Nor does
a loopback peer of any other server: behind an SSH tunnel or a port forward
every person's requests come from loopback. The outcome is kept for the
status, which answers only the account that started.
"""

import hashlib
import hmac
import html
import ipaddress
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import cloud_auth, config, mcp_oauth, ratelimit, zotero_account, zotero_oauth, zotero_sync
from ..auth import _is_https, require_personal_user_id, require_user_id
from ..chatgpt_oauth import _b64url
from ..cloud_auth import CloudAuthError
from ..db import account_name, connect_users_db
from ..logbuf import log
from ..server_settings import LOOPBACK_HOSTS, public_url_settings
from ..zotero_account import AccountError
from ..zotero_web import ZoteroClient, ZoteroError

router = APIRouter(prefix="/api", tags=["zotero"])

GUESTS_REFUSED = "Guest accounts cannot connect a Zotero account."
NO_SIGN_IN = "This server cannot connect Zotero by signing in. Use an API key instead."
EXPIRED = "This connection expired or was already used. Start again in Gamma: Settings → Account & sync."
CANCELLED = "You cancelled connecting Zotero."
ACCOUNT_GONE = "The Gamma account that started connecting Zotero no longer exists."
OTHER_ACCOUNT = ("Zotero was not connected: this browser is signed in to a different Gamma account than the one "
                 "that started connecting. Start again in Gamma: Settings → Account & sync.")
CALLBACK_PATH = "/api/zotero/oauth/callback"
CONFIRM_PATH = "/api/zotero/oauth/confirm"
COOKIE = "gamma_zotero_oauth"  # the browser's half of the binding (module doc)
# The round trip's records in the pending store (gamma/mcp_oauth.py), keyed
# by its state: its secrets, which the callback uses up; who started it,
# which the status reads while the callback works (so it outlives the
# secrets by the longest callback); and how it ended. A confirmation page
# keeps the secrets and the callback's answer under a one-time token.
PENDING_KIND, STARTED_KIND, RESULT_KIND = "zotero_connect", "zotero_started", "zotero_result"
CONFIRM_KIND = "zotero_confirm"
PENDING_TTL = 600
STARTED_TTL = PENDING_TTL + 120
RESULT_TTL = 600
# Shorter than the broker's code (cloud/gammacloud config.ZOTERO_CODE_TTL,
# 300 s), so a Connect click still finds the code unredeemed.
CONFIRM_TTL = 240
# A failed look at Gamma Cloud's broker is not repeated for this long: the
# account answer, which Settings asks for on opening, does not wait on an
# unreachable account server every time.
PROBE_RETRY = 60
_probe_failed: dict[str, float] = {}  # issuer -> time.monotonic() before which it is not asked again


def oauth_base(request: Request) -> str:
    """Where zotero.org or the broker sends the browser back: the confirmed
    public URL, else a loopback request's own origin; "" when neither (the
    module doc says why)."""
    configured = public_url_settings()["public_url"]
    if configured:
        return configured
    origin = str(request.base_url).rstrip("/")
    return origin if _loopback(origin) else ""


def _loopback(base: str) -> bool:
    return urlsplit(base).hostname in LOOPBACK_HOSTS


def _own_computer() -> bool:
    """Whether this server is the desktop app's own, run as the person on
    their computer (``config.folder_links_enabled``: only the app's sidecar
    sets it). Only there does a loopback peer stand for the person whose
    browser it is."""
    return config.folder_links_enabled()


def _same_machine(request: Request) -> bool:
    """Whether the request's connection comes from this machine: a
    loopback peer (``ratelimit.client_ip``), which, unlike the Host header,
    the client does not choose."""
    try:
        ip = ipaddress.ip_address(ratelimit.client_ip(request))
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def _broker_takes(base: str) -> bool:
    """Whether Gamma Cloud's broker sends a browser back to ``base``: https,
    or plain HTTP on loopback (its ``servers.norm_url``), of a server it
    knows (a public one is connected first)."""
    return (urlsplit(base).scheme == "https" or _loopback(base)) and not cloud_auth.needs_connect()


def oauth_mode(request: Request) -> str | None:
    """How this server connects a Zotero account by signing in, for a page
    at ``request``'s address: "server" with its own Zotero app, "cloud"
    through Gamma Cloud's broker, None when a key must be pasted (no
    ``oauth_base``, a base the broker refuses, or an account server that
    cannot be reached)."""
    base = oauth_base(request)
    if not base:
        return None
    if config.zotero_client()[0]:
        return "server"
    issuer = cloud_auth.settings()["issuer"]
    if not issuer or not _broker_takes(base) or _probe_failed.get(issuer, 0.0) > time.monotonic():
        return None
    try:
        cloud_auth.zotero_broker()
    except CloudAuthError:
        _probe_failed[issuer] = time.monotonic() + PROBE_RETRY
        return None
    return "cloud"


def _account(user_id: str, request: Request) -> dict:
    """The account answer of a personal account."""
    mode = oauth_mode(request)
    return {**zotero_account.status(user_id), "can_edit": True,
            "oauth": {"available": mode is not None, "mode": mode}}


@router.get("/zotero/account")
def get_account(request: Request):
    user_id = require_user_id(request)
    if request.state.is_guest:
        return {"connected": False, "can_edit": False, "oauth": {"available": False, "mode": None}}
    return _account(user_id, request)


class KeyBody(BaseModel):
    api_key: str = Field(max_length=256)


def _refused(e: AccountError) -> HTTPException:
    return HTTPException(e.status, str(e))


@router.put("/zotero/account")
def connect_key(payload: KeyBody, request: Request):
    """Connect a key the person pasted (400 when Zotero refuses it or it
    cannot read their library, 502 when Zotero cannot be asked)."""
    user_id = require_personal_user_id(request, GUESTS_REFUSED)
    try:
        zotero_account.connect(user_id, payload.api_key, via="key")
    except AccountError as e:
        raise _refused(e) from None
    log.info(f"[zotero] {request.state.user} connected a Zotero account with a key")
    return _account(user_id, request)


@router.delete("/zotero/account")
def disconnect(request: Request):
    user_id = require_personal_user_id(request, GUESTS_REFUSED)
    zotero_account.disconnect(user_id)
    return {"ok": True}


@router.get("/zotero/libraries")
def libraries(request: Request):
    """The libraries the connected key reaches, for the link dialog
    (``ZoteroClient.libraries``: whether the key may read and write each,
    read and store its files, see its notes); 409 when none is connected
    or Zotero no longer accepts the key (the person reconnects), 502 when
    Zotero cannot be asked."""
    user_id = require_personal_user_id(request, GUESTS_REFUSED)
    try:
        client = zotero_account.client_for(user_id)
        with zotero_sync.asking_zotero():
            return {"libraries": client.libraries()}
    except AccountError as e:
        raise _refused(e) from None


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


@router.post("/zotero/oauth/start")
def oauth_start(request: Request):
    """Begin signing in at zotero.org: the address of the approval (in
    ``server`` mode zotero.org's own, in ``cloud`` mode the broker's) and
    the state the page polls with; unless this machine starts it on a
    loopback base of the desktop app's own server, the cookie that binds
    the round trip to this browser. 400 where signing in is not offered."""
    user_id = require_personal_user_id(request, GUESTS_REFUSED)
    ratelimit.check(f"zotero-oauth-start:account:{user_id}", 20, 600)
    base = oauth_base(request)
    if not base:
        raise HTTPException(400, NO_SIGN_IN)
    state = secrets.token_urlsafe(24)
    consumer_key, consumer_secret = config.zotero_client()
    if consumer_key:
        callback = f"{base}{CALLBACK_PATH}?{urlencode({'state': state})}"
        try:
            token, token_secret = zotero_oauth.request_token(consumer_key, consumer_secret, callback)
        except ZoteroError as e:
            raise HTTPException(502, str(e)) from None
        pending = {"mode": "server", "token": token, "token_secret": token_secret}
        url = zotero_oauth.authorize_url(token, name=f"Gamma ({cloud_auth.server_name(base)})")
    elif cloud_auth.settings()["enabled"]:
        if cloud_auth.needs_connect():
            raise HTTPException(400, cloud_auth.NOT_CONNECTED)
        if not _broker_takes(base):
            raise HTTPException(400, NO_SIGN_IN)
        try:
            connect_endpoint, _ = cloud_auth.zotero_broker()
        except CloudAuthError as e:
            raise HTTPException(400 if str(e) == cloud_auth.NO_ZOTERO_BROKER else 502, str(e)) from None
        verifier = secrets.token_urlsafe(48)
        pending = {"mode": "cloud", "verifier": verifier}
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        url = connect_endpoint + "?" + urlencode({"server": base, "state": state, "code_challenge": challenge})
    else:
        raise HTTPException(400, NO_SIGN_IN)
    response = JSONResponse({"state": state, "url": url}, headers={"Cache-Control": "no-store"})
    if not (_own_computer() and _loopback(base) and _same_machine(request)):
        nonce = secrets.token_urlsafe(32)
        pending["browser"] = _digest(nonce)
        response.set_cookie(COOKIE, nonce, max_age=PENDING_TTL, path=CALLBACK_PATH, secure=_is_https(request),
                            httponly=True, samesite="lax")
    mcp_oauth.store(PENDING_KIND, base, state, {"user_id": user_id, **pending}, PENDING_TTL)
    mcp_oauth.store(STARTED_KIND, base, state, {"user_id": user_id}, STARTED_TTL)
    return response


class StateBody(BaseModel):
    state: str = Field(max_length=128)


@router.post("/zotero/oauth/status")
def oauth_status(payload: StateBody, request: Request):
    """How a round trip this account started stands; 404 for one it did not
    start, or that expired unfinished."""
    user_id = require_personal_user_id(request, GUESTS_REFUSED)
    base = oauth_base(request)
    if payload.state and base:
        result = mcp_oauth.load(RESULT_KIND, base, payload.state)
        if result and result.get("user_id") == user_id:
            if result.get("error"):
                return {"state": "error", "error": result["error"]}
            return {"state": "connected", "account": zotero_account.status(user_id)}
        started = mcp_oauth.load(STARTED_KIND, base, payload.state)
        if started and started.get("user_id") == user_id:
            return {"state": "pending"}
    raise HTTPException(404, EXPIRED)


def _browser_account(request: Request) -> str:
    """The Gamma account this browser's session is, "" for none."""
    return (request.state.user_id or "") if request.state.auth == "session" else ""


def _cookie_matches(request: Request, pending: dict) -> bool:
    nonce, expected = request.cookies.get(COOKIE, ""), pending.get("browser", "")
    return bool(nonce and expected) and hmac.compare_digest(_digest(nonce), expected)


@router.get("/zotero/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request, state: str = "", oauth_token: str = "", oauth_verifier: str = "",
                   code: str = "", error: str = "", error_description: str = ""):
    """The end of the round trip, in the browser that made it: zotero.org
    (``server`` mode: ``oauth_token``, ``oauth_verifier``) or the broker
    (``cloud`` mode: ``code``, or ``error`` and ``error_description``)
    sends it here. Connects the account that started it when this browser
    is shown to be that person's, else refuses or asks (module doc), and
    keeps the outcome for the status."""
    ratelimit.check(f"zotero-oauth-callback:ip:{ratelimit.client_ip(request)}", 30, 600)
    base = oauth_base(request)
    pending = mcp_oauth.load(PENDING_KIND, base, state, consume=True) if base and 0 < len(state) <= 128 else None
    if not pending:
        return _page(EXPIRED)
    answer = {"oauth_token": oauth_token, "oauth_verifier": oauth_verifier, "code": code, "error": error,
              "error_description": error_description}
    who, cookie = _browser_account(request), _cookie_matches(request, pending)
    if who and who != pending["user_id"]:
        log.info("[zotero] a Zotero sign-in came back to a browser signed in to another account: refused")
        response = _conclude(state, pending, base, OTHER_ACCOUNT)
    elif error or cookie or (_own_computer() and _loopback(base) and "browser" not in pending):
        response = _conclude(state, pending, base, _finish(pending, base, **answer))  # a session alone: never
    else:
        response = _confirmation(state, pending, base, answer)
    if cookie:
        response.delete_cookie(COOKIE, path=CALLBACK_PATH, secure=_is_https(request), httponly=True, samesite="lax")
    return response


@router.post("/zotero/oauth/confirm", response_class=HTMLResponse)
def oauth_confirm(request: Request, token: str = Form("", max_length=128)):
    """The confirmation page's Connect: finishes the round trip it holds,
    once."""
    ratelimit.check(f"zotero-oauth-callback:ip:{ratelimit.client_ip(request)}", 30, 600)
    base = oauth_base(request)
    held = mcp_oauth.load(CONFIRM_KIND, base, token, consume=True) if base and token else None
    if not held:
        return _page(EXPIRED)
    state, pending = held["state"], held["pending"]
    who = _browser_account(request)
    if who and who != pending["user_id"]:
        return _conclude(state, pending, base, OTHER_ACCOUNT)
    return _conclude(state, pending, base, _finish(pending, base, **held["answer"]))


def _conclude(state: str, pending: dict, base: str, problem: str) -> HTMLResponse:
    """Keep the outcome for the status; the page that says it."""
    mcp_oauth.store(RESULT_KIND, base, state, {"user_id": pending["user_id"], "error": problem}, RESULT_TTL)
    if problem:
        return _page(problem)
    with connect_users_db() as conn:
        username = account_name(conn, pending["user_id"])
    return _page("", account=username, server=cloud_auth.server_name(base))


def _confirmation(state: str, pending: dict, base: str, answer: dict) -> HTMLResponse:
    """The page that asks before connecting: it names the Gamma account and
    the server that would hold the key, and its Connect posts the token
    that keeps the round trip (``CONFIRM_TTL``). The started record lives
    on as long, so the status keeps answering pending meanwhile."""
    with connect_users_db() as conn:
        username = account_name(conn, pending["user_id"])
    if not username:
        return _conclude(state, pending, base, ACCOUNT_GONE)
    token = secrets.token_urlsafe(32)
    mcp_oauth.store(CONFIRM_KIND, base, token, {"state": state, "pending": pending, "answer": answer}, CONFIRM_TTL)
    started = mcp_oauth.load(STARTED_KIND, base, state, consume=True) or {"user_id": pending["user_id"]}
    mcp_oauth.store(STARTED_KIND, base, state, started, CONFIRM_TTL + 120)
    text = (f"The Zotero account you just approved would be connected to the Gamma account "
            f"“{html.escape(username)}” on {html.escape(cloud_auth.server_name(base))}, which could then read "
            f"and change your Zotero library. Connect only if that Gamma account is yours. If someone sent you "
            f"the link, close this tab instead.")
    form = (f'<form method="post" action="{CONFIRM_PATH}"><input type="hidden" name="token" value="{token}">'
            f'<button type="submit">Connect</button></form>')
    return HTMLResponse(_PAGE.format(title="Connect Zotero to Gamma?", text=text, form=form, script=""),
                        headers=_PAGE_HEADERS)


def _finish(pending: dict, base: str, *, oauth_token: str, oauth_verifier: str, code: str, error: str,
            error_description: str = "") -> str:
    """Trade what the callback was handed for the API key and connect the
    account ``pending`` names with it: "" once connected, else what went
    wrong, a sentence for the page and the status."""
    if error == "access_denied":
        return CANCELLED
    if error:
        return error_description.strip()[:300] or f"Zotero was not connected ({error[:100]})."
    user_id = pending["user_id"]
    with connect_users_db() as conn:
        username = account_name(conn, user_id)
    if not username:  # deleted since: no key is made for it
        return ACCOUNT_GONE
    api_key = ""
    try:
        if pending["mode"] == "server":
            api_key = _server_key(pending, oauth_token, oauth_verifier)
        else:
            api_key = _broker_key(pending, base, code)
        zotero_account.connect(user_id, api_key, via="oauth")
    except ZoteroError as e:
        problem = str(e)
    except CloudAuthError as e:
        problem = f"Gamma Cloud could not finish connecting Zotero: {e}"
    except AccountError as e:  # zotero_account.connect refused the key
        problem = str(e)
        if e.status >= 500:  # Zotero could not check it, so it is not kept: nor left behind there
            ZoteroClient(api_key).revoke()
    else:
        log.info(f"[zotero] {username} connected a Zotero account by signing in ({pending['mode']})")
        return ""
    log.info(f"[zotero] connecting {username}'s Zotero account by signing in failed: {problem}")
    return problem


def _server_key(pending: dict, oauth_token: str, oauth_verifier: str) -> str:
    """``server`` mode: zotero.org's approval traded for the key."""
    consumer_key, consumer_secret = config.zotero_client()
    if not consumer_key:
        raise ZoteroError("This server no longer has a Zotero app: start again")
    if oauth_token != pending["token"]:
        raise ZoteroError("Zotero's answer does not belong to this connection: start again")
    if not oauth_verifier:
        raise ZoteroError("Zotero did not approve the connection: start again")
    return zotero_oauth.access_token(consumer_key, consumer_secret, pending["token"], pending["token_secret"],
                                     oauth_verifier)["api_key"]


def _broker_key(pending: dict, base: str, code: str) -> str:
    """``cloud`` mode: the broker's one-time code traded for the key, with
    the verifier and this server's address the code is bound to (the
    broker has Zotero make the key only now)."""
    if not code:
        raise CloudAuthError("it sent no code")
    _, token_endpoint = cloud_auth.zotero_broker()
    answer = cloud_auth._post_form(token_endpoint,
                                   {"server": base, "code": code, "code_verifier": pending["verifier"]})
    api_key = answer.get("api_key")
    if not isinstance(api_key, str) or not api_key:
        raise CloudAuthError("its answer holds no key")
    return api_key


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Gamma</title>
<style>
:root {{ color-scheme: light dark; --fg: #1f2328; --muted: #59636e; --bg: #ffffff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg: #e6edf3; --muted: #9198a1; --bg: #16181d; }} }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
  color: var(--fg); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 34rem; padding: 24px 16px; }}
h1 {{ font-size: 1.25rem; margin: 0 0 8px; overflow-wrap: anywhere; }}
p {{ margin: 0; color: var(--muted); overflow-wrap: anywhere; }}
form {{ margin: 16px 0 0; }}
button {{ font: inherit; padding: 6px 18px; border: 0; border-radius: 6px; background: var(--fg); color: var(--bg);
  cursor: pointer; }}
</style></head>
<body><main><h1>{title}</h1><p>{text}</p>{form}</main>{script}</body></html>
"""
# The page loads nothing, runs only its own one-line script and posts only
# to this server.
_PAGE_HEADERS = {"Cache-Control": "no-store",
                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                            "script-src 'unsafe-inline'; form-action 'self'; "
                                            "frame-ancestors 'none'"}


def _page(problem: str, *, account: str = "", server: str = "") -> HTMLResponse:
    """The callback's page: connected (naming the Gamma ``account`` on
    ``server`` that holds the key), in a tab that tries to close itself
    (browsers let a script close only a tab a script opened, so the page
    also says it may be closed), or what went wrong, which stays to be
    read."""
    if not problem:
        where = (f"Connected to the Gamma account “{html.escape(account)}” on {html.escape(server)}. "
                 if account else "")
        return HTMLResponse(_PAGE.format(title="Zotero is connected to Gamma", text=f"{where}You can close this tab.",
                                         form="", script="<script>window.close()</script>"),
                            headers=_PAGE_HEADERS)
    return HTMLResponse(_PAGE.format(title="Zotero is not connected", text=html.escape(problem), form="", script=""),
                        status_code=400, headers=_PAGE_HEADERS)
