# Developer guide

Gamma is a self-hosted PDF annotation and notes app with nested blocks,
personal and shared workspaces, real-time collaboration, and AI tools.
Start with [running and testing](debugging.md) and the
[repository map](repository.md); read the relevant topic below before changing
an area, and update its documentation with the code.

## Architecture and shared rules

- **Applications:** `backend/` serves the FastAPI API; `frontend/` is the React
  and Vite client. The client calls same-origin `/api/*`, proxied to port 9001
  by Vite in development. Docker serves the built client from FastAPI.
  `desktop/` wraps servers in Electron, `ipad/` and `android/` are the
  native tablet and phone apps (shells over the web app with a Gamma
  server of their own), `extension/` clips papers from the
  browser, `cloud/` provides the separate Gamma Cloud account service, and
  `sites/` holds the public website. See the [repository map](repository.md).
- **Identity and storage:** accounts and workspaces are separate. `users.db`
  holds accounts, sessions, memberships, and account-wide preferences; each workspace has
  its own `pages.db`, `data.db`, and uploads under `GAMMA_DATA_DIR`. Password
  and Gamma Cloud sign-in create the same app session. Reuse the existing
  workspace/share authorization helpers and pass workspace IDs to data
  helpers; integration tokens do not grant account-management access.
  See [workspaces](workspaces.md), [storage](user_db.md), and [auth/API](api.md).
- **Blocks and writes:** pages are root blocks; notes and annotations are
  descendants. Use `generate_key_between` / `generateKeyBetween` for
  lexicographically ordered positions and `page_now()` for UTC timestamps.
  Existing-page edits use `apply_ops` / `commit_ops`; bulk replacements have
  an explicit reload path. Create pages through `blocks_store.create_page`,
  and use the trash/restore/delete helpers for their lifecycle. Keep viewer
  state (open editor and folding) separate from the document, and preserve
  other people's changes when rebasing undo. See [collaboration](collab.md).
- **Database changes:** open databases through `db.connect_*`; their context
  manager commits or rolls back and hands the connection back to the
  per-thread cache ([user_db.md](user_db.md) "Connections"). Use numbered
  [migrations](migrations.md) for stored-shape changes, not repairs on read.
  Blocking database/file work belongs in sync FastAPI handlers or a worker
  thread. Check-then-write operations need a write lock or a database
  constraint. See [debugging gotchas](debugging.md#gotchas-worth-knowing).
- **Frontend conventions:** reuse the command catalogs, preference registry,
  design tokens, and shared controls. New state goes into an owner module
  beside its feature (a hook or a store), not into `app/App.jsx`.
  `frontend/tests/appState.test.mjs` holds App's hooks and size to a
  baseline ([New state](frontend-refactor.md#new-state)). Translate
  user-visible strings through `t` / `tn` / `T` and update the catalogs in
  the same change. An issue found on one device that may reach other UI
  (logic, display or arrangement) gets one clean, unified solution in the
  shared layer, not a patch for that device or surface. See
  [hotkeys](hotkeys.md), [settings](settings.md),
  [UI design](ui-design.md), [i18n](i18n.md), and the
  [source map](../../frontend/src/README.md).
- **AI providers:** build per-user configuration through `ai_runtime(user)`;
  protocol-specific behavior belongs in `gamma/ai_protocols/`, and model
  listings/context windows come from the live catalog. See [AI](ai.md).
- **Validation:** test the affected modules and their direct consumers;
  browser behavior needs the relevant end-to-end scenarios. A frontend change
  passes `npm run lint` ([Frontend lint](debugging.md#frontend-lint)). Documentation
  changes need diff and link checks, not application builds. Broaden coverage
  when shared contracts or uncertain impact warrant it. See the
  [test selection policy](debugging.md#local-changes-test-the-affected-modules).
  A change to a hot path runs the [timing budgets](debugging.md#performance-budgets)
  before and after.

## Topic reference

These describe the implementation unless explicitly marked as plans.

| Topic | Reference |
|---|---|
| Source layout, assets, and documentation conventions | [Repository](repository.md) |
| Run commands, test selection, browser tests, timing budgets, and diagnostics | [Running, testing, and debugging](debugging.md) |
| CI, packaging, and release workflows | [GitHub Actions](github_actions.md) |
| HTTP endpoints and authorization | [API](api.md) |
| Search normalization, indexing, and result highlighting | [Search](api.md#search-searchpy-gammablock_indexpy-gammasearch) |
| Accounts, databases, stored files, quotas, and logs | [Storage and accounts](user_db.md) |
| Workspace roles, sharing, and backups | [Workspaces](workspaces.md) |
| Data-directory upgrades | [Migrations](migrations.md) |
| Guest accounts, expiry, and demo mode | [Guests](guests.md) |
| Block operations, page sockets, reconciliation, and undo | [Collaboration](collab.md) |
| Offline workspace copies and sync conflicts | [Mirrors](mirror.md) |
| A personal workspace kept in step with a Zotero library through Zotero's Web API: what syncs, the round, merging, PDFs, notes, annotations, conflicts, connecting the account | [Zotero link](zotero_sync.md) |
| A folder of the workspace and a directory on this computer kept in step, notes both ways, a PDF pushed to a page, and a file read where it is: what lands on disk, the lens, links and highlights, a round and the engine's classes (notes, PDF, picture, and a work folder's file of any format), conflicts, the rules, the desktop app, the routes, the gamma-sync client | [Synced vaults](synced_vaults.md) |
| PDF transport, manifests, caching, and load timing | [PDF loading](pdf_loading.md) |
| Ink format, input, editing, merging, and PDF ink import/export | [Handwriting](handwriting.md) |
| The lasso selection's Convert menu: handwriting redrawn as clean shapes or turned into a text box by the chat's model, and copied or saved as SVG | [Handwriting and AI](handwriting_ai.md) |
| Sheets of paper in a page, the notes and notebook views, paper | [Notebooks](notebooks.md) |
| Typed text boxes on PDF pages and sheets, and their PDF interchange | [Text boxes](text_boxes.md) |
| Photos and images placed on PDF pages and sheets: moving, turning, cropping and flipping them, the upload, the `/Stamp` they become in the annotated PDF, and Notability's pictures | [Pictures](pictures.md) |
| Audio recordings in step with the notes: capture, the one replay store, playback, transcription | [Recordings](recording.md) |
| Metadata, citations, and PDF resolution | [Paper metadata](paper_metadata.md) |
| Imports and exports: dialogs, formats, pipelines | [Import and export](import_export.md) |
| The typesetting engine both PDF writers share, and the annotated-PDF export | [PDF typesetting](pdf_typesetting.md) |
| Background jobs, the tasks tray, and adding a kind of long work | [Background tasks](tasks.md) |
| Folders and labels, pages inside pages, page cards, recents, and trash | [Home library](home_library.md) |
| A page's document, the `doc` property, of any format: the format registry, the stored shape and its rules (the listed state of a file too large to keep), the one read and its predicates (the file a page owns, the file it shows, read in place, a library PDF), one library page per stored file, migration steps 52 and 55, the shared fixtures, the viewer registry and the viewers (the code view with its highlight ranges, the picture with its regions, the player, the file pane) | [Documents](documents.md) |
| Work folders: a synced vault's folder whose tracked files are pages that follow the disk, the library rule (what the library lists, what is a paper), the op rule (who may refile, rename or trash a file), `.gammaignore` and the tracking dialog, the takeover of older vaults, Add to library and Save a copy, migration step 53, and every root-page reader classified | [Work folders](work_folders.md) |
| Pages side by side: the open tabs arranged into two panes, each with its strip, its page buttons and its page, split by a tab's menu, the topbar's right-click or a drag onto a side of the page; the ringed live pane and the view of the other, drawn as the page itself, with the docked windows around the groups; this browser's arrangement over the synced tabs | [Tab groups](tab_groups.md) |
| Provider configuration, chat, and the library agent | [AI](ai.md) |
| Agent tools and guardrails | [AI tools](ai_tools.md) |
| Long-paper context and grounding | [AI context](ai_context.md) |
| Chat buckets per page and folder, the active conversation, the archive, and exporting a conversation with its pictures | [Chat history](chat_history.md) |
| The translated PDF view, its engines and limits, and the annotated PDF that carries it | [PDF translation](translation.md) |
| Clickable PDF citations and library links | [PDF citations](pdf_citations.md) |
| A page's highlights found again by their text after its PDF is replaced, the rough matches drawn dashed, the ones not found drawn hollow with their closest match, the review card of both, moving one by hand; a text file's ranges found again by their quote and context when the file changes | [Highlights after a PDF replacement](highlight_relocation.md) |
| MCP, integration tokens, OAuth, and assistant plugins | [MCP](mcp.md) |
| Preferences, Settings panes, notices, and backup tasks | [Settings](settings.md) |
| The server's network proxy: the three modes, routing by destination and the bypass list, HTTP and SOCKS5 tunnels, guarded fetches pinned through the proxy, the Zotero stream, S3 and the local agents, the hosted lock, the admin API and its Test | [Network proxy](network_proxy.md) |
| A NAS over HTTPS: the system trust store in the server, no redirects between Gamma servers, failures worded, a self-signed certificate trusted by its fingerprint, moving a server's address with its links, HSTS on public names only, where plain http is still allowed | [HTTPS for a NAS](https.md) |
| Themes, controls, menus, dialogs, and editor interactions | [UI design](ui-design.md) |
| Command catalogs, dispatch, and keybindings | [Hotkeys](hotkeys.md) |
| The note editor's @ menu: dates, people and page links as chips, reminders and their delivery | [Mentions and reminders](mentions.md) |
| Comment threads on notes, pages, highlights, regions of a picture, ranges of a text file and handwriting: storage, who may comment, the API, following the blocks; each account's Inbox and its live delivery | [Comments](comments.md) |
| Feedback: the comment threads on a work folder's files as entries an agent acts on, with the place each was opened on, the source line SyncTeX maps it to, a picture of its region, the version kept while it is open, Send and its requests, the files below `.gamma/feedback/`, the routes, the feedback token scope and the MCP tools, the seam for a connector | [Feedback](feedback.md) |
| An agent on this computer: Codex CLI or Claude Code turned on for a work folder in the desktop app, which takes the maker's Sends, works in the folder's directory and answers each comment; finding the agents, the claim and the run, the prompt and the answer, the two drivers, the processes, what vaults.db keeps, the routes, trust | [Local agents](local_agents.md) |
| A computer and the servers its vaults name: the `computer` token and its channel (an announce and a held poll), presence kept by the server and shown to the vault's maker in any window, the sync panel's line, the routes on both sides, trust | [Computers](computers.md) |
| LaTeX pairing, completion, caret behavior, preview, and pasted `\( \)` / `\[ \]` math | [LaTeX editing](latex_editing.md) |
| Mermaid and SVG diagrams: rendering, sanitizing, sizing and editing | [Diagrams](diagrams.md) |
| Interface translations and catalogs | [Internationalization](i18n.md) |
| Tours, anchors, triggers, and seeded welcome content | [Onboarding](onboarding.md) |
| Browser connector | [Extension](extension.md) |
| Electron shell, local servers, packaging, and QA | [Desktop](desktop.md) |
| The iPad app (a shell over the web app, with a native Pencil layer and a Gamma server of its own on the device), the host contract and chrome pages it shares with the Android app, the home-screen web app, and the touch editing bar | [iPad](ipad.md) |
| The Android app: the same design in Kotlin, a WebView shell with a native pen layer for any Android stylus and a Gamma server of its own on Chaquopy's Python; its build, tests, release and Google Play listing | [Android](android.md) |
| The map of Gamma Cloud: its pieces and names, what happens on a sign-in, a purchase and a lapse, where settings live, the Admin page | [Gamma Cloud](cloud.md) |
| Gamma Cloud identity service and server sign-in | [Cloud accounts](cloud_accounts.md) |
| Paid hosted containers: limits sync, lifecycle, the fleet agent and batch upgrades | [Hosted servers](hosted.md) |
| Plans, Stripe checkout and webhooks, the effective-plan rule, the Plan page | [Billing](billing.md) |
| Public website build and deployment | [Website](../../sites/README.md) |

## Plans and design references

- [Frontend decomposition](frontend-refactor.md): the one live plan. The
  folder layout and the bundle split are done, and "Lazy boundaries" describes
  the chunks as built; moving state ownership out of `App.jsx` has started:
  the block tree's view, the AI connect dialog's form, handwriting and
  the tabs' arrangement into groups ([tab groups](tab_groups.md)).
- [Block-centric design](block_centric.md): the design reference for the
  block model, with its staged roadmap. The stages are built, and so are
  sub-pages, as pages that name the page they are inside; the block
  zoom-in is the open item. The stored shapes as built are in
  [storage](user_db.md) and [home library](home_library.md).
- [Any file as a page](../research/file-explorer.md): a plan whose steps 0
  to 6 are built ([documents](documents.md),
  [work folders](work_folders.md), [feedback](feedback.md),
  [highlight relocation](highlight_relocation.md#text-files)). A page's
  document of any format with a viewer for each, work folders through
  synced vaults whose tracked files are chosen, comment threads as feedback
  a local agent acts on, text files in search and the chat, a Files
  window, and highlights and threads on a text file's lines that follow
  their text when the file changes; its steps and what is still to decide.
- [Scaling on SQLite](../research/scaling.md): items 1 to 12 of its work list
  are done (item 8, the blob seam, was built and then taken out again) and
  described in the topic docs above; the cluster items (LiteFS, placement, a
  router and worker processes) wait for a second node.
- [Research notes](../research/README.md): surveys, findings, and design
  rationale, grouped by area, with what each still leaves open. Keep current
  implementation details in the topic docs above.
