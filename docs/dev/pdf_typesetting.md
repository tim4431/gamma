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
meet. `pdf_notes` places its cards in the same frame. A popup's text
(`highlight_note_text`) is the highlight's own note and the notes under it,
as an indented bullet list (`popup_text` of `highlight_note_blocks`).
A text box on a PDF page among them is left out with the notes under it,
since the box is written as its own `/FreeText`. A box under a sheet stays
a line: the nearest sheet wins. The highlight's comment threads are no
part of it: they live beside the blocks ([comments.md](comments.md)).

Handwriting blocks (`ink_url`) become `/Ink` annotations: one per look
bucket (colour × tool × size × opacity) of the group, `/InkList` polylines
mapped through the same rect → user-space conversion, `/BS /W` the mean
drawn width, `/BS /S /D /D [on off]` when the bucket's first stroke is
dashed or dotted (the pattern its size's, [handwriting.md](handwriting.md)
"Dash geometry"), the caption on the first, an `/NM`, and a private `/GammaInk`
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
in the content, as the screen draws them, the pencil's grain and the
calligraphy nib included (a dashed or dotted pressure stroke as one
dashed line at its mean width and shade). The page is the drawing, so
there is no `/FreeText` or `/Ink` layer. The switches do not apply.

### Notes and comments as cards

`?notes=1` prints each highlight's notes as a card, and `?comments=1` each
of its open comment threads as a card of its own (`gamma/pdf_notes.py`).
Notes go where `?notes_at=` says: `beside` (the default) in a column beside
the page, `page` in the page's own free space. Threads always go beside the
page, where the viewer puts a highlight's threads once a lower zoom leaves
room for them ([comments.md](comments.md) "The PDF margin"). Comments work
with the Notes switch off. The full text of the notes stays in the popup
either way.

**A card** (`gamma/pdf_cards.py`) is laid out once into rows, each measured
from the content's top-left, at 8 pt. A note card is the highlight's own
note and the notes under it (`pdf_export.highlight_note_blocks`:
`(depth, markdown)` in reading order, the same walk the popup text is made
from), each deeper one a bullet indented by its depth. A thread card is its
comments oldest first, each as the viewer's margin draws it: the author's
avatar (their initial on their colour, `person_color`, the app's
`personColor` with the same eight colours, held to it by a test), their
name, the day, and the comment under them past the avatar. The replies
step in to the first comment's text (the viewer's margin keeps them in
line, inside a card per thread), so on paper a thread reads as one comment
and its answers, and two threads under one note stay apart. A deleted root
reads "Comment deleted". A thread on a note is drawn under that note in
the note card, a little past its text, when the note card is beside the
page too: a reply then stays next to what it answers. That includes the
highlight's own threads, under its own note, since a highlight's own text
is its note ([comments.md](comments.md) "Vocabulary"). When the note is on
the page, or not printed (the Notes switch off, an empty note, a text
box), the thread is a card of its own in the column. A thread on one of
the notes under the highlight then opens with that note's first line,
quoted (italic, behind a bar, cut with an ellipsis); the highlight's own
need no quote, as the viewer's margin shows them. The Markdown goes through `pdf_document.chunks`,
the parser the notes PDF and text boxes use, and is set in the compact look
of a rendered note: headings a little larger, lists with their bullets or
numbers, to-dos with a drawn box, quotes with a bar, fenced code on a tint,
tables a line per row, pictures (capped at 150 pt tall), display math
centred; bold, italic, code, marks, links and inline math within a line.
`[[refs]]` and `![[embeds]]` read as the block they name (the router's
`block_ref_resolver`). Links become `/Link` annotations over their text. A
card is white with a hairline border and rounded corners; a note card
carries its highlight's colour down its left edge, a thread card none, as
in the viewer. `clip` shortens a card to a height, ending it in an
ellipsis (a picture or equation the cut falls in shrinks to the room left,
down to 16 pt).

**Every card of a highlight** is collected by the router's `_collect_cards`
from every highlight block, the ones still embedded in the PDF included
(their annotations are not rewritten, but their notes and threads are the
reader's own): its notes, each with the threads on it
(`highlight_note_blocks` names each note's block; the highlight's own
threads ride with its own note); the threads on the highlight when it has
no note shown; and the threads on blocks under it no card shows, each with
that block's text to quote. The threads are
`comments.threads_for(…, state="open")`: resolved ones leave the margin and
stay out here too. In the column a highlight's cards come in this order:
the note card, then the threads that stand apart in reading order, the
highlight's own first.

**The column.** When anything goes beside the page, every page of the copy
is widened on the right of the page as shown (`_widen`). The media and crop
boxes become the view box grown on the user-space side the display frame's
x axis runs to: the right on an upright page, the top on a quarter turn,
the left upside down, the bottom on three quarters. So the page's content,
its annotations and the display origin stay where they were, and the trim,
bleed and art boxes, which lay inside the old box, are dropped. The column
is `COLUMN_GAP` (18 pt) from the page, `EDGE` (12 pt) from the new edge, and
as wide as 45% of the document's widest page as shown, between 170 and
300 pt (`_column_width`). Every page gets it, cards or not, so the pages
keep one width as they do in the viewer. The strip is painted the viewer's
backdrop grey with a hairline at the page's edge, which also covers
whatever the old crop box hid there. A highlight's cards stand together,
its note card first; in the gap a thin bar in a darker shade of its colour
spans its lines, and a line joins its first line to the same height in its
first card: level when the card is, slanted when stacking moved it.

**Stacking** (`_stack`) is the viewer's `stackCards`
(`comments/marginLayout.js`) without an active card: sorted by their
highlight's top, each card at that top or `CARD_GAP` under the card above,
none above the page's top edge. A page cannot run on into the next one's
column, as the viewer's does, so the run is then pushed up off the page's
foot, each card ending a gap over the one under it. When the cards do not
fit even so, `_column` shortens them: the tallest are cut to one height,
the largest that fits (`_water_level`). A card keeps at least `MIN_LINES`
(three) lines, as a card at rest in the viewer does. Past that the column
zooms out through `SCALES`: laid out wider by the scale's inverse and drawn
that much smaller, as the viewer's column is at a lower zoom. At the last
(0.45) a card keeps two lines, and cards that still do not fit are left out
and logged (past about 45 on a Letter page).

**On the page** (`notes_at=page`) a note card goes in the nearest patch of
the page's own empty space, with a dashed leader back to its highlight, and
the page keeps its size (unless threads widen it). Free space comes from
`gamma/pdf_free_space.py`: MuPDF's drawing log of the page (a bbox device
of the module's own, logging what `page.get_bboxlog()` logs: one box per
drawing operation, form XObjects flattened into the page's space,
invisible text skipped, except that a shading is bounded by the clip it is
painted through, where `get_bboxlog` logs the whole plane, so a
gradient-filled box counts as occupied) rasterized into an occupancy grid
(display space, 3 pt cells) with a summed-area table behind the candidate
search (`place`); a placed card is marked too, so cards never overlap. The
log takes in the annotations' appearances, so what `annotate_pdf` wrote
first counts as occupied: the text boxes with any switch, the highlights
and ink when the highlight layer is on. The log comes in the unrotated
page's space, its origin at the view box's corner and scaled by
`/UserUnit` like `page.rect`; `bbox_log` takes the unit out
(`pdf_text.user_unit`), and `page_space` maps it to user space against the
view box `page_frame` found, not through `page.transformation_matrix`,
which on a rotated page forgets a crop box's offset. The read holds
`pdf_text._lock` (PyMuPDF is not thread-safe and holds the GIL in every
call): one open of the document, one short call per page, the grids built
after the lock is let go. A file MuPDF cannot open, or one that needs a
password, reads as a page with its usual text block busy, so the cards go
in its margins. A card tries three widths, wider ones for a picture or
display math (`BOX_WIDTHS`, `WIDE_WIDTHS`), up to 45% of the page tall; a
one-line note gets a one-line-wide card, and a narrow card or a squeezed
equation costs extra in the search. On a page with no free space a card
takes the least-covered spot and covers some of the page.

**Drawing.** Everything is laid out in the page's display frame (x right,
y down, origin at the view box's top-left, /Rotate applied: the frame the
viewer stores rects in) and drawn as one Form XObject per page (`_form`),
whose `/Matrix` maps that frame to user space (`_frame`) and whose
resources are its own: the base-14 fonts its text used, the document's
Type 3 fonts and the pictures. `_stamp` adds it to a copy of the page's
resources as `/GmNotes` (numbered past a name the page already has) and
makes the page's contents a shared `q` stream, the streams the page had,
then `Q` and the form's `Do`. The page's own streams are never parsed or
rewritten. PyPDF2's `merge_page`, which an earlier writer used, parses
every content stream into operators and writes it back uncompressed, which
for a whole book is slow and swells the file. The pages without cards
share one form per geometry, holding only the strip, and a page that gets
neither a strip nor a card is left as it is. The `/UserUnit` is left out of
the layout, as Gamma's viewer, pdf.js and pdfium leave it out.

### Vector text (math and CJK) → Type 3 fonts

`gamma/vector_text.py` lays out what the base-14 fonts can't: `math()`
typesets LaTeX with ziamath, `glyphs()` shapes CJK per character with ziafont
(a *plain .ttf* — ziafont can't open the .ttc collections most CJK font
packages ship, hence `fonts-droid-fallback` in the Dockerfile; without it CJK
falls back to the non-embedded CID font, which MuPDF and pdfium read through
its ToUnicode map but Gamma's own viewer neither draws nor extracts; see Fonts
and content streams below). Both return a `Drawing`: the **glyph placements**
(which ziafont glyph, standing for which character, at which baseline point
and size) and, separately, path ops for the non-glyph shapes (fraction bars,
radical vincula, `\boxed{}` frames). ziamath is never asked for SVG — a flattened
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
allocated as indirect objects up front so pages (including the forms
`pdf_notes` draws page by page) can reference them, and `finalize()` fills them
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

`gamma/pdf_image.py` embeds `![](/api/uploads/…)` refs as image XObjects. JPEG
goes in verbatim, and so does an 8-bit gray, RGB or palette PNG without
transparency (PDF's `/Predictor 15` IS PNG row filtering), at any size and
without decoding. Everything else is decoded with Pillow: a PNG with an alpha
channel or a `tRNS` chunk (most pasted screenshots), 16-bit or sub-byte
samples, an interlaced PNG, GIF and WebP. The colour is written back as an
8-bit PNG and embedded the same verbatim way, and transparency becomes an
`/SMask` (an indirect object, which `XObjectStore` makes), so a text box's
tint shows through a transparent corner instead of a white rectangle. A 25 MP
screenshot takes about 0.7 s and 200 MB while decoded; `MAX_DECODED_PIXELS`
(40 MP) bounds that memory. A picture that can't be embedded (an SVG, a
corrupt file, one past the cap) is logged with the reason and drawn as its
alt text, or "image"; the export job's result lists only the pages it left
out, not pictures. A palette's `/Indexed` lookup must be a `ByteStringObject`: as a
text string PyPDF2 re-encodes it to UTF-16 and the picture comes out one flat
colour.

### Fonts and content streams

Text is a hand-built content stream (a page's own in the notes PDF and a
sheet's, a Form XObject's beside a page or in an annotation's appearance)
using three fonts every viewer has: Helvetica (WinAnsi), Symbol (Greek/math —
`pdf_typeset.SYMBOL` holds codes AND advance widths measured from the font
itself; every `note_markup.SYMBOLS` value must be drawable by one of the three,
which a test enforces), and a non-embedded STSong-Light CID font for CJK,
the last resort when no outline font is installed — plus the per-document
Type 3 fonts above for typeset math and CJK outlines. The CID font is one
object per writer, shared by every page that names it, and carries a font
descriptor and a ToUnicode map as indirect objects of their own (ISO 32000-1
asks for the descriptor that way; PDF 2.0 no longer does). The map lists the
characters drawn with the font — `draw_spans` records them, `finalize_cid`
writes the map — one `bfchar` each, never a `bfrange` over the whole plane:
the spec forbids a range that carries its destination's last byte past 255,
and PyPDF2 expands every code of a range into a table entry, 63,000 for each
page that names the font. With the two, MuPDF and pdfium read its text even
where no viewer font draws it. pdf.js does not unless it is given its CMap
files: the text is encoded through `UniGB-UCS2-H`, which pdf.js must load
(`cMapUrl`) before the font loads at all, and without it the font fails and
the characters are neither drawn nor extracted. Gamma's own viewer passes no
`cMapUrl`, so there the fallback's CJK is simply absent, from the text layer
and search too — the cure is an outline font on the server, not a viewer
change. PyPDF2 3.0.1 does not read it either: it has no `UniGB-UCS2-H`
encoding.
Deliberately no reportlab dependency; Pillow only decodes pictures (above).
A content stream must be an indirect object or the file is unreadable. The
document writer's page streams, the notes' forms and every glyph program
are Flate-compressed (`flate_encode`).
