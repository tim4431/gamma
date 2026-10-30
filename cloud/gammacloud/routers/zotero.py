"""The Zotero broker's routes (``zotero.py``), each a 404 while the broker
has no Zotero app:

- ``GET /zotero/connect?server=&state=&code_challenge=``: straight on to
  Zotero's authorize page, whose consent already names the server the key
  is for, or a page saying what is wrong with the request;
- ``GET /zotero/callback``: Zotero sends the browser back; on to the server
  with a one-time code, or with ``error=access_denied``;
- ``POST /api/zotero/connect/token`` (form: ``code``, ``code_verifier``,
  ``server``): the server itself, from anywhere, without a cookie; Zotero
  makes the key now and the answer is ``{api_key, user_id, username}``.
"""

from contextlib import closing, suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .. import db, pages, ratelimit, zotero
from ..accounts import Problem
from ..servers import is_local

router = APIRouter()
PER_IP = (30, 600)   # each route: hits per address per 10 minutes
# The keys one server may receive an hour. Counted once Zotero made the key:
# a visit or a callback with a made-up verifier costs an anonymous client
# nothing, and counting it would let anyone lock a server's people out.
# Loopback servers and those that run Gamma Cloud's code (``zotero.ours``)
# are not capped.
PER_SERVER_HOURLY = 30
SERVER_BUSY = ("{host} has received as many Zotero connections through Gamma Cloud as it may in an hour. "
               "Try again later, or paste a Zotero API key in Gamma: Settings → Account & sync.")


def _page(title: str, message: str, status: int):
    return HTMLResponse(pages.error_page(title, message), status_code=status, headers=pages.NO_STORE)


def _unavailable():
    return _page("Zotero is not available here",
                 "This account server does not connect Zotero accounts. Paste a Zotero API key in Gamma instead.", 404)


def _go(url: str):
    return RedirectResponse(url, status_code=302, headers=pages.NO_STORE)


def _limited(request: Request, route: str):
    """The per-IP limit of a browser page, refused as the broker's own page
    (429, with Retry-After); None while under it."""
    try:
        ratelimit.check(f"zotero-{route}:ip:{ratelimit.limit_ip(request)}", *PER_IP)
    except HTTPException as e:
        return HTMLResponse(pages.error_page("Cannot connect Zotero", str(e.detail)), status_code=429,
                            headers={**pages.NO_STORE, **(e.headers or {})})
    return None


@router.get("/zotero/connect", response_class=HTMLResponse)
def zotero_connect(request: Request, server: str = "", state: str = "", code_challenge: str = ""):
    if not zotero.enabled():
        return _unavailable()
    if refused := _limited(request, "connect"):
        return refused
    try:
        with closing(db.connect()) as conn:
            origin = zotero.check(conn, server, state, code_challenge)
        token, secret = zotero.request_token()   # no transaction open while zotero.org answers
    except Problem as e:
        return _page("Cannot connect Zotero", e.detail, e.status)
    with closing(db.connect()) as conn:
        url = zotero.start(conn, token, secret, origin, state, code_challenge)
        conn.commit()
    return _go(url)


@router.get("/zotero/callback", response_class=HTMLResponse)
def zotero_callback(request: Request, oauth_token: str = "", oauth_verifier: str = "", denied: str = ""):
    if not zotero.enabled():
        return _unavailable()
    if refused := _limited(request, "callback"):
        return refused
    token = oauth_token or denied
    with closing(db.connect()) as conn:
        flow = zotero.take(conn, token)
        # Zotero's verifier or a made-up one: only trading it at zotero.org tells
        url = zotero.issue(conn, flow, token, oauth_verifier) if flow and oauth_verifier else ""
        conn.commit()   # the request is spent either way
    if not flow:
        return _page("Cannot connect Zotero",
                     "This Zotero connection expired or was already used. Start again from Gamma.", 400)
    return _go(url or zotero.return_url(flow["server"], {"error": "access_denied", "state": flow["state"]}))


def _token(form) -> JSONResponse:
    with closing(db.connect()) as conn:
        try:
            approval = zotero.redeem(conn, str(form.get("code", "")), str(form.get("code_verifier", "")),
                                     str(form.get("server", "")))
        except Problem:
            conn.commit()  # a code is spent by its first try, right or wrong
            raise
        conn.commit()
        server = approval["server"]
        capped = not is_local(server) and not zotero.ours(conn, server)
    limit = f"zotero-key:server:{server}"
    retry = ratelimit.full(limit, PER_SERVER_HOURLY, 3600) if capped else 0
    if retry:   # refused before Zotero makes a key
        raise HTTPException(429, SERVER_BUSY.format(host=urlsplit(server).hostname),
                            headers={"Retry-After": str(retry)})
    # no transaction open while zotero.org answers; its refusal is a 502 and counts for nothing
    key = zotero.access_token(approval["token"], approval["secret"], approval["verifier"])
    if capped:
        with suppress(HTTPException):   # counted now the key exists, which goes out even past the limit
            ratelimit.check(limit, PER_SERVER_HOURLY, 3600)
    with closing(db.connect()) as conn:
        db.audit(conn, "zotero.connect", actor=f"zotero:{key['user_id']}", detail=server)
        conn.commit()
    return JSONResponse(key, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/api/zotero/connect/token")
async def zotero_token(request: Request):
    if not zotero.enabled():
        raise HTTPException(404, "Zotero is not available here.")
    ratelimit.check(f"zotero-token:ip:{ratelimit.limit_ip(request)}", *PER_IP)
    if int(request.headers.get("content-length", "0") or 0) > 4096:
        raise HTTPException(413)
    form = await request.form()
    return await run_in_threadpool(_token, form)
