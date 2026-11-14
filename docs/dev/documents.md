# A page's document

A page may carry one *document*: the file it shows beside its notes, a
paper's PDF, a figure, a LaTeX source, a data file, a recording, a build
("Formats" below). The document is one property of the page's row,
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
predicates), `gamma/documents/text.py` (a document's text, "Text
extraction" below), `gamma/documents/text_anchors.py` (a text file's
highlight ranges placed again after it changes,
[highlight_relocation.md](highlight_relocation.md#text-files)), `gamma/normalize.py` `document_shape` and `page_documents`
(older rows rewritten, migration step 52), `db.BLOCK_HOT_COLUMNS["doc_id"]`
(the generated column), and on the client `frontend/src/documents/`:
`formats.js` and `pageDocument.js`, the twins, `viewers.js` (the viewer
registry), `documentView.js` and `DocumentPane.jsx`, which decide and draw
the viewer a page shows, `viewerSurface.js` and `viewers/`, the panes of
its own ("Viewers" below). Tests:
`backend/tests/test_documents.py`, `test_documents_text.py`,
`test_shared_fixtures.py`, `test_migrations.py` (`test_v52_*`),
`test_restore_consistency.py` (a backup from before step 52),
`frontend/tests/documents.test.mjs`, `libraryUtils.test.mjs`,
`viewerSurface.test.mjs`, `textLoad.test.mjs`, `textMark.test.mjs`,
`textAnchors.test.mjs` and `textMarks.test.mjs`, over the fixtures
`tests/shared/formats.json`, `tests/shared/document.json`,
`tests/shared/textdecode.json` and `tests/shared/textposition.json`
("Fixtures" below).

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
  answer as a 400. `vault_extension(name)` is the same for a work folder's
  file, with a blocked one stored as `.bin` rather than refused
  (`gamma/vault/kinds.py`: the vault takes every file it is given).
- `media_type(ext)`: how a stored file is served; None for a blocked or
  malformed extension, which `/api/uploads/` refuses.
- `format_of(name, head=None)`: `(format, language)` by `NAMES`, then
  `EXTENSIONS`, then the first bytes when `head` is given, else
  `("binary", None)`. `language_of(name, ext)`: a text document's
  language, by `NAMES`, then a text row of `EXTENSIONS`; a Markdown row's
  is `markdown`, for a `.md` read in place as text (an opened file outside
  every vault), though `format_of` still answers `("markdown", None)` for
  the name, which is what makes a Markdown file a note everywhere else.
- `stored_format(name, ext, head)`: the format of a file already stored
  under `ext` with the shown name `name`, which an attach, a by-file and
  the vault's file class settle on the server: the extension's row unless
  it is `.bin` (a name without one, a blocked one) or unknown; else the
  whole name (`Makefile` stored as `.bin`); else the name's own
  extension's row (an upload stored under its media type's extension,
  `shot` as `.png`, is an image whatever its name says); else
  `sniff(head)`; else `("binary", None)`. A sniffed `pdf` under another
  extension (`2301.12345v2`) is the caller's to settle: the attach stores
  a `.pdf` copy and attaches a PDF, the vault reads it as binary (a PDF is
  a PDF by its name there).
- `sniff(head)`: a name the table does not know, settled by its first
  8 KiB. `%PDF-` at the start is `pdf`; a NUL byte or bytes that are no
  UTF-8 are `binary` (a multi-byte character cut by the limit is still
  UTF-8); anything else, nothing included, is plain text. `stored_format`
  is its caller.

`formats.py` imports nothing from Gamma. The client's `documents/formats.js`
is its twin for names only, since the client never serves a file or sees
its bytes: `FORMATS`, `EXTENSION_RE`, `BLOCKED_EXTENSIONS`, `EXTENSIONS`
(format and language), `IMAGE_MEDIA_TYPES` (the image rows' media types,
which the file chip sends to `/upload-image`), `NAMES`, `extensionOf`,
`storedExtension` (null for a blocked extension), `formatOf` and
`languageOf`. The file chip (`transfers/FileChip.jsx`) and the uploads'
routing (`transfers/fileRouting.js` `routeFile` and `isPdfFile`, which
`shared/lib/utils.js` re-exports and `transfers/openedFiles.js` follows) ask
it what a name is.

## The stored shape

```text
doc: {format, name, hash, ext, at, url?, size?, copy?}
```

| Field | Holds |
|---|---|
| `format` | One of `FORMATS`: stored, never read again from the name. |
| `name` | The file's shown name, a display leaf (`storage.display_filename`, at most 500 characters), or `""` on a page that never had one: a clip, a page opened from an address, a Zotero or zip import. |
| `hash` | The stem the file is, or would be, stored under in the workspace's uploads: the content hash (`sha256[:24]`) of an upload or of a file read in place; the hash of the address (`url_hash`) for a PDF opened or clipped from an address, whether or not a copy was cached; the name a stored PDF kept when its embedded annotations were stripped. The shape `db.safe_doc_id` takes. `""` only at `url`. |
| `ext` | The stored file's extension, lowercase with the dot (`EXTENSION_RE`); `.pdf` for a PDF. The stored name is `hash + ext` (`stored_name`). |
| `at` | Where the bytes are read from: `store`, the workspace's uploads; `path`, a file on this computer through a `vaults.db` `linked_files` row ([synced_vaults.md](synced_vaults.md#a-file-read-where-it-is)), with a stored copy perhaps; `url`, an address the PDF proxy fetches, with a copy cached under `hash` perhaps; `listed`, nowhere ("The listed state" below). |
| `url` | Only at `url`, never empty: the address the document is shown from. Never its own stored address (that document is at `store`); it may be another upload's, on a page the Zotero merge gave a PDF of its own while it showed a duplicated one's. |
| `size` | The file's bytes, a whole number. Written by every attach and replace (`storage.size`), and by a work folder's vault on every document it makes, which the work folder's list view shows and sorts by ([work_folders.md](work_folders.md#the-stamps)); required at `listed`. |
| `copy` | `true` or absent, only at `store`: the page shows a stored file it does not own, a duplicated page or one attached by the bare address of an upload. |

No other key, and every writer stores the fields in that order, the
rewrite of older rows included, so two copies of a page are byte-equal.
A stored doc holds to these rules, and `document` refuses anything else:

1. At `store` or `path` there is a hash and no address.
2. At `url` there is an address; the hash is the address's or `""`.
3. Only a document at `store` is a copy.
4. A PDF's extension is `.pdf`.
5. At `listed` there is no hash and no address, there is a `size`, and the
   format is neither `pdf` nor `markdown`.

### The listed state

A work folder's file past the keep limit (`config.vault_keep_bytes`,
`GAMMA_VAULT_KEEP_MB`, 64 unless set, read when asked) is listed without its
bytes: the vault's file class hashes nothing and serves nothing, and the
page's document is

```text
doc: {format, name, hash: "", ext, at: "listed", size}
```

The limit is the file class's alone ([work_folders.md](work_folders.md)):
a PDF or a Markdown file is never listed, however large (scanned books are
read in place as before), so `document` refuses a listed one of either
format. Its view has `url: ""` and `file: ""`, `own_hash` and `file_hash`
are `""`, `in_place` is false and `is_listed(view)` true, so no lookup of
"the page of this file", no AI read and no store touches it, while
`library.follows` (a doc that is not None) keeps the page following the
disk. The page's pane says so ("Which viewer a page shows"). A duplicate
of such a page writes no document (`copy_document` raises; the client's
`copyDocument` answers null), and nothing holds a file for it. An older
server's op path refuses the shape (`document_props` is a 400), so the
step that brought it, 55, moves the workspace format
([migrations.md](migrations.md)). Twin: `pageDocument.js` `isListed`;
`tests/shared/document.json` holds the listed cases.

Two more hold across the shape's history. The hash of every page that had
a doc id before step 52 is that doc id, so every record keyed by a file's
hash stays valid untouched: `data.db`'s `pdf_docs`, `text_fts*` and
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
- The five rules hold; `name` is reduced to its display leaf.

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
  /api/pages/{id}/attachment {doc_id, source_url, original_filename}` for a
  PDF, `{file, original_filename}` for a stored file of any format (`file`
  its stored name, `<hash><ext>`, alone), `POST /api/blocks/by-doc/{doc_id}`
  (a PDF), `POST /api/pages/by-file {file, name, folder}` (any format), the
  clip. Each becomes a doc once, through `document`; an id it refuses is a
  400. On a page read in place, `PUT /attachment` without an address keeps
  `at: "path"`.
- **The format is the server's** (`routers/pages.StoredFile`): a `file` is
  read for what it is with `formats.stored_format(original_filename, ext,
  head)` over the shown name, the stored extension and the first
  `SNIFF_BYTES` of the stored file; `markdown` is refused (400: a note is
  made from it, `POST /pages/from-file`); bytes that sniff as a PDF under
  another extension (an arXiv download named by its id, stored as
  `<hash>.12345v2`) are stored again as `<hash>.pdf` and attached as that
  PDF. A `source_url` that is a stored file's address of another extension
  than `.pdf` is a 400 (`NAME_THE_FILE`), never a PDF shown from there.
  `size` is `storage.size` of the stored file on every attach and replace;
  a PDF's title is automatic (`auto_title`), any other format's is the
  name alone, since no record ever retitles it.
- **A replacement keeps the page's format**: `PUT /attachment` with a
  file whose settled format differs from the page's document's is a 400
  ("This page's document is {format}; detach it first"); a page with none
  takes any, as `POST` would give it. `replace_attachment(..., format, ext,
  size, keep_format)` is the one path (the route passes `keep_format` for
  every writer but a synced vault's client); a vault's file class, here
  or over HTTP, may change the format (a file
  whose name says nothing and whose bytes sniff otherwise). The highlight
  stamps and the `relocate-highlights` run happen only when the outgoing
  and the incoming documents are both PDFs; a picture replaced by a
  picture writes no stamp on its regions, which read as drawn on another
  version by their own `hash` ([highlight_relocation.md](highlight_relocation.md));
  a text file replaced by a text file stamps nothing either, and its
  ranges are placed again by their quotes, in the replacement's request
  up to 512 KiB, else by the job
  ([highlight_relocation.md](highlight_relocation.md#text-files)).
- **One library page per stored file**, for every format, keyed by hash
  and extension (`blocks_store.page_for_file(conn, hash, ext)`: the same
  bytes uploaded as `a.txt` and `a.log` are two stored files and two
  pages; a `.pdf` is `page_for_doc`'s, `SQL_LIBRARY_PDF`). It serves the
  attach 409 (`attachment belongs to another page`, with the `page_id`),
  Add to library's 409 and the op path's library check
  (`ops.work_patch`), the duplicate rule (`second_owner_as_copy`), the
  by-file lookup and a backup merge's match (`ws_backup`: `live_docs` by
  stored name). The record lookup, Zotero and the clipper stay library
  PDFs (`SQL_LIBRARY_PDF`, `is_library_pdf`). `POST /api/pages/by-file`
  is the lookup-or-create by stored file (`get_or_create_doc_page(...,
  format, ext, size)`: no `auto_title` and no manifest for a non-PDF; a
  `.pdf`, and a sniffed PDF, go the by-doc way) → `{page, created}`; `POST
  /api/pages/by-docs {files: ["<hash><ext>", …]}` answers `files: {name:
  {id, title}}` beside `pages` (`blocks_store.pages_for_files`: a `.md`
  name finds its note by `markdown_import`), which the file chips ask by
  stored name.
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
| is the page a library document, a library PDF | `work_folders.library.library_document(props)`, `is_library_pdf(props)` | the library rule, over the page's properties: a work folder's file only when added to the library, any other page unless read in place ([work_folders.md](work_folders.md#the-library-rule)) |

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
highlight relocation, translation memory, the text index's live set
(`gamma/search/index.py` `purge_unused`: a copy keeps the rows of the file
it shows), the
Zotero link's comparison of files, a folder chat's PDF search
(`ai_tools._scope_pages`), and on the client `pdfStored`, the viewer's `fileId`, the translation scope, the
annotated PDF's translation (`translateExport.exportFile`,
`folderTranslation.folderPapers`) and the chat's PDF ids.

**Read in place** (`in_place`) is asked as itself only where a page read
in place is set apart for its file: `linked_files.open_file` and its carry,
and the client's `duplicable`, `isOpenedFile` and `pdfLinked`. The paper
readers ask the library rule (`library_document`, `is_library_pdf`, and
`SQL_LIBRARY_PDF` in the statements;
[work_folders.md](work_folders.md#the-library-rule)), which keeps this
test of `at` for the pages outside a work folder, so an Opened files PDF
is none of theirs: the one-page-per-document lookups, `clip.find_page`,
`ws_backup`'s review and merge, the metadata status and batch, the Zotero
preview's match and the record lookup on open. Every other PDF reader
takes a PDF stored or read in place alike (`is_pdf`, `own_hash`): a
lookup asked by hand (`POST /metadata/fetch`, the Paper metadata button),
`findPageForUrl`, the AI, search and the Zotero link.

## Text extraction

`gamma/documents/text.py` reads a document's text by its format. It is
the one reader the search index reads every document through, and what
the chat reads a text document with; it imports `textnorm` and, inside
`extract`, `pdf_text` (a server without PyMuPDF still reads text files),
and nothing of the routers, the index or the AI.

| Function | What it answers |
|---|---|
| `extract(path, format, *, name)` | the index's units (`Unit(no, text)`, normalized as the index stores them) with `lines`, `chars`, `truncated`, `encoding`, `bad`. A PDF's units are its pages (`pdf_text.extract_pages`, past the chat's page cache), each cut at `PAGE_CHARS` (20,000), empty pages dropped; ImportError and every other failure raise, for the indexer to decide what to record. A text file's are runs of `RUN_LINES` (40) lines, each line `normalize_text`'d and cut at `LINE_CHARS` (500), so `RUN_LINES * LINE_CHARS == PAGE_CHARS` and a run and a page rank as units of one size; a run of empty lines is dropped but keeps its number. Any other format is a ValueError: callers test `INDEXED_FORMATS` (`pdf`, `text`) |
| `unit_lines(unit, lines)` | the first and last line of a run: `((unit-1)*40+1, min(unit*40, lines))`. The index stores no line columns; a change to `RUN_LINES` bumps `textnorm.INDEX_VERSION` |
| `read_lines(path, from_line, to_line, *, budget)` | a `Window(lines, last, total, decoded)`: the lines raw, each cut at `VIEW_LINE_CHARS` (10,000, the viewer's) with `…[line cut]`, read 64 KiB at a time until `to_line` or until their characters reach `budget`; the rest of the file is then counted from its bytes, never decoded (a 1 MiB read's `\n` and `\r`, a `\r\n` once), so a 64 MB log read for its first lines decodes a few hundred KB and counts the rest in ~40 ms. `from_line` below 1 reads from 1; past the end gives no lines and the right `total`. A NUL in what was decoded, or too many U+FFFD, is `ValueError("not a text file")` |
| `outline(lines, language, *, from_line, budget, cut)` | `[(line, text)]` of where things are, from `from_line` on, each text at most 80 characters, within `budget` (an entry costs its text and 10), sampled every k-th when the rule finds more: LaTeX's sectioning commands (with a `\label` on the line or the next), `abstract`, figures and tables with their first caption, `\input`, `\include` and `\bibliography` as written; the headings of Markdown, reStructuredText and Typst; Python's classes and functions, nested by indentation; JavaScript's and TypeScript's functions, classes, exports, top-level function or object constants and tests; the C family's definitions with their imports folded into one entry; JSON's top-level keys, an array's length (shown whatever `from_line`), a notebook's cells; YAML's, TOML's and INI's top-level keys and tables; BibTeX's entries with their titles; a table's header and its count of rows (shown whatever `from_line`); anything else by its paragraphs. A rule that finds fewer than three entries is filled in by the paragraphs, in the budget its own entries leave (they are never sampled away). A file under 40 lines has none; lines that are only the file's first (`cut`: the read stopped at `MAX_TEXT_LINES` or `MAX_TEXT_CHARS`) end it with "(the outline stops at line N)", N the last line read (`outline_stop`). The regexes are anchored and linear, so a crafted file does not hold the thread |
| `locate(unit_text, query, first_line)` | the line of a run a search hit matched, and its text cut to 200 characters around the match, by FTS5's token model: the normalized query's runs of letters and digits, each a whole word on the line, the last a prefix, case and diacritics aside (`section` finds `\section`, never `\subsection`; `fig:loss` finds `\ref{fig: loss}`); the line holding the most wins, the earliest of equals; none (a phrase across a line break) gives the run's first non-empty line |
| `numbered(lines, from_line)` | the lines as the chat sends them, `cat -n`'s shape: the number right-aligned in six columns, a tab, the line |

**The caps.** A text file is indexed, outlined and counted up to
`MAX_TEXT_LINES` (50,000) lines or `MAX_TEXT_CHARS` (2,000,000) decoded
characters, whichever comes first; `extract` reads at most
`MAX_TEXT_CHARS * 4 + 3` bytes (enough for the cap in UTF-8), so a 3 GB
log is never read whole, and a file cut there ends at its last whole line
with `truncated` set. A single long line is cut, not truncated: a
minified file is one unit of `LINE_CHARS`.

**The encodings** (`decode`): a byte-order mark picks UTF-8 (`utf-8-sig`),
UTF-16LE (what Windows PowerShell 5.1 writes) or UTF-16BE, the mark being no
text; anything else is UTF-8 with each bad sequence a U+FFFD, `bad` when one
was replaced. `binary` when the decoded text holds a NUL anywhere, or U+FFFD
past `BAD_SHARE` (5%) of its characters; its text is then `""`. There is
no code-page guess. The viewer's `textLoad.decodeText` applies the same
rule to a whole file, and `tests/shared/textdecode.json` holds both.

**Secrets.** `is_secret(name)` is the one exception to what leaves the
computer for the model: a file whose leaf, lowercased, is one of
`SECRET_NAMES` (`.env`, `.netrc`, `.npmrc`, `.pypirc`, `.htpasswd`,
`id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`), starts with `.env.` or ends
with `.pem`, `.key`, `.p12` or `.pfx` is never indexed (it is recorded with
no text), never put in a chat's context and never read by a tool, nor is
the quote of a range or a selection of its lines: the chat, `read_page`,
`read_block`, a thread's place and the feedback entries name its lines
alone (`feedback.anchors.text_kept`). The
index is keyed by the bytes and the rule by the name, so a search applies
it again to every page a hit reaches (`search.hits.docs_of` leaves a
secrets file's page out of the scope, `hits.build` gives it no hit): a file
indexed as `settings.txt` and renamed `.env`, or a copy named `.env`, is
never a hit, and a `.env` never indexed is indexed once renamed. The
viewer still shows it on the person's own screen. `formats.NAMES` still
reads `.env` as `text` in `shell`: what a file is and what the model may
see are two questions.

**Where the PDF readers stay.** The index reads PDFs through `extract`.
The chat's PDF head and windows (`ai_context.pdf_excerpt`, through
`pdf_text.extract_text_pages`, cached per page, with `[PDF page N]`
labels), the selection placement, `GET /pdf-text-status`, the translation
briefs, metadata's title guess, `fetch_paper`'s bytes, glyph boxes and
rasters keep `pdf_text`: they need page labels and a cache the index must
not share, since a walk over a library would evict the papers a chat is
reading.

## In SQL

`model.py` holds the conditions that are spliced into `WHERE` clauses over
a `unified_blocks` row:

- `SQL_PDF`: its document is a PDF (`json_extract(properties,
  '$.doc.format') = 'pdf'`).
- `SQL_IN_PLACE`: it is read in place (`$.doc.at` is `path`): the linked
  files' idle sweep (`linked_files._IDLE`), and the Gamma export, which
  carries the bytes and rewrites its copy's documents to `at: "store"`.

A PDF that counts for the library's one-page-per-document lookups is the
library rule's `work_folders.library.SQL_LIBRARY_PDF`, over the generated
`work_folder` column: a work folder's file counts only once added to the
library, and the `at` test is asked only of the pages outside a work
folder ([work_folders.md](work_folders.md#the-library-rule)). It goes with
the `doc_id` column, which leaves copies out: `page_for_doc`,
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
the text index purge) read it as they did. The share checks
(`routers/uploads.py`) read `doc.hash` and `doc.ext` instead, of any
document, copies and a URL's cached copy included: a page's own, and the
copy a block inside it shows. `root_pages`
answers the column for a document of any format, with its `format`,
`ext` and `at`, for the search to keep the formats it indexes
(`text.INDEXED_FORMATS`). The column reads `$.doc.hash` raw, so a malformed
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
desktop vault on another server (`vault.http_remote.HttpRemote`) refuses a feed of
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
`isPdf`, `pdfFileHash`, `inPlace`, `isListed` and `sourceAddress`. Beside
them:

- `pdfProxyUrl(url, {save, share})` and `pdfViewerUrl(src, {share})`: the
  address the viewer loads a PDF from, a stored `/api/` address as it is
  (with a share's token), anything else through the proxy.
  `documentViewerUrl(doc, {share})` is that for a page's PDF document, `""`
  for none. The rule lives here alone: `shared/lib/utils.js` imports
  `pdfProxyUrl` (`probePdfUrl`), and `BlockProperties.jsx` calls
  `pdfViewerUrl`.
- `documentFileUrl(doc, {share, asset})`: where any other viewer reads a
  document's bytes, and what every `<img>`, `<audio>` and `<video>` src and
  every `fetch` of a document goes through: a stored file's or an in-place
  file's upload address through `asset`, the host's `assetUrl`
  (`shared/lib/utils.js`, which adds the workspace and this view's share
  token; this module is pure and cannot import it, so App and PagePane
  pass it), with `share` carried when given; a PDF at an address through
  the proxy; `""` for a listed file and for any other document at an
  address, which has no file here. Never navigated to and never framed: an
  SVG is only ever an `<img>`.
- `copyDocument(doc)`: the stored doc a duplicate writes (App's
  `duplicatePage`). A stored file is shown as a copy of it, an address is
  kept without the original's URL hash (an upload's address making it a
  copy of that upload), and a file read in place is shown from its stored
  copy, which `duplicable` never offers; null for no document.

The library's rows (`library/libraryUtils.js` `pageBlocksOf`) carry
`_doc`, the page's view. `duplicable` leaves out pages read in place,
`pageKindLabel(doc)` says the format's word (`formatLabel`: "PDF",
"LaTeX", "Picture", "File"; a language's word is `LANGUAGE_LABELS`', the
server's `formats.LANGUAGE_NAMES` too, which the chat's context names it
by, both held to `tests/shared/formats.json` "language_names") or "Page", `defaultPageTitle(doc)` titles a
page from its document's name or address, and `findPageForUrl` matches a
link against `sourceAddress` (a copy's upload address holds no DOI or
arXiv id). `FileGlyph` (`shared/ui/Icons.jsx`) takes a `format` and draws
a glyph per format (the PDF's stamp, a text's brackets, a picture's
mountain, audio's note, video's play triangle, a plain sheet for a binary,
a note's lines), tinted by format in `library/library.css`; the cards, the
pickers' rows, the open tabs (App's `tabKinds`) and the page family's
`sub_pages[]` hand it theirs. App
holds the open page's `doc` (`pageDocument(focusedBlock)`) and `docId`
(`ownHash(doc)`); the chat (`chat/ChatDock.jsx`) takes `doc` and reads its
PDF by `fileHash`.

### Which viewer a page shows

`documents/viewers.js` is the viewer registry. `FORMAT_VIEWER` is the
document pane a format gets: `pdf` → `pdf`, `text` → `text`, `image` →
`image`, `audio` and `video` → `media`, `binary` → `file`; a Markdown note
has none, since its notes are the file. A Markdown file opened outside
every vault is not a note: it is a text document (`format: "text"`,
`ext: ".md"`, read in place), so it gets the code view, coloured as
Markdown (`language_of` answers `markdown`, `codeLanguages.js` maps it to
language-data's `Markdown`). No document of format `markdown` is ever
stored. `viewerOf(format, doc)` reads it,
with `"file"` for a listed document whatever its format ("The listed
state"). `VIEWERS` is one descriptor per pane, the notebook's included,
and what each pane brings to the frame around it, which App reads from
the descriptor and never from the pane's id:

| Field | Meaning |
|---|---|
| `tools` | the strip tools that apply (`app/viewerToolItems.js` ids), before App's own conditions: the PDF's `zoom, ink, record, highlighter, translate, selectMode`, the notebook's `zoom, ink, record, paper, notesView`, the picture's `zoom, selectMode` (the region toggle by finger), the code view's `highlighter` (the text highlighters, [settings.md](settings.md#text-highlighters)), none for media and the file pane |
| `host` | the host renders it (DocumentPane's `render`: the PDF and the notebook, which take the host's state); every other pane DocumentPane draws from the surface |
| `ink` | the markup strip, Record and the player hang on it (the PDF and the notebook) |
| `regions` | it takes regions (`image_position`, [api.md](api.md) "The highlight shape"; the picture) |
| `margin` | its threads stand in the comment margin, which App fetches as the page starts to open (the PDF, the picture and the code view) |
| `word`, `close` | its name in the View menu and the phone tab, and the × button's title (`T()` keys; "PDF" stays the untranslated format token) |

`documents/documentView.js` is one rule for the open page (App) and a tab
group's page (`workspace/PagePane.jsx`, [tab_groups.md](tab_groups.md)):
`documentView({doc, url, sheets, notebook, hidden, open, dock})` answers

| Field | Meaning |
|---|---|
| `viewer` | what mounts: the PDF pane once it has its address (`url`, App's `pdfUrl`), any other pane from the document alone, else `"notebook"` while the notebook view shows, else null; a `url` alone is a PDF |
| `spec` | the viewer's descriptor (`VIEWERS[viewer]`), or null |
| `notebook` | the notebook view shows ([notebooks.md](notebooks.md)) |
| `notebookAvailable` | the page has sheets and no document pane |
| `hidden` | the pane is closed (`pdfHidden`), never while the notebook shows |
| `shown` | a viewer mounts and shows; never in a docked Notes view |
| `center` | the notes take the middle: in a docked Notes view, with the pane closed, or on an open page with neither a pane nor its notebook view (App adds its home view) |

App names its answer `docView` (`view` is the notes view's state there)
and reads its gates from it and from `spec`, never from the pane's id: the
× button's title is `spec.close`, the View menu's window row and the
phone's tab say `spec.word` ("PDF" until a PDF has its address), the strip
offers the tools `spec.tools` names under App's own conditions (a touch
screen, a read-only page; the picture's `selectMode` is the region toggle
by finger, "Draw a region with a finger") and is drawn only while it has
one, the ink strip, Record and the player hang on `spec.ink`
(`viewerInk`), the zoom window and the translation stay the PDF's.
`documents/DocumentPane.jsx` draws the viewer `view.viewer` names through
the host's render function for it (the PDF and the notebook, which take
App's state: `spec.host`), else its own lazy viewer for the pane with the
surface the host gives ("Viewers" below), else the fallback (App's "No PDF
open."). App keeps it inside `div.viewerWrap`, so a closed pane stays
mounted and is only hidden, and keys it by the page while a pane of
DocumentPane's own shows, so a text or picture page opens at its own
place; PagePane mounts it while the view is `shown` and draws the notes
otherwise, so a closed pane unmounts there. Opening a page
(`openBlock`): the pane its document gets is preloaded with the comment
margin (a PDF's, a picture's or a text file's) as the open starts; a page with a pane
seeds no empty note and starts no editor; a file page with nothing
written yet, and no arrangement of its own, opens with the Notes window
collapsed to its bar ("Click to start writing" in the tail once it
opens); only a PDF sets `pdfUrl`, and `docId` is a PDF's hash or "" (its
readers mean a PDF). `documents/codeLanguages.js`
`codeLanguageName(doc)` is the text viewer's language: the registry's
language word (the view's, else the name's own extension's) mapped to a
`@codemirror/language-data` description name (`latex` → LaTeX, `matlab` →
Octave, `ini` → Properties files, …), never language-data's own file-name
matching, which disagrees with the registry; plain text, CSV, BibTeX,
Typst, reStructuredText and a Makefile are shown plain. It picks the
grammar alone: the view's bar names the language by `formatLabel`, the
Format column's word (MATLAB, not Octave). Tests:
`tests/documents.test.mjs` and `tests/viewers.test.mjs`.

### Viewers

The panes of DocumentPane's own (`documents/viewers/`), two lazy chunks:
`TextViewer.jsx` (the CodeMirror packages come in with it alone) and
`FileViews.jsx` (`ImageViewer`, `MediaViewer`, `FilePane`). How each
looks and behaves is [ui-design.md](ui-design.md) ("The code view", "The
image view"); what they are:

| Pane | Shows | Takes from the surface |
|---|---|---|
| `text` | the file's text read-only in CodeMirror, coloured by `codeLanguageName`, with line numbers, Find (CodeMirror's search, its panel the app's find row at the top, `FindPanel.jsx`; Ctrl+F in `.docTextView` is left to it by `app.search`), Wrap and Download in its bar, Ctrl+wheel for the text size (`useTextScale`, as the notes and the chat; no A−/A+ buttons, since none of them has any); a binary after all, a file the server refuses (404: a work folder's file on another computer), or no `fileUrl`, hands over to `FilePane`; a line link's lines marked (below); the page's ranges of the file drawn over its text, a selection made into one or sent to the chat, and their comment cards in a column beside the text or their badges ("A range of a text file", below) | `doc`, `fileUrl`, `phone`, `hidden`, `readOnly`, `preview`, `place {line}`, `onPlace`, `reportRef`, `mark`, `onMarkDone`, `onReveal`, `regions`, `anchors`, `flashId`, `onRegionJump`, `onRegionContext`, `onRegionThread`, `commentMargin`, `marginRef`, `onMarginLost`, `scrollRef`, `onAnchor`, `onChatSelection`, `onHighlightAction` |
| `image` | the picture at fit or at App's zoom, its regions drawn as the PDF draws areas (dashed with "Drawn on an earlier version of this picture" when `image_position.hash` is not the file's), the comment margin beside it or a badge per commented region, the region drag and its tip (Comment, Note, Ask AI); a picture that will not load, or no `fileUrl`, hands over to `FilePane`, and the next version of the file mounts a picture again | `doc`, `fileUrl`, `readOnly`, `hidden`, `scale`, `place {top, left, scale}`, `onPlace`, `reportRef`, `regions`, `anchors`, `flashId`, `regionMode`, `onRegion`, `onRegionJump`, `onRegionContext`, `onRegionThread`, `commentMargin`, `marginRef`, `onMarginLost`, `scrollRef`, `captureRef`, `onAskRegion`, `onReveal` |
| `media` | `<audio>` or `<video>` with the browser's controls (a `.webm` a video until its metadata says it has no picture), paused when the pane closes | `doc`, `fileUrl`, `hidden`, `onReveal` |
| `file` | the format's glyph, the name, the format and size (`doc.size`, else one HEAD's `Content-Length`), one sentence (a binary, a listed file, a document at an address, whose `fileUrl` is `""`, a file whose bytes are on another computer, a file that would not play or was binary after all), Download (not for a listed file, nor one with no `fileUrl`, nor one the HEAD answered 404; no HEAD is sent for those) and Show in folder where `onReveal` is given | `doc`, `fileUrl`, `onReveal`, `sentence` (the other panes' hand-over) |

**The surface** is one object, App's built by
`documents/viewerSurface.js` `useViewerSurface(state, handlers)` (memoised
on what it shows; the handlers reach App's latest through stable
closures, null where App gives none, so a viewer leaves that control
out), PagePane's read-only (`{doc, fileUrl, pageId, readOnly, preview,
place, onPlace, reportRef, regions, scale}`):

| Field | App passes |
|---|---|
| `doc`, `fileUrl`, `pageId` | the page's document, `documentFileUrl(doc, {asset: assetUrl})`, the page |
| `readOnly`, `hidden`, `phone`, `preview` | the workspace's, `pdfHidden`, `isPhone`; `preview` a tab group's pane (presses only) |
| `scale: {value, onZoomTo, onEffectiveScale}` | `pdfScale`, `zoomTo`, `setPdfEffScale`: the picture's zoom is the PDF's state, so the strip's buttons and the pill work unchanged |
| `place`, `onPlace`, `reportRef` | where the page's pane last stood (`tabScrollRef`, the code view's `{line}`, the picture's `{top, left, scale}`), read once as the pane mounts; `onPlace` writes it back, and `captureScrollPos` calls `reportRef` before leaving a pane that is not the PDF's, as `navEntry` does for Back (below). A closed pane (`hidden`: still mounted, `display: none`, which drops the scroll) reports where it last stood in view, keeps its box and so its fit, and is put back there as it opens |
| `regions`, `anchors`, `flashId`, `regionMode` | `highlights` and `anchors`, which are the document's (`pageHighlights` / `commentAnchors` with `positionKeyOf(doc)` and `fileHash(doc)`: a PDF's highlights, a picture's regions or a text file's ranges, so the jumps, the margin, the chat and the comment cleanup take a region or a range with no branch); `flashingId`; `areaSelectMode` off a share |
| `onRegion(position, {color, comment})` | `addRegion`: `addHighlight({quote: "", position, color}, {place: "image_position"})`, then `commentOnNewHighlight` for a comment (its abandoned-comment cleanup is the passage's); null when read-only |
| `onRegionJump`, `onRegionContext`, `onRegionThread` | the PDF's highlight handlers (`highlightPressed`, the highlight menu, `openThread` with the margin) |
| `commentMargin`, `marginRef`, `onMarginLost`, `scrollRef`, `captureRef` | the PDF's `commentMargin`, `pdfMarginRef`, `marginLost`, `scrollToRef`, `pdfCaptureRef`: one viewer mounts at a time, so one set of refs serves both |
| `onAskRegion(position)` | the chat's picture of the region (`regionPicture("area", pageId, 1, regionBox(position))`); null on a share |
| `onAnchor(position, quote, extras)` | the code view's selection made into a range: `finishSelection` with the `text_position` key (the PDF's `onSelectionFinished` body: a highlight, a comment's, the highlighter's, or a move's new place), returning the new range's id; null when read-only |
| `onHighlightAction(id, action, value)` | `madeHighlightAction`, the PDF's bar over a highlight just made, for the bar over a range the highlighter made; null when read-only |
| `onChatSelection(text, additive, {line, end, file})` | `addPdfSelection`: a selection of the code view for the chat, its lines labelled; `""` clears the set as a plain click in the PDF does |
| `onReveal` | the desktop shell's `reveal-on-disk` for a work folder's file; null elsewhere |
| `mark`, `onMarkDone` | `pageTarget` when it is a line link's (`{kind: "line", pageId, line, to, quote}`) on the open page, else null; `onMarkDone()` clears `pageTarget`, `onMarkDone(mark)` only while it is `mark` |

**The text reader** (`viewers/textLoad.js`): one Range request for the
first 2 MiB (a 206's `Content-Range` gives the size; a server that ignores
the range answers 200, and as much of it is read, the rest cancelled);
the bytes decoded by their byte-order mark (UTF-8, UTF-16LE as Windows
PowerShell writes, UTF-16BE), else as UTF-8 with replacement, the view
then saying "Some bytes are not UTF-8 and show as �."; a head drops its
partial last line (a head with no line break at all, a minified file, is
kept as its one line) and every line is cut at 10,000 characters; a NUL
anywhere in what was decoded, or U+FFFD past 5% of its characters, means
binary (`notText`, the server's `text.decode` rule, pinned for both by
`tests/shared/textdecode.json`), and the pane hands over to `FilePane`'s
sentence; Show all reads the rest up to 16 MiB, past which (by the size
the response says, else once that many bytes came) the bar says to
download, the body left unread. One read at a time: a new version of the
file (a new `fileUrl`) cancels a Show all under way and reloads at the
same top line.

**The line mark.** A line link (`/?page=<id>&line=N[-M][&quote=…]`,
[pdf_citations.md](pdf_citations.md) "Line links": the chat's pill, a
note's, the search panel's Files hit, a pasted address on a cold load or
a share's) sets App's `pageTarget` to `{kind: "line", pageId, line, to,
quote}`; the PDF's passage is the same state with `kind: "citation"`, each
viewer taking its own kind. Once the file's text is in and the pane shows,
`viewers/textMark.js` `placeMark(lines, target)` says where: line N, unless
the quote (the first line's verbatim text) is not that line's trimmed text,
when the nearest line whose trimmed text is the quote, else whose text
with its spaces folded is it, else which holds it, is marked instead
(`moved`), the range as long as it was; a quote no line holds marks the
number (`lost`). A line is scrolled to the middle once (a range, its first
line to the top) and the lines are drawn with a line decoration (`.docLineMark`, a wrapped line whole), and the notice
`.docMarkNotice` (a polite live region) says "Line N marked." to a screen
reader alone, and on screen "The line moved: marked where its text is
now." or "Its text is not in the file now: marked by its number.". A line
or a quote past the 2 MiB head reads the whole file first (Show all's
read), and only then is a line past the end: nothing is marked and the
notice says "The file has N lines now; line L is past its end." (past
the 16 MiB cap, "Line L is past the part of the file shown here.
Download it to read it all."). A new version of the file (read whole
again for a mark past its head), or the whole file read for the mark,
places it again without scrolling; pressing Show all ends it like any
press outside the marked lines. Escape, or a press anywhere but on the
marked lines, ends it and calls `onMarkDone`. A binary or listed document
shows `FilePane`, marks nothing and ends the mark; the view going (the
reader left the page by Quick open, Back or a tab) ends the mark it placed
(`onMarkDone(mark)`, which clears `pageTarget` only while it is that
mark), and `openBlock` drops a target of another page than the one it
opens, so a page opened again later opens at its own place. A tab group's
pane (`PagePane`'s read-only surface) takes no mark: only the live page
marks a line.

**A range of a text file.** A highlight of a text file is a block with
`text_position` ([api.md](api.md) "The highlight shape": its lines and
columns in code points, the text around it, the version it was measured
on). In the code view:

- *A selection* is read off the DOM selection's two ends
  (`view.posAtDOM`, which does not depend on CodeMirror tracking a
  finger's selection into its state) and made a span by
  `viewers/textAnchors.js` `spanOf`, the one source of a span; a span
  touching a line the view cut at 10,000 characters (`textLoad.js`
  `cutLinesOf`: its "…" is not the file's) is none. `viewers/TextTip.jsx`
  opens the PDF's popup for it, cut to the marks (`TipMarks`: swatches,
  highlighters, the fold, keys 1–9), Copy where the host's own edit menu
  is off (the iPad's, turned off while the code's text is selected, as
  over the PDF's), and Comment on this passage; a press makes the range
  (`onAnchor`). A mouse selection's popup opens where the button was
  released once the hand moves on (`pdf/selectionTip.js`), a finger's
  once its selection is still for 350 ms (`selectionchange`; touch sends
  no mouseup), and a scroll or a press elsewhere closes it. With a text
  highlighter armed a release of the mouse or the pen makes the range at
  once, the selection tinted its colour meanwhile, and the bar over it
  follows where the host gives `onHighlightAction`. The same release (a
  finger's selection, settled) tells the chat, with its lines and the
  file's name (`onChatSelection`; Ctrl or ⌘ adds), and a plain click in
  the text clears the chat's passages, except a click on a range, which
  adds its quote instead. Read-only, the ranges draw and no popup opens;
  a tab group's pane (`preview`) draws the ranges alone. The popup's frame
  and tools are the PDF's (`shared/ui/TipFrame.jsx`,
  `shared/ui/SelectionTip.jsx`), so the chunk imports nothing of pdf.js.
  No Link or Translate.
- *The marks* (`viewers/textMarks.js`): one state field of decorations,
  fed the page's ranges (`regions`), the active card's and the flashing
  one's ids and the file's stem, and rebuilt from them whenever the text
  changes. A placed range is a mark over its text (`markSpec`: a wash, or
  its line style, drawn by the PDF's rule and winning over the commented
  look, the active card's laying a faint wash under it; to check dashed,
  one measured on another version and not placed again yet faint, a
  commented one stronger with a line at its
  foot, a link's line, the active card's strong, a jump's flash); a lost
  one, whose old lines now hold other text, is never a mark: a dashed tag
  at the start of its old first line (the file's last when it is past
  the end), its quote as the title; every range, placed or lost, puts the
  line cue (a bar in its colour's line tone, dashed for a lost one) on
  each of its lines, so a file can be scanned for its commented lines. A
  click on a mark or a tag jumps to the range (`onRegionJump`: the quote
  to the chat, the row in the notes) and makes its card the active one; a
  right-click or a held finger opens its menu (`menuPress`).
- *The layers beside the text* (`viewers/TextOverlay.jsx`), two because the
  scroller scrolls both ways when lines do not wrap. The badges
  (`.docTextOverlay`, inside CodeMirror's scroller over the content's
  box) ride with the text: a `NoteBadge` at the end of a range's last
  line, a lost one's beside its tag, for a note or for threads no card
  shows. The comment column (`.docTextMarginHost`, beside the scroller)
  stands at the view's right edge however far the text scrolls sideways,
  its track moved with the vertical scroll alone, the cards level with
  their ranges' first lines (`CommentMarginSlot`'s `topOf`); while it
  stands the scroller ends where it begins, so wrapped text wraps short of
  it and a long line scrolls sideways inside the text's own box, never
  under the cards, and a wheel over the column scrolls the text. It
  stands with room for 360 px of text beside it, never on a
  phone; `marginRef` answers App by that room, and a resize or a wrap
  that takes it from the active card calls `onMarginLost`. Every place is
  read in CodeMirror's measure phase, again as its geometry, viewport or
  text changes.
- *A jump* (`scrollRef`: a thread opened, a row's dot, the pending jump)
  brings the range's first line into view, centred, unless it is in view
  already, and flashes it; asked before the text of this version is in,
  while the pane is closed or past the head read so far (the whole file
  read first), it waits and runs once the text is there.
- *A new version of the file* reloads the text at the same top line and
  draws the ranges where their positions say. The client never places a
  range itself: the server places them again on the replacement
  ([highlight_relocation.md](highlight_relocation.md) "Text files") and
  its writes arrive over the page's socket; until then a range measured
  on the old version reads pending, drawn faint.
- *Its menu and its row* are a highlight's, without the Link rows: Accept
  for a dashed one (a text file has no review card), "Move to a
  selection…" (the next selection in the code view is its place), "Use
  the closest match ({n}%)" for a lost one that has a `closest`, Comment
  and Properties ("Lines 118–120" with its chips). Its row in the notes
  has the dot that jumps to it ("Jump to lines 118–120", or that it was
  not found or matched roughly) and the Move button ("Move to a selection
  in the file").

**Back's place.** `navEntry` asks the pane's `reportRef` and keeps the
page's place (`place`, the code view's `{line}`) beside the PDF's scroll.
`goBackNav` to another page writes it to `tabScrollRef` before opening the
page, whose pane mounts there; on the same page (a line link to the open
file pushed the entry) it sets `pageTarget` to `{kind: "line", line,
scrollOnly: true}`, which the mounted view scrolls to (its top line, no
mark, no notice) and ends at once. Back returns to where the reader was;
the mark is for links.

**The region tool**: one drag for the PDF's area and the picture's region
(`shared/ui/regionDrag.js`; `shared/ui/regionGeometry.js` turns the drag
into `image_position` in the picture's own pixels, [api.md](api.md) "The
highlight shape"). On a
picture a mouse or a pen draws with no key held (a picture has no text to
select), a finger only in region mode (the strip's toggle), two fingers
pinch; never from the margin, a badge or the tip, and not at all when
read-only. The tip (the PDF's frame, marks and tools) offers the colour,
Comment and Ask AI. The
region's row in the notes (`editor/BlockTree.jsx`) is a highlight's row
with "Region of the picture" where the page chip would be and its crop
(`AreaSnapshot`, through the picture's `captureRef`; a crop that comes
back empty while the picture loads is asked again); the notes lock leaves
it open (`lockedRow`), and the thread popover, the guide's note demo and
the history's labels treat it as a highlight (`isPlaceBlock`). A
replaced picture keeps its regions where they were, drawn dashed; `PUT
/attachment` keeps the page's format and starts no relocation between
pictures ([highlight_relocation.md](highlight_relocation.md)).

## Fixtures

- `tests/shared/formats.json`: `names` (a file name's format, language and
  stored extension; both sides), `media` (how each stored extension is
  served: media type, inline, sandboxed; the backend alone), `sniff`
  (first bytes; the backend alone), `vault` (`vault_extension`; the
  backend alone) and `stored` (`stored_format`'s table: name, stored
  extension, head → format, language; the backend alone). Read by
  `test_shared_fixtures.py` and `documents.test.mjs`.
- `tests/shared/document.json`: `cases` (properties in, the view out or
  null, for every rule of `check_document`, the listed state's included;
  both sides), `legacy` (each state of the table above, before and after:
  `document_shape`, and `test_migrations.py`, which writes them as root,
  trashed and nested rows and runs the step) and `copies` (the doc a
  duplicate writes: `copyDocument`, the client alone; null for a listed
  file).
- `tests/shared/textdecode.json`: `cases` (bytes in base64; the text,
  `bad` and `binary` out, both sides; `encoding`, the backend's codec name,
  the backend alone): a file of each byte-order mark, bytes that are no
  UTF-8, a NUL at byte 5,000 and at byte 12,000 of 16 KB (past the first
  8 KiB: anywhere in the text decides), mostly bad bytes, an empty file, a
  character cut at the end. Read by `test_shared_fixtures.py` and
  `textLoad.test.mjs`.
- `tests/shared/blockkind.json`: a block whose one property is a document
  is a note; a region of a picture, and a range of a text file, are
  highlights.
- `tests/shared/textposition.json`: a range of a text file's span, as
  given and as stored (`trimmed`: a column past its line clamped, an end
  at column 0 moved back past the line breaks), its columns in UTF-16 code
  units as CodeMirror counts them, and the quote, prefix and suffix cut
  from the lines (astral-plane characters, a tab, CJK, the file's two
  ends, a line break alone). Read by `test_shared_fixtures.py`
  (`highlights.span_text`, `clamp_span`) and `textAnchors.test.mjs`
  (`contextOf`, `toCodePoints`, `toCodeUnits`).

A rule that changes on one side is a case added to the fixture, which
fails the side that drifts ([debugging.md](debugging.md)). Tests make
pages with `backend/tests/conftest.py` `doc_props(hash, *, url, name, at,
copy, format, ext, size)`, which builds `{"doc": …}` through `document`; only the migration's
and the restore's tests write the flat keys, on purpose.

## As built (step 3)

Step 3 of [research/file-explorer.md](../research/file-explorer.md) "In
steps": any file as a page. The model's part is above (the listed state,
`size` on every writer, `stored_format`, `vault_extension`, the viewer
registry, a picture's regions as `image_position` highlights, migration
step 55), and with it:

- **Documents of other formats in the library** ("Writing a document"
  above): a stored file of any format attached by its name (`file`), one
  library page per stored file by hash and extension, `POST
  /api/pages/by-file`, `by-docs`' `files`, a replacement that keeps the
  page's format and relocates highlights only between PDFs. `POST
  /upload-file` keeps an image's own extension when it is an image row of
  the declared type (`photo.jpeg` stays `.jpeg`), so a document's stored
  name is the same on every server a mirror pushes it to
  ([mirror.md](mirror.md) "What travels"); the client uploads any file
  through `uploadAnyFile` (`shared/lib/uploadParts.js`: `/upload-file`,
  in parts past 32 MiB with `kind: "file"`). The paper features stay the
  PDF's: the metadata status, batch and hand lookup leave out or refuse
  (409) a page whose document is no PDF unless it carries a record, a clip
  starts a lookup for a library PDF alone (`is_library_pdf`), the AI's
  `clip_region` and `view_pdf_page` want a PDF, and the restore review's
  and the import reports' `kind` is the document's format.
- **The client's ways in** (`transfers/documentUploads.js`, with its pure
  rules in `transfers/fileRouting.js`): `routeFile` says what a file
  becomes (`pdf`, `markdown`, `file`, or nothing for an executable),
  `dropFilter` leaves a dropped directory's dot entries, `node_modules`,
  temporary names and executables out (`ASK_PAST_FILES`: past 100 files
  App asks first), `uploadOneFile` makes a library page (by-file),
  `attachFile` the open page's document (`{file}` to the attachment
  routes), `promoteChip` a chip's page by its format. The library's drop
  and *Upload files…* take any file (no `accept`, `PAGE_FILE_ACCEPT` is
  gone), a file a native app hands over that is no picture, note or
  archive is a page (`openedFiles.openedKind`), and a file chip of every
  format offers *Add to library* (`FileChip`: the lookup by stored name,
  `rememberDocPage(name, page)`). In App, `uploadFiles` routes every file
  by `routeFile` (a PDF the PDF way, Markdown the importer, any other
  `uploadOneDocument` → by-file, a single one opening its page), a
  dropped or picked directory goes through `uploadFolder` (`dropFilter`,
  and past `ASK_PAST_FILES` the question "Upload {n} files to the
  library?"), the paperclip's *Upload a file…* and *Replace the file…*
  (the picker accepting the page's format's extensions, `replaceAccept`)
  take any file through `attachFile`, with *Find highlights in this PDF*
  and *Push from a file on this computer…* for a PDF alone and *Detach
  the file…* for every document; the attach 409 reads "That file is
  already in the library as {title}"; a chip's *Add to library* is
  `promoteChip`, then `rememberDocPage` by the stored name.
- **Their viewers.** Each is a row of `FORMAT_VIEWER` with its descriptor
  in `VIEWERS`, a component, and `DocumentPane`'s default branch, which
  draws it with the host's surface when the host gives no render function:
  text in CodeMirror, pictures, the browser's players, and a pane for what
  cannot be shown or is listed.
- **The vault's classes.** The engine's class registry
  (`gamma/vault/kinds.py`) decides a file's class by its name through this
  registry's formats, and a work folder's class `file` makes a page of
  every other file, with `vault_extension` and `stored_format`; its Add to
  library and Save a copy store any format
  ([work_folders.md](work_folders.md) "Add to library, Save a copy,
  Remove"), a listed file neither (409 "This file is too large to keep in
  Gamma.").
- **The tables kept apart:** `FileChip.KINDS` (its words are finer than
  formats, and the tooltip's alone), and the hex-only parsers
  `pdfSource.docIdOf` and `FileChip.parseUploadUrl`.
- **Left for later:** the highlight menu opened on a region shows the
  PDF's rows (a region has no rows of its own yet); the AI title is offered
  for a PDF alone (`docId` is a PDF's); the chat's context and `read_block`
  draw a PDF's area pictures, not a region's; a region exports as a note in
  the readable exports; a text file is read, never edited; carrying an
  *Opened files* page to another server is for a PDF alone (the page
  itself takes every format with a viewer); a listed file's head is not shown as text; the keep limit is
  an environment setting ([work_folders.md](work_folders.md) "Known gaps").
  The whole list is the plan's "As built (step 3)".
