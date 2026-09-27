# Workspaces

An account identifies a person. A workspace holds a library: pages, uploaded
files, chats and search indexes. Each browser tab opens one workspace.

Use the account menu to switch libraries. **Settings → Workspaces** lists the
libraries you can open and provides their export, import and management
actions. **Settings → Backups** manages saved workspace snapshots.
Administrators manage shared workspaces under **Settings → Server** and
each account's personal workspaces on its row under **Settings → Users**.

## Personal and shared libraries

Every account starts with a personal workspace and can create more. Each
personal workspace has exactly one member, its owner. Use page share links
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
   `workspaces.claim_pending_memberships(username, subject)` from
   `cloud_auth.resolve_account`, after the local account is known. The local
   account can be newly provisioned, claimed by username, linked from
   Settings → Account & sync, or already linked. Every pending row for that subject
   becomes a `workspace_members` row with its role, and the pending rows are
   deleted. If the person is already a member, the existing role stays.
   Rows for a workspace that is gone or no longer shared are dropped.

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
name no workspace, including extension clips, use this library. Deleting the
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
    pages.db               blocks and the per-page operation log
    data.db                chats, cover snapshots and search indexes
    uploads/               PDFs, images and other attachments
```

Workspace IDs are random and stable. Renaming an account or workspace changes
database rows without moving files. Schema upgrades run through the
[versioned migration system](migrations.md).

Workspace members share chats and cover snapshots as well as pages. AI keys,
provider choice and the preference profile (appearance, reading, library and
chat settings, [settings.md](settings.md)) belong to the account. Open tabs, recents,
reading positions and saved layouts belong to an **account and workspace**.
Their browser caches use `user@workspace`; another account opening the same
shared library gets its own reading state. Unscoped legacy session caches are
not restored because their owner is unknown.

## Request contract

Keep identity and data location separate in endpoint code:

| Helper in `backend/gamma/auth.py` | Purpose |
|---|---|
| `require_user(request)` | Session username; account-only data such as AI settings |
| `require_ws(request, write=False)` | Workspace ID with effective viewer access |
| `require_ws(request, write=True)` | Workspace ID with editor or owner access |
| `resolve_ws(request)` | Read through a share token, otherwise normal workspace access |
| `require_ws_writer(request)` | Write through an edit share, otherwise workspace editor access |
| `share_scope(request)` | The `ShareScope` (one page, or the pages filed in one folder) a share-enabled endpoint must enforce |

Without a share token, selection is `?ws=` first, then `X-Gamma-Workspace`,
then the account's default. An inaccessible explicit workspace is refused;
the server does not fall back to another library. A share token chooses its
own workspace and confines access to one page, or to the pages filed in one
folder ([api.md](api.md) "Shares"). Workspace roles and the share's invites
determine whether that person can view or edit them.

Pass the workspace ID to data helpers such as `connect_pages_db` and
`commit_ops`. Use `request.state.user` as the actor in the operation log.
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
| `DELETE /workspaces/{id}` | Delete the workspace, its content and its saved workspace backups |
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
  `/import-data`.
- **Saved workspace snapshots:** Settings → Backups. Owners create or delete
  them, members list and download them, editors merge them, and owners restore
  them in place. Each ZIP is independent. Up to 20 are kept per workspace;
  they do not count against upload quotas. Guests cannot keep snapshots.
- **Server snapshots:** Settings → Server. These cover the whole data
  directory and are restored with the server stopped. They are separate from
  workspace snapshots; see [migrations and server backups](migrations.md).

Exports transfer library content. Passwords, sessions and private AI
credentials stay with the account.

`restore_zip(mode="merge", selected=)` filters pages, chats and uploads by the
`page:<id>` / `chat:<id>` ids `_review_import` hands it.
Replace mode never takes a selection.

**Scheduled tasks** (`gamma/backup_schedule.py`, API in [api.md](api.md)):
a task belongs to an account, names the owned workspaces it snapshots
(a fixed selection or "all owned"), a five-field UTC cron and a retention
rule (keep N snapshots or N days). Tasks are files, `backups/tasks/<id>.json`.
The app lifespan runs `run_due` every 30 s; each task is processed under an
OS file lock (`<id>.lock`, `msvcrt`/`fcntl`), so several workers never run
one task twice. A task whose `next_run` passed while the server was down
runs once on the next round, then reschedules from the cron.
A failed run is retried after an hour. "Run now" sets `requested`, keeps
the scheduled `next_run` and wakes the loop for a round at once (`_wake`).
Snapshots are pruned per task and workspace after every run.

## Clones (mirrors)

A personal workspace can be a **mirror** of a workspace on another Gamma
server — a *clone* of its *origin* in the UI's git vocabulary: it holds a
copy, edits made in it are pushed to the origin when it is reachable, and
edits made there are pulled. The desktop app makes one from the switcher
(the *clone* chip on a remote workspace's row); any Gamma makes one from
Settings → Account & sync → Clones with the server's address and a write-scope
integration token made there. `GET /workspaces/mine` marks such a workspace
with `mirror_of`; a workspace that publishes pages to Gamma Cloud (a
filtered mirror of the share host) is not a clone and is marked
`publishing` instead. The whole design — the change feed, the three-way merge,
edit-beats-delete, the conflict list — is [mirror.md](mirror.md).

## Current limits

- Some viewer screens still expose write controls; the server refuses those
  operations. Effective permissions remain a server decision.
- Shared workspace chats are visible to other workspace members.
- The account directory is visible to every signed-in non-guest account.
- Cross-workspace page transfer uses export and merge, with no direct move.
- The browser extension clips into the default workspace.
- The desktop shell refreshes its workspace list on navigation or menu open.

The historical reasoning is in [research/workspaces.md](../research/workspaces.md).
Executable coverage is in `backend/tests/test_workspaces.py`,
`backend/tests/test_pending_memberships.py`,
`frontend/tests/sessionState.test.mjs` and `frontend/tests/e2e/`.
