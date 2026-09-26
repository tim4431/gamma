---
name: update-demo-server
description: Build the Gamma server image from a branch by dispatching docker.yml (only :sha-<short>, never :latest), then pin that tag in the demo's own compose project on the VPS (/root/Container/gamma-demo, demo.gammapdf.com) and restart it. No merge to main is needed.
---

# Updating the public demo on the VPS

`demo.gammapdf.com` is a Gamma in demo mode ([docs/dev/guests.md](../../../docs/dev/guests.md)
"Demo mode"). It is its OWN compose project on the VPS `root@69.63.206.178`,
folder `/root/Container/gamma-demo/`: `compose.yml`, `.env` (one line,
`GAMMA_TAG=sha-<short>`, the image to run), `demo.env` (its settings) and
`data/` (its whole state). It runs `ghcr.io/tim4431/gamma:${GAMMA_TAG}` and
answers as `gamma-demo` on the external Docker network `gamma-edge`, where the
account project's Caddy (`/root/Container/gamma-account/`) routes the name to
it. The repository's copies are in `cloud/deploy/demo/`; setup and the layout:
[cloud/deploy/demo/README.md](../../../cloud/deploy/demo/README.md).

`.github/workflows/docker.yml` dispatched on a branch without a version pushes
exactly the `sha-<short>` tag and never moves `:latest`, which the NAS pulls.
This skill builds through it and pins the result by writing the demo's `.env`.
Workflows: [docs/dev/github_actions.md](../../../docs/dev/github_actions.md#dockeryml).

Rules:

- Work only in `/root/Container/gamma-demo/`. Never run a compose command in
  `gamma-account/` from this skill, never restart `account`, `share` or
  `caddy`: they belong to the `update-account-server` skill. The demo's route
  (the Caddyfile's `@demo` handle, Caddy on `gamma-edge`) lives there too; if
  it is missing, say so and point to that skill.
- Never read out, copy off the host or overwrite `demo.env` or `data/`.
- Never commit. Never pass `-f version` to `docker.yml`: a version adds the
  release tags, and with them `:latest`.

In every command below, `<sha>` is the full `headSha` and `<tag>` is `sha-`
plus its first 7 characters (`echo sha-${sha:0:7}`). The metadata action's
`type=sha` truncates to 7; `git rev-parse --short` may print more and must not
be used for the tag.

## 1. What will ship

Branch: the one given as an argument, else `git branch --show-current`
(normally `dev`). The image is built from what is PUSHED on that branch:

```bash
git status --short backend/ frontend/ Dockerfile docker-entrypoint.sh
git fetch -q origin
git log --oneline origin/<branch>..<branch>
```

- Uncommitted changes under `backend/`, `frontend/` or the image files will
  NOT be in the image. Tell the user and ask whether to go on. Never commit
  them yourself.
- Local commits not on the remote: `git push origin <branch>`.

## 2. Build (or reuse a build)

A commit that already has a green `docker.yml` run has its tag in GHCR (every
run, including a push to `main`, pushes `sha-<short>`). Look before building:

```bash
git rev-parse origin/<branch>
gh run list --workflow docker.yml --commit <that sha> --status success --limit 1 --json databaseId,headSha,event,url
```

If a run is listed, skip the build and use its `headSha`. To redeploy an older
published commit without building (the user names it, or pick one from
`gh run list --workflow docker.yml --status success --limit 10 --json headSha,headBranch,event,createdAt`),
use that `headSha` and go to step 3.

Otherwise dispatch and find the run:

```bash
gh workflow run docker.yml --ref <branch>
gh run list --workflow docker.yml --branch <branch> --event workflow_dispatch --limit 1 --json databaseId,headSha,status,url
```

The run may take a few seconds to appear; list it again rather than guess. Its
`headSha` must equal `git rev-parse origin/<branch>`. Then wait (run it in the
background; a multi-arch build under QEMU takes about 10 minutes, longer
without a warm cache):

```bash
gh run watch <run-id> --exit-status
```

Red → report `gh run view <run-id> --log-failed` and stop: no tag was pushed
and nothing changes on the host.

## 3. What runs now

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-demo && cat .env && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q demo)"
```

This prints the pinned tag (keep it for rollback) and the running commit. If
the revision equals `<sha>`, the image needs no deploy: say so, do step 4 only
if `cloud/deploy/demo/` changed since that commit, and otherwise stop.

No `/root/Container/gamma-demo/` at all means a first deployment: follow
[cloud/deploy/demo/README.md](../../../cloud/deploy/demo/README.md) "First
deployment" with the user.

## 4. Deploy files changed?

Compare the host's `compose.yml` with the commit being deployed:

```bash
ssh root@69.63.206.178 "cat /root/Container/gamma-demo/compose.yml" | diff --strip-trailing-cr - <(git show <sha>:cloud/deploy/demo/compose.yml)
```

The file carries no pin, so any difference is a real change. Show it to the
user and copy it only once they agree (a backup stays next to it):

```bash
git show <sha>:cloud/deploy/demo/compose.yml | ssh root@69.63.206.178 "cd /root/Container/gamma-demo && cp compose.yml compose.yml.bak && cat > compose.yml && docker compose config -q"
```

`demo.env` is never copied or edited. List the variables the example gained or
lost since the running commit, and the names (names only, never values) the
host's file sets:

```bash
git diff <running revision>..<sha> -- cloud/deploy/demo/demo.env.example
ssh root@69.63.206.178 "grep -o '^[A-Z_]*=' /root/Container/gamma-demo/demo.env"
```

Name any new variable to the user to add by hand; step 5's `up -d` applies it.

## 5. Pin the tag and restart

One script on the host. It checks the tag format and that the image exists
before touching anything, writes `.env` (the old one kept as `.env.bak`),
checks the resolved image, then pulls and recreates the demo:

```bash
ssh root@69.63.206.178 bash -s -- <tag> <<'EOF'
set -eu
TAG="$1"
echo "$TAG" | grep -Eq '^sha-[0-9a-f]{7}$' || { echo "not a sha-<7 hex> tag: $TAG"; exit 1; }
cd /root/Container/gamma-demo
docker pull -q "ghcr.io/tim4431/gamma:$TAG"
cp .env .env.bak
printf 'GAMMA_TAG=%s\n' "$TAG" > .env
if ! docker compose config demo | grep -q "image: ghcr.io/tim4431/gamma:$TAG\$"; then
  cp .env.bak .env; echo "compose does not resolve the new tag; .env restored"; exit 1
fi
docker compose up -d
EOF
```

`up -d` recreates the demo alone: guests lose their connection for the seconds
of the restart and keep their workspaces. At start the image upgrades the data
directory itself (`manage.py migrate`, snapshot first into `data/backups/`). It
refuses a directory written by a newer build, so a `demo` that keeps
restarting needs its log read before anything else.

Offer to set the same tag in the repository's `cloud/deploy/demo/.env.example`
(a working-tree edit left for the user to commit, never committed here), so a
first deployment from the repository starts on a current build.

## 6. Verify

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-demo && docker compose ps && docker compose logs --tail 20 demo"
ssh root@69.63.206.178 "cd /root/Container/gamma-demo && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q demo)"
curl -s https://demo.gammapdf.com/api/server-config | grep -o '"demo": *true'   # "demo":true
curl -s -o /dev/null -w "%{http_code}\n" https://demo.gammapdf.com/            # 200
```

- `demo` is `Up … (healthy)`; the image's healthcheck needs up to about 30 s
  after start. Its revision label equals `<sha>`.
- `server-config` reports `"demo":true`. If it is missing, `GAMMA_DEMO=1` is
  not in `demo.env` (or the build predates demo mode).
- A 502 from the demo's name means Caddy cannot reach `gamma-demo`: check
  `docker network inspect gamma-edge` lists both the demo and Caddy. A 404
  means the account project's Caddyfile lacks the `@demo` handle. Both are
  the `update-account-server` skill's to fix; tell the user.
- On a first deployment the admin's one-time password is in the log. Give the
  user the command (`docker compose logs demo | grep -A2 "created the admin account"`)
  rather than its output. The shared AI connection, "Guests may use it" and
  the allowance are the admin's to set in the GUI (Settings → Server → Shared
  AI provider). AI keys never go into `demo.env`.

Report the old → new tag and commit (short sha + subject), the run link if one
was built, and anything unusual in the log (a migration step, errors).

## Resetting the demo (only when the user asks)

`data/` is the demo's whole state (guest accounts and workspaces, the admin
account, the shared AI connection, the settings) and it is disposable, but
wipe it only on the user's explicit request:

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-demo && docker compose down && rm -rf data && docker compose up -d"
```

The instance starts fresh: the admin is seeded again, with a new random
password in the log unless `demo.env` sets one, and the shared AI connection,
"Guests may use it" and the allowance must be entered again. A
`GAMMA_GUEST_SEED` zip kept in `data/` goes too. Move it aside first
(`mv data/guest-seed.zip .` before the `rm`, then
`mkdir -p data && mv guest-seed.zip data/` before the `up`) if one is in use.

## Rollback

Run step 5 again with the previous tag from step 3 (or `.env.bak`). If the
newer build already upgraded the data directory, the older one refuses it and
keeps restarting. Then either restore the snapshot the upgrade took in
`data/backups/<time>-v<N>/` ([docs/dev/migrations.md](../../../docs/dev/migrations.md)
"Running it"), or reset the demo as above. Both need the user's agreement.
