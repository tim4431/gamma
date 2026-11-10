# Tab groups: how VS Code and SiYuan arrange panes

Issue #153 asked for a PDF and a separately created notes page open side
by side, each keeping its own scroll and editing state, resizable and
swappable, with top-and-bottom splits on a tablet, as SiYuan does it. The
request names the two products whose arrangement of tabs into panes is
best known; this note records how each does it and what Gamma took.
The mechanics as built are in [dev/tab_groups.md](../dev/tab_groups.md).

## VS Code: editor groups

The editor area (`EditorPart`, `vs/workbench/browser/parts/editor/`) is a
grid of editor groups. The grid is `SerializableGrid` over `SplitView`
(`vs/base/browser/ui/grid/`): a tree whose branch nodes are split views
in one orientation holding their children's sizes, and whose leaf nodes
are the groups. A group (`EditorGroupView`) owns its list of editors
(`EditorGroupModel`: the editors in order and in most-recently-used
order, which is active, which are pinned, sticky or a preview) and draws
its own tab bar over the editor's control. Only one group is active;
focus and the title bar follow it, and an editor opened without a target
lands in it. Each editor is an instance of its own, so a group's editors
are independent of every other group's, and the same file can be open in
two groups at once.

Splitting is `addGroup(location, direction)`: the grid inserts a sibling
in the location's parent when that split has the right orientation, else
replaces the leaf with a split of two. Dragging a tab or a file over a
group's editor area (`EditorDropTarget`) divides the area into a middle
and four edges: an edge within a threshold of the pointer splits in that
direction and shows an overlay over the half the editor would take, the
middle moves or copies the editor into the group. Dragging onto another
group's tab bar inserts at the tab under the pointer. A group whose last
editor closes is removed when `workbench.editor.closeEmptyGroups` is on,
the default; *Split Editor* without a direction follows
`workbench.editor.openSideBySideDirection`. The grid's shape and sizes are
serialized (`editorpart.state`, in the workspace's storage), so a window
reopens with its arrangement; a second window has its own.

## SiYuan: Layout, Wnd and Tab

SiYuan's editor area (`app/src/layout/`) is a tree of `Layout` nodes,
each with a direction (`lr`, left to right, or `tb`, top to bottom), a
size and its children, which are `Layout`s or `Wnd`s. A `Wnd` is a pane:
a tab header bar and a container, holding `Tab`s, each tab a model of
its own (a `Protyle` editor for a document, a PDF viewer for an asset,
search, backlinks…) so two panes showing two documents are two editors.
The whole tree is kept as JSON (`window.siyuan.layout`, saved to the
workspace's `conf/layout.json`), each node with its `direction`, `size`,
`resize` and children, each tab with its model and whether it is active
or pinned, and the app rebuilds the panes from it at start.

Dragging a tab header over another pane's tab bar inserts it there; over
the pane's content area the drag-over handler divides the area into the
middle and four edge bands and marks the one the pointer is in, and the
drop splits the pane on that side (`Wnd.split(direction)`: a new
`Layout` of the right direction wrapping the pane when its parent runs
the other way) or moves the tab into the pane. A pane whose last tab
closes is removed and its parent collapsed when one child remains. The
tab's own menu offers the splits too, and the arrangement is the
device's: sync carries the documents, not `layout.json`.

## What Gamma took, and what differs

Both are the same shape: a tree of splits whose leaves are tab groups,
drop zones at a group's edges that split it, empty groups removed, the
tree serialized with its sizes. Gamma's `workspace/tabLayout.js` is that
tree as pure functions, and `TabGroups.jsx` draws it with the dock's
resizable panels and the browser's drag, which the strip's reorder
already used ([dev/tab_groups.md](../dev/tab_groups.md)).

The difference is in what a group shows. In both products every tab is
an editor instance of its own; in Gamma the page on screen is App's
state, one page's worth (the tree, the PDF, the ink, the collab session,
the comments), so only one group can hold the workbench. That group is
live; the others show their page as a read view (`PagePane.jsx`: the PDF
with its highlights, or the notes rendered), and a press on a view makes
its group live, carrying the scroll across. The issue's reader gets the
notes live beside the paper to read, which is the case it describes; a
highlight on the paper takes a press on it first. Making every group a
workbench is the page-session extraction of
[dev/frontend-refactor.md](../dev/frontend-refactor.md), which the tree
and frames do not depend on. Gamma also keeps a tab in one group only
(the same page cannot be open twice), syncs the tabs across devices as
before while the arrangement stays in the browser, as both products keep
it per window or device, and draws no groups on a phone.

## Still open

A second workbench, so a read view can be edited in place; a read view
that follows changes made elsewhere; handwriting, text boxes and pictures
drawn in a read view; a group's most-recently-used order for what a
close shows (VS Code's `mru`; Gamma uses the tab history, which is the
account's across groups); a keyboard way to move focus between groups;
the issue's wish for several windows, which the desktop app's shell could
offer and the browser cannot.

What the two-pane UI lacks as drawn, Notability's Multi-Note beside VS
Code's groups, and the design recommended for it, the same chrome on every
pane first and the second workbench after, and what was built of it:
[split-panes.md](split-panes.md).
