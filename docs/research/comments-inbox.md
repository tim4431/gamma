# Comments and an inbox

Survey date: 2026-10-20. How Notion's comments, Inbox and notifications
behave, what Gamma already had that such a feature would build on, the
designs weighed for storing comments and producing an inbox, and the one
picked. The mechanics are in [dev/comments.md](../dev/comments.md).

**Status (2026-10-20).** Built as designed: comments are a table of each
workspace's `pages.db` and the inbox an event table in `users.db`, with
live nudges over the page sockets and a poll elsewhere
([dev/comments.md](../dev/comments.md), whose "Not built" lists what the
first version leaves out).

## What Notion does

Sources: the help pages on [comments, mentions and reminders][n-comments],
[updates and notifications][n-inbox], [the sidebar][n-sidebar],
[sharing and permissions][n-perms] and [reminders][n-remind], and the API
guide [Working with comments][n-api]. Details marked "unsure" could not be
confirmed on the current pages.

- **Anchors.** A comment opens a discussion on the page (under the title),
  on a selection of text or a whole block (select, then Comment; the block
  handle's Comment; Cmd/Ctrl+Shift+M), on a database property, or on a
  database row.
- **Threads.** Replies stay in their discussion. Resolving hides it from the
  page; the comments pane (the speech bubble at the page's top right) keeps it
  under a Resolved filter with Re-open. The pane filters by person and by
  open or resolved, lists the newest reply first, and does not re-sort while
  open. A click scrolls to the anchor. Inline display is either the thread
  text in the margin or icons only.
- **Bodies.** @people, @pages (which make a backlink), @dates and emoji
  reactions. The author can edit and delete.
- **Who may comment.** Full access, Can edit and Can comment may; Can view may
  not. A link visitor must sign in to comment. Whether Can comment may resolve
  is unsure.
- **Inbox.** A fixed sidebar entry with a red badge (whether it shows a
  number is unsure). Filters: unread and read, unread only, archived, all
  workspace updates, and by type (access, discussions, mentions, updates,
  workspace). Bulk Mark all as read, Archive all and Archive read. Items group
  by page and thread, each with who, what, the page, a snippet and the time,
  an inline reply box and resolve. A click opens the thread at its anchor.
  Each page can be set to all updates, all comments, or replies and
  @mentions.
- **What notifies.** Being @mentioned; a reply to your comment; any comment
  on a page set to all comments; being added to a person property; a
  reminder for you; an invitation to a page; access requests. Nothing from a
  page you cannot open. Mentions and replies always land in the Inbox.
- **Delivery.** Desktop push about ten seconds after a mention, mobile push if
  it is not seen within five minutes, e-mail when Notion is closed, Slack and
  Teams integrations, all switched in Settings, Notifications.

## Where Gamma started

- `@username` mentions are plain text in note blocks, with chips, a menu and
  reminders found through the notes index in every workspace an account can
  open ([dev/mentions.md](../dev/mentions.md)). Its Not built list said being
  mentioned notified no one.
- The only per-account feeds were reminders (derived on each listing, done
  marks in `users.db`) and the notices red dot (registered sources, seen
  marks in a pref). Both poll every five minutes.
- The only live channel is the page socket, one room per workspace and page
  ([dev/collab.md](../dev/collab.md)). A tab on the home library has none.
- "Comment" already meant the note typed under a PDF highlight, which the
  annotated PDF writes as the annotation's text, and "reply" meant a sticky
  note's child notes.
- Pages have no owner column and blocks no author: the op log keeps actors
  for about a day.
- Sharing a page from a personal library to a named account is the usual
  share, and a personal workspace has no other members, so the people a page
  is shared with are mostly not members.

## Designs weighed

**Comments as blocks.** Child blocks of the commented block, with a new
`kind`, would travel through clones, publications, backups, the op log and
undo with no code of their own, as folders and labels do. They were turned
down: about thirty readers of the block tree on the server and every tree
walker in the client would have to skip them, a highlight with a comment
would count as having a note (and show the PDF badge), the annotated PDF
would print threads into the annotation, the author would travel in
properties that any editor or mirror token could write, every comment would
move the page in the change feed (a mirror round, a home reorder, a folder
sync rewrite), and the new `kind` case is a workspace migration that pauses
every clone.

**Comments in block properties.** They would also travel, but every edit's
echo and three-way merge would carry the whole thread. Turned down.

**Comments in their own table of `pages.db`.** The AI chats' shape: written
by people, so in `pages.db` (backups carry it), invisible to every block
reader, deleted with the page, kept in Recently deleted. The cost is a line
in each copy path (backup merge, Gamma export, the untrash path) and no sync
to clones in the first version. Picked.

**An inbox derived on each listing.** The reminders model: scan every
workspace's notes for `@me` and compare with a cursor. It cannot tell who
wrote a mention or when once the op log has moved on, it notifies again after
every edit of a block that keeps the name, and it costs one scan per
workspace on every poll. Turned down.

**An inbox as an event table.** Rows written when the mention, reply or
share happens, with the actor and a snippet, read and archive marks on the
row, and a settle on listing for items whose source went away. Picked. Three
details came out of the review: dedupe by comparing a block's mentions before
and after the edit (a unique index would drop a second share to the same
person and would never be droppable in that release); reachability per item
(membership or a share that admits the account, with the share token kept on
the row so the jump can open the share); and a failure policy so a busy
`users.db` never fails a comment already stored.

**Live delivery.** An account socket is new infrastructure for every
deployment; a five-minute poll is far from Notion's ten seconds. Picked: a
content-free nudge sent to the recipient's peers in the page rooms they
already hold, and a 30-second poll while a tab shows no page. The account
socket stays the next step.

## Still open

- Comments on clones, publications and the share host.
- Comments in the exports other than the Gamma zip, the AI's reads, search
  and MCP; a highlight's thread could become PDF reply annotations
  (`/IRT`), which the import already reads as child notes.
- A role that reads without commenting (viewers comment, unlike Notion's
  Can view), following a page, reactions, ranges inside a block.
- E-mail and push when no Gamma window is open; the iPad's web view has no
  notifications.

[n-comments]: https://www.notion.com/help/comments-mentions-and-reminders
[n-inbox]: https://www.notion.com/help/updates-and-notifications
[n-sidebar]: https://www.notion.com/help/navigate-with-the-sidebar
[n-perms]: https://www.notion.com/help/sharing-and-permissions
[n-remind]: https://www.notion.com/help/reminders
[n-api]: https://developers.notion.com/guides/data-apis/working-with-comments
