# Split panes: what the two-pane UI lacks, how Notability and VS Code draw two panes, and the design recommended

Survey from 2026-11-07. Pages stand side by side in tab groups
([dev/tab_groups.md](../dev/tab_groups.md); the earlier survey of VS Code's
and SiYuan's pane trees is [tab-groups.md](tab-groups.md)). The wish that
started this note: each pane should have a UI of its own, both drawn by
Gamma alike, and each controlled on its own, the way Notability's Multi-Note
and VS Code's editor groups do it. This note records what the screen shows
today, what the two products do that Gamma does not, and the design that
follows. Part of it is built, narrowed by the developer; "As built", below,
says what and how it differs.

## What the screen shows today

The states below were taken from the built app on an isolated server (the
e2e harness), with a paper, a page of notes and a third page open: one
group, the paper split right, the paper pressed live, a third tab in the
notes' group, split down, and a tablet-sized touch window.

- **Live and view look the same.** The live group's active tab keeps its
  full title and a view's reads muted, and that is the whole difference
  in the frames. The tools that come with editing belong to the live page
  alone: the viewer's side column (zoom out, zoom in, fit to width, the
  pen, Record, the text highlighter, Translate), the full-screen button
  and the close ×. They appear in whichever pane was pressed last and
  vanish from the other, so the screen changes on every press between the
  two. A view shows no controls at all, though it scrolls, and a
  Ctrl+wheel or a pinch zooms it (PdfViewer's wheel handler calls the
  view's `onZoomTo`). The page pill ("1 / 3") is the viewer's own and
  shows in both.
- **The paper's Notes window becomes a third column.** With the notes
  page live on the left and the paper in a view on the right, the docked
  Notes window on the far right shows the paper's notes: a title row with
  the paper's name and the same comment, attach, info and delete buttons
  the notes page draws over its own list. Two title rows of the same
  shape, one a page and one a window, and nothing says which belongs to
  which. The window opens by default (`notesPaper` in App.jsx keeps the
  last paper's notes while a notes page is live), so a reader who split
  precisely to write in a page of their own gets the paper's empty notes
  beside it until they close the window.
- **The topbar goes hollow.** With the strip moved into the groups, the
  topbar keeps its full height for the home button at the left, the
  action icons at the right and an empty strip between them.
- **Group actions are hidden.** Split right and Split down are rows of a
  tab's right-click menu and of the command palette, and Join tab groups
  is in the same two places. A pane itself offers nothing: no split, no
  close group, no swap of the two sides, no flip from side by side to
  stacked, no maximize, and no key moves the focus between groups (the
  three commands are unbound). The issue that asked for the feature
  (#153) asked for swapping sides too.
- **Two presses for any tool on a view.** The pen on the paper, while
  the notes are live, is a press on the paper to make it live and a
  second on the pen that has just appeared. On a tablet that is two taps
  for the most common move, and the first tap lands on the PDF.
- **On a tablet** the notes page in a view shows its rows' handles and
  comment buttons at rest, as the touch rules say, so the view reads
  busier than the paper beside it, and the docked Notes window's title
  wraps in its narrow column.

What works and should stay: a press keeps every box where it was (the
groups, the windows, the paper's place and zoom, the notes' rows), the
sash drags with no observer loop, a press on a note lands in its editor
with the caret where it was pressed, the drop zones, and the arrangement
kept per browser over the account's tabs.

## How VS Code draws two groups

Every group is a full editor; nothing is a view, and the same file can be
open in two groups at once ([tab-groups.md](tab-groups.md) has the grid).
What differs between the active group and the rest is drawn, not
functional:

- **The active group** is told by its tabs: the theme gives an unfocused
  group's active tab a dimmer foreground and no accent border, and a
  focused group that is empty a border of its own. Nothing dims the
  editor's content.
- **Editor actions live on the group's own tab bar**, at its right end:
  split, and a "…" More Actions menu with Lock Group, close all and the
  rest. They show on the active group only, and everywhere once
  `workbench.editor.alwaysShowEditorActions` is on (VS Code 1.90). A
  locked group shows a lock icon in that toolbar; new editors skip a
  locked group unless dragged into it.
- **Commands and keys**, Windows bindings: Ctrl+\ splits the editor,
  Ctrl+K Ctrl+Shift+\ splits it inside its group, Ctrl+1, Ctrl+2 and
  Ctrl+3 focus the first, second and third group, Ctrl+K Ctrl+← and
  Ctrl+K Ctrl+→ focus the group on that side, Ctrl+Alt+→ moves the
  editor into the next group, Ctrl+K Ctrl+M toggles the group maximized,
  and Shift+Alt+0 flips the layout between vertical and horizontal. A
  double-click on a tab toggles the group's size
  (`workbench.editor.doubleClickTabToToggleEditorGroupSizes`: expand by
  default, or maximize, or off). View › Editor Layout offers presets (two
  columns, three columns, a 2×2 grid) and the sash resizes groups.
- **The side bars and the panel stand outside the grid**, as Gamma's dock
  windows stand outside the groups.

## How Notability draws two notes

Notability's Multi-Note (iPad) opens a second note beside the first from
the Note Switcher, the thumbnail list that a swipe from the left edge
opens (the gesture can be set to "Swipe from Left Edge" or "None"):

- **Opening.** Press and hold a note's thumbnail and choose *Open On
  Right* or *Open On Bottom*, or drag the note from the switcher into the
  editing area. On a Mac a second note opens in a new window instead
  (right-click › *Open in a New Window*).
- **Arranging.** A held press on a thumbnail of a note in use offers
  *Switch Orientation*, left-right to top-bottom and back, and *Switch
  Notes*, which swaps the two. The middle spacer is dragged to resize.
- **Both notes are notes.** Each is edited in place; there is no read
  copy. Each pane shows its note under its own title, and as far as this
  survey could tell each pane carries its own toolbar, so a pen chosen in
  one does not change the other; Notability's help does not say so, and
  this was not checked on a device ("Still open").
- **Several windows.** Multiple instances of the app can open the same
  notebook twice, and each instance can hold an in-app split, so four
  documents can stand side by side.

## What the two share, and Gamma lacks

Both products treat the two panes as equals. A pane is a whole editor
with its own controls, and what marks the active one is a quiet change
of colour, not a change of which controls exist. Both put the pane's own
actions in the pane, VS Code on the tab bar's right end and Notability in
the switcher's held-press menu, and both offer a swap and a flip. Gamma's
frames have the tree, the strips, the drops and the sash; what they lack
is the pane's chrome and a second editable page.

## The constraint

App.jsx holds one page: its tree, its collab session, its editor state,
its PDF viewer, its ink, its text boxes and pictures, its comments. So one
group is live and the others draw views ([tab-groups.md](tab-groups.md)
"What Gamma took"). Making every group editable is the page-session
extraction of [dev/frontend-refactor.md](../dev/frontend-refactor.md), the
largest step of that plan, and nothing in the frames waits on it:
`renderPane` is the seam.

## Options weighed

- **The second workbench first.** Every group a full page session, Layer 2
  below. It answers the wish completely, and it is weeks of refactor
  across App.jsx before anything on screen changes.
- **The same chrome on every pane, on the one-page model.** Each group
  draws the pane's controls whether it is live or not, with the controls
  that need the workbench making the pane live as they act, in one press.
  Days of work, in the frames and a shared strip component, and the
  chrome becomes the workbench's when Layer 2 lands.
- **Where a paper's notes go while pages are split.** In the pane with its
  PDF, as the one-group layout shows the Notes window beside the page,
  with the pane's own toggle; or left in the outer dock, labelled as the
  paper's and tinted with its group, a press on the label focusing that
  group. The first removes the twin title rows and makes each pane a
  Gamma; the second is cheaper and keeps the dock's model untouched.

## The design recommended

Layer 1, the same chrome on every pane, in this order, each step with a
step in `tests/e2e/scenarios/tabGroups.mjs`:

1. **Pane actions on the pane's header.** `GroupFrame` (TabGroups.jsx)
   draws, after the strip, split right, split down and a "…" menu with
   Swap with neighbour, Flip orientation, Maximize (Restore), Close group
   and Join tab groups. The model gains `swapChildren(layout, splitId,
   i)`, `flipSplit(layout, splitId)` and a `max: groupId | null` that
   `normalizeLayout` drops when the group is gone, each pure and in
   `tests/tabLayout.test.mjs`. A maximized group fills the tree and the
   others collapse to their strips, as VS Code's maximize does.
2. **One viewer strip, drawn by both sides.** App's `viewerTool` map and
   `stripPlaces` move into a component (`pdf/ViewerStrip.jsx`) that takes
   the places, the lit states and an `actions` object. The live page
   passes its actions as today. A view (PagePane.jsx) passes its own:
   zoom out, zoom in and fit act on the view's `zoomTo`; the pen, the
   highlighter, Record, Translate and Paper call `press({tool: id})`,
   which `activateTabGroup` carries into `openBlock` as a `then` action,
   the way `{title}` and `{labels}` ride today, so the page opens live
   with that tool armed in one press. The full-screen button and the
   close × draw in a view too; the × flips the page's saved `pdfHidden`,
   and the view redraws as the notes. The page pill stays the viewer's.
3. **The live marker, VS Code's way, and the topbar.** In workspace.css
   the live group's active tab keeps the accent band and its full title,
   a view's active tab loses the band and keeps the muted title, the pane
   actions read quieter in a view, and an empty group that is live takes
   `--focus-ring`. The topbar's empty strip collapses, so with groups
   framed the bar is its icon row.
4. **A paper's notes inside its pane.** While the layout is not single,
   the Notes window leaves the outer dock: the live host holds the page
   view and the Notes window side by side in a `PanelGroup`, the pane's
   header toggles them, and the size is the group node's (`notes: {open,
   size}`, kept with the layout). A view draws `PdfView` beside
   `NotesView`, which already serves the dock as its body. `notesPaper`
   and the dock's `winVisible.notes` branch for the split case go. The
   one-group layout keeps the dock as it is, so nothing changes for a
   reader who never splits.
5. **Commands and the held-finger menu.** `app.focusNextGroup`,
   `app.focusPrevGroup`, `app.moveTabToNextGroup`, `app.swapGroups`,
   `app.flipGroups` and `app.maximizeGroup` in appCommands.js, group
   View, unbound by default so the account can give them VS Code's keys.
   The tab's menu (its right-click, its held finger) gains Swap, Flip and
   Maximize, so a tablet reaches them where Notability puts them.

Docs that change with it: dev/tab_groups.md ("What the user sees", "The
frames"), ui-design.md's "Tab groups" rows, hotkeys.md's command list and
the user guide's "Pages side by side".

Layer 2, every group live: the page-session extraction. A `usePageSession`
owning one page's tree, collab session, editor state, viewer state and
ink, App composing one per group, in the order frontend-refactor.md gives
(the save queues first, then the page session, then navigation). The
frames, the model and the per-pane chrome of Layer 1 stay; a view becomes
a session. Decide on it after Layer 1 has been lived with: a press to move
the focus is what VS Code asks for too, and what Layer 1 leaves is only
that the other pane's editor closes meanwhile.

## As built

The developer chose a smaller first step than Layer 1, and one part
Layer 1 did not propose:

- **One split, left and right.** The page's place splits once, into two
  panes. *Split down*, the top and bottom drop zones and the second split
  are gone from the UI; the model (`workspace/tabLayout.js`) keeps its
  tree, and a layout kept in another shape is drawn as it was. Split,
  a tab's menu offers *Move to the other pane*, and a tab dragged onto the
  other pane's page lights the whole pane. No swap, flip or maximize yet.
- **The live pane ringed** in `--focus-ring`, round its strip and its
  page, while two panes stand; the other pane's active tab stays quieter.
  The note proposed a ring only for an empty live group.
- **The topbar's page buttons in each pane**, which the note did not
  propose. While split, Search, Share and the View menu stand at the end
  of each pane's strip and act on that pane's page, the way VS Code puts
  a group's editor actions on its own tab bar. Each topbar item that
  offers it has a place, top or pane, set from a right-click on the
  button or Settings › Top bar; the sync pill stays on top. In the pane
  that is not live a button stands in for the item and, pressed, makes
  the pane live and opens the item there once its page is open, the
  `then` action step 2 proposed for the viewer's tools.
- **The topbar keeps its height** with Home, Back, the items left on top
  and Account; it was not collapsed.
- **A right-click on the topbar or a pane's strip** offers *Split right*
  (one group) or *Join tab groups* (split) beside Customize.

Left as proposed for later: one viewer strip drawn by views too (step 2),
the paper's notes inside its pane rather than the dock's Notes window
(step 4), the focus and swap commands (step 5), and Layer 2.

## Still open

- Whether Notability's Multi-Note gives each note its own toolbar, which
  this survey believes but could not confirm from its help pages.
- Whether Layer 1 is enough, or the second workbench is wanted for the
  case Layer 1 cannot give: typing in one pane while drawing in the other
  with no press between.
- A view follows no changes made elsewhere since it was read (no socket),
  which Layer 1 leaves as it is and Layer 2 removes.
- Several windows, which the desktop shell could offer and a browser
  cannot; Notability's Mac app and its multiple instances take that road.
- The tablet in landscape: three columns of notes, paper and dock leave
  each narrow, and Layer 1's step 4 is where the dock's column goes away.
