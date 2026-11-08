# Sub-pages: a page inside a page

Asked 2026-11-03: how could Gamma have a page within a page, as Notion
does, where the child page is reached through its parent and is not listed
as a page of its own in the library? This note surveys what the stored
model and the code assume today, weighs three shapes a sub-page could
take, and recommends one. The recommended shape is built (November 2026,
the `child-pages` branch). How it works now is in
[dev/home_library.md](../dev/home_library.md) "Sub-pages", with the API in
[dev/api.md](../dev/api.md), the migration step in
[dev/migrations.md](../dev/migrations.md), the mirror's rounds in
[dev/mirror.md](../dev/mirror.md#pages-inside-pages), the exports, the
imports and the folder link in [dev/import_export.md](../dev/import_export.md)
and [dev/folder_sync.md](../dev/folder_sync.md), the agent's tools in
[dev/ai_tools.md](../dev/ai_tools.md), and the user's side in the
[user guide](../user_guide/user_guide.md#pages-inside-pages). "As built"
below lists where the build differs from this note. The mechanics before
it are in [dev/user_db.md](../dev/user_db.md) "pages.db" and
[dev/block_centric.md](../dev/block_centric.md).

## What the code assumes today

A page is a row of `unified_blocks` whose `parent_id` is `'root'`
(`'trash'` while it is in Recently deleted). That one test is the whole
definition, and it is held in many places rather than one: the generated
`kind` column (`db.BLOCK_HOT_COLUMNS`) and its Python twin
`blocks_store.block_kind`; `page_root_id` (the page a block lives in: its
stored `page_id`, if that row is under root), `IN_LIBRARY`, `is_op_page`,
`batch_page`, `root_pages`, `pages_in_folder`, `page_in_folder`,
`pages_in_scope` and the library listing (`GET /blocks/root/children`);
`fetch_subtree`'s fast path (a page is read by `page_id`, anything else
walks the parents); `create_page` and `insert_page_tree`, which insert
under root; `ops.trash_page` / `restore_page`, which move a row between
root and trash; `auth.ShareScope.allows_page`; the Zotero index
`idx_ub_zotero_key`, which only a query naming `parent_id = 'root'` uses;
and the test suite's `page_id_drift` check, which walks the parents and
expects a page right under a reserved row. The string `'root'` occurs on
148 lines of 50 backend modules. Everything keyed on a page (the op log,
the change log, the room, the notes index, shares, chats, tabs, recents,
reading positions, covers, the mirror, backups, folder links, the Zotero
link) is keyed on that root row's id.

The `/page` slash command is the nearest thing to a sub-page now
(`editor/slashCommands.js`, `App.createLinkedPage`): it mints an id, puts
a `[[id]]` chip where it was typed, and makes a root page under that id
filed in the open page's folders. So a "page made from a page" exists and
is linked from where it was made, but it is a library page like any other.
[block_centric.md](../dev/block_centric.md) "Sub-pages" said a sub-page
should be a zoom-in on a subtree instead, and that the promoted page is a
root page on purpose, because every subsystem assumes pages are root
blocks. That reasoning is right about the cost of nesting the rows; it
does not follow that a sub-page has to be a subtree.

## Three shapes

**A. Zoom-in on a subtree** (the plan in block_centric.md). A sub-page is
a block with children in the parent's tree; opening it shows that subtree
alone with the block's text as the title and a breadcrumb back. Nothing
stored changes, and the library never sees it because it is not a root
row. But it is not a page: it cannot carry a PDF (a page carries one
document), has no chat, share, tabs entry, trash entry, op log or room of
its own, every search hit and export names the parent, and the parent's
tree carries all of it: `fetch_subtree` reads the whole page, and the
page's room sends every op to everyone on it, so a project page with
thirty sub-pages of notes is one tree of every note. This is Logseq's
outline zoom, a fine feature for outlines; it is not what "page within
page" means in Notion, where a sub-page is a page in every respect except
where it is listed.

**B. A page row under a page.** The child is a page row (its own
`page_id`, its own blocks) whose `parent_id` is the parent page or a block
of it. The library excludes it for free (`parent_id = 'root'`), and it is
a page to everything else. The price is the definition above: "a page" has
to become "a row whose `page_id` is its own id, reachable from root
without passing trash", so liveness is a walk up the chain instead of one
indexed read, every reader listed above changes, `fetch_subtree` must not
read a child page's rows as the parent's, an op `delete` on the block the
child hangs under must stop at the page row (today it would take the whole
child with it, past the trash), trashing a parent must cascade or every
reader walks, and `page_id_drift` needs a new rule. It is the Notion
model done literally, at a cost the next shape avoids.

**C. A root page that names its parent** (recommended). A sub-page is a
root page like any other with one more property, `parent_page: <page id>`.
It is filed in a page the way a page today is filed in folders by
`folders: [ids]`, and filing is a property, not a place in the tree. Every
subsystem keyed on a page keeps working untouched: the op path, the room,
the op log and change log, the notes index, trash and restore, shares,
chats, tabs, recents, reading positions, covers, the mirror, backups,
folder links and the Zotero link. What changes is where it is listed and
how it is reached. The rest of this note is shape C.

## The design

**Stored.** `properties.parent_page`, a page id, on the sub-page; nothing
on the parent. A generated column `parent_page`
(`json_extract(properties, '$.parent_page')`) with a partial index, as
`doc_id` has, so "the sub-pages of P" and "is this a sub-page" are indexed
reads and the SQL rules below need no Python pass; that is one numbered
migration step ([dev/migrations.md](../dev/migrations.md)) with no row
rewrite, since there is no old shape. The op path's rule for the key
(beside `ops._filing_patch`): a set must name a page under root that is
not the page itself and does not have the page among its own ancestors
(no cycles), else 400; null or an empty string removes the key. A parent
that is gone (deleted for good on another copy of the workspace) reads as
no parent: the page shows at the library root, as a page naming a deleted
folder shows there. A page's ancestors are the chain of `parent_page`
reads, and a sub-page's *top* is the first ancestor without one.

**Depth and cost.** The pointer nests to any depth: a sub-page's parent
may be a sub-page, the cycle rule keeps the chain finite, and nothing
below assumes one level. What was one indexed read stays one: whether a
page is live, which page a block is in, an op batch's page, a room, all
unchanged, because a sub-page is still a root row and a trashed parent
takes its tree under the trash with it (below). A page's parent is a key
on a row already loaded; its sub-pages are one seek on the partial index;
the chain, the top ancestor and the cycle check are one seek per level,
and the depth is a handful; a cascade writes one row per page of the
sub-tree, once, in one transaction. The stored cost is one small key per
sub-page, a virtual column, and an index over the sub-page rows only.

**Filing.** A sub-page carries no `folders` of its own: its folders are
its top's. The one "filed in F or below" rule
(`_FILED_IN_SUB`, used by `pages_in_folder`, `page_in_folder`,
`root_pages`, `pages_in_scope`) gains the sub-pages of the pages it finds:
the filed pages as today, then their descendants through the index, one
recursive CTE downward (`reach(id)`: the filed ids, union the rows whose
`parent_page` is in `reach`). That costs the filed pages plus their
descendants and never walks up from every page of the library; it is the
adjacency-plus-recursive-CTE shape the folder tree already uses
(`_SUBTREE`). A materialized path would spare the recursion, but a move
would then rewrite every descendant's path, the rewrite folder renames
were freed of ([data-model.md](data-model.md)). So a folder view's
counts, a folder share, the search scope, a folder export, a folder
chat's agent and a folder link on disk all reach sub-pages without code
of their own. Labels stay per page (a tag, not a place), so a label view
lists sub-pages that carry it.

**Creation.** `POST /api/pages` takes `parent_page` beside `folders`
(`routers/pages.PageCreate`), and `create_page` stores it through
`page_root_props`, validated as the op path validates a set. The `/page`
command sends `parent_page: <the open page>` instead of the open page's
folders, so what it makes is a sub-page, linked by the `[[id]]` chip where
it was typed, opened with the title ready to type as today; its hint
becomes "A new page inside this one". A library page is made as today
from the home view's New page; the page header's menu can offer "New
sub-page" too.

**Listing.** `GET /blocks/root/children` keeps shipping every root page,
sub-pages included, each with its `parent_page`; the home view is where
they drop out: `App.pageBlocks` / `scopePages` leave out a page whose
parent is live, so cards, folder counts (`folderMeta`), label counts and
the empty state do not count them. Pickers keep them: Quick open, the
`[[` picker, the @ menu's page picker and the chat's page picker list a
sub-page under its parent's name ("Parent › Child" as the row's second
line, `librarySearch.rankLibraryPages`), and a sub-page the viewer opened
stays in the recents strip and may be pinned. The agent's `list_pages`
line gets `in="Parent title"`. The listing's per-page text preview
(`_page_preview`, one index seek per page) is the one cost that grows
with the number of sub-pages while the home view draws none of them: a
sub-page needs no preview, so the listing skips it, and should a
Notion-sized import ever bring thousands, they can go out as id, title
and parent only, or be listed per parent on demand (`GET
/pages/{id}/subpages` for the Pages inside panel). A knob, not a limit of
the shape.

**Reaching it.** Three ways, none of which is a page tree in a sidebar
(the flat library with folders and labels stays the navigation model):

- the `[[id]]` chip the `/page` command wrote (or any chip or link to it);
- a **Pages inside** panel under the parent's notes, beside the Linked
  from panel (`editor/BacklinksPanel.jsx` is the pattern): the sub-pages
  in library order, each a row that opens it, so a sub-page whose chip was
  deleted is still reachable;
- the page header's breadcrumb: the top's folder path as today, then
  `Parent / Child`, each crumb opening that page, titles from the listing
  the client already holds (no new endpoint).

Deleting the chip leaves the page; Notion deletes the page with its
block, but a chip here is also typed by hand, copied and pasted, and an
undo of a delete would have to restore a trashed page. The page's own
header trashes it.

**Moving.** Two `set`s of `parent_page` through the op path, from the
page header's menu: "Move to library…" removes the key and files the page
in its former top's folders; "Move into page…" picks a page (the same
picker as the `[[` chip's) and sets it. The Pages inside panel can take a
drop of a page card later; not needed first.

**Trash.** Trashing a page trashes its sub-pages with it: `ops.trash_page`
walks the sub-page tree and moves every one under `trash` in the same
transaction, each stamped `deleted_with: <the page trashed>` beside
`deleted_at` / `deleted_by`, and each touched `deleted` in the change log
so a copy of the workspace drops it too. Recently deleted lists only the
page trashed (`trash.list_trash` passes by a `deleted_with` row whose top
is in the trash), Restore brings the tree back (`restore_page` restores
the rows stamped with it, their `parent_page` kept), the sweeper and
Delete permanently purge them together, and `delete_page` of a parent
purges its sub-pages. A sub-page trashed alone is stamped as today, and
its restore puts it back under its parent when the parent is live, else
at the library root with the key removed. The other rule, leaving the
sub-pages where they are and hiding every page whose chain passes a
trashed one, was considered and rejected: it makes liveness a walk in
every reader, which shape C exists to avoid.

**Reach.** A page share reaches the page's sub-pages too
(`ShareScope.allows_page`: the page, or one whose ancestor chain reaches
it), as Notion's share of a page does; a folder share reaches them through
the filing rule. A publication (`mirrors.page_filter`) of a page lists its
sub-pages too. `assert_block_in_scope` and `block_ids` follow from
`allows_page` and need no change.

**A sub-page carries what a page carries.** A PDF through the header's
paperclip (the one-page-per-PDF rule holds), files, a record, handwriting,
a chat. Promotion of a file chip ("Add to library") keeps making a root
page filed with the asking page, for the reason block_centric.md gives:
a paper appears under several projects and needs one identity. A sub-page
is for the notes that belong to one page. The Zotero link treats a
sub-page as any page: one with a record or a PDF is an item.

**Search.** Nothing changes in the indexes; a hit names its page. The
Ctrl+F panel's groups and the chat's citations show "Parent › Child" for a
sub-page's hit, from the same listing the pickers use.

**Exports and imports.** The Markdown and Obsidian exports write a
sub-page's file in a directory named for its parent (`Parent/Child.md`,
Notion's and Obsidian's layout; `obsidian_export.page_dir`) with
`parent:` in its front matter; a folder link on disk follows the export's
layout. The zip import reads `parent:` back, and Notion's "Markdown &
CSV" export, whose subpage directories become folders today
([dev/import_export.md](../dev/import_export.md)), can make sub-pages
instead, so a Notion page tree comes in as a page tree. That is the
natural follow-up and the main thing a Notion user would notice (built:
"As built" below).

**Older tabs and copies.** A tab or a mirror from before the step does not
know the key: it lists sub-pages at the library root and leaves the key
alone, which is harmless. No reader tolerance is needed.

## Where the change lands

Backend: `db.py` (the column and index, a migration step);
`blocks_store.py` (`parent_page`, `sub_pages`, `page_chain`, the filing
rule, `page_root_props`); `ops.py` (the set rule, the trash cascade);
`routers/pages.py` (`PageCreate.parent_page`); `auth.py`
(`allows_page`); `ai_tools.py` (the listing line); `trash.py` (the
listing); `markdown_export.py`, `obsidian_export.py`, `folder_sync.py`,
`markdown_zip_import.py` (the layout). Frontend: `slashCommands.js` and
`App.createLinkedPage` (the command), `App.pageBlocks` / `scopePages` /
`folderMeta` (the listing), the page header (the breadcrumb and the menu's
two moves), a `SubPagesPanel` beside `BacklinksPanel.jsx`, the pickers'
second line, the search panel's group label. Docs: a "Sub-pages" section
in home_library.md, the "Sub-pages" paragraph of block_centric.md,
api.md, import_export.md, the user guide's `/page` line.

Tests: the page module's unit tests (creation, the cycle rule, cascade
trash, restore and purge, the filing rule, the share rule),
`test_block_columns.py` for the column, and the browser suites `notes`
(the command nests, the breadcrumb, Pages inside), `trash` (the cascade),
`folders` (hidden and not counted), `quickOpen` (listed under its parent)
and `share` (a page share reaches a sub-page).

## As built

Shape C was built as designed above, with these differences.

- **One rule for "the parent".** Every reader takes a page's *effective*
  parent (`blocks_store.page_parents`, `page_parent`, `page_ancestors`,
  `page_top`): the stored `parent_page` when it names a live page and the
  chain up from there reaches a library page without repeating a page
  within `MAX_PAGE_DEPTH` (64) pages, counting the top. Anything else (a
  dangling id, a parent in Recently deleted, a cycle, a chain too deep)
  reads as a library page. So the nesting has a depth, 64 pages, and the
  op path refuses a target that already has 63 ancestors.
- **The cycle check walks rows in Recently deleted too.** A target that is
  a page, live or deleted, is refused (`conflict: cycle`) when the stored
  chain up from it passes through the page, since a restore would close
  the cycle. A target that is no page is stored as written, as a folder id
  is. A restore drops a `parent_page` that would close a cycle.
- **The "sync" client skips that check.** The mirror's own batches (client
  `sync`, `ops.SYNC_CLIENT`) store what two copies merged, so a cycle made
  by two copies each moving one page into the other is stored and read as
  two library pages, rather than refused on every round.
- **The migration step leaves the data format alone.** Step 47 adds the
  generated column and its index; `migrations.DERIVED_STEPS` keeps
  `workspace_format()` at 44, so a copy on either side of the step keeps
  syncing with its remote, as "Older tabs and copies" expected.
- **`POST /api/pages`'s `parent_page` must name a live page** (400
  otherwise) and drops the page's `folders`. A `parent_page` inside
  `properties`, the publishing mirror's path, is stored as the writer
  stores it, live or not.
- **The family endpoint.** The crumbs and the Pages inside panel do not
  read the listing: `GET /api/pages/{id}/family` answers the effective
  ancestors (top first; under a share only those it reaches) and the
  pages right inside (`{id, title, updated_at}`, with `pdf: true` on one
  that carries a PDF, so a share view shows the right glyph without a
  listing). It replaces the `/pages/{id}/subpages` the note kept in
  reserve. The app reads it on every page open (beside the tree for a page
  the library lists), after its own sub-page writes and, since 2026-11-06,
  when the window comes back, with the listing; a sub-page made or moved
  in by another client shows then, or at the next open. The listing still
  carries every page, each with `parent` (the effective one, not the
  stored key). It first skipped a sub-page's preview; since 2026-11-06 it
  reads one for every page, as a sub-page's card shows in the pinned and
  recents strips and a label view.
- **The crumbs name pages only.** No folder path comes before them: a page
  inside another shows no folder chips at all, and its label field offers
  labels only, since its folders are its top's.
- **The tab menu, not a page-header menu.** Gamma has no page-header menu,
  so "New page inside", "Move into page…" and, for a sub-page, "Move to
  library" are in the tab's right-click menu and the library's page menu
  ("Move into page…" for a selection too), and "New page inside" is the
  panel's last row. There is no palette command. "Move to folder" is
  hidden for a sub-page, and dropping one on a folder tile is refused.
  Since 2026-11-06 a Pages inside row has the library's page menu as well
  (without Rename), and drags: a page dropped on a page (a library row or
  card, a Pages inside row, a crumb) goes inside it, never into itself or
  a page inside it, and one dropped on the topbar's Home goes to the
  library, as Move to library sends it.
- **Label counts include sub-pages.** The "Listing" paragraph above left
  them out of the label counts while "Filing" had a label view list them;
  the build follows "Filing": a label's tile counts, and its view lists,
  the pages carrying it, sub-pages too (in a folder, those whose top is
  filed there). "No label" leaves sub-pages out.
- **Writers that file follow the top.** A clip into a folder files a
  sub-page's top (`blocks_store.filed_folders`), and so does an upload into
  a folder of a PDF already on a sub-page; the chat's Save to library uses
  the open page's top's folder; the agent's `save_paper` and `move_page`
  refuse a sub-page; the search panel's folder chip keeps hits inside
  sub-pages. The Zotero link, the imports and a backup's merge store none
  on a sub-page either (2026-11-07); a raw write or a merged copy still
  may, and every reader passes them by.
- **The agent.** A sub-page's `list_pages` line shows `parent=<page id>`
  in place of its folders, not `in="Parent title"`; a `parent` filter
  lists the pages right inside a page; `read_page`'s header names the
  pages around and inside the page (at most 50, then a `list_pages` call
  for the rest, in a page chat the search); `list_folders` counts library
  pages only. A page chat reads and searches the pages inside its page.
  The agent has no page-making tool, so none takes a parent.
- **Trash.** As designed, plus: a page that went with another and is
  restored alone brings back the pages that went with that one from
  inside it (`ops.went_inside`), so nothing inside it is left behind; a
  sub-page deleted on its own is listed, and reached by a folder chat,
  with the folders of its live parent's top, where a restore puts it.
  Restore on a missing-page notice opens the page the link named, inside
  the restored tree.
- **Shares reach further than the page.** The Inbox tells invitees of a
  parent's share about the pages inside it (nearest share first), and a
  parent change revalidates the share peers. A share view opens a page
  inside the shared one in place, a history entry each
  (`?share=T&page=<id>`; on a page host the published page's address with
  `?page=<id>`), and a page the share does not reach leaves the page on
  screen as it was.
- **The mirror's handling.** A round works pages parents first
  (`_round_order`), so a deleted tree goes as a tree. An edit beats a
  delete for sub-pages too, both ways: a page inside a page deleted on
  the other side is kept when it was edited here, and comes back when it
  was edited there; a page moved into the deleted page on the other side
  is spared; a page that went with its parent here while the remote keeps
  it is restored and merged rather than laid in anew. A publication moves
  its pages, the pages inside them and every page that travelled before;
  a page that leaves it is deleted on the share host after a share
  editor's text is pulled; unpublishing a page keeps a page inside it that
  is published on its own. [dev/mirror.md](../dev/mirror.md#pages-inside-pages)
  has the rules.
- **The share host's cap counts every published page.** A publication's
  sub-pages count against the plan like the page that carries them:
  publishing a page needs room for it and the pages inside it
  (`publish._check_cap`). A review round had made sub-pages free, which
  let one published page carry any number of pages past the plan; that
  was taken back, as a billing rule is not this feature's to change, and
  the developer confirmed it (2026-11-03): sub-pages count.
- **A link to a sub-page deleted with its parent names that parent.** The
  404's `trashed` entry is the page it went with, so a `[[ref]]` chip
  shows the parent's title struck through and the missing-page notice
  says the parent is in Recently deleted; its Restore brings the tree back
  and opens the linked page. Confirmed as wanted (2026-11-03).
- **The exports nest more than the vault.** The readable Markdown export
  nests sub-pages too (`Top-x/Child-y.md`), its links and asset paths
  made relative to each file; the annotated-PDF and Notability folder
  zips nest them; a Zotero export gives a sub-page its top's collections.
  `parent:` is written only when the parent is in the same export. A page's
  export takes the pages inside it only when asked (below). The folder link's manifest
  lists each page with sub-pages among its `dirs`, and library pages are
  named before sub-pages, so a sub-page never takes a library page's file
  name.
- **The zip import** resolves `parent:` to the note beside the note's
  directory first, then a wikilink's vault path, then as a link, drops
  values that make a cycle or nest too deep, and makes no folder for the
  parent's directory.
- **Notion's pages inside pages come in as sub-pages.** The developer
  asked for it (2026-11-03: "notion should be subpages"), as the only
  mapping, with no choice in the Import dialog. Notion writes no
  `parent:`, so the import pairs a directory with the page beside it: a
  directory whose name carries a Notion id, beside a note or a database
  CSV named exactly as it is, holds that page's sub-pages
  (`NotionDialect.paired_page`, in `gamma/notion_import.py`) and makes no
  folder; a database's rows become sub-pages of its table page. A
  front-matter `parent:` still wins, a directory that pairs with nothing
  stays a folder, and vaults
  and plain zips, which carry no Notion ids, are unchanged. A parent left
  out of the selection leaves the note filed by its directory; one
  imported before is named as the parent, and existing pages are not
  changed. The cycle and depth cut is the `parent:` one, which now counts
  the library's pages above a parent imported before. Parents are written
  before the pages inside them, so a stopped import leaves no page inside
  one it never wrote. The report gives each page its `parent`, and the
  review's library column draws a sub-page under its parent, whose
  checkbox covers it.
- **"Include pages inside" on a page's export.** Asked the same day
  ("export should have also export subpage switch, as notion"): the Export
  dialog's review step has the switch for a page with pages inside it, in
  every format (Gamma and Notability get a review step for it), off by
  default as Notion's "Include subpages" is and remembered with the other
  switches. On, the export (`sub_pages`, on the job, the page's export
  GETs and the bibliography review) takes every page inside, laid out as
  a folder export lays out its pages, and a format that writes one file
  for one page writes a zip for several. In the annotated-PDF and
  Notability zips the page's file is at the top rather than under its
  folders; the vault keeps the page under its folders, as it is alone.
  A share that reaches the page reaches every page inside it, so the
  pages inside need no check of their own. In a page share's view, which
  has no listing, the hint names no number.
- **Duplicate keeps `parent_page`**: the copy is a sibling inside the same
  page; the pages inside the original are not copied.
- **The Properties popover** names a sub-page's `parent_page` ("Inside
  page", under Filing) and a cascaded row's `deleted_with` ("Went with"),
  each opening that page ([block-properties.md](block-properties.md)).

## What this leaves open

- Whether the zoom-in of shape A is still wanted for outlines once
  sub-pages exist; it is orthogonal and can come later.
- Whether a sub-page's `[[id]]` chip should render as a page card (title,
  icon, a line of preview) rather than an inline chip, as Notion's
  sub-page block does.
- The Pages inside panel follows a sub-page another client makes, moves
  in or takes out when the window comes back, with the library listing (at
  most every 15 s), not while the window keeps the focus: the change
  reaches the moved page's room only, never the parent's. A content-free
  nudge to the rooms of the page left and the page entered, as the
  comments' `{t: "comments"}` is, would make it live.
- A share view now adds an in-app Back entry for each page it opens
  through the panel, the crumbs or a PDF link region, where before only a
  chip click did; whether that is wanted everywhere.

### Gaps found on 2026-11-05

A read-only survey of the dev branch, every item checked against the code,
asked where a sub-page still does less than a library page. It confirmed
the open items above still hold (drag and drop, MCP's `export_page`, the
panel not following other clients, the 402 at the cap, a sub-page
unpublished alone sent again, a kept page landing unfiled, the `[[`
picker's path) and found these as well. A kept page landing unfiled is
fixed since, on 2026-11-07: a page a mirror keeps out of a deleted page
is filed in the folders of that page's top, stays published on a share
host, and a page moved there into a page deleted here comes out of it on
both sides (`ops.kept_out_of`, `sync_engine._settle_parents`, mirror.md
"Pages inside pages"); and Empty and the sweeper read what went with a
page from one read of the trash, the column indexed (migration step 48,
home_library.md "Recently deleted"). The 402 at the cap (now a quiet
wait, `CapDeferred`) and the sub-page unpublished alone (now left out of
its parent's publication) are fixed since, on 2026-11-07, with the
sharing and publishing gaps below. The library's filter box not
finding sub-pages was among them and is fixed (home_library.md
"Listing and counting"), and so are, on 2026-11-06, the moves of a
selection holding a page and a page inside it (they keep to the topmost
pages, as Delete does), Move to folder hidden by one sub-page in a
selection, the move dialogs matching titles only and Move block to page
showing no path (both are Ctrl+P's palette now, `PagePicker`), and
Recently deleted not saying a sub-page deleted on
its own comes back inside its parent (home_library.md "Moving one" and
"Recently deleted"). Also fixed that day: the `[[` picker and the
editor's `@` menu now match a sub-page's path and every page's folders
and labels by name rather than id (`rankRefPages`); a library row or
card counts the pages inside a page in a chip of its own; and a
sub-page's card (the strips, a label view) shows its path in place of
folder chips and a preview of its notes, which the listing now reads for
it, while Quick open matches it on its top's folders rather than its own
(home_library.md "Listing and counting", "In the pickers" and "The
card"). So, too, are the Pages inside rows without a menu or a drag and
no page taking a dropped page: a row has the page menu and drags, a page
row, card, panel row or crumb takes a page inside, and Home takes one to
the library (home_library.md "Reaching one" and "Moving one").

Sharing and publishing: every gap found here is fixed (2026-11-07). The
Share popover counts the pages inside a shared page and names the link
that opens a page from around it (`via`); Move into page… asks before it
moves pages into a shared or published page (`GET /pages/{id}/reach`);
Publish says the pages inside go along and count, the cap's refusal says
how many it counted (`going`), a sub-page carried by its parent's
publication reads as published with it (its popover, the sync pill,
Settings' count of `published_pages`), and Leave out keeps it off the
share host (`sync_engine.leave_out`); Add to my library takes the sub-page
the address names, with the pages inside it; and an edit share's link
makes pages inside the shared page (home_library.md "Shares", mirror.md
"Publishing").

Integrations and the agent: every gap found here is fixed (2026-11-07).
The Zotero link files a sub-page's item where its top is
(`blocks_store.filed_folders`), refiles it with its top, and answers
Zotero's change of its collections with its top's rather than storing
folders on it (zotero_sync.md "Collections and folders"). The Zotero
import merging into a sub-page files it nowhere, and it and the Notability
and GoodNotes re-imports report its top's folders; the Gamma export's
review shows a sub-page under its parent, and one imported without its
parent is filed in its top's folders (import_export.md "Gamma-to-Gamma
export"). A page chat reads and searches the pages inside its page, and
edits only that page; `read_page`'s properties line names a sub-page's
path and its top's folders, and its header points a page chat at the
search rather than `list_pages`; the trash tools say which pages come
back with a page and which go back inside one (ai_tools.md). The
connector's folder picker counts a sub-page for its top's folders
(extension.md), and the MCP `export_page` takes `sub_pages`, its PDF zip
named by `/pages/{id}/export-pdf?sub_pages=1` (mcp.md "Export a page"):
the item the survey found still open, which the open list no longer holds.

Deliberate, worth a second look: "Start your library" and the first-run
tour count library pages only, so a new user whose pages all sit inside
the seeded ones still sees them; and a duplicated page has no pages
inside, while its `/page` chips still open the original's.
