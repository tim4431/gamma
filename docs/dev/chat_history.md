# Chat history

Where a chat conversation is kept: one bucket per page, folder or the
library root, the active conversation of each, and the archive of earlier
ones. The chat request itself is in [ai.md](ai.md).

## Buckets

Focused page id in the paper view, `home` at the library root, the
folder's own id per folder (folders are blocks,
[home_library.md](home_library.md) "Folders and labels") — each folder keeps
its own conversation, and switching folders re-scopes the next message. A
bucket is always a block id or `home`, so a rename or a move of the folder
changes nothing about its chat. No conversation is ever dropped with a
folder: deleting one (`DELETE /api/folders/{id}`, "Keep pages" and "Delete
pages too" alike) files the active conversation and the history of the
folder and of every folder below it into the library chat's history
(`home`, `file_into_home` in `routers/chats.py`), where they stay
findable. ChatDock's `chatKey` is the open page's id, else the open
folder's id, else `home`. A bucket switch empties the composer at once and
then reads the stored conversation; a draft typed while that read is in
flight stays (opening a history entry still empties it). A remount of the
dock in the same bucket (an iPad turning, the dock closed and opened
again) brings the draft back, and an ask from App (a handwriting block's
"Transcribe with AI") is sent once however often the dock mounts: both
live in ChatDock's module-level `kept`, since one dock is mounted at a
time.

Replies stream per bucket, independently. `chat/chatSession.js` (owned by
App, so navigation can unmount the dock while a request runs) keeps one
in-flight reply per bucket. `active` is the set of streaming buckets, each
with its own `AbortController`. Asking one paper, opening another and asking
it too runs both requests at once. The composer, the Stop button and the
edit/re-send controls are disabled only while THIS bucket's reply streams
(`busyHere` in `ChatDock`), and Stop aborts only that one. A bucket refuses
a second question until its reply ends. The stream's `done` agent event
carries the bucket so a background reply finishing does not clear the open
page's live edit preview (`handleAgentEvent`). Covered by
`tests/chatSession.test.mjs` and the e2e `chat navigation` steps.

Two tabs, or two members, can hold the same bucket's conversation, so a save
never replaces a copy it hasn't seen. The active row's `updated_at` is the
conversation's version. `GET /chats/{key}` returns it, the session keeps the
one it last read or wrote per bucket (`seen`, `version`), and every save
sends it (`PUT /chats/{key}` `{messages, updated_at}`). A save made from an
older copy is refused with 409 and the stored conversation. The session then
merges the two (`mergeChats`, three-way from the copy it had read). Every
message either side added stays, ours after the message it follows, else at
the end. Our newer version of a message wins, theirs wins where ours is
unchanged, and what either side dropped since the copy was read goes: this
tab's edit-and-resend, and the other side's New chat (the stored
conversation is then empty, or another one opened from history) or
edit-and-resend, so a stale tab never brings an archived conversation back.
A turn (a question and the replies after it) this tab changed since stays
whole: a reply that finished here after another tab's New chat starts the
new conversation with its question. The session shows the merge and saves
it against the stored version; a reply still streaming is rebased onto it
on its next update. A tab that comes back into focus (`focus`,
`visibilitychange`) reads the stored conversation again and shows it when
its version is not the tab's, as long as the tab's copy is saved and no
reply streams there; the composer's draft stays. Messages carry
a client-minted `id`, so the versions of one streamed reply are one message;
older messages match by content. A save that fails on the network or with a
5xx is retried (1, 3, 8 s). One that still fails, or a refusal, marks the
bucket in the session's `failed` map, and the dock shows "This conversation
isn't saved" with Retry until a save goes through. Covered by `tests/chatConflicts.test.mjs`,
`backend/tests/test_chat_versions.py` and the e2e step "two tabs asking in
one conversation".

Chats belong to the workspace, and only its editors change them
(`require_ws(write=True)` on every chat write). A workspace viewer asks the
AI with the reading tools, but its conversation stays in the tab: App's
save does nothing for it, the dock shows a "Not saved" tag, hides History,
and New chat starts over locally.

## Stored conversations

Each bucket keeps its earlier conversations. `chats` (in the workspace's
pages.db, beside the pages they are about) holds the
one ACTIVE conversation per bucket — what the panel shows and autosaves —
plus its `title`, its `updated_at` the conversation's version; `chat_history`
holds the archived ones (`id, bucket, title, messages, created_at,
updated_at`). Routes: `gamma/routers/chats.py`, prefixes `/api/chats` (the
active conversation) and `/api/chat-history` (the archive).

- **New chat** (+ in the header) archives the conversation: it POSTs
  `/chat-history/archive` `{bucket, messages, title, updated_at}`, which
  files it into history and clears the active row. The title is the user's,
  else the first user message's first non-quote line (`derive_title`). The
  client sends its own copy of the messages, so a reply still inside the
  500 ms autosave debounce is kept. An empty conversation archives to
  nothing. A stored conversation newer than the copy (another tab kept
  talking) is archived too, never deleted. Whichever of the two holds every
  message of the other is archived alone; otherwise both are.
- The **History** button (clock icon) opens a popover listing the active
  conversation first (highlighted, "now") and then the bucket's archived
  ones newest-first (`GET /chat-history?bucket=`; title, age, message count
  in the tooltip), with a search box filtering on title + first message.
  Clicking an entry POSTs `/chat-history/{id}/open` with the current
  conversation and its version: the current one is archived (a newer stored
  one too, as for New chat), the entry becomes the active row and leaves
  history, and the answer carries the new version. A conversation is always
  in exactly one place.
  - Each row's menu (its "⋯", a right-click or a held finger) has Rename,
    and on an archived row Select and Delete.
  - Rename: inline `aiKeyInput`; Enter (not one that confirms an input
    method's word) or a blur saves, Escape cancels. The active chat's title goes through
    `PUT /chats/{key}` `{title}`, which leaves the messages and the version
    as they are; an entry's through `PUT /chat-history/{id}`. The autosave
    never sends a title, so it can't roll a rename back.
  - Delete: confirm dialog, then `DELETE /chat-history/{id}`. The active
    conversation has no delete; start a new chat instead.
  - Several at once: the menu's Select ticks a row, and while anything is
    ticked every archived row shows its tick box and a click on a row
    ticks it instead of opening it. The bar under the list says how
    many, offers **Select all** (the rows the search leaves listed) and
    **Clear**, and its **Delete** confirms once and sends them in one call
    (`POST /chat-history/delete` `{ids}`). The ticks belong to the open
    popover: closing it, or a search that hides a ticked row, drops them, so
    Delete never takes a row the user cannot see.
- History follows its bucket: a folder delete moves its entries into
  `home`'s with the active rows, and `ops.delete_page` drops the active row
  and the entries of a page deleted for good, in the deleting transaction
  (a page in Recently deleted keeps its chats). A Gamma export carries its pages' buckets whole (the active
  conversation and the history; on a folder export the folder views'
  buckets too), and a backup merge or an import adds every conversation
  the workspace lacks — a bucket's active one, an archived one by its id
  (`db.copy_chats`); a replace restore brings the backup's chats with its
  pages.
