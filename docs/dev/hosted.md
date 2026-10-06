# Hosted servers and the fleet

A Pro account on Gamma Cloud gets a Gamma container of its own at
`<username>-user.<GAMMA_CLOUD_HOSTED_DOMAIN>`: the plans marked `hosted` in
`config.PLAN_LIMITS`. The suffix (`GAMMA_CLOUD_HOSTED_SUFFIX`, `-user`) is
part of every hosted server's name (`hosted.url_of`), so no username can
take a service's name in the zone (`account`, `app`, `share`, `demo`, the
`-pages` hosts). Its owner reaches it through the entrance
([cloud_accounts.md](cloud_accounts.md) "The entrance") and need not know
the address. Lite and Plus have none; their library is an account
on the shared server ([cloud_accounts.md](cloud_accounts.md) "Plans on the
share host"). The account server
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

All three were added in schema step 9 (`db.py`), `hosts.orphans` in step
10.

| table | what |
|---|---|
| `hosts` | a machine an agent runs on: `name`, `address` (a note), `token_hash` (the agent's bearer token, hashed), capacity and use from the last heartbeat (`memory_mb`, `memory_used_mb`, `disk_mb`, `disk_used_mb`), `accepting` (open for new servers), `agent_version`, `orphans` (JSON list: the labels of containers its agent reported that no server row on the host names), `last_seen_at` |
| `hosted_servers` | one per account (`account_id` unique): `label` (the hostname label, the username at creation, unique), `host_id`, `client_id` (its OIDC client, kind `container`), `image_tag` (the tag it runs), `state`, `read_only`, `limits` (the last sync answer, JSON, which holds the container's size too), `report` (what the container last reported, plus the agent's `agent` part), `reported_at`, `synced_at`, `state_changed_at`, `created_at`, `deleted_at` |
| `fleet_jobs` | the queue: `host_id`, `server_id` (empty for an orphan's removal), `kind`, `payload` (JSON), `state`, `attempts`, `result` (JSON text, 4000 chars at most; a `logs` job's 100,000, its oldest lines dropped first), `wave` (`<run>/<nnn>` for an upgrade run), `created_at`, `started_at`, `finished_at` |

A job is `queued` (the agent may take it), `held` (a later wave of an
upgrade run), `running` (handed to the agent), `done`, `failed` or
`canceled`. A `create` job's payload holds the container's client secret.
It is blanked to `{}` when the job finishes, times out or is canceled, and
the admin API never shows a payload's `env`.

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
| `read_only` | `provisioning`, `running`, `grace` | `read_only`, and the "read-only" mail, which says why (`hosted._read_only_mail`): the plan ended, or it is still paid and has no server of its own, so the library belongs on the shared server |

A server whose container was never made (its last `create` job is not
`done`) returns to `provisioning` rather than `running`. `suspended` and
`deleted` rows are not moved. Every pass recomputes `limits`, so a plan
change between Plus and Pro reaches the container at its next hourly
sync. No `sync` job exists: the container syncs at startup and every hour.

**Container size.** Each hosted plan in `config.PLAN_LIMITS` names a
`memory_mb` and `cpus`: Pro has 1536 MB and 2 CPUs. The `create` payload carries them and `limits` keeps them. A pass
whose new limits have another size than the stored ones resizes the
container (`hosted._resize`, through `_resize_if_moved`). It enqueues an
`upgrade` job with `{label, memory_mb, cpus}` and no image, which the
agent applies in place: the same container and image, no pull and no
restart, and a stopped container stays stopped. A resize still queued
takes a newer size instead of a second job. Before the container exists,
a queued `create` job takes the new size, and a `create` already running
is resized once it is done. A late result of a `create` whose payload was
blanked no longer says its size, so it is resized to be sure. A lapsed
server is never resized. It keeps the limits of the last hosted plan it
ran, or Pro's when that plan has no container any more
(`hosted._container_plan`): a move down from Pro, or a server made while
Lite and Plus had containers. Such a server turns read-only and follows
the lifecycle, and no new one is made for the account.
Rows stored before sizes existed have none in `limits`; they get them at
the next pass without a resize, and their containers keep the agent's
default size until the plan changes.

**`tick(conn)`**, hourly from `app.purge`, which commits:

1. the stale-host alarm (`fleet.stale_hosts`): a host silent for 15
   minutes (`fleet.STALE_AFTER`) logs a warning on every tick and gets one
   audit row `fleet.host_stale` per silence. Nothing is changed:
   placement already skips a host by its `last_seen_at`, its first
   heartbeat back makes it a candidate again, and `accepting` stays the
   admin's choice;
2. a job `running` for over an hour (`fleet.JOB_TIMEOUT`) is failed, with
   `timed_out` in its result, and a `create` job's payload is blanked. A
   result the agent sends later is still taken unless an admin retried or
   canceled the job meanwhile;
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

**Placement** (`fleet.place`) counts committed memory, not what a host
happens to use. A host's free memory is its `memory_mb` less the
`memory_mb` of every server on it that is not deleted and less 1024 MB it
keeps for itself (`fleet.HOST_RESERVE_MB`), floored at 0. A host qualifies
when it is accepting, was seen within 15 minutes, has more free disk
(`disk_mb - disk_used_mb`) than the plan's `quota_mb` and at least the
plan's `memory_mb` free. The one with the most free memory wins, the
oldest on a tie. With none the row stays `provisioning` with
`report.note` = "waiting for a host with room". Every heartbeat places
the waiting servers (`hosted.place_waiting`), so a new host's first report
takes them at once, and the hourly tick tries too. *Provision* on a
waiting server places it now instead of refusing it as one that exists.

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
 "memory_mb": 768, "cpus": 1.0,
 "offsite": {"interval_s": 86400, "keep": 7},
 "grace_until": "2026-10-10T09:00:00.000Z",
 "message": "Payment failed; this server becomes read-only on 10 Oct unless the card is fixed at account.gammapdf.com/plan."}
```

The plan's numbers come from `config.PLAN_LIMITS`. `memory_mb` and `cpus`
are the container's size, which Gamma ignores. `status` follows the
state table above. `grace_until` is set only in grace. `message` is one
sentence the container shows: the grace deadline, the read-only stop
date, the deletion date, or the suspension, and empty when all is well.
Errors: 401 for missing or bad credentials (the desktop client has no
secret and is refused too), 404 for a client that names no live server,
429 past 60 syncs an hour per client.

`GET /api/hosted/status` (portal session only) is what the plan page
polls: `{"server": null}` or `{"server": {id, label, url, state,
read_only, limits, report, reported_at, synced_at, stops_at, deletes_at,
host}}` (`hosted.status_for`). `stops_at` is set while the server is
read-only and `deletes_at` from then until it is deleted; both are the
dates the tick itself acts on, and the Plan page shows them.

**In the account's server list.** `servers.of_account`, which feeds
`/api/me` (meant for the desktop launcher, which does not read it yet),
the Overview and the Devices page, puts the account's hosted server first
while it is not deleted. The row has the
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
  409 for one not running). A job that timed out (tick step 2) still
  takes its late result, unless an admin retried or canceled it since. A
  done `create` moves the server to `running`, clears `report.note` and
  sends the ready mail; a failed one leaves it `provisioning` with the
  error in `report.note`. A done `upgrade` with a `tag` sets `image_tag`,
  and so does a done `rollback` whose reported image is
  `FLEET_IMAGE:<tag>`. A done `delete` with no server drops its label
  from the host's orphans. Then the waves are checked.
- `POST /api/fleet/heartbeat` `{agent_version, memory_mb, disk_mb,
  memory_used_mb, disk_used_mb, containers: [{label, running, health,
  memory_mb, memory_limit_mb, data_mb, image}]}` updates the host
  (`last_seen_at` too) and, for each container of a server on this host,
  the server's `report.agent` (with its own `last_seen_at`). A label that
  no server row on this host names, in any state, is an orphan. Each
  heartbeat replaces the host's `orphans`, and the admin's view leaves out
  a label a row has named since.

Job payloads and results:

| kind | payload | result when done |
|---|---|---|
| `create` | `{label, account_id, plan, image, env, data_dir, memory_mb, cpus, network, public_url}`. `env`: `GAMMA_HOSTED=1`, `GAMMA_CLOUD_ISSUER`, `GAMMA_CLOUD_CLIENT_ID`, `GAMMA_CLOUD_CLIENT_SECRET`, `GAMMA_CLOUD_POLICY` (the plan's), `GAMMA_CLOUD_ADMIN_SUBJECT` (the account id, which also stops the image seeding an `admin` of its own), `GAMMA_PUBLIC_URL`, `GAMMA_GUEST_MAX=0` (no guest logins: a guest would not count against the plan's accounts). `memory_mb` and `cpus` are the plan's; `network` is null, the agent's own | `{container, image, health}` |
| `start`, `stop`, `restart` | `{label}` | `{container, health}`; `stop`: `{container, stopped}`, or a `note` when there is no container |
| `delete` | `{label, account_id}`; an orphan's removal has `account_id` `""` and no server | `{removed, data, bucket_prefix, bucket_objects}` |
| `upgrade` | `{label, image, tag}`; a resize is `{label, memory_mb, cpus}` | `{container, image, previous, memory_mb, cpus, health}` |
| `rollback` | `{label}` | `{container, image, previous, health}` (or a `note`: already rolled back) |
| `logs` | `{label}` | `{container, lines, since}`: the last 200 lines, each with its Docker timestamp; `since` is the oldest one's, or `""` |

A failed job's result is `{"error": "..."}`.

The `create` job's client is made on first use (kind `container`,
redirect `https://<label>.<domain>/api/auth/cloud/callback`, owner = the
account). Every rebuild of the payload (a retry) rotates its secret, so
the payload is the only place a secret ever exists in clear.
`FORWARDED_ALLOW_IPS` is not in the payload: the agent fills it from the
fleet network's subnet.

**Upgrade waves.** `fleet.upgrade(tag, wave_size, server_ids?)` takes the
servers with a host in `running`, `grace`, `read_only` or `suspended`
(only `server_ids` when given; servers that already have an image upgrade
pending are skipped, but a pending resize does not count) and enqueues one
`upgrade` job each to `FLEET_IMAGE:<tag>`, in waves of `wave_size` under a
run id: wave 1 `queued`, the rest `held`. `release_waves` queues a run's
next wave only when every job of the earlier waves is `done` or
`canceled`. A `failed` job therefore pauses the run until an admin retries
it (the same job id is queued again, so its wave can finish) or cancels
it. An upgrade does not change the container's size and a resize never
touches its image, so the two give the same result in either order.

`fleet.upgrade_one(server_id, tag)` is one server's upgrade outside any
wave: 409 unless the server is in one of those states with a host and has
no image upgrade pending. Its `image_tag` moves when the job is done. A
server is `outdated` while its image (`FLEET_IMAGE:image_tag`) differs
from `fleet.default_image()` (`FLEET_IMAGE:FLEET_IMAGE_TAG`).

**Orphans.** `fleet.remove_orphan(host_id, label)` enqueues a `delete`
job with `{label, account_id: ""}` and no server. The container and its
data directory go; the bucket prefix stays, since it may belong to the
account's live server on another host. It answers 404 for a label that
is not an orphan on that host and 409 when a server row there has the
label. A removal already pending is returned again rather than doubled.
A server can take the label after the removal was queued, so the check is
made again when the job would be handed to the agent (`claim` cancels it,
naming the server) and when it is retried (409). Running it then would
delete that server's container and data.

**Retries and timeouts.** `fleet.retry` queues a `failed` or `canceled`
job again under its id. It refuses any job of a deleted (or purged) server
but its `delete` job, and a `create` job that a later one replaced (a
deleted row brought back has a new one). A retried `create` gets a new
payload and secret.

## The agent

`cloud/fleet/gammafleet/agent.py`, run as `python -m gammafleet.agent`
in its own image (`cloud/fleet/Dockerfile`: `docker` and `boto3`). The
module docstring lists the environment: `GAMMA_FLEET_ACCOUNT_URL`,
`GAMMA_FLEET_HOST_TOKEN`, `GAMMA_FLEET_NETWORK` (`gamma-fleet`),
`GAMMA_FLEET_DATA_ROOT` (`/srv/gamma`), `GAMMA_FLEET_MEMORY_MB` (768) and
`GAMMA_FLEET_CPUS` (1) for a job that names no size, and the optional
bucket `GAMMA_FLEET_S3_*`.

The loop: a heartbeat when due (every five minutes, a minute after a
failed one), one long poll, the job it got. A poll that comes back empty
within two seconds (a server that does not long-poll) or fails is
followed by a 10-second sleep. A result that cannot be delivered is
retried five times. Every failure becomes a `failed` result with the
error text and is never raised.

A server's container is `gamma-<label>`, labelled `gamma.label`,
`gamma.account` and `gamma.plan`, restart `unless-stopped`, with
`<root>/<label>/data` on `/data`, on the fleet network. Its limits are
`mem_limit`, `memswap_limit` (twice the memory, Docker's default for
`--memory`) and a CPU quota (`cpu_quota` per `cpu_period` of 100 ms), not
`nano_cpus`, because Docker's update changes only the quota. What it was created with (image, environment, limits,
network) is kept in `<root>/<label>/container.json` (mode 600), so
`start` and `restart` can rebuild a missing container and `upgrade`
reuses the configuration. With a bucket configured every container also gets
`GAMMA_S3_BUCKET`, `_ENDPOINT`, `_REGION`, `_ACCESS_KEY`, `_SECRET_KEY` and
`GAMMA_S3_PREFIX=<prefix><account id>/`.

- `create`: refuse a payload with no account id (the bucket prefix is the
  account's, and a later delete finds the copies by it), write
  `container.json`, pull, replace any container left by an earlier attempt
  and any `-prev` left by an earlier life of the label (the data directory
  stays), run, wait for health;
- `start`: start it unless it is running, or run it from `container.json`
  when it is gone; `restart`: restart it, or run it from `container.json`
  when it is gone. Both wait for health. `stop`: stop it (with no
  container the job is done, with a note);
- `delete`: remove the container and any `-prev` one, the server's
  directory, and, when the payload names the account, its bucket prefix.
  With nothing there it is done all the same;
- `upgrade`: pull, rename the running container to `gamma-<label>-prev`
  and stop it, run the new image with the same configuration (and the
  payload's `memory_mb` and `cpus` when it has them), wait for health,
  then remove the previous one and save the new image and size in
  `container.json`. A payload with no image is a resize: Docker's update
  sets the new limits on the container as it is, and `container.json`
  takes the size. With no container only `container.json` changes;
- `rollback`: with a `-prev`, stop and remove the live container, rename
  `-prev` back to the live name, start it, wait for health, and save its
  image in `container.json`, marked `rolled_back`. With no `-prev` it
  fails with "nothing to roll back to", unless the live container already
  runs the marked image, in which case it is started if needed and the job
  is done ("already rolled back"). The next upgrade clears the mark;
- `logs`: the live container's last 200 lines with timestamps
  (`logs(tail=200, timestamps=True)`, each line cut to 400 characters).

A failed upgrade stops the new container (its logs stay) and keeps the
previous one stopped, not removed; the error says so. Gamma may already
have migrated the data directory, so starting the old image again is the
operator's call, by `rollback` (Gamma's own pre-migration copy is in the
data directory, [migrations.md](migrations.md)). A retry while a `-prev`
exists keeps that one as the fallback and replaces only the failed
container under the live name. The last known-good container is removed
only after a new one passed its health check. A resize while such a
failure waits (a `-prev`, and a live container on another image than
`container.json`) is refused until the upgrade is retried or rolled back.

Every handler can run again, after it finished or after the agent died
midway, and end in the same place: a `create` replaces what an attempt
left; `start` and `stop` leave a container already in that state alone;
`delete` with nothing there is done; an `upgrade` that died after the
rename finds the `-prev` and goes on; a `rollback` that died after the
rename finds the marked image and only starts it.

Health is `GET http://gamma-<label>:9001/api/health` answering 200 within
120 seconds, polled every 2 seconds. The agent joins the fleet network so
the name resolves. The heartbeat reads memory from `/proc/meminfo` and
disk from the data root's file system, and for each `gamma-<label>`
container its status, Docker health, memory limit (`HostConfig.Memory`),
memory use and data-directory size. The memory use comes from `stats()`,
a second or two per running container. The data directory is walked at
most every 30 minutes per container (`DATA_EVERY`); a `create` or
`delete` measures it afresh.

## Admin

The Admin page's **Servers** tab (`pages_fleet.py`), top to bottom:

- **a summary strip** (the portal's shared `.tiles`, as on the Billing
  tab): hosts (fresh and stale), servers by state, jobs
  queued, running and failed, and the default image with the number of
  servers that are *outdated*;
- **hosts**: name, agent version, id and address; the last heartbeat,
  *stale* past 15 minutes; memory as committed of total, with what is
  free, the reserve and what is in use, over a thin meter (committed, then
  the reserve); disk used of total over another; the server count; *Open*
  / *Close* for placement. A host with orphans gets a row under it with
  each label and *Remove*. *Add host* shows the agent token once;
- **hosted servers**: the label linked to its address, its id and host;
  the account (username and id); the plan with its quota, memory and
  CPUs; pills for the lifecycle state, *read-only* where the state does not
  say it, and *up*, *down* or *unhealthy* from the agent, with any note and
  the jobs in flight or failed; the image tag, with *outdated* and an
  inline *Upgrade* to the default tag; data used of the quota over a
  meter, and its size on disk; the last sync and the agent's last report;
  the *Actions* menu. Deleted servers are hidden behind *show N deleted*.
  *Provision* has a username search (`GET /accounts?q=`, debounced) that
  fills the account id. It says so when the account already has a
  server, is on a plan without one, or has not confirmed its address;
- **logs**: *Logs* enqueues a `logs` job. The viewer under the servers
  shows a "fetching" strip and polls `GET /jobs/{id}` every 2 seconds (for
  three minutes at most), then the lines in a scrolling monospace block,
  with *Fetch again* and *Close*. A done `logs` job in the Jobs table has
  *View*;
- **upgrade**: an image tag and a wave size, for every running server;
- **jobs**: the last 100, filtered by state. Each row has its age, the
  kind (an upgrade's tag or *resize*; for a run, the job's wave and the
  run's servers done of total), server, host, state, duration and the
  first 160 characters of the result (all of it on hover), with *Retry*
  (failed or canceled) and *Cancel* (queued, held or failed).

The tab reloads every 4 seconds while a job is queued or running, and
only while it is the open tab of a visible page. Otherwise nothing polls.
A reload waits while an *Actions* menu has the focus.

The API behind it (`/api/admin`, admins through a portal session only):

- `GET /hosts`: each host with `servers`, `committed_mb`, `reserve_mb`,
  `free_mb` and `orphans`. `POST /hosts` `{name, address}` (the token is
  in this answer only), `PATCH /hosts/{id}` (`name`, `accepting`),
  `POST /hosts/{id}/orphans/{label}/remove` → `{job}`;
- `GET /servers` → `{servers, default_image}`, each server with
  `memory_mb`, `cpus`, `quota_mb`, `image`, `outdated`, its account, its
  host and its job counts by state. `POST /servers/provision`
  `{account_id}` (the account's effective plan must be hosted; 409 when
  it has a server). `POST /servers/upgrade` `{tag, wave_size,
  server_ids?}` → `{run, image, jobs, waves}`. `POST /servers/{id}/upgrade`
  `{tag}`, `/logs` and `/rollback` → `{job}`. `POST /servers/{id}/{action}`
  → `{server}`;
- `GET /jobs?state=&limit=`: each job with `duration_s`, and for one of
  an upgrade run `wave_total` and `wave_done` (the run's jobs, and how
  many are done). A `logs` job's lines are only counted there
  (`line_count`); `GET /jobs/{id}` has the whole result.
  `POST /jobs/{id}/retry|cancel`.

The server actions:

- `restart`, `stop`, `start` enqueue that job and leave the state as it
  is: an admin's stop is not a lapse, and the tick does not undo it;
- `suspend` sets `suspended` (read-only; the lifecycle leaves it alone);
- `resume` recomputes the state from the account;
- `delete` ends it now. An account still on a hosted plan gets a fresh
  server at its next plan change;
- `logs` is a job whose result holds the container's last 200 lines;
- `rollback` is a job back to the container a failed upgrade kept ("The
  agent");
- `upgrade` `{tag}` is this server's upgrade alone (`fleet.upgrade_one`).

`manage.py`: `hosts` (with committed and free memory and any orphans),
`add-host <name> [--address A]` (prints `GAMMA_FLEET_HOST_TOKEN=…` once),
`servers` (with size, tag and *outdated*), `provision <username>`,
`jobs [--state S]`.

## Deployment

Two compose projects share the work, joined by one external network:

- the account server's, [cloud/deploy/](../../cloud/deploy/README.md)
  ("Hosted servers" there): `GAMMA_CLOUD_HOSTED_DOMAIN` set to the zone
  of the Caddyfile's wildcard site, `GAMMA_CLOUD_FLEET_IMAGE` and
  `_IMAGE_TAG` for what a new server runs, and Caddy on `gamma-fleet`.
  The Caddyfile routes every `<label>-user.gammapdf.com` to
  `gamma-<label>:9001` (the `@hosted` rule, which must agree with
  `GAMMA_CLOUD_HOSTED_SUFFIX`), passing Cloudflare's `CF-Connecting-IP` as
  `X-Forwarded-For`. A stopped
  server is a 502 for its name;
- the agent's own, [cloud/fleet/deploy/](../../cloud/fleet/deploy/README.md)
  (`/root/Container/gamma-fleet/` on the VPS):
  `ghcr.io/tim4431/gamma-fleet:latest` with the Docker socket and
  `/srv/gamma` mounted at the same path, on `gamma-fleet`, with no port.
  Its `.env` holds the account server's public address
  (`GAMMA_FLEET_ACCOUNT_URL=https://account.gammapdf.com`), the host
  token and the bucket. The `update-fleet` skill updates it;
- the network `gamma-fleet`, created once per host with the pinned
  subnet `10.203.0.0/24`. Caddy, the agent and every hosted container
  join it, and each container trusts it for `X-Forwarded-For`.

A host is added on the Servers tab or with `manage.py add-host`. It takes
servers after its first heartbeat. The agent only calls out, so on a host
anywhere it needs the token and nothing else.

Routing to a second host needs a DNS record per server (or an edge proxy)
and is not built. Caddy reaches a container by its name on `gamma-fleet`,
which resolves only on Caddy's own host. Until then the agent that runs
the servers runs beside Caddy, and any other host stays closed for
placement.

## Tests

- `cloud/tests/test_hosted.py`: the sync and its errors, `plan_changed`
  through every target, labels, the tick's lifecycle with its mails,
  `/api/hosted/status` and the hosted row in `/api/me`. Its helpers
  (`make_account`, `make_host`, `hosting`, …) are imported by
  `test_fleet.py`.
- `cloud/tests/test_fleet.py`: host tokens, the job queue and its long
  poll, the heartbeat and orphans, placement by committed memory and the
  stale alarm, resizes, upgrade waves and one server's upgrade, logs and
  rollback jobs, stuck jobs with late results and retries, the job rows'
  durations and run progress, schema step 10, the admin endpoints and the
  CLI.
- `cloud/fleet/tests/test_agent.py`, run from `cloud/fleet` with
  `python -m pytest -q`: the agent against a fake Docker client and a
  fake account server, every job kind with its failures and with a second
  run, the heartbeat and its data-size cache, the loop, the settings and
  the HTTP client.

Time is moved by backdating rows (`past_due_since`, `state_changed_at`,
`last_seen_at`, `started_at`), like the other account-server tests.
