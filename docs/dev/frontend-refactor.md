# App.jsx decomposition plan

The file organization is in place ([source map](../../frontend/src/README.md):
functional folders directly under `src/`, alongside `app/` and `shared/`,
without an extra `features/` layer), and so is the startup bundle split
([Lazy boundaries](#lazy-boundaries)). The state decomposition below is
still a proposal: modules have moved, their state ownership has not been
redesigned, and `app/App.jsx` is about 11,000 lines. Two steps are taken:
the block tree holds the document only, and the viewer's own state (the open
editor, their folding) is App's `view` beside it
(`shared/model/blockModel.js`, [collab.md](collab.md)); the AI connect
dialog's form state is `useProviderEditor` (`settings/providerEditor.js`).
Everything from "Goal and current constraints" on is open work.

## Lazy boundaries

Built, unlike the plan below. The startup bundle is `main.jsx` plus the
`App` chunk it imports once the locale catalog is in. What the first screen
does not show comes in chunks of its own, through `lazySurface` and
`LazyBoundary` (`shared/ui/lazy.jsx`):

| Surface | Mounts | Shown while it loads |
|---|---|---|
| Settings dialog and its panes | on first opening, then stays mounted (`useLatch`) | nothing |
| Chat dock | with its dock window | the window's frame (`DockWindow`), body empty |
| Guide overlay (`guide.css`) | when a tour, offer or finish card first shows, then stays | nothing |
| Ctrl+P / Ctrl+Shift+P palette | on first opening, then stays | nothing |
| Import, Export, import review, Recently deleted, Report a problem | while open | nothing |
| Share popover | while open | nothing |
| pdf.js and its worker | `loadPdfEngine()` in `pdf/PdfViewer.jsx`, awaited just before `getDocument` | the viewer's skeleton and status pill |

Mermaid was already loaded this way (`shared/lib/mermaidRenderer.js`).

The preload rule: no click waits on the network for a chunk, and the first
screen downloads only what it paints.

- A surface opened from a button is fetched when the pointer enters or focus
  reaches the button: the account button (Settings, Tours, Report a problem,
  and the View rows a phone shows there), the View menu (Import/Export, the
  AI Chat toggle), Share, Recently deleted. The import review is fetched
  while the Import dialog is open, since a picked file opens it already
  uploading. The palette has no button: it is fetched when the main thread
  is idle after the library is up.
- A surface on the first screen is fetched with the startup requests: the
  chat dock on a desktop, the PDF engine when the address names a page, a
  share or a PDF. On the library the engine waits until the listing has been
  in for 1.5 s and the main thread is idle, so it is warm for the first PDF
  without competing with the library.
- What every page paints at first render stays in the `App` chunk: the
  block editor (BlockTree and CodeMirror; an empty page opens with its seed
  block in the editor), note rendering (react-markdown, rehype-raw and its
  parse5, KaTeX, highlight.js), the PDF viewer component, the ink and markup
  layers.

Adding one: import the module only through `lazySurface` (one static import
anywhere in the `App` chunk's graph puts it back), and move the small
exports App itself needs into a module of their own, as
`settings/providerEditor.js`, `settings/workspaceRoles.js` and the
`settingsNavigation.js` / `settingsSearch.js` split did. A surface that
used to stay mounted while closed mounts on first use and then stays, so its
state between openings is kept. CSS imported by a lazy module ships with its
chunk and is appended after the `App` CSS: check that none of its rules ties
with one that used to follow it. A chunk that cannot be fetched (a deploy
replaced the hashed files under an open tab) leaves the fallback in place
instead of unmounting the app; a reload fetches the new files. Sizes and
what each first screen downloads: [the bundle note](../research/bundle.md).

## Goal and current constraints

`frontend/src/app/App.jsx` still combines session checks,
library mutations, account preference synchronization, page loading and saving,
PDF state, AI provider state, dock geometry, and most workspace markup.

The goal is a small composition root with explicit state owners and commands.
Extraction should remove coupled responsibilities, not merely move long
sections into hooks that accept every variable from App. Keep the existing
React stack, URL formats, API contracts, and stored preference keys. Follow
the [block-centric model](block_centric.md): a page owns its block tree and
may carry a PDF attachment.

## Proposed ownership

Paths below are relative to `frontend/src/`. The folders exist except
`app/preferences/`, `pages/` and `workspace/`; the hooks and state owners in
this table are proposals.

| Module or area | Responsibility | Existing code to extract |
|---|---|---|
| `app/App.jsx` | Compose session gate, navigation, active page, library, and workspace shell | The final route/view decision and connections between features |
| `app/useSession.js` | Identity, login/logout, conflict detection, session refresh | `checkSession`, `doLogin`, `doGuestLogin`, session event listeners |
| `app/navigation.js`, `app/useNavigation.js` | Parse/build URLs, home/page transitions, link-jump back stack | `homeUrlFor`, initial query parsing, `openBlock`, `goHome`, `pushNav`, `goBackNav` orchestration |
| `app/preferences/` | Account-scoped tabs, recents, appearance, and reading positions | `pushPrefSoon`, `applyServerTabs`, read-position and recents synchronization |
| `pages/usePageSession.js` | Active page identity, tree, load state, and metadata updates | Page fields, `loadBlocksForBlock`, the data-loading portion of `openBlock` |
| `collaboration/` (existing: `usePageCollab.js`) | Queued writes, debounce, retries, explicit flush, unload handling — the live session hook `usePageCollab` ([collab.md](collab.md)); App keeps the transition effect and the remote-apply glue | `commit`, `flush`, `onRemoteOps` |
| `editor/` | Block editing, caret/focus, undo, and notes rendering | Existing editor files, `editTail`, notes-window markup and editor actions |
| `library/` | Listing derivation, selection, page/folder/label operations, and library UI | `pageBlocks` through `homeItems`, click handlers, filing, rename/move/delete, carousels |
| `workspace/` | Dock arrangement, visibility, panel sizes, drag geometry, and phone presentation | `moveWindow`, `startWindowDock`, `renderSlotGroup`, per-page layout snapshots |
| `pdf/` | Viewer controls, PDF/notes jumps, scroll restoration, translation, and snapshots | Existing viewer/translation files, `restorePdfScroll`, zoom and capture logic |
| `transfers/` | Upload/import/export operations | `uploadFiles`, format imports, backup transfer functions (the Background tasks store, tray and the job handlers in `useAppJobs.js` already live in `tasks/`) |
| `sharing/` | Share-link resolution/gates and owner share controls | `resolveShare`, `loadShareSettings`, invitation mutations (the popover itself, `SharePopover.jsx`, already lives here) |
| `settings/` | Settings panels and AI provider form/request state | Existing settings files plus the provider list, Test, usage and login-check handlers App still holds (the connect dialog's form, catalog and sign-in are already `useProviderEditor`) |
| `chat/` | Chat attachments and page-change notifications | `addBlockToChat`, `addHighlightToChat`, image selection, existing `ChatDock` |
| `shared/ui/`, `shared/lib/` | Reusable controls, API transport, and small shared functions | Menus, icons, selected parts of `shared/ui/Widgets.jsx` and `shared/lib/utils.js` |

Split the larger areas into focused files as their state owners emerge.
For example, library listing selectors, selection state, mutations, and
`LibraryView` should be separate; do not create one giant `useLibrary` hook.
Existing helpers such as `pageAttachment` and `useBlockHistory` remain the
source of truth throughout the moves.

## Logic cleanup to do with the extraction

### 1. Navigation and page state

Currently `openBlock` fetches a subtree, sets many page fields, changes the
URL, restores dock ratios and zoom, updates tabs/recents, and triggers other
page work. `goHome`, share resolution, and attachment operations manage related
state in separate places.

Separate loading a page from choosing where to navigate. Parse route inputs
in one helper, preserving `src`/`url`, `block`/`page`, share, folder, category,
and unlabelled aliases. A page session owns the loaded page and tree; display
values derive from that page, with separate draft state only for edits that
can be cancelled. Commit a loaded page as one transition rather than scattering
identity, attachment, and title resets among callers.

Use a navigation generation or abort signal so an earlier request cannot
replace a page opened later. Cancel delayed dock/scroll restoration on page
changes. Keep the existing distinction between link-jump Back history and
ordinary tab/library navigation.

### 2. Saving and undo

Keep `useBlockHistory` as the one undo stack per page. Page loads, collapse,
and edit-mode changes must retain their existing treatment; maintain caret
restoration and typing groups.

The existing `collaboration/collabSession.js` already serializes op batches
per page and retains pending page sessions across navigation. The module tests
cover navigating during a slow save and retrying failed batches. Preserve `flush`
and `hasPending`, the current typing/structural debounce policy (350/80 ms),
and immediate commits where requested. Audit account changes and callers such
as cross-page block moves before extracting more state: they must await the
required write before reparenting or fetching replacement data.

Navigation can render the next page while the old page's queue finishes, but
must not discard the old queue. Failed saves must remain identifiable and
retryable. Share-edit saves retain scoped access; read-only shares never queue
writes. Keep unload saving best-effort and do not claim it guarantees delivery.

### 3. Dock arrangement

Layout is currently split between `layout`, collapse/visibility flags,
`pageLayoutsRef`, panel handles, `openBlock`, and drag/render functions.
Create one layout state model with pure operations: move, collapse, toggle
visibility, snapshot, and restore. Validate persisted data: known window IDs,
valid slots, arrays, and no duplicates. Keep existing storage keys and migrate
values on read where needed.

Put pointer geometry in a small DOM adapter that emits layout commands.
`pointercancel` should clear the drag preview without committing a move;
currently it shares the drop handler. Preview and drop must use the same target
calculation. Keep collapsed headers outside percentage-sized panels, preserve
window order, and restore ratios only when the target panel structure exists.
Make all retry timers cancellable on navigation and unmount.

Phone overlays should derive from the same visible-window model while retaining
the desktop arrangement for the switch back to a wider viewport. Preserve
notes in the center for pages without a PDF attachment and layout inheritance
for pages without a saved arrangement.

### 4. Preference synchronization

Centralize scheduling, cancellation, account checks, and error handling, while
keeping each preference's merge policy explicit. Tabs and recents use server
state with their existing local rules; reading positions and snapshots merge
per page. Do not force all of them through one generic merge algorithm.

Capture account identity when scheduling a write, and reject stale responses
after an account change. Move side effects out of React state-updater functions
so updater replay cannot issue duplicate writes. Put browser preference
declarations in `PREFS` (`app/prefDefs.js`), including the home sort/kind/layout declarations
currently scattered through App, retaining legacy key migration.

### 5. Library, providers, and shared UI

Extract pure selectors for folder/label rollups, sorting, search dimming, and
visible slices. Keep one command path for operations invoked by cards, menus,
drag/drop, and keyboard shortcuts. Preserve folder refinement rules and the
ordering of folder-chat moves before folder navigation changes.

The AI connect dialog's drafts, busy/error state, catalog requests and OAuth
sign-in live in the settings feature (`useProviderEditor`,
`settings/providerEditor.js`). App still holds the provider list, the Test
and Usage results and the login check; it should receive the selected
provider/model and refresh commands instead.

Split `shared/ui/Widgets.jsx` by ownership: generic controls stay shared;
workspace docks (`DockWindow`, `OpenTabs`) and chat/editor rendering
(`ChatMarkdown`, `GammaLinkCard`, `BlockDropIndicator`) belong with their
feature. The transfer dialogs already live in `transfers/`.
The highlight palette is `shared/model/highlightColors.js` and the zoom
limits are `shared/model/zoom.js`, so neither the block editor nor the
notebook viewer imports the PDF viewer for constants. Split `shared/lib/utils.js` into API transport and
domain helpers as actual consumers are moved; avoid another catch-all folder.

## Implementation order

1. **Establish behavioral checks.** Cover fast page switching after an edit,
   failed saves across two pages, cross-page block moves, undo/caret behavior,
   account switches with pending preference writes, and dock restore/cancel.
   Use deterministic unit tests for queues and pure layout operations; browser
   checks exercise the user flows. Keep this separate from mechanical moves.
2. **Extract leaf modules and view sections.** Move settings/provider form
   logic (done: `useProviderEditor`), library selectors, URL helpers, and
   reusable UI. Extract `LibraryView`, `NotesPane`, `WorkspaceTopbar`, and
   feature dialogs with narrow inputs and command callbacks. These are
   manageable initial changes with clear owners.
3. **Extract dock state and rendering.** Add the layout model, drag adapter,
   `DockSlot`, and phone shell. Implement validation/cancellation corrections
   with the corresponding checks. Keep PDF scroll restoration separate from
   dock geometry.
4. **Extract page saving, then navigation.** Establish the page/account save
   queues before moving the page lifecycle. Then introduce the page session,
   navigation commands, and generation-guarded restoration.
5. **Extract sync and longer operations.** Move account preference sync,
   transfers, sharing, metadata, and chat integration into their owners. Features
   communicate via explicit callbacks such as `onPageChanged` and
   `onTransferUpdated`, not an application-wide bag of setters.
6. **Finish the composition root and styles.** Done so far: App lives in
   `app/`, library and settings CSS are colocated, and `main.jsx` keeps the
   stylesheet order. Reduce App's responsibilities further and split the
   remaining shared styles only when cascade interactions can be checked.

Each stage should build and remain usable on its own. Keep file moves and
behavior corrections distinguishable in the diff. No router library, global
state dependency, API redesign, or backend package reorganization is needed
for this work.

## Validation and documentation

- Build the frontend after each stage; use targeted behavioral tests for the
  changed owner rather than a test that only repeats its implementation.
- Exercise home folders/labels/pins, page creation, PDF and notes editing,
  sharing, import/export, tabs/Back, and phone/desktop layout as relevant.
- For save changes, verify two pages cannot overwrite each other's queued
  data, retries preserve newer edits, and stale account work cannot write.
- For layout changes, verify resize ratios, drag/reorder/cancel, collapsed
  headers, inherited/per-page layouts, and notes-only pages.
- For navigation, verify rapid A → B → C transitions leave C active, preserve
  A's edits, and never apply A/B's scroll or layout to C.
- Update `frontend/src/README.md`, `frontend/README.md`, `docs/dev/README.md`, and the
  relevant `docs/dev/` topic files as each owner moves. Mark completed stages
  here so proposed paths are never mistaken for the implemented layout.

Completion means App only composes the session/view and feature boundaries;
it does not implement save queues, provider forms, folder mutations, or
pointer geometry. A few hundred lines is a useful direction, not a line-count
target that justifies hiding complexity in a replacement giant hook.
