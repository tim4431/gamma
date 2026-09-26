"""Connecting a self-hosted Gamma server (``connect.py``): the approval
page a server's admin is sent to, its Connect button, and the endpoint the
server fetches its client from.

- ``GET /connect-server?server=&state=&code_challenge=``: signed out goes
  to the login page and comes back; an unconfirmed e-mail sees the verify
  notice; otherwise the confirm card.
- ``POST /connect-server/continue``: the person approved; answers
  ``{redirect}`` back to the server with a one-time code.
- ``POST /api/servers/connect/token`` (form: ``code``, ``code_verifier``,
  ``server``): called by the server itself, from anywhere, without a
  cookie; answers ``{client_id, client_secret}``.
"""

from contextlib import closing
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import connect, db, pages, ratelimit, sessions
from ..accounts import Problem

router = APIRouter()


@router.get("/connect-server", response_class=HTMLResponse)
def connect_page(request: Request, server: str = "", state: str = "", code_challenge: str = ""):
    ratelimit.check(f"connect:ip:{ratelimit.client_ip(request)}", 60, 600)
    try:
        origin = connect.check(server, state, code_challenge)
    except Problem as e:
        return HTMLResponse(pages.error_page("Cannot connect this server", e.detail), status_code=400)
    with closing(db.connect()) as conn:
        account = sessions.resolve(conn, request)
        conn.commit()
    if not account:
        here = "/connect-server?" + urlencode({"server": server, "state": state, "code_challenge": code_challenge})
        return RedirectResponse("/login?" + urlencode({"next": here}), status_code=302)
    return HTMLResponse(pages.connect_page(origin, state, code_challenge, account,
                                           verify_needed=not account["email_verified_at"]), headers=pages.NO_STORE)


class ConnectBody(BaseModel):
    server: str
    state: str
    code_challenge: str


@router.post("/connect-server/continue")
def connect_continue(body: ConnectBody, request: Request):
    with closing(db.connect()) as conn:
        account = sessions.resolve(conn, request)
        if not account:
            raise HTTPException(401, "not signed in")
        if not account["email_verified_at"]:
            raise HTTPException(403, "Confirm your e-mail address first.")
        ratelimit.check(f"connect:account:{account['id']}", 20, 3600)
        origin = connect.check(body.server, body.state, body.code_challenge)
        redirect = connect.approve(conn, account, origin, body.state, body.code_challenge)
        conn.commit()
    return {"redirect": redirect}


def _token(form):
    with closing(db.connect()) as conn:
        try:
            out = connect.exchange(conn, str(form.get("code", "")), str(form.get("code_verifier", "")),
                                   str(form.get("server", "")))
        except Problem:
            conn.commit()  # a code is spent by its first try, right or wrong
            raise
        conn.commit()
    return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/api/servers/connect/token")
async def connect_token(request: Request):
    ratelimit.check(f"connect-token:ip:{ratelimit.client_ip(request)}", 30, 600)
    if int(request.headers.get("content-length", "0") or 0) > 4096:
        raise HTTPException(413)
    form = await request.form()
    return await run_in_threadpool(_token, form)
