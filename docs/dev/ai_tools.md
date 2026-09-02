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
| `list_pages` | List pages | folder | List the folder's pages: id, title, attachments (`[pdf]` when the page carries a PDF, `[]` for text-only), folder paths, labels, cached metadata (first author, year, venue), last-update date |
| `read_page` | Read papers & notes | folder + paper | Read one page: a windowed excerpt of the extracted PDF text plus the user's highlights and notes |
| `read_block` | Read note blocks | folder + paper | Read a page's notes as an id-prefixed outline — the ids the editing tools take |
| `search_pdfs` | Search PDF text | folder + paper | Full-text search over the reachable PDFs, snippets with page numbers |
| `rename_page` | Rename pages | folder | Change a page's title |
| `move_page` | Move pages | folder | File a page into a (sub)folder |
| `edit_block` | Edit note blocks | folder + paper | Replace one note block's markdown text |
| `create_block` | Edit note blocks | folder + paper | Add a note block under a page or block, optionally after a sibling |
| `move_block` | Edit note blocks | folder + paper | Re-parent/reorder a note block (with its subtree) |

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

### read_block (both scopes)

The user's notes as an outline of blocks, every line prefixed with its block
id (`- [id] text`) — the ids the editing tools take, so the agent is told to
call it before any block edit. `block_id` may be a page id (the whole page's
note tree) or a nested block id (that block's own text in full plus its
subtree). Highlight blocks show their quoted passage inline; long child
contents are snipped per line with an explicit "read_block this id for the
full text" marker, and the listing stops at the read-window budget naming how
many blocks were left out. (`read_page` shows the same notes without ids —
context for answering; `read_block` is the editing view.)

### rename_page / move_page (folder only)

`rename_page` changes a page's title. `move_page` files a page into a
(sub)folder — a new path creates the folder, and memberships outside the
current folder are kept. Both are reversible with another call.

### edit_block / create_block / move_block (both scopes, one permission)

The note editors, all under the single "Edit note blocks" permission.
`edit_block` replaces one block's entire markdown text (page roots are
refused — titles go through `rename_page`; editing a highlight block edits its
note text, never the anchored passage). `create_block` inserts a new block
under a page or block, after the sibling named by `after_id` (default: last).
`move_block` re-parents/reorders a block with its subtree — cycle-checked, and
cross-page moves (allowed when both pages are in scope) refuse subtrees
containing highlight blocks, whose PDF anchors are tied to their own paper.
All three touch the page root's `updated_at` (like the editor's autosave PUT)
so the home feed reorders, and their UI actions carry `page_id` (moves across
pages also `src_page_id`) — the frontend reloads the open page's block tree
when it was touched. There is still no delete under any permission: an
unwanted block is emptied or left for the user.

Typical uses: *"rename these to AuthorYear style"*, *"file the readout papers
into a subfolder"*, *"which of these papers measure T1? summarize the
approaches"*, *"tidy my notes on this paper into sections"* — and in a paper
chat, *"where does this paper define the protocol?"* (it searches inside the
PDF and quotes page numbers) or *"add a summary block to my notes"*.

## Guardrails

Deliberately not offered under any permission:

- Deleting anything — pages, blocks, folders, files.
- Editing highlight anchors or flat labels (folder labels change only through
  `move_page`).
- Reaching anything outside the chat's scope — enforced by the server on every
  call, not just by instructions.
- Reaching uploads, share links, settings, or other users' data.

Disarmed tools are not offered to the model, and the server additionally
refuses to execute them if called. Output/argument sizes are capped
(`_LIST_CAP`, `_DETAIL_CAP`, `_ARG_CAP`), and the loop itself is bounded —
rounds and a ≤200-mutation guard, detailed in [ai.md](ai.md).

**Every tool call is shown in the reply** — reads included: listing, reading
and searching render as ☰/📖/🔍 lines; renames, moves and note edits/creates
as ✎/📁/＋ lines — so there is always a visible record of what the agent
looked at and changed (clicking a chip expands the arguments and the output
the model got).
