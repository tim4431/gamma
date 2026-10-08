# Workspaces

An account identifies a person. A workspace holds a library: pages, uploaded
files, chats and search indexes. Each browser tab opens one workspace.

Use the account menu to switch libraries. **Settings → Workspaces** lists the
libraries you can open and provides their export, import and management
actions. **Settings → Backups** manages saved workspace snapshots.
Administrators manage shared workspaces under **Settings → Server** and
each account's personal workspaces on its row under **Settings → Users**.

## Personal and shared libraries

Every account starts with a personal workspace, holding the Welcome page
and its sample PDF ([onboarding.md](onboarding.md)), and can create more
(those start empty). Each personal workspace has exactly one member, its
owner. Use page share links
to give others access to individual pages; personal workspaces cannot invite
additional workspace members.

A server administrator creates shared workspaces and chooses their owner.
The owner can then invite existing accounts:

| Role | Read pages | Edit pages | Manage members, rename, delete or restore |
|---|---|---|---|
| Viewer | Yes | No | No |
| Editor | Yes | Yes | No |
| Owner | Yes | Yes | Yes |

A shared workspace must retain at least one owner. To transfer ownership,
make another member an owner before removing or demoting the current owner.

### Pending invitations (Gamma Cloud usernames)

When this server signs people in with Gamma Cloud
([cloud_accounts.md](cloud_accounts.md)), an owner or an administrator can
also invite a person who has no account here yet by their **Gamma Cloud
username**. The invite editor then offers "On this server" or "Gamma Cloud
username". Such an invitation grants edit or view access, never ownership:

1. The server asks the account server for the account with exactly that
   username (`GET <issuer>/api/lookup/username?u=`, bearer access token).
   The answer is the account's stable id, its **subject**. If no account
   has that username, the invitation is refused.
2. If a local account is already linked to that subject (`identities`),
   that account becomes a member immediately.
3. Otherwise a row in `pending_memberships` (users.db: workspace, subject,
   the username as typed in lowercase, role, inviter, time; one per
   workspace and subject) records the invitation. Inviting the same
   username again changes its role.
4. **The claim.** Each cloud sign-in calls
   `workspaces.claim_pending_memberships(user_id, subject)` from
   `cloud_auth.resolve_account`, after the local account is known. The local
   account can be newly provisioned, claimed by username, linked from
   Settings → Account & sync, or already linked. Every pending row for that subject
   becomes a `workspace_members` row with its role, and the pending rows are
   deleted. If the person is already a member, the existing role stays.
   Rows for a workspace that is gone or no longer shared are dropped.

Under the `invited` sign-in policy a pending row is also what admits a
newcomer: a cloud account with no account here is provisioned only when an
invitation to a shared workspace waits for its subject, and the claim then
makes it a member ([cloud_accounts.md](cloud_accounts.md) "Which local
account").

Pending rows are keyed by subject, not by username, so a later rename on
either side does not redirect an invitation. `GET /workspaces/{id}` lists
pending rows after the members, tagged `pending: true` with their `subject`.
The Manage page shows them with a *pending* tag and a remove button that
withdraws the invitation. Deleting a workspace removes its pending rows, and
so does converting it to personal. Counts of `members` include explicit
members only. Invitations require cloud sign-in on this server, and the
lookup is rate limited per inviting account. Code: `backend/gamma/workspaces.py`
(`invite_cloud`, `claim_pending_memberships`); tests:
`backend/tests/test_pending_memberships.py`.

The lookup runs on the inviter's own Gamma Cloud grant: an access token from
the refresh token stored with their linked identity
(`cloud_auth.access_token_for`, [cloud_accounts.md](cloud_accounts.md)). An
inviter whose account here is not linked to Gamma Cloud is refused with
"Link your own Gamma Cloud account" (503), and so is one whose grant the
account server does not honour.

Administrators also choose shared workspace access:

- **Private:** only explicit members can open it.
- **Public:** every signed-in, non-guest account can open it with the
  configured viewer or editor role. Explicit membership takes precedence:
  an invited viewer stays a viewer even if public access permits editing.
  Public access creates no membership to leave and consumes no workspace
  creation slot.

Public does not mean anonymous. Page share links provide access for people
without accounts — including editing, when the sharer sets "Anyone with the
link" to "Can edit" ([api.md](api.md) "Link visitors"). Each guest login is
a throwaway account with one personal workspace, deleted with it
`guest_ttl_hours` (default 24) after the login ([guests.md](guests.md)). A
guest cannot create workspaces, join shared ones or use public access, and
its workspace takes no backup import and keeps no snapshots
(`workspaces.is_guest_workspace`).

Administrators may manage a workspace without joining it. This does not grant
access to its private pages: an administrator must join a private shared
workspace to read it. Personal workspaces cannot be joined.

## Defaults, conversion and storage

Each account chooses one personal workspace as its **default**. Requests that
name no workspace use this library — the browser extension's saves unless a
workspace is picked in it ([extension.md](extension.md)), an older client, a
link without `ws`. Deleting the
default selects the oldest remaining personal workspace. The last personal
workspace cannot be deleted independently of the account.

Administrators can convert between kinds:

- **Personal → shared:** keep the owner; move the account's default if
  necessary. Refuse conversion of its last personal workspace.
- **Shared → personal:** require exactly one member, make that person the
  owner, reset access to private and the public role to viewer, and remove
  the workspace quota.

Workspace updates are atomic. A request combining a rename, conversion,
access change or default change either applies all fields or changes
nothing. Authorization happens before mutation; validation uses the resulting
kind. The model and CLI helpers share this transaction in
`backend/gamma/workspaces.py`.

An account's upload quota covers all its personal workspaces together. Shared
workspace uploads count against nobody's personal allowance; administrators
can give each shared workspace its own quota. `0` or `null` means unlimited.
`GET /api/quota` reports the selected workspace's limits. See
[storage limits](user_db.md) for per-file limits and quota accounting.

## Data ownership

```text
GAMMA_DATA_DIR/
  users.db                 accounts, sessions, workspaces, memberships,
                           page shares and account preferences
  workspaces/<id>/
    pages.db               blocks, the op and change logs, AI chats, notes index,
                           each account's tabs, recents and reading positions
    data.db                derived only: PDF text index, PDF manifests, covers
    uploads/               PDFs, images and other attachments
```

Workspace IDs are random and stable. Renaming an account or workspace changes
database rows without moving files. Schema upgrades run through the
[versioned migration system](migrations.md).

Workspace members share chats and cover snapshots as well as pages. Like
pages, only editors and owners change them (a viewer's chat and covers stay
in its browser tab). AI keys, provider choice and the preference profile
(appearance, reading, library and chat settings, [settings.md](settings.md))
belong to the account. Open tabs, recents, reading positions and saved
layouts belong to an **account and workspace**; the server keeps the
first three in the workspace's pages.db (`workspace_prefs`), and an
account's go when it leaves the workspace or is deleted.
Their browser caches use `user@workspace`; another account opening the same
shared library gets its own reading state. Unscoped legacy session caches are
not restored because their owner is unknown.

## Request contract

Keep identity and data location separate in endpoint code:

| Helper in `backend/gamma/auth.py` | Purpose |
|---|---|
| `require_user_id(request)` | The session account's id (`users.id`), what storage takes: account-only data such as AI settings (an integration token gets 403) |
| `require_user(request)` | The same check, answering the session's username: what an endpoint shows or compares with a name it was given |
| `require_ws(request, write=False)` | Workspace ID with effective viewer access |
| `require_ws(request, write=True)` | Workspace ID with editor or owner access (and, through an integration token, a write-scope one) |
| `can_write(request)` | The same write rule as a yes/no, for an endpoint that offers less instead of refusing (the AI chat arms no changing tools) |
| `resolve_ws(request)` | Read through a share token, otherwise normal workspace access |
| `require_ws_writer(request)` | Write through an edit share, otherwise workspace editor access |
| `share_scope(request)` | The `ShareScope` (one page, or the pages filed in one folder) a share-enabled endpoint must enforce |

Without a share token, selection is `?ws=` first, then `X-Gamma-Workspace`,
then the account's default. An inaccessible explicit workspace is refused;
the server does not fall back to another library. A share token chooses its
own workspace and confines access to one page, or to the pages filed in one
folder ([api.md](api.md) "Shares"). Workspace roles and the share's invites
determine whether that person can view or edit them. The Share popover
shows a shared workspace's members as one row under *Who has access* (the
member count; for a public workspace, that anyone signed in can read too),
since they open every page with their workspace role whatever the share
says.

Pass the workspace ID to data helpers such as `connect_pages_db` and
`commit_ops`. The actor in the operation log is `auth.actor_of(request)`:
the account's id (`request.state.user_id`), or a link visitor's label. The
membership helpers in `gamma/workspaces.py` take account ids too; the
endpoints take and answer usernames and translate (`db.account_id`), and
lists of people (`workspaces.members`, `pending_invites`) carry usernames.
Account-wide preference keys do not require access to the selected workspace;
workspace-specific preferences do.

The browser fetch wrapper adds the workspace header. Browser-issued image and
download requests need `assetUrl`, which adds `ws` or the share token to the
URL. Store bare `/api/uploads/<hash>.<ext>` URLs in block content. Page sockets
carry `ws` or `share` in their URL because browser WebSocket handshakes cannot
set these custom headers.

## API entry points

All paths below begin with `/api`. Full payloads and authorization rules are
in the [API reference](api.md).

| Method and path | Result or action |
|---|---|
| `GET /session` | Account, default workspace and accessible workspace list |
| `GET /workspaces/mine` | Accessible workspaces with upload sizes, plus account storage totals |
| `POST /workspaces` | Create a personal workspace; administrators may create shared ones or choose another owner |
| `GET /workspaces/{id}` | Details, effective role, explicit members and quota |
| `PUT /workspaces/{id}` | Atomic settings update; omitted fields stay unchanged |
| `DELETE /workspaces/{id}` | Delete the workspace, its content and its saved workspace backups, after one final copy went to `backups/deleted/` |
| `PUT /workspaces/{id}/members/{user}` | Invite or change a membership role |
| `DELETE /workspaces/{id}/members/{user}` | Remove a member, or leave your own explicit membership |
| `POST /workspaces/{id}/invites` | Invite by Gamma Cloud username (`{username, role}`): a membership now, or a pending one |
| `GET /workspaces/{id}/invites` | Pending invitations waiting for a first cloud sign-in |
| `DELETE /workspaces/{id}/invites/{subject}` | Withdraw a pending invitation |
| `GET /workspaces/find-page/{id}` | Locate a page or block among accessible workspaces |
| `GET /accounts` | `{accounts: [{username, is_admin}]}` for invite and owner pickers; non-guest accounts only |
| `GET /admin/workspaces` | Administrator's inventory, including orphaned directories |

`GET /session` and `/workspaces/mine` use `members` as a count. Workspace
details use `members` as an array, with pending invitations last
(`pending: true`). Details report `personal_of` as the owner's username; the
session list instead has a boolean `personal`.

## Browser startup and switching

`app/App.jsx` waits for the session and selects the workspace before loading
library data:

1. Use an explicit `?ws=` if accessible. Otherwise show an unavailable
   workspace screen and keep the requested URL intact.
2. For a page or block link without `ws`, try `find-page`.
3. Try `gamma-last-ws:<user>` from this browser.
4. Use the account's default, or its first accessible workspace.

`applyWorkspace` sets the fetch header, account/workspace session scope and
viewer role, then releases the `wsReady` startup gate. It never invents an
owner role for an unknown workspace. Viewer layout restoration also waits
for this scope.

A page or block link that finds nothing (the page was deleted, or lives in a
workspace this account can't reach) lands on the library with a notice under
the topbar, "That page isn't here", offering Search the library (quick open)
and Dismiss. The dead id is dropped from the address bar. A link clicked
inside the app that answers 404 shows the same notice and leaves the open page
as it was. Other failures (a 500, the network) stay in the status pill.

Switching navigates to `/?ws=<id>` and reloads the app. Tabs, the current page
and the live editing session belong to the library being left. Within a
library, each page retains its queued saves when navigation starts before a
save finishes. See [collaboration](collab.md) for delivery and retry behavior.

## Export and backups

Workspace exports, saved workspace snapshots and Gamma page exports use
`gamma-backup-1` ZIP files (`backend/gamma/ws_backup.py`). They contain a
manifest, database snapshots and uploads unless databases-only was selected.

- **Export / Import:** Settings → Workspaces. Any member exports; editors
  may merge an import; owners may replace the workspace. Export all bundles
  the account's personal workspaces. API: `/export`, `/export-all`,
  `/import-data` (`routers/ws_backups.py`); the web app runs each as a
  background job — `workspace-export`, `restore` — that shows in Background
  tasks ([tasks.md](tasks.md)).
- **Saved workspace snapshots:** Settings → Backups. Owners create or delete
  them, members list and download them, editors merge them, and owners restore
  them in place. Each ZIP is independent. Up to 20 manual ones are kept per
  workspace (`MAX_PER_WORKSPACE`); they do not count against upload quotas.
  No snapshot is taken while the server's disk has less than 1 GB free
  (`MIN_FREE_BYTES`). Guests cannot keep snapshots. The web app takes and
  restores them as `snapshot` and `restore` jobs.
- **Server snapshots:** Settings → Server. These cover the whole data
  directory and are restored with the server stopped. They are separate from
  workspace snapshots; see [migrations and server backups](migrations.md).
  The web app takes one as a `server-backup` job.

A job reports its progress as it goes: bytes of a zip written, files
unpacked or copied, then the phase. A restore can be stopped until it swaps
or merges the databases, and not after (`restore_zip`'s `progress`,
[tasks.md](tasks.md)).

Exports transfer library content. Passwords, sessions and private AI
credentials stay with the account, and so do each account's open tabs,
recents and reading positions: a zip's pages.db has `workspace_prefs`
empty, none of its bytes left (`ws_backup.PRIVATE_TABLES`), and a restore
keeps the live rows.

A snapshot copies the databases first (the SQLite backup API) and lists the
uploads only after that, so every file the copied pages name is on disk when
the list is taken. A file an orphan sweep removes in between is left out and
named in the manifest's `missing_uploads`; it never fails the snapshot. A
workspace takes one snapshot at a time (a lock per workspace). Each is
written under a unique temporary name (`.<name>.<random>.part`, its
database copies beside it) and renamed when complete; a second one in the
same second is named `<time>-<label>.2`. What a killed process leaves of an
unfinished one (the work files of workspace snapshots, final copies and
server backups older than an hour) is removed at startup, hourly after that
and before the workspace's next snapshot (`ws_backup.sweep_stale_temp`). Every database copy is
quick-checked (`gamma/integrity.py`) and the result goes into the
manifest's `integrity`. The listing shows a damaged copy and missing files.

**Restoring** (`restore_zip`) checks the backup before it touches anything:
the zip's shape and `PRAGMA quick_check` of its databases. A damaged backup
is refused whole. Then the unpacked copies are normalized to the current
shapes (`_normalize_copies`, [user_db.md](user_db.md) "pages.db"): a
backup from before schema version 28 has its chats moved from its data.db
into its pages.db, and every backup has its notes index built again from
its rows (it may have been built under other normalization rules).

- **Replace** keeps what the workspace holds
  now as an automatic `pre-restore` snapshot with its uploads, and swaps the
  databases in: pages.db is copied into the live file in one write
  transaction — its chats with it, the notes index following the copied
  rows through its triggers — and data.db with the backup API. The pre-restore snapshot shows as "Before restore"; the
  newest three stay (`PRE_RESTORE_KEEP`) and do not count against the cap.
  The restore is refused when that snapshot cannot be taken.
- **Files.** The backup's files the workspace lacks are copied in. Files
  only the old pages used are left to the orphan cleanup, and the
  pre-restore snapshot still has them.
- **Merge** adds the pages the workspace lacks, whole. A block of such a
  page whose id the workspace uses elsewhere (it moved to another page
  since) comes in under a fresh id with its children, so nothing is grafted
  into a page nobody restored. In the same transaction it adds every
  conversation the workspace lacks (a bucket's active one, an archived one
  by its id; `chats_added` counts them). The backup's data.db is not read.
  The backup's folders and labels join the workspace's trees first
  (`_merge_trees`), and an added page is filed under the workspace's ids
  for them ([import_export.md](import_export.md) "Gamma-to-Gamma export").
- **Pages in Recently deleted** count as lacking. A merge (and the reviewed
  Gamma import, which plans such a page as "create") removes the trashed
  copy's rows and brings the backup's version back live under the same ids.
  Its chats, op log and reading state, all keyed by the page id, stay with
  it; the answer counts it in `pages_added` and `from_trash`. Blocks the
  trashed copy gained after the backup are gone with it; the trash's own
  Restore keeps them ([home_library.md](home_library.md) "Recently
  deleted"). A backup zip holds the trash as it was, and a replace brings
  back that trash.

Either way a restore writes pages behind the op log, so it keeps the log and
the change feed honest ([collab.md](collab.md)):

- every page it wrote gets a `reload` entry above the highest seq either
  side had (a tab's seq never goes back), which touches it `live` in the
  change log; a replace copies pages.db in and writes these in the same
  write transaction that read the live seqs, so a batch committed meanwhile
  waits and lands above them;
- the change log is never replaced, so the cursors that copies hold into
  it stay good. A replace copies every table but `page_changes`, then
  turns `deleted` every page that is no page of the library afterwards
  (removed, in the restored Recently deleted, or deleted in the backup's
  own log) unless it already is. Pages a merge brought back turn `live`;
- every open room of the workspace (a replace) or of the added pages (a
  merge) is told to reload, and the commit listeners hear of it.

`restore_zip(mode="merge", selected=)` filters pages, chats and uploads by the
`page:<id>` / `chat:<id>` ids `_review_import` hands it.
Replace mode never takes a selection.

**Deleting a workspace** (the Manage page, or an account's deletion taking
its workspaces with it) first writes one final copy of it, uploads
included, to `backups/deleted/<id>-<name>-<time>.zip` (`ws_backup.keep_final_copy`).
That folder is outside the ones that go with the workspace. It is the same
zip format, so an administrator restores it into any workspace through
Import (`/import-data`). Copies older than 90 days are removed when the next
one is written (`DELETED_KEEP_DAYS`); an admin may delete them by hand
before that. When a copy cannot be written, nothing is deleted (507). The
copy is written before the delete's checks run again under the users.db
write lock; when they refuse after all (the account's other personal
workspace went meanwhile, so this one is its last), the copy goes again —
no copy is kept of a delete that did not happen. A guest's workspace keeps
no copy. The directory is then renamed to `.deleting-<id>-…` (atomic, so a
background pass about to open one of its databases finds none instead of
creating a fresh file in a half-removed directory) and removed, each step
closing the server's own cached connections to the workspace first and
retried for a second while a file is held open (Windows). What still stays
is removed at the next startup or hourly (`workspaces.remove_leftovers`); a
dot-named directory is never taken for a workspace. Its open page sockets
close (`collab.revalidate`).

**Scheduled tasks** (`gamma/backup_schedule.py`, API in [api.md](api.md)):
a task belongs to an account, names the owned workspaces it snapshots
(a fixed selection or "all owned"), a five-field UTC cron and a retention
rule (keep N snapshots or N days). Tasks are files, `backups/tasks/<id>.json`.
The app lifespan runs `run_due` every 30 s; each task is processed under an
OS file lock (`<id>.lock`, removed with the task; `msvcrt`/`fcntl`), so several workers never run
one task twice. A task whose `next_run` passed while the server was down
runs once on the next round, then reschedules from the cron.
A failed run is retried after an hour. "Run now" sets `requested`, keeps
the scheduled `next_run` and wakes the loop for a round at once (`_wake`).
Snapshots are pruned per task and workspace after every run.

Every run is a full copy, so tasks have limits (constants in
`backup_schedule.py` and `ws_backup.py`):

- a schedule fires at most once an hour (its cron has a single minute
  value);
- a task keeps at most 90 days or 90 snapshots (`MAX_RETENTION`);
- an account has at most five tasks (`MAX_TASKS`);
- a workspace keeps at most 100 scheduled snapshots of all its tasks
  together (`MAX_SCHEDULED_PER_WORKSPACE`);
- no run starts while the disk has less than 1 GB free (`MIN_FREE_BYTES`).

A task saved before a limit existed fails its next run with the reason in
`last_error` (the `backup-failed` notice) until it is edited; pausing it is
always allowed.

## Clones (mirrors)

A personal workspace can be a **mirror** of a workspace on another Gamma
server — a *clone* of its *origin* in the UI's git vocabulary: it holds a
copy, edits made in it are pushed to the origin when it is reachable, and
edits made there are pulled. The desktop app makes one from the switcher
(the *clone* chip on a remote workspace's row); any Gamma makes one from
Settings → Workspaces → Clones with the server's address and a write-scope
integration token made there. `GET /workspaces/mine` marks such a workspace
with `mirror_of`; a workspace that publishes pages to Gamma Cloud (a
filtered mirror of the share host) is not a clone and is marked
`publishing` instead. The whole design — the change feed, the three-way merge,
edit-beats-delete, the conflict list — is [mirror.md](mirror.md).

## Folders on disk

The desktop app keeps a folder of a workspace as a directory on the
computer it runs on: each paper's PDF beside a Markdown note of its
highlights and notes, one way, kept up to date. A folder's *Keep on this
computer…* or the bar's *on disk* chip starts it. The layout, the rounds,
the links and the client that does the same without the app are
[folder_sync.md](folder_sync.md).

## Current limits

- Some viewer screens still expose write controls; the server refuses those
  operations. Effective permissions remain a server decision.
- Shared workspace chats are visible to other workspace members. There is
  one conversation per page or folder: two members asking at once end up in
  one merged conversation ([chat_history.md](chat_history.md)).
- The account directory is visible to every signed-in non-guest account.
- Cross-workspace page transfer uses export and merge, with no direct move.
- The desktop shell refreshes its workspace list on navigation or menu open.

The historical reasoning is in [research/workspaces.md](../research/workspaces.md).
Executable coverage is in `backend/tests/test_workspaces.py`,
`backend/tests/test_pending_memberships.py`,
`frontend/tests/sessionState.test.mjs` and `frontend/tests/e2e/`.
