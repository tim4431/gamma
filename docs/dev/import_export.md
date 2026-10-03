# Import and export

The View menu's (≡) Import…/Export… dialogs and every pipeline behind them: embedded
PDF annotations, Logseq graphs, Zotero libraries, Markdown notes (Obsidian
vaults, Notion exports), Markdown and Obsidian vault export, the notes
typeset as their own PDF, the BibTeX bibliography, and the annotated-PDF
writer. Code: `gamma/routers/imports.py`, `gamma/zotero_import.py`,
`gamma/zotero_export.py`, `gamma/logseq_import.py`, `gamma/markdown_import.py`,
`gamma/markdown_zip_import.py`, `gamma/markdown_export.py`, `gamma/obsidian_export.py`, `gamma/pdf_export.py`,
`gamma/pdf_notes.py`, `gamma/pdf_document.py`, `gamma/pdf_typeset.py`,
`gamma/note_markup.py`, `gamma/vector_text.py`, `gamma/pdf_glyphs.py`,
`gamma/pdf_image.py`, `gamma/text_box.py`, `gamma/bibtex.py`,
`gamma/routers/export.py`; frontend dialogs in
[ImportExport.jsx](../../frontend/src/transfers/ImportExport.jsx), imported directly by
[App.jsx](../../frontend/src/app/App.jsx). Exports and library imports run as
background jobs, which the dialogs follow ([tasks.md](tasks.md)). The
Export… row carries the `page.export` guide anchor: a hint beside it offers
the export once a page has five highlights of the user's own
([onboarding.md](onboarding.md)).

## Importing annotations embedded in a PDF

`/api/import/pdf-annotations` converts annotations embedded in the PDF file
(SumatraPDF/Acrobat/Gamma-export highlights, and /Square//Circle → area
highlights) into highlight blocks — each a `pdf_position` in the page's
points (the page box once, the quads as bare rects, `area: true` for a
/Square or /Circle; [api.md](api.md) "The highlight shape") under a block
id of its own, which is the highlight's id — idempotent via `properties.imported_annot`
keys; opacity honors the annotation's `/CA` so a Gamma export → re-import
round-trips exact colors; PyPDF2 dict access returns `IndirectObject`s, always
`.get_object()` them. `/Ink` (freehand drawings from any PDF app, or a Gamma
export) becomes a handwriting block: the strokes are stored as an `.ink`
upload (`gamma/ink.py`), the block gets `ink_url` / `ink_strokes` /
`pdf_position`; a Gamma export's private `/GammaInk` key restores pressure
and time, foreign ink is polylines at the annotation's width
([handwriting.md](handwriting.md)). `/FreeText` (typed text) and `/Text`
(a sticky note) become text-box blocks (`text_box`, `pdf_page`), not
highlights, by the rules in
[text_boxes.md](text_boxes.md#server-and-interchange). Their place comes
from `/Rect` through the page's view box and `/Rotate`
(`pdf_export.page_frame`, `pdf_point_to_viewer`), the exact inverse of the
export's mapping. The highlight, area and ink import does a plain
media-box flip. A text box's `imported_annot` key
(`pdf_export.annotation_key`) is the one a highlight block made from the
same annotation carries, so such a block counts as there and nothing comes
twice.

Only what the page shows is imported (`pdf_export.annotation_shown`): an
annotation flagged Hidden or NoView, or a review-state stamp (`/State`,
`/StateModel`), makes nothing. A reply (`/IRT`, not a `/RT /Group` member)
to an annotation of any imported kind is a note under that annotation's
block, in page order, its text as written. A reply to a reply goes under
that reply, and one answering something not imported is left out. A
reply's key is its rectangle's plus its `/NM` (or its place in `/Annots`).
A block holding the rectangle's key alone also counts as the reply, so a
thread whose replies were stored as annotations of their own adds nothing
twice. The keys are looked up under the `block_id` at any depth
(`_imported_blocks`), so a reply's note, or a block moved deeper since, is
found. The blocks land
under the `block_id` asked for, which may be a block inside the page; the
reload and the log entry go to the page. `found` counts the annotations
that make blocks, replies included.

The Logseq import (`POST /api/import/logseq`, a .pdf + .edn and an optional
.md) goes into the page carrying that PDF, made when there is none, under
the write lock like every lookup by attachment. Highlights already on the
page are skipped by their quote and notes by their text (a note written
twice in the .md stays twice), so running it again adds nothing. Into an
existing page it logs one `reload` under the importing account. An EDN
position keeps the page size in every rect; the import stores it once
(`logseq_import.edn_highlight_position`, `highlights.from_scaled`), and an
annotation of the .md the EDN has no box for keeps its `hl-page` as a
position of its page alone.

Because imported annotations would otherwise render twice (pdf.js paints them
into the canvas AND the blocks draw as overlays), the Settings → Reading &
editing → PDFs → "Imported annotations" preference (`embAnnots`) either
sends `strip: true` (the default) so the import rewrites the stored PDF
without them, or keeps the file as it came and hides them viewer-side
(`annotationMode: DISABLE`). Stripping makes the blocks the only copy, so
deleting one in Gamma removes it from the exports too. A kept original
outlives its block: delete the block and the annotated export still shows
the original (the View menu's "Import…" dialog can
override that for one run; the auto-import on open always follows the
preference). The strip (`_strip_embedded_annotations`) removes the
annotations the import makes blocks of, with their threads (the replies
and review states that answer them) and their `/Popup` windows
(`pdf_export.drop_annotations`). Links, kinds not imported, hidden ones
and those that make no block (a note with no text) stay. The blocks whose
originals it removed get `properties.annot_stripped`, which tells
`/export-pdf` to write them again. The export skips `imported_annot`
highlights and ink only while the original is embedded
(`pdf_export.still_embedded`). A text box it
writes either way, replacing the embedded original
([below](#annotated-pdf-export)).

## The Import dialog

The View menu's single "Import…" entry → `ImportDialog` in `transfers/ImportExport.jsx`, the
export dialog's counterpart. Step one is a source card: annotations embedded
in this PDF, a Logseq .pdf + .edn, a Zotero library .zip, Markdown notes (one
`.md` or a `.zip` such as a Notion export), or a Gamma export .zip. Double-click
or Next confirms. Only sources with an option get a review step. That option is
the strip switch, which applies to embedded annotations, including those
inside Zotero's exported PDFs. Markdown, Logseq and Gamma open the file picker
directly, with their preparation notes under the selected card.
`resolveImport` in `transfers/transferFormats.js` decides both.
Zotero is the default source (a numbered step guide reusing
settingsKit's `Step`); with a PDF open, that PDF's own annotations win. Nothing
is remembered: the switch starts from the Settings preference each time, so the
setting stays the standing policy.

Zotero, Markdown (ZIP or one file), Obsidian/Notion ZIPs and Gamma exports
share one review flow: Upload → Review → Import → Summary.

- `POST /api/import/review` takes the file, its `source` (`zotero`,
  `markdown-zip`, `markdown-file`, `gamma`), an optional `folder` (the
  destination folder's id, `""` the library's top; 400 when it is no
  folder, `import_review.destination`) and `strip`.
  It stages the upload, runs the source's preview and returns the two trees
  plus a `review_id`; `DELETE` discards it.
- The import itself is a background job ([tasks.md](tasks.md)):
  `POST /api/jobs/import` with `{review_id, selected: [...]}` imports from
  the staged upload against the current library, reporting each item; its
  result is the import report. Asking again for the same review answers the
  job already started (409 with another selection).
- Each source is a request-free preview and commit in `routers/imports.py`
  (`_review_source`), over the staged file and the review's metadata; the
  plain endpoints (`/import/zotero`, `/import/markdown-zip`, …) run the
  same functions in the request.
- Staging (`gamma/import_staging.py`): a directory per review, bound to the
  account and workspace, expiring after two hours (`TTL`; expired ones are
  swept on the next upload). The import job holds a claim (`running/`
  mkdir; a `DELETE` meanwhile answers 409) while it reads the upload, and
  removes the review when it ends — done, failed or stopped. Staging has
  its own 1 GB cap (`import_staging.MAX_BYTES`) and is not metered by the
  account quota.
- Selection ids are source ids (`page:<id>` / `chat:<id>` for a Gamma
  export, Zotero item keys, ...); duplicate Zotero records share
  `selection_ids`. `parse_selection` (`gamma/import_review.py`): `None`
  (no field) imports everything, `[]` imports nothing.
- A stopped import keeps what it wrote: the Zotero and Markdown imports
  write the new pages whose files they already stored, then stop.
- Frontend (`src/transfers/`): `ImportReviewDialog.jsx` owns the steps and
  the report. It uploads and reviews itself, then follows the import job
  (`tasks.start("import", …)`) with its progress and Stop. It may close while
  the job runs, and the job's row in Background tasks opens it again on that
  job (`{jobId}`, no file: the summary then shows the library column only).
  `ImportTree.jsx` renders one tree with its checkboxes (a folder toggles
  its descendants); `importReview.js` owns selection, filters and tree
  construction (filters only hide rows; hidden selections stay selected);
  `importApi.js` the upload, the job's body and the discard;
  `shared/lib/xhrUpload.js` the upload progress.

Logseq and in-PDF annotations keep their own import controls.

## Plain Markdown uploads

The add menu's file picker, directory picker, and whole-window file drop all
accept `.md` / `.markdown` alongside PDFs, and the Import dialog's "Markdown
notes" source takes one `.md` too. `POST /api/import/markdown` decodes
UTF-8 (5 MB cap), reduces any browser-supplied relative upload path to its
filename leaf, uses a YAML-frontmatter `title` or that filename's stem as the
note-page title (the form's `folder`, a folder id, files the page there; a
front-matter `folder:` path files it in the folder at that path below it,
made where missing in the page's own transaction), and converts the document into nested Gamma blocks through
`gamma/markdown_import.py`. Headings and indented lists retain hierarchy;
paragraphs, fenced code, math and other Markdown stay as raw block content for
the normal editor renderer. Lines indented under a list item continue that
item, whether directly below it or after a blank line when aligned with the
item's text. That is how the Markdown export writes a multi-line block, and a
fence or `$$` opened that way swallows its lines, blank ones included. Text
indented deeper after a blank line becomes a child block, which is how Notion
exports a toggle's content. In mixed folder uploads, Markdown note pages and PDF pages
are filed in the same subfolders; unsupported files are skipped.
`POST /api/import/markdown` answers the page's `folders` (ids).

## Markdown zips: Obsidian vaults, Notion exports, Gamma exports, zipped notes

Mermaid fences stay as editable Markdown through import/export and render as
diagrams in the frontend. The diagram toolbar can download SVG separately;
backend PDF exports retain code-block output. See [mermaid.md](mermaid.md).

`POST /api/import/markdown-zip` (Import dialog → "Markdown notes", pick a
`.zip`; `gamma/markdown_zip_import.py`) turns a zip of `.md` files into one
page per file. One logic covers a zipped Obsidian vault, Notion's Export →
"Markdown & CSV" (with subpages), Gamma's own Markdown export and any zipped
folder of notes, because they only differ in naming and link conventions
(the survey behind the Obsidian mapping: [docs/research/obsidian.md](../research/obsidian.md)):

- **Title**: front-matter `title`, else the leading `# H1` (stripped from
  the body — Notion and Gamma both write one), else the filename with
  Notion's `Title <32-hex id>` suffix removed. In an Obsidian vault
  (recognised by its `.obsidian/` folder) the filename is the title, as in
  Obsidian itself, and the H1 stays in the body unless it repeats the title.
- **Folders**: directories become folders, each directory name a folder
  name as written (Notion's id suffix stripped), made below the import's
  destination folder (the form's `folder`, an id: the dialog's target, the
  open library folder) where missing and reused by name where there
  (`ops.ensure_filing`, matched as `blocks_store.named`: exactly, else
  ignoring case). Notion puts a page's subpages (and its images) in a
  folder named after the page, so the Notion page tree becomes the folder
  tree. A front-matter `folder:` path wins over the directory. The folders
  and labels the chosen new notes need are made once, before any page is
  written (one committed batch per tree): an import that stops keeps them.
  A preview makes none. One common root directory (a zipped folder)
  and Notion's `Export-<uuid>/` wrappers are dropped; Notion's `Part-N.zip`
  members (big exports) are read in place; `.obsidian/`, `.trash/` and
  `.canvas` files are skipped (a canvas is a warning).
- **Links**: a link to another note in the zip becomes a `[[page]]` mention
  of the page it produced — Markdown links (Notion's percent-encoded
  `[Sub](Parent%20<id>/Sub%20<id>.md)`, Gamma's `[label](Page-id.md)` and
  `*(from [title](file.md))*`) and Obsidian wikilinks (`[[Note]]`,
  `[[Note|alias]]`, `[[folder/Note]]`) alike. A target is resolved relative
  to the note, then as an exact vault path, then by basename anywhere in the
  zip (nearest directory wins), case-insensitively — Obsidian's own rule.
  `[[Note#Heading]]` and `[[Note#^id]]` point at that heading block or
  anchored block (`^id` markers are indexed per note and removed from the
  text; an anchor on its own line belongs to the block before it),
  `![[Note#^id]]` becomes a Gamma synced block `![[id]]`, while `![[Note]]`
  and `![[Note#Heading]]` degrade to mentions (a Gamma embed shows one
  block). A link or image pointing at a bundled file uploads it
  (`store_file`, content-hash dedup, storage limits per file — an over-limit
  file is a warning and the link stays as typed) and points at
  `/api/uploads/…`; `![[img.png|300]]` / `|300x200` becomes the editor's
  `![|300](url)` size form, other pipe text the alt. Links that resolve to
  nothing stay as typed (so a Gamma `[[id]]` is never touched).
- **Obsidian specifics**: front-matter `tags` (list, flow list or comma
  string; `tag` too) become labels, label blocks made by name where missing
  (a name keeps its `/` and `,`), `aliases`
  are kept in `properties.aliases`; `> [!type]+`/`-` fold markers are
  dropped (in `md_to_blocks`, so pasted text loses them too);
  `%%comments%%` are removed in a vault (inline or block, never inside
  fences). Inline `#tags`, footnotes and task states pass through as text.
- **Notion specifics**: a database `Name <id>.csv` becomes a page holding
  the table (`_all.csv` preferred when both exist — it has every row; capped
  at 500 rows × 40 columns) and its row pages `Name <id>/Row <id>.md` land in
  the folder of that name; `<aside>` callouts become `> [!info]` callouts;
  the row pages' `Property: value` lines stay as text.
- **Gamma specifics**: the front matter's `source:` restores the PDF when it
  is bundled (`assets/<sha>.pdf`, or the vault export's quoted
  `"[[Paper.pdf]]"` → `doc_id`, the page becomes a paper again) or the
  remote URL (`source_url`) when it isn't; the export writes the page's
  attachment URL there, derived from `doc_id` for a stored copy
  (`page_attachment`); `doi`/`authors`/`year` →
  `properties.meta` (`source: manual`; `authors` as a YAML list or a comma
  string), the ```` ```bibtex ```` block → `properties.bibtex`. Highlights come back as their quote blocks, not as
  positioned highlights — the Gamma format (`?mode=gamma`) is the lossless
  route; Markdown is for notes and for other apps.
- **Idempotent**: a `.md` already imported (same bytes — `markdown_import`
  digest — or the same `notion_id`) is skipped, and links to it resolve to
  the existing page, so re-importing an export adds nothing.
- **In short batches**: a page's bundled files are stored while its links
  are rewritten, outside any transaction. Its rows then wait with the next
  ones, and every `PAGES_PER_COMMIT` (50) pages go in as one short
  transaction: `insert_note_page` takes the write lock and touches each
  page in the change log. Other writers are never shut
  out for the length of an import, the change feed lists each batch as it
  lands ([collab.md](collab.md) "The change feed"), and an import that
  fails half way keeps the batches it finished. (A transaction per page
  made big imports about twice as slow.)

The report's counts (`pages_created`, `pages_skipped`, `assets_stored`,
`links_resolved`, `notion`) and warnings appear in the shared dialog. `pages`
lists selected pages and their destinations, including pages already in the library. The same engine's `preview=True`
mode parses notes and checks assets without writing uploads or blocks; optional
selection limits page creation and associated assets. Links to unselected new
notes remain as written, with warnings. A selected note may still link to an
already imported note. To make the round trip work the
Markdown export writes the page's folder path into the front matter
(`folder:`), relative to the exported folder — a folder export's root pages
carry none — so importing the zip into a folder rebuilds the same tree there.
The path is the names joined with `/`, a `/` inside a name written as `-`
(so "TCP/IP" re-imports as one folder, "TCP-IP"); the importer splits the
value on `/` (`blocks_store.split_path`).

The review and the report list each page's `folders` as paths, each a list
of names from the library's top (`[["Imports", "Notes"]]`: names may hold
`/`): a new page's are the destination's path and its own, an existing
(skipped or merged) page's its current ones. The report's `folder` is the
destination's id.

## Zotero library import

`POST /api/import/zotero` (⋮ → Import… → Zotero library): a zip of Zotero's
File → Export Library → "Zotero RDF" (with Export Files/Notes).
`gamma/zotero_import.py` parses the RDF (items, journal records carrying the
DOI, collections, tags, HTML notes) and tolerant zip-name lookup
(cp437-mojibake, NFC/NFD, backslashes); the endpoint in `routers/imports.py`
uploads PDFs (dedup + quota per file, over-quota items are skipped not fatal),
upserts pages keyed by file hash then `properties.zotero_key` (re-exports
change bytes — Zotero re-embeds annotations at export time), maps
collections→folders, nested as in Zotero, made below the destination
folder (the optional `folder` form field, a folder id) where missing — an
item in no collection lands in the destination itself —,
tags→labels (by name, made where missing), notes→child blocks
(`properties.zotero_note`), then runs the
shared `import_embedded_annotations` (reader annotations arrive inside the
exported PDFs; `strip` follows the client's embedded-annotations preference).
Merging only fills gaps: existing meta/bibtex/files are kept, the folders
and labels union (the page's own ids first, those that still exist —
`existing_in`).
Each item's PDF is stored first, outside any transaction
(`_zotero_prepare`). New pages and their notes then go in
`ZOTERO_PAGES_PER_COMMIT` (50) at a time, in one short transaction under
the write lock, each page touched in the change log (`_zotero_write_new`);
an item that merges writes the new pages before it first, so the report
keeps the export's order. Each new page is looked for again once the lock
is held: when another import of the same item made it meanwhile (a
double-click, two tabs), or an earlier item of the batch did, this one
merges into it after the commit instead of making a second. The embedded annotations are likewise checked
against the page's `imported_annot` keys under the lock. A merge into an
existing page is an op batch by the importing account (a `set` of the
changed properties plus an `insert` per new note, through `apply_ops`),
which the page's open tabs and the workspace's mirrors see like any edit.
A re-import that changes nothing writes nothing.

Choosing the ZIP opens the shared import review dialog. Its two trees show
the archive (including empty directories and unused files) and the destination
library (PDF/page, new/update, collection paths, notes). The active library
folder becomes the import's destination (its id). `POST /api/import/zotero/preview` accepts
the same `file` and `folder`, requires workspace write access, and writes no
pages or uploads. Both endpoints use `plan_zotero_archive`, so attachment
resolution and warnings agree. The staged review flow reuses the upload,
rechecks the current library, and leaves actual results and warnings in the dialog.

- Inputs: standalone and inline PDF attachments, both MIME namespaces,
  literal or resource paths. Lookup normalizes dot segments, percent
  encoding and ZIP filename encodings. A URL in metadata is not an
  attachment; an empty directory supplies no bytes.
- `resolve_pdf_entry`: when the filename changed, only a unique PDF in the
  same `files/<attachment-id>` directory is used; ambiguous or cross-item
  matches are never guessed.
- Extra PDFs of one item become extra pages in its collections, keyed
  `<key>#pdf:<path>`. Reimporting a complete export attaches a recovered
  PDF to an existing metadata-only page.
- Warned: missing or invalid PDFs, unsupported or unlinked files, recovered
  filenames, existing PDFs kept.

## Zotero RDF export

The import's exact inverse (`gamma/zotero_export.py`, endpoint branches in
`routers/export.py`): `?mode=zotero-rdf` on `/pages/{id}/export` and
`/folders/{id}/export` builds a `<slug>/<slug>.rdf` + `<slug>/files/<n>/<name>.pdf`
zip that Zotero's File → Import reads (unzipped) and Gamma's own
`/api/import/zotero` accepts as-is. Element shapes mirror what Zotero itself
writes and `parse_zotero_rdf` reads: venue/volume/DOI on a standalone
`bib:Journal` referenced by `dcterms:isPartOf`, notes as `bib:Memo` HTML
(top-level non-highlight subtrees, one note each — the inverse of the import's
notes→child-blocks mapping), the page's folders as the `z:Collection` tree
(paths below the exported folder on a folder export — a page filed in the
exported folder itself is in no collection —, the whole path for one page),
its labels' names as `dc:subject`,
`properties.zotero_key` reused as `rdf:about` so keys survive a round trip.
Attachment paths live in `z:path` like Zotero's own export — never an
`rdf:resource` *element*, an RDF/XML syntax term that Zotero tolerates but
strict parsers (rdflib) reject. The pipeline is verified against a live Zotero
via its connector server's `/connector/import` (same translator code path as
the wizard). Zotero cannot read the .zip itself — its wizard reports
"unsupported format" for one, so the zip ships a README.txt telling people to
extract and pick the .rdf, and the export dialog shows a numbered step guide
for the format. Pasted note images: embedded in the Memo HTML as data URIs
(Zotero's note import keeps them — verified live; `_EMBED_IMAGE_CAP` guards
size), attached to the item as image `z:Attachment`s when bundling, and
replaced by a plain `(image: … — see item notes)` placeholder in annotation
comments (`strip_image_md`) — comments come from the PDF's `/Contents` and can
never render a picture, so a highlight whose notes carry images ALSO becomes
its own Memo with a page+quote header (`highlight_memo_html`). An export job
([tasks.md](tasks.md)) reports each page as it goes. Highlights are not in the RDF
— like Zotero's "Include Annotations" they're burned into the exported PDF
copies with `pdf_export.annotate_pdf` (`highlights=0` skips that, `pdf=0`
omits the files entirely, `notes=0` the Memos). The copies carry no ink or
text boxes; a text box is a Memo ([below](#text-boxes-in-the-exports)).

## The export framework

The export job (`POST /api/jobs/export`, what the Export dialog starts),
`/pages/{id}/export` and `/folders/{id}/export` share one driver (`_run_export`
in `routers/export.py`). A folder export names its folder by id (the job's
`folder`, the route's path); `_Filing` reads the folder and label trees
once per export — a page's folder paths below the exported folder (its
folders outside it left out), its labels' names — for the builders that
write directories, collections or tags. It walks the selected pages exactly once (subtree
fetch, `build_tree`, a progress report) and feeds each page to a per-format
`_Builder` keyed by the mode: `_MarkdownBuilder`, `_ObsidianBuilder`,
`_NotesPdfBuilder`, `_AnnotatedPdfBuilder`, `_LogseqBuilder`,
`_ZoteroBuilder`, `_GammaBuilder`. The builder accumulates zip parts, and
`save(dest)` writes the download, returning its name and media type. The
job saves to its file; `response()` saves to a temporary file and answers
with it. `begin(conn, root_ids)` shows a builder the whole export set before
the walk; the DB connection is closed by the time `save` runs. A generated
part too big to hold (an annotated PDF) is `spool`ed to a temporary file
and packed from there, and a bundled upload is packed straight from the
uploads directory. `skip(page, reason)` leaves a page out, and `summary()`
(the job's result) counts the pages and lists the skipped ones. Adding an
export format means adding a builder; the endpoints, the job and the zip
writer stay untouched. A builder whose download isn't a zip overrides
`save`: `_NotesPdfBuilder` writes one PDF, `_MarkdownBuilder` a bare `.md`
for one page with no local files, `_AnnotatedPdfBuilder` one page's PDF.

`annotated-pdf` (`_AnnotatedPdfBuilder`) is each page's PDF with its
highlights, handwriting and text boxes as standard annotations and, with
the notes switch, its notes printed on the page (`annotated_page_pdf`, what
`/pages/{id}/export-pdf` runs). For one page it is that PDF. For a folder it
is a zip of them, `<subfolder>/<Title>.pdf`, the directories mirroring the
page's first folder path below the exported folder (`_Filing`,
`obsidian_export.page_dir`). A
page with sheets of paper and no PDF is exported as its sheets. A page with
neither is left out, and the finished export lists it. A folder with no
PDF at all fails with the reason.

The single-page exports are also plain functions, for callers without a
request: `page_builder` (one page through a mode's builder, what
`/pages/{id}/export` runs), `page_markdown` (the readable Markdown, unbundled,
upload links left as `/api/uploads/…`), `page_notes_pdf` and `annotated_pdf`
(what `/pages/{id}/export-pdf` runs). They raise `HTTPException` like the
routes. The MCP `export_page` tool is their other caller ([mcp.md](mcp.md)
"Export a page").

## Gamma-to-Gamma export

`?mode=gamma` (`_GammaBuilder`): a *scoped account backup* in the same
`gamma-backup-1` layout as `/api/export` (`gamma/ws_backup.py`) — a `pages.db` holding just the
selected page subtrees verbatim (same block ids), the folder and label
blocks they are filed under — every folder a page is in with the folders
above it, every label it carries, and on a folder export the exported
folder's whole subtree (empty subfolders too) with the folders above it —,
and their AI chats — each page's bucket whole, the active conversation and
its history, plus on a folder export the folder views' own buckets, the
folder's and its subfolders' ids (`db.copy_chats`) — `uploads/` with just the referenced
files (`storage.upload_refs`, the reference rule the orphan check uses:
doc_id PDFs + `/api/uploads/…` in content/properties), and a
`manifest.json`. No `data.db`: everything in it is rebuilt (since schema
version 28; an older export carries its chats in one, and the import moves
them as a restore does). **There is no new import code**: any Gamma
imports it through the existing `/api/import-data?mode=merge` — additive
(the folders and labels join the workspace's trees: a block it has by id is
that block, else the one at the same path, or a label of the same name, else
it is added there; the pages are filed under the workspace's ids for them
and the folder chats follow — `ws_backup._merge_trees`, `_refiled`; a
reviewed import brings only the folders and labels its chosen pages need),
deduped by block id / doc id / conversation / content hash, so re-importing
adds nothing.
A page it adds comes in whole and touched now (the change feed sees it); a
block whose id the workspace already uses on another page gets a fresh id
([workspaces.md](workspaces.md) "Export and backups"). The
⋮ Import dialog's "Gamma export (.zip)" source reviews the zip like the
other library sources, then merges the selected pages (`restore_zip` merge
with a selection) as the import job; guests can't import. A Gamma export is
a complete copy, so the Export dialog has no switches for it.

### Importing a shared page by link

The same pipeline, without the zip ever touching disk: `importSharedPage` in
`app/App.jsx` takes a share URL (`https://other/?share=<token>`), resolves the
token against that origin's `/api/share/{token}`, fetches the page as
`/api/pages/{id}/export?mode=gamma&share=<token>`, and hands the blob to
`runBackupImport(…, "merge")` with `after.openPage` set: a `restore` job
([tasks.md](tasks.md)), after which the tab reloads. Block ids survive the
export, so the reload lands on `?page=<id>` in the importer's own library.
The fetch is browser-side on purpose: the browser reaches a Gamma on
the LAN or at `localhost` that the server's SSRF guard (`net_guard.py`)
refuses. That works because share GETs answer
`Access-Control-Allow-Origin: *` ([api.md](api.md)); with `*` the browser
sends no cookies cross-origin, so only links open to `anyone` import from
another Gamma, while a same-origin link also carries the session and
invite-only ones work too. Two entry points:

- The topbar's "+" box: a pasted `<origin>/?share=<token>` is recognised by
  shape (`parseGammaShareLink`: the SPA root with a `share` query), shows a
  hint, and Enter imports instead of fetching a PDF. Everything else typed
  there is still a paper.
- The share view's topbar: a signed-in non-guest viewer gets "Add to my
  library" (the same function on `window.location.href`). The page's owner
  gets "Open in my library" instead, a plain jump to `?page=<id>`, since the
  page is already theirs. A visitor with no account gets "Sign in" (the
  share's own sign-in gate), after which the link opens again with the
  import button.

The remote must be recent enough to serve `mode=gamma` and the CORS
header; an older one surfaces as "couldn't reach …" / "too old" in the
status line and the task's row.

## The Export dialog

The View menu's single "Export…" entry → `ExportDialog` in `transfers/ImportExport.jsx`.
Step one is a format card. The PDF row holds Annotated PDF, the Notes row PDF
and Markdown, the ZIP row Obsidian, Logseq, Zotero and Gamma. Double-click or
Next confirms. Formats with editable options get a review step: the
Highlights, Notes and Bundle-the-files switches beside an illustrative page
(`illustrations/TransferPreview.jsx`, an example of the options, not a render
of the document). Gamma has fixed contents, and a PDF without a stored copy
can only be the original file, so both export straight from step one. Logseq
shows only the bundle switch. BibTeX has no switches but still gets a step:
it previews the real bibliography
([below](#bibtex-bibliography)). `hasReviewStep` answers that question for
the setup and the job step together, so their breadcrumbs agree. The
breadcrumb returns to the cards without losing edits, from the job's last
step too. There `onLeaveJob` (App's `leaveExportJob`) drops the dialog's
hold on the job, which goes on in Background tasks as if the window had
closed, and `ExportDialog` reopens the setup at the step the crumb named. Both dialogs are a `SubDialog` (focus trap, Escape, backdrop)
with its close-button header; the footer holds only Next or the final action.
Zotero's post-export steps expand under "Open this export in Zotero".
`transfers/transferFormats.js` owns the format table (label, category, hint,
editable and fixed options, `EXPORT_SWITCH_TEXT`, the row order `CATEGORIES`)
and `resolveExport`. The resolver turns the saved options into the controls,
decides whether a review step is needed, and builds the one payload the
preview and the download share. Zotero highlights
live inside bundled PDFs, so turning bundling off disables Highlights without
changing the saved preference. The chosen format and options are remembered
in `localStorage` (`gamma-export-opts`).

Export starts the export as a background job ([tasks.md](tasks.md)).
`exportJobBody` turns the payload into the job's body: the page or folder,
the server's mode for the format (each format's `mode`: `annotated-pdf`,
`notes-pdf`, `readable`, `obsidian`, `logseq-graph`, `zotero-rdf`, `bibtex`,
`gamma`)
and the three flags. The dialog then shows its last step, the job's
(`ExportJobStep`). While the job runs it shows its progress (pages, then
the packing of the zip) with Stop, and says the window may close. The job
goes on in Background tasks, whose row opens this step again. Once ready,
the file downloads by itself when the window is open; a closed window's
file is offered in the pill with a Download button, and waits in the tray.
The finished step names the file and its size, says "Saved to your
downloads" once the automatic download has happened (`job.downloaded`;
the button then reads "Download again"), counts the pages, lists the
pages left out with the reason, and says what to do next: Zotero's steps,
Obsidian's unzip-into-a-vault, Gamma's Import → Gamma export, BibTeX's
beside-your-.tex. A failed or
stopped export offers Start again. A share view has no background tasks:
its Export downloads through the endpoints below, with the same mode and
flags as query parameters.

The switches mean, per endpoint:
`/pages/{id}/export?mode=readable&highlights=&notes=&pdf=` (Markdown,
`render_readable` in `markdown_export.py`; dropping highlights keeps a
highlight block's own text as a plain bullet; the front matter carries the
page's folder path relative to the exported folder so the zip re-imports
into the same tree; image sizes export in the
Obsidian dialect — `obsidian_image_sizes` rewrites any legacy `{:width N}`
to `![alt|N](url)`. Block links resolve against the export set
(`resolve_block_links` + `_MarkdownBuilder.begin`'s page-id → filename map):
a `[[ref]]` or PDF link region whose target page is in the same export
becomes a relative link to that page's .md — so a folder zip is
self-contained — and reads as plain text otherwise; a `![[embed]]`
materializes the synced block's content with a *(from …)* attribution,
nested embeds degrading to mentions; ids the resolver doesn't know stay as
typed) and
`mode=annotated-pdf` (`/pages/{id}/export-pdf?highlights=&notes=` answers the
same). "Annotated PDF" is the paper itself and is hidden when there is none
(a note page); a page with sheets of paper shows it too, and exports them
([notebooks.md](notebooks.md)). An unsaved proxy PDF can
export only its original file, so it skips the options page and exports directly.
"PDF" in the Notes row (`?mode=notes-pdf`) takes over as the fallback format, and its
Bundle switch is hidden because a document always embeds its images. Two
combinations are special: a
Logseq graph is defined by carrying both layers, so highlights and notes are
always included and only file bundling gets a switch; a PDF with both off is the stored file itself, which the frontend
downloads from the viewer's own URL (so it also works for a PDF that only
exists behind the proxy).

The dialog can also target a whole folder: opened from home with a folder open
(the ⋮ Export… entry) or from a folder card's context menu (`exportFolder`
state in App.jsx), it exports every page filed there or below, in any
format. There Annotated PDF and BibTeX sit in their own "Papers" row: each
paper's annotated PDF, with the Highlights and Notes switches, in one zip
whose directories are the subfolders; pages without a PDF are left out and
listed once it is done. BibTeX is one `.bib` for every paper in the folder.

## BibTeX bibliography

`?mode=bibtex` on both export endpoints (`_BibtexBuilder`, entries from
`gamma/bibtex.py`): one `.bib` file, never a zip — a page's own citation
entry, or one bibliography for every paper in a folder. This is what a LaTeX
document cites, so the export has no switches and no bundling.

- **Where an entry comes from**: `properties.bibtex`, the rendering the
  metadata lookup cached (a registrar's own BibTeX when doi.org served one,
  else `bibtex.build_entry` over `properties.meta`). A page that has a record
  but no rendering — an older page, a hand-made one — is rendered on the
  spot. A page with no metadata at all is skipped with "page has no paper
  metadata" and named in the finished export; a set where no page has any
  fails with "none of these pages has paper metadata to cite".
- **Citation keys**: generated as first author's surname + year
  (`bibtex.default_key`; it must not change, because cached entries carry
  it). Two papers by one author in one year would collide in a
  bibliography, so `bibtex.unique_keys` suffixes clashes `a`, `b`, … `z`,
  `aa` — Better BibTeX's convention. Pinned keys are assigned first, so the
  key a user chose is never the one that moves; two pins that collide are
  still made distinct rather than writing a broken file.
- **A pinned key** is `properties.cite_key`, set in the metadata popover's
  Cite key row (empty shows the generated key as its placeholder). It lives
  *beside* `meta`, not inside it, because a refetch replaces the record and
  must not take the key with it: the user's `.tex` files cite that key. It
  outranks both the generated key and a registrar's own, is cleaned of what
  BibTeX breaks on (`bibtex.clean_key`: whitespace, `, { } ( ) = \ " # % ~`),
  and clearing the record clears the pin too. The metadata lookup, a hand
  edit, the AI `cite` tool and this export all read the same pin.
  A Zotero import brings one along when Better BibTeX left a
  `Citation Key:` line in the item's Extra field
  (`zotero_import._citation_key`, carried as RDF `dc:description`), so a
  migrated library keeps citing papers by the names its documents use.
- **Order**: sorted by citation key, then title. An unchanged library
  re-exports byte-identically — the header comment counts the entries and
  names the folder but carries no timestamp — so a `.bib` kept in a
  repository or refreshed from a link shows a diff only when the metadata
  changed.
- **Only page roots are read.** `_BibtexBuilder.roots_only` tells the driver
  to hand over the page's own row instead of walking its subtree, so a whole
  library's bibliography is one query per page rather than a tree fetch each.
  That is what lets the dialog preview it synchronously.

### The review step

`GET /api/bibliography?page_id=|folder=` (a folder id) answers what the export *would*
write, as data: `entries` (per citable page: `page_id`, the page `title`, the
`key` the file will use, `pinned`, and the entry `text`), `skipped` (the
pages left out, with the reason) and `text` (the file). It runs the same
builder as the download — `keyed_records()` and `text()` serve both — so the
review cannot disagree with the file, and `preview()` returns an empty
bibliography as data where `save` would refuse, so the dialog can show the
pages it could not cite instead of an error.

The dialog's BibTeX step is the two panes the Zotero import review uses
(`.bibColumns`, the same shape as `.importReviewColumns` with its own
classes): the papers on the left, each with the citation key underneath and a
pin mark when that key is pinned, the pages that cannot be cited listed under
them with the reason. The right pane holds whichever
paper is picked, with a copy button for that one entry; the footer's "Copy
all" takes the whole bibliography and appears only when there is more than
one. The modal widens for this step only (`transferModalWide`). Export is
held while the review loads, when it fails and when nothing is citable.

### Keeping a .bib up to date

The export endpoint answers the *current* bibliography on every request and
accepts a share token, so a folder share link doubles as a fixed URL:

```
<origin>/api/folders/<folder id>/export?mode=bibtex&share=<token>
```

That is what Overleaf's Upload → From External URL (and its Refresh button),
a Makefile or a cron'd `curl` can pull, which is the job Better BibTeX's
auto-export does in Zotero — without a file watcher, because the server
renders on demand. The token names the workspace, so the URL carries neither
a session nor a `ws=`. The BibTeX step shows the link under "Keep this .bib
up to date" when the page or folder is already shared, and otherwise points
at Share: creating the link publishes the pages, and that choice (with its
audience) belongs to the share popover, not to an export dialog.

## Obsidian vault export

`?mode=obsidian` on both export endpoints (`_ObsidianBuilder` →
`gamma/obsidian_export.py`) writes a zip that IS a vault: unzip it into an
existing vault or open the folder as one. The readable Markdown export is
for reading anywhere; this one speaks Obsidian's dialect so links, embeds and
attachments work inside the app (what Obsidian expects and why:
[docs/research/obsidian.md](../research/obsidian.md)). Always a zip, even for
one page.

- **Files**: `<dir>/<Title>.md`, the directory tree = the page's first folder
  path relative to the exported folder (a single page keeps its whole
  path), one directory per folder name. `vault_name` strips what Obsidian refuses in a name
  (`* " \ / < > : | ? # ^ [ ]`, leading dots); same-named pages in one
  directory get ` 2`, ` 3` suffixes. No `# Title` H1 — the filename is the
  title, and a `title:` property is written only when the name had to be
  sanitised. `.obsidian/app.json` (attachment folder = `attachments/`) marks
  the zip as a vault, which is also how the importer recognises it.
- **Front matter**: `tags` (the page's labels by name), `aliases`, `source`
  (the bundled PDF as a quoted wikilink `"[[Paper.pdf]]"`, else the URL),
  `doi`, `authors` (list), `year`; then the BibTeX fence as in the readable
  export.
- **Outline → document** (the Logseq importer's flattening): a top-level
  heading block is a heading and its children follow as document content;
  a top-level leaf block is a paragraph; a top-level block with children is
  a list item with its subtree as a nested list (2-space indents,
  multi-line content indented under the bullet). Re-importing therefore
  nests everything after a heading under it — the one lossy step; the Gamma
  format stays the lossless route.
- **Links**: `[[id]]` / `![[id]]` whose target is in the export become
  `[[Title]]` for a page (`[[dir/Title]]` when two exported pages share a
  name — Obsidian's rule), `[[Title#^id]]` / `![[Title#^id]]` for a block
  (`[[#^id]]` on the same page), and the target block gets ` ^id` written:
  on its bullet line in a list, at the end of a paragraph, on its own line
  after a fence / table / quote / callout. `anchor_marker` makes the id
  Obsidian-legal (letters, digits, dashes). Only linked blocks carry
  anchors; `begin` scans the workspace's link-bearing blocks up front so a
  target renders with its anchor even when the linking page comes later.
  Targets outside the export degrade like the readable export (text /
  materialised embed). PDF link regions become `[[Title|label]]` or
  `[label](url)`.
- **Highlights**: a `> [!quote]` callout whose title links the bundled PDF's
  page — `[[Paper.pdf#page=3|p. 3]]` (`p. 3` without the bundle) — then the
  highlight's own note as a paragraph and its children as a list; inside a
  list, `- > quote` with the page link on the next line. The Highlights /
  Notes switches mean what they do in the Markdown export.
- **Attachments**: images as `![alt|300](attachments/<sha>.<ext>)` (the
  form Gamma stores, which Obsidian reads too), the PDF as
  `attachments/<Title>.pdf` (shared by pages of one document); the Bundle
  switch off leaves server links instead.

The vault importer above reads all of this back (titles from filenames,
`^id` anchors and wikilinks into mentions and synced blocks, `tags` into
labels, the quoted `source` into the paper) — `test_obsidian.py` has the
round trip.

## Notes as a PDF document

`?mode=notes-pdf` on both export endpoints (`_NotesPdfBuilder` →
`gamma/pdf_document.py`) typesets the *notes themselves* as a new PDF — the
inverse of the annotated export below, and the only PDF a page without a paper
can produce, so the Export dialog offers it everywhere (a folder export puts
every page in one document, each starting on a fresh sheet).

Each block's markdown is parsed twice: into chunks (headings, paragraphs,
`>` quotes and `> [!type]` callouts, list items, `- [ ]` todos, fenced code,
`---` rules, GFM tables, images — honoring the editor's size, Obsidian
`![alt|300]` or legacy Logseq `{:width N}`, capped at the column —
`![[embed]]` synced blocks, `$$…$$` math) and each
chunk's text into styled inline spans (bold, italic, `code`, strike,
`==mark==`, `[[refs]]`, links, `$…$` math; a backslash before ASCII
punctuation is that character, as CommonMark and the screen read it, so an
escaped `*` or `$` prints as typed). Highlights become quoted passages
with a bar in the highlight's own colour and a `p. N` marker, and the
Highlights/Notes switches mean exactly what they do in the Markdown export
(drop highlights and a highlight block keeps its own writing as a plain
bullet). Links become real `/Link` annotations, page titles and headings
become PDF bookmarks. Layout constants (A4, margins, sizes) live at the top of
the module.

Tables draw as a real grid: column widths measured from the cells (squeezed
proportionally into the column when too wide), wrapped cells, `:---:`
alignment honored, the header bold on a tint and repeated when a page break
falls inside the table. Fenced code is a bordered tinted card, one card
segment per page it spans. `[[refs]]` and `![[embeds]]` resolve through a
`resolve_ref` callback (`_block_ref_resolver` in `routers/export.py` — its own
sqlite connection, since the request's closes before `response()` runs): a ref
reads as its target's first line in link colour, an embed renders the synced
block's content as a card with a soft bar and a muted `from <page>` source
line (nested embeds degrade to refs so transclusion can't recurse).

Pagination is per line, not per block: the canvas breaks a page between lines
so nothing is ever clipped, and code lines carry their leading whitespace as an
x offset because wrapping drops spaces at the start of a line.

## Text boxes in the exports

A text box ([text_boxes.md](text_boxes.md)) is the user's writing placed on
a page, so every text export writes it as a note, and none takes it for a
highlight: it has no `pdf_position` (`highlights.is_highlight` refuses a
box even with one). A box on a PDF page also says which
page (`text_box.box_page`); a box on a sheet says nothing more, and neither
does a box on no page. The nearest sheet wins, as on screen: the recursive
writers pass down whether they walked through a sheet, so a box under one
names no page whatever its `pdf_page` says. The Highlights switch leaves
boxes in, the Notes switch takes them out with the other notes. The
annotated PDF writes them with either switch on (below).

- **Markdown** (`markdown_export.py`): a note bullet, then the page line a
  highlight carries (two-space indent, `` `p.N` ``).
- **Obsidian** (`obsidian_export.py`): the text as a note, never a
  highlight's `[!quote]` callout, followed by the page link
  `[[Paper.pdf#page=N|p. N]]` (`p. N`
  without the bundle). At the top level the link is a paragraph of its
  own, since a line straight after a list, a quote or a table would run
  into it; in a list it is a line under the bullet.
- **Logseq graph** (`logseq_graph_export.py`): a plain block with no page.
  Logseq keeps a page (`hl-page`) only on annotation blocks, and
  `collect_highlights` takes highlights only (`is_highlight`), so a box
  never reaches the hls page, the EDN file or the area crops.
- **Zotero RDF** (`zotero_export.py`): a note (`bib:Memo`). A box on a PDF
  page opens with a bold "Text box on p.N" line, as a highlight's memo
  opens with its page. The bundled PDF copies carry highlights only, so a
  box reaches Zotero once, as the note.
- **Notes as PDF** (`pdf_document.py`): a small grey "text box, p. N" line,
  like the "handwriting, p. N" line and kept with the text, then the text
  in the normal note style.
- **Annotated PDF**: a `/FreeText` (below), with either switch on, since
  `notes=0`, that format's default, means "don't paint the notes on the
  page". The sheet export draws a sheet's boxes as page content.

## The shared typesetting engine

`gamma/pdf_typeset.py` is what both PDF writers draw with — font choice per
character (Helvetica in four styles, Courier, Symbol, the non-embedded
STSong-Light CID font), AFM widths, span resolution through `vector_text`,
tokenizing, wrapping and the content-stream operators. Spans are
`(kind, payload, level, style)`; `style` is a `Style(bits, href)`, so the note
boxes pass `PLAIN` and the document passes emphasis and link targets through
the same layout code. Everything is laid out in the y-down display frame and
flipped into user space by one `cm`. `pdf_image.XObjectStore` is the shared
upload → image-XObject registry.

## Annotated-PDF export

Both the annotation and visible-note writers read through `ExportPdfReader`.
PyPDF2 reads a dangling indirect reference as `None` and its writer then
fails with an empty `AssertionError`; `ExportPdfReader` substitutes a
`NullObject`. The stored original is unchanged.

`/api/pages/{id}/export-pdf`: highlights become standard `/Highlight` (or
`/Square` for area notes) annotations with the note text in the popup
(`gamma/pdf_export.py`) — `?highlights=0` skips that layer entirely. Every
`/Square` carries an `/NM` id (`Zotero-<key>`, deterministic from the block
id): Zotero's pdf-worker maps `/Square`→image annotation but silently DROPS
one without an id, while `/Highlight` imports id-less — without `/NM`, area
notes vanish in Zotero. It also carries an appearance stream (`/AP /N`)
drawing the viewer's look — a multiply wash at a quarter of the colour's
alpha under a 2pt border — because a viewer synthesizing the box from
`/Rect` + `/C` + `/BS` draws only the outline. No `/IC`: a viewer that
regenerates from it would fill at the full `/CA` and hide the figure.

Every writer here maps through the page's view box (`page_frame`), the frame
the viewer stores positions in (scaled by the position's `width` /
`height`, the page as measured when it was taken): the crop box clipped to the media box, as
pdf.js, pdfium and MuPDF show a page, or the media box when the two do not
meet. `pdf_notes` places its notes in the same frame. A popup's text
(`highlight_note_text`) is the annotation's comment and the notes under it.
A text box on a PDF page among them is left out with the notes under it,
since the box is written as its own `/FreeText`. A box under a sheet stays
a line: the nearest sheet wins.

Handwriting blocks (`ink_url`) become `/Ink` annotations: one per look
bucket (colour × tool × size × opacity) of the group, `/InkList` polylines
mapped through the same rect → user-space conversion, `/BS /W` the mean
drawn width, the caption on the first, an `/NM`, and a private `/GammaInk`
string holding the bucket's `gamma-ink` strokes for a lossless re-import.
Same skip rule as highlights for ink still embedded in the file.

Text boxes on the PDF's pages (`_collect_text_boxes`: a positive-integer
`pdf_page`, held by no sheet, with text) become `/FreeText` annotations.
They are written before the ink and the highlights, so they sit under
them, as on screen. The appearance (`/AP /N`) typesets the box's Markdown
with the shared engine (`text_box.pdf_ops`), upright on a turned page.
`/Contents` is the plain text, references read through the notes PDF's
`_block_ref_resolver`. A private `/GammaTextBox` holds the Markdown and the
box for a lossless re-import. Each key, and why, is in
[text_boxes.md](text_boxes.md#server-and-interchange).

Unlike the other kinds, a box imported from the file and still embedded in
it is written as Gamma has it. Its original (matched by its
`imported_annot` key, `annotate_pdf`'s `replaced`) leaves the copy with its
popup and thread, so an edit made here reaches the export. A box deleted
here leaves its original, since nothing records the deletion. The boxes
are written with either switch on: `highlights=0&notes=1` keeps them with
the painted notes, and only both off (the stored file) leaves them out.

A page with sheets of paper and no PDF has none to annotate:
`annotated_pdf` writes `notebook.notebook_pdf` instead. Each sheet is a PDF page of its paper's
size, painted with the paper, its text boxes typeset on it as real,
selectable text, and the ink groups under it drawn over them as vectors
in the content (the page is the drawing, so no `/FreeText` or `/Ink`
layer). The switches do not apply.

### Notes drawn on the page

`?notes=1` adds a second layer from `gamma/pdf_notes.py` — every non-empty note
is *drawn on the page*, in the nearest patch of empty space, with a leader line
back to its highlight. Free space comes from pdfium page-object bounds
rasterized into an occupancy grid (display space, top-left origin, /Rotate
applied — same frame the viewer stores rects in) with a summed-area table
behind the candidate search; already-placed boxes are marked occupied so notes
never collide.

Notes are markdown, so `gamma/note_markup.py` splits each one into text spans
(`(TEXT, str, level)`, level ±1 = real super/subscript), inline-math spans,
display-math items and image items first; markdown emphasis/links/code are
stripped.

### Vector text (math and CJK) → Type 3 fonts

`gamma/vector_text.py` lays out what the base-14 fonts can't: `math()`
typesets LaTeX with ziamath, `glyphs()` shapes CJK per character with ziafont
(a *plain .ttf* — ziafont can't open the .ttc collections most CJK font
packages ship, hence `fonts-droid-fallback` in the Dockerfile; without it CJK
falls back to the non-embedded CID font, which pdf.js renders as latin
gibberish). Both return a `Drawing`: the **glyph placements** (which ziafont
glyph, standing for which character, at which baseline point and size) and,
separately, path ops for the non-glyph shapes (fraction bars, radical
vincula, `\boxed{}` frames). ziamath is never asked for SVG — a flattened
`<path>` has lost the glyph's identity — but for its layout tree, which
`_walk` traverses exactly as ziamath's own `draw()` would (`nodexy` offsets,
phantoms skipped, stretched delimiters split into the MATH-assembly parts
they are built from); only the bar/box/strike leaves draw into a scratch SVG
that becomes path ops. `_paint` honours each shape's `fill`/`stroke`/`fill-rule`:
`\boxed{}` is a *stroked, unfilled* rect, and painting it solid turns the whole
equation into a black slab. SVG's y-down axis matches the display frame, so
positions drop in with a translate/scale; inline math and CJK sit on the text
baseline, `$$…$$` gets a centred row, and a box that had to shrink an
equation or picture loses to a wider candidate.

`gamma/pdf_glyphs.py` turns the placements into text. One `GlyphFonts` per
document builds **Type 3 fonts** — fonts whose glyph programs are PDF path
operators — from the same outlines: each distinct glyph is one `CharProc`
stored once per document (in the source font's own units, `FontMatrix` =
1/unitsPerEm, so one program serves every size), `Widths` come from the font's
advances, and a `/ToUnicode` CMap maps each code back to the character the
layout said it drew (the font's cmap as fallback), so the equation is
selectable, searchable and copies out as `α`, `∑`, `x`. `draw()` emits the
glyphs of a drawing as `Tf`/`TJ` runs — consecutive glyphs on one baseline
become a single `TJ` whose adjustments carry the exact layout positions — and
allocates codes as it meets new glyphs; a font takes 255 codes (single-byte),
then a second resource (`GmT30`, `GmT31`, …) opens. Font dictionaries are
allocated as indirect objects up front so pages (including the overlays
`pdf_notes` merges mid-way) can reference them, and `finalize()` fills them
in before the writer serialises — both writers call it last. Glyph programs
use `d1` (shape-only), so they take the fill colour in force where they are
shown. `vector_text.header` sets it to the caller's colour
(`draw(drawing, color)`, which `pdf_typeset.draw_spans` passes on), by
default the engine's near-black (`TEXT_COLOR`). So math and CJK in a
coloured text box, a quote or a muted line take that line's colour.
Nothing is rasterised and no font file is shipped; compared with
drawing every occurrence as filled paths the file shrinks (a repeated glyph
costs two bytes) and the text layer appears; every writer that draws MATH
spans owns a `GlyphFonts`, there is no path-only fallback.

Known upstream limit: ziamath 0.13 stretches `\left(…\right)` around a
`\sum`/`\int` with a runaway MATH-assembly (hundreds of extender parts, a
parenthesis ~2000 pt tall); `_pieces` refuses an assembly of more than
`MAX_ASSEMBLY_PARTS`, so such an expression takes the text fallback instead
of a page-tall bracket. When ziamath is missing or chokes,
`note_markup.latex_spans` falls back to a unicode approximation
(`\frac{a}{b}` → `a/b`, unknown commands keep their name so `\sin` works) —
tests cover both fallbacks.

### Images

`gamma/pdf_image.py` embeds `![](/api/uploads/…)` refs as image XObjects — JPEG
verbatim, 8-bit gray/RGB/palette PNG verbatim too (PDF's `/Predictor 15` IS PNG
row filtering), alpha/16-bit PNG unfiltered in Python onto white (hence
`MAX_PIXELS`). A palette's `/Indexed` lookup must be a `ByteStringObject`: as a
text string PyPDF2 re-encodes it to UTF-16 and the picture comes out one flat
colour.

### Fonts and content streams

Text is a hand-built content stream merged with `merge_page` using three fonts
every viewer has: Helvetica (WinAnsi), Symbol (Greek/math —
`pdf_typeset.SYMBOL` holds codes AND advance widths measured from the font
itself; every `note_markup.SYMBOLS` value must be drawable by one of the three,
which a test enforces), and a non-embedded STSong-Light CID font for CJK —
plus the per-document Type 3 fonts above for typeset math and CJK outlines.
Deliberately no reportlab/Pillow dependency. PyPDF2 leaves merged content
inline in the page dict; it must be re-added as an indirect object or the file
is unreadable. The document writer's page streams and every glyph program are
Flate-compressed (`flate_encode`); the overlay streams `merge_page` produces
stay as PyPDF2 leaves them.
