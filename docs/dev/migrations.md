# Data migrations

How the data directory moves from one Gamma release's shapes to the next,
safely and without accumulating patches. Code: `gamma/migrations.py`
(runner + numbered steps), `gamma/normalize.py` (content normalization of a
workspace's files), `db.SCHEMA_VERSION` and `db.WS_VERSION_BASE`,
`manage.py migrate` / `backups`.

## The rules

1. **Two stamps, one number.** `PRAGMA user_version` of `users.db` is the
   data directory's version. Each workspace's `pages.db` has a
   `user_version` of its own: the newest step whose per-workspace part has
   run on that workspace (`db.workspace_version`). A `pages.db` stamped 0
   is at `db.WS_VERSION_BASE` (33): every workspace that existed when the
   stamps came had had every step up to 33 from the runner that walked them
   all, so none had to be walked to get a stamp. A new workspace
   (`seed.create_workspace_files`, which accounts, guests, imports and
   mirrors go through) is stamped `db.SCHEMA_VERSION`, what the running
   code expects. `db.py`'s `CREATE TABLE` statements always describe the
   current shape; nothing is patched on a read.
2. **A release that changes stored shapes ships one numbered step** and
   bumps `SCHEMA_VERSION`. The step is the whole change — moved files,
   rebuilt tables, rewritten rows — written to be re-runnable (it checks
   what it is about to do). Never bump without a step, never add a step
   without bumping. From version 34 on a step declares its scope: a
   *global* part in `migrations.STEPS`, a function of the `users.db`
   connection run at startup, and/or a *workspace* part in
   `migrations.WORKSPACE_STEPS`, a function of one workspace's files run as
   rule 5 says, both under the step's version and name. A global part never
   touches a workspace's files. Steps 20 to 33 are global functions; those
   that change every workspace walk them themselves (`_each_pages_db`,
   which stamps each `pages.db` it changed with the step's version).
3. **The runner goes first.** `migrations.ensure_current()` is the first
   thing `app.create_app` does, before any table is opened. A data
   directory that is *ahead* of the binary (`NewerDataError`), older than
   `MIN_UPGRADABLE` (`TooOldDataError`) or whose upgrade failed is not
   served: the server comes up with one page instead
   (`app._blocked_app`), the same guidance at every address and as a 503
   JSON under `/api`, built by `migrations.guidance()` and printed by
   `manage.py migrate` too. It says that nothing was changed and what to
   run (below, "Below the floor"). Serving a page rather than exiting
   keeps a restarting container from looping on a log line nobody reads.
   `db.connect_users_db()` refuses an outdated file too (`SchemaOutdated`), so
   no code path can read old shapes with new assumptions. That refusal
   covers every `manage.py` command but `migrate`, which is why the Docker
   entrypoint runs `manage.py migrate --global-only` before `manage.py
   setup`. When `migrate` refuses, the entrypoint skips `setup` and starts
   the server anyway, so the page is shown rather than the container
   restarting.
4. **Backup, then step, then stamp.** Before an upgrade changes anything,
   `users.db` is snapshotted with the backup API into
   `backups/<time>-v<N>/` (relative paths kept, plus `manifest.json`),
   even when every pending step is a workspace one and `users.db` gains
   only its stamp. Every workspace's databases join it only when a pending
   step walks them (33 or below). Uploads are never copied: steps move
   directories, never rewrite files. A workspace's own steps copy its
   `pages.db` and `data.db` into that snapshot, at `workspaces/<id>/`,
   just before they run (`backups.add_workspace`). The copy goes into the
   newest automatic snapshot (`backups.latest_auto`) when it holds a data
   directory at least as new as the workspace, else into a new one of
   `users.db` alone. Each copy is a line of the snapshot's
   `workspaces.jsonl`, which the listing adds to the manifest's files and
   checks, so a walk over every workspace never rewrites a growing
   manifest. A workspace the snapshot holds already keeps its first copy.
   So restoring the snapshot gives `users.db` and every workspace as they
   were before the upgrade. Steps run in order; the version is stamped
   after each one (in `users.db` for a global part; in the `pages.db`, in
   the step's own transaction, for a workspace part), so an interrupted
   upgrade resumes at the step that did not finish. An unfinished upgrade
   and its snapshot are named in `backups/upgrade.json` until its global
   steps are done, and every retry reuses that snapshot. So a restart loop
   over a failing step neither piles up copies nor pushes the clean
   pre-upgrade one out. A snapshot that cannot be written (a full disk)
   stops the upgrade before any step, as a `MigrationError`. Once an
   upgrade finishes, the marker goes and only the newest `KEEP_BACKUPS` (3)
   automatic snapshots are kept; hand-made backups are never pruned.
5. **A workspace is upgraded when it is opened.** `db._open_ws_db` reads a
   `pages.db`'s stamp when a connection first opens it, so no request pays
   for the check. When a workspace step is pending for it,
   `migrations.upgrade_workspace` takes this process's lock for that
   workspace (another thread opening it waits there, not on SQLite's busy
   timeout), reads the stamp again, copies the two databases (rule 4) and
   runs the pending steps in order (`migrations.run_workspace_steps`).
   Each runs in one `BEGIN IMMEDIATE` transaction on `pages.db` that also
   writes its stamp, with `data.db` in a transaction of its own, committed
   just before. Then the connection applies the schema statements and
   opens as usual. A step that fails is rolled back and raises a
   `MigrationError` that names the workspace. That workspace answers a 503
   with the startup page's guidance (`migrations.guidance`, error
   `workspace_not_upgradable`) and stays at its last completed step; every
   other one is served, and the next open tries again. After startup a
   background walk (`migrations.warming`, in the app's lifespan) opens
   every workspace still behind (`migrations.is_behind`), one at a time.
   It rests after each as long as it took, at most
   `migrations.WARM_PAUSE_MAX_S` (1 s), so most are upgraded before anyone
   opens them. While no workspace step is above the base it starts nothing
   and opens nothing. The startup pass (`app._startup_maintenance`) leaves
   a workspace that is behind to it. `manage.py migrate` runs the global
   steps and then every workspace's (`migrations.upgrade_workspaces`), for
   an operator who wants the whole upgrade done at once.
6. **The previous release can read `users.db`.** From version 34 on, a
   global step keeps `users.db` readable by the release before it: it adds
   columns and tables, and never renames or drops one in the same release;
   what it replaces is dropped by a step one release later. Review enforces
   this, not code. It prepares for two releases serving one data
   directory during a rolling upgrade. A release refuses a data directory
   newer than itself (`NewerDataError`, rule 3).
7. **Nothing piles up.** Old steps are deleted once `MIN_UPGRADABLE` is
   raised past them; a data directory that old must first run the release
   named in `UPGRADE_VIA`, the newest one that still carries them, and the
   refusal says so. The floor is 19: a directory at 19 or later upgrades
   here (steps 20 to 34). The normalizers are the one exception: they also
   run on every backup restore (`/api/import-data`), because a backup can
   be older than any step, so they stay for as long as such backups are
   accepted. A restored copy stamped below the base (0: taken before the
   stamps, so of any age up to it) goes through the normalizers of the
   steps that walked every workspace. Every copy then runs the workspace
   steps above its stamp (0 counting as the base) before the schema
   statements, and is stamped current (`ws_backup._normalize_copies`).
   From 34 on, a step's workspace part is its own restore normalizer.

## Below the floor

`MIN_UPGRADABLE` is 19, so a directory at version 19 or later upgrades
here, and `UPGRADE_VIA` names the way up from anything older: the server
image `ghcr.io/tim4431/gamma:sha-8708ebb`, built from
the release of 2026-10-01, which carries steps 1 to 24 and brings any data
directory to version 24. Run it once on the same data directory, wait for
its "data directory upgraded" log line, then start the current release,
which takes it from there. The page a blocked server shows spells this out
for Compose, plain Docker and the desktop app, and says first that nothing
has been changed. Raising the floor again is one commit: delete the steps
below it, their frozen schema statements and their tests, move
`MIN_UPGRADABLE`, and point `UPGRADE_VIA` at the newest image that still
has them. Do it once your own deployments have passed the new floor.

## Versions

Steps 20 to 33 are global: they run at startup, and those that change the
workspaces walk every one of them. From 34 on a row says which parts a step
has (rule 2).

| version | step | what it does |
|---|---|---|
| 0–19 | *(removed)* | The unversioned layout and the first nineteen steps (the baseline, the move to `workspaces/<id>/`, the workspace kinds, publisher sessions, integration tokens, MCP OAuth, AI usage, mirrors and their cadence, the sync log and conflict columns, identities, pending memberships, the profile pref, the cloud grant, the mirror page filter). Below the floor: run `UPGRADE_VIA` first ("Below the floor" above) |
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
| 31 | `session_columns` | `sessions` loses `guest_date`, a column the guest login wrote and nothing read: the table is rebuilt in its current shape with its rows, so no one is signed out. Step 25 builds the table as its own time had it, column included (`_V25_USERS_SCHEMA`), so every upgrade passes through here; a table without the column is left as it is |
| 32 | `share_token_workspace` | A share token carries its workspace, `<workspace id>.<secret>` ([api.md](api.md) "Auth model"), so a router can place share traffic by the prefix without a lookup. In `users.db`, as one transaction: every token of `shares` without a dot (the bare secret every share had) gains its share's `workspace_id` and a `.` in front, and the `share_users` rows keyed by it follow. Links sent out before this version stop working: their bare token names no share any more, and opening one counts as an unknown token. The shares keep their settings and invitations; the Share popover shows the new link. Re-runnable: a token with a dot is left as it is |
| 33 | `page_ops_batch_id` | A batch's id is kept on its row of the op log ([collab.md](collab.md) "Ops"), so a retry is answered from the row, across restarts, instead of from memory. Every workspace's `page_ops` gains `batch_id` and `cursor` (`''` on the rows logged before) and the partial unique index `idx_page_ops_batch` on page, client and batch id (`normalize.page_ops_batch_id`, one transaction per file, which a restored older backup goes through too, before the schema statements). A file without an op log is left to the schema statements. `users.db` is untouched; `schema=()`. Re-runnable: a file that has the columns and the index is left as it is |
| 34 | `workspace_prefs` | A workspace part only (`users.db` gains just its stamp). The prefs that name a workspace's pages — open tabs, recents, reading positions: every key but `db.USER_PREF_KEYS` — move into the workspace ([user_db.md](user_db.md) "pages.db"): its `pages.db` gains `workspace_prefs` (`user_id`, `key`, `value`, `updated_at`), filled from the `users.db` `user_prefs` rows under its id, read through a connection of the step's own, when the workspace is first opened after the upgrade (or walked). A row the workspace has at the same or a later time is kept (`ON CONFLICT … WHERE excluded.updated_at > …`), so a rerun never undoes a write made since. A restored backup's copy (`ws` `''`) gets the table empty. `users.db` is not written: its rows stay for the release before (rule 6), and the next release's global step deletes the `user_prefs` rows whose `workspace_id` is not `''` |
| 35 | `folder_links` | A global part only. `users.db` gains `folder_links` ([folder_sync.md](folder_sync.md) "Links kept by the server"): the folders kept as directories on the server's own disk, with the change-log seq and the status of their last round, created from the live statement (`IF NOT EXISTS`, so a rerun changes nothing). No workspace is touched |

## Backups (`gamma/backups.py`)

The runner's snapshots are one use of a general facility: a backup is
`backups/<time>-<label>/` with every SQLite file at its relative path (taken
with the SQLite backup API, so consistent while the server runs), the
`uploads/` directories when asked, and a `manifest.json` (time, label, schema
version, file list, `workspaces`: whether every workspace's databases are
in it, whether uploads are included, `auto` for the runner's, and
`integrity`: each database copy's `PRAGMA quick_check` result,
`gamma/integrity.py`). The workspace copies the runner adds to its
snapshot later (each written under a dot-name and renamed once whole) are
listed as lines of `workspaces.jsonl` beside the manifest, and the listing
counts them among its files. The snapshot is written as `.<name>.part` and renamed
once the manifest, written last, is in. Making that directory claims the name:
a second snapshot of the same label in the same second (another thread, or
`manage.py` beside the server) takes the next second's name instead of
writing into the first one's copy. A failure removes it, so a full disk
never leaves a half copy. A directory without a manifest (a crash mid-copy)
is never listed or pruned; delete it by hand. A database SQLite cannot read
is copied byte for byte instead of failing the whole snapshot, and its
damage is recorded. A damaged copy raises the admins' `db-damage` notice.

- **Take one**: Settings → Server → *Server backups* (admins; *Databases
  only* or *Everything*), `POST /api/admin/backups` (507 when it cannot be
  written), or `manage.py backups --create [--uploads] [--label x]`. The
  migration runner takes a databases-only one labelled `v<N>`, marked
  `auto` (`users.db`, every workspace's databases only when a step that
  walks them is pending, and each workspace's added as its own steps run:
  rule 4), and after a finished upgrade keeps the newest
  `backups.KEEP_BACKUPS` of those; hand-made ones are never pruned.
- **See / download / delete**: the same pane, `GET /api/admin/backups`,
  `GET /api/admin/backups/{name}/download` (a zip of the directory),
  `DELETE /api/admin/backups/{name}`; `manage.py backups [--delete <name>]
  [--prune]` (`--prune` thins the automatic ones only).
- **Restore**: `manage.py backups --restore <name>` with the server stopped
  — copies the files back over the data directory (WAL/SHM sidecars of the
  live files are dropped first) and leaves everything the snapshot lacks as
  it is (in a runner's snapshot without the workspaces: every workspace its
  upgrade had not reached, whose files are still from before it); uploads
  come back only from a backup that carried them. Start the
  server afterwards; it migrates the restored data if the snapshot predates
  the binary (a restore abandons an unfinished upgrade, so that start takes a
  fresh snapshot). Deliberately not an HTTP endpoint: a whole-directory swap
  under a running server is not safe.

Per-workspace backups — the snapshots users keep on the server from
Settings → Backups and the `/api/export` zips — are `gamma/ws_backup.py`
([workspaces.md](workspaces.md) "Export and backups"), a different, smaller thing.
With off-site copies on, each database is also copied to a bucket every
interval it changed, with the uploaded files, and those copies come back
with `manage.py offsite --restore` instead (`gamma/offsite.py`,
[debugging.md](debugging.md#off-site-copies-in-a-bucket)).

## Running it

- **Automatically**: the global steps at every server start (Docker, the
  desktop sidecar, dev), the workspace steps as each workspace is opened
  and in the background walk after it (rule 5). The startup log names the
  snapshot, the steps applied and the workspace steps left to the
  workspaces; the walk logs how many it upgraded. On Windows an upgrade
  that moves directories needs no other process holding the files — which
  is the case at startup. The Docker entrypoint runs `manage.py migrate
  --global-only` before the server, which leaves the workspaces to it.
- **By hand** (server stopped): `python manage.py migrate` runs the global
  steps and then every workspace's pending ones. `--global-only` leaves
  the workspaces to the server, and `--dry-run` lists what would run.
  `--status` only reports, with the number of workspaces behind
  (`migrations.workspaces_behind`: one read of every workspace's
  `pages.db`, which `migrations.status()` leaves out to stay cheap).
  `python manage.py backups` sees, takes, thins or restores snapshots.

A failed step leaves the version at the last completed step; the server
serves the guidance page (rule 3) naming the cause and the snapshot. Fix
the cause and start again (the step resumes, with the same snapshot), or
restore the snapshot with `manage.py backups --restore <name>` and run
the previous release. A failed workspace step refuses that workspace alone
(rule 5); the guidance it answers names the copy of its files in the
snapshot, and the next open, the next walk or `manage.py migrate` tries it
again.

A step that walks every workspace's `pages.db` (22, 23, 25–30 and 33 —
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
def _v35_something(conn: sqlite3.Connection) -> None:
    """One sentence on the new users.db shape."""
    if "new_col" not in _columns(conn, "users"):      # re-runnable: check first
        conn.execute("ALTER TABLE users ADD COLUMN new_col TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _v35_something_workspace(ws: str, pages: sqlite3.Connection, data: sqlite3.Connection | None) -> None:
    """One sentence on the new pages.db shape."""
    cols = _columns(pages, "page_ops")
    if cols and "new_col" not in cols:  # re-runnable; an old copy without the table gets it from the schema
        pages.execute("ALTER TABLE page_ops ADD COLUMN new_col TEXT NOT NULL DEFAULT ''")

STEPS = [..., (35, "something", _v35_something)]                      # its global part, if it has one
WORKSPACE_STEPS = [..., (35, "something", _v35_something_workspace)]  # its workspace part, if it has one
SCHEMA_VERSION = 34   # gamma/db.py
```

- A global part gets an open `users.db` connection and commits its work.
  From 34 on it adds columns and tables only (rule 6) and touches no
  workspace's files. Steps up to 33 that did open them did so themselves
  (`closing(sqlite3.connect(...))` — leave no handle open, Windows locks
  moved directories otherwise), and one that changed every workspace's
  `pages.db` went through `_each_pages_db(step, fn)`, which skips a
  damaged file instead of failing the upgrade.
- A workspace part gets the workspace's id (`''` on a backup's copy), its
  `pages.db` connection, with `db.register_functions` (the notes index's
  triggers call `textnorm` on every block write), and its `data.db`
  connection (None when the workspace or the backup has none). It runs
  inside the runner's transactions, so it does not commit, and before the
  schema statements: it sees the file as the version before left it, and
  `PAGES_SCHEMA` may index what it adds. It is also the restore's
  normalizer (rule 7), so it runs on copies of every shape since the base:
  a table it changes may be missing on an old one, and the schema
  statements create that one. It reaches the files through the two
  connections only; opening the workspace through `db.connect_*` would
  wait on the lock its own run holds.
- Update `USERS_SCHEMA` / `DATA_SCHEMA` / `PAGES_SCHEMA` to the new shape at
  the same time, so a fresh install and an upgraded one end up identical.
  An older step that creates a table from those lists must keep creating
  the shape of its time, so when a step changes a table's shape, freeze the
  statements the older steps used. Steps 20–24 create users.db tables from
  `migrations._V24_USERS_SCHEMA` (users.db at version 24). Step 25 rebuilds
  the users.db tables it re-keys from `migrations._V25_USERS_SCHEMA` (those
  tables at versions 25–30: `sessions` still with `guest_date`); step 31,
  the newest to rebuild one, rebuilds `sessions` from `db.USERS_SCHEMA` itself
  (`migrations._rebuild_table` takes the list), and the next step that
  changes a users.db table freezes what step 31 used. `_each_pages_db`
  applies `migrations._V25_PAGES_SCHEMA` (pages.db at version 25) to every
  workspace's file before the step's own work — the steps up to 27 that
  walk the workspaces (22, 23, 25–27) rely on it — unless the step passes
  `schema=()`, as steps 28–30 and 33 do: that list still has
  `deleted_pages`, which step 27 drops. A workspace part applies no
  statements of its own time first: the file is at the version before it.
- The restore brings a backup's copy to the current shape
  (`ws_backup._normalize_copies`). A copy stamped below the base goes
  through the normalizers of the steps that walked every workspace, in
  `normalize.py`, before the schema statements: `block_columns` (steps 26,
  29 and 30), `page_changes` (step 27), `pages_db_chats` (step 28) and
  `page_ops_batch_id` (step 33). Then every copy runs the workspace steps
  above its stamp, and after the schema statements `block_fts` (step 28)
  and then `folder_blocks` (step 29) run before the content normalizers,
  whose rewrites the index's triggers follow; `highlight_shape` (step 30)
  runs after the content normalizers. From 34 on a step needs no
  normalizer apart from its workspace part. `block_columns`' column
  definitions are `db.BLOCK_HOT_COLUMNS`, and it replaces a generated
  column whose stored definition is not the current one (drop its index,
  `ALTER TABLE DROP COLUMN`, add it again, re-index — with every generated
  column after it, keeping a fresh table's column order): a step that
  redefines `kind` changes it there and runs `block_columns` again, as
  steps 29 and 30 do. The normalizers read the current definitions on
  purpose (`folder_blocks` inserts `blocks_store.STORED_COLUMNS`,
  `block_columns` adds `BLOCK_HOT_COLUMNS`), and they run on files of every
  older shape, so a step that adds a stored block column defines it in
  `BLOCK_HOT_COLUMNS`: `block_columns`, which runs before every other block
  normalizer, then gives an older file the column before `folder_blocks`
  writes it.
- A change to the search normalization (`textnorm.normalize_text`) is a
  step too: the notes index's triggers delete a row's old text as the
  current rules make it, so a workspace's index must be rebuilt
  (`normalize.block_fts`, in a workspace part) before the server writes a
  block of it under the new rules, which the workspace part's run on first
  open guarantees — a restore rebuilds a backup's on its own. Bump
  `textnorm.INDEX_VERSION` with it: that re-extracts the PDF index, which
  is versioned per paper.
- A change to block *content* shapes (a renamed property, a syntax) goes into
  `normalize.py` as a `LIKE`-filtered idempotent rewrite, called from the
  step's workspace part, which a restore runs too. Never repair a shape on
  a read path (a listing, a fetch): reads write nothing, so two copies of a
  workspace only ever differ by what the op log and the change log record.
- Test a global part in `tests/test_migrations.py`: build the old layout by
  hand in a temp data directory (`v24_users_db()` there builds users.db as
  version 24 shaped it), run `ensure_current()`, assert the new shape, run
  it again (no-op), and cover the interrupted-run resume. Test a workspace
  part as `tests/test_lazy_migrations.py` does its test-only step: a
  workspace stamped as the version before, opened (and walked, restored,
  failed and retried).
