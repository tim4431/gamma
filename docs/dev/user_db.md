# Users, workspaces, databases, and accounts

Where state lives on disk, how a request becomes a user and a workspace, and
everything account-shaped: seeding, the CLI, the admin GUI, storage limits,
the server log. Code: `gamma/db.py` (schemas/paths), `gamma/auth.py`
(middleware + workspace resolution), `gamma/workspaces.py`, `gamma/seed.py`,
`gamma/routers/admin.py`, `gamma/server_settings.py`, `gamma/logbuf.py`,
`backend/manage.py`. The workspace model itself: [workspaces.md](workspaces.md);
schema upgrades: [migrations.md](migrations.md).

## Data directory layout

All state is SQLite + files on disk under a data directory (env
`GAMMA_DATA_DIR`, defaults to the repo's `data/`):

- `users.db` — global. Its `PRAGMA user_version` is the data directory's
  schema version (`db.SCHEMA_VERSION`). Every request reads it, so it has
  WAL journal mode and the `db.BUSY_TIMEOUT_S` (10 s) busy timeout like the
  workspace databases: a reader never waits on a writer. `db.connect_users_db`
  sets both; the session middleware's plain connections (`auth._users_db`)
  pass the timeout too. Tables:
  - `users` — accounts (bcrypt), the guest/admin flags, nullable per-user
    storage-limit overrides, `default_workspace` (the personal workspace).
    `is_guest = 1` rows are throwaway guest accounts (`guest-<8 chars>`,
    empty hash) that `gamma/guests.py` mints per guest login and deletes
    `guest_ttl_hours` after their `created_at` ([guests.md](guests.md));
  - `sessions` — session tokens, with `via` (`cloud` for one a Gamma Cloud
    sign-in minted, else empty); `guest_date` is written with a guest
    session's creation date and read by nothing;
  - `identities` — the Gamma Cloud identity linked to an account
    (`provider`, the account server's `subject`, `username`, `email`, the
    last verified `claims` — username, plan — the Fernet-encrypted
    `refresh_token` and `revoked_at`, when the account server last refused
    that grant); one per account and provider
    ([cloud_accounts.md](cloud_accounts.md));
  - `workspaces` (`id`, `name`, `created_by`, `kind` personal/shared,
    `access` private/public, `public_role`, `quota_mb`) and
    `workspace_members` (`workspace_id`, `username`, `role`
    owner/editor/viewer — a personal workspace has exactly its account);
  - `pending_memberships` — shared-workspace invitations by Gamma Cloud
    username, keyed by workspace and cloud `subject`, waiting for that
    person's first sign-in ([workspaces.md](workspaces.md) "Pending
    invitations");
  - `shares` — share links, one per `(workspace_id, page_id)` or per
    `(workspace_id, folder)` (the other column `''`), with `created_by`,
    `audience` anyone/users/list, `role` view/edit and the comma-separated
    `allowed_users` ([api.md](api.md) "Shares");
  - `user_prefs` — small JSON values per `(username, workspace_id, key)`:
    workspace `''` for the account-wide keys (`db.USER_PREF_KEYS`: the
    preference `profile`, the active AI provider, the AI provider entries
    and the machine-translation keys with their secrets, the seen notices),
    the workspace id for everything that names its pages (open
    tabs, recents, pinned folders, reading positions). A value the server
    edits in part (the provider entries, the translation keys, the seen
    notices) is changed through `db.update_pref(user, key, change)`: the
    read and the write share one `BEGIN IMMEDIATE` transaction, so two
    edits of one list never undo each other;
  - `settings` — admin-tunable server settings (KV), including the
    admin-confirmed `public_url`, the shared AI provider entries
    (`ai_providers`, keys encrypted, [ai.md](ai.md)) and the `cloud_*`
    keys of the cloud sign-in (below);
  - `publisher_sessions` — encrypted publisher cookie snapshots per
    `(username, host)`, imported by the Connector ([extension.md](extension.md));
  - `integration_tokens` — hashed assistant tokens per account and workspace
    (with a `scope`, read or write), and `mcp_oauth` — the OAuth flow's
    expiring records ([mcp.md](mcp.md));
  - `mirrors` — the offline copies of remote workspaces: the local workspace,
    the remote's address and workspace, the write token (Fernet-encrypted
    with the data directory's key), the feed cursors, the last round's
    status and the `page_filter` of a publication ([mirror.md](mirror.md));
  - `jobs` — background jobs (exports, backups, restores, imports, the
    search indexer): owner (`''` for a workspace's own work), workspace,
    kind, parameters, state, last progress, result, error, the produced
    file's name, type and size, and the server process that runs it. Kept
    24 hours after they end ([tasks.md](tasks.md)).
- `jobs/` — the files background jobs produce (`<id>/artifact`, downloaded
  through `/api/jobs/{id}/download`) and the uploads they read
  (`incoming/`). Swept with their rows; not metered against any quota and
  not part of server snapshots.
- `workspaces/<id>/pages.db` — the core data model: the `unified_blocks`
  table. Everything is a block (self-referential `parent_id`, fractional-index
  `position` strings like `a0`, `a0V` from the `fractional-indexing` package).
  Root-level blocks (parent `'root'`) are pages; the pages in Recently deleted
  hang under the reserved `trash` row instead, for 30 days
  ([home_library.md](home_library.md) "Recently deleted"). A page may CARRY a PDF
  attachment (`doc_id` / `source_url` / `original_filename`, read through
  `blocks_store.page_attachment()`). Highlights are blocks with `highlight_id` /
  `pdf_position` in their JSON `properties` column; free notes are blocks
  without. Next to it:
  - `page_ops` — the per-page operation log: one row per applied batch,
    `seq` counting up per page, pruned to the newest 300 rows, 24 hours and
    2 MB of payload ([collab.md](collab.md));
  - `deleted_pages` — a tombstone per deleted page (`page_id`, `deleted_at`,
    `actor`), so a copy of the workspace can tell a deleted page from one it
    never had. Written by `ops.trash_page` and `ops.delete_page` (which also
    drops the page's log rows); cleared when a page is restored or created
    under the same id;
  - `sync_pages` / `sync_conflicts` / `sync_log` — a mirror's per-page base
    tree, the merges it decided on its own and what its rounds did
    ([mirror.md](mirror.md); empty in a workspace that mirrors nothing);
  - `upload_orphans` — the stored files nothing references any more
    (`name`, `since`; "Stored files" below).

  Open it ONLY through `db.connect_pages_db(ws)`:
  WAL journal mode (readers never wait on a writer — several browsers,
  several members), a 10 s busy timeout, and the schema statements (so a
  restored backup gains `page_ops` and `deleted_pages`). Backups copy it with
  the sqlite backup API, which is WAL-safe.
- `workspaces/<id>/data.db` — the workspace's derived data: AI `chats` +
  `chat_history`, `page_snaps` (the recents-card cover thumbnails, synced via
  `/api/page-snaps` — too big for the prefs KV), the viewer's per-document
  manifests `pdf_docs` (byte size, page count, page sizes —
  `gamma/pdf_meta.py`, [pdf_loading.md](pdf_loading.md)) and the two lazily
  built FTS5 search indexes: `pdf_fts`/`pdf_fts_docs` (extracted PDF text per page —
  schema + queries `gamma/pdf_index.py`, extraction `routers/search.py`) and
  `block_fts`/`block_fts_meta` (every non-root block's content keyed by its
  page root, rebuilt per page when the page changed — `gamma/block_index.py`),
  each with a `*_rows` side table mapping a paper / page to its rows' rowids,
  so a paper's or a page's rows are deleted by rowid instead of a scan of the
  whole FTS table. The indexes are rebuilt on demand (a search, the
  background refresher), a page or a few hundred PDF pages per transaction;
  their rows are pruned when pages go. Derived data: no migration step — the
  tables are `CREATE ... IF NOT EXISTS`, and a new `*_rows` table takes over
  the rows already there when it is created. Open it through
  `db.connect_data_db(ws)`: WAL and the busy timeout, like pages.db.
  Shared by the workspace's members. Personal prefs used to live here (a
  `prefs` table); migration step 2 moved them to `users.db`.
- `workspaces/<id>/uploads/` — PDFs, images and generic file attachments
  (`/api/upload-file`), filenames are content sha256[:24] + extension (dedup;
  a PDF the proxy or a clip cached is named by its URL's hash instead), and
  `.partial/`, where a write in progress lives until it is complete
  ("Stored files" below).
- `backups/<time>-<label>/` — snapshots of the whole data directory's
  databases (and, on request, the uploads): the migration runner's `v<N>`
  ones (newest three kept once an upgrade finishes; `backups/upgrade.json`
  names the snapshot of one that has not) and the ones admins take from
  Settings → Server or `manage.py backups`, never pruned
  ([migrations.md](migrations.md) "Backups").
- `backups/workspaces/<id>/<time>-<label>.zip` — one workspace's
  server-kept snapshots (Settings → Backups): `/api/export` zips, full
  copies, at most 20 manual ones per workspace plus the scheduled and
  "pre-restore" ones, deleted with the workspace
  ([workspaces.md](workspaces.md) "Export and backups").
- `backups/deleted/<id>-<name>-<time>.zip` — the final copy of each deleted
  workspace (the same zip format), kept 90 days: older ones are removed when
  the next copy is written ([workspaces.md](workspaces.md) "Export and
  backups").
- `backups/integrity.json` — the latest failed integrity check per database
  file (`gamma/integrity.py`), what the admins' `db-damage` notice reads;
  `backups/tasks/` holds the backup tasks.

Workspace ids are random tokens (`workspaces.new_workspace_id`), so renaming
an account or a workspace never moves files. `db.safe_ws_id` / `safe_doc_id`
guard every path built from one. The background passes over every workspace
(startup's schema pass, the trash sweeper, the stored-file reconciliation)
walk `db.workspace_ids()`: the directories on disk whose name is a
workspace id, whether or not users.db still lists them. The migration steps
keep their own walk.

**Connections are closed deterministically.** `connect_users_db`,
`connect_pages_db` and `connect_data_db` return `db.Connection`: its `with`
block commits (rolls back on an exception) and then closes it. sqlite3's own
Connection only commits there and leaves the closing to the garbage
collector. On Windows an open handle keeps a deleted workspace's directory
on disk and its WAL files locked. So a connection is never used after its
`with` block, and a raw `sqlite3.connect` (a backup copy, a snapshot) is
wrapped in `contextlib.closing`. The request handlers that open them are
sync defs, in the threadpool ([debugging.md](debugging.md) "Gotchas worth
knowing").

The journal mode lives in the file. A new workspace's `pages.db` and
`data.db` are created in WAL mode (`seed.create_workspace_files`), and the
startup pass opens every existing one. A connection reads the mode and
switches a file still in rollback mode (`db._wal`), without waiting: the
switch needs the file to itself, and SQLite answers "database is locked" at
once while another connection has it open. A connection that cannot switch
works in the file's mode until a later one does.

## Stored files

`gamma/storage.py` writes them, `gamma/upload_gc.py` keeps track of the ones
nothing uses any more.

- **Written whole.** Every writer of a stored file goes through
  `storage.write_atomic`: uploads, images, ink, imports (`storage.store_file`
  / `store_pdf`), the PDF proxy's cache, a clip's copy, the AI chat's
  re-download, a mirror's pull, a restore. The bytes go to a temp file in
  `uploads/.partial/`, are flushed to disk and renamed over the name. A write
  cut short (a full disk, a killed process) leaves nothing under the name,
  never a truncated file that a later upload of the same bytes would take
  for stored.
- **Dedup repairs.** A dedup hit compares the sizes and rewrites a copy an
  older write left short. For a PDF only when the stored bytes are the start
  of the new ones: a PDF whose embedded annotations were stripped keeps its
  name with other bytes.
- **Checked names.** Bytes that arrive under a name someone else chose (a
  mirror's pull) are checked first (`storage.matches_name`: a PDF must be a
  PDF, anything else must hash to its name). Bytes that are not what the
  name says are dropped, so a captive portal's page never becomes a stored
  PDF. Nor does half of one: a pull that ends short of the length the
  remote announced is refused before the check ([mirror.md](mirror.md)).
- **What counts as a reference.** `storage.upload_refs`, the one grammar:
  `/api/uploads/<name>` anywhere in a block's content or properties (images,
  file chips, `ink_url`, `source_url`) and a block's `doc_id` (its PDF,
  `<doc_id>.pdf`). A mirror's file transfer and a page export's file list
  read references through it too.
- **Unreferenced files are kept for 30 days.** When the last reference to a
  file goes, the file stays on disk and is served as before. Its name is
  recorded in `upload_orphans` with the time. An undo, a cut pasted in a
  later batch, a block moved to another page, an AI edit or a re-attached
  PDF brings a reference back. The writer then clears the record in its own
  transaction (`upload_gc.claim`: the op batches, `PUT
  /blocks/{id}/children`, `blocks_store.create_page`). An upload of the same
  bytes re-dates the file (`os.utime`), which restarts both the upload grace
  and the 30 days. It does so under the workspace's `upload_gc.guard`, the
  lock the purge holds from its check to its delete, so a re-upload lands
  either before the check (the file stays) or after the delete (the bytes
  are written again). Until it is purged, an unreferenced file counts against
  the quota like any other.
- **Who notices.** An op batch knows the names it stopped referencing
  (`dropped_uploads` on `apply_ops`'s result; `ops.delete_page` and the
  subtree replace likewise). It hands them to `upload_gc.schedule`, a check
  on the module's own thread a couple of seconds later, never in the
  request. A batch that drops nothing (typing in a block that keeps its
  image, a folder change on a PDF page) costs nothing.
- **The full pass.** `upload_gc.reconcile` is one scan of the blocks that
  mention an upload, diffed against the directory listing. It runs for every
  workspace a minute after startup and every six hours, and catches what
  writers outside the op path (imports, a restore, a mirror) left behind. It
  records unreferenced files older than the 15-minute upload grace (an
  upload is stored before the block that names it). It clears the records
  of files that are referenced again or gone, removes day-old temp files
  from `.partial/`, and purges. A workspace whose pages.db cannot be opened
  is logged as an error and the pass goes on to the next.
- **The purge.** A file whose record and mtime are both more than 30 days
  old is deleted. The references are read again under the workspace's write
  lock first, so no batch can add one meanwhile. The purge refuses (a
  warning in the server log, nothing deleted) when the pages.db looks wrong:
  no root row, no pages, a failing `PRAGMA quick_check`, or more files at
  once than one purge may take (over 100, or over 10 and a fifth of the
  workspace's files). A restore starts the restored records' 30 days over
  (`upload_gc.restart_clocks`), so it never makes a file due at once.

## Schema versions

There is no lazy `ALTER TABLE` on connect any more: `db.py` always creates
the current shape, and an existing data directory is brought up to it by the
numbered steps in `gamma/migrations.py` — at every server start, with a
snapshot first, refusing a newer directory. `db.connect_users_db()` raises
`SchemaOutdated` on an old file so nothing reads it with new assumptions.
Content normalization of a workspace's files (`gamma/normalize.py`) runs in
the baseline step and on every backup restore. Rules, versions and how to
write a step: [migrations.md](migrations.md).

## Auth model

`session` cookie → middleware resolves `request.state.user` (+ `is_guest`,
`is_admin`, `default_ws`). It reads users.db in a worker thread
(`auth._off_loop`, with its own `AUTH_THREADS` tokens), never on the event
loop, and caches nothing: a session revoked by a logout, a password change
or `manage.py` (another process) ends with the next request. A guest
account past its lifetime is deleted by the middleware on its next request
(which then runs signed out), or by the sweeper `gamma/guests.py` runs in the app lifespan every 10 minutes. Both go
through `workspaces.delete_account`, the one account deletion, which the
admin API and `manage.py delete-user` use too ([guests.md](guests.md)).

Which workspace a request reads or writes is a second decision
(`require_ws` / `resolve_ws` / `require_ws_writer` —
[workspaces.md](workspaces.md)): `?ws=` or the `X-Gamma-Workspace` header,
else the account's default workspace, gated by membership and role. Share
tokens grant access to ONE page (any page — paper or plain notes), or to the
pages filed in ONE folder, of one workspace for the audience the sharer
chose: endpoints that support shared views resolve the workspace from the
share token (`resolve_ws` — the token wins over the visitor's own session
for choosing whose data is read) and confine reads to the pages in reach
(`share_scope` + `assert_block_in_scope`); write endpoints require a member
with the editor or owner role (`require_ws(write=True)`), except the block
writers, which accept an `edit` share through `require_ws_writer` under the
same scope. Keep that distinction when touching endpoints. Full
endpoint/auth table: [api.md](api.md).

**Sign in with Gamma Cloud** (`gamma/cloud_auth.py`,
[cloud_accounts.md](cloud_accounts.md)) is a second way to mint a session
row, not a second identity: the callback verifies the account server's ID
token, finds the `identities` row (or links, claims or provisions one per
the admin's policy), inserts the same `sessions` row the password login
does (marked `via = 'cloud'`) and sets the same cookie. The hourly grant
check (`gamma/cloud_sync.py`) deletes those rows, and only those, once the
account server refuses the account's grant. An account the cloud
provisioned has an EMPTY password hash and the password login refuses it.
`manage.py set-password` gives it one. The settings live in the `settings`
KV: `cloud_issuer`, `cloud_client_id`, `cloud_client_secret`
(Fernet-encrypted with the data directory's key), `cloud_policy` and
`cloud_share_host`. A provisioned container takes them from `GAMMA_CLOUD_*`
instead. The same KV keeps `cloud_device_id` (this install's name at the
account server) and `cloud_server_url` (a sidecar's loopback address for the
server list).

## First-run seeding

The APP seeds the first admin, not launcher scripts — `seed.ensure_admin_seed()`
runs at startup and creates an "admin" account (with its personal workspace)
and a RANDOM password printed once to the console (env-overridable via
`GAMMA_ADMIN_USER` / `GAMMA_ADMIN_PASSWORD`) ONLY while zero non-guest
accounts exist. Deliberately not keyed on "no admin exists": auto-adding an
admin login to an upgraded multi-user instance would be a backdoor — those
get a startup hint to run `manage.py set-admin`. `seed.create_workspace_files`
writes a workspace's empty files. `workspaces.ensure_personal` gives an
account its personal workspace; with `welcome=True` (every account-creating
path) `seed.seed_welcome` adds the Welcome page and its sample PDF
([onboarding.md](onboarding.md)). No guest account is seeded: each guest
login makes its own.

## manage.py CLI

User CRUD: `create-user` (without a password: an account the password
login refuses until `set-password`), `set-password`, `set-admin`,
`rename-user`, `delete-user` (`workspaces.delete_account`: also the
workspaces only that account owned, each copied to `backups/deleted/`
first — refused when a copy cannot be written; guest accounts too), `list-users`,
`list-identities` / `link-identity` / `unlink-identity` (the Gamma Cloud
identity of an account, [cloud_accounts.md](cloud_accounts.md)),
`sweep-guests [--all]` (delete the expired guest accounts now; `--all`
every guest), `setup` (idempotent: a personal workspace for every account +
missing files; creates no guest). Workspaces: `list-workspaces`,
`create-workspace <name> <owner> [shared [public [viewer|editor]]]`, `set-member
<ws> <user> <owner|editor|viewer|none>`, `set-access <ws> <private|public>
[viewer|editor]`. Data directory: `migrate`
(`--status`, `--dry-run`), `backups` (list, naming automatic and damaged
ones; `--create [--uploads]`, `--delete`, `--restore`, `--prune` — the
automatic pre-upgrade snapshots only). Every command but
`migrate`/`backups` refuses an outdated data directory.

`rename-user` updates every row that names the account: users, sessions,
shares, memberships, prefs, identities, and what the account controls (its
offline copies' `mirrors.owner`, its `ai_usage`, its publisher connections
— re-sealed, since the snapshot names its account — and the invitations it
sent). It also renames the owner of its backup tasks
(`backup_schedule.renaming`): the task files are rewritten under their
locks, and a rename while one of them runs is refused. No other files move.
`routers/admin.rename_account` is the one rename, shared with the Users
pane, and `rename_account_rows` the one list of rows; nothing of the
account passes to a later account created under the old name.

`set-password` revokes the account's existing browser sessions and integration
tokens, matching password changes through the admin API.

## User management GUI

`gamma/routers/admin.py` (`/api/admin/users*`, `/api/admin/workspaces`),
frontend [SettingsUsers.jsx](../../frontend/src/settings/SettingsUsers.jsx): admins
manage accounts from Settings → Users; non-admins get the same pane as "You"
(their single row from session + `/api/quota`, since `/api/admin/*` is
admin-only). Each account row lists the account's personal workspaces
(from `/api/admin/workspaces`) with Open / Manage — the workspace dialog in
admin mode. Shared workspaces are not per account and are managed from
Settings → Server
([SettingsWorkspacesAdmin.jsx](../../frontend/src/settings/SettingsWorkspacesAdmin.jsx),
on top of `/api/admin/workspaces` + the workspace API, which admins pass
without membership — [workspaces.md](workspaces.md)). Backups are not here: every workspace's
export/import lives on its row in Settings → Workspaces and its snapshots
in Settings → Backups ([workspaces.md](workspaces.md)); admins reach any
workspace from Settings → Server (`/api/export?user=` still serves an
account's default workspace to scripts). A guest's workspace can be
exported but never restored into. Rails: a guest account takes storage
limits and deletion but no password, admin flag or new name; no
self-delete; the last admin can't be demoted or deleted. Deleting an
account (`workspaces.delete_account`) deletes its personal workspaces and
the shared ones it alone owned (the response lists them); shared workspaces
with another owner survive. Each deleted workspace is copied to
`backups/deleted/` first, all or nothing: when a copy cannot be written the
account stays (507).

## Storage limits

`gamma/server_settings.py`: per-account max upload size (`max_upload_mb`) and
total quota (`quota_mb`, 0 = unlimited); server-wide defaults in the users.db
`settings` KV, per-user overrides as nullable `users` columns (NULL = inherit,
explicit JSON null clears). An account's limits apply to uploads into its
PERSONAL workspaces, and its usage is their `uploads/` directories together
— nothing anyone uploads into a shared workspace counts against a person. A
shared workspace is checked against the server-wide per-file cap and its
own `workspaces.quota_mb` (NULL = unlimited; admins set it in Settings →
Workspaces or Members & sharing). `workspace_quota(ws)` resolves the pair
that applies. `check_upload_allowed(ws, n)` hard-gates `/api/uploads`, `/api/upload-image`,
`/api/upload-file` and the imports (413 over per-file, 507 over quota;
already-stored hashes always pass — dedup adds no bytes); `can_store`
soft-gates best-effort caches (proxy `save=1`, ai_context re-download).
`GET /api/quota` = the request's workspace: the limits that apply,
`used_bytes` (the account's total for a personal workspace, the workspace's
own for a shared one), `workspace_bytes` (this workspace's), and `account`
— the person whose limits these are, "" for a shared workspace;
deliberately NOT part of `/api/session` (identity only). Backup-restore imports are unmetered.
Details + UI in [settings.md](settings.md).

## Server dashboard and log

Settings → Server opens with a **Dashboard** (`GET /api/admin/server-info`,
`gamma/version.py`, [api.md](api.md)): three tiles — the build (`v<version>`
from `GAMMA_VERSION`, stamped by the Docker build and the desktop shell;
"dev build" for a checkout), uptime, and warnings · errors logged since
startup (`logbuf.counts()`) — plus an Updates row comparing the build with
the newest GitHub release (cached; "Check now" refetches; `GAMMA_UPDATE_CHECK=off`
for air-gapped servers). A Docker image built from a push or a branch
rather than a release is `v<tag>-dev.<n>` (`n` commits after the newest
`v*` tag) and also compares against its branch (`GAMMA_BRANCH`): commits
there that this build lacks are an update to `v<tag>-dev.<m>`, the version
that branch's next image carries, with a "What's new" link to GitHub's
comparison. A Docker server cannot update itself, so an
available update only says which image to pull; the desktop app updates
on its own. Things worth an admin's eye are logged at WARNING — a
share-link visitor over the write throttle, an address probing unknown
share links ([api.md](api.md) "Link visitors") — so the tile turns amber
and the log's "Warnings" filter shows them. An admin who never opens the
pane still hears of a newer release and of logged errors: both are notices
(`gamma/notices.py`, `GET /api/notices`), the red dot on the account
button that leads to this pane — [settings.md](settings.md) "Notices".

**Databases** (`POST /api/admin/check-databases`, `gamma/integrity.py`):
"Check now" runs SQLite's `PRAGMA quick_check` on `users.db` and on every
workspace's `pages.db` and `data.db` as they are, changing nothing, and
lists each damaged file with what SQLite found. Every snapshot checks its
database copies the same way (server backups, workspace snapshots, the
copy a restore is about to put in place). The latest failure per file is
kept in `backups/integrity.json` until a later check of that file passes,
and while any is there admins get the `db-damage` notice. A damaged file is
best replaced from a backup: a workspace restore, or a server snapshot with
the server stopped.

`gamma/logbuf.py`, `GET /api/admin/logs?after=<seq>`: all backend logging goes
through `logbuf.log` (a `logging` logger — use it, not `print()`), which tees
to the console and a scrubbed in-memory ring buffer (2000 entries, gone on
restart) shown admin-only in Settings → Server → "Log", with a level filter
(All / Warnings / Errors) and a badge per non-info line. Secret-shaped
substrings (Bearer/sk- keys, `password=`/`token=` pairs, 40+-char urlsafe runs
— session/share tokens) are masked at insert time; the one-time seeded admin
password in `seed.py` stays a raw `print()` on purpose and must never route
through the logger. `uvicorn.access` is deliberately not captured (its lines
carry `?share=` query strings); the middleware's `[http]` line covers requests
path-only.
