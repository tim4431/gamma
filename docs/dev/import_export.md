# Import and export

The View menu's (≡) Import…/Export… dialogs and every pipeline behind them: embedded
PDF annotations, Logseq graphs, Zotero libraries, Markdown notes (Obsidian
vaults, Notion exports), Notability notes, GoodNotes notebooks, Markdown and Obsidian vault export, the notes
typeset as their own PDF, the BibTeX bibliography, and the annotated-PDF
writer (its internals and the shared typesetting engine are in
[pdf_typesetting.md](pdf_typesetting.md)). Code: `gamma/routers/imports.py`, `gamma/zotero_import.py`,
`gamma/zotero_export.py`, `gamma/zotero_notes.py`, `gamma/logseq_import.py`, `gamma/markdown_import.py`,
`gamma/markdown_zip_import.py`, `gamma/import_zip.py` (the zip walk the library imports share),
`gamma/notability_import.py`, `gamma/notability.py`, `gamma/notability_archive.py`,
`gamma/notability_map.py`, `gamma/notability_export.py`, `gamma/notability_write.py`,
`gamma/note_app_import.py` (what the Notability and GoodNotes imports share), `gamma/goodnotes_import.py`,
`gamma/goodnotes.py`, `gamma/goodnotes_codec.py`, `gamma/goodnotes_map.py`, `gamma/rtf.py`, `gamma/markdown_export.py`, `gamma/obsidian_export.py`, `gamma/pdf_export.py`,
`gamma/pdf_notes.py`, `gamma/pdf_document.py`, `gamma/pdf_typeset.py`,
`gamma/note_markup.py`, `gamma/vector_text.py`, `gamma/pdf_glyphs.py`,
`gamma/pdf_image.py`, `gamma/text_box.py`, `gamma/picture.py`, `gamma/bibtex.py`,
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
keys; opacity honors the annotation's `/GammaAlpha`, else its `/CA`, so a
Gamma export → re-import round-trips exact colors (a Zotero copy keeps its
alpha in the first, [below](#zotero-rdf-export)), and each channel is rounded, not truncated (an
app that writes 0.831 means 212); PyPDF2 dict access returns `IndirectObject`s, always
`.get_object()` them. `/Ink` (freehand drawings from any PDF app, or a Gamma
export) becomes a handwriting block: the strokes are stored as an `.ink`
upload (`gamma/ink.py`), the block gets `ink_url` / `ink_strokes` /
`pdf_position`. A Gamma export's private `/GammaInk` key restores pressure,
time, the dash, a calligraphy pen's nib and a closed shape's record and
fill. An export writes a filled shape as its outline's `/Ink` alone, never
a `/Square`, `/Circle` or `/Polygon`. Foreign ink is polylines at the
annotation's width, dashed when its border style is (`/BS /S /D`)
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

**Image stamps.** A `/Stamp` becomes a picture block (`picture`,
`pdf_page`; [pictures.md](pictures.md#server-and-interchange)) when its
appearance draws one image and no text (`_picture_from_annotation`). The
appearance is `/AP /N`, or the state `/AS` names when `/N` holds several.
Its resources, nested forms included, must name exactly one image XObject
that is no image mask, and no font. A rubber stamp of words or of
vectors alone makes nothing. The key is `annotation_key` with the `/Stamp`
subtype, and replies come in under the picture as under any imported
annotation.

- **A Gamma export's stamp** carries `/GammaPicture`
  (`{v, picture, caption, text}`), which restores the picture: its size,
  crop, turn, flips and Markdown caption. Its place is the centre of
  `/Rect` through the page's view box and rotation, the inverse of the
  export's mapping, so a move in another viewer holds. When `/Contents` on
  one line differs from the `text` written, `/Contents` wins, escaped as
  plain text, as a text box's does.
- **A foreign stamp** is the whole image, where its appearance draws it,
  as a viewer draws an annotation (ISO 32000-1, 12.5.5): the form's
  `/BBox` through its `/Matrix` fitted to `/Rect`, then the `cm` its
  content draws the image through (`_Drawn`, nested forms followed), then
  the page's turn. A NoRotate stamp stays upright, pinned at its `/Rect`'s
  top-left. The frame comes from where the image's corners land
  (`_frame_of`): its sides, its turn from the top side, and `flip_h` when
  the corners run the other way round. A skew is lost. Its `/Contents` is
  the caption, escaped as plain text.
- Either is kept on the page (`picture.keep_on_surface`).

**A stamp's file** is read only for the stamps the import adds, once per
image however many stamps draw it, and before the write lock
(`_PictureFiles`, `pdf_image.image_file`). The PDF is untrusted, so every
read is bounded:

- `pdf_image.stream_data` inflates a `/FlateDecode` by hand, never past the
  size the image needs. PyPDF2's `get_data` would inflate it whole and keep
  the result on the reader.
- An image is opened only as the format its filter names (JPEG, JPEG 2000,
  or a rebuilt PNG). It is refused when its header's size is not its
  `/Width` and `/Height`, before anything is decoded.
- What is decoded counts against `MAX_DECODED_PIXELS` (40 MP an image) and
  one `DecodeBudget` for the whole import (`MAX_IMPORT_PIXELS`, 200 MP), so
  a small PDF that draws thousands of large images cannot hold the import
  for minutes.
- At most 1 MB of appearance content is read per PDF to find where its
  images are drawn. Past that, an image is taken to fill its form's
  `/BBox`.

What a stamp's image is stored as:

- **A `/DCTDecode` image** without a soft mask is the JPEG it holds. Its
  bytes stay as they are when their EXIF orientation is the one the PDF
  draws it through: the `/GammaOrientation` of the form a Gamma export
  draws it in, else 1, since PDF viewers draw an image as stored.
  Otherwise the orientation tag is set to that one in place, or, with no
  tag to set, the image is decoded.
- **A Flate image with PNG row filters** (`/Predictor` 10 to 15), gray,
  RGB or a palette, without a soft mask and drawn as stored, is the PNG it
  was made from, its compressed rows as they are.
- **Anything else**, a JPEG with a soft mask among them, is decoded with
  Pillow and stored as a PNG, its `/SMask` the alpha channel, turned as
  the PDF draws it. Gray, RGB, CMYK, ICC-based and indexed spaces read. An
  image mask, a Lab, Separation or DeviceN space, a filter other than
  Flate, DCT and JPX, and a predictor it cannot rebuild do not.

The stored file (`_picture_url`) is the one a Gamma export drew the
picture from when the workspace has it at the size of the image read back.
So a round trip keeps the file, a GIF's animation with it. Otherwise the
image is stored under its content hash. A stamp whose image does not read
or is past the budget makes no block, and its replies none either. It is
logged, and its annotation stays in the file for a later import. A full
workspace (507) stops the import, as it does for the handwriting's files.

A text highlight's quote is the text under its quads, read through MuPDF
(`pdf_text.GlyphPages`, each page once, under the extraction lock;
`pdf_text.quote_under`). A glyph is a character of MuPDF's raw text
dictionary with its box mapped back to PDF user space, the space the quads
are in (the page is read unrotated for this: on a rotated page PyMuPDF's own
transform forgets a cropped page box's offset); the box is the glyph's
advance by the font's ascender and descender, so a thin "i" or "." has a
centre as sound as any letter's, and after each of MuPDF's lines comes a
line break without a box. A glyph counts when its centre lies in a quad, so
a highlight that starts mid-line quotes from there, one across lines quotes
both lines joined by a space (a word hyphenated across them keeps its
hyphen, as MuPDF extracts it), and a quad's edge never drags in the glyph it
touches. A file MuPDF can't open imports its highlights without quotes.
PyPDF2's text visitor is no use for this: it reports each line at the
position of the line after it, which once quoted the line above a highlight.

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
the original. Every import follows the preference: the View menu's
"Import…" dialog, the auto-import on open and the desktop app's open (it
asks nothing per run). The strip (`_strip_embedded_annotations`) removes the
annotations the import makes blocks of, with their threads (the replies
and review states that answer them) and their `/Popup` windows
(`pdf_export.drop_annotations`). Links, kinds not imported, hidden ones
and those that make no block (a note with no text) stay, and so does a
stamp the import left out (`keep`). The strip reads no image. The blocks
whose originals it removed get `properties.annot_stripped`, which tells
`/export-pdf` to write them again. The export skips `imported_annot`
highlights and ink only while the original is embedded
(`pdf_export.still_embedded`). A text box or a picture
it writes either way, replacing the embedded original
([pdf_typesetting.md](pdf_typesetting.md#annotated-pdf-export)).

Replacing the page's PDF with another file (`PUT /pages/{id}/attachment`)
marks every block imported from the old one the same way
(`imports.originals_gone`), since the originals left with that file. An
import then keeps the mark in step with the file. It sets the mark where
its strip removed the original. It takes the mark off where the file holds
the original again, so a replacement that carries the same annotations
draws them once. Only the page's own PDF counts for that, read by an
import that does not strip it, and unchanged since the read: an import of
the page's previous file, or one whose file another import stripped
meanwhile, takes no mark off. The check and the marks run under the write
lock.

## The Import dialog

The View menu's single "Import…" entry → `ImportDialog` in `transfers/ImportExport.jsx`, the
export dialog's counterpart. Step one is a source card: annotations embedded
in this PDF, a Logseq .pdf + .edn, a Zotero library (its export folder or a
.zip of it), Markdown notes (one
`.md` or a `.zip` such as a Notion export), Notability notes (one or more
`.note` files, or a `.zip` of them), GoodNotes notebooks (one or more
`.goodnotes` files, or a `.zip` of them), or a Gamma export .zip. Double-click
or Next confirms. Only Zotero gets a review step, its export guide.
Annotations in this PDF imports at once, and it and Zotero (for the
annotations inside its exported PDFs) send the strip preference above.
Markdown, Notability, GoodNotes, Logseq and Gamma open the
file picker directly, with their preparation notes under the selected card.
`resolveImport` in `transfers/transferFormats.js` decides both.
The Notability and GoodNotes pickers take several files, which go up as one
`.zip` (`folderZip.js` `filesUpload`, a name picked twice numbered), and take
any file on an iPhone or iPad, whose Safari greys out a `.note` or a
`.goodnotes`.
Zotero is the default source (a numbered step guide reusing
settingsKit's `Step`); with a PDF open, that PDF's own annotations win.

The iPad app opens the dialog with a `.zip` already chosen, one shared to
it from another app ([ipad.md "Opened files"](ipad.md#opened-files)). The
dialog is then titled with the file's name and asks which export it is.
It offers only the sources that read an archive (`archive` in
`resolveImport`: Zotero, Markdown, Notability, GoodNotes and Gamma). Its
button is *Import*, and the file goes straight to that source's review
with no picker. A `.note` or `.ntb` shared to the app skips the dialog and
opens the Notability review, a `.goodnotes` the GoodNotes review.

Zotero, Markdown (ZIP or one file), Obsidian/Notion ZIPs, Notability notes,
GoodNotes notebooks and Gamma exports share one review flow: Upload → Review →
Import → Summary.

- `POST /api/import/review` takes the file, its `source` (`zotero`,
  `markdown-zip`, `markdown-file`, `gamma`, `notability`, `goodnotes`), an optional `folder` (the
  destination folder's id, `""` the library's top; 400 when it is no
  folder, `import_review.destination`) and `strip`.
  It stages the upload, runs the source's preview and returns the two trees
  plus a `review_id`; `DELETE` discards it. An upload past 32 MiB comes in
  parts instead (`POST /api/import/review/parts`, [api.md](api.md)), the
  protocol of a PDF's upload in parts ([user_db.md](user_db.md) "Stored
  files"), since a proxy in front of the server caps a request's body
  (Cloudflare at 100 MB). The parts are appended to the staged upload, and
  finishing answers with the preview, as the single request does.
- The import itself is a background job ([tasks.md](tasks.md)):
  `POST /api/jobs/import` with `{review_id, selected: [...]}` imports from
  the staged upload against the current library, reporting each item; its
  result is the import report. Asking again for the same review answers the
  job already started (409 with another selection).
- Each source is a request-free preview and commit in `routers/imports.py`
  (`_review_source`), over the staged file and the review's metadata; the
  plain endpoints (`/import/zotero`, `/import/markdown-zip`, …) run the
  same functions in the request. A preview may return a `stage`, what it
  learned that the commit can reuse (a Zotero review's PDF digests):
  `_preview_staged` adds it to the review's metadata
  (`import_staging.update`), and no answer carries it.
- Staging (`gamma/import_staging.py`): a directory per review, bound to the
  account and workspace, expiring after two hours (`TTL`; expired ones are
  swept on the next upload). The import job holds a claim (`running/`
  mkdir; a `DELETE` meanwhile answers 409) while it reads the upload, and
  removes the review when it ends — done, failed or stopped. Staging has
  no size cap and is not metered by the account quota; an upload that would
  leave the disk with less than 1 GB free is refused (507,
  `import_staging.MIN_FREE_BYTES`). A review expires two hours after its
  last activity (a part, a claim, the dialog's keep-alive, the import's
  start), so a long upload in parts does not run out. Until the next
  upload's sweep removes it, an expired review comes back on a keep-alive
  or the import's start (`import_staging.touch`); once swept, both answer
  404.
- Selection ids are source ids (`page:<id>` / `chat:<id>` for a Gamma
  export, Zotero item keys, ...); duplicate Zotero records share
  `selection_ids`. `parse_selection` (`gamma/import_review.py`): `None`
  (no field) imports everything, `[]` imports nothing.
- The two columns answer each other: each page names the upload's members
  it comes from in `source_paths`, exactly as `entries` names them, its own
  file first. That is a Zotero item's PDF and then its other attachments,
  a note, a Notability note, or a Gamma export's `pages.db` and uploads.
  A note found in a zip inside the upload names that zip
  (`import_zip.ZipEntry.outer`), and `archive_entries` names members as
  the zip walk decodes them (`import_zip.entry_name`). A Zotero job's
  report keeps the paths too, so the summary's columns still link.
- A stopped import keeps what it wrote: the Zotero and Markdown imports
  write the new pages whose files they already stored, then stop.
- Frontend (`src/transfers/`): `ImportReviewDialog.jsx` owns the steps and
  the report. It uploads and reviews itself, then follows the import job
  (`tasks.start("import", …)`) with its progress and Stop. It may close while
  the job runs, and the job's row in Background tasks opens it again on that
  job (`{jobId}`, no file: the summary then shows the library column only).
  Until Import, the upload and the review are the dialog's own, not a
  task: a close by the backdrop, Escape or × asks first, since it stops
  the upload or drops the staged review (or the choices of one gone); its
  Cancel stays direct. While it shows a review, the dialog sends a
  keep-alive every 10 minutes and when the window regains focus or becomes
  visible. A review gone anyway (404 once swept, from the keep-alive or
  from Import) is cleared with a message, and Retry preview uploads the
  file the dialog still holds again, keeping the selection. A preview the server refuses (400, 413, 415: a file the source
  does not read, such as a Supernote `.note`, or one too large) shows its reason
  without the retry a lost connection gets. The source column is headed by
  what was uploaded: one file, a ZIP's contents, a folder, or the files
  picked together.
  `ImportTree.jsx` renders one tree with its checkboxes (a folder toggles
  its descendants). A tree of more than 300 files (a vault) starts with its
  folders closed, a closed folder's rows are made only once it opens, and
  a list shows 300 files at a time, so 20,000 notes open and click as fast
  as a few. A row with a match in the other column has its name as a
  button. Choosing it lights the match in both columns and shows the first
  match in the other. The folders on the way open at once, since a folder
  still growing open would leave the row short of where the scroll must
  reach; the reader's own folds keep their motion. A long list grows to
  hold the match, and unless it is already in view it scrolls to the
  middle of its column. With the columns stacked (a phone), that column is
  brought into the dialog's view, clear of the sticky footer. The other
  matches are lit too, and a second click lets go. A match the filter
  hides brings the filter back to All, and a hidden status line reads out
  the row chosen and how many matches it has. Within each of its folders,
  the library column lists its pages in the source column's order, each by
  the first of its files listed there, so the columns line up. Both sort
  folders by name with numbers by value (`files/999` before
  `files/1000`).
  `importReview.js` owns selection, filters, tree construction and the
  links and order (`importLinks`, `orderLikeSource`, `firstRow`; filters
  only hide rows, and hidden selections stay selected);
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

A document past 5 MB, or past 200,000 blocks (`markdown_import.MAX_BLOCKS`),
is refused with a 413 that says which, never cut short. A 5 MB file of real
notes holds about 100,000 blocks (one of 50,000 parses in 0.2 s and imports
in 2.5 s). Only a file of nothing but very short lines gets past the block
cap: a 5 MB one of `- x` lines would be 1.3 million blocks, a minute and
about a gigabyte to write. The editor's paste as blocks
(`POST /api/markdown-blocks`) has the same two caps: a paste past them stays
text, and the status line says so.

## Markdown zips: Obsidian vaults, Notion exports, Gamma exports, zipped notes

Diagram fences (`mermaid` and `svg`) stay as editable Markdown through
import/export and render as diagrams in the frontend. The diagram toolbar can
download SVG separately; backend PDF exports retain code-block output. See
[diagrams.md](diagrams.md).

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
  and Notion's `Export-<uuid>/` wrappers are dropped; zips inside the zip
  (Notion's `Part-N.zip` members of a big export) are read in place, each
  spooled to a temporary file, to two levels deep and up to 500 of them
  (the walk the library imports share, `gamma/import_zip.py`:
  `MAX_ZIP_DEPTH`, `MAX_NESTED_ZIPS`; each holds its file open until the
  import ends), and a warning names every one left closed; `.obsidian/`,
  `.trash/` and `.canvas` files are skipped (a canvas is a warning).
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
  (`store_file_stream`, content-hash dedup, the storage quota — a file over
  it is a warning and the link stays as typed) and points at
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
  the table (`_all.csv` preferred when both exist — it has every row). Every
  row is kept, 500 rows (or 100,000 characters) to a table, each table a
  block of its own under the header, so each renders and stays under a
  block's 200,000 characters. Columns past 40 are left out with a warning.
  Its row pages `Name <id>/Row <id>.md` land in
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
  transaction: `blocks_store.insert_page_tree`, the library imports' bulk
  writer, takes the write lock and touches each page in the change log. Other writers are never shut
  out for the length of an import, the change feed lists each batch as it
  lands ([collab.md](collab.md) "The change feed"), and an import that
  fails half way keeps the batches it finished. (A transaction per page
  made big imports about twice as slow.)
- **No size cap**: neither the zip nor its number of notes is capped; a note
  past 5 MB or 200,000 blocks is a warning and not imported. What guards
  the server instead is a zip-bomb rule, Apache POI's: an entry past 1 MiB
  that unpacks to over 100 times its packed size (`MAX_RATIO`) is not read,
  with a warning, and an archive that does so as a whole is refused (413).
  Notes pack about 4:1 and photos or PDFs 1:1, a zip bomb about 1000:1. A
  bundled file has no size cap: it is streamed into storage, never read
  whole.
  A nested zip is spooled, and a bundled file stored, only while the disk
  keeps `import_staging.MIN_FREE_BYTES` free (507 for the zip, a warning
  for the file), as a staged upload must.

The report's counts (`pages_created`, `pages_skipped`, `assets_stored`,
`links_resolved`, `notion`) and warnings appear in the shared dialog. `pages`
lists selected pages and their destinations, including pages already in the library. The same engine's `preview=True`
mode reads notes and their links and checks assets without writing uploads or
blocks. It parses a note into blocks only when it is long enough to pass the
block cap, and reads no more of a bundled file than a PDF's header (a
20,000-note vault: 4 s, a 7.8 MB answer, 0.7 MB gzipped). An import
parses each new note twice, first for the blocks its links may target (their
ids kept by place in the tree), then when it writes the page, so it never
holds every note's tree at once (20,000 notes, 620,000 blocks: 100 s, at
most 330 MB). Optional
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

`POST /api/import/zotero` (View menu ≡ → Import… → Zotero library): a zip of
Zotero's File → Export Library → "Zotero RDF" (with Export Files/Notes).
A workspace a Zotero link keeps in step with a library through the Web API
([zotero_sync.md](zotero_sync.md)), detached or not, takes no RDF import:
the review's upload (whole or in parts), the preview and the commit answer
409 "This workspace is kept in step with <library>; its items arrive by
themselves." (`imports._refuse_linked`), since the link brings the items
and the import's copies would go back to Zotero as doubles.
`gamma/zotero_import.py` parses the RDF (items with their item type,
publisher and ISBN, journal and book records carrying the DOI, collections,
tags, HTML notes, standalone notes, and what Gamma's record has no place
for, below) and tolerant zip-name lookup
(cp437-mojibake, NFC/NFD, backslashes); the endpoint in `routers/imports.py`
uploads PDFs (dedup + quota per file, over-quota items are skipped not fatal),
upserts pages keyed by file hash then `properties.zotero_key` (re-exports
change bytes — Zotero re-embeds annotations at export time) — an item Gamma
exported (`#gamma_page_<id>`) first by the page it came from, when it is in
this workspace, so a Gamma export brought back merges into its pages even
where another page holds the same PDF, and a note Gamma exported
(`#gamma_note_<id>`) is there as that block (`_zotero_existing_page`;
the preview looks in the same order) —. A key local to one Zotero database
(`#item_<n>`, which two libraries reuse for different works) merges only
into a page of the same work (`zotero_import.same_work`: the same DOI when
both have one, else the same title, casefolded, punctuation as spaces), as
the page's record has it or as the Zotero record it was imported with did
(`zotero_rdf`'s `work`), so a record edited in Gamma still merges. An item
with no DOI retitled in Zotero is another work by this rule: once its PDF's
bytes change too, it comes back as a second page. URL and `urn:isbn:` keys
merge as before. The look-up reads every page
under the key through `idx_ub_zotero_key` ([user_db.md](user_db.md)), so
two such pages can sit side by side. The import maps
collections→folders, nested as in Zotero, made below the destination
folder (the optional `folder` form field, a folder id) where missing — an
item in no collection lands in the destination itself —,
tags→labels (by name, made where missing), notes→block trees (below),
the item's other attachments→file chips and
links under the page's "Attachments" block (below), then runs the
shared `import_embedded_annotations` (reader annotations arrive inside the
exported PDFs; `strip` follows the client's embedded-annotations preference).
Zotero writes each of its kinds as a standard annotation
([research/zotero-annotations.md](../research/zotero-annotations.md)): a
highlight and an underline become highlights (Gamma has no underline; the
quote is the text under it), an image an area highlight, ink handwriting, a
note a text box, and a text annotation a Zotero box, a text box laid out as
Zotero's reader lays it out ([text_boxes.md](text_boxes.md#zotero-boxes)). A text
annotation turned in Zotero
is its unturned `/Zotero:Rect` upright on the same centre, since a box
cannot turn. Zotero writes no `/CA` on markup, so its colours take the
importer's 0.45; its annotation tags (`/Zotero:Tags`) are not kept. A
block made from an annotation Zotero wrote keeps its `/Zotero:Key` (when
it is a valid key, never Gamma's own `/NM`) as
`properties.zotero_annotation`, so the annotation it came from stays named.
In a workspace a Zotero link keeps, an annotation whose `/Zotero:Key` the
link already holds (a `zotero_objects` row) makes no block when the PDF's
embedded annotations are imported (`/api/import/pdf-annotations`, the
import on open): the link brings that annotation itself
([zotero_sync.md](zotero_sync.md#annotations)). Rows no
link keeps (a linked workspace's backup restored where there is no link)
hold nothing back.
Nothing writes it back: Gamma's copies name annotations by block id, and a
key Zotero knows would let it re-parent its own annotation. A duplicated
block drops it, as it drops `imported_annot`.
`test_each_kind_of_zotero_annotation_imports` holds the shapes Zotero 9
writes.
Merging only fills gaps: existing meta/bibtex/files are kept, the folders
and labels union (the page's own ids first, those that still exist —
`existing_in`).
Each item's PDF is stored first, outside any transaction
(`_zotero_prepare`), and so are the pictures in its notes. New pages and
their note trees then go in `ZOTERO_PAGES_PER_COMMIT` (50) at a time, in
one short transaction under the write lock
(`blocks_store.insert_page_tree`), each page touched in the change log and
the uploads it names claimed from the upload GC's orphans
(`upload_gc.claim`) in the same transaction (`_zotero_write_new`);
an item that merges writes the new pages before it first, so the report
keeps the export's order. Each new page is looked for again once the lock
is held: when another import of the same item made it meanwhile (a
double-click, two tabs), or an earlier item of the batch did, this one
merges into it after the commit instead of making a second. The embedded annotations are likewise checked
against the page's `imported_annot` keys under the lock. A merge into an
existing page is an op batch by the importing account (a `set` of the
changed properties plus an `insert` per new block, each parent before its
children; `_insert_ops`), which the page's open tabs and the workspace's
mirrors see like any edit. It is cut at `MAX_OPS` and applied in one
transaction (`apply_batches`), so an item that fails partway leaves its
page as it was.
A re-import that changes nothing writes nothing.

A note is a block tree on its item's page (`gamma/zotero_notes.py`
`note_tree`, read with the standard library's HTML parser): the note's
first block element is the root, which carries the note's key
(`properties.zotero_note`), each later element is a child, and a list item
is a child with its nested list under it. Headings, quotes, code blocks,
`<pre class="math">`, tables and rules become Gamma's markdown for them;
an ordered item keeps its `N.`. Inline, bold, italic, strike, underline,
Zotero's yellow highlight (`==mark==`), colour spans (Gamma's
`<span style>`), code, math and links come in as Gamma writes them,
escaped so the text reads back as typed through `note_markup.INLINE_RE`
(the inline grammar the PDF writers share). A `javascript:`, `vbscript:`
or `data:` link is its text. A paragraph, heading or list item of only
whitespace, no-break spaces included (the `<p>&nbsp;</p>` blank lines of
older Zotero notes), is left out. A block longer than the editor saves
(`ops.MAX_CONTENT`, 200,000 characters) is cut there with a warning, and a
note nested deeper than 200 levels (`zotero_import.NOTE_DEPTH_MAX`, below
what the import's copy and the export can walk) comes in as its text in one
block, with a warning, instead of failing its item. The export's `note_html` is the exact
inverse for every tree the import makes. A picture in a note (a data URI,
its alt and `width`) is stored as an upload when the note comes in
(`store_images`, at most 25 MB each, `NOTE_IMAGE_MAX_BYTES`) and linked as
`![alt|width](/api/uploads/…)`. One of a type Gamma does not store, a
larger one, or one past the quota is "(image)" with a warning, and so is
an image Zotero keeps as an attachment (`data-attachment-key`), which the
RDF names by a key that is not the note's. An image attachment with the
bytes of a note's picture makes no chip.

A standalone note (a `bib:Memo` no item references) is a note page,
titled by its first line as text (`zotero_import._note_page`), filed in its
collections and labelled with its tags, with no PDF, `zotero_key` and
`zotero_note` both its key. The rest of the note is the page's body. When
the note opens with a heading or a paragraph, that block's later lines are
the body's first block. A first block that is a table, code, math or a
quote, or one whose first line holds a picture or a link, which a title
as text would lose, stays whole at the top of the body. The review lists it as a page that misses
nothing, and the report counts it as a page, not in `notes_imported`.
Imported again it is refiled and relabelled only.

What Zotero holds and Gamma's record (`meta`) has no field for travels
with the page as `properties.zotero_rdf`, which only the Zotero RDF export
reads (the item's `rdf`, version 1): the full date, the creators as Zotero
split them (editors too), the publisher with its place, the abstract,
URL, ISSN, issue, journal
abbreviation, Extra, an item type Gamma reads as a document with its RDF
classes, the PDF attachment's title, and every other element of the item
and its container record as Zotero wrote it. Past 64 KB its largest fields
are dropped, with a warning. The import sets it when it sets the page's
record, and later on a page of the same work that keeps none.

The parse also reads an attachment's path from the `rdf:resource` element
older exports wrote when there is no `z:path`, takes a call number
(`dcterms:LCC` in a `dc:subject`) for no tag, takes the container that is
not a `bib:Series` as an item's venue, and reads an arXiv id from
`z:archiveID` and an `arXiv:` line in Extra. Each PDF is hashed as it
streams from the zip, once per archive (`plan_zotero_archive`), and a
review's preview keeps the digests, by entry size and CRC, for its commit
(the `stage` above). A PDF the workspace already stores, the same digest
and size, is not copied again.

The Zotero step leads with "Choose folder…" (the export's folder, as
Zotero leaves it); "or choose a .zip" sits beside it, and the step takes
either dropped on it (`ExportDrop`: one directory entry, walked by
`shared/lib/dropEntries.js` like the library's folder drop, or a `.zip`).
No picker offers both at once: a browser's is a file or a folder picker
(`webkitdirectory`, Electron's too), and so are the Windows and Linux
dialogs. iPhone/iPad Safari has no folder picker, so there ".zip" is the
button. A folder is packed into a ZIP in the page
(`transfers/folderZip.js`: stored entries, one CRC pass over the files,
progress as the "packing" phase; ZIP64 records past 4 GB or 65,535 files),
so the server only ever sees an archive;
neither a pick nor a drop lists empty directories. A drop stops at the
dialog, so the library behind never takes the folder's PDFs as uploads.
Choosing or dropping the export opens the shared import review dialog. Its two trees show
the archive (including empty directories and unused files) and the destination
library (PDF/page, new/update, collection paths, notes, attachments). The active library
folder becomes the import's destination (its id). `POST /api/import/zotero/preview` accepts
the same `file` and `folder`, requires workspace write access, and writes no
pages or uploads. Both endpoints use `plan_zotero_archive`, so attachment
resolution and warnings agree. The staged review flow reuses the upload,
rechecks the current library, and leaves actual results and warnings in the dialog.

- Inputs: standalone and inline attachments of any kind, both MIME
  namespaces, literal or resource paths. Lookup normalizes dot segments,
  percent encoding and ZIP filename encodings. A URL in an item's metadata
  is not an attachment, but a web-link attachment (a `z:Attachment` with a
  URL and no `z:path`) is; an empty directory supplies no bytes. A
  standalone attachment, one no item links, is an item of its own, titled
  by its file name when it has no title.
- `resolve_entry`: when the filename changed, only a unique file in the
  same `files/<attachment-id>` directory is used, and for a PDF only a
  unique PDF; ambiguous or cross-item matches are never guessed.
- One page per item, as in Zotero's item tree. The item's first PDF that
  is in the zip and is a PDF is the page's document. Its other attachments,
  in the export's order, go under the page's `## Attachments` block
  (`properties.zotero_attachments`), made after its notes the first time
  one arrives: each file a file chip, `[name](/api/uploads/<hash>.<ext>)`,
  stored as `POST /upload-file` stores it (`storage.content_refusal`, the
  same refusals) and streamed from the zip, each web link a
  `[title](url)` link; both carry the attachment's key as
  `properties.zotero_attachment`. A PDF repeating the document's bytes is
  no chip, and an executable or a `.ink` is not imported. An attachment
  the page holds already makes no chip: by its key, else a link by its URL
  in a block of the page, and a file by its stored name. That is a name a
  block of the page references (`storage.upload_refs`: an image, a
  picture's file, the page's PDF) or one the import is about to give the
  page: the images in its notes, and, when it attaches the item's PDF, the
  files that PDF's Gamma picture stamps were drawn from (each
  `/GammaPicture`'s `url`, `_stamped_pictures`). So a Gamma export brought
  back adds nothing for its note images or its pictures, whether it merges
  into the page it came from or makes a new one. A file that cannot be
  stored (not what its extension says, past the storage quota) is a
  warning, and the item and its other attachments go on. The report
  counts `attachments_imported`, the preview each page's `attachments`.
  Pages earlier imports made for extra PDFs (keyed `<key>#pdf:<path>`) are
  left as they are; the chip for such a PDF opens that page. Reimporting a
  complete export attaches a recovered PDF to an existing metadata-only
  page.
- Warned: missing or invalid PDFs, missing files, unsupported or unlinked
  files, recovered filenames, existing PDFs kept, attachments that could
  not be stored. The files beside an attachment in its `files/<id>`
  folder (a web snapshot's page resources) are one warning per folder,
  and Zotero's own `.zotero-*` files none.
- An export taken before Zotero finished writing it (`unwritten_file`).
  Zotero writes a `files/<id>/` path only for a file it has, makes the
  folders at once and writes the files after its export window closes: the
  RDF translator calls `saveFile` without awaiting it, and with "Include
  Annotations" each PDF waits its turn in Zotero's PDF worker. A full
  library zipped or picked right away therefore lacks the tail of its PDFs
  (their folders empty), which appear on disk later; one item exported
  alone finishes in time. A missing PDF with nothing under its folder, in an
  archive that has the `files/` tree, says so, and one warning at the top
  asks to wait and import again, which attaches the PDFs to the pages
  already made. A file Zotero doesn't have locally gets no `z:path` at all,
  and an archive of only the `.rdf` has no `files/` tree, so neither looks
  like this.

## Notability notes

`POST /api/import/notability` (View menu ≡ → Import… → Notability notes;
`gamma/notability_import.py`, which reads a note through
`gamma/notability.py` and `gamma/notability_archive.py` and places it through
`gamma/notability_map.py`) imports Notability's "Note" export. A `.note` is a
zip holding one folder: `Session.plist` (the whole note, a keyed archive),
`metadata.plist`, its PDFs, pictures and recordings. It imports the "NTB"
export too: an `.ntb` is a zip of `noteBundle`, a FlatBuffers log of the
note's records, and the pictures and PDF they name under `assets/`, read by
`gamma/notability_ntb.py` into the same model, so what follows holds for
both. The run over the upload, its guards, the commit and the report are
shared with the [GoodNotes import](#goodnotes-notebooks)
(`gamma/note_app_import.py`). The upload is one `.note` or `.ntb`, several picked together (the
dialog packs them into one `.zip`), a `.zip` of them (a library's export),
or a zip of an unzipped note folder (`.nbn`, iCloud's form). Each note
becomes one page. It is a source of the
reviewed import above (`source` `notability`), so the import itself is the
`import` job. The format, its sources and what was decided where they
disagree are in [research/notability-format.md](../research/notability-format.md).

**What a note becomes** (`notability_map.plan_note`):

| The note | Becomes |
|---|---|
| No PDF page, and something drawn or placed on its pages | A notebook: a sheet per page, each on the note's paper ([notebooks.md](notebooks.md)) |
| No PDF page and nothing on its pages | A plain note page: its typed text as notes |
| Its pages are exactly one bundled PDF's, 1..n in order | A page carrying that PDF as it is |
| Any other note with a PDF page: paper pages among them, several PDFs, pages reordered, repeated or left out | A page carrying one PDF made of the note's pages in order (`compose_pdf`): each PDF page copied from its file, each paper page a blank page of the paper's size with the paper painted on it |

- A sheet matches a Notability paper page (its own paper, ink and boxes on
  it, more paper added by writing low), so a paper note stays a notebook to
  write on.
- A sheet can carry neither a PDF background nor a paper image, the notebook
  view shows only on a page without a PDF, and a page carries one PDF. A
  mixed note therefore becomes one composed PDF, rather than pages split
  apart that lose its order.
- A note on one PDF keeps the file's bytes, shared by content hash with any
  page of the same file. A new page is made even when another page carries
  that PDF, since the note is its own document (`page_for_doc` still
  answers with the older page). The page gets no `auto_title`: the note's
  name is the user's.
- Annotations another app saved inside the PDF are not imported, since
  Notability hides them; Import → Annotations in this PDF brings them in.

**Where things land.** A note is one canvas in document units, its pages
stacked without gaps. It is W units wide: the locked reflow state's page
width, else the paper's `lockedWidth`, else 768 for a note from 2013–14,
else 565. The paper's left edge is at `−W × 20/768`. A paper page is 612 pt
wide (an A4 one 595.28) and as tall as its paper asks; a PDF page is scaled
to W and bottom-aligned in a slot of `ceil(h)` units. The layout's pages come
first (one whose PDF or page is missing or unreadable is paper, with a
warning), then paper pages while something lies below the last, and trailing
empty paper pages are dropped, as Notability's export drops them
(`notability_map.frames`). An item is on the page holding the vertical centre
of its bounding box, above page 1 counting as page 1. It is never split: a
stroke that crosses a page's edge keeps its samples outside that page's
frame, where the screen cuts it off.

**Handwriting.** Notability stores a curve as a fitted Bézier chain with a
width multiplier, a force and a tilt per anchor, and no raw samples. Each
curve becomes one gamma-ink v2 stroke ([handwriting.md](handwriting.md)
"Interchange"):

- **Samples.** The chain is flattened in page points, each cubic cut into
  equal steps by the larger of Wang's bound (within 0.05 pt of the curve)
  and a bound that keeps steps within a point, then decimated as the client
  decimates. The client's pen outline (perfect-freehand at streamline 0.4)
  moves each point only part of the way to the next sample, and its own
  input arrives about a point apart: sparser samples would round off a
  written corner.
- **Channels.** `w` is the fractional widths, linear between anchors. `p`
  (force ÷ the Pencil's maximum, 25/6) and `a`/`z` (the tilt) are written
  only where the note has real values (1.0, π/2 and (1, 0) are its
  "unknown"), in the client's channel order. A curve of even width is an
  even stroke (`pen: false`); a highlighter is the highlighter tool at its
  stored alpha; a pencil curve has `brush: "pencil"` and no `o`, since
  Notability's shading law is not public. There is never a `t` or `t0`: the
  note stores no time for a sample.
- **Files.** A page's strokes are packed into files by `ink.group_files`,
  the client's split (5,000 strokes, 500,000 samples, 3 MB of canonical
  bytes), stroke ids `n0`, `n1`, … in each file, so the same note stores the
  same files in any workspace. Each file passes `ink.parse_ink` on its bytes
  and is stored as `ink.dumps`.
- **Strokes.** The curves the eraser cut one stroke into share a UUID, and
  the tools (`curvesstyles`) and dashes count strokes, so every piece keeps
  its stroke's. A curve of style 6, or with an alpha of 0, is not drawn by
  Notability and not imported.
- **Groups** (16.x) are read with their transforms composed and their
  widths scaled, their shapes from the shape list each member holds.
- **Shapes** keep the record the client's recogniser writes (params in page
  points, normalised as `shapes.js` does), so a handle drag rebuilds them: a
  line, a straight arrow, a rectangle or square with square corners, a
  triangle, an ellipse, with a fill on the closed ones. Their samples are
  the ones `shapes.js` draws the params as (`ink.shape_samples`, held to
  `shapeSamples` by `tests/shared/inkshapes.json` `samples`): a line its
  two ends, an arrow, a rectangle or a triangle its corners and each side
  cut into equal steps of at most 2 pt, an ellipse max(48, perimeter ÷ 2)
  samples: what a handle drag writes again from the params. A curved
  line, a polygon, a skewed rectangle or ellipse, and every shape in an
  ink group are plain even strokes of their outline, at the same step; a
  curved line is flattened as a curve is, its head drawn as an arrow's. A
  partly erased shape is a plain stroke along each piece of its centre
  line the eraser left (`outlinePath`). A pencil shape keeps the pencil
  brush.
- **A sticky note** (a coloured sheet to write on) is a filled rectangle
  stroke in band 0, turned as it was: under the ink in every renderer and
  exporter, and moved and resized like any shape. An empty text box would
  go when its editor closed, could not turn and would lie under the ink.
- On a page the strokes go stickies, then the curves and shapes in the
  note's own z-order: a shape's index and a group's are their places among
  the strokes.

**Text, math, pictures, recordings.**

- The text typed on the pages has no stored place (Notability lays it out
  from the top of page 1), so it becomes the page's notes, first in the
  page: a block per paragraph, nested by its list level, a numbered item
  "n. text", a checklist item the editor's to-do (`- [ ] `). Bold, italic,
  strikethrough and highlight become Markdown, and each run is escaped
  (`text_box.escape_markdown`), so "$5" or "# x" reads as typed.
- A text box becomes a fixed-width text box (`pdf_page` on a PDF page, under
  its sheet on paper): a line per paragraph, the size and colour of most of
  its characters, its paper colour as fill. A turned box stands upright on
  its centre, since a box cannot turn ([text_boxes.md](text_boxes.md)).
- A formula (Notability stores its LaTeX alone) becomes a box of display
  math in its colour.
- A picture becomes a picture block
  ([pictures.md](pictures.md#the-notability-import-and-export),
  `notability_map.picture_block`): `pdf_page` on a PDF page, under its
  sheet on paper. Its file is stored as it came, never decoded
  (`note_app_import.AppImport.store_picture`, which the GoodNotes import
  shares). Its frame (`documentOrigin`, `unscaledContentSize`) goes
  through the page's frame and is kept on the page
  (`picture.keep_on_surface`). The turn
  (`rotationDegrees`, in radians) is `angle` in degrees, and the flips are
  `flip_h` and `flip_v`. The crop (`FigureCropRectKey`, in the file's
  pixels; an `.ntb`'s in the asset's logical size, `crop_size`) is
  fractions of the image as the browser shows it, cut to the image; one
  that holds nothing is the whole image. A stretch needs nothing: the
  frame's aspect is not the crop's. The caption (`captionFieldText`), on
  one line and escaped, is the block's content. A picture whose file is
  missing, past `MAX_HELD_BYTES`, or not a PNG, JPEG, GIF or WebP whose
  size reads is left out with a warning.
- An animated GIF is its animation whenever the note has it within
  `MAX_HELD_BYTES` and it stores, cropped, flipped or turned alike: the
  browser animates it so. Otherwise it is its still frame.
- Pictures an older import made stay text boxes holding an image. Their
  pixels were transformed and their crop is gone, so no migration makes
  them picture blocks.
- Each recording (`.m4a`, listed by `Recordings/library.plist`) becomes a
  recording block after everything else ([recording.md](recording.md)): one
  segment, its length from the file's movie header
  (`recording.mp4_duration_ms`), started when Notability made it. No stroke
  is placed on it: the note times each stroke drawn during a recording as
  an event, but not its samples, and the replay places a stroke only by its
  samples' times, which are never invented.

**One note at a time.** The upload's walk (`gamma/import_zip.py`, the
Markdown import's too) opens a `.zip` inside it but not a `.note`: each note
is opened only while it is read, in a stack of its own, its temporary file
closed before the next. So a library of thousands of notes imports whole
and never sits on disk twice. For each chosen note, in path order:

1. Its Session.plist is read and hashed. A note whose digest a page of the
   library carries (`properties.notability_import`), or that came earlier in
   the upload, is skipped and shown where it is.
2. The PDFs its pages name are checked against `MAX_HELD_BYTES` (256 MB: a
   PDF is read whole into memory, parsed and composed) and opened. One past
   it skips the note; one that does not open becomes paper.
3. Its PDF (the original or the composed one), its ink page by page, its
   pictures and its recordings are stored, outside any transaction.
4. Its folder and its page are written in one short transaction under the
   write lock, its digest looked for again there: the folder made where
   missing (`blocks_store.folder_inserts`, read under the lock, one batch
   through `ops.apply_batches`), then the page
   (`blocks_store.insert_page_tree`). An import that finds the note there
   writes neither and names the page where it is filed, so two imports of
   one note at once leave one page and no empty folder, and two filing
   into one new folder make it once.

A note's folder is the directories above it in the upload, less one top
directory every note shares (a zipped folder, or the one picked files are
packed into), below the destination. A note with none is filed by its
subject (`noteSubject`, unless unsorted), else in the destination. A
preview reads every note the same way, but not its ink, and writes nothing.
Storing a note's files meets one refusal, a 507 (the quota or the disk),
which stops the import and lists the notes not reached with its reason,
since each would meet it too. A note that cannot be read (one past a limit
below, or one that fails) is skipped (`skipped`). A stopped job keeps the
notes it finished.

**Limits.** The upload is untrusted, and every guard is in the code it
names:

- Zip bombs: the shared ratio rule. A member past 1 MiB that unpacks to
  over 100 times its packed size is not read; an archive that does so as a
  whole is refused (413), and a note that does is skipped.
- A central directory past 16 MiB (about 100,000 files) is refused before
  zipfile reads it (413 for the upload; a note is skipped): zipfile builds
  an object per entry and never reads the entry count
  (`import_zip.open_zip`).
- Zips inside the upload open to 2 levels and 500 in all, never under
  `NBPDFIndex/` (Notability's search cache), and are spooled only while the
  disk keeps 1 GB free.
- Plists (`notability_archive.load_plist`) are size-capped (Session.plist
  256 MiB, the others 4 MiB). A binary plist's trailer is checked before
  plistlib allocates its tables (at most 1,000,000 objects), and so are the
  references its arrays and dictionaries hold (at most 8,000,000 in all):
  plistlib makes a tuple slot and a list slot of each, about 17 bytes for
  a 1-byte reference. XML entities are refused. An archive is read by named
  keys only, UID chains followed at most 64 hops, every value type-checked
  where it is read.
- An archive can name one object a million times for a few bytes each, so
  no cost is paid per name: what an object reads as (a string, an array, a
  mapping) is made once (`Archive._once`), and a note's costs are counted
  where they are spent. The plists inside a note (its ink groups, their
  archives, shape lists and dash styles) are read up to 256 MiB in all, its
  typed text up to 5,000,000 characters (a text, a formula or a caption
  past it is cut short or left out), the formatting runs on that text up
  to 200,000 (past them a text keeps its words only; a run named twice in
  one text is read once), and its objects (text boxes, pictures, formulas,
  stickies) up to 10,000. A name (a file's, a font's, a class's) longer
  than 1,024 characters is no name; a missing file is named once.
- The ink arrays stay `array.array`, each checked against its count before
  it is unpacked. At most 1,000,000 curves and 100,000 shapes a note,
  10,000 points a curve or a shape, 1,024 pieces a Bézier segment, 5,000
  pages (the layout is read no further), and ink groups 8 deep and 20,000
  in all, a group's plist and the archive inside it each counting one. A
  stroke's samples are counted before one is made: a curve, a shape's
  outline or a sticky's past 50,000 is left out unmade, and so are the
  stroke that would take the note past 20,000,000 and every stroke after
  it (`notability_map.page_strokes`). A NaN, an infinity or a coordinate
  past 10⁶, the groups' transforms applied, leaves its item out. So does
  a stroke that would land past ±10,000,000 pt on its page (`ink.MAX_XY`,
  what a stroke file holds). A unit of the note is the page's width ÷ W
  points, so only a page many times as many points wide as W gets there:
  a letter page of a note a few dozen units wide, or a PDF page far wider
  than its note. Finding the notes of an upload is linear in its files.
- Pictures are PNG, JPEG, GIF or WebP by their first bytes, and nothing
  decodes them: only their size is read, from the header
  (`picture.image_size`, the EXIF orientation applied as browsers apply
  it), so a picture of any declared size costs no more. A file is read and
  stored once per note however many pictures show it (`store_picture`'s
  `files`). A still is read again at most once, to be stored for a picture
  shown without the animation an earlier one showed. A bundled PDF is read
  and opened once however many names lead to it, and a recording file
  listed twice is one recording. Recordings are `.m4a` by their `ftyp`,
  their length read from box headers only, and are streamed into storage
  (`storage.store_file_stream`). A text box's, a formula's or a caption's
  text is cut at 200,000 characters, what the editor saves
  (`ops.MAX_CONTENT`).
- An `.ntb`'s log is read up to 1,500,000 records (`notability.MAX_RECORDS`;
  past them the rest is left out, with a warning), and its text styles may
  touch 20,000,000 characters in all (`notability_ntb.MAX_STYLED`; past them
  a text keeps its words only).
- Every file goes through `store_pdf`, `store_file` or
  `store_file_stream` (quota, dedup), its size checked from the zip before a
  byte is read: a PDF or picture, held whole in memory, up to
  `MAX_HELD_BYTES`.

**The report**: `pages_created`, `pages_skipped`, `pdfs_stored`,
`strokes_imported`, `text_boxes_imported`, `pictures_imported`,
`recordings_imported`, the `pages` (each with its kind, folders, warnings
and `missing`), `skipped` and `warnings`. What a note leaves out is one
warning per kind ("3 rotated text boxes are placed upright"). `entries`
lists the upload's notes and, not imported, its other files.

**Not read, and said so.**

- A Supernote `.note`, and a "PDF + Recording" backup (add its PDF instead).
- From an `.ntb`, what no bundle seen yet has shown how it keeps: its
  recordings, a shape's fill and arrowhead, a checklist's ticks, a highlight
  on text. A bundle with several PDFs does not say where their pages go:
  they are paper, with a warning. Its log is read as the decoders found it
  (a record's last version, erase records); the sample is a compacted
  list.
- Left out with a warning: a partly erased shape whose centre line does
  not read, a polygon's fill, objects
  of a kind this import does not know, and anything past the limits. A
  template paper comes in as plain paper of its colour.
- Not kept: a text box's turn, alignment, fonts, per-run colours and
  underline; a sticky's lines; Notability's pencil shading and dash
  lengths (Gamma draws its own); where a caption is drawn; note links and
  tags; the
  handwriting Notability recognised (`HandwritingIndex`).
- A text box or picture that overlaps a sticky is drawn under it, since
  boxes lie under the ink layer.
- A long paper note is a notebook of as many sheets, and the notebook view
  mounts every sheet ([notebooks.md](notebooks.md) "Not built yet").

**A note Gamma wrote** ([Notability export](#notability-export)) reads
as any other, but for three things the export put there for it:

- A curve whose UUID begins `GAMMAI` (`notability.GAMMA_UUID`, a version 8
  UUID no random one can be) is read as the stroke it was
  (`notability_map.exact_stroke`): its anchors are its samples, taken as
  they are, not a Bézier chain to flatten. It has a channel where some
  anchor holds more than Notability's unknown, a stroke with force is one
  a stylus drew (`pen`), and its opacity is the alpha's value of fewest
  decimals that writes the same alpha back. The curves one such stroke was cut into, each starting
  on the anchor the last ended on, are joined into one
  (`notability._joined`); a piece Notability's eraser left starts
  elsewhere and stays a stroke.
- Gamma's copy (the root's `GammaNoteKey`, `notability.PRIVATE_KEY`:
  zlib-compressed JSON, read up to 64 MiB inflated) is used item by item,
  only where the note still holds what it describes: a stroke whose
  samples are the curve's (as many, each within 0.05 pt, the float32 the
  note stores), a shape whose samples span the same box (both
  `copied_stroke`), a text box at the same place and width with the same
  text (`copied_box`), a picture the note shows as the copy has it
  (`copied_picture`, below), the notes when the typed text is the one written
  (`copied_flow`), the sheets' papers when the pages are as many
  (`copied_papers`), and a recording file's place among its recording's
  segments. What the copy holds is checked as the import's own output is
  (`ink.Stroke`, `normalize_text_box`, `normalize_picture`,
  `normalize_paper`, `MAX_CONTENT`, `MAX_BLOCKS`), so it can only say what
  a note could.
  The copy's order of the page's notes, sheets and recordings (a PDF's
  handwriting, boxes and pictures counted as one) orders the page when the note
  brings as many of each (`notability_import._ordered`); else the page is
  its notes, its sheets or markup, then its recordings.
  - A picture's copy is `{caption, picture}`. It is used while the note
    shows the picture at the same place and size (within `COPY_TOLERANCE`,
    0.05 pt), turn (within `COPY_TURN`, 0.05°, the short way round), crop
    (within `COPY_CROP`, 0.001 of a side) and flips. Then the caption's
    Markdown and every number are the copy's, and the file is the note's.
  - The copy an older export wrote of the box it drew a picture in
    (`{alt, text_box}`) gives its alt text as the caption, while the
    picture is still where that box's padding put it.
- A line separator (U+2028) in the typed text is a line break inside one
  note: the export joins a note's lines with it.

**Real notes.** `backend/tests/test_notability_samples.py` imports every
`.note` and `.ntb` of `backend/tests/samples/notability/` and fails on
anything left out for want of reading it. The folder holds the example
note of
[Notability-notes-converter](https://github.com/nokcha0/Notability-notes-converter),
exported by Notability 16 as both "Note" and "NTB" (its README says
more): the module pins the Note export against the note's own bytes and
its thumbnail, and holds the NTB export to it, stroke for stroke within a
point. `test_notability_ntb.py` covers what the sample does not (a log's
versions and erase records, text boxes, a PDF's pages, damage) on bundles
`ntb_fixtures.py` writes. `GAMMA_NOTABILITY_SAMPLES` names a folder of
more notes to import the same way.

## GoodNotes notebooks

`POST /api/import/goodnotes` (View menu ≡ → Import… → GoodNotes notebooks;
`gamma/goodnotes_import.py`, which reads a notebook through
`gamma/goodnotes.py`, `gamma/goodnotes_codec.py` and `gamma/rtf.py` and
places it through `gamma/goodnotes_map.py`) imports GoodNotes' own format,
the `.goodnotes` its iPad, iPhone and Mac apps export. The upload is one
`.goodnotes`, several picked together (the dialog packs them into one
`.zip`), a `.zip` of a folder's export, or a `.goodnotes.zip` backup. Each
notebook becomes one page. It is a source of the reviewed import (`source`
`goodnotes`), and it shares the run over the upload, its guards, the
commit, the storing of pictures and the report with the Notability import
(`gamma/note_app_import.py`): what [Notability notes](#notability-notes)
says of one note at a time, of folders, of a 507 and a note skipped, of the
limits on zips, pictures and stored files and of the report holds here too.
The format, its sources and what was decided where they disagree are in
[research/goodnotes-onenote-import.md](../research/goodnotes-onenote-import.md).

**The file.** A `.goodnotes` is a zip. `index.events.pb` is the document's
event journal: its title, its paper templates, its pages made, bound to
another template, moved and deleted, and its recordings. `index.notes.pb`
lists a `notes/<N>` member per page, and `index.attachments.pb` the
attachments: PDFs, pictures and audio. Every member is a stream of
protobuf records, and a stroke's geometry is a tpl image in Apple's
framed LZ4 (`goodnotes_codec`). Pages come in the order of their order
keys, compared byte for byte, a deleted page left out. A page's member is
its id plus one, as one 128-bit number. Every page is bound to a one-page
PDF of its own size: one of GoodNotes' papers, or a page of a PDF the user
brought. Geometry is in canvas units of 6/11 pt, from the page's top-left.

**What a notebook becomes** (`goodnotes_map.plan_notebook`):

| The notebook | Becomes |
|---|---|
| Every page on one of GoodNotes' papers: blank, dotted, squared, narrow or wide ruled, or a cover | A notebook: a sheet per page of the page's size, with its paper's colour and rule colour (read from its PDF) and its pattern and pitch (16 pt dots and squares, 17.5 and 25 pt rules); a cover is a blank sheet of its colour |
| Its pages exactly one PDF's, 1..n in order | A page carrying that PDF as it is |
| Anything else: a paper the import does not know (Cornell, music, planners, a custom template), PDF pages among papers, pages reordered or repeated | A page carrying one PDF composed of the pages in order (`compose_pdf`), each copied from the PDF page it is bound to, GoodNotes' own papers among them |

- A page whose paper is missing, or whose PDF does not open, is blank
  paper of its size, with a warning. A turned page is imported upright
  (counted).
- The preview reads the journal and the indexes but no page, so the kind
  it shows (Page or PDF) follows from the templates alone.

**Handwriting.** Each stroke becomes one gamma-ink v2 stroke per sub-path
(an old file keeps the eraser's cuts as sub-paths), in GoodNotes' drawing
order, its lasso offset added ([handwriting.md](handwriting.md)
"Interchange"):

- **Ball pen** (constant width): the Ballpoint (`brush: "monoline"`), even,
  W/2 pt wide. Its quadratic segments are raised to cubics and sampled as
  the Notability import samples a curve.
- **Highlighter**: the highlighter tool at its width and alpha (0.5). It
  lies in band 0 under the ink, where GoodNotes draws it on top.
- **Fountain and brush pens** (variable width; the file does not say
  which): the Pen, `size` the stroke's widest, `w` each sample's half width
  over the widest half width. One of even width is even.
- **Tape**: an opaque even band as wide as the tape. It no longer hides and
  reveals (counted).
- **Pencil**: drawn with an Apple Pencil, the Pencil with `p` (force ÷
  25/6) and `w` and `o` baked by the brush table's law
  (`goodnotes_map.pencil_law`, held to `tests/shared/inkwidth.json`), so a
  restyle bakes the same. Its tilt is not written, since the two angle
  columns are in dispute. Drawn without a Pencil (force 0, the Mac), it is
  even.
- A line style is the stroke's `dash`.
- **A recognised shape** keeps a shape record where Gamma has one (a line,
  a rectangle with square corners, a triangle, an ellipse), as the
  Notability import's shapes do (`notability_map.shape_stroke`). A curved
  line, any other polygon and an open polyline are plain outlines. A
  shape's fill, a record of its own joined by its outline's id, is the
  shape's `fill` at its alpha (0.1).
- Erased strokes stay in the file as tombstones (metadata #3, content #14)
  and are passed by.

Samples lie at most a point apart, decimated as the client decimates.
Widths scale with the page when its PDF is drawn at another size than
6/11 pt a unit.

**Text, pictures, recordings.**

- A legacy text box (kind #8, Cocoa RTF, read by `gamma/rtf.py`) and a
  schema-35 one (kind #21, its runs in Apple LZ4) become fixed-width text
  boxes by the Notability import's rules (`notability_map.text_box`): the
  size and colour of most of the characters, the box's fill, bold
  (a font's or a weight's), italic and strikethrough as Markdown. Each box
  is grown by Gamma's padding so its text starts where GoodNotes' frame
  puts it. A turned box stands upright on the centre GoodNotes turned it to
  (counted from half a degree).
- A picture becomes a picture block by the Notability import's rules
  (`notability_map.picture_block`, stored by the shared
  `AppImport.store_picture`): its file stored as it came, never decoded,
  and once however many pictures show it; its frame (`#2`) through the
  page's and kept on the page; its turn (`#3.#3`, in radians) as `angle` in
  degrees. A die-cut sticker, a one-page PDF, is rendered through PyMuPDF
  to a PNG, transparent around the art, once for all the pictures that
  show it: its longer side twice that of the largest of their frames, in
  points, and at most 1,024 px (`goodnotes_import._sticker`), and the
  blocks show that PNG. A picture GoodNotes shows cropped comes in whole
  (counted), since where the visible rect (`#3`) cuts it is not confirmed.
- Each recording whose audio the notebook holds becomes a recording block
  after the sheets or the PDF's markup: one segment, its length from the
  file's movie header, started when GoodNotes started it, titled
  "Recording" (numbered when there are several). No stroke is placed on
  it: GoodNotes times a stroke, not its samples.

**The key** is `properties.goodnotes_import`, a SHA-256 of every member's
path, size and CRC-32 as the zip's directory lists them, but for what
GoodNotes derives (the search index, the thumbnail). It is computed
without reading a byte (`goodnotes.notebook_digest`). The same notebook
imported again is skipped and named; an edited one is a new page.

**Limits**, beyond the shared ones:

- The commit reads one page at a time, as its ink is stored, and lets the
  page's ink go once its strokes are made (`goodnotes.read_page`), so one
  page's ink is held at a time. Records are parsed as they are read.
- Protobuf: 4,000,000 records a member, 1,000,000 fields a message, the
  group wire types refused.
- An LZ4 frame's blocks are checked against what the frame may still
  produce before they are decoded, and a stroke's geometry stops at 8 MiB,
  a notebook's at 1 GiB (a dense page unpacks to about 0.7 MB). A tpl
  image's format is checked against the five GoodNotes writes before a
  value is read, and an array's count against the bytes left.
- The indexes 64 MiB each, the page index read to 50,000 entries (a
  deleted page stays listed), a page's member 256 MiB, 1,000,000 elements
  a notebook, 100,000 points a stroke, 2,000,000 a page's ink and shapes,
  10,000 corners a shape, 10,000 runs and 200,000 characters a text
  element, 64 MiB of text boxes' text a notebook.
- An RTF of 4 MiB at most, its groups past 64 deep passed by without a
  copy, its text 1,000,000 characters; a character past the BMP (an emoji)
  joined from its two surrogates, `\'hh` bytes in a row decoded together,
  so a double-byte code page reads.
- A coordinate past 10⁷ units, or not finite, leaves its element out; a
  date past year 9999 reads as none.

**Not read, and said so.**

- Shape tool objects (kind #21 with #9 0), lines and connectors (#22),
  sticky notes (#20) and math (#11): no public file holds one, so their
  keys are unconfirmed. They are left out, named by kind.
- Not read at all: a partial erase in the form a current build may write
  (`#16 erasers`), Layers, page links, the outline, bookmarks, and the
  search index's recognised handwriting.
- Not kept: tape's hide and reveal, a picture's crop, a page's turn, a text
  box's alignment, underline and fonts, the pencil's grain and GoodNotes'
  own pencil law, dash lengths.
- GoodNotes on Android, Windows and the web exports PDF only. Such a PDF
  imports as a paper, and Import → Annotations in this PDF brings in what
  an Editable export holds as annotations, as foreign ink: even polylines.

**Real notebooks.** `backend/tests/test_goodnotes_samples.py` imports every
`.goodnotes` of `backend/tests/samples/goodnotes/` (one CC0 notebook, its
README says more) and of the folder `GAMMA_GOODNOTES_SAMPLES` names, and
fails on anything left out for want of reading it. A `.pdf` beside a
notebook, named as it is, is taken as GoodNotes' own Editable export of
it, and the planner is held to it: the page sizes exactly, each ball pen,
highlighter and straight shape within 2 pt of its annotation and of its
width, each piece of a variable-width stroke inside its record's rect.
The public notebooks of goodparse and parser-for-goodnotes, which their
licences keep out of the repository, pass it: 14 notebooks import whole,
and the six with exports match them. `test_goodnotes.py` covers the
encodings, each element and each damage on notebooks
`goodnotes_fixtures.py` writes, and `test_goodnotes_import.py` the import
through the API.

## Notability export

`?mode=notability` (`_NotabilityBuilder`; `gamma/notability_export.py` maps
the page, `gamma/notability_write.py` writes the file) is each page as a
Notability note, the `.note` its "Note" export writes, for Notability to
open and for Gamma's own Notability import to bring back as the page it
was. One page: the `.note` itself, `<Title>.note`. A folder: a zip of
them, `<dir>/<Title>.note`, filed as the annotated PDFs are. The job's
result lists what each page lost (`warnings`: `{page_id, title,
reason}`), one line per kind as the import's warnings, and the finished
step shows them. There are no switches.

**The file** is what Notability 16.1.5 writes, class for class and key for
key, each value inline or behind a UID as the app puts it (the sample's
Note export is the model, and `test_the_note_is_written_as_notability_writes_one`
holds the two to it): a stored zip of one folder named after the note,
`Session.plist` (GLKeyedArchiver, format 9), `metadata.plist` (unfiled),
`Recordings/library.plist`, `thumb12x.png` (a blank page of the paper),
`PDFs/<UUID>.pdf`, `Images/Image n.<ext>`, `Assets/<id>/media.gif`,
`Recordings/Recording n.m4a`, and the empty folders the app keeps. What is
known of Notability opening such a file, and what is not, is in
[research/notability-format.md](../research/notability-format.md#writing-a-note).

**Pages** are the import's frames (`notability_map.frames`) run backwards,
computed by the same function on the note's own layout:

- A PDF page: the stored PDF as it is, all its pages in order, W the first
  page's display width, so its points are document units. A sheet of paper
  on a PDF page is left out.
- A notebook: a paper page per sheet, W the page's width (612, an A4 page
  595.28), so a unit is a point. Notability gives a note one paper, so every
  page has the first sheet's: A4, letter (`lockedWidth`, 612 × 803.25 pt),
  US Letter (`staticWidth`, 612 × 792), legal, or one of them landscape, as
  the import sizes them (`paper_size`), its colour `Color:#RRGGBB`, a ruling
  `Lines:false:false:<inches>`. A sheet of another size goes on the nearest
  one's page, and grid and dot paper are plain; both said.
- A page of neither: its notes alone, on letter paper.

**Handwriting.**

- A stroke is a Bézier chain whose segments are straight, the controls at
  a third and two thirds of each chord: Notability draws the polyline its
  samples make, each anchor with the sample's width multiplier (`w`), force
  (`p` × 25/6) and tilt (`a`; `z` as a unit vector), 1.0, π/2 and (1, 0)
  where it has none. A highlighter is style 4, a pencil style 5, any other
  pen style 3; the colour's alpha is the opacity the stroke is drawn at
  (`ink.drawn_alpha`); a dash is the stroke's entry in `dashStyles`.
- Its UUID begins `GAMMAI` (`notability.gamma_uuid`), so the import reads
  its samples back exactly, also after Notability has saved the note. A
  stroke longer than 3,334 anchors (10,000 points, the reader's cap) is
  written as curves of one UUID, each starting on the anchor the last
  ended on, which the reader joins.
- A page's strokes go in the order the screen draws them (band 0 of every
  group, then band 1, each in file order), which is the note's z-order.
- A shape record Notability has a shape for (a line, an arrow, a
  rectangle, an ellipse, a triangle, a regular polygon) is a shape of the
  shape list, with its fill and head, so Notability edits it as a shape;
  the import makes the record again (a polygon comes back an outline). A
  sticky (the import's band 0 rectangle) is a sticky again when it comes
  before the page's other band 0 strokes.

**Boxes, pictures, notes, recordings.**

- A text box is a text box at its place and size, its fill its paper
  colour, its Markdown Notability's runs (`box_runs`: a paragraph per line,
  lists by indent, bold and italic in the font's name, code in Menlo,
  strikes and marks as Notability keeps them, a link underlined, a
  `[[ref]]` as the text of the block it names, math as its TeX), as the
  import's `box_markdown` reads them back.
- A picture block is an `ImageMediaObject` (`_picture`). Its frame goes
  through the note's. `rotationDegrees` is the turn in radians,
  `FigureCropRectKey` the crop × the file's pixel size as shown
  (`picture.image_size`), and `isFlippedHorizontal` and
  `isFlippedVertical` the flips. `captionFieldText` is the caption's plain
  text on one line, references read as their labels, and
  `isCaptionEnabled` is set when there is one.
- Its file goes under `Images/` as it is. An animated GIF is its still
  frame under `Images/` and the GIF under `Assets/<id>/media.gif`, as
  Notability keeps one. Two pictures that show one file are written as
  two files.
- A surface's pictures come before its boxes in `zIndex`, so they lie
  under them, as on screen.
- A box holding one picture, as imports made one before a picture was a
  block of its own, is still a picture (`_boxed_picture`): in the box less
  its padding, as tall as the picture shown that wide. Its alt text is the
  caption, less an Obsidian size that ends it (`_ALT_WIDTH`:
  `![Figure 2|300x200](…)` gives "Figure 2"). It comes back a picture
  block.
- The page's notes (each block that is on no surface, in document order)
  are the typed text (`flow_runs`): a paragraph per note at its depth, a
  top-level note plain and a deeper one a bullet, "n. …" a numbered item,
  "- [ ] …" a checklist item, a heading bold, a note's lines joined by a
  line separator.
- A recording's segments are each a recording of Notability's, named after
  it.

**Gamma's copy.** What Notability's model has no place for travels in the
note as Gamma's own copy, which Notability does not decode and drops once
it saves the note (`Draft.private`, the root's `GammaNoteKey`): every
stroke verbatim by its UUID (its time, a pencil's `o`, a brush and nib
Notability lacks, its band and keys), each box's Markdown and box, each
picture's Markdown caption and `picture` (`{caption, picture}`), the notes
as blocks, each sheet's paper, which recording each file is a segment of,
and the order of the page's notes,
sheets and recordings. The import uses each item of it only
while the note still holds what it describes ([above](#notability-notes)).
So a note out and back in is the page it was, stroke for stroke and key
for key; a note Notability has saved comes in as Notability holds it:
every sample with its force, tilt and width, the shapes' records, every box
and note Notability can format, but no time, no pencil opacity, no brush
it lacks, no empty note, one paper, no empty last sheet, and the order an
import gives a page.

**Left out, and said:** highlights on a PDF's text (Notability keeps none;
the Annotated PDF does), a PDF page's sheets of paper, a recording that is
no `.m4a` (a browser's WebM or Ogg) or is still being made, the notes under
a recording, handwriting or a picture on no page, a file missing on the
server, and a picture's file that does not read.
Changed, and said: a sheet of a size Notability lacks, grid and dot paper,
a sheet of another paper than the first.

**Tests.** `test_notability_export.py` round-trips a notebook with a stroke
of every kind (pressure, tilt, time, a pencil's opacity, a calligraphy nib,
dashes, band 0, an `rgba()` colour, an extra key, 4,000 samples), shapes,
boxes, pictures (cropped, stretched, turned, flipped, captioned, an animated
GIF), notes and a recording of two segments; a
PDF page of three page sizes; and the sample's Note export, each the same
page after. With the copy stripped it holds every stroke to what Notability
keeps of it; with an item edited, that item comes as the note has it; and
the file is held to the sample's classes and keys. An old box holding a
picture comes back a picture block, its Obsidian size no part of its
caption. The browser suite
exports a page from the dialog and imports it back
(`transfer: a page out as a Notability note and back in is the page it
was`).

## Zotero RDF export

The import's exact inverse (`gamma/zotero_export.py`, endpoint branches in
`routers/export.py`): `?mode=zotero-rdf` on `/pages/{id}/export` and
`/folders/{id}/export` builds a `<slug>/<slug>.rdf` + `<slug>/files/<n>/<name>.pdf`
zip that Zotero's File → Import reads (unzipped) and Gamma's own
`/api/import/zotero` accepts as-is. Element shapes mirror what Zotero itself
writes (its "Zotero RDF" translator's `generateItem`) and `parse_zotero_rdf`
reads. The item carries the page's item type as `z:itemType`, which is what
Zotero's importer goes by, under the RDF class Zotero gives it
(`bib:Article`, `bib:BookSection`, `bib:Book`, `bib:Thesis`, `bib:Report`;
a conference paper and a preprint get none). A page with no record is a
`document`. A journal article's or a conference paper's venue, volume and
DOI sit on a standalone `bib:Journal` referenced by `dcterms:isPartOf`, a
chapter's book title, DOI and ISBN on a `bib:Book`, and a type without a
container carries its DOI and ISBN itself. A publisher (a thesis's
university, a report's institution) is a `dc:publisher` `foaf:Organization`
with a `foaf:name`. The import reads all of these back: `z:itemType` through
`item_types.from_zotero`, the publisher, and the ISBN from the item's or its
container's identifiers. Notes go as `bib:Memo` HTML, each top-level
non-highlight subtree one note written by `zotero_notes.note_html`, the
inverse of the import's tree, its first block as its own element and the
blocks under it as one list, as the page shows them (so a Zotero note's
later paragraphs come back to Zotero as list items): headings, lists numbered or not, quotes, code,
math, tables and links, a `[[ref]]` as the text it names, an image's alt and
width as its `<img>`'s. A note page (`zotero_note` equal to `zotero_key`)
goes back as a standalone note, its title an `<h1>` and then its blocks,
and is left out when notes are off. The page's "Attachments" block is no
note: each file chip under it goes back as an attachment of the item,
packed from storage as `files/<n>/<k>/<name>` and titled by its label, and
each web link as a linked-URL attachment (a `z:Attachment` with a URL and
no `z:path`). Other blocks under that heading still go as a note. The page's folders go as the `z:Collection` tree
(paths below the exported folder on a folder export — a page filed in the
exported folder itself is in no collection —, the whole path for one page),
its labels' names as `dc:subject`. An item's `rdf:about` is
`properties.zotero_key`, so keys survive a round trip, else
`#gamma_page_<page id>`, and a Memo's is `#gamma_note_<block id>`
(`GAMMA_PAGE_KEY`, `GAMMA_NOTE_KEY`): the same from one export to the
next, whatever else the export holds, which is what the import upserts by.
A Zotero key already written in the same export (a copied page keeps its
Zotero key) gives way to the page id, as Zotero's translator keeps its own
names unique.
A page's `zotero_rdf` goes back while it is a record of the same work as
the page's (`same_work`): the original type and RDF classes, the full date
(while its year is the record's), the creators as Zotero had them (while
the names are the record's), Extra with its `Citation Key:` line set to
the pinned key, the PDF attachment's title, and the passed-through
elements verbatim. A record edited into another work leaves it out. Since
any editor of a page can write its properties, the stored pass-through is
checked on the way out: an element holding `<!` (a DTD or entity), one the
import maps itself (`zotero_import.MAPPED`, `CONTAINER_MAPPED`), and one
holding an item, a note or a file path, or linking an attachment or a note
(`z:Attachment`, `bib:Memo`, `z:path`, `link:link`,
`dcterms:isReferencedBy`, an `rdf:resource` element, `z:itemType`) are
dropped, the creators only as `bib:authors`, the publisher only as
`dc:publisher`, and at most 64 KB of elements per list goes out. A
related-item link (`dc:relation`) goes back as written; Zotero's importer
ignores it.
Without one, an author is split into Zotero's two fields by `split_name`:
"Surname, Given" as written, a lowercase particle (van, de, von) with the
surname, a suffix (Jr., III) after a comma with it, and one word or an
organisation a single-field creator. A preprint with an arXiv id names its
archive (`z:archiveID arXiv:<id>`) and, with no publisher, `arXiv` as its
repository. An item carries one URL: the pass-through's, else the arXiv
abstract page, else the page's `web_url`. A volume with no container record
sits on the item, as Zotero writes it.
Attachment paths live in `z:path` like Zotero's own export — never an
`rdf:resource` *element*, an RDF/XML syntax term that Zotero tolerates but
strict parsers (rdflib) reject. The pipeline is verified against a live Zotero
via its connector server's `/connector/import` (same translator code path as
the wizard). The per-type shapes (conference paper, chapter, book, thesis,
report, publisher, ISBN) were taken from the translator's source and
round-trip through `parse_zotero_rdf`; they have not yet been imported into a
live Zotero. Zotero cannot read the .zip itself — its wizard reports
"unsupported format" for one, so the zip ships a README.txt telling people to
extract and pick the .rdf, and the export dialog shows a numbered step guide
for the format. Pasted note images: embedded in the Memo HTML as data URIs
(Zotero's note import keeps them — verified live; `_EMBED_IMAGE_CAP` guards
size), attached to the item as image `z:Attachment`s when bundling (the
pictures placed on a page too), and replaced by a plain
`(image: … — see item notes)` placeholder in annotation
comments (`strip_image_md`) — comments come from the PDF's `/Contents` and can
never render a picture, so a highlight whose notes carry images ALSO becomes
its own Memo with a page+quote header (`highlight_memo_html`). An export job
([tasks.md](tasks.md)) reports each page as it goes. Annotations are not in the RDF
— like Zotero's "Include Annotations" they're burned into the exported PDF
copies with `pdf_export.annotate_pdf` (`_ZoteroBuilder._pdf_copy`), as
the annotated PDF writes them. A PDF with nothing to draw goes in as it is
stored, packed from the uploads directory and never read, and so does one
whose annotating fails. The marks are highlights, area notes and handwriting
with `highlights=1`, text boxes and pictures with either switch (`pdf=0`
omits the files entirely, `notes=0` the Memos). An ink group's caption and
notes are its `/Ink` comment, not a Memo too; a text box is its `/FreeText`
alone, and a Memo as well only when notes sit under it, which a Zotero text
annotation cannot hold ([below](#text-boxes-in-the-exports)). A picture is
its `/Stamp` alone by the same rule ([below](#pictures-in-the-exports)).
Without a copy (`pdf=0`, no stored PDF, `highlights=0` for ink) they stay
Memos.

What Zotero takes from the copies, by its pdf-worker's `readRawAnnotation`
(Zotero 9.0.6; [research/zotero-annotations.md](../research/zotero-annotations.md)):
`/Highlight` → highlight, `/Square` → image, `/Ink` → ink, `/FreeText` →
text (Zotero 7+). A `/Stamp` is drawn from its appearance and imported as
nothing, so a picture stays part of the PDF. An `/Ink`, `/Square` or
`/FreeText` without a Zotero id (`/NM` `Zotero-<key>`) is dropped, so each
carries one from its block id (`zotero_annot_key`); a `/FreeText` also needs
`/Zotero:Rect`, `/Zotero:Rotation` and `/Zotero:FontSize` ([text_boxes.md](text_boxes.md#server-and-interchange)).
Zotero shows them once the PDF is open, locked (external), and its reader's
File → Import Annotations… makes them its own and takes them out of the
file; the zip's README.txt says so, and says once that Zotero shows the
pictures but imports no annotation for them. Zotero blends a colour with
white by the annotation's `/CA`, so a copy (`annotate_pdf(..., zotero=True)`)
writes no `/CA` on highlights and area notes. Their alpha goes in the
private `/GammaAlpha` (`pdf_export.GAMMA_ALPHA`) instead, which Gamma's
import reads first, so Zotero shows the true colour and a copy brought
back keeps it. Another viewer draws such a highlight at full strength; an
area note's appearance stream keeps its alpha. Ink, text boxes and pictures
are written as in the annotated PDF. Every annotation Gamma writes names its
author in `/Zotero:AuthorName` as well as `/T`, since Zotero reads only
the first.

## The export framework

The export job (`POST /api/jobs/export`, what the Export dialog starts),
`/pages/{id}/export` and `/folders/{id}/export` share one driver (`_run_export`
in `routers/export.py`). A folder export names its folder by id (the job's
`folder`, the route's path); `obsidian_export.Filing` reads the folder and label trees
once per export — a page's folder paths below the exported folder (its
folders outside it left out), its labels' names — for the builders that
write directories, collections or tags. It walks the selected pages exactly once (subtree
fetch, `build_tree`, a progress report) and feeds each page to a per-format
`_Builder` keyed by the mode: `_MarkdownBuilder`, `_ObsidianBuilder`,
`_NotesPdfBuilder`, `_AnnotatedPdfBuilder`, `_NotabilityBuilder`,
`_LogseqBuilder`, `_ZoteroBuilder`, `_GammaBuilder`. The builder accumulates zip parts, and
`save(dest)` writes the download, returning its name and media type. The
job saves to its file; `response()` saves to a temporary file and answers
with it. `begin(conn, root_ids)` shows a builder the whole export set before
the walk; the DB connection is closed by the time `save` runs. A generated
part too big to hold (an annotated PDF) is `spool`ed to a temporary file
and packed from there, and a bundled upload is packed straight from the
uploads directory. The zip stores PDFs and already-compressed media as
they are and deflates the rest (`_write_zip`, `_STORED_EXTENSIONS`):
deflating a PDF costs time for a few percent. `skip(page, reason)` leaves a page out, and `summary()`
(the job's result) counts the pages and lists the skipped ones. Adding an
export format means adding a builder; the endpoints, the job and the zip
writer stay untouched. A builder whose download isn't a zip overrides
`save`: `_NotesPdfBuilder` writes one PDF, `_MarkdownBuilder` a bare `.md`
for one page with no local files, `_AnnotatedPdfBuilder` one page's PDF,
`_NotabilityBuilder` one page's `.note`.

`annotated-pdf` (`_AnnotatedPdfBuilder`) is each page's PDF with its
highlights, handwriting, text boxes and pictures as standard annotations
and, with the Notes switch, its notes as cards beside each page or on it, and
with the Comments switch its open comment threads as cards beside it
([pdf_typesetting.md](pdf_typesetting.md#notes-and-comments-as-cards);
`annotated_page_pdf`, what `/pages/{id}/export-pdf` runs). For one page it is that PDF. For a folder it
is a zip of them, `<subfolder>/<Title>.pdf`, the directories mirroring the
page's first folder path below the exported folder (`Filing`,
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
adds nothing. A folder's export names its folder in the manifest
(`scope.folder`), and the merge's report then carries `folder`: the
workspace's id for it, the folder it became (one of the same name already
there, perhaps), or `""` when the import brought it nowhere.
A page it adds comes in whole and touched now (the change feed sees it); a
block whose id the workspace already uses on another page gets a fresh id
([workspaces.md](workspaces.md) "Export and backups"). The
Import dialog's "Gamma export (.zip)" source reviews the zip like the
other library sources, then merges the selected pages (`restore_zip` merge
with a selection) as the import job; guests can't import. A Gamma export is
a complete copy, so the Export dialog has no switches for it.

Through a share token (`?share=`: another Gamma importing a shared page or
folder by its link, below) the export carries only what the share shows
(`_GammaBuilder._shared_trees`):

- A folder export: the exported folder's subtree, that folder moved to the
  top level (its `parent_id` the folders tree's root), none of the folders
  above or beside it; and the labels its pages carry, flat, as a folder
  share's listing shows them.
- A page opened through a folder share: its folders inside the shared
  folder and the ones between, the shared folder at the top; and its
  labels, flat, as for a folder export.
- A page share, which shows no folder or label: none, so the page lands
  unfiled.

Each page's filing is cut to the folders and labels kept, and of the chats
only each page's active conversation travels (the one
`GET /chats/{page}?share=` reads): no archived conversation and no folder
view's chat. A member's own export is unchanged. The other formats through
a share still name the folder's whole path (the download's name, a `.bib`'s
header, a Markdown page's front matter) and render embeds of pages outside
the share.

### Importing a shared page or folder by link

The same pipeline, without the zip ever touching disk: `importSharedPage` in
`app/App.jsx` takes a share URL (`https://other/?share=<token>`), resolves the
token against that origin's `/api/share/{token}`, fetches what it shares as a
Gamma export — a page as `/api/pages/{id}/export?mode=gamma&share=<token>`,
a folder as `/api/folders/{id}/export?mode=gamma&share=<token>` — and hands
the blob to `runBackupImport(…, "merge")`: a `restore` job
([tasks.md](tasks.md)), after which the tab reloads into what came over. A
folder share's link may name one of its pages (`page=`, the share view's
address while a page is open) or a subfolder (`folder=`, while one is
browsed), and then that is what is fetched; a page or folder the share does
not reach is refused (403, which is also what one deleted meanwhile gets:
a share does not learn whether an id outside its reach exists), and so is a
folder with no pages (404 "no pages in that folder", said as such). Block ids survive the export, so a page
reloads on `?page=<id>` in the importer's own library (`after.openPage`); a
folder reloads on the id the restore's report names (`after.openFolder`,
read from the finished job), since it may have become a folder of the same
name the library already had. The folder fetched (the shared one, or the
subfolder the link names) arrives at the top level (above). Pages the library already has (the same id, or the same PDF) are
left as they are and are not filed into the imported folder.
The fetch is browser-side on purpose: the browser reaches a Gamma on
the LAN or at `localhost` that the server's SSRF guard (`net_guard.py`)
refuses. That works because share GETs answer
`Access-Control-Allow-Origin: *` ([api.md](api.md)), refusals too, so the
import can say which one it got; with `*` the browser
sends no cookies cross-origin, so only links open to `anyone` import from
another Gamma, while a same-origin link also carries the session and
invite-only ones work too. Two entry points:

- The topbar's "+" box: a pasted `<origin>/?share=<token>` is recognised by
  shape (`parseGammaShareLink`: the SPA root with a `share` query), shows a
  hint, and Enter imports instead of fetching a PDF. Everything else typed
  there is still a paper.
- The share view's topbar: a signed-in non-guest viewer gets "Add to my
  library" on a page and on a folder share's listing (not on a page host,
  which serves no library). A share view has no workspace and no background
  tasks of its own, so it does not import: `addShareToLibrary` puts the link
  for what it shows (the open page, or the folder browsed) in
  `sessionStorage` (`gamma-import-share`) and goes to the library, whose
  boot takes it and imports there, with the tray and the reload of the "+"
  box. That library opens on the last workspace used, unless the account
  may only read it, then on its personal one (`chooseWorkspace`). Only the
  app's own tab can leave that key, so an address alone never imports
  anything. The page's owner
  gets "Open in my library" instead, a plain jump to `?page=<id>` (a folder
  share's to `?folder=<id>`) with the share's workspace as `ws=` and no
  token, since the page is already theirs. A visitor with no account gets "Sign in" (the
  share's own sign-in gate), after which the link opens again with the
  import button.

The remote must be recent enough to serve `mode=gamma` and the CORS
header; an older one surfaces as "couldn't reach …" / "too old" in the
status line and the task's row. A remote from before the share-scoped
export still answers, with its export as a member's (the folders above,
the archived chats): the leak is that server's, which any holder of the
token can already download, and the import takes what it is given.

## The Export dialog

The View menu's single "Export…" entry → `ExportDialog` in `transfers/ImportExport.jsx`.
Step one is a format card. The PDF row holds Annotated PDF, the Notes row PDF,
Markdown and Notability, the ZIP row Obsidian, Logseq, Zotero and Gamma. Double-click or
Next confirms. Formats with editable options get a review step: the
Highlights, Notes and Bundle-the-files switches beside an illustrative page
(`shared/illustrations/TransferPreview.jsx`, an example of the options, not
a render of the document). Annotated PDF has two more: "Place notes", a
choice between Beside the page and On the page (`NOTES_PLACES`; it waits on
the Notes switch), and Comments, the open threads on each highlight and on
its notes as cards beside the page, with or without the notes (a note's
threads under it when the notes are beside the page too, the highlight's
own under its own note). The preview draws them where they go: the
note and a comment card in a column beside the paper, or the note on it. Gamma and Notability have fixed contents, and a
PDF without a stored copy can only be the original file, so these export
straight from step one. Logseq
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
`notes-pdf`, `readable`, `notability`, `obsidian`, `logseq-graph`,
`zotero-rdf`, `bibtex`, `gamma`)
and the three flags, and for an annotated PDF `comments` and `notes_at`. The dialog then shows its last step, the job's
(`ExportJobStep`). While the job runs it shows its progress (pages, then
the packing of the zip) with Stop, and says the window may close. The job
goes on in Background tasks, whose row opens this step again. Once ready,
the file downloads by itself when the window is open; a closed window's
file is offered in the pill with a Download button, and waits in the tray.
The finished step names the file and its size, says "Saved to your
downloads" once the automatic download has happened (`job.downloaded`;
the button then reads "Download again"), counts the pages, lists the
pages left out with the reason, what the file could not keep of a page
(the result's `warnings`, a Notability note's), and says what to do next:
Zotero's steps, Obsidian's unzip-into-a-vault, Gamma's Import → Gamma
export, BibTeX's beside-your-.tex, Notability's open-it-there. A failed or
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
`mode=annotated-pdf` (`/pages/{id}/export-pdf?highlights=&notes=&comments=&notes_at=`
answers the same; `notes_at` is `beside` or `page`, 400 for anything
else). "Annotated PDF" is the paper itself and is hidden when there is none
(a note page); a page with sheets of paper shows it too, and exports them
([notebooks.md](notebooks.md)). An unsaved proxy PDF can
export only its original file, so it skips the options page and exports directly.
"PDF" in the Notes row (`?mode=notes-pdf`) takes over as the fallback format, and its
Bundle switch is hidden because a document always embeds its images. Two
combinations are special: a
Logseq graph is defined by carrying both layers, so highlights and notes are
always included and only file bundling gets a switch; a PDF with every switch off is the stored file itself, which the frontend
downloads from the viewer's own URL (so it also works for a PDF that only
exists behind the proxy).

The dialog can also target a whole folder: opened from home with a folder open
(the View menu's Export…) or from a folder card's context menu (`exportFolder`
state in App.jsx), it exports every page filed there or below, in any
format. There Annotated PDF and BibTeX sit in their own "Papers" row: each
paper's annotated PDF, with the Highlights, Notes and Comments switches and the notes' place, in one zip
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
  spot (here, in the Markdown and Obsidian exports, in a cached
  `/metadata/fetch` answer and in the AI `cite` tool alike).
  A page with no metadata at all is skipped with "page has no paper
  metadata" and named in the finished export; a set where no page has any
  fails with "none of these pages has paper metadata to cite".
- **What an entry looks like** (`bibtex.build_entry`): the entry type
  follows the record's item type
  ([paper_metadata.md](paper_metadata.md#metadata-fetch--edit--cite)), as
  Zotero's own BibTeX export maps it:

  | Item type | Entry | Venue and publisher as |
  |---|---|---|
  | `journalArticle`, `preprint` | `@article` | `journal` |
  | `conferencePaper` | `@inproceedings` | `booktitle`, `publisher` |
  | `bookSection` | `@incollection` | `booktitle`, `publisher`, `isbn` |
  | `book` | `@book` | `publisher`, `isbn` |
  | `thesis` | `@phdthesis` | `school` |
  | `report` | `@techreport` | `institution` |
  | `presentation`, `document` | `@misc` | `howpublished` |

  An article or preprint whose venue is only arXiv's is
  `journal = {arXiv preprint arXiv:<id>}`. Zotero writes a preprint as
  `@misc`; Gamma keeps this form because every cached arXiv entry already
  has it. An article with neither a venue nor an arXiv id is `@misc` too (an
  `@article` without a journal is a BibTeX warning). An arXiv id always adds `eprint`/`archivePrefix`, so a published
  paper read from arXiv cites the journal and keeps the preprint.
- **Every printed entry is valid BibTeX.** Every entry leaves through
  `bibtex._format`: `build_entry` writes through it, and `page_entry` reads
  a stored entry back (`normalize_entry`, a small parser for any layout:
  doi.org's one-line rendering, an entry an older Gamma cached, one restored
  from a Markdown export) and writes it again. So the export, its review,
  the share popover's Copy BibTeX, `cite` and the Markdown and Obsidian
  exports all print the same text, whatever was stored. The rules:
  - Text values are ASCII LaTeX (`bibtex.latex_text`). An accented letter
    becomes BibTeX's special character (`F{\"o}sel`, which classic BibTeX
    sorts and abbreviates right), a Greek letter or math symbol `$\alpha$`,
    registry markup a macro (`<i>` → `\emph{}`, `<sub>` →
    `\textsubscript{}`), and a MathML formula math (`spin-$1/2$`, through
    `gamma/mathml.py`). Entities are decoded and `& % # _ $ ^` escaped
    outside math. What is already LaTeX stays as it is, so a second pass
    changes nothing. A character with no LaTeX form (CJK) stays UTF-8 for
    a Unicode engine.
  - Braces balance in every value; an unmatched one is dropped, since
    BibTeX counts every brace and one stray brace breaks the file.
  - The month is a standard macro (`month = sep`); doi.org writes `Sept`,
    `June` and `July`, which no style defines. A page range takes `--`.
  - The entry type is one every style defines, and a type whose venue
    field is missing (`@article` without `journal`, `@inproceedings`
    without `booktitle`, a thesis without `school`, a report without
    `institution`) is written as `@misc`: apsrev4-2 fails on an `@article`
    with no journal.
  - The key is ASCII (`bibtex.clean_key` spells `Fösel` as `Fosel`):
    pdflatex cannot `\cite` a key holding a UTF-8 letter.
  - Identifiers and links (`doi`, `url`, `eprint`, `isbn`, `issn`) are
    written raw, since `\url` and `\doi` read their argument verbatim. A
    `%` or `#` in a URL prints as it is under the url package or hyperref
    and under biber; escaped it would print its backslash. doi.org's
    resolver link for the entry's own DOI is dropped, since styles print it
    beside the DOI. A bare macro other than a month (`journal = prl`) is
    written as its text: the file carries no `@string` to define it.
  - A field the stored entry lacks but the metadata record has is added:
    the `pages`, because doi.org's rendering leaves out an APS or IOP
    article number. Records fetched before the lookup read article numbers
    have none; a refetch adds them.

  Checked against TeX Live 2024 (pdflatex with plainnat and apsrev4-2
  through BibTeX, and biblatex through biber): a 22-entry export and a
  corpus of awkward records compile with no errors, and
  `tests/test_bibtex_entries.py` pins the rules. Two limits are pdflatex's,
  not the file's: a letter with two accents (Vietnamese "ễ", written
  `{\~{\^e}}`) typesets through BibTeX but not through biber under
  pdflatex, and CJK needs XeLaTeX or LuaLaTeX. What the registries really
  return: [research/registry-records.md](../research/registry-records.md).
- **Citation keys**: generated as first author's surname + year
  (`bibtex.default_key`; it must not change, because cached entries carry
  it). Two papers by one author in one year would collide in a
  bibliography, so `bibtex.unique_keys` suffixes clashes `a`, `b`, … `z`,
  `aa` — Better BibTeX's convention. Clashes are found case-insensitively
  (classic BibTeX treats `Smith2020` and `smith2020` as one entry). Pinned
  keys are assigned first, so the key a user chose is never the one that
  moves; two pins that collide are still made distinct rather than writing
  a broken file.
- **A pinned key** is `properties.cite_key`, set in the metadata popover's
  Cite key row (empty shows the generated key as its placeholder). It lives
  *beside* `meta`, not inside it, because a refetch replaces the record and
  must not take the key with it: the user's `.tex` files cite that key. It
  outranks both the generated key and a registrar's own, is cleaned of what
  BibTeX breaks on (`bibtex.clean_key`: whitespace, `, { } ( ) = \ " # % ~`),
  and clearing the record clears the pin too. The metadata lookup and a
  hand edit write it into the cached entry; every surface that prints the
  entry — this export, the AI `cite` tool, the Markdown and Obsidian
  exports — goes through `bibtex.page_entry`, which applies the pin to a
  cached entry that predates it or carries a registrar's own key, so the
  chat and the `.bib` file never disagree on a key.
  A Zotero import brings one along when Better BibTeX left a
  `Citation Key:` line in the item's Extra field
  (`zotero_import._citation_key`, carried as RDF `dc:description`), so a
  migrated library keeps citing papers by the names its documents use; the
  Zotero RDF export writes the same line for a pinned key, so a library
  that goes out and comes back keeps its keys.
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
pages it could not cite instead of an error. The share popover's Copy
BibTeX asks it too (`?page_id=`) when it opens, so the entry it copies is
the one the file carries, not the stored text.

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

The folder sync ([folder_sync.md](folder_sync.md)) writes the same dialect
with the PDF beside the note. It shares `unique_name` and
`VaultContext.place_pages`/`place_pdf` with this export and the
annotated-PDF folder export. `vault_name` appends `_` to a Windows device
name (`CON` → `CON_`).

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
chunk's text into styled inline spans (bold, italic, `<u>underline</u>`,
`code`, strike, `==mark==`, `[[refs]]`, links, `$…$` math; a colored
run's `<span style>` tags dropped, its text kept; a backslash before ASCII
punctuation is that character, as CommonMark and the screen read it, so an
escaped `*` or `$` prints as typed). A paragraph's lines are parsed
together, the lines CommonMark joins (a plain line under plain text, a list
item or a to-do, a `>` line under a quote line), so a mark that crosses a line
break, `**a⏎b**`, is bold on both lines as the note shows it, each line still
its own chunk. Bold, italic, strike, underline, colored runs and link labels
cross; `==mark==`, code, math and refs stay within a line, as on screen; an
image ends the text a mark may cross. A link's target, an image's source and
a bare web address hold one level of balanced parentheses, as Gamma's
renderer reads them, so an Elsevier DOI
(`https://doi.org/10.1016/S0140-6736(20)30183-5`) or a Wikipedia page links
whole and a parenthesis around an address stays text. The grammar lives in
`note_markup` (`INLINE_RE`), which the Zotero notes read too. Highlights become quoted passages
with a bar in the highlight's own colour and a `p. N` marker, and the
Highlights/Notes switches mean exactly what they do in the Markdown export
(drop highlights and a highlight block keeps its own writing as a plain
bullet). A text box and a picture placed on a page have rules of their own
([text boxes](#text-boxes-in-the-exports), [pictures](#pictures-in-the-exports)).
Links become real `/Link` annotations, page titles and headings
become PDF bookmarks. Layout constants (A4, margins, sizes) live at the top of
the module.

Tables draw as a real grid: column widths measured from the cells (squeezed
proportionally into the column when too wide), wrapped cells, `:---:`
alignment honored, the header bold on a tint and repeated when a page break
falls inside the table. Fenced code is a bordered tinted card, one card
segment per page it spans. `[[refs]]` and `![[embeds]]` resolve through a
`resolve_ref` callback (`markdown_export.block_ref_resolver` — its own
sqlite connection, since the request's closes before `response()` runs): a ref
reads as its target's first line in link colour, an embed renders the synced
block's content as a card with a soft bar and a muted `from <page>` source
line (nested embeds degrade to refs so transclusion can't recurse).

Pagination is per line, not per block: the canvas breaks a page between lines
so nothing is ever clipped, and code lines carry their leading whitespace as an
x offset because wrapping drops spaces at the start of a line.

## Recordings in the exports

A recording block ([recording.md](recording.md)) is audio the exports
cannot carry as a PDF annotation, so each renderer says what it is and
hands the files on where it can. The Markdown export writes a
`**Recording · 12:34**` line with one link per audio file (`[audio](…)`,
or `[part 1](…) [part 2](…)`), so `collect_and_rewrite` bundles the files
into `assets/` like any `/api/uploads/` reference the rendered Markdown
names; the Obsidian vault writes the same line and an `![[attachments/<hash>.webm]]`
embed per file; the notes PDF a muted "Recording · 12:34 (audio not
included in a PDF)" line; the annotated PDF nothing. The title and the
children (a transcript's paragraphs are plain notes) follow as usual. With
the highlights switch off a recording is treated like ink: its property
dropped, its title kept. The Gamma backup needs nothing of its own: it
copies every referenced upload.

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
annotated PDF, and the Zotero export's PDF copies, write them with either
switch on ([pdf_typesetting.md](pdf_typesetting.md#annotated-pdf-export)).
Pictures follow the same rules ([below](#pictures-in-the-exports)).

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
- **Zotero RDF** (`zotero_export.py`): a box on a PDF page rides in the
  bundled PDF copy as the annotated PDF's `/FreeText`, which Zotero 7+
  imports as its text annotation, and is no note. One with notes under it
  is a note as well (`bib:Memo`), since Zotero's text annotation holds its
  text alone. Without a copy, every box is a note; one on a PDF page opens
  with a bold "Text box on p.N" line, as a highlight's memo opens with its
  page.
- **Notes as PDF** (`pdf_document.py`): a small grey "text box, p. N" line,
  like the "handwriting, p. N" line and kept with the text, then the text
  in the normal note style.
- **Annotated PDF**: a `/FreeText`, with either switch on, since
  `notes=0`, that format's default, means "don't paint the notes on the
  page". The sheet export draws a sheet's boxes as page content.

## Pictures in the exports

A picture ([pictures.md](pictures.md)) is placed on a page as a text box
is, and the exports treat it as they treat a box. It is a note and never a
highlight (`highlights.is_highlight` refuses one). The nearest sheet wins,
and only a picture on a PDF page names its page (`picture.picture_page`).
Its caption is the block's content. The Highlights switch leaves pictures
in, and the Notes switch takes them out with the other notes. The
annotated PDF and the Zotero export's PDF copies draw them with either
switch on ([pdf_typesetting.md](pdf_typesetting.md#annotated-pdf-export)).

The text exports write a picture as one line, `picture.markdown_of`:
`![caption](url)`. The caption is the one a reader sees: its Markdown read
as text (`text_box.plain_text`), on one line, without `[`, `]` and `|`
(which would end the alt text or read as an Obsidian size), and escaped
once. The url is the picture's upload, so the Bundle switch bundles it as
it bundles any image of a note. In the Markdown, Obsidian, Logseq and
Zotero exports, a picture whose value names no file has no line: its
caption is written as a note's text.

- **Markdown** (`markdown_export.py`): the line as a bullet, then the page
  line a text box carries (`` `p.N` ``) on a PDF page.
- **Obsidian** (`obsidian_export.py`): the line as a paragraph at the top
  level and as a bullet in a list, then the page link
  `[[Paper.pdf#page=N|p. N]]` on a PDF page, as for a box.
- **Logseq graph** (`logseq_graph_export.py`): the line as a plain block,
  its file bundled into `assets/`, and no page, as a box has none.
- **Zotero RDF** (`zotero_export.py`, `routers/export.py`
  `_ZoteroBuilder`):
  - With a PDF copy, a picture on a PDF page is drawn in it as the
    annotated PDF's `/Stamp`. Zotero shows the stamp but imports no
    annotation for it, which the README says once. The picture is no
    note, unless notes sit under it.
  - When bundling, the picture's file also rides as an item attachment,
    as a note's images do. The Zotero import makes no chip of it when the
    export comes back ([Zotero library import](#zotero-library-import)).
  - A picture no copy carries is a note (`note_html`). One on a PDF page
    opens with a bold "Picture on p.N" line. Then come the image, embedded
    as a data URI like any note image with the caption's plain text as its
    alt, and the caption's Markdown under it, since Zotero does not show the
    alt.
- **Notes as PDF** (`pdf_document.py`, `_Canvas.picture`): the picture as
  placed, cropped, flipped and turned. Its turned frame is fitted to the
  column (never enlarged) and to a page's text height. Then come a small
  grey "picture, p. N" line on a PDF page, as handwriting has, and the
  caption as a bullet. A picture whose file is missing or cannot be
  embedded is a grey "picture" line, with its page when it has one.
- **Annotated PDF**: a `/Stamp`, with either switch on. Among a highlight's
  notes in its popup, a picture on the PDF page is left out with the notes
  under it, since it is its own `/Stamp`. One under a sheet stays a line,
  its `![caption](url)`. The sheet export draws a sheet's pictures as page
  content, under its boxes.
- **Notability**: an `ImageMediaObject` with its crop, turn, flips and
  caption ([Notability export](#notability-export)).
- **Gamma export and backups**: the file rides by the reference in the
  `picture` property (`storage.upload_refs` reads properties too).

**Not built yet:**

- The Markdown, Obsidian and Logseq exports and a Zotero note show the
  whole file, upright: the line carries no crop, turn, flip or size.
- Zotero keeps no picture. It draws the stamp, and its Import Annotations
  leaves it in the PDF.
- A foreign stamp's skew is lost, and so is whatever its appearance draws
  beside its one image (a border, vectors).
- The Notability export writes two pictures that show one file as two
  files: whether Notability takes two media sharing one is unknown. How
  Notability itself shows a turned, cropped, flipped or captioned picture
  Gamma wrote is untested, and so is the order of its flips and turn.

## The PDF writers

The typesetting engine both PDF writers draw with (`gamma/pdf_typeset.py`)
and the annotated-PDF export (reading through `ExportPdfReader`, notes and
comments as cards beside the page or on it, vector text as Type 3 fonts,
images, fonts and content streams) are described in
[pdf_typesetting.md](pdf_typesetting.md).
