# PDF translation

The viewer's translated view: the button and its states, how a page is
planned into requests, what a request carries (the paper's brief, the
context around its texts, formulas as tokens), the engines and their
limits, and the bench that measures it. Provider configuration and the chat
are in [ai.md](ai.md); the engine preferences are in
[settings.md](settings.md). Why it is built this way, and the numbers before
and after: [research/translation-quality.md](../research/translation-quality.md).

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
  translating while running).
- The button is one of the strip's tools (`translate` in the `viewerTools`
  preference, [settings.md](settings.md#bars-order-and-modes)): its place
  is the user's order, and hidden (Settings → Translation › Translation
  button off, or Appearance › Viewer tools) it is a row of the strip's ⋯
  menu that does what a click does. While it translates or a translation
  is shown it stays on the strip, whatever its mode.

## The queue

A whole-document job queues pages nearest the current page first (forward
before backward at equal distance), so the page being read paints
immediately. The queue lives in `pdf/PdfViewer.jsx` (`runTransJob`,
`translateCtl`), producer/consumer style:

- The producer segments each queued page and its neighbours in the job
  (`ensureParas`), plans the page (`pdf/translatePlan.js`, next section) and
  appends its chunks to one flat list.
- N workers (Settings → Translation → parallel requests,
  1–`TRANSLATE_PARALLEL_MAX` = 4 in `app/prefDefs.js`, below the six AI calls
  an account may have open at once) stream through the list across page
  boundaries: the first request is in flight while later pages are still
  segmenting, chunks paint as they land, and char-weighted progress shows
  under the button and as a background-tasks row.
- Halting aborts the in-flight requests (each job carries an
  AbortController) and keeps finished chunks.
- A chunk whose request fails is asked again (`translatePlan.requestChunk`).
  A 429 with `Retry-After` (the account's AI calls are all in use, often by
  the server asking the model again for other chunks) waits as long as it
  says, for up to two minutes in all; any other failure (the network, the
  provider, a stream cut off before its final line) is asked once more after
  two seconds; a 429 without it (the shared allowance used up) is final. A
  chunk that still fails stops the job, and the host says why once the
  requests in flight have ended ("Translation failed: …").
- The server asks the model again for what its reply got wrong ("The
  server") and names the paragraphs that still failed (`failed`). Those
  stay pending: the original shows, without the queued wash, and the page
  is not done. At the job's end the host says how many there are, with a
  "Try again" that runs a job over just their pages (`retryUntranslated`);
  the button on such a page asks for them too.

A page's entry in `transMap` holds results by block index: a translation;
`null` for a block that came back as its source (the original shows); `""`
for a block whose words all went to the part before it (a hyphenated word's
tail alone at a column's top: it is masked, nothing is written); missing
while not landed, or while the server could not translate it (its index is
then in the entry's `failed` set). A job seeds each page's state from its
entry under the job's key, so a re-run keeps what is done and asks only for
the rest — a `null` block included. A page is done when none of its blocks
is pending, which a neighbouring page's unit can complete; a done page is
skipped by the button and by "Translate whole document", so only "Translate
this page" asks for its `null` blocks again.

The viewer requests `stream: true`. The reply is then NDJSON: `{"i": [request
indices], "text": partial}` lines as the model writes each paragraph, then the
same final object a plain call returns: `{translations, model, cached, redone,
failed, effort}`, where `redone` counts the paragraphs asked again ("The
server"), `failed` lists the request indices that came back as their source
because they still would not translate, and `effort` is the level asked
for. `ai_client.partial_json_strings` reads the
complete elements of the half-written JSON array plus the one in progress.
Lines are throttled to ~20/s (`_TRANSLATE_STREAM_INTERVAL`), and an element
maps to every request index that shares its source text. `stream: false` (the
default) still answers in one JSON body. Asking again never streams. An
upstream failure mid-stream is an in-band `{"error"}` line.

On the page, a paragraph whose translation is queued gets a faint accent
wash over its original lines, an in-flight one shimmers, streamed text
types onto the page behind a caret (masking the original as soon as there
is something to show; `translatePlan.partials` splits a joined paragraph's
partial text at the markers written so far), and a landed paragraph fades
in. That is `TransPending`/`TransPara` in `pdf/PdfViewer.jsx`, driven by the
entry's `queued`/`busy`/`partial` fields, which the job clears when it ends,
halts or fails.

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
  formulas is prose.

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
`page_id` (the page being translated, for the paper's brief), `head` (the
document's opening text, up to 12,000 characters, when the server has no
stored copy: `App.jsx` passes `translateScope={{pageId, stored}}`, `stored`
when the PDF's URL is one of the server's uploads), `before`, `after`,
`section`, and `build_brief: false` for a one-off text. App's `translateChunk`
sends them with `texts`, `lang`, `model`, `effort`, `speed` and `stream`.

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
  the viewer builds the operator list once (`page.getOperatorList()`) and lets
  its decoded images go again when the page is far from the one being read.
  Names are cached per document.
- `pdf/pdfMath.js` decides, per run, what is math: a math font (CMMI, CMSY,
  CMEX, MSAM/MSBM, Euler, txfonts/newtx, MathTime, Symbol, Euclid, Cambria
  Math, STIX and Latin Modern math, KaTeX's, …), a typewriter/code font,
  mostly math characters (Greek, operators, arrows, math alphanumerics,
  private-use glyphs, and ð Þ þ ¼, the letters AIP's symbol fonts draw
  brackets, plus and equals with), a sub- or superscript, or a one- or
  two-letter variable in an italic font (not an italic "a", "et al." or
  another short word: `ITALIC_WORDS`). A run of lower-case prose is no formula
  whatever its font. Digits, operators and brackets between two math runs
  belong to the formula, so "V(r) = C₆/r⁶" is one span.
- Each formula span becomes a token, `{v1}`, `{v2}`… in the block's `src`, its
  box (glyph extent, scale-1 units) and baseline kept in `spans`. Citation
  markers (`[12]`, `[3–7]`) and URLs become tokens too, kept as literal text.
  A block whose words are all formula (a display equation) stays original.
- The prompt says the tokens stand for what the reader sees as printed and
  must each be kept once. The server checks every reply paragraph
  (`translate_context.checked`: `{ v1 }`, `｛v1｝` and `{V1}` are read as
  `{v1}`); the paragraphs that lost, doubled or invented a token are redone
  together in one call, and one that fails again comes back verbatim. A
  machine-translation service's reply gets the same check, without the redo.
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

`gamma/translate_context.py`. The first translation request of a paper makes
its brief, once per account, workspace, paper, language and model:

- **The paper** (`paper_of`): with `page_id`, the page row in the request's
  workspace (`require_ws`, checked only when a page is named) — its title,
  authors, year and venue from `meta`, and its stored PDF (`page_attachment`,
  `find_upload_file`, never a download). Without a stored file, the request's
  `head`. The key names the stored file (`doc:<id>`). A page without one is
  named by its id (`page:<id>`), so the selection translator, which sends no
  head, finds the brief a job made from the head. Without a page, the key is a
  hash of the head.
- **The material** (`Paper.material`): the PDF's outline (sections up to the
  second level, figure bookmarks left out) and its first 8,000 characters.
  Only the request that makes the brief reads the file.
- **The call**: one model call with `_BRIEF_PROMPT` returns a JSON object —
  the field, a two-sentence summary in the target language, notation the
  paper defines, 20–40 recurring terms with their renderings, and names that
  stay as written. `render_brief` turns it into the system prompt's closing
  section (at most 4,000 characters). Its usage counts as `translate`.
- **Sharing it** (`brief_for`): parallel requests for the same key wait for
  the one making it, up to 150 s. Briefs are kept in memory (LRU of 300),
  like the paragraph cache.
  - At most six requests of an account wait at once, the six AI calls it may
    have open; the rest go on without the brief.
  - A streamed request starts its response first and makes or waits for the
    brief inside the stream, which keepalive lines hold open. A plain request
    waits before it answers.
  - The brief is why a paper's first requests are slow: the model writes up
    to 40 terms and 15 symbols before any paragraph is translated, often half
    a minute. A streamed request that waits for it says so with a `{"stage":
    "brief"}` line, then `{"stage": "translate"}` once it is made. While any
    request of a job is at that stage, the status pill says "Reading the paper
    first, so its terms stay the same throughout…" and the background-tasks
    row "reading the paper…" (`transReading` in `pdf/PdfViewer.jsx`,
    `handleTranslateState` in `App.jsx`).
  - A failure or an unusable reply is remembered for two minutes, and the
    requests translate without a brief meanwhile. A call Gamma refused itself
    (the account's calls all in use, or its allowance spent) says nothing
    about the paper and is not remembered.
- **`build_brief: false`** (the selection translator) carries the brief only
  when it is made already, never waits for one.
- **Prompt caching**: the brief sits in the system prompt, which every chunk
  of the paper shares. Anthropic's adapter marks the system block (and, as for
  the chat, the user turn, a small write premium on each chunk); the OpenAI
  wires get `prompt_cache_key` = `translate_context.cache_key` (one per
  account, workspace, paper and language). A model whose minimum cacheable
  prefix is longer than the rules plus the brief caches nothing.

## The server

`ai_translate` in `routers/ai.py`, with the prompt in
`translate_context.py`:

- **The system prompt** (`system_prompt`): the user message is a JSON object
  (`translate_context.user_turn`: `texts`, and when given `section`, `before`
  — its last 1,500 characters — and `after`); the reply a JSON array of the
  same length. Rules: keep every `{br}` at the matching clause boundary;
  translate a string that starts or ends mid-sentence as a part that joins
  its context; keep each token once; keep other math, numbers, units and
  citation markers; one rendering per term, after the brief; references
  such as "Fig. 2" and "Eq. (3)" the target language's way; headings as
  headings. The target language's conventions follow for Chinese (full-width
  punctuation, a space between Chinese and Latin text), Japanese (である調)
  and Korean. The brief closes it.
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
  translations, and repair calls. Translation services ignore it. Changing
  speed keeps existing paragraph and brief caches.
- **Cache**: an **in-memory only** LRU (~5k entries, lock-guarded because
  requests run in the threadpool) per (user, language, bare model name, the
  brief's key, source text). Nothing goes to disk; the cache makes
  halts/retries/re-shows free until a restart.
  - The brief's key names the paper, not the brief's text, so a translation
    made with the brief stays cached after the brief leaves its LRU, and one
    made without a brief stays apart.
  - Duplicate paragraphs within a request go upstream once. A paragraph that
    comes back verbatim is not cached, so a retry can improve it.
  - Caps: 200 texts / 60k chars per request.
- **Reading the reply** (`_parse_translation_array`): a JSON array of one
  string per text, code fences and prose around it tolerated. Asked for one
  text, a model may leave the brackets off, so a reply that holds no array
  of strings is that text's translation (`_bare_translation`: out of a code
  fence, or a JSON string's value; "见 [12]" is text, not the array `[12]`),
  unless it is empty or JSON that is no string. An empty translation counts
  as broken (`translate_context.checked`).
- **Asking again** (`again` in `ai_translate`): the paragraphs whose reply
  broke a token are redone together in one call, aligned by index. A reply
  array of the wrong length (the model merged or dropped an element, and
  which one is unknown) is asked again in two halves, and a half whose reply
  is off too one paragraph per call: a model keeps a short array far more
  often than a long one, so a chunk usually takes two more calls, not one
  per paragraph. The calls of one round run concurrently (up to
  `_TRANSLATE_REPAIR_WORKERS`, 4). A call that failed (not a reply off) is
  not split further. A paragraph goes with the section but not the chunk's
  neighbours, and one that still fails comes back verbatim instead of
  failing the request, named in the final object's `failed` (the request
  indices it fills, a duplicate's too) and not cached. `redone` counts the
  paragraphs asked again.
- **Call slots**: these calls, and a streamed request's own call once past
  the door's check, wait for one of the account's six call slots (up to
  `_TRANSLATE_SLOT_WAIT`, 120 s) rather than being refused: the stream's own
  call has freed its slot by then, but the account's other requests and
  their repairs may hold them all. The door still refuses a request at once
  when all six are in use, a 429 with `Retry-After` that the viewer waits
  out ([ai.md](ai.md) "Calls open at once").

## Selection translation

The text-selection popup (`PlainTip` in `pdf/PdfViewer.jsx`: the highlight
colors, a line, then Comment and the link) carries a 文A button, last, when
Settings → Translation › "Translate a selection" is on (`selTranslate`,
account pref, default on).

- It sends the selection as ONE text through the page translator's request
  (`translateChunk`: same model or service, language, server cache,
  streamed partials), with the page and `build_brief: false`: the paper's
  brief comes along when it is made already. Lines are rejoined by
  `selectionParagraphs` (`pdf/pdfTranslate.js`, the page blocks' hyphen/CJK
  rules), capped at 5000 characters. It sends no tokens: the selection is the
  text layer's.
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
- The service path needs no AI provider. It has no effort, no brief, no
  streamed partials (the stream is just the final line), no token usage and
  nothing to ask again, since the APIs answer aligned lists. A service
  translates sentence by sentence and takes no context.
- Cache, validation and dedup are the LLM path's, keyed on `engine:<id>`.
  Nothing tells a service to keep the `{v1}` tokens or `{br}` markers; a
  reply that broke a token comes back verbatim (the original shows), and a
  lost marker is cut by length. `failed` stays empty: a service answers the
  same text the same way, so the viewer asking again would get the same.

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
  node tests/bench/translateBench.mjs paper.pdf --live --judge --lang zh-CN
node tests/bench/translateBench.mjs --record tests/fixtures/translatePaper.json
```

It writes `<label>.json` and `<label>.html` (each request's texts and
translations side by side) to the OS temp dir (`--out` to choose).

- **Dry numbers**: blocks, blocks kept original, units and requests sent,
  characters; `endsMid` (body texts sent that end mid-sentence: a paragraph a
  break cut, or one that goes on past a display equation, which the context
  covers), `startsMid` (texts sent that start in lower case), `joined` (units
  with `{br}`), `tokens`, `mathChars` (math characters still sent as text
  outside tokens), and `contextMathChars` (math characters in the context
  around the requests, which goes as extracted).
- **Live numbers** (`--live`, through an isolated server from the e2e harness,
  the requests the viewer makes): failed requests, paragraphs back verbatim,
  paragraphs `redone` (the server's count), paragraphs `untranslated` (the
  server's `failed`: still broken after asking again), breaks lost, the median request
  time, the wall time, the effort the server asked for, and the token usage.
  The server keeps every token (a broken paragraph comes back as its source),
  so a lost token shows in `redone` and `unchanged`, never in the texts. The
  isolated server runs with the model catalog on, as a real one does, so the
  default effort is the model's lowest level.
- **`--judge`** asks the same model once which renderings each of the paper's
  frequent terms got across the translations: `meanRenderings`, 1.0 when every
  term was rendered one way; `null` with an `error` when there were no
  frequent terms or the judge's call failed (`--compare` shows it as —).
- **The fixture paper** (`frontend/tests/fixtures/translate/paper.tex`) is
  original text in a two-column revtex layout with the cases the view has to
  get right; `--record` writes its pdf.js runs to
  `frontend/tests/fixtures/translatePaper.json`, which the unit tests read.
