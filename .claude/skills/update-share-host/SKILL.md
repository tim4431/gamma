---
name: update-share-host
description: Pull main's newest server image (:latest) for the Gamma Cloud share host (app.gammapdf.com, share.gammapdf.com, the page hosts) — the share service of the account project on the VPS — and restart that one service, at once rather than waiting up to an hour for the project's Watchtower. It runs main, like the demo; branch work reaches it through a merge.
---

# Updating the share host on the VPS

The share host is the `share` service of the account project on the VPS
`root@69.63.206.178`, folder `/root/Container/gamma-account/`: one Gamma in
cloud mode (`ghcr.io/tim4431/gamma`) behind the project's Caddy. It serves
`app.gammapdf.com` (where Gamma Cloud accounts and their Lite / Plus
libraries live), `share.gammapdf.com` (where Gamma servers publish pages) and
the page hosts `<username>-pages.gammapdf.com`
([cloud/deploy/README.md](../../../cloud/deploy/README.md) "The free share
host").

It runs `main`'s `:latest`, like the demo and the NAS, and keeps itself
current the same way: the project's `watchtower` service
(`gamma-share-watchtower`, scope `share`, touching only containers labelled
`com.centurylinklabs.watchtower.enable=true` with that scope) polls GHCR
every hour and recreates `share` when a merge's `docker.yml` run has
published a new `:latest`. This skill does that pull at once. It builds
nothing; branch work reaches the share host through the `merge` skill, run
only on the user's yes. Workflows:
[docs/dev/github_actions.md](../../../docs/dev/github_actions.md#dockeryml).

Rules:

- Touch only the `share` service and `compose.override.yml` (which exists
  only as a rollback pin, below). Never restart `account` or `caddy`, never
  `down` the project: they are the `update-account-server` skill's. The
  project's `compose.yml` (the `watchtower` service and the `share` labels
  included) and `Caddyfile` are that skill's too: if the host's copy lacks
  `gamma-share-watchtower`, say so and point to its deploy-file step.
- Never read out, copy off the host or overwrite `.env`, `share.env`,
  `data/` or `share-data/`.
- Never commit, never merge on your own, and never dispatch `docker.yml` from
  here.

In every command below, `<sha>` is the full `headSha` of the run being
deployed.

## 1. What will ship

`main`'s newest `docker.yml` run:

```bash
git fetch -q origin
gh run list --workflow docker.yml --branch main --limit 3 --json databaseId,headSha,status,conclusion,url
```

- `completed success` → its `headSha` is `<sha>`. It can be older than
  `origin/main` when the merges since touched only `cloud/` or `sites/`
  (`paths-ignore`: no run, nothing in the image changed).
- `queued` / `in_progress` → wait in the background with
  `gh run watch <run-id> --exit-status` (about 10 minutes), then use it.
- `completed failure` → report `gh run view <run-id> --log-failed` and stop:
  `:latest` did not move and nothing changes on the host.

## 2. What runs now

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-account && cat compose.override.yml 2>/dev/null; docker compose ps share watchtower; docker inspect --format '{{.Config.Image}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q share)"
```

This prints any rollback pin, the image and the running commit (keep it for
rollback). If the revision equals `<sha>` (Watchtower may have got there
first), there is nothing to deploy: say so and stop.

A `compose.override.yml` naming a `sha-` tag holds the share host on that
build, and Watchtower with it (it follows the tag the container runs). Show
it to the user: it was set on purpose (a rollback, or a data format held
back, step 3). Remove it only on their yes (`mv compose.override.yml
compose.override.yml.bak`); step 4 then brings `:latest`.

## 3. Does the data format move?

Every desktop app and Gamma server that publishes here syncs with this
server, and a sync pauses while the two keep a workspace's data in different
shapes ([docs/dev/mirror.md](../../../docs/dev/mirror.md) "The data format":
the newest migration step with a workspace part). Compare the running commit
with `<sha>`:

```bash
release=$(gh release view --json tagName -q .tagName); git fetch -q origin tag "$release"
for s in <running revision> <sha> "$release"; do
  echo "$s: $(git show $s:backend/gamma/migrations.py | sed -n '/^WORKSPACE_STEPS = \[/,/^\]/p' | grep -oE '^\s*\([0-9]+' | tr -d ' (' | tail -1)"
done
```

The last line is the desktop release people run. All the same → go on. A
higher number at `<sha>` than at the release means every publishing desktop
app pauses ("Paused · update Gamma") until a desktop release carries that
step. Watchtower deploys it within the hour anyway, so the `merge` skill is
where this is asked first; here, tell the user and suggest the `release`
skill. To hold the share host back until the release is out, pin the running
build (step "Rollback", with its `sha-` tag) on their word.

## 4. Pull and restart

```bash
ssh root@69.63.206.178 bash -s -- <sha> <<'EOF'
set -eu
cd /root/Container/gamma-account
[ ! -f compose.override.yml ] || { echo "a rollback pin is in place (step 2); nothing restarted"; exit 1; }
docker compose pull -q share
GOT=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' ghcr.io/tim4431/gamma:latest)
[ "$GOT" = "$1" ] || { echo ":latest is $GOT, not $1; nothing restarted"; exit 1; }
docker compose up -d --no-deps share
EOF
```

`up -d --no-deps share` recreates the share host alone: its visitors lose
their connection for the seconds of the restart. At start the image upgrades
`share-data/` itself (snapshot first into `share-data/backups/`). It refuses a
directory written by a newer build, so a `share` that keeps restarting needs
its log read before anything else.

## 5. Verify

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker compose ps share watchtower && docker compose logs --tail 20 share && docker compose logs --tail 5 watchtower"
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q share)"
curl -s -o /dev/null -w "%{http_code}\n" https://app.gammapdf.com/                    # 200
curl -s https://share.gammapdf.com/api/server-config | head -c 300                  # a JSON answer
```

- `share` is `Up … (healthy)`; the image's healthcheck needs up to about
  30 s after start. Its revision label equals `<sha>`. `watchtower` is `Up`
  and its log names its next scheduled run (a `client version … is too old`
  there means `DOCKER_API_VERSION` is missing from the host's `compose.yml`).
- A 502 means Caddy cannot reach `share`: read `docker compose logs share`
  first (a refused data directory, a failed migration).

Report the old → new commit (short sha + subject), the `docker.yml` run it
came from, and anything unusual in the log (a migration step, errors).

## Rollback

Every `main` build also stays in GHCR as `sha-<7 hex>`
(`gh run list --workflow docker.yml --branch main --status success --limit 10 --json headSha,createdAt,url`;
the tag is `sha-` plus the `headSha`'s first 7 characters, never
`git rev-parse --short`, which may print more). With the user's agreement,
pin one in `compose.override.yml`, a file holding only the share image,
which `docker compose` loads by itself, so the project's `compose.yml` and
secrets stay untouched:

```bash
ssh root@69.63.206.178 bash -s -- sha-<old> <<'EOF'
set -eu
TAG="$1"
echo "$TAG" | grep -Eq '^sha-[0-9a-f]{7}$' || { echo "not a sha-<7 hex> tag: $TAG"; exit 1; }
cd /root/Container/gamma-account
docker pull -q "ghcr.io/tim4431/gamma:$TAG"
printf '%s\n' "# A rollback pin for the share host (the update-share-host skill); remove it to follow :latest again." \
  "services:" "  share:" "    image: ghcr.io/tim4431/gamma:$TAG" > compose.override.yml
docker compose config share | grep -q "image: ghcr.io/tim4431/gamma:$TAG\$" || { rm compose.override.yml; echo "compose does not resolve the tag; pin removed"; exit 1; }
docker compose up -d --no-deps share
EOF
```

The pin holds until step 2 removes it. If the newer build already upgraded
`share-data/`, the older one refuses it and keeps restarting: restore the
snapshot the upgrade took in `share-data/backups/<time>-v<N>/`
([docs/dev/migrations.md](../../../docs/dev/migrations.md) "Running it"), only
with the user's agreement — it discards what was published since.
