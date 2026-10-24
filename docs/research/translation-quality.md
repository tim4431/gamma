# Why the translated view reads worse than the paper, and the plan to fix it

The viewer's translated view ([dev/translation.md](../dev/translation.md))
translates well-segmented paragraphs with a capable model and still reads
badly on a real paper: terms change from page to page, sentences break at
column and page boundaries, and every inline formula comes out as the glyph
soup pdf.js extracted. Read from the code in October 2026 (`routers/ai.py`
`_TRANSLATE_PROMPT` and `ai_translate`, `pdf/pdfTranslate.js`,
`pdf/PdfViewer.jsx` `runTransJob` and `TransPara`, `app/App.jsx`
`translateChunk`), with PDFMathTranslate's formula handling and TextFold's
prompt ([textfold.md](textfold.md) "Translation context") as the
comparisons. Nothing was changed and no provider was called. This note
records what the model is given today, why that produces each of the three
faults, and an ordered plan, built as "As built" at its end describes.

## What one translation request carries today

The client segments a page into blocks (`segmentPage`): one block is a
visual paragraph inside one column on one page. The producer in
`runTransJob` cuts the translatable blocks of a page into chunks of at most
6 paragraphs or 1,200 characters, and 1–4 workers send the chunks to
`/api/ai/translate` in parallel, across page boundaries, nearest page first.

One request is a JSON array of those 1–6 strings, plus `lang`, `model` and
`effort`. The system prompt is about ninety words: translate paragraphs from
an academic paper into the language, answer a same-length array, keep inline
math, numbers, `[12]` markers and URLs as written, academic register,
headings as headings. The request carries no document id, no title, no
field, no section heading, no neighbouring paragraph and no shared
terminology. Two chunks of the same page are translated by two workers that
know nothing of each other.

The server cache is keyed on (account, language, bare model name, source
text). The reply is parsed as a same-length array; a miscount goes to the
per-paragraph salvage.

Formulas: `isTranslatable` leaves a block original when fewer than half of
its characters are letters, or it has fewer than four letters, or no two
letters stand together. Everything else goes to the model as pdf.js
extracted it. The run's font (`fontName`, an opaque pdf.js id) is used only
to find paragraph boundaries (`fontShare`); nothing reads the real font name.

## The three faults and their causes

**Terms drift.** "Ansatz", "fidelity", "readout", "we" versus "the authors",
an acronym expanded on page 2 and used bare on page 7: each chunk decides on
its own. With parallel workers the same term is settled several times at
once, and the cache then keeps each decision. TextFold's prompt gives the
title, an outline and a rule to use standard terms the same way throughout;
Gamma's gives the language. This is the fault the user sees as "no context
of the whole paper".

**Sentences break at column and page ends.** A paragraph that runs from
the bottom of the left column into the right column, or over a page break,
is two blocks. Each half goes to the model as "one paragraph" and is
translated as a complete sentence. In a language that reorders the clause
(Chinese, Japanese, German) the first half ends on a dangling object and the
second half starts with a verb that lost its subject. Two-column physics
papers do this on nearly every page, and the whole-document job's
nearest-first page order means the two halves are usually in different
requests anyway.

**Formulas are glyph soup, then "translated".** pdf.js maps math-font glyphs
through the font's ToUnicode table: a Σ from CMEX becomes "P", a subscript
sits inline ("anφn" for a_n φ_n), fraction bars and radicals vanish, Type 3
glyphs come out as private-use characters. The prompt tells the model to
keep "inline math exactly as written", so the best case keeps the soup, the
common case tidies it into something plausible and wrong, and the worst case
drops it. A display equation is left original only when it is symbol-dense
enough for `isTranslatable`; "where x denotes the position and t the time"
on the equation's own line, or an equation with a few words of prose, passes
the test and is mangled. Inside a prose paragraph the translation is one
plain-text string in `TransPara`, so the formula is rendered as text in the
translation's font, shrunk to fit: nothing on the page shows the reader the
formula they would have seen.

Smaller points found on the way:

- `translateEffort` is sent raw. A reasoning model at its default effort
  spends thinking tokens on every chunk; TextFold's bench found 3–9× the time
  for little quality ([textfold.md](textfold.md)). The default for
  translation should be the model's lowest level unless the user picks one.
- The cache key has no room for context. Once the request carries a brief
  or a glossary, the key must hash it in, or an older context-free
  translation of the same paragraph is served forever.
- The machine-translation services (Microsoft, Google, Youdao) translate
  sentence by sentence and can take no paper context; steps 1 and 4 below
  still help them, steps 2, 3 and 5 are the LLM path's only.

## What others do

- **PDFMathTranslate / BabelDOC** (the widely used PDF paper translators):
  formula runs are found by the font name (a regex over
  `CM[^R]|MS.M|XY|MT|BL|RM|EU|LA|RS|LINE|LCIRCLE|TeX-|rsfs|txsy|wasy|stmary|.*Mono|.*Code|.*Ital|.*Sym|.*Math`)
  and by character class, replaced by `{v1}`, `{v2}`… in the text sent to
  the model, and after translation the original glyphs are placed back at
  the token's position, in their original font. The prompt says the tokens
  are formulas and must be kept. Whole paragraphs are the unit; the prompt
  names the field.
- **TextFold** gives the title, the section outline, and a "same term the
  same way" rule, and counts formula and citation markers lost in
  translation to decide on a retry.
- **Immersive Translate and the browser translators** translate a web page
  paragraph by paragraph with the page title in the prompt, and keep
  `<code>` and `notranslate` spans by placeholder.

The two ideas worth taking are the placeholder round trip for formulas and
a once-per-document context that every chunk carries.

## Plan

Each step ships on its own and is measured on the bench of step 0. The
order is by quality gained per effort; step 4 is the largest.

### 0. A bench that reads before and after

A script on the e2e harness ([dev/debugging.md](../dev/debugging.md))
opens three or four fixed papers (a two-column revtex paper with heavy
inline math, a single-column CS paper, a paper with equations that have
words in them), translates fixed pages into Chinese and German through the
real client engine, and writes the source and translated paragraphs as JSON
plus one HTML page that shows them side by side. Four numbers per run:

- fragments: blocks sent whose source ends without sentence punctuation and
  is the last block of its column;
- retention: `[12]` markers, URLs and (after step 4) `{vN}` tokens present
  in the source and missing or duplicated in the translation;
- consistency: for the fifteen most frequent multi-word source terms, how
  many distinct target strings appear;
- time and tokens per page, from `ai_usage`.

Without this, every later step is judged by reading one page.

### 1. Join a paragraph split by a column or page break (client)

In the producer, before chunking: a block is a *continuation candidate*
when it is the last block of its column (no block below it in the same
x-range on the page), its last line ends with no sentence punctuation (or
with a hyphen), and the next block in reading order (the top of the next
column, or the first block of the next page) starts with a lowercase letter
or a continuation word, has the same dominant size and no first-line
indent. The two blocks are sent as **one** text with a `⟨BR⟩` marker at the
join; the prompt asks for exactly one `⟨BR⟩` in the translation, at the
sentence boundary nearest the original split. The viewer puts the part
before the marker into the first block's box and the rest into the second;
`TransPara` already shrinks to fit, so an uneven split only changes the font
size. If the marker is missing from the reply, the translation is split by
the source halves' character share at the nearest punctuation. A page-break
join needs the next page segmented (`ensureParas` reads text only, no
render), which the producer can do ahead of the chunking of the current
page.

This needs no server change except accepting the marker in the prompt, and
a unit test file for `pdfTranslate.js` (there is none today).

### 2. A paper brief and glossary, made once, sent with every chunk (server + client)

The translate request gains `doc_id` (the viewer has it: it calls
`pdf-text-status?doc_id=`). On the first request for a (account, doc, lang,
model) the server builds a **brief**: from the head of the document's text
(`pdf_excerpt`, the first ~8,000 characters, the same extraction the chat
uses) one model call returns the field, a two-sentence summary, the
notation that matters ("ρ is the density matrix; L the system size"), and
a glossary of 20–40 recurring terms with the chosen target rendering and
which acronyms and proper nouns stay as written. The brief is kept in the
same in-memory LRU style as the paragraph cache (it is one or two
kilobytes; persisting it per document can come later). A PDF the viewer
shows by its source URL alone, with no `doc_id`, sends the text of its first
two pages as `head` instead, and the server builds the brief from that.

Every chunk call then has a system prompt of the fixed rules followed by the
brief. On Anthropic the system block already gets `cache_control`
(`_with_breakpoints`); on OpenAI a `cache_key` hashed from (account, doc,
lang) goes through `open_ai` the way the chat's `_cache_key` does, so the
brief is read from the prefix cache on every chunk after the first. The
paragraph cache key gains a hash of the brief. A chunk sent before the
brief exists waits for it (one call, a few seconds) rather than going
without: the first page is the one that sets the terms.

Cost: one extra call per document and about 1–2k mostly cached tokens per
chunk. This is the step that addresses "the whole paper's context" and
should go first after the bench.

### 3. Neighbours in the chunk, and bigger chunks

The user message becomes `{"before": "…", "texts": [...], "after": "…"}`:
the paragraph before the chunk and the one after it, as source text, marked
context only. The prompt translates the array alone. Each text also carries
the section it is under: the nearest preceding block whose size is larger
than the body's or that is a short line of its own (the segmenter already
has sizes and line counts), sent as `section` next to the text. The chunk
cap rises from 6 paragraphs / 1,200 characters to about 10 / 2,500, so a
column is usually one chunk and chunk edges fall at column edges; the
streamed partials keep the first paint early. Feeding back the previous
chunk's *translation* was considered and dropped: with parallel workers it
is often not there yet, and the brief gives the consistency it was for.

### 4. Formulas: placeholders in, the original glyphs out

**Detect.** `pdfTranslate.js` scores each run as math from three signals:
the real font name, read through `page.commonObjs.get(fontName)?.name` once
the page's operator list exists (the raster builds it when it paints the
page; to be verified against pdf.js 4.4, whose `TextContent.styles` holds
only the generic fallback family) matched against the PDFMathTranslate
regex above; a baseline offset against the line's (sub- and superscripts);
and the character class (Greek, mathematical operators, private-use
U+E000–F8FF, combining marks). Adjacent math runs merge into one **math
span** with its box. A block whose math spans cover most of its width is a
display equation and stays original, replacing today's letter-ratio guess,
which remains the fallback when no font names can be read.

**Send.** Each math span in a prose block becomes `{v1}`, `{v2}`… in the
text sent, in order; `[12]` markers and URLs go the same way, so the model
can no longer break them and they cost no tokens. The prompt says the
tokens are formulas and must each appear exactly once, where the target
language's grammar puts them. The server checks every reply paragraph for
its tokens; a paragraph that lost or doubled one goes to the existing
per-paragraph salvage, then verbatim. The services (Microsoft, Google,
Youdao) get the same tokens; they survive machine translation in practice.

**Show.** `TransPara` renders the translation as text nodes with, at each
token, an inline element showing the original formula: a crop of the page's
bitmap at the span's box, drawn from the raster's canvas at the current
scale and device pixel ratio, with its height tied to the translated text's
font size and its vertical alignment from the span's baseline offset. The
reader sees the formula as typeset in the paper, inside the translated
sentence. Its `alt`/copy text is the span's extracted string, so a copied
translation carries the extracted text rather than `{v1}`. The selection
translator's panel shows the tokens as the extracted text (no page geometry
there).

Considered and not taken for the page: asking the model to rewrite the soup
as LaTeX and rendering it with KaTeX (already in the bundle). It doubles the
output, hallucinates on anything the extraction lost, and the glyph crop is
exact. A "formula to LaTeX" button on the selection translator could use it
later.

### 5. Prompt and settings

- The fixed rules name the target language's conventions: full-width
  punctuation and no spaces around CJK, acronyms and proper nouns kept with
  the target term on first use, "Fig. 1"/"Eq. (3)"/"Table 2" rendered one
  way throughout, units and numbers unchanged, author names and citations
  untouched, the glossary followed.
- Effort: when the user has not picked one, send the model's lowest level
  for translation; keep the preference as the override.
- Settings → Translation says that a fast non-reasoning model is the right
  pick, and later offers a per-paper "terms" box prefilled from the brief's
  glossary for the reader who disagrees with a term.

### Order and size

Step 0 and step 1 first (small, client only, measurable). Step 2 next: it
is the one the user asked for and the biggest change in what the reader
sees. Steps 3 and 5 are small once 2 exists. Step 4 is the largest, mostly
client work in `pdfTranslate.js` and `TransPara`, and can be split: detection
and placeholders first (the text already improves because the model stops
touching formulas), the glyph crop second.

## What stays as it is

Geometry never leaves the client; the brief uses text the server already
extracts. The chunk queue, the streamed partials, the per-paragraph salvage
and the show/hide states are unchanged. The in-memory-only cache policy
holds; the brief is kept the same way.

## As built

Built in October 2026; the mechanics are in
[dev/translation.md](../dev/translation.md). Where it differs from the plan
above:

- **The request names the page, not the document.** `page_id`, checked
  against the request's workspace, as the chat does: nothing new may key on
  a bare `doc_id` (`routers/ai.py`). The brief is read from the page's
  stored PDF without downloading it; a page shown from its source URL sends
  the document's opening text (`head`) instead.
- **Anthropic's adapter is unchanged.** It marks the system block and the
  user turn, as for the chat. Translation reads the cached system prompt and
  pays a small write premium on each chunk; marking the system block alone
  would need a new parameter on every wire for a gain only on models whose
  minimum cacheable prefix falls between the two.
- **Step 1 needed a new segmenter, not only a join.** The bench showed the
  old one merging lines across the gutter wherever the two columns'
  baselines differed by a few points, dropping raised superscripts out of
  their paragraph, reading an arXiv stamp in the margin as a line across the
  column, and splitting a paragraph at a wrapped URL. Lines now group by
  their glyphs' middle, a short script folds back into the line it touches,
  a soft gap is a cut only where a whitespace river runs through the lines
  around it, and rotated text is left out. A first version was reviewed
  against 104 local papers; its regressions (whole lines folded as scripts,
  pdf.js's 0/0 font metrics, gutters missed beside a short line) are fixed.
- **Joins needed a reading order and a model of the page.** Regions cut
  where text fills both halves of the page on both sides, furniture
  (captions, running heads, page numbers) out of the flow, display equations
  in it, and a stricter test for where a paragraph goes on (prose on both
  sides, the page's paragraph style, a one-line continuation that ends a
  sentence or introduces an equation).
- **Citation markers and URLs are tokens drawn as text**, formulas tokens
  drawn as crops. A formula the model moves across a join is drawn from its
  own block's page.
- **Effort**: the lowest level is chosen on the server from the model's
  listed levels; on the Anthropic wire that is the first level after `none`.

Measured with the bench's dry mode (what is sent; no model called), pages
1–4 of four local papers and the fixture, before and after:

| Paper | Requests | Texts ending mid-sentence | Math characters sent as text | Joined paragraphs | Math characters in the context |
|---|---|---|---|---|---|
| Fixture (2 pages) | 7 → 3 | 9 → 3 | 32 → 5 | 0 → 3 | 0 |
| Two-column revtex, CM fonts | 19 → 8 | 36 → 14 | 167 → 17 | 0 → 3 | 11 |
| Two-column pdfTeX, CM fonts | 23 → 12 | 17 → 3 | 143 → 2 | 0 → 4 | 41 |
| Single-column CS | 14 → 8 | 5 → 3 | 3 → 0 | 0 → 1 | 0 |
| Two-column APS, MathTime fonts | 18 → 12 | 12 → 7 | 84 → 0 | 0 → 1 | 10 |

The last column is new with the context: it counts the math characters in
the text around each request (the text before and after it and its
section heading). That text goes as extracted, formulas and all, for the
model to read, never to translate.

Over the first twelve pages of 104 local papers (1,078 pages) the planner
makes 389 joins; a sample read by hand was right, with a few wrong ones
left: a figure's axis label after a sentence that breaks off ("…will be" /
"Time, T", the next block being tick labels that pass for an equation),
and a new paragraph after "…that" on a page that indents.

Still open: the live numbers (time, tokens, paragraphs redone for a lost
token, term consistency with `--judge`) need a provider key and were not
run; the brief's effect on term consistency is therefore unmeasured.
Cross-page crops draw only while the other page holds pixels. The
selection translator sends the text layer's text, without tokens.
