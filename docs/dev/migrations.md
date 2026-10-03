# Data migrations

How the data directory moves from one Gamma release's shapes to the next,
safely and without accumulating patches. Code: `gamma/migrations.py`
(runner + numbered steps), `gamma/normalize.py` (content normalization of a
workspace's files), `db.SCHEMA_VERSION`, `manage.py migrate` / `backups`.

## The rules

1. **One version for the whole data directory**, stored as `PRAGMA
   user_version` of `users.db`. `db.SCHEMA_VERSION` is what the running code
   expects. `db.py`'s `CREATE TABLE` statements always describe the current
   shape; nothing is patched on connect.
2. **A release that changes stored shapes ships one numbered step** in
   `migrations.STEPS` and bumps `SCHEMA_VERSION`. The step is the whole
   change — moved files, rebuilt tables, rewritten rows — written to be
   re-runnable (it checks what it is about to do). Never bump without a step,
   never add a step without bumping.
3. **The runner goes first.** `migrations.ensure_current()` is the first
   thing `app._startup_maintenance` does, before any table is opened; a data
   directory that is *ahead* of the binary (`NewerDataError`) or older than
   `MIN_UPGRADABLE` (`TooOldDataError`) stops the server with a clear message.
   `db.connect_users_db()` refuses an outdated file too (`SchemaOutdated`), so
   no code path can read old shapes with new assumptions. That refusal
   covers every `manage.py` command but `migrate`, which is why the Docker
   entrypoint runs `manage.py migrate` before `manage.py setup` — `setup`
   alone would exit on an old volume before the server ever starts.
4. **Backup, then step, then stamp.** Before the first pending step every
   SQLite file is snapshotted with the backup API into
   `backups/<time>-v<N>/` (relative paths kept, plus `manifest.json`);
   uploads are never copied — steps move directories, never rewrite files.
   Steps run in order; the version is stamped after each one, so an
   interrupted upgrade resumes at the step that did not finish. An
   unfinished upgrade and its snapshot are named in `backups/upgrade.json`,
   and every retry reuses that snapshot. So a restart loop over a failing
   step neither piles up copies nor pushes the clean pre-upgrade one out. A
   snapshot that cannot be written (a full disk) stops the upgrade before
   any step, as a `MigrationError`. Once an upgrade finishes, the marker
   goes and only the newest `KEEP_BACKUPS` (3) automatic snapshots are kept;
   hand-made backups are never pruned.
5. **Nothing piles up.** Old steps are deleted once `MIN_UPGRADABLE` is
   raised past them (a release or two later); a data directory that old must
   first run a release that still has them — the refusal says so. The
   content normalizers in `gamma/normalize.py` are the one exception: they
   also run on every backup restore (`/api/import-data`), because a backup can
   be older than any step, so they stay for as long as such backups are
   accepted.

## Versions

| version | step | what it does |
|---|---|---|
| 0 | — | every Gamma before schema versions: `users.db` with lazily added columns, `users/<username>/` per account |
| 1 | `baseline` | the pre-workspace world in its final shape: the columns that used to be added on connect, share rows keyed by page (doc-keyed rows resolved or dropped), per-user files normalized (content shapes, legacy tables; an old `chats` without `title` is step 28's to move) |
| 2 | `workspaces` | `users/<username>/` → `workspaces/<id>/` with a `workspaces` row and an owner membership per account (`users.default_workspace`); `data.db` `prefs` → `users.db` `user_prefs` (account-wide keys under workspace `''`, page-naming keys under the new workspace); `shares` rebuilt as `(workspace_id, page_id, created_by, …)`. [workspaces.md](workspaces.md) |
| 3 | `workspace_access` | `workspaces` gains `access` (private / public), `public_role` and `quota_mb` (a shared workspace's own cap); existing rows stay private with no quota. Nothing moves |
| 4 | `workspace_kinds` | `workspaces` gains `kind`: every account's default workspace and every single-member workspace become `personal` (private, no workspace quota), the rest `shared`. Other members of a default workspace are dropped (personal workspaces have no other members) and named in the log |
| 5 | `publisher_sessions` | Adds private, encrypted publisher cookie snapshots in `users.db`, keyed by account and publisher host. Existing content and account settings are unchanged |
| 6 | `integration_tokens` | Adds the `integration_tokens` table in `users.db`: hashed assistant tokens per account and workspace ([mcp.md](mcp.md)) |
| 7 | `mcp_oauth` | Adds the `mcp_oauth` table in `users.db`: OAuth client registrations, pending authorizations and access-token audiences, all expiring |
| 8 | `ai_usage` | Adds the `ai_usage` table in `users.db`: per-account token counts of AI calls ([ai.md](ai.md)) |
| 9 | `upload_path_titles` | Runs the content normalizers over every workspace's `pages.db` once more for the new `upload_path_titles` step: a directory path that a browser leaked into `original_filename` (and into the generated title, while it still equals it) becomes the leaf. This used to be repaired on every library listing with raw SQL outside the op log; reads now write nothing |
| 10 | `mirrors` | `integration_tokens` gains `scope` (`read`, the old meaning, or `write`); users.db gains `mirrors`, the offline copies of remote workspaces ([mirror.md](mirror.md)). Per-workspace `pages.db` files gain `sync_pages` / `sync_conflicts` on connect (additive `CREATE TABLE IF NOT EXISTS`, like `page_ops`) |
| 11 | `mirror_cadence` | `mirrors` gains `poll_s` (how often a round checks the original, 0 = by hand) and `on_change` (a round a few seconds after a local edit); `mode` may be `off` (detached) |
| 12 | `sync_log_stats` | every workspace's `sync_log` gains `stats`, the git-style block counts of a row (JSON `{add, del, mod}`); older rows carry none |
| 13 | `sync_conflict_base` | every workspace's `sync_conflicts` gains `base`, the text a merged block had before either side edited it (the resolver's diff view); older rows carry none |
| 14 | `identities` | Adds the `identities` table (+ unique index per account) in `users.db`: the Gamma Cloud identity linked to an account ([cloud_accounts.md](cloud_accounts.md)) |
| 15 | `ai_explicit_models` | AI provider entries (`user_prefs` key `ai-settings`) with no models picked get the default model they were implicitly using written in (anthropic `claude-haiku-4-5-20251001`, openai `gpt-4o-mini`, chatgpt `gpt-5.1`): the running code no longer has built-in default models ([ai.md](ai.md)) |
| 16 | `pending_memberships` | Adds the `pending_memberships` table (+ an index by subject) in `users.db`: shared-workspace invitations by Gamma Cloud username, keyed by workspace and cloud subject, waiting for that person's first sign-in ([workspaces.md](workspaces.md)). Nothing else changes |
| 17 | `profile` | The account-wide `appearance` pref (`{theme, pdfDark}`) becomes the new `profile` pref (`{theme, pdfDarkPage}`, keyed by the web app's preference names, same `updated_at`); an existing profile is kept; `appearance` rows are dropped ([settings.md](settings.md)) |
| 18 | `cloud_grant` | `sessions` gains `via` (`''` a password or the guest, `cloud` a Gamma Cloud sign-in; existing rows count as password sessions) and `identities` gains `revoked_at`: the grant check ends only the sessions a cloud sign-in minted when the account server refuses that account's grant ([cloud_accounts.md](cloud_accounts.md)) |
| 19 | `mirror_page_filter` | `mirrors` gains `page_filter`: NULL (every page travels, what every existing mirror keeps) or a JSON list of the only page ids that do, the shape a published page's mirror has ([mirror.md](mirror.md) "The page filter") |
| 20 | `guest_accounts` | Guests became throwaway accounts minted per login ([guests.md](guests.md)): the legacy shared `guest` account (`is_guest = 1`) is deleted with its sessions, memberships, prefs, shares, tokens and usage rows, and its personal workspace's rows, directory and stored snapshots (a directory that will not go is logged and left as an orphan). Any other `is_guest` row — what `manage.py create-user` without a password used to make — becomes a normal password-less account, so the guest expiry never deletes it |
| 21 | `folder_shares` | `shares` gains `folder`: a share names a page (`page_id`) or a folder-label path (`folder`, the pages filed there or below it, read live), the other column `''`; the page unique index becomes partial (`WHERE page_id != ''`) and a folder twin joins it ([api.md](api.md) "Shares") |
| 22 | `upload_orphans` | Every workspace's `pages.db` gains `upload_orphans` (`name`, `since`): a stored file nothing references any more is kept 30 days before it is purged, instead of being deleted with its last reference ([user_db.md](user_db.md) "Stored files"). The table starts empty; the first background pass after startup records what is unreferenced |
| 23 | `page_trash` | Every workspace's `pages.db` gains the reserved `trash` row beside `root` (parentless, position `a1`): the parent of the pages in Recently deleted ([home_library.md](home_library.md) "Recently deleted"). Written now so no block can take the id before the first delete; new workspaces get it from `seed.create_workspace_files`, and `ops.trash_page` writes it where a restored older backup lacks it. A workspace where a block already holds the id is named in the log and left as it is |
| 24 | `jobs` | Adds the `jobs` table (+ owner and workspace indexes) in `users.db`: background jobs — exports, backups, restores, imports, the search indexer — with their parameters, progress, result, error and produced file ([tasks.md](tasks.md)). Nothing else changes |
| 25 | `account_ids` | Accounts are keyed by a stable id ([user_db.md](user_db.md) "Accounts are named by id"): `users` is rebuilt with `id` (a random token, the primary key) beside a unique `username`. In `users.db`, as one transaction: the `username` columns of `sessions`, `identities`, `integration_tokens`, `publisher_sessions`, `workspace_members`, `user_prefs` and `ai_usage` become `user_id` (tables rebuilt, rows naming no account dropped); `workspaces.created_by`, `workspace_members.added_by`, `pending_memberships.invited_by`, `mirrors.owner` and `jobs.owner` hold the id (a name no account has becomes `''`; a job of one is deleted); `shares.allowed_users` becomes `share_users` rows (names no account has dropped) and the column goes; the publisher snapshots are re-sealed under the id (one that no longer opens is dropped); the minutes-long OAuth records that name an account (`mcp_oauth` kinds `consent`, `code`, `cloud_login`) are dropped. Then every workspace's `page_ops.actor`, `deleted_pages.actor` and trashed pages' `deleted_by`, and the backup task files' `owner`, name the id (`link:<name>`, `mirror` and `''` stay; a name no account has becomes `''`) — rewritten where they still hold a name, so a rerun after a crash finishes them with the ids already stored |
| 26 | `block_columns` | Every workspace's `unified_blocks` gains its typed hot fields, indexed ([user_db.md](user_db.md) "pages.db"): `page_id` (stored; filled in by a recursive walk down from the reserved rows — a page's own id on it and every block below it, kept in Recently deleted, `''` on `root` / `trash` and on a row whose parent is gone) and the generated VIRTUAL `kind` and `doc_id`, with `idx_ub_page`, `idx_ub_kind` and the partial `idx_ub_doc`. `ALTER TABLE ADD COLUMN` and the backfill in one transaction per file (`normalize.block_columns`, which a restored older backup goes through as well); a file that has them is left as it is |
| 27 | `page_changes` | Every workspace's `pages.db` gains its change log, `page_changes` ([collab.md](collab.md) "The change feed"), in place of the `deleted_pages` tombstones (`normalize.page_changes`, which a restored older backup goes through too; one transaction per file): a row per page, seqs in the order the pages were last written (`updated_at`, then the id) — `live` under `root`, `deleted` in Recently deleted (its tombstone's time and actor, else its trash stamps) — then a `deleted` row per tombstone of a page that is gone, in the order they went; `deleted_pages` is dropped. In `users.db` every mirror's `remote_cursor` and `local_cursor` (cursors of the old time-ordered feed) start over as `''`: the next round walks both feeds whole and writes nothing for a page that did not move |
| 28 | `chats_and_notes_index` | Every workspace's `pages.db` takes the AI chats and the notes index ([user_db.md](user_db.md) "pages.db", "The notes index"): `chats` (`block_id` renamed `bucket`; a row without a title gets `''`) and `chat_history` are copied in from the workspace's `data.db` and dropped there once the copy is committed (`normalize.pages_db_chats`), the notes index is created in `pages.db` — the view `block_fts_src`, the external-content FTS5 table `block_fts`, its three triggers — and built from the blocks (`normalize.block_fts`), and `data.db` drops its own `block_fts`, `block_fts_meta` and `block_fts_rows` (`normalize_data_db`). A restored older backup goes through the same. The step applies no frozen pages.db statements first (`_each_pages_db(..., schema=())`): its files are at step 27's shape. Re-runnable: rows already copied are kept, the index is built again. data.db is left holding only derived data |
| 29 | `folder_blocks` | Folders and labels become blocks ([home_library.md](home_library.md) "Folders and labels"). Every workspace's `pages.db`, one transaction per normalizer: the generated `kind` gains its `folder` / `label` cases (`normalize.block_columns` drops the outdated column with its index, and `doc_id` after it, and adds them again in a fresh table's order), then `normalize.folder_blocks`: the reserved rows `folders` and `labels`; a folder block for every path the pages' `properties.folder` (comma-separated `/` paths, in the library and in Recently deleted) and the folder chats' `home:<path>` buckets name — so an empty folder's chat keeps its folder —, nested by segment, new siblings ordered by name under ids derived from the path (`normalize.tree_block_id`: two copies converted apart name a folder alike); a label block for every name of `properties.category`; each page's filing rewritten to `properties.folders` / `labels` (ids, in the old order) without stamping or touching the page (refiling is no edit); each folder chat's bucket moved to its folder's id (`home:` alone to `home`; a conversation whose new bucket has one already is filed into that bucket's history); a `live` row of the change log for each tree, so a mirror pulls them. Then in `users.db`, per workspace: a folder share names its folder's id (a path no folder answers to — its pages filed elsewhere since — deletes the share and its invitations), and each account's `pinned-folders` pref (`[{path, at}]`) becomes `properties.pinned` on those folder blocks — the newest pin of any member, a folder being one block for all of them — before every `pinned-folders` row is deleted. The step applies no frozen pages.db statements (`schema=()`). A restored older backup goes through `block_columns` and `folder_blocks` too. Re-runnable: a converted file has nothing of the old shape, and the users.db half resolves what it finds against the trees (an id stays, a path is looked up) |
| 30 | `highlight_shape` | The highlight shape ([api.md](api.md) "The highlight shape", `gamma/highlights.py`). Every workspace's `pages.db`, one transaction per normalizer: the generated `kind`'s `highlight` case reads a `pdf_position` object instead of a `highlight_id` (`block_columns`), then `normalize.highlight_shape` over the rows a `LIKE` finds: every `pdf_position` in the stored shape (`highlights.from_scaled`: the page and its size once from the position's or its first measured rect's size, a rect measured at another size scaled into it, no `pageNumber` / `width` / `height` per rect, `area` at the top, the page from `pdf_page` when the position named none; one with no page anywhere goes); a highlight (a `highlight_id`) with no position but a `pdf_page` keeps its page as `pdf_position: {pageNumber}`; `highlight_id` goes, and `pdf_page` but on a text box (its only page); a link region's `link_highlight_id` becomes `link_block_id`, the block that had that highlight id on the page it links to (`link_page_id`; the block of that id when there is one, else the first by id), a note's `linked_highlight_id` the block that had it on its own page — one that resolves to nothing goes; a `source_url` that is the row's `doc_id`'s stored copy (`/api/uploads/<doc_id>.pdf`) goes. Nothing is stamped or touched (a shape is no edit): what a row becomes depends on the file's rows alone, so a mirror and its remote upgraded apart rewrite their copies of a page alike and the next round finds nothing to send (a filtered mirror may resolve a link to a page only one side has differently). `users.db` is untouched. `schema=()`. A restored older backup goes through `block_columns` and `highlight_shape` too, after the content normalizers (whose oldest step still writes a page's `sourceUrl` as `source_url`). Re-runnable: a converted row rewrites to itself |
| 31 | `session_columns` | `sessions` loses `guest_date`, a column the guest login wrote and nothing read: the table is rebuilt in its current shape with its rows, so no one is signed out. Step 25 builds the table without it from the start, so a directory it brought up is left as it is |

## Backups (`gamma/backups.py`)

The runner's snapshots are one use of a general facility: a backup is
`backups/<time>-<label>/` with every SQLite file at its relative path (taken
with the SQLite backup API, so consistent while the server runs), the
`uploads/` directories when asked, and a `manifest.json` (time, label, schema
version, file list, whether uploads are included, `auto` for the runner's,
and `integrity`: each database copy's `PRAGMA quick_check` result,
`gamma/integrity.py`). The snapshot is written as `.<name>.part` and renamed
once the manifest, written last, is in. A failure removes it, so a full disk
never leaves a half copy. A directory without a manifest (a crash mid-copy)
is never listed or pruned; delete it by hand. A database SQLite cannot read
is copied byte for byte instead of failing the whole snapshot, and its
damage is recorded. A damaged copy raises the admins' `db-damage` notice.

- **Take one**: Settings → Server → *Server backups* (admins; *Databases
  only* or *Everything*), `POST /api/admin/backups` (507 when it cannot be
  written), or `manage.py backups --create [--uploads] [--label x]`. The
  migration runner takes a databases-only one labelled `v<N>`, marked
  `auto`, and after a finished upgrade keeps the newest
  `backups.KEEP_BACKUPS` of those; hand-made ones are never pruned.
- **See / download / delete**: the same pane, `GET /api/admin/backups`,
  `GET /api/admin/backups/{name}/download` (a zip of the directory),
  `DELETE /api/admin/backups/{name}`; `manage.py backups [--delete <name>]
  [--prune]` (`--prune` thins the automatic ones only).
- **Restore**: `manage.py backups --restore <name>` with the server stopped
  — copies the files back over the data directory (WAL/SHM sidecars of the
  live files are dropped first) and leaves everything the snapshot lacks as
  it is; uploads come back only from a backup that carried them. Start the
  server afterwards; it migrates the restored data if the snapshot predates
  the binary (a restore abandons an unfinished upgrade, so that start takes a
  fresh snapshot). Deliberately not an HTTP endpoint: a whole-directory swap
  under a running server is not safe.

Per-workspace backups — the snapshots users keep on the server from
Settings → Backups and the `/api/export` zips — are `gamma/ws_backup.py`
([workspaces.md](workspaces.md) "Export and backups"), a different, smaller thing.

## Running it

- **Automatically**: at every server start (Docker, the desktop sidecar, dev).
  The startup log names the snapshot and the steps applied. On Windows an
  upgrade that moves directories needs no other process holding the files —
  which is the case at startup.
- **By hand** (server stopped): `python manage.py migrate` (`--status` to
  only report, `--dry-run` to list what would run), `python manage.py
  backups` to see, take, thin or restore snapshots.

A failed step stops the server: the version stays at the last completed
step, the message names the cause and the snapshot. Fix the cause and start
again (the step resumes, with the same snapshot), or restore the snapshot:
copy its files back over the data directory. For a directory move,
`manifest.json` in the snapshot and the `workspaces` table in the snapshot's
`users.db` (after step 2) say which directory belongs to which account. Step
2 commits an account's `workspaces` row and `default_workspace` before it
moves `users/<name>/`, and a resumed run moves the files into the id it
recorded. The prefs it copies out of `data.db` are committed before their
old table goes.

A step that walks every workspace's `pages.db` (9, 12, 13, 22, 23, 25–30 —
`migrations._each_pages_db`) does not fail on one damaged file. That
workspace is logged as an error and skipped, and the upgrade goes on: one
broken library never keeps every other account's server from starting. The
skipped workspace stays at the old shape; after restoring its file from the
step's snapshot, the step has to be run on it again. Startup follows the
same rule: a workspace whose files cannot be opened is logged and left out,
and the others are served.

Never run an older Gamma on an upgraded data directory: it refuses when it
knows about versions, and an older one that does not would create empty
accounts next to the moved data. Roll back by restoring the snapshot.

## Writing a step

```python
def _v3_something(conn: sqlite3.Connection) -> None:
    """One sentence on the new shape."""
    if "new_col" not in _columns(conn, "users"):      # re-runnable: check first
        conn.execute("ALTER TABLE users ADD COLUMN new_col TEXT NOT NULL DEFAULT ''")
    conn.commit()

STEPS = [..., (3, "something", _v3_something)]
SCHEMA_VERSION = 3   # gamma/db.py
```

- The step gets an open `users.db` connection; per-workspace files it opens
  itself (`closing(sqlite3.connect(...))` — leave no handle open, Windows
  locks moved directories otherwise). A change to every workspace's
  `pages.db` goes through `_each_pages_db(step, fn)`, which skips a damaged
  file instead of failing the upgrade.
- Update `USERS_SCHEMA` / `DATA_SCHEMA` / `PAGES_SCHEMA` to the new shape at
  the same time, so a fresh install and an upgraded one end up identical.
  An older step that creates a table from those lists must keep creating
  the shape of its time, so when a step changes a table's shape, freeze the
  statements the older steps used. Steps 1–24 create users.db tables from
  `migrations._V24_USERS_SCHEMA` (users.db at version 24). Step 1 and
  `_each_pages_db` apply `migrations._V25_PAGES_SCHEMA` (pages.db at
  version 25) unless the step passes `schema=()`, as steps 28–30 do: that
  list still has `deleted_pages`, which step 27 drops. A later step that
  walks the workspaces passes `schema=()` or freezes the statements of its
  own time. A pages.db connection a step
  opens has `db.register_functions` (`_each_pages_db` registers them): the
  notes index's triggers call `textnorm` on every block write.
- A shape a restored workspace backup must have too goes into
  `normalize.py` as well, called from the step and from the restore
  (`ws_backup._normalize_copies`) before the schema statements:
  `block_columns` (steps 26, 29 and 30), `page_changes` (step 27) and
  `pages_db_chats` (step 28) are the ones; `block_fts` (step 28) and then
  `folder_blocks` (step 29) run after the schema statements and before the
  content normalizers, whose rewrites the index's triggers follow;
  `highlight_shape` (step 30) runs after the content normalizers.
  `block_columns`' column definitions are `db.BLOCK_HOT_COLUMNS`, and it
  replaces a generated column whose stored definition is not the current
  one (drop its index, `ALTER TABLE DROP COLUMN`, add it again, re-index —
  with every generated column after it, keeping a fresh table's column
  order): a step that redefines `kind` changes it there and runs
  `block_columns` again, as steps 29 and 30 do.
- A change to the search normalization (`textnorm.normalize_text`) is a
  step too: the notes index's triggers delete a row's old text as the
  current rules make it, so every workspace's index must be rebuilt
  (`normalize.block_fts` through `_each_pages_db`) before the server
  writes a block under the new rules — a restore rebuilds a backup's on its
  own. Bump `textnorm.INDEX_VERSION` with it: that re-extracts the PDF
  index, which is versioned per paper.
- A change to block *content* shapes (a renamed property, a syntax) goes into
  `normalize.py` as a `LIKE`-filtered idempotent rewrite, called from the
  step *and* automatically on restore. Never repair a shape on a read path
  (a listing, a fetch): reads write nothing, so two copies of a workspace
  only ever differ by what the op log and the change log record.
- Test it in `tests/test_migrations.py`: build the old layout by hand in a
  temp data directory (`v24_users_db()` there builds users.db as version 24
  shaped it), run `ensure_current()`, assert the new shape, run it again
  (no-op), and cover the interrupted-run resume.
