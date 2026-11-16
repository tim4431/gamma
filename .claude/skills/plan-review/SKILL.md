---
name: plan-review
description: Have GPT-6 Astra (through the Codex CLI, read-only) hunt for holes in an implementation plan before code is written — races, missing migration steps, permission edges, released clients, existing code the plan re-implements — then check each finding against the code, keep the real ones and decline the over-defensive ones. Use when a plan for a change beyond a local fix is ready, before presenting or building it.
---

# Plan review by GPT-6 Astra

Astra (`gpt-6-astra`) reviews defensively. It imagines every way a design
can fail, including many that cannot happen here. That makes it a good
finder of holes and a poor judge of which to fix. This skill uses it for
the first job and keeps the second: Astra lists, Claude checks each item
against the code and decides. Astra never edits anything, and none of its
findings goes into the plan unchecked.

When: the plan meets the bar of [workflow.md](../../../docs/dev/workflow.md#plan)
(a stored shape or a migration, authorization or sharing, sync, more than
one surface or device, a family of endpoints), or the user asks. Not for a
local fix, a rename or a docs change.

## 1. The plan, in a file

The plan as it will be presented: plan mode's file, or `$SCRATCH/plan.md`
(`$SCRATCH` is the session's scratchpad directory). It names the files and
modules it changes and the docs it read, so the reviewer can open them. A
plan without names gets generic findings.

## 2. Hand it to Astra

One call, from the checkout or worktree the plan is for, in the background
(Bash's `run_in_background`; it takes 3 to 15 minutes, and the
notification says when it ends):

```bash
codex login status || echo "codex is not signed in"
timeout 30m codex exec -m gpt-6-astra -c model_reasoning_effort=high \
  -s read-only --ephemeral -C "$PWD" -o "$SCRATCH/plan-review.md" \
  "$(cat .claude/skills/plan-review/prompt.md)" \
  < "$SCRATCH/plan.md" > "$SCRATCH/plan-review.log" 2>&1
```

- `-s read-only`: Astra reads the repository and runs read-only commands.
  It cannot write, so it cannot touch `data/` either. The prompt tells it
  not to import the backend, which migrates a data directory.
- The plan arrives on stdin, which Codex appends to the prompt as a
  `<stdin>` block. `-o` keeps the final message, the findings; the log
  holds the rest.
- `high` effort; `xhigh` for a migration, an authorization change or a
  sync design.
- [prompt.md](prompt.md) is the brief. It asks for every hole Astra can
  find, each tied to the code, since recall is what Astra is for.

If `codex` is missing, not signed in, or the call fails, say so in one
line and offer a Claude `Plan` agent with the same prompt. Do not swap in
another model without saying so.

## 3. Triage

Read every finding, then check each against the code it cites, its
callers and the topic doc. Each gets one verdict:

- **Fix the plan.** The failure can happen in Gamma as it is: a real
  caller, a stored row, a released client, a concurrent request. Change
  the plan, and when the case is hard, add its test to the plan.
- **Test only.** The plan already handles it, but the case is hard enough
  to deserve a test that shows it.
- **Decline**, with one line why:
  - the state is impossible, and the line of code that makes it so is
    cited;
  - the guard would sit in front of a caller the change owns, where the
    fix is the caller;
  - it adds a fallback, a retry, a flag, an env var, a validation layer or
    a shim for code that ships in the same change;
  - it is about a scale or a future caller nobody plans;
  - an existing helper already covers it, named.

Compatibility is the one place where Astra's caution often pays.
Something already released really does meet the new code: the desktop,
iPad and Android apps run older code against a newer server, and so do the
extension and a mirror's data format ([mirror.md](../../../docs/dev/mirror.md)
"The data format"), and rows stored before a migration. A finding about
those is checked with care. One about code shipping in the same change is
declined.

"Reuse" findings name an existing module the plan re-implements. Open it,
and when it fits, the plan uses it.

Keep the plan in the repository's own style. Astra's structure and
wording do not carry over.

## 4. Present

The plan gains a short **Review** section: what changed because of the
review, one line per finding, then the declined findings, one line each
with its reason. The user sees both, so a decline they disagree with
takes one line to overturn.

One round. A second goes only to a revision that brought in a mechanism
the first round did not see (a new table, a new protocol), with only that
part. Never loop until Astra has nothing left to say: it always has.
