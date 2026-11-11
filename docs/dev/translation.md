# PDF translation

The viewer's translated view: the button and its states, the job that
translates a document and outlives the viewer, a paper opened again from
its kept translations, the overlay (how a translation is set, masked and
painted, selected, and a paragraph's menu), how a page is planned into
requests (its reference list and the text in its figures left original),
what a request carries (the paper's brief, the workspace's glossary, the
context around its texts, formulas as tokens), the server and the
translations it keeps, an annotated-PDF export that writes the translation
into the file, the engines and their limits, and the bench that measures
it. Provider configuration and the chat are in [ai.md](ai.md); the engine
preferences are in [settings.md](settings.md). Why it is built this way,
and the numbers before and after:
[research/translation-quality.md](../research/translation-quality.md),
[research/translation-reliability.md](../research/translation-reliability.md)
and [research/babeldoc-translator.md](../research/babeldoc-translator.md).

## The button

`POST /api/ai/translate` backs the viewer's translated view. ONE 文A button
in the PDF zoom column does everything by state:

- On the page being read, a click translates it unless it is already fully
  translated under the current language and model. On such a page a click
  toggles show/hide for ALL pages (the viewer reports `current` next to
  `pages` in its state).
- A page a halted job left half-done counts as untranslated, so a click
  finishes it: blocks that came back already stay, the rest are asked for.
  Switching language or model in Settings makes the button translate afresh.
- Hidden = slashed icon; holding Alt (the `pdf.peekOriginal` held key, [hotkeys.md](hotkeys.md#held-keys)) peeks. Its name is what its click
  does: Translate, Show original, Show translation, or Stop translating
  while a job runs.
- **While a job runs** a bar under the glyph fills with the job's progress
  (`.pdfTransBar`, where the highlighter's colour sits on its button), so
  nothing on the strip moves as a job starts or ends; the percentage is in
  the tooltip and the job's background-tasks row. A click then halts the
  job, and the stop glyph takes the glyph's place under the pointer and the
  keyboard's focus, and at all times where nothing hovers.
- Right-click (long-press on touch) opens the option menu (`translateRows`
  in App): Stop translating while a job runs; Translate this page,
  Translate whole document and Translate references too (once a page has a
  reference list left original, "The reference list"); then Show original
  or Show translation, a "Translate into" flyout of the languages (the
  `translateLang` preference, as Settings sets it) and Translation
  settings…; then, apart, Forget this paper's translations… ("Kept
  translations"). The first three and the toggle are the catalog's
  `pdf.translatePage`, `pdf.translateDocument` and `pdf.toggleTranslation`
  ([hotkeys.md](hotkeys.md#the-translations-commands)), drawn as
  `CommandMenuItem` rows: their keys show, and a row that has nothing to do
  greys out with its reason (the page, or every page, translated already:
  `current` and `docDone` of `translateState.buttonState`; a job under
  way). Ctrl+Shift+O and the palette run the same commands, while the PDF
  is on screen as the button is: with the PDF closed, or a phone's panel
  over it, they start no job the reader cannot see.
- Forget is a `danger` row and asks first (the confirm box): it forgets the
  paper's translations in every language and model, and its brief, for
  everyone in the workspace, so showing it translated again asks the model
  or service anew. It stays a whole-paper action rather than the current
  language's and model's: the server's memory is cut off by a generation
  per document, which a narrower forget would have to split by language
  and model, the brief included.
- A page that is all references has nothing to show or hide. While nothing
  is translated anywhere, a click on one runs the job, and the status pill
  says what happened ("The reference list"), so the click is never dead.
  Once a page shows text, the button lights and toggles from there as from
  any page.
- The button is one of the strip's tools (`translate` in the `viewerTools`
  preference, [settings.md](settings.md#bars-order-and-modes)): its place
  is the user's order, and hidden (Settings → Language and Translation ›
  Translation button off, or Appearance › Viewer tools) it is a Translate
  row of the strip's ⋯ menu that opens the option menu beside the row, as a
  flyout would (a `SubMenuItem` cannot hold the language flyout: a menu's
  flyouts share one open state). While it translates or a translation is
  shown it stays on the strip, whatever its mode.

## The queue

A whole-document job queues pages nearest the current page first (forward
before backward at equal distance), so the page being read paints
immediately. The job is `pdf/translateJob.js` (`createTranslateJob`), a
module without React that runs in Node under the tests. Producer/consumer
style:

- The producer segments each queued page and the pages within two of it in
  the job, plans the page (`pdf/translatePlan.js`, next section) and appends
  its chunks to one flat list. A paragraph that runs into the next page needs
  that page, and whether a block there is in a reference list can take that
  page's neighbour to tell.
- N workers (Settings → Language and Translation → parallel requests,
  1–`TRANSLATE_PARALLEL_MAX` = 4 in `app/prefDefs.js`) stream through the
  list across page boundaries: the first request is in flight while later
  pages are still segmenting, chunks paint as they land, and progress by
  characters shows under the button and as a background-tasks row, those
  streamed included ("A page's entry", below).
- **One limit for every job.** Jobs of several documents run side by side,
  and their chunk requests together go through one limit of
  `TRANSLATE_PARALLEL_MAX` in flight (`createLimit`, held by
  `pdf/translateState.js`), below the six AI calls an account may have open
  at once. A request waits for a place in the order they came, a halt takes
  it out of the line, and its time counts from when it goes. Without the
  limit, two jobs ask for more calls than the account has. A chunk the
  server turned away (a 429 with Retry-After) then loses the freed call to
  a worker sending its next chunk, runs out of patience and leaves its
  paragraphs in the original. The brief's request, one per job, goes
  outside the limit.
- Halting aborts the in-flight requests (each job carries an
  AbortController) and keeps finished chunks.

**The paper's brief at the job's start.** As it starts, the job asks for
the paper's brief (`POST /api/ai/translate/brief`, "The paper's brief")
when the paper can have one: the page names it, or the document's opening
text does. It never waits for the answer. The opening text (`head`, up to
8,000 characters of the first three pages) is read only for a PDF the
server has not stored. Only the brief's request carries it, unless there
is no page: then every request does.

- **The first chunk goes at once**, with `wait_brief: false`: it carries the
  brief only if it is made already, so text lands within one request's
  time.
- **The gate.** Every later chunk waits until the brief's request is under
  way. That is its first line (the server has claimed the brief, so the
  chunk finds it being made and waits for it there), the request's end
  (made, found, or failed after its last try), or a halt. A try
  that fails and is to be asked again (the account's AI calls all in use)
  does not open the gate: the chunks let through then would find no brief
  being made and come back without it. `requestChunk`'s patience bounds the
  wait. The later chunks go with `wait_brief: true`.
- **The end-of-queue re-ask.** Each final reply says whether its
  translations carry the brief (`brief`). Once the queue is done, when some
  chunk came back with the brief, the chunks that came back without it are
  asked once more, with `wait_brief: true`, whatever comes back. A chunk
  whose request fails then keeps the text it has; one that lands fades its
  new text in. Nothing loops.

**Failures.** A chunk whose request fails is asked again
(`translateJob.requestChunk`):

- A 429 with `Retry-After` (the account's AI calls are all in use) waits as
  long as it says, for up to two minutes in all (`SLOT_PATIENCE_MS`).
- Any other failure (the network, the provider, a stream cut off before its
  final line) is asked once more after two seconds.
- A 429 without `Retry-After` (the shared allowance used up) is final.

A chunk that still fails is dropped, not the job. Its paragraphs go into
their pages' `failed` sets and stay pending: the original shows, the page
is not done, and the next job asks for them. The workers go on. The job
stops, with the last failure's reason, only when:

- `FAILS_IN_A_ROW` (3) chunks fail with no success between them: the
  provider is down, not one connection lost;
- or a chunk's failure is final (`finalRefusal`): a 429 without
  `Retry-After`, or a 401, 403 or 503 from Gamma (not signed in, no access,
  no AI provider). A 429 whose `Retry-After` runs past the patience is not
  final: it drops that chunk only.

The server asks the model again for what its reply got wrong ("The
server") and names the paragraphs that still failed (`failed`). Those stay
pending too, without the queued wash. At the job's end the status counts
`untranslated`, the paragraphs left in the original: those the server could
not translate and those whose chunk's request failed (`unanswered`). The
end-of-job message says how many, with a "Try again" that runs a job over
just their pages (`retryUntranslated`) while the document is on screen.
When a chunk's request failed, it says the translation request failed (a
service's as much as a model's); otherwise that the paragraphs could not be
translated. The button on such a page asks for them too, and each such
paragraph keeps a mark beside it once the pill has gone ("A paragraph's
menu"). A page that could not be read (its segmentation threw) gets no
entry, so it is not done: the status counts it once in `untranslated` and
names it in `unread`, and "Try again" and the next job read it again. A
stopped job says "Translation failed: …" (or "Translation failed." with no
reason), with a "Try again" that runs the same job again (`rerun`: the
viewer keeps the last job asked for per document, its pages, `references`
and `focus`, where a page done meanwhile asks for nothing; one that was
"Translate whole document", `whole`, asks again for every page not done),
or, when Gamma found no AI provider to ask (the stopping request's
`refused` status, 503), "Open settings". So "Try again" after a stopped
"Translate references too" asks for those reference lists again, and after
a stopped retry of the untranslated paragraphs for their pages alone.

Once a session, a hint follows a job's end. When "Translate with" follows
the chat model and the session's first job that reached the model had a
median request over 20 s (`SLOW_MS`), the status line says a faster model
can be picked. Its action opens Settings → Language and Translation. It
waits for a job's end with nothing else to say. Replies the server had
cached or kept are not counted in the median.

Also once a session, a job that finishes on the PDF on screen says that
Export now gives a PDF with the translation in it, and its **Export…** opens
the dialog on Annotated PDF. It waits for a job's end with nothing else to
say, the model hint included, while no error or button holds the pill
(`statusFree`), and for a translation the export would carry
(`exportsTranslation`, the count the dialog reads). A page read through the
link proxy counts only with Save external PDFs on, the setting that keeps
the copy the export reads. A job stopped, one on another PDF and one whose
translation was hidden meanwhile leave it to a later job. A stopped job's
status stays short of complete, even when every chunk it had planned came
back before its last pages were segmented.

### Outliving the viewer

A document's translation lives in `pdf/translateState.js`, module state
keyed by the document's URL (upload names are content hashes, so the URL's
content never changes): its segmented blocks, its page entries, show/hide,
its job and the job's status. The viewer renders a document's `snapshot`
through `useSyncExternalStore` and starts its jobs; it only draws.

- **One job per document.** Jobs of different documents run side by side
  (under one limit, "The queue").
- **Unmounting the viewer does not halt a job.** The viewer's `DOC_CACHE`
  keeps a pdf.js document open while a job reads it
  (`translateState.pinned`), so the job can segment pages after the viewer
  is gone ([pdf_loading.md](pdf_loading.md)). The jobs reading a document
  are counted, apart from the kept states: a job halted at the session's
  end reads on until it has unwound, and a job started on the same
  document meanwhile keeps it open when that one ends. A document opened
  again comes back translated as it was left.
- **The page names its own document only.** The host's scope is `{url,
  pageId, stored, file}`: the page, the PDF it shows, whether the server
  stores it, and the page's file (what "Forget this paper's translations"
  names).
  While the next PDF loads, the viewer still shows the last one, but the
  host has moved on to the next page. A job (or a selection) on the
  document still shown then names no page (`scopeOf`) and is read from its
  opening text. So the next page's id never carries the wrong paper's
  text, brief or kept translations, and Forget is not offered.
- **Fonts and far pages.** Font names are cached per pdf.js document. A page
  with text to translate has its operator list built ("Text in figures"), so
  every segmented page lets its decoded images go again (`page.cleanup()`)
  unless it is within two pages of the one being read, in the document on
  screen.
- **At most `MAX_DOCS` (8) documents are kept**, the least recently used
  going first, never one whose job runs.
- **App's side** is `app/translateClient.js` (the chunk and brief requests,
  forgetting a document, ending the jobs) and `app/translateFollow.js`
  (what the app says, free of React). It follows the jobs of every
  document: a job's background-tasks row keeps counting after the reader
  leaves its PDF, and its cancel halts it. Since jobs of several documents
  run side by side, the row names what it translates, the paper and the
  language (`jobName`: "Translate p. 3 of “Title” into Deutsch", a
  paragraph's, or the document's), the paper by the page on screen as the
  job starts, which is the job's PDF. It ends on "Done", or on where it
  stopped. A job that did not do what it was asked ends as failed (the
  list's warning glyph, not a success's check): one stopped by failures, and
  one on a paragraph whose new text did not come ("earlier translation
  kept", or the paragraph could not be translated). A job with paragraphs
  left in the original did what it could and is done. The reading pill ("Reading the
  paper first…") shows only while the PDF on screen has a request waiting
  for the brief.
- **The session's end** (logout, another account or workspace) halts every
  job, its requests in flight aborted, and drops every document's state
  (`endTranslations` → `translateState.reset`), so no request goes out on
  the next session's cookie and the next account sees none of these
  translations.

### A paper opened again

A paper this browser last left showing its translation opens translated, from
the translations the workspace keeps ("Kept translations"), with no model call
and no AI provider needed. Nothing else opens translated: a paper never
translated here asks the server nothing and segments nothing, a colleague's
translation of it does not appear until this browser asks for one, and Show
original holds across a reload. Only kept rows come back: a translation the
workspace does not keep (a viewer's, or one made with Keep translations off)
lives in the account's memory, which the lookup never reads, so such a paper
opens in the original.

- **The memory** (`pdf/translateRecall.js`, App's `translateRecall`): the
  papers this browser left showing their translation, each with the key
  (language and model) it was shown under, in `localStorage` under
  `gamma-translated:<user>@<workspace>`, at most `RECALL_MAX` (300), the one
  used longest ago going first. A paper is named by the file the server keeps
  its translations under (`scopeOf`'s `file`), not by the viewer's URL, which
  can carry a share token or a cache-busting `annots=`; a duplicated page
  shows the same file. The viewer remembers it whenever its translation shows
  under the key (a job landed, a seed landed, Show translation), but only
  while the workspace keeps what it translates (`translateKeeps`: Keep
  translations on and the account may write the workspace), and lets it go
  on Show original whatever the role; Forget lets it go too. Storage that
  fails remembers nothing and breaks nothing: the paper then opens in the
  original.
- **The key it opens under** (`translateRecall.openKey`, App's `paperKey`):
  the settings' key when the remembered one names the same language and
  model, whatever connection reaches it (`sameModel`: `<lang>|<entry>:<name>`
  matches `<lang>|<other entry>:<name>` and `<lang>|server:<entry>:<name>`,
  since the rows are kept under the model's name): a connection removed and
  made again, which gets a new entry id, or a switch between one's own key
  and a shared one, finds the same rows. With no chat model on offer and no
  service picked in Translate with (the free service, or nothing, stands in
  for one), a paper left under a chat model's key in the same language opens
  under that key: its lookups name that model, its entries are kept under it
  and the export reads them. A job on such a paper asks that model too, which
  needs its connection back (the server answers 503, "AI not configured");
  picking a service in Translate with makes the settings' key the paper's,
  and the paper opens in the original under it. A selection always asks what
  the settings name (`onTranslateSelection`). A Forget reads the memory again,
  so the paper then follows the settings.
- **Seeding** (`translateState.seedPages`): when the document on screen is in
  the memory under the key it opens under, the page being read and
  those beside it are seeded once the reader has stayed there 300 ms
  (`SEED_REST_MS`), and more as the reader moves, near the view only. Each
  page and its neighbours are segmented as a job segments them, and planned
  twice (`seedUnits`): as a whole-document job plans them, a paragraph a page
  break cut joined with the page beside it, and as a job over one page plans
  them, split at the break. Which form went to the server depended on what
  was segmented then, so both are looked up, the joined one used first.
  Neither plan has `leave`, so a reference list kept under "Translate
  references too" comes back; and each block of a figure's text is looked up
  as the unit of its own that Translate this paragraph sent (`planBlock`),
  which no page's plan holds, so a label translated by hand comes back too
  and one never asked for stays original without making the page less done.
  The texts go in `POST
  /api/translations/lookup` requests within a translation request's caps
  (`lookupBatches`), usually one for the three pages. Each block still
  without a result takes its part of the first unit found that holds it; a
  block with one keeps it, and a page holding another key's translation is
  left alone. A block of a reference list nothing was found for is left as a
  job leaves it, so a page a job finished comes back done. A page where
  nothing was found gets no entry, nor does one where nothing but its
  running head or foot was (`translatePlan.runningBlocks`: a block of three
  lines or fewer whose text, numbers aside, stands in the same place on
  another segmented page, as the planner tells a running head) while it has
  more to translate than its reference list; a figure's text found is the
  page's own, wherever it stands. Those are the same on every
  page and were kept from the pages translated; found alone, they would
  count the page as translated and its export would write translated heads
  around an original body. A page of references a job finished (its heads
  translated, its list left) comes back done, and a page with an entry under
  the key takes them as any other block.
- **What did not come back** stays original and pending: the page is not
  done, the button offers to translate it, and the job it runs asks only for
  those blocks (a seeded entry is an ordinary entry under the key). A
  paragraph that uses a glossary term changed since it was kept is one of
  them ("The glossary").
- **Nothing is shown or hidden by a seed**, no job status changes and no task
  row appears. A page looked up under the key is not looked up again until a
  forget or another key.
- **Races.** No seed starts while a job runs on the document, and a job
  started aborts the seeds under way (their pages may be looked up again
  later). Forget moves the document's `epoch` on, so a lookup answering after
  it lands nothing, and so does one answering under a key no longer the
  document's.

### A page's entry

A page's entry holds results by block index: a translation; `null` for a
block that came back as its source (the original shows); `""` for a block
whose words all went to the part before it (a hyphenated word's tail alone
at a column's top: it is masked, nothing is written); missing while not
landed, or while its paragraph could not be translated (its index is then
in the entry's `failed` set). A block of a reference list that stays
original is `null` too, with its index in the entry's `left` set, which is
what tells it from a block that came back as its source. A job seeds each
page's state from its entry under the job's key, so a re-run keeps what is
done and asks only for the rest — a `null` block included, and a left block
is left again at its page's turn. A page is done when none of its blocks is
pending, which a neighbouring page's unit can complete; a done page is
skipped by the button and by "Translate whole document", so only "Translate
this page" asks for its `null` blocks again. An entry's `original` set,
present only when it has blocks, holds those the reader shows in the
original for now ("A paragraph's menu"). It is the reader's, not a job's: a
job hands out each entry with the set the page's entry holds as it goes
(`translateJob` reads the live entry through `seed`), less a block that has
just landed, which was asked for again. It ends with the entries, at Forget
and under another language or model, and is not kept anywhere.

The viewer requests `stream: true`. The reply is then NDJSON: `{"i":
[request indices], "text": partial}` lines as the model writes each
paragraph, then the same final object a plain call returns: `{translations,
model, cached, redone, failed, effort, brief}`.

- `cached`: every text was found in memory or among the kept translations,
  with no model call.
- `redone`: the paragraphs asked again ("The server").
- `failed`: the request indices that came back as their source because
  they still would not translate.
- `effort`: the level asked for, `""` for a service.
- `brief`: the translations carry the paper's brief, or there is no paper
  to have one. False means a brief was possible but missing.

`translate_context.partial_segments` reads what the reply has written of
each segment so far, the one in progress included, less a tag half-written
at its end. Lines are throttled to ~20/s (`_TRANSLATE_STREAM_INTERVAL`), and
a segment maps to every request index that shares its source text.
`stream: false` (the default) still answers in one JSON body. Asking again
never streams. An upstream failure mid-stream is an in-band `{"error"}`
line.

## The overlay

The overlay masks exactly the original lines of a block (plus the leading
between them) and lays the translation over them, at the size "How a
translation is set" gives it (the largest that fits its box, a body
paragraph no larger than the page's cap) — so figures a paragraph brushes
against are not masked over, and the layout never moves. The layer lies over the text layer (z-index 1 to
its 0) and under the highlights, find marks and link boxes. Translated text is
selectable and copyable, and its paragraphs are drawn in the page's reading
order (`transSelect.pageReading`, the planner's: columns one after the other,
a reference list the page holds read as text), keyed by block index, so a drag
across columns copies them as they read. While a page shows it, the original
text layer stands down under the masks only (`transBlock.maskedRuns` gives the
spans there, which take `.transMasked`: no pointer, no selection). Where no
translation is drawn, the original keeps working as on any page: a reference
list left original, text in a figure, a paragraph still waiting or failed,
one the reader shows in the original ("A paragraph's menu").

On the page, a paragraph whose translation is queued gets a faint accent
wash over its original lines, an in-flight one shimmers, streamed text
types onto the page behind a caret (`translateReply.partials` splits a
joined paragraph's partial text at the markers written so far), and a
paragraph that lands fades in. That is `TransPending`/`TransPara` in
`pdf/TransOverlay.jsx`, driven by the entry's `queued`/`busy`/`partial`
fields, which the job clears when it ends, halts or stops. The handwriting
the AI redraws or converts shows the same shimmer and a fade of its own as
it lands ([handwriting_ai.md](handwriting_ai.md#while-the-ai-works)).

- **Streaming masks what it has reached.** Only the lines the text has come to
  are masked (`transBlock.linesReached`); the others keep the original, washed
  and shimmering as a waiting paragraph's are. Where the text ends is read once
  an update, from the layout the fit has just made, and a mask once on stays on
  when the fit steps down. The original's spans stand down for the whole block
  while it streams, since `.transMasked` is set per block so that streaming
  touches no span: a line ahead can be read but not selected for those seconds.
- **It starts at the page's body size.** A body paragraph starts streaming at
  what the page's landed body paragraphs take (`pageCap`), so it does not step
  down to it in sight. A page with none landed starts it at the block's size.
- **The percentage counts streamed characters.** `translateJob` adds each unit's
  streamed length to the characters landed, up to 90% of the unit, since a
  target language runs longer or shorter than the source and the job should say
  100% only with the last reply in. A reply replaces its unit's streamed share.
  While it runs the figure never goes back, though the total grows as pages are
  planned, a request asked again streams from its start and a lost chunk gives
  its share back. The end is what the replies alone reach: 1 for a finished job,
  and for a stopped or failed one the share that came back, which can be under
  the last figure shown. A request that streams nothing moves it only at its
  reply, and Chinese, about half the length of its source, jumps at each reply.
- **A landing fades, a mount does not.** `TransLayer` compares each final text
  with the entry at its last commit (`transBlock.blockLanding`). What is new
  fades in through the opacity of `.pdfTransText` over `--dur-slow`, whether the
  job is still working on the page or this was its last paragraph, and so does
  a paragraph asked again at the queue's end. Words that streamed there are in
  sight already, so they start from 0.6, not from nothing. A mount has no last
  commit: a show after a hide, a scroll back, a page entering the look-ahead and
  a document coming back draw what is there. Reduced motion ends the fade at
  once, by the app's one rule. The fade's opacity is a stacking context on the
  text alone and for its length ("Masks, ground and ink"). `pdfTransLanded` stays
  for `.inkAiWork.landed`, which has no children over it.

### Selecting translated text

A translation is reflowed: nothing in it maps to the original's words. So a
selection with an end in translated text stands for whole blocks
(`pdf/transSelect.js`, read off the page by `PdfViewer`'s selection sync):

- **Which blocks.** An end in a translation is its paragraph's block
  (`data-block` on `.pdfTransPara`); an end in the original's text (a block
  left original, a figure's label) is the block its character lies in
  (`blockAt`, by where it is on the page). The selection covers the two, the
  body text between them in reading order when both are in it (furniture
  reads last, so from a caption or a figure's label only the two ends), and
  every part on this page of a paragraph a column break cut: the planner's
  unit, planned again from the segmented pages (`unitsOnPage`), less a part
  with nothing drawn over it. The page is the anchor's, or the focus's when
  only that one shows its translation (a drag from an untranslated page into
  the next one's translation); the other end, on another page, takes this
  one as far as its body text goes that way. An end off every page (a drag
  out into the notes or the viewer's margin) goes by the selection's
  direction (`selectionForward`: `Selection.direction`, else whether the
  range starts at the anchor): forward to the page's end, backward from its
  start. So a drag from a reference entry left original up into the
  translation above it is one selection over both.
- **What it keeps.** Its rects are those blocks' ORIGINAL lines (`blocksQuote`,
  scale 1, carried to the page's pixels as a text-layer selection's are) and
  its quote their original text, a block a line. A highlight, comment or link
  made from it lies where the paper's words are, with those words: it stays
  when the translation is hidden and exports as any other. The reflowed
  translation's rects are never stored; they would mean nothing once it is
  hidden. The popup offers no 文A for it, and its Copy (the iPad's) copies the
  translation as selected. The chat's PDF passage is the selected text, the
  translation.
- **Where the popup opens.** A mouse's at its release, as any; a finger's, a
  pen's or the keyboard's under what is seen selected (`shown`, the rects of
  the selected text of the translation and of the text layer's spans that do
  not stand down, `drawnRects`), not under the original lines it keeps: those
  of a paragraph a column break cut start in the other column.
- **Selected again.** A selection that stands for exactly the lines of a
  highlight on the page (`transSelect.sameHighlight`, within 1 pt) is that
  highlight, made from such a selection before: the popup is its bar (its
  colour, comment and link, as the highlighter's bar for a highlight it made)
  and the highlighter makes no second one.
- **A selection of the original** across masked spans (two blocks left
  original with a translated one between) keeps none of their rects: the
  range has them though they are not selected.
- **Its ways in.** Mouse, a held finger and a pen's drag select translated
  text as any text (`pdf/penSelect.js` reads a caret in `.pdfTransText`), and
  the popup opens the same way (`selectionTip.inTextLayer` takes a selection
  with an end there). No `menuPress` sits on a paragraph: its hold is the
  system's word selection, and the popup's ⋯ opens the paragraph's menu
  instead. A right press keeps the selection (the popup's dismissal takes
  the primary button only), so the menu's Copy rows can take it, and closes
  the popup, which lies above every menu and would cover its first rows; only
  the primary button's release ends a selection, so it does not come back at
  the right-click. The text layer's tool states reach
  the translation: the rectangle's crosshair and its no-selection, the ink
  ring's hidden cursor, the selection colour, and no selection on a read-only
  page or with a tool armed (`.still` on the layer).
- **Marks over a translation.** A link box whose middle lies in a block with a
  translation drawn is not drawn (`linksOff`): its words have moved, it would
  take a click or a drag's start meant for the translation, and the citation
  marker inside the translation is plain text. A find hit there marks the
  paragraph instead, as an outline round its block, one per block, active when
  any hit in it is (`marksOver`): the search reads the original, and the
  paragraph is all that still holds where the hit is. Highlights stay where
  they are: they are the original's places by design. But a highlight's rect
  whose middle lies in such a block takes no pointer (`onBlocks`): one made
  from a translation covers the whole paragraph, and would take every
  right-click, drag and press meant for it. Its menu is the paragraph's
  Highlight row ("A paragraph's menu"); its note badge still opens it. While
  the original is peeked at (Alt) these stay as the translation has them.

### A paragraph's menu

A paragraph on a page that shows its translation has a menu of its own, for
its text and its translation. A paragraph a column or page break cut (one
planner unit, `translateState.unitOf`: the unit a job asking for that block
alone under the viewer's key would plan, from the pages segmented so far) is
one paragraph here: every row acts on all its blocks.

- **Its ways in.** A mouse's right-click on a block of the page: one
  delegated `onContextMenu` on `.pdfPageWrap` (`PdfPage`'s `onTransMenu`).
  It runs after a highlight's, a link's or a note's own menu, which call
  `preventDefault`, and only while the translation shows under the viewer's
  key, with no tool armed and not in rectangle mode. The block is the
  overlay's paragraph under the pointer (`data-block`), or where the overlay
  draws nothing over it (waiting, failed, left original, shown in the
  original, peeked at) the block whose lines hold the point
  (`transSelect.menuBlockAt`, `blockAt`). A block that is no text to
  translate (a display equation, an author line) has none, and the
  browser's menu stays. A finger's or a pen's hold is the system's word
  selection, so nothing listens for one (no `menuPress`, and a `contextmenu`
  that followed no mouse press is left alone): a selection's popup carries a
  ⋯ (`MenuButton`, "Paragraph actions") that opens the same menu for the
  block the selection starts in, from the text layer as from a translation.
  A paragraph that could not be translated has a mark, which opens it too
  (below). Opening it, the viewer reads what the menu says of the paragraph
  (`transSelect.paragraphMenu`) and hands it to the host
  (`onParagraphContext`), indices and texts, never DOM nodes. App renders it
  beside the highlight menu, as one `ContextMenu` of `menuGroups`, and its
  rows act through the viewer's `translateCtl`.
- **Copy translation** and **Copy original**: the selection's, when one is
  held in the paragraph (the translation as selected, and the original of
  the blocks it stands for or as selected in the text layer); otherwise the
  paragraph's, its translation as the overlay draws it (a formula as its
  extracted text, the parts read on without a space between Han or kana)
  and its original a block a line, every part of the unit read from the
  segmented pages, a part on a page with no entry under the key included.
  Copy translation only where there is one.
- **Show original here** / **Show translation here**: the paragraph's blocks
  go into the entry's `original` set (`translateState.setOriginal`). The
  overlay draws nothing over them, so their original is the text layer's
  again, selectable and taking the pointer, and their marks go back to the
  original's places. Export still writes them translated ("Exporting the
  translation"). Not while the original is peeked at.
- **Translate again**: the paragraph asked for again, alone and afresh: a
  job with `focus: {pn, i}` and `fresh` (`translateCtl.translateUnit`,
  `startJob`, `createTranslateJob`). It segments the pages beside its block,
  plans that block's unit (`translatePlan.planBlock`, with nothing counted
  as filled, so the unit is the whole paragraph), and sends one request with
  `fresh: true` and `wait_brief: true`, the first too. A page beside it that
  holds a translation under another key is not the job's
  (`translateJob.focusPages`): the paragraph is then its part on this page,
  and that page keeps the other language's or model's translation, which an
  entry of this key's with one block would have replaced. The menu takes its
  unit by the same pages. It owes its page
  nothing else: no block of the page shows as queued, the page's
  untranslated, left and failed blocks stay as they were, and `done` with
  them. While its request is out, the paragraph's text stays and shimmers;
  its new words stream over it with every mask in place, and land with the
  fade a new text has; the page's other paragraphs keep their size ("The
  page's cap"). If the request fails, or the server could not
  translate it, the paragraph keeps the text it had, with no failed mark;
  the status counts it in `unchanged`, its row says "earlier translation kept"
  and the pill that the paragraph could not be translated again, with Try
  again. Not offered for a service (`engine:`), which answers the same text
  the same way.
- **Translate this paragraph**: for a paragraph with no translation: one
  that came back as its source, failed, was left original in a reference
  list, or is a figure's text. The same job, so it bypasses the list check,
  and a figure's block is a unit of its own (`planBlock`): its text is drawn
  and exported once it has one. A failure marks it failed, and the pill says
  "This paragraph could not be translated.", with Try again.
- **Highlight**, a flyout: when a highlight lies on the paragraph (the one
  under the right-click, else the first with a rect on the block,
  `transSelect.highlightOn`), its own menu's rows (App's `highlightRows`:
  colour, link, reference point, Move, Comment, Properties, Delete), since
  its rects over the translation take no pointer ("Marks over a
  translation").
- **While a job runs** on the document, the rows that start one are greyed,
  with the reason as their title: one job per document.
- **Read-only** (a viewer of the workspace): the Copy rows alone, and the
  highlight's rows a viewer has.

**A failed paragraph's mark.** A paragraph a job could not translate keeps
the original under nothing but a thin bar at its left edge, in the warning
tone at part strength (`TransFailed`, `.pdfTransFailed`), drawn while the
translation shows, after the job too (`blockShow` gives `{failed}`; the
page's layer is drawn for it alone). The bar is a button outside any text,
so a selection or a pen's drag never takes it in. Its press opens the
paragraph's menu below it, where Translate this paragraph is. A job on one
paragraph keeps the page's other marks; one that lands the paragraph takes
its mark away.

### How a translation is set

The overlay is laid out once, at a fixed reference scale, and scaled like a
page, so its text never re-wraps and a zoom does no layout.

- **One scale.** `.pdfTransLayer` is the page at `REF_SCALE` (`transBlock.js`,
  2 px per pt) and carries one CSS transform to the page's scale, origin top
  left, as the text layer does from scale 1. A zoom commit writes that one
  transform; the paragraphs' memo holds. The layer is not promoted
  (`will-change`), which would rasterise it at the reference scale and blur it.
  `text-rendering: geometricPrecision` keeps advances linear in the size.
  Blocks, masks and their pad are all in pt times that scale, so
  `maskedRuns` works out what they cover at scale 1.
- **Only near the view.** `PdfPage` renders the layer under `overlays`, as the
  highlights and link boxes are, and while `transWorking` still says so.
- **A ratio per block.** A paragraph's size is the block's times `--fit`, the
  largest ratio that fits its box. `transFit.js` keeps it in a `WeakMap` on the
  page's `paras` array, by block index and text, so a remount measures nothing.
  A landing paragraph finds it by halving, to within 1%, in at most nine
  reflows. A streaming one keeps the ratio it has and takes one step down
  (0.93) when it stops fitting, since its text only grows. The record notes how
  many of the block's formulas were text at the measure, and a block measures
  again only when one of those has become a picture: `TransMath` says so
  (`onPicture`), on its own page's paint or on another's.
- **No box, no measure.** With the PDF hidden (`.viewerWrap.pdfHidden`,
  `display: none`) a page that a held citation keeps rendered stays mounted, and
  a job can land on it. Its boxes are 0 high there, every ratio would fit, and a
  kept ratio is never asked again. So a paragraph whose box has no height
  measures nothing and keeps nothing: it watches its box (a `ResizeObserver`,
  only in that state) and, once the box has a height again, measures as when a
  formula became a picture, and the page's cap with it.
- **A floor, no cut.** The floor is a ratio, `MIN_FIT`, 5% as in the export, so
  the screen and the file give up on the same blocks and low zoom changes
  nothing: text is small and complete there, as the original is. No box clips.
  `.pdfTransText` is at least its box and grows past it, so its `offsetHeight`
  is the measure, which `scrollHeight` is not: that counts a tall formula's
  drawn box hanging past the last line. A block that does not fit at the floor
  runs out of its box, in sight. On nine real papers (145 pages, about 1650
  blocks per language) a stand-in model at 1.2 times the English in German and
  0.45 in Chinese ran none out, nor did 3 times on three of them; no block came
  under 0.35.
- **The page's cap.** Paragraph by paragraph a page reads as a patchwork, so
  the body paragraphs share a cap: `bodyBlocks` takes the blocks of two lines or
  more within 6% of the page's size (`pageFlow.bodySize`) that are prose and no
  heading, so one-liners and table cells are out, and `pageCap` the lower
  quartile of their ratios. `.bodyText`'s font size is the block's times
  `min(var(--fit), var(--cap, 1))`, so a paragraph smaller than the cap keeps
  its own and applying the cap is one write of `--cap` on the layer. `TransLayer`
  writes it in its layout effect, which runs after its paragraphs', and only
  once no job works on the page and no paragraph of it is in flight or
  streaming (`transFit.layerCap`), so text that has landed does not creep: one
  settle, at the job's end. A job on one paragraph (Translate again) owes a
  done page nothing, but its paragraph trades its landed ratio for a streaming
  one, and the cap of the rest would be another, so the untouched paragraphs
  would grow and shrink back. A job asked over a page that has one leaves it
  as it is until it ends: the settled cap is kept beside the ratios, on `paras`
  with the entry's translate key (`transFit.settledCap`, since the blocks are
  one array under every language), and a layer mounted while a job works (a
  scroll past the look-ahead, Hide then Show) writes that one. A page with none
  settled under that key, a first job, gets its cap at the end.
- **Face.** `--font-page` (`tokens.css`): Times New Roman first, for its lining
  figures, then a serif CJK face; `:lang()` picks those for `zh-TW`, `ja` and
  `ko`. The layer carries `lang` from the translate key
  (`translateRecall.langOfKey`: the key opens with a code of
  `TRANSLATE_LANGS`, a BCP-47 tag), and so does the
  selection panel's translated text. `hyphens: auto` needs that `lang`. The fit
  relies on no hyphenation: a browser without the language's dictionary breaks
  fewer words and measures that. `overflow-wrap: anywhere` keeps a long word
  in a narrow box.
- **The paper's own face** (`pdf/paperFace.js`). `blockFace` names the font
  most of a block's text is in by the characters, formulas left out: `font`,
  pdf.js's id for it (`g_d0_f2`), and `face`, its name without the subset tag
  (`CMR10`), when the file holds the font (`pdfFonts` reads pdf.js's
  `missingFile`) and it is no Type 3 font. pdf.js loads an embedded font into
  `document.fonts` under that id, its glyphs mapped from the characters it
  knows (pdf.js 4.4's `toUnicodeExtraMap`), so the block is set in it
  (`.paperFace`, `--paper-face`, then `--font-page`) when it draws every
  letter of the block's words, blanks and format characters aside: TeX's fonts
  have no space, which the browser takes from the next face. `hasGlyph` asks a
  canvas: a character measured in the font with two different fallbacks
  behind it is as wide both ways only when the font has it, and its glyph must
  hold ink, since a subset can keep a letter's place and empty its outline. It
  keeps the answer per font and character. A subset holds only the letters the
  paper used, so a translation into another language often needs one it
  lacks; the block then keeps `--font-page` whole, one face a block, not a
  letter here and there in another. The paper's font carries its own weight
  and slant, so the block drops `.bold` and `.italic`. A streaming paragraph
  can lose the face when a letter it lacks streams in; the fit keeps the face it
  measured (`transFit.needsFit`), so it measures again then. The id is the open
  document's: the translation keeps a paper's blocks longer than the viewer
  keeps its pdf.js document (`MAX_DOCS` against `DOC_CACHE_MAX`), and
  destroying the document takes its fonts out of `document.fonts`. So the face
  is used only while its FontFace is there (`fontLive`), and what `hasGlyph`
  read is kept with the FontFace it read it from; a paper opened again after the
  viewer let it go sets its translation in `--font-page`, with `.bold` and
  `.italic`, since its blocks still name the old ids. The export decides
  the same on the file's own copy of the font ("Exporting the translation").
- **Leading and shape** (`transBlock.blockSetting`). Latin and Cyrillic targets
  take the block's own pitch, the median step between its lines' tops over its
  size, held to 1.12 to 1.30 (1.15 for one line). CJK keeps 1.28: its glyphs fill
  the em. A paragraph opener's `indent` is its `text-indent`, in the page's
  terms; a hanging indent has nowhere to go, and a centred block has none: its
  first line is only shorter than its second. A block is justified when the
  original is (`justified`, "Centred blocks and room") and its box is 18 em
  wide or more, and ragged (`.ragged`) otherwise: a narrower box opens wide
  gaps. The export takes the same leading, indent and justification
  ("Exporting the translation").
- **Weight and slant.** `segmentPage` sets a block's `bold` and `italic` when
  90% of its text by the characters is in such a face, formulas left out
  (`blockFace`, `pdfMath.faceStyle`), so a bold lead-in or an italic phrase
  leaves a paragraph alone. They come from the font's name: TeX's letters (CMBX,
  CMTI, SFBX, SFTI), the word in most (`-Bold`, `-Semibold`, `-Italic`), Nimbus's
  `-Medi` and `-ReguItal`, AIP's `.B`, `.I`, `.BI`. A light CJK weight
  (`-DemiLight`) is no bold, and the short slant takes its capital (`Ital`, or
  `ITAL`), so `DS-Digital` and `Capitalis` are upright. A Type 3 font says
  nothing. `src` and `text` do not change, so kept rows stay valid. CJK targets
  take no italic: their fonts have none. A bold CJK block is bold only in a face
  that has a bold (`font-synthesis: none`): the browser's synthetic bold on
  SimSun fills its strokes in at body size. Bold inside a paragraph is not
  carried.

### Centred blocks and room

`pdf/pageShape.js` `markShape` runs where a page is segmented, after
`markFigures`, so the overlay and the export read the same blocks: `centred`,
`justified`, `room` and `foot`. It takes
the columns from `pageLayout` (`edges`): a band of one column has the text width
as its edges, and a block out of the flow, a caption or a running head, takes
the nearest column that holds most of it.

- **Centred** (`isCentred`): a block at least 1 em clear of both edges with
  every line within a quarter em of the column's middle (or 1% of its width),
  and, with more lines than one, lines that differ by 2 em or more: lines of one
  width are a justified block inset from both edges, as an abstract is. A long
  line opening in lower case goes on from somewhere. Over 467 pages of 60
  papers it marks 412 blocks, 10 of them of several lines.
- **Justified** (`isJustified`), for a block that is not centred: its lines but
  the last end within half an em of one right edge (a hyphen or a protruded
  comma stands a little off) and its lines but the first start within half an
  em of one left edge, where a ragged block's lines end where their words do.
  Three lines or more say so by themselves. Of two lines the first is the
  block's widest whatever the setting, so it must also end at the column's
  edge; one line must fill the column from its left edge, or from an opener's
  indent of 2 em at most: a paragraph's first line at a column's foot. On the
  fixture paper every body paragraph is justified, and its table rows, its
  lone lines and its reference entries are not.
- **Room** (`roomOf`): a one-line block's `room`, `{x1, x2}`, the box its text may
  fill out to the column's edge, 1 em short of a line or figure beside it (one
  that shares a quarter of the shorter one's height: lines above and below touch
  through their glyph boxes). It reads the other blocks' lines, not their boxes:
  a paragraph's box runs past a heading beside its first line, and a line it
  lies inside (a fragment of that line, a piece of a formula) leaves it no room
  either way. Over 663 pages of 60 papers, no room runs over text on its row,
  where 20 did by boxes. `markFigures` gives the figures for that. A centred block
  grows both ways alike. A block over a figure stays in its box. The masks stay
  on the original line (`blockBoxes`), so nothing else on the page moves. So a
  short English heading does not limit a long translation: German at 1.2 times
  the English left 21 of 331 one-line blocks under 0.8 of their size over five
  papers, mostly author lines and figure labels, and a heading keeps its size
  above the body wherever the column holds it in one line. No box grows
  downward, so a heading the column cannot hold shrinks.
- **Foot** (`footOf`): where a block's text stops, when another block's first
  line starts inside the foot of its box, below the middle of its last line and
  across its text's width. Tall glyph boxes make such seams: the next block's
  text opens at the top of its own box, so a block filling its box to the bottom
  wrote its last line over that first line. `blockBoxes` ends the box there,
  less nothing of the pad, since both texts start a pad above their lines; the
  line boxes may still touch by 0.15 em, which holds no ink (the half-leading
  and the room over the ascenders and under the descenders come to about a
  quarter of an em in Times and in a CJK face). The masks stay on the lines. Of
  11056 translated blocks on those 663 pages, 4213 have a foot, which takes 0.12
  em on average from their box, and 71 lose over half an em, mostly formula
  lines and figure labels set close.

### Masks, ground and ink

**The masks.** `.pdfTransLayer` is the one stacking context: its `transform`
makes it. All the masks of all the blocks sit at a negative z-index, so they lie
under all the text in one pass, and no block's mask cuts the last line of the
block before it. Nothing between a formula's crop and the layer may make a
stacking context (`opacity`, `transform`, `filter`, `contain`, a `z-index`, an
animation of those): that would keep masks apart from the text, and isolate the
crop's blend. A landing's fade is the one exception, on `.pdfTransText` alone and
for its length: a crop has its own paper to blend with, and the fading text is
still above every mask. On `.pdfTransPara` it would take the block's masks into a
context of their own. A mask's bottom is the next line's top, or its own bottom when
the glyph boxes overlap and the next line may be shorter (`blockBoxes`).

The crops lie between the two: `.pdfTransMath` takes `z-index: -1`, over every
mask and under every line of text, its paper and canvas blending in a context of
their own. A tall formula's crop hangs into the leading and stretches no line
("Formulas"), so its paper reaches over the descenders of the line above and the
tops of the line below, and that text is drawn over it. Over a figure, whose
text has a background of its own, a crop keeps its place in the text, or the
background would hide it. A tinted mask's ground, and the paper under it where
the treatment blends, run past the mask and are cut at its edge
(`clip-path: inset(0)`): two layers each smoothing an edge of its own left the
paper showing through, a hairline round every mask. A blended pair is cut once,
as a group; an unblended one the clip cuts layer by layer, so it has no paper
under it, which an opaque ground does not need. On tint p1 at DPR 1, of the
masks' pixels and one around them, those off the panel by more than 3 levels
went from 9896 to 0 in gamma-dark, 7019 to 0 in light, 7949 to 0 flipped.

The text has no background: past the masks it is on the page's own ground. A
block over a figure (`overFigure`) does need one, or its lines would run through
the drawing. A line's background is painted with its text, so such text is the
one exception, at a z-index between the masks and the other text: no background
crosses another block's text. Two such blocks that touch can still cut each
other; that was one pair in 24 such blocks on 467 pages.

**The treatments.** `.pdfViewer` holds `--trans-paper`, `--trans-ink` and
`--trans-filter`, set once per treatment in `app.css`. `--trans-ink` is what the
treatment makes of black: `#f0f0f0` flipped (`invert(0.94)`), white at 90% over
the paper in gamma-dark, the paper at 18% in the multiplied themes. A crop's
blend reads what is under it in the layer, and the page is not, so `.pdfTransMath`
paints `--trans-paper` behind its canvas: the blend then gives what it gives on
the page, wherever the text runs.

**Ground and ink** (`pdf/transPaint.js`) are read from the base bitmap, raw:
once a page, for all its translated blocks at once, kept beside the ratios
(`transFit.paintsOf`, on the `paras` array), so a zoom, a remount, a language or
a theme reads nothing. `TransLayer`'s layout effect reads at the first paint it
sees, through `pageRaster.basePixels`: the page's bitmap goes into one scratch
canvas (`willReadFrequently`) at 2 px per pt, unsmoothed, so that a stem keeps
its colour, or coarser (`transPaint.sampleScale`): no finer than the bitmap
itself, and no more than 4 MP, as the server's sample. A canvas past the
browser's limits reads back zeros (a 10000 pt page painted black masks) or
throws (14400 pt: `Out of memory` in a layout effect, which took the app down).
A read that fails is kept as a page with no paint, so it is not tried again at
every paint. A block takes the pixels of its lines and of the 1.5 pt ring around
them apart, about 6000 at most; a row of a line's ring that lies in the next
line's height is that line's, since ink reaches it.

- The **ground** is the commonest colour, buckets of 8 levels a channel taking in
  antialiasing, then the exact colour that leads the bucket, since a mask's
  edge shows one level. Every channel at 250 or more is plain paper. It must lie
  around the lines too, 5% of the ring, where no ink reaches; else the block has
  no paint. Over a gradient or a textured panel the text's own colour can be the
  commonest of many: black text on a panel of 100 steps gave a black mask under
  dark words on screen and near-black ones in the file.
- A colour that fills half of the lines themselves and reaches the ring at all
  (1%) is their ground before that: a band no taller than its line has paper in
  the ring above and below, which with white words on the band outvoted it, and
  the file had a white stripe with the band's green as its ink.
- The **ink** is the commonest colour among what lies farthest from the ground,
  which leaves antialiased edges out. It counts with a channel spread of 40 or
  more, or on a dark ground (white on a band). A grey on a light ground is the
  page's usual ink: thin black text at a low zoom holds no darker pixel, and a
  grey heading loses little. Half of those pixels must lie within 48 levels of
  the mode: a black paragraph thick with blue citations is no blue block. It
  must stand 2:1 from its ground in contrast: the viewer's bitmap draws a file's
  own highlights, a yellow one ties black for farthest from white and has more
  area, and whole paragraphs came out yellow on paper at 1.43:1. The lowest
  genuine ink over 142 papers is 2.55.
- A dark ground with no ink of its own has no paint: the usual ink could not be
  read on it, on paper it can.

A block with neither has no paint and costs nothing. A sample writes classes and
custom properties only, so nothing is laid out again. Over the first 12 pages of
146 files (20247 blocks translated once text in figures is left out), these
rules gave a different paint than the commonest colour alone on 67 blocks: the
two fixtures above, 64 paragraphs under a file's yellow highlights, and a legend
in dark blue on blue, which goes on paper.

- A tinted block's masks take `.tinted`. Each paints a `::after` of the raw
  `--ground` that the canvas's own rules treat, and under a treatment that
  blends the canvas (gamma-dark's screen, the multiplied themes) the paper it
  blends with in a `::before` (`--trans-under`, none elsewhere), both cut at
  the mask's edge by its clip: the
  treatment selectors in `app.css` name it, as a selector of its own since
  `:is()` takes no pseudo-element. The mask's z-index makes it a stacking
  context, which holds the blend. No treatment is worked out in JS. On tint p1
  the abstract's masks match its ground to the level under every treatment, and
  one level off in gamma-dark.
- An inked block takes `.inked`, and its words `.pdfTransInk`: `--ink` under
  `filter: var(--trans-filter)`. The filter stays off anything that holds a crop,
  which is the page's picture and has been treated already, and a filter would
  isolate its blend. So each run of words is wrapped, and a filter makes a
  stacking context per run, a cost only the few inked blocks pay. `--ground` and
  `--ink` shadow the theme's tokens of those names inside the paragraph, where
  nothing reads those. `--trans-filter` gets as near to the canvas's treatment as
  a filter can: the opacity is in it, the multiply and the screen are not.

On five pages of each of 60 papers (PyMuPDF's pixels, 298 pages, 4996 blocks),
blocks came out tinted and inked by the hundred: answers in red and blue, a
referee reply, white on a journal's band, a slide's navigation.

**What a read costs.** The copy off the GPU is the cost, so it is once a page
and not once a block. On an RTX 4070 in headless Chrome with a hardware canvas,
a page of 2131 × 2802 took 2 to 4 ms to draw and 12 to 34 ms to read, during a
job and a scroll. In the commit of a base paint at load, while pages raster, the
first pages took up to 32 ms to draw and 50 to 110 ms to read, and later ones
13. A sync read waits for what the GPU has queued: one 2.5 s after load with no
frame presented took 630 to 790 ms, even of a 512 px canvas, and 23 ms after
8 s. Passing an `ImageBitmap` to a worker kept the main thread to 0.2 ms, with
the read taking 19 to 30 ms there; it is not built. Deciding takes 4 ms a page,
30 at worst. With the software canvas of headless Chromium the read is 1.3 and
4.5 ms.

A zoom does not lay the overlay out again on every frame. `TransLayer` is
laid out at the zoom its page settled at, sized to the page at that zoom,
and stretched to the page box by the ratio of the layer's zoom to it
(`--viewer-scale`, [ui-design.md](ui-design.md#zoom-gestures-in-a-viewer)).
A wheel train renders none of it, and once the zoom has stopped for
`ZOOM_SETTLE_MS` the paragraphs fit their boxes again, once. That is only
while the page is inside the look-ahead. A page away from the view keeps
its last layout, stretched, until it comes back.

## What a request carries

Geometry never leaves the client. `pdf/pdfTranslate.js` segments a page's
pdf.js text runs into blocks:

- `textRuns` maps TextContent items through the scale-1 viewport, with each
  run's real font name (`pdf/pdfFonts.js`, below) and glyph ascent and
  descent (pdf.js's 0/0 for a font it has no metrics for counts as none).
  Rotated text (an arXiv stamp, a rotated axis label) is left out.
- Runs group into lines by their glyphs' vertical middle, anchored on the
  line's longest run, so a big operator (CMEX's Σ hangs below its baseline)
  joins the line it is drawn on. A stretch of a line set apart from the rest
  by a hard gap, three characters or more, moves to a nearer line within
  reach that has text of its size beside it: an inline formula whose glyph
  box sorted it onto the other column's line goes back into its sentence. A
  short superscript or subscript chain that formed a line of its own is
  folded back into the line whose glyph it touches; a line of smaller text (a
  reference beside the body, a sidebar) never folds.
- A line splits at a hard gap (≥ 2.2 line heights) and at a soft one only
  where a column gutter runs: `isGutter` looks for a stretch of the gap that
  the lines around it leave open (a river), whether or not the columns'
  baselines line up.
- An inline fraction's numerator and denominator stand clear of the glyphs
  around them, under the words of the line above and over those of the line
  below. A line they stand at the height of (within 0.55 of its height) takes
  them from up to 0.8 line heights away, so they join its formula and not those
  lines. Computer Modern's `√` hangs from its baseline, which its font's metrics
  do not say, so it is placed by its own glyph box.
- Lines merge into blocks by step, size, indent and font. Each block keeps
  per-line rects, its first line's `indent`, its extracted `text`, and
  `src`/`spans` ("Formulas"). A block is translatable when its text without
  formulas is prose and it is no author line ("Formulas"). A display
  equation's number alone at the column's right edge (`(1)`, `(12a)`, `(A.7)`)
  stays a block of its own: the paragraph under it, which starts at the column's
  left edge, does not take it as its first word.

`pdf/pageFlow.js` reads a page's structure and `pdf/translatePlan.js` plans
what goes to the model along it, for the viewer and for the bench alike:

- **Reading order** (`pageLayout`): the page is cut where a band of
  whitespace runs across its full width with text in both halves above and
  below (above a reference list; not beside a figure). Within a region,
  blocks wider than 60% of the text width stand alone; the narrower ones
  between them read column by column. Furniture reads last.
- **The body flow** (`pageFlow`): reading order without furniture — text in
  a figure ("Text in figures", below), page numbers, smaller running heads and
  footnotes, captions ("FIG. 1.", "Fig. 1 Conceptual overview", "Figure 2
  (left):", "Extended Data Fig. 3 |", not "Figure 1 compares"), and one-line
  blocks set apart in the top or bottom margin unless they start in lower case
  or are a numbered or named heading. A section number is a number of up to
  three digits, a Roman numeral, or a capital with a period, before a word in
  capitals ("A. Results", "A1. Review", "2 Methods", and "A Proof", where the
  capital has no period, but not "A similar argument", "2 to incident power"
  or "70569 Stuttgart"). A byline only looks numbered and is no heading: "M.
  Saffman et al.", "12 J. Smith and B. Jones", "12 | Nature | Vol 600 | 14
  October 2021". "A. Results and discussion" has a word where a byline has an
  initial, and Wiley's "1 | INTRODUCTION" has one bar. A long block that is no
  text (a display equation, a table of numbers) stays in the flow, so a
  paragraph never joins across it, unless it stands in a figure ("Text in
  figures") or is a journal's folio: one line in the top or bottom margin of
  digits and capitals, hyphenated numbers after them ("035002-2", "1700123",
  "L12345"), with no space, period or parenthesis. A display equation or a
  figure's row of ticks can stand in the margin too, so no other block with no
  text goes there. A page with no flow of its own (a full-page figure and its
  caption) is walked past, among the pages segmented so far.
- **The reference list** is read by `pdf/pageLists.js` and stays original
  ("The reference list", below). A list the page holds by itself takes part
  in its structure: it does not count towards the page's body size, which on
  a page ending in a small list would flip to the list's, it is no
  furniture in whatever size it is set, and the page's text width counts it
  (else, beside a column of body text, its lines read as wide and stand
  between the body's), so it has columns. A page whose paragraphs outside the
  list hold under 5% of its text reads by the list's size.
- **Joins** (`continues`): a paragraph that a column or page break cut in two
  (or three) is ONE unit, its parts joined with `{br}`. A joins B only across
  a column or page break, when:
  - A is running text of two or more lines that ends mid-sentence, judged on
    its words without formula tokens.
  - B is body text of the same size, within 5%, that opens no paragraph of its
    own: no heading or caption, no first-line indent, running text itself. The
    rest of a caption cut from its first line reads as body text and is set a
    size smaller. Across the 142 papers measured, the parts of 14 joins
    differed by over 6%, none by less, and 13 of the 14 were wrong.
  - Where the indent tells nothing (a page without indents, a one-line B), B
    reads on: it starts in lower case or follows a word no sentence ends on.
    A one-line B also ends a sentence, fills the page's measure, or
    introduces a display equation ("…in the rotating frame the" /
    "Hamiltonian reads").
  - A word hyphenated at A's end goes on only in lower case, and moves whole
    into A's part.
  - A join across a page break needs both pages in the job. Whichever page is
    planned first claims the unit, and it fills both.
- **Wrapped headings**: the segmenter keeps a line that starts deeper than the
  one above apart, which an author stack needs, so a centred heading that wraps
  comes as two blocks ("II. EXACT DYNAMICS OF A RELEASED" / "WAVE PACKET"). The
  planner joins them as a paragraph is joined, within one column, when A is a
  heading of up to two lines that fills half the page's measure, and B is one
  line of A's size (within 5%), right under it (0.25 of a size), on the same
  middle (0.06 of a size), and no caption or numbered heading. A also has to
  read as a heading by its words: a section number that is no superscript mark,
  an end on a word no phrase ends on (not a comma), or capitals with no comma.
  Names and affiliations set large or in capitals do none of that and stay
  apart. Across a page's top margin the second line is furniture and the two
  stay apart.
- **Units and chunks**: one unit per translatable block or chain, in reading
  order; a chunk closes at 10 units or 2,500 characters (`chunkUnits`). Each
  unit renumbers its blocks' formula tokens into one sequence and knows whose
  each is.
- **Context**: each chunk carries `before` (the flow block before its first
  unit), `after` (after its last), each up to 1,500 characters as extracted,
  and `section`, "The section" below.

### The section

`section` is the nearest section heading before the chunk's first unit in the
flow, over earlier pages as far as they are segmented, however far that is. A
block outside the flow (a caption) sits under what stands last on its page. It
goes only into the prompt, not into any key, so a kept row does not depend on it:
a job started in the middle of a paper sends fewer sections, not other rows.

A heading names a section (`isSection`, `sectionsOf`) when it:

- opens on a capital or a number, not on punctuation or in lower case (a line of
  a paragraph or of a title);
- has a word of four letters, or two CJK characters: a bare formula or a label
  ("RB BR RR") has none;
- breaks off nowhere, on a comma, a hyphen or a word no phrase ends on;
- is no sentence: six words or more ending in a period;
- does not stand right under text of its column at the body's size, unless that
  text is a heading: "1. The resonator supports…" is a line that opens on a
  number, and a contents page's lines stand like that.

It is also no block of a reference list, and no running head: text that stands
at the same height (within 4 pt) on another page read so far, its numbers left
out. A contents line that recurs as a heading stands elsewhere and stays. A
wrapped heading is one section, its lines joined.

### The reference list

A paper's reference list goes to no model: names, journals and years read
as written in any language, and translating them costs a seventh of a
paper's characters. Most lists have no heading of their own and a hanging
indent cuts an entry into two blocks, so `pageList` (`pdf/pageLists.js`)
reads the shape of a page's blocks, in no order of work. The rule was scored
on 34 papers with a list, read by hand: 99.9% of the list blocks found, in
all 34 ([research/babeldoc-translator.md](../research/babeldoc-translator.md)
"As built").

- A **cite** is a block holding a volume, page and year; a doi or arXiv
  identifier; a year with a name, an opening `[n]` or `n.`, or `pp.`; or a
  year closing the block, not a date. It has at most 16 lower-case words per
  year. A caption or a section label is none.
- The cites of the page's commonest size group into columns, then into
  **chains**, split where the gap to the next cite passes 8 times their size.
  A chain of one (a running head, a stray citation) does not count. Sizes are
  the cites' own, not the page's body size, which is read with the list left
  out.
- A chain's **blocks** are everything in its column at its size between its
  first cite and its last, and the blocks that touch those ends and read as
  entry pieces. It stops at a section label (Acknowledgements, Author
  contributions, Data availability and so on), a "References" heading, or a
  block of 20 or more lower-case words.
- A page **qualifies** on 6 chained cites, or on 2 beside a page that has 6
  by itself. A chain that counts only through its neighbour needs a cite
  shaped as an entry, by more than an identifier: a Data availability
  statement's doi makes it a cite, and two of them after a list are no end of
  it. A page not segmented counts as having none, so a page alone decides by
  itself. "Translate this page" may see neither neighbour; the short end of a
  list is then translated as any text is.

`pageList` marks no block: blocks are cached and shared, and the page's
layout reads `translate`. Its answer is a summary kept with the page's flow
in the planner. `createPlanner` takes `leave(pn, i)`: `planPage` calls it for
a block of a list and skips the block, unless it has its translation
already, and walks of a paragraph stop at the list's edge in both
directions. The job then makes the block `null`, takes it out of `pending`
and adds it to the entry's `left`, so the page can be done, and progress,
which counts chunks, does not see it.

A left block is the original on the page, for all the original is good for:
the overlay draws nothing over it, so its text can be selected, highlighted
and copied on a page that shows a translation around it, as on one that holds
only references. An annotated PDF keeps it in the original too, and a page of
nothing but left blocks is neither written nor counted in the export dialog
("Exporting the translation").

"Translate references too" (the button's menu, and the pill) runs a job over
the pages with left blocks (`translateState.pagesLeft`) with `references`
on, so the planner gets no `leave`. Their `null` blocks go back to pending,
as any re-run's do. A block stays in `left` until its translation lands, so
a job stopped halfway can be asked again, and a block that has text is never
left afterwards. "Try again" after such a job that lost a chunk runs with
`references` too (`translateState.referencesFailed`).

A job's status says `refsLeft`, the blocks it left, and `refsOnly`, that it
found nothing else to send: a page of references, or a page whose body is
translated already, through "Translate this page". A `refsOnly` job always
ends in "Nothing else to translate here: the references stay in the original."
with the action, since the page would otherwise not change at a click. Any
other job says "The references stay in the original." once a session, on the
PDF on screen, with the pill free, and before the export hint.

### Text in figures

A plot's tick labels, axis titles and legends stay original. Their words are
few, and the mask the overlay lays under a translation would cover the marks
they sit on. `pdf/pageFigures.js` reads the drawings from pdf.js's operator
list, so a PDF the server has not stored has them too, and no server route is
needed. Reading the text alone is not nearly as good: it finds 44% of the
labels, or 74% with table cells among them.

- **Boxes** (`paintedBoxes`): what the list paints, through the scale-1
  viewport, the frame blocks are in, so /Rotate, a crop box and /UserUnit need
  no conversion. The CTM and the clip's box are kept through save, restore and
  form XObjects. The page's annotations are left out.
- **Regions** (`pageRegions`, `figureRegions`): boxes over 60% of the page and
  rules across it go first, since they would tie everything together. Boxes
  that touch on a 6 pt grid make a cluster. A cluster is a region when it is a
  picture (images and shadings over 30% of its area) or holds 12 boxes, is at
  least 30 × 25 pt, a picture 30 × 20, and does not reach both sides of the
  page. A gradient bar under a panel's labels is a few boxes 24 pt tall (figs
  p2's panel d): over 142 papers and 2420 pages, with 1404 shadings on 125 of
  them, counting shadings as pictures took in its four labels and nothing else.
  A cluster of 3 to 11 boxes and no picture is `sparse`: a plot drawn in a
  few strokes, a frame, a curve or two and each axis's ticks as one path
  (gates p3's panel b, ten boxes, whose labels sit on its top edge).
- **Figures**: a sparse region is one only when tick numbers line its bottom
  and its left or right side, at least three on each, outside its box and
  within 18 pt of it: a table's numbers stand inside its rules. Over the same
  142 papers this took 15 blocks more, all of them a plot's labels and
  legends, and no prose. A region is dropped when it holds running text (three lines and
  12 words or more) or starts within 40 pt under a Table caption, so frames,
  tinted boxes and tables stay as they are, and table cells translate. Regions
  within 24 pt of each other merge, as panels, unless that takes in running
  text.
- **Blocks** (`markFigures`): a translatable block with 80% of its lines within
  18 pt of a figure takes `figure` and `translate: false`, unless it is a
  caption, a heading larger than the body, or prose of 8 words or more. CJK
  characters count as words. A block with no text there (tick numbers, "mF= -3
  -2 -1 0", "NA = 0.55") takes `inFigure`. Any other translatable block whose
  box overlaps a figure takes `overFigure`.

Marking happens in `translateState.segmentNow`, before the blocks are kept: the
first blocks made for a page stay, and entries are seeded by their identity.
With `translate` false the rest follows: the block is never pending, never
planned, drawn or exported, and takes no part in the page's body size or
measure. A page without text to translate builds no list for this. A list that
fails gives no regions, and the page plans as if it had no drawing.

A `figure` block is furniture before anything else, whatever its length, so a
paragraph goes on past a figure's label ("…will be" / "Time, T" is no join) and
reads as no display equation after a one-line block (`intro`). An `inFigure`
block leaves the flow as well, so a paragraph goes on past a figure's numbers,
but it keeps its place in the columns: the text in both halves of the page that
a region's cut asks for is often a plot's ticks, and without them a block lands
between the two parts of a paragraph. A figure makes no cut in the reading
order: no joined paragraph touched one of these blocks.

### The request

`translatePlan.requestFields` turns the viewer's context into the request:

- `page_id`: the page being translated, which names the paper (its brief,
  its kept translations).
- `head`: the document's opening text, only when there is no page to name
  the paper by. A page whose PDF the server has not stored sends its head
  with the brief's request alone.
- `wait_brief`: false for a text that must not wait while the paper's brief
  is being made (a job's first chunk, a selection), else true.
- `keep`: whether the server may keep the translations in the workspace
  (Settings → Language and Translation › Keep translations).
- `fresh`: sent only true, by a job on one paragraph (its menu's Translate
  again and Translate this paragraph): the server answers from neither
  memory nor the kept translations, and its answer replaces them ("Kept
  translations").
- `before`, `after` and `section`.

`chunkRequest` in `app/translateClient.js` sends them with `texts`, `lang`,
`model`, `effort`, `speed` and `stream: true`.

A reply comes back per unit. `translateReply.results` splits a joined unit's
translation at its `{br}` markers. When the model lost or doubled one, it
cuts by the parts' lengths at the nearest sentence or clause boundary, never
inside a number or after an abbreviation, and inserts no space between kana
or Han. Each piece's tokens are renumbered for the block it lands in
(`{v:pn.i.k}` for a formula the model moved across the join).

## Formulas

pdf.js extracts math as flattened text — a subscript inline, CMEX's Σ as "P",
fraction bars and radicals gone — and a model given that text returns
something plausible and wrong. So formulas in the texts to translate never
reach the model as text (the context around them, `before`, `after` and
`section`, goes as extracted, for the model to read):

- `pdf/pdfFonts.js` reads each run's real PDF font name. A text item's
  `fontName` is pdf.js's per-document id; the name sits on the FontFaceObject
  in `page.commonObjs`, there only once an operator list was built for a page
  using the font. A drawn page has it; for a page segmented ahead of the view
  the job builds the operator list once (`page.getOperatorList()`) and lets
  its decoded images go again when the page is far from the one being read.
  Names are cached per document.
- `pdf/pdfMath.js` decides, per run, what is math:
  - a math font (CMMI, CMSY, CMEX, MSAM/MSBM, Euler, txfonts/newtx, MathTime
    and its raw `RMT` fonts, Symbol, Euclid, Cambria Math, STIX and Latin
    Modern math, KaTeX's, …), or a typewriter/code font;
  - the publishers' typesetter fonts behind AIP's, APS's and Elsevier's
    PDFs: "AdvP" or "AdvPS" with six hex digits (Computer Modern's math in
    its own encoding; "AdvP" with four sets body text), the Pi fonts
    ("AdvPi1"), "AdvMacMth…", and the "AdvPS" Symbol, MathPi and AMS fonts;
  - mostly math characters (Greek, operators, arrows, math alphanumerics,
    private-use glyphs, and ð Þ þ ¼, the letters AIP's symbol fonts draw
    brackets, plus and equals with);
  - a sub- or superscript;
  - a one- or two-letter variable in an italic font (not an italic "a",
    "et al." or another short word: `ITALIC_WORDS`). An italic font is
    named so, or ends in "-It", "-I", ".I" or ".BI", or ".I+03" (AIP's
    italics for another code page).

  A run of lower-case prose is no formula whatever its font.
- What joins a formula besides its own runs (`lineParts`):
  - Digits, operators and brackets between two math runs, so "V(r) = C₆/r⁶"
    is one span.
  - Digits, brackets, primes and an accent glued to one. A math accent
    comes out as a modifier letter of its own (U+02C6–02DC: the hat of Ĵ, a
    tilde, a dot); alone, beside no formula, it is a name's accent (TeX's Š
    is a caron over an S).
  - The letter or two under an accent set over it, when the accent is the
    formula's or the letter is italic (â).
  - One Latin letter set tight between two formula runs, one of them math
    by its glyphs (the S of ¹S₀).
  - Up to four letters set tight between two scripts (the Ca of ⁴⁰Ca⁺), or
    between a formula's opening and closing delimiters (the "vac" of
    |vac⟩). The delimiters include AIP's ð Þ, and the "h", "i" and "j" an
    AdvP font sets ⟨, ⟩ and | as, without a Unicode map.
- Each formula span becomes a token, `{v1}`, `{v2}`… in the block's `src`, its
  box (glyph extent, scale-1 units) and baseline kept in `spans`. Citation
  markers (`[12]`, `[3–7]`) and URLs become tokens too, kept as literal text.
  A block whose words are all formula (a display equation) stays original,
  and a formula's operator names count as no words (`cos`, `sin`, `log`, `max`
  and so on, in `isTranslatable`): what is left of an equation's line once its
  symbols are tokens, `{v1} cos{v2} sin{v3} ;`, stays original when under four
  letters remain.
- An author line stays original (`isNameList` in `pdf/pdfTranslate.js`):
  names separated by commas, "and" or "&", most of them a first and a last
  name, with an affiliation mark (a token, maybe with a footnote sign)
  ending at least half of them and at least two. A name is one to four
  capitalised words with no digit; its only lower-case words are particles
  ("van der", "de la"). A mark inside a piece makes it no name, so no
  author line. A mark glued to the word after it opens an affiliation, and
  there must be fewer of those than marked names.
- The prompt says the tokens stand for what the reader sees as printed and
  must each be kept once. The server checks every reply segment
  (`translate_context.checked`: `{ v1 }`, `｛v1｝` and `{V1}` are read as
  `{v1}`); the segments that lost, doubled or invented a token are asked
  again with the others the reply got wrong ("The server"). A
  machine-translation service's reply gets the same check, without the
  re-ask.
- The overlay draws a formula token as `TransMath`: a crop of its page's
  whole-page bitmap (`pageRaster.basePixels`) at the span's box, sized in em
  of the paragraph's font so it shrinks with the text, and set on the text's
  baseline. Its scale comes from the bitmap's pixels and its page's width in pt
  (`data-pt-width` on the page's box, read for a formula of another page too).
  A tall formula keeps its drawn box with a smaller layout box
  (`transBlock.cropBox`), negative margins holding that inside the line, so it
  hangs into the leading, as it does on the page, and stretches no line. The
  lines around it are drawn over it ("Masks, ground and ink"); what of the
  original's own neighbouring lines its box takes in shows at its edges. A
  citation or URL token is drawn as its text.
  - The raster's `onBase` callback (each base paint or prime) redraws the
    crop after a zoom. The crop takes no zoom: the bitmap covers the page
    box, whose width at scale 1 the box carries as `data-base-w`, so it is
    right in the middle of a zoom too. A formula moved across a join into another page's
    paragraph is drawn from its own page, whose box announces each paint (a
    `pdfbasepaint` event).
  - Until the page holds pixels the extracted text stands in
    (`.pdfTransMath.bare`). The extracted text also lies under the crop,
    transparent, so a copy of the translation carries it.
  - The page colour treatments (flipped colours, the dark and tinted themes)
    apply to the crop as to the page, on paper of its own ("Masks, ground and
    ink").

## The paper's brief

`gamma/translate_context.py`. A paper's brief is made once per account,
workspace, paper, language and model, by a request of its own:
`POST /api/ai/translate/brief`, which a job sends as it starts. A
translation request never makes it.

- **The paper** (`paper_of`): with `page_id`, the page row in the request's
  workspace (`require_ws`, checked only when a page is named) — its title,
  authors, year and venue from `meta`, and its stored PDF (the file its
  document shows, `documents.model.file_hash`; `find_upload_file`, never a
  download). A duplicated page, which shows the original's stored file
  without owning it (its doc is a `copy`), is that stored file, as the
  viewer reads it: the same paper, kept translations and Forget as the page
  it was copied from. Without a stored file, the request's
  `head`. The key names the stored file (`doc:<id>`). A page without one is
  named by its id (`page:<id>`), so the chunks and the selection translator,
  which send no head, find the brief a brief request made from the head.
  Without a page, the key is a hash of the head. Only a paper with a stored
  file in the workspace (`doc_id`) has kept translations and a kept brief.
- **The material** (`Paper.material`): the PDF's outline (sections up to the
  second level, figure bookmarks left out) and its first 8,000 characters.
  Only the request that makes the brief reads the file.
- **The call**: one model call with `_BRIEF_PROMPT` returns a JSON object —
  the field, notation the paper defines (at most 10 symbols), 20–30
  recurring terms with their renderings, and names that stay as written.
  `render_brief` turns it into the system prompt's closing section (at most
  4,000 characters). It runs at the translation's effort and speed. Its
  output cap is 3,000 tokens (`_BRIEF_TOKENS`), plus 8,000 for thinking when
  the user picked an effort (`_brief_budget`), and its timeout 90 s. Its
  usage counts as `translate`.
- **The brief request** claims its turn before its first line goes out
  (`BriefTurn`):
  - The brief made already (in memory, else kept with the paper) answers
    `{"ready": true}` at once.
  - A failure remembered answers `{"ready": false, "error"}` with why.
  - The first request to ask is the **maker**: it takes one of the
    account's call slots (waiting up to 120 s) and makes the brief, with
    retries ("The server") that end by the time the chunks stop waiting for
    it (`BRIEF_WAIT`, 150 s, less the wait for the slot).
  - A later one is a **waiter**: it waits for the maker up to 150 s.
  - While the brief is being made the stream says `{"stage": "brief"}`
    first; its last line is `{"ready", "error"?}`. A client that goes away
    before that first line lets its claim go. A maker whose client goes away
    later carries on, its slot wait included: the brief is kept for the
    paper's next job, and other requests may be waiting for it.
- **A translation request** (`wait_brief`, the `wait_brief` function) takes
  the brief made already: memory first, then the kept one, which goes back
  into memory. Else, with `wait_brief: true` and a brief request making it
  right now, it waits for it, up to 150 s. Else it goes without one at once.
  - At most six requests of an account wait at once, the six AI calls it may
    have open; the rest go on without the brief.
  - A streamed request that will wait starts its response first and waits
    inside the stream, which keepalive lines hold open, with a `{"stage":
    "brief"}` line, then `{"stage": "translate"}`. It waits only for a brief
    already being made when it arrives. A plain request waits before it
    answers.
  - While any request of the job on screen is at that stage, the status
    pill says "Reading the paper first, so its terms stay the same
    throughout…" and the background-tasks row "reading the paper…"
    (`app/translateFollow.js`).
- **Memory and keeping**: briefs are kept in memory (LRU of 300). A brief
  made for a stored paper is kept with it in `data.db` when the brief
  request's `keep` is on and the account may write ("Kept translations").
- **Failures**: a failure or an unusable reply is remembered for 30 s, with
  why (`_BRIEF_FAILED_TTL`); a brief request meanwhile answers that reason
  and the chunks translate without a brief. A call Gamma refused itself
  (the account's calls all in use, or its allowance spent) says nothing
  about the paper and is not remembered.
- **Prompt caching**: the brief sits in the system prompt, which every chunk
  of the paper shares. Anthropic's adapter marks the system block (and, as for
  the chat, the user turn, a small write premium on each chunk); the OpenAI
  wires get `prompt_cache_key` = `translate_context.cache_key` (one per
  account, workspace, paper and language). A model whose minimum cacheable
  prefix is longer than the rules plus the brief caches nothing.

## The glossary

`gamma/translate_terms.py`. A workspace keeps its own renderings of terms,
per target language: "Rydberg blockade → 里德堡阻塞". A chat model follows
them in every paper of the workspace and over the paper's brief. A
translation service takes none.

- **Where it lives**: `translation_terms (lang, source, target, by, at)` in
  the workspace's pages.db (`db.PAGES_SCHEMA`, additive, no step), primary
  key `(lang, source)` with `source` compared without case, so a source
  term has one translation per language. It is what people write, so not
  in data.db with the kept translations: a backup zip and a replace carry
  it. A merge does not (it belongs to no page, and another workspace's
  terms would change how this one's papers translate), nor does a Gamma
  page export or a mirror, which carry pages. A write touches no page and
  goes in neither the op log nor the change log; the settings pane reads it
  when it opens.
- **Caps**: 500 terms per language (`MAX_TERMS`), 100 characters per side
  (`TERM_MAX_CHARS`), whitespace collapsed as it is kept.
- **Routes** (`routers/translate.py`), each write answering the language's
  terms as the read does:
  - `GET /api/translate/terms?lang=` → `{terms: [{source, target, by, at}],
    max, max_chars, can_edit}`, by source; any member (`by` is the
    username, "" once the account is gone).
  - `POST /api/translate/terms {lang, source, target}`: add; 409 when the
    language has the source, whatever its case, or is full.
  - `PUT /api/translate/terms {lang, was, source, target}`: edit; 404
    without `was`, 409 when `source` is another term.
  - `DELETE /api/translate/terms?lang=&source=`.
  - Writes need `require_ws(write=True)`: a viewer and a read-only token
    get 403.
  - The body is a `StorableBody`: half a character (the selection
    translator's prefill cut in two) is stored as U+FFFD, never a 500.
- **Matching** (`matched(text, rows)`, `Glossary.used`): case-insensitive,
  except a term written as an acronym (two or more capitals and no lower
  case: LED, SET, US), which matches in its case only, so "led", "set" and
  "us" are not it. A term that starts or ends with a Latin letter or digit
  matches whole words there ("dot" is not in "dotted"), a CJK one as a
  substring; one ending in a Latin letter also takes an English plural's
  "s" or "es" ("qubits", "LEDs"). Its separators are one class: any run of
  spaces, hyphens (`-`, U+2010, the non-breaking U+2011) or en dashes in a
  term matches any run of them in the text ("Rydberg blockade" finds
  "Rydberg-blockade", "spin-orbit coupling" finds "spin–orbit coupling"),
  and a straight apostrophe matches the curly one a PDF sets. A `{br}` is a
  space and a `{v1}` token is no word. Each term is regex-escaped. The text
  is read from its start: at each place the term that starts earliest is
  used, of those starting there the longest, and what it covers is no
  other's. So where one term holds another ("quantum dot", "dot") the
  longer is used; where two only overlap ("neutral atom", "atom array
  qubits" in "neutral atom array qubits") the one starting first is. Every
  form of a term is that term: its stamp names the row, not the text.
- **The prompt**: each call (a chunk, its re-ask, a selection) carries the
  terms its own texts use, one line above the segments after the context:
  `Terms: Rydberg blockade → 里德堡阻塞; lattice → 晶格`
  (`user_turn(..., terms=)`, built in `request_parts` from the texts the
  call sends). It is one line and masked as the context is, so a term
  cannot open a segment or a line of its own. The rules name the line and
  say it wins over the paper's brief. The system prompt does not change, so
  the paper's prompt cache still serves every chunk.
- **Keys**: a text's stamp (`Glossary.stamp`) is a short hash of the
  `source → target` pairs it uses, sorted and with the source's case
  folded, "" when it uses none. It enters the memory key
  (`translate_key(..., stamp)`) and the kept row's name
  (`translate_store.src_hash(text, stamp)`, through `Kept.src`). With ""
  both are exactly what they were before glossaries, so every row of a text
  that uses no term stays valid, and a term added or changed makes only the
  texts that use it miss. A row under an old stamp stays in the table: put
  the term back and it is found again; Forget, Clear and the purge take it
  as any other. The server works the stamp out from the workspace's terms,
  in `/api/ai/translate` and in the lookup route alike; the client sends
  nothing for it. Only a request that names a page has a workspace's terms.
  A service's keys carry no stamp.
- **A term changed**: the paragraphs on screen keep their words; the viewer
  drops nothing. Translate again (a paragraph's menu) asks for one with the
  term. Any request that sends a paragraph using it misses memory and the
  kept rows and asks the model with the term: a new job's, over pages not
  translated yet, and a later read's. A paper read again (a reload, another
  session) is seeded from the kept rows, the lookup misses the paragraphs
  that use the term, those blocks stay pending and the next job asks for
  them alone ("A paper opened again"). A page shown already in this session
  is done, so a job does not send its paragraphs again until then.
- **Errors**: a glossary that cannot be read translates as if it were
  empty, logged; it never fails a translation.

The client: Settings → Language and Translation › Glossary
([settings.md](settings.md)) lists the terms of the language Translate into
names, with Add term, Edit and delete for a writer of the workspace and the
rows alone for a reader. The selection translator's Add to glossary
("Selection translation") adds one. `app/translateClient.js` `addTerm`,
`editTerm`, `removeTerm`; a write the server refuses throws the reader's
words for it (`termRefusal`: the term there already, the language full, the
term edited gone), which both forms show.

## The server

`gamma/routers/translate.py` (`ai_translate`, `ai_translate_brief`, the kept
translations' routes, the services' credentials), with the paragraph cache
and the kept translations in `gamma/translate.py` over
`gamma/translate_store.py`, and the prompt and the wire in
`translate_context.py`:

- **The system prompt** (`system_prompt`): the user message
  (`translate_context.user_turn`) is plain text. Its context comes first, a
  line each when given: `Section: …` (up to 200 characters), `Before
  (context only, not to translate): …` (the last 1,500 characters),
  `After (…)` (the first 1,500) and `Terms: …`, the workspace's glossary
  terms the call's texts use ("The glossary"). Then a blank line and the texts as
  numbered segments, one per line: `<s1>…</s1>`, `<s2>…</s2>`. Text that
  reads as a segment's tag (a paper quoting markup, `<S2>`, `＜s1＞`) goes
  out between `‹ ›` (`_mask`): echoed inside its segment, it would end that
  segment early or open another. Its translation gets the source's brackets
  back (`unmask`). The reply is the same segments, the same numbers in the
  same order, each `<sN>its translation</sN>` on a line of its own, never
  merged or split. Rules: keep
  every `{br}` at the matching clause boundary; translate a segment that
  starts or ends mid-sentence as a part that joins its context; keep each
  token once; keep other math, numbers, units and citation markers; one
  rendering per term, after the brief; the Terms line's renderings, over
  the brief; references such as "Fig. 2" and "Eq.
  (3)" the target language's way; headings as headings. The target
  language's conventions follow for Chinese (full-width punctuation, a space
  between Chinese and Latin text), Japanese (である調) and Korean. The brief
  closes it.
- **Effort**: the user's pick in Settings → Language and Translation, else the model's
  lowest level (`_translate_effort`: `ai_catalog.reasoning_efforts`, then
  `translate_context.default_effort` — on the Anthropic wire the first level
  after `none`, which would leave the parameter out). A model whose levels are
  unknown, or that takes none, gets no effort parameter.
- **Speed**: the separate account preference `translateSpeed` in Settings →
  Language and Translation → Speed sends `speed` (`""`, `flex`, or `fast`).
  `_translate_speed` checks the resolved model's `ai_catalog.speed_tiers`;
  Default, an unsupported tier, or a failed lookup uses the provider's usual
  routing. The same tier applies to the paper's brief, streamed and plain
  translations, and the re-ask. Translation services ignore it. Changing
  speed keeps existing paragraph and brief caches.
- **Reading the reply** (`translate_context.segments`): each segment by its
  number. A tag is read tolerantly: spaced, a capital S, full-width
  brackets. A segment's text runs to its closing tag (a miswritten number
  still ends it), else to the next opening tag, else to the end. Text
  outside the segments and numbers outside 1..n are ignored. A number the
  reply leaves out, opens twice or leaves empty has no translation. Neither
  has the last segment of a reply cut short: left open at its end
  while the provider's stop reason says the output cap stopped it
  (`truncated_stop`, heard through `call_ai`'s and `sse_deltas`' `on_stop`),
  or, when the provider said nothing, while the reply closed the others.
  Asked for one text, a reply without any tag is the translation itself (out
  of a code fence, or a JSON string's value), unless the cap cut it. Each
  segment then gets its masked tags back and is checked for its tokens and
  breaks (`translations`, `checked`).
- **Asking again** (`finish`): every segment without a translation (left
  out, doubled, empty, or a broken token) is asked again in ONE call, those
  texts alone, numbered afresh, with the section but not the chunk's
  neighbours. `redone` counts them. One that still fails comes back
  verbatim instead of failing the request, named in the final object's
  `failed` (the request indices it fills, a duplicate's too), and is neither
  cached nor kept. A re-ask that fails as a call leaves all of its segments
  so.
- **One call slot per request**: the translation call and its re-ask run
  under one of the account's six call slots (`ai_client.call_slot`), taken
  once the brief is settled: a request waiting for the brief holds none.
  - A plain request takes it with no wait: all six in use is a 429 with
    `Retry-After`, as for any other call.
  - A streamed request is refused at once at the door when all six are in
    use (`check_call_slot`), a 429 with `Retry-After` that the viewer waits
    out ([ai.md](ai.md) "Calls open at once"). Inside its stream it waits for
    its slot up to `_TRANSLATE_SLOT_WAIT` (120 s): the account's other calls
    may have taken the last one meanwhile. A request whose client left
    during that wait (the response is a `WatchedStream`) lets the slot go
    without opening its call.
  - The brief request is refused at the door the same way and waits for its
    slot inside its stream. A job of N workers holds at most N slots, and
    its brief one more.
- **Retries**: the chunk's call, the re-ask and the brief are tried up to
  twice more (`_UPSTREAM_RETRIES`) when they fail before any of the reply
  came, under the same slot. Which failures are retried, and after how
  long, is `ai_client.open_ai`'s `retries` ([ai.md](ai.md) "Calls open at
  once"). The brief passes `within`, so its tries end by the time its
  chunks stop waiting for it ([below](#the-papers-brief)). A stream cut
  midway is not retried: the viewer asks for the chunk again.
- **Cache**: an in-memory LRU (~5k entries, lock-guarded because requests
  run in the threadpool) per (user, language, bare model name, the paper's
  key, whether the brief came along, source text, and the stamp of the
  glossary terms it uses, none for most texts: "The glossary"). It makes halts, retries
  and re-shows free until a restart; the kept translations ("Kept
  translations") outlive it.
  - The paper's key (`translate_context.brief_key`) names the paper and its
    generation, not the brief's text, so a translation made with the brief
    stays cached after the brief leaves its LRU, and one made without a
    brief stays apart.
  - Duplicate paragraphs within a request go upstream once. A paragraph that
    comes back verbatim is not cached, so a retry can improve it.
  - Caps: 200 texts / 60k chars per request.

## Kept translations

A paper whose PDF is stored in the workspace keeps its translations and
its brief in the workspace's `data.db`, so a paper read again shows its
translation without asking a model, after a restart too.
`gamma/translate_store.py`:

- **Tables** (in `db.DATA_SCHEMA`, which every `data.db` connection
  applies: derived data, like the PDF text index, so no migration):
  - `pdf_translations (doc_id, lang, model, src, text, brief, at)`, one row
    per paragraph, primary key `(doc_id, lang, model, src)`. `src` is the
    first 32 hex digits of the SHA-256 of the source text as sent (tokens
    and `{br}` included), so a page segmented again finds its paragraphs
    whatever their block indices, and of the stamp of the glossary terms
    the text uses when it uses any (`src_hash(text, stamp)`, "The
    glossary"). `brief`: made with the paper's brief.
  - `pdf_translation_briefs (doc_id, lang, model, brief, at)`: the rendered
    brief.
  - `model` is the bare model name, as the memory cache keys it, or
    `engine:<id>` for a machine-translation service.
- **Reading**: a request reads the kept rows of its texts in one query,
  before memory, and puts what it finds in memory; memory answers what they
  miss. The rows are the workspace's and memory is the account's, so another
  editor's Translate again, which replaced a row, reaches every account at
  once rather than when its LRU lets the old words go. A request that carries the brief takes only rows made with it.
  Kept translations are read whatever the request's `keep`.
- **Writing**: after the reply, the new translations go to memory and, when
  the request's `keep` is on and its account may write the workspace
  (`auth.can_write`), to the store with `brief` as the request had it. A
  viewer's translations stay in memory. A row made with the brief is never
  replaced by one made without, unless the request was `fresh` (a
  paragraph's Translate again, "A paragraph's menu"): it reads neither
  memory nor the kept rows, and what comes back replaces both
  (`TranslateBatch(fresh=True)`, `translate_store.put(replace=True)`). A
  `fresh` text that still fails comes back as its source and named in
  `failed`, as any other, and is neither cached nor kept, so the earlier
  translation stays on the server. A service's translations are kept too, as
  made with a brief (they take none). At most `MAX_ROWS` (20,000) rows per
  document, language and model: past it, new rows are skipped, with one
  warning per document per process.
- **Generations**: a document's generation is how often it was forgotten
  plus how often its workspace was cleared, both counted in memory and only
  growing. The memory keys of its translations and its brief carry it, so a
  forgotten or cleared document misses memory too and its next job
  translates afresh; the old entries age out of their LRUs. A request reads
  the generation once as it begins, and its kept writes are dropped when the
  generation moved on meanwhile. A forget and a write of the same document
  take turns (32 lock stripes by document; a clear takes them all, in
  order), so no write whose check passed lands after the delete.
- **Purge**: `pdf_index.purge_unused` calls `translate_store.purge` beside
  `pdf_meta.purge`, so a PDF that leaves the workspace takes its kept
  translations and brief along. A file a duplicated page still shows has
  not left: the sweep reads the file a copy shows (`file_hash`, as
  `paper_of` does) as well as the `doc_id` column, which leaves copies out.
- **Errors**: every function logs its own sqlite errors and carries on as
  if nothing were kept. Derived data never fails a translation.

The routes, workspace-scoped like the PDFs (any member reads what is kept,
a writer clears it):

- `POST /api/translations/lookup` `{page_id, lang, model, texts}` →
  `{translations: [text or null per text], model}`: the kept translations of
  the texts, as a translation request would send them, in the paper the page
  names, under that language and model; what the viewer seeds a paper from
  ("A paper opened again"). It reads the rows alone, never the account's
  memory, writes nothing and calls no model. `model` is read without an AI
  provider (`_kept_model`): `engine:<id>` as it is; a registry id
  (`server:<entry>:<name>`, or `<entry>:<name>` whose entry is one of the
  account's own or has an entry id's shape, 8 URL-safe characters, as one
  since removed does) by its name alone, with no AI runtime built, so a seed
  decrypts no key and refreshes no sign-in, and a member with no provider
  set up reads what the others kept; only a bare name (which may hold a
  colon of its own, "qwen2.5:7b") or none goes through the account's
  runtime (`ai_runtime`, which raises nothing): the model it lists, else the
  name as sent, else its default. The answer's `model` is the name the rows
  are under, `""` when nothing names one. A row is
  named by `Kept.src` from the text and the workspace's glossary on the
  server, as `/api/ai/translate` names it, so nothing about the key is the
  client's to send and a text using a term changed since misses. The caps of `/api/ai/translate` (200 texts, 60k
  characters). A page of another workspace, or one with no stored file,
  keeps nothing: every text is `null`.
- `GET /api/translations/stats` → `{documents, paragraphs, bytes}`, the
  bytes being the kept text's UTF-8 size, translations and briefs. Settings
  → Language and Translation › Service shows it under "Keep translations"
  (`translateKeep`, account, default on), with Clear
  ([settings.md](settings.md)). Clear sends the next route, then drops
  every document's entries in the viewer (`clearTranslations`,
  `translateState.forgetAll`; a document whose job runs keeps what it
  lands), so the next click translates afresh.
- `DELETE /api/translations` → `{removed}` (paragraph rows): the
  workspace's kept translations and briefs, and the server's memory of every
  document in it (`translate.clear`).
- `DELETE /api/translations/{doc_id}?page=<page id>` → `{removed}`: one
  document's, in every language and model, and its memory
  (`translate.forget`); with `page`, also the memory of a paper that has no
  stored file, which is keyed by its page (`page:<id>`) instead. The
  translate button's "Forget this paper's translations…" sends it once the
  reader confirms (`forgetTranslation`), then drops the document's entries in the viewer
  (`translateState.forget`), so the next click translates afresh. The menu
  offers it while no job runs, when some page holds translated text, the
  document on screen is the page's and the account may write the
  workspace. The page must also have a file the server keeps translations
  under (`fileHash` of its document: its upload, the original's upload on
  a duplicated page, or for a PDF opened from its link the copy the proxy
  saves under the link's hash). A PDF opened
  from its link with Save external PDFs off may have no copy: nothing of it is
  kept, and Forget resets the server's memory of it through `page`.

## Selection translation

The text-selection popup (`PlainTip` in `pdf/PdfViewer.jsx`: the highlight
colors, a line, then Copy (in the iPad app, whose system menu is off over
the PDF), Comment and the link) carries a 文A button, last, when
Settings → Language and Translation › "Translate a selection" is on
(`selTranslate`, account pref, default on), and the selection is not in a
translation, which is translated already ("Selecting translated text").
Its buttons act on the mouse's press, which keeps the PDF's selection, and
on a click no press led to: Enter or Space on a focused button, an
assistive technology's activation. The streaming text is a polite live
region that is `aria-busy` while it streams, so it is read once, whole.

- It sends the selection as ONE text through the page translator's request
  (`translateChunk`: same model or service, language, server cache,
  streamed partials), with the page and `wait_brief: false`: the paper's
  brief comes along when it is made already, never waited for. It sends
  `keep` as a chunk does, so its translation is kept with the paper's.
  Lines are rejoined by `selectionParagraphs` (`pdf/pdfTranslate.js`, the
  page blocks' hyphen/CJK rules), capped at 5000 characters. It sends no
  tokens: the selection is the text layer's.
- The result shows under the colors as a fold-out panel: a header with the
  language, spinner, Add to glossary and copy button, then selectable text.
  The button refolds it.
- **Add to glossary** (`GlossaryTermForm`): where a reader meets a term.
  Offered once the translation is in, for a writer of the workspace (App
  passes `onAddTerm` unless the session is read-only or a share view) and
  a selection of at most 100 characters. It opens a form under the text:
  the selection and its translation (its first 100 characters, counted as
  the server counts them, so no character is cut in two), both editable,
  then Add, which sends `POST /api/translate/terms` in the language the
  panel translated into. The form says "Added to the glossary" and folds; a
  refusal says why in its place, in the interface's language ("The
  glossary has “switched” already", `termRefusal`). The fields take
  the focus and the keys; the PDF's selection is the popup's snapshot, so
  the colours still highlight it. Nothing on screen changes ("The
  glossary").
- "Translate on select" (`selTranslateAuto`, default off) starts it as soon
  as the popup opens.
- The popup is keyed by the selection, so a new selection starts over and
  aborts the previous request. A click or selection inside the popup keeps
  it open and where it is (the viewer's selection sync ignores a mouse
  release in `.plainTip` and a selection anchored there).
- `TipFrame` opens the popup by the point where the mouse was released
  (under the selection for a touch or keyboard selection, or a release far
  from it; `pdf/selectionTip.js`) and flips it above when it would run past
  the window's bottom, in the frame a streaming translation grows it
  ([ui-design.md](ui-design.md#menus-and-popups-open-in-place)). Like the popup itself, it needs edit rights on the page.

## Exporting the translation

Export → Annotated PDF writes the open page as the viewer shows it: while
its translated view is on, the translation takes the place of the original
text in the file, under the highlights, handwriting, text boxes and notes
the export puts on as always. With the translation hidden (the slashed 文A)
the original goes out. There is no switch for it. The dialog's summary says
that the translation replaces the original text, in which language and on
how many pages, and that showing the original with the translate button
first exports the original (`transfers/transferFormats.js` `exportSummary`,
one sentence after the plain or annotated base). The
session's first translation to finish says so on the pill, with **Export…**.

- **What goes** is what the overlay draws (`pdf/translateExport.js`
  `exportLayout`, over `translateState.snapshot`): the page entries under
  the current language and model, and of those their final texts. A paper
  shown from its kept translations ("A paper opened again") has entries only
  for the pages seeded near where the reader went, so every page is seeded
  (`translateCtl.seedAll`, through the lookup, no model call) as the dialog
  opens on an annotated PDF (App's `exportSeeding`), and the file holds
  every page the workspace keeps a translation of, not only those seen.
  Until that seed ends the summary names no number ("…on the pages it
  covers, which are being counted") and the dialog's Export is busy; then
  it counts the pages the file will hold. The export seeds again as it
  starts, which reads only what is still unseeded. While an export starts
  its button stays busy and a second press starts nothing; a dialog closed
  meanwhile starts no job, or the one that did goes on in Background tasks
  with its file offered (`useAppJobs.runExport`, `closeExport`). On papers
  of 40, 53 and 92 pages, seeding every page took 0.7, 1.0 and 1.5 s in
  Node (17 to 21 ms a page of segmenting and planning) and 4, 4 and 7
  lookups. No seed runs while a job works on the document: it exports what
  has landed. A block
  not landed yet, back as its source, or of a reference list left original
  (`left`) stays original, and a page with only such blocks is neither written
  nor counted in the dialog's summary, so the file keeps its bibliography until
  "Translate references too" has run. A `""` block loses its lines and gets
  nothing. Streamed partial text never goes, so a job still running exports
  what has landed. A figure's text the reader asked for ("Translate this
  paragraph") goes as it shows. A block the reader shows in the original
  for now (the entry's `original`, "Show original here") still goes
  translated: that is a look at the page, not a choice of what the file
  holds, which the translation's show/hide makes. A text's tokens are read by the
  overlay's own function (`translateReply.textParts`): a formula becomes its
  page, box and baseline, a citation marker or URL its text.
- **The request** is `translation: {doc_id, lang, pages: [{page, blocks:
  [{lines, size, parts, box?, foot?, centred?, justified?, bold?, italic?,
  indent?, face?}]}]}` beside the switches in `POST /api/jobs/export`
  ([api.md](api.md)), in the viewer's units. `box` is a one-line block's room
  and `foot` where its text stops, the same as on screen; a centred block sends
  no `indent`. `face` is the name of the font most of the block is in, which
  the server finds in the file by it; pdf.js's id means nothing there. Every field
  with a `?` may be absent: an older client calls a newer server. The leading comes from
  `lines`, and ground and ink from the file itself, so no colour is sent. App sends it (`exportTranslation`,
  through `tasks/useAppJobs.js` `runExport`) for a page's annotated PDF (a
  folder's carries `translations`, "A folder's export" below), and only
  when the PDF the viewer translated is the file the job reads
  (`translateExport.exportFile`): the stored file the page's document
  names (`fileHash` of its document, [documents.md](documents.md)), as
  its upload or as the proxy reading the page's own link, whose saved
  copy is named by the page's `doc.hash`. A duplicated page's is the
  original's upload, so its annotated PDF carries the translation as the
  original's does. A page shown from an address alone names no stored
  file and writes none; a link page whose copy was not kept fails its
  annotated PDF, translated or not.
  Such an export always goes through the job, even with every switch off. A
  share view translates nothing and sends none.
- **Retry**: the job's params name the language (`translation`) but cannot
  hold the layout, so the server keeps the translation it was sent with the
  job (`translation.json.gz`, `jobs.start(kept=...)`) for as long as the job
  is listed, a failed, stopped or interrupted one too, until it is removed
  from Background tasks or swept a day after it ended. Start again sends
  `translation_of`, the job's id, in place of `translation`
  (`taskKinds.js`), and the new job writes the same layout and keeps it in
  turn; the server takes it only from the account's own export of the same
  page (or folder: `translations.json.gz`) in the same workspace, and answers
  404 once it is gone. Kept layouts
  count against the account's 10 GB of finished files ([tasks.md](tasks.md)),
  so a translated export, even one that fails at once, gets 507 past it. It writes
  what the viewer showed when the first export started, so a PDF replaced
  since fails it again. In the dialog, a failed or stopped one offers
  "Export again…" instead while its page is open (`taskKinds.exportAgain`),
  which goes back to the setup and reads the viewer afresh, and Start again
  otherwise.

`gamma/pdf_translated.py` writes it into the page's stored PDF before the
annotations go on (`routers/export.py` `_translated`). When the page's PDF
is another file than the one translated (replaced meanwhile), or the file
cannot be written, the page is left out with that reason, like any page
whose annotated PDF fails: an export of the page alone fails with it, and
with the pages inside it the rest go out and the result lists the page
under `skipped`.

1. **The original lines go.** The middle of each line (`BAND`: 0.3 of its
   height left at the top and at the bottom) is redacted, text only, a
   hundred lines at a time (`REDACT_BATCH`): the glyphs leave the content
   stream, pictures and drawings stay. MuPDF takes every glyph whose box
   (the font's full height, not its outline) meets the area, so the middle
   takes the line's own glyphs, and a tall glyph of the next line that
   reaches into its box stays in the file. Then the lines are masked as the
   overlay masks them (`_masks`: each line and the leading below it, down to
   its own bottom when the next line's glyph box runs into it), which also
   covers a line that is drawn rather than written, a scanned page under its
   text layer. A mask takes the block's ground, white on plain paper ("Ground
   and ink", below). The annotations the redactions dropped
   (MuPDF takes the links and FreeText boxes they meet) go back into the
   page's `/Annots` as they were stored: a link keeps working over the
   translation, as in the viewer, and another app's text box stays. So do the
   page's `/DefaultGray`, `/DefaultRGB` and `/DefaultCMYK`: the rewritten
   content names them nowhere, so MuPDF leaves them out of its resources, and
   every colour of the page shifts. On tint p1 the abstract's panel went from
   236 to (230, 231, 232).
2. **The translation is laid out** in the block's box (its lines' union,
   or a one-line block's `box`, with the overlay's 0.75 units of room, its
   bottom a pad above the `foot` when it has one) by MuPDF's HTML engine
   (`Story`), set as the overlay sets it: at the block's size, in its ink,
   with its leading (`_leading`: the rule of `transBlock.blockLeading`,
   "Leading and shape"), its first line's indent (in em,
   so it shrinks with the text, where the overlay's stays the page's), bold,
   italic and centred as the block is (no italic for CJK), justified where the
   original is and the box is 18 em wide (`_setting`, `JUSTIFY_EM`, the
   overlay's rule), shrunk until it
   fits, down to 5%, the overlay's `MIN_FIT` too: past that it runs out of its
   box whole, as on screen. Every page's blocks go into one temporary document,
   subset once, whose pages are shown on theirs, so a font is embedded once for
   the whole file. A formula holds its place in the line as an empty picture of
   its width and its height above the baseline; in a justified line too, MuPDF
   reports the place where it draws it.
   **The face** is the paper's own where it can be (`_paper_faces`): the
   block's `face` is looked up by name among its page's fonts (subset tag and
   a composite font's `-Identity-H` left off both sides), embedded and no Type
   3, and the font is used when it has a glyph with an outline for every letter
   the block sets, blanks and format characters aside (`_PaperFont.inked`:
   MuPDF's own subsetter keeps a dropped letter's place and empties it, which
   `has_glyph` still counts). A composite font's program often maps no
   character to a glyph: LuaLaTeX and XeLaTeX embed every OpenType font as a
   bare CID-keyed CFF, and a subset TrueType program can come without its
   cmap. Such a program is given a Unicode cmap from the file's ToUnicode
   first, as pdf.js does for the overlay (`gamma/pdf_font_cmap.py`): a
   CID-keyed CFF is written again with each glyph at its CID (MuPDF reads a
   CIDFontType0's codes as CIDs, so the glyph index the text is written with
   must be one) and wrapped in an OpenType file, a TrueType program has its
   cmap replaced, its glyphs found through `/CIDToGIDMap`. A CID the
   ToUnicode names as several characters (a ligature) stays out of it. The
   program goes into the stories' archive as
   `pf<xref>.<ext>` and each block's CSS names it in an `@font-face`; MuPDF
   embeds it once however many blocks load it, and without its weight or slant
   set over it, which it has itself. The blanks it lacks (TeX's fonts have
   no space) are set in Charis (`_blanks`, `span.gs`), which the file has for
   the other blocks anyway, where MuPDF's own fallback would bring in Noto
   Serif, 60 KB, for the spaces alone. A Type 1 program is embedded as it was
   extracted: `subset_fonts` leaves it, and it was a subset already (21 KB for
   the fixture's CMR10). A block whose letters the font lacks, which on a TeX
   paper translated out of English is most, keeps the serif whole.
3. **Stamped.** The temporary page is shown over the page, turned with it,
   and each formula goes on the baseline at its placeholder's place,
   stretched over the formula's box, as itself where it can be: what its
   page draws in its box and nothing else, the page's own glyphs in the
   page's own fonts, vector at any zoom (`_vectors`). MuPDF's content filter
   (its sanitize filter, with a culler: `_culler`, `_filtered`) asks of
   every glyph, path and picture the page draws whether to keep it; a glyph
   is kept when the centre of its box lies in the formula's box (in the
   box it shares most with, when two formulas' boxes overlap, so no glyph
   is drawn twice), a painted path when it meets the box, every clip
   always, and a form XObject the page draws is filtered with it as a copy
   of its own, since MuPDF keeps every form a content draws (a figure's
   would come whole). The page's content is filtered once down to what
   meets any of its formulas' boxes, then that, much smaller, once a
   formula (`_isolate`), before any page changed. The formula's content is
   clipped to its box, starts from a page's usual state (`_USUAL_STATE`:
   black, not the translation's ink the sheet set before it), and has its
   resources renamed for their objects (`_renamed`, which reads the content
   as tokens, so a name in a string stays). The sheet marks the
   placeholder's place (`/Gq<n> Do`, `_formula_texts`) and, once shown on
   the page, that mark becomes the formula's content and its resources join
   the sheet's (`_inline`), in the sheet's own stream rather than a form of
   its own: pdf.js starts a form's text afresh, so a space before the
   formula was lost ("interactionVij"). So its glyphs are its text, read in
   their place between the words around it, by MuPDF, pypdf and pdf.js as
   by the hidden text below. A glyph is drawn where the page drew it, or
   the formula is a picture: MuPDF 1.28's filter drops a `Tm` that repeats
   the line's after text was shown (1.27's keeps it), so a glyph drawn over
   the one before it (an accent, the bar of an ħ, where a producer places
   every run by `Tm` as LuaTeX does) would move, and every formula's glyphs
   are checked against the page's (`_drawn_alike`).
   **A picture** stands in for a formula whose box holds a picture of its
   page (a scan under its text layer, an equation set as an image), whose
   box holds nothing drawn, whose content passes `FORMULA_BYTES` (32 KB,
   a figure's paths running through it), whose glyphs moved, whose content
   names a resource it cannot rename or holds an inline image, and every
   formula of a page MuPDF cannot filter: its box on the original page
   (`FORMULA_DPI`, 600; coarser past `FORMULA_PIXELS`) on a clear ground,
   drawn before any page changed.
   Under each picture lies the formula's text, written but not painted
   (render mode 3), as the overlay keeps it under its crop: copying a
   sentence copies its formulas with it, in their place. The text goes where
   the placeholder was drawn (`_formula_texts`, in place of its drawing on
   the temporary page, which also takes the placeholders out), so it reads
   between the words around it, stretched over the formula's box: its em the
   formula's height, its run the formula's width. Each letter goes in the
   first face that has it (`_formula_fonts`): Noto Sans Math, which MuPDF
   builds in (SIL Open Font License), for ASCII, Greek, operators, arrows
   and math alphanumerics, then Charis SIL, the translation's own, for the
   accented Latin and the Cyrillic it lacks (µ, ·, é), then MuPDF's
   fallback. So a file with a pictured formula takes one font more, subset:
   on a stand-in layout of the fixture paper (its own words as the
   translation, its 120 math spans as formulas) the file came to 226 KB
   against 169 KB without the text, in the same 0.45 s. MuPDF writes a
   character past U+FFFF into a ToUnicode
   map as its bare number, which readers garble (math italic 𝑥 came back as
   U+1D46), so every such entry of the temporary document becomes its UTF-16
   surrogate pair (`_surrogates`), the translation's own too. A formula
   whose box is off its page, or that a page join moved from a page turned
   otherwise, is written as its text, visibly.

The fonts the redaction left unused leave the file as it is written
(`garbage=1`; merging duplicates as well would only add time under the
lock, since the writer makes none); a font only the formulas use stays for
them. On a stand-in layout of the fixture paper (its own words as the
translation, MuPDF's 114 math spans as the formulas) the write takes 0.2 s
and the file 165 KB, against 0.43 s and 237 KB with every formula a
picture; on 40 pages of a LuaTeX textbook (3424 formulas, 112 of them
pictures for an overstruck glyph), 8.9 s and 3.2 MB against 12.7 s and
4.1 MB ([research](../research/translated-pdf-export.md#formulas-as-themselves)).

**Ground and ink.** A page exported may never have been shown, so the server
reads them itself and the request carries none. `_Sample` draws the page once,
before anything is changed, at 2 px per pt in its display frame (4 MP at most, a
bigger page coarser), without its annotations, which are no ground, and with
MuPDF's smoothing off, so a stem keeps its colour at that resolution. That level
is global, so it is put back at once, under the lock every other call takes.
`_paint` decides each block by the rules and numbers of `pdf/transPaint.js`
("Masks, ground and ink"), on about 2000 of its pixels, its lines' and its
ring's apart. On hard.pdf's banner, white on a green band inside its line's
box, the file has the green and the white words. It works on counts of
distinct colours, so no per-pixel loop runs in Python, and numpy, which
the server does not have, is not needed.

On a PDF of 293 pages with 2208 blocks the sample took 9 ms a page, 2.0 s to
draw and 0.6 s to decide, of a write of about 35 s; on 148 pages of a 25 MB PDF,
12.5 ms a page, 1.8 s of about 11 s, the worst page 165 ms. On
tint p1 the panel comes out one colour in MuPDF (236) and within a level in
pdf.js (236 against 236, 237).

**What the file does not take from the overlay.**

- **The faces.** Where the paper's own font cannot set a block, the export
  keeps MuPDF's serif fonts, embedded and subset, whatever the machine: Charis
  SIL, Noto Serif for a letter it lacks, and Droid Sans Fallback for all of
  Chinese, Japanese and Korean. The overlay's `--font-page` (Times New Roman
  and a serif per CJK language) is the reader's own machine's and cannot be
  embedded. So a CJK file is in a sans, and bold CJK is MuPDF's synthetic
  bold. Charis also sets wider than Times. The paper's own font is the same
  program on both sides, but the overlay reads pdf.js's rebuilt copy and the
  export MuPDF's, so on a font whose characters pdf.js cannot name the two can
  decide differently.
- **The page's cap.** Each block shrinks by itself, so the body paragraphs of a
  file differ in size where the screen's share a cap.
- **Hyphenation.** `Story` hyphenates nothing, so a justified line in the file
  can have wider gaps than the overlay's, which hyphenates where the browser
  has the language's dictionary.
- **The treatment.** The file holds the page's own colours; flipped colours and
  the themes are the viewer's.

MuPDF's behaviour the writer depends on, each held by a test in
`tests/test_translated_pdf.py`:

- A picture in a line is reported 0.8 of its height above where it is drawn
  (`_IMAGE_DROP`), with its bottom on the baseline, and in a justified line
  at the place the widened gaps move it to.
- The writer draws the empty placeholder as an opaque pixel stretched over
  its place, so the temporary pages' pictures are taken out before use (each
  drawing replaced by its formula's hidden text, or its mark).
- MuPDF 1.28's PDF device writes ignored text in render mode 3 and 1.27's
  paints it, so the hidden text sets the mode itself; a test reads it back
  under the formula's picture, painted nowhere, at every turn of the page.
- A vector crop of a formula (the page shown through a clip) carries the
  whole page's text, invisible but read by pdf.js and any reader that
  ignores clips. That is why a formula is filtered down to its box.
- The content filter takes out what a formula's box does not hold, a form's
  content too, and its glyphs read in their place, at every turn of the page;
  a page drawn inside a form gives up only what lies in the box.
- MuPDF 1.28's filter drops a `Tm` that repeats the line's: an overstruck
  glyph is drawn where the page drew it, or the formula is a picture.

Why it is built this way, and the measurements:
[research/translated-pdf-export.md](../research/translated-pdf-export.md).

### A folder's export

Export → Annotated PDF of a folder writes each of its papers as it would
open: translated where this browser left it showing its translation ("A
paper opened again"), the original otherwise. No viewer shows those papers,
so the browser reads them itself (`pdf/folderTranslation.js`), with the
viewer's own code and no viewer:

- **Which papers** (`folderPapers`): the pages filed in the folder or below
  it, a page inside another filed where its top is (the server's
  `pages_in_folder` rule), that show a stored PDF (`fileHash` of the
  document, `doc_id` as the job names it: a duplicated page's is the
  original's; a page shown from an address alone has no file to write
  into), and of those the ones whose file the memory
  holds under a key naming the settings' language and model
  (`translateRecall.openKey`, App's `folderKeyOf`). The paper open in the viewer goes as the viewer shows it
  (`exportTranslation`, so Show original there exports its original).
- **Each paper** (`paperLayout`): its PDF opened with pdf.js and no viewer
  (`PdfViewer.openPdfDocument`: the viewer's byte caches, else one GET, the
  shared worker), every page seeded from the kept translations through the
  lookup under that key, with no model call (`translateState.seedDetached`:
  `seedPages`' work on a state of its own, which the store does not keep and
  no one is told of, so the documents the reader has open stay in it), and
  laid out by `exportLayout`, as a page's export lays it out. The document
  is destroyed after. One paper at a time (`folderTranslations`); a paper
  whose PDF or lookup fails, or where nothing was found, goes in the
  original.
- **The dialog** (`pdf/useFolderTranslations.js`) reads them as it opens on
  the folder's annotated PDF: its summary says the translation replaces the
  original text of the papers this browser left showing it, "which are being
  counted", with its Export busy, then how many papers
  (`transferFormats.exportSummary`). Export takes that read; closing the
  dialog, or another format, stops it.
- **The request** carries `translations: [{page_id, doc_id, lang, pages}]`,
  each as `translation` is, in place of `translation` (400 with a page, 400
  for both). The server writes each into its page's PDF as the folder's
  export reaches it (`_AnnotatedPdfBuilder.add_page`, by page id); one for a
  page the folder no longer holds is passed by, and a paper whose PDF was
  replaced is left out with its reason like any page that fails, the rest
  going out. The caps above hold for all of them together (a folder is one
  book), so the browser leaves out, in the original, a paper that would take
  the whole past them (`TRANSLATION_BOUNDS`) rather than have the export
  refused. The job's params name the language; Start again sends
  `translation_of` as for a page, and the server keeps the list as
  `translations.json.gz`.

What the workspace does not keep stays out: a translation made by a viewer
or with Keep translations off is never looked up, so such a paper goes in
the original unless it is the one open.

## Machine-translation services

"Translate with" also offers the services in `gamma/translate_engines.py`.
The viewer then sends `model: "engine:<id>"`, and `/api/ai/translate` hands
the misses to `translate_engines.translate` instead of a chat model.

- **Microsoft (free)** needs no setup. It is the endpoint Edge's own page
  translation calls: `POST
  edge.microsoft.com/translate/translatetext?to=<code>&isEnterpriseClient=false`
  with a JSON array of strings and no key or token. The reply has
  Translator v3's shape (`[{detectedLanguage, translations: [{text, to}]}]`);
  the source language is detected per text.
- The endpoint is unofficial and undocumented, so it can change or throttle
  without notice; Google and Youdao are the fallback. The older
  `/translate/auth` token flow answers 404 since July 2026.
- Microsoft refuses requests past about 50k characters (measured), so
  batches stay at 100 texts / 20k characters.
- Its consecutive failures are counted in memory. From `FREE_ALERT_AFTER`
  (3) on, the server log gets one warning per streak and each account that
  met them gets the `free-translate` notice ([settings.md](settings.md)
  "Notices"); the Settings row shows the error. One success ends the streak.
- **Google Cloud Translation**: v2 basic, the API key sent as
  `X-Goog-Api-Key`, `format: "text"`.
- **Youdao**: the `openapi.youdao.com/v2/api` batch, with a v3 SHA-256
  signature over the concatenated queries. A query listed in `errorIndex`
  comes back verbatim and uncached.
- Each service maps the target codes to its own (Microsoft `zh-Hans`,
  Youdao `zh-CHS`) and splits a request by its batch limits.
- The service path needs no AI provider. It has no effort (`""` in the
  final object), no brief (the
  brief request answers `{"ready": false}` at once), no streamed partials
  (the stream is just the final line), no token usage and nothing to ask
  again, since the APIs answer aligned lists. A service translates sentence
  by sentence and takes no context.
- Cache, kept translations, validation and dedup are the LLM path's, keyed
  on `engine:<id>`; its final object says `brief: true`. Nothing tells a
  service to keep the `{v1}` tokens or `{br}` markers; a reply that broke a
  token comes back verbatim (the original shows), and a lost marker is cut
  by length. `failed` stays empty: a service answers the same text the same
  way, so the viewer asking again would get the same.

Credentials are per account under the reserved `translate-engines` pref.
Like `ai-settings`, `/api/prefs` refuses it and the only read path is the
masked `GET /api/translate/engines`; guests can't store any.
`PUT` / `DELETE /api/translate/engines/{id}` set or drop a service's
credentials, and `POST /api/translate/engines/{id}/test` translates one
sentence for the row's Test button (in-body result).

## The bench

`frontend/tests/bench/translateBench.mjs` measures what the viewer sends and,
with a provider, what comes back, for fixed papers, so a change to the
segmenter, the planner or the prompt can be measured before and after. It
segments and plans pages with the viewer's own modules under pdf.js in Node.

```bash
cd frontend
node tests/bench/translateBench.mjs                       # the fixture paper (needs pdflatex)
node tests/bench/translateBench.mjs paper.pdf --pages 1-4 --label before
node tests/bench/translateBench.mjs --compare before.json after.json
GAMMA_BENCH_PROTOCOL=anthropic GAMMA_BENCH_API_KEY=… GAMMA_BENCH_MODEL=… \
  node tests/bench/translateBench.mjs paper.pdf --live --stream --judge --lang zh-CN
node tests/bench/translateBench.mjs --record tests/fixtures/translatePaper.json
```

It writes `<label>.json` and `<label>.html` (each request's texts and
translations side by side) to the OS temp dir (`--out` to choose).

- **Dry numbers**: blocks, blocks kept original, `figureBlocks` (those that
  are text in a figure), `refsLeft` (the blocks of reference lists left out,
  as a job leaves them), units and requests sent,
  characters; `endsMid` (body texts sent that end mid-sentence: a paragraph a
  break cut, or one that goes on past a display equation, which the context
  covers), `startsMid` (texts sent that start in lower case), `joined` (units
  with `{br}`), `tokens`, `mathChars` (math characters still sent as text
  outside tokens), `glued` (a single letter or modifier letter glued to a
  token: a symbol font's glyph extracted as a Latin letter, or a piece of
  the formula left out of its span, which `mathChars` cannot see), and
  `contextMathChars` (math characters in the context around the requests,
  which goes as extracted).
- **Live numbers** (`--live`, through an isolated server from the e2e harness,
  the requests the viewer makes). Each paper goes as the viewer's job sends
  it (`sendChunks`): the brief asked for at its start and not awaited, the
  first chunk with `wait_brief: false`, the others held until the brief
  request is under way and then waiting for the brief on the server, and
  the end-of-queue re-ask. The numbers:
  - failed requests, paragraphs back verbatim, paragraphs `redone` (the
    server's count, the re-ask's included), paragraphs `untranslated` (the
    server's `failed`: still broken after asking again), breaks lost;
  - `askedAgain` (chunks the end-of-queue re-ask sent again), `briefless`
    (chunks still without the brief after it), `briefFailed`
    (papers whose brief request did not end ready), `briefMs` (the median
    paper's wait for its brief);
  - the median and 90th-percentile request time, `firstMs` (the median
    paper's wait for its first text: its first partial line with
    `--stream`, which reads each chunk's NDJSON as the viewer does, else its
    first reply), the wall time, the effort the server asked for, and the
    token usage.

  The server keeps every token (a broken paragraph comes back as its
  source), so a lost token shows in `redone` and `unchanged`, never in the
  texts. The isolated server runs with the model catalog on, as a real one
  does, so the default effort is the model's lowest level.
- **`--judge`** asks the same model once which renderings each of the paper's
  frequent terms got across the translations: `meanRenderings`, 1.0 when every
  term was rendered one way; `null` with an `error` when there were no
  frequent terms or the judge's call failed (`--compare` shows it as —).
- **The fixture paper** (`frontend/tests/fixtures/translate/paper.tex`) is
  original text in a two-column revtex layout with the cases the view has to
  get right; `--record` writes its pdf.js runs to
  `frontend/tests/fixtures/translatePaper.json`, which the unit tests read.
