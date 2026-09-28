# How the AI chat manages context, and where it wastes it

A study of what one `/api/ai/chat` request carries, what grows as a
conversation goes on, and what would make the chat cheaper and more robust.
Read from the code in September 2026 (`gamma/ai_context.py`,
`gamma/ai_tools.py`, `gamma/routers/ai.py`, `gamma/ai_client.py`,
`gamma/ai_protocols/*`, `frontend/src/chat/ChatDock.jsx`). The mechanics as
they are meant to work live in [docs/dev/ai.md](../dev/ai.md),
[ai_context.md](../dev/ai_context.md) and [ai_tools.md](../dev/ai_tools.md);
this note is the audit behind the next change to them, not a copy.

## What one request carries

Chat APIs keep nothing between calls, so every request rebuilds the whole
prompt. Gamma's has four layers:

1. **Fixed per conversation.** The system prompt (`_SYSTEM_PROMPT` ≈1.2k
   chars + `_CITATION_PROMPT` ≈0.9k, plus `agent_system()` ≈3–5k when tools
   are on) and, with tools, the tool specs (up to 14, ≈10k chars of JSON).
2. **The page context**, built fresh on every request by `gather_inputs`
   and injected once, in front of the *oldest* user turn
   (`build_messages`, `ai_context.py:385-391`): each context page's title,
   properties line, notes (always for a note page, with `include_notes`
   for a PDF page) and, for a PDF, the first `context_char_limit` chars
   (default 60,000) labelled as an excerpt when the document doesn't fit —
   or windows around the selected passages when the message has a
   selection, plus a document map (≈2.4k, in practice up to 4.1k for
   24–47-page papers) in agent chats. The "Model saw pages 1–5 of 19" chip
   reports how far the character budget reached, not a page count anyone
   chose.
3. **The conversation**: every saved turn's `text`, unbounded, and in agent
   chats every earlier reply's tool calls replayed as call + result turns,
   the results sharing an 8,000-char budget newest-first
   (`TOOL_REPLAY_BUDGET`), the calls themselves never dropped.
4. **This turn**: the question, up to 24k chars of selected PDF passages
   and 24k of selected note text, the cursor block and attached chips
   (≤13.5k), pasted images and selection crops, native PDF files.

With tools, one turn is then a loop of up to 32 rounds
(`MAX_TOOL_ROUNDS`), and each round re-sends all of the above plus every
tool result so far, in full, plus every `view_pdf_page` picture and every
attachment (`routers/ai.py:1488-1520`). Nothing inside a turn is elided.

That is why a page chat's third message on a 19-page physics paper reads
"↑28k": ≈20k tokens for the 60k-char excerpt (dense notation tokenizes
worse than prose), ≈4k of prompts and tool specs, and the turns so far —
all of it sent again on every message, and again on every tool round.

## What already works and must survive any change

The pieces the 2026-08 eval put in place ([ai_context.md](../dev/ai_context.md))
are the reason the chat answers from the paper instead of from memory:
the EXCERPT label and `CONTEXT_INTRO`, the grounding clause in the system
prompt, `[PDF page N]` labels feeding citations, the document map, the
search relaxation, `ensure_indexed` kicking the indexer from any chat, the
coverage report streamed back as the first NDJSON line, the selection-
centred context with crops for formula text, and the cross-turn tool-result
elision with its "call again before quoting" note. None of the problems
below is fixed by weakening these.

## Problems, by cost

### 1. No prompt caching on any wire

`grep cache_control|prompt_cache|previous_response_id` over the repo finds
nothing. The Anthropic adapter (`ai_protocols/anthropic.py:58-86`) sets no
`cache_control` breakpoints, so system, tools, the 60k excerpt and the
history are billed as fresh input on every turn and every tool round —
`cache_read`/`cache_write` in the usage line, and the "% from the prompt
cache" the UI already renders (`tokenUsage.cachedPercent`), are always 0
there. OpenAI's Chat Completions and Responses wires rely on automatic
prefix caching with no `prompt_cache_key`; the Responses wire hard-codes
`store: False` (`responses.py:67`) without `previous_response_id`; the
Codex backend gets a fresh `uuid4()` `session_id` on every request
(`chatgpt.py:99`), where Codex CLI keeps one per session.

Within one agent turn the total input is roughly quadratic in the number of
rounds: round *n* re-sends *n−1* rounds of results and the whole fixed
part. A 10-round answer on a 60k excerpt is on the order of 200k+ uncached
input tokens.

### 2. The prefix changes when it shouldn't

Caching (explicit or automatic) works on a stable prefix. Gamma's prefix is
the system prompt, then the tools, then the context glued to the oldest
user turn — and that context is *rebuilt every request*: a message with a
PDF selection swaps the head excerpt for `selection_context` windows, the
next one without a selection swaps it back, `include_notes` or the context
size edited mid-conversation rewrite it, a note edit changes the notes
section. Every such change invalidates everything after it, which is the
whole conversation. The context is historically attached to the first
question but semantically belongs to the current one.

### 3. Nothing bounds the prompt before the provider refuses it

The model's context window is fetched (`ai_catalog.context_window`) and
drawn as the header's ring, red past 80% — and used for nothing else. The
server never estimates the assembled prompt's size, never compares it with
the window, never trims. Character caps exist on the parts (excerpt,
selections, chips, replay budget), but not on the history's prose, the
replayed call stubs, the in-turn tool results, or images. Overflow arrives
as the provider's 400/413, classified `too_long` (`ai_client.py:83-98`),
shown as "This conversation is too long for the model" with "New chat" as
the fix. One accidental retry exists: a native-PDF request that fails with
any 4xx is retried as text (`open_with_fallback`), which may happen to fit,
and then wrongly marks the provider as refusing PDF files.

Cut-off replies aren't detected either: Anthropic's
`stop_reason: max_tokens`, OpenAI's `finish_reason: length` and the
Responses `response.incomplete` event are only consulted when the reply is
empty, so a reply that hit the 8,192 output cap looks complete, a
`response.incomplete` stream loses its usage, and a tool call whose
arguments were cut off runs with `{}` (`parse_tool_args`).

### 4. The same text goes in several times

- **Head excerpt vs `read_page`.** The first 60k chars are in context, and
  the agent prompt says "look the answer up with the tools before answering,
  even if you think you know it … a PDF excerpt is only part of the
  document" (`ai_tools.py:1446-1452`) — even when the whole paper fit and no
  EXCERPT label was added. The model dutifully calls `read_page(pdf_page=1)`
  and gets the text it already has. Nothing tells it which pages the
  excerpt covers.
- **Notes ×4.** A page's notes and highlights are in the context section
  (when included), again in **every** `read_page` result — the read window
  caps the PDF text only; `page_report_section` appends all notes and
  highlights uncapped on every windowed call, so paging a paper N times
  sends the notes N times — again with ids in `read_block`, and again in the
  focus/chip section.
- **Selections ×2.** A selected PDF passage is quoted in the question (≤24k)
  *and* sits inside its `selection_context` window; a note selection is
  quoted (≤4k each) *and* its whole block is attached as a chip.
- **Replay vs re-read.** The replayed results carry "call again before
  quoting or editing", so the model re-reads, and the request then holds
  the 4k replayed copy and the fresh full copy of the same page.
- Each `read_page` repeats the page's title, properties and summary, which
  the context already holds.

### 5. Growth across a long conversation

The client sends every saved message object as `history` — including
base64 `images` from earlier turns, `actions` with 4k results, `context`
and `usage` — on every request (`ChatDock.jsx:927`); the server reads only
`text` and `actions`, but the upload happens, and the same array is
re-`PUT` to `/api/chats` every 500 ms while a reply streams. On the server,
`build_messages` replays every non-error turn in full: no turn limit, no
token budget, no summary. In agent chats the replayed tool-call stubs and
the `_ELIDED_RESULT` placeholders are never dropped and the
`_REPLAYED_NOTE` prefixes (≈120 chars each) aren't counted against the
replay budget. A long conversation ends only in `too_long`.

### 6. Budgets measured in the wrong unit

Every budget is characters. 60,000 chars is ≈15k tokens of English prose,
≈20k+ of notation-heavy physics, and ≈40–60k of Chinese or Japanese text —
the same setting fills a quarter or a whole 64k window depending on the
paper. The multi-page split divides `multi_context_char_limit` by *all*
context pages, note-only pages included, which get unbounded notes anyway
and waste their PDF share. Uploaded PDF files each get the full
single-paper budget on top of the split. The document map's sampling step
(`rows*100//2400`) lets 24–47-page papers exceed the stated 2.4k.

### 7. Docs that no longer match the code

`ai_tools.md` says `_DETAIL_CAP`/`_ARG_CAP` cap tool output — they cap the
saved chip, the model gets the full result. `ai.md`'s "Read window" is the
PDF text only. `ai_context.md`'s "~2.4k" map, "every single-page chat
request" (it is every PDF page of every request) and "split evenly across
papers" (across pages) are each a little off.

## What to change, in order

The order is by payoff per line of code. Each step is independent, and the
usage line's `cache_read` and the header ring make every one measurable in
the UI before and after.

### Tier 1 — cheap, and most of the cost

1. **Ask for caching on every wire.** Anthropic: `cache_control:
   {type: "ephemeral"}` on the last tool spec, the system prompt, the
   context turn and the last history turn (four breakpoints is the API's
   limit; the adapter is the one place, `anthropic.py:request`). OpenAI
   Chat and Responses: `prompt_cache_key` = the chat bucket, so turns of one
   conversation route to the same cache. Codex: one `session_id` per
   conversation, not per request (`chatgpt.py:99`). Leave `store: False`
   and `previous_response_id` alone — server-side storage of the
   conversation is a privacy trade the user hasn't made, and prefix caching
   gets most of the saving without it.
2. **Keep the prefix stable within a conversation.** Split the context into
   a *document part* that stays put — the head excerpt, notes, map, in its
   own first user turn, rebuilt only when the page or its settings change —
   and a *per-message part* — selection windows, crops, the focus block,
   chips — that rides with the current question, where it belongs
   semantically anyway (`final_prompt` already quotes the selection there).
   Then the excerpt is cached from the second message on, and the only
   moving part is small.
3. **Elide inside a turn, as across turns.** After each round, replace all
   but the last *k* rounds' tool results (and pictures) with the same
   `_ELIDED_RESULT` stub the replay uses, under a per-turn budget. The
   model already lives with that convention across turns. This turns the
   quadratic growth into linear.
4. **Pre-flight and one retry.** Estimate the prompt (chars/4 by default;
   chars/1.5 for CJK-heavy text is close enough) and compare with the
   window already fetched; when over, trim the oldest turns (keeping the
   context turn) before sending rather than after the 400 — and on a
   `too_long` from the provider, trim once and retry instead of showing the
   card. Stop `open_with_fallback` from blaming the PDF file for a size
   refusal (a 413 is not "native parts rejected").

### Tier 2 — the duplication

5. **`read_page` sends notes once.** Notes and highlights only on the first
   window of a page (`pdf_offset == 0 and pdf_page == 1`) or on an explicit
   `notes=true`; later windows say "(notes shown in the first read)". Cap
   them like the chips (`MAX_BLOCK_SECTION_CHARS`) and continue through
   `read_block`. Drop the repeated properties line on continuation windows.
6. **Tell the model what it already has.** The context label should name
   the covered pages ("the first 60,000 characters — PDF pages 1–5 of
   19"), the agent prompt should say "pages 1–5 are in context above; read
   later pages with read_page" and drop the "even if you think you know it"
   line when the whole document fit (`cover["partial"]` is known when
   `agent_system` is built). The document map can start at the first
   page *not* in the excerpt.
7. **Stop quoting selections twice.** With `selection_context` in place the
   question needs the passage's label and location, not its full text (or
   the text only when no window could be placed). Same for a note
   selection whose block is attached as a chip.
8. **Send only what the server reads.** `history` should carry `{role,
   text, actions}` — not images, coverage, usage — and the autosave should
   not re-PUT base64 images every 500 ms (save them once, reference by an
   upload hash, as the notes do for pictures).

### Tier 3 — long conversations and units

9. **Compaction.** When the conversation passes ~60% of the window
   (the ring's figure), summarize the turns older than the last few into
   one "earlier in this conversation" note with one cheap model call,
   store it on the chat row so it's computed once, and replay summary +
   recent turns. Offer it as a button too (Claude Code's `/compact`). Drop
   replayed tool-call stubs older than the summary point. The `read_chats`
   tool keeps the full transcript reachable.
10. **Detect truncation.** Surface `stop_reason: max_tokens` /
    `finish_reason: length` / `response.incomplete` as a `partial` flag on
    the reply (the chip exists: "reply cut off at the output limit"), keep
    the usage from `response.incomplete`, and refuse to run a tool call
    whose arguments didn't parse.
11. **Budget in tokens.** Keep the settings in characters (that is what a
    person can picture) but convert with a per-script ratio when cutting,
    so a Chinese paper and an English one fill the same share of the
    window; split the multi-page budget among PDF pages only; count
    uploaded files inside it; fix the map's step so 2.4k means 2.4k.
12. **Per-model output cap.** Look up the model's max output alongside its
    context window (the catalog sources carry it) and send
    `min(model max, window − prompt estimate)` instead of a flat 8,192;
    give the Codex path a cap too.

Then bring `ai.md`, `ai_context.md` and `ai_tools.md` in line with
whatever ships, and add the numbers: the eval harness that produced the
2026-08 table is the right tool to show that recall didn't move while
input tokens per question fell.

## What shipped from this (September 2026)

Tier 1 whole, Tier 2's steps 5–6 and 8, and step 10 from Tier 3:
`cache_control` breakpoints on Anthropic, `prompt_cache_key` on OpenAI's
platform and the Codex backend with a per-conversation `session_id`; the
context split into the document part on the oldest turn and the message
part with the question; `elide_live_results` as the in-turn valve;
`prompt_tokens` against the catalog's window with `drop_turns`, and a
retry on `too_long`; `read_page` skipping the pages and notes the context
holds, with `coverage_lines` in the agent prompt and the excerpt label
naming its pages; the map from the cut-short page; the client sending
`{role, text, actions}` only; the stop reason surfaced as `truncated`.
The coverage pill now folds in what the tools read and which pages
nobody saw. Still open: quoting a selection twice (step 7), compaction
(9), token-aware budgets and the multi-page split (11), a per-model
output cap (12). The mechanics are in [ai.md](../dev/ai.md) "Prompt
caching" and "Fitting the window", [ai_context.md](../dev/ai_context.md)
and [ai_tools.md](../dev/ai_tools.md).

## What was not measured

This is a reading of the code, not a measurement. The one live number
(28k input on the third message of a page chat) matches the estimate but a
before/after on cached tokens per turn, input tokens per agent turn by
round count, and the recall table from `ai_context.md` re-run after Tier 2
are what should decide whether steps 6–7 cost accuracy. `cache_read` is
already in every usage line, so step 1's effect shows in the UI on the
first day.
