# Agent tools

What the library agent's tools do, how each one is used, and the guardrails
around them. The registry lives in `gamma/ai_tools.py`; the surrounding wiring
— scopes, permissions, the tool loop, replay — is in [ai.md](ai.md).

Every tool is one `TOOLS` entry declaring its wire spec, Settings permission
key, allowed scopes, mutating flag, and executor — so arming a chat is one
filter (`agent_tools`), dispatch is one lookup (`run_agent_tool`), and the
in-scope check (`_load_scoped_page`/`_scope_docs`: folder = tag prefix match,
page = id equality) is shared by every executor. Folder semantics mirror
[frontend/src/libraryUtils.js](../../frontend/src/libraryUtils.js) via the
shared `gamma/foldertags.py` rules; keep them in sync.

## The tools

| Tool | Permission | Scope | What it does |
|---|---|---|---|
| `list_pages` | List pages | folder | List the folder's pages: id, title, kind (pdf/note), folder paths, labels, cached metadata (first author, year, venue), last-update date |
| `read_page` | Read papers & notes | folder + paper | Read one page: a windowed excerpt of the extracted PDF text plus the user's highlights and notes |
| `search_pdfs` | Search PDF text | folder + paper | Full-text search over the reachable PDFs, snippets with page numbers |
| `rename_page` | Rename pages | folder | Change a page's title |
| `move_page` | Move pages | folder | File a page into a (sub)folder |

### list_pages (folder only)

Optional `label` / `folder` / `title_contains` filters narrow the listing, or
`list_labels: true` returns just the label/folder vocabulary with counts —
the cheap way to learn how a library is organized before acting on it.

### read_page (both scopes)

Returns a `page_report_section`: an excerpt of the extracted document text
plus the page's highlights and nested notes. `pdf_chars` sizes the excerpt per
call, capped by the Settings → Assistant "Read window" preference
(`gamma-ai-read-chars` → request `read_char_limit`, riding in the scope dict
as `read_chars`; default cap 20 000 — `agent_tools` formats the effective cap
into the armed spec so the model knows what it may ask for). `pdf_page` starts
the excerpt at a 1-based PDF page (extract_text's `start_page` — how a
`search_pdfs` hit is followed up), and `pdf_offset` windows onward from there;
while text remains, the excerpt names the next offset, so long papers are read
in successive windows.

### search_pdfs (both scopes)

In-scope FTS snippets via the `routers/search.py` helpers (the same index and
query rules as Ctrl+F); un-indexed papers are kicked to the background indexer
and reported so the model knows results may be incomplete. The MATCH ANDs
every term, so a zero-hit query is retried with only its longest words and the
result labelled approximate — otherwise the strict query reads as "the paper
is silent" and the model answers from memory.

### rename_page / move_page (folder only)

`rename_page` changes a page's title. `move_page` files a page into a
(sub)folder — a new path creates the folder, and memberships outside the
current folder are kept. These are the only mutations, and both are reversible
with another call.

Typical uses: *"rename these to AuthorYear style"*, *"file the readout papers
into a subfolder"*, *"which of these papers measure T1? summarize the
approaches"* — and in a paper chat, *"where does this paper define the
protocol?"* (it searches inside the PDF and quotes page numbers).

## Guardrails

Deliberately not offered under any permission:

- Deleting anything — pages, notes, folders, files.
- Editing notes, highlights, or flat labels (folder labels change only through
  `move_page`).
- Reaching anything outside the chat's scope — enforced by the server on every
  call, not just by instructions.
- Reaching uploads, share links, settings, or other users' data.

Disarmed tools are not offered to the model, and the server additionally
refuses to execute them if called. Output/argument sizes are capped
(`_LIST_CAP`, `_DETAIL_CAP`, `_ARG_CAP`), and the loop itself is bounded —
rounds and a ≤200-mutation guard, detailed in [ai.md](ai.md).

**Every tool call is shown in the reply** — reads included: listing, reading
and searching render as ☰/📖/🔍 lines, renames and moves as ✎/📁 lines — so
there is always a visible record of what the agent looked at and changed
(clicking a chip expands the arguments and the output the model got).
