# AI context for long papers

How the AI chat gets a page's content, why that used to fail on long papers,
and the measurements behind the current design. Code: `gamma/ai_context.py`
(context assembly), `gamma/ai_tools.py` (agent tools), `gamma/pdf_text.py`
(extraction), `gamma/pdf_index.py` + `gamma/block_index.py` (the PDF and notes indexes;
`gamma/routers/search.py` extracts PDFs into the former).

## What a page contributes

Context is framed as *pages from the user's knowledge base*
(`ai_context.CONTEXT_INTRO` precedes it in the user turn). Each page section
(`page_report_section`) is: `### title`, a properties line (folders, labels,
cached metadata, web source, attachment), and the user's notes tree with
highlights; a page that carries a PDF adds the document's text — for the
chat, the labelled head excerpt below; for `read_page`, a `pdf_chars`
window. A page without an attachment is its notes, always included;
`include_notes` only decides whether PDF pages also show theirs. In the
notes, and in the focus outline of the cursor's and the attached blocks
(`notes_focus_section`), a text box ([text_boxes.md](text_boxes.md)) and a
picture ([pictures.md](pictures.md)) are notes, never area highlights. A
label before the text says where each is, by one rule for both
(`placed_label`): "(text box on p. N)", "(picture on a page of paper)",
"(text box, not placed on a page)" and so on. The nearest sheet above
wins, and an outline that starts at a block asks `under_sheet` once.

A picture's text is its `![caption](url)` line (`picture_text`,
`picture.markdown_of`), so the model reads the caption and has the URL
`view_image` opens ([ai_tools.md](ai_tools.md#view_image-both-scopes)).
The picture itself is not in the context. A picture with no caption is
listed too, and one whose value names no file reads as its caption.

The head excerpt is the *document part* of the context: it goes on the
oldest user turn and reads the same on every turn of a conversation
(with the PDF switch on, the file takes its place, below),
which is what the providers' prompt caches key on ("Prompt caching" in
[ai.md](ai.md)). What a message selects is the *message part*: windows
around the selected passages (placed by page and section, with a picture
of a formula — "Selected PDF passages" in [ai.md](ai.md)) go in front of
that question alone, `SELECTION_WINDOW_CHARS` (10,000) per passage within
the page budget and without the head slice the excerpt already gives
(`selection_context(with_head=False)`), and not at all when the excerpt
holds the whole document. Before, a selection swapped the head excerpt for
its windows, so the conversation's opening changed with every selection
and nothing could cache. Everything below is about the PDF part of a page
and applies unchanged.

The passages themselves are quoted in the question. The selection gets the
same single-paper budget as the head excerpt (`context_char_limit`, 60,000
by default), because it is the part of the paper the question is about. One
budget is shared by the PDF passages and the selected note text
(`passage_budget`, `fair_shares`), and the attached blocks share another of
the same size. The windows around the passages never take more than it. A
passage that doesn't fit is cut with a line saying so. Its chip in the
composer says beforehand what it keeps, and the reply's `{"shortened"}`
pill says what went ("Selected PDF passages" in [ai.md](ai.md)). None of
it touches the document part or the system prompt, so the cached head
stays as it is. When the model's window can't hold the message, the
pre-flight fit leaves out the earlier turns first and only then halves this
budget ("Fitting the window" in [ai.md](ai.md)).

### With the PDF switch on

The composer's PDF switch (`attach_pdf`) sends the page's PDF file
with every message while it is on. The file always goes in the same place:
`gather_inputs` returns it as `document_pdfs` beside the document part, and
`build_messages(files=)` puts it on the turn that carries that part, as the
turn's `files` (`ai_protocols.base.turn_files`). That is the oldest kept
user turn, or the only turn of a first message. Each wire writes a turn's
files first, then its pictures, then the text. So a request opens the same
way as the one before it: system prompt, tools, then that turn with its
file and text. The provider's prompt cache serves this head from the
second message on. The history the client sends never holds a file, so the
server adds it again on each request.

What each message carries for that page:

- **The page section:** the title, the properties line and, with
  `include_notes`, the notes. There is no document text, because the
  provider reads the file.
- **Coverage:** `native`, with no pages counted. The agent prompt says the
  PDF is attached as a file to the conversation's first message, and that
  `read_page` still gives its extracted text page by page
  (`ai_tools.coverage_lines`).
- **Document map (tools on):** from page 1.
- **A selected passage:** labelled with the viewer's page only, with no
  text window and no crop.

The file can't go natively in three cases, and the page then gets the head
excerpt instead, at the same place on the oldest turn:

- **The switch is off.** Turning it on or off mid-conversation changes the
  oldest turn, so the next request misses the cache past the system prompt
  and the tools once.
- **The provider rejects the file** with a 4xx (other than 401, 403 or
  429). That same message is retried as text, and its coverage says
  `native_requested` without `native`.
- **The library PDFs pass 20 MB of base64** together, or one file passes
  15 MB on its own. A document that doesn't fit goes as the excerpt, and
  its coverage says `native_skipped: "too_large"` (`"missing"` when there
  is no file to send).

A page without a PDF is its notes and never asks for a file, so its
coverage has `native_requested` false whatever the switch says.

Two other rules cover the switch:

- **When it is on.** It starts on in a new conversation and stays as the
  user leaves it. Each message records the documents it carried
  (`pdfDocs`). When a conversation is loaded, `showLoaded` turns the switch
  on only if the latest message carried this document. It defaults on only
  for a model marked `native_pdf`.
- **Uploaded PDFs** (the composer's [+]) go with their own message only, on
  the newest turn after that turn's own files. They are not library pages,
  so later messages carry neither the file nor an excerpt of it.

On OpenAI's side ([File inputs](https://developers.openai.com/api/docs/guides/file-inputs)),
a PDF file part is read twice: the text is extracted, and on a vision
model every page is also rendered as a picture. Both go into the context
and both are billed. Gamma leaves `detail` out. The default `auto` reads
pages at `high` on GPT-5.6 and later, and at `low` on earlier models. The
limits are under 50 MB per file and 50 MB per request. OpenAI's prompt
cache covers documents like any other part of the prefix
([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)),
so a cached head bills the file at the cached-input rate.

## The problem

A page chat injects the first `context_char_limit` chars of the PDF's
extracted text (default 60,000 since 2026-08-28 — Settings → AI → Chat →
"Single paper"; the eval below ran at the old 8,000). A 50-page paper is
~280,000 chars, so at 8k the model saw under 3% of it — and nothing used to
tell it that. Asked for a detail deeper in
the paper, it answered from its memory of similar papers: confident, specific,
and wrong (a camera model that isn't in the paper, n=70 where the paper says
n=53, fidelities off by tenths of a percent). The page-scope agent tools
(`read_page`, `search_library` — then `search_pdfs`) mostly fix this, but
four things still lost facts:
the model didn't know the excerpt was an excerpt, it guessed page numbers, an
over-specific search query returned zero hits (read as "the paper is silent"),
and every page past 400 was silently invisible.

## Measurements

Ground-truth detail questions (string-graded facts, each buried well past the
8k head) against the real `/api/ai/chat` on an isolated copy of live data,
model `gpt-5.6-terra`, page-scope agent with read+search. "Traps" ask for
plausible facts the paper does *not* contain; the right answer is "not in this
paper".

| config | paper 164k/28pp | review 282k/51pp | book 855k/484pp | normal questions |
|---|---|---|---|---|
| tools off, 8k head | 46% | 48% | — | 100% |
| agent, before fixes | 94% (3.9 calls/q) | 90% (4.1) | 88% (6.0) | 100% |
| + grounding prompt | 100% (3.8) | 94% (4.9) | — | 100% |
| + excerpt label, doc map, search fallback | 100% (3.5) | 97% (4.1) | 92% (9.9) | — |
| + page cap lifted | — | — | 96% (3.3) | — |
| **all shipped (re-run)** | **100%** (3.8) | **94%** (4.6) | **96%** (3.3) | **100%** |

Also tried: 5× larger head context (40k). +3 points for 5× the tokens on every
message, and it doesn't stop fabrication — the tools are the better lever.

## What shipped, and why

- **Grounding prompt** (`_SYSTEM_PROMPT` in `routers/ai.py`, plus the
  mechanical tool-guidance lines `agent_system()` appends in `ai_tools.py`):
  anything stated as being *in these pages or their documents* must come from
  text actually read; look it up first, say "not in their pages" otherwise —
  and say whether a fact comes from a PDF (with its page number) or from the
  user's notes. Scoped to claims about the pages — general background stays
  answerable, which is what keeps the "normal questions" column at 100% (an
  unscoped version refused to explain what a transversal gate is). With tools
  off, this converts fabricated answers into honest "the excerpt doesn't say"
  (recall drops to 17% — those were memory, not reading).
- **Excerpt label** (`head_context` / `extract_pdf_context`): the injected
  head is prefixed with `[EXCERPT — the first 60,000 characters of this
  51-page PDF: PDF pages 1–9, page 9 cut short. Pages 10–51 are NOT shown
  below …]` whenever the document didn't fit; `CONTEXT_INTRO` likewise
  says a page's document text "is often an excerpt (see its label)".
  Unlabelled, "here is the text" reads as the whole paper. The same
  coverage reaches the agent prompt (`ai_tools.coverage_lines`: which
  pages are in context, read from the cut-short page on) and `read_page`,
  which never sends a page the excerpt shows in full — before, the prompt
  told the model to look everything up "even if you think you know it",
  and it re-read page 1 of a paper whose first nine pages it had.
- **Page labels** (`extract_text(..., label_pages=True)` in `pdf_text.py`):
  every non-empty physical page is prefixed `[PDF page N]`, counted from
  `start_page` with blank pages included; a `read_page` continuation that
  starts mid-page repeats it as `[PDF page N; continued]` outside the offset
  window (`pdf_excerpt`), and each context section carries
  `Gamma page ID: <id>`. Both feed the clickable citations
  ([pdf_citations.md](pdf_citations.md)).
- **Document map** (`document_map`): for page/folder agent chats, a
  `MAP_BUDGET` (2.4k-char) outline — one line per PDF page (sampled for big
  documents, the step rounded up so the budget holds), taken from the FTS
  index so it costs a query, not a re-parse — starting at the page the
  excerpt cut short (`from_page`; no map when the excerpt holds the whole
  document). Each attached paper gets its own map labelled with its Gamma
  page ID. The model jumps to the right page instead of guessing; on one
  question this cut 6 tool calls to 1.
- **Search relaxation** (`_run_search_library`): the FTS query ANDs every
  term, and agents write 6–9-word natural queries — one word the page doesn't
  use meant zero hits and a wrong "the paper doesn't discuss this". A miss now
  retries with only the longest words and labels the result as approximate; a
  true miss says explicitly "do not answer from your own knowledge". (The
  same relaxation covers the notes index the tool searches since Stage 2.)
- **Page cap raised** (`pdf_text.MAX_PAGES`, 400 → 5000): the old cap made a
  484-page book end at page 400 *everywhere* — not in the search index, not
  reachable by `read_page`, indistinguishable from the document ending. Worst
  case was a search hit on p.410 the agent then couldn't read: 32 tool calls of
  thrashing. The cap survives only as a runaway guard and logs when it bites.
  (`textnorm.INDEX_VERSION` bumped so old indexes rebuild lazily.)
- **Extraction lock** (`pdf_text._lock`): PyMuPDF's documentation says it is
  not thread-safe (running it on several threads "may cause incorrect
  behaviour or even crash Python itself"; pdfium, which read here before,
  showed it: two overlapping extractions both died with "Failed to load
  page", even on different files). Sync endpoints run in FastAPI's
  threadpool and the search indexer runs in a background thread, so a chat
  could collide with its own indexing, get the failure sentinel as context,
  and answer blind. Every PyMuPDF call is serialized behind one lock
  (`_MuPdfLock`), and every document is closed and every page dropped
  explicitly inside it: PyMuPDF frees by reference count, so nothing is
  finalized later on another thread (pypdfium2 closed its objects from
  weakref finalizers the cyclic GC ran on whatever thread allocated next,
  and needed them netted through the lock; that net,
  `_serialize_finalizers`, is gone). The walks over every page
  take the lock per page, not per document, so a chat's extraction runs
  between the pages of a book being indexed instead of after it (measured:
  `/pdf-text-status` during a 5,000-page reindex, 5.6 s → 77 ms). The lock
  is handed over in turn: a bare `RLock` is unfair, and on Linux the walk
  took it back after each page before a waiting thread woke. So a fresh
  taker queues at a second lock (the "door") and holds it while it waits,
  and the walk's next page lines up behind it. One more fact about PyMuPDF
  shapes the calls under the lock: it holds the GIL inside every call, so a
  page's text or a render stalls the event loop — every request and every
  WebSocket — for those milliseconds (pdfium, called through ctypes,
  released it). That is why every call in `pdf_text` is one page's text,
  size, glyphs or picture, never a pass over a whole document inside the
  library.
- **The page-text cache** (`pdf_text.iter_page_texts`): a chat reads the
  paper on every turn (the head excerpt, a selection's placement over the
  whole document, each `read_page` window from its start page), and a
  MuPDF walk over a long book takes a second (1,041 and 1,052 ms for the
  text of a 484- and a 1,310-page book), under the lock, in the way of
  the viewer's manifest walks and the indexer. A stored PDF never changes
  under its name (a content hash), so the text of a page, once extracted,
  is kept in the process. Per file it holds the pages read so far: a head
  read fills the head, and the next window goes on from there. It keeps at
  most `TEXT_CACHE_FILES` (64) files and `TEXT_CACHE_CHARS` (32 million
  characters) together, least recently read dropped first. The key carries
  the file's size and mtime, so a file rewritten in place (annotations stripped) is read
  again; bytes sources (a PDF in a chat message) and the search indexer's
  walk (`cache=False`, so a library's worth of papers does not push out the
  ones being read) bypass it. `page_count` answers from it too. Tests:
  `tests/test_pdf_text_cache.py`.

## Known limits

- A leading question about a fact that is *not* in the paper ("what fidelity
  does this review report for X?") can still elicit the remembered number,
  now hedged rather than asserted — the model searches, finds nothing, and
  names the value while admitting it didn't find it in the text.
- The map and index only exist once the paper is indexed. Every chat
  request names a page with a PDF (`gather_inputs` → `ensure_indexed`, for
  each PDF page in context) kicks background indexing for an un-indexed or
  stale paper, so the first message on a fresh paper works without
  map/search and the next one has both. (Before, only a search call kicked
  it — a chat that only ever used `read_page`, or ran with tools off, never
  got its paper indexed.)
- With tools off the model sees only the labelled head excerpt; nothing else
  in a plain chat can reach the rest of the paper (unless the PDF switch
  sends the file; a provider that refuses it gets that same excerpt).
  Tools are on by default in every chat kind (`gamma-ai-agent-enabled`,
  reading allowed — [ai.md](ai.md) "Permissions and knobs"), but a chat
  with them switched off, or a reply that never calls one, lives on the head
  budget alone — hence the 60,000 default (a typical ~20-page paper fits
  whole; the multi-paper total is 120,000, `multi_context_char_limit`, split
  evenly across the context pages, note pages included). The budgets are
  characters: 60,000 of English prose is ~15k tokens, of dense notation
  ~20k, of Chinese ~50k
  (`ai_context.estimate_tokens` counts that way for the window fit). The
  truncation is also shown to the user: `head_context` returns the coverage
  (`pages_shown` from `pdf_text.extract_text_pages`, total from
  `page_count`) that the chat streams back as its `context` line and renders
  as a "Model saw pages 1–9 of 22" chip on the reply — with the pages the
  reply's tools read folded in ([ai.md](ai.md)).
- Grading is substring matching on de-markdowned/de-LaTeXed answers; the
  harness lives outside the repo (session scratchpad, `eval/`).
