# Billing: plans, Stripe and the Plan & billing page

How the account server sells the Lite, Plus and Pro plans: the rule that
turns a grant and a subscription into the one plan every claim carries, the
Stripe wiring (Checkout, the Customer Portal and its flows, the webhook,
the nightly reconciliation), the portal's Plan & billing page, the Admin
page's Billing tab and the website's pricing and terms pages. The design and the reasons behind the prices
are in [research/cloud-plans.md](../research/cloud-plans.md). The account
server itself is [cloud_accounts.md](cloud_accounts.md).

Where a plan's library lives is `config.PLAN_LIMITS`. Lite and Plus
(`shared`) are an account on the shared server, whose address is
`GAMMA_CLOUD_SHARE_HOST_URL`; the plan's storage reaches that server as a
claim ([cloud_accounts.md](cloud_accounts.md) "Plans on the share host").
Pro (`hosted`) provisions a container the account administers
([hosted.md](hosted.md)).

Code: `cloud/gammacloud/billing.py` (Stripe and the rules),
`routers/billing.py` (`/api/billing/*`), `pages_billing.py` (the Plan
page, the Overview's plan card and the Billing tab), the effective-plan
rule in `accounts.py`. Tests:
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
  from `plan`. A grant may end: `accounts.granted_until` is when (NULL: it
  does not). An invite with `grant_days` sets it at registration, and the
  Admin page's *Grant ends…*, `PATCH /api/admin/accounts/{id}`
  `{granted_until}` and `set-plan --until` set or clear it; a new plan from
  the select is a new grant with no end. `accounts.expire_grants`, hourly
  from `app.purge`, sets every grant whose day has passed back to `free`,
  clears the date, audits `account.grant_expired` and recomputes the plan,
  so a hosted server with no subscription behind it lapses through its
  usual lifecycle ([hosted.md](hosted.md)).
- the subscription row's `plan`, but only while its status pays for it:
  `active` or `trialing`, or `past_due` for `config.GRACE_DAYS` (7) after
  `past_due_since` (`accounts.billed_plan`).

`accounts.recompute_plan(conn, account_id, actor, source)` sets `plan` to
the higher of the two by `config.PLAN_RANK` and audits a change as
`account.plan` with its source (`stripe`, `admin`, `invite` or `expiry`)
and both inputs. It calls `hosted.plan_changed` when the plan changed; billing passes
`notify=True` after every subscription write, so a grace period or a cancel
reaches the hosted server before the plan itself moves. A webhook therefore
never erases a courtesy grant, and a lapsed subscription falls back to the
grant, or to `free`, by itself. Nothing announces the end of a grace
period, so `billing.tick` recomputes every `past_due` row hourly.

`accounts.public` adds `granted_plan`, `granted_until`, `plan_source`
(`stripe` when the subscription carries the plan, `granted`, or `free`),
and `renews_at` /
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
| `GAMMA_CLOUD_STRIPE_PRICE_LITE_MONTH`, `_LITE_YEAR`, `_PLUS_MONTH`, `_PLUS_YEAR`, `_PRO_MONTH`, `_PRO_YEAR` | the six recurring Price ids; `config.STRIPE_PRICES` maps the key a browser sends (`plus_month`, …) to (plan, interval, price id). A plan is sold only with both of its ids |
| `GAMMA_CLOUD_APP_URL` | the shared server's address for people (default: `GAMMA_CLOUD_SHARE_HOST_URL`); Lite and Plus are sold only while there is one |
| `GAMMA_CLOUD_HOSTED_DOMAIN` | the zone of the hosted containers; Pro is sold only while it is set |

`config.PLAN_PRICES_USD` is what the pages show; what is charged is the
Price in Stripe, so the two must agree. The SDK is `stripe` in
`cloud/requirements.txt`. It is imported only when billing makes its first
call, so a server without the secret runs without the package, and a
secret set on an image without it fails billing calls (logged), not the
server.

**The client.** `billing.client()` is a small wrapper over the SDK's
`StripeClient`: verify an event, create a Checkout Session, create a portal
session, retrieve, update, cancel and list subscriptions, retrieve a
customer and list its invoices. Each call answers plain dicts and raises
`BillingError` when Stripe does not answer. Tests swap it with `billing.set_client(fake)`. No call is
made while cloud.db's write lock is held: the routes commit the session
touch first, and the webhook and refresh read Stripe before they take the
lock.

**Checkout.** `POST /api/billing/checkout {price}` (portal session only,
rate limited per account) answers `{url}` and the page sends the browser
there. `billing.checkout_url` refuses while billing is off (503), for an
unknown price key or one without a price id (400), for a plan that cannot
be sold (503; `billing.can_sell`, "Plans on sale"), for an unconfirmed e-mail (403: a Gamma server
would not sign it in), and while
the account holds a subscription in `active`, `trialing`, `past_due`,
`unpaid` or `paused` (409, pointing at the Plan page). The browser never
states a plan; it names a price key the server looks up.

The session is `mode: subscription` with one line item of the price and
`client_reference_id` = the account id. It names the account's Stripe
customer when there is one (with `customer_update` address and name
`auto`, which Stripe Tax needs), else the account's e-mail.
`automatic_tax` is on and promotion codes are allowed.
`custom_text.submit.message` is `billing.CHECKOUT_NOTE`, which says beside
the Subscribe button that payments are not refunded. `metadata` and
`subscription_data.metadata` are `{account_id, plan}`. The success URL is
`/plan?checkout=success&session_id={CHECKOUT_SESSION_ID}` and the cancel
URL `/plan`, both on `GAMMA_CLOUD_PUBLIC_URL`.

**The Customer Portal.** `POST /api/billing/portal {flow, price}` answers a
portal session for the account's customer (404 without one) that returns
to `/plan`. Every change made there comes back through the webhook.
`flow` (`billing.PORTAL_FLOWS`) picks the page the portal opens on:

| `flow` | opens | refused |
|---|---|---|
| `""` | the portal's home: invoices, billing address, tax id | |
| `payment` | the payment method | |
| `cancel` | cancelling this subscription at the end of its period | 409 with no held subscription, or one already set to end |
| `switch` | Stripe's confirmation of a move to the price key `price`, with what is charged | 400 for an unknown price or the current one; 503 for another plan that cannot be sold (`can_sell`; the other billing period of the plan held needs only its price id); 409 unless the subscription is `active` or `trialing` and not set to end |

A flow ends with a redirect to `/plan?billing=<flow>`, where the page says
what was saved. `switch` reads the subscription first for the id of the
item it moves. A flow the portal's configuration does not allow is logged
and the session opens on the portal's home instead.

`POST /api/billing/keep` (`billing.keep`) undoes a cancellation that has
not taken effect: it clears `cancel_at_period_end` (and a `cancel_at` date,
which the portal may set instead), stores the answer and audits
`billing.keep`. 409 when the plan is not set to end.

Configure the portal in the Stripe dashboard (Settings → Billing → Customer
portal): payment methods and invoice history on; cancellation on, at the
end of the billing period; plan switching on with the six prices as the
products a customer may switch between, upgrades prorated and charged at
once, downgrades scheduled for the end of the period. The last setting
keeps a downgrade from leaving a credit on the customer's balance.

**No refunds.** Nothing in the account server refunds a payment, and no
page offers one:

- Checkout says so beside its button (`CHECKOUT_NOTE`), and so do the Plan
  page's closing lines, the pricing page and the
  [terms](../../TERMS.md) (`/terms/` on the site), which keep the one
  exception: a refund the law gives a buyer and that cannot be excluded.
- A cancel runs to the end of the paid period. Only an account deletion
  ends a subscription at once ("Deleting a paying account"), and its
  confirmation says that nothing is refunded.
- A refund the operator decides to give is made in Stripe's dashboard.

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
older than an hour, ignoring a Stripe that does not answer. With
`?fresh=1` it re-reads one older than 10 seconds, which the page asks for
when the portal has just sent the browser back. The Plan page itself never
calls Stripe: its script calls `/api/billing/me` after load and reloads the
page when the plan, status, interval, cancel flag or period end moved.

`GET /api/billing/me` answers `billing.summary`: `enabled`, `sells` (the
plans `can_sell` allows), `shared_url` (the shared server's address),
`plan`, `plan_source`, `granted_plan`, `has_customer`,
`subscription` (`status`, `plan`, `interval`, `amount_usd`, `renews_at`,
`cancel_at_period_end`, `period_end`, `seats`, `can_switch`,
`past_due_since`, `grace_ends_at`, `ended_at`, `updated_at`) or null,
`prices` (`config.PLAN_PRICES_USD`) and `hosted` (`hosted.status_for`,
which carries the dates a lapsed server stops and is deleted).

**The payment method and the invoices.** `GET /api/billing/details`
(`billing.details`, 60 an hour per account) reads them from Stripe when
the page asks, and stores nothing. `payment_method` is the one the next
charge goes to: the subscription's `default_payment_method`, else the
customer's `invoice_settings.default_payment_method`, as `{kind, brand,
last4, exp_month, exp_year}` or null. `invoices` are the newest 12
(`billing.INVOICES_SHOWN`) that are not drafts, each `{id, number,
created, description, total, currency, status, url, pdf}`; `total` is in
cents, `url` is Stripe's hosted page (where an open invoice is paid and a
paid one has its receipt) and `pdf` its download. An account Stripe does
not know answers empty lists without a call. A Stripe that does not answer
is a 502, and the page says the history is not available.

## Plans on sale

`billing.can_sell(plan)` is the one answer to "may this plan be bought
now", and `billing.why_not(plan)` says why not in a few words ("" when it
can). The first reason found, in this order:

1. the operator holds it back: it is not in `settings.plans_on_sale()`
   (the `plans_on_sale` row in cloud.db, default all three);
2. billing is off (no `GAMMA_CLOUD_STRIPE_SECRET`);
3. a price id is missing: the page offers a month and a year, so both are
   needed ("no yearly Stripe price id");
4. the plan's home is not configured: `GAMMA_CLOUD_APP_URL` for Lite and
   Plus, `GAMMA_CLOUD_HOSTED_DOMAIN` for Pro.

Everything that sells asks it: a checkout (503 "This plan is not
available yet."), a subscriber's switch to another plan in the portal
(the same 503), the Plan page's cards and Resume, `summary.sells`, and the
website. A switch between the month and the year of the plan already
held needs only that price id, so a held-back plan's subscribers can
still change how they pay. Holding a plan back takes nothing from anyone
who has it: the subscription, the grant and the server stay as they are.
Emptying the hosting domain is not a way to stop selling Pro, since it
also takes every hosted server's address away; the setting is.

The Admin page's Settings tab has a **Plans** section: an *On sale*
checkbox per paid plan, saved as `PATCH /api/admin/settings
{plans_on_sale: "lite plus"}`, and a pill per plan, `on sale`, `held back`,
or `cannot be sold` with the reason. `GET` and `PATCH /api/admin/settings`
carry the rows as `plans: [{plan, on_sale, sellable, reason}]` (built in
the router: `settings.py` does not import billing).

`GET /api/plans` is public: `{"plans": {"lite": {"on_sale": true}, …}}`,
`on_sale` being `can_sell`. It answers with `Access-Control-Allow-Origin:
https://gammapdf.com` (`pages.SITE`) and `Cache-Control: public,
max-age=300`, set by the route itself since the middleware's `no-store` on
`/api/` is only a default. The website's pricing page reads it ("The
website").

## The Plan & billing page

`/plan`, a portal page with the **Plan & billing** item in the side
navigation (`pages_billing.plan_page` inside `pages.app`). It uses the
portal's shared classes; its own `<style>` block adds the plan cards, the
Monthly/Yearly toggle and the invoice table. The route (`portal.plan`)
resolves the session and renders from the account and `billing.summary`
alone: no device or server lists, and no Stripe call. The script fills in
the payment method and the billing history from `/api/billing/details`
after load. Every payment action is a redirect to a page at Stripe:
Checkout for a first purchase, the portal on one flow for a change. Every
Choose and Resume button follows `pages_billing.can_buy`, the same test
`checkout_url` applies (the plan can be sold and the account holds no
subscription), so the page never offers a checkout that would be refused. Every state ends with
the terms in small print (`_fine`): payments are not refunded, a cancel
runs to the end of the period, and a link to `/terms/`.

It has six states (`pages_billing.plan_state`):

- **Free.** Four cards, Free, Lite, Plus and Pro, with six lines each (the
  paid ones drawn from `config.PLAN_LIMITS`), the monthly and the yearly
  price with the yearly saving, a Monthly/Yearly toggle, and "Choose Lite" /
  "Choose Plus" / "Choose Pro", which post to checkout and follow the URL. A
  line under the cards points to self-hosting. A plan that cannot be sold
  reads "Coming soon", disabled; when no paid plan sells, a line under the
  cards says that only the Free plan is available right now and paid
  plans are coming soon (to someone holding a plan: theirs stays as it
  is). A granted plan shows a
  strip saying so, its server, and the four cards with the granted one
  marked as the current plan.
- **Checkout returned** (`?checkout=success` on an account with a Pro
  subscription, or a Stripe customer and no row yet, until the server
  runs; a Lite or Plus plan has nothing to set up and goes straight to
  Active): three
  steps, *Payment received*, *Creating your server* and *Ready*, ticked off
  as `GET /api/hosted/status`, polled every 3 seconds, reaches `running`;
  then the address and Open. On an account Stripe does not know yet (the
  webhook may still be on its way) the parameter only adds a "reload in a
  minute" strip above the free state.
- **Active.** Two sections side by side, then two below:
  - *Subscription*: the plan and billing period, the price, the next
    payment date, the payment method, and the actions *Change plan* (a link
    down to the cards), *Update payment method* and *Cancel plan*, each a
    portal flow. A plan set to end shows *Ends* in place of the next
    payment and *Keep my plan* in place of the change and cancel buttons.
  - where the library lives (`pages_billing._home`). On Pro, *Your
    server*: the address, storage used against the cap (from
    `limits.quota_mb` and `report.uploads_bytes`), members against
    `max_accounts`, the off-site copies, the last report. A read-only
    server gets a strip above both. On Lite and Plus, *Your library*: the
    shared server's address, the plan's allowance and Open, which goes
    through that server's cloud sign-in (`/api/auth/cloud/start`), so the
    session carries the plan as it is now. A server left from a move down
    from Pro is listed under it until it is deleted.
  - *Billing history*: the invoices with date, description and number,
    amount, status, and *Receipt* and *PDF* links; an open invoice has
    *Pay*. Its header links to the portal's home for the rest.
  - *Change plan*, while `subscription.can_switch`: the four cards as
    switches. Another plan reads "Upgrade to …" or "Switch to …", or
    "Coming soon" while it cannot be sold; the current plan's card offers
    its other billing period while that price id is set. The toggle starts
    on the period the subscription pays by.
- **Past due.** The same without the cards, under a strip with the date
  the grace ends and Fix payment (the portal's home, where the open invoice
  is paid).
- **Paused.** The same, under a "Payment collection is paused" strip whose
  button opens the Customer Portal; a paused subscription is held, so it is
  resumed there, not through a new checkout.
- **Cancelled or read-only.** For Lite and Plus, a section saying that
  the library is kept but closed until a plan opens it again
  ([cloud_accounts.md](cloud_accounts.md) "Who the shared server takes"),
  with Resume. For Pro, when the server stops being readable and
  when it is deleted (`stops_at` and `deletes_at` of `hosted.status_for`,
  the lifecycle's own dates), Export (`<server url>/?settings=backups`,
  while the server is read-only) and Resume (a new checkout of the same
  price, shown only while checkout is open), followed by the billing
  history and the plan cards. `unpaid` is still held: it offers Fix payment
  and no cards.

After a portal flow the page shows what was saved (`?billing=`,
`pages_billing.RETURNED`).

**The Overview.** `pages_billing.overview_plan` is its plan card: the plan,
"Renews 3 Nov · $5 a month" or when it ends, the shared server's address
or the hosted server's address and storage, and *Plan and billing*. `overview_alert` adds a strip above
the page for a failed payment, a paused subscription, or a server that is
read-only or stopped. The Settings page's delete row says that a
subscription ends at once without a refund.

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

`pages_billing.ADMIN_TAB` and `ADMIN_JS`, top to bottom:

- **a summary strip** (`billing.admin_summary`, the `summary` of the list
  below): the subscriptions that pay now (`active`, `trialing`,
  `past_due`) by plan, what they bring in a month (a yearly price counts a
  twelfth, from `config.PLAN_PRICES_USD`, so before tax and discounts), and
  how many have a failed payment or are set to end;
- **subscriptions**: `GET /api/admin/subscriptions?status=` (the rows
  joined with username, e-mail and the effective plan; admin only) with a
  status filter, the period end, the cancel flag and the Stripe customer id
  linked to `https://dashboard.stripe.com/customers/<id>`
  (`/test/customers/` with a test key), and a Refresh per row
  (`POST /api/admin/subscriptions/{account_id}/refresh`, a re-read from
  Stripe, audited as `billing.refresh`);
- **webhook events**: `GET /api/admin/billing-events?limit=`, the newest 50
  rows of `billing_events` with the account and the outcome.

Refunds, disputes and invoices stay in Stripe's dashboard. On the Accounts
tab the Plan column names the plan the account is on, with "paid" when a
subscription lifts it above the grant and "granted" when the grant gives
it; the select under it, labelled *grant*, shows and sets the granted plan
only, with the day it ends beside it (*Grant ends…* sets or clears it).
Which plans are on sale is the Settings tab's Plans section ("Plans on
sale"). `manage.py
subscriptions [--status S]` prints the same list.

## The website

`sites/site/pricing.html` (`/pricing`, in the header, the footer and
`sitemap.xml`) shows the same four cards with static copy and links Lite,
Plus and Pro to `https://account.gammapdf.com/plan`. Those links carry
`data-plan`; `sites/site/site.js` asks `GET /api/plans` ("Plans on sale")
and turns the link of a plan not on sale into a disabled "Coming soon".
A call that fails leaves the page as written. The site's CSP lets
`https://account.gammapdf.com` through `connect-src` for it. The FAQ's "Is Gamma
free?" says the app is free and open source and points to it. Keep its
numbers in step with `config.PLAN_LIMITS` and `config.PLAN_PRICES_USD`.
Its notes say that payments are not refunded and link to `/terms/`, the
repository's [TERMS.md](../../TERMS.md) rendered by the site build. The
footer links there too.

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
   export GAMMA_CLOUD_STRIPE_PRICE_LITE_MONTH=price_... # and the other five
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

A purge (`accounts.purge`, hourly once `config.PURGE_DELETED_DAYS` (30) have
passed since the deletion, or the admin's Purge now)
first hands a live hosted server to `hosted.purge_account`, which deletes it
and removes its row, then removes the account's `subscriptions` row with
the rest; `billing_events` keeps its rows.

## Not built yet

- Pro seats beyond ten, a Plus storage add-on, and mail at each billing
  transition (Stripe's own dunning mail covers failed payments).
- Mail of its own when a plan is cancelled or changed; Stripe's receipts
  and the server's lifecycle mail ([hosted.md](hosted.md)) are what goes
  out.
- The portal's configuration is set by hand in Stripe's dashboard; nothing
  creates or checks it from here.
