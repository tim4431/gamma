# Deploying the account server

How `account.gammapdf.com` runs: the `gamma-cloud` image on a small VPS
with Caddy terminating TLS on the host's own ports, DNS at Cloudflare. A
Cloudflare Tunnel variant exists for a host without a public address (a
NAS). The service itself is described in
[docs/dev/cloud_accounts.md](../../docs/dev/cloud_accounts.md).

```
deploy/
  compose.yml          account, share, demo + caddy (a VPS with a public address)
  Caddyfile            TLS for CADDY_HOST → account:9002; *.gammapdf.com → share / demo
  compose.tunnel.yml   layered on compose.yml: cloudflared instead of caddy
  compose.build.yml    layered on compose.yml: build from ./src instead of pulling
  Dockerfile.local     the image built from a copy of cloud/ (compose.build.yml)
  .env.example         → .env: public URL, registration mode, SMTP, Turnstile, Google/GitHub, hostname
  share.env.example    → share.env: the share host's cloud client and page hosts
  demo.env.example     → demo.env: the public demo (demo mode, guest expiry and cap, first admin)
```

The whole state of the service is the `data/` folder next to the compose
file (`cloud.db`, its backups). That folder is the secret: it holds the
signing keys and every token hash. Moving to another host is copying
`data/`, `.env` and the compose files and starting them there.

## The current deployment

`root@69.63.206.178`, folder `/root/Container/gamma-account/`, running
`compose.yml` with the GHCR image. Updates go through the
`update-account-server` skill (`.claude/skills/`); the demo next to it
through `update-demo-server`.

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
   # fill in .env: SMTP, Turnstile; CADDY_HOST is the hostname above
   chmod 600 .env
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
   of hosted servers, the audit log.
5. **Sign in** at https://account.gammapdf.com/login, change the password
   under Settings, then register a second account in a private window with
   an invite code to see the verify mail arrive.

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
  limits as the second line.
- **Turnstile** (dashboard → Turnstile → add widget for the hostname,
  managed mode): the site key and secret go into `.env`; register and
  reset then show the widget.
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

The compose file also runs `demo`: a Gamma (`ghcr.io/tim4431/gamma`) in
demo mode at `https://demo.gammapdf.com`, where anyone can try Gamma
without an account. **Try the demo** on its login page makes a throwaway
guest account that is deleted with its workspace after
`GAMMA_GUEST_TTL_HOURS`; the admin's shared AI key is metered per guest.
What demo mode changes and how guests work:
[docs/dev/guests.md](../../docs/dev/guests.md). The image is pinned to the
`sha-<short>` tag of a branch build (`docker.yml` dispatched on the branch,
which never moves `:latest`), and the `update-demo-server` skill
(`.claude/skills/`) builds, pins and restarts it. That skill rewrites the
`demo` image line in the host's `compose.yml`, so the host's line is the
one that counts.

Setting it up once:

1. **DNS.** At Cloudflare, `A demo → <the VPS address>`, proxied. The `*`
   record of the share host already routes the name here; the named record
   keeps the demo up if the wildcard ever changes. The Caddyfile's
   `*.gammapdf.com` site answers it with the internal certificate (SSL mode
   "Full") and proxies it to `demo:9001`.
2. **`demo.env` first.** Every `docker compose` command in the folder
   refuses to run while a file named by `env_file` is missing, the account
   server's updates included, so create it before the new `compose.yml`
   arrives:

   ```bash
   cd ~/Container/gamma-account
   curl -o demo.env https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/deploy/demo.env.example
   chmod 600 demo.env
   # optional: GAMMA_ADMIN_PASSWORD, GAMMA_GUEST_MAX, GAMMA_GUEST_SEED
   ```

3. **The files.** Copy the new `compose.yml` and `Caddyfile` over the
   host's (from the branch that has them, as the `update-demo-server` skill
   does), then start the demo and load the new site:

   ```bash
   docker compose up -d demo
   docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
   ```

   Write the Caddyfile in place (`cat > Caddyfile`), never with `sed -i` or
   an editor that replaces the file: it is bind-mounted as a single file,
   and a new inode stays invisible to the running container.
4. **Check.** `curl -s https://demo.gammapdf.com/api/server-config` contains
   `"demo":true`, and the page opens on **Try the demo**.
5. **The admin, once.** With `GAMMA_ADMIN_PASSWORD` empty the container
   prints a random password for `admin` once:
   `docker compose logs demo | grep -A2 "created the admin account"`. Sign
   in through **Admin sign-in** on the login page, change the password
   (Settings → Users), then under Settings → Server → Shared AI provider
   add the shared key, turn **Guests may use it** on and set the per-guest
   allowance. AI keys are never environment variables.
6. **Optional sample library.** Prepare a workspace (the admin's own here
   will do), export it (Settings → Workspaces → Export, or download a
   snapshot from Settings → Backups), copy the zip to
   `demo-data/guest-seed.zip`, uncomment `GAMMA_GUEST_SEED` in `demo.env`
   and `docker compose up -d demo`. Every new guest starts from a copy.

`demo-data/` is the demo's whole state and it is disposable: no backup.
Wiping it (`docker compose stop demo && rm -rf demo-data && docker compose
up -d demo`) resets the demo to a fresh instance: a new admin password in
the log, the shared key and allowance to enter again, and the seed zip to
copy back if one was used.

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
