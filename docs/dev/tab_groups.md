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
- **The live group.** One group is live: its active tab is the page App
  has open, with the full workbench (the PDF, the notes and the other
  windows docked around it, the strip's tools, editing). Every other
  group shows its active tab as a view to read: a page with a PDF shows
  the PDF with its highlights, scrolled and zoomed as the viewer is; a
  page without one shows its notes rendered. The active tab of a group
  that is not live reads quieter. A press on a read view, or on a tab in
  its strip, makes its group live and opens the page in the workbench
  where the view was. The library and a page opened from it, from a link
  or from the palette land in the live group; a page already open in
  another group brings that group forward.
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

`TabGroups` draws the tree. A split is a `PanelGroup` of the dock's
react-resizable-panels, horizontal for a row, with a `Panel` per child
at the layout's size and a sash between; it is keyed by its children's
ids, so a split that gained or lost a child lays out afresh from the
layout's sizes rather than the panels' remembered ones, and its
`onLayout` reports a drag back as `onResize(splitId, sizes)`. A group is
a `GroupFrame`: its strip (`OpenTabs`, the topbar's component, with
`groupId` so a drag knows where it came from) over its body. The live
group's body holds `live`, App's work area; another group's holds
`renderPane(group)`. With one group, or on a phone (`single`), the one
frame is unframed: no strip, the body alone.

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

**Sizes.** The work area sizes itself to the window (`.main` in app.css);
in a framed group it fills the frame instead (`.tabGroup.framed .main`
in `workspace.css`). The dock's own drop zones, where a window's grip
drags it left, right or bottom, measure the live frame rather than the
window (App's `benchRect`).

## What a group shows: `workspace/PagePane.jsx`

A group that is not live shows its active tab through `PagePane`: the
page's tree read once (`/api/blocks/{id}/subtree`), laid over with the
page's own unsaved edits through the collab session's `overlay`, so a
page just left live shows what was typed. A page with a PDF shows it in
`PdfViewer` under a read-only `PageToolsContext` (no pen, no actions, no
marks), with its highlights, the viewer's zoom (Ctrl+wheel) and scroll. A
page without one shows its title and notes rendered: each block's text as
`BlockMarkdown`, a highlight's quote over its note, children under their
parent; handwriting groups, sheets and other blocks without text are left
out. A press calls `onActivate`: the primary button's or the first
finger's, down and up within 8 px, decided on its `click`. A scroll, a drag,
a text selection, a right-click and a press on a scrollbar do not
(`onScrollbar`: past the client box of the element pressed, on a side it
overflows; Windows and Linux draw scrollbars that take presses). The click
matters twice. Windows sends a right-click's `contextmenu` after the
release, so a release that switched groups would open the menu over the
page made live. And the pane takes its own click before it gives way to the
workbench, which a release would leave to land on whatever mounted under
the pointer.

The scroll travels. The pane reports its viewer's scroll as
`onScroll(pageId, {top, scale})`, the shape App's `tabScrollRef` keeps
for a tab left live (`captureScrollPos`), and opens at that store's
entry when it mounts, applied as the document's boxes are laid out
(`onLoadState` on `layout` and `rendered`) and scaled from the scale it
was measured at. So a page read in a pane and then made live opens in
the workbench where the pane was (`openBlock` with `restoreScroll`), and
a page left live reads on where it was.

What a read view leaves out: editing, handwriting, text boxes, pictures,
comments, the translated view, and changes made since it was read (its
own or another device's). These are the workbench's, which App holds for
one page ([frontend-refactor.md](frontend-refactor.md) "Goal and current
constraints"): making every group live is the page-session extraction
that plan describes, and the model and frames here need no change for
it; `renderPane` is the seam.

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
(`selectTab`, or `goHome` for an empty group). `closeTab` finds the next
page within the closed tab's group: the latest page still open there or
library view in the tab history, else the group's neighbour tab; a group
emptied goes and the neighbouring group's page comes forward. The strip's
reorder (`reorderGroupTabs`) moves the dragged tab to the target's place
among the group's tabs as shown, pinned first, never across the
pinned/unpinned boundary.

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
from the tab menu beside notes being edited and reads the PDF in its
pane, makes the pane's group live with a press and finds the notes
rendered in theirs, keeps the arrangement across a reload, drops a tab on
a group's bottom edge and sees the groups stacked, closes a group with
its last tab, and opens an empty group with the library in it and closes
it from its view. `select.mjs` runs it for the model and the pane, and
every group for the frames.
