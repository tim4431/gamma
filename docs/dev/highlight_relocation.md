# Highlights after a PDF is replaced

A page's PDF can be replaced by another file: a paper compiled again from
its TeX, or the published version in place of the arXiv one. The
replacement comes through `PUT /pages/{id}/attachment` (the Document menu's
**Replace the PDF…**, a build script, [api.md](api.md) "Pages"), from a
Zotero link bringing its item's file
([zotero_sync.md](zotero_sync.md#pdfs)), or in the desktop app from a file
on this computer that changed: a synced vault's PDF, a vault of one file,
or a PDF read where it is ([synced_vaults.md](synced_vaults.md#a-pdf-read-where-it-is)).

A highlight's place (`pdf_position`, [api.md](api.md) "The highlight
shape") is a page and rectangles that hold only for the file it was made
on. After a replacement each highlight is found again in the new file by
its passage:

- Found exactly, it is drawn solid, as it was.
- Found changed a little (a renumbered citation, a reworded phrase) but
  alike enough, it is drawn dashed, pending until the person accepts, moves
  or discards it on its review card.
- Found nowhere alike enough, it is marked `lost` and drawn hollow where it
  was. A lost text highlight keeps the place most like it as its closest
  match when that reads at least half alike.

Every run matches a highlight from its reference, the last place someone
confirmed, never from a pending guess ([The reference](#the-reference)).

Code:

- `gamma/highlight_relocate.py`: the search and the write.
- `gamma/highlights.py`: the position's flags, `prev` and `closest`
  (`previous_place`, `valid_prev`, `valid_closest`, `from_scaled`).
- `pdf_text.GlyphPages`: the glyphs, page frames and the PDF's own
  pictures.
- `routers/pages.py`: `replace_pdf` over `replace_attachment` (the Zotero
  link's path too), `_start_relocation`,
  `POST /api/jobs/relocate-highlights`.
- `documents.model.file_hash` (over `page_document`): the stored file a
  page shows.

In the web app:

- `pdf/PdfViewer.jsx`: the dashed and hollow looks, the closest match's
  outline, the tip's move button.
- `pdf/HighlightReview.jsx`, `pdf/highlightReview.js`: the review card, its
  queue, steps and note, the next one after a decision, Discard's step.
- `shared/lib/wordDiff.js`: the card's word comparison.
- `shared/model/blockModel.js`: `placedToCheck`, `placeToReview`,
  `roughMatchPercent`, `acceptedPosition`, `closestPlace`,
  `closestPlacement`, `reviewUndone`, `blockPlace`, `insertBlockAt`.
- `editor/BlockTree.jsx`: the row's dashed and hollow dots, its move
  button.
- `app/App.jsx`: Accept, Use closest match, Discard and a step's undo, the
  notes following the card, move mode, the Document menu's rows, the
  replacement's message.
- `app/prefDefs.js`, `settings/SettingsDialog.jsx`: the thresholds.
- `tasks/taskKinds.js`, `tasks/useAppJobs.js`: the task row and the summary
  when a run ends.

## The job

A `PUT /pages/{id}/attachment` that changes the stored file of a page with
highlights placed on its PDF does two things. In its own transaction it
stamps each highlight with the file it was measured on
(`pdf_position.doc_id`, the next section). After the commit it starts the
`relocate-highlights` job ([tasks.md](tasks.md)), returned as `job` beside
the block. The same file again, or the same stored file from another
address, stamps nothing and starts no run.

The stored file a page shows is its document's hash (`doc.hash`:
`documents.model.file_hash`, `pageDocument.js` `fileHash`). The library's
Duplicate gives a copy the same file marked `copy`: it shows that file
without owning it, so its own hash (`own_hash`) stays empty, as the lookups
of the page of a file expect.

A PDF shown from an address with no hash is no stored file. (A
URL-opened PDF has one, the URL's hash, and is fetched as the indexer
fetches it.) A replacement by such a URL starts no run and answers
`relocation_error` "the PDF is not stored on the server" (`NOT_STORED`).
The Document menu's "Find highlights in this PDF" is disabled for such a
page, its title saying why.

The job's owner is the request's account, a session's or a write-scope
integration token's ([tasks.md](tasks.md) "The API"), or a Zotero link's
owner. A share link never reaches the endpoint.

A Zotero link holds back a highlight still naming an old file, a dashed one
and a lost one as waiting annotations, each with its own reason
(`zotero_annotations.hold_reason`,
[zotero_sync.md](zotero_sync.md#annotations)). The link is told once the
job ends, however it ends (`zotero_sync_pdfs.relocation_ended`). A PDF
replaced here waits for that before the link decides it
([zotero_sync.md](zotero_sync.md#the-plan)).

The job's params are `{page_id, title, doc_id, min_match, exact_chars}`:
the file and the thresholds ([The thresholds](#the-thresholds)). Its key is
`<page id>:<doc_id>`, one run per page and file. A replacement first stops
the same account's queued or running run for the outgoing file. It never
waits for it, since their keys differ. A run past its last stop, or another
member's, finishes and has its write refused (below).

A run that cannot start leaves the replacement standing. That happens when
the account has 20 background tasks queued or running (`jobs.start`'s 429),
or while a run for the same page and file is still stopping. The answer
then carries `relocation_error` instead of `job`, and the log has
`[relocate] <page>: the job did not start (<reason>)`. The tab says "PDF
replaced. Its highlights were not placed (…): choose Find highlights in
this PDF in the Document menu to place them." The stamps stay for a later
run.

The Document menu's **Find highlights in this PDF**, and a finished task
row's Start again, start a run by hand (`POST /api/jobs/relocate-highlights
{page_id}`). It needs a session account that edits the workspace (a token
gets 403). It answers:

- 200 and the run, also one already going for the page's current file.
- 409 only while that run is still stopping.
- 429 when the account's jobs are full.
- 400 for a page without a PDF, with one not stored (`NOT_STORED`), or
  without highlights placed on it or known by their quote.
- 404 for no such page.

The run (`relocate`) works from a snapshot of the page's placed highlights
(`page_highlights`, `highlights.is_highlight`): highlights and link regions
with rectangles, at any depth, and the text highlights known by their page
and quote alone (`unplaced`: no rectangles, no area, a quote; below). Ink
groups, text boxes, pictures, sheets and any other position naming only its
page are skipped. It opens the new file
(`ai_context.pdf_path`), reads every page (phase `reading`, unit `pages`),
places each highlight (phase `matching`, unit `highlights`), then closes
the files and drops what it read. A stop asked for before the last matching
report lands there. Then the run sets `stoppable=False` and writes. A new
file not stored, unreadable, or with no text layer on any page fails the
run rather than leaving every highlight lost.

**A highlight known by its quote alone.** A synced vault reads a `[!quote]`
callout that links the page's own PDF and carries no anchor Gamma knows (a
vault copied from another computer) as a highlight with its quote and page
and no rectangles ([synced_vaults.md](synced_vaults.md#links-and-highlights)),
and starts a run on the page's current file for it
(`LocalRemote.place_highlights`; a run that cannot start now waits for the
vault's next whole round). The run looks for its quote, the page it names
first, and draws it dashed wherever it is found, an exact copy too, since
the words may stand twice: `match` its confidence and `prev` that page and
quote with no rectangles. One not found stays as it is, counted `lost`. A
pending highlight whose `prev` has no rectangles and whose own are this
file's is left for the person, so a run over the same file again writes
nothing.

**The write** (`_write`) is one compare-and-set, in one transaction under
the page's write lock. It fails, writing nothing, when the page is no
longer live ("the page was deleted before its highlights were placed —
restore it and choose Find highlights in this PDF"). It fails too when the
page no longer carries the file placed for (`file_hash`: "the page's
PDF changed again before the highlights were placed"). A highlight deleted
since the snapshot, or whose `pdf_position` or `quote` changed, is left
alone and counted in `changed`. The rest go as `set` ops in batches of at
most `MAX_OPS`, client `relocate`, actor the owner. They commit together,
then each batch's `after_commit` sends them to the open tabs over the page
socket.

The result is
`{highlights, moved, kept, lost, approx, changed, min_match, exact_chars}`:
five counts adding up to `highlights` ([What a run
writes](#what-a-run-writes)), then the thresholds used.

## Which file a position was measured on

`pdf_position.doc_id` names the stored file (a page's `doc.hash`, the stem
it is stored under) a position's rectangles were measured on, when that is
not the page's PDF. Without it they belong to the current PDF, so the
viewer, the imports and a move by hand never write the field. The previous
file is not enough. A run that never wrote, a highlight left lost, or a
wrong file replaced again leaves rectangles of an older one.

The replacement writes the stamps beside the marks on the blocks imported
from the old file's annotations, in batches of `MAX_OPS`. Every placed
highlight naming no file gets the outgoing file's id, on a duplicated page
the original's file it shows. A page shown from a URL alone gives
`unstored` (`storage.UNSTORED`). Uploads are named by content hash, so
`unstored` names none. A position that names a file keeps it. The same
write drops a lost highlight's `closest`, a place in the outgoing file that
the card would point into if no run followed.

A backup's restore keeps `doc_id`, `lost`, `approx`, `match`, `prev` and
`closest`, each only in its own form (`highlights.from_scaled`, [What a run
writes](#what-a-run-writes)).

The run reads each highlight (passage, context, anchor, picture) from its
reference's file ([The reference](#the-reference)): its `doc_id`, or a
pending one's `prev.doc_id`, else the new file. Another file is looked for
among the workspace's uploads only (`find_upload_file`, never fetched),
each opened once. A `doc_id` that is no file name names none. A lost
position without `doc_id` was not found in the new file, so only its quote
is looked for.

A file not stored logs
`[relocate] <page>: the file <id> is no longer stored; its highlights are placed by their quotes`,
and they are, without context. That happens on a copy that never received
the file (a mirror, a share's export), after it was removed outside the
orphan bookkeeping, and on a page that reads its PDF in place (a work
folder's build, an opened PDF, a vault's PDF) with no comment thread open:
the outgoing bytes were never stored, and the path holds the new ones.

While a thread on such a page is open, every build it shows is stored
(`feedback/kept.py`, [feedback.md](feedback.md) "The commented version
kept"): the bytes at the thread's open, then the incoming ones first at
each replacement (`vault_sync.LocalRemote.replace_pdf`,
`linked_files._take_bytes`). So the outgoing file the stamps name is in
the store, which `find_upload_file` reads before a linked path, the run
places an area by the text inside it however many builds follow, and the
replacement's `keep_old_days` applies to it once nothing holds it. The
thread's own version is held until its resolve. A highlight made on a
build that was replaced before its thread was opened is placed by its
quote, as without a thread: nothing is stored at the first highlight.

The stamp and a `prev`'s `doc_id` are references (`storage.upload_refs`).
So a pending highlight holds the file it is matched from through any number
of replacements. Accept, Use closest match, a move, a Discard or a solid
placement drops the name and hands the file to the orphan bookkeeping (30
days, or a sync's `keep_old_days` of 1 to 30, [user_db.md](user_db.md)
"Stored files"). `_write` claims the files its written positions name
(`upload_gc.claim`), which drops that shorter life.

A text highlight's stored quote and the text under its rectangles agree
when, both folded, they read alike from `QUOTE_AGREES` (0.6, difflib's
ratio, `_agrees`). One that holds the other and is at least 0.6 of its
length agrees at once. A shorter one does not count by being held: a
rectangle now over a short line may cover a word of the quote. When they
disagree, the rectangles are not that file's, and the quote is looked for
without context.

When they agree on the page's new file (a position without `doc_id`, or one
naming that file), the highlight is kept without a search. Its `doc_id`
goes, and a `lost` mark goes with its `closest`.

A position the run places or keeps names no file. A lost one and an area
nothing placed (`approx`, [Areas](#areas)) name their reference's file in
`doc_id`. A dashed placement keeps its reference in `prev`, which its card
compares against. Later runs read each highlight from its reference's file.

## Finding a passage

**The text.** `PageText` reads a page's glyphs as one string, each
character remembering its glyph. `GlyphPages.page` gives each character
with its box, and a boxless break after each MuPDF line. Lines are joined
with a space, and a word hyphenated at a line's end is joined whole. Every
character is folded: dashes to one hyphen, curly quotes straight, soft
hyphens dropped, ligatures to letters (NFKC), case folded, whitespace
collapsed. `normalize_quote` folds a stored quote, pdf.js's reading of a
selection, the same way.

A MuPDF line holding only a number of one to five digits is left out with
its glyphs, in both files. Such lines are margin line numbers (lineno) or a
page number the other version lacks. Every index counts the glyphs left. A
run holds every page of the new file until matching ends, hence the chars
as one string and the boxes as one flat array.

**The passage.** In the file the highlight was measured on, its rectangles
go back to the glyphs' space through `pdf_export.viewer_rect_to_pdf`. The
mapper takes the stored frame's size and the page's view box and rotation
(`GlyphPages.frame`). The glyphs whose centre they cover are the
highlight's (`pdf_text.covers`, as the annotation import's `quote_under`).
A text highlight's needle is those glyphs' text alone, so a caption drawn
between its lines is left out. It is read as a selection reads
(`_stretches`, `_joined`):

- Each run of covered glyphs is one stretch. A mark without a box does not
  break it.
- Two stretches the text already joins are one: a word hyphenated at a
  line's end, or a line that ends in a covered space.
- The stretches are joined with one space. A word hyphenated where the
  highlight skips a line break is joined whole.

Rectangles a little off a file's glyphs (a frame a few percent off scale, a
line's rectangle running into the next column) read the quote's neighbours
too. Such a reading still agrees with the quote (`_agrees`). Searched for
as it is, its extra words would get rectangles, and changed ones would make
the match rough. In a file other than the page's new one, a reading past
either end of a copy of the folded quote gives way to that copy
(`_on_quote`). It is one stretch, the copy sharing most text with the
reading (the first on a tie). A reading within the copy stays, its
rectangles saying which words are the passage (a caption they skip). So
does one that no copy touches, such as a quote pdf.js read otherwise or one
holding a margin line number. On the new file, the reading as it is decides
([Which file](#which-file-a-position-was-measured-on)). The context is the
40 characters (`CONTEXT_CHARS`) before the first stretch and after the
last.

The highlight's **pieces** (`_pieces`, `Piece`) are its stretches grouped
as the file read them. Stretches with only a line's end between are one
piece. A new piece begins where the file read words the highlight does not
cover: a caption between two columns, a footnote, a mark the rectangles
miss. Each piece has its own 40 characters either side and the count of
words skipped before it.

An area's needle is the text from its first covered glyph to its last. Its
anchor is that text's line boxes (`rects_for_span`) as one box, in
fractions of the page. It is measured as the new file's match is, so an
unchanged area is found where it was. The covered glyphs alone would fail
over a table's column, which the file draws row by row. They are the
needle, and their box the anchor, only when the span is longer than twice
their count plus ten.

Without the file, the stored `quote` is the needle, with no context and in
one piece. That covers a file gone or `unstored`, a lost position without
`doc_id`, and a quote that disagrees with its file.

**The candidates** (`find_passage`) are looked for on every page of the new
file, for a needle of at least three characters (`MIN_NEEDLE`):

- Every exact copy on word edges, so "ion" in "motion" or "fig. 1" in "fig.
  12" is none. The context lifts the rule on a side where the old passage
  began or ended inside a word.
- The rough search's places (`_rough_on_page`), for a needle of
  `exact_chars` characters or more ([The thresholds](#the-thresholds)),
  whatever its word count.
- With no exact copy anywhere, the passage across each page break
  (`_across`), for a needle of `exact_chars` characters and two words.
- With no exact copy anywhere, a passage the old file read in several
  pieces, found piece by piece (`_by_pieces`).

**The rough search** runs when there is no exact copy. With a context, it
also runs when no exact copy's surroundings fit it from `EXACT_SURE` (0.9).
Each page word whose core (the word less its edge punctuation) is a needle
word votes for a start. Around the six best-voted starts (`MAX_SEEDS`, none
within the slack of a better one: one word more than the give-up bound
below), `_rows` and `_align` align the needle word by word. The alignment
is semi-global, in a window of the needle's length plus the slack either
side. On a tie the later start and the earliest end win, so a dropped edge
word never pulls in its neighbour. No word is frozen, not even a number or
a negation.

A page word added costs one, and a word read as another one minus their
likeness (`_word_cost`). A passage word dropped costs `DROP`, one plus a
millionth. So a run keeping the passage's last word beats one as cheap that
stops short: "in the cryostat." read in "in a new cryostat." keeps
"cryostat.". Likeness (`_ends_alike`) is twice the characters two words
share at their start and, past it, at their end, over both lengths. For one
change it equals difflib's ratio, twenty times faster. Two words that both
have digits take the mean of that and their likeness with digits read as 0.
"[27]," for "[25]," then costs 0.1 and "5" for "4" 0.5, so a changed number
at an edge is read as the new number, not dropped. A pair under half alike
costs `DROP`, so a last word swapped for an unlike one ("method." as
"approach.") is left out. The costs cannot tell it from a dropped word
before the next sentence's first.

**Across a page break** (`_across`), the needle's head is aligned against
one page's last words and its tail against the next page's first. Each
window is the needle's length plus 20 (`JOIN_SLACK`). Both parts start and
end freely, so the footer, footnotes and running head between them cost
nothing. The cheapest cut wins, each part covering some of its page's
words. The candidate, a `part`, is only the part on the page with more of
the passage's words (the first on a tie).

**Piece by piece** (`_by_pieces`), the longest piece (the first of the
longest) is found anywhere. It is found exactly, or roughly from
`exact_chars` characters when no exact copy leads anywhere. Each later
piece must begin, and each earlier one end, within reach of its neighbour
(`_beside`). The reach is the words the old file skipped between them, plus
20 (`JOIN_SLACK`). Each piece is found there (`_piece_hits`): exactly on
word edges, its own context lifting the rule on a side, else, from
`exact_chars` characters, as the cheapest rough run within reach. Of
several hits the cheapest wins (an exact copy first), then one on the same
page, then the gap nearest the old one. A piece may lie on the next or
previous page when its neighbour is within reach of the break. A chain
crosses one break at most. It is then a `part` on the page with more of the
passage's words (the first on a tie), covering its pieces there.

Two heuristic shortcuts keep the rough search fast. An alignment gives up
past a cost of 0.4 per needle word, and at least one (`ROUGH_BOUND`,
`_bound`). A page or a window is aligned only when the needle's words found
there unchanged (up to punctuation) hold half its letters (`ROUGH_PRESENT`,
`_holds`, `_holds_split` across a break). A place past either is no
candidate. Such a place reads, as a rule, far under the default threshold,
though a lowered one could have taken it
([Limits](#limits-and-open-items)).

**The confidence.** A candidate's confidence is difflib's `SequenceMatcher`
ratio of the folded needle and the text found, without its junk heuristic
(`_similar`). Only an exact copy reaches 1.0. Across a break the parts are
joined with a space. Piece by piece, both sides' pieces are, the text
between left out. The alignment's cost only finds the places.

**The ranking.** With a context, a candidate's score is its confidence plus
a fifth (`CONTEXT_WEIGHT`) of the context fit. That fit is the mean
likeness of each side of the context to the 40 characters beside the
candidate. Unrelated windows fit 0.30 at the median and 0.475 at the 99th
percentile, lightly edited ones 0.83 to 0.90. A passage reworded in place
thus outranks a verbatim copy elsewhere while it reads more than about 0.86
like the old one. Reworded further, the highlight goes to the copy, solid
([Limits](#limits-and-open-items)). On a tie the page nearest the
reference's page wins ([The reference](#the-reference)), then the place
nearest it. Nearness runs centre to centre, from the reference's
`boundingRect` to the candidate's glyph boxes, each in fractions of its
page. Punctuation at a match's edges that the needle lacks ("cooled." for
"cooled") stays outside (`_trimmed`).

**The answer.** The best-ranked candidate is the new place when its
confidence reaches the run's `min_match` (0.8 by default). A needle under
`exact_chars` must also be exact. Otherwise the highlight is lost. An exact
match is drawn solid, a rough one dashed ([What a run
writes](#what-a-run-writes)). A run also passes `CLOSEST_MIN` (0.5, set by
hand, the lowest threshold an account can set) as `find_passage`'s
`closest`. A best candidate under `min_match` but at least that alike then
comes back marked `below`. It places nothing, and the lost highlight keeps
it as its `closest`. A needle under `exact_chars` never gets one: its only
candidates are exact, and applied.

**The rectangles.** The match's glyphs give one box per MuPDF line, and two
lines sharing a baseline (math inside a sentence) merge (`rects_for_span`).
A passage found piece by piece gets each piece's boxes alone. Each box goes
to the viewer's frame through `pdf_point_to_viewer` and `GlyphPages.frame`:
the crop box clipped to the media box, in the glyphs' space, with /Rotate.
The frame's size is `display_size`, the crop box with its sides swapped on
a quarter turn. A rotated or cropped page thus places as pdf.js draws it.

A position on the old page within half a percent of the page of the old one
is in the **same place** (`SAME_PLACE`). A lost highlight never is. A rough
match, a `part`, and one found from a pending highlight's `prev` or for a
lost one rewrite the `quote` even there ([The reference](#the-reference)).
The new quote is the text as the new file spells it (`PageText.original`,
`_spelled`), written when it differs. That is its glyphs' characters in the
file's case, whitespace as one space, and a word hyphenated at a line's end
whole.

**The cost.** The matching is pure Python and holds the GIL. It naps 2 ms
after every 10 ms (`_breathe`, `NAP`, `NAP_EVERY`) to let requests run. It
reports a bare progress before it reads a page's pictures and once per page
the rough, page-break and piece-by-piece searches cover. A stop thus lands
within a page. Pages past `pdf_text.MAX_PAGES` (5000) are not read, so a
highlight there is not found.
[research/highlight-anchoring.md](../research/highlight-anchoring.md#measurements)
measures a run's time, memory and the requests it slows.

## Areas

An area highlight (`area: true`) has no quote. Three things can place it,
tried in this order.

**The text inside it** places it when its own file is at hand. The needle
needs twelve characters and two words (`AREA_MIN_CHARS`, `AREA_MIN_WORDS`),
as a caption, axis labels or an equation has. It is found only as a whole,
since a part across a page break would put the box askew. The box keeps its
offsets around the match, in fractions of the page, clamped to it
(`_box_by_offsets`). The match's confidence decides as a text highlight's
does: solid when exact, dashed with its `match` when rough.

**The picture it covers** in its own file places it next. That is an image
the PDF draws, not a picture placed on the page
([pictures.md](pictures.md)). It must cover half of the box or lie half
under it. It is known by MuPDF's digest of its data and its pixel size
(`GlyphPages.images`). The box follows the same data in the new file by
offsets, solid, on the page nearest the old one. A figure pdfTeX embeds
again is the same picture, a publisher's re-encoded one another.

**Its page's other highlights** come third: every highlight placed by its
text or picture, exactly or roughly, from the same old page of the same
file. That file is the reference's: the position's `doc_id`, a pending
one's `prev.doc_id`, or the new file without one, since a shift measured
from another file's page says nothing of this one. A kept highlight counts
as no shift, a lost one not at all. They must all land on one new page,
their centre shifts (fractions of the page) agreeing within 15% across and
down (`RIDE_SPREAD`). The area then goes to that page, shifted by their
median, as an estimate: `approx` without `match`, dashed. A median within
half a percent leaves it where it is, counted as kept.

**Nothing placed it** when it has no neighbours, neighbours on two pages or
disagreeing, or a shift carrying an edge off the page. An area read from
the page's new file, made on it, then stays, counted as kept. One measured
on another file keeps its rectangles and `doc_id`, so a later run reads it
there again. It is marked `approx` without `match` and counted so, written
once with its `prev` those same rectangles. Accepting it drops its
`doc_id`, so a later run on the current file leaves it be. The next
replacement stamps it with the outgoing file, from which it rides along.

A pending area whose `prev` has rectangles and names the page's new file,
its position naming another, is that file put back: a replacement stamps
the outgoing file on every position. It goes back to its reference at once,
without a search: solid, counted as moved or kept, an estimate too. It adds
no shift for others to ride along with, since nothing of it was read. Any
other pending area takes these steps from its reference ([The
reference](#the-reference)). Failing those, it rides along, as an estimate,
with the highlights placed from where its rectangles are now. Their staying
put leaves it as it is, confirming nothing. Without a ride (`unplaced`),
the first case that fits decides:

- With `prev`'s file the page's new file (a run on that file drew it
  dashed), it goes back to its reference solid, counted as moved or kept.
- With its rectangles another file's, it goes back to its reference,
  `prev`'s file as its `doc_id`, still `approx` with that `prev`. So it
  stops naming the file an earlier run guessed it on.
- With its rectangles the page's new file's, it stays as it is.

Apart from going back to a reference measured on the page's own file
(that file put back, or nothing placing it there), an area placed by
estimate (`approx` without `match`) stays one until the person accepts or
moves it. Its text or picture found again, read under its reference, places
it with no `match`. Its neighbours all staying at its reference put it back
there, still `approx` with its `prev`.

## The thresholds

Two numbers, set by hand and not calibrated, decide what the rough search
may apply. `ROUGH_APPLY` (0.8) is the confidence from which the best place
is applied. `ROUGH_MIN_CHARS` (12) is the folded length of a needle or
piece under which only an exact copy counts. They are the defaults. An
account sets its own in Settings → Reading & editing › Replacing a PDF
([settings.md](settings.md) "The Settings dialog"):

- **Apply a rough match from** (`relocateMinMatch`): 50 to 100% in steps of
  5, default 80, a `Stepper` with Reset. At 100% only an exact copy moves a
  highlight.
- **Passages shorter than this must match exactly** (`relocateExactChars`):
  0 to 60 characters, default 12, a `UnitInput` committed on blur. At 0
  every passage may match roughly.

Both are account preferences in `PREFS`, kept in the profile and synced.
Their codecs, `stepClamped(50, 100, 5)` and `intClamped(0, 60)`, read a
value to the nearest step and into range. A run reads both from its owner's
profile by the same names as it starts (`_start_relocation`,
`db.get_profile`, `highlight_relocate.thresholds`), the codec's way. A
missing value, or one that is no number (a string, a bool), is the default,
each on its own. The job's params record them ([The job](#the-job),
`min_match` as a fraction from 0.5 to 1.0), and `relocate` passes them
through `place_all` to every `find_passage`. A later change leaves started
runs as they are. A guest's preferences stay in its browser
([settings.md](settings.md) "Where settings are stored"), so its runs go by
the defaults.

## What a run writes

A run leaves each highlight in one of three looks, and counts it by the
look it leaves (`place_all`'s `kinds`).

**Solid.** An exact match of its reference placed it, or the PDF's picture
under an area did. Or it stayed where it was made: a highlight on the
page's own file over its quote, or an area there that nothing moves. Or
it went back where it was confirmed: a pending area whose file was put
back ([Areas](#areas)). Its position carries no `approx`, `match` or
`prev`. In a new place it counts as `moved`, as does a lost one found
again, even where it was. In the same place it counts as `kept`, written
only to drop a `doc_id`, a flag or a stale `match` or `prev`, or to respell
its quote.

**Dashed** (`approx: true`). A rough match placed it, also where it was (a
sentence reworded in place). `match` is its confidence rounded to two
decimals, at most 0.99, since 1.0 is an exact match's. An area placed by
estimate, or one measured on another file that nothing placed, is dashed
without `match`.

**Hollow** (`lost: true`). Nothing found it, or its best place reads too
unlike it: under `min_match`, or not exact for a needle under
`exact_chars`. It keeps its old rectangles and `doc_id`, and a pending one
goes back to its reference ([The reference](#the-reference)). One already
lost is written again only when its `closest` (below) changes. A run that
finds it later, even in place, drops `lost` and `closest`, as a move by
hand does.

**Where it was before.** Every dashed placement also stores
`pdf_position.prev` for the person to compare
(`highlights.previous_place`):

    {"doc_id": "<the file its rectangles were measured on>", "pageNumber": 1,
     "rects": [[0.1176, 0.1136, 0.4902, 0.1313], …], "quote": "…"}

- `doc_id`: the file the run read the highlight from, its position's
  `doc_id`, else the page's new file. It is written even when that file is
  gone or `unstored`, and the card then shows the quote alone.
- `rects`: fractions of that page as shown (`[x1, y1, x2, y2]`, top-left
  origin, clamped, four decimals, at most 200), none without a frame size.
- `quote`: the stored quote before the run respelled it (`""` for an area,
  at most 1000 characters).

A pending one placed dashed again keeps its `prev`, the place before the
run that first drew it dashed. A pending one placed solid, or left hollow
at its reference, loses it. `highlights.from_scaled` keeps `prev` only in
`valid_prev`'s form, and `match` only as a number from 0 to 1.

**Its closest place.** A lost text highlight's best candidate may read
under the threshold but at least `CLOSEST_MIN` (0.5) alike ([The
answer](#finding-a-passage)). It then keeps it as `pdf_position.closest`,
to be taken on its card (`closest_place` in `place_all`). It is a position
in the new file (`pageNumber`, `width`, `height`, `boundingRect`, `rects`),
built as a placement's is (`rects_for_span` over the match's spans). Its
`match` is rounded as a dashed one's, and its `quote` is as the new file
spells it (`_spelled`), at most 1000 characters. It is written, and kept by
`from_scaled`, only in `highlights.valid_closest`'s form. That allows one
to 200 rectangles, so a candidate of more gives none. A run that leaves the
highlight lost writes its own candidate or none. Any other run drops it,
and so does a replacement of the PDF ([Which
file](#which-file-a-position-was-measured-on)).

**The counts.** `moved` and `kept` (solid), `approx` (dashed, or still
pending), `lost` (hollow) and `changed` add up to `highlights`. `changed`
counts the highlights left as a person changed them while the run matched
([The job](#the-job)).

### The reference

A highlight that is `approx` when a run starts is pending. Its place is a
guess nobody approved, so a run does not match it from there. Each
highlight has a **reference**, the last place someone confirmed: its file,
page, rectangles and quote.

A solid or a lost highlight's reference is its own position, as is that of
a pending one stored without `prev` (found roughly, it gets that place as
its `prev`). A dashed one's is its `prev` (`_Seen.ref`), and so is that of
one left lost while pending (`lost`, `approx` and a `prev`).
`reference_place` turns `prev` into a position: its fractions in the frame
of `prev`'s file's page (else the position's own), naming `prev`'s file.
Areas are matched from their reference too, and a pending one goes back
there solid when its `prev`'s file is put back ([Areas](#areas)).

A run reads a pending highlight's passage, context and anchor from `prev`'s
file at `prev`'s rectangles (`read_from(prev.doc_id)`), and measures the
nearest place from there. A `prev` quote clipped to 1000 characters is not
compared with the text under the rectangles. With the file no longer
stored, the run looks for `prev`'s quote alone, a clipped one as the
passage's start. Then:

- **found exactly**: solid and resolved, its quote as the new file spells
  it.
- **found roughly**: dashed, `match` against the reference (not the guess),
  `prev` unchanged.
- **not found**: hollow at its reference, with no `prev`, its `closest` the
  search's best candidate (above).

Hollow at its reference means `prev`'s page, rectangles, file (as `doc_id`)
and quote, so the card's Before picture is cut from that file. Its hollow
outline is drawn at those fractions of the current file's page. A `prev`
quote clipped to 1000 characters gives way to the text under `prev`'s
rectangles in that file. With the file gone, the highlight keeps the quote
it had until a run finds it again.

Accept, a move by hand, Use closest match and an exact match write a
position without `prev`, so the next replacement matches from there.
Accept's undo puts `prev` back ([In the web app](#in-the-web-app)).

A run over the file it already ran on (Find highlights in this PDF again)
writes nothing, a dashed one's `match` read against the same reference. A
stricter threshold set since leaves a rough match under it hollow at its
reference, its rough place offered as the closest match.

The file a reference names stays stored while it names it ([Which
file](#which-file-a-position-was-measured-on)).

## In the web app

**On the page.** The viewer (`PdfPage`) draws a lost highlight hollow, on a
flipped page too: a dashed line with no fill, in the stronger shade of its
colour that underlines a commented passage (`--hl-dark-line`). Hollow is
the inline style's first branch, so it wins over an area, a comment, a link
or pending. Its rects carry the unstyled class `pdfHlLost`, which the
browser tests find it by.

A pending one (`placedToCheck`: `approx` and not lost) keeps its look under
a dashed edge: an area's border, or a passage's 1px outline in that shade.
A passage's edge is an outline because a commented or link passage's
underline is a border. The dash rides inside a shorthand. React rewrites
only the style keys that change, and a shorthand beside an unchanged
border-style would reset it to solid.

A lost or pending highlight has no title, not even its comment's (a link
region keeps its own), since its review card says what happened. The flags
are the viewer's alone: exports and the AI read the position as any other.

**The review card** (`HighlightReview.jsx`, `useHighlightReview`). Hovering
a dashed or hollow highlight opens a card anchored to it after 150 ms. The
card waits 200 ms as the pointer leaves, so the pointer can cross into it.
Leaving both closes it, as `NoteBadge`'s tip does. Only those highlights'
rectangles listen for the pointer (PdfPage's `reviewable`), so a page of
many highlights costs nothing more. On touch, a tap opens the card in place
of its usual action, which a second tap does. `lastPointer` tells a tap
from its compatibility mouse events. A right-click or a long press opens
the highlight's menu in place of the card, shown or on its way.

Escape closes the card, and so do a press outside it and its highlight, and
another page or file. So does the highlight leaving the review elsewhere:
its menu's Accept, a move not started from the card, an undo back to solid,
or a deletion by someone else. Only these close a card opened by a tap,
Previous or Next, the Document menu or a step.

Such a card takes the focus once it shows, so the keys reach its buttons.
Reached by Previous or Next, it focuses that button, and gone on to after
an Accept (below), its own Accept when enabled. So Enter walks on, or
accepts the next. A step keeps the focus on its button.

The browser takes the focus from what it hides. So a card hidden a moment
drops it: when the viewer scrolls after Use closest match or back on an
undo, or the highlight leaves sight. Shown again with the focus on nothing,
the card puts it back on the control last focused in it, or on itself when
that is disabled. Focus moved elsewhere meanwhile forgets that control.

A card on a highlight that took a step stays while the pointer rests on
another dashed or hollow highlight.

The card, at most 360 px wide (the window less 16 px on a phone), is a
portal on the shared popup surface (`.hlReviewCard` beside `.pdfNoteTip` in
app.css). `placeFloating` and `useFloatingPosition` place it under the
highlight's rectangles, else above, following the page as it scrolls
([ui-design.md](ui-design.md#menus-and-popups-open-in-place)). It hides
while the highlight is out of sight. It waits up to four seconds for
rectangles a page has not drawn (still rendering, or scrolled so far it
dropped them), then closes.

The whole card hides through its inline `visibility`, and nothing in it is
set visible on its own. The before picture is hidden until it loads
(`.hlReviewShot:not(.loaded) img`), then inherits. A picture set `visible`
would show through the hidden card, alone over the page.

A lost highlight can name a page past the PDF's last, after a revision
dropped an appendix (`pastLastPage`). Nothing draws it, so only Previous,
Next and the Document menu reach it, without scrolling. Its card stands
centred near the viewer's foot, with "This version of the PDF has no page
10." (`.hlReviewOffPage`) under its header.

**The notes follow the card.** A card that goes to a highlight scrolls the
notes panel to its block (PdfViewer's `onReviewFollow`, App's
`scrollToBlock` and `reveal`). It goes to one on a tap, Previous or Next,
the advance after a decision and the Document menu's Review. The row is
scrolled to the middle and marked focused in the tree's sense, its folded
parents opened, and the chat's Cursor chip follows it. No editor opens and
the focus stays in the card. Unlike a click, it adds no quote to the chat.
A hovered card leaves the notes where they are. On a phone the row is
scrolled to when the notes panel opens.

`reviewCardState` gives what the card shows, with the step in effect on it
(below).

**A pending (dashed) card** shows the header "Matched roughly: 92%"
(`roughMatchPercent`), or "Placed by estimate" without `match`. Then come
**Before**, from its `prev` (`reviewPrev`, below), and for a text highlight
its old quote against its current one, word by word. Its **Note** (below)
and, for an editor, the row of steps (below) follow.

**A lost (hollow) card** has the header "Not found in this version". Its
**Before** comes from its own position (`lostPlace`). When its `doc_id`
names a file other than the page's current one (PdfViewer's `fileId`, the
`fileHash` of App's page document), the picture is cut from that file. Its
rectangles are drawn over it, and its stored quote beneath. Otherwise its
rectangles are drawn hollow right there, and the card shows only the stored
quote.

With a `closest` place, the card adds "Closest match: 72%" with **Show**,
and the stored quote set against the candidate's text, word by word. That
comparison replaces the quote under the picture, and on the current file
the whole of Before. `closestPlace` reads the place defensively, taking a
missing `boundingRect` as the rectangles' union. A lost area has no
`closest`.

Show scrolls to the candidate and outlines its rectangles while the card is
open, the card anchored to the outline (`.pdfHlGhost` in app.css, taking no
pointer). A candidate pages away is drawn once its page renders, which
PdfPage reports (`review.drawn`) so the card can move under it.

An editor's card has one row of steps in one order (`reviewActions`):
**Accept**, **Use closest match** (a lost card with a closest match only),
**Move…** and **Discard**. No button appears or goes on a step. A lost
card's Accept is disabled until Move… or Use closest match placed it, then
takes that place as the decision (below). Until then its title is "Nothing
to accept until it is placed: use Move… or Use closest match first", or "…:
use Move… first" without a closest match. The primary button is Accept
while it applies and is not taken, else Use closest match. Beside Use
closest match, Move… and Discard show their icons alone, named in
`aria-label` and `title`: four labelled buttons do not fit the card. Under
the steps, on a line of their own (`.hlReviewNav`), stand **Previous**, "2
of 3" and **Next**. A viewer who may not edit sees neither row, and Show
stays.

**The note** (`noteSummary`) stands under the quote or the comparison.
App's `reviewNote` reads it from the live block. It is the block's text
line by line through search's `plainSnippet` (markdown's marks, refs and
tags go). It shows four lines at most, which scroll (`.hlReviewNoteText`).
Under them come its counts, if any (`.hlReviewNoteCounts`): "2 notes under
it · 1 comment". They are the blocks under the highlight at any depth and
its comment threads, open or resolved, as Discard counts them. Once Discard
deleted the block, the card shows the note recorded with the step
(`h.cardNote`).

**On a short window** the card is at most `min(70vh, 520px)` tall. The note
gives way first, down to its label, one line and its counts. Then a dashed
card's comparison does, down to three lines or all of a shorter one (`Diff`
sets its content height as `--hl-review-diff-h`). The rest keeps its size.
Past those floors, under about 660 px with a tall Before picture, the card
scrolls as a whole.

**After a step** taken on the card (Accept, Use closest match, a move
started from its Move…, Discard), the card stays as it was. Nothing on it
moves: the Playwright suite compares every box within a pixel. It is drawn
from where the highlight stood before the first step of the chain, with its
quote then (`reviewCardState` over `stepOrigin`, back through a move's
`prior`). Only the step's button changes, in place:

- It reads "Accepted", "Placed", "Moved" or "Discarded" with a check, and
  is pressed (`aria-pressed`, the `.uiBtn.on` look).
- Its title says that pressing it again undoes the step ("Accepted: press
  again to undo").
- The icon-only Move… and Discard beside Use closest match show the check
  alone, with "Moved" or "Discarded" as their `aria-label`.

Each step button holds both its faces in one grid cell, the one not shown
hidden (`.hlReviewFace`, `.hlReviewRoom` in app.css), so its width holds.

A button that does not apply stays in its place, disabled
(`reviewActions`):

- a lost card's Accept before anything placed it (`unplaced`);
- after Discard: Accept, Use closest match and Move…;
- after any other step: Use closest match and Discard.

Show is disabled while a step is in effect.

The card follows its highlight when the step took it elsewhere. A discarded
one's card stays anchored to its old rectangles on its page (`placeBox`,
scaled as PdfPage scales them).

**A decision goes on to the next one.** Accept and Discard decide
(`decided`). A move and Use closest match alone wait for Accept. After a
decision the card shows its pressed button for a moment (`ADVANCE_MS`, 600
ms). Then it goes on as Next would, the notes panel following, to
`nextToCheck`: the next undecided entry of the walk, round from the last to
the first.

The moment starts once the decision is the card's step in effect. It ends
with nothing done:

- on any press on the card (a capture-phase click), the decision's own undo
  among them;
- when the card closes;
- when the step in effect changes by other means (Ctrl+Z, a collaborator's
  edit).

With none left to check, the card stays and its count reads "All reviewed".
The Document menu's Accept all opens no card.

A step is in effect only while the highlight is as the step left it
(`stepInEffect`, over App's `reviewPosition`: the block's `pdf_position`,
null for no block). For Accept, Use closest match and a move, that is the
position the step wrote. For a Discard, the highlight is drawn no more and
its block is gone or keeps the step's `{pageNumber}`. When Ctrl+Z, someone
else's change or a move by hand ends the step, the card shows the highlight
as it is. Dashed or hollow again, it has its buttons as before any step.
Solid elsewhere or deleted by someone else, it leaves nothing to review,
and the card closes.

The card shows its newest step still in effect (`reviewList`, back through
a move's `prior`). Ctrl+Z after a move started from an accepted or placed
card takes back the move alone, and the card shows Accepted (or Placed)
pressed.

**Before** is a picture of the highlight in the file it was measured on. It
comes from
`GET /api/ai/selection-crop/{doc_id}?page={pageNumber}&box={x1,y1,x2,y2}`,
the chat's region picture, open to any workspace member and cached
([api.md](api.md) "AI (`ai.py`)"). `cropBox` widens the union of the old
rectangles by 2.4 lines, as far across in points as down, and keeps it on
the page. A line is a text highlight's median rectangle height, within 0.6%
to 3% of the page, or 1.4% for an area. `rectsInCrop` draws the old
rectangles over it in the highlight's colour, and `cropAspect` shapes the
placeholder while it loads. The picture takes the page's paper and theme,
flipped on a flipped page.

When the picture cannot be had, the line "The previous version of this PDF
is no longer stored." stands over the old quote, which ends in "…" when
clipped. A missing file answers 404. A file a reference names stays stored,
so this is for a copy that never received it or one removed outside that
rule ([Which file](#which-file-a-position-was-measured-on)). A `doc_id`
that is no file name, such as `unstored`, asks for no picture.

A share view asks for none either (`beforeCrop`), and its line reads "The
previous version is not shown through a share link." The route reads no
share token and the asker picks its box. Opened to a share link, it would
let one cut whole pages of a file the owner replaced.

**The comparison** sets the two quotes against each other word by word
(`quoteDiff` over `shared/lib/wordDiff.js`, the merge conflict card's word
diff). The words gone are struck through and the words added marked.
Whitespace reads as one space. An unchanged stretch beside a change is cut
to six words (`DIFF_CONTEXT`). Two empty quotes show nothing.

A pending card's old quote may be clipped at 1000 characters, inside a word
too (blockModel's `PREV_MAX_QUOTE`, `reviewPrev`'s `clipped`). It is then
compared up to its last whole word, and the current quote only as far,
ending in an ellipsis. Past the cut, only the words that replace old ones
struck there are marked added. A change past the cut is not shown, though
the picture shows the old passage. A lost card's stored quote is whole.

**The review queue** (`reviewQueue`, `placeToReview`) holds the open page's
pending and lost highlights, and those waiting for Accept on a card
(below). It is in document order: page, then top, then left, as
`pendingInOrder` orders Accept all's. A lost one past the PDF's last page
sorts last.

**The card's walk** (`reviewList`, `reviewPlace`) is what **Previous** and
**Next** walk and "{i} of {n}" counts, in the same order. It holds the
queue, and the highlights a step on a card resolved since the viewer showed
this page and file. Each stands where it stood before its step
(`stepOrigin`), so a step changes neither the count nor what Previous and
Next reach.

The steps are the viewer's own (`useHighlightReview`'s `steps`): each
highlight's id mapped to the step last taken on its card and the highlight
as the card had it. The card records its own steps, and App records a move
from it (`reviewRef` with `done`). Another page or file or a reload starts
them afresh (`review.reset`, on PdfViewer's `url`, `fileId` and `pageId`).

A resolved highlight with no step in effect is back to check at its place.
Solid elsewhere or deleted by someone else, it leaves the walk. One that
Discard deleted stays until the walk starts afresh. Previous and Next
scroll round the walk, a discarded one to where it stood (`entryPosition`).
Both are disabled when the walk holds one highlight.

The entries left to check (`reviewPlace`'s `left`) are those with no step
in effect and those waiting for Accept (`waitingForAccept`). The waiting
ones are solid, so PdfViewer tells App their ids (`onReviewWaiting`) and
`reviewQueue`'s `waiting` adds them: Review counts what `left` counts.
Waiting lasts as long as the walk. After it, a highlight moved or placed
without Accept counts as resolved where the step put it.

The count holds the room of both its texts, so the last step moves nothing.
Previous and Next are `ctlBtn` chevrons on either side of it, as on the
merge resolver's conflict card (`.mergeNav`). The steps stay on one line in
English and Chinese (count "2 / 3") down to a 360 px phone, unless the
interface font is larger. A window under 376 px makes the card narrower
than its 360 px. Its sides, the steps' gaps and the labelled buttons'
padding then tighten (app.css, the `max-width: 375px` rule after
`.hlReviewCount`). Below 360 px they may wrap.

**Accept** (App's `acceptHighlightPlaces`, `acceptedPosition`) is the
card's Accept, the highlight's context menu entry and the Document menu's
Accept all. The context menu offers **Accept** to an editor on a pending
highlight, before **Move to a selection…**. Each drops `approx`, `match`,
`prev` and `doc_id` and keeps the rectangles, so the highlight is drawn
solid where it is. A later run reads it from this file, not the old one
([The reference](#the-reference)). A run under way skips the changed
position by its compare-and-set. Accept is one tree edit for any number of
highlights: one undo step, sent to the others as ops.

After a move or Use closest match on the card, the highlight is solid
already and the card's Accept writes nothing. It takes that place as the
decision (`reviewStep`'s `confirms`, its `after` the position as it is, its
`prior` that step), titled "Keep the highlight where it was placed".

**Use closest match** (App's `placeClosestMatch`, `closestPlacement`) gives
the lost highlight the candidate's place and quote, its position becoming
the `closest` one. `lost`, `closest`, `doc_id`, `approx`, `match` and
`prev` go, and it is drawn solid, in one tree edit. The card scrolls to the
new place and follows it.

**Discard** (App's `discardReviewHighlight`, with `discardKind`,
`discardOutcome`, `discardStep` and `discardApplied` in highlightReview.js)
follows Move… on a dashed or a lost card, for an editor. It is for a
passage not in the new file as it was, which the person does not want
placed. It is one tree edit either way:

- A highlight with no note (content empty or spaces), no child blocks (even
  empty ones) and no comment thread, open or resolved
  (`comments.threadsByBlock`), is deleted (`removeBlockTree`).
- Any other stays as a note, its `pdf_position` its page alone,
  `{pageNumber}`.

A page alone is the shape of a highlight whose place on its page is not
known (a Logseq import's). So nothing draws it, and the queue leaves it
out. Its quote, colour, note, children and threads stay. Its notes row has
a grey dot and the ⊕ that attaches it to a highlight.

App's `discardOf` picks the button's title from what the block carries
(`discardOutcome`), so it holds after another step too:

- "Delete the highlight: it holds no note or comment, and its text is not
  in this version"
- "Take the highlight off the PDF and keep it as a note, with its quote,
  note and comments"

Discard is on the card alone, one highlight at a time after a look at it.
The highlight menu's Delete does the first, and no menu does the second.

**Moving a highlight** (App's `moveHighlightId`) starts, for an editor,
from the context menu's **Move to a selection…**, a lost row's button, or
the card's **Move…**, which closes the card. Move mode and attach mode (a
note's ⊕ waiting for a click on a highlight) end each other
(`startHighlightMove`, `startAttachMode`), so one banner shows.

In move mode the highlights let the pointer through (`.pdfMoving`), so the
text under them can be selected, the highlight's own included. The next
selection, text or an area drag (the area key,
[hotkeys.md](hotkeys.md#held-keys)), shows a tip with one button, *Move the
highlight here* (`PlainTip`'s `onMove`). It translates nothing, even with
translate on select on. An area drag sends no picture to the chat
(`PdfViewer`'s `onAreaSelected` skips `onAreaSelection`).

`moveHighlightTo` sets `pdf_position` to the selection's, with no `doc_id`,
`lost`, `closest`, `approx`, `match` or `prev`, and `quote` to its text. An
area drag makes an area with no quote. The move is one tree edit, and the
highlight flashes. Escape, Cancel, another page or another file on this
page ends the mode. It serves any misplaced highlight, not only one to
review.

A move from the card's Move… is remembered (`startReviewMove`,
`moveFromReviewRef`: `{id, prior}`, `prior` the card's step in effect if
any). Any other start of a move forgets it. After the move,
`moveHighlightTo` reopens the card through `reviewRef`, which records the
step. It does not scroll, since the selection is in sight.

**Undoing a step** is its button pressed again. The card keeps what the
step changed (`reviewStep`: `{kind, before: {position, quote?}, after}`),
`before` holding the quote only for a placement or a move. The undo (App's
`undoReviewStep`, `reviewUndone`) is one more tree edit, so Ctrl+Z takes it
back in turn. It restores `before`, an empty quote dropped, only while the
position is still `after`. After a placement's or a move's undo the card
scrolls back.

A move from a card whose step was in effect keeps that step as its `prior`,
while the position it moved from is the one that step wrote. Its undo
leaves the card on that step, so after Accept and a move, Moved and then
Accepted, pressed in turn, each go back one step. An Accept that confirmed
a place (`confirms`) wrote nothing, so its undo writes nothing either
(`undoReviewStep` returns), and the card goes back to that step.

A Discard's step is `discardStep`'s
`{kind: "discarded", deleted, before: {position}}`, plus `removed` (the
block and its place, `blockPlace`) for a deletion, else `after`,
`{pageNumber}`. `discardUndone` takes it back:

- A deleted highlight is inserted again where it stood (`insertBlockAt`),
  as Ctrl+Z of a deletion inserts it. When its parent has gone since, it
  goes at the end of the page's top level.
- One kept as a note gets its dashed or lost position again
  (`reviewUndone`).

The undo does nothing when the deleted one is back already, or when the
kept one's position changed since.

**The Document menu.** When the open page has highlights to review, the
paperclip popover lists:

- **Review 3 highlights** (**Review 1 highlight** for one), for anyone,
  titled "Show the first dashed or hollow highlight with what it matched
  and where it was before";
- **Accept all 3 rough matches** (**Accept 1 rough match** for one), for an
  editor while some are pending. The status says how many when there are
  several.

Review closes the menu, shows the PDF if it was hidden, scrolls to the
queue's first and opens its card. A waiting one's card opens with its
step's button pressed and Accept enabled. Accept all counts the pending
ones alone, an estimated area included. A discarded one is in neither.

**Find highlights in this PDF**'s title says what a run does: "Find each
highlight's text in this file and move the highlight there; one matched
roughly is drawn dashed, one whose text is gone hollow".

**The notes row.** A lost row's dot is hollow (`.highlightDot.lost`, its
colour the inline `border-color`), titled "Not found in the current PDF —
jump to where it was". For an editor on a page with a PDF, a move button
stands beside it among the row's hover controls. A pending row's dot keeps
its fill inside a dashed ring in its colour's line tone
(`.highlightDot.approx`, `darkPageLine`). Its title is "Matched roughly
(92%) after the PDF was replaced — jump to it", or "Placed by estimate
after the PDF was replaced — jump to it" without `match`.

**What the tab says.** The run's Background tasks row reads "Placing the
highlights of “…”". Its phases are "Reading the PDF" and "Finding each
highlight's text", with a stop button until it writes. The tab that started
the run reports its end (`relocationSummary`). A tab starts one by a
replacement (whose answer it adopts), the menu or the row's Start again.
The summary holds:

- the counts, "Highlights placed in the PDF: 2 moved, 1 matched roughly or
  estimated, 5 stayed in place, 1 not found.", a missing one read as 0;
- when one is dashed, "Hover the dashed highlight to compare and accept
  it.", or for several "Hover a dashed highlight to compare and accept it,
  or review all 3 from the Document menu.";
- how many someone moved meanwhile (`changed`);
- when one is lost, how to move a hollow highlight.

A failed or stopped run says so, unless a run for another file of the same
page started after it. `taskKinds.relocationSuperseded` reads that from the
task listing the run ended in. A replacement then stopped it or had its
write refused, and the newer run reports. While one of the account's runs
for the page is going, **Find highlights in this PDF** is disabled. Its
title is "This page's highlights are being placed — see Background tasks".

## Limits and open items

- The thresholds were set by hand, not calibrated, and the character
  likeness is generous. At the default 80% both of these are placed
  dashed, which is what the dashed look and the card are for:
  - "one dos tres cuatro five … ten" against "one two three … ten" reads
    0.87;
  - "a doublet at the resonance … when the second beam is switched on"
    against "a single line below the resonance … when the first beam is
    switched off" reads 0.83.
- An exact copy is taken, solid and not pending, wherever it stands:
  - A passage reworded in place to under about 0.86 alike
    ([The ranking](#finding-a-passage)) goes to a verbatim copy elsewhere:
    an abstract's sentence to the conclusion that restates it.
  - A short passage naming something else in the new file does the same.
    "Fig. 3" lands on the new Fig. 3 after a renumbering, at any
    threshold, since the new file holds it word for word.
  - The surroundings cannot tell such a copy from a revision, so a rule on
    the context fit would dash correct placements too. An unchanged
    sentence beside an inserted or reworded one fits its old context about
    as poorly (0.2 to 0.35) as an unrelated copy does (about 0.32).
  - Telling them apart is open. It would take both sides of the old
    context found next to each other, around different text.
- The threshold applies to the best-ranked candidate alone. At a high
  `min_match`, a rough candidate its context ranks above an exact copy
  elsewhere can fall under it. The highlight is then lost, with that
  candidate as its closest match, not the copy.
- The rough search's bound and pre-filter are for speed, and a place past
  them is no candidate. At a threshold near 50%, a place reading 0.6 or
  0.7 alike may be neither offered nor kept as a closest match.
- A passage is split only where the old file read text between its
  pieces. One the new file splits elsewhere (reflowed lines, a caption or
  footnote drawn inside it) or reads in another order (interleaved
  columns) is found only roughly, or lost.
- Without its file (gone, `unstored`, a lost highlight without
  `doc_id`), the stored quote is the needle, with no context:
  - Two identical short passages are told apart by page and place alone.
  - Each margin line number a review copy put in the quote is a word too
    many, so the quote is placed dashed or lost.
  - Keeping the context with the highlight when it is made, as a W3C
    `TextQuoteSelector`'s prefix and suffix, would serve such runs
    ([research/highlight-anchoring.md](../research/highlight-anchoring.md)).
- A passage under `exact_chars` characters, or of one word, that the new
  file splits at a page break is not found.
- A page whose PDF is shown from a URL alone (no hash, and an address
  that is not an upload's) starts no run. Its highlights stay where they
  were.
- Ink groups, text boxes and pictures placed on the page
  ([pictures.md](pictures.md)) stay where they were, though their pages
  may have shifted. Carrying them by the ride-along rule is open.
- An area over a vector figure without text or an embedded image rides
  along, or stays where it was, marked `approx`, until the person accepts
  it or the file it was confirmed on is put back.
- A run's ops are the server's, so no tab's undo history has them. A
  highlight moved wrongly is moved back by hand, and a dashed or hollow
  one's card shows where it was before.
- The passages are read with MuPDF, the viewer's text with pdf.js. A
  quote the viewer stored that MuPDF reads differently (spacing in a
  mathematical line) is found roughly, drawn dashed, or not at all.

## Tests

`backend/tests/test_highlight_relocate.py` covers what follows.

**The page text**: the folding, hyphenated words, the file's spelling of a
passage, the compact glyph store, number-only lines left out.

**The passage under a highlight**:

- across a line's end, and across two columns with a caption between;
- an area over a table's column;
- line rectangles a line low (a frame a tenth off its scale) or past their
  column's edge, placed solid on the quote's own copy;
- rectangles within the quote, or a quote the page lacks, read as they are.

**The search**:

- an exact passage on another page, word edges, and the ranking;
- the rough search over a changed number, citation, symbol, negation or
  punctuation, and a text too unlike;
- the twelve-character rule, a dropped first or last word, an added word;
- a passage reworded in place against an exact copy elsewhere;
- a passage split by a page break, and one found piece by piece;
- the bound changing no answer, and a stop within a page.

**The rectangles** on a plain and on a rotated cropped page.

**What a run leaves**:

- dashed placements with `match`, respelled quote and `prev`, and a solid
  one by the quote's copy;
- a lost highlight with its `closest` (kept by `from_scaled`) at 80% and
  95%, placed dashed at 70%. No `closest` under `CLOSEST_MIN` or twelve
  characters.
- later runs replacing, removing or dropping `closest`, and one on the same
  file writing nothing;
- the lost highlight's own file back as the page's PDF, dropping `lost`,
  `doc_id` and `closest`;
- an exact placement dropping a stale `match` and `prev`, and a dashed one
  keeping its `prev` when its old file is gone;
- the thresholds read from a profile.

**The reference**: a highlight made on a PDF's v1, left pending on its
replacements v2 and v3, matched from v1.

- renumbered again, v1's words back a line lower, the passage gone;
- v1's sentence gone while v2's guess reads like a v3 sentence;
- v1's text changed further, a stricter threshold, v1's file gone;
- v3 being v1's own file, one left lost while pending, one without `prev`;
- a pending area, one back solid when v3 is v1's own file (its caption
  or picture there, nothing there, found roughly on v2, or ridden along on
  v2), one a run on its own file drew dashed left so by the next, and each
  rerun on v3 writing nothing.

**`place_all`**:

- two files with each outcome, and a file gone;
- a quote that disagrees with its rectangles, or a short line that is only
  part of it;
- a highlight over its quote on the page's own file;
- a sentence reworded in place, a hyphenated respelling, margin line
  numbers, a new file without text;
- a lost highlight already marked not written again, and one found again;
- the page cap and the ride-along's cases;
- estimated, unplaced and accepted areas through later runs and
  replacements;
- an unplaced area not carried by highlights measured on another file.

**The endpoint and the job**:

- the PUT's run and answer, the counts end to end, the owner's thresholds;
- a later manual run at other thresholds, and a stricter one;
- two replacements with highlights still pending, and a dashed area solid
  once the PDF it was confirmed on is put back;
- the stamps, and `relocation_error` when the jobs are full;
- a run that never wrote, a wrong file in between, a lost highlight read
  from its own file;
- a `doc_id` not stored or no file name, a page shown from a URL alone, a
  duplicated page;
- the manual run after a skipped one and by the stored quotes;
- a second replacement stopping the run before, and a manual start
  answering the running one;
- a page without highlights, and a token's 403 on the manual start.

**The write**: a highlight a person changes or deletes while the run
matches, the page deleted meanwhile, the batches in one transaction, the
file changed again.

The other backend tests:

- `test_pages.py`: the replacement's stamp.
- `test_upload_retention.py`: the files a reference keeps. v1 stays while a
  highlight names it, past a sync's day. v2 and v3 are recorded once a run
  moves the rectangles off them, v2 with the sync's day. v1 is an orphan on
  Accept, named again by its undo, and kept the usual 30 days once deleted.
- `test_highlight_shape.py`: `previous_place`'s fractions, and what
  `from_scaled` keeps: `match` only from 0 to 1, `prev` and `closest` only
  in their form (`closest`'s quote clipped, up to 200 rectangles).
- `test_restore_consistency.py`: a restore keeping `doc_id`, `lost`,
  `closest`, `approx`, `match` and `prev`.

In the web app, `frontend/tests/highlightReview.test.mjs` checks the card's
arithmetic.

**The queue and the walk**:

- the queue in document order, a solid one left out, and one past the PDF's
  last page last;
- Previous and Next round the walk, disabled for one alone;
- a step changing neither the count nor the walk, and `stepOrigin` through
  a move's `prior`;
- all placed: still to check. All accepted: "All reviewed" (`left` 0).

**Before and the card's state**: `prev` read defensively, a lost one's
Before, each kind of card, each step leaving the card as it was.

**The buttons** (`reviewActions`):

- one row in one order for each kind of card, the same set on a dashed one
  and a lost one without a closest match;
- after each step, its button pressed and the ones that do not apply
  disabled;
- a lost one's Accept disabled until a move or Use closest match;
- Accept after a move or Use closest match confirming that place, writing
  nothing (`confirms`). None without such a step or once the highlight
  moved on, as after the app's undo of the move.

**Decisions and steps**:

- `decided` (Accept and Discard) going on to `nextToCheck`, round, and to
  one waiting for Accept;
- `stepInEffect` after the app's undo, a collaborator's move and a
  deletion;
- what each step changes for its undo, and a move's `prior`.

**The picture and the comparison**: the crop asked of the old file and not
through a share link, its box and the old rectangles in it, the quote
comparison, an old quote clipped to a `prev`'s length.

**Discard**:

- which of the two it does: a note of spaces is no note, and an empty child
  or a thread keeps it;
- only a lost or a dashed one discarded, `discardOutcome` whatever the
  place, and `noteSummary`;
- a bare one deleted and a noted one kept, each undone, also when the
  parent has gone or it is back already;
- the card and the walk after Discard, an undo and a later move or
  deletion;
- `placeBox`, `blockPlace` and `insertBlockAt`.

The other web app unit tests:

- `logseqPdfModel.test.mjs`: `placedToCheck`, `roughMatchPercent`,
  `acceptedPosition` (`doc_id` going too), `placeToReview`, `closestPlace`,
  `closestPlacement` and `reviewUndone`;
- `tasks.test.mjs`: `relocationSummary` and `relocationSuperseded`;
- `settings.test.mjs`: the two thresholds' defaults and clamping;
- `documents.test.mjs`: the file a page shows (`fileHash`).

The browser suite runs a step by part of its name (`npm run e2e -- --only
"<part of the name>"`). The steps are in `pdf.mjs`, save the last, which is
in `files.mjs`.

**"replacing the PDF moves a highlight to its text"**:

- the job's params, and the hollow and dashed looks on a light and a
  flipped page and in the notes;
- attach mode and move mode ending each other;
- the card on hover and while the pointer is in it;
- Accept leaving every box the same within a pixel, the advance, Previous
  and the undo;
- Move… from an accepted card, Ctrl+Z and Ctrl+Y;
- the hollow card, and the Document menu's Review and Accept all;
- moves from the hollow card (undone back hollow with its `doc_id`), the
  row's button and an area drag;
- an account at 100%: a lost citation's closest match, Show and Use closest
  match.

**"a highlight still dashed at the next replacement is matched from the
version it was made on"**: v3 printing v1's citation a line lower, and v1's
sentence gone while v3 has one like v2's guess.

**"a hollow highlight's card leaves nothing on screen"**:

- nothing of the card, picture included, while its highlight is scrolled
  away by the scroller or the wheel;
- Discard on a bare one and on one with a note, each undone.

**"a review card shows its highlight's note"**:

- the note and its counts, the notes panel staying put;
- Next to a bare one out of the notes' sight, and to a hollow one;
- Move… and Accept on the hollow one, the focus on the next one's Accept;
- Discard on a bare dashed one, undone within the moment and after, and on
  a noted one, undone after.

**"on a short window a review card's note gives way"**:

- at 1280x600, three lines of a long comparison and one of a twelve-line
  note;
- a short comparison shown whole, no taller;
- at 1280x720, the comparison at full height, no scroll, Previous / Next in
  the card;
- a hollow one moved without Accept, counted by Review until Accept.

**"a hollow highlight's card follows Show to a far page"**:

- Show on a closest match six pages on;
- Next to a highlight from a dropped page, past the four seconds' wait;
- Use closest match by Enter, and a 360 px phone in English and Chinese.

**"the highlight follows its text"** (`files.mjs`): the Document menu's
Replace the PDF…, the summary in the status pill, the highlight placed
dashed with its note, a second tab following.