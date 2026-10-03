# Gamma on the iPad (and other tablets)

There are two ways to use Gamma on an iPad:

- **The native app** (`ipad/`, [ipad/README.md](../../ipad/README.md))
  keeps a copy of one workspace on the iPad. Its PDFs and notebooks read
  and write offline with Apple Pencil, and it syncs with the server by the
  desktop mirror's rules.
- **The web app installed to the home screen** is every feature, online.
  It is described under [The installed web app](#the-installed-web-app).

Both store the same thing: handwriting is the same `gamma-ink` file and
the same block properties, whichever client wrote it
([handwriting.md](handwriting.md)). The native app also opens the web app
in a web view for everything it does not do itself.

## The native app

### What the user does

1. Enter the server's address and sign in on its own web page, with a
   password or Gamma Cloud (whatever the server offers).
2. Choose a workspace. The app mints a write token for it
   (`POST /api/integrations/tokens {scope: "write"}`, the credential a
   desktop clone holds, [mirror.md](mirror.md) "Credentials") and keeps it
   in the Keychain. The web session stays in the app's web views for
   "Open on the web".
3. The first sync brings the workspace over, files included. The library
   lists its pages by folder. A PDF page opens in PDFKit, and a page of
   notes as its outline. A page with sheets of paper
   ([notebooks.md](notebooks.md)) opens in the notebook view, its sheets
   one under the other; a toolbar button shows its notes alone and back.
   The notes open beside a PDF or the sheets. "Add a page to write on" at
   the end of the notes adds a sheet after the last.
4. The Pencil writes. Fingers scroll, zoom and select. The tool strip has
   the pen and highlighter presets, the eraser, the hand (the Pencil
   scrolls), a new group, undo and redo. The Pencil's double tap switches
   to the eraser and back. A handwriting group's row in the notes has a
   play button: the page scrolls to the group and replays its writing.
5. Rounds run every 30 seconds while the app is in front, when it comes
   back, two seconds after an edit, and on request. The sync button shows
   a round running, edits not sent yet, or a problem. "Sync decisions"
   lists the choices rounds made on their own (merged texts, edits that
   beat deletions). "Receive only" stops pushing, like a clone's
   direction.

### A replica: a mirror without a server

A desktop clone is a second Gamma server holding a copy
([mirror.md](mirror.md)). An iPad cannot run that server, so the app keeps
a **replica**: the same copy, kept by the same protocol and rules, on a
device with no server of its own.

- **What the remote sees is a clone's traffic.** The replica calls
  `GET /api/sync/whoami`, walks `GET /api/sync/changes`, reads
  `GET /api/blocks/{id}/subtree`, and pushes `POST /api/pages/{id}/ops`
  batches under batch ids with `client: "sync"`. It creates pages with
  `POST /api/pages {id, title, properties}` and deletes them with
  `DELETE /api/blocks/{id}` (never the folder and label trees, below).
  Files travel by name with `HEAD`/`GET`
  `/api/uploads/<name>` and `POST /api/upload-file`, or `/api/uploads`
  for a PDF. The server needs nothing new.
- **The cursor is the feed's.** Each round walks the change feed to the
  end from the cursor the last one kept (`remote_cursor` in the host's
  meta) and keeps the new one. It is a string the replica stores as it is
  and never reads (the remote's change-log seq; `""` at first). A cursor
  the remote never gave out lists from the start once, which costs a
  re-walk and no writes.
- **The rules are the mirror's**, one page at a time as in
  `sync_engine._sync_page`. A page deleted on one side goes on the other
  unless the other side edited it. The remote's changes since the saved
  base are applied here, and an edit beats a delete in both directions.
  Only this copy's own edits are pushed. A push whose answer was lost is
  confirmed and sent again only where the remote does not show it. A
  block the remote moved to another page moves here too, carrying what
  was typed in it here.
- **Folders and labels are two more snapshots**, `folders` and `labels`
  ([collab.md](collab.md) "The folder and label trees"; `tree.js`
  `TREES`). The feed lists them like pages, and a round reconciles them
  first, so a page filed in a new folder never arrives before its
  folder. The root of each is a reserved row whose `parent` is `null`. A
  folder is a block under `folders` or under another folder, with its name
  as `content` and its pin as `props.pinned`. A label is a child of
  `labels`. A tree is never created or deleted on either side: the remote
  refuses a page with a reserved id, so a tree the remote lacks is left as
  it is, and a tombstone for one deletes nothing. A tree missing here,
  before the first round that lists it, is pulled whole (its snapshot
  stored). Both sides' folders are kept, since each side's pages are filed
  in its own. A page names its filing as block ids,
  `props.folders` and `props.labels`.
- **The library's rows** come from `views.js libraryRows(roots, {folders,
  labels})`, where `roots` is every stored page root and the second
  argument holds the two trees' snapshots (either may be missing until a
  round brings it). The tree roots get no row. A row is `{id, title,
  kind, position, folders, labels}`, ordered by position. `folders` lists
  the page's folders as display paths, the names from the top joined
  with `" / "` (`"Physics / QEC"`), and `labels` lists the label names.
  Both follow the page's filing order, with no repeats. A name may contain
  any character, `/` and `,` included, so the host groups by these
  strings and never splits them. An id the trees lack (a dangling id: the
  folder deleted, or not here yet) gives nothing. `pageView` carries the
  page's raw filing ids as `folders` and `labels`. The web app reads the
  same names with `libraryUtils.js` `filingChips` over the listing's trees.
- **The code is JavaScript,** a port of the pure half of the Python
  engine that runs in the app's JavaScriptCore. `frontend/src/replica/tree.js`
  is `sync_tree.py`, and `reconcile.js` holds `_known`,
  `_reconcile_remote_ops`, `_own_edits`, `_split`, `_strays` and
  `_unlanded`. `round.js` is `_sync_page` and `_round`, and `textmerge.js`
  is `textmerge.py` over the same diff-match-patch. The replica applies
  the remote's ops itself, with the server's merge rules
  (`tree.applyLocal`: a text edited from an older base merges, a drawing
  edited from an older `ink_url` merges by stroke, an insert of a known
  id changes nothing).
- **Local writes during a round** use versions instead of the Python
  engine's op-log scan (`_touched`). The round reads a page with its
  version and writes the merge only if the page is still at that
  version, otherwise it reads again. It then acknowledges the version it
  pushed from, so an edit made during the push stays marked for the next
  round.
- **The host** is where the replica lives: the device's storage,
  network and files. `round.js` names its interface, and the iPad's is
  Swift (`ipad/GammaIPad/Core/Replica.swift` over SQLite and URLSession).
  The tests' host keeps it in memory (`frontend/tests/replica/memoryHost.mjs`).
  The host only reads and writes. Every decision is the shared JavaScript's.

Two implementations of one logic need a pin. The pure rules have shared
fixtures, which both the Python engine and the JavaScript port read:

- `tests/shared/synctree.json`: diff, apply, moved and `_unlanded`, with
  outputs taken from the Python reference.
- `textmerge.json`: merge, contains and map_offset.
- `inkmerge.json`: the stroke merge.
- `paper.json`: notebook paper.

The rounds themselves run against a real server in the browser suite's
`replica` group (below).

### Ink on the iPad

The iPad captures the Pencil itself, and stores exactly what a browser
stores.

- **Capture.** A Pencil-only gesture recognizer (`Ink/InkPageView.swift`
  `PencilRecognizer`) takes the coalesced touches, every sample the
  Pencil measured. Each sample carries `preciseLocation`, force over
  `maximumPossibleForce`, and the hardware timestamp, in ms since the
  stroke's first sample. A touch that reports no force, such as the
  lift, keeps the last pressure, as `ink/inkInput.js` does. Predicted
  touches only extend the preview and are never stored. Fingers keep
  PDFKit's scrolling, zooming and text selection, because its scroll
  views accept direct touches only.
- **Encoding.** The stroke is `ink.js encodeStroke` in the core, with
  `ch: "xypt"` like a browser pen and `t0` as wall-clock ms rounded to an
  integer (the schema's `t0: int`).
- **Frame.** A PDF page's ink is in pdf.js's viewport frame at scale 1,
  rotation applied. The core's `viewportTransform` is pdf.js's
  `PageViewport` transform. `Reader/PDFReader.swift` composes its inverse
  with PDFKit's page-to-overlay mapping, read off three points, so
  rotated pages and every zoom land in the same frame. A notebook
  sheet's ink is in the sheet's own frame, points from its top-left.
- **Saving** is `replica/edits.js saveInk`, the browser's `flushInk` on
  the device. The file is written as `serializeInk` bytes and named by
  their hash. The block properties come from `inkProps`. The base is the
  file the strokes were drawn onto. An emptied group follows the same
  rule as in the browser.
- **Drawing** comes from the core too: a pen stroke's outline polygon
  (`perfect-freehand` with `outlineOptions`) is filled as the web's
  `svgPathFromPoints` path, and a highlighter's centre line is stroked
  and multiplied. The live stroke is a plain line at the tool's width
  until the outline replaces it.
- **Undo and redo** rebase by stroke id (`mergeInk`), like the browser's.
- **Replay** runs on the core's `inkTimeline`, the browser's
  ([handwriting.md](handwriting.md) "Replay"). `InkSession.replay` takes
  the timeline once. On each frame (a main-actor task, about 60 a second)
  it rebuilds the group's geometry: the strokes finished keep their
  geometry, and the one being written is cut to its first samples (a
  prefix of its `pts`) and passed to `strokeGeometry`. The frames go out
  on `replayFrames`, a Combine subject rather than a published value, so
  only the page the group is on redraws and SwiftUI does not. An edit to
  the group, or the store changing it, ends the replay.
- **Sheets.** `pageView` lists a page's sheets (`sheets`); its kind is
  `pdf` or `page`, since nothing else marks a notebook. Writing low on
  the last sheet adds the next, once (edits.js `addSheet` with `after`
  and `once`, as the browser's `addSheetAfter` does). "Add page" in the
  reader adds one after the last sheet. A new sheet is folded and takes
  the paper of the sheet before it.

Why not PencilKit: its `PKDrawing` is an opaque format no server or
browser reads ([research/handwriting.md](../research/handwriting.md)).
Converting to and from it resamples strokes and loses their identity.
Raw touches carry everything `gamma-ink` stores.

### Tests

- `ipad/scripts/core.test.mjs` runs the bundle in a bare context with no
  browser or Node globals. It covers ink, paper, the viewport transform,
  a notebook made through a synchronous host, the replay's timeline, and
  a page placed among a note's blocks. It runs in `check.yml` and
  `ipad.yml`.
- `frontend/tests/replica.test.mjs` covers the tree rules against the
  shared fixtures, the local merges, edit-beats-delete, the page views and
  the library rows. Rounds against a remote in memory cover the folder and
  label trees: reconciled first and pulled whole, both sides' folders
  kept, never deleted either way, and never created on a remote that
  lacks one.
- The browser suite's `replica` group (`frontend/tests/e2e/scenarios/replica.mjs`,
  no browser) runs rounds against a real server: the first fill, the
  folder and label trees both ways with the library rows named from them,
  pushes, text and drawings merged on both sides, an edit beating a delete
  both ways, a lost answer, a block moved between pages, deletions both
  ways, and receive-only.
- `ipad/GammaIPadTests` (XCTest, on macOS in `ipad.yml`) covers the
  store's version, edit-mark and tombstone semantics, file names, and the
  bundled core through the Swift host (the replay's order, a page among a
  note's blocks).

### Not built yet

- A highlight from a text selection. PDFKit selects, and the web view
  highlights.
- Sheets drawn in place in a page's outline, as the browser's notes view
  draws them (the notebook view shows them instead), and sheets in a PDF
  page's notes (the PDF is that page's reader).
- The lasso, the partial eraser, moving and restyling strokes, and
  editing presets beyond colour and width.
- Text boxes ([text_boxes.md](text_boxes.md)). The app neither draws them
  on a PDF page or a sheet nor makes them. A box shows as a note row with
  its text, which edits like any note. The browser redraws the box at the
  new text's size and stores that size at its next local edit of the box.
- A per-page choice of what to keep offline: every file comes over, like
  a desktop clone.
- Sync in the background, and resolving a conflict from the app (the
  list is for looking; the web app's clone view resolves).

### Following the server's data model

Schema versions 25–30 changed what the server stores and answers
([migrations.md](migrations.md)). The JavaScript core follows them and its
tests pin it (`frontend/tests/replica*.mjs`, `ipad/scripts/core.test.mjs`);
the app ships the core built from the current `replica/`
(`ipad/core/entry.js`). The Swift code reads two things from it:

- **The library** (`Replica.libraryRows`, `LibraryView.swift`) passes the
  stored page roots and the two tree snapshots (`store.snapshot("folders")`
  / `("labels")`) to `libraryRows` above. The view groups by a row's first
  folder path as it is, and its
  search box matches titles, paths and labels. A page the app makes
  (`createPage`, `createNotebook`) is filed nowhere; filing is the web
  app's.
- **An ink row's page** (`NotesView.swift`) is `pdf_position.pageNumber`;
  `pdf_page` is a text box's only (the app shows no text boxes). The core's
  `pageView` keys `pdfInk` by the position's page and derives `pdf.url` from
  `doc_id` when a page stores no `source_url`.

The replica never reads `actor` (an account id), the block dict's `page_id`
and `kind` (it ignores them and sends neither back), or the library listing
endpoint (the app's library is the replica's).

A replica synced before the server's step 30 keeps its pages' old highlight
shape until the server lists each page again (the step touched no page).
The core reads the new shape only, so an ink group's page shows once its
page has come over again: `Sync now` after the server's upgrade does it.

## The installed web app

The home-screen app is the web app installed to the home screen. Its
browser ink layer already gives Apple Pencil pressure, tilt, hover, palm
rejection and Safari's touch-gesture handling
([handwriting.md](handwriting.md)). Every other feature (the block editor,
search, AI, sharing, workspaces) is the same React code the desktop shell
hosts. [research/ipad.md](../research/ipad.md) has the reasoning, and
what the upstream fork's PDFKit/PencilKit app was measured against.

### What the user does

1. Open the server's URL in Safari on the iPad and sign in.
2. Share button → **Add to Home Screen** → Add. Chrome and Edge on a
   desktop or Android tablet offer **Install Gamma** in the address bar
   for the same result.
3. Gamma opens full screen from the icon, without Safari's toolbars,
   signed in as before (the session cookie lives a year; the installed app
   has its own cookie jar, so the first launch may ask once).

Writing then works as everywhere else: a Pencil draws right away, fingers
scroll and pinch, the tool strip's presets are per browser (the installed
app counts as one browser, separate from Safari).

### What the install adds, and where it lives

| Piece | Where | Notes |
|---|---|---|
| The manifest | `frontend/public/media/manifest.webmanifest` | `display: standalone`, `start_url` `/` (the app then picks the last used workspace), the three PNG icons. Unversioned, so under `/media/` (sent `no-cache`, [repository.md](repository.md)). The backend registers its media type, `application/manifest+json`, because Windows and slim images lack it (`gamma/app.py`; `tests/test_static.py`). |
| The icons | `frontend/public/media/icons/apple-touch-icon.png` (180), `icon-192.png`, `icon-512.png`, `icon-maskable-512.png` | Rendered from the one brand mark by `tools/branding/build.mjs` like every other icon ([design/brand](../../design/brand/README.md)); never edited by hand. The `bleed` option of `mark.mjs` gives them a full-bleed square plate — the OS masks the corners itself, and iOS paints transparent corners black — and the maskable one keeps the mark inside the inner 80%. |
| The head tags | `frontend/index.html` | `manifest`, `apple-touch-icon`, `apple-mobile-web-app-capable` / `mobile-web-app-capable`, the title, `apple-mobile-web-app-status-bar-style` **default**; the pre-paint script adds `theme-color` for the stored theme (below). |
| The status bar colour | `index.html`'s pre-paint script, then `app/App.jsx`, `paintStatusBar` (the theme effect and the phone-topbar effect) | With the *default* status-bar style the bar sits above the viewport and is painted with `theme-color`. Before the bundle loads, the pre-paint script writes the resolved theme's chrome colour from a small table of each theme's `--bg-page` (`tests/themes.test.mjs` holds it to the tokens), so a cold start never shows another theme's bar. From then on the meta is set to the topbar's background (the chrome, `--bg-page`) whenever the theme changes, so the bar continues the topbar for every theme — or to the library's (the content surface, `--bg-surface`) while the compact layout shows no topbar. Both are resolved to a hex first (`tokenHex`): most tokens are `color-mix()` expressions. `black-translucent` was rejected: it puts content under the bar with fixed light text, wrong on the light themes. |
| Standalone-mode CSS | `shared/styles/app.css`, `@media (display-mode: standalone)` | The document stops rubber-banding (`overscroll-behavior: none` on html/body; the panes still scroll) and `.app` pads `env(safe-area-inset-bottom)` for the home indicator. There is no top inset to absorb with the default status bar. |

### Layout by orientation

An iPad's Safari sends a desktop-class UA, so the layout follows the
screen, not the device (`PHONE_MQ` / `useIsPhone` in App.jsx):

- **Upright** — a coarse pointer, portrait, at most 1024px wide (every iPad
  up to the 12.9" Pro, and Android tablets) — gets the compact shell the
  phone uses (`.phoneUI`): the PDF or the notes full width, Notes and Chat
  as full-screen panels, one bottom bar (below). Two docks in 820px would
  leave ~200px columns, too narrow for the chat header's buttons. At this
  width the bar's items are roomier and its popovers keep
  a menu's width at the right end instead of spanning the screen (the
  `min-width: 701px` block under the phone rules in `app.css`).
- **Landscape** gets the desktop docks, as on a laptop. Turning the tablet
  swaps the shell live; the dock arrangement is kept for the way back.
- A phone keeps the compact shell both ways: 700px wide or less, or a
  coarse pointer on a screen at most 500px tall.

#### The compact shell

- **The bottom bar** (`.phoneBottomBar`): view tabs on the left — Library
  (home: the topbar has no Home button here, so it stays on a page's bar
  and carries the `header.home` anchor), the page's PDF and Notes, Chat —
  and the topbar's actions on the right: Add, Search, a page's Share (and
  a clone's Sync pill), More. Every item is a 22px icon over its word
  (`.barLabel`, `--fs-2xs`, hidden on the desktop topbar), about 50px tall.
- **More** is the account button (`header.account`) under another name:
  its sheet is the account menu with the rest of the topbar on top —
  Background tasks, the open folder's Share this folder, and the View
  menu's rows (`viewMenuItems`; of the window toggles only PDF, since the
  tabs switch Notes and Chat). The tasks, folder-share and View buttons are
  not rendered; their popovers open from those rows in the sheet's place.
  While something runs, More shows the tasks spinner.
- **The topbar** holds only tabs and Back, so with neither (a library with
  no tab open) it is not shown (`.topbar.phoneBare`), and theme-color
  switches to the library's `--bg-surface` so the status bar continues it
  (`paintStatusBar` in App.jsx).

Nothing else is tablet-specific: the viewport meta already disables
browser zoom in favour of each viewer's own pinch-zoom (the PDF's and the
notebook's — [notebooks.md](notebooks.md)), `touch-action:
manipulation` removes double-tap zoom, and both layouts carry the touch
rules the ink layer and the viewer already have.

### Not built (and why)

- **No service worker or offline shell.** Installability on iOS does not
  need one, and a cache layer would sit on top of the asset cache rules
  in [repository.md](repository.md). Offline reading and writing is the
  native app's job ([above](#the-native-app)).
- **Pencil double tap and squeeze** are not exposed to web content by
  iPadOS. Pencil hover is exposed (a `pen` pointer with no buttons) and
  already shows the tool footprint. The native app handles the double tap.
- **No wrapper around the web app.** The native app is not a
  `WKWebView` shell: it reads and writes its replica natively, and opens
  the web app in a web view only for what it does not do itself.

### Tests

- `backend/tests/test_static.py`: the manifest's media type and cache
  header.
- `tests/e2e/scenarios/ipad.mjs` (`npm run e2e -- --only ipad`), in
  Chromium touch contexts:
  - Tablet-sized: the manifest parses with `standalone` display, and every
    icon it and the `apple-touch-icon` link name is a real PNG.
    `theme-color` matches what sits under the status bar (the library with
    no tab open, the topbar once one is) and follows a theme change. The
    bundled stylesheet carries the standalone block (the document
    overscroll rule and the home-indicator inset). Chromium cannot emulate
    `display-mode`: CDP accepts the feature, but `matchMedia` ignores it.
  - Upright (834×1194) is the compact shell: the PDF full width, Notes full
    screen from the bottom bar. Rotating to landscape brings the docks back.
  - A 390px phone: no topbar without tabs, the bar's words (Library Chat
    Add Search More, then Library Notes Chat Add Search Share More on a
    page), the listing bar inside the screen, a long title wrapping with its
    chips under it, More's sheet opening Background tasks, and the Library
    tab going home.
  - Only the device can check the Add to Home Screen flow, the status-bar
    paint and the home indicator.
- `node tools/branding/build.mjs --check` pins the icons to the mark.
