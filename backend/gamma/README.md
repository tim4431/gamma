# gamma/

The backend package. `app.py` assembles the middleware + routers and serves the built frontend.

```
config.py          env config
db.py              schemas, SCHEMA_VERSION, per-workspace DB paths, the connect helpers (closed by their with block), account id ↔ name, user prefs
migrations.py      versioned data-directory upgrades (snapshot, numbered steps)
backups.py         server backups: snapshots under backups/ (create, zip, delete, restore)
ws_backup.py       one workspace's backup zip: export, restore, stored snapshots, a deleted workspace's final copy
integrity.py       PRAGMA quick_check of a database; the latest failure per file (the db-damage notice)
normalize.py       content normalization of a workspace's files (migration + restore)
workspaces.py      workspaces + memberships (roles, billing, personal workspace)
auth.py            session middleware → request.state.user_id / user (read in a worker thread); request → workspace / share
seed.py            workspace file creation, the Welcome page new accounts start with, first admin
onboarding/        welcome.md, the seeded Welcome page (docs/dev/onboarding.md)
blocks_store.py    block tree helpers: subtrees, the change log (touch_page), the folder and label trees
highlights.py      a block's place on a PDF page: the pdf_position shape (highlights, link regions, ink)
storage.py         uploads (content-addressed, written atomically) + the upload-reference grammar
upload_gc.py       unreferenced uploads: recorded, kept 30 days, then purged (background thread)
trash.py           Recently deleted: the trashed pages, their 30-day purge (background sweeper)
ink.py             the gamma-ink stroke file: schema, codec, geometry, renderers, the three-way stroke merge
notebook.py        notebook pages: paper rules, sheets, the notebook as a PDF (docs/dev/notebooks.md)
ai_protocols/      one adapter per AI wire protocol (request, stream, usage, models, quota)
ai_client.py       provider-agnostic AI transport (open, read, stream, errors)
ai_catalog.py      live model listings + context windows (provider, then models.dev)
ai_context.py      PDF attachments, extraction, and chat context assembly
ai_agent.py        the agent tool loop, its two user-facing pauses, and the helper a tool delegates to
ai_permissions.py  the chat tools' Allow / Ask / Off states and the approvals a reply waits on
paper_research.py  a background research job: the same loop headless, filing its report as a page
logseq_import.py   EDN / Markdown importers
ratelimit.py       in-process fixed-window limits per key (bounded; the client address is the peer)
compression.py     gzip for whole JSON answers only (never streams, files or ranges)
app.py             assembly + SPA serving
routers/           one module per API area — see routers/README.md
```

## Data model — everything is a block

Each workspace's `pages.db` has the `unified_blocks` table (plus the `page_ops`
and `page_changes` logs, the AI chats and the notes index — docs/dev/user_db.md).
Rows form a tree via `parent_id`;
sibling order is the lexicographic `position` (fractional-index strings like `a0`, `a0V`).

```
parent_id = 'root'                         ← pages
   │  props.doc_id  → PDF page
   │  props.folders = [folder ids]         → filed in those folders
   │  props.labels = [label ids]           → its labels
   │
   ├─ block  (plain text)                  ← free note
   ├─ block  props.pdf_position            ← highlight on the PDF (its id is the block id)
   │     props.link_url / link_page_id         → clickable reference link
   │     (+ link_block_id: a highlight there)
   └─ block  …nested children (indent/outdent)

parent_id = 'trash'                        ← pages in Recently deleted (30 days;
                                             props.deleted_at / deleted_by) — no page
                                             until restored (trash.py)

parent_id = 'folders' (or a folder)        ← folders: content = name, nested
parent_id = 'labels'                       ← labels: content = name, flat
                                             (pseudo-pages to the op path;
                                             docs/dev/home_library.md)
```

Key columns: `id, parent_id, position, content, properties (JSON), created_at, updated_at`, the stored `page_id`, and the generated `kind` and `doc_id` (docs/dev/user_db.md).

Invariants:
- Positions come from `generate_key_between` — never hand-write them. The one
  exception is the reserved parentless rows, written with fixed positions
  because nothing orders them: `root` (`'a0'`, `seed.create_workspace_files`)
  and `trash`, `folders`, `labels` (`'a1'`, `ensure_reserved`).
- `PUT /blocks/{id}/children` replaces the whole subtree (delete + reinsert, one
  transaction; never `root`'s); the upload names it drops go to the orphan check.
- A file nothing references is kept 30 days, then purged
  (`upload_gc.py`, docs/dev/user_db.md "Stored files").
- Timestamps are UTC ISO strings with a `Z` suffix (`page_now()`).
