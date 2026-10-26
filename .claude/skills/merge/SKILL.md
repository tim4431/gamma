---
name: merge
description: Merge the branch's committed work into main via a PR and report the Docker publish the merge started. Runs the PR's suites locally first and fixes what fails, flaky test or real bug, in commits of its own; a red PR check gets the same fix and another try. Never commits the tree's uncommitted work, never releases the desktop app or extension — that is the `release` skill.
---

# Merge to main

One job: get what is committed on the branch into `main`, green. The
suites the PR will run are run here first, and a failure, locally or on
the PR, is fixed and the merge tried again rather than handed back. The
`check` workflow validates the PR (and `cloud` when `cloud/` changed,
`site` when `sites/` or its inputs changed; `check` is skipped for a PR
that only touches those); the merge itself triggers `docker.yml`
(`ghcr.io/tim4431/gamma:latest`; skipped for a merge that only touches
`cloud/` or `sites/`) and, when the site's inputs changed, `site.yml`,
which redeploys gammapdf.com from `main`. The desktop app and the browser
extension are NOT released by a merge — run the `release` skill for that
(it dispatches `desktop.yml` on `main`, which releases both, and then sends
the iPad app to TestFlight). The iPad app goes to TestFlight only when a
commit on `main` carries a fresh `ipad-test-release-NNN` marker (a release
pushes one; between releases, the `release-ipad` skill); a plain merge
releases nothing there either. The account
server and the website do not need a merge at all: `update-account-server`
/ `build-site` publish them from the branch. Details:
`docs/dev/github_actions.md`.

**Scope: existing commits, plus the fixes this skill makes.** Uncommitted
changes are ongoing work — leave them in the tree, never commit them here;
a fix's commit stages only the fix's own files (`git add <paths>`, never
`-A`). A failure whose fix would touch a file with uncommitted changes is a
question for the user. If there is nothing committed beyond `origin/main`,
stop and say so.

**Fixing a failure.** Find the cause before touching anything; a red test
is either:

- **flaky** — it passes on a rerun, or fails only under load, in another
  order or on one OS: a race, an unawaited state, a fixed sleep, a shared
  name or data directory between files. Fix the cause (wait for the state
  the step needs, isolate the data, the rules in
  [debugging.md](../../../docs/dev/debugging.md)), not the symptom: no
  retry loop, no longer timeout alone, no skipped test. Rerun it several
  times (`--only <step>` for a browser step, the file alone for pytest) to
  show the flake is gone.
- **a real bug** — in the app or in a test that asserts the wrong thing.
  Fix it with a test that fails without the fix, and verify it as any
  change (the [test policy](../../../docs/dev/debugging.md#local-changes-test-the-affected-modules),
  the `verify` skill for UI behavior), docs updated with the behavior.

Each fix is its own commit on the branch, its message saying what failed
and why. A failure that needs a product decision, or one whose cause is
outside the repo and that a rerun does not clear, is reported to the user
instead, and the merge waits.

## Steps

1. **Check**: `git status --short`, `git fetch -q origin`,
   `git log --oneline origin/main..HEAD`. On `main` itself: create a branch
   first (`git checkout -b <short-topic>`); the normal case is `dev`. No
   commits ahead of main → "nothing to merge", stop.

2. **Test locally**: what the PR's checks will run, on this machine first
   (Node through `fnm`, the backend venv's Python: CLAUDE.md; `$SCRATCH`
   is the session's scratchpad directory):

   ```bash
   # backend/ — check: backend (~1.5 min)
   venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadfile
   # frontend/ — check: frontend
   npm test
   # repository root — check: branding, frontend (desktop), extension
   node tools/branding/build.mjs --check && node --test tools/branding/check.test.mjs
   node --test desktop/test/keeping.test.js
   node --test extension/tests/*.test.mjs
   # frontend/ — check: e2e (~5 min), built into the scratchpad: a build
   # into frontend/dist would empty what the developer's 9001 server serves
   npx vite build --outDir "$SCRATCH/dist" --emptyOutDir
   GAMMA_E2E_DIST="$SCRATCH/dist" npm run e2e -- --continue
   # cloud/, when the branch changes it — cloud.yml
   ../backend/venv/Scripts/python.exe -m pytest tests -q
   # sites/, when the branch changes sites/, docs/, PRIVACY.md or TERMS.md — site.yml
   node build.mjs --strict
   ```

   Run the browser suite on its own, not beside the backend suite: its
   steps time what they wait for, and a loaded machine makes failures CI
   would not see (a failure that shows only under load is still a flake
   worth fixing, but find it apart from the noise). A failure → fix it (above),
   rerun what the fix touches, and go on once everything is green.

3. **Push**: `git push -u origin <branch>`.

4. **PR**: `gh pr create --base main --head <branch> --title "<title>" --body "<bullets>"`
   — title from the commit subjects, body a few bullets of what changed.
   If a PR for the branch is already open, reuse it.

5. **Wait for the checks**: `gh pr checks <n> --watch` (`check`: backend
   pytest, frontend unit tests + build, the browser suite in two shards,
   extension zip; ~5 min, skipped for a PR that only touches the account
   server or the website; `cloud`: the account server's tests, when
   `cloud/` changed; `site`: the website's build, when its inputs
   changed). Red → read the failure: `gh run view <id> --log-failed`, and
   for the browser suite the screenshots, page problems and server log
   tail, `gh run download <id> -n e2e-failures-<shard> -D "$SCRATCH/ci"`.
   Reproduce it locally (`npm run e2e -- --only "<step>"`, the pytest
   file alone), fix it (above), commit, push — the push restarts the
   checks — and watch again. A failure CI shows but the machine does not
   is a flake still: the log, the screenshots and the code find its cause.
   One whose cause is outside the repo (a runner, npm or PyPI outage, a
   GitHub incident; the log says so) gets one `gh run rerun <id> --failed`,
   named in the report. After three red rounds, stop and report what is
   still failing. Never merge over a red check.

6. **Merge**: `gh pr merge <n> --merge`. First, the data format: the
   share host follows `main`'s `:latest` through its Watchtower, so a
   merge reaches it within the hour, and every desktop app that publishes
   there pauses ("Paused · update Gamma") while its release lacks a
   workspace migration step the server has ([docs/dev/mirror.md](../../../docs/dev/mirror.md)
   "The data format"). Compare the newest step on the branch with the
   desktop release's:

   ```bash
   release=$(gh release view --json tagName -q .tagName); git fetch -q origin tag "$release"
   for s in HEAD "$release"; do
     echo "$s: $(git show $s:backend/gamma/migrations.py | sed -n '/^WORKSPACE_STEPS = \[/,/^\]/p' | grep -oE '^\s*\([0-9]+' | tr -d ' (' | tail -1)"
   done
   ```

   A higher number on the branch → tell the user before merging, and
   suggest the `release` skill right after (or holding the share host
   back with the `update-share-host` skill's rollback pin until it is
   out). Merge only on their yes.

7. **Report**: a few seconds after the merge,
   `gh run list --limit 5 --json workflowName,status,event,url,databaseId`
   shows the Docker run the merge started (none for an account-server-only
   merge — say so). Report it with its link. Do not
   wait for it unless asked (`gh run watch <id> --exit-status`).

8. **Clean up the worktree.** Once a branch's work is merged — here into
   `main`, or a feature branch's into `dev` — remove its worktree and the
   branch without asking: `git worktree remove .claude/worktrees/<name>`
   then `git branch -d <name>` (the plain `-d` refuses an unmerged branch,
   which is the one case to stop and ask; never `-D` or `--force` on your
   own). A worktree with uncommitted changes is likewise a question, not a
   deletion. Run this from the main checkout, after leaving the worktree
   session if the work was done in one.

Follow-ups to offer, not to run: `update-server`, `update-demo-server` and
`update-share-host` once the Docker run has published (they deploy that
build to the NAS, the demo and the share host; `update-needed` checks all
deployments); `release` if the merged
work should ship as a new desktop app, extension and iPad TestFlight
version; `release-ipad` if it should reach TestFlight alone (that needs its
marker in a commit before the merge, so it is a merge of its own).
