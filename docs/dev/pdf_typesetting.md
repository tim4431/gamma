# PDF typesetting and the annotated-PDF export

How Gamma writes PDFs: the typesetting engine both writers share, and the
annotated-PDF writer that puts highlights, notes, ink and text boxes into a
copy of the source file. Which exports exist and how they are started is in
[import_export.md](import_export.md); the notes typeset as their own
document are [there too](import_export.md#notes-as-a-pdf-document).

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
