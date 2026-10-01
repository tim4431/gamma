# Frontend source

Code is grouped by the part of Gamma it serves. Functional folders sit directly
under `src/`; there is no extra `features/` layer or requirement to route imports
through barrel files. `main.jsx` remains the Vite entry point.

| Folder | Responsibility and entry points |
| --- | --- |
| `app/` | `App.jsx` connects the application views, navigation, saves, and docks; `prefDefs.js` declares every preference (key, default, codec, browser or account scope), `prefs.js` turns them into state and syncs the account ones, `sessionState.js` handles session restoration; `notices.js` + `useNotices.js` are the red dot's model and feed (what wants a look, resolved by visiting its Settings pane); `appCommands.js` is the app-scope shortcut catalog and `commands.js` joins it with the block one for the palette and the Keyboard pane ([docs/dev/hotkeys.md](../../docs/dev/hotkeys.md)) |
| `auth/` | Login, session/share access screens (`LoginPage.jsx`) and MCP authorization (`McpConsent.jsx`) |
| `chat/` | AI conversation panel (`ChatDock.jsx`) and its pure helpers (failure copy `chatErrors.js`, the agent's steps `agentSteps.js`, chip previews `chipText.js`), what a reply changed with each note change's revert (`AgentChanges.jsx`, rules in `aiRevert.js`), the card for a fetch handed to the browser, live in a waiting reply and under a finished one (`FetchHandoffCards.jsx`, rules in `fetchHandoff.js`), the approval card a tool call waits on (`ApprovalCard.jsx`, rules and the conversation's own decisions in `approvals.js`), a reply's "Save to library" list (`ReplyPapers.jsx`, rules in `chatPapers.js`), paper mentions, the tool permission catalog (`chatSettings.js`: states, defaults, presets), and the token-usage formatting (`tokenUsage.js`) shared with Settings |
| `collaboration/` | `usePageCollab.js`, the pure `collabSession.js` state machine, presence UI, `MirrorPopover.jsx` — the header's sync pill of a clone with its settings and conflicts views — and `MergeResolver.jsx`, the conflict chip on a block row ([docs/dev/mirror.md](../../docs/dev/mirror.md)) |
| `editor/` | Outliner (`BlockTree.jsx`), the "Linked from" section under a page's notes (`BacklinksPanel.jsx`), the `[[` link picker (`RefPicker.jsx`, its pure lists `refLists.js`), CodeMirror (`BlockCmEditor.jsx`), undo history, Markdown and LaTeX editing, slash commands, the block-scope shortcut catalog (`blockCommands.js`) with the formatting runners (`markCommands.js`) and the pure fence scanner (`fences.js`); the object frame around rendered images, tables and diagrams (`MdObject.jsx`) with its pure move algebra (`mdObjects.js`) and the math, image and table scanners every consumer shares (`mdScan.js`) |
| `guide/` | First-run and contextual guides: `anchors.js` (the `data-guide` registry), `events.js` (event bus), `triggers.js` (eligibility and account-scoped progress), `keys.js` (`{key:…}` chords in tour copy), `finish.js` (a finished tour's card), `useGuide.js` + `GuideOverlay.jsx` (engine, invitation and spotlight), `media.js` + `media/*.svg` (the illustration registry and its drawings), `tours/` (one data file per tour) — [docs/dev/onboarding.md](../../docs/dev/onboarding.md) |
| `ink/` | Handwriting codec, geometry and stroke merge (`ink.js`), input sampling, draft storage with each draft's base (`inkStore.js`), and `InkLayer.jsx`: the page layer, the selection menu and its placement (`useSelectionMenuAnchor`, a text box's menu uses it too), the notes card, and the pieces `markup/` shares (`ColorChoices`, `swallowClick`) ([docs/dev/handwriting.md](../../docs/dev/handwriting.md)). The iPad bundle imports `ink.js`; the tool strip is in `markup/` |
| `markup/` | What a page surface (a PDF page or a sheet) carries, and the tool strip: `PageTools.jsx` (`PageToolsContext`, the tools every layer reads, with ref-backed actions, and `armedClasses`), `MarkupLayers.jsx` (the one place a surface's layers mount, and `useMarks`, each surface's marks), `MarkupToolbar.jsx` (the strip), and text boxes — the pure rules and geometry (`textBox.js`), App's side (`useTextBoxes.js`), the layer, menu and style choices (`TextBoxLayer.jsx`), `markup.css` ([docs/dev/text_boxes.md](../../docs/dev/text_boxes.md)) |
| `notebook/` | Sheets of paper to write on: the paper rules and sheet helpers (`notebook.js`, pure), the notebook view and the paper menu (`NotebookViewer.jsx`), a sheet in the notes view (`NoteSheet.jsx`) — [docs/dev/notebooks.md](../../docs/dev/notebooks.md) |
| `replica/` | The iPad app's sync core, never loaded by the web app: the mirror's tree rules (`tree.js`), three-way decisions (`reconcile.js`), rounds over a device's host (`round.js`), the device's edits (`edits.js`), page views (`views.js`), the text merge (`textmerge.js`) — [docs/dev/ipad.md](../../docs/dev/ipad.md) |
| `library/` | Library cards and browsing controls (`FileBrowser.jsx`), a new library's "Start your library" panel (`LibraryEmpty.jsx`), the Ctrl+P page palette and Ctrl+Shift+P command palette (`QuickOpen.jsx`), the Recently deleted dialog (`RecentlyDeleted.jsx`), folder/page rules, title scoring, and `library.css` |
| `pdf/` | `PdfViewer.jsx`, document loading, citations, translation, and scroll alignment |
| `search/` | Workspace search (`SearchPanel.jsx`) and its pure result text: notes as one plain line, the query's matches mapped back through the normalized text (`snippets.js`) |
| `settings/` | `SettingsDialog.jsx`, individual settings panes, shared pane controls (`SettingsKit.jsx`), the profile sync reading (`syncState.js`), navigation, integration setup, and `settings.css` |
| `sharing/` | The Share popover (`SharePopover.jsx`, a page or a folder as its target): link, access, invited people, stop sharing; the share view's access pill (`ShareAccess.jsx`) |
| `support/` | Report a problem: the dialog (`ReportProblem.jsx`) and the pure report builder it and the tests share (`problemReport.js`) |
| `transfers/` | Import/export dialogs (`ImportExport.jsx`, with the export job's last step), the import review (`ImportReviewDialog.jsx`, `ImportTree.jsx`, `importApi.js`, `importReview.js`), format rules (`transferFormats.js`: the formats, their server modes and the export job's body), and upload/file chips (`FileChip.jsx`) |
| `tasks/` | Background tasks ([docs/dev/tasks.md](../../docs/dev/tasks.md)): the store of server jobs and this tab's own work (`useTasks.js`), the pure model (`taskModel.js`) and kind registry (`taskKinds.js`: titles, progress lines, row actions, where a row opens), the tray and the progress block dialogs share (`TasksTray.jsx`, `tasks.css`), the jobs API (`tasksApi.js`) |
| `shared/model/` | Block tree helpers and the viewer's `view` beside the tree — open editor, folding (`blockModel.js`), block operations (`blockOps.js`), and highlight colors |
| `shared/i18n/` | Interface language: `i18n.js` (`t`, `tn`, `T`, the locale store, `fmtDate`), `locales.js` (the language list, pure), `locales/<code>.json` (one catalog per language) — [docs/dev/i18n.md](../../docs/dev/i18n.md); `tools/i18n.mjs` keeps the catalogs complete |
| `shared/lib/` | API transport and helpers (`utils.js`), plain formatting free of the browser (`format.js`: `fmtBytes`), the multipart upload (`xhrUpload.js`), search text normalization, canvas sizing, published pages' slugs and page hosts (`slug.js`), and keyboard chords + the shortcut dispatcher (`hotkeys.js`) |
| `shared/ui/` | Reused widgets, menus, icons, menu hover intent, key caps, the brand mark (`BrandMark.jsx`) and the wheel-to-sideways pan of a strip (`wheelPan.js`) |
| `shared/illustrations/` | Decorative settings/import previews and their local image assets |
| `shared/styles/` | `tokens.css`: every design token (theme colours, type, radii, layers), linked first by `index.html`; `app.css`: base controls and cross-application styles |

## Placement and naming

- Put code beside its main consumer. Sharing a helper between two files does not
  automatically make it a `shared/` module; library title scoring, for example,
  stays in `library/` even though chat and workspace search also use it.
- Use PascalCase for React component modules and camelCase for JavaScript
  helpers. A hook-only module can use a `use` prefix, as in `usePageCollab.js`.
- Import the owning module directly. `shared/model/blockModel.js` is the general
  page/block model, not an import adapter.
- Keep styles with their owner when already separate. `main.jsx` deliberately
  loads application, library, then settings CSS in that order to preserve the cascade.
- Keep tests in `frontend/tests/`; run `npm test`, `npm run build`, and
  `npm run e2e` from `frontend/` (the browser suite needs the backend dependencies
  and a Playwright browser).

## Remaining cleanup

`app/App.jsx` still owns several kinds of state; `shared/ui/Widgets.jsx` and
`shared/lib/utils.js` still combine responsibilities. In particular, the shared
Markdown renderer understands PDF citations, so these folders are ownership
groups rather than enforced dependency layers. Split those modules when changing
their behavior, with the relevant tests, instead of adding forwarding wrappers.

See the [decomposition plan](../../docs/dev/frontend-refactor.md) for the larger
state-ownership work, and [UI design](../../docs/dev/ui-design.md) for component
details and conventions.
