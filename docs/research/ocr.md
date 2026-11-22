# Scanned PDFs: telling an OCR layer apart, repairing its text, and what real OCR would add

A study from November 2026 of what Gamma does with a scanned paper. Such a
file is a picture of each page with an invisible OCR text layer drawn over
it, or with no text at all. The question was why selection, highlights,
search and the translated view were so poor on scans, and what to build. The
note records what was measured, the three repairs tried and why one was
picked. A second stage, real OCR, was then measured against a transcribed
truth: which engine, what it costs on each platform, when it beats a scan's
own layer, and a design that follows, whose server part is built. A third
stage measured how to find a scan's formulas and read them as LaTeX:
- a layout model finds them well enough for the translated view;
- of ten local readers, a 0.9B OCR model, GLM-OCR, reads them best, and
  comes closest to a frontier model's single pass.

None of the third stage is built. The mechanics
as built are in [dev/pdf_loading.md](../dev/pdf_loading.md) (the text
layer, and the recognized words in the viewer),
[dev/documents.md](../dev/documents.md#text-extraction) (the server's text,
and [Recognized text](../dev/documents.md#recognized-text): the engine, the
run, the store) and [dev/translation.md](../dev/translation.md) (the OCR
flag). The code of Stage 1 is
[scanDetect.js](../../frontend/src/pdf/scanDetect.js),
[scanText.js](../../frontend/src/pdf/scanText.js),
[pageText.js](../../frontend/src/pdf/pageText.js) and
[pdf_scan.py](../../backend/gamma/pdf_scan.py).

## What a scan's text layer looked like to the app

An OCR layer is text in render mode 3 (`3 Tr`, invisible), usually in a
font the file does not embed (Times-Roman, Arial, `GlyphLessFont`), laid
over one image per page. pdf.js 4.4.168 reads it like any other text. Its
`getTextContent` carries no render mode, so neither the viewer nor the
segmenter knew a page was a scan.

What the producers write differs more than expected:

- **One item per word, sized to the ink.** Lounis and Cohen-Tannoudji,
  J. Phys. II 2, 579 (1992), from HAL, sets each word's font size to that
  word's ink height. Words on one printed line vary in size by a factor of
  about 2 (median max/min 1.95–2.0), and only 3% of lines share a baseline.
  Each box is 0.71–0.75 of its word's ink width, centred on it.
- **One item per word, sized to the line.** The American Physical Society's
  legacy scans (iText 1.4) and Acrobat Capture (Acrobat 4 and Pdf.Capture
  5.2) set one size per line or close to it. Their boxes start where the
  word starts and are narrow: box over ink width 0.89–0.91 for the APS
  files, 0.69–0.97 for Capture.
- **Several words per item, scaled to the box.** Tesseract 5.3 (the
  Internet Archive's files), LuraDocument and CVISION fit each item to its
  box with `Tz`. Their boxes are accurate: box over ink 0.9–1.02.

pdf.js then adds damage of its own. Every word ends its item, so a page of
the Lounis paper has 741 items: 371 words, 337 `" "` items and 33 empty
end-of-line items. The `" "` items carry a width in unscaled pixel units, up
to 731 pt on a 510 pt page. A Ctrl+F match across one gave a rect running
past the page.

### The Lounis layer is drawn a line above its words

The Lounis pages set a y-flipping matrix (`0.24 0 0 -0.24 0 708.48` once
combined) before the text. Every item's transform is `[s, 0, 0, -s, x, y]`
and its glyphs hang *down* from `y`, so `y` is the top of the printed word,
checked against the ink. pdf.js's `TextLayer` takes the height as
`hypot(c, d)`, which drops the sign, and lays every span out upright above
`y`. Each span covered `[y − 0.195h, y + 0.805h]` while the print was at
`[y − h, y]`. Only a fifth of each span overlapped its word. The search
rects, `textRuns` (the segmenter's input) and `maskedRuns` made the same
assumption. MuPDF follows the flip correctly, so the server was not
affected.

On the four scored Lounis pages the baseline layer had a mean vertical error
of 0.62 line heights at the top and 0.78 at the bottom, no line passed the
line test below, and a pointer on a word landed in its span 0% of the time.
Turning the mirrored items upright alone, with nothing else changed, took
those to 0.07, 0.12, 45% and 74%.

### What the server read

MuPDF cut each printed line of the Lounis scan into about a line a word (348
lines for 371 words on page 2), and those of the APS scans into a few words
each (2.6 to 2.8). The content stream was already in reading order. So the
model reading `read_page` got about one word per line, a lone "Abstract" or
"References" in the prose passed the named-heading test and mislabelled a
selection's section, the metadata lookup's three-line title block held three
words, and highlight relocation dropped every bare number as a line number
(1 to 8 per page). Re-sorting the words by y then x is wrong: it reordered
the equation zones on 9 of the 14 scanned pages (stacked fractions, sub- and
superscripts).

## The corpus

Twelve scans of three kinds (a poor layer, a good one, none) and the
born-digital PDFs of the developer's own library. The files could not be
committed: most are under copyright, and the negatives are the developer's
library. They are named by citation here; the public ones can be fetched
again from their sources.

| Paper | Source, producer | Pages | Layer |
|---|---|---|---|
| Lounis and Cohen-Tannoudji, J. Phys. II 2, 579 (1992) | HAL jpa-00247657; HAL cover, OCR unknown | 15 | Poor: mirrored, sized to each word's ink, centred narrow boxes, math |
| Hänsch, Shahin and Schawlow, Phys. Rev. Lett. 27, 707 (1971) | APS legacy scan, iText 1.4 | 4 | Poor: one item per word, size spread 1.22–1.25; image covers 0.86 of the page |
| Holstein and Primakoff, Phys. Rev. 58, 1098 (1940) | APS legacy scan, iText 1.4 | 16 | Poor: as Hänsch, math-heavy; image covers 0.83 |
| Kitagawa and Ueda, Phys. Rev. A 47, 5138 (1993) | APS legacy scan, iText 1.4 | 6 | Poor: as Hänsch; layer 0.7% wider than the scan |
| Feshbach, Ann. Phys. 5, 357 (1958) | Acrobat 4 Capture; page image in 19 strips | 34 | Poor: one size per line in Arial, boxes start at the word, baseline offset |
| NACA Report 1135 (Ames Research Staff, 1953) | NASA NTRS 19930091059; Pdf.Capture 5.2 | 70 | Poor: integer coordinates, type smaller than the print, tables |
| Kollin, Benton and Jepsen, Proc. SPIE 1136 (1989) | CVISION PdfCompressor 5.0 | 8 | Good, but two layers: per word and a duplicate per line |
| Le Journal de Physique et le Radium 22 (3), March 1961 | Internet Archive, Tesseract 5.3 | 64 | Good: French, `Tz` per word |
| Physical Review 2 (3), September 1913 | Internet Archive from microfilm, Tesseract 5.3 | 88 | Good: tables |
| Annalen der Physik und Chemie 31 (1834), one issue | Internet Archive from microfilm, Tesseract 5.3 | 16 | Good: German Antiqua |
| Siegman, *Lasers* (1986) | LuraDocument | 1,310 | Good: whole lines as one item, the best aligned |
| Shannon, Bell Syst. Tech. J. 27, 379 (1948) | Internet Archive, PDFlib | 45 | None: images only |

The negatives: 122 born-digital PDFs, 3,809 pages, every page read. Ten
pages have render-mode-3 text, all of it `<latexit sha1_base64=…>` payloads
in size-0 Courier from LaTeXiT equation graphics. Five pages have images over
0.6 of the page (at most 0.85). No page has both.

OCR text is also wrong in ways no geometry can fix: "J. PJ1ys. II" for
*J. Phys. II*, "Rksum4" for *Résumé*, "ii03" for 1103, and the volume 5 in
Feshbach's running head read as 6.

## How a layer was scored

The ground truth is the ink, taken from the scan alone. A script renders
each page at 200 dpi in grey (the OCR text is invisible, so the pixels are
the scan) and counts a pixel as dark below 0.65 of the page's median grey.

1. The words are the whitespace tokens of the selection string pdf.js
   gives (items joined, `\n` after `hasEOL`), so one-word, multi-word and
   glued items are handled alike. Each word's estimated box comes from its
   item: x from the width, y as `[f − |d|, f]` when mirrored, else
   `[f − 0.22h, f + 0.75h]`.
2. Lines follow stream order, broken at a `\n`, a vertical jump over 0.6
   line heights, x running backwards, or a gap over 3 line heights.
3. A line's ink band grows from its most-inked row while a row has at least
   max(2, 1% of the width) dark pixels; touching lines are cut at a valley
   under 15% of the peak.
4. A word's ink extent is the dark columns over the band, cut at the widest
   blank run between neighbours. A word is scored only when both cuts are
   real gaps, its ink is 0.45–2.3 times the estimate and the band is
   confident.

That kept 81–97% of the words per file (equation and table pages 47–83%).
Overlays of nine pages were checked by eye. The sample was 46 pages: four per
scan with a layer (three of text, one of equations, tables or references)
and six of Siegman.

The evaluator feeds a candidate `repairTextContent(tc)` each page's
TextContent and draws its spans as `TextLayer` does. The measures used below:

- **vTop, vBot**: mean distance of the span's top and bottom from the ink's,
  in line heights.
- **lineOK**: lines whose span band covers 90% of the ink band and overshoots
  it by at most 25% of its height.
- **own90**: words whose own span covers 90% of their ink width.
- **hit**: ink inside the word's span that also holds the band's middle row,
  so a pointer on the word lands in it.
- **cover**: a line's ink columns covered by some span.
- **L, R**: signed error of the span's left and right edges, in line
  heights. With the space folded into the word, R near +0.40 is the word gap
  and L should be near 0.
- **blocks/pg**: what the translation segmenter makes of the page.

Every candidate also had to keep the page's text exactly, keep stream order,
give upright `[h, 0, 0, h, x, b]` items that tile each line, and stay on the
page. An oracle built from the truth scores lineOK 100%, own90 100%, R
+0.40 and 35.6 blocks a page; the baseline 48%, 53%, −0.09 and 59.5.

## Stage 1: telling a scan page apart

**The rule.** A page is a scan with an OCR layer when nearly all of its
shown glyphs are invisible and images cover most of it:

- invisible means `Tr & 3 == 3` (modes 3 and 7), tracked through `q`/`Q` and
  forms, counting non-space glyphs; the share must be at least 0.90;
- coverage is the union of every painted image box, through the CTM, clipped
  to the page, on a 100 × 100 grid; it must be at least 0.70;
- at least one glyph is shown; annotations are not counted.

Neither term separates the sets alone. LaTeXiT pages reach 0.968 invisible
with 0.011 coverage, and a born-digital page with a large photo reaches 0.853
coverage with nothing invisible. Coverage 0.70 sits between the lowest scan
(0.81, Holstein and Primakoff) and the highest negative with invisible text
(0.17). Feshbach's page is 19 strips, so coverage has to be a union. Hänsch's
image covers 0.86, so a 0.9 threshold would have missed it.

**Reaching the rule in the viewer.** The render mode is only in the operator
list, which pdf.js does not share with the render: the first
`getOperatorList()` after a paint is a full worker pass. On scans that
cost 39 ms at the median and up to 654 ms, nearly all of it image decoding.
So the viewer first runs a prescreen on the TextContent it already has
(0.1 ms a page), which passes when any of four marks of measured text holds:

1. **mirror**: at least half the characters in a y-flipped text space
   (only Lounis);
2. **scaled**: at least 20% of characters with `|a/|d| − 1| > 0.03`, a `Tz`
   fitted to each box (Tesseract, Acrobat 4 Capture, LuraDocument, CVISION);
3. **sizes**: at least 10 sizes in 0.05 pt bins, the commonest under 30% of
   the characters (the APS scans, Lounis);
4. **lattice**: at least half the word origins on the pixel grid of a scan
   resolution (72 to 600 dpi, within 0.012 pt), which catches Pdf.Capture's
   integer points and Acrobat 4's 0.75 pt grid.

No single mark covers every producer. Only a page that passes gets an
operator list and the confirm. A per-document memo keyed by font id then
skips most of those. A page whose text is all in fonts seen only invisible
on confirmed scans counts as a scan; one whose text is all in fonts seen
only visible counts as not; anything else is confirmed.

**Results.**

| Set | Pages | Prescreen passes | Confirmed scans | Wrong |
|---|---|---|---|---|
| Scan pages with a layer (every page of ten scans, 98 of Siegman's sampled) | 418 | 418 | 418 | 0 |
| Scan pages without text (Shannon's 45, 2 of Siegman's 5) | 47 | 0 | 0 | 0 |
| Born-digital pages | 3,809 | 185 (4.9%) | 0 | 0 |

The Python twin covered every page of Siegman's book, its 5 image-only
pages among them (below). Every scan page cleared at least one mark by 1.55
times its threshold or more (the weakest a Kollin page). The born-digital
passes are mostly files typeset on a device grid (dvips and Ghostscript,
FrameMaker); their operator lists cost a median of about 7 ms (p90 20 ms),
but up to 0.6 s on a page drawing many vector marks, which that page's text
layer waits for. With the memo, the 185 passes needed 88 operator lists
and the 418 scan pages 20, and the memo was never wrong. The JS confirm also
ran on a seeded random 300 negative pages regardless of the prescreen: none
positive. The closest negative scored 0.25 of the way to a flip.

Ctrl+F over Siegman's 1,310 pages costs 6.2 s of TextContent today. The
prescreen adds 0.09 s. Without the memo the confirms would add about 30 s,
and about 3.6 minutes on a book of Internet Archive JPX pages; with it, two
confirms. A 500-page born-digital book goes from 3.6 s to about 3.9 s.

**The server's twin** applies the same rule with PyMuPDF: a byte scan of
the page's and its forms' streams for `3 Tr` or `7 Tr` first, then span
alpha from `get_text("dict")` (MuPDF reports both modes as alpha 0), then
`get_image_info()` boxes on the same grid. `get_texttrace()` was rejected:
it drops mode 7 and reports mode 2 twice. JS and Python agreed on all 466
scan-file pages and every confirmed negative; Python also ran on all 1,310
of Siegman's pages (1,305 scans, the 5 image-only pages not). As shipped,
the check costs a median 0.2 ms on a born-digital page (mean 1.0 ms), and a
page's text 3.0 ms on average against 2.2 before; a scan page's text costs
about 5 ms against 1 (Siegman's book about 6 s more to index). A first
version's byte scan, a pattern starting with the operand, was tried at
every byte and nearly doubled the born-digital text cost (+88%); finding
the operator as a literal and reading its operand just before it fixed
that.

## Stage 1: the repair, three candidates

All three take a page's TextContent and nothing else, keep its words and
their order, group them into lines in stream order (never re-sorted, so
columns and equations keep their reading order), give a line one band and
upright items, fold the space into the word (`"word "`) and tile each line.
They differ in how much they infer.

- **tile**: fixed typographic constants. Upright boxes are read as
  `[f − 0.2h, f + 0.75h]`, mirrored ones as `[f − |d|, f]`. A letter test
  decides per page whether boxes hug each word's ink (words of x-height
  letters under 0.85 of the height of words with ascenders; 0.65–0.66 on
  Lounis, 0.99–1.07 on every other file). If so the line takes the union of
  its words' bands, outliers dropped; otherwise their median. Cuts between
  words at the middle of the gap. 2.2 ms a page.
- **calibrate**: fits each page's box-to-ink ratio and anchoring by a
  regression on the gaps between neighbours, with a fixed effect per line
  (a justified line has one word gap), then widens each box about its fitted
  anchor. It recovers each producer's layout untold: Lounis centred
  (α 0.45–0.50, truth 0.49), the APS and Capture files left-anchored
  (α 0–0.1, k 1.15–1.33). 11.6 ms a page.
- **free**: the same horizontal fit plus a vertical model per line:
  ascender, x-height, baseline and descender lines on ink-sized pages; on
  the others a band height from the median of three cues (the size, the
  line pitch, the widths), each with a prior. About 500 lines. 4 ms a page.

| 46 pages | Baseline | tile | calibrate | free | Oracle |
|---|---|---|---|---|---|
| lineOK, all | 48% | 53% | 53% | 57% | 100% |
| lineOK, poor layers | 45% | 57% | 58% | 65% | 100% |
| own90, all | 53% | 98% | 92% | 97% | 100% |
| own90, poor layers | 26% | 97% | 87% | 95% | 100% |
| own90, Kitagawa and Ueda | 20% | 97% | 54% | 80% | 100% |
| lineOK, NACA 1135 | 19% | 14% | 14% | 47% | 100% |
| L / R, all | 0.03 / −0.09 | −0.30 / 0.17 | 0.00 / 0.42 | −0.18 / 0.31 | 0 / 0.40 |
| Blocks a page | 59.5 | 33.4 | 34.0 | 32.5 | 35.6 |

Every candidate kept the text on 46 of 46 pages with no shape, tiling or
order faults. free also ran over all 1,632 pages of the scans with a layer
with no text change. Lounis page 2 went from 96 blocks to 16–18 (oracle 23), and a
Kitagawa page from 86 blocks of one or two lines to about 30 paragraphs of
four to nine.

**Why tile.** calibrate placed letters best (L 0.00, R 0.42, the oracle's)
but loses the Kitagawa layer. That layer is 0.7% wider than its scan, so
every box starts after its ink by an amount that grows across the page (1.1
pt in the left column, 2.8 pt in the right). A gap fit cannot see a uniform
shift, so widening each box from its fitted anchor pushed each span into the
next word's ink: own90 54%, stolen 11%. Any layer that drifts against its
scan would do the same, and drift is common in scans. free had the best
vertical numbers, chiefly NACA's band height, but carries 500 lines of
fitted priors for gains of a few points elsewhere, and still loses part of
Kitagawa (80%). tile is the simplest and does not depend on a box's
placement inside its ink, so it is the most robust to drift.

### Where to cut between two words

The midpoint cut put every span's left edge half a gap before its ink (L
−0.30) on the left-anchored layers, where a box starts at the word and its
slack is on its right. An earlier sweep had shown that giving the left word
a fixed 0.65 of the gap helped NACA (own90 88% to 95%) but cost Kitagawa (97%
to 93%), whose boxes are shifted right. The rule taken bounds the distance
instead. On pages whose boxes are sized to the line, the cut sits at most
0.35 line heights before the next box's start; on pages whose boxes hug each
word's ink (the letter test above; the corpus's only such producer centres
them), the cut stays at the middle.

| own90 | Midpoint | Capped at 0.35 |
|---|---|---|
| NACA 1135 | 88% | 94% |
| Feshbach | 97% | 98% |
| Kitagawa and Ueda | 97% | 97% |
| L / R, all | −0.30 / 0.17 | −0.25 / 0.22 |

The band top stays at 0.75 of the size. 0.8, pdf.js's own ascent, scored
lineOK 51% on all pages against 53%.

### What the chosen design scores

Baseline → chosen, the 46 pages; 3.6 ms a page.

| File | vTop | vBot | lineOK | own90 | hit | cover | Blocks a page |
|---|---|---|---|---|---|---|---|
| Lounis | 0.62 → 0.03 | 0.78 → 0.05 | 0% → 92% | 15% → 98% | 0% → 99% | 12% → 98% | 134.8 → 28.5 |
| Hänsch | 0.10 → 0.05 | 0.12 → 0.12 | 66% → 66% | 45% → 99% | 91% → 100% | 91% → 100% | 49.0 → 42.8 |
| Holstein | 0.12 → 0.06 | 0.08 → 0.07 | 69% → 79% | 45% → 99% | 91% → 100% | 90% → 99% | 54.3 → 33.0 |
| Kitagawa | 0.09 → 0.04 | 0.10 → 0.09 | 67% → 69% | 20% → 97% | 84% → 99% | 85% → 99% | 71.5 → 33.5 |
| Feshbach | 0.30 → 0.24 | 0.24 → 0.24 | 15% → 20% | 16% → 98% | 76% → 92% | 75% → 89% | 28.3 → 19.3 |
| NACA 1135 | 0.21 → 0.25 | 0.07 → 0.07 | 19% → 14% | 5% → 94% | 73% → 97% | 73% → 92% | 192.3 → 84.0 |
| Kollin | 0.22 → 0.15 | 0.13 → 0.13 | 43% → 44% | 99% → 99% | 99% → 100% | 100% → 100% | 41.0 → 29.8 |
| J. Phys. Radium 1961 | 0.08 → 0.05 | 0.12 → 0.12 | 57% → 63% | 49% → 99% | 89% → 100% | 88% → 99% | 53.3 → 56.8 |
| Phys. Rev. 1913 | 0.04 → 0.07 | 0.08 → 0.09 | 65% → 54% | 47% → 98% | 89% → 99% | 89% → 99% | 26.3 → 22.0 |
| Annalen 1834 | 0.06 → 0.05 | 0.09 → 0.09 | 74% → 77% | 56% → 100% | 90% → 100% | 91% → 100% | 13.0 → 13.3 |
| Siegman | 0.10 → 0.05 | 0.08 → 0.08 | 74% → 74% | 93% → 96% | 98% → 99% | 100% → 100% | 14.2 → 14.2 |
| All | 0.17 → 0.09 | 0.15 → 0.11 | 48% → 53% | 53% → 98% | 85% → 99% | 90% → 99% | 59.5 → 33.4 |

Two changes came after this table and measured the same on these pages: a
word starts a new line when it starts before the word before it ends (the
server's row rule, which needed it on layers MuPDF reads whole lines of),
and a line's ends are bounded by the page's box rather than its outermost
boxes. The browser check then found one span TextLayer drew short: a line
ending in a one-letter word, which pdf.js stretches to its width only when
its matrix is stretched; such items now say so, and Lounis's own90 is 99%.

The horizontal measures are close to the oracle everywhere. lineOK stays low
where the band's height or offset is the producer's and cannot be read from
the TextContent (below). About 8% of lines have no descender, so their
uniform band hangs 0.2–0.3 line heights below the ink; a band per line's
letters would score better but give highlights of uneven height, and was
left out.

## The server side

The server regroups a scan page's text into rows, with the confirm rule
deciding which pages. Stream order is kept; a line break between two words
of one row becomes a space. The length is unchanged, so every character
offset the chat, `read_page` and the seams hand out stays valid. The glyph
walk behind highlight relocation and quote placement does the same, with a
boxless space in place of the `\n` and the raw glyph boxes kept, because
imported annotations were drawn over the narrow layer.

No index version was bumped. Normalized, the regrouped text of the 14
scanned Lounis pages differs from the stored rows in 4 places, each a wrong
hyphen join that a word cut off on its own line had made, so the
search index and the document map do not change in practice, and a bump
would re-extract every PDF of every workspace. The page-text cache is in
memory and starts cold on a restart.

## Translating OCR text

The translation request says when its texts came from a scan's OCR layer
(the client knows from the detector; the server could only tell for a
stored file, and a request does not say which pages its texts came from).
The system prompt then asks the model to read through misread characters
(digits or symbols for letters, lost accents, split or merged words) and to
translate what the author wrote. The flag is per chunk, from the pages its
texts come from: HAL puts a born-digital cover before every scan, and the
cover's chunks go without it. It is kept out of the kept translation's key,
which the server derives without the client; the new segmentation gives a
scan new source texts anyway, so rows made before it simply stop matching.

The brief's prompt says the same, so its glossary does not propose garbled
terms. A first version took the brief's flag from the job's first page; a
review caught that a whole-document job started on HAL's cover then made the
paper's brief without it, and the brief is kept per paper whatever later
jobs say. The brief's flag now follows the text it reads: the server checks
a stored file's opening pages itself, and the viewer says so of a head it
read from scanned pages.

## What no TextContent repair can fix

- **Feshbach's baseline offset.** Acrobat 4 Capture puts `f` about 0.2 of a
  line above the baseline (ink at `[f − 0.40h, f + 0.55h]`), by an amount
  that changes by page (0.13 to 0.32 line heights). Bands sit 0.24 line
  heights too high; lineOK 20%.
- **NACA's undersized type.** The ink band is 1.21 times the size, so the
  span tops fall short (vTop 0.25, lineOK 14%). A line-pitch rule would
  catch it but inflate any double-spaced typescript 1.6 times.
- **Kitagawa's 0.7% x drift.** tile absorbs it; widening a box cannot.
- **Kollin's duplicate layer.** Every word is drawn twice, per word and as a
  spaceless glued line ("HainesandBrumm6and…"); the text must be kept, so
  both are tiled and each word is covered twice.
- **Skew** the layer does not encode: on a Journal de Physique page the truth
  finds 166 lines where the repair keeps the layer's 119, and a median band is off by up to
  half the skew.
- **Wrong characters**, which only real OCR can replace.

## Stage 2: what real OCR adds

Measured in November 2026, still before anything was built: which engine
reads these scans best, what it costs, where it can run, and when it beats
the layer a scan already has.

### Pages and ground truth

Fifteen pages: a text page from every scan in the corpus (two from Lounis),
two display-equation pages (Lounis p8, Holstein and Primakoff p8) and page 2
of Shannon (1948), which has no layer. Each page was rendered at 300 dpi in
grey, the picture every engine got.

The truth was transcribed twice under one set of rules. The rules were:
- write what is printed, printing errors such as "Febry-Perot" kept;
- one printed line per line, in reading order;
- tag display equations to be scored apart, and leave out text inside figures.

Both passes were done by a model reading full-resolution tiles of the scan:
- the first proofread a Tesseract draft, with two other engines' readings
  shown beside each line;
- the second read the tiles alone and never saw any OCR output.

A third pass settled every line on which the two differed, from a crop of
the line.
- Over the 13 text pages (796 lines) the passes differed on two text lines
  (a space and a built-up fraction) and on eight equation or figure lines.
- On the two equation pages they differed on 14 lines, nearly all in how a
  built-up fraction or the space around an operator is written.
- Five pages came out identical byte for byte.

### Measures

- **Errors**: character error rate. Each printed line is matched to the
  cheapest stretch of the engine's page text, so reading order and line
  merges cost nothing. Both sides are normalized first (NFKC; quotes and
  dashes unified; whitespace collapsed).
- **Recall, precision**: the printed words found in the engine's text, and
  the engine's words found in the print, counted as multisets.
- **Continuity**: of consecutive printed lines, the share whose text follows
  directly in the engine's text. Selection, translation blocks and a phrase
  searched across a line break all need it.
- **Missed**: printed lines matched at less than half their characters.

Times are recognition alone on a Ryzen 9 9900X. One thread stands in for a
NAS core; no NAS was measured.

### Results

The 12 text pages that have a layer:

| Engine | Errors | Recall | Precision | Continuity | Missed | s/page, one thread |
|---|---|---|---|---|---|---|
| The existing layers | 1.80% | 95.7% | 78.5% | 91.7% | 0.3% | – |
| Tesseract 5.5.4, fast models, eng+fra+deu | 1.29% | 96.3% | 93.4% | 95.0% | 0.1% | 2.5 (CLI), 3.7 (tesserocr) |
| the same, best models | 1.29% | 96.3% | 93.2% | 95.0% | 0.1% | 4.9 (one language a page) |
| the same, English only | 1.53% | 95.3% | 92.5% | 94.9% | 0.1% | 1.8 |
| tesseract.js 7.0.0, automatic page segmentation | 1.34% | 96.2% | 93.2% | 95.0% | 0.1% | 4.3 (WASM in Node) |
| tesseract.js 7.0.0 as it comes (one block) | 1.62% | 95.3% | 91.4% | 41.0% | 0.0% | 4.4 |
| MuPDF's Tesseract (PyMuPDF 1.28.2), fast | 2.11% | 95.3% | 92.0% | 41.7% | 0.3% | 1.8 |
| OnnxTR 0.9, db_resnet50 + parseq | 1.77% | 95.8% | 93.1% | 66.7% | 0.1% | 18.4 on 24 threads |
| OnnxTR 0.9, fast_base + crnn_vgg16_bn | 2.30% | 96.0% | 92.3% | 75.1% | 2.0% | 13.7 |
| RapidOCR 3.10, PP-OCRv6 small | 2.98% | 94.7% | 93.6% | 45.5% | 2.8% | 14.1 |
| RapidOCR 3.10, PP-OCRv6 medium | 3.21% | 94.3% | 94.8% | 44.4% | 3.4% | 21.0 on 24 threads |

The layers' precision is low because Kollin's page carries every word twice.
Shannon's page, which has no layer, reads at 0.41% errors with Tesseract.

The layer against Tesseract (fast, eng+fra+deu), page by page:

| Page | Layer errors | Layer recall | OCR errors | OCR recall | Words the two share |
|---|---|---|---|---|---|
| Lounis p2 | 3.69% | 87.2% | 0.81% | 96.2% | 88.9% |
| Lounis p4 | 3.84% | 91.1% | 2.12% | 93.8% | 90.4% |
| NACA p15 | 4.26% | 93.3% | 2.65% | 96.0% | 82.5% |
| J. Phys. Radium p12 | 3.63% | 93.0% | 2.48% | 92.7% | 88.6% |
| Holstein p2 | 2.20% | 94.3% | 1.63% | 94.7% | 94.4% |
| Kitagawa p4 | 2.92% | 94.5% | 2.56% | 94.5% | 92.6% |
| Hänsch p2 | 0.95% | 97.2% | 1.26% | 98.2% | 95.2% |
| Feshbach p3 | 0.71% | 96.9% | 0.78% | 97.7% | 97.7% |
| Annalen p10 | 1.14% | 95.6% | 0.71% | 95.9% | 98.9% |
| Phys. Rev. p8 | 0.89% | 98.3% | 0.57% | 98.3% | 98.5% |
| Siegman p99 | 0.41% | 98.8% | 0.51% | 98.4% | 98.4% |
| Kollin p2 | 0.05% | 99.8% | 0.39% | 98.6% | 98.9% |

Display-equation lines (2,341 characters) came out 47–57% wrong with every
engine, and 56.6% wrong in the existing layers.

### What the numbers say

- **Tesseract 5.5 is the engine.** It makes the fewest errors. Among the
  engines it is the only one that keeps reading order on two-column pages
  through its own layout analysis.
  - The fast models do as well as the best ones, at half the time and a
    quarter of the size.
  - Loading English, French and German together costs nothing on English
    pages and about 10% in time. English alone loses the accents: the
    French page goes from 2.5% to 3.3% errors, the German one from 0.7% to
    2.8%.
  - OpenMP threads slow it down. With one thread per page, MuPDF's copy
    took 1.8 s against 2.8 s, and tesserocr 3.7 s against 4.9 s.
- **RapidOCR and OnnxTR are no better here, and slower.** RapidOCR reads
  the most lines exactly (79–81%). It also misses whole lines (2.8–3.4%)
  and interleaves the columns.
  - OnnxTR's README gives 0.57 s a page. That figure is for form images,
    not for 600-word pages at 300 dpi.
  - PP-OCRv6's medium model is no better than the small one.
- **MuPDF's built-in Tesseract reads the characters about as well, but
  loses the layout.** MuPDF regroups the recognized words by position:
  - its blocks span both columns, so continuity is 42%;
  - it starts a new line at the wider space after a full stop, which
    accounts for most of its extra errors;
  - it dropped two printed lines of the French page.

  A reading-order step was tried on every engine's lines and words: lines
  split at gaps no word space makes, or rebuilt from the geometry, then the
  columns split at the gutter the fewest lines cross.
  - It reached 56–93% continuity depending on the engine: 83% on MuPDF's
    lines, 93% on RapidOCR's.
  - It added errors every time (MuPDF's 2.11% became 2.32%).

  Tesseract's own layout analysis is worth keeping rather than rebuilt.
- **tesseract.js matches the server's Tesseract once its page segmentation
  is set to automatic.** As it comes, it reads the page as one block and
  runs the two columns together.
- **OCR is worth it on the poor layers.** On Lounis p2 errors fall from 3.7%
  to 0.8% and word recall rises from 87% to 96%. NACA, J. Phys. Radium and
  Holstein gain too. On the good layers OCR is equal or slightly worse
  (Kollin's CVISION layer, Siegman's LuraDocument). Those layers were made
  from better pictures than a 300-dpi render of the PDF.
  - The share of words a page's layer has in common with a fresh OCR of it
    separates the two groups: 98–99% on the good layers, 82–90% where OCR
    helps. Hänsch, Holstein and Kitagawa share 92–95% and gain little. The
    threshold rests on 12 pages.
- **No engine reads display equations.** They stay with
  [Stage 3](#stage-3-formulas-on-scanned-pages).

### Where each engine can run

| Platform | Tesseract with its layout | Other options |
|---|---|---|
| Docker, amd64 and arm64 | The tesserocr 2.13.0 wheel (MIT; 5.6 and 5.3 MB, Tesseract 5.5.4 and Leptonica inside) plus the models: fast eng 4.1 MB, fra 1.1, deu 1.5. No apt package is needed. Debian's `tesseract-ocr` would add 69 packages, about 100 MiB. | MuPDF's Tesseract, already in PyMuPDF 1.28.2: only the models are needed, but the layout is lost. |
| Desktop, Windows and macOS | tesserocr wheels: 23 MB on Windows, 3.6–4.1 MB on macOS. PyInstaller 6.22 freezes it with no extra flag: the Windows bundle grew by 65 MB (57.7 MB of native libraries, 33 MB of that ICU's data, and the 6.8 MB of models); a minimal Linux freeze took 29 MB of libraries. macOS was not tried. | Windows.Media.Ocr reads only the languages Windows has installed. With no French pack, "garçon déjà prêt" came back as "garcon déjå prét". |
| Android, iPad | None. The PyMuPDF the apps ship (Flet's 1.27.2.3) is built without OCR ("No OCR support in this build"), and tesserocr has no mobile wheels on PyPI or Flet's index. | tesseract.js in the app's web view. Apple Vision from Swift on the iPad (0 MB). ML Kit on Android: about 4 MB bundled, or 260 KB through Play services; it needs a Kotlin bridge and sends usage metrics to Google. RapidOCR's dependencies exist on Flet's index (about 60 MB with models), but it read worst here. |
| Any browser | tesseract.js: about 4.5 MB on first use (the 1.46 MB LSTM core and 2.95 MB of English best_int), both servable from Gamma's own files. | – |

The survey's published comparisons agree with these results:
- On historical newspapers Tesseract beat the older PaddleOCR models and
  EasyOCR ([Carlson et al.](https://arxiv.org/html/2304.02737)).
- No published comparison of PP-OCRv5 or v6 with Tesseract on old scans was
  found.

Vision-language models that write LaTeX are the field's answer for
equations, and they are a Stage 3 question:
- Chandra, LightOnOCR-2 and olmOCR score 82–89 on
  [olmOCR-Bench](https://github.com/allenai/olmocr)'s old-scan math, as
  reported by the models' own authors.
- The models small enough for a CPU have 0.9–1.2 billion parameters, and no
  CPU time per page has been published for them.

[Stage 3](#stage-3-formulas-on-scanned-pages) measured the small ones that
llama.cpp runs, on formula crops, on the CPU and the GPU.

### A design that follows

It is built ([dev/documents.md](../dev/documents.md#recognized-text)): the
server's engine from the Document menu, tesseract.js in the window where
the server has none, automatic runs for scans with no text, and a way back
to the file's own text. What is left is under [Still open](#still-open).

- **Engine.** Tesseract 5.5 everywhere, with the fast models and English,
  French and German loaded by default:
  - tesserocr on the server (Docker and desktop);
  - tesseract.js where the server has none (the mobile apps, or any
    browser), with automatic page segmentation set.

  Both give the same text and the same kind of word boxes.
- **When to run it.**
  - A scanned page with no text: always worth it.
  - A scan with a layer: OCR a page or two first, and replace the layer when
    under about 93% of words are shared. Otherwise keep the layer and Stage
    1's repair.
  - In both cases the Document menu could also offer it.

  It runs as a background job. At 2.5–4 s a page on one core here, and
  perhaps two to three times that on a NAS core (not measured), a 15-page
  paper takes one to three minutes. Siegman's 1,310 pages would take hours.
- **Storage.** Kept per stored file, by hash, as derived data beside the
  text index: words with their boxes, about 7 KB a page gzipped (17 KB as
  compact JSON). A 300-page book is about 2 MB.
- **Reading.** Through the seams Stage 1 built, so search, the chat, MCP,
  quotes and translation need no OCR case of their own:
  - `pageText` gives the viewer upright items from the OCR words, with no
    repair needed, since a word box is the ink;
  - `pdf_text`'s text and glyph readers read the same words;
  - the document's search index is rebuilt once.
- **Highlights.** The page picture does not change, so a highlight's
  rectangles still hold; only the text under them changes. Moving them by
  their passage, as for a replaced PDF, would lose those made on garbled
  text ("Rksum4"). They keep their place and take the new text as their
  quote.
- **Not the default: a new version of the PDF.** Writing the OCR text into
  a new version of the file, as OCRmyPDF does, would carry the better text
  into downloads and Zotero, but it changes the person's file: a synced
  vault's, a Zotero attachment's. MuPDF's `Pixmap.pdfocr_tobytes`, the way
  to do it without Tesseract's command line, wrote no text in a test here.

### The window's pipeline, measured once built

Measured in November 2026 on the built pipeline, in headless Chromium on
the same machine: the page drawn by pdf.js 4.4.168 (legacy build) at the
plan's 300 dpi, turned grey into a PGM by `ocrRaster.js`, read by
tesseract.js 7.0.0 with the SIMD LSTM core, the fast models from
`backend/tessdata` and automatic page segmentation, and its TSV parsed as
the window sends it. Twelve of the 13 text pages; Siegman p99 was left
out, since its PDF is only in a developer's own library.

| Page | Errors, window | Errors, server | Words shared with the server | The same, over the truth's words | Recognize s | Draw s |
|---|---|---|---|---|---|---|
| Lounis p2 | 1.10% | 0.81% | 0.986 | 0.994 | 1.94 | 0.12 |
| Lounis p4 | 2.04% | 2.12% | 0.933 | 1.000 | 2.90 | 0.10 |
| Hänsch p2 | 0.81% | 1.26% | 0.968 | 1.000 | 4.53 | 0.46 |
| Holstein p2 | 1.55% | 1.63% | 0.964 | 0.995 | 6.40 | 0.45 |
| Kitagawa p4 | 2.49% | 2.56% | 0.960 | 0.996 | 5.13 | 0.46 |
| Feshbach p3 | 0.71% | 0.78% | 0.990 | 1.000 | 3.28 | 0.12 |
| NACA p15 | 2.65% | 2.65% | 0.871 | 1.000 | 4.40 | 0.23 |
| Kollin p2 | 0.46% | 0.39% | 0.990 | 0.996 | 6.96 | 0.65 |
| J. Phys. Radium p12 | 2.57% | 2.48% | 0.928 | 0.985 | 5.14 | 0.82 |
| Phys. Rev. p8 | 0.78% | 0.57% | 0.996 | 0.998 | 3.43 | 0.88 |
| Annalen p10 | 0.71% | 0.71% | 1.000 | 1.000 | 2.14 | 0.45 |
| Shannon p2 | 0.41% | 0.41% | 0.981 | 1.000 | 2.80 | 0.06 |
| **All** | **1.31%** | **1.33%** | **0.964** | **0.997** | 4.09 mean | 0.40 mean |

- **The window reads as well as the server.** 1.31% errors against
  1.33%, and 95.0% continuity against 94.8%.
- **The two read different words outside the text.** Six pages share
  fewer than 97% of their words with the server's reading (NACA p15 the
  fewest, 87%). Nearly all the words that differ are in figures, display
  math and specks, which the truth leaves out: of the window's 63 extra
  words on NACA p15, 6 are in the truth. Over the truth's own words the
  two share 99.7%.
- **Time.** The engine was ready in 0.16 s from a local server, and a page
  took about 4.1 s to recognize and 0.4 s to draw, about the server's
  3.8 s. No request left the page's own server.
- **One pdf.js rule found on the way.** A render with intent `print`
  refuses an optional-content config read for the display intent, so
  every page failed until the window let the print intent use its own.

### Not measured

- A NAS, a phone or a tablet. The window's pipeline was measured in
  Chromium on a desktop only: not in WebKit, Firefox or the apps' web
  views, and not over a slow link, where the core and the models are some
  11 MB to fetch once.
- tesserocr frozen by PyInstaller on macOS, and MuPDF's Tesseract on Linux
  and macOS (seen only as strings in the wheels).
- Apple Vision and ML Kit accuracy.
- Fraktur: the Annalen page is set in Antiqua.
- A render above 300 dpi, or at the scan's own resolution.

## Stage 3: formulas on scanned pages

Measured in November 2026, before anything was built: how to find the
formulas on a scanned page, how well local models and a hosted one read them
as LaTeX, what that costs, and whether a bad reading can be told from a good
one.

### Two jobs, not one

A scan's formulas need two different things.

- **Where the formulas are.** The translated view needs only this. Its
  formula rules ([dev/translation.md](../dev/translation.md#formulas)) pick
  a formula's runs by their font (CMMI, newtx and the like), their
  characters, or their place as a sub- or superscript. The viewer hands
  the translation a scan's text repaired: each printed line is one band at
  one size, in the layer's own text font, or in Gamma's own font for
  recognized words. So no run is a sub- or superscript and no font rule
  applies. Only a run that is mostly Greek or symbols counts as math, with
  the digits and operators between two such runs. The rest of a formula
  goes to the model as garbled prose.

  If the page's formula regions were known, a run inside one would count
  as math, as on a born-digital PDF:
  - inside a sentence, it becomes a `{vN}` token, drawn as a crop of the
    page;
  - a display line whose words are all formula stays untranslated, rather
    than being translated as garbled words.

  No LaTeX is needed for that.
- **What the formulas say.** The chat, MCP, copy and search read text, and
  for them a formula's garbled words are worse than nothing. They need LaTeX.

### Pages and truth

- **Regions.** 69 pages at 300 dpi in grey from seven scans: Lounis p3–15,
  Holstein and Primakoff p2–16, Kitagawa p1–6, Feshbach p3–34, NACA p9,
  J. Phys. Radium p33 (1961) and Annalen p3 (1834). As in Stage 2, the
  marking and the transcriptions below were done by a model reading
  full-resolution tiles. Every display equation was marked from tiles of
  the page with a 100 px grid, under written rules:
  - the box holds the body and leaves out the equation number;
  - there is one entry per numbered equation, and rows that share a number
    are one entry.

  The boxes were then snapped to the ink. That gave 359 displays, 289 of
  them numbered and 71 of several rows.
- **LaTeX of the real scans.** 152 of those displays, sampled across the
  papers. They were transcribed twice under written conventions (amsmath;
  `\mathrm` for upright names; primes as `'`); the second pass never saw
  the first. The two readings agreed on 126. The other 26 were settled by
  rendering both readings beside the crop. The normalizer of the time was
  used; the final one, below, counts 130 agreements. Scored against the
  settled truth, the two passes alone are at 0.005 NED each (the token
  edit distance of Measures; 95% and 90% exact). That is a ceiling rather
  than an accuracy, since an error both passes made cannot show.
- **LaTeX of synthetic scans.** 191 display equations from eight arXiv
  physics papers (1997–2013). Each was rendered alone in a Times-like font
  at the width of a journal column, then degraded into an old bi-level
  scan:
  - ink spread or thinned, on 111 of the 191;
  - a rotation up to 0.6°;
  - blur and a noisy threshold;
  - on 67 (35%), a JBIG2-like loss of resolution that breaks thin
    strokes;
  - specks and pinholes.

  The source LaTeX is the exact truth. None of the eight papers is under a
  Creative Commons licence, so the set stays private.
- **Inline formulas.** No truth was made. They were looked at by eye on
  Lounis p6, and read and timed on the 53 that PP-DocLayoutV3 boxed at a
  score of 0.5 or more on Lounis p6 and p8.

### Measures

- **Finding.** A found box matches a marked display when the two boxes'
  intersection over their union (IoU) is at least 0.5, greedily, best
  first. The scores are:
  - recall, precision and F1, their harmonic mean;
  - how many displays a finder's boxes cover by at least half, joined or
    split;
  - how many printed equation numbers a box swallowed, which would make a
    reader read the number into the formula.
- **Reading.** NED, the normalized edit distance, is the edit distance
  between the token sequences over the truth's length (capped at 1; 0 is
  perfect). Exact is the share of identical sequences. Both sides are first
  stripped of what does not change the print:
  - sizing (`\left`, `\big`) and spacing;
  - the wrappers for upright text (`\mathrm`, `\operatorname`, `\text`);
  - braces that group nothing;
  - the spellings of an array or a matrix (`\left(\begin{array}{c}` is
    `\begin{pmatrix}`);
  - plain TeX's spellings (`{a \over b}` is `\frac{a}{b}`, `{\bf AB}` is
    `\mathbf{AB}`);
  - the order of a sub- and superscript (`x^2_M` is `x_M^2`);
  - synonyms (`\dfrac` for `\frac`, `\le` for `\leq`, `\overrightarrow`
    for `\vec`).

  Each rule came from readings that print the same as their truth but were
  scored as errors:
  - Without the array rule, GLM-OCR's habit of wrapping every multi-row
    display in `\begin{array}{l}` doubled its NED.
  - Without the script and `\over` rules, the synthetic set's NEDs were
    nearly twice as high: its arXiv sources write `x^2_M`.

  A test of pairs that must and must not come out equal guards the
  normalizer. An ink IoU between the two renders was computed as well. It
  ranked the readers inconsistently (Pix2Text first on the synthetic set),
  so it is not used.
- **Times.** Engine calls on the decoded crop or page after one warm-up, on
  the same Ryzen 9 9900X (one thread, and the runtime's default of 12) and
  an RTX 4070 SUPER.
  - Every engine was first timed on three pages or five crops.
  - The candidates were timed again on an otherwise idle machine, one at a
    time: the 13 Lounis pages, the 30 Lounis displays of the real sample,
    and the 53 inline crops.
  - The full runs' own times are not used. Renders and other runs beside
    them doubled some.

### Finding formulas

Every model finder ran through ONNX Runtime 1.32 on the CPU. A display
counts if its class is the model's display-formula class, at a score of
0.5 or more.

| Finder | Licence | Size | Recall | Precision | F1 | Covered | Numbers swallowed | s/page, one thread / 12 |
|---|---|---|---|---|---|---|---|---|
| PP-DocLayoutV3 (RapidAI's ONNX) | Apache-2.0 | 130.5 MB | 0.936 | 0.933 | 0.935 | 350/359 | 2/289 | 1.18 / 0.54 |
| the same, fp16 weights | Apache-2.0 | 65.3 MB | 0.936 | 0.933 | 0.935 | 350/359 | 2/289 | 1.18 / 0.53 |
| the same, int8 MatMul | Apache-2.0 | 108.7 MB | 0.944 | 0.939 | 0.942 | 349/359 | 3/289 | – |
| DocLayout-YOLO DocStructBench | AGPL-3.0 | 75 MB | 0.933 | 0.920 | 0.927 | 345/359 | 1/289 | 1.33 / 0.37 |
| PP-DocLayout_plus-L | Apache-2.0 | 130 MB | 0.855 | 0.895 | 0.875 | 342/359 | 1/289 | 0.81 / 0.22 |
| Tesseract's lines and layout rules | – | – | 0.552 | 0.520 | 0.535 | 223/359 | 0/289 | – |

The rules in the last row are the cheapest way: a line ending in an
equation number after a wide gap, or an indented line that reads poorly or
holds math characters.

PP-DocLayoutV3 was timed on the 13 Lounis pages on an idle machine, and the
others on three pages. The fp16 weights are no faster on a CPU. The
process's working set peaked at about 1.0 GB with either file.

- **PP-DocLayoutV3 finds nearly every display**, and alone gives what
  translation needs. It finds equation numbers as a class of their own
  (286 boxes at 0.5 against 289 numbers), and inline formulas as another.
- **By paper it is perfect on Lounis** (58 of 58, no false box) and on
  J. Phys. Radium (16 of 16). Its recall is 0.98 on Feshbach and 0.94 on
  NACA.
- **About half of Holstein's misses are the marking convention.** Its
  recall there is 0.83, 14 of 81 displays unmatched:
  - 6 are rows that share one number, which it boxes one by one; for
    translation each row is still a formula region;
  - 5 are boxes that join two displays;
  - the rest are two misses and one box too loose.
- **Nine displays of 359 were missed:**
  - the two tables of chemical formulas on the 1834 Annalen page;
  - two on Kitagawa p4, one on Feshbach p8 and one on Holstein p6;
  - three found only in part: another on Feshbach p8, and two multi-row
    displays of which it boxed some rows.
- **No box was off on its own.** Of its 360 boxes, two lay less than 60%
  on marked displays: one joined two Holstein displays and the text line
  between them, and one was only taller than its display.
- **fp16 weights halve the file and change nothing.** int8 for the
  transformer's matrix products changes little in size; the convolutions
  stay fp32.
- **Inline formulas are found as well**, though without truth to score
  them. On Lounis p6 its 28 boxes (all scored 0.3 or more; one takes in
  "H, z") cover |f⟩, S_fi, (E_i − H + iη)⁻¹, G_ee(z) = ⟨φ_e|G(z)|φ_e⟩ and
  the like:
  - It missed one |i⟩, a bare H and an ω.
  - It left the upright V and the S of "S-matrix" unboxed.
  - It boxed nothing that is not math, and left cross-references such as
    "(3.2)" alone. Across Lounis it gave 450 inline boxes at 0.3, up to 56 a page,
  and 392 at 0.5. The rapid-layout and PaddleX pipelines
  drop an inline box inside a text box, so that filter has to be off to
  keep them.

### Reading formulas

The display crops, cut with an 8 px margin, went to every reader as they
are, and every reader decoded greedily. The vision-language models ran
through llama.cpp b12359 (MIT) with what their model cards give:
- "Formula Recognition:" for GLM-OCR and PaddleOCR-VL;
- "Convert formula to LaTeX." for Granite-Docling;
- the image alone for LightOnOCR-2;
- dots.ocr's text-only prompt, "Extract the text content from this image."

LightOnOCR-2 and dots.ocr answer in Markdown, so their code fences and `$`
delimiters were stripped. LightOnOCR-2's card samples at temperature 0.2.

| Reader | Licence | Size | Real NED | Real exact | Synthetic NED | Synthetic exact | Compiles (real) | GPU s/crop |
|---|---|---|---|---|---|---|---|---|
| GLM-OCR (0.9B, Q8_0 GGUF) | MIT | 1,435 MB | **0.037** | **53%** | 0.028 | 63% | 99% | 0.49 |
| PaddleOCR-VL 1.6 (GGUF) | Apache-2.0 | 1,818 MB | 0.067 | 51% | 0.042 | 53% | 89% | 0.38 |
| LightOnOCR-2-1B (Q8_0 GGUF) | Apache-2.0 | 1,089 MB | 0.067 | 41% | 0.045 | 51% | 100% | 0.31 |
| dots.ocr (Q8_0 GGUF) | MIT | 3,239 MB | 0.103 | 39% | 0.080 | 42% | 80% | 0.47 |
| Granite-Docling 258M | Apache-2.0 | 522 MB | 0.124 | 28% | 0.217 | 15% | 91% | 0.74 |
| PP-FormulaNet plus-M (MinerU's ONNX) | Apache-2.0 | 594 MB | 0.153 | 33% | 0.105 | 30% | 91% | – |
| TexTeller 3 (PyTorch) | Apache-2.0 | 1.19 GB | 0.173 | 35% | 0.130 | 27% | 95% | – |
| Pix2Text MFR 1.5 (ONNX) | MIT | 120 MB | 0.186 | 17% | 0.116 | 33% | 95% | – |
| PP-FormulaNet plus-S (converted here) | Apache-2.0 | 334 MB | 0.252 | 16% | 0.162 | 19% | 57% | – |
| rapid_latex_ocr (pix2tex) | MIT | 179 MB | 0.375 | 10% | – | – | 72% | – |
| Claude Opus 5.5, one pass (hosted) | – | – | – | – | **0.020** | **75%** | – | – |

- **The GPU times** are over the 152 real crops.
- **The hosted model.** Six agents each read about 32 synthetic crops once,
  with no rendering or checking, as a product would ask once. Their
  transcripts show one Read of each crop and nothing else.
- **Two leaders of the published benchmark trail here.**
  - LightOnOCR-2 and dots.ocr lead olmOCR-Bench's old-scan math, at 85.6
    and 85.5 against GLM-OCR's 68.3 (each figure as published).
  - That benchmark scores whole pages of public-domain textbooks, and these
    are crops of physics journals.
  - Chandra, which leads it, was not run: its weights' licence caps the
    user's revenue and forbids competing with its maker's API. olmOCR-2
    was not run either, being a 7B model.

On the CPU, timed on an idle machine one reader at a time (a stray process
held about 3 of the 24 logical cores throughout):

| Reader | s/display (30 Lounis displays), one thread / 12 | s/inline formula (53), one thread / 12 | Peak memory |
|---|---|---|---|
| GLM-OCR | 4.6 (at most 19.7) / 1.56 | 1.05 / 0.38 | 1.9 GB (llama-server) |
| PaddleOCR-VL 1.6 | 6.5 / 1.59 | – | 2.0 GB |
| LightOnOCR-2-1B | 4.7 / 1.43 | 9.8 / 4.0 (median 1.05 / 0.35) | 2.3 GB, 3.1 GB on the inline crops |
| dots.ocr | 11.2 / 3.06 | 1.92 / 0.56 | 3.5 GB |
| PP-FormulaNet plus-M | 1.31 / 0.87 | – | 0.9 GB |

The others were timed only on five crops of Lounis p8, three of them wide
(one thread / 12):
- Granite-Docling: 20.8 / 4.83 s;
- TexTeller: 8.40 / 6.04 s;
- Pix2Text: 3.62 / 1.46 s;
- PP-FormulaNet plus-S: 0.70 / 0.39 s;
- pix2tex: 6.0 s on 12 threads over its full run (median 0.8, at most 46).

On the 30 displays the CPU readings of GLM-OCR, PaddleOCR-VL and plus-M
score the same as on the GPU (GLM-OCR 0.007, 26 exact).

By paper, on the real crops:

| Reader | Lounis | NACA | Holstein | Kitagawa | Feshbach | J. Phys. Radium | Annalen |
|---|---|---|---|---|---|---|---|
| GLM-OCR | 0.007 (26 of 30 exact) | 0.017 | 0.024 | 0.024 | 0.070 | 0.075 | 0.138 |
| PaddleOCR-VL 1.6 | 0.069 | 0.015 | 0.033 | 0.009 | 0.058 | 0.265 | 0.141 |
| LightOnOCR-2-1B | 0.058 | 0.042 | 0.077 | 0.014 | 0.082 | 0.101 | 0.132 |
| dots.ocr | 0.174 | 0.176 | 0.050 | 0.014 | 0.093 | 0.102 | 0.186 |

What the readers get wrong:
- the "fi" ligature of a subscript read as "n" (S_fn, T_n̂);
- the vector arrows of the 1961 French print, which PaddleOCR-VL reads as
  a row of their own (`\rightarrow \\ \alpha`);
- `\left| … \right|` bars invented around a crop;
- script and Fraktur capitals read as one another (Feshbach's ℋ as 𝔎, or
  as `\mathcal{J}\mathcal{C}` by LightOnOCR-2);
- bold lost or added;
- a second row dropped (LightOnOCR-2 on NACA);
- plain text for LaTeX. dots.ocr's text mode answered 10 of the 152 real
  crops in Unicode (`H = H₀ + V`, `ħω`) and two in HTML `<sub>`.

The small readers also write malformed LaTeX: plus-S's `\mathbf_{n}` and
unbalanced braces.

On inline crops GLM-OCR takes 0.08 s on the GPU and about 17 output tokens.
By eye, of the first 24 on Lounis p6:
- 16 are exact;
- 5 more are right but took in a neighbour's comma or bracket from the
  crop's margin;
- 3 are misread: the fi ligature (S_fn) and two with invented bars.

LightOnOCR-2 ran away on 9 of the 53 inline crops, writing "H_0, H_1, H_2,
…" up to its 1,024-token cap. That accounts for its inline mean of 9.8 s
against a median of 1.05 s.

### Telling a bad reading

A reading is bad here when its NED is over 0.25. Several signals a product
could compute without the truth were tried for whether they rank good
readings (NED ≤ 0.10) above bad ones. The measure is the AUC, the chance
that a good reading scores above a bad one; 0.5 is chance.

| Signal | AUC, GLM-OCR / PaddleOCR-VL / plus-M (real) | What it is |
|---|---|---|
| Agreement with another reader | 0.73 / 0.89 / 0.98 | 1 − the token edit distance to the nearest of the other nine local readers' readings |
| The render against the crop | 0.57 / 0.63 / 0.81 | ink IoU of the reading's render, scaled to the crop's ink |
| Aspect ratio | 0.40 / 0.61 / 0.72 | the two ink boxes' width over height |
| Compiles | 0.50 / 0.51 / 0.57 | – |
| A repeated 4-gram | 0.37 / 0.36 / 0.65 | the share of tokens in a loop |

- **On the real crops, agreement ranks the best readers' readings best**:
  0.73 for GLM-OCR, 0.89 for PaddleOCR-VL. The render and the aspect ratio
  help only the weaker readers, plus-M among them (0.81). A render looks
  too unlike a 1950s typeface for the comparison with the crop to tell
  much, and nearly every reading compiles.
- **On the synthetic set the best readers are too rarely wrong to rank.**
  GLM-OCR has one bad reading of 191, Claude two and PaddleOCR-VL three.
  On the real set GLM-OCR has six, so its AUCs also rest on few cases.
- **Loops are rare, and a repetition rule flags good readings.**
  - GLM-OCR and PaddleOCR-VL never looped within 1,024 tokens.
  - LightOnOCR-2 looped once on a display and on 9 of the 53 inline crops.
  - dots.ocr looped once.
  - Granite-Docling ran 7 of the 343 crops to the cap.
  - PP-FormulaNet plus-M looped on 4 real crops (24–30 s each), and pix2tex
    on wide crops (up to 46 s).

  A formula repeats itself (|φ_e⟩ … |φ_e⟩), so the 18 GLM-OCR readings with
  half their tokens in a repeated 4-gram average 0.02 NED. A cap on the
  length of a reading, relative to its crop's width, stops a loop without
  judging the rest.
- **As a guard, a second reader keeps what both say.** GLM-OCR checked by
  PaddleOCR-VL, both sets together (343 crops, 7 of GLM-OCR's readings
  bad):

  | Keep where the two differ by at most | Kept | NED of the kept | Bad readings kept | NED of the flagged |
  |---|---|---|---|---|
  | nothing | 49% | 0.011 | 0 | 0.052 |
  | 5% | 71% | 0.017 | 1 | 0.069 |
  | 10% | 84% | 0.023 | 3 | 0.079 |
  | 20% | 94% | 0.029 | 4 | 0.088 |
  | everything | 100% | 0.032 | 7 | – |

  The guard doubles the time spent reading. At a 5% difference it keeps
  71% of the readings at about half the error (0.017 against 0.032), and
  flags 6 of the 7 bad ones. LightOnOCR-2 as the checker does about as well
  (71% kept, 0.017).

### A hosted model's cost

No API key was at hand, so token counts are estimates from the pricing
documentation (November 2026), not counted.
- **An image** costs about one token per 28 × 28 pixels. A larger one is
  scaled down to at most 2,576 px on the long side and about 4,800 tokens
  (some 3.75 megapixels), whichever comes first. A 300-dpi page reaches
  that cap, so a whole page is about 4,800. The 152 display crops come to
  150 image tokens on average (median 80, at most 1,300), and an inline
  crop to about 9 (at most 36).
- **A reading** writes about 80–110 tokens for a display and 17 for an
  inline formula, the local models' means, before any thinking. Claude
  Opus 5.5 cannot turn thinking off, only lower its effort. It costs $4 in
  and $20 out per million tokens; Sonnet 5.5 costs $2 and $10, and Haiku
  5.5 $0.10 and $0.50.
- **Per formula**, with about 50 tokens of prompt, Opus 5.5 comes to about
  $0.003 a display and $0.0006 an inline formula, Sonnet 5.5 to half that.
  Haiku 5.5 comes to a fortieth of Opus, but its accuracy on these crops
  was not measured.
- **Per paper**, Lounis's 58 displays come to $0.12–0.16 with Opus 5.5,
  and its 450 inline boxes (at a score of 0.3; 392 at 0.5) to about $0.27.
- **Per page**, a page sent whole costs about $0.02 in and, with its text
  written out, $0.04–0.06 out.
- **The pictures leave the server.** The crops or pages go to whoever the
  person's AI provider is.

### Whole pages with the small models

Could one of the small vision models replace Tesseract for a scan's text,
not only its formulas? Four were run on Stage 2's 15 truth pages and scored
as Stage 2 scores an engine (text lines; display math apart):
- GLM-OCR;
- PaddleOCR-VL 1.6;
- LightOnOCR-2-1B;
- Granite-Docling 258M.

They used the same llama.cpp b12359 builds as for the formulas. Larger
models (dots.ocr, olmOCR-2 at 7B) and hosted services were left out.

Each was run two ways where its makers do both:
- **The whole page**, with the model's page prompt.
- **By region**, as GLM-OCR's SDK and PaddleOCR-VL's pipeline run it.
  PP-DocLayoutV3 finds the page's blocks in the model's reading order, and
  each block's crop is read with its prompt (text, formula or table).

Their Markdown, HTML, DocTags and LaTeX were turned into the truth's flat
style before scoring (`stage3/flatten_vlm.py`, with its own tests). A
difference of convention may remain, as a small cost to the models.

| Engine | Errors | Recall | Precision | Continuity | s/page, GPU | s/page, CPU (estimated), 12 threads / one |
|---|---|---|---|---|---|---|
| Tesseract 5.5, fast, eng+fra+deu | 1.46% | 96.1% | 91.7% | 94.1% | – | 2.5–3.7 on one thread, measured (CLI, tesserocr) |
| PaddleOCR-VL 1.6, by region | **0.86%** | 96.0% | 93.8% | 95.5% | 5.5 | 44 / 190 |
| LightOnOCR-2-1B, whole page | 1.03% | 96.2% | 91.3% | 95.7% | 5.0 | 32 / 100 |
| GLM-OCR, by region | 1.25% | 94.3% | 84.3% | 96.0% | 6.0 | 46 / 130 |
| PaddleOCR-VL 1.6, whole page | 1.38% | 95.2% | 92.5% | 95.5% | 3.1 | 25 / 110 |
| Granite-Docling 258M, whole page | 1.78% | 93.7% | 82.0% | 95.0% | 3.3 | 27 / 110 |
| GLM-OCR, whole page | 3.33% | 92.2% | 91.1% | 95.7% | 5.2 | 38 / 107 |

What the table shows:
- **The poorest pages gain most.** PaddleOCR-VL by region takes Lounis p8's
  text from 4.62% to 1.37%, Holstein p8's from 4.84% to 3.06% and J. Phys.
  Radium p12 from 2.48% to 1.56%.
- **A few clean pages lose.** Annalen p10 goes from 0.71% to 1.52%, and
  Siegman p99 from 0.51% to 0.67%.
- **Precision.** GLM-OCR and Granite-Docling read words the page does not
  have. Their precision is 84% and 82%.
- **GLM-OCR depends on how it is run.** It reads well by region and poorly
  whole.
- **No run looped.** None stopped at its 8,192-token cap.

GPU times are on an RTX 4070 SUPER.

The CPU times are estimates, not timings. The machine was busy with other
work during this stage, at 99–100% of its 24 threads.
- **The rates** come from Stage 3's crop timings on the same CPU while it
  was idle. llama-server's own split gives, on 12 threads, prefill of
  185–244 tokens a second and decode of 55–65.
- **The page counts** are the GPU runs' token counts. A page writes about
  1,100 tokens, and by region it reads about 5,000 (1,000–2,000 whole).
- **One thread** is the 12-thread figure times the ratio of the two
  timings on the same crops: 2.8 to 4.3, depending on the model.
- **The figures are extrapolated.** The crops held at most about 600 input
  tokens, and a page holds 1,000–5,000. Take them to within about ±40%.
- **Writing the text costs about 20 s a page** on 12 threads, whatever the
  model or the mode.
- **Memory** is about 2 GB per model server, as measured in Stage 3.

Against Tesseract's 2.5–3.7 s a page on one thread:
- **One thread each:** a page takes 27–76 times as long.
- **The model on 12 threads:** still 7–18 times as long.

For a 20-page paper read by PaddleOCR-VL by region, that is about 15
minutes on all 12 cores of this desktop, or an hour on one core.

**None of the four gives word boxes.** They give text per block. The viewer's
text layer, selection, highlights and search hits need each word's box. A
model's text could only correct Tesseract's words block by block, Tesseract
keeping the boxes; that pairing was neither built nor measured.

So Tesseract stays the engine for a scan's words.

### What the numbers say

- **Finding comes first, and it is solved.** PP-DocLayoutV3, under
  Apache-2.0 and with fp16 weights of 65.3 MB:
  - covered 97.5% of the displays by at least half (recall 0.936 at IoU
    0.5), all of Lounis's;
  - found the inline formulas too;
  - costs about a second a page on one core, less than Tesseract's 1.8–3.7
    s a page in Stage 2.
- **For translation, that is enough.** A formula region's words become a
  formula token, drawn as a crop of the page, and a display stays
  untranslated rather than being translated as garbled words. No reader's
  LaTeX is involved, so no reading error can reach the translated view.
- **GLM-OCR reads best of the ten local readers, and comes closest to a
  frontier model's single pass.**
  - On the synthetic scans it is at 0.028 NED and 63% exact, against Opus
    5.5's 0.020 and 75%.
  - On the real ones it is at 0.037; the next, PaddleOCR-VL and
    LightOnOCR-2, are at 0.067.
  - On Lounis it read 26 of 30 displays exactly.
  - It is a 1.4 GB download, MIT licensed, and runs through llama.cpp in
    1.9 GB of memory.
  - Per display: 4.6 s on one core, 1.6 s on 12 threads.
  - Per inline formula: 1.05 s and 0.38 s.
  - Lounis's 58 displays take 4.5 minutes on one core, and its 450 inline
    boxes (392 at a score of 0.5) another 7–8. A NAS core is perhaps two to
    three times slower (not measured).
- **A published benchmark did not predict the order.** LightOnOCR-2 and
  dots.ocr lead old-scan math on olmOCR-Bench, as published. Here they
  make twice and three times GLM-OCR's error on the real scans, and
  LightOnOCR-2 runs away on inline crops.
- **The dedicated formula readers are well behind.** PP-FormulaNet,
  Pix2Text and TexTeller make four to seven times GLM-OCR's error on both
  sets, and pix2tex ten times on the real scans.
- **There is no cheap guard.** A second reader's agreement catches most
  bad readings: checked by PaddleOCR-VL, 6 of GLM-OCR's 7 over both sets.
  It doubles the cost. GLM-OCR is rarely wrong to begin with, once in the
  191 synthetic scans, and the page's crop stays the authority wherever a
  person sees the formula.
- **For a page's text, Tesseract stays.**
  - The best small model, PaddleOCR-VL read by region, makes about 40%
    fewer errors (0.86% against 1.46%).
  - It takes 27–76 times Tesseract's time on a core, estimated.
  - It gives no word boxes, which everything that draws or selects text
    needs.
  - Its affordable use is the formulas alone, where it is a few seconds a
    page.

### A design that follows

Not built.
- **Regions with the recognized text.** The server's run (`pdf_ocr.run`)
  draws every scan page at 300 dpi for PP-DocLayoutV3.
  - That includes the pages it does not recognize. Today, once the sample
    of three layered pages reads well, the run skips the file's other
    layered pages; those still need their regions.
  - It stores the page's display and inline boxes beside its words, or
    beside nothing for a page that keeps its layer.
  - A change to the boxes takes a new stamp, so the readers' caches let go
    of the old view.
  - The number boxes are kept too, for a later use such as linking "(3.2)"
    to its equation.
  - The finder needs ONNX Runtime and the fp16 model, 65.3 MB, too large
    for the repo, so it would be fetched once or put in the Docker image.
    ONNX Runtime 1.32's wheels are 27.8 MB on Linux x86_64, 23.2 MB on
    arm64, 16.7 MB on Windows and 22.5 MB on Apple Silicon (about 51 MB
    installed on Windows). There is none for Intel Macs, and none for the
    apps on PyPI.
  - The window's run (`ocr-tab`, for the apps and servers without the
    engine) finds no regions until onnxruntime-web is measured.
- **The viewer marks the words.** `pageText` flags an item whose box lies
  mostly inside a display or inline region (`math: true`), `textRuns`
  carries the flag onto its run, and `pdfMath.isMathRun` takes it before
  its font rules. The formula becomes a token, and a line that is all
  formula stays untranslated.
  - The overlay's crop (`TransMath`) has to be cut at the region's box, not
    at the words' box (`spanBox`). The words' box is the repaired line band,
    which Stage 1 found off the ink on some layers (Feshbach's baseline,
    NACA's small type, skew).
  - An equation number stays text. It lies outside the display's box (the
    finder swallowed 2 of 289), and `pdfMath` takes no run of digits and
    brackets set apart from a formula.
- **LaTeX as an option.** A run of its own (a job kind beside `ocr`) sends
  each display's crop, and optionally each inline one's, to a reader:
  - GLM-OCR through llama.cpp's server where the person installed it, a
    download of about 1.5 GB with the engine (25 MB zipped for the CPU),
    using 1.9 GB of memory while it reads;
  - or the AI provider the person set up.

  It keeps `{box, latex, reader}` per region.
- **Where the LaTeX goes.** `pdf_text`'s readers give a region's LaTeX in
  place of its words, `$$…$$` for a display and `$…$` inline, so the chat,
  MCP and search read the formula. Copying a selection that covers a whole
  formula copies its LaTeX. The translated view keeps drawing crops.
- **Guards.** Cap a reading's length by its crop's width, so a loop ends
  early. Where both readers are installed, keep a reading the second one
  agrees with and mark the rest as uncertain to the chat. In the viewer,
  the crop is always what is shown.

### Not measured

- **Other hardware**: a NAS, onnxruntime-web in a window, the apps.
- **Inline readings against a truth.**
- **A hosted model on the real scans.** Their truth was made by the same
  kind of model, which would make the comparison circular.
- **Where a whole-page reading's LaTeX sits.** It gives LaTeX without a
  separate finder (Granite-Docling read Lounis p8 whole in 4 s on the GPU).
  Its text was scored ([above](#whole-pages-with-the-small-models)); where
  its formulas sit on the page was not.
- **The small models' page reading on a CPU.** It was estimated from crop
  timings, on a busy machine. The runner for an idle one is in the harness.
- **Tesseract's boxes with a model's text, block by block.**
- **GLM-OCR at a smaller quantization than Q8_0.**
- **Fraktur formulas.**

## Still open

### Stage 2: what is left to build

Built ([dev/documents.md](../dev/documents.md#recognized-text)): the
server's engine from the Document menu; tesseract.js in the window where
the server has none (the Android and iPad apps, macOS before 15), the
server judging and keeping the words it sends; automatic runs of a scan
uploaded with no text at all, on a server with the engine, one at a time
and giving way to a run someone asks for; Forget the recognized text; and
a socket message, so the open tabs that show the file read the new text.
Left:

- **More languages** than English, French and German.
- **Copies elsewhere**: carrying recognized text to mirrors, clones and
  publishing.
- **Automatic runs in the window**, and for a scan whose layer is poor:
  the automatic runs take only scans with no text, on the server.
- **The window on devices**: its speed and memory on a mid-range phone and
  a tablet, the screen kept on past Auto-Lock, and a run sent to the
  background and back ([dev/android.md](../dev/android.md#device-checks),
  [dev/ipad.md](../dev/ipad.md#device-checks)).

The threshold for replacing a layer rests on 12 pages and should be
checked on more.

### Stage 3: formulas on scanned pages

Measured, with a design ([Stage 3](#stage-3-formulas-on-scanned-pages)).
Nothing of it is built. Left:

- **Formula regions with the recognized text**: PP-DocLayoutV3 in the
  server's run, the regions stored per page, and the viewer marking the
  words inside them as math for the translated view. This is the part that
  fixes translation, and the one to build first.
- **LaTeX as an option**: GLM-OCR through llama.cpp, or the person's AI
  provider, for the chat, MCP, copy and search.
- **What was not measured**: a NAS, the window and the apps, inline
  readings against a truth, and a hosted model on the real scans.
- **A page's text by a small model**: decided against for now
  ([Whole pages with the small models](#whole-pages-with-the-small-models)).
  If it comes back, it would be Tesseract's word boxes with PaddleOCR-VL's
  text by region, as an opt-in run where a GPU or time allows.

The field's known failures on old print were looked for:
- characters repeated until the output runs out, on 19th-century
  newspapers ([Bourne 2025](https://arxiv.org/html/2502.14901));
- Nougat looping on scanned pages
  ([Blecher et al.](https://arxiv.org/html/2308.13418v1));
- letters and diacritics added that the page does not have
  ([Levchenko 2025](https://arxiv.org/html/2510.06743)).

On crops, GLM-OCR and PaddleOCR-VL never looped. The others that did:
- LightOnOCR-2, on 9 of 53 inline crops;
- Granite-Docling, on 7 of 343 crops;
- PP-FormulaNet plus-M, dots.ocr and pix2tex, on a few.

Invented content showed as small additions: bars around a formula, a
`\boxed{}` from TexTeller.

### Smaller items

- `title_guess` on scans read from their own layer: it reads
  `get_text("dict")` sizes, which on the Lounis layer are ink heights, and
  picks the words with capitals and ascenders. That paper only works
  because its first page is HAL's born-digital cover. Feeding it the
  regrouped rows with one size per line would fix it. A recognized first
  page is read from its recognized lines instead.
- Layers that neither detector sees, identically and by design: an OCR
  layer drawn visibly and then painted over by the image, and text hidden by
  fill alpha 0 (`ca=0`).
- Right-to-left and vertical OCR text. The line rule breaks a line where x
  runs backwards, so an RTL line would become one line per word.
- Browser timings of the operator list. Everything was timed in Node with
  pdf.js's fake worker; in the browser the image decoding competes with
  rendering in the worker.

## Rebuilding the study

The harness for the three stages is [tools/ocr-study](../../tools/ocr-study/README.md).
The corpus, the transcribed truth and the synthetic equations cannot be
committed: most of the pages are under copyright, and so are the arXiv
papers the equations come from. To rebuild them:

1. **Corpus.** The public scans: HAL jpa-00247657; the Internet Archive
   items `bstj27-3-379`, `journal-de-physique-1963_1961-03_22_3`,
   `sim_physical-review_1913-09_2_3` and
   `sim_annalen-der-physik_1834_31_22`; NASA NTRS 19930091059. The APS,
   Annals of Physics, SPIE and Siegman files are copyrighted; any library
   copy from the same producers serves. Add every born-digital PDF at hand
   as negatives.
2. **Characterize** each page with PyMuPDF: image coverage, characters by
   render mode, fonts, `Tz` values, words per item, per-line size spread,
   lines sharing a baseline, box over ink width. That table is what showed
   the three layer kinds.
3. **Dump** each sampled page's TextContent with pdf.js 4.4.168's legacy
   build in Node 24 (`useSystemFonts: true`, as the viewer).
4. **Build the truth** from the rendered scan as described above, and look
   at overlays of a page per producer before trusting it.
5. **Evaluate** each candidate on structured clones of the TextContent with
   the measures above, against the baseline and an oracle built from the
   truth. The oracle's scores are the check that the evaluator can tell a
   good layer from a bad one.
6. **Detector**: the prescreen and the confirm on every page, the confirm
   also on a random sample of negatives, the Python twin on every page, and
   the two verdicts compared page by page.
7. **Stage 2 pages**: render each at 300 dpi in grey with PyMuPDF, and keep
   the layer's words (`get_text("words")`, stream order) as one more engine.
8. **Engines**: each writes, per page, lines of words with boxes in points
   and its recognition time.
   - Tesseract's TSV output, or tesserocr's result iterator, with a line
     started at `RIL.TEXTLINE`;
   - PyMuPDF's `get_textpage_ocr(full=True)`;
   - RapidOCR with `return_word_box`;
   - OnnxTR's export;
   - tesseract.js with `blocks: true`.

   Run them one at a time, then again with one thread
   (`OMP_THREAD_LIMIT=1`, or ONNX Runtime's `intra_op_num_threads`).
9. **Truth**: cut each page into full-resolution tiles by column, and
   transcribe it twice under written rules: once proofreading a draft that
   shows other engines' readings, once from the tiles alone. Settle each
   disagreement from a crop of its line.
10. **Score** with the measures of Stage 2. The best-substring edit distance
    is a row-vectorized dynamic program; a pure-Python one is too slow for
    page-length texts.
11. **Stage 3 pages**: render the 69 pages at 300 dpi in grey. Mark every
    display equation from tiles with a labelled 100 px grid, under written
    rules, then snap each box to the ink.
12. **Stage 3 truth**:
    - sample the displays and transcribe each twice under written LaTeX
      conventions, settling disagreements by rendering both readings
      beside the crop;
    - build the synthetic set from arXiv sources: one equation a page,
      rendered at column width and degraded with a recorded seed.
13. **Finders and readers**:
    - the finders through ONNX Runtime;
    - the dedicated readers through ONNX Runtime or PyTorch;
    - the vision-language models through llama.cpp's server with the
      prompts from their model cards.

    Run them one at a time, then again with one thread. Time the CPU on an
    idle machine: renders and other runs beside it doubled some times.
14. **Score** the regions by IoU and coverage. Score the LaTeX by token edit
    distance after the normalization above. Test the normalizer on pairs
    that must and must not come out equal before trusting a score.

Stage 3's renders take about 20 minutes with six pdflatex at a time. The
readers take about an hour on the CPU, mostly pix2tex and TexTeller, and
about 10 minutes on the GPU.
