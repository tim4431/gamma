---
name: cleanup
description: Clean up the code and docs committed since the last "Cleanup of …" commit — unify scattered logic, conform UI to the shared design, cut over-engineering and dead code, plain-prose the docs, list what was left unfinished, and remove the worktrees of merged branches. Behavior-preserving; commits each topic as "Cleanup of …", never pushes.
---

# Cleanup of recent commits

A cleaning pass over work that already landed on the branch. The output is
a tidier tree, one `Cleanup of …` commit per topic, and a short report. It
is NOT a bug hunt (`/code-review`) and NOT a restyle: the goal is that the
new code looks like it was written by the same careful person who wrote the
rest of the repo.

Rules that hold throughout:

- **Behavior-preserving.** Refactor, dedupe, delete dead things, reword
  docs. Anything that changes what the user sees or what an endpoint
  returns is a *proposal* in the report, not an edit.
- **Commit as you clean.** Each topic is committed when it is clean and
  its checks pass (Step 4), under a subject that starts with `Cleanup of …`.
  Running this skill is the request to commit. The newest such commit is
  where the next pass starts, so a pass never leaves cleaned work
  uncommitted. Never push, never amend, never commit anything but the
  pass's own edits.
- **Don't touch unrelated dirty files.** Other sessions may be editing the
  same tree. Only edit files that the scoped commits touched, plus files a
  fix genuinely has to reach (a shared helper, a doc). A file that was
  dirty before the pass began is someone else's: leave it, and name it in
  the report if a fix needed it.
- **Conform, don't invent.** When code needs a control, class, helper, or
  doc section, use the one the repo already has. Never introduce a new
  style or abstraction to fix an old one.

## Step 1 — scope

The scope is every commit since the last cleanup pass: from the newest
commit whose subject starts with `Cleanup` to `HEAD`. Not `main..HEAD`:
`dev` is long-lived, and what an earlier pass read stays read. Never narrow
the range on your own: a large one is split into topics (Step 1b), not cut
down. Before reading anything, tell the user the range in one line — first
and last commit, the number of commits, files and lines, and the topics it
holds — and name anything you leave out and why. A range the user did not
ask for is the most common way this skill fails.

Argument forms (`/cleanup <arg>`):

| arg          | scope                                              |
|--------------|----------------------------------------------------|
| *(none)*     | `<last Cleanup commit>..HEAD`. With no such commit in the history, the last 5 commits |
| `N`          | the last N commits (`HEAD~N..HEAD`)                |
| `<a>..<b>`   | that range                                         |
| `tree`       | uncommitted changes only (`git diff HEAD`); cleaned in place and not committed, since the work under them is not |
| other words  | a description, not a range: the default range, with the words as emphasis (`/cleanup the import dialogs`) |

```bash
git status --short                                   # what is dirty before the pass, and whose
base=$(git log -1 -i --grep='^Cleanup' --format=%H)  # check its subject is the match, not a body line
git rev-list --count $base..HEAD
git diff --shortstat $base..HEAD
git log --first-parent --format='%h %s' --reverse $base..HEAD   # one line per feature or direct commit
git diff --shortstat <merge>^1 <merge>                          # a merge's size
```

Fix `base` once, at the start. The pass's own `Cleanup of …` commits land
after it and are not part of the range.

```bash
git diff --stat <range>
git diff <range>                       # read it; it is the input
```

Read a topic's diff before judging anything in it. Source and `docs/dev`
are read in full. Tests, fixtures, research notes and generated files are
read for what the checks ask of them (a copied setup, a dead helper, a test
of nothing), not line by line. Note every file touched and which
`docs/dev/*.md` covers each area (the topic table is in
`docs/dev/README.md`); read those docs too — they define what "the existing
way" is.

### Step 1b — topics, and who cleans them

Split the range into topics, by feature and not by folder. A topic is one
thing that was built: a merged branch with the direct commits that followed
it up, or a run of direct commits about one feature. A merge's diff is
`<merge>^1 <merge>`. Small commits of one area are grouped; a 7,000-line
merge stands alone. Each topic is cleaned, checked and committed on its
own, so a pass that is stopped halfway has still landed what it finished.

A range of one or two topics, or of less than about 8,000 changed lines,
is the coordinator's alone. A larger one may have subagents:

- **Two, usually.** Not two at a time with more to follow: two for the
  pass. Agents drain the user's quota fast, and a third is for a range
  that two lanes and the coordinator's own plainly cannot hold. Never
  more than three.
- **One lane each.** A lane is a run of related topics that one cleaner
  takes in order, the biggest first, with a commit after each. The
  coordinator takes a lane of its own, so three cleaners cover the range.
  Topics that share files go in the same lane.
- **The coordinator's model and effort.** Start each as a fork
  (`subagent_type: "fork"`), which runs on the coordinator's model at its
  effort, right after the range is stated and before the coordinator has
  read any diff, so the context it carries is this file and the scope.
  Where forks are not available, a general-purpose agent told to read this
  file first, with the session's model and effort passed explicitly.

Give each agent:

- `base`, its topics in order with their commits or merge ranges, and the
  files they touch (`git diff --stat`), which are the files it owns;
- the `docs/dev/*.md` of its areas;
- the five checks, Step 3 and Step 4 as its brief, with the rule that it
  edits and commits only its own files. Shared files — `App.jsx`,
  `app.css`, the i18n catalogs, `docs/dev/README.md`,
  `frontend/src/README.md`, `docs/dev/api.md`, `settings.md` — belong to no
  one: a cleaner edits only the hunks of its own topics in them, with the
  Edit tool, never a rewrite;
- the report shape of Step 6, per topic, with each commit's hash, so the
  reports merge.

The coordinator merges the reports, runs the closing checks of Step 4 once
over the whole tree, and writes one report whose Scope section lists every
topic with its range and its commit. A topic no one finished is listed
under "Left out" with a range that `/cleanup <a>..<b>` takes, never
dropped quietly: the next default pass starts after this one's commits and
will not come back for it.

## Step 2 — the five checks

Work through all five and collect findings first. Then fix in Step 3.
Each finding is a line: `file:line — what — fix / proposal`.

### 1. Scattered or duplicated logic

- The same computation or rule in two places (two components, backend +
  frontend, two routers). Move it into one helper and call it from both.
  Exception: mirrors the docs declare on purpose (`textnorm.py` ↔
  `shared/lib/textnorm.js`, `text_box.py` ↔ `markup/textBox.js`) — those
  stay mirrored but must actually match.
- A feature spread across App.jsx state + a component + a util when one
  module would own it whole. Prefer the module the file map in
  `frontend/src/README.md` already assigns to that concern.
- New state in `app/App.jsx` from the scoped commits, even when
  `frontend/tests/appState.test.mjs` passed. In
  `frontend/tests/app-state-baseline.json`, a raised ceiling, or a raised
  count without a hook newly counted in `frontend/tools/appState.mjs`. A
  shared hook put into the tool's `INFO` that holds a value for its caller.
  The state belongs in an owner
  ([frontend-refactor.md](../../../docs/dev/frontend-refactor.md#new-state)).
- New helpers that duplicate an existing one under another name
  (`grep -rn "def <name>\|function <name>\|const <name>"` before keeping
  a new one).
- Copy-pasted branches that differ by one value — parameterize.

### 2. UI cleanliness

Reference: [docs/dev/ui-design.md](../../../docs/dev/ui-design.md) and
[docs/dev/settings.md](../../../docs/dev/settings.md).

- Controls use the unified classes (`uiBtn`, `uiClose`, `ctlBtn`,
  `aiKeyInput`, …) and settings panes use only `settingsKit` primitives
  with `MenuSelect`/`ActionMenu` for choices. A bespoke `<button style=…>`
  or a one-off `.myThingBtn` styled like a button is a finding.
- Bespoke CSS classes do layout only. Colors, radii, fonts, shadows come
  from the theme variables; a hard-coded `#hex` or `rgba(` in new CSS is a
  finding unless it is a documented exception.
- Dead CSS: for every selector added in the diff, grep `frontend/src` for
  the class name. No user → delete.
- Dead logic: state that is set but never read, props passed but unused,
  handlers wired to nothing, `useEffect`s whose deps can never change.
- Inline styles that repeat an existing class; `!important`; z-index
  guesses; duplicated media queries.
- Consistency with neighbors: same spacing scale, same icon set, same hover
  affordance pattern as the surrounding component (e.g. mdTools hover
  toolbars, menu rows). "Looks different from the panel next to it" is a
  finding even if it works.
- Do NOT restyle existing panels to a new taste. Cleaning means making the
  new part match the old parts, never the reverse.

### 3. Over-engineering

- Abstractions with one caller (a factory, registry, or base class used
  once). Inline it.
- Options, flags, or config knobs nothing sets. Remove the knob and the
  branch. Repo rule: no new env vars for things a GUI setting or a
  hardcoded default covers.
- Defensive branches for states the types or the caller make impossible.
- Generic utilities built for hypothetical reuse; premature caching or
  memoization without a measured reason; a new dependency where 20 lines
  would do.
- Indirection layers (wrapper → wrapper → impl) that exist only so the
  code "could" be swapped later.
- Backend: new tables or columns nothing reads; endpoints with no frontend
  caller (check `grep -rn "/api/<path>" frontend/src extension`).

### 4. Documentation prose

Applies to the changed lines of `docs/dev/*.md`, `README` and
`desktop/docs/*`. The docs are reference material for someone who reads them
once and then works; they are not a changelog and not marketing.

Remove or rewrite:

- Praise and filler adjectives: *robust, seamless, elegant, powerful,
  clean, simple, comprehensive, carefully, gracefully, intuitive*.
- Narration of the writing process: "we then decided…", "this was tricky
  because…", "as before", "now", "previously", "no longer". State what IS.
  History belongs in git.
- Throat-clearing: "Note that", "It is worth mentioning", "Importantly",
  "In order to", "Basically", "Simply".
- Hedged or doubled statements ("essentially the same, more or less").
- Sentences over ~25 words; chains joined by em-dashes or semicolons.
  Split them.
- Bullets that are paragraphs. One or two sentences per bullet.
- Repetition of what the code or a neighbouring doc already says nearby.
- Headings that announce sections nobody needs ("Overview", "Summary",
  "Conclusion").

Keep: exact names (files, functions, flags, endpoints), the reason behind
a non-obvious decision (one sentence), invariants, gotchas.

Also check the docs are TRUE after the commits: every file, function,
endpoint, and setting a doc names must exist (`grep`), and every new
endpoint, setting, tool, or file the diff adds must appear in the doc that
covers its area (api.md, settings.md, ai_tools.md, ui-design.md's file
map, …).

### 5. Unfinished work

Signals, in rough order of reliability:

- `TODO`, `FIXME`, `XXX`, `HACK`, `WIP`, `later`, `for now` added by the
  diff (`git diff <range> | grep -n "^+.*\(TODO\|FIXME\|XXX\|HACK\|WIP\|for now\)"`).
- Commit messages with "wip", "part 1", "start", "draft", "todo".
- Commented-out code; `console.log`/`print()` debugging (`logbuf.log` is
  the only backend log surface).
- Stubs: functions that `pass`/`return null`/throw `NotImplemented`;
  handlers that only `preventDefault`; empty `catch`.
- One half of a pair: backend endpoint without a caller, frontend call
  without a route, a setting stored but never read, a pref key in
  `app/prefs.js` with no UI, a migration without a reader.
- Docs describing behavior the code doesn't have yet, or code the docs
  don't mention.
- Tests: a backend change under `gamma/routers` or `gamma/*.py` with no
  test touched — note it (adding a test is in scope when cheap).
- Keyboard shortcuts, menu entries, or settings mentioned in the UI text
  that do nothing.

## Step 3 — apply

Fix everything from checks 1–5 that is behavior-preserving and local, one
topic at a time. Order: delete dead things → dedupe → simplify → conform
UI → rewrite docs.

- Keep each edit minimal. A cleanup that rewrites a file wholesale is
  itself a finding.
- When you move logic, update every caller and the doc that names its
  location.
- When you delete a helper, class, endpoint, or setting, grep once more
  for its name across `backend/`, `frontend/src`, `extension/`,
  `desktop/`, and `docs/`.
- Update `docs/dev/*.md` for every structural change you make (the docs
  must describe the tree as it is after the cleanup).

Do NOT apply: anything that changes visible behavior, removes a feature,
changes an API shape, or that you are unsure is dead. Those go in the
report as proposals with a one-line reason.

## Step 4 — verify, then commit the topic

Once per topic, after its last edit, the checks of what it touched:

```bash
cd backend && venv/Scripts/python.exe -m pytest tests/<the affected modules> -q
# when frontend/src changed (node is fnm-managed, not on the tool-shell PATH):
eval "$(fnm env --shell bash)"
cd frontend && npm run lint && node --test tests/<the affected>.test.mjs
# when app/App.jsx changed (a lower count: node tools/appState.mjs --baseline):
cd frontend && node --test tests/appState.test.mjs
```

Two vector-math tests fail under the conda `python`; use the venv one.
Pre-existing failures that the scoped commits didn't cause are reported,
not fixed. For UI changes beyond class swaps, run the `verify` skill and
look at the result. `npm run build` is the coordinator's, at the end:
cleaners in one tree would build over each other.

Then commit the topic, by path, so nothing another cleaner or another
session left in the tree goes with it:

```bash
git status --short                      # yours, and nothing you did not edit
git commit -m "Cleanup of <the topic>: <what changed>" -- <the files you edited>
```

- The subject starts with `Cleanup of`, names the topic, then says what
  changed, in the prose of the log's other subjects. Read the last two
  with `git log -2 -i --grep='^Cleanup' --format=%s`.
- Never `git add -A`, `git commit -a` or `git stash`. A new file is added
  by name (`git add <file>`) before the commit.
- `index.lock` exists: another cleaner is committing. Wait and try again.
- A shared file that holds another cleaner's uncommitted hunks
  (`git diff <file>`) stays out of the commit. Name it in the report, and
  the coordinator commits it at the end.
- A topic with nothing to clean gets no commit. The report says it was
  read and found clean.

The coordinator's closing checks, once, after every lane has reported:

```bash
git status --short                      # nothing of the pass left uncommitted
eval "$(fnm env --shell bash)"
cd frontend && npm run lint && npx vite build --outDir <scratchpad>/cleanup-dist --emptyOutDir
cd backend && venv/Scripts/python.exe -m pytest tests/<modules of files two lanes both reached> -q
```

The build goes to a scratch directory: `frontend/dist` is what the
developer's own server on port 9001 serves, and `npm run build` empties it.
What the closing checks turn up is fixed and committed as one more
`Cleanup of …`, with the shared files the lanes left.

## Step 5 — worktrees

Merged branches leave their worktrees behind, so a pass also goes through
`git worktree list`, from the main checkout, after the closing checks.
For each worktree but the main one:

```bash
git -C <path> status --porcelain                    # uncommitted work
git merge-base --is-ancestor <its HEAD> dev && echo merged
git rev-list --count dev..<its HEAD>                # commits dev does not have
git cherry dev <its HEAD>                           # "-": dev has it under another hash
git -C <path> log -1 --format=%cr                   # how long it has been still
```

- **Merged into `dev` and clean**: remove it without asking, as CLAUDE.md
  says. `git worktree remove <path>`, then `git branch -d <branch>` when it
  has one; a detached HEAD has none.
- **Uncommitted files, or commits `dev` does not have**: a question, never
  a deletion, even when `git cherry` shows every commit as already in
  `dev`. List it in the report with its branch, commits ahead, dirty count
  and age, so the user can answer it in a line.
- **Its last commit minutes old**: left for the next pass. A session may
  still be working in a worktree whose branch it has just merged.
- `git worktree prune` drops the entries of worktrees whose directory is
  gone.

Never `git branch -D`, `git worktree remove --force` or `rm -rf` on a
worktree without the user's yes.

## Step 6 — report

One message, structured as:

1. **Scope**: the range, then each topic with its commits and the hash of
   its `Cleanup of …` commit (or "found clean"), and what was left out and
   why (also when nothing was), as ranges `/cleanup` takes.
2. **Applied**: bullets grouped by check (1–5), each `file — what changed`.
3. **Proposals**: behavior-affecting or uncertain items, each with the
   one-line reason it was not applied.
4. **Unfinished**: the check-5 list, as concrete next tasks.
5. **Verification**: test and build results, verbatim on failure.
6. **Worktrees**: the ones removed, and the ones left, each with why.

Do not push. Do not pad the report with things that were already fine.
