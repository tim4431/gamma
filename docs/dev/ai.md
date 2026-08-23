# AI wiring: providers, chat, and the agent loop

Developer-facing map of the AI stack — provider storage and protocols, the
`/api/ai/chat` request/stream shape, and the library agent's internals. The
human-facing agent doc (tools, permissions, privacy) is [agent.md](agent.md);
how long papers reach the model is [ai_context.md](ai_context.md). Code:
`gamma/ai_settings.py`, `gamma/ai_client.py`, `gamma/ai_context.py`,
`gamma/ai_tools.py`, `gamma/chatgpt_oauth.py`, `gamma/routers/ai.py` +
the chat-history router.

## Providers

There are NO env API keys; providers are per-user GUI entries (Settings →
Providers) stored under the reserved `ai-settings` prefs key in the user's
`data.db` — a LIST of `{id, name, protocol, api_key, base_url, models}` managed
via `POST/PUT/DELETE /api/ai/providers[/{id}]`. The generic prefs endpoints
refuse the key; the only read path is the masked `GET /api/ai/settings` (last-4
hint, never the key), guests can't write. `POST /api/ai/providers/{id}/test`
probes an entry with a tiny live completion for the settings list's Test button
— result in-body, never an HTTP error, and it clears the OAuth refresh backoff
so an expired ChatGPT grant is re-tried immediately.

`ai_runtime(user)` in `gamma/ai_settings.py` builds the per-request config and
model registry (ids are `<entryId>:<model>`; the wire format comes from the
entry's `protocol`, never from the provider id) — AI endpoints must use it, not
module-level config constants. Env vars only set each protocol's default base
URL (`GAMMA_AI_ANTHROPIC_BASE_URL` / `GAMMA_AI_OPENAI_BASE_URL`).

### The chatgpt protocol (OAuth)

A third protocol, `chatgpt`, holds OAuth tokens instead of a key (Codex CLI's
PKCE flow in `gamma/chatgpt_oauth.py`; entries created only via
`POST /api/ai/oauth/chatgpt/start`+`complete` — the user pastes the
localhost:1455 callback URL since nothing listens there; access tokens refresh
lazily in `ai_runtime`). Its wire is the Responses API on
`chatgpt.com/backend-api/codex` (stream-only SSE; non-stream callers join
deltas), and PDF attachments go as native `input_file` parts with an automatic
retry as extracted text if the backend rejects them.

## Chat endpoint

`/api/ai/chat` speaks both the Anthropic Messages API and the OpenAI Chat
Completions API. Requests carry a model-registry id, optional `effort`
(→ Anthropic `output_config.effort` / OpenAI `reasoning_effort`; omitted unless
set — some models reject it), optional `system` override, pasted `images`
(data URLs → native image content parts), and `pages`/`include_notes` for
multi-paper context. `stream: true` (the chat UI's mode) returns NDJSON lines
of `{"delta"}`/`{"error"}` parsed from the provider's SSE; upstream failures
before the first byte still return normal HTTP errors.

Context is extracted text by default (a head excerpt labelled as such when the
document doesn't fit — see [ai_context.md](ai_context.md)), or the PDF itself
as a native document/file content part when the request sets `attach_pdf`; the
built-in chat system prompt grounds paper claims in text actually read (the
model must look details up or say they're absent, never fill gaps from memory).

PDF extraction (`gamma/pdf_text.py`) is serialized behind a lock — pdfium is
not thread-safe and overlapping extractions fail both — and reads up to
`MAX_PAGES` (5000, a runaway guard that logs when it bites; pages past it are
invisible to search AND read_page, so keep it far above real documents).

When the request carries a `selection` (quoted PDF passages), the single-paper
text context is selection-centered instead of head-of-document:
`selection_context` (`ai_context.py`) locates each passage's PDF page by
normalized-text match (`_locate_passage`, page-seam aware) and spends the
budget on a small head slice plus windows starting at those pages, labeled with
their page numbers; unlocatable selections fall back to the plain head excerpt.

Reasoning models burn invisible tokens — keep `max_tokens` generous (empty
responses raise with the finish reason). `/api/ai/models` feeds the chat
panel's switchers and the prompt editor (four editable prompts: chat system,
metadata extraction, PPT citation — defaults in `ai.py` — and the library-agent
base prompt, default in `ai_tools.py`).

## Library agent internals

Human-facing doc: [agent.md](agent.md) — keep it in sync. Every chat declares
an `agent_scope` — `"folder"` (home/folder view; `folder` = current path, `""`
= root) or `"page"` (paper view; `page_id` = the focused page) — plus
`permissions` (the Settings → Assistant → Folder agent per-tool toggles —
localStorage JSON `gamma-ai-agent-perms`, missing key = allowed) and optional
`agent_system` (custom base prompt; the Prompts pane's "Library agent" entry,
default `ai_tools.AGENT_PROMPT` via `/api/ai/models`).

`gamma/ai_tools.py` is a registry (`TOOLS`): each tool declares its wire spec,
permission key, allowed scopes, mutating flag, and executor — arming is
`agent_tools(scope_type, perms)`, dispatch is
`run_agent_tool(user, scope, name, args)`, and the scope check
(`_load_scoped_page`/`_scope_docs`: folder = tag prefix match, page = id
equality) is shared by all executors.

The tools:

- `list_pages` (folder only; optional `label`/`folder`/`title_contains`
  filters, or `list_labels: true` for the label/folder vocabulary with counts)
- `read_page` (page_report_section excerpt + notes, `pdf_chars` per call capped
  by the Settings → Assistant "Read window" preference (`gamma-ai-read-chars` →
  request `read_char_limit`, rides in the scope dict as `read_chars`, default
  20000 — `agent_tools` formats the cap into the armed spec), `pdf_page` starts
  the excerpt at a 1-based PDF page (extract_text's start_page — how a
  search_pdfs hit is followed up) and `pdf_offset` windows onward from there —
  the excerpt names the next offset while text remains; both scopes)
- `search_pdfs` (in-scope FTS snippets via the routers.search helpers, kicks
  background indexing; a zero-hit query is retried with its longest words only
  and the result labelled approximate — the strict AND query otherwise reads as
  "the paper is silent"; both scopes)
- `rename_page` + `move_page` (folder only)

Folder semantics mirror
[frontend/src/libraryUtils.js](../../frontend/src/libraryUtils.js); keep them
in sync. Everything off (or no/invalid scope) = plain chat; un-armed tools are
also refused at execution. Deliberately no delete, no note/highlight edits, and
no flat-label writes (folder labels change only through move_page) — every
action is reversible.

### The tool loop

The router runs a tool loop (`agent_events`; rounds default 32, user-tunable
1–100 via `tool_rounds` from Settings → Assistant → "Tool rounds"
(`gamma-ai-tool-rounds`); ≤200 mutations — runaway guards, not workload caps;
the Responses builders enable `parallel_tool_calls` when tools ride along so
bulk renames batch per round) over `ai_client.sse_events`, which parses tool
calls from all three protocols' SSE (tool defs + `tool_calls`/`role:"tool"`
message extensions are translated per wire in the request builders).

Every tool call streams back as an
`{"action": {kind, summary, tool, args, result}}` NDJSON line (kinds
list/read/search/rename/move, plus `error` with `error: true` for
failed/blocked calls) that the chat renders as a chip and saves in the message
— clicking a chip expands the arguments and the (truncated, `_DETAIL_CAP`)
output the model got; only applied rename/move count against
`MAX_TOOL_ACTIONS` and trigger the home-feed refresh (`onLibraryChange`).

On agent requests, `build_messages(..., with_tools=True)` (`ai_context.py`)
replays each saved reply's recorded actions as assistant `tool_calls` +
`role:"tool"` turns, so the model keeps prior tool results across turns instead
of re-listing; results share `TOOL_REPLAY_BUDGET` chars newest-first (older
ones elided), and `_anthropic_messages` folds a plain user turn into a
preceding tool_result turn to keep roles alternating. Plain chats never replay
(providers reject tool blocks without tool defs).

OpenAI-protocol calls that carry tools are rerouted to the platform
`/v1/responses` (`wire_protocol`) — gpt-5.x rejects function tools on chat
completions — but only against the official api.openai.com base URL; custom
gateways keep chat-completions tools.

## Chat history buckets

Focused page id in the paper view, `home` at the library root,
`home:<folder path>` per folder — the `/api/chats/{block_id:path}` routes take
the `:path` converter for the nested keys, and folder rename/move/delete calls
`POST /api/chats/folder-rename` ({src, dst}; dst "" deletes) BEFORE rewriting
the tags so the destination bucket exists when ChatDock reloads (a destination
holding a real conversation wins; empty save-echo rows are overwritten).
