# Why the translated view fails and waits, what keeping a translation costs, and the plan

The translated view ([dev/translation.md](../dev/translation.md)) plans a
paper well since [translation-quality.md](translation-quality.md) was built:
split paragraphs go as one text, formulas as tokens, a brief of the paper
rides with every request. What it still does badly, as the reader meets it:
a job sometimes stops or leaves paragraphs in English; the first click on a
paper waits half a minute before any text lands; a whole-document job takes
long; and everything it made is gone after a restart. Read from the code in
October 2026 (`routers/ai.py` `ai_translate`, `translate_context.py`,
`pdf/PdfViewer.jsx` `runTransJob`, `pdf/translatePlan.js`, `pdf/pdfMath.js`,
`app/App.jsx` `translateChunk`), with the library's own 120 PDFs scanned for
their text volume and fonts, and the bench's dry mode run on four of them.
No provider was called for this survey (the live baseline ran later, see
"As built"). This note answers four questions and ends in an ordered plan.

## What one translation costs today, measured

The local library (`data/workspaces/*/uploads`): 120 PDFs, 5,052 pages,
13.4 M characters of text, 2,660 characters a page on average over
everything (front matter, figures and references included). 84 are set in
TeX fonts (Computer Modern, Latin Modern, MathTime, STIX), 36 in a
publisher's (Elsevier's `AdvOT…`/`AdvP4C4E…`, Nature's `Harding`/`Graphik`
with `MathematicalPi` and `EuclidMath`, Helvetica and Times from Word).

The bench's dry mode (`frontend/tests/bench/translateBench.mjs`, pages 1–6
of each) on four of them, what the viewer would send:

| Paper | Characters sent | Units | Requests | Formula tokens | Math characters leaked as text |
|---|---|---|---|---|---|
| Two-column, Computer Modern | 29,321 | 93 | 13 | 168 | 17 |
| Elsevier (AdvOT text, AdvP symbol fonts) | 28,127 | 202 | 24 | 291 | 2 |
| Nature-style (Helvetica, MathematicalPi) | 32,994 | 113 | 18 | 323 | 2 |
| MathTime + Palatino | 15,212 | 61 | 13 | 48 | 6 |

So a dense page sends 2,500–5,500 characters in 2–4 requests, and a
30-page paper is 60–120 requests of 1,000–2,500 characters each. Every
request's reply is the whole translation of its texts: for Chinese about
0.45 characters per source character, 1,000–2,500 output tokens a request.
Output tokens are what the job waits on: at a model's 50–100 tokens a
second a request takes 15–40 s, so three parallel requests do a page in
roughly 20–40 s and a 30-page paper in 10–20 minutes. Input is mostly the
repeated system prompt (rules plus the brief, about 2,000 tokens a request,
cached where the wire caches it) and the context around each request
(`before`, `after`, `section`, up to 3,200 characters, never cached). For a
30-page paper that is on the order of 300k input tokens, two thirds of
them the cached prefix, and 70k output tokens — paid again in full for every
translation the server has forgotten.

## 1. Why it sometimes fails

Five failure classes, each a code path. Which of them a given user met is
in the server log's `[ai_translate]` lines
([dev/debugging.md](../dev/debugging.md)); the log is in memory, so it has
to be read before a restart.

- **The reply is a positional JSON array.** The model answers one JSON
  array of strings for the request's `texts`, aligned by position. Models
  merge two paragraphs into one element, drop one, or write a quote or
  newline that breaks the JSON (CJK quotation marks inside a string, a code
  fence around the array). `_parse_translation_array` then raises, and
  `again` asks for the chunk in two halves, then one text per call: a
  10-paragraph chunk whose reply is one element short costs up to 1 + 2 +
  10 further calls. A text that still comes back wrong returns verbatim and
  is named in `failed`, which the reader sees as "N paragraphs could not be
  translated" with a Try again. The repair ladder is 150 lines of
  `routers/ai.py` and the largest single cause of both failures and slowness;
  the debugging doc then said "many `expected N, got M` lines mean the model
  keeps losing paragraphs".
- **Call slots run out.** An account has six AI calls open at once
  (`ai_client.MAX_OPEN_CALLS`). A job runs up to four workers, each request's
  repairs take up to four more calls (`_TRANSLATE_REPAIR_WORKERS`), and the
  brief takes one: a chatty model with a parallel-4 job asks for more than
  six at once. The door refuses with a 429 the viewer waits out for up to two
  minutes (`SLOT_PATIENCE_MS`); calls past the door wait up to 120 s for a
  slot (`_TRANSLATE_SLOT_WAIT`) inside a 180 s call timeout; the request's
  stream, still open, shows a shimmering paragraph that does nothing. The
  job's end is then "Translation failed: too many AI calls at once" or a
  silent five-minute stall on one chunk.
- **The brief fails.** One call with `max_tokens` 8,000 and a 150 s timeout
  writes 20–40 terms, 15 symbols and a summary. A slow model or a
  rate-limited provider misses the timeout; the failure is remembered for
  two minutes, during which chunks translate without a brief under a
  different cache key, then the next request tries again. The paper ends up
  partly translated with the glossary and partly without, and a re-run
  re-translates the part made without.
- **One chunk stops the job.** A request that fails twice (the network, a
  provider 5xx, a stream cut before its final line) sets `failed` in
  `runTransJob` and every worker stops. A 30-page job dies on one bad chunk;
  the next click resumes from the cache, but the reader has to click.
- **Microsoft's free endpoint** is undocumented; a throttle or a change
  answers an `EngineError`, a 502, and the job stops after one retry. The
  `free-translate` notice tells the user afterwards, not the job.

Client-side causes were looked for and not found: segmentation is
deterministic, `pageFaces` falls back to character classes after its 3 s
operator-list timeout without failing, and the overlay never throws on a
missing span.

## 2. Why the first click waits

- **The brief is made in the critical path.** A paper's first request
  makes it (the parallel requests wait, `_BRIEF_WAIT` 150 s): the model
  reads 8,000 characters of the paper plus the outline and writes a JSON
  object of up to 8,000 tokens before any paragraph is translated. That is
  the "Reading the paper first…" pill, 10–60 s depending on the model. It is
  paid per (account, workspace, paper, language, model), again after a
  restart or once 300 other briefs pushed it out of the LRU.
- **The paragraph cache is memory only.** 5,000 paragraphs process-wide
  (`_TRANSLATE_CACHE_CAP`, 150–300 pages at the 15–35 units a page the
  bench counts), gone on restart. A paper read
  yesterday is translated again today, brief included.
- **A PDF shown from its URL** segments its first three pages before the
  first request (`documentHead`, operator lists included) and sends 12,000
  characters of `head` with every chunk, which the server uses once.
- **The model.** "Translate with" follows the chat model unless set. A
  large or reasoning chat model (effort defaults to the lowest level, but
  the model is still the large one) writes at 30–60 tokens a second where a
  small one writes at 150. Settings says a fast, cheap model is right; the
  default does not pick one.

## 3. What keeping a translation costs, and whether the user should decide

Nothing is kept today, deliberately: the cache "makes retries, re-shows and
halted-job resumes free until a restart". The cost of keeping it, from the
survey above:

- A Chinese translation is about 0.45 characters per source character at
  three bytes each: 1.3× the source's bytes. A dense page sends 4,000–5,500
  characters, so its translation is 5–7 KB raw. The source text of these
  papers compresses 3:1 with zlib (measured on their first 12 pages); the
  translation about 2.5:1: 2–3 KB a page.
- A 30-page paper: about 200 KB raw, 70 KB compressed, per language and
  model. The brief: 1–4 KB.
- The whole local library, every page translated into one language: about
  17 MB raw, 6–7 MB compressed. The uploads it came from are 360 MB and the
  backups directory 651 MB. Kept translations are under 2% of the PDFs they
  belong to.

So storage is not the cost. The cost is not keeping it: a forgotten paper is
10–20 minutes and some 300k input and 70k output tokens again, and a
forgotten brief 30 s before every first page. The user should still decide,
for two reasons that have nothing to do with size: a translation is derived
data some people would rather not have on a shared server, and a stored
translation keyed by a model name outlives the provider that made it. The
plan below adds one switch (keep translations, on by default), shows what
is kept with a Clear, and forgets a document's translation with the
document.

## 4. How formulas are recognized, and inline ones

The rules are `pdf/pdfMath.js`, per run of a line
([dev/translation.md](../dev/translation.md) "Formulas"); nothing is sent to
a model to decide. A run is math when:

- its PDF font name (read from `page.commonObjs` once an operator list
  exists, `pdf/pdfFonts.js`) matches the math-font table: Computer Modern's
  CMMI/CMSY/CMEX, AMS, Euler, Latin Modern and the OpenType math fonts,
  txfonts/newtx, MathTime, Mathematica (which also matches Nature's
  MathematicalPi), Euclid, Symbol, STIX, KaTeX's; or a typewriter font;
- most of its characters are math (Greek, operators, arrows, math
  alphanumerics, private-use glyphs of symbol fonts, and AIP's ð Þ þ ¼);
- it is a sub- or superscript (folded back into its line by the segmenter);
- it is a one- or two-letter word in an italic font that is not a word
  ("a", "et", "al"…): a variable.

Digits, operators and brackets between two math runs, or glued to one, join
the formula, so `V(r) = C₆/r⁶` is one span. Adjacent math runs within 0.65
line heights merge into one span; the span's box is its glyphs' extent with
the fonts' ascent and descent, kept on the block, and the block's text gets
`{v1}`, `{v2}`… in its place. Citation markers and URLs are tokens too,
drawn as text. The overlay draws a formula token as a crop of the page
bitmap at the span's box, sized in em of the paragraph so it shrinks with
the text, on the text's baseline.

Inline equations are therefore handled the same way as display ones: there
is no separate rule. A display equation is a block whose text without its
tokens is not prose (`isTranslatable`), so it stays original; "where {v1} is
the density matrix" on an equation's own line is prose with a token and is
translated. In the bench's dry run above the four papers leaked 2–17 math
characters each over six pages, so the font table covers the library's
TeX and publisher fonts alike.

What it misses, from reading the bench's texts:

- **Symbol fonts that map their glyphs to Latin letters.** Elsevier's
  `AdvP4C4E74` keeps the Computer Modern Symbol encoding and no ToUnicode
  table, so ⟨, ⟩ and | are extracted as "h", "i" and "j" (checked with
  PyMuPDF on the Elsevier paper: `|a⟩` is `jai`); the font is not in the
  table and those are not math characters, so `⟨Ĵ⟩` goes to the model as
  `h{v2}ˆi` — the hat (U+02C6, a modifier letter outside the math character
  class, from the text font `AdvTTbdb21c9e+02`) outside the token too. The
  bench's "math characters leaked" metric is blind to exactly this, so the
  2 above is an undercount for that paper. The MathTime raw fonts (`RMTMI`)
  and Elsevier's `.I+03`-style italic suffixes are missed by the table and
  the italic test the same way.
- **Multi-letter italic names.** A variable or function of three letters or
  more in a text italic font (`Tr`, `Re` pass; `exp`, `Ref`, `Gap` set in
  Times-Italic do not) is a word to the classifier.
- **Author and affiliation blocks** are prose with superscript tokens
  (`Simon J. Evered{v3}, Rahul Sahay{v4}, …`) and are sent to the model,
  which has nothing to translate in them.
- **Scanned PDFs with an OCR layer** have no fonts: only the character
  class and script rules apply, and OCR writes few math characters.
- **A page far from the view** has its operator list built for its font
  names and then let go (`page.cleanup()`); when the 3 s wait runs out the
  page is classified by characters alone, silently.

None of these needs a new approach: the first three are table entries, a
character range and a planner rule, measured by the bench.

## What others do

- **PDFMathTranslate / BabelDOC** translate one paragraph per model call
  (a thread pool over paragraphs), so alignment never fails and a failed
  paragraph is retried alone; formulas are `{v1}` placeholders found by font
  name and character class, as here. The cost is a call per paragraph.
- **Immersive Translate** batches paragraphs into one call with a numbered
  marker around each and splits the reply at the markers; a paragraph
  whose marker is missing is retried on its own.
- **Both keep their output**: PDFMathTranslate writes a translated PDF,
  Immersive Translate keeps its translations in the browser.

The two ideas taken: numbered segments instead of a positional array, and a
store keyed by the text's hash.

## Plan

Each step ships on its own and is measured on the bench before and after.
Step 0 is the measurement the earlier note never got.

### 0. The live baseline

Run `translateBench.mjs --live --judge` on the four papers above (pages 1–6,
Chinese) with a provider key from the environment, before anything changes:
failed requests, paragraphs `redone`, paragraphs `untranslated`, the
median request time, the wall time, the token usage. Keep the JSON under
`frontend/tests/bench/` as `baseline-<date>.json`, not a fixture. Every
later step reruns it with `--compare`.

### 1. Move the translator out of `routers/ai.py` (pure move)

`ai_translate`, the cache, `_TranslateBatch`, the repair and the engine
endpoints (about 500 lines) become `routers/translate.py`, with the
cache and batch in `gamma/translate.py`; `translate_context.py` (prompt,
brief) and `translate_engines.py` stay. `routers/ai.py` keeps the chat. No
behaviour changes; the tests move with it. Every later diff is then
readable.

### 2. Numbered segments instead of a positional JSON array (server)

The user turn becomes the texts as numbered segments, one per line:

```
<s1>If we now repeat the same procedure with {v1} atoms …</s1>
<s2>Indeed we will later see that {v2} …</s2>
```

with `section`, `before` and `after` as labelled context above them, and
the reply the same segments in the same form. The parser (`segments()`)
reads `<sN>…</sN>` tolerantly (spaced, full-width or `[N]`-style tags are
read too), aligns by number, and:

- a number missing or doubled → that text alone is asked again (at most two
  such calls at once), then verbatim and `failed`, as now;
- text outside the tags is ignored (prose, fences);
- a streamed partial is everything after the last `<sN>` opened:
  `ai_client.partial_json_strings` goes (replaced by
  `translate_context.partial_segments`), and so do
  `_parse_translation_array`, `_bare_translation`, the halves-then-singles
  ladder (`_halves`, `_singles`, `again`'s loop) and the
  one-text-without-brackets case.

The token check (`checked`), `{br}`, the verbatim fallback and `failed` are
unchanged; `redone` counts the single re-asks. Structured output (a JSON
schema on the wires that take one) was considered: it guarantees valid JSON
but not the array's length, which is the actual failure, and the local
OpenAI-compatible servers and the chatgpt wire do not all take it. Numbered
segments work on every wire. Measured on the bench: `redone` and
`untranslated` should fall to near zero.

### 3. One call slot per translation request (server)

A request takes one of the account's six slots for its whole life — its
stream and its re-asks run under the same slot, the re-asks serially — so a
job of N workers holds N slots, and the brief one. `_TRANSLATE_SLOT_WAIT`
stays only at the door. The viewer's 120 s patience for a 429 shrinks to the
door's `Retry-After` plus a margin. With step 2 there is little to repair,
so this costs no throughput. `TRANSLATE_PARALLEL_MAX` stays 4: the chat
keeps two.

### 4. Keep translations and briefs (server + client)

Two tables in the workspace's `pages.db`, migration 45:

- `pdf_translations(doc_id, lang, model, page, data, updated_at)`, primary
  key (doc_id, lang, model, page); `data` a zlib-compressed JSON object
  `{src_hash: translation}` for the page's blocks, `src_hash` the first 16
  hex digits of SHA-256 of the block's `src` (tokens included), so a
  re-segmented page finds its paragraphs again whatever its block indices
  became; `model` the bare model name or `engine:<id>`, as the cache key.
- `pdf_translation_briefs(doc_id, lang, model, brief, made_at)`.

Written by `/api/ai/translate` after `finish` when the request names a page
with a stored PDF and the translator's account has the switch on; the
memory LRU stays as the hot layer and serves PDFs shown from a URL. Read by
`GET /api/translations/{doc_id}?lang=&model=&pages=1-12` →
`{pages: {n: {hash: text}}, brief: true|false}`: the viewer seeds `transMap`
from it when the paper opens, so a kept paper shows at the first click, with
no AI connection, and a job asks only for what is missing. Members of the
workspace read it (they read the PDF); the share view may show it
read-only later. `DELETE /api/translations/{doc_id}` is "Forget this
translation" in the button's menu; `upload_gc`'s purge drops the rows with
the file. Not exported, synced or mirrored, like `workspace_prefs`; the
workspace backup copies the whole `pages.db` (SQLite's backup API in
`backups.py`), so it carries them as it carries the rest.

Settings → Language and Translation gains, under Service, **Keep
translations** (account, default on; off: memory only, as today) and a
line "Kept: N documents, X MB" with Clear (the workspace's rows).

### 5. First text before the brief (client + server)

- `POST /api/ai/translate/brief {page_id | head, lang, model}` makes the
  brief or returns the kept one (`{ready, waited}`; a stream of `{stage}`
  lines for the pill). The viewer calls it as the job starts, in parallel
  with segmentation, and sends `head` only there: chunks carry `page_id`
  alone.
- The job's first chunk goes at once with `build_brief: false` (with the
  brief if it exists, else without), so its paragraphs stream onto the page
  within one request's latency. The later chunks wait for the brief inside
  their stream, as now. When the brief lands and the first chunk went
  without it, the job asks for that chunk once more and fades the new text
  in; the kept translation is the one made with the brief. One extra
  request, the first time only.
- The brief itself gets smaller: `max_tokens` 2,500, 20–30 terms, 10
  symbols, no summary (the field and the terms are what the chunks use), a
  60 s timeout, `speed: fast` where the model offers it; a failed brief is
  tried once more by the next request instead of a two-minute blackout, and
  a chunk that went without a brief is kept apart in the store as now
  (`brief: false` on its rows) so a re-run can redo it.

### 6. Throughput and the whole-document job

- The job survives leaving the page: `runTransJob` and its queue become
  `pdf/translateJob.js`, a module without React that the host owns, holding
  a pdf.js document of its own to segment with (opened from the same bytes,
  which the HTTP cache and the IndexedDB copy serve again at once,
  [dev/pdf_loading.md](../dev/pdf_loading.md)); the viewer renders whatever
  its page's entry holds when it mounts again. A whole-document job then
  runs on while the reader opens other pages, and its results land in the
  store.
- A fast model by default: when "Translate with" follows the chat model
  and that model is a reasoning or large one, the first job of a session
  says so once in the status line ("Translating with <model>; a smaller
  model is usually enough", a link to the setting). No automatic pick: the
  catalog does not know which model is cheap on every provider.
- Chunk size: with numbered segments a longer chunk no longer risks a
  longer repair, so 15 units / 4,000 characters is worth measuring (fewer
  requests, the same output tokens, more context per request); the bench's
  wall time decides.

### 7. Formulas: the table entries from the survey

`^AdvP4C4E`, `^AdvPS` (Elsevier's symbol fonts), `^RMT(MI|SY)` (MathTime
raw) in the math-font table; `.I+\d+$` beside `.I$` in the italic suffixes;
U+02C6–02DC (modifier letters: the hat, the tilde) in the math character
class; a planner rule that leaves an author block (one line, every word
capitalised, a token after every name) original. The bench's "leaked"
metric gains a count of tokens' neighbours that are single Latin letters
from a symbol font, so the `h{v2}ˆi` case shows. Measured on the 36
non-TeX PDFs of the library with the bench's dry mode in a loop.

### Code structure at the end

- Backend: `routers/translate.py` (translate, brief, kept translations,
  engines), `gamma/translate.py` (batch, cache, segments, re-asks),
  `gamma/translate_store.py` (the tables), `translate_context.py`,
  `translate_engines.py`. `routers/ai.py` is 500 lines shorter and the
  repair ladder is gone.
- Frontend: `pdf/translateJob.js` (the queue, tested in Node against a fake
  transport), `pdf/TransOverlay.jsx` (`TransPara`, `TransMath`,
  `TransPending` out of `PdfViewer.jsx`), `app/translateClient.js`
  (`translateChunk`, the brief call, the kept-translation reads, the pill
  and background-task wiring out of `App.jsx`), `pdf/translatePlan.js`,
  `pdf/pdfTranslate.js`, `pdf/pdfMath.js`, `pdf/pdfFonts.js` as they are.
- Docs: `dev/translation.md` rewritten around segments, the store and the
  brief call; `dev/api.md`, `dev/settings.md` and the user guide's
  translation lines; this note's "As built".

### Order and size

0 and 1 first (half a day each; 1 is a move). 2 and 3 next (two days
together; they remove more code than they add and end the failures). 4 is
the largest (three days: tables, routes, the viewer's seeding, Settings,
cleanup, tests) and the one that makes a paper's second reading free. 5 is
two days and ends the wait at the first click. 6 and 7 follow as they fit;
6's job module is the only part that touches the viewer's structure.

## What stays as it is

Segmentation stays in the client on pdf.js. Segmenting on the server with
PyMuPDF (which gives every span's font name and flags without an operator
list) was considered for a server-side document job: it would mean two
extractors whose blocks disagree, an overlay that must still match pdf.js's
text layer, and redoing the tuning of the segmenter that was reviewed
against 104 papers. The planner, the tokens and the crops, the streamed
partials, the selection translator and the machine-translation services are
unchanged; a service's replies are kept in the same store under its
`engine:<id>` key.

## As built

Built in October 2026 on the branch `translation-improve-2`; the mechanics
are in [dev/translation.md](../dev/translation.md). Where it differs from
the plan above:

- **The live baseline** (step 0) ran first: `gpt-5.6-luna` through the
  OpenAI API, the four papers, pages 1–6, three requests in parallel, the
  code before this work. 14 of 68 requests failed, every one a connect
  timeout to the provider (`WinError 10060`, 21 s per attempt on Windows)
  that nothing retried; 34 paragraphs stayed in English, the first text
  took 57 s and the run 15.6 minutes. That added two items the plan did
  not have (below: retries, and failed chunks that no longer stop the job).
- **Kept in `data.db`, not `pages.db`, and no migration.** The tables are
  derived data, like the PDF text index beside them: made on first use,
  dropped with their document by `pdf_index.purge_unused` (not `upload_gc`),
  and never the reason a translation fails.
- **One row per paragraph, not a blob per page.** `pdf_translations` is
  keyed by (document, language, model, the source text's hash: 32 hex
  digits, not 16), with no compression and no page column, so the server
  reads exactly the paragraphs a request sends. A row says whether it was
  made with the brief, and such a row is never replaced by one made
  without. At most 20,000 rows per document, language and model.
- **No read route and no seeding.** `GET /api/translations/{doc_id}` was
  not built: the chunk requests read the kept rows on the server where
  memory misses, and a fully kept chunk answers `cached` with no model
  call. A kept paper therefore still needs a click and an AI connection;
  it costs no tokens. The routes built are `GET /api/translations/stats`,
  `DELETE /api/translations` and `DELETE /api/translations/{doc_id}`. A
  forget and a clear also make the server's memory of the documents new
  (a generation in their memory keys), so the next job translates afresh.
- **Only the brief request makes the brief.** `wait_brief` replaced
  `build_brief`: a chunk waits for a brief being made, or goes without,
  and never makes one. The job's later chunks wait until the brief request
  is under way (the gate), so they find the brief being made instead of
  racing it. The brief is 20–30 terms and 10 symbols with no summary, as
  planned, but its cap is 3,000 tokens (8,000 more at an effort the user
  picked) and its timeout 90 s, at the translation's own speed. A failure
  is remembered for 30 s instead of two minutes.
- **The brief request answers** `{"stage": "brief"}` and then
  `{ready, error?}`, not `{ready, waited}`.
- **The door does not wait.** `check_call_slot` refuses at once with a 429
  when the account's slots are all in use; `_TRANSLATE_SLOT_WAIT` (120 s)
  is how long a request waits inside its stream for its one slot, the
  chunk's and the brief's alike.
- **The re-ask is one call.** Every segment the reply got wrong goes again
  in one call, numbered afresh, under the request's one call slot. Tags are
  read with spaces, a capital S and full-width brackets, not `[N]`.
- **The code sits in other modules than the plan's map.**
  `gamma/translate.py` holds the memory cache, `Kept` and `TranslateBatch`;
  the segment wire (`user_turn`, `segments`, `partial_segments`) is in
  `translate_context.py`, and the re-ask is `finish` in
  `routers/translate.py`. On the client, `app/translateClient.js` holds the
  chunk and brief requests, forgetting a document and the hook that follows
  the jobs; `app/translateFollow.js` says what the app shows of them (the
  task rows, the reading pill, the end message, the model hint). Nothing
  reads kept translations on the client.
- **Retries were added** after the baseline's connect timeouts: the chunk,
  the re-ask and the brief are tried twice more when they fail before any
  of the reply came ([dev/ai.md](../dev/ai.md) "Calls open at once").
- **A failed chunk no longer stops the job.** Its paragraphs stay in the
  original and the job goes on. It stops only after three chunks fail in a
  row, or at once on a refusal every later request would meet too (the
  shared allowance spent, 401, 403, 503). The end-of-job message says when
  the connection to the provider was the cause.
- **The job pins the viewer's pdf.js document** in its cache instead of
  opening one of its own, and the state that outlives the viewer is
  `pdf/translateState.js`. The model hint is said when the session's first
  job's median request took over 20 s, not by guessing the model's size.
- **The formula rules** differ from the planned table entries
  ([dev/translation.md](../dev/translation.md) "Formulas"): Elsevier's
  "AdvP" fonts are told apart by their name's hex digits, not taken as one
  prefix; U+02C6–02DC is not a math character, a modifier letter joins
  the formula it is glued to instead; letters set tight inside a formula
  and the "h", "i", "j" an AdvP font writes for its delimiters join it,
  which the plan did not have; the author-line rule reads names and
  affiliation marks, not capitals. The bench's new dry count, `glued`, was
  measured on the four papers, not on the 36 non-TeX PDFs.

Dry numbers before and after the formula rules (the bench's dry mode,
pages 1–6, both runs counted with the bench's current metrics):

| Paper | Units | Formula tokens | Math characters sent as text | Letters glued to a token |
|---|---|---|---|---|
| Two-column, Computer Modern | 93 → 91 | 168 → 149 | 17 → 17 | 8 → 5 |
| Elsevier (AdvOT text, AdvP symbol fonts) | 202 → 192 | 291 → 236 | 2 → 2 | 117 → 17 |
| Nature-style (Helvetica, MathematicalPi) | 113 → 113 | 323 → 318 | 2 → 2 | 27 → 21 |
| MathTime + Palatino | 61 → 60 | 48 → 46 | 6 → 0 | 6 → 2 |
| All four | 469 → 456 | 830 → 749 | 27 → 21 | 158 → 45 |

Fewer units and tokens is the rules working: 13 more blocks stay original,
author lines among them, and pieces of one formula that went as two tokens
go as one.

Live numbers, the baseline against the branch: `gpt-5.6-luna` through the
OpenAI API, the same four papers, pages 1–6, 68 requests, three in
parallel, Chinese. The bench sends what the viewer sends: the brief asked
for as the job starts, the first chunk not waiting for it, the others held
until the brief request is under way, and the chunks that came back
without the brief asked once more at the end. "Streamed" reads each
chunk's NDJSON as the viewer does, so its first text is the first partial
line.

| Measure | Before | After | After, streamed |
|---|---|---|---|
| Requests failed | 14 | 0 | 0 |
| Paragraphs left in English (`untranslated`) | 34 | 0 | 2 |
| Paragraphs asked again (`redone`) | 83 | 5 | 8 |
| Chunks asked again for the brief (`askedAgain`) | — | 4 | 4 |
| First text after the click (`firstMs`) | 57.0 s | 5.0 s | 1.3 s |
| The brief ready (`briefMs`) | — | 7.3 s | 7.7 s |
| Median request | 29.0 s | 6.2 s | 5.4 s |
| Slowest tenth of requests | 63.9 s | 11.4 s | 10.1 s |
| Whole run | 15.6 min | 2.7 min | 2.6 min |
| Renderings per frequent term (`--judge`, 1.0 is one each) | 2.27 | 1.33 | not run |
| Input / output tokens | 109k / 28k | 118k / 35k | 116k / 35k |

Part of the before column's time is the connect timeouts, which the
after runs did not meet. Two earlier runs on the branch, on a network that
answered, came out alike (no failures, 0.8–3.5 s to the first text, 2.3–2.4
minutes, 1.33 and 1.67 renderings), so the gain is the wire and the brief,
not luck with the network. The judge is one model call over the
paragraphs and moves by a few tenths between runs. The two paragraphs the
streamed run left in English came back broken twice (named in `failed`,
offered by Try again). The tokens grew by the four chunks asked again and
the paragraphs that came back translated instead of failing. Fewer of them
came from the prompt cache (12–24k against 63k); the shorter brief leaving
more prompts under the provider's 1,024-token minimum would explain it, but
that was not measured. A paper's second reading was not benched: the kept
rows answer it with no model call (`backend/tests/test_ai_translate_kept.py`).

Declined or not built: the read route and seeding the viewer from it, the
share view's read of kept translations, compressing the rows, a bigger
chunk (still 10 units / 2,500 characters), a shorter patience for a busy
account (still two minutes), and multi-letter italic names (`exp` in
Times-Italic is still a word).
