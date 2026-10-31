# PDF translation

The viewer's translated view: the button and its states, the job that
translates a document and outlives the viewer, how a page is planned into
requests, what a request carries (the paper's brief, the context around its
texts, formulas as tokens), the server and the translations it keeps, the
engines and their limits, and the bench that measures it. Provider
configuration and the chat are in [ai.md](ai.md); the engine preferences
are in [settings.md](settings.md). Why it is built this way, and the
numbers before and after:
[research/translation-quality.md](../research/translation-quality.md) and
[research/translation-reliability.md](../research/translation-reliability.md).

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
- Hidden = slashed icon; holding Alt (the `pdf.peekOriginal` held key, [hotkeys.md](hotkeys.md#held-keys)) peeks. A click during a job halts it.
- Right-click (long-press on touch) opens the option menu: Translate this
  page / Translate whole document / Show original·translation (Stop
  translating while running), and Forget this translation ("Kept
  translations").
- The button is one of the strip's tools (`translate` in the `viewerTools`
  preference, [settings.md](settings.md#bars-order-and-modes)): its place
  is the user's order, and hidden (Settings → Translation › Translation
  button off, or Appearance › Viewer tools) it is a row of the strip's ⋯
  menu that does what a click does. While it translates or a translation
  is shown it stays on the strip, whatever its mode.

## The queue

A whole-document job queues pages nearest the current page first (forward
before backward at equal distance), so the page being read paints
immediately. The job is `pdf/translateJob.js` (`createTranslateJob`), a
module without React that runs in Node under the tests. Producer/consumer
style:

- The producer segments each queued page and its neighbours in the job,
  plans the page (`pdf/translatePlan.js`, next section) and appends its
  chunks to one flat list.
- N workers (Settings → Translation → parallel requests,
  1–`TRANSLATE_PARALLEL_MAX` = 4 in `app/prefDefs.js`) stream through the
  list across page boundaries: the first request is in flight while later
  pages are still segmenting, chunks paint as they land, and char-weighted
  progress shows under the button and as a background-tasks row.
- **One limit for every job.** Jobs of several documents run side by side,
  and their chunk requests together go through one limit of
  `TRANSLATE_PARALLEL_MAX` in flight (`createLimit`, held by
  `pdf/translateState.js`), below the six AI calls an account may have open
  at once. A request waits for a place in the order they came, a halt takes
  it out of the line, and its time counts from when it goes. Without it,
  two jobs would ask for more calls than the account has, and a chunk the
  server turned away (a 429 with Retry-After) nearly always lost the freed
  call to a worker sending its next chunk at once, ran out of patience and
  left its paragraphs in the original. The brief's request, one per job,
  goes outside the limit.
- Halting aborts the in-flight requests (each job carries an
  AbortController) and keeps finished chunks.

**The paper's brief at the job's start.** As it starts, the job asks for
the paper's brief (`POST /api/ai/translate/brief`, "The paper's brief")
when the paper can have one: the page names it, or the document's opening
text does. It never waits for the answer. The opening text (`head`, up to
12,000 characters of the first three pages) is read only for a PDF the
server has not stored, and only the brief's request carries it, unless
there is no page: then every request does.

- **The first chunk goes at once**, with `wait_brief: false`: it carries the
  brief only if it is made already, so text lands within one request's
  time.
- **The gate.** Every later chunk waits until the brief's request is under
  way: its first line came (the server has claimed the brief, so the
  chunk finds it being made and waits for it there), or the request ended
  (made, found, or failed after its last try), or the job halted. A try
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
(`translatePlan.requestChunk`):

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
When a chunk's request failed, it says the connection to the AI provider
failed; otherwise that the paragraphs could not be translated. The button
on such a page asks for them too. A stopped job says "Translation failed:
…".

Once a session, a hint follows a job's end: when "Translate with" follows
the chat model and the session's first job that reached the model had a
median request over 20 s (`SLOW_MS`), the status line says a faster model
can be picked, with an action that opens Settings → Language and
Translation. It waits for a job's end with nothing else to say. Replies
the server had cached or kept are not counted in the median.

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
  stores it, and the page's file (what "Forget this translation" names).
  While the next PDF loads, the viewer still shows the last one, but the
  host has moved on to the next page; a job (or a selection) on the
  document still shown then names no page (`scopeOf`) and is read from its
  opening text, so the next page's id never carries the wrong paper's
  text, brief or kept translations, and Forget is not offered.
- **Fonts and far pages.** Font names are cached per pdf.js document. A
  page segmented ahead of the view lets its decoded images go again
  (`page.cleanup()`) unless it is within two pages of the one being read,
  in the document on screen.
- **At most `MAX_DOCS` (8) documents are kept**, the least recently used
  going first, never one whose job runs.
- **App's side** is `app/translateClient.js` (the chunk and brief requests,
  forgetting a document, ending the jobs) and `app/translateFollow.js`
  (what the app says, free of React). It follows the jobs of every
  document: a job's background-tasks row keeps counting after the reader
  leaves its PDF, and its cancel halts it. The reading pill ("Reading the
  paper first…") shows only while the PDF on screen has a request waiting
  for the brief.
- **The session's end** (logout, another account or workspace) halts every
  job, its requests in flight aborted, and drops every document's state
  (`endTranslations` → `translateState.reset`), so no request goes out on
  the next session's cookie and the next account sees none of these
  translations.

### A page's entry

A page's entry holds results by block index: a translation; `null` for a
block that came back as its source (the original shows); `""` for a block
whose words all went to the part before it (a hyphenated word's tail alone
at a column's top: it is masked, nothing is written); missing while not
landed, or while its paragraph could not be translated (its index is then
in the entry's `failed` set). A job seeds each page's state from its entry
under the job's key, so a re-run keeps what is done and asks only for the
rest — a `null` block included. A page is done when none of its blocks is
pending, which a neighbouring page's unit can complete; a done page is
skipped by the button and by "Translate whole document", so only "Translate
this page" asks for its `null` blocks again.

The viewer requests `stream: true`. The reply is then NDJSON: `{"i":
[request indices], "text": partial}` lines as the model writes each
paragraph, then the same final object a plain call returns: `{translations,
model, cached, redone, failed, effort, brief}`.

- `cached`: every text was found in memory or among the kept translations,
  with no model call.
- `redone`: the paragraphs asked again ("The server").
- `failed`: the request indices that came back as their source because
  they still would not translate.
- `effort`: the level asked for.
- `brief`: the translations carry the paper's brief, or there is no paper
  to have one. False means a brief was possible but missing.

`translate_context.partial_segments` reads what the reply has written of
each segment so far, the one in progress included, less a tag half-written
at its end. Lines are throttled to ~20/s (`_TRANSLATE_STREAM_INTERVAL`), and
a segment maps to every request index that shares its source text.
`stream: false` (the default) still answers in one JSON body. Asking again
never streams. An upstream failure mid-stream is an in-band `{"error"}`
line.

On the page, a paragraph whose translation is queued gets a faint accent
wash over its original lines, an in-flight one shimmers, streamed text
types onto the page behind a caret (masking the original as soon as there
is something to show; `translatePlan.partials` splits a joined paragraph's
partial text at the markers written so far), and a landed paragraph fades
in. That is `TransPending`/`TransPara` in `pdf/TransOverlay.jsx`, driven by
the entry's `queued`/`busy`/`partial` fields, which the job clears when it
ends, halts or stops.

The overlay masks exactly the original lines of a block (plus the leading
between them) and lays the translation over them with an inline cloned
background, shrinking its font until it fits — so figures a paragraph
brushes against are never painted over, and the layout never moves.
Translated text is selectable/copyable; while shown, the invisible original
text layer stands down.

## What a request carries

Geometry never leaves the client. `pdf/pdfTranslate.js` segments a page's
pdf.js text runs into blocks:

- `textRuns` maps TextContent items through the scale-1 viewport, with each
  run's real font name (`pdf/pdfFonts.js`, below) and glyph ascent and
  descent (pdf.js's 0/0 for a font it has no metrics for counts as none).
  Rotated text (an arXiv stamp, a rotated axis label) is left out.
- Runs group into lines by their glyphs' vertical middle, anchored on the
  line's tallest run, so a big operator (CMEX's Σ hangs below its baseline)
  joins the line it is drawn on. A short superscript or subscript chain that
  formed a line of its own is folded back into the line whose glyph it
  touches; a line of smaller text (a reference beside the body, a sidebar)
  never folds.
- A line splits at a hard gap (≥ 2.2 line heights) and at a soft one only
  where a column gutter runs: `isGutter` looks for a stretch of the gap that
  the lines around it leave open (a river), whether or not the columns'
  baselines line up.
- Lines merge into blocks by step, size, indent and font. Each block keeps
  per-line rects, its first line's `indent`, its extracted `text`, and
  `src`/`spans` ("Formulas"). A block is translatable when its text without
  formulas is prose and it is no author line ("Formulas").

`pdf/translatePlan.js` plans what goes to the model, for the viewer and for
the bench alike:

- **Reading order** (`pageLayout`): the page is cut where a band of
  whitespace runs across its full width with text in both halves above and
  below (above a reference list; not beside a figure). Within a region,
  blocks wider than 60% of the text width stand alone; the narrower ones
  between them read column by column. Furniture reads last.
- **The body flow** (`pageFlow`): reading order without furniture — page
  numbers, smaller running heads and footnotes, captions ("FIG. 1.", "Fig. 1
  Conceptual overview", "Extended Data Fig. 3 |", not "Figure 1 compares"),
  and one-line blocks set apart in the top or bottom margin unless they start
  in lower case or are a numbered or named heading. A long block that is no
  text (a display equation, a table of numbers) stays in the flow, so a
  paragraph never joins across it.
- **Joins** (`continues`): a paragraph that a column or page break cut in two
  (or three) is ONE unit, its parts joined with `{br}`. A joins B only across
  a column or page break, when:
  - A is running text of two or more lines that ends mid-sentence, judged on
    its words without formula tokens.
  - B is body text of the same size that opens no paragraph of its own: no
    heading or caption, no first-line indent, running text itself.
  - Where the indent tells nothing (a page without indents, a one-line B), B
    reads on: it starts in lower case or follows a word no sentence ends on.
    A one-line B also ends a sentence, fills the page's measure, or
    introduces a display equation ("…in the rotating frame the" /
    "Hamiltonian reads").
  - A word hyphenated at A's end goes on only in lower case, and moves whole
    into A's part.
  - A join across a page break needs both pages in the job. Whichever page is
    planned first claims the unit, and it fills both.
- **Units and chunks**: one unit per translatable block or chain, in reading
  order; a chunk closes at 10 units or 2,500 characters (`chunkUnits`). Each
  unit renumbers its blocks' formula tokens into one sequence and knows whose
  each is.
- **Context**: each chunk carries `before` (the flow block before its first
  unit), `after` (after its last), each up to 1,500 characters as extracted,
  and `section` (the nearest heading before it in the flow, over earlier
  segmented pages).

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
- `before`, `after` and `section`.

`chunkRequest` in `app/translateClient.js` sends them with `texts`, `lang`,
`model`, `effort`, `speed` and `stream: true`.

A reply comes back per unit. `translatePlan.results` splits a joined unit's
translation at its `{br}` markers. When the model lost or doubled one, it
cuts by the parts' lengths at the nearest sentence or clause boundary, never
inside a number or after an abbreviation, and inserts no space between CJK
characters. Each piece's tokens are renumbered for the block it lands in
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
  A block whose words are all formula (a display equation) stays original.
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
  baseline. A citation or URL token is drawn as its text.
  - The raster's `onBase` callback (each base paint or prime) redraws the
    crop after a zoom. A formula moved across a join into another page's
    paragraph is drawn from its own page, whose box announces each paint (a
    `pdfbasepaint` event).
  - Until the page holds pixels the extracted text stands in
    (`.pdfTransMath.bare`). The extracted text also lies under the crop,
    transparent, so a copy of the translation carries it.
  - The page colour treatments (flipped colours, the dark and tinted themes)
    apply to the crop as to the page.

## The paper's brief

`gamma/translate_context.py`. A paper's brief is made once per account,
workspace, paper, language and model, by a request of its own:
`POST /api/ai/translate/brief`, which a job sends as it starts. A
translation request never makes it.

- **The paper** (`paper_of`): with `page_id`, the page row in the request's
  workspace (`require_ws`, checked only when a page is named) — its title,
  authors, year and venue from `meta`, and its stored PDF (`page_attachment`,
  `find_upload_file`, never a download). A duplicated page, which carries
  only the stored upload's URL, is that stored file (`attachment_file`), as
  the viewer reads it: the same paper, kept translations and Forget as the
  page it was copied from. Without a stored file, the request's
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
    it (`_BRIEF_WAIT`, 150 s, less the wait for the slot).
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

## The server

`gamma/routers/translate.py` (`ai_translate`, `ai_translate_brief`, the kept
translations' routes, the services' credentials), with the paragraph cache
and the kept translations in `gamma/translate.py` over
`gamma/translate_store.py`, and the prompt and the wire in
`translate_context.py`:

- **The system prompt** (`system_prompt`): the user message
  (`translate_context.user_turn`) is plain text. Its context comes first, a
  line each when given: `Section: …` (up to 200 characters), `Before
  (context only, not to translate): …` (the last 1,500 characters) and
  `After (…)` (the first 1,500). Then a blank line and the texts as
  numbered segments, one per line: `<s1>…</s1>`, `<s2>…</s2>`. Text that
  reads as a segment's tag (a paper quoting markup, `<S2>`, `＜s1＞`) goes
  out between `‹ ›` (`_mask`), since echoed inside its segment it would end
  that segment early or open another, and its translation gets the source's
  brackets back (`unmask`). The reply is
  the same segments, the same numbers in the same order, each `<sN>its
  translation</sN>` on a line of its own, never merged or split. Rules: keep
  every `{br}` at the matching clause boundary; translate a segment that
  starts or ends mid-sentence as a part that joins its context; keep each
  token once; keep other math, numbers, units and citation markers; one
  rendering per term, after the brief; references such as "Fig. 2" and "Eq.
  (3)" the target language's way; headings as headings. The target
  language's conventions follow for Chinese (full-width punctuation, a space
  between Chinese and Latin text), Japanese (である調) and Korean. The brief
  closes it.
- **Effort**: the user's pick in Settings → Translation, else the model's
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
  reply leaves out, opens twice or leaves empty has no translation, and
  neither has the last segment of a reply cut short: left open at its end
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
  came, under the same slot; which failures are retried, and after how
  long, is `ai_client.open_ai`'s `retries` ([ai.md](ai.md) "Calls open at
  once"). The brief passes `within`, so its tries end by the time its
  chunks stop waiting for it ([below](#the-papers-brief)). A stream cut
  midway is not retried: the viewer asks for the chunk again.
- **Cache**: an in-memory LRU (~5k entries, lock-guarded because requests
  run in the threadpool) per (user, language, bare model name, the paper's
  key, whether the brief came along, source text). It makes halts, retries
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

- **Tables** (made on first use, `ensure_schema`: derived data, like the
  PDF text index, so no migration):
  - `pdf_translations (doc_id, lang, model, src, text, brief, at)`, one row
    per paragraph, primary key `(doc_id, lang, model, src)`. `src` is the
    first 32 hex digits of the SHA-256 of the source text as sent (tokens
    and `{br}` included), so a page segmented again finds its paragraphs
    whatever their block indices. `brief`: made with the paper's brief.
  - `pdf_translation_briefs (doc_id, lang, model, brief, at)`: the rendered
    brief.
  - `model` is the bare model name, as the memory cache keys it, or
    `engine:<id>` for a machine-translation service.
- **Reading**: where memory misses, a request reads the kept rows of its
  texts in one query (500 hashes to an IN list), and puts what it finds back
  in memory. A request that carries the brief takes only rows made with it.
  Kept translations are read whatever the request's `keep`.
- **Writing**: after the reply, the new translations go to memory and, when
  the request's `keep` is on and its account may write the workspace
  (`auth.can_write`), to the store with `brief` as the request had it. A
  viewer's translations stay in memory. A row made with the brief is never
  replaced by one made without. A service's translations are kept too, as
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
  translations and brief along.
- **Errors**: every function logs its own sqlite errors and carries on as
  if nothing were kept. Derived data never fails a translation.

The routes, workspace-scoped like the PDFs (any member reads what is kept,
a writer clears it):

- `GET /api/translations/stats` → `{documents, paragraphs, bytes}`, the
  bytes being the kept text's UTF-8 size, translations and briefs. Settings
  → Language and Translation › Service shows it under "Keep translations"
  (`translateKeep`, account, default on), with Clear
  ([settings.md](settings.md)), which sends the next route and then drops
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
  translate button's "Forget this translation" sends it
  (`forgetTranslation`), then drops the document's entries in the viewer
  (`translateState.forget`), so the next click translates afresh. The menu
  offers it while no job runs, when some page holds translated text, the
  document on screen is the page's, the page has a file the server keeps
  translations under (`attachmentFile`: its upload, a duplicated page's
  upload, or for a PDF opened from its link the copy the proxy saves under
  the link's hash) and the account may write the workspace. A PDF opened
  from its link with "Save a copy" off may have no copy: nothing of it is
  kept, and Forget resets the server's memory of it through `page`.

## Selection translation

The text-selection popup (`PlainTip` in `pdf/PdfViewer.jsx`: the highlight
colors, a line, then Comment and the link) carries a 文A button, last, when
Settings → Translation › "Translate a selection" is on (`selTranslate`,
account pref, default on).

- It sends the selection as ONE text through the page translator's request
  (`translateChunk`: same model or service, language, server cache,
  streamed partials), with the page and `wait_brief: false`: the paper's
  brief comes along when it is made already, never waited for. It sends
  `keep` as a chunk does, so its translation is kept with the paper's.
  Lines are rejoined by `selectionParagraphs` (`pdf/pdfTranslate.js`, the
  page blocks' hyphen/CJK rules), capped at 5000 characters. It sends no
  tokens: the selection is the text layer's.
- The result shows under the colors as a fold-out panel: a header with the
  language, spinner and copy button, then selectable text. The button
  refolds it.
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
- The service path needs no AI provider. It has no effort, no brief (the
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

- **Dry numbers**: blocks, blocks kept original, units and requests sent,
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
