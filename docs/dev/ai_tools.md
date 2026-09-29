# Agent tools

What the library agent's tools do, how each one is used, and the guardrails
around them. The registry lives in `gamma/ai_tools.py`; the surrounding wiring
— scopes, permissions, the tool loop, replay — is in [ai.md](ai.md).

Every tool is one `TOOLS` entry declaring its wire spec, Settings permission
key, allowed scopes, mutating flag, and executor — so arming a chat is one
filter (`agent_tools`), dispatch is one lookup (`run_agent_tool`), and the
in-scope check (`_load_scoped_page`/`_scope_pages`: folder = tag prefix match
via `foldertags.path_within`, page = id equality) is shared by every
executor. When the request names a cursor block (`focus_block_id`) or attached block
chips (`context_blocks`), `agent_system` adds one line each so "this block" /
"these" resolve to ids without a `read_block` round-trip — their text is
already in the context (see "Pointing the chat at notes" in [ai.md](ai.md)).
Whenever a reading tool is armed it also tells the model to point at a page
as a markdown link `[title](/?page=<page_id>)` using ids from the tool
results; `ChatMarkdown` (`shared/ui/Widgets.jsx`) renders such same-origin
`?page=`/`?block=` links as open-in-place (`onOpenPage` → `openBlock`,
Ctrl/Cmd-click still opens a tab), so "find me the paper about X" ends in a
clickable link to the page.
Folder semantics mirror
[frontend/src/library/libraryUtils.js](../../frontend/src/library/libraryUtils.js) via the
shared `gamma/foldertags.py` rules; keep them in sync.

Pages in Recently deleted are out of every tool's reach, the MCP adapter's
included: they are not under `root`, and `_load_scoped_page` and
`blocks_store.page_root_id` find no page for them or their blocks
([home_library.md](home_library.md) "Recently deleted"). The agent cannot
delete pages.

Attached library pages (`context_pages` in the tool scope) extend reading access
beyond the current page or folder. `_scope_pages` combines the base scope and
these references for reads and search. `run_agent_tool` removes `context_pages`
before dispatching a mutation, so attachments do not grant editing access.

## The tools

The [MCP adapter](mcp.md) exposes a read-only subset of this same registry to
external assistants. `agent_tools` filters definitions and `run_agent_tool`
enforces the caller's allowlist at dispatch. Gamma chat passes its armed tool
set; MCP passes its fixed allowlist of the seven read tools below that stay
inside the library (everything but the web and write tools) and a
non-writable workspace scope.

| Tool | Permission | Scope | What it does |
|---|---|---|---|
| `list_pages` | List pages | folder | List the folder's pages: id, title, attachments (`[pdf]` when the page carries a PDF, `[]` for text-only), folder paths, labels, cached metadata (first author, year, venue), last-update date |
| `list_folders` | List pages | folder | The folder tree below the chat's folder: every path with its implied parents, subfolders indented, pages filed directly and in total; the pages in no folder at the root |
| `read_page` | Read pages | folder + page | Read one page: title, properties, the user's highlights and notes, and — when it carries a PDF — a windowed excerpt of the attachment's extracted text |
| `read_block` | Read note blocks | folder + page | Read a page's notes as an id-prefixed outline — the ids the editing tools take |
| `read_chats` | Read pages | folder + page | Read the AI chat kept with a page or folder: the current conversation as a numbered transcript, the earlier ones by `chat_id` |
| `view_pdf_page` | View PDF pages | folder + page | Look at one page of the page's PDF as a picture — a scan with no usable text layer, a figure, a table's layout |
| `search_library` | Search library | folder + page | Full-text search over the reachable pages' notes AND PDF text; hits carry a `source` (note hits: block id + page, PDF hits: page number). `search_pdfs` is its deprecated alias (replay only) |
| `search_papers` | Search papers online | folder + page | Scholarly search outside the library — Crossref + arXiv (keyless), or a direct DOI / arXiv-id lookup — returning registry records, available abstracts, and the `doi:` / `arXiv:` string `fetch_paper` takes |
| `search_web` | Search papers online | folder + page | General web search through a configured Brave or SearXNG provider; returns titles, URLs and snippets for discovering papers, lab publication pages and repository copies |
| `fetch_paper` | Fetch documents | folder + page | Read a document that is not in the library by DOI, arXiv id or URL: the PDF behind it (same resolver as opening a link, open-access fallback included) in `read_page`-style windows, else the web page's readable text; nothing is stored. A sign-in, bot check or paywall hands the fetch to the user's browser through a card in the reply |
| `rename_page` | Rename pages | folder | Change a page's title |
| `move_page` | Move pages | folder | File a page into a (sub)folder |
| `edit_block` | Edit note blocks | folder + page | Replace one note block's markdown text |
| `create_block` | Edit note blocks | folder + page | Add a note block under a page or block, optionally after a sibling |
| `move_block` | Edit note blocks | folder + page | Re-parent/reorder a note block (with its subtree) |

### list_pages (folder only)

Optional `label` / `folder` / `title_contains` filters narrow the listing, or
`list_labels: true` returns just the label vocabulary with counts. A `folder`
filter lists that folder's pages and its subfolders'; a relative path resolves
inside the chat's folder (`_in_scope_folder`, the rule `list_folders`,
`read_chats` and `move_page` share).

### list_folders (folder only)

How the library is organized, before acting on it or walking it: one line per
folder under the chat's folder (or under `folder`), as a full path indented by
depth, with the pages filed directly in it and, when different, the pages
anywhere below it. Parents that only exist through a deeper path (`a` for a
page filed in `a/b`) are shown, and a page in two subfolders counts once in
their parent. At the library root a last line counts the pages in no folder.
The result ends by pointing at `list_pages(folder=…)` and `read_page`, which
is how an assistant traverses the tree. Shares the "List pages" permission
with `list_pages`.

### read_page (both scopes)

Returns a `page_report_section`: the page's title, a properties line
(folders, labels, cached metadata, web source, attachment), an excerpt of the
attachment's extracted text when the page carries a PDF, then the page's
highlights and nested notes. A page without an attachment returns its notes —
they are its content. `pdf_chars` sizes the excerpt per call, capped by the
Settings / AI / Advanced / "Read window" preference (`gamma-ai-read-chars` →
request `read_char_limit`, riding in the scope dict as `read_chars`; default
cap 20 000 — `agent_tools` formats the effective cap into the armed spec so
the model knows what it may ask for). `pdf_page` starts the excerpt at a
1-based PDF page (extract_text's `start_page` — how a `search_library` PDF
hit is followed up), and `pdf_offset` windows onward from there; while text
remains, the excerpt names the next offset, so long documents are read in
successive windows. The `pdf_*` names stay for compatibility; they mean
"attachment text".

An area highlight (a Ctrl+drag rectangle: a highlight block with
`pdf_position.area` and no quote) has no text to show, so its line names
the rectangle and its page — "Area highlight (a rectangle on PDF page 4;
picture 1 attached)" — and the crop of that region rides on the result as
a picture (`ai_context.area_highlight` turns the stored pixel rectangle
into page fractions, `render_area_crops` renders it like a selection
crop; the chip's `images`, which the loop moves onto the tool message like
`view_pdf_page`'s page). At most `MAX_AREA_CROPS` (4) per page per read;
the rest say "no picture: more than the limit on this page". `read_block`
does the same on its outline lines, and the chat context does it for the
notes of a context page (the pictures go with the message's images; the
coverage entry's `area_pictures` says how many, and the chat shows a
"Pictures of N area highlights sent" pill).

What the conversation context already holds is never sent again. The
chat's coverage report rides in the scope (`scope["coverage"]`,
`context_cover`): for a page whose head excerpt is in context, a read with
no offset of a PDF page the excerpt shows in full continues from the page
the excerpt cut short and says so; a document the excerpt holds whole
answers "nothing more to read" (notes only); and the notes and highlights
come once — with the first window of a read (`pdf_offset` 0, `pdf_page`
1) or on `notes: true` — capped at the read window (a hint points at
`read_block` for the rest), and not at all when the context carries them
(`include_notes`, or a page without a PDF). The chip carries `pdf_pages`
(`[first, last]`, from `page_report_section`'s `report`) — what the chat's
coverage pill folds in as "read 3–5 with tools" — and its summary names
the span. The agent prompt says the same from the other side
(`coverage_lines`): which pages of which document are in context, and to
read from the cut-short page on.

### search_library (both scopes)

One query over both FTS indexes for the in-scope pages: the notes index
(`gamma/block_index.py` — changed pages are rebuilt before the query for up
to 0.2 s, so an edit made a moment ago is found; the background refresher
does the rest) and the PDF index (`gamma/pdf_index.py`
`pdf_missing`/`search_pdf` — the same indexes and query rules as
`GET /api/search` / Ctrl+F).
Note hits come first as `- note [block_id] in "title" (page_id …): snippet`
— ids `read_block` and the editors take — then PDF hits as `- PDF "title"
p.N (page_id …): snippet`. Un-indexed PDFs are kicked to the background indexer and
reported (as are note pages the background refresher hasn't reached) so the model knows
results may be incomplete. The MATCH ANDs every term, so a zero-hit query is
retried with only its longest words and the result labelled approximate —
otherwise the strict query reads as "the pages are silent" and the model
answers from memory. `search_pdfs` (the pre-Stage-2 name) is kept only as a
dispatch alias for replayed chats — see "Replay across turns" in
[ai.md](ai.md).

### read_block (both scopes)

The user's notes as an outline of blocks, every line prefixed with its block
id (`- [id] text`) — the ids the editing tools take, so the agent is told to
call it before any block edit. `block_id` may be a page id (the whole page's
note tree) or a nested block id (that block's own text in full plus its
subtree). Highlight blocks show their quoted passage inline, handwriting
blocks a "handwriting on p. N" label before their caption; long child
contents are snipped per line with an explicit "read_block this id for the
full text" marker, and the listing stops at the read-window budget naming how
many blocks were left out. (`read_page` shows the same notes without ids —
context for answering; `read_block` is the editing view.)

### read_chats (both scopes)

The AI chat kept with a page or folder, read from the rows the chat panel
keeps (`chats` / `chat_history` in data.db, bucket keys as in
[ai.md](ai.md) "Chat history buckets"). `page_id` names a page's chat; without
it, `folder` names a folder's (a path resolved inside the chat's folder, the
library root when empty). In a paper chat the page is the default and folder
chats are out of scope. Pages and folders go through the same scope checks as
every other read.

The current conversation comes back as a numbered transcript. Each entry says
who spoke, what rode along (attached PDFs and files by name, context pages by
title, a count of images; never their data), the text, and for a reply the
summaries of the tools it used. A failed reply is marked. After it comes the
index of the bucket's earlier conversations: `chat_id`, title (the user's,
else `derive_title`), message count, last update. `chat_id` reads one of
those, and only within the bucket it belongs to. Long conversations are
windowed by whole messages within the read budget (the "Read window"
preference); the transcript ends by naming the `start` to continue from. The
spec tells the model that replies in it are an AI's earlier answers, not the
page's content. Shares the "Read pages" permission with `read_page`.

### view_pdf_page (both scopes)

The model's eyes on a PDF: `page_id` + 1-based `pdf_page` rasterize that
page through pdfium (`pdf_text.render_page`, under the same lock as every
other pdfium walk) with its longer side at `RENDER_MAX_SIDE` px (1568 —
past that providers downscale anyway), JPEG through Pillow when it is
installed, else a PNG written in-process. The result text names the page,
the document's page count and the picture's size; the picture itself rides
on the action as `images` (`[(media_type, base64)]`). `run_agent_tool` keeps
it there and the chat loop moves it onto the model's tool result
(`{"role": "tool", …, "images"}`) before the chip is streamed — so the
picture reaches the model once and is never saved into the chat or replayed
(the result text says so; a later turn calls again). On the wire an
Anthropic `tool_result` carries the image blocks after the text; the OpenAI
chat-completions and Responses wires only take text in a tool result, so
their pictures follow the round's results as one user turn ("Pictures
returned by the tool calls above, in call order"; `ai_protocols/base.py`
`tool_image_turns`) — never the turn the user's own attachments ride on.
The armed prompt tells the model when a picture is worth its tokens
(missing or garbled extracted text, a figure, handwriting) and to say when
an answer was read from one. A page without a PDF, a page number past the
end (the count is named) and a file pdfium can't open are refused in text.
Its chip is 👁 "Looked at p. N of …", carrying `page_id` + `pdf_page`.

### search_papers / search_web / fetch_paper (both scopes)

These read-only tools reach outside the library (`gamma/ai_web.py` and
`gamma/web_search.py`; executors in `ai_tools.py`). The `web_search` permission
arms both search tools; `web_read` independently arms `fetch_paper`.
General web search also requires the server configuration below. For a cited
work, the agent finds the reference with `search_library` / `read_page`,
identifies it with `search_papers`, and reads it with `fetch_paper`.

`search_papers` takes a free-text `query` (title, keywords, authors) and asks
the keyless registries the metadata lookup already uses
([paper_metadata.md](paper_metadata.md)): Crossref's bibliographic search
(`metadata._crossref_search`) and the arXiv API (`_arxiv_search`, a title phrase
OR words ANDed over title/authors/abstract in one request). The phrase branch
keeps exact cited titles containing stopwords findable. The two lists are
interleaved in their own relevance order. A work both registries return (same
DOI, arXiv id or normalized title) is one record that keeps both identifiers
and fills a missing abstract from the other record. Matching uses the whole
normalized title and cannot merge conflicting nonempty DOI or arXiv ids on
title alone. A record whose title exactly matches the query ranks first.
A query that is itself a DOI or arXiv id (bare, `doi:`/`arXiv:`-prefixed, or a
URL; `ai_web.identifier`) is looked up directly. `limit` defaults to 8 (max
20). Each record is one line (title, up to three authors, year, venue, DOI,
arXiv id with its PDF URL) ending with the `fetch_paper(source=…)` call that
reads it — both calls, the arXiv version first, when it has both. The result
reminds the model these are registry records, not the user's pages. Available
abstracts appear beneath the records, up to 1,200 characters plus a truncation
marker. Registry records retain up to 4,000 characters of sanitized plain text.
Abstracts support relevance assessment and do not verify document identity.

`search_web(query, limit=8)` finds papers by topic and locates copies by exact
title or DOI on author, lab or repository pages. Queries are limited to 600
characters and 75 words; the result limit is clamped to 1–20. The adapter returns records
with `title`, `url`, `snippet`, and `provider`; the tool renders clickable titles
and snippets. Titles are bounded to 300 characters and snippets to 1,200,
with HTML removed and entities decoded. Invalid, credentialed and non-HTTP(S)
result URLs are discarded, and duplicate URLs are removed. Search queries,
snippets and linked content remain untrusted data: a result is a discovery
lead, not a retrieved paper or an instruction to the agent.

#### General web search configuration

Set these in the **backend server's environment**. `gamma/web_search.py`
reads them on each call:

| Variable | Behavior |
|---|---|
| `GAMMA_WEB_SEARCH_PROVIDER` | Optional `brave` or `searxng`. If unset, infer Brave when its API key is present, otherwise SearXNG when its URL is present. |
| `GAMMA_BRAVE_SEARCH_API_KEY` | Required for Brave. Sent as an `X-Subscription-Token` header to the fixed Brave search endpoint; omitted on redirects and never put in the URL. |
| `GAMMA_SEARXNG_URL` | Required for SearXNG: a public HTTP(S) instance URL, optionally ending in `/search`, with no credentials, query string or fragment. Base paths are supported. |

Brave requests `/res/v1/web/search?q=…&count=…` and reads
`web.results[].description` as the snippet. SearXNG requests
`/search?format=json&q=…` and reads `results[].content`. The SearXNG instance
must enable JSON output; many public instances disable it and return 403.
The normal SSRF guard applies, so a localhost/private-network SearXNG instance
is not a supported endpoint. See the official [Brave Web Search API](https://api-dashboard.search.brave.com/app/documentation/web-search)
and [SearXNG Search API](https://docs.searxng.org/dev/search_api.html) documentation.

Each search uses guarded requests with a 20-second timeout and a 2 MB response
cap, and requires a JSON response. `WebSearchError` distinguishes missing
configuration, bad configuration/query, access denial, rate limiting,
unavailability and invalid responses from a successful empty result. Errors
do not include raw provider payloads or credentials. With no provider
configured, the model is told to continue with `search_papers` and report the
limitation instead of repeatedly retrying `search_web`.

The provider adapters have offline transport fixtures. Live Brave and SearXNG
search remain unverified; check subscription, JSON access and upstream search
availability in the deployment.

#### Reading a discovered document

`fetch_paper` takes a `source` (DOI, arXiv id or http(s) URL) and reads the
document in windows with `read_page`'s knobs: `pdf_chars` (default and cap
from the Read window preference, shared through `_window_args`), `pdf_page`,
`pdf_offset`, and an excerpt that names the next offset while text remains.
Pass `expected_title` and `expected_doi` when the work is known. The title both
ranks PDF links on a publication list and checks a fetched PDF's opening title
lines. `version_policy` defaults to `allow_preprint`. `published_only` requires
published-version evidence and rejects manuscripts, preprints and unknown
versions. These arguments are preserved in continuation calls and browser
handoff retry instructions and must also accompany attempts at another copy.

PDF identity and publication version are separate checks. A supplied title must
match complete normalized opening lines before detected affiliation, abstract
or body boundaries. A title mentioned in an introduction or reference is
insufficient. Unusual layouts, scanned pages or poor extraction can reject the
right paper and require manual inspection. A resolved DOI conflicting with
`expected_doi` rejects the candidate.
A DOI occurring in the text is reported as supporting evidence and alone does
not mark identity verified. HTML pages remain discovery material.

The PDF behind the source comes from `routers.pdf.resolve_source`, the
resolver the extension and the "open a link" path use (arXiv abs/html → pdf,
publisher `citation_pdf_url` tags fetched with the article page as `Referer`,
the Unpaywall open-access fallback, browser headers). Unpaywall candidates are
deduplicated and ranked published, accepted, submitted, then unknown. The
resolver probes up to four eligible candidates within a 30-second OA budget
(including the registry lookup), keeping remaining candidates when one probe
succeeds. The AI fetch then tries up to four returned locations within a separate
45-second download-attempt budget: a failed download, unreadable PDF or identity
mismatch advances to the next candidate. These retry budgets do not guarantee
an end-to-end deadline for DNS, reading and extraction. Browser handoff follows
the permitted alternatives when an access restriction remains.

Resolution, downloading and the page fallback run in one
`net_guard.browsing_session`: one cookie jar, so what a landing page sets (a
session id, an institutional-access handshake) reaches the PDF request, as in
a browser tab. When **Use journal sign-ins** is on, connected publisher
sessions are used for the authenticated caller's account, restricted to each
connected HTTPS host; guest and share
requests use none. The tool binds that identity in its worker from the chat
scope, never from model arguments, and resets it after the fetch. Cookie values
never enter the model's context. It is downloaded through the SSRF guard under a size cap
(`FETCH_MAX_BYTES`, 40 MB) and extracted page by page
(`pdf_text.extract_pages`); every page's text is prefixed `[p. N]` so the
model can cite pages. The cache retains the resolver's open-access version note
and document provenance: requested source and expected identity, candidate and
actual final URLs, source kind, DOI,
`version`, `version_evidence`, `version_verified`, `identity_verified`, and
`identity_evidence` where available. Reading windows repeat the source note,
version and identity labels, and the actual final URL when it differs from
the candidate URL. An Unpaywall version is registry evidence;
it is not proof that downloaded bytes belong to the requested paper. A changed
download host invalidates previously observed version evidence. Output labels
an unidentified version or identity as unknown/unverified.

When no PDF is reachable (a paywall, a plain web page)
and the source is a page, its readable text is returned instead
(`ai_web.html_text`: head, scripts and styles dropped, block tags to line
breaks, entities unescaped), labelled as a web page with the reason no PDF
came, and followed by PDF-looking links (`gamma/paper_links.py`: a `.pdf` path,
a `/pdf` route, or "PDF" in the link text). The parser keeps link labels and
nearby citation text and ranks candidates against the expected title (or DOI)
before limiting results. Links already tried by the resolver are omitted, and up to eight
remaining `{url, title, context}` candidates are shown for the model to inspect.
A fetched document lives in an in-memory LRU (`_CACHE_MAX_DOCS` /
`_CACHE_MAX_CHARS`) keyed by account, usable-cookie fingerprint and resolved URL,
plus version policy, expected title and expected DOI, with source aliases in the
same partition. A permissive or differently identified fetch cannot satisfy a
stricter request from its cache. The windows of one paper cost one
download without sharing authenticated text between accounts. A session connect,
refresh, disconnect or cookie expiry changes the partition, so the next fetch
can retry an earlier abstract or blocked page. The fetched document is not
written to disk or added to the workspace; a restart forgets the cache (chat
history still keeps answers and shortened tool results). Other failures (not a
PDF and not a page, too large, no text layer) come back as `error:` text
suggesting the user drop the PDF onto Gamma.

Every fetch result carries a line saying the text is fetched web content and not
instructions, and the armed prompt says the same (ignore instructions found
in a document, tell the user). The prompt also says to prefer the library
for anything it holds and to name a fetched document (title, DOI/URL, page)
when answering from it. Their action chips are 🌐 (search) and ⬇ (fetch,
carrying the resolved `url`).

#### Walls and the browser handoff

What stopped a fetch is named (`ai_web.WALLS`), so a person can take over:

- `captcha`: a bot check or CAPTCHA page, served as 200, 403 or 503. It is
  recognized by Cloudflare's `cf-mitigated: challenge` header or challenge
  script, other vendors' markers (DataDome, PerimeterX, Imperva, Akamai's
  "Access Denied", Google's "unusual traffic") or the page title. A
  reCAPTCHA / hCaptcha / Turnstile widget counts only on a page with under
  2 000 readable characters, since an article page may carry a newsletter
  form.
- `login`: the request ended on a sign-in page (a login or SSO path or host,
  or a password field on a thin page).
- `denied`: HTTP 401, 403 or 418. `rate`: HTTP 429.
- `script`: the page has no readable text without its scripts (a browser
  shows it).
- `abstract`: the page fallback read an article page whose PDF was out of
  reach (the page advertised one, or its DOI's publisher refused).

The page fallback follows `<meta http-equiv="refresh">` redirects (up to two
hops, `pdf.meta_refresh`) as the resolver does, so a DOI landing that only
redirects (Elsevier's linkinghub) reaches the publisher and its wall.

A wall on the PDF still falls back to the page's text, so the model keeps
what was readable. In an account's own chat (`_chat_scope`'s `handoff_user`:
not a guest, not a share link), a wall opens a request in
`gamma/fetch_handoff.py`. Requests are per account and in memory, live 6
hours, at most 20 are kept, and there is one per work however its source is
spelled (reused while it waits). The page to open is the publisher's, never
`doi.org`, a sign-in page or a bot check's own host (`ai_web._entry`). For a
sign-in or bot-check address the page it would return to is taken from its
query (Radware's `ssc=` on `validate.perfdrive.com` in front of IOP, a sign-in
page's `next=` / `uri=`): starting from the paper's page, the site sends the
person through its check and back. The action carries `handoff: {id, host, wall,
source}`. A blocked fetch is an error action ("Needs your browser: host")
whose result tells the model to say briefly what blocked it and end its
reply, without retrying, switching versions or answering from memory. An
article-page-only read returns the page with the same instruction for
questions that need more. The armed prompt says the same.

The chat renders a card per request at its step, among the reply's steps
(`chat/FetchHandoffCards.jsx`; its rules, tested, in `chat/fetchHandoff.js`).
While the request goes on the card also shows with the steps folded, under
their pill (`PinnedFetches`), so it moves between two places: what it learnt
(the server's view, the Connector's answer, whether it was opened) is kept
per request for the page's life, not in the card. A fetch handed to the
browser does not count as a failed step.

- **Open {host}** has Gamma Connector open the publisher's page in a new
  tab when it answered the card (`openRoute`: `connector-tab` `open`
  through the extension's `bridge.js`); it takes the request (the card then
  reads "Gamma Connector is watching the tab") and sends the PDF from the tab
  once the user has signed in or passed the check
  ([extension.md](extension.md#fetches-handed-from-the-chat)). With no
  Connector to take the tab the page opens directly. The desktop app, and a
  Connector that has not answered yet, go through `/api/ai/handoffs/<id>/go`:
  for the request's owner that Gamma page goes straight on to the publisher,
  anyone else holding the link gets a "Continue to host?" button, so it is no
  open redirect; the Connector knows the tab by that address.
- **Fetch blocked papers in the background** (Settings → AI → Tools,
  `fetchInBackground`, account-wide, off by default): a card in the
  conversation's last reply hands its request to the Connector without a
  click (`autoOpens`), once, and not again after its tab was closed. The
  Connector tries in a background tab next to the Gamma tab (never switched
  to), three requests at a time, and closes the tab after delivery; the card
  reads "getting it in a background tab", or that the request waits for the
  papers before it.
  Nothing solves a CAPTCHA: what completes by itself is what the browser gets
  unasked — the user already signed in (or on the institution's network), or
  a check that passes a real browser on its own. When the page needs the user
  (a `NEEDS_YOU` note), **Show the tab** becomes the card's main button and
  brings that tab forward.
- Before that, the card asks the Connector whether it can
  (`window.postMessage` → the extension's `bridge.js`, answered after the
  Connector checked the request with its own server) and says so under the
  hint (`connectorNote`): it will send the PDF back; it is signed out of
  this server or signed in to another account; or no answer came within
  4.5 s — not in this browser, a version from before chat fetching, or set
  to another server — naming the address to set. In the desktop app (an
  `Electron/` User-Agent) the page opens in the system browser, and the card
  says a Connector there works too. It asks again when the page is opened
  again or the window regains focus.
- While the Connector watches the tab, the card also shows what it reports
  doing there (the request's `note`, `watchNote`): no PDF link on the page
  yet, a bot check or CAPTCHA to pass there, the PDF link leads to a
  sign-in, opening the PDF in the tab, the site did not hand it over (save it
  from the tab and drop it here), or the tab shows another paper; **Show the
  tab** brings that tab forward. When its tab was closed before the PDF came,
  the card is back to Open, saying so.
- **Upload PDF**, or a PDF dropped on the card, sends a file the user
  downloaded; the drop never reaches the page underneath.
- **Dismiss** settles the request; the Connector closes a background tab it
  opened for it.

The card asks the server every 2.5 s while the user is at the page, every
10 s while the request waits in the conversation's last reply, and otherwise
when the window regains focus. A delivered PDF (`POST …/pdf`: at most
`FETCH_MAX_BYTES`, a `%PDF` with a text layer; 409 once the request is
settled) is extracted and kept with the request for its account only
(30 M characters across requests, the oldest dropped), and the PDF itself is
held for saving (200 MB across requests, the oldest let go first; its text
stays). `fetch_paper` checks it
before any network fetch, for the same work in any spelling or the request's
URLs, and applies the current identity and version policy again. A passing
document carries a source note saying the user fetched it in their browser.
Browser-delivered PDFs currently lack publication-version evidence, so
`published_only` rejects them with an unverified-version error; delivery alone
does not guarantee a strict fetch can return their text. When every
request of the reply is settled with a PDF delivered, the reply is the
conversation's last, the chat is idle, the composer is empty, and this tab saw
a request waiting, the chat sends "I got it in my browser — {source} is
available now. Please continue." by itself. Otherwise **Continue with the
PDF** shows under the steps pill; a reload never resends. A card whose PDF
arrived while it was watched reads "The PDF arrived" for `ARRIVED_MS` (4 s),
then becomes the paper's file card at its step (`cardLook`); one already
done when the chat opened is its file card at once.

Every paper a reply fetched shows at its step as a card
(`chat/FetchHandoffCards.jsx` and `ChatDock`, rules tested in
`chat/chatPapers.js`): the step that first read a document is its file card
(`chat/FetchedPaper.jsx`; the action carries the document's `title`, `pdf`,
`pages`, and `request` when the user's browser delivered it), and later
windows of it stay plain steps; the step a wall stopped is the request's
card. A file card shows only the paper's title — the fetch's, else the
registry's (`GET /api/library/preview`), else its source — with what was
read ("PDF, 21 pages", a web page, from your browser) in its tooltip, and a
chevron for the step's tool output. While a fetch runs, its step shows the
paper it names, by the registry's title, with a spinner until the card
lands. File cards carry a **+** (Add to library), after asking the library
whether it holds the paper already (`GET /api/library/lookup`; then it reads
"In library" and opens the page). **+** saves through `POST
/api/clip`, the Connector's ingest (dedup, resolve and store the PDF, file
the page, look its metadata up), into the folder the chat is about (the
viewed folder, else the open paper's first) with Settings → Reading's
open-access, stored-copy and metadata choices. A PDF that came from the
user's browser is stored from the held copy first (`POST
/api/ai/handoffs/<id>/store`); the rest the server fetches again, and one it
cannot reach is saved as a page with its web source. Viewers and share
links see the file cards without **+**.

Guest and share-link chats get no card. Their access failures still explain
the Connector's **Publisher sessions**, **Connect this publisher** /
**Refresh now**, the **Use journal sign-ins** permission, and the alternative
of saving the PDF from a browser tab: login or CAPTCHA completion is the
user's browser task, and transferred cookies cannot guarantee access through
a challenge bound to that browser or IP. The model is told not to repeatedly
retry a blocked URL and to respect rate limits. Reading an uploaded library
page requires **Read pages** and selecting that page as context.

### rename_page / move_page (folder only)

`rename_page` changes a page's title. `move_page` files a page into a
(sub)folder — a new path creates the folder, and memberships outside the
current folder are kept. Both are reversible with another call. Their
actions name the change for the chat's change list: `title` (the page's
title before the call), `from` / `to` (the old and new title; the old
folder paths, comma-joined, and the new one, `""` for the library root).

### edit_block / create_block / move_block (both scopes, one permission)

The note editors, all under the single "Edit note blocks" permission.
`edit_block` changes one block's markdown text. `mode` `replace` (default)
makes `content` the block's entire new text. `append` / `prepend` add
`content` after / before the existing text on its own line, so the model
sends only the addition and never retypes what is there; a blank line
separates the two when either side is a heading, list, quote, table, fence,
display math or multi-line (`join_block_text`, mirrored in `editor/BlockTree.jsx`
for the streamed preview). `patch` rewrites one passage in place: `find`
quotes the existing text (it must occur once — exact match first, then a
whitespace-relaxed one so a wrapped quote still hits; zero or several hits
are refused with the count) and `content` replaces it, an empty `content`
cutting it, so deleting or correcting one sentence of a long block never
retypes the rest (`patch_block_text`, also mirrored in `editor/BlockTree.jsx`).
`selection` replaces exactly the note text the user selected for this
message. `selection` names it by its label from the request's
`note_selections` (`"S1"`; optional when there is only one), and the block
is the selection's — a different `block_id` is refused. The range is
replaced at its recorded offsets while they still hold the selected text,
else at that text's one occurrence; otherwise the edit is refused as changed
(`replace_selection_text`, mirrored in `editor/BlockTree.jsx`). The streamed
`progress` carries the selection's text and offset as `find` / `at`. After
an edit the selection covers the new text, so a second edit in the same turn
rewrites the first. The spec tells the model that a change to selected
text is always a selection edit; otherwise to prefer append for
"add / extend / note that", patch for deleting or fixing one part, and
replace only for full rewrites. The action carries
`mode`, and its chip reads "Appended to" / "Prepended to" / "Edited part of" /
"Edited the selection in" / "Edited". Page
roots are refused (titles go through `rename_page`); editing a highlight
block edits its note text, never the anchored passage.

A replace is the model's rewrite of text it has seen, so it needs the
block's full text from this turn. The scope dict lives for one request and
keeps `read_texts` (`notes_seen(scope)`): the stored text of every block a
`read_block`, a `read_page` or the chat's own context (the page's notes, the
cursor block, attached chips — the `notes_seen` argument in `ai_context`)
showed whole. A child that `read_block` snipped does not count. The replace
goes out with that text as its `base`, so the three-way merge in `ops.py`
keeps whatever the user typed into the block while the model was writing
([collab.md](collab.md)). A replace of a block not read in full this turn
is refused with "read the block first", and so is one whose `content`
carries read_block's `[truncated — read_block(` marker. After a replace the
block counts as read with the model's own text, as does a block
`create_block` made; after the other modes it has to be read again.
`append`, `prepend`, `patch` and `selection` need no read: they apply to
the current text.

`create_block` inserts a new block
under a page or block, after the sibling named by `after_id` (default: last).
`move_block` re-parents/reorders a block with its subtree — cycle-checked, and
cross-page moves (allowed when both pages are in scope) refuse subtrees
containing highlight blocks, whose PDF anchors are tied to their own paper.
All three go through the op path (`ops.apply_ops`, [collab.md](collab.md)):
logged, fanned out to anyone on the page, and the page root's `updated_at`
stamped so the home feed reorders. Their UI actions carry `page_id` (moves across
pages also `src_page_id`) and `block_id` (the edited/moved block, or the
created block's new id; `read_block` actions carry it too). The frontend
reloads the open page's block tree when it was touched and lights the block
up; edit/create calls are previewed in the block while the model is still
writing them (see "Watching the agent work" in [ai.md](ai.md)). There is still no delete under any permission: an
unwanted block is emptied or left for the user.

Typical uses: *"rename these to AuthorYear style"*, *"file the readout papers
into a subfolder"*, *"which of these papers measure T1? summarize the
approaches"*, *"where did I note something about bias-preserving gates?"*
(a notes hit with its block id), *"tidy my notes on this page into
sections"* — and in a page chat, *"where does this paper define the
protocol?"* (it searches inside the PDF and quotes page numbers) or *"add a
summary block to my notes"*.

## Guardrails

Deliberately not offered under any permission:

- Deleting anything — pages, blocks, folders, files.
- Editing highlight anchors or flat labels (folder labels change only through
  `move_page`).
- Reading library pages outside the base scope and attached references, or
  editing pages outside the base scope. The server checks every call.
- Reaching uploads, share links, settings, or other users' data.
- Adding a fetched paper to the library — `fetch_paper` reads, it never
  creates a page; the user drops the PDF or uses the extension for that.

Disarmed tools are not offered to the model, and the server additionally
refuses to execute them if called. Output/argument sizes are capped
(`_LIST_CAP` on listings; `_DETAIL_CAP` and `_ARG_CAP` cap the saved chip
only — the model gets the full result, under the live budget
`ai_context.LIVE_RESULT_BUDGET`), and the loop itself is bounded —
rounds and a ≤200-mutation guard, detailed in [ai.md](ai.md).

**Every tool call is shown in the reply**, reads included, so there is
always a visible record of what the agent looked at and changed. One pill
sums them up ("6 steps · listed, read 1 page · 1 failed") and expands to a
line per call, its icon naming the action kind (`ACTION_ICONS` in
`chat/ChatDock.jsx`: list, book, search, eye, globe, download, pencil,
folder, plus). Each line expands to the arguments and the output the model
got. Everything that changed is listed again under the pill: "Changed in
your library" (renamed and filed pages, old → new) and "Changed in your
notes" (edited, added and moved blocks), each entry a link to the page or
block. The note tools' actions carry their page's `title` for that list; a
change tool that changed nothing is marked `noop` and not listed.
