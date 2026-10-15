# What the tours do not teach

Surveyed 14 October 2026 (dev at `8246adff`): every user-facing control in
the chat, the app shell, the PDF viewer and handwriting, the notes editor,
the library and Settings, checked against the 15 tours and 12 hints in
`frontend/src/guide/tours/` and the anchors in `guide/anchors.js`, and
against the commits since 15 September. Findings only. How the guide works
is in [dev/onboarding.md](../dev/onboarding.md); the October review that
shaped today's tours is the last section of [onboarding.md](onboarding.md).

Today's cards carry about 760 words (step and hint titles and bodies,
English). Most gaps below are single controls or gestures, which is why
the proposals that came out of this survey lean on hints offered after the
action rather than on longer tours.

## Closing a window and getting it back

Nothing teaches either. The Arrange windows tour had a "Reopen a closed
window" step on the View menu until the October 2 rewrite (`858d5f75`)
removed it with its Chinese strings; `contextualGuide.mjs` still checks
close and reopen through View.

- **Closing.** Each docked window's × (`DockWindow`, `shared/ui/Widgets.jsx`)
  hides it; its tooltip, "Close window (reopen from the View menu)", is the
  only hint in the product. The PDF's × sits top-right of the viewer
  ("Close PDF", or "Back to the notes" in the notebook view). Notes has a ×
  only beside a visible PDF; elsewhere the notes fill the centre.
- **Getting it back.** View (≡, `header.view`) → Windows → **AI Chat**,
  **Notes**, **PDF** (or **Notebook view**). The palette's "Show or hide
  the chat / notes / PDF" commands have no default key. The chat also
  comes back through a library card's "Ask AI about this page",
  "Transcribe with AI" on a drawing, and the tours that `show` it; the PDF
  through the paperclip popover's "Show the PDF", a highlight's note or a
  chat citation.
- **Why it bites.** Visibility is saved with each page's layout, and a page
  without one copies what is on screen, so a chat closed once stays closed
  on every new page. Closing Notes also hides the page title, labels and
  the metadata button, which live in its header. A Ctrl-drag box, a click
  on a highlight or a Ctrl+click on a note still attach to a closed chat
  without opening it, so the attachment seems to vanish.
- **Phone.** The × only returns to the main view; the bottom bar's Chat
  tab (no anchor) brings it back. `app.toggleChat` flips `chatHidden`,
  which the phone panel ignores, so the command does nothing there.

## The chat

- **New chat and History.** A plain click on **+** archives the
  conversation into History; Alt+click (`8c0ee23e`, 14 October) discards
  it with no confirm and no undo. Only the tooltip says so. History (the
  clock, no anchor) is the only way back to an old conversation: a click
  on a row reopens it and archives the current one; right-click or ⋯
  renames, selects or deletes.
- **Untaught since September:** the model chip (model, effort, the speed
  tier of 1 October), the PDF switch that sends the whole file with every
  message by default (12 October), the change list with per-row undo and
  redo of what the assistant edited (30 September), the "Allowed in this
  chat" chip, Find (Ctrl+F inside the chat), Export context, Save to
  library and the blocked-paper cards. The approval card has a hint.
- Registered anchors no step uses: `chat.context`, `chat.settings`.

## The PDF and handwriting

- **Highlights after they are made.** A click on a highlight scrolls to its
  note and quotes it into the chat (Ctrl+click appends); right-click or a
  long-press is the only way to recolour, link or delete one on the PDF;
  the note badge previews the note; the note's dot jumps back. None of it
  is taught, and none of it has an anchor.
- **Untaught tools:** the Text tool and text boxes (30 September, the
  largest PDF feature without a tour), the ink selection's menu and its
  resize and rotate handles, the strip's keys (1–9, P, H, E, L, T, V) and
  the Hand tool, Find in the PDF (`header.search` is registered and
  unused), the page-number box, full screen (`viewer.fullscreen` is
  registered and unused), translating a selection.
- The notes side's ⊕ "Attach to a PDF highlight" shows its banner, but
  nothing reads attach mode when a highlight is clicked
  (`attachModeBlockIdRef` is written and never read), so it never links.

## Notes

- **Enter.** By default Enter breaks the line and Shift+Enter starts a new
  note (`enterNewNote`, swappable in Settings → Keyboard), the reverse of
  most outliners. Only the Welcome page says so. `block.created` and
  `block.indented` are catalogued and still emitted nowhere.
- **No anchors and no teaching:** the ⋮⋮ handle (drag to reorder, more than
  50 px right to nest, click for Copy link to block, Duplicate, Move to
  page…), the **+** under it (Alt+click adds above), the fold arrow, the
  `/` menu as a whole (about twenty inserts, fuzzy matching, `/red` for
  colours; tours type only `/table` and `/note`), Paste as (Mention or
  Synced block for a block link, a titled link for a URL, At this level or
  Nested for several lines, 11 October), `@` mentions and reminders
  (9 October), the object menu on pictures, tables and diagrams.
- The math-keys hint says Tab jumps to the next `{ }`; since 6 October Tab,
  not Enter, also accepts a LaTeX suggestion, and the hint doesn't say so.

## The library and Settings

- **Import** (a Zotero folder or .zip, Obsidian, Notion, Logseq, a Gamma
  export) is a View-menu row with no anchor; the Add tour never mentions
  it. Dropping files and folders onto the library is mentioned only by the
  empty-library card.
- **The listing bar:** the filter box (Ctrl+F on the library; Enter with no
  match searches inside notes and PDFs), Sort with Custom order, the
  Labels mode that browses labels as folders, and Select mode with batch
  menus. Only the bar as a whole is an anchor.
- **Folders:** folder sharing (one link to every page filed there, now and
  later) and the folder menu; the sharing tour requires an open page.
- **Paper details:** the ⓘ popover (item type, cite key, Verify, the
  Fetched date of 14 October; a red ! for unverified), and the BibTeX
  export's "Keep this .bib up to date" link for Overleaf.
- **Settings** has no anchors, and offers wait while it is open; only
  Connections (from the AI chat tour) and Gamma Cloud (a hint) are ever
  pointed at.

## The shell

Ctrl+Shift+P (or `>` in Ctrl+P) is the only way to the unbound commands and
is untaught; so are Ctrl+P's Shift+Enter (create a page) and Ctrl+Enter
(search inside), tabs (pin, middle-click to close, the all-tabs menu), F2,
Delete and Ctrl+, .

## Smaller findings

- Untranslated chat strings: the Tools button's aria-label, three composer
  placeholders, "{n} selected" and the History age "now"
  (`chat/ChatDock.jsx`).
- In Chinese, 聊天 names the Chat window and 对话 a conversation, but two
  tour strings use 对话 for the window (Arrange windows' offer line, the
  finish card's AI chat tile), and two export commands say 资料库 where
  everything else says 文库.
- Stale docs: the user guide says card chips filter the library;
  `FileBrowser.jsx` places the chips setting under General; `handwriting.md`
  names a "New group" control with no UI; `ai.md` says a picture dropped on
  the chat attaches.

## What was taken

The survey's 33 proposed changes and 6 fixes went to review on 14 October,
each with its cards in English and Chinese. Built
([dev/onboarding.md](../dev/onboarding.md)):

- **Windows:** a hint right after a window is closed, pointing at View and
  retired when one is brought back. The palette's Show or hide commands now
  work on a phone.
- **Chat:** hints after the first New chat (History, Alt+click), the first
  message sent with the whole PDF, and the first reply that changed things;
  the AI chat tour gained the model chip, a Ctrl+click on a note, and New
  chat as its last card.
- **PDF:** a Your highlights tour, the Text tool in the handwriting tour, a
  hint on a selection of ink, a translate card that names the selection.
- **Notes:** a Moving notes around tour, hints for Shift+Enter, the / menu,
  Paste as, @ mentions and a selected object; the math hint says Tab
  accepts. The ⊕ attach now links the highlight clicked next.
- **Library:** Import ends the Add tour, which also says files can be
  dropped; Organize your library gained the filter, Select and the Labels
  view; the sharing tour covers a folder; hints for unverified paper
  details and a folder's .bib link.
- **Shell:** hints for the command palette, Settings and pinning tabs.
  A middle-click no longer closes a tab.
- **Copy:** 聊天 for the Chat window throughout, 文库 for the library, the
  untranslated chat strings, a citation's preview.

Turned down: the Arrange windows close-and-reopen steps (the hint does
that job), a hint for an attachment landing in a closed chat, a Ctrl+F
hint, full screen and page-number steps in the viewer tours, and a default
key for showing the chat.
