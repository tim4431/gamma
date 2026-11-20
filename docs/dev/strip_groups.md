# Groups in the tab strip

Chrome's tab groups: a named, coloured run of tabs in a strip, behind a
chip that a click collapses. They are not the panes side by side, which
the code calls groups too ([tab_groups.md](tab_groups.md)); in the code
these are **strip groups**, and the interface uses Chrome's words for them
and "pane" for the panes (*Join panes*, *Close this pane*). Chrome's own
measures and rules, read from Chromium's source, are in
[the research note](../research/chrome-strip-groups.md).

## What the user sees

- **Making one.** A tab's right-click menu (not a pinned tab's: Chrome's
  rule) has *Add tab to new group*. When its strip holds other groups the
  row is a submenu, *Add tab to group ▸*, with *New group*, then each
  other group of that strip, by its colour and its name (*Unnamed group*
  when it has none). A new group takes the colour its strip uses least, in
  Chrome's order (blue, red, yellow, green, pink, purple, cyan, orange,
  grey); its tab stays where it stands, out of any group it was in, and
  the group's editor opens under its chip with the name field focused. A
  tab added to a group goes to the end of the group, and a collapsed group
  opens to show it. *Remove from group*
  takes a tab out, to just after the group, so the group stays one run.
- **In the strip.** A group's chip stands before its tabs: a pill of its
  colour with its name in black or white, whichever reads on the colour,
  or a square of the colour when it has none. While the group is open a
  line of its colour runs along the strip's foot, where the active tab
  meets the page, from under the chip to its last tab, and rises round the
  group's active tab as its outline, flaring back into the line at the
  tab's feet as Chrome's does (in a pane's strip, which stands on the
  head's border, it meets the outline square). A collapsed group is its
  chip alone. The *All open tabs* list shows a grouped tab's dot.
- **Collapse.** A click on the chip (Enter or Space on it) hides the
  group's tabs; the chip stays. Collapsing the group the strip's page is
  in opens first the nearest tab outside the group, after it first, then
  before it, a tab in sight before one in another collapsed group, or the
  library when every tab is in the group. A click on a collapsed chip
  shows the tabs again, and so does opening one of its pages from
  anywhere else (quick open, a link, *All open tabs*); the page a reload
  restores does not, as no one picked it. A collapsed group still shows
  its strip's active tab: the page another device's collapse left on
  screen here, or a pane's page, is never without its tab.
- **The editor.** A right-click (or a held finger) on a chip opens it:
  the name, kept as it is typed (Enter or Esc closes), the nine colours,
  *Ungroup* (the tabs stay, in no group), *Move group to the other pane*
  (split) and *Close group*, which closes the group's tabs without asking
  (a closed tab loses nothing).
- **Dragging in a strip.** A dragged tab takes the place of the tab it
  passes over, as the strip always did, and that tab's group: over a tab of
  a group it joins the group, at either end of it too; over a tab in none
  it leaves its own, unless it is its group's only tab, which carries the
  group along. A collapsed group is passed whole, never joined, so the
  tab never vanishes under the drag. Over a chip it stands just before that
  group, and past the strip's last item at the end, out of any group (the
  strip's gaps and padding are no end). A chip dragged carries its whole
  group past a tab, or past another group whole, never into it; so does
  Ctrl+← / → (⌘ on a Mac) on a chip that has the keyboard's focus, as on
  Chrome's header. Pinned and unpinned tabs never trade places.
- **Panes.** A group lives in a pane's strip. A tab dropped into the
  other pane's strip joins the group it lands inside (both neighbours in
  it); *Move to the other pane*, a drop on the other pane's page and
  *Split right* take it out of its group, unless it is the group's only
  tab. *Move group to the other pane* moves the group to the end of the
  other pane's strip, and what is on screen stays. *Join panes* keeps the
  groups. A group another device makes over tabs this browser keeps in
  both panes gathers into the pane of its first tab.
- **Where they follow.** With the tabs: every browser signed in to the
  same account and workspace on this server shows the same groups, names,
  colours and collapsed state. A mirror's server or another server keeps
  its own tabs, and so its own groups.
- **A phone's** one strip shows the groups and holds every tab; a tap on a
  chip collapses it, a held finger opens the editor, without the pane row.

## Where it is kept

On the tabs, in the synced `open-tabs` list ([settings.md](settings.md)):
every member of a group carries `group: {id, title, color, collapsed?}`
(`id` from `makeId`, `title` up to 40 characters, `color` one of the nine
names, `collapsed` only when true). A tab, its group and the group's look
go to the server in one PUT, through the tabs' own push, pull, cache and
merge in App. The look on every member costs some 60 bytes a tab, small
beside the tabs' own titles under the server's 64 KB cap, and leaves
nothing to lose when a member closes. There is no server change and no
migration: the server keeps the list as it is.

The tabs' sync is last writer wins, and the groups ride on it: a browser
tab opened before the update keeps `group` through its spreads of a tab,
drops it for a tab it rebuilds, and may reorder a tab into the middle of a
group; the rules below mend any such list on the next read.

## The model: `workspace/stripGroups.js`

Pure, node-tested (`tests/stripGroups.test.mjs`); an operation returns a
new list.

- `normalizeTabs(tabs)`: the list as it is kept, the step `updateTabs`,
  `applyServerTabs` and the first read of this browser's cache take every
  list through. Pinned tabs first, a stable partition; no group on a
  pinned tab or on a malformed one; a group's members one run (met in
  several runs, the longest keeps it, the first on a tie, and the others'
  tabs leave it); every member the look of the group's first tab that
  carries one.
- `gatherPanes(layout, tabs)`: each group's tabs into the pane of this
  browser's arrangement its first tab is in (`tabLayout.js` `moveTabs`,
  so what is on screen stays). Another device grouping tabs this browser
  keeps in both panes, or a phone's one strip, would otherwise leave part
  of a group in each pane: a chip in each, and the list sorted by the
  panes' order would split the group and the rules take tabs out of it on
  every device. App's reconcile effect runs it between two reconciles,
  the second ordering each pane by the list.
- `stripRuns(tabs)`: a strip's tabs as it draws them, a tab alone or a
  group with its run. `groupsIn`, `membersOf`.
- `groupTab`, `joinGroup`, `leaveGroup`, `ungroup`, `setGroup` (title,
  colour, collapsed), `leastUsedColor`, `groupTint(color)` (the style a
  chip, a tab, a dot and a swatch take).
- `collapseHeir(strip, groupId, activeId)`: what a collapse opens:
  `undefined` when the strip's page is not in the group, else the nearest
  tab outside it, or `null` for the library.
- `moveInStrip(strip, dragged, target)`: a strip's drag, with the rules
  above. `dragged` a tab id or `{group}`, `target` a tab id, `{group}` (a
  chip) or `null` (the strip's end); null when nothing changes.
- `takeGroups(tabs, strip)`: the groups a strip's drag gave its tabs, onto
  the list. `landTab(tabs, tabId, paneTabIds)`: a tab moved into another
  pane joins the group it landed inside, else keeps its own beside one of
  its tabs or as its only tab, else is in none.

## The owner, the strip and App

`workspace/useStripGroups.jsx` is the owner (App's hooks stay at their
baseline, [frontend-refactor.md](frontend-refactor.md#new-state)). It
holds which group's editor is open and the effect that shows a collapsed
group whose page is picked (not the first page shown, which a load
restores), and gives App the tab menu's rows
(`tabMenuItems(tabId, close)`), what the chips do (`strip`:
`onGroupChange`, `onGroupMenu`) and the open editor
(`workspace/StripGroupMenu.jsx`: a `ContextMenu` with `role="none"`
holding a labelled dialog, its colours the palette's swatches, drawn
once). It takes `stripOf(tabId)` (the tabs of the strip a tab is in),
`closeTabs`, `otherPane` (null on a phone) and `moveTabsToPane`. The
editor opens at the chip's `menuAnchor`; a new group's once its chip has
risen into place (`arriving`). The name field takes focus a frame after
the editor opens, as the editor is laid out hidden until it is placed.

`OpenTabs` (`shared/ui/Widgets.jsx`) draws `stripRuns`: the chip
(`data-strip-group`, draggable, `menuPress` for the editor) before its run,
a collapsed run's tabs left out but for the active one. The chips are
`ListMotion` items, so a collapse sinks the tabs out and the rest glide. A
chip's drag stays in its strip: it does not enter `tabDrag`, so no pane
and no page shows a drop layer for it. A tab from the other pane over a
chip shows the drop bar before the chip and lands before the group.

A strip draws its pane's tabs in the list's order (`groupTabs`), which the
arrangement only follows a render later (its reconcile effect): a group
change reorders the list (a tab joining goes to the end of the run), and a
strip in the arrangement's order would draw that render with the group in
two runs, two chips under one key.

App: `applyLayout(next, {target, retab})` takes the groups a change gave
the tabs (`retab`) and the panes' order in one `updateTabs`, so a list is
never held to the rules between a move and the group it lands in; a change
that moves no tab but regroups one still applies. The strip's
reorder (`reorderGroupTabs`) is `moveInStrip` with `takeGroups`, a move
into another pane and *Split right* pass `landTab`. `closeTabs(ids)` is
`closeTab` for several (*Close group*): the others leave the arrangement
before the page on screen picks the page that comes forward, so it is
never another closed one, and a closed tab still opening is taken back. *Move group to the other pane* is
`tabLayout.js`'s `moveTabs`, which keeps the page on screen and what the
other pane showed. A strip whose page goes to the library (a collapse with
nothing outside the group) does so through the layout, which takes back a
pick still opening.

## Look

`tokens.css` names the nine colours as scheme constants,
`--strip-group-<name>`, Chrome's classic tab group palette: its Google 300
tones on the dark scheme's chrome, its deeper ones on the light scheme's.
The chip's name is `--on-strip-group`, Chrome's darkest grey on the dark
scheme and white on the light one, but on the light yellow and orange
(`--on-strip-group-yellow`, `-orange`), where the grey reads better: the
choice Chrome's `GetColorWithMaxContrast` makes. `groupTint(color)` sets
`--strip-group` and `--strip-group-ink` on a chip, a tab, a dot and a swatch.

In app.css the chip is an item as tall as a tab (a drop and a drag take it
as one, and its stretch of the line stands where the tabs' do) holding the
20 px pill with Chrome's 6 px corners; the keyboard's ring goes round the
pill. The line is 2 px with round ends, at the foot of the strip's band
(`--tab-band`, the room under the tabs the active tab fills to join the
page: the topbar's padding and border, or a pane head's border alone).
Each item draws its stretch (`.stripGroupLine`), reaching over the gap
(`--tab-gap`) to the next; the chip's starts under its pill, the last
tab's (`.groupEnd`) stops short of its end. The open group's active tab
wears the colour as its border and an inset shadow (2 px, nothing moves),
its band narrowed inside them, and its stretch becomes two feet: the
corners of boxes `--tab-foot` square whose stroke comes down beside the
band and turns out into the line. A pane's strip has a 1 px band and a
2 px foot, a square joint.

## Tests

`tests/stripGroups.test.mjs` covers the rules: the kept list, a group
gathered into one pane, grouping, joining (a collapsed group opening) and
removing, each drag (joining at either end, leaving, a group's only tab
carrying it, a collapsed group passed whole, a chip, the end, the pinned
boundary, a group carried past a tab and past another group, the other
pane's tabs left as they were), the collapse's heir and a tab landing in
the other pane. `tests/tabLayout.test.mjs` covers `moveTabs`. The
`tab-groups` browser group has two *strip groups* steps. The first makes
a group from a tab's menu, names and colours it in the editor, drags a tab
into it, finds it in a second browser of the account, drags over the
strip's padding and finds nothing moved, reloads, collapses the group of
the page on screen and finds the tab outside it opened, opens a grouped
page with quick open and finds the group shown, drags the chip past a tab
and back with Ctrl+← / →, drags a tab out and puts it back through the submenu, and closes the
group of two tabs, the page on screen among them, to the page viewed
before. The second moves a group to the other pane and finds the page on
screen still on screen there, joins the panes, and collapses a group that
holds every tab to the library.

## Left out

- Chrome's *New tab in group*: Gamma has no blank tab, and a page opened
  from the library lands at the end of the live pane, in no group.
- Saved groups (Chrome's closed-group bookmarks) and keyboard commands for
  grouping, which Chrome binds none of either.
- Dragging a chip to the other pane (the editor's row moves a group).
- A group another device made over tabs this browser keeps in both panes
  shows a chip in each pane; nothing moves its tabs into one.
- On an iPad the editor's field is focused outside the press that opened
  it, so the keyboard comes up on a tap in the field.
