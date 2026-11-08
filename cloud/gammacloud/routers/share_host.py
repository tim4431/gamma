"""``GET /api/share-host/deletions``: the Gamma Cloud accounts deleted
since a point (``accounts.deletions``), which the shared server asks for
every hour and whose libraries and published pages it then deletes
(docs/dev/cloud_accounts.md "A deleted account on the share host").

Only the share host's own client may ask, with its client id and secret as
Basic auth: the list names accounts, if only by their random ids. A pair
that fails is a 401, counted against the caller's IP; another client's
pair is a 403.
"""

from contextlib import closing

from fastapi import APIRouter, HTTPException, Query, Request

from .. import accounts, db, oidc, ratelimit

router = APIRouter(prefix="/api")


@router.get("/share-host/deletions")
def share_host_deletions(request: Request, after: int = Query(0, ge=0)):
    """``{deletions: [{id, account_id, deleted_at}], next}``, the deletions
    after audit entry ``after``; call again with ``after=next`` while a
    page comes back full."""
    auth = request.headers.get("authorization", "")
    with closing(db.connect()) as conn:
        try:
            if not auth.lower().startswith("basic "):
                raise oidc.OAuthError("invalid_client", "no client credentials", 401)
            client = oidc.basic_client(conn, auth)
        except oidc.OAuthError:
            ratelimit.check(f"deletions:bad:{ratelimit.limit_ip(request)}", 30, 3600)
            raise HTTPException(401, "The shared server's client id and secret are required.",
                                headers={"WWW-Authenticate": "Basic"}) from None
        if client["kind"] != "share-host":
            raise HTTPException(403, "Only the shared server's client may list deleted accounts.")
        ratelimit.check(f"deletions:client:{client['client_id']}", 120, 3600)
        return accounts.deletions(conn, after)
