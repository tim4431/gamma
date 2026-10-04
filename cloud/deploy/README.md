# Deploying the account server

How `account.gammapdf.com` runs: the `gamma-cloud` image on a small VPS
with Caddy terminating TLS on the host's own ports, DNS at Cloudflare. A
Cloudflare Tunnel variant exists for a host without a public address (a
NAS). The service itself is described in
[docs/dev/cloud_accounts.md](../../docs/dev/cloud_accounts.md).

```
deploy/
  compose.yml          account, share + caddy (a VPS with a public address); caddy also on gamma-edge
                       and gamma-fleet; the fleet agent behind the `fleet` profile
  Caddyfile            TLS for CADDY_HOST → account:9002; *.gammapdf.com → share / gamma-demo /
                       gamma-<label> (hosted servers)
  compose.tunnel.yml   layered on compose.yml: cloudflared instead of caddy
  compose.build.yml    layered on compose.yml: build from ./src instead of pulling
  Dockerfile.local     the image built from a copy of cloud/ (compose.build.yml)
  .env.example         → .env: public URL, SMTP, Google/GitHub, hostname (not the sign-up gate)
  share.env.example    → share.env: the share host's cloud client and page hosts
  demo/                the public demo, its own compose project (demo/README.md)
```

The whole state of the service is the `data/` folder next to the compose
file (`cloud.db`, its backups). That folder is the secret: it holds the
signing keys and every token hash. Moving to another host is copying
`data/`, `.env` and the compose files and starting them there.

## The current deployment

`root@69.63.206.178`, folder `/root/Container/gamma-account/`, running
`compose.yml` with the GHCR image. Updates go through the
`update-account-server` skill (`.claude/skills/`). The public demo is its
own compose project in `/root/Container/gamma-demo/`
([demo/README.md](demo/README.md), the `update-demo-server` skill); this
project's Caddy only routes its name to it over the external network
`gamma-edge`, which must exist before this file starts
(`docker network create --subnet 10.202.0.0/24 gamma-edge`, once per host;
the demo trusts that subnet's `X-Forwarded-For`). The same goes for
`gamma-fleet` (`--subnet 10.203.0.0/24`), the hosted servers' network
("Hosted servers" below): create it before updating to a compose file
that names it.

The Gamma image believes `X-Forwarded-For` from loopback only
(`FORWARDED_ALLOW_IPS`), so every proxied Gamma here names its proxy: the
share host trusts this project's default network, whose subnet
`compose.yml` pins (`10.201.0.0/24`), and the demo trusts `gamma-edge`'s
(`EDGE_SUBNET` in its `.env`). Without that every visitor would share the
proxy's rate limits. A host whose default network predates the pin needs
`docker compose down && docker compose up -d` once to recreate it.

## First deployment on a VPS

1. **DNS.** In the Cloudflare zone add `A account → <the VPS address>`,
   proxied (orange cloud), and set SSL/TLS mode to **Full** for the zone
   (or a configuration rule for the hostname). Cloudflare holds the public
   certificate; between Cloudflare and the host Caddy serves its own
   internal certificate (`tls internal` in the Caddyfile), because Let's
   Encrypt cannot validate a proxied hostname. *Full (strict)* needs a
   Cloudflare Origin CA certificate mounted into Caddy instead (see the
   Caddyfile). A 521 from Cloudflare means it could not reach port 443 of
   the address in the record; a 526 means strict mode with the internal
   certificate.
2. **The folder** on the host:

   ```bash
   mkdir -p ~/Container/gamma-account && cd ~/Container/gamma-account
   curl -O https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/deploy/compose.yml
   curl -O https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/deploy/Caddyfile
   curl -o .env https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/deploy/.env.example
   # fill in .env: public URL, SMTP, Google/GitHub; CADDY_HOST is the hostname above
   # (Turnstile and the registration mode are set later on the Admin page, not here)
   chmod 600 .env
   # the network Caddy shares with the demo (demo/README.md), once per host
   docker network inspect gamma-edge >/dev/null 2>&1 || docker network create --subnet 10.202.0.0/24 gamma-edge
   # the network of the hosted servers ("Hosted servers" below), once per host
   docker network inspect gamma-fleet >/dev/null 2>&1 || docker network create --subnet 10.203.0.0/24 gamma-fleet
   docker compose up -d
   ```

3. **Check** from the host and from outside:

   ```bash
   curl -s http://127.0.0.1:9002/api/health
   curl -s https://account.gammapdf.com/.well-known/openid-configuration
   ```

   The `issuer` in the answer must be exactly `https://account.gammapdf.com`.
   `docker compose logs caddy` shows the internal certificate being made;
   from the host, `curl -k --resolve account.gammapdf.com:443:127.0.0.1
   https://account.gammapdf.com/api/health` must answer 200.
4. **The first admin and invites** (inside the container, where `manage.py`
   and `/data` are):

   ```bash
   docker compose exec account python manage.py create-account you@example.org you --admin --verified
   docker compose exec account python manage.py invite --uses 20 --note "friends"
   ```

   From then on the **Admin** page in the portal does this: accounts
   (search, plan, verify, admin, rename, delete), invites, the OIDC clients
   of hosted servers, the sign-up settings, the audit log.
5. **Sign in** at https://account.gammapdf.com/login, change the password
   under Settings, then register a second account in a private window with
   an invite code to see the verify mail arrive.

## The sign-up settings

The registration mode (`open` / `invite` / `closed`), the Cloudflare
Turnstile keys and extra blocked mail domains are edited on the **Admin
page → Settings**. They live in `cloud.db` and take effect without a
restart. `GAMMA_CLOUD_REGISTRATION`, `GAMMA_CLOUD_TURNSTILE_*` and
`GAMMA_CLOUD_BLOCKED_EMAIL_DOMAINS` in `.env` are not read; the startup
log names any still set so they can be deleted. `manage.py settings`
shows and sets the same values from the shell.

### Opening registration

Set **Registration** to `open`. The sign-up forms drop the invite field
and a new account gets the `free` plan. Invite codes still work and still
grant their plan.

Do these two **before** the switch:

1. **Turnstile**, on the same tab (how to get the keys is below). The check
   runs only with both keys stored, and the tab shows a warning while
   registration is open without it.
2. **The Cloudflare rate rule** on `/api/register` (below). The in-process
   limiter resets when the container restarts; Cloudflare's rule does not.

What else limits abuse, with nothing to configure:

- **The verify mail is the gate.** An unverified account cannot sign in to
  or connect any Gamma server.
- **One inbox, one account.** `f.o.o+1@gmail.com` and `foo@gmail.com` are
  the same mailbox, so the second registration is a 409.
- **Throwaway-mail domains are refused**, subdomains included. The
  Settings tab's list adds to the built-in one.
- **Five registrations an hour per IP**, with an IPv6 /64 counted as one.

A catch-all domain of someone's own still yields any number of verifiable
addresses; only Turnstile and the rate rules limit that. Watch the account
list and the audit log for a while after the switch.

## Mail from noreply@gammapdf.com

Cloudflare does not send outbound mail; any SMTP submission service does
(Resend, Postmark, Amazon SES, Brevo, Mailgun). The steps are the same
everywhere:

1. Add the domain `gammapdf.com` in the provider and put the DNS records it
   asks for (SPF as a TXT on the apex, one to three DKIM records, sometimes
   a return-path CNAME) into the Cloudflare zone. Wait for "verified".
2. Create an SMTP credential. Put it into `.env`:

   ```
   GAMMA_CLOUD_MAIL=smtp
   GAMMA_CLOUD_MAIL_FROM=Gamma Cloud <noreply@gammapdf.com>
   GAMMA_CLOUD_SMTP_HOST=smtp.resend.com      # or the provider's host
   GAMMA_CLOUD_SMTP_PORT=587
   GAMMA_CLOUD_SMTP_USER=resend               # Resend: literally "resend"
   GAMMA_CLOUD_SMTP_PASSWORD=<the API key>
   ```

3. `docker compose up -d` (the env is read at start), then request a
   password reset for your own account and watch the mail arrive.

The sender address needs no mailbox. If you want replies to reach you,
Cloudflare Email Routing can forward `noreply@gammapdf.com` (or better a
`support@`) to your inbox; that is inbound only and independent of the
above.

## Cloudflare settings worth turning on (once proxied)

- **Rate rules** (Security → WAF → Rate limiting): `/api/login`,
  `/api/register`, `/api/reset/request`, `/authorize/login` and `/token` —
  e.g. 30 requests per minute per IP. The server has its own in-process
  limits as the second line. With `open` registration `/api/register`
  is the one that matters: make its rule stricter than the rest.
- **Turnstile** (dashboard → Turnstile → add widget for the hostname,
  managed mode): put the site key and secret key into Admin → Settings;
  register and reset then show the widget.
- **Cache**: nothing to do — the server sets `Cache-Control: no-store` on
  the API and the pages; only `/jwks` is cacheable (5 min).
- **Access** is NOT used: the portal must be reachable by everyone.
- **Origin lock-down.** The server takes the client address (rate limits,
  the address on the Devices page) from Cloudflare's `CF-Connecting-IP`.
  On the VPS, Caddy also answers direct connections to port 443, so anyone
  who knows the host's address can bypass Cloudflare, including its rate
  rules, and set that header themselves. Accept Cloudflare only: allow 443
  from the ranges at <https://www.cloudflare.com/ips/> in the host's
  firewall, or in the Caddyfile before `reverse_proxy`:

  ```
  @direct not remote_ip <the Cloudflare ranges>
  abort @direct
  ```

  Before switching it on, check that Caddy sees the real peer address and
  not Docker's gateway: with access logging on, a request through the
  hostname must log a Cloudflare address. The tunnel variant opens no port
  and needs none of this.

## Sign in with Google and GitHub

Each provider shows up on the sign-in pages once both of its values are in
`.env`; leave them empty to offer only e-mail and password.

- **Google** (Google Cloud console → APIs & Services): configure the OAuth
  consent screen (external, app name *Gamma Cloud*, scopes `openid`,
  `email`, `profile`; publish it), then Credentials → Create OAuth client
  ID → *Web application*:
  - Authorized JavaScript origins: `https://account.gammapdf.com` (the
    one-tap prompt needs it; add `http://localhost` and
    `http://localhost:9002` for a local test).
  - Authorized redirect URIs: `https://account.gammapdf.com/oauth/google/callback`.
  - `GAMMA_CLOUD_GOOGLE_CLIENT_ID` / `_SECRET` in `.env`. The one-tap
    prompt ("Sign in to gammapdf.com with google.com") is on with it;
    `GAMMA_CLOUD_GOOGLE_ONE_TAP=0` turns it off.
- **GitHub** (Settings → Developer settings → OAuth Apps → New): homepage
  `https://gammapdf.com`, callback
  `https://account.gammapdf.com/oauth/github/callback`; generate a client
  secret; `GAMMA_CLOUD_GITHUB_CLIENT_ID` / `_SECRET` in `.env`.

Restart the container after editing `.env` (`docker compose up -d`).

## Connecting Gamma servers

- **The desktop app / any local Gamma**: Settings → Server → Sign-in →
  Account server `https://account.gammapdf.com`, server client empty,
  policy *Claim* or *Refuse*. Nothing to register on the account server:
  every local Gamma is the built-in desktop client.
- **A hosted Gamma** (a container you run for someone): create a client on
  the Admin page (kind *container*, callback
  `https://<name>.gammapdf.com/api/auth/cloud/callback`), and start that
  Gamma with `GAMMA_CLOUD_ISSUER`, the shown `GAMMA_CLOUD_CLIENT_ID` and
  `GAMMA_CLOUD_CLIENT_SECRET`, `GAMMA_CLOUD_POLICY=claim` and
  `GAMMA_CLOUD_ADMIN_SUBJECT=<the customer's account id>` (on the Admin
  page under the username), which makes that person its admin on first
  sign-in.

## The free share host

The compose file also runs `share`: one Gamma in cloud mode
(`ghcr.io/tim4431/gamma`, pinned to an image tag) that holds every free
account's published pages and answers `share.gammapdf.com` and the page
hosts `<username>-pages.gammapdf.com` ([docs/dev/cloud_accounts.md](../../docs/dev/cloud_accounts.md)
"The share host"). Setting it up once:

1. A wildcard DNS record at Cloudflare: `*` → this host's address, proxied.
   Named records (`account`) keep precedence; the universal certificate
   covers the first-level wildcard, and the Caddyfile's `*.gammapdf.com`
   site serves the internal certificate behind it (SSL mode "Full").
2. The share host's client on the account server:
   `docker compose exec account python manage.py create-client "Share host" share-host https://share.gammapdf.com/api/auth/cloud/callback`
   (the secret is shown once).
3. `share.env` from `share.env.example`: the client id and secret, and
   `GAMMA_CLOUD_ADMIN_SUBJECT` = your cloud account id, so your first sign-in
   there makes you its admin. The image also seeds an `admin` account with a
   random password printed once to the container's log while no account
   exists; delete it or set its password from Settings → Users afterwards.
4. `GAMMA_CLOUD_SHARE_HOST_URL=https://share.gammapdf.com` in `.env`, then
   `docker compose up -d` (the account server restarts with the new
   variable, `share` starts) and `docker compose exec caddy caddy reload
   --config /etc/caddy/Caddyfile` for the new site.
5. Check: `curl https://share.gammapdf.com/api/health` answers ok,
   `https://account.gammapdf.com/.well-known/openid-configuration` shows
   `gamma_share_host`, and from a linked desktop the share popover's
   Gamma Cloud section offers Publish.

The default storage quota per account on the share host is its Settings →
Server storage default; set it small. `share-data/` is the share host's
state (published pages and files) — back it up like `data/`.

## The demo server

`demo.gammapdf.com` is its own compose project in
`/root/Container/gamma-demo/`, with its own image pin, settings and data:
[demo/README.md](demo/README.md). This project holds only its way in: the
Caddyfile's `@demo` handle sends the name to `gamma-demo:9001`, and Caddy
joins the external network `gamma-edge` where the demo answers under that
alias. A stopped demo is a 502 for its name and nothing else here.

## Billing

Stripe checkout and subscriptions ([docs/dev/billing.md](../../docs/dev/billing.md))
are configured in `.env`:

- `GAMMA_CLOUD_STRIPE_SECRET`: the API secret key. Billing is off while it
  is empty (no checkout; plans are only granted by an admin or an invite).
- `GAMMA_CLOUD_STRIPE_WEBHOOK_SECRET`: the signing secret of the webhook
  endpoint, which Stripe calls at `POST https://account.gammapdf.com/api/billing/webhook`.
- `GAMMA_CLOUD_STRIPE_PRICE_PLUS_MONTH`, `_PLUS_YEAR`, `_PRO_MONTH`,
  `_PRO_YEAR`: the Stripe Price id behind each plan and interval.

`docker compose up -d` after editing them (the env is read at start).

## Hosted servers

A Plus or Pro account gets a Gamma container of its own at
`<username>.gammapdf.com`. The account server decides what should exist;
a fleet agent on each host does the Docker work
([docs/dev/hosted.md](../../docs/dev/hosted.md), the agent's own
[README](../fleet/README.md)). Turning it on for this host:

1. **The network.** `docker network create --subnet 10.203.0.0/24
   gamma-fleet`, once per host, before this compose file starts (Caddy
   joins it and refuses to start without it). Every hosted container joins
   it as `gamma-<label>` and trusts that subnet's `X-Forwarded-For`.
2. **The domain.** `GAMMA_CLOUD_HOSTED_DOMAIN=gammapdf.com` in `.env`. It
   must be the zone the Caddyfile's wildcard site serves and the wildcard
   DNS record covers (the share host's step 1 above already made both).
   The account server builds each server's public URL, OIDC callback and
   mail links as `https://<label>.<domain>`. Caddy routes every
   `<label>.gammapdf.com` that is not `share`, `demo` or a `-pages` host to
   `gamma-<label>:9001`. Empty means hosting is off: no server is created,
   whatever the plan.
3. **The host.** Admin → Servers → *Add host* (or `docker compose exec
   account python manage.py add-host vps-1`) shows the agent's token once.
   Put it into `.env` as `GAMMA_FLEET_HOST_TOKEN`, add
   `COMPOSE_PROFILES=fleet`, and, for off-site copies, the
   `GAMMA_FLEET_S3_*` bucket (each server copies under
   `hosted/<account id>/`).
4. **The agent image.** No workflow publishes it yet: on the host, from a
   checkout, `docker build -t ghcr.io/tim4431/gamma-fleet:latest
   cloud/fleet`.
5. `docker compose up -d`, then `docker compose exec caddy caddy reload
   --config /etc/caddy/Caddyfile`. Within five minutes the host shows on
   the Servers tab with its memory and disk; until its first heartbeat it
   takes no servers.
6. **A first server by hand.** Give an account a Plus or Pro plan on the
   Accounts tab (a courtesy grant), or *Provision* its account id on the
   Servers tab. The agent pulls the image, starts `gamma-<username>` with
   its data in `/srv/gamma/<username>/data`, waits for its health check,
   and the account gets a "Your Gamma is ready" mail.

A second host runs only the agent (`cloud/fleet/README.md` has the
`docker run` line, with `GAMMA_FLEET_ACCOUNT_URL=https://account.gammapdf.com`).
Caddy on this host cannot reach its containers, so a server placed there
also needs a DNS record of its own pointing at that host and a proxy
there; until that exists, close the second host for placement or keep to
one host.

The Servers tab is the operator's view of hosts, servers, upgrade waves
and the job queue ([docs/dev/hosted.md](../../docs/dev/hosted.md)
"Admin"). A host silent for 15 minutes shows *stale* and takes no new
servers until it reports again. `manage.py hosts`, `servers`, `jobs`,
`add-host` and `provision` do the same from the shell.

## Updating

```bash
cd ~/Container/gamma-account && docker compose pull account && docker compose up -d
```

(`/update-account-server` does this after checking the publish finished,
and verifies the running commit afterwards.) To run a patch from source
instead, copy `cloud/` to `./src` and layer `compose.build.yml` with
`--build`.

The server upgrades its own `cloud.db` at start with a copy taken first
(`data/backups/*-v<N>.db`) and refuses a database written by a newer
build, so a rollback is the previous image plus that copy.

## Backups

There is no scheduled backup. The server copies `cloud.db` to
`data/backups/*-v<N>.db` before a schema upgrade (the last 3 kept), and
`manage.py backup` takes a `*-manual.db` on demand. Treat any copy as a
secret.

## The tunnel variant (a NAS)

Zero Trust → Networks → Tunnels → create, public hostname
`account.gammapdf.com` → `http://account:9002`, the connector token into
`.env` as `TUNNEL_TOKEN`, then
`docker compose -f compose.yml -f compose.tunnel.yml up -d`. No port is
opened and Cloudflare terminates TLS; the DNS record is the tunnel's.
