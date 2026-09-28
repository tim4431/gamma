# Data migrations

How the data directory moves from one Gamma release's shapes to the next,
safely and without accumulating patches. Code: `gamma/migrations.py`
(runner + numbered steps), `gamma/normalize.py` (content normalization of a
workspace's files), `db.SCHEMA_VERSION`, `manage.py migrate` / `backups`.

## The rules

1. **One version for the whole data directory**, stored as `PRAGMA
   user_version` of `users.db`. `db.SCHEMA_VERSION` is what the running code
   expects. `db.py`'s `CREATE TABLE` statements always describe the current
   shape; nothing is patched lazily on connect any more.
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
| 1 | `baseline` | the pre-workspace world in its final shape: the columns that used to be added on connect, share rows keyed by page (doc-keyed rows resolved or dropped), per-user files normalized (content shapes, legacy tables, `chats.title`) |
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

A step that walks every workspace's `pages.db` (9, 12, 13, 22, 23 —
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
- A change to block *content* shapes (a renamed property, a syntax) goes into
  `normalize.py` as a `LIKE`-filtered idempotent rewrite, called from the
  step *and* automatically on restore. Never repair a shape on a read path
  (a listing, a fetch): reads write nothing, so two copies of a workspace
  only ever differ by what the op log and `deleted_pages` record.
- Test it in `tests/test_migrations.py`: build the old layout by hand in a
  temp data directory, run `ensure_current()`, assert the new shape, run it
  again (no-op), and cover the interrupted-run resume.
