# What Gamma took from Oleafly

Study date: 2026-11-05 to 11-07. [Oleafly](https://github.com/Oleafly/Oleafly)
is an open-source desktop editor for LaTeX, Typst and Markdown, an Overleaf
alternative built on Tauri, React and Rust. Its product overlaps little with
Gamma's. What carries over is how the repository is engineered: rules
written down are also held by a test, budgets are numbers a build is checked
against, and each feature has a small owner with its own tests.

The study read Oleafly's README, its docs (architecture, design system, PDF
preview, editor, collaboration server, AI copilot, checkpoints, keyboard
shortcuts, updates), its package manifest and a few scripts
(`check-web-budget.mjs`, `check-performance-budget.mjs`,
`production-hook-audit.mjs`, the editor performance suite). Each idea was
then designed against Gamma's code by one agent, attacked by a second where
the change was large, built in a worktree of its own, reviewed and merged on
the `learn-from-oleafly` branch. Several ideas changed shape on contact with
the code, and the scans behind the guards found bugs on the way. Both are
recorded below. The mechanics live in the dev docs each section names.

## Guards: written rules held by tests

**App's state.** Oleafly keeps client state in about fifty small stores,
each with a test. Gamma had the same pieces (the `ink/` owners, the stores
read through `useSyncExternalStore`), but `app/App.jsx` kept collecting
state: from 317 to 458 state and effect hooks and from 7,853 to 10,747 code
lines between 2026-09-17 and 11-05. All six commits in that history that
added more than 150 lines also added hooks, while the commits that left the
hooks alone grew the file by a median of 7 lines. So the ratchet counts
hooks exactly in both directions and holds code lines only to a ceiling.
Built: [dev/frontend-refactor.md](../dev/frontend-refactor.md#new-state).
Left out: `useMemo` and `useCallback`, which are a composition root's glue
and often a row's identity fix, and the four feature giants, which already
live in their feature's folder.

**The startup bundle.** Oleafly fails its build over fixed byte ceilings
and checks that exactly one pdf.js worker ships. Gamma had measured its
October split ([bundle.md](bundle.md)) but checked nothing afterwards.
Weekly rebuilds showed startup JS growing 37% in five weeks, from 535,344 to
731,215 bytes Brotli, with every lazy boundary intact. The gate pairs byte
ceilings, 10% over the build with a reason each, with structural rules: every
literal `import()` keeps a chunk of its own outside the startup graph, one
worker, and precompressed copies. The structural rules matter more than the
bytes: one static import of the Settings dialog stayed just under the Brotli
ceiling. Built: [dev/frontend-refactor.md](../dev/frontend-refactor.md#the-startup-budget).
Left out: an exact-byte ratchet, which every feature would trip, and a
total-JS ceiling, which lazy mermaid dominates.

**UI conventions.** Oleafly's design-system tests fail on stray UI, with
exceptions allowed per file and per reason. Gamma's
[dev/ui-design.md](../dev/ui-design.md) was stricter in prose but enforced
only raw CSS values. The new scanner checks 22 rules over the JSX and CSS
with a per-file baseline and `ui-allow <rule>: why` comments. Its first scan
found 122 breaks, many of them real bugs, all fixed in the same change:

- 27 Enter handlers that committed while an input method was still
  composing, which matters for Chinese input;
- 8 file picks that bypassed `pickFiles`, the iOS fix;
- 4 controls hidden with opacity but still tappable;
- 10 `font: inherit` shorthands that undid the interface-size reset;
- 2 username fields left autocapitalizing.

Built: [dev/ui-design.md](../dev/ui-design.md#ratchets). Left out:
Oleafly's ban on the native `title` attribute, which Gamma's doc prescribes
in places, and ten candidate rules that were too noisy.

**The lint.** Oleafly blocks a change on correctness lint errors. Gamma had
no linter. Biome 2.5.15, pinned exactly, now runs only bug-finding rules,
with every diagnostic blocking. Its first run found a `\b` in
`tools/themes.mjs` that had become a literal backspace byte, so the theme
contrast evaluator silently dropped any `html[...]` rule. It also found a
test regex that had lost an escape and a dead duplicate CSS declaration.
Built: [dev/debugging.md](../dev/debugging.md#frontend-lint). A type check
was measured and not built. `checkJs` gave 1,700 errors over `src`, none a
real bug. The only clean module set was the 136 modules the node tests
already cover at 96.7% of lines. Non-strict mode types unannotated
parameters as `any`, so it misses the typo a type check is for.

**Twin implementations.** Oleafly keeps its Rust and TypeScript twins in
step with shared golden fixtures. Gamma already did this more widely (24
files under `tests/shared`), but a second tier of mirrored rules was held
together only by comments or by case tables copied into both suites. Eight
new fixtures and a registry test, which fails any fixture without a reader
on both sides, found four drifts, each a cross-language trap:

- Python's `round` takes a half to even and `Math.round` takes it up, so an
  imported highlight at alpha 0.3 stored `4c` where the app writes `4d`;
- JS counts UTF-16 units and Python code points, so a share-link name could
  keep a lone surrogate;
- JS `\s` takes U+FEFF and leaves U+0085, and Python's the reverse;
- a JS object literal answers prototype keys, so `normalizeItemType("constructor")`
  returned a function.

The fractional-key pair found a fifth: neither library checks a key's
characters, and an op could store a position that made every later append
under that parent a 500. Built: [dev/debugging.md](../dev/debugging.md).

## Measurements: budgets and a soak

**Timing budgets.** Oleafly's editor suite asserts a p95 against a fixed
budget for each case, alongside a correctness check. Gamma's blocking
performance guards were all structural. `npm run test:perf` and
`pytest -m perf` now time the pure hot paths on seeded fixtures. In CI they
only report, since a shared runner's speed has no cause in the repo and the
merge skill forbids merging over a red check. Measured at idle: a keystroke
on a 1,000-block page costs about 1.5 ms of pure work. Half of it is the
highlights memo, mostly `JSON.stringify` of a 254 KB list kept only for its
identity. `blocksToHighlights` runs twice per key. The server's ink merge
spends about two-thirds of its 21 ms in a pydantic round-trip under the
write lock. `inkStore.js` keeps every drawing it loaded for the tab's life,
about 37 MB each. These are follow-ups, not fixed here. Built:
[dev/debugging.md](../dev/debugging.md#performance-budgets).

**A collaboration soak.** Oleafly runs crowd tests: many scripted people
over time, with latency percentiles, memory and convergence. The Gamma soak
drives N copies of the production client session over Node's fetch and
WebSocket against an isolated server. So every debounce, retry, catch-up and
merge is production code. Its oracle replays every lost token through the
server's merge, and it found two real bugs:

- `textmerge.merge('the cat sat', 'the cool cat sat', 'the sat')` returned
  `'the ool csat'`, in about 1.5% of conflicting pairs. An insertion slid
  across a repeated character into the span the other side deleted. Merges
  now read changes at word boundaries.
- A set whose content equalled its base, a word typed and deleted again
  within one save, was answered with the older text, which undid peers'
  edits.

At 20 tabs over 300 s, ack p99 was 33 ms, fan-out p99 39 ms, and server
memory and the database levelled off after a minute. A short run is in the
browser suite. Built: [dev/collab.md](../dev/collab.md).

## Features

**Search qualifiers.** Oleafly's library search takes GitHub-issue syntax.
Gamma's Ctrl+F already had one hand-built qualifier in its label and folder
chips. It now reads `label:`, `folder:`, `is:`, `no:`, `author:`, `venue:`,
`year:`, `created:`, `updated:` and `in:`, with commas for alternatives and
a leading `-` to exclude. Each is evaluated on the library listing the
client already holds. A known key with a value Gamma cannot apply is
searched as text under an amber line, so a typo never quietly widens a
filtered search. Left out: OR and parentheses, which the fuzzy matchers
cannot express, and negated free text, since physics text starts with minus
signs. Built: [dev/home_library.md](../dev/home_library.md).

**Models that cannot call tools.** Oleafly checks tool calling per model
before use. In Gamma, a model whose endpoint refused tools failed every
message with misleading advice: OpenRouter's "No endpoints found that
support tool use" read as a bad base URL. Gamma now makes one live check per
connection and model before its first tool-enabled chat. A model that fails
answers as a plain chat and says so. Left out: making such models
unselectable, since Gamma degrades and says why, as it does for pictures.
Built: [dev/ai.md](../dev/ai.md#models-that-cant-call-tools).

**Plan mode.** Oleafly's agent can plan read-only before it changes
anything. Gamma's agent began changing things within the reply. With Plan
on, changing calls are held at dispatch, the model proposes a checklist with
`update_plan`, and Approve plan runs it under the chat's usual approvals and
revert. Changing calls are refused at dispatch rather than filtered from the
offered tools, so the tool specs and the system prompt stay byte-identical
and the prompt cache holds. Left out: Oleafly's PLAN/STEP/REVIEW pill and
its changed-files panel, which Gamma's steps pill and changes list already
cover. Built: [dev/ai.md](../dev/ai.md#planning-first-plan-mode).

**A page without a WebSocket.** Oleafly falls back to a held HTTP request
when sockets are blocked. Gamma already posted every edit over HTTP and had
an op-log catch-up, so the fallback is a plain 2 s poll of that catch-up
after two failed socket attempts. A plain GET on the socket path now
answers 426 and logs the proxy fix. Left out: a held request, which pins
connections for a small population of misconfigured proxies, and presence
over HTTP. Built: [dev/collab.md](../dev/collab.md).

**Zoom without re-rendering pages.** This was the "Left open" item of
[performance.md](performance.md), confirmed by Oleafly's habit of setting
editor colours as root variables. The committed zoom reaches the pages as a
registered, non-inheriting `--viewer-scale`. Over a 20-notch Ctrl+wheel
train, script time fell from 180 to 96 ms at 300 pages. A translated
document's layout fell from 124 to 5 ms. On the merge, dev's new
translation layer, laid out once at a reference scale, took the variable
into its one transform, so a zoom now lays out no translation at all. WebKit does not follow a parent's
change on a child whose inline style was not written by script, so every
element that picks the variable up carries one. Details:
[pdf-zoom-frames.md](pdf-zoom-frames.md).

**React 19.** Oleafly runs React 19. Gamma moved to 19.2.9 and stays off
19.3, which adds about 7 KB for features Gamma does not use. One real break
came of it: `inert` became a boolean. The React Compiler was measured and
left out, since every function on the keystroke path bails out. Details:
[react-19.md](react-19.md).

## Not taken

- Tauri and Rust: Gamma's Electron shell exists so that a Python server
  runs on the device.
- The ban on outlines and focus rings: Gamma's one focus ring serves
  keyboard users better.
- A 98% coverage threshold, which mostly produces churn.
- The pdf.js 6 upgrade: an unexplained regression measured on 6.4 is
  recorded in [pdf-zoom-rendering.md](pdf-zoom-rendering.md).

## Still open

- The keystroke and ink costs the timing budgets measured.
- The CI runner's speed against the desktop's, read from the `perf` job's
  first summaries, before the budgets could block.
- Qualifiers for highlights, ink, comments and shares, which the listing
  does not carry yet.
- Real-provider rates of refused calls and prose-only plans in plan mode.
- A WebKit run of the zoom change on an iPad.
- The highlight review's quote length, which has the same UTF-16 against
  code-point mismatch.
