---
name: merge
description: Merge the branch's committed work into main via a PR and report the Docker publish the merge started. Never commits, never releases the desktop app or extension — that is the `release` skill.
---

# Merge to main

One job: get what is already committed on the branch into `main`. The
`check` workflow validates the PR (and `cloud` when `cloud/` changed,
`site` when `sites/` or its inputs changed; `check` is skipped for a PR
that only touches those); the merge itself triggers `docker.yml`
(`ghcr.io/tim4431/gamma:latest`; skipped for a merge that only touches
`cloud/` or `sites/`) and, when the site's inputs changed, `site.yml`,
which redeploys gammapdf.com from `main`. The desktop app and the browser
extension are NOT released by a merge — run the `release` skill for that
(it dispatches `desktop.yml` on `main`, which releases both). The iPad app
goes to TestFlight only when a merged commit's message carries a fresh
`ipad-test-release-NNN` marker (the `release-ipad` skill); a plain merge
releases nothing there either. The account
server and the website do not need a merge at all: `update-account-server`
/ `build-site` publish them from the branch. Details:
`docs/dev/github_actions.md`.

**Scope: existing commits only.** Uncommitted changes are ongoing work —
leave them in the tree, never commit them here. If there is nothing
committed beyond `origin/main`, stop and say so.

## Steps

1. **Check**: `git status --short`, `git fetch -q origin`,
   `git log --oneline origin/main..HEAD`. On `main` itself: create a branch
   first (`git checkout -b <short-topic>`); the normal case is `dev`. No
   commits ahead of main → "nothing to merge", stop.

2. **Push**: `git push -u origin <branch>`.

3. **PR**: `gh pr create --base main --head <branch> --title "<title>" --body "<bullets>"`
   — title from the commit subjects, body a few bullets of what changed.
   If a PR for the branch is already open, reuse it.

4. **Wait for the check**: `gh pr checks <n> --watch --fail-fast`
   (`check`: backend pytest, frontend unit tests + build, the browser suite
   in two shards, extension zip; ~5 min, skipped for a PR that only
   touches the account server or the website; `cloud`: the account server's
   tests, when `cloud/` changed). Red → report
   the failing job (`gh run view <id> --log-failed`) and stop; fixing is
   normal work on the branch, then re-run this skill. Do not merge over a
   red check.

5. **Merge**: `gh pr merge <n> --merge`. First, the data format: the
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

6. **Report**: a few seconds after the merge,
   `gh run list --limit 5 --json workflowName,status,event,url,databaseId`
   shows the Docker run the merge started (none for an account-server-only
   merge — say so). Report it with its link. Do not
   wait for it unless asked (`gh run watch <id> --exit-status`).

7. **Clean up the worktree.** Once a branch's work is merged — here into
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
work should ship as a new desktop app or extension version; `release-ipad`
if it should reach TestFlight (that needs its marker in a commit before
the merge, so it is a merge of its own).
