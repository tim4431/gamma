# Gamma Cloud operations: what the operator can do from the Admin page

A read of the account server, the fleet agent and the deploy guides in
October 2026, from the operator's chair: selling or holding back a plan,
deciding who may register, what happens when a payment starts and ends,
running containers on one host and on a second, updates, configuring a
customer's container, and what the operator learns about a running
server. Each section says what the code did then and what was missing;
the last one ranks the work and marks what has been built since. The
mechanics are in [dev/cloud_accounts.md](../dev/cloud_accounts.md),
[dev/billing.md](../dev/billing.md) and [dev/hosted.md](../dev/hosted.md).

## Which settings the operator can change, and where

| | where it lives | changed by |
|---|---|---|
| registration mode, Turnstile keys, blocked mail domains | `cloud.db` (`settings.py`) | Admin → Settings, at once |
| Stripe keys and price ids, the hosting domain, the shared server's address, mail, Google and GitHub, the default image tag | the environment (`config.py`) | editing `.env` on the VPS and restarting |
| what a plan contains, its price as shown, the container's size, the grace, read-only and deletion periods | constants in `config.py` | a new image |

The Settings tab shows only the first row. Nothing on the page says
whether Stripe is on, in test or live mode, which price ids are missing,
or which mail backend runs, so a wrong `.env` is found by its symptoms.

## Selling a plan, or holding it back

A plan is on sale when `billing.can_sell` says so: the Stripe secret is
set, and the plan's home is configured (`GAMMA_CLOUD_APP_URL` for Lite and
Plus, `GAMMA_CLOUD_HOSTED_DOMAIN` for Pro). A plan that is not shows a
disabled "Not available yet" button on `/plan`. So the display exists, but
no switch does:

- Lite and Plus share one condition and cannot be held back separately.
- Emptying the hosting domain to hold Pro back also empties every existing
  server's address (`hosted.url_of`) and stops provisioning for granted
  accounts. It is not a sales switch.
- An empty price id does not take a plan off sale. `can_sell` does not read
  the price ids, so the card still says "Choose Lite" and the checkout
  answers 400 "Unknown price."
- Removing the Stripe secret holds everything back, and with it the
  Customer Portal, the reconcile and the deletion of any account with a
  live subscription (refused with a 502).
- The portal's `switch` flow (`billing._flow`) and the cards of a
  subscriber (`pages_billing._button`) never ask `can_sell`. A Plus
  subscriber can move to Pro while hosting is off: they pay for Pro,
  `hosted.plan_changed` logs "hosting is off" and no server is made.
- `sites/site/pricing.html` is static and always offers all three.

## Who may register, and invites

The mode (`open`, `invite`, `closed`), Turnstile and the blocked domains
are editable and take effect at once. Invites are a code, uses left, a
plan and a note. What they lack:

- no expiry and no way to switch one off short of deleting it;
- no record of who used a code: `take_invite` only counts down, and the
  `account.create` audit row names the username and plan, not the code;
- a spent code stays in the list with 0 uses, and the list shows what is
  left, not how many were given;
- the plan an invite grants is a courtesy grant, which never lapses
  (`hosted.target`): an invite for Pro is a free server for good. There is
  no grant that ends on a date;
- no list of allowed mail domains (a university only), only blocked ones.

## A payment starting and ending

Start: `/plan` posts a price key, Stripe Checkout takes the payment, the
webhook reads the subscription from Stripe, writes the `subscriptions` row
and recomputes the plan as the higher of the grant and the subscription.
For Lite and Plus that is all: the allowance reaches the shared server as
a claim at the next sign-in, or within the hour. For Pro,
`hosted.plan_changed` makes the server row, places it on a host and queues
a `create` job, and the owner gets "Your Gamma is ready".

End: a cancel runs to the end of the paid period and can be undone until
then. After it a Lite or Plus library is closed with nothing deleted (the
shared server signs in only an account on a plan), and a Pro server is read-only at once, stopped after 30
days and deleted after 90, with a mail at each step. A failed payment
keeps the plan for 7 days. Deleting an account cancels at Stripe first.

This holds together. What it leaves out:

- **Nobody tells the operator.** A `create` job that fails leaves a paying
  customer on `provisioning` with the error in `report.note`. A server
  with no host to go on waits with "waiting for a host with room". A
  silent host is an audit row and a log line. A webhook recorded as
  `mismatch` or `unknown` is a log line. Each is found by opening the tab.
- A refund or a dispute changes nothing here (`charge.refunded` and
  `charge.dispute.created` are not handled); the subscription has to be
  cancelled in Stripe by hand.
- Deleted accounts are never purged on their own (`manage.py
  purge-deleted` is not scheduled), so their names and addresses stay
  taken.
- Known and listed elsewhere: no move of a library between Plus and Pro,
  no retention rule for a lapsed Lite or Plus library, no seats past ten.

## The fleet

**Several containers on one host.** This works. Placement counts
committed memory: a host's total, less 1024 MB, divided by Pro's 1536 MB,
so an 8 GB host takes four Pro servers. A server is made by granting Pro
on the Accounts tab or by *Provision*, and the Servers tab restarts,
stops, suspends, deletes, upgrades, rolls back and reads logs. The limits
of that model:

- one server per account, and none without an account, so no staging or
  canary container can be made from the page;
- the host is chosen by placement alone, and a server cannot be moved to
  another host;
- size and limits come from the plan only. No server can be given more
  storage, seats or memory than its plan says.

**A new host.** Installing the agent is five steps by hand: Docker, the
network with its pinned subnet, `/srv/gamma`, *Add host* for the token,
then the compose file and `.env`. It is short, and a script could do it
from the token alone. The real limit is routing: Caddy reaches a server
as `gamma-<label>` on a Docker network, which resolves only on Caddy's own
host. A server placed on a second host is unreachable, so every other
host has to stay closed for placement. The fleet is one host until each
server gets a DNS record of its own (one Cloudflare API call in the
`create` job, with a Caddy on every host) or an edge proxy routes by
label.

**Updates.** Nothing updates itself. The account server and the agent
are each updated by a skill that dispatches a build and pulls it on the
VPS. The shared server is pinned to a tag in `compose.yml`. A hosted
container moves only with an upgrade run from the Servers tab, in waves
that pause on the first failure. Two things make that harder than it
needs to be:

- the default tag is an environment variable, so changing what new
  servers run means editing `.env`;
- *outdated* compares tag names (`hosted.admin_view`). With the default
  tag `latest`, a container started months ago on `latest` is never
  outdated. The mark only means something when the default is a
  `sha-…` tag.

**Configuring a container.** The admin of a hosted container is its
owner (`GAMMA_CLOUD_ADMIN_SUBJECT` is their account id), not the
operator. Under the `invited` policy the operator cannot even sign in to
it, and that is the right default for a customer's private library. From
outside, the operator sets what the hourly sync carries (quota, per-file
cap, account cap, sign-in policy, off-site schedule, read-only), the
container's size and its image. The environment a container was created
with is fixed in `container.json`; an upgrade reuses it and no job
rewrites it, so a new variable or a rotated bucket key means work on the
host. No notice can be sent to every hosted admin.

## What the operator learns about a running server

- From the container, hourly (`/api/hosted/sync`): build, schema version,
  account count, upload bytes, data-directory bytes, public URL.
- From the agent, every five minutes: the host's memory and disk, and per
  container whether it runs, Docker's health, memory used and allowed,
  the data directory's size, the image.
- On request: the last 200 log lines.
- The audit log: every state change.

Each report replaces the one before, so there is no history and no trend.
Nothing says whether a server is used (a last write, accounts active this
week), how often it fails (5xx responses, restarts), or how much CPU it
takes, though the agent's `stats()` call already returns the CPU figures.
The shared server, where every free, Lite and Plus library lives, reports
nothing at all to the account server.

## Work, in the order worth doing

1. **Tell the operator.** Mail to an alert address for: a failed `create`
   or upgrade job, a server waiting for a host, a stale host, a host over
   85% of its memory or disk, a container down for two heartbeats, a
   webhook `mismatch`. An Overview tab that opens on the same list, with
   the counts the Servers and Billing strips already compute.
   *Built:* alerts mailed once they have lasted, and the Overview tab
   ([dev/hosted.md](../dev/hosted.md) "Alerts").
2. **Plans on sale as a setting.** A `plans_on_sale` row in `settings`,
   read by `can_sell`, with a checkbox per plan on the Settings tab and
   the reason a plan cannot be sold beside it (billing off, a price id
   missing, no home). `can_sell` should require the plan's price ids, and
   the `switch` flow should ask it. The website's pricing page reads the
   same answer or is rebuilt with it. *Built:* all of it, the website
   reading `/api/plans` ([dev/billing.md](../dev/billing.md) "Plans on
   sale").
3. **A configuration panel**, read-only: each environment setting as set,
   missing or off, Stripe's mode, and a *Send test mail* button. *Built:*
   the Settings tab's Configuration section
   ([dev/cloud_accounts.md](../dev/cloud_accounts.md) "Running").
4. **Invites**: an expiry date, on and off, uses given and left, the
   accounts that used each code, a copyable registration link, and a
   grant that ends on a date (`granted_until`, read by `recompute_plan`).
   *Built:* all of it ([dev/cloud_accounts.md](../dev/cloud_accounts.md)
   "Registration and the portal").
5. **The default image tag as a setting** on the Servers tab, with
   *Upgrade all outdated*, and *outdated* decided by the image the agent
   reports rather than by the tag's name. *Built:* with automatic upgrades
   besides ([dev/hosted.md](../dev/hosted.md) "Outdated").
6. **History.** A `metrics` table of samples from the heartbeat and the
   sync, thinned to an hour and kept 30 days, drawn as small trends on the
   Servers tab; CPU, restarts, last write and active accounts added to
   the reports. Counts only, and [PRIVACY.md](../../PRIVACY.md) says so.
   *Built:* the hourly samples, sparklines and a server's History
   ([dev/hosted.md](../dev/hosted.md) "History"); PRIVACY.md does not
   cover Gamma Cloud yet.
7. **Per-server overrides** of quota, seats and size, merged over the
   plan in `hosted.limits_for`, and an `update` job that recreates a
   container with a changed environment. *Built:* a server's own limits
   and environment ([dev/hosted.md](../dev/hosted.md) "What moves a
   server").
8. **A second host**: a DNS record per server and a Caddy per host, a
   bootstrap script for the agent, then a `move` job. *Being built:* the
   records, the Caddy and the script; no `move` job yet.
9. The shared server reporting usage per account, which the Plan page and
   a retention rule for lapsed plans both wait for. *Not built.*

Left alone on purpose: the operator as admin inside every customer's
container. If support needs it, the owner should grant it for a limited
time from their own Server pane.
