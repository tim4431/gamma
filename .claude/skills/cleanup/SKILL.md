---
name: cleanup
description: Clean up the code and docs committed since the last "Cleanup of …" commit — unify scattered logic, delete what new work superseded (old implementations, their CSS, keys, endpoints, tests and doc sections), replace hard-coded colours and sizes with tokens, cut over-engineering, prune tests that cannot fail or only detect change, plain-prose the docs, list what was left unfinished, and remove the worktrees of merged branches. `cleanup sweep <area>` runs the code checks over an area's existing code instead. Behavior-preserving; commits each topic, never pushes.
---

# Cleanup of recent commits

A cleaning pass over work that already landed on the branch. The output is
a smaller, tidier tree, one `Cleanup of …` commit per topic, and a short
report. It is NOT a bug hunt (`/code-review`) and NOT a restyle: the goal
is that the new code looks like it was written by the same careful person
who wrote the rest of the repo, and that nothing it replaced is left
behind.

Expect to delete. A feature that lands usually supersedes something (an
old component, a helper, a fallback, a setting, the tests and the doc
section of the old way), and the pass that leaves those in place has not
done its job. A pass whose diff is mostly additions or rewording should
look again.

Rules that hold throughout:

- **Behavior-preserving.** What the user sees and what an endpoint returns
  stay the same. Within that, be bold: deleting unreachable code, a
  superseded implementation, a setting nothing reads, a test that cannot
  fail, or a shim for a shape no client still sends is behavior-preserving.
  Do it; do not propose it. Only a change the user would notice goes in
  the report as a proposal.
- **Settle doubt, don't defer it.** "Maybe still used" is a question with
  an answer: grep every name across `backend/`, `frontend/src`,
  `extension/`, `desktop/`, `ipad/`, `android/`, `cloud/`, `tests/` and
  `docs/`, and `git log -S'<name>'` for when its last caller went. Dead on
  that evidence → delete. Still in doubt after it → a proposal, with what
  the search found.
- **Commit as you clean.** Each topic is committed when it is clean and
  its checks pass (Step 4), under a subject that starts with `Cleanup of …`.
  Running this skill is the request to commit. The newest such commit is
  where the next pass starts, so a pass never leaves cleaned work
  uncommitted. Never push, never amend, never commit anything but the
  pass's own edits.
- **Don't touch unrelated dirty files.** Other sessions may be editing the
  same tree. Edit the files that the scoped commits touched, the files
  holding what they superseded, and files a fix genuinely has to reach (a
  shared helper, a doc). A file that was dirty before the pass began is
  someone else's: leave it, and name it in the report if a fix needed it.
- **Conform, don't invent.** When code needs a control, class, token,
  helper, or doc section, use the one the repo already has. Never
  introduce a new style or abstraction to fix an old one.

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
| `sweep [area]` | no range: the existing code and tests of one area ([Sweep](#sweep)) |
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
- the seven checks, Step 3 and Step 4 as its brief, with the rule that it
  edits and commits only its own files. Shared files — `App.jsx`,
  `app.css`, the i18n catalogs, `docs/dev/README.md`,
  `frontend/src/README.md`, `docs/dev/api.md`, `settings.md`, the ratchet
  baselines — belong to no one: a cleaner edits only the hunks of its own
  topics in them, with the Edit tool, never a rewrite;
- the report shape of Step 6, per topic, with each commit's hash, so the
  reports merge.

The coordinator merges the reports, runs the closing checks of Step 4 once
over the whole tree, and writes one report whose Scope section lists every
topic with its range and its commit. A topic no one finished is listed
under "Left out" with a range that `/cleanup <a>..<b>` takes, never
dropped quietly: the next default pass starts after this one's commits and
will not come back for it.

### Sweep

`/cleanup sweep <area>` cleans code that no recent range covers: an area's
existing source and its tests, about 5,000 lines of source a run. An area
is a folder of `frontend/src/` or a backend area as the topic table of
`docs/dev/README.md` names it (`gamma/routers/` with the modules a topic
doc lists). With no area given, take the one swept longest ago, which the
commit subjects record:

```bash
git log -1 --format=%cs -F --grep='Sweep of <area>:' HEAD   # empty: never swept
```

A sweep runs checks 1 to 5 and 7 over the area and updates the docs that
name what it removed. The docs' own prose and truth are `audit-docs`'
patrol. Its commits are `Sweep of <area>: <what changed>`, never
`Cleanup of …`: a sweep commit must not move the base of the next default
pass, which would then skip the range commits it never read. A sweep with
nothing to remove gets `git commit --allow-empty -m "Sweep of <area>: found clean"`,
so the next one moves on. Subagents follow Step 1b: one area per lane.

## Step 2 — the seven checks

Work through all seven and collect findings first. Then fix in Step 3.
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

### 2. Superseded code

What the new work replaced, still in the tree. Look past the diff: the
leftovers are in files the range did not touch.

- **Names the range deleted.** The diff's removed lines name functions,
  components, CSS classes, routes, preference keys, i18n keys and test
  helpers. Grep each one that no added line brings back. A definition
  whose last user went (a class whose component is gone, a catalog key
  nothing calls, a helper only the deleted code used, a test of the
  removed path) is deleted.
- **The old way beside the new.** A commit that "replaces", "moves",
  "switches to", "instead of" or "now" leaves an old path: the previous
  component, its stylesheet, its endpoint and tests, the preference that
  chose between the two, the docs section. Find it by the old names (the
  commit message, the removed lines, `git log -S`) and delete all of it,
  not only the call.
- **Compatibility code.** Grep the area for `legacy`, `old`, `compat`,
  `fallback`, `deprecated`, `v1`, `migrate`, `still`, `pre-`: shims that
  read an older shape, try a second way when the first fails, or keep an
  old name working. Each one needs a living reason: a released client that
  still sends the old shape (the desktop, iPad and Android apps, the
  extension, a mirror's data format: [mirror.md](../../../docs/dev/mirror.md)
  "The data format"), or stored data no migration converts. Without one,
  delete it. With one, a comment names the reason.
- **Settled choices.** A preference, a flag or an env var whose every
  path but one is gone, or whose default no one changes: keep the chosen
  path, delete the switch and its UI.

### 3. UI cleanliness and hard-coded values

Reference: [docs/dev/ui-design.md](../../../docs/dev/ui-design.md) and
[docs/dev/settings.md](../../../docs/dev/settings.md).

- Controls use the unified classes (`uiBtn`, `uiClose`, `ctlBtn`,
  `aiKeyInput`, …) and settings panes use only `settingsKit` primitives
  with `MenuSelect`/`ActionMenu` for choices. A bespoke `<button style=…>`
  or a one-off `.myThingBtn` styled like a button is a finding.
- **Raw values in stylesheets.** Colours, font sizes, radii, shadows,
  z-indexes, durations, easings and font families come from the tokens
  ([Tokens](../../../docs/dev/ui-design.md#tokens)).
  `node tools/designTokens.mjs --list <kind>` (from `frontend/`) lists
  every offender of a kind; take the ones in the scope's files, replace
  each with its token, and record the lower floor with
  `node tools/designTokens.mjs --baseline`. A value that is raw on purpose
  carries a `ds-allow` reason.
- **Raw values in scripts.** The scanner reads only stylesheets, so grep
  the scope's `.js`/`.jsx` files:
  `grep -nE "#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(" <files>` and every
  `style={{…}}` with a number. An inline colour, size, gap, radius or
  shadow becomes a class or `var(--…)`. The exceptions are data rather
  than design: the ink and highlight palettes, the PDF page's own colours,
  the collaborator colours, a size computed from the page. The same
  number written in two places (a width in CSS and in a measuring
  script, a timeout in two modules) becomes one named constant or token
  beside its owner.
- `node tools/uiConventions.mjs --list` (from `frontend/`): the breaks of
  the shared conventions in the scope's files are fixed and the baseline
  lowered.
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

### 4. Over-engineering

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

### 5. Tests that do not earn their place

Reference: [What a test is for](../../../docs/dev/debugging.md#what-a-test-is-for).
For each test the scope added or changed (in a sweep, each test of the
area), ask which plausible bug in Gamma would make it fail.

- **Delete**: tests that cannot fail (a constant against its literal, a
  mock against what it was told, an import, a default nothing reads),
  mirrors of the implementation (the same expression, internal call
  sequences through mocks), change detectors (exact wording, a CSS value,
  a grep of a source file, a whole snapshot), a behavior already covered
  at a cheaper layer, the framework's own behavior.
- **Merge**: near-identical tests that differ by one input become one
  parametrized test (`pytest.mark.parametrize`, a cases array).
- **Move**: pure logic checked through a browser step goes to a unit
  test; the step keeps what only the browser shows.
- **Keep**: what the user meets, the hard cases, regression tests. A test
  that came with a fix (`git log -S'<test name>' --format='%h %s'`) stays
  unless it is a change detector of that fix's wording, and a behavior
  never loses its last test. The ratchets and the `tests/shared/` readers
  stay.
- **Fragile** tests that stay (a shared account between files, a fixed
  sleep, a wait on an earlier state than the next line needs) are fixed
  as [Order and timing](../../../docs/dev/debugging.md#order-and-timing)
  says.

The report counts the tests deleted, merged and moved, per kind.

### 6. Documentation

Applies to the changed lines of `docs/dev/*.md`, `README` and
`desktop/docs/*`, and to every doc section about what the range
superseded. The docs are reference material for someone who reads them
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
map, …). A section describing an implementation the range replaced, a
feature it removed, or plan steps it built is deleted
([documentation conventions](../../../docs/dev/repository.md#documentation-conventions)).

### 7. Unfinished work

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
- Tests: a user-visible behavior or a hard case of the range with no
  test — note it (adding the test is in scope when cheap).
- Keyboard shortcuts, menu entries, or settings mentioned in the UI text
  that do nothing.

## Step 3 — apply

Fix everything from checks 1–7 that is behavior-preserving, one topic at a
time. Order: delete superseded and dead things → prune tests → dedupe →
simplify → replace raw values → conform UI → rewrite docs.

- An edit may be large when it leaves the code smaller and clearer:
  collapsing two implementations into one, rewriting a module the range
  left tangled, deleting a file. Churn is the finding: renames,
  reorderings and reformatting that change nothing.
- When you move logic, update every caller and the doc that names its
  location.
- When you delete a helper, class, endpoint, setting, test or key, grep
  once more for its name across `backend/`, `frontend/src`, `extension/`,
  `desktop/`, `ipad/`, `android/` and `docs/`.
- Update `docs/dev/*.md` for every structural change you make (the docs
  must describe the tree as it is after the cleanup).

Do NOT apply: anything that changes what the user sees, removes a feature
the user can still reach, or changes an API shape a client reads. Those go
in the report as proposals with a one-line reason. Code that is only
possibly dead is settled by the search above first.

## Step 4 — verify, then commit the topic

Once per topic, after its last edit, the checks of what it touched:

```bash
cd backend && venv/Scripts/python.exe -m pytest tests/<the affected modules> -q
# when frontend/src changed (node is fnm-managed, not on the tool-shell PATH):
eval "$(fnm env --shell bash)"
cd frontend && npm run lint && node --test tests/<the affected>.test.mjs
# when a stylesheet or a script's conventions changed: the ratchets
cd frontend && node --test tests/designTokens.test.mjs tests/uiConventions.test.mjs
# when app/App.jsx changed (a lower count: node tools/appState.mjs --baseline):
cd frontend && node --test tests/appState.test.mjs
# when a doc changed, from the repository root:
node tools/check_doc_links.mjs
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

- The subject starts with `Cleanup of` (a sweep: `Sweep of <area>:`),
  names the topic, then says what changed, in the prose of the log's
  other subjects. Read the last two with
  `git log -2 -i --grep='^Cleanup' --format=%s`.
- Never `git add -A`, `git commit -a` or `git stash`. A new file is added
  by name (`git add <file>`) and a deleted one by `git rm <file>` before
  the commit.
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

1. **Scope**: the range (or the swept area), then each topic with its
   commits and the hash of its commit (or "found clean"), and what was
   left out and why (also when nothing was), as ranges `/cleanup` takes.
2. **Applied**: bullets grouped by check (1–7), each `file — what changed`.
   Lines removed and added by the pass's own commits
   (`git diff --shortstat <HEAD when the pass began>..HEAD`).
3. **Tests**: deleted, merged and moved, per kind, with the files.
4. **Proposals**: behavior-affecting or still-uncertain items, each with
   the one-line reason it was not applied and what the search found.
5. **Unfinished**: the check-7 list, as concrete next tasks.
6. **Verification**: test and build results, verbatim on failure.
7. **Worktrees**: the ones removed, and the ones left, each with why.

Do not push. Do not pad the report with things that were already fine.
