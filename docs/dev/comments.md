# Comments and the inbox

People leave comments on a note, on a page and on a PDF highlight, reply to
them and resolve them. Backend: `gamma/comments.py` (storage, the rules, the
writers, the sweep) and `gamma/routers/comments.py` (the API); the Inbox,
which tells people what concerns them, is `gamma/inbox.py` and
`gamma/routers/inbox.py`. The research
behind the design is [research/comments-inbox.md](../research/comments-inbox.md).

## Threads

A thread hangs on one block: a note, a highlight (its block) or the page
itself (the page's root block). It is a root comment and the replies that
name it. Its rows are the table `comments` in the workspace's `pages.db`
(`db.COMMENTS_SCHEMA`):

| Column | Meaning |
|---|---|
| `id` | the comment's id, of a block id's shape (`blocks_store.BLOCK_ID_RE`); the client may mint it |
| `thread_id` | the root's id; a root has `thread_id = id` |
| `block_id` | the anchor: the block the thread is on |
| `page_id` | the page the block was on at the last write that placed it; a hint for the purges, never what a read trusts |
| `author`, `author_name` | the writer's account id, and its username at write time (shown once the id no longer names an account) |
| `body` | plain text, with `@username` and `[[id]]` read as in notes ([mentions.md](mentions.md)), at most `comments.MAX_BODY` (20,000) characters |
| `created_at`, `updated_at` | `updated_at` moves on an edit of the text only |
| `resolved_at`, `resolved_by` | on the root only: when and by whom the thread was resolved |
| `deleted_at` | a root deleted while it still has replies: a tombstone, its text gone |
| `orphaned_at` | set when the anchor block is deleted, cleared when it comes back |

Indexes: `block_id`, `(thread_id, created_at)`, `page_id`, and a partial one
on `orphaned_at` for the sweep.

The comments live beside the blocks, as the AI chats do, and never inside
them. Every block reader (the exports, the AI context, search, folder sync,
PDF typesetting, the outliner, the three-way merge, undo) therefore keeps
working without knowing that comments exist.

`comments.threads_for(conn, page_ids, *, state=None)` is the one reader. It
joins each comment to its block (`unified_blocks.page_id`, so a page's own
thread comes along) and leaves out the orphaned ones. A thread is `{id,
block_id, page_id, resolved_at, resolved_by, created_at, last_at, comments}`,
oldest first, and each comment is `{id, thread_id, author, body, created_at,
updated_at, deleted}`. People are named by username: `author` falls back to
`author_name`, and `resolved_by` to `""`, once the account is gone. A
tombstone reads `deleted: true` with an empty body. `last_at` is the newest
of the thread's writes and its resolution. `state` is `open`, `resolved` or
`all`. The caller decides which pages may be read; a page in Recently
deleted is no page of the library.

A comment write never calls `touch_page`. It moves no change feed, starts no
mirror round and does not reorder the home library. Nothing in
`unified_blocks` changes, so the suite's drift checks
(`tests/conftest.py`) hold.

The workspace's migration needs no step of its own for the table: it is
additive, and `db._open_ws_db` applies `PAGES_SCHEMA` to every `pages.db` it
opens, as do `seed.create_workspace_files`, `ws_backup._normalize_copies`
and the Gamma export's in-memory database. Step 40 (`comments_inbox`) is a
global step only ([migrations.md](migrations.md) rule 2), so
`migrations.workspace_format()` stays where it was and clones and
publications keep syncing across the upgrade.

## Who may comment

| | read threads | comment, reply | edit | delete | resolve, reopen |
|---|---|---|---|---|---|
| workspace owner | yes | yes | own | any comment | yes |
| editor | yes | yes | own | own | yes |
| viewer | yes | yes | own | own | yes |
| signed-in account through a share (view or edit) | the share's pages | yes | own | own | yes |
| link visitor (no account, or a guest account) | the share's pages | no | no | no | no |
| guest account | in its own workspace | yes | own | own | yes |
| integration token, read scope | yes | no | no | no | no |
| integration token, write scope | yes | yes | own | own | yes |

`auth.require_commenter(request, page_id)` decides every comment write. It
resolves the workspace as a read does (`resolve_ws`), refuses a link visitor
(403 "sign in to comment": there is no account to sign the comment with) and
a token without the write scope, then confines a share request to the
share's pages (`share_scope(request).allows_page`). For an edit, a delete or
a resolve, the page is the one the comment's block is on now
(`comments.page_of`). The router reads it before the write's lock and hands
it to the writer, which answers 404 when the comment is no longer on that
page by the time it holds the lock. The owner's delete of someone else's
comment checks `ws_role(request) == "owner"` for a session, not a token: an
integration token deletes its own comments only, whoever owns it. An owner
who opens their own page through a share link (`?share=`) is a share
visitor there and deletes their own comments only. Reads use `resolve_ws`
and `share_scope`, as for the page itself.

A share visitor sees the usernames of the people who wrote or resolved a
comment on the shared page, as presence already shows them.

On a hosted server in read-only mode, comment writes are refused like any
other content write ([hosted.md](hosted.md)).

## The API

Every handler is a sync `def`: it opens the workspace's `pages.db`, and a
write waits on the workspace's write lock. The writers in `gamma/comments.py`
check and write under `blocks_store.write_lock` and commit. Bodies go through
`ops.StorableBody`, so half an emoji is stored as U+FFFD and a `NaN` is
refused with a 400.

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/pages/{page_id}/comments?state=open\|resolved\|all` | | `{threads}`; 404 for a page in Recently deleted or gone, 400 for another `state` |
| POST | `/api/pages/{page_id}/comments` | `{id?, block_id, body, thread_id?, client?}` | 201 `{comment, thread}` |
| PATCH | `/api/comments/{id}` | `{body, client?}` | `{comment}` |
| DELETE | `/api/comments/{id}?client=` | | `{deleted: true}` |
| POST | `/api/comments/{id}/resolve` | `{resolved, client?}` | `{thread}` |

- **Create.** `block_id` is a block of the page or the page itself (400
  otherwise). With `thread_id` the comment is a reply: the id must be a root
  on that same block (400 otherwise). A text that is empty or only spaces is
  a 400, and one past 20,000 characters a 413. `id` is the client's id for
  the comment. A comment of that id by the same author on the same page is
  answered as stored and nothing is written again, so a retried request is
  harmless; a 404 when that comment is hidden since. An id that another
  author's comment holds, or one of the author's comments on another page,
  is a 409, so a retry never reads a thread outside the page the request
  was authorized for.
- **Edit.** The author only (403 otherwise). An edit that leaves the text as
  it was keeps `updated_at`.
- **Delete.** The author, or the workspace's owner (signed in, not through
  a token or a share link). A root that still has
  replies becomes a tombstone. Deleting the last reply under a tombstone
  takes the tombstone with it, so a thread always shows a comment.
- **Resolve.** On a root only (400 for a reply). `resolved: false` reopens.
  Anyone who may comment on the page may do either.
- A comment that is hidden answers 404: its block was deleted (orphaned),
  or its page is in Recently deleted.

After every write that changes something (an edit to the same text, or a
resolve of a resolved thread, changes nothing) the router publishes
`{t: "comments", block_id, thread_id}` to the page's room, to every peer but the writer's tab (`client`,
`collab.publish(..., exclude=client)`). It then hands the write's event to
`comments.after_write(ws, event)`. The event is `{kind, actor, page_id,
block_id, thread_id, comment_id, ...}`, where `kind` is `create`, `reply`,
`edit` (with `before`), `delete` (`comment_ids`), `resolve` or `reopen`
(`thread_author`), or `sweep` (`comment_ids`). The router adds `via`, the
share token the writer came through (`auth.share_via`; `""` for a member).
It is the one place where the derived work hangs: the Inbox ("The inbox"
below). It never raises. Create, edit and resolve read the thread they
answer with before their commit, under the write lock, so a batch that
deletes the block in between cannot leave a stored write without its
answer or its event.

## Lifecycle

A comment follows its block. The block writers call the hooks inside their
own transactions: `comments.orphan` (the blocks were deleted),
`comments.place` (the blocks live on this page now: their comments' `page_id`
follows and `orphaned_at` clears) and `comments.purge` (the page goes for
good). For a writer a share confines to its pages (an edit share's
visitor), `place` brings back only the comments last placed on this page
(`any_page=False`): an id taken from a block deleted on another page shows
none of that page's threads. A member may read every page, so a member's
insert takes the block's threads whichever page they were on (a cut and
paste).

| Event | What happens to the comments |
|---|---|
| a block deleted by an op (`ops._Batch.delete`) | its subtree's comments get `orphaned_at` in the same transaction and are hidden |
| an undo or an AI revert inserts the block again under its id (`_Batch.insert`) | `orphaned_at` cleared, `page_id` refreshed: shown again (a share editor's insert: only the comments this page held) |
| `PUT /blocks/{id}/children` | the old children that did not come back are orphaned; the rows written are placed, as for an insert |
| seven days after `orphaned_at` | deleted by `comments.sweep` |
| a block moved within its page | nothing |
| a block moved to another page (`ops.move_across_pages`) | the subtree's comments take the new `page_id` |
| an insert that pulls a block out of a page in Recently deleted | that block's comments take the new page; the rest of the subtree the trashed copy loses is orphaned |
| a page to Recently deleted | the rows stay, hidden: a read finds the page's root under `trash` |
| a page restored | shown again |
| a page deleted for good (`ops.delete_page`: Delete permanently, Empty, the 30-day purge, a share host's delete) | its blocks' comments go, and so do its orphaned ones whose block is gone; a thread on a block that lives on another page now stays |
| `blocks_store.create_page(block_id=)` over a copy in Recently deleted | that copy's comments go with its blocks |
| a backup restored in place | the table is copied with the rest |
| a backup merge, a Gamma import, "Add to my library" | copied with the pages they add (below) |
| a clone, a publication, the share host | not synced ([mirror.md](mirror.md) "What travels") |

**The sweep.** `comments.sweep` runs at startup and hourly in the app's
lifespan (`app.py`), beside `trash.sweep`, not in `db_maintenance`. It walks
`workspace_ids()`, deletes the comments orphaned more than
`ORPHAN_KEEP_DAYS` (7) ago and hands each workspace's deletion to
`after_write` as a `sweep` event. Like the trash's sweep, it remembers each
workspace's file stamp (`trash.files_stamp`) and its next expiry, so a
workspace whose files have not changed is not opened again until something
in it is due.

**Copies.** `comments.rows_of_pages(conn, page_ids)` gives the comments
shown on pages as stored rows (roots before replies, `page_id` as the blocks
have it now), and `comments.copy_rows(dst, rows, blocks=)` inserts them
into another `pages.db`. A comment id that the destination already has is
minted afresh, and its thread's replies follow the new id.

- The Gamma export (`routers/export.py` `_GammaBuilder`) copies the threads
  shown on the exported pages. The authors' ids mean nothing on another
  server, so `author_name` is what shows there.
- The backup merge (`ws_backup._merge`) copies an added page's comments.
  `_fresh_ids` returns its remap (`{the backup's block id: the id it gets}`),
  and `block_id` follows it. On the untrash path, where the backup's page
  replaces a copy in Recently deleted, the trashed copy's comments are
  deleted first. The merge answers `comments_added`, and the review
  (`_review_import`) gives each page entry a `comments` count. A page the
  workspace has is skipped, with its comments, so merging the same backup
  twice adds nothing.

## The inbox

Each signed-in account has one Inbox across every workspace and share it
can reach. Backend: `gamma/inbox.py` and `gamma/routers/inbox.py`. The
items are rows of `users.db` `inbox_items` ([user_db.md](user_db.md)), one
per event and recipient, written when the event happens. A notes-index
scan could find a block that says `@alice`, but not who typed it or when,
and it would notify again on every edit.

| Kind | When | Who hears | Written by |
|---|---|---|---|
| `mention` | a note gains `@name` | each person newly named | `ops._Batch` keeps the people each text it touches named before the batch and names after it (`mentions.people_in`: person mentions outside code; a `(remind @x)` names no one; an insert's text had none, a deleted block's has none). `mention_changes` diffs them across the whole batch, so a name that moves from one note to another (Enter before `@name`, a join) is no new mention. The result carries `mentions_added` (`[{block_id, users, text}]`), `mentions_moved` and `mentions_dropped` (below), and `ops.after_commit` hands it to `inbox.from_ops` |
| `comment_mention` | a comment's text gains `@name` (a new comment, a reply, an edit) | each person newly named | `comments.after_write` → `inbox.from_comment` |
| `reply` | a reply is posted | the authors of the thread's other comments and the people they mention, less those the reply itself mentions | the same |
| `resolved` | someone resolves a thread another person started | the thread's author | the same |
| `shared` | people are invited to a page or folder share | each new invitee, with the share's token | `routers/shares._invite`, from both `_create` and `_update`: it compares the old and the new `share_users`, keeps `added_at` for the people who stay, and `_tell` writes the items after the commit |
| `invited` | an account is added to a shared workspace | the new member | `workspaces.set_member` (which returns whether it inserted the membership: a role change tells no one), `invite_cloud`'s linked branch, `claim_pending_memberships` for each workspace joined, from its inviter, and `workspaces.create` for an owner someone else made a shared workspace for (an admin's `POST /api/workspaces` with an owner) |

No one hears of a batch that an offline copy wrote (client `sync`: the
names were typed on the other server, so mentions typed in a clone tell no
one here), of a mirror's round (actor `mirror`), or of a link visitor's
edit (no account to name). The AI's batches carry the chat owner's id, so
they count as the owner's. A retried batch is answered from the op log and
never reaches `after_commit`, so a retry tells no one twice. A reopen tells
no one.

**Names that move or go.** `inbox.from_ops` follows the rest of the
batch's diff whoever wrote it. `mentions_moved` (`[{user, from, to}]`: a
person some texts of the batch lost and another gained) moves the
person's items not archived from the `from` blocks to `to`, so a split
before the name leaves one unread item, on the new block. `mentions_dropped`
(`[{block_id, users}]`: a text still there that names people no more, whom
no text of the batch gained) archives their item on that block at once and
nudges them. The editor saves a note after a pause in typing, the @ menu
open or not, so `@ben` on its way to `@benjamin` is saved, and ben's item
goes as soon as the longer name is. The bell's count drops with it,
though a system notification already shown stays. A deleted block's
items are left to the settle, so an undo before it writes nothing new.

**Writing.** Every producer calls `inbox.emit(kind, recipients, actor, ws,
page_id=, block_id=, thread_id=, comment_id=, snippet=, via=)` after its
own commit. It catches and logs every error and never raises, as the commit
listeners do (`ops.notify_commit`), so a stored note or comment never
answers an error because users.db was busy. It leaves out the actor, guest
accounts and accounts that are gone. Of the first `REACH_CANDIDATES` (100)
of the rest, it writes an item for the first `MAX_RECIPIENTS` (25) that
`reach` admits, so accounts named first who cannot open the page take none
of the places. A `mention` of someone who has the same block's mention
unread and not archived writes nothing more while it stays so: a name typed
again, or an undo of the block's deletion before the recipient's listing
settled the item (after the settle, the undo writes a fresh item). The
check and the rows go in one users.db write transaction (`BEGIN
IMMEDIATE`), so two batches naming the same person in the same block at
once write one item. Each recipient's open page sockets then get a nudge
(below). The `snippet` is at most 200
characters of the text at the time: the note, the comment, the root
comment for `resolved`, the page title or folder name for `shared`, the
workspace's name for `invited`. A `[[link]]` in it is written as the words
it shows (`inbox._named_refs`: a page's title, a note's first line, read from
the workspace's pages.db), so the Inbox never shows a bare id; a link to
nothing found stays as written.

**Reach.** `inbox.reach(ws, page_id, user_ids, via="")` answers
`{user_id: share_token | ""}`. It gives `""` to an account with a role in
the workspace (`workspaces.role_of`: a member, or anyone a public
workspace lets in). Otherwise it gives the token of a share of the
workspace whose scope reaches the page (the page's share, or a folder
share holding it) and whose audience admits the account
(`auth.share_access`), but only a share that names the account among its
invitees, or `via`, the share the event came through. `via` is the
writer's own `?share=` token (`auth.share_via`, passed by the ops and
comment routers through `ops.commit_ops`, `after_commit` and the comment
event), or a share's own token for its invitees (`routers/shares._tell`).
An anyone-with-the-link or any-signed-in-account share the account was
never given is never handed out: a visitor through a view-only share who
mentions an account of their own must not hand it the page's edit link.
Anyone else is left out, so a non-member mentioned on a page that is not
shared with them hears nothing. Without a page (an invitation, a folder
share) the role counts, and `via` when its audience admits the account.
So a signed-in account invited to a share hears of the mentions, replies
and resolves on that page, with `share_token` set; the app opens such an
item through the share link.

**Reading.** `GET /api/inbox?filter=unread|all|archived&before=&limit=`
lists the account's items newest first. Each item has `{id, kind, actor,
workspace_id, workspace_name, page_id, page_title, block_id, thread_id,
comment_id, share_token, snippet, at, read_at, archived_at}`. `actor` is a
username (`""` once the account is gone). `page_title` is the page's title
now, or the folder's name for a folder share, while the account may still
read it: always for a member (a trashed page is in its Recently deleted),
else while its way in still admits it (`_entitled`). An archived item of a
page the account lost answers `""`, so a page renamed after a share
stopped never shows its new name to the former invitee; the snippet keeps
the words of the moment. `share_token` is the way in as the settle last
found it (below). `all` is everything not archived. `before` is the `at` the last listing ended on. A listing never
splits the items of one moment, so it may run past `limit` (at most 100).
`more` says whether older items remain. `GET /api/inbox/unread` is a count
on the partial index of unread rows, with no settle, so until a listing
runs it also counts items the listing will archive (a mention whose block
is gone, a page since trashed, a share since stopped). Every listing answer
carries `unread` after its settle, and the bell takes the count from it
whenever one arrives.
`POST /api/inbox/mark {ids? (at most 100), all?, read?, archived?}` marks
items read or unread, archived or back. A stamp already set is kept, and
the answer is `{unread}`.

**Settling.** Before it lists, `inbox.listing` archives the account's items
that lead nowhere any more. That is an item whose page is in Recently
deleted or gone, whose block or comment is gone, a `mention` whose note no
longer names the account (an edit by any path: a subtree replace, a
restore), or whose page the account can no longer reach (the folder share,
for a folder share's item; the workspace, for an invitation). Reach is
asked again with the item's token as `via`: an item whose way in is
another now takes the token `reach` gives, `""` once the account is a
member, or the token of a share that names it after the old one was
stopped and made again. A `shared` item tells of its own share, so it
settles when that share no longer lets the account in, whatever other
share does. A restored page leaves its items archived.
Only the workspaces whose pages.db was read whole count: one that cannot
be read keeps its items, and a workspace that no longer exists archives
them. The archive goes in the same users.db write, so `unread` and the
Unread tab agree. Like the reminders' listing (`mentions._settle`,
[mentions.md](mentions.md) "Reminders"), this is a read that writes: the
rows are the account's own bookkeeping, not stored content.

**Cleanup.** A deleted comment's items go with it (the `delete` event), and
so do the items of the comments `comments.sweep` deletes. A workspace's
items go with it (`workspaces._delete_rows`), an account's with it as the
recipient (`delete_account_workspaces`), and a member's items in a
workspace go when the member is removed or leaves (`remove_member`).
`inbox.sweep` runs at startup and daily in the app's lifespan
(`SWEEP_INTERVAL_S`). It deletes read items older than 90 days and items
archived more than 30 days ago, and archives unread items older than 180
days.

**Who has one.** The three endpoints are for a signed-in account's session
only: a guest and an integration token get 403 (as `/api/notices` refuses
them), and no session gets 401. A guest is never a recipient. Marking is
account bookkeeping, so `/api/inbox/` is in `hosted.READ_ONLY_ALLOWED`:
a read-only hosted server still takes it, while comment writes are refused
like any other content.

## Live delivery

| Where the recipient's tab is | How it learns | Latency |
|---|---|---|
| a page open, in any workspace or share | `{t: "inbox"}` through `collab.publish_account` | under a second |
| the home library or Settings | `GET /api/inbox/unread` every 30 s while visible, and on window focus | up to 30 s |
| the Inbox panel open | the same poll every 5 s, plus the nudge | under 5 s |
| a hidden tab | nothing until it shows again | on return |

`collab.publish_account(user_id, message)` finds the `(room, peer)` pairs
whose `peer.account` is the account, in any room of any workspace (a share
link's included), as `revalidate_account` does. It then schedules one
coroutine on the socket loop that sends to them and drops a peer whose send
fails, as `Room.broadcast` does. The message is content-free: it says only
that something changed, and the tab asks `/api/inbox` for the rest, so a
socket admitted to one page learns nothing about another. A tab on the page
where a comment was written gets both `comments` and `inbox`, and the
client coalesces them (it debounces nudges by 300 ms). The account socket
`WS /api/ws/account` is reserved for later ([collab.md](collab.md) "Rooms
and the socket").

## In the app

### Comments

The code is `frontend/src/comments/`. Two modules load with the app:
`comments.js` (pure: folding, ordering, the changes a write makes before
the server answers, the thread link; `tests/comments.test.mjs`) and
`useComments.js`. Two surfaces load on first use, through `lazySurface`
([frontend-refactor.md](frontend-refactor.md) "Lazy boundaries"):
`ThreadPopover.jsx` (with the composer, `CommentComposer.jsx`) and
`CommentsPanel.jsx`. `comments.css` holds every rule, design tokens only,
and loads with the app, since the rows need the control's rules.

**The page's threads.** App runs one `useComments` for the open page. It
reads `GET /api/pages/{id}/comments?state=all` when the page opens, and
again when the page's socket says `comments` or says `hello` after a
reconnect: `collabSession.js` hands both to its `onComments` option
([collab.md](collab.md)). One request is out at a time, and nudges that
arrive meanwhile make one more after it. `refresh()` resolves with what
the request it waited for answered (`{pageId, threads}`, or null). A page
whose comments answer 404 or 403 has none, and `readable` turns false. The
threads fold into
`threadsByBlock` and `countByBlock` (open threads per block). Both Maps
keep their identity while they read the same (`keepIfSame`), and a refetch
that changed nothing keeps every thread object (`keepThreads`), so it
re-renders no row. The reader's own writes show at once. A new comment gets
its id here (`makeId`, a block id's shape), so a retried request is
answered as stored. Every write sends this tab's `client`, so the room's
nudge skips it. A refused write puts the threads back as they were while
the list is still the one the change made (a refetch or another write
since leaves it), says so in the status line, and refetches. A refused
comment gets its text back in its field, and a refused edit keeps its field
open with the new text.

**Where the controls show.** App's `commentsOn` is `!publicPage &&
comments.readable`. Without it there is no row control (`rowProps.onComment`
is null, which also hides the handle menu's Comment and `block.comment`), no
title-row control, no Comment in the highlight menu, no popover and no
Comments window (its View-menu row, the phone More sheet's row,
`winVisible.comments`, and `app.toggleComments`, whose `when` reads
`commentsOn` from `appCmdRef`): a page
host's publication (comments stay off the share host in v1) and a page
whose comments the reader cannot read. A refetch that answers before the server has a new comment shows
it again from the writes still on their way.

**The row control.** `BlockRow` (`editor/BlockTree.jsx`) draws a `ctlBtn`
with `MessageSquareIcon` after the row's body, beside the ×. It shows the
block's open count in the accent while there is one. Otherwise it is
revealed with the row (hover, the focused row, its own `:focus-visible`),
takes no press while hidden, and is always there under `(hover: none)`.
While its popover is open it stays shown (`.on`) with `aria-expanded`:
`rowProps.commentOpenFor` names the block, and `BlockTree` hands each row
its own boolean, as it does the count. It
needs `rowProps.onComment`, not editing rights, so a viewer and a signed-in
share visitor start threads from read-only rows. It is a button, so the
row's mousedown leaves it be, and it stays while the row is edited.
`BlockTree` gives each row its own count from `rowProps.commentCounts`, as
it does the ref labels, so a new count re-renders that row alone. The
pointer entering it, or the focus, fetches the popover's module.

**The thread popover.** App renders one instance through a portal to
`body`, keyed by its block. It hangs from the element that opened it, or
the block's row control (`data-comment-for`), placed from its own measured
size before it is painted (`useFloatingPosition` with `follow`, `flipY`
above the anchor when there is no room below; [ui-design.md](ui-design.md)
"Menus and popups open in place"). It is a `.popover` at `--z-popover`, at
most `min(70vh, 640px)` tall and 380 px wide (the window's width less
16 px on a narrower screen). One
capture-phase pointerdown closes it, except one inside it, on its anchor
(which toggles it), or in `[data-popover], .ctxMenu, .refPopup,
.slashMenu, .mathPreviewTip, .latexAcPopup` (its "⋯" menu, a field's `[[`
picker, @ menu and "/" menu, its LaTeX preview and completion, which draw
above it at `--z-refpopup`). Escape closes it and gives the focus back to the
control. Closing it loses no unsent text (below). It lists the block's open threads oldest first, then a resolved
one only when it was asked for (a deep link, the window's Resolved tab) or
"Show n resolved threads". A new request to write on the block whose
popover is open (the handle menu's Comment, Ctrl/⌘+Shift+M) opens "New
comment" in it and puts the focus there. Each thread shows its comments and a
reply field. Its first comment's author line carries the thread's Resolve
(a check mark) or Reopen button beside the "⋯"; a resolved thread says
who resolved it at its foot, where the reply field was. A comment shows its author's avatar (`PeerAvatar`,
`nameColor`), name, time and text rendered as a note is (`BlockMarkdown`
with `blockId="comment:<id>"`, so math, @people chips and `[[links]]` work,
a link to a block outside the page getting its label from App's reference
cache, which `onFetchRefs` fills when the threads arrive; the
popover hands it a link handler that keeps its identity and calls App's
latest, since `BlockMarkdown`'s memo leaves handlers out), and
"edited" once changed. A popover draws its first 2,000 characters of text
at once (`comments.js` `laterComments`, a `$$…$$` equation counting 100
more), counted from where it opens: from its top, or, through a deep link
or an Inbox jump, from the thread it scrolls to, then the threads below
it, then those above it, the nearest first. It draws each comment after
them as it comes within a screen of the view (an IntersectionObserver on
`.commentPopBody`, the render in a transition), with a box of about its
height (`estimatedLines`) in its place until then. A comment written or
shown after the popover opened is drawn at once. A thread of twelve long
comments full of math (300 equations) otherwise built every equation
before the popover showed: about 900 ms in one task, against about 80 ms
now. Its "⋯" (`MenuButton`) has Edit and Delete for its
author (Delete for the workspace's owner too) and Copy link. "New comment"
starts another thread. A link visitor without an account reads, with "Sign
in to comment" (the share's sign-in gate) in place of the fields.

**The composer.** The field is the note editor, `BlockCmEditor`, with its
send button. It has a note's typing aids, from the modules a note row
uses: `[[links]]`, @people and date chips, inline marks and math drawn in
place (App's MentionContext as `mentions`, `labelsFor(text)` as
`refLabels`); "[[" opens the note's link picker (`editor/useRefPicker.js`:
pages by title, then blocks from `GET /api/block-search` without the
thread's own block; a picked row writes `[[id]]` and caches its label
with App's `onCacheRef`, and "]]" after a page's exact title links it);
"@" opens the note's @ menu at the caret (`editor/useMentionMenu.js`:
people, dates and page links, no "Remind" rows, since the reminder scan
reads notes only, and no "Invite…"); inside `$…$` and `$$…$$` the live
preview, the `\command` and `\begin{}` completion, the Tab hop and the `$`
and bracket pairing of [latex_editing.md](latex_editing.md)
(`editor/useMathUi.js`); "/" opens the note's command menu
(`editor/useSlashMenu.js`, the hook a note row uses: typed only, never
inside math or a code fence). A comment offers the commands that write
text: headings, to-do, the lists, quote, callout, divider, the two
equations, table, code block, Mermaid diagram, today's date, "Link to
note" (which opens the field's own `[[` picker), highlight and the colours.
The commands that make a page or a sheet, upload a picture or embed a note
`need` an ability the field does not pass (`slashCommands.js`
`filterSlashCommands`' `can`: the field passes none, a note row passes
`image` and `embed` always, `newPage` and `sheet` when App gives it their
handlers). A command's text replaces the field's in one edit, which the
field's undo takes back. App hands the pickers `links` (`pages`, the open
page as `rootId`, `lookupBlock`, `refCache`, `onCacheRef`) through the
popover. Left out: the outliner's keys, "Paste as", image and file upload,
sheets, embeds, presence carets and object drags. The keys:
`sendsOnShiftEnter` and `composing` (`chat/enterKey.js`) decide Return.
Enter breaks the line as in a note, continuing a list or a quote
(`mdLines.enterPlan`) outside a code fence or `$$…$$`; Shift+Enter and
Ctrl/⌘+Enter send. On an on-screen keyboard Return breaks the line, Shift
or not, and the button sends (Ctrl/⌘+Enter still sends from a tablet's
keyboard). An edit's Save follows the same rule. The send button's hover
title names the key (`"{label} ({hint})"` with `chordLabel("Shift-Enter")`:
"Comment (Shift+Enter)", "⇧↩" on a Mac; on an on-screen keyboard
`chordLabel("Mod-Enter")`, the key that sends there); its accessible name
is the plain action. While a picker, the "/" menu or the completion is open it takes ↑ ↓
Enter Tab and Escape first. The field keeps its own undo history (`ownHistory`), so Ctrl/⌘+Z
stays out of the page's block history; F2 stays with it too. The history
starts afresh when the field's text is set from outside (emptied on
sending, put back when a send fails), so an undo never brings back part of
a sent comment. The field sits in one rounded box (`.commentBox`, which
takes the focus ring) with the round send button at its right, the chat
composer's `chatCircleBtn` at the compact size of a line of text
(`--ctl-icon-btn-xs`, 24 px, with a 12 px arrow), as Notion has it: on the
text's line while the text is one line, at its last line once it wraps. The text is in the
rendered comment's type and grows up to 40vh, then scrolls. The field is focused a frame after
mounting, since the popover stands hidden until it is placed and a hidden
field takes no focus. An edit's field has Cancel and Save in a bar under
its box (where the length warning shows too), and Escape cancels it. It
closes once the server has the edit. Unsent text is kept per field for as
long as the tab lives, in a module-level map of `CommentComposer.jsx`
keyed `new:<block>`, `reply:<thread>` and `edit:<comment>` (`draftKey`):
a press outside the popover, Escape, a second press on its control or
another block's comments close it, and the text is back when it opens
again. A popover opens "New comment" when its block has a draft, and a
comment with an unsaved edit opens in its edit field. Sending empties the
field and its draft; Cancel drops an edit's. The field takes the focus a
frame after mounting only when it does not have it already, so the caret
of a field focused meanwhile stays where it is.

**Other ways in.** The ⋮⋮ handle menu's Comment. The block command
`block.comment` ("Comment on this note", Ctrl/⌘+Shift+M; no `edits`,
since a comment changes no note; [hotkeys.md](hotkeys.md)), which the
command palette offers on a read-only page too: the palette leaves out
only the `edits` commands there, as the keys do. The
highlight menu's Comment on the PDF. The selection popup's Comment (below).
The page title row's control, for the
page's own thread (on its root block), which share visitors get too.

**Comment on a selection.** The PDF selection popup (`PlainTip`) offers
Comment, the first of its tools, where the reader may comment on the page
and annotate it (`selComment`: `commentsOn && canComment`, and the
viewer's `onSelectionFinished`, which a read-only page leaves out). A
press makes the highlight by the swatches' path (`handleSelConfirm` →
`onSelectionFinished` with `{comment: true}`), in the palette's first
highlight colour, without opening its note for typing (`addHighlight`'s
`edit: false`). `commentOnNewHighlight` then waits for the highlight's
first rect to be drawn and opens the thread popover from it with "New
comment" focused, as the highlight menu's Comment does. App keeps that
highlight in `freshCommentRef` while its popover is open; when the popover
closes, or another block's takes its place, a highlight with no note and
no thread (`abandonedCommentHighlight`, `comments/comments.js`) is deleted
through the tree, as the highlight menu's Delete does: an abandoned
comment leaves no mark, as in Notion. An unsent comment does not keep it.
Nor does it leave an undo step: when the deletion takes the tree back to
the newest undo entry's (`blockHistory.takesBack`, nothing else edited
since), it is made with `foldBlocks`, which takes the making's entry away
as a text box left empty does ([text_boxes.md](text_boxes.md)); otherwise it is an
edit of its own. `freshCommentRef` lives in memory, so a full page load
while that popover is open (a reload, a closed tab) keeps the empty
highlight; an in-app page or tab switch closes the popover and deletes it.

**The PDF.** A highlight with open threads gets the note badge
(`NoteBadge`) whether or not it has a note, drawn in the accent
(`.hasThread`). Its tip gives the note, then the count. A highlight with
open threads and no note (`commentOnlyHighlight`: no text, no child note)
is drawn as Notion draws a commented passage: a lighter wash of its colour
over a 2px line in a strong tone of the same hue (`--hl-dark-line`, which
a flipped page draws too); a linked one keeps the link's blue line, and
one with a note, or with no open thread, its plain fill. A click (a tap
too) jumps as before and opens the thread popover from the badge
(`PdfViewer`'s `onHighlightThread(id, badge)`). The count rides on the
highlight objects (`threadCount`), which the viewer's per-page memo
compares beside `hasNote`.

**The Comments window.** Dock window `comments` (`renderWindow`, a
`LazyBoundary` whose fallback is the empty `DockWindow`), shown while
`!commentsHidden && !!focusedBlockId && !homeMode && commentsOn`. It starts closed. The
View menu's Comments row and `app.toggleComments` show it, and on its
first showing it goes last in the right-hand column when no slot holds it
(`moveWindow("comments", "right", 99)`). On a phone it is a panel over the
page, from the More sheet's Comments row (no bottom tab of its own). Its
grip is the guide anchor `comments.grip`. It lists the page's threads,
Open or Resolved (a `Segmented` whose labels carry the counts), the latest
activity first, each with what it is on: the highlight's quote, the
note's words, or "This page". Its lines name a `[[link]]` by its label
(`anchorSnippet`'s `labelOf`: the page's blocks, then App's reference
cache), not by its id. A click scrolls to the anchor, flashes it
and opens its popover at the thread.

**Links.** Copy link gives `?page=&block=&thread=` (no `block` for the
page's own thread) with the workspace, or with `share=` in a share view.
App reads `thread` at boot beside `block` and `page` and opens the page as
for `block`; a folder share opens the folder's page that `page` names (else
`block`, else the listing). The thread waits in `pendingThreadRef` as
`{threadId, pageId}`. The first page whose threads load takes it: on its
own page the popover opens at that thread ("That comment thread is no
longer on this page." when it is gone), and any other page drops it.
`openThreadAt(threadId, blockId, pageId)` does the same for a thread in
this workspace, for the Inbox; on the open page it reads the thread from
the threads in hand, or from its own `refresh()`. Each ends in
`showThread`, which takes a note's thread to the notes with `showInNotes`:
on a phone a page with a PDF opens on its PDF tab, and the notes panel
comes up so the note shows under its popover. A highlight's thread stays
on the PDF.

**Vocabulary.** A thread is called a comment in the interface ("3
comments", "Comment on this note"); a highlight's own text is its note.

### The Inbox

The code is `frontend/src/inbox/`. Three modules load with the app:
`inboxText.js` (pure: a sentence per kind, the snippet, the days, where a
click goes, the rings, the changes a mark makes before the server answers;
`tests/inboxText.test.mjs`), `useInbox.js` and `InboxButton.jsx`, which
imports `inbox.css` (design tokens only). `InboxPanel.jsx` loads on first
use through `lazySurface`, when the pointer or the focus reaches the bell.

**The count.** App runs one `useInbox`, on for a signed-in account that is
not a guest, outside share views. It asks `GET /api/inbox/unread` at load,
every 30 s while the tab shows, on window focus and when a hidden tab
shows again. While the panel is open it asks the listing itself every 5 s
instead. The page socket's `inbox` message reaches the tab on any page:
`collabSession.js` hands it to `onInbox`, which `usePageCollab` turns into
the window event `gamma:inbox`. `useInbox` takes a nudge, a focus and a
tab showing again 300 ms after the last of them, as one request: a comment
on the open page also sends `comments`, and a return to the tab fires both
focus and `visibilitychange`. Every listing (the rings' one too) and every
mark answers `unread`, and the bell takes the count from each of them: the
bare count still includes items the listing archives on its way. An
answer to a request sent before the latest mark, or sent while a mark was
on its way (the server may have answered it before storing the mark), is
dropped, so a poll never undoes a mark; a count or listing dropped that way
is asked again once the marks have settled. A poll that changes nothing keeps the held
listing, so the open panel does not render again every 5 s.

**The bell.** `InboxButton` sits in `topbarActions` before the account
button, in a `data-popover="inbox"` anchor, with the guide anchor
`header.inbox` and `BellIcon`. Its `.inboxBadge` (after `.navBackBadge`) shows the unread
count, "9+" past nine (`shortCount`); the badge is hidden from screen
readers, and the button's name and title carry the count ("Inbox — 3
unread"). On a phone the button is not drawn: the More sheet has an Inbox
row with the count as its `trailing`, and the More button shows the
running spinner, then the inbox count, then the notice dot, with the count
in its title.

**The panel.** A header popover with Unread, All and Archived (a
`Segmented`), Mark all read, and the items grouped by day (Today,
Yesterday, then the date). A row has the actor's avatar, one sentence
("ann replied to a comment on Draft"), the note's or the comment's words,
the workspace's name when it is not this one, and the time. Its actions,
shown on hover and focus (always under a touch screen), mark it read or
unread and archive it or move it back. Items load when the panel opens, 50
at a time, with Show older. A mark shows at once and takes the item out
of a tab it no longer belongs in. A refused one puts back the rows and the
count it changed, unless a later mark has changed them since, and is put
right by a fresh look once the marks have settled. Each tab has its own
empty state.

**The jump.** A click marks the item read, closes the panel and goes where
`inboxJump` says. An item of this workspace opens in place: with a thread,
`openThreadAt(threadId, blockId, pageId)` opens the page at its block with
the thread's popover (comments, "Links"); without one, `openBlockLink`
scrolls to the note. An item reached through a share navigates to
`/?share=<token>&page=&block=&thread=`, and one of another workspace to
`?ws=&page=&block=&thread=` (a folder share's to `?ws=&folder=`); App
reads `thread` at boot. An invitation
switches to its workspace. A folder shared with a member opens the folder
in the library. The mark goes with `keepalive`, so a navigation never cuts
it off.

**System notifications.** The browser pref `inboxSystemNotifications`
(Settings → Appearance › Interface › Inbox notifications; off by default;
not shown to a guest account, which has no Inbox: `hasInbox` in the
settings value)
rings a fresh unread item once per browser as a system notification, as
reminders do. Turning it on asks the browser's permission, inside the
click. When the count rises, or a nudge comes while the panel is open, the
tab reads the newest ten unread items. Each one of the last 15 minutes
that this browser has not rung (`gamma-inbox-rung` in localStorage, the
latest 200 ids) rings, five at most; the notification's tag keeps two tabs
to one. Its click opens the item as a row's click does. The desktop app
has notifications; the iPad's WKWebView has none, and the row says so.

## Not built

- **Comments off this server.** Clones, publications and the share host
  carry no comments: each server keeps its own threads ([mirror.md](mirror.md)
  "What travels"), and a page host's publication shows no comment
  controls. Syncing them needs the author's name carried along, as the
  Gamma export does (`author_name`). The sync popover of a clone or a
  publication says so: "Comments stay on each server: a sync carries
  none." (`MirrorPopover`).
- **Comments in the other readers.** The exports other than the Gamma zip
  (Markdown, the annotated PDF, Obsidian, Logseq, Zotero), the AI's context
  and tools, search and the MCP tools leave comments out.
  `comments.threads_for` is the reader they would call. A highlight's
  threads could become PDF reply annotations (`/IRT`), which the import
  already reads as child notes.
- **More of a thread.** No reactions, no anchor on a range of text inside a
  block (a thread hangs on the whole block), no person filter in the
  Comments window, no role or share access that reads without commenting
  (every signed-in reader may comment), no following a page to hear of
  every comment on it.
- **More of the inbox.** Reminders keep their corner cards and stay out of
  the Inbox. There is no e-mail, no mobile push and no notification on the
  iPad (its WKWebView has no `Notification`); a system notification rings
  only while a Gamma tab is open. A tab without a page open learns of new
  items through the 30-second poll: the account socket `WS
  /api/ws/account` is reserved, not built ([collab.md](collab.md)).
- **Invitations without an inviter.** A membership made from the command
  line (`manage.py`) has no actor, so its item reads "Someone added you to
  …".
- **Mentions typed in a clone.** They tell no one. The copy pushes its
  batches with client `sync`, which the Inbox skips ("The inbox" above):
  the names were typed on another server, whose accounts need not be this
  one's.

## Tests

- `backend/tests/test_comments.py`: create, reply, edit, delete and the
  tombstone, resolve and reopen, and no change-log move for any of them; the
  validation (empty and over-long text, a block outside the page, a reply
  into a reply or onto another block, a bad id, an unknown page or state,
  half an emoji); a repeated client id answered as stored, 409 for
  another author's and for the author's own on another page (a share
  visitor's retry through another share reading nothing of it), 404 once
  its page is in Recently deleted; an edit, a resolve and a delete refused
  with 404 once the comment is off the page they were authorized for; the permission table row by row (owner,
  editor, viewer, a non-member, an anonymous link visitor, a guest account
  through a share link, a signed-in account through a view share, an edit
  share and a list share, a guest at home, read and write tokens with a
  write token's edit and delete of its own comments only); the events
  handed to `after_write` for every kind, with `via`; a page in Recently deleted hiding its threads and its restore
  showing them; the purge of a page deleted for good, sparing a note moved
  away, and of a trashed copy that `create_page(block_id=)` replaces; a block delete orphaning, an undo bringing back, the sweep after
  seven days and not before; an insert pulling a block out of a trashed
  page; `PUT /blocks/{id}/children`; an edit share's visitor inserting, or
  replacing children with, an id deleted from a private page and reading
  none of its threads, while the visitor's own undo and a member's paste
  keep theirs; a backup merge on the untrash path with
  a remapped block and re-minted comment ids, and the review's count; the
  Gamma export round trip with `author_name` after the author's account is
  deleted; the room's `{t: "comments"}` reaching every tab but the writer's.
  The suite's drift checks run after each test.
- `backend/tests/test_inbox.py`: every producer (a mention in a note,
  through the ops and the block endpoint; a comment's mention, a reply, a
  resolve and its edit; a page and a folder share; an invitation); the
  mention diff (a re-save is silent, a new name in the same block tells
  that person, a reminder, code and an e-mail address name no one); no
  self, no guest, no non-member; the batches of an offline copy, a mirror
  and a link visitor telling no one; the AI's batch counting as its chat
  owner's; a replayed batch telling no one twice; a share's invitee getting
  the token and hearing of what happens on the page; a view-only folder
  share's visitor mentioning an account of its own, which gets no item and
  so not the page's anyone-with-the-link edit token, while a mention
  through that link hands out that link; a half-typed `@ib_ben` archived at
  once when the text becomes `@ib_benjamin`, a name removed by another path
  settling, and a split and a join leaving one unread item that follows
  the name; a stopped and remade share (the mention takes the new token,
  the old `shared` item settles), the archived titles once the share is
  gone for good and the page renamed, and an invitee who becomes a member
  getting `""`; the owner of a shared workspace an admin made for them
  told; the 25 cap, filled
  from the people the page admits; the filters, the
  marks and the paging, one moment never split; the settle after a block
  delete, a trash (a member keeping the title), a stopped share (page and
  folder), but not for a workspace that failed; the cleanup on leaving, workspace and account
  deletion and the comment sweep; the daily sweep; the nudge reaching the
  recipient's sockets only; a users.db error never failing the comment or
  the batch; who has an Inbox (guests, tokens, no session).
- `backend/tests/test_collab.py`: `publish_account` reaching every socket
  of one account and no one else's. `test_shares.py`: `added_at` kept by a
  later save, and only new invitees told. `test_workspaces.py` and
  `test_pending_memberships.py`: `set_member`'s inserted flag and the
  `invited` items of a claim and a linked invitation. `test_hosted.py`:
  `/api/inbox/mark` passing the read-only gate.
- `backend/tests/test_migrations.py`: step 40, re-run.
- `backend/tests/test_restore_consistency.py`: `_fresh_ids`' remap.
- `frontend/tests/comments.test.mjs`: the folding per block and the open
  counts, Open and Resolved by latest activity, the popover's list, a
  refetch keeping what reads the same, a new thread and a reply shown once,
  an edit, a delete with the server's tombstone rules, resolve and reopen,
  who may edit and delete, the 9+ badge, the anchor's one line, the thread
  link, the commented-passage rule, the abandoned Comment highlight, which
  comments a popover draws later and the height of the box standing in for
  one. `frontend/tests/enterKey.test.mjs`: `sendsOnShiftEnter`, on a
  keyboard and an on-screen one. `frontend/tests/slashCommands.test.mjs`:
  the commands a comment's field offers, without a page, a sheet, an
  upload or an embed.
  `frontend/tests/selectionTip.test.mjs`: the selection popup's anchor from
  the release point, and its placement from it. `frontend/tests/refLists.test.mjs`: the typed `[[query`, a picked
row's edit and the hand-typed title's. `frontend/tests/inboxText.test.mjs`: the sentence of each kind, the
  snippet, the tabs, the days, the jump of each kind (in place, another
  workspace, through a share, a folder, an invitation), a listing's first
  page over the older items held, the marks and their count, and the
  rings. `frontend/tests/collabSession.test.mjs`: the socket's `comments`
  and `hello` reaching `onComments`, and `inbox` reaching `onInbox` on any
  page.
- Browser: the `comments` group (`tests/e2e/scenarios/comments.mjs`): two
  editors, a viewer and a link visitor without an account. A thread on a
  note from its row control, the popover opening in place, the @ menu of
  people; the reply showing live in the other tab; Resolve, the window's
  Resolved tab and Reopen; Ctrl+Shift+M from an open editor and Escape back
  to the control; the page's own thread; edit and delete one's own, and
  another person's comment offering Copy link only; a reply left unsent
  back in its field when the popover opens again, and none once emptied;
  the field as a note's editor: Ctrl+Z undoing its own typing, "[[" and
  part of a title opening the link
  picker, Escape closing only the picker, the picked page a chip in the
  field and its title in the sent comment, "@" and a name picking a person
  whose chip shows, `$\sqrt{x` showing the live preview while the caret is
  inside, Enter breaking the line and Shift+Enter sending, the sent comment
  showing KaTeX; the "/" menu offering the commands that write text and
  none of New page, Handwritten note, Image and Embed note, Escape closing
  only the menu, `/h2` and Enter making a heading, a bulleted list
  continuing on Enter, the Equation block's `$$` with the live preview, the
  sent comment showing an h2, the list and KaTeX, and the send button a
  24 px circle with a 12 px arrow on the text's line, titled "Comment
  (Shift+Enter)" and "Send reply (Shift+Enter)"; the viewer commenting
  from a read-only row; the visitor reading with "Sign in to comment";
  `?thread=` opening the thread; a note thread's Copy link in a folder
  share, opened, showing the thread; a highlight's thread from its menu and
  its PDF badge; on a phone, a note's thread link on a page with a PDF
  bringing up the notes with the note under the popover; a mouse selection's
  popup opening in place within 40px of the release point, its colours, the
  line and then Comment and Link; Comment making a highlight with its
  popover and the field focused, the comment sent with Shift+Enter, and the
  highlight's rects drawn with the 2px line of its colour; a second Comment
  left with Escape leaving no highlight. `tests/e2e/select.mjs` runs it for `frontend/src/comments/`
  (with `collab` and `inbox`), the editor and the PDF viewer,
  `routers/comments.py`, `gamma/inbox.py`, and `gamma/comments.py` with
  `notes-pdf-share`, `collab` and `transfers` too, since every block
  writer runs that module's hooks.
- Browser: the `inbox` group (`tests/e2e/scenarios/inbox.mjs`): an owner
  and an editor of a shared workspace, and an account outside it invited
  to a page share. An invitation opening its workspace; a mention typed
  through the @ menu in a note reaching the bell of a tab with
  a page open within 3 s (the nudge: the poll is 30 s); the home library,
  without a socket, catching up on focus; an item of another workspace
  opening there at its block; an item of this workspace landing on its
  block with the thread open, in place; the invitee hearing of the share, a
  mention and a reply and opening the reply through the share; Mark all
  read; Archive and Move back to the inbox; an item whose page went to
  Recently deleted settling into Archived; Inbox notifications turned on
  in Settings ringing a new item once per browser (a stand-in
  `Notification`). `select.mjs` runs it for `frontend/src/inbox/`,
  `gamma/inbox.py`, `routers/inbox.py`, `routers/shares.py`, the comments
  modules, `MentionMenu.jsx` and `SettingsAppearance.jsx`.
