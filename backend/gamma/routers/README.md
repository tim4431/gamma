# routers/

One module per API area. Mounted under `/api` in `gamma/app.py`.

| file | routes | does |
|------|--------|------|
| `auth.py`     | `/api/login`, `/logout`, `/session`, `/accounts`, `/login-guest` | session cookies, the account directory, guest login |
| `cloud_auth.py` | `/api/server-config`, `/api/auth/cloud/*` | Sign in with Gamma Cloud (`gamma/cloud_auth.py`): sign-in and linking, connecting this server, the account's profile sync, what the login page needs |
| `integrations.py` | `/api/integrations/tokens*` | an account's integration tokens (`gamma/integrations.py`); session only |
| `ws_backups.py` | `/api/workspaces/{id}/backups*`, `/export`, `/export-all`, `/import-data`, `/jobs/workspace-export`, `/jobs/snapshot`, `/jobs/restore*` | workspace backups: snapshots, downloads, restores, and their background jobs |
| `backup_tasks.py` | `/api/backup-tasks*`              | an account's scheduled backup tasks (`gamma/backup_schedule.py`): list, preview, create, edit, run, delete |
| `jobs.py`     | `/api/jobs`, `/jobs/{id}*`          | Background tasks: list, read, stop, remove and download jobs (`gamma/jobs.py`; each kind starts in its own router) |
| `workspaces.py` | `/api/workspaces/*`               | workspaces: list/create/rename/delete, members and roles, invitations by Gamma Cloud username |
| `admin.py`    | `/api/admin/*`, `/api/jobs/server-backup` | accounts, server settings, shared AI providers, every workspace, server backups and off-site copies, the database check, the server log |
| `blocks.py`   | `/api/blocks/*`, `/block-search`    | the block tree (CRUD, children, subtree, backlinks, by-doc), block search |
| `pages.py`    | `/api/pages`, `/pages/by-docs`, `/pages/from-file`, `/pages/{id}/attachment` | page-first endpoints: create a page (also from a stored Markdown file), attach/detach its document, which pages carry which PDFs |
| `trash.py`    | `/api/trash`, `/trash/{id}/restore` | Recently deleted: list, restore, delete for good, empty |
| `uploads.py`  | `/api/uploads/*`, `/upload-image`, `/upload-file`, `/pdf-info/{doc_id}`, `/quota` | PDF / image / any-file upload + serving (content-addressed; executables refused); a PDF's upload in parts (`/uploads/parts`, gamma/upload_parts.py); a PDF's manifest (gamma/pdf_meta.py); the storage limits |
| `ink.py`      | `/api/upload-ink`                   | handwriting uploads: one `gamma-ink` file per ink group, stored like any upload |
| `pdf.py`      | `/api/resolve-pdf`, `/pdf`          | find a real PDF url (arXiv → meta tag → Unpaywall OA), and proxy it (optional local cache) |
| `metadata.py` | `/api/metadata/*` (`fetch`, `update`, `verify`, `status`, `cite`) | paper metadata + BibTeX + PPT citation (cached on the page) |
| `ai.py`       | `/api/ai/*` (chat, models, providers, approvals, revert, translate, transcribe, search services, ChatGPT OAuth), `/translate/engines*` | chat orchestration, AI settings, OAuth, translation |
| `ai_handoffs.py` | `/api/ai/handoffs/{id}*`         | the chat's fetch handoffs (gamma/fetch_handoff.py): a PDF the user's browser fetches for the chat, from the card or the Gamma Connector |
| `chats.py`    | `/api/chats/*`, `/api/chat-history*` | the AI chats per bucket (a page, a folder, `home`) and their history (workspace data) |
| `collab.py`   | `/api/pages/{id}/ops`, `/api/ws/page/{id}` | the op write path + the page websocket (pages, and the `folders` / `labels` trees) |
| `sync.py`     | `/api/sync/whoami`, `/sync/changes`, `/sync/folders*` | the workspace change feed, for anything that keeps a copy in step (a desktop mirror, the iPad's replica), and the folder manifest and notes files the gamma-sync client reads (`gamma/folder_sync.py`) |
| `mirrors.py`  | `/api/mirrors*`                     | mirrors of a workspace on another Gamma server (gamma/sync_engine.py): create, run a round, status, log, conflicts; the owner only |
| `folder_links.py` | `/api/folder-links*`               | folders kept as directories on this computer (gamma/folder_links.py) — of the server's workspaces, or of another Gamma server read with a token of it: list, make, change, a round now, remove; the desktop app's own server only (404 elsewhere), session only, editors (a remote source's link: its account) |
| `publish.py`  | `/api/pages/{id}/publish`, `/publish/limit`, `/pages/resolve-public`, `/auth/cloud/exchange` | publishing a page to the free share host (gamma/publish.py): the publishing server's half and the share host's |
| `folders.py`  | `/api/folders/{id}`, `/api/labels/{id}` (DELETE) | deleting a folder or a label: the subtree, the ids on the pages, the folder chats and shares |
| `prefs.py`    | `/api/prefs/*`, `/api/page-snaps*`  | per-account (and per-workspace) synced prefs, cover snapshots |
| `notices.py`  | `/api/notices*`                     | the red dot's feed (gamma/notices.py) and the ack when a notice is seen |
| `search.py`   | `/api/search`, `/pdf-search`, `/search-reindex` | FTS5 search over notes (`block_fts`) and PDF text (`pdf_fts`; pypdfium2, normalized via `gamma/textnorm.py`), the PDF index built by the workspace's indexing job |
| `links.py`    | `/api/link-preview`                 | a web page's title for link chips (through the SSRF guard, cached in-process) |
| `clip.py`     | `/api/clip`, `/clip/note`, `/library/*` | the Gamma Connector's endpoints: save a page or a selection, look the library up for the popup |
| `publisher_sessions.py` | `/api/publisher-sessions*` | publisher sign-in cookies the Connector imports, stored encrypted and write-only |
| `shares.py`   | `/api/share/*`, `/api/share-settings/*` | share links of a page or a folder (by id) |
| `imports.py`  | `/api/import/*`, `/jobs/import`     | Logseq, embedded-PDF-annotation, Markdown and the reviewed library imports (the import itself a job) |
| `export.py`   | `/api/pages/{id}/export*`, `/folders/{id}/export`, `/bibliography`, `/jobs/export` | every export format, as a download or a job |

Gotchas:
- **Route order** for `/api/blocks/*`: the static prefix `by-doc` must register **before** the `/{block_id}` routes.
- Every data endpoint resolves its WORKSPACE (`require_ws` / `resolve_ws` / `require_ws_writer` in `gamma/auth.py` — `?ws=`, the `X-Gamma-Workspace` header, a `?share=` token, else the account's default personal workspace) and passes the id to the data helpers; `actor_of(request)` (the account's id, `request.state.user_id`) is the actor. Identity-only endpoints use `require_user_id` (the id, what storage takes) or `require_user` (the username, what is shown).
- Endpoints that touch a database or files, and slow ones (downloads, AI, PyPDF2), are sync `def` — FastAPI threadpools them, so nothing waits on SQLite on the event loop. `async def` only when the handler must await (a body stream, a socket, an SDK handler), its database work in `run_in_threadpool`. They run side by side: a check-then-write takes the write lock first (`BEGIN IMMEDIATE` / `blocks_store.write_lock`) or lets a constraint decide. `tests/test_event_loop.py` checks it (docs/dev/debugging.md "Gotchas").
