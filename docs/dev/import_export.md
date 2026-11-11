# Import and export

The View menu's (≡) Import…/Export… dialogs and every pipeline behind them: embedded
PDF annotations, Logseq graphs, Zotero libraries, Markdown notes (Obsidian
vaults, Notion exports), Notability notes, GoodNotes notebooks, Markdown, Obsidian vault and Notion export, the notes
typeset as their own PDF, the BibTeX bibliography, and the annotated-PDF
writer (its internals and the shared typesetting engine are in
[pdf_typesetting.md](pdf_typesetting.md)). Code: `gamma/routers/imports.py`, `gamma/zotero_import.py`,
`gamma/zotero_export.py`, `gamma/zotero_notes.py`, `gamma/logseq_import.py`, `gamma/markdown_import.py`,
`gamma/markdown_zip_import.py`, `gamma/markdown_zip_links.py`, `gamma/markdown_zip_dialects.py`, `gamma/notion_import.py`, `gamma/notion_html.py`, `gamma/notion_blocks.py`, `gamma/notion_gamma.py`, `gamma/notion_nfm.py`, `gamma/import_zip.py` (the zip walk the library imports share),
`gamma/notability_import.py`, `gamma/notability.py`, `gamma/notability_archive.py`,
`gamma/notability_map.py`, `gamma/notability_export.py`, `gamma/notability_write.py`,
`gamma/note_app_import.py` (what the Notability and GoodNotes imports share), `gamma/goodnotes_import.py`,
`gamma/goodnotes.py`, `gamma/goodnotes_codec.py`, `gamma/goodnotes_map.py`, `gamma/rtf.py`, `gamma/markdown_export.py`, `gamma/obsidian_export.py`, `gamma/notion_export.py`, `gamma/export_walk.py`, `gamma/pdf_export.py`,
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
app that writes 0.831 means 212); pypdf's `.get()` hands back `IndirectObject`s unresolved, always
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
- Either is locked when its `/F` has the Locked bit (128). The bit decides,
  not `/GammaPicture`, so a lock or an unlock made in another viewer holds.
- Either takes its opacity from its `/CA` (`_alpha`), not from
  `/GammaPicture`, for the same reason. A stamp without one is opaque.
- Neither takes a `z`. The stamps come in in the page's order, which is
  the order a Gamma export stacks them in.
- Either is kept on the page (`picture.keep_on_surface`).

**A stamp's file** is read only for the stamps the import adds, once per
image however many stamps draw it, and before the write lock
(`_PictureFiles`, `pdf_image.image_file`). The PDF is untrusted, so every
read is bounded:

- `pdf_image.stream_data` inflates a `/FlateDecode` by hand, never past the
  size the image needs. pypdf's `get_data` would inflate it whole, up to its
  75 MB cap, and keep the result on the reader.
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
touches. A file MuPDF can't open imports its highlights without quotes. A
server whose PyMuPDF does not load is another matter, since a block keeps
the quote it is made with: the import stops with the ImportError, writes
nothing, and the next one makes the blocks with their quotes. Every reader
of `GlyphPages` goes by the same rule, so a relocation fails without moving
a highlight, and a Zotero round tries the unit again.

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
the write lock like every lookup by attachment (a new page's `doc` has no
name: the by-doc call the client makes next gives it the file's).
Highlights already on the
page are skipped by their quote and notes by their text (a note written
twice in the .md stays twice), so running it again adds nothing. A note's
Logseq image size, `![alt](url){:width N}`, comes in as Gamma's
`![alt|N](url)`, and the page's notes are compared in that form too. Into an
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
`.md` or a `.zip`), Notability notes (one or more
`.note` files, or a `.zip` of them), GoodNotes notebooks (one or more
`.goodnotes` files, or a `.zip` of them), Notion pages (the `.zip` of a Notion
export), or a Gamma export .zip. Double-click
or Next confirms. Only Zotero gets a review step, its export guide.
Annotations in this PDF imports at once, and it and Zotero (for the
annotations inside its exported PDFs) send the strip preference above.
Markdown, Notability, GoodNotes, Notion, Logseq and Gamma open the
file picker directly, with their preparation notes under the selected card.
`resolveImport` in `transfers/transferFormats.js` decides both.
The Notability and GoodNotes pickers take several files, which go up as one
`.zip` (`folderZip.js` `filesUpload`, a name picked twice numbered), and take
any file on an iPhone or iPad, whose Safari greys out a `.note` or a
`.goodnotes`.
Zotero is the default source (a numbered step guide reusing
settingsKit's `Step`); with a PDF open, that PDF's own annotations win.

The iPad and Android apps open the dialog with a `.zip` already chosen,
one shared to the app from another app ([ipad.md "Opened files"](ipad.md#opened-files),
[android.md](android.md) "Opened files"). The
dialog is then titled with the file's name and asks which export it is.
It offers only the sources that read an archive (`archive` in
`resolveImport`: Zotero, Markdown, Notability, GoodNotes, Notion and Gamma). Its
button is *Import*, and the file goes straight to that source's review
with no picker. A `.note` or `.ntb` shared to the app skips the dialog and
opens the Notability review, a `.goodnotes` the GoodNotes review.

Zotero, Markdown (ZIP or one file), Obsidian/Notion ZIPs, Notability notes,
GoodNotes notebooks and Gamma exports share one review flow: Upload → Review →
Import → Summary.

- The review is a background job ([tasks.md](tasks.md)):
  `POST /api/jobs/import-preview` takes the file, its `source` (`zotero`,
  `markdown-zip`, `markdown-file`, `gamma`, `notability`, `goodnotes`), an optional `folder` (the
  destination folder's id, `""` the library's top; 400 when it is no
  folder, `import_review.destination`) and `strip`.
  It stages the upload and starts the job (`import-preview`), which runs
  the source's preview, reporting its items, and keeps the two trees
  beside the upload; `GET /api/import/review/{id}` reads them, with the
  `review_id`, and `DELETE` discards them (stopping a job still reading).
  An upload past 32 MiB comes in parts instead
  (`POST /api/jobs/import-preview/parts`, [api.md](api.md)), the protocol
  of a PDF's upload in parts ([user_db.md](user_db.md) "Stored files"),
  since a proxy in front of the server caps a request's body (Cloudflare
  at 100 MB). The parts are appended to the staged upload, and finishing
  starts the job, as the single request does. The largest archives are the
  ones whose review takes minutes (every PDF of a Zotero export inflated
  and hashed), past the 100 s a proxy lets a silent request run: as a job,
  the review is read to the end and kept. `POST /api/import/review` (and
  its `/parts`) answers the same review in the request, for scripts.
- The import itself is a background job ([tasks.md](tasks.md)):
  `POST /api/jobs/import` with `{review_id, selected: [...]}` imports from
  the staged upload against the current library, reporting each item; its
  result is the import report. Asking again for the same review answers the
  job already started (409 with another selection).
- Each source is a request-free preview and commit in `routers/imports.py`
  (`_review_source`), over the staged file and the review's metadata, each
  taking a job's `progress`; the plain endpoints (`/import/zotero`,
  `/import/markdown-zip`, …) run the same functions in the request. A
  preview may return a `stage`, what it learned that the commit can reuse
  (a Zotero review's PDF digests): `_preview_staged` adds it to the
  review's metadata as it keeps the review (`import_staging.keep_review`,
  `review.json` beside the upload), and no answer carries it. A Gamma
  export's preview reads the staged zip where it lies and unpacks only its
  databases and its handwriting files (`ws_backup.preview_zip`): the
  uploads are known by their names in the zip, and step 41 converts a
  stroke file of an older backup, as a restore's copy does.
- Staging (`gamma/import_staging.py`): a directory per review, bound to the
  account and workspace, expiring after two hours (`TTL`; expired ones are
  swept on the next upload). The review's job drops the upload when it
  fails or is stopped. The import job holds a claim (`running/`
  mkdir; a `DELETE` meanwhile answers 409) while it reads the upload, and
  removes the review when it ends — done, failed or stopped. An import is
  refused (409) while the review's job still reads; once it starts, the
  review's job leaves Background tasks, the import's row standing for it. Staging has
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
  the report. It uploads the file itself, then follows the review's job
  (`tasks.adopt`, its progress block while it reads) and reads the review
  once it is done; then it follows the import job
  (`tasks.start("import", …)`) with its progress and Stop. It may close
  while either job runs, and the job's row in Background tasks opens it
  again on that job: a review's (`{previewJobId, previewJob}`, no file: its
  review once read), an import's (`{jobId}`: the summary then shows the
  library column only). A review read while its dialog was closed is
  offered in the pill too. A review's job no listing carries any more (its
  row cleared, or dismissed by an import from another tab) is asked for by
  its id, and one gone from the server is taken as ended: its review is
  read if it is still kept, else the dialog says the server no longer
  holds it. While the file goes up it is the dialog's own, not a task, and once
  a review shows, closing drops it: a close by the backdrop, Escape or ×
  asks first then, since it stops the upload or drops the staged review
  (or the choices of one gone). A close while the review is read asks
  nothing and leaves it to its job. Its Cancel stays direct and drops the
  review, stopping a job still reading it, also in a dialog opened on that
  job's row (the job's params name the review). While it shows a review, the dialog sends a
  keep-alive every 10 minutes and when the window regains focus or becomes
  visible. A review gone anyway (404 once swept, from the keep-alive or
  from Import) is cleared with a message, and Retry preview uploads the
  file the dialog still holds again, keeping the selection (a dialog opened
  from a row holds no file: it asks for the file again). A preview the
  server refuses (a failed review job: a file the source does not read,
  such as a Supernote `.note`, or one too large; or a start refused with
  400, 413, 415) shows its reason without the retry a lost connection
  gets, and so does a review stopped from its row. A review job the
  server's restart cut short, or a bug failed (`importReview.reviewRefused`
  tells them from a refusal by the error), offers Retry preview, which
  drops the upload the restart left and uploads the file again (a close
  drops it too). The source column is headed by
  what was uploaded: one file, a ZIP's contents, a folder, or the files
  picked together.
  `ImportTree.jsx` renders one tree with its checkboxes (a folder toggles
  its descendants). A tree of more than 300 files (a vault) starts with its
  folders closed, a closed folder's rows are made only once it opens, and
  a list shows 300 files at a time, so 20,000 notes open and click as fast
  as a few. In the library column a sub-page (an entry whose `parent` is
  another entry's id) is a row under its parent's row, at any depth,
  rather than a file of its top's folder (`buildImportTree`'s `inside`
  lists, paged the same way); an entry whose parent is not listed (a page
  already in the library, or one the filter hides) or whose chain of
  parents loops stays where its folders put it. A page row's checkbox
  covers the pages inside it, as a folder's does (`rowItemIds`), since
  without their parent they would be filed by their directories. One of
  them ticked under an unticked new parent is drawn where the import then
  files it, its entry's `folders_alone` (`buildImportTree` takes the
  selection for that; a page already in the library stays the parent
  whatever is ticked, and after the import the report's own places are
  drawn).
  `importReview.js` owns selection, filters, tree construction and the
  links and order (`importLinks`, `orderLikeSource`, `firstRow`, whose
  `sub` leads to a row inside a page's row; filters only hide rows, and
  hidden selections stay selected);
  `importApi.js` the upload, the job's body and the discard;
  `shared/lib/xhrUpload.js` the upload progress.
- The columns find each other's rows (`ImportTree`'s `link` prop, from
  `ImportReviewDialog`). A row with a match in the other column has its
  name as a button. Choosing it lights every match in both columns and
  shows the first match in the other column; a second click lets go.
  - The folders on the way open at once (`importTreeReveal`): a folder
    still growing open would leave the row short of where the scroll must
    reach. The reader's own folds keep their motion.
  - A long list grows to hold the match. Unless the match is in view, its
    column scrolls it to the middle; with the columns stacked (a phone),
    that column is brought into the dialog's view, clear of the sticky
    footer.
  - A match the filter hides brings the filter back to All. A hidden
    status line reads out the row chosen and how many matches it has.
  - Within each folder, the library column lists its pages in the source
    column's order, each by the first of its files listed there, so the
    columns line up. Both sort folders by name with numbers by value
    (`files/999` before `files/1000`).

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
the normal editor renderer, except a Logseq image size: `md_to_blocks`, and
so every import that parses with it (the zips and the paste as blocks among
them), turns `![alt](url){:width N}` outside code into the `![alt|N](url)`
Gamma stores (`note_markup.obsidian_image_sizes`); a synced vault's file,
read in spans mode, keeps `{:width N}` as it writes it
([synced_vaults.md](synced_vaults.md#the-lens)). A list item keeps its marker, as a list block does:
`- x` and `* x` are the block `- x`, `1. x` stays, and a task is `- [ ] x`.
A bullet that only wraps a block of its own (`- # H`, `- ```py`, `- - x`,
which a vault writes) gives way to that block; `- $$x$$ is it`, `- |x| is it`,
`- >5` and `- ```x``` y` are bullets, and a line that closes the fence it opens
is inline code wherever it stands, in the zip's own passes over a note too (they
read the parser's `_FENCE_LINE_RE`). A fence closes only on a line that is all
its mark and at least as long as what opened it (`markdown_import.shuts_fence`,
the rule of `notion_nfm` and, for mark and length, the editor's `mdCode.codeFences`),
in a list item too, so a ```` fence can hold ``` lines. On the page a line four
columns past its fence is code (`shuts_code`, CommonMark's rule; the vault's
`%%` pass reads it too), and in a list item any indent
closes. Lines indented under a list item continue
that item, whether directly below it or after a blank line when aligned with
the item's text. Text indented deeper after a blank line becomes a child block,
which is how Notion exports a toggle's content. So does a fence or `$$` opened
on an item's next line: the editor draws a fence's card only at column 0.

`$$` opens multi-line math only at the start of a line's text with no pair on
that line, as in remark-math, in an item as at the top: `- costs $$ and more`
keeps what follows out of it, while `- $$`, `- [ ] $$` and `1. $$` take the lines
below whatever they look like (a row like `- x + y &= 3 \\` is no list item). One
left mid-line in an item closes on the item's next `$$`, as `scanMathSpans` pairs
them in a block, so `- is $$`, `  x`, `  $$` stays one block. That `$$` may start
its line, even at column 0, and opens nothing, unless it is a formula of its own:
a `$$` alone on its line, remark-math's close, comes next, within the item or, for
a `$$` at column 0, anywhere below.

One limit: a `$$` at the start of a line that opens and is never closed takes the
rest of the document into its block, at the top or in an item, with no text lost.
`- $$ never closed`, `- next`, a blank line and a paragraph are one block. The
opener looks at its own line and not for a closer below it, so this is remark-math's
reading at the top level, while in an item remark-math ends the math with the
item and `scanMathSpans` leaves it raw.

Gamma's readable export writes every block as a `- ` bullet, so `x` is `- x`
and the list block `- x` is `- - x`, and there the marker says only "a block".
`markdown_import.readable_outline` knows such a file by the one shape that
writer has: a front-matter `title` and no `gamma_id` (the line the gamma-sync
client writes, and a folder the desktop app kept before synced vaults; their
bullets are lists, and a synced vault writes no such line), then the H1, BibTeX if any, and only `- `
lines at the start of a line, so every branch of the writer keeps a block's next
lines under its bullet (`_bullet`). `md_to_blocks(..., outline=True)` then reads each
`- ` as a separator, which gives the blocks back as they were, from old zips
too, with a fence or `$$` kept in its item with all its lines. A note of another
app with that shape loses its markers, and a readable file edited out of that
shape keeps them, as does an old zip's page with a multi-line area-highlight note
or link label, which the writer used to leave at the start of a line; no text
goes either way. What each caller passes:

| Caller | `outline` |
|---|---|
| Zip (`ZipRun.parse`) | `plan.outline`: `readable_outline`, for a note of no Notion id outside a vault. A vault's note and a Notion page of that shape keep their lists |
| `POST /api/import/markdown`, `POST /pages/from-file` (`markdown_page`) | `readable_outline` |
| `POST /api/markdown-blocks` | The request's `outline`, which the editor sends for its own copy of a subtree (`sourceCopy.isOutlineCopy`) |
| `seed.py` (the welcome page) | Never: its lists stay lists |

Pages imported before markers were kept read as indented paragraphs. The
markers are gone, so nothing repairs them.

`md_to_blocks(..., spans=True)` is the same parser for a synced vault's lens
(`gamma/md_lens.py`, [synced_vaults.md](synced_vaults.md#the-lens)), which
writes a file back by patching its own lines. Every block also carries the
lines it came from (`Span`: the source lines, the raw indentation and
marker, the block's class), and a few rules change so that a block's own
lines are one run: a paragraph under an item ends before what follows it is
read, a line that would go on an item with children is one more child, a
`$$` at the left edge after a blank line is out of any item, only spaces and
tabs are whitespace, a line of code keeps its trailing whitespace, and after
a blank line at the top, with no list open, what stands four columns in is
one `verbatim` block of raw lines. Without `spans` the parser reads as it
always has, byte for byte: every importer above passes none.

An empty list block is its marker and a space (`- `, `1. `), and the readable
and vault exports keep that space (`markdown_export.block_text`), which is what
tells it from a block that is only a dash: `- -` reads as `-`, `- - ` as `- `.

A front-matter value loses one pair of quotes and no more, so `'Tis` and
`Say "hi"` stay as they are (`_fm_scalar`). A double-quoted value is read as
JSON, which is how the vault writes one, unless that fails or gives a control
character: a hand-written `"C:\foo"` is a path, not a form feed. A `'it''s'`
is `it's`, and a `[a, b]` list keeps the commas inside its quoted items. The
readable export writes a title bare, so a title that is all in quotes (`"x"`)
loses them there once.

In mixed folder uploads, Markdown note pages and PDF pages
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

`POST /api/import/markdown-zip` (Import dialog → "Markdown notes" or "Notion
pages", pick a `.zip`; `gamma/markdown_zip_import.py`) turns a zip of `.md`
files into one page per file. For a `.zip` the two cards differ only in their
text (`IMPORT_SOURCES` in `transfers/transferFormats.js`) and the review's
title (`FORMATS` in `transfers/importApi.js`), and only Markdown's picker takes
a `.md` as well: the server reads the zip's dialect, so a Notion export under
Markdown notes reads the same. A review opened again from its job, which keeps
the endpoint, is titled Markdown's. One logic covers a zipped Obsidian vault, Notion's Export →
"Markdown & CSV" or HTML (with subpages), Gamma's own Markdown export and any
zipped folder of notes, as stages of `ZipRun`
(the survey behind the Obsidian mapping: [docs/research/obsidian.md](../research/obsidian.md)).
What differs is the zip's dialect, picked once after the walk
(`gamma/markdown_zip_dialects.py`, Notion's in `gamma/notion_import.py`):
**Vault** with `.obsidian` in any path, else **Notion** with a 32-hex id in a
note's or CSV's name or a page of Notion's HTML export among its `.html` files
([below](#notions-html-export)), else **Plain**, which Gamma's exports are too.

| Rule | Notion | Vault | Plain |
|---|---|---|---|
| `notion_id`, match by id | Yes | No | none hold one |
| Id left out of the title | Yes | Yes | none hold one |
| CSV | A table page with an id, else as Plain | As Plain | A file a note can link; left out with a warning if none does |
| Directory pairing, flat tree, stubs, cut titles, absent CSVs | Yes | No | No |
| Relations | Yes | No | No |
| Asides, quote tails, link and property lines | Each file with an id | No | No |
| `^id` anchors | None in a file with an id, else as Plain | All | Those a `#^id` link in the zip names |
| Wikilinks | Text in a file with an id, else links | Yes | Yes |
| `%%comments%%` | No | Yes | No |

The rest of this section holds for every dialect unless it says otherwise:

- **Title**: front-matter `title`, else the leading `# H1` (stripped from
  the body — Notion and Gamma both write one; a closing run of `#` counts
  only after a space, as in CommonMark, so `# C#` is "C#", though an H1
  whose whole text is the front-matter title repeats it: the export writes
  the title "Issue #" as `# Issue #`, and an H1 taken for the title loses
  its Markdown escapes, so `# Foo \[bar]` is "Foo [bar]"), else the filename with
  Notion's `Title <32-hex id>` suffix removed. In an Obsidian vault
  (recognised by its `.obsidian/` folder) the filename is the title, as in
  Obsidian itself, and the H1 stays in the body unless it repeats the title.
- **Folders**: directories become folders, each directory name a folder
  name as written (Notion's id suffix stripped), made below the import's
  destination folder (the form's `folder`, an id: the dialog's target, the
  open library folder) where missing and reused by name where there
  (`ops.ensure_filing`, matched as `blocks_store.named`: exactly, else
  ignoring case). Notion's directory for a page holds the pages inside
  it, which come in as sub-pages (below) and so need no folder. A
  front-matter `folder:` path wins over the directory. The folders
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
- **Pages inside pages**: a note's page goes inside another note's page
  ([home_library.md](home_library.md) "Sub-pages") when its front matter
  names that note as `parent:` or, without one, when the note sits in
  Notion's directory for that page. A `parent:` is the readable export's
  relative link (`parent: ../Top-x.md`) or a vault's `"[[Parent]]"`,
  unwrapped like `source:`. It is resolved from the note in this order
  (`parent_note`): the note beside the note's own directory when its name
  is the value's (the layout the exports write, `Top.md` beside
  `Top/Child.md`), then a wikilink's exact vault path, then as a link is.
  Notion writes no `parent:`: it puts the pages inside `Title <id>.md` in
  a directory beside it, at every depth, and a database's row pages in a
  directory beside its `Name <id>.csv`. Until about November 2025 that
  directory was named as the file (`Title <id>/`); since, it is the title
  alone (`Title/`), and a second page of the title in one directory gets
  its id's first and last four digits (`Title abcd-wxyz/`). So a note
  directly in a directory goes inside the note (`.md` or `.markdown`) or
  database (`.csv`, or the `_all.csv`) beside it that the directory names
  (`paired_page`, `owner_of`): `Title <id>/` the file named exactly so;
  `Title abcd-wxyz/` the one of that title whose id starts and ends so (a
  name that only looks like that, `Budget 2024-2025/`, and matches no page
  of the shorter title is a plain title); plain `Title/` the one file of
  that title with a Notion id not claimed by a partial-id directory, and
  when several are left, the one whose lines link into the directory,
  else the database whose rows the notes in it are (a page and a database
  of one title side by side). Titles compare as `_norm_name` does (NFC,
  spaces collapsed, case folded). A vault or a plain zip carries no
  Notion ids, so its `Topic.md` beside `Topic/` stays a page beside a
  folder, and a directory that pairs with nothing stays a folder. A
  database's page can also come as a note of the database's own id beside
  its CSV (`People <id>.md`: a line linking to the CSV, then the view's
  `filters:` and `sort:`); that note is folded into the database's page,
  its other lines above the table, links to it resolving there.
  A flat Notion export ("Create folders for subpages" off) has every page,
  database and attachment in one directory and no directory in it. When a
  zip's Notion files all sit in one directory that holds no directory,
  its tree is taken from the pages (`_flat_tree`). A page's sub-pages and
  databases are its lines that are one link to them (`_link_lines`: how
  Notion writes a sub-page, a database or a link to a page) under their
  title — the H1's or the file name's, compared by letters and digits
  (`_title_key`: `[**Plan**](…)` names `Plan`) — or under no label (an
  untitled page). A file with no Notion id beside them is no page of the
  export, and a link to it nests nothing. A database's rows are notes
  titled as its CSV's first column (an empty cell, or a wholly blank row,
  is "Untitled"): per row name as many notes of that title as it has such
  rows. All databases' offers are ranked together, so a note goes where
  its values fit and the zip's order does not decide: those whose
  property values match a row's cells best first, then those sharing more
  columns, then those no page links to. Exact titles go first, for every
  database, then titles by letters and digits (`C` is not `C++`), where a
  note a page links to must also match a value. Both ask a note with
  property lines for one of the columns (a row page can show a property
  its CSV leaves out); rows still without a page then take any note of
  their title, since Notion leaves a row's empty properties out and a
  first line such as `Attendees: Ada` reads as one. A note a database
  takes is its row whichever page links to
  it. A callout's lines are no link lines (a navigation box's links are
  mostly not its page's sub-pages), so a sub-page or database linked only
  inside a callout gets no parent. Walked breadth-first from the files nothing names, then from the
  first file still unreached in zip order (a sub-page whose line links
  back to its top names it too), the first to name a file is its parent,
  so a link to a page from its sibling never takes it from the page
  above. Top-level pages of a workspace export that link to each other
  would nest under the first: this tree is inferred, and no flat export
  seen carries Notion's `index.html` manifest
  ([research note](../research/notion-roundtrip.md)). When the
  parent is another chosen note of the zip, or one imported before (the
  same digest or `notion_id`), the note's page stores that note's page id
  as `parent_page`, written by `insert_page_tree` with the rest (every
  page id is known before any page is written). The import writes each
  parent before the pages inside it (`depth`; within a depth the notes
  keep their order), so one that stops or fails leaves no page inside a
  page it never wrote: a database's table goes before its rows, though its
  CSV is read after every note. Such a note is filed where its top is: its
  directory and any `folder:` are passed by, and no folder is made for its
  parent's directory. A page already in the library is not changed. A
  `parent:` that names nothing in the zip or the note itself names no
  parent, and neither do parents that close a cycle or nest deeper than
  `MAX_PAGE_DEPTH` (64) pages, counting the library's pages above a parent
  imported before (`above`): those notes are filed by their directories,
  as library pages. Parents are found, and cycles and depth cut, over
  every note of the zip, chosen or not (`every`, `every_lookup`); then a
  parent left out of the selection is dropped, and nothing takes its
  place: not another note of its name, not the Notion page whose directory
  holds the note. So what the preview reports (every note chosen) holds
  for any selection, and its `folders_alone` is where a note goes without
  its parent.
- **Links**: a link to another note in the zip becomes a `[[page]]` mention
  of the page it produced — Markdown links (Notion's percent-encoded
  `[Sub](Parent%20<id>/Sub%20<id>.md)`, whose balanced `( )` stay unencoded
  as in `Tasks%20(grid%20view)%20<id>.csv`, Gamma's `[label](Page-id.md)` and
  `*(from [title](file.md))*`) and Obsidian wikilinks (`[[Note]]`,
  `[[Note|alias]]`, `[[folder/Note]]`) alike. A target is resolved relative
  to the note, then as an exact vault path, then by basename anywhere in the
  zip (nearest directory wins), case-insensitively — Obsidian's own rule.
  `[[Note#Heading]]` and `[[Note#^id]]` point at that heading block
  (matched as Obsidian's `stripHeading` does, punctuation read as spaces:
  `## Learn C#` by `[[Note#Learn C]]`, the first of two alike winning) or
  anchored block (`^id` markers are indexed per note and removed from the
  text; an anchor on its own line belongs to the block before it; a
  dialect keeps them as above, because `E = m c ^2` ends the same way and
  folder sync writes real ones with no `.obsidian`, so `plan_files` reads
  every body for `#^id` before it parses the first; an anchor taken off a
  bullet's first line, `- $$ ^id`, leaves what it hid, and a bullet that only
  wraps a block gives way to it),
  `![[Note#^id]]` becomes a Gamma synced block `![[id]]`, while `![[Note]]`
  and `![[Note#Heading]]` degrade to mentions (a Gamma embed shows one
  block). A link or image pointing at a bundled file uploads it
  (`store_file_stream`, content-hash dedup, the storage quota — a file over
  it is a warning and the link stays as typed) and points at
  `/api/uploads/…`; `![[img.png|300]]` / `|300x200` becomes the editor's
  `![|300](url)` size form, other pipe text the alt, and a Logseq graph's
  `![alt](../assets/x.png){:width 300}` comes in as `![alt|300](url)`. A CSV that is no table
  page is such a file; one no new note links is left out with a warning
  once the pages are written, and a zip holding no note and no table page is
  a 400. Links that resolve to
  nothing stay as typed (so a Gamma `[[id]]` is never touched). A link to
  a Notion `Name <id>.csv` the export left out reaches the page of that id,
  the way a `.md` link reaches a database; a page's link to its own absent
  CSV stays as typed, with the warning.
- **Obsidian specifics**: front-matter `tags` (list, flow list or comma
  string; `tag` too) become labels, label blocks made by name where missing
  (a name keeps its `/` and `,`), `aliases`
  are kept in `properties.aliases`; `> [!type]+`/`-` fold markers stay
  (the editor folds a callout by them); `%%comments%%` are removed in a
  vault (inline or block, never inside fences). Inline `#tags` and
  footnotes pass through as text. A task is written as the editor's
  `- [ ] text` / `- [x] text`, whatever bullet, case or spacing it came
  with (Notion writes `- [ ]  text`), so pasted text keeps it too, an empty
  task's included. A fence indented under a list item comes without that
  indent.
- **Notion specifics**: a database `Name <id>.csv` becomes a page holding
  the table (`_all.csv` preferred when both exist — it has every row — in
  the column order of the view's CSV, since `_all` sorts its columns). Every
  row is kept, 500 rows (or 100,000 characters) to a table, each table a
  block of its own under the header, so each renders and stays under a
  block's 200,000 characters, but for a single row past that: it stays
  whole, to read, and takes shortening before it saves. A CSV's lines end
  as the file ends them, a bare CR too, and one that cannot be read is a
  warning, the rest of the zip imported. Columns past 40 are left out with a warning.
  Its row pages `Name <id>/Row <id>.md` are pages inside it (above). Two
  databases of one name in one page (Notion's `Untitled <id>`) are two
  pages, each holding its own rows, since their ids differ. Notion cuts a
  file's name at 50 characters and a CSV has no H1, so a database of a
  longer title takes it from its stub note or the line linking to it. A
  stub's text that takes the page past `MAX_BLOCKS` stays out with a
  warning and the table comes in: its page already has rows and links by
  the write stage, so `fold` checks it (`ZipRun.too_many`);
  in a Notion note `<aside>`, its tags alone on their lines and indented
  as the block is (in a toggle or a list item; in a code block it is
  code, and other sources' `<aside>` stays HTML), becomes a `> [!info]`
  callout, its first line the
  title (since 2026 Notion writes the icon alone on that line; the blank
  line after an icon-only line goes, after a line of text it stays and
  parts paragraphs); the empty `>` line Notion ends each quote with is
  dropped, also when the quote is indented in a list item or toggle or
  sits in a callout (`_drop_quote_tails`); the row pages' `Property:
  value` lines stay as text. Such a note's headings hold nothing: they
  stand side by side with what follows them (`flat_headings`), as they do
  in Notion. Notion escapes nothing, so a `<tag>` in its text is written
  `\<tag>`, in a database's cells too, or the note would read it as HTML
  and drop it (`_escape_tags`): not in code (a fence in a quote or callout,
  or opened on a list item's line, included), math or a link's target, and
  not `<u>` or a callout's `<img>` icon, which Notion means as HTML. A
  relation, in a database cell or a row's
  property line, is written `Title (…%20<id>.md)` (the path relative to
  where Notion pleased: the database's page for a cell, the row for a
  line) and becomes a mention of the page its path names when that file
  has the id (a zip can hold one page twice), else of the page of that id,
  of the import or the library (`_link_relations`). Its title runs from the relation before it,
  the cell's `|` or the line's `Name: ` up to the path, and must start
  with the file's name compared by letters and digits only (`_loose`:
  Notion writes `GAOS 1.0` as `GAOS 1 0 <id>.md` and cuts names at 50
  characters, sometimes inside a parenthesis, which a link's target may
  then leave unclosed).
- **Gamma specifics**: the front matter's `source:` restores the PDF when it
  is bundled (`assets/<sha>.pdf`, or the vault export's quoted
  `"[[Paper.pdf]]"` → the page's `doc`, stored: the page becomes a paper
  again) or the remote URL (a `doc` at that address) when it isn't; the
  export writes there the address the page's document is fetched from
  (`page_document`'s `url`: its stored file's, a duplicated page's
  included, or the address it is shown from;
  [documents.md](documents.md)). Such a PDF's `doc.name` is empty, and
  `original_filename` stays the note's own file name (the Markdown
  provenance beside `markdown_import`); `doi`/`authors`/`year` →
  `properties.meta` (`source: manual`; `authors` as a YAML list or a comma
  string), the ```` ```bibtex ```` block → `properties.bibtex`. Highlights come back as their quote blocks, not as
  positioned highlights — the Gamma format (`?mode=gamma`) is the lossless
  route; Markdown is for notes and for other apps.
- **Idempotent**: a `.md` already imported is skipped, and links to it
  resolve to the existing page, so re-importing an export adds nothing. A
  note with a Notion id is the page of that `notion_id`; any other is the
  page of its bytes (the `markdown_import` digest). Two Notion pages of one
  template have the same bytes, so for a Notion note the digest finds only
  the page made from that very file alone (`POST /import/markdown`: no
  Notion id, the same `original_filename`), and that page once per import.
  A database's stub note imported again resolves to the database's page
  with its CSV.
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
  Notes pack about 4:1 and photos or PDFs 1:1, a zip bomb about 1000:1. What
  a small file can ask of the server is linear in it: no pattern over a note's
  text, a CSV's cell or a file's name rescans the line per unclosed `](`, `[`,
  `<img` or space run (a link's target holds no `](`, a label no unbalanced
  `[`, a heading's first run of spaces gives nothing back, and a code span's
  mark is read to 16 backticks), and a long entry name or a wide CSV costs its
  bytes, with no directory prefix kept and no row widened. A file that cannot
  be read, a page, a table or a bundled file, is a warning and fails nothing.
  A bundled file has no size cap: it is streamed into storage, never read
  whole.
  A nested zip is spooled, and a bundled file stored, only while the disk
  keeps `import_staging.MIN_FREE_BYTES` free (507 for the zip, a warning
  for the file), as a staged upload must.

The report's counts (`pages_created`, `pages_skipped`, `assets_stored`,
`links_resolved`, `notion`) and warnings appear in the shared dialog. `pages`
lists selected pages and their destinations, including pages already in the library,
each with `parent`, the id of the page it goes inside: a new page's
`parent_page`, an existing page's effective parent, `""` for a library
page. A new sub-page's entry also has `folders_alone`: the folders it is
filed in when the page it is inside is left out of the selection, its
directory's below the destination, as a library page's would be (the
review computes it from the preview, made with every note chosen). The same engine's `preview=True`
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
carry none — and a sub-page's parent (`parent:`), so importing the zip into
a folder rebuilds the same tree there, the pages inside pages included.
The path is the names joined with `/`, a `/` inside a name written as `-`
(so "TCP/IP" re-imports as one folder, "TCP-IP"); the importer splits the
value on `/` (`blocks_store.split_path`).

The review and the report list each page's `folders` as paths, each a list
of names from the library's top (`[["Imports", "Notes"]]`: names may hold
`/`): a new page's are the destination's path and its own, an existing
(skipped or merged) page's its current ones, and a sub-page's, new or
existing, its top's.
The report's `folder` is the destination's id.

## Notion's HTML export

Notion's Export → HTML comes through the zip import above: the same stages, the
same **Notion** dialect, the same rules for folders, pages inside pages, links,
files, selection and re-imports. Only the way a page is read out of its file
differs (`PageSource`): `NotionHtmlSource` in `gamma/notion_import.py` takes the
page through `notion_html.read_page` into the block model (`notion_blocks`) and
`notion_gamma.to_gamma` writes that as Markdown nodes. So its links, images and
sub-pages take the road a Markdown export's take (`rewrite_links`, `owner_of`,
`paired_page`, `_flat_tree`), while a block of code or a formula is not looked
in at all, and a link around code and math is found by `WRITTEN_LINKS_RE`.

- **Sniffing** (`html_pages`, `notion_html.is_notion_page`): a `.html` is a page
  of Notion's when its first 64 KB hold the article mark
  (`<article … class="page…"`) and the body mark (`class="page-body"`). The
  stylesheet comes first, so the article starts up to 17,696 bytes in. A page's
  properties come before its body and run as long as they like, so a file with
  the article mark alone is read on, to the 5 MB of `MAX_MARKDOWN_BYTES`. A file
  longer than that counts as a page, for the import to say it left it out. One
  such page makes the zip Notion's whatever its file names hold, unless it is a
  vault. Any other `.html`, `index.html` included, stays a bundled file, so a zip
  of plain HTML with no note and no CSV stays a 400. One that cannot be read
  counts as no page, with a warning, since every `.html` of a zip is looked at,
  linked or not.
- **The page**: its title is `h1.page-title`, else the file name; its id the
  `article`'s, else the file name's. Every era is read, as a zip holds one era
  but people arrive with old ones: what 2026 writes in `data-notion-*` attributes
  an older export says in classes and in the KaTeX under a formula. The parser is
  the standard library's, the Zotero notes' with a `<p>` that stays open at a
  `<div>`, because Notion writes a paragraph's children in a `div.indented`
  inside the `<p>`, where an HTML5 parser would move them out. A page deeper
  than 200 elements goes on as flat text with a warning, one not in UTF-8 is left
  out with a warning, and so is one past `MAX_BLOCKS`. The parser keeps 256
  elements open (`_Parser.max_open`; a Zotero note's has no limit) and reads what
  nests past that flat, so tags that close nothing cost a bounded walk on a 5 MB
  file.
- **Blocks**:

  | Notion | Gamma |
  |---|---|
  | Bullet, number, to-do | `- x`, `N. x` from the list's `start`, `- [ ] x`, one block per `li`, its children blocks |
  | Toggle with children | The text, `collapsed` and the children: `details[open]` says nothing (82 of 82 in the samples carry it), so every one comes in closed |
  | Toggle with none | A plain block: a later Tab under a `collapsed` one would hide what is indented |
  | Toggle heading | The heading, `collapsed` and the children. Any other heading holds nothing, as `flat_headings` has it on the Markdown route |
  | Callout | `> [!type] icon`, the type by colour: gray, brown and none `info`, red `danger`, orange and yellow `warning`, green `tip`, blue `note`, purple and pink `important`. No more of the colour is kept |
  | Text colour, background | The editor's span (`mdMarks.js`), a background its tint. `yellow_background` is `==x==`, brown the export's own `#9f765a`. Notion's `teal` is its green. `tests/shared/notioncolors.json` pins these |
  | A block's colour | Falls on its runs, not on code, equations or tables |
  | Underline, date | `<u>x</u>`, `@2026-10-09` |
  | Formula | The TeX of `data-notion-equation`, or of the older `annotation` |
  | Text | Literal: `$5`, `<tag>`, `[x]`, `@name` and `1. Intro` are escaped, not read |
  | Image | `![caption\|W](src)`. The export has no alt text, so the caption stands there, hidden, and the width is kept |
  | File, audio, video | `[name](url)` |
  | Bookmark, embed | `[title](url)` without its description; a bare address |
  | Table of contents, empty paragraph, page icon and cover | Dropped |
  | Columns, synced block | Read in place, one after another |

  A link to a block goes to its page: every anchored link in the samples is a
  table of contents'.
- **Databases**: the CSV stays the source of rows, as on the Markdown route, and
  `_RELATION_RE` takes `.html` paths in its cells. An HTML zip holds one CSV for
  a database, every column in `_all`'s order, so the table in the page gives the
  view's order and `h4.collection-title` the name the file name cut
  (`db_order`, `db_title`; pages plan before CSVs). A `div.collection-content`
  is a line naming the database: a mention of its table page once its id, the
  CSV's, is in `csv_by_id`, its name otherwise. A row page's `table.properties`
  becomes one first block of `Key: value` lines, as the Markdown export writes
  them. Those values are what the page shows, which `_row_fit` matches against
  the CSV's cells: a date here is ISO and a relation a title where the cell has
  the long form, so neither counts towards the row in a flat export, and two rows
  of one name that differ in nothing else go by zip order. With directories no
  row is matched.
- **A page already imported from Markdown** is that page of `notion_id`, so it is
  skipped and HTML never upgrades it: delete it first to get what HTML keeps.
  A zip of `.md` and `.html` for one id is in no sample and has no special case.

`tests/test_notion_html.py` has the reader, `tests/test_notion_html_zip.py` the
zip, on `tests/notion_fixtures/`: the two exports of one synthetic page of
November 2026, and a page written by hand in the older eras' markup, which holds
the colour the Notion page lacks. A flat HTML export is in no sample, so its test
is hand-made too. What the 2026 format leaves unseen, a coloured block or callout
among it, is in the
[research note](../research/notion-roundtrip.md#what-the-html-export-holds).

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
into a page of the same work. `zotero_import.same_work` compares the DOI
when both have one, else the title, casefolded with punctuation as spaces.
The page's record counts, and so does the Zotero record it was imported
with (`zotero_rdf`'s `work`), so a record edited in Gamma still merges. An
item with no DOI retitled in Zotero is another work by this rule: once its
PDF's bytes change too, it comes back as a second page. A URL or
`urn:isbn:` key names the work itself and merges into the page carrying
it. The look-up reads every page
under the key through `idx_ub_zotero_key` ([user_db.md](user_db.md)), so
two such pages can sit side by side. The import maps
collections→folders, nested as in Zotero, made below the destination
folder (the optional `folder` form field, a folder id) where missing — an
item in no collection lands in the destination itself; an item that merges
into a page inside another page ([home_library.md](home_library.md)
"Sub-pages") files it nowhere, since it is filed where its top is, and the
preview and the report name its top's folders and the page it is inside
(`parent`) —,
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
`existing_in`). A page that owns no PDF is given the item's as its own;
one that shows a file it does not own (a duplicated page, one opened from
an address) goes on showing it, under its name (`_own_zotero_pdf`).
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
titled by its first line as text (`zotero_import.note_page`), filed in its
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
dialogs. iPhone/iPad Safari has no folder picker, and neither has the
Android app (a WebView's chooser picks no folder, and its files carry no
`webkitRelativePath`), so there ".zip" is the button (`canPickFolder`).
A folder is packed into a ZIP in the page
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
  written corner. A shape's sides, sampled every 2 pt (`SHAPE_STEP`), keep
  their corners because the outline cuts a straight run finer before it
  draws it ([handwriting.md](handwriting.md) "Straight runs").
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
  widths scaled, their shapes from the shape list each member holds. A
  member of a type the import does not know, a type 1 member that is no
  archive, a type 2 member that is no shape list (a mapping whose `shapes`,
  `kinds` and `indices` are lists), or an item that is no member at all is
  left out and said ("1 item in a group of handwriting could not be read
  and was left out"); the group's other members are drawn. The sample's
  70 type 2 members are all shape lists.
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
  sheet on paper. Its file is stored as it came, a TIFF, BMP or AVIF as a
  PNG (`note_app_import.AppImport.store_picture`, which the GoodNotes
  import shares; Limits, below). Its frame (`documentOrigin`,
  `unscaledContentSize`) goes through the page's frame and is kept on the
  page (`picture.keep_on_surface`): one that crosses the page's edge is
  moved onto it whole, scaled down when it is larger than the page. A
  frame with a side under 8 pt (`picture.MIN_SIDE`) first grows about its
  centre until that side is 8 pt, its aspect kept; on a page too small for
  it then, `keep_on_surface` scales it down and the short side stays 8 pt.
  The turn (`rotationDegrees`, in radians, taken within a half turn either
  way, so a huge one is the same turn) is `angle` in degrees, and the
  flips are `flip_h` and `flip_v`. The note's `locked` is the picture's
  lock, which wins over Gamma's copy of it.
- The crop (`FigureCropRectKey`, in the file's pixels; an `.ntb`'s in the
  asset's logical size, `crop_size`) is fractions of the image as the
  browser shows it; one that holds nothing of the image is the whole image
  in the whole frame. Inside the image a stretch needs nothing: the
  frame's aspect is not the crop's. A crop that reaches past the image is
  cut to it, and the frame by the same fractions of the crop in its own
  axes, mirrored for the flips, its centre moved with the part kept and
  turned with it (`notability_map._cut`). So the part of the image kept
  lies where Notability drew it, unstretched, and a crop larger than the
  image shows the whole image in the part of the frame it covered.
- The image is the full-resolution one. When the note lacks its file or
  flags it `imageIsMissing`, the smaller copy (`contentSnapshot`) stands
  in, and failing that an animated GIF's animation alone, the still frame
  then not named missing. A stand-in is shown whole in Notability's frame,
  since the crop is measured on the image the note lacks. The report says
  so ("1 picture shown cropped comes in whole: the note lacks the image
  its crop is measured on"), unless the crop is all of that image (at its
  origin, with the stand-in's aspect: `notability_map._whole`). With none
  of them the picture is named missing.
- The caption (`captionFieldText`, when `isCaptionEnabled`) is the block's
  content line by line. A line ends at any line terminator (`\n`, `\r\n`,
  a lone `\r`, Notability's line separator U+2028, U+2029), and each line
  is trimmed and escaped as plain text (`text_box.markdown_of`), so none
  reads as a heading, list, quote, rule or code block. It is cut at
  `ops.MAX_CONTENT` characters, never inside an escape, and the cut is
  said ("1 picture caption was cut short").
- A picture whose file is missing, past `MAX_HELD_BYTES`, or not a
  readable PNG, JPEG, GIF, WebP, SVG, TIFF, BMP or AVIF is left out with a
  warning.
- An animated GIF is its animation whenever the note has it within
  `MAX_HELD_BYTES` and it stores, cropped, flipped or turned alike: the
  browser animates it so. Otherwise it is its still frame, unsaid.
- An `.ntb`'s picture (a block of the image type, or one that names an
  asset) is as its record and the log's later edits and moves leave it
  ([research/notability-format.md](../research/notability-format.md#ntb),
  "A log"). Its frame is its size times its scale, and its flips and lock
  are read. Its crop, which Notability measures on the image as flipped,
  is mirrored onto the image as stored. Its caption is the text whose text
  field names it, plain with its line breaks, when the block shows one
  (EnableCaption).
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
   the upload, is skipped and shown where it is: in the folders it is
   filed in, a page inside another page's its top's
   (`blocks_store.filed_folders`), as a GoodNotes notebook's too.
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
- A PNG, JPEG, GIF or WebP picture (by its first bytes) is stored as it
  came and never decoded: only its size is read, from the header
  (`picture.image_size`, the EXIF orientation applied as browsers apply
  it), so a picture of any declared size costs no more. So is an SVG, its
  size read from its root element (`picture.svg_size`); its scripts never
  run ([pictures.md](pictures.md#svg-pictures)). A TIFF, BMP or
  AVIF, which a page cannot show, is decoded into a PNG of its first frame
  as shown, its EXIF orientation applied (an AVIF's `irot` and `imir` too,
  as Pillow reads them), so the crop's fractions hold
  (`note_app_import.picture_file`). A 16-bit grey one stays 16-bit (a
  32-bit one is cut to the 16 bits a PNG holds), and a CMYK or Lab one
  loses its colour profile, which an RGB PNG cannot use. Its pixels, and
  a TIFF's tile, which is decoded whole however little of it the picture
  shows, are checked against `pdf_image.MAX_DECODED_PIXELS` (40,000,000)
  before it is decoded, and one past them is left out ("Picture Scan.tif
  is left out: over 40,000,000 pixels"). This holds for a `.note`'s
  pictures, an `.ntb`'s `image/*` assets and GoodNotes' pictures alike.
  HEIC, PDF, an SVG outside the SVG namespace, a picture of floating-point
  samples (whose range is not said) and any other file are left out
  ("Picture Photo.heic is left out: not a readable PNG, JPEG, GIF, WebP,
  SVG, TIFF, BMP or AVIF picture"); an `.ntb` asset that
  is no `image/*` is an object of a kind the import does not know. A file
  is read and stored once per note however many pictures show it
  (`store_picture`'s `files`). A still is read again at most once, to be
  stored for a picture shown without the animation an earlier one showed.
  A bundled PDF is read and opened once however many names lead to it,
  and a recording file listed twice is one recording. Recordings are
  `.m4a` by their `ftyp`,
  their length read from box headers only, and are streamed into storage
  (`storage.store_file_stream`). A text box's, a formula's or a caption's
  text is cut at 200,000 characters, what the editor saves
  (`ops.MAX_CONTENT`).
- An `.ntb`'s log is read up to 1,500,000 records (`notability.MAX_RECORDS`;
  past them the rest is left out, with a warning), and its text styles may
  touch 20,000,000 characters in all (`notability_ntb.MAX_STYLED`; past them
  a text keeps its words only). Any number of records may point at one
  vector, so the ids and tables the bundle's vectors list are 6,000,000 in
  all, every reading of a vector counting (`notability_ntb.MAX_LISTED`);
  past them a vector lists nothing more, without a warning. A real note
  lists each record a few times at most: the sample lists 577 over its 430
  records.
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
  on text. A picture's flips, scale and caption, and the edits after
  insertion, are read from Notability's web app's schema with no sample to
  confirm them. A bundle with several PDFs does not say where their pages
  go: they are paper, with a warning.
- An `.ntb`'s log is replayed for blocks: versions, edits, moves, deletes
  and restores, in their order
  ([research/notability-format.md](../research/notability-format.md#ntb),
  "A log"); the sample is a compacted list. Handwriting comes in where it
  was drawn. A record that moves, turns or resizes a stroke, shape or group
  is not applied to it and is said once ("ink edit": "1 later move, turn
  or resize of handwriting is left out: it comes in where it was drawn"),
  and one that changes it otherwise is a record of a kind the import does
  not know. A page deleted in Notability is not taken out, since the pages
  after it would be numbered again ("deleted page": "1 page deleted in
  Notability is not taken out").
- Left out with a warning: a partly erased shape whose centre line does
  not read, an item of an ink group that does not read ("group item"), a
  polygon's fill, objects of a kind this import does not know, and
  anything past the limits. A template paper comes in as plain paper of
  its colour. Changed with a warning: a picture shown cropped whose image
  the note lacks comes in whole ("crop"), and a caption past
  `ops.MAX_CONTENT` is cut ("long caption").
- Not kept: a text box's turn, alignment, fonts, per-run colours and
  underline; a sticky's lines; Notability's pencil shading and dash
  lengths (Gamma draws its own); where a caption is drawn, and its style;
  a picture's rounded corners; note links and tags; the
  handwriting Notability recognised (`HandwritingIndex`).
- Gamma draws a sticky as ink, over every picture and text box, and a
  text box over the pictures. A picture or text box Notability draws over
  a sticky, and a picture it draws over a text box, so lie under them, and
  the report says so (`notability_map._covered`): "picture on sticky" ("1
  picture lies on a sticky note, which Gamma draws over it"), "box on
  sticky" ("1 text box lies on a sticky note, which Gamma draws over it")
  and "picture on box" ("1 picture lies on a text box, which Gamma draws
  over it"). Notability draws a page's objects in their z-order, which
  the reader sorts them by. Two meet when their frames, turned as Gamma
  turns them (a sticky and a picture about their centres, a text box or a
  formula upright), overlap by more than an edge. The test is exact, by
  separating axes: two turned frames whose bounding boxes meet do not
  count unless the frames do. A `.note` and an `.ntb` are checked alike,
  at the commit; a preview does not say it.
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
    Markdown and every number but `z` are the copy's, and the file is the
    note's. No `z` comes back: the media's `zIndex` order, which the
    blocks take as their document order, is the stacking.
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
its thumbnail (page 1's pictures as Gamma draws them, too), and holds the
NTB export to it, stroke for stroke within a point.
`test_notability_ntb.py` covers what the sample does not on bundles
`ntb_fixtures.py` writes: a log's versions, edits and moves of blocks
(each slot, and a turn, scale or crop table that clears it), deletes and
restores in their order and deleted pages; a picture's scale, flips,
flipped crop and caption; text boxes and a text's owner (its text field);
strikethrough in slot 13; a PDF's pages; damage and the `MAX_LISTED`
budget. `GAMMA_NOTABILITY_SAMPLES` names a folder of more notes to import
the same way.

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
- **Tape**: an opaque even band as wide as the tape. It does not hide and
  reveal (counted).
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
  `AppImport.store_picture`): its file stored as it came, a TIFF, BMP or
  AVIF as a PNG ([Limits](#notability-notes)), and once however many
  pictures show it; its frame (`#2`) through the page's and kept on the
  page, a side under 8 pt grown to it; its turn (`#3.#3`, in radians) as
  `angle` in degrees. A die-cut sticker, a one-page PDF, is rendered
  through PyMuPDF to a PNG, transparent around the art, once for all the
  pictures that show it: its longer side twice that of the largest of
  their frames, in points, and at most 1,024 px
  (`goodnotes_import._sticker`, through `pdf_text.page_raster`), and the
  blocks show that PNG. A picture
  GoodNotes shows cropped comes in whole (counted), since where the
  visible rect (`#3`) cuts it is not confirmed.
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
was. One page: the `.note` itself, `<Title>.note`. A folder, or a page
with the pages inside it: a zip of them, `<dir>/<Title>.note`, filed as
the annotated PDFs are (a sub-page's inside its parent's directory). The job's
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
  (`picture.image_size`), `$0` the same rectangle as the path Notability
  keeps beside it (`notability_write.crop_path`; null for a picture shown
  whole), `isFlippedHorizontal` and `isFlippedVertical` the flips, and
  `locked` the lock. Readers such as notesconverter and OpenNote show a
  crop only when that path is there. `captionFieldText` is the caption's
  plain text on one line, references read as their labels, and
  `isCaptionEnabled` is set when there is one. A media object holds no
  opacity, so a faded picture is opaque in Notability.
- Its file goes under `Images/` as it is. An animated GIF is its still
  frame under `Images/` and the GIF under `Assets/<id>/media.gif`, as
  Notability keeps one. Notability takes no SVG, so an SVG is a PNG of it,
  `SVG_PNG_SIDE` (2048) px on its longer side; one that does not draw is
  left out and said ("picture svg"). Two pictures that show one file are
  written as two files.
- A surface's pictures come before its boxes in `zIndex`, and both before
  its stickies (`notability_export._Page`), so pictures lie under boxes
  and both under stickies, as on screen. A page with a sticky over a
  picture or a box comes back as it was, with no warning.
- The pictures take their `zIndex` in the order they stack
  (`picture.stack_order`). The import reads them back in that order, with
  no `z`.
- A box holding one picture, as imports made one before a picture was a
  block of its own, is still a picture (`_boxed_picture`): in the box less
  its padding, as tall as the picture shown that wide. Its alt text is the
  caption, less an Obsidian size that ends it (`note_markup.alt_and_width`:
  `![Figure 2|300x200](…)` gives "Figure 2"). It comes back a picture
  block.
- The page's notes (each block that is on no surface, in document order)
  are the typed text (`flow_runs`): a paragraph per note at its depth, a
  top-level note plain and a deeper one a bullet, "n. …" a numbered item,
  "- [ ] …" a checklist item, "- …" a bullet at any depth, a heading bold, a
  note's lines joined by a line separator.
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
it lacks, no picture's opacity, no empty note, one paper, no empty last
sheet, and the order an import gives a page.

**Left out, and said:** highlights on a PDF's text (Notability keeps none;
the Annotated PDF does), a PDF page's sheets of paper, a recording that is
no `.m4a` (a browser's WebM or Ogg) or is still being made, the notes under
a recording, handwriting or a picture on no page, a file missing on the
server, and a picture's file that does not read.
Changed, and said: a sheet of a size Notability lacks, grid and dot paper,
a sheet of another paper than the first, a picture's opacity.

**Tests.** `test_notability_export.py` round-trips a notebook with a stroke
of every kind (pressure, tilt, time, a pencil's opacity, a calligraphy nib,
dashes, band 0, an `rgba()` colour, an extra key, 4,000 samples), shapes,
boxes, pictures (cropped, stretched, turned, flipped, captioned, locked,
faded, an animated GIF), notes and a recording of two segments; a
PDF page of three page sizes; and the sample's Note export, each the same
page after. With the copy stripped it holds every stroke to what Notability
keeps of it; with an item edited, that item comes as the note has it; and
the file is held to the sample's classes and keys, a cropped picture's
crop path to the sample's bytes. A sticky over a picture and a box comes
back over them, unwarned. An old box holding a picture comes back a
picture block, its Obsidian size no part of its caption. The browser suite
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
exported folder itself is in no collection —, the whole path for one page;
a sub-page's are its top's),
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
write directories, collections or tags. A sub-page
([home_library.md](home_library.md) "Sub-pages") is filed where its top
is: `Filing` reads the pages' effective parents once (`page_parents`) and
gives a sub-page its top's folders, its own `folders` passed by. A folder
export takes the pages inside its pages (`pages_in_folder`), each after its
parent. A page's export takes the pages inside it, at any depth, only with
`sub_pages` (the job's `sub_pages`, `sub_pages=1` on `/pages/{id}/export`,
`/pages/{id}/export-pdf` and `/api/bibliography`; `_page_ids`,
`with_sub_pages`), each after its parent, and a job reads them again when
it runs. A share that reaches the page reaches every page inside it
(`ShareScope.allows_page`), so they need no check of their own; a page the
share does not reach is refused (403). Without the flag, or on a page with
none inside, a page's export is the page alone. Where the
folders are directories (the vault, the annotated PDFs, Notability, the
Notion zip, the folder sync), a sub-page's file goes in a directory named for its parent,
beside the parent's file, when the parent's is in the same export
(`Filing.directory`, `parent_dir`), else where its top's folders put it.
A page's export of the annotated PDFs, the Notability notes or the Notion zip mirrors no
folder (`_Builder.directory`): the page's file is at the zip's top and each
page inside it in its parent's directory (`Filing.parent_dir`). The vault
keeps the page under its folders, as it does alone (`<folder>/<Title>.md`,
the pages inside in `<folder>/<Title>/`), the readable files nest from the
zip's top, and the formats without directories (notes-PDF, BibTeX, Logseq,
Zotero, Gamma) hold every page as a folder export does.
It walks the selected pages exactly once (subtree
fetch, `build_tree`, a progress report) and feeds each page to a per-format
`_Builder` keyed by the mode: `_MarkdownBuilder`, `_ObsidianBuilder`,
`_NotesPdfBuilder`, `_AnnotatedPdfBuilder`, `_NotabilityBuilder`, `_NotionBuilder`,
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
for a page export that writes one file with no local files, and
`_AnnotatedPdfBuilder` and `_NotabilityBuilder` the one `.pdf` or `.note`
of a page export that writes one (a page whose pages inside had nothing to
give included). A page export of several files is a zip named for the
page (`<slug>.zip`, `<slug>-annotated.zip`, `<slug>-notability.zip`).

`annotated-pdf` (`_AnnotatedPdfBuilder`) is each page's PDF with its
highlights, handwriting, text boxes and pictures as standard annotations
and, with the Notes switch, its notes as cards beside each page or on it, and
with the Comments switch its open comment threads as cards beside it
([pdf_typesetting.md](pdf_typesetting.md#notes-and-comments-as-cards);
`annotated_page_pdf`, what `/pages/{id}/export-pdf` runs). For one page it is that PDF. With the pages
inside it, a zip of their PDFs, the page's at the top (`Tree.pdf`,
`Tree/Branch.pdf`, `Tree/Branch/Leaf.pdf`; one PDF among them is that PDF);
`/pages/{id}/export-pdf?sub_pages=1` on a page with pages inside it answers
the same, without the `X-Annotations-Written` and `X-Notes-Rendered`
counts, and on a page with none answers as without the flag (the address
the MCP `export_page` names for its file, [mcp.md](mcp.md#export-a-page)). For a folder it
is a zip of them, `<subfolder>/<Title>.pdf`, the directories mirroring the
page's first folder path below the exported folder (`Filing`,
`obsidian_export.page_dir`), and a sub-page's in its parent's directory
when the parent's PDF is in the zip (`Review.pdf`, `Review/Cited.pdf`). A
duplicated page (a `doc` that is a `copy`) is exported on the stored file
it shows, as the page it was copied from is, here and in every export that
packs or draws on a PDF (Zotero RDF, Logseq, Obsidian, Notability: they
read `file_hash`, [documents.md](documents.md)). A
page with sheets of paper and no PDF is exported as its sheets. A page with
neither is left out, and the finished export lists it. A folder, or a page
and the pages inside it, with no PDF at all fails with the reason. A page
exported while the viewer shows its translation carries it, and its PDF
(not those of the pages inside it) is written translated before the
annotations go on ([translation.md](translation.md#exporting-the-translation)).
A folder's export carries the translations of its papers this browser left
translated, read in the browser with no viewer, each written into its own
PDF ([translation.md](translation.md#a-folders-export)).

The single-page exports are also plain functions, for callers without a
request: `page_builder` (one page through a mode's builder, what
`/pages/{id}/export` runs), `page_markdown` (the readable Markdown, unbundled,
upload links left as `/api/uploads/…`), `page_notes_pdf`, `annotated_pdf`
(what `/pages/{id}/export-pdf` runs) and `page_export_file` (a mode's
download as bytes, with `sub_pages` the pages inside too). They raise `HTTPException` like the
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
the file of each block's document, `doc`, + `/api/uploads/…` in
content/properties), and a
`manifest.json`. No `data.db`: everything in it is rebuilt (since schema
version 28; an older export carries its chats in one, and the import moves
them as a restore does). **There is no new import code**: any Gamma
imports it through the existing `/api/import-data?mode=merge` — additive
(the folders and labels join the workspace's trees: a block it has by id is
that block, else the one at the same path, or a label of the same name, else
it is added there; the pages are filed under the workspace's ids for them
and the folder chats follow — `ws_backup._merge_trees`, `_refiled`; a
reviewed import brings only the folders and labels its chosen pages need),
deduped by block id / the PDF a page owns (the `doc_id` column, a library
PDF: `SQL_LIBRARY_PDF`) / conversation / content hash, so re-importing
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

The review (`ws_backup._review_import`) reads the workspace through
`connect_pages_db`, so one nobody has opened since the server's upgrade is
upgraded first, and its pages are matched in the shape the backup's are
brought to ([migrations.md](migrations.md)).

Pages inside pages ([home_library.md](home_library.md) "Sub-pages") keep
their `parent_page`. The review (`ws_backup._review_import`) gives each
page entry its `parent`, the entry id of the page it is inside (the
workspace's page's effective parent for a page it has, else the backup's,
named by the workspace's page when it has that one), and `folders`, the
paths of its top's folders, so the library column draws a sub-page under
its parent. A new sub-page's `folders_alone` are those same folders: where
it lands when the selection leaves its parent out, since `restore_zip`
then writes its top's folders on it, and the review brings them (its
`_tree`). The merge (`_merge`, `_placed`) puts a page inside the page its
`parent_page` names when that page comes too or the workspace has it (a
page it has for the same PDF under another id stands for it; a selection
that leaves that page out has `restore_zip` write the workspace's id in
first, as the review named it), dropping
any `folders` of its own; otherwise it drops `parent_page`, and the page is
a library page filed by its folders, which the report shows with `parent`
`""`.

A page that reads its PDF where it is on this computer (`doc.at` "path",
[synced_vaults.md](synced_vaults.md#a-pdf-read-where-it-is)) travels as
bytes: the file is read through `storage.files` into `uploads/` like a
stored one, and the copy's pages have their document stored (`at`
"store", `_GammaBuilder.finish`), so where the export lands the page is an
ordinary one with no path to read from. The
same export (`page_gamma_zip`, with the page's properties patched first) is
how *Add to library…* takes an opened PDF to another workspace of the server
with everything on it.

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
view's chat. A member's own export is unchanged.

Every format keeps to the share the same way, in `_Builder` and so with no
rule of a builder's own (`tests/test_share_export.py` runs all of `_BUILDERS`,
and the `/export-pdf` route):

- A `[[ref]]` or `![[embed]]` of a block on a page the share does not reach
  resolves to nothing (`_ref_resolver`, by `ShareScope.pages`), at any depth
  of embeds. It stays as typed, or reads as its bare id in a PDF, a Zotero
  note or a Notability note, which is the chip the share view itself draws, so
  the block's text, its page's title and the pictures in it stay out. A
  vault's `^anchors` come from the share's own pages.
- An upload the share cannot read stays out of any zip, whatever named it
  (`_can_bundle`, the rule of `GET /api/uploads/…?share=` read once per
  export, `uploads._pages_upload_names`); the Notion zip warns of it.

Logseq and Gamma write a ref as stored, and BibTeX reads page properties
alone, so they read no block outside the pages exported to begin with.
`POST /api/jobs/export?share=`, the share view's export, is held to the
share the same way (`opts["share"]`), and the job is the share's own
([tasks.md](tasks.md#a-share-views-exports)); without a `?share=` it works
in the signed-in account's own workspace.

Folder and label names are not held back this way. The formats but Gamma's
still write the folder's whole path (the download's name, a `.bib`'s header)
and a page's folders and labels where they write them (a Markdown page's front
matter, the vault's directories and tags, Zotero's collections and tags, the
Notion zip's `Tags:`), a page share's too.

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
  which serves no library). A share view has no workspace of its own and
  lists no background tasks, so it does not import: `addShareToLibrary` puts the link
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
Markdown, Notability and Notion, the ZIP row Obsidian, Logseq, Zotero and Gamma. Double-click or
Next confirms. Formats with editable options get a review step: the
Highlights, Notes and Bundle-the-files switches beside an illustrative page
(`shared/illustrations/TransferPreview.jsx`, an example of the options, not
a render of the document). Annotated PDF has two more: "Place notes", a
choice between Beside the page and On the page (`NOTES_PLACES`; it waits on
the Notes switch), and Comments, the open threads on each highlight and on
its notes as cards beside the page, with or without the notes (a note's
threads under it when the notes are beside the page too, the highlight's
own under its own note). The preview draws them where they go: the
note and a comment card in a column beside the paper, or the note on it.
While the open page shows its translation, the Annotated PDF's summary says
that the translation replaces the original text, in which language and on
how many pages; there is no switch for it (hiding the translation exports
the original). A folder's says how many of its papers go translated, once
they are read as the dialog opens. Gamma and Notability have fixed contents, and a
PDF without a stored copy can only be the original file, so these export
straight from step one, Gamma and Notability unless the page has pages
inside it (below). Logseq
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

A page with pages inside it gets one switch more for every format,
"Include pages inside", off by default as Notion's "Include subpages" is
and remembered with the others (`subPages`); with it, a format without
switches gets the review step too (`offersSubPages`). A folder, a page
with none inside and a PDF that can only be the original file get no
switch. Its hint says how many pages it adds: App passes `subPages`, the
page's descendants in the listing (`pageDescendants`) or the open page's
family's pages right inside when those are more. A page share's view lists
no pages (the page is not in `pageBlocks`, `subPagesDirect`), so there the
hint and the summary name no number. With the switch on, the summary adds
"The {n} pages inside it are exported with it." (BibTeX says instead that
the pages without paper metadata are left out), a PDF with every other
switch off is exported by the server rather than downloaded as the viewer's
file, and the
BibTeX review and its link take the pages inside too
([below](#the-review-step)). The job step's breadcrumb counts a review
step for a job whose params carry `sub_pages`, or whose page is still the
open one and offers the switch.

Export starts the export as a background job ([tasks.md](tasks.md)).
`exportJobBody` turns the payload into the job's body: the page or folder,
the server's mode for the format (each format's `mode`: `annotated-pdf`,
`notes-pdf`, `readable`, `notability`, `notion`, `obsidian`, `logseq-graph`,
`zotero-rdf`, `bibtex`, `gamma`)
and the three flags, for a page `sub_pages`, and for an annotated PDF `comments` and `notes_at`. The dialog then shows its last step, the job's
(`ExportJobStep`). While the job runs it shows its progress (pages, then
the packing of the zip) with Stop, and says the window may close. The job
goes on in Background tasks, whose row opens this step again. Once ready,
the file downloads by itself when the window is open; a closed window's
file is offered in the pill with a Download button, and waits in the tray.
The finished step names the file and its size, says "Saved to your
downloads" once the automatic download has happened (`job.downloaded`;
the button then reads "Download again"), counts the pages, lists the
pages left out with the reason, what the file could not keep of a page
(the result's `warnings`, a Notability note's or a Notion zip's page's), and
says what to do next: Zotero's steps, Obsidian's unzip-into-a-vault, Gamma's
Import → Gamma export, BibTeX's beside-your-.tex, Notability's open-it-there,
Notion's Settings → Import → ZIP. A failed or
stopped export offers Start again, with the job's params (`sub_pages`,
`comments` and `notes_at` included); one that wrote the viewer's
translation offers Export again… instead, back to the setup, while its
page is open, and otherwise Start again with the translation the server
kept with the job
([translation.md](translation.md#exporting-the-translation)). A share view's
Export starts the same job under its share token and follows it the same
way, with no tray: a window closed meanwhile has its file offered in the
pill once it is ready, and the step says so
([tasks.md](tasks.md#a-share-views-exports)).

The switches mean, per endpoint:
`/pages/{id}/export?mode=readable&highlights=&notes=&pdf=` (Markdown,
`render_readable` in `markdown_export.py`; dropping highlights keeps a
highlight block's own text as a plain bullet; the front matter carries the
page's folder path relative to the exported folder so the zip re-imports
into the same tree; the files sit at the zip's top, `<Title>-<id12>.md`,
but a sub-page's sits in a directory named for its parent, beside the
parent's file (`Top-x.md`, `Top-x/Child-y.md`, Notion's layout), its
`folder:` its top's path and its front matter naming the parent's file as
`parent:` (relative to its own, percent-encoded as its links are:
`parent: ../Top-x.md`) when the parent is in the export; links between
exported files and `assets/` paths are relative to each file
(`../Other-z.md`, `../../assets/<sha>.png`); image sizes export in the
Obsidian dialect — `obsidian_image_sizes` rewrites a Logseq `{:width N}`
outside code to `![alt|N](url)`. Block links resolve against the export set
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
always included and only file bundling gets a switch; a PDF with every switch off, the pages inside it left out, is the stored file itself, which the frontend
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
entry (with `sub_pages`, one bibliography for the page and every page
inside it), or one bibliography for every paper in a folder. This is what a LaTeX
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

`GET /api/bibliography?page_id=|folder=` (a folder id; `sub_pages=1` with
`page_id`, the pages inside it too) answers what the export *would*
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
When "Include pages inside" ([above](#the-export-dialog)), under the
columns, is on, the review asks for the page with the pages inside it
(`bibliographyPreviewUrl`'s `subPages`) and asks again when the switch
changes, so it holds what the `.bib` will.

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
up to date" when the page or folder is already shared (a page's link with
`&sub_pages=1` while "Include pages inside" is on), and otherwise points
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
  path relative to the exported folder (a page's export keeps the page's
  whole path, the pages inside it below it with `sub_pages`), one
  directory per folder name. A sub-page's file sits in a
  directory named for its parent, beside the parent's
  (`Sub/Top.md`, `Sub/Top/Child.md`, `Sub/Top/Child/Grandchild.md`), when
  the parent is in the export, else where its top's folders put it. The
  library pages are named before the sub-pages (`name_files`), so a
  sub-page never takes a library page's name. `vault_name` strips what Obsidian refuses in a name
  (`* " \ / < > : | ? # ^ [ ]`, leading dots); same-named pages in one
  directory get ` 2`, ` 3` suffixes. No `# Title` H1 — the filename is the
  title, and a `title:` property is written only when the name had to be
  sanitised. `.obsidian/app.json` (attachment folder = `attachments/`) marks
  the zip as a vault, which is also how the importer recognises it.
- **Front matter**: `parent` for a sub-page whose parent is in the export
  (`"[[Top]]"`, the parent's link text, `[[dir/Title]]` when names clash;
  after `title:`), `tags` (the page's labels by name), `aliases`, `source`
  (the bundled PDF as a quoted wikilink `"[[Paper.pdf]]"`, else the URL),
  `doi`, `authors` (list), `year`; then the BibTeX fence as in the readable
  export. A page with none of these writes no front matter, and if its text
  would then open with a `---` line, that divider is written `***`, as in the
  Notion export: a second `---` below it would close it as front matter, and
  the text between would go.
- **Outline → document** (the Logseq importer's flattening): a top-level
  heading block is a heading and its children follow as document content;
  a top-level leaf block is a paragraph; a top-level block with children is
  a list item with its subtree as a nested list (2-space indents,
  multi-line content indented under the bullet). A list block is an item
  as it stands, not one inside another: `- x`, `1. x` or `- [ ] x` with one
  marker, its children at the marker's text column; only such blocks differ
  from any other block here. Its next lines keep their layout, but text at
  its left edge moves under the marker, and a fence or `$$` block there moves
  whole, list-shaped and indented lines included. Re-importing has two lossy steps: everything
  after a heading nests under it, and a plain block written as a list item
  comes back a bullet, its text whole, because a vault's lists stay lists. The
  Gamma format stays the lossless route.
- **Links**: `[[id]]` / `![[id]]` whose target is in the export become
  `[[Title]]` for a page (`[[dir/Title]]` when two exported pages share a
  name — Obsidian's rule), `[[Title#^id]]` / `![[Title#^id]]` for a block
  (`[[#^id]]` on the same page), and the target block gets ` ^id` written:
  on its bullet line in a list, at the end of a paragraph, on its own line
  after a fence / table / quote / callout, a block that ends in a fence (text
  after a closing line would leave it open) or, under the bullet, a block that
  is only a marker (it would be read as the item's text). `anchor_marker` makes the id
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

The gamma-sync client ([synced_vaults.md](synced_vaults.md#the-client)) writes
the same dialect with the PDF beside the note, and a page's `gamma_id` in its
front matter. It shares `unique_name`, `name_files`,
`link_texts` and `VaultContext.place_pages`/`place_pdf` with this export and the
annotated-PDF folder export. `vault_name` appends `_` to a Windows device
name (`CON` → `CON_`).

The vault importer above reads all of this back (titles from filenames,
`^id` anchors and wikilinks into mentions and synced blocks, `tags` into
labels, the quoted `source` into the paper, `parent` into the page it is
inside) — `test_obsidian.py` has the round trip, and
`test_sub_pages_export.py` the nested layouts of every format, with and
without `sub_pages`, and their round trips.

A synced vault writes a highlight in a dialect of its own, on purpose
(`gamma/vault_links.py`, [synced_vaults.md](synced_vaults.md#links-and-highlights)):
the whole highlight is one callout, the person's note inside it, so that the
lens reads it back as one block, and its anchor stands on its own line after
it, Obsidian's rule for a quote:

```
> [!quote] [[Paper.pdf#page=3|p. 3]]
> the quoted text
>
> the person's note, every line

^a1b2c3
```

The export above puts the note after the callout as a paragraph. A
vault reads a callout as a highlight by its first line alone (`> [!quote`,
any case, folded or not), and a callout that links the page's own PDF and is
no highlight Gamma knows becomes one with its quote and page and no
rectangles, which the relocation run then places
([highlight_relocation.md](highlight_relocation.md)).

## Notion export

`?mode=notion` on both export endpoints (`_NotionBuilder` →
`gamma/notion_export.py`) writes a zip for Notion's file importer (Settings →
Import → ZIP). Always a zip, even for one page. Nobody has run one through
Notion yet: the layout copies Notion's own exports and a construct in doubt goes
out in its safest form ([what is unknown](../research/notion-roundtrip.md#what-notions-importer-takes)),
so the Export dialog says what the zip holds and leaves the rest to the
importer. A second import makes the pages a second time, since the importer
gives back no ids; the finished step says so. The Notion card sits in the Notes
row with the Highlights and Notes switches and no Bundle switch.

A page goes `export_walk.walk` (what each block is to an export, by the readable
export's rules) → `notion_gamma.from_gamma` (the block model of `notion_blocks`,
where one block's content can be several blocks) → `notion_blocks.lower_for_file`
→ `notion_nfm.write(form="file")`. The readable and vault exports keep their own
walkers, and `test_export_walk.py` holds the three to the same blocks. Each
construct in doubt is one row of `notion_blocks.CAPS`, so what a probe finds
changes a row and no block code:

| Construct | In the zip | `CAPS` row |
|---|---|---|
| Callout | A quote opening with its icon: `> 💡 text`. Type and colour go | `callout` |
| Toggle, paragraph with children | `- x` with its children nested; a paragraph in a list is a bullet | `toggle`, `paragraph_children` |
| Folded heading | The heading, its children after it | `toggle_heading` |
| H4 and below | `###`, as the help page says the importer reads them | `heading_level` |
| `==x==`, a background | Bold; a text colour goes | `color` |
| Underline | `<u>x</u>`, which Notion's own Markdown export writes | `underline` |
| Date | `@2026-10-09` as text | `date` |
| Sub-page, a page alone on a line | A link line | `page` |
| Table with no header | An empty header row | `headerless_table` |
| Line break inside a block | Two spaces and a newline | `hard_break` |
| Nested list | Indented by 4, Notion's own | `list_indent` |
| Markdown characters in text | Escaped; a probe of `\*x\* \$5 \<tag>` decides | `escape` |

- **Files**: `Paper.md`, its images in `Paper/`, a sub-page in `Paper/Child.md`,
  filed as the annotated PDFs are (`_Builder.directory`): a page's export mirrors
  no folder, a folder's has its subfolders as directories
  (`Reading/Other.md`). Names are `vault_name`, unique in their directory
  (`name_files`), and hold no id, even a page imported from Notion: a kept
  ` <id>` in a title is the worst failure on offer.
- **Title**: the file name is the title. A page opens with an H1 of its title
  only when the name cannot hold it (it had to change, or was numbered) or when an
  H1 of the notes would open the file: whichever of the two Notion's importer
  takes for the title, and a read back takes the H1, no title is lost.
- **Metadata**: no front matter, which Notion would show as text. The `bibtex`
  fence comes first, as the zip import reads one only at a body's start, then
  `DOI:`, `Authors:`, `Year:` and `Tags:` lines and the PDF's name. A divider
  opening the file is `***`, which opens no front matter.
- **Pages inside pages**: a `[[child]]` alone on a line stays there as a link,
  and any other page inside gets a link line at the page's end. A database is
  its table page: the table as pipes, its rows as sub-pages, their property lines
  as text, and no CSV, which could not make relations and would duplicate.
- **Links**: a mention of a page in the export is a relative `.md` link,
  percent-encoded as Notion's own are; a mention of a block links its page;
  one outside the export is its label. A block's label is its first line less
  a list marker, or its page's title when that line has no words, as a code
  block's or an image's has not.
- **Images and other files**: an uploaded image goes in the page's directory
  (`Paper/<sha>.png`), its `|300` size gone, and an image from the web stays at
  its address. Handwriting is rendered as an SVG beside it, untried in Notion.
  A PDF, a recording or any other file stays out, as Notion's
  importer drops them, whatever `pdf` says: its name as text, a link if it has a
  web address, and a warning. A recording has no name to give: it is its length
  in bold, the word `audio` (`part 1`, `part 2` for several segments), then its
  title.
- **The switches** mean what they do in the Markdown export, and between them
  decide what the zip holds beside the Markdown (`notionFiles` says it on the
  card's summary and the preview's files row). With Highlights on, a highlight
  is a quote with its `p. N`, then your note, and handwriting comes along;
  off, the highlight's own text is a paragraph, and link regions, handwriting and
  recordings go. Notes off leaves out the text of every block, and with it the
  images and pictures, and keeps the quotes, handwriting and a recording's
  length and `audio` line. Both off, a page is its title and paper record.
- **Warnings** (the job's `warnings`, under each page's title): a file left out,
  an image gone from storage, a file over the 5 MB of Notion's Free plan, a zip
  over the 10,000 files the help page calls likely to fail, a text too long for
  its marks and a page too deep to write.
- **Limits of a page**: a text past 20,000 characters (`_LONGEST_MARKED`) keeps
  its bold and other marks as typed, since `_MARK_RE` costs the square of the
  length on delimiters left open; its code, formulas, images and links still
  read, but for a link whose label holds a bracket. A link's title and a
  `\( \)` are bounded in `_ATOM_RE` for the same reason, at 2,000 and 1,000
  characters: past that each is typed text. A page nested too deep for the
  pipeline, which recurses a level at a time, is left out (about 250 levels);
  a link to it from another page is left pointing at no file, and an export
  with no page left fails with the reason. Warnings reach a job's result only:
  the download routes (scripts', an import by link) answer the file alone.

Read straight back into Gamma the zip is **Plain**, having no ids: `Paper/` is a
folder and not sub-pages, a plain block with children is a bullet, and what
follows a heading nests under it. Titles and text come back, and
`test_notion_export_return.py` pins all of this. A tree comes back only from
Notion's own re-export, once Notion has given the pages ids.

`notion_nfm.write(form="nfm")` and `read` speak Notion-flavored Markdown, the API's
form, and `notion_nfm.Links` is the seam: `page` and `asset` answer a relative path
here and would answer a Notion URL there. They are tested and nothing sends them:
token, rate limiter, uploads and id map are not built
([the API route](../research/notion-roundtrip.md#the-api-route)).

## Notes as a PDF document

`?mode=notes-pdf` on both export endpoints (`_NotesPdfBuilder` →
`gamma/pdf_document.py`) typesets the *notes themselves* as a new PDF — the
inverse of the annotated export below, and the only PDF a page without a paper
can produce, so the Export dialog offers it everywhere (a folder export, or
a page's with the pages inside it, puts every page in one document, each
starting on a fresh sheet).

Each block's markdown is parsed twice: into chunks (headings, paragraphs,
`>` quotes and `> [!type]` callouts, list items, `- [ ]` todos, fenced code,
`---` rules, GFM tables, images — honoring the editor's size, Obsidian
`![alt|300]` or Logseq's `{:width N}` after the image
(`note_markup.alt_and_width`), capped at the column —
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
note). A plain note is indented text, as on the page, and a list block keeps
its own marker. A text box and a picture placed on a page have rules of their own
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
  `collect_highlights` takes highlights only (`is_highlight` and not
  `is_link`), so a box never reaches the hls page, the EDN file or the
  area crops.
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
  placed, cropped, flipped, turned and at its opacity. Its turned frame is
  fitted to the column (never enlarged) and to a page's text height. Then
  come a small grey "picture, p. N" line on a PDF page, as handwriting has,
  and the caption as a bullet. A picture whose file is missing or cannot
  be embedded is a grey "picture" line, with its page when it has one.
- **Annotated PDF**: a `/Stamp`, with either switch on, its opacity in its
  appearance and its `/CA`. An SVG is drawn as vectors and comes back as
  the SVG ([pictures.md](pictures.md#svg-pictures)). Among a highlight's
  notes in its popup, a picture on the PDF page is left out with the notes
  under it, since it is its own `/Stamp`. One under a sheet stays a line,
  its `![caption](url)`. The sheet export draws a sheet's pictures as page
  content, under its boxes.
- **Notability**: an `ImageMediaObject` with its crop, turn, flips, lock
  and caption; its opacity is said and left out, and an SVG is a PNG of it
  ([Notability export](#notability-export)).
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
  Notability itself shows a turned, cropped, flipped, captioned or locked
  picture Gamma wrote is untested. Its own drawing puts the flips before
  the turn, as Gamma does; where its crop falls against the flips no
  sample shows.

## The PDF writers

The typesetting engine both PDF writers draw with (`gamma/pdf_typeset.py`)
and the annotated-PDF export (notes and comments as cards beside the page
or on it, vector text as Type 3 fonts,
images, fonts and content streams) are described in
[pdf_typesetting.md](pdf_typesetting.md).
