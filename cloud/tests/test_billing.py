"""Stripe billing (gammacloud/billing.py, docs/dev/billing.md) against a fake
Stripe client: checkout, the webhook and its idempotency, the effective-plan
rule, reconcile, the portal's flows, the payment method and invoices, the
Plan page's states and the admin's Billing tab. No network call is made; the
hosted side is stubbed to record what it heard."""

import json
from contextlib import closing

import pytest
from conftest import make_admin, register, set_setting, verify
from fastapi.testclient import TestClient

from gammacloud import accounts, billing, config, db, hosted

PRICES = {"lite_month": ("lite", "month", "price_lite_m"), "lite_year": ("lite", "year", "price_lite_y"),
          "plus_month": ("plus", "month", "price_plus_m"), "plus_year": ("plus", "year", "price_plus_y"),
          "pro_month": ("pro", "month", "price_pro_m"), "pro_year": ("pro", "year", "price_pro_y")}
PERIOD_END = 1893456000  # 2030-01-01


class FakeStripe:
    """Stands in for billing.StripeClient. ``subs`` is what Stripe "has"."""

    def __init__(self):
        self.subs: dict[str, dict] = {}
        self.sessions: list[dict] = []
        self.portals: list[dict] = []
        self.retrieved: list[str] = []
        self.canceled: list[str] = []
        self.updates: list[tuple] = []
        self.customers: dict[str, dict] = {}
        self.invoices: list[dict] = []
        self.refuse_flows = False       # a portal configuration that allows no deep link
        self.list_calls = 0
        self.down = False
        self.plan_changed: list[str] = []
        self.hosted: dict[str, dict] = {}

    def construct_event(self, payload, signature, secret):
        if signature != "sig-ok" or secret != "whsec_test":
            raise billing.BadSignature("no")
        return json.loads(payload)

    def create_checkout_session(self, params):
        self.sessions.append(params)
        return {"id": "cs_1", "url": "https://checkout.stripe.test/cs_1"}

    def create_portal_session(self, params):
        if self.down or (self.refuse_flows and "flow_data" in params):
            raise billing.BillingError("no")
        self.portals.append(params)
        return {"url": "https://billing.stripe.test/p_1"}

    def retrieve_subscription(self, subscription_id, expand=()):
        if self.down:
            raise billing.BillingError("down")
        self.retrieved.append(subscription_id)
        return json.loads(json.dumps(self.subs[subscription_id]))

    def update_subscription(self, subscription_id, params):
        if self.down:
            raise billing.BillingError("down")
        self.updates.append((subscription_id, params))
        s = self.subs[subscription_id]
        s.update({k: (None if v == "" else v) for k, v in params.items()})
        return json.loads(json.dumps(s))

    def retrieve_customer(self, customer_id, expand=()):
        if self.down:
            raise billing.BillingError("down")
        return json.loads(json.dumps(self.customers.get(customer_id, {"id": customer_id})))

    def list_invoices(self, customer_id, limit):
        if self.down:
            raise billing.BillingError("down")
        return json.loads(json.dumps([i for i in self.invoices if i["customer"] == customer_id][:limit]))

    def cancel_subscription(self, subscription_id):
        if self.down:
            raise billing.BillingError("down")
        self.canceled.append(subscription_id)
        self.subs[subscription_id]["status"] = "canceled"
        return json.loads(json.dumps(self.subs[subscription_id]))

    def list_subscriptions(self, starting_after=""):
        self.list_calls += 1
        if self.down:
            raise billing.BillingError("down")
        return list(reversed(list(self.subs.values()))), False


def sub(id="sub_1", customer="cus_1", price="price_plus_m", status="active", cancel=False, quantity=1, metadata=None):
    return {"id": id, "object": "subscription", "customer": customer, "status": status,
            "cancel_at_period_end": cancel, "cancel_at": None, "metadata": metadata or {},
            "items": {"data": [{"id": "si_1", "price": {"id": price}, "quantity": quantity,
                                "current_period_end": PERIOD_END}]}}


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(config, "STRIPE_SECRET", "sk_test_x")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(config, "HOSTED_DOMAIN", "gammapdf.test")
    monkeypatch.setattr(config, "APP_URL", "https://share.gammapdf.test")
    monkeypatch.setattr(config, "STRIPE_PRICES", PRICES)
    monkeypatch.setattr(hosted, "plan_changed", lambda conn, account_id: fake.plan_changed.append(account_id))
    monkeypatch.setattr(hosted, "status_for", lambda conn, account_id: fake.hosted.get(account_id))
    billing.set_client(fake)
    yield fake
    billing.set_client(None)


_n = [0]


def send(client, etype, obj, event_id=None, signature="sig-ok"):
    _n[0] += 1
    body = json.dumps({"id": event_id or f"evt_{_n[0]}", "type": etype, "data": {"object": obj}})
    return client.post("/api/billing/webhook", content=body, headers={"Stripe-Signature": signature})


def completed(client, fake, account_id, s=None, customer="cus_1"):
    """A checkout for ``account_id`` finishing with subscription ``s``."""
    s = s or sub(customer=customer)
    fake.subs[s["id"]] = s
    r = send(client, "checkout.session.completed", {"object": "checkout.session", "mode": "subscription",
                                                    "client_reference_id": account_id, "customer": customer,
                                                    "subscription": s["id"]})
    assert r.status_code == 200, r.text
    return r.json()["outcome"]


def row(account_id):
    with closing(db.connect()) as conn:
        return accounts.subscription(conn, account_id)


def plan_of(account_id):
    with closing(db.connect()) as conn:
        return accounts.by_id(conn, account_id)["plan"]


def sql(query, *args):
    with closing(db.connect()) as conn:
        conn.execute(query, args)
        conn.commit()


def signed_up(client, username="alice"):
    account = register(client, username)
    verify(client, username)
    return account


# --- checkout and the portal --------------------------------------------------

def test_checkout_refused(client, stripe, monkeypatch):
    alice = register(client)
    assert client.post("/api/billing/checkout", json={"price": "nope"}).status_code == 400
    assert client.post("/api/billing/checkout", json={"price": "plus_month"}).status_code == 403  # unverified
    verify(client)
    monkeypatch.setitem(config.STRIPE_PRICES, "pro_year", ("pro", "year", ""))
    assert client.post("/api/billing/checkout", json={"price": "pro_year"}).status_code == 400
    # the page offers both intervals, so a plan missing one price id is not sold at all
    assert client.post("/api/billing/checkout", json={"price": "pro_month"}).status_code == 503
    completed(client, stripe, alice["id"])
    r = client.post("/api/billing/checkout", json={"price": "lite_month"})
    assert r.status_code == 409 and "Plan page" in r.json()["detail"]
    monkeypatch.setattr(config, "STRIPE_SECRET", "")
    assert client.post("/api/billing/checkout", json={"price": "plus_month"}).status_code == 503
    assert stripe.sessions == []
    with TestClient(client.app, base_url="http://testserver") as anon:
        assert anon.post("/api/billing/checkout", json={"price": "plus_month"}).status_code == 401


def test_checkout_session_fields(client, stripe):
    alice = signed_up(client)
    r = client.post("/api/billing/checkout", json={"price": "plus_year"})
    assert r.status_code == 200 and r.json()["url"] == "https://checkout.stripe.test/cs_1"
    p = stripe.sessions[-1]
    assert p["mode"] == "subscription" and p["line_items"] == [{"price": "price_plus_y", "quantity": 1}]
    assert p["client_reference_id"] == alice["id"] and p["customer_email"] == "alice@example.org" and "customer" not in p
    assert p["automatic_tax"] == {"enabled": True} and p["allow_promotion_codes"] is True
    assert "not refunded" in p["custom_text"]["submit"]["message"]  # said beside Checkout's button
    assert p["success_url"] == "http://testserver/plan?checkout=success&session_id={CHECKOUT_SESSION_ID}"
    assert p["cancel_url"] == "http://testserver/plan"
    assert p["metadata"] == {"account_id": alice["id"], "plan": "plus"}
    # after a subscription ended, the next checkout reuses the Stripe customer
    completed(client, stripe, alice["id"], sub(status="canceled"))
    assert client.post("/api/billing/checkout", json={"price": "pro_month"}).status_code == 200
    p = stripe.sessions[-1]
    assert p["customer"] == "cus_1" and "customer_email" not in p and p["customer_update"]["address"] == "auto"


def test_portal(client, stripe):
    alice = signed_up(client)
    assert client.post("/api/billing/portal").status_code == 404
    completed(client, stripe, alice["id"])
    r = client.post("/api/billing/portal")
    assert r.status_code == 200 and r.json()["url"].startswith("https://billing.stripe.test/")
    assert stripe.portals[-1] == {"customer": "cus_1", "return_url": "http://testserver/plan"}


def test_portal_flows(client, stripe):
    """Each action opens the portal on its own page and comes back to /plan."""
    alice = signed_up(client)
    completed(client, stripe, alice["id"])

    def flow(**body):
        r = client.post("/api/billing/portal", json=body)
        return r.status_code, (stripe.portals[-1].get("flow_data") if r.status_code == 200 else r.json()["detail"])

    done = {"type": "redirect", "redirect": {"return_url": "http://testserver/plan?billing=payment"}}
    assert flow(flow="payment") == (200, {"type": "payment_method_update", "after_completion": done})
    code, data = flow(flow="cancel")
    assert code == 200 and data["subscription_cancel"] == {"subscription": "sub_1"}
    assert data["after_completion"]["redirect"]["return_url"].endswith("/plan?billing=cancel")
    # a switch names the subscription's item and the price the key stands for; Stripe confirms it
    code, data = flow(flow="switch", price="pro_year")
    assert code == 200 and data["type"] == "subscription_update_confirm"
    assert data["subscription_update_confirm"] == {
        "subscription": "sub_1", "items": [{"id": "si_1", "price": "price_pro_y", "quantity": 1}]}
    assert flow(flow="switch", price="plus_month")[0] == 400        # the plan they have
    assert flow(flow="switch", price="nope")[0] == 400
    assert flow(flow="refund")[0] == 400
    # a portal whose configuration allows no deep link still opens, on its home
    stripe.refuse_flows = True
    assert flow(flow="switch", price="pro_month") == (200, None)
    stripe.refuse_flows = False
    # set to end: nothing to cancel or switch until it is kept
    stripe.subs["sub_1"]["cancel_at_period_end"] = True
    send(client, "customer.subscription.updated", {"id": "sub_1"})
    assert flow(flow="cancel")[0] == 409 and flow(flow="switch", price="pro_month")[0] == 409
    assert flow(flow="payment")[0] == 200
    stripe.down = True
    assert flow(flow="payment")[0] == 502


def test_keep_undoes_a_scheduled_cancel(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    assert client.post("/api/billing/keep").status_code == 409      # not set to end
    stripe.subs["sub_1"].update(cancel_at_period_end=False, cancel_at=PERIOD_END)  # the portal set a date
    send(client, "customer.subscription.updated", {"id": "sub_1"})
    assert row(alice["id"])["cancel_at_period_end"] == 1
    page = client.get("/plan").text
    assert "data-keep" in page and "Ending" in page and "does not renew" in page
    assert "data-portal=cancel" not in page and "data-switch=" not in page
    stripe.down = True
    assert client.post("/api/billing/keep").status_code == 502
    stripe.down = False
    assert client.post("/api/billing/keep").json() == {"ok": True}
    assert stripe.updates == [("sub_1", {"cancel_at_period_end": False}), ("sub_1", {"cancel_at": ""})]
    assert row(alice["id"])["cancel_at_period_end"] == 0
    assert client.get("/api/me").json()["account"]["renews_at"] == "2030-01-01T00:00:00.000Z"
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT 1 FROM audit WHERE event = 'billing.keep' AND account_id = ?", (alice["id"],)).fetchone()
    assert "Your plan renews again" in client.get("/plan?billing=keep").text


def test_details_payment_method_and_invoices(client, stripe):
    alice = signed_up(client)
    assert client.get("/api/billing/details").json() == {"payment_method": None, "invoices": []}  # Stripe not asked
    completed(client, stripe, alice["id"])
    stripe.invoices = [
        {"id": "in_3", "customer": "cus_1", "status": "draft", "created": PERIOD_END, "total": 500},
        {"id": "in_2", "customer": "cus_1", "status": "open", "number": "GAMMA-0002", "created": PERIOD_END,
         "total": 525, "currency": "usd", "hosted_invoice_url": "https://invoice.stripe.test/in_2",
         "invoice_pdf": "https://invoice.stripe.test/in_2.pdf", "lines": {"data": [{"description": "1 × Plus"}]}},
        {"id": "in_1", "customer": "cus_1", "status": "paid", "number": "GAMMA-0001", "created": PERIOD_END, "total": 525},
        {"id": "in_x", "customer": "cus_other", "status": "paid", "created": PERIOD_END, "total": 1},
    ]
    stripe.customers["cus_1"] = {"id": "cus_1", "invoice_settings": {"default_payment_method": {
        "type": "sepa_debit", "sepa_debit": {"last4": "3000"}}}}
    d = client.get("/api/billing/details").json()
    assert d["payment_method"] == {"kind": "sepa_debit", "brand": "", "last4": "3000", "exp_month": None, "exp_year": None}
    assert [i["id"] for i in d["invoices"]] == ["in_2", "in_1"]     # no draft, nobody else's
    assert d["invoices"][0] == {"id": "in_2", "number": "GAMMA-0002", "created": "2030-01-01T00:00:00.000Z",
                                "description": "1 × Plus", "total": 525, "currency": "usd", "status": "open",
                                "url": "https://invoice.stripe.test/in_2", "pdf": "https://invoice.stripe.test/in_2.pdf"}
    # the subscription's own payment method is the one charged
    stripe.subs["sub_1"]["default_payment_method"] = {"type": "card", "card": {
        "brand": "visa", "last4": "4242", "exp_month": 8, "exp_year": 2028}}
    assert client.get("/api/billing/details").json()["payment_method"] == {
        "kind": "card", "brand": "visa", "last4": "4242", "exp_month": 8, "exp_year": 2028}
    stripe.down = True
    assert client.get("/api/billing/details").status_code == 502
    with TestClient(client.app, base_url="http://testserver") as anon:
        assert anon.get("/api/billing/details").status_code == 401


# --- the webhook --------------------------------------------------------------

def test_webhook_signature_and_duplicates(client, stripe):
    alice = signed_up(client)
    assert send(client, "invoice.paid", {}, signature="forged").status_code == 400
    stripe.subs["sub_1"] = sub()
    obj = {"object": "checkout.session", "client_reference_id": alice["id"], "customer": "cus_1", "subscription": "sub_1"}
    assert send(client, "checkout.session.completed", obj, "evt_a").json() == {"outcome": "applied"}
    assert send(client, "checkout.session.completed", obj, "evt_a").json() == {"outcome": "duplicate"}
    assert stripe.retrieved == ["sub_1"]  # the repeat never reached Stripe
    assert send(client, "customer.created", {"id": "cus_1"}, "evt_b").json() == {"outcome": "ignored"}
    with closing(db.connect()) as conn:
        events = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM billing_events")}
    assert events["evt_a"]["outcome"] == "applied" and events["evt_a"]["account_id"] == alice["id"]
    assert events["evt_b"]["outcome"] == "ignored"


def test_webhook_stripe_down_rolls_back(client, stripe):
    """A delivery Stripe cannot be read for is refused and not recorded, so
    Stripe's retry is handled."""
    alice = signed_up(client)
    stripe.subs["sub_1"] = sub()
    stripe.down = True
    obj = {"client_reference_id": alice["id"], "customer": "cus_1", "subscription": "sub_1"}
    assert send(client, "checkout.session.completed", obj, "evt_r").status_code == 502
    stripe.down = False
    assert send(client, "checkout.session.completed", obj, "evt_r").json() == {"outcome": "applied"}


def test_completed_binds_and_claims_carry_the_plan(client, stripe):
    from test_oidc import CALLBACK, decode, pkce, signed_in_code
    alice = signed_up(client)
    assert completed(client, stripe, alice["id"]) == "applied"
    r = row(alice["id"])
    assert (r["stripe_customer_id"], r["stripe_subscription_id"], r["plan"], r["status"]) == ("cus_1", "sub_1", "plus", "active")
    assert r["current_period_end"] == "2030-01-01T00:00:00.000Z" and r["seats"] == 1
    assert plan_of(alice["id"]) == "plus" and alice["id"] in stripe.plan_changed
    me = client.get("/api/me").json()["account"]
    assert me["plan"] == "plus" and me["plan_source"] == "stripe" and me["granted_plan"] == "free"
    assert me["renews_at"] == "2030-01-01T00:00:00.000Z" and me["cancel_at"] is None
    verifier, challenge = pkce()
    code = signed_in_code(client, challenge)
    tokens = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
                                         "client_id": config.DESKTOP_CLIENT_ID, "code_verifier": verifier}).json()
    assert decode(tokens["id_token"])["plan"] == "plus"
    with closing(db.connect()) as conn:
        audit = conn.execute("SELECT detail FROM audit WHERE event = 'account.plan' AND account_id = ?",
                             (alice["id"],)).fetchall()
    assert any("source=stripe" in a["detail"] for a in audit)


def test_subscription_updated_reads_stripe_not_the_body(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    stripe.subs["sub_1"] = sub(price="price_pro_y", quantity=3, cancel=True)
    # the body claims something else; the handler reads the subscription from Stripe
    r = send(client, "customer.subscription.updated", {**sub(price="price_plus_m"), "status": "canceled"})
    assert r.json() == {"outcome": "applied"}
    r = row(alice["id"])
    assert (r["plan"], r["price_id"], r["seats"], r["cancel_at_period_end"], r["status"]) == ("pro", "price_pro_y", 3, 1, "active")
    assert plan_of(alice["id"]) == "pro"
    me = client.get("/api/me").json()["account"]
    assert me["cancel_at"] == "2030-01-01T00:00:00.000Z" and me["renews_at"] is None


def test_metadata_finds_the_account_before_checkout_completes(client, stripe):
    """``customer.subscription.created`` may arrive first: the subscription's
    metadata (set at checkout) names the account."""
    alice = signed_up(client)
    stripe.subs["sub_1"] = sub(metadata={"account_id": alice["id"]})
    assert send(client, "customer.subscription.created", {"id": "sub_1"}).json() == {"outcome": "applied"}
    assert plan_of(alice["id"]) == "plus"
    # an invoice event names the subscription in either place
    assert send(client, "invoice.paid", {"parent": {"subscription_details": {"subscription": "sub_1"}}}).json() == {"outcome": "applied"}
    # a subscription no account can be found for changes nothing
    stripe.subs["sub_x"] = sub(id="sub_x", customer="cus_x")
    assert send(client, "customer.subscription.created", {"id": "sub_x"}).json() == {"outcome": "unknown"}


def test_past_due_grace_then_drop(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    stripe.subs["sub_1"]["status"] = "past_due"
    send(client, "invoice.payment_failed", {"subscription": "sub_1"})
    r = row(alice["id"])
    assert r["status"] == "past_due" and r["past_due_since"]
    assert plan_of(alice["id"]) == "plus"  # in grace
    first = r["past_due_since"]
    send(client, "invoice.payment_failed", {"subscription": "sub_1"})
    assert row(alice["id"])["past_due_since"] == first  # a second failure keeps the start
    summary = client.get("/api/billing/me").json()
    assert summary["subscription"]["grace_ends_at"] > first
    # the grace runs out with no event: the hourly tick drops the plan
    sql("UPDATE subscriptions SET past_due_since = ? WHERE account_id = ?", db.after(-8 * 86400), alice["id"])
    with closing(db.connect()) as conn:
        billing.tick(conn)
        conn.commit()
    assert plan_of(alice["id"]) == "free"
    # paid again: back to plus, and the grace mark is cleared
    stripe.subs["sub_1"]["status"] = "active"
    send(client, "invoice.paid", {"subscription": "sub_1"})
    assert plan_of(alice["id"]) == "plus" and row(alice["id"])["past_due_since"] is None


def test_cancel_drops_but_a_grant_survives(client, stripe):
    alice = signed_up(client)
    make_admin("alice")
    completed(client, stripe, alice["id"], sub(price="price_pro_m"))
    assert plan_of(alice["id"]) == "pro"
    # an admin's courtesy grant below the paid plan changes nothing yet
    assert client.patch(f"/api/admin/accounts/{alice['id']}", json={"plan": "plus"}).json()["account"]["plan"] == "pro"
    stripe.subs["sub_1"]["status"] = "canceled"
    stripe.subs["sub_1"]["ended_at"] = PERIOD_END
    send(client, "customer.subscription.deleted", {"id": "sub_1"})
    r = row(alice["id"])
    assert r["status"] == "canceled" and r["ended_at"] == "2030-01-01T00:00:00.000Z"
    assert plan_of(alice["id"]) == "plus"  # the grant
    me = client.get("/api/me").json()["account"]
    assert me["plan_source"] == "granted" and me["renews_at"] is None
    client.patch(f"/api/admin/accounts/{alice['id']}", json={"plan": "free"})
    assert plan_of(alice["id"]) == "free"


def test_late_event_of_an_old_subscription_is_stale(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"], sub(id="sub_old", status="canceled"))
    completed(client, stripe, alice["id"], sub(id="sub_new"))
    assert send(client, "customer.subscription.deleted", {"id": "sub_old"}).json() == {"outcome": "stale"}
    assert row(alice["id"])["stripe_subscription_id"] == "sub_new" and plan_of(alice["id"]) == "plus"


def test_mismatch(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = signed_up(other, "bob")
    # a session naming bob whose customer is alice's
    assert completed(client, stripe, bob["id"], sub(id="sub_2", customer="cus_1")) == "mismatch"
    assert row(bob["id"]) is None and plan_of(bob["id"]) == "free"
    assert row(alice["id"])["stripe_subscription_id"] == "sub_1"
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT outcome FROM billing_events ORDER BY received_at DESC LIMIT 1").fetchone()[0] == "mismatch"


# --- keeping the copy right ---------------------------------------------------

def test_reconcile_repairs_and_runs_daily(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    stripe.subs["sub_1"]["status"] = "canceled"  # the cancel event never arrived
    stripe.subs["sub_other"] = sub(id="sub_other", customer="cus_9")  # nobody's
    with closing(db.connect()) as conn:
        billing.tick(conn)
        conn.commit()
    assert row(alice["id"])["status"] == "canceled" and plan_of(alice["id"]) == "free"
    assert stripe.list_calls == 1
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key = ?", (billing.RECONCILED_KEY,)).fetchone()
        billing.tick(conn)  # not due again for a day
        conn.commit()
    assert stripe.list_calls == 1
    with closing(db.connect()) as conn:
        assert billing.reconcile(conn) == {"seen": 2, "repaired": 0, "unknown": 1}


def test_billing_me_refreshes_an_old_row(client, stripe):
    alice = signed_up(client)
    d = client.get("/api/billing/me").json()
    assert d["enabled"] and d["sells"] == ["lite", "plus", "pro"] and d["subscription"] is None and d["plan"] == "free"
    assert d["shared_url"] == "https://share.gammapdf.test"
    assert d["prices"] == config.PLAN_PRICES_USD and d["hosted"] is None
    completed(client, stripe, alice["id"])
    d = client.get("/api/billing/me").json()
    assert d["subscription"]["status"] == "active" and d["subscription"]["interval"] == "month"
    assert d["subscription"]["amount_usd"] == 5 and d["plan_source"] == "stripe" and d["has_customer"]
    n = len(stripe.retrieved)
    stripe.subs["sub_1"]["status"] = "past_due"
    assert client.get("/api/billing/me").json()["subscription"]["status"] == "active"  # fresh: not re-read
    assert len(stripe.retrieved) == n
    sql("UPDATE subscriptions SET updated_at = ? WHERE account_id = ?", db.after(-2 * 3600), alice["id"])
    stripe.down = True  # a failed refresh is ignored
    assert client.get("/api/billing/me").json()["subscription"]["status"] == "active"
    stripe.down = False
    assert client.get("/api/billing/me").json()["subscription"]["status"] == "past_due"
    # back from the portal the page asks for a fresh read, however new the row
    stripe.subs["sub_1"]["status"] = "active"
    assert client.get("/api/billing/me").json()["subscription"]["status"] == "past_due"
    sql("UPDATE subscriptions SET updated_at = ? WHERE account_id = ?", db.after(-60), alice["id"])
    assert client.get("/api/billing/me?fresh=1").json()["subscription"]["status"] == "active"


def test_off_without_a_secret(client, monkeypatch):
    register(client)
    monkeypatch.setattr(config, "STRIPE_SECRET", "")
    d = client.get("/api/billing/me").json()
    assert d["enabled"] is False and d["sells"] == []
    assert client.post("/api/billing/webhook", content=b"{}").status_code == 503
    page = client.get("/plan").text
    assert "Coming soon" in page and "data-choose=" not in page
    assert "Only the Free plan is available right now. Paid plans are coming soon." in page


# --- the Plan page ------------------------------------------------------------

def test_plan_page_states(client, stripe):
    with TestClient(client.app, base_url="http://testserver") as anon:
        r = anon.get("/plan", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("/login")
    alice = signed_up(client)
    page = client.get("/plan")
    assert page.status_code == 200
    assert "Choose Lite" in page.text and "Choose Plus" in page.text and "Choose Pro" in page.text
    assert "$20" in page.text and "$50" in page.text and "1 GB" in page.text and "self-host" in page.text
    assert "alice-user.gammapdf.test" in page.text and "Your current plan" in page.text
    # a free account Stripe does not know: the parameter alone is not a checkout
    page = client.get("/plan?checkout=success").text
    assert "Setting up your server" not in page and "If you have just paid" in page and "Choose Plus" in page
    completed(client, stripe, alice["id"], sub(price="price_pro_m"))
    assert "Setting up your server" in client.get("/plan?checkout=success").text
    page = client.get("/plan").text
    assert "Setting up your server" not in page and "Subscription" in page and "Next payment" in page
    assert "data-portal=payment" in page and "data-portal=cancel" in page and "Billing history" in page
    assert "not refunded" in page and "/terms/" in page
    stripe.hosted[alice["id"]] = {"url": "https://alice.gammapdf.test", "state": "running", "read_only": False,
                                  "limits": {"quota_mb": 6144, "offsite": {"interval_s": 86400, "keep": 7}},
                                  "report": {"uploads_bytes": 3 * 1024 ** 3}, "reported_at": db.now()}
    page = client.get("/plan?checkout=success").text
    assert "Thank you" in page and "3.0 GB of 6 GB" in page and "https://alice.gammapdf.test" in page
    assert "Daily off-site copies, 7 kept" in page
    assert "Your plan change is saved" in client.get("/plan?billing=switch").text
    assert "Done." not in client.get("/plan?billing=nonsense").text
    stripe.subs["sub_1"]["status"] = "past_due"
    send(client, "invoice.payment_failed", {"subscription": "sub_1"})
    page = client.get("/plan").text
    assert "Fix payment" in page and "read-only on" in page
    stripe.subs["sub_1"]["status"] = "canceled"
    send(client, "customer.subscription.deleted", {"id": "sub_1"})
    stripe.hosted[alice["id"]].update(read_only=True, state="read_only", stops_at=db.after(30 * 86400),
                                      deletes_at=db.after(90 * 86400))
    page = client.get("/plan").text
    assert "data-resume='pro_month'" in page and "Readable until" in page
    assert "https://alice.gammapdf.test/?settings=backups" in page
    assert "30 days left" in page and "Deleted on" in page and "Billing history" in page
    stripe.subs["sub_1"]["status"] = "unpaid"
    send(client, "customer.subscription.updated", {"id": "sub_1"})
    page = client.get("/plan").text
    # unpaid is still held: paid in the portal, so no Resume and no plan to choose (checkout would answer 409)
    assert "retries ran out" in page and "Fix payment" in page
    assert "data-resume=" not in page and "data-choose=" not in page
    assert client.post("/api/billing/checkout", json={"price": "plus_month"}).status_code == 409


def test_plan_page_for_a_library_on_the_shared_server(client, stripe, monkeypatch):
    """Lite and Plus: no server to wait for or to retire, the shared
    server's address and the plan's allowance instead."""
    alice = signed_up(client)
    page = client.get("/plan").text
    assert "Your library on Gamma Cloud, always on at <b>share.gammapdf.test</b>" in page
    assert "A Gamma server of your own at <b>alice-user.gammapdf.test</b>" in page
    completed(client, stripe, alice["id"])
    page = client.get("/plan?checkout=success").text
    assert "Setting up your server" not in page and "Thank you" in page and "<h2>Your server" not in page
    assert "Your library" in page and "6 GB, 100 MB per file" in page
    assert "href='https://share.gammapdf.test/api/auth/cloud/start?next=/'" in page   # Open signs in afresh
    assert "data-switch=pro>Upgrade to Pro" in page
    assert "share.gammapdf.test" in client.get("/").text                               # the Overview's plan card
    stripe.subs["sub_1"]["cancel_at_period_end"] = True
    send(client, "customer.subscription.updated", {"id": "sub_1"})
    assert "goes back to the free allowance" in client.get("/plan").text
    stripe.subs["sub_1"]["status"] = "past_due"
    send(client, "invoice.payment_failed", {"subscription": "sub_1"})
    assert "Your plan ends on" in client.get("/plan").text and "Your plan ends on" in client.get("/").text
    stripe.subs["sub_1"]["status"] = "canceled"
    send(client, "customer.subscription.deleted", {"id": "sub_1"})
    page = client.get("/plan").text
    assert "What happens to your library" in page and "free allowance" in page and "data-resume='plus_month'" in page
    assert "Readable until" not in page and "Export" not in page
    # each plan is sold only while its home is configured
    monkeypatch.setattr(config, "HOSTED_DOMAIN", "")
    page = client.get("/plan").text
    assert "Choose Plus" in page and "Choose Pro" not in page and "Coming soon" in page
    assert client.get("/api/billing/me").json()["sells"] == ["lite", "plus"]
    assert client.post("/api/billing/checkout", json={"price": "pro_month"}).status_code == 503
    assert client.post("/api/billing/checkout", json={"price": "lite_year"}).status_code == 200


def test_plan_page_paused(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"], sub(status="paused"))
    page = client.get("/plan").text
    assert "Payment collection is paused" in page and "data-portal" in page
    assert "data-resume=" not in page and "data-choose=" not in page and "plan ended" not in page
    assert client.post("/api/billing/checkout", json={"price": "plus_month"}).status_code == 409
    assert client.post("/api/billing/portal").status_code == 200


def test_resume_only_when_checkout_accepts(client, stripe, monkeypatch):
    alice = signed_up(client)
    completed(client, stripe, alice["id"], sub(status="canceled"))
    assert "data-resume='plus_month'" in client.get("/plan").text
    monkeypatch.setattr(config, "APP_URL", "")  # no shared server, so Plus is not sold: no Resume either
    page = client.get("/plan").text
    assert "data-resume=" not in page and "Coming soon" in page


# --- plans on sale ------------------------------------------------------------

def test_why_not_names_the_first_reason(client, stripe, monkeypatch):
    assert [billing.why_not(p) for p in ("lite", "plus", "pro")] == ["", "", ""]
    assert billing.why_not("free") == "not a paid plan" and not billing.can_sell("free")
    monkeypatch.setitem(config.STRIPE_PRICES, "lite_year", ("lite", "year", ""))
    assert billing.why_not("lite") == "no yearly Stripe price id"
    monkeypatch.setitem(config.STRIPE_PRICES, "lite_month", ("lite", "month", ""))
    assert billing.why_not("lite") == "no monthly or yearly Stripe price id"
    monkeypatch.setattr(config, "APP_URL", "")
    assert billing.why_not("plus") == "no shared server address (GAMMA_CLOUD_APP_URL)"
    monkeypatch.setattr(config, "HOSTED_DOMAIN", "")
    assert billing.why_not("pro") == "hosting is off (GAMMA_CLOUD_HOSTED_DOMAIN)"
    monkeypatch.setattr(config, "STRIPE_SECRET", "")
    assert billing.why_not("pro") == "billing is off (no Stripe secret)"
    set_setting("plans_on_sale", "lite plus")
    assert billing.why_not("pro") == "held back by the operator" and not billing.can_sell("pro")


def test_a_held_back_plan_is_not_sold(client, stripe):
    """The operator's switch: no card, no checkout, and the Admin page says
    why; the page says plainly when nothing paid is for sale."""
    alice = signed_up(client)
    make_admin("alice")
    set_setting("plans_on_sale", "lite plus")
    page = client.get("/plan").text
    assert "Choose Plus" in page and "Choose Pro" not in page and "Coming soon" in page
    assert client.get("/api/billing/me").json()["sells"] == ["lite", "plus"]
    assert client.post("/api/billing/checkout", json={"price": "pro_month"}).status_code == 503
    rows = {p["plan"]: p for p in client.get("/api/admin/settings").json()["plans"]}
    assert rows["plus"] == {"plan": "plus", "on_sale": True, "sellable": True, "reason": ""}
    assert rows["pro"] == {"plan": "pro", "on_sale": False, "sellable": False, "reason": "held back by the operator"}
    r = client.patch("/api/admin/settings", json={"plans_on_sale": ""})
    assert r.status_code == 200 and not any(p["on_sale"] for p in r.json()["plans"])
    page = client.get("/plan").text
    assert "data-choose=" not in page and "Only the Free plan is available right now" in page
    # someone who holds a plan keeps it
    completed(client, stripe, alice["id"])
    assert plan_of(alice["id"]) == "plus" and "Your plan stays as it is" in client.get("/plan").text


def test_a_switch_needs_the_plan_on_sale(client, stripe, monkeypatch):
    """A subscriber cannot pay for a plan that cannot be provisioned, and
    still switches the billing period of the plan they hold."""
    alice = signed_up(client)
    completed(client, stripe, alice["id"], sub(price="price_plus_y"))
    monkeypatch.setattr(config, "HOSTED_DOMAIN", "")   # Pro cannot be provisioned
    set_setting("plans_on_sale", "lite")               # and Plus is held back
    page = client.get("/plan").text
    assert "data-switch=pro" not in page and "Coming soon" in page and "data-switch=lite>Switch to Lite" in page
    assert "data-switch=plus data-m>Switch to monthly billing" in page
    assert client.post("/api/billing/portal", json={"flow": "switch", "price": "pro_month"}).status_code == 503
    r = client.post("/api/billing/portal", json={"flow": "switch", "price": "plus_month"})
    assert r.status_code == 200
    assert stripe.portals[-1]["flow_data"]["subscription_update_confirm"]["items"][0]["price"] == "price_plus_m"
    # without the other period's price id there is no such switch to offer
    monkeypatch.setitem(config.STRIPE_PRICES, "plus_month", ("plus", "month", ""))
    page = client.get("/plan").text
    assert "Switch to monthly billing" not in page and "Your current plan" in page
    assert client.post("/api/billing/portal", json={"flow": "switch", "price": "plus_month"}).status_code == 400


def test_the_website_reads_the_plans_on_sale(client, stripe):
    r = client.get("/api/plans", headers={"Origin": "https://gammapdf.com"})
    assert r.status_code == 200
    assert r.json() == {"plans": {"lite": {"on_sale": True}, "plus": {"on_sale": True}, "pro": {"on_sale": True}}}
    assert r.headers["access-control-allow-origin"] == "https://gammapdf.com"
    assert r.headers["cache-control"] == "public, max-age=300"
    set_setting("plans_on_sale", "lite plus")
    assert client.get("/api/plans").json()["plans"]["pro"] == {"on_sale": False}


# --- grants that end ----------------------------------------------------------

def test_a_grant_that_ends(client, stripe):
    signed_up(client)
    make_admin("alice")
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = signed_up(other, "bob")
    url = f"/api/admin/accounts/{bob['id']}"
    r = client.patch(url, json={"plan": "pro", "granted_until": "2099-01-01"})
    assert r.json()["account"]["granted_until"] == "2099-01-01T00:00:00.000Z" and r.json()["account"]["plan"] == "pro"
    assert client.patch(url, json={"granted_until": "2000-01-01"}).status_code == 400   # passed
    assert client.patch(url, json={"granted_until": "soon"}).status_code == 400
    # the date alone moves the end of the grant the account has
    r = client.patch(url, json={"granted_until": "2098-06-01T12:00:00+02:00"}).json()["account"]
    assert r["granted_until"] == "2098-06-01T10:00:00.000Z" and r["granted_plan"] == "pro"
    # a plan from the select is a new grant, with no end
    assert client.patch(url, json={"plan": "plus"}).json()["account"]["granted_until"] is None
    assert client.patch(url, json={"plan": "free", "granted_until": "2099-01-01"}).status_code == 400
    client.patch(url, json={"plan": "pro", "granted_until": "2099-01-01"})
    # when it ends, a subscription keeps its plan and the hosted side hears of the change
    completed(client, stripe, bob["id"], sub(customer="cus_b"), customer="cus_b")
    assert plan_of(bob["id"]) == "pro"
    sql("UPDATE accounts SET granted_until = ? WHERE id = ?", db.after(-60), bob["id"])
    stripe.plan_changed.clear()
    with closing(db.connect()) as conn:
        assert accounts.expire_grants(conn) == 1
        conn.commit()
        row = conn.execute("SELECT granted_plan, granted_until FROM accounts WHERE id = ?", (bob["id"],)).fetchone()
        assert tuple(row) == ("free", None)
        assert conn.execute("SELECT detail FROM audit WHERE event = 'account.grant_expired' AND account_id = ?",
                            (bob["id"],)).fetchone()["detail"] == "pro"
        assert accounts.expire_grants(conn) == 0
    assert plan_of(bob["id"]) == "plus" and stripe.plan_changed == [bob["id"]]


def test_plan_page_never_calls_stripe(client, stripe):
    """The page renders the stored copy; GET /api/billing/me (which its
    script calls) is what refreshes an old row."""
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    sql("UPDATE subscriptions SET updated_at = ? WHERE account_id = ?", db.after(-2 * 3600), alice["id"])
    stripe.subs["sub_1"]["status"] = "past_due"
    n = len(stripe.retrieved)
    page = client.get("/plan").text
    assert len(stripe.retrieved) == n and "Next payment" in page and "SHOWN" in page
    assert client.get("/api/billing/me").json()["subscription"]["status"] == "past_due"
    assert len(stripe.retrieved) == n + 1


def _hosted_row(account_id, state="running"):
    ts = db.now()
    sql("INSERT OR IGNORE INTO hosts (id, name, token_hash, created_at) VALUES ('h1', 'host-1', 'x', ?)", ts)
    sql("INSERT INTO hosted_servers (id, account_id, label, host_id, state, state_changed_at, created_at, deleted_at) "
        "VALUES (?, ?, 'alice', 'h1', ?, ?, ?, ?)", f"srv-{account_id}", account_id, state, ts, ts,
        ts if state == "deleted" else None)


def test_rename_refused_while_a_hosted_server_exists(client, stripe):
    alice = signed_up(client)
    make_admin("alice")
    _hosted_row(alice["id"])
    r = client.post("/api/me/username", json={"username": "alicia", "password": "correct horse battery"})
    assert r.status_code == 409 and "contact support" in r.json()["detail"]
    r = client.patch(f"/api/admin/accounts/{alice['id']}", json={"username": "alicia"})
    assert r.status_code == 409
    assert client.get("/api/me").json()["account"]["username"] == "alice"
    sql("UPDATE hosted_servers SET state = 'deleted', deleted_at = ? WHERE account_id = ?", db.now(), alice["id"])
    r = client.post("/api/me/username", json={"username": "alicia", "password": "correct horse battery"})
    assert r.status_code == 200, r.text


def test_verification_tells_the_hosted_side(client, stripe):
    from conftest import invite
    alice = register(client, code=invite(plan="pro"))
    assert plan_of(alice["id"]) == "pro"
    stripe.plan_changed.clear()
    verify(client)
    assert stripe.plan_changed == [alice["id"]]
    with closing(db.connect()) as conn:  # a second confirmation is not news
        accounts.mark_verified(conn, alice["id"])
        conn.commit()
    assert stripe.plan_changed == [alice["id"]]


def test_plan_page_pro_members_and_granted(client, stripe):
    alice = signed_up(client)
    make_admin("alice")
    client.patch(f"/api/admin/accounts/{alice['id']}", json={"plan": "plus"})
    page = client.get("/plan").text
    assert "granted" in page and "Choose Pro" in page and "Choose Plus" not in page
    completed(client, stripe, alice["id"], sub(price="price_pro_y"))
    stripe.hosted[alice["id"]] = {"url": "https://alice.gammapdf.test", "state": "running", "read_only": False,
                                  "limits": {}, "report": {"accounts": 4}}
    page = client.get("/plan").text
    assert "4 of 10" in page and "billed yearly" in page and "$200 a year" in page


def test_plan_page_cards_switch_for_a_subscriber(client, stripe):
    """A paid-up subscriber sees the cards as switches: the other plans, and
    their own plan's other billing period. The toggle starts on theirs."""
    alice = signed_up(client)
    completed(client, stripe, alice["id"], sub(price="price_plus_y"))
    page = client.get("/plan").text
    assert "Change plan" in page and "data-choose=" not in page
    assert "data-switch=pro>Upgrade to Pro" in page and "data-switch=lite>Switch to Lite" in page
    assert "data-switch=plus data-m>Switch to monthly billing" in page and "class='plans y'" in page
    assert client.get("/api/billing/me").json()["subscription"]["can_switch"] is True
    # a failed payment is fixed first: no switches
    stripe.subs["sub_1"]["status"] = "past_due"
    send(client, "invoice.payment_failed", {"subscription": "sub_1"})
    page = client.get("/plan").text
    assert "data-switch=" not in page and "Change plan" not in page and "Payment failed" in page


def test_overview_plan_card_and_alert(client, stripe):
    alice = signed_up(client)
    page = client.get("/").text
    assert "A paid plan adds one that is always on" in page and "Self-host instead" in page
    assert "Plan &amp; billing" in page                             # the side navigation
    completed(client, stripe, alice["id"], sub(price="price_pro_m"))
    stripe.hosted[alice["id"]] = {"url": "https://alice.gammapdf.test", "state": "running", "read_only": False,
                                  "limits": {"quota_mb": 6144}, "report": {"uploads_bytes": 512 * 1024 ** 2}}
    page = client.get("/").text
    assert "Renews" in page and "$20 a month" in page and "512 MB of 6 GB" in page and "alice.gammapdf.test" in page
    assert "Self-host instead" not in page and "payment failed" not in page
    stripe.subs["sub_1"]["status"] = "past_due"
    send(client, "invoice.payment_failed", {"subscription": "sub_1"})
    page = client.get("/").text
    assert "Your last payment failed" in page and "href='/plan'>Plan and billing" in page
    # the settings page says what deleting a paying account does
    stripe.subs["sub_1"]["status"] = "active"
    send(client, "invoice.paid", {"subscription": "sub_1"})
    assert "ends at once, without a refund" in client.get("/settings").text


# --- the admin's Billing tab --------------------------------------------------

def test_admin_subscriptions(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    assert client.get("/api/admin/subscriptions").status_code == 403
    assert client.post(f"/api/admin/subscriptions/{alice['id']}/refresh").status_code == 403
    make_admin("alice")
    d = client.get("/api/admin/subscriptions").json()
    assert d["enabled"] and [s["username"] for s in d["subscriptions"]] == ["alice"]
    assert d["subscriptions"][0]["stripe_customer_id"] == "cus_1"
    assert d["summary"] == {"by_plan": {"lite": 0, "plus": 1, "pro": 0}, "paying": 1, "mrr_usd": 5.0,
                            "past_due": 0, "ending": 0}
    events = client.get("/api/admin/billing-events").json()["events"]
    assert [(e["type"], e["outcome"], e["username"]) for e in events] == [("checkout.session.completed", "applied", "alice")]
    assert client.get("/api/admin/subscriptions", params={"status": "canceled"}).json()["subscriptions"] == []
    stripe.subs["sub_1"]["status"] = "canceled"
    r = client.post(f"/api/admin/subscriptions/{alice['id']}/refresh")
    assert r.status_code == 200 and r.json()["subscription"]["status"] == "canceled"
    assert client.post("/api/admin/subscriptions/nobody/refresh").status_code == 404
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = signed_up(other, "bob")
        assert other.get("/api/admin/billing-events").status_code == 403
    completed(client, stripe, bob["id"], sub(id="sub_b", customer="cus_b", price="price_pro_y", cancel=True), customer="cus_b")
    s = client.get("/api/admin/subscriptions").json()["summary"]   # alice's is cancelled; a yearly Pro counts a twelfth
    assert s["by_plan"] == {"lite": 0, "plus": 0, "pro": 1} and s["mrr_usd"] == 16.67 and s["ending"] == 1
    page = client.get("/admin").text
    assert "function loadBilling" in page and "tab-billing" in page and "dashboard.stripe.com" in page


def test_manage_commands(client, stripe, capsys):
    import manage
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    stripe.subs["sub_1"]["status"] = "canceled"
    manage.main(["billing-sync"])
    assert "repaired 1" in capsys.readouterr().out
    manage.main(["subscriptions", "--status", "canceled"])
    out = capsys.readouterr().out
    assert "alice" in out and "cus_1" in out and "effective=free" in out


# --- deleting a paying account ------------------------------------------------

def test_self_delete_cancels_the_subscription_first(client, stripe):
    alice = signed_up(client)
    completed(client, stripe, alice["id"])
    stripe.down = True
    r = client.post("/api/me/delete", json={"password": "correct horse battery"})
    assert r.status_code == 502 and "not deleted" in r.json()["detail"]
    assert client.get("/api/me").status_code == 200  # still there, still signed in
    assert stripe.canceled == [] and row(alice["id"])["status"] == "active"
    stripe.down = False
    assert client.post("/api/me/delete", json={"password": "wrong password"}).status_code == 403
    assert stripe.canceled == []  # a wrong password reaches no Stripe call
    assert client.post("/api/me/delete", json={"password": "correct horse battery"}).status_code == 200
    assert stripe.canceled == ["sub_1"]
    r = row(alice["id"])
    assert r["status"] == "canceled" and r["ended_at"] and r["cancel_at_period_end"] == 0
    with closing(db.connect()) as conn:
        assert accounts.by_id_deleted(conn, alice["id"])
        events = [a["event"] for a in conn.execute("SELECT event FROM audit WHERE account_id = ?", (alice["id"],))]
    assert "billing.cancel" in events and events.index("billing.cancel") < events.index("account.delete")


def test_admin_delete_cancels_the_subscription_first(client, stripe, monkeypatch):
    signed_up(client)
    make_admin("alice")
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = signed_up(other, "bob")
    completed(client, stripe, bob["id"], sub(customer="cus_b"), customer="cus_b")
    # billing switched off with a subscription still live: nothing to cancel it with, so refused
    monkeypatch.setattr(config, "STRIPE_SECRET", "")
    assert client.post(f"/api/admin/accounts/{bob['id']}/delete").status_code == 502
    monkeypatch.setattr(config, "STRIPE_SECRET", "sk_test_x")
    stripe.down = True
    assert client.post(f"/api/admin/accounts/{bob['id']}/delete").status_code == 502
    with closing(db.connect()) as conn:
        assert accounts.by_id(conn, bob["id"])
    stripe.down = False
    assert client.post(f"/api/admin/accounts/{bob['id']}/delete").status_code == 200
    assert stripe.canceled == ["sub_1"] and row(bob["id"])["status"] == "canceled"
    # an ended subscription is not cancelled again; the account just goes
    with TestClient(client.app, base_url="http://testserver") as other:
        carol = signed_up(other, "carol")
    completed(client, stripe, carol["id"], sub(id="sub_c", customer="cus_c", status="canceled"), customer="cus_c")
    assert client.post(f"/api/admin/accounts/{carol['id']}/delete").status_code == 200
    assert stripe.canceled == ["sub_1"]


def test_purge_removes_the_hosted_server_and_the_subscription(client, stripe):
    """Both tables reference accounts: a purge must clear them, the live
    server going through hosted.purge_account (a delete job)."""
    signed_up(client)
    make_admin("alice")
    with TestClient(client.app, base_url="http://testserver") as other:
        bob = signed_up(other, "bob")
    completed(client, stripe, bob["id"], sub(customer="cus_b"), customer="cus_b")
    ts = db.now()
    sql("INSERT INTO hosts (id, name, token_hash, created_at) VALUES ('h1', 'host-1', 'x', ?)", ts)
    sql("INSERT INTO hosted_servers (id, account_id, label, host_id, state, state_changed_at, created_at) "
        "VALUES ('srv1', ?, 'bob', 'h1', 'running', ?, ?)", bob["id"], ts, ts)
    assert client.post(f"/api/admin/accounts/{bob['id']}/delete").status_code == 200
    r = client.post(f"/api/admin/accounts/{bob['id']}/purge")
    assert r.status_code == 200, r.text
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT 1 FROM accounts WHERE id = ?", (bob["id"],)).fetchone() is None
        assert conn.execute("SELECT 1 FROM hosted_servers WHERE account_id = ?", (bob["id"],)).fetchone() is None
        assert accounts.subscription(conn, bob["id"]) is None
        assert conn.execute("SELECT kind FROM fleet_jobs WHERE server_id = 'srv1' AND kind = 'delete'").fetchone()
        assert conn.execute("SELECT 1 FROM billing_events WHERE account_id = ?", (bob["id"],)).fetchone()
