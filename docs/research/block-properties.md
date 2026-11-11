# Block properties: what makes a block special, and where that shows

Survey from 2026-11-02, from reading the block table (`gamma/db.py`,
`gamma/blocks_store.py`, `gamma/ops.py`), the client's model
(`shared/model/blockModel.js`, `shared/model/blockOps.js`, the per-kind
modules) and the surfaces that show a block. Three questions: which
property keys make a block "special", whether that data structure is
defined and clean, and whether a right-click on a block lets a person see
or change its properties. The mechanics live in the dev docs linked below;
this note keeps the inventory and the verdict. A cleanup followed the same
week; [its section](#cleanup-november-2026) says what it removed and what
stays on purpose, and the catalog below is the state after it.

## The short version

- **One table, one JSON column, one derived discriminator.** Every block
  is a row of `unified_blocks` with a `properties` JSON object. What a
  block *is* (`kind`) is never stored: a generated SQL column derives it
  from which keys the object holds, in a fixed precedence, and
  `blocks_store.block_kind` repeats the rule in Python for the block dict
  ([dev/user_db.md](../dev/user_db.md) "pages.db"). The client's model never
  reads `kind`; it asks the same questions of the properties through one
  predicate per kind (`isInkGroup`, `isTextBox`, `isPicture`, `isSheet`,
  `isRecordingBlock`, `isLinkBlock`, `isHighlightBlock`). Only the
  read-only Properties popover shows the server's `kind` (from
  `GET /api/blocks/{id}`), and only while the server's properties match
  the tab's.
- **The rule is defined once per side and pinned by tests**, and each
  special value (a text box, a picture, a sheet's paper, a recording, a
  highlight's position) has a normalizer on both sides with shared JSON
  fixtures. The per-kind shapes are in good order.
- **The catalog as a whole was not written down anywhere** when the survey
  was made, and the API has no schema for it: `properties` is an untyped
  dict, the op path stores any key a client sends, and the keys are
  documented across eight files. That is the Notion and Logseq shape,
  chosen on purpose (unrelated keys never conflict, the mirror carries
  `properties` alone), but it is why the question "which keys exist"
  needed a survey. The client keeps one copy, `CATALOG` in
  `editor/blockProps.js`, which mirrors [the catalog](#the-catalog) for the
  Properties popover; the dev docs have no catalog table.
- **No surface showed a block's properties** when the survey was made;
  the handle menu's Properties popover shows them read-only
  ([below](#where-a-property-can-be-seen-or-changed)). The block menu
  opens from a click on the ⋮⋮ handle, not from a right-click; a
  right-click on a note's text is the browser's own menu. Its other rows
  are actions (copy link, copy as Markdown, comment, add to chat,
  transcribe, add a page, duplicate, move, delete). A special
  block's properties are edited through its own surface: the highlight's
  menu on the PDF, the picture's and the text box's menus, the ⓘ
  metadata popover, the paperclip, the home context menu. Everything else
  can be changed only through the API or the database.

## The catalog

The keys as the code reads them in November 2026, by family. "Page" means
the root row of a page (`parent_id = 'root'` or `'trash'`); "block" means
a row inside a page. Folder and label rows carry `pinned` and nothing else
of note ([dev/home_library.md](../dev/home_library.md) "Folders and labels").

**The discriminator.** The generated `kind` column tests these keys in
this order and stops at the first hit; a block with none is a `note`.

| Key | Makes | Test | Shape and doc |
|---|---|---|---|
| `ink_url` | `ink` | the key is present, whatever its value | the stroke file's upload URL; `ink_strokes` beside it is a cached count ([dev/handwriting.md](../dev/handwriting.md) "Model") |
| `text_box` | `text_box` | an object | `{x, y, w, h, auto, size, color, bg, zotero}` ([dev/text_boxes.md](../dev/text_boxes.md) "Model") |
| `picture` | `picture` | an object | `{url, x, y, w, h, angle, crop, flip_h, flip_v, locked, opacity, z}` ([dev/pictures.md](../dev/pictures.md) "Model") |
| `sheet` | `sheet` | an object | the paper `{width, height, color, pattern, spacing, line}` ([dev/notebooks.md](../dev/notebooks.md) "Model") |
| `recording` | `recording` | an object | `{started, segments[], state, dur, by, at}` ([dev/recording.md](../dev/recording.md) "Model") |
| `link_url`, `link_page_id` | `link` | either one non-empty | a link region on the PDF; `link_block_id` names a highlight on the target page ([dev/pdf_citations.md](../dev/pdf_citations.md)) |
| `pdf_position` | `highlight` | an object | `{pageNumber, width, height, boundingRect, rects, area?}` plus the relocation keys `doc_id`, `lost`, `approx`, `match`, `prev`, `closest` ([dev/api.md](../dev/api.md) "The highlight shape") |

The precedence matters because the kinds overlap on purpose: an ink
group and a link region carry a `pdf_position` too (so `ink` and `link`
are tested before `highlight`), a text box may carry a `picture` beside
it (the box wins), and a Zotero text box is a `text_box` with `zotero:
true`. `tests/test_block_columns.py` pins the SQL against the Python rule
with one block per overlap, and [dev/migrations.md](../dev/migrations.md)
records each time the column learned a kind (step 38 recordings, step 44
pictures), since a generated column has to be rebuilt to change.

**Placement and companions of a placed block.**

| Key | On | Meaning |
|---|---|---|
| `pdf_page` | a text box or a picture on a PDF page | the 1-based page; these two store no position. Absent on a sheet, where the tree says which sheet |
| `quote`, `color` | a highlight | its text; its colour as `#rrggbbaa`, never a palette slot ([dev/settings.md](../dev/settings.md) "Colors") |
| `linked_highlight_id` | a block placed by its page alone | the highlight it was attached to in the notes (attach mode) |
| `imported_annot`, `annot_stripped` | a highlight, text box or picture that came from the PDF's own annotations | where it came from; that the PDF it came from was replaced ([dev/import_export.md](../dev/import_export.md)) |
| `t0` | a block the web client makes, except an empty page's seed block and an ink group (its strokes carry their own) | wall-clock ms it was made, the replay's clock; `transcript: true` with `t0` on a recording's transcript paragraphs ([dev/recording.md](../dev/recording.md)) |
| `collapsed` | any block | the fold a page opens with: a document property, while a reader's own folding is the `view` beside the tree (`blockModel.js`) |
| `zotero_readonly`, `zotero_author` | a block a Zotero link will not write back | `lossy` or `locked`, and whose it is ([dev/zotero_sync.md](../dev/zotero_sync.md)) |

**The page row.** A page is a block whose properties are the record of the
page: what it carries, where it is filed, what it is about.

| Family | Keys | Doc |
|---|---|---|
| the document | `doc` (`{format, name, hash, ext, at, url?, size?, copy?}`: the one file the page shows, read through `page_document` / `pageDocument` and nowhere else; the Properties popover shows `doc.name`, `doc.hash` and `doc.url` as its Document rows) | [dev/documents.md](../dev/documents.md) |
| filing | `folders`, `labels` (lists of block ids), `pinned` on a page, a folder or a label row (an unpin deletes the key), `parent_page` (the page a sub-page sits inside; it carries no `folders` of its own and is filed with its top page) | [dev/home_library.md](../dev/home_library.md), [research/sub-pages.md](sub-pages.md) |
| the record | `meta` (the bibliographic record, `meta.kind` its item type), `meta_error`, `auto_title`, `bibtex` (the entry of record, not a cache: see "Is it clean?"), `cite_key`, `ppt_cite` | [dev/paper_metadata.md](../dev/paper_metadata.md) |
| where it came from | `web_url`, `web_meta` (a clip), `web_clips` (the "Web clips" page), `markdown_import` (the file's hash), `original_filename` (the name of the Markdown file the page was made from, beside `markdown_import`; never a document's name), `notion_id`, `aliases` (a zip import), `notability_import`, `goodnotes_import` (a note app's re-import key), `zotero_key`, `zotero_pdf`, `zotero_note`, `zotero_rdf`, `zotero_annotation`, `zotero_attachments` / `zotero_attachment`, `seeded` (the Welcome page and the Sketchbook) | [dev/extension.md](../dev/extension.md), [dev/import_export.md](../dev/import_export.md), [dev/zotero_sync.md](../dev/zotero_sync.md), [dev/onboarding.md](../dev/onboarding.md) |
| in Recently deleted | `deleted_at`, `deleted_by`, `deleted_with` (on a sub-page deleted with a page: that page, whose restore brings it back) | [dev/home_library.md](../dev/home_library.md) "Recently deleted" |

Keys no writer produces any more. `pdf_page` on a highlight is rewritten
by `gamma/normalize.py` when a workspace is upgraded or a backup is
restored; the properties panel tags one it still finds "Old key". Two stay
in the rows that have them: `summary` (a page summary typed before July
2026, or the old Welcome page's canned one), which only the agent's page
header still reads, and `guide_demo`, the first-run tour's old marker,
which nothing reads.

## Is it well defined?

**The discriminator, yes.** One rule, three copies that cite each other
(`db.BLOCK_HOT_COLUMNS`, `blocks_store.block_kind`, the client's
predicates with their comments), the SQL and Python copies tested against
each other, the client's predicates tested in `blockOps.test.mjs`. The
rule is also cheap to reason about: presence of a key, in a listed order.

**The special values, yes.** Each kind-making object has a normalizer on
both sides (`normalize_text_box` / `normalizeTextBox`, `normalize_picture`
/ `normalizePicture`, the paper, the recording, `highlights.from_scaled`)
that gives a bad key its default, clamps and rounds numbers the same way
and drops unknown keys, and the two sides are pinned by the fixtures in
`tests/shared/` (`textbox.json`, `textboxmerge.json`, `picture.json`,
`picturemerge.json`, `paper.json`, `zoterotext.json`, the `ink*.json`
family). The text box and the picture also merge key by key under
concurrent edits (`ops.PLACED_KEYS`, `blockOps.PLACED`), the one place
the server normalizes a value on the way in.

**The catalog, no.** Three things are missing:

1. **No list.** The keys above are documented, but across
   [dev/user_db.md](../dev/user_db.md), [dev/api.md](../dev/api.md), the
   backend README's diagram (which stops at highlights and links) and six
   topic docs. Nobody can answer "which keys may a block carry" without
   reading all of them. This note is the first list.
2. **No schema on the API.** `POST /blocks` and the op path take
   `properties: dict`. `ops.set` and `ops.insert` check only what SQLite
   cannot store (lone surrogates, non-finite numbers), the page row's
   filing lists, a block's document (a `doc` is checked and stored
   canonical, and the flat keys it was kept in before migration step 51
   are refused), and that a share editor does
   not touch the page row. Any other key, of any shape, is stored. The
   normalizers make reads safe, so this is not a correctness hole; it is
   why a typo or a stray marker can live in a row forever.
3. **Two spellings.** Keys are snake_case except inside `pdf_position`,
   whose `pageNumber`, `boundingRect`, `x1`…`y2` are the
   react-pdf-highlighter spelling the shape started from. Documented, and
   step 30 chose to keep it, but it is the one place a reader has to know.

## Is it clean?

Mostly, and more so after the [cleanup](#cleanup-november-2026). The debt
the earlier survey listed ([data-model.md](data-model.md) item 6) is
paid. What stays:

- **Cached derivations.** `ink_strokes` repeats what the ink file holds
  (its one reader is the agent's outline), an ink group's `pdf_position`
  its strokes' box (for queries), and `recording.dur` the segments' total
  (the server reads it first, the client recomputes it; a Notability
  re-import stores the whole recording's length on one segment). Each is
  kept in step by its writers. `bibtex` beside `meta` is not one of them:
  it is the entry of record, either the registrar's own text, an
  imported entry or a `build_entry` output whose cite key people's `.tex`
  files already use, so it cannot be rebuilt from `meta`.
- **Old record shapes read on both sides.** Pre-Zotero `meta.kind` names
  (`item_types._LEGACY`, `itemTypes.js` `LEGACY`) and AI records stored
  before `meta.unverified` are read with a fallback. No step rewrites
  them, so the readers can go only with a numbered workspace step, which
  pauses mirror pairs until both sides upgrade.
- **Two "link" vocabularies.** `link_url` / `link_page_id` /
  `link_block_id` (a link region: the block points somewhere) and
  `linked_highlight_id` (a note attached to a highlight: something points
  at the block). Correct, documented, easy to misread.

Nothing found is a bug. The structure is the one its design doc wanted
([dev/block_centric.md](../dev/block_centric.md)): no page types, the kind
implicit in which property is present, every feature carrying its own
keys.

## Where a property can be seen or changed

**The block menu is not a right-click.** A click on the ⋮⋮ handle opens
it (`BlockTree.jsx`, `onHandleClick`); the row's `onMouseDown` passes a
right-click through, and the CodeMirror editor only places the caret on
one, so a right-click on note text shows the browser's menu (spelling,
copy, paste). The menu offers Copy link to block, Copy as markdown,
Properties, Comment, Add to chat, Transcribe with AI (an ink group), Add
page below, Duplicate, Move to page… and Delete. A read-only page draws
its rows without the handle.

**Properties** opens a read-only popover (`editor/BlockProperties.jsx`,
[dev/ui-design.md](../dev/ui-design.md#the-properties-popover)) that
shows what the block is and every key it stores, as labelled rows in
sections, or as JSON. Its `CATALOG` (`editor/blockProps.js`) mirrors
[the catalog](#the-catalog) above, one entry per key, and a key the
catalog does not list shows under Other keys, tagged "Not in the
catalog". It also opens from the palette (Block properties, Page
properties), a tab's menu, the home menu of a page, a folder or a label,
and the highlight's menu on the PDF. It reads `GET /api/blocks/{id}` for
the server's `kind` and the dict's own fields, and edits nothing.

What a right-click does open is the menu of the *object* under the
pointer, and each of those menus edits that object's own property. Every
key below is also shown, read-only, in the Properties popover:

| Property | Where it is shown | Where it is changed |
|---|---|---|
| `color`, the link keys, `approx` / `lost` | the highlight on the PDF and its card in the notes | the highlight's right-click menu on the PDF: swatches, Link to page… / Change link…, Accept and Move… after a replacement, Delete ([dev/highlight_relocation.md](../dev/highlight_relocation.md)) |
| `picture` | the picture on the page | its menu: Crop, Flip, Opacity, Lock, Bring to front, Reset; its handles |
| `text_box` | the box on the page | its menu and the style picker (size, colour, fill, auto width) |
| `ink_url` | the strokes | the lasso's menu and Style picker; Transcribe in the handle menu |
| `sheet` | the notebook view | the paper menu |
| `recording` | the recording card and player | the pill while recording |
| `collapsed` | the fold triangle | folding, as a document edit |
| `zotero_readonly` | the badge on the row | the Zotero link only |
| `meta`, `bibtex`, `cite_key` | the ⓘ metadata popover | the same popover ([dev/paper_metadata.md](../dev/paper_metadata.md)) |
| `doc` | the paperclip popover | attach, replace, detach there |
| `folders`, `labels`, `pinned` | the header's labels, the cards, the folder tree | the home context menu (right-click on a card, a folder or a label) |
| `parent_page` | the crumbs above a sub-page's title and its parent's Pages inside panel ([dev/home_library.md](../dev/home_library.md#sub-pages)) | `/page`, New page inside, Move into page… and Move to library (the home page menu, the tab menu) |
| `deleted_with` | Recently deleted, which lists only the page it went with | that page's Restore and Delete permanently |
| `t0`, `ink_strokes`, `imported_annot`, `annot_stripped`, `linked_highlight_id`, `auto_title`, `meta_error`, `web_meta`, `markdown_import`, `original_filename`, `notion_id`, `aliases`, `zotero_*`, `seeded`, `summary` (the agent's page header only), `guide_demo` | the Properties popover (read-only) | `GET /api/blocks/{id}`, `PUT /api/blocks/{id}` with a properties patch, or `sqlite3` on `pages.db` (`json_extract(properties, '$.key')`) |

The agent's `read_block` and `read_page` tools load the properties but
show the model an outline, not the keys ([dev/ai_tools.md](../dev/ai_tools.md)).

## Options this leaves open

Findings, not decisions:

- A catalog table in the dev docs, one line per key with its row, shape
  and doc: the first table above, moved to [dev/user_db.md](../dev/user_db.md)
  "pages.db" or [dev/api.md](../dev/api.md) "The block dict", and kept
  with the code.
- A **Properties** row in the ⋮⋮ handle menu showing the block's
  `properties` read-only. Built: the Properties popover
  ([above](#where-a-property-can-be-seen-or-changed)). Still open: a raw
  edit for members who want one. A PATCH with a null value deletes a key,
  so the editor would be the op path as it is.

## Cleanup (November 2026)

What the survey and a second, wider sweep found historic was removed on
the `property-cleanup` branch. Behaviour is unchanged except where a line
says otherwise.

**The client's model and rows.**

- `withLegacyAccessors` is gone. The rows read `blockColor`,
  `blockQuote`, `blockPage` and `blockPosition`, and `walkBlocks` no
  longer overwrites a node's `position` (the server's sibling key) with
  the PDF place.
- The viewer's highlight is a flat `{id, quote, note, hasNote, color,
  position}` instead of react-pdf-highlighter's `{content: {text},
  comment: {text}}`, and `addHighlightAsBlock` takes the same fields.
- Gone with no reader: the camelCase `parentId` written on new nodes,
  `_isEmpty`, `normalizeBlocks`, `blocksToPageMarkdown`, App's `summary`
  state, the `makeBlockId` alias (one `makeId`), and blockOps' private
  copies of `updateBlockTree`, `extractBlock` and the JSON comparison.
- `removeBlockTree` copies only the path and returns the same array on a
  miss, so its callers dropped their `findBlock` guards; `appendChild` is
  gone.
- `isInkGroup` and `isLinkBlock` join the per-kind predicates. Every
  object test uses `isRecord`, so an array-valued `pdf_position`,
  `text_box` or `sheet` is classed as the server classes it.
- The first-run tour keeps its demo note's id in App instead of writing
  `guide_demo`. Resuming an unfinished demo now works within a session
  only.
- Writers store what readers expect: a link region's unset link and an
  unpinned page delete the key instead of storing `""`, a new link region
  stores `LINK_COLOR` (`#8cb4ff59`), and pasted blocks, a text box's
  Duplicate, an object moved out to its own block and the tour's new
  demo note carry `t0`.

**The server.**

- One kind rule: `blocks_store.block_kind` tests links through
  `highlights.is_link` and ink groups through `highlights.is_ink`
  (`isInkGroup`'s rule: an `ink_url` key, whatever its value), which the
  exporters, the Zotero sync and the agent's handwriting tools use too.
  `zotero_annotations._kind` and the agent's delete card read
  `block_kind`, so a deleted link region is now called a link and an
  empty ink group handwriting.
- One page-row rule: `blocks_store.page_root_props` (filing as id lists,
  no redundant `source_url`) for `create_page` and `insert_page_tree`, so
  imported pages follow it too; `zotero_local`'s copy is gone.
- `PUT /blocks/{id}/children` refuses properties that are not an object
  (a JSON string was the April 2026 client's shape) and checks its body
  like the other block writes.
- `GET /api/share/{token}` no longer sends `doc_id`, a fallback from
  shares keyed by PDF.
- A web clip's selection is inserted through the op path; the raw
  `_insert_last` writer is gone.
- `/metadata/cite` sends `bibtex.page_entry` instead of the raw stored
  entry.
- Dead parameters and aliases: `normalize_data_db(keep_prefs)`,
  `normalize_paper`'s and `normalizePaper`'s fallback paper, the Logseq
  builders' string properties, `render_notes`' `note` string,
  `STORED_COLUMNS` (one `BLOCK_COLUMNS`).
- `notebook.normalize_paper` rounds as its JavaScript twin does (a
  hundredth's half up); `tests/shared/paper.json` pins a tie.
- Comments and docs that described removed code were rewritten, among
  them `normalize.py`'s docstring (its steps run on a restore; the
  baseline step that also ran them is gone).

**Left as it was, on purpose.**

- Every step of `gamma/normalize.py` and `gamma/migrations.py`: a restore
  takes backups of any age.
- Reader tolerance for what older tabs, mirror peers, imports and other
  apps still send: `rgba()` colours, `""` link keys and pins,
  `guide_demo` in old rows.
- The old record shapes and the cached derivations above.
- The Logseq `{:width N}` image size in content. The Logseq and Markdown
  imports convert it to `![alt|N](url)`, but stored rows keep it: a
  paste, the API or a mirror peer stores it as sent, an earlier release's
  imports stored it as written, and a restore rewrites it only on a copy
  stamped below `migrations.WS_VERSION_BASE` (a backup from before the
  stamps, or a scoped Gamma export). So its readers stay: one per side,
  `note_markup`'s image sizes and `mdMarks.js` `scanImageSyntax`.
- A mirror whose remote predates the data-format check still syncs.
  Refusing it would stop syncing with servers on v0.2.15 to v0.2.17.
