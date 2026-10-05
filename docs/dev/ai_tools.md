# Agent tools

What the library agent's tools do, how each one is used, and the guardrails
around them. The registry lives in `gamma/ai_tools.py`; the surrounding wiring
— scopes, permissions, the tool loop, replay — is in [ai.md](ai.md).

Every tool is one `TOOLS` entry declaring its wire spec, Settings permission
key, allowed scopes, mutating flag, and executor — so arming a chat is one
filter (`agent_tools`), dispatch is one lookup (`run_agent_tool`), and the
in-scope check (`_load_scoped_page`/`_scope_pages`: folder = the page is
filed in the chat's folder or below it, `_reach` / `_filed_in` over
`blocks_store.folder_subtree_ids`; page = id equality) is shared by every
executor. A mutating entry also has a `preview`: what its approval card
shows when the user set its permission to Ask. Each changer has three
parts: a `_plan_*` function checks a call and works out the change, the
executor applies the plan, and the preview describes it. So the card and
the change come from one place ("Asking before a call" in
[ai.md](ai.md#asking-before-a-call-approvals)). A permission is either
reading or changing: its tools share the `mutating` flag, which also sets its
default (reading allowed, changes ask; `ai_permissions.permission_state`).
When the request names a cursor block (`focus_block_id`) or attached block
chips (`context_blocks`), `agent_system` adds one line each so "this block" /
"these" resolve to ids without a `read_block` round-trip — their text is
already in the context (see "Pointing the chat at notes" in [ai.md](ai.md)).
Whenever a reading tool is armed it also tells the model to point at a page
as a markdown link `[title](/?page=<page_id>)` using ids from the tool
results; `ChatMarkdown` (`shared/ui/Widgets.jsx`) renders such same-origin
`?page=`/`?block=` links as open-in-place (`onOpenPage` → `openBlock`,
Ctrl/Cmd-click still opens a tab), so "find me the paper about X" ends in a
clickable link to the page.
Folders and labels are blocks ([home_library.md](home_library.md) "Folders
and labels"). A folder chat's scope names its folder by id (`{"type":
"folder", "folder": <id>}`, `""` at the library root; the chat request's
`folder`, [ai.md](ai.md)), and the prompt names it by path with its id
(`The user is viewing the folder "Physics / QEC" (id …)`). The tools take
and show paths for the model, the names joined with ` / `
(`blocks_store.PATH_SEP`), and the ids `list_folders` shows.
`_resolve_folder` reads every `folder` argument: `""` is the chat's folder
(the library root in a page chat); a folder id is taken as it is (refused
outside the chat's folder); anything else is a path, split on ` / ` first,
then on `/`, each name matched exactly, else ignoring case
(`blocks_store.named`). A folder chat reads a path inside its folder: one
that starts with the folder's own path is read from there, any other as a
path below it. A page chat reads a path from the top. A path several sibling folders share
is an error naming each one's id and full path, so the model can pass the
id. A path no folder has is an error for a read (`_find_folder`) and is
made for `move_page` and `save_paper` (`_made`, `ops.ensure_filing`, only
when the call runs: a preview makes nothing). Labels are named by name.

Pages in Recently deleted are out of the tools' reach, the MCP adapter's
included: they are not under `root`, and `_load_scoped_page` and
`blocks_store.page_root_id` find no page for them or their blocks
([home_library.md](home_library.md) "Recently deleted"). Two folder-chat
tools are the exception: `list_deleted` lists them and `restore_page` brings
one back. The agent cannot delete pages.

Attached library pages (`context_pages` in the tool scope) extend reading access
beyond the current page or folder. `_scope_pages` combines the base scope and
these references for reads and search. `run_agent_tool` removes `context_pages`
before dispatching a mutation, so attachments do not grant editing access.

## The tools

The [MCP adapter](mcp.md) exposes a read-only subset of this same registry to
external assistants. `agent_tools` filters definitions and `run_agent_tool`
enforces the caller's allowlist at dispatch. Gamma chat passes its armed tool
set; MCP passes its fixed allowlist of seven read tools that stay inside
the library (not the web and write tools, `view_ink`, `cite` or
`list_deleted`) and a non-writable workspace scope.

| Tool | Permission | Scope | What it does |
|---|---|---|---|
| `list_pages` | List pages | folder | List the folder's pages: id, title, attachments (`[pdf]` when the page carries a PDF, `[]` for text-only), folder paths, labels, cached metadata (first author, year, venue), last-update date |
| `list_folders` | List pages | folder | The folder tree below the chat's folder: every path with its implied parents, subfolders indented, pages filed directly and in total; the pages in no folder at the root |
| `read_page` | Read pages | folder + page | Read one page: title, properties, the user's highlights and notes, and — when it carries a PDF — a windowed excerpt of the attachment's extracted text |
| `read_block` | Read note blocks | folder + page | Read a page's notes as an id-prefixed outline — the ids the editing tools take |
| `read_chats` | Read pages | folder + page | Read the AI chat kept with a page or folder: the current conversation as a numbered transcript, the earlier ones by `chat_id` |
| `view_pdf_page` | View pages and handwriting | folder + page | Look at one page of the page's PDF as a picture — a scan with no usable text layer, a figure, a table's layout |
| `view_ink` | View pages and handwriting | folder + page | Look at the user's handwriting as a picture: a group's strokes on their PDF page or page of paper, cropped to them, or the whole page with all its handwriting |
| `cite` | Read pages | folder + page | The citation records kept with pages: the paper metadata, its BibTeX and the slide citation, for up to 50 pages |
| `search_library` | Search library | folder + page | Full-text search over the reachable pages' notes AND PDF text; hits carry a `source` (note hits: block id + page, PDF hits: page number). `search_pdfs` is its deprecated alias (replay only) |
| `search_papers` | Search papers online | folder + page | Scholarly search outside the library — Crossref, arXiv and OpenAlex at once, or a direct DOI / arXiv-id lookup — returning merged registry records (citation count, the start of the abstract) with the `doi:` / `arXiv:` string `fetch_paper` takes; an optional year filter and citation or recency order |
| `related_papers` | Search papers online | folder + page | One step through OpenAlex's citation graph from a DOI, arXiv id or exact title: the works it cites, the works citing it, or related works, most cited first |
| `search_web` | Search papers online | folder + page | General web search through the account's engine (the chat's own AI connection, Brave Search or SearXNG): titles, URLs and snippets as leads for `fetch_paper`. Offered only when an engine is available |
| `fetch_paper` | Fetch documents | folder + page | Read a document that is not in the library by DOI, arXiv id or URL: the PDF behind it (same resolver as opening a link, several open-access copies tried) in `read_page`-style windows, else the web page's readable text with its PDF links; nothing is stored. The result names the PDF's version and checks it against the paper's title when given. A sign-in, bot check or paywall hands the fetch to the user's browser through a card in the reply |
| `save_paper` | Save papers | folder + page | Add a paper to the library by DOI, arXiv id or URL, through the ingest the reply's Save to library and the Connector use |
| `rename_page` | Rename pages | folder | Change a page's title |
| `move_page` | Move pages | folder | File a page into a (sub)folder |
| `list_deleted` | List pages | folder | List Recently deleted as far as the chat's folder reaches |
| `restore_page` | Restore deleted pages | folder | Bring a page back from Recently deleted, filed where it was |
| `edit_block` | Edit note blocks | folder + page | Replace one note block's markdown text |
| `create_block` | Edit note blocks | folder + page | Add a note block under a page or block, optionally after a sibling |
| `move_block` | Edit note blocks | folder + page | Re-parent/reorder a note block (with its subtree) |

### list_pages (folder only)

Optional `label` / `folder` / `title_contains` filters narrow the listing, or
`list_labels: true` returns just the label vocabulary with counts (at the
library root every label, an unused one as 0 pages; in a folder the labels
its pages carry). Each line shows the page's `folders=["A / B", …]` paths
and `labels=[…]` names. A `folder` filter (a path or an id,
`_resolve_folder`) lists that folder's pages and its subfolders'; `label`
matches a name exactly, else ignoring case.

### list_folders (folder only)

How the library is organized, before acting on it or walking it: one line per
folder from the chat's folder (or from `folder`) down, `- [<id>] "a / b"`
indented by depth, with the pages filed directly in it and, when different,
the pages anywhere below it (`no pages` for an empty folder — folders are
real and may be empty). A page in two subfolders counts once in their
parent. At the library root a last line counts the pages in no folder. The
result ends by pointing at `list_pages(folder="<path or id>")` and
`read_page`, which is how an assistant traverses the tree. Shares the "List pages" permission
with `list_pages`.

### read_page (both scopes)

Returns a `page_report_section`: the page's title, a properties line
(folders, labels, cached metadata, web source, attachment), an excerpt of the
attachment's extracted text when the page carries a PDF, then the page's
highlights and nested notes. A page without an attachment returns its notes —
they are its content. `pdf_chars` sizes the excerpt per call, capped by the
Settings / AI / Tool usage / "Read window" preference (`gamma-ai-read-chars` →
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
a picture (`ai_context.area_highlight` turns the stored rectangle, in
the position's `width` × `height`, into page fractions,
`render_area_crops` renders it like a selection crop; the chip's
`images`, which the loop moves onto the tool message like
`view_pdf_page`'s page). `area_highlight` answers only for a highlight
(`highlights.is_highlight`: a position, no ink group), never for a text
box, which has no quote either, nor for a highlight placed by its page
alone. At most
`MAX_AREA_CROPS` (4) per page per read;
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
(`gamma/block_index.py`, in pages.db, kept current by every write — an
edit made a moment ago is found) and the PDF index (`gamma/pdf_index.py`
`pdf_missing`/`search_pdf` — the same indexes and query rules as
`GET /api/search` / Ctrl+F).
Note hits come first as `- note [block_id] in "title" (page_id …): snippet`
— ids `read_block` and the editors take — then PDF hits as `- PDF "title"
p.N (page_id …): snippet`. Un-indexed PDFs are kicked to the background indexer and
reported so the model knows
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
many blocks were left out. A text box ([text_boxes.md](text_boxes.md)) shows
"(text box on p. N)", "(text box on the page of paper above)" or "(text
box, not placed on a page)" before its text. The nearest sheet above a box
wins over its `pdf_page`. (`read_page` shows the same notes without ids —
context for answering; `read_block` is the editing view.)

### read_chats (both scopes)

The AI chat kept with a page or folder, read from the rows the chat panel
keeps (`chats` / `chat_history` in pages.db, bucket keys as in
[chat_history.md](chat_history.md) "Buckets": a folder's bucket is its id).
`page_id` names a page's chat; without it, `folder` names a folder's (a path
or an id, `_resolve_folder`; the library root's, `home`, when empty). In a paper chat the page is the default and folder
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

A model that reads text only is offered neither this nor `view_ink`
(`PICTURE_TOOLS`, [ai.md](ai.md#other-services)).

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

### view_ink (both scopes)

The model's eyes on handwriting ([handwriting.md](handwriting.md)). An ink
group's text is only its caption, so read_block labels the block
("handwriting on p. N, K strokes" or "on the page of paper above") and
view_ink shows the strokes. `block_id` names a handwriting block or a page
of paper (a sheet). A group is drawn where it was written, cropped to its
strokes with a margin (`ink_view.crop_box`: a tenth of the strokes' larger
side or 18 pt, and at least 2 inches a side, so one word is seen with what
surrounds it). `area: "page"` shows the whole PDF page or sheet with all its
handwriting instead. A sheet's id always shows the whole sheet.

Nothing is drawn by hand. Both paths reuse an export
(`gamma/ink_view.py`):

- **A sheet** (a `canvas` group, or the sheet itself) is the notebook
  export's one-page PDF, `notebook.notebook_pdf`: the paper painted and the
  strokes as vectors.
- **A PDF page** is that page on its own with the groups written on it as
  the annotated export's `/Ink` annotations
  (`pdf_export.page_with_ink`). pdfium generates their appearance when it
  renders, so highlighters stay translucent over the text. Ink imported
  from the PDF and still embedded in it (`pdf_export.still_embedded`) is
  not added twice. When the PDF
  is missing or PyPDF2 cannot copy the page (an encrypted file), the
  strokes are drawn on blank paper of the page's size, and the result says
  so.

`pdf_text.render_page` rasterizes either, whole at `RENDER_MAX_SIDE` or
cropped under its 4× zoom cap, and `pdf_text.image_part` encodes the picture
as every tool picture is. It rides on the chip's `images` like
`view_pdf_page`'s and is never saved. The result names the block, the
page and the stroke count, quotes the caption, and asks the model to mark a
word it cannot read `[illegible]` rather than guess. Its chip is ✎
"Looked at handwriting in …" (kind `ink`, with `block_id`), and the open
page rings that block. It shares the View permission with `view_pdf_page`.

With `edit_block` armed, the agent prompt also says how to transcribe:
look with view_ink, then write the text into the group's caption, as an
append when it has one. The notes' **Transcribe with AI** (a handwriting
block's ⋮⋮ menu) sends exactly that request with the block attached as a
chip, and an attached handwriting block's picture rides with the message
(see "Pointing the chat at notes" in [ai.md](ai.md)). So a chat without
tools still answers with the transcription.

### cite (both scopes)

The citation records Gamma keeps with pages, so a bibliography is built
from the records and not from the model's memory. `page_ids` takes up to
50 pages; in a page chat the default is the open page. For each page the
result gives:

- the record from `properties.meta` (authors, year, venue, volume, pages,
  publisher, ISBN, DOI, arXiv id; [paper_metadata.md](paper_metadata.md)),
  and the paper's own title when it differs from the page's;
- the stored BibTeX, else one built from the record the way a hand edit
  builds it (`bibtex.build_entry`), under the citation key pinned on the page
  when there is one — so what the model cites matches the `.bib` export
  ([import_export.md](import_export.md#bibtex-bibliography));
- the slide citation when one was made (`ppt_cite`). None is generated,
  since that is an AI call.

A record flagged `unverified` says so. A page without metadata says so
too, and the model is told not to invent one. Nothing is looked up or
stored. Pages outside the scope are refused one by one, and a call where
every page is refused is an error. Its chip is ❝ "Cited N pages" (kind
`cite`), under the Read permission.

### The web tools (both scopes)

```mermaid
flowchart LR
  q["a paper the library lacks"] --> lib["search_library, read_page"]
  lib --> held{"already a page?"}
  held -- yes --> read
  held -- no --> find["search_papers: Crossref + arXiv + OpenAlex"]
  find --> more["search_web, related_papers"]
  more --> probe
  find --> probe["fetch_paper mode=probe"]
  probe --> right{"the right paper?"}
  right -- no --> find
  right -- yes --> long{"answer could be anywhere in it?"}
  long -- yes --> helper["read_paper: a helper reads it"]
  long -- no --> read["fetch_paper, window by window"]
  helper --> ans(["answer, citing pages"])
  read --> ans --> keep["save_paper"]
```

The agent's reach outside the library, read-only: `search_papers`,
`related_papers` and `search_web` under **Search papers online**
(`web_search`), `fetch_paper` and `read_paper` under **Fetch documents**
(`web_read`). The
code is `gamma/ai_web.py` (registries, citation graph, fetching),
`gamma/openalex.py`, `gamma/paper_links.py` and `gamma/search_services.py`
(general web search); the executors are in `ai_tools.py`.

Two uses shape them. A work the user's pages cite but do not hold: *"read
reference 12 of this paper and tell me what it measures"*. The agent finds the
reference entry with `search_library` / `read_page`, identifies the work with
`search_papers` and reads it with `fetch_paper`. And discovery from a research
question or an experimental setup: *"find papers on density-dependent loss in
Raman sideband cooling of 85Rb in a 1D lattice"*. With the search tools armed,
`agent_system` gives the model a researcher's recipe: two to four short concept
queries (the phenomenon, the method, the system, not every parameter at once),
each run through `search_papers` (and `search_web` when armed), relevance judged
from the abstracts, the strongest match's citations followed with
`related_papers`, and only the few decisive papers fetched. The answer says
which papers match the user's case directly and which are analogies (another
species, geometry or regime), and never presents one paper's number as a
general limit. Without a web engine the prompt adds that general web search is
off and where to turn it on.

#### search_papers

`search_papers` takes a free-text `query` (title, keywords, authors) and asks
three registries at once, in a thread pool: Crossref's bibliographic search
(`metadata._crossref_search`) and the arXiv API (`_arxiv_search`), which the
metadata lookup also uses ([paper_metadata.md](paper_metadata.md)), and
OpenAlex (`openalex.search`). They are asked with `detail=True`, which adds
each record's `abstract` (plain text, JATS stripped, `metadata.plain_abstract`)
and Crossref's `cited_by` count; the metadata lookup stores the records it
finds, so it asks without. arXiv's request is a title phrase OR words ANDed
over title/authors/abstract; the phrase branch keeps exact cited titles
containing stopwords findable. A query of more than five words that matches
nothing is asked once more with its five longest words, so a descriptive query
degrades to near matches instead of nothing.

The three lists are interleaved in their own relevance order (`ai_web._merge`),
OpenAlex's first: its relevance reads a topic best, where Crossref's puts
loosely matching book chapters first.
A work two registries return (same DOI, arXiv id or normalized title) is one
record that takes the fields the first lacks — a journal record its arXiv
preprint and an arXiv record its DOI, the abstract, OpenAlex's open-access PDF
(`oa_pdf`) — and the larger citation count. `from_year` keeps works from that
year on; it goes to Crossref and OpenAlex as a filter and is applied to the
merged list for arXiv. `sort` is `relevance` (the default), `citations` or
`recent`. The order is applied here, to the relevant candidates: Crossref's own
citation sort discards relevance and returns unrelated highly cited papers. A
record whose title is exactly the query (a cited reference) ranks first
whatever the order. A query that is itself a DOI or arXiv id (bare,
`doi:`/`arXiv:`-prefixed, or a URL; `ai_web.identifier`) is looked up directly,
and OpenAlex's record of it (a lookup by id, which costs nothing) adds the
abstract, citation count and open-access PDF.

`kind` and `open_access` narrow the search instead of the model spelling
"journal", "PDF" or "preprint" into the query, where those words only
confuse the match. `kind` is `any`, `article` (published in a journal) or
`preprint`, and each goes to the registries that can answer it: an
`article` search skips arXiv and asks Crossref for `type:journal-article`
and OpenAlex for `type:article`; a `preprint` search skips Crossref's
bibliographic index, which is about the published record. `open_access`
asks OpenAlex for `is_oa:true` and drops merged records that name no PDF
anyone can read (`ai_web._free_full_text`: an arXiv copy or an
open-access location). The head names whatever was narrowed, and says so
again when nothing was found.

`limit` defaults to 8 (max 20). Each record is one line (title, up to three
authors, year, venue, `cited by N`, DOI, arXiv id with its PDF URL, else the
open-access PDF) ending with the `fetch_paper(source=…)` call that reads it —
both calls, the arXiv version first, when it has both — and a second line with
the first 400 characters of its abstract. A work this reply already listed
shrinks to one line pointing back (`format_records(first_sight=…)`, from the
message's `ai_tools.Tally`), so the second and third query of a search cost
a line per repeat instead of a whole record. The result reminds the model these
are registry records, not the user's pages, and that an abstract says what a
paper is about, not what it found. A registry that did not answer is named at
the end (`search_papers(notes=…)` collects them): "(Not searched: OpenAlex
paused searches without an API key (heavy load) — a free OpenAlex API key in
Settings → AI → Tool usage → Online search avoids this.)".

OpenAlex answers without an account on a small daily budget. A search costs a
tenth of a cent of it, a lookup by id nothing, and keyless searches are paused
when its cluster is loaded (503). The account's optional key (Online search,
below), else the server's `GAMMA_OPENALEX_API_KEY`, gives ten times the budget
and uninterrupted search. The key travels in the `Authorization` header. The
registry switch `GAMMA_METADATA_LOOKUP=off` applies to OpenAlex too.

#### related_papers

`related_papers(source, relation, sort, limit, from_year)` takes a DOI, an
arXiv id or an exact title and returns the works it cites (`references`), the
works citing it (`citations`, the default) or OpenAlex's related works
(`similar`), as `search_papers` records. They come most cited first, or newest
first with `sort: "recent"`: the most cited works citing a classic are mostly
reviews, and the newest are its follow-up work. An arXiv paper is followed
through its published DOI when the arXiv record has one, since the journal
record carries the citations; else through its arXiv DOI, else by exact title
(`openalex.find_title`). The lookup is free; the list is one filter request
(`cites:W…`, or up to 50 of the work's `referenced_works` / `related_works` by
id). The head names the relation and, for citations, the total count.

#### search_web

`search_web(query, limit)` searches the general web for what the registries
miss: author and lab publication lists, institutional repositories, theses,
and another copy of a paper whose publisher PDF is blocked. It is armed only
when `search_services.web_engine` finds an engine for this chat (the TOOLS
entry's `needs: "web_engine"`, met through `ai_tools.available(scope)`; the
chat scope carries `web_engine`). Each result is a title link, the `doi:` /
`arXiv:` string the URL carries when it carries one (a DOI or arXiv link, a
publisher path with the DOI in it) with the `search_papers` call that gives its
registry record, and the snippet. The head says these are leads, not verified
papers, to be read with `fetch_paper(source=URL, title=…)`. Titles are capped
at 300 characters and snippets at 400, HTML is stripped, non-http(s) and
credentialed URLs are dropped, and duplicates are removed. One message may run
`MAX_WEB_SEARCHES` (10) searches, since each is a paid query or a call on the
chat's connection; the tool rounds cap everything else.

The engines (`gamma/search_services.py`):

- **Your AI connection** (`ai`): one short call on the chat's own connection
  with its provider's hosted search tool, the way Codex and Claude search. The
  adapters say which wires have one (`Protocol.hosted_web_search`): the
  Responses wires (OpenAI's platform, the ChatGPT sign-in) send
  `{"type": "web_search"}`, and Anthropic's own API sends
  `web_search_20250305` (at most three searches per call). A Chat Completions
  gateway has none. A tool spec with a `hosted` entry goes out as that entry
  on every wire that has one. The call asks for one `title | url | summary`
  line per page, at low effort when the chat's connection takes an effort. The
  stream yields `("web_sources", …)` for the pages the provider reports (the
  Responses `web_search_call` sources, which OpenAI's platform sends when asked
  with `include`, its URL citations, and Anthropic's `web_search_tool_result`
  and citations). When sources are reported, only lines for those pages are
  kept, and reported pages the model did not list follow; a URL from the
  model's memory never passes. The call counts as chat usage.
- **Brave Search** (`brave`): the Brave Search API with the account's key in
  the `X-Subscription-Token` header.
- **SearXNG** (`searxng`): a SearXNG instance's `/search?format=json`. The
  account's own URL goes through the SSRF guard, so it must be a public host.
  The server's `GAMMA_SEARXNG_URL` serves accounts without their own and may
  be on the private network, since the admin chose it.

`engine` picks one: **Automatic** (the default) takes Brave or SearXNG when set
up, else the AI connection; a named engine is used only when it is available,
so a missing key never switches services silently; **Off** turns general web
search off.

#### Online search services

Settings → AI → Tool usage → **Online search** (`settings/OnlineSearch.jsx`) holds
**Search the web with** (the engine) and one row per service: Brave Search (API
key), SearXNG (address) and OpenAlex (optional API key). Each row has Set up,
or Test, Edit and Remove. Test runs one small search with the stored settings.
The settings are the account's, stored in `users.db` `user_prefs` under the
reserved account-wide `search-services` key (`{"engine", "brave": {api_key},
"searxng": {url}, "openalex": {api_key}}`, each with `updated_at`). Like
`ai-settings` and `translate-engines`, the generic `/api/prefs` endpoints refuse
the key. `GET /api/ai/search-services` masks secrets to their last four
characters; a secret left empty on save keeps the stored one. Guests cannot
store keys. The routes are in [api.md](api.md).

#### fetch_paper

`fetch_paper` takes a `source` (DOI, arXiv id or http(s) URL) and reads the
document in windows with `read_page`'s knobs: `pdf_chars` (default and cap
from the Read window preference, shared through `_window_args`), `pdf_page`,
`pdf_offset`, and an excerpt that names the next offset while text remains.
`mode: "probe"` reads only `PROBE_CHARS` (1 500) of front matter with the
version and the identity check, for deciding whether a candidate is the
right paper before a full read costs a window; its excerpt ends by saying
to call again without `mode` to read it, and its chip reads "front matter
only".
The PDF behind the source comes from `routers.pdf.resolve_source`, the
resolver the extension and the "open a link" path use (arXiv abs/html → pdf,
publisher `citation_pdf_url` tags fetched with the article page as `Referer`,
up to four Unpaywall open-access copies, browser headers). The whole fetch — the
resolver's walk, the download and the page fallback — runs in one
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
model can cite pages. The resolver's open-access version note is retained in
the cache and every reading window, so a submitted preprint or accepted
manuscript is not silently presented as the publisher's PDF. The head also
names the version the resolver established (`version`: the publisher's PDF,
an arXiv preprint, or an open-access published, accepted or submitted copy;
unknown for a PDF a link served directly).

Two optional arguments carry what the model knows. `title` (the paper's exact
title) is checked against the document (`ai_web.identity`: the normalized title
on the first three PDF pages, or in a web page's text): the head says it
appears, or warns that it does not and the document may be another one.
`version: "published"` refuses open-access copies that are not the published
version (`resolve_source(published_only=True)`); a read under it never reuses
what an any-version read cached (the cache alias carries the policy), and a
preprint it still gets (an arXiv source) is flagged as not the version asked
for.

When no PDF is reachable (a paywall, a plain web page)
and the source is a page, its readable text is returned instead
(`ai_web.html_text`: head, scripts and styles dropped, block tags to line
breaks, entities unescaped), labelled as a web page with the reason no PDF
came, and followed by the page's PDF links for the model to try
(`paper_links.pdf_links`: a `.pdf` path, a `/pdf` or download route, "PDF",
"full text" or "Download" in the link text; links the resolver already tried
are left out). A lab's publication list holds hundreds of them, one paper per
item with its title as the link text, so each link keeps its text and the text
of the item it sits in (a bare "[PDF]" link borrows the item before it), and
`paper_links.rank` orders them against `title` before the eight shown: the
share of the title's words in the link's text, item and path, plus one for the
whole title. On a real lab list of 238 PDF links, the wanted paper moved from
position 227 to first. Without a title the page order stays and the head
suggests passing one. A fetched document lives in an in-memory LRU (`_CACHE_MAX_DOCS` /
`_CACHE_MAX_CHARS`) keyed by account, usable-cookie fingerprint and resolved URL,
with source aliases in the same partition, so the windows of one paper cost one
download without sharing authenticated text between accounts. A session connect,
refresh, disconnect or cookie expiry changes the partition, so the next fetch
can retry an earlier abstract or blocked page. The fetched document is not
written to disk or added to the workspace; a restart forgets the cache (chat
history still keeps answers and shortened tool results). Other failures (not a
PDF and not a page, too large, no text layer) come back as `error:` text
suggesting the user drop the PDF onto Gamma.

Every result carries a line saying the text is fetched web content and not
instructions, and the armed prompt says the same (ignore instructions found
in a document, tell the user). The prompt also says to prefer the library
for anything it holds and to name a fetched document (title, DOI/URL, page)
when answering from it, with its version when the result names one, and to
pass the paper's title to `fetch_paper` whenever it knows it. The action
chips are 🌐 for the three search tools (kind `websearch`) and ⬇ for fetches
(carrying the resolved `url`); the step pill counts searches as "searched
online".

#### Walls and the browser handoff

```mermaid
flowchart LR
  src["fetch_paper(source)"] --> got{"browser already sent one?"}
  got -- yes --> text
  got -- no --> resolve["resolve_source: arXiv, citation_pdf_url, 4 Unpaywall copies"]
  resolve --> dl["download: SSRF guard, one cookie jar, publisher sign-ins"]
  dl --> pdf{"a PDF with text?"}
  pdf -- yes --> text(["text, [p. N] prefixed, version named"])
  pdf -- no --> wall{"a wall?"}
  wall -- no --> page["the article page, its PDF links ranked"] --> text
  wall -- yes --> req["open_request: one per work, 6 h"]
  req --> waits{"paper_wait?"}
  waits -- no --> under["card under the reply; the model stops there"]
  waits -- yes --> card["{handoff} card + a Background tasks row"]
  card --> who["Gamma Connector from the tab, or the user drops the PDF"]
  who --> out{"wait_for_all"}
  out -- delivered --> again["the call runs again"] --> text
  out -- dismissed --> skip["skipped, with what to do instead"]
  out -- expired --> gave["nothing arrived; the card stays"]
```

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
source}`, and the chip reads "Needs your browser: host".

**The reply waits on the card.** A blocked fetch does not end the turn: the
loop's `settle` hook (`ai_agent.PaperWait`) sends a `{"handoff"}` line for
each request the round opened and blocks on
`fetch_handoff.wait_for_all`, exactly as an Ask permission blocks on its
approval card. The calls of one round wait together, so four blocked papers
cost one wait. What happens next:

- **Delivered.** The same call runs again. `fetch_paper` reads a delivered
  PDF before it fetches anything, so the model gets the paper's text where
  it asked for it, with `delivered: true` on the chip. No second turn, and
  no message in the user's name.
- **Skipped.** The card's Skip settles the request (`DELETE …/<id>` with an
  optional `note`, like declining an approval): the model hears that the
  user did not want it and what to do instead (`ai_tools.skipped_fetch`),
  and carries on.
- **Unanswered.** The wait gives up after `IDLE_TIMEOUT` (5 minutes of
  nothing happening; every report from Gamma Connector starts that stretch
  over, up to `MAX_WAIT`, 15 minutes). The model is told what blocked the
  paper and that nothing arrived (`ai_tools.unanswered_fetch`), the request
  stays open for its 6 hours, and its card stays under the reply.

A wall on the PDF still falls back to the page's text, so the model keeps
what was readable; an article-page-only read carries the same card. With
`search_web` armed and the chat told not to wait, the model may first run
one search for the paper's exact title to read another legitimate copy (an
author's or lab's page, a repository) and say which version it read, unless
the user asked for the publisher's own copy. The resolver has already tried
the open-access copies by then; the card stays either way. The armed prompt
says which of the two it is — the reply waits, or the card is under it.

**"Don't wait in this chat."** Skip's second button
(`withoutPaperWait` in `chat/approvals.js`) records the choice for that
conversation in this browser, beside the permissions its approval cards
allowed, and its requests then send `paper_wait: false`. The reply ends on
the card instead, the model is told to finish, and **Continue with the PDF**
under the reply asks the chat to go on once something arrived. The chat
never sends that continuation by itself: a message in the user's name is
the user's to send.

The card is the same component live and afterwards
(`chat/FetchHandoffCards.jsx`; its rules, tested, in `chat/fetchHandoff.js`)
— `LiveHandoffCards` while the reply waits, `FetchHandoffCards` over a
finished reply's actions for the requests it left behind:

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
- **Fetch blocked papers in the background** (Settings → AI → Tool usage → Tools,
  `fetchInBackground`, account-wide, on by default): a card in the
  conversation's last reply hands its request to the Connector without a
  click (`autoOpens`), once, and not again after its tab was closed. The
  Connector tries in an ordinary tab next to the Gamma one — unfocused, so
  the user keeps reading, but there in the tab strip to switch to — three
  requests at a time, and closes the tab after delivery; the card reads
  "getting it in a tab of its own", or that the request waits for the papers
  before it. A tab the user switches to is theirs from then on: it stays
  open, and frees its turn (`chrome.tabs.onActivated` in the worker).
  Nothing solves a CAPTCHA: what completes by itself is what the browser gets
  unasked — the user already signed in (or on the institution's network), or
  a check that passes a real browser on its own. When the page needs the user
  (a `NEEDS_YOU` note), **Show the tab** becomes the card's main button and
  moves the tab next to Gamma.
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
- **Skip** (live) or **Dismiss** (afterwards) settles the request; the
  Connector closes a background tab it kept for it. Skip opens one line,
  "What should the assistant do instead? (optional)", with **Skip** and
  **Skip, and don't wait in this chat**.

The card asks the server every 2.5 s while the user is at the page, every
10 s while the request waits in the conversation's last reply, and otherwise
when the window regains focus. A delivered PDF (`POST …/pdf`: at most
`FETCH_MAX_BYTES`, a `%PDF` with a text layer; 409 once the request is
settled) is extracted and kept with the request for its account only
(30 M characters across requests, the oldest dropped), and the PDF itself is
held for saving (200 MB across requests, the oldest let go first; its text
stays). `fetch_paper` reads it
before any fetch, for the same work in any spelling or the request's URLs,
with a source note saying the user fetched it in their browser.

A request still waiting also shows in **Background tasks** as a read-only
`paper-handoff` row (`GET /api/jobs` adds them from
`fetch_handoff.waiting`, the way it adds a running scheduled backup), so a
card whose reply has scrolled away is still somewhere to find; a click on
the row opens the publisher's page through `/go`
([tasks.md](tasks.md)).

Every reply that read or named papers ends with a **Save to library** pill
(`chat/ReplyPapers.jsx`, rules tested in `chat/chatPapers.js`): what its
`fetch_paper` calls read (the action carries the document's `title`, `pdf`,
and `request` when the user's browser delivered it), what a wall stopped
(the handoff's request), and the DOI / arXiv links in its text, once each
by identifier. Opening it asks the library which it holds already (`GET
/api/library/lookup`) and the registry for titles the reply left out (`GET
/api/library/preview`). The checked papers are saved one by one through
`POST /api/clip`, the Connector's ingest (dedup, resolve and store the PDF,
file the page, look its metadata up), into the folder the chat is about
(the viewed folder, else the open paper's first) with Settings → Reading's
open-access, stored-copy and metadata choices. A paper whose PDF came from the
user's browser is stored from the held copy first (`POST
/api/ai/handoffs/<id>/store`); the rest the server fetches again, and one it
cannot reach is saved as a page with its web source. Viewers and share
links get no pill.

Guest and share-link chats get no card. Their access failures still explain
the Connector's **Publisher sessions**, **Connect this publisher** /
**Refresh now**, the **Use journal sign-ins** permission, and the alternative
of saving the PDF from a browser tab: login or CAPTCHA completion is the
user's browser task, and transferred cookies cannot guarantee access through
a challenge bound to that browser or IP. The model is told not to repeatedly
retry a blocked URL and to respect rate limits. Reading an uploaded library
page requires **Read pages** and selecting that page as context.

#### read_paper

Delegation, under the same **Fetch documents** permission as `fetch_paper`.
`read_paper(source, question, title, version)` hands one document and one
question to a second agent (`ai_agent.Helper`): it fetches the paper, reads
as many windows as the question needs, and hands back one cited paragraph.
The windows stay in the helper's own conversation, so the chat carries an
answer of at most `_HELPER_ANSWER_MAX` (4 000) characters instead of a
forty-page paper it would have to re-send every round for the rest of the
reply. Four papers asked the same question cost about what one full read
would.

The helper runs the same `AgentLoop` with `fetch_paper` as its only tool,
no gate and no settle, at most `HELPER_ROUNDS` (12) rounds, on the chat's
own connection with its own prompt (`READ_PAPER_PROMPT`: one document, one
question, a page number beside every number, say plainly when the document
does not answer it). Nothing it does can change the library, its pictures
never leave it, and its cache key is the conversation's with `:helper`
appended so the two prompts do not fight over one prefix cache.

The parent's chip carries the document the helper actually read (`url`,
`title`, `pdf`, `version`), its calls as `children` (up to 12, without
their output — a child's text is the helper's, not the chat's), and
`spent`, the helper's token counts, which the loop yields as a `usage`
event so the reply's footer counts the whole answer. A wall the helper met
cannot show a card from in there, so its request rides up on the parent
action as the `handoff`, and the chat's own card and wait take over
([above](#walls-and-the-browser-handoff)).

The tool is offered only when **Read long papers with a helper**
(`gamma-ai-delegate-reads`, account-wide, on by default) is set — the
request's `delegate_reads`, which the scope carries as `delegates` and
`available()` turns into the entry's `needs: "helper"`. The armed prompt
says when to reach for it (the answer could be anywhere in a long
document, or the same question over several papers) and when not (the
document's own wording, a table, a quotation, or an abstract that already
settles it).

### save_paper (both scopes)

Adds a paper to the library when the user asks, the same way the reply's
**Save to library** does. `source` is a DOI, an arXiv id or an http(s)
URL; `title` is the exact title when known. The call goes through the clip
ingest (`routers/clip.py` `save_clip`, the body of `POST /api/clip`):

- the usual dedup by identifier;
- the PDF resolved, fetched and stored, or a page carrying the paper's web
  address when none is reachable;
- the page filed;
- the metadata lookup started in the background.

The request is built as `chat/chatPapers.js` builds it. An identifier's
DOI or arXiv page is the page's source, and a URL is also the address to
resolve. The account's Reading choices ride in the request as
`paper_save` (`{allow_oa, save_copy, fetch_metadata}`, missing means on),
the same ones the pill uses. When the user's browser delivered this source
through a fetch handoff, the held PDF is stored first (`storage.store_pdf`)
and the clip uses it. Journal sign-ins are bound from the chat scope like
`fetch_paper`'s.

`folder` files the paper (a path or an id, `_resolve_folder`; a missing
one is made when the call runs). A folder chat reads it inside its folder;
a page chat from the top, else the open page's first folder, which is where
the pill files. The clip gets the folder's id (`ClipRequest.folder`). A
paper already in the library is only filed. When it is in that folder
already (by id), `_plan_save_paper` answers that nothing changed, so there
is no card and the chip is a no-op.
In a page chat the new page is outside the chat's reach, and the result
says to read the paper with `fetch_paper`.

The approval card's preview has the paper's `title` (the one passed, else
the library page's, else the source), the folder it goes `to` and the source
as its `diff`. A paper the library holds adds `existed` and its `page_id`,
and the card reads "File … in …" instead of "Save … to …". Planning looks
nothing up online; the PDF is fetched only once the call runs.

One message saves at most `MAX_SAVES` (20) papers. The count lives in
`scope["tally"]`, which `run_agent_tool` creates before it copies the scope
for a mutation, so the copies share it. The action (kind `save`) carries
`page_id`, `title`, `to` (the folder, `""` for the library root),
`existed` and `pdf`. The chat lists it under "Changed in your library" as
"Saved {page} to {folder}", or as filed there when it was already in the
library. The agent prompt says to save only on request, never as a side
effect of reading. The permission is **Save papers** (`save`), a change
permission in every chat kind, Ask by default.

### rename_page / move_page (folder only)

`rename_page` changes a page's title. `move_page` files a page into a
(sub)folder named by path or id (`_resolve_folder`). A path no folder has
is created on the `folders` tree before the page's batch. The move is one
`set` of the page's `folders`: ids outside the chat's folder that still
exist are kept (`existing_in`), the in-scope ones are replaced by the
target, and a folder above the target gives way (`blocks_store.refiled`).
Both are reversible with another call. Their actions name the change for
the chat's change list: `title` (the page's title before the call), `from` /
`to` (the old and new title; the old folder paths, comma-joined, and the new
one, `""` for the library root). The result text adds "(a new folder)" when
the call made the folder.

### list_deleted / restore_page (folder only)

Recently deleted ([home_library.md](home_library.md)). A deleted page is in
a folder chat's reach when it was filed under the chat's folder; at the
library root, every deleted page is. A permission either reads or changes,
so the two tools have one each:

- `list_deleted`, under **List pages**, lists such pages, the last deleted
  first: id, title, the folders a restore puts the page back in (the trash
  entry's `folders` ids, read as paths), when and
  by whom it was deleted, and when it goes for good (`trash.list_trash`).
  `title_contains` filters by title. Its chip is a `list` one.
- `restore_page`, under **Restore deleted pages** (`restore`, Ask by
  default), runs `ops.restore_page`, the route's restore. The page goes
  back under the library root in its folders, with its notes, highlights,
  files and chats. `_plan_restore_page` answers a page that is not deleted
  (a no-op) or was filed outside the chat's folder (refused) without a card.

The approval card's preview has the page's `title` and the folders it goes
back `to`. The restore's action (kind `restore`) carries `page_id`, `title`
and `to` (its folders). The chat lists it under "Changed in your library".
Deleting is not offered.

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
They also refuse a text box, placed on its own page's PDF or sheet, unless
the sheet it is on moves with it (`_loose_text_box`).
All three go through the op path (`ops.apply_ops`, [collab.md](collab.md)):
logged, fanned out to anyone on the page, and the page root's `updated_at`
stamped so the home feed reorders. Their UI actions carry `page_id` (moves across
pages also `src_page_id`) and `block_id` (the edited/moved block, or the
created block's new id; `read_block` actions carry it too). The frontend
reloads the open page's block tree when it was touched and lights the block
up; edit/create calls are previewed in the block while the model is still
writing them (see "Watching the agent work" in [ai.md](ai.md)). Each of
the three records what undoing it needs, so the user can revert it from the
reply ("Reverting a note change" below). No tool deletes under any
permission: an unwanted block is emptied or left for the user.

Typical uses: *"rename these to AuthorYear style"*, *"file the readout papers
into a subfolder"*, *"which of these papers measure T1? summarize the
approaches"*, *"where did I note something about bias-preserving gates?"*
(a notes hit with its block id), *"tidy my notes on this page into
sections"* — and in a page chat, *"where does this paper define the
protocol?"* (it searches inside the PDF and quotes page numbers) or *"add a
summary block to my notes"*. Handwriting, citations, saving and Recently
deleted: *"transcribe my handwriting on this page"*, *"BibTeX for everything
in this folder"*, *"save the three most cited follow-ups into refs"*, *"bring
back the page on Rydberg blockade I deleted last week"*.

## Guardrails

Deliberately not offered under any permission:

- Deleting anything — pages, blocks, folders, files.
- Editing highlight anchors or labels, creating or renaming folders on
  their own (a folder is made only as `move_page`'s or `save_paper`'s
  target), and refiling except through `move_page`.
- Reading library pages outside the base scope and attached references, or
  editing pages outside the base scope. The server checks every call.
- Reaching uploads, share links, settings, or other users' data.
- Adding a paper as a side effect of reading it: `fetch_paper` never
  creates a page. Only `save_paper` does, under its own permission and on
  the user's request.

Tools whose permission is Off are not offered to the model, and the server
additionally refuses to execute them if called. A tool whose permission is
Ask is offered, but each call waits on an approval card and runs only once
the user allows it. Output/argument sizes are capped
(`_LIST_CAP` on listings; `_DETAIL_CAP` and `_ARG_CAP` cap the saved chip
only — the model gets the full result, under the live budget
`ai_context.LIVE_RESULT_BUDGET`), and the loop itself is bounded —
rounds and a ≤200-mutation guard, detailed in [ai.md](ai.md).

**Every tool call is shown in the reply**, reads included, so there is
always a visible record of what the agent looked at and changed. One pill
sums them up ("6 steps · listed, read 1 page · 1 failed") and expands to a
line per call, its icon naming the action kind (`ACTION_ICONS` in
`chat/AgentChanges.jsx`: list, book, search, eye, pen, quote, globe, download,
file-plus, history, pencil, folder, plus). Each line expands to the
arguments and the output the model got. Everything that changed is listed
again under the pill: "Changed in your library" (renamed, filed, saved and
restored pages) and "Changed in your notes" (edited, added and moved
blocks), each entry a link to the page or block. The note tools' actions carry their page's `title` for that list; a
change tool that changed nothing is marked `noop` and not listed.

### Reverting a note change

Each row under "Changed in your notes" has a revert button, and the list a
"Revert all" when it holds more than one change. The user takes back one
change at a time, in any order, and whatever anyone wrote since stays. A
reverted row has a redo button that puts the change back.

The note tools record what undoing and redoing their change needs, as
`revert` on the action:

| Tool | `revert` |
| --- | --- |
| `edit_block` | `before` and `after`: the block's text just before and just after the write, read under the write lock |
| `create_block` | `after`: the text the block was made with; `parent` and `position`: where it went |
| `move_block` | `parent` and `position`: where the block was; `to_parent` and `to_position`: where it went |

The chat saves the action with the reply. A revert sends it to
`POST /api/ai/revert` (`gamma/ai_revert.py`), which plans and writes under
one write lock:

| Change | Reverted by | Stops (409) when |
| --- | --- | --- |
| edit | merging the change `after → before` into the text stored now (`textmerge.merge` with word-level hunks, `semantic`) | `changed`: someone changed the agent's own text |
| new block | deleting it | `filled`: it was typed in or has notes under it (`preview.children`) |
| move | moving it back to the old parent at the old key, re-keyed if a sibling took it (across pages: `ops.move_across_pages`) | `moved`: it was moved on since |

Typing elsewhere in an edited block survives the merge. So two edits to
different parts of one block revert independently. The merge works on
words and phrases, not characters: a later rewrite of the agent's sentence
then clashes with the revert as a whole, where characters would interleave
the two. A change taken back already is a no-op (`noop`): an edit the text
still holds reverted (`textmerge.contains`), or a new block that is gone.

A redo (`redo: true`) runs the same plan the other way. An edit merges
`before → after`. A new block is inserted again under its old id, parent
and key; with its parent gone it is `gone`. A move goes to `to_parent` at
`to_position`, and stops with `moved` when the block left the place the
revert put it. An older move without `to_position` goes last under its
parent, and an older new block without `parent` cannot be redone. A redo of
a new block brings back the agent's text only: notes deleted with it by a
forced revert stay gone.

A stop carries `preview.diff`, what `force` would write. A forced edit
writes `before` with the later changes that don't overlap the agent's
text. The user's overlapping words are lost, and the diff shows them struck
out first. Some stops are `gone` and cannot be forced: the old parent was
deleted or is now inside the block, or the highlight or text-box rule
refuses the move back across pages. A deleted note answers 404.

A revert is the user's own write: `actor` is the account and the op log's
`client` is `"revert"`. It fans out like any edit, so the open page and
collaborators see it live. A redo is the same. The chat then marks the
action `reverted` (a redo drops the mark), saves the conversation
(`chatSession.edit`), and reloads the touched pages when no socket brought
the ops.

In the list, a reverted row shows "Reverted" and its redo button. A stopped
row says why, shows the diff, and offers "Revert anyway", "Delete anyway"
or "Move back anyway" (for a redo, "Redo anyway" or "Move anyway") beside
"Keep it". "Revert all" goes newest first and stops at the first row
that asks. The buttons wait while a reply streams. A viewer and the share
view get none.

The request's history leaves `revert` out of the actions it sends
(`chat/aiRevert.js` `forReplay`). The replay puts "the user reverted this
change afterwards" before a reverted action's result, so the model does not
build on it ([ai.md](ai.md) "Replay across turns"). An action without
`revert`, saved by an older version, shows no button. Ctrl+Z does not
reach any of this: the agent's writes and the reverts both arrive as remote
ops, which the undo history never records ([collab.md](collab.md)).
