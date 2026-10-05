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
- the pane splitter 5 px

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
  formatting it adds buttons for bold, italic, link and inline math.
- A note row's editor hands `beforeinput` `historyUndo` / `historyRedo` to
  the block history (`editor/BlockCmEditor.jsx`), so the keyboard's undo
  key and the three-finger swipes undo notes.
- Inline code and strike still have no touch way in, and nothing handles
  `beforeinput` `formatBold`.
- Indent and outdent keep the editor open, and a Backspace in an empty
  block moves on to the block above.
- The native app's notes have the same bar ([dev/ipad.md](../dev/ipad.md)
  "Editing the notes"), with "Handwritten note" in its Insert menu.
- `tests/e2e/scenarios/ipad.mjs` covers the bar by touch: structure,
  inserts, formatting and notes undo, with a stand-in for the soft
  keyboard's viewport.
- The typing aids (`contentAttributes`), the hidden handle, the menus,
  hover-only tools, target sizes and one-off gaps above are as found.
