---
name: audit-docs
description: Patrol the developer docs against the code — delete statements that are false, sections about features that were removed or replaced, plans whose steps are built or dropped, research-index entries that no longer hold, skill commands that no longer work — taking each run the docs audited longest ago. Commits each doc as "Docs audit of <path>: …", never pushes. `/audit-docs <files>` audits those instead.
---

# Docs audit

A recurring pass over the docs that no recent commit touched. `cleanup`
checks the docs of what just landed. This skill takes the rest, a few files
a run, so over a few weeks every doc is read against the code again
([workflow.md](../../../docs/dev/workflow.md#patrols)).

Rules that hold throughout:

- **True or gone.** A false sentence is corrected when the true statement
  is short and checked against the code. Otherwise it is deleted. A doc
  that says less and is right beats one that says more and is wrong. A
  doubt is settled by reading the code, never by keeping the sentence.
- **The present only.** Sections about a removed feature, a replaced
  implementation or a dropped plan are deleted, and so is narration of
  history ("previously", "was changed to", "no longer"). Git keeps all of
  it ([repository.md](../../../docs/dev/repository.md#documentation-conventions)).
- **Docs only.** No code edits. Where the doc describes a deliberate
  behavior and the code has drifted from it, the code may be the one that
  is wrong. Report it, and leave the doc as it is until the user decides.
- **Commit per doc.** Running this skill is the request to commit, as for
  `cleanup`: by path, never `git add -A`, never amend, never push. Files
  that were dirty before the pass belong to someone else.

## 1. Pick

The candidates are `docs/dev/*.md`, `docs/research/*.md`, `docs/design/*.md`,
`frontend/src/README.md`, `desktop/docs/*.md`, the apps' READMEs
(`cloud/`, `ipad/`, `android/`, `sites/`, `tools/*/`), and
`.claude/skills/*/SKILL.md`, whose commands and paths drift as well. The
last audit of each is the newest commit whose subject names its path:

```bash
for f in docs/dev/*.md docs/research/*.md docs/design/*.md frontend/src/README.md desktop/docs/*.md \
         cloud/README.md ipad/README.md android/README.md sites/README.md tools/*/README.md \
         .claude/skills/*/SKILL.md; do
  d=$(git log -1 --format=%cs -F --grep="Docs audit of $f:" HEAD)
  echo "${d:-never} $(wc -l < "$f") $f"
done | sort | head -25
```

Take the docs never audited first, then the oldest. Among equals, take the
one whose area saw the most commits since (`git log --oneline --since=<date>
-- <the area's code> | wc -l`): code that moved under a doc is where it
went wrong. Take about 4,000 lines of docs a run. A doc over that is a run
of its own. Before reading, tell the user the pick in one line: the files,
their lines, and when each was last audited.

## 2. Read against the code

Read each doc whole. Check every claim that names something:

- **Names**: a file, function, class, component, CSS class, route,
  preference key, env var, CLI flag, test, workflow job. Does it exist, and
  is it where the doc says (`git ls-files`, `grep -rn`)?
- **Behavior**: "X does Y when Z". Read the code path. A claim about an
  edge case needs the branch that handles it.
- **Numbers**: a limit or a default is checked against its constant. A
  measurement that drifts (test counts, timings, line counts) is deleted
  unless it is the point of the sentence. In a research note it stays as a
  dated measurement.
- **Commands** in the docs and the skills: the script and its flags exist
  (`--help`, or read the script). Do not run commands that deploy, publish
  or touch the developer's ports and `data/` (CLAUDE.md).
- **Superseded content**: a section about an older mechanism kept beside
  the current one, or about a feature whose code is gone.
  `git log -S'<name>' --oneline -- <area>` finds when it went. Delete it.
- **Plans**: built steps described in their topic doc are deleted from the
  plan, which keeps its open steps. A plan that is all built, or dropped,
  is deleted, along with its row or paragraph in the index that lists it
  ([docs/dev/README.md](../../../docs/dev/README.md#plans-and-design-references),
  [research/README.md](../../../docs/research/README.md)). The reason for
  dropping a plan stays in one line where the decision would come up
  again.
- **Duplicates**: one mechanism explained in two docs is kept in the doc
  that owns it, and the other links there.
- **Research notes** are dated findings. Surveys of other products and
  measurements stay. Sentences about Gamma's present that are false go,
  and the "Still open" column of `research/README.md` must match the code.

Source comments cite docs by path and section title. Before renaming or
deleting a heading, search the code for its text
(`grep -rn "<heading>" backend frontend desktop extension ipad android cloud tools`)
and update the citations.

## 3. Edit, check, commit

Delete what is false or obsolete first, then correct what is short to
correct, then move duplicates, then fix links. Keep the doc's voice: plain
declarative sentences, exact names, no filler. Check 6 of the `cleanup`
skill lists the prose rules.

```bash
node tools/check_doc_links.mjs     # every doc: a deleted heading may be another doc's anchor
git commit -m "Docs audit of <path>: <what changed>" -- <path> <docs whose links changed>
git commit --allow-empty -m "Docs audit of <path>: found true"   # nothing to change
```

The empty commit records the audit, so the next run's pick moves on. The
subject always names the audited doc's path followed by a colon, which is
what the pick searches for.

For a pick of several large docs, up to two forks (`subagent_type:
"fork"`) may take one doc each, the largest ones, started once the pick is
stated. Each edits and commits its own doc and the links into it. The
coordinator takes the rest, then runs the link check once over the tree
after every fork has reported.

## 4. Report

1. **Pick**: each doc with its previous audit date and its commit hash.
2. **Removed**: per doc, what went and why (false, removed feature,
   replaced implementation, built or dropped plan, duplicate).
3. **Corrected**: per doc, what now says something different.
4. **Code to look at**: places where the doc describes a deliberate
   behavior and the code does something else, for the user to decide.
5. **Checks**: the link check's result.
