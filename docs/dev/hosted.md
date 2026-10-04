# Hosted servers and the fleet

A Plus or Pro account on Gamma Cloud gets a Gamma container of its own at
`<username>.<GAMMA_CLOUD_HOSTED_DOMAIN>`. The account server
([cloud_accounts.md](cloud_accounts.md)) decides which containers should
exist and what limits each one runs under. A small agent on every host
does the Docker work. The container learns its limits by calling the
account server. The reasons behind this shape are in
[research/cloud-plans.md](../research/cloud-plans.md); billing, which
moves the plan, is in [billing.md](billing.md).

Code:

- `cloud/gammacloud/hosted.py`: the server rows, the lifecycle
  (`plan_changed`, `tick`), the limits answer, admin actions;
- `cloud/gammacloud/fleet.py`: hosts, placement, the job queue, upgrade
  waves;
- `cloud/gammacloud/routers/hosted.py` (`/api/hosted/*`),
  `routers/fleet.py` (`/api/fleet/*`), and the block at the end of
  `routers/admin.py`;
- `cloud/gammacloud/pages_fleet.py`: the Admin page's Servers tab;
- `cloud/fleet/`: the agent, package `gammafleet`, which imports nothing
  from `gammacloud`.

The Gamma side of the sync (`backend/gamma/hosted.py`: the cached limits,
the `invited` policy, the read-only gate) is in
[cloud_accounts.md](cloud_accounts.md) "Hosted containers".

Hosting is off while `GAMMA_CLOUD_HOSTED_DOMAIN` is empty: no server row
is created, whatever the plan. Rows that already exist keep following the
lifecycle. A new server runs `GAMMA_CLOUD_FLEET_IMAGE`
(`ghcr.io/tim4431/gamma`) at `GAMMA_CLOUD_FLEET_IMAGE_TAG` (`latest`),
kept as its `image_tag`; an upgrade run moves that tag.

## Tables

All three were added in schema step 9 (`db.py`).

| table | what |
|---|---|
| `hosts` | a machine an agent runs on: `name`, `address` (a note), `token_hash` (the agent's bearer token, hashed), capacity and use from the last heartbeat (`memory_mb`, `memory_used_mb`, `disk_mb`, `disk_used_mb`), `accepting` (open for new servers), `agent_version`, `last_seen_at` |
| `hosted_servers` | one per account (`account_id` unique): `label` (the hostname label, the username at creation, unique), `host_id`, `client_id` (its OIDC client, kind `container`), `image_tag` (the tag it runs), `state`, `read_only`, `limits` (the last sync answer, JSON), `report` (what the container last reported, plus the agent's `agent` part), `reported_at`, `synced_at`, `state_changed_at`, `created_at`, `deleted_at` |
| `fleet_jobs` | the queue: `host_id`, `server_id`, `kind`, `payload` (JSON), `state`, `attempts`, `result` (JSON text, 4000 chars at most), `wave` (`<run>/<nnn>` for an upgrade run), `created_at`, `started_at`, `finished_at` |

A job is `queued` (the agent may take it), `held` (a later wave of an
upgrade run), `running` (handed to the agent), `done`, `failed` or
`canceled`. A `create` job's payload holds the container's client secret.
It is blanked to `{}` when the job finishes or is canceled, and the
admin API never shows a payload's `env`.

## Server states

| state | container | `read_only` | sync `status` |
|---|---|---|---|
| `provisioning` | not yet: no host had room, or the `create` job has not finished | no | `active` (or `grace`) |
| `running` | up | no | `active` |
| `grace` | up, payment failed, writable until `grace_until` | no | `grace` |
| `read_only` | up, writes refused, reads and exports work | yes | `read_only` |
| `stopped` | stopped | yes | `stopped` |
| `suspended` | an admin's hold: up, read-only, left alone by the lifecycle | yes | `read_only` |
| `deleted` | container, data directory and bucket prefix removed; the row stays with `deleted_at` | yes | `stopped` |

Every change of state writes `state_changed_at` and an audit row
`hosted.state` (`<label> <old> -> <new> (<why>)`).

## What moves a server

**`plan_changed(conn, account_id)`.** Billing calls it inside its
transaction after every plan or subscription write, and so does the
admin's plan change (`accounts.recompute_plan`). It reads `accounts.plan`
(the effective plan), `accounts.granted_plan` and the account's
`subscriptions` row, and works out a target with `hosted.target`:

- the effective plan is not hosted (or the account is deleted): `read_only`;
- a hosted `granted_plan` (a courtesy grant), or a hosted plan with no
  subscription: `active`, and it never lapses;
- subscription `past_due`: `grace` until `past_due_since + GRACE_DAYS`,
  then `read_only`;
- `canceled`, `unpaid`, `incomplete_expired`, or `ended_at` set:
  `read_only`;
- anything else with a hosted plan: `active`.

With no row (or a `deleted` one) and an active hosted target, it creates
the server (`create`): state `provisioning`, placement, and a `create`
job. A deleted row is reused and starts fresh, with no data. Two rules
come first:

- **A confirmed address.** An account whose e-mail is not confirmed
  (`email_verified_at` NULL, e.g. an invite that granted Pro at
  registration) gets no row; the verify path calls `plan_changed` again.
  The admin's *Provision* answers 409 for it.
- **A label of its own.** The label is the username unless another
  account's row holds it, in any state (a deleted row's `delete` job may
  still be pending). Then it is the username plus `-` and four hex
  characters of the account id's SHA-256 (more while that is taken too).
  Another row is never relabelled: its container, data directory and
  address are named after its own label. The `create` payload's `label`
  and `data_dir` are always the new row's. A rename is refused while a
  server exists ([billing.md](billing.md)).

With a row, the target moves it:

| target | from | to |
|---|---|---|
| `active` | `grace`, `read_only` | `running` |
| `active` | `stopped` | `running`, plus a `start` job |
| `grace` | `running`, `read_only`, `stopped` | `grace` (`stopped` also gets a `start` job) |
| `read_only` | `provisioning`, `running`, `grace` | `read_only`, and the "read-only" mail |

A server whose container was never made (its last `create` job is not
`done`) returns to `provisioning` rather than `running`. `suspended` and
`deleted` rows are not moved. Every pass recomputes `limits`, so a plan
change between Plus and Pro reaches the container at its next hourly
sync. No `sync` job exists: the container syncs at startup and every hour.

**`tick(conn)`**, hourly from `app.purge`, which commits:

1. the stale-host alarm (`fleet.stale_hosts`): a host silent for 15
   minutes (`fleet.STALE_AFTER`) logs a warning on every tick and gets one
   audit row `fleet.host_stale` per silence. Nothing is changed:
   placement already skips a host by its `last_seen_at`, its first
   heartbeat back makes it a candidate again, and `accepting` stays the
   admin's choice;
2. a job `running` for over an hour (`fleet.JOB_TIMEOUT`) is failed;
3. each server that is not deleted, in its own savepoint so one failure
   does not stop the rest:
   - a `provisioning` server with no host is placed again;
   - the target above is applied (this is where a grace period ends);
   - `read_only` for `READ_ONLY_DAYS` (30) → `stopped`, with a `stop` job
     and the "stopped" mail;
   - `stopped` for `DELETE_DAYS - READ_ONLY_DAYS` (60) → `deleted`: a
     `delete` job, the OIDC client removed, queued jobs canceled. A week
     before, the "will be deleted in a week" mail goes out once (audit
     `hosted.delete_warning`);
4. the next upgrade waves are released.

So a cancelled subscription is read-only on day 0, stopped on day 30 and
deleted on day 90. Mail goes through `mail.py` to the account's address:
"Your Gamma is ready" when a `create` job is done, "read-only", "stopped",
and "will be deleted in a week". SMTP is sent on a thread, since
`plan_changed` runs inside the caller's write transaction.

**A deleted account.** `plan_changed` reads a soft-deleted account as a
lapse, so its server turns read-only and follows the lifecycle, and a
restore within the grace period resumes it. Before an account row is
purged, `hosted.purge_account` deletes a live server now and removes the
row, which references the account.

**Placement** (`fleet.place`): among hosts that are accepting, were seen
within 15 minutes and have more free disk (`disk_mb - disk_used_mb`) than
the plan's `quota_mb`, the one with the most free memory. With none the
row stays `provisioning` with `report.note` = "waiting for a host with
room", and the tick tries again.

## The sync

`POST /api/hosted/sync`, HTTP Basic `client_id:client_secret` of the
container's own OIDC client. The secret is checked against the client's
hash like the token endpoint does. The path is excluded from the
same-origin check.

Body: `{"version", "schema", "accounts", "uploads_bytes", "data_bytes",
"public_url"}`. It is stored as `report` (the agent's `agent` part is
kept), with `reported_at` and `synced_at`. The answer is the server's
limits, computed fresh and stored as `limits`:

```json
{"plan": "plus", "status": "grace", "read_only": false, "policy": "refuse",
 "max_accounts": 1, "quota_mb": 6144, "max_upload_mb": 100,
 "offsite": {"interval_s": 86400, "keep": 7},
 "grace_until": "2026-10-10T09:00:00.000Z",
 "message": "Payment failed; this server becomes read-only on 10 Oct unless the card is fixed at account.gammapdf.com/plan."}
```

The plan's numbers come from `config.PLAN_LIMITS`. `status` follows the
state table above. `grace_until` is set only in grace. `message` is one
sentence the container shows: the grace deadline, the read-only stop
date, the deletion date, or the suspension, and empty when all is well.
Errors: 401 for missing or bad credentials (the desktop client has no
secret and is refused too), 404 for a client that names no live server,
429 past 60 syncs an hour per client.

`GET /api/hosted/status` (portal session only) is what the plan page
polls: `{"server": null}` or `{"server": {id, label, url, state,
read_only, limits, report, reported_at, synced_at, host}}`
(`hosted.status_for`).

**In the account's server list.** `servers.of_account`, which feeds
`/api/me` (the desktop launcher), the Overview and the Devices page, puts
the account's hosted server first while it is not deleted. The row has the
same shape as a linked server's, with `kind: "hosted"`, `hosted: true`,
`name` "Your hosted Gamma", the server's `state`, `version` and `schema`
from its last sync, and `last_seen_at` from `synced_at`. When the
container also registered itself (`POST /api/me/servers`, after the owner
signed in there), the linked row at the same address merges into it and
lends it its grant. The entry comes from `hosted_servers`, so an unlink or
a Remove on the portal never takes it off the list.

## The fleet API

The agent's calls, `Authorization: Bearer <host token>`, 401 otherwise:

- `GET /api/fleet/jobs?wait=25` hands out the host's oldest `queued` job,
  marked `running` with `attempts` + 1, as `{"job": {id, kind, server_id,
  payload}}`. With none it holds the request up to `wait` seconds (at
  most 30) and then answers `{"job": null}`. It is a plain `def`, so it
  waits on a worker thread, on a `threading.Condition` that an enqueue
  notifies. It also re-reads the database every second, since an enqueue
  is visible only once its transaction commits. Each round is a plain
  read; the write lock is taken (and the row read again) only when a job
  is there, so an idle poll never holds it.
- `POST /api/fleet/jobs/{id}` `{"state": "done"|"failed", "result": {...}}`
  for a job of this host that is `running` (404 for another host's job,
  409 for one not running). A done `create` moves the server to
  `running` and sends the ready mail. A failed one leaves it
  `provisioning` with the error in `report.note`. A done `upgrade` sets
  `image_tag`. Then the waves are checked.
- `POST /api/fleet/heartbeat` `{agent_version, memory_mb, disk_mb,
  memory_used_mb, disk_used_mb, containers: [{label, running, health,
  memory_mb, data_mb, image}]}` updates the host (`last_seen_at` too) and,
  for each container of a server on this host, the server's
  `report.agent` (with its own `last_seen_at`).

Job payloads:

| kind | payload |
|---|---|
| `create` | `{label, account_id, plan, image, env, data_dir, memory_mb, cpus, network, public_url}`. `env`: `GAMMA_HOSTED=1`, `GAMMA_CLOUD_ISSUER`, `GAMMA_CLOUD_CLIENT_ID`, `GAMMA_CLOUD_CLIENT_SECRET`, `GAMMA_CLOUD_POLICY` (the plan's), `GAMMA_CLOUD_ADMIN_SUBJECT` (the account id), `GAMMA_PUBLIC_URL`. `memory_mb`, `cpus` and `network` are null, which means the agent's defaults |
| `start`, `stop`, `restart` | `{label}` |
| `delete` | `{label, account_id}` |
| `upgrade` | `{label, image, tag}` |

The `create` job's client is made on first use (kind `container`,
redirect `https://<label>.<domain>/api/auth/cloud/callback`, owner = the
account). Every rebuild of the payload (a retry) rotates its secret, so
the payload is the only place a secret ever exists in clear.
`FORWARDED_ALLOW_IPS` is not in the payload: the agent fills it from the
fleet network's subnet.

**Upgrade waves.** `fleet.upgrade(tag, wave_size, server_ids?)` takes the
servers with a host in `running`, `grace`, `read_only` or `suspended`
(only `server_ids` when given; servers that already have an upgrade
pending are skipped) and enqueues one `upgrade` job each to
`FLEET_IMAGE:<tag>`, in waves of `wave_size` under a run id: wave 1
`queued`, the rest `held`. `release_waves` queues a run's next wave only
when every job of the earlier waves is `done` or `canceled`. A `failed`
job therefore pauses the run until an admin retries it (the same job id
is queued again, so its wave can finish) or cancels it.

## The agent

`cloud/fleet/gammafleet/agent.py`, run as `python -m gammafleet.agent`
in its own image (`cloud/fleet/Dockerfile`: `docker` and `boto3`). The
module docstring lists the environment: `GAMMA_FLEET_ACCOUNT_URL`,
`GAMMA_FLEET_HOST_TOKEN`, `GAMMA_FLEET_NETWORK` (`gamma-fleet`),
`GAMMA_FLEET_DATA_ROOT` (`/srv/gamma`), `GAMMA_FLEET_MEMORY_MB` (768),
`GAMMA_FLEET_CPUS` (1), and the optional bucket `GAMMA_FLEET_S3_*`.

The loop: a heartbeat when due (every five minutes, a minute after a
failed one), one long poll, the job it got. A poll that comes back empty
within two seconds (a server that does not long-poll) or fails is
followed by a 10-second sleep. A result that cannot be delivered is
retried five times. Every failure becomes a `failed` result with the
error text and is never raised.

A server's container is `gamma-<label>`, labelled `gamma.label`,
`gamma.account` and `gamma.plan`, restart `unless-stopped`, with
`<root>/<label>/data` on `/data`, `mem_limit` and `nano_cpus`, on the
fleet network. What it was created with (image, environment, limits,
network) is kept in `<root>/<label>/container.json` (mode 600), so
`start` can rebuild a missing container and `upgrade` reuses the
configuration. With a bucket configured every container also gets
`GAMMA_S3_BUCKET`, `_ENDPOINT`, `_REGION`, `_ACCESS_KEY`, `_SECRET_KEY` and
`GAMMA_S3_PREFIX=<prefix><account id>/`.

- `create`: write `container.json`, pull, replace any container left by an
  earlier attempt (the data directory stays), run, wait for health;
- `start`: start it, or run it from `container.json` when it is gone;
  `stop`; `restart`. `start` and `restart` wait for health;
- `delete`: remove the container and any `-prev` one, the server's
  directory, and the bucket prefix;
- `upgrade`: pull, rename the running container to `gamma-<label>-prev`
  and stop it, run the new image with the same configuration, wait for
  health, then remove the previous one and save the new image in
  `container.json`.

A failed upgrade stops the new container (its logs stay) and keeps the
previous one stopped, not removed; the error says so. Gamma may already
have migrated the data directory, so starting the old image again is the
operator's call (Gamma's own pre-migration copy is in the data directory,
[migrations.md](migrations.md)). A retry while a `-prev` exists keeps
that one as the fallback and replaces only the failed container under the
live name. The last known-good container is removed only after a new one
passed its health check.

Health is `GET http://gamma-<label>:9001/api/health` answering 200 within
120 seconds, polled every 2 seconds. The agent joins the fleet network so
the name resolves. The heartbeat reads memory from `/proc/meminfo` and
disk from the data root's file system, and for each `gamma-<label>`
container its status, Docker health, memory use and data-directory size.

## Admin

The Admin page's **Servers** tab (`pages_fleet.py`):

- hosts: name, id, address, agent version, last heartbeat (*stale* past
  15 minutes), memory and disk used, server count, and *Open* / *Close*
  for placement. *Add host* shows the agent token once;
- hosted servers: the label linked to its address, the host, the account,
  the plan, state and read-only pills, *up* / *down* from the agent,
  version, data size, last sync, any note or failed jobs, and the
  actions. *Provision* takes an account id;
- *Upgrade*: an image tag and a wave size, for every running server;
- the last 100 jobs, with *Retry* (failed or canceled) and *Cancel*
  (queued, held or failed).

The API behind it (`/api/admin`, admins through a portal session only):
`GET`/`POST /hosts` (the token is in the create answer only),
`PATCH /hosts/{id}` (`name`, `accepting`), `GET /servers`,
`POST /servers/provision` `{account_id}` (the account's effective plan
must be hosted; 409 when it has a server), `POST /servers/upgrade`
`{tag, wave_size, server_ids?}`, `POST /servers/{id}/{action}`,
`GET /jobs?state=&limit=`, `POST /jobs/{id}/retry|cancel`.

The server actions:

- `restart`, `stop`, `start` enqueue that job and leave the state as it
  is: an admin's stop is not a lapse, and the tick does not undo it;
- `suspend` sets `suspended` (read-only; the lifecycle leaves it alone);
- `resume` recomputes the state from the account;
- `delete` ends it now. An account still on a hosted plan gets a fresh
  server at its next plan change.

`manage.py`: `hosts`, `add-host <name> [--address A]` (prints
`GAMMA_FLEET_HOST_TOKEN=…` once), `servers`, `provision <username>`,
`jobs [--state S]`.

## Deployment

[cloud/deploy/README.md](../../cloud/deploy/README.md) "Hosted servers"
has the steps. In short:

- the external network `gamma-fleet`, created once per host with the
  pinned subnet `10.203.0.0/24`. Caddy and the agent join it, and every
  container trusts it for `X-Forwarded-For`;
- `GAMMA_CLOUD_HOSTED_DOMAIN` set to the zone of the Caddyfile's
  wildcard site;
- the Caddyfile routes every `<label>.gammapdf.com` that is not `share`,
  `demo` or a `-pages` host to
  `gamma-{http.request.host.labels.2}:9001`, passing Cloudflare's
  `CF-Connecting-IP` as `X-Forwarded-For`. A stopped server is a 502 for
  its name;
- the agent is the `fleet` service of `compose.yml` behind the `fleet`
  profile, with the Docker socket and `/srv/gamma` mounted at the same
  path. Its host token comes from `.env`;
- a host is added on the Servers tab or with `manage.py add-host`. It
  takes servers after its first heartbeat.

Routing to a second host needs a DNS record per server (or an edge proxy)
and is not built. Until it is, run one host or close the others for
placement.

## Tests

- `cloud/tests/test_hosted.py`: the sync and its errors, `plan_changed`
  through every target, labels, the tick's lifecycle with its mails,
  `/api/hosted/status` and the hosted row in `/api/me`. Its helpers
  (`make_account`, `make_host`, `hosting`, …) are imported by
  `test_fleet.py`.
- `cloud/tests/test_fleet.py`: host tokens, the job queue and its long
  poll, the heartbeat, placement and the stale alarm, upgrade waves, a
  stuck job, the admin endpoints and the CLI.
- `cloud/fleet/tests/test_agent.py`, run from `cloud/fleet` with
  `python -m pytest -q`: the agent against a fake Docker client and a
  fake account server, every job kind with its failures, the heartbeat,
  the loop, the settings and the HTTP client.

Time is moved by backdating rows (`past_due_since`, `state_changed_at`,
`last_seen_at`, `started_at`), like the other account-server tests.
