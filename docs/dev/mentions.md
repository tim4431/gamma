# Mentions and reminders

Typing `@` in a note opens a menu with three sections, as in Notion:

- **Date**: Today, and "Remind me" (or "Remind @bob"), tomorrow at 9 by
  default.
- **People**: the workspace's members (you first, marked "(You)"), then
  Invite….
- **Link to page**: pages by title, folder, label or path, each with
  where it is (its folder, or for a page inside another the pages it is
  in).

Each pick writes plain text into the note. The editor and the rendered view
draw that text as a chip.

## The syntax

| Text | Meaning |
|---|---|
| `@2026-10-09` | a date |
| `@2026-10-09 14:30` | a date with a time: a wall-clock time with no zone |
| `@2026-10-10 09:00 (remind @alice)` | a reminder for the account named `alice`; without a time it is due at 09:00 |
| `@alice` | a person, by username |
| `[[id]]` | a page: the `[[` picker's link ([ui-design.md](ui-design.md#file-map-frontendsrc)) |
| `[[id\|words]]` | the same link showing its own words in place of the page's title ("Page links with words of their own" below) |

Because a mention is readable text, every reader that knows nothing of it
still shows something that makes sense. That covers the exports, the PDF
typesetting, the AI's reads, search and the iPad's plain editor. The rules:

- The `@` starts a word. It may not follow a letter, a digit, `@`, `/`,
  `.`, `+`, `-` or a backslash, so an e-mail address, a path such as
  `medium.com/@writer` and an escaped `\@` are not mentions. The classes are
  ASCII, so Chinese text may run straight into a mention (`请@bob看一下`).
- A username is what an account may be called (letters, digits, `_`, `.`,
  `-`), less a trailing `.` or `-`, which end the sentence.
- A date that does not exist (`@2026-02-30`) or an impossible time is no
  mention. Neither is a date run on into other characters (`@2026-10-101`).
- A mention inside code (a fence or inline code) or math is not read. Nor
  is one inside a Markdown link's text or an HTML tag, where a chip would
  break the link around it.

`frontend/src/editor/mentions.js` (`MENTION_RE`, `scanMentions`) and
`backend/gamma/mentions.py` (`MENTION_RE`, `scan`) hold the same rule.
`tests/shared/mentions.json` pins both sides, and a case added there fails
whichever side drifts.

## The @ menu

`editor/useMentionMenu.js` owns the trigger, the keys and the insertion,
for a note row (`editor/BlockTree.jsx`) and a comment's field, which has the
same menu without "Remind" rows (the reminder scan reads notes only) or
"Invite…" ([comments.md](comments.md) "The composer"). The rows come from
`mentionRows` in `mentions.js`. The popup is `MentionMenuPopup` in
`editor/MentionMenu.jsx`: the `[[` picker's surface, rows and footer, with a
person's `PersonAvatar` in the icon column.

- **Opening.** An `@` typed at a word start opens the menu
  (`mentionTrigger`). While the menu is open its query may hold spaces, so
  `@next fri` and two-word titles keep it open. A line break, more than 40
  characters, a selection, math, a code fence or a `[[` closes it, and so
  does a query nothing answers. The exception is a date still being typed
  (`datePending`: every word a number or the start of a word the dates
  know, such as `next`, `in 3 d` or `tomorrow 3`). It keeps the menu alive
  but hidden, with no rows, until the phrase means a date. Escape closes the
  menu and keeps the text.
- **Date.** A bare `@` offers Today and a reminder for tomorrow at 9. A
  query offers the dates it names (`parseDateQuery`) and a reminder at the
  first of them. The words it reads are English:
  - today, tomorrow, yesterday and their prefixes (`t` is both today and
    tomorrow), plus `now`, `tmr` and `tmrw`;
  - weekdays: the coming one, `this fri`, `next fri` (the Friday of next
    week, weeks starting on Monday) and `last mon`;
  - `in 3 days`, `2 weeks ago`, `next month`;
  - month names (`oct 12`, `12th October 2027`) and `YYYY-MM-DD`;
  - an optional trailing time: `3pm`, `9:30`, `noon`. A bare number is a
    day of the month, never a time.

  A time alone is today. As a reminder, it is tomorrow once that time has
  passed, and a moment already gone reminds no one. A query starting with
  `remind` (`remind me fri 4pm`) offers only the reminder. `remind @bo
  fri` offers one for each member the name starts (`remindTargets`), and
  `remind bob fri` for the member it names in full; any other word after
  `remind` is read as the date, for you. A date more than a day away shows
  its weekday beside it.
- **People.** The workspace's members are matched by the start of the
  username, then the start of one of its parts (`k` finds `bob.k`), then the
  `/` menu's fuzzy match (`fuzzyIndices`). Invite… follows when the editor
  may share the page (App's `onInvite`: not a viewer, a share view or a
  guest).
- **The agents.** A comment's field lists the agents a comment on its page
  can be sent to (the field's `links.agents`, `chat/agents.js`
  `commentAgents`: the chat's computer agents for the page, this
  computer's that the account turned on or another account lends it, or
  the one of the computer that keeps the page's work folder, the account's
  own or a lending owner's: [local_agents.md](local_agents.md) "A
  co-author's turn") in People after the people (`mentionRows`'
  `agents`, past their cap of five): a bot icon, its label (*Codex*,
  *Claude Code*) matched by name or label, and "on this computer" or "on
  TIM-PC", that computer offline (`agentOffline`), or its problem when it
  cannot run. A member of the agent's name gets no row there, since in that
  comment the name is the agent's. Picking it writes `@codex ` and ticks the
  field's *Send to Codex* ([comments.md](comments.md) "Sent to an agent");
  the comment is a turn of its thread ([local_agents.md](local_agents.md)
  "A comment sent to an agent"). A note's menu never lists them.
- **Link to page.** Pages come from `rankRefPages`, the `[[` picker's
  ranking, which gives the recent ones before anything is typed: the
  title, then as weaker hits the page's folder and label chips by name
  and, for a page inside another, the pages it is in, its folders being
  its top's ([home_library.md](home_library.md#sub-pages) "In the
  pickers"). A row names where the page is: a sub-page's path, else its
  first folder. The open page is never offered.
- **Picking** (Enter, Tab or a click) replaces the typed `@query` with the
  row's text (`mentionText`) and a space. Change and caret go in one editor
  transaction, so keys typed right after land after the mention.
  - A page's chip label is cached at once (`onCacheRef`).
  - A reminder calls App's `onReminderSet`, still inside the key press, so
    the browser's notification prompt can appear.
  - Invite… removes the typed text and opens the page's Share popover.

## Chips

`mentionChip` in `mentions.js` decides what a token shows, and both views
share it:

- **A date** reads Today, Tomorrow or Yesterday, else the full date
  ("October 12, 2026"), then its time ("9:00 AM"), in the interface language
  (`fmtDate`). Its hover title is the full date with its weekday.
- **A reminder** is accent-coloured behind an alarm clock and turns red
  once it is due. Its title says whom it reminds.
- **A person** is a chip only when someone here has that name: a member of
  the workspace, or the signed-in account. Any other `@name` stays text.
- **An agent a comment can be sent to** (`agents`, given to a comment
  field and to the person's own posted comments only, never through
  `MentionContext`): its exact name is the agent's chip, a bot behind it
  (`.mention-agent::before`), before a member of the same name.

The rendered view's `mdPreprocess` (BlockTree.jsx) turns each mention,
outside math and code, into a `mention:` link (`mentionMarkdown`), which
BlockMarkdown draws as `MentionChip`. The editor's `buildInlineDecos`
(BlockCmEditor.jsx) replaces a mention with `MentionChipWidget`. It claims
the range before the inline marks, so a username's `_` never reads as
emphasis. As with a `[[ref]]`, a caret touching the chip shows the source,
and a press on it puts the caret after the `@`. To change a date, edit its
text; there is no date picker.

App provides two contexts (`MentionMenu.jsx`). `MentionContext` holds who
can be mentioned, `{me, people, known}`. It changes rarely, and the rows and
the editor read it. `MentionClock` is the reminders' clock, which moves when
a reminder falls due, at midnight, and at least hourly. Only the chips read
it, so a tick re-renders nothing else; memoized rows keep their markdown.
The reminder chip's alarm clock is a CSS mask (`.mention-remind::before`),
which the widget and the rendered chip share.

## Page links with words of their own

`[[id|words]]` is a `[[id]]` link that shows `words` in place of its
target's title or first line. The order is Obsidian's and MediaWiki's
(Dendron reverses the two, Roam and Logseq write `[words]([[page]])`). It
links by id, so a renamed page keeps it, and it counts in backlinks. The
rules:

- The id comes first. The words run to the `]]`, with no bracket or line
  break in them.
- In a table's row the pipe is `\|`, as Obsidian writes it, since the table
  tools split cells on a bare `|` (`mdScan.js` `splitCells`). Both
  spellings read the same.
- Blank words (`[[id| ]]`) are none: the chip shows its target's label.
- An embed's words are none: `![[id|x]]` embeds the block as `![[id]]` does.
- A hand-typed `[[title|words]]` that no page answers is a dashed unlinked
  chip showing its words.

The words are set in the link's card ("The link card" below): its ✎, then
Text. By hand, pick the page in the `[[` picker and type `|words` before
the `]]`, or type `[[Exact title|words]]`, which links when one page has
that title. A `|` typed in the picker's query closes the picker, since what
follows is the link's words. The chip shows the words. In the editor its
hover title names the target (`refTargetTitle`), and in the rendered view
its card does. The caret touching a link in the editor shows its source.

`frontend/src/editor/refLabels.js` (`REF_RE`, `EMBED_RE`, `CHIP_RE`,
`refWords`) and `backend/gamma/note_markup.py` (`BLOCK_REF_RE`,
`ref_alias`, `ref_token`) hold the one syntax for every reader. Every
reader that writes a link as text keeps its words: the readable Markdown,
Obsidian (`[[Title|words]]`), PDF and Zotero exports and the Inbox's
snippets ([import_export.md](import_export.md)). Notion gets a link with
those words, never a page block, whose title would be the page's. Folder
sync carries them both ways ([synced_vaults.md](synced_vaults.md) "Links
and highlights").

## The link card

Every link in a note's text has one card (`shared/ui/LinkCard.jsx`), the
citation pill's preview made general. That covers a page or note link, a
web link (with a label or bare), a link into this library and a citation.
An uploaded file keeps its chip's own menu (Add to library, Download), and
a work folder's path link its own.

- **What it says** (`describeLink`), from the top:
  - A bold title: the page's title, the page a note is on, the site's title
    from `/api/link-preview`, or the cited paper.
  - A muted line of what and where: "Page · Physics", "Note", the author
    and the address shortened, "PDF page 3".
  - The note's text or the citation's quote.
  - "Open page ↵", "Open note ↵", "Open link ↗" or "Open in PDF ↵".
- **Where the note may be edited**, three quiet buttons follow it: Edit
  (✎), Copy link and Remove link. Copy link copies the page's own link for a
  page or note link, the address otherwise.
- **Edit** turns the card into its form.
  - Text is the link's words, with the target's own as its placeholder.
    Empty words show the title, so a page link follows a rename.
  - Link is the target's token. Typing into it, or clearing it, searches as
    the `[[` picker does (`useRefRows`: pages, then notes), and an address,
    a DOI or an arXiv id offers "Link to …" first (`isLinkInput`).
  - Enter picks a row, then saves. Escape steps back.
  - Saving writes the Markdown (`linkEdit.js` `linkMarkdown`): `[[id]]` or
    `[[id|words]]` for a page or a note (`\|` in a table's row), and
    `[words](url)` or the address alone for an address. A bare address that
    would lose a character is written `<url>`.
  - A web link given a page becomes a page link, and a page link given an
    address a web link.
  - Remove link leaves the words as text, or the title for a page link
    without words. A bare address has no Remove.
- **How it opens.**
  - In a rendered note, the chat and comments, a hover after a moment or
    the focus previews it. A right-click or a held finger pins it
    (`LinkHover`, `menuPress`, as Gamma opens a menu).
  - A click opens the link, and the pointer can cross into the card
    (`menuAim`). A pinned card closes on a press elsewhere or Escape.
  - In the note editor the card shows, pinned, under the link the caret
    stands strictly inside (after a press or the arrow keys). Typing hides
    it, and Escape closes it before it closes the editor.
  - Ctrl+K and the Link buttons of the selection toolbar and the editing
    bar open its form (`linkCardHost.js`). The form edits the link the caret
    is on, or links the selected words, with the cursor in Link.
  - The form's fields take the focus without ending the editing session,
    and a press outside both ends both. The editor's side is
    `editor/editorLinks.jsx` (`useEditorLinkCard`).
- **A web link's chip.** The rendered view and the note editor both draw a
  web link as a chip (BlockTree.jsx `LinkChip`, editorLinks.jsx
  `LinkChipWidget`).
  - It shows the site's icon and `webLinkLabel`'s words
    (`shared/lib/webLinks.js`): a Markdown link's own, else GitHub's
    readable name, the site's title or the host. The address is its hover
    title.
  - The editor shows a link's source only while an end of the selection
    stands inside it (the arrow keys step in) or while it is being typed.
    A link typed or deleted into stays source until the caret leaves it
    (`typedLink`).
  - A link the note opened with, the card wrote or a paste put there is a
    chip from the start, and a caret at its edge keeps it one.
  - A press on the editor's chip puts the caret after it and shows its
    card, pinned. With Ctrl or Cmd it opens the link.
- **The site's title** comes from `/api/link-preview` (backend
  `gamma/link_preview.py`), which `loadSitePreview` asks once per address.
  A request that failed is asked again the next time. Paste as → Titled
  link writes the same title, or the site's name when there is none.
  - YouTube, X, Reddit, TikTok, Vimeo and Spotify answer through their
    oEmbed endpoints. An X post's text is its title.
  - Any other page is fetched with a browser's headers in the reader's
    language, and its head read: `og:title`, else `twitter:title`, else
    `<title>`, and its `author`. The site's name is cut from either end of
    the title ("…_哔哩哔哩_bilibili", "GitHub - …").
  - A page that is not the one asked for gives none: an HTTP error, or a
    redirect to a sign-in, visitor, check or 404 page or to the home page.
    Nor does a title that is only the site's name or an error's, and the
    chip then shows the host.
  - What each platform answers a server:
    [research/link-previews.md](../research/link-previews.md).
- **Which link a rendered card edits.** The rendered view knows a link by
  its target only, so BlockMarkdown counts its links per target as it
  renders them. The nth link of a target is the nth in the source
  (`linkEdit.js` `linkSpans`, `linkKey`, `nthLinkSpan`).
  - The scan reads the targets as the renderer does: outside code and math,
    a bare address ending where GFM ends it. A link it cannot find offers
    no edit.

An address pasted over selected words links them, in every note editor
(`pastedLink`). It makes `[words](url)`, or `[[id|words]]` for a copied
link to a page or a note of this library. It leaves alone a selection in
code or math, over more than one line or holding a link, and a clipboard
with files. One undo takes the paste back whole. With nothing selected an
address pastes as text, followed by the note row's "Paste as" chooser.

## People

`GET /api/people` lists the explicit members of the request's workspace,
`[{username, role}]`, owners first. Any member may read it; a share link's
visitor, who is no member, may not. App reads it once per workspace. A share
view mentions no one: its menu has no people and no reminder, and its person
mentions stay text.

A person is named by username, so the server can find a person's mentions
through the notes index, and a share view reads the name without a lookup.
The cost: an account renamed later (`rename_account`) leaves its earlier
mentions under the old name, as plain text.

## Reminders

**Who and where.** A reminder reminds the account its token names.
`mentions.reminders_for` looks in every workspace the account can open.
Each workspace gets one notes-index query, `block_fts MATCH '"remind"
"<username>"'`, which reads every block with both words in it, however
many there are. The blocks' text is then read again with the regex,
outside code (`reminders_in`). A workspace whose query fails (a damaged
file, an upgrade that failed) is logged and left out, and the others are
listed all the same. One workspace with 5,000 reminders beside as many
other notes answers in about 50 ms, and 20,000 in about 200 ms. The listing
leaves out:

- a page in Recently deleted;
- text past the index's `NOTES_INDEX_CHARS`;
- a block that a clone or a publication copies under the same id, which is
  listed once, from the workspace that is neither.

A reminder for someone in a workspace they cannot open reaches no one.

**When.** A time has no zone. The browser that shows the reminder decides
when it is due, in the zone it runs in, so the server never compares times.

**Delivery** (`app/useReminders.js`, one instance in App). It reads
`GET /api/reminders` on load, every 5 minutes, when the window comes back,
and 2.5 s after a "Remind me" pick (once the note is saved).
`reminderState` (`app/reminders.js`) gives the reminders due and not
dismissed, and the moment to look again: the next reminder, midnight or an
hour on.

- Each reminder due is a card in the bottom right corner
  (`app/ReminderAlerts.jsx`, above a phone's bottom bar). A card shows when
  it is due, its note's text without the reminder, and the page. Open goes
  to the note: `openBlockLink` in this workspace, or a navigation to
  `?ws=&page=&block=` for another one, since switching workspaces is a
  navigation. A × dismisses the reminder. Past three cards, one line offers
  to dismiss them all.
- A reminder due within the last 12 hours also makes a system
  notification, once per browser (`gamma-reminders-rung` in localStorage,
  the latest 200), when the browser allows notifications. Gamma asks the
  first time "Remind me" is picked. The notification's tag keeps two tabs
  to one notification, and a click on it opens the note.

**Done.** A dismissed reminder's key (`<block id>/<date>/<time>`) is a row
of `users.db` `reminders_done`, with the account, the workspace a listing
last found the reminder in and when it was dismissed. A block id has no
`/`, and a block keeps its id through edits and moves, so a key names one
reminder for as long as its text stays. Changing a reminder's time makes it
a new reminder, and so does moving its text into another block.
`POST /api/reminders/done {keys, done}` writes the rows, at most 100 keys a
request (Dismiss all sends its keys 100 at a time), and `done: false`
brings a reminder back. The rows hold in every browser.

There is no cap on the marks. Each listing settles them instead
(`mentions._settle`):

- A mark takes the workspace its reminder was found in. A new one has none
  until the next listing, and neither have those migration step 39 moved
  from the old `reminders-done` pref.
- A mark whose reminder the listing did not find is dropped when the
  listing read its workspace whole: the block was deleted, or the reminder
  edited away. A page in Recently deleted still holds its reminders, so
  restoring it brings them back done. A mark with no workspace yet needs
  every workspace read, and a mark made after the listing began is never
  dropped by it.
- A workspace that could not be read, or that the account can no longer
  open, keeps its marks, so leaving a shared workspace and coming back
  finds them.
- The marks of a deleted workspace or account go with it.

So the rows hold the dismissed reminders that still exist, and those of the
workspaces out of reach. A block deleted and brought back under its own id
after a listing has run (an undo) is not done any more. The marks are a
table, not a pref: one is about 60 bytes of JSON, so the prefs' 64 KiB
(`routers/prefs.py` `MAX_VALUE_BYTES`) would hold about a thousand. A
person who sets a reminder a day passes that within three years.
`GET /api/reminders` answers the done keys among the reminders it lists.

**Not built.**

- Nothing reminds anyone while no Gamma tab or desktop window is open: the
  server sends no e-mail or push.
- Mentions themselves are not listed from the notes index: the Inbox is
  told when a note gains a person's mention, and keeps that as an item
  ([comments.md](comments.md) "The inbox"). `mentions.people_in` is the
  rule it diffs by: the person mentions outside code.

## Tests

- `tests/shared/mentions.json`, read by `backend/tests/test_shared_fixtures.py`
  and `frontend/tests/mentions.test.mjs`: the syntax.
- `frontend/tests/mentions.test.mjs`: labels, the date words, the trigger,
  the rows and each pick's text. `frontend/tests/reminders.test.mjs`: due,
  the wake-up moment and a card's line. `frontend/tests/refLists.test.mjs`
  and `subPages.test.mjs`: the page rows' matching (chips by name, a
  sub-page's path and its top's folders).
- `backend/tests/test_mentions.py`: the people endpoint, reminders across a
  personal and a shared workspace (another person's, one in code, one in a
  workspace its person cannot open), a page in Recently deleted, the done
  list per account, 620 reminders all done, a reminder after 600 other
  blocks the query matches, and which marks a listing drops: those of a
  deleted block and a reminder edited away, but not those of a page in
  Recently deleted, a workspace that failed or one the account left.
  `backend/tests/test_migrations.py`: step 39's move of the old pref.
- Browser: the `note-mentions` group (`tests/e2e/scenarios/noteMentions.mjs`).
  It covers the bare menu's sections, each pick's text and chip, a name no
  one has, Escape, Invite… opening the Share popover, and a reminder due in
  another workspace: its card, Open, and a dismissal that holds after a
  reload. The `sub-pages` group finds a page inside another by its label's
  name, its path named on the row.
