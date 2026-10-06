"""The OpenID Connect endpoints: discovery, JWKS, authorize (an HTML page
that signs the person in, or lets a signed-in person continue), token,
userinfo, revoke. The logic is in ``oidc.py``; this is the wire.

An account whose e-mail is not verified cannot sign in to a Gamma server:
the authorize page shows the verify notice instead of issuing a code. That
is the one gate a hosted Gamma relies on.

``/token`` and ``/revoke`` read the form on the event loop and do the rest
in the threadpool: a request waiting for cloud.db's write lock must never
hold up the whole server.
"""

import base64
import re
import sqlite3
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import accounts, db, entrance, oidc, pages, ratelimit, sessions
from ..oidc import OAuthError
from .external import sign_in_page

router = APIRouter()
EXPIRED = "This sign-in request expired. Start again from the app."


def _no_store(payload, status=200):
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _oauth_error(e: OAuthError):
    return _no_store({"error": e.code, "error_description": e.description}, e.status if e.status != 302 else 400)


@router.get("/.well-known/openid-configuration")
def discovery():
    return oidc.discovery()


@router.get("/jwks")
def jwks():
    with closing(db.connect()) as conn:
        oidc.ensure_signing_key(conn)
        conn.commit()
        return JSONResponse(oidc.jwks(conn), headers={"Cache-Control": "public, max-age=300"})


# --- authorize ----------------------------------------------------------------

@router.get("/authorize")
def authorize(request: Request):
    ratelimit.check(f"authorize:ip:{ratelimit.limit_ip(request)}", 60, 600)
    if len(str(request.url)) > 8192:
        raise HTTPException(414)
    params = dict(request.query_params)
    with closing(db.connect()) as conn:
        try:
            req = oidc.begin(conn, params)
        except OAuthError as e:
            if e.status == 302:
                extra = {"error": e.code, "error_description": e.description}
                if params.get("state"):
                    extra["state"] = params["state"]
                return RedirectResponse(oidc.redirect_with(params["redirect_uri"], extra), status_code=302)
            return HTMLResponse(pages.error_page("Cannot sign in", e.description), status_code=400)
        account = sessions.resolve(conn, request)
        step = _step(conn, req, account)
        conn.commit()
    return _answer(request, req, account, step)


def _step(conn, req: dict, account) -> tuple[str, object]:
    """What the authorize step does for whoever is signed in, with its
    writes made: ``signin`` and ``verify`` (nobody, or an unconfirmed
    address), ``go`` with the address to send the browser to (the request
    finished, or the person forwarded to a server of their own), and
    ``choose`` / ``upgrade`` / ``confirm`` from ``entrance.decide``."""
    if not account:
        return "signin", None
    if not account["email_verified_at"]:
        return "verify", None
    what, value = entrance.decide(conn, req, account)
    if what == "finish":
        return "go", oidc.finish(conn, req, account)
    if what == "forward":
        oidc.drop(conn, req)
        return "go", value
    return what, value


def _answer(request: Request, req: dict, account, step: tuple[str, object]):
    what, value = step
    if what == "signin":
        return sign_in_page(request, lambda social: pages.authorize_page(req, None, social=social),
                            request_id=req["id"])
    if what == "go":
        return RedirectResponse(value, status_code=302, headers=pages.NO_STORE)
    if what == "choose":
        return HTMLResponse(pages.where_page(account, value, req["id"]), headers=pages.NO_STORE)
    if what == "upgrade":
        return HTMLResponse(pages.no_library_page(account, value), headers=pages.NO_STORE)
    return HTMLResponse(pages.authorize_page(req, account, verify_needed=what == "verify"), headers=pages.NO_STORE)


@router.get("/authorize/resume")
def authorize_resume(request: Request, request_id: str = ""):
    """The authorize page again for a request still pending — where a
    Google/GitHub sign-in started there comes back when it cannot finish
    on its own (cancelled, or the account's e-mail is unconfirmed)."""
    with closing(db.connect()) as conn:
        req = oidc.pending(conn, request_id)
        account = sessions.resolve(conn, request)
        step = _step(conn, req, account) if req else None
        conn.commit()
    if not req:
        return HTMLResponse(pages.error_page("Cannot sign in", EXPIRED), status_code=400)
    return _answer(request, req, account, step)


class AuthorizeLogin(BaseModel):
    request_id: str
    login: str
    password: str


@router.post("/authorize/login")
def authorize_login(body: AuthorizeLogin, request: Request):
    """Sign in on the authorize page; answers ``{redirect}`` for the page to
    follow: the server that asked, or where ``entrance.after_sign_in`` sends
    the person instead. Also sets the portal cookie, so the next server's
    sign-in is one click."""
    ip_key = f"login:ip:{ratelimit.limit_ip(request)}"
    who_key = f"login:who:{accounts.login_bucket(body.login)}"  # one window with /api/login
    ratelimit.check(ip_key, 10, 300)
    ratelimit.check(who_key, 10, 300)
    with closing(db.connect()) as conn:
        req = oidc.pending(conn, body.request_id)
        if not req:
            raise HTTPException(400, EXPIRED)
        account = accounts.by_login(conn, body.login)
        if not accounts.password_ok(account, body.password):
            raise HTTPException(401, "Wrong e-mail, username or password.")
        token = sessions.create(conn, account["id"], request)
        if not account["email_verified_at"]:
            conn.commit()
            resp = JSONResponse({"verify_needed": True, "account": accounts.public(account)})
            sessions.set_cookie(resp, token)
            return resp
        redirect = entrance.after_sign_in(conn, req, account)
        conn.commit()
    ratelimit.reset(ip_key)
    ratelimit.reset(who_key)
    resp = JSONResponse({"redirect": redirect})
    sessions.set_cookie(resp, token)
    return resp


class AuthorizeContinue(BaseModel):
    request_id: str


@router.post("/authorize/continue")
def authorize_continue(body: AuthorizeContinue, request: Request):
    """The signed-in person confirmed the server that is asking (the
    confirm card's Continue, or the shared server on the chooser). The
    shared server is refused for an account it does not take
    (``oidc.admits``), which no page offers."""
    with closing(db.connect()) as conn:
        req = oidc.pending(conn, body.request_id)
        if not req:
            raise HTTPException(400, EXPIRED)
        account = sessions.resolve(conn, request)
        if not account:
            raise HTTPException(401, "not signed in")
        if not account["email_verified_at"]:
            raise HTTPException(403, "Confirm your e-mail address first.")
        if not oidc.admits(req["client"], account):
            raise HTTPException(403, "Your plan has no online library. See the plans on your account's Plan page.")
        redirect = oidc.finish(conn, req, account)
        conn.commit()
    return {"redirect": redirect}


@router.post("/authorize/cancel")
def authorize_cancel(body: AuthorizeContinue):
    with closing(db.connect()) as conn:
        req = oidc.pending(conn, body.request_id)
        if not req:
            return {"redirect": ""}
        conn.execute("DELETE FROM oauth_requests WHERE id = ?", (req["id"],))
        conn.commit()
    extra = {"error": "access_denied"}
    if req["state"]:
        extra["state"] = req["state"]
    return {"redirect": oidc.redirect_with(req["redirect_uri"], extra)}


# --- token / userinfo / revoke ------------------------------------------------

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _client_from(request: Request, form) -> tuple[str, str | None]:
    """client_id + secret from the body (client_secret_post) or the
    Authorization header (client_secret_basic)."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            raw = base64.b64decode(auth[6:]).decode()
            client_id, _, secret = raw.partition(":")
            return client_id, secret
        except (ValueError, UnicodeDecodeError):
            raise OAuthError("invalid_client", "bad basic auth", 401)
    return str(form.get("client_id", "")), (str(form["client_secret"]) if form.get("client_secret") else None)


def _device(form) -> tuple[str, str]:
    """The (per-install id, name) a client sends with its code; anything
    malformed is dropped rather than refused."""
    device_id = str(form.get("device_id", ""))
    name = " ".join("".join(c for c in str(form.get("device_name", "")) if c.isprintable()).split())[:64]
    return (device_id if DEVICE_ID_RE.match(device_id) else "", name)


def _busy():
    return _no_store({"error": "temporarily_unavailable", "error_description": "The server is busy. Try again."}, 503)


def _token(request: Request, form):
    grant_type = str(form.get("grant_type", ""))
    with closing(db.connect()) as conn:
        try:
            client_id, secret = _client_from(request, form)
            client = oidc.authenticate_client(conn, client_id, secret)
            if grant_type == "authorization_code":
                out = oidc.exchange_code(conn, client, str(form.get("code", "")), str(form.get("redirect_uri", "")),
                                         str(form.get("code_verifier", "")), request, _device(form))
            elif grant_type == "refresh_token":
                out = oidc.refresh_grant(conn, client, str(form.get("refresh_token", "")), request)
            else:
                raise OAuthError("unsupported_grant_type")
        except OAuthError as e:
            conn.commit()  # a replayed code's or a reused token's revocation must land
            return _oauth_error(e)
        except sqlite3.OperationalError as e:
            if db.is_busy(e):
                return _busy()
            raise
        conn.commit()
    return _no_store(out)


@router.post("/token")
async def token(request: Request):
    ratelimit.check(f"token:ip:{ratelimit.limit_ip(request)}", 120, 600)
    if int(request.headers.get("content-length", "0") or 0) > 16384:
        raise HTTPException(413)
    form = await request.form()
    return await run_in_threadpool(_token, request, form)


@router.get("/userinfo")
def userinfo(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return _no_store({"error": "invalid_token"}, 401)
    with closing(db.connect()) as conn:
        found = oidc.resolve_access_token(conn, auth[7:].strip())
        conn.commit()
        if not found:
            return _no_store({"error": "invalid_token"}, 401)
        account, row = found
        return _no_store({"sub": account["id"], **oidc.claims_for(account, row["scope"])})


def _revoke(request: Request, form):
    with closing(db.connect()) as conn:
        try:
            client_id, secret = _client_from(request, form)
            client = oidc.authenticate_client(conn, client_id, secret)
        except OAuthError as e:
            return _oauth_error(e)
        try:
            oidc.revoke(conn, client, str(form.get("token", "")))
            conn.commit()
        except sqlite3.OperationalError as e:
            if db.is_busy(e):
                return _busy()
            raise
    return _no_store({})


@router.post("/revoke")
async def revoke(request: Request):
    if int(request.headers.get("content-length", "0") or 0) > 16384:
        raise HTTPException(413)
    form = await request.form()
    return await run_in_threadpool(_revoke, request, form)
