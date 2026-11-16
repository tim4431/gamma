---
name: branch-review
description: Review a feature branch before it merges into dev on four questions — does it run (built, tested and driven here, not assumed), is the implementation elegant, are its tests worth keeping (none that cannot fail, no change detectors), and does it reuse the repo's existing infrastructure and widgets — and report each finding as file:line with its fix. Runs the branch's pre-dev checks itself. Never merges, never pushes.
---

# Review of a branch before dev

The gate between a feature branch and `dev` ([workflow.md](../../../docs/dev/workflow.md)).
It is not `cleanup`, which passes over what already landed, and not a bug
hunt alone (`/code-review`). It answers four questions about the branch
and lists what to change before it merges.

Argument: a branch, a worktree path, or nothing for the current branch.
The range is `git merge-base dev <branch>..<branch>`, so commits that
reached `dev` meanwhile stay out. On `dev` itself there is no branch to
review: say so and point to `cleanup`. Uncommitted work is not reviewed;
name it when `git status --short` shows any.

```bash
base=$(git merge-base dev <branch>)
git log --format='%h %s' --reverse $base..<branch>
git diff --stat $base..<branch>
git diff $base..<branch>
```

## Who reads

An author reads their own diff through what they meant it to do. So when
this skill runs in the session that wrote the branch, questions 2 to 4 go
to one fresh `general-purpose` agent, on the session's model. It gets the
branch, `base`, the stat, the path of this file to read first, and the
topic docs of the areas, and nothing of the author's reasoning. The
session runs question 1 in the meantime and merges the two into one
report. In a fresh session, one reader does all four.

## 1. Does it run

Run it here; what the author reported does not count. On the branch's last
commit, in its worktree:

- **Backend:** the full suite once, from `backend/`:
  `venv/bin/python -m pytest tests -q -n auto --dist loadfile`
  (`venv/Scripts/python.exe` on Windows; about 4 minutes). This is the once-per-branch run that
  [debugging.md](../../../docs/dev/debugging.md#local-changes-test-the-affected-modules)
  asks for before `dev`, so it is not repeated after the review unless a
  fix touches the backend. A worktree without its own venv uses the main
  checkout's (`git worktree list | head -1`).
- **Frontend:** `npm run lint && npm test` from `frontend/`, and
  `node --test tests/appState.test.mjs` when `app/App.jsx` changed.
- **UI behavior:** `npm run build`, then `npm run e2e -- --changed dev`.
  In a worktree the build is its own. In the main checkout `npm run build`
  empties the `frontend/dist` that the developer's 9001 server serves, so
  say so before it runs. Check that the selected steps include one that
  exercises each new user-visible behavior. A behavior that no step
  reaches is a finding: add the step, or drive it once with the `verify`
  skill and say so.
- **Docs:** `node tools/check_doc_links.mjs <the docs the branch changed>`
  from the repository root.
- **Reachability:** an endpoint no client calls
  (`grep -rn "/api/<path>" frontend/src extension desktop`), a component
  nothing mounts, a preference with no reader, a string outside the i18n
  catalogs (`node tools/i18n-audit.mjs` lists candidates), a migration
  step missing from `WORKSPACE_STEPS`.

Record each command with its result for the report, verbatim on failure.

## 2. Is it elegant

The smallest shape that does the job and fits the architecture of
[docs/dev/README.md](../../../docs/dev/README.md#architecture-and-shared-rules):

- The feature lives in an owner module beside its kind (the
  [source map](../../../frontend/src/README.md)), not spread across
  `App.jsx` state, a component and a util. New state in `App.jsx` is a
  finding ([frontend-refactor.md](../../../docs/dev/frontend-refactor.md#new-state)).
- No over-engineering: an abstraction with one caller, an option nothing
  sets, a new env var where a setting or a default does, a defensive
  branch for a state the caller makes impossible, a cache with no
  measured reason, a wrapper that only forwards.
- No duplicated logic: the same rule in two components, or in the backend
  and the frontend without a `tests/shared/` case holding them together.
- Nothing left from what the branch replaced: when the new code takes over
  from an old path, the old component, CSS, i18n keys, endpoint, tests and
  doc section are gone in the same branch.
- No hard-coded colours, sizes, radii, shadows, z-indexes or durations
  where a token exists ([ui-design.md](../../../docs/dev/ui-design.md#tokens)),
  in stylesheets or inline styles. A value that is data (an ink or
  highlight palette, the PDF page's own colours) carries a `ds-allow`
  reason.
- Names say what a thing is in the words the docs use. Comments say why,
  not what.

## 3. Are the tests worth keeping

Read every test the branch adds or changes against
[What a test is for](../../../docs/dev/debugging.md#what-a-test-is-for).
For each one, ask which plausible bug would make it fail.

- **Cut:** tests that cannot fail, mirrors of the implementation, change
  detectors (exact wording, a CSS value, a source grep, a whole snapshot),
  a behavior already covered at a cheaper layer, the framework's own
  behavior. Name each with the kind.
- **Missing:** a user-visible behavior of the branch with no test, a hard
  case (a merge, a migration, an authorization edge, a race) with none,
  or a bug fix without a test that fails before it.
- **Fragile:** a shared account or data between files, a fixed sleep, a
  wait on an earlier state than the next line needs, a module global left
  set ([Order and timing](../../../docs/dev/debugging.md#order-and-timing)).
  These are what turn red on the merge to `main`.
- **Misplaced:** pure logic checked in a browser step, which belongs in a
  unit test; a browser step that re-checks what the API test does.

## 4. Does it reuse what exists

For each new helper, component, class, endpoint, table or test fixture,
search for the one the repository already has before accepting it
(`grep -rn "def <name>\|function <name>\|const <name>"`, the topic doc of
the area). The usual ones:

| Need | Existing |
|---|---|
| Order among siblings, timestamps | `generate_key_between` / `generateKeyBetween`, `page_now()` |
| Edit a page, make one, trash one | `apply_ops` / `commit_ops`, `blocks_store.create_page`, the trash helpers ([collab.md](../../../docs/dev/collab.md)) |
| Who may do it | `require_user`, `require_ws`, `require_ws_writer`, `require_commenter`, `require_admin` in `gamma/auth.py` |
| A database, a stored-shape change | `db.connect_*`, a numbered step in `gamma/migrations.py` |
| Long work with progress | `gamma/jobs.py` and the tasks tray ([tasks.md](../../../docs/dev/tasks.md#adding-a-kind)) |
| An AI call | `ai_runtime(user)`, `gamma/ai_protocols/` |
| A button, a close, a menu, a choice | `uiBtn`, `uiClose`, `ctlBtn`, `MenuButton`, `MenuSelect`, `ActionMenu`, `Menus.jsx` |
| A settings pane | `settings/SettingsKit.jsx` (`Section`, `Row`, `Toggle`, `Segmented`, `Field`, `SubDialog`…), the preference registry (`app/prefDefs.js`, [settings.md](../../../docs/dev/settings.md)) |
| A dialog, a popover | `DialogOverlay`, `Popover` (`shared/ui/Surfaces.jsx`), `confirmBox` ([ui-design.md](../../../docs/dev/ui-design.md#dialogs)) |
| A press, a drag, a device question | `shared/ui/press.js`, `shared/ui/useDragOrder.js`, `shared/lib/pointer.js` |
| Pick or save a file | `shared/lib/pickFiles.js`, `shared/lib/saveFile.js` |
| A command or a shortcut | the command catalogs ([hotkeys.md](../../../docs/dev/hotkeys.md)) |
| Text the user reads | `t` / `tn` / `T` and the catalogs ([i18n.md](../../../docs/dev/i18n.md)) |
| Motion | `shared/ui/motion.js`, `ListMotion`, `MotionWidgets` ([ui-design.md](../../../docs/dev/ui-design.md#motion)) |
| Test accounts and clients | conftest's `client`, `anon`, `fresh_client()`, `make_user` |
| A browser step | the harness: `Account.api()`, `makePdf`, `openPage`, `until()`, `assertNoProblems` |

A bespoke version of any of these is a finding unless the branch says why
the shared one cannot serve. The fix is to use the shared one, or to
extend it in place. A parallel one is never the fix.

## 5. Report

One message:

1. **Verdict:** "ready for `dev`" or "needs changes", with the counts.
2. **Runs:** each command and its result; the behaviors no step reached.
3. **Findings** under the four questions, most severe first, each
   `file:line — what — fix`. A finding that needs a product decision says
   so instead of a fix.
4. **Tests:** to cut (with the kind), missing, fragile, misplaced.

Nothing that was fine, and no praise. When this runs in the session that
wrote the branch, fix the findings in one batch, then rerun only the
checks the fixes touch. The merge into `dev` is the user's call.
