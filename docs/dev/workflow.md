# How a change is made

The path a change takes from a request to `main`, and the skill in
`.claude/skills/` that carries each step. The steps scale with the change.
A local fix skips the plan and its review and goes from a commit to a
merge. A feature that adds a stored shape goes through every step.

| Step | Skill | Output |
|---|---|---|
| Plan, and a second opinion on it | `plan-review` | a plan with a Review section |
| Build | (none) | commits on a branch, or on `dev` |
| Review before `dev` | `branch-review` | findings, fixed on the branch |
| Clean what landed on `dev` | `cleanup` | `Cleanup of …` commits |
| Merge `dev` to `main` | `merge` | a green PR, merged |
| Patrol the docs | `audit-docs` | `Docs audit of …` commits |
| Patrol an area's code and tests | `cleanup sweep <area>` | `Sweep of …` commits |

## Plan

A change that is more than a local fix gets a written plan before any
code: one that adds or changes a stored shape or a migration, touches
authorization or sharing, touches sync (collaboration, mirrors, vaults,
Zotero), reaches more than one surface or device, or adds a family of
endpoints. The plan says:

- what the user sees and does, case by case, failure cases included;
- the modules it changes, and the existing helpers, widgets and topic docs
  it builds on, by name (the [shared rules](README.md#architecture-and-shared-rules),
  the [source map](../../frontend/src/README.md), the topic table);
- the stored shape and its migration, and what released clients do with
  it (the desktop, iPad and Android apps, the extension, a mirror's data
  format);
- how each user-visible behavior and each hard case is tested
  ([What a test is for](debugging.md#what-a-test-is-for));
- what it leaves out.

The `plan-review` skill then hands the plan to GPT-6 Astra through the
Codex CLI, read-only. Astra reviews defensively. It finds the race, the
missing migration step and the permission edge, and it also asks for
guards against states that cannot happen. Its findings are an input, not
a verdict. Each is checked against the code, and only one whose failure
can happen in Gamma as it is changes the plan. A guard, fallback or knob
for an impossible state is declined, since the `cleanup` skill would take
it out again.

## Build

In a worktree branched from `dev` when one is asked for. While writing,
run no tests: write a batch of edits, then run its checks once
([test policy](debugging.md#local-changes-test-the-affected-modules)).
Write tests for what the user meets and for the hard cases, and nothing
else ([What a test is for](debugging.md#what-a-test-is-for)).

## Review, then merge into dev

`branch-review` reads the branch against `dev` and answers four questions.
Does it run: built, tested and driven, not assumed. Is it elegant: the
smallest shape that fits the architecture. Are its tests worth keeping.
Does it reuse what the repository already has. Its findings are fixed on
the branch. The branch then runs the full backend suite and the frontend
unit tests once, on its last commit, and merges into `dev`. A failure
found there costs minutes; the same failure found by the PR to `main`
costs a CI round.

## Cleanup, then merge to main

`cleanup` passes over what landed on `dev` since its last pass. It
restructures what a feature built in steps left tangled (an overgrown
module split, two implementations made one), unifies scattered logic,
removes the code a new feature replaced, replaces
hard-coded values with tokens, prunes tests, and fixes the docs the work
touched. `merge` takes a snapshot of `dev` to `main` through a PR and runs
the PR's suites locally first.

## Patrols

Two passes look at what no recent commit touched, a few files at a time.
Each run takes what was patrolled longest ago, which its commit subjects
record (`git log --grep='^Docs audit of'`, `--grep='^Sweep of'`).

- `audit-docs` checks the developer docs and research notes against the
  code. It deletes false statements, the designs of features that were
  replaced or dropped, and plans whose steps are built.
- `cleanup sweep <area>` runs the code checks of `cleanup` over an area's
  existing code and tests: tangled modules, leftovers of old
  implementations, hard-coded values, tests that do not earn their place.

Once every week or two, or before a release, is often enough.
