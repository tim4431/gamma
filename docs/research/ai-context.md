# How the AI chat manages context, and where it wastes it

A study of what one `/api/ai/chat` request carries, what grows as a
conversation goes on, and what would make the chat cheaper and more robust.
Read from the code in September 2026 (`gamma/ai_context.py`,
`gamma/ai_tools.py`, `gamma/routers/ai.py`, `gamma/ai_client.py`,
`gamma/ai_protocols/*`, `frontend/src/chat/ChatDock.jsx`). The mechanics as
they are meant to work live in [docs/dev/ai.md](../dev/ai.md),
[ai_context.md](../dev/ai_context.md) and [ai_tools.md](../dev/ai_tools.md);
this note is the audit behind the next change to them, not a copy.

## What the audit found

What the code did in September 2026, and why each point cost something.
What it does now is in the dev docs named above, in [ai.md](../dev/ai.md)
under "Prompt caching" and "Fitting the window".

Chat APIs keep nothing between calls, so every request rebuilds the whole
prompt: the system prompt and, with tools, up to 14 tool specs (≈10k chars
of JSON); the page context, which for a PDF is its first 60,000 chars by
default plus a document map (≈2.4k, in practice up to 4.1k for 24–47-page
papers); every saved turn; and this turn's question with up to 24k chars
of selected PDF passages, 24k of selected note text and ≤13.5k of attached
chips. With tools, one turn is a loop of up to 32 rounds, and each round
re-sent all of that plus every tool result and picture so far, in full.

That is why a page chat's third message on a 19-page physics paper read
"↑28k": ≈20k tokens for the 60k-char excerpt (dense notation tokenizes
worse than prose), ≈4k of prompts and tool specs, and the turns so far —
all of it sent again on every message, and again on every tool round.

Seven problems, by cost:

1. **No prompt caching on any wire.** No request asked for it: no
   `cache_control` breakpoints on Anthropic, no `prompt_cache_key` on
   OpenAI, and a fresh `session_id` per request on the Codex backend,
   where Codex CLI keeps one per session. System, tools, the excerpt and
   the history were billed as fresh input on every turn and every tool
   round. Within one agent turn the total input was roughly quadratic in
   the number of rounds: round *n* re-sent *n−1* rounds of results and the
   whole fixed part. A 10-round answer on a 60k excerpt was on the order
   of 200k+ uncached input tokens.
2. **The prefix changed when it shouldn't.** Caching (explicit or
   automatic) works on a stable prefix. The context was glued to the
   oldest user turn and rebuilt on every request: a message with a PDF
   selection swapped the head excerpt for windows around the selection,
   the next one swapped it back, and a settings or note edit rewrote it.
   Every such change invalidated everything after it, which is the whole
   conversation. The context was historically attached to the first
   question but semantically belongs to the current one.
3. **Nothing bounded the prompt before the provider refused it.** The
   model's context window was fetched and drawn as the header's ring, red
   past 80%, and used for nothing else. Character caps existed on the
   parts, but not on the history's prose, the replayed call stubs, the
   in-turn tool results or images. Overflow arrived as the provider's
   400/413, shown as "This conversation is too long for the model" with
   "New chat" as the fix. A native-PDF request refused for its size was
   retried as text, and the provider was then wrongly marked as refusing
   PDF files. Cut-off replies weren't detected either: a reply that hit
   the 8,192 output cap looked complete, and a tool call whose arguments
   were cut off ran with `{}`.
4. **The same text went in several times.** The head excerpt and
   `read_page`: the agent prompt said to look the answer up "even if you
   think you know it", even when the whole paper fit, and nothing told
   the model which pages the excerpt covered. A page's notes up to four
   times: in the context section, in every `read_page` window (uncapped,
   so paging a paper N times sent the notes N times), in `read_block` and
   in the focus/chip section. A selected passage twice: quoted in the
   question and inside the window around it. A replayed tool result
   beside the fresh re-read its "call again before quoting" note asks for.
5. **Growth across a long conversation.** The client uploaded every saved
   message object as `history`, base64 images and 4k action results
   included, though the server read only the text and the actions. The
   server replayed every non-error turn in full: no turn limit, no token
   budget, no summary. A long conversation ended only in `too_long`.
6. **Budgets measured in the wrong unit.** Every budget is characters.
   60,000 chars is ≈15k tokens of English prose, ≈20k+ of notation-heavy
   physics, and ≈40–60k of Chinese or Japanese text — the same setting
   fills a quarter or a whole 64k window depending on the paper. The
   multi-page split divides its budget by *all* context pages, note-only
   pages included, which get unbounded notes anyway and waste their PDF
   share. Uploaded PDF files each got the full single-paper budget on top
   of the split.
7. **Docs that no longer matched the code**, in small ways (what the chip
   caps cap, what the read window covers, the map's size). Those passages
   are since corrected in the three docs.

## What already works and must survive any change

The pieces the 2026-08 eval put in place ([ai_context.md](../dev/ai_context.md))
are the reason the chat answers from the paper instead of from memory:
the EXCERPT label and `CONTEXT_INTRO`, the grounding clause in the system
prompt, `[PDF page N]` labels feeding citations, the document map, the
search relaxation, `ensure_indexed` kicking the indexer from any chat, the
coverage report streamed back as the first NDJSON line, the selection-
centred context with crops for formula text, and the cross-turn tool-result
elision with its "call again before quoting" note. None of the problems
above is fixed by weakening these.

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
   conversation, not per request. Leave `store: False` and
   `previous_response_id` alone — server-side storage of the
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

- Step 1: `cache_control` breakpoints on Anthropic (`_with_breakpoints` in
  `ai_protocols/anthropic.py`), `prompt_cache_key` on OpenAI's platform
  (`ai_protocols/openai.py`, `responses.py`), and a per-conversation
  `session_id` on the Codex backend (`ai_protocols/chatgpt.py`).
- Step 2: the context split into the document part on the oldest turn and
  the message part with the question (`ai_context.build_messages`,
  `message_context`).
- Step 3: `ai_context.elide_live_results` as the in-turn valve
  (`LIVE_KEEP_ROUNDS`, `LIVE_RESULT_BUDGET`).
- Step 4: `ai_context.prompt_tokens` against the catalog's window with
  `drop_turns`, and a retry on `too_long` (`routers/ai.py`).
- Steps 5–6: `read_page` skipping the pages and notes the context holds,
  with `ai_tools.coverage_lines` in the agent prompt and the excerpt label
  naming its pages; the map from the cut-short page
  (`ai_context.document_map`, `from_page`).
- Step 8: the client sending `{role, text, actions}` only, since joined by
  `reasoning`, the thinking a provider wants back
  (`frontend/src/chat/ChatDock.jsx`).
- Step 10: the stop reason surfaced as `truncated` (`truncated_stop` in
  `ai_protocols/base.py`).
- Step 8: a message's pictures are stored once and sent by URL, and the
  history carries `{role, text, actions, images}` (`gamma/ai_pictures.py`;
  [ai-pictures.md](ai-pictures.md)).

The coverage pill now folds in what the tools read and which pages
nobody saw.

## Still open

- Step 7: a selection is still quoted twice.
- Step 9: compaction.
- Step 11: token-aware budgets and the multi-page split. Only the map's
  step is since fixed (`document_map` rounds it up).
- Step 12: the per-model output cap is built in part.
  `ai_catalog.reply_cap` bounds a named service's cap by the model's
  output limit, while OpenAI, Anthropic and custom endpoints keep the flat
  8,192.

## What was not measured

This is a reading of the code, not a measurement. The one live number
(28k input on the third message of a page chat) matches the estimate but a
before/after on cached tokens per turn, input tokens per agent turn by
round count, and the recall table from `ai_context.md` re-run after Tier 2
are what should decide whether steps 6–7 cost accuracy. `cache_read` is
already in every usage line, so step 1's effect shows in the UI on the
first day.
