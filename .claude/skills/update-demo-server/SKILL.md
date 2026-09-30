---
name: update-demo-server
description: Pin the public demo (demo.gammapdf.com) to main's newest server image — the :sha-<short> tag a merge's docker.yml run pushed, not :latest — in its own compose project on the VPS (/root/Container/gamma-demo) and restart it. The demo runs main; branch work reaches it through a merge.
---

# Updating the public demo on the VPS

`demo.gammapdf.com` is a Gamma in demo mode ([docs/dev/guests.md](../../../docs/dev/guests.md)
"Demo mode"). It is its OWN compose project on the VPS `root@69.63.206.178`,
folder `/root/Container/gamma-demo/`: `compose.yml`, `.env` (`GAMMA_TAG=sha-<short>`,
the image to run, and `EDGE_SUBNET`, the subnet of `gamma-edge` whose
`X-Forwarded-For` the demo believes), `demo.env` (its settings) and
`data/` (its whole state). It runs `ghcr.io/tim4431/gamma:${GAMMA_TAG}` and
answers as `gamma-demo` on the external Docker network `gamma-edge`, where the
account project's Caddy (`/root/Container/gamma-account/`) routes the name to
it. The repository's copies are in `cloud/deploy/demo/`; setup and the layout:
[cloud/deploy/demo/README.md](../../../cloud/deploy/demo/README.md).

The demo runs `main`, like the NAS. Every `docker.yml` run on `main` (each
merge) pushes `sha-<short>` beside `:latest`; the demo pins that tag rather
than following `:latest`, so it changes only when deployed and a rollback is
one line. This skill builds nothing: it takes `main`'s newest green run and
writes its tag into the demo's `.env`. Branch work reaches the demo through
the `merge` skill, run only on the user's yes.
Workflows: [docs/dev/github_actions.md](../../../docs/dev/github_actions.md#dockeryml).

Rules:

- Work only in `/root/Container/gamma-demo/`. Never run a compose command in
  `gamma-account/` from this skill, never restart `account`, `share` or
  `caddy`: they belong to the `update-account-server` skill. The demo's route
  (the Caddyfile's `@demo` handle, Caddy on `gamma-edge`) lives there too; if
  it is missing, say so and point to that skill.
- Never read out, copy off the host or overwrite `demo.env` or `data/`.
- Never commit, never merge on your own, and never dispatch `docker.yml` from
  here.

In every command below, `<sha>` is the full `headSha` and `<tag>` is `sha-`
plus its first 7 characters (`echo sha-${sha:0:7}`). The metadata action's
`type=sha` truncates to 7; `git rev-parse --short` may print more and must not
be used for the tag.

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
  no tag was pushed and nothing changes on the host.

Work that is only on a branch is not in any of these. Say how many image
files `main` lacks (`git diff --name-only origin/main origin/<branch> --
backend/app.py backend/manage.py backend/gamma backend/requirements.txt
frontend ':!frontend/tests' Dockerfile docker-entrypoint.sh`) and offer the
`merge` skill; the merge's own run is then the one to wait for.

## 2. An older build

To go back to an earlier `main` build (the user names it, or pick one from
`gh run list --workflow docker.yml --branch main --status success --limit 10 --json headSha,createdAt,url`),
use its `headSha` as `<sha>`: every run's tag stays in GHCR.

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
A `compose.yml` that needs `EDGE_SUBNET` fails `docker compose config -q` until
the host's `.env` has it: add `EDGE_SUBNET=<subnet>` with the subnet
`docker network inspect gamma-edge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`
prints (with the user's agreement, like the copy). Without it the demo's
image trusts no proxy and every visitor shares Caddy's guest-login limit.

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
{ grep -v '^GAMMA_TAG=' .env.bak || true; printf 'GAMMA_TAG=%s\n' "$TAG"; } > .env
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

Report the old → new tag and commit (short sha + subject), the `docker.yml`
run it came from, and anything unusual in the log (a migration step, errors).

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
