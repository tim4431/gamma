---
name: update-demo-server
description: Build the Gamma server image from a branch by dispatching docker.yml (only :sha-<short>, never :latest), then pin that tag for the `demo` service on the VPS behind demo.gammapdf.com and restart it. No merge to main is needed.
---

# Updating the public demo on the VPS

`demo.gammapdf.com` is a Gamma in demo mode ([docs/dev/guests.md](../../../docs/dev/guests.md)
"Demo mode"): the `demo` service of the compose project on the VPS
`root@69.63.206.178`, folder `/root/Container/gamma-account/`, next to
`account`, `share` and `caddy` (Cloudflare in front, Caddy's `*.gammapdf.com`
site proxying the name to `demo:9001`). It runs `ghcr.io/tim4431/gamma`
pinned by a `sha-<short>` tag. `.github/workflows/docker.yml` dispatched on a
branch without a version pushes exactly that tag and never moves `:latest`,
which the NAS pulls. This skill builds through it and pins the result in the
HOST's `compose.yml`. The host's `demo` image line is the one that counts: it
is usually ahead of the one in the repository. Setup and first deployment:
[cloud/deploy/README.md](../../../cloud/deploy/README.md) "The demo server".
Workflows: [docs/dev/github_actions.md](../../../docs/dev/github_actions.md#dockeryml).

Never read out, copy off the host or overwrite `.env`, `share.env`,
`demo.env`, `data/` or `share-data/`. Restart only `demo` (plus a Caddy
reload when the Caddyfile changed). `account` and `share` belong to the
`update-account-server` skill. Never commit. Never pass `-f version` to
`docker.yml`: a version adds the release tags, and with them `:latest`.

In every command below, `<sha>` is the full `headSha` and `<tag>` is
`sha-` + its first 7 characters (`echo sha-${sha:0:7}`). The metadata
action's `type=sha` truncates to 7; `git rev-parse --short` may print more
and must not be used for the tag. The pinned `share` tag (`sha-a0d31c6`) shows
the shape.

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

A commit that already has a green `docker.yml` run has its tag in GHCR
(every run, including a push to `main`, pushes `sha-<short>`). Look before
building:

```bash
git rev-parse origin/<branch>
gh run list --workflow docker.yml --commit <that sha> --status success --limit 1 --json databaseId,headSha,event,url
```

If a run is listed, skip the build and use its `headSha`. To redeploy an
older published commit without building (the user names it, or pick one
from `gh run list --workflow docker.yml --status success --limit 10 --json headSha,headBranch,event,createdAt`),
use that `headSha` and go to step 3.

Otherwise dispatch and find the run:

```bash
gh workflow run docker.yml --ref <branch>
gh run list --workflow docker.yml --branch <branch> --event workflow_dispatch --limit 1 --json databaseId,headSha,status,url
```

The run may take a few seconds to appear. List it again rather than guess.
Its `headSha` must equal `git rev-parse origin/<branch>`. Then wait:

```bash
gh run watch <run-id> --exit-status
```

It is a multi-arch build (amd64 and arm64 under QEMU), about 10 minutes,
longer without a warm cache. If it is red, report
`gh run view <run-id> --log-failed` and stop: no tag was pushed and nothing
changes on the host. `:latest` is untouched either way (the tag rules are
explained in docker.yml's header).

Note `<sha>` and `<tag>`.

## 3. What runs now

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-account && grep -A3 '^  demo:' compose.yml | grep 'image:' ; docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q demo)"
```

This prints the pinned image line (keep the old tag for rollback) and the
running commit. If the revision equals `<sha>`, the image needs no deploy:
say so, run step 4 only if `cloud/deploy/` changed since that commit, and
otherwise stop.

- No `demo:` service in the host's `compose.yml`, or no `demo.env`
  (`ssh root@69.63.206.178 "ls /root/Container/gamma-account"`), means this
  is the FIRST deployment. Follow cloud/deploy/README.md "The demo server"
  with the user. `demo.env` must exist before the new `compose.yml` is
  copied over: while a file named by `env_file` is missing, every
  `docker compose` command in that folder fails, the account server's
  included. Create it from the example only with the user's agreement and
  only if it is absent:
  `git show <sha>:cloud/deploy/demo.env.example | ssh root@69.63.206.178 "cd /root/Container/gamma-account && test ! -e demo.env && cat > demo.env && chmod 600 demo.env"`.
  The user sets `GAMMA_ADMIN_PASSWORD` or reads the one-time random password
  from the log themselves (step 6). Do not paste it into the chat.

## 4. Deploy files changed?

The host keeps its own `compose.yml` and `Caddyfile`. Compare them with the
commit being deployed:

```bash
ssh root@69.63.206.178 "cat /root/Container/gamma-account/compose.yml" | diff - <(git show <sha>:cloud/deploy/compose.yml)
ssh root@69.63.206.178 "cat /root/Container/gamma-account/Caddyfile"   | diff - <(git show <sha>:cloud/deploy/Caddyfile)
```

- The `demo` image line normally differs, because the host is ahead. That
  difference alone is no reason to copy anything.
- For any other difference, show it to the user and copy the file only once
  they agree. A copied `compose.yml` resets the `demo` line to the
  repository's tag, and step 5 then re-pins it. Changes it makes to
  `account` or `share` (e.g. a new `share` tag) take effect only at their
  next `docker compose up -d`. Tell the user; this skill does not restart
  them.

  ```bash
  git show <sha>:cloud/deploy/compose.yml | ssh root@69.63.206.178 "cd /root/Container/gamma-account && cp compose.yml compose.yml.bak && cat > compose.yml"
  ```

- The Caddyfile is bind-mounted as a single file, so write it IN PLACE
  (`cat >`, which keeps the inode). Never use `sed -i`, `mv` or `scp`, which
  replace the file and leave the container reading the old one. Then reload,
  and restore the old copy if the reload refuses the new one (the running
  config stays the old one when a reload fails):

  ```bash
  git show <sha>:cloud/deploy/Caddyfile | ssh root@69.63.206.178 "cd /root/Container/gamma-account && cp Caddyfile Caddyfile.bak && cat > Caddyfile && (docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile || { cat Caddyfile.bak > Caddyfile; echo 'reload refused: Caddyfile restored'; exit 1; })"
  ```

- `demo.env` is never copied or edited. List the variables the example
  gained or lost since the running commit, and the names (names only,
  never values) the host's file sets:

  ```bash
  git diff <running revision>..<sha> -- cloud/deploy/demo.env.example
  ssh root@69.63.206.178 "grep -o '^[A-Z_]*=' /root/Container/gamma-account/demo.env"
  ```

  Name any new variable to the user to add by hand (then `up -d demo`
  applies it).

## 5. Pin the tag and restart

One script on the host. It checks the tag exists before touching the file,
rewrites ONLY the image line inside the `demo:` service (the range runs from
`  demo:` to the next line indented two spaces or less; `share` uses the same
image name with another tag and stays as it is), refuses and restores if
anything else changed, then pulls and recreates `demo` alone:

```bash
ssh root@69.63.206.178 bash -s -- <tag> <<'EOF'
set -eu
TAG="$1"
IMG=ghcr.io/tim4431/gamma
echo "$TAG" | grep -Eq '^sha-[0-9a-f]{7}$' || { echo "not a sha-<7 hex> tag: $TAG"; exit 1; }
cd /root/Container/gamma-account
test -f demo.env || { echo "demo.env missing: first deployment, see cloud/deploy/README.md"; exit 1; }
grep -q '^  demo:' compose.yml || { echo "no demo service in compose.yml: first deployment, see cloud/deploy/README.md"; exit 1; }
docker pull -q "$IMG:$TAG"
cp compose.yml compose.yml.pre-demo-pin
sed -i "/^  demo:[[:space:]]*\$/,/^ \{0,2\}[a-z]/ s#^\(    image: ghcr\.io/tim4431/gamma:\)sha-[0-9a-f]*[[:space:]]*\$#\1$TAG#" compose.yml
diff compose.yml.pre-demo-pin compose.yml || true
if ! docker compose config demo | grep -q "image: $IMG:$TAG\$"; then
  cp compose.yml.pre-demo-pin compose.yml; echo "the demo image line was not rewritten; compose.yml restored"; exit 1
fi
if [ "$(diff compose.yml.pre-demo-pin compose.yml | grep -c '^>')" -gt 1 ]; then
  cp compose.yml.pre-demo-pin compose.yml; echo "more than one line changed; compose.yml restored"; exit 1
fi
docker compose pull demo
docker compose up -d demo
EOF
```

The printed `diff` shows the one changed line (none if the tag was already
pinned). `up -d demo` recreates only `demo`: guests lose their session for
the seconds of the restart and keep their workspaces. At start the image
upgrades the data directory itself (`manage.py migrate`, snapshot first into
`demo-data/backups/`). It refuses a directory written by a newer build, so a
`demo` that keeps restarting needs its log read before anything else.

Offer to set the same tag on the `demo` image line of the repository's
`cloud/deploy/compose.yml` (a working-tree edit left for the user to commit,
never committed here). Then a later copy of the file, by this skill or by
`update-account-server`, does not roll the demo back.

## 6. Verify

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker compose ps && docker compose logs --tail 20 demo"
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q demo)"
curl -s https://demo.gammapdf.com/api/server-config | grep -o '"demo": *true'   # "demo":true
curl -s -o /dev/null -w "%{http_code}\n" https://demo.gammapdf.com/            # 200
```

- `demo` is `Up … (healthy)`. The image's healthcheck needs up to ~30 s
  after start. Its revision label equals `<sha>`.
- `server-config` reports `"demo":true`. If it is missing, `GAMMA_DEMO=1` is
  not in `demo.env` (or the build predates demo mode). A Cloudflare 521/502
  means Caddy or the container is not reachable: check
  `docker compose logs caddy`. A 404 from the demo name means the host's
  Caddyfile lacks the `@demo` handle (step 4).
- On a first deployment the admin's one-time password is in the log. Give
  the user the command (`docker compose logs demo | grep -A2 "created the admin account"`)
  rather than its output. The shared AI key, "Guests may use it" and the
  allowance are the admin's to set in the GUI (Settings → Server → Shared AI
  provider). AI keys never go into `demo.env`.

Report the old → new tag and commit (short sha + subject), the run link if
one was built, and anything unusual in the log (a migration step, errors).

## Resetting the demo (only when the user asks)

`demo-data/` is the demo's whole state (guest accounts and workspaces, the
admin account, the shared AI key, the settings) and it is disposable, but
wipe it only on the user's explicit request:

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker compose stop demo && rm -rf demo-data && docker compose up -d demo"
```

The instance starts fresh. The admin is seeded again, with a new random
password in the log unless `demo.env` sets one, and the shared AI key,
"Guests may use it" and the allowance must be entered again. A
`GAMMA_GUEST_SEED` zip kept in `demo-data/` is gone too. Move it aside first
(`mv demo-data/guest-seed.zip .` before the `rm`, then
`mkdir -p demo-data && mv guest-seed.zip demo-data/` before the `up`) if one
is in use.

## Rollback

Run step 5 again with the previous tag from step 3. If the newer build
already upgraded the data directory, the older one refuses it and keeps
restarting. Then either restore the snapshot the upgrade took in
`demo-data/backups/<time>-v<N>/` ([docs/dev/migrations.md](../../../docs/dev/migrations.md)
"Running it"), or reset the demo as above. Both need the user's agreement.
