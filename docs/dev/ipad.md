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
   the end of the notes adds a sheet after the last. While a note is
   edited, a bar over the keyboard nests, moves, adds and formats notes
   ([Editing the notes](#editing-the-notes)).
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

### Editing the notes

`NotesView` lists a page's blocks as text fields, indented by depth. A
note's text is sent as `setText` from the text it was edited from, so a
text changed meanwhile merges. While a field has the keyboard, the editing
bar (`Views/NoteEditBar.swift`) sits on it. It is the web app's bar
([below](#the-editing-bar)), with the same tools in the same order but
Next math argument:

**Insert** · Outdent · Indent · Move up · Move down · New block · Bold ·
Italic · Inline code · Strikethrough · Link · Inline equation · Undo ·
Redo … **Done**

- **Where it sits.** It is the notes' bottom safe-area inset, and the
  keyboard takes the safe area, so the bar rides on the keyboard without
  measuring it. With a hardware keyboard it sits at the bottom of the
  notes. Where the notes are narrow (the inspector beside a PDF), the
  tools scroll sideways and Done stays.
- **The outline.** Outdent, Indent, Move up, Move down and New block are
  `edits.js` `outdent`, `indent`, `moveBlock` and `addNoteAfter`. Each is
  the browser's own change of the page's tree (`blockModel.js`
  `outdentBlock`, `indentBlock`, `moveSibling`, `insertSibling`), sent as
  the ops the browser sends for it (`blockOps.js` `diffTrees`). A button is
  greyed out where the web's is: Outdent at the top level, Indent and Move
  up on a first child, Move down on a last.
- **Undo and redo.** Each outline edit returns its undo, and `OutlineUndo`
  registers it with the window's undo manager, which the bar's Undo and
  Redo run.
  - An undo is a list of placements: where each block the edit moved, made
    or took away stood, as its parent and the sibling it followed.
    `edits.js` `restore` puts them back and returns the redo.
  - The keys are worked out when the undo runs, as the browser's undo
    does. A round may have re-keyed a block meanwhile: the server re-keys
    an insert whose key a sibling has.
  - Whether the text fields' typing and the keyboard's own undo key use
    the same manager is a device check.
- **Text commands.** Bold, Italic, Inline code, Strikethrough, Link and
  Inline equation are the web editor's own plans (`markCommands.js`
  `markPlan` with `**`, `*`, `` ` `` and `~~`, `linkPlan`, `mathInsertAt`).
  - Insert lists the `/` menu's text insertions (`slashInserts.js`) and,
    on a page without a PDF, a page to write on after the note.
  - The core's `format` and `insert` turn a plan into one replacement and
    the selection after, in UTF-16 offsets. `KeyboardField` makes the
    replacement through the field's own `UITextInput`, so it undoes like
    typing.
  - A link's address is not taken from the clipboard as the web's is:
    reading it raises iPadOS's paste prompt.
- **Done** takes the keyboard away, which commits the note.

### Tests

- `ipad/scripts/core.test.mjs` runs the bundle in a bare context with no
  browser or Node globals. It covers ink, paper, the viewport transform,
  a notebook made through a synchronous host, the replay's timeline, a
  page placed among a note's blocks, the editing bar's outline edits with
  their undo and redo, and its text commands. It runs in `check.yml` and
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
  pushes, the editing bar's outline edits with their undo and redo, text
  and drawings merged on both sides, an edit beating a delete
  both ways, a lost answer, a block moved between pages, deletions both
  ways, and receive-only.
- `ipad/GammaIPadTests` (XCTest, on macOS in `ipad.yml`) covers the
  store's version, edit-mark and tombstone semantics, file names, and the
  bundled core through the Swift host (the replay's order, a page among a
  note's blocks, an outline edit and its undo). The editing bar itself
  (`NoteEditBar`, `KeyboardField`, `OutlineUndo`) has no automated test;
  it is checked on a device.

### Keeping the host in step

Nearly everything the app knows about the server is the shared
JavaScript, so a protocol change is made once, in `frontend/src/replica/`,
and the checks above carry it to the app: `check.yml` bundles the core
and runs `core.test.mjs` on every pull request, the shared fixtures pin
the pure rules against the Python engine, and the `replica` group runs
rounds against the real server. The Swift host decides nothing; a rule
that would need a Swift change belongs in the core.

What is Swift's alone is small: the web session's shape and the write
token's minting (`Core/Remote.swift` `ServerSetup`), files moved by name
through `/api/uploads` and `/api/upload-file` (`Remote.upload`, `head`,
`download`), and the upload-reference pattern (`Core/Store.swift`).
`backend/tests/test_ipad_contract.py` pins those four answers from the
server's side, with a comment naming the Swift that reads each one. A
backend change under `routers/sync.py`, `routers/collab.py`, `ops.py`,
`sync_engine.py`, `sync_tree.py`, `textmerge.py`, `routers/uploads.py`,
`routers/integrations.py` or the session's answer runs, in this order:

```bash
cd backend && venv/Scripts/python.exe -m pytest tests/test_ipad_contract.py tests/test_shared_fixtures.py tests/test_sync_feed.py -q
node ipad/scripts/build-core.mjs && node --test ipad/scripts/core.test.mjs
cd frontend && npm run e2e -- --group replica,ipad
```

A Swift change (`ipad/**`) also runs the XCTest suite on a Mac, which
`ipad.yml` does in CI. When a server answer the Swift reads changes,
change `test_ipad_contract.py` and the Swift together, and say so in
the step's row of [migrations.md](migrations.md) when stored shapes are
involved, as "Following the server's data model" below does for the
core.

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
- The rest of the web outliner in the notes. Return makes no new note,
  and Backspace in an empty note does not delete it. A typed `/` opens no
  menu, and Insert offers no links, embeds, images or new pages. Notes do
  not fold, and a note's markdown shows as its source.
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
| Standalone-mode CSS | `shared/styles/app.css`, `@media (display-mode: standalone)` | `.app` pads `env(safe-area-inset-bottom)` for the home indicator. There is no top inset to absorb with the default status bar. The document's `overscroll-behavior: none` (html/body; the panes still scroll) sits outside the block: a Safari tab gets it too. |

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
- **A page with sheets** ([notebooks.md](notebooks.md)) has Notebook and
  Notes tabs in place of the page's tab and the Notes panel's: they switch
  between its two views (`setNotebookView`), which close any panel over
  the page.
- **Panels and jumps.** Notes and Chat show as full-screen panels whose
  title is a plain label: no "⠿" grip, since a panel neither drags nor
  folds. A jump from the notes to the page (a handwriting or text box
  row's, a highlight's) closes the panel over it (`showOnPage`,
  `jumpToHighlightId`), and Show note on the page brings the notes up
  (`showInNotes`): the Notes panel, or on a page with sheets its notes
  view.
- **More** is the account button (`header.account`) under another name:
  its sheet is the account menu with the rest of the topbar on top —
  Background tasks, the open folder's Share this folder, and the View
  menu's rows (`viewMenuItems`; of the window toggles only PDF, since the
  tabs switch Notes and Chat, then Undo, Redo, Go to page and Command
  palette). The tasks, folder-share and View buttons are
  not rendered; their popovers open from those rows in the sheet's place.
  A flyout (the account menu's Tours) opens inline under its row, since
  beside it the sheet's own scroll box would clip it. While something
  runs, More shows the tasks spinner.
- **The topbar** holds only tabs and Back, so with neither (a library with
  no tab open) it is not shown (`.topbar.phoneBare`), and theme-color
  switches to the library's `--bg-surface` so the status bar continues it
  (`paintStatusBar` in App.jsx).

### The editing bar

The notes take Tab, Shift+Enter and Ctrl chords for their structure and
formatting. The iPad's on-screen keyboard has no Tab or Ctrl, and no
dependable Shift+Return ([research/touch.md](../research/touch.md)). So
while a note's editor is open on a touch screen, a bar sits on top of the
keyboard (`editor/EditBar.jsx`):

**Insert** (the `/` menu) · Outdent · Indent · Move up · Move down · New
block · Bold · Italic · Inline code · Strikethrough · Link · Inline
equation · Next math argument · Undo · Redo … **Done**

The native app's notes have the same bar ([Editing the notes](#editing-the-notes)).

- **Every tool is a command.** All but Undo and Redo are block commands
  ([hotkeys.md](hotkeys.md)), run with the context the row's keydown
  dispatches with (`commandContext` in `BlockTree.jsx`). So a button does
  what the same command does from a key or the palette.
  - Four of them exist for the bar and have no default chord:
    `block.insertMenu`, `block.newBelow`, `block.math` and
    `block.nextSlot` (each is described in [hotkeys.md](hotkeys.md)).
  - Undo and Redo are the page's block history (App's `undoBlocks`), as
    Ctrl+Z.
  - A button whose command's `when` fails is greyed out: Outdent at the
    top level, Indent and Move up on a first child, Move down on a last.
  - Next math argument is Tab's hop in a math snippet (the next `{}`
    slot, past a `\right`, out of the span), which an on-screen keyboard
    has no key for. It is the one transient tool (`TRANSIENT`): it shows
    only while its `when` holds, the caret in math with somewhere to go,
    instead of greying out.
  - Done blurs the editor, which closes it and the keyboard.
- **When it shows.** It shows when the primary pointer is coarse, or the
  last press was a finger or a pen (a touch laptop). Where the primary
  pointer is fine, a mouse or trackpad press never brings it. It is read
  once when the editor opens (`touchTyping()` in `shared/lib/pointer.js`,
  [ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)). A
  read-only page opens no editor, so it has no bar.
- **What it looks like.** The markup tool strip's surface and separators
  (`.pdfInkBar`, `.pdfInkSep`), centred and floating 6 px above what it
  sits on. Its `ctlBtn`s carry the strip's 16 px icons and are 36 px, the
  ink menu's finger size.
- **Where it sits.** It is `position: fixed`, its `top` set from the
  visual viewport's bottom, the top of the keyboard (`measure`). iPadOS
  does not shrink the layout viewport for its keyboard, and `top` is
  measured in the layout viewport, as `visualViewport.offsetTop` is.
  - iPadOS moves the visual viewport itself: the keyboard slides, and a
    scroll pans the view under the keyboard. Safari draws that move at
    once, but a script can only follow a frame later, so a bar chasing it
    would shake. Instead the bar steps aside while its place keeps
    changing (`.moving`: invisible, not pressable). It fades back in once
    its place has been still for 150 ms (`useBarPlace`).
  - Opening an editor counts as a move for 350 ms, the keyboard's rise, so
    the bar appears with the keyboard rather than under it.
  - The events do not always come at the end of a move, and none come when
    the keyboard returns to an editor that kept the focus. So the
    viewport's and the window's events, a focus change and a tap each
    watch the place for a second, a frame at a time.
  - It never sits lower than the top of the compact shell's bottom bar.
    That bar must stay reachable when no keyboard is up: a new page opens
    its first block's editor by itself, and a hardware keyboard raises
    none.
  - With neither a keyboard nor a bottom bar, it sits on the screen's
    edge, clear of the home indicator (`.editBar.atEdge`).
  - Its layer, `--z-editbar`, is over the compact shell's full-screen Notes
    panel and under the editor's own popups.
  - The room it takes at the visual viewport's bottom (`editBarSpace()`)
    is the editor's bottom scroll margin, so the caret scrolls into view
    above it. Caret-anchored popups (the slash menu, the math preview,
    `useCaretAnchored`) leave that room free.
  - On a narrow screen the tools scroll sideways and Done stays at the end.
- **The keyboard stays up.** iPadOS keeps it while focus stays in the
  editor, and raises it again only for a `focus()` made during a gesture.
  - A press never takes focus: the bar cancels `pointerdown` and
    `mousedown`, and its buttons have `tabIndex` −1.
  - A command runs inside `flushSync`, so a row that re-renders, or
    remounts, does so within the tap.
  - Indent and outdent remount the row. App keeps its editor open through
    that (`keepEditorThrough`, [hotkeys.md](hotkeys.md#the-block-commands-plumbing)),
    and the bar puts the caret back in the same tap (`keepCaret`).
  - A Backspace in an empty block moves on to the end of the block shown
    above (`onDelete`'s `focusAbove`).
- **The keyboard's own undo.** The iPad keyboard's undo and redo keys, and
  the three-finger swipes, send `beforeinput` `historyUndo` /
  `historyRedo`. A note row's `BlockCmEditor` hands them to the block
  history (its `onUndo`), so the browser never undoes CodeMirror's DOM
  there. An editor without `onUndo` (a text box's, an embed card's) leaves
  them to the browser.
- **The keyboard's own formats.** The iPad keyboard's B and I, and the
  Format menu over a selection, send `beforeinput` `formatBold`,
  `formatItalic` and `formatStrikeThrough`. Every `BlockCmEditor` runs
  `block.bold`, `block.italic` and `block.strike` for them, the commands
  of Ctrl+B, Ctrl+I and Ctrl+Shift+X.
- **Typing aids.** CodeMirror switches the system's typing aids off. In
  prose the editor turns them back on (`autocorrect`, `autocapitalize`
  sentences, `spellcheck`, `writingsuggestions`); in a code fence or math,
  where a corrected word is a broken command, they stay off. The caret's
  place decides as it moves (`typingAids` in `BlockCmEditor.jsx`).

Apart from the bar and the compact shell, the touch rules are the ones the
ink layer and the viewers already carry, and the shared ones every control
follows ([ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)).
The viewport meta asks for no browser zoom, leaving pinch-zoom to each
viewer (the PDF's and the notebook's, [notebooks.md](notebooks.md)).
Safari tabs ignore it, so on any touch screen, in either layout, the app
also refuses Safari's gesture events (`touchScreen()`, `App.jsx`); the
viewers' own pinch runs off `touchstart` / `touchmove` and is unaffected.
`touch-action: manipulation` removes double-tap zoom, and
`overscroll-behavior: none` on html and body keeps the document from
rubber-banding or pulling to refresh, in a Safari tab as installed; only
the panes scroll.

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
- `frontend/tests/editBar.test.mjs`: the Insert and Inline equation edits
  (`slashInsertAt`, `mathInsertAt` in `editor/markCommands.js`; neither
  applies inside math or a code fence), where Move up / down, Indent and
  Outdent apply (their `when`), Next math argument's hop and where it
  applies, and the bar's four commands starting unbound.
- `tests/e2e/scenarios/ipad.mjs` (`npm run e2e -- --only ipad`), in
  Chromium touch contexts:
  - Tablet-sized: the manifest parses with `standalone` display, and every
    icon it and the `apple-touch-icon` link name is a real PNG.
    `theme-color` matches what sits under the status bar (the library with
    no tab open, the topbar once one is) and follows a theme change. The
    document's overscroll is off, and the bundled stylesheet carries the
    standalone block (the home-indicator inset). Chromium cannot emulate
    `display-mode`: CDP accepts the feature, but `matchMedia` ignores it.
  - Upright (834×1194) is the compact shell: the PDF full width, Notes full
    screen from the bottom bar. Rotating to landscape brings the docks back.
  - A 390px phone: no topbar without tabs, the bar's words (Library Chat
    Add Search More, then Library Notes Chat Add Search Share More on a
    page), the listing bar inside the screen, a long title wrapping with its
    chips under it, More's sheet opening Background tasks, and the Library
    tab going home.
  - The editing bar, upright, by tap. A tap on a note opens it with the bar
    floating centred above the bottom bar, its buttons 36 px wide, Outdent
    greyed out at the top level.
    - A stand-in visual viewport plays the keyboard. The bar lands on it
      when its event comes before the move, stays on it when the view
      scrolls, and finds it back on the next tap when it returns with no
      event. Through a pan that goes on for 40 frames it never shows, and
      it shows where the pan stops.
    - Indent, Outdent and Move down change the saved tree, and after each
      the same block's editor still has the focus.
    - New block focuses a new empty block.
    - Bold wraps the selection; Undo and Redo take it back and put it
      back, and so do the keyboard's `historyUndo` / `historyRedo` input
      events.
    - Inline equation inserts `$x$`, its x typed over.
    - Insert opens the slash menu, and a tapped item applies.
    - Done closes the editor and the bar.
    - A mouse context's editor has no bar.
  - Only the device can check the Add to Home Screen flow, the status-bar
    paint and the home indicator. It is also needed for these editing-bar
    behaviors:
    - the bar riding on the on-screen keyboard (emulation has none)
    - the keyboard staying up through every button, Indent and Outdent
      included
    - the keyboard's own undo key sending `historyUndo`
- `node tools/branding/build.mjs --check` pins the icons to the mark.
