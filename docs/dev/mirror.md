# Offline copies (mirrors)

A **mirror** is a workspace on one Gamma server that keeps a copy of a
workspace on another Gamma server and keeps the two in step: edits made in
the copy go to the original, edits made on the original arrive in the copy.
The everyday case is the desktop app: a local server that holds a copy of a
workspace on the lab's NAS, so the library opens on the train and the notes
written there land on the NAS when it is reachable again.

Code: `gamma/sync_engine.py` (the engine and the mirror registry),
`gamma/sync_tree.py` (snapshots and the diff between them),
`gamma/routers/sync.py` (the change feed and `whoami`, what a mirror reads
on the remote), `gamma/routers/mirrors.py` (the mirror API on the server
that holds the copy), `gamma/publish.py` + `gamma/routers/publish.py`
(publishing a page to the share host, below), `frontend/src/settings/SettingsMirrors.jsx` (Settings →
Account & sync → Clones), `frontend/src/collaboration/MirrorPopover.jsx`
(the header's sync pill, its settings and review views),
`frontend/src/collaboration/MergeResolver.jsx` (the merge chip on a block
row), `desktop/main.js` `keepOffline` (the shell's one-click flow). Why the
model is ops plus a three-way merge and not a CRDT:
[research/collaboration.md](../research/collaboration.md).

## The model in one paragraph

The original (the **remote**) is the authority. The copy runs on an
ordinary Gamma server (the **local**) as an ordinary personal workspace of
the person who made it, with one extra row in `users.db` (`mirrors`) and,
per page, the tree it held after the last reconciliation (`sync_pages` in
the copy's `pages.db`). A **round** asks both servers' change feeds which
pages moved since the last round and reconciles each one three ways from
that saved tree: the remote's changes are applied locally through the
normal op path (so the local server's own text merge keeps the local
keystrokes and the page's open editors see them arrive), what still differs
is pushed to the remote as an op batch under the mirror's write token, and
the remote's tree is fetched back and becomes the new base. Nothing about
the frontend changes: editing a mirror is editing a workspace.

## What travels

- **Pages and blocks**: the whole tree, root properties included (title,
  folder, labels, metadata), by block id and fractional position. Ids are
  kept, so a block is the same block on both sides forever.
- **Files**: every upload a page references (`/api/uploads/<hash>.<ext>` in
  content or properties, a page's `doc_id`), by content hash — fetched when
  missing on the copy, uploaded when missing on the original. A re-run
  never duplicates. Every fetched tree's files are checked, not only the
  changed blocks', and each round ends with a sweep for files the copy's
  pages name but lack (`missing_uploads`), so a round cut short after a
  page landed but before its PDF did heals by itself.
- **Deletions**: pages through the tombstones (`deleted_pages`), blocks
  through the diff.

Not synced: preferences (reading positions, open tabs, recents — they are
per account and per server), chats, cover snapshots, search indexes (the
copy rebuilds its own).

## The page filter

A mirror may name the only pages that travel: `mirrors.page_filter`, a JSON
list of page ids (NULL, what every mirror made by hand has, means every
page; migration step 19). Publishing is the one thing that makes such a
mirror today. With a filter a round:

- takes from both change feeds, and from its retry list, only the listed
  pages, and fetches only their missing files (`missing_uploads(ws,
  pages)`); a page outside it never travels in either direction, whatever
  the feeds say, and neither does its deletion;
- treats a listed page that has no saved base as new whatever the feeds say
  (`_filtered`): a tombstone on the remote from an earlier publication says
  nothing about this one. So a page added to the filter (`filter_add`) goes
  over whole at the next round, created there under its id with its files;
- drops from the filter a listed page that was deleted on the remote and not
  here, instead of deleting it here: the copy there was removed (by
  unpublishing, or by its owner on the remote), the page here stays;
- drops a listed page that is gone here and has no base (`_prune_filter`,
  at the start of a round): a page deleted here is deleted there by the
  normal rule first, then leaves the filter;
- asks nothing of the remote when the filter is empty.

Local writes to pages outside the filter neither mark the copy dirty nor
ask for a sync-on-change round (`ops.commit_listeners` get the page id; the
engine caches each copy's filter in `_filters`). Several filtered mirrors of
one remote workspace may coexist (each local workspace that publishes to the
same account has its own); a second unfiltered mirror of the same remote is
still refused. Changes to the filter are made under `round_lock(ws)`, the
lock a round holds, so no round sees half of one.

## The change feed (remote side)

`GET /api/sync/changes?since=&limit=` lists the pages whose root was
stamped after a cursor, each with its latest op `seq`, and the pages
deleted after it, as one time-ordered stream ([collab.md](collab.md) "The
change feed" has the cursor rules). The feed is a hint: the engine compares
each listed page's `seq` with the one it holds and fetches the tree only
when they differ. `GET /api/sync/whoami` tells the engine who its token is,
which workspace and role it has there, and whether it may write. A round
reuses an earlier round's answer for the same link for 15 minutes
(`WHOAMI_TTL_S`); a round that ends with an error forgets it, so a revoked
token or a lowered role shows on the next round.

The local server has the same feed, read in-process, so local edits are
found the same way; the engine's own writes are tagged client `sync` and
diff to nothing on the next round.

## One round, one page (`sync_engine._sync_page`)

| the page is… | what happens |
|---|---|
| new on the remote | created here under its id, the remote tree laid in, files fetched |
| new here (two-way) | created there under its id (`POST /pages` with `id` + `properties`), the tree pushed, files uploaded |
| changed there only | the diff base → remote applied here |
| changed here only | the diff base → here pushed there, with each set's `base` text so the remote merges against anything that landed meanwhile |
| changed on both | the remote diff applied here first (merges recorded), then what still differs pushed, then the remote tree fetched back |
| deleted there, untouched here | deleted here (a local tombstone, the sync state dropped) |
| deleted there, edited here | re-created there with the local tree (`page_restored`) |
| deleted here, untouched there | deleted there |
| deleted here, edited there | re-created here from the remote (`page_restored_from_remote`) |

Inside a page the same rule holds at block level, **an edit beats a
delete** (a move counts as an edit): a subtree the remote deleted stays when something in it was
edited here (the push re-inserts it there), and a subtree deleted here
comes back whole when the remote edited inside it. A block the remote
moved *out* of a subtree deleted here is no part of that deletion any
more: it comes back whole (with its own children) where the remote put
it, while the subtree it left stays deleted unless something still inside
it was touched there — without this the push would delete the moved block
on the remote too (`_reconcile_remote_ops`, `escaped`). Same-block text edits
merge by span through `gamma/textmerge.py` on whichever server applies the
op; two edits to the same characters resolve by the remote's order.

Three more rules keep the two trees identical in the odd cases
(`tests/test_mirror_edges.py` pins each):

- **Positions.** The server re-keys a block that lands on a taken key, so
  when the remote's answer moves two siblings past each other, applying
  those moves here in order would re-key one of them and leave this side's
  keys off the remote's — and every later round would push the difference
  again. `_parked` (in `_apply_local`) moves the block that holds a
  target key to a fresh key at the end first, so every move lands where it
  says and one round settles it.
- **Cross-page moves.** A block id lives in one page. When the remote's ops
  insert a block that lives in another page here (moved there on the
  remote, or edited here after the remote moved it), `_relocated` deletes
  it from that page first and inserts it with the text it has *here*, which
  the push then sends on; the page it left is reconciled at its own turn
  (both sides deleted it there). The other way round — this copy still
  holds the block in a page the remote moved it out of — the remote refuses
  the push (`403 … outside this page`) and the page is deferred
  (`PageDeferred`: kept on the retry list, not an error) while the
  receiving page's round moves it over; the next round finds nothing left
  to push.
- **The same edit made on both sides** (or a retried batch) is one edit: an
  op whose content already is the block's text merges nothing (`ops.py`,
  `textmerge.merge`).

Every decision the engine takes on its own is a row of `sync_conflicts`
(`merged`, `kept_local_edit`, `restored_remote_edit`, `page_restored`,
`page_restored_from_remote`) with the texts involved — for a `merged` block
also `base`, the text before either side edited it, so the resolver can
show what each side changed. Sync never blocks on one: the person looks at
the list and, for a merge, can put back "mine" or "theirs" — an ordinary
edit that the next round pushes, written from the text the conflict
recorded as its `base`, so words typed into the block since the merge are
kept over the chosen version rather than lost. The text is written into
the page the block is in *now* (it may have moved since) and only then is
the conflict marked resolved; a write the block refuses answers 409 and
leaves the conflict open.

Pull-only mirrors (a read token, or a viewer's, or the *Receive only*
direction) apply the remote's changes and never push; local edits stay
local and survive later remote changes to other spans of the same block,
since the saved base is always the remote's tree. Such a round does not
walk the local feed and leaves the local cursor where it is, so the first
round that may push — the direction switched back to two-way, or a
write token or role restored on the remote after a spell as a viewer
(such a round drops to pull only for its own duration and reports it as
its error) — finds every edit made here meanwhile; `pending_local` stays
true until then. (Switching back to two-way also resets the cursor, for
copies from before this rule.) The direction of a detached copy cannot be
changed (400): reattaching restores the one it had.

## Rounds and cadence

The engine's loop (`start_loop`; `GAMMA_SYNC_INTERVAL=0` turns it off, the
tests) ticks every second and gives a round to each mirror that is due: its
own `poll_s` come round (per copy, `mirrors.poll_s`: 5 = *Live*, 30, 300,
0 = only by hand), or a local edit `DEBOUNCE_S` (1 s) ago when the copy's
`on_change` is set — `ops.commit_listeners` tells the engine about every
committed write (`request_sync`, which also wakes the loop, so the round
starts the moment the quiet second is over rather than at the next tick;
the engine's own writes, client `sync`, do not count, and a typing burst
is one round). The same listener marks the copy **dirty** (`_dirty`, in
memory): `has_local_changes(ws)` is true from a local write until a round
that started after it finishes without error, and the API reports it as
`pending_local` (two-way copies only) — the pill's "local edits not
synced yet" state (with *Sync after an edit* on, the pill spins from the
edit until a poll confirms the round is done, since a one-page round is
shorter than the poll interval). The first pass runs
`FIRST_PASS_S` (5 s) after startup, so a copy whose first fill was cut short
by a restart continues at once; "Sync now" (`POST /api/mirrors/{ws}/sync`,
`?wait=1` for the answer) runs one on demand. Rounds of one mirror never
overlap: a second caller waits for the round lock and reads the mirror's
row only once it holds it, so a page unpublished or a detach done while
it waited is what it runs with. What the person does *while* a round
runs is kept too: the round only ever patches its keys of the status JSON
(`_patch_status`, one read-modify-write under a lock, the same path every
other writer of the status takes), it saves the feeds' cursors only when
nothing reset them meanwhile, a detach makes it stop at its next page
(the pages left over go on the retry list for the reattach), and a force
is noted as `status.force` and applied by the next round under the lock
(`_start_force`), never by the running one. A round that cannot reach the remote records the error on the
mirror and moves no cursor. A page that fails inside a round — whatever the
exception — is reported, kept on the mirror's `retry` list with the flags
it had, and worked again next round (the feeds' cursors have moved past
it). Nothing a round does can leave the `running` flag up: every exception
brings it down with `last_error`, and a process stopped in the middle of a
round (the desktop app quit) is caught at the next startup by
`reset_interrupted`, which clears the flag and notes `interrupted`; the
next round simply continues, a round is idempotent.

The status the Settings row and the header pill show is the mirror's
`status` JSON: `last_sync`, `last_error`, `pages_pulled`, `pages_pushed`,
`pages_deleted`, `files_pulled`, `files_pushed`, `blocks_added` /
`blocks_removed` / `blocks_changed` (the round's git-style totals), `mode`, `remote_role`,
`remote_user`, `retry`, `interrupted`, and while a round runs `running`
with `progress` (`done`, `total`, `page` — the title being worked —,
`first` for the first fill, `at`, and `file` `{name, done, total, dir}`
while a file travels, updated a few times a second from the streaming
transport), saved before every page so "21 of 79 pages" and "↓ paper.pdf
3.2 / 14 MB" move. What a round did, page by page, is the copy's `sync_log`
(`pulled`, `pushed`, `created here` / `there`, `deleted here` / `there`,
`restored here` / `there`, `replaced here` / `there`; the newest 500 rows,
`GET /api/mirrors/{ws}/log`), each row with its git-style `stats`: `add`
blocks inserted, `del` blocks removed (a delete counts its subtree), `mod`
blocks set or moved — computed from the ops the round applied or pushed
(`_stats`), or the page's size when it came or went whole (`_whole`) — and
its `changes`, what each edit did block by block for the log's diff view
(`_changes`: `{k: add | del | mod | props | move, id, text, old?}`, the
new text and, for `mod`, the old one, capped at 40 entries of 240
characters; a page that came or went whole lists its blocks). Both live in
the row's `stats` JSON; `list_log` hands `changes` out as its own key.

## What the person sees

The UI speaks git: the mirror is a **clone**, the workspace it follows is
its **origin** (the **remote**), a round **pulls** then **pushes** but the
UI only ever says **Sync** (the direction is *Two-way* or *Receive only*),
a block both sides changed is a **conflict** resolved between **local** and
**remote**, a force is **force pull** / **force push**, pausing is
**detach** / **reattach**, and dropping the link is **remove origin**. (The
code and the API keep *mirror*, *remote*, *mine* / *theirs*.)

### The header's sync pill (`MirrorPopover.jsx`)

Shown while a clone is open, in the desktop app and in a browser alike.

- An icon whose state is drawn on it, like the background-tasks button: the
  refresh glyph spinning while a round runs; a count badge when conflicts
  wait; a dot, accent for local edits not synced yet, green when up to date,
  red on a problem; an unlink glyph when detached; a cloud while the first
  fill has not run. With *Sync after an edit* on, the icon spins instead of
  showing the accent dot, until the round is confirmed done.
- No words on it: the state's sentence and the last sync time are the
  tooltip. `data-state` (`busy`, `conflicts`, `error`, `pending`, `ok`,
  `detached`, `new`) is what the browser test reads.
- The pending state is known before the server says so: the page's collab
  session raises `gamma:local-edit` when it queues ops. The pill shows the
  dot at once and polls every 2 s until a poll after a short grace reports
  `pending_local` false.
- `mirrorState(info, {busy, pending})` is the one reading of the status
  (state, icon, tone, line, tooltip, badge or dot) that the pill, the
  popover and the Settings row share.
- Polls the mirror every 20 s, every 2 s while a round runs or an edit is
  pending (the log too while open), only while the pill is shown and the tab
  is visible; a tab coming back reads at once. A publication's pill off its
  pages does not poll, and edits there are not pending for it; it reloads on
  `gamma:mirror` (a publish or unpublish here). When a poll sees the open
  conflicts move (`conflicts_open` / `conflicts_newest`) it raises
  `gamma:mirror-changed` so the page's conflict chips refresh.

Click: a popover of icons and numbers.

- The head: the clone's name with *remote · host*, a **Sync** icon button
  and the gear.
- The state line: the last round's `+3 −1 ~2`, the progress bars while a
  round runs, a *Resolve* button when conflicts wait (it opens the conflict
  cards, each resolved in place or opened on its block).
- The **Log**: a direction arrow per row and its `+3 −1 ~2` block counts.
  Clicking a row opens its changes block by block as a diff (`ChangeList`:
  added blocks tinted green with `+`, removed ones struck red with `−`, a
  changed block as a word diff of old → new with `~`, moves and property
  changes named). The arrow at the row's end opens the page.
- The gear turns the popover into the clone's **sync settings**, built from
  the settings kit's rows: *Automatic sync* (Live / 30 s / 5 min / Manual, a
  `Segmented`), the *Sync after an edit* toggle, *Direction* (*Two-way* /
  *Receive only*), then *Force pull* / *Force push* (confirmed inline; a
  receive-only clone cannot force push), *Detach* / *Reattach*, and a danger
  *Remove origin*.

### The conflict card (`MergeResolver.jsx`, `ConflictCard`)

One surface for every list: the chip on a block row, the pill's conflicts
view, Settings.

- A kind line (a merge glyph for *Auto-merged*, an arrow for a restore, the
  long story as the hint), then the versions: **Local** (this clone) and
  **Remote** (origin) side by side, and for an auto-merge the **Merged**
  text under them.
- With the row's `base` each panel is a git-style word diff (`wordDiff`, an
  LCS over word and space tokens). Local shows what local changed against
  the base, its added words in the local colour and the words it removed
  struck through; remote likewise in the remote colour. The merged text
  shows what the merge did, each added word coloured by the side that wrote
  it (dotted when both did).
- Without a base (a *diverged* block, or a row from before it was kept) the
  two texts are shown against each other and the merged text by
  attribution.
- Every version carries a radio (clicking the panel picks it too). The one
  in the block now is tagged *in the block* and preselected, and one
  **Apply** confirms: on the preselected version it marks the conflict
  resolved as it is, on another it writes that text.
- The non-textual kinds (*Kept local*, *Restored remote*, the page
  restores) show the one text involved and an *OK*.
- `useConflicts(wsId)` loads a clone's open conflicts and posts a decision;
  the pill's review view and Settings share it.

**The chip.** A block the sync merged or had to decide on carries a small
chip at its row's right end; its popover is the card. App owns which chip is
open (`mergeOpen`) and the page's conflicts in tree order (`mergeOrder`):
the card's ‹ n / N › step through them, and a decision opens the next one
down the page, so a page of conflicts is worked through in one pass. App
reads the page's conflicts (`GET /api/mirrors/{ws}/conflicts?page=`) on
open and on `gamma:mirror` / `gamma:mirror-changed` (no timer of its own:
the pill's poll raises the latter); a decision
is an ordinary edit the next round pushes. The lists in the pill and in
Settings jump to the block (`gamma:jump`).

### Settings → Account & sync → Clones (`SettingsMirrors.jsx`)

- One row per clone. Its avatar is its state (the same reading as the pill:
  a spinning refresh while a round runs, a check when up to date, a warning
  on a problem, an unlink glyph when detached), then the name with its tags
  (*open*, *receive only*, *detached*, *problem*, *unpushed edits*, *N
  conflicts*), *clone of X · origin host* and one short status line
  (progress and the file in flight while a round runs; *up to date 14:37 ·
  2 pages pulled* after; *local edits not pushed yet* while `pending_local`).
  The list is read again every 2 s while any row's round runs
  (`useMirrors`), so the progress moves.
- Actions: Open, *Sync* (*Reattach* when detached), *Conflicts* (the same
  cards, each resolved there or opened on its block) and a "more"
  `ActionMenu`: *Force pull*, *Force push* (off on a receive-only clone),
  *Detach*, and a danger *Remove origin*. The forces and the removal are
  confirmed by the shared confirm box.
- No intro paragraph: the empty state's one sentence says what a clone is.
- *Clone a remote workspace* asks for the origin server's address, a write
  token made there, *Into* (a new workspace, or one of yours: an imported
  backup, a clone whose origin was removed, with *If a page differs*: take
  remote's or keep local), a name and the direction as two `IconChoices`
  tiles.

### Publishing (`SharePopover.jsx` `PublishSection`)

A publication is not a clone, and the UI keeps the two apart.
`GET /api/workspaces/mine` and `/api/session` give a workspace that
publishes `publishing: true` and leave its `mirror_of` empty, so it stays in
Settings' Personal list and the desktop switcher never calls it a clone.
`isPublication(info)` (a non-null `page_filter`) is how the frontend tells a
mirror's own answer apart.

- **The share popover** gets a *Gamma Cloud* section under the local share
  when the server has cloud sign-in on and is not itself a share host
  (`server-config`: `cloud.enabled`, and `guest` not false), never for the
  guest. App owns the data (`loadPublishState`, `publishPage`,
  `unpublishPage`, `syncPublication`) and reads
  `GET /api/pages/{id}/publish` when the popover opens, then every 5 s while
  a round runs or a local edit waits (`pending_local`), else every 20 s.
  - Not published, allowed: "Keep this page reachable while this computer
    is off." and a primary **Publish**; where the plan caps publishing
    (the answer's `limit` has a `max`) the hint counts instead, "3 of 5
    pages published". While it runs the button is disabled and shows the
    spinning refresh glyph; a refusal shows its `detail` under the row, and
    the cap's refusal (a 409 carrying `limit`) adds an *Open account* button
    to the issuer's portal, the Settings Account row's target.
  - Not published, refused: the `reason` as the row's hint. When the reason
    is the sign-in one, *Link Gamma Cloud account* opens Settings → Account & sync,
    where the existing link flow runs.
  - Published but without a share there (publishing failed after the page
    reached the share host): the row says so and offers *Publish again*,
    the same `POST`, which finishes the job.
  - Published: the cloud link as the row hint with *Copy link* — the
    answer's `public_url`, the page's pretty address when the share host
    has page hosts, with the token link in the row's hover title as the
    fallback that also works — a danger
    icon button that asks inline before it unpublishes, the state line
    (`mirrorState` of the answer's `mirror`, the pill's icon and words) with
    a *Sync now* icon button (`POST /api/mirrors/{ws}/sync?wait=1`; off,
    and the state line says so, while the publication is detached — the
    answer's `mirror` carries `mode` and `detached` for that, and the
    refusal's `reason` shows under the row), and
    the cloud share's access as the local share draws it: the three
    audience tiles (their hints in the share host's terms) and the View /
    Edit segmented as the section's action. A change is
    `POST /api/pages/{id}/publish {audience, role}`, shown at once and put
    back when the server refuses.
  - A viewer of the workspace sees the state and the link but no buttons.
- **The header's sync pill** shows for a publication only on a published
  page (a clone syncs the whole workspace, so its pill is on every page; a
  publication syncs the pages in its filter, so its pill is on those;
  Settings → Account & sync → Sync pill, *Synced pages* / *Every page*, can put it on every page instead). Its
  tooltip and name line say *Published to Gamma Cloud* with the count of
  pages and the host; its gear keeps *Automatic sync* and *Sync after an
  edit* and hides *Direction*, the forces, *Detach* and *Remove origin*,
  which would break it (a detached publication still offers *Reattach*).
  The first publication in a workspace sets `publishing` on the open
  workspace, so the pill appears without a reload.
- **Settings → Account & sync** (the sync sections in `SettingsSync.jsx`,
  `PublishingSection` in `SettingsMirrors.jsx`) lists publications under
  *Publishing*, above *Clones* (the pill's gear link opens this pane for both): the state avatar, the workspace's
  name with its tags, *N published pages · host*, the status line, a
  *Conflicts* button when any wait, and a "more" menu with *Sync now* and
  a danger *Stop publishing all* (confirmed, then
  `DELETE /api/pages/{id}/publish?ws=` for every page in the filter).
  Open conflicts there raise the `publish-conflicts` notice on that pane.

### The desktop switcher

On a remote server every workspace row carries a *clone* chip on hover.
Once a clone exists the chip reads *open clone* and opens it (one clone per
workspace: a second *clone* opens the existing one). On the local server the
clone's row reads *clone* and its *origin* chip opens the workspace it
follows. The shell keeps a map of clones in its registry and starts the
local servers that hold them when the app launches, so clones sync in the
background whichever server the window shows
([desktop/docs/architecture.md](../../desktop/docs/architecture.md)).

## Credentials

The mirror signs in to the remote with an **integration token** of the
`write` scope ([mcp.md](mcp.md) "Manual tokens"; `POST
/api/integrations/tokens {scope: "write"}`, made on the remote by a member
who may write there). On the HTTP API a bearer token is the account behind
it, confined to the token's workspace, never an admin and never a session
that manages tokens or accounts (`auth.py`, `require_ws`). The token is
stored Fernet-encrypted in the copy's `users.db` with the data directory's
key (`publisher_sessions.cipher`). Pushed batches land in the remote's op
log under that account with client `sync`.

## Making one

- **Desktop app**: open the remote server, open the switcher, the *clone*
  chip on the workspace's row. The shell mints the token on the remote with
  the page's session, starts (or makes) a local server, signs into it with
  the seeded admin credentials, creates the mirror there and moves the
  window to it ([desktop/docs/architecture.md](../../desktop/docs/architecture.md)).
- **Any Gamma**: Settings → Account & sync → Clones → *Clone a remote
  workspace*: the server address and a write token made there.

**Detach and reattach.** *Detach* (`POST /api/mirrors/{ws}/detach`) sets
the mirror's `mode` to `off`: no round runs and the workspace lists as an
ordinary one (`mirror_of` is empty), but the row keeps the token, the
cursors and every page's base. *Reattach* (`POST /api/mirrors/{ws}/relink`,
optionally a new token or address) checks the remote and switches the mode
back; the next round is a normal three-way merge of what both sides did
meanwhile. A re-link to a different remote workspace drops the bases and
adopts its pages (below). *Remove origin* (`DELETE /api/mirrors/{ws}`)
drops the link and the sync state; the workspace stays.

**Linking an existing workspace, and the adopt policy.** `POST
/api/mirrors` with `workspace_id` links a personal workspace of the caller's
instead of making a new one. Its pages that exist on both sides have no
common base, so the first round **adopts** one side's version whole
(`adopt`: `theirs`, the original's — the default — or `mine`), and every
block whose text differed becomes a `diverged` conflict holding both texts,
resolvable like a merge. The same path serves a normal mirror whose round
was cut short between a page's creation and its state. Pages one side alone
has are created on the other, as always.

**Force.** *Force pull* / *Force push* (`POST /api/mirrors/{ws}/force`
`{direction: pull | push}`) makes one side identical to the other whatever
happened: the next round starts by clearing the bases and cursors
(`_start_force`, under the round lock — a round already running finishes
as it was), every page goes through the
adopt policy (`theirs` for pull, `mine` for push), and pages the losing side
alone has — including pages the winner deleted after a sync, whose
tombstones say nothing during a force — are deleted there (`prune`); what
the loser had is kept in `diverged` conflicts. A force pull reads the local
feed whatever the direction, so a receive-only clone's own pages go too.
Cheap when little differs:
a page whose trees are equal costs one read and no write, and only the
differing blocks of a page are pushed, files only when the other side lacks
the hash. Confirmed inline in the popover; a pull-only clone cannot force
push.

## Publishing

A page of a local Gamma (the desktop sidecar, usually) can be published to
the free share host, so its share link works while the laptop is closed
(`gamma/publish.py`, the plan's step 6). The share host is a Gamma with
cloud sign-in under the `provision` policy and the *Accept published pages*
switch on ([cloud_accounts.md](cloud_accounts.md) "The share host"); the
account server names it (`gamma_share_host` in its discovery document).
Publishing is a filtered two-way mirror of the person's default personal
workspace there:

1. `POST /api/pages/{id}/publish` (a workspace editor with a linked Gamma
   Cloud identity holding a token; the page a root block). Refused with 409
   and a message when the account has no identity ("Sign in with Gamma Cloud
   to publish."), when the account server names no share host, when this
   server is itself a share host, or when the workspace already follows
   another server.
2. No mirror yet: the server asks the account server for an access token
   (`cloud_auth.access_token_for`), trades it at the share host's
   `POST /api/auth/cloud/exchange` for a write token on the person's
   workspace there, and links this workspace to it (`create_mirror` with
   `workspace_id` = this workspace, `adopt: mine`, two-way, `page_filter:
   [id]`, named *Gamma Cloud*). Another local workspace of the same account
   that already publishes lends its token instead, since the share host
   keeps one live token per account and calling server. A mirror that
   exists: the page is added to its filter (with `adopt: mine` for the next
   round), and a token the share host no longer accepts is exchanged again
   for every publishing mirror of the account.
3. One round runs at once; the page must have a base afterwards (else 502
   with the round's error).
4. `POST /api/share/{id}` on the share host under the mirror's token makes
   the share (default anyone / view; the request's `audience` / `role` set
   it, on a new link or an existing one through `PUT /api/share-settings`).
   The answer is the link `<share host>/?share=<token>` (`url`), the
   page's public address (`public_url`, below), the share and the mirror's
   status.

From then on the page is an ordinary mirrored page: edits here go there at
the next round, edits made through an edit share come back, conflicts are
the usual rows. `DELETE /api/pages/{id}/publish` stops the share there,
deletes the copy there and drops the page and its base from the filter, all
under the round lock (so no round reads the copy's deletion as the page's);
the page here is untouched, and when the share host cannot be reached
nothing changes (502). An empty filter leaves the mirror row in place.
`GET /api/pages/{id}/publish` reads whether the page is published, its live
share there and the mirror's raw status, plus `can_publish` / `reason` for
the popover.

**The plan's cap.** The share host limits how many pages a Gamma Cloud
plan may publish: `config.PLAN_PAGE_LIMITS` (`{"free": 5}`; the env var
`GAMMA_FREE_PAGE_LIMIT` overrides the free plan's number, 0 lifts it;
other plans are unlimited). A person's workspace there holds only
published pages, so the count is its root pages. The one place a
publishing mirror makes a page there, `POST /api/pages`, answers 402 with
"Free plan: up to 5 published pages. Unpublish one, or upgrade your Gamma
Cloud plan." and `{limit, used, plan}` for an account's default personal
workspace once it holds that many (`publish.cap_refusal`), after the "id
taken" check, so a round re-creating a page that is already there, and
every round of a page already published, is never refused. The plan is the
identity's last `plan` claim, which the share host stores at every exchange
and sign-in. It applies only while the server is a share host; a
self-hosted server never counts. On the publishing side, `publish` reads
`GET /api/publish/limit` on the share host before a page's first round
there; a full workspace is exchanged once more first, so an upgrade counts
at once. Still full, the page leaves the filter again and the answer is 409
with the share host's words and `limit: {used, max, plan}`; a 402 the round
itself met (the workspace filled up meanwhile) ends the same way.
Unpublishing deletes the copy there, which frees a slot. `GET
/api/pages/{id}/publish` carries the same `limit` whenever the account
holds a publishing token, read fresh on every call.

**Public addresses.** With `GAMMA_PAGE_HOST` set on the share host (a
pattern such as `{username}-pages.gammapdf.com`, checked at startup: one
`{username}`, a hostname otherwise), every published page also has a pretty
address on a hostname per account,
`https://<username>-pages.gammapdf.com/<slug>-<page id>`. The suffix keeps
page hosts apart from service hostnames (services are never named with it).
The slug (`publish.slug`, mirrored in `frontend/src/shared/lib/slug.js`,
pinned by `tests/shared/slug.json`) is the title ASCII-folded (NFKD, marks
dropped), lowercased, runs of anything but `[a-z0-9]` turned into one `-`,
trimmed, at most 60 characters; a title with nothing left (a CJK one) gives
none and the path is just `/<id>`. It is decoration: routing uses only the
trailing id, so a renamed page keeps its links. The publishing server
builds the address (`public_url` in the publish answers) from the share
host's `page_host` in its `/api/server-config`, the account's username
there (the mirror's `remote_user`), the page's title and the share host's
scheme and port; without a pattern it is the token link.

A page host serves the same SPA (asset URLs are root-relative, so any host
loads them). At boot (`PageHostGate` in `App.jsx`) the app reads
`/api/server-config`; when `page_host` is set and the hostname matches it,
it calls `GET /api/pages/resolve-public?host=&path=` and enters the share
view with the token it returns, as if `?share=<token>` were in the URL
(`utils.setShareView`); the address bar keeps the pretty address, its slug
brought in line with the current title. The resolver reads the username out
of the host, takes the page with the trailing id (a page id may hold a `-`,
so every tail after a `-` is tried, the longest shared page winning) from
that account's default personal workspace, and answers its share; the
share's audience and role apply as for the token link. Any other path on a
page host, the home included, is the share view's "not found". Cookies are
per host, so on a page host nobody is signed in: a page shared only with
signed-in users or invited people shows the sign-in gate there, and its
token link is the way in.

Limitation: a workspace that is already a copy of another server (a clone
of the lab's NAS) cannot publish: one remote per copy, and the page's home is
that other server. Publish from there (its admin can turn it into a share
host, or its pages can be shared from it directly). The publishing mirror
is listed in `GET /api/mirrors` with the clones; the UI shows it apart
("What the person sees", Publishing).

## API

| method | path | what |
|---|---|---|
| GET | `/api/mirrors` | the caller's mirrors with status |
| POST | `/api/mirrors` | `{remote_url, token, name?, mode?, workspace_id?, adopt?}` → the mirror (validated against the remote's `whoami` first; a read token or a viewer's role makes it `pull`; `workspace_id` links an existing workspace of the caller's under the `adopt` policy); the first fill runs in the background |
| GET | `/api/mirrors/{ws}` | one mirror, with `conflicts_open` and `conflicts_newest` (the newest open conflict's id: the pair changes exactly when the open conflicts do), `pending_local` (a local write no round has pushed yet; two-way copies only), `poll_s`, `on_change`, `detached`, `interval_s` (0 = the loop is off), `page_filter` (null = every page) |
| PATCH | `/api/mirrors/{ws}` | `{poll_s?, on_change?, mode?}` — the cadence and direction |
| POST | `/api/mirrors/{ws}/sync[?wait=1]` | a round now |
| POST | `/api/mirrors/{ws}/detach` | detach (the link is kept) |
| POST | `/api/mirrors/{ws}/relink` | `{token?, remote_url?, adopt?}` — link again, a round in the background |
| POST | `/api/mirrors/{ws}/force` | `{direction: pull \| push}` — replace one side with the other, in the background |
| DELETE | `/api/mirrors/{ws}` | forget the link |
| GET | `/api/mirrors/{ws}/log?limit=` | what the last rounds did, page by page, newest first (`stats` counts, `changes` block by block, `exists`: the page is still here) |
| GET | `/api/mirrors/{ws}/conflicts[?resolved=1][&page=]` | the decisions to look at (`mine`, `theirs`, `result`, and `base` for a merge), one page's with `page` |
| POST | `/api/mirrors/{ws}/conflicts/{id}` | `{choice: keep \| mine \| theirs}` |

Session-only, the mirror's owner only, never a guest. Publishing's three
endpoints are in [api.md](api.md) "Publishing".

## Testing

`backend/tests/test_mirror.py` runs the whole thing in one process: one
account's workspace is the remote, another account's mirror follows it, and
the engine's transport is a TestClient (`sync_engine.default_fetch`) so
every request is the real HTTP API with the real token. Covered: the first
fill, edits both ways, different-block and same-span merges with the
conflict rows and their resolution, edit-versus-delete both ways, pages
created and deleted on either side, files by hash, pull-only, stopping.
`test_sync_tree.py` pins the diff; `test_token_api.py` the bearer rules;
`test_sync_feed.py` the feed. `test_mirror_edges.py` is the odd cases:
typing while a round is in flight, two clones of one remote editing the
same blocks, a move against a delete (both ways: a block moved here out of
a subtree deleted there, and a block moved there out of a subtree deleted
here), a child added inside a subtree
deleted here, a subtree deleted on both sides, the same position taken on
both sides, the title renamed on both sides, props against text, the same
edit on both sides, a round cut short after its push, resolving a conflict
after more typing, a block moved to another page while edited here, edits
made while the remote is unreachable — each ending with both sides equal. The progress reports, the interrupted-flag
reset, the retry of a page that failed, detach + re-link, linking an
existing workspace, the force in both directions, the cadence and the
sync-on-change trigger, a detach and a force asked for while a round runs,
edits made while the remote had demoted the account, a force pull on a
receive-only clone, and a conflict resolved after its block moved to
another page are in `test_mirror.py` too. `test_publish.py`
covers the page filter (pages and deletions outside it stay put both ways,
a page added later goes over, a published page removed there leaves the
filter), the share host's exchange against a fake account server, and
publishing end to end: the page and its PDF on the share host, the link
resolving there, an edit through an edit share coming back, unpublishing,
a re-publication, a revoked token exchanged again, and the refusals. The browser scenario
drives the popover's settings view, detach / link again and the merge chip;
`publish.mjs` publishes end to end against a second Gamma started as the
share host and a stand-in account server
([debugging.md](debugging.md)). The
desktop's flow — the *keep offline* chip, the registry map, the
*offline copy* / *original* cross-links, one copy per workspace — is a step
of `desktop/test/e2e.js`.

## Limits and next steps

- The op log is not replayed: a round works from trees, so a page that
  changed on both sides costs one fetch and one push, and the remote's
  per-batch authors are not carried into the copy's log (its actor is
  `mirror`).
- The change feed lists a page whose root moved; a writer that never stamps
  the root would be missed — every writer does today (ops, reloads,
  cross-page moves, imports).
- A mirror of a mirror works but doubles the delay; a workspace mirrored
  from two servers into one copy is refused (one remote per copy).
- Every automated test runs both sides in one process (the backend suite's
  TestClient transport; the browser scenario clones a workspace of the same
  server). Two real servers are not exercised.
