# Highlights after a PDF is replaced

A page's PDF can be replaced by another file: a paper compiled again from
its TeX, the published version in place of the arXiv one (`PUT
/pages/{id}/attachment`, the Document menu's **Replace the PDF…**, a build
script, [api.md](api.md) "Pages"). A highlight's place is a page and
rectangles in that page's frame (`pdf_position`, [api.md](api.md) "The
highlight shape"), good for the file it was made on: in the new file every
line has moved. What stays is the text. So after a replacement each
highlight is found again in the new file by its passage and moved to where
that passage is now; one whose passage is nowhere is marked `lost` and shown
hollow where it was, until the person moves it or a later run finds it.

Code: `gamma/highlight_relocate.py` (the search and the write),
`pdf_text.GlyphPages` (the glyphs, a page's frame and pictures),
`routers/pages.py` (`replace_pdf`, `_start_relocation`, `POST
/api/jobs/relocate-highlights`), `blocks_store.attachment_file` (the stored
file a page shows); in the web app `pdf/PdfViewer.jsx` (the
hollow look, the tip's move button), `editor/BlockTree.jsx` (the row's
hollow dot and move button), `app/App.jsx` (move mode, the Document menu's
rows, what the replacement says), `tasks/taskKinds.js` and
`tasks/useAppJobs.js` (the task row, the summary when a run ends).

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
are `{page_id, title, doc_id}`,
the file it places for, and its key is `<page id>:<doc_id>`: one run per
page and file. A replacement first stops the same account's run for the
file it replaced, queued or running, since that run's result would be for a
file the page no longer shows; the new run never waits for it, their keys
differing. A run already past its last stop finishes on its own; a write
after the replacement is refused (below). Another member's run for the old
file is left alone and fails at its write the same way, writing nothing.

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
groups, text boxes, sheets and a position of its page alone are not its
business). It opens the new file (`ai_context.pdf_path`: the stored copy,
or a URL-opened PDF fetched as the indexer fetches it), reads every page
(phase `reading`, unit `pages`), places each highlight (phase `matching`,
unit `highlights`), and closes the files and all it read from them. Once
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
else "the page's PDF changed again before the highlights were placed". Either way nothing is
written. The op for a highlight deleted since the snapshot, or whose
`pdf_position` or `quote` changed (a person moved it meanwhile), is
dropped: the highlight is left as it is and counted in `changed` instead
of its outcome. The rest go as `set` ops in batches of at most `MAX_OPS`
on the page, client `relocate`, actor the owner, all committed together,
then each batch's `after_commit`: ordinary ops, so they reach the open tabs
over the page socket and the viewer redraws. The result is `{highlights,
moved, kept, lost, approx, changed}`, the last five adding up to the
first.

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
A position that already names a file keeps it. A backup's restore keeps
`doc_id`, `lost` and `approx` with the rest of the position
(`highlights.from_scaled`, which every restored position passes through).

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
found in that file, so only its quote is looked for. And when a text
highlight's stored quote and the text read under its rectangles do not
agree (both folded, neither holds the other, a likeness below
`QUOTE_AGREES`, 0.6), the rectangles do not belong to that file: the quote
is looked for, without context.

What the run writes carries no `doc_id`: a moved or approx position is a
new one, measured on the new file, and a kept one that named a file is
written once more without it. A lost one keeps it (the old position with
`lost: true`), so a later run still reads that highlight from the file it
was made on.

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
of those glyphs alone, read as a selection reads: each stretch of covered
glyphs is one piece (a mark without a box does not break it, and two
stretches the text already joins, a word hyphenated at a line's end or a
line that ends in a covered space, are one), the pieces are joined with one
space, and a word hyphenated where the highlight skips a line break is
joined whole. So a caption or a margin note the file draws between two
lines of the highlight is not in the needle. The context is the 40
characters (`CONTEXT_CHARS`) before the first piece and after the last.

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
quote that disagrees with it) the stored `quote` is the needle and there is
no context.

**The search** (`find_passage`) looks on every page of the new file.

- Every exact occurrence is a candidate if it starts and ends on word
  edges: "ion" in "motion" or "fig. 1" in "fig. 12" is not the passage.
  The rule is lifted on a side where the context shows the old passage
  itself began or ended inside a word.
- From four words and twelve characters on, the word-by-word search
  (`_fuzzy_on_page`): the page's words equal to the needle's vote for where
  the needle would start, the six best-voted starts are aligned against the
  needle (`_align`, a semi-global edit distance over words: the run may
  start and end anywhere in its window; on a tie the later start, so a
  dropped first word never pulls in the word before it), and a run within
  the budget is a candidate. The budget is one edit per six words, at least
  one and never more than eight. A word changed, added or dropped costs
  one; a word differing in its punctuation a quarter; a word spelled nearly
  the same (a character ratio of 0.8 or more, both at least four letters) a
  half; a word with a digit, a single letter or symbol (a variable, an
  operator, a Greek letter) or a negation cannot be changed, added or
  dropped: "4 K" is not "5 K" and "is not" is not "is". Only the prose
  punctuation around it may differ, for a quarter, as around any word
  ("Fig. 3," for "Fig. 3.", "(4)" for "4", "x, y and z" for "x, y, and
  z"); a prime, a factorial or angle brackets are the symbol's own
  (`_PROSE_PUNCT`). It runs when there is no exact
  candidate, and also when there is a context and no exact candidate stands
  in surroundings that read like it, so a passage reworded in place beats a
  verbatim copy elsewhere. A page, or a window, that lacks too many of the
  needle's words to hold such a run is passed over without aligning
  (`_enough`), which changes no answer.
- When no page holds the passage, exactly or word by word, and the
  word-by-word search would take it, across each page break (`_across`):
  the needle's head is aligned against the last words of one page and its
  tail against the first words of the next (the needle's length and 20
  more, `JOIN_SLACK`), both free to start and end anywhere, so the footer,
  footnotes and running head between them ("Phys. Rev. A 104, 033101
  (2021)") cost nothing; the cut that costs least within the whole
  passage's budget wins, and each part must cover some words of its page. The highlight goes to the page with more of the
  passage's words (the first on a tie), covering that part, and its quote
  becomes that part as the new file spells it. A shorter passage is not
  looked for across a break: a word or two found at some page break mean
  nothing.

Among the candidates, one whose surroundings reach `CONTEXT_OK` (0.6, the
mean likeness of the sides the context has to the text beside the
candidate; unrelated 40-character windows score about 0.3, lightly edited
ones 0.83 to 0.9) comes first, then the fewest edits, the closest
surroundings, the page nearest the highlight's old page, and the place
nearest the old one: the centre of the stored `boundingRect`, as fractions
of its page, against the centre of the candidate's glyph boxes, as
fractions of the new page. The punctuation a match carries at its edges
that the needle does not ("cooled." for "cooled") stays outside.

**The rectangles.** The glyphs the match spans give one box per line of
MuPDF's; two lines sharing a baseline (a span of math inside a sentence)
merge (`rects_for_span`). Each box goes to the viewer's frame through
`pdf_point_to_viewer` and the page's frame (`GlyphPages.frame`: the rect
MuPDF shows, the crop box clipped to the media box, taken back to the
glyphs' space through the same matrix the glyphs use, and the page's
/Rotate), the frame's size being the page as shown (`display_size`: the
crop box, its sides swapped on a quarter turn), so a rotated or cropped
page places like pdf.js draws it. A position found within half a percent of
the page of the old one, on the same page, of a highlight not lost, counts
as **kept**; any other is **moved**. A passage found by the
word-by-word search with edits, or a part across a page break, also
rewrites the highlight's `quote` to the text as the new file spells it,
when that differs from the stored one, since that is what the highlight
covers now; a kept highlight too.

**The cost.** The matching is pure Python and holds the GIL, so it naps for
2 ms after every 10 ms of matching (`_breathe`, `NAP`, `NAP_EVERY`) and the
server's requests go on meanwhile. It reports a bare progress once per page
the word-by-word and page-break searches cover, and before it reads a
page's pictures, so a stop lands within a page. A file's pages past
`pdf_text.MAX_PAGES` (5000) are not read: a highlight there is a passage
not found. What a run costs in time and memory, and the requests it
slows: [research/highlight-anchoring.md](../research/highlight-anchoring.md#measurements).

## Areas

An area highlight (`area: true`) has no quote. It is placed, in this order,
by:

- **the text inside it**, when its own file is at hand and the needle read
  there has at least twelve characters and two words (a caption, an axis's
  labels, an equation's letters): the passage is found as above (only the
  whole of it: a part across a page break would put the box askew) and the
  box is put around it as it sat around the old anchor, the same offsets,
  as fractions of the page, clamped to the page (`_box_by_offsets`);
- **the picture it covers**, in its own file: the image drawn under at
  least half of the box (or of which at least half is under the box),
  known by MuPDF's digest of its data with its pixel size
  (`GlyphPages.images`), found where the new file draws the same data, on
  the page nearest the old one when it draws it more than once (a figure
  embedded again by pdfTeX; a publisher's re-encoded figure is another
  picture); the box follows it by offsets;
- **its page's other highlights**: every highlight placed from the same
  old page by its text or picture, a kept one counting as no shift (lost
  ones count for nothing); when they all landed on one new page and their
  shifts (of the centre, as fractions of the page) agree within 15%
  (`RIDE_SPREAD`) across and down, the area goes to that page shifted by
  their median and is marked `approx`, drawn under a dashed edge, since
  this is an estimate. A median that leaves it on its page within half a
  percent keeps it, and so does a shift that would carry any edge of it
  off the page;
- else it **stays** where it was, counted as kept.

An `approx` area stays `approx` through later runs, whatever places it: the
text or picture under an estimated box is only what the estimate happened
to cover, so finding it again confirms nothing. Placed again on the same
file it is kept with its flag (no write); after another replacement it
follows what lay under it and is counted `approx`. A move by hand clears
the flag.

## Lost, approx, and moving by hand

A highlight whose passage is not found keeps its rectangles (and its
`doc_id`) and gets `pdf_position.lost: true`, written once; a run that
finds it later, even in the same place, or a move by hand, writes a
position without the flag. The viewer (`PdfPage`) draws it hollow: a branch
of its inline style ahead of the others, so whatever else it is (an area,
commented, a link), it is a dashed line in the stronger shade of its
colour that underlines a commented passage (`--hl-dark-line`), with no
fill, on a flipped page too. Its rects carry the class `pdfHlLost`, which
has no style; the browser tests find a lost one by it. `approx` keeps the
area's look under a dashed edge (the dash rides inside the border
shorthand). The title says what happened, and to an editor how to fix it:
"This highlight's text was not found in the current PDF — right-click to
move it to a selection", "Placed by estimate after the PDF was replaced —
right-click to move it" (a viewer who may not edit reads the first half
alone). In the notes, a lost row's dot is hollow (`.highlightDot.lost` in
app.css, its colour the inline `border-color`), the dot's title says so,
and for an editor on a page with a PDF a button beside it (the row's
hover controls, like the ⊕ of a note without a highlight) starts a move. Exports and the AI read the position as any
other; the flags are the viewer's.

**Moving a highlight** (App's `moveHighlightId`): a highlight's context
menu on the page offers **Move to a selection…** to an editor, the lost row
its button. Move mode and attach mode (a note's ⊕ waiting for a click on a
highlight) end each other (`startHighlightMove`, `startAttachMode`), so one
banner shows and the PDF's next selection means one thing. The banner says
what to do; the next selection on the PDF, text or an area drag (the area
key, [hotkeys.md](hotkeys.md#held-keys)), shows the tip with one button,
*Move the highlight here* (`PlainTip`'s `onMove`), and translates nothing,
even with translate on select on. `moveHighlightTo` sets the highlight's
`pdf_position` to the selection's (no `doc_id`, `lost` or `approx`) and its
`quote` to the selection's text; an area drag makes it an area with no
quote, as one made the usual way. It is a tree edit like any other, so it
is undoable and reaches the others, and the highlight flashes. Escape,
Cancel, another page, or another file on this page ends the mode. It
serves any misplaced highlight, not only a lost one.

**What the tab says.** The run's Background tasks row reads "Placing the
highlights of “…”", its phases "Reading the PDF" and "Finding each
highlight's text", with a stop button until it writes. When it ends, the
tab that started it (by the replacement, whose answer it adopts, by the
menu or by Start again) says what it found: "Highlights placed in the
PDF: 2 moved, 5 unchanged, 1 not found." (approx counted as moved), then
how many someone moved meanwhile (`changed`), then, when one is lost, how
to move a hollow highlight. A run that failed or was stopped says so,
unless the PDF was replaced again meanwhile: a run for another file of the
same page started after it (`taskKinds.relocationSuperseded`, over the
listing the run ended in) means the replacement stopped it or had its write
refused, and the newer run reports instead. While one of the account's runs
for the page is going, **Find highlights in this PDF** is disabled, its
title "This page's highlights are being placed — see Background tasks".

## Limits and open items

- A highlight whose covered glyphs MuPDF reads in an order the new file
  does not (a selection across two columns that the old file draws
  interleaved, line by line) is found only within the edit budget; its
  passage is otherwise marked lost. Splitting the needle and finding its
  parts apart is open.
- Without its file (purged, `unstored`, a lost highlight without
  `doc_id`) the stored quote is the needle and there is no context: two
  identical short passages are told apart by page and place alone, and a
  quote the viewer stored with a review copy's margin line numbers in it is
  not found in a file without them (numbers are words the search may not
  drop). Keeping a passage's context with the highlight when it is made (a
  `TextQuoteSelector`'s prefix and suffix, as the W3C model has it) would
  serve such runs; see
  [research/highlight-anchoring.md](../research/highlight-anchoring.md).
- A passage of fewer than four words or twelve characters that the new
  file splits at a page break is not found.
- A page whose PDF is shown from a URL alone (no `doc_id`, and an address
  that is not an upload's) has no file a run can read: no run starts for
  it, and its highlights stay where they were.
- Ink groups and text boxes stay where they were; their pages may have
  shifted. Carrying them along with the page's highlights (the ride-along
  rule) is open.
- An area over a vector figure without text and without an embedded image
  rides along or stays.
- A run's ops are the server's: no tab's undo history has them. A highlight
  moved wrongly is moved back by hand.
- The passages are read with MuPDF, the viewer's text with pdf.js; a quote
  stored by the viewer that MuPDF reads differently (spacing in a
  mathematical line) is found by the word-by-word search or not at all.
- An area drag in move mode also goes to the chat as a picture, as every
  area drag does.

## Tests

`backend/tests/test_highlight_relocate.py`:

- the page text: the folding, hyphenated words, the compact glyph store,
  number-only lines left out;
- the passage under a highlight: across a line's end, across two columns
  with a caption drawn between them, an area over a table's column measured
  alike in both files;
- the search: an exact passage on another page; context, then page, then
  place; the word-by-word search's edits and protected numbers, the
  punctuation around a number or a variable allowed to change; a dropped
  first word; word edges; a passage reworded in place against an exact copy
  elsewhere; the nearest place measured on the page; a passage split by a
  page break; the pruned search against aligning every window; a stop
  within a page;
- the rectangles on a plain and on a rotated cropped page;
- `place_all`: two files with each outcome; a file gone; a quote that is
  not the text under the rectangles; a kept highlight's new spelling;
  margin line numbers either way; a new file without text; a lost
  highlight not written again, and one found again; the page cap; the
  ride-along's cases; an approx area kept as an estimate by a second run
  on the same file and after the next replacement; an area with nothing to
  go by;
- the endpoint and the job: the PUT's run and its answer, the stamps and
  `relocation_error` when the jobs are full, a run that never wrote, a lost
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
`test_highlight_shape.py` and `test_restore_consistency.py` that a restore
keeps `doc_id`, `lost` and `approx`; `frontend/tests/tasks.test.mjs` the
superseded run's silence (`relocationSuperseded`) and
`libraryUtils.test.mjs` `attachmentFile`. The browser suite
(`npm run e2e -- --only "<part of the name>"`): `pdf.mjs`'s step "replacing
the PDF moves a highlight to its text" (the job's result, the hollow look
on a light and a flipped page and in the notes, a comment leaving it
hollow, attach mode and move mode ending each other, the move from the
row's button with no translation, a second move by an area drag clearing
the quote) and `files.mjs`'s step "the highlight follows its text" (the
Document menu's Replace the PDF…, the summary in the status pill, the
highlight moved with its note, a second tab following).
