# Scanned PDFs: telling an OCR layer apart, repairing its text, and what real OCR would add

A study from November 2026 of what Gamma does with a scanned paper. Such a
file is a picture of each page with an invisible OCR text layer drawn over
it, or with no text at all. The question was why selection, highlights,
search and the translated view were so poor on scans, and what to build. The
note records what was measured, the three repairs tried and why one was
picked. A second stage, real OCR, was then measured against a transcribed
truth: which engine, what it costs on each platform, when it beats a scan's
own layer, and a design that follows. A third, a vision model for scanned
equations, is left open. The mechanics as built are in
[dev/pdf_loading.md](../dev/pdf_loading.md) (the text layer),
[dev/documents.md](../dev/documents.md#text-extraction) (the server's text)
and [dev/translation.md](../dev/translation.md) (the OCR flag). The code is
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
- **No engine reads display equations.** They stay with Stage 3.

### Where each engine can run

| Platform | Tesseract with its layout | Other options |
|---|---|---|
| Docker, amd64 and arm64 | The tesserocr 2.13.0 wheel (MIT; 5.6 and 5.3 MB, Tesseract 5.5.4 and Leptonica inside) plus the models: fast eng 4.1 MB, fra 1.1, deu 1.5. No apt package is needed. Debian's `tesseract-ocr` would add 69 packages, about 100 MiB. | MuPDF's Tesseract, already in PyMuPDF 1.28.2: only the models are needed, but the layout is lost. |
| Desktop, Windows and macOS | tesserocr wheels: 23 MB on Windows, 3.6–4.1 MB on macOS. A PyInstaller freeze was not tried. | Windows.Media.Ocr reads only the languages Windows has installed. With no French pack, "garçon déjà prêt" came back as "garcon déjå prét". |
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

### A design that follows

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

### Not measured

- A NAS, a phone, or tesseract.js in a browser rather than Node.
- tesserocr frozen by PyInstaller, and MuPDF's Tesseract on Linux and
  macOS (seen only as strings in the wheels).
- Apple Vision and ML Kit accuracy.
- Fraktur: the Annalen page is set in Antiqua.
- A render above 300 dpi, or at the scan's own resolution.

## Still open

### Stage 2: building it

The design above, from the engine to the highlights. Its threshold for
replacing a layer rests on 12 pages and should be checked on more.

### Stage 3: a vision model for scanned math

Every OCR engine measured above got 47–57% of the characters of display
equations wrong, and the formula rules ([dev/translation.md](../dev/translation.md))
read fonts a scan does not have. Translating a scanned equation-heavy paper
well probably means sending the page picture to a vision model with the
text, through the AI provider the person has set up.

The open questions:
- how a model's LaTeX is placed on the page;
- what it costs per page;
- how to catch what such models are known to get wrong on old print:
  - characters repeated until the output runs out, on 19th-century
    newspapers ([Bourne 2025](https://arxiv.org/html/2502.14901));
  - letters and diacritics added that the page does not have, on
    18th-century print ([Levchenko 2025](https://arxiv.org/html/2510.06743));
  - Nougat looping on scanned pages
    ([Blecher et al.](https://arxiv.org/html/2308.13418v1)).

Not designed or measured.

### Smaller items

- `title_guess` on scans: it reads `get_text("dict")` sizes, which on the
  Lounis layer are ink heights, and picks the words with capitals and
  ascenders. That paper only works because its first page is HAL's
  born-digital cover. Feeding it the regrouped rows with one size per line
  would fix it.
- Layers that neither detector sees, identically and by design: an OCR
  layer drawn visibly and then painted over by the image, and text hidden by
  fill alpha 0 (`ca=0`).
- Right-to-left and vertical OCR text. The line rule breaks a line where x
  runs backwards, so an RTL line would become one line per word.
- Browser timings of the operator list. Everything was timed in Node with
  pdf.js's fake worker; in the browser the image decoding competes with
  rendering in the worker.

## Rebuilding the study

The harness for both stages is [tools/ocr-study](../../tools/ocr-study/README.md).
The corpus and the transcribed truth cannot be committed, because most of
the pages are under copyright. To rebuild them:

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

The 46-page evaluation takes about 1.2 s, the truth 13 s. Stage 2's engines
take about 40 minutes over the 15 pages, its scoring about two minutes per
engine.
