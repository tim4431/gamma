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
  pass the timeout too. An account is keyed by `users.id`, a random token
  (`db.new_account_id`); its unique `username` is only the name people see
  and sign in with ("Accounts are named by id" below). Tables:
  - `users` — accounts (`id`, `username`, bcrypt hash), the guest/admin
    flags, nullable per-user storage-limit overrides, `default_workspace`
    (the personal workspace).
    `is_guest = 1` rows are throwaway guest accounts (`guest-<8 chars>`,
    empty hash) that `gamma/guests.py` mints per guest login and deletes
    `guest_ttl_hours` after their `created_at` ([guests.md](guests.md));
  - `sessions` — session tokens, with `via` (`cloud` for one a Gamma Cloud
    sign-in minted, else empty);
  - `identities` — the Gamma Cloud identity linked to an account
    (`provider`, the account server's `subject`, `user_id`, `email`, the
    last verified `claims` — username, plan — the Fernet-encrypted
    `refresh_token` and `revoked_at`, when the account server last refused
    that grant); one per account and provider
    ([cloud_accounts.md](cloud_accounts.md));
  - `workspaces` (`id`, `name`, `created_by`, `kind` personal/shared,
    `access` private/public, `public_role`, `quota_mb`) and
    `workspace_members` (`workspace_id`, `user_id`, `role`
    owner/editor/viewer, `added_by` — a personal workspace has exactly its
    account);
  - `pending_memberships` — shared-workspace invitations by Gamma Cloud
    username, keyed by workspace and cloud `subject`, waiting for that
    person's first sign-in ([workspaces.md](workspaces.md) "Pending
    invitations");
  - `shares` — share links, one per `(workspace_id, page_id)` or per
    `(workspace_id, folder)` (`folder` a folder block's id; the other
    column `''`), with `created_by`,
    `audience` anyone/users/list and `role` view/edit; `share_users`
    (`token`, `user_id`, `role`) — the people a link invites, each with
    their own role ([api.md](api.md) "Shares");
  - `user_prefs` — small JSON values per `(user_id, workspace_id, key)`:
    workspace `''` for the account-wide keys (`db.USER_PREF_KEYS`: the
    preference `profile`, the active AI provider, the AI provider entries
    and the machine-translation keys with their secrets, the seen notices),
    the workspace id for everything that names its pages (open
    tabs, recents, reading positions). A value the server
    edits in part (the provider entries, the translation keys, the seen
    notices) is changed through `db.update_pref(user, key, change)`: the
    read and the write share one `BEGIN IMMEDIATE` transaction, so two
    edits of one list never undo each other;
  - `settings` — admin-tunable server settings (KV), including the
    admin-confirmed `public_url`, the shared AI provider entries
    (`ai_providers`, keys encrypted, [ai.md](ai.md)) and the `cloud_*`
    keys of the cloud sign-in (below);
  - `publisher_sessions` — encrypted publisher cookie snapshots per
    `(user_id, host)`, each sealed with its account's id, imported by the
    Connector ([extension.md](extension.md));
  - `integration_tokens` — hashed assistant tokens per account and workspace
    (with a `scope`, read or write), and `mcp_oauth` — the OAuth flow's
    expiring records ([mcp.md](mcp.md));
  - `mirrors` — the offline copies of remote workspaces: the local workspace,
    the remote's address and workspace, the write token (Fernet-encrypted
    with the data directory's key), the feed cursors, the last round's
    status and the `page_filter` of a publication ([mirror.md](mirror.md));
  - `jobs` — background jobs (exports, backups, restores, imports, the
    search indexer): owner (an account id, `''` for a workspace's own
    work), workspace, kind, parameters, state, last progress, result, error, the produced
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
  ([home_library.md](home_library.md) "Recently deleted"). Folders and labels
  are blocks too, under two more reserved rows, `folders` (nested: a folder's
  children are its subfolders) and `labels` (flat): `content` is the name,
  `position` the order, `properties.pinned` a pin; a page is filed by id, in
  `properties.folders` and `properties.labels` (lists of block ids;
  [home_library.md](home_library.md) "Folders and labels"). A page may CARRY a PDF
  attachment (`doc_id` / `source_url` / `original_filename`, read through
  `blocks_store.page_attachment()`; a page stores no `source_url` that is
  its own `doc_id`'s stored copy, `/api/uploads/<doc_id>.pdf`, which the
  attachment derives). Highlights, link regions and ink groups on a PDF page
  are blocks with a `pdf_position` in their JSON `properties` column (the
  page, the page size once, the rectangles: `gamma/highlights.py`,
  [api.md](api.md) "The highlight shape"); the block id is the highlight's
  id. Free notes are blocks without. Beside the seven columns blocks always had (`id, parent_id,
  position, content, properties, created_at, updated_at`) are the typed hot
  fields (`db.BLOCK_HOT_COLUMNS`), each indexed:
  - `page_id` — stored, set by every writer: the page a row lives under, the
    page's own id on the page row; `folders` / `labels` on every folder and
    label block (the trees are pseudo-pages to the op path,
    [collab.md](collab.md) "The folder and label trees"). `''` on the
    reserved parentless rows (`root`, `trash`, `folders`, `labels`); a page
    in Recently deleted and its blocks keep theirs
    (the trash entry a 404 names is found through it). Op inserts take the
    batch's page, a move to another page rewrites the moved subtree's
    (`ops.move_across_pages`), and the writers outside the op path —
    `create_page`, the subtree replace, the importers, the clip, a backup
    merge or restore — write it with the row. So "which page is this block
    in" (`blocks_store.page_root_id`, the op batches, a share's reach) is one
    indexed read instead of a walk up the parents. The test suite checks
    every written workspace against the parent walk after each test
    (`tests/conftest.py` `page_id_drift`).
  - `kind` — generated (VIRTUAL: computed when read, never written): NULL on
    the reserved rows, `page` under `root` or `trash`, `folder` / `label`
    in the trees (from `page_id`), else `ink` (an
    `ink_url` key), `text_box`, `sheet` (an object there), `link` (a
    non-empty `link_url` or `link_page_id`), `highlight` (a
    `pdf_position` object), `note`. The
    block dict and block search report it.
  - `doc_id` — generated: `properties.doc_id`. "Which page carries this
    PDF" (`page_for_doc`) and "which files are still in use" read its
    partial index (`WHERE doc_id IS NOT NULL`).

  The op log and the mirror carry `properties` alone; nothing writes `kind`
  or `doc_id`. Next to it:
  - `page_ops` — the per-page operation log: one row per applied batch,
    `seq` counting up per page, pruned to the newest 300 rows, 24 hours and
    2 MB of payload ([collab.md](collab.md));
  - `page_changes` — the workspace's change log, one row per page that
    exists or ever existed (`page_id`, `seq` unique across the workspace,
    `kind` `live` or `deleted`, `at`, `actor` an account id like the op
    log's). Every write to a page moves its row to the next seq
    (`blocks_store.touch_page`, inside the write's transaction); trashing
    and deleting for good write `deleted`, creating and restoring `live`.
    The change feed reads it ([collab.md](collab.md) "The change feed"), so
    a copy of the workspace finds what moved and tells a deleted page from
    one it never had;
  - `sync_pages` / `sync_conflicts` / `sync_log` — a mirror's per-page base
    tree, the merges it decided on its own and what its rounds did
    ([mirror.md](mirror.md); empty in a workspace that mirrors nothing);
  - `upload_orphans` — the stored files nothing references
    (`name`, `since`; "Stored files" below);
  - `chats` / `chat_history` — the AI chat ([ai.md](ai.md) "Chat history
    buckets"): one active conversation per `bucket` (a page's id, a
    folder's id, or `home`) with its `title` and its version
    `updated_at`, and the bucket's earlier conversations by `id`. They live
    with the pages they are about, so a backup, a restore and a Gamma
    export carry them with no code of their own; a page's chats go when the
    page is deleted for good (`ops.delete_page`), not while it is in
    Recently deleted; a deleted folder's are filed into `home`'s history;
  - `block_fts` — the notes index ("The notes index" below).

  Open it ONLY through `db.connect_pages_db(ws)`:
  WAL journal mode (readers never wait on a writer — several browsers,
  several members), a 10 s busy timeout, the SQL function the notes
  index calls (`db.register_functions`) and the schema statements (only
  `CREATE … IF NOT EXISTS`: no column is added on connect). A raw
  `sqlite3.connect` that writes blocks or reads the notes index (a backup
  copy, a migration step, the Gamma export's in-memory file) registers the
  function itself. A restored backup is brought to the current shape
  before anything reads it or anything live is touched
  (`ws_backup._normalize_copies`, which runs the `normalize.py` steps of
  migrations 26–30 in the order [migrations.md](migrations.md) "Writing a
  step" gives). A restore never replaces the live
  change log ([workspaces.md](workspaces.md) "Export and backups"). Backups
  copy it with the sqlite backup API, which is WAL-safe. Nothing may
  `VACUUM` a pages.db: `unified_blocks` has no `INTEGER PRIMARY KEY`, so
  VACUUM may give its rows new rowids, which the notes index keys on (no
  code does; keep it that way).
- `workspaces/<id>/data.db` — what the workspace can lose and rebuild: the
  file may be deleted, and everything in it comes back on demand. Nothing a
  person wrote lives here. It holds `page_snaps` (the recents-card cover
  thumbnails, synced via `/api/page-snaps` — too big for the prefs KV;
  captured again as a PDF is read), the viewer's per-document manifests
  `pdf_docs` (byte size, page count, page sizes — `gamma/pdf_meta.py`,
  [pdf_loading.md](pdf_loading.md); computed again when a PDF opens) and
  the PDF text index `pdf_fts` / `pdf_fts_docs` (extracted PDF text per
  page — schema + queries `gamma/pdf_index.py`, extraction
  `routers/search.py`; extracted again by the next search), with its
  `pdf_fts_rows` side table mapping a paper to its rows' rowids, so a
  paper's rows are deleted by rowid instead of a scan of the whole FTS
  table. The index is built on demand (a search starts the workspace's
  indexing job), a few hundred PDF pages per transaction; the rows of
  papers no block carries are purged when a page or a PDF goes
  (`pdf_index.purge_unused`). Derived data: no migration step — the
  tables are `CREATE ... IF NOT EXISTS`, and a new `pdf_fts_rows` takes
  over the rows already there when it is created. Open it through
  `db.connect_data_db(ws)`: WAL and the busy timeout, like pages.db.
  Shared by the workspace's members.

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

**The notes index.** `block_fts` in pages.db is an FTS5 table with
external content: its content is the view `block_fts_src(rowid, block_id,
page_id, content)` over `unified_blocks`, keyed by the block's rowid, and it
holds no text of its own (snippets and the `block_id` / `page_id` columns
are read back through the view). The view shows every block inside a page
(highlights, ink, text boxes and links included) but no page's own row,
since a title is found by the library list, and no reserved row:
`parent_id NOT IN ('root', 'trash') AND page_id != ''`. Folder and label
names are rows of their trees, so they are indexed too; no search reaches
them, since every search names the pages it keeps to. The indexed text is
`textnorm(content)` (`textnorm.normalize_text`, registered on the
connection) cut at `db.NOTES_INDEX_CHARS` (20,000). A page in Recently deleted keeps its rows (its blocks keep
their `page_id`), and every search keeps to the pages it reaches
(`block_index.search_blocks` takes the page ids), so trashing and
restoring, which move only the page's own row, change nothing in the
index. Three triggers keep it current inside the writing transaction, for
the rows the write touched: after an insert, after a delete, and after an
update of `content`, `page_id` or `parent_id` that changes one of them
(the FTS5 `delete` of the old row as the view showed it, then the insert
of the new; a properties-only edit or a reorder costs the index nothing).
So every writer — an op batch, the subtree replace, a
cross-page move, an import, a restore — is searchable the moment it
commits, and inside its own transaction already. Two rules keep it exact:
a trigger's `delete` must hand FTS5 what the current rules make of the old
row, so a change to `normalize_text` ships with a migration step that
rebuilds every workspace's index (`textnorm.INDEX_VERSION` versions the
PDF index only); and the rowids must never be renumbered (no `VACUUM`).
`INSERT INTO block_fts(block_fts) VALUES('rebuild')` (`block_index.rebuild`)
is the whole recovery path: migration step 28 and every restore run it
(`normalize.block_fts`), and so does Settings' rebuild
(`POST /api/search-reindex`). An op batch pays the FTS write of each row it
changed: a 5,000-block import takes about 190 ms with the index against
35 ms without, a one-block edit about 0.1 ms more, a rebuild of 5,000
blocks about 55 ms (measured 2026-10).

Workspace ids are random tokens (`workspaces.new_workspace_id`), so renaming
an account or a workspace never moves files. `db.safe_ws_id` / `safe_doc_id`
guard every path built from one. The background passes over every workspace
(startup's schema pass, the trash sweeper, the stored-file reconciliation)
walk `db.workspace_ids()`: the directories on disk whose name is a
workspace id, whether or not users.db still lists them. The migration steps
keep their own walk.

**Accounts are named by id.** An account is `users.id` everywhere it is
stored: the `user_id` columns, `created_by` /
`added_by` / `invited_by` / `owner`, `share_users`, the sealed publisher
snapshots, the backup task files (`owner`), and in every workspace's
`pages.db` the op log's and the change log's `actor` and a trashed page's
`deleted_by` (beside the labels of writers that are no account,
`link:<name>` and `mirror` — `auth.actor_of`). The username is what people
see and type: the API takes and answers usernames (members, invitations,
share people, the admin's user rows, presence), and nothing a client sends
or reads needs an id. The two translations are `db.account_id(conn,
username)` and `db.account_name(conn, user_id)`, each one indexed lookup;
`db.account_names(conn, ids)` is the second in one query for a list, and
lists of people are joined to `users` in their own query, never looked up
row by row. A request carries both (`request.state.user_id`, what storage
takes; `request.state.user`, what is shown and logged), and so does a page
socket's peer. A rename changes `users.username` and nothing else that
names the account ("manage.py CLI" below); a
deleted account's id is never reused, so nothing of it passes to a later
account that takes its name.

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
nothing uses.

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

`db.py` always creates the current shape and alters nothing on connect. An
existing data directory is brought up to it by the numbered steps in
`gamma/migrations.py` at every server start, with a snapshot first. A
directory this build cannot upgrade (newer, or below `MIN_UPGRADABLE`) is
not served: the server shows one page saying what to run instead. A server
linked to Gamma Cloud reports its build and schema version there
([cloud_accounts.md](cloud_accounts.md)). `db.connect_users_db()` raises
`SchemaOutdated` on an old file so nothing reads it with new assumptions.
Content normalization of a workspace's files (`gamma/normalize.py`) runs on
every backup restore. Rules, versions, the floor and how to write a step:
[migrations.md](migrations.md). A server linked to Gamma Cloud
reports its schema version, with its build, to the account server
([cloud_accounts.md](cloud_accounts.md), "The server list").

## Auth model

`session` cookie → middleware resolves `request.state.user_id` and
`request.state.user` (+ `is_guest`, `is_admin`, `default_ws`);
`require_user_id` / `require_personal_user_id` hand an endpoint the id,
`require_user` / `require_admin` the username. It reads users.db in a worker thread
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
pages filed in ONE folder or below it (by the folder's id), of one workspace for the audience the sharer
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

`rename-user` changes the account's username and nothing else that names
it: every row, file and op log names the id. The one convention that
follows is the name of a personal workspace of the account still named
after it. Sessions, share links, tokens, backup tasks and running jobs go
on as they were, and no file moves. `routers/admin.rename_account` is the
one rename, shared with the Users pane. Commands take usernames throughout
and translate them to ids (`db.account_id`).

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
