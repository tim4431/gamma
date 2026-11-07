# Home library

Folders, labels, sub-pages, the card surfaces, the recents strip, and
cover snapshots.
The **Organize your library** tour teaches this model once the library
passes 20 pages: a label is a word you would search for, like a method or
an author; a folder groups papers by topic or project and can hold other
folders; and one paper can sit in several folders without a copy
([onboarding.md](onboarding.md)). The user guide's
[Labels or folders?](../user_guide/user_guide.md#labels-or-folders) gives
the same model with research examples.
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
summary per page, not the page block: its title, times, `preview`, the
page it is inside (`parent`, "Sub-pages" below) and the few properties
these surfaces read. Those are the attachment fields,
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
  ([ai.md](ai.md#mentioning-library-papers)). A sub-page's row names the
  pages it is in ("Sub-pages").
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
chat's agent. A page inside another page carries no folders of its own: it
is filed where its top page is ("Sub-pages" below). Folders are real rows, so an empty folder exists for everyone
and syncs; there is no derived tree.

Every change to a tree is an op on it, the trees being pseudo-pages to the
op path ([collab.md](collab.md) "The folder and label trees"): a new folder
is an `insert` under `folders` (or a folder), a rename one `set` of its
content, a move or a reorder one `move`, a pin a `set` of
`properties.pinned` — `POST /api/pages/folders/ops`, `…/labels/ops` for
labels. None of these touches a page: no page's `updated_at`, op log or
change-log row moves, so the "modified" sort, the change feed and the
mirrors see no edit. Deleting is the one change
that reaches pages: `DELETE /api/folders/{id}` (`ops.delete_tree`, which
the agent's `delete_folder` runs too) deletes the folder with its
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
paths, resolved by name ([ai_tools.md](ai_tools.md)). A folder chat's agent
can also rename folders, make, rename and apply labels, and delete a folder
(as Keep pages does) or a label. Each deletion asks first, and the reply
can revert it ([ai_tools.md](ai_tools.md) "Folders and labels" and
"delete_folder / delete_label").

In the app the trees arrive with the listing. App keeps them as `libTree`
(`libraryTree` in [libraryUtils.js](../../frontend/src/library/libraryUtils.js):
id → folder with its name, parent, position, pin, creation time, path and
subfolders; id → label), and every surface names folders and labels from
it. A page
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
empty field takes the last label off. An open sub-page's field files it by
labels only ("Sub-pages"): it shows no folder chips and suggests no folder
and no *New folder*, its placeholder is "Add a label…", and a name that
matches no label makes a new one rather than picking a folder of that name. The browser suite's `folders` group
(`frontend/tests/e2e/scenarios/folders.mjs`) covers the folder and label
flows, and its harness files pages by path through `Account.folder` /
`label` / `file`.

## Sub-pages

A page can sit inside another page, as in Notion: it is reached through
that page and is not listed in the library. The shapes weighed and why this
one was chosen are in [research/sub-pages.md](../research/sub-pages.md). A
**sub-page** is a page like any other, a row under `root` (`trash` while in
Recently deleted), whose properties carry `parent_page`, the id of the page
it is inside. Nothing is stored on the parent, and everything keyed on a
page (the op path, rooms, the op log and the change log, the notes index,
shares, chats, tabs, recents, the mirror, backups, folder links, the
Zotero link) treats a sub-page as the page it is. A page without the key
is a **library page**. The generated column `parent_page` and its partial
index ([user_db.md](user_db.md) "pages.db") make "the pages inside P" one
indexed read.

**The effective parent** is the one rule every reader uses
(`blocks_store`'s sub-pages section). A live page's parent is its stored
`parent_page` when that names a live page and the chain of such parents
reaches a page without one, holding at most `MAX_PAGE_DEPTH` (64) pages,
its top included, and none twice. Otherwise the page reads as a library
page: a parent deleted for good or not brought by a mirror yet, one in
Recently deleted, an id that is no page, a cycle (only merged copies make
one) and a chain too deep all read as no parent, as a page filed in a
deleted folder reads as unfiled. A page's *ancestors* are that chain, top
first, and its *top* is the first of them (a library page is its own top).
The readers are `page_parents(conn)` (every live sub-page's effective
parent, one query), `page_parent`, `page_ancestors`, `page_top`, `top_of`
(the top from a `page_parents` map in hand), `sub_pages` (the pages right
inside, in library order), `page_descendants` and `with_sub_pages`
(parents before the pages inside them, siblings in library order). The
client's twins in [libraryUtils.js](../../frontend/src/library/libraryUtils.js)
read the listing's `parent` with the same depth limit: `pageAncestors`,
`topPageOf`, `pagePathLabel` (the ancestors' titles joined, "Top › Mid")
and `pageDescendants`.

**The key is written** by a `set` on the page root, which `ops._parent_patch`
checks beside `_filing_patch` ([collab.md](collab.md) "Ops"): null or `""`
removes it, a page inside itself or inside one of its own sub-pages is
refused, and a set drops the page's own `folders`. A parent that is no page
is stored as written, as a folder id is: a mirror may bring a sub-page
before its parent. The page writers (`create_page`, `insert_page_tree`)
keep a `parent_page` of the right shape through `page_root_props`, without
asking whether it names a page. A share editor never sets a page root's
properties, so a share never moves a page.

**Filing by the top.** A sub-page carries no folders of its own: it is
filed where its top is, and `folders` it still carries (written past the
op rule by an import, a raw write or a merged copy) count for nothing.
`pages_in_folder` lists the library pages filed in the folder or below it,
each followed by its descendants, `page_in_folder` asks the question of the
page's top, and `pages_in_scope` tests a folder chip against the top and a
label chip against the page itself. So a folder view, a folder share, the
search scope, a folder export, a folder chat's agent and a folder link on
disk reach a sub-page through its top without code of their own.
`root_pages` without a folder reaches every live page, sub-pages included
(search, export, the agent, reindexing). `blocks_store.filed_folders` is
a page's folders by this rule, which the connector's clip and lookup
answer; a clip into a folder of a paper that is a sub-page files its top
there ([api.md](api.md) "Browser extension"). The app follows the same
rule: the chat's "Save to library" offers the open page's top's first
folder, a PDF uploaded into a folder while it already sits on a sub-page
files the top, a drop of sub-pages on a folder is refused ("A page inside
another page is filed with it."), the page menu has no Move to folder for
one, and an open sub-page's label field files by labels only ("Folders
and labels" above). Labels stay per page: a label is a tag, not a place.

**Listing and counting.** `GET /blocks/root/children` lists every page,
sub-pages included, each with `parent`, its effective parent (`""` for a
library page), and a sub-page with an empty `preview` (no preview query
runs for it). App keeps `parent` as `_parentPage` on the listing's rows
and on its page blocks, and the home view's own surfaces draw from the
library pages alone (`libraryPages`): the root feed and a folder's listing
(`scopePages`), the folder counts and dates (`folderMeta`), "No label",
the count of Delete folder, the "Start your library" check and the
guide's triggers. A label view and a label's count are the exception: they
take in the sub-pages carrying the label (`labelScopePages`: at the root
every page, in a folder the pages whose top is filed there), and a
sub-page without a label is not in "No label". So does the filter box's
search: with a query, the view's sub-pages (the same `labelScopePages`)
that match by title, label or path join the matches, after the library
pages that match, each row with an `insideTagBadge` chip naming its path
(`CardLabels`' `inside`, shown whatever the File labels preference); with
the box empty they are not listed, and a label view, "No label" and the
folders-only and labels views add none. Every other surface keeps
every page: tabs, the recents and pinned strips (a sub-page may be pinned,
and the strips' page menu offers it Move to library), and the pickers,
which name a sub-page's path. Quick open, the page pickers (Move to
page, Move into page, the link dialog) and the chat's `@` and `+`
pickers rank through `rankLibraryPages`, which matches the path too, as a
weaker hit, so typing a parent's title lists the pages inside it after the
title hits; Quick open's, the page pickers' and the `@` picker's rows
show the path on their second line, the `+` picker after the title. The
`[[` picker's meta reads "kind · path" and the `@` menu's page row the
path, each else the page's first folder. The Ctrl+F search panel names a hit's page "Parent › Child",
the pages above it muted and shrinking first.

**Reaching one.** Three ways, none of them a page tree in a sidebar:

- the `[[id]]` chip `/page` wrote, or any chip or link to the page;
- **Pages inside** (`editor/SubPagesPanel.jsx`), under the notes above
  Linked from: "N pages inside", the page's sub-pages in library order,
  each row with its glyph (a PDF's for one that carries a PDF) opening
  that page, Back returning. Where `lib.organize`, a last row "New page
  inside" makes another. Its fold is the browser pref `subPagesCollapsed`
  ([settings.md](settings.md)); a page with no sub-pages shows nothing;
- **the crumbs** (`editor/PageCrumbs.jsx`) at the top of the page header:
  the ancestors, top first, each a button that opens its page (Back
  returns), separated by `/`, the page's own title below. A library page
  has none.

App reads `GET /pages/{id}/family` ([api.md](api.md) "Pages") for the open
page: alongside its tree when the listing has the page, as the comments
are (any other open, a link to a page the listing lacks, reads it once the
tree has landed, so a page that is gone asks for nothing more); again after its own sub-page writes (a page made,
either move, a Restore) and when another client's `set` on the open page's
root changes its `parent_page`. Until the answer lands the page reads as
having none. A page another device makes inside the open page, or moves
into it, shows in its panel at the next open.

**Making one.** `/page` makes a sub-page of the open page and links it
where it was typed ("Listing, sorting, filtering" below). **New page
inside** (the panel's last row, the home page menu for one page, a tab's
right-click menu) posts `POST /api/pages {title: "", parent_page}` and
opens the page like New page, its title ready to type; the parent's panel
lists it when you come back. The server refuses a `parent_page` that names
no live page (400). Duplicate keeps `parent_page`, so the copy is a page
beside the original inside the same parent; the pages inside it are not
copied.

**Moving one.** Both moves are a `set` of `parent_page` per page through
`PUT /blocks/{id}` (`placePages`), and both read the listing and the open
page's family again after.

- **Move into page…** (the home page menu, for one page or a selection,
  and a tab's menu) opens the move picker (`PagePicker`, below) as "Move
  into page…" over every page but the moved ones and the pages inside them,
  where they cannot go. A pick puts each page inside the one picked, and the
  server drops their folders. "Moved N pages into “Title”."
- **Move to library** (the same menus, for a sub-page) sends
  `{parent_page: null, folders: <its top's folders, or null>}`, so the page
  lands in the folders it was listed under until then. "Moved N pages to
  the library."

A block's **Move to page…** (its handle menu and the palette; an image,
table or diagram's "Another page…") goes to a sub-page as to any page
([collab.md](collab.md) "Ops": `/blocks/{id}/reorder` with the target as
`parent_id`). Its picker and Move into page's are one: `PagePicker`
(`library/PagePicker.jsx`), quick open's look, keys and rows over the
listing App holds, read again behind it as it opens. A sub-page's row names
the pages it is in; a block's or an object's picker leaves out the open
page and lists the open page's own sub-pages first, under "Inside this
page" (`rankLibraryPages`' `first`), before the recent pages.

**Shares.** A page share reaches the shared page and every page inside it
(`ShareScope.allows_page`: one whose ancestors hold the shared page), and a
folder share the sub-pages of the pages filed there, through their top. The
share view opens them in place from a chip, a crumb or a Pages inside row
(`goSharedPage`), each a history entry that Back and forward replay:
`?share=T&page=<id>`, or the plain `?share=T` for the shared page. On a page
host the address is the shared page's pretty one, `?page=<id>` added for a
page inside it, so the address reloads and can be sent. A deep link opens
the page `page=` names when the share reaches it, else the shared page. A
page the share does not reach leaves the open page and the address as they
are ("Could not open that page.") and adds no Back entry. The family read
through a share leaves out the ancestors the share does not reach, so the
shared page itself shows no crumbs. A publication carries the published
page's sub-pages, and its share there reaches them ([mirror.md](mirror.md)
"Publishing").

**Deleting** a page takes the pages inside it to Recently deleted, and its
restore brings them back ("Recently deleted" below).

**Elsewhere.** The agent's `list_pages` marks a sub-page with its parent
and filters by one, and `read_page` names a page's parent and the pages
inside it ([ai_tools.md](ai_tools.md)). The Markdown and Obsidian exports
and a folder link on disk write a sub-page's file in a directory named for
its parent, with `parent:` in its front matter, which the Markdown zip
import reads back ([import_export.md](import_export.md),
[folder_sync.md](folder_sync.md)). That import also makes a Notion
export's pages inside pages sub-pages, its database rows inside the
table's page, and the review shows each under the page it goes inside
([import_export.md](import_export.md#markdown-zips-obsidian-vaults-notion-exports-gamma-exports-zipped-notes)).
A page's export in any format takes the pages inside it when the Export
dialog's "Include pages inside" is on (`sub_pages`), nested as a folder
export nests them ([import_export.md](import_export.md#the-export-framework)). A page share's invitees hear about the
pages inside it ([comments.md](comments.md) "The inbox"). The Properties
popover shows `parent_page` under Filing as "Inside page" and `deleted_with`
under Recently deleted as "Went with", each naming its page, which opens or
steps in like any page a row names
([ui-design.md](ui-design.md#the-properties-popover)).

Tests: `backend/tests/test_sub_pages.py` (the rule, the op, the endpoints,
filing and shares), `test_sub_pages_lifecycle.py` (trash, restore, delete
for good, the mirror and publishing), `test_sub_pages_ai.py`,
`test_sub_pages_export.py`, `test_sub_pages_import.py` (a Notion export's
tree), `frontend/tests/subPages.test.mjs` (the client
helpers) and the browser suite's `sub-pages` group
(`frontend/tests/e2e/scenarios/subPages.mjs`).

## Listing, sorting, filtering

The root view is a recents feed of ALL library pages (a sub-page is
reached through its parent, "Sub-pages"), rendered incrementally (30 +
IntersectionObserver load-more). Folders and files render as ONE merged sorted
listing (list and grid): date sorts rank a folder by its most recent contained
page, a folder with no page in it or below it by when it was made (the
folder block's `created_at`), Title A–Z intermixes by name; a KindToggle picks what the listing shows —
folders + files, folders only, files only, or **labels**.

The sort choice (updated/created/viewed/title/custom, an iconed MenuSelect
pill; "viewed" ranks by the account-synced view history with modified time
as tie-break) and the kind filter are both per-VIEW — localStorage
`gamma-home-sort-map` / `gamma-home-kinds-map`, keyed by the folder's id
with `""` = root and `"#<label id>"` for a label view. A view without a sort
of its own inherits its nearest ancestor folder's (a label view inherits the
root's); the kind filter is not inherited, so a view without a choice of its
own shows folders + files.

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
pages carry. A label's count and its view take in the sub-pages that carry
it ("Sub-pages"). `labelMeta`, the label twin of `folderMeta`, gives each a count
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
pinned last regardless of sort, shown only while some library page in scope carries
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
(a sub-page, listed only while the box holds a query, on its title, its
labels or the pages it is in: "Listing and counting" under
[Sub-pages](#sub-pages))
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

**What moves.** The listing shows once it has loaded: before, it is blank,
so it never says it is empty before its rows arrive. A read that fails
ends the wait too: the New page and New folder items show, with "Could
not load the library." and a Try again button in place of the empty line
or the start panel. Within one listing an item added, removed or moved by
its sort glides the others to their places (`ListMotion`,
[ui-design.md](ui-design.md) "Lists that change"): a new row fades in and
a new card grows in where it will rest, and one that goes fades (a row)
or shrinks away (a card) where it stood while the rest close up. Another
view, kind filter, folder or label is another listing, which comes into
focus where it stands; a sort, the filter box's text and Load more change
the listing at once. A read that is a load moves nothing either
(`fetchHomeBlocks({ still: true })`): the listing read again on coming
home from a page, on the window's wake or after a metadata refresh (the
page's own and a lookup job's end, `tasks/useAppJobs.js`), an import
started elsewhere landing, and the server's view history landing after
the listing. Each marks what it
sets (`homeReadRef`), and a change is still when everything it changed
came from such a read. A read after the person's own write (a delete, a
new folder, a filing) moves.

**Selecting.** A click selects, Ctrl/⌘+click toggles one item and
Shift+click extends a range; a finger's tap opens instead, as in a phone's
file manager (`isTap`). **Select** in the toolbar is the way to pick
several without a Ctrl key: in Select mode (`selecting`) every click or tap
toggles, a picked row's glyph and a picked card's corner show a check
(`SelectCheck`), and the toolbar shows "n selected", a "⋯" that opens the
menu a right-click on the selection would, and Done. Clearing the
selection, Escape included, ends the mode.

**/page** in a block's editor makes a page inside the open one, as in
Notion (App's `createLinkedPage`; "Sub-pages" above). The block's text gets the `[[id]]` link at once, under
an id minted in the browser; the chip's label is cached with it, so the id
is never looked up before the page exists. Then `POST /api/pages {id,
parent_page}` makes it a sub-page of the open page, and it opens like a
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
empty line. Nothing of the user's means no library pages, or only seeded ones
(`properties.seeded`, the Welcome page and the Sketchbook every new account starts with):
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
on the page) or on the folder block (a `set` on the `folders` tree). Unpin
writes null, which drops the key (an empty `pinned`, which older clients
wrote on a page, reads as unpinned). A folder's pin is the workspace's,
like a page's: every member sees it.
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

- A page: Open (Enter) · Rename (F2) | New page inside (one page) | Pin ·
  Add label ▸ · Move to folder ▸ (not for a sub-page) · Move into page… ·
  Move to library (a sub-page) · Duplicate | Copy link · Share… · Export… ·
  Ask AI about this page · Properties | Delete (Del). The three rows for
  pages inside pages are in "Sub-pages" above. Properties, for one page, opens the read-only
  Properties popover at the menu's point
  ([ui-design.md](ui-design.md#the-properties-popover)). The shortcuts are each row's `keys`: the effective chords
  of `app.renameTitle` and `app.deletePages`, which act on the one / all
  selected pages of the listing.
- "Add label" lists every label in the library, checked when all the
  acted-on pages carry it (a click then removes it). It ends with "New
  label…", a name typed in place (the label of that name, else a new one:
  `makeLabel`).
- Share…, Export… and Ask AI act on the page itself, so they open it first
  and run once it is on screen (`openPageThen`, openBlock's `then`). An
  open that a later one overtakes (a tab pressed before the page lands)
  drops its action with it.
- A folder: Open | New page here · New subfolder | Move to ▸ · Move up ·
  Move down | Rename · Pin · Share… · Export… · Keep on this computer…
  (desktop app only, a folder filed once) · Properties (one folder) | Delete.
- A label: Open · Rename · Properties | Delete.
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
for "Delete pages too", whose count takes in the pages inside them, which
go with them and whose tabs close too; the folder goes only once every one
of its pages went (a Stop or a failure keeps it, and says so); a label's (`deleteLabel`) calls
`DELETE /api/labels/{id}`. A rename is one `set` on the tree and asks
nothing: no page changes.

## Recently deleted

Deleting a page moves it to a 30-day trash instead of removing it. The
page menu's Delete, the Del key, the page header's trash button and a
folder's "Delete N pages too" all call `DELETE /api/blocks/{id}` on the
page root, and the server moves the page there (`ops.trash_page`) with
the pages inside it ("Sub-pages" above). The confirmations say where it
goes and for how long, and for a page with pages inside it how many go
with it; one page is quoted by its title ("Move "Notes" and the 2 pages
inside it to Recently deleted?"), several are counted ("Move these 3
pages to Recently deleted?"). The library's Delete and the header's
button ask the same question, built by `deleteConfirm` in
`library/libraryUtils.js`. Their tabs close too, and none is a place a closed tab goes
back to. A selection holding a page and a page inside it sends the outer
one only, so the cascade takes the rest rather than one going alone
first. Many pages go as a batch (`library/pageBatch.js`, [tasks.md](tasks.md)
"The web app"): they leave the library at once, a row of Background tasks
counts them from ten on with Stop, and the status says how many went and
what failed. The header's button closes the page's tab first, as its × does (back to the latest page still
open or library view in the tab history, App's `closeTab`), and then
deletes. The `trashed` the page socket gets with the answer
([collab.md](collab.md)) is meant for other windows. If it reached the page
still open here, it would put up the "moved to Recently deleted" notice.

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

**The pages inside go with it.** `ops.trash_page` reads the page's
descendants first (`page_descendants`: once the page is gone they would
read as library pages) and moves them under `trash` in the same
transaction, after it, each stamped with the page's `deleted_at` and
`deleted_by` and with `deleted_with`, the page's id. They keep their
`parent_page`, so a restore puts the tree back as it was. Each one's room
hears `trashed`, and the change log turns the page `deleted` first, then
each of them in order, parents before the pages inside them
([collab.md](collab.md) "The change feed"). Recently deleted lists the
page alone: `trash.list_trash` passes by a row whose `deleted_with` names
a page still in the trash, and each entry's `sub_pages` counts the rows
that went with it. The 404 of a block of one of those rows carries the
entry of the page it went with (`blocks_store.trash_entry`), so a Restore
from a link brings the whole tree back. An entry's `folders` are where a
restore files it (`blocks_store.trashed_folders`): for a sub-page
deleted on its own while its parent is live, the folders of that
parent's top.

Restoring (`ops.restore_page`) puts the page back under `root`, last in the
library, filed where it was (a folder deleted meanwhile is passed by).
Its `parent_page` stays while it names a live page that is not inside it,
and goes otherwise: a sub-page deleted on its own comes back inside its
parent, or at the library root when the parent is gone or in Recently
deleted. The rows stamped `deleted_with` it come back after it, inside
the pages they were in. One of those rows restored by itself
(`POST /api/trash/{id}/restore` names it) is a page deleted alone, and
brings back the rows that went with the same page from inside it
(`ops.went_inside`). It touches each page `live` again, this one first,
so the change feed shows the pages as created again. A mirror
that brings the page back under its id (`blocks_store.create_page`)
replaces the trashed copy. So does a merge restore of a backup that has the
page (`ws_backup._merge`): the trashed copy's rows go, and the backup's
version comes back live under the same ids, its chats and op log kept
([workspaces.md](workspaces.md) "Export and backups").

`gamma/trash.py` deletes pages trashed more than `KEEP_DAYS` (30) days ago
for good through `ops.delete_page`, the one path that drops a page's chats
and index rows. It deletes the rows that went with the page too, each
the same way. Delete permanently, Empty and the sweeper walk the listing,
so they reach those rows only through their page. The sweeper runs at
startup and every hour (`lifespan`). It
skips a workspace whose `pages.db` and WAL are unchanged since the last
sweep, until the next expiry that sweep found (`trash._due`), so a host with
many workspaces does not open them all every hour.
Delete permanently and Empty take the same path at once. On a share host,
whose pages are published copies, `DELETE /api/blocks/{id}` deletes for good
directly, the page's live descendants with it. Endpoints:
[api.md](api.md) "Recently deleted". Editors and owners
trash and restore; a viewer and a share link cannot. A folder chat's agent
can list the pages that were filed under its folder and restore them on
request (`list_deleted`, `restore_page`, [ai_tools.md](ai_tools.md)); it
cannot trash or delete a page. A trashed page's files
stay referenced, so they count against the storage quota until the page is
deleted for good. Then the orphan check takes over
([user_db.md](user_db.md) "Stored files").

In the app, [RecentlyDeleted.jsx](../../frontend/src/library/RecentlyDeleted.jsx)
is a `SubDialog` opened from the trash button at the right end of the home
toolbar (at the library root, where `lib.organize`) or from the
`app.recentlyDeleted` command. It lists each page with its title, who
deleted it and when, the days it has left, its folder and, for a page
deleted with pages inside it, "with N pages inside", as `aiProvRow`
rows (a row's folder: the first of its entry's `folders` ids the folder
tree has, by its path). Each row has Restore and a trash button that deletes for good after
App's confirm box, which names the pages inside, and the dialog's footer
has Empty, whose confirmation and status count every page that goes,
the pages inside included. The pointer or the
focus reaching the trash button asks for `GET /api/trash` with the
dialog's module (`library/trashList.js`), so a click lands on the dialog
with its pages; the dialog takes an answer asked in the last 2 s as it
is, shows an older one while it asks again, and lets it go as it closes. A link to a trashed
page gets a 404 whose body carries `trashed` (the page's trash entry). The
missing-page notice then names the page and offers Restore, which restores
it and opens the page that holds what the link named. For a sub-page that
went with its parent the entry is the parent's, so the notice and the
chip below name the parent, and Restore brings the tree back and opens
the sub-page. The "moved to Recently deleted" notice of the open page
restores the page's trash entry too (the one the page's session was
told, else the one the 404 of `GET /blocks/{id}` carries), so an open
sub-page whose parent was deleted elsewhere comes back with the whole
tree, inside its parent. A `[[ref]]` or `![[embed]]` naming a block
of a trashed page is missing from the ref lookup (`/block-search?ids=`), so
App's `onFetchRefs` asks `GET /blocks/{id}` for each such id (a member only,
never a share view) and caches the 404's `trashed` entry, or `missing` when
there is none. The chip then shows the page's title struck through
(`.trashedRef`, the dashed look of an unlinked chip) and the card says the
page is in Recently deleted; a click opens the notice with Restore. An id
nothing holds renders as the unlinked chip, its card as "Embedded note not
found." (`refLabelOf`, `editor/refLabels.js`). The browser suite's `trash` group
(`frontend/tests/e2e/scenarios/trash.mjs`) covers the flow.
