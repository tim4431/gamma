# Mentions and reminders

Typing `@` in a note opens a menu with three sections, as in Notion:

- **Date**: Today, and "Remind me", tomorrow at 9 by default.
- **People**: the workspace's members (you first, marked "(You)"), then
  Invite….
- **Link to page**: pages by title, each with its folder path.

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

`editor/BlockTree.jsx` owns the trigger, the keys and the insertion, like
the `/` menu's. The rows come from `mentionRows` in `mentions.js`. The popup
is `MentionMenuPopup` in `editor/MentionMenu.jsx`: the `[[` picker's
surface, rows and footer, with a person's `PeerAvatar` in the icon column.

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
  `remind` (`remind me fri 4pm`) offers only the reminder. A date more
  than a day away shows its weekday beside it.
- **People.** The workspace's members are matched by the start of the
  username, then the start of one of its parts (`k` finds `bob.k`), then the
  `/` menu's fuzzy match (`fuzzyIndices`). Invite… follows when the editor
  may share the page (App's `onInvite`: not a viewer, a share view or a
  guest).
- **Link to page.** Pages come from `rankRefPages`, the `[[` picker's
  ranking, which gives the recent ones before anything is typed. The open
  page is never offered.
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
"<username>"'`, which reads at most 500 candidate blocks. The blocks' text
is then read again with the regex, outside code (`reminders_in`). The
query leaves out:

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

**Done.** A dismissed reminder's key (`<block id>/<date>/<time>`) goes into
the account-wide `reminders-done` pref (`db.REMINDERS_DONE_PREF_KEY`),
which keeps the latest 500. Changing a reminder's time makes it a new
reminder. `POST /api/reminders/done {keys, done}` writes the pref
(`done: false` brings a reminder back), and it holds in every browser.

**Not built.**

- Nothing reminds anyone while no Gamma tab or desktop window is open: the
  server sends no e-mail or push.
- Being mentioned notifies no one: there is no inbox. The notes index could
  find a person's mentions the same way it finds their reminders.
- The menu only offers "Remind me". A reminder for someone else is typed by
  hand (`(remind @bob)`).

## Tests

- `tests/shared/mentions.json`, read by `backend/tests/test_shared_fixtures.py`
  and `frontend/tests/mentions.test.mjs`: the syntax.
- `frontend/tests/mentions.test.mjs`: labels, the date words, the trigger,
  the rows and each pick's text. `frontend/tests/reminders.test.mjs`: due,
  the wake-up moment and a card's line.
- `backend/tests/test_mentions.py`: the people endpoint, reminders across a
  personal and a shared workspace (another person's, one in code, one in a
  workspace its person cannot open), a page in Recently deleted, the done
  list per account.
- Browser: the `note-mentions` group (`tests/e2e/scenarios/noteMentions.mjs`).
  It covers the bare menu's sections, each pick's text and chip, a name no
  one has, Escape, Invite… opening the Share popover, and a reminder due in
  another workspace: its card, Open, and a dismissal that holds after a
  reload.
