# Home library

Folders, labels, the card surfaces, the recents strip, and cover snapshots.
The **Organize your library** tour teaches this model once the library
passes 20 pages: a label is anything you will look for later, a folder is a
label that nests, and filing never copies the page
([onboarding.md](onboarding.md)).
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

The library is one request, `GET /blocks/root/children`. It returns a page
summary per page, not the page block: its title, times, `preview` and the
few properties these surfaces read. Those are the attachment fields,
`folders`, `labels`, `pinned`, `seeded`, and `meta`'s `title` / `authors` /
`year` / `venue` / `volume` / `doi` / `arxiv_id`, read by quick open, the
chat and link pickers and the DOI / arXiv matching of an opened link.
Beside the pages come the folder and label trees, each with the op-log
position it reflects ([api.md](api.md) "The library listing"). A surface
that needs more of a page (BibTeX, citation, summary, full metadata) reads
the page itself; the metadata popover, the export dialog and the page
header all work on the open page's block.

Quick open ([QuickOpen.jsx](../../frontend/src/library/QuickOpen.jsx)) is the
keyboard way into the library from anywhere: Ctrl+P (the `app.quickOpen`
command, [hotkeys.md](hotkeys.md); not in a share view) opens a palette over
the library.

- Its page rows are `rankLibraryPages`
  ([librarySearch.js](../../frontend/src/library/librarySearch.js)) drawn by
  [PageOption.jsx](../../frontend/src/library/PageOption.jsx), both shared
  with the chat's `@` picker; the chat's `+` page picker uses the ranking
  ([ai.md](ai.md#mentioning-library-papers)).
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

Folders and labels are blocks (migration step 29 converts older
libraries, [migrations.md](migrations.md)). Two reserved parentless rows sit beside
`root` and `trash`: `folders` and `labels` (`blocks_store.FOLDERS`,
`LABELS`). A folder is a block under `folders` or under another folder —
its `content` the name, its `position` its place among its siblings, its
`properties` like any block's (`pinned`, below); a label is a block right
under `labels`, flat. Names may hold any character, `/` and `,` included; a
path reads as the names joined with ` / ` (`blocks_store.PATH_SEP`). Every
block of a tree has the tree's id as its `page_id` and `kind` `folder` /
`label` ([user_db.md](user_db.md) "pages.db").

A page is filed by id: `properties.folders` is a list of folder ids,
`properties.labels` a list of label ids. Filing stays many-to-many — a page
may be in several folders (drag/add is a soft link; filing into a subfolder
refines the folder above away), no folders = library root. "Filed in F or
below" is "carries F or a folder below it" (`blocks_store.folder_subtree_ids`,
`pages_in_folder`, `root_pages(conn, folder_id)`), the one rule for a
folder view, a folder share, a folder export, the search scope and a folder
chat's agent. Folders are real rows, so an empty folder exists for everyone
and syncs; there is no derived tree.

Every change to a tree is an op on it, the trees being pseudo-pages to the
op path ([collab.md](collab.md) "The folder and label trees"): a new folder
is an `insert` under `folders` (or a folder), a rename one `set` of its
content, a move or a reorder one `move`, a pin a `set` of
`properties.pinned` — `POST /api/pages/folders/ops`, `…/labels/ops` for
labels. None of these touches a page: no page's `updated_at`, op log or
change-log row moves, so the "modified" sort, the change feed and the
mirrors see no edit. Deleting is the one change
that reaches pages: `DELETE /api/folders/{id}` deletes the folder with its
subfolders and, in the same transaction, takes their ids off every page
that carried them (one op batch per page); the pages stay, filed elsewhere
or at the library root. The deleted folders' AI chats are filed into the
library chat's history (`home`) and their share links stop.
`DELETE /api/labels/{id}` takes a label off every page. A page that still
names a deleted folder or label (one removed by a raw op, or on another
copy of the workspace) passes it by on every read, and the next refiling
of the page by any writer drops it ([collab.md](collab.md)).

Everything else that names a folder names its id: a folder's AI chat bucket
(the folder view's chat; `home` at the root), a folder share
(`shares.folder`), the search scope (`?scope=<folder id>`), the clip
target, an import's destination, a folder export, the home URLs
(`?folder=<id>`, `?label=<id>`). The agent's tools keep taking and showing
paths, resolved by name ([ai_tools.md](ai_tools.md)).

In the app the trees arrive with the listing. App keeps them as `libTree`
(`libraryTree` in [libraryUtils.js](../../frontend/src/library/libraryUtils.js):
id → folder with its name, parent, position, pin, path and subfolders; id
→ label), and every surface names folders and labels from it. A page
row's `_folders` / `_labels` are the ids of its filing the trees have
(`filedIn`: a dangling id shows nothing). `_folderChips` / `_labelChips`
are the same as `{id, name}`, a folder named by its path. App writes the
trees through `treeOps` (one batch on `folders` or `labels`). It files a
page through `filePage`, a `set` of the whole `folders` / `labels` list
from `filedIn`, so a refiling drops a dangling id. A folder or label made
by name goes through `ensureFolders` / `makeLabel`. They reuse one of that
name (exactly, else ignoring case, like the server's `blocks_store.named`)
and run one after another, so a name committed twice is made once.

The home view does not join the trees' rooms. It reads the listing again
after each of its own writes and when the window comes back (focus or
visibility, at most every 15 s); that is when another device's changes
show. The listing's pages are not live either, so one read keeps both in
step. A view naming a folder or label the trees lack (deleted elsewhere, a
stale link) goes back to the library's root (a share's folder).

The page header's label field (`page.labels` / `page.labelInput`) files
the open page by both. Its suggestions are the folders (by path, a folder
icon) and labels whose names contain what is typed, then *New label "q"*
and *New folder "q"* (a top-level folder) when no label, or no top-level
folder, has that name. Enter on a suggestion picks it, and Tab picks the highlighted
suggestion or else the first; Enter (or leaving
the field) with none picked takes the label of that name, else the folder
whose path or name it is, else makes a new label. Each pick writes the
page at once, a folder with the same refinement as dropping the page on
it; a name may hold any character, "," and "/" included. Backspace in the
empty field takes the last label off. The browser suite's `folders` group
(`frontend/tests/e2e/scenarios/folders.mjs`) covers the folder and label
flows, and its harness files pages by path through `Account.folder` /
`label` / `file`.

## Listing, sorting, filtering

The root view is a recents feed of ALL pages, rendered incrementally (30 +
IntersectionObserver load-more). Folders and files render as ONE merged sorted
listing (list and grid): date sorts rank a folder by its most recent contained
page, Title A–Z intermixes by name; a KindToggle picks what the listing shows —
folders + files, folders only, files only, or **labels**.

The sort choice (updated/created/viewed/title/custom, an iconed MenuSelect
pill; "viewed" ranks by the account-synced view history with modified time
as tie-break) and the kind filter are both per-VIEW — localStorage
`gamma-home-sort-map` / `gamma-home-kinds-map`, keyed by the folder's id
with `""` = root and `"#<label id>"` for a label view; a view without an
entry inherits from its nearest ancestor folder (a label view inherits the
root).

**Custom order** is the folders' own order: their `position` in the folder
tree, which every member sees alike, then the pages in the library's order
(labels keep the label tree's). In it, a folder dropped on the leading or
trailing edge of a listed folder (a row's top or bottom quarter, a tile's
left or right one; `folderDropEdge`) lands beside it; a selection of
several lands there in its listed order. That is one `move` per folder,
in one batch, with fractional keys between the new neighbours
(`folderPositions`, `placeFolders`), and the edge shows a line
(`.dropBefore` / `.dropAfter`). A drop in the middle moves them in. A folder drag carries its id
under its own type (`FOLDER_DRAG_TYPE`) beside the text payload, since a
drag-over can read only the types.

**The label view** is the flat mirror of the folder view, not a separate
surface: the KindToggle's Labels mode lists the labels in scope as the same
rows and cards folders use, with a tag glyph. At the root that is every
label (a label is a block, carried or not), in a folder the labels its
pages carry. `labelMeta`, the label twin of `folderMeta`, gives each a count
and its latest modified/added/viewed time, so labels sort by the same
clock. Click selects, double-click opens (on
touch a tap opens, as for pages and folders: `isTap` in App.jsx, which
asks `lastPointer()`), a paper dropped on one gets that label, and its menu
(right-click, a held finger or the row's "⋯") is the existing label
rename/delete menu. Opening a label KEEPS the folder scope (`?folder=<id>`
and `?label=<id>` can both be in the URL — `homeUrlFor`), so a label opened inside
a folder reads as "this folder, narrowed to that label"; its browse bar is the
same back row + breadcrumb, ending in a label crumb, and dropping a paper on
its back row takes the label off. Inside a label there are only papers, so the
KindToggle hides and the kind filter is ignored there.

The labels listing ends with a **"No label"** pseudo-label (dashed tag glyph,
pinned last regardless of sort, shown only while some page in scope carries
no label): `labelMeta` rolls unlabelled pages up under the `NO_LABEL`
sentinel from `library/libraryUtils.js` — `~none`, an id no block can have
(`~` is no block-id character) — and `labelName`
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
Ctrl+P and the chat's page pickers): the workspace search's typo-tolerant title scorer, case/diacritic-
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
page count), the date the active sort uses, the pin slot, kept empty on
rows without a pin, and the row's "⋯" (`MenuButton`), its slot kept on a
row without a menu ("No label"). The date is `formatShortDate` from libraryUtils.js
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
label view. Committing inserts the folder block, last among its siblings:
an empty folder is real, shared and synced like any; a name a sibling has
already makes nothing. The toolbar is filter box → sort → kind → list/grid
→ Select.

**Selecting.** A click selects, Ctrl/⌘+click toggles one item and
Shift+click extends a range; a finger's tap opens instead, as in a phone's
file manager (`isTap`). **Select** in the toolbar is the way to pick
several without a Ctrl key: in Select mode (`selecting`) every click or tap
toggles, a picked row's glyph and a picked card's corner show a check
(`SelectCheck`), and the toolbar shows "n selected", a "⋯" that opens the
menu a right-click on the selection would, and Done. Clearing the
selection, Escape included, ends the mode.

**/page** in a block's editor makes a page too, as in Notion (App's
`createLinkedPage`). The block's text gets the `[[id]]` link at once, under
an id minted in the browser; the chip's label is cached with it, so the id
is never looked up before the page exists. Then `POST /api/pages {id,
folders}` files the page in the open page's folders, and it opens like a
New page, with its title ready to type; Back returns to the link. A
creation that fails takes the link back out of the block. A share's editor
does not offer the command.

In the compact layout (`.phoneUI`: phones and upright tablets,
[ipad.md](ipad.md)) the toolbar wraps. At the library's root its label
becomes a large "Library" heading (`homeListRoot`); a folder or label view
keeps the small label, since the breadcrumb above names it. The filter box
takes the full width, then sort, kind and list/grid share one row. A file
row gives the title its own wrapping line, with the folder and label chips
on a second line (the Phone block at the end of `library.css`).

Search chips (Tab autosuggest; Enter stays the search's own key) cover both kinds, named from the trees: a
label chip keeps the pages carrying that label, a folder chip (its path)
the pages filed in the folder or below it.

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
(`gamma-home-file-labels`: off/labels/folders/both); a label chip's menu
opens on a right-click or a held finger.

A card takes its menu as `onMenu`: a right-click or a held finger
(`menuPress`), or the "⋯" in the cover's top-left corner
(`.pageCardMenu`; the pin and the recents × keep the top-right,
`.pageCardCorner`). Where a pointer
hovers, the "⋯", the × and an unpinned pin show with the card; where none
does the "⋯" and the × are always there and an unpinned pin is not (Pin
is in the menu). A list row's pin follows the same rule.

## Pinned

The Pinned strip at the library root holds pages and folders, most recently
pinned first. A pin is stored on its block, the same for both:
`properties.pinned` = ISO timestamp, on the page (`setPagesPinned`, a `set`
on the page) or on the folder block (a `set` on the `folders` tree). A
folder's pin is the workspace's, like a page's: every member sees it.
Pin/Unpin is on the folder context menu (acts on the folder selection when the clicked
folder is part of one); a tab's right-click menu offers "Pin to library" for
its page next to "Pin tab" (the tab-strip pin, a different thing); the
strip's folder card is the grid's folder card with an unpin button, and it
is a drop target like any folder. A pin moves and renames with its folder
and goes with it. `setFoldersPinned` writes the pins of the acted-on
folders in one batch on the tree, and the strip reads `pinned` off
`libTree`.

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

Each recents card's × (on hover, always where nothing hovers) removes the entry (and its snapshot) account-wide;
the strip has no arrow chrome — a vertical mouse wheel pans it sideways (native
non-passive listener in `CardCarousel`), touch swipes natively.

## The context menu

The home context menu (page/folder/label) opens on a right-click, a held
finger or the item's "⋯" ([ui-design.md](ui-design.md#one-behaviour-for-mouse-and-finger)).
It is built from the `shared/ui/Menus.jsx`
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
  label…", a name typed in place (the label of that name, else a new one:
  `makeLabel`).
- Share…, Export… and Ask AI act on the page itself, so they open it first
  and run once it is on screen (`openPageThen`).
- A folder: Open | New page here · New subfolder | Move to ▸ · Move up ·
  Move down | Rename · Pin · Share… · Export… | Delete.
- The folder's Move to ▸ lists the top level ("All files") and every
  folder the acted-on folders can go into: none of them, nor one below
  them (`folderTargets` in `library/libraryUtils.js`), checked where they
  already are; a pick is `moveFolders`, as a drop on the folder is. Move
  up and Move down show in the custom order only, for one folder, and
  step it past its neighbour among its siblings (`folderNeighbour`,
  `placeFolders`), greyed at either end. Moving and ordering folders is
  then possible without a drag.

The "Move to folder" flyout lists every folder by its path, ordered by
the *active home sort* (`folderMenuIds`, via the library-wide `folderMeta`
rollup; the custom order keeps the tree's) and checks the ones the
selection already carries. It ends with the "remove from" rows, one per
folder. Adding uses the soft-link `addPagesToFolder`, the same as dropping
a card on a folder, a folder's tile or row, or a crumb of the folder path
above the listing (`dropOnFolder`); the back row takes the pages out of
the open folder (`removePagesFromFolder`).

A folder's menu also has **Share…**. It opens the folder, then the share
popover under the topbar's link button, which an open folder shows as a
page does. The popover is `sharing/SharePopover.jsx` with a folder `target`:
the page's popover with folder wording, without the Gamma Cloud and
Citation sections. Its one link reaches every page filed in the folder or
below it, now and later, and a folder moved into it brings its pages
([api.md](api.md) "Shares"). The share and the folder's chat name the
folder by id, so a rename or a move leaves both as they are. A delete stops
the folder's shares (and its subfolders') but never drops a chat: each
conversation is filed into the library chat's history (`home`), whichever
of "Keep pages" / "Delete pages too" was chosen. The folder menu's Delete
(`deleteFolder`) calls `DELETE /api/folders/{id}`, after trashing the pages
for "Delete pages too"; a label's (`deleteLabel`) calls
`DELETE /api/labels/{id}`. A rename is one `set` on the tree and asks
nothing: no page changes.

## Recently deleted

Deleting a page moves it to a 30-day trash instead of removing it. The
page menu's Delete, the Del key, the page header's trash button and a
folder's "Delete N pages too" all call `DELETE /api/blocks/{id}` on the
page root, and the server moves the page there (`ops.trash_page`). The
confirmations say where it goes and for how long.

On the server, the trash is a reserved block `trash` beside `root`
(`blocks_store.TRASH`). A trashed page is moved under it, and `deleted_at`
and `deleted_by` (the actor: an account's id, shown by its username —
`trash.named`) are stamped in its properties. Its blocks, files, chats,
op log and filing (`folders`, `labels`) stay as they were, and every row keeps its
`page_id`. Everything that finds pages asks for `parent_id = 'root'` or for
a block's page in the library (`page_root_id`: its `page_id`, if that page
is under `root`). So a
trashed page drops out of the library, folder counts, searches, backlinks,
`[[ref]]` resolution, shares, the agent's and MCP's tools and exports
without code of its own. Its blocks read as not found, and ops
to it are refused. The block readers that scan across pages (block search,
backlinks) keep to `blocks_store.IN_LIBRARY` (a row whose page is under
`root`). To a copy of the workspace the
page is deleted: trashing turns its row of the change log (`page_changes`)
`deleted`, as a hard delete does, and the change feed lists it as a
tombstone ([collab.md](collab.md) "The change feed"). Deleting it for good
later keeps that row.

Restoring (`ops.restore_page`) puts the page back under `root`, last in the
library, filed where it was (a folder deleted meanwhile is passed by). It touches the page `live` again, so the
change feed shows the page as created again. A mirror
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
rows (a row's folder: the first of its entry's `folders` ids the folder
tree has, by its path). Each row has Restore and a trash button that deletes for good after
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
(`frontend/tests/e2e/scenarios/trash.mjs`) covers the flow.
