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
global step only ([migrations.md](migrations.md) rule 2), so it does not
move `migrations.workspace_format()`: the comments never pause clones and
publications.

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
| `shared` | people are invited to a page or folder share | each new invitee, with the share's token | `routers/shares._invite`, from both `_create` and `_update`: it compares the old and the new `share_users`, keeps `added_at` for the people who stay, and `shares.tell` writes the items after the commit; also `shares.invite_cloud`'s linked branch and `shares.claim_pending` for each share joined, from its inviter |
| `invited` | an account is added to a shared workspace | the new member | `workspaces.set_member` (which returns whether it inserted the membership: a role change tells no one), `invite_cloud`'s linked branch, `claim_pending_memberships` for each workspace joined, from its inviter, and `workspaces.create` for an owner someone else made a shared workspace for (an admin's `POST /api/workspaces` with an owner) |
| `join_request` | someone with no account here asks to join through a share's link (a refused cloud sign-in under the `invited` policy, [cloud_accounts.md](cloud_accounts.md) "Requests to join") | whoever made the share and the workspace's owners, while they edit there | `join_requests.ask`, for a new request or one that moved to this share. The actor is `cloud:<subject>`, no account (listed as `""`); the snippet is the cloud username, which the sentence names. It settles once the request no longer waits on that page's or folder's share (approved, declined, gone or moved), and a click opens that Share popover |

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

`collab.publish_account` sends the nudge to every page socket of the
account, in any room of any workspace, a share link's included
([collab.md](collab.md) "Rooms and the socket"). The message is
content-free: it says only that something changed, and the tab asks
`/api/inbox` for the rest, so a socket admitted to one page learns nothing
about another. A tab on the page where a comment was written gets both
`comments` and `inbox`, and the client coalesces them (it debounces nudges
by 300 ms). A tab with no page open has no socket and polls ("Not built"
below).

## In the app

### Comments

The code is `frontend/src/comments/`. Two modules load with the app:
`comments.js` (pure: folding, ordering, the changes a write makes before
the server answers, the thread link; `tests/comments.test.mjs`) and
`useComments.js`. Three surfaces load on first use, through `lazySurface`
([frontend-refactor.md](frontend-refactor.md) "Lazy boundaries"):
`ThreadPopover.jsx` and the PDF margin's cards, `CommentMargin.jsx` (both
draw a thread with `Thread.jsx`, which holds the composer,
`CommentComposer.jsx`), and `CommentsPanel.jsx`. `comments.css` holds every
rule, design tokens only, and loads with the app, since the rows need the
control's rules.

**The page's threads.** App runs one `useComments` for the open page. It
reads `GET /api/pages/{id}/comments?state=all` when the page opens, and
again when the page's socket says `comments` or says `hello` after a
reconnect: `collabSession.js` hands both to its `onComments` option
([collab.md](collab.md)). For a page the library lists, the first read
is asked for alongside the page's tree (`prefetch`, from App's
`openBlock`), so the threads come with the page rather than a round trip
after it; a link to a page that is gone asks for its tree alone. Only the fetch the page's
opening makes takes that answer, and the room's `hello` as the page joins
reads again whatever was written in between. One request is out at a time, and nudges that
arrive meanwhile make one more after it. `refresh()` resolves with what
the request it waited for answered (`{pageId, threads}`, or null). A page
whose comments answer 404 or 403 has none, and `readable` turns false. The
threads fold into
`threadsByBlock` and `countByBlock` (open threads per block). Both Maps
keep their identity while they read the same (`keepIfSame`), and a refetch
that changed nothing keeps every thread object (`keepThreads`), so it
re-renders no row. The reader's own writes show at once. A new comment gets
its id here (`makeId`, a block id's shape), so a retried request is
answered as stored; `add` resolves that id once stored (false when
refused), which a new thread's margin card activates. Every write sends this tab's `client`, so the room's
nudge skips it. A refused write puts the threads back as they were while
the list is still the one the change made (a refetch or another write
since leaves it), says so in the status line, and refetches. A refused
comment gets its text back in its field, and a refused edit keeps its field
open with the new text. A refetch that answers before the server has a new
comment shows it again from the writes still on their way.

**Where the controls show.** App's `commentsOn` is `!publicPage &&
comments.readable`. Without it there is no row control (`rowProps.onComment`
is null, which also hides the handle menu's Comment and `block.comment`), no
title-row control, no Comment in the highlight menu, no popover and no
Comments window (its View-menu row, the phone More sheet's row,
`winVisible.comments`, and `app.toggleComments`, whose `when` reads
`commentsOn` from `appCmdRef`): a page
host's publication (comments stay off the share host) and a page
whose comments the reader cannot read.

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
comment" in it and puts the focus there. Each thread shows its comments;
its reply field shows, focused, once the thread is the active one, as in
Notion: a press on the thread (not on one of its buttons, links or fields),
Enter or Space on it, or the link that opened the popover at it makes it
active, and a thread holding an unsent reply keeps its field. Its first comment's author line carries the thread's Resolve
(a check mark) or Reopen button beside the "⋯"; a resolved thread says
who resolved it at its foot, where the reply field was. The replies step in
under the first comment's text, each avatar under its name, as the
annotated PDF prints them ([pdf_typesetting.md](pdf_typesetting.md)); the
reply field keeps the thread's full width. A comment shows its author's avatar (`PersonAvatar`), name, time and text rendered as a note is (`BlockMarkdown`
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
shown after the popover opened is drawn at once. Without this, a thread of
twelve long comments full of math (300 equations) builds every equation
before the popover shows: about 900 ms in one task, against about 80 ms
with it. Its "⋯" (`MenuButton`) has Edit and Delete for its
author (Delete for the workspace's owner too) and Copy link. "New comment"
starts another thread. A link visitor without an account reads, with "Sign
in to comment" (the share's sign-in gate) in place of the fields.

**The composer.** The field is the note editor, `BlockCmEditor`, with a
send button. It has a note's typing aids, from the modules a note row
uses: `[[links]]`, @people and date chips, inline marks and math drawn in
place (App's MentionContext as `mentions`, `labelsFor(text)` as
`refLabels`). The pickers read App's `links` (`pages`, the open page as
`rootId`, `lookupBlock`, `refCache`, `onCacheRef`), handed through the
popover.

- "[[" opens the note's link picker (`editor/useRefPicker.js`): pages by
  title, then blocks from `GET /api/block-search` without the thread's own
  block. A picked row writes `[[id]]` and caches its label with
  `onCacheRef`; "]]" after a page's exact title links it.
- "@" opens the note's @ menu at the caret (`editor/useMentionMenu.js`):
  people, dates and page links. It has no "Remind" rows, since the
  reminder scan reads notes only, and no "Invite…".
- Inside `$…$` and `$$…$$` come the live preview, the `\command` and
  `\begin{}` completion, the Tab hop and the `$` and bracket pairing of
  [latex_editing.md](latex_editing.md) (`editor/useMathUi.js`).
- "/" opens the note's command menu (`editor/useSlashMenu.js`: typed only,
  never inside math or a code fence). A comment offers the commands that
  write text: headings, to-do, the lists, quote, callout, divider, the two
  equations, table, code block, Mermaid diagram, today's date, "Link to
  note" (the field's own `[[` picker), highlight and the colours. The
  commands that make a page or a sheet, upload a picture or embed a note
  `need` an ability the field does not pass (`slashCommands.js`
  `filterSlashCommands`' `can`: a note row passes `image` and `embed`, and
  `newPage` and `sheet` when App gives it their handlers). A command's text
  replaces the field's in one edit, which the field's undo takes back.

Left out: the outliner's keys, "Paste as", image and file upload, sheets,
embeds, presence carets and object drags.

**The composer's keys.** `sendsOnShiftEnter` and `composing`
(`chat/enterKey.js`) decide Return. Enter breaks the line as in a note,
continuing a list or a quote (`mdLines.enterPlan`) outside a code fence or
`$$…$$`. Shift+Enter and Ctrl/⌘+Enter send. On an on-screen keyboard
Return breaks the line, Shift or not, and the button sends; Ctrl/⌘+Enter
still sends from a tablet's keyboard. An edit's Save follows the same
rule. While a picker, the "/" menu or the completion is open, it takes
↑ ↓ Enter Tab and Escape first. The field keeps its own undo history
(`ownHistory`), so Ctrl/⌘+Z stays out of the page's block history, and F2
stays with it too. The history starts afresh when the field's text is set
from outside (emptied on sending, put back when a send fails), so an undo
never brings back part of a sent comment.

**The composer's box.** The field sits in one rounded box (`.commentBox`,
which takes the focus ring) with the round send button at its right: the
chat composer's `chatCircleBtn` at the size of a line of text
(`--ctl-icon-btn-xs`, 24 px, with a 12 px arrow). As in Notion, the button
is on the text's line while the text is one line, and at its last line
once it wraps. The text is in the rendered comment's type and grows up to
40vh, then scrolls. The button's hover title names the key
(`"{label} ({hint})"` with `chordLabel("Shift-Enter")`: "Comment
(Shift+Enter)", "⇧↩" on a Mac; `chordLabel("Mod-Enter")` on an on-screen
keyboard), and its accessible name is the plain action. An edit's field
has Cancel and Save in a bar under its box, where the length warning shows
too. Escape cancels an edit, and it closes once the server has it. The
field takes the focus a frame after mounting, since the popover stands
hidden until it is placed and a hidden field takes no focus; a field
focused meanwhile keeps its caret.

**Drafts.** Unsent text is kept per field for as long as the tab lives, in
a module-level map of `CommentComposer.jsx` keyed `new:<block>`,
`reply:<thread>` and `edit:<comment>` (`draftKey`). A press outside the
popover, Escape, a second press on its control or another block's comments
close it, and the text is back when it opens again. A popover opens "New
comment" when its block has a draft, and a comment with an unsaved edit
opens in its edit field. Sending empties the field and its draft; Cancel
drops an edit's.

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
comment" focused, as the highlight menu's Comment does; where the page has
room for the margin, the new thread opens as a card there instead ("The
PDF margin" below). App keeps that
highlight in `freshCommentRef` while its popover (or card) is open; when it
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
too) jumps to the note, as on any highlight, and opens the thread popover from the badge
(`PdfViewer`'s `onHighlightThread(id, badge)`). The count rides on the
highlight objects (`threadCount`), which the viewer's per-page memo
compares beside `hasNote`. Where the page has room for a column beside it,
the threads are cards in the margin and leave the badge (below).

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
and opens its popover at the thread. A thread that comes rises into the
list, and one with a new reply glides to the top as the others make room
(`ListMotion`); the page's threads loading fill it still. In a thread,
wherever it is drawn, a new comment rises into place.

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

### The PDF margin

Beside the PDF's pages, as Notion shows comments in a page's right margin,
each highlight's open threads are cards in a column, level with the
highlight's first line. Only the PDF has the margin: a note's threads, the
page's own and the notes pane's controls keep the popover, and the notes
pane has no comments section.

**When it shows.** While the document has a highlight with an open thread,
or a new thread's card is open, every page with room for the column
stands beside it: the page and a column `CARD_W` (280 px) wide,
`COLUMN_GAP` (24 px) from the page, are centred together, so the page
moves left by half of what the column takes (`marginLeft`,
`comments/marginLayout.js`). A page that would leave less than `EDGE`
(16 px) at either side has no margin and stays centred: a page at
fit-width, a narrow pane, a high zoom. A phone has none (App passes no
`commentMargin`). With no open thread in the document every page stays
centred. The viewer reads its own width (a ResizeObserver) and
hands each page its left edge (`PdfPage`'s `marginLeft`, null for none),
and the zoom anchor's horizontal arithmetic reads the same layout
(`pageLeft`). A zoom keeps the line under the pointer. It keeps the point
across the line too while the layout stays the same (the column fits
before and after, or neither), and once the page is wider than the
viewer. A zoom that makes the column appear or go moves the page sideways
by half the column's width (about 150 px) under the pointer: the page
fits the viewer, so there is no horizontal scroll to take the move up. A
page without the margin keeps the
badge's count and the popover; a page with it draws no count on the badge
(the badge stays for a highlight's own note).

**Where the cards come from.** The column is inside the page's box
(`.commentMargin`, absolute, `left: calc(100% + COLUMN_GAP)`), so it
scrolls and zooms with the page and needs nothing for pages out of view:
a page draws it only inside its look-ahead, like its highlights. App
provides `CommentMarginContext` (`comments/marginSlot.jsx`) through the
viewer's `commentMargin` prop: the page's `threadsByBlock`, the active
card's target and the popover's props and handlers. A page's
`CommentMarginSlot` reads it, so a new comment re-renders the margins and
not the pages, and finds the page's highlights with an open thread (and
the one a new thread's card is for), each at its first rect's top
(`highlightTop`). Every page with room draws its column, with or without
cards, so a page's first card can grow in and its last one shrink away.
The cards (`CommentMargin.jsx`) load on first use, which a PDF page
opening asks for with its tree.

**Stacking.** `stackCards` (`marginLayout.js`, `tests/marginLayout.test.mjs`)
sorts the cards by their highlight's top, keeping a highlight's threads in
order. Each goes at its highlight's top, or `CARD_GAP` (8 px) under the
card above when it would overlap it. The active card stays level with its
highlight: the ones under it are pushed down, the ones over it up, as in
Notion, but none above `min`; when they would go there, the cards stack
down from it and the active one comes as near its highlight as they leave
room for. Cards near a page's foot may run past it. The annotated-PDF
export prints a highlight's notes the same way, with no active card and
the run kept on its page (`gamma/pdf_notes.py` `_stack`,
[pdf_typesetting.md](pdf_typesetting.md#notes-and-comments-as-cards)): a change
to this rule belongs there too.

The drawn pages' margins are one column. Each page knows its top in px
from the first page's top (the viewer sums the page heights and
`PAGE_GAP`s, `PdfPage`'s `pageTop`) and tells the viewer's
`createMarginStack` (`MarginStackContext`, `marginSlot.jsx`) where its
lowest card ends, in the same frame. A page's `min` is `CARD_GAP` under
the lowest end among the pages above it, else the first page's top, so a
card grown at a page's foot pushes the next page's cards down, and at a
low zoom a page's cards go on under the ones above. A page reads that end
with `useSyncExternalStore`; it depends on the pages above only, so the
column settles page by page. The cards of the pages above are never moved
up for an active card on a later page: that card comes as near its
highlight as they leave room for. The margin holding the active card
(`.commentMargin.hasActive`) is drawn over the other pages' margins. The heights are the cards' own, measured before the
paint and again by a ResizeObserver as a thread opens, a field grows or a
formula is drawn. A measure is compared with the last one, kept in a ref,
and sets no state when they agree: done in a state updater, React re-ran
it against an older state as the observer's updates and the layout
effect's interleaved, and it looped ("Maximum update depth exceeded"). A
card moves to its new place through its `top`, on `--dur-slow`; one that
comes grows in where it stands and one that goes (resolved, a new thread's
card closed) shrinks away there (`ListMotion`, [ui-design.md](ui-design.md)
"Lists that change"). A new thread sent shows in the new card, over its
emptied field, while it is on its way: it has no card of its own then
(`CommentMargin` leaves out a thread on the highlight that was not shown
when the new card came), so the two never stand together with the new
card pushing the other up. When `add` answers, the thread's card takes
the new card's place as the active one, mounted where it rests, so its
`top` has nothing to move from, and neither comes in nor goes with
motion. Refused, the field gets its text back.

**A card.** At rest it is the thread's first comment (avatar, name, time
and text as comments are drawn, clamped to three lines) and "n replies",
with neither Resolve nor the "⋯". It is `Thread.jsx`'s `Thread` with
`summary`: the popover's own thread view, whose active rule it shares. A
press on the card (not on a button, link or field), Enter or Space on it,
or a press on its highlight in the page makes it the active card: the
whole thread, the reply field focused, Resolve on the first comment and
each comment's "⋯", drawn as the popover draws them (comments past the
first 2,000 characters as they near the view). A card holding an unsent
reply keeps its field. One card is active at a time; a press outside every
margin (one on its own highlight, a menu or a popup it opened excepted)
or Escape makes none active. The active card's highlight is drawn
stronger: its wash mixed with its own strong line colour (`--hl-line`, or
`--hl-dark-line` on a light page), while the card shows. Resolve takes
the card away and makes none active. The page's pens, lasso, eraser and
Text tool leave a press on a card alone (`.commentMargin` is in
`ink/InkLayer.jsx`'s `NOT_INK`, and `markup/TextBoxLayer.jsx` skips it),
and a right-click on one opens no "Paste here" (`BESIDE_PAGE`), so a card
is pressed, read and typed in with a tool armed. A press on a card lets go
of a handwriting selection, as a press off the page does, so the strip's
Delete and the handwriting's Ctrl+C, X and D reach no strokes while a card
is read; a focused card or thread popover keeps its Enter and Escape from
the strip ([hotkeys.md](hotkeys.md) "Widget keys"). A link visitor without an account
can make a card active to read it and gets "Sign in to comment" under
the thread, as in the popover.

**The active card is App's `threadPop`.** `openThread(..., { pdf: true })`
(a highlight's click or badge, the highlight menu's Comment, the selection
popup's Comment, a jump to a thread from the Comments window, a link or
the Inbox) asks the viewer whether the highlight's page has room
(`marginRef.current(hlId)`); when it has, and the thread is not a
resolved one (which has no card), the target is `{..., margin: true}`
and App renders no popover for it. A press on a card opens its thread the
same way. So Copy link, Escape and the abandoned-Comment rule read the
same state as for the popover: a new thread's card (`compose`, a field
of its own, focused) closed with nothing sent takes its highlight away.
Sent, `add`'s id makes the new thread the active card. When the active
card's page loses its room (a zoom, a narrower pane), the viewer calls
`onMarginLost` and App shows the thread in the popover from the
highlight instead, drafts and all (they are keyed per field, not per
surface).

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
("ann replied to a comment on Draft"; a request to join names the cloud
username asking, `inboxActor`: "cat asks to open Draft"), the note's or the comment's words,
the workspace's name when it is not this one, and the time. Its actions,
shown on hover and focus (always under a touch screen), mark it read or
unread and archive it or move it back. Items load when the panel opens, 50
at a time, with Show older. Each tab keeps the listing it showed last:
the panel opens again, and a tab comes back, at its top on the first page
of what it held while a fresh look is on its way, and the server's page
that look brings takes its place, changing it in place. The older rows
Show older had loaded are not kept, since no look would refresh them, and
Show older comes back with the look (`reopenedListing`, `lookedListing`).
A tab seen for the first time, or empty when it was, keeps its place
blank until the listing is in (`Reveal`). A mark shows at once and takes the item out
of a tab it no longer belongs in, the row leaving from where it stood
while the rest close up (`ListMotion`); a day's header goes with its last
row, and Mark all read empties Unread at once. A refused one puts back the rows and the
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
in the library. A request to join of this workspace opens the Share
popover of its page (the page opened first) or folder, where it is
decided; one of another workspace navigates to that page or folder. The mark goes through the page's keepalive ledger at
`normal` priority (`apiJson`'s `keepalive`, [collab.md](collab.md)
"Keepalive"), so a navigation does not cut it off.

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
  and the annotated PDF (Markdown, Obsidian, Logseq, Zotero), the AI's
  context and tools, search and the MCP tools leave comments out. The
  annotated PDF's Comments switch prints the open threads on a highlight
  and on its notes as cards beside the page, as the margin draws them, a
  note's threads (a highlight's own under its own text) under the note
  when it is printed beside the page too
  ([pdf_typesetting.md](pdf_typesetting.md#notes-and-comments-as-cards)).
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
  items through the 30-second poll: there is no account socket
  ([collab.md](collab.md) "Limits and next steps").
- **Invitations without an inviter.** A membership made from the command
  line (`manage.py`) has no actor, so its item reads "Someone added you to
  …".
- **Mentions typed in a clone.** They tell no one. The copy pushes its
  batches with client `sync`, which the Inbox skips ("The inbox" above):
  the names were typed on another server, whose accounts need not be this
  one's.

## Tests

- `backend/tests/test_comments.py`: the writes and their validation, a
  repeated client id answered as stored, and the permission table row by
  row. Also the events handed to `after_write`, and the lifecycle: Recently
  deleted, the purges, a delete orphaning and an undo bringing back, the
  sweep, `PUT /blocks/{id}/children`, and a share editor's insert reading no
  other page's threads. The backup merge, the Gamma export round trip and
  the room's `{t: "comments"}` too. The suite's drift checks run after each
  test.
- `backend/tests/test_inbox.py`: every producer and the mention diff (a
  re-save, a moved name, a half-typed name archived); who is left out (the
  actor, guests, non-members, an offline copy, a mirror, a link visitor);
  the share tokens `reach` hands out and the settle updates; the 25 cap.
  Also the listing, its paging and its settle, the marks, the cleanups and
  the sweep, the nudge reaching the recipient's sockets only, a users.db
  error never failing a write, a share whose name cannot be read still
  telling its invitee, and who has an Inbox.
- `test_collab.py`: `publish_account`. `test_shares.py`: `added_at` and only
  new invitees told. `test_workspaces.py`, `test_pending_memberships.py`:
  the `invited` items. `test_hosted.py`: `/api/inbox/mark` past the
  read-only gate. `test_migrations.py`: step 40, re-run.
  `test_restore_consistency.py`: `_fresh_ids`' remap.
- Frontend node tests: `comments.test.mjs` (folding, ordering, the changes
  shown before the server answers, who may edit, the snippets and the
  link, the highlight rules, which comments draw later),
  `marginLayout.test.mjs` (a page's left edge and the stacking),
  `inboxText.test.mjs` (sentences, days, jumps, listings, marks, rings),
  `selectionTip.test.mjs`, `refLists.test.mjs`, `enterKey.test.mjs`
  (`sendsOnShiftEnter`), `slashCommands.test.mjs` (a comment's commands)
  and `collabSession.test.mjs` (the `comments` and `inbox` messages).
- Browser: the `comments` group (`tests/e2e/scenarios/comments.mjs`), with
  two editors, a viewer and a link visitor without an account. It covers a
  thread from each control, live replies, resolve, the Comments window, the
  composer as a note's editor (pickers, math, the "/" menu, Enter and
  Shift+Enter, the send button), drafts, `?thread=` and Copy link, the
  selection popup's Comment, and the PDF margin's cards (placement,
  activation, a reply, Escape, Resolve, no margin at fit-width; beside
  the pen strip, a press with a tool armed, a handwriting selection let
  go, the right-click, Enter and Escape). `frontend/src/ink/**` and
  `frontend/src/markup/**` select it too.
- Browser: the `inbox` group (`tests/e2e/scenarios/inbox.mjs`): an
  invitation's jump, a mention's nudge reaching an open page within 3 s and
  the home library catching up on focus, the jumps in place, into another
  workspace and through a share, Mark all read, Archive and back, an item
  settling once its page is in Recently deleted, and a system notification
  ringing once per browser.
- `tests/e2e/select.mjs` runs these groups for the comment and inbox
  modules, frontend and backend. `gamma/comments.py` also runs
  `notes-pdf-share`, `collab` and `transfers`, since every block writer runs
  its hooks.
