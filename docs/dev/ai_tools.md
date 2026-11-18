# Agent tools

What the library agent's tools do, how each one is used, and the guardrails
around them. The registry lives in `gamma/ai_tools.py`; the surrounding wiring
— scopes, permissions, the tool loop, replay — is in [ai.md](ai.md).

Every tool is one `TOOLS` entry declaring its wire spec, Settings permission
key, allowed scopes, mutating flag, and executor — so arming a chat is one
filter (`agent_tools`), dispatch is one lookup (`run_agent_tool`), and the
in-scope check (`_load_scoped_page`/`_scope_pages`: folder = the page is
filed in the chat's folder or below it, `_reach` / `_filed_in` over
`blocks_store.folder_subtree_ids`; page = id equality, and for a reading
tool the pages inside the page too) is shared by every executor. A mutating entry also has a `preview`: what its approval card
shows when the user set its permission to Ask. Each changer has three
parts: a `_plan_*` function checks a call and works out the change, the
executor applies the plan, and the preview describes it. So the card and
the change come from one place ("Asking before a call" in
[ai.md](ai.md#asking-before-a-call-approvals)). A permission is either
reading or changing: its tools share the `mutating` flag, which also sets its
default (reading allowed, changes ask; `ai_permissions.permission_state`).
A cursor block (`focus_block_id`), attached block chips (`context_blocks`),
note selections and selected handwriting (`ink_selections`, a picture of
the strokes with their block's id) reach the model with the message, so
"this block" / "these" resolve without a `read_block` round-trip (see
"Pointing the chat at notes" in [ai.md](ai.md)). `agent_system` never names
them: it holds only what stays the same for a whole conversation, since the
system prompt is part of the cached prefix ("Prompt caching" in
[ai.md](ai.md)).
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
id. A path no folder has is an error for a read (`_find_folder`), and for
`rename_folder` and `delete_folder`. It is made for `move_page`
(`blocks_store.folder_inserts`, in the call's own transaction) and
`save_paper` (`_made`, `ops.ensure_filing`), only when the call runs: a
preview makes nothing. A one-word argument that is an id but no folder's —
a page's, a note's, a label's, or a folder's deleted in the last day (the
folder tree's op log) — is refused, not made (`_not_a_folder`); an id
deleted longer ago still reads as a name. Labels are named by name
(`_find_label`: exactly, else ignoring case; a name several labels share
is refused). The folder and label tools read a label's name on one line
(`_labels_called`), so a label an import kept with a run of spaces is
found, not made a second time.

A page inside another page, a sub-page ([home_library.md](home_library.md)
"Sub-pages"), is filed where its top page is: the tools read its folders
as its top's (`_load_scoped_page` through `blocks_store.filed_folders`),
so a folder chat reaches the pages inside the pages filed under its
folder, `list_pages`' folder filter finds them there, and a page chat's
`save_paper` files where the open page's top is. Its own stored
`folders`, if any, are passed by, and `read_page`'s properties line names
its path and its top's folders, never them. Labels stay per page.

A page chat reads the pages inside its page, at any depth, as attached
context pages are read: `run_agent_tool` gives a reading tool's scope
`inside`, and `_page_in_scope` then takes a page whose ancestors hold the
chat's page (`blocks_store.page_ancestors`), `_scope_pages` the page's
descendants (`page_descendants`), so `read_page`, `read_block`, the view
tools, `read_chats`, `cite` and `search_library` reach them. A changing
tool's scope has no `inside` (nor does an approval card's preview): the
note editors still edit the chat's own page alone, and a page above it is
out of reach. The prompt says so ("the read and search tools the pages
inside it too").

Pages in Recently deleted are out of the tools' reach, the MCP adapter's
included: they are not under `root`, and `_load_scoped_page` and
`blocks_store.page_root_id` find no page for them or their blocks
([home_library.md](home_library.md) "Recently deleted"). Two folder-chat
tools are the exception: `list_deleted` lists them and `restore_page` brings
them back. `delete_page` moves library pages there, and deleting a folder
or a label never deletes one.

Attached library pages (`context_pages` in the tool scope) extend reading access
beyond the current page or folder. `_scope_pages` combines the base scope and
these references for reads and search. `run_agent_tool` removes `context_pages`
before dispatching a mutation, so attachments do not grant editing access.

## The tools

The [MCP adapter](mcp.md) exposes a read-only subset of this same registry to
external assistants. `agent_tools` filters definitions and `run_agent_tool`
enforces the caller's allowlist at dispatch. Gamma chat passes its armed tool
set. MCP passes `mcp_tools`: every reading tool that stays inside the
library, without the web tools, the change tools or `list_deleted`. Its
scope is read-only, and its `link_base` makes results carry absolute links
to pages, PDF pages, blocks and comment threads (`gamma_link`; the chat's
results carry none, [mcp.md](mcp.md) "Links in results"). The three
writing tools (`comment`, `resolve`, `highlight`) reach MCP at a
connection's Comment level, the four note tools at its Edit level
([mcp.md](mcp.md) "The Comment level", "Editing notes").

| Tool | Permission | Scope | What it does |
|---|---|---|---|
| `list_pages` | List pages | folder | List the folder's pages (at the library root, the pages the library lists): id, title, a work folder's file's `path=` in it, attachments (the document's format, `[pdf]`, `[text:latex]` with a text file's language, `[binary listed]`, `[]` for a page of notes) and `size=`, folder paths or, for a sub-page, the page it is inside (`parent=`), labels, cached metadata (first author, year, venue), last-update date, most recently added first (library order), in batches (`limit`, `after`) |
| `list_folders` | List pages | folder | The folder tree below the chat's folder: every path with its implied parents, subfolders indented, library pages filed directly and in total; the pages in no folder at the root |
| `read_page` | Read pages | folder + page | Read one page, by id or a work folder's file by its `path`: title, the pages it is inside and the pages inside it, properties, the user's highlights and notes, its open comment threads, and its document's text — a PDF's extracted text in windows by page and offset, a text file's numbered lines in windows by `from_line` / `to_line` |
| `read_block` | Read note blocks | folder + page | Read a page's notes as an id-prefixed outline — the ids the editing tools take |
| `read_chats` | Read pages | folder + page | Read the AI chat kept with a page or folder: the current conversation as a numbered transcript, the earlier ones by `chat_id` |
| `list_annotations` | Read pages | folder + page | The highlights and comment threads of a page, a folder or the library (a page's or a folder's id, a folder's path, a work folder by name, `home`; the chat's own page or folder without one), each with its file, place, source line, note and a thread's comments, as Markdown, in batches (`limit`, `after`) |
| `read_annotation` | Read pages | folder + page | One annotation, a thread in any state or a bare highlight, as Markdown and as JSON, with the pictures of its region now and as it was commented on |
| `view_pdf_page` | View pages and handwriting | folder + page | Look at one page of the page's PDF as a picture — a scan with no usable text layer, a figure, a table's layout |
| `view_ink` | View pages and handwriting | folder + page | Look at the user's handwriting as a picture: a group's strokes on their PDF page or page of paper, cropped to them, or the whole page with all its handwriting |
| `view_image` | View pages and handwriting | folder + page | Look at the pictures a note block embeds (`![alt](/api/uploads/…)` in its markdown), or a picture block's own, all of them or one by index |
| `cite` | Read pages | folder + page | The citation records kept with pages: the paper metadata, its BibTeX and the slide citation, for up to 50 pages |
| `search_library` | Search library | folder + page | Full-text search over the reachable pages' notes AND their documents' text; note hits name the block id and page, PDF hits the page number, file hits the file's path and line. `search_pdfs` is its deprecated alias (replay only) |
| `search_papers` | Search online | folder + page | Scholarly search outside the library — Crossref, arXiv and OpenAlex at once, or a direct DOI / arXiv-id lookup — returning merged registry records (citation count, the start of the abstract) with the `doi:` / `arXiv:` string `fetch_paper` takes; an optional year filter and citation or recency order |
| `related_papers` | Search online | folder + page | One step through OpenAlex's citation graph from a DOI, arXiv id or exact title: the works it cites, the works citing it, or related works, most cited first |
| `search_web` | Search online | folder + page | General web search through the account's engine (Brave Search, Tavily, SearXNG or the chat's own AI connection), narrowed to the past day, week, month or year and to one site when asked: titles, URLs, the page's date when the engine reports one, and snippets, as leads for `fetch_url` (a paper's for `fetch_paper`). Offered only when an engine is available |
| `fetch_paper` | Fetch documents | folder + page | Read a paper that is not in the library by DOI, arXiv id or the URL of its page or PDF: the PDF behind it (same resolver as opening a link, several open-access copies tried) in `read_page`-style windows, else the page as `fetch_url` reads it, with its PDF links; nothing is stored. The result names the PDF's version and checks it against the paper's title when given. A sign-in, bot check or paywall hands the fetch to the user's browser through a card in the reply |
| `fetch_url` | Fetch documents | folder + page | Read any page or file at an http(s) URL in one GET: a web page's main content as Markdown with its links, title, site and dates, and a long page's sections with their offsets; plain text, JSON, XML and CSV as they are; a PDF page by page. `find` starts the window at a phrase. A wall is a sentence for the model, never a card; nothing is stored |
| `save_paper` | Save papers | folder + page | Add a paper to the library by DOI, arXiv id or URL, through the ingest the reply's Save to library and the Connector use |
| `rename_page` | Rename pages | folder | Change pages' titles, up to 50 in one call |
| `move_page` | Move pages | folder | File pages into a (sub)folder, up to 100 in one call; never a sub-page, which goes where its top goes |
| `list_deleted` | List pages | folder | List Recently deleted as far as the chat's folder reaches |
| `restore_page` | Restore deleted pages | folder | Bring pages back from Recently deleted, filed where they were, up to 100 in one call; the user can revert it |
| `delete_page` | Delete pages | folder | Move pages to Recently deleted with the pages inside them, up to 100 in one call; the user can revert it |
| `rename_folder` | Organize folders and labels | folder | Rename a folder; a name a folder beside it has is refused; never a move |
| `create_label` | Organize folders and labels | folder | Make a label with no pages (an existing name is reused) |
| `rename_label` | Organize folders and labels | folder | Rename a label, where every page carrying it is in reach |
| `label_page` | Organize folders and labels | folder | Put labels on pages or take them off, up to 100 in one call, making a missing label |
| `delete_folder` | Delete folders and labels | folder | Delete a folder and its subfolders, never their pages; the user can revert it, the folders' chats included |
| `delete_label` | Delete folders and labels | folder | Delete a label and take it off its pages; the user can revert it |
| `edit_block` | Edit note blocks | folder + page | Change one note block's markdown text: replace it, add to it, patch one passage, or rewrite the user's selection |
| `create_block` | Edit note blocks | folder + page | Add a note block under a page or block, optionally after a sibling |
| `move_block` | Edit note blocks | folder + page | Re-parent/reorder a note block (with its subtree) |
| `delete_block` | Edit note blocks | folder + page | Delete a note block with everything under it; the user can revert |
| `clip_region` | Edit note blocks | folder + page | Store a picture of a PDF region (with the handwriting on it, if asked) or of a handwriting block as an upload the notes can embed, and return its URL |
| `comment` | Comments and highlights | folder + page | Write a comment as the chat's agent, for its account: a reply in a thread, or a new thread on a PDF quote with its page, a text file's lines, a note or the page itself |
| `resolve` | Comments and highlights | folder + page | Resolve a comment thread once it is done; never a reopen |
| `highlight` | Comments and highlights | folder + page | Highlight a PDF quote or a text file's lines as the chat's agent, with a note, a colour and a style |

### list_pages (folder only)

Optional `label` / `folder` / `title_contains` / `parent` filters narrow the listing, or
`list_labels: true` returns just the label vocabulary with counts (at the
library root every label, an unused one as 0 pages; in a folder the labels
its pages carry). Each line shows the page's `folders=["A / B", …]` paths
and `labels=[…]` names, and its document as `attachments=[<format>]` (a
text file's with its language, `[text:latex]`; a work folder's file listed
without its bytes `[binary listed]`) with its `size=` in bytes when
stored. A work folder's file shows `path="sections/method.tex"` after its
title, its path inside the work folder (`work_folders.paths.page_path`,
the folder tree read once a call), which `read_page(path=…)` takes. A
sub-page's line shows `parent=<page id>`, its
effective parent (`blocks_store.page_parents`), in place of `folders=`: it
is filed where its top is, and naming those folders would invite a
`move_page` on it. A `folder` filter (a path or an id,
`_resolve_folder`) lists that folder's pages and its subfolders', and the
pages inside them; `label`
matches a name exactly, else ignoring case. `parent` (a page id) lists
the pages right inside that page, which is how the agent walks a page
tree past what `read_page`'s header names; it rides along in the
next-batch call, the summary reads "… inside page <id>", and a listing
with nothing in it says "No pages match those filters."

At the library root the pages are those the library lists
([work_folders.md](work_folders.md) "The library rule",
`work_folders.library.listed`, by a sub-page's top): a work folder's file
not in the library is listed only where a folder named files it, the
chat's own folder or the `folder` filter (a directory of the work folder,
or the work folder itself), and the root's label counts and `label=`
count listed pages alone. A page in the chat's context is listed whatever
it is. An empty root reads "The library lists no page." The tools that
change a page or a folder ask the work-folder rule up front, as a person
(client `ai`), and answer what it refuses in its words: `rename_page` of
a file named by its file, `move_page` of a file filed by its directory
(a note of a work folder that pulls moves between its directories, its
directory leading its folders), `move_page` of a library page into a work
folder, `delete_page` and `restore_page` of a file that leaves and comes
back with its file, `rename_folder` of a work folder's directory (its
root takes a new name in Gamma), and `delete_folder` of a directory, of
the root, or of a folder holding one ([work_folders.md](work_folders.md)
"The op rule"). A folder named by its path for a write (`move_page`,
`save_paper`) passes by every work folder from the top, so a name it
shares makes a library folder; by its id, or in a reading tool, a work
folder's directory is reached, and a write there goes by the rule.

Pages come most recently added first: library order backwards
(`position`). No op moves a page, so renaming, filing, labelling or
editing one never changes its place. A new page goes last in the
library, and so first here, unless it was synced in or placed by hand,
which takes its own place. A restored page comes back last too. A call
shows at most `limit` pages (default and most `_LIST_CAP`, 400). When
more match, the header names the batch and the next call ("7, most
recently added first; 4–6 shown; next: list_pages(…)"), and the result
ends with the same call: `[… N more pages — call list_pages(<the same
filters>, after="<last id>") to continue]`. The header copy survives the
saved chip's `_DETAIL_CAP`, which a long listing's tail does not.

`after` is a page id. The listing goes on after that page's place
whatever the filters match now, so a batch the agent renamed or
refiled away is neither repeated nor skipped. A page that left the
library (Recently deleted, or gone) is an error. A page restored
meanwhile starts the walk over: pass the id of a page listed before
it. `after` is not checked against the chat's reach. After Continue the
replayed call keeps its `after`, so at most one batch repeats, and a
change already made answers as already done.

`list_labels` lists the busiest `limit` labels and names how many more
there are. It does not page: its counts move as pages are labelled, and
every label is found by name.

With `list_pages` and a page-changing tool armed, the prompt asks for a
batch at a time (`list_pages(limit=100)`): a full 400-line listing
outgrows `LIVE_RESULT_BUDGET` and is a stub two rounds later.

### list_folders (folder only)

How the library is organized, before acting on it or walking it: one line per
folder from the chat's folder (or from `folder`) down, `- [<id>] "a / b"`
indented by depth, with the pages filed directly in it and, when different,
the pages anywhere below it (`no pages` for an empty folder — folders are
real and may be empty). A page in two subfolders counts once in their
parent. At the library root a last line counts the pages in no folder.
The counts are of library pages, as the home view's are: a sub-page goes
with the page it is inside and counts nowhere. The
result ends by pointing at `list_pages(folder="<path or id>")` and
`read_page`, which is how an assistant traverses the tree. Shares the "List pages" permission
with `list_pages`.

### read_page (both scopes)

Returns a `page_report_section`: the page's title, where it sits among
pages, a properties line
(a work folder's file's `path:` and `work folder:`, folders, labels, cached
metadata, web source, `document: <format> "<name>"`; for a sub-page
`path: "Top" › "Mid" › "Page"` and `folders (its top page's): …`, the
folders it is filed in, `ai_context.page_properties_line` through
`blocks_store.filed_folders`), its document's text — an excerpt of a PDF's
extracted text, a window of a text file's lines (below), one line for a
document of another format — then the page's highlights and nested notes,
the text files its notes' chips link ([ai_context.md](ai_context.md) "File
chips") and its open comment threads (ai_context.md "Open comment
threads"). A page without a document returns its notes — they are its
content. The page is named by `page_id`, or by `path`, a work folder's
file's path (`_page_by_path`, through `work_folders.paths`): in the work
folder of the chat's folder, else of a page chat's page, else the one the
path's first segment names (`paper/sections/method.tex`); each name
exactly, else ignoring case, and a name without an extension that names
nothing tried with `.tex`, then `.bib`, as LaTeX writes them. No work
folder answers "no work folder is named …", several pages refuse naming
their ids, and the page then passes the scope check as any page. On MCP a
bare `read_page(path=)` with several workspaces reads the default one
unless `workspace` names another. `pdf_chars` sizes the window per call, capped by the
Settings / AI / Tool usage / "Read window" preference (`gamma-ai-read-chars` →
request `read_char_limit`, riding in the scope dict as `read_chars`; default
cap 20 000 — `agent_tools` formats the effective cap into the armed spec so
the model knows what it may ask for). `pdf_page` starts the excerpt at a
1-based PDF page (extract_text's `start_page` — how a `search_library` PDF
hit is followed up), and `pdf_offset` windows onward from there; while text
remains, the excerpt names the next offset, so long documents are read in
successive windows. The `pdf_*` names stay for compatibility; they mean
"the document's text".

A text file reads by lines. `from_line` (1-based) starts the window there
and `to_line` (inclusive) ends it; without `to_line` as many lines go as
fit `pdf_chars` with their numbers (`ai_context.text_window`, read from
the file, streamed: a 64 MB log read for its first lines reads a few
hundred KB). Each line is numbered as the context numbers it, under
`[Lines 50–52 of 120. …]`, and a window that stops short ends
`…[more lines remain — call read_page again with from_line=N to
continue]`. The first window (`from_line` 1, no `to_line`) is the
context's own pair, the head and the outline of the rest
(`ai_context.text_head`, kept per file version). A `from_line` past the
end answers "from_line 3000 is past the end — the file is 2,310 lines
long"; `pdf_page` / `pdf_offset` on a text file, and `from_line` /
`to_line` on a PDF, are answered with one line naming the other kind's
arguments and ignored. A secrets file answers that its text is kept from
the model, a listed file that there is nothing to read. The chip carries
`lines: [first, last]` and the summary `Read “main.tex” l. 1–400`; on
MCP the `[Links: …]` footer adds `line N: <url>&line=N (a range:
&line=N-M; add &quote=<the first line's verbatim text, …>)` and the
chip's `path`.

Where the page sits among pages comes right after its `Gamma page ID:`
line (`ai_context.page_family_lines`, so the chat's page context, MCP
`read_page` and `read_gamma_link` show it too): a sub-page's
`Inside page: "Top" › "Mid" (the parent's page ID: <id>)`, its effective
ancestors top first, and a page's `Pages inside (n): "Title" (page ID:
<id>); …`, the pages right inside it in library order. That line names the
first `SUB_PAGES_SHOWN` (50), then `… N more — list_pages(parent="<id>")
lists them all`; in a page chat, which has no `list_pages`, `… N more,
which this chat cannot list — search_library searches them all` (the
`listing` argument; the chat's own page context says the same in a page
chat). Titles are cut to 120 characters, and an empty one reads
"Untitled". A library page with nothing inside it has neither line.

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
`MAX_AREA_CROPS` (12) per page per read, a guard against a page covered in rectangles;
the rest say "no picture: more than the limit on this page". `read_block`
does the same on its outline lines, and the chat context does it for the
notes of a context page (the pictures go with the message's own, within
its picture budget — "Pictures" in [ai.md](ai.md#pictures); the coverage
entry's `area_pictures` says how many went, and the chat shows a "Pictures
of N area highlights sent" pill).

What the conversation context already holds is never sent again. The
chat's coverage report rides in the scope (`scope["coverage"]`,
`context_cover`): for a page whose head excerpt is in context, a read with
no offset of a PDF page the excerpt shows in full continues from the page
the excerpt cut short and says so; a document the excerpt holds whole
answers "nothing more to read" (notes only); and the notes and highlights
come once — with the first window of a read (`pdf_offset` 0, `pdf_page`
1) or on `notes: true` — capped at the read window (a hint points at
`read_block` for the rest), and not at all when the context carries them
(`include_notes`, or a page without a PDF); the open threads come with them,
and not for a page in the context, which holds them. A text file's entry
names its lines the same way: a read of lines the head shows continues from
the first line not shown ("lines 1–400 are in the context above; this read
continues from line 401"), a range inside them and a file held whole
answer "nothing more to read". The chip carries `pdf_pages`
(`[first, last]`, from `page_report_section`'s `report`) — what the chat's
coverage pill folds in as "read 3–5 with tools" — and its summary names
the span. The agent prompt says the same from the other side
(`coverage_lines`): which pages of which document are in context, and to
read from the cut-short page on; for a text file, which lines, and
`read_page(page_id, from_line=N)` for the rest.

### search_library (both scopes)

One query over both FTS indexes for the in-scope pages: the notes index
(`gamma/block_index.py`, in pages.db, kept current by every write — an
edit made a moment ago is found) and the document text index
(`gamma/search/`: `index.pending`, and the hits `search/hits.build` makes
for `GET /api/search` and `POST /api/document-search` too — the same
indexes and query rules as Ctrl+F).
Note hits come first as `- note [block_id] in "title" (page_id …): snippet`
— ids `read_block` and the editors take — then the documents' hits in the
index's one rank order: a PDF's as `- PDF "title" p.N (page_id …):
snippet`, a text file's as `- file "sections/method.tex" l.118 (lines
81–120; page_id …): <the matched line>`, its path in its work folder (a
library file's title), the line the query matched in the run of lines the
index holds and the run's span; on MCP each carries its link,
`&pdf_page=N` or `&line=N`. Un-indexed documents of every format are
kicked to the background indexer and counted ("3 document(s) not indexed
yet") so the model knows results may be incomplete. The MATCH ANDs every term, so a zero-hit query is
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
subtree). Highlight blocks show their quoted passage inline — a range of a
text file with its lines and how the last change left it, `(highlight:
"…"; lines 118–120)`, `…; line 47; not found in the current file)`, `…;
placed roughly)` (`ai_context.range_words`, the page section's words too) —
handwriting blocks a "handwriting on p. N" label before their caption; long child
contents are snipped per line with an explicit "read_block this id for the
full text" marker, and the listing stops at the read-window budget naming how
many blocks were left out. A text box ([text_boxes.md](text_boxes.md)) shows
"(text box on p. N)", "(text box on the page of paper above)" or "(text
box, not placed on a page)" before its text. The nearest sheet above a box
wins over its `pdf_page`.

A picture block ([pictures.md](pictures.md)) is labelled by the same rule
(`ai_context.placed_label`): "(picture on p. N)", "(picture on the page of
paper above)" or "(picture, not placed on a page)". Its text is its
`![caption](url)` line (`ai_context.picture_text`, `picture.markdown_of`):
the caption as a reader sees it, on one line, and the URL `view_image`
opens. A long caption is snipped before the line is made. The block's
stored text, the one `edit_block` changes, is the caption alone, and an
edit whose text carries the picture's own `](url)` is refused with that
rule, so the line read never lands in the caption. A picture
whose value names no file reads as its caption. (`read_page` shows the
same notes without ids — context for answering; `read_block` is the
editing view.) A note's file chip of a text file shows the file's first
lines under it, as the page section does ([ai_context.md](ai_context.md)
"File chips"); a secrets file's chip says its text is kept from the model.

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

### list_annotations and read_annotation (both scopes)

The highlights and comment threads of a page, a folder or the library: a
comment is the user's request, the passages quoted stay document data
([annotations.md](annotations.md): the entries, their places, sources and
pictures are `gamma/annotations/entries.py`'s, the text `markdown.render`'s).
Both share the "Read pages" permission and reach MCP by `mcp_tools`' rule.
The reader's modules load on the first call, since they bring SyncTeX and
the region pictures. Their names until step 3.1 of
[research/agents-as-participants-plan.md](../research/agents-as-participants-plan.md),
`list_feedback` and `read_feedback` (read_feedback's `thread_id` too), stay
callable through `DEPRECATED_TOOLS` so a saved chat replays.

`list_annotations` lists the entries of a scope in the Markdown form the
annotation files use (`pictures="tool"`: no image, a `Picture:
read_annotation(id=…)` line instead), one document headed `# Annotations on
<name>` (`the library` for the library), a batch at a time (`limit`,
default 20, at most 50; `after` the entry the previous batch ended at, and
the result names the next call when more are left; a thread answered or
resolved since its batch still marks where the listing goes on). `scope`
(`_annotation_scope`) is `home` (the library), a page's or a folder's id
(a work folder, a directory of one, a library folder), a work folder by
name (exactly, else ignoring case; exactly one), else a folder by its path
as the tools show it (`_find_folder`); left out, the chat's own: a page
chat's page, a folder chat's folder, the library at the root. `state` open
(the default), resolved or all, which filters the threads alone: a bare
highlight is always listed. The reach is the chat's: a
page chat reads its own page (and the pages in its context), a folder chat
the folders and pages below its folder, `home` at the library root alone;
anything else is "that folder is outside this chat's scope" or the page's
refusal. The document's `owner` is the chat's account, which an agent uses
to tell the person's comments from other members'. The action carries
`page_id` when the scope is one page.

`read_annotation(id)` reads one item, a thread in any state or a bare
highlight by its block id: its Markdown form,
then the entry as JSON (with the `picture_url` / `before_url` routes), and
its pictures — the region now, then, after a rebuild, as it was commented
on — on the action's `images` as `view_pdf_page` carries its picture,
unless the model reads text only (`scope["pictures"] is False`; the tool is
not in `PICTURE_TOOLS`, its text being useful alone). The action carries
`page_id`, `block_id` and `pdf_page`, so MCP's `structuredContent` locates
the entry. A reply's id, a hidden thread, a highlight that has a thread
(read by the thread's id) and an unknown id are "no annotation has that
id".

`gamma_link(scope, page_id, *, thread_id=…)` links a thread
(`&page=&block=&thread=`, the address the Inbox opens), so an MCP entry
links its card; in the chat the entries' links are their queries alone.

### view_pdf_page (both scopes)

A model that reads text only is offered neither this nor `view_ink`
(`PICTURE_TOOLS`, [ai.md](ai.md#other-services)).

The model's eyes on a PDF: `page_id` + 1-based `pdf_page` rasterize that
page through MuPDF (`pdf_text.render_page`, under the same lock as every
other PyMuPDF call, the picture encoded outside it) with its longer side at
`RENDER_MAX_SIDE` px (1568 — past that providers downscale anyway), a JPEG
through Pillow. The result text names the page, the document's page count
and the picture's size; the picture itself rides
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
end (the count is named) and a file MuPDF can't open are refused in text;
a text file's page answers `"main.tex" is a text file: read it with
read_page`.
Its chip is 👁 "Looked at p. N of …", carrying `page_id` + `pdf_page` and
`picture`, the `GET /api/ai/page-image/{page_id}?page=N` URL the expanded
chip shows; the result names the same URL for the model to embed in its
reply as a markdown image when the user should see the page ("Pictures" in
[ai.md](ai.md#pictures)). The page is the file as stored: the pictures,
text boxes and handwriting placed on it are not drawn. The route's `ink=1`
draws the page as the user sees it, its pictures and text boxes with the
handwriting over them (`ink_view.page_with_handwriting`). A region
attached with its handwriting and `clip_region`'s `ink` use the same
drawing (`ai_pictures.render_region`).

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
  export's one-page PDF, `notebook.notebook_pdf` (`ink_view._sheet_pdf`):
  the paper painted, its pictures drawn and its text boxes typeset, and the
  strokes over them as vectors.
- **A PDF page** is that page on its own (`pdf_export.page_with_ink`). Its
  pictures and text boxes are drawn into its content as the annotated
  export picks them (`ink_view._placed`: `routers/export.py`
  `_collect_pictures`, `_collect_text_boxes`; `notebook.placed_ops`), and
  the groups are stamped over them. A box or a picture imported from the
  file and still embedded in it is drawn as Gamma has it, its original
  taken out of the copy first, so it shows once. Every stroke is drawn as
  vectors into the copied page's content through `ink.pdf_path_ops`, as
  the notes-as-PDF export draws it. So the model sees the looks as drawn
  (baked widths and tapers, dashes, the pencil's three shades in the
  screen's grain, the calligraphy nib; a dashed or dotted pencil as one
  dashed line at its mean shade), every group's highlighters under every
  group's pens.
  A highlighter is
  multiplied onto the page through a graphics state, so the text under
  it stays as dark as it was. `/Ink` annotations could not do this: a
  viewer draws them over the page's content.
  `backend/tests/test_ai_tools_ink.py` renders text under a highlighter
  and asserts every glyph pixel stays dark. It also checks the pencil's
  grain where the screen draws it, on turned and cropped pages too, and
  the nib's slanted ends. Ink imported
  from the PDF and still embedded in it (`pdf_export.still_embedded`) is
  not added twice. When the PDF
  is missing or pypdf cannot copy the page (a file that needs a user
  password to open), the strokes are drawn on blank paper of the page's
  size, and the result says so.

`pdf_text.render_page` rasterizes either, whole at `RENDER_MAX_SIDE` or
cropped under its 4× zoom cap, and `pdf_text.image_part` encodes the picture
as every tool picture is. It rides on the chip's `images` like
`view_pdf_page`'s and is never saved. The result names the block, the
page and the stroke count, quotes the caption, and asks the model to mark a
word it cannot read `[illegible]` rather than guess. Its chip is ✎
"Looked at handwriting in …" (kind `ink`, with `block_id` and `picture`,
the `GET /api/ai/ink-image/{block_id}` URL it expands to and the result
names for the reply), and the open page rings that block. It shares the View permission with `view_pdf_page`.

With `edit_block` armed, the agent prompt also says how to transcribe:
look with view_ink, then write the text into the group's caption, as an
append when it has one. The notes' **Transcribe with AI** (a handwriting
block's ⋮⋮ menu) sends exactly that request with the block attached as a
chip, and an attached handwriting block's picture rides with the message
(see "Pointing the chat at notes" in [ai.md](ai.md)). So a chat without
tools still answers with the transcription. The selection menu's
**Convert** → **Transcribe in the chat** does the same for a selection of
whole groups. For part of a group it sends the selected strokes instead,
as `ink_selections` ("Pointing the chat at notes" in [ai.md](ai.md)).
Each is drawn alone where it lies through `ink_view.picture(…, ink=file)`:
the same page with the inline file in place of the block's stored one, so
strokes not saved yet are seen too. The prompt's handwriting paragraph
says a selection is read from its picture, not with view_ink, whose
picture is the whole block as it was saved. The answer comes in the chat,
and with `edit_block` armed the agent appends it to that block's caption,
as for a whole group.

### view_image (both scopes)

The model's eyes on the pictures in the notes. A note's picture is a
markdown image in its text (`![alt](/api/uploads/<hash>.png)`, sized
`![alt|300](…)` by the editor), which `read_block` shows as that line and
nothing more. `block_id` names a note block; its pictures (`note_pictures`,
in order), up to `MAX_VIEW_IMAGES` (6) or the one `index` names (1-based),
are read from the workspace's store, sized like every picture
(`ai_pictures.read_stored`) and ride on the chip's `images` like
`view_pdf_page`'s page. A model takes no SVG, so an SVG goes as a PNG of
it ([pictures.md](pictures.md#svg-pictures)). The result lists them with
their alt text and size and names each URL, which the model may embed in
its reply. A block without a picture, an index past the end and a file
gone from the store are refused in text. A stored file that does not read
as a picture, such as an SVG that cannot be drawn, is said to be so.

A picture block's own picture comes first, its caption the alt text. It is
the whole file, so the result says how the page shows it otherwise
(`_placed_as`): "This is the whole file; the page shows it cropped to
10%–70% across and 25%–75% down, turned 30° clockwise, flipped left to
right, at 50% opacity." The agent prompt says a picture placed on a page
appears in read_block as such a line, labelled "(picture on …)", its text
the caption.

The tool's chip is "Looked at N pictures in a note of …" (kind
`image`, with `block_id` and `picture`, the first one's URL). Shares the
View permission and `PICTURE_TOOLS` with the other two.

### cite (both scopes)

The citation records Gamma keeps with pages, so a bibliography is built
from the records and not from the model's memory. `page_ids` takes up to
50 pages; in a page chat the default is the open page. For each page the
result gives:

- the record from `properties.meta` (its item type as a `Type:` line, in
  Zotero's names, so a reference style knows a chapter from an article;
  authors, year, venue, volume, pages, publisher, ISBN, DOI, arXiv id;
  [paper_metadata.md](paper_metadata.md)), and the paper's own title when it
  differs from the page's;
- the stored BibTeX, else one built from the record the way a hand edit
  builds it (`bibtex.build_entry`), under the citation key pinned on the page
  when there is one — so what the model cites matches the `.bib` export
  ([import_export.md](import_export.md#bibtex-bibliography));
- the slide citation when one was made (`ppt_cite`). None is generated,
  since that is an AI call.

A record flagged `unverified` says so, with why from its `meta.match`
(no author found by its title, a title search phrase, an identifier the
PDF only prints, an AI reading), and a confirmed one says what tied it to
the PDF ("Matched by: an AI check against the PDF"; [paper_metadata.md](paper_metadata.md)).
A page without metadata says so
too, and the model is told not to invent one. Nothing is looked up or
stored. Pages outside the scope are refused one by one, and a call where
every page is refused is an error. Its chip is ❝ "Cited N pages" (kind
`cite`), under the Read permission.

### The web tools (both scopes)

```mermaid
flowchart LR
  q["a question the library does not answer"] --> lib["search_library, read_page"]
  lib --> held{"already a page?"}
  held -- yes --> libans(["answer from the library"])
  held -- no --> kind{"a paper?"}
  kind -- yes --> find["search_papers: Crossref + arXiv + OpenAlex"]
  find --> more["search_web, related_papers"]
  more --> probe
  find --> probe["fetch_paper mode=probe"]
  probe --> right{"the right paper?"}
  right -- no --> find
  right -- yes --> long{"answer could be anywhere in it?"}
  long -- yes --> helper["read_paper: a helper reads it"]
  long -- no --> read["fetch_paper, window by window"]
  helper --> ans(["answer citing PDF pages"])
  read --> ans --> keep["save_paper"]
  kind -- "no: a fact, docs, news, software" --> web["search_web: recency, site"]
  web --> page["fetch_url: sections, find, offset"]
  page --> cited(["answer citing the pages as links"])
```

The agent's reach outside the library, read-only: `search_papers`,
`related_papers` and `search_web` under **Search online** (`web_search`),
`fetch_paper`, `fetch_url` and `read_paper` under **Fetch documents**
(`web_read`). The executors are in `ai_tools.py`; the code under them
depends one way, `html_markdown ← web_page ← web_fetch ← ai_web`, with the
search engines beside them:

- `gamma/ai_web.py`, the scholarly reach: the registries (with
  `gamma/openalex.py`), the citation graph, `fetch_document` (the paper
  resolver first, then the page) and the papers' cache;
  `gamma/paper_links.py` ranks a page's PDF links.
- `gamma/web_fetch.py`, reading a URL: the one guarded GET both readers
  use (`get`), the size caps, the walls (`WALLS`, `access_wall`, `refusal`,
  `entry`), `uri`, the caches' class (`DocCache`), `fetch_page`
  (`fetch_url`'s reader) and the windows (`window`, `window_pages`,
  `find`). `BROWSER_HEADERS` and `meta_refresh` live here, and
  `routers/pdf.py` imports them, so no module of the web layer imports a
  router.
- `gamma/web_page.py`, a response body as the text the model reads, and
  `gamma/html_markdown.py`, the HTML tree and the Markdown writer it shares
  with the web clipper and the Zotero and Notion imports
  ([The page reader](#the-page-reader)).
- `gamma/search_services.py`, the engines behind `search_web`, their
  adapters and the account's settings.

Three uses shape them. A work the user's pages cite but do not hold: *"read
reference 12 of this paper and tell me what it measures"*. The agent finds the
reference entry with `search_library` / `read_page`, identifies the work with
`search_papers` and reads it with `fetch_paper`. Discovery from a research
question or an experimental setup: *"find papers on density-dependent loss in
Raman sideband cooling of 85Rb in a 1D lattice"*. With the search tools armed,
`agent_system` gives the model a researcher's recipe: two to four short concept
queries (the phenomenon, the method, the system, not every parameter at once),
each run through `search_papers` (and `search_web` when armed), relevance judged
from the abstracts, the strongest match's citations followed with
`related_papers`, and only the few decisive papers fetched. The answer says
which papers match the user's case directly and which are analogies (another
species, geometry or regime), and never presents one paper's number as a
general limit. And a fact that is current, versioned or specific: *"what's
new in Python 3.14?"*, a price, a person, this week's news.
The agent looks it up with `search_web` and reads the best pages with
`fetch_url` instead of answering from memory, and cites the pages it used
as links.

The prompt's web paragraph (`agent_system`) names the armed web tools and
says when to use them: for anything outside the library, current, versioned
or specific facts (releases, docs, prices, people, news) looked up instead
of answered from memory, and works the user's pages cite but do not hold,
with the library preferred for what it holds. One sentence then sorts the
armed tools, built from their names only, so it never names a tool that is
off: "Papers: search_papers and fetch_paper. Anything else: search_web and
fetch_url; cite the pages you used as links." Which tool is for what is
otherwise each spec's, said once: `search_web`'s says that for papers
`search_papers` comes first, `fetch_paper`'s sends any page that is not a
paper's to `fetch_url`, and `fetch_url`'s sends a paper to `fetch_paper`.
With `search_papers` armed and no web engine, the prompt adds that general
web search is not set up and where to turn it on.

A chat armed with `search_web` or `fetch_url` (`ai_tools.DATED_TOOLS`) is
told today's date, since a result dated "3 days ago" or "the latest
release" means nothing without it: `_chat_prompt` (`routers/ai.py`) passes
`Today is YYYY-MM-DD (UTC).` (`page_now()[:10]`) as `build_messages`'
`date_line`, which opens the oldest user turn. It is never in
`agent_system`, which no message may change, and never on the newest turn:
the next message replays that turn without the line, so it would pay for
the turn and its tool rounds again. On the oldest turn it reads the same
all day, so the cached head changes at most once a day
([ai.md](ai.md#prompt-caching)). The context export (`POST
/api/ai/chat/context`) shows it as the model reads it.

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

`search_web(query, limit, recency, site)` searches the web: current or
versioned facts, documentation, software and datasets, news, products,
people and organisations, and what the registries miss (lab publication
lists, theses, another copy of a blocked paper); its spec says that for
papers `search_papers` comes first. `recency` is `day`, `week`, `month` or
`year` (`search_services.RECENCY`) and keeps pages of that past stretch.
`site` is one domain: `search_services.site_of` takes a URL, `site:x`, `*.x`
or a host with a path to the bare host in its IDNA form, and several sites,
an IP address or a name without a dot are an `error:` naming the shape
wanted, as an unknown `recency` is. It is armed only when
`search_services.web_engine` finds an engine for this chat (the TOOLS
entry's `needs: "web_engine"`, met through `ai_tools.available(scope)`; the
chat scope carries `web_engine`).

Each result is a title link, the page's date when the engine reports one,
the `doi:` / `arXiv:` string the URL carries when it carries one (a DOI or
arXiv link, a publisher path with the DOI in it) with the `search_papers`
call that gives its registry record, and the snippet. The head names the
engine, the filters asked for and the day it ran: `Web results for "…" (8,
via Brave Search, past week, docs.python.org, searched 2026-11-17 — leads: a
snippet is what a page says about itself; read a page with fetch_url, or a
paper with fetch_paper, before relying on it)`. No results says the same,
adding that widening the filters may help when there were any. Titles are
capped at 300 characters and snippets at 400, HTML is stripped, non-http(s)
and credentialed URLs are dropped, `utm_*` parameters are taken off the URL
(OpenAI's citations add `utm_source=openai`), and duplicates are removed by
that URL. One message may run `MAX_WEB_SEARCHES` (10) searches,
since each is a paid query or a call on the chat's connection; the tool
rounds cap everything else, unless the user chose Unlimited rounds
([ai.md](ai.md) "The tool loop").

A result's date comes only from a field the engine fills itself, never from
text a model wrote, so a page the AI engine's model dates from memory carries
none. It is `YYYY-MM-DD` when it parses (ISO 8601, RFC 2822, "April 30,
2025"); other text (Brave's "3 days ago") is shown as the engine wrote it, cut
at 40 characters, and an impossible ISO date is dropped.

The engines (`gamma/search_services.py`, `ENGINES`: a label, the fields the
settings ask for and an adapter each). Every adapter takes the same request
(the query, the limit, `recency`, `site`) and answers `[{title, url, snippet,
date?}]`:

- **Brave Search** (`brave`): the Brave Search API with the account's key in
  the `X-Subscription-Token` header. `recency` is its `freshness` (`pd`,
  `pw`, `pm`, `py`), `site` a `site:` operator appended to the query; the
  date is the result's `page_age`, else its `age`.
- **Tavily** (`tavily`): `POST https://api.tavily.com/search` with
  `Authorization: Bearer <key>` and `{query, max_results, search_depth:
  "basic", include_published_date: true}`, plus `time_range` for `recency`
  and `include_domains: [site]`. Tavily returns a result's
  `published_date` only when asked (by default only on its news topic).
  HTTP 432 and 433, its plan and pay-as-you-go limits, read as 429 does: the
  rate limit or quota is reached.
- **SearXNG** (`searxng`): a SearXNG instance's `/search?format=json`, with
  `time_range` for `recency` and `site:` in the query; the date is
  `publishedDate`. The account's own URL goes through the SSRF guard, so it
  must be a public host. The server's `GAMMA_SEARXNG_URL` serves accounts
  without their own and may be on the private network, since the admin
  chose it.
- **Your AI connection** (`ai`): one short call on the chat's own connection
  with its provider's hosted search tool, the way Codex and Claude search.
  `hosted_tool` asks the wire a call with tools goes over
  (`Protocol.tool_wire(conf).hosted_web_search(conf, site)`, [ai.md](ai.md)
  "Protocol adapters"): the Responses wires (OpenAI's platform, the ChatGPT
  sign-in) send `{"type": "web_search"}` with `filters: {allowed_domains:
  [site]}`, and Anthropic's own API sends `web_search_20250305` with
  `max_uses` 5 and `allowed_domains: [site]`. A Chat Completions gateway has
  none. A tool spec with a `hosted` entry goes out as that entry on every
  wire that has one. The prompt asks for up to `limit` pages, primary and
  authoritative sources first (the official site or documentation, the
  original publication, announcement or data, the people or organisation
  concerned) over aggregators, reposts and listicles, one `title | url |
  summary` line each; `site` also goes into the query as `site:`, and a
  `recency` adds "Only pages published or updated in the past week: today is
  YYYY-MM-DD (UTC)." The call runs at low effort when the chat's connection
  takes an effort.

  The stream says what the search did. It yields `("web_searched", {"error"})`
  as each hosted search runs, with the provider's error code when one failed,
  and `("web_sources", …)` for the pages the provider reports (the Responses
  `web_search_call` sources, which OpenAI's platform sends when asked with
  `include`, its URL citations, and Anthropic's `web_search_tool_result` and
  citations, with `page_age` as the date). A reply that never searched is a
  `SearchError` ("the AI connection answered without searching the web"), and
  so is one whose search failed and reported no page: its lines could only be
  URLs from memory. When the provider lists what its search found, only
  lines for those pages are kept, and listed pages the model did not write
  follow; a list of none keeps none, so a search that found nothing gives no
  results rather than the model's memory. Only a wire that lists nothing
  (the ChatGPT backend, asked for no sources and citing none) has its lines
  taken as written. A reply cut short keeps the sources it reported. `ai_client.CallRefused` (the account's call slots all in use, an
  allowance used up) is a `SearchError` with its reason. The call counts as
  chat usage (`ai_usage.recorder`), and its integer token counts also go into the
  `meter` the tool passes, which `_run_search_web` puts on the action as
  `spent`, failed or not, so the reply's footer counts the search (the loop
  yields it as a `usage` event, as it does `read_paper`'s helper). The meter
  never carries the served `speed`, which the reply would take for its own
  model's.

`engine` picks one: **Automatic** (the default) takes the first engine that
is ready in `ENGINES`' order, Brave Search, Tavily, SearXNG, then the AI
connection; a named engine is used only when it is available, so a missing
key never switches services silently; **Off** turns general web search off.

#### Online search services

Settings → AI → Tool usage → **Online search** (`settings/OnlineSearch.jsx`)
holds **Search the web with** (the engine: Automatic, Your AI connection,
Brave Search, Tavily, SearXNG or Off) and one row per service
(`search_services.SERVICES`): Brave Search (API key), Tavily (API key),
SearXNG (address) and OpenAlex (optional API key). The AI connection is a
choice, never a row: it needs nothing set up, so `SERVICES` is the engines
that have fields, and OpenAlex. Each row has Set up, or Test, Edit and
Remove. Test runs one small search with the stored settings: a general query
(`TEST_QUERY`) for a web engine, a registry search for OpenAlex. A row that
is set up shows its first plain field (SearXNG's address), else its key's
hint; OpenAlex's says it works without a key (`optional` in
`SERVICE_FORMS`).

The settings are the account's, stored in `users.db` `user_prefs` under the
reserved account-wide `search-services` key (`{"engine", "brave": {api_key},
"tavily": {api_key}, "searxng": {url}, "openalex": {api_key}}`, each with
`updated_at`). An older server keeps only the services it knows and reads an
engine it does not know as Automatic. Like `ai-settings` and
`translate-engines`, the generic `/api/prefs` endpoints refuse the key. `GET
/api/ai/search-services` masks secrets to their last four characters; a
secret left empty on save keeps the stored one. Guests cannot store keys. The
routes are in [api.md](api.md).

#### fetch_url

`fetch_url(url, find, offset, chars)` reads any page or file at an http(s)
URL through `web_fetch.fetch_page`: one GET, with the browser's headers and an
Accept that asks for a page first, then text, JSON and a PDF. What comes back
depends on the body:

- **A web page** (`kind: "html"`): its main content as Markdown with its
  links ([The page reader](#the-page-reader)), and the head `Read web page
  "<title>" (<url>, N chars, <site>, by <author>, published …, updated …,
  fetched …)`, each fact when the page has it (`_read_facts`: "updated" only
  when it differs from "published", "fetched" the day it was read, in UTC).
- **Any other text** (`kind: "text"`: plain, Markdown, JSON, XML and feeds,
  CSV, `text/x-*`, JavaScript): the decoded body as it is, `Read text file
  (<url>, N chars, fetched …)`. Nothing is pretty-printed or parsed.
- **A PDF** (by its type or a body that starts `%PDF-`, so one served as
  `application/octet-stream` too): its text page by page with `[p. N]`
  lines, `Read PDF <url> (N pages, N chars of text, fetched …)`, and the
  action's `pdf_pages`.
- **Anything else** (an image, audio, video, font or model type, or a NUL
  byte in the first kilobyte of a body without a UTF-16 byte-order mark, as
  an archive or an office file has) is "not a web page or text"; an empty
  text body "is empty".

A body is read up to `web_fetch.PAGE_MAX_BYTES` (5 MB) and the rest cut
(`truncated`; the head says only its first 5 MB were read); a PDF is refused
past `PDF_MAX_BYTES` (40 MB), the message naming its size when the server
gave one.

The window is `read_page`'s. `chars` and `offset` are passed to
`_window_args` as its `pdf_chars` and `pdf_offset`, so the default and the
cap are the Read window preference's, and the excerpt ends with
`fetch_url(url="…", offset=N)` while text remains. `find` starts the window
at a phrase (`web_fetch.find`): its first match at or after `offset`, in any
case and with any white space between its words, on the same joined text
`window` cuts (a PDF's `[p. N]` lines included), backed up to the start of
its line by at most 300 characters. The excerpt says where it starts and
where the phrase first occurs; a phrase not there says so. On a page longer
than the window, a read that asked for no place (no `find`, no `offset`), or
whose phrase is not there, lists the page's sections in its head, one line
`- ## Heading — offset N` each: the h1–h3 the page reader wrote, at their
offsets in the text, each cut at 80 characters and the list sampled to 2 000
(`web_page.OUTLINE_BUDGET`, through `documents.text._fit`). The model reads a
section by passing its offset.

Before any fetch, a PDF the user's browser already delivered for that
address (`fetch_handoff.delivered`, the account's `handoff_user`) is read,
and its chip says it came from the browser. A source that `ai_web.identifier`
reads as a paper (a bare DOI or arXiv id, a `doi.org` or `arxiv.org` link) is
answered `error: … names a paper — read it with fetch_paper(source="…")`. A
page whose address carries a DOI or an arXiv id (`_url_identifier`, a
publisher path with the DOI in it) is read, and the head adds that
`fetch_paper(source="doi:…")` reads the paper itself and, when **Search
online** is not off, that `search_papers` gives its registry record. The tool
binds `publisher_sessions.current_user` from the chat scope around the fetch,
as `fetch_paper` does, so **Use journal sign-ins** applies to its connected
hosts too.

A wall is a sentence for the model, never a card: the handoff can only bring
back a PDF, and a docs page, a product page or a news article has none. The
result is `error:` with what stopped the server and, behind a wall, that only
a person gets past it in their own browser: work from the search snippet or
another source, or ask the user to paste the text, and do not retry the
address. An address that looks like a paper's (a DOI or an arXiv id in it, or
a `.pdf` path; `_paper_like`) adds that `fetch_paper(source="…")` looks for its
PDF, open-access copies included, and can hand a blocked one to the user's
browser. The reply never waits.

`fetch_url` reads walls as any web page shows them (`access_wall` with
`general`). Cloudflare's `cf-mitigated: challenge` header and a host that
serves nothing but a bot check always count. A vendor's challenge markers, a
CAPTCHA widget, a password field and a sign-in address count only on a thin
HTML page (under 2 000 characters of all the text a browser shows, menus
and forms included — `web_page.Page.visible`, not the main content, so a
short article under a header's sign-in box reads; HTML by its Content-Type,
or a body that starts with `<` when there is none): a docs page under
`/login/help`, a long article with a newsletter form, or a script file that
mentions `_cf_chl_opt` reads. An HTML page with no readable text is the
`script` wall (it needs a browser). A `<meta http-equiv="refresh">` is
followed, at most two hops, only from an HTML page with under 500 readable
characters (an interstitial such as Elsevier's linkinghub); a readable page
that refreshes to a sign-in after five minutes is read as it is.

Read pages are kept in `web_fetch`'s page cache (`_pages`: 24 documents and
10 M characters, PDFs included), partitioned like the papers' cache by
`publisher_sessions.cache_scope()` and keyed by the address without its
fragment. A page unused for `PAGE_TTL` (15 minutes) is read anew, so a price
or a news index is read as it is now, while a page read in windows stays
the same. A document this call downloaded carries `fresh`; a continuation
(`offset` above 0) served by such a read says that the page was read anew and
may have changed since the windows before it. The page cache and the papers'
never answer for each other: `fetch_url` and `fetch_paper` of one URL are two
entries.

The address is read as the URI it spells (`web_fetch.uri`): the page reader
shows a link's address with its characters decoded (`光`, `例え.jp`), and
the model copies it so; `uri` gives the host its IDNA form and
percent-encodes the rest of the path, query and fragment as UTF-8, reserved
characters and escapes kept, so it names the same page. It runs before the
SSRF guard sees the host, so a loopback address written in ideographic or
enclosed digits and dots (`127。0。0。1`, `①②⑦.0.0.1`) is refused.

The result carries the untrusted-content line, as `fetch_paper`'s does. The
action is `{kind: "fetch", url, title, pdf, summary: "Read “…”"}`, with
`pdf_pages` for a PDF; `url` is one of `ai_agent.STEP_ARGS`, so the running
step reads "Reading example.org…", or "Reading 3 web pages…" for a round's
batch (`runningLabel` in `chat/agentSteps.js`), and an approval card "Read
{url}" (`chat/approvals.js`). The reply's **Save to library** lists only
`fetch_paper`'s reads (`chat/chatPapers.js`), so a page read with `fetch_url`
is never offered as a paper. MCP does not offer it (`web_read`, as for every
web tool).

#### The page reader

`web_page.read(raw, url, ctype)` turns a response body into what the model
reads, a `Page(title, text, kind, meta, outline)`; both readers use it
through `web_fetch.get`. `html_markdown` holds the parts it shares with the
web clipper and the Zotero and Notion imports.

- **Decoding** (`decode`): the byte-order mark, else the charset the
  Content-Type names, else the one a `<meta charset>`, `http-equiv` or
  `<?xml encoding>` in the first 4 KB declares, read as a browser reads the
  name (gb2312 and gbk as gb18030, shift_jis as cp932, latin-1 and us-ascii
  as cp1252, euc-kr as cp949, big5 as big5hkscs; a `<meta>` declaring UTF-16
  as UTF-8), else UTF-8; what cannot be decoded (a body cut in the middle of
  a character) is replaced. A body without a Content-Type is HTML when it
  starts as one (the WHATWG sniffing rules).
- **The head** is read with regexes over the text before `<body>` (its first
  200 KB): the `<title>` as written (site suffix and all), else `og:title`,
  else the h1's text; `meta` with the keys the page has: `site`
  (`og:site_name`), `description`, `author` (never a URL), `published` and
  `modified` (`YYYY-MM-DD` when they read as a date, an ISO date or time,
  `2017/06/12` or a Unix time; the head's JSON-LD `datePublished` and
  `dateModified` fill in when no meta tag does, as on the BBC), `canonical`
  and `lang`, each unescaped and cut at 500 characters; and `<base href>`,
  which relative links resolve against.
- **The tree** comes from `html_markdown.Parser`, the tree builder the
  Zotero and Notion imports use, in a subclass that builds what pages lean
  on a browser for: a `<head>` left open ends at the first element or text a
  head cannot hold; `dt`, `dd`, `tr`, `td`, `th`, `thead`, `tbody`, `tfoot`
  and `option` close the open one before them; a `<p>` ends at any block;
  `</body>` and `</html>` end nothing (a lab's publication list had a stray
  `</html>` inside a 2015 abstract, and everything before 2015 came after
  it). Only scripts and styles are left out while parsing, and a MathJax
  `math/tex` script becomes its formula. The tree is at most
  `html_markdown.DEPTH` (120) deep, which the writer and every pass recurse
  within: past it, elements lie flat in the last one kept, and the parser
  still tracks them (a `Counter` of their tags, so an end tag for none of
  them costs nothing), so text inside one that hides stays out.
- **What a browser never shows** goes: the head, noscript, templates, SVG,
  canvases, frames, objects and embeds, audio and video, dialogs, form
  controls, `hidden` (except `until-found`) and `aria-hidden="true"`
  elements, MathJax's preview, inline `display:none` and
  `visibility:hidden`, and code line-number gutters that hold only numbers. React's streamed segments
  (`<div hidden id="S:0">`, which a script moves into place; X's posts are
  there) stay.
- **Chrome** goes: `nav` and `menu`; `header` and `footer` outside a `main`
  or an `article`; an `aside` outside the article (in a `main` without one,
  the main is the article; the BBC's "Related" and "More from the BBC" are
  asides beside the article in its `main`); the navigation, banner,
  contentinfo, complementary, search, dialog, alertdialog, menu, menubar,
  toolbar and tablist roles; class or id tokens that name chrome (navbox,
  sidebar, breadcrumbs, cookie, consent, share, related, toc, catlinks,
  sr-only, GitHub's `VisuallyHidden` …), on an inline element only the
  tokens of text never read (a Sphinx heading's text is an `<a
  class="toc-backref">`). A wrapper
  that holds the main, an article, the h1 or over 30 % of the page's text is
  no chrome: arXiv wraps its whole page in `div.flex-wrap-footer`.
- **The content** is the largest `main` (or `role="main"`) with 250+
  characters, else the page's one `article`, else what a descent from
  `<body>` reaches through each child that holds 85 % of its parent's text
  (its text outside links, or all of it on a page with under 500 characters
  outside its links, a page of headlines). The page's h1 goes first when it
  lies outside. Outside a main or an article, link farms go: a block of 5+
  links that are over 60 % of its text, with under four words of its own per
  link and no paragraph, unless the farms are over 80 % of the content (a
  news portal's headline lists are what it has).
- **The writer**, `html_markdown.Markdown`, writes blocks: headings, nested
  lists, quotes, fenced code with its language (`data-lang`, a
  `language-x`, `lang-x`, `highlight-x` or `sourceCode x` class on the
  `<pre>`, its `<code>` or a wrapper), `$…$` / `$$…$$` math (a `<math>`'s
  TeX annotation or `alttext`, else `mathml.to_latex`), figures with their
  captions, rules, and tables. A table's rows are found in one walk that
  skips the tables inside it, and its cells are laid on the table's grid, a
  cell's text in every grid cell its `rowspan` and `colspan` cover (a header
  group, a category column). What a tree's tables write beyond their cells'
  own text — span repetition and the padding of short rows to the widest —
  has a budget of 2 M characters (`_TABLE_CHARS`), past which a table's rows
  go as they come, each with its own cells: a few bytes of markup cannot
  write gigabytes. A table whose cells
  hold blocks (paragraphs, lists, tables, code, quotes, figures, rules,
  headings, a video or frame, a display formula) is a layout table, written as its cells' content in order; a
  wrapper in a cell, such as Wikipedia's centering `<div>`, does not make
  one. A subclass changes the writing through hooks (`text`,
  `line`, `href`, `image`, `heading`, `code_lang`). The clipper's
  (`web_clip.ClipMarkdown`) writes a note a reader keeps: text escaped where
  Markdown would read markup, `mailto:` links kept, pictures as `![alt](url)`
  pointing at the copies the browser uploaded. The reader's
  (`web_page.PageMarkdown`) writes what the model reads: text as the page has
  it, a picture as `[image: alt]` (nothing without alt text) and a link that
  holds only one as `[image: alt](url)`, `mailto:` and `javascript:` links as
  their text, and a link's address absolute and readable (`%E5%85%89` shows
  as 光; every ASCII escape such as `%2F`, `%3F` or `%23`, the host and any
  character that does not print stay as they are, so the address names the
  same page). It drops in-page footnote markers (`[12]`, `[a]`, `[citation
  needed]`), heading permalink marks (¶, §, 🔗, a leading `# ` or a
  trailing ` #`) and a short label above a code block that only repeats its
  language (MDN's "js"), takes a code block's language from
  SyntaxHighlighter's `brush:` class too, and links a teaser card (a link
  around blocks) by its heading, else its first line.
- **The outline** is the h1–h3 the writer wrote, found by their `Heading`
  type with their offsets in the text, so a `#` line in a code block is
  never one.

Measured on 18 saved pages (Wikipedia in English and Chinese, docs sites,
GitHub, news articles and front pages, blogs, lab pages, arXiv, LinkedIn, X),
the menus, footers and language lists that were 5–65 % of the old regex
stripper's text are 0–4 % of the text at the top on every page but GitHub's
(9 %), and 0–3 % at the bottom on all but three (the Guardian's article 11 %,
LinkedIn 19 %, arXiv's abstract page 57 %), with the links, headings, lists,
table rows and code indentation kept; a Shift_JIS novel declared in its
`<meta>` reads with no replacement character, and a page takes 10–300 ms.
The comparison and what is left are in
[web-search.md](../research/web-search.md).

#### fetch_paper

`fetch_paper` takes a `source` (a DOI, an arXiv id or the http(s) URL of a
paper: an arXiv, DOI, publisher or repository page, or its PDF; its spec sends
any other page to `fetch_url`) and reads the document in windows with
`read_page`'s knobs: `pdf_chars` (default and cap from the Read window
preference, shared through `_window_args`), `pdf_page`, `pdf_offset`, and
an excerpt that names the next offset while text remains.
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
never enter the model's context. It is downloaded through the SSRF guard
(`web_fetch.read_bounded`) under a size cap (`web_fetch.PDF_MAX_BYTES`, 40 MB,
refused by the Content-Length before a byte is read when it says more) and
extracted page by page
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

When no PDF is reachable (a paywall, a plain web page) and the source is a
page, the page is read as `fetch_url` reads it: `web_fetch.get`, the one page
GET both readers use, and `web_page.read`, so it comes as Markdown with its
title, metadata and outline, and a page over 5 MB is cut and flagged rather
than refused. A body that is a PDF after all (served as
`application/octet-stream`, which the resolver did not take for one) is read
page by page. The head reads `Fetched web page "…" (<url>, N chars, <site>,
published …, fetched …) — no PDF was reachable (<note>)`, the facts the page
has of those (`_read_facts`). The note is written for the model from the
resolver's trace (`ai_web._no_pdf`): the host refused this server, the PDF
link the page advertises gave no PDF (or none of its N links did), no
(published) open-access copy of the DOI was found, else that the address serves
`text/html`, not a PDF. The resolver's own message, which tells a person
clipping what to do in their browser, stays for the clip UI. A URL source is
read as the URI it spells (`web_fetch.uri`, [fetch_url](#fetch_url)), so an
address copied from a `fetch_url` result reads too. The page's PDF links
follow for the model to try (`paper_links.pdf_links` on the decoded HTML; a
text body has none: a `.pdf` path, a `/pdf` or download route, "PDF",
"full text" or "Download" in the link text; links the resolver already tried
are left out). A lab's publication list holds hundreds of them, one paper per
item with its title as the link text, so each link keeps its text and the text
of the item it sits in (a bare "[PDF]" link borrows the item before it), and
`paper_links.rank` orders them against `title` before the eight shown: the
share of the title's words in the link's text, item and path, plus one for the
whole title. On a real lab list of 238 PDF links, the wanted paper moved from
position 227 to first. Without a title the page order stays and the head
suggests passing one. A fetched paper lives in an in-memory LRU, `ai_web._papers`
(a `web_fetch.DocCache` of 24 documents and 30 M characters, kept until crowded
out), partitioned by account and usable-cookie fingerprint
(`publisher_sessions.cache_scope()`) and keyed by resolved URL with the source
strings as aliases, so the windows of one paper cost one download without
sharing authenticated text between accounts. A page read with `fetch_url` lives
in a cache of its own: neither answers for the other, and pages cannot crowd
out papers; `ai_web.clear_cache()` clears both. A session connect,
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
when answering from it, with its version when the result names one. The spec
asks for the paper's title whenever the model knows it (a document it is not
found in is unverified) and for probes of several candidates in one go. The
action chips are 🌐 for the three search tools (kind `websearch`) and ⬇ for
fetches, `fetch_url`'s too (kind `fetch`, carrying the resolved `url`); the
step pill counts searches as "searched online".

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

What stopped a fetch is named (`web_fetch.WALLS`), so a person can take
over. These are `fetch_paper`'s rules (`access_wall` without `general`);
`fetch_url` counts the body's markers only on a thin page
([fetch_url](#fetch_url)).

- `captcha`: a bot check or CAPTCHA page, served as 200, 403 or 503. It is
  recognized by Cloudflare's `cf-mitigated: challenge` header or challenge
  script, a host that serves nothing but a bot check (Radware's
  `validate.perfdrive.com`), other vendors' markers (Fastly's `/_fs-ch-`
  "Client Challenge", DataDome, PerimeterX, Imperva, Akamai's "Access
  Denied", Google's "unusual traffic") or the page title ("Just a moment",
  "Checking your browser" …), in an HTTP error's body too (`refusal`), so a
  403 that is a bot check reads as one. A
  reCAPTCHA / hCaptcha / Turnstile widget counts only on a page with under
  2 000 characters of visible text (menus included), since an article page
  may carry a newsletter form.
- `login`: the request ended on a sign-in page (a login or SSO path or host,
  or a password field on a thin page).
- `denied`: HTTP 401, 403 or 418. `rate`: HTTP 429.
- `script`: the page has no readable text without its scripts (a browser
  shows it).
- `abstract`: the page fallback read an article page whose PDF was out of
  reach (the page advertised one, or its DOI's publisher refused).

The page fallback follows `<meta http-equiv="refresh">` redirects (up to two
hops, `web_fetch.meta_refresh`) as the resolver does, from a page with under
500 characters of visible text, so a DOI landing that only redirects (Elsevier's
linkinghub) reaches the publisher and its wall, while a readable page with a
delayed refresh to a sign-in is read.

A wall on the PDF still falls back to the page's text, so the model keeps
what was readable. In an account's own chat (`_chat_scope`'s `handoff_user`:
not a guest, not a share link), a wall opens a request in
`gamma/fetch_handoff.py`. Requests are per account and in memory, live 6
hours, at most 20 are kept, and there is one per work however its source is
spelled (reused while it waits). The page to open is the publisher's, never
`doi.org`, a sign-in page or a bot check's own host (`web_fetch.entry`). For a
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
`web_fetch.PDF_MAX_BYTES`, a `%PDF` with a text layer; 409 once the request is
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
as many windows as the question needs, and hands back a cited answer, as
long as the question needs and about 1 000 words at most
(`READ_PAPER_PROMPT`). The windows stay in the helper's own conversation,
so the chat carries an answer of at most `_HELPER_ANSWER_MAX` (8 000)
characters instead of a forty-page paper it would have to re-send every
round for the rest of the reply. Four papers asked the same question cost
about what one full read would.

The helper runs the same `AgentLoop` with `fetch_paper` as its only tool
(its scope has no web engine, so a blocked paper does not tell it to run a
`search_web` it does not have), no gate and no settle, at most `HELPER_ROUNDS` (12) rounds, on the chat's
own connection with its own prompt (`READ_PAPER_PROMPT`: one document, one
question, a page number beside every number, say plainly when the document
does not answer it). Nothing it does can change the library, its pictures
never leave it, and its cache key is the conversation's with `:helper`
appended so the two prompts do not fight over one prefix cache.

The helper reads in the widest window the user's read cap allows. The tool
passes it a scope with `read_default` set to that cap, which `_window_args`
and the tool's description use in place of `READ_CHARS_DEFAULT` (6 000). A
150 000-character review is then about eight reads at the default cap of
20 000, where the short window needs twenty-five. The result the helper
gets before its last round ends with `LAST_ROUND`, which tells it to
answer. A helper that still asks for another read has no answer: the call
fails with "the helper did not get to an answer", and the loop's own line
about the round limit is not passed on. A helper whose conversation
outgrew the model's window has no answer either. Once the chat is
stopped, a helper opens no further turn and answers nothing
([ai.md](ai.md) "Stop").

The parent's chip has its own kind, `helper`, and its summary names the
helper ("Helper read “…”"). It carries the document the helper actually
read (`url`, `title`, `pdf`, `version`), how many calls it made (`steps`),
those calls as `children` (up to 12, without their output — a child's text
is the helper's, not the chat's), and
`spent`, the helper's token counts, which the loop yields as a `usage`
event so the reply's footer counts the whole answer. Each `fetch_paper`
child names the PDF pages its window read (`pdf_pages`, from
`web_fetch.window_pages`), which the chat shows beside its summary. That is
what tells reads of one document apart. A wall the helper met
cannot show a card from in there, so its request rides up on the parent
action as the `handoff`, and the chat's own card and wait take over
([above](#walls-and-the-browser-handoff)).

While it works, the helper reports its status (`Helper.on_status`), its
whole state each time it changes: `{id, label, state, steps, step?,
blocked?}`.

- `label` is the paper's title, or its source when the call gave none.
- `state` is `reading`, `answering`, `done` or `failed`.
- `steps` counts the calls it finished, and `step` is the call running
  now (the loop's own step payload).
- `blocked` is the host that stopped a fetch.

The chat's loop cannot carry these: it is inside the `read_paper` call
until the helper answers. A streamed chat puts them on its stream itself,
as `{"helper"}` lines. `ai_chat` makes the relay's queue and hands it to
`keepalive_lines` as `inbox`, and the helper's thread adds to it without
waiting. A line is dropped when the client reads too slowly; each line
carries the whole state, so the next one makes up for it. The chat keeps
the newest state per `id` and shows a row per helper under the steps pill
until the call's chip lands ([ai.md](ai.md#the-tool-loop)).

The tool is offered only when **Read long papers with a helper**
(`gamma-ai-delegate-reads`, account-wide, on by default) is set — the
request's `delegate_reads`, which the scope carries as `delegates` and
`available()` turns into the entry's `needs: "helper"`. The armed prompt
says when to reach for it: a request to read, summarise, explain or
compare papers outside the library (one call per paper, in one turn — a
probe shows only the front matter and is for checking a candidate), an
answer that could be anywhere in a long document, or the same question
over several papers. And when not: the document's own wording, a table, a
quotation, or an abstract that already settles a narrow question.

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
is no card and the chip is a no-op. A paper that is a sub-page is filed
where its top is (`blocks_store.filed_folders`): when that is where it was
asked to go, or no folder was asked for, the answer is that it is already
in the library inside page `<parent>` and nothing changed; any other
folder is refused with `move_page`'s wording (move the outermost page),
and the clip is never called.
In a page chat the new page is outside the chat's reach (its page and the
pages inside it), and the result says to read the paper with `fetch_paper`.

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

`rename_page` changes pages' titles (`renames`). `move_page` files pages
(`page_ids`) into a (sub)folder named by path or id (`_resolve_folder`). A
path no folder has is made on the `folders` tree in the same transaction
as the pages' batches. The move is one `set` of each page's `folders`: ids
outside the chat's folder that still exist are kept (`existing_in`), the
in-scope ones are replaced by the target, and a folder above the target
gives way (`blocks_store.refiled`'s rule). The user can revert either from
its row (below). Their actions name the change for the chat's change list:
`title` (the page's title before the call), `from` / `to` (the old and new
title; the old folder paths, comma-joined, and the new one, `""` for the
library root). The result text adds "(a new folder)" when the call made
the folder. A sub-page is refused ("that page is inside page <id> and is
filed where that page is — move the outermost page instead"): it goes
where its top goes. The rest of the batch still moves. Several pages in
one call: below.

### list_deleted / restore_page (folder only)

Recently deleted ([home_library.md](home_library.md)). A deleted page is in
a folder chat's reach when it was filed under the chat's folder; at the
library root, every deleted page is. Where a deleted page is filed is
where a restore puts it back (`blocks_store.trashed_folders`): a sub-page
deleted on its own counts as filed where its live parent's top is, and
the pages that went to Recently deleted with a page are not listed apart
(`trash.list_trash`), so they are listed, restored and reached through
that page. A permission either reads or changes,
so the two tools have one each:

- `list_deleted`, under **List pages**, lists such pages, the last deleted
  first: id, title, the folders a restore puts the page back in (the trash
  entry's `folders` ids, read as paths), the page it goes back inside
  (`| goes back inside page <id>`, the entry's `parent`: a sub-page deleted
  on its own), how many pages went with it and come back with it (`| the 3
  pages inside it come back with it`, `sub_pages`), when and
  by whom it was deleted, and when it goes for good (`trash.list_trash`).
  The header says so too. `title_contains` filters by title. Its chip is a
  `list` one.
- `restore_page`, under **Restore deleted pages** (`restore`, Ask by
  default), takes `page_ids` and runs `ops.restore_page`, the route's
  restore, for each; a page that fails is left out and named, and one that
  came back before the failure counts as restored. The page goes
  back under the library root in its folders, with its notes, highlights,
  files and chats, and the pages that went with it come back inside it;
  a sub-page deleted on its own goes back inside its parent. The result
  says both: `… to inside page <id>, filed where that page is ("A")`, and
  `…, with the N pages inside it` (a batch names each page that went back
  inside one). `_plan_restore_one` finds a page that is not deleted
  (a no-op) or was filed outside the chat's folder (refused). Named alone,
  it is answered without a card; in a list, it is left out of the card.

The approval card's preview has the page's `title` and the folders it goes
back `to`. The restore's action (kind `restore`) carries `page_id`, `title`
and `to` (its folders), and `revert`: `{"restore": [page ids]}`, which
sends the pages back to Recently deleted. The chat lists it under "Changed
in your library".

### delete_page (folder only)

Under **Delete pages** (`delete`, Ask by default). It takes `page_ids` and
runs `ops.trash_page`, the app's Delete, for each: the page goes to
Recently deleted with the pages inside it (`page_descendants`), its notes,
highlights, files and chats kept, for `KEEP_DAYS` (30) days. It never
deletes a page for good. `_plan_trash_one` refuses a page outside the
chat's reach (`_load_scoped_page`; a sub-page is reached where its top is
filed) and answers one already in Recently deleted as a no-op. A page
inside another page of the call goes with that one and counts as already
so, as the app's Delete of a selection sends the outer page only. A page
that fails is left out and named; one that went before the failure counts
as deleted.

The card's preview has the page's `title`, a `diff` striking it out, how
many pages inside go too (`inside`) and the days Recently deleted keeps
them (`days`); a call naming several pages adds the batch card's fields
(below). The action (kind `delete`) carries `page_id`, `title` and
`inside`, or for several pages `items` and `inside`, and `revert`:
`{"trash": [page ids]}`, which restores them. The chat lists it under
"Changed in your library", the title in bold with no link, since the page
is in Recently deleted. `delete_block` refuses a page's id and points at
`delete_page`.

### Folders and labels (folder only)

`rename_folder`, `create_label`, `rename_label` and `label_page` live under
**Organize folders and labels** (`organize`, Ask by default). Each writes
with client `ai`.

- `rename_folder` is one `set` of the folder's name on the `folders` tree
  (`apply_ops`). It touches no page, as the app's rename doesn't. It
  refuses:
  - the library root;
  - a name with ` / `, so a rename is never read as a move;
  - a name a folder beside it has (`blocks_store.named`, the app's
    `findNamed` rule, because the server keeps no names apart);
  - a folder outside reach.
- `create_label` runs `ensure_filing(labels=…, client="ai")`. A name some
  label has (ignoring case) is a no-op.
- `rename_label` is one `set` on the `labels` tree. It refuses a name
  another label has.
- `label_page` is one `set` of each page's `labels` (`page_ids`, with
  `add` / `remove` for all of them). It keeps the other labels, drops ids
  that are no label any more (`existing_in`), and makes a label it cannot
  find (`label_inserts`, in the call's transaction, only when the call
  runs).

Labels are library-wide, so reach works differently for them.
`create_label` and `label_page` (on a page in reach) work in any folder
chat. `rename_label` and `delete_label` work only when every page carrying
the label is in reach (`_label_out_of_reach`). That is always true at the
library root, and for a label on no page. Each page carrying it is
judged by `_label_page_reached`: a page in Recently deleted by the folders
a restore puts it back in (`trashed_folders`: one that went there with
another page by that page's), a live sub-page by where its top is filed,
and any other page by its own folders.

Previews carry `title` (the folder's path, the label's name or the page's
title) and a `diff`. A rename shows the word diff. `label_page` shows the
page's labels: kept as `ctx`, removed as `del`, added as `ins`.

Actions:

- the renames: kind `rename`, with `title`, `from`, `to`;
- `create_label`: kind `label`, with `title`, `to`;
- `label_page`: kind `label`, with `page_id`, `title`, `added`, `removed`.

`rename_folder`, `create_label` and `rename_label` are undone by another
call and carry no `revert`; `label_page` carries one, like `rename_page`
and `move_page`.

### Several pages in one call (rename_page, move_page, label_page, restore_page, delete_page)

**Arguments.** `rename_page` takes `renames` (`[{page_id, title}]`, at
most 50: each title is written out, and a turn the output cap cuts off is
lost whole). The others take `page_ids`, at most 100 (`BATCH_MAX`, a
`list_pages(limit=100)` batch). The one-page form (`page_id`, plus
`title` for a rename) is accepted too, for replayed chats, and so are a
list's whole JSON text and one bare id. A cut-off list or ids in prose
are refused, never read in part (`_listed`, `_page_ids_arg`). The same
holds for `add` / `remove`: a list's JSON text is that list, `"[]"`
included, and a cut-off one is refused (`_names_arg`). An id over 64
characters is cut and answered "no such page" without a lookup.

**Per page.** Every page goes through the one-page planner
(`_plan_*_one`). What the call shares (`folder`; `add` / `remove`) is
read once and refuses the whole call when bad, with no card. A page that
cannot change is left out: the card names it, the result gives the
reason, and the others change. A call whose every page is already so is
a no-op; one whose pages all fail is an error.

**Writes.** One transaction under the write lock: a folder
(`folder_inserts`) or labels (`label_inserts`) the call makes, then one
`set` per page (`apply_batches`, client `ai`), one commit, then
`ops.after_commits` (the folder-share check once). `_batch_write` holds
this frame for rename, move and label, from the plan to
`ops.after_commits`. Each tool hands it its planner, its digest and its
write step, and builds its own result and action. A restore runs
`ops.restore_page` per page and a deletion `ops.trash_page`, and each one
commits on its own.

**Move.** A page keeps the folders outside the chat's folder that are
not above the target (`stays`). Its card line strikes the folders it
leaves and shows the ones it stays in.

**The card** for two or more pages: `count`, `diff` (one word-diff line
per page, every page), `left_out` (at most 20 page titles cut to a line,
else the ids as given) and `left_out_count`, `same_count`, `to` /
`new_folder` (move), `new_labels` (label) and `digest`. A one-page
call's preview is unchanged and has no digest.

**After Allow** a rename, move or label call is planned again under the
write lock. A restore is planned again, then restores one page at a
time. The digest holds each page as it was (`before`) and as it ends
(`after`). When it differs (a page or the target changed while the card
waited, even where the page would end the same — for a folder still to
be made, the folder it goes under and its names), nothing is written and
the model hears `BATCH_CHANGED`.

**The result**: "ok — renamed 35 of 37 pages." then "Already so, not
changed: …" and "Left out — fix these and send only them again:" with the
pages grouped by reason, then `Changed:` with the page ids (a restore
adds links to the first 20). The parts the next call needs come first,
so a saved chat's 4,000-character cut never takes them.

**The action** for two or more pages: one per call, kind as the tool's,
`summary` ("Renamed 37 pages"), `changes` (pages changed), `items` (each
page as the one-page action names it) and `revert`; move adds `to`,
label `added` / `removed`. The chat lists it as one row that unfolds to
its pages.

**The change limit** counts each changed page. A call that would change
more pages than the message may still change (`changes_left`) is refused
whole, before any card, with a refusal that starts like the loop's own
(`CHANGE_LIMIT_PREFIX`), so the reply ends on Continue. Under Unlimited
rounds there is no limit.

**Permissions** are one per tool: Allow or "Allow in this
chat" lets a list run without a card, as many one-page calls would.

### delete_folder / delete_label (folder only)

Their permission is **Delete folders and labels** (`organize_delete`, Ask
by default). It takes all three states like any other: every deletion can
be reverted, the folders' chats included (below). The card's Allow once is
the red one. Its preview carries the folder's or label's `block_id`. Once
allowed, the gate plans the call again (`TREE_DELETES`), and when its path
or name names another block by then (a rename or a move while the card
waited), nothing is deleted and the model is told so
(`ai_agent.CHANGED_WHILE_ASKING`).

Both read `ops.tree_deletion` for the card and the revert record, then run
`ops.delete_tree`, the body of `DELETE /api/folders/{id}` and
`/labels/{id}` (the app's "Keep pages"). The checks, the record and the
deletion run under the tool's write lock, with client `ai`. Pages are
never trashed.

`delete_folder` refuses:

- the library root;
- the chat's own folder (one above it is out of reach anyway);
- a folder outside reach;
- a folder with a share link in its subtree (`routers/shares.shared_folders`),
  because the deletion would stop the link for good.

Both refuse a deletion whose revert record would not fit (`_tree_revert`:
more than `_DELETE_REVERT_MAX` characters, some 5,800 pages, or more than
`MAX_OPS` folders). The user deletes those in the app. So every deletion
the agent makes can be reverted.

The folder card shows:

- the folder's path (`diff`);
- its subfolders (`subfolders`, at most 20, in tree order, and
  `subfolder_count`);
- the pages that lose the filing and stay (`pages`, at most 20 titles in
  library order, and `page_count`);
- the folders whose AI chats move to the library chat's history
  (`chat_folders`, at most 20, and `chat_folder_count`; `chats` counts the
  conversations).

`delete_label`'s card shows the name and the pages it comes off.

The action has kind `delete`, with `block_id` (the folder or label),
`title`, `count` (the pages refiled), `chats` (a folder's) and `revert`
(`{tree, blocks, pages}`, and for a folder whose chats moved `chats`:
`{history id: folder}`, what `routers/chats.file_into_home` moved). The
chat lists it under "Changed in your library", with a revert button
("Reverting a note change" below).

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
replace only for full rewrites. The agent prompt's one "Note editing"
paragraph, armed with the permission, adds only what the specs cannot say:
which ids to use, that the cursor and attached blocks need no read, that a
quoted selection is edited in place rather than answered, and that
`delete_block` is for whole blocks. The action carries
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
Notes write math only as `$…$` and `$$…$$`, which both specs tell the model,
and a `content` that writes it as `\( … \)` or `\[ … \]` (GPT models do) is
converted before anything compares, previews or stores it, by the rules a
paste follows: a `\[ … \]` converts only when it is shaped as a formula, so
an escaped bracket stays (`paste_math.dollar_math`, the twin of
`frontend/src/editor/pasteMath.js`). A replace keeps every `\( \)` pair and
dollar formula the prose it read already held as written (`keep`), so
writing back what the block says changes nothing. A patch, a selection edit,
an append or a prepend converts its passage unless the passage lands in a
code fence, inline code or math (`paste_math.paste_math`, at the offset
`patch_block_text` and `replace_selection_text` return; an append is judged
on the line of its own it goes on). `find` is never converted.
`move_block` re-parents/reorders a block with its subtree — cycle-checked, and
cross-page moves (allowed when both pages are in scope) refuse subtrees
containing highlight blocks, whose PDF anchors are tied to their own paper.
They also refuse a text box or a picture, placed on its own page's PDF or
sheet, unless the sheet it is on moves with it (`_loose_placed`). The
refusal names the kind: "pictures are placed on their page — they can
only move within the same page, or with the page of paper they are on",
and "text boxes …" for a box.
`delete_block` removes a block with its subtree (below).
All four go through the op path (`ops.apply_ops`, [collab.md](collab.md)):
logged, fanned out to anyone on the page, and the page root's `updated_at`
stamped so the home feed reorders. Their UI actions carry `page_id` (moves across
pages also `src_page_id`) and `block_id` (the edited/moved block, or the
created block's new id; `read_block` actions carry it too). The frontend
reloads the open page's block tree when it was touched and lights the block
up; edit/create calls are previewed in the block while the model is still
writing them (see "Watching the agent work" in [ai.md](ai.md)). Each of
the four records what undoing it needs, so the user can revert it from the
reply ("Reverting a note change" below). Pages are never deleted by a tool:
an unwanted page is left for the user.

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

### delete_block (both scopes)

Removes a note block with everything nested under it, in the Edit note
blocks permission. A page id is refused: `delete_page` deletes pages.
Deleting a highlight removes its mark from the PDF; deleting a handwriting
block removes the handwriting. The approval card names the kind (`what`,
the block's kind through `_DELETED_KIND`: note, highlight, link,
handwriting, text box, picture, page of paper, recording), counts the
nested blocks (`children`) and shows the text struck out. The action
(kind `delete`, with `page_id`, `block_id`, `title`) records every block
of the subtree, parent first, as `revert`
(`{"blocks": [[id, parent, position, content, properties], …]}`), so the
user's revert puts the subtree back where it was with the same ids and the
redo takes it again (`ai_revert._plan_delete`; a record over
`_DELETE_REVERT_MAX` chars is not kept and the result says so). The prompt
tells the model to delete only what the user asked to remove and to edit
instead when only part of a block should go.

### clip_region (both scopes)

Stores a picture for the notes to embed, in the Edit note blocks
permission, and answers with its URL: a PDF page of `page_id` (`pdf_page`,
1-based) or its region `box` (`[x0, y0, x1, y1]` as page fractions), with
its pictures and text boxes and the user's handwriting over them when `ink`
is true, else the page as stored (`ai_pictures.render_region`);
or a handwriting block's or a page of paper's picture (`block_id`, `area`
"page" for the whole page — what `view_ink` shows). The picture is sized
like every other and stored under its content hash (`ai_pictures.store_picture`,
the workspace's quota), so the same region clipped twice is one file; the
upload GC keeps it while a note or a chat names it. The result tells the
model to put it in a note as a line of its own, `![caption](url)`, with
`create_block` or `edit_block`. The approval card shows the region from the
same `GET /api/ai/page-image` / `GET /api/ai/ink-image` URL the chat renders
(`picture` on the preview), storing nothing until the user allows. Its chip
is "Clipped a picture of …" (kind `clip`, with `url` and `picture`); it is a
change the budget counts (`MAX_TOOL_ACTIONS`) but not a note change, so it
has no revert row.

A written block may only name stored files that exist: `edit_block` and
`create_block` refuse content whose `/api/uploads/` references the
workspace does not hold (`missing_uploads`; references the block already
had are not checked), and their approval cards show the pictures a change
adds (`pictures` on the preview). The prompt says which URLs the model may
embed: the ones in a picture's label line, `clip_region`'s result, and
`read_block`'s text.

### comment / resolve / highlight (both scopes, one permission)

The writing tools: what the agent writes in the library under its own name,
for the account it acts for ([annotations.md](annotations.md) "The writing
tools"; `gamma/annotations/hand.py` writes, `places.py` finds the place). One
permission, **Comments and highlights** (`comment`, Ask by default as every
change is, in the "Make changes" group of Settings → AI → Tool usage), arms
all three. They are `mutating` and `commenting` (`WRITING_TOOLS`): armed
where the scope may comment (`can_comment`, `auth.can_comment`: any member,
a viewer's chat too, and a token that comments there), not only where it
edits (`can_write`), and `run_agent_tool` refuses them without it ("you
cannot comment in this workspace"). Who writes is the scope's `agent`, the
stamp a block it makes keeps (`agents.stamp`; the chat's is Gamma AI's for
the person, `agents.gamma()`, or the connection's of a token that drives
the chat, `agents.of_request`), read back as `agents.of_stamp`, and its
`actor`, the account that authors the comment and the op. A new highlight
is a block, an edit: a viewer's chat comments on the page or a note, and
its `highlight` or `comment` on a passage or lines is refused before it
asks (`hand.may_mark`, in `_plan_write`).

- **`comment`**: `body`, and `thread_id` for a reply, else a place of a page
  (by `page_id`, a work folder's file by `path`, a `block_id`'s page, else a
  page chat's): `quote` with `pdf_page` on a PDF, `line` and `end_line` (and
  their `quote`) in a text file, `block_id` a note, or none of them for the
  page's own thread. A new thread on a passage or a range is on a new
  highlight of it, drawn in the wash as a person's commented passage is.
- **`resolve`**: `thread_id`; a resolved thread answers "resolved already"
  and is no change (`noop`); never a reopen, which is the person's.
- **`highlight`**: a passage or a range, as `comment`'s, with `note` (the
  block's text), `color` (`yellow`, `green`, `blue`, `purple`: the palette's
  first four washes, `hand.COLORS`) and `style` (`highlight`, `underline`,
  `squiggly`, `strikeout`, as the viewer's styles); a place the agent
  highlighted already is refused with that highlight's id.

A quote is found exactly, as the relocation folds text, or not at all: one
not on the page is refused with the closest passage the finder saw, never
placed by guess ([annotations.md](annotations.md) "The writing tools"). The
plan of a call (`_plan_write`) checks the thread or the page is in the chat's
reach as any change's does (a page chat writes on its page alone; the
context's pages are read-only), and the approval card shows the page's
title, the place (`preview.place`: `{kind, page?, lines?, quote?}`, which the
card words, `approvals.placeText`) and the comment's text as a diff; a call
whose place is not found asks nobody. Each answers with the ids it made (the
thread and the comment, the new highlight) and the link `list_annotations`
gives the entry (`entries.link_of`, `?ws=&page=&block=` and `&thread=` for a
thread; absolute on MCP), and its action (kind `comment`, `resolve` or
`highlight`, with `page_id`, `block_id`, `thread_id`, `comment_id`, `link`,
`title`, a reply's `reply`) lists it under the reply as a note change:
"Commented in …", "Replied in a comment thread in …", "Resolved a comment
thread in …", "Highlighted a passage in …" (`chat/agentSteps.js`); a
highlight reloads the open page's tree as a block tool does (`BLOCK_TOOLS`).
None has a revert row: a comment and a highlight are the person's to delete.
The same words on the same place by the same agent within `hand.REPEAT_S`
are refused with what the first call wrote, so a call made again never
doubles a comment. The prompt names them once any is armed: reply where a
comment asks, say what changed, resolve once done, ask on a passage, quote
verbatim, comment or highlight only when asked.

## Guardrails

Deliberately not offered under any permission:

- Deleting pages for good, or files. A page goes to Recently deleted only
  by `delete_page`, a folder or a label is deleted only by `delete_folder`
  / `delete_label`, which never delete a page, and a note block only by
  `delete_block`. The user can revert each of them, and a deletion too
  large to record its revert is refused (`delete_folder`, `delete_label`)
  or says it cannot be reverted (`delete_block`).
- Deleting a shared folder (the user stops sharing it first), the chat's
  own folder or one above it; editing highlight anchors; creating a folder
  on its own (a folder is made only as `move_page`'s or `save_paper`'s
  target); moving or reordering folders; and refiling except through
  `move_page` and `label_page`.
- Reading library pages outside the base scope and attached references, or
  editing pages outside the base scope. The server checks every call.
- Reaching uploads other than the pictures a note embeds (`view_image`)
  and the ones `clip_region` stores, share links, settings, or other
  users' data.
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
rounds and a ≤200-change guard (a call that changes several pages counts
each), both lifted when the user chooses Unlimited rounds, detailed in
[ai.md](ai.md).

**Every tool call is shown in the reply**, reads included, so there is
always a visible record of what the agent looked at and changed. One pill
sums them up ("6 steps · listed, read 1 page · 1 failed") and expands to a
line per call, its icon naming the action kind (`ACTION_ICONS` in
`chat/AgentChanges.jsx`: list, book, search, eye, pen, quote, globe, download,
file-plus, history, pencil, folder, plus, label). Each line expands to the
arguments and the output the model got. Everything that changed is listed
again under the pill: "Changed in your library" (renamed, filed, saved,
deleted and restored pages; renamed, made, applied and deleted folders and labels) and
"Changed in your notes" (edited, added and moved
blocks), each entry a link to the page or block. A call that changed
several pages is one entry that unfolds to them. The note tools' actions carry their page's `title` for that list; a
change tool that changed nothing is marked `noop` and not listed.

### Reverting a note change

Each row under "Changed in your notes" has a revert button. The user takes
back one change at a time, in any order, and whatever anyone wrote since
stays. A reverted row has a redo button that puts the change back. Pages
renamed, moved, labelled, deleted or restored by the agent — one or many in
one call — and a deleted folder or label have one under "Changed in your
library". To take back several at once, "Revert…" opens a dialog (below).
A note that shows an edit or a new note made without asking as tracked
changes has a Revert of its own on the bar under it, the same revert as the row's (`sendRevert`,
marked in the conversation the same way), its steps newest first
([ai.md](ai.md#watching-the-agent-work-live-footprint)).

The note tools record what undoing and redoing their change needs, as
`revert` on the action:

| Tool | `revert` |
| --- | --- |
| `edit_block` | `before` and `after`: the block's text just before and just after the write, read under the write lock |
| `create_block` | `after`: the text the block was made with; `parent` and `position`: where it went |
| `move_block` | `parent` and `position`: where the block was; `to_parent` and `to_position`: where it went |
| `delete_block` | `blocks`: every block of the subtree, parent first, as `[id, parent, position, content, properties]` |
| `delete_folder` / `delete_label` | `tree`; `blocks` (as `delete_block`'s: the folder's subtree, or the label); `pages`: `{page id: the ids it carried}`; a folder's `chats`: `{history id: folder}`, the conversations it moved into the library chat's history |
| `rename_page` / `move_page` / `label_page` | `pages`: `[page id, field, before, after]` per page — `title` (text, as stored: `""` for an untitled page), `folders` or `labels` (id lists, as read and as written) |
| `delete_page` / `restore_page` | `trash` / `restore`: the page ids the call moved into Recently deleted, or out of it |

The chat saves the action with the reply. A revert sends it to
`POST /api/ai/revert` (`gamma/ai_revert.py`), which plans and writes under
one write lock:

| Change | Reverted by | Stops (409) when |
| --- | --- | --- |
| edit | merging the change `after → before` into the text stored now (`textmerge.merge` with word-level hunks, `semantic`) | `changed`: someone changed the agent's own text |
| new block | deleting it | `filled`: it was typed in or has notes under it (`preview.children`) |
| move | moving it back to the old parent at the old key, re-keyed if a sibling took it (across pages: `ops.move_across_pages`) | `moved`: it was moved on since |
| deletion | inserting the subtree again with the same ids, parents and keys (a no-op when the block is back already); a redo deletes it again | `gone`: the parent it was under was deleted |
| deleted folder or label | inserting the blocks again with the same ids, parents and keys, filing the library pages that carried them again and moving the folders' chats back into their histories (`routers/chats.chats_back`); a redo runs `ops.delete_tree` again | `gone`: the folder it was in was deleted, a folder or label of that name is there again, or (redo) the folder is shared now |
| deleted or restored pages | in one step, `kind: "trash"`, no `block_id`: each page goes back where the call found it, one `ops.restore_page` or `ops.trash_page` each, with the pages inside it; a page already there is passed by; a redo makes the call again | never forced. A page deleted for good since is counted (`kept`); when that is every page, it answers 409 `kept` |
| renamed, moved or relabelled pages | in one step, `kind: "pages"`, no `block_id`. A title is set back where the page still reads as the call left it. Folders and labels go by set delta: the ids the call put on come off, the ones it took off go back where they exist, and a filing made since stays | never forced. A page changed since, deleted or in Recently deleted is left and counted (`kept`), and so is a page whose folders to go back to were all deleted, which would be filed in none. When that is every page, nothing is written and it answers 409 `kept`, so the row is not marked. A redo is the same the other way |

A deleted folder's or label's row sits under "Changed in your library", with
a revert and a redo button. A revert moves each conversation the deletion
moved back into its folder's history, where the folder is back and the
conversation still sits in the library chat's history: the folder's active
conversation comes back as an earlier one, and one the user deleted or
moved since stays as it is. A redo moves them again, under the same ids, so
a second revert finds them too. A restored folder's creation time is the
revert's. A page moved to Recently deleted after the
deletion is not filed back: it lost the id in the deletion, and a revert
cannot write to a trashed page. A redo deletes the folder as it is then,
subfolders added since included (as `delete_block`'s redo deletes the
notes added since), and a later revert puts back what the first deletion
took.

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
action `reverted`, with `kept` when a page tool's revert left pages as
they were (a redo drops the mark, and keeps `kept` only when it too left
pages as they were), saves the conversation
(`chatSession.edit`), and reloads the touched pages when no socket brought
the ops.

In the list, a reverted row shows "Reverted" and its redo button. A stopped
row says why, shows the diff, and offers "Revert anyway", "Delete anyway"
or "Move back anyway" (for a redo, "Redo anyway" or "Move anyway") beside
"Keep it". The buttons wait while a reply streams. A viewer and the share
view get none. A page tool's row whose every page changed since says so,
with Dismiss; one that left some says how many under the row.

**Several at once.** While the conversation holds two changes or more that
can still be reverted, each reply with one has "Revert…" in its first
list's head. It opens `chat/RevertChanges.jsx` over the dock: every such
change of the conversation (`aiRevert.revertible`), grouped under the
message that asked for it, the latest first, each with a checkbox, the
reply it was opened from ticked, and "Select all". Revert takes the ticked
ones back newest first, each as its row does (`sendRevert`), and marks each
in the saved conversation as it goes. It stops at the first that asks: the
row says why, with "Revert anyway" where it can be forced and "Skip it",
which unticks it so the next Revert goes on. The list is the one the
dialog opened with, so a reverted row stays, marked "Reverted". It closes
when the dock shows another conversation.

A folder or label a page tool's call made stays after a revert, maybe
empty. The folders a page comes back to
go last in its list, and a page filed again below a folder the move took
it out of can end up in both levels.

The request's history leaves `revert`, and a batch's per-page `items`, out
of the actions it sends (`chat/aiRevert.js` `forReplay`). The replay puts "the user reverted this
change afterwards" before a reverted action's result, so the model does not
build on it ([ai.md](ai.md) "Replay across turns"). An action without
`revert`, saved by an older version, shows no button. Ctrl+Z does not
reach any of this: the agent's writes and the reverts both arrive as remote
ops, which the undo history never records ([collab.md](collab.md)).
