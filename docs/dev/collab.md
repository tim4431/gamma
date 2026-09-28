# Real-time collaboration

Several people can edit a page together through a shared workspace or an
editable page share. Their changes and cursor positions appear live. Two
browsers signed into the same account also work. Backend: `gamma/ops.py`, `gamma/collab.py`,
`gamma/routers/collab.py`. Frontend: `src/collaboration/usePageCollab.js`, `src/shared/model/blockOps.js`,
`src/collaboration/Presence.jsx`, plus small hooks in `app/App.jsx`, `editor/BlockTree.jsx`,
`editor/BlockCmEditor.jsx` and `editor/blockHistory.js`.

Block undo/redo returns a description of the action (`describeTransition`):
note creation/deletion/move, a text edit with a short preview, a properties
or highlight edit. It is derived from the before/after trees, so a rebased
remote change is never named as ours. The status pill shows it. The
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
operation batches are broadcast but never written to the operation log.

## Ops (`gamma/ops.py`)

| op | fields | notes |
|---|---|---|
| `set` | `id`, `content?`, `base?`, `props?` | `content` replaces the text; with `base` (the text it was edited from) it is merged into a block changed meanwhile ("same-block merge" below); `props` is a PATCH (`{key: value \| null}`, null deletes), so unrelated properties never conflict |
| `insert` | `id`, `parent`, `position?`, `content`, `props` | the client mints id and position (fractional-indexing, same library both sides); a position colliding with a sibling is re-keyed and the applied op echoes the final key; re-inserting an id the page already has (a retry, a rescue) leaves the block as it is, so nobody's newer edit or move is undone, and echoes it |
| `move` | `id`, `parent`, `position?` | cycle-checked (400), collision-re-keyed |
| `delete` | `id` | the subtree; an unknown id is a no-op (a retry) |

Rules: every touched block and every insert parent must be inside the page
(403 otherwise, 404 unknown). The page root may only be `set` (a share
editor: its content, never its properties) and is never moved, deleted or
inserted. `parent: "root"` is refused: ops never create pages. A bad op
fails the whole batch and nothing is written.

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

Only touched rows get `updated_at`; the page root is stamped once per batch
(home-feed order and the notes-index fingerprint). A batch tracks the upload
names its ops stop and start referencing. The names it takes up clear their
`upload_orphans` rows in the same transaction. The names it dropped
(`dropped_uploads` on the result; a cut and paste within the batch drops
nothing) go to `upload_gc.schedule`, a check on its own thread, never in
the request ([user_db.md](user_db.md) "Stored files"). The data.db purge
runs only when blocks were deleted, and its library-wide part only when one
of them carried a PDF.

A batch may carry `batch`, the client's id for it, the same on every retry.
The answer to a batch this process already applied for that client is kept
in memory (`_replays`: at most `REPLAY_KEEP` answers for `REPLAY_TTL`
seconds each, holding seq, time and caret; the ops are read back from the
log). A retry gets that answer instead of being applied twice, which the
three-way merge would otherwise do to the same keystrokes. The lookup and
the store happen under the batch's write lock. A restart forgets the
answers; a retry after one is applied again, which the create-if-absent
insert and the merge's "already the text" rule make mostly harmless.

`apply_ops(conn, page_id, ops, actor=, client=, share_scoped=, cursor=, batch_id=)` applies and
commits; `after_commit(ws, conn, result)` does the derived-data work and
publishes; `commit_ops(ws, page_id, ops, actor=)` is both on a fresh
connection — `ws` the workspace id, `actor` the account making the change. Every server-side writer
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
is `ops.delete_page`: the subtree, the page's log rows and a `deleted_pages`
tombstone (`page_id`, `deleted_at`, `actor`; cleared if the id is created
again) in one transaction, so another copy of the workspace can tell
"deleted" from "never seen". Trashing writes the same tombstone, and a later
`delete_page` keeps it. Writers that rewrite a tree wholesale (`PUT
/blocks/{id}/children`, imports into an existing page, the target half of a
cross-page move) log and publish a `reload` instead; a cross-page move's
source page gets a `delete` (`record_ops`). The subtree replace and the
cross-page move each run in one transaction under the write lock
(`blocks_store.write_lock`), so the checks and the writes see one state and
a failure half way leaves the old tree. `blocks_store.delete_subtree` /
`delete_children` start with `DELETE` for that reason: Python's sqlite3
opens its implicit transaction only before a statement that begins with
INSERT, UPDATE, DELETE or REPLACE, so a `WITH … DELETE` would commit on the
spot.

## The op log

`page_ops(page_id, seq, actor, client, at, ops)` in each workspace's `pages.db`
(`db.PAGES_SCHEMA`), one row per applied batch, `seq` counting up per page
(the write lock is taken up front with `BEGIN IMMEDIATE`, so it never
collides). `actor` is the account that made the change (a share editor's own
name), `client` the tab's id, `"ai"` (the agent's tools) or `"meta"` (the
paper-metadata worker's property writes — the one content write opening a
page can cause, [paper_metadata.md](paper_metadata.md)).

A `set` logs the block's whole text, so typing in one long block writes that
text once per flush. The log is therefore bounded three ways per page:
`KEEP_OPS` rows (300), `KEEP_OPS_HOURS` of age (24) and `KEEP_OPS_BYTES` of
payload (2 MB). The bounds are checked every `PRUNE_EVERY` batches (16), and
at once after a batch bigger than its share of the bytes (`ops._prune`).
Only the oldest rows go and the newest always stays, so `seq` keeps counting
from it and a gap is still told by the lowest seq left.

`GET /api/pages/{id}/ops?since=` returns the batches after a seq. It answers
410, and the client reloads the tree, in three cases: the log no longer
reaches back; what follows is more than a catch-up should replay
(`CATCHUP_MAX_BATCHES` 200, `CATCHUP_MAX_BYTES` 1 MB); or the log cannot
continue from `since` at all, because it ends before it or skips the batch
right after it. A backup restore leaves the last case
([workspaces.md](workspaces.md) "Export and backups"). It puts a page's log
from the backup in place, then logs a `reload` one above the highest seq the
live log or the backup had (`log_reload(after=)`). That way a page's seq
never goes back, and a tab that was anywhere before the restore reloads
instead of dropping the next batches as already seen. The restore also tells
the open rooms to reload.

`GET /blocks/{id}/subtree` on a page carries the `seq` its tree reflects,
both read in one snapshot (a batch committed between two separate reads
would be counted but missing from the tree).

## Commit listeners

`ops.commit_listeners` is a list of `fn(ws, client, page_id)` called after
every committed write: a batch (`after_commit`), a page deletion, trashing
or restore, a cross-page move's `record_ops`, a `note_reload`. Two register
at import: an offline copy's engine (`sync_engine._on_commit`, its
sync-on-change) and the notes index (`block_index.page_changed`, from
routers/search.py: the page is re-indexed in the background once it has
been quiet for `QUIET_S`). A listener that raises is logged and never breaks
the write.

## The change feed (`gamma/routers/sync.py`)

`GET /api/sync/changes?since=&limit=` is the workspace-wide view the
per-page log lacks: the pages whose root block was stamped after a cursor,
each with its latest `seq`, and the `deleted_pages` tombstones written after
it, as one time-ordered stream. It exists for anything that keeps a copy of
a workspace in step (a mirror, [mirror.md](mirror.md); a backup merge) so it
can find out *which* pages to look at without walking the library; what
actually changed on a page is still its op log (`seq`,
`GET /pages/{id}/ops?since=`), and a page whose log no longer reaches back
is refetched whole.

It is a hint, not a ledger, and the consumer must be idempotent: while a
walk is paginating the cursor is `<time>|<id>` and strict (nothing repeats),
but a caught-up answer's cursor is the server time minus a 60 s grace, so
the last minute is re-listed on every poll. That covers writers whose
timestamp predates their commit by a moment (`create_page` stamps before
its insert) without a workspace-wide sequence that every writer would have
to append to. A writer must never stamp much earlier than it commits, or
its page falls behind the grace and is never listed: the Zotero and
Markdown-zip imports commit page by page, each stamped inside its own short
transaction. Every writer stamps the page root once per batch — `apply_ops`,
`record_ops` (a cross-page move's source), `log_reload` (a subtree replace,
an import into an existing page, every page a backup restore wrote), the
raw import paths — so a page never changes without the feed noticing. A
replace restore also tombstones the pages it removed, and a merge clears
the tombstones of the pages it brings back.
Deleting a page (`ops.delete_page`) drops its log rows and leaves the
tombstone the feed reports; the tombstone goes when the id is created
again. A page moved to Recently deleted (`ops.trash_page`) keeps its log but
leaves the feed's pages and gets the tombstone, so to a copy it is deleted.
Restoring it clears the tombstone and stamps the root, so the page is listed
again as if created.

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
visitors"). Anything else is closed with 4403 before accept. The handshake
runs these reads (`_socket_access`) and the read of the log position in
worker threads; the room itself only ever changes on the loop.

`collab.revalidate(ws)` runs `peer_access` again for every peer of the
workspace's rooms whenever access there changes: a share updated or stopped
(`routers/shares.py`, a folder share moved or dropped with its folder), a
member re-roled or removed, the workspace's access changed or the workspace
deleted (`routers/workspaces.py`). A peer that lost access leaves the room
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
knock the tab out of its room nor take the room from the others.
Messages:

- server → client: `hello {client, color, seq, peers}` on join; `join {peer}`
  / `leave {client}`; `cursor {client, block, anchor, head}`; `ops {seq, at,
  actor, client, ops, cursor?}` for every applied batch (the sender's own
  included, it filters by client id; `cursor` is the writer's caret in the
  text after the batch, when the POST carried one — the server also stores
  it as the writer's presence); `reload {seq}`.
- client → server: `cursor {block, anchor, head}` only (`anchor`/`head` = -1
  when no editor is open on that block). **Writes never travel over the
  socket**: they are `POST /api/pages/{id}/ops`, so auth and scoping live in
  one place. A dropped socket does not stop HTTP saves; a closing tab attempts
  to flush queued edits with a keepalive fetch.

A peer is `{client, user, name, color, can_edit, block, anchor, head}`; colour
is an index into an 8-slot palette handed out per room (CSS `--peer-N`).
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
immediate retry on the browser's `online`, and `peers` / `me` as React
state. The session owns:

- **the base tree**: what the server is known to hold from this tab's point of
  view. The block tree's transition effect calls
  `commit(tree)`: a load (a fetched tree, marked by App's `loaded()`) makes
  the tree the new base; any other transition is diffed against the base
  (`diffTrees`) and the ops queued. The mark sits on the tree value itself
  (a `WeakMap` of tree → `"load"` / `"remote"`), read once by the
  transition that commits it — never a flag set beside `setBlocks`: such a
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
  hold once the queue has landed. `keepUiFlags` carries only the open editor
  and the folding onto the fetched tree, never the screen's text: a screen
  copy of unsent text would become the base and never be sent. A page left
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
    holds it — its insert was lost — sends that block and its subtree again
    as inserts ahead of the batch (up to `MAX_RESCUES` per page);
  - a `conflict` (the page changed under the batch) drops only the op at
    `index`. The rest goes out again at once as a new batch (text edits
    keep the base they were typed from, so the server merges them), and the
    page is refetched, since the screen still shows the refused change;
  - signed out (401), another account signed in in this browser (the
    X-Gamma-User 409), offline, a server error, 408 or 429: the batch waits,
    id and all, and is retried after 3 s, doubling up to a minute, never
    given up. The `online` event and a socket hello retry at once, and it
    goes out once the person signs in again. Typing meanwhile queues behind
    it;
  - anything else (malformed, not allowed any more): that batch alone is
    dropped, what was queued after it still goes, and the page is refetched.
  While edits wait, a notice stays up (`onSaveNotice(text, "pending")`, a
  "save" pill in App until the edits are saved). A dropped batch gets a
  "rejected" notice that stays 20 s, where the status line alone lasts a
  second. Text over `MAX_CONTENT` (the server's per-block limit) never
  enters the queue. The op goes without it and the base keeps the last text
  that can be saved, so the text is diffed and sent again once shortened.
  The editor stays open on it (`tooLong(id)`), the notice says why, and a
  refetch meanwhile keeps the long text on screen.
- **same-block merge**: a content `set` carries `base`, the text the change
  was made from (`diffTrees` reads it off the base tree; `pushOp` keeps the
  first base of a run of keystrokes). When the server finds the block
  changed since, it applies the edit as a patch onto the current text
  (diff-match-patch with fuzzy context matching): edits to different spans
  both survive, a hunk that no longer fits is dropped and the stored text
  stands for that span. The echoed op carries the merged text and the
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
- **reconciliation**: `inflight` counts queued-or-sent content sets per
  block (text folded into a queued property-only set counts too — `pushOp`
  returns the op it folded into). The content of a remote `set` for a block with one in flight is *deferred* and, on
  the ack, applied only if its seq is higher than the ack's (theirs is the
  newer server value), else dropped (ours is). Its property patch still
  applies immediately, so successive updates to different keys are preserved.
  Other operations apply at
  once — to the base, to the on-screen tree through `onRemoteOps` (a
  `"remote"` transition: no history entry; it is diffed like an edit, and
  since the base already has the ops only an edit of ours rendered in the
  same pass goes out), and to every
  undo snapshot (`blockHistory.rebase`), so undoing your own edit never
  reverts someone else's. A snapshot lacking the target parent of a remote
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
  refetches the subtree with `keepUiFlags` (the open editor and the folding
  survive the swap) and `overlay` (the unsaved edits do). App's refetch
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
keeps siblings sorted by key. UI-only fields (`editMode`, the `collapsed`
flag) never travel; a remote `collapsed` *property* updates the stored value
but not the viewer's own folding — folding stays personal, the stored value
is the default for the next open.

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
  the plain run around it is looked up in the rendered text, and an offset
  in markup that renders as nothing (a link's URL) goes to the end of the
  text before it, one inside math to before the formula. It is re-placed
  when the text, the caret or the view's size changes; a caret that can't be
  placed is not drawn.

## Testing

- `backend/tests/test_collab.py`: op semantics, scoping, the log and
  catch-up, the socket (TestClient `websocket_connect`; sockets need a
  context-managed client), AI-tool and cross-page fan-out. Every socket
  test opens its own page, and sequence numbers are asserted relative to
  the hello's (or the previous ack's) — never as absolute counts — so a
  step added to one test never renumbers the others.
- `backend/tests/test_collab_robustness.py`: conflict codes and the op
  index, a retried batch answered once, create-if-absent inserts, lone
  surrogates, a tab reconnecting on its client id, a stale room, the hello
  counting a batch committed while joining, revoked shares and removed or
  re-roled members closing or re-announcing open sockets.
- `frontend/tests/blockOps.test.mjs`: `node --test tests/blockOps.test.mjs`
  from `frontend/` (pure diff/apply round-trips).
- `frontend/tests/collabSession.test.mjs`: `createCollabSession` over fake
  HTTP, socket and timers — ack/socket ordering and catch-up, content versus
  property reconciliation, the merged text on an ack (landed at once, or
  held while a newer set of ours is queued), retries and their backoff, rejection, navigation
  during a save, presence messages, the caret throttle, reconnect backoff,
  read-only sessions; browser behavior is covered separately below.
- `frontend/tests/collabRobustness.test.mjs`: the refused-batch paths
  (conflict, signed out, refused for good), batch splitting, text too long,
  a batch id across retries, a reload and a return to the page keeping
  unsaved edits, a failed refetch, a revoked socket, and the undo stack
  after a reload or over a move it can't place (`blockHistory`'s
  `observeTree` / `rebaseHistory` without React).
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
  lands once the network is back, a remote delete, a note moved in with a
  reload surviving Ctrl+Z, a failed refresh keeping the page and retrying, a
  highlight made by the other person; `share.mjs` covers the invited editor on a share link and
  the stranger typing through an anyone-with-the-link edit share under a
  renamed display name.

## Limits and next steps

- Queued edits live in memory, not in durable offline storage. The tab asks
  before it closes with edits unsaved. Keepalive saves on tab close are best
  effort and subject to browser limits (64 KB), so an outage followed by
  confirming the close, or a crash, still loses them.
- A remote delete of a block with our text edit in flight applies at once;
  the edit then comes back as a rescue only if our base still held the
  block, else it is dropped with the block (the screen already showed the
  delete).
- Same-block simultaneous typing merges by span (three-way merge above); a set whose content already is the block's text (the same edit sent twice, a retried batch, a clone pushing what it already pulled) merges nothing — patching it in again would double the change;
  two people changing the *same* characters within one save window still
  resolve by server order for that span. If character-exact convergence
  ever matters, the upgrade path is CodeMirror's collab rebase on just the
  open block; not a CRDT.
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
  change feed and the tombstones: [mirror.md](mirror.md). It works from
  trees, not from replaying this log, so a copy that was away longer than
  the log reaches back needs no fallback.

The survey behind this design (OT vs record-level LWW vs CRDT, why the old
snapshot autosave could not be patched) is in
[research/collaboration.md](../research/collaboration.md).
