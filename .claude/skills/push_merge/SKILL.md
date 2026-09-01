---
name: push_merge
description: Push the branch's committed work and merge it to main via a PR (leaves uncommitted changes alone unless told to include them).
---

# Push & merge-request

Merge the branch's committed work into `main` via a PR.

**Default scope: existing commits only.** If the working tree has
uncommitted changes, do NOT commit them — they are ongoing work; leave
them in the tree and merge only what is already committed. Only commit
the tree when the user explicitly asked to include the uncommitted
changes in this push.

## Steps

1. **Check the tree**: `git status` + `git diff --stat`. If on `main`,
   create a feature branch first (`git checkout -b <short-topic-name>`);
   otherwise stay on the current branch. Note any uncommitted changes —
   they stay out of the PR (see scope rule above). If there are no
   unpushed/unmerged commits either, stop and tell the user there is
   nothing to merge.

2. **Pre-flight** (skip only if this session already ran them on the
   current tree):

   ```bash
   cd backend && venv/Scripts/python.exe -m pytest tests -q
   # frontend, when src/ changed (node is fnm-managed, not on PATH):
   export PATH="$HOME/AppData/Roaming/fnm/aliases/default:$PATH"
   cd frontend && npm run build
   ```

3. **Commit — only if explicitly asked**: when (and only when) the user
   asked to include the uncommitted changes, `git add` the
   changed/untracked project files (never `backend/users/`, `venv/`,
   `dist/`, scratch files). Commit message follows the repo's style —
   one short descriptive line (see `git log --oneline`), ending with:

   ```
   Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
   ```

   Otherwise skip this step entirely.

4. **Push**: `git push -u origin <branch>`.

5. **PR**: `gh pr create --base main --title "<title>" --body "<summary>"` —
   a few bullet points of what changed and how it was verified.

6. **Merge**: If the PR is clean and ready to merge, merge.

## After the merge

Do NOT merge the PR yourself unless asked. Once the user merges to `main`,
GitHub Actions publishes `ghcr.io/tim4431/gamma`; deploying that to the NAS
is the `update-server` skill — offer it as the follow-up.
