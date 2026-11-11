# A page's document

A page may carry one *document*: the file it shows beside its notes, a PDF
on every page so far. The document is one property of the page's row,
`properties.doc`, an object that says what the file is (`format`), what it
is called (`name`), which stored file it is (`hash` and `ext`) and where
its bytes are read from (`at`). One function on each side reads it, and
every question a reader asks of a page's file ("does this page own a
PDF?", "which stored file does it show?", "is it read in place?") is a
predicate over what that function answers. A nested block may carry a
`doc` of the same shape (older data, the properties inspector); the
lookups of "the page of this file" never count one.

Code: `gamma/documents/formats.py` (the format registry),
`gamma/documents/model.py` (the stored shape, its builder, its read and the
predicates), `gamma/normalize.py` `document_shape` and `page_documents`
(older rows rewritten, migration step 52), `db.BLOCK_HOT_COLUMNS["doc_id"]`
(the generated column), and on the client `frontend/src/documents/`:
`formats.js` and `pageDocument.js`, the twins, and `documentView.js` and
`DocumentPane.jsx`, which decide and draw the viewer a page shows. Tests:
`backend/tests/test_documents.py`, `test_shared_fixtures.py`,
`test_migrations.py` (`test_v51_*`), `test_restore_consistency.py` (a
backup from before step 52), `frontend/tests/documents.test.mjs` and
`libraryUtils.test.mjs`, over the fixtures `tests/shared/formats.json` and
`tests/shared/document.json` ("Fixtures" below).

## Formats

What a file is follows from its name, through one table. `formats.FORMATS`
are the seven formats a document can have: `pdf`, `markdown`, `text`,
`image`, `audio`, `video` and `binary`. *Format* is the registry's word,
since "kind" names the block's `kind` column and half a dozen other things.

- **`EXTENSIONS`**: one row per extension Gamma knows (`formats.Ext`), with
  its format, the language of a text one (`latex` for `.tex`, `python` for
  `.py`, `json` for `.ipynb`) and how a stored file with it is served: its
  media type (none: `application/octet-stream`), `inline` (the browser
  renders it on navigation; every other file is sent with
  `Content-Disposition: attachment`) and `sandboxed` (a sandboxing CSP as
  well, for an SVG or HTML file opened as a top-level document). An
  extension the table does not list is `binary`, served as a download.
- **`NAMES`**: whole names that carry no extension, lowercased, each with
  its format and language: `Makefile`, `Dockerfile`, `CMakeLists.txt`,
  `LICENSE`, `README`, `.gitignore`, `.gammaignore`, `.env` and a few more.
- **The serving views**, read off the table: `IMAGE_MEDIA_TYPES`,
  `IMAGE_EXTENSIONS` (a media type's first extension: `image/jpeg` is
  `.jpg`), `ALLOWED_IMAGE_TYPES`, `INLINE_EXTENSIONS` and
  `SANDBOXED_EXTENSIONS`. `GET /api/uploads/{name}`, the image and file
  uploads, the recordings, the chat's pictures and the importers read
  them, so a row of the table is served alike everywhere.
- **`BLOCKED_EXTENSIONS`**: what an operating system runs when opened
  (`.exe`, `.bat`, `.ps1`, `.lnk` …), the one kind of file Gamma refuses to
  store. Archives and packages are fine.
- **`EXTENSION_RE`** (`^\.[a-z0-9]{1,12}$`): the shape of a stored
  extension.

The functions:

- `extension_of(name)`: the last suffix, lowercased; `""` when there is
  none (`.gitignore` has none) or it has no stored extension's shape.
- `stored_extension(name)`: the extension a file is stored under, `.bin`
  when it has none; a `ValueError` for a blocked one, which the uploads
  answer as a 400.
- `media_type(ext)`: how a stored file is served; None for a blocked or
  malformed extension, which `/api/uploads/` refuses.
- `format_of(name, head=None)`: `(format, language)` by `NAMES`, then
  `EXTENSIONS`, then the first bytes when `head` is given, else
  `("binary", None)`. `language_of(name, ext)`: a text document's language.
- `sniff(head)`: a name the table does not know, settled by its first
  8 KiB. `%PDF-` at the start is `pdf`; a NUL byte or bytes that are no
  UTF-8 are `binary` (a multi-byte character cut by the limit is still
  UTF-8); anything else, nothing included, is plain text. Only its fixture
  calls it so far; its caller is the attach of a file of any format
  ("What step 3 adds").

`formats.py` imports nothing from Gamma. The client's `documents/formats.js`
is its twin for names only, since the client never serves a file or sees
its bytes: `FORMATS`, `EXTENSION_RE`, `BLOCKED_EXTENSIONS`, `EXTENSIONS`
(format and language), `NAMES`, `extensionOf`, `storedExtension` (null for
a blocked extension), `formatOf` and `languageOf`. The file chip
(`transfers/FileChip.jsx`) and `shared/lib/utils.js` (`isPdfFile`,
`isMarkdownFile`) ask it what a name is.

## The stored shape

```text
doc: {format, name, hash, ext, at, url?, size?, copy?}
```

| Field | Holds |
|---|---|
| `format` | One of `FORMATS`: stored, never read again from the name. Every document is `pdf` so far. |
| `name` | The file's shown name, a display leaf (`storage.display_filename`, at most 500 characters), or `""` on a page that never had one: a clip, a page opened from an address, a Zotero or zip import. |
| `hash` | The stem the file is, or would be, stored under in the workspace's uploads: the content hash (`sha256[:24]`) of an upload or of a file read in place; the hash of the address (`url_hash`) for a PDF opened or clipped from an address, whether or not a copy was cached; the name a stored PDF kept when its embedded annotations were stripped. The shape `db.safe_doc_id` takes. `""` only at `url`. |
| `ext` | The stored file's extension, lowercase with the dot (`EXTENSION_RE`); `.pdf` for a PDF. The stored name is `hash + ext` (`stored_name`). |
| `at` | Where the bytes are read from: `store`, the workspace's uploads; `path`, a file on this computer through a `vaults.db` `linked_files` row ([synced_vaults.md](synced_vaults.md#a-pdf-read-where-it-is)), with a stored copy perhaps; `url`, an address the PDF proxy fetches, with a copy cached under `hash` perhaps. |
| `url` | Only at `url`, never empty: the address the document is shown from. Never its own stored address (that document is at `store`); it may be another upload's, on a page the Zotero merge gave a PDF of its own while it showed a duplicated one's. |
| `size` | The file's bytes, a whole number. Reserved: no writer sets it and nothing reads it ("What step 3 adds"). |
| `copy` | `true` or absent, only at `store`: the page shows a stored file it does not own, a duplicated page or one attached by the bare address of an upload. |

No other key, and every writer stores the fields in that order, the
rewrite of older rows included, so two copies of a page are byte-equal.
A stored doc holds to these rules, and `document` refuses anything else:

1. At `store` or `path` there is a hash and no address.
2. At `url` there is an address; the hash is the address's or `""`.
3. Only a document at `store` is a copy.
4. A PDF's extension is `.pdf`.

Two more hold across the shape's history. The hash of every page that had
a doc id before step 52 is that doc id, so every record keyed by a file's
hash stays valid untouched: `data.db`'s `pdf_docs`, `pdf_fts*` and
`pdf_translations*`, highlights' `pdf_position.doc_id` and `prev.doc_id`,
`vaults.db`'s `linked_files` and `vault_files`, the Zotero link's
records, job keys (`<page>:<hash>`), translation memory (`doc:<stem>`)
and saved chats. And a stored PDF's address is `/api/uploads/<hash>.pdf`,
the key the viewer's copy in IndexedDB is kept under
([pdf_loading.md](pdf_loading.md)).

`format` and `ext` are stored rather than read from `name` because names
are often empty (clips, URL pages, Zotero and zip imports), made up (the
note-app imports name a page's PDF `<title>.pdf`), another file's
(`Reading.md` on a PDF page from a Markdown zip) or without an extension
(`2301.12345v2`, whose last suffix even has a stored extension's shape),
and one format has many extensions. `copy` is a flag because a duplicated
page shows the original's file (referenced, shared, translated and
relocated against like the original's) but is never "the page of that
PDF": the flag says so in one place, and the generated column leaves
copies out.

Not part of `doc`:

- `original_filename` is the Markdown provenance key alone: the `.md` or
  `.csv` a page was made from, beside `markdown_import`
  ([import_export.md](import_export.md)). It is never a document's name.
- `web_url`, `web_meta`, `opened_files`, `parent_page`, `zotero_pdf`,
  `auto_title` and `meta` are page properties of their own.
- A highlight's `pdf_position.doc_id`, and every HTTP field named `doc_id`
  (the upload answers, the attachment and by-doc bodies, the clip, job
  parameters, metadata status rows, search hits): these name a stored
  file, not a page property ([api.md](api.md)).

## Writing a document

`documents.model.document(hash, url, name, *, at, format, ext, size, copy)`
is the one builder of a stored doc. It answers the canonical form or
raises `ValueError`:

- `ext` defaults to the format's own (`FORMAT_EXT`, `.pdf` for a PDF); any
  other format names it.
- A hash passes `db.safe_doc_id`.
- No hash and an address that is exactly a stored file's of that extension
  (`parse_upload_url`): a copy of that file, at `store`, with no address.
- A hash and its own stored address: the address goes, and `at: "url"`
  becomes `store`.
- `at` is `url` when an address is left, else `store`, unless given.
- The four rules hold; `name` is reduced to its display leaf.

`check_document(value)` checks a doc a writer supplies (an object of the
eight fields or some of them, each of its type) and rebuilds it through
`document`. `document_props(props, patch=False, refuse=True)` is every
writer's rule for properties: a `doc` is rebuilt by `check_document`, a
`null` one dropped (kept in a `patch`, where it removes the key), a flat
key a document was kept in before step 52 (`LEGACY_KEYS`: `doc_id`,
`source_url`, `linked_file`) dropped when `null` and refused with a value.
It raises with the reason, or with `refuse` false drops what it would
refuse.

Where a document is written:

- **The op path** (`ops._document_patch`, `document_props` as a 400, in
  `_Batch.set` and `insert`, on every block): `doc: null` removes the
  document; a `doc` is checked and
  stored canonical, or the batch is a 400 with the reason. A legacy key
  with `null` is passed by (a mirror's base, an older client clearing what
  is not there); one with a value is a 400, "`<key>` is no longer a block
  property: a block's document is its doc property". A share editor may not
  change the page row ([collab.md](collab.md)), its `doc` included.
- **The routes that take raw properties** store what `document_props`
  leaves, or answer its 400: `POST /api/pages`, `POST /api/blocks` (any
  parent) and `PUT /api/blocks/{id}/children` (every node, before anything
  is written).
- **A new page a client writes whole** (`POST /api/blocks` at root, the
  duplicate's route) never makes a second owner of a file
  (`blocks_store.second_owner_as_copy`): a doc whose hash a library page
  owns (`page_for_doc`), or one read in place whose file another page
  reads in place, is stored as the copy a duplicate writes
  (`model.copy_document`, the twin of the client's `copyDocument`). That
  is the request an open tab from before step 52 sends when it duplicates
  a page: the properties read fresh, `doc` among them, `doc_id` taken out.
- **The page writers inside the server** go through
  `blocks_store.page_root_props` (`create_page`, `insert_page_tree`, a page
  a mirror creates), which drops what is malformed
  (`document_props(..., refuse=False)`): the legacy keys, and a `doc` that
  does not check.
- **The request bodies** keep their own words: `POST
  /api/pages/{id}/attachment {doc_id, source_url, original_filename}`,
  `POST /api/blocks/by-doc/{doc_id}`, the clip. Each becomes a doc once,
  through `document`; an id it refuses is a 400. On a page read in place,
  `PUT /attachment` without an address keeps `at: "path"`.
- `document_at(doc, at)` is the same file read from `store` or `path`: Add
  to library on an opened PDF, a vault's PDF stored on this server.
  `document_title(name, url, hash)` is the automatic title a document
  gives a page: its name, else the name its address suggests, else its
  hash.

## Reading a document

`page_document(props)` is the one read of what a block carries: a view of
its `doc`, or None when it has none or one that does not check (a
malformed doc reads as no document; the read never raises).

```text
{format, language, name, hash, ext, size, at, url, file, copy}
```

`language` is a text document's (`formats.language_of`), else None; `size`
None when not stored; `url` where the document is fetched from, its
address at `url`, else `/api/uploads/<file>`; `file` the stored name,
`hash + ext`, `""` without a hash, whether or not the bytes are there
(that stays `storage`'s question); `copy` a bool. No reader decides what a
page is from the raw properties.

A document means different things to different readers, and each reader
names the one it means:

| The reader asks | It reads | The answer |
|---|---|---|
| does the block carry anything | `page_document(p)` | the view, or None |
| which file the page **owns** | `own_hash(view)` | the hash; `""` for none and for a copy |
| which stored file the page **shows** | `file_hash(view)` | the hash, a copy's included (the original's) |
| is it a PDF | `is_pdf(view)` | `format == "pdf"` |
| is it read in place | `in_place(view)` | `at == "path"` |
| which stored PDF the page shows | `pdf_file_hash(view)` | `file_hash` of a PDF, else `""` |
| the address it is shown from | `source_address(view)` | `url` at `url`, else `""` |
| where to fetch it | `view["url"]` | the address, or the stored file's |
| its shown name | `view["name"]` | `""` when it has none |
| a stored name, an upload's address | `stored_name(hash, ext)`, `upload_url(name)` | the one place each is built |

**Identity readers** use `own_hash`, with `is_pdf` where they are about
PDFs alone: `page_for_doc` and `pages_for_docs`, by-doc and the
attachment's 409, `clip.find_page`, a backup merge's match (`ws_backup`),
the search's hit-to-page map (`root_pages`, through the column), the
metadata lookup and its status, the Zotero item identity
(`zotero_merge.wants_item`), a folder's manifest on disk (`folder_sync`: a
copy writes no PDF there), the import reports' `pdf` or `page`, a vault's
page identity, and the client's `docId` (`ownHash(doc)`: the metadata
popover, the PDF-text status, the annotation import).

**Bytes readers** use `file_hash` (`pdf_file_hash` where they read a
PDF), so a duplicated page reads the file it shows as the original does: the AI's context and tools
(`ai_context.page_report_section` and `gather_inputs`, `read_page`'s PDF
window, `view_pdf_page`, `clip_region`, `ai_pictures.page_doc_id`,
`ink_view.picture`), the exports (the graph and Obsidian bundles, the
Zotero RDF's PDF, the annotated PDF and its translation, Notability), the
highlight relocation, translation memory, the PDF index's live set
(`pdf_index._live_docs`: a copy keeps the rows of the file it shows), the
Zotero link's comparison of files, a folder chat's PDF search
(`ai_tools._scope_pages`), and on the client `pdfStored`, the viewer's `fileId`, the translation scope, the
annotated PDF's translation (`translateExport.exportFile`,
`folderTranslation.folderPapers`) and the chat's PDF ids.

**Read in place** (`in_place`, with `SQL_LIBRARY_PDF` in the statements)
is asked only where a page read in place is set apart from the library: the one-page-per-document lookups,
`clip.find_page`, `ws_backup`'s review and merge, `linked_files.open_pdf`
and its carry, and the client's `duplicable`, `isOpenedPdf` and
`pdfLinked`. Every other PDF reader takes a PDF stored or read in place
alike (`is_pdf`, `own_hash`): the metadata lookup and its status, the Paper
metadata button, `findPageForUrl`, the AI, search and the Zotero link.

## In SQL

`model.py` holds the conditions that are spliced into `WHERE` clauses over
a `unified_blocks` row:

- `SQL_PDF`: its document is a PDF (`json_extract(properties,
  '$.doc.format') = 'pdf'`).
- `SQL_IN_PLACE`: it is read in place (`$.doc.at` is `path`): the linked
  files' idle sweep (`linked_files._IDLE`), and the Gamma export, which
  carries the bytes and rewrites its copy's documents to `at: "store"`.
- `SQL_LIBRARY_PDF`: a PDF that counts for the library's
  one-page-per-document lookups, one not read in place. It goes with the
  `doc_id` column, which leaves copies out: `page_for_doc`,
  `pages_for_docs`, `clip.find_page`, `ws_backup`.

**The generated column.** `unified_blocks.doc_id`
([user_db.md](user_db.md) "pages.db") is the hash of the document the row
owns:

```sql
doc_id TEXT GENERATED ALWAYS AS (CASE WHEN json_extract(properties, '$.doc.copy') IS NULL
    THEN NULLIF(json_extract(properties, '$.doc.hash'), '') END) VIRTUAL
```

NULL on a row without a document, on one shown from an address alone and
on a copy. It covers every row, nested and trashed ones included, with the
partial index `idx_ub_doc`. Its name is the column's from before step 52,
so the statements that read it (`page_for_doc`, the upload references,
the PDF index purge) read it as they did. The share checks
(`routers/uploads.py`) read `doc.hash` and `doc.ext` instead, of any
document, copies and a URL's cached copy included: a page's own, and the
copy a block inside it shows. `root_pages`
answers `CASE WHEN SQL_PDF THEN doc_id END`, `""` for a page whose
document is no PDF. The column reads `$.doc.hash` raw, so a malformed
`doc` with a hash passes it; a reader that reads the row's view as well
(`clip.find_page`) skips a row `page_document` refuses. The library
listing's summary (`routers/blocks.py` `SUMMARY_PROPS`) carries `doc`
whole.

**Stored files.** `storage.upload_refs` ([user_db.md](user_db.md) "Stored
files") counts a block's document as its `file`, at any `at` and on a copy
too, so a copy's file and a URL page's cached copy stay referenced; a `doc`
that does not check still holds the stored name its `hash` and `ext` make
(`.pdf` without one), so a doc no reader can read (a raw writer's, or one
with a field a later release adds) never lets its file go, though it shows
none. The orphan pass (`upload_gc`) reads the blocks whose properties name
`"doc"`.

## Older shapes: migration step 52

Before step 52 a page kept its document in flat keys: `doc_id` (the hash),
`source_url` (an address, stored only when the file came from elsewhere; a
duplicated page kept an upload's address alone), `original_filename` and
`linked_file: true` (read in place). Step 52 (`page_documents`, a workspace
part alone, [migrations.md](migrations.md)) rewrites every row, nested
and in Recently deleted too, through `normalize.document_shape`, then
gives the generated `doc_id` column the definition above (`block_columns`
drops and adds `doc_id`, `parent_page` and `deleted_with` with their
indexes).

`document_shape(props)` goes in this order, with H the old `doc_id` (a
string `safe_doc_id` takes, an int's digits, else none) and U its
`source_url`, else the camelCase `sourceUrl`, stripped as `document` strips
an address (one that is only whitespace is none):

1. A `doc` there already is kept, and the flat keys go.
2. Neither H nor U: no document, and the flat keys go.
3. The name is `original_filename` as it is, which goes with it, except on
   a page made from a Markdown file (`markdown_import`), where
   `original_filename` stays the source's name and the document has none.
4. H and `linked_file`: read in place, any address dropped.
5. H, and no U or U its own stored address: stored.
6. H and another U: at U, with H as the hash it owns.
7. No H, and U exactly a stored PDF's address: a copy of that file.
8. No H, any other U: at U, with no hash.

Every document it makes is `format: "pdf"`, `ext: ".pdf"`, built directly
in canonical order, so nothing raises and two copies rewritten apart
agree. Every state the rows held, by the letters
`tests/shared/document.json`'s `legacy` cases go by:

| | Before | Made by | After |
|---|---|---|---|
| A | `doc_id` H, `original_filename`? | uploads, by-doc, imports, the Welcome page, a vault's page stored here | stored, `hash` H |
| B | H, `source_url` an address (H its hash) | Open PDF by URL, a clip, a by-doc backfill | at that address, `hash` H |
| C | H, `source_url` `/api/uploads/H.pdf` (or `sourceUrl`) | rows older than step 30, raw writers | stored, `hash` H |
| D | `source_url` `/api/uploads/H.pdf` alone | a duplicated page, an attach by an upload's address | a copy of H |
| E | `source_url` an address alone | an attach by URL, a duplicate of a URL page, a Markdown zip's `source: http…` | at that address, no hash |
| E5 | H, `source_url` `/api/uploads/K.pdf` | the Zotero merge onto a copy | at `/api/uploads/K.pdf`, `hash` H: shows K, owns H |
| F | `source_url` another host's upload, a picture's upload, an upload with a query, a stem of 129 characters | fixtures, raw writers | at that address, no hash |
| G | H, `linked_file` | Open with, Opened files, a vault's PDFs | read in place, `hash` H |
| G2 | H, `linked_file`, `source_url` | no writer found | read in place; the address goes |
| G3 | `source_url` `/api/uploads/H.pdf`, `linked_file` | a copy of a page read in place | a copy of H |
| H | `linked_file` alone | a detach of a page read in place | no document |
| I | `original_filename`, `markdown_import` | the Markdown, Notion and Obsidian imports | unchanged |
| I2 | as I, with `doc_id` or `source_url` | a Markdown zip's page with a PDF | as A, B or E, `name` `""`; `original_filename` and `markdown_import` kept |
| J | `doc_id` `""`, `source_url` `""` | older tabs and peers | no document |
| K | `sourceUrl` | the earliest pages | read as `source_url` |
| N | a `doc_id` `safe_doc_id` refuses, or no string | raw writers | as if absent (an int is its digits) |
| O | a `doc` already | rows written after the step, an export from a server at 52 | kept; the flat keys go |

The step is pure: no disk reads, no `updated_at`, no op-log or change-log
row (a shape is no edit). It rewrites the mirror's stored bases as well
(`sync_pages`: every block entry but a push in flight, `~pending`, which
is resent as it was), so the first round after both sides upgraded sends
nothing for the shape.

It is not in `migrations.DERIVED_STEPS`: stored properties change, so
`workspace_format()` moves from 44 to 52 ([mirror.md](mirror.md) "The data
format"), and a mirror pair or a publish pauses until both sides have run
it. The other readers of another server's pages hold the same line: a
desktop vault on another server (`vault_http.HttpRemote`) refuses a feed of
another format, as the mirror does, and `gamma_sync` refuses a server
below format 52 (`Server.check_format`, `DOCUMENT_FORMAT`;
[synced_vaults.md](synced_vaults.md#the-client)). An older gamma_sync
cannot be told from the server: it finds no document on a server at 52,
and is updated with it. An open tab from before the upgrade keeps the flat
shape until it reloads; its one document write, a duplicate, sends the
page's `doc` read fresh, which the server stores as a copy of the
original's file ("A new page a client writes whole" above).

As the restore's normalizer ([migrations.md](migrations.md) rule 6), the
step runs on every backup or scoped Gamma export stamped below 52, an
unstamped export (every scoped export before release 52) counting as the
base; rows already in this shape pass by rule 1. A scoped export is
stamped with its release (`_GammaBuilder`), so a release before 52 refuses
one as "made by a newer Gamma" rather than merging pages whose document it
cannot read. On a copy stamped below the base the content normalizers run after
the steps: `normalize_pages_db` reduces a `doc.name` with a path to its
leaf (the step copies `original_filename` as it is), and `highlight_shape`
(step 30) reads the flat `doc_id` key itself, so it finds nothing on rows
step 52 has rewritten.

## The client

`documents/pageDocument.js` is `model.py`'s twin: `storedName`,
`uploadUrl`, `parseUploadUrl`, `pageDocument(block)` (the same view by the
same checks, or null) and the same predicates, `ownHash`, `fileHash`,
`isPdf`, `pdfFileHash`, `inPlace` and `sourceAddress`. Beside them:

- `pdfProxyUrl(url, {save, share})` and `pdfViewerUrl(src, {share})`: the
  address the viewer loads a PDF from, a stored `/api/` address as it is
  (with a share's token), anything else through the proxy.
  `documentViewerUrl(doc, {share})` is that for a page's PDF document, `""`
  for none. The rule lives here alone: `shared/lib/utils.js` imports
  `pdfProxyUrl` (`probePdfUrl`), and `BlockProperties.jsx` calls
  `pdfViewerUrl`.
- `copyDocument(doc)`: the stored doc a duplicate writes (App's
  `duplicatePage`). A stored file is shown as a copy of it, an address is
  kept without the original's URL hash (an upload's address making it a
  copy of that upload), and a file read in place is shown from its stored
  copy, which `duplicable` never offers; null for no document.

The library's rows (`library/libraryUtils.js` `pageBlocksOf`) carry
`_doc`, the page's view. `duplicable` leaves out pages read in place,
`pageKindLabel(doc)` says "PDF" or "Page", `defaultPageTitle(doc)` titles a
page from its document's name or address, and `findPageForUrl` matches a
link against `sourceAddress` (a copy's upload address holds no DOI or
arXiv id). `FileGlyph` (`shared/ui/Icons.jsx`) takes a `format` and draws
the PDF glyph for `pdf`; the cards, the pickers' rows, the open tabs (App's
`tabKinds`) and the page family's `sub_pages[]` hand it theirs. App
holds the open page's `doc` (`pageDocument(focusedBlock)`) and `docId`
(`ownHash(doc)`); the chat (`chat/ChatDock.jsx`) takes `doc` and reads its
PDF by `fileHash`.

### Which viewer a page shows

`documents/documentView.js` is one rule for the open page (App) and a tab
group's page (`workspace/PagePane.jsx`, [tab_groups.md](tab_groups.md)).
`viewerOf(format)` is the document pane a format gets (`FORMAT_VIEWER`,
`pdf` alone; a Markdown note has none, since its notes are the file), and
`documentView({doc, url, sheets, notebook, hidden, open, dock})` answers:

| Field | Meaning |
|---|---|
| `viewer` | what mounts: `"pdf"` when there is a viewer address, else `"notebook"` while the notebook view shows, else null |
| `notebook` | the notebook view shows ([notebooks.md](notebooks.md)) |
| `notebookAvailable` | the page has sheets and no document pane |
| `hidden` | the pane is closed (`pdfHidden`), never while the notebook shows |
| `shown` | a viewer mounts and shows; never in a docked Notes view |
| `center` | the notes take the middle: in a docked Notes view, with the pane closed, or on an open page with neither a pane nor its notebook view (App adds its home view) |

App names its answer `docView` (`view` is the notes view's state there)
and reads its gates from it: the viewer's close, tools and full-screen
buttons, the ink strip, the zoom, the translation, the phone bar and the
page header's Notebook button. `documents/DocumentPane.jsx` draws the
viewer `view.viewer` names through the host's render function for it, and
the fallback when there is none (App's "No PDF open."); a viewer a host
does not render is the fallback too. App keeps it inside `div.viewerWrap`,
so a closed PDF stays mounted and is only hidden; PagePane mounts it while
the view is `shown` and draws the notes otherwise, so a closed PDF
unmounts there.

## Fixtures

- `tests/shared/formats.json`: `names` (a file name's format, language and
  stored extension; both sides), `media` (how each stored extension is
  served: media type, inline, sandboxed; the backend alone) and `sniff`
  (first bytes; the backend alone). Read by `test_shared_fixtures.py` and
  `documents.test.mjs`.
- `tests/shared/document.json`: `cases` (properties in, the view out or
  null, for every rule of `check_document`; both sides), `legacy` (each
  state of the table above, before and after: `document_shape`, and
  `test_migrations.py`, which writes them as root, trashed and nested rows
  and runs the step) and `copies` (the doc a duplicate writes:
  `copyDocument`, the client alone).
- `tests/shared/blockkind.json`: a block whose one property is a document
  is a note.

A rule that changes on one side is a case added to the fixture, which
fails the side that drifts ([debugging.md](debugging.md)). Tests make
pages with `backend/tests/conftest.py` `doc_props(hash, *, url, name, at,
copy)`, which builds `{"doc": …}` through `document`; only the migration's
and the restore's tests write the flat keys, on purpose.

## What step 3 adds

A plan, not built ([research/file-explorer.md](../research/file-explorer.md)
"In steps"). The stored shape takes any format; the writers, the readers
and the viewers stop at PDFs.

- **Documents of other formats.** Attaching a file of any format, where
  `format_of(name, head)` and `sniff` get their caller and a document names
  its `ext` (`FORMAT_EXT` knows only the PDF's).
- **Their viewers.** Each is a row of `FORMAT_VIEWER`, a component, and
  `DocumentPane`'s default branch, which draws it with `{doc, url, pageId,
  readOnly}` when the host gives no render function: text in CodeMirror,
  pictures, the browser's players, and a pane for what cannot be shown.
- **The vault's kinds.** `vault_disk.NOTES`, `PAPERS` and `kind_of` sort a
  vault's files by extensions of their own; they become the registry's
  formats with the vault engine's class registry.
- **The tables kept apart until then:** the mirror's `pdf` or `file` upload
  route by extension (`sync_engine` `post_parts` and `_push_files`),
  `FileChip.KINDS` (its words are finer than formats), and the hex-only
  parsers `pdfSource.docIdOf` and `FileChip.parseUploadUrl`.
- **`size`**, written when the work folders' listing reads it, and the
  library flag of step 1, which takes the place of `SQL_LIBRARY_PDF`'s
  test of `at`.
