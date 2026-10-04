# Real-time collaboration

Several people can edit a page together through a shared workspace or an
editable page share. Their changes and cursor positions appear live. Two
browsers signed into the same account also work. Backend: `gamma/ops.py`, `gamma/collab.py`,
`gamma/routers/collab.py`. Frontend: `src/collaboration/usePageCollab.js`, `src/shared/model/blockOps.js`,
`src/collaboration/Presence.jsx`, plus small hooks in `app/App.jsx`, `editor/BlockTree.jsx`,
`editor/BlockCmEditor.jsx` and `editor/blockHistory.js`.

Block undo/redo returns a description of the action (`describeTransition`):
note creation/deletion/move, a text edit with a short preview, a properties
or highlight edit, a text box's creation, deletion, text edit, move, style
change or resize ([text_boxes.md](text_boxes.md)). It is derived from the
before/after trees, so a rebased remote change is never named as ours.
The status pill shows it. Undo never
takes back what someone else changed after the step was recorded (see
"reconciliation" below): what it had to leave alone is said ("Undone: … —
kept what someone else changed since"), a step with nothing of ours left
says "Can't undo: someone else changed this since", and a step that changes
nothing any more is passed over, never reported as undone. The
handwriting stroke history is separate ([handwriting.md](handwriting.md)).

## The model in one paragraph

A page's editor sends small **operations** on blocks. The server applies each batch in one transaction, orders
it (a per-page `seq`), logs it, and fans it out to everyone on the page over a
websocket. Edits to different blocks or property keys can coexist, and two
people typing in one block keep both edits unless they change the same
characters (a three-way merge, below). Structural changes can conflict, such
as editing a block someone else deletes. Presence helps people coordinate
but does not prevent conflicts.

SQLite is the source of truth; there is no OT or CRDT. Concurrent edits of
one block's text are reconciled by a stateless three-way merge at apply
time (`gamma/textmerge.py`, below).
Presence is temporary: standalone cursor messages and optional cursors on
operation batches are broadcast, not logged. The one exception is a named
batch's caret, kept on its row of the operation log only so that a retry
gets the same answer ("Ops" below).

## Ops (`gamma/ops.py`)

| op | fields | notes |
|---|---|---|
| `set` | `id`, `content?`, `base?`, `props?`, `base_props?` | `content` replaces the text; with `base` (the text it was edited from) it is merged into a block changed meanwhile ("same-block merge" below); `props` is a PATCH (`{key: value \| null}`, null deletes), so unrelated properties never conflict; `base_props` holds the values the patch was computed from — an ink group's new `ink_url` whose base is not the stored one is merged into the stored drawing by stroke ([handwriting.md](handwriting.md) "Two writers, one group"), a text box's `text_box` into the stored box key by key ("same-box merge" below), other keys stay last-writer-wins |
| `insert` | `id`, `parent`, `position?`, `content`, `props` | the client mints id and position (fractional-indexing, same library both sides); a position colliding with a sibling is re-keyed and the applied op echoes the final key; re-inserting an id the page already has (a retry, a rescue) leaves the block as it is, so nobody's newer edit or move is undone, and echoes it |
| `move` | `id`, `parent`, `position?` | cycle-checked (400), collision-re-keyed |
| `delete` | `id` | the subtree; an unknown id is a no-op (a retry) |

Rules: every touched block and every insert parent must be inside the page
(403 otherwise, 404 unknown), judged by its stored `page_id`. An insert
writes the batch's page there; ops carry no `page_id` or `kind`, since the
server keeps both ([user_db.md](user_db.md) "pages.db"). The page root may only be `set` (a share
editor: its content, never its properties) and is never moved, deleted or
inserted. `parent: "root"` is refused: ops never create pages. A bad op
fails the whole batch and nothing is written. A page root's filing
(`properties.folders` / `labels`) must be a list of block ids (400
otherwise); it is stored as written, an id of a folder this copy does not
have included (next section).

### The folder and label trees

Folders and labels are blocks ([home_library.md](home_library.md) "Folders
and labels"), under two reserved parentless rows beside `root` and `trash`:
`folders` and `labels` (`blocks_store.TREES`). To the op path each is a
**pseudo-page**: a batch may name `folders` or `labels` as its page
(`POST /pages/folders/ops`), and its ops touch the blocks under it, whose
stored `page_id` is the tree's id. A new folder is one `insert` (parent
`folders` or a folder), a rename one `set` of its content, a move or a
reorder one `move`, a pin a `set` of `properties.pinned`; a label is an
`insert` under `labels` and stays flat (another parent: 400 "labels do not
nest"). The reserved row itself is never set, moved or deleted (403), and a
block of a tree never moves into a page or a page's block into a tree (403
"outside this page"). None of it touches a page: a rename is one row of the
tree, not a rewrite of every page filed below the folder. Deleting a folder
or a label is the one change that reaches pages, so it has its own
endpoint: `DELETE /folders/{id}` / `DELETE /labels/{id}`
(`routers/folders.py`, [api.md](api.md) "Folders and labels"). One
transaction runs the subtree's `delete` on the tree and one `set` per page
that carried an id of it (`ops.apply_batches`). The tree's `delete` op
itself files the folder chats into the library chat's history and
`ops.after_commit` stops the folder's shares, so a folder deleted by a
plain tree batch (a mirror round or the iPad relaying one) is cleaned up
the same way; only the pages' refiling is the endpoint's own. A tree has everything a page has: its op log
(`GET /pages/folders/ops?since=`), its row of the change log (touched
`live` by every batch), its room (`/ws/page/folders`) and a subtree read
with `seq` (`GET /blocks/folders/subtree`). Only members reach a tree: no
share link does, and the share view reads its folder in the listing
instead.

A page's `folders` / `labels` may name an id with no block (a dangling id:
its folder deleted by a raw op or on another copy, or not brought by a
mirror yet). Every reader passes it by, nothing repairs it on a read, and
the op path stores a filing as written — a mirror's page must not lose an
id its tree has not reached, nor a published page the ids of folders the
share host never sees. The writers that refile a page by intent (the
clip, the agent, an import's merge, a backup merge, a folder delete) keep
only the ids that exist (`blocks_store.existing_in`), so a dangling id goes
with the page's next refiling.

When the op was fine but the page changed under it, the answer adds
`conflict` and `index` (the op's place in the batch): `missing` (its block
or parent is gone, 404), `moved` (it lives in another page now, 403
"outside this page") or `cycle` (the move would make one, 400). A client can
then tell a race from a malformed batch and drop just that op. The statuses
match what a server without conflict codes answers, so an offline copy's
engine can match on them against a server of any version.

Lone UTF-16 surrogates (half an emoji) in any string of a batch are stored
as U+FFFD (`ops.storable`, also on the block create and update bodies).
SQLite cannot encode them, and one would fail its batch on every retry.
A number that is not finite (`NaN`, `Infinity`, `-Infinity`, or `1e999`,
too big for a float) fails its batch with 400 `not a finite number: NaN`,
and nothing is written. Python's JSON reader takes them bare, SQLite would
store them, and no JSON answer can carry one back. The block create and
update bodies refuse one with the same 400 and wording, as do the other
bodies built on `ops.StorableBody` (the metadata edit, the chat writes).
A value stored before the rule stays; the tree reads send it as null.

Only touched rows get `updated_at`; the page is touched once per batch
(`blocks_store.touch_page`: the root's stamp — home-feed order — and the
page's row of the workspace change log, "The change feed" below). The notes
index follows the touched rows inside the batch's transaction (its triggers,
[user_db.md](user_db.md) "The notes index"). A batch tracks the upload
names its ops stop and start referencing. The names it takes up clear their
`upload_orphans` rows in the same transaction. The names it dropped
(`dropped_uploads` on the result; a cut and paste within the batch drops
nothing) go to `upload_gc.schedule`, a check on its own thread, never in
the request ([user_db.md](user_db.md) "Stored files"). The data.db purge
(`pdf_index.purge_unused`, the PDF rows of papers nothing carries) runs only
when a deleted block carried a PDF.

A batch may carry `batch`, the client's id for it, the same on every retry.
The batch's row of the op log keeps the id (`batch_id`) and the writer's
caret as stored, remapped by a merge (`cursor`), one row per page, client
and id ("The op log" below). A retry looks that row up first thing under
the batch's write lock and gets the first attempt's answer from it (seq,
time, actor, the ops as applied, the caret) instead of being applied
twice, which the three-way merge would otherwise do to the same
keystrokes. Nothing is fanned out or derived again. The
answer is in the database, so it survives a restart. The limit is pruning:
once the log has dropped the row, a retry is applied again, which the
create-if-absent insert and the merge's "already the text" rule make
mostly harmless. A refused batch writes no row, so the same id, fixed and
sent again, applies. An offline copy names its pushes the same way and
keeps the ids in its state until the answer is read, sending again only
what the remote does not show yet ([mirror.md](mirror.md) "Rounds cut
short").

`apply_ops(conn, page_id, ops, actor=, client=, share_scoped=, cursor=, batch_id=)` applies and
commits; `after_commit(ws, conn, result)` does the derived-data work and
publishes; `commit_ops(ws, page_id, ops, actor=)` is both on a fresh
connection; `apply_batches(conn, [(page_id, ops), …], actor=)` applies
several pages' batches in the caller's transaction, each logged on its page
(the caller commits, then `after_commit`s each); `ensure_filing(ws, conn,
paths=, labels=, under=, actor=)` makes the folders and labels a writer
files by name where they are missing (one committed batch per tree). In all
of them `ws` is the workspace id and `actor` who makes the change
(`auth.actor_of`: the account's id, or a label for a writer that is no
account). Every server-side writer
goes through them — the single-block endpoints in `routers/blocks.py` are thin
wrappers, page attach/detach, the metadata write, the clip endpoints, the
attachment-marker backfill of `get_or_create_doc_page`, the `annot_stripped`
marks after an embedded-annotation strip, and the AI
tools (`edit_block`, `create_block`, `move_block`, `rename_page`, `move_page`)
call them directly — so everything a page's viewers see comes from one path
and one log. Reads never write: a stored shape that needs repairing is a
`normalize.py` step ([migrations.md](migrations.md)), not a fix-up on a
listing. Pages themselves are not blocks of any page: creating one is
`blocks_store.create_page` (a plain insert). Deleting one is `ops.trash_page`,
which moves the page under the reserved `trash` block for 30 days
([home_library.md](home_library.md) "Recently deleted"; a share host
deletes for good at once). A batch on a page
there is refused like one on a deleted page (404), and a block it holds is
outside every live page. `ops.restore_page` brings it back. Deleting for good
is `ops.delete_page`: the subtree and the page's log rows go in one
transaction, and the page's row of the change log turns `deleted`, so
another copy of the workspace can tell "deleted" from "never seen".
Trashing turns it `deleted` already, and a later `delete_page` keeps that
row; creating the id again turns it `live`. Writers that rewrite a tree wholesale (`PUT
/blocks/{id}/children`, imports into an existing page, the target half of a
cross-page move) log and publish a `reload` instead; a cross-page move's
source page gets a `delete` (`record_ops`), and the moved subtree's rows
take the target's `page_id` (`ops.move_across_pages`, the one cross-page
move: `/blocks/{id}/reorder`, the agent's `move_block`, a revert). The
subtree replace and the cross-page move each run in one transaction under the write lock
(`blocks_store.write_lock`), so the checks and the writes see one state and
a failure half way leaves the old tree. `blocks_store.delete_subtree` /
`delete_children` start with `DELETE` (and `move_subtree_to_page` with
`UPDATE`) for that reason: Python's sqlite3
opens its implicit transaction only before a statement that begins with
INSERT, UPDATE, DELETE or REPLACE, so a `WITH … DELETE` would commit on the
spot.

## The op log

`page_ops(page_id, seq, actor, client, at, ops, batch_id, cursor)` in each workspace's `pages.db`
(`db.PAGES_SCHEMA`), one row per applied batch, `seq` counting up per page
(the write lock is taken up front with `BEGIN IMMEDIATE`, so it never
collides). `actor` is the id of the account that made the change (a share
editor's own; [user_db.md](user_db.md) "Accounts are named by id"), or a
label for a writer that is no account: `link:<name>` for a link visitor,
`mirror` for a mirror's round; `''` for the server's own writes. An actor is
shown by name only where a list shows people (`auth.actor_names`, Recently
deleted's `deleted_by`); the log, the batches on the socket and the change
feed carry it as stored. `client` the tab's id, `"ai"` (the agent's tools), `"revert"` (the
user taking an agent change back from the chat, [ai_tools.md](ai_tools.md)
"Reverting a note change") or `"meta"` (the
paper-metadata worker's property writes — the one content write opening a
page can cause, [paper_metadata.md](paper_metadata.md)). `batch_id` is the
client's name for the batch and `cursor` its caret as stored (JSON); both
are `''` on a batch without a name, which covers every server-side writer
(`record_ops`, `log_reload`, `apply_batches` and the rest). A partial
unique index (`idx_page_ops_batch`, where `batch_id != ''`) allows one row
per page, client and id. The catch-up below returns neither column: an old
batch's caret would place a peer who may have left.

A `set` logs the block's whole text, so typing in one long block writes that
text once per flush. The log is therefore bounded three ways per page:
`KEEP_OPS` rows (300), `KEEP_OPS_HOURS` of age (24) and `KEEP_OPS_BYTES` of
payload (2 MB). The bounds are checked every `PRUNE_EVERY` batches (16), and
at once after a batch bigger than its share of the bytes (`ops._prune`).
Only the oldest rows go and the newest always stays, so `seq` keeps counting
from it and a gap is still told by the lowest seq left. A batch's id goes
with its row, so a retry that arrives after its row was pruned is applied
again ("Ops" above).

`GET /api/pages/{id}/ops?since=` returns the batches after a seq. It answers
410, and the client reloads the tree, in three cases: the log no longer
reaches back; what follows is more than a catch-up should replay
(`CATCHUP_MAX_BATCHES` 200, `CATCHUP_MAX_BYTES` 1 MB); or the log cannot
continue from `since` at all, because it ends before it or skips the batch
right after it. A backup restore leaves the last case
([workspaces.md](workspaces.md) "Export and backups"). It puts a page's log
from the backup in place, then logs a `reload` one above the highest seq the
live log or the backup had (`log_reload(after=)`). A replace reads the live
log, copies the backup's pages.db in (all but the change log, which carries
on: "The change feed" below) and writes those `reload`s in one write
transaction (`ws_backup._replace`), so a batch posted meanwhile waits and
lands above the `reload` instead of taking a seq the restored log hands out
again. That way a page's seq never goes back, and a tab that was anywhere before the restore reloads
instead of dropping the next batches as already seen. The restore also tells
the open rooms to reload.

`GET /blocks/{id}/subtree` on a page carries the `seq` its tree reflects,
both read in one snapshot (a batch committed between two separate reads
would be counted but missing from the tree).

## Commit listeners

`ops.commit_listeners` is a list of `fn(ws, client, page_id)` that
`ops.notify_commit` calls after every committed write: a batch
(`after_commit`), a page deletion, trashing or restore, a cross-page move's
`record_ops`, a `note_reload`, and a backup restore (`page_id` "",
`ws_backup._announce`). One registers
at import: an offline copy's engine (`sync_engine._on_commit`, its
sync-on-change). A listener that raises is logged and never breaks
the write.

## The change feed (`gamma/routers/sync.py`)

`page_changes(page_id, seq, kind, at, actor)` in each workspace's `pages.db`
is the workspace-wide log the per-page op log lacks: one row per page that
exists or ever existed, `kind` `live` or `deleted`. Every write to a page
calls `blocks_store.touch_page(conn, page_id, actor, kind)` inside its own
transaction: the row takes the next seq of the workspace (`MAX(seq) + 1`),
and a `live` touch also stamps the root's `updated_at`. The writers that
touch `live` are `apply_ops` (once per batch, a tree's batch touching the
tree), `record_ops` (a cross-page
move's source), `log_reload` (a subtree replace, an import into an existing
page, a cross-page move's target, every page a backup restore wrote),
`create_page` (a page made, or made again under an id the log has as
deleted), `restore_page` and the raw import paths (the Markdown and Zotero
imports' new pages, the Logseq import's new page once its notes are in).
`trash_page` and `delete_page` (of a page not in Recently deleted already)
touch `deleted`. Each holds the workspace's write lock (`write_lock`, or the
transaction's first write takes it), so seqs are handed out in commit order
and never twice. `tests/test_page_changes.py` names the writers, and every
pages.db the suite writes is checked after each test (`page_changes_drift`:
a page of the library has a `live` row no older than its stamp, a page in
Recently deleted a `deleted` one, a tree with blocks a `live` one no older
than its newest block). The trees are listed like pages (their ids are
`folders` and `labels`); they are never deleted.

`GET /api/sync/changes?since=&limit=` lists the rows with `seq > since` in
seq order, at most `limit`: a live page with its latest op `seq` (`pages`),
a deleted one as a tombstone (`deleted`, its `at` and `actor`), `cursor`
the highest seq listed (or `since`), `more` while rows remain. It exists
for anything that keeps a copy of a workspace in step (a mirror,
[mirror.md](mirror.md); the iPad's replica, [ipad.md](ipad.md)) so it can
find out *which* pages to look at without walking the library; what
actually changed on a page is still its op log (`seq`,
`GET /pages/{id}/ops?since=`), and a page whose log no longer reaches back
is refetched whole.

The feed is exact. A change is listed once to any cursor below it: a page
has one row, so a page written again moves to its new seq and is listed
there again. Nothing slips behind a cursor: a reader never
sees a seq before every seq below it has committed. A consumer walks the
feed to the end and keeps the cursor; a page listed twice in one walk
(written again meanwhile) is what its last entry says. The cursor is a
decimal string the consumer stores as it is (`""` = from the start). One
the log cannot have given out (not a count, or above its newest seq: a
workspace put back whole from an older snapshot) reads as from the start,
which costs a consumer a re-walk and nothing else, since it compares each
page's op `seq` with its own.

Deleting a page (`ops.delete_page`) drops its log rows and leaves its
`deleted` row; creating the id again turns it `live`. A page moved to
Recently deleted (`ops.trash_page`) keeps its op log, but its row turns
`deleted`, so to a copy it is deleted. Restoring it turns the row `live`,
so the page is listed again as if created. A backup restore never replaces
the live change log, so a copy's cursor in it stays good: a replace touches
every page it wrote and turns `deleted` every page that is no page of the
library afterwards, a merge touches the pages it brings back
([workspaces.md](workspaces.md) "Export and backups"). The page's room
hears both: `trashed` when it goes (an open tab keeps what its typist has
not sent yet and says the page was deleted, instead of reloading into a
404) and `reload` when it comes back — from the trash (`ops.restore_page`)
or with a merge restore that takes it out of the trash (`ws_backup._merge`)
— so the tab refetches and sends what it kept.

## Rooms and the socket (`gamma/collab.py`, `routers/collab.py`)

One in-memory room per `(workspace, page_id)` — Gamma is one uvicorn process
everywhere (Docker, the desktop sidecar), so nothing is shared across
workers. `publish` schedules sends on the loop the sockets live on and is
safe from threadpool code. `POST /pages/{id}/ops` is a sync def, like the
other block writers and the sync AI chat endpoint that runs the tools: a
batch may wait on the workspace's write lock, which must never happen on the
loop. Batches committed one after the other by several worker threads are
published in seq order as a rule, because `after_commit` fans out before the
data.db cleanup; the client's ordered inbox takes the exception. A handler
being torn down announces its leave through `publish` too, never by awaiting
inside a possibly cancelled scope.

`WS /api/ws/page/{page_id}[?ws=id&share=token&client=id]`. The HTTP
middleware does not run for websockets, so the handler resolves the session
cookie itself (`auth.session_lookup`), the workspace (`?ws=`, else the
account's default — `auth.workspace_access`) and the share token. Whether
they admit the viewer, and with edit rights, is
`collab.peer_access(ws, page, account, is_guest, token)`, the HTTP rules
(`auth.share_access` for a token): a member joins with their workspace role
(viewers presence-only), a share token admits its audience (view or edit —
an anyone-with-the-link edit share admits a visitor without an account, who
joins under the display name in `?name=`, [api.md](api.md) "Link
visitors"). The rooms of the `folders` and `labels` trees admit members
only. Anything else is closed with 4403 before accept. The handshake
runs these reads (`_socket_access`) and the read of the log position in
worker threads; the room itself only ever changes on the loop. Once the peer
is in the room and has its hello (still the first message a socket gets),
`peer_access` runs once more: a revoke that landed between the first check
and the join found no peer for `revalidate` to close, so this check closes
it (4403), announcing its `leave`. A
client gone at any point of the handshake (navigated away as the socket
opened) is an ordinary close, never an ASGI error.

`collab.revalidate(ws)` runs `peer_access` again for every peer of the
workspace's rooms whenever access there changes: a share updated or stopped
(`routers/shares.py`, a folder share dropped with its folder), a
member re-roled or removed, the workspace's access changed or the workspace
deleted (`routers/workspaces.py`), an account deleted
(`collab.revalidate_account` from `workspaces.delete_account`: every
workspace with a room it is in, and the workspaces that went with it — a
share peer whose account is gone counts as a stranger), and a batch that
changes what a folder holds — a page root's `folders` set, a folder moved or
deleted on the `folders` tree (`collab.revalidate_shares` from
`ops.after_commit`, only when a peer of the workspace came through a share
link: a folder share reaches the pages filed in its folder or below it, and
the batch itself still reaches the visitor it refiles away). A peer that lost access leaves the room
at once and its socket is closed with 4403; the client does not reconnect.
A peer whose edit right changed is announced again with a fresh `join`. Any
handler may call it: it runs on the sockets' loop, does the database checks
in a worker thread, then applies the answers to the peers still in the room.

A peer joins the room before the hello's `seq` is read, so a batch committed
while it was joining is either counted in the hello (the client catches up
on it) or fanned out to it, never neither. A batch fanned out while the seq
is being read reaches the socket before the hello; the client's ordered
inbox takes it whenever it arrives. The same tab joining again (its client
id: a reconnect before the server noticed the old socket drop, a
display-name reconnect) replaces its peer, keeping its colour, and the old
socket is closed with 4409. `leave` removes only that very peer, and
unregisters a room only while it is the registered one. A leave is announced
only when something was removed, so an old socket's teardown can neither
knock the tab out of its room nor take the room from the others. A peer
whose send fails leaves at once and the room announces its `leave` then
(its handler's teardown finds nothing left to remove), so no ghost peer
stays with the others.
Messages:

- server → client: `hello {client, color, seq, peers}` on join; `join {peer}`
  / `leave {client}`; `cursor {client, block, anchor, head}`; `ops {seq, at,
  actor, client, ops, cursor?}` for every applied batch (the sender's own
  included, it filters by client id; `cursor` is the writer's caret in the
  text after the batch, when the POST carried one — the server also stores
  it as the writer's presence); `reload {seq}` (refetch the tree — a
  change ops cannot express, a restore, the page back from Recently
  deleted); `trashed` (the page went to Recently deleted: keep the unsent
  edits, show it, wait for a `reload`).
- client → server: `cursor {block, anchor, head}` only (`anchor`/`head` = -1
  when no editor is open on that block). **Writes never travel over the
  socket**: they are `POST /api/pages/{id}/ops`, so auth and scoping live in
  one place. A dropped socket does not stop HTTP saves; a closing tab attempts
  to flush queued edits with a keepalive fetch.

A peer is `{client, user, name, color, can_edit, block, anchor, head}` —
`user` the account's username, which the handshake reads with the session;
the room keeps the account's id beside it (never sent) for `revalidate`.
Colour is an index into an 8-slot palette handed out per room (CSS `--peer-N`).
A share-link visitor without an account has `user: ""` and `name` = their
display name (`?name=`, else `Anonymous`); their op batches carry
`actor: "link:<name>"`. A rename in the share view reconnects the socket
(`usePageCollab`'s `reconnect`) so presence picks up the new name.

## The client (`src/collaboration/collabSession.js`, `src/collaboration/usePageCollab.js`, `src/shared/model/blockOps.js`)

`createCollabSession` (`collaboration/collabSession.js`) is the session as a plain state
machine over injected dependencies — the JSON call, the socket factory, the
keepalive POST, timers, and callbacks for peers/me — so the node tests drive
it with fakes. `usePageCollab` (`collaboration/usePageCollab.js`, one per open page in App.jsx)
only wires it to the browser: `utils.apiJson`, `new WebSocket(...)` on the
share- or workspace-qualified URL, the pagehide keepalive, a `beforeunload`
prompt while `hasPending()`, a flush when the tab goes to the background
(`visibilitychange` to hidden — a phone may kill it without a pagehide), an
immediate retry on the browser's `online` and on the window regaining
focus, and `peers` / `me` as React state. The session owns:

- **the base tree**: what the server is known to hold from this tab's point of
  view. The block tree's transition effect calls
  `commit(tree)`: a load (a fetched tree, marked by App's `loaded()`) makes
  the tree the new base; any other transition is diffed against the base
  (`diffTrees`) and the ops queued. The mark sits on the tree value itself
  (a `WeakMap` of tree → `"load"` / `"remote"` / `"fold"`), read once by
  the transition that commits it. `"fold"` (`foldBlocks`, a text box's
  measured size) is diffed and sent like any edit but joins the undo
  entry before it. It is never a flag set beside `setBlocks`: such a
  flag outlived a load React skipped (nothing changed) or was set by an
  effect running before the autosave one, and the edit committed with it
  was taken for a load and never sent. A view change (unfolding to reveal a
  block) is not a load either: it diffs to nothing. One exception:
  an empty page opens with a client-minted placeholder block
  (`seedBlockIdRef` in App.jsx) that the server has never seen, so the load
  commit leaves it out of the base — the first edit to it diffs as an
  `insert`, never as a `set` the server would 404. Positions live in
  one `Map id → key` shared with `blockOps`, so tree objects and history
  snapshots stay untouched.
- **fetched trees keep unsaved edits**: every tree App fetches for a page
  (opening it, a `reload` message, a 410, a conflict, a refetch after an
  import) goes through `overlay(pageId, tree)` first, which lays this tab's
  unsaved edits of that page over it (the batch out, then the queue; ops
  apply idempotently). The base the load makes then already holds them while
  they are still queued, which is what the base means: what the server will
  hold once the queue has landed. Nothing is taken from the screen's tree:
  a screen copy of unsent text would become the base and never be sent. The
  open editor and the folding live beside the tree (App's `view`), so a
  refetch leaves them alone. A page left
  with edits unsaved keeps its session. Back on it, `commit` resumes that
  session (its queue and retries go on, its acks land on the new visit's
  tree) instead of starting one beside it.
- **per-page save state**: each page keeps its own queue, positions, pending
  content counts and retries. Navigating while a save is in progress does not
  retarget its queued edits or let its response change the next page's state.
  `set` ops on one block coalesce (`pushOp`); typing flushes
  after 350 ms, a structural op after 80 ms, an editor closing at once
  (`saveNowRef`), `flush()` before navigation. A batch (`out`) is at most
  `MAX_OPS` ops of the queue, in order (a paste of 600 blocks is two). It
  keeps its id (`batch`) until the server answers it, on every retry and in
  the pagehide keepalive, so a retry of a batch that did land is answered,
  not applied again. The POST response is the ack: re-keyed positions are
  adopted from it, and an insert the server already had converges on the
  block as the server has it. A refused batch is handled by why:
  - a 404 naming a block the server doesn't have (`missing`) while the base
    holds it sends that block and its subtree again ahead of the batch:
    inserts (create-if-absent), and a move of the block to where this tab
    has it. Its insert may have been lost, or someone moved it under a
    block this very batch deletes — the move takes it out before the
    delete does, so the typing in it survives. A block missing again after
    its rescue is not rescued twice (the batch itself removes it): it is a
    conflict like below. `MAX_RESCUES` bounds the rescues between two saves
    that go through;
  - a `conflict` (the page changed under the batch) drops the op at `index`
    and, unless it was a cycle, every op of the batch and of the queue
    behind it on the vanished block: its own ops, inserts under it (and
    what goes under those) and moves into it — at once, not one op per
    round trip. The rest goes out again at once as a new batch (text edits
    keep the base they were typed from, so the server merges them), and the
    page is refetched once, since the screen still shows the refused
    change. Dropped text or new notes are never dropped silently: a
    "rejected" notice says someone deleted or moved the note;
  - a 404 for the page itself (it went to Recently deleted — the room's
    `trashed` message usually says so first, and App's refetch answering
    404 with a trash entry calls `gone`): the page's edits are *parked*.
    Nothing is dropped or sent; the batch out keeps its id and typing still
    queues. App shows the page's notice with Restore (`onGone`); from
    another page the "save" pill says why edits wait. The next load of the
    page (the restore's `reload`, the Restore button's refetch) unparks
    them: the overlay has kept them on screen and they go out;
  - signed out (401), another account signed in in this browser (the
    X-Gamma-User 409), offline, a server error, 408 or 429: the batch waits,
    id and all, and is retried after 3 s, doubling up to a minute, never
    given up. The `online` event, the window regaining focus and a socket
    hello retry at once, as does a `flush()`; a failure there starts the
    retries over at 3 s rather than stretching them (a save lands seconds
    after the network is back, not a minute). It goes out once the person
    signs in again. Typing meanwhile queues behind it;
  - anything else (malformed, not allowed any more): that batch alone is
    dropped, what was queued after it still goes, and the page is refetched.
  While edits wait, a notice says why in words ("Not saved yet — the server
  can't be reached. Retrying…", never the browser's "Failed to fetch"):
  `onSaveNotice(text, "pending")`, a "save" pill in App that goes when the
  edits are saved — never the in-progress status line, which nothing would
  clear. A dropped edit gets a "rejected" notice that stays 20 s, where the
  status line alone lasts a second. Text over `MAX_CONTENT` (the server's per-block limit) never
  enters the queue. The op goes without it and the base keeps the last text
  that can be saved, so the text is diffed and sent again once shortened.
  The editor stays open on it (`tooLong(id)`), the notice says why, and a
  refetch meanwhile keeps the long text on screen.
- **same-group merge**: an ink group's drawing is saved through
  `PUT /blocks/{id}` with `base_properties: {ink_url}`, the file the
  draft was edited from, and the server merges it into a drawing someone
  else saved meanwhile. The answer's `properties` names the merged file,
  and the draft takes it ([handwriting.md](handwriting.md) "Client").
- **same-box merge**: every change of a text box sends its whole
  `text_box`, since a keystroke stores the size the box measured at. So the
  set carries `base_props: {text_box}`, the box it was changed from
  (`diffTrees` reads it off the base; `pushOp` keeps the base of a key's
  first change in a run). The server merges it key by key into the box it
  holds (`text_box.merge_text_box`), and the echo names the result, so a
  move survives someone typing ([text_boxes.md](text_boxes.md) "Merge").
  - A remote set of a box with changes of ours still on their way lands
    merged with them, as the server will merge them (`ahead`). Theirs alone
    would take our change off the screen until the ack, and a keystroke
    folded into the queued set would then send the box without it.
  - On their way means queued, or in the batch out until its own fan-out
    comes by (`landed`). An own message marks the batch out as landed only
    when its seq is newer than `s.acked`, the seq of our newest answered
    batch. An older batch read late, after a refetch or from the log on
    return, is not the one out.
  - Our batch, in its place in the order, lands the boxes as the server
    stored them where this tab holds something else and nothing newer of
    ours is on its way (`storedBoxes`). The main case is a refetch, whose
    overlay lays our batch over the box whole.
- **same-block merge**: a content `set` carries `base`, the text the change
  was made from (`diffTrees` reads it off the base tree; `pushOp` keeps the
  first base of a run of keystrokes). When the server finds the block
  changed since, it merges the two changes in base coordinates
  (`textmerge.merge`: each side's edit `base → text` as replaced spans from
  diff-match-patch's diff, both applied to `base`): edits to different
  spans both survive, and so do insertions at the same or neighbouring
  offsets, the one stored first coming first — two people typing at one
  caret keep both people's keystrokes (text typed in front of a word the
  other replaced stays in front of the replacement). Only a span of ours that replaces
  characters the stored change also replaced is dropped; the stored text
  stands for it. Typing merges by characters. A revert of the agent's edit
  merges by words and phrases (`semantic=True`, [ai_tools.md](ai_tools.md)
  "Reverting a note change"), so two rewrites of one sentence clash as
  wholes instead of interleaving. The echoed op carries the merged text and the
  batch's `cursor` is remapped into it. On the ack, a set whose stored
  text differs from what we sent lands on screen like a remote op — but
  only when no newer set of ours for that block is queued or in flight:
  that newer set's base is the text we sent, so the server patches those
  keystrokes onto its merge and the later ack brings the whole result;
  touching the base early would make the next diff repeat the change.
  The editor re-reports our caret after any external change of its text
  (a merge, a remote edit before the caret), so the others see it where it
  moved to. Writers without a `base` (imports, a bare `PUT /blocks/{id}`)
  replace the text. `PUT /blocks/{id}` takes a `base` too. An `![[embed]]`
  card editing a block of another page sends the text of its copy, so an
  edit made on the source's own page meanwhile survives; the answer's
  `content` is what the card shows next (a refused write says so and
  fetches the source again). Opening a page fetches the copies its
  cards and `[[ref]]` chips show again (`refreshRefs` in App.jsx), so a
  card never shows what its source said on an earlier visit. The AI agent's
  `edit_block` replace sends the text it read in that turn (`read_block`,
  `read_page`, the chat's context — [ai_tools.md](ai_tools.md)), so a person
  typing in that block while the model writes keeps their keystrokes.
  - The server merges before the batch takes the workspace's write lock
    (`ops._premerge`, against the text stored then). Under the lock the
    batch reuses each merge whose block still holds that text and merges
    any other again, so it stores the same either way. A batch with
    nothing to merge pays one read of its sets' rows by id. Measured in
    2026-10 (`apply_ops` in process, one page): a typing flush's merge is
    0.05–0.1 ms of a 1–8 ms hold, but a block edited in 40 places (the
    agent's edit, an offline copy's round) takes 33–39 ms to merge at 4
    or 16 KB, over 90 % of the hold. With the merge before the lock,
    those holds are 1.5–3.3 ms. The diff still holds the GIL, so the
    process's other requests slow while it runs. What it saves is the wait
    on the lock: SQLite's busy handler polls with growing sleeps, and a
    typist writing beside a stream of such merges got 10 batches through
    in 10 s with the merge under the lock, against 200 outside it.
  - The ink merge stays under the lock, since it stores the merged file
    there ([handwriting.md](handwriting.md) "Two writers, one group"). On
    the Windows test machine almost all of its 20–30 ms is file work:
    reading the three files, the quota check's walk of the uploads
    directory, and the fsync'd write. The stroke merge itself is 0.3 ms.
    A text box's merge is 0.02 ms.
- **reconciliation**: `inflight` counts queued-or-sent content sets per
  block (text folded into a queued property-only set counts too — `pushOp`
  returns the op it folded into). The content of a remote `set` for a block with one in flight is *deferred* and, on
  the ack, applied only if its seq is higher than the ack's (theirs is the
  newer server value), else dropped (ours is). Its property patch still
  applies immediately, so successive updates to different keys are preserved.
  Structure and properties the other way round: a remote batch applied here
  after an edit of ours was ordered *before* it on the server whenever our
  batch comes back with a higher seq. When our batch comes by in the
  ordered inbox (its ack, or its own fan-out while it is still out) and
  remote batches were applied since its edits were queued (`remote` against
  the batch's `mark`), `reassert` replays its structure in its order — moves,
  deletes, and inserts of blocks this tab no longer has (a rescue re-created
  a note someone deleted: it comes back with the text typed here) — and its
  property patches, skipping any block a newer queued edit of ours touches.
  So a block both people moved ends where this tab put it, and a rescued
  note reappears here. These ops land without an undo-history rebase
  (`onRemoteOps`' `own`). A remote move this tab
  can't place — its block or its target is gone here — means an unsent
  delete of ours took them along here but not (yet) on the server, where
  that move came first: a note moved out of the block we deleted survives
  there, one moved into it goes with it. The page is refetched, our unsent
  edits laid over it.
  Other operations apply at
  once — to the base, to the on-screen tree through `onRemoteOps` (a
  `"remote"` transition: no history entry; it is diffed like an edit, and
  since the base already has the ops only an edit of ours rendered in the
  same pass goes out), and to every
  undo snapshot (`blockHistory.rebase`), so undoing your own edit never
  reverts someone else's. App commits a remote transition at once
  (`flushSync`), so its diff runs before the next batch advances the base.
  Rendered later, the tree of a first batch would be diffed against a base
  that already held the second, and an idle tab would send the second
  batch's notes back as deletes and re-inserts (a paste arriving as two
  batches).
  Ops landing on a fetched tree not committed yet keep it a load. A remote
  move that shifts the row of the open editor (it, an ancestor, or a
  reorder among their siblings — `displacedRow`) remounts or detaches that
  editor; App takes its blur for what it is and puts the focus and caret
  back, so the typing goes on.
  In the undo history a remote content set is carried over onto each
  snapshot's own text as the change `before → after` (`rebaseText`, one
  replaced span each side), so undoing our typing in a block someone else
  typed in takes out only ours; where the two spans overlap the snapshot
  keeps its text and marks the block `contested`. A remote `text_box` is
  carried over key by key the same way (`mergeTextBox(snapshot's box,
  theirs, before's)`), so their measured size or restyle never blocks
  undoing our move, nor comes undone with it. Every block a remote batch
  changes is stamped (`touched`), and a step (`undoStep` → `planRestore`)
  never deletes a block, with what it holds, that someone else edited,
  moved or made after the step was recorded, nor reverts contested text:
  those parts are left as they are and the rest applies. A snapshot lacking the target parent of a remote
  move or insert (it predates that block) keeps the block where it was:
  `applyOps` never lets a block vanish with an op it can't place. A load (a
  fetched tree) empties the undo stack, since its snapshots predate what the
  fetch brought in and restoring one would delete a note just moved in.
- **ordered catch-up**: `seq` means the last contiguous batch processed,
  initially seeded from the tree fetch. HTTP acknowledgements and socket
  batches enter the same ordered inbox. If batch 12 arrives before 11, the
  client fetches `…/ops?since=10` before advancing. A hello with a higher
  sequence triggers the same recovery. Only one catch-up request runs at a
  time; old-page responses are ignored. A 410 or a `reload` message
  refetches the subtree through `overlay` (the unsaved edits survive the
  swap; the view beside the tree is untouched). App's refetch
  (`loadBlocksForBlock`) carries a token and the page id: an answer
  overtaken by a newer refetch or arriving for a page no longer open is
  dropped. A failed refetch keeps the tree on screen (never an empty page),
  shows "Couldn't refresh the page" and tries again (2 s, doubling up to
  30 s). It also tells the session (`reloadFailed`), which stops holding the
  socket's batches back for it.
- **presence**: `peers` state from `hello`/`join`/`leave`/`cursor` and the
  `cursor` on an `ops` batch; every update bumps the peer's `rev`, which is
  how the editor tells a fresh caret report from one it should keep mapping.
  `sendCursor` throttled to 80 ms, fed by the editor's selection (`onCaret`),
  editor open/close and the focused row — but while a batch is queued or in
  flight the standalone message waits and the caret rides on the batch
  instead. Carets are offsets in the sender's text; sent on their own they
  reach the others up to a typing debounce before the text does, and an
  offset past the typed characters lands a few characters off in the older
  copy — and stays off once the batch maps it further along. Carried with
  the batch, the receiver syncs the text and places the caret in one render.
  After the ack a held-back caret move goes out standalone; a `hello`
  (reconnect) resends the current caret, since the server starts a joiner
  with none. A socket the server closed with 4403 (access revoked) is not
  reopened.

`diffTrees(base, next, pageId, pos)` emits inserts (unknown ids), moves (a
known id under another parent, or out of order — the longest increasing run
of existing keys stays, the rest are re-keyed), sets (content / property
patches), and deletes of the top-most removed subtrees last (a block that
escaped a deleted parent is moved out first). `applyOps` is idempotent and
keeps siblings sorted by key. The tree is the document only: the block
whose editor is open and the viewer's own folding live beside it in App's
`view` (`{editingId, folds}`, `shared/model/blockModel.js` — `withEditing`,
`toggleFold`, `revealBlock`), so opening an editor or unfolding to reveal a
block changes no tree and produces no op, and the tree's every transition
is a document change. Folding a block writes its stored `collapsed`
property too (the default every viewer opens the page with) and the
viewer's own fold; a block the viewer never touched follows the stored
value, a remote change included.

`flattenBlocks` includes every block, even descendants hidden by a fold;
use it for lookups. `visibleBlocks(blocks, view)` skips folded descendants
for the outliner, using `isFolded` to resolve the viewer's override or stored
default. `closeEditing(view, id)` only closes that editor, so a late blur
cannot close another block's newly opened editor.

The page has one undo stack (`editor/blockHistory.js`), derived from committed
tree transitions; call sites do not opt individual edits into history.
CodeMirror has no separate `history()`. Loads, remote transitions, undo/redo
applications, and folding changes do not create entries. Rapid content edits
of one block coalesce. An editor change records the pre-change selection:
undo while editing restores it, while undo outside an editor opens none.
The stack clears on page switches and fetched loads; cross-page moves are
not undoable. Remote changes are rebased as described above.

Ops on the page root (a rename, page properties) update the title / page
state in App instead of the tree.

## What the user sees (`src/collaboration/Presence.jsx`, CSS in `shared/styles/app.css`)

- the header avatar stack (initial, peer colour; faded while only viewing;
  click jumps to the person's block; the tooltip marks an account-less
  share-link visitor "(via link)");
- small avatar chips before the row a person is on (in the ⋮⋮ handle
  column, fading while the row is hovered — never over the row's content or
  an embed card's controls), and a coloured left edge
  while someone has that block's editor open;
- inside an open editor, each peer's caret with a name tag and a tinted
  selection (`remoteCursorField` in `editor/BlockCmEditor.jsx`, keyed by peer: a
  peer whose `rev` changed is placed fresh from its offsets, the others keep
  mapping through every change — ours and other peers' — so a caret stays
  put while we type and shifts correctly when someone else edits before
  it). External value changes reach the editor as the minimal prefix/suffix
  replacement, tagged so they are not re-reported as local edits — the
  caret maps through instead of jumping;
- on a block whose editor we don't have open, the same caret and name tag
  over its rendered view (`RenderedCarets` in `Presence.jsx`). The offset is
  placed by text, the reverse of how a click opens the editor
  (`locateInRendered` / `renderedCaretRect` in `editor/clickToSource.js`):
  the plain run around it is looked up in the rendered text (again without
  the whitespace at its ends when that fails: the rendered view drops a
  line's trailing space, and typing is usually sent right after one), and
  an offset in markup that renders as nothing (a link's URL) goes to the
  end of the text before it, one inside math to before the formula. It is re-placed
  when the text, the caret or the view's size changes; a caret that can't be
  placed is not drawn.

## Testing

- `backend/tests/test_collab.py`: op semantics, scoping, the log and
  catch-up, the socket (TestClient `websocket_connect`; every socket of a
  test must live on one event loop, so clients come from `conftest.py` —
  `login()`, `fresh_client()` — which share the session client's portal,
  never from a bare `TestClient(app)`), AI-tool and cross-page fan-out. Every socket
  test opens its own page, and sequence numbers are asserted relative to
  the hello's (or the previous ack's) — never as absolute counts — so a
  step added to one test never renumbers the others.
- `backend/tests/test_collab_robustness.py`: conflict codes and the op
  index, a retried batch answered once from its log row (from a fresh
  process too, and applied again once the row is pruned), create-if-absent
  inserts, lone surrogates, a tab reconnecting on its client id, a stale
  room, the hello
  counting a batch committed while joining, revoked shares and removed or
  re-roled members closing or re-announcing open sockets.
- `backend/tests/test_text_box_merge.py`: the same-box merge through the
  op endpoint and `PUT /blocks/{id}`; its rule's cases are
  `tests/shared/textboxmerge.json`.
- `backend/tests/test_textmerge.py`: the same-block merge — word-level
  hunks keeping two rewrites of one phrase apart, different spans,
  insertions at one caret keeping both (through the op endpoint too), an
  insertion in front of a replaced word, an insertion inside the other
  side's deleted span, a span both replaced.
- `frontend/tests/blockOps.test.mjs`: `node --test tests/blockOps.test.mjs`
  from `frontend/` (pure diff/apply round-trips, and which remote moves
  displace the open editor's row).
- `frontend/tests/blockHistory.test.mjs`: what a transition counts as, and
  collaborative undo — a remote text change carried over onto an older
  text, undo taking out only our typing in a block someone else wrote in,
  never deleting a note they wrote in or under, contested text kept while
  the rest of the step applies, a step emptied by them said so, a step that
  changes nothing passed over, a text box rebased key by key.
- `frontend/tests/collabSession.test.mjs`: `createCollabSession` over fake
  HTTP, socket and timers — ack/socket ordering and catch-up, content versus
  property reconciliation, the merged text on an ack (landed at once, or
  held while a newer set of ours is queued), a text box's change meeting
  someone else's (queued, out, after our fan-out, as stored, the next
  keystroke on a queued move, a refetch's overlay, an older batch read
  late), retries and their backoff, rejection, navigation
  during a save, presence messages, the caret throttle, reconnect backoff,
  read-only sessions; browser behavior is covered separately below.
- `frontend/tests/collabRobustness.test.mjs`: the refused-batch paths
  (conflict, signed out, refused for good), every op on a deleted block
  dropped in one round trip, a delete racing a move into it, a rescue not
  repeated and its budget restored, ours put back on top of theirs (a block
  both moved, a note a rescue re-created), a remote move this tab can't
  place refetching the page, a page in Recently deleted parking its
  edits until it is back, early retries starting over, batch splitting,
  text too long, a batch id across retries, a reload and a return to the
  page keeping unsaved edits, a failed refetch, a revoked socket, and the
  undo stack after a reload or over a move it can't place (`blockHistory`'s
  `observeTree` / `rebaseHistory` / `undoStep` without React).
- End to end: `npm run e2e -- --only collab` from `frontend/`
  (`tests/e2e/scenarios/collab.mjs`, [debugging.md](debugging.md)): two
  browser contexts on one page of a shared workspace — presence stack and row
  chips, typing in one tab appears in the other (with the typist's caret on
  the other tab's rendered row), edits to different blocks
  converge, same-block typing keeps both people's text (and edits at both
  ends of one long block both survive with the carets in place), a caret after a
  mid-block edit sits where the person typed (and the other caret shifts
  along), undo after a remote edit
  keeps the remote edit, a rename reaches the other tab, an edit made offline
  lands once the network is back (its notice in words, gone with the save),
  a remote delete, a note moved in with a
  reload surviving Ctrl+Z, a failed refresh keeping the page and retrying,
  undo never deleting the text the other wrote in our note, the other
  indenting around an open editor keeping it open, typing at one caret
  keeping both people's keystrokes, a paste arriving as two batches leaving
  the idle tab silent, typing kept through Recently deleted and saved after
  the restore, a
  highlight made by the other person; `share.mjs` covers the invited editor on a share link and
  the stranger typing through an anyone-with-the-link edit share under a
  renamed display name, and `textBoxes.mjs` a text box one person moves
  while the other types in it.

## Limits and next steps

- Queued edits live in memory, not in durable offline storage. The tab asks
  before it closes with edits unsaved. Keepalive saves on tab close are best
  effort and subject to browser limits (64 KB), so an outage followed by
  confirming the close, or a crash, still loses them.
- A remote delete of a block with our text edit in flight applies at once;
  the edit then comes back as a rescue only if our base still held the
  block, else it is dropped with the block (the screen already showed the
  delete, and a "rejected" notice says the edit couldn't be saved).
- Same-block simultaneous typing merges by span (three-way merge above); a set whose content already is the block's text (the same edit sent twice, a retried batch, a clone pushing what it already pulled) merges nothing — merging it in again would double the change;
  two people replacing the *same* characters within one save window still
  resolve by server order for that span (insertions at one spot keep both).
  If character-exact convergence ever matters, the upgrade path is
  CodeMirror's collab rebase on just the open block; not a CRDT.
- Undo's text rebase takes each side's change as one span: two edits of
  ours far apart in one block with someone's edit between them count as
  overlapping, and that block's text is left as it is by the undo.
- The socket needs a proxy that forwards websocket upgrades (Vite's dev proxy
  has `ws: true`; a reverse proxy in front of the NAS must pass `Upgrade`).
  uvicorn needs the `websockets` package (`requirements.txt`; the desktop
  freeze collects it).
- Rooms are per process: a multi-worker deployment would need a shared bus.
- Presence carries only the block and caret; it could also carry the PDF
  viewport (which page someone is reading).
- The op log has `actor` and `at` per batch but nothing reads them yet: an
  activity view ("who changed what") would be derivable from it, a page
  version history only for the last day it keeps.
- A mirror of a workspace (a desktop copy that syncs) is built on the
  change feed: [mirror.md](mirror.md). It works from
  trees, not from replaying this log, so a copy that was away longer than
  the log reaches back needs no fallback.

The survey behind this design (OT vs record-level LWW vs CRDT, why the old
snapshot autosave could not be patched) is in
[research/collaboration.md](../research/collaboration.md).
