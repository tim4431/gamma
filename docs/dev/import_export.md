# Import and export

The ⋮ menu's Import…/Export… dialogs and every pipeline behind them: embedded
PDF annotations, Logseq graphs, Zotero libraries, Markdown export, the notes
typeset as their own PDF, and the annotated-PDF writer. Code: `gamma/routers/imports.py`, `gamma/zotero_import.py`,
`gamma/zotero_export.py`, `gamma/logseq_import.py`, `gamma/markdown_export.py`, `gamma/pdf_export.py`,
`gamma/pdf_notes.py`, `gamma/pdf_document.py`, `gamma/pdf_typeset.py`,
`gamma/note_markup.py`, `gamma/vector_text.py`,
`gamma/pdf_image.py`; frontend dialogs in
[widgets.jsx](../../frontend/src/widgets.jsx).

## Importing annotations embedded in a PDF

`/api/import/pdf-annotations` converts annotations embedded in the PDF file
(SumatraPDF/Acrobat/Gamma-export highlights, notes, and /Square//Circle → area
highlights) into highlight blocks — idempotent via `properties.imported_annot`
keys; opacity honors the annotation's `/CA` so a Gamma export → re-import
round-trips exact colors; PyPDF2 dict access returns `IndirectObject`s, always
`.get_object()` them.

Because imported annotations would otherwise render twice (pdf.js paints them
into the canvas AND the blocks draw as overlays), the Settings → Reading → PDF
viewer → "Annotations inside the file" preference either hides them viewer-side
(`annotationMode: DISABLE`, default) or sends `strip: true` so the import
rewrites the stored PDF without them (the ⋮ menu's "Import…" dialog can
override that for one run; the auto-import on open always follows the
preference); stripped blocks get `properties.annot_stripped`, which tells
`/export-pdf` to write them again (it skips `imported_annot` blocks only while
the original is still embedded).

## The Import dialog

The ⋮ menu's single "Import…" entry → `ImportDialog` in `widgets.jsx`: the
export dialog's counterpart — pick a source (annotations embedded in this PDF,
a Logseq .pdf + .edn, or a Zotero library .zip), flip the strip switch (applies
to embedded annotations, including the ones inside Zotero's exported PDFs),
confirm. Zotero is the default source (a numbered step guide reusing
settingsKit's `Step`); with a PDF open, that PDF's own annotations win. Nothing
is remembered: the switch starts from the Settings preference each time, so the
setting stays the standing policy.

## Plain Markdown uploads

The add menu's file picker, directory picker, and whole-window file drop all
accept `.md` / `.markdown` alongside PDFs. `POST /api/import/markdown` decodes
UTF-8 (5 MB cap), reduces any browser-supplied relative upload path to its
filename leaf, uses a YAML-frontmatter `title` or that filename's stem as the
note-page title, and converts the document into nested Gamma blocks through
`gamma/markdown_import.py`. Headings and indented lists retain hierarchy;
paragraphs, fenced code, math and other Markdown stay as raw block content for
the normal editor renderer. In mixed folder uploads, Markdown note pages and
PDF pages receive the same subfolder labels; unsupported files are skipped.

## Zotero library import

`POST /api/import/zotero` (⋮ → Import… → Zotero library): a zip of Zotero's
File → Export Library → "Zotero RDF" (with Export Files/Notes).
`gamma/zotero_import.py` parses the RDF (items, journal records carrying the
DOI, collections, tags, HTML notes) and tolerant zip-name lookup
(cp437-mojibake, NFC/NFD, backslashes); the endpoint in `routers/imports.py`
uploads PDFs (dedup + quota per file, over-quota items are skipped not fatal),
upserts pages keyed by file hash then `properties.zotero_key` (re-exports
change bytes — Zotero re-embeds annotations at export time), maps
collections→folder labels (optional `folder` prefix form field),
tags→`category`, notes→child blocks (`properties.zotero_note`), then runs the
shared `import_embedded_annotations` (reader annotations arrive inside the
exported PDFs; `strip` follows the client's embedded-annotations preference).
Merging only fills gaps: existing meta/bibtex/files are kept, labels union.

## Zotero RDF export

The import's exact inverse (`gamma/zotero_export.py`, endpoint branches in
`routers/export.py`): `?mode=zotero-rdf` on `/pages/{id}/export` and
`/folders/export` builds a `<slug>/<slug>.rdf` + `<slug>/files/<n>/<name>.pdf`
zip that Zotero's File → Import reads (unzipped) and Gamma's own
`/api/import/zotero` accepts as-is. Element shapes mirror what Zotero itself
writes and `parse_zotero_rdf` reads: venue/volume/DOI on a standalone
`bib:Journal` referenced by `dcterms:isPartOf`, notes as `bib:Memo` HTML
(top-level non-highlight subtrees, one note each — the inverse of the import's
notes→child-blocks mapping), folder labels as the `z:Collection` tree (a folder
export confines them to paths under the exported folder), tags as `dc:subject`,
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
its own Memo with a page+quote header (`highlight_memo_html`). `/api/folders/export` (all modes) reports per-page
progress through `/api/folders/export-progress`, which the frontend polls into
the status pill during folder exports. Highlights are not in the RDF
— like Zotero's "Include Annotations" they're burned into the exported PDF
copies with `pdf_export.annotate_pdf` (`highlights=0` skips that, `pdf=0`
omits the files entirely, `notes=0` the Memos).

## The export framework

`/pages/{id}/export` and `/folders/export` share one driver (`_run_export` in
`routers/export.py`): it walks the selected pages exactly once (subtree fetch
→ `build_tree` → progress bookkeeping) and feeds each page to a per-format
`_Builder` (`_MarkdownBuilder`, `_NotesPdfBuilder`, `_LogseqBuilder`,
`_ZoteroBuilder`, `_GammaBuilder` — keyed by `?mode=`), which accumulates zip
parts and names the download. `begin(conn, root_ids)` shows a builder the
whole export set before the walk — the DB connection is closed by the time
`response()` runs. Adding an export format = adding a builder; the
endpoints, progress plumbing and `_zip_response` stay untouched. A builder
whose download isn't a zip overrides `response()` instead (`_NotesPdfBuilder`
returns one PDF).

## Gamma-to-Gamma export

`?mode=gamma` (`_GammaBuilder`): a *scoped account backup* in the same
`gamma-backup-1` layout as `/api/export` — a `pages.db` holding just the
selected page subtrees verbatim (same block ids), a `data.db` with their AI
chats (plus the folder view's own `home:<path>` chat buckets on a folder
export), `uploads/` with just the referenced files (doc_id PDFs + anything
matching `UPLOAD_RE` in content/properties — the orphan-cleanup reference
rule), and a `manifest.json`. **There is no new import code**: any Gamma
imports it through the existing `/api/import-data?mode=merge` — additive,
deduped by block id / doc id / content hash, so re-importing adds nothing. The
⋮ Import dialog's "Gamma export (.zip)" source feeds the zip to that endpoint
via the same upload/progress path as Settings → Restore backup (guests can't
import). The dialog's three switches don't apply — a Gamma export is a 1:1
copy, so they're pinned on.

## The Export dialog

The ⋮ menu's single "Export…" entry → `ExportDialog` in `widgets.jsx`: one
Notion-style dialog — format (PDF / Notes as PDF / Markdown / Logseq graph /
Zotero RDF / Gamma) plus Highlights, Notes and Bundle-the-files switches (per-format hint
text lives in the `EXPORT_SWITCH_TEXT` table), remembered in `localStorage`
(`gamma-export-opts`). The switches are query flags on two endpoints:
`/pages/{id}/export?mode=readable&highlights=&notes=&pdf=` (Markdown,
`render_readable` in `markdown_export.py`; dropping highlights keeps a
highlight block's own text as a plain bullet; image sizes export in the
Obsidian dialect — `obsidian_image_sizes` rewrites any legacy `{:width N}`
to `![alt|N](url)`. Block links resolve against the export set
(`resolve_block_links` + `_MarkdownBuilder.begin`'s page-id → filename map):
a `[[ref]]` or PDF link region whose target page is in the same export
becomes a relative link to that page's .md — so a folder zip is
self-contained — and reads as plain text otherwise; a `![[embed]]`
materializes the synced block's content with a *(from …)* attribution,
nested embeds degrading to mentions; ids the resolver doesn't know stay as
typed) and
`/pages/{id}/export-pdf?highlights=&notes=`. "PDF" is the paper itself and is
hidden when there is none (a note page, an unsaved proxy PDF, a folder) —
"Notes as PDF" (`?mode=notes-pdf`) takes over as the fallback format, and its
Bundle switch is hidden because a document always embeds its images. Two
combinations are special: a
Logseq graph is defined by carrying both layers, so its switches are pinned on
and disabled; a PDF with both off is the stored file itself, which the frontend
downloads from the viewer's own URL (so it also works for a PDF that only
exists behind the proxy).

The dialog can also target a whole folder: opened from home with a folder open
(the ⋮ Export… entry) or from a folder card's context menu (`exportFolder`
state in App.jsx), it drops the single-PDF format and sends the same
format/switch flags to `/folders/export?name=` (readable, `logseq-graph` or
`zotero-rdf`).

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
`==mark==`, `[[refs]]`, links, `$…$` math). Highlights become quoted passages
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

`/api/pages/{id}/export-pdf`: highlights become standard `/Highlight` (or
`/Square` for area notes) annotations with the note text in the popup
(`gamma/pdf_export.py`) — `?highlights=0` skips that layer entirely. Every
`/Square` carries an `/NM` id (`Zotero-<key>`, deterministic from the block
id): Zotero's pdf-worker maps `/Square`→image annotation but silently DROPS
one without an id, while `/Highlight` imports id-less — without `/NM`, area
notes vanish in Zotero.

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

### Vector text (math and CJK)

`gamma/vector_text.py` draws what the base-14 fonts can't, as vector paths:
`math()` typesets LaTeX with ziamath, `glyphs()` renders CJK per character with
ziafont (a *plain .ttf* — ziafont can't open the .ttc collections most CJK font
packages ship, hence `fonts-droid-fallback` in the Dockerfile; without it CJK
falls back to the non-embedded CID font, which pdf.js renders as latin
gibberish). Both libraries emit plain glyph *outlines* (M/L/Q/Z paths + rects)
once `config.svg2 = False` — otherwise glyphs come wrapped in a `<symbol>`
whose own viewBox rescales them ~1.5×, so `_svg_ops` refuses any
`<symbol>`/`<use>`/`transform` rather than drawing it at the wrong size. It
also honours each shape's `fill`/`stroke`/`fill-rule`: `\boxed{}` is a
*stroked, unfilled* rect, and painting it solid turns the whole equation into a
black slab. SVG's y-down axis matches the display frame, so ops drop in with a
translate/scale; inline math and CJK sit on the text baseline (the viewBox
origin *is* the baseline), `$$…$$` gets a centred row, and a box that had to
shrink an equation or picture loses to a wider candidate. When ziamath is
missing or chokes, `note_markup.latex_spans` falls back to a unicode
approximation (`\frac{a}{b}` → `a/b`, unknown commands keep their name so
`\sin` works) — tests cover both fallbacks.

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
which a test enforces), and a non-embedded STSong-Light CID font for CJK.
Deliberately no reportlab/Pillow dependency. PyPDF2 leaves merged content
inline in the page dict; it must be re-added as an indirect object or the file
is unreadable.
