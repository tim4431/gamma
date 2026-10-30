# Highlights after a PDF is replaced

A page's PDF can be replaced by another file: a paper compiled again from
its TeX, the published version in place of the arXiv one (`PUT
/pages/{id}/attachment`, the Document menu's **Replace the PDF…**, a build
script, [api.md](api.md) "Pages"). A highlight's place is a page and
rectangles in that page's frame (`pdf_position`, [api.md](api.md) "The
highlight shape"), good for the file it was made on: in the new file every
line has moved. What stays is the text. So after a replacement each
highlight is found again in the new file by its passage and moved to where
that passage is now. One found exactly is drawn as before, solid. One found
changed a little (a renumbered citation, a reworded phrase) is placed when
it reads alike enough, and drawn dashed, pending, until the person compares
it on its review card and accepts it or moves it. One whose passage is
nowhere, or only in places too unlike it, is marked `lost` and shown hollow
where it was, keeping the place most like it as its closest match when that
reads at least half alike, until the person moves it, takes that match on
its review card, or a later run finds it.

Code: `gamma/highlight_relocate.py` (the search and the write),
`gamma/highlights.py` (the position's flags, `prev` and `closest`:
`previous_place`, `valid_prev`, `valid_closest`, `from_scaled`),
`pdf_text.GlyphPages` (the glyphs, a page's frame and the pictures the PDF
itself draws),
`routers/pages.py` (`replace_pdf`, `_start_relocation`, `POST
/api/jobs/relocate-highlights`), `blocks_store.attachment_file` (the stored
file a page shows); in the web app `pdf/PdfViewer.jsx` (the dashed and
hollow looks, the closest match's outline, the tip's move button),
`pdf/HighlightReview.jsx` with `pdf/highlightReview.js` (the review card,
its queue and its walk, its steps and whether each is in effect,
Discard's step and its pure arithmetic) and
`shared/lib/wordDiff.js` (its word comparison),
`shared/model/blockModel.js` (`placedToCheck`,
`placeToReview`, `roughMatchPercent`, `acceptedPosition`, `closestPlace`,
`closestPlacement`, `reviewUndone`, `blockPlace`, `insertBlockAt`),
`editor/BlockTree.jsx` (the row's dashed and hollow dots, its move
button), `app/App.jsx` (Accept, Use closest match, Discard, a step's
undo, the stored position the card reads, move mode, the Document menu's
rows, what the replacement says),
`app/prefDefs.js` and `settings/SettingsDialog.jsx` (the thresholds),
`tasks/taskKinds.js` and `tasks/useAppJobs.js` (the task row, the summary
when a run ends).

## The job

`PUT /pages/{id}/attachment` that changes the stored file, on a page with
highlights placed on its PDF, writes in its own transaction which file each
highlight's rectangles were measured on (`pdf_position.doc_id`, the next
section) and, after its commit, starts the `relocate-highlights` job
([tasks.md](tasks.md)). The answer carries it as `job` beside the block, so
the tab that replaced the file knows the run at once and says what it found
when it ends. The same file again, or the same stored file from another
address (only `source_url` changes), writes nothing of the kind and starts
nothing.

The stored file a page shows is its attachment's `doc_id`, or, on a page
that has none, the upload its `source_url` serves when that is exactly
`/api/uploads/<doc_id>.pdf`: the library's Duplicate gives a copy only that
address (`blocks_store.attachment_file`, `libraryUtils.attachmentFile` in
the web app; the attachment's own `id` stays empty, as gating and lookups by
attachment expect). A copy's highlights are therefore read from, stamped
with and placed in that stored file like the original's. A PDF shown from
another URL with no `doc_id` is no stored file (a URL-opened PDF has one,
the URL's hash, and the run fetches it as the indexer does): a replacement
by such a URL starts no run and answers `relocation_error` "the PDF is not
stored on the server", the manual start answers 400 with it, and the
Document menu's row is disabled with a title that says why.

The job's owner is the account behind the request, a session's or a
write-scope integration token's. A token's run shows in its account's
Background tasks, while the token itself reads no jobs (`/api/jobs`
answers it 403). A share link never reaches the endpoint. The job's params
are `{page_id, title, doc_id, min_match, exact_chars}`, the file it places
for and the thresholds it goes by ([The thresholds](#the-thresholds)), and
its key is `<page id>:<doc_id>`: one run per page and file. A replacement
first stops the same account's run for the file it replaced, queued or
running, since that run's result would be for a file the page no longer
shows; the new run never waits for it, their keys differing. A run already
past its last stop finishes on its own; a write after the replacement is
refused (below). Another member's run for the old file is left alone and
fails at its write the same way, writing nothing.

A run that cannot start leaves the replacement standing: when the
account's background tasks are full (20 queued or running, `jobs.start`'s
429), or a run for the same page and file is still stopping, the
answer carries the reason as `relocation_error` instead of `job`, the log
has `[relocate] <page>: the job did not start (<reason>)`, and the tab says
"PDF replaced. Its highlights were not placed (…): choose Find highlights
in this PDF in the Document menu to place them." The highlights keep the
file their rectangles belong to, so a later run reads them right.

The Document menu's **Find highlights in this PDF**, and a finished row's
Start again in Background tasks, start the run by hand (`POST
/api/jobs/relocate-highlights {page_id}`): for a replacement whose run did
not start, failed or was stopped, for a page restored from Recently
deleted, or for a PDF replaced before the job existed. It is for session
accounts only (a token gets 403, as at every job start) and workspace
editors. While a run for the page's current file is going, the answer is
that run (200); 409 only while that run is still stopping, 429 when the
account's jobs are full, 400 for a page without a PDF, with one not stored
(above) or without highlights placed on it, 404 for no such page. A manual
run reads each highlight from its own file, as the replacement's does.

The run (`relocate`) reads the page's placed highlights, the snapshot it
works from: highlights and link regions with rectangles, whatever their
depth in the notes (`page_highlights`, `highlights.is_highlight`; ink
groups, text boxes, pictures, sheets and a position of its page alone are
not its business). It opens the new file (`ai_context.pdf_path`: the
stored copy, or a URL-opened PDF fetched as the indexer fetches it), reads
every page (phase `reading`, unit `pages`), places each highlight (phase
`matching`, unit `highlights`), and closes the files and all it read from
them. Once
the matching is done it reports so (a stop asked for until then lands
there) and marks itself unstoppable (`stoppable=False`): from there it
writes. A new file that is not stored on the server, cannot be read, or
has no text layer at all fails the run rather than marking every highlight
lost.

**The write** (`_write`) is one compare-and-set, in one transaction under
the page's write lock. The page must still be a live page: one deleted or
in Recently deleted fails the run with "the page was deleted before its
highlights were placed — restore it and choose Find highlights in this
PDF". It must still carry the file the run placed for (`attachment_file`),
else "the page's PDF changed again before the highlights were placed".
Either way nothing is written. The op for a highlight deleted since the
snapshot, or whose `pdf_position` or `quote` changed (a person moved it
meanwhile), is dropped: the highlight is left as it is and counted in
`changed` instead of its outcome. The rest go as `set` ops in batches of at
most `MAX_OPS` on the page, client `relocate`, actor the owner, all
committed together, then each batch's `after_commit`: ordinary ops, so
they reach the open tabs over the page socket and the viewer redraws. The
result is `{highlights, moved, kept, lost, approx, changed, min_match,
exact_chars}`: the five counts after the first add up to it
([What a run writes](#what-a-run-writes)), and the last two are the
thresholds the run went by.

## Which file a position was measured on

`pdf_position.doc_id` names the stored file (a page `doc_id`, the content
hash) a position's rectangles were measured on, when that is no longer the
page's PDF. A position without it was measured on the page's current PDF,
which is what the viewer, the imports and a move by hand write, so none of
them know of it. The page's previous file is not enough: a run that never
wrote (stopped, failed, refused, or one that could not start), a highlight
left lost, a wrong file put up and replaced again each leave highlights
whose rectangles belong to a file before that one.

The replacement writes it, in the transaction that changes the attachment
(beside the marks on the blocks imported from the old file's annotations,
in batches of `MAX_OPS` the same way): the outgoing file's id on every
placed highlight of the page whose position names no file yet (a
duplicated page's: the upload its address serves), or `unstored`
(`highlight_relocate.UNSTORED`; uploads are named by their content hash, so
it names none) for a page that showed its old file from another URL alone.
A position that already names a file keeps it. The same write drops a lost
highlight's `closest` ([What a run writes](#what-a-run-writes)): that is a
place in the outgoing file, and a run that fails or never starts would
leave the card pointing into a file the page no longer shows. A backup's
restore keeps `doc_id`, `lost`, `approx`, `match`, `prev` and `closest`
with the rest of the position (`highlights.from_scaled`, which every
restored position passes through; `prev` and `closest` only in their own
form, [What a run writes](#what-a-run-writes)).

The run reads each highlight (its passage, context, anchor and picture)
from the file its own position names: the page's new file when that is the
one, else the stored file, looked for among the workspace's uploads only
(`find_upload_file`, never fetched), each file opened once. A `doc_id` that
is no file name names no file. A file no longer stored logs `[relocate]
<page>: the file <id> is no longer stored; its highlights are placed by
their quotes`, and so they are, without context. The stamp is no reference
to the file: it is purged 30 days after nothing else references it, as any
replaced file ([user_db.md](user_db.md) "Stored files"). A position without
`doc_id` is read from the page's new file, unless it is `lost`: it was not
found in that file, so only its quote is looked for. A text highlight's
stored quote and the text read under its rectangles agree when, both
folded, they read alike from `QUOTE_AGREES` (0.6, difflib's ratio); one
that holds the other and is at least 0.6 of its length always does and is
told at once (`_agrees`). A shorter one does not count by being held: a
rectangle now over a short line that happens to be two words of the quote
is not the quote. When they do not agree, the rectangles do not belong to
that file: the quote is looked for, without context. When they agree and
the file is the page's new one (a position without `doc_id`, as in a run
by hand on an unchanged file, or one naming that very file), the highlight
is where it belongs and is kept without a search (a `doc_id` naming that
file is dropped, a `lost` mark cleared with its `closest`).

What the run writes carries no `doc_id`: a position in a new place is a new
one, measured on the new file, and one left where it was that named a file
is written once more without it. A lost one keeps it (the old position with
`lost: true`), so a later run still reads that highlight from the file it
was made on, and so does an area nothing placed (the old position with
`approx: true`, [Areas](#areas)). The file a dashed placement's old
rectangles belong to is kept apart, in `prev`, for the person to compare
and not for a later run to read ([What a run writes](#what-a-run-writes)).

## Finding a passage

**The text.** `PageText` reads a page's glyphs (`GlyphPages.page`: each
character with its box, a boxless break after every line of MuPDF's) as
one string, each character remembering its glyph: lines joined with a
space, a word hyphenated at a line's end joined whole ("entangle-" +
"ment"), and every character folded — dashes to one hyphen, curly quotes
straight, soft hyphens dropped, ligatures to their letters (NFKC), case
folded, whitespace collapsed. A MuPDF line that is a number of one to five
digits alone is left out with its glyphs, in both files: margin line
numbers (a review copy's lineno) and a page number on a line of its own,
which the other version does not have. Every index counts the glyphs left.
`normalize_quote` folds a stored quote the same way, so the viewer's text
of a selection (pdf.js's reading) meets MuPDF's. A run reads every page of
the new file and keeps them until its matching ends, so a page is kept
compactly (the chars as one string, the boxes as one flat array).

**The passage.** In the file the highlight was measured on, its rectangles
go back to the glyphs' space through the export's mapper
(`pdf_export.viewer_rect_to_pdf`: the stored frame's size, the page's view
box from `GlyphPages.frame` and its rotation), and the glyphs whose centre
they cover are the highlight's (`pdf_text.covers`, the rule of the
annotation import's `quote_under`). A text highlight's needle is the text
of those glyphs alone, read as a selection reads: each run of covered
glyphs is one stretch (a mark without a box does not break it, and two
stretches the text already joins, a word hyphenated at a line's end or a
line that ends in a covered space, are one), the stretches are joined with
one space, and a word hyphenated where the highlight skips a line break is
joined whole (`_joined`). So a caption or a margin note the file draws
between two lines of the highlight is not in the needle.

When the covered glyphs, read from a file other than the page's new one,
run past the highlight's stored quote, the quote's own copy on the page is
the passage instead (`_on_quote`), as one stretch: of the page's copies of
the folded quote, the one that shares the most text with what the
rectangles read (the first of those), when that reading runs past either
of its ends. On the new file itself, what the rectangles read as they are
says whether they are over their quote (below). Rectangles a little off the file's
glyphs read the quote's neighbours too: stored in a frame that does not
match their scale (a page size a few percent off the one they were
measured at), a sentence's line rectangles sit a line low or high and read
the end of the sentence before or the start of the one after, and a line's
rectangle that runs on over the next word, or past the column's edge into
the next column, reads those words. Such a reading is still alike enough
to count as the quote (`_agrees`, below); searched for as it read, it was
found in the new file with those neighbours, given rectangles of their
own, and roughly when they changed (a renumbered citation in the next
sentence), so drawn dashed with a rectangle over the next line. A reading
within the copy stays as it is: the rectangles then say which of the
quote's words are the passage (a caption between two columns they skip).
So does one when the page holds no copy of the quote that it touches (a
quote the viewer read otherwise than MuPDF does, a margin line number in
it). The context is the 40 characters (`CONTEXT_CHARS`) before the first
stretch and after the last.

The highlight's **pieces** (`_pieces`, `Piece`) are its stretches as the
file read them: stretches with nothing but a line's end between are one
piece, and a new piece begins where the file read words the highlight does
not cover (a caption drawn between two columns, a footnote, a mark the
rectangles miss), each with its own 40 characters either side and the
number of words skipped before it. A highlight from a column's foot to the
next column's top, on a page whose content stream draws a caption between
them, has two; most highlights have one.

An area's needle is the text from its first covered glyph to its last, and
its anchor that text's line boxes (`rects_for_span`) as one box, as
fractions of the page: measured as the match in the new file is measured,
so an unchanged area is found where it was. The covered glyphs alone would
not do for an area over a table's column, which the file draws row by row:
they never follow each other in the text, not even in an identical file.
Only when that span runs through much text the area does not cover (longer
than twice its covered glyphs and ten: glyphs drawn far out of reading
order) is the needle the covered glyphs' text and the anchor their box.

Without the file (gone, `unstored`, a lost position without `doc_id`, or a
quote that disagrees with it) the stored `quote` is the needle, with no
context and in one piece.

**The candidates** (`find_passage`) are looked for on every page of the
new file. A needle of fewer than three characters (`MIN_NEEDLE`) is not
looked for at all.

- Every exact copy is a candidate if it starts and ends on word edges:
  "ion" in "motion" or "fig. 1" in "fig. 12" is not the passage. The rule
  is lifted on a side where the context shows the old passage itself began
  or ended inside a word.
- The rough search (`_rough_on_page`), for a needle of at least
  `exact_chars` characters (twelve by default, [The thresholds](#the-thresholds))
  whatever its count of words. It runs when there is no exact copy, and
  also when there is a context and no exact copy stands in surroundings
  that read like it from `EXACT_SURE` (0.9), so a passage reworded in place
  may beat a verbatim copy elsewhere. On each page, the words whose core
  (the word without the punctuation at its edges) is one of the needle's
  vote for where the needle would start, and around the six best-voted
  starts (`MAX_SEEDS`, none within the slack of a better one) the needle is
  aligned word by word (`_rows`, `_align`: a semi-global alignment, the run
  free to start and end anywhere in a window of the needle's length and
  the slack either side; on a tie the later start and the earliest end, so
  a dropped first or last word never pulls in the word beside it). No word
  is frozen: a number, a citation, a symbol or a negation may change like
  any other. A page word added costs one, a passage word dropped `DROP`, a
  hair more (one plus a millionth), so that a run keeping the passage's
  last word beats one as cheap that stops short of it: "in the cryostat."
  read in "in a new cryostat." covers "cryostat.", where a cut before "a
  new" would have cost the same and read less alike. A word read as
  another costs one less how alike the two are (`_word_cost`):
  `_ends_alike`, twice the characters they share at their start and, past
  it, at their end, over both lengths, which for one change inside a word
  is difflib's ratio at a twentieth of its time; for two words that both
  have digits, the mean of that and their likeness with every digit read
  as 0. So "[27]," for "[25]," costs 0.1 and "5" for "4" 0.5, and a
  changed number at a passage's edge is read as the new number rather
  than dropped. A pair less than half alike costs `DROP`, as a dropped
  word does, so a last word swapped for an unlike one ("with this method."
  as "with this approach.") is still left out: the costs cannot tell it
  from a dropped word before the next sentence's first.
- When no page holds an exact copy, the passage across each page break
  (`_across`), for a needle of at least `exact_chars` characters and two
  words: its head is aligned against the last words of one page and its
  tail against the first words of the next (the needle's length and 20
  more, `JOIN_SLACK`), both free to start and end anywhere, so the footer,
  footnotes and running head between them ("Phys. Rev. A 104, 033101
  (2021)") cost nothing; the cut that costs least in all wins, and each
  part must cover some words of its page. The candidate is the part on the
  page with more of the passage's words (the first on a tie), a `part`
  whose quote becomes that part as the new file spells it.
- When no page holds an exact copy and the old file read the passage in
  several pieces, piece by piece (`_by_pieces`): the longest piece (the
  first of the longest) wherever it is, exactly, else, from `exact_chars`
  characters on, roughly when no exact copy leads anywhere; then each piece after it must begin, and each piece
  before it end, within the words the old file skipped between the two and
  20 more (`JOIN_SLACK`) of its neighbour (`_beside`), so the caption still
  drawn between two columns costs nothing. A piece is found as a passage
  is (`_piece_hits`): exactly on word edges (its own context lifting the
  rule on a side), else, from `exact_chars` characters on, the cheapest
  rough run within reach; a shorter piece exactly or not at all. Among
  several hits of a piece within reach, the cheapest wins (an exact copy
  first), then one on the same page, then the gap nearest the old one. The
  next piece may be on the next page (the one before, on the previous
  page) when its neighbour lies within that reach of the page break, the
  footer and running head between costing nothing; a chain crosses one
  break at most, and then the highlight goes to the page with more of the
  passage's words (the first on a tie), covering the pieces there, its
  quote theirs as the new file spells it. The chains are candidates beside
  the others, a chain's surroundings being the text before its first piece
  and after its last.

Two shortcuts keep the rough search fast, and both are heuristics: an
alignment gives up once it costs more than 0.4 per word of the needle
(`ROUGH_BOUND`, at least one, `_bound`), and a page, or a window, is
aligned only when the needle's words found there unchanged up to their
punctuation hold half of its letters (`ROUGH_PRESENT`, `_holds`;
`_holds_split` for some cut at a page break). A place past either is no
candidate. Such a place reads, as a rule, far less alike than the default
threshold, but a lowered one could have taken it
([Limits](#limits-and-open-items)). Giving up early itself changes no
answer.

**The confidence.** A candidate's confidence is how alike the needle and
the text found there read, both folded: difflib's `SequenceMatcher` ratio
without its junk heuristic (`_similar`), 1.0 only for an exact copy.
Across a page break it is measured over both parts joined with a space;
for a passage found piece by piece, over the old pieces and the ones
found, each joined with a space, the text between them left out. The
alignment's cost only finds the places; the confidence says how alike
each one is.

**The ranking.** A candidate's score is its confidence plus a fifth
(`CONTEXT_WEIGHT`, 0.2) of its context fit when the needle has a context:
the mean likeness (`_similar`) of the sides the context has to the 40
characters beside the candidate. Unrelated 40-character windows score 0.30
at the median and 0.475 at the 99th percentile, lightly edited ones 0.83
to 0.90, so a passage reworded in place, in surroundings that read like
the old ones, outranks a verbatim copy of it in the conclusion, as long
as it reads more than about 0.86 like the old one: the copy scores 1.0
plus a fifth of some 0.3, the one in place at most its confidence plus
0.2. Reworded further, the highlight goes to the copy, solid
([Limits](#limits-and-open-items)). Without a
context the score is the confidence. On a tie the page nearest the
highlight's old page wins, then the place nearest the old one: the centre
of the stored `boundingRect`, as fractions of its page, against the centre
of the candidate's glyph boxes, as fractions of the new page. The
punctuation a match carries at its edges that the needle does not
("cooled." for "cooled") stays outside (`_trimmed`).

**The answer.** The best-ranked candidate is the highlight's new place
when its confidence reaches the run's `min_match` (0.8 by default) and, for
a needle shorter than `exact_chars`, only when it is exact. Otherwise there
is none, and the highlight is lost. An exact match is drawn solid, a rough
one dashed ([What a run writes](#what-a-run-writes)). A run also asks for
the best-ranked candidate under the threshold (`find_passage`'s `closest`,
given `CLOSEST_MIN`, 0.5, set by hand: the lowest threshold an account can
set). For a needle of at least `exact_chars` characters, that candidate
comes back marked `below` when it reads at least that alike, from the same
search. It places nothing: the highlight is lost and keeps it as its
`closest`. A shorter needle has only exact candidates, which are applied,
so it never gets one.

**The rectangles.** The glyphs the match spans give one box per line of
MuPDF's; two lines sharing a baseline (a span of math inside a sentence)
merge (`rects_for_span`). A passage found piece by piece gets each
piece's boxes, and nothing of what lies between them. Each box goes to the
viewer's frame through `pdf_point_to_viewer` and the page's frame
(`GlyphPages.frame`: the rect MuPDF shows, the crop box clipped to the
media box, taken back to the glyphs' space through the same matrix the
glyphs use, and the page's /Rotate), the frame's size being the page as
shown (`display_size`: the crop box, its sides swapped on a quarter turn),
so a rotated or cropped page places like pdf.js draws it. A position found
within half a percent of the page of the old one, on the same page, of a
highlight not lost, is in the **same place**; any other is in a new place.
A passage found roughly (a confidence under 1.0), or a part across a page
break, also rewrites the highlight's `quote` to the text as the new file
spells it, when that differs from the stored one, since that is what the
highlight covers now; in the same place too. As the file spells it is the
characters of the glyphs the text was read from, in the file's case
(`PageText.original`): whitespace and line ends one space, and a word
hyphenated at a line's end one word, as the page's text joins it
("Entanglement", never "Entangle- ment"); the pieces of a passage found
piece by piece are joined as their stretches are (`_spelled`).

**The cost.** The matching is pure Python and holds the GIL, so it naps for
2 ms after every 10 ms of matching (`_breathe`, `NAP`, `NAP_EVERY`) and the
server's requests go on meanwhile. It reports a bare progress once per page
the rough, page-break and piece-by-piece searches cover, and before it
reads a page's pictures, so a stop lands within a page. A file's pages
past `pdf_text.MAX_PAGES` (5000) are not read: a highlight there is a
passage not found. What a run costs in time and memory, and the requests it
slows: [research/highlight-anchoring.md](../research/highlight-anchoring.md#measurements).

## Areas

An area highlight (`area: true`) has no quote. It is placed, in this order,
by:

- **the text inside it**, when its own file is at hand and the needle read
  there has at least twelve characters and two words (a caption, an axis's
  labels, an equation's letters): the passage is found as above (only the
  whole of it: a part across a page break would put the box askew) and the
  box is put around it as it sat around the old anchor, the same offsets,
  as fractions of the page, clamped to the page (`_box_by_offsets`). The
  match's confidence decides as a text highlight's does: solid when exact,
  dashed with its `match` when rough;
- **the picture it covers**, in its own file: an image the PDF itself
  draws, not a picture placed on the page ([pictures.md](pictures.md)),
  drawn under at least half of the box (or of which at least half is
  under the box), known by MuPDF's digest of its data with its pixel size
  (`GlyphPages.images`), found where the new file draws the same data, on
  the page nearest the old one when it draws it more than once (a figure
  embedded again by pdfTeX; a publisher's re-encoded figure is another
  picture); the box follows it by offsets, solid;
- **its page's other highlights**: every highlight placed by its text or
  picture, exactly or roughly, from the same old page of the same file (the
  `doc_id` its position names, or the new file for one that names none: a
  shift measured from another file's page says nothing of this one), a
  kept one counting as no shift (lost ones count for nothing); when they
  all landed on one new page and their shifts (of the centre, as fractions
  of the page) agree within 15% (`RIDE_SPREAD`) across and down, the area
  goes to that page shifted by their median, an estimate: `approx` without
  `match`, drawn dashed. A median that leaves it on its page within half a
  percent leaves it where it is, counted as kept;
- else **nothing placed it** (no neighbours, neighbours on two pages or
  disagreeing, or a shift that would carry an edge of it off the page). An
  area read from the page's new file, made on it, stays as it is, counted
  as kept. One measured on another file stays where it was with its
  rectangles and its `doc_id`, still that file's measurement, so a later
  run reads it from that file again; it is marked `approx` without `match`
  (written once, its `prev` those same rectangles on that file) and counted
  so, drawn dashed for the person to check (an area over a plotted figure,
  with neither text nor an embedded picture to go by, on a page whose text
  reflowed). Until the person accepts it: Accept drops its `doc_id` with
  the rest, so its rectangles are the current file's, a later run on that
  file leaves it be, and the next replacement stamps it with the outgoing
  file, from which it rides along with its neighbours.

An area placed by estimate (`approx` without `match`) stays an estimate
through later runs, whatever places it: the text or picture under an
estimated box is only what the estimate happened to cover, so finding it
again confirms nothing and gives it no `match`. Like every dashed
highlight it is pending until the person accepts it or moves it
([What a run writes](#what-a-run-writes)).

## The thresholds

Two numbers decide what the rough search may apply, both set by hand and
not calibrated: the confidence from which the best place is applied
(`ROUGH_APPLY`, 0.8) and the length of a folded needle, or piece, under
which only an exact copy is (`ROUGH_MIN_CHARS`, 12 characters). These are
the defaults; an account sets its own in Settings → Reading & editing ›
Replacing a PDF ([settings.md](settings.md) "The Settings dialog"):

- **Apply a rough match from** (`relocateMinMatch`): a percentage from 50
  to 100 in steps of 5, default 80, a `Stepper` with Reset. At 100% only an
  exact copy moves a highlight.
- **Passages shorter than this must match exactly**
  (`relocateExactChars`): 0 to 60 characters, default 12, a `UnitInput`
  committed when it loses the focus. At 0 every passage may match roughly.

Both are account preferences in `PREFS` (codecs `stepClamped(50, 100, 5)`
and `intClamped(0, 60)`: a value between two steps reads as the nearer
one, a value out of range as its end), kept in the account's profile and
synced as the others are. When a run starts, by a replacement or by hand
(`_start_relocation`), the server reads both from its owner's profile
(`db.get_profile`, by the same names; `highlight_relocate.thresholds`) and
reads a number as the codec does: cut to a whole number, put on its step
from the minimum and pulled into its range (83 is 85, 400 is 100, −3 is
0). A missing value, or one that is no number (a string, a bool), is the
default, each preference on its own. The job's params record them
(`min_match` as a fraction from 0.5 to 1.0, `exact_chars`), `relocate`
passes them through `place_all` to every `find_passage` and to the piece
search, and the result echoes them, so a setting changed later leaves the
runs already started or done as they are. A token's replacement goes by
its account's profile. A guest's account preferences stay in its browser
([settings.md](settings.md) "Where settings are stored"), so a guest's
runs go by the defaults.

## What a run writes

A run leaves each highlight in one of three looks, and counts it by the
look it leaves (`place_all`'s `kinds`):

- **solid**: placed by an exact match or by the PDF's picture under an
  area, or left where it was made (a highlight on the page's own file over
  its quote, an area there that nothing moves). Its position carries no
  `approx`, `match` or `prev`. In a new place it counts as `moved` (so does
  a lost one found again, even where it was); in the same place as `kept`,
  written then only to drop a `doc_id` or a stale `match` or `prev`, or to
  respell its quote.
- **dashed** (`approx: true`): placed by a rough match, with `match`, its
  confidence rounded to two decimals and at most 0.99 (1.0 is an exact
  match's), also when it lands where it was (a sentence reworded in place);
  or an area placed by estimate, or one measured on another file that
  nothing placed, without `match`. It counts as `approx`.
- **hollow** (`lost: true`): nothing found it, or its best place reads too
  unlike it (under `min_match`, or not exact for a needle under
  `exact_chars`). It keeps its old rectangles and its `doc_id`, with, for
  a text highlight, its `closest` place (below), and counts as `lost`. One
  already lost is written again only when its `closest` changes: another
  place, or none any more. A run that finds it later, even in the same
  place, or a move by hand, writes a position without the mark and without
  `closest`.

**Where it was before.** Every placement a run draws dashed also stores
`pdf_position.prev`, where the highlight was before, for the person to
compare (`highlights.previous_place`):

    {"doc_id": "<the file its rectangles were measured on>", "pageNumber": 1,
     "rects": [[0.1176, 0.1136, 0.4902, 0.1313], …], "quote": "…"}

`doc_id` is the file the run read the highlight from (its position's
`doc_id`, else the page's new file for a position measured on it), written
even when that file is no longer stored or is `unstored` (the card then
shows the quote alone); `pageNumber` its page there; `rects` its
rectangles as fractions of that page as shown (`[x1, y1, x2, y2]`, top-left
origin, clamped to the page, four decimals, at most 200; none for a
position without a frame's size); `quote` its stored quote before the run
respelled it (`""` for an area, at most 1000 characters). An exact
placement, a kept one and a newly lost one get no `prev`, and a stale one
goes when a highlight not pending is placed solid. `highlights.from_scaled`
keeps `prev` only in this form (`valid_prev`: a string `doc_id`, a
positive whole page, at most 200 rectangles of four numbers from 0 to 1, a
string quote clipped to 1000 characters) and `match` only as a number from
0 to 1.

**Its closest place.** A lost text highlight whose best candidate read
under the threshold but at least `CLOSEST_MIN` (0.5) alike
([The answer](#finding-a-passage)) keeps that candidate as
`pdf_position.closest`, for the person to look at and take on its card
(`closest_place` in `place_all`):

    {"pageNumber": 2, "width": 612, "height": 792,
     "boundingRect": {"x1": 72, "y1": 300, "x2": 540, "y2": 328},
     "rects": [{"x1": 72, "y1": 300, "x2": 540, "y2": 314}, …],
     "match": 0.72, "quote": "…"}

The place in the page's new file, built as a placement's position is (the
match's spans through `rects_for_span`, in the new file's frame); `match`
its confidence rounded to two decimals and at most 0.99, as a dashed
placement's; `quote` its text as the new file spells it (`_spelled`), at
most 1000 characters. The run writes it only in the form
`highlights.valid_closest` keeps (a positive whole page, a positive width
and height, a placed `boundingRect`, one to 200 placed rectangles, `match`
a number from 0 to 1, a string quote clipped to 1000 characters; the
rectangles bare), so a candidate of more than 200 rectangles gives none.
`from_scaled` keeps a well-formed `closest` and drops a malformed one. A
later run that leaves the highlight lost replaces `closest` with its own
candidate, or removes it when there is none; one that places it, or
finds it where it is on its own file, removes it. A replacement of the PDF
drops it too ([Which file](#which-file-a-position-was-measured-on)).

**Pending.** A highlight that is `approx` when a run starts is pending:
drawn dashed, and not yet accepted. Only the person resolves it, by
accepting it (`approx`, `match`, `prev` and `doc_id` go, the rectangles
stay, [In the web app](#in-the-web-app)) or by moving it by hand (a new position
without any of them). A run leaves it pending whatever it finds, an exact
copy included. Placed in a new place, it stays `approx` with its own
`match` (one without takes this run's rough match; an estimate takes none)
and its own `prev` (one without, made before `prev` existed, gets one
now). Found where it is, it is written only to drop a `doc_id` or respell
its quote. Not found, it is marked `lost` and keeps the rest (with a
`closest` place, as any lost text highlight may get), though it is drawn
hollow and reviewed as a lost one. An area nothing placed is left as it is. So Find highlights
in this PDF on the same file again leaves every dashed highlight dashed,
and a `prev` keeps showing where the highlight was before the run that
first drew it dashed, not before the last one.

**The counts.** The result's `moved` (solid, in a new place), `kept`
(solid, where it was), `approx` (dashed: matched roughly, placed by
estimate, or pending already), `lost` (hollow) and `changed` (left as a
person changed it while the run matched, [The job](#the-job)) add up to
`highlights`.

## In the web app

**On the page.** The viewer (`PdfPage`) draws a lost highlight hollow: a
branch of its inline style ahead of the others, so whatever else it is (an
area, commented, a link, pending), it is a dashed line in the stronger
shade of its colour that underlines a commented passage
(`--hl-dark-line`), with no fill, on a flipped page too. Its rects carry
the class `pdfHlLost`, which has no style; the browser tests find a lost
one by it. A pending one (`approx` and not lost, `placedToCheck`) keeps
its look under a dashed edge: an area's border, a passage's 1px outline
in that same stronger shade. An outline, so that a commented or link
passage keeps its underline, which is a border; and the dash rides inside
a shorthand, since React rewrites only the style keys that change and a
shorthand rewritten beside an unchanged border-style would reset the dash
to solid. A lost or a pending highlight has no title, not even its
comment's (a link region keeps its own): hovering or tapping it opens its
review card, which says what happened. Exports and the AI read the position as any other; the
flags are the viewer's.

**The review card** (`HighlightReview.jsx`, `useHighlightReview`).
Hovering a pending (dashed) or a lost (hollow) highlight opens, after
150 ms, a card anchored to it. The pointer may cross into the card, which
waits 200 ms as the pointer leaves the highlight, and leaving both closes
it, as the note badge's tip does (`NoteBadge`). Only dashed and hollow
highlights' rectangles listen for the pointer (PdfPage's `reviewable`), so
a page of many highlights costs nothing more. Touch has no hover: a tap on
one (`lastPointer`, which also tells the tap's compatibility mouse events
apart) opens the card instead of the tap's usual action, a second tap on
it does that action, and a press outside the card and its highlight closes
it. A right-click on one, or a finger held on it, opens the highlight's
menu in place of the card, shown or on its way. Escape closes the card
too, and so does another page or file, or the highlight leaving the
review without the card's doing (its menu's Accept, a move not started
from the card, undone back to solid, deleted by someone else). A card
opened by a tap, by Previous or Next, from the Document menu or by a step
on it stays until then and takes the focus once it shows, so the keys
reach its buttons; one reached by Previous or Next focuses that button of
its own, so Enter walks on. A step keeps the focus on its button, which
stays. The browser takes the focus from what it hides, so a card hidden
a moment (the viewer scrolling to the closest match that Use closest
match took the highlight to, or back on an undo; the highlight scrolled
out of sight) drops it to the page; the card remembers the control the
focus was last on in it (forgotten once the focus goes somewhere else)
and, shown again with the focus still on nothing, puts it back there, or
on the card when that control is disabled now, so Enter presses the
step's button again. A card whose highlight took a step on a card stays while the
pointer rests on another dashed or hollow highlight: hovering opens no
card in its place. It is 360 px wide at most (the window's width less
16 px on a phone), so its row of buttons fits on one line (below). It is
a portal on the shared popup surface (`.hlReviewCard` beside `.pdfNoteTip`
in app.css, fading in as the tips do), placed with `placeFloating` and
`useFloatingPosition` ([ui-design.md](ui-design.md#menus-and-popups-open-in-place)):
under the highlight's rectangles, above them when there is no room below,
following the page as it scrolls, hidden while the highlight is out of the
viewer's sight and shown again on it when it scrolls back, and, opened for
a highlight whose page is still rendering, waiting up to four seconds for
its rectangles (as long as it waits for a page scrolled so far that it
drops them, then it closes). Hidden is the whole card, through its inline
`visibility`: nothing in it is set visible on its own. The before picture,
unseen until it is in, then inherits the card's visibility
(`.hlReviewShot:not(.loaded) img` hides it, nothing shows it); a picture
set `visible` once it loaded showed through the hidden card, alone and
without its rectangles, over the page text at the window's edge where
the hidden card stood, while the pages scrolled under it. A lost highlight from a page
the PDF no longer has (a revision that dropped an appendix: the run keeps
its old page number, `pastLastPage` against the viewer's page count) is
drawn nowhere, so no hover or tap reaches it; Previous, Next and the
Document menu do, without scrolling, and its card stands in the viewer,
centred near its foot, the line "This version of the PDF has no page 10."
under its header (`.hlReviewOffPage`), with Before, a closest match on a
page the PDF has, Move…, Previous and Next as any lost one's.

What it shows is `reviewCardState`'s, from the highlight and the card's
step in effect on it, if any (below):

- **Pending** (dashed): the header "Matched roughly: 92%"
  (`roughMatchPercent`, the position's `match`), or "Placed by estimate"
  for one without `match`; **Before** from its `prev` (`reviewPrev`,
  below); for a text highlight its old quote against its current one, word
  by word (below); and for an editor **Accept** (the primary button),
  **Move…** (move mode, below), then **Previous**, "2 of 3" and **Next**.
- **Lost** (hollow): the header "Not found in this version"; **Before**
  from its own position (`lostPlace`), since a lost highlight keeps its
  rectangles and the file they were measured on: when its `doc_id` names a
  file other than the page's current one (PdfViewer's `fileId`, App's
  `attachmentFile`), the picture is cut from that file, its rectangles
  turned into fractions of their frame and drawn over it, its stored quote
  beneath; when it names none, or the current file, its rectangles are this
  file's, drawn hollow right there, so there is no picture, only its stored
  quote. When the position keeps a `closest` place (`closestPlace`, read
  defensively as the server keeps it, a missing `boundingRect` taken as
  the rectangles' union), the card adds "Closest match: 72%" with **Show**,
  and the stored quote against the candidate's text, word by word, which
  then stands in for the quote under the picture (and, on the current
  file, for Before). Show scrolls to the candidate's page and outlines its
  rectangles while the card is open (`.pdfHlGhost` in app.css: a dotted
  line in the colour's line tone over a faint wash, taking no pointer, the
  dark-tuned colour on a flipped page), the card anchoring to the outline:
  a candidate a few pages away is drawn only once its page renders, after
  the scroll, and PdfPage then says so (`review.drawn`, the hook's
  `drawn`), so the card places itself under the outline.
  For an editor: **Use closest match** (the primary button, when there is
  one), **Move…**, **Discard** (below), then **Previous**, "1 of 3" and
  **Next**; nothing to accept. Beside Use closest match, Move… and Discard
  show their icons alone (their names in `aria-label` and `title`): three
  labelled step buttons and the walk's controls do not fit on one line at
  the card's width. A lost area has no `closest`.

**After a step** taken on the card (Accept, Use closest match, a move
started from its Move…, or Discard) the card stays as it was: the same
header, Before picture, comparison and closest match, the same buttons in
the same places, nothing on it moving (the Playwright suite compares every
box within a pixel). What it shows is drawn from where the highlight stood
before the step, and its quote then (`reviewCardState` over `stepOrigin`:
the position of the step's `before`, through a move's `prior` to the step
before it, and the quote the first step that rewrote it kept). Only the
step's button changes, in place: it reads "Accepted", "Placed", "Moved" or
"Discarded" with a check (Accept and Use closest match show no icon
before, the check marking a step done), pressed (`aria-pressed`, the
`.uiBtn.on` look), its title saying that pressing it again undoes the step
("Accepted: press again to undo"). On a lost card with a closest match,
Move… and Discard show their icons alone (above), so pressed they show
the check alone, "Moved" or "Discarded" being their `aria-label` and the
start of their title. Each step button holds both its faces in
one grid cell, the one not shown hidden (`.hlReviewFace`, `.hlReviewRoom`
in app.css), so it keeps one width either way. A button whose action no
longer applies stays in its place, disabled (`reviewActions`): after
Discard, Use closest match and Move… (nothing is left on the page); after
Use closest match, Discard; after a move, the others. Move… stays usable
after Accept and Use closest match: a move from there keeps the earlier
step to come back to (below). Show is disabled while a step is in effect.
The card follows its highlight when the step took it elsewhere (Use
closest match, a move). A discarded highlight is drawn no more, so its
card shows it as the card last had it and stays where it stood: anchored
to its old rectangles on its page (`placeBox`, from the page's box on
screen, the way PdfPage scales them), following the page and hidden out of
sight as any card.

A step is in effect only while the highlight is as the step left it
(`stepInEffect`): its stored position (App's `reviewPosition`, the block's
`pdf_position`, null for no block) is the one the step wrote, or, for a
Discard, its block is gone (a deleted one) or keeps the `{pageNumber}` it
wrote (one kept as a note), and it is drawn no more. The app's own undo
(Ctrl+Z), someone else's change or a move by hand elsewhere ends it, and
the card shows the highlight as it is now: dashed or hollow again, its
buttons as before any step; solid somewhere else, or deleted by someone
else, nothing to review, and the card closes. The card shows the newest
of its steps still in effect (`reviewList`, back through a move's
`prior`): Ctrl+Z after a move started from an accepted (or placed) card
takes back the move alone, so the highlight is as Accept left it, and
the card shows Accepted pressed, the highlight still in the walk at its
place; Ctrl+Y redoes the move and the card shows Moved again.

A viewer who may not edit sees the card without actions (Show stays).

**Before** is a picture of the highlight in the file it was measured on,
`GET /api/ai/selection-crop/{doc_id}?page={pageNumber}&box={x1,y1,x2,y2}`
(the chat's picture of a region, any workspace member, cached;
[api.md](api.md) "AI (`ai.py`)"), the box being the union of the old
rectangles widened by about two lines of text and kept on the page
(`cropBox`: 2.4 times a text highlight's median rectangle height, kept
within 0.6% to 3% of the page, or 1.4% of it for an area, as wide across in
points as down), with the old rectangles drawn over it in the highlight's
colour where they fall in the crop (`rectsInCrop`). While it loads, a
placeholder of the crop's shape (`cropAspect`). The picture takes the
page's paper and the theme's treatment of it, flipped on a flipped page, as
the viewer's pages and the zoom window do. When it cannot be had (a file no
longer stored answers 404; a `doc_id` that is no file name, such as
`unstored`, asks for none) one line says "The previous version of this PDF
is no longer stored.", and the old quote follows (ending in "…" when
clipped, below). A share view asks for no picture (`beforeCrop`): the
route is a workspace member's and reads no share token, and its box is the
asker's to choose, so opening it to a share link would let one cut whole
pages of a file the owner replaced. Its line says "The previous version is
not shown through a share link.", and the old quote follows.

**The comparison** of two quotes, word by word (`quoteDiff` over
`shared/lib/wordDiff.js`, the merge conflict card's word diff): the words
gone struck through, the words added marked, as the chat's approval card
marks them, whitespace read as one space, an unchanged stretch cut to six
words beside a change (`DIFF_CONTEXT`); nothing when both are empty. A
pending one's old quote as long as a `prev` keeps (`PREV_QUOTE_MAX`, 1000
characters, `reviewPrev`'s `clipped`) is the start of a longer passage,
cut wherever the thousandth character fell: it is compared up to its last
whole word and the current quote only as far, the rest of the current one
not marked added (save the words that replace old ones struck at the cut),
and an ellipsis ends it. A change past the cut is not shown; the picture
shows the old passage. A lost one's stored quote is whole.

**The review queue** (`reviewQueue`, `placeToReview`): the open page's
pending and lost highlights in document order (by page, then top, then
left, as `pendingInOrder` orders the pending ones for Accept all): the ones
left to check, which the Document menu's Review counts and opens the first
of. A lost one past the PDF's last page sorts last.

**The card's walk** (`reviewList`, `reviewPlace`): what **Previous** and
**Next** walk and "{i} of {n}" counts, in the same order: the queue, and
the highlights resolved by a step on a card while the viewer has shown
this page and file, each standing where it stood before its step
(`stepOrigin`), so a step changes neither the count ("2 of 3" stays "2 of
3") nor what Previous and Next reach. The steps are the viewer's
(`useHighlightReview`'s `steps`: the highlight's id → the step last taken
on its card, a move from a resolved card keeping that card's step as its
`prior`, the highlight as the card had it, and what its Discard would
do), recorded by the card (Accept, Use closest match, Discard) and by App
after a move from it (`reviewRef` with `done`); another page or file (a
new replacement) or a reload starts them afresh (`review.reset`, on
PdfViewer's `url`, `fileId` and `pageId`). A resolved one is in the walk
on the newest of its steps still in effect (above); one none of whose
steps is in effect any more is there as what it is now: to check again,
at its place; solid elsewhere, or deleted by someone else, out of it.
One the card deleted by Discard stays, as the card shows it, until
the walk starts afresh. Previous and Next scroll to the highlight (one
discarded to where it stood, `entryPosition`) and open its card, round from
the last to the first and back, both disabled when the walk holds one
highlight; a resolved one opens with its step's button pressed, which
undoes it. When none in the walk is left to check, the count reads "All
reviewed" in its place. The count holds the room of both its texts, so the
last step moves nothing. They are `ctlBtn` chevrons on either side of the
count, as the merge resolver's conflict card walks its conflicts
(`.mergeNav`), after the step buttons. The row stays on one line in
English and Chinese (whose count is "2 / 3") down to a 360 px phone,
wrapping only under a larger interface font: a window narrower than
376 px makes the card narrower than its 360 px, and there its sides, the
row's gaps and the labelled buttons' padding are tighter (app.css, the
`max-width: 375px` rule after `.hlReviewCount`). Below 360 px the row
may wrap.

**Accept** (App's `acceptHighlightPlaces`, `acceptedPosition`): the card's
Accept, the highlight's context menu (**Accept**, offered to an editor on a
pending highlight, before **Move to a selection…**) and the Document
menu's Accept all remove `approx`, `match` and `prev` from the position
and keep its rectangles, so the highlight is drawn solid where it is.
They remove `doc_id` too: accepting says the rectangles are right on the
PDF the page shows, so they no longer name another file (an area nothing
placed kept its old file's until then), and a later run neither reads them
from that file nor dashes them again; a run under way for that file skips
the position, changed meanwhile, by its compare-and-set. It is a tree edit
like any other, one for any number of highlights: one undo step, sent to
the others as ops. The card's Accept leaves the card open on the
highlight as it was, its Accept now "Accepted", pressed.

**Use closest match** (App's `placeClosestMatch`, `closestPlacement`):
the lost highlight takes the candidate's place and text. Its position
becomes the `closest` one (`pageNumber`, `width`, `height`,
`boundingRect`, `rects`) and its quote the candidate's quote, so `lost`,
`closest`, `doc_id`, `approx`, `match` and `prev` go with the old
position and it is drawn solid. A tree edit like any other: undoable, sent
to the others as ops. The card scrolls to the new place and follows the
highlight there, as it was, its Use closest match now "Placed".

**Discard** (App's `discardLostHighlight`; `discardKind`, `discardStep`,
`discardApplied` in highlightReview.js), on a lost highlight's card for an
editor, beside Move…: the highlight's passage is gone, and the person does
not want it placed. One that carries nothing of its own (no note, its
content empty or spaces; no child blocks, even empty ones; no comment
thread, open or resolved, `comments.threadsByBlock`) is deleted, as the
highlight menu's Delete deletes it (`removeBlockTree`). One that carries
any stays as a note and comes off the PDF: its `pdf_position` becomes its
page alone, `{pageNumber}`, the shape of a highlight whose place on its
page is not known (a Logseq import's), so nothing draws it and the review
queue leaves it out; its rectangles, `lost`, `closest`, `doc_id`,
`approx`, `match` and `prev` go, its quote, colour, note, children and
threads stay. Its row then reads as such a note's: a grey dot ("Jump to
page (no exact position)"), "p.1" and the ⊕ that attaches it to a
highlight. Either way it is one tree edit (ops, undo). The button's title
says which it will do: "Delete the highlight: it holds no note or
comment, and its text is not in this version", or "Take the highlight off
the PDF and keep it as a note, with its quote, note and comments" (the
viewer's `discardOf`). The card then stays as it was, its Discard now
"Discarded" (above). There is
no Discard on a dashed one, in the highlight's menu (its Delete does the
one, Move to a selection… the other's undoing) or in the Document menu:
discarding goes one highlight at a time, after a look at its card.

**Moving a highlight** (App's `moveHighlightId`): a highlight's context
menu on the page offers **Move to a selection…** to an editor, a lost row
its button and the review card **Move…**, which closes the card to make
way for the selection. Move mode and attach mode (a note's ⊕ waiting for a
click on a highlight) end each other (`startHighlightMove`,
`startAttachMode`), so one banner shows and the PDF's next selection means
one thing. The banner says what to do; the next
selection on the PDF, text or an area drag (the area key,
[hotkeys.md](hotkeys.md#held-keys)), shows the tip with one button, *Move
the highlight here* (`PlainTip`'s `onMove`), and translates nothing, even
with translate on select on; an area drag sends no picture of the region
to the chat either (`PdfViewer`'s `onAreaSelected` skips
`onAreaSelection` in move mode). `moveHighlightTo` sets the highlight's
`pdf_position` to the selection's (no `doc_id`, `lost`, `closest`,
`approx`, `match` or `prev`) and its `quote` to the selection's text; an
area drag makes it an area with no quote, as one made the usual way. It is
a tree edit like any other, so it is undoable and reaches the others, and
the highlight flashes. Escape, Cancel, another page, or another file on this page ends
the mode. It serves any misplaced highlight, not only a lost or a pending
one. A move started from the card's Move… is remembered
(`startReviewMove`, `moveFromReviewRef`: `{id, prior}`, `prior` the card's
step in effect when it had one; any other start of a move forgets it):
once the selection has moved the highlight, `moveHighlightTo` opens the
card on it again through `reviewRef`, which records the step: as it was,
its Move… now "Moved", pressed, without scrolling, the selection being in
sight.

**Undoing a step**: its button pressed again ("Accepted", "Placed",
"Moved", "Discarded"; there is no Undo button of its own). Each step is one
tree edit (`acceptHighlightPlaces`, `placeClosestMatch`,
`moveHighlightTo`), and the card keeps what it changed (`reviewStep`:
`{kind, before: {position, quote?}, after}`, `before` the position before
the step, with the quote for a placement or a move, `after` the position
it wrote). The undo (App's `undoReviewStep`, `reviewUndone`) is one more
tree edit that puts that position back, and for a placement or a move
that quote (an empty one dropped), so the app's own undo takes it back in
turn. It does nothing when the highlight's position is no longer the one
the step wrote (changed since, by an undo or by someone else). The card is
then as it was before the step, nothing on it moving: after an Accept's
undo, pending again; after a placement's or a move's it scrolls back to
where the highlight was. A move started from a card whose step was in
effect ("Accepted", "Placed") keeps that step as its own `prior` while
the position it moved from is the one that step wrote: its undo leaves the
card on that step again, its button pressed, so Accept and a move, then
Moved and Accepted pressed in turn, go back one step each time to the
highlight pending. A Discard's step
(`discardStep`: `{kind: "discarded", deleted, before: {position}}` with,
for a deletion, `removed`, the block as it was and its place among its
siblings, `blockPlace`; else `after`, `{pageNumber}`) is taken back by
`discardUndone`: a deleted highlight inserted again where it stood
(`insertBlockAt`; at the page's top, at the end, when its parent has gone
since; nothing when it is back already), an insert of its id as the
app's own undo of a deletion sends; one kept as a note given its lost
position again (`reviewUndone`, nothing when its position changed since).
Either way it is back on the page, hollow, where the card stands, and the
card is as it was before the Discard.

**The Document menu.** When the open page has highlights to review, the
paperclip popover lists **Review 3 highlights** (**Review 1 highlight**
for one; for anyone: it closes the menu, shows the PDF if it was hidden,
scrolls to the first in the review queue and opens its card) and, for an
editor while some are pending, **Accept all 3 rough matches** (**Accept 1
rough match** for one: one Accept over all of them, the status saying how
many when there are several). Review counts the pending and the lost ones
left to check (not the ones resolved on a card, which the card's walk
keeps), Accept all the pending ones alone, an estimated area included; a
discarded one is in neither, and there is no Discard over them all. Review's
title is "Show the first dashed or hollow highlight with what it matched
and where it was before". **Find highlights in this PDF**'s title says
what a run does: "Find each highlight's text in this file and move the
highlight there; one matched roughly is drawn dashed, one whose text is
gone hollow".

**The notes row.** A lost row's dot is hollow (`.highlightDot.lost` in
app.css, its colour the inline `border-color`), its title says so, and for
an editor on a page with a PDF a button beside it (the row's hover
controls, like the ⊕ of a note without a highlight) starts a move. A
pending row's dot keeps its fill inside a dashed ring in its colour's line
tone (`.highlightDot.approx`, `darkPageLine`, as the viewer draws it), its
title "Matched roughly (92%) after the PDF was replaced — jump to it", or
"Placed by estimate after the PDF was replaced — jump to it" for one
without `match`. A click on either jumps to the highlight, as any dot's
does.

**What the tab says.** The run's Background tasks row reads "Placing the
highlights of “…”", its phases "Reading the PDF" and "Finding each
highlight's text", with a stop button until it writes. When it ends, the
tab that started it (by the replacement, whose answer it adopts, by the
menu or by Start again) says what it found (`relocationSummary`):
"Highlights placed in the PDF: 2 moved, 1 matched roughly or estimated, 5
stayed in place, 1 not found.", then, when one is dashed, "Hover the dashed
highlight to compare and accept it." ("Hover a dashed highlight to compare
and accept it, or review all 3 from the Document menu." for several), how
many someone moved meanwhile (`changed`), and, when one is lost, how to
move a hollow highlight. A count missing from a result written before it
existed reads as 0. A run that failed or was stopped says so, unless the
PDF was replaced again meanwhile: a run for another file of the same page
started after it (`taskKinds.relocationSuperseded`, over the listing the
run ended in) means the replacement stopped it or had its write refused,
and the newer run reports instead. While one of the account's runs for the
page is going, **Find highlights in this PDF** is disabled, its title
"This page's highlights are being placed — see Background tasks".

## Limits and open items

- The thresholds were set by hand, not calibrated, and the character
  likeness is generous: "one dos tres cuatro five … ten" against "one two
  three … ten" reads 0.87, and "a doublet at the resonance … when the
  second beam is switched on" against "a single line below the resonance …
  when the first beam is switched off" 0.83. Both would be placed, dashed,
  at the default 80%; the dashed look and the card are there for such
  places.
- An exact copy is taken, solid and not pending, wherever it stands. A
  passage the new file rewrites in its own place (past about 0.86 alike,
  [The ranking](#finding-a-passage)) but repeats word for word elsewhere
  moves to the copy without a prompt: an abstract's sentence to the
  conclusion that restates it, a term to its next use. A short passage
  that now names something else does the same: "Fig. 3" lands on the new
  Fig. 3 after the figures were renumbered, at any threshold, since the
  new file holds it word for word. The surroundings cannot tell
  such a copy from an ordinary revision: an unchanged sentence beside an
  inserted or reworded one fits its old context about as poorly (0.2 to
  0.35) as an unrelated copy does (about 0.32), so a rule on the context
  fit would dash correct placements too. Telling them apart would take
  evidence that the old place survives with other words in it (both sides
  of the old context found next to each other, around different text),
  which is open.
- The threshold is applied to the best-ranked candidate alone. At a high
  `min_match`, a rough candidate that its context ranks above an exact copy
  elsewhere leaves the highlight lost rather than taking the exact copy,
  and its card offers that rough candidate as the closest match, not the
  copy.
- The rough search's bound and its pre-filter are for speed, but a place
  past them is no candidate: at a threshold lowered toward 50%, a place
  reading 0.6 or 0.7 alike may never be offered, nor kept as a lost
  highlight's closest match.
- A passage is split only where the old file read text between its
  pieces. One that the new file splits somewhere else (its lines reflowed
  and a caption or footnote now drawn inside it), or reads in another order
  (two columns drawn interleaved, line by line), is found only as far as
  the rough search reads it alike, and is otherwise marked lost.
- Without its file (purged, `unstored`, a lost highlight without
  `doc_id`) the stored quote is the needle and there is no context: two
  identical short passages are told apart by page and place alone, and a
  quote the viewer stored with a review copy's margin line numbers in it
  reads less like a file without them, each number a word too many, so it
  is placed dashed or, past the threshold, lost. Keeping a passage's
  context with the highlight when it is made (a `TextQuoteSelector`'s
  prefix and suffix, as the W3C model has it) would serve such runs; see
  [research/highlight-anchoring.md](../research/highlight-anchoring.md).
- A passage under `exact_chars` characters, or of one word, that the new
  file splits at a page break is not found.
- A page whose PDF is shown from a URL alone (no `doc_id`, and an address
  that is not an upload's) has no file a run can read: no run starts for
  it, and its highlights stay where they were.
- Ink groups, text boxes and pictures placed on the page
  ([pictures.md](pictures.md)) stay where they were; their pages may have
  shifted. Carrying them along with the page's highlights (the ride-along
  rule) is open.
- An area over a vector figure without text and without an embedded image
  rides along, or stays where it was marked `approx`.
- A run's ops are the server's: no tab's undo history has them. A highlight
  moved wrongly is moved back by hand; a dashed or hollow one's card shows
  where it was before.
- The passages are read with MuPDF, the viewer's text with pdf.js; a quote
  stored by the viewer that MuPDF reads differently (spacing in a
  mathematical line) is found roughly, drawn dashed, or not at all.

## Tests

`backend/tests/test_highlight_relocate.py`:

- the page text: the folding, hyphenated words, the file's spelling of a
  passage, the compact glyph store, number-only lines left out;
- the passage under a highlight: across a line's end, across two columns
  with a caption drawn between them (its two pieces), an area over a
  table's column measured alike in both files; rectangles that read past
  the stored quote placed by the quote's own copy, exactly over its words:
  line rectangles stored in a frame a tenth off their scale, which read a
  line low into the next sentence and its citation (dashed, with a rectangle
  over the next sentence, before), and line rectangles that run past their
  column's edge into the next column's lines (the sentence, one ending
  mid-sentence and one from the paragraph's start); rectangles within the
  quote, or a quote the page does not hold, read as they are;
- the search: an exact passage on another page; context, then page, then
  place; the rough search over changed words (a number, a citation, a
  symbol, a negation, punctuation: none frozen) with its confidence, a
  changed number at the passage's edge covered, a text too unlike not
  placed, twelve characters enough whatever the count of words, and a
  changed passage under twelve characters lost however alike; a dropped
  first word; a word added before a passage's last word or after its first
  (the whole sentence covered and respelled, and in place still ahead of a
  verbatim copy elsewhere) beside a dropped last word that does not take
  in the next sentence's first; word edges; a passage reworded in place against an exact copy
  elsewhere; the nearest place measured on the page; a passage split by a
  page break; a passage across two columns found piece by piece past the
  caption (shifted, a piece reworded, across a page break, too far from the
  piece before); the bound changing no answer and a page without the
  needle's words not aligned; a stop within a page;
- the rectangles on a plain and on a rotated cropped page;
- what a run leaves: a renumbered citation, a sentence with a few words
  changed, a changed number, and a stray symbol inside the old rectangles
  of a highlight without a quote, placed dashed with their `match`,
  respelled quote and `prev` (that symbol's highlight with a quote found
  exactly by the quote's copy, solid); a deleted
  sentence whose nearest look-alike reads too unlike lost, and placed at a
  threshold of 0.5; a lost highlight keeping its closest place under the
  threshold (its place in the new file, `match` and quote, kept by
  `from_scaled`), at the default 80% and at 95% but placed dashed at 70%,
  none for a best place under `CLOSEST_MIN` or a passage under twelve
  characters; a later run on the same file writing nothing, another file
  replacing the closest place, one with nothing alike removing it, one with
  the sentence placing it solid without it, and the file it was measured
  on, back as the page's PDF, dropping `lost`, `doc_id` and `closest`; an
  exact placement of a highlight not pending solid,
  dropping a stale `match` and `prev`; a pending highlight staying pending
  with its first `prev` and `match` whatever a later run finds, and a
  pending one without `prev` getting one; a dashed placement keeping where
  the highlight was when that file is gone; the thresholds read from a
  profile (in range, on their step, clamped, else the defaults) and
  deciding what a rough match applies;
- `place_all`: two files with each outcome; a file gone; a quote that is
  not the text under the rectangles, and a short line under them that is
  only a part of it; a highlight on the page's own file over its quote kept
  without a search; a sentence reworded in place staying, dashed, with its
  new spelling, and a respelled quote with a word hyphenated at a line's
  end; margin line numbers either way; a new file without text; a lost
  highlight not written again, and one found again; the page cap; the
  ride-along's cases; an area placed by estimate staying one through a
  second run on the same file and after the next replacement; an area with
  nothing to go by, on its own file and on another; such an area, once
  accepted, left be by the next run on that file and carried along at the
  next replacement; an unplaced area not
  carried by highlights measured on another file;
- the endpoint and the job: the PUT's run and its answer, the counts of a
  run end to end with the flags stored, a run going by its owner's
  thresholds as they were when it started (its params and result, a later
  run by hand at others, the first run's result unchanged), the stamps (a
  lost highlight's `closest` dropped with them) and `relocation_error` when
  the jobs are full, a run that never wrote, a lost
  highlight read from its own file by the next runs, a wrong file in
  between, a `doc_id` not stored or no file name, a page shown from a URL
  alone and replaced by a stored file, a duplicated page (the manual run on
  it, a replacement stamping its stored file), a PDF shown from a URL alone
  starting no run, the manual run after a skipped one, a second
  replacement stopping the run before and a manual start answering the
  running one, a page without highlights, the manual run by the stored
  quotes, a token's 403 on the manual start beside its replacement's run;
- the write: a highlight a person changes or deletes while the run
  matches, the page deleted meanwhile, the batches in one transaction, the
  file changed again.

`test_pages.py`'s replacement test checks the stamp;
`test_highlight_shape.py` that `from_scaled` keeps `match` as a number
from 0 to 1, `prev` only in its own form and `closest` only in its own
(a valid one, its quote clipped, 200 rectangles, extra keys stripped, a
malformed one dropped), and `previous_place`'s fractions of the page;
`test_restore_consistency.py` that a restore keeps `doc_id`, `lost`,
`closest`, `approx`, `match` and `prev`. In the web app,
`frontend/tests/highlightReview.test.mjs` the card's arithmetic (the
pending highlights in document order; the review queue of the dashed and
the hollow ones, a solid one left out; Previous and Next round the walk,
both disabled for one alone; a step changing neither the count nor the
walk, Accept, Use closest match and a move from an accepted card each
leaving the highlight where it stood before, all of them resolved "All
reviewed" (`left` 0); `stepOrigin` through a move's `prior`; a highlight
past the PDF's last page told apart and kept in the queue, last;
`prev` read defensively; a lost one's before from its own place, a
picture only of a file other than the page's; the card's state, pending,
lost with and without a closest match, and after Accept, a move from the
accepted card, Use closest match and Discard the same as before the step
but for its `step`; the buttons (`reviewActions`) in their places after
each step, the step's pressed, the ones that no longer apply disabled; a
step in effect only while the highlight is as it left it, the app's undo
showing it to check again, a collaborator's move or a deletion taking it
out of the walk; what each step changes, for its undo, a move from a
resolved card keeping that card's step as its `prior`; the picture asked
of the old file and not through a share link, the crop's box and the old
rectangles in it, the quote comparison, a long passage compared quickly, an old quote
clipped to a `prev`'s length compared only as far as it goes; Discard:
which of the two it does, a note of spaces no note, an empty child or a
thread keeping it, only a lost one discarded; a bare one deleted as one
tree edit and out of the queue, its undo putting it back in its place,
lost, at the top's end when its parent has gone, nothing when it is back
already; one with a note kept with its quote, colour, note and children,
its position its page alone, out of the page's highlights and the queue,
the rest of the tree keeping its identity, its undo putting the lost
position back, nothing over a later change; after Discard the card as it
was, Discarded, the deleted one kept in the walk where it stood, lost and
to check again when it is back, out of the walk once it is placed
elsewhere, one kept as a note leaving the walk when someone deletes it;
`placeBox`; `blockPlace` and `insertBlockAt`),
`logseqPdfModel.test.mjs` `placedToCheck`, `roughMatchPercent`,
`acceptedPosition` (`doc_id` going too), `placeToReview`, `closestPlace`
(read defensively), `closestPlacement` and `reviewUndone` (nothing taken
back over a later change), `tasks.test.mjs` `relocationSummary` and the
superseded run's silence (`relocationSuperseded`), `settings.test.mjs` the
two thresholds' defaults and clamping, and `libraryUtils.test.mjs`
`attachmentFile`. The browser suite (`npm run e2e -- --only "<part of the
name>"`): `pdf.mjs`'s step "replacing the PDF moves a highlight to its
text" (the job's result and its default thresholds in the params, the
hollow look on a light and a flipped page and in the notes, a comment
leaving it hollow, attach mode and move mode ending each other; two
renumbered citations matched roughly, with their stored `match`, respelled
quote and `prev`, the dashed outline on a light and a flipped page, no
title, the rows' dashed dots with the percentage, no Accept on an exact
match; the review card on hover with its header, the before picture with
the old rectangles over it, the struck and marked citation and "2 of 3"
(the hollow one first), its buttons on one line, staying while the
pointer is in it and closing when it leaves; Next; Accept on the card
leaving the card's box, header, loaded picture, comparison, count and
every button's box the same within a pixel, Accept reading "Accepted",
pressed, its title saying a press undoes it, Move… still enabled;
Accepted pressed again dashing the highlight again, saved, the card the
same, and Accept again; the accepted card staying while the pointer rests
on another dashed highlight; Move… from it and a selection, "Moved",
pressed, the header as before and Accept disabled, and Moved pressed again
putting the card back on "Accepted", the position saved as accepted; Next
wrapping to the hollow one, whose card reads "Not found in this version",
"1 of 3" (the accepted one kept in the walk), no Accept and a before
picture cut from the first file, on to the rough one, "2 of 3", back with
Previous, and Previous round from the first to the accepted one, "3 of 3",
Accepted pressed; the hollow one's card on hover; the Document menu's
"Review 2 highlights", Escape, and "Accept 1 rough match" for the rest,
"Review 1 highlight" left; Move… on the hollow one's card and a selection
reopening it, "Moved" pressed and the count "All reviewed", no Close, and
Moved pressed again putting the highlight back hollow with its `doc_id`,
saved, "1 of 2"; the move from the row's button with no
translation, a second move by an area drag clearing the quote and putting
no picture in the chat; and an account at 100% whose next renumbered
citation is left lost with its `closest` place, its card showing "Closest
match", the citations compared and a before picture of the second
version, Show drawing one outline per rectangle, and Use closest match
placing it solid there with the candidate's rectangles and quote, the
position free of `lost`, `closest`, `doc_id`, `approx`, `match` and
`prev`, the card the same, following it, Use closest match reading
"Placed", pressed, Discard disabled and the count as it was or "All
reviewed"); its step "a hollow
highlight's card leaves nothing on screen" (nothing of the card
on screen, its picture included, while its highlight is scrolled out of
sight with the pointer in the card, from the scroller and from the wheel with the card opened from the Document
menu, and the card on its highlight again, picture and all, when it
scrolls back; Discard on the bare one, its title saying it deletes: the
card the same where it stood within a pixel, Discard reading "Discarded",
pressed, Move… disabled and Next enabled, the highlight gone from the page
and the store, and Discarded pressed again bringing it back hollow with
its file and quote, saved; Next and Discard on the one with a note, its
title saying it keeps it: the row and its note staying, the position its
page alone, the quote kept, pressed again hollow again, a second Discard,
Previous and Next coming back to it Discarded, and "Review 1 highlight" in
the Document menu); its step "a hollow
highlight's card follows Show to a far page" (Show on a closest match six
pages on keeping the card in sight beside the outline, without a scroll;
Next to a highlight lost from a page the new version dropped, its card in
the viewer saying so, "2 of 2", a before picture of that page in the first
file, still open past the four seconds' wait, Next going round to the
first, the Document menu's "Review 2 highlights") and `files.mjs`'s step "the
highlight follows its text" (the Document menu's Replace the PDF…, the
summary in the status pill, the highlight placed dashed with its note, a
second tab following).
