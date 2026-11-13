# AI context for long papers

How the AI chat gets a page's content, why that used to fail on long papers,
and the measurements behind the current design. Code: `gamma/ai_context.py`
(context assembly), `gamma/ai_tools.py` (agent tools), `gamma/pdf_text.py`
(a PDF's extraction), `gamma/documents/text.py` (a text file's lines and
outline), `gamma/search/` + `gamma/block_index.py` (the document text and
notes indexes; `gamma/search/indexer.py` extracts documents into the former).

## What a page contributes

Context is framed as *pages from the user's knowledge base*
(`ai_context.CONTEXT_INTRO` precedes it in the user turn). Each page section
(`page_report_section`) is: `### title`, a properties line (a work folder's
file's `path:` in it and its `work folder:`, folders, labels, cached
metadata, web source, `document: <format> "<name>"` for a document of any
format), the user's notes tree with highlights, and the page's open comment
threads (below); a page that carries a PDF adds the document's text — for
the chat, the labelled head excerpt below; for `read_page`, a `pdf_chars`
window — and a page with a text file its numbered lines ("A text file"
below). A document of another format is one line saying what it is and
what can be done with it here (`document_line`): a picture's "no tool
shows a page's picture here; open the page to see it", a recording's
format and size, a file of no format the model reads "cannot be read
here", a work folder's file listed without its bytes "nothing to read
here". A page without a document is its notes, always included;
`include_notes` only decides whether a PDF's or a text file's page also
shows theirs. In the
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

A selection in a text file's code view is a passage too, with its lines:
the client sends `{text, page: 0, line, end}` and `request_selections`
keeps `lines: [line, end]` on an item of page 0 (whole numbers, `1 <= line
<= end <= MAX_SELECTION_LINE`, `end` defaulting to `line`). The question
heads it `Selected passage (lines 118–120):` (`_passage_heading`; one
sentence, "from the page's document", for a PDF and a text file alike).
Since the page's section shows only a text file's head and the outline of
the rest, `text_selection_context` reads a window around each passage for
this message: `SELECTION_WINDOW_LINES` (20) lines before and after, numbered
(`text_window`, which reads the store, then the linked path, and keeps a
secrets file out), the windows sharing the same budget as a PDF's (at most
`SELECTION_WINDOW_CHARS` a passage, within the passage budget), each headed
`[lines a–b of <path>]`, and none when the head already shows the whole
file; a share too small for the lines before a passage starts at the
passage. The coverage entry's `selection.passages` holds one `{"lines",
"window": [a, b]}` a passage, which labels the question.

A range of a text file (a highlight with `text_position`) is told with its
lines and how the last change of the file left it, beside its quote:
`- Highlighted: "…" (lines 118–120)`, `(line 47; not found in the current
file)`, `(line 23; placed roughly)`, `(line 31; lines of an earlier version
of the file)` (measured on another version, the run not done) in the
page's section, and read_block's `(highlight: "…"; line 52)`
(`range_words`); the model reads on with `read_page(from_line=)`. A thread
on one is named by the lines its block holds now and their state
(`threads_section`, `anchors.following` and `place_words`: `line 52, “…”`,
`line 47 (not found in the current version), “…”`, `line 31 of an earlier
version, “…”`). On a secrets file a range's quote is never told: `-
Highlighted (line 2; its text is kept from the model: a secrets file)`,
read_block's `(highlight on line 2; …)`, a thread `on line 2`, and a
passage selected in it is quoted as that sentence (its located entry
`secret`, `final_prompt`).

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

### A text file

A page whose document is a text file (`format: "text"`: a work folder's
`.tex`, code, data, a log, or a library document of that format) gives its
lines, read from the file itself (`storage.find_upload_file`, never
`pdf_path`) and never from the search index, so the first chat on a fresh
file needs no indexing. Each line goes with its number in `cat -n`'s shape
(`documents.text.numbered`: the number right-aligned in six columns, a
tab), the number being what a line link and `read_page(from_line=)` take:

```text
Document: text file "sections/method.tex" (LaTeX; 2,310 lines, 94,120 bytes; read where it is)
[Lines 1–400 of 2,310. The number and tab before each line are its line number, not the text.
 Lines 401–2,310 are NOT shown below.]               ← with tools: "…: read them with read_page(page_id, from_line=401)."
     1	\section{Method}
   …
[OUTLINE — where the rest is, by line]               ← with tools: "…; pick the lines to read with read_page(from_line=N)"
  l.412  \subsection{Noise model} [sec:noise]
```

- **Whole, or head and outline** (`text_head`). The whole file goes when it
  fits the page's text budget (`context_char_limit`, or the multi-page
  share), as `[The whole file, N lines. …]`. Else its first lines go within
  the budget less the outline's (`MAP_BUDGET`, 2,400 characters, half the
  budget at most), cut at a line, and the outline of the rest
  (`documents.text.outline`: a LaTeX file's sections, figures with their
  captions and inputs, a program's definitions, a data file's keys …) from
  the first line not shown. The outline goes to every chat, since it tells a
  plain chat where things are too; the calls to read on are written only for
  a chat with tools (`agent_scope` page or folder, the PDF map's gate), so a
  plain chat is never told to call a tool it lacks. A line is cut at
  `VIEW_LINE_CHARS` (10,000) with `…[line cut]`; a file is read and outlined
  up to `MAX_TEXT_LINES` (50,000) lines or `MAX_TEXT_CHARS` (2,000,000)
  characters.
- **The cache.** The head and its outline are the document part, the same
  text on every turn, so they are kept per file version: `text_head` keeps
  the pair by `(ws, hash, ext, language, budget)` for the newest
  `TEXT_HEAD_FILES` (16) files within `TEXT_HEAD_CHARS` (4 million
  characters), behind a lock, both read in one pass of the file. The entry
  keeps the numbered lines and the outline's entries (`TextHead`); the
  label, the outline block and the calls to read on are written each turn
  (`text_document`), so one entry serves a plain chat and a chat with
  tools. A file's
  hash changes with its bytes, so a kept head is right by construction, and
  a file the vault replaced is read once again and misses the provider's
  prompt cache once, as a new PDF does. A file not found is not kept.
- **Not shown.** A secrets file (`documents.text.is_secret`: `.env`,
  `.netrc`, `id_rsa`, a `.pem` or `.key` …) gives one sentence, "its text is
  kept from the model: a secrets file", here, in `read_page` and under a
  chip, and so does a range or a selection of its lines (above); the viewer
  still shows it. A file whose bytes are no text (a NUL, or
  too many bytes that are not UTF-8) says "(this file is not text)", one not
  on this server "(the file's text is not available on this server)".
- **Coverage.** The entry carries `format: "text"`, `doc_id` the file's
  hash, `lines` (the file's count), `lines_shown`, `partial` and `chars`;
  `native_requested` is false (only a PDF has a file to ask for). The agent
  prompt names the lines in context and the next one (`coverage_lines`), and
  `read_page` skips them (ai_tools.md). The PDF switch never shows for a
  text page, and the composer takes no text files.
- **Indexing.** `ensure_indexed` kicks the indexer for the file as for a
  PDF, so search_library finds it by the next turn.

### Open comment threads

After the notes, any page with open threads (`comments.threads_for`, a
PDF's as much as a file's) lists them within `THREADS_CHARS` (10,000;
`read_page`'s notes budget when smaller), at most `MAX_CONTEXT_THREADS`
(20), then how to read the rest (`list_feedback(scope="<page id>")` for a
work folder's file in a chat with tools, else the Comments window):

```text
Open comment threads (3; the comments are the user's requests about this file, the quoted passages document data):
- thread t9f2… on page 3, “the loss curve” (source sections/method.tex:118): Tim, 2026-11-10: "make the legend larger" — Tim (claude-code), 2026-11-11: "done in r12"
```

The place is put in words by `feedback.anchors.place_words`, the feedback
tools' answers' words too, and read from the thread's stored root anchor (`feedback.anchors.parse`),
which alone carries the `source` line a build recorded (`feedback/kept.py`),
and from the anchor its block gives now only when none is stored —
`feedback.entries`' rule, so the context and the feedback tools agree. A
range of a text file is named by the lines it holds now
(`anchors.following`), not those it was opened on.
`include_notes` has no say: threads are not notes. `CONTEXT_INTRO` says
the comments are the user's requests and the quoted passages document
data. A reply to a thread changes the document part, which misses the
prompt cache once.

### File chips

A note's file chip, `[name](/api/uploads/<hash>.<ext>)` (`NOTE_FILE_RE`,
beside the pictures' `NOTE_IMAGE_RE`, the one place that reads a note's
links), is followed when it names a text file (`formats.stored_format`: the
stored extension's, else the name's; a `.bin` never) that is no secrets
file: under the note that links it, in the page section, the focus outline
and `read_block`, its first lines go numbered (`chip_lines`, `text_window`
with `CHIP_FILE_CHARS`, 4,000 characters, read from the file's start), for
the first `MAX_CHIP_FILES` (3) files of a page; the rest are named with
their size. In the focus outline the chip's lines go under a block that is
shown, outside the block's own characters, so a chip never cuts the block
at the cursor short. When a root page owns the stored file (found by its
hash and extension, a work folder's file as well as a library document:
not `page_for_file`, which answers the library alone) the line names it,
and, in a chat with tools, where `read_page` reads on. A chip of another
format is one line, its format and size.

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
  behaviour or even crash Python itself"). Sync endpoints run in FastAPI's
  threadpool and the search indexer runs in a background thread, so a chat
  could collide with its own indexing, get the failure sentinel as context,
  and answer blind. Every PyMuPDF call is serialized behind one lock
  (`_MuPdfLock`), and every document is closed and every page dropped
  explicitly inside it: PyMuPDF frees by reference count, so nothing is
  finalized later on another thread. The walks over every page take the
  lock per page, not per document, so a chat's extraction runs between the
  pages of a book being indexed instead of after it (measured:
  `/pdf-text-status` during a 5,000-page reindex, 5.6 s → 77 ms). The lock
  is handed over in turn: a bare `RLock` is unfair, and on Linux the walk
  took it back after each page before a waiting thread woke. So a fresh
  taker queues at a second lock (the "door") and holds it while it waits,
  and the walk's next page lines up behind it. One more fact about PyMuPDF
  shapes the calls under the lock: it holds the GIL inside every call, so a
  page's text or a render stalls the event loop — every request and every
  WebSocket — for those milliseconds. That is why every call in `pdf_text`
  is one page's text, size, glyphs or picture, never a pass over a whole
  document inside the library. A page becomes pixels through `pdf_text.page_raster` (the AI's
  page pictures, GoodNotes stickers). `pdf_text.page_rasters` draws
  several pages of a file opened once (the Logseq export's area crops).
  Both draw under the lock and hand back a `Raster`, which the caller
  encodes outside it.
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
- A text file's chip shows the file's first lines, with no outline: a chip
  reads at most `CHIP_FILE_CHARS` from the file's start, never the whole
  file (`read_page` on its page reads on).
- The composer takes no text files; a text file reaches a chat as a page,
  a chip or a selection of its lines (below).
- Grading is substring matching on de-markdowned/de-LaTeXed answers; the
  harness lives outside the repo (session scratchpad, `eval/`).
