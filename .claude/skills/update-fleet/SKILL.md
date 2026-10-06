---
name: update-fleet
description: Publish the fleet agent image (ghcr.io/tim4431/gamma-fleet) from the current branch by dispatching fleet.yml, then pull it on the VPS (/root/Container/gamma-fleet) and restart the agent. Hosted servers keep running; no merge to main is needed.
---

# Updating the fleet agent on the VPS

The fleet agent is one container per host of hosted Gamma servers
(`cloud/fleet/`, [docs/dev/hosted.md](../../../docs/dev/hosted.md) "The
agent"). On the VPS `root@69.63.206.178` it is its own compose project in
`/root/Container/gamma-fleet/` (service `fleet`), apart from the account
server's project next to it; the two share only the external network
`gamma-fleet`. Deployment details:
[cloud/fleet/deploy/README.md](../../../cloud/fleet/deploy/README.md).

The folder's `.env` holds the host token and the off-site bucket key:
never read it out, copy it off the host, or overwrite it. `/srv/gamma/` on
the host is the customers' data; never touch it from here.

## Publish from the branch

`.github/workflows/fleet.yml` runs the agent's tests and publishes
`ghcr.io/tim4431/gamma-fleet:latest` + `:sha-<short>` on a push to `main`
that touches `cloud/fleet/`, or when dispatched from ANY branch. What
ships is what is COMMITTED and pushed on the branch; never commit here.

```bash
git status --short cloud/fleet/
git fetch -q origin && git log --oneline origin/<branch>..<branch>   # local commits → push first
gh workflow run fleet.yml --ref <branch>
gh run list --workflow fleet.yml --branch <branch> --event workflow_dispatch --limit 1 --json databaseId,headSha,status,url
gh run watch <run-id> --exit-status
```

A red `test` or `publish` job → report `gh run view <run-id> --log-failed`
and stop; `:latest` is unchanged. Note the run's `headSha`.

Only to redeploy an image already published: skip the dispatch and take
the newest successful run's `headSha` (`gh run list --workflow fleet.yml --limit 5`).

What is running now:

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-fleet && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q fleet)"
```

Equal to the run's `headSha` → nothing to deploy; say so and stop.

## Deploy files changed?

The host keeps its own `compose.yml`. If `cloud/fleet/deploy/compose.yml`
changed since the last deploy, compare and copy only once the user agrees:

```bash
ssh root@69.63.206.178 "cat /root/Container/gamma-fleet/compose.yml" | diff - <(git show <headSha>:cloud/fleet/deploy/compose.yml)
```

New variables in `cloud/fleet/deploy/.env.example` are named to the user to
add by hand.

## Update

The agent finishes the job it holds before it exits (the compose file
allows five minutes), and the hosted containers are not compose services,
so they keep running through the restart:

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-fleet && docker compose pull && docker compose up -d"
```

## Verify

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-fleet && docker compose ps && docker compose logs --tail 20 fleet"
ssh root@69.63.206.178 "cd /root/Container/gamma-fleet && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q fleet)"
```

- The log starts with `gamma-fleet <version>: https://account.gammapdf.com, network gamma-fleet, data /srv/gamma`
  and shows no `heartbeat failed` or `job poll failed` after it.
- The revision label equals the run's `headSha`.
- The Admin page's Machines tab shows the host seen *just now* with its
  memory and disk (the first heartbeat goes out at start) and the new
  agent version.

Report the old → new commit and anything unusual from the log.

## Rollback

Pin the previous image by its sha tag in the host's `compose.yml`
(`image: ghcr.io/tim4431/gamma-fleet:sha-<old>`) and `docker compose up -d`.
A job the restart cut off stays `running` until the account server fails
it after an hour; retry it from the Jobs table.
