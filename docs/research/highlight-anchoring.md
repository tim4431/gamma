# Finding highlights again in a replaced PDF

Findings of October 2026. The question: a page's PDF is replaced by
another version of the same paper (a TeX compile, the published version in
place of the arXiv one) and every highlight sits where its text was, not
where it is. How do annotation systems keep highlights anchored when the
document under them changes, and what should Gamma do? The design that
came of it is built: [dev/highlight_relocation.md](../dev/highlight_relocation.md).

## What others do

**Hypothesis** anchors a web annotation by three selectors of the W3C Web
Annotation model: a `RangeSelector` (the DOM range), a
`TextPositionSelector` (character offsets into the page's text) and a
`TextQuoteSelector` (the exact text with a prefix and a suffix of context).
Anchoring tries them in that order: the range when the document has not
changed, the position next, and when both fail a fuzzy search for the
prefix and suffix around the expected position, the text between them
checked against the saved quote within an acceptance threshold. The quote
selector is the check on every other strategy; the position selector
resolves ambiguity and makes the common case fast, since a fuzzy full-text
search of the document is slow. An annotation nothing anchors is kept as an
*orphan*, listed apart, never deleted. The client's older
`dom-anchor-text-quote` module is public; its current equivalent is not
separately published.

**Zotero** stores a PDF annotation's position as the page index and
rectangles in that page's space, in its own database. Its documentation
warns that adding, deleting, reordering or rotating pages puts annotations
on the wrong page or in the wrong place, and the forum's advice for a new
version of a file is to keep the old file to swap back; one user rewrote
the new file's media box to the old one's so the coordinates would hold.
There is no re-anchoring.

**Proposals elsewhere** (GitHub issues of reading and note apps) converge
on one shape: store the highlighted text with a window of context, try the
cached rectangle first, re-derive the rectangle from the quote when the
text at the cached place no longer matches, allow a few inserted or changed
words, and keep what cannot be found in an orphan list marked "not found in
this version". None had an implementation to compare against. Microsoft
Research's 2000 technical report on robust annotation positioning made the
same case for text anchors with context over coordinates, with a
confidence score when the text changed.

## What Gamma had

- A highlight is a block with `pdf_position` (the page, the page's size
  when it was measured, rectangles in that frame) and a `quote`, the
  viewer's text of the selection ([dev/api.md](../dev/api.md) "The
  highlight shape"). Nothing records the context around the quote.
- `PUT /pages/{id}/attachment` replaced the file and left every position as
  it was; the documentation said so.
- Two text matchers existed: the browser's citation matcher
  (`pdf/pdfCitation.js`, `pdf/fuzzyCitation.js`: a quote found on one page
  of pdf.js's text layer, word by word with a bounded budget, protected
  numbers and symbols) and, on the server, PyMuPDF's glyph boxes
  (`pdf_text.GlyphPages`) with `quote_under`, which reads the text under an
  imported annotation's quads.
- The export's mappers (`pdf_export.viewer_point_to_pdf`,
  `pdf_point_to_viewer`) already related a stored rectangle to PDF user
  space through the page's crop box and rotation.

## The design, and why

**On the server, from both files.** At the moment of a replacement the old
file is still stored (the orphan bookkeeping keeps it 30 days), so the
server can read the exact text under each highlight's rectangles and its
surroundings from the old file — a quote selector with prefix and suffix
computed after the fact — and look for it in the new file with the same
extraction engine on both sides. The browser could do neither: it has one
file open, would need every page's text layer rendered, and is not there
when a build script replaces the file through the API. The run is a
background job (the tasks tray) so a book's worth of highlights does not
hold a request.

**Each highlight names the file it was measured on.** The first design
read every highlight from the page's previous file, the one the
replacement took away. That holds only while every run writes. A run
stopped, failed, refused because the account's jobs were full, or
overtaken by the next replacement before it wrote leaves highlights whose
rectangles belong to the file before; a highlight left lost keeps the old
file's rectangles through every later version; and after a wrong file put
up and replaced again, every highlight would be read from the wrong file,
whose text under the old rectangles is something else. So a position
records the stored file its rectangles were measured on
(`pdf_position.doc_id`), written by the replacement in its own
transaction and only when that file is no longer the page's. Whatever
writes positions on the current file (the viewer, the imports, a move by
hand) writes none and needs to know nothing of it. A safety net covers a
position that names no file but was measured on an older one (a PDF
replaced before the job existed): when the text read under its rectangles
does not read like its stored quote, the quote is looked for instead.

**Text first, exact then approximate, every page.** An exact occurrence of
the folded passage anywhere in the new file is the normal case for a
recompiled paper; the word-by-word alignment covers copyediting between a
preprint and the publisher's version. The browser matcher's rules were
taken over where they fit: a budget of edits per word, cheap costs for
punctuation and near-spellings, no edits to numbers, single letters,
symbols or negations beyond the prose punctuation around them ("Fig. 3,"
for "Fig. 3.", "(4)" for "4", a copyeditor's comma after "x, y"; a prime
or a factorial is the symbol's own). The browser matcher locks that
punctuation as well; here it may change, for a quarter, because the word
itself must still match exactly, so the change cannot put a highlight on
another statement, while refusing it marks lost a highlight whose words
are all still there. The budget is one edit per six words from four
words on (the browser's is one per eight from eight words on; highlights
are shorter than citations and the context breaks ties).

**Ranking by context first**, rather than refusing an ambiguous passage: a
highlight of "Fig. 3" has several places in any paper, and the forty
characters around the old one pick the right one when the old file is
there. A place whose surroundings clearly read like the old ones outranks
one with fewer edits, and the word-by-word search runs also when no exact
copy stands in its old surroundings: a sentence copyedited in place is the
same highlight, a verbatim copy of it in the conclusion is not. "Clearly"
is a mean likeness of 0.6, from what the windows score: unrelated
40-character windows 0.30 at the median and 0.475 at the 99th percentile,
lightly edited ones 0.83 to 0.90. Then the fewest edits, the closer
surroundings, the nearest page, and the place nearest the old rectangles,
measured as fractions of each page: a passage's place in the page's text
against the old rectangles' height down the page put a highlight at the
top of a two-column page's right column on the copy at the foot of the
left one. Without the old file, page and place decide. A wrong place is
visible and moved by hand; a refusal would leave more work.

**Exact hits on word edges.** A short passage found exactly inside a
longer word ("ion" in "motion", "Fig. 1" in "Fig. 12") is not the
passage; the old file's context shows when the selection itself began or
ended inside a word, and only then is such a hit taken.

**Across a page break.** A new version reflows its pages, and a passage
that sat within one page may straddle a break with the footer, footnotes
and the next page's running head between its halves: numbers the
word-by-word search may not skip. The halves are aligned apart, the head
against the end of one page and the tail against the start of the next,
and the highlight goes to the page with the larger part, covering it,
since a highlight has one page. A passage of fewer than four words is not
joined: with a budget of one edit, a word found near some page's end and
another dropped would place almost every lost short highlight at a page
break.

**Piece by piece where the old file skipped text.** A highlight from a
column's foot to the next column's top skips what the content stream
draws between them, often a figure's caption or a footnote. The needle
leaves that out, so the passage reads as a selection does, but the
unchanged page still has the caption between its two halves, and the
caption's words are far more than the edit budget. So such a passage is
also kept as its pieces, and when it is nowhere whole the pieces are found
one after another, each beginning within the old file's skip and a little
more of the one before. Each piece keeps the rules of a passage (exact on
word edges, word by word within its own budget from four words on, the
pieces together within the whole passage's), since a short piece found at
some distance would otherwise land anywhere. A highlight read from the
page's own file over its quote needs no search at all: it is where it
belongs, whatever the search would make of it.

**Later: a confidence instead of frozen words.** Freezing numbers lost what
a new version had only renumbered: on *Attention Is All You Need*, arXiv
v1 against v7 with every third sentence highlighted, the edit budget
placed 49 of 76 and lost 27, mostly passages whose citations v7
renumbered. So no word is frozen and there is no budget: each place has a
confidence, how alike its text and the passage read (difflib's ratio, 1.0
only for an exact copy), ranked with a fifth of its context's likeness
added; the best is applied from 0.8, a passage under twelve characters
only when exact. Both thresholds were set by hand, not calibrated, and
each account may set its own. A rough match is drawn dashed and keeps
where the highlight was before, which a review card shows with the words
that changed, until the person accepts or moves it, so a generous
threshold costs a highlight to check rather than a silent wrong one. On
the same pair: 72 of 76 on their text, 49 exact and 23 rough (0.95 to
0.99, mostly renumbered citations), none wrong, 4 lost, each a passage
gone from v7. The budget, the frozen words, the four-word minimum and the
context-first ranking above no longer hold; the measurements below are of
the edit-budget matcher.

**Line numbers.** A review copy (lineno) prints a number beside every
line, which MuPDF reads as a line of its own. Lines made of one to five
digits alone are left out of both files' text, so the numbered copy and
the camera-ready one read alike; a page number on its own line goes the
same way.

**A `lost` flag, not a deletion and not silence.** A highlight whose
passage is gone keeps its notes and its old rectangles, is drawn hollow
where it was and listed hollow in the notes, and offers a move to a
selection. This is Hypothesis's orphan, kept in place rather than in a
separate list, since the page is the place the person looks.

**Areas by what is inside them.** An area highlight has no quote, but the
text its box covers (a caption, axis labels) is a passage like any other,
and a figure embedded again by pdfTeX is the same image data, which MuPDF
digests. Both place the box by offsets around the thing found. An area
over nothing recognisable follows the page's other highlights when they all
moved the same way, and says so (`approx`). Otherwise it stays, and says so
too: one measured on another file is an `approx` mark where it was,
keeping the `doc_id` of the file its rectangles belong to, rather than a
silent "unchanged" that a later run would read from the wrong file.

**Declined:** moving the viewer's whole highlight model to selectors stored
at creation time (a large change to a working shape; the context is
computed from the old file instead, see the open item below); pixel
matching of rendered regions for figures (slow, and the embedded image's
digest covers the common case); automatic undo of a run (the ops are the
server's; a move by hand is the correction); refusing ambiguous passages.

## Still open

- Storing a passage's prefix and suffix with the highlight when it is made,
  so a run without the file a highlight was measured on (purged 30 days
  after it was replaced, or a page shown from a URL alone) has context too.
  The folding and search would stay as they are; `normalize_quote` would
  read the stored context.
- A passage that the new file splits where the old one did not, or reads in
  another order (two interleaved columns): finding its parts without the
  old file's pieces to go by. The page break, and the pieces the old file
  read apart, are built.
- Ink groups and text boxes riding along with the page's highlights.
- Calibrating the rough match's thresholds (0.8, twelve characters) on
  real version pairs.
- Measurements on a long book and on an arXiv–publisher pair; the ones
  below are of a synthetic paper.

## Measurements

October 2026, on a development machine, a synthetic edited paper (21 pages
before, 22 after) with 210 highlights:

- Matching took 5.5 s, about 2 s of it reading the pages; 199 s before the
  search skipped the pages and windows that lack too many of a passage's
  words, cached its word costs and tried difflib's cheap bounds first. It
  moved 150 highlights and found 60 lost (144 and 66 before).
- A page's subtree request while a run matched: 42 ms at the median, 99 ms
  at the 90th percentile, 230 ms at most, against 6 ms idle; 3.6 s at the
  median before the run napped (pure Python holds the GIL, and a request
  waits out the switch interval at every handoff). The naps cost up to
  about 17% of a run's time when nothing else runs.
- Memory: 50 bytes per glyph kept, 62 with the word indexes (5.9 MB for
  22 pages, 94,786 glyphs), against 318 before; a closed file lets go of
  what was read from it. The boxes alone take 32 bytes per glyph (four
  doubles); single precision would halve that and lose the exact equality
  with `GlyphPages.page` the tests check.

## Sources

- Hypothesis, "Fuzzy Anchoring": https://web.hypothes.is/blog/fuzzy-anchoring/
- Hypothesis developer list on the recommended anchoring selectors:
  https://groups.google.com/a/list.hypothes.is/g/dev/c/JCH8BRkp-cs
- Jon Udell, "Notes for an annotation SDK":
  https://blog.jonudell.net/2021/09/03/notes-for-an-annotation-sdk/ and
  https://github.com/judell/TextQuoteAndPosition
- Zotero documentation, "PDF Reader and Note Editor":
  https://www.zotero.org/support/pdf_reader; forum threads
  https://forums.zotero.org/discussion/104519/updating-a-pdf-keeping-the-annotations
  and https://forums.zotero.org/discussion/128003/swap-annotations-and-notes-to-a-new-copy-of-the-underlying-pdf-possible
- Microsoft Research, "Robust Annotation Positioning in Digital Documents"
  (TR-2000-95): https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/tr-2000-95.pdf
- Proposals: https://github.com/PDFCraftTool/pdfcraft/issues/164,
  https://github.com/Aman4563/lumen-ai-notes/issues/161
