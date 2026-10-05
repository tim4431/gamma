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
  compose.yml    the agent alone: the image, .env, the Docker socket, /srv/gamma, the gamma-fleet network
  .env.example   → .env: the account server's address, this host's token, container defaults,
                 the off-site bucket
```

It is a compose project of its own, apart from the account server's
([cloud/deploy/](../../deploy/README.md)). Updating, restarting or moving
the agent never touches the portal, the share host or the demo, and
their updates never touch the agent. The two projects share only the
external network `gamma-fleet`. Caddy joins it on the account side to
route `<label>.gammapdf.com` to `gamma-<label>:9001`, and the agent
starts every hosted container on it.

## Where it runs

`root@69.63.206.178`, folder `/root/Container/gamma-fleet/`, next to the
account server's `/root/Container/gamma-account/` and the demo's
`/root/Container/gamma-demo/`. The `update-fleet` skill
(`.claude/skills/`) updates it.

**One host for now.** Caddy finds a hosted server by its container name
on `gamma-fleet`, and a Docker network's names resolve only on its own
host. So until each server gets a DNS record of its own (or an edge
proxy routes to the right host), the agent that runs the servers must
run on the same host as the account project's Caddy. A second host can be
added and reports its heartbeat. A server placed there is unreachable,
though, so keep that host closed for placement (Admin → Servers →
*Close*).

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

Docker installed, then the same steps as on the VPS: the network with
the same pinned subnet, `mkdir -p /srv/gamma`, a new host on the Servers
tab (each host has its own name and token), the folder, `docker compose
up -d`. `.env` keeps `GAMMA_FLEET_ACCOUNT_URL=https://account.gammapdf.com`
and the same bucket as the other hosts. The agent only calls out, so the
host needs no open port and no DNS record for the agent. Close the host
for placement until the single-host caveat above is solved.

## Updating

```bash
cd ~/Container/gamma-fleet && docker compose pull && docker compose up -d
```

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
- **The bucket key** reaches every hosted container's environment
  (`GAMMA_S3_*`), so give it rights on that one bucket only.
