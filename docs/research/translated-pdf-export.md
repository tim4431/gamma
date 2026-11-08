# Putting the translated view into an exported PDF

Issue #156 asked for translations kept as a PDF of their own, attached to
the paper, so the translated text could be highlighted and written on, and
read again without spending tokens. Kept translations already answered the
last part ([dev/translation.md](../dev/translation.md#kept-translations),
since v0.3.10). For the rest, a second page carrying a translated twin of
the file was weighed and set aside: a soft link between two pages that
dangles when either is deleted, duplicated or replaced, and a twin that goes
stale with nothing saying so. The decision was simpler: **an annotated-PDF
export writes the page as the viewer shows it**, its translation in place
of the original text while the translated view is on. What follows is how
the text gets into the file, measured on PyMuPDF 1.28.2 in November 2026
with the repository's translation fixture paper
(`frontend/tests/fixtures/translate/paper.tex`, two revtex pages, 154 math
spans) and synthetic pages. How it works now:
[dev/translation.md](../dev/translation.md#exporting-the-translation).

## Where the text comes from

The server cannot lay the translation out on its own. Geometry never
leaves the client, and the kept translations are keyed by hashes of the
viewer's segmentation, so the server knows neither the blocks nor which
kept rows belong to which of them. The viewer has all of it for every
translated page: each block's line boxes at scale 1, its font size, its
formula spans with their boxes, and the final texts. So the export carries
what the viewer shows, and the file matches what the reader checked on
screen. The cost is a second layout engine: the overlay fits text with the
browser's fonts, the file with MuPDF's, so line breaks and sizes differ a
little between the two.

A folder's export, whose papers no viewer shows, runs the viewer's code in
the browser with no viewer: pdf.js reads each paper the browser left
translated, its pages are seeded from the kept translations and laid out as
a page's export lays them out, and the layouts go with the job
([dev/translation.md](../dev/translation.md#a-folders-export)). Doing it
on the server instead would have needed a second segmenter, in Python, that
splits and hashes paragraphs exactly as the viewer does, or Node beside the
server.

## The original text: masked or taken out

Masks over the original lines, as the overlay draws them, would keep the
original in the file: every reader would select, search and copy both
texts. Redaction takes the glyphs out of the content stream and, with
`images=NONE` and `graphics=NONE`, leaves pictures and drawings as they
are; with `fill=False` it paints nothing in their place. MuPDF takes every
glyph whose box (the font's full height, not the outline) meets the area,
so redacting whole line boxes also took glyphs of the lines next to them.
Redacting only the middle of each line, MuPDF's own line boxes as the
stand-in, every other text block kept (the first and last two rows counted
glyph by glyph, the others by the length of the text before and after):

| Left out at the top and the bottom | Glyphs left in the redacted lines | Glyphs taken from the kept blocks |
|---|---|---|
| 0 | 0 | 4 |
| 0.15 | 0 | 4 |
| 0.3 | 0 | 3 |
| 0.35 | 0 | 3 |
| 0.4 | 3 | 0 |

The three taken at 0.3 were one word that MuPDF's blocks split across two
blocks on one baseline, an artefact of the stand-in. The writer uses 0.3.
A tall glyph of the next line that reaches into a line's box (a display
formula's operator) stays, and a test holds it there.

## The translation: one document for every page

`page.insert_htmlbox` lays out one box: it writes a temporary PDF per call
and shows it on the page, so every block embeds its own copy of the fonts.
The writer does the same work once: every block of every page is laid out
with `Story` into one temporary document, a page per translated page, which
is then shown on its page. MuPDF's writer embeds the fonts whole (Droid Sans
Fallback is 4 MB); on a page of three short lines in Chinese, Russian and
accented Latin, `subset_fonts` on that document alone took it from
3,958,170 to 74,557 bytes. Subsetting the whole output instead would touch
the paper's own fonts. The layout of the fixture's 42 blocks took 0.05 s.

`Story` needs a few workarounds:

- **Formulas need a placeholder of their size.** MuPDF's HTML ignores
  `display: inline-block`, and a span set so swallowed the rest of the
  paragraph. An `<img>` of an empty SVG takes its place in the line.
- **It reports a picture's place 0.8 of its height above where it draws
  it**, with the bottom on the baseline. That held across 128 combinations
  of size (7 to 20), picture height (0.2 to 2.5 em), line height and
  script, to within 0.00002 pt.
- **The writer draws that placeholder as an opaque one-pixel picture**
  stretched over the formula's place. A transparent PNG placeholder drew
  black. The temporary pages hold no other picture, so an images-only
  redaction removes them all.
- **Redaction drops the link and FreeText annotations it meets**; a
  Highlight, a sticky note, Ink, shapes and form fields stay. The first
  writer read the links back with `get_links` and wrote them with
  `insert_link`, which on a turned page put them back turned (PyMuPDF
  reports their rectangles in the display frame and writes them unrotated),
  and which restored none once the page held a link MuPDF cannot follow (no
  action, or JavaScript). The writer now puts the dropped annotations back
  in the page's `/Annots` as they were stored.
- **Saving with `garbage=1` is enough**: it drops the fonts the redaction
  left unused. `garbage=3` also merges duplicate objects, which the writer
  does not make, and took 0.21 s against 0.10 s on a synthetic
  thousand-page file, all of it under the lock.
- **Redaction grows faster than the number of areas**: on a synthetic page
  of a thousand short lines, redacting them took 9.3 s in one call and
  1.4 s in calls of a hundred.

## The masks

Redaction takes text, not what is drawn. On a scanned page the visible
words are the picture, and its text layer is the only thing redacted, so
the translation would be set over the scanned original. The overlay
covers each line with a white mask; the writer paints the same masks, each
line and the leading below it, after the redaction and before the
translation.

## Formulas: vector or picture

The first writer showed each formula as the original page through a clip
(`show_pdf_page` with `clip`). It looked right and was wrong. The clip
hides only what is drawn, so every formula carried the whole page's text,
invisible but read by every reader that ignores clips, pdf.js among them.
On the fixture's first page that came to 163,800 Latin letters extractable,
against 34 visible.

A copy of the page per formula, its other text redacted, keeps the formula
vector and alone:

| Way | Per formula | Size |
|---|---|---|
| a redaction annotation per run of other text | 219 ms | |
| one annotation holding every run as a quad | 41 ms | |
| picture at 600 dpi from one display list per page | 0.7 ms | about 830 bytes |

Even at 41 ms, each copy would also carry the page's line art, a pgfplots
figure in every formula, unless a second redaction pass took it out. The
overlay already draws formulas as pictures of the page. The writer does
the same at print resolution, with a clear ground. On the fixture, all 154
formulas as pictures took 1.31 s for the whole write, 1.73 s once the lines
were masked as well. The file went from 215,993 to 160,324 bytes, smaller
because the fonts the redaction left unused leave it. It holds one CJK font
subset and none of the original page's text (the formulas' own text came
later, below).

## The formula's text under its picture

The overlay keeps a formula's extracted text under its crop, so copying a
sentence copies its formulas. The file does the same with text written in
render mode 3, which paints nothing. Glyphs that are never painted still
need a font: each letter needs a glyph of its own, so that the font's
ToUnicode map can give it back. The candidates, measured in November 2026:

| Font | Where from | Licence | Size | Covers |
|---|---|---|---|---|
| Noto Sans Math | built into MuPDF (`fz_lookup_noto_math_font`) | SIL OFL | 253 KB, 2,655 glyphs | ASCII, 49 of 57 basic Greek, all of U+2200–22FF and U+2A00–2AFF, 99 of 112 arrows, 996 of 1,024 math alphanumerics; 6 of Latin-1's 96 (no µ, ·, é) |
| Charis SIL | built into MuPDF, the translation's face | SIL OFL | 58 KB | all of Latin-1 and Cyrillic, part of Greek (no ε, η, ψ), some operators (∑, ≤) |
| STIX Two Math, Latin Modern Math | would ship with the repo (not tried) | SIL OFL, GUST Font License | about 0.5 to 1 MB | all of the above |

No font has to ship with the repo. The writer sets each letter in the first
face that has it, Noto Sans Math and then Charis, and MuPDF's fallback
(`fz_encode_character_with_fallback`) finds the rest. Started in Charis
alone, the fallback brought in Noto Serif for a Greek letter, about 33 KB
more. Started in Noto Sans Math alone, it brought in Noto
Serif for µ. MuPDF's CFF subsetter keeps the shared subroutines, so a subset
of a few glyphs still weighs 25 KB, and the ToUnicode map lists the whole
font (9 KB).

A stand-in layout of the fixture paper, its own words as the translation and
MuPDF's 120 math spans as the formulas, came to 226 KB with the text and
169 KB without it. The write took the same 0.45 s.

Two findings shaped the writer:

- **Where the text goes.** Text added after the paragraph's is read after
  it: pdf.js orders a page's text as the content stream draws it, so a
  selection across a sentence would leave its formulas out. The
  placeholders' drawings are already in reading order, so each is replaced
  with the formula's text, written in the placeholder's own space by a PDF
  device of its own. A device of its own is needed because the writer leaves
  out a `Tf` that repeats the last one, and that would make a moved chunk
  depend on the state around it. The fonts are renamed for their objects so
  they meet none of the sheet's names. MuPDF 1.28's device writes ignored
  text in render mode 3, but 1.27's (the mobile builds) paints it, so the
  writer sets the mode itself.
- **Past U+FFFF.** MuPDF writes such a character into a ToUnicode map as its
  bare number, `<1d465>` for 𝑥, which readers garble: MuPDF and pdf.js 4.4
  both read U+1D46 (ᵆ), and an emoji in the translation came back as U+1F60
  (ὠ). The writer turns each one into its UTF-16 surrogates (`<d835dc65>`).
  With the surrogates, MuPDF, pypdf and pdf.js all give back
  `∑ᵢ 𝑥ᵢ² ⊗ ℝ → 𝒜` and the emoji.

## Justified text and the paper's own font

Both the overlay and the file set the translation in a plain serif, and
the overlay justified every box 18 em wide whatever the original did,
while the file was ragged. Measured in November 2026 on PyMuPDF 1.28.2,
pdf.js 4.4 and the fixture paper compiled with pdfLaTeX:

- **Telling a justified block.** Lines but the last ending within half an
  em of one edge mark every body paragraph of the fixture's two pages and
  none of its table rows, lone lines or reference entries. Ragged lines end
  where their words do, several em apart; a hyphen or microtype's
  protrusion stays well inside the half em.
- **`Story` justifies** (`text-align: justify`), the last line left
  ragged, and reports a picture in a justified line where it draws it, the
  widened gaps included (to 0.01 pt), so formulas keep their places.
- **The paper's fonts are subsets.** The fixture's CMR10 has 43 of the 64
  characters tried (ASCII letters, a few accented ones, punctuation), and no
  space; CMR7 has one letter. A translation
  out of English nearly always needs a letter the subset lacks (ä, z, a
  capital), so the font is used only for a block it draws whole, and a
  block falls back whole rather than letter by letter.
- **`has_glyph` is not enough.** MuPDF's own subsetter keeps the dropped
  letters' places and empties their outlines, and `has_glyph` still finds
  them (and `glyph_bbox` gives the font's box for every glyph). The outline
  must hold ink: MuPDF's `fz_outline_glyph` in the file, a canvas's
  `actualBoundingBox` in the browser.
- **Spaces.** A TeX font has no space glyph. Left to MuPDF, the space came
  from its fallback, Noto Serif, whose subset weighed 60 KB; set in Charis,
  12.5 KB, a font the file holds anyway once any block keeps the serif.
- **A program without a cmap.** `Story` finds a letter's glyph only
  through the font program's own cmap. LuaLaTeX and XeLaTeX embed every
  OpenType font (Latin Modern among them) as a bare CID-keyed CFF under a
  Type0/Identity-H, which maps no character: `has_glyph` is 0 for every
  letter, so every such paper fell back to the serif in the file while
  pdf.js, mapping from the ToUnicode, showed its font on screen. A subset
  TrueType program from MuPDF's subsetter has no cmap either. The cmap is
  built from the ToUnicode (`pdf_font_cmap`). Wrapping the CFF in an
  OpenType file as it is (glyph index = GID, through its charset) drew
  nothing: MuPDF reads a CIDFontType0's codes as CIDs, so the text it
  writes by glyph index found other, empty glyphs. Written again with each
  glyph at its CID, a LuaLaTeX and a XeLaTeX paper's LMRoman10-Regular set
  the translation, justified, in its own glyphs.
- **Embedding.** `Story` takes a font from its archive through
  `@font-face`; a PFA program works, and every story that loads the same
  bytes shares one font object in the output. `subset_fonts` leaves a Type
  1 program whole (21 KB for CMR10, already a subset).
- **In the browser** pdf.js 4.4 maps each glyph it can name from its
  character into the rebuilt font's cmap (`toUnicodeExtraMap`) and adds the
  font to `document.fonts` under its id, so CSS can set text in it. pdf.js
  drops the font's bytes once loaded unless `fontExtraProperties` is set, so
  no cmap can be read; whether the font has a character is read from a
  canvas, the character measured with two different fallbacks behind the
  font. How it works now:
  [dev/translation.md](../dev/translation.md#how-a-translation-is-set).

## Still open

- Formulas as vector glyphs, which would need the isolation above without
  its cost: MuPDF's content filter with a culler, or the formula's glyph
  runs written again with the page's own font resources.
- A block whose subset lacks a letter falls back to the serif whole. Mixing
  the paper's font with a matching face for the missing letters (a CM-like
  face for a TeX paper, Times for a Times one) would keep the look on more
  blocks, at a font of its own in the file.
- `Story` does not hyphenate, so a justified paragraph in the file can open
  wider gaps than the overlay's, which hyphenates where the browser has the
  language's dictionary.
- The overlay's face is pdf.js's id for the font in one open document. A
  paper's blocks outlive that document (the viewer keeps two, the
  translation eight), so a paper opened again after the viewer let it go
  shows its translation in the serif, its blocks naming the old ids. Its
  blocks would need their `font` found again in the new document (by name,
  among the page's fonts) without losing what their entries are tied to.
