# AI: providers, chat, and the library agent

The AI stack in one place — provider storage and protocols, the `/api/ai/chat`
request/stream shape, and the library agent: what it can reach, how the tool
loop runs, and what the user controls. The tools themselves are catalogued in
[ai_tools.md](ai_tools.md); how long papers reach the model is
[ai_context.md](ai_context.md). Code: `gamma/ai_settings.py`,
`gamma/ai_protocols/` (one adapter per wire), `gamma/ai_client.py`,
`gamma/ai_catalog.py`, `gamma/ai_context.py`, `gamma/ai_tools.py`,
`gamma/chatgpt_oauth.py`, `gamma/routers/ai.py` + the chat-history router.

## Provider and models

There are NO env API keys; providers are GUI entries: each account's own
(Settings → AI › Connections), plus the server's shared ones an admin adds
(below). An account's entries are stored under the reserved account-wide
`ai-settings` pref in `users.db` — a LIST of `{id, name, protocol, api_key, base_url, models}` managed
via `POST/PUT/DELETE /api/ai/providers[/{id}]`. An entry offers exactly the
models picked for it (from the provider's live listing in the form): there is
no built-in default model, so an entry with none picked offers nothing and its
Test button says so (migration step 15 wrote the old defaults into entries
that had relied on them). A saved entry never switches between sign-in and
API key (`PUT` with such a protocol is a 400). The generic prefs endpoints
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
connection check (Settings → AI › Connections → "Check at login",
account pref `gamma-ai-login-check`, default `ping`): mode `"ping"` verifies the
credential for free — OAuth entries hit the usage endpoint, API keys list
`/v1/models`, both 401 on a dead credential (404/405 = gateway without a
listing → ok-but-unverified, no false alarm) — and `"test"` runs the same tiny
completion as the Test button. The answer is always in-body
(`{configured, ok, auth?, error?}`); a failure renders as a warning strip in
the chat window ("authentication is broken — sign in again"), dismissed or
cleared by a passing Test / provider edit.

`ai_runtime(user)` in `gamma/ai_settings.py` builds the per-request config and
model registry from the account's own entries followed by the server's shared
ones (ids are `<entryId>:<model>`; the wire format comes from the entry's
`protocol`, never from the provider id; the default model is the account's
own first model, else the server's first) — AI endpoints must use it, not
module-level config constants for credentials or model routing. Env vars set
each protocol's administrator-controlled default base URL, including
`GAMMA_AI_CHATGPT_BASE_URL`.

Named services (`SERVICES` in `gamma/ai_protocols/__init__.py`, sent as `services` with the
settings) are form presets: a protocol plus a fixed endpoint, listed in the
form's service menu between the protocols and "Custom endpoint". DeepSeek is
the `openai` protocol at `https://api.deepseek.com`. An entry made from one
stores only protocol + base URL; `provider_label` recognizes the pair and
names the entry after the service when it has no name of its own. The
`openai` wire follows the endpoint (`is_openai_platform` in `ai_protocols/openai.py`):
only OpenAI itself gets `max_completion_tokens`, the Responses API for tool
calls, and the gpt-/o-family filter on its model listing. Compatible servers
get `max_tokens`, Chat Completions tools and their full listing (minus
embedding/audio/image models). Anthropic subscription sign-in is deliberately
absent: Anthropic's terms forbid third-party apps from routing requests
through Free/Pro/Max plan credentials, so Claude is reached with a Console API
key.

The Connections pane also exposes `POST /api/ai/providers/{id}/usage`. For a
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

### Protocol adapters

Everything that differs between providers lives on one adapter per wire in
`gamma/ai_protocols/` (`base.Protocol`), and nothing outside that package
branches on a protocol id. Routes, the chat loop, `ai_settings` and
`ai_client` ask the entry's adapter (`ai_protocols.of(conf)`):

| Concern | Adapter member |
|---|---|
| what the form offers | `label`, `base_url` (env default, `config.AI_BASE_URLS`), `auth` (`"key"` / `"oauth"` + the `oauth` module that refreshes tokens), `entry` |
| the chat call | `wire(conf, tools)` (a sibling wire for some calls), `request(...)`, `reply_text`, `read_reply`, `streams_only` |
| the stream | `events` (one loop in the base) over `stream_event` / `stream_end` |
| token counts | `usage(raw)` → `{input, output, cache_read, cache_write}` |
| models | `models_request`, `models(data, conf)` → `[{id, context_window}]`, `catalog_hints` |
| credential check | `ping_request` (default: the model listing) |
| quota | `has_account_usage`, `account_usage_request`, `account_usage` |
| attachments, dictation | `native_pdf`, `transcription` (a rank), `transcription_request`, `transcript` |

The wires: `anthropic.py` (Messages API), `openai.py` (Chat Completions for
OpenAI and every compatible server), `responses.py` (the Responses API that
OpenAI's platform and the ChatGPT backend both speak; `openai-responses` is
the variant an OpenAI entry switches to for tool calls, never an entry's own
protocol), `chatgpt.py` (the Codex backend: its listing, quota and client
version). `ai_client.py` is the transport (open, read, stream, errors);
`ai_catalog.py` fetches and caches listings and context windows
(`fetch_json` is the one fetch every listing, quota and ping goes through).
The sign-in flow itself (`chatgpt_oauth.py`, `/api/ai/oauth/chatgpt/*`) is
provider-specific by nature.

A new service on an existing wire (a gateway, a hosted model) is a
`SERVICES` preset or just a custom base URL. A new wire is one module
subclassing `Protocol` (or `ResponsesWire`) that overrides what differs from
the OpenAI-shaped defaults, plus one line in `WIRES` and its default URL in
`config.AI_BASE_URLS`; `tests/test_ai_wire.py` pins each wire's request and
stream shapes.

### Shared provider entries

An admin can add provider entries for the whole server (Settings → Server →
Shared AI provider, `/api/admin/ai-providers*`), so the members of a lab do
not each need a key. A shared entry has an account entry's shape (`id, name,
protocol, api_key, base_url, models, test_model, created_at`, plus `oauth`
for a sign-in): an API key, or a ChatGPT subscription the admin signs in to
from the same form (`POST /api/admin/ai-providers/chatgpt/start` +
`complete`, the account flow's `begin_chatgpt_signin` /
`redeem_chatgpt_signin` with the state bound to `("server", <admin>)`, so
neither side's state redeems on the other; `provider_id` on `complete`
reconnects an entry). The list lives in the users.db `settings` KV under
`ai_providers` as `{providers: [...], guests: bool, allowance: {accounts,
guests}}`, at most `MAX_PROVIDERS` (20) entries, each `api_key` and each
sign-in's `oauth` tokens Fernet-encrypted with the data directory's key the
way the cloud client secret is (`publisher_sessions.cipher`); a key or a
sign-in that no longer decrypts reads as none and logs a warning. A shared
sign-in's token refresh runs under the entry's own lock
(`_refreshed_server_oauth`: every account's requests refresh the same
tokens, and OpenAI rotates the refresh token) and writes only the tokens
back. Its refresh backoff is reset only by an admin's Test, usage query or
login check, so a dead shared grant is not retried on every account's
login. The signed-in e-mail (`account`) is masked like the key hint: admins
only. The same helpers validate both
lists (`new_key_entry`, `update_entry`, `apply_provider_fields`,
`mask_entry` in `ai_settings.py`).

Ids are namespaced, `server:<id>`, so a shared entry's models
(`server:<id>:<model>`) never collide with an account's; the registry marks
them `shared: true`. `ai_runtime` (via `shared_access`) offers them to
every account after its own entries. Guest accounts get them only while
the admin switch `guests` is on (default off); a name that is not an account
(a link visitor) never does. `GET /api/ai/settings` lists them after the
account's own as read-only rows (`shared: true`), with the last-4 key hint
for admins only; `/api/ai/providers/{id}` never edits or deletes them (404).
An admin may name a shared id on the Test probe, the model catalog and a
sign-in's subscription usage (`/api/ai/providers/{id}/usage`), which is how
the Server section's form lists models and tests a saved entry; the
login check (`/api/ai/health`) accepts any entry the account can use. Token
usage stays per account: a member's calls through a shared entry are
recorded on that member (provider id `server:<id>`), and there is no
server-wide meter.

**Shared entries and the allowance.** The admin may cap what each account
spends through the shared entries: `allowance: {accounts, guests}` in the
same config (tokens, input + output, per account per rolling 24 hours; 0 =
unlimited, the default; `GET/PUT /api/admin/ai-providers`, either key alone).
`ai_usage.shared_used` sums the account's `server:` rows in the window;
`ai_runtime` reports `allowance: {limit, used, exhausted}` whenever a
shared entry is in the runtime (limit 0 = unlimited, never exhausted; null
when none is — guests take the guests' limit, everyone else the accounts')
and, under a limit, puts `allowance: {user, limit}` on each shared provider
conf. The one choke point is `ai_client.open_ai`
(`call_ai` goes through it): `check_allowance` re-reads the count on every
call and raises `AllowanceExhausted`, an `HTTPException` 429 whose detail
names the used and limit tokens and points at Settings → AI. Chat (both
modes; the stream opens eagerly, so a refused first call is a real 429, and
a later agent round ends the stream with the detail as its `error` line),
translation (the stream variant checks before it starts), metadata
extraction and `/metadata/cite` let it through as a 429; the Test probe and
the login test report it in-body. Dictation reports no tokens, so it is not
metered, only refused once the allowance is used up. Model listings and the
context-window lookup spend nothing and are never refused; the shared models
stay listed. Own entries are never marked, so never metered. A Usage reset
keeps the rows the allowance still counts. `GET /api/ai/models` and
`GET /api/ai/usage` carry the same `allowance` object. The contract (guests,
demo mode): [guests.md](guests.md).

### The chatgpt protocol (OAuth)

A third protocol, `chatgpt`, holds OAuth tokens instead of a key (Codex CLI's
PKCE flow in `gamma/chatgpt_oauth.py`; entries created only via
`POST /api/ai/oauth/chatgpt/start`+`complete` — the user pastes the
localhost:1455 callback URL since nothing listens there; access tokens refresh
lazily in `ai_runtime`). Its wire is the Responses API on
`chatgpt.com/backend-api/codex` (stream-only SSE; non-stream callers join
deltas), and PDF attachments go as native `input_file` parts with an automatic
retry as extracted text if the backend rejects them. That retry applies to
any provider that answers a native-PDF request with a 4xx other than
401/403/429 (compatible servers may refuse `file` parts too). Anthropic has
no `minimal` effort; its adapter sends `low` for it.

Its model list (`POST /api/ai/model-catalog`) is Codex CLI's own listing call,
`GET {base}/models?client_version=…`, made with the entry's token. The backend
hides models newer than the client version it is told, so Gamma claims the
newest Codex CLI release: npm's `latest` for `@openai/codex`, cached for 6 h
(`codex_client_version` in `ai_protocols/chatgpt.py`; on a failed lookup the last good version, else a
floor constant, with a retry after 10 min). No model names are hardcoded. A
failed listing is a 502 the picker shows, and a fresh connect whose listing
fails starts with no models. The sign-in `state` belongs to the account that
started it. Token refreshes are serialized per account and re-read the
entries first (`_refreshed_oauth` in `ai_settings.py`): OpenAI rotates refresh
tokens, so of two parallel refreshes the second would fail and save stale
tokens over the fresh ones.

## Chat endpoint

`/api/ai/chat` speaks both the Anthropic Messages API and the OpenAI Chat
Completions API. Requests carry a model-registry id, optional `effort`
(→ Anthropic `output_config.effort` / OpenAI `reasoning_effort`; omitted unless
set — some models reject it), optional `system` override, pasted `images`
(data URLs → native image content parts), and the context PAGES: `pages`
(several — a report across pages) or, when empty, the one page of `page_id`
(the open page). A page's PDF attachment is derived server-side
(`blocks_store.page_attachment`) — `doc_id` is still accepted as a
compatibility input: it resolves to the page carrying that PDF
(`blocks_store.page_for_doc`) and does nothing when no page does; send
`page_id`, nothing new may depend on `doc_id`. `stream: true` (the chat UI's
mode) returns NDJSON lines of
`{"delta"}`/`{"error"}` parsed from the provider's SSE; upstream failures
before the first byte still return normal HTTP errors. Every AI NDJSON
stream (chat, the tool loop, translation) runs through `keepalive_lines`
(`routers/ai.py`), which pumps the source generator from a worker thread.
A `{"ping": 1}` line goes out after 15 s of silence, so a reverse proxy's
idle timeout (nginx and Synology default to 60 s, Cloudflare to 100 s) does
not cut the response while the model thinks over a long context.
Clients skip `ping`. A consumer that leaves before the source
ends (Stop, or the connection dropped anyway) stops the source at its next
yield and logs a warning with the elapsed time. The client turns a
failure with no reply text into an AI message carrying `error: true` — shown
as an error bubble, saved with the chat so it survives a reload, but left out
of the `history` it sends on later turns, and `build_messages` skips such
items too should an older client send them.

Context is *pages from the user's knowledge base* (`ai_context.gather_inputs`
→ `page_report_section`): each page contributes its title, a properties line
(folders, labels, cached metadata, web source, attachment) and its notes
tree; a page that carries a PDF adds the document's extracted text (a head
excerpt labelled as such when the document doesn't fit — see
[ai_context.md](ai_context.md)), or the PDF itself as a native document/file
content part when the request sets `attach_pdf`, and shows its notes only
with `include_notes`. A page without an attachment IS its notes, so they
always go — `include_notes` only means "also add my notes/highlights for PDF
pages". The built-in chat system prompt frames the model as working inside
that knowledge base and grounds claims about the pages in text actually read
(look details up or say they're absent, never fill gaps from memory; cite a
PDF by page number, say when something comes from the user's notes). With a
document in context, `_CITATION_PROMPT` is appended, custom prompt or not.
It asks for `[p. N](/?page=<id>&pdf_page=N&quote=…)` links built from the
`[PDF page N]` labels and the `Gamma page ID` each context section carries
([pdf_citations.md](pdf_citations.md)).

Whatever went to the model is reported back: the stream's first line is
`{"context": [...]}` (non-stream: a `context` field) with one entry per
page — `title`, `doc_id` (`""` for a page without a PDF), `native` (the file
itself was sent), `native_requested`, `partial`, `chars`, `pages`,
`pages_shown` (uploaded `files` are reported the same way, and get the
single-page budget when they fall back to text). The chat saves it on the
reply and shows a chip only when
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

### Selected PDF passages

A PDF selection reaches the chat with its position. App keeps
`pdfSelections` as `{text, page, box}` (`pdf/pdfSelectionSpot.js`:
`rangeSpot` for a live selection, which gives the page its start sits on and
the union of its rects there, and `highlightSpot` for a clicked highlight's
stored position). `box` is `[x0, y0, x1, y1]` as fractions of the page,
top-left origin. The request sends them as `selections`, at most 6 × 4000
chars. The older `selection` string ("---"-joined text) is still read when
`selections` is empty (`ai_context.request_selections`).

The single-paper text context then centres on the passages instead of the
start of the paper (`selection_context`):

- **Placing.** Each passage is matched on normalized text
  (`_locate_passage`, page-seam aware), on the viewer's page first and then
  anywhere. A phrase the paper repeats therefore lands where it was
  selected. A passage whose text isn't found (a formula's glyph soup, or
  anything under 12 chars) still gets the viewer's page, placed by its box's
  top.
- **Window.** The budget goes to a small head slice (dropped when a window
  already reaches the top) plus one window per passage. The window opens up to
  2500 chars *before* the passage, where its set-up and definitions are,
  crossing into the previous page when needed. Each page part carries its
  `[PDF page N]` label. Passages inside an earlier window share it.
- **Section.** Each passage is labelled with the section it falls under.
  The PDF's own outline is read first (`pdf_text.outline`): the path of
  entries before the spot, with figure bookmarks and a lone title entry
  left out. An entry on the spot's own page counts only when its title
  stands as a heading line before it (`_title_offset`), so the word
  "Attention" in the prose is not the "3.2 Attention" heading. Without an
  outline, the nearest heading-shaped line before the spot is used: the
  numbered, Roman-numeral, lettered and named-section shapes (`_heading_line`,
  which rejects reference entries, tables of contents and body lines like
  "852 nm").
- **Picture.** When a passage's text wasn't found, or reads as a formula or
  table (`text_unreliable`: math symbols, private-use glyphs, many
  one-character tokens), `selection_crops` renders its box from the PDF
  (`pdf_text.render_page(..., box=)`), grown to a readable strip and padded.
  At most 3 pictures per message; they ride with the user's own images.

The question labels each passage "Selected passage (PDF page 7; section
"Methods › Noise model"; a picture … is attached)" (`final_prompt`, from the
located entries `gather_inputs` puts in the open paper's coverage as
`selection: {passages: [{page, section, found, crop}]}`). The reply's chip
reads "Model saw text around p. 7 · Methods › Noise model", plus "Picture of
the selection sent" when one went. Nothing placed at all falls back to the
plain head excerpt. With a native PDF attachment there is no window and no
picture, and the passages carry the viewer's page only.

### Mentioning library papers

`chat/PaperMentionInput.jsx` owns the picker. `chat/paperMentions.js` owns mention text edits
and `MAX_CHAT_REFERENCES`, shared with `chat/ChatDock.jsx`. The six-reference UI limit
mirrors the API's seven-page limit (`pages`, de-duplicated server-side),
leaving one slot for the current page.

Type `@` in the chat composer to search library titles with the same ranking,
typo tolerance, and separator matching as library search (`library/librarySearch.js`).
Arrow keys choose a result; Enter or Tab attaches it, Escape dismisses the
query, and clicking or tapping a result also works. Results include author,
year, venue, and folder details. A completed mention inserts the title and
adds a removable context chip; the chip controls which page IDs are sent.
The `+` menu offers the same library search.

References persist for follow-up questions and are saved as `contextPages`
on each user message. Loading a conversation restores its last references;
editing an earlier message reuses that message's references. Each contributes
its title, metadata, summary, and PDF excerpt (or native PDF), with optional
notes/highlights. The multi-paper text budget is shared by valid pages; a
selection still centers the open paper's excerpt. Tool-enabled chats also
receive document maps labelled with page IDs.

Explicit references expand `read_page`, `read_block`, and `search_library`
access within the current workspace, even outside the original page/folder.
They never expand the editing scope. PDF search hits identify their Gamma
page IDs so the assistant can read further or cite the matching paper,
including when titles are identical. References are resolved server-side;
missing pages and non-page blocks are ignored.

### Pointing the chat at notes

Three optional request fields say what the message is about inside the
notes. The server resolves all three against the request's context pages.

- `focus_block_id` — the block row the cursor is on (`focusedId` in
  `app/App.jsx` → `focusedNote`). The chat shows it as a "Cursor" chip, like a
  PDF selection, and sends it with every message; the chip's × leaves it out
  until the cursor moves to another block. Its text and sub-blocks enter the
  context as an id-labelled outline ("The user's cursor is on this note
  block …"), and the agent prompt says "this block" / "here" mean that id.
  So *"expand this"* edits the right block without a `read_block` first.
- `context_blocks` — ids of blocks attached as chips: Ctrl+click a block
  row, or the ⋮⋮ handle menu's **Add to chat** (`onAddToChat` → `chatNotes`
  in App). Each is served as `[id] text` with its subtree indented
  (`ai_context.notes_focus_section`, the same form `read_block` uses), and
  the agent prompt lists the ids, so *"rewrite these"* means them. Capped at
  12 chips / 12k chars (`MAX_CONTEXT_BLOCKS`, `MAX_BLOCK_SECTION_CHARS`).
  Ids outside the context pages, and page ids, are dropped silently.
- `note_selections` — selected note text as exact ranges of block sources,
  `[{block_id, from, to, text}]`; `text` is the source slice the client saw.
  Two sources feed it:
  - The open editor's selection. A plain drag on a rendered note opens the
    editor and keeps selecting in the raw source. App's `noteSel` (settled
    120 ms after the last change) turns the Cursor chip into a Selection
    chip. It survives the editor closing when the chat input is clicked, and
    is dropped once sent.
  - A Ctrl+drag across rendered text. `sourceRangeOfSelection`
    (`editor/clickToSource.js`) maps both ends back to source offsets and
    widens an end inside a formula to the whole formula. A selection it
    can't place attaches its block instead.

  `ai_context.request_note_selections` validates them and labels them S1,
  S2… (6 max). The prompt quotes each with its block id, and each
  selection's block rides along whole in the notes-focus section. Both the
  prompt and the agent prompt say that a question is answered in the chat,
  while an instruction that transforms the selection (rewrite, fix,
  translate, …) is an in-place `edit_block` mode `"selection"`. Without that
  rule a small model answered "translate this" with the translation in its
  reply.

Chips render in the composer's chip strip next to PDF passages, each kind
an icon (text cursor = cursor block, highlighter = selected note text,
outline = attached block, quote = PDF passage). They clear on send and on a
page switch, since the ids belong to the page. Ctrl+click on a highlight card sends the quote as a PDF
passage, not a block chip.

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
- `"page"` — the page chat (`page_id` = the focused page, with or without a
  PDF): tools reach only that page — the reading tools plus the note-block
  editors; the page-level organizers (list/rename/move) don't exist there.

The request also carries `permissions` (the tool map of the chat's KIND —
see below; missing key = allowed, so new tools default on) and
optional `agent_system` (custom base
prompt; the Prompts pane's "Library agent" entry, default
`ai_tools.AGENT_PROMPT` via `/api/ai/models`). The scope and permission lines
are always appended mechanically to the base prompt, so a custom prompt can
change the agent's style but not widen its reach. Everything off (or no/invalid
scope) = plain chat. Among those mechanical lines: with a reading tool armed,
the model is asked to link the pages it refers to as `[title](/?page=<id>)`,
which the chat opens in place (details in [ai_tools.md](ai_tools.md)).

### Permissions and knobs (Settings → Assistant)

The single **Enable tools** switch (`gamma-ai-agent-enabled`, default on)
governs tool use in every chat; every chat starts with tools on. The Tools
button (sliders icon) in each folder/PDF chat header toggles the configured tool set for that
chat only. New chat resets the switch back to on.

Which tools a chat may use is configured per chat KIND — there are three
(`CHAT_KINDS` in `app/prefDefs.js`, `CHAT_KIND_ROWS` in `settings/SettingsDialog.jsx`):

- **Folder chat** — the home/folder view (`agent_scope: "folder"`).
- **PDF chat** — a page with a PDF attached (`agent_scope: "page"`).
- **Notes chat** — a page without one (`agent_scope: "page"`).

Settings → Assistant → Tool configuration shows one row per kind whose
control is a `ToggleGroup` of icon + short-name chips (List, Read, Blocks,
Search, Rename, Move, Edit — folder scope offers all, page scope the reading
tools and the note-block editors); clicking a chip allows or forbids that
tool for every chat of the kind. The stored map is localStorage JSON
`gamma-ai-agent-perms` = `{folder, pdf, notes}` → `{list, read, block_read,
view, search, web_search, web_read, rename, move, block_edit}` (a pre-kind flat map is applied to every
kind on read). The chat header's ⚙ popover carries the same picker for the
kind of the chat it is opened in (`AgentToolPicker` in `settings/SettingsDialog.jsx`,
bound to the same map), so a change in either place is the same change.
`ChatDock` derives its kind from its props (`organizeFolder` set → folder;
else `pageAttach` → pdf; else notes) and sends that kind's map as the
request's `permissions`.

One permission per capability: List pages, Read pages, Read note blocks,
View PDF pages (`view` → `view_pdf_page`, a rendered page picture for a
scan or a figure), Search library (`search_library` — notes and PDF text; the stored key is
still `search`), Search papers online (`web_search` → `search_papers`), Fetch
documents (`web_read` → `fetch_paper`; both web tools are read-only and
described in [ai_tools.md](ai_tools.md)), Rename pages, Move pages, and Edit
note blocks (one chip arming `edit_block`/`create_block`/`move_block`
together). The "Read & search" preset (`chat/chatSettings.js` `READ_TOOLS`)
includes the two web tools and the page viewer. Plus:

- **Tool rounds** (`gamma-ai-tool-rounds` → request `tool_rounds`, default 32,
  user-tunable 1–100) — provider round-trips one message may use.
- **Read window** (`gamma-ai-read-chars` → request `read_char_limit`, default
  20 000) — the most document text one `read_page` call may return; long
  papers are read in windows of this size.

Rounds and the ≤200-mutation ceiling are runaway guards, not workload caps.

### The tool loop

The router runs a loop (`agent_events`) over `ai_client.sse_events`, which
parses tool calls from every wire's SSE (`Protocol.events`): the model calls
tools → the server executes them → results go back → repeat until it
answers. Each adapter's `request` maps the tool defs and the
`tool_calls`/`role:"tool"` turns to its wire. The Responses body enables
`parallel_tool_calls` when tools ride along, so bulk renames batch per round.

Every tool call streams back as an
`{"action": {kind, summary, tool, args, result}}` NDJSON line (kinds
list/read/view/search/rename/move/edit/create, plus `error` with `error: true` for
failed/blocked calls) that the chat renders as a chip and saves in the message
— clicking a chip expands the arguments and the (truncated, `_DETAIL_CAP`)
output the model got; only applied mutations count against
`MAX_TOOL_ACTIONS` and trigger the home-feed refresh (`onLibraryChange`), and
the note-block tools' actions carry `page_id`/`src_page_id` so the frontend
reloads the open page's block tree when the AI touched it (`onNotesChange`;
with the page's live socket up the tools' ops already arrived through it and
the reload is skipped — [collab.md](collab.md)). A `view_pdf_page` result
also carries the rendered page: the loop lifts it off the action into the
tool message's `images` before yielding the chip, so the model sees the
picture and the saved chat never holds it ([ai_tools.md](ai_tools.md)).

### Watching the agent work (live footprint)

The chat forwards every stream event to the app as it arrives
(`onAgentEvent` in `chat/ChatDock.jsx` → `handleAgentEvent` in `app/App.jsx`), so the
notes panel shows where the agent is, not just what it did.

- Actions of `read_block`/`edit_block`/`create_block`/`move_block` carry the
  `block_id` they touched. On the open page a read block pulses an accent
  ring for a moment. An edited/created/moved block gets an accent tint that
  fades over a few seconds and is scrolled into view. A whole-page read
  (`read_page`, or `read_block` on the page id) sweeps the outline: every
  row rings once, staggered top to bottom (`aiScan` → inline
  `animation-delay` per row), and the list's left edge pulses. An applied
  edit reloads the tree immediately (same guards as `onNotesChange`, plus
  never while the user has a block editor open), so the change is visible
  while the agent carries on.
- `{"progress": {tool, id, block_id (+ mode, + find for patch) | parent_id (+ after_id), content}}`
  lines preview an `edit_block`/`create_block` call the model is still
  writing. `ai_client.sse_events` yields `tool_delta` events with the raw
  argument JSON so far, on all three wires (Anthropic `input_json_delta`,
  chat-completions `tool_calls[].function.arguments`, Responses
  `response.function_call_arguments.delta`). The loop reads the target id
  and the `content` string out of the partial JSON
  (`ai_client.partial_json_object`: complete string values plus the one
  being written, decoded as far as it goes). The block types the new
  markdown in behind a caret in place of its stored text; an `append` /
  `prepend` edit keeps the stored text and types the addition at its end /
  start. For a create, a ghost row appears under the named parent after the
  named sibling. When the action lands, the reload swaps in the real block.
  Only armed edit/create tools are previewed; other tools' arguments are
  never streamed. Non-stream callers never see progress lines.

Everything is display-only: marks and previews live in App state
(`aiMarks`, `aiLive`, `aiScan` → `rowProps` → `BlockRow`/`BlockTree`),
clear on page switch and when the reply ends, and never enter the block
tree, the undo history or the op queue.

### Replay across turns

On agent requests, `build_messages(..., with_tools=True)` (`ai_context.py`)
replays each saved reply's recorded actions as assistant `tool_calls` +
`role:"tool"` turns, so the model keeps prior tool results across turns
instead of re-listing. Every replayed result is prefixed with a note
(`_REPLAYED_NOTE`): it is from an earlier turn, the notes may have changed,
call again before quoting or editing. The base prompt says the same, so a
request to read, show or check something is answered from a fresh call, not
last turn's outline (the agent's own edits change what `read_block`
returns). Results share `TOOL_REPLAY_BUDGET` chars newest-first
(older ones elided), and `_messages` in `ai_protocols/anthropic.py` folds a plain user turn into a
preceding tool_result turn to keep roles alternating. Plain chats never replay
(providers reject tool blocks without tool defs). Renamed tools replay under
their current name (`ai_context.DEPRECATED_TOOLS`, e.g. the saved
`search_pdfs` chips of old chats become `search_library` calls), and a model
that copies the old name out of that history is still served: the agent loop
and `run_agent_tool` canonicalize the name before the permission check and
dispatch, and the resulting action chip carries the current name. Old names
are never offered as tools.

OpenAI-protocol calls that carry tools are rerouted to the platform
`/v1/responses` (`OpenAIChat.wire`) — gpt-5.x rejects function tools on chat
completions — but only against the official api.openai.com base URL; custom
gateways keep chat-completions tools.


## PDF translation

`POST /api/ai/translate` backs the viewer's translated view. ONE 文A button
in the PDF zoom column does everything by state:

- On the page being read, a click translates it unless it is already fully
  translated under the current language and model. On such a page a click
  toggles show/hide for ALL pages (the viewer reports `current` next to
  `pages` in its state).
- A page a halted job left half-done counts as untranslated, so a click
  finishes it from the cache. Switching language or model in Settings makes
  the button translate afresh.
- Hidden = slashed icon; holding Alt peeks. A click during a job halts it.
- Right-click (long-press on touch) opens the option menu: Translate this
  page / Translate whole document / Show original·translation (Stop
  translating while running).

A whole-document job queues pages nearest the current page first (forward before backward at
equal distance), so the page being read paints immediately. The queue lives
in `pdf/PdfViewer.jsx` (`translateCtl`), producer/consumer style: the producer
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

The viewer requests `stream: true`. The reply is then NDJSON: `{"i":
[request indices], "text": partial}` lines as the model writes each
paragraph, then the same final `{translations, model, cached}` object a
plain call returns. `ai_client.partial_json_strings` reads the complete
elements of the half-written JSON array plus the one in progress. Lines are
throttled to ~20/s (`_TRANSLATE_STREAM_INTERVAL`), and an element maps to
every request index that shares its source text. `stream: false` (the
default) still answers in one JSON body. The salvage path never streams. An
upstream failure mid-stream is an in-band `{"error"}` line.

On the page, a paragraph whose translation is queued gets a faint accent
wash over its original lines, an in-flight one shimmers, streamed text
types onto the page behind a caret (masking the original as soon as there
is something to show), and a landed paragraph fades in. That is
`TransPending`/`TransPara` in `pdf/PdfViewer.jsx`, driven by the entry's
`queued`/`busy`/`partial` fields, which the job clears when it ends, halts
or fails.

Geometry never leaves the client: `frontend/src/pdf/pdfTranslate.js` segments
pdf.js text runs into paragraph blocks (columns via whitespace-river
detection, paragraphs via indents/font changes, figure-wrap via sustained
width changes; math-heavy/numeric blocks are skipped), each carrying
PER-LINE rects. The overlay masks exactly those original lines (plus the
leading between them) and lays the translation over them with an inline
cloned background — so figures a paragraph brushes against are never painted
over, and the layout never moves. Translated text is selectable/copyable;
while shown, the invisible original text layer stands down.

Targets are the allowlisted `TRANSLATE_LANGS` codes
(`gamma/translate_engines.py`, shared by both translation paths; mirrored in
`frontend/src/app/prefDefs.js`). What translates is Settings → Reading ›
"Translate with" (`translateModel`, a browser pref; "" is the default).
`translateModelFor` (`app/prefDefs.js`) turns the pick into what is sent:
the pick while it is still offered, the free Microsoft service when there
is no chat model at all, else the chat model. Reasoning `effort` and
parallel requests sit in the same Translation section. Effort is omitted
unless picked; Low/Minimal is the speed lever for reasoning models. The
"Translation button" switch hides the viewer's button; the selection
translator has its own switch.

The server keeps an **in-memory only** LRU (~5k entries, lock-guarded
because requests run in the threadpool) per (user, language, bare model
name, source text). Nothing goes to disk; the cache makes
halts/retries/re-shows free until a restart. Duplicate paragraphs within a
request go upstream once. Caps: 200 texts / 60k chars per request.

**Selection translation.** The text-selection popup (`PlainTip` in
`pdf/PdfViewer.jsx`, the highlight colors + link) carries a 文A button
when Settings → Reading › "Translate a selection" is on (`selTranslate`,
account pref, default on).

- It sends the selection as ONE text through the page translator's request
  (`translateChunk`: same model or service, language, server cache,
  streamed partials). Lines are rejoined by `selectionParagraphs`
  (`pdf/pdfTranslate.js`, the page blocks' hyphen/CJK rules), capped at
  5000 characters.
- The result shows under the colors as a fold-out panel: a header with the
  language, spinner and copy button, then selectable text. The button
  refolds it.
- "Translate on select" (`selTranslateAuto`, default off) starts it as soon
  as the popup opens.
- The popup is keyed by the selection, so a new selection starts over and
  aborts the previous request. A click or selection inside the popup keeps
  it open (the viewer's selection sync ignores a selection anchored in
  `.plainTip`).
- `TipFrame` flips the popup above the selection when it would run past the
  window's bottom. Like the popup itself, it needs edit rights on the page.

**Machine-translation services.** "Translate with" also offers the services
in `gamma/translate_engines.py`. The viewer then sends `model:
"engine:<id>"`, and `/api/ai/translate` hands the misses to
`translate_engines.translate` instead of a chat model.

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
- The service path needs no AI provider. It has no effort, no streamed
  partials (the stream is just the final line), no token usage and nothing
  to salvage, since the APIs answer aligned lists.
- Cache, validation and dedup are the LLM path's, keyed on `engine:<id>`.

Credentials are per account under the reserved `translate-engines` pref.
Like `ai-settings`, `/api/prefs` refuses it and the only read path is the
masked `GET /api/translate/engines`; guests can't store any. Unlike the LLM
prompt, nothing tells these services to leave math, `[12]` citation markers
or URLs alone. PDF text carries no LaTeX and math-heavy paragraphs are
skipped client-side, so the risk is small.

## Token usage

Every AI call's token counts come back from the provider itself and are
kept per account, so the chat can show what a reply cost and Settings can
show what a week cost. Code: `gamma/ai_usage.py`, `ai_client.normalize_usage`,
`frontend/src/chat/tokenUsage.js`.

- **On the wire.** `sse_events` ends every stream with a `("usage", {input,
  output, cache_read, cache_write})` event when the provider reported one:
  Anthropic's `message_start` (input, cache read/write) + the final
  `message_delta` (output); the Responses wire's `response.completed`;
  Chat Completions' trailing usage chunk, which the request asks for with
  `stream_options.include_usage` (OpenAI, vLLM, Ollama, llama.cpp, LiteLLM
  all honour it). Non-stream JSON bodies carry `usage` and `read_reply` /
  `call_ai` pass it to an `on_usage` callback. `input` is the whole prompt as
  the provider counted it (Anthropic's uncached + cache-read + cache-write
  parts summed, the way OpenAI's `prompt_tokens` already includes
  `cached_tokens`); `cache_read` / `cache_write` are the cached parts of it.
  A provider that reports nothing (some gateways) yields no event, and
  nothing else changes.
- **In the chat stream.** `/api/ai/chat` emits `{"usage": …}` lines — one per
  provider turn, so an agent reply with three tool rounds sends three; the
  client sums them onto the reply (`usage` on the saved message, like
  `context` and `actions`). Non-stream callers get one summed `usage` field.
  The panel shows a dim line under each reply (↑ input, ↓ output, "N%
  cached" when the provider served part of the prompt from its cache) and
  the conversation total in the chat-settings popover and the button's
  tooltip. While a reply streams the same line ticks up inside the
  "Thinking / Responding" pill, Claude Code style: exact counts for the
  rounds already reported plus a `~` estimate for the one still arriving
  (`estimateTokens`: characters received / 4, text deltas and previewed
  tool arguments alike; reset when that round's report lands). Replies
  saved before this carry no counts and show nothing.
- **Context ring.** Left of the chat header's ⚙, Claude Code style: how full
  the model's window is. The figure is the latest reply's LAST round alone
  (its input + output, which the next message carries as history), saved on
  the reply as `context_tokens` — the summed `usage` would overcount an
  agent reply. A reply saved before that field counts only when it had no
  tool rounds. The window is looked up live, never tabled in the code
  (`ai_catalog.context_window`, for every protocol alike):
  `GET /api/ai/context-window?model=<pid>:<model>` reads the entry's own
  model listing first (`Protocol.models` — Anthropic's `max_input_tokens`,
  the Codex backend's `context_window`, the `context_length` /
  `max_model_len` of OpenRouter, vLLM, Groq, …), then the public models.dev
  catalog for listings that carry no size (OpenAI's, DeepSeek's) — there the
  provider this entry talks to wins (`Protocol.catalog_hints`: the
  endpoint's host labels, OpenAI for the ChatGPT backend), else the value
  most providers agree on. Both are cached like the
  Codex version (6 h; a failed lookup retried after 10 min, the last good
  answer kept). A model neither knows gets `null`: no ring, and the popover
  shows the token count alone. The client asks once per model per page load
  (`useContextWindow` in `ChatDock.jsx`). The ring turns red past 80%;
  clicking it opens the chat-settings popover, whose Tokens section spells
  the figure out (`contextUsed`).
- **Stored.** `ai_usage.record` writes one row per call to `ai_usage` in
  `users.db` (account, time, kind, provider id + name, model, the four
  counts); `ai_usage.recorder(kind, entry, rt)` is the `on_usage` callback the
  call sites bind (`rt["user"]` names the account). Kinds: `chat` (every
  chat turn, agent rounds included), `translate` (each batch), `metadata`
  (AI extraction), `cite` (the slide citation), `test` (the Test button and
  the login test). Dictation has no token report. Rows older than
  `KEEP_DAYS` (400) go on the next write. Recording never raises.
- **Shown.** `GET /api/ai/usage` → `{windows: {today, week, month, all} →
  {calls, input, output, cache_read, cache_write}, kinds: {kind → the same}
  and models: [{provider_id, provider_name, model, …}] over the last 30
  days, first_at, keep_days, allowance}` (`allowance`: the shared entries'
  24-hour allowance, above, or null when no shared entry applies); `DELETE /api/ai/usage` forgets the
  account's rows except those the allowance still counts. Settings → AI › Connections → **Token usage** renders three
  tiles (today / 7 days / 30 days), the all-time line with Reset, and a
  by-model table (plus a by-kind block when more than one kind ran).
  Guests never see it (the pane shows it only to an account that can
  store keys). No prices anywhere: they differ per
  provider and change; the tokens are what every provider agrees on.

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

Replies stream per bucket, independently. `chat/chatSession.js` (owned by
App, so navigation can unmount the dock while a request runs) keeps one
in-flight reply per bucket. `active` is the set of streaming buckets, each
with its own `AbortController`. Asking one paper, opening another and asking
it too runs both requests at once. The composer, the Stop button and the
edit/re-send controls are disabled only while THIS bucket's reply streams
(`busyHere` in `ChatDock`), and Stop aborts only that one. A bucket refuses
a second question until its reply ends. The stream's `done` agent event
carries the bucket so a background reply finishing does not clear the open
page's live edit preview (`handleAgentEvent`). Covered by
`tests/chatSession.test.mjs` and the e2e `chat navigation` steps.

### Chat history

Each bucket keeps its earlier conversations. `chats` (data.db) holds the
one ACTIVE conversation per bucket — what the panel shows and autosaves —
plus a `title` column (added lazily by `connect_data_db`); `chat_history`
holds the archived ones (`id, bucket, title, messages, created_at,
updated_at`). Routes: `gamma/routers/chats.py`, prefix `/api/chat-history`.

- **New chat** (+ in the header) archives the conversation: it POSTs
  `/chat-history/archive` `{bucket, messages, title}`, which files it into
  history and clears the active row. The title is the user's, else the first
  user message's first non-quote line (`derive_title`). The client sends its
  own copy of the messages, so a reply still inside the 500 ms autosave
  debounce is kept. An empty conversation archives to nothing.
- The **History** button (clock icon) opens a popover listing the active
  conversation first (highlighted, "now") and then the bucket's archived
  ones newest-first (`GET /chat-history?bucket=`; title, age, message count
  in the tooltip), with a search box filtering on title + first message.
  Clicking an entry POSTs `/chat-history/{id}/open` with the current
  conversation: the current one is archived, the entry becomes the active
  row and leaves history. A conversation is always in exactly one place.
  - Rename: inline `aiKeyInput`. The active chat's title goes through
    `PUT /chats/{key}` `{messages, title}`, an entry's through
    `PUT /chat-history/{id}`. The autosave never sends a title, so it can't
    roll a rename back.
  - Delete: confirm dialog, then `DELETE /chat-history/{id}`. The active
    conversation has no delete; start a new chat instead.
- History follows its bucket: `POST /chats/folder-rename` rewrites entry
  buckets along with the active rows, and `purge_page_data` drops a deleted
  page's entries. The gamma export/import and the account-merge path copy
  only the active `chats` rows, not history.
