"""What a hosted container calls with its own client credentials
(``POST /api/hosted/sync``: its report in, its limits out), and the
signed-in account's hosted server for the plan page
(``GET /api/hosted/status``). docs/dev/hosted.md."""

import base64
import hmac
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request

from .. import db, hosted, oidc, ratelimit
from .accounts import portal_account

router = APIRouter(prefix="/api/hosted")
SYNCS_PER_HOUR = 60


def _basic(request: Request) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        raise HTTPException(401, "client credentials required", headers={"WWW-Authenticate": "Basic"})
    try:
        client_id, _, secret = base64.b64decode(auth[6:], validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(401, "bad basic auth", headers={"WWW-Authenticate": "Basic"}) from None
    return client_id, secret


@router.post("/sync")
def sync(body: dict, request: Request):
    client_id, secret = _basic(request)
    with closing(db.connect()) as conn:
        client = oidc.get_client(conn, client_id)
        if (not client or not client.get("secret_hash") or not secret
                or not hmac.compare_digest(db.token_hash(secret), client["secret_hash"])):
            raise HTTPException(401, "unknown client or bad secret", headers={"WWW-Authenticate": "Basic"})
        row = conn.execute("SELECT id FROM hosted_servers WHERE client_id = ? AND state != 'deleted'",
                           (client_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "this client has no hosted server")
        ratelimit.check(f"hosted-sync:{client_id}", SYNCS_PER_HOUR, 3600)
        db.begin_write(conn)
        limits = hosted.sync(conn, row["id"], body)
        conn.commit()
    return limits


@router.get("/status")
def status(request: Request):
    """The plan page's poll: the account's server or ``{"server": null}``."""
    with closing(db.connect()) as conn:
        account = portal_account(conn, request)
        return {"server": hosted.status_for(conn, account["id"])}
