"""``/api/billing``: Checkout, the Customer Portal, keeping a plan that was
set to end, and the signed-in account's subscription, payment method and
invoices (portal session only, like the account settings), and the Stripe
webhook (no session and no CSRF check: Stripe signs it, and
``app._CROSS_ORIGIN_OK`` lets it through). docs/dev/billing.md.

Each handler resolves the session and commits before it calls Stripe, so a
slow Stripe never holds cloud.db's write lock.
"""

from contextlib import closing

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .. import billing, db, ratelimit
from ..accounts import Problem
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
    """``{price: lite_month | lite_year | plus_month | plus_year | pro_month | pro_year}`` → ``{url}``,
    the Checkout Session the browser goes to."""
    account = _signed_in(request)
    ratelimit.check(f"checkout:{account['id']}", 20, 3600)
    with closing(db.connect()) as conn:
        return {"url": billing.checkout_url(conn, account, body.price)}


class PortalBody(BaseModel):
    flow: str = ""      # billing.PORTAL_FLOWS: "" (the portal's home), payment, cancel, switch
    price: str = ""     # the price key a switch moves to


@router.post("/portal")
def portal(request: Request, body: PortalBody | None = None):
    """→ ``{url}``, a Customer Portal session for the account's customer,
    opened on the page ``flow`` names."""
    account = _signed_in(request)
    ratelimit.check(f"billing-portal:{account['id']}", 30, 3600)
    body = body or PortalBody()
    with closing(db.connect()) as conn:
        return {"url": billing.portal_url(conn, account, body.flow, body.price)}


@router.post("/keep")
def keep(request: Request):
    """Undo a cancellation that has not taken effect yet (``billing.keep``)."""
    account = _signed_in(request)
    ratelimit.check(f"billing-keep:{account['id']}", 10, 3600)
    billing.keep(account["id"])
    return {"ok": True}


@router.get("/details")
def details(request: Request):
    """``billing.details``: the payment method on file and the latest
    invoices, read from Stripe now. The Plan page asks after it has loaded."""
    account = _signed_in(request)
    ratelimit.check(f"billing-details:{account['id']}", 60, 3600)
    try:
        return billing.details(account["id"])
    except billing.BillingError as e:
        log.warning("billing: details of %s failed: %s", account["id"], e)
        raise Problem(502, billing.NO_ANSWER) from e


@router.get("/me")
def me(request: Request, fresh: bool = False):
    """``billing.summary`` after re-reading a row older than an hour from
    Stripe, or with ``fresh`` (the page just came back from the portal) one
    older than a few seconds; a Stripe that does not answer leaves the copy
    as it is."""
    account_id = _signed_in(request)["id"]
    try:
        billing.refresh(account_id, max_age=billing.REFRESH_FRESH if fresh else billing.REFRESH_AFTER)
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
