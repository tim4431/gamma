# React 19 and the React Compiler

Findings from November 2026, from two questions: what does moving the
frontend from React 18.3.1 to React 19 cost and break, and does the React
Compiler make the slow path (App's render on every keystroke) any faster?
The answers: the move is cheap and was made, to 19.2 rather than 19.3; the
compiler compiles nothing on the slow path and was left out. The rules that
came out of it are in [dev/debugging.md](../dev/debugging.md) ("Gotchas
worth knowing": the 19.2 pin, the boolean `inert` prop, StrictMode's
double ref attach) and [dev/frontend-refactor.md](../dev/frontend-refactor.md)
(hand memoization stays).

## How it was measured

Scratch copies of `frontend/` (no `node_modules`, no `dist`), each with its
own install of React 18.3.1, 19.2.9 or 19.3.1; nothing was installed into
the repository but the upgrade itself. Production builds were compared by
the entry chunk (`assets/index-*.js`, which every first screen loads) and
the `App` chunk, in minified bytes and in the Brotli file the build's
precompress step writes. The browser suite ran in full on 6 workers
against each build, and `markdownSecurity.mjs` and `npm run e2e:latex`,
which `npm run e2e` does not run, on their own.

StrictMode was looked at two ways: unit tests that mount a root in jsdom
(node runs React's development build), and a probe that served a
development build (`NODE_ENV=development vite build`) from the e2e
harness's backend and opened Settings and a folded Notes window in
Chromium.

The compiler: babel-plugin-react-compiler 1.0.0 over every `.js` and
`.jsx` in `src`, with `noEmit` and a logger that records each function it
compiles or skips and why, in infer mode and again with ESLint
suppressions ignored; then real builds with the plugin through
`@vitejs/plugin-react` 4.7's `babel.plugins`, in infer and annotation mode.

## What touches React

| Package | Version | React it accepts |
|---|---|---|
| `react-markdown` | 10.1.0 | `>=18` |
| `react-resizable-panels` | 2.1.9 (pinned to v2 on purpose) | `^16.14` through `^19` |
| `@vitejs/plugin-react` | 4.7.0 | no React peer; Vite `^4.2` through `^7` |
| `hast-util-to-jsx-runtime` (through react-markdown) | 2.3.6 | none |

Nothing blocks 19 and nothing needs it. Only `frontend/package.json` lists
React; `desktop/`, `sites/`, `extension/` and the plugins have none.
react-resizable-panels v2 keys a saved layout by the panels' ids or
constraints, never by `useId`, so layouts survive React 19's new `useId`
format.

## The releases

18.3.1 (2024-04-26) is the last React 18. On 2026-10-14 every 19 line got a
patch: 19.0.9, 19.1.10, 19.2.9 and 19.3.1. In react-dom's client those
patches change the version string and nothing else (19.2.8 against 19.2.9,
and 19.3.0 against 19.3.1, diffed): the fixes were in server packages Gamma
does not ship. So React 18 lacks no fix that reaches Gamma's bundle. What
19 gives is the APIs the follow-ups below want (`useEffectEvent`,
`<Activity>`, ref cleanups), React's Performance Tracks in development and
profiling builds for the keystroke profile that
[performance.md](performance.md) says nobody has taken, and a line that
still gets releases.

## 19.2 or 19.3

| React | Entry chunk, min | Entry chunk, Brotli | `App` chunk, min |
|---|---|---|---|
| 18.3.1 | 148.9 kB | 42.4 KB | 2,552.8 kB |
| 19.2.9 | 199.8 kB | 54.3 KB | 2,552.8 kB |
| 19.3.1 | 229.0 kB | 61.6 KB | 2,552.8 kB |

The `App` chunk's Brotli moves by under 1 KB. The extra 7 KB in 19.3 is
react-dom's client carrying stable view transitions and Fragment refs: its
production client names `ViewTransition` 219 times (19.2.9: once) and
`FragmentInstance` 72 times (19.2.9: never). Gamma uses neither. 19.2.9
already exports `Activity`, `useEffectEvent`, `cacheSignal` and the
compiler's runtime. So `package.json` asks for `~19.2.9`: a refresh takes
19.2 patches and never 19.3, and a move to 19.3 is a decision of its own.

## The audit

| React 19 change | In Gamma |
|---|---|
| `ReactDOM.render`, `hydrate`, `unmountComponentAtNode`, `findDOMNode` removed | none; `main.jsx` uses `createRoot` |
| `defaultProps` on functions, `propTypes`, legacy context, string refs, `createFactory`, `UNSAFE_` lifecycles | none |
| `useRef()` needs an argument | every call has one |
| `react-dom/test-utils`, `react-test-renderer` | none; 5 unit tests use `renderToStaticMarkup`, still exported |
| `element.ref` deprecated; `cloneElement` | neither; `Children.toArray` only reads props |
| A callback ref may return a cleanup | every callback ref returns nothing |
| `<title>`, `<meta>`, `<link>`, `<style>`, `<script>` hoisted to the head | none in JSX; a note's raw HTML goes through `rehypeNoteSanitize`, whose schema strips them |
| New `useId` format | the ids go only into `id`, `aria-*` and `url(#…)` |
| Uncaught errors through `reportError` | still a window `error` event, which App logs into the problem report as before |
| `forwardRef` and `.Provider` | 6 and 39 uses, both still work |
| Boolean `inert` | five sites wrote the empty string: the break below |

Two class components remain (`LazyBoundary`, `ListMotion`), on APIs 19
keeps. Each Suspense boundary holds one surface, so 19's sibling
pre-warming changes nothing.

## What broke

**`inert` became a boolean.** React 18 wrote `inert=""` for the empty
string and dropped `inert={true}`; React 19 writes `inert=""` for true,
nothing for false, and reads the empty string as false, with a warning in
development only. Gamma wrote `inert={x ? "" : undefined}` at five sites:
the folded dock window (`Widgets.jsx`), a closing `Collapse`
(`MotionWidgets.jsx`), the Settings sub-dialog's header and content while
it asks (`SettingsKit.jsx`), and the Settings body while a change is
pending (`SettingsDialog.jsx`). On 19 none of them was inert, and neither
was anything that skips `closest("[inert]")` under them. Two browser steps
caught it: `motion.mjs`'s "what it folds takes no press while it goes" and
`comments.mjs`'s "the Notes window folded, its rows inert". The three
Settings sites have no browser step. The sites now pass a boolean, which
React 18 would drop, so the fix landed with the version.
`tests/inert.test.mjs` fails on any string given to `inert` in `src` and
pins React's rendering of true and false.

**StrictMode attaches a callback ref twice.** In development React 19
calls a host element's ref with the element, then null, then the element
again as it mounts; React 18 called it once. Two refs did work on that:

- `useLeave` (`shared/ui/leave.js`). The null queued the surface's leave,
  and the second attach saw that leave pending and marked the surface as
  taking another's place (`data-over`), which turns off the backdrop's fade
  (`motion.css`). Every dialog in `npm run dev` came up without its fade.
  A surface attached again before its own leave has gone through now counts
  as no arrival (`holding` in leave.js).
- The comment margin's card refs (`CommentMargin.jsx`) cached one callback
  per card and dropped it on null. After StrictMode's double attach, every
  render handed React a new callback, so each card was detached, attached,
  unobserved and observed by the margin's ResizeObserver on every render.
  The callbacks now live as long as the margin.

`tests/leave.test.mjs` mounts a dialog under StrictMode and fails on 19
without the leave.js change. The probe on development builds:

| Build | Settings backdrop | Folded Notes window | Console |
|---|---|---|---|
| React 18.3.1, old source | fades in | inert | quiet |
| React 19.2.9, old source | `data-over`, no fade | not inert | "Received an empty string for a boolean attribute" |
| React 19.2.9, fixed | fades in | inert | quiet |

The other refs allow for the double call: the cleanup-ref pattern
(`useTextScale`, `useWheelPan`), map set and delete, and the toolbar's
overflow ref, whose state update bails out as a same-value update.

**One browser step raced.** "The welcome deck's way in opens the seeded
Welcome page" waited for no guide card at all after the deck's last
button. The Welcome page holds a PDF, and its opening offers the windows
tour, so the step passed only when a poll landed between the deck going
and that offer coming. A frame-by-frame probe shows the same cards in the
same order on both versions, but on 19 the gap is shorter: alone, the step
passed twice out of two on 18 and once out of three on 19. It now waits
for the deck alone.

Everything else passed unchanged on 19.2.9: `npm test`, the full browser
suite (653 of 653), `markdownSecurity.mjs` and the LaTeX editor's script.
One full run on a loaded host failed "the fourth trip home offers the
Ctrl+P hint" and an ink step waiting 30 s for a PDF; the triggered-guide
group then passed three times out of three and the next full run passed
whole. The suite's
steps that check a menu opens in place (`assertOpensInPlace`) pass too, so
[menu-flicker.md](menu-flicker.md)'s account of which updates render
before the paint still holds.

## A development build is no gate

The browser suite drives the production build, which never double-attaches
a ref and logs no warning. Run against a development build, the motion,
settings, comments, notes-pdf-share and guide groups passed 138 of 178
steps on React 18 and 133 of 178 on 19.3.1 with the fixes, most failures
on both being the block editor not visible within 5 s. That is too red to
tell a React regression from a slow build, and the cause was not looked
into. The development-only cases rest on the unit tests above.

## WebKit

The iPad app shows this bundle in WKWebView, and the iPad and Android
workflows do not run on frontend changes. Playwright's WebKit does not
start on the measuring host as installed (missing system libraries); with
those libraries unpacked from Ubuntu's packages beside it and Playwright's
host check skipped, the motion, settings, comments and ipad groups ran
under `GAMMA_E2E_BROWSER=webkit` against a React 18 build and against the
19.2.9 build. Neither passes: of the 110 steps, the 18 build failed 22
and the 19.2.9 build 19, and 15 of those failed on both, most of them
WebKit on this host and not React (the iPad shell's file and Pencil steps,
the comment margin's cards). Each step that failed on the 19.2.9 build
alone was run again by itself on both builds, twice: two of the Inbox's
motion steps and Settings' Server → Guests gave the same result on both.
So WebKit showed no break of its own, but the check is weak, and the next
TestFlight build is the first real WKWebView run.

## The compiler

babel-plugin-react-compiler 1.0.0 in infer mode compiles 553 of 686
functions. Of the 133 it skips: 57 on syntax it does not handle yet, 51 on
refs read or written during render, 9 on an `eslint-disable … react-hooks`
comment (there are 78 such comments and no ESLint config), 8 on a
reassignment after render, 8 other. Every function on the keystroke path
is among the skipped:

| Function | Skipped for |
|---|---|
| `LibraryApp` (`App.jsx`) | its ESLint suppressions; with those ignored, `try`/`finally` and `try` without `catch` (15), `throw` inside `try`, `\|\|=`, `x++` captured in closures, and refs written during render |
| `PageHostGate` (`App.jsx`) | a conditional inside `try`/`catch` |
| `BlockRow` | a suppression, then `try` without `catch` |
| `BlockMarkdown` | a variable reassigned after render |
| `BlockTree` | an update expression on a variable a closure captures |
| `PdfViewer` | the same |
| `PdfPage` | refs read during render |
| `useStableActions`, `useLatch` | refs read and written during render |
| `lazySurface`'s surface | `\|\|=` |

What compiles are leaves off that path, and their parent hands them new
props on every render anyway. On the build (19.3.1 scratch copy):

| Build | Time | `App` chunk | `SettingsDialog` chunk |
|---|---|---|---|
| no compiler | 10.5 s | 2,552.8 kB | 238.0 kB |
| infer mode | 19.6–22.4 s | 2,723.1 kB (+40 KB Brotli) | 299.4 kB |
| annotation mode, nothing annotated | 12.7 s | 2,552.8 kB | 238.0 kB |

So the compiler doubles the build, adds 40 KB of Brotli to `App`, and
speeds up nothing that is slow. Annotation mode (`"use memo"`) has no
function worth annotating today. The compiler does not need React 19: its
`target` option with the `react-compiler-runtime` package covers React 17
and 18, so its trigger below is independent of this upgrade.
`@vitejs/plugin-react` 6 takes it through `@rolldown/plugin-babel` and
needs Vite 8; on Vite 5 it goes through plugin-react 4.7's or 5's
`babel.plugins`.

## Decision, and when to look again

React moved to `~19.2.9` with the five `inert` sites, the leave.js and
comment margin fixes and their tests, and no React 19 API adopted yet. The
cost is 11.9 KB of Brotli on every first screen's entry chunk, for the
APIs and the profiling the follow-ups want. No compiler, ESLint or plugin
option was added.

- The compiler: once the NotesPane or page session of
  [frontend-refactor.md](../dev/frontend-refactor.md) is out of `App.jsx`,
  run the compile report again on a scratch copy over the new owners.
  Annotate (`"use memo"`) only an owner that compiles and sits on the
  keystroke path, with a browser profile before and after.
- 19.3: when a feature wants view transitions or Fragment refs.
- Follow-ups, each its own change: ref cleanups in place of the
  cleanup-ref pattern (`useTextScale`, `useWheelPan`, the comment margin's
  cards); `<Activity>` for the `useLatch` surfaces (Settings, the palette,
  the guide), which would also stop their effects while hidden;
  `useEffectEvent` in place of render-time `xRef.current = x` writes, which
  any later compiler attempt needs first; dropping `forwardRef` and the
  `.Provider` spellings.
- Not measured: the keystroke's render cost on 18 against 19. React 19.2's
  Performance Tracks make that profile cheap to take now.
