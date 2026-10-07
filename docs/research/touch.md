# The web app on a touch screen with no keyboard

Survey from 2026-10-03, of the `dev` working tree while other work was in
progress (the `/page` → `/note` rename among it). The question: what can
someone do in the web app on an iPad with fingers and an Apple Pencil, and
no hardware keyboard? The method was a source audit, plus Playwright runs in
Chromium touch emulation at 834×1194 and 1194×834 (DPR 2) against the e2e
harness's throwaway server. Nothing was run on a real iPad. Anything only
the device can show is marked **device check**. Current mechanics are in
[dev/ipad.md](../dev/ipad.md), [dev/handwriting.md](../dev/handwriting.md)
and [dev/notebooks.md](../dev/notebooks.md).

## Main finding

The writing surfaces were built for touch. The notes outliner, the library
and the menus were built for a mouse and a keyboard.

- **Built for touch:** Pencil-only ink with palm rejection, two-finger pinch
  and pan, the notebook view, text boxes, lasso moves and the ink menu all
  carry pointer-type rules and their own e2e steps.
- **Built for mouse and keyboard:** around them, structure is changed with
  Tab, Shift+Enter and Ctrl chords; actions sit in right-click menus; and
  tools appear on hover.

The reported symptom ("a drawable page needs `/note`") is half of this:

- **New notebook works by tap.** Add → New notebook makes a new page with
  one sheet. Add sits on the compact bottom bar, so it is one tap away
  (measured).
- **A sheet in an existing page does not.** That includes a PDF's notes.
  The only ways in are typing `/note` in an open block (`SlashMenu.jsx`), or
  the ⋮⋮ handle's "Add page below" (`BlockTree.jsx`).
- **The handle can't be seen.** It is `opacity: 0` on every row at rest and
  shows only on the row being edited (measured, 18×22 px).

The native iPad app has an "Add a page to write on" row at the end of the
notes. The web app has no counterpart.

## What needs a keyboard, a mouse or hover

**Inserting.**
- The insert list (handwritten note, table, image, math, code, callout,
  headings, links) opens only on a typed `/` in an open editor. Its items
  are tappable buttons once it is open.
- The row's `+` adds an empty block. Alt+click on it adds one above.

**Structure.**
- Indent and outdent are Tab and Shift+Tab. Move up and down have no chord
  and live in the command palette, which only Ctrl+Shift+P opens.
- A new block is Shift+Enter by default (`enterNewNote`, account-wide,
  default false). The soft keyboard has neither Tab nor a dependable
  Shift+Return.
- Reordering is HTML5 drag on the handle. Reports disagree on whether
  iPadOS starts such a drag from a finger at all.

**Undo and formatting.**
- Notes undo is Ctrl+Z only. The ink strip's Undo covers ink and text boxes.
- Nothing handles `beforeinput` `historyUndo` / `formatBold`. Those are what
  the iPad keyboard's shortcut bar and the three-finger swipe send.
- Bold, italic, code, strike and link are chords marked `palette: false`.
  The only touch formatting is the typed `/` list (highlight, math,
  headings, lists).

**Typing.**
- CodeMirror's defaults switch off spellcheck, autocorrect,
  auto-capitalisation and writing suggestions. Gamma sets no
  `contentAttributes`, so iPad prose gets none of them.
- A Backspace in an empty block deletes it without a focus target, so the
  editor closes and the keyboard drops.
- CodeMirror 6.43 re-dispatches iOS Enter and Backspace as synthetic
  keydowns, so keyCode 229 is not an issue.

**Menus.** iPadOS Safari never fires `contextmenu` on a long press (sources
below). About twelve `onContextMenu` sites have no other way in:
- **Library page:** Rename, Pin, Labels, Move to folder, Duplicate, Copy
  link, Share, Export, Ask AI, Delete.
- **Folder:** New page here, Rename, Delete.
- **Label:** Rename, Delete.
- **PDF highlight:** colour, link, copy as reference point.
- **Note object:** image, table and diagram Move, Copy, Delete.
- **Tab:** Pin.

Only two long presses exist: the translate button and the citation pill.
A tap opens a library item, so touch has no multi-select either.

**Hover.** These are hidden until `:hover`, with no `(hover: none)` rule:
- chat message Copy and Edit & re-send
- the chat history row's Rename, Delete and select box
- library pins and code block Copy
- the image and Mermaid toolbars
- table `+` strips
- a highlight's attach and unlink buttons

Only three `(hover: none)` rules exist: the recents ×, the chat copy tools
and ink replay.

**Size.** Measured or declared hit areas include:
- PDF column buttons 26×26, tab × 24, small × 20
- colour swatches 22 and 18
- fold arrow 12×20, row handles 18×22
- PDF note badge 15
- table handles 13–22
- the pane splitter 5 px (as drawn; its touch band is wider, see the
  [second survey](#second-survey-mouse-interactions-under-a-finger))

Only text boxes and the ink menu enlarge for `(pointer: coarse)`.

**Other one-off gaps.**
- **Area notes.** The text/rectangle toggle for area notes renders only in
  the compact layout (`isPhone`). A landscape iPad has no touch way to draw
  an area note.
- **Palettes.** Ctrl+P and Ctrl+Shift+P have no button.
- **Double-click only:** image zoom in editable notes, Mermaid source, a
  resize grip's natural size, dock collapse.
- **Add's URL field.** Opening Add autofocuses its URL field (measured),
  which raises the keyboard over the menu.

## What works

- **Ink.**
  - Fingers scroll and the Pencil writes (`inkPenOnly` defaults to
    `(pointer: coarse)`), and palms are swallowed while a pen is down.
  - The ink strip has Undo and Redo.
  - Drawing with a finger exists, behind Settings → Handwriting.
- **Zoom.** Two fingers pinch and pan in the PDF and the notebook
  (`viewerZoom.js`).
- **Text boxes.** A tap or drag makes one, a finger drags the selected box,
  and the width grip is enlarged on coarse pointers.
- **Notebook.** Sheets are added by writing low on the last one, by
  **Add page** and by a sheet's **+**. The paper menu is a plain button.
- **PDF selection.** A text selection reaches the highlight tip through a
  debounced `selectionchange` once a touch has been seen. In emulation the
  tip showed 10 of 10 times, upright and landscape.
  - Its listener re-subscribes on every App render, because its dependency
    is an inline callback, and the cleanup clears the pending timer. That is
    fragile, though it did not fail.
- **Layout.** An upright tablet gets the compact shell (measured).
- **Taps.** A tap opens a library item, and the slash menu stays above the
  keyboard (`useCaretAnchored` reads `visualViewport`).

## Platform facts and their consequences

- **No long-press `contextmenu`.** Since iOS 13, Safari does not fire it
  ([MDN browser-compat-data #6376](https://github.com/mdn/browser-compat-data/issues/6376),
  [React #21812](https://github.com/react/react/issues/21812)). Android
  Chrome does. A long press on a `draggable` element may start a drag
  instead, so a long-press menu has to switch dragging off on touch.
- **Media queries.**
  - Published measurements of iPad Safari give `(pointer: coarse)` and
    `(hover: none)`, even with a Magic Keyboard trackpad. With a trackpad,
    `any-pointer: fine` and `any-hover: hover` are also true
    ([t3code #14868](https://github.com/pingdotgg/t3code/issues/14868),
    [WebKit 209292](https://bugs.webkit.org/show_bug.cgi?id=209292),
    [CSS-Tricks](https://css-tricks.com/interaction-media-features-and-their-potential-for-incorrect-assumptions/)).
  - `main.jsx` claims the opposite for "Request Desktop Website". It sets
    `html[data-touch]` as the fallback, but nothing reads it.
  - The compact shell, the pen-only default and every `(hover: none)` rule
    depend on which is right. **Device check.**
- **Browser zoom.** Safari tabs ignore `user-scalable=no`, so a pinch
  outside the two viewers may zoom the whole app. ipad.md now says so too
  (it once said the meta prevents it). Gesture events are blocked only in
  the compact layout. **Device check**, including the installed app.
- **WebKit selection.** The PDF text layer is a bare `pdfjsLib.TextLayer`,
  without `TextLayerBuilder`'s end-of-content element and its
  `selectionchange` fix, which keep WebKit selections from jumping while a
  handle is dragged. **Device check.**
- **The callout menu.** iOS's own Copy / Look Up menu shows beside Gamma's
  highlight tip. Nothing sets `-webkit-touch-callout`, though that property
  governs link and image callouts, not the text edit menu.

## Test coverage at the time

- **Covered, in Chromium emulation:** the install pieces, the layout by
  orientation, ink input and editing, pinch and pan, text boxes by finger,
  and canvas limits at high zoom. `GAMMA_E2E_BROWSER=webkit` exists but is
  not part of a regular run.
- **Not covered:** a touch text selection, any insert, menu or structure
  operation by touch, the soft keyboard, and notes undo.

## Since

- **The editing bar** answers the inserting, structure and undo gaps for an
  open editor ([dev/ipad.md](../dev/ipad.md) "The editing bar"). For
  formatting it adds buttons for bold, italic, inline code,
  strikethrough, link and inline math.
- A note row's editor hands `beforeinput` `historyUndo` / `historyRedo` to
  the block history (`editor/BlockCmEditor.jsx`), so the keyboard's undo
  key and the three-finger swipes undo notes.
- `beforeinput` `formatBold`, `formatItalic` and `formatStrikeThrough` run
  the formatting commands.
- Indent and outdent keep the editor open, and a Backspace in an empty
  block moves on to the block above.
- The native app's notes have the same bar ([dev/ipad.md](../dev/ipad.md)
  "Editing the notes"), with "Handwritten note" in its Insert menu.
- `tests/e2e/scenarios/ipad.mjs` covers the bar by touch: structure,
  inserts, formatting and notes undo, with a stand-in for the soft
  keyboard's viewport.
- The typing aids, the hidden handle, the menus, hover-only tools, target
  sizes and the one-off gaps above but Add's focused URL field are closed
  too, after the [second survey](#second-survey-mouse-interactions-under-a-finger)
  (its closing "Since").

## Second survey: mouse interactions under a finger

Survey from 2026-10-04, of `dev`. The first survey asked what cannot be
done without a keyboard. This one asks where an interaction built for a
mouse exists but is unreliable, awkward or loses state under a finger. It
started from two reports from an iPad: a double tap did not collapse the
chat window, and switching to the notebook and back "needs improving".

**Method.** Six source audits, one per area (shell, reading and writing
surfaces, notes editor, library and settings, chat, stylesheets), and
Playwright probes in Chromium touch emulation at 1194×834 and 834×1194
(DPR 2) against the e2e harness's throwaway server. Nothing was run on an
iPad. Below, **measured** means a probe reproduced it, and everything else
is from reading the code. **Device check** means only the device can
settle it.

### The dock grip and the splitters

- **A splitter's touch band covers the headers beside it** (measured).
  react-resizable-panels 2.1.9 widens every resize handle's hit area by
  15 px a side when `(pointer: coarse)` matches (5 px otherwise). For a
  press in that band its capture-phase `pointerdown` listener calls
  `preventDefault()` and `stopImmediatePropagation()`, so the app never
  sees the press. Gamma passes no `hitAreaMargins` to its four
  `PanelResizeHandle`s.
  - In the default layout (Notes over Chat on the right) the band of the
    Notes/Chat splitter covers the upper half of the Chat header: half of
    the "⠿ Chat" grip and half of its close button. The band of the
    PDF/dock splitter covers the left 15 px of the dock column.
  - A drag of the Chat window that starts on the upper third of its grip
    does nothing. From the grip's lower edge it re-docks.
  - A tap there that moves at all drags the splitter. A double tap with
    7 px of wobble moved the Notes/Chat split by 14 px, and the header
    slid from under the second tap.
- **The grip is small.** "⠿ Chat" is 43×17 px (Notes 50×17) in a 31 px
  header, and the 24 px close button is 6 px to its right (measured).
- **Collapse rests on the browser's `dblclick`.** `DockWindow`'s grip has
  `onDoubleClick`, and `startWindowDock` counts no taps. In Chromium a
  clean double tap collapsed the window with 90 and 250 ms between the
  taps, and not with 400 ms (measured). Whether iPadOS sends `dblclick`
  here, and whether a page zoomed by a stray pinch still does, is a
  **device check**.
- **A small move is a drag.** `startWindowDock` treats 8 px of travel as a
  drag, and it commits `moveWindow` on `pointercancel` as on `pointerup`.
- **A folded window stays folded.** Closing a folded Chat and reopening it
  from the View menu brings back the 31 px bar (measured). `collapsedWins`
  is kept per page, and only `showChat` clears it.
- **Upright there is no collapse and no drag** (by design, `renderWindow`),
  but the header still draws "⠿" and the tooltip that names both.

### The notebook view and back

Measured on a five-sheet notebook, zoomed in, at page 4:

- **The switch loses the place both ways.** "Notes view" lands at the top
  of the notes, on page 1. "Notebook view" lands on page 1 of the
  notebook. Zoom, the armed tool and a lasso selection survive.
  - `NotebookViewer` mounts fresh. The notes window moves between the
    centre and a dock, so it remounts too.
  - `nbCurrent` knows the sheet in the middle of the view, but only the
    paper menu reads it.
- **The way back is off screen.** In the notes view the switch is a 26 px
  button in the tool row under each sheet. The first was 1281 px down an
  834 px screen. The other way is the View menu (More, upright).
- **Upright, the bottom bar changes shape.** Library / Notebook / Notes /
  Chat becomes Library / Notes / Chat, so the Notebook tab goes with the
  notebook view.
- **Notes view with the Notes window closed is an empty page.** In the
  notebook view with the Notes window closed, "Notes view" hides the
  viewer, and `notesWindow` renders only while `notesVisible`. The page
  showed no sheets and no rows (measured).
- **A tab switch resets the notebook.** Notebook at page 3, to the PDF's
  tab and back: page 1, with the tool disarmed. The PDF's own scroll and
  zoom came back exactly.
  - `captureScrollPos` and `restorePdfScroll` know only `.pdfViewer`.
  - The page-change cleanup disarms the tool. The Pencil's fallback is
    always a pen preset, so a highlighter comes back as a pen.
- **A notebook inherits the PDF's zoom.** A notebook made while a PDF
  stood at a numeric zoom opened with a 1310 px sheet in an 856 px viewer
  (measured). `openBlock` sets `pdfScale` only from a saved layout.
- **What is kept.** Upright, the bottom bar's Notebook and Notes tabs keep
  everything, because the viewer stays mounted. Rotation kept page 3,
  though fit-width drifts by the padding and gaps, which do not scale.
- **Compact shell, from the code.**
  - A jump from the Notes panel (`showOnPage`, `jumpToHighlightId`)
    scrolls the viewer behind the panel, and "Show note" from the viewer
    does not open the panel.
  - The Notes tab and the viewer's Notes view button share one icon for
    two different things.
- **Leaving the app.** A hidden page flushes its ink, but drafts live in
  memory (`ink/inkStore.js`) and the flush is two plain fetches. Strokes
  can be lost if iOS kills the tab first. **Device check.**

### Other findings

**Shell.**
- **Rotation remounts the windows.** Rotating, or resizing Split View
  across the breakpoint, remounts Chat, Notes and the topbar's popovers,
  because the docks and the phone panels are different parents.
  - The chat draft is lost (measured).
  - `ChatDock`'s `askedRef` starts again, so the last "Transcribe with AI"
    request is sent a second time.
- **More → Tours shows nothing upright.** The submenu flips to the left of
  the sheet, whose `overflow-y: auto` clips it. 9 px of its 190 were
  visible (measured).
- **The 13″ iPad Pro gets the docks upright.** It is 1032 px wide, past
  `PHONE_MQ`'s 1024 px.
- **Undo outside an open editor is Ctrl+Z only.** That covers a block
  deleted from its menu and a highlight.
- **Tabs.** A tab's menu is right-click only, and a pinned tab has no ×.
- **The sync pill** keeps the reason for a failure in a `title`.
- **Guide copy names gestures touch lacks.** Only a step's body has a
  touch variant (`bodyTouch`). Titles do not ("Double-click to fold",
  "Right-click a page for its menu"), and the touch body's "Long-press a
  page" does nothing on iPadOS.
- **Autofocus raises the keyboard** on Add, the all-tabs list, chat
  History and the page picker.
- **Upright, Search opens in the keyboard's place.** Its field sat at
  y 1094 of 1194 with the results under it (measured). **Device check.**
- **Dialogs ignore the keyboard.** `.reportOverlay` and Settings are
  centred in the layout viewport, which the keyboard does not shrink.

**PDF, ink and text boxes.**
- **An armed tool traps the swipe.** With Text armed, or finger drawing
  on, a swipe makes a box or a stroke (`touch-action: none`). In the notes
  view nothing then scrolls past a sheet taller than the screen.
- **A pinch leaves a stroke in finger-draw mode.** A second finger does
  not cancel the first finger's stroke. `beginAreaDrag` has the same gap.
- **A finger-only user gets no hint** that fingers do not draw.
- **The highlight tip.**
  - Its pending timer is cleared by any App re-render inside its 350 ms,
    because the listener's dependency is an inline callback. This is the
    first survey's "fragile", traced.
  - The tip is `position: fixed` and closes on `mousedown`, which a touch
    scroll never sends, so it stays put over other text.
- **Selections.** A scroll that starts outside the selection's page drops
  a lasso or a selected text box. A selection as large as the view turns
  every swipe into a move.
- **The paper menu and the tool strip overlap** when the viewer is
  narrower than about 1190 px.
- **`pointercancel` discards a whole pen stroke.** **Device check** for
  how often the Pencil gets one.

**Notes editor.**
- **The typed line sits behind the editing bar.** The caret's scroll
  margin is the bar's height only, and the keyboard is not counted.
  **Device check.**
- **The bar blinks on every block switch.** It is mounted per row, so each
  switch runs its 350 ms first-open hide, and `.editBar.moving` passes
  taps through to the row under it.
- **The `[[` picker** does not close on a tap elsewhere, and then swallows
  Return.
- **The row's ×** (20 px, on the focused row) deletes the whole subtree,
  and the status says "Ctrl+Z to undo".
- **A near miss opens the editor.** The targets are the 14 px checkbox and
  the 12 px fold arrow.
- **Tables.** Tapping the next cell after an edit focuses it outside the
  gesture, so the keyboard drops. **Device check.**
- **Formatting.** `formatBold` and `formatItalic` from the iPad keyboard's
  B and I are dropped. LaTeX slots hop on Tab only. The bar's Link reads
  the clipboard, which raises iPadOS's paste prompt.
- **More right-click-only menus.** The file chip's "Add to library", and
  "edit source" on a picture or table in an open editor.

**Library and settings.**
- **Right-click only, with no detour** (verified): folder Rename and
  Delete, label Rename and Delete, page Duplicate.
- **Drag only:** moving a folder into a folder, and Custom order
  (`dropOnFolder`).
- **Sign-in.** The username field sets no `autocapitalize`, and the lookup
  is exact, so "Tim" for "tim" is refused.
- **Import and restore** click a file input that is never attached to the
  document. **Device check.**
- **"Upload folder…"** is offered where Safari cannot pick one.
- **Settings.** Rows keep their explanations in `title`. The shortcut
  recorder needs focus, which Safari does not give a tapped button.

**Chat.**
- **Return always sends** (`PaperMentionInput.jsx`), so a message has one
  line. In Edit & re-send, Return re-sends and drops what followed, and
  that handler has no composition guard.
- **Send and Stop sit under the keyboard.** The composer's toolbar is
  below the textarea, and nothing in the chat reads `visualViewport`.
  **Device check.**
- **A second PDF passage or note selection needs Ctrl.**
- **Following the reply** compares raw `scrollTop` and unsticks early only
  for `wheel`. **Device check** with momentum scrolling.
- **A missed chip × opens the page.** The × is 20 px and sits on the
  chip's page button. The page opens and the draft is cleared.
- **A reply cut off by an app switch is lost.** The server does not finish
  or save it. The paper hand-off card, which sends the user to Safari, is
  gone on return. **Device check.**

**Stylesheets.**
- **Hover reveals with no other path:** code Copy, table + strips, image
  and Mermaid tools and grips, attach and unlink, chat message tools,
  chat history row tools, card and row pins. Most are `opacity: 0` and
  still take taps.
- **The first tap may only hover.** WebKit can treat a tap that reveals
  such a control as a hover and withhold the click. The first tap on a
  library row, a note row or a chat history row would then do nothing.
  Chromium has no such rule. **Device check**, the most valuable one.
- **Sizes.** No shared control reaches 36 px: `uiBtn` 32, `ctlBtn` 26,
  `uiClose` 24, `uiCloseSm` 20, send 27. The Settings switch is 34×20 in
  a row that is a `div`, not a `label`.
- **Absent everywhere:** `-webkit-tap-highlight-color`,
  `-webkit-touch-callout`, `any-pointer` and `interactive-widget`.
- **Overscroll.** `overscroll-behavior: none` on the document is
  standalone only, so a Safari tab can rubber-band and pull to refresh.
  **Device check.**
- **Text fields are under 16 px.** Only `maximum-scale=1` keeps a focus
  from zooming the page.
- **Touch is decided five ways:** `(pointer: coarse)` read once, the same
  read live, the last pointer's type, "a touch was seen", and
  `maxTouchPoints`. `html[data-touch]` is still unread.

### Mechanisms that cover several findings

- **A long-press hook** beside `ContextMenu` (`shared/ui/Menus.jsx`). For
  touch and pen it calls the existing `onContextMenu` handler after about
  500 ms, cancels on movement, swallows the click that follows and
  switches `draggable` off for that press. It covers the library, tab,
  highlight, object and file-chip menus. A visible "⋯" stays the way to
  find them.
- **One hover pass.** Put the reveal rules inside `@media (hover: hover)`
  and reveal by state on touch: the focused row, `.mdObjectSelected`.
  This also removes the first-tap risk.
- **Sizes for coarse pointers.** Either control-size tokens in
  `tokens.css`, or a `::before` hit-area extender on the small controls.
  Interface size, already per browser, is the stopgap.
- **The visible viewport as CSS variables,** factored out of the editing
  bar's `useBarPlace`, for the chat composer, dialogs, Search and
  `ContextMenu`'s clamp.
- **One host per window,** portalled into the dock or the phone panel, so
  a rotation does not remount Chat and Notes.
- **An Undo action on the status pill,** which already carries a button,
  for deletes made outside an editor.

### Corrections to the first survey

- **The pane splitter** is drawn 5 px wide, but its touch band is 35 px.
- **The 36 px ink menu and editing bar** are that size on every device,
  not only on coarse pointers. One `(pointer: coarse)` rule exists, the
  text box's.
- **Dock collapse** is not only double-click-only: the splitter's band
  and the grip's size are the larger part.

### Test coverage

No scenario drives the docks, the notebook's view switch, the library's
menus or the chat by touch. The measured items above came from one-off
probes and would each fit a step in `ipad.mjs` or `notebooks.mjs`.

### Since

Built since, in the `dev` working tree of 2026-10-05, under one rule: every
mouse interaction also works under a finger through one code path, with no
touch branch and the same controls at the same sizes on every device. The
mechanics are in [dev/ui-design.md](../dev/ui-design.md#one-behaviour-for-mouse-and-finger).

- **The long-press hook** is `shared/ui/press.js`: `menuPress` for every
  right-click-only menu of both surveys, `doublePress` for every
  double-click-only action (dock fold, picture zoom, diagram source, a
  grip's natural size). A "⋯" (`MenuButton`) is the visible way in on
  library rows and cards, chat history rows and the picture and diagram
  toolbars. Feature code asks `shared/lib/pointer.js` instead of deciding
  touch five ways.
- **One hover pass**, as proposed: reveals sit under `(hover: hover)`, a
  hidden control takes no press, and where nothing hovers it shows at rest
  or by state. That removes the first-tap risk in the code; the device
  check stays.
- **Sizes.** The owner chose one size set for every device over sizes for
  coarse pointers: the `--ctl-*` tokens, about 10 % larger than the sizes
  measured above (`uiBtn` 35, `ctlBtn` 29, `uiClose` 26 / 22 / 31, send
  31), with wider hit areas on the thin grips.
- **The docks:** the splitters take 6 px a side for a mouse and a finger
  alike, the grip is the header's full height, a double press folds, one
  press opens a folded window, a closed window forgets its fold, and a
  `pointercancel` moves nothing ([dev/ui-design.md](../dev/ui-design.md#dock-windows)).
- **The notebook view** keeps the place across the view switch both ways,
  a tab switch and a fit-width resize; the way back is a **Notebook**
  button in the title row; the compact bar keeps Notebook and Notes tabs;
  the notes view shows with the Notes window closed; a notebook opens at
  fit-width ([dev/notebooks.md](../dev/notebooks.md)). Compact-shell jumps
  close or open the panel ([dev/ipad.md](../dev/ipad.md#the-compact-shell)).
- **The viewer:** the area-note toggle on any touch screen, a second
  finger cancelling a finger's stroke or area drag, a hint when fingers
  scroll under pen-only drawing, and the highlight tip subscribed once and
  closed by a scroll.
- **The notes editor:** Inline code, Strikethrough and Next math argument
  on the editing bar (the first two in the native app's too),
  `beforeinput` formats, typing aids in prose, the ⋮⋮ handle at rest, a
  selected figure's tools, table handles by touch, and Undo on the status
  pill for a deleted block ([dev/ipad.md](../dev/ipad.md#the-editing-bar),
  [dev/ui-design.md](../dev/ui-design.md#the-status-pill)).
- **The library:** Select mode, folder Move to, Move up and Move down
  without a drag, file picks through `pickFiles`, the sign-in field's
  attributes ([dev/home_library.md](../dev/home_library.md)).
- **The chat:** Return breaks the line on an on-screen keyboard and
  Ctrl/⌘+Enter sends, every Enter has the input-method guard, and the
  draft and the last ask survive a remount, rotation included. Send and
  Stop stay in the toolbar under the field, the owner's choice
  ([dev/ai.md](../dev/ai.md#the-composer),
  [dev/chat_history.md](../dev/chat_history.md)).
- **The shell:** gesture events are refused on any touch screen, the
  document never rubber-bands in a Safari tab either, More's flyouts open
  inline, and the View menu carries Undo, Redo, Go to page and Command
  palette.
- **Tests.** `tests/e2e/scenarios/touch.mjs` (`npm run e2e -- --only
  touch`) runs each behaviour once with a mouse on a desktop and once with
  a finger on an emulated tablet; `tests/press.test.mjs` pins the gesture
  counting.

Still open:

- **One host per window.** Rotation still remounts Notes and the topbar's
  popovers; Chat keeps only its draft and its ask.
- **The visible viewport as CSS variables.** The chat composer, dialogs
  and Search still sit under the keyboard.
- **The swipe trap.** An armed Text tool, or finger drawing, still turns a
  swipe into a box or a stroke.
- **A second passage** still needs Ctrl, and the **`[[` picker** still
  ignores a tap elsewhere.
- **Undo reach.** Only a deleted block offers Undo on the pill; other
  deletes outside an editor do not.
- **Ink drafts** can still be lost when iOS kills the tab.
- Every **device check** above, none of which has run, and the other
  findings not named here, which are as found.
