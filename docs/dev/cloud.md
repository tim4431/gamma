# Gamma Cloud: how it fits together

Gamma itself is a self-hosted app: one server, one data directory, the
people its admin lets in. Gamma Cloud is the hosted side of it: one
account for everyone, a shared server where most libraries live, a
container of one's own for a Pro account, and one operator who runs all
of it from a web page. This document is the map. The mechanics of each
part are in [cloud_accounts.md](cloud_accounts.md) (identity and the
portal), [billing.md](billing.md) (plans and Stripe) and
[hosted.md](hosted.md) (containers and the fleet); the deploy guides are
[cloud/deploy/README.md](../../cloud/deploy/README.md) and
[cloud/fleet/deploy/README.md](../../cloud/fleet/deploy/README.md).

## The pieces

```
                       people, the desktop app, the extension, MCP clients
                                            |
                                     Cloudflare (DNS, proxy, Turnstile)
                                            |
        +-----------------------------------+-----------------------------------+
        |                                   |                                   |
 gammapdf.com                    account.gammapdf.com              *.gammapdf.com
 the website                     the ACCOUNT SERVER                 Caddy on the VPS
 (a Cloudflare Worker)           cloud/  one process, cloud.db        |
                                   - who you are (OIDC)               +-- app / share / <user>-pages
                                   - your plan, Stripe                |     the SHARED SERVER: one Gamma
                                   - the fleet's job queue            |     in cloud mode (Free, Lite, Plus)
                                   - the Admin page                   |
                                            ^                         +-- <user>-user.gammapdf.com
                                            |  heartbeats, jobs       |     a HOSTED SERVER per Pro account:
                                            |                         |     gamma-<label>, its own data dir
                                     the FLEET AGENT  ----------------+
                                     one per host, holds the Docker socket
                                            |
                                     /srv/gamma/<label>/      an S3 bucket
                                     the servers' data        off-site copies
```

| piece | what it is | where it runs | its state |
|---|---|---|---|
| **Account server** | `cloud/`, package `gammacloud`: accounts, the sign-in portal, an OpenID Connect provider, plans and billing, the hosts and the job queue, the Admin page. It holds no notes or files. | `/root/Container/gamma-account/` on the VPS, image `ghcr.io/tim4431/gamma-cloud` | `data/cloud.db` — the secret: signing keys and every token hash |
| **Shared server** | one ordinary Gamma (`ghcr.io/tim4431/gamma`) in cloud mode: everyone may sign in, published pages live here, a Lite or Plus plan is an allowance on an account here | the same compose project, service `share` | `share-data/` |
| **Hosted servers** | one Gamma container per Pro account, `gamma-<label>`, which its owner administers | on a fleet host, started by the agent | `/srv/gamma/<label>/data`, plus hourly copies in the bucket under `hosted/<account id>/` |
| **Fleet agent** | `cloud/fleet/`, package `gammafleet`: creates, starts, stops, upgrades, rebuilds and removes those containers, reports every five minutes. Calls out only. | `/root/Container/gamma-fleet/` on each host, image `ghcr.io/tim4431/gamma-fleet` | `container.json` per server (what it was started with) |
| **Caddy** | TLS and routing by hostname: `account` → the account server, `app`/`share`/`<user>-pages` → the shared server, `demo` → the demo, `<label>-user` → `gamma-<label>` | the account project on the VPS; one per routed host besides | — |
| **Cloudflare** | DNS for the zone (a wildcard plus named records), the proxy and certificate in front of everything, Turnstile on sign-up, the rate rules | — | — |
| **Stripe** | Checkout, the Customer Portal, subscriptions, invoices, tax. The account server keeps a copy of each subscription and is told by webhook. | — | the source of truth for money |
| **Website** | `sites/`: the front page, pricing, the rendered docs | a Cloudflare Worker | — |

One VPS runs the account server, the shared server, Caddy and one fleet
agent today. A second host runs an agent and a Caddy of its own, and the
servers on it get DNS records of their own ("Hosts" below).

## Names

| name | answers | from |
|---|---|---|
| `gammapdf.com` | the website | the Worker |
| `account.gammapdf.com` | the portal, the OIDC endpoints, the Admin page, the APIs the agents and containers call | the account server |
| `app.gammapdf.com` | the shared server, for people: sign in, your library | `share` |
| `share.gammapdf.com` | the same server, the address Gamma servers publish pages to (fixed, since mirrors remember it) | `share` |
| `<username>-pages.gammapdf.com` | that person's published pages | `share` |
| `<username>-user.gammapdf.com` | that person's hosted server | `gamma-<label>` |
| `demo.gammapdf.com` | the public demo, its own compose project | `gamma-demo` |

Every hosted name ends in `-user` and every page host in `-pages`, so no
username can take a service's name. People are told one address,
`app.gammapdf.com`; the account server sends a Pro owner on to their own
server at sign-in ("The entrance" in cloud_accounts.md).

## What happens

**Signing in.** Every Gamma server — the desktop app's sidecar, the shared
server, a hosted container, a server someone runs themselves — is an OIDC
client of the account server. A sign-in is an authorization-code flow
with PKCE; the ID token carries the account id (which never changes), the
username, the plan and, on a paid plan, the storage it allows. The
server mints its ordinary session from it. An unconfirmed e-mail address
is the gate: such an account can use the portal but cannot sign in to
any Gamma. A Gamma server never calls the account server on a data
request.

**Registering.** The operator decides who may: `open`, by `invite` code,
or `closed`, on the Admin page and with effect at once. Turnstile, one
account per inbox, a throwaway-domain list, an allowed-domain list (a
university only), rate limits and Cloudflare's rules bound an open
sign-up. An invite code can carry a plan, a number of uses, an expiry
and a grant that ends after so many days, and it remembers who used it.

**Buying a plan.** The Plan page sends the browser to Stripe Checkout
with a price the server looked up; the browser never names a plan. The
webhook reads the subscription back from Stripe and the account's
effective plan becomes the higher of what an admin or invite granted and
what the subscription pays for. For Lite and Plus that is all: the
allowance reaches the shared server as a claim within the hour. For Pro
the account server makes a server row, picks a host with room, mints the
container's own OIDC client and queues a `create` job; the agent pulls
the image, starts the container, waits for its health check, and the
owner gets "Your Gamma is ready". Which plans may be bought at all is a
switch on the Admin page: a plan held back reads "Coming soon" on the
Plan page and the website, and a subscriber cannot switch to it.

**A plan ending.** A cancel runs to the end of the paid period. A failed
payment keeps the plan 7 days. After that a Lite or Plus account keeps
its library under the free allowance; a Pro server turns read-only at
once (reads and exports work), is stopped after 30 days and deleted after
90, with a mail at each step. A grant that was given a date ends the
same way. Deleting an account cancels at Stripe first and purges the row
30 days later.

**What a container knows.** A hosted container never hears about money.
At startup and every hour it posts a short report to the account server
(build, schema, accounts, active accounts, bytes, last write, errors,
uptime) and gets back its limits: plan, status, read-only, sign-in
policy, account cap, quota, per-file cap, off-site schedule, and one
sentence to show. The operator can give one server numbers of its own
over its plan's. Everything else about the container is its owner's:
the operator is not an admin inside it and, under the `invited` policy,
cannot sign in to it.

**Keeping the fleet current.** New servers run the default image tag, a
setting on the Servers tab. A server is *outdated* when its tag is not
the default or the agent reports that the tag has moved in the registry.
An upgrade run moves servers in waves and pauses on the first failure;
the agent keeps the previous container until the new one is healthy, so
a failed upgrade is rolled back with one job. With automatic upgrades on,
the hourly tick starts such a run by itself, one server at a time. Extra
environment variables, fleet-wide or per server, reach running
containers the same way, through `update` jobs in waves.

**Watching it.** Every heartbeat and sync becomes an hourly sample kept
for 30 days, drawn as trends on the Servers tab. A fixed list of problems
is derived from the state on every heartbeat, job result and tick — a
failed job, a server waiting for a host, a stale host, a host that is
full, a container that is down, a missing DNS record, a Stripe event
that matched no account, open registration without Turnstile — and each
new one is mailed to the operator once, after it has persisted for the
time its kind allows. The Overview tab lists what needs attention.

## Hosts

A fleet host is a machine with Docker, the agent and a token the Admin
page shows once. The agent only calls out, so a host needs no open port
for it. Two kinds:

- **the entrance's own VPS**, where the account project's Caddy reaches
  every container by name on the `gamma-fleet` network and the wildcard
  DNS record covers every hostname. This host has no `public_ip`;
- **a routed host** elsewhere: its agent's compose project also runs a
  Caddy (`COMPOSE_PROFILES=edge`), the host has a `public_ip` on the
  Servers tab, and every server placed there gets a Cloudflare DNS record
  of its own, made and removed by the account server (`dns.py`, with a
  token in its environment). Without that token a routed host takes no
  servers.

`cloud/fleet/deploy/bootstrap.sh` sets up a fresh host in one command,
with `--edge` for a routed one and `--auto-update` for a daily pull of
the agent image. Placement counts committed memory: a host's total less
1 GB kept for itself, divided by what each server's plan (or its own
override) reserves.

## Where a setting lives

| kind | examples | changed by |
|---|---|---|
| **settings** in `cloud.db`, effect at once | registration mode, Turnstile, blocked and allowed mail domains, plans on sale, alerts and their address, the default image tag, automatic upgrades, fleet-wide environment | Admin → Settings and Servers, or `manage.py settings` |
| **environment** of the account server | the public URL, mail, Stripe keys and price ids, the shared server's addresses, the hosting domain and suffix, the fleet image, Google/GitHub, the Cloudflare DNS token | `.env` on the VPS and a restart; the Settings tab shows each as set, missing or off |
| **code** | what a plan contains and costs, the container size per plan, the grace, read-only and deletion periods, the alert kinds and their delays | `config.py`, `alerts.py`; a new image |
| **per server** | its own quota, seats and size, its own environment variables, suspend | the Servers tab's Actions |

## The Admin page

`account.gammapdf.com/admin`, for accounts with the admin flag, in tabs
that deep-link by hash:

| tab | what |
|---|---|
| Overview | what needs attention, then the numbers: accounts, paying and monthly revenue, servers by state, hosts, failed jobs |
| Accounts | search; the granted plan and when it ends; verify, rename, admin, delete, restore, purge |
| Invites | codes with uses, plan, expiry, grant length, who used them; on/off; a copyable sign-up link |
| Clients | the OIDC clients of hosted servers and the share host, and the ones people connected themselves |
| Servers | hosts and their capacity; every hosted server with its state, version, data, trends and actions (restart, stop, suspend, limits, environment, logs, history, upgrade, rollback, delete); the default image and upgrade runs; fleet-wide environment; the job queue |
| Billing | subscriptions by status with a link into Stripe, the newest webhook events |
| Settings | sign-up, plans on sale, alerts, and the read-only configuration panel with a test mail |
| Audit log | every account-changing event, with its actor |

`manage.py` does the same from the shell inside the container, and the
page, the CLI and the `/api/admin/*` API call the same functions.

## Trust

- The account server is the one process on the public internet that
  holds secrets for everyone; it never holds the Docker socket. The agent
  holds the socket and is told what to do; whoever holds a host's token
  receives that host's jobs, client secrets included, so the token and
  the agent's `.env` are secrets.
- Every secret at rest in `cloud.db` is a hash of a random token. A
  container's client secret exists in clear only in the job that creates
  it, and is blanked when the job ends; so are environment values.
- Each hosted container trusts `X-Forwarded-For` from the fleet network's
  pinned subnet only, and the account server reads the client address
  from Cloudflare's header only, which is why the origin must accept
  Cloudflare alone (the deploy README's lock-down).
- The bucket key reaches every container; it must be scoped to that one
  bucket.
- Customers' data is theirs: the operator sees counts, sizes and the last
  200 log lines, never content, and has no account inside a customer's
  server.

## Updating the parts

| part | how |
|---|---|
| the account server | the `update-account-server` skill: build from a branch, pull on the VPS, restart; `cloud.db` upgrades itself at start with a copy taken first |
| the fleet agent | the `update-fleet` skill, or the daily pull `bootstrap.sh --auto-update` installs; hosted containers keep running through it |
| hosted containers | an upgrade run on the Servers tab, or automatic upgrades |
| the shared server | the image tag pinned in the account project's `compose.yml` |
| the demo | the `update-demo-server` skill |
| the website | the `build-site` skill |

`update-needed check` says which of these is behind the code.

## Reading on

- [cloud_accounts.md](cloud_accounts.md): the portal, registration, the
  OIDC provider, the entrance, the preference profile, the Gamma side of
  sign-in, the share host.
- [billing.md](billing.md): the effective-plan rule, Stripe, the Plan
  page, the Billing tab, running it locally in test mode.
- [hosted.md](hosted.md): the tables, server states and the lifecycle, the
  sync, the fleet API and the agent, alerts and history, the Servers tab.
- [research/cloud-plans.md](../research/cloud-plans.md) and
  [research/cloud-operations.md](../research/cloud-operations.md): why it
  is shaped this way, and what was found missing before the operator's
  tools were built.
