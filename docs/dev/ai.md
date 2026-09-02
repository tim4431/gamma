# AI: providers, chat, and the library agent

The AI stack in one place — provider storage and protocols, the `/api/ai/chat`
request/stream shape, and the library agent: what it can reach, how the tool
loop runs, and what the user controls. The tools themselves are catalogued in
[ai_tools.md](ai_tools.md); how long papers reach the model is
[ai_context.md](ai_context.md). Code: `gamma/ai_settings.py`,
`gamma/ai_client.py`, `gamma/ai_context.py`, `gamma/ai_tools.py`,
`gamma/chatgpt_oauth.py`, `gamma/routers/ai.py` + the chat-history router.

## Provider and models

There are NO env API keys; providers are per-user GUI entries (Settings →
Provider and models) stored under the reserved `ai-settings` prefs key in the user's
`data.db` — a LIST of `{id, name, protocol, api_key, base_url, models}` managed
via `POST/PUT/DELETE /api/ai/providers[/{id}]`. The generic prefs endpoints
refuse the key; the only read path is the masked `GET /api/ai/settings` (last-4
hint, never the key), guests can't write. `POST /api/ai/providers/{id}/test`
probes an entry with a tiny live completion for the settings list's Test button
— result in-body, never an HTTP error, and it clears the OAuth refresh backoff
so an expired ChatGPT grant is re-tried immediately. The probe's model:
the entry's optional `test_model` (editable in the form's Models step), else
the `model` sent with the request (the client passes its effective metadata
model — the cheap utility model), else the entry's first model. A failed probe
carries an `auth` flag on 401/403 so the row renders
"sign-in expired — reconnect" instead of the upstream body. Upstream error
details are summarized before display everywhere (`upstream_detail` in
`ai_client.py`): JSON bodies reduce to their message field, HTML error pages
(a proxy's 502 page) to their `<title>`.

`POST /api/ai/health` ({provider_id, mode}; "" = first entry) is the login
connection check (Settings → Provider and models → "Check at login",
localStorage `gamma-ai-login-check`, default on): mode `"ping"` verifies the
credential for free — OAuth entries hit the usage endpoint, API keys list
`/v1/models`, both 401 on a dead credential (404/405 = gateway without a
listing → ok-but-unverified, no false alarm) — and `"test"` runs the same tiny
completion as the Test button. The answer is always in-body
(`{configured, ok, auth?, error?}`); a failure renders as a warning strip in
the chat window ("authentication is broken — sign in again"), dismissed or
cleared by a passing Test / provider edit.

`ai_runtime(user)` in `gamma/ai_settings.py` builds the per-request config and
model registry (ids are `<entryId>:<model>`; the wire format comes from the
entry's `protocol`, never from the provider id) — AI endpoints must use it, not
module-level config constants for credentials or model routing. Env vars set
each protocol's administrator-controlled default base URL, including
`GAMMA_AI_CHATGPT_BASE_URL`.

The Provider and models pane also exposes `POST /api/ai/providers/{id}/usage`. For a
ChatGPT OAuth entry it reads normalized subscription rate-limit windows
(`used_percent`, `remaining_percent`, and reset time) without exposing the
bearer token. Opening the pane queries OAuth usage automatically (the Usage
button remains available for a manual refresh). Each window is one compact
summary line above the same progress meter used for storage quota, filled by
the percentage used. Each provider row also opens its model configuration,
where models can be fetched, added, or removed.
API-key protocols return an explicit unavailable result because OpenAI-
compatible and Anthropic-style providers do not share a portable quota API.
An expired ChatGPT sign-in answers in-body (`{available: false, auth: true}`
with a "sign in again" reason) rather than dumping the upstream 401.
The account request deliberately uses the administrator-controlled ChatGPT
protocol URL, not an entry field, and OAuth entries cannot edit their API key
or base URL; this prevents a settings request from redirecting a bearer token.
The ChatGPT account endpoint is provider-specific and may require maintenance
if its upstream contract changes.

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

Whatever went to the model is reported back: the stream's first line is
`{"context": [...]}` (non-stream: a `context` field) with one entry per
document — `title`, `doc_id`, `native` (the file itself was sent),
`native_requested`, `partial`, `chars`, `pages`, `pages_shown` (uploaded
`files` are reported the same way, and get the single-paper budget when they
fall back to text). The chat saves it on the reply and shows a chip only when
it matters: "Model saw pages 1–9 of 22" for a truncated paper, "PDF file not
accepted — sent as text" when the file was requested but the provider took
text instead. `/api/ai/models` marks each model `native_pdf` (false for
ChatGPT sign-in entries: their wire is the Codex backend, which refuses
`input_file` parts). The chat's PDF button doesn't default on for such a
model; switching it on by hand shows a warning pill, and pending uploaded
PDFs get the same warning on their chips.

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

## The library agent

The chat is more than a chatbot: it can act on the library through tools the
server executes on its behalf ([ai_tools.md](ai_tools.md) describes each one).
What it can reach depends on where the chat is opened — every chat declares an
`agent_scope`:

- `"folder"` — the home/folder chat (`folder` = current path, `""` = root):
  tools reach the pages in that folder.
- `"page"` — the paper chat (`page_id` = the focused page): tools reach only
  that paper — the reading tools plus the note-block editors; the page-level
  organizers (list/rename/move) don't exist there.

The request also carries `permissions` (the effective per-chat tool choices,
initially based on Settings → Assistant → Folder agent — localStorage JSON
`gamma-ai-agent-perms`, missing key = allowed, so new tools default on) and
optional `agent_system` (custom base
prompt; the Prompts pane's "Library agent" entry, default
`ai_tools.AGENT_PROMPT` via `/api/ai/models`). The scope and permission lines
are always appended mechanically to the base prompt, so a custom prompt can
change the agent's style but not widen its reach. Everything off (or no/invalid
scope) = plain chat.

### Permissions and knobs (Settings → Assistant)

The overall **Enable tools** switch (`gamma-ai-agent-enabled`, default on)
disables tool use everywhere. The "Folder chats"/"PDF chats" toggles set each
scope's per-chat default (`gamma-ai-folder-tools-default` /
`gamma-ai-pdf-tools-default`; folder on, PDF off). The Tools
button (sliders icon) in each folder/PDF chat header toggles the configured tool set for that
chat only. New chat resets the switch to the Settings default. The chat
header's ⚙ popover also carries a Tools section — the per-chat switch plus the
same per-tool permission rows Settings shows (filtered to the chat's scope,
editing the same stored map; `AGENT_PERM_ROWS` in `settings.jsx`).

One permission per capability: List pages, Read papers & notes, Read note
blocks, Search PDF text, Rename pages, Move pages, and Edit note blocks (one
toggle arming `edit_block`/`create_block`/`move_block` together). Folder scope
offers all of them; PDF scope exposes the reading tools and the note-block
editors. Plus:

- **Tool rounds** (`gamma-ai-tool-rounds` → request `tool_rounds`, default 32,
  user-tunable 1–100) — provider round-trips one message may use.
- **Read window** (`gamma-ai-read-chars` → request `read_char_limit`, default
  20 000) — the most document text one `read_page` call may return; long
  papers are read in windows of this size.

Rounds and the ≤200-mutation ceiling are runaway guards, not workload caps.

### The tool loop

The router runs a loop (`agent_events`) over `ai_client.sse_events`, which
parses tool calls from all three protocols' SSE (tool defs +
`tool_calls`/`role:"tool"` message extensions are translated per wire in the
request builders; the Responses builders enable `parallel_tool_calls` when
tools ride along, so bulk renames batch per round): the model calls tools →
the server executes them → results go back → repeat until it answers.

Every tool call streams back as an
`{"action": {kind, summary, tool, args, result}}` NDJSON line (kinds
list/read/search/rename/move/edit/create, plus `error` with `error: true` for
failed/blocked calls) that the chat renders as a chip and saves in the message
— clicking a chip expands the arguments and the (truncated, `_DETAIL_CAP`)
output the model got; only applied mutations count against
`MAX_TOOL_ACTIONS` and trigger the home-feed refresh (`onLibraryChange`), and
the note-block tools' actions carry `page_id`/`src_page_id` so the frontend
reloads the open page's block tree when the AI touched it (`onNotesChange`;
skipped when the user typed during the reply — their queued autosave wins).

### Replay across turns

On agent requests, `build_messages(..., with_tools=True)` (`ai_context.py`)
replays each saved reply's recorded actions as assistant `tool_calls` +
`role:"tool"` turns, so the model keeps prior tool results across turns
instead of re-listing; results share `TOOL_REPLAY_BUDGET` chars newest-first
(older ones elided), and `_anthropic_messages` folds a plain user turn into a
preceding tool_result turn to keep roles alternating. Plain chats never replay
(providers reject tool blocks without tool defs).

OpenAI-protocol calls that carry tools are rerouted to the platform
`/v1/responses` (`wire_protocol`) — gpt-5.x rejects function tools on chat
completions — but only against the official api.openai.com base URL; custom
gateways keep chat-completions tools.


## PDF translation

`POST /api/ai/translate` backs the viewer's translated view. ONE 文A button
in the PDF zoom column does everything by state: click translates the
current page when nothing is translated yet, toggles show/hide for ALL pages
once translations exist under the current language+model (switching either
in Settings makes the button translate afresh; hidden = slashed icon;
holding Alt peeks), and
halts a running job; right-click (long-press on touch) opens the option
menu — Translate this page / Translate whole document / Show
original·translation (Stop translating while running). A whole-document job
queues pages nearest the current page first (forward before backward at
equal distance), so the page being read paints immediately. The queue lives
in `pdfViewer.jsx` (`translateCtl`), producer/consumer style: the producer
segments queued pages in order and feeds one flat list of ~6-paragraph /
1200-char chunks, while N workers (Settings → Reading → parallel requests,
typed, 1–32) stream through it across page boundaries — the first request is
in flight while later pages are still segmenting, chunks paint as they land,
char-weighted progress shows under the button and as a background-tasks row.
Halting aborts the in-flight requests (each job carries an AbortController)
and keeps finished chunks; re-running skips done pages and re-fills partial
ones from the server cache. Reliability: each chunk gets one client-side
retry, and the server salvages a miscounted model reply ("expected 5, got
4") by re-translating that batch paragraph by paragraph, concurrently — a
paragraph that still fails comes back verbatim (shown as original, uncached)
instead of failing the request.

Geometry never leaves the client: `frontend/src/pdfTranslate.js` segments
pdf.js text runs into paragraph blocks (columns via whitespace-river
detection, paragraphs via indents/font changes, figure-wrap via sustained
width changes; math-heavy/numeric blocks are skipped), each carrying
PER-LINE rects. The overlay masks exactly those original lines (plus the
leading between them) and lays the translation over them with an inline
cloned background — so figures a paragraph brushes against are never painted
over, and the layout never moves. Translated text is selectable/copyable;
while shown, the invisible original text layer stands down.

Targets are the allowlisted `TRANSLATE_LANGS` codes (mirrored in
`frontend/src/prefs.js`); model and reasoning `effort` come from Settings →
Reading (model follows the chat model by default; effort omitted unless
picked — Low/Minimal is the speed lever for reasoning models); the whole
Translation section can be switched off there too. The server keeps an
**in-memory only** LRU (~5k entries, lock-guarded — requests run in the
threadpool) per (user, language, bare model name, source text) —
deliberately nothing on disk; it makes halts/retries/re-shows free until a
restart. Duplicate paragraphs within a request go upstream once. Caps: 200
texts / 60k chars per request.

## Chat history buckets

Focused page id in the paper view, `home` at the library root,
`home:<folder path>` per folder — each folder keeps its own conversation, and
switching folders re-scopes the next message. The
`/api/chats/{block_id:path}` routes take the `:path` converter for the nested
keys, and folder rename/move/delete calls `POST /api/chats/folder-rename`
({src, dst}; dst "" deletes) BEFORE rewriting the tags so the destination
bucket exists when ChatDock reloads (a destination holding a real conversation
wins; empty save-echo rows are overwritten) — folder conversations follow
renames and moves, and are deleted with their folder.
