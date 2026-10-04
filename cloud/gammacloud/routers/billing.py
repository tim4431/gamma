"""``/api/billing``: Checkout, the Customer Portal and the signed-in
account's subscription (portal session only, like the account settings),
and the Stripe webhook (no session and no CSRF check: Stripe signs it, and
``app._CROSS_ORIGIN_OK`` lets it through). docs/dev/billing.md.

Each handler resolves the session and commits before it calls Stripe, so a
slow Stripe never holds cloud.db's write lock.
"""

from contextlib import closing

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import billing, db, ratelimit
from ..log import log
from .accounts import portal_account

router = APIRouter(prefix="/api/billing")


def _signed_in(request: Request):
    with closing(db.connect()) as conn:
        account = portal_account(conn, request)
        conn.commit()
    return account


class CheckoutBody(BaseModel):
    price: str


@router.post("/checkout")
def checkout(body: CheckoutBody, request: Request):
    """``{price: plus_month | plus_year | pro_month | pro_year}`` → ``{url}``,
    the Checkout Session the browser goes to."""
    account = _signed_in(request)
    ratelimit.check(f"checkout:{account['id']}", 20, 3600)
    with closing(db.connect()) as conn:
        return {"url": billing.checkout_url(conn, account, body.price)}


@router.post("/portal")
def portal(request: Request):
    """→ ``{url}``, a Customer Portal session for the account's customer."""
    account = _signed_in(request)
    ratelimit.check(f"billing-portal:{account['id']}", 30, 3600)
    with closing(db.connect()) as conn:
        return {"url": billing.portal_url(conn, account)}


@router.get("/me")
def me(request: Request):
    """``billing.summary`` after re-reading a row older than an hour from
    Stripe; a Stripe that does not answer leaves the copy as it is."""
    account_id = _signed_in(request)["id"]
    try:
        billing.refresh(account_id, max_age=billing.REFRESH_AFTER)
    except billing.BillingError as e:
        log.warning("billing: refresh of %s failed: %s", account_id, e)
    with closing(db.connect()) as conn:
        return billing.summary(conn, account_id)


@router.post("/webhook")
async def webhook(request: Request):
    """Stripe's deliveries: the raw body and its ``Stripe-Signature``. The
    handler reads Stripe and cloud.db, so it runs off the event loop."""
    payload = await request.body()
    return await run_in_threadpool(billing.handle_webhook, payload, request.headers.get("stripe-signature", ""))
