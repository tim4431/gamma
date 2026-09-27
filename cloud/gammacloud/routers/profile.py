"""What a Gamma server calls with a person's access token: the preference
profile (``/api/me/prefs``), the server list (``/api/me/servers``) and the
username lookup (``/api/lookup/username``).

- The profile takes an access token with the ``prefs`` scope, or the
  portal session. A token without the scope is a 403 with
  ``WWW-Authenticate: Bearer error="insufficient_scope"``.
- The server list and the lookup take any access token (every one carries
  ``openid``) and never the portal session: they are what a Gamma server
  does, not a person in the portal.

Writes are rate limited per account; the lookup, an enumeration surface,
per token, per account and per IP.
"""

import json
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import accounts, db, oidc, prefs, ratelimit, servers, sessions

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
    identity there (or refreshing its name and ``last_seen_at``)."""
    with closing(db.connect()) as conn:
        account, row = bearer(conn, request)
        ratelimit.check(f"servers-write:{account['id']}", 60, 3600)
        url = _server_url(conn, row, body.url)
        server = servers.link(conn, account["id"], url, servers.norm_name(body.name, url), row["grant_id"])
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
    ratelimit.check(f"lookup:ip:{ratelimit.client_ip(request)}", 30, 600)
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
