"""``POST /api/reports``: a Gamma server forwards a report of content or
of an AI reply (``reports.py``, docs/dev/cloud.md "Reports").

The call may come three ways, and the report records which:

- with the reporter's access token (``Authorization: Bearer``), from a
  server where they signed in with Gamma Cloud: the account is known;
- with the server's own client id and secret (``Authorization: Basic``),
  from a confidential client, for a reporter with no linked account there
  (a link visitor on the share host): the server is known;
- with neither, from a server that signs no one in with Gamma Cloud or a
  desktop or app server whose reporter has no linked account.

A token or secret that fails is a 401, counted against the caller's IP. A
call with neither is held to a tight per-IP limit; a token's to its
account's, a secret's to its client's, since a server forwards everyone's
reports through one address.
"""

from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import alerts, db, oidc, ratelimit, reports, servers
from .profile import bearer

router = APIRouter(prefix="/api")


class ReportBody(BaseModel):
    report_id: str
    server_url: str = ""
    server_name: str = ""
    kind: str
    reason: str
    details: str = ""
    content: str = ""
    author: str = ""
    title: str = ""
    page_id: str = ""
    comment_id: str = ""
    link: str = ""
    reporter: str = ""


def _caller(conn, request: Request) -> tuple[str, str]:
    """(account id, client id) the call proves, '' for what it does not."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        account, row = bearer(conn, request)
        return account["id"], row["client_id"]
    if auth.lower().startswith("basic "):
        try:
            client = oidc.basic_client(conn, auth)
        except oidc.OAuthError:
            raise HTTPException(401, "The client id or secret is wrong.",
                                headers={"WWW-Authenticate": "Basic"}) from None
        if client["secret_hash"] is None:  # a public client proves nothing
            return "", ""
        return "", client["client_id"]
    return "", ""


def _receive(body: ReportBody, request: Request) -> tuple[dict, list]:
    with closing(db.connect()) as conn:
        try:
            account_id, client_id = _caller(conn, request)
        except HTTPException:
            ratelimit.check(f"reports:bad:{ratelimit.limit_ip(request)}", 30, 3600)
            raise
        if account_id:
            ratelimit.check(f"reports:account:{account_id}", 30, 3600)
            ratelimit.check(f"reports:account-day:{account_id}", 100, 86400)
        elif client_id:
            ratelimit.check(f"reports:client:{client_id}", 300, 3600)
        else:
            ratelimit.check(f"reports:ip:{ratelimit.limit_ip(request)}", 20, 3600)
        url = servers.norm_url(body.server_url) if body.server_url.strip() else ""
        fields = reports.check(**body.model_dump(exclude={"server_url", "server_name"}))
        row, created = reports.store(conn, fields, server_url=url,
                                     server_name=servers.norm_name(body.server_name, url) if url else body.server_name,
                                     account_id=account_id, client_id=client_id, ip=ratelimit.ip_of(request))
        due = alerts.sync(conn) if created else []
        conn.commit()
    return row, due


@router.post("/reports", status_code=201)
def receive_report(body: ReportBody, request: Request):
    """Keep a report → ``{ok, id}``; the same ``report_id`` from the same
    server again answers the first one's id. The operator is mailed about
    it at once when alerts are on."""
    row, due = _receive(body, request)
    alerts.notify(due)
    return {"ok": True, "id": row["id"]}
