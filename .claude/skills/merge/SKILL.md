---
name: merge
description: Merge a snapshot of the branch's committed work (a merge/<date> branch in a worktree, so later pushes to dev stay out) into main via a PR, bring dev up to main after, and report the Docker publish the merge started. Runs the PR's suites locally first and fixes what fails, flaky test or real bug, in commits of its own; a red PR check gets the same fix and another try, and a flake in code the merge does not change may get one rerun and a line in the known flakes. Never commits the tree's uncommitted work, never releases the desktop app, the Android app or the extension — that is the `release` skill.
---

# Merge to main

One job: get what is committed on the branch into `main`, green. The
suites the PR will run are run here first, and a failure, locally or on
the PR, is fixed and the merge tried again rather than handed back. The
`check` workflow validates the PR (and `ipad` / `android` when the apps or
what they carry changed, `Assistant plugin package` for the plugins,
`cloud` when `cloud/` changed, `fleet` when `cloud/fleet/` changed, `site`
when `sites/` or its inputs changed; `check` is skipped for a PR that only
touches the account server or the website); the merge itself triggers
`docker.yml` (`ghcr.io/tim4431/gamma:latest`; skipped for a merge that
only touches `cloud/` or `sites/`), `site.yml` when the site's inputs
changed (it redeploys gammapdf.com from `main`) and `fleet.yml` when
`cloud/fleet/` changed. The desktop app, the Android app and the browser
extension are NOT released by a merge — run the `release` skill for that
(it dispatches `desktop.yml` on `main`, which releases all three, sends
them to their stores and then the iPad app to TestFlight). The iPad app goes to TestFlight only when a
commit on `main` carries a fresh `ipad-test-release-NNN` marker (a release
pushes one; between releases, the `release-ipad` skill); a plain merge
releases nothing there either. The account
server and the website do not need a merge at all: `update-account-server`
/ `build-site` publish them from the branch. Details:
`docs/dev/github_actions.md`.

**Scope: the commits on the branch when the merge starts, plus the fixes
this skill makes.** The merge takes a snapshot of the branch (step 1) and
merges that: work merged into `dev` while the PR runs waits for the next
merge, rather than restarting the checks on code nobody tested here
(releasing 0.4.0 took seven rounds, three of them for two features merged
into `dev` meanwhile). Uncommitted changes are ongoing work — leave them in
the tree, never commit them here; a fix's commit stages only the fix's own
files (`git add <paths>`, never `-A`). A failure whose fix would touch a
file with uncommitted changes is a question for the user. If there is
nothing committed beyond `origin/main`, stop and say so.

**Fixing a failure.** Find the cause before touching anything; a red test
is one of:

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
- **a test that does not earn its place** — it went red because an
  intended change moved what it copies: a change detector (exact wording,
  a CSS value, a source grep, a snapshot), a mirror of the implementation,
  a re-check of a behavior a cheaper test covers
  ([What a test is for](../../../docs/dev/debugging.md#what-a-test-is-for)).
  Updating its expectation only sets up the next red round. Rewrite it to
  assert what the change is for, or delete it when another test already
  does, in a commit of its own that names the kind. A regression test,
  or the last test of a user-visible behavior, is fixed instead.

Each fix is its own commit on the snapshot's branch, its message saying
what failed and why. A failure that needs a product decision, or one whose cause is
outside the repo and that a rerun does not clear, is reported to the user
instead, and the merge waits.

## Steps

1. **Check, and take the snapshot**: `git status --short`,
   `git fetch -q origin`, `git log --oneline origin/main..HEAD`. No commits
   ahead of main → "nothing to merge", stop. Then give the branch's head a
   branch of its own, in a worktree, without leaving the checkout's branch
   (another session may be committing on `dev` there, and its commits must
   stay on `dev`):

   ```bash
   git branch merge/<yyyy-mm-dd> HEAD
   git worktree add .claude/worktrees/merge-<yyyy-mm-dd> merge/<yyyy-mm-dd>
   ```

   Everything from here (the suites, the fixes, the commits, the pushes)
   happens in that worktree. On `main` itself the same: the snapshot is the
   branch.

2. **Test locally**: what the PR's checks will run, on this machine first,
   in the worktree (Node through `fnm`: CLAUDE.md; `$MAIN` is the main
   checkout, `git worktree list | head -1`, whose `backend/venv` runs the
   backend and whose ignored `backend/venv313` — made once with
   `uv venv -p 3.13 backend/venv313` and `uv pip install -r
   requirements-mobile.txt -r requirements-dev.txt` — the apps' Python;
   `npm ci` once in the worktree's `frontend/`; `$SCRATCH` is the session's
   scratchpad directory):

   ```bash
   # backend/ — check: backend (~4 min)
   "$MAIN/backend/venv/Scripts/python.exe" -m pytest tests -q -n auto --dist loadfile
   # frontend/ — check: frontend
   npm run lint && npm test
   # repository root — check: branding, frontend (desktop), extension
   node tools/branding/build.mjs --check && node --test tools/branding/check.test.mjs
   node --test desktop/test/*.test.js
   node --test extension/tests/*.test.mjs
   # repository root — check: mobile-pins, when the branch changes
   # backend/requirements*.txt; a Python 3.13 on Linux or macOS (WSL here:
   # under a Windows Python, pip asks for pywin32, which no device has)
   bash tools/check_mobile_pins.sh
   # backend/ — check: backend-mobile, when the branch changes backend/: the
   # suite under the apps' Python and pins
   "$MAIN/backend/venv313/Scripts/python.exe" -m pytest tests -q -n auto --dist loadfile
   # frontend/ — check: e2e (~12 min), the worktree's own build (the
   # developer's 9001 server serves the main checkout's frontend/dist, not this one)
   npm run build
   npm run budget  # check: frontend
   GAMMA_E2E_PYTHON="$MAIN/backend/venv/Scripts/python.exe" npm run e2e -- --continue
   # cloud/, when the branch changes it — cloud.yml
   "$MAIN/backend/venv/Scripts/python.exe" -m pytest tests -q
   # sites/, when the branch changes sites/, docs/, desktop/ui/fonts/, docker-compose.yml.example, PRIVACY.md or TERMS.md — site.yml
   node build.mjs --strict
   # android/, when the branch changes what android.yml watches (android/,
   # backend/requirements*.txt, backend/gamma_mobile.py, desktop/ui/, the
   # ink fixtures) — android.yml, JDK 21, the Android SDK and a Python 3.13
   # (CLAUDE.md says where); its emulator job runs on the PR. prebuilt
   # takes the worktree's frontend/dist, built above
   bash ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug -Pgamma.webBuild=prebuilt
   ```

   Run the browser suite on its own, not beside the backend suite: its
   steps time what they wait for, and a loaded machine makes failures CI
   would not see (a failure that shows only under load is still a flake
   worth fixing, but find it apart from the noise). A failure → fix it (above),
   rerun what the fix touches, and go on once everything is green.

3. **Push**: `git push -u origin merge/<yyyy-mm-dd>`.

4. **PR**: `gh pr create --base main --head merge/<yyyy-mm-dd> --title "<title>" --body "<bullets>"`
   — title from the commit subjects, body a few bullets of what changed.
   If a PR for the branch is already open, reuse it.

5. **Wait for the checks**: `gh pr checks <n> --watch` (`check`: the
   mobile pins, the backend suite on the apps' Python 3.13 and pins
   (`backend-mobile`), backend pytest, frontend lint, unit tests + build + startup budget, the desktop
   shell's unit tests, the browser suite in three shards, extension zip, and
   `perf`, the timing budgets, a table in the run's summary and never red;
   ~5 min, skipped for a PR that only touches the account server or the
   website; `ipad`: the iPad app on a simulator, ~40 min, and `android`:
   the Android unit tests, release build and emulator, ~30 min, when the
   apps or what they carry changed; `Assistant plugin package`: the
   plugins on three OSes, when they changed; `cloud`: the account server's
   tests, when `cloud/` changed; `fleet`: the fleet agent's, when
   `cloud/fleet/` changed; `site`: the website's build, when its inputs
   changed). Red → read the failure: `gh run view <id> --log-failed`, and
   for the browser suite the screenshots, page problems and server log
   tail, `gh run download <id> -n e2e-failures-<shard> -D "$SCRATCH/ci"`
   (for the Android emulator, `-n android-emulator-failures`: the
   server's log, logcat and the test report).
   Reproduce it locally (`npm run e2e -- --only "<step>"`, the pytest
   file alone), fix it (above), commit, push — the push restarts the
   checks — and watch again. A failure CI shows but the machine does not
   is a flake still: the log, the screenshots and the code find its cause.
   One whose cause is outside the repo (a runner, npm or PyPI outage, a
   GitHub incident; the log says so) gets one `gh run rerun <id> --failed`,
   named in the report. So may one in code the merge does not change —
   neither the failing test nor what it exercises is in
   `git diff origin/main...HEAD` — that the machine does not show and whose
   cause is not found within the round: rerun it once, and add it to
   [debugging.md "Known flakes"](../../../docs/dev/debugging.md#known-flakes)
   (what was seen, what is known of the cause) in a commit of its own on
   the branch, named in the report. A failure in code the merge changes is
   always fixed first, and a second failure of the same step is no flake
   to rerun. After three red rounds, stop and report what is
   still failing. Never merge over a red check. The `perf` job is green
   even when one of its suites failed, so `gh pr checks` never shows a
   breach: read `gh run view <id>`, whose annotations name each case over
   its budget (`perf budget`) and each suite that failed (`perf suite`).
   A failed suite with no case over budget failed a case's own check,
   did not load or crashed; its log says which:
   `gh run view <id> --log --job "$(gh run view <id> --json jobs --jq '.jobs[] | select(.name == "perf") | .databaseId')" | grep -E 'not ok|✖|FAILED|over its budget|Error'`.
   Name the case and its numbers in the report, and reproduce it on an
   idle machine (`npm run test:perf`, `python -m pytest tests/test_perf.py
   -m perf -s`). It is not fixed as part of the merge, and a breach never
   by raising its budget ([debugging.md](../../../docs/dev/debugging.md#performance-budgets)).

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
   shows the runs the merge started: Docker (none for a merge that only
   touches the account server or the website — say so), and the site's
   and the fleet agent's when their paths changed. Report each with its
   link. Do not wait for them unless asked (`gh run watch <id>
   --exit-status`).

8. **Bring `dev` up, and clean up.** The fixes the merge made are on
   `main` now and not on `dev`: in the main checkout, on `dev`,
   `git fetch -q origin && git merge origin/main`, then `git push origin dev`
   (a conflict, or uncommitted changes in the files the merge brings, is a
   question for the user). Then remove the snapshot without asking:
   `git worktree remove .claude/worktrees/merge-<yyyy-mm-dd>`,
   `git branch -d merge/<yyyy-mm-dd>`, `git push origin --delete merge/<yyyy-mm-dd>`.
   The same goes for any branch whose work is merged — a feature branch
   into `dev` too: its worktree removed, then `git branch -d <name>` (the
   plain `-d` refuses an unmerged branch, which is the one case to stop and
   ask; never `-D` or `--force` on your own). A worktree with uncommitted
   changes is likewise a question, not a deletion. Run this from the main
   checkout, after leaving the worktree session if the work was done in one.

Follow-ups to offer, not to run: `update-server`, `update-demo-server` and
`update-share-host` once the Docker run has published (they deploy that
build to the NAS, the demo and the share host; `update-needed` checks all
deployments); `release` if the merged
work should ship as a new desktop app, Android app, extension and iPad
TestFlight version; `release-ipad` if it should reach TestFlight alone
(that needs its marker in a commit before the merge, so it is a merge of
its own).
