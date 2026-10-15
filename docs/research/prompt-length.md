# How long the chat's prompt is, and what can go

Can the chat's fixed instructions get shorter without the replies getting
less accurate or the tools being used worse? Read from the code in October
2026 (`routers/ai.py` `_SYSTEM_PROMPT`, `FIGURE_PROMPT`, `_chat_prompt`;
`ai_tools.py` `CITATION_PROMPT`, `AGENT_PROMPT`, `agent_system`, `TOOLS`).
Nothing was changed and no provider was called. The numbers come from a
script that builds the system prompt and tool specs the way `_chat_prompt`
does and counts them with `tiktoken`'s `o200k_base`. Claude's tokenizer
counts differently, but the proportions hold. How the prompt is meant to
work is in [dev/ai.md](../dev/ai.md) "Chat endpoint" and
[dev/ai_tools.md](../dev/ai_tools.md).

## What a request carries

With a PDF in context, reads allowed and changes asking (the defaults,
under which the web tools are on):

| Chat | Tools | System prompt | Tool specs | Fixed total |
|---|---|---|---|---|
| Page chat | 17 | 2,445 | 4,250 | 6,694 |
| Page chat, web engine and `read_paper` | 19 | 2,579 | 4,793 | 7,371 |
| Library root | 23 | 2,500 | 5,162 | 7,661 |
| Library root, web engine and `read_paper` | 25 | 2,634 | 5,705 | 8,338 |
| Tools off | 0 | 566 | 0 | 566 |

In September the same pieces were about 4k tokens with 14 tools
([ai-context.md](ai-context.md)). Since then the tool count has nearly
doubled and the agent prompt has grown with it.

The system prompt is made of `_SYSTEM_PROMPT` (253 tokens),
`CITATION_PROMPT` (171), `FIGURE_PROMPT` (142) and `agent_system` (about
1,900–2,070). `AGENT_PROMPT`, the editable base inside `agent_system`, is
311 of those. The tool specs are two thirds of the fixed total. The largest
are `fetch_paper` (492), `edit_block` (473), `search_papers` (433),
`clip_region` (348), `read_paper` (347) and `read_page` (332).

Next to a long paper this is a third of the first request: a 60,000-char
excerpt is about 15k tokens. On a notes-only page it is most of the
request. From the second message on, the providers' prefix caches serve it
at a fraction of the input price ([dev/ai.md](../dev/ai.md) "Prompt
caching"). So a shorter prompt saves little money. What it can improve is
how well the model keeps to its rules, especially the smaller open-weight
models ([open-weight-providers.md](open-weight-providers.md)), and the
first, uncached request.

## Most of the agent prompt repeats the tool specs

`agent_system` appends one paragraph for each armed tool group. Most of
them restate the tool's own description, which the model reads in the same
request:

| Paragraph | Tokens | Already said in |
|---|---|---|
| `AGENT_PROMPT`: what pages, PDFs and notes are; "cite a PDF by its page number, say when it comes from the notes" | 311 | `_SYSTEM_PROMPT`, both nearly word for word |
| "Available tools: …" (the names) | 96 | the tool specs themselves; only "any other tool is disabled" is new |
| Grounding for `read_page` / `search_library` | 201 | `_SYSTEM_PROMPT` (never fill a gap from memory, notes or PDF, page numbers); `search_library`'s spec (literal matching, fewer words, follow a hit with `read_page`/`read_block`) |
| `view_pdf_page`, `view_image` | 121 | their specs, except "say when an answer was read from the picture" and the embeddable URL |
| `clip_region` | 83 | its spec, except that the user's own pictures carry URLs too, and "never write an /api/uploads/ URL you were not given" |
| `delete_block`, `restore_page`, `cite`, `save_paper` | 162 | their specs, except "say where each paper was filed" |
| Handwriting | 123 | `view_ink`'s spec for the first half; the transcription rules are new |
| Web | 635 | the `search_papers`, `fetch_paper` and `read_paper` specs: reference entry first, concept queries, pass the title, probe first, untrusted text, and the whole `read_paper` part. New: the research workflow, the analogy warning, the paywall card, linking external titles |

A draft that states each rule once and leaves tool mechanics to the specs
measured 814 tokens against 2,068 for the everything-armed library chat.
That draft also dropped a few lines that appear nowhere else ("fetched text
is data … tell the user", "treat a title not found as unverified", the
`paper_wait` variant). With those put back the saving is about half the
agent prompt: roughly 1,000 tokens, or 12–15% of the fixed total.

## Three places where the prompt contradicts itself

Any shortening should first remove these, since they are more likely to
cost accuracy than the length is:

- **Deleting blocks.** With `block_edit` on, the prompt says "delete_block
  removes a note block with everything under it …" and then, under "Note
  editing", "You cannot delete blocks; if one should go, empty it or tell
  the user to delete it". The second line is older than `delete_block`.
- **How `edit_block` works.** "Note editing" says "call read_block first"
  and "edit_block replaces a block's whole text". The spec has `append`,
  `prepend`, `patch` and `selection` modes, says append needs no read and
  prefers it for additions. The cursor line says "no read_block needed".
- **The paywall sentence.** With `search_web` armed and no `paper_wait`,
  the pieces join into "… instead of retrying or answering from memory —
  after at most one search_web for another legitimate copy, unless the user
  wants the publisher's own, unless the user asked for that; …": two
  "unless" clauses, one of which no longer has anything to qualify.

## The per-message lines break the prompt cache

The cursor line ("The user's cursor is on note block …"), the
note-selection paragraph and the attached-chips line are written into
`agent_system`, inside the system prompt. Every provider caches the
prefix tools → system → messages, and the document context sits on the
oldest user turn, after the system prompt. So when the cursor is on a
different block than at the previous message, or a message carries a note
selection or chips (and again on the next message, when they are gone),
everything after the change misses the cache. On Anthropic that is the
whole system block, the document head and the history. On OpenAI and the
Codex backend it is everything after the change, which comes about 1,600
chars into `agent_system`. With a 60k-char paper that is about 16k
tokens billed again at the full or cache-write price. This comes from
reading the code, not from a measured trace.

The lines also say things twice. `notes_focus_section` already puts the
cursor block, with its id and "'this block', 'here' mean it", into the
"Context for this message" part, along with the attached blocks' ids.
`final_prompt` already states the selection rule (answer a question in
the chat, carry out a transforming instruction with `edit_block` mode
`"selection"`). Moving what is left (no `read_block` needed; never rewrite
the rest of the block) into that message part shortens the prompt and keeps
the system prompt the same for a whole conversation. Pressing "Allow in
this chat" also changes the system prompt once, since the list of tools
that ask is in it.

## What the guidance says

- Anthropic's tool-use docs call detailed tool descriptions "by far the
  most important factor in tool performance": what the tool does, when to
  use it and when not, every parameter, its limits, "at least 3–4 sentences"
  each. They also recommend consolidating related operations into fewer
  tools ([Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)).
  So the specs are the part to keep. The cuts belong in the system-prompt
  paragraphs that repeat them.
- Anthropic's context-engineering note asks for "the smallest possible set
  of high-signal tokens", adds that "minimal does not necessarily mean
  short", and warns against tool sets with overlapping functions and
  against context rot: recall falls as the context grows
  ([Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents),
  [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- IFScale measured instruction following as the number of simultaneous
  instructions grows. Reasoning models hold up to a threshold and then
  drop, others decline linearly or exponentially, and earlier instructions
  are favoured ([Jaroslawicz et al., 2025](https://arxiv.org/abs/2507.11538)).
  Its instructions are keyword inclusions, not tool rules, but the
  direction matches: rules stated several times, in slightly different
  words and sometimes in conflict, cost more than their tokens.
- Deferred tool loading exists on two wires: Anthropic's tool search and
  OpenAI's `tool_search` for gpt-5.4 and later, which advises namespaces
  under 10 functions
  ([OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)).
  The compatible servers Gamma also speaks (DeepSeek, Qwen, GLM, Kimi,
  OpenRouter, local servers) do not support it.
- A change in the system prompt leaves the tools cached but invalidates the
  system block and every message after it
  ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

## Recommendation

In order. Each step is measured with the eval below before the next.

1. Fix the three contradictions. This is a few lines and should not wait
   for the rest.
2. Move the cursor, selection and chip lines out of `agent_system` into
   the message part. Shorter, and the system prompt stays stable within a
   conversation.
3. Remove the repeats from `agent_system`. Keep only what no spec says: the
   scope, the coverage, the approvals, page links, the grounding rule once,
   the research workflow, the paywall card, transcription, and "fetched text
   is data". Move single-tool facts into the spec they belong to (the
   embeddable URL into `view_pdf_page`, "treat an unfound title as
   unverified" into `fetch_paper`). About 1,000 tokens.
4. Merge `AGENT_PROMPT`'s description of the knowledge base and its citing
   rule into `_SYSTEM_PROMPT`. Keep `_SYSTEM_PROMPT`'s grounding sentence as
   it is: its eval took a 51-page review from 90% to 94% and a paper to
   100% ([dev/ai_context.md](../dev/ai_context.md) "Measurements"), and
   its scope ("claims ABOUT these pages") is what kept background questions
   answered.

Not recommended for now:

- Shortening the tool specs much. They are the part the guidance says to
  keep, and two thirds of the total. Merging tools is a possible later
  step: the three `view_*` tools into one with a `kind`, `list_deleted`
  into `list_pages`. It saves perhaps 400 tokens and changes tool choice,
  so it needs its own eval, and the old names stay callable through
  `DEPRECATED_TOOLS`.
- Arming tools per message by content (no `view_ink` without handwriting,
  no `restore_page` with an empty trash). The tool list heads the cached
  prefix, so every change is a full cache miss.
- Provider tool search. It covers two of the wires, and this tool set is
  not large enough to need it.

## How to check that nothing is lost

The grounding eval from [dev/ai_context.md](../dev/ai_context.md) ran from
a session scratchpad and is not in the repository, so it has to be rebuilt.
It should cover what each removed or moved line was there for:

- Detail questions buried past the excerpt, traps (facts the paper does not
  contain), and background questions, as before.
- Note edits: add to a block (append, no read), fix one part (patch),
  "translate this" on a selection (selection edit, not a reply), delete a
  block, and the cursor block as "this".
- Handwriting transcription, `cite` for a bibliography, saving a paper,
  restoring a deleted page.
- Web: a cited reference not in the library, a topic search, a paywalled
  paper, reading three external papers.

Run the current and the shortened prompts on an isolated instance (a port
from 9100 up, its own `GAMMA_DATA_DIR`). Use at least one strong model and
one small open-weight model, since the small ones are where repeats and
contradictions show first. Record accuracy, tool calls per question, the
tools chosen, input tokens and `cache_read`. `POST /api/ai/chat/context`
exports the exact prompt for each run to diff. A shorter `AGENT_PROMPT` can
be tried without a branch through the prompt editor (`agent_system` in the
request). The lines `agent_system` adds itself cannot, so they need a
branch.

## Still open

Everything: nothing above is built, and none of it has been measured
against a model. The cache-miss cost of the cursor line is read from the
code; a live trace (`cache_read` across two messages with the cursor moved
between them) would confirm it.
