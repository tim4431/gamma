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
subset and no hidden text.

## Still open

- Formulas as vector glyphs, which would need the isolation above without
  its cost: MuPDF's content filter with a culler, or the formula's glyph
  runs written again with the page's own font resources.
- The formula's text in the file, which the overlay keeps under its crop
  for copying. Writing it invisibly needs a font with math coverage.
- Justified text and the paper's own typeface, where the overlay and the
  file both use a plain serif.
