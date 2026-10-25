---
name: triage-issues
description: Work through the repo's open GitHub issues — read each, check it against the code, fix the real bugs on dev with tests, commit, and reply on the issue in the maintainer's voice. Feature requests get an assessment and wait for the developer's yes. Never closes issues, never pushes.
---

# Triage GitHub issues

One pass over the open issues: **read → check → fix → reply**. Arguments
may name issue numbers or an author; with none, take every open issue the
maintainer has not answered yet (`gh repo view --json owner -q .owner.login`
is the maintainer).

**Scope.** A bug that holds up is fixed, committed on `dev` and answered. A
feature request (or a bug that turns out to be one) is assessed and brought
to the developer — built only on their yes. Nothing is pushed, merged,
released or closed here: the replies say the fix comes with the next
release, and the `merge` / `release` skills ship it.

## Steps

1. **Read.** The GitHub MCP server may be down; `gh` always works:

   ```bash
   gh issue list --state open --limit 100 --json number,title,labels,author,createdAt
   gh issue view <n> --json title,body,comments,labels
   ```

   Read the bodies whole: the Diagnostics block's timings and request lines
   often settle the cause (a request logged at 11:34 and a 410 at 13:49 is a
   two-hour expiry). Note the reporter's language and what they asked for.

2. **Check the tree.** `git status --short`, `git log --oneline -3`. The
   developer works in this same checkout and commits and pulls while you
   work: their uncommitted edits are never yours to revert, reformat or
   stage, and HEAD may move under you. Look again before every commit.

3. **Check each issue against the code.** Read the topic doc for the area
   ([developer guide](../../../docs/dev/README.md)), then the code. Decide:
   a bug, a UX bug (the app silently loses the user's work or state), works
   as designed, cannot happen, or a feature request. Cite `file:line` for
   the root cause; a throwaway `GAMMA_DATA_DIR` probe or a scratch test
   beats a guess. When a cause is uncertain, look at it from several sides
   (storage, lifecycle, what a restart changes) instead of settling on the
   first story. Before fixing, have a skeptic try to refute each "real"
   verdict and its fix plan; several issues can be checked in parallel.

4. **Feature requests: assess, then ask.** Say what exists today, whether
   the request is reasonable, what it would take (files, risks, guardrails
   it touches — e.g. [ai_tools.md](../../../docs/dev/ai_tools.md)
   "Guardrails"), and a smaller alternative if there is one. Stop there
   until the developer decides; their product calls go into the build.

5. **Fix the bugs.** The smallest change that fully resolves the
   complaint, by the [shared rules](../../../docs/dev/README.md#architecture-and-shared-rules):
   existing helpers, `t()`/`T()` strings with the zh catalog translated
   (`npm run i18n -- --sync`, then `npm run i18n` at 0 problems), the topic
   docs updated in their own plain style, a regression test (a backend
   test, and a browser step in `frontend/tests/e2e/scenarios/` for UI
   behaviour). Fixes touching the same files are one piece of work. A survey
   that informed the fix (another project's behaviour, a protocol's rules)
   becomes a [research note](../../../docs/research/README.md).

6. **Verify.** Unit tests by the
   [test policy](../../../docs/dev/debugging.md#local-changes-test-the-affected-modules),
   then the `verify` skill: one `npm run build` after the last frontend
   edit — **say first that it empties `frontend/dist`, so the developer's
   9001 page answers 500 until it finishes** — and the affected browser
   groups in the harness (`npm run e2e -- --group <g> --jobs 3 --continue`;
   never ports 9001, 5173 or 9002). A failure that also fails on a clean
   checkout is pre-existing: confirm it and report it, don't hide it.

7. **Commit on `dev`,** one commit per issue (or per group of issues fixed
   together), staging only that fix's paths — never `git add -A`. The
   message is one long sentence in the repo's style (`git log`), naming the
   issue as `(#N)`; no `Fixes #N` keyword unless the developer wants the
   issue closed on merge. When two fixes share a file, stage one fix's
   hunks from a `git diff` patch **with context** (a `-U0` patch applied with
   `--unidiff-zero` inserts at the wrong line), written with **LF** line
   endings (Python on Windows writes CRLF unless told otherwise), through
   `git apply --cached`; then read `git diff --cached` before committing.

8. **Reply** on each issue with `gh issue comment <n> --body-file -`:
   - In the maintainer's own voice. Never mention an AI, an assistant, an
     agent, Claude or automation.
   - In the language the issue was written in. Short: thanks, what was
     wrong in one or two plain sentences (the user's terms, not internals),
     what changed, "for the next release".
   - When the cause could not be proven, ask for the one thing that would
     settle it (a log line and where to find it, the deployment, a proxy).
   - A feature request is answered only once the developer has decided:
     built, planned, or why not, with the alternative.
   - Do not close the issue.

9. **Report** to the developer: per issue, the verdict, the cause with
   `file:line`, the fix and its commit, the checks run with results, what
   was left out and why, and anything found on the way that deserves its
   own issue. Say what is committed but not pushed.
