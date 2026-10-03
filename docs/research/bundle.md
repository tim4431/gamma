# The startup bundle

Findings from October 2026, from one question: the frontend shipped one
2.7 MB main chunk behind a single lazy boundary (`main.jsx` importing
`app/App.jsx` once the locale catalog is in); what did it hold, what does
each first screen need, and how far does splitting it by surface go without
changing what a page paints? The mechanics that came out of it, the lazy
boundaries and the preload rule, are in
[frontend-refactor.md](../dev/frontend-refactor.md#lazy-boundaries).

## How it was measured

Bundle composition: a treemap from `rollup-plugin-visualizer` through a
temporary Vite config outside the repository (no dependency added), and
minified bytes per module from `source-map-explorer` over a build with
source maps. The visualizer's own sizes are before minification and run
about twice as large.

First screens: a backend from the e2e harness (free port, throwaway data
directory) serving the build, Chromium with a fresh context and the cache
disabled, and the page's resource timing read the moment the first screen
is up: the library when its card for a page shows, a PDF page at its first
`pdf-painted` mark, a notes page when its first block row shows. Whatever
starts in the next 3 s (6 s throttled) counts as "after". One run each,
unthrottled and at 20 Mbps / 40 ms; the byte counts repeat exactly, the
times move by tens of milliseconds between runs.

## What the main chunk held

2,691,705 bytes, 691 KB as Brotli. The ten largest modules, minified:

| Module | KB | Pulled in by |
|---|---|---|
| `pdfjs-dist/legacy/build/pdf.mjs` | 380 | the PDF viewer |
| `katex` | 264 | note and chat Markdown (`rehype-katex`), the LaTeX editor's preview and lint; mermaid's math (already lazy) |
| `app/App.jsx` | 228 | the root: library, page, docks, menus |
| `@codemirror/view` | 152 | the block editor (`BlockCmEditor`, also text boxes); the guide's typing demo |
| `chat/ChatDock.jsx` | 61 | the chat dock |
| `parse5` parser | 47 | `rehype-raw` in note rendering |
| `parse5` tokenizer | 47 | the same |
| `@codemirror/state` | 46 | the block editor |
| `editor/BlockTree.jsx` | 45 | the notes outliner |
| `pdf/PdfViewer.jsx` | 43 | the PDF viewer |

By package or folder: the Settings panes 250 KB, CodeMirror altogether
245 KB, `highlight.js` 165 KB over 37 modules (`lib/common`, for code
fences in notes, chat replies and the editor's code widget), `parse5` with
`entities` 159 KB, the chat 116 KB, the guide 80 KB. `diff-match-patch` is
not in the web bundle (only `replica/`, the iPad app's); `perfect-freehand`
is 4 KB.

Every first screen also downloaded the pdf.js worker, 1.4 MB (333 KB as
Brotli): it was created at module scope, so the library and notes pages
fetched it too.

## What splitting by surface reached

Settings with its panes, the chat dock, the guide overlay, the palette, the
import, export and review dialogs, Recently deleted, Report a problem, the
share popover and pdf.js became chunks of their own. The main chunk went to
1,883,010 bytes (494 KB Brotli, 605 KB gzip); JavaScript files in the build
from 67 to 80. Three moves made it possible beyond `React.lazy` itself: App's
own needs from the Settings modules moved into small modules
(`providerEditor.js`, `workspaceRoles.js`, `settingsNavigation.js` without
the search catalog), and the worker is created with the engine.

First screens, Brotli bodies of the static files (KiB), first screen up at
20 Mbps / 40 ms:

| First screen | Before | After |
|---|---|---|
| Library | 1207 (JS 1041), up at 1.17 s | 719 (JS 556), up at 1.08 s; then pdf.js, its worker and the palette (423) 1.5 s after the listing |
| PDF page | 1207 (JS 1041), up at 1.30 s | 1139 (JS 976), up at 1.14 s |
| Notes page | 1249 (JS 1041), up at 1.16 s | 1180 (JS 976), up at 1.17 s |

Unthrottled, the library was up at 352 ms instead of 484 and a PDF page at
405 instead of 520. A notes page still fetches pdf.js and the worker at
startup: a page address does not say whether the page has a PDF, and
starting the engine with the app keeps a PDF page's cold open where it was
(the e2e probe: 0.50 to 0.54 s from `open` to first paint, four runs). The
chat dock is on a desktop's first screen by default, so its chunk (31 KB)
goes out with the startup requests.

## What stayed in, and why

1.88 MB is not under the 1.5 MB that was hoped for. What is left: KaTeX
263 KB, `App.jsx` 249, `highlight.js` 165, CodeMirror 245, the editor
folder 139, `parse5` 127, shared UI and helpers 92, the PDF viewer
component 58, the guide's engine and tour data 50, collaboration 48, ink 39.

- Note rendering (react-markdown, `rehype-raw` and its parse5, KaTeX,
  `highlight.js`) paints every page's notes at first render. Loading any of
  it late shows raw `$…$`, raw HTML or uncoloured code for a moment.
- CodeMirror: an empty page opens with its seed block in the editor, and
  the editor's ref is used right after a block opens (caret, focus).
- `App.jsx` holds the library and the page markup alike; it splits only
  with the [decomposition](../dev/frontend-refactor.md).
- The guide's hook and tour data decide offers on every screen; only its
  overlay moved out.
- Not split for size against coupling: the search panel (14 KB: its header
  button lives in it, it keeps a pinned search live while closed, and the
  editor's `[[` picker imports its `MarkedText`), the mirror popover (18 KB:
  its sync pill is on screen in a clone or publishing workspace), the
  notebook view (7 KB, a notebook page's first screen), the ink and markup
  layers (painted on pages that carry ink), the login page (the first screen
  when signed out).

The rest of the way to 1.5 MB is the page surface: the block editor with
CodeMirror, note rendering, the PDF viewer, ink and the notebook are about
1 MB of the chunk that the library never paints. Splitting there needs the
page view out of `App.jsx` first, and its chunk fetched beside the `App`
chunk whenever the address names a page, so no page waits on a second round
trip.

## Found on the way

The import review's e2e step held `/api/import/review` behind a gate that
never matched: the upload's URL carries `?ws=`, which a Playwright glob
without `*` does not cover, so the step had been racing the upload. The
glob now ends in `*`.
