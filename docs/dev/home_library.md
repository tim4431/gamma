# Home library

Folders, labels, the card surfaces, the recents strip, and cover snapshots.
Code: [fileBrowser.jsx](../../frontend/src/fileBrowser.jsx),
[libraryUtils.js](../../frontend/src/libraryUtils.js),
[menus.jsx](../../frontend/src/menus.jsx), glue in App.jsx.

## Folders and labels

Folders are "folder labels" — `properties.folder` on a page block is a
comma-separated list of paths (`"readout/nondestructive, cooling"`); `/` nests,
a page can be in several folders (drag/add is a soft link, only an ancestor tag
gets refined away), no tags = library root. The folder tree is derived from the
paths in use (plus localStorage-only empties); rename/delete are prefix
rewrites across pages. Standard labels stay in `properties.category` — the two
are distinguished by property, never by string convention.

The paper view's label frontmatter edits both: typed input containing `/`
becomes a folder label (`cs229/` → folder cs229, `cs229/hw` → subfolder;
suggestions offer existing folder paths with a folder icon), everything else a
flat label.

## Listing, sorting, filtering

The root view is a recents feed of ALL pages, rendered incrementally (30 +
IntersectionObserver load-more). Folders and files render as ONE merged sorted
listing (list and grid): date sorts rank a folder by its most recent contained
page, Title A–Z intermixes by name; a KindToggle picks what the listing shows —
folders + files, folders only, files only, or **labels**.

The sort choice (updated/created/viewed/title, an iconed MenuSelect pill;
"viewed" ranks by the account-synced view history with modified time as
tie-break) and the kind filter are both per-VIEW — localStorage
`gamma-home-sort-map` / `gamma-home-kinds-map`, keyed by folder path with
`""` = root and `"#<label>"` for a label view, seeded from the older global
`gamma-home-sort` / `gamma-home-kinds` keys; a view without an entry inherits
from its nearest ancestor folder (a label view inherits the root).

**The label view** is the flat mirror of the folder view, not a separate
surface: the KindToggle's Labels mode lists the labels carried by the pages in
scope (`labelMeta`, the label twin of `folderMeta` — count + latest
modified/added/viewed, so labels sort by the same clock), as the same rows and
cards folders use with a tag glyph. Click selects, double-click opens, a paper
dropped on one gets that label, right-click is the existing label
rename/delete menu. Opening a label KEEPS the folder scope (`?folder=…` and
`?category=…` can both be in the URL — `homeUrlFor`), so a label opened inside
a folder reads as "this folder, narrowed to that label"; its browse bar is the
same back row + breadcrumb, ending in a label crumb, and dropping a paper on
its back row takes the label off. Inside a label there are only papers, so the
KindToggle hides and the kind filter is ignored there.

A search box sits left of the sort pill (`ListFindBox`, live as you type, per
view, not persisted). It never drops anything: matching items float to the top
of the current sort and the rest stay in place dimmed (`.homeDim`). A page
matches on its title plus its folder/label chips; matching is
case/diacritic-folded, every whitespace term must appear.

New folder is the FIRST item of the listing itself, not a toolbar button (a
`folderNewBtn` row / `pageCardAdd` tile that turns into its own name input in
place — Enter or blur commits, Escape cancels); it is hidden while the folder
is filtered to files-only, to labels, or inside a label view. The toolbar is
search box → sort → kind → list/grid.

Search chips (Tab autosuggest) cover both kinds: label chips match exactly,
folder chips match by prefix.

## The card

One shared card (`PageCard` in `fileBrowser.jsx`) renders every home card
surface — the "Recently viewed" strip, the pinned strip, and the grid
listing's files, folders AND labels: a cover over a bottom-stuck
footer of title + folder/label chips + kind and relative time (library cards
show the time matching the active sort — viewed/created/modified; the recents
strip always shows viewed). Only the recents strip shows snapshot covers;
library cards always use the glyph.

The card geometry lives on the card itself, not per surface: a 16/10 cover, two
reserved title lines and one reserved chip line (`CardLabels` renders its span
even when empty, unless labels are off — so pass `labelMode` on EVERY card,
folder placeholders included), plus one `--card-w` for the fixed-width strips
and the grid's minimum track. Recents, pinned and grid cards are therefore the
same card; only the grid stretches it to fill the row, and the recents strip
(`.recentsCarousel`) overrides `--card-w` to render slightly larger cards.

The chips (`CardLabels`, also reused by the list rows) are display-only and
gated by Settings → General → "File labels" (`gamma-home-file-labels`:
off/labels/folders/both).

## Recents and snapshots

The recents queue itself syncs across devices (whole-list last-write-wins via
the `recent-views` prefs key, tabs-style — a union merge would resurrect
×-removed entries).

Snapshots are captured client-side from the rendered pdf.js canvases at the
last-read spot (debounced on scroll-settle + a post-render retry loop in
App.jsx, `captureViewerSnapshot`) and stored server-side in the per-user
`page_snaps` table via `/api/page-snaps` ({pageId: {img, at}}, JPEG data URLs,
per-page newest-`at` wins, server prunes past `PAGE_SNAPS_CAP`; too big for the
64KB prefs KV) so covers follow the strip to every device — batched pushes, a
full pull + local-heal on login, `?after=` delta pulls on window focus;
localStorage `gamma-page-snaps:<user>` is only the instant-paint cache, pruned
to the pages still in the 24-entry recents queue; Settings → General →
"Recents thumbnails" (`gamma-recent-thumbs`) swaps covers to the plain glyph
and stops capturing.

Each recents card's hover × removes the entry (and its snapshot) account-wide;
the strip has no arrow chrome — a vertical mouse wheel pans it sideways (native
non-passive listener in `CardCarousel`), touch swipes natively.

## The context menu

The home right-click menu (page/folder/label) is built from the `menus.jsx`
primitives: a "Move to folder" flyout lists every folder path ordered by the
*active home sort* (`folderMenuPaths`, via the library-wide `folderMeta`
rollup), checks the ones the selection already carries, and ends with the
per-tag "remove from" rows; adding still uses the soft-link `addPagesToFolder`
(same as dropping a card on a folder). Every page card surface opens the SAME
menu — the Recently-viewed strip and the pinned strip included.
