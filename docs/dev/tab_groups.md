# Tab groups

Pages side by side. The open tabs are arranged into groups, each group a
frame with its own tab strip over its page, split left and right or top
and bottom, as VS Code's editor groups and SiYuan's panes are
([the survey](../research/tab-groups.md)). A reader keeps a PDF in one
group and a notes page of their own in another, and edits the notes while
the paper stays in view (issue #153). With one group nothing of this
shows: the strip stays in the topbar and the page fills the work area as
it always did.

## What the user sees

- **Splitting.** A tab's right-click menu offers *Split right* and *Split
  down*: the tab goes into a group of its own beside its group. A tab
  dragged onto a group's page shows where it would land: a band over the
  left, right, top or bottom half splits the group on that side, the
  middle takes the tab into the group. Dragged onto another group's strip
  it joins that group where the bar between its tabs shows. The command
  palette has *Split right*, *Split down* and *Join tab groups* (unbound
  until the account gives them keys). A tab that is alone in its group
  splits off an empty group instead, which shows the library to pick its
  page.
- **What splits.** The groups share the page's place, the middle of the
  work area. The docked windows (Notes, Chat, Comments) stand around the
  groups as they stand around the page with one group, at their own
  sizes, as VS Code's side bars stand outside its editor grid.
- **The live group.** One group is live: its active tab is the page App
  has open, editable, with the viewer's tools. Every other group shows a
  view of its active tab drawn as the page itself would draw it: a page
  with a PDF shows the PDF with its highlights, handwriting, text boxes
  and pictures, at the page's own zoom and where it was left; a page in
  its notebook view shows its sheets; any other page, or one whose PDF is
  closed, shows its notes as the editor draws them. A press on a view, or
  on a tab in its strip, makes its group live, and nothing on screen
  moves: the groups, the windows, the paper's place and zoom and the
  notes' rows stay where they were, and only the tools of the page being
  edited come and go. A press on a note opens that note's editor with the
  caret where the press was, on the title the title's. The active tab of
  a group that is not live reads quieter. The library and a page opened
  from it, from a link or from the palette land in the live group; a page
  already open in another group brings that group forward.
- **The Notes window.** It shows the notes of the page on screen when that
  page has a PDF (or a notebook). While the page on screen has its notes
  in its own group (a page without a PDF, or the library), it keeps the
  notes of the paper another group shows, the last one on screen, so it
  neither comes nor goes as presses move between a paper and the notes
  beside it, and the paper's highlights stay in view while the notes are
  written. A press on them makes the paper's group live.
- **Closing.** A tab closed in a group shows the group's latest-viewed
  tab still open, else the neighbour. The last tab closed takes its group
  away, and its room goes to the group beside it. An empty group that is
  not live says so and offers *Open a page here* and *Close this group*.
  *Join tab groups* puts every tab back in one group, the open page still
  open.
- **Where it is kept.** The tabs themselves are the account's, synced as
  before (`open-tabs`, [settings.md](settings.md)). Their arrangement is
  this browser's, like the dock layout: `gamma-tab-layout:<user>@<ws>` in
  localStorage, read when the account and workspace are known and checked
  against the tabs then open. Another device shows the same tabs in one
  group until it splits them itself.
- **A phone** shows one view at a time: the arrangement is kept but not
  drawn, the strip stays in the topbar, and the split commands are
  absent.

## The model: `workspace/tabLayout.js`

A layout is `{root, active, seq}`. `root` is a group, `{type: "group",
id, tabs: [pageId…], active: pageId | null}`, or a split, `{type:
"split", id, dir: "row" | "col", children: [node…], sizes: [percent…]}`
(a row lays its children left to right). `active` is the live group's
id; `seq` numbers the next node id (`g7`, `s3`). A tab is in exactly one
group. Every function is pure and returns a new layout, or its argument
itself when nothing changed, so a caller compares identities.

- `singleLayout(tabIds, active)`: one group.
- `groupsOf`, `groupById`, `groupOf(layout, tabId)`, `activeGroup`,
  `tabOrder` (every tab, group by group), `isSingle`.
- `activate(layout, groupId, tabId?)`: the live group, and what it shows
  (null: the library).
- `reconcile(layout, tabIds, shownTabId)`: the arrangement follows the
  tabs list and the page on screen. Tabs the list lost leave their groups
  (a group emptied this way goes, as a close would take it), each group's
  tabs take the list's order among them, tabs no group holds join the
  live group at its end, and the shown tab becomes its group's active tab
  with that group live; `""` or null leaves the live group on the library.
- `moveTab(layout, tabId, toGroupId, index)`: within a group a reorder at
  the index named before the tab is taken out, as a strip's reorder
  names it; across groups the tab becomes the target's active tab, the
  live role follows the live page, and a group emptied goes.
  `reorderGroup(layout, groupId, ids)` is a strip's reorder as a whole.
- `splitGroup(layout, groupId, side, tabId?)`: a new group on the side
  (`left`, `right`, `top`, `bottom`), as one more child of a parent split
  of the same direction (the split group's room halved between them),
  else as a new split of two in the group's place. The tab moves into it
  as a move would; the only tab of the split group itself cannot be split
  off. Without a tab the new group is empty and live.
- `removeTab(layout, tabId, {prefer})`: a close. The group shows `prefer`
  when it still holds it, else the tab after the closed one; a group
  emptied goes while another remains, and a live group that goes hands
  the live role to the group nearest its place. `removeGroup(layout,
  groupId)` takes a group out, its tabs joining the group that takes its
  room.
- `resizeSplit(layout, splitId, sizes)`: the sizes a sash was dragged to,
  ignored within half a percent. `joinAll`: one group in reading order.
- `normalizeLayout(value, tabIds)`: a stored layout against the open
  tabs. An unknown shape, or nothing open across several groups, gives
  one group of every tab; else ids are renumbered, tabs not open dropped
  (a tab met twice kept where it was first), a group whose every tab is
  gone dropped (one saved empty stays), a split left with one child
  unwrapped, sizes made to fit, unplaced tabs added to the live group,
  and a live group that is gone replaced by the first.

`tests/tabLayout.test.mjs` covers each of these.

## The frames: `workspace/TabGroups.jsx`

`TabGroups` draws the tree inside the dock's middle slot (`slot-main`,
App's work area around it unchanged). A split is a `PanelGroup` of the dock's
react-resizable-panels, horizontal for a row, with a `Panel` per child
at the layout's size and a sash between; it is keyed by its children's
ids, so a split that gained or lost a child lays out afresh from the
layout's sizes rather than the panels' remembered ones, and its
`onLayout` reports a drag back as `onResize(splitId, sizes)`. A group is
a `GroupFrame`: its strip (`OpenTabs`, the topbar's component, with
`groupId` so a drag knows where it came from) over its body. Another
group's body holds `renderPane(group)`. The live group's body holds an
empty slot, and `live`, App's page view (`div.main`), is drawn once
beside the tree, in a host laid over that slot and clipped at its edge
as a group's panel clips a view (`useLiveHost`: placed after every
render of the groups, on every resize of the slot or the root from a
resize observer, and on every move of a sash between groups from the
split's `onLayout`, through `HostPlacer`, in the commit of the panels'
new sizes: a sash move re-renders the panels alone, and a host placed
from the slot's observer callback would resize the page in it after it
had laid out, which the browser reports as a resize observer loop and the
viewer's fit then takes a frame late). The tree and the
host are the root's first and second children whatever the layout's
shape, so a change of live group, a split, a join or a group closed moves
the host and remounts nothing in it: the viewer keeps its document, zoom
and scroll, the notes their editor. `liveId` names the group the host
stands over (App's `shownGroupId`, below). With one group, or on a phone
(`single`), the one frame is unframed: no strip, the body alone (on a
phone the live group's).

**Drags.** The strip's reorder already uses the browser's drag; a tab's
`dragstart` also records the tab in `shared/ui/tabDrag.js`, a small
store every strip and frame reads (the drag's data cannot be read while
it passes over, only on the drop). While a drag is on, each frame covers
its body with a drop layer; `dropZoneAt` reads the zone from the pointer
in the body: an edge band (the lesser of 30% of the side and 220 px
across or 160 px down; the band the pointer is deeper into wins a
corner) or the middle, drawn as a band over the half the tab would take,
or the whole body. The drop calls `onSplit(groupId, side, tabId)` or
`onMoveTab(tabId, groupId, end)`. The dragged tab's own group offers no
middle, and no edge when the tab is its only one. A strip under a tab
from another group shows a bar before the tab the pointer is over (or
after the last) and takes it on the drop at that place. The store ends
the drag on the source's `dragend`, and on the window's `drop` and
`dragend` after React's handlers, since a source the drop unmounts sends
no `dragend`.

**Sizes.** The page view sizes itself to the window (`.main` in app.css);
in the host it fills the host (`.tabLiveHost > .main` in
`workspace.css`), which stacks under a drag's drop layers (they have a
z-index of their own). The dock's drop zones measure the work area, as
they always did.

## What a group shows: `workspace/PagePane.jsx`

A group that is not live shows its active tab through `PagePane`, a view
of the page drawn with the live page view's own components, so that a
press making it live changes nothing on screen. What it shows is the live
page's rule: `documentView` (`documents/documentView.js`) over the page's
document (`pageDocument`), its sheets, its notebook view and its closed
pane, with `dock` forcing the notes; when the view shows a viewer,
`DocumentPane` draws the one it names (`pdf` or `notebook`), else the
notes. Unlike the live page, a closed PDF is not mounted here.

- **Its tree.** Read once per page shown (`/api/blocks/{id}/subtree`),
  laid over with the page's unsent edits through the collab session's
  `overlay`. A page this session left draws at once from what it held
  then (`keepPaneTree`, which `leaveCurrentPage` calls), before the
  answer comes.
- **A PDF.** The viewer the live page uses, with its highlights (link
  highlights with their target) and its marks built from the tree the
  way the live page builds them (`groupsBySurface`, `textBoxesBySurface`,
  `picturesBySurface`, `useMarks`), the PDF's dark pages and embedded
  annotations as set. At the page's own zoom (`pageLayouts`), else the
  one on screen; a press hands the zoom the view shows over (`zoom`),
  which the page opens at, and a zoom the reader chooses in the view
  becomes the page's own (`onZoom`). At its place: where it was left this session (`tabScrollRef`,
  `{top, left, scale}`, applied as the boxes are laid out), else the top
  of the page last read (`readPos`), as App's coarse restore lands it.
  Its scroll, across too, is reported as it moves (`onScroll`) and again
  on every press, so the page opens exactly there (App restores `left`
  as well, and keeps it when a page is left).
- **A notebook view** shows the sheets in `NotebookViewer`, read-only, at
  the page's zoom (its own once changed, `onZoom`) and its place on them
  (`tabScrollRef`'s `{place}`), which it reports as it scrolls.
- **Notes.** The notes window's markup (`div.main pdfHidden > .sidebar`
  with the title row, its labels and its buttons at rest, and the list)
  around `BlockTree` in its `preview`: the editable tree's look (the
  handle column, the delete button in the flow, task boxes, sheets with
  their buttons) with nothing that edits; for an account that may not
  edit, the read-only tree its live notes are too. Every handler a row may call is
  a function that does nothing or makes the page live; the tree's view is
  the pane's own (folds toggle there, and go with a press, below). It
  lands where the page was left (`notesPlace`, App's `notesPlaceRef`,
  `{id, offset}`) and reports its place as it scrolls, and a page with
  sheets in its notes the place on them too (`{place}`, which App's
  notes land on after the row). Sheets are drawn among the notes unless
  the page's notebook view shows them, as the live notes do. A file
  dropped on a row goes on to App, which takes none on a view. In the
  dock (`dock`) the same view is the Notes window's body.
- **Its tools.** A read-only `PageToolsContext` with `preview`: nothing
  armed, `actions.onJump` and `onActivate` making the page live. Under
  `preview` a surface draws no one's strokes in progress, registers no
  surface size, never restores strokes from the ink journal and never
  drops a draft for the file its tree names (those are kept by PDF page
  number, which two documents share, or are the live page's to save, and
  a view's tree, read once, can predate this tab's own save:
  `MarkupLayers`, `InkLayer`'s `view`, `inkStore.inkFor`'s `restore`),
  and a sheet's buttons press through to `onActivate`.

**Presses.** A press makes the page live (`onActivate(target)`): the
primary button's or the first finger's, down and up within 8 px, decided
on its `click`, and never a control's own (a button, a link, an input).
A scroll, a drag, a text selection, a right-click and a press on a
scrollbar do not (`onScrollbar`: past the client box of the element
pressed, on a side it overflows; Windows and Linux draw scrollbars that
take presses). Windows sends a right-click's `contextmenu` after the
release, so a release that switched groups would open the menu over the
page made live. A press on a note goes through the row as it would in
the editor: the row maps it to a place in the note's source
(`clickPos`), and the preview hands it to `onStartEdit(id, true,
clickPos)`, which makes the page live with `{edit: {id, clickPos}}`; the
title gives `{title: true}`, the labels `{labels: true}`. Every press
carries the folds the view's notes were given (`folds`): the page opens
folded as the view had it, with the note to edit revealed (openBlock),
so no row below moves and the editor opens in sight. A highlight, link
or note badge on a viewer told nothing of them (PdfViewer hands its
pages those handlers only when it has them) lets the press through to
the page.

What a view still leaves out: editing until the press, a sub-page's
crumbs over its title, comment counts, the translated view, and changes
made elsewhere since it was read (it has no live socket). Making every group live at once is the page-session
extraction of [frontend-refactor.md](frontend-refactor.md); the model,
the frames and the host need no change for it, and `renderPane` is the
seam.

## App's side

`tabLayout` sits beside `openTabs` in App.jsx. `keepTabLayout` sets it and
writes this browser's copy in the same step, as `updateTabs` does with
the tabs, so an account switch cannot race a write into the wrong key;
the account-switch effect reads the copy back through `normalizeLayout`
against the cached tabs, and clears it with the tabs on sign-out. An
effect keeps the arrangement in step with the tabs and the page on
screen through `reconcile` (the picked tab stands in for the page while
it opens). Every change goes through `applyLayout`: the layout is kept,
the synced tabs take its order (`updateTabs`, which keeps pinned tabs
first), and the page on screen follows the live group's active tab
(`selectTab`, or `goHome` for an empty group). A press's target rides
along (`activateTabGroup(groupId, tabId, target)`, `applyLayout`,
`selectTab`) into `openBlock`: the view's `folds` open the page folded
alike, with the note to edit revealed; `{edit}` stages the note's editor
(`pendingFocusRef` with the press's offset, and the press itself for the
row's editor to take, `takeClickPos`), `{title}` and `{labels}` run as
the page's `then` actions once it is on screen. `closeTab` finds the next
page within the closed tab's group: the latest page still open there or
library view in the tab history, else the group's neighbour tab; a group
emptied goes and the neighbouring group's page comes forward. The strip's
reorder (`reorderGroupTabs`) moves the dragged tab to the target's place
among the group's tabs as shown, pinned first, never across the
pinned/unpinned boundary.

**What stays where.** `shownGroupId` is the group the page view stands
over: the group of the page App holds, else (the library) the live one.
A press on another group's view makes that group live at once, but its
view and the page on screen stay where they are until the pressed page's
tree lands, and the two change places in that commit, so neither shows
the other's page meanwhile. With pages side by side `openBlock` brings
back only what belongs to the page (its PDF closed or shown, its zoom),
never the page's saved window arrangement and sizes: the windows are the
groups' together. Leaving a page then keeps the arrangement it has saved
and takes only those two (`captureScrollPos`), so after a join each page
opens as it did before the split. `paneProps(pageId)` gives a view what it opens at and
`paneShared` what App lends it (ref labels, the library, file chips, the
notes' text size and keys, the PDF's looks, rights). `notesPaper` is the
page the docked Notes window shows while the page on screen has its
notes in its group. The lookups that took the first notes list or viewer
in the document take the open page's: the notes' landing and their place
(`liveNotesList`), the reading-position tracker and the recents snapshot
(`viewerWrapRef`), the ink zoom window's and ruler's visible part
(`visiblePart`), the tour's anchors (`[data-read-view]` skipped); a file
dropped on a view does nothing.

The topbar renders the strip while the layout is one group (or on a
phone), else an empty strip for the actions to stand right of; each
framed group's strip is `TabGroups`'s. A strip's *All open tabs* menu is
`openPopover` `"tabs"` in the topbar and `"tabs:<groupId>"` in a frame.
The tab menu's *Split right*, *Split down* and *Join tab groups* rows and
the `app.splitRight`, `app.splitDown` and `app.joinGroups` commands
(`app/appCommands.js`, group View; the context's `phone`, `groups`,
`splitTab(side)`, `joinGroups()`) call `splitTab` and `joinTabGroups`.

## Tests

`frontend/tests/tabLayout.test.mjs` is the model. The browser group
`tab-groups` (`tests/e2e/scenarios/tabGroups.mjs`) splits a paper off
from the tab menu beside notes being edited and finds the page covering
its group's body with the dock beside the groups, makes the paper's group
live with a press and finds the notes drawn in theirs, drags the sash
between the groups and finds the paper on screen following its group
with no resize observer loop, presses between the two and finds every
box where it was (the groups, the docked windows, the paper's first page
and its place, the notes' row, list and title) and the Notes window on
the paper's notes throughout, presses a note and finds its editor open,
unfolds a note in a view and presses its child and finds the child's
editor open where it was pressed, keeps the arrangement across a reload,
finds a drag's drop layer over the live group's page, drops a tab on a
group's bottom edge and sees the groups stacked, closes a group with
its last tab, opens an empty group with the library in it and closes it
from its view, and leaves a view's group as it is on a right-click or a
scrollbar press. `select.mjs` runs it for the model and the pane, and
every group for the frames.
