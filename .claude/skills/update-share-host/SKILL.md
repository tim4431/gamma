---
name: update-share-host
description: Pin the Gamma Cloud share host (app.gammapdf.com, share.gammapdf.com, the page hosts) to main's newest server image — the :sha-<short> tag a merge's docker.yml run pushed — through the account project's compose.override.yml on the VPS, and restart that one service. It runs main, like the demo; branch work reaches it through a merge.
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

It runs `main`, like the demo and the NAS. The repository's
`cloud/deploy/compose.yml` names `:latest` for it; the host pins the exact
build in `compose.override.yml` beside it, a file holding only the share
image, which `docker compose` loads by itself. This skill builds nothing: it
takes `main`'s newest green `docker.yml` run and writes its tag there.
Pinned rather than `:latest`, the share host changes only when deployed, and
a rollback is one line. Branch work reaches it through the `merge` skill,
run only on the user's yes. Workflows:
[docs/dev/github_actions.md](../../../docs/dev/github_actions.md#dockeryml).

Rules:

- Touch only `compose.override.yml` and the `share` service. Never restart
  `account` or `caddy`, never `down` the project: they are the
  `update-account-server` skill's. The project's `compose.yml` and
  `Caddyfile` are that skill's too.
- Never read out, copy off the host or overwrite `.env`, `share.env`,
  `data/` or `share-data/`.
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

- `completed success` → its `headSha` is `<sha>`.
- `queued` / `in_progress` → wait in the background with
  `gh run watch <run-id> --exit-status`, then use it.
- `completed failure` → report `gh run view <run-id> --log-failed` and stop.

## 2. What runs now

```bash
ssh root@69.63.206.178 "cd /root/Container/gamma-account && cat compose.override.yml 2>/dev/null; docker inspect --format '{{.Config.Image}} {{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q share)"
```

This prints the pinned image (keep it for rollback) and the running commit.
If the revision equals `<sha>`, there is nothing to deploy: say so and stop.
No `compose.override.yml` yet means the share host runs whatever image the
host's `compose.yml` names: the first run of this skill creates the file.

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
step: tell the user, suggest the `release` skill together with this deploy,
and go on only on their yes.

## 4. Pin the tag and restart

One script on the host. It checks the tag format and that the image exists
before touching anything, writes `compose.override.yml` (the old one kept as
`compose.override.yml.bak`), checks the resolved image, then recreates the
share host alone:

```bash
ssh root@69.63.206.178 bash -s -- <tag> <<'EOF'
set -eu
TAG="$1"
echo "$TAG" | grep -Eq '^sha-[0-9a-f]{7}$' || { echo "not a sha-<7 hex> tag: $TAG"; exit 1; }
cd /root/Container/gamma-account
docker pull -q "ghcr.io/tim4431/gamma:$TAG"
[ -f compose.override.yml ] && cp compose.override.yml compose.override.yml.bak
printf '%s\n' "# The share host's image: main's build, pinned by the update-share-host skill." \
  "services:" "  share:" "    image: ghcr.io/tim4431/gamma:$TAG" > compose.override.yml
if ! docker compose config share | grep -q "image: ghcr.io/tim4431/gamma:$TAG\$"; then
  if [ -f compose.override.yml.bak ]; then cp compose.override.yml.bak compose.override.yml; else rm compose.override.yml; fi
  echo "compose does not resolve the new tag; the override was put back"; exit 1
fi
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
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker compose ps share && docker compose logs --tail 20 share"
ssh root@69.63.206.178 "cd /root/Container/gamma-account && docker inspect --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' \$(docker compose ps -q share)"
curl -s -o /dev/null -w "%{http_code}\n" https://app.gammapdf.com/                    # 200
curl -s https://share.gammapdf.com/api/server-config | head -c 300                  # a JSON answer
```

- `share` is `Up … (healthy)`; the image's healthcheck needs up to about
  30 s after start. Its revision label equals `<sha>`.
- A 502 means Caddy cannot reach `share`: read `docker compose logs share`
  first (a refused data directory, a failed migration).

Report the old → new tag and commit (short sha + subject), the `docker.yml`
run it came from, and anything unusual in the log (a migration step, errors).

## Rollback

Run step 4 again with the previous tag from step 2 (or
`compose.override.yml.bak`). If the newer build already upgraded
`share-data/`, the older one refuses it and keeps restarting: restore the
snapshot the upgrade took in `share-data/backups/<time>-v<N>/`
([docs/dev/migrations.md](../../../docs/dev/migrations.md) "Running it"), only
with the user's agreement — it discards what was published since.
