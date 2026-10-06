# Deploying the fleet agent

The agent is one container per host of hosted Gamma servers. It holds
the Docker socket and works through the job queue the account server
keeps for that host: create, start, stop, restart, upgrade or resize, roll
back and delete the `gamma-<label>` containers and fetch their logs, and a
heartbeat every five minutes. What it does and the API it speaks are in
[docs/dev/hosted.md](../../../docs/dev/hosted.md); the package and its
tests are in [../README.md](../README.md).

```
fleet/deploy/
  compose.yml    the agent: the image, .env, the Docker socket, /srv/gamma, the gamma-fleet network;
                 and, under the profile edge, the Caddy of a host with a public IP of its own
  Caddyfile      that Caddy: <label>-user.<domain> → gamma-<label>:9001
  .env.example   → .env: the account server's address, this host's token, container defaults,
                 the off-site bucket, the edge profile and its domain
  bootstrap.sh   one command that installs or updates all of it on a host
```

It is a compose project of its own, apart from the account server's
([cloud/deploy/](../../deploy/README.md)). Updating, restarting or moving
the agent never touches the portal, the share host or the demo, and
their updates never touch the agent. The two projects share only the
external network `gamma-fleet` on the account server's VPS. Caddy joins
it on the account side to route `<label>-user.gammapdf.com` to
`gamma-<label>:9001`, and the agent starts every hosted container on it.

## Where it runs

`root@69.63.206.178`, folder `/root/Container/gamma-fleet/`, next to the
account server's `/root/Container/gamma-account/` and the demo's
`/root/Container/gamma-demo/`. The `update-fleet` skill
(`.claude/skills/`) updates it.

**Two kinds of host.** A proxy finds a hosted server by its container
name on `gamma-fleet`, and a Docker network's names resolve only on its
own host, so every host needs a proxy beside its servers:

- **The account server's VPS** has no public IP on the Servers tab. The
  account project's Caddy routes its servers, and the zone's wildcard
  record points there. Its agent runs without the `edge` profile: that
  Caddy already holds ports 80 and 443, and a second one would not start.
- **Any other host** has its public IP set on the Servers tab: a *routed*
  host. It runs its own Caddy (the `edge` profile, this folder's
  `Caddyfile`), and the account server gives each server placed on it a
  proxied DNS record of its own at Cloudflare,
  `<label>-user.gammapdf.com` → the host's IP, which wins over the
  wildcard ([docs/dev/hosted.md](../../../docs/dev/hosted.md)
  "Deployment"). That needs `GAMMA_CLOUD_CF_API_TOKEN` and
  `GAMMA_CLOUD_CF_ZONE_ID` in the account server's `.env`; without them a
  routed host takes no servers, and the Servers tab says *no dns token:
  closed*.

## First deployment

### On the account server's VPS

1. **The network.** It exists already if the account project runs, since
   its Caddy refuses to start without it:

   ```bash
   docker network inspect gamma-fleet >/dev/null 2>&1 || docker network create --subnet 10.203.0.0/24 gamma-fleet
   ```

   The subnet is pinned because every hosted container trusts it for
   `X-Forwarded-For` (the agent hands it over as `FORWARDED_ALLOW_IPS`).
2. **The data root.** `mkdir -p /srv/gamma`.
3. **The host.** On the portal, Admin → Servers → *Add host* with a name
   (`vps-1`), or from the account project:

   ```bash
   cd /root/Container/gamma-account && docker compose exec account python manage.py add-host vps-1
   ```

   Either one shows `GAMMA_FLEET_HOST_TOKEN=gf_…` once.
4. **The folder.**

   ```bash
   mkdir -p ~/Container/gamma-fleet && cd ~/Container/gamma-fleet
   B=https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/fleet/deploy
   curl -o compose.yml $B/compose.yml
   curl -o .env $B/.env.example
   chmod 600 .env
   # in .env: GAMMA_FLEET_HOST_TOKEN from step 3, and the GAMMA_FLEET_S3_* bucket for off-site copies
   docker compose up -d
   ```

5. **Check.** `docker compose logs --tail 20 fleet` starts with
   `gamma-fleet <version>: https://account.gammapdf.com, network
   gamma-fleet, data /srv/gamma` and has no `heartbeat failed` or `job poll
   failed` after it. The first heartbeat goes out at start, so the
   Servers tab shows the host seen *just now* with its memory and disk. A
   host takes servers only after that first heartbeat.

`bootstrap.sh` without `--edge` ("On a fresh host" below) does steps 1, 2,
4 and 5 in one command, and works here too.

The account side (`GAMMA_CLOUD_HOSTED_DOMAIN`, the Caddyfile's wildcard
route, a first server by hand) is in the account server's README,
[Hosted servers](../../deploy/README.md#hosted-servers).

### Moving from the account project's `fleet` service

Older account compose files ran the agent as their own `fleet` service
behind the `fleet` profile. Never run two agents on one host. Both
would work the same Docker daemon, so stop the old one first:

1. Copy the account project's new `compose.yml` (it has no `fleet`
   service) and run `docker compose up -d --remove-orphans` there. That
   removes the old agent's container. The hosted containers are not
   compose services and keep running.
2. Start this folder's `.env` from `.env.example` (step 4's `curl`), which
   holds `GAMMA_FLEET_ACCOUNT_URL`: the old service set that address in
   the compose file, not in `.env`, and the agent refuses to start without
   it. Then move `GAMMA_FLEET_HOST_TOKEN` and the `GAMMA_FLEET_S3_*` lines
   by hand from the account project's `.env` into it, and delete them and
   `COMPOSE_PROFILES=fleet` from the account one. The token stays the same
   host.
3. Steps 4 and 5 above.

### On a fresh host

*Add host* on the Servers tab (each host has its own name and token)
answers with the token and one line to run on the host as root:

```bash
curl -fsSL https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/fleet/deploy/bootstrap.sh \
  | bash -s -- --token gf_… [--edge] [--domain gammapdf.com] [--account-url https://account.gammapdf.com] \
    [--dir ~/Container/gamma-fleet] [--ref main] [--auto-update]
```

(`manage.py add-host` prints the same line.) `bootstrap.sh`:

- installs Docker through get.docker.com when `docker` is missing;
- creates `gamma-fleet` with the pinned subnet `10.203.0.0/24` when it is
  absent (and warns when it has another subnet), and `/srv/gamma`;
- downloads `compose.yml`, `Caddyfile` and `.env.example` of `--ref` into
  `--dir`, replacing the first two, so a pin in `compose.yml` does not
  survive a run;
- writes `.env` (mode 600) from `.env.example`, or keeps the one there and
  sets only what the options name: the token, the account server's address,
  `GAMMA_FLEET_DOMAIN`, and `COMPOSE_PROFILES=edge` with `--edge`;
- with `--auto-update`, installs a systemd timer, `gamma-fleet-update`,
  that runs `docker compose pull --quiet && docker compose up -d` in the
  folder once a day;
- pulls, starts the project and prints the agent's log with what to look
  for (step 5 above), then what is left to do by hand.

It is safe to run again: a second run downloads the files and pulls the
images again and changes nothing else, and without `--token` it keeps the
token in `.env`. Left by hand: the bucket (`GAMMA_FLEET_S3_*`, the same bucket as
the other hosts, then `docker compose up -d`). The agent only calls out,
so a host without `--edge` needs no open port and no DNS record.

### A second host, with its own Caddy

1. **The account server** needs a Cloudflare API token with *Zone → DNS →
   Edit* on the zone, and the zone's id (the zone's Overview page):
   `GAMMA_CLOUD_CF_API_TOKEN` and `GAMMA_CLOUD_CF_ZONE_ID` in its `.env`,
   then `docker compose up -d` there. Admin → Settings → Configuration
   shows *DNS records* on.
2. **The host**: *Add host* with its **public IP** (IPv4 or IPv6), then the
   line it shows, which has `--edge`, on the host. A host added without
   one gets it with *Public IP…* in its row. `--domain` is needed only
   when the hosting domain is not `gammapdf.com`, and
   `GAMMA_FLEET_SUFFIX` in `.env` only when the account server's
   `GAMMA_CLOUD_HOSTED_SUFFIX` is not `-user`.
3. **The firewall**: 80 and 443 from Cloudflare's ranges only ("Security"
   below).
4. **Check.** The host shows *dns on* in the Hosts table. Each server
   placed on it shows *dns pending* until its record is made (at once
   after the placement, else within the hour) and then *dns ok*; the
   error of a failed try is on the pill's hover. From the host,
   `curl -k --resolve <label>-user.gammapdf.com:443:127.0.0.1
   https://<label>-user.gammapdf.com/api/health` answers 200 once the
   server runs, and the same URL answers from anywhere through
   Cloudflare.

`docker compose logs caddy` shows the internal certificate being made. A
521 from Cloudflare means it could not reach port 443 of the host; a
name that is a 404 here is not `<label>-user.<domain>`.

Never use `--edge` (or `COMPOSE_PROFILES=edge`) on the account server's
VPS: its account project's Caddy holds 80 and 443, and its servers are
reached through the wildcard record. Leave its public IP blank on the
Servers tab for the same reason.

## Updating

```bash
cd ~/Container/gamma-fleet && docker compose pull && docker compose up -d
```

That pulls Caddy too on a host with the `edge` profile. Running
`bootstrap.sh` again does the same and also takes the newest
`compose.yml` and `Caddyfile`; a host bootstrapped with `--auto-update`
pulls and restarts by itself once a day.

`fleet.yml` publishes `ghcr.io/tim4431/gamma-fleet:latest` and
`:sha-<short>` on a push to `main` that touches `cloud/fleet/`, or when
dispatched from any branch (`gh workflow run fleet.yml --ref dev`). The
`update-fleet` skill dispatches it, deploys it and checks the result.

The hosted containers are their own containers (restart
`unless-stopped`), so restarting the agent leaves them running. On
SIGTERM the agent finishes the job it holds before it exits, which is
why the compose file allows five minutes to stop. A job cut off anyway
stays `running` until the account server fails it after an hour; retry it
from the Jobs table. To roll back, pin the previous build in
`compose.yml` (`image: ghcr.io/tim4431/gamma-fleet:sha-<old>`) and
`docker compose up -d`.

## What /srv/gamma holds

Per server, `/srv/gamma/<label>/`:

- `data/`, the server's whole Gamma data directory: its database,
  uploads and its own backups. It is mounted on the container's `/data`;
- `container.json` (mode 600), what the container was created with:
  image, environment and limits. The environment includes the
  container's OIDC client secret and the bucket key.

This is the customers' data. Back it up: the off-site bucket holds each
server's own copies on its plan's schedule, but bringing a whole host
back takes a copy of `/srv/gamma` itself (a disk snapshot, or a nightly
`restic` or `rsync` elsewhere). Because of `container.json`, treat every
copy as a secret.

## Security

- **The socket is root.** Whoever controls the agent's container or its
  image controls the host. Run only the published image, built by
  `fleet.yml` from `cloud/fleet/`, and keep `.env` at mode 600. The host
  token is the host's only credential. Whoever holds it receives this
  host's jobs, the client secrets in `create` payloads among them.
- **Outbound only.** The agent publishes no port. It long-polls
  `/api/fleet/jobs` and posts heartbeats and results; the account server
  never connects to the host. Those calls go through Cloudflare like any
  client's. A WAF or bot rule that challenges non-browser clients must
  skip `/api/fleet/` (the agent's user agent is `gamma-fleet/<version>`).
- **Origin lock-down on a routed host.** Its Caddy answers 80 and 443 to
  anyone who knows the address, and passes `CF-Connecting-IP` on as the
  client's address, which each hosted container believes. So the account
  server's rule ([cloud/deploy/README.md](../../deploy/README.md)
  "Cloudflare settings worth turning on") applies to every routed host as
  well: allow 80 and 443 from the ranges at
  <https://www.cloudflare.com/ips/> only, in the host's firewall (or the
  provider's). Note that Docker's published ports bypass a plain `ufw`
  rule; filter in the provider's firewall or in the `DOCKER-USER` chain.
- **The Cloudflare token** on the account server can edit every record of
  the zone. Make it for that one zone with *DNS → Edit* only, and keep it
  in the account server's `.env`, never on a fleet host.
- **The bucket key** reaches every hosted container's environment
  (`GAMMA_S3_*`), so give it rights on that one bucket only.
