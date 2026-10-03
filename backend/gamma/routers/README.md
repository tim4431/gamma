# routers/

One module per API area. Mounted under `/api` in `gamma/app.py`.

| file | routes | does |
|------|--------|------|
| `auth.py`     | `/api/login`, `/logout`, `/session`, `/accounts` | session cookies, the account directory |
| `ws_backups.py` | `/api/workspaces/{id}/backups*`, `/export`, `/export-all`, `/import-data`, `/jobs/workspace-export`, `/jobs/snapshot`, `/jobs/restore*` | workspace backups: snapshots, downloads, restores, and their background jobs |
| `jobs.py`     | `/api/jobs`, `/jobs/{id}*`          | Background tasks: list, read, stop, remove and download jobs (`gamma/jobs.py`; each kind starts in its own router) |
| `workspaces.py` | `/api/workspaces/*`               | workspaces: list/create/rename/delete, members and roles |
| `admin.py`    | `/api/admin/*`                      | accounts, server settings, every workspace, the server log |
| `blocks.py`   | `/api/blocks/*`                     | the block tree (CRUD, children, subtree, by-doc) |
| `pages.py`    | `/api/pages`, `/pages/by-docs`, `/pages/{id}/attachment` | page-first endpoints: create a page, attach/detach its document, which pages carry which PDFs |
| `trash.py`    | `/api/trash`, `/trash/{id}/restore` | Recently deleted: list, restore, delete for good, empty |
| `uploads.py`  | `/api/uploads/*`, `/upload-file`    | PDF / image / any-file upload + serving (content-addressed; executables refused); a PDF's upload in parts (`/uploads/parts`, gamma/upload_parts.py) |
| `pdf.py`      | `/api/resolve-pdf`                  | find a real PDF url (arXiv → meta tag → Unpaywall OA) |
| `metadata.py` | `/api/metadata/fetch`, `/cite`      | paper metadata + BibTeX + PPT citation (cached on the page) |
| `ai.py`       | `/api/ai/chat`, `/models`, providers | chat orchestration, AI settings, OAuth |
| `chats.py`    | `/api/chats/*`, `/api/chat-history*` | the AI chats per bucket (a page, a folder, `home`) and their history (workspace data) |
| `collab.py`   | `/api/pages/{id}/ops`, `/api/ws/page/{id}` | the op write path + the page websocket (pages, and the `folders` / `labels` trees) |
| `folders.py`  | `/api/folders/{id}`, `/api/labels/{id}` (DELETE) | deleting a folder or a label: the subtree, the ids on the pages, the folder chats and shares |
| `prefs.py`    | `/api/prefs/*`, `/api/page-snaps*`  | per-account (and per-workspace) synced prefs, cover snapshots |
| `search.py`   | `/api/search`, `/pdf-search`, `/search-reindex` | FTS5 index over PDF text (pypdfium2, normalized via `gamma/textnorm.py`), built by the workspace's indexing job |
| `shares.py`   | `/api/share/*`, `/api/share-settings/*` | share links of a page or a folder (by id) |
| `imports.py`  | `/api/import/*`, `/jobs/import`     | Logseq, embedded-PDF-annotation, Markdown and the reviewed library imports (the import itself a job) |
| `export.py`   | `/api/pages/{id}/export*`, `/folders/{id}/export`, `/bibliography`, `/jobs/export` | every export format, as a download or a job |

Gotchas:
- **Route order** for `/api/blocks/*`: static prefixes (`by-doc`, `children`, `subtree`) must register **before** `/{block_id}`.
- Every data endpoint resolves its WORKSPACE (`require_ws` / `resolve_ws` / `require_ws_writer` in `gamma/auth.py` — `?ws=`, the `X-Gamma-Workspace` header, a `?share=` token, else the account's personal workspace) and passes the id to the data helpers; `actor_of(request)` (the account's id, `request.state.user_id`) is the actor. Identity-only endpoints use `require_user_id` (the id, what storage takes) or `require_user` (the username, what is shown).
- Endpoints that touch a database or files, and slow ones (downloads, AI, PyPDF2), are sync `def` — FastAPI threadpools them, so nothing waits on SQLite on the event loop. `async def` only when the handler must await (a body stream, a socket, an SDK handler), its database work in `run_in_threadpool`. They run side by side: a check-then-write takes the write lock first (`BEGIN IMMEDIATE` / `blocks_store.write_lock`) or lets a constraint decide. `tests/test_event_loop.py` checks it (docs/dev/debugging.md "Gotchas").
