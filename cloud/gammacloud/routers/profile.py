"""What a Gamma server calls with a person's access token: the preference
profile (``/api/me/prefs``), the server list (``/api/me/servers``), the
username lookup (``/api/lookup/username``) and the e-mail invitations it
sends (``/api/invitations``, ``invitations.py``).

- The profile takes an access token with the ``prefs`` scope, or the
  portal session. A token without the scope is a 403 with
  ``WWW-Authenticate: Bearer error="insufficient_scope"``.
- The server list, the lookup and the invitations take any access token
  (every one carries ``openid``) and never the portal session: they are
  what a Gamma server does, not a person in the portal.

Writes are rate limited per account; the lookup, an enumeration surface,
per token, per account and per IP. The invitations send mail to an address
the caller chooses: per account and per recipient of that account (each an
hour and a day). Only a request that will send counts per sign-in client,
and, for the servers people connected themselves, in one daily circuit
breaker they share: refused calls cannot use it up, and the operator's
own servers (the share host, hosted ones) stay outside it. Their per-IP
limit counts only tokens that fail, since the caller is a server that may
hold many people's tokens (the share host, hosted servers behind one
address).
"""

import json
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import accounts, db, invitations, oidc, prefs, ratelimit, servers, sessions
from .accounts import send_mail

router = APIRouter(prefix="/api")

MAX_BODY = prefs.MAX_VALUE_BYTES + 4096   # the value plus the envelope


def bearer(conn, request: Request):
    """(account, token row) for the request's bearer access token; 401
    without one."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "An access token is required.", headers={"WWW-Authenticate": "Bearer"})
    found = oidc.resolve_access_token(conn, auth[7:].strip())
    if not found:
        raise HTTPException(401, "invalid or expired token",
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    return found


def prefs_account(conn, request: Request):
    """The account behind an access token with the ``prefs`` scope, or
    behind the portal session."""
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        account, row = bearer(conn, request)
        if "prefs" not in row["scope"].split():
            raise HTTPException(403, "This token was not granted the prefs scope.",
                                headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="prefs"'})
        return account
    account = sessions.resolve(conn, request)
    if not account:
        raise HTTPException(401, "not signed in")
    return account


# --- the preference profile ---------------------------------------------------

@router.get("/me/prefs")
def list_prefs(request: Request):
    with closing(db.connect()) as conn:
        account = prefs_account(conn, request)
        conn.commit()
        return {"prefs": prefs.listing(conn, account["id"])}


@router.get("/me/prefs/{key}")
def get_pref(key: str, request: Request):
    with closing(db.connect()) as conn:
        account = prefs_account(conn, request)
        conn.commit()
        found = prefs.get(conn, account["id"], prefs.check_key(key))
    if not found:
        raise HTTPException(404, "not set")
    return found


async def _json_body(request: Request, limit: int):
    """The request body as JSON, refused past ``limit`` bytes before it is
    all read."""
    try:
        declared = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        raise HTTPException(400, "bad Content-Length") from None
    if declared > limit:
        raise HTTPException(413, f"A preference value is at most {prefs.MAX_VALUE_BYTES // 1024} KB.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, f"A preference value is at most {prefs.MAX_VALUE_BYTES // 1024} KB.")
    try:
        return json.loads(bytes(body))
    except ValueError:
        raise HTTPException(400, "The body must be JSON.") from None


def _put(request: Request, key: str, body):
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(400, "The body is {value, updated_at?}.")
    with closing(db.connect()) as conn:
        account = prefs_account(conn, request)
        ratelimit.check(f"prefs-write:{account['id']}", 120, 600)
        try:
            ts = prefs.put(conn, account["id"], key, body["value"], body.get("updated_at"))
        except prefs.Conflict as e:
            conn.commit()
            return JSONResponse({"detail": "A newer value is stored.", **e.stored}, status_code=409)
        conn.commit()
    return {"updated_at": ts}


@router.put("/me/prefs/{key}")
async def put_pref(key: str, request: Request):
    """``{value, updated_at?}``: store; ``updated_at`` older than the stored
    one is a 409 carrying the stored ``{value, updated_at}``."""
    prefs.check_key(key)
    body = await _json_body(request, MAX_BODY)
    return await run_in_threadpool(_put, request, key, body)


@router.delete("/me/prefs/{key}")
def delete_pref(key: str, request: Request):
    with closing(db.connect()) as conn:
        account = prefs_account(conn, request)
        ratelimit.check(f"prefs-write:{account['id']}", 120, 600)
        removed = prefs.delete(conn, account["id"], key)
        conn.commit()
    return {"ok": True, "removed": removed}


# --- the server list ----------------------------------------------------------

class ServerBody(BaseModel):
    url: str
    name: str = ""
    version: str | None = None
    server_schema: int | None = Field(None, alias="schema", ge=0, le=1_000_000)


class ServerRef(BaseModel):
    url: str


def _server_url(conn, row, raw: str) -> str:
    """The normalized address, if the token's client may name it."""
    url = servers.norm_url(raw)
    client = oidc.get_client(conn, row["client_id"])
    if not client or not servers.allowed_for(client, url):
        raise HTTPException(403, "That address is not one this server's sign-in client is registered for.")
    return url


@router.post("/me/servers")
def link_server(body: ServerBody, request: Request):
    """A Gamma server registering itself under the person who linked their
    identity there (or refreshing its name, ``last_seen_at``, and the
    ``version`` and ``schema`` it reports when it sends them)."""
    with closing(db.connect()) as conn:
        account, row = bearer(conn, request)
        ratelimit.check(f"servers-write:{account['id']}", 60, 3600)
        url = _server_url(conn, row, body.url)
        server = servers.link(conn, account["id"], url, servers.norm_name(body.name, url), row["grant_id"],
                              servers.norm_version(body.version), body.server_schema)
        conn.commit()
    return {"server": server}


@router.delete("/me/servers")
def unlink_server(request: Request, body: ServerRef | None = None, url: str = ""):
    """``{url}`` in the body (or ``?url=``): the server unlinked the
    identity."""
    with closing(db.connect()) as conn:
        account, row = bearer(conn, request)
        ratelimit.check(f"servers-write:{account['id']}", 60, 3600)
        removed = servers.unlink(conn, account["id"], _server_url(conn, row, body.url if body else url))
        conn.commit()
    return {"ok": True, "removed": removed}


# --- username lookup ----------------------------------------------------------

@router.get("/lookup/username")
def lookup_username(request: Request, u: str = ""):
    """``{sub, username}`` of a verified, live account with exactly this
    username; 404 otherwise. For inviting a person by cloud username before
    they ever signed in on that server."""
    ratelimit.check(f"lookup:ip:{ratelimit.limit_ip(request)}", 30, 600)
    with closing(db.connect()) as conn:
        account, row = bearer(conn, request)
        conn.commit()
        ratelimit.check(f"lookup:token:{row['token_hash']}", 20, 600)
        ratelimit.check(f"lookup:account:{account['id']}", 60, 3600)
        name = (u or "").strip().lower()
        found = accounts.by_username(conn, name) if accounts.USERNAME_RE.match(name) else None
    if not found or not found["email_verified_at"]:
        raise HTTPException(404, "no such account")
    return {"sub": found["id"], "username": found["username"]}


# --- e-mail invitations -------------------------------------------------------

class InvitationBody(BaseModel):
    email: str
    server_url: str
    server_name: str = ""
    kind: str
    what: str = ""
    invite_id: str
    next: str = "/"


class InvitationRef(BaseModel):
    server_url: str
    invite_id: str


def _inviting_server(conn, row, raw: str) -> tuple[dict, str]:
    """The token's client and the server it may send invitations for:
    ``_server_url``'s rule, but never through the desktop client or at a
    loopback address, since the mailed link has to reach the server from
    anywhere."""
    client = oidc.get_client(conn, row["client_id"])
    if client and client["kind"] == "desktop":
        raise HTTPException(403, "A server signing in through the desktop app cannot send invitations: the link "
                                 "has to reach it from anywhere.")
    url = _server_url(conn, row, raw)
    if servers.is_local(url):
        raise HTTPException(403, "That address is not one this server's sign-in client is registered for.")
    return client, url


@router.post("/invitations")
def send_invitation(body: InvitationBody, request: Request):
    """A Gamma server mails an invitation to an address: ``{ok,
    expires_at}``, the same whether the address has an account here.
    The same ``invite_id`` again, through the same client, replaces the
    invitation and its link (409 through another). A mail that cannot be
    sent answers 503 and leaves things as they were: a first invitation is
    gone, a replaced one works again."""
    with closing(db.connect()) as conn:
        try:
            account, row = bearer(conn, request)
        except HTTPException:
            ratelimit.check(f"invitations:ip:{ratelimit.limit_ip(request)}", 60, 3600)
            raise
        ratelimit.check(f"invitations:account:{account['id']}", 30, 3600)
        ratelimit.check(f"invitations:day:{account['id']}", 100, 86400)
        client, url = _inviting_server(conn, row, body.server_url)
        fields = invitations.check(email=body.email, kind=body.kind, what=body.what, invite_id=body.invite_id,
                                   next_path=body.next)
        # One inbox, from one inviter: no limit across inviters, which would
        # let anyone keep a person from being invited at all.
        to = f"{accounts.email_canon(fields['email'])}:{account['id']}"
        ratelimit.check(f"invitations:to:{to}", 5, 3600)
        ratelimit.check(f"invitations:to-day:{to}", 20, 86400)
        ratelimit.check(f"invitations:client:{client['client_id']}", 600, 3600)
        if invitations.self_connected(client):
            ratelimit.check("invitations:connected", 3000, 86400)
        sent, replaced, token, has_account = invitations.send(conn, account, client, url,
                                                              servers.norm_name(body.server_name, url), **fields)
        conn.commit()
    try:
        send_mail(sent["email"], *invitations.invitation_mail(account, sent, token, has_account))
    except HTTPException:
        with closing(db.connect()) as conn:
            invitations.take_back(conn, sent, replaced)
            conn.commit()
        raise
    return {"ok": True, "expires_at": sent["expires_at"]}


@router.post("/invitations/cancel")
def cancel_invitation(body: InvitationRef, request: Request):
    """The Gamma server withdrew an invitation: its link stops working, and
    a code nobody registered with goes with it. ``removed`` is False when
    there was none; 409 when another client of the server sent it."""
    with closing(db.connect()) as conn:
        account, row = bearer(conn, request)
        ratelimit.check(f"invitations-cancel:{account['id']}", 120, 3600)
        client, url = _inviting_server(conn, row, body.server_url)
        removed = invitations.cancel(conn, client, url, body.invite_id, account["id"])
        conn.commit()
    return {"ok": True, "removed": removed}
