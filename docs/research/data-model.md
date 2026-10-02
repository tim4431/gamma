# The data model, surveyed for a clean-slate redesign

Findings from 2026-10, from reading the stored shapes
([dev/user_db.md](../dev/user_db.md), `gamma/db.py`) and the code that
depends on them, asked one question: if stored data did not have to stay
compatible, which structural changes would pay for themselves? Ranked by
payoff per effort. The current mechanics live in the dev docs; this note
keeps the diagnosis and the reasoning, so the ranking can be revisited when
a redesign is actually on the table.

## What the survey found

The block table is sound. `unified_blocks(id, parent_id, position,
content, properties JSON, created_at, updated_at)` with fractional-index
positions and client-minted ids is the shape Notion, Logseq and Craft
converged on; the op log and the per-page `seq` are what collaboration and
the mirror need; the trash as a reserved parent is cheap and consistent.
None of that wants changing. The debt sits around it:

1. **The account's name is its key.** `users.username` is the primary key
   and is copied into sixteen columns across twelve `users.db` tables, into
   the comma-separated `shares.allowed_users` list, into every workspace's
   `page_ops.actor` and `deleted_pages.actor`, and into the backup task
   files. A rename is a hand-kept list of updates in
   `routers/admin.rename_account_rows`, plus re-sealing the encrypted
   publisher sessions because the ciphertext names the account, plus a
   refusal while a backup task runs. The workspace databases are not
   touched, so an op log keeps the old name, which a later account may take.
   Workspaces solved exactly this in 2026-09 by becoming random ids
   ([workspaces.md](workspaces.md)); accounts are the one entity still
   named by a mutable string.

2. **Folders and labels are strings without identity.** A page's
   `properties.folder` is a comma-separated list of slash paths, its
   `properties.category` a list of label names. The tree is derived from
   the paths in use, so an empty folder exists only in one browser's
   localStorage, a folder cannot be pinned through a property (pins live
   in the prefs KV instead), commas and slashes are forbidden in names, and
   the path rules are maintained twice (`gamma/foldertags.py` and
   `libraryUtils.js`). A rename is a prefix rewrite the client applies to
   every page filed below the folder: one op batch per page, each bumping
   the page's `updated_at` (so the "modified" sort and the change feed show
   an organisational change as an edit, mirrors re-pull every page, the
   notes index re-indexes each), followed by a best-effort server call that
   moves the folder's chat bucket (`home:<path>`) and folder shares, whose
   failure "orphans a conversation or a share". Every consumer that names
   a folder (shares, chats, pins, the agent's scope, the search scope)
   keys on the path string.

3. **The change feed is time-based because there is no workspace-wide
   sequence.** `GET /sync/changes` orders pages by the root's `updated_at`
   text stamp and re-lists the last 60 seconds on every poll because a
   writer may stamp before it commits ([dev/collab.md](../dev/collab.md)
   "The change feed"). Deletions need their own `deleted_pages` table,
   written by both trashing and hard deletion. The mirror, the iPad
   replica and backup merges all consume this feed, and the direction of
   the product ([mirror.md](../dev/mirror.md), [ipad.md](../dev/ipad.md))
   makes more consumers likely. Every writer already stamps the page root
   once per batch; a counter would cost the same discipline and be exact.

4. **`data.db` is called derived but holds user content.** Its comment
   says "derived / regenerable", and the FTS indexes, PDF manifests,
   translation cache and cover thumbnails are. The AI chats are not: in one
   local workspace they are 13 MB of conversation, the largest thing in
   either database. Because they sit in the derived file they are
   excluded from mirror sync, exported and restored by special code
   (`routers/export.py`, `ws_backup.py` each create a `chats` table by
   hand), and keyed to their subject by a string that is either a page id
   or a `home:<folder path>` bucket, which the folder rename above has to
   chase. Meanwhile the derived indexes live apart from the rows they
   index, so `gamma/block_index.py` carries a staleness protocol:
   `block_fts_meta` fingerprints, `block_fts_rows` rowid maps,
   `mark_page_dirty`, a quiet-period refresher thread and a search-time
   rebuild budget. SQLite's external-content FTS5 with triggers in the same
   file would maintain the index inside the writing transaction and delete
   most of that module.

5. **Hot page fields are reached through JSON.** `doc_id`, `folder`,
   `highlight_id`, `web_url`, `markdown_import`, `zotero_key` and
   `source_url` are queried with `json_extract` in about twenty places,
   none indexed; `blocks_store.root_pages` and the library listing load
   every root row and parse its JSON in Python to filter by folder. The
   root listing also ships each page's whole `properties`, including the
   cached `bibtex` and `ppt_cite` strings nobody reads on a card. At a few
   hundred pages this is invisible; the 7,000-block local workspace is
   where it starts to show.

6. **Smaller redundancies.** A highlight carries a second identity
   (`highlight_id`, referenced by `link_highlight_id`) beside its block id;
   `pdf_page` duplicates `pdf_position.pageNumber`; each rect in
   `pdf_position.rects` repeats the viewport `width`, `height` and
   `pageNumber` it was measured in; every PDF page stores
   `source_url = /api/uploads/<doc_id>.pdf`, which is `doc_id` again;
   `bibtex` is derivable from `meta`; `sessions.guest_date` is written and
   read by nothing; recents, open tabs and reading positions are
   whole-list JSON values in the prefs KV, merged last-writer-wins across
   devices.

## What would be worth changing, in order

**Give accounts a stable id.** `users(id TEXT PRIMARY KEY, username UNIQUE,
…)`, every other table referencing `user_id`, `shares.allowed_users`
becoming a `share_users(token, user_id, role)` table, `page_ops.actor` and
`deleted_pages.actor` holding the id. The rename list, the publisher
re-seal, the backup-task refusal and the name-reuse hazard all go. This is
bounded work with no product-visible change and should come first in any
redesign, because every other change below writes actor columns too.

**Make folders and labels blocks.** Keep the flat library and the
many-to-many filing that the current model deliberately has, but give each
folder and label a row: blocks under two reserved parents (`folders`,
`labels`) beside `root` and `trash`, with `parent_id` giving the folder
nesting and `position` the order. A page then carries `folders: [id, …]`
and `labels: [id, …]` in its properties. A rename is one `set` on one
block; empty folders persist and sync; pins, shares, chat buckets and
scopes key on an id; names may contain anything; the mirror, the op log,
undo, backups and the agent tools already know how to carry a block, so
no second sync entity is needed. The frontend's derived folder tree
becomes a read of the `folders` subtree plus a count per id.

**Replace the time-based change feed with a workspace log.** One
`changes(seq INTEGER PRIMARY KEY, page_id, kind, at, actor)` table in
`pages.db`, appended inside every writing transaction (the same place
`apply_ops` stamps the root today), with `deleted` as a kind, so
`deleted_pages` folds into it. The feed becomes `WHERE seq > ?`, exact,
clock-independent, with no grace window and no "stamp close to commit"
rule for writers to remember. Mirrors and the replica then need one cursor
per workspace instead of a time plus an id.

**Split by lifecycle, not by origin.** Move `chats` and `chat_history` into
`pages.db`, keyed by page id (a folder-level chat keys by the folder block's
id once folders are blocks), so they travel with the pages in sync, backup
and export without special code. Leave `data.db` holding only what can be
deleted and rebuilt: FTS indexes, PDF manifests, PDF text, translation
cache, thumbnails. Then move the notes index into `pages.db` as an
external-content FTS5 table maintained by triggers, so `block_index.py`
shrinks to its query half. The PDF text index stays derived; it is keyed by
file hash and could even become a server-wide cache, since the same PDF in
two workspaces is extracted twice today.

**Index the hot fields.** Generated columns on `unified_blocks` for
`doc_id`, `highlight_id` and a `kind` discriminator (`page`, `note`,
`highlight`, `ink`, `sheet`, `text_box`, `link`, derived once at write time
instead of re-derived in `routers/blocks._block_kind`), each with an index,
and a `page_folders(page_id, folder_id)` join if the properties array is
not enough for the listing. The listing then filters in SQL and returns
only the columns a card needs.

**Tidy the highlight shape.** The block id is the highlight id; links point
at block ids; `pdf_position` stores one page number and page size with
rects in PDF points, the form the export already converts to; `source_url`
is stored only when it differs from the stored file. None of this is
urgent; it is what a rewrite of `pdf_position` for the iPad's native
renderer would want anyway.

## Considered and not worth it

- **Integer timestamps.** ISO text sorts correctly and reads in a debugger;
  the saving is bytes, not logic.
- **A server-wide content-addressed blob store.** It would dedupe a PDF
  across workspaces, but quota accounting is per directory size,
  deletion would need cross-workspace reference counts, and workspace
  export, deletion and mirroring would each grow a case. The duplication
  costs disk, which is cheap on a self-hosted machine.
- **A different database.** Per-workspace SQLite files are what make
  backup, export, mirror and deletion one-file operations; the hosting
  survey ([hosting.md](hosting.md)) reached the same conclusion from the
  other direction.
- **A page-type column.** The block-centric design decided against it
  ([dev/block_centric.md](../dev/block_centric.md)) and the survey found
  nothing that needs it; the `kind` above is for child blocks, where the
  type is already implicit in which property is present.
