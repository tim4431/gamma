# gamma/

The backend package. `app.py` assembles the middleware + routers and serves the built frontend.

```
config.py          env config
db.py              schemas, SCHEMA_VERSION, per-workspace DB paths, the connect helpers (closed by their with block), user prefs
migrations.py      versioned data-directory upgrades (snapshot, numbered steps)
backups.py         server backups: snapshots under backups/ (create, zip, delete, restore)
ws_backup.py       one workspace's backup zip: export, restore, stored snapshots, a deleted workspace's final copy
integrity.py       PRAGMA quick_check of a database; the latest failure per file (the db-damage notice)
normalize.py       content normalization of a workspace's files (migration + restore)
workspaces.py      workspaces + memberships (roles, billing, personal workspace)
auth.py            session middleware → request.state.user (read in a worker thread); request → workspace / share
seed.py            workspace file creation, the Welcome page new accounts start with, first admin
onboarding/        welcome.md, the seeded Welcome page (docs/dev/onboarding.md)
blocks_store.py    recursive-CTE tree helpers
storage.py         uploads (content-addressed, written atomically) + the upload-reference grammar
upload_gc.py       unreferenced uploads: recorded, kept 30 days, then purged (background thread)
trash.py           Recently deleted: the trashed pages, their 30-day purge (background sweeper)
ai_protocols/      one adapter per AI wire protocol (request, stream, usage, models, quota)
ai_client.py       provider-agnostic AI transport (open, read, stream, errors)
ai_catalog.py      live model listings + context windows (provider, then models.dev)
ai_context.py      PDF attachments, extraction, and chat context assembly
logseq_import.py   EDN / Markdown importers
ratelimit.py       in-process fixed-window limits per key (bounded; the client address is the peer)
compression.py     gzip for whole JSON answers only (never streams, files or ranges)
app.py             assembly + SPA serving
routers/           one module per API area — see routers/README.md
```

## Data model — everything is a block

Each workspace's `pages.db` has the `unified_blocks` table (plus the `page_ops`
log). Rows form a tree via `parent_id`;
sibling order is the lexicographic `position` (fractional-index strings like `a0`, `a0V`).

```
parent_id = 'root'                         ← pages
   │  props.doc_id  → PDF page
   │  props.category = "quantum, review"   → labels (comma-separated)
   │
   ├─ block  (plain text)                  ← free note
   ├─ block  props.highlight_id+pdf_position   ← highlight on the PDF
   │     props.link_url / link_page_id         → clickable reference link
   └─ block  …nested children (indent/outdent)

parent_id = 'trash'                        ← pages in Recently deleted (30 days;
                                             props.deleted_at / deleted_by) — no page
                                             until restored (trash.py)
```

Key columns: `id, parent_id, position, content, properties (JSON), created_at, updated_at`.

Invariants:
- Positions come from `generate_key_between` — never hand-write them.
- `PUT /blocks/{id}/children` replaces the whole subtree (delete + reinsert, one
  transaction; never `root`'s); the upload names it drops go to the orphan check.
- A file nothing references any more is kept 30 days, then purged
  (`upload_gc.py`, docs/dev/user_db.md "Stored files").
- Timestamps are UTC ISO strings with a `Z` suffix (`page_now()`).
