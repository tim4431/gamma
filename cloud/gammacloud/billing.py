"""Stripe billing: Checkout, the Customer Portal and its deep links, the
webhook, the nightly reconciliation, and what the Plan page shows
(docs/dev/billing.md; the design is docs/research/cloud-plans.md "Verifying
a purchase").

The rules that make a purchase trustworthy:

- The browser never states a plan. It names a price key
  (``config.STRIPE_PRICES``); the server makes the Checkout Session with the
  account id as ``client_reference_id``.
- Only Stripe changes a subscription row, and never from an event body: the
  webhook checks the signature, records the event id (a repeat is answered
  and ignored), then reads the subscription from Stripe and writes what it
  is now. Stripe does not promise event order; a fresh read makes every
  event idempotent.
- ``subscriptions`` is a copy. ``reconcile`` repairs a row that drifted,
  once a day from ``tick`` and on an admin's Refresh; the Plan page
  re-reads a row older than an hour. Invoices and the payment method are
  not copied at all: ``details`` reads them from Stripe when the page asks.
- Nothing here refunds. A cancel runs to the end of the paid period (the
  portal's cancel flow, undone by ``keep``); only an account deletion ends
  a subscription at once.
- The effective plan is ``accounts.recompute_plan``: the higher of the
  granted plan and the plan a live subscription pays for.

Nothing else in the account server speaks Stripe's vocabulary. The SDK sits
behind ``client()`` (a small wrapper returning plain dicts), which tests
replace with a fake through ``set_client``. No network call is made while
cloud.db's write lock is held: Stripe is read first, then the row written.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from . import accounts, config, db, hosted
from .accounts import Problem
from .log import log

# Statuses during which the account has a subscription to manage, not to
# buy: a second checkout is refused and pointed at the portal.
HELD_STATUSES = ("active", "trialing", "past_due", "unpaid", "paused")
HANDLED_EVENTS = ("checkout.session.completed", "customer.subscription.created", "customer.subscription.updated",
                  "customer.subscription.deleted", "invoice.paid", "invoice.payment_failed")
REFRESH_AFTER = 3600            # the Plan page re-reads a row older than this (seconds)
REFRESH_FRESH = 10              # ... and, back from the portal, one older than this
RECONCILE_EVERY = 86400
RECONCILED_KEY = "billing_reconciled_at"   # a row in ``settings`` that settings.py does not know (see tick)
MAX_PAGES = 200                 # of 100 subscriptions each, per reconcile
INVOICES_SHOWN = 12             # the Plan page's billing history; the portal has the rest
NO_ANSWER = "Stripe did not answer. Try again in a minute."
# What Checkout shows beside its Subscribe button (plain text, 1200 characters at most).
CHECKOUT_NOTE = ("Payments are not refunded. Your plan starts at once; you can cancel any time and keep it "
                 "until the end of the period you paid for.")


class BillingError(Exception):
    """Stripe could not be reached or refused the call."""


class BadSignature(Exception):
    """A webhook body whose signature does not check out."""


# --- the Stripe client --------------------------------------------------------

class StripeClient:
    """The calls billing makes, through the official SDK, answering plain
    dicts. Imported lazily, so the server runs (with billing off) without
    the package."""

    def __init__(self, secret: str):
        try:
            import stripe
        except ImportError as e:  # a secret set on an image without the package: billing fails, not the server
            raise BillingError("the stripe package is not installed (cloud/requirements.txt)") from e
        self._stripe = stripe
        self._client = stripe.StripeClient(secret)

    def _call(self, fn, *args, **kw) -> dict:
        try:
            return fn(*args, **kw).to_dict()
        except self._stripe.StripeError as e:
            raise BillingError(getattr(e, "user_message", None) or str(e)) from e

    def construct_event(self, payload: bytes, signature: str, secret: str) -> dict:
        try:
            return self._client.construct_event(payload, signature, secret).to_dict()
        except (ValueError, self._stripe.SignatureVerificationError) as e:
            raise BadSignature(str(e)) from e

    def create_checkout_session(self, params: dict) -> dict:
        return self._call(self._client.v1.checkout.sessions.create, params)

    def create_portal_session(self, params: dict) -> dict:
        return self._call(self._client.v1.billing_portal.sessions.create, params)

    @staticmethod
    def _expand(expand: tuple) -> dict | None:
        return {"expand": list(expand)} if expand else None

    def retrieve_subscription(self, subscription_id: str, expand: tuple = ()) -> dict:
        return self._call(self._client.v1.subscriptions.retrieve, subscription_id, self._expand(expand))

    def update_subscription(self, subscription_id: str, params: dict) -> dict:
        return self._call(self._client.v1.subscriptions.update, subscription_id, params)

    def retrieve_customer(self, customer_id: str, expand: tuple = ()) -> dict:
        return self._call(self._client.v1.customers.retrieve, customer_id, self._expand(expand))

    def list_invoices(self, customer_id: str, limit: int) -> list[dict]:
        """The customer's invoices, newest first."""
        return self._call(self._client.v1.invoices.list, {"customer": customer_id, "limit": limit}).get("data") or []

    def cancel_subscription(self, subscription_id: str) -> dict:
        """Cancel now (not at period end); answers the canceled subscription."""
        return self._call(self._client.v1.subscriptions.cancel, subscription_id)

    def list_subscriptions(self, starting_after: str = "") -> tuple[list[dict], bool]:
        params = {"status": "all", "limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        page = self._call(self._client.v1.subscriptions.list, params)
        return page.get("data") or [], bool(page.get("has_more"))


_client = None


def client():
    """The Stripe client for ``config.STRIPE_SECRET`` (or the test fake)."""
    global _client
    if _client is None:
        _client = StripeClient(config.STRIPE_SECRET)
    return _client


def set_client(fake) -> None:
    """Tests: replace the client (None goes back to the real one)."""
    global _client
    _client = fake


def enabled() -> bool:
    """Billing is on: a Stripe secret is configured."""
    return bool(config.STRIPE_SECRET)


def can_sell(plan: str) -> bool:
    """A plan can be bought: billing is on and the place its library lives
    is configured, the shared server's address (``config.APP_URL``) for
    Lite and Plus and the hosting domain (``config.HOSTED_DOMAIN``) for a
    plan with a container."""
    limits = config.PLAN_LIMITS.get(plan, {})
    home = config.APP_URL if limits.get("shared") else config.HOSTED_DOMAIN if limits.get("hosted") else ""
    return enabled() and bool(home)


# --- helpers ------------------------------------------------------------------

def _id(value) -> str:
    """A Stripe reference as its id, whether expanded or not."""
    if isinstance(value, dict):
        return value.get("id") or ""
    return value or ""


def _iso(ts) -> str | None:
    """A Unix time from Stripe in cloud.db's timestamp form."""
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def price_info(price_id: str) -> tuple[str, str]:
    """(plan, interval) a configured Stripe Price buys; ("", "") if unknown."""
    for plan, interval, pid in config.STRIPE_PRICES.values():
        if pid and pid == price_id:
            return plan, interval
    return "", ""


def _fields(s: dict) -> dict:
    """The row a Stripe Subscription object makes (not yet the grace and
    end dates, which depend on the row before)."""
    items = (s.get("items") or {}).get("data") or []
    item = items[0] if items else {}
    price_id = _id(item.get("price"))
    plan, _ = price_info(price_id)
    if not plan:
        log.warning("billing: subscription %s has price %s, which no GAMMA_CLOUD_STRIPE_PRICE_* names",
                    s.get("id"), price_id)
    # Newer API versions keep the period on the item, older ones on the subscription.
    period_end = s.get("current_period_end") or item.get("current_period_end")
    if s.get("cancel_at"):
        period_end = min(int(s["cancel_at"]), int(period_end)) if period_end else s["cancel_at"]
    return {"stripe_subscription_id": s.get("id") or "", "stripe_customer_id": _id(s.get("customer")),
            "price_id": price_id, "plan": plan or "free", "status": s.get("status") or "none",
            "cancel_at_period_end": 1 if (s.get("cancel_at_period_end") or s.get("cancel_at")) else 0,
            "current_period_end": _iso(period_end), "seats": int(item.get("quantity") or 1)}


def _same(row, fields: dict) -> bool:
    return all(row[k] == v for k, v in fields.items())


def _owner(conn, s: dict, named: str = "") -> tuple[str, str]:
    """(account id, problem) for a Stripe subscription: the row that already
    holds it, else the account the checkout named (``named``, the session's
    ``client_reference_id``) or the subscription's metadata, else the row
    holding its customer. ``problem`` is ``mismatch`` when the named account
    is not the one the customer is bound to, ``unknown`` when no account
    fits."""
    row = conn.execute("SELECT account_id FROM subscriptions WHERE stripe_subscription_id = ? "
                       "AND stripe_subscription_id != ''", (s.get("id") or "",)).fetchone()
    if row:
        return (named, "mismatch") if named and named != row["account_id"] else (row["account_id"], "")
    customer = _id(s.get("customer"))
    bound = conn.execute("SELECT account_id FROM subscriptions WHERE stripe_customer_id = ? AND stripe_customer_id != ''",
                         (customer,)).fetchone() if customer else None
    named = named or (s.get("metadata") or {}).get("account_id") or ""
    if named:
        if bound and bound["account_id"] != named:
            return named, "mismatch"
        return (named, "") if accounts.by_id(conn, named) else ("", "unknown")
    return (bound["account_id"], "") if bound else ("", "unknown")


def _write(conn, account_id: str, s: dict, actor: str = "stripe") -> str:
    """Store what Stripe says the subscription is now and recompute the
    plan; ``applied``, or ``stale`` when the row holds another subscription
    that is still held and this one is over (an old subscription's late
    event after a resume)."""
    f = _fields(s)
    row = accounts.subscription(conn, account_id)
    if (row and row["stripe_subscription_id"] and row["stripe_subscription_id"] != f["stripe_subscription_id"]
            and row["status"] in HELD_STATUSES and f["status"] not in HELD_STATUSES):
        return "stale"
    same_sub = bool(row) and row["stripe_subscription_id"] == f["stripe_subscription_id"]
    if f["status"] == "past_due":
        f["past_due_since"] = row["past_due_since"] if same_sub and row["past_due_since"] else db.now()
    else:
        f["past_due_since"] = None
    if f["status"] in ("canceled", "incomplete_expired"):
        f["ended_at"] = (row["ended_at"] if same_sub and row["ended_at"] else None) or _iso(s.get("ended_at")) or db.now()
    else:
        f["ended_at"] = None
    if not f["stripe_customer_id"] and row:
        f["stripe_customer_id"] = row["stripe_customer_id"]
    cols = list(f)
    conn.execute(
        f"INSERT INTO subscriptions (account_id, {', '.join(cols)}, updated_at) VALUES (?, {', '.join('?' * len(cols))}, ?) "
        f"ON CONFLICT (account_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}, "
        "updated_at = excluded.updated_at",
        (account_id, *f.values(), db.now()))
    if not row or row["status"] != f["status"] or row["plan"] != f["plan"]:
        db.audit(conn, "billing.subscription", account_id, actor,
                 f"{f['stripe_subscription_id']} {f['plan']} {f['status']}")
    accounts.recompute_plan(conn, account_id, actor, "stripe", notify=True)
    return "applied"


# --- checkout and the portal --------------------------------------------------

def checkout_url(conn, account, price_key: str, return_base: str = "") -> str:
    """The Checkout Session URL for one of the price keys. Refused
    while billing is off or the plan cannot be sold (``can_sell``), for an
    unknown or unconfigured price, for an unconfirmed e-mail (a Gamma
    server would not sign it in), and while the account already holds a
    subscription (409: the portal changes it)."""
    if not enabled():
        raise Problem(503, "Paid plans are not available yet.")
    plan, _, price_id = config.STRIPE_PRICES.get(price_key or "", ("", "", ""))
    if not price_id:
        raise Problem(400, "Unknown price.")
    if not can_sell(plan):
        raise Problem(503, "This plan is not available yet.")
    if not account["email_verified_at"]:
        raise Problem(403, "Confirm your e-mail address first: Gamma servers sign you in with it.")
    sub = accounts.subscription(conn, account["id"])
    if sub and sub["status"] in HELD_STATUSES:
        raise Problem(409, "You already have a subscription. Change or cancel it on the Plan page.")
    base = (return_base or config.PUBLIC_URL).rstrip("/")
    meta = {"account_id": account["id"], "plan": plan}
    params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "client_reference_id": account["id"],
        "automatic_tax": {"enabled": True},
        "allow_promotion_codes": True,
        "custom_text": {"submit": {"message": CHECKOUT_NOTE}},
        "success_url": f"{base}/plan?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/plan",
        "metadata": meta,
        "subscription_data": {"metadata": meta},  # so the subscription names its account on its own
    }
    if sub and sub["stripe_customer_id"]:
        params["customer"] = sub["stripe_customer_id"]
        params["customer_update"] = {"address": "auto", "name": "auto"}  # Stripe Tax needs the address
    else:
        params["customer_email"] = account["email"]
    try:
        session = client().create_checkout_session(params)
    except BillingError as e:
        log.warning("billing: checkout for %s failed: %s", account["id"], e)
        raise Problem(502, NO_ANSWER) from e
    return session["url"]


PORTAL_FLOWS = ("", "payment", "cancel", "switch")


def _can_switch(sub) -> bool:
    """A plan or interval switch is offered on a subscription that is paid
    up and not set to end (an ending one is kept first, ``keep``)."""
    return bool(sub and sub["stripe_subscription_id"] and sub["status"] in accounts.LIVE_STATUSES
                and not sub["cancel_at_period_end"])


def _flow(sub, flow: str, price_key: str) -> dict:
    """The portal's ``flow_data`` for one of ``PORTAL_FLOWS``: the page it
    opens on instead of its home. ``switch`` opens Stripe's confirmation of
    a move to ``price_key``, which shows what is charged before the person
    agrees; it reads the subscription for the item to move."""
    if flow == "payment":
        return {"type": "payment_method_update"}
    held = sub["stripe_subscription_id"] and sub["status"] in HELD_STATUSES
    if flow == "cancel":
        if not held or sub["cancel_at_period_end"]:
            raise Problem(409, "There is no plan to cancel.")
        return {"type": "subscription_cancel", "subscription_cancel": {"subscription": sub["stripe_subscription_id"]}}
    _, _, price_id = config.STRIPE_PRICES.get(price_key or "", ("", "", ""))
    if not price_id:
        raise Problem(400, "Unknown price.")
    if price_id == sub["price_id"]:
        raise Problem(400, "That is your current plan.")
    if not _can_switch(sub):
        raise Problem(409, "This plan cannot be changed right now. Fix the payment or keep the plan first.")
    items = (client().retrieve_subscription(sub["stripe_subscription_id"]).get("items") or {}).get("data") or []
    if not items:
        raise BillingError(f"subscription {sub['stripe_subscription_id']} has no item")
    return {"type": "subscription_update_confirm", "subscription_update_confirm": {
        "subscription": sub["stripe_subscription_id"],
        "items": [{"id": items[0]["id"], "price": price_id, "quantity": sub["seats"] or 1}]}}


def portal_url(conn, account, flow: str = "", price_key: str = "", return_base: str = "") -> str:
    """A Customer Portal session for the account's Stripe customer. With no
    ``flow`` it opens on the portal's home (invoices, billing details); a
    flow opens the page for that one thing and comes back to ``/plan`` when
    it is done: ``payment`` (the payment method), ``cancel`` (at the end of
    the period) or ``switch`` to ``price_key``. A flow the portal's
    configuration does not allow falls back to its home."""
    if not enabled():
        raise Problem(503, "Billing is not available yet.")
    if flow not in PORTAL_FLOWS:
        raise Problem(400, "Unknown billing action.")
    sub = accounts.subscription(conn, account["id"])
    if not sub or not sub["stripe_customer_id"]:
        raise Problem(404, "There is no billing account yet.")
    back = f"{(return_base or config.PUBLIC_URL).rstrip('/')}/plan"
    params = {"customer": sub["stripe_customer_id"], "return_url": back}
    try:
        if flow:
            flow_data = {**_flow(sub, flow, price_key),
                         "after_completion": {"type": "redirect", "redirect": {"return_url": f"{back}?billing={flow}"}}}
            try:
                return client().create_portal_session({**params, "flow_data": flow_data})["url"]
            except BillingError as e:
                log.warning("billing: the portal refused the %s flow for %s (is it allowed in the portal's "
                            "configuration?): %s", flow, account["id"], e)
        return client().create_portal_session(params)["url"]
    except BillingError as e:
        log.warning("billing: portal for %s failed: %s", account["id"], e)
        raise Problem(502, NO_ANSWER) from e


# --- the webhook --------------------------------------------------------------

def _subject(etype: str, obj: dict) -> str:
    """The subscription id an event is about ("" for none)."""
    if etype == "checkout.session.completed":
        return _id(obj.get("subscription")) if obj.get("mode", "subscription") == "subscription" else ""
    if etype.startswith("customer.subscription."):
        return obj.get("id") or ""
    if etype.startswith("invoice."):
        parent = (obj.get("parent") or {}).get("subscription_details") or {}
        return _id(obj.get("subscription")) or _id(parent.get("subscription"))
    return ""


def _seen(conn, event_id: str) -> bool:
    return conn.execute("SELECT 1 FROM billing_events WHERE id = ?", (event_id,)).fetchone() is not None


def handle_webhook(payload: bytes, signature: str) -> dict:
    """One Stripe delivery; answers ``{"outcome": ...}``: ``applied``,
    ``duplicate``, ``ignored`` (a type not handled, or no subscription),
    ``mismatch`` (the checkout's customer belongs to another account; logged,
    nothing written), ``unknown`` (no account fits), ``stale``. A bad
    signature is a 400; Stripe being unreachable a 502, which rolls the
    event back so Stripe's retry is handled."""
    if not enabled() or not config.STRIPE_WEBHOOK_SECRET:
        raise Problem(503, "Billing is not set up.")
    try:
        event = client().construct_event(payload, signature or "", config.STRIPE_WEBHOOK_SECRET)
    except BadSignature as e:
        raise Problem(400, "bad signature") from e
    except BillingError as e:
        log.warning("billing: webhook: %s", e)
        raise Problem(503, "Billing is not set up.") from e
    event_id, etype = event.get("id") or "", event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}
    if not event_id:
        raise Problem(400, "no event id")
    with closing(db.connect()) as conn:
        if _seen(conn, event_id):
            return {"outcome": "duplicate"}
    subscription_id = _subject(etype, obj) if etype in HANDLED_EVENTS else ""
    fresh = None
    if subscription_id:  # read Stripe before taking the write lock
        try:
            fresh = client().retrieve_subscription(subscription_id)
        except BillingError as e:
            log.warning("billing: event %s: cannot read subscription %s: %s", event_id, subscription_id, e)
            raise Problem(502, "Stripe did not answer.") from e
    with closing(db.connect()) as conn:
        db.begin_write(conn)
        try:
            conn.execute("INSERT INTO billing_events (id, type, received_at) VALUES (?, ?, ?)",
                         (event_id, etype[:100], db.now()))
        except sqlite3.IntegrityError:
            return {"outcome": "duplicate"}  # a concurrent delivery of the same event won
        account_id, outcome = "", "ignored"
        if fresh:
            named = (obj.get("client_reference_id") or "") if etype == "checkout.session.completed" else ""
            account_id, outcome = _owner(conn, fresh, named)
            if outcome == "mismatch":
                log.warning("billing: event %s names account %s, but customer %s is bound to another account; "
                            "nothing changed", event_id, account_id, _id(fresh.get("customer")))
            elif not outcome:
                outcome = _write(conn, account_id, fresh)
        conn.execute("UPDATE billing_events SET account_id = ?, outcome = ? WHERE id = ?",
                     (account_id, outcome, event_id))
        conn.commit()
    return {"outcome": outcome}


# --- keeping the copy right ---------------------------------------------------

CANCEL_ON_DELETE = HELD_STATUSES + ("incomplete",)  # a subscription that could still charge


def _store(account_id: str, s: dict, actor: str, event: str = "", detail: str = "") -> None:
    """Write a subscription just read from Stripe, in a transaction of its
    own, with an audit row when ``event`` names one."""
    with closing(db.connect()) as conn:
        db.begin_write(conn)
        _write(conn, account_id, s, actor)
        if event:
            db.audit(conn, event, account_id, actor, detail)
        conn.commit()


def cancel_for_deletion(account_id: str, actor: str) -> bool:
    """Before an account is deleted: cancel its subscription at Stripe now
    (not at period end) and store the canceled copy. Returns whether there
    was one to cancel. Raises ``Problem(502)`` when it cannot be cancelled
    (Stripe unreachable, or billing switched off with a subscription still
    live), so the caller refuses the deletion rather than leave a deleted
    account being charged. Runs before the deletion's transaction: no
    network call under the write lock."""
    with closing(db.connect()) as conn:
        row = accounts.subscription(conn, account_id)
    if not row or not row["stripe_subscription_id"] or row["status"] not in CANCEL_ON_DELETE:
        return False
    detail = ("The account has a paid subscription that could not be cancelled at Stripe, so it was not "
              "deleted. Try again in a few minutes.")
    if not enabled():
        log.warning("billing: cannot cancel %s before deleting %s: billing is off",
                    row["stripe_subscription_id"], account_id)
        raise Problem(502, detail)
    try:
        canceled = client().cancel_subscription(row["stripe_subscription_id"])
    except BillingError as e:
        log.warning("billing: cancelling %s before deleting %s failed: %s", row["stripe_subscription_id"], account_id, e)
        raise Problem(502, detail) from e
    _store(account_id, canceled, actor, "billing.cancel", f"{row['stripe_subscription_id']} on account deletion")
    return True


def keep(account_id: str) -> None:
    """Undo a cancellation that has not taken effect: the subscription
    renews again at the end of its period. 409 when the plan is not set to
    end."""
    if not enabled():
        raise Problem(503, "Billing is not available yet.")
    with closing(db.connect()) as conn:
        row = accounts.subscription(conn, account_id)
    if (not row or not row["stripe_subscription_id"] or row["status"] not in HELD_STATUSES
            or not row["cancel_at_period_end"]):
        raise Problem(409, "Your plan is not set to end.")
    try:
        fresh = client().update_subscription(row["stripe_subscription_id"], {"cancel_at_period_end": False})
        if fresh.get("cancel_at"):  # the portal may have set a date instead of the flag
            fresh = client().update_subscription(row["stripe_subscription_id"], {"cancel_at": ""})
    except BillingError as e:
        log.warning("billing: keeping %s for %s failed: %s", row["stripe_subscription_id"], account_id, e)
        raise Problem(502, NO_ANSWER) from e
    _store(account_id, fresh, account_id, "billing.keep", row["stripe_subscription_id"])


def refresh(account_id: str, max_age: int | None = None) -> bool:
    """Re-read the account's subscription from Stripe and store it; with
    ``max_age``, only when the row is older than that. Returns whether it
    read. Raises ``BillingError`` when Stripe does not answer."""
    if not enabled():
        return False
    with closing(db.connect()) as conn:
        row = accounts.subscription(conn, account_id)
    if not row or not row["stripe_subscription_id"]:
        return False
    if max_age is not None and row["updated_at"] > db.after(-max_age):
        return False
    _store(account_id, client().retrieve_subscription(row["stripe_subscription_id"]), "system")
    return True


def reconcile(conn) -> dict:
    """Page through every subscription Stripe has and repair the rows that
    differ. Stripe lists the newest first; per account the newest held one
    wins, else the newest. The caller commits."""
    subs: list[dict] = []
    after = ""
    for _ in range(MAX_PAGES):
        page, more = client().list_subscriptions(after)
        subs += page
        if not more or not page:
            break
        after = page[-1]["id"]
    best: dict[str, dict] = {}
    unknown = 0
    for s in subs:
        account_id, problem = _owner(conn, s)
        if problem:
            unknown += 1
            continue
        current = best.get(account_id)
        if current is None or (current.get("status") not in HELD_STATUSES and s.get("status") in HELD_STATUSES):
            best[account_id] = s
    repaired = 0
    db.begin_write(conn)
    for account_id, s in best.items():
        row = accounts.subscription(conn, account_id)
        if row and _same(row, _fields(s)):
            continue
        if _write(conn, account_id, s, "system") == "applied":
            repaired += 1
    return {"seen": len(subs), "repaired": repaired, "unknown": unknown}


def tick(conn) -> None:
    """The hourly pass from ``app.purge``: a failed payment whose grace ran
    out loses its plan (no event says so), and once a day ``reconcile``.

    The last reconcile time is a row in the ``settings`` table under
    ``RECONCILED_KEY``. settings.py ignores keys it does not know (its
    cache and the Admin page only read ``settings.DEFAULTS``), so the row is
    written here directly rather than through ``settings.update``, which
    would refuse the key."""
    for row in conn.execute("SELECT account_id FROM subscriptions WHERE status = 'past_due'").fetchall():
        accounts.recompute_plan(conn, row["account_id"], "system", "stripe")
    if not enabled():
        return
    last = conn.execute("SELECT value FROM settings WHERE key = ?", (RECONCILED_KEY,)).fetchone()
    if last and last["value"] > db.after(-RECONCILE_EVERY):
        return
    conn.commit()  # end any transaction before the network calls
    try:
        result = reconcile(conn)
    except BillingError as e:
        log.warning("billing: reconcile failed: %s", e)
        return
    conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                 (RECONCILED_KEY, db.now(), db.now()))
    if result["repaired"] or result["unknown"]:
        log.info("billing: reconciled %s", json.dumps(result))


# --- what the Plan page shows -------------------------------------------------

def _plus_days(iso: str | None, days: int) -> str | None:
    return _iso(db.parse(iso).timestamp() + days * 86400) if iso else None


def summary(conn, account_id: str) -> dict:
    """The Plan page's data and ``GET /api/billing/me``, from cloud.db
    alone. When a lapsed server stops and is deleted is the server's to say
    (``hosted.status_for``)."""
    account = accounts.by_id(conn, account_id)
    pub = accounts.public(account, conn)
    row = accounts.subscription(conn, account_id)
    sub = None
    if row and row["stripe_subscription_id"] and row["status"] != "none":
        plan, interval = price_info(row["price_id"])
        plan = plan or row["plan"]
        ends = bool(row["cancel_at_period_end"]) and row["status"] in HELD_STATUSES
        sub = {"status": row["status"], "plan": plan, "interval": interval,
               "amount_usd": config.PLAN_PRICES_USD.get(plan, {}).get(interval),
               "renews_at": row["current_period_end"] if row["status"] in accounts.LIVE_STATUSES and not ends else None,
               "cancel_at_period_end": ends, "period_end": row["current_period_end"], "seats": row["seats"],
               "can_switch": enabled() and _can_switch(row),
               "past_due_since": row["past_due_since"],
               "grace_ends_at": _plus_days(row["past_due_since"], config.GRACE_DAYS),
               "ended_at": row["ended_at"], "updated_at": row["updated_at"]}
    return {"enabled": enabled(), "sells": [p for p in config.PLANS if can_sell(p)],
            "shared_url": config.APP_URL,
            "plan": pub["plan"], "plan_source": pub["plan_source"], "granted_plan": pub["granted_plan"],
            "has_customer": bool(row and row["stripe_customer_id"]),
            "subscription": sub, "prices": config.PLAN_PRICES_USD,
            "hosted": hosted.status_for(conn, account_id)}


def _payment_method(pm) -> dict | None:
    """What the page says about a payment method: its kind (``card``,
    ``sepa_debit``, …), and where the kind has them the brand, the last
    four digits and the expiry."""
    if not isinstance(pm, dict):
        return None
    kind = pm.get("type") or ""
    of = pm.get(kind) if isinstance(pm.get(kind), dict) else {}
    return {"kind": kind, "brand": of.get("brand") or "", "last4": of.get("last4") or "",
            "exp_month": of.get("exp_month"), "exp_year": of.get("exp_year")}


def _invoice(i: dict) -> dict:
    """A Stripe invoice as a billing-history row. ``total`` is in the
    currency's minor unit (cents); ``url`` is Stripe's hosted page, where an
    open invoice is paid and a paid one has its receipt."""
    lines = (i.get("lines") or {}).get("data") or []
    return {"id": i.get("id") or "", "number": i.get("number") or "", "created": _iso(i.get("created")),
            "description": (lines[0].get("description") if lines else "") or i.get("description") or "",
            "total": int(i.get("total") or 0), "currency": i.get("currency") or "usd", "status": i.get("status") or "",
            "url": i.get("hosted_invoice_url") or "", "pdf": i.get("invoice_pdf") or ""}


def details(account_id: str) -> dict:
    """``GET /api/billing/details``: the payment method the next charge
    goes to (the subscription's, else the customer's default) and the
    newest ``INVOICES_SHOWN`` invoices, read from Stripe now. Nothing of it
    is stored. Raises ``BillingError`` when Stripe does not answer."""
    with closing(db.connect()) as conn:
        row = accounts.subscription(conn, account_id)
    if not enabled() or not row or not row["stripe_customer_id"]:
        return {"payment_method": None, "invoices": []}
    pm = None
    if row["stripe_subscription_id"] and row["status"] in HELD_STATUSES:
        pm = client().retrieve_subscription(row["stripe_subscription_id"],
                                            expand=("default_payment_method",)).get("default_payment_method")
    if not isinstance(pm, dict):
        customer = client().retrieve_customer(row["stripe_customer_id"],
                                              expand=("invoice_settings.default_payment_method",))
        pm = (customer.get("invoice_settings") or {}).get("default_payment_method")
    invoices = [_invoice(i) for i in client().list_invoices(row["stripe_customer_id"], INVOICES_SHOWN)
                if i.get("status") != "draft"]
    return {"payment_method": _payment_method(pm), "invoices": invoices}


def admin_summary(conn) -> dict:
    """The Billing tab's strip: the subscriptions that pay now by plan,
    what they bring in a month (a yearly price counts a twelfth, from
    ``config.PLAN_PRICES_USD``), and how many are past due or set to end."""
    by_plan = {p: 0 for p in config.PLANS if p != "free"}
    mrr, past_due, ending = 0.0, 0, 0
    for row in conn.execute("SELECT price_id, status, cancel_at_period_end FROM subscriptions WHERE status IN "
                            "('active', 'trialing', 'past_due')").fetchall():
        plan, interval = price_info(row["price_id"])
        if plan not in by_plan:
            continue
        by_plan[plan] += 1
        price = config.PLAN_PRICES_USD[plan]
        mrr += price["year"] / 12 if interval == "year" else price["month"]
        past_due += row["status"] == "past_due"
        ending += bool(row["cancel_at_period_end"])
    return {"by_plan": by_plan, "paying": sum(by_plan.values()), "mrr_usd": round(mrr, 2),
            "past_due": past_due, "ending": ending}
