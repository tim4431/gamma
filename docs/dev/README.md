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
  `desktop/` wraps servers in Electron, `extension/` clips papers from the
  browser, `cloud/` provides the separate Gamma Cloud account service, and
  `sites/` holds the public website. See the [repository map](repository.md).
- **Identity and storage:** accounts and workspaces are separate. `users.db`
  holds accounts, sessions, memberships, and preferences; each workspace has
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
  manager commits or rolls back and closes the connection. Use numbered
  [migrations](migrations.md) for stored-shape changes, not repairs on read.
  Blocking database/file work belongs in sync FastAPI handlers or a worker
  thread. Check-then-write operations need a write lock or a database
  constraint. See [debugging gotchas](debugging.md#gotchas-worth-knowing).
- **Frontend conventions:** reuse the command catalogs, preference registry,
  design tokens, and shared controls. Translate user-visible strings through
  `t` / `tn` / `T` and update the catalogs in the same change. See
  [hotkeys](hotkeys.md), [settings](settings.md), [UI design](ui-design.md),
  [i18n](i18n.md), and the [source map](../../frontend/src/README.md).
- **AI providers:** build per-user configuration through `ai_runtime(user)`;
  protocol-specific behavior belongs in `gamma/ai_protocols/`, and model
  listings/context windows come from the live catalog. See [AI](ai.md).
- **Validation:** test the affected modules and their direct consumers;
  browser behavior needs the relevant end-to-end scenarios. Documentation
  changes need diff and link checks, not application builds. Broaden coverage
  when shared contracts or uncertain impact warrant it. See the
  [test selection policy](debugging.md#local-changes-test-the-affected-modules).

## Topic reference

These describe the implementation unless explicitly marked as plans.

| Topic | Reference |
|---|---|
| Source layout, assets, and documentation conventions | [Repository](repository.md) |
| Run commands, test selection, browser tests, and diagnostics | [Running, testing, and debugging](debugging.md) |
| CI, packaging, and release workflows | [GitHub Actions](github_actions.md) |
| HTTP endpoints and authorization | [API](api.md) |
| Search normalization, indexing, and result highlighting | [Search](api.md#search-searchpy-gammablock_indexpy-gammapdf_indexpy) |
| Accounts, databases, stored files, quotas, and logs | [Storage and accounts](user_db.md) |
| Workspace roles, sharing, and backups | [Workspaces](workspaces.md) |
| Data-directory upgrades | [Migrations](migrations.md) |
| Guest accounts, expiry, and demo mode | [Guests](guests.md) |
| Block operations, page sockets, reconciliation, and undo | [Collaboration](collab.md) |
| Offline workspace copies and sync conflicts | [Mirrors](mirror.md) |
| PDF transport, manifests, caching, and load timing | [PDF loading](pdf_loading.md) |
| Ink format, input, editing, merging, and PDF ink import/export | [Handwriting](handwriting.md) |
| Sheets of paper in a page, the notes and notebook views, paper | [Notebooks](notebooks.md) |
| Metadata, citations, and PDF resolution | [Paper metadata](paper_metadata.md) |
| Imports, exports, and shared PDF typesetting | [Import and export](import_export.md) |
| Background jobs, the tasks tray, and adding a kind of long work | [Background tasks](tasks.md) |
| Folder labels, page cards, recents, and trash | [Home library](home_library.md) |
| Provider configuration, chat, and the library agent | [AI](ai.md) |
| Agent tools and guardrails | [AI tools](ai_tools.md) |
| Long-paper context and grounding | [AI context](ai_context.md) |
| Clickable PDF citations and library links | [PDF citations](pdf_citations.md) |
| MCP, integration tokens, OAuth, and assistant plugins | [MCP](mcp.md) |
| Preferences, Settings panes, notices, and backup tasks | [Settings](settings.md) |
| Themes, controls, menus, dialogs, and editor interactions | [UI design](ui-design.md) |
| Command catalogs, dispatch, and keybindings | [Hotkeys](hotkeys.md) |
| LaTeX pairing, completion, caret behavior, and preview | [LaTeX editing](latex_editing.md) |
| Mermaid rendering and editing | [Mermaid](mermaid.md) |
| Interface translations and catalogs | [Internationalization](i18n.md) |
| Tours, anchors, triggers, and seeded welcome content | [Onboarding](onboarding.md) |
| Browser connector | [Extension](extension.md) |
| Electron shell, local servers, packaging, and QA | [Desktop](desktop.md) |
| The native iPad app (offline replica, Pencil) and the home-screen web app | [iPad](ipad.md) |
| Gamma Cloud identity service and server sign-in | [Cloud accounts](cloud_accounts.md) |
| Public website build and deployment | [Website](../../sites/README.md) |

## Plans and research

- [Frontend decomposition](frontend-refactor.md): remaining App.jsx ownership
  work; the current folder layout is in the source map.
- [Block-centric design](block_centric.md): target model, dated inventory,
  and staged roadmap; check the code before treating a planned step as built.
- [Research notes](../research/README.md): surveys, findings, and design
  rationale. Keep current implementation details in the topic docs above.
