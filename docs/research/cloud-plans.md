# Gamma Cloud plans: pricing, billing, and hosted servers in batch

Design worked out in October 2026 for the paid side of Gamma Cloud: what
the Free, Lite, Plus and Pro plans contain and cost, how a purchase is
verified and kept inside the account server, what the subscription page
shows, how Stripe is wired in, and how the paid containers are created,
upgraded and retired as a fleet. It restates the product shape the earlier
[hosting.md](hosting.md) note deferred to (a container per paying customer,
the free tier in the desktop app, one share host, one account server),
since the planning file that held it was never in the repository. The
design is now built: the status paragraph at the end of "Order of work"
lists where the result differs from the text, and the current mechanics
are in [dev/cloud_accounts.md](../dev/cloud_accounts.md),
[dev/billing.md](../dev/billing.md) and [dev/hosted.md](../dev/hosted.md).

## Where things stand

- The account server (`cloud/`, v0) has `plan` as a column on `accounts`
  with the values `free`, `plus`, `pro` (`config.PLANS`; `lite` has been
  added since, and `plan` is now computed, rule 4 below). An admin sets
  it, or an invite code grants it. Every ID token and `/userinfo` answer
  carries it as the `plan` claim, and each Gamma server stores the last
  claims per linked identity.
- The claim is read in exactly one place: the share host's publish cap
  (`config.PLAN_PAGE_LIMITS`: `free` 5 pages, every other plan unlimited).
- The portal's plan card already says "A hosted Gamma server of your own
  comes with the Plus and Pro plans" and points at self-hosting until then.
  `accounts.RESERVED_USERNAMES` already keeps the service names out of the
  username space because a paid container is `<username>.gammapdf.com`.
- A provisioned container is configured by environment
  (`GAMMA_CLOUD_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_POLICY`,
  `_ADMIN_SUBJECT`); the admin subject becomes the server's admin on first
  sign-in with no password on the wire. `servers.of_account` has a slot for
  provisioned servers next to the ones a person linked by hand.
- Not built: anything that takes money, anything that creates a container,
  and any limit on a Gamma server that depends on a plan other than the
  publish cap. The website's FAQ says "no plans, no seats and no account
  with us". (All of it since built; the FAQ now points to `/pricing`.)

## The plans

| | Free | Lite | Plus | Pro |
|---|---|---|---|---|
| Price | 0 | $2 a month, $20 a year | $5 a month, $50 a year | $20 a month, $200 a year |
| Library | the desktop app, on disk | a hosted Gamma of your own at `<username>.gammapdf.com` | a hosted Gamma of your own at `<username>.gammapdf.com` | the same, for a group |
| Accounts on the server | none | 1 | 1 | 10 included; more as seats later |
| Shared workspaces | self-host only | no; share-by-link (view or edit) still works for anyone | no; share-by-link (view or edit) still works for anyone | yes: roles, live cursors, invitations by cloud username |
| Storage for uploads | the share host's small default | 1 GB | 6 GB | 100 GB pooled, per-workspace quotas |
| Per-file cap | 50 MB | 50 MB | 100 MB | 250 MB |
| Published pages | 5 | unlimited | unlimited | unlimited |
| Devices | against a server you run | desktop offline copies, iPad replica, extension clipping, Codex and Claude Code over MCP | desktop offline copies, iPad replica, extension clipping, Codex and Claude Code over MCP | the same |
| Off-site copies | none | daily, 3 kept | daily, 7 kept | hourly, 30 kept |
| AI | own key or ChatGPT sign-in | the same | the same | the same, plus one shared connection the admin adds for the whole server |
| Administration | none | you administer your server | you administer your server | users, workspaces, shared AI, guests |

**What Plus sells.** The desktop app is free and local, so the thing a
Plus customer pays for is not storage: it is a server that is always on.
Devices stay in sync through it, share links work with the laptop closed,
the browser extension clips from any machine, and assistants reach the
library at a public URL. The storage figure is a cap, not the headline.

**Lite** came after the first three plans: $2 a month or $20 a year for the
same single-account server with 1 GB, 50 MB per file and daily copies, 3
kept. It is the entry price for someone who wants a hosted library but
has a few hundred papers, and it costs the operator the same container
as Plus.

**Why these storage numbers.** Zotero's storage plans are the price list
researchers know: 300 MB free, 2 GB for $20 a year, 6 GB for $60 a year,
unlimited for $120 a year. Obsidian Sync is $4 a month (annual) for 1 GB and
$8 for 10 GB. Plus at $50 a year with 6 GB matches Zotero's middle tier for
less and adds the server; at 2 GB it would have looked worse than Zotero
per gigabyte. Pro at 100 GB for a group of ten undercuts Zotero's unlimited
per person by a wide margin. "Unlimited" itself is not promised: a
container has one disk, and the figure must stay one the fleet can place.
The one gap this leaves is a single heavy user who wants more than 6 GB
without a group plan; a storage add-on on Plus (a second Stripe price) can
close it later without a third plan. What the storage costs the operator is
small either way: off-site copies run at about a cent and a half per
gigabyte-month, and the VPS disk is the larger part.

**Why Plus is one account.** The cost of a container does not depend much
on how many people sign in to it, so the single-account rule is a product
line, not a cost line: it is what separates "my library, hosted" from "a
server for my group". Anyone-with-the-link edit shares already allow
anonymous writes, so a Plus customer can still work on one page with a
co-author who has no account, and that keeps the rule from feeling mean.

**Why Pro counts members.** A flat price with no cap invites a department
onto one container. Ten included accounts fits a lab; seats beyond that
come later as a Stripe quantity on the same subscription.

**Why no bundled AI tokens.** Bundled tokens are the one line whose cost
the operator does not control, and the meter that exists (the shared AI
allowance, [dev/guests.md](../dev/guests.md)) counts per account over a
rolling 24 hours with no monthly window and no server-wide pool, so ten Pro
members would mean ten times the exposure. Anthropic's terms forbid routing
through subscription credentials, so any bundled Claude is API-billed. A
question with a PDF attached runs 30k to 60k input tokens, about $0.10 to
$0.20 on a Sonnet-class model, so a $20 plan absorbs a few dozen such
questions a month across the whole server, which a lab spends in a week.
Meanwhile ChatGPT sign-in gives many people AI at no cost to Gamma, and
Pro's shared connection (the admin's one key for the lab) already exists.
If a bundled allowance is wanted later it needs a monthly, per-server pool
on a cheap model for metadata, translation and summaries, sold as an
add-on with its own price, and a way to put the operator's key into a
container.

## What a container enforces, and where it learns it from

Today a Gamma server reads nothing plan-shaped except the publish cap. A
hosted container needs its owner's plan to decide five things: the default
upload quota, the per-file cap, how many accounts may exist, which sign-in
policy applies, and whether the server is in a read-only state because the
subscription lapsed. Two ways to get them were weighed.

**From the owner's plan claim.** The grant check already refreshes every
identity's claims hourly, so the admin subject's stored `plan` would be
current within an hour and nothing new travels. It fails on the cases that
matter most: a suspended server whose owner never signs in again, a server
whose owner unlinked, and a status (grace, read-only) that is not a plan.

**From one call to the account server (chosen).** The container
authenticates with its own OIDC client id and secret, which it already
holds in its environment, and asks `POST /api/hosted/sync` at startup and
hourly from the app's `every()` loop. The body is its report (build and
schema version, accounts, upload bytes, data-directory bytes, public URL);
the answer is its limits:

```json
{"plan": "pro", "status": "active", "read_only": false,
 "policy": "invited", "max_accounts": 10,
 "quota_mb": 102400, "max_upload_mb": 250,
 "offsite": {"interval_s": 3600, "keep": 30},
 "grace_until": null, "message": ""}
```

The container keeps the last answer in its `settings` KV, so a blip at the
account server changes nothing, and a server that never got one runs on its
environment's defaults. Where each field lands on the Gamma side:

- `quota_mb` and `max_upload_mb` become the server-wide defaults
  (`server_settings._plan_caps`) when the admin has not set them (a
  hosted admin's own setting may only go lower). No environment variable
  sets those defaults today, so this is the first way a provisioned
  container gets them without `manage.py` inside the container.
- `max_accounts` and `policy` feed `cloud_auth._resolve`. A new policy
  **`invited`** is needed (since built in `cloud_auth.py`): provision only
  a subject that holds a pending invitation on this server
  (`pending_memberships`), refuse everyone else.
  Checked against the code: under `refuse` and `claim` an unknown subject
  is refused before `claim_pending_memberships` runs, and `provision` lets
  any cloud account in, so neither fits a Pro server that invites its
  members. Plus runs `refuse` with the owner as admin subject, which admits
  exactly one person.
- `read_only` refuses every write (uploads, block ops, shares, imports)
  with one message naming the reason, while reads, exports and the Backups
  zip keep working, so a lapsed customer can always leave with their data.
  The same switch serves a reported phishing host.
- `offsite` sets the copy interval and generations
  ([dev/debugging.md](../dev/debugging.md) "Off-site copies in a bucket"),
  since the bucket settings themselves come from the environment.
- The status and plan show in the hosted admin's Server pane and as a
  notice ("Payment failed; your server becomes read-only on …"), through
  the existing notices table.

A plan change therefore needs no restart: upgrade, downgrade, grace and
suspension all arrive with the next sync. (The `sync` job planned below
for asking sooner was not built: the container's admin has Sync now.)

## Verifying a purchase

Stripe first, for the reasons everyone picks it: hosted Checkout and the
Customer Portal remove the card form, the portal page and most of the
dunning mail from this codebase, and Stripe Tax collects VAT and sales tax
where the operator is registered. The alternative worth knowing about is a
merchant of record (Paddle, Lemon Squeezy): it is the seller on the
invoice and handles every jurisdiction's tax for a higher fee, which for a
one-person operator selling worldwide may be worth more than the
difference. The design below is the same either way; only the webhook
vocabulary changes.

The rules that make the purchase trustworthy:

1. **The browser never states a plan.** It asks for a Checkout Session
   (`POST /api/billing/checkout {price}`) while signed in to the portal; the
   server creates the session with `client_reference_id` = the account id,
   the account's e-mail, `mode: subscription`, the price id from
   configuration, `automatic_tax` on, and success and cancel URLs back on
   `/plan`. The answer is the session URL and the browser is sent there.
   One subscription per account: a second checkout while one is active is
   refused and pointed at the portal.
2. **Only the webhook changes a plan.** `POST /api/billing/webhook` checks
   the signature with the webhook secret, stores the event id in
   `billing_events` (primary key; a repeat is answered 200 and ignored),
   then **fetches the subscription from Stripe** rather than trusting the
   event body. Stripe does not promise event order, and a fresh read makes
   every event idempotent: the handler always writes the subscription's
   current state. Events handled: `checkout.session.completed` (binds the
   Stripe customer to the account named by `client_reference_id`; a
   mismatch with the session's customer is logged and refused),
   `customer.subscription.created`, `.updated`, `.deleted`,
   `invoice.paid`, `invoice.payment_failed`.
3. **Status is a copy, Stripe is the source.** `subscriptions` holds
   `account_id`, `stripe_customer_id`, `stripe_subscription_id`, `price_id`,
   `plan`, `status` (Stripe's: `active`, `trialing`, `past_due`, `unpaid`,
   `canceled`, `incomplete`, `incomplete_expired`, `paused`),
   `cancel_at_period_end`, `current_period_end`, `seats`, `updated_at`.
   A nightly job pages `Subscription.list(status=all)` and repairs any row
   that drifted; the `/plan` page also refreshes a row older than an hour
   when it is opened.
4. **Granted plans and billed plans are kept apart.** `accounts.plan`
   stays the effective plan every claim reads, but it becomes computed:
   the higher of `accounts.granted_plan` (what an admin or an invite gave,
   today's `set_plan` and `take_invite`) and the subscription's plan while
   its status is `active`, `trialing` or `past_due` within grace. A webhook
   can then never erase a courtesy grant, and a lapsed subscription falls
   back to the grant or to `free` by itself.
5. **The Customer Portal does the rest.** `POST /api/billing/portal`
   creates a portal session and the browser goes there to change the
   payment method, switch between the Lite, Plus and Pro prices, cancel at
   period end, or download invoices. The portal configuration allows
   exactly those switches; every change comes back through the webhook.
6. **Edge rules.** The webhook path is excluded from the portal session
   check, the same-origin check (`app._CROSS_ORIGIN_OK`) and the Turnstile
   gate, and allowed through Cloudflare's WAF. As built it is not limited
   to Stripe's published addresses: the signature check refuses anything
   else. Keys live in the environment: `GAMMA_CLOUD_STRIPE_SECRET`,
   `GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET`, and the six price ids
   (`..._PRICE_LITE_MONTH`, `_LITE_YEAR`, `_PLUS_MONTH`, `_PLUS_YEAR`,
   `_PRO_MONTH`, `_PRO_YEAR`). Stripe's test mode and the CLI's event
   forwarding cover local development; the tests (`test_billing.py`)
   build events against a fake Stripe client, the way `test_cloud_auth.py`
   fakes the account server.

What this means for "validation" from a Gamma server's point of view: a
container never hears about money. It exists because a webhook provisioned
it, and it learns its limits from `/api/hosted/sync` with credentials only
the provisioner wrote. There is nothing a browser or a container can claim
that the account server did not decide.

## Keeping it inside Gamma Cloud

Billing is a module of `gammacloud`, next to accounts and OIDC, and nothing
else in the repository learns Stripe's vocabulary:

- `billing.py` (sessions, the webhook handler, the status rules, the
  effective-plan computation) and `routers/billing.py` (`/api/billing/*`,
  the webhook); the `stripe` package is the one new dependency of `cloud/`.
- Schema step 9: `subscriptions`, `billing_events`, `accounts.granted_plan`
  (back-filled from `plan`), and the hosted-server tables below. The
  current `plan` column keeps its meaning.
- Audit rows for every plan change name their source (`stripe`, `admin`,
  `invite`), so the admin page's audit tab explains a plan the way it
  explains an invite today.
- `accounts.public` gains `plan_source`, `renews_at` and `cancel_at`, so
  the Overview card can say "Plus · renews 3 Nov" without a Stripe call.
- The Admin page's Accounts tab shows the billed status beside the plan
  select, which keeps working as the courtesy grant. A new Billing tab
  lists subscriptions by status, with a link into Stripe's dashboard per
  customer; refunds and disputes stay in Stripe.
- Account deletion (`/api/me/delete`) cancels the subscription at Stripe
  first and tears the container down (below; as built, the server lapses
  like a cancelled plan and is deleted when the account is purged); a
  deleted account with a live subscription is refused until the cancel
  succeeded.

Gamma servers keep seeing only the `plan` claim and, for hosted ones, the
limits answer. The share host's publish cap keeps reading the claim.

## The subscription page

A `/plan` page in the portal, server-rendered like the others
(`pages.py`), with a "Plan" item in the side navigation. It has five
states, decided by the account's subscription row and hosted-server row:

- **Free, nothing hosted.** Three cards: Free (what the desktop app gives,
  "your current plan"), Plus and Pro, each with the table above in six
  lines, a monthly and a yearly price with the yearly saving named, and one
  button, "Choose Plus" / "Choose Pro", which posts to
  `/api/billing/checkout` and follows the answer. A line under the cards
  points to self-hosting for people who do not want a hosted server.
- **Checkout returned.** `?checkout=success` shows "Setting up your
  server…" and polls `GET /api/hosted/status` every few seconds until the
  state is `running`, then shows the address with an Open button. A
  cancelled checkout is just the first state again.
- **Active.** The plan name, the renewal date and amount, "Manage billing"
  (the Customer Portal), the server's address, its storage used against the
  plan's cap (from the last report), member count on Pro, and the next
  off-site copy time. Pro also lists "Invite people" with a line that says
  members sign in with their own Gamma Cloud account.
- **Past due.** A warning strip with the date the server becomes
  read-only and a "Fix payment" button into the portal.
- **Cancelled or read-only.** The retention countdown, an "Export" link
  to the server's Backups pane, and "Resume" into checkout.

Gamma itself contributes two things: the hosted admin's Server pane shows
the plan and a "Manage plan" link to `/plan`, and the storage meter the
Account card already draws reads the cap the limits answer set. The
website gets a `/pricing` page with the same three cards linking to
`/plan`, and the FAQ's "Is Gamma free?" answer becomes "the app is free and
open source; the hosted plans are …".

## Provisioning and the fleet

**Tables on the account server.** `hosts` (id, name, address, capacity:
memory and disk, what is used, the agent's token hash, last heartbeat) and
`hosted_servers` (account id, hostname label = the username at
provisioning, host, OIDC client id, image tag, `state`
`provisioning` / `running` / `suspended` / `stopped` / `deleted`, limits as
last computed, last report, created and changed times). `fleet_jobs` is the
queue: host, server, kind (`create`, `update`, `restart`, `start`, `stop`,
`delete`, `snapshot`, `upgrade`, `sync`), payload, state, attempts, result.
As built, the states add `grace` and `read_only`, and the kinds are
`create`, `start`, `stop`, `restart`, `delete`, `upgrade` (which also
resizes), `rollback` and `logs` ([dev/hosted.md](../dev/hosted.md)).

**An agent per host, not a socket in the account server.** Creating a
container means the Docker socket, which is root on the host. The account
server is the one process on the public internet, so it should not hold
it. Instead a small service, `gamma-fleet`, runs on every host with the
socket and the Docker SDK, makes outbound calls only, and long-polls
`GET /api/fleet/jobs` with its host token, which names the host. It
applies each job, reports the result, and every five minutes posts each
container's health, memory and the size of its data directory. A host
whose heartbeat stops is shown stale and takes no new placements. The
agent is a second small Python package under `cloud/fleet/`, deployed as
a container with the socket mounted, and it shares nothing with
`gammacloud` but the HTTP shape.

**Creating a server** (a `create` job, enqueued by the webhook when a
subscription becomes active and no row exists):

1. Pick the host with the most free memory that has disk for the plan.
2. Create the OIDC client (kind `container`, redirect
   `https://<label>.gammapdf.com/api/auth/cloud/callback`); the secret is
   written into the job payload once and never stored in clear.
3. The agent starts `gamma-<label>` from `ghcr.io/tim4431/gamma:<tag>` (the
   tag is the fleet's current pin, per server so a canary can run ahead),
   with `/srv/gamma/<label>/data` on `/data`, the cloud variables, a
   fixed `GAMMA_PUBLIC_URL`, `FORWARDED_ALLOW_IPS` = the fleet network's
   pinned subnet, the off-site bucket variables with prefix
   `hosted/<account id>/`, a memory limit and a CPU share, restart
   `unless-stopped`, and labels naming the account and plan. It waits for
   `/api/health` and reports `running`.
4. The account server mails "Your Gamma is ready at …", adds the server to
   `servers.of_account` so `/api/me` lists it (the desktop launcher's
   first-run sign-in, the plan's unbuilt step 6, then shows it), and the
   container's first `/api/hosted/sync` fetches its limits.

**Routing without per-customer configuration.** Caddy can pick the
upstream from the hostname: in the wildcard site, after the `share`,
`demo` and `-pages` handles, `reverse_proxy gamma-{http.request.host.labels.2}:9001`
sends `alice.gammapdf.com` to the container named `gamma-alice` on the
fleet network (labels count from the right: `com` is 0). The wildcard DNS
record and the internal certificate already cover every name. A second host
needs either a DNS record per customer pointing at its host (one Cloudflare
API call in the `create` job) or an edge Caddy whose map file the account
server generates from `hosted_servers`; the placement table exists from day
one so that choice can wait.

**Batch operations** live on a Servers tab of the Admin page, over the
same job queue: upgrade to a tag in waves (N servers at a time per host,
the next wave only when the last is healthy, the whole run paused on the
first failure), restart, suspend and resume, resize limits, send one
notice to every hosted admin. An `upgrade` job pulls the image once per
host, then per container: stop, start a new container with the same
configuration, wait for health. Gamma itself snapshots and migrates the
data directory at startup and refuses one newer than itself, so a rollback
is the previous tag plus Gamma's own pre-migration copy
([dev/migrations.md](../dev/migrations.md)). This is what the
`update-demo-server` skill does by hand for one container.

**Capacity.** A container is one uvicorn process; measure its idle
resident memory and the growth under a PDF-heavy session before buying
hosts, and place by `(host memory − 1 GB) / (resident + headroom)`. Disk is
the data directory, which the app's quota does not fully meter (databases
and derived files are outside it), so the agent's reported directory size
is what capacity planning and the per-customer disk alarm read; a hard cap
needs XFS project quotas on the data mount, which can come later.

**Backups and the trust boundary.** Each container runs its own off-site
copies into one bucket under its prefix, so a lost host is restored by
starting new containers and running `manage.py offsite --restore` against
each prefix; that runbook is the fleet's disaster recovery. The customer is
the admin of their container and the Backups pane masks the secret key, but
the key is still in that container's environment, so one compromised
container must not read another's copies: use per-prefix credentials where
the store supports them (Backblaze B2 application keys take a name prefix;
R2 tokens scope to a bucket, which would mean a bucket per customer).

## Lifecycle

The server's state follows the subscription:

| Subscription | Server | What the customer sees |
|---|---|---|
| `active`, `trialing` | `running`, limits of the plan | normal |
| `past_due` | `running`, 7 days of grace | a warning strip on `/plan` and a notice on the server |
| grace over, `unpaid` | `running`, `read_only` | writes refused with the reason; export works |
| `canceled` at period end | `read_only` for 30 days, then `stopped` | the countdown on `/plan`; the hostname answers a "this server is paused" page from Caddy |
| 90 days after cancel | `deleted`: container, volume, bucket prefix | a final mail a week before |
| Pro to Plus | limits drop at the next sync; the policy becomes `refuse` | members other than the owner lose sign-in; over-quota means no new uploads, nothing deleted |
| Plus to Pro | limits rise at the next sync; the policy becomes `invited` | invitations work |

Mail goes out at each transition from the account server's own mail
backends; Stripe's dunning mail is turned on for the payment side so the
two do not overlap. A username rename while a server exists changes its
hostname, public URL and redirect URI, so v1 refuses the rename while the
state is not `deleted`, and a later `update` job can allow it.

## Order of work

1. **Limits on the Gamma side**: `gamma/hosted.py` with the hourly sync,
   the KV cache, the defaults path in `server_settings`, the `invited`
   policy and account cap in `cloud_auth`, the `read_only` gate, the
   Server-pane rows and the notice. Testable against a fake account server
   like the existing cloud tests. This is the piece every other step
   depends on and it is useful before any money moves: an admin can set a
   plan by hand and watch a hand-run container obey it.
2. **The hosted-server tables and `/api/hosted/sync`** on the account
   server, with `hosted_servers` rows created by hand for the first few
   customers.
3. **The fleet agent and the job queue**, exercised on one host with hand
   enqueued `create` and `upgrade` jobs, plus the Caddy label route. At this
   point invited customers can be hosted by an admin's click.
4. **Stripe**: the billing module, the webhook, the effective-plan rule, the
   `/plan` page and the website's pricing page. Terms of service and a
   refund policy go up with it; the register page's terms link already
   exists.
5. **Lifecycle jobs**: grace, read-only, stop, delete, and their mail.
6. **Batch tooling** on the Admin page: waves, the Servers tab, capacity
   display, the stale-host alarm.

**Status (October 2026).** Steps 1 to 6 are built and merged, with the
mechanics in [dev/hosted.md](../dev/hosted.md),
[dev/billing.md](../dev/billing.md) and the "Hosted containers" subsection
of [dev/cloud_accounts.md](../dev/cloud_accounts.md). What differs from the
text above: a hosted server needs a confirmed e-mail before it is created;
a username rename is refused while a server exists; a label another
account ever held is never reused; a `paused` subscription has its own
page state; stale hosts are only flagged, never closed; deletion comes 60
days after the stop; the Caddy route by hostname label serves one host
only, and a stopped server's name is a plain 502 rather than a "paused"
page; upgrade waves count servers across the fleet, not per host; a
resize follows the plan rather than an admin's action; and lifecycle mail
covers the server's ready, read-only, stopped and deletion warning, with
none of its own for a failed payment. Later additions: a Lite plan ($2 a
month, 1 GB); a memory and CPU size per plan, with placement by the memory
committed to a host's servers plus a 1 GB reserve; resizes in place
through Docker's update; `logs` and `rollback` jobs and orphan containers;
the agent as a compose project of its own (`cloud/fleet/deploy/`),
published by `fleet.yml`; and hosted containers with no seeded admin and
no guests. Not built: a second host's routing, snapshot jobs, the `update`
and `sync` jobs, a notice to every hosted admin, Pro seats beyond ten, the
Plus storage add-on, the desktop launcher's first-run sign-in, and the
terms and privacy text.

Open decisions for the owner: whether to go with Stripe plus Stripe Tax or
a merchant of record; whether Pro seats beyond ten are sold at launch or
later; whether a Plus storage add-on ships with v1; and the retention
periods above, which the privacy policy must state.

Sources for the price anchors: [Zotero storage](https://www.zotero.org/storage),
[Obsidian plans and storage limits](https://obsidian.md/help/Plans+and+storage+limits).
