# Home library

Folders, labels, the card surfaces, the recents strip, and cover snapshots.
Code: [FileBrowser.jsx](../../frontend/src/library/FileBrowser.jsx),
[libraryUtils.js](../../frontend/src/library/libraryUtils.js),
[Menus.jsx](../../frontend/src/shared/ui/Menus.jsx), glue in App.jsx.

## What a viewer may do

[libraryAccess.js](../../frontend/src/library/libraryAccess.js) derives one
object, `lib` in App.jsx, from how the library was reached. Every affordance
of the home library asks it, never a role or a share token:

- `root`: the folder the view is confined to, a folder share's folder or ""
  otherwise. `openFolder` clamps into it; the back row and the breadcrumb
  stop at it.
- `browse`: there is a library to list (a page share has none).
- `organize`: New page / New folder, drags and drops, rename, move,
  duplicate, delete, labels, sharing a folder, file drops onto the library,
  Recently deleted (below).
- `pin`: the pin buttons and the pinned strip.
- `history`: the recents strip.

A workspace viewer browses everything and organizes nothing. A folder
share's visitor browses the shared folder only: the share view is the home
library at that folder, listed through `GET /blocks/root/children` with the
token ([api.md](api.md) "Shares"). Its pages open in the same view (`page=`
beside the token, each a history entry). The topbar's home button, and each
crumb of the folder path that leads the page's title, return to a folder
listing.

Quick open ([QuickOpen.jsx](../../frontend/src/library/QuickOpen.jsx)) is the
keyboard way into the library from anywhere: Ctrl+P (the `app.quickOpen`
command, [hotkeys.md](hotkeys.md); not in a share view) opens a palette over
the library.

- With no query it lists pages under three headings: Recent (the recents
  queue), Open tabs, and Everything else by last edit. Each row shows its
  time on the right, viewed for a recent page and modified otherwise; the
  open page says Current.
- A query ranks through `createLibraryMatcher` (the listing's filter box
  matcher, below), recency breaking ties. Results fall under Pages, Folders
  (matched on the name or the path) and Labels. The matched characters are
  marked (`MarkedText` in SearchPanel.jsx over `markedParts` from
  `search/snippets.js`); a typo-only match stays unmarked. A page row shows
  its folder and label chips (`CardLabels`, as on a file row), so a
  label-only match reads as one.
- A typed list ends with two actions. *Search inside notes and PDFs for "q"*
  (Ctrl+Enter) opens the workspace search with the query (App's
  `openSearchWith`). *Create page "q"* (Shift+Enter, only where
  `lib.organize`) calls `createPage` with that title, in the folder the home
  view has open.
- Enter opens a page through `openPage`, the same path as a card
  double-click, and a folder or label in its home view. A query starting
  with `>` is the command palette ([hotkeys.md](hotkeys.md)).

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
cards folders use with a tag glyph. Click selects, double-click opens (on
touch a tap opens, as for pages and folders: `isTap` in App.jsx), a paper
dropped on one gets that label, right-click is the existing label
rename/delete menu. Opening a label KEEPS the folder scope (`?folder=…` and
`?category=…` can both be in the URL — `homeUrlFor`), so a label opened inside
a folder reads as "this folder, narrowed to that label"; its browse bar is the
same back row + breadcrumb, ending in a label crumb, and dropping a paper on
its back row takes the label off. Inside a label there are only papers, so the
KindToggle hides and the kind filter is ignored there.

The labels listing ends with a **"No label"** pseudo-label (dashed tag glyph,
pinned last regardless of sort, shown only while some page in scope carries
no label): `labelMeta` rolls unlabelled pages up under the `NO_LABEL`
sentinel from `library/libraryUtils.js` — a string containing a comma, which no real
label can be since `parseFolderTags` splits on commas — and `labelTitle`
turns it into the display name. Opening it (`?unlabelled=1` in the URL,
`homeUrlFor`) lists the pages without any label; a paper dropped on its tile
loses all its labels (`clearPagesLabels`). It has no rename/delete menu and
its back row is plain navigation.

A filter box sits left of the sort pill (`ListFindBox`, "Filter by title or
label", live as you type, per view, not persisted; Ctrl+F on the home page
focuses it). It never drops anything: matching items float to the top
of the current sort and the rest stay in place dimmed (`.homeDim`). A page
matches on its title or its folder/label chips through `createLibraryMatcher`
([librarySearch.js](../../frontend/src/library/librarySearch.js), shared with
Ctrl+P): the workspace search's typo-tolerant title scorer, case/diacritic-
folded, every whitespace term must hit the title or a chip, and title hits
outrank chip-only ones.

The box reads no notes or PDF text, and points to the search that does
(`ListSearchElsewhere`). With nothing matched, a banner above the listing
("No title or label matches …") offers *Search inside notes and PDFs* with
the full search's shortcut. Enter in the box does the same. With matches, a
quiet row right after the last match offers it. Both open the workspace
search with the query filled in (SearchPanel's `initialQuery`, App's
`openSearchWith`). Neither is a `.fileRow` / `.folderRow`.

The list view is a table. Every row ends in the same trailing columns
(`rowColumns` in App.jsx): the kind ("PDF", "Page", or a folder's or label's
page count), the date the active sort uses, and the pin slot, kept empty on
rows without a pin. The date is `formatShortDate` from libraryUtils.js
("Today, 9:41", "Yesterday", "Sep 12", else the year), with the full date in
its title. It reads the same clock as a card's `cardTime`. A thin header
(`.fileListHead`, not a `.fileRow`) names the columns, and its date column
follows the sort (Modified / Viewed / Added). A PDF's file glyph is tinted
with `--kind-pdf`. Under 600 px the date column hides.

**New page** and **New folder** are the FIRST items of the listing itself,
not toolbar buttons. New page (`newPageAllowed`: not in a label view, not
while only folders show) is a `pageCardAdd` tile / `folderNewBtn` row that
creates a blank page in the open folder via `POST /api/pages` and opens it
with the title ready to type — creating a page never needs a file; a PDF is
attached on the page afterwards (the paperclip in the page header, or a PDF
dropped on the open page). New folder is the same tile/row shape and turns
into its own name input in place (Enter or blur commits, Escape cancels); it
is hidden while the folder is filtered to files-only, to labels, or inside a
label view. The toolbar is filter box → sort → kind → list/grid.

In the compact layout (`.phoneUI`: phones and upright tablets,
[ipad.md](ipad.md)) the toolbar wraps. At the library's root its label
becomes a large "Library" heading (`homeListRoot`); a folder or label view
keeps the small label, since the breadcrumb above names it. The filter box
takes the full width, then sort, kind and list/grid share one row. A file
row gives the title its own wrapping line, with the folder and label chips
on a second line (the Phone block at the end of `library.css`).

Search chips (Tab autosuggest) cover both kinds: label chips match exactly,
folder chips match by prefix.

**Start your library.** A library with nothing of the user's in it yet
shows `library/LibraryEmpty.jsx` at the root, in both views, instead of the
empty line. Nothing of the user's means no root pages, or only seeded ones
(`properties.seeded`, the Welcome page every new account starts with):
`isFreshLibrary` in `library/libraryUtils.js`, the same test the first
tour's offer uses. The panel sits above the New page / New folder items and
the Welcome page: a dashed card with the mark and four action cards. They
open a paper from a link (the Add popover, its URL field focused), upload
files (the Add menu's file types, `PAGE_FILE_ACCEPT` in
`shared/lib/utils.js`), start a blank note page (`createPage`) and open the
Import dialog. A footer offers the first tour when it can start, and the
drop tip. The panel shows only once the listing has loaded, only where the
library can be organized (`lib.organize`: not for a workspace viewer or a
share visitor), and never in a folder or label view, which keep their own
messages. It goes with the first page of the user's own. Its anchor is
`home.empty` (view `fresh`, so the browser suite's presence check skips it).

## The card

One shared card (`PageCard` in `library/FileBrowser.jsx`) renders every home card
surface — the "Recently viewed" strip, the pinned strip, and the grid
listing's files, folders AND labels: a cover over a bottom-stuck
footer of title + folder/label chips + kind and relative time (library cards
show the time matching the active sort — viewed/created/modified; the recents
strip always shows viewed). The kind is what the page carries
(`pageKindLabel` — "PDF" or "Page"; never a note/paper dichotomy). Covers:
the recents strip shows the snapshot when one exists (PDF pages capture
one); a page without a snapshot shows a text preview of its first blocks
(`preview` on the root listing, rendered as `.pageCardPreview`) on every card
surface; only a page with neither falls back to the glyph.

The card geometry lives on the card itself, not per surface: a 16/10 cover, two
reserved title lines and one reserved chip line (`CardLabels` renders its span
even when empty, unless labels are off — so pass `labelMode` on EVERY card,
folder placeholders included), plus one `--card-w` for the fixed-width strips
and the grid's minimum track. Recents, pinned and grid cards are therefore the
same card; only the grid stretches it to fill the row, and the recents strip
(`.recentsCarousel`) overrides `--card-w` to render slightly larger cards.

The chips (`CardLabels`, also reused by the list rows) are display-only and
gated by the Folders and Labels switches of Settings → Appearance → Library
(`gamma-home-file-labels`: off/labels/folders/both).

## Pinned

The Pinned strip at the library root holds pages and folders, most recently
pinned first. A page pin is stored on the page (`properties.pinned` = ISO
timestamp, `setPagesPinned`). A folder has no block of its own, so folder
pins are a synced pref: `/api/prefs/pinned-folders` holds `[{path, at}]`,
whole-list last-write-wins like the recents queue, with
`gamma-pinned-folders:<user>` in localStorage as the instant-paint cache
(`updatePinnedFolders` / `setFoldersPinned`). Pin/Unpin is on the folder
context menu (acts on the folder selection when the clicked folder is part of
one); a tab's right-click menu offers "Pin to library" for its page next to
"Pin tab" (the tab-strip pin, a different thing); the strip's folder card is the grid's folder card with an unpin button,
and it is a drop target like any folder. Folder rewrites carry pins along:
`applyFolderMap` (rename/move) and `deleteFolderByName` remap the list
(`remapPinnedFolders`), and a pin whose path no longer exists in
`allFolderPaths` is not shown.

## Recents and snapshots

The recents queue itself syncs across devices (whole-list last-write-wins via
the `recent-views` prefs key, tabs-style — a union merge would resurrect
×-removed entries).

Snapshots are captured client-side from the rendered pdf.js canvases at the
last-read spot (debounced on scroll-settle + a post-render retry loop in
App.jsx, `captureViewerSnapshot`) and stored server-side in the
`page_snaps` table via `/api/page-snaps` ({pageId: {img, at}}, JPEG data URLs,
per-page newest-`at` wins, server prunes past `PAGE_SNAPS_CAP`; too big for the
64KB prefs KV) so covers follow the strip to every device — batched pushes, a
full pull + local-heal on login, `?after=` delta pulls on window focus;
localStorage `gamma-page-snaps:<user>` is only the instant-paint cache, pruned
to the pages still in the 24-entry recents queue. The table is the
workspace's (its `data.db`), so writing to it takes an editor: a viewer of a
shared workspace keeps its captures in that cache and pushes nothing
(`snapsWritableRef`). The Thumbnails switch of
Settings → Appearance → Library (`gamma-recent-thumbs`) swaps covers to the
plain glyph and stops capturing.

Each recents card's hover × removes the entry (and its snapshot) account-wide;
the strip has no arrow chrome — a vertical mouse wheel pans it sideways (native
non-passive listener in `CardCarousel`), touch swipes natively.

## The context menu

The home right-click menu (page/folder/label) is built from the `shared/ui/Menus.jsx`
primitives, in groups split by dividers (`menuGroups`). Every page card
surface opens the SAME page menu, the Recently-viewed strip and the pinned
strip included:

- A page: Open (Enter) · Rename (F2) | Pin · Add label ▸ · Move to folder ▸ ·
  Duplicate | Copy link · Share… · Export… · Ask AI about this page |
  Delete (Del). The shortcuts are each row's `keys`: the effective chords
  of `app.renameTitle` and `app.deletePages`, which act on the one / all
  selected pages of the listing.
- "Add label" lists every label in the library, checked when all the
  acted-on pages carry it (a click then removes it). It ends with "New
  label…", a name typed in place.
- Share…, Export… and Ask AI act on the page itself, so they open it first
  and run once it is on screen (`openPageThen`).
- A folder: Open | New page here · New subfolder | Rename · Pin · Share… ·
  Export… | Delete.

The "Move to folder" flyout lists every folder path ordered by the *active
home sort* (`folderMenuPaths`, via the library-wide `folderMeta` rollup) and
checks the ones the selection already carries. It ends with the per-tag
"remove from" rows. Adding uses the soft-link `addPagesToFolder`, the same
as dropping a card on a folder.

A folder's menu also has **Share…**. It opens the folder, then the share
popover under the topbar's link button, which an open folder shows as a
page does. The popover is `sharing/SharePopover.jsx` with a folder `target`:
the page's popover with folder wording, without the Gamma Cloud and
Citation sections. Its one link reaches every page filed in the folder or
below it, now and later ([api.md](api.md) "Shares"). Renaming, moving or
deleting a folder carries its chat buckets and its shares along through
`POST /folders/rename`, one call made before the tag rewrite. A delete drops
the folder's shares but never its chats: each active conversation is filed
into the folder's chat history, under the same key, whichever of "Keep
pages" / "Delete pages too" was chosen.

## Recently deleted

Deleting a page moves it to a 30-day trash instead of removing it. The
page menu's Delete, the Del key, the page header's trash button and a
folder's "Delete N pages too" all call `DELETE /api/blocks/{id}` on the
page root, and the server moves the page there (`ops.trash_page`). The
confirmations say where it goes and for how long.

On the server, the trash is a reserved block `trash` beside `root`
(`blocks_store.TRASH`). A trashed page is moved under it, and `deleted_at`
and `deleted_by` are stamped in its properties. Its blocks, files, chats,
op log and folder labels stay as they were. Everything that finds pages asks
for `parent_id = 'root'` or walks up to a page (`page_root_id`). So a
trashed page drops out of the library, folder counts, searches, backlinks,
`[[ref]]` resolution, shares, the agent's and MCP's tools, exports and the
change feed without code of its own. Its blocks read as not found, and ops
to it are refused. The block readers that scan across pages (block search,
backlinks) skip `blocks_store.trashed_ids`. To a copy of the workspace the
page is deleted: trashing writes the `deleted_pages` tombstone a hard delete
leaves.

Restoring (`ops.restore_page`) puts the page back under `root`, last in the
library, with its folder labels. It stamps the root and clears the
tombstone, so the change feed shows the page as created again. A mirror
that brings the page back under its id (`blocks_store.create_page`)
replaces the trashed copy. So does a merge restore of a backup that has the
page (`ws_backup._merge`): the trashed copy's rows go, and the backup's
version comes back live under the same ids, its chats and op log kept
([workspaces.md](workspaces.md) "Export and backups").

`gamma/trash.py` deletes pages trashed more than `KEEP_DAYS` (30) days ago
for good through `ops.delete_page`, the one path that drops a page's chats
and index rows. The sweeper runs at startup and every hour (`lifespan`).
Delete permanently and Empty take the same path at once. On a share host,
whose pages are published copies, `DELETE /api/blocks/{id}` deletes for good
directly. Endpoints: [api.md](api.md) "Recently deleted". Editors and owners
trash and restore; a viewer and a share link cannot. A folder chat's agent
can list the pages that were filed under its folder and restore one on
request (`list_deleted`, `restore_page`, [ai_tools.md](ai_tools.md)); it
cannot trash or delete. A trashed page's files
stay referenced, so they count against the storage quota until the page is
deleted for good. Then the orphan check takes over
([user_db.md](user_db.md) "Stored files").

In the app, [RecentlyDeleted.jsx](../../frontend/src/library/RecentlyDeleted.jsx)
is a `SubDialog` opened from the trash button at the right end of the home
toolbar (at the library root, where `lib.organize`) or from the
`app.recentlyDeleted` command. It lists each page with its title, who
deleted it and when, the days it has left and its folder, as `aiProvRow`
rows. Each row has Restore and a trash button that deletes for good after
App's confirm box, and the dialog's footer has Empty. A link to a trashed
page gets a 404 whose body carries `trashed` (the page's trash entry). The
missing-page notice then names the page and offers Restore, which restores
it and opens what the link named. A `[[ref]]` or `![[embed]]` naming a block
of a trashed page is missing from the ref lookup (`/block-search?ids=`), so
App's `onFetchRefs` asks `GET /blocks/{id}` for each such id (a member only,
never a share view) and caches the 404's `trashed` entry, or `missing` when
there is none. The chip then shows the page's title struck through
(`.trashedRef`, the dashed look of an unlinked chip) and the card says the
page is in Recently deleted; a click opens the notice with Restore. An id
nothing holds renders as the unlinked chip, its card as "Embedded note not
found." (BlockTree's `refLabelOf`). The browser suite's `trash` group
(`e2e/scenarios/trash.mjs`) covers the flow.
