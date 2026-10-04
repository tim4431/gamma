---
name: update-needed
description: Find which Gamma deployments are behind the code — the account server (account.gammapdf.com), the public demo (demo.gammapdf.com), the NAS server — by comparing the commit each one runs with what is pushed, then run update-account-server, update-demo-server and update-server for exactly those, in that order. `check` as the argument reports without deploying.
---

# Update what needs updating

Three deployments, each with its own skill. This skill decides which of them
the code has moved past, then runs those skills. It never re-implements
their steps: for each deployment it runs, read that skill's `SKILL.md`
and follow it.

| deployment | runs | built from | skill |
|---|---|---|---|
| account server | `ghcr.io/tim4431/gamma-cloud`, `/root/Container/gamma-account/` on `root@69.63.206.178` | the branch (`cloud.yml`) | `update-account-server` |
| demo | `ghcr.io/tim4431/gamma:sha-<short>`, `/root/Container/gamma-demo/` on the same VPS | `main` only (the `sha-<short>` a merge's `docker.yml` run pushes, pinned) | `update-demo-server` |
| NAS | `ghcr.io/tim4431/gamma:latest`, `/mnt/TimNAS2/Container/gamma/` on `Tim@100.100.10.237` | `main` only (a merge publishes `:latest`) | `update-server` |

Arguments: a branch name (default `git branch --show-current`, normally
`dev`) and/or `check` (report, then stop). Never commit. Never merge without
asking: the demo and the NAS follow `main`, and getting branch changes there
is the `merge` skill's job, run only on the user's yes.

## 1. Collect (read-only)

```bash
git fetch -q origin
git rev-parse origin/<branch> origin/main
git status --short backend/ frontend/ cloud/ Dockerfile docker-entrypoint.sh
git log --oneline origin/<branch>..<branch>
```

What each deployment runs: every image carries its commit in the
`org.opencontainers.image.revision` label. One read per host:

```bash
L='{{.Config.Image}} {{index .Config.Labels "org.opencontainers.image.revision"}} {{.Name}}'
ssh -o ConnectTimeout=10 -o BatchMode=yes root@69.63.206.178 "for d in gamma-account gamma-demo; do cd /root/Container/\$d && for c in \$(docker compose ps -q); do docker inspect --format '$L' \$c; done; done"
ssh -o ConnectTimeout=10 -o BatchMode=yes Tim@100.100.10.237 "cd /mnt/TimNAS2/Container/gamma/ && for c in \$(docker compose ps -q); do docker inspect --format '$L' \$c; done"
```

From the output: the account server is the `gamma-cloud` image
(`gamma-account-account-1`), the demo is `gamma-demo-demo-1`, the NAS is its
`ghcr.io/tim4431/gamma:latest` container. The account project's `share`
service (a pinned Gamma image, the share host) is reported but never updated
here: its tag is changed by hand in the host's `compose.yml`.

What `main`'s image is (the candidate for the demo and the NAS) — the
newest `docker.yml` run on `main`:

```bash
gh run list --workflow docker.yml --branch main --limit 3 --json headSha,status,conclusion,event,url
```

- A host that does not answer: report it, mark its deployments *unknown*,
  go on with the rest.
- A running commit missing locally (`git cat-file -e <sha>^{commit}` fails):
  `git fetch origin <sha>`; if it is still missing, compare nothing and
  mark it *unknown — running a commit this clone does not have*.
- An empty label: *unknown build*; count it as needing an update.

## 2. Decide

What goes into each image (the Dockerfiles' `COPY` lines), as pathspecs:

```bash
CLOUD="cloud/app.py cloud/manage.py cloud/gammacloud cloud/requirements.txt cloud/Dockerfile"
IMG="backend/app.py backend/manage.py backend/gamma backend/requirements.txt backend/requirements-s3.txt frontend :!frontend/tests Dockerfile docker-entrypoint.sh"
```

A deployment needs an update when the image it runs differs from the
candidate in those paths — `git diff --name-only <running> <candidate> -- <paths>`
is not empty. Tests, docs and other folders never make one due.

- **Account server**: running vs `origin/<branch>`, `$CLOUD`. Also list
  `cloud/deploy/` changes outside `cloud/deploy/demo/` (the host's
  `compose.yml` / `Caddyfile` / `.env.example`): they alone make it due, since
  its skill compares and copies them.
- **Demo** and **NAS**, each: running vs the newest `docker.yml` run on
  `main`:
  - that run succeeded and its `headSha` ≠ the running commit, and `$IMG`
    differs → due.
  - that run is queued / in progress → due once it finishes (both skills
    wait for it).
  - that run failed → not deployable; report its link.
  - the demo also: `cloud/deploy/demo/` changes between its running commit
    and that `headSha` (its skill compares `compose.yml` and names new
    `demo.env` variables).
  - separately: `git diff --name-only origin/main origin/<branch> -- $IMG`
    not empty → the branch has server changes `main` lacks. The demo and the
    NAS get them only through a merge. Say how many files, and ask whether
    to run the `merge` skill; never merge on your own.
- **Share host** (report only): running vs `origin/<branch>`, `$IMG`.

Uncommitted or unpushed changes in those paths are NOT in any image: name
them. Unpushed commits are pushed by `build-cloud` and the `merge` skill
(`git push origin <branch>`); uncommitted ones stay out — say so, and ask
whether to go on before building or merging.

## 3. Report the plan

One table, then the decision per row. For example:

| deployment | runs | candidate | changed | action |
|---|---|---|---|---|
| account server | `5372586` | `e3e058c` (dev) | 12 image files, `compose.yml`, `Caddyfile` | update |
| demo | `sha-0e07fe9` | `4bd648c` (main, built) | 9 image files | update |
| NAS | `4bd648c` | `4bd648c` (main, built) | — | up to date |

Then, under the table: dev has 16 server files main lacks → merge?
| share host | `sha-a0d31c6` | `e3e058c` | 20 image files | manual (pinned tag) |

Short commits with their subjects (`git log -1 --format='%h %s' <sha>`), and
under each due row the changed files grouped by folder — a line each, not a
dump. With `check`, stop here.

## 4. Update, in this order

Nothing due → say so and stop. Otherwise follow each due deployment's skill,
one after the other:

1. **Account server first** (`.claude/skills/update-account-server/SKILL.md`,
   which builds through `build-cloud`). Gamma servers may rely on what a new
   account server offers (for example connecting a server needs its
   `/connect-server`), never the other way round.
2. **The merge**, when the user said yes to one in step 2: the `merge`
   skill. Its `docker.yml` run is then the demo's and the NAS's candidate,
   and both skills wait for it before deploying.
3. **Demo** (`.claude/skills/update-demo-server/SKILL.md`).
4. **NAS** (`.claude/skills/update-server/SKILL.md`).

Only the account server builds here (`cloud.yml`, ~3 minutes); the demo and
the NAS take `main`'s build (`docker.yml`, ~10 minutes after a merge). When
both are pending, start the `cloud.yml` build and the merge together, deploy
the account server as soon as its run is green, then the demo and the NAS
once the merge's run is. Wait with `gh run watch <id> --exit-status` in the
background, never a sleep loop.

Each skill keeps its own stops: a red build ends that deployment (report
the failed log, go on with the others only if they do not depend on it —
the demo and the NAS do not depend on the account server's build), a
deploy-file difference is shown and copied only on the user's yes, a secret
file is never read or copied. A deployment whose skill finds it already
running the candidate is not an error; note it and move on.

## 5. Report

Per deployment: old → new commit (short sha + subject), the run link if one
was built, and anything unusual from its verify step (a migration running,
errors in the log). Then what is left: a pending merge, the share host's
pinned tag, uncommitted changes that did not ship.
