# Billing: plans, Stripe and the Plan page

How the account server sells the Plus and Pro plans: the rule that turns a
grant and a subscription into the one plan every claim carries, the Stripe
wiring (Checkout, the Customer Portal, the webhook, the nightly
reconciliation), the portal's Plan page, the Admin page's Billing tab and
the website's pricing page. The design and the reasons behind the prices
are in [research/cloud-plans.md](../research/cloud-plans.md). The account
server itself is [cloud_accounts.md](cloud_accounts.md), and what a paid
plan provisions is [hosted.md](hosted.md).

Code: `cloud/gammacloud/billing.py` (Stripe and the rules),
`routers/billing.py` (`/api/billing/*`), `pages_billing.py` (the Plan page
and the Billing tab), the effective-plan rule in `accounts.py`. Tests:
`cloud/tests/test_billing.py`, against a fake Stripe client. Nothing else
in the repository speaks Stripe's vocabulary, and a Gamma server never
hears about money: it sees the `plan` claim and, when hosted, the limits
answer of `/api/hosted/sync`.

## The effective plan

`accounts.plan` is the plan every ID token, `/userinfo` and `/api/me`
answer carries, and it is computed. Two inputs feed it:

- `accounts.granted_plan`, what an admin or an invite gave. The Admin
  page's plan select and `manage.py set-plan` write it
  (`accounts.set_plan`, audited as `account.grant`); registration stores
  the invite's plan there (`accounts.create`). Schema step 9 back-filled it
  from `plan`.
- the subscription row's `plan`, but only while its status pays for it:
  `active` or `trialing`, or `past_due` for `config.GRACE_DAYS` (7) after
  `past_due_since` (`accounts.billed_plan`).

`accounts.recompute_plan(conn, account_id, actor, source)` sets `plan` to
the higher of the two by `config.PLAN_RANK` and audits a change as
`account.plan` with its source (`stripe`, `admin` or `invite`) and both
inputs. It calls `hosted.plan_changed` when the plan changed; billing passes
`notify=True` after every subscription write, so a grace period or a cancel
reaches the hosted server before the plan itself moves. A webhook therefore
never erases a courtesy grant, and a lapsed subscription falls back to the
grant, or to `free`, by itself. Nothing announces the end of a grace
period, so `billing.tick` recomputes every `past_due` row hourly.

`accounts.public` adds `granted_plan`, `plan_source` (`stripe` when the
subscription carries the plan, `granted`, or `free`), and `renews_at` /
`cancel_at`, the period end when the subscription renews or ends then. A
free account skips the subscription read.

## Tables

Schema step 9 (`db.py`):

| table | what |
|---|---|
| `subscriptions` | one row per account, the account server's copy of its Stripe subscription: `stripe_customer_id`, `stripe_subscription_id`, `price_id`, `plan` (what the price buys, `free` for a price no variable names), `status` (Stripe's word), `cancel_at_period_end`, `current_period_end`, `seats` (the item quantity), `past_due_since` (set when the status becomes `past_due`, kept through repeats, cleared when it leaves), `ended_at` (when it was canceled), `updated_at` (the last read from Stripe) |
| `billing_events` | every webhook delivery's event id (primary key), type, the account it concerned and the `outcome` |
| `accounts.granted_plan` | the courtesy grant above |

A new subscription after a cancel (Resume) replaces the row's
subscription id; the Stripe customer stays, so the next checkout reuses it.

## Stripe

**Configuration.** All in the environment (`config.py`), so billing is off
until it is set:

| variable | what |
|---|---|
| `GAMMA_CLOUD_STRIPE_SECRET` | the secret key (`sk_live_…` / `sk_test_…`); empty = billing off |
| `GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET` | the webhook endpoint's signing secret (`whsec_…`) |
| `GAMMA_CLOUD_STRIPE_PRICE_PLUS_MONTH`, `_PLUS_YEAR`, `_PRO_MONTH`, `_PRO_YEAR` | the four recurring Price ids; `config.STRIPE_PRICES` maps the key a browser sends (`plus_month`, …) to (plan, interval, price id) |
| `GAMMA_CLOUD_HOSTED_DOMAIN` | a paid plan is a hosted server, so checkout also needs hosting on |

`config.PLAN_PRICES_USD` is what the pages show; what is charged is the
Price in Stripe, so the two must agree. The SDK is `stripe` in
`cloud/requirements.txt`. It is imported only when billing makes its first
call, so a server without the secret runs without the package, and a
secret set on an image without it fails billing calls (logged), not the
server.

**The client.** `billing.client()` is a small wrapper over the SDK's
`StripeClient` with six calls (verify an event, create a Checkout
Session, create a portal session, retrieve, cancel and list
subscriptions) that answer plain dicts and raise `BillingError` when Stripe
does not answer. Tests swap it with `billing.set_client(fake)`. No call is
made while cloud.db's write lock is held: the routes commit the session
touch first, and the webhook and refresh read Stripe before they take the
lock.

**Checkout.** `POST /api/billing/checkout {price}` (portal session only,
rate limited per account) answers `{url}` and the page sends the browser
there. `billing.checkout_url` refuses while billing or hosting is off (503),
for an unknown price key or one without a price id (400), for an
unconfirmed e-mail (403: the hosted server would not sign it in), and while
the account holds a subscription in `active`, `trialing`, `past_due`,
`unpaid` or `paused` (409, pointing at Manage billing). The browser never
states a plan; it names a price key the server looks up.

The session is `mode: subscription` with one line item of the price and
`client_reference_id` = the account id. It names the account's Stripe
customer when there is one (with `customer_update` address and name
`auto`, which Stripe Tax needs), else the account's e-mail.
`automatic_tax` is on and promotion codes are allowed. `metadata` and
`subscription_data.metadata` are `{account_id, plan}`. The success URL is
`/plan?checkout=success&session_id={CHECKOUT_SESSION_ID}` and the cancel
URL `/plan`, both on `GAMMA_CLOUD_PUBLIC_URL`.

**The Customer Portal.** `POST /api/billing/portal` answers a portal
session for the account's customer (404 without one) that returns to
`/plan`. The portal does the payment method, switching between the four
prices, cancelling at period end, resuming and invoices; every change comes
back through the webhook. Configure it in the Stripe dashboard (Settings →
Billing → Customer portal) to allow exactly those, with the four prices as
the products a customer may switch between.

**The webhook.** `POST /api/billing/webhook` takes the raw body and the
`Stripe-Signature` header; it has no session and no CSRF check (it is in
`app._CROSS_ORIGIN_OK`). `billing.handle_webhook`:

1. checks the signature with the webhook secret (400 if it fails, 503 while
   billing is off);
2. answers `{"outcome": "duplicate"}` for an event id already in
   `billing_events`;
3. for a handled type, reads the subscription the event names from Stripe
   (502 if Stripe does not answer; nothing is recorded, so Stripe's retry is
   handled). The event body is never trusted for state: Stripe does not
   promise order, and a fresh read makes every event idempotent;
4. inserts the event id (a concurrent delivery that won the insert makes
   this one a duplicate), applies the subscription, records the outcome and
   commits.

The handled types are `checkout.session.completed`,
`customer.subscription.created`, `.updated`, `.deleted`, `invoice.paid` and
`invoice.payment_failed` (an invoice names its subscription in
`subscription` or, on newer API versions, `parent.subscription_details`).
Anything else is recorded as `ignored`. The account a subscription belongs
to is the row already holding its id, else the account the checkout named
(`client_reference_id`) or the subscription's metadata, else the row
holding its customer. A checkout whose customer is already bound to another
account, or whose subscription another account holds, is recorded as
`mismatch` and logged, and nothing is written. No account at all is
`unknown`. A late event of an older subscription that has ended, while the
row holds a newer one that has not, is `stale` and skipped. Otherwise the
row is written from the fresh read and the plan recomputed: outcome
`applied`. The read gives the plan (its price through
`config.STRIPE_PRICES`), the status, the cancel flag (`cancel_at_period_end`
or `cancel_at`), the period end (on the subscription or, on newer API
versions, its item) and the seats (the item quantity).

Point the endpoint at `<public url>/api/billing/webhook` with those six
events. Cloudflare's WAF must let Stripe's POSTs through to that path.

**Reconcile.** `billing.reconcile(conn)` pages through
`Subscription.list(status=all)` (newest first; per account the newest
subscription still held wins, else the newest) and rewrites every row that
differs from that read, recomputing the plan. `billing.tick`, which `app.purge` runs hourly, runs it once a day; the
last run time is a `billing_reconciled_at` row in the `settings` table,
written directly because `settings.py` reads only its own keys and ignores
others. `manage.py billing-sync` runs it at once, and the Admin page's
Refresh re-reads one account. `GET /api/billing/me` also re-reads a row
older than an hour, ignoring a Stripe that does not answer; the Plan page
itself never calls Stripe, and its script calls `/api/billing/me` after
load and reloads the page when the plan or status moved.

`GET /api/billing/me` answers `billing.summary`: `enabled`,
`checkout_open`, `plan`, `plan_source`, `granted_plan`, `has_customer`,
`subscription` (`status`, `plan`, `interval`, `amount_usd`, `renews_at`,
`cancel_at_period_end`, `period_end`, `seats`, `past_due_since`,
`grace_ends_at`, `ended_at`, `read_only_until`, `deletes_at`) or null,
`prices` (`config.PLAN_PRICES_USD`) and `hosted` (`hosted.status_for`).

## The Plan page

`/plan`, a portal page with the **Plan** item in the side navigation
(`pages_billing.plan_page` inside `pages.app`). It uses the portal's shared
classes; its own `<style>` block adds the plan cards, the Monthly/Yearly
toggle and a danger variant of `.notice`. The route (`portal.plan`)
resolves the session and renders from the account and `billing.summary`
alone: no device or server lists, and no Stripe call. Every Choose and Resume button
follows `pages_billing.can_buy`, the same test `checkout_url` applies
(checkout open and no held subscription), so the page never offers a
checkout that would answer 409. It has six states
(`pages_billing.plan_state`):

- **Free.** Three cards, Free, Plus and Pro, with six lines each drawn from
  `config.PLAN_LIMITS`, the monthly and the yearly price with the yearly
  saving, a Monthly/Yearly toggle, and "Choose Plus" / "Choose Pro", which
  post to checkout and follow the URL. A line under the cards points to
  self-hosting. While checkout is closed the buttons read "Not available
  yet" and are disabled. A granted plan shows a strip saying so, its
  server, and the three cards with the granted one marked as the current
  plan.
- **Checkout returned** (`?checkout=success` on an account with a
  subscription row or a Stripe customer, until the server runs): "Setting
  up your server…", polling `GET /api/hosted/status` every 3 seconds until
  the state is `running`, then the address and Open. On an account Stripe
  does not know yet (the webhook may still be on its way) the parameter
  only adds a "reload in a minute" strip above the free state.
- **Active.** The server (address, storage used against the cap from
  `limits.quota_mb` and `report.uploads_bytes`, members against
  `max_accounts` on Pro, the last report) beside the subscription (plan and
  interval, price, the renewal or end date, Manage billing). A read-only
  server gets a strip.
- **Past due.** The same, under a strip with the date the grace ends and
  Fix payment (the portal).
- **Paused.** The same, under a "Payment collection is paused" strip whose
  button opens the Customer Portal; a paused subscription is held, so it is
  resumed there, not through a new checkout.
- **Cancelled or read-only.** The retention countdown (readable until
  `config.READ_ONLY_DAYS` after the end, deleted `config.DELETE_DAYS` after
  it), Export (`<server url>/?settings=backups`) and Resume (a new checkout
  of the same price, shown only while checkout is open), followed by the
  plan cards. `unpaid` is still held: it offers Fix payment and no cards.

## Usernames and confirmations with a hosted server

`accounts.set_username` (the account's own rename and the admin's) answers
409 "Your hosted server is named after your username; contact support to
rename" while the account has a `hosted_servers` row that is not deleted:
the hostname, public URL and redirect URI were fixed from the username at
provisioning, and renaming them is a later job. A rename to the same name
is a no-op.

`accounts.mark_verified`, which every confirmation path goes through (the
mail link, a reset, a Google or GitHub sign-in on the address, the admin),
calls `hosted.plan_changed` the first time an address is confirmed, since a
hosted server waits for a confirmed e-mail: a paid plan an invite granted
at registration gets its server once the link is clicked.

## The Admin page's Billing tab

`pages_billing.ADMIN_TAB` and `ADMIN_JS`. It lists
`GET /api/admin/subscriptions?status=` (the rows joined with username,
e-mail and the effective plan; admin only) with a status filter, the period
end, the cancel flag and the Stripe customer id linked to
`https://dashboard.stripe.com/customers/<id>`, and a Refresh per row
(`POST /api/admin/subscriptions/{account_id}/refresh`, a re-read from
Stripe, audited as `billing.refresh`). Refunds, disputes and invoices stay
in Stripe's dashboard. On the Accounts tab the plan select shows and sets
the granted plan, with a "paid" pill when a subscription lifts the
effective plan above it. `manage.py subscriptions [--status S]` prints the
same list.

## The website

`sites/site/pricing.html` (`/pricing`, in the header, the footer and
`sitemap.xml`) shows the same three cards with static copy and links Plus
and Pro to `https://account.gammapdf.com/plan`. The FAQ's "Is Gamma free?"
says the app is free and open source and points to it. Keep its numbers in
step with `config.PLAN_LIMITS` and `config.PLAN_PRICES_USD`.

## Running it locally with Stripe test mode

1. In the Stripe dashboard, in test mode, create a product per plan with a
   monthly and a yearly recurring price, and turn on Stripe Tax (or leave
   it off in test mode; Checkout then shows no tax).
2. Start an isolated account server with the test key and the price ids,
   on a port of its own and a throwaway data directory:

   ```bash
   cd cloud
   export GAMMA_CLOUD_DATA_DIR=/tmp/gc-billing GAMMA_CLOUD_PUBLIC_URL=http://127.0.0.1:9150
   export GAMMA_CLOUD_HOSTED_DOMAIN=gammapdf.test GAMMA_CLOUD_STRIPE_SECRET=sk_test_...
   export GAMMA_CLOUD_STRIPE_PRICE_PLUS_MONTH=price_... # and the other three
   python manage.py setup && python manage.py create-account you@example.org you --admin --verified
   ```

3. Forward Stripe's events with the CLI, and give the server the signing
   secret it prints:

   ```bash
   stripe listen --forward-to http://127.0.0.1:9150/api/billing/webhook
   export GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET=whsec_...
   uvicorn app:app --port 9150
   ```

4. Open `/plan`, choose a plan, pay with `4242 4242 4242 4242`. A failed
   renewal is `stripe trigger invoice.payment_failed`, or a test clock on
   the customer. The Admin page's Billing and Audit tabs show what arrived.

The tests need none of this: `test_billing.py` swaps the client for a fake
and stubs the hosted side, and makes no network call.

## Deleting a paying account

`POST /api/me/delete` and the admin's `POST /api/admin/accounts/{id}/delete`
call `billing.cancel_for_deletion` before the account is deleted. A
subscription in `active`, `trialing`, `past_due`, `unpaid`, `paused` or
`incomplete` is cancelled at Stripe at once (not at period end), the
canceled copy is stored (audited as `billing.cancel`) and the plan
recomputed, and only then is the account deleted. When the cancel cannot be
made (Stripe does not answer, or billing was switched off while a
subscription is still live) the deletion is refused with a 502 and nothing
changes, so a deleted account is never left being charged. A subscription
that has already ended is left alone. The Stripe call is made after the
password check and outside the deletion's transaction.

A purge (`accounts.purge`, after the grace period or the admin's Purge now)
first hands a live hosted server to `hosted.purge_account`, which deletes it
and removes its row, then removes the account's `subscriptions` row with
the rest; `billing_events` keeps its rows.

## Not built yet

- Pro seats beyond ten, a Plus storage add-on, and mail at each billing
  transition (Stripe's own dunning mail covers failed payments).
- Refunds on deletion: a cancel now ends the subscription without a
  prorated refund; issue one from Stripe's dashboard if it is owed.
