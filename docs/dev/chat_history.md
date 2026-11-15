# Chat history

Where a chat conversation is kept: one bucket per page, folder or the
library root, the active conversation of each, and the archive of earlier
ones; and how one is exported with its pictures. The chat request itself is
in [ai.md](ai.md).

A conversation with a computer's agent (Codex or Claude Code,
[local_agents.md](local_agents.md) "The chat's computer side") keeps
nothing here: its conversations use the same buckets (a page, a folder,
`home`) but are the account's alone, in their own private table
(`computer_chats`, with where each one's agent works), written by the
server's runner, and no member, share, workspace export, backup or
`read_chats` reads them. A deleted folder files them into the library's
History too (`local_agents.chats.file_into_home`, in the same
transaction), each keeping its directory. The chat's History lists them
beside the bucket's conversations here ("Every agent's conversations"),
and their owner exports them as these are ("Exporting a conversation").

## Buckets

Focused page id in the paper view, `home` at the library root, the
folder's own id per folder (folders are blocks,
[home_library.md](home_library.md) "Folders and labels") — each folder keeps
its own conversation, and switching folders re-scopes the next message. A
bucket is always a block id or `home`, so a rename or a move of the folder
changes nothing about its chat. No conversation is ever dropped with a
folder: deleting one (`DELETE /api/folders/{id}`, "Keep pages" and "Delete
pages too" alike, or the agent's `delete_folder`) files the active
conversation and the history of the folder and of every folder below it
into the library chat's history
(`home`, `file_into_home` in `routers/chats.py`), where they stay
findable. ChatDock's `chatKey` is the open page's id, else the open
folder's id, else `home`. A bucket switch empties the composer at once and
then reads the stored conversation; a draft typed while that read is in
flight stays (opening a history entry still empties it). Meanwhile the
transcript shows what this tab holds of the bucket from the switch's
first render: its own copy (a reply sent here), else what the dock last
showed of it (the session's `remember` / `recall`, the last twenty
conversations read or made here), which the stored copy then replaces in
place. Its rows are its own (keyed by the bucket and their place), so
nothing of the conversation before opens, folds or morphs into it, and an
empty one known this way shows its empty line, or the setup card, at once.
A bucket never read stays blank until it is read, rather than saying it is
empty; what it showed meanwhile is not remembered, so coming back to it
before its read lands is blank again. A remount of the
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
older messages match by content. A user message's `images` are the pictures
it sent, by URL ("Pictures" in [ai.md](ai.md#pictures)): a stored upload or
a region of a PDF page the server draws, never the bytes. An older chat may
hold data URLs; they show but are never sent again. The request's history
carries them, so the newest stay in the model's view. A save that fails on
the network or with a 5xx is retried (1, 3, 8 s). One that still fails, or a refusal, marks the
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
  files it into history, clears the active row and answers with the entry's
  `id` (null when nothing was filed). The title is the user's,
  else the first user message's first non-quote line (`derive_title`). The
  client sends its own copy of the messages, so a reply still inside the
  500 ms autosave debounce is kept. An empty conversation archives to
  nothing. A stored conversation newer than the copy (another tab kept
  talking) is archived too, never deleted. Whichever of the two holds every
  message of the other is archived alone; otherwise both are.
- **Alt+click on New chat** throws the conversation away instead: the same
  call with `discard: true` clears the active row and files nothing. There
  is no confirm and no undo. Instead, Alt held over the button turns it into
  **Discard chat** (red, a trash icon; [Alt variants](ui-design.md#alt-variants))
  before the click. An empty chat has nothing to discard, so the button
  stays New chat. A stored conversation newer than the copy that holds
  messages the copy lacks is still archived, so another tab's work is never
  lost.
- The **History** button (clock icon) opens a popover listing the active
  conversation first (highlighted, "now") and then the bucket's archived
  ones newest-first (`GET /chat-history?bucket=`; title, age, message count
  in the tooltip), with a search box filtering on title + first message.
  Each bucket's list as last read stays while the dock is mounted, and an
  archive or an open made here changes it as the server did: the
  conversation filed leads it, under the id the call answered with, and an
  entry opened leaves it. After a page change, an archive or an open the
  popover opens on that list and reads it again in place, and an open that
  fails reads it again too (another tab may have opened or deleted the
  entry). A first read shows "Loading…" only when it is slow
  (`.loadingHint`, [Motion](ui-design.md#things-that-change-in-place)).
  Clicking an entry POSTs `/chat-history/{id}/open` with the current
  conversation and its version: the current one is archived (a newer stored
  one too, as for New chat), the entry becomes the active row and leaves
  history, and the answer carries the new version and `archived`, the id the
  current one was filed under (null when it was empty). A conversation is
  always in exactly one place.
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
- **Every agent's conversations.** While the chat has a computer's agent
  ([ai.md](ai.md) "The picker"), History reads the bucket's conversations
  with it beside these (`GET /api/local-agents/chat/history?bucket=`:
  the archived ones and its active one, `active`, of whichever agent) and
  lists all of them together (`chat/computer/computerChat.js`
  `historyRows`): the conversation shown first, then by time Gamma AI's
  archived ones, the computer agents' archived ones, and each agent's
  current conversation while another agent is shown (Gamma AI's active
  one, or the computer's `active`), every row led by its agent's icon. A
  row opens with its agent picked: one of Gamma AI's as above, one of a
  computer agent's through `POST /api/local-agents/chat/history/{id}/open`
  (the active one there archived), and an agent's current one by picking
  that agent alone; while a computer agent's turn is to end its other rows
  are greyed, with why in their title. Rename is Gamma AI's; Export, Select
  and Delete (`DELETE /api/local-agents/chat/history/{id}`) work on either,
  and a bulk Delete sends each to its own table.
- **Sessions started elsewhere.** With a computer's agent picked (this
  computer's, or another computer's of the reader's, asked over its
  channel), History lists under *On {computer}* the sessions its CLI keeps
  of the directory it works in, started in a terminal or an editor (`GET
  /api/local-agents/sessions`, read each time the popover opens), in the
  same rows: title, time, what it was started from or *In Gamma*, a busy
  one greyed with why (and every row while conversations are ticked or an
  open is on its way); another computer offline, too old to be asked or
  not answering yet says so in place of the rows. A row opens it as the bucket's conversation, whose
  next turn resumes the session ([local_agents.md](local_agents.md)
  "Sessions started elsewhere").
- History follows its bucket: a folder delete moves its entries into
  `home`'s with the active rows, and `ops.delete_page` drops the active row
  and the entries of a page deleted for good, in the deleting transaction
  (a page in Recently deleted keeps its chats). A Gamma export carries its pages' buckets whole (the active
  conversation and the history; on a folder export the folder views'
  buckets too), and a backup merge or an import adds every conversation
  the workspace lacks — a bucket's active one, an archived one by its id
  (`db.copy_chats`); a replace restore brings the backup's chats with its
  pages.

## Exporting a conversation

The chat header's download menu → "Export conversation", or a History row's
menu → "Export", saves a conversation as a file to keep. It holds what the
chat shows, with every picture beside it (`gamma/chat_export.py`). The other
entry of the header's menu, "Export context", is the prompt the model is
sent ([ai.md](ai.md#exporting-the-context)).

- **The file.** `<title>.zip` holds `<title>.md` and an `assets/` folder
  with one file per picture. A conversation that shows no picture is the
  bare `<title>.md`. The title is the user's, else the first question's
  first line (`derive_title`).
- **The transcript.** An `# title` heading, then a line naming whose chat it
  is ("AI chat of “Paper” · 14 messages · exported …"), then one section
  per message. `## You` lists the message's pictures, its attached PDFs,
  the pages put in context and the text as typed. `## AI · model · effort`
  shows the selection pictures the model got, then **Steps** (each tool
  chip's summary, with the picture a view or ink chip expands to), then the
  reply's Markdown. A failed reply is one line with its error, and a reply
  that broke off says so. Reasoning, tool output, approvals and usage are
  left out: they aren't part of the conversation as read.
- **The pictures.** Every picture is read or drawn now in the request's
  workspace, the way the chat shows it (`ai_pictures.picture_at`). That
  covers a message's stored uploads, its PDF regions, an older chat's data
  URLs, the chips' pictures, the selection crops on the coverage report,
  and the `/api/uploads/…` and `/api/ai/…` images a reply embeds. A reply's
  fenced code stays as written. Each picture is written once, named by a
  hash of its bytes, and linked as `assets/<hash>.<ext>`. A picture that
  can no longer be drawn becomes "*(picture no longer available: …)*" and
  never fails the export. Absolute URLs and data URLs inside a reply stay as
  they are.
- **Routes.** `POST /api/chats/export` `{bucket, messages, title}` takes
  the open conversation from the tab's copy, so a reply still in the
  autosave debounce and a viewer's unsaved conversation come along.
  `GET /api/chat-history/{id}/export` reads an archived one. Both take any
  member (`require_ws`), a viewer too. They answer in the request, like the
  context export, and are not a background job.
- **A computer agent's conversation** exports for its owner alone, active
  or archived, from the server's copy (`GET
  /api/local-agents/chat/history/{id}/export`, 404 for another account's):
  the same file, headed "Chat with Codex of …", with the person's messages
  and the passages they carried (each quoted with its page), and the
  agent's with its steps in words ("Read sections/intro.tex", "Not allowed
  to run curl", `chat_export.step_text`), its reply, the files it changed
  ("**Changed · 2**", "The PDF was rebuilt") and how a turn ended that did
  not answer ("*Stopped.*", "*This turn failed: …*"). Such a conversation
  holds no pictures, so it is always the bare `.md`.

Covered by `backend/tests/test_chat_export.py` (a computer agent's by
`test_local_agents_chat.py`'s `test_a_turn_runs_and_its_reply_is_kept`),
the e2e step "a picked
picture is stored and attached by URL" (the zip and its two pictures) and
"Alt+click New chat discards the conversation" (a History row's export).
